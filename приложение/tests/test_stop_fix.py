"""Тесты для FIX STOP-FIX (2026-09-29).

STOP обнуляет фазу (следующая задача → plan).
PAUSE сохраняет фазу (продолжение).
"""
import json
from pathlib import Path

from core.orchestrator import (
    _detect_continue_phase, PHASE_PLAN, PHASE_CODE, PHASE_REVIEW,
)


def _write_checkpoint(project_dir: Path, status: str,
                      phase_current: str = "review",
                      stage_index: int = 0):
    """Пишет минимальный run_state.json."""
    ai_link = project_dir / ".ai_link"
    ai_link.mkdir(parents=True, exist_ok=True)
    data = {
        "run_id": "test_run",
        "project_dir": str(project_dir),
        "status": status,
        "phase": {
            "current": phase_current,
            "stage_index": stage_index,
        },
    }
    (ai_link / "run_state.json").write_text(
        json.dumps(data), encoding="utf-8"
    )


def _write_plan(project_dir: Path):
    """Пишет минимальный plan.json."""
    ai_link = project_dir / ".ai_link"
    ai_link.mkdir(parents=True, exist_ok=True)
    plan = {
        "status": "in_progress",
        "stages": [
            {"name": "Stage 1", "items": [
                {"text": "item 1", "status": "pending"}
            ]},
        ],
    }
    (ai_link / "plan.json").write_text(
        json.dumps(plan), encoding="utf-8"
    )


def test_stopped_starts_from_plan(tmp_path):
    """STOP → status=stopped → старт с PLAN (не с review)."""
    _write_checkpoint(tmp_path, status="stopped", phase_current="review")
    _write_plan(tmp_path)
    phase, stage = _detect_continue_phase(str(tmp_path), "новая задача")
    assert phase == PHASE_PLAN
    assert stage == 0


def test_paused_continues_from_phase(tmp_path):
    """PAUSE + items НЕ pending → продолжаем с review."""
    _write_checkpoint(tmp_path, status="paused", phase_current="review")
    # План с уже закрытыми items (code уже отработал)
    ai_link = tmp_path / ".ai_link"
    ai_link.mkdir(parents=True, exist_ok=True)
    plan = {
        "status": "in_progress",
        "stages": [
            {"name": "Stage 1",
             "items": [{"text": "item 1", "status": "done"}]},
        ],
    }
    (ai_link / "plan.json").write_text(
        json.dumps(plan), encoding="utf-8")
    phase, stage = _detect_continue_phase(str(tmp_path), "продолжить")
    assert phase == PHASE_REVIEW


def test_running_continues_from_phase(tmp_path):
    """RUNNING + items НЕ pending → продолжаем с review."""
    _write_checkpoint(tmp_path, status="running", phase_current="review")
    ai_link = tmp_path / ".ai_link"
    ai_link.mkdir(parents=True, exist_ok=True)
    plan = {
        "status": "in_progress",
        "stages": [
            {"name": "Stage 1",
             "items": [{"text": "item 1", "status": "in_progress"}]},
        ],
    }
    (ai_link / "plan.json").write_text(
        json.dumps(plan), encoding="utf-8")
    phase, stage = _detect_continue_phase(str(tmp_path), "")
    assert phase == PHASE_REVIEW


def test_no_checkpoint_starts_fresh(tmp_path):
    """Нет чекпоинта → план не создан → (None, 0)."""
    phase, stage = _detect_continue_phase(str(tmp_path), "новая задача")
    assert phase is None
    assert stage == 0


def test_stopped_with_plan_done_still_plan(tmp_path):
    """STOP даже если plan.json есть — старт с plan."""
    _write_checkpoint(tmp_path, status="stopped", phase_current="code")
    _write_plan(tmp_path)
    phase, stage = _detect_continue_phase(str(tmp_path), "новая задача")
    assert phase == PHASE_PLAN
    assert stage == 0

def test_paused_with_pending_resets_to_code(tmp_path):
    """PHASE-RESET: чекпоинт=review, но items этапа ВСЕ pending
    → старт с code (code не выполнялся)."""
    _write_checkpoint(tmp_path, status="paused", phase_current="review",
                      stage_index=0)
    _write_plan(tmp_path)   # 1 pending item в stage 0
    phase, stage = _detect_continue_phase(str(tmp_path), "продолжить")
    assert phase == PHASE_CODE
    assert stage == 0
