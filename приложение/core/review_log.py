"""Журнал review-вердиктов для оркестратора.

Пишет каждый review в review_log.jsonl и считает статистику:
  • сколько ревьюер придирается ложно (syntax_errors_before == 0)
  • сколько валидных (syntax_errors_before > 0)
  • какой агент-ревьюер как часто ошибается
"""

import json
from pathlib import Path
from typing import Dict, Any, List, Optional
from datetime import datetime


def _log_path(project_dir: str) -> Path:
    return Path(project_dir) / ".ai_link" / "review_log.jsonl"


def count_syntax_errors(project_dir: str) -> int:
    """Считает синтаксические ошибки в проекте через auditor."""
    try:
        from core.auditor import check_syntax
        return len(check_syntax(project_dir))
    except Exception:
        return 0


def log_review(project_dir: str,
               agent: str,
               verdict: str,
               output: str = "",
               syntax_errors_before: int = 0,
               fix_applied: bool = False,
               syntax_errors_after: int = 0,
               validated_override=None,
               severity: str = "") -> Dict[str, Any]:
    """Пишет запись в review_log.jsonl.

    verdict: "ok" | "problems" | "unknown"
               | "problems_rejected" | "problems_confirmed"
               | "problems_unknown"

    validated_override — явный признак «проблема реальна»
    (True) / «придирка» (False). Перебивает всё остальное.

    severity: "critical" | "major" | "minor" | "unknown" | ""
    Используется для определения validated, когда нет override:
      • critical → True (точно чиним)
      • minor    → False (стиль, игнорим)
      • иначе    → None (не оценивали — не портим статистику)
    """
    path = _log_path(project_dir)
    path.parent.mkdir(parents=True, exist_ok=True)

    # CROSS-REVIEW-2: правильное определение validated.
    # Раньше: validated = syntax_errors > 0 для problems.
    # Это ошибочно: логическая ошибка/SQL-инъекция могут быть
    # без синтаксических ошибок, а review помечался как «придирка».
    if validated_override is not None:
        validated = bool(validated_override)
    elif verdict == "problems_confirmed":
        validated = True
    elif verdict == "problems_rejected":
        validated = False
    elif verdict in ("problems", "problems_unknown"):
        sev = (severity or "").lower()
        if sev == "critical":
            validated = True
        elif sev == "minor":
            validated = False
        elif syntax_errors_before > 0:
            validated = True
        else:
            # Не оценивали — не портим статистику.
            # Решит cross-verify или ручной разбор.
            validated = None
    else:
        validated = None  # ok / unknown

    record = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "agent": agent,
        "verdict": verdict,
        "syntax_errors_before": int(syntax_errors_before),
        "fix_applied": bool(fix_applied),
        "syntax_errors_after": int(syntax_errors_after),
        "validated": validated,
        "output_head": (output or "")[:200],
    }
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        pass
    return record


def load_reviews(project_dir: str, limit: int = 50) -> List[Dict[str, Any]]:
    """Читает последние N записей review."""
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


def get_reviewer_stats(project_dir: str) -> Dict[str, Dict[str, Any]]:
    """Статистика по агентам-ревьюерам.

    Возвращает:
      {
        "browser_qwen": {
            "total": 15,
            "ok": 3,
            "problems": 12,
            "validated_true": 2,
            "validated_false": 10,
            "validated_unknown": 0,
            "false_alarm_rate": 0.833
        }
      }
    """
    records = load_reviews(project_dir, limit=1000)
    stats: Dict[str, Dict[str, Any]] = {}
    for r in records:
        agent = r.get("agent", "unknown")
        s = stats.setdefault(agent, {
            "total": 0, "ok": 0, "problems": 0, "unknown": 0,
            "validated_true": 0, "validated_false": 0,
            "validated_unknown": 0,
        })
        s["total"] += 1
        v = r.get("verdict", "unknown")
        if v == "ok":
            s["ok"] += 1
        elif v in ("problems", "problems_confirmed",
                   "problems_rejected", "problems_unknown"):
            s["problems"] += 1
            val = r.get("validated")
            if val is True:
                s["validated_true"] += 1
            elif val is False:
                s["validated_false"] += 1
            else:
                s["validated_unknown"] += 1
        else:
            s["unknown"] += 1

    # Считаем false_alarm_rate
    for agent, s in stats.items():
        checked = s["validated_true"] + s["validated_false"]
        if checked > 0:
            s["false_alarm_rate"] = round(s["validated_false"] / checked, 3)
        else:
            s["false_alarm_rate"] = None
    return stats


def reset_log(project_dir: str) -> None:
    """Удаляет журнал (для тестов)."""
    path = _log_path(project_dir)
    if path.exists():
        try:
            path.unlink()
        except Exception:
            pass
