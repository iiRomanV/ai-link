"""PLAN-REPLACE-TITLE (2026-10-02): при замене title/goal — новые."""
import json
from pathlib import Path


def _write_plan(path, task, status, title, goal, stages):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "title": title, "goal": goal, "task": task,
        "status": status, "created_at": "2026-10-01T00:00:00",
        "stages": stages,
    }, ensure_ascii=False), encoding="utf-8")


def _plan_text(title, goal, stage_names):
    lines = [f"НАЗВАНИЕ: {title}", f"ЦЕЛЬ: {goal}", ""]
    for name in stage_names:
        lines.append(f"ЭТАП: {name}")
        lines.append("- шаг 1")
        lines.append("- шаг 2")
        lines.append("")
    return "\n".join(lines)


def test_replace_title_uses_new(tmp_path):
    """status=done → title/goal берём новые."""
    from core.orchestrator import _save_project_plan
    plan_path = tmp_path / ".ai_link" / "plan.json"
    _write_plan(plan_path, "старая", "done",
                "СТАРОЕ НАЗВАНИЕ", "СТАРАЯ ЦЕЛЬ",
                [{"name": "Старый", "items": [{"text": "x", "status": "done"}]}])

    _save_project_plan(
        str(tmp_path),
        _plan_text("НОВОЕ НАЗВАНИЕ", "НОВАЯ ЦЕЛЬ", ["Новый этап"]),
        "новая задача",
    )

    data = json.loads(plan_path.read_text(encoding="utf-8"))
    assert data["title"] == "НОВОЕ НАЗВАНИЕ"
    assert data["goal"] == "НОВАЯ ЦЕЛЬ"


def test_append_keeps_old_title(tmp_path):
    """status=in_progress → title/goal старые (доработка)."""
    from core.orchestrator import _save_project_plan
    plan_path = tmp_path / ".ai_link" / "plan.json"
    _write_plan(plan_path, "текущая", "in_progress",
                "СТАРОЕ НАЗВАНИЕ", "СТАРАЯ ЦЕЛЬ",
                [{"name": "Старый", "items": [{"text": "x", "status": "pending"}]}])

    _save_project_plan(
        str(tmp_path),
        _plan_text("НОВОЕ", "НОВАЯ", ["Новый этап"]),
        "текущая",
    )

    data = json.loads(plan_path.read_text(encoding="utf-8"))
    # При дописывании — старое название сохраняется
    assert data["title"] == "СТАРОЕ НАЗВАНИЕ"


def test_no_old_plan_uses_new(tmp_path):
    """Нет старого плана → новые title/goal."""
    from core.orchestrator import _save_project_plan
    _save_project_plan(
        str(tmp_path),
        _plan_text("ПЕРВОЕ", "ЦЕЛЬ 1", ["Этап 1"]),
        "первая задача",
    )
    plan_path = tmp_path / ".ai_link" / "plan.json"
    data = json.loads(plan_path.read_text(encoding="utf-8"))
    assert data["title"] == "ПЕРВОЕ"
    assert data["goal"] == "ЦЕЛЬ 1"
