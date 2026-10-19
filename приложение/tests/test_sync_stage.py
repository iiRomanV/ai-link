"""SYNC-STAGE (2026-10-01): run_state.stage_index vs plan.json."""
import json
from pathlib import Path

from core.orchestrator import _detect_continue_phase


def _write_cp(project_dir: Path, stage_index: int, status: str = "running") -> None:
    ai = project_dir / ".ai_link"
    ai.mkdir(parents=True, exist_ok=True)
    cp = {
        "run_id": "test",
        "task": "задача",
        "status": status,
        "phase": {
            "current": "code",
            "stage_index": stage_index,
            "total_stages": 4,
        },
    }
    (ai / "run_state.json").write_text(
        json.dumps(cp, ensure_ascii=False), encoding="utf-8"
    )


def _write_plan(project_dir: Path, done_until: int) -> None:
    """Пишет plan.json с N этапов. Этапы [0..done_until) — done."""
    ai = project_dir / ".ai_link"
    ai.mkdir(parents=True, exist_ok=True)
    stages = []
    for i in range(4):
        status = "done" if i < done_until else "pending"
        stages.append({
            "name": f"Этап {i+1}",
            "items": [{"text": f"шаг {i+1}", "status": status}],
        })
    plan = {"task": "задача", "status": "in_progress", "stages": stages}
    (ai / "plan.json").write_text(
        json.dumps(plan, ensure_ascii=False), encoding="utf-8"
    )


def test_run_state_up_to_date(tmp_path):
    """run_state.stage_index=2, plan.json: 0-1 done, 2 pending.
    Ожидаем: остаётся 2."""
    _write_cp(tmp_path, stage_index=2)
    _write_plan(tmp_path, done_until=2)
    phase, idx = _detect_continue_phase(str(tmp_path), "задача")
    assert idx == 2


def test_run_state_lags_behind(tmp_path):
    """run_state.stage_index=0, plan.json: 0-2 done, 3 pending.
    Ожидаем: поднимается до 3."""
    _write_cp(tmp_path, stage_index=0)
    _write_plan(tmp_path, done_until=3)
    phase, idx = _detect_continue_phase(str(tmp_path), "задача")
    assert idx == 3


def test_all_stages_done(tmp_path):
    """run_state.stage_index=1, plan.json: все done.
    Ожидаем: stage_index не трогается (все done — нечего поднимать)."""
    _write_cp(tmp_path, stage_index=1)
    _write_plan(tmp_path, done_until=4)
    phase, idx = _detect_continue_phase(str(tmp_path), "задача")
    assert idx == 1


def test_no_plan(tmp_path):
    """run_state есть, plan.json нет.
    Ожидаем: stage_index из run_state (нечего сверять)."""
    _write_cp(tmp_path, stage_index=1)
    phase, idx = _detect_continue_phase(str(tmp_path), "задача")
    assert idx == 1


def test_stopped_status_ignores_sync(tmp_path):
    """status=stopped → возврат (PHASE_PLAN, 0) до всякой синхронизации."""
    _write_cp(tmp_path, stage_index=2, status="stopped")
    _write_plan(tmp_path, done_until=1)
    phase, idx = _detect_continue_phase(str(tmp_path), "задача")
    assert idx == 0
