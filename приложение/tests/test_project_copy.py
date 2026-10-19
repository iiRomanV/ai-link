"""Тесты для core.project_copy (Этап 3, 2026-10-08)."""

from pathlib import Path

from core.project_copy import (
    copy_project_for_audit,
    is_our_copy,
    _dir_skipped,
    _file_skipped,
)


def _make_project(root: Path) -> None:
    """Создаёт фейковый проект."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "main.py").write_text("print('hi')\n", encoding="utf-8")
    (root / "README.md").write_text("# Proj\n", encoding="utf-8")
    (root / "requirements.txt").write_text("requests\n", encoding="utf-8")
    (root / ".env.example").write_text("KEY=your_key\n", encoding="utf-8")
    (root / ".env").write_text("KEY=real_key\n", encoding="utf-8")
    (root / ".gitignore").write_text(".env\n", encoding="utf-8")

    # Мусор — не должен копироваться.
    (root / "__pycache__").mkdir(exist_ok=True)
    (root / "__pycache__" / "x.pyc").write_text("cache", encoding="utf-8")
    (root / "app.log").write_text("log", encoding="utf-8")

    # git папка.
    (root / ".git").mkdir(exist_ok=True)
    (root / ".git" / "config").write_text("git_config", encoding="utf-8")

    # venv.
    (root / ".venv").mkdir(exist_ok=True)
    (root / ".venv" / "pyvenv.cfg").write_text("venv", encoding="utf-8")


def test_copy_creates_folder_next_to_source(tmp_path):
    src = tmp_path / "myproj"
    _make_project(src)

    r = copy_project_for_audit(str(src), user_name="test_user")
    assert r["ok"] is True, r
    copy_path = Path(r["copy_path"])
    assert copy_path.exists()
    assert copy_path.name.startswith("myproj_source_")


def test_copy_preserves_important_files(tmp_path):
    src = tmp_path / "myproj"
    _make_project(src)

    r = copy_project_for_audit(str(src))
    assert r["ok"] is True
    dst = Path(r["copy_path"])

    assert (dst / "main.py").exists()
    assert (dst / "README.md").exists()
    assert (dst / "requirements.txt").exists()
    assert (dst / ".env.example").exists()
    # .env сохраняется (пользователь сам решит, что с ним).
    assert (dst / ".env").exists()
    assert (dst / ".gitignore").exists()


def test_copy_skips_garbage(tmp_path):
    src = tmp_path / "myproj"
    _make_project(src)

    r = copy_project_for_audit(str(src))
    assert r["ok"] is True
    dst = Path(r["copy_path"])

    assert not (dst / "__pycache__").exists()
    assert not (dst / ".git").exists()
    assert not (dst / ".venv").exists()
    assert not (dst / "app.log").exists()


def test_copy_writes_identity(tmp_path):
    src = tmp_path / "myproj"
    _make_project(src)

    r = copy_project_for_audit(str(src), user_name="test_user")
    assert r["ok"] is True
    dst = Path(r["copy_path"])

    ident = dst / ".ai_link" / "IDENTITY"
    assert ident.exists()
    text = ident.read_text(encoding="utf-8")
    assert "created_by: link" in text
    assert "user: test_user" in text
    assert "source:" in text


def test_is_our_copy_true(tmp_path):
    src = tmp_path / "myproj"
    _make_project(src)

    r = copy_project_for_audit(str(src))
    assert r["ok"] is True
    assert is_our_copy(r["copy_path"]) is True


def test_is_our_copy_false_for_source(tmp_path):
    src = tmp_path / "myproj"
    _make_project(src)
    assert is_our_copy(str(src)) is False


def test_copy_nonexistent_source(tmp_path):
    r = copy_project_for_audit(str(tmp_path / "nope"))
    assert r["ok"] is False
    assert "не найдена" in r["error"]


def test_copy_refuses_existing_target(tmp_path, monkeypatch):
    """Если папка-копия уже есть — не перезаписываем."""
    src = tmp_path / "myproj"
    _make_project(src)

    import core.project_copy as _pc
    import types

    # Фиксируем timestamp, чтобы имя было предсказуемым.
    _fixed = "20260101_000000"

    class _FakeDateTime:
        @staticmethod
        def now():
            class _T:
                @staticmethod
                def strftime(fmt):
                    return _fixed
                @staticmethod
                def isoformat(timespec="seconds"):
                    return _fixed
            return _T()

    monkeypatch.setattr(_pc, "datetime", _FakeDateTime)

    # Первая копия — создаётся.
    r1 = copy_project_for_audit(str(src))
    assert r1["ok"] is True, r1

    # Вторая копия с тем же ts — должна отказаться.
    r2 = copy_project_for_audit(str(src))
    assert r2["ok"] is False
    assert "уже существует" in r2["error"]


def test_dir_skipped():
    assert _dir_skipped(".git") is True
    assert _dir_skipped("__pycache__") is True
    assert _dir_skipped(".venv") is True
    assert _dir_skipped("node_modules") is True
    assert _dir_skipped("src") is False
    assert _dir_skipped("tests") is False


def test_file_skipped():
    assert _file_skipped("x.pyc") is True
    assert _file_skipped("app.log") is True
    assert _file_skipped("x.tmp") is True
    assert _file_skipped("main.py") is False
    assert _file_skipped("README.md") is False
