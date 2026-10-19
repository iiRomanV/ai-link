"""STALE-STATE (2026-10-01): сброс чекпоинта при смене задачи."""
import json
from pathlib import Path

from core.orchestrator import _reset_stale_state, _checkpoint_path


def _write_cp(project_dir: Path, task: str, status: str = "running") -> None:
    ai = project_dir / ".ai_link"
    ai.mkdir(parents=True, exist_ok=True)
    cp = {
        "run_id": "test",
        "project_dir": str(project_dir),
        "task": task,
        "status": status,
        "phase": {
            "current": "review",
            "stage_index": 14,
            "total_stages": 21,
        },
    }
    _checkpoint_path(str(project_dir)).write_text(
        json.dumps(cp, ensure_ascii=False), encoding="utf-8"
    )


def _write_plan(project_dir: Path) -> None:
    ai = project_dir / ".ai_link"
    ai.mkdir(parents=True, exist_ok=True)
    plan = {"task": "old", "stages": [{"name": "s1"}]}
    (ai / "plan.json").write_text(
        json.dumps(plan, ensure_ascii=False), encoding="utf-8"
    )


def test_same_task_keeps_checkpoint(tmp_path):
    _write_cp(tmp_path, "Доработай X")
    _write_plan(tmp_path)
    assert _reset_stale_state(str(tmp_path), "Доработай X") is False
    assert _checkpoint_path(str(tmp_path)).exists()
    assert (tmp_path / ".ai_link" / "plan.json").exists()


def test_new_task_resets_checkpoint_and_plan(tmp_path):
    _write_cp(tmp_path, "Напиши проект с нуля")
    _write_plan(tmp_path)
    assert _reset_stale_state(str(tmp_path), "Добавь кнопку") is True
    assert not _checkpoint_path(str(tmp_path)).exists()
    assert not (tmp_path / ".ai_link" / "plan.json").exists()
    stale = list((tmp_path / ".ai_link").glob("*.stale_*"))
    assert len(stale) == 2


def test_no_checkpoint_no_reset(tmp_path):
    assert _reset_stale_state(str(tmp_path), "Добавь кнопку") is False


def test_empty_task_keeps_checkpoint(tmp_path):
    _write_cp(tmp_path, "Напиши проект")
    assert _reset_stale_state(str(tmp_path), "") is False
    assert _checkpoint_path(str(tmp_path)).exists()


def test_whitespace_only_task_keeps_checkpoint(tmp_path):
    _write_cp(tmp_path, "Напиши проект")
    assert _reset_stale_state(str(tmp_path), "   ") is False
    assert _checkpoint_path(str(tmp_path)).exists()
