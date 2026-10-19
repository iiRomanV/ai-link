"""ROLES (2026-10-04): комбинации ролей после A.1.

A.1: авто-снятие монополистов из _migrate_assignments УДАЛЕНО.
Теперь:
  • load_assignments() НЕ трогает конфликты монополистов.
  • check_role_conflicts() находит конфликты (blocking/warnings).
  • orchestrate() возвращает failed при blocking.
"""
import json
from pathlib import Path
import pytest


def _write(tmp_path, monkeypatch, assignments):
    f = tmp_path / "assignments.json"
    f.write_text(json.dumps({
        "version": "1.0",
        "assignments": assignments,
    }, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(
        "core.capability_benchmark.ASSIGNMENTS_FILE", f,
    )
    return f


NON_MONOPOLISTS = ["planning", "coding", "review", "verifier", "comprehension"]


# ── Тест 1: все не-монополисты на одной ИИ ──

def test_all_non_monopolists_share_one_ai(tmp_path, monkeypatch):
    """5 ролей на одной модели — разрешено, конфликтов нет."""
    _write(tmp_path, monkeypatch, {k: "browser_deepseek" for k in NON_MONOPOLISTS})
    from core.capability_benchmark import load_assignments, check_role_conflicts
    data = load_assignments()
    a = data["assignments"]
    for k in NON_MONOPOLISTS:
        assert a.get(k) == "browser_deepseek", f"роль {k} не сохранилась"
    conf = check_role_conflicts()
    assert conf["blocking"] == []
    assert conf["warnings"] == []


# ── Тест 2: brain + любая другая роль — блокировка ──

@pytest.mark.parametrize("other_role", NON_MONOPOLISTS)
def test_brain_conflicts_with_any_role(tmp_path, monkeypatch, other_role):
    """A.1: brain + X на одной модели — НЕ снимается. Конфликт в blocking."""
    _write(tmp_path, monkeypatch, {
        "brain": "browser_deepseek",
        other_role: "browser_deepseek",
    })
    from core.capability_benchmark import load_assignments, check_role_conflicts
    data = load_assignments()
    a = data["assignments"]
    assert a["brain"] == "browser_deepseek"
    assert a[other_role] == "browser_deepseek", "не должно автосниматься"
    conf = check_role_conflicts()
    assert len(conf["blocking"]) == 1
    assert "brain" in conf["blocking"][0]
    assert other_role in conf["blocking"][0]


# ── Тест 3: dialog + любая другая роль — предупреждение ──

@pytest.mark.parametrize("other_role", NON_MONOPOLISTS)
def test_dialog_conflicts_with_any_role(tmp_path, monkeypatch, other_role):
    """A.1: dialog + X на одной модели — НЕ снимается. Warning (не blocking)."""
    _write(tmp_path, monkeypatch, {
        "dialog": "browser_deepseek",
        other_role: "browser_deepseek",
    })
    from core.capability_benchmark import load_assignments, check_role_conflicts
    data = load_assignments()
    a = data["assignments"]
    assert a["dialog"] == "browser_deepseek"
    assert a[other_role] == "browser_deepseek", "не должно автосниматься"
    conf = check_role_conflicts()
    assert conf["blocking"] == []
    assert len(conf["warnings"]) == 1
    assert "dialog" in conf["warnings"][0]
    assert other_role in conf["warnings"][0]


# ── Тест 4: brain + dialog на разных моделях — ок ──

def test_brain_and_dialog_different_models(tmp_path, monkeypatch):
    """brain=X, dialog=Y — можно, если X ≠ Y."""
    _write(tmp_path, monkeypatch, {
        "brain": "browser_deepseek",
        "dialog": "gigachat",
    })
    from core.capability_benchmark import load_assignments, check_role_conflicts
    data = load_assignments()
    a = data["assignments"]
    assert a["brain"] == "browser_deepseek"
    assert a["dialog"] == "gigachat"
    conf = check_role_conflicts()
    assert conf["blocking"] == []
    assert conf["warnings"] == []


# ── Тест 5: brain + dialog на одной модели — оба остаются ──

def test_brain_and_dialog_same_model(tmp_path, monkeypatch):
    """A.1: brain=X, dialog=X → НЕ снимается, оба остаются."""
    _write(tmp_path, monkeypatch, {
        "brain": "browser_deepseek",
        "dialog": "browser_deepseek",
    })
    from core.capability_benchmark import load_assignments
    data = load_assignments()
    a = data["assignments"]
    assert a["brain"] == "browser_deepseek"
    assert a["dialog"] == "browser_deepseek", "не должно автосниматься"


# ── Тест 6: монополист + все не-монополисты — все остаются ──

def test_brain_removes_all_others(tmp_path, monkeypatch):
    """A.1: brain=X + остальные=X → НИЧЕГО не снимается.
    Конфликт виден в check_role_conflicts (5 blocking)."""
    assignments = {"brain": "browser_deepseek"}
    for k in NON_MONOPOLISTS:
        assignments[k] = "browser_deepseek"
    _write(tmp_path, monkeypatch, assignments)
    from core.capability_benchmark import load_assignments, check_role_conflicts
    data = load_assignments()
    a = data["assignments"]
    for k in NON_MONOPOLISTS:
        assert a[k] == "browser_deepseek", f"{k} не должно автосниматься"
    conf = check_role_conflicts()
    assert len(conf["blocking"]) == 5


# ── Тест 7: разные модели — нет конфликтов ──

def test_different_models_no_conflicts(tmp_path, monkeypatch):
    """Каждой роли — своя модель, конфликтов нет."""
    _write(tmp_path, monkeypatch, {
        "planning": "browser_deepseek",
        "coding": "browser_deepseek",
        "review": "browser_qwen",
        "verifier": "browser_perplexity",
        "comprehension": "ollama",
        "dialog": "gigachat",
        "brain": "ollama_brain",
    })
    from core.capability_benchmark import load_assignments, check_role_conflicts
    data = load_assignments()
    a = data["assignments"]
    for k, v in a.items():
        assert v != "", f"{k} пустой"
    conf = check_role_conflicts()
    assert conf["blocking"] == []
    assert conf["warnings"] == []


# ── Тест 8: одна модель на всё + brain — ничего не снимается ──

def test_one_ai_for_everything_with_brain(tmp_path, monkeypatch):
    """A.1: одна модель на всё → ничего не автоснимается."""
    _write(tmp_path, monkeypatch, {
        "brain": "browser_deepseek",
        "dialog": "browser_deepseek",
        "planning": "browser_deepseek",
        "coding": "browser_deepseek",
        "review": "browser_deepseek",
        "verifier": "browser_deepseek",
        "comprehension": "browser_deepseek",
    })
    from core.capability_benchmark import load_assignments
    data = load_assignments()
    a = data["assignments"]
    assert a["brain"] == "browser_deepseek"
    assert a["dialog"] == "browser_deepseek"
    assert a["planning"] == "browser_deepseek"
    assert a["comprehension"] == "browser_deepseek"


# ── Тест 9: миграция не трогает корректные ──

def test_migration_keeps_valid(tmp_path, monkeypatch):
    """Корректные назначения не портятся миграцией."""
    _write(tmp_path, monkeypatch, {
        "planning": "browser_deepseek",
        "coding": "browser_deepseek",
        "review": "browser_qwen",
    })
    from core.capability_benchmark import load_assignments
    data = load_assignments()
    a = data["assignments"]
    assert len(a) == 3
    assert a["planning"] == "browser_deepseek"
    assert a["review"] == "browser_qwen"


# ── Тест 10: gigachat на всех ролях (кроме монополистов) ──

def test_gigachat_all_non_monopolists(tmp_path, monkeypatch):
    """GigaChat на 5 ролях — ок, работает."""
    _write(tmp_path, monkeypatch, {k: "gigachat" for k in NON_MONOPOLISTS})
    from core.capability_benchmark import load_assignments
    data = load_assignments()
    a = data["assignments"]
    for k in NON_MONOPOLISTS:
        assert a.get(k) == "gigachat"
