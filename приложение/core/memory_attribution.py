"""MEMORY-ATTRIBUTION — 5-уровневая модель credit assignment.

FIX ATTRIBUTION (2026-09-30):
  retrieved → eligible → used → supported → verified_effect
  Только verified_effect меняет confidence.

MVP (без LLM): правила + regex, работает локально.

Правила:
  retrieved  = паттерн был в applied_pattern_ids (FTS5 нашёл)
  eligible   = scope/phase/trigger_terms пересекаются с контекстом
  used       = изменённые файлы (changed_files) пересекаются
                с signature или expected_files
  supported  = used И следующий review вернул OK
  verified   = supported И VERIFY прошёл

Веса для Beta (в memory_patterns.record_evidence):
  verified_effect=True → S += 1.0
  used+supported=True  → S += 0.25
  иначе               → не обновляет confidence
"""
import re
from typing import Any, Dict, List, Optional, Set


# ─────────────────────────────────────────────────────────────
#  Keywords
# ─────────────────────────────────────────────────────────────

_STOPWORDS: Set[str] = {
    "the", "and", "for", "with", "this", "that", "from",
    "для", "что", "это", "как", "или", "при", "его", "её",
    "они", "был", "была", "все", "нет", "если", "то",
    "есть", "только", "нужно", "можно", "также", "было",
    "будет", "надо", "либо", "лишь", "очень", "ещё",
    "code", "file", "true", "false", "none",
    "может", "быть", "куда", "где", "чем", "тем", "этот",
}


def extract_keywords(text: str, min_len: int = 4,
                     max_n: int = 20) -> List[str]:
    """Извлекает значимые слова (4+ символа, без стоп-слов).

    Возвращает список уникальных слов в порядке появления.
    """
    if not text:
        return []
    words = re.findall(
        r"[A-Za-zА-Яа-яЁё_][A-Za-zА-Яа-яЁё0-9_]+",
        text.lower(),
    )
    seen: List[str] = []
    for w in words:
        if len(w) < min_len or w in _STOPWORDS or w in seen:
            continue
        seen.append(w)
        if len(seen) >= max_n:
            break
    return seen


# ─────────────────────────────────────────────────────────────
#  Pattern terms
# ─────────────────────────────────────────────────────────────

def _pattern_terms(pattern: Dict[str, Any]) -> Set[str]:
    """Собирает все значимые термы паттерна.

    Источники:
      • signature (split по '-', '_')
      • context_json (парсим "title", "tags")
      • fix (первые 500 симв)
    """
    import json as _json
    terms: Set[str] = set()

    sig = pattern.get("signature", "") or ""
    for w in re.split(r"[-_]", sig.lower()):
        if len(w) >= 4 and w not in _STOPWORDS:
            terms.add(w)

    ctx = pattern.get("context_json", "") or ""
    try:
        parsed = _json.loads(ctx) if ctx else {}
        if isinstance(parsed, dict):
            for k in ("title", "tags"):
                v = parsed.get(k, "") or ""
                for w in re.split(r"[,\s_\-]+", str(v).lower()):
                    if len(w) >= 4 and w not in _STOPWORDS:
                        terms.add(w)
    except Exception:
        pass

    fix = (pattern.get("fix") or "")[:500]
    for w in extract_keywords(fix, min_len=4, max_n=15):
        terms.add(w)

    return terms


# ─────────────────────────────────────────────────────────────
#  Eligibility score
# ─────────────────────────────────────────────────────────────

def eligibility_score(
    pattern: Dict[str, Any],
    phase: str,
    task_terms: Optional[Set[str]] = None,
    provider: str = "",
    component: str = "",
) -> float:
    """Оценка тематического соответствия паттерна и контекста.

    Возвращает 0.0 - 1.0.
    """
    score = 0.0

    # 1. Scope: provider
    pat_provider = (pattern.get("scope_value") or "").lower()
    pat_scope = (pattern.get("scope") or "global").lower()
    if pat_scope == "global":
        score += 0.10
    elif pat_provider and provider:
        if pat_provider == provider.lower():
            score += 0.25

    # 2. Trigger terms паттерна
    terms = _pattern_terms(pattern)
    if task_terms and terms:
        overlap = terms & {t.lower() for t in task_terms}
        # 5+ совпадений → +0.40; 1 совпадение → +0.08
        score += min(0.40, len(overlap) * 0.08)

    # 3. Component (имя файла) в signature/tags
    if component:
        comp_low = component.lower()
        if comp_low in (pattern.get("signature") or "").lower():
            score += 0.30
        elif comp_low in " ".join(terms):
            score += 0.20

    # 4. Phase (пока не храним per-pattern phase — бонус базовый)
    if phase in ("fix", "review"):
        score += 0.05

    return min(score, 1.0)


# ─────────────────────────────────────────────────────────────
#  Detect used
# ─────────────────────────────────────────────────────────────

def detect_used(
    pattern: Dict[str, Any],
    changed_files: Optional[List[str]] = None,
) -> float:
    """Проверяет, «использовался» ли паттерн в fix.

    Сигналы:
      • имя изменённого файла встречается в signature/terms
      • fix-строка содержит терм из changed_files

    Возвращает 0.0 - 1.0.
    """
    if not changed_files:
        return 0.0

    terms = _pattern_terms(pattern)
    if not terms:
        return 0.0

    score = 0.0
    for f in changed_files:
        f_low = f.lower()
        # Имя файла без пути
        f_name = f_low.split("/")[-1]
        f_stem = f_name.replace(".py", "").replace(".bas", "")
        if f_stem in terms:
            score += 0.5
        elif any(t in f_low for t in terms if len(t) >= 5):
            score += 0.3

    return min(score, 1.0)


# ─────────────────────────────────────────────────────────────
#  Classify evidence
# ─────────────────────────────────────────────────────────────

def classify_evidence(
    pattern: Dict[str, Any],
    phase: str,
    phase_status: str,
    problem_text: str = "",
    changed_files: Optional[List[str]] = None,
    provider: str = "",
    component: str = "",
    verify_passed: bool = False,
    next_review_ok: bool = False,
    eligibility_threshold: float = 0.35,
    used_threshold: float = 0.3,
) -> Dict[str, Any]:
    """Возвращает полный attribution-набор для record_evidence.

    Ключи: retrieved, eligible, used, supported, verified_effect,
    relevance_score, attribution_method.
    """
    # retrieved = True (мы уже знаем, что паттерн был в applied_pattern_ids)
    retrieved = True

    task_terms = set(extract_keywords(problem_text, max_n=15))
    elig = eligibility_score(
        pattern, phase,
        task_terms=task_terms,
        provider=provider,
        component=component,
    )
    eligible = elig >= eligibility_threshold

    used_score = detect_used(pattern, changed_files or [])
    used = used_score >= used_threshold

    # supported = used И следующий review дал OK
    supported = bool(used and next_review_ok)

    # FIX ATTRIBUTION-GATE (2026-09-30): verified_effect требует
    # eligible=True. Иначе нерелевантный паттерн (случайно совпавший
    # по файлу) получает полный credit → inflation.
    verified = bool(supported and verify_passed and eligible)

    return {
        "retrieved": retrieved,
        "eligible": eligible,
        "used": used,
        "supported": supported,
        "verified_effect": verified,
        "relevance_score": round(elig, 3),
        "attribution_method": "rule_v1",
    }
