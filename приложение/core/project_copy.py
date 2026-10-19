"""PROJECT-COPY (2026-10-08): копия чужого проекта для аудита.

Создаёт C (source-копия) рядом с B (оригинал) перед аудитом.
Смысл: работаем в B, но есть точка отката.

Что СОХРАНЯЕТСЯ:
  • .env, .env.example, .gitignore
  • requirements.txt, pyproject.toml, package.json
  • README.md, LICENSE
  • .ai_link/ (наша память, если есть)

Что ИГНОРИРУЕТСЯ:
  • .git/ (история)
  • __pycache__/, *.pyc
  • .venv/, venv/, env/
  • node_modules/
  • .pytest_cache/, .mypy_cache/, .ruff_cache/
  • dist/, build/, target/
  • *.log, *.tmp
"""

import getpass
import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List


# Папки, которые НЕ копируем.
_SKIP_DIRS = {
    ".git", "__pycache__", ".pytest_cache", ".mypy_cache",
    ".ruff_cache", ".tox", ".eggs", ".cache",
    ".venv", "venv", "env", ".env",
    "node_modules", "dist", "build", "target",
    ".idea", ".vscode", ".DS_Store",
}

# Расширения, которые НЕ копируем (мусор).
_SKIP_EXTS = {".pyc", ".pyo", ".pyd", ".log", ".tmp", ".swp", ".swo"}


def _dir_skipped(name: str) -> bool:
    """True, если папку пропускаем."""
    return name in _SKIP_DIRS


def _file_skipped(name: str) -> bool:
    """True, если файл пропускаем (по расширению)."""
    if not name:
        return False
    lower = name.lower()
    for ext in _SKIP_EXTS:
        if lower.endswith(ext):
            return True
    return False


def _write_identity(dst: Path, source: Path, user_name: str) -> None:
    """Пишет .ai_link/IDENTITY в копии.

    Это маркер: «эта папка — копия для отката, создана Link».
    """
    try:
        ai_dir = dst / ".ai_link"
        ai_dir.mkdir(parents=True, exist_ok=True)
        ident = ai_dir / "IDENTITY"
        content = (
            f"created_by: link\n"
            f"user: {user_name or getpass.getuser()}\n"
            f"created_at: {datetime.now().isoformat(timespec='seconds')}\n"
            f"source: {source}\n"
        )
        ident.write_text(content, encoding="utf-8")
    except Exception:
        pass


def copy_project_for_audit(source_dir: str,
                            user_name: str = "") -> Dict[str, Any]:
    """Создаёт копию source_dir рядом с ним.

    Имя копии: <source_name>_source_YYYYMMDD_HHMMSS/

    Возвращает:
      {"ok": True, "copy_path": "...", "copied": N,
       "skipped": M, "errors": [...]}
      или
      {"ok": False, "copy_path": "", "error": "..."}
    """
    src = Path(source_dir).resolve()
    if not src.exists() or not src.is_dir():
        return {
            "ok": False, "copy_path": "",
            "copied": 0, "skipped": 0,
            "error": f"папка не найдена: {src}",
        }

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    dst = src.parent / f"{src.name}_source_{ts}"

    if dst.exists():
        return {
            "ok": False, "copy_path": "",
            "copied": 0, "skipped": 0,
            "error": f"копия уже существует: {dst}",
        }

    copied = 0
    skipped = 0
    errors: List[str] = []

    try:
        dst.mkdir(parents=True, exist_ok=False)
    except Exception as e:
        return {
            "ok": False, "copy_path": "",
            "copied": 0, "skipped": 0,
            "error": f"не удалось создать папку: {e}",
        }

    try:
        for root, dirs, files in os.walk(src):
            root_p = Path(root)
            rel_root = root_p.relative_to(src)

            # Фильтруем папки (in-place — важно для os.walk).
            dirs[:] = [d for d in dirs if not _dir_skipped(d)]

            # Создаём папку в копии.
            dst_root = dst / rel_root
            try:
                dst_root.mkdir(parents=True, exist_ok=True)
            except Exception as e:
                errors.append(f"{rel_root}: mkdir {e}")
                continue

            for f in files:
                if _file_skipped(f):
                    skipped += 1
                    continue
                src_f = root_p / f
                dst_f = dst_root / f
                try:
                    shutil.copy2(str(src_f), str(dst_f))
                    copied += 1
                except Exception as e:
                    errors.append(f"{rel_root / f}: {e}")

        # Пишем IDENTITY в копию.
        _write_identity(dst, src, user_name)

        return {
            "ok": True,
            "copy_path": str(dst),
            "copied": copied,
            "skipped": skipped,
            "errors": errors[:10],
        }
    except Exception as e:
        return {
            "ok": False, "copy_path": str(dst),
            "copied": copied, "skipped": skipped,
            "error": str(e),
        }


def is_our_copy(project_dir: str) -> bool:
    """True, если папка — наша копия (есть .ai_link/IDENTITY)."""
    p = Path(project_dir) / ".ai_link" / "IDENTITY"
    return p.exists() and p.is_file()
