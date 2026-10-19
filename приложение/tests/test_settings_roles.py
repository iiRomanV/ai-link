"""ROLES (2026-10-01): проверка ASSIGNMENT_KEYS, PHASE_INFO, монополистов."""
import json
from pathlib import Path
import pytest


def test_assignment_keys_7_roles():
    """7 ролей: planning, coding, review, verifier, dialog, comprehension, brain."""
    from core.capability_benchmark import ASSIGNMENT_KEYS
    expected = {
        "planning", "coding", "review", "verifier",
        "dialog", "comprehension", "brain",
    }
    assert set(ASSIGNMENT_KEYS) == expected
    assert "concept" not in ASSIGNMENT_KEYS
    assert "fallback" not in ASSIGNMENT_KEYS


def test_monopolist_roles():
    """brain и dialog — монополисты."""
    from core.capability_benchmark import MONOPOLIST_ROLES
    assert MONOPOLIST_ROLES == {"brain", "dialog"}


def test_phase_info_has_new_roles():
    """PHASE_INFO содержит review и verifier."""
    from core.capability_benchmark import PHASE_INFO
    assert "review" in PHASE_INFO
    assert "verifier" in PHASE_INFO
    assert "title" in PHASE_INFO["review"]
    assert "desc" in PHASE_INFO["review"]


def test_phase_tooltip_returns_string():
    """phase_tooltip возвращает непустую строку для роли."""
    from core.capability_benchmark import phase_tooltip
    tip = phase_tooltip("coding")
    assert isinstance(tip, str)
    assert len(tip) > 0


def test_set_assignment_new_roles(tmp_path, monkeypatch):
    """review и verifier сохраняются."""
    assignments_file = tmp_path / "assignments.json"
    monkeypatch.setattr(
        "core.capability_benchmark.ASSIGNMENTS_FILE",
        assignments_file,
    )
    from core.capability_benchmark import set_assignment
    assert set_assignment("review", "browser_deepseek") is True
    assert set_assignment("verifier", "browser_qwen") is True
    data = json.loads(assignments_file.read_text(encoding="utf-8"))
    assert data["assignments"]["review"] == "browser_deepseek"
    assert data["assignments"]["verifier"] == "browser_qwen"


def test_set_assignment_unknown_key(tmp_path, monkeypatch):
    """Неизвестный ключ не сохраняется."""
    assignments_file = tmp_path / "assignments.json"
    monkeypatch.setattr(
        "core.capability_benchmark.ASSIGNMENTS_FILE",
        assignments_file,
    )
    from core.capability_benchmark import set_assignment
    assert set_assignment("unknown_xyz", "browser_deepseek") is False


def test_list_available_agents_returns_list():
    """list_available_agents возвращает список словарей."""
    from core.capability_benchmark import list_available_agents
    agents = list_available_agents()
    assert isinstance(agents, list)
    for a in agents:
        assert "id" in a
        assert "name" in a
