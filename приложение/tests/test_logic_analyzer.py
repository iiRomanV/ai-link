"""Тесты для core.logic_analyzer."""

from pathlib import Path
from unittest.mock import patch
from core.logic_analyzer import (
    analyze_file, analyze_project, _parse_ai_response,
)


def _write(path: Path, content: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_parse_ai_response_valid():
    text = '[{"severity": "high", "line": 5, "message": "bad"}]'
    r = _parse_ai_response(text)
    assert len(r) == 1
    assert r[0]["severity"] == "high"


def test_parse_ai_response_with_thinking():
    text = (
        "Завершено размышление\n"
        "Вот найденные проблемы:\n"
        '[{"severity": "low", "line": 1, "message": "x"}]\n'
    )
    r = _parse_ai_response(text)
    assert len(r) == 1


def test_parse_ai_response_empty():
    assert _parse_ai_response("") == []
    assert _parse_ai_response("не JSON") == []
    assert _parse_ai_response("[]") == []


def test_analyze_file_missing(tmp_path):
    r = analyze_file(str(tmp_path), "nope.py")
    assert not r["success"]
    assert "не найден" in r["error"]


def test_analyze_file_unknown_agent(tmp_path):
    _write(tmp_path / "a.py", "x = 1\n")
    r = analyze_file(str(tmp_path), "a.py", agent_mode="nonexistent")
    assert not r["success"]
    assert "Неизвестный" in r["error"] or "неизвестный" in r["error"]


def test_analyze_file_mock_success(tmp_path):
    _write(tmp_path / "a.py", "def foo():\n    return 1\n")
    ai_text = '[{"severity": "medium", "line": 1, "message": "foo не используется"}]'
    with patch("core.agents.browser_agent.query_browser_ai") as m:
        m.return_value = {"success": True, "text": ai_text, "error": None}
        r = analyze_file(str(tmp_path), "a.py")
        assert r["success"]
        assert len(r["issues"]) == 1
        assert r["issues"][0]["category"] == "logic"
        assert r["issues"][0]["file"] == "a.py"


def test_analyze_file_mock_failure(tmp_path):
    _write(tmp_path / "a.py", "x = 1\n")
    with patch("core.agents.browser_agent.query_browser_ai") as m:
        m.return_value = {"success": False, "text": "", "error": "timeout"}
        r = analyze_file(str(tmp_path), "a.py")
        assert not r["success"]
        assert r["error"] == "timeout"


def test_analyze_project_skips_tests(tmp_path):
    _write(tmp_path / "main.py", "x = 1\n")
    _write(tmp_path / "tests" / "test_main.py", "y = 2\n")
    ai_text = "[]"
    with patch("core.agents.browser_agent.query_browser_ai") as m:
        m.return_value = {"success": True, "text": ai_text, "error": None}
        r = analyze_project(str(tmp_path), max_files=10)
        assert "main.py" in r["analyzed_files"]
        assert not any("tests/" in f for f in r["analyzed_files"])


def test_analyze_project_max_files(tmp_path):
    for i in range(5):
        _write(tmp_path / f"f{i}.py", "x = 1\n")
    with patch("core.agents.browser_agent.query_browser_ai") as m:
        m.return_value = {"success": True, "text": "[]", "error": None}
        r = analyze_project(str(tmp_path), max_files=2)
        assert len(r["analyzed_files"]) == 2
        assert r["total_py_files"] == 5
