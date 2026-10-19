"""BRAIN-RULE-FIRST — ограниченный арбитр между фазами.

Идея (Perplexity): brain — не второй кодер, а диспетчер.
Сначала правила (без LLM), потом LLM для исключений.

Три режима (по возрастанию сложности):
  1. rule_brain    — детерминированные правила, без LLM.
                     Покрывает 90% случаев.
  2. qwen_brain    — LLM (Qwen) для нестандартных ситуаций.
  3. external_brain — другая модель (DeepSeek) для сложных
                      архитектурных решений.

Brain возвращает JSON, не текст:
  {
    "decision": "continue_phase" | "run_fix" | "run_review"
                | "next_stage" | "pause" | "finish" | "abort",
    "reason": "...",
    "next_phase": "fix" | "review" | "code" | "" | null,
    "next_action": "run_review" | "run_fix" | ... | null,
    "confidence": 0.0..1.0,
    "requires_user": bool,
    "mode": "rule" | "qwen" | "external" | "fallback"
  }

Policy Engine проверяет допустимость решения ДО исполнения:
  • brain НЕ может продлевать лимиты
  • brain НЕ может менять файлы / запускать команды
  • brain НЕ может менять архитектуру проекта
  • max_fix_cycles / max_same_failures — жёсткие пределы
"""

import json
from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional


# ─────────────────────────────────────────────────────────────
#  Константы решений
# ─────────────────────────────────────────────────────────────

D_CONTINUE = "continue_phase"   # продолжаем ту же фазу
D_NEXT_STAGE = "next_stage"     # перейти к следующему этапу
D_RUN_FIX = "run_fix"           # запустить fix
D_RUN_CODE = "run_code"         # запустить code (после plan)
D_RUN_REVIEW = "run_review"     # запустить review
D_FINISH = "finish"             # успешно завершить
D_PAUSE = "pause"               # пауза (нужен пользователь/лимит)
D_ABORT = "abort"               # прекратить (безнадёжно)


# ─────────────────────────────────────────────────────────────
#  Политики (жёсткие пределы, поверх которых brain не властен)
# ─────────────────────────────────────────────────────────────

@dataclass
class Policy:
    max_fix_cycles: int = 3
    max_same_failures: int = 2
    max_stages_without_progress: int = 3
    allow_user_escalation: bool = True


def policy_check(decision: Dict[str, Any],
                 state: Dict[str, Any],
                 policy: Optional[Policy] = None) -> Dict[str, Any]:
    """Проверяет решение на допустимость.

    Возвращает скорректированное решение:
      • добавляет "policy_note"
      • может подменить на "pause", если лимит исчерпан
      • запрещает brain менять то, что не его дело
    """
    pol = policy or Policy()
    d = dict(decision or {})
    dec = d.get("decision", "")

    fails = int(state.get("same_failures", 0) or 0)
    fix_cycles = int(state.get("fix_cycles", 0) or 0)
    no_progress = int(state.get("stages_without_progress", 0) or 0)

    # Лимит на fix-циклы
    if dec == D_RUN_FIX and fix_cycles >= pol.max_fix_cycles:
        d["policy_note"] = (
            f"fix_cycles={fix_cycles} ≥ max={pol.max_fix_cycles} — "
            f"эскалация в pause"
        )
        d["decision"] = D_PAUSE
        d["requires_user"] = True
        return d

    # Лимит одинаковых падений
    if dec in (D_CONTINUE, D_RUN_FIX, D_RUN_REVIEW) and \
            fails >= pol.max_same_failures:
        d["policy_note"] = (
            f"same_failures={fails} ≥ max={pol.max_same_failures} — "
            f"эскалация в pause"
        )
        d["decision"] = D_PAUSE
        d["requires_user"] = True
        return d

    # Нет прогресса много этапов
    if no_progress >= pol.max_stages_without_progress:
        d["policy_note"] = (
            f"stages_without_progress={no_progress} ≥ "
            f"max={pol.max_stages_without_progress} — эскалация в pause"
        )
        d["decision"] = D_PAUSE
        d["requires_user"] = True
        return d

    # Запрещаем brain'у менять phase произвольно
    # (next_phase допустим только из белого списка)
    nxt = d.get("next_phase")
    if nxt and nxt not in ("plan", "code", "review", "fix",
                            "fallback", "dialog", "concept"):
        d["policy_note"] = f"next_phase={nxt!r} вне белого списка"
        d["next_phase"] = None

    return d


# ─────────────────────────────────────────────────────────────
#  rule_brain — детерминированные правила (без LLM)
# ─────────────────────────────────────────────────────────────

def rule_brain(state: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Принимает решение БЕЗ LLM по правилам.

    state (ожидаемые ключи):
      phase               — текущая фаза
      phase_status        — статус фазы (success/failed/ok/problems/...)
      review_status       — ok / problems / unknown
      review_severity     — critical / major / minor / ""
      verification_status — verified / failed / partial (из VERIFICATION-RECEIPT)
      files_created       — сколько файлов создано в code-фазе
      iterations          — сколько итераций заняла фаза
      same_failures       — сколько раз подряд падает одна фаза
      fix_cycles          — сколько fix уже было
      stop_reason         — из SMART-EXIT (no_progress/loop_detected)
      has_stages          — есть ли план по этапам
      stage_index         — текущий этап
      total_stages        — всего этапов

    Возвращает dict-решение или None, если правило не сработало.
    """
    phase = state.get("phase", "")
    status = state.get("phase_status", "")
    review_status = state.get("review_status", "")
    severity = (state.get("review_severity") or "").lower()
    verify = state.get("verification_status", "")
    files_created = int(state.get("files_created", 0) or 0)
    iterations = int(state.get("iterations", 0) or 0)
    stop_reason = state.get("stop_reason", "")

    # 1a. fallback провалился — раньше всех (нет смысла чинить)
    if phase == "fallback" and status not in ("success", "ok", ""):
        return _r(D_PAUSE,
                  reason="fallback провалился — нужен пользователь",
                  requires_user=True, confidence=0.85)

    # 1b. verification failed → fix (раньше review ok!)
    if verify == "failed":
        return _r(D_RUN_FIX,
                  reason="verification failed — инструменты нашли проблемы",
                  next_phase="fix", confidence=0.9)

    # 1c. plan успешен → code (новая фаза)
    if phase == "plan" and status == "success":
        return _r(D_RUN_CODE,
                  reason="plan успешно составлен → code",
                  next_phase="code", confidence=0.95)

    # 1d. plan провалился ПЕРВЫЙ раз → retry (не fix)
    if phase == "plan" and status in ("failed", "timeout", "stopped"):
        same_fails = int(state.get("same_failures", 0) or 0)
        if same_fails == 0:
            return _r(D_CONTINUE,
                      reason="plan провалился, но это первая попытка",
                      next_phase="plan", confidence=0.7)
        return _r(D_PAUSE,
                  reason=f"plan провалился {same_fails} раз — "
                         f"нужен пользователь (задача неясна?)",
                  requires_user=True, confidence=0.85)

    # 1e. review success, но статус не ok и не problems — неизвестно
    if phase == "review" and status == "success" and \
            review_status not in ("ok", "problems"):
        return _r(D_PAUSE,
                  reason=f"review вернул неясный статус: "
                         f"{review_status or 'пусто'}",
                  requires_user=True, confidence=0.6)

    # 1f. Фаза провалилась — нечего продолжать
    if status in ("failed", "timeout", "stopped"):
        return _r(D_RUN_FIX,
                  reason=f"phase={phase} status={status}",
                  next_phase="fix", confidence=0.85)

    # 2. code завершился, но файлов нет — провал, не успех
    if phase == "code" and status == "success" and files_created == 0:
        return _r(D_RUN_FIX,
                  reason="code успех без файлов — считаем провалом",
                  next_phase="fix", confidence=0.9)

    # 3. code success + файлы есть → review
    if phase == "code" and status == "success" and files_created > 0:
        return _r(D_RUN_REVIEW,
                  reason=f"code успех ({files_created} файлов)",
                  next_phase="review", confidence=0.95)

    # 4. review ok → финал или следующий этап
    if phase == "review" and review_status == "ok":
        if state.get("has_stages") and \
                state.get("stage_index", 0) + 1 < state.get("total_stages", 0):
            return _r(D_NEXT_STAGE,
                      reason="review ok — есть ещё этапы",
                      next_phase="code", confidence=0.95)
        return _r(D_FINISH, reason="review ok — всё готово",
                  confidence=0.95)

    # 5. review problems, только MINOR → не fix, всё хорошо
    if phase == "review" and review_status == "problems" and severity == "minor":
        return _r(D_FINISH,
                  reason="review только MINOR — игнорируем",
                  confidence=0.9)

    # 5a. review problems + cross-verify rejected → придирки
    # FIX: brain_shadow раньше не знал про cross-verify и всегда
    # давал run_fix. Теперь — учитывает вердикт второй модели.
    _cv = (state.get("review_cross_verdict") or "").lower()
    if phase == "review" and review_status == "problems" and _cv == "rejected":
        return _r(D_FINISH,
                  reason="cross-verify отверг проблемы как придирки",
                  confidence=0.95)

    # 6. review problems + CRITICAL/MAJOR → fix
    if phase == "review" and review_status == "problems":
        return _r(D_RUN_FIX,
                  reason=f"review problems (severity={severity or 'unknown'})",
                  next_phase="fix", confidence=0.9)

    # 8. fix успешен → обратно в review
    if phase == "fix" and status == "success":
        return _r(D_RUN_REVIEW, reason="fix успешен — на review",
                  next_phase="review", confidence=0.9)

    # 9. SMART-EXIT: idle/no_progress/loop → fix или pause
    if stop_reason in ("no_progress", "loop_detected"):
        return _r(D_RUN_FIX,
                  reason=f"smart-exit: {stop_reason}",
                  next_phase="fix", confidence=0.7)

    # 10. Резервная фаза тоже провалилась
    if phase == "fallback" and status not in ("success", "ok"):
        return _r(D_PAUSE,
                  reason="fallback провалился — нужен пользователь",
                  requires_user=True, confidence=0.85)

    # Ничего не подошло — пусть решает LLM
    return None


def _r(decision: str, reason: str = "",
       next_phase: Optional[str] = None,
       next_action: Optional[str] = None,
       confidence: float = 0.5,
       requires_user: bool = False,
       mode: str = "rule") -> Dict[str, Any]:
    return {
        "decision": decision,
        "reason": reason,
        "next_phase": next_phase,
        "next_action": next_action,
        "confidence": float(confidence),
        "requires_user": bool(requires_user),
        "mode": mode,
    }


# ─────────────────────────────────────────────────────────────
#  LLM-режимы (пока заглушки — подключаются отдельно)
# ─────────────────────────────────────────────────────────────

def qwen_brain(state: Dict[str, Any],
               caller: Optional[Any] = None) -> Optional[Dict[str, Any]]:
    """LLM-режим: Qwen принимает решение.

    caller(prompt) → dict {"success": bool, "text": str}.
    Если caller не задан — возвращает None (LLM недоступен).
    """
    if caller is None:
        return None
    prompt = _build_brain_prompt(state)
    try:
        r = caller(prompt)
    except Exception as e:
        return _r(D_PAUSE, reason=f"qwen_brain error: {e}",
                  requires_user=True, mode="qwen")
    if not r or not r.get("success"):
        return None
    parsed = _parse_brain_json(r.get("text", ""))
    if not parsed:
        return None
    parsed.setdefault("mode", "qwen")
    return parsed


def external_brain(state: Dict[str, Any],
                   caller: Optional[Any] = None) -> Optional[Dict[str, Any]]:
    """LLM-режим: другая модель (DeepSeek) для сложных случаев."""
    if caller is None:
        return None
    prompt = _build_brain_prompt(state)
    try:
        r = caller(prompt)
    except Exception as e:
        return _r(D_PAUSE, reason=f"external_brain error: {e}",
                  requires_user=True, mode="external")
    if not r or not r.get("success"):
        return None
    parsed = _parse_brain_json(r.get("text", ""))
    if not parsed:
        return None
    parsed.setdefault("mode", "external")
    return parsed


# ─────────────────────────────────────────────────────────────
#  Главный вход
# ─────────────────────────────────────────────────────────────

def decide(state: Dict[str, Any],
           policy: Optional[Policy] = None,
           qwen_caller: Optional[Any] = None,
           external_caller: Optional[Any] = None,
           use_llm: bool = False) -> Dict[str, Any]:
    """Принимает решение: rule → (опц.) qwen → (опц.) external.

    use_llm=False по умолчанию: только правила. Это и есть
    «rule-first»: LLM подключается осознанно.

    Всегда возвращает dict (никогда None).
    """
    # 1. Правила
    d = rule_brain(state)
    if d is not None:
        return policy_check(d, state, policy)

    # 2. Qwen (если разрешено)
    if use_llm:
        d = qwen_brain(state, caller=qwen_caller)
        if d is not None:
            return policy_check(d, state, policy)

        # 3. Внешний brain
        d = external_brain(state, caller=external_caller)
        if d is not None:
            return policy_check(d, state, policy)

    # 4. Не смогли решить — просим пользователя
    d = _r(D_PAUSE,
           reason="brain не смог решить автоматически",
           requires_user=True, confidence=0.3, mode="fallback")
    return policy_check(d, state, policy)


# ─────────────────────────────────────────────────────────────
#  Вспомогательное
# ─────────────────────────────────────────────────────────────

BRAIN_PROMPT_TEMPLATE = """Ты — арбитр между фазами AI Link.
Тебе дано состояние прогона. Верни РОВНО ОДИН JSON-объект
без пояснений и без markdown-обёртки:

{{
  "decision": "continue_phase|next_stage|run_fix|run_review|finish|pause|abort",
  "reason": "короткая причина на русском",
  "next_phase": "plan|code|review|fix|fallback|null",
  "next_action": "строка или null",
  "confidence": 0.0,
  "requires_user": false
}}

ПРАВИЛА:
  • Не предлагай менять лимиты (max_phases, fix_cycles) — это не твоё.
  • Не предлагай писать код или менять файлы — ты только диспетчер.
  • Если сомневаешься — decision="pause", requires_user=true.

СОСТОЯНИЕ:
{state}
"""


def _build_brain_prompt(state: Dict[str, Any]) -> str:
    # Компактный JSON без больших вложений
    safe = {}
    for k, v in (state or {}).items():
        if isinstance(v, (str, int, float, bool)) or v is None:
            safe[k] = v
        elif isinstance(v, list) and len(v) <= 10:
            safe[k] = v
    return BRAIN_PROMPT_TEMPLATE.format(
        state=json.dumps(safe, ensure_ascii=False, indent=2)
    )


def _parse_brain_json(text: str) -> Optional[Dict[str, Any]]:
    """Извлекает JSON-объект решения из ответа LLM."""
    if not text:
        return None
    t = text.strip()
    # Убираем ```json ... ```
    if t.startswith("```"):
        first = t.find("\n")
        last = t.rfind("```")
        if first != -1 and last > first:
            t = t[first + 1:last].strip()
    # Ищем первый { ... }
    i = t.find("{")
    j = t.rfind("}")
    if i == -1 or j == -1 or j <= i:
        return None
    try:
        d = json.loads(t[i:j + 1])
    except Exception:
        return None
    if not isinstance(d, dict):
        return None
    if "decision" not in d:
        return None
    # Нормализация типов
    d["confidence"] = float(d.get("confidence", 0.5) or 0.5)
    d["requires_user"] = bool(d.get("requires_user", False))
    return d
