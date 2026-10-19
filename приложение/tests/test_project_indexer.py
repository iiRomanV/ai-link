"""Тесты для core.project_indexer."""

from pathlib import Path

from core.project_indexer import (
    file_hash,
    extract_python_symbols,
    index_project,
    update_project_index,
    find_symbol_usages,
)
from core.project_state import load_state


def _write(path: Path, content: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_file_hash_stable(tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("hello", encoding="utf-8")
    h1 = file_hash(f)
    h2 = file_hash(f)
    assert h1 == h2
    assert len(h1) == 32  # md5


def test_file_hash_changes(tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("hello", encoding="utf-8")
    h1 = file_hash(f)
    f.write_text("hello world", encoding="utf-8")
    h2 = file_hash(f)
    assert h1 != h2


def test_extract_python_symbols_basic(tmp_path):
    f = tmp_path / "mod.py"
    _write(f, "import os\nfrom pathlib import Path\n\nclass Foo:\n    pass\n\ndef bar():\n    pass\n")
    r = extract_python_symbols(f)
    assert "Foo" in r["classes"]
    assert "bar" in r["functions"]
    assert "os" in r["imports"]
    assert "pathlib" in r["imports"]
    assert "error" not in r


def test_extract_python_symbols_syntax_error(tmp_path):
    f = tmp_path / "bad.py"
    _write(f, "def broken(:\n    pass\n")
    r = extract_python_symbols(f)
    assert "error" in r
    assert "syntax error" in r["error"]


def test_extract_python_symbols_async(tmp_path):
    f = tmp_path / "async_mod.py"
    _write(f, "import asyncio\n\nasync def fetch():\n    pass\n")
    r = extract_python_symbols(f)
    assert "fetch" in r["functions"]
    assert "asyncio" in r["imports"]


def test_index_project_counts(tmp_path):
    _write(tmp_path / "a.py", "def foo(): pass\n")
    _write(tmp_path / "b.py", "class Bar: pass\n")
    _write(tmp_path / "readme.md", "# hi\n")
    r = index_project(str(tmp_path))
    paths = [f["path"] for f in r["files"]]
    assert "a.py" in paths
    assert "b.py" in paths
    assert "readme.md" in paths
    assert "a.py" in r["symbols"]
    assert "b.py" in r["symbols"]
    assert "readme.md" not in r["symbols"]


def test_index_project_dependencies(tmp_path):
    _write(tmp_path / "core" / "__init__.py", "")
    _write(tmp_path / "core" / "storage.py", "def save(): pass\n")
    _write(tmp_path / "ui" / "__init__.py", "")
    _write(
        tmp_path / "ui" / "window.py",
        "from core.storage import save\n\ndef run(): save()\n",
    )
    r = index_project(str(tmp_path))
    deps = r["dependencies"].get("ui/window.py", [])
    assert "core/storage.py" in deps


def test_index_project_skips_pycache(tmp_path):
    _write(tmp_path / "main.py", "def go(): pass\n")
    _write(tmp_path / "__pycache__" / "main.cpython-310.pyc", "garbage")
    r = index_project(str(tmp_path))
    paths = [f["path"] for f in r["files"]]
    assert "main.py" in paths
    assert not any("__pycache__" in p for p in paths)


def test_index_project_skips_git(tmp_path):
    _write(tmp_path / "main.py", "def go(): pass\n")
    _write(tmp_path / ".git" / "config", "garbage")
    r = index_project(str(tmp_path))
    paths = [f["path"] for f in r["files"]]
    assert "main.py" in paths
    assert not any(".git" in p for p in paths)


def test_update_project_index_writes_state(tmp_path):
    _write(tmp_path / "main.py", "def go(): pass\n")
    result = update_project_index(str(tmp_path))
    assert result["files_count"] >= 1
    assert result["py_files"] >= 1
    state = load_state(str(tmp_path))
    assert len(state["structure"]["files"]) >= 1
    assert "main.py" in state["structure"]["symbols"]


def test_get_dependencies(tmp_path):
    from core.project_indexer import get_dependencies
    _write(tmp_path / "core" / "__init__.py", "")
    _write(tmp_path / "core" / "storage.py", "def save(): pass\n")
    _write(tmp_path / "ui" / "__init__.py", "")
    _write(
        tmp_path / "ui" / "window.py",
        "from core.storage import save\n\ndef run(): save()\n",
    )
    update_project_index(str(tmp_path))
    deps = get_dependencies(str(tmp_path), "ui/window.py")
    assert "core/storage.py" in deps


def test_get_dependencies_missing_file(tmp_path):
    from core.project_indexer import get_dependencies
    _write(tmp_path / "main.py", "def go(): pass\n")
    update_project_index(str(tmp_path))
    deps = get_dependencies(str(tmp_path), "не_существует.py")
    assert deps == []


def test_get_dependents(tmp_path):
    from core.project_indexer import get_dependents
    _write(tmp_path / "core" / "__init__.py", "")
    _write(tmp_path / "core" / "storage.py", "def save(): pass\n")
    _write(tmp_path / "ui" / "__init__.py", "")
    _write(
        tmp_path / "ui" / "window.py",
        "from core.storage import save\n\ndef run(): save()\n",
    )
    update_project_index(str(tmp_path))
    dependents = get_dependents(str(tmp_path), "core/storage.py")
    assert "ui/window.py" in dependents


def test_get_dependents_leaf(tmp_path):
    from core.project_indexer import get_dependents
    _write(tmp_path / "main.py", "def go(): pass\n")
    update_project_index(str(tmp_path))
    dependents = get_dependents(str(tmp_path), "main.py")
    assert dependents == []


def test_find_broken_links_clean(tmp_path):
    from core.project_indexer import find_broken_links
    _write(tmp_path / "core" / "__init__.py", "")
    _write(tmp_path / "core" / "storage.py", "def save(): pass\n")
    _write(
        tmp_path / "ui" / "window.py",
        "from core.storage import save\n\ndef run(): save()\n",
    )
    update_project_index(str(tmp_path))
    broken = find_broken_links(str(tmp_path))
    assert broken == []


def test_find_broken_links_detects(tmp_path):
    from core.project_indexer import find_broken_links
    _write(tmp_path / "core" / "__init__.py", "")
    _write(
        tmp_path / "ui" / "window.py",
        "from core.missing_module import foo\n\ndef run(): foo()\n",
    )
    update_project_index(str(tmp_path))
    broken = find_broken_links(str(tmp_path))
    assert any(b["import"] == "core.missing_module" for b in broken)


# --------------------------------------------------------------------------- #
# find_symbol_usages (SAFE-REPLACE, 2026-09-28)
# --------------------------------------------------------------------------- #
def test_find_symbol_usages_call(tmp_path):
    """Находит прямой вызов функции."""
    _write(tmp_path / "mod.py", "def hello():\n    return 1\n")
    _write(tmp_path / "caller.py",
           "from mod import hello\n\nprint(hello())\n")
    usages = find_symbol_usages(str(tmp_path), "hello")
    files = {u["file"] for u in usages}
    assert "caller.py" in files
    kinds = {u["kind"] for u in usages}
    assert "call" in kinds


def test_find_symbol_usages_attr(tmp_path):
    """Находит вызов через obj.method()."""
    _write(tmp_path / "mod.py",
           "class Box:\n    def open(self):\n        pass\n")
    _write(tmp_path / "caller.py",
           "b = Box()\nb.open()\n")
    usages = find_symbol_usages(str(tmp_path), "open")
    assert any(u["kind"] == "attr" for u in usages)


def test_find_symbol_usages_not_found(tmp_path):
    """Несуществующий символ → пусто."""
    _write(tmp_path / "a.py", "x = 1\n")
    usages = find_symbol_usages(str(tmp_path), "nonexistent_xyz")
    assert usages == []


def test_find_symbol_usages_skips_pycache(tmp_path):
    """__pycache__ игнорируется."""
    _write(tmp_path / "a.py", "print(hello())\n")
    _write(tmp_path / "__pycache__" / "b.py", "print(hello())\n")
    usages = find_symbol_usages(str(tmp_path), "hello")
    assert all("__pycache__" not in u["file"] for u in usages)


def test_find_symbol_usages_skip_tests(tmp_path):
    """skip_tests=True игнорирует tests/."""
    _write(tmp_path / "src.py", "hello()\n")
    _write(tmp_path / "tests" / "t.py", "hello()\n")
    usages = find_symbol_usages(str(tmp_path), "hello", skip_tests=True)
    files = {u["file"] for u in usages}
    assert "src.py" in files
    assert not any(f.startswith("tests/") for f in files)


def test_find_symbol_usages_broken_syntax(tmp_path):
    """Синтаксически битый файл не ломает функцию."""
    _write(tmp_path / "good.py", "hello()\n")
    _write(tmp_path / "bad.py", "def broken(:\n")
    usages = find_symbol_usages(str(tmp_path), "hello")
    files = {u["file"] for u in usages}
    assert "good.py" in files


def test_find_symbol_usages_empty_symbol(tmp_path):
    """Пустой символ → пусто."""
    _write(tmp_path / "a.py", "x = 1\n")
    usages = find_symbol_usages(str(tmp_path), "")
    assert usages == []

