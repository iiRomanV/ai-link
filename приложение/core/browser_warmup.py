"""Прогрев браузерных сайтов.

Проверяет, что сайт открывается и отвечает. Статусы отдаёт в UI.
Последовательный прогрев — не все сайты сразу, а по одному.
"""

import threading
import time
from typing import Dict, Any, List, Optional, Callable

# Статусы
ST_DISABLED = "disabled"          # ⚪ сайт выключен в конфиге
ST_IDLE = "idle"                  # ⚪ включён, но не прогрет
ST_LOADING = "loading"            # 🟡 идёт прогрев
ST_READY = "ready"                # 🟢 готов, отвечает
ST_LOGIN = "login_required"       # 🔴 нужен вход
ST_ERROR = "error"                # ⚫ ошибка

_statuses: Dict[str, Dict[str, Any]] = {}
_lock = threading.Lock()
_warmup_thread: Optional[threading.Thread] = None
_cancel_flags: Dict[str, bool] = {}

PING_PROMPT = "Ответь одним словом: OK"


def _set_status(site_id: str, status: str, message: str = "",
                callback: Optional[Callable] = None) -> None:
    with _lock:
        _statuses[site_id] = {
            "status": status,
            "message": message,
            "ts": time.time(),
        }
    if callback:
        try:
            callback(site_id, dict(_statuses[site_id]))
        except Exception:
            pass


def get_status(site_id: str) -> Dict[str, Any]:
    """Возвращает статус одного сайта."""
    with _lock:
        return dict(_statuses.get(site_id, {"status": ST_IDLE, "message": "", "ts": 0}))


def get_all_statuses() -> Dict[str, Dict[str, Any]]:
    """Возвращает статусы всех сайтов из browser_sites.json."""
    from core.agents.browser_sites_manager import load_sites
    result: Dict[str, Dict[str, Any]] = {}
    try:
        data = load_sites()
        for s in data.get("sites", []):
            sid = s.get("id")
            if not sid:
                continue
            if not s.get("enabled", False):
                result[sid] = {"status": ST_DISABLED, "message": "выключен в конфиге", "ts": 0}
            elif not _is_agent_enabled(sid):
                result[sid] = {"status": ST_DISABLED, "message": "агент выключен", "ts": 0}
            else:
                result[sid] = get_status(sid)
                if result[sid]["status"] == ST_DISABLED:
                    result[sid]["status"] = ST_IDLE
    except Exception:
        pass
    return result


def _map_reason_to_status(reason: str) -> str:
    """Преобразует reason из browser_manager в статус прогрева."""
    if reason == "login_required":
        return ST_LOGIN
    return ST_ERROR


def _is_agent_enabled(site_id: str) -> bool:
    """Проверяет, включён ли соответствующий агент в agent_registry.

    В registry id с префиксом browser_<site_id>.
    Если агента нет в registry — считаем включённым.
    """
    try:
        from core.agent_registry import get_agent
        agent = get_agent(f"browser_{site_id}")
        if agent is None:
            return True
        return bool(agent.get("enabled", True))
    except Exception:
        return True


def warmup_site(site_id: str, timeout: int = 180,
                callback: Optional[Callable] = None) -> Dict[str, Any]:
    """Синхронный прогрев одного сайта. Отправляет короткий ping."""
    # Проверка: агент включён?
    if not _is_agent_enabled(site_id):
        _set_status(site_id, ST_DISABLED, "агент выключен", callback)
        return get_status(site_id)

    # Защита от двойного прогрева
    with _lock:
        info = _statuses.get(site_id, {})
        current = info.get("status")
        started = info.get("ts", 0)
    if current == ST_LOADING:
        # Если грузится больше 120 секунд — считаем зависшим, разрешаем перезапуск
        import time as _t
        if _t.time() - started < 120:
            return get_status(site_id)
        # иначе — продолжаем, сбросим и попробуем снова

    _cancel_flags[site_id] = False
    _set_status(site_id, ST_LOADING, "загружается...", callback)

    try:
        from core.agents.browser_agent import query_browser_ai
        r = query_browser_ai(site_id, PING_PROMPT)
    except Exception as e:
        _set_status(site_id, ST_ERROR, str(e)[:200], callback)
        return get_status(site_id)

    if _cancel_flags.get(site_id):
        _set_status(site_id, ST_IDLE, "отменено", callback)
        return get_status(site_id)

    if r.get("success"):
        _set_status(site_id, ST_READY, "готов", callback)
    else:
        reason = r.get("reason", "")
        status = _map_reason_to_status(reason)
        msg = r.get("error", reason)[:200] if r.get("error") else reason
        _set_status(site_id, status, msg, callback)

    return get_status(site_id)


def cancel_warmup(site_id: str) -> None:
    """Помечает прогрев сайта как отменённый."""
    _cancel_flags[site_id] = True


def _is_site_assigned(site_id: str) -> bool:
    """ROLES (2026-10-01): True, если модель назначена хотя бы
    на одну роль в agent_assignments.json.
    """
    try:
        from core.capability_benchmark import load_assignments
        data = load_assignments()
        assignments = data.get("assignments") or {}
        agent_id = f"browser_{site_id}"
        return agent_id in assignments.values()
    except Exception:
        return True  # не смогли прочитать — не блокируем


def warmup_all_async(callback: Optional[Callable] = None,
                     timeout: int = 180,
                     only_assigned: bool = True) -> threading.Thread:
    """ROLES (2026-10-01): прогрев только назначенных моделей.

    Раньше грели ВСЕХ enabled — Qwen дёргался даже если не назначен.
    """
    global _warmup_thread

    def _worker():
        from core.agents.browser_sites_manager import load_sites
        try:
            data = load_sites()
        except Exception:
            return
        for s in data.get("sites", []):
            if not s.get("enabled", False):
                continue
            sid = s.get("id")
            if not sid:
                continue
            if not _is_agent_enabled(sid):
                _set_status(sid, ST_DISABLED, "агент выключен", callback)
                continue
            if only_assigned and not _is_site_assigned(sid):
                _set_status(sid, ST_IDLE, "не назначен на роль", callback)
                continue
            if _cancel_flags.get(sid):
                continue
            warmup_site(sid, timeout=timeout, callback=callback)
            time.sleep(1)

    _warmup_thread = threading.Thread(target=_worker, daemon=True)
    _warmup_thread.start()
    return _warmup_thread


def warmup_site_async(site_id: str,
                      callback: Optional[Callable] = None,
                      timeout: int = 180) -> threading.Thread:
    """Фоновый прогрев одного сайта."""
    def _worker():
        warmup_site(site_id, timeout=timeout, callback=callback)

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    return t


def force_reset_site(site_id: str) -> None:
    """Принудительно сбрасывает статус сайта в idle.

    Используется, если прогрев завис (браузер закрыт вручную).
    """
    with _lock:
        if site_id in _statuses:
            _statuses[site_id] = {
                "status": ST_IDLE,
                "message": "сброшено",
                "ts": 0,
            }
    _cancel_flags[site_id] = False


def reset_all() -> None:
    """Сбрасывает все статусы к idle (для тестов и перезапуска)."""
    with _lock:
        _statuses.clear()
    _cancel_flags.clear()
