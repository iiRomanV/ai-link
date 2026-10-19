"""
Модуль памяти AI Link.
Обеспечивает глобальную и проектную память.
"""

import json
import os
from pathlib import Path
from typing import Dict, Any, Optional

GLOBAL_MEMORY_DIR = Path.home() / ".ai_link" / "brain"

DEFAULT_GLOBAL_RULES = {
    "name": "AI Link",
    "role": "Настольный агент-разработчик",
    "components": {
        "Link": "Мозг: маршрутизация, планирование, память, проверка",
        "HandLink": "Руки: файлы, команды, проверки"
    },
    "rules": [
        "Всегда требовать JSON-ответ от агентов при выполнении проектных задач",
        "Проверять существование файлов после создания",
        "Не выполнять команды с rm -rf, sudo, mkfs, shutdown, reboot",
        "Не выходить за пределы project_dir"
    ]
}

def _ensure_global_dir() -> Path:
    GLOBAL_MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    return GLOBAL_MEMORY_DIR

def load_global_memory() -> Dict[str, Any]:
    """Загружает глобальную память. Если файла нет, создаёт с правилами по умолчанию."""
    _ensure_global_dir()
    file = GLOBAL_MEMORY_DIR / "system_rules.json"
    if file.exists():
        try:
            with open(file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    save_global_memory(DEFAULT_GLOBAL_RULES)
    return DEFAULT_GLOBAL_RULES.copy()

def save_global_memory(data: Dict[str, Any]):
    """Сохраняет глобальную память в файл."""
    _ensure_global_dir()
    file = GLOBAL_MEMORY_DIR / "system_rules.json"
    with open(file, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def load_project_memory(project_dir: str) -> Dict[str, Any]:
    """Загружает или создаёт проектную память в <project_dir>/.ai_link/project.json."""
    memory_dir = Path(project_dir) / ".ai_link"
    memory_dir.mkdir(parents=True, exist_ok=True)
    file = memory_dir / "project.json"
    if file.exists():
        try:
            with open(file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    data = {
        "project_dir": str(project_dir),
        "task": "",
        "agent_mode": "",
        "plan": [],
        "history": []
    }
    save_project_memory(project_dir, data)
    return data

def save_project_memory(project_dir: str, data: Dict[str, Any]):
    """Сохраняет проектную память."""
    memory_dir = Path(project_dir) / ".ai_link"
    memory_dir.mkdir(parents=True, exist_ok=True)
    file = memory_dir / "project.json"
    with open(file, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def append_project_history(project_dir: str, entry: Dict[str, Any]):
    """Добавляет запись в историю проекта (project.json, ключ history)."""
    data = load_project_memory(project_dir)
    data.setdefault("history", []).append(entry)
    save_project_memory(project_dir, data)

def get_global_rule(rule_text: str) -> Optional[str]:
    """Ищет правило в глобальной памяти."""
    memory = load_global_memory()
    for rule in memory.get("rules", []):
        if rule_text in rule:
            return rule