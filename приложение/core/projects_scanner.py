"""Сканирование папки с проектами AI Link.

Пользователь сам указывает путь к папке проектов в настройках.
Сканер смотрит только внутри неё. Наружу не выходит.
"""

import json
import shutil
from pathlib import Path
from typing import Dict, Any, List, Optional
from datetime import datetime


def get_projects_dir() -> str:
    """Возвращает путь к папке проектов из настроек."""
    try:
        from config.config_manager import get_setting
        return get_setting("projects_dir", "") or ""
    except Exception:
        return ""


def set_projects_dir(path: str) -> None:
    """Сохраняет путь к папке проектов."""
    try:
        from config.config_manager import set_setting
        set_setting("projects_dir", path or "")
    except Exception:
        pass


def _is_inside(path: Path, base: Path) -> bool:
    """Проверяет, что path внутри base."""
    try:
        path.resolve().relative_to(base.resolve())
        return True
    except (ValueError, OSError):
        return False


def _read_plan_title(project_dir: Path) -> Dict[str, Any]:
    """Читает title и status из .ai_link/plan.json."""
    plan_path = project_dir / ".ai_link" / "plan.json"
    if not plan_path.exists():
        return {"title": project_dir.name, "status": "?",
                "has_plan": False, "mtime": 0}
    try:
        data = json.loads(plan_path.read_text(encoding="utf-8"))
    except Exception:
        return {"title": "", "status": "", "has_plan": False,
                "mtime": plan_path.stat().st_mtime}
    return {
        "title": data.get("title", "") or project_dir.name,
        "status": data.get("status", ""),
        "has_plan": True,
        "mtime": plan_path.stat().st_mtime,
    }


def _is_hidden(project_dir: Path) -> bool:
    """Проект помечен как скрытый?"""
    return (project_dir / ".ai_link" / "hidden.flag").exists()


def scan_projects(include_hidden: bool = False) -> List[Dict[str, Any]]:
    """Сканирует папку проектов. Возвращает список словарей.

    Формат:
      {
        "name": "имя_папки",
        "path": "/полный/путь",
        "title": "Название из плана",
        "status": "done|in_progress|...",
        "has_plan": bool,
        "hidden": bool,
        "mtime": float (время изменения плана),
        "mtime_str": "2026-09-14 18:30",
      }
    """
    base_str = get_projects_dir()
    if not base_str:
        return []
    base = Path(base_str)
    if not base.exists() or not base.is_dir():
        return []

    result: List[Dict[str, Any]] = []
    try:
        for child in sorted(base.iterdir()):
            if not child.is_dir():
                continue
            if child.name.startswith("."):
                continue
            # Ищем только папки с .ai_link (то есть "проекты AI Link")
            ai_link_dir = child / ".ai_link"
            if not ai_link_dir.exists():
                continue
            hidden = _is_hidden(child)
            if hidden and not include_hidden:
                continue
            meta = _read_plan_title(child)
            mtime = meta.get("mtime", 0) or 0
            mtime_str = ""
            if mtime:
                mtime_str = datetime.fromtimestamp(mtime).strftime(
                    "%Y-%m-%d %H:%M"
                )
            result.append({
                "name": child.name,
                "path": str(child),
                "title": meta.get("title", "") or child.name,
                "status": meta.get("status", ""),
                "has_plan": meta.get("has_plan", False),
                "hidden": hidden,
                "mtime": mtime,
                "mtime_str": mtime_str,
            })
    except Exception:
        pass

    # Сортировка: сначала видимые (по mtime сверху), потом скрытые
    result.sort(
        key=lambda x: (
            1 if x.get("hidden") else 0,    # скрытые — в конец
            -x.get("mtime", 0),              # свежие — вверх
        )
    )
    return result


def hide_project(project_dir: str) -> bool:
    """Помечает проект как скрытый (не удаляя)."""
    path = Path(project_dir)
    if not path.exists():
        return False
    flag = path / ".ai_link" / "hidden.flag"
    try:
        flag.parent.mkdir(parents=True, exist_ok=True)
        flag.write_text("hidden\n", encoding="utf-8")
        return True
    except Exception:
        return False


def unhide_project(project_dir: str) -> bool:
    """Убирает метку «скрытый»."""
    flag = Path(project_dir) / ".ai_link" / "hidden.flag"
    if not flag.exists():
        return False
    try:
        flag.unlink()
        return True
    except Exception:
        return False


def rename_project(project_dir: str, new_title: str) -> bool:
    """Меняет title в plan.json. Если плана нет — создаёт минимальный."""
    new_title = (new_title or "").strip()
    if not new_title:
        return False
    base = Path(project_dir)
    if not base.exists():
        return False
    ai_link_dir = base / ".ai_link"
    ai_link_dir.mkdir(parents=True, exist_ok=True)
    path = ai_link_dir / "plan.json"
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    else:
        data = {"stages": []}
    data["title"] = new_title
    if "status" not in data:
        data["status"] = "?"
    try:
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return True
    except Exception:
        return False


def clear_project_memory(project_dir: str) -> bool:
    """Очищает содержимое .ai_link/, но оставляет саму папку
    и СОХРАНЯЕТ название проекта.

    После очистки создаётся минимальный plan.json с тем же title
    и пустыми stages, статус «?».
    """
    base_str = get_projects_dir()
    if not base_str:
        return False
    path = Path(project_dir)
    if not path.exists():
        return False
    if not _is_inside(path, Path(base_str)):
        return False

    ai_link_dir = path / ".ai_link"
    # Прочитаем старый title (если есть)
    old_title = ""
    old_plan = ai_link_dir / "plan.json"
    if old_plan.exists():
        try:
            data = json.loads(old_plan.read_text(encoding="utf-8"))
            old_title = data.get("title", "") or ""
        except Exception:
            old_title = ""
    if not old_title:
        old_title = path.name

    # Удаляем содержимое
    try:
        if ai_link_dir.exists():
            for child in ai_link_dir.iterdir():
                if child.is_dir():
                    shutil.rmtree(str(child))
                else:
                    child.unlink()
        else:
            ai_link_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        return False

    # Создаём новый минимальный plan.json с сохранённым title
    try:
        new_plan = {
            "title": old_title,
            "goal": "",
            "status": "?",
            "stages": [],
            "created_at": "",
            "cleared_at": datetime.now().isoformat(timespec="seconds"),
        }
        (ai_link_dir / "plan.json").write_text(
            json.dumps(new_plan, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception:
        pass

    return True


def delete_project(project_dir: str) -> bool:
    """Удаляет всю папку проекта (код + .ai_link).

    Проверяет, что путь внутри папки проектов.
    """
    base_str = get_projects_dir()
    if not base_str:
        return False
    path = Path(project_dir)
    if not path.exists():
        return False
    # Защита: путь внутри папки проектов
    if not _is_inside(path, Path(base_str)):
        return False
    # Защита: не удаляем саму папку проектов
    try:
        if path.resolve() == Path(base_str).resolve():
            return False
    except Exception:
        return False
    try:
        shutil.rmtree(str(path))
        return True
    except Exception:
        return False
