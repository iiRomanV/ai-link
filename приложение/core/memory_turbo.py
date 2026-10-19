"""MEMORY-TURBO — турбо-режим памяти (SDR + semantic).

FIX MEMORY-V1 (2026-09-30):
  Пока это ЗАГЛУШКА. Реализация SDR — в следующей сессии.
  Задача сейчас:
    • Определить RAM
    • Проверить, разрешён ли турбо (config.turbo_memory)
    • Дать UI информацию для галочки
    • Не падать, если ресурсов мало

API:
  get_ram_gb()             → float (всего RAM)
  get_available_ram_gb()   → float (доступно сейчас)
  is_turbo_supported()     → bool (>= 16 ГБ)
  is_turbo_enabled()       → bool (config + supported)
  can_enable_turbo()       → (bool, str) — можно ли включить + причина
  turbo_info()             → Dict со всей инфой для UI
  sdr_search()             → заглушка (позже)
"""
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

# Порог по умолчанию — 16 ГБ
DEFAULT_THRESHOLD_GB = 16.0


def get_ram_gb() -> float:
    """Всего RAM в ГБ (из /proc/meminfo)."""
    try:
        with open("/proc/meminfo", "r") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    kb = int(line.split()[1])
                    return kb / 1024 / 1024
    except Exception:
        pass
    return 0.0


def get_available_ram_gb() -> float:
    """Доступно RAM в ГБ (MemAvailable)."""
    try:
        with open("/proc/meminfo", "r") as f:
            for line in f:
                if line.startswith("MemAvailable:"):
                    kb = int(line.split()[1])
                    return kb / 1024 / 1024
    except Exception:
        pass
    return 0.0


def _get_threshold_gb() -> float:
    """Порог из config или дефолт."""
    try:
        from config.config_manager import load_config
        cfg = load_config()
        v = cfg.get("turbo_memory_ram_threshold")
        if isinstance(v, (int, float)) and v > 0:
            return float(v)
    except Exception:
        pass
    return DEFAULT_THRESHOLD_GB


def is_turbo_supported() -> bool:
    """True, если RAM >= порога."""
    return get_ram_gb() >= _get_threshold_gb()


def is_turbo_enabled() -> bool:
    """True, если турбо включён в config И поддерживается."""
    try:
        from config.config_manager import load_config
        cfg = load_config()
        if not cfg.get("turbo_memory", False):
            return False
    except Exception:
        return False
    return is_turbo_supported()


def can_enable_turbo() -> Tuple[bool, str]:
    """Можно ли включить турбо. Возвращает (ok, reason)."""
    total = get_ram_gb()
    threshold = _get_threshold_gb()
    if total >= threshold:
        return (True, "")
    return (
        False,
        f"Недостаточно RAM: {total:.1f} ГБ "
        f"(нужно {threshold:.0f}+ ГБ). "
        f"Турбо-режим будет недоступен, но базовая память работает."
    )


def turbo_info() -> Dict[str, Any]:
    """Полная информация для UI."""
    total = get_ram_gb()
    available = get_available_ram_gb()
    threshold = _get_threshold_gb()
    supported = total >= threshold
    enabled = is_turbo_enabled()
    ok, reason = can_enable_turbo()
    return {
        "ram_total_gb": round(total, 1),
        "ram_available_gb": round(available, 1),
        "threshold_gb": threshold,
        "supported": supported,
        "enabled": enabled,
        "can_enable": ok,
        "reason": reason,
    }


def sdr_search(query: str, limit: int = 10,
               db_path: Optional[Path] = None) -> list:
    """Семантический поиск через ONNX MiniLM.

    FIX MEMORY-SEMANTIC (2026-09-30): реальная реализация.
    Возвращает список [{memory_id, score, model}].

    ⚠️ Модель English-only! Английские термины — работает,
       русские запросы — мусор (см. memory_semantic.py).
    """
    if not is_turbo_enabled():
        return []
    try:
        from core import memory_semantic as sem
        if not sem.is_available():
            return []
        return sem.find_semantic(query, limit=limit,
                                 db_path=db_path)
    except Exception:
        return []
