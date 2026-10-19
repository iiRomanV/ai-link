"""Тесты для core.route_rules."""

from core.route_rules import (
    get_rule, get_fallback_agents, decide_action, extract_reason,
    DEFAULT_RULES, FALLBACK_AGENTS,
    ACTION_RETRY, ACTION_SWITCH, ACTION_SKIP, ACTION_STOP,
)


def test_rules_defined():
    assert "login_required" in DEFAULT_RULES
    assert "input_not_found" in DEFAULT_RULES
    assert "response_timeout" in DEFAULT_RULES
    assert "launch_failed" in DEFAULT_RULES


def test_get_rule_login_required():
    r = get_rule("login_required")
    assert r["action"] == ACTION_SKIP
    assert r["max_attempts"] == 1


def test_get_rule_input_not_found():
    r = get_rule("input_not_found")
    assert r["action"] == ACTION_RETRY
    assert r["max_attempts"] == 3


def test_get_rule_unknown():
    r = get_rule("совсем_не_такой_reason")
    assert r["action"] == ACTION_RETRY
    assert r["max_attempts"] == 1


def test_get_fallback_agents(monkeypatch, tmp_path):
    """ROLES (2026-10-01): get_fallback_agents через brain.

    Без brain → []. С brain=ollama → [ollama].
    """
    import json as _j
    af = tmp_path / "assign.json"
    af.write_text(_j.dumps({
        "version": "1.0", "assignments": {"brain": "ollama"},
    }), encoding="utf-8")
    monkeypatch.setattr(
        "core.capability_benchmark.ASSIGNMENTS_FILE", af,
    )
    plan_agents = get_fallback_agents("plan")
    assert plan_agents == ["ollama"]
    code_agents = get_fallback_agents("code")
    assert code_agents == ["ollama"]


def test_get_fallback_agents_unknown_phase(monkeypatch, tmp_path):
    """ROLES (2026-10-01): без brain → [] для любой фазы."""
    monkeypatch.setattr(
        "core.capability_benchmark.ASSIGNMENTS_FILE",
        tmp_path / "no_assign.json",
    )
    agents = get_fallback_agents("нет_такой_фазы")
    assert agents == []


def test_decide_retry_first_time():
    r = decide_action("code", "response_timeout", attempts_made=0)
    assert r["action"] == ACTION_RETRY


def test_decide_switch_after_retries():
    r = decide_action("code", "response_timeout", attempts_made=1)
    # max_attempts=1, попытка уже сделана — переключаемся
    assert r["action"] == ACTION_SWITCH


def test_decide_stop_after_switch():
    r = decide_action("code", "response_timeout", attempts_made=2)
    assert r["action"] == ACTION_STOP


def test_decide_login_required_skip():
    r = decide_action("plan", "login_required", attempts_made=0)
    assert r["action"] == ACTION_SKIP


def test_decide_launch_failed_switch():
    r = decide_action("code", "launch_failed", attempts_made=0)
    assert r["action"] == ACTION_SWITCH


def test_extract_reason():
    assert extract_reason("login_required: нужен вход") == "login_required"
    assert extract_reason("response_timeout after 30s") == "response_timeout"
    assert extract_reason("неизвестная ошибка") == ""


def test_extract_reason_empty():
    assert extract_reason("") == ""
    assert extract_reason(None) == ""
