"""HAND-LESSONS (2026-10-02): read_file/replace_in_file на папке."""
import pytest
from pathlib import Path


def _make_hl(tmp_path):
    """Создаёт HandLink на временном проекте."""
    from core.hand_link import HandLink
    sub = tmp_path / "sub"
    sub.mkdir(parents=True, exist_ok=True)
    return HandLink(str(tmp_path))


def test_read_file_on_dir_returns_error(tmp_path):
    """read_file на папке → явная ошибка, не IsADirectoryError."""
    hl = _make_hl(tmp_path)
    (tmp_path / "sub").mkdir(exist_ok=True)
    r = hl.execute([{"type": "read_file", "path": "sub"}])
    assert r["success"] is False
    assert "папка" in r.get("error", "").lower() or \
           "not a file" in r.get("error", "").lower() or \
           "папка" in r.get("error", "")


def test_read_file_on_file_works(tmp_path):
    """read_file на файле → работает."""
    hl = _make_hl(tmp_path)
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    r = hl.execute([{"type": "read_file", "path": "a.py"}])
    assert r["success"] is True


def test_replace_in_file_on_dir_returns_error(tmp_path):
    """replace_in_file на папке → явная ошибка."""
    hl = _make_hl(tmp_path)
    (tmp_path / "sub").mkdir(exist_ok=True)
    r = hl.execute([{
        "type": "replace_in_file",
        "path": "sub",
        "find": "x",
        "replace": "y",
    }])
    assert r["success"] is False


def test_replace_in_file_on_file_works(tmp_path):
    """replace_in_file на файле → работает."""
    hl = _make_hl(tmp_path)
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    r = hl.execute([{
        "type": "replace_in_file",
        "path": "a.py",
        "find": "x = 1",
        "replace": "x = 2",
    }])
    assert r["success"] is True
    assert (tmp_path / "a.py").read_text(encoding="utf-8") == "x = 2\n"


def test_list_directory_on_file_returns_error(tmp_path):
    """list_directory на файле → ошибка (старое поведение)."""
    hl = _make_hl(tmp_path)
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    r = hl.execute([{"type": "list_directory", "path": "a.py"}])
    assert r["success"] is False
