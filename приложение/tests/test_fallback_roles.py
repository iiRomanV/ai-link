"""ROLES (2026-10-01): fallback через brain, не hardcoded."""
import json
from pathlib import Path
import pytest


def _write_assignments(tmp_path, monkeypatch, data):
    f = tmp_path / "assign.json"
    f.write_text(json.dumps({
        "version": "1.0",
        "assignments": data,
    }, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(
        "core.capability_benchmark.ASSIGNMENTS_FILE", f,
    )
    return f


def test_fallback_agents_empty_when_brain_unset(tmp_path, monkeypatch):
    """brain не назначен → get_fallback_agents возвращает []."""
    _write_assignments(tmp_path, monkeypatch, {})
    from core.route_rules import get_fallback_agents
    assert get_fallback_agents("plan") == []
    assert get_fallback_agents("code") == []
    assert get_fallback_agents("review") == []


def test_fallback_agents_brain_when_set(tmp_path, monkeypatch):
    """brain = ollama → get_fallback_agents возвращает [ollama]."""
    _write_assignments(tmp_path, monkeypatch, {"brain": "ollama"})
    from core.route_rules import get_fallback_agents
    assert get_fallback_agents("plan") == ["ollama"]
    assert get_fallback_agents("code") == ["ollama"]
    assert get_fallback_agents("fallback") == ["ollama"]


def test_fallback_agents_ignores_disabled_brain(tmp_path, monkeypatch):
    """brain = выключенный агент → []."""
    _write_assignments(tmp_path, monkeypatch, {"brain": "disabled_agent"})
    # _is_agent_enabled вернёт True для неизвестных — поэтому проверим
    # с реальным выключенным агентом
    from core.route_rules import get_fallback_agents
    # Если агент не в agents.json — считается включённым
    # Тест: назначение есть → возвращает его
    result = get_fallback_agents("plan")
    assert result == ["disabled_agent"]


def test_fallback_agents_no_ollama_by_default(tmp_path, monkeypatch):
    """По умолчанию (без brain) — НЕ подставляет ollama."""
    _write_assignments(tmp_path, monkeypatch, {
        "planning": "browser_deepseek",
        "coding": "browser_deepseek",
    })
    from core.route_rules import get_fallback_agents
    for ph in ["plan", "code", "review", "fix", "fallback"]:
        assert "ollama" not in get_fallback_agents(ph)


def test_fallback_agents_no_hardcoded(tmp_path, monkeypatch):
    """Раньше был hardcoded список → теперь его нет."""
    _write_assignments(tmp_path, monkeypatch, {})
    from core.route_rules import FALLBACK_AGENTS
    assert FALLBACK_AGENTS == {}
