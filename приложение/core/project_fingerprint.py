"""Б63 Уровень A (14.10.2026): отпечаток проекта.

Считает per-file sha256 для отслеживаемых файлов.
Хранит снимок в .ai_link/project_snapshot.json.

Отпечаток нужен, чтобы:
  • понять, изменился ли проект с прошлого прогона;
  • если изменился — пометить историю как устаревшую
    (events.jsonl и review_log.jsonl в архив, done → needs_recheck).

Совет Perplexity: не общий хеш, а per-file map {path: sha256}.
Плюс git state (HEAD + status) — если есть .git.
"""
import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Tuple

import logging

logger = logging.getLogger(__name__)

# Расширения, которые отслеживаем
_TRACKED_EXTS = {
    ".py", ".json", ".yaml", ".yml", ".toml", ".cfg", ".ini", ".md",
}

# Папки, которые НЕ сканируем
_SKIP_DIRS = {
    ".venv", "venv", "env", "__pycache__", ".git",
    "node_modules", ".pytest_cache", ".ai_link",
    ".diagnostics", ".mypy_cache", ".ruff_cache",
    ".claude", ".cursor", ".scavenger",
}

_MAX_FILES = 5000


def _snapshot_path(project_dir: str) -> Path:
    return Path(project_dir) / ".ai_link" / "project_snapshot.json"


def _hash_file(path: Path) -> str:
    """sha256 содержимого. Нормализация line endings."""
    try:
        raw = path.read_bytes()
        # Нормализуем CRLF → LF
        raw = raw.replace(b"\r\n", b"\n")
        return hashlib.sha256(raw).hexdigest()
    except Exception:
        return ""


def _scan_files(project_dir: str) -> Dict[str, str]:
    """Возвращает {rel_path: sha256} для отслеживаемых файлов."""
    p = Path(project_dir).resolve()
    if not p.exists():
        return {}

    result: Dict[str, str] = {}
    try:
        for f in sorted(p.rglob("*")):
            if not f.is_file():
                continue
            rel_parts = f.relative_to(p).parts
            if any(part in _SKIP_DIRS for part in rel_parts):
                continue
            if f.suffix.lower() not in _TRACKED_EXTS:
                continue
            rel_str = str(f.relative_to(p))
            h = _hash_file(f)
            if h:
                result[rel_str] = h
            if len(result) > _MAX_FILES:
                logger.warning(
                    "PROJECT-FP: больше %d файлов — обрезано", _MAX_FILES
                )
                break
    except Exception as e:
        logger.warning("PROJECT-FP: ошибка обхода: %s", e)
    return result


def _git_state(project_dir: str) -> Dict[str, Any]:
    """Снимает git HEAD и статус. Пустой dict, если git нет."""
    import subprocess
    p = Path(project_dir)
    if not (p / ".git").exists():
        return {}
    try:
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(p), capture_output=True, text=True, timeout=5,
        )
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=str(p), capture_output=True, text=True, timeout=5,
        )
        return {
            "head": (head.stdout or "").strip()[:40],
            "dirty": bool((status.stdout or "").strip()),
        }
    except Exception:
        return {}


def compute_root_hash(files: Dict[str, str]) -> str:
    """Общий Merkle-style root hash от per-file map."""
    if not files:
        return ""
    parts = [f"{k}:{v}" for k, v in sorted(files.items())]
    combined = "\n".join(parts)
    return hashlib.sha256(combined.encode("utf-8")).hexdigest()


def compute(project_dir: str) -> Dict[str, Any]:
    """Полный snapshot: {schema_version, created_at, git, files, root_hash}."""
    files = _scan_files(project_dir)
    return {
        "schema_version": 2,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "git": _git_state(project_dir),
        "files": files,
        "root_hash": compute_root_hash(files),
        "py_count": sum(1 for k in files if k.endswith(".py")),
    }


def save(project_dir: str, snapshot: Dict[str, Any]) -> None:
    """Сохраняет snapshot в .ai_link/project_snapshot.json."""
    path = _snapshot_path(project_dir)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(snapshot, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as e:
        logger.warning("PROJECT-FP: не сохранить: %s", e)


def load(project_dir: str) -> Dict[str, Any]:
    """Читает snapshot ({} если нет)."""
    path = _snapshot_path(project_dir)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def compare(project_dir: str) -> Dict[str, Any]:
    """Сравнивает текущий snapshot с сохранённым.

    Возвращает:
      changed: bool      — отличается ли
      first_run: bool    — не было сохранённого
      root_hash: str     — текущий
      saved_root: str    — сохранённый
      added: list        — новые файлы
      removed: list      — удалённые
      modified: list     — изменённые
      git_rolled_back: bool — если HEAD изменился или git status стал чистым
                              (потенциальный откат)
    """
    current = compute(project_dir)
    saved = load(project_dir)

    if not saved or not saved.get("root_hash"):
        return {
            "changed": False, "first_run": True,
            "root_hash": current["root_hash"], "saved_root": "",
            "added": [], "removed": [], "modified": [],
            "git_rolled_back": False,
            "snapshot": current,
        }

    cur_files = current.get("files", {})
    old_files = saved.get("files", {})

    added = sorted(set(cur_files) - set(old_files))
    removed = sorted(set(old_files) - set(cur_files))
    modified = sorted(
        k for k in (set(cur_files) & set(old_files))
        if cur_files[k] != old_files[k]
    )

    # Git: если HEAD был и стал другим — вероятен откат
    git_rolled_back = False
    cur_git = current.get("git", {})
    old_git = saved.get("git", {})
    if cur_git.get("head") and old_git.get("head"):
        if cur_git["head"] != old_git["head"]:
            git_rolled_back = True

    changed = bool(added or removed or modified) or git_rolled_back

    return {
        "changed": changed,
        "first_run": False,
        "root_hash": current["root_hash"],
        "saved_root": saved["root_hash"],
        "added": added,
        "removed": removed,
        "modified": modified,
        "git_rolled_back": git_rolled_back,
        "snapshot": current,
    }
