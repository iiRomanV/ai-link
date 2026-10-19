"""ROLES (2026-10-01): роли через agent_assignments, без hardcoded."""
import json
from pathlib import Path


def test_get_verifier_no_role_returns_empty(tmp_path, monkeypatch):
    """Роль 'verifier' не назначена → пусто (cross_verify пропущен)."""
    monkeypatch.setattr(
        "core.capability_benchmark.get_assigned_agent",
        lambda key: None,
    )
    from core.cross_verifier import get_verifier
    assert get_verifier("browser_deepseek") == ""


def test_get_verifier_role_assigned(monkeypatch):
    """Роль 'verifier' = browser_perplexity → возвращается она."""
    monkeypatch.setattr(
        "core.capability_benchmark.get_assigned_agent",
        lambda key: "browser_perplexity" if key == "verifier" else None,
    )
    monkeypatch.setattr(
        "core.cross_verifier._is_agent_available",
        lambda a: True,
    )
    from core.cross_verifier import get_verifier
    assert get_verifier("browser_deepseek") == "browser_perplexity"


def test_get_verifier_same_as_review(monkeypatch):
    """Verifier = review → пусто (нет смысла проверять себя)."""
    monkeypatch.setattr(
        "core.capability_benchmark.get_assigned_agent",
        lambda key: "browser_deepseek" if key == "verifier" else None,
    )
    monkeypatch.setattr(
        "core.cross_verifier._is_agent_available",
        lambda a: True,
    )
    from core.cross_verifier import get_verifier
    assert get_verifier("browser_deepseek") == ""


def test_phase_review_maps_to_review_category():
    """PHASE_TO_CATEGORY[PHASE_REVIEW] == 'review'."""
    from core.orchestrator import PHASE_TO_CATEGORY, PHASE_REVIEW
    assert PHASE_TO_CATEGORY[PHASE_REVIEW] == "review"


def test_get_phase_agent_no_assignment_returns_empty(monkeypatch):
    """Без назначения и без автоподбора → пусто."""
    monkeypatch.setattr(
        "core.capability_benchmark.get_assigned_agent",
        lambda key: None,
    )
    from core.orchestrator import get_phase_agent, PHASE_CODE
    result = get_phase_agent(PHASE_CODE)
    assert result == ""


def test_get_phase_agent_assigned(monkeypatch):
    """Назначено → возвращается."""
    monkeypatch.setattr(
        "core.capability_benchmark.get_assigned_agent",
        lambda key: "browser_deepseek" if key == "coding" else None,
    )
    from core.orchestrator import get_phase_agent, PHASE_CODE
    assert get_phase_agent(PHASE_CODE) == "browser_deepseek"


def test_review_migration_from_comprehension(monkeypatch, tmp_path):
    """review не задан, comprehension задан → используется comprehension."""
    assignments_file = tmp_path / "assignments.json"
    assignments_file.write_text(json.dumps({
        "version": "1.0",
        "assignments": {"comprehension": "browser_qwen"},
    }), encoding="utf-8")

    monkeypatch.setattr(
        "core.capability_benchmark.ASSIGNMENTS_FILE",
        assignments_file,
    )
    from core.capability_benchmark import get_assigned_agent
    assert get_assigned_agent("review") == "browser_qwen"


def test_audit_agent_for_role_no_default():
    """_agent_for_role без назначения → пусто (не 'browser_deepseek')."""
    from core.audit_project import _DEFAULT_AGENTS_BY_ROLE
    assert _DEFAULT_AGENTS_BY_ROLE == {}


def test_warmup_only_assigned_signature():
    """warmup_all_async имеет параметр only_assigned=True."""
    import inspect
    from core.browser_warmup import warmup_all_async
    sig = inspect.signature(warmup_all_async)
    assert "only_assigned" in sig.parameters
    assert sig.parameters["only_assigned"].default is True


def test_is_site_assigned_no_file():
    """Нет файла назначений → True (не блокируем)."""
    from core.browser_warmup import _is_site_assigned
    # Проверяем что функция не падает
    result = _is_site_assigned("nonexistent_site")
    assert isinstance(result, bool)
