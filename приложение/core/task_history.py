"""
История выполнения задач для AI Link.
Каждая задача записывается в JSONL-файл.
"""

import json
import re
from pathlib import Path
from typing import Dict, Any, List
from datetime import datetime

HISTORY_FILE = Path.home() / ".ai_link" / "logs" / "task_history.jsonl"


def _ensure_dir():
    HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)


def log_task(
    task: str,
    agent: str,
    success: bool,
    files_created: int = 0,
    steps_completed: int = 0,
    duration: float = 0.0,
    notes: str = ""
):
    """Записывает задачу в историю (одна строка — один JSON)."""
    _ensure_dir()
    entry = {
        "timestamp": datetime.now().isoformat(),
        "task": task,
        "agent": agent,
        "success": success,
        "files_created": files_created,
        "steps_completed": steps_completed,
        "duration": round(duration, 2),
        "notes": notes
    }
    with open(HISTORY_FILE, 'a', encoding='utf-8') as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def load_history(limit: int = 100) -> List[Dict[str, Any]]:
    """Загружает последние limit записей истории."""
    if not HISTORY_FILE.exists():
        return []
    entries = []
    try:
        with open(HISTORY_FILE, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except Exception:
        return []
    return entries[-limit:]


def find_similar_tasks(task: str, limit: int = 20) -> List[Dict[str, Any]]:
    """Ищет похожие задачи в истории по ключевым словам."""
    if not task:
        return []
    words = set(re.findall(r'\w{5,}', task.lower()))
    if not words:
        return []
    similar = []
    for entry in load_history(limit=200):
        prev = entry.get("task", "").lower()
        prev_words = set(re.findall(r'\w{5,}', prev))
        if words & prev_words:
            similar.append(entry)
        if len(similar) >= limit:
            break
    return similar


def get_success_rate(agent: str) -> float:
    """Возвращает долю успешных задач для агента."""
    entries = [e for e in load_history(limit=500) if e.get("agent") == agent]
    if not entries:
        return 0.0
    success = sum(1 for e in entries if e.get("success"))
    return success / len(entries)


def get_agent_stats(agent: str) -> Dict[str, Any]:
    """Возвращает статистику по агенту."""
    entries = [e for e in load_history(limit=500) if e.get("agent") == agent]
    total = len(entries)
    if total == 0:
        return {"total_tasks": 0, "success_rate": 0.0, "avg_duration": 0.0}
    success = sum(1 for e in entries if e.get("success"))
    total_duration = sum(e.get("duration", 0) for e in entries)
    return {
        "total_tasks": total,
        "success_rate": round(success / total, 2),
        "avg_duration": round(total_duration / total, 2)
    }


if __name__ == "__main__":
    log_task("тестовая задача", "gigachat", True, files_created=1, duration=5.2)
    print("История:", load_history(limit=5))
