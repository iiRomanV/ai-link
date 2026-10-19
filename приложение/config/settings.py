"""Настройки AI Link.

Пути (DB_PATH, LOG_DIR, AI_LINK_DATA_DIR) — константы, они фиксированы.
Настройки (форг, режим, research, попытки) — ФУНКЦИИ, чтобы читать
свежие значения из config.json при каждом обращении.
"""

import os
from pathlib import Path
from config.config_manager import get_setting

# ============ ПУТИ (константы) ============

AI_LINK_DATA_DIR = Path.home() / ".ai_link"
AI_LINK_DATA_DIR.mkdir(exist_ok=True)

LOG_DIR = AI_LINK_DATA_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)

DB_PATH = AI_LINK_DATA_DIR / "ai_link.db"

# ============ НАСТРОЙКИ (функции, читают свежее) ============



def get_app_mode() -> str:
    """Режим работы: minimal / medium / maximal."""
    return os.getenv("AI_LINK_MODE", get_setting("app_mode", "minimal"))


def get_research_enabled() -> bool:
    """Включён ли research-узел."""
    return bool(get_setting("research_enabled", True))


def get_research_engine() -> str:
    """Движок research: duckduckgo / ..."""
    return get_setting("research_engine", "duckduckgo")


def get_max_attempts() -> int:
    """Максимальное число попыток выполнения."""
    return int(get_setting("max_attempts", 1))
