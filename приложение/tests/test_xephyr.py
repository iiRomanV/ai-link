"""Б77 (16.10.2026): Xephyr — виртуальный дисплей для браузеров."""


def test_xephyr_module_imports():
    """Модуль импортируется, функции на месте."""
    from core import xephyr_manager as x
    assert hasattr(x, "ensure_running")
    assert hasattr(x, "stop")
    assert hasattr(x, "hide")
    assert hasattr(x, "show")
    assert hasattr(x, "is_alive")
    assert hasattr(x, "get_display")


def test_xephyr_is_alive_false_when_not_running():
    """Без запущенного Xephyr is_alive() = False."""
    from core import xephyr_manager as x
    # Если Xephyr уже был запущен тестами — пропускаем
    # (не должны мешать друг другу)
    if x.is_alive():
        assert x.get_display() == ":99"
    else:
        assert x.is_alive() is False
        assert x.get_display() == ""


def test_use_xephyr_in_defaults():
    """В дефолтах config_manager есть use_xephyr."""
    from config.config_manager import DEFAULT_CONFIG
    assert "use_xephyr" in DEFAULT_CONFIG
    assert isinstance(DEFAULT_CONFIG["use_xephyr"], bool)


def test_browser_manager_has_xephyr_block():
    """В browser_manager есть блок запуска через Xephyr."""
    from pathlib import Path
    src = Path("core/agents/browser_manager.py").read_text(encoding="utf-8")
    assert "use_xephyr" in src
    assert "xephyr_manager" in src
    assert "DISPLAY" in src


def test_main_has_xephyr_start():
    """В main.py есть ensure_running + atexit."""
    from pathlib import Path
    src = Path("main.py").read_text(encoding="utf-8")
    assert "xephyr_manager" in src
    assert "ensure_running" in src
    assert "atexit" in src
