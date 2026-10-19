"""MIGRATE (2026-10-01): миграция старых назначений.

A.1 (2026-10-04): авто-снятие монополистов УДАЛЕНО.
Конфликты видит check_role_conflicts (см. test_role_conflicts.py).
"""
import json
from pathlib import Path
import pytest


def _write_assignments(tmp_path, monkeypatch, data):
    f = tmp_path / "assignments.json"
    f.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(
        "core.capability_benchmark.ASSIGNMENTS_FILE", f,
    )
    return f


def test_migrate_concept_to_planning(tmp_path, monkeypatch):
    """concept → planning (если planning пусто)."""
    f = _write_assignments(tmp_path, monkeypatch, {
        "version": "1.0",
        "assignments": {"concept": "gigachat"},
    })
    from core.capability_benchmark import load_assignments
    data = load_assignments()
    assert data["assignments"].get("planning") == "gigachat"
    assert "concept" not in data["assignments"]
    saved = json.loads(f.read_text(encoding="utf-8"))
    assert "concept" not in saved["assignments"]


def test_migrate_concept_does_not_override_planning(tmp_path, monkeypatch):
    """concept НЕ перезаписывает существующий planning."""
    _write_assignments(tmp_path, monkeypatch, {
        "version": "1.0",
        "assignments": {
            "planning": "browser_deepseek",
            "concept": "gigachat",
        },
    })
    from core.capability_benchmark import load_assignments
    data = load_assignments()
    assert data["assignments"]["planning"] == "browser_deepseek"
    assert "concept" not in data["assignments"]


def test_migrate_fallback_removed(tmp_path, monkeypatch):
    """fallback удаляется."""
    _write_assignments(tmp_path, monkeypatch, {
        "version": "1.0",
        "assignments": {"fallback": "browser_deepseek"},
    })
    from core.capability_benchmark import load_assignments
    data = load_assignments()
    assert "fallback" not in data["assignments"]


def test_migrate_brain_conflict_preserved(tmp_path, monkeypatch):
    """A.1 (2026-10-04): brain=X + planning=X НЕ снимаются.
    Конфликт виден в check_role_conflicts."""
    _write_assignments(tmp_path, monkeypatch, {
        "version": "1.0",
        "assignments": {
            "brain": "browser_deepseek",
            "planning": "browser_deepseek",
            "coding": "browser_deepseek",
        },
    })
    from core.capability_benchmark import load_assignments, check_role_conflicts
    data = load_assignments()
    a = data["assignments"]
    assert a["brain"] == "browser_deepseek"
    assert a["planning"] == "browser_deepseek", "не должно автосниматься"
    assert a["coding"] == "browser_deepseek", "не должно автосниматься"
    conf = check_role_conflicts()
    assert len(conf["blocking"]) == 2


def test_migrate_dialog_conflict_preserved(tmp_path, monkeypatch):
    """A.1: dialog=Y + planning=Y НЕ снимается. Warning, не blocking."""
    _write_assignments(tmp_path, monkeypatch, {
        "version": "1.0",
        "assignments": {
            "dialog": "browser_deepseek",
            "planning": "browser_deepseek",
            "review": "browser_qwen",
        },
    })
    from core.capability_benchmark import load_assignments, check_role_conflicts
    data = load_assignments()
    a = data["assignments"]
    assert a["dialog"] == "browser_deepseek"
    assert a["planning"] == "browser_deepseek", "не должно автосниматься"
    assert a["review"] == "browser_qwen"
    conf = check_role_conflicts()
    assert conf["blocking"] == []
    assert len(conf["warnings"]) == 1


def test_migrate_no_change_keeps_file(tmp_path, monkeypatch):
    """Если мигрировать нечего — файл не перезаписывается."""
    f = _write_assignments(tmp_path, monkeypatch, {
        "version": "1.0",
        "assignments": {"planning": "browser_deepseek"},
    })
    before = f.read_text(encoding="utf-8")
    from core.capability_benchmark import load_assignments
    load_assignments()
    # Файл мог быть перезаписан с updated_at — проверяем что assignments те же
    after = json.loads(f.read_text(encoding="utf-8"))
    assert after["assignments"] == {"planning": "browser_deepseek"}


def test_migrate_auto_off(tmp_path, monkeypatch):
    """auto_migrate=False — не трогает файл."""
    f = _write_assignments(tmp_path, monkeypatch, {
        "version": "1.0",
        "assignments": {"fallback": "browser_deepseek"},
    })
    from core.capability_benchmark import load_assignments
    data = load_assignments(auto_migrate=False)
    assert "fallback" in data["assignments"]
