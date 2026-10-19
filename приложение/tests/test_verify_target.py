"""B+ (19.10.2026): _verify_target — детерминированная проверка target.

Проверяем 4 типа acceptance-checks:
  • grep_absent   — regex НЕ должен найтись → done.
  • grep_present  — regex должен найтись → done.
  • file_exists   — файл существует → done.
  • python_compile — py_compile OK → done.

Плюс:
  • AND-семантика: все checks в acceptance[] должны пройти.
  • Нет acceptance[] → не done.
  • Битый regex → не done (лучше заблокировать).
  • Исключение .ai_link/__pycache__/.git при glob.
"""
from pathlib import Path

import pytest

from core.orchestrator import _verify_target


def _write(base, rel, text=""):
    p = Path(base) / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


# ─── grep_absent ───────────────────────────────────────────────

def test_grep_absent_pass_when_missing(tmp_path):
    """Пароля НЕТ в .py → grep_absent проходит."""
    _write(tmp_path, "config.py", "import os\nPW = os.environ.get('PW')\n")
    t = {"id": "P0-1", "acceptance": [{
        "type": "grep_absent",
        "pattern": r'ADMIN_PASSWORD\s*=\s*["\']',
        "glob": "**/*.py",
    }]}
    r = _verify_target(t, str(tmp_path))
    assert r["ok"] is True
    assert r["verifier"] == "code"


def test_grep_absent_fail_when_found(tmp_path):
    """Пароль ЕСТЬ в .py → grep_absent НЕ проходит."""
    _write(tmp_path, "config.py", 'ADMIN_PASSWORD = "123"\n')
    t = {"id": "P0-1", "acceptance": [{
        "type": "grep_absent",
        "pattern": r'ADMIN_PASSWORD\s*=\s*["\']',
        "glob": "**/*.py",
    }]}
    r = _verify_target(t, str(tmp_path))
    assert r["ok"] is False
    assert "grep_absent" in r["reason"]


def test_grep_absent_skips_ai_link_dir(tmp_path):
    """Файлы в .ai_link/ НЕ проверяются (там может быть план)."""
    _write(tmp_path, ".ai_link/foo.py", 'ADMIN_PASSWORD = "x"\n')
    _write(tmp_path, "app.py", "ok = 1\n")
    t = {"id": "P0-1", "acceptance": [{
        "type": "grep_absent",
        "pattern": r"ADMIN_PASSWORD",
        "glob": "**/*.py",
    }]}
    r = _verify_target(t, str(tmp_path))
    assert r["ok"] is True, "файл в .ai_link/ не должен учитываться"


# ─── grep_present ──────────────────────────────────────────────

def test_grep_present_pass(tmp_path):
    _write(tmp_path, "config.py",
           "import os\nPW = os.environ.get('ADMIN_PASSWORD')\n")
    t = {"id": "P0-1", "acceptance": [{
        "type": "grep_present",
        "pattern": r"os\.environ\.get\(['\"]ADMIN_PASSWORD",
        "path": "config.py",
    }]}
    r = _verify_target(t, str(tmp_path))
    assert r["ok"] is True


def test_grep_present_fail(tmp_path):
    _write(tmp_path, "config.py", "PW = 'hardcoded'\n")
    t = {"id": "P0-1", "acceptance": [{
        "type": "grep_present",
        "pattern": r"os\.environ",
        "path": "config.py",
    }]}
    r = _verify_target(t, str(tmp_path))
    assert r["ok"] is False
    assert "grep_present" in r["reason"]


# ─── file_exists ───────────────────────────────────────────────

def test_file_exists_pass(tmp_path):
    _write(tmp_path, ".gitignore", ".env\n")
    t = {"id": "P0-1", "acceptance": [{
        "type": "file_exists", "path": ".gitignore",
    }]}
    r = _verify_target(t, str(tmp_path))
    assert r["ok"] is True


def test_file_exists_fail(tmp_path):
    t = {"id": "P0-1", "acceptance": [{
        "type": "file_exists", "path": ".gitignore",
    }]}
    r = _verify_target(t, str(tmp_path))
    assert r["ok"] is False


# ─── python_compile ────────────────────────────────────────────

def test_python_compile_pass(tmp_path):
    _write(tmp_path, "a.py", "x = 1\n")
    t = {"id": "P0-1", "acceptance": [{
        "type": "python_compile", "path": "a.py",
    }]}
    r = _verify_target(t, str(tmp_path))
    assert r["ok"] is True


def test_python_compile_fail(tmp_path):
    _write(tmp_path, "a.py", "def broken(:\n    pass\n")
    t = {"id": "P0-1", "acceptance": [{
        "type": "python_compile", "path": "a.py",
    }]}
    r = _verify_target(t, str(tmp_path))
    assert r["ok"] is False


# ─── AND-семантика, битый regex, пустой acceptance ─────────────

def test_all_checks_must_pass(tmp_path):
    """Два checks, один не прошёл → не done."""
    _write(tmp_path, "config.py",
           'ADMIN_PASSWORD = "123"\n# token in code\n')
    t = {"id": "P0-1", "acceptance": [
        {"type": "grep_absent", "pattern": r'ADMIN_PASSWORD\s*=\s*["\']',
         "glob": "**/*.py"},
        {"type": "file_exists", "path": ".gitignore"},
    ]}
    # .gitignore НЕТ → второй check не пройдёт
    r = _verify_target(t, str(tmp_path))
    assert r["ok"] is False


def test_bad_regex_returns_not_ok(tmp_path):
    _write(tmp_path, "a.py", "x\n")
    t = {"id": "P0-1", "acceptance": [{
        "type": "grep_absent", "pattern": "[unclosed", "glob": "**/*.py",
    }]}
    r = _verify_target(t, str(tmp_path))
    assert r["ok"] is False
    assert "битый regex" in r["reason"]


def test_empty_acceptance_returns_not_ok(tmp_path):
    t = {"id": "P0-1"}
    r = _verify_target(t, str(tmp_path))
    assert r["ok"] is False
    assert "пустой acceptance" in r["reason"]


def test_unknown_type_returns_not_ok(tmp_path):
    t = {"id": "P0-1", "acceptance": [{
        "type": "unknown_check", "path": "x",
    }]}
    r = _verify_target(t, str(tmp_path))
    assert r["ok"] is False
    assert "неизвестный" in r["reason"]
