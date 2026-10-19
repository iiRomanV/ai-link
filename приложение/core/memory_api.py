"""MEMORY-API — единый фасад над memory_search + memory_patterns.

FIX MEMORY-V1 (2026-09-30):
  Мозг должен дёргать ОДИН модуль, не помнить про 2.
  Все операции памяти — через этот фасад.

API:
  # Запись
  remember_lesson(...)         — сохранить урок (FTS + pattern)
  record_outcome(...)          — записать результат применения

  # Чтение
  find_similar(...)            — поиск по FTS5
  get_active_patterns(...)     — топ активных паттернов
  render_for_prompt(...)       — текст для вставки в промпт

  # Управление
  promote/demote/retire/quarantine(pattern_id)
  enforce_caps()
  auto_retire()
  stats()

  # Инициализация
  init()
"""
from pathlib import Path
from typing import Any, Dict, List, Optional

from core import memory_search as ms
from core import memory_patterns as mp


# ─────────────────────────────────────────────────────────────
#  init
# ─────────────────────────────────────────────────────────────

def init(db_path: Optional[Path] = None) -> None:
    """Инициализация: создать схему, если ещё нет."""
    from core.ai_link_db import init_schema
    init_schema(db_path)


# ─────────────────────────────────────────────────────────────
#  запись
# ─────────────────────────────────────────────────────────────

def remember_lesson(
    signature: str,
    title: str,
    content: str,
    fix: str = "",
    scope: str = "global",
    scope_value: str = "",
    tags: str = "",
    source: str = "observed",
    salience: float = 0.5,
    db_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Сохраняет урок: запись в FTS5 + pattern в state machine.

    Возвращает {"pattern_id": int|None, "memory_id": str, "ok": bool}.

    Идемпотентно: повторный вызов с тем же signature не задваивает.
    """
    memory_id = f"lesson:{signature}"

    # 1. Индекс в FTS5
    fts_ok = ms.index_memory(
        memory_id=memory_id,
        kind="lesson",
        scope=scope if not scope_value else f"{scope}:{scope_value}",
        title=title,
        content=content,
        tags=tags,
        db_path=db_path,
    )

    # 2. Pattern в state machine
    pattern_id = mp.add_pattern(
        signature=signature,
        context_json=f'{{"title": "{title}", "tags": "{tags}"}}',
        fix=fix,
        scope=scope,
        scope_value=scope_value,
        source=source,
        salience=salience,
        db_path=db_path,
    )

    return {
        "ok": fts_ok and pattern_id is not None,
        "pattern_id": pattern_id,
        "memory_id": memory_id,
    }


def record_outcome(
    pattern_id: int,
    outcome: str,
    run_id: str = "",
    model: str = "",
    injected: bool = False,
    verification_json: str = "",
    # FIX ATTRIBUTION (2026-09-30)
    phase: str = "",
    problem_keywords: str = "",
    relevance_score: float = 0.0,
    retrieved: bool = False,
    eligible: bool = False,
    used: bool = False,
    supported: bool = False,
    verified_effect: bool = False,
    attribution_method: str = "",
    db_path: Optional[Path] = None,
) -> bool:
    """Записывает результат применения паттерна.

    outcome: "success" | "failure" | "neutral"

    FIX ATTRIBUTION (2026-09-30): новые опциональные аргументы
    для 5-уровневой модели.
    """
    return mp.record_evidence(
        pattern_id=pattern_id,
        outcome=outcome,
        run_id=run_id,
        model=model,
        injected=injected,
        verification_json=verification_json,
        phase=phase,
        problem_keywords=problem_keywords,
        relevance_score=relevance_score,
        retrieved=retrieved,
        eligible=eligible,
        used=used,
        supported=supported,
        verified_effect=verified_effect,
        attribution_method=attribution_method,
        db_path=db_path,
    )


# ─────────────────────────────────────────────────────────────
#  чтение
# ─────────────────────────────────────────────────────────────

def find_similar(
    query: str,
    scope: Optional[str] = None,
    kind: Optional[str] = None,
    limit: int = 10,
    db_path: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    """Поиск похожих уроков/паттернов по FTS5.

    Возвращает список dict-ов с score.
    """
    return ms.search(
        query=query,
        scope=scope,
        kind=kind,
        limit=limit,
        db_path=db_path,
    )


def get_active_patterns(
    scope: Optional[str] = None,
    project_name: str = "",
    limit: int = mp.CAP_PROMPT,
    db_path: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    """Топ-N активных паттернов для инъекции в промпт.

    MEMORY-PROJECT-SCOPE (2026-10-11): project_name — фильтр
    по проекту (global + project:<имя>).
    """
    return mp.get_for_prompt(
        scope=scope,
        project_name=project_name,
        limit=limit,
        db_path=db_path,
    )


def render_for_prompt(
    project_name: str = "",
    scope: Optional[str] = None,
    limit: int = mp.CAP_PROMPT,
    db_path: Optional[Path] = None,
    max_chars: int = 2000,
) -> str:
    """Готовый текст для вставки в промпт.

    Формат:
      === MEMORY (N patterns) ===
      [pattern:sig1] fix1
      [pattern:sig2] fix2
      ...
      ========================
    Возвращает "" если паттернов нет.
    """
    # MEMORY-PROJECT-SCOPE (2026-10-11): project_name отдельным
    # параметром. get_for_prompt отфильтрует global + project:<имя>.
    pats = get_active_patterns(
        scope=scope,
        project_name=project_name or "",
        limit=limit,
        db_path=db_path,
    )
    if not pats:
        return ""

    lines = [f"=== MEMORY ({len(pats)} patterns) ==="]
    for p in pats:
        sig = p.get("signature", "")[:80]
        fix = (p.get("fix") or "").strip().replace("\n", " ")[:160]
        conf = p.get("confidence", 0.0)
        if fix:
            lines.append(f"[{sig}] (conf={conf:.2f}) {fix}")
        else:
            lines.append(f"[{sig}] (conf={conf:.2f})")

    text = "\n".join(lines) + "\n========================"
    if len(text) > max_chars:
        text = text[:max_chars - 30] + "\n... [обрезано]"
    return text


# ─────────────────────────────────────────────────────────────
#  управление
# ─────────────────────────────────────────────────────────────

def promote(pattern_id: int,
            db_path: Optional[Path] = None) -> bool:
    return mp.promote(pattern_id, db_path)


def promote_if_ready(pattern_id: int,
                     db_path: Optional[Path] = None) -> bool:
    return mp.promote_if_ready(pattern_id, db_path)


def demote(pattern_id: int,
           db_path: Optional[Path] = None) -> bool:
    return mp.demote(pattern_id, db_path)


def retire(pattern_id: int,
           db_path: Optional[Path] = None) -> bool:
    return mp.retire(pattern_id, db_path)


def quarantine(pattern_id: int,
               db_path: Optional[Path] = None) -> bool:
    return mp.quarantine(pattern_id, db_path)


def enforce_caps(db_path: Optional[Path] = None) -> Dict[str, int]:
    return mp.enforce_caps(db_path)


def auto_retire(db_path: Optional[Path] = None) -> Dict[str, int]:
    return mp.auto_retire(db_path=db_path)


def stats(db_path: Optional[Path] = None) -> Dict[str, Any]:
    """Общая сводка: patterns + FTS count."""
    out = mp.stats(db_path)
    out["fts_count"] = ms.count(db_path)
    return out
