"""ARCHIVE-EXTRACT (2026-10-08): распаковка архивов чужих проектов.

Поддерживает:
  • .tar.gz / .tgz / .tar / .tar.bz2 / .tar.xz — tarfile (Python)
  • .zip — zipfile (Python)
  • .7z — CLI `7z`
  • .rar — CLI `unrar` (если установлен)

Что делает:
  • Детект архива по расширению.
  • Распаковка в <archive_stem>_extracted/ рядом с архивом.
  • Защита от path traversal (../).
  • Пропускает мусор: __MACOSX, .DS_Store, .git/, node_modules/.
"""

import shutil
import subprocess
import tarfile
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


# Расширения-архивы (по убыванию специфичности).
ARCHIVE_EXTS: Tuple[str, ...] = (
    ".tar.gz", ".tar.bz2", ".tar.xz", ".tgz", ".tbz2", ".txz",
    ".tar", ".zip", ".7z", ".rar",
)

# Что не распаковываем.
_SKIP_PATTERNS = (
    "__MACOSX", ".DS_Store", ".git/", "node_modules/",
    "__pycache__/", ".pytest_cache/",
)


def detect_archive(path: str) -> Optional[str]:
    """Возвращает тип архива (расширение) или None."""
    if not path:
        return None
    p = Path(path)
    if not p.exists() or not p.is_file():
        return None
    lower = p.name.lower()
    for ext in ARCHIVE_EXTS:
        if lower.endswith(ext):
            return ext
    return None


def _is_skipped(rel_path: str) -> bool:
    """True, если файл надо пропустить при распаковке."""
    for pat in _SKIP_PATTERNS:
        if pat in rel_path:
            return True
    return False


def _safe_target(base: Path, member_name: str) -> Optional[Path]:
    """Защита от path traversal: ../ в имени архивной записи."""
    try:
        target = (base / member_name).resolve()
        target.relative_to(base.resolve())
        return target
    except Exception:
        return None


def _extract_tar(archive: Path, dst: Path) -> Dict[str, Any]:
    """Распаковка tar.* через tarfile."""
    extracted = 0
    skipped = 0
    errors: List[str] = []
    try:
        with tarfile.open(str(archive), "r:*") as tf:
            for member in tf.getmembers():
                if _is_skipped(member.name):
                    skipped += 1
                    continue
                target = _safe_target(dst, member.name)
                if target is None:
                    errors.append(f"path traversal: {member.name}")
                    continue
                try:
                    tf.extract(member, str(dst))
                    extracted += 1
                except Exception as e:
                    errors.append(f"{member.name}: {e}")
    except Exception as e:
        return {"ok": False, "error": str(e),
                "extracted": extracted, "skipped": skipped,
                "errors": errors[:10]}
    return {"ok": True, "error": "",
            "extracted": extracted, "skipped": skipped,
            "errors": errors[:10]}


def _extract_zip(archive: Path, dst: Path) -> Dict[str, Any]:
    """Распаковка zip через zipfile."""
    extracted = 0
    skipped = 0
    errors: List[str] = []
    try:
        with zipfile.ZipFile(str(archive), "r") as zf:
            for name in zf.namelist():
                if _is_skipped(name):
                    skipped += 1
                    continue
                target = _safe_target(dst, name)
                if target is None:
                    errors.append(f"path traversal: {name}")
                    continue
                try:
                    zf.extract(name, str(dst))
                    extracted += 1
                except Exception as e:
                    errors.append(f"{name}: {e}")
    except Exception as e:
        return {"ok": False, "error": str(e),
                "extracted": extracted, "skipped": skipped,
                "errors": errors[:10]}
    return {"ok": True, "error": "",
            "extracted": extracted, "skipped": skipped,
            "errors": errors[:10]}


def _extract_7z(archive: Path, dst: Path) -> Dict[str, Any]:
    """Распаковка через 7z CLI (p7zip-full)."""
    exe = shutil.which("7z") or shutil.which("7za")
    if not exe:
        return {"ok": False, "error": "7z не установлен (p7zip-full)",
                "extracted": 0, "skipped": 0, "errors": []}
    try:
        r = subprocess.run(
            [exe, "x", str(archive), f"-o{dst}", "-y",
             "-x!__MACOSX", "-x!.DS_Store", "-x!.git"],
            capture_output=True, text=True, timeout=600,
        )
        if r.returncode != 0:
            return {"ok": False, "error": (r.stderr or r.stdout)[:300],
                    "extracted": 0, "skipped": 0, "errors": []}
        return {"ok": True, "error": "", "extracted": 0,
                "skipped": 0, "errors": []}
    except Exception as e:
        return {"ok": False, "error": str(e),
                "extracted": 0, "skipped": 0, "errors": []}


def _extract_rar(archive: Path, dst: Path) -> Dict[str, Any]:
    """Распаковка через unrar CLI."""
    exe = shutil.which("unrar")
    if not exe:
        return {"ok": False,
                "error": "unrar не установлен. Установите: sudo apt install unrar",
                "extracted": 0, "skipped": 0, "errors": []}
    try:
        r = subprocess.run(
            [exe, "x", "-y", str(archive), f"{dst}/"],
            capture_output=True, text=True, timeout=600,
        )
        if r.returncode != 0:
            return {"ok": False, "error": (r.stderr or r.stdout)[:300],
                    "extracted": 0, "skipped": 0, "errors": []}
        return {"ok": True, "error": "", "extracted": 0,
                "skipped": 0, "errors": []}
    except Exception as e:
        return {"ok": False, "error": str(e),
                "extracted": 0, "skipped": 0, "errors": []}


def extract_archive(archive_path: str,
                    dst_dir: Optional[str] = None) -> Dict[str, Any]:
    """Распаковывает архив.

    По умолчанию dst = <archive_stem>_extracted/ рядом с архивом.

    Возвращает:
      {"ok": bool, "extracted_to": "...", "type": ".tar.gz",
       "extracted": N, "skipped": N, "error": "...", "errors": [...]}
    """
    src = Path(archive_path).resolve()
    ext = detect_archive(str(src))
    if ext is None:
        return {"ok": False, "extracted_to": "",
                "type": "", "extracted": 0, "skipped": 0,
                "error": f"не архив: {src.name}", "errors": []}

    if dst_dir:
        dst = Path(dst_dir).resolve()
    else:
        # Имя без расширения.
        stem = src.name
        for e in ARCHIVE_EXTS:
            if stem.lower().endswith(e):
                stem = stem[:-len(e)]
                break
        dst = src.parent / f"{stem}_extracted"

    if dst.exists():
        return {"ok": False, "extracted_to": str(dst),
                "type": ext, "extracted": 0, "skipped": 0,
                "error": f"папка уже существует: {dst}", "errors": []}

    try:
        dst.mkdir(parents=True, exist_ok=False)
    except Exception as e:
        return {"ok": False, "extracted_to": "",
                "type": ext, "extracted": 0, "skipped": 0,
                "error": f"mkdir: {e}", "errors": []}

    if ext.startswith(".tar") or ext in (".tgz", ".tbz2", ".txz"):
        result = _extract_tar(src, dst)
    elif ext == ".zip":
        result = _extract_zip(src, dst)
    elif ext == ".7z":
        result = _extract_7z(src, dst)
    elif ext == ".rar":
        result = _extract_rar(src, dst)
    else:
        result = {"ok": False, "error": f"неизвестный формат: {ext}",
                  "extracted": 0, "skipped": 0, "errors": []}

    return {
        "ok": result.get("ok", False),
        "extracted_to": str(dst),
        "type": ext,
        "extracted": result.get("extracted", 0),
        "skipped": result.get("skipped", 0),
        "error": result.get("error", ""),
        "errors": result.get("errors", []),
    }


def find_archives_in(folder: str) -> List[str]:
    """Находит все архивы в папке (top-level)."""
    p = Path(folder)
    if not p.exists() or not p.is_dir():
        return []
    found: List[str] = []
    for f in p.iterdir():
        if f.is_file() and detect_archive(str(f)):
            found.append(str(f))
    return found
