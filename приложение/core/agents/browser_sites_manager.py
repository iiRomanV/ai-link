"""
Управление списком браузерных сайтов для AI Link.
Список хранится в ~/.ai_link/brain/browser_sites.json.
"""

import json
from pathlib import Path
from typing import Dict, Any, List, Optional

SITES_FILE = Path.home() / ".ai_link" / "brain" / "browser_sites.json"

DEFAULT_SITES = {
    "sites": [
        {
            "id": "qwen",
            "name": "Qwen Chat",
            "url": "https://chat.qwen.ai",
            "input_selector": "textarea[placeholder*='message'], textarea",
            "submit_key": "Enter",
            "response_selector": ".markdown-body, .response-content, .chat-message",
            "wait_selector": ".markdown-body, .response-content",
            "timeout": 60,
            "profile_subdir": "qwen_profile",
            "enabled": True
        },
        {
            "id": "perplexity",
            "name": "Perplexity",
            "url": "https://www.perplexity.ai",
            "input_selector": "textarea[placeholder*='Ask'], textarea",
            "submit_key": "Enter",
            "response_selector": ".prose, .markdown, [class*='answer']",
            "wait_selector": ".prose, .markdown",
            "timeout": 90,
            "profile_subdir": "perplexity_profile",
            "enabled": True
        }
    ]
}


def _ensure_dir():
    SITES_FILE.parent.mkdir(parents=True, exist_ok=True)


def load_sites() -> Dict[str, Any]:
    """Загружает список сайтов. Если файла нет — создаёт с настройками по умолчанию."""
    _ensure_dir()
    if SITES_FILE.exists():
        try:
            with open(SITES_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    save_sites(DEFAULT_SITES)
    return DEFAULT_SITES.copy()


def save_sites(data: Dict[str, Any]):
    """Сохраняет список сайтов."""
    _ensure_dir()
    with open(SITES_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def get_site(site_id: str) -> Optional[Dict[str, Any]]:
    """Возвращает сайт по id (регистронезависимо)."""
    data = load_sites()
    sid = site_id.lower()
    for site in data.get("sites", []):
        if site.get("id", "").lower() == sid:
            return site
    return None


def get_enabled_sites() -> List[Dict[str, Any]]:
    """Возвращает только включённые сайты."""
    return [s for s in load_sites().get("sites", []) if s.get("enabled", False)]


def add_site(site: Dict[str, Any]):
    """Добавляет новый сайт."""
    data = load_sites()
    if any(s.get("id") == site.get("id") for s in data.get("sites", [])):
        raise ValueError(f"Сайт с id '{site.get('id')}' уже существует")
    data.setdefault("sites", []).append(site)
    save_sites(data)


def update_site(site_id: str, updates: Dict[str, Any]):
    """Обновляет поля сайта."""
    data = load_sites()
    for site in data.get("sites", []):
        if site.get("id") == site_id:
            site.update(updates)
            break
    save_sites(data)


def remove_site(site_id: str):
    """Удаляет сайт по id."""
    data = load_sites()
    data["sites"] = [s for s in data.get("sites", []) if s.get("id") != site_id]
    save_sites(data)


if __name__ == "__main__":
    print("Сайты загружены:")
    for s in load_sites().get("sites", []):
        print(f"  - {s.get('name')} ({s.get('id')}) -> {s.get('url')} | enabled={s.get('enabled')}")
