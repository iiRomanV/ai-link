"""Парсер команд «сохрани как правило».

Распознаёт фразы типа:
  • «сохрани как правило: пути только относительные»
  • «запомни правило — использовать type hints»
  • «правило: всегда docstrings»
  • «добавь правило глобально: не использовать print»

Возвращает:
  {
    "is_rule": bool,
    "text": "<текст самого правила>",
    "scope": "project" | "global" | "unknown",
  }
"""

import re
from typing import Dict, Any


# Триггеры, которые открывают команду.
# AN: добавлены триггеры без слова «правило», но
# ТОЛЬКО с маркером области (глобально / для проекта),
# чтобы не ловить «запомни моё имя» и подобное.
TRIGGERS = [
    # Существующие (со словом «правило»)
    "сохрани как правило",
    "запомни правило",
    "добавь правило",
    "запиши правило",
    "правило:",
    "правило —",
    "правило -",
    # AN: «запомни» + явный маркер области
    "запомни глобально",
    "запомни для всех проектов",
    "запомни во всех проектах",
    "запомни везде",
    "запомни для проекта",
    "запомни в проекте",
    "запомни для этого проекта",
    "запомни в этом проекте",
    "запиши в память",
    "учти на будущее",
]

# Маркеры глобальной области
GLOBAL_MARKERS = [
    "глобально",
    "для всех проектов",
    "во всех проектах",
    "везде",
]

# Маркеры проектной области
PROJECT_MARKERS = [
    "для проекта",
    "в проекте",
    "для этого проекта",
    "в этом проекте",
]


def _strip_trigger(text: str) -> tuple:
    """Находит триггер, возвращает (текст правила, найденный триггер).

    AN: триггеры проверяются от длинных к коротким — чтобы
    «запомни глобально» не перебивалось «запомни правило» и т.п.
    """
    low = text.lower().strip()
    # Длинные триггеры первыми
    sorted_triggers = sorted(TRIGGERS, key=len, reverse=True)
    for trig in sorted_triggers:
        idx = low.find(trig)
        if idx == -1:
            continue
        # После триггера может быть ':' или '—' или '-'
        rest = text[idx + len(trig):].strip()
        # Убираем ведущие разделители
        rest = re.sub(r"^[:—\-]\s*", "", rest).strip()
        return rest, trig
    return "", ""


def _detect_scope(text: str) -> str:
    """Ищет маркер области (глобально / проект)."""
    low = text.lower()
    for marker in GLOBAL_MARKERS:
        if marker in low:
            return "global"
    for marker in PROJECT_MARKERS:
        if marker in low:
            return "project"
    return "unknown"


def _clean_rule(text: str) -> str:
    """Убирает маркеры области из текста правила."""
    result = text.strip()
    # Удаляем "глобально", "для проекта" и т.п. в конце
    for marker in GLOBAL_MARKERS + PROJECT_MARKERS:
        # Регистронезависимо с сохранением остального
        pattern = re.compile(re.escape(marker), re.IGNORECASE)
        result = pattern.sub("", result)
    # Убираем лишние запятые и пробелы
    result = re.sub(r"\s+", " ", result).strip()
    result = re.sub(r"^[,;:—\-]\s*", "", result).strip()
    result = re.sub(r"[,;:—\-]\s*$", "", result).strip()
    return result


def parse_rule_command(text: str) -> Dict[str, Any]:
    """Парсит фразу пользователя.

    Возвращает:
      is_rule: True, если это команда сохранения правила
      text:    текст правила (без служебных слов)
      scope:   "project" | "global" | "unknown"
    """
    if not text or not text.strip():
        return {"is_rule": False, "text": "", "scope": "unknown"}

    rule_text, trigger = _strip_trigger(text)
    if not trigger:
        return {"is_rule": False, "text": "", "scope": "unknown"}

    if not rule_text:
        # Триггер есть, но правила нет
        return {"is_rule": False, "text": "", "scope": "unknown",
                "error": "не указан текст правила"}

    scope = _detect_scope(text)
    clean = _clean_rule(rule_text)

    if not clean:
        return {"is_rule": False, "text": "", "scope": "unknown",
                "error": "пустое правило после очистки"}

    return {"is_rule": True, "text": clean, "scope": scope}


def is_rule_command(text: str) -> bool:
    """Быстрая проверка: содержит ли текст команду правила."""
    return parse_rule_command(text).get("is_rule", False)
