"""Scene Manager (A.5, подэтап 6.2).

Управление моделями сцены и их назначением на роли.

Слоты (9):
  link, arms, brain, user,
  planning, coding, review, verifier, comprehension

Модели хранятся как файлы в ~/.ai_link/scene_models/.
Назначения — в ~/.ai_link/scene.json.
"""
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional


MODELS_DIR = Path.home() / ".ai_link" / "scene_models"
ASSIGNMENTS_FILE = Path.home() / ".ai_link" / "scene.json"

# Поддерживаемые форматы (расширения файлов)
SUPPORTED_EXTS = {".png", ".gif"}

# Слоты сцены: id → человеческое имя (русский).
# Порядок = порядок в диалоге.
SLOTS: Dict[str, str] = {
    "link": "Линк",
    "arms": "Руки",
    "brain": "Мозг",
    "user": "Пользователь",
    "planning": "План",
    "coding": "Код",
    "review": "Ревью",
    "verifier": "Проверка",
    "comprehension": "Осмысление",
}


def _ensure_dir() -> None:
    MODELS_DIR.mkdir(parents=True, exist_ok=True)


def list_models() -> List[Dict[str, Any]]:
    """Возвращает список моделей из ~/.ai_link/scene_models/.

    Каждая модель: {"name": "mycat.png", "path": "...", "size_kb": 12.3}
    Отсортировано по имени.
    """
    _ensure_dir()
    result = []
    for p in sorted(MODELS_DIR.iterdir()):
        if not p.is_file():
            continue
        if p.suffix.lower() not in SUPPORTED_EXTS:
            continue
        try:
            size = p.stat().st_size / 1024.0
        except Exception:
            size = 0.0
        result.append({
            "name": p.name,
            "path": str(p),
            "size_kb": round(size, 1),
        })
    return result


def load_assignments() -> Dict[str, str]:
    """Читает назначения ролей. Возвращает {slot: model_filename}.

    Отсутствующие слоты — не в словаре (или пустые строки).
    """
    if not ASSIGNMENTS_FILE.exists():
        return {s: "" for s in SLOTS}
    try:
        data = json.loads(ASSIGNMENTS_FILE.read_text(encoding="utf-8"))
        assigns = data.get("assignments", {})
        result = {s: "" for s in SLOTS}
        for slot in SLOTS:
            v = assigns.get(slot, "")
            if isinstance(v, str):
                result[slot] = v
        return result
    except Exception:
        return {s: "" for s in SLOTS}


def save_assignments(assignments: Dict[str, str]) -> None:
    """Сохраняет назначения в ~/.ai_link/scene.json."""
    _ensure_dir()
    data = {
        "version": "1.0",
        "assignments": {s: assignments.get(s, "") for s in SLOTS},
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }
    ASSIGNMENTS_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def get_model_for_slot(slot: str) -> Optional[Dict[str, Any]]:
    """Возвращает модель для слота или None.

    Если файл не существует — тоже None (модель удалили вручную).
    """
    assigns = load_assignments()
    name = assigns.get(slot, "")
    if not name:
        return None
    path = MODELS_DIR / name
    if not path.exists():
        return None
    return {"name": name, "path": str(path)}


def add_model(src_path: str) -> Dict[str, Any]:
    """Копирует файл в ~/.ai_link/scene_models/.

    Возвращает {"ok": bool, "name": str, "error": str}.
    Если файл с таким именем уже есть — добавляет суффикс _1, _2 и т.д.
    """
    import shutil

    _ensure_dir()
    src = Path(src_path)
    if not src.exists() or not src.is_file():
        return {"ok": False, "name": "", "error": "файл не найден"}
    if src.suffix.lower() not in SUPPORTED_EXTS:
        return {"ok": False, "name": "",
                "error": f"формат {src.suffix} не поддерживается "
                         f"(только PNG, GIF)"}

    dst = MODELS_DIR / src.name
    if dst.exists():
        stem = src.stem
        ext = src.suffix
        n = 1
        while True:
            candidate = MODELS_DIR / f"{stem}_{n}{ext}"
            if not candidate.exists():
                dst = candidate
                break
            n += 1

    try:
        shutil.copy2(str(src), str(dst))
    except Exception as e:
        return {"ok": False, "name": "", "error": str(e)[:200]}

    return {"ok": True, "name": dst.name, "error": ""}


def delete_model(name: str) -> bool:
    """Удаляет модель по имени файла. Возвращает True, если удалена."""
    path = MODELS_DIR / name
    if not path.exists() or not path.is_file():
        return False
    try:
        path.unlink()
    except Exception:
        return False
    # Заодно убираем из назначений.
    assigns = load_assignments()
    changed = False
    for slot, v in assigns.items():
        if v == name:
            assigns[slot] = ""
            changed = True
    if changed:
        save_assignments(assigns)
    return True


# ============================================================
# SETTINGS (6.6, 2026-10-07): модель сна и будущие настройки
# ============================================================
DEFAULT_SLEEP_MODEL = "собачка_спит.gif"


def load_settings() -> Dict[str, Any]:
    """Читает настройки сцены (модель сна и др.) из scene.json."""
    result = {
        "sleep_model": DEFAULT_SLEEP_MODEL,
    }
    if not ASSIGNMENTS_FILE.exists():
        return result
    try:
        data = json.loads(ASSIGNMENTS_FILE.read_text(encoding="utf-8"))
        st = data.get("settings", {})
        if isinstance(st, dict):
            result.update(st)
    except Exception:
        pass
    # Гарантируем ключ.
    if not result.get("sleep_model"):
        result["sleep_model"] = DEFAULT_SLEEP_MODEL
    return result


def save_settings(settings: Dict[str, Any]) -> None:
    """Сохраняет настройки сцены (не трогая assignments)."""
    _ensure_dir()
    if ASSIGNMENTS_FILE.exists():
        try:
            data = json.loads(ASSIGNMENTS_FILE.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    else:
        data = {}
    data.setdefault("version", "1.0")
    data.setdefault("assignments", {s: "" for s in SLOTS})
    data["settings"] = dict(settings)
    data["updated_at"] = datetime.now().isoformat(timespec="seconds")
    ASSIGNMENTS_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def get_sleep_model_path() -> Optional[str]:
    """Путь к файлу модели сна или None."""
    st = load_settings()
    name = st.get("sleep_model", "")
    if not name:
        return None
    p = MODELS_DIR / name
    if not p.exists():
        return None
    return str(p)
