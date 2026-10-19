"""Тесты для core.browser_warmup."""

import threading
import time
from unittest.mock import patch

from core import browser_warmup
from core.browser_warmup import (
    get_status, get_all_statuses, warmup_site, warmup_site_async,
    cancel_warmup, reset_all,
    ST_IDLE, ST_READY, ST_ERROR, ST_LOGIN, ST_LOADING, ST_DISABLED,
)


def setup_function(_func):
    reset_all()


def test_reset_clears_status():
    reset_all()
    assert get_status("qwen")["status"] == ST_IDLE


def test_warmup_success(tmp_path):
    with patch("core.agents.browser_agent.query_browser_ai") as m:
        m.return_value = {"success": True, "text": "OK", "error": None}
        r = warmup_site("qwen")
        assert r["status"] == ST_READY


def test_warmup_login_required():
    # Патчим _is_agent_enabled, чтобы тест не зависел от реального registry
    with patch("core.browser_warmup._is_agent_enabled", return_value=True), \
         patch("core.agents.browser_agent.query_browser_ai") as m:
        m.return_value = {
            "success": False, "reason": "login_required",
            "text": "", "error": "нужен вход",
        }
        r = warmup_site("perplexity")
        assert r["status"] == ST_LOGIN


def test_warmup_timeout():
    with patch("core.agents.browser_agent.query_browser_ai") as m:
        m.return_value = {
            "success": False, "reason": "response_timeout",
            "text": "", "error": "timeout",
        }
        r = warmup_site("qwen")
        assert r["status"] == ST_ERROR


def test_warmup_exception():
    with patch("core.agents.browser_agent.query_browser_ai") as m:
        m.side_effect = RuntimeError("boom")
        r = warmup_site("qwen")
        assert r["status"] == ST_ERROR


def test_warmup_cancel():
    def slow(*args, **kwargs):
        time.sleep(0.3)
        return {"success": True, "text": "OK", "error": None}

    with patch("core.agents.browser_agent.query_browser_ai", side_effect=slow):
        # Отменяем сразу же
        def cancel_thread():
            time.sleep(0.05)
            cancel_warmup("qwen")

        t = threading.Thread(target=cancel_thread)
        t.start()
        r = warmup_site("qwen")
        t.join()
        assert r["status"] == ST_IDLE


def test_callback_called():
    calls = []

    def cb(site_id, status):
        calls.append((site_id, status["status"]))

    with patch("core.agents.browser_agent.query_browser_ai") as m:
        m.return_value = {"success": True, "text": "OK", "error": None}
        warmup_site("qwen", callback=cb)
        # Должны быть как минимум loading -> ready
        statuses = [c[1] for c in calls]
        assert ST_LOADING in statuses
        assert ST_READY in statuses


def test_get_all_statuses_reads_config():
    # Мокаем load_sites — вернём два сайта, один выключен
    def fake_load():
        return {"sites": [
            {"id": "qwen", "enabled": True},
            {"id": "perplexity", "enabled": False},
        ]}

    with patch("core.agents.browser_sites_manager.load_sites", side_effect=fake_load):
        result = get_all_statuses()
        assert result["qwen"]["status"] == ST_IDLE
        assert result["perplexity"]["status"] == ST_DISABLED


def test_warmup_site_async():
    with patch("core.agents.browser_agent.query_browser_ai") as m:
        m.return_value = {"success": True, "text": "OK", "error": None}
        t = warmup_site_async("qwen")
        t.join(timeout=5)
        r = get_status("qwen")
        assert r["status"] == ST_READY
