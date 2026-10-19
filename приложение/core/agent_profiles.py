"""
Профили агентов для AI Link.
Хранит возможности и ограничения каждого ИИ-агента.
"""

import json
from pathlib import Path
from typing import Dict, Any, Optional

PROFILES_FILE = Path.home() / ".ai_link" / "brain" / "agent_profiles.json"

DEFAULT_PROFILES = {
    "agents": [
        {
            "id": "gigachat",
            "name": "GigaChat (Sber API)",
            "type": "api",
            "capabilities": {
                "max_context_tokens": 4000,
                "max_actions_per_request": 3,
                "supports_json": True,
                "supports_code_generation": True,
                "supports_research": False,
                "speed": "fast",
                "cost": "paid"
            },
            "best_for": ["simple_file_creation", "quick_edits", "dialog"],
            "avoid_for": ["large_projects", "multi_file_generation", "research"]
        },
        {
            "id": "ollama",
            "name": "Локальная Qwen (Ollama)",
            "type": "local",
            "capabilities": {
                "max_context_tokens": 8000,
                "max_actions_per_request": 3,
                "supports_json": True,
                "supports_code_generation": True,
                "supports_research": False,
                "speed": "fast",
                "cost": "free"
            },
            "best_for": ["simple_file_creation", "local_tasks", "dialog"],
            "avoid_for": ["large_projects", "multi_file_generation"]
        },
        {
            "id": "browser_qwen",
            "name": "Qwen (браузер)",
            "type": "browser",
            "capabilities": {
                "max_context_tokens": 8000,
                "max_actions_per_request": 3,
                "supports_json": True,
                "supports_code_generation": True,
                "supports_research": False,
                "speed": "medium",
                "cost": "free",
                "requires_login": True
            },
            "best_for": ["dialog", "reasoning", "medium_tasks"],
            "avoid_for": ["large_projects"]
        },
        {
            "id": "browser_deepseek",
            "name": "DeepSeek (браузер)",
            "type": "browser",
            "capabilities": {
                "max_context_tokens": 32000,
                "max_actions_per_request": 20,
                "supports_json": True,
                "supports_code_generation": True,
                "supports_research": True,
                "speed": "slow",
                "cost": "free",
                "requires_login": True
            },
            "best_for": ["large_projects", "multi_file_generation", "code_generation"],
            "avoid_for": ["quick_edits"]
        },
        {
            "id": "browser_perplexity",
            "name": "Perplexity (браузер)",
            "type": "browser",
            "capabilities": {
                "max_context_tokens": 16000,
                "max_actions_per_request": 2,
                "supports_json": False,
                "supports_code_generation": False,
                "supports_research": True,
                "speed": "medium",
                "cost": "free",
                "requires_login": True
            },
            "best_for": ["research", "dialog", "analysis"],
            "avoid_for": ["large_projects", "code_generation"]
        }
    ]
}


def _ensure_dir():
    PROFILES_FILE.parent.mkdir(parents=True, exist_ok=True)


def load_profiles() -> Dict[str, Any]:
    """Загружает профили агентов. Если файла нет — создаёт с настройками по умолчанию."""
    _ensure_dir()
    if PROFILES_FILE.exists():
        try:
            with open(PROFILES_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    save_profiles(DEFAULT_PROFILES)
    return DEFAULT_PROFILES.copy()


def save_profiles(data: Dict[str, Any]):
    """Сохраняет профили агентов."""
    _ensure_dir()
    with open(PROFILES_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def get_agent_profile(agent_id: str) -> Optional[Dict[str, Any]]:
    """Возвращает профиль агента по id (регистронезависимо)."""
    data = load_profiles()
    agent_id_lower = agent_id.lower()
    for agent in data.get("agents", []):
        if agent.get("id", "").lower() == agent_id_lower:
            return agent
    return None


def get_max_actions(agent_id: str) -> int:
    """Возвращает максимальное число действий за один запрос для агента."""
    profile = get_agent_profile(agent_id)
    if profile:
        return profile.get("capabilities", {}).get("max_actions_per_request", 3)
    return 3


def is_good_for_large_project(agent_id: str) -> bool:
    """Проверяет, подходит ли агент для многофайловых проектов."""
    profile = get_agent_profile(agent_id)
    if not profile:
        return False
    return "large_projects" in profile.get("best_for", [])


if __name__ == "__main__":
    # Быстрый тест
    print("Профили загружены:")
    for a in load_profiles().get("agents", []):
        print(f"  - {a['id']}: {a['name']} (max_actions={a['capabilities']['max_actions_per_request']})")
