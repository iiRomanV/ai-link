"""MODEL-SUBSTITUTION — fallback-цепочки агентов по ролям.

Сейчас: route_rules.FALLBACK_AGENTS знает про фазы (plan/code/...).
Уточнение от Perplexity: важны не фазы, а РОЛИ агента:
  planner, coder, reviewer, brain, researcher, concept, dialog.

Если агент роли недоступен — берём следующего из цепочки.
Замена ЛОГИРУЕТСЯ в events.jsonl (model_substituted).
Молча не переключаемся.

API:
  get_chain(role)              → список агентов
  role_for_phase(phase)        → имя роли
  next_in_chain(role, current) → следующий или ""
  substitute(role, failed)     → следующий или ""
  try_chain(role, call_fn, ...) → (agent, result)
  log_substitution(...)        → пишет событие в events.jsonl
  summary_for_llm()            → компактный список для промптов
"""

from typing import Dict, Any, List, Optional, Callable


# ─────────────────────────────────────────────────────────────
#  Роли → цепочки агентов (приоритет: первый — лучший)
# ─────────────────────────────────────────────────────────────

# Ollama отсечён: мало RAM, роняет систему. Не ставим его
# в цепочки. Если когда-нибудь вернём — допишем первым/последним.
ROLE_CHAINS: Dict[str, List[str]] = {
    "planner":    ["browser_qwen", "browser_deepseek", "browser_perplexity"],
    "coder":      ["browser_deepseek", "browser_qwen", "browser_perplexity"],
    "reviewer":   ["browser_qwen", "browser_deepseek", "browser_perplexity"],
    "brain":      ["browser_qwen", "browser_deepseek"],
    "researcher": ["browser_perplexity", "browser_qwen", "browser_deepseek"],
    "concept":    ["browser_deepseek", "browser_qwen"],
    "dialog":     ["browser_deepseek", "browser_qwen"],
    "fallback":   ["browser_deepseek", "browser_qwen"],
}


# Фаза оркестратора → роль
PHASE_TO_ROLE: Dict[str, str] = {
    "plan":     "planner",
    "code":     "coder",
    "review":   "reviewer",
    "fix":      "coder",
    "fallback": "fallback",
    "concept":  "concept",
    "dialog":   "dialog",
}


# Известные агенты (для валидации)
KNOWN_AGENTS = {
    "browser_qwen", "browser_deepseek", "browser_perplexity",
    "gigachat", "ollama",
}


# ─────────────────────────────────────────────────────────────
#  Базовый API
# ─────────────────────────────────────────────────────────────

def get_chain(role: str) -> List[str]:
    """Цепочка агентов для роли ([] если роль неизвестна)."""
    return list(ROLE_CHAINS.get(role, []))


def role_for_phase(phase: str) -> str:
    """Имя роли для фазы (сама фаза, если маппинга нет)."""
    return PHASE_TO_ROLE.get(phase, phase)


def next_in_chain(role: str, current: str) -> str:
    """Следующий агент в цепочке после current. "" если нет."""
    chain = get_chain(role)
    if not chain:
        return ""
    try:
        idx = chain.index(current)
    except ValueError:
        # current не из цепочки — вернём первый
        return chain[0] if chain else ""
    if idx + 1 < len(chain):
        return chain[idx + 1]
    return ""


def substitute(role: str, failed_agent: str) -> str:
    """Агент на замену. "" если цепочка исчерпана."""
    return next_in_chain(role, failed_agent)


def is_known(agent: str) -> bool:
    return agent in KNOWN_AGENTS


# ─────────────────────────────────────────────────────────────
#  Логирование замены
# ─────────────────────────────────────────────────────────────

def log_substitution(project_dir: str,
                     role: str,
                     old_agent: str,
                     new_agent: str,
                     reason: str = "") -> None:
    """Пишет событие model_substituted в events.jsonl.

    Никогда не падает — при ошибке тихо игнорирует.
    """
    try:
        from core.event_log import log_event
        log_event(project_dir=project_dir,
                  event_type="model_substituted", payload={
                      "role": role,
                      "old": old_agent,
                      "new": new_agent,
                      "reason": (reason or "")[:200],
                  })
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────
#  try_chain — главный помощник
# ─────────────────────────────────────────────────────────────

def try_chain(role: str,
              call_fn: Callable[[str], Dict[str, Any]],
              project_dir: str = "",
              max_attempts: Optional[int] = None,
              reason_prefix: str = "") -> Dict[str, Any]:
    """Пробует агентов по цепочке роли, пока не получится успех.

    call_fn(agent) → dict {"success": bool, "text": str, "error": str}

    Возвращает:
      {
        "success": bool,
        "agent": "<итоговый агент или "">",
        "result": {...}    # последний ответ call_fn,
        "tried": [список агентов в порядке попыток],
        "error": "если не получилось",
      }

    Логирует каждую замену в events.jsonl (если project_dir задан).
    """
    chain = get_chain(role)
    if not chain:
        return {
            "success": False, "agent": "", "result": {},
            "tried": [], "error": f"нет цепочки для роли {role!r}",
        }

    limit = max_attempts if max_attempts is not None else len(chain)
    tried: List[str] = []
    last_result: Dict[str, Any] = {}
    prev_agent = ""

    for i, agent in enumerate(chain[:limit]):
        tried.append(agent)
        # Логируем КАЖДУЮ замену — начиная со второй попытки.
        if i > 0 and project_dir:
            log_substitution(
                project_dir, role, prev_agent, agent,
                reason=(reason_prefix + " | " if reason_prefix else "") +
                       "previous failed",
            )
        prev_agent = agent
        try:
            r = call_fn(agent)
        except Exception as e:
            r = {"success": False, "text": "", "error": str(e)[:200]}
        last_result = r or {}
        if last_result.get("success"):
            return {
                "success": True, "agent": agent, "result": last_result,
                "tried": tried, "error": "",
            }

    return {
        "success": False,
        "agent": "",
        "result": last_result,
        "tried": tried,
        "error": (last_result.get("error")
                  or f"все агенты роли {role!r} не справились"),
    }


# ─────────────────────────────────────────────────────────────
#  Для промптов
# ─────────────────────────────────────────────────────────────

def summary_for_llm() -> str:
    """Компактное описание ролей и их цепочек — для промпта brain."""
    lines = ["РОЛИ И АГЕНТЫ (fallback-цепочки):"]
    for role, chain in ROLE_CHAINS.items():
        lines.append(f"  • {role}: {' → '.join(chain)}")
    return "\n".join(lines)


def reset_chains() -> None:
    """Сбрасывает цепочки к дефолтным (для тестов)."""
    # Ничего не делаем — ROLE_CHAINS не изменялся.
    # Оставлено для симметрии с reset_* в других модулях.
    pass
