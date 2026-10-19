"""KEEP-AWAKE-HEARTBEAT (2026-10-03): heartbeat xdg-screensaver reset."""
from pathlib import Path


def test_heartbeat_worker_exists():
    """Функция _heartbeat_worker определена."""
    src = Path("core/keep_awake.py").read_text(encoding="utf-8")
    assert "def _heartbeat_worker" in src
    assert "KEEP-AWAKE-HEARTBEAT" in src


def test_heartbeat_uses_xdg_screensaver():
    """Heartbeat вызывает xdg-screensaver reset."""
    src = Path("core/keep_awake.py").read_text(encoding="utf-8")
    assert "xdg-screensaver" in src
    assert '"reset"' in src


def test_heartbeat_30_sec_interval():
    """Интервал 30 секунд."""
    src = Path("core/keep_awake.py").read_text(encoding="utf-8")
    assert "timeout=30.0" in src


def test_start_keep_awake_launches_heartbeat():
    """start_keep_awake запускает heartbeat."""
    src = Path("core/keep_awake.py").read_text(encoding="utf-8")
    assert "_start_heartbeat()" in src


def test_stop_heartbeat_defined():
    """Функция _stop_heartbeat определена."""
    src = Path("core/keep_awake.py").read_text(encoding="utf-8")
    assert "def _stop_heartbeat" in src


def test_heartbeat_daemon_thread():
    """Thread запускается как daemon."""
    src = Path("core/keep_awake.py").read_text(encoding="utf-8")
    assert "daemon=True" in src
