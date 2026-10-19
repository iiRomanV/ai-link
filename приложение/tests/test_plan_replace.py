"""PLAN-REPLACE (2026-10-01): замена плана при status=done."""
import json
import tempfile
from pathlib import Path

import pytest


def _write_plan(path: Path, task: str, status: str, stages):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "title": "Test",
        "goal": "Test",
        "task": task,
        "status": status,
        "created_at": "2026-10-01T00:00:00",
        "stages": stages,
    }, ensure_ascii=False), encoding="utf-8")


def _plan_text(stage_names):
    """Строит plan_text в формате, который парсит _save_project_plan."""
    lines = ["НАЗВАНИЕ: Test", "ЦЕЛЬ: Test goal", ""]
    for name in stage_names:
        lines.append(f"ЭТАП: {name}")
        lines.append("- шаг 1")
        lines.append("- шаг 2")
        lines.append("")
    return "\n".join(lines)


def test_plan_replace_when_done(tmp_path):
    """Старый план status=done → заменяется (не дописывается)."""
    from core.orchestrator import _save_project_plan
    plan_path = tmp_path / ".ai_link" / "plan.json"
    _write_plan(plan_path, "старая", "done", [
        {"name": "Старый этап A", "items": [{"text": "x", "status": "done"}]},
        {"name": "Старый этап B", "items": [{"text": "y", "status": "done"}]},
    ])

    _save_project_plan(
        str(tmp_path), _plan_text(["Новый этап 1", "Новый этап 2"]),
        "новая задача",
    )

    data = json.loads(plan_path.read_text(encoding="utf-8"))
    names = [s["name"] for s in data["stages"]]
    assert "Старый этап A" not in names
    assert "Старый этап B" not in names
    assert "Новый этап 1" in names
    assert "Новый этап 2" in names
    assert len(data["stages"]) == 2


def test_plan_append_when_in_progress(tmp_path):
    """Старый план status=in_progress → дописывается."""
    from core.orchestrator import _save_project_plan
    plan_path = tmp_path / ".ai_link" / "plan.json"
    _write_plan(plan_path, "текущая", "in_progress", [
        {"name": "Старый этап", "items": [{"text": "x", "status": "pending"}]},
    ])

    _save_project_plan(
        str(tmp_path), _plan_text(["Новый этап"]), "текущая",
    )

    data = json.loads(plan_path.read_text(encoding="utf-8"))
    names = [s["name"] for s in data["stages"]]
    assert "Старый этап" in names
    assert "Новый этап" in names
    assert len(data["stages"]) == 2


def test_plan_fresh_when_no_file(tmp_path):
    """Нет файла → создаётся."""
    from core.orchestrator import _save_project_plan
    _save_project_plan(
        str(tmp_path), _plan_text(["Этап 1"]), "первая задача",
    )
    plan_path = tmp_path / ".ai_link" / "plan.json"
    assert plan_path.exists()
    data = json.loads(plan_path.read_text(encoding="utf-8"))
    assert data["status"] == "in_progress"
    assert len(data["stages"]) == 1


def test_plan_replace_creates_stale(tmp_path):
    """При замене старый план уходит в .stale."""
    from core.orchestrator import _save_project_plan
    plan_path = tmp_path / ".ai_link" / "plan.json"
    _write_plan(plan_path, "старая", "done", [
        {"name": "Старый", "items": [{"text": "x", "status": "done"}]},
    ])

    _save_project_plan(
        str(tmp_path), _plan_text(["Новый"]), "новая",
    )

    stales = list((tmp_path / ".ai_link").glob("plan.json.stale_*"))
    assert len(stales) >= 1
