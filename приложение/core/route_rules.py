"""Правила роутинга при сбоях фазы.

Когда фаза падает, оркестратор смотрит на reason и решает:
  • retry (повторить ту же фазу)
  • switch_agent (взять другого агента)
  • skip (пропустить фазу)
  • stop (остановиться)
"""

from typing import Dict, Any, Optional


# Возможные действия
ACTION_RETRY = "retry"
ACTION_SWITCH = "switch_agent"
ACTION_SKIP = "skip"
ACTION_STOP = "stop"


# Таблица: reason → (действие, сколько раз максимум)
# reason приходит из browser_manager (см. 6.1)
DEFAULT_RULES: Dict[str, Dict[str, Any]] = {
    "login_required": {
        "action": ACTION_SKIP,
        "max_attempts": 1,
        "note": "не тратить время, требуется вход",
    },
    "input_not_found": {
        "action": ACTION_RETRY,
        "max_attempts": 3,
        "note": "селектор мог не догрузиться",
    },
    "response_timeout": {
        "action": ACTION_RETRY,
        "max_attempts": 1,
        "note": "один короткий retry",
    },
    "launch_failed": {
        "action": ACTION_SWITCH,
        "max_attempts": 1,
        "note": "браузер не запустился — взять другого",
    },
    "send_failed": {
        "action": ACTION_RETRY,
        "max_attempts": 2,
        "note": "промпт не отправился, повторить",
    },
    "site_not_found": {
        "action": ACTION_SWITCH,
        "max_attempts": 1,
        "note": "сайта нет — взять другого",
    },
    "site_disabled": {
        "action": ACTION_SWITCH,
        "max_attempts": 1,
        "note": "сайт выключен — взять другого",
    },
}


# ROLES (2026-10-01): hardcoded fallback-цепочки УБРАНЫ.
# Было: "plan": ["browser_qwen", "browser_deepseek", "ollama"] —
# игнорировало роли, подставляло ollama даже когда не назначено.
# FALLBACK_AGENTS оставлен пустым для совместимости импортов.
FALLBACK_AGENTS: Dict[str, list] = {}


def get_rule(reason: str) -> Dict[str, Any]:
    """Возвращает правило для reason (или дефолтное)."""
    if reason in DEFAULT_RULES:
        return dict(DEFAULT_RULES[reason])
    # Неизвестная ошибка — 1 retry, потом стоп
    return {
        "action": ACTION_RETRY,
        "max_attempts": 1,
        "note": "неизвестная ошибка",
    }


def get_fallback_agents(phase: str) -> list:
    """ROLES (2026-10-01): fallback через роль 'brain'.

    Раньше возвращал hardcoded цепочки (browser_qwen, ollama),
    игнорируя роли. Из-за этого подставлялась ollama даже когда
    пользователь не назначал.

    Теперь:
      • Если роль 'brain' назначена → [brain]
      • Если нет → [] → фаза стоп (не подставляем ничего)
    """
    try:
        from core.capability_benchmark import get_assigned_agent
        brain = get_assigned_agent("brain")
        if brain:
            return [brain]
    except Exception:
        pass
    return []


def decide_action(phase: str, reason: str,
                  attempts_made: int = 0) -> Dict[str, Any]:
    """Решает, что делать при сбое.

    Возвращает:
        {
          "action": "retry" | "switch_agent" | "skip" | "stop",
          "reason": "оригинальный reason",
          "attempts_made": N,
          "max_attempts": M,
          "note": "..."
        }
    """
    rule = get_rule(reason)
    action = rule["action"]
    max_attempts = rule["max_attempts"]

    # Логика по начальному действию
    if action == ACTION_SKIP or action == ACTION_STOP:
        # Не меняется — эти действия окончательные
        pass
    elif action == ACTION_RETRY:
        if attempts_made < max_attempts:
            action = ACTION_RETRY          # ещё можно попробовать
        elif attempts_made == max_attempts:
            action = ACTION_SWITCH         # хватит retry, меняем агента
        else:
            action = ACTION_STOP           # всё испробовали, стоп
    elif action == ACTION_SWITCH:
        if attempts_made == 0:
            action = ACTION_SWITCH         # первый раз — switch
        else:
            action = ACTION_STOP           # уже пробовали — стоп

    return {
        "action": action,
        "reason": reason,
        "attempts_made": attempts_made,
        "max_attempts": max_attempts,
        "note": rule.get("note", ""),
    }


def extract_reason(error_text: str) -> str:
    """Пытается извлечь reason из текста ошибки.

    Если ошибка типа "login_required: ..." — вернёт "login_required".
    Иначе — пустая строка.
    """
    if not error_text:
        return ""
    known = list(DEFAULT_RULES.keys())
    low = error_text.lower()
    for r in known:
        if r in low:
            return r
    return ""
