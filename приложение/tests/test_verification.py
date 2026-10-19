"""Тесты VERIFICATION-RECEIPT."""

import json
from pathlib import Path

from core import verification as vr


def _mk_project(tmp_path, with_tests=False, with_bad_py=False):
    (tmp_path / "app.py").write_text("x = 1\n", encoding="utf-8")
    if with_bad_py:
        (tmp_path / "broken.py").write_text(
            "def f(:\n  pass\n", encoding="utf-8")
    if with_tests:
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "test_ok.py").write_text(
            "def test_ok():\n    assert 1 == 1\n", encoding="utf-8")
    return tmp_path


def test_build_receipt_structure(tmp_path):
    _mk_project(tmp_path)
    r = vr.build_receipt(str(tmp_path), phase="code", run_id="r1")
    assert r["phase"] == "code"
    assert r["run_id"] == "r1"
    assert "syntax" in r and "tests" in r and "imports" in r
    assert "files" in r and "ruff" in r and "git_diff" in r
    assert r["status"] in ("verified", "failed", "partial")


def test_syntax_failed(tmp_path):
    _mk_project(tmp_path, with_bad_py=True)
    r = vr.build_receipt(str(tmp_path), phase="code")
    assert r["syntax"]["status"] == "failed"
    assert r["status"] == "failed"


def test_tests_passed(tmp_path):
    _mk_project(tmp_path, with_tests=True)
    r = vr.build_receipt(str(tmp_path), phase="code")
    assert r["tests"]["status"] == "passed"
    assert r["tests"]["passed"] >= 1
    assert r["tests"]["failed"] == 0


def test_tests_skipped(tmp_path):
    _mk_project(tmp_path, with_tests=False)
    r = vr.build_receipt(str(tmp_path), phase="code")
    assert r["tests"]["status"] == "skipped"


def test_save_and_load(tmp_path):
    _mk_project(tmp_path)
    r = vr.build_and_save(str(tmp_path), phase="code", run_id="r2")
    assert r["saved_to"]
    last = vr.load_last_receipt(str(tmp_path))
    assert last["phase"] == "code"
    assert last["run_id"] == "r2"
    # файлы
    files = list((tmp_path / ".ai_link" / "verification").glob("*.json"))
    assert len(files) >= 2  # main + last


def test_missing_project_dir():
    r = vr.build_receipt("/tmp/__no_such_ai_link_test__", phase="code")
    assert r["status"] == "failed"
    assert "error" in r


def test_ruff_unavailable(tmp_path, monkeypatch):
    _mk_project(tmp_path)
    monkeypatch.setattr(vr.shutil, "which", lambda name: None)
    r = vr.build_receipt(str(tmp_path), phase="code")
    assert r["ruff"]["status"] == "unavailable"


def test_git_diff_not_git(tmp_path, monkeypatch):
    _mk_project(tmp_path)
    # Эмулируем отсутствие git-репо: git есть, rc != 0
    import subprocess as sp
    real_run = sp.run

    def fake_run(*a, **kw):
        if a and a[0] and a[0][0] == "git":
            class _R:
                returncode = 128
                stdout = ""
                stderr = "not a git repo"
            return _R()
        return real_run(*a, **kw)
    monkeypatch.setattr(vr.subprocess, "run", fake_run)
    r = vr.build_receipt(str(tmp_path), phase="code")
    assert r["git_diff"]["status"] == "not_git"
