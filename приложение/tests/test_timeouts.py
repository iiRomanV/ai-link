"""TIMEOUTS-IMPORT (2026-10-02): файл таймаутов существует и читается."""
def test_timeouts_module_exists():
    from config import timeouts
    assert timeouts is not None

def test_all_timeout_values_present():
    from config import timeouts
    for name in ("REASONING_START", "REASONING_MIN", "REASONING_STOP",
                 "REASONING_MAX", "JSON_WAIT", "FALLBACK"):
        assert isinstance(getattr(timeouts, name), int)
    assert isinstance(timeouts.TIMEOUTS, list)

def test_timeouts_values_reasonable():
    from config import timeouts
    assert 1 <= timeouts.REASONING_START <= 60
    assert 1 <= timeouts.REASONING_MIN <= 200
    assert 1 <= timeouts.REASONING_STOP <= 60
    assert 30 <= timeouts.REASONING_MAX <= 3600
    assert 10 <= timeouts.JSON_WAIT <= 600
    assert 10 <= timeouts.FALLBACK <= 600
    for t in timeouts.TIMEOUTS:
        assert t >= 10

def test_timeouts_ascending():
    from config import timeouts
    arr = timeouts.TIMEOUTS
    for i in range(len(arr) - 1):
        assert arr[i] <= arr[i + 1], f"TIMEOUTS не возрастает"

def test_browser_manager_imports_timeouts():
    from pathlib import Path
    src = Path("core/agents/browser_manager.py").read_text(encoding="utf-8")
    assert "from config import timeouts" in src
