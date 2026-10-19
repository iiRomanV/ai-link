"""Тесты REPO-MAP."""

from pathlib import Path

from core import repo_map as rm


def _mk(tmp_path):
    (tmp_path / "core").mkdir()
    (tmp_path / "core" / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "core" / "storage.py").write_text(
        "class Storage:\n"
        "    def save(self):\n"
        "        pass\n"
        "    def load(self):\n"
        "        pass\n",
        encoding="utf-8",
    )
    (tmp_path / "core" / "manager.py").write_text(
        "from core.storage import Storage\n"
        "def run_manager():\n"
        "    pass\n",
        encoding="utf-8",
    )
    (tmp_path / "main.py").write_text(
        "from core.manager import run_manager\n"
        "def main():\n"
        "    run_manager()\n",
        encoding="utf-8",
    )
    return tmp_path


def test_build_repo_map_structure(tmp_path):
    _mk(tmp_path)
    m = rm.build_repo_map(str(tmp_path))
    assert m["status"] == "ok"
    assert m["py_files"] >= 3
    assert "core/storage.py" in m["modules"]
    assert "Storage" in m["modules"]["core/storage.py"]["classes"]
    assert "save" in m["modules"]["core/storage.py"]["functions"]


def test_dependencies_built(tmp_path):
    _mk(tmp_path)
    m = rm.build_repo_map(str(tmp_path))
    deps = m["modules"]["core/manager.py"]["dependencies"]
    assert "core/storage.py" in deps


def test_save_and_load(tmp_path):
    _mk(tmp_path)
    path = rm.save_repo_map(str(tmp_path))
    assert path
    assert Path(path).exists()
    loaded = rm.load_repo_map(str(tmp_path))
    assert loaded["py_files"] >= 3


def test_render_for_llm(tmp_path):
    _mk(tmp_path)
    rm.save_repo_map(str(tmp_path))
    text = rm.render_for_llm(str(tmp_path))
    assert "КАРТА ПРОЕКТА" in text
    assert "core/storage.py" in text
    assert len(text) <= 3000


def test_render_max_chars(tmp_path):
    _mk(tmp_path)
    # Добавим много модулей
    for i in range(50):
        (tmp_path / f"mod{i}.py").write_text(
            f"def f{i}():\n    pass\n", encoding="utf-8")
    rm.save_repo_map(str(tmp_path))
    text = rm.render_for_llm(str(tmp_path), max_chars=300)
    assert len(text) <= 300


def test_relevant_files(tmp_path):
    _mk(tmp_path)
    rm.save_repo_map(str(tmp_path))
    files = rm.relevant_files(str(tmp_path), "storage save", top_k=3)
    assert "core/storage.py" in files


def test_relevant_empty_query(tmp_path):
    _mk(tmp_path)
    assert rm.relevant_files(str(tmp_path), "") == []
    assert rm.relevant_files(str(tmp_path), "ab") == []


def test_map_summary(tmp_path):
    _mk(tmp_path)
    rm.save_repo_map(str(tmp_path))
    s = rm.map_summary(str(tmp_path))
    assert s["py_files"] >= 3
    assert "core" in s["groups"]


def test_missing_project():
    m = rm.build_repo_map("/tmp/__no_such_repo_map_test__")
    assert m["status"] == "failed"
    assert m["py_files"] == 0


def test_reset(tmp_path):
    _mk(tmp_path)
    rm.save_repo_map(str(tmp_path))
    assert rm.load_repo_map(str(tmp_path))
    rm.reset_repo_map(str(tmp_path))
    assert rm.load_repo_map(str(tmp_path)) == {}
