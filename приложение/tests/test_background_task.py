"""Тесты для core.background_task."""

import time
from unittest.mock import patch
from core.background_task import (
    BackgroundTask, BackgroundTaskState,
    ST_IDLE, ST_RUNNING, ST_PAUSED, ST_STOPPING,
    ST_DONE, ST_FAILED, ST_STOPPED,
)


def _fake_orch_success(task, project_dir, **kwargs):
    # Имитируем работу с прогрессом
    cb = kwargs.get("progress_callback")
    should_stop = kwargs.get("should_stop")
    for phase in ["plan", "code", "review"]:
        if should_stop and should_stop():
            return type("R", (), {
                "success": False, "status": "stopped",
                "final_phase": phase, "phases_done": 0,
                "error": "остановлено пользователем", "seconds": 0.0,
            })()
        if cb:
            pr = type("PR", (), {
                "status": "success", "agent": "test",
                "seconds": 0.1, "error": "",
            })()
            cb(phase, pr)
        time.sleep(0.05)
    return type("R", (), {
        "success": True, "status": "success",
        "final_phase": "review", "phases_done": 3,
        "error": "", "seconds": 1.0,
    })()


def test_initial_state(tmp_path):
    bt = BackgroundTask("задача", str(tmp_path))
    s = bt.status()
    assert s.status == ST_IDLE
    assert s.task == "задача"
    assert s.phase_results == []


def test_start_returns_true(tmp_path):
    with patch("core.orchestrator.orchestrate",
               side_effect=_fake_orch_success):
        bt = BackgroundTask("задача", str(tmp_path))
        ok = bt.start()
        assert ok
        bt.wait(timeout=5)
        assert bt.status().status == ST_DONE


def test_start_twice_fails(tmp_path):
    def slow_orch(task, project_dir, **kwargs):
        time.sleep(0.3)
        return type("R", (), {"success": True, "status": "success",
                              "final_phase": "", "phases_done": 0,
                              "error": "", "seconds": 0.3})()

    with patch("core.orchestrator.orchestrate", side_effect=slow_orch):
        bt = BackgroundTask("задача", str(tmp_path))
        assert bt.start() is True
        assert bt.start() is False  # уже запущена
        bt.wait(timeout=5)


def test_on_progress_callback(tmp_path):
    calls = []

    def cb(phase, result):
        calls.append(phase)

    with patch("core.orchestrator.orchestrate",
               side_effect=_fake_orch_success):
        bt = BackgroundTask("задача", str(tmp_path), on_progress=cb)
        bt.start()
        bt.wait(timeout=5)
    assert "plan" in calls
    assert "code" in calls
    assert "review" in calls


def test_on_finish_callback(tmp_path):
    finishes = []

    def cb(state):
        finishes.append(state.status)

    with patch("core.orchestrator.orchestrate",
               side_effect=_fake_orch_success):
        bt = BackgroundTask("задача", str(tmp_path), on_finish=cb)
        bt.start()
        bt.wait(timeout=5)
    assert len(finishes) == 1
    assert finishes[0] == ST_DONE


def test_stop_flag_stops_task(tmp_path):
    def slow_orch(task, project_dir, **kwargs):
        should_stop = kwargs.get("should_stop")
        for i in range(20):
            if should_stop and should_stop():
                return type("R", (), {
                    "success": False, "status": "stopped",
                    "final_phase": "plan", "phases_done": 1,
                    "error": "остановлено пользователем", "seconds": 0.5,
                })()
            time.sleep(0.05)
        return type("R", (), {"success": True, "status": "success",
                              "final_phase": "review", "phases_done": 20,
                              "error": "", "seconds": 1.0})()

    with patch("core.orchestrator.orchestrate", side_effect=slow_orch):
        bt = BackgroundTask("задача", str(tmp_path))
        bt.start()
        time.sleep(0.15)
        bt.stop()
        bt.wait(timeout=5)
        assert bt.status().status == ST_STOPPED


def test_pause_and_resume(tmp_path):
    """Пауза задерживает, resume продолжает."""
    def orch(task, project_dir, **kwargs):
        should_stop = kwargs.get("should_stop")
        cb = kwargs.get("progress_callback")
        for i, phase in enumerate(["plan", "code", "review"]):
            # Проверка stop/pause В НАЧАЛЕ каждой фазы
            if should_stop and should_stop():
                return type("R", (), {"success": False, "status": "stopped",
                                      "final_phase": "", "phases_done": 0,
                                      "error": "", "seconds": 0.0})()
            if cb:
                pr = type("PR", (), {"status": "success", "agent": "t",
                                     "seconds": 0.1, "error": ""})()
                cb(phase, pr)
            # Задержка между фазами — чтобы успеть нажать pause
            time.sleep(0.3)
        return type("R", (), {"success": True, "status": "success",
                              "final_phase": "review", "phases_done": 3,
                              "error": "", "seconds": 0.9})()

    with patch("core.orchestrator.orchestrate", side_effect=orch):
        bt = BackgroundTask("задача", str(tmp_path))
        bt.start()
        # Ждём немного, чтобы первая фаза прошла
        time.sleep(0.1)
        bt.pause()
        assert bt.status().status == ST_PAUSED
        time.sleep(0.3)
        # Всё ещё на паузе (не завершилась)
        assert bt.status().status == ST_PAUSED
        bt.resume()
        assert bt.status().status == ST_RUNNING
        bt.wait(timeout=5)
        assert bt.status().status == ST_DONE


def test_exception_in_orch_fails_task(tmp_path):
    with patch("core.orchestrator.orchestrate",
               side_effect=RuntimeError("boom")):
        bt = BackgroundTask("задача", str(tmp_path))
        bt.start()
        bt.wait(timeout=5)
        s = bt.status()
        assert s.status == ST_FAILED
        assert "boom" in s.error


def test_status_is_snapshot(tmp_path):
    """status() возвращает копию, не ссылку."""
    with patch("core.orchestrator.orchestrate",
               side_effect=_fake_orch_success):
        bt = BackgroundTask("задача", str(tmp_path))
        bt.start()
        bt.wait(timeout=5)
    s1 = bt.status()
    s2 = bt.status()
    assert s1 is not s2
    assert s1.phase_results == s2.phase_results
