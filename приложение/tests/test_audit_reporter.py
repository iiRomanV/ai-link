"""Тесты для core.audit_reporter."""

from unittest.mock import patch
from core.audit_reporter import (
    format_issues_for_ai,
    humanize_audit,
    save_audit_result,
    load_audit_result,
)


def test_format_issues_empty():
    text = format_issues_for_ai([])
    assert "не найдено" in text


def test_format_issues_basic():
    issues = [
        {"severity": "high", "category": "syntax",
         "file": "a.py", "line": 5, "message": "bad"},
        {"severity": "low", "category": "todo",
         "file": "b.py", "line": 10, "message": "# TODO"},
    ]
    text = format_issues_for_ai(issues)
    assert "a.py" in text
    assert "b.py" in text
    assert "high" in text
    assert "syntax" in text


def test_humanize_audit_empty(tmp_path):
    r = humanize_audit(str(tmp_path), {"issues": []})
    assert r["success"]
    assert "чистый" in r["text"].lower() or "не найдено" in r["text"].lower()


def test_humanize_audit_unknown_agent(tmp_path):
    r = humanize_audit(str(tmp_path),
                       {"issues": [{"severity": "low", "file": "a.py"}]},
                       agent_mode="nonexistent")
    assert not r["success"]
    assert "Неизвестный" in r["error"]


def test_humanize_audit_mock(tmp_path):
    report = {"issues": [
        {"severity": "high", "category": "syntax",
         "file": "a.py", "line": 5, "message": "bad"},
    ], "total": 1}

    with patch("core.agents.browser_agent.query_browser_ai") as mock_q:
        mock_q.return_value = {
            "success": True,
            "text": "В файле a.py проблема на строке 5",
            "error": None,
        }
        r = humanize_audit(str(tmp_path), report, agent_mode="browser_deepseek")
        assert r["success"]
        assert "a.py" in r["text"]

        called_prompt = mock_q.call_args[0][1]
        assert "a.py" in called_prompt
        assert "syntax" in called_prompt
        assert "НЕ JSON" in called_prompt


def test_humanize_audit_mock_failure(tmp_path):
    report = {"issues": [
        {"severity": "low", "file": "a.py", "line": 1, "message": "x"},
    ]}
    with patch("core.agents.browser_agent.query_browser_ai") as mock_q:
        mock_q.return_value = {
            "success": False, "text": "", "error": "timeout",
        }
        r = humanize_audit(str(tmp_path), report)
        assert not r["success"]
        assert r["error"] == "timeout"


def test_save_and_load_audit_result(tmp_path):
    tech = {
        "total": 2,
        "by_severity": {"high": 1, "low": 1},
        "by_category": {"syntax": 1, "todo": 1},
        "issues": [{"severity": "high", "file": "a.py"}],
    }
    save_audit_result(str(tmp_path), "объяснение человека", tech)
    loaded = load_audit_result(str(tmp_path))
    assert loaded is not None
    assert loaded["total"] == 2
    assert loaded["human_report"] == "объяснение человека"
    assert loaded["by_severity"]["high"] == 1


def test_load_audit_result_empty(tmp_path):
    loaded = load_audit_result(str(tmp_path))
    assert loaded is None
