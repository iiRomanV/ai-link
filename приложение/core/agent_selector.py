"""
Выбор оптимального агента для задачи.
Использует профили и историю выполнения.
"""

from typing import Optional
from core.agent_profiles import (
    get_agent_profile,
    get_max_actions,
    is_good_for_large_project,
)
from core.task_history import find_similar_tasks, get_agent_stats


def estimate_complexity(task: str) -> str:
    """
    Оценивает сложность задачи: 'low', 'medium' или 'high'.
    """
    if not task:
        return "low"
    task_lower = task.lower()

    high_keywords = [
        "приложение", "программу", "систему", "сайт", "сервис",
        "проект", "с нуля", "разработай", "спроектируй",
        "несколько файлов", "архитектуру"
    ]
    medium_keywords = [
        "модуль", "класс", "функцию", "скрипт",
        "исправь", "добавь", "обнови"
    ]

    if any(kw in task_lower for kw in high_keywords):
        return "high"
    if any(kw in task_lower for kw in medium_keywords):
        return "medium"
    return "low"


def select_best_agent(task: str, enabled_agents: list) -> str:
    """
    Выбирает лучшего агента для задачи.
    enabled_agents — список включённых агентов (объекты с 'id').
    """
    if not enabled_agents:
        return "gigachat"

    enabled_ids = [a.get("id", "").lower() for a in enabled_agents]
    complexity = estimate_complexity(task)

    # 1. Ищем похожие задачи в истории
    similar = find_similar_tasks(task, limit=10)
    if similar:
        # Берём самого успешного агента из похожих задач
        agent_scores = {}
        for entry in similar:
            agent = entry.get("agent", "").lower()
            if agent not in enabled_ids:
                continue
            score = agent_scores.get(agent, 0)
            if entry.get("success"):
                score += 1
            agent_scores[agent] = score
        if agent_scores:
            best = max(agent_scores, key=agent_scores.get)
            if agent_scores[best] > 0:
                return best

    # 2. Для сложных задач ищем агента с поддержкой больших проектов
    if complexity == "high":
        for agent_id in enabled_ids:
            if is_good_for_large_project(agent_id):
                return agent_id
        # Если нет — берём агента с максимальным max_actions
        best_id = enabled_ids[0]
        best_max = 0
        for agent_id in enabled_ids:
            m = get_max_actions(agent_id)
            if m > best_max:
                best_max = m
                best_id = agent_id
        return best_id

    # 3. Для простых задач — берём первого включённого
    return enabled_ids[0]


def explain_choice(task: str, agent_id: str) -> str:
    """Возвращает текстовое объяснение, почему выбран агент."""
    profile = get_agent_profile(agent_id)
    if not profile:
        return f"Выбран агент {agent_id} по умолчанию."
    complexity = estimate_complexity(task)
    name = profile.get("name", agent_id)
    return f"Выбран {name} (сложность задачи: {complexity})."


if __name__ == "__main__":
    # Быстрый тест
    test_agents = [
        {"id": "gigachat", "enabled": True},
        {"id": "ollama", "enabled": True},
    ]
    print(select_best_agent("создай файл hello.txt", test_agents))
