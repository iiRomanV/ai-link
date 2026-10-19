import json
from pathlib import Path
from typing import Dict, List, Optional, Any

AGENTS_FILE = Path.home() / ".ai_link" / "agents.json"

DEFAULT_AGENTS = [
    {
        "id": "gigachat",
        "name": "GigaChat (Sber API)",
        "type": "api",
        "enabled": False,
        "config": {
            "model": "GigaChat:latest"
        }
    }
]

def load_agents() -> List[Dict[str, Any]]:
    """Загружает список агентов из файла. Если файла нет, создаёт с агентом по умолчанию."""
    if AGENTS_FILE.exists():
        try:
            with open(AGENTS_FILE, 'r', encoding='utf-8') as f:
                agents = json.load(f)
            # Убедимся, что есть хотя бы один агент, иначе вернём дефолтного
            if not agents:
                agents = DEFAULT_AGENTS.copy()
            # Нормализация id к нижнему регистру
            for agent in agents:
                if "id" in agent and isinstance(agent["id"], str):
                    agent["id"] = agent["id"].lower()
            return agents
        except Exception:
            return DEFAULT_AGENTS.copy()
    else:
        save_agents(DEFAULT_AGENTS)
        return DEFAULT_AGENTS.copy()

def save_agents(agents: List[Dict[str, Any]]):
    """Сохраняет список агентов в файл."""
    AGENTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(AGENTS_FILE, 'w', encoding='utf-8') as f:
        json.dump(agents, f, ensure_ascii=False, indent=2)

def get_agent(agent_id: str) -> Optional[Dict[str, Any]]:
    """Возвращает агента по идентификатору или None."""
    for agent in load_agents():
        if agent["id"] == agent_id:
            return agent
    return None

def set_agent_enabled(agent_id: str, enabled: bool):
    """Включает или отключает агента."""
    agents = load_agents()
    for agent in agents:
        if agent["id"] == agent_id:
            agent["enabled"] = enabled
            break
    save_agents(agents)

def update_agent_config(agent_id: str, config: Dict[str, Any]):
    """Обновляет конфигурацию агента."""
    agents = load_agents()
    for agent in agents:
        if agent["id"] == agent_id:
            agent["config"].update(config)
            break
    save_agents(agents)

def add_agent(agent: Dict[str, Any]):
    """Добавляет нового агента (если id не существует)."""
    agents = load_agents()
    if any(a["id"] == agent["id"] for a in agents):
        raise ValueError(f"Агент с id '{agent['id']}' уже существует")
    agents.append(agent)
    save_agents(agents)

def remove_agent(agent_id: str):
    """Удаляет агента по id."""
    agents = load_agents()
    agents = [a for a in agents if a["id"] != agent_id]
    save_agents(agents)

def get_enabled_agents() -> List[Dict[str, Any]]:
    """Возвращает список включённых агентов."""
    return [a for a in load_agents() if a.get("enabled", False)]
