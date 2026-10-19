"""AUDIT-PLANNER (2026-10-08, Этап 4.1): предварительный план из аудита.

Собирает промпт из:
  • result аудита (техдолг, идеи, логика, секреты).
  • user_hint — что пользователь написал про проект.

Вызывает LLM (роль planning) → получает план в формате plan.json.

Сохраняет в audits/<audit_id>/audit_plan.json (ПРЕДВАРИТЕЛЬНЫЙ,
не в plan.json проекта). Пользователь согласует → потом применяем.
"""

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def _audit_dir(project_dir: str, audit_id: str) -> Path:
    return Path(project_dir) / ".ai_link" / "audits" / audit_id


def _collect_audit_summary(result: Dict[str, Any]) -> str:
    """Сжатая сводка аудита для промпта."""
    lines: List[str] = []

    # Цель проекта (если LLM-описание было)
    desc = result.get("llm_description") or {}
    if desc.get("success"):
        _d = (desc.get("description") or "").strip()
        if _d:
            lines.append("=== ОПИСАНИЕ ПРОЕКТА ===")
            lines.append(_d[:2000])
            lines.append("")

    # Технический аудит
    tech = result.get("technical") or {}
    issues = tech.get("issues") or []
    if issues:
        lines.append(f"=== ТЕХНИЧЕСКИЕ ПРОБЛЕМЫ ({len(issues)}) ===")
        for i, it in enumerate(issues[:30], 1):
            sev = it.get("severity", "?")
            f = it.get("file", "?")
            msg = (it.get("message") or "")[:200]
            lines.append(f"  {i}. [{sev}] {f}: {msg}")
        lines.append("")

    # Секреты (факты, без значений)
    sec = result.get("security") or {}
    if sec.get("total"):
        lines.append(f"=== СЕКРЕТЫ: {sec['total']} (значения скрыты) ===")
        for it in (sec.get("findings") or [])[:10]:
            lines.append(f"  • {it.get('file', '?')}:{it.get('line', '?')}")
        lines.append("")

    # Логика (LLM-находки)
    la = result.get("logic_analysis") or {}
    la_issues = la.get("issues") or []
    if la_issues:
        lines.append(f"=== ЛОГИЧЕСКИЕ НАХОДКИ ({len(la_issues)}) ===")
        for i, it in enumerate(la_issues[:20], 1):
            f = it.get("file", "?")
            line = it.get("line", "?")
            msg = (it.get("message") or "")[:200]
            sev = it.get("severity", "?")
            lines.append(f"  {i}. [{sev}] {f}:{line} — {msg}")
        lines.append("")

    # Идеи LLM
    ideas = result.get("llm_ideas") or {}
    if ideas.get("success"):
        _i = (ideas.get("ideas") or "").strip()
        if _i:
            lines.append("=== ИДЕИ ОТ LLM ===")
            lines.append(_i[:2000])
            lines.append("")

    # Зависимости
    deps = result.get("dependencies") or {}
    if deps.get("files"):
        lines.append("=== ЗАВИСИМОСТИ ===")
        for f in (deps.get("files") or [])[:5]:
            lines.append(f"  • {f}")
        lines.append("")

    return "\n".join(lines)


_PLAN_PROMPT_TEMPLATE = """Ты — AI Link, планировщик проектов.

Пользователь принёс ЧУЖОЙ проект на доработку.
Сначала прошёл аудит. Теперь нужно составить ПЛАН доработки.

ОРИГИНАЛЬНАЯ ЗАДАЧА ПОЛЬЗОВАТЕЛЯ:
{user_hint}

РЕЗУЛЬТАТЫ АУДИТА:
{audit_summary}

ТВОЯ ЗАДАЧА:
Составь ПЛАН ДОРАБОТКИ проекта. Учитывай только то, что реально
найдено в аудите + пожелания пользователя. НЕ переделывай проект
с нуля, если он рабочий.

ФОРМАТ ОТВЕТА — СТРОГО ТАКОЙ:

НАЗВАНИЕ: <короткое название>
ЦЕЛЬ: <1-2 предложения, что хотим получить>

ЭТАП: <название этапа 1>
- <шаг 1.1>
- <шаг 1.2>

ЭТАП: <название этапа 2>
- <шаг 2.1>

ПРАВИЛА:
  • 2-5 этапов, в каждом 2-5 шагов.
  • Шаги конкретные: «Исправить SQL-инъекцию в db.py:42»,
    а не «улучшить код».
  • Если пользователь хочет добавить функциональность — этап
    «Новая функциональность» с конкретными шагами.
  • Если пользователь просит что-то НЕ трогать — не включай.
  • КАЖДЫЙ ШАГ — ОБЯЗАТЕЛЬНО с дефисом в начале строки: «- шаг».
  • НЕ пиши reasoning перед планом. Сразу НАЗВАНИЕ.
  • Никаких пояснений вокруг. Только план.

ПРИМЕР ПРАВИЛЬНОГО ОТВЕТА:

НАЗВАНИЕ: Очистка утечек
ЦЕЛЬ: Убрать секреты из history.json.

ЭТАП: Удаление секретов
- Открыть history.json:1985 и заменить секрет
- Проверить валидность JSON

ЭТАП: Защита
- Добавить в .gitignore
- Создать history.json.example

ТЕПЕРЬ ТВОЙ ОТВЕТ:
"""


def build_plan_from_audit(project_dir: str,
                          audit_id: str,
                          user_hint: str = "",
                          agent_mode: Optional[str] = None
                          ) -> Dict[str, Any]:
    """Строит предварительный план из аудита.

    Возвращает:
      {"ok": True, "plan": {...}, "raw": "...", "path": "..."}
      или {"ok": False, "error": "..."}
    """
    d = _audit_dir(project_dir, audit_id)
    audit_json = d / "audit.json"
    if not audit_json.exists():
        return {"ok": False, "error": f"нет audit.json: {audit_json}"}

    try:
        result = json.loads(audit_json.read_text(encoding="utf-8"))
    except Exception as e:
        return {"ok": False, "error": f"json: {e}"}

    summary = _collect_audit_summary(result)
    hint = (user_hint or "").strip() or "(без дополнительных пожеланий)"

    prompt = _PLAN_PROMPT_TEMPLATE.format(
        user_hint=hint,
        audit_summary=summary[:6000],
    )

    # Роль planning.
    if not agent_mode:
        try:
            from core.audit_project import _agent_for_role
            agent_mode = _agent_for_role("planning")
        except Exception:
            agent_mode = ""
    if not agent_mode:
        return {"ok": False, "error": "роль planning не назначена"}

    try:
        from core.audit_project import _llm_call
        r = _llm_call(agent_mode, prompt, timeout=180)
    except Exception as e:
        return {"ok": False, "error": f"llm: {e}"}

    if not r.get("success"):
        return {"ok": False,
                "error": r.get("error") or "LLM не ответил"}

    raw = (r.get("text") or "").strip()
    plan = _parse_plan_text(raw)

    try:
        out = d / "audit_plan.json"
        out.write_text(
            json.dumps({
                "audit_id": audit_id,
                "created_at": _now(),
                "user_hint": user_hint,
                "raw": raw[:10000],
                "plan": plan,
                "status": "draft",   # draft → accepted / rejected
            }, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.info("AUDIT-PLANNER: сохранён %s", out)
    except Exception as e:
        return {"ok": False, "error": f"save: {e}"}

    return {"ok": True, "plan": plan, "raw": raw, "path": str(out)}


def _now() -> str:
    from datetime import datetime
    return datetime.now().isoformat(timespec="seconds")


# PLAN-LETTERS (2026-10-08): буквы этапам (А, Б, В...)
# и номера item'ам (1, 2, 3...) — для чёткого диалога с LLM.
_BUKVY = "АБВГДЕЖЗИКЛМНОПРСТУФХЦЧШЩЭЮЯ"


def _letter(idx: int) -> str:
    """А, Б, В, ... для этапов. За пределами — «Э1», «Э2»..."""
    if 0 <= idx < len(_BUKVY):
        return _BUKVY[idx]
    return f"Э{idx + 1}"


def _parse_plan_text(text: str) -> Dict[str, Any]:
    """Парсит план из текста LLM в структуру plan.json.

    FORMAT-FLEX (2026-10-08): принимаем и с дефисами, и без.
    LLM часто пишет просто текст после «ЭТАП:».

    Формат plan.json: {title, goal, stages: [{name, items: [...]}]}.
    """
    title = ""
    goal = ""
    stages: List[Dict[str, Any]] = []
    current: Optional[Dict[str, Any]] = None

    def _clean(s: str) -> str:
        """Убирает ведущие маркеры списка, нумерацию."""
        _s = s.strip()
        for prefix in ("- ", "* ", "• ", "· "):
            if _s.startswith(prefix):
                return _s[len(prefix):].strip()
        # Нумерация: "1. ", "1) ", "1 - ",
        # а также "А1.", "Б2.", "Г10)" — буква + цифра.
        import re as _re
        m = _re.match(r"^(?:[А-ЯA-Z])?\d+[.)]?\s*-?\s*", _s)
        if m:
            return _s[m.end():].strip()
        return _s

    for line in (text or "").splitlines():
        s = line.strip()
        if not s:
            continue
        u = s.upper()

        if u.startswith("НАЗВАНИЕ:"):
            # PLAN-DEDUP (2026-10-08): LLM иногда пишет план 2 раза
            # (сначала в reasoning, потом "Use exactly" — ещё раз).
            # При повторном НАЗВАНИИ — сбрасываем и начинаем заново,
            # берём последний полный план.
            if stages or title:
                stages.clear()
                current = None
                title = ""
                goal = ""
            title = s[len("НАЗВАНИЕ:"):].strip()
            # Мусор типа '". No extra text. Use exactly.'
            if title.startswith('"') or "extra text" in title.lower():
                title = ""
            continue
        if u.startswith("ЦЕЛЬ:"):
            _g = s[len("ЦЕЛЬ:"):].strip()
            if _g.startswith('"'):
                _g = ""
            goal = _g
            continue
        if u.startswith("ЭТАП:") or u.startswith("### ЭТАП"):
            name = s
            for pref in ("ЭТАП:", "### ЭТАП", "## ЭТАП", "# ЭТАП"):
                if name.upper().startswith(pref):
                    name = name[len(pref):].strip()
                    break
            if name and not name.startswith("<"):
                # PLAN-FIX-LETTER: срезаем [Х] в конце имени.
                import re as _re
                name = _re.sub(r"\s*\[[А-ЯA-Z]\]\s*$", "",
                               name).strip()
                if not name:
                    continue
                current = {
                    "letter": _letter(len(stages)),
                    "name": name,
                    "items": [],
                    "origin": "initial",
                }
                stages.append(current)
            continue

        # Пропускаем заголовки markdown.
        if s.startswith("#"):
            continue

        # Любая другая строка внутри этапа → item.
        if current is not None:
            body = _clean(s)
            if not body:
                continue
            if body.startswith("<"):
                continue
            current["items"].append({
                "num": len(current["items"]) + 1,
                "text": body[:300],
                "status": "pending",
            })

    if not title:
        title = "План доработки"
    return {
        "title": title,
        "goal": goal,
        "stages": stages,
        "status": "draft",
    }

# ============================================================
# PLAN-DISCUSS (2026-10-08, Шаг 4): обсуждение плана с LLM.
# ============================================================

def _plan_to_text(plan: Dict[str, Any]) -> str:
    """Сериализация плана в текст для промпта."""
    lines = []
    lines.append(f"НАЗВАНИЕ: {plan.get('title', '')}")
    lines.append(f"ЦЕЛЬ: {plan.get('goal', '')}")
    for s in plan.get("stages") or []:
        letter = s.get("letter", "?")
        lines.append("")
        _nm = s.get("name", "")
        # PLAN-FIX-LETTER (2026-10-08): срезаем [Х] из имени, если
        # оно там оказалось от предыдущей версии или от LLM.
        import re as _re
        _nm = _re.sub(r"\s*\[[А-ЯA-Z]\]\s*$", "", _nm).strip()
        lines.append(f"ЭТАП: {_nm}")
        for it in s.get("items") or []:
            txt = it.get("text", "")
            # Чистим, если текст уже с префиксом «А1.».
            import re as _re
            txt = _re.sub(r"^[А-ЯA-Z]?\d+[.)]\s*", "", txt).strip()
            lines.append(f"  - {txt}")
    return "\n".join(lines)


def _history_to_text(history: List[Dict[str, str]]) -> str:
    """История диалога → текст для промпта."""
    lines = []
    for msg in history:
        who = "ПОЛЬЗОВАТЕЛЬ" if msg.get("role") == "user" else "LLM"
        lines.append(f"{who}: {msg.get('text', '')}")
    return "\n".join(lines)


_REASONING_LEAD = (
    # English
    "user asks", "user wants", "user said", "user says",
    "user requests", "user needs", "user is", "user has",
    "the user", "we need", "we should", "we must", "we can",
    "let me", "let's", "i need", "i should",
    "answer briefly", "answer:", "response:",
    "skip a", "skip b", "skip c", "skip d", "skip e",
    "respond briefly", "respond:", "reply:",
    # Russian
    "мы должны", "пользователь просит", "пользователь хочет",
    "пользователь сказал", "пользователь пишет",
    "ответим", "сформулируем", "нужно ответить",
    "нужно сказать", "нужно уточнить",
)


def _strip_reasoning_lead(text: str) -> str:
    """Убирает reasoning из начала ответа LLM.

    Приоритет 1 (PLAN-MARKER): если в тексте есть «Ответ:» —
        берём всё ПОСЛЕ последнего маркера.
    Fallback: срезаем короткие reasoning-строки из начала.
    """
    if not text:
        return text
    text = text.strip()

    # PLAN-MARKER: ищем маркеры ответа. Берём последний.
    _MARKERS = ("Ответ на вопрос:", "Ответ:", "ОТВЕТ:",
                "Answer:", "ANSWER:")
    last_pos = -1
    last_len = 0
    for m in _MARKERS:
        p = text.rfind(m)
        if p > last_pos:
            last_pos = p
            last_len = len(m)
    if last_pos >= 0:
        after = text[last_pos + last_len:].strip()
        # Если после маркера хоть что-то осмысленное — берём.
        if len(after) >= 3:
            return after

    # Fallback: срезаем reasoning-строки из начала.
    lines = text.split("\n")
    if not lines:
        return text
    i = 0
    while i < len(lines):
        cur = lines[i].strip()
        if not cur:
            i += 1
            continue
        low = cur.lower()
        if len(cur) <= 200 and any(low.startswith(m)
                                    for m in _REASONING_LEAD):
            i += 1
            continue
        break
    rest = lines[i:]
    while rest and not rest[0].strip():
        rest = rest[1:]
    return "\n".join(rest).strip()

def discuss_plan(plan: Dict[str, Any],
                 history: List[Dict[str, str]],
                 message: str,
                 agent_mode: str = "") -> Dict[str, Any]:
    """Один шаг диалога обсуждения плана.

    Возвращает {ok, text, error}. LLM отвечает как консультант
    по плану: соглашается, возражает, уточняет. План НЕ меняется.
    """
    if not agent_mode:
        try:
            from core.audit_project import _agent_for_role
            agent_mode = _agent_for_role("planning")
        except Exception:
            agent_mode = ""
    if not agent_mode:
        return {"ok": False, "text": "",
                "error": "роль planning не назначена"}

    plan_text = _plan_to_text(plan)
    hist_text = _history_to_text(history)

    prompt = (
        "Ты — консультант по плану доработки проекта.\n"
        "Отвечай кратко, по делу, по-русски, максимум 5-7 строк.\n\n"
        "ФОРМАТ ОТВЕТА (обязательно):\n"
        "  Если хочешь рассуждать — reasoning пиши ДО.\n"
        "  Потом строка «Ответ:» и сразу сам ответ.\n"
        "  Пример:\n"
        "    Reasoning: краткие мысли...\n"
        "    Ответ: твой ответ пользователю\n\n"
        "ЗАПРЕЩЕНО:\n"
        "  • переписывать весь план — просто отвечай на сообщение.\n"
        "  • писать «Мы должны», «Let me», «The user».\n"
        "  • отвечать без маркера «Ответ:».\n\n"
        "ТЕКУЩИЙ ПЛАН:\n"
        f"{plan_text}\n\n"
        "ИСТОРИЯ ОБСУЖДЕНИЯ:\n"
        f"{hist_text}\n\n"
        f"СООБЩЕНИЕ ПОЛЬЗОВАТЕЛЯ: {message}"
    )

    try:
        from core.audit_project import _llm_call
        r = _llm_call(agent_mode, prompt, timeout=120)
    except Exception as e:
        return {"ok": False, "text": "", "error": str(e)[:200]}

    if not r.get("success"):
        return {"ok": False, "text": "",
                "error": r.get("error", "LLM error")[:200]}

    _txt = _strip_reasoning_lead(r.get("text") or "")
    return {"ok": True, "text": _txt, "error": ""}


def _norm(s: str) -> str:
    """Нормализация текста для сравнения."""
    import re as _re
    s = (s or "").strip().lower()
    s = _re.sub(r"\s+", " ", s)
    return s[:80]  # первые 80 символов


def _words(s: str) -> set:
    """Множество значимых слов (4+ символа, без стоп-слов)."""
    import re as _re
    s = (s or "").lower()
    words = _re.findall(r"[а-яa-z]{4,}", s)
    stop = {"этап", "пункт", "шаг", "проверка", "результат",
            "проект", "файл", "секрет", "защита", "очистка"}
    return {w for w in words if w not in stop}


def _stages_similar(name_a: str, name_b: str) -> bool:
    """Похожи ли имена этапов? (пересечение значимых слов > 40%)"""
    wa = _words(name_a)
    wb = _words(name_b)
    if not wa or not wb:
        return False
    inter = wa & wb
    smaller = min(len(wa), len(wb))
    return len(inter) / smaller >= 0.4


def _mark_diff(old_plan: Dict[str, Any],
               new_plan: Dict[str, Any]) -> Dict[str, int]:
    """IN-PLACE MERGE: сливает новый план со старым, сохраняя порядок.

    • Убранный этап → остаётся НА СВОЁМ МЕСТЕ, status='removed',
      все items → rejected.
    • Убранный item → остаётся в этапе, status='rejected'.
    • Добавленный item → в конец этапа, status='added'.
    • Новый этап → в конце, status='added'.
    • Итоговые этапы получают буквы А, Б, В... по порядку.

    Возвращает статистику {removed_stages, removed_items, added_items}.
    """
    stats = {"removed_stages": 0, "removed_items": 0, "added_items": 0}

    old_stages = old_plan.get("stages") or []
    new_stages = new_plan.get("stages") or []

    # Какие новые этапы уже сматчены — чтобы не использовать дважды.
    matched_new: set = set()
    result: List[Dict[str, Any]] = []

    def _find_new_match(name: str):
        """Найти соответствующий новый этап (точное или похожее имя)."""
        # Точное
        for i, ns in enumerate(new_stages):
            if i in matched_new:
                continue
            if _norm(ns.get("name", "")) == _norm(name):
                return i, ns
        # Похожее
        for i, ns in enumerate(new_stages):
            if i in matched_new:
                continue
            if _stages_similar(name, ns.get("name", "")):
                return i, ns
        return -1, None

    # 1) Идём по СТАРЫМ этапам по порядку.
    for olds in old_stages:
        oname = olds.get("name", "")
        oitems = olds.get("items") or []

        idx, news = _find_new_match(oname)

        if news is None:
            # Этап убран — оставляем на месте, все items rejected.
            result.append({
                "letter": "",
                "name": oname,
                "items": [
                    {"num": 0,
                     "text": it.get("text", ""),
                     "status": "rejected"}
                    for it in oitems
                ],
                "origin": "removed",
                "status": "removed",
            })
            stats["removed_stages"] += 1
            continue

        matched_new.add(idx)
        nitems = news.get("items") or []

        # Индекс по нормализованному тексту нового этапа.
        new_by_text = {_norm(it.get("text", "")): it for it in nitems}
        # Все тексты (норм.) новых items — для быстрого поиска.
        new_texts = set(new_by_text.keys())

        merged_items: List[Dict[str, Any]] = []

        # 1a) Старые items в их порядке: pending или rejected.
        for oit in oitems:
            tnorm = _norm(oit.get("text", ""))
            if tnorm in new_texts:
                nit = new_by_text[tnorm]
                merged_items.append({
                    "num": 0,
                    "text": nit.get("text", oit.get("text", "")),
                    "status": "pending",
                })
            else:
                merged_items.append({
                    "num": 0,
                    "text": oit.get("text", ""),
                    "status": "rejected",
                })
                stats["removed_items"] += 1

        # 1b) Новые items, которых не было в старом → added.
        old_texts = {_norm(it.get("text", "")) for it in oitems}
        for nit in nitems:
            tnorm = _norm(nit.get("text", ""))
            if tnorm not in old_texts:
                merged_items.append({
                    "num": 0,
                    "text": nit.get("text", ""),
                    "status": "added",
                })
                stats["added_items"] += 1

        # Имя этапа: новое (LLM мог переименовать).
        result.append({
            "letter": "",
            "name": news.get("name", "") or oname,
            "items": merged_items,
            "origin": "initial",
            "status": "pending",
        })

    # 2) Новые этапы, которые ни с чем не сматчились → added в конец.
    for i, ns in enumerate(new_stages):
        if i in matched_new:
            continue
        result.append({
            "letter": "",
            "name": ns.get("name", ""),
            "items": [
                {"num": 0, "text": it.get("text", ""),
                 "status": "added"}
                for it in ns.get("items") or []
            ],
            "origin": "added",
            "status": "added",
        })
        stats["added_items"] += len(ns.get("items") or [])

    # 3) Переназначаем буквы и номера.
    for i, s in enumerate(result):
        s["letter"] = _letter(i)
        for j, it in enumerate(s.get("items") or [], 1):
            it["num"] = j

    # 4) Подменяем stages в new_plan.
    new_plan["stages"] = result

    return stats



def apply_discussion(plan: Dict[str, Any],
                     history: List[Dict[str, str]],
                     agent_mode: str = "") -> Dict[str, Any]:
    """Перестроить план по всей переписке.

    Возвращает {ok, plan, text, error}.
    plan — новая структура (в том же формате, что _parse_plan_text).
    """
    if not agent_mode:
        try:
            from core.audit_project import _agent_for_role
            agent_mode = _agent_for_role("planning")
        except Exception:
            agent_mode = ""
    if not agent_mode:
        return {"ok": False, "plan": None, "text": "",
                "error": "роль planning не назначена"}

    plan_text = _plan_to_text(plan)
    hist_text = _history_to_text(history)

    prompt = (
        "Ты — планировщик. Обнови план доработки с учётом "
        "обсуждения ниже.\n\n"
        "ФОРМАТ ОТВЕТА (обязательно):\n"
        "  • Reasoning пиши ДО (если нужен).\n"
        "  • Потом строка «Ответ:» и СРАЗУ план.\n"
        "  • Начни с НАЗВАНИЕ: — один раз, не повторяй.\n"
        "  • Сохрани тот же формат (НАЗВАНИЕ, ЦЕЛЬ, ЭТАП, пункты).\n"
        "  • Формат пунктов: «- текст» (без букв и цифр в начале).\n\n"
        "ЗАПРЕЩЕНО:\n"
        "  • писать «Мы должны», «Let me», «The user».\n"
        "  • добавлять буквы «А1.», «Б2.» в текст пунктов.\n"
        "  • писать reasoning ПОСЛЕ маркера «Ответ:».\n\n"
        "ТЕКУЩИЙ ПЛАН:\n"
        f"{plan_text}\n\n"
        "ОБСУЖДЕНИЕ (что менять):\n"
        f"{hist_text}\n\n"
        "Верни ОБНОВЛЁННЫЙ план в том же формате."
    )

    try:
        from core.audit_project import _llm_call
        r = _llm_call(agent_mode, prompt, timeout=180)
    except Exception as e:
        return {"ok": False, "plan": None, "text": "",
                "error": str(e)[:200]}

    if not r.get("success"):
        return {"ok": False, "plan": None, "text": "",
                "error": r.get("error", "LLM error")[:200]}

    raw = (r.get("text") or "").strip()
    if not raw:
        return {"ok": False, "plan": None, "text": "",
                "error": "пустой ответ LLM"}

    new_plan = _parse_plan_text(raw)
    if not new_plan.get("stages"):
        return {"ok": False, "plan": None, "text": raw,
                "error": "не удалось распарсить план"}

    # PLAN-DIFF (2026-10-08): сравниваем со старым планом и помечаем.
    stats = _mark_diff(plan, new_plan)

    return {"ok": True, "plan": new_plan, "text": raw,
            "error": "", "diff": stats}

