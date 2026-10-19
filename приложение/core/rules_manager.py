"""Менеджер правил проекта и глобальных правил.

Правила — это «памятка для агентов»: как работать в проекте.
Хранятся в двух местах:
  • Глобальные: ~/.ai_link/brain/rules.json
  • Проектные:  <project_dir>/.ai_link/project.json (ключ rules)

Агенты (DeepSeek, Qwen) видят объединённый список правил
в промпте через build_context.
"""

import json
from pathlib import Path
from typing import Dict, Any, List, Optional
from datetime import datetime


GLOBAL_RULES_FILE = Path.home() / ".ai_link" / "brain" / "rules.json"


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _project_rules_path(project_dir: str) -> Path:
    return Path(project_dir) / ".ai_link" / "project.json"


# === Глобальные правила ===

def load_global_rules() -> List[Dict[str, Any]]:
    """Читает список глобальных правил.

    Формат каждой записи:
        {"text": "...", "created_at": "...", "source": "user|link"}
    """
    if not GLOBAL_RULES_FILE.exists():
        return []
    try:
        data = json.loads(GLOBAL_RULES_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []
    # Может быть список или {"rules": [...]}
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return data.get("rules", [])
    return []


def save_global_rules(rules: List[Dict[str, Any]]) -> None:
    """Сохраняет глобальные правила."""
    GLOBAL_RULES_FILE.parent.mkdir(parents=True, exist_ok=True)
    try:
        GLOBAL_RULES_FILE.write_text(
            json.dumps(rules, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception:
        pass


def add_global_rule(rule: str, source: str = "user") -> Dict[str, Any]:
    """Добавляет одно глобальное правило. Возвращает добавленную запись."""
    rule = (rule or "").strip()
    if not rule:
        return {}
    rules = load_global_rules()
    # Проверка дубликатов
    for r in rules:
        if r.get("text", "").strip() == rule:
            return r
    entry = {
        "text": rule,
        "created_at": _now(),
        "source": source,
    }
    rules.append(entry)
    save_global_rules(rules)
    return entry


def remove_global_rule(rule_text: str) -> bool:
    """Удаляет правило по тексту. Возвращает True, если удалено."""
    rules = load_global_rules()
    new_rules = [r for r in rules if r.get("text", "").strip() != rule_text.strip()]
    if len(new_rules) == len(rules):
        return False
    save_global_rules(new_rules)
    return True


# === Проектные правила ===

def load_project_rules(project_dir: str) -> List[Dict[str, Any]]:
    """Читает список правил проекта из .ai_link/project.json."""
    path = _project_rules_path(project_dir)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    rules = data.get("rules", [])
    if not isinstance(rules, list):
        return []
    # Если это список строк — нормализуем в dict
    result: List[Dict[str, Any]] = []
    for r in rules:
        if isinstance(r, str):
            result.append({"text": r, "created_at": "", "source": "user"})
        elif isinstance(r, dict):
            result.append(r)
    return result


def save_project_rules(project_dir: str, rules: List[Dict[str, Any]]) -> None:
    """Сохраняет правила проекта в .ai_link/project.json."""
    path = _project_rules_path(project_dir)
    path.parent.mkdir(parents=True, exist_ok=True)

    # Читаем существующий project.json, чтобы не потерять ключи
    existing: Dict[str, Any] = {}
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            existing = {}

    existing["rules"] = rules
    try:
        path.write_text(
            json.dumps(existing, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception:
        pass


def add_project_rule(project_dir: str, rule: str,
                     source: str = "user") -> Dict[str, Any]:
    """Добавляет одно правило в проект."""
    rule = (rule or "").strip()
    if not rule:
        return {}
    rules = load_project_rules(project_dir)
    for r in rules:
        if r.get("text", "").strip() == rule:
            return r
    entry = {
        "text": rule,
        "created_at": _now(),
        "source": source,
    }
    rules.append(entry)
    save_project_rules(project_dir, rules)
    return entry


def remove_project_rule(project_dir: str, rule_text: str) -> bool:
    """Удаляет правило проекта по тексту."""
    rules = load_project_rules(project_dir)
    new_rules = [r for r in rules if r.get("text", "").strip() != rule_text.strip()]
    if len(new_rules) == len(rules):
        return False
    save_project_rules(project_dir, new_rules)
    return True


# === Объединённый список ===

def all_rules(project_dir: str) -> Dict[str, List[Dict[str, Any]]]:
    """Возвращает правила разделённо: глобальные и проектные."""
    return {
        "global": load_global_rules(),
        "project": load_project_rules(project_dir),
    }


def all_rules_text(project_dir: str) -> str:
    """Возвращает объединённый текст правил для промпта."""
    data = all_rules(project_dir)
    lines: List[str] = []

    if data["global"]:
        lines.append("=== ГЛОБАЛЬНЫЕ ПРАВИЛА ===")
        for r in data["global"]:
            lines.append(f"- {r.get('text', '')}")

    if data["project"]:
        if lines:
            lines.append("")
        lines.append("=== ПРАВИЛА ПРОЕКТА ===")
        for r in data["project"]:
            lines.append(f"- {r.get('text', '')}")

    return "\n".join(lines)
