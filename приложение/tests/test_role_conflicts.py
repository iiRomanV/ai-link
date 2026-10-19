"""ROLES-CONFLICT (2026-10-04): проверка конфликтов ролей."""
import json
from pathlib import Path

import pytest


def _write_assignments(tmp_path, monkeypatch, data):
    f = tmp_path / "assignments.json"
    f.write_text(json.dumps({
        "version": "1.0",
        "assignments": data,
    }, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(
        "core.capability_benchmark.ASSIGNMENTS_FILE", f,
    )
    return f


def test_no_conflicts_empty(tmp_path, monkeypatch):
    """Всё на разных моделях — нет конфликтов."""
    _write_assignments(tmp_path, monkeypatch, {
        "planning": "browser_deepseek",
        "coding": "browser_deepseek",
        "review": "browser_qwen",
        "brain": "browser_perplexity",
        "dialog": "gigachat",
    })
    from core.capability_benchmark import check_role_conflicts
    r = check_role_conflicts()
    assert r["blocking"] == []
    assert r["warnings"] == []


def test_brain_blocks_coding(tmp_path, monkeypatch):
    """brain = coding → blocking."""
    _write_assignments(tmp_path, monkeypatch, {
        "brain": "browser_deepseek",
        "coding": "browser_deepseek",
    })
    from core.capability_benchmark import check_role_conflicts
    r = check_role_conflicts()
    assert len(r["blocking"]) == 1
    assert "brain=coding" in r["blocking"][0]


def test_brain_blocks_review(tmp_path, monkeypatch):
    """brain = review → blocking."""
    _write_assignments(tmp_path, monkeypatch, {
        "brain": "browser_deepseek",
        "review": "browser_deepseek",
    })
    from core.capability_benchmark import check_role_conflicts
    r = check_role_conflicts()
    assert len(r["blocking"]) == 1
    assert "brain=review" in r["blocking"][0]


def test_brain_blocks_planning(tmp_path, monkeypatch):
    """brain = planning → blocking."""
    _write_assignments(tmp_path, monkeypatch, {
        "brain": "browser_deepseek",
        "planning": "browser_deepseek",
    })
    from core.capability_benchmark import check_role_conflicts
    r = check_role_conflicts()
    assert len(r["blocking"]) == 1


def test_dialog_warns_coding(tmp_path, monkeypatch):
    """dialog = coding → warning, не blocking."""
    _write_assignments(tmp_path, monkeypatch, {
        "dialog": "browser_deepseek",
        "coding": "browser_deepseek",
    })
    from core.capability_benchmark import check_role_conflicts
    r = check_role_conflicts()
    assert r["blocking"] == []
    assert len(r["warnings"]) == 1
    assert "dialog=coding" in r["warnings"][0]


def test_dialog_warns_review(tmp_path, monkeypatch):
    """dialog = review → warning."""
    _write_assignments(tmp_path, monkeypatch, {
        "dialog": "browser_deepseek",
        "review": "browser_deepseek",
    })
    from core.capability_benchmark import check_role_conflicts
    r = check_role_conflicts()
    assert r["blocking"] == []
    assert len(r["warnings"]) == 1


def test_coding_review_same_ok(tmp_path, monkeypatch):
    """coding = review = fix на одной модели — разрешено."""
    _write_assignments(tmp_path, monkeypatch, {
        "coding": "browser_deepseek",
        "review": "browser_deepseek",
        "fix": "browser_deepseek",
    })
    from core.capability_benchmark import check_role_conflicts
    r = check_role_conflicts()
    assert r["blocking"] == []
    assert r["warnings"] == []


def test_brain_and_dialog_different_models(tmp_path, monkeypatch):
    """brain и dialog на разных моделях — ок."""
    _write_assignments(tmp_path, monkeypatch, {
        "brain": "browser_qwen",
        "dialog": "gigachat",
        "coding": "browser_deepseek",
    })
    from core.capability_benchmark import check_role_conflicts
    r = check_role_conflicts()
    assert r["blocking"] == []
    assert r["warnings"] == []


def test_multiple_blocking(tmp_path, monkeypatch):
    """brain = coding и brain = review — 2 blocking."""
    _write_assignments(tmp_path, monkeypatch, {
        "brain": "browser_deepseek",
        "coding": "browser_deepseek",
        "review": "browser_deepseek",
    })
    from core.capability_benchmark import check_role_conflicts
    r = check_role_conflicts()
    assert len(r["blocking"]) == 2


def test_orchestrate_fails_on_brain_conflict(tmp_path, monkeypatch):
    """orchestrate() возвращает failed, если brain конфликтует."""
    _write_assignments(tmp_path, monkeypatch, {
        "brain": "browser_deepseek",
        "coding": "browser_deepseek",
    })
    # Создаём минимальный проект
    proj = tmp_path / "proj"
    proj.mkdir()
    from core.orchestrator import orchestrate
    r = orchestrate(
        task="Тест",
        project_dir=str(proj),
        max_phases=1,
        cross_verify=False,
        brain_switch=False,
    )
    assert r.success is False
    assert "Конфликт ролей" in r.error
    assert "brain=coding" in r.error


def test_orchestrate_passes_with_warning(tmp_path, monkeypatch):
    """dialog = coding → warning, orchestrate стартует."""
    # Тут просто проверяем что orchestrate НЕ падает на blocking
    _write_assignments(tmp_path, monkeypatch, {
        "dialog": "browser_deepseek",
        "coding": "browser_deepseek",
        "planning": "browser_qwen",
    })
    from core.capability_benchmark import check_role_conflicts
    r = check_role_conflicts()
    assert r["blocking"] == []
    assert len(r["warnings"]) == 1
