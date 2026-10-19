"""Тесты для core.auditor."""

from pathlib import Path
from core.auditor import (
    check_syntax, check_empty_files, check_todos,
    run_audit,
)


def _write(path: Path, content: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_check_syntax_clean(tmp_path):
    _write(tmp_path / "a.py", "def foo():\n    return 1\n")
    issues = check_syntax(str(tmp_path))
    assert issues == []


def test_check_syntax_broken(tmp_path):
    _write(tmp_path / "bad.py", "def broken(:\n    pass\n")
    issues = check_syntax(str(tmp_path))
    assert len(issues) == 1
    assert issues[0]["severity"] == "high"
    assert issues[0]["category"] == "syntax"
    assert issues[0]["file"] == "bad.py"


def test_check_empty_files(tmp_path):
    _write(tmp_path / "empty.py", "")
    _write(tmp_path / "ok.py", "x = 1\n")
    _write(tmp_path / "__init__.py", "")
    issues = check_empty_files(str(tmp_path))
    assert len(issues) == 1
    assert issues[0]["file"] == "empty.py"


def test_check_todos_only_comments(tmp_path):
    _write(tmp_path / "a.py",
           "# TODO: сделать позже\n"
           "x = 'TODO в строке'\n"
           "# обычный комментарий\n"
           "def foo():\n"
           "    pass  # FIXME: потом\n")
    issues = check_todos(str(tmp_path))
    assert len(issues) == 2
    msgs = [i["message"] for i in issues]
    assert any("сделать позже" in m for m in msgs)
    assert any("потом" in m for m in msgs)


def test_check_todos_skips_auditor(tmp_path):
    (tmp_path / "core").mkdir()
    _write(tmp_path / "core" / "auditor.py", "# TODO: внутри аудитора\n")
    issues = check_todos(str(tmp_path))
    assert issues == []


def test_run_audit_aggregates(tmp_path):
    _write(tmp_path / "a.py", "x = 1\n")
    _write(tmp_path / "bad.py", "def broken(:\n    pass\n")
    _write(tmp_path / "b.py", "# TODO: fix\n")
    _write(tmp_path / "empty.py", "")
    r = run_audit(str(tmp_path))
    assert r["total"] >= 3
    assert r["by_severity"]["high"] >= 1
    assert r["by_category"]["syntax"] >= 1
    assert r["by_category"]["todo"] >= 1
    assert r["by_category"]["empty"] >= 1


def test_run_audit_skips_pycache(tmp_path):
    _write(tmp_path / "a.py", "x = 1\n")
    _write(tmp_path / "__pycache__" / "old.py", "def broken(:\n")
    r = run_audit(str(tmp_path))
    files = [i["file"] for i in r["issues"]]
    assert not any("__pycache__" in f for f in files)
