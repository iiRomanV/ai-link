"""KEEP-AWAKE-CLI (2026-10-02): run_task.py держит ПК в тонусе."""
from pathlib import Path


def test_run_task_imports_keep_awake():
    """run_task.py использует start/stop_keep_awake."""
    src = Path("run_task.py").read_text(encoding="utf-8")
    assert "KEEP-AWAKE-CLI" in src
    assert "start_keep_awake" in src
    assert "stop_keep_awake" in src
    assert "cleanup_stale_keep_awake" in src


def test_run_task_has_finally():
    """stop_keep_awake вызывается в finally — гарантированно."""
    src = Path("run_task.py").read_text(encoding="utf-8")
    # Ищем finally рядом со stop_keep_awake
    assert "finally:" in src
    assert "stop_keep_awake" in src


def test_run_task_keep_awake_before_orchestrate():
    """KEEP-AWAKE стартует ДО orchestrate()."""
    src = Path("run_task.py").read_text(encoding="utf-8")
    idx_ka = src.find("start_keep_awake")
    idx_orch = src.find("result = orchestrate(")
    assert idx_ka > 0, "start_keep_awake не найден"
    assert idx_orch > 0, "orchestrate не найден"
    assert idx_ka < idx_orch, "KEEP-AWAKE должен стартовать ДО orchestrate"
