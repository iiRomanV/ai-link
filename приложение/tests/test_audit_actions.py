"""Тесты для core.audit_actions."""

from pathlib import Path
from core.audit_actions import (
    prepare_actions, apply_action,
    ACTION_KEEP, ACTION_SKIP, ACTION_SHOW, ACTION_IMPACT, ACTION_FIX,
)


def _write(path: Path, content: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_prepare_actions_syntax_has_fix():
    issue = {"severity": "high", "category": "syntax",
             "file": "broken.py", "line": 1, "message": "bad"}
    actions = prepare_actions(issue)
    ids = [a["id"] for a in actions]
    assert ACTION_FIX in ids
    assert ACTION_IMPACT in ids
    assert ACTION_SHOW in ids
    assert ACTION_KEEP in ids
    assert ACTION_SKIP in ids


def test_prepare_actions_todo_no_fix():
    issue = {"severity": "low", "category": "todo",
             "file": "a.py", "line": 5, "message": "# TODO"}
    actions = prepare_actions(issue)
    ids = [a["id"] for a in actions]
    assert ACTION_FIX not in ids  # TODO не правится автоматом
    assert ACTION_IMPACT in ids


def test_prepare_actions_no_file():
    issue = {"severity": "low", "category": "todo",
             "file": "", "line": 0, "message": "x"}
    actions = prepare_actions(issue)
    ids = [a["id"] for a in actions]
    assert ACTION_SHOW not in ids
    assert ACTION_IMPACT not in ids
    assert ACTION_KEEP in ids


def test_action_show_ok(tmp_path):
    _write(tmp_path / "a.py", "line1\nline2\nline3\nline4\nline5\n")
    issue = {"file": "a.py", "line": 3, "message": "x"}
    r = apply_action(str(tmp_path), issue, ACTION_SHOW)
    assert r["success"]
    assert ">> 3:" in r["snippet"]
    assert r["total_lines"] == 5


def test_action_show_missing_file(tmp_path):
    issue = {"file": "nope.py", "line": 1, "message": "x"}
    r = apply_action(str(tmp_path), issue, ACTION_SHOW)
    assert not r["success"]
    assert "не найден" in r["error"]


def test_action_impact(tmp_path):
    _write(tmp_path / "core" / "__init__.py", "")
    _write(tmp_path / "core" / "storage.py", "def save(): pass\n")
    _write(tmp_path / "ui" / "__init__.py", "")
    _write(tmp_path / "ui" / "window.py",
           "from core.storage import save\n\ndef run(): save()\n")
    from core.project_indexer import update_project_index
    update_project_index(str(tmp_path))
    issue = {"file": "core/storage.py", "line": 1, "message": "x"}
    r = apply_action(str(tmp_path), issue, ACTION_IMPACT)
    assert r["success"]
    assert "ui/window.py" in r["dependents"]


def test_action_keep_writes_decision(tmp_path):
    from core.project_state import load_decisions
    _write(tmp_path / "a.py", "x = 1\n")
    issue = {"file": "a.py", "line": 1, "message": "проверено, ок"}
    r = apply_action(str(tmp_path), issue, ACTION_KEEP)
    assert r["success"]
    decisions = load_decisions(str(tmp_path))
    assert len(decisions) == 1
    assert "Оставлено" in decisions[0]["what"]


def test_action_skip_writes_known_issue(tmp_path):
    from core.project_state import load_state
    _write(tmp_path / "a.py", "x = 1\n")
    issue = {"file": "a.py", "line": 1, "message": "потом разберёмся"}
    r = apply_action(str(tmp_path), issue, ACTION_SKIP)
    assert r["success"]
    state = load_state(str(tmp_path))
    assert any("Отложено" in i for i in state["work"]["known_issues"])


def test_apply_unknown_action(tmp_path):
    issue = {"file": "a.py", "line": 1, "message": "x"}
    r = apply_action(str(tmp_path), issue, "нет_такого")
    assert not r["success"]
    assert "неизвестное" in r["error"]


def test_action_fix_wrong_category(tmp_path):
    issue = {"file": "a.py", "line": 1, "message": "x", "category": "todo"}
    r = apply_action(str(tmp_path), issue, "fix")
    assert not r["success"]
    assert "не поддерживается" in r["error"]


def test_action_fix_missing_file(tmp_path):
    issue = {"file": "nope.py", "line": 1, "message": "x",
             "category": "syntax"}
    r = apply_action(str(tmp_path), issue, "fix")
    assert not r["success"]
    assert "не найден" in r["error"]


def test_action_fix_ai_failure(tmp_path):
    from unittest.mock import patch
    _write(tmp_path / "a.py", "def broken(:\n")
    issue = {"file": "a.py", "line": 1, "message": "syntax",
             "category": "syntax"}
    with patch("core.agents.browser_agent.query_browser_ai") as m:
        m.return_value = {"success": False, "text": "", "error": "timeout"}
        r = apply_action(str(tmp_path), issue, "fix")
        assert not r["success"]
        assert "timeout" in r["error"]


def test_action_fix_ai_task_complete(tmp_path):
    from unittest.mock import patch
    _write(tmp_path / "a.py", "def broken(:\n")
    issue = {"file": "a.py", "line": 1, "message": "syntax",
             "category": "syntax"}
    with patch("core.agents.browser_agent.query_browser_ai") as m:
        m.return_value = {
            "success": True,
            "text": '```json\n{"tool": "task_complete", "message": "не могу"}\n```',
            "error": None,
        }
        r = apply_action(str(tmp_path), issue, "fix")
        assert not r["success"]
        assert "не смог" in r["error"]


def test_action_fix_success(tmp_path):
    from unittest.mock import patch
    _write(tmp_path / "a.py", "def broken(:\n    pass\n")
    issue = {"file": "a.py", "line": 1, "message": "syntax",
             "category": "syntax"}
    fixed = "def broken():\n    pass\n"
    ai_resp = (
        '```json\n'
        '{"tool": "write_file", "path": "a.py", '
        '"content": "def broken():\\n    pass\\n"}\n'
        '```'
    )
    with patch("core.agents.browser_agent.query_browser_ai") as m:
        m.return_value = {"success": True, "text": ai_resp, "error": None}
        r = apply_action(str(tmp_path), issue, "fix")
        assert r["success"], r
        assert r["action"] == "fix"
        assert (tmp_path / "a.py").read_text(encoding="utf-8") == fixed
        assert (tmp_path / "a.py.bak").exists()


def test_action_fix_rollback_on_broken(tmp_path):
    from unittest.mock import patch
    original = "def broken(:\n    pass\n"
    _write(tmp_path / "a.py", original)
    issue = {"file": "a.py", "line": 1, "message": "syntax",
             "category": "syntax"}
    # ИИ вернёт тоже битый код
    ai_resp = (
        '```json\n'
        '{"tool": "write_file", "path": "a.py", '
        '"content": "def still broken(:\\n    pass\\n"}\n'
        '```'
    )
    with patch("core.agents.browser_agent.query_browser_ai") as m:
        m.return_value = {"success": True, "text": ai_resp, "error": None}
        r = apply_action(str(tmp_path), issue, "fix")
        assert not r["success"]
        assert "откат" in r["error"]
        # Файл вернулся к оригиналу
        assert (tmp_path / "a.py").read_text(encoding="utf-8") == original
