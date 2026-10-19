"""Тесты MEMORY-TURBO (заглушка + RAM-проверка)."""

from unittest.mock import patch, mock_open
from core import memory_turbo as mt


def test_get_ram_gb_real():
    """На реальной системе возвращает разумное число."""
    ram = mt.get_ram_gb()
    assert ram > 0
    assert ram < 1024  # не 1000 ТБ


def test_get_available_ram_gb_real():
    avail = mt.get_available_ram_gb()
    assert avail >= 0


def test_is_turbo_supported_true():
    """Если RAM >= порога → True."""
    with patch("core.memory_turbo.get_ram_gb", return_value=32.0):
        with patch("core.memory_turbo._get_threshold_gb", return_value=16.0):
            assert mt.is_turbo_supported() is True


def test_is_turbo_supported_false():
    """Если RAM < порога → False."""
    with patch("core.memory_turbo.get_ram_gb", return_value=8.0):
        with patch("core.memory_turbo._get_threshold_gb", return_value=16.0):
            assert mt.is_turbo_supported() is False


def test_can_enable_turbo_ok():
    with patch("core.memory_turbo.get_ram_gb", return_value=32.0):
        with patch("core.memory_turbo._get_threshold_gb", return_value=16.0):
            ok, reason = mt.can_enable_turbo()
            assert ok is True
            assert reason == ""


def test_can_enable_turbo_low_ram():
    with patch("core.memory_turbo.get_ram_gb", return_value=8.0):
        with patch("core.memory_turbo._get_threshold_gb", return_value=16.0):
            ok, reason = mt.can_enable_turbo()
            assert ok is False
            assert "8.0" in reason
            assert "16" in reason


def test_is_turbo_enabled_no_config():
    """turbo_memory=False → enabled=False."""
    with patch("core.memory_turbo.is_turbo_supported", return_value=True):
        with patch("config.config_manager.load_config",
                   return_value={"turbo_memory": False}):
            assert mt.is_turbo_enabled() is False


def test_is_turbo_enabled_no_support():
    """turbo_memory=True, но RAM мало → enabled=False."""
    with patch("core.memory_turbo.is_turbo_supported", return_value=False):
        with patch("config.config_manager.load_config",
                   return_value={"turbo_memory": True}):
            assert mt.is_turbo_enabled() is False


def test_turbo_info_real():
    """turbo_info возвращает все ключи."""
    info = mt.turbo_info()
    assert "ram_total_gb" in info
    assert "ram_available_gb" in info
    assert "threshold_gb" in info
    assert "supported" in info
    assert "enabled" in info
    assert "can_enable" in info
    assert "reason" in info
    assert info["ram_total_gb"] > 0


def test_sdr_search_disabled():
    """SDR отключён → пустой список."""
    with patch("core.memory_turbo.is_turbo_enabled", return_value=False):
        assert mt.sdr_search("test") == []


def test_sdr_search_enabled_stub():
    """SDR включён, но пока заглушка → пустой список."""
    with patch("core.memory_turbo.is_turbo_enabled", return_value=True):
        assert mt.sdr_search("test") == []
