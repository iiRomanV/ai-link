"""Тесты для core.post_check и интеграции с HandLink."""

from pathlib import Path
from core.post_check import (
    check_py_compile, check_project_tests, check_file,
)
from core.hand_link import HandLink


def _write(path: Path, content: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_check_py_compile_ok(tmp_path):
    f = tmp_path / "good.py"
    _write(f, "x = 1\n")
    r = check_py_compile(f)
    assert r["ok"]


def test_check_py_compile_broken(tmp_path):
    f = tmp_path / "bad.py"
    _write(f, "def broken(:\n")
    r = check_py_compile(f)
    assert not r["ok"]
    assert "error" in r


def test_check_project_tests_skipped_no_tests(tmp_path):
    r = check_project_tests(tmp_path)
    assert r["ok"]
    assert r.get("skipped")


def test_check_project_tests_pass(tmp_path):
    _write(tmp_path / "tests" / "test_simple.py",
           "def test_ok():\n    assert 1 == 1\n")
    r = check_project_tests(tmp_path)
    assert r["ok"]
    assert not r.get("skipped")


def test_check_project_tests_fail(tmp_path):
    _write(tmp_path / "tests" / "test_fail.py",
           "def test_bad():\n    assert 1 == 2\n")
    r = check_project_tests(tmp_path)
    assert not r["ok"]


def test_check_file_python_in_tests_skips_pytest(tmp_path):
    _write(tmp_path / "tests" / "test_x.py", "def test_x(): assert True\n")
    f = tmp_path / "tests" / "test_x.py"
    r = check_file(f, tmp_path)
    assert r["py_compile"] is not None
    assert r["pytest"] is None  # pytest не запускается для файлов в tests/


def test_handlink_creates_bak_on_overwrite(tmp_path):
    hl = HandLink(str(tmp_path))
    # Первое создание
    hl.execute([{"type": "create_file", "path": "a.py",
                 "content": "x = 1\n"}])
    assert not (tmp_path / "a.py.bak").exists()
    # Перезапись — должен создаться .bak
    hl.execute([{"type": "create_file", "path": "a.py",
                 "content": "x = 2\n"}])
    assert (tmp_path / "a.py.bak").exists()
    assert (tmp_path / "a.py.bak").read_text() == "x = 1\n"


def test_handlink_returns_post_checks(tmp_path):
    hl = HandLink(str(tmp_path))
    r = hl.execute([{"type": "create_file", "path": "good.py",
                     "content": "x = 1\n"}])
    assert r["success"]
    assert "post_checks" in r
    assert "good.py" in r["post_checks"]
    assert r["post_checks"]["good.py"]["py_compile"]["ok"]


def test_handlink_rollback_on_broken_py(tmp_path):
    hl = HandLink(str(tmp_path))
    # Сначала хороший файл
    hl.execute([{"type": "create_file", "path": "a.py",
                 "content": "x = 1\n"}])
    # Теперь ломаем
    r = hl.execute([{"type": "create_file", "path": "a.py",
                     "content": "def broken(:\n"}])
    # Файл должен откатиться к старой версии
    assert (tmp_path / "a.py").read_text() == "x = 1\n"
    # И в post_checks отмечен rollback
    pc = r["post_checks"]["a.py"]
    assert pc["rolled_back"] is True


def test_handlink_does_not_rollback_on_pytest_fail(tmp_path):
    # Создаём плохой тест
    _write(tmp_path / "tests" / "test_bad.py",
           "def test_x(): assert False\n")
    hl = HandLink(str(tmp_path))
    r = hl.execute([{"type": "create_file", "path": "new.py",
                     "content": "y = 2\n"}])
    # py_compile OK — файл не откатился
    pc = r["post_checks"]["new.py"]
    assert pc["py_compile"]["ok"]
    assert pc["rolled_back"] is False
    # pytest упал — это в отчёте
    assert pc["pytest"] is not None
    assert not pc["pytest"]["ok"]


def test_replace_in_file_post_check(tmp_path):
    hl = HandLink(str(tmp_path))
    hl.execute([{"type": "create_file", "path": "a.py",
                 "content": "x = 1\n"}])
    r = hl.execute([{"type": "replace_in_file", "path": "a.py",
                     "find": "x = 1", "replace": "x = 42"}])
    assert r["success"]
    assert (tmp_path / "a.py").read_text() == "x = 42\n"
    assert "post_checks" in r
