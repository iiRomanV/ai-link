"""Утилита: убить висящие Chromium от наших профилей + чистить Singleton.

Вызывается при старте приложения (main.py) и при выходе
(ui/main_window.py closeEvent).

Не задевает сторонние процессы: pkill ищет только Chromium,
у которых в командной строке есть путь к нашим профилям
(~/.ai_link/browser_profiles/). Чужие Chromium с другим профилем
не подпадают под фильтр.
"""
import logging
import subprocess
import time
from pathlib import Path

logger = logging.getLogger(__name__)


def cleanup_stale_browsers() -> dict:
    """Убивает висящие Chromium от наших профилей + чистит Singleton.

    Возвращает: {"killed": bool, "cleaned": int}
    """
    profiles_dir = Path.home() / ".ai_link" / "browser_profiles"

    # 1) Убиваем висящие Chromium только от наших профилей.
    killed = False
    try:
        r = subprocess.run(
            ["pkill", "-9", "-f", str(profiles_dir)],
            timeout=5,
            capture_output=True,
        )
        if r.returncode == 0:
            killed = True
            time.sleep(0.5)  # дать ОС прибрать процессы
    except Exception as e:
        logger.debug("cleanup_stale_browsers: pkill не сработал: %s", e)

    # 2) Чистим файлы-замки Singleton (остаются после kill -9).
    cleaned = 0
    if profiles_dir.exists():
        for profile in profiles_dir.iterdir():
            if not profile.is_dir():
                continue
            for pattern in ("SingletonCookie", "SingletonLock",
                            "SingletonSocket"):
                f = profile / pattern
                try:
                    if f.is_symlink() or f.exists():
                        f.unlink()
                        cleaned += 1
                except Exception:
                    pass

    if killed or cleaned:
        logger.info(
            "cleanup_stale_browsers: убито=%s, Singleton удалено=%s",
            killed, cleaned,
        )
    return {"killed": killed, "cleaned": cleaned}


# ─────────────────────────────────────────────────────────────
#  BROWSER-CACHE-CLEANUP: чистка безопасного кэша профилей
# ─────────────────────────────────────────────────────────────

# Что чистим (только кэш — не трогаем сессии и логины):
_SAFE_CACHE_DIRS = (
    "Cache",
    "Code Cache",
    "GPUCache",
    "DawnGraphiteCache",
    "DawnWebGPUCache",
    "GrShaderCache",
    "ShaderCache",
)

# Вложенные пути (внутри Default/ или сразу в профиле):
_SAFE_NESTED = (
    ("Service Worker", "CacheStorage"),
    ("Service Worker", "ScriptCache"),
)


def clean_browser_cache(profile_name: str = None,
                         safe: bool = True) -> dict:
    """Чистит БЕЗОПАСНЫЙ кэш (Cache, Code Cache, GPUCache,
    Service Worker CacheStorage/ScriptCache).

    НЕ ТРОГАЕТ:
      • Cookies / Cookies-journal
      • Local Storage, Session Storage, IndexedDB
      • Login Data, Sessions, Bookmarks, Preferences
      • History, Web Data, Account Web Data

    Аргументы:
      profile_name — если задан, чистим только этот профиль
                     (например, "qwen_profile"). Иначе — все.
      safe — True: только безопасные папки; False: ничего лишнего
             (всё равно НЕ трогает Cookies и т.п.)

    Возвращает:
      {"cleaned_dirs": N, "freed_bytes": M, "profiles": [names],
       "errors": [str]}
    """
    import shutil as _sh

    profiles_dir = Path.home() / ".ai_link" / "browser_profiles"
    if not profiles_dir.exists():
        return {"cleaned_dirs": 0, "freed_bytes": 0,
                "profiles": [], "errors": ["профили не найдены"]}

    def _dir_size(d: Path) -> int:
        total = 0
        try:
            for f in d.rglob("*"):
                if f.is_file():
                    try:
                        total += f.stat().st_size
                    except Exception:
                        pass
        except Exception:
            pass
        return total

    targets: list = []
    if profile_name:
        p = profiles_dir / profile_name
        if p.is_dir():
            targets = [p]
    else:
        targets = [p for p in profiles_dir.iterdir() if p.is_dir()]

    cleaned_dirs = 0
    freed_bytes = 0
    errors: list = []
    touched_profiles: list = []

    for prof in targets:
        # Кэш может быть и в корне профиля, и в Default/
        bases = [prof, prof / "Default"]
        for base in bases:
            if not base.is_dir():
                continue
            # Прямые кэш-папки
            for name in _SAFE_CACHE_DIRS:
                d = base / name
                if not d.is_dir():
                    continue
                try:
                    sz = _dir_size(d)
                    _sh.rmtree(d, ignore_errors=True)
                    cleaned_dirs += 1
                    freed_bytes += sz
                except Exception as e:
                    errors.append(f"{d}: {e}")
            # Вложенные
            for parts in _SAFE_NESTED:
                d = base
                for part in parts:
                    d = d / part
                if not d.is_dir():
                    continue
                try:
                    sz = _dir_size(d)
                    _sh.rmtree(d, ignore_errors=True)
                    cleaned_dirs += 1
                    freed_bytes += sz
                except Exception as e:
                    errors.append(f"{d}: {e}")
        touched_profiles.append(prof.name)

    logger.info(
        "clean_browser_cache: dirs=%s, freed=%.1f MB, profiles=%s",
        cleaned_dirs, freed_bytes / (1024 * 1024), touched_profiles,
    )

    return {
        "cleaned_dirs": cleaned_dirs,
        "freed_bytes": freed_bytes,
        "profiles": touched_profiles,
        "errors": errors,
    }
