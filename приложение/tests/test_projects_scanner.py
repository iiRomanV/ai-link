"""Тесты для core.projects_scanner."""

import json
from pathlib import Path
from unittest.mock import patch

from core import projects_scanner
from core.projects_scanner import (
    get_projects_dir, set_projects_dir, scan_projects,
    hide_project, unhide_project, rename_project,
    clear_project_memory, delete_project,
)


def _setup_project(base: Path, name: str, title: str = "",
                    status: str = "in_progress",
                    with_plan: bool = True,
                    hidden: bool = False,
                    code_file: str = "main.py"):
    """Создаёт проект в папке базы."""
    proj = base / name
    proj.mkdir(parents=True, exist_ok=True)
    (proj / ".ai_link").mkdir(exist_ok=True)
    if code_file:
        (proj / code_file).write_text("# code\n", encoding="utf-8")
    if with_plan:
        (proj / ".ai_link" / "plan.json").write_text(
            json.dumps({
                "title": title or name,
                "status": status,
                "stages": [],
            }, ensure_ascii=False),
            encoding="utf-8",
        )
    if hidden:
        (proj / ".ai_link" / "hidden.flag").write_text("hidden\n",
                                                        encoding="utf-8")
    return proj


def _patch_base(tmp_path):
    return patch.object(
        projects_scanner, "get_projects_dir",
        return_value=str(tmp_path)
    )


# === get/set ===

def test_get_projects_dir_empty():
    with patch("config.config_manager.get_setting", return_value=""):
        assert get_projects_dir() == ""


def test_get_projects_dir_value():
    with patch("config.config_manager.get_setting",
               return_value="/tmp/proj"):
        assert get_projects_dir() == "/tmp/proj"


def test_set_projects_dir():
    with patch("config.config_manager.set_setting") as m:
        set_projects_dir("/tmp/x")
        m.assert_called_once_with("projects_dir", "/tmp/x")


# === scan_projects ===

def test_scan_no_base():
    with _patch_base(Path("/nonexistent/path/123456")):
        assert scan_projects() == []


def test_scan_empty(tmp_path):
    with _patch_base(tmp_path):
        assert scan_projects() == []


def test_scan_finds_project(tmp_path):
    _setup_project(tmp_path, "proj1", title="Проект 1")
    with _patch_base(tmp_path):
        result = scan_projects()
        assert len(result) == 1
        assert result[0]["name"] == "proj1"
        assert result[0]["title"] == "Проект 1"
        assert result[0]["has_plan"] is True


def test_scan_skips_non_ai_link_dirs(tmp_path):
    """Папки без .ai_link не считаются проектами."""
    (tmp_path / "просто_папка").mkdir()
    (tmp_path / "просто_папка" / "file.txt").write_text("x")
    _setup_project(tmp_path, "real_project")
    with _patch_base(tmp_path):
        result = scan_projects()
        assert len(result) == 1
        assert result[0]["name"] == "real_project"


def test_scan_skips_hidden_dirs(tmp_path):
    (tmp_path / ".hidden").mkdir()
    _setup_project(tmp_path, "visible")
    with _patch_base(tmp_path):
        result = scan_projects()
        assert len(result) == 1
        assert result[0]["name"] == "visible"


def test_scan_hidden_project_excluded(tmp_path):
    _setup_project(tmp_path, "proj1", hidden=True)
    _setup_project(tmp_path, "proj2", hidden=False)
    with _patch_base(tmp_path):
        result = scan_projects()
        assert len(result) == 1
        assert result[0]["name"] == "proj2"


def test_scan_hidden_included(tmp_path):
    _setup_project(tmp_path, "proj1", hidden=True)
    with _patch_base(tmp_path):
        result = scan_projects(include_hidden=True)
        assert len(result) == 1
        assert result[0]["hidden"] is True


def test_scan_sorted_by_mtime(tmp_path):
    import time
    _setup_project(tmp_path, "old")
    time.sleep(0.05)
    _setup_project(tmp_path, "new")
    with _patch_base(tmp_path):
        result = scan_projects()
        # Свежий — первым
        assert result[0]["name"] == "new"


def test_scan_no_plan_falls_back_to_dirname(tmp_path):
    _setup_project(tmp_path, "myproj", with_plan=False)
    with _patch_base(tmp_path):
        result = scan_projects()
        assert len(result) == 1
        assert result[0]["title"] == "myproj"
        assert result[0]["has_plan"] is False


# === hide/unhide ===

def test_hide_project(tmp_path):
    proj = _setup_project(tmp_path, "p1")
    assert hide_project(str(proj)) is True
    assert (proj / ".ai_link" / "hidden.flag").exists()


def test_unhide_project(tmp_path):
    proj = _setup_project(tmp_path, "p1", hidden=True)
    assert unhide_project(str(proj)) is True
    assert not (proj / ".ai_link" / "hidden.flag").exists()


def test_unhide_missing(tmp_path):
    assert unhide_project(str(tmp_path / "nope")) is False


# === rename_project ===

def test_rename_project(tmp_path):
    proj = _setup_project(tmp_path, "p1", title="Старое")
    assert rename_project(str(proj), "Новое") is True
    data = json.loads((proj / ".ai_link" / "plan.json").read_text())
    assert data["title"] == "Новое"


def test_rename_empty_title(tmp_path):
    proj = _setup_project(tmp_path, "p1")
    assert rename_project(str(proj), "") is False
    assert rename_project(str(proj), "   ") is False


def test_rename_no_plan(tmp_path):
    """Если плана нет — rename_project создаёт минимальный plan.json."""
    proj = _setup_project(tmp_path, "p1", with_plan=False)
    assert rename_project(str(proj), "New") is True
    # Файл создан, title записан
    plan_path = proj / ".ai_link" / "plan.json"
    assert plan_path.exists()
    import json as _json
    data = _json.loads(plan_path.read_text(encoding="utf-8"))
    assert data["title"] == "New"


# === clear_project_memory ===

def test_clear_memory(tmp_path):
    """После очистки .ai_link/ остаётся, но с минимальным plan.json.

    Название проекта сохраняется.
    """
    proj = _setup_project(tmp_path, "p1", title="Мой проект")
    plan_path = proj / ".ai_link" / "plan.json"
    assert plan_path.exists()
    with _patch_base(tmp_path):
        assert clear_project_memory(str(proj)) is True
    # Папка .ai_link осталась
    assert (proj / ".ai_link").exists()
    # plan.json пересоздан с тем же title
    assert plan_path.exists()
    import json as _json
    data = _json.loads(plan_path.read_text(encoding="utf-8"))
    assert data["title"] == "Мой проект"
    assert data["status"] == "?"
    assert data["stages"] == []
    # Код остался
    assert (proj / "main.py").exists()


def test_clear_memory_outside_base(tmp_path):
    """Нельзя чистить вне папки проектов."""
    other = tmp_path / "outside"
    other.mkdir()
    (other / ".ai_link").mkdir()
    base = tmp_path / "base"
    base.mkdir()
    with _patch_base(base):
        assert clear_project_memory(str(other)) is False
    # .ai_link на месте
    assert (other / ".ai_link").exists()


# === delete_project ===

def test_delete_project(tmp_path):
    proj = _setup_project(tmp_path, "p1")
    with _patch_base(tmp_path):
        assert delete_project(str(proj)) is True
    assert not proj.exists()


def test_delete_outside_base(tmp_path):
    other = tmp_path / "outside"
    other.mkdir()
    (other / ".ai_link").mkdir()
    base = tmp_path / "base"
    base.mkdir()
    with _patch_base(base):
        assert delete_project(str(other)) is False
    assert other.exists()


def test_delete_base_itself(tmp_path):
    """Нельзя удалить саму папку проектов."""
    with _patch_base(tmp_path):
        assert delete_project(str(tmp_path)) is False
    assert tmp_path.exists()


def test_delete_missing(tmp_path):
    with _patch_base(tmp_path):
        assert delete_project(str(tmp_path / "nope")) is False
