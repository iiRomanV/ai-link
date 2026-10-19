"""
Планировщик AI Link.
Разбивает задачу пользователя на шаги.
"""

import re
from typing import List, Dict, Any


def generate_plan(task: str) -> List[Dict[str, Any]]:
    """
    Генерирует простой план на основе задачи.
    Возвращает список шагов вида:
    [
        {"step": 1, "action": "описание шага", "status": "pending"},
        ...
    ]
    """
    if not task or not task.strip():
        return []

    task_lower = task.lower()

    # Признаки того, что задача — про приложение или большой проект
    is_application = any(kw in task_lower for kw in [
        "приложение", "программу", "проект", "систему",
        "сайт", "сервис", "бота", "бота", "игру"
    ])

    steps: List[Dict[str, Any]] = []

    if is_application:
        steps = [
            {"step": 1, "action": "Определить структуру проекта (папки, файлы)", "status": "pending"},
            {"step": 2, "action": "Создать основной файл запуска (main.py или index.html)", "status": "pending"},
            {"step": 3, "action": "Создать вспомогательные модули", "status": "pending"},
            {"step": 4, "action": "Создать файл зависимостей (requirements.txt или package.json)", "status": "pending"},
            {"step": 5, "action": "Проверить запуск и исправить ошибки", "status": "pending"}
        ]
    else:
        # Для простых задач — один шаг
        steps = [
            {"step": 1, "action": f"Выполнить задачу: {task}", "status": "pending"}
        ]

    return steps


def format_plan_for_display(plan: List[Dict[str, Any]]) -> str:
    """Форматирует план в читаемый текст."""
    if not plan:
        return "(план пуст)"
    lines = []
    for item in plan:
        status_mark = {
            "pending": "⏳",
            "in_progress": "🔄",
            "done": "✅",
            "failed": "❌"
        }.get(item.get("status", "pending"), "⏳")
        lines.append(f"{status_mark} Шаг {item['step']}: {item['action']}")
    return "\n".join(lines)


if __name__ == "__main__":
    # Быстрый тест
    plan = generate_plan("создай приложение для учёта расходов")
    print(format_plan_for_display(plan))
    print("---")
    plan2 = generate_plan("создай файл hello.txt")
    print(format_plan_for_display(plan2))
