"""COST-LEDGER — учёт ресурсов на LLM и инструменты.

Учитываем НЕ только деньги:
  • api_cost (для платных API) — может быть None
  • browser_time (для DeepSeek/Qwen)
  • requests, latency
  • restarts, failures

Ресурсные классы:
  browser_free — браузерные (DeepSeek/Qwen/Perplexity), 0₽
  api_paid     — GigaChat/Yandex, платные
  local_free   — Ollama (отсечён)

Для browser-моделей estimated_cost = None, usage_source="unknown".
НЕ выдумываем токены — они недоступны из UI.

API:
  record(run_id, model, role, duration, requests, success,
         resource_class, estimated_cost)  → id
  ingest_from_events(project_dir)  → сколько записей добавлено
  summary(run_id=None, project_dir=None)  → сводка
  summary_for_llm()  → компактный текст
  reset_ledger(db_path)  → очистить (для тестов)
"""

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List, Optional


# ─────────────────────────────────────────────────────────────
#  Классы ресурсов
# ─────────────────────────────────────────────────────────────

RC_BROWSER_FREE = "browser_free"
RC_API_PAID = "api_paid"
RC_LOCAL_FREE = "local_free"
RC_UNKNOWN = "unknown"


def resource_class_for(model: str) -> str:
    """Определяет ресурсный класс по имени агента."""
    m = (model or "").lower()
    if m.startswith("browser_"):
        return RC_BROWSER_FREE
    if m in ("gigachat", "yandex", "openai", "anthropic"):
        return RC_API_PAID
    if m == "ollama":
        return RC_LOCAL_FREE
    return RC_UNKNOWN


def _db_path() -> Path:
    from core.ai_link_db import get_db_path
    return get_db_path()


def _ensure_schema() -> Path:
    from core.ai_link_db import init_schema
    return init_schema()


# ─────────────────────────────────────────────────────────────
#  Прямая запись
# ─────────────────────────────────────────────────────────────

def record(run_id: str,
           model: str,
           role: str = "",
           duration_seconds: float = 0.0,
           requests: int = 0,
           success: bool = True,
           resource_class: str = "",
           estimated_cost: Optional[float] = None,
           ts: str = "",
           db_path: Optional[Path] = None) -> Optional[int]:
    """Добавляет запись в cost_ledger. Возвращает id или None.

    ts: время события (из events.jsonl). Если пусто — now().
    Это важно для идемпотентности: ingest_from_events сравнивает
    ts события, а не момент вызова ingest.
    """
    try:
        from core.ai_link_db import init_schema as _init_schema
        path = _init_schema(db_path)
        rc = resource_class or resource_class_for(model)
        _ts = ts or datetime.now().isoformat(timespec="seconds")
        con = sqlite3.connect(str(path))
        try:
            cur = con.execute(
                "INSERT INTO cost_ledger "
                "(run_id, model, role, duration_seconds, requests, "
                " estimated_cost, resource_class, success, ts) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    run_id or "",
                    model or "",
                    role or "",
                    float(duration_seconds or 0),
                    int(requests or 0),
                    (None if estimated_cost is None
                     else float(estimated_cost)),
                    rc,
                    1 if success else 0,
                    _ts,
                ),
            )
            con.commit()
            return cur.lastrowid
        finally:
            con.close()
    except Exception:
        return None


# ─────────────────────────────────────────────────────────────
#  Ingest из events.jsonl
# ─────────────────────────────────────────────────────────────

def _events_path(project_dir: str) -> Path:
    return Path(project_dir) / ".ai_link" / "events.jsonl"


def ingest_from_events(project_dir: str,
                       db_path: Optional[Path] = None) -> Dict[str, Any]:
    """Читает events.jsonl, находит model_call/model_response,
    собирает пары и пишет их в cost_ledger.

    Идемпотентность: используем уникальный ключ (run_id, ts,
    model) — если уже есть, пропускаем.

    Возвращает: {"added": N, "skipped": M}.
    """
    src = _events_path(project_dir)
    if not src.exists():
        return {"added": 0, "skipped": 0}

    try:
        lines = src.read_text(encoding="utf-8").splitlines()
    except Exception:
        return {"added": 0, "skipped": 0}

    # Собираем пары model_call → model_response в порядке появления
    pending: List[Dict[str, Any]] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except Exception:
            continue
        et = ev.get("event_type", "")
        if et == "model_call":
            pending.append({
                "ts": ev.get("ts", ""),
                "run_id": ev.get("run_id", ""),
                "phase_id": ev.get("phase_id", ""),
                "agent": (ev.get("payload_json") or {}).get("agent", ""),
                "prompt_len": (ev.get("payload_json") or {}).get(
                    "prompt_len", 0),
            })
        elif et == "model_response" and pending:
            # Сопоставляем по ближайшему предыдущему model_call
            call = pending.pop(0)
            payload = ev.get("payload_json") or {}
            agent = payload.get("agent") or call.get("agent", "")
            call_ts = call.get("ts", "")
            call_run = call.get("run_id", "")
            # Идемпотентность
            if _already_recorded(call_run, call_ts, agent, db_path):
                continue
            record(
                run_id=call_run,
                model=agent,
                role=call.get("phase_id", ""),
                duration_seconds=float(payload.get("elapsed", 0) or 0),
                requests=1,
                success=bool(payload.get("success", False)),
                estimated_cost=None,  # из UI токены недоступны
                ts=call_ts,  # ts события (не now!) — для идемпотентности
                db_path=db_path,
            )

    return {"added": 0, "skipped": 0}  # числа не считаем точно (по ТЗ — тихо)


def _already_recorded(run_id: str, ts: str, model: str,
                      db_path: Optional[Path] = None) -> bool:
    """Проверяет, есть ли запись (run_id, model, day) в ledger."""
    try:
        path = _db_path() if db_path is None else db_path
        if not path.exists():
            return False
        # Грубая проверка: есть ли запись с таким run_id+model за тот же день
        day = (ts or "")[:10]
        con = sqlite3.connect(str(path))
        try:
            cur = con.execute(
                "SELECT COUNT(*) FROM cost_ledger "
                "WHERE run_id = ? AND model = ? AND ts LIKE ?",
                (run_id, model, day + "%"),
            )
            return cur.fetchone()[0] > 0
        finally:
            con.close()
    except Exception:
        return False


# ─────────────────────────────────────────────────────────────
#  Сводка
# ─────────────────────────────────────────────────────────────

def summary(run_id: str = "",
            project_dir: str = "",
            db_path: Optional[Path] = None) -> Dict[str, Any]:
    """Сводка по ledger.

    Фильтры: run_id (если задан) и project_dir (через runs).
    Возвращает агрегаты по моделям и общий итог.
    """
    path = _db_path() if db_path is None else db_path
    if not path.exists():
        return {"exists": False, "total_rows": 0}

    con = sqlite3.connect(str(path))
    con.row_factory = sqlite3.Row
    try:
        where, params = [], []
        if run_id:
            where.append("run_id = ?")
            params.append(run_id)
        elif project_dir:
            # Найдём run_id по project_dir
            cur = con.execute(
                "SELECT run_id FROM runs WHERE project_dir = ?",
                (project_dir,),
            )
            ids = [r[0] for r in cur.fetchall()]
            if not ids:
                return {"exists": True, "total_rows": 0}
            placeholders = ",".join("?" * len(ids))
            where.append(f"run_id IN ({placeholders})")
            params.extend(ids)
        wsql = (" WHERE " + " AND ".join(where)) if where else ""

        cur = con.execute(
            "SELECT model, resource_class, "
            "       COUNT(*) AS calls, "
            "       SUM(duration_seconds) AS seconds, "
            "       SUM(requests) AS requests, "
            "       SUM(CASE WHEN success=1 THEN 1 ELSE 0 END) AS ok, "
            "       SUM(CASE WHEN success=0 THEN 1 ELSE 0 END) AS fail, "
            "       SUM(COALESCE(estimated_cost, 0)) AS cost "
            "FROM cost_ledger" + wsql +
            " GROUP BY model, resource_class ORDER BY seconds DESC",
            params,
        )
        by_model = [dict(r) for r in cur.fetchall()]
        total_rows = sum(r["calls"] for r in by_model)
        total_seconds = sum((r["seconds"] or 0) for r in by_model)
        total_cost = sum((r["cost"] or 0) for r in by_model)
        total_ok = sum(r["ok"] for r in by_model)
        total_fail = sum(r["fail"] for r in by_model)
        return {
            "exists": True,
            "total_rows": total_rows,
            "total_seconds": round(total_seconds, 2),
            "total_cost": round(total_cost, 4),
            "total_ok": total_ok,
            "total_fail": total_fail,
            "by_model": by_model,
        }
    except Exception:
        return {"exists": True, "total_rows": 0}
    finally:
        con.close()


def summary_for_llm(db_path: Optional[Path] = None) -> str:
    """Компактная текстовая сводка — для промпта brain."""
    s = summary(db_path=db_path)
    if not s.get("exists") or not s.get("total_rows"):
        return "COST-LEDGER: пусто"
    lines = [
        f"COST-LEDGER: {s['total_rows']} вызовов, "
        f"{s['total_seconds']:.1f} сек, "
        f"ok={s['total_ok']} fail={s['total_fail']}"
    ]
    for m in s.get("by_model", [])[:6]:
        lines.append(
            f"  • {m['model']} ({m['resource_class']}): "
            f"{m['calls']} вызовов, {(m['seconds'] or 0):.1f} сек, "
            f"ok={m['ok']} fail={m['fail']}"
        )
    return "\n".join(lines)


def reset_ledger(db_path: Optional[Path] = None) -> None:
    """Удаляет все записи cost_ledger (для тестов)."""
    path = _db_path() if db_path is None else db_path
    if not path.exists():
        return
    con = sqlite3.connect(str(path))
    try:
        con.execute("DELETE FROM cost_ledger")
        con.commit()
    except Exception:
        pass
    finally:
        con.close()
