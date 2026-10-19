"""Тесты для core.night_mode."""

from unittest.mock import patch
from pathlib import Path
from core.night_mode import (
    NightConfig, NightResult, run_night,
    _build_report, _save_report, _save_checkpoint,
)


def _fake_orch_success(task, project_dir, **kwargs):
    """Мок: успешная оркестрация."""
    return type("R", (), {
        "success": True, "status": "success", "error": "",
    })()


def _fake_orch_fail(task, project_dir, **kwargs):
    return type("R", (), {
        "success": False, "status": "failed", "error": "test error",
    })()


def test_config_defaults():
    c = NightConfig()
    assert c.max_hours == 8.0
    assert c.max_tasks == 15
    assert c.max_consecutive_failures == 2
    assert c.task_timeout == 600.0


def test_run_night_all_success(tmp_path):
    tasks = ["task 1", "task 2", "task 3"]
    with patch("core.orchestrator.orchestrate",
               side_effect=_fake_orch_success):
        r = run_night(tasks, str(tmp_path))
    assert r.success
    assert r.tasks_done == 3
    assert r.tasks_failed == 0
    assert r.status == "completed"
    assert r.report_path
    assert Path(r.report_path).exists()


def test_run_night_stops_on_consecutive_failures(tmp_path):
    tasks = ["t1", "t2", "t3", "t4", "t5"]
    with patch("core.orchestrator.orchestrate",
               side_effect=_fake_orch_fail):
        r = run_night(tasks, str(tmp_path))
    # Должен остановиться на 2-м провале
    assert r.status == "stopped_consecutive_failures"
    assert r.tasks_failed == 2
    assert r.consecutive_failures == 2
    # Задачи 3-5 не выполнялись
    assert len(r.task_results) == 2


def test_run_night_resets_counter_on_success(tmp_path):
    """После провала успешная задача сбрасывает счётчик."""
    def fake_orch(task, project_dir, **kwargs):
        if task == "bad":
            return type("R", (), {"success": False, "status": "failed",
                                  "error": "err"})()
        return type("R", (), {"success": True, "status": "success",
                              "error": ""})()

    tasks = ["bad", "good", "bad", "good", "good"]
    with patch("core.orchestrator.orchestrate", side_effect=fake_orch):
        r = run_night(tasks, str(tmp_path))
    # Провалы не подряд — не останавливается
    assert r.tasks_failed == 2
    assert r.tasks_done == 3
    assert r.status in ("completed", "completed_partial")


def test_run_night_max_tasks(tmp_path):
    tasks = [f"task {i}" for i in range(10)]
    cfg = NightConfig(max_tasks=3)
    with patch("core.orchestrator.orchestrate",
               side_effect=_fake_orch_success):
        r = run_night(tasks, str(tmp_path), config=cfg)
    assert r.tasks_done == 3
    assert r.status == "max_tasks"


def test_run_night_exception_in_orchestrator(tmp_path):
    with patch("core.orchestrator.orchestrate",
               side_effect=RuntimeError("boom")):
        r = run_night(["t1"], str(tmp_path))
    assert r.tasks_failed == 1
    assert r.task_results[0]["status"] == "exception"


def test_run_night_report_contains_tasks(tmp_path):
    with patch("core.orchestrator.orchestrate",
               side_effect=_fake_orch_success):
        r = run_night(["Task X", "Task Y"], str(tmp_path))
    report = Path(r.report_path).read_text(encoding="utf-8")
    assert "Task X" in report
    assert "Task Y" in report
    assert "Ночной отчёт" in report


def test_run_night_callback(tmp_path):
    calls = []

    def cb(num, task, result):
        calls.append((num, task, result.get("success")))

    with patch("core.orchestrator.orchestrate",
               side_effect=_fake_orch_success):
        run_night(["A", "B"], str(tmp_path), progress_callback=cb)
    assert len(calls) == 2
    assert calls[0][0] == 1
    assert calls[1][0] == 2


def test_run_night_empty_tasks(tmp_path):
    r = run_night([], str(tmp_path))
    assert r.tasks_done == 0
    assert r.status in ("completed", "completed_partial")


def test_checkpoint_saved(tmp_path):
    with patch("core.orchestrator.orchestrate",
               side_effect=_fake_orch_success):
        run_night(["t1"], str(tmp_path))
    ckpt = Path(tmp_path) / ".ai_link" / "night_checkpoint.json"
    assert ckpt.exists()


def test_build_report_no_failures():
    r = NightResult(
        success=True, status="completed",
        tasks_done=3, tasks_failed=0,
        started_at="2026-09-13T22:00:00",
        finished_at="2026-09-13T23:00:00",
        elapsed=3600,
        task_results=[
            {"task": "A", "success": True, "status": "success",
             "error": "", "seconds": 100},
        ],
    )
    report = _build_report(r, NightConfig())
    assert "Все задачи выполнены" in report
