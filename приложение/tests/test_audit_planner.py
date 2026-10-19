"""Тесты для core.audit_planner (Этап 4.1, 2026-10-08)."""

import json
from pathlib import Path

import pytest

from core.audit_planner import (
    build_plan_from_audit,
    _parse_plan_text,
    _collect_audit_summary,
)


def _make_audit(project_dir: Path, audit_id: str,
                result: dict | None = None) -> Path:
    """Создаёт audits/<id>/audit.json."""
    d = project_dir / ".ai_link" / "audits" / audit_id
    d.mkdir(parents=True, exist_ok=True)
    if result is None:
        result = {
            "status": "success",
            "audit_id": audit_id,
            "technical": {"issues": [
                {"severity": "MAJOR", "file": "db.py",
                 "message": "SQL-инъекция в query_user"},
                {"severity": "MINOR", "file": "utils.py",
                 "message": "неиспользуемый import"},
            ], "total": 2},
            "security": {"total": 1, "findings": [
                {"file": "config.py", "line": 15},
            ]},
            "logic_analysis": {"success": True, "issues": [
                {"severity": "major", "file": "main.py",
                 "line": 42, "message": "не обрабатывается None"},
            ], "total": 1},
            "llm_description": {"success": True,
                                "description": "CLI-утилита для заметок."},
            "llm_ideas": {"success": True,
                          "ideas": "Добавить экспорт в Markdown."},
            "dependencies": {"files": ["requirements.txt"]},
        }
    (d / "audit.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return d


def _fake_llm(prompt: str) -> dict:
    """Заглушка LLM — всегда отвечает одним планом."""
    return {
        "success": True,
        "text": (
            "НАЗВАНИЕ: Доработка заметок\n"
            "ЦЕЛЬ: Исправить SQL-инъекцию и добавить экспорт.\n\n"
            "ЭТАП: Безопасность\n"
            "- Исправить SQL-инъекцию в db.py\n"
            "- Добавить валидацию входа\n\n"
            "ЭТАП: Функциональность\n"
            "- Реализовать экспорт в Markdown\n"
        ),
        "error": "",
    }


def test_parse_plan_text_basic():
    text = (
        "НАЗВАНИЕ: Test\n"
        "ЦЕЛЬ: Goal text\n\n"
        "ЭТАП: Этап 1\n"
        "- шаг 1\n"
        "- шаг 2\n\n"
        "ЭТАП: Этап 2\n"
        "- шаг 3\n"
    )
    plan = _parse_plan_text(text)
    assert plan["title"] == "Test"
    assert plan["goal"] == "Goal text"
    assert len(plan["stages"]) == 2
    assert plan["stages"][0]["name"] == "Этап 1"
    assert len(plan["stages"][0]["items"]) == 2
    assert plan["stages"][1]["items"][0]["text"] == "шаг 3"


def test_parse_plan_text_empty():
    plan = _parse_plan_text("")
    assert plan["title"] == "План доработки"
    assert plan["stages"] == []


def test_collect_summary_includes_technical(tmp_path):
    d = _make_audit(tmp_path, "20261008_000000")
    result = json.loads((d / "audit.json").read_text(encoding="utf-8"))
    summary = _collect_audit_summary(result)
    assert "ТЕХНИЧЕСКИЕ ПРОБЛЕМЫ" in summary
    assert "SQL-инъекция" in summary
    assert "СЕКРЕТЫ" in summary
    assert "ЛОГИЧЕСКИЕ НАХОДКИ" in summary


def test_build_plan_from_audit_success(tmp_path, monkeypatch):
    _make_audit(tmp_path, "20261008_000000")

    # Подменяем LLM в audit_project
    import core.audit_project as ap
    monkeypatch.setattr(
        ap, "_agent_for_role", lambda r: "browser_test"
    )
    monkeypatch.setattr(
        ap, "_llm_call",
        lambda mode, prompt, timeout=180: _fake_llm(prompt),
    )

    r = build_plan_from_audit(
        str(tmp_path), "20261008_000000",
        user_hint="исправь баги и добавь экспорт",
    )
    assert r["ok"] is True, r
    plan = r["plan"]
    assert plan["title"] == "Доработка заметок"
    assert len(plan["stages"]) == 2
    assert plan["status"] == "draft"

    # Файл сохранён
    out = tmp_path / ".ai_link" / "audits" / "20261008_000000" / "audit_plan.json"
    assert out.exists()
    saved = json.loads(out.read_text(encoding="utf-8"))
    assert saved["status"] == "draft"
    assert saved["audit_id"] == "20261008_000000"
    assert "исправь баги" in saved["user_hint"]


def test_build_plan_no_audit_json(tmp_path):
    r = build_plan_from_audit(str(tmp_path), "nonexistent")
    assert r["ok"] is False
    assert "audit.json" in r["error"]


def test_build_plan_no_agent(tmp_path, monkeypatch):
    _make_audit(tmp_path, "20261008_000000")

    import core.audit_project as ap
    monkeypatch.setattr(ap, "_agent_for_role", lambda r: "")
    monkeypatch.setattr(
        ap, "_llm_call",
        lambda *a, **kw: {"success": False, "text": "", "error": "no agent"},
    )

    r = build_plan_from_audit(str(tmp_path), "20261008_000000")
    assert r["ok"] is False
    assert "planning" in r["error"].lower() or "agent" in r["error"].lower()


def test_build_plan_llm_failure(tmp_path, monkeypatch):
    _make_audit(tmp_path, "20261008_000000")

    import core.audit_project as ap
    monkeypatch.setattr(ap, "_agent_for_role", lambda r: "browser_test")
    monkeypatch.setattr(
        ap, "_llm_call",
        lambda *a, **kw: {"success": False, "text": "", "error": "timeout"},
    )

    r = build_plan_from_audit(str(tmp_path), "20261008_000000")
    assert r["ok"] is False
    assert "timeout" in r["error"]


def test_build_plan_empty_user_hint(tmp_path, monkeypatch):
    _make_audit(tmp_path, "20261008_000000")

    import core.audit_project as ap
    monkeypatch.setattr(ap, "_agent_for_role", lambda r: "browser_test")
    monkeypatch.setattr(
        ap, "_llm_call",
        lambda mode, prompt, timeout=180: _fake_llm(prompt),
    )

    r = build_plan_from_audit(str(tmp_path), "20261008_000000", user_hint="")
    assert r["ok"] is True
    # Проверим, что в prompt попал placeholder.
    # (в файле user_hint пустой, raw содержит ответ)
    out = tmp_path / ".ai_link" / "audits" / "20261008_000000" / "audit_plan.json"
    saved = json.loads(out.read_text(encoding="utf-8"))
    assert saved["user_hint"] == ""
