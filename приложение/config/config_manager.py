import json
from pathlib import Path
from typing import Any, Dict

CONFIG_FILE = Path.home() / ".ai_link" / "config.json"

DEFAULT_CONFIG = {
    "research_enabled": True,
    "research_engine": "duckduckgo",
    "max_attempts": 1,
    "app_mode": "minimal",
    "use_firejail": False,
    "warmup_on_start": True,
    "minimize_browser": True,
    "debug_logs": False,
    # Б77 (16.10.2026): браузеры в Xephyr :99 — не перехватывают
    # фокус. True по умолчанию. False → старое поведение.
    "use_xephyr": True,
    # FIX MEMORY-V1 (2026-09-30): турбо-память (SDR + semantic)
    # Требует 16+ ГБ RAM. По умолчанию выключено.
    "turbo_memory": False,
    # Порог включения турбо (ГБ), для UI-предупреждения
    "turbo_memory_ram_threshold": 16,
    # A3 (2026-10-07): имя пользователя. Используется в plan.json
    # (created_by_user), чтобы различать свои и чужие проекты.
    "user_name": "",
}

def load_config() -> Dict[str, Any]:
    """Загружает конфигурацию из файла. Если файла нет, создаёт с настройками по умолчанию."""
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                config = json.load(f)
            # Объединяем с дефолтными, чтобы добавить отсутствующие ключи
            merged = DEFAULT_CONFIG.copy()
            merged.update(config)
            return merged
        except Exception:
            return DEFAULT_CONFIG.copy()
    else:
        save_config(DEFAULT_CONFIG)
        return DEFAULT_CONFIG.copy()

def save_config(config: Dict[str, Any]):
    """Сохраняет конфигурацию в файл."""
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
        json.dump(config, f, ensure_ascii=False, indent=2)

def get_setting(key: str, default=None):
    """Возвращает значение настройки по ключу."""
    config = load_config()
    return config.get(key, default)

def set_setting(key: str, value: Any):
    """Устанавливает значение настройки и сохраняет."""
    config = load_config()
    config[key] = value
    save_config(config)
