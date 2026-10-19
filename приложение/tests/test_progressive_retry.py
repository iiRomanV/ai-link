"""PROGRESSIVE-RETRY (2026-10-02): таймауты по попыткам."""
from pathlib import Path


def test_browser_manager_uses_timouts_array():
    """_do_query использует timeouts.TIMEOUTS."""
    src = Path("core/agents/browser_manager.py").read_text(encoding="utf-8")
    assert "timeouts.TIMEOUTS" in src
    assert "PROGRESSIVE-RETRY" in src


def test_no_hardcoded_360():
    """Хардкод max(timeout, 360) убран."""
    src = Path("core/agents/browser_manager.py").read_text(encoding="utf-8")
    assert "max(timeout, 360)" not in src


def test_submit_uses_sum_of_timouts():
    """submit() ждёт сумму TIMEOUTS + буфер."""
    src = Path("core/agents/browser_manager.py").read_text(encoding="utf-8")
    assert "sum(timeouts.TIMEOUTS)" in src
    assert "_total_timeout" in src


def test_max_attempts_from_array():
    """MAX_ATTEMPTS = len(TIMEOUTS)."""
    src = Path("core/agents/browser_manager.py").read_text(encoding="utf-8")
    assert "MAX_ATTEMPTS = len(_timeouts)" in src


def test_timeouts_progressive_increasing():
    """Массив TIMEOUTS возрастающий — прогрессивный retry."""
    from config import timeouts
    arr = timeouts.TIMEOUTS
    for i in range(len(arr) - 1):
        assert arr[i] <= arr[i + 1], (
            f"TIMEOUTS не прогрессивный: {arr[i]} > {arr[i+1]}"
        )


def test_timeouts_values_match_plan():
    """Массив = [60, 90, 180, 600, 1800]."""
    from config import timeouts
    assert timeouts.TIMEOUTS == [60, 90, 180, 600, 1800]
