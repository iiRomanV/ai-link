"""BROWSER-FOCUS: не даём Chromium красть фокус.

Подход (от Perplexity, 2026-09-26):
  • Не трогаем browser_manager.py (логика чтения ответов — святое)
  • Watchdog в отдельном потоке следит за активным окном
  • Если активное окно стало Chromium — возвращаем предыдущее
  • НО: если пользователь САМ кликнул в Chromium — не трогаем
    (определяем по времени: если > 5 сек прошло от появления
     окна и restore уже был — считаем, что это ручной клик)

Плюсы:
  • Chromium ВИДИМ (видно капчу/логин)
  • Пользователь МОЖЕТ кликнуть в Chromium — фокус перейдёт
  • Фокус НЕ крадётся автоматически при старте/reset

Зависимости: xdotool, wmctrl (опционально).
"""

import logging
import shutil
import subprocess
import threading
import time
from typing import Optional, List, Tuple

logger = logging.getLogger(__name__)

# Класс окон Chromium (из WM_CLASS)
_CHROMIUM_CLASSES = (
    "chromium-browser", "Chromium-browser",
    "google-chrome", "Google-chrome",
)

# Сколько раз возвращать фокус
_RESTORE_ATTEMPTS = 3
# Задержки между попытками (сек)
_RESTORE_DELAYS = (0.2, 0.7, 1.5)
# Интервал проверки активного окна (сек)
# FIX 2026-09-26: 0.5 → 0.1 — не успеваем поймать нажатия клавиш
_CHECK_INTERVAL = 0.1
# Через сколько секунд после restore «забыть» про это окно
# (если пользователь кликнул сам — не восстанавливать)
_FORGET_AFTER = 8.0

# Глобальное состояние
_watchdog_thread: Optional[threading.Thread] = None
_watchdog_stop = threading.Event()

# Последнее НЕ-Chromium активное окно
_prev_active_id: Optional[str] = None
_prev_active_title: str = ""

# Окна Chromium, для которых мы уже сделали restore
# (id → timestamp). Если пользователь кликнул сам — не трогаем.
_restored: dict = {}

# Включён ли режим
_enabled = True


def _have_tools() -> bool:
    """True, если xdotool и wmctrl есть."""
    return (shutil.which("xdotool") is not None
            and shutil.which("wmctrl") is not None)


def set_enabled(val: bool) -> None:
    """Включить/выключить режим."""
    global _enabled
    _enabled = bool(val)
    logger.info("BROWSER-FOCUS: режим %s",
                "включён" if _enabled else "выключен")


def is_enabled() -> bool:
    return _enabled


def get_active_window() -> Tuple[Optional[str], str]:
    """Возвращает (id, title) активного окна."""
    try:
        r = subprocess.run(
            ["xdotool", "getactivewindow"],
            capture_output=True, text=True, timeout=2,
        )
        wid = (r.stdout or "").strip()
        if not wid:
            return None, ""
        r2 = subprocess.run(
            ["xdotool", "getwindowname", wid],
            capture_output=True, text=True, timeout=2,
        )
        title = (r2.stdout or "").strip()
        return wid, title
    except Exception:
        return None, ""


def get_window_class(wid: str) -> str:
    """Возвращает WM_CLASS окна (первый элемент)."""
    try:
        r = subprocess.run(
            ["xprop", "-id", wid, "WM_CLASS"],
            capture_output=True, text=True, timeout=2,
        )
        line = (r.stdout or "").strip()
        # WM_CLASS(STRING) = "instance", "Class"
        if "=" in line:
            parts = line.split("=", 1)[1].strip()
            # Берём первый элемент
            first = parts.split(",")[0].strip().strip('"')
            return first
    except Exception:
        pass
    return ""


def is_chromium(wid: str, title: str = "") -> bool:
    """True, если окно — Chromium (по классу или заголовку)."""
    cls = get_window_class(wid)
    if cls in _CHROMIUM_CLASSES:
        return True
    # Fallback: по заголовку
    low = (title or "").lower()
    for m in ("google chrome", "chromium", "deepseek",
              "qwen", "chatgpt", "perplexity"):
        if m in low:
            return True
    return False


def activate_window(wid: str) -> bool:
    """Активирует окно через xdotool.

    FIX 2026-09-26: windowfocus (без --sync) — быстрее,
    затем windowactivate для WM.
    """
    try:
        r = subprocess.run(
            ["xdotool", "windowfocus", "--sync", wid],
            capture_output=True, timeout=2,
        )
        if r.returncode == 0:
            subprocess.run(
                ["xdotool", "windowactivate", wid],
                capture_output=True, timeout=1,
            )
            return True
        return False
    except Exception:
        return False


def _restore_focus_async(wid: str, title: str = "") -> None:
    """Возвращает фокус окну wid (2-3 попытки в отдельном потоке)."""
    def _worker():
        # FIX 2026-09-26: немедленная первая попытка (без sleep)
        if activate_window(wid):
            logger.info(
                "BROWSER-FOCUS: возвращён фокус на '%s' (%s)",
                title[:40], wid,
            )
            return
        for delay in (0.1, 0.3, 0.7):
            time.sleep(delay)
            if activate_window(wid):
                logger.info(
                    "BROWSER-FOCUS: возвращён фокус (retry) "
                    "на '%s' (%s)", title[:40], wid,
                )
                return
        logger.debug(
            "BROWSER-FOCUS: не удалось вернуть фокус на %s", wid,
        )
    t = threading.Thread(target=_worker, daemon=True,
                         name="BrowserFocusRestore")
    t.start()


def _watchdog_loop():
    """Основной цикл: следит за активным окном."""
    global _prev_active_id, _prev_active_title
    logger.info("BROWSER-FOCUS: watchdog запущен (интервал %.1fs)",
                _CHECK_INTERVAL)

    while not _watchdog_stop.is_set():
        try:
            if not _enabled:
                _watchdog_stop.wait(_CHECK_INTERVAL)
                continue

            wid, title = get_active_window()
            if not wid:
                _watchdog_stop.wait(_CHECK_INTERVAL)
                continue

            # Chromium стал активным?
            if is_chromium(wid, title):
                # Уже делали restore для этого окна?
                now = time.time()
                last = _restored.get(wid, 0)
                if now - last > _FORGET_AFTER:
                    # Свежий перехват — вернём фокус
                    if _prev_active_id and _prev_active_id != wid:
                        # Проверим, что предыдущее окно ещё живо
                        try:
                            r = subprocess.run(
                                ["xdotool", "getwindowname",
                                 _prev_active_id],
                                capture_output=True, text=True,
                                timeout=2,
                            )
                            if r.returncode == 0:
                                logger.info(
                                    "BROWSER-FOCUS: Chromium перехватил "
                                    "фокус → возвращаю '%s'",
                                    _prev_active_title[:40],
                                )
                                _restore_focus_async(
                                    _prev_active_id,
                                    _prev_active_title,
                                )
                                _restored[wid] = now
                        except Exception:
                            pass
                # Не обновляем _prev_active (это Chromium)
            else:
                # Обычное окно — запоминаем как «предыдущее»
                if wid != _prev_active_id:
                    _prev_active_id = wid
                    _prev_active_title = title
                    logger.debug(
                        "BROWSER-FOCUS: prev_active=%s '%s'",
                        wid, title[:40],
                    )

            # Чистим старые записи в _restored
            cutoff = time.time() - 60
            for k in list(_restored.keys()):
                if _restored[k] < cutoff:
                    del _restored[k]

        except Exception as e:
            logger.debug("BROWSER-FOCUS watchdog: %s", e)

        _watchdog_stop.wait(_CHECK_INTERVAL)

    logger.info("BROWSER-FOCUS: watchdog остановлен")


def start_watchdog() -> bool:
    """Запускает watchdog."""
    global _watchdog_thread
    if not _have_tools():
        logger.info(
            "BROWSER-FOCUS: xdotool/wmctrl не установлены — "
            "режим недоступен"
        )
        return False
    if _watchdog_thread and _watchdog_thread.is_alive():
        return True
    _watchdog_stop.clear()
    _watchdog_thread = threading.Thread(
        target=_watchdog_loop, daemon=True,
        name="BrowserFocusWatchdog",
    )
    _watchdog_thread.start()
    return True


def stop_watchdog() -> None:
    """Останавливает watchdog."""
    global _watchdog_thread
    _watchdog_stop.set()
    if _watchdog_thread and _watchdog_thread.is_alive():
        try:
            _watchdog_thread.join(timeout=2)
        except Exception:
            pass
    _watchdog_thread = None


# ─── Совместимость со старым API ───

def force_move_all() -> int:
    """Оставлено для совместимости. Возвращает 0."""
    return 0
