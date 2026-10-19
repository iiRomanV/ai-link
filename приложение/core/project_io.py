import json
from pathlib import Path
from typing import Dict, Any

def export_project(task: str, project_dir: str, research_enabled: bool, max_attempts: int, filepath: str):
    """
    Сохраняет параметры проекта в JSON-файл.
    """
    data = {
        "task": task,
        "project_dir": project_dir,
        "research_enabled": research_enabled,
        "max_attempts": max_attempts
    }
    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def import_project(filepath: str) -> Dict[str, Any]:
    """
    Загружает параметры проекта из JSON-файла.
    Возвращает словарь с ключами task, project_dir, research_enabled, max_attempts.
    """
    with open(filepath, 'r', encoding='utf-8') as f:
        data = json.load(f)
    # Проверяем обязательные ключи
    required = ['task', 'project_dir', 'research_enabled', 'max_attempts']
    for key in required:
        if key not in data:
            raise KeyError(f"Отсутствует ключ '{key}' в файле проекта")
    return data
