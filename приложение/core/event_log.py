"""Durable event log (EVENT-LOG).

Пишет события в <project>/.ai_link/events.jsonl — append-only.

Зачем: при падении процесса видно, что именно произошло, и
можно делать идемпотентный resume. Дополняет run_state.json
(там — снимок, здесь — поток событий между снимками).

Каждое событие:
  {
    "ts": "2026-09-22T12:00:00",
    "run_id": "...",
    "phase_id": "code",
    "iteration": 3,
    "event_type": "tool_call_requested",
    "payload_json": {...}
  }

Типы событий (планируемые):
  run_started, run_finished,
  phase_started, phase_completed,
  checkpoint_saved, checkpoint_cleared,
  model_call, model_response,
  tool_call_requested, tool_result_received,
  browser_restarted,
  run_paused, run_resumed, run_failed

Контекст прогона (run_id, project_dir, phase_id, iteration)
передаётся через contextvars — чтобы не тащить его через
десятки функций. Устанавливается в orchestrate().
"""

import json
import contextvars
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, List, Optional


# ─────────────────────────────────────────────────────────────
#  Контекст прогона (неявная передача run_id/phase/iteration)
# ─────────────────────────────────────────────────────────────

_run_ctx: contextvars.ContextVar[Dict[str, Any]] = contextvars.ContextVar(
    "ai_link_run_ctx", default={}
)


def set_run_context(run_id: str = "", project_dir: str = "",
                    phase_id: str = "", iteration: int = 0) -> None:
    """Устанавливает текущий контекст прогона."""
    _run_ctx.set({
        "run_id": run_id,
        "project_dir": project_dir,
        "phase_id": phase_id,
        "iteration": iteration,
    })


def update_context(phase_id: Optional[str] = None,
                   iteration: Optional[int] = None) -> None:
    """Точечно обновляет phase_id/iteration в текущем контексте."""
    cur = dict(_run_ctx.get())
    if phase_id is not None:
        cur["phase_id"] = phase_id
    if iteration is not None:
        cur["iteration"] = iteration
    _run_ctx.set(cur)


def get_context() -> Dict[str, Any]:
    """Возвращает текущий контекст (копию)."""
    return dict(_run_ctx.get())


def clear_context() -> None:
    """Сбрасывает контекст (для тестов и завершения прогона)."""
    _run_ctx.set({})


# ─────────────────────────────────────────────────────────────
#  Запись / чтение
# ─────────────────────────────────────────────────────────────

def _log_path(project_dir: str) -> Path:
    return Path(project_dir) / ".ai_link" / "events.jsonl"


def log_event(project_dir: str = "",
              event_type: str = "",
              payload: Optional[Dict[str, Any]] = None,
              run_id: str = "",
              phase_id: str = "",
              iteration: int = 0) -> Dict[str, Any]:
    """Пишет событие в events.jsonl.

    Пустые run_id/phase_id/iteration/project_dir берутся
    из текущего контекста. Никогда не падает: при ошибке
    записи тихо возвращает сформированную запись.
    """
    ctx = _run_ctx.get()
    if not run_id:
        run_id = ctx.get("run_id", "")
    if not phase_id:
        phase_id = ctx.get("phase_id", "")
    if not iteration:
        iteration = ctx.get("iteration", 0) or 0
    if not project_dir:
        project_dir = ctx.get("project_dir", "")

    record = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "run_id": run_id,
        "phase_id": phase_id,
        "iteration": int(iteration),
        "event_type": event_type,
        "payload_json": payload or {},
    }
    if not project_dir:
        return record
    try:
        path = _log_path(project_dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        pass
    return record


def load_events(project_dir: str, limit: int = 200) -> List[Dict[str, Any]]:
    """Читает последние N событий из events.jsonl."""
    path = _log_path(project_dir)
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except Exception:
        return []
    result: List[Dict[str, Any]] = []
    for line in lines[-limit:]:
        line = line.strip()
        if not line:
            continue
        try:
            result.append(json.loads(line))
        except Exception:
            continue
    return result


def count_events(project_dir: str, event_type: str = "") -> int:
    """Считает события (все или конкретного типа)."""
    events = load_events(project_dir, limit=100000)
    if not event_type:
        return len(events)
    return sum(1 for e in events if e.get("event_type") == event_type)


def reset_log(project_dir: str) -> None:
    """Удаляет журнал (для тестов)."""
    path = _log_path(project_dir)
    if path.exists():
        try:
            path.unlink()
        except Exception:
            pass
