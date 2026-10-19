"""Б63 Уровень A (14.10.2026): архив истории проекта.

Когда project_snapshot показывает, что проект изменился
(откат, ручная правка, git checkout), история прогонов
становится недостоверной. Что делаем:

  1. events.jsonl       → .ai_link/events_archive/<ts>.jsonl
  2. review_log.jsonl   → .ai_link/review_log_archive/<ts>.jsonl
  3. plan.json: все done → needs_recheck
  4. run_state.json     → в stale

Не удаляем — переносим. Можно вернуться к истории.

Совет Perplexity: архив вместо удаления.
"""
import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Dict, Any

import logging

logger = logging.getLogger(__name__)


def _ts() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _ai_dir(project_dir: str) -> Path:
    return Path(project_dir) / ".ai_link"


def archive_history(project_dir: str) -> Dict[str, Any]:
    """Переносит events.jsonl и review_log.jsonl в архивы.

    Возвращает {'archived': [...], 'ts': ...}.
    """
    ai = _ai_dir(project_dir)
    if not ai.exists():
        return {"archived": [], "ts": ""}

    ts = _ts()
    result = {"archived": [], "ts": ts}

    # events.jsonl → events_archive/
    events_src = ai / "events.jsonl"
    if events_src.exists():
        events_arc_dir = ai / "events_archive"
        events_arc_dir.mkdir(exist_ok=True)
        dst = events_arc_dir / f"{ts}.jsonl"
        try:
            shutil.move(str(events_src), str(dst))
            result["archived"].append(f"events.jsonl → events_archive/{ts}.jsonl")
            logger.info(
                "HISTORY-ARCHIVE: events.jsonl → events_archive/%s.jsonl",
                ts,
            )
        except Exception as e:
            logger.warning("HISTORY-ARCHIVE: events не перенести: %s", e)

    # review_log.jsonl → review_log_archive/
    review_src = ai / "review_log.jsonl"
    if review_src.exists():
        review_arc_dir = ai / "review_log_archive"
        review_arc_dir.mkdir(exist_ok=True)
        dst = review_arc_dir / f"{ts}.jsonl"
        try:
            shutil.move(str(review_src), str(dst))
            result["archived"].append(
                f"review_log.jsonl → review_log_archive/{ts}.jsonl"
            )
            logger.info(
                "HISTORY-ARCHIVE: review_log.jsonl → "
                "review_log_archive/%s.jsonl", ts,
            )
        except Exception as e:
            logger.warning("HISTORY-ARCHIVE: review_log не перенести: %s", e)

    return result


def reset_done_to_needs_recheck(project_dir: str) -> int:
    """В plan.json меняет все done → needs_recheck.

    Возвращает количество изменённых items.
    """
    plan_path = _ai_dir(project_dir) / "plan.json"
    if not plan_path.exists():
        return 0
    try:
        data = json.loads(plan_path.read_text(encoding="utf-8"))
    except Exception:
        return 0

    changed = 0
    for stage in data.get("stages", []) or []:
        # stage.status: done → needs_recheck
        if stage.get("status") == "done":
            stage["status"] = "needs_recheck"
            changed += 1
        for item in stage.get("items", []) or []:
            if item.get("status") == "done":
                item["status"] = "needs_recheck"
                changed += 1

    # Если все этапы были done — план не done
    if data.get("status") == "done":
        data["status"] = "needs_recheck"

    try:
        plan_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as e:
        logger.warning("HISTORY-ARCHIVE: plan.json не записать: %s", e)
        return 0

    if changed:
        logger.warning(
            "HISTORY-ARCHIVE: done → needs_recheck (%d items)", changed
        )
    return changed


def on_project_changed(project_dir: str, diff: Dict[str, Any]) -> Dict[str, Any]:
    """Полный сценарий при обнаружении изменений проекта.

    Шаги:
      1. Архивировать events и review_log.
      2. Сбросить done → needs_recheck в plan.json.
      3. Переместить run_state.json в stale.

    Возвращает отчёт.
    """
    result = {
        "archived": [],
        "needs_recheck": 0,
        "run_state_stale": False,
    }

    # 1. Архив истории
    arc = archive_history(project_dir)
    result["archived"] = arc.get("archived", [])
    result["ts"] = arc.get("ts", "")

    # 2. done → needs_recheck
    result["needs_recheck"] = reset_done_to_needs_recheck(project_dir)

    # 3. run_state в stale
    ai = _ai_dir(project_dir)
    run_state = ai / "run_state.json"
    if run_state.exists():
        try:
            ts = _ts()
            dst = run_state.with_name(f"run_state.json.stale_{ts}")
            shutil.move(str(run_state), str(dst))
            result["run_state_stale"] = True
            logger.info("HISTORY-ARCHIVE: run_state.json → stale_%s", ts)
        except Exception as e:
            logger.warning("HISTORY-ARCHIVE: run_state не убрать: %s", e)

    return result
