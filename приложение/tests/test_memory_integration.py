"""Тесты MEMORY-INTEGRATION (паттерны в промпте оркестратора)."""
import sqlite3
from pathlib import Path

from core.orchestrator import (
    _build_text_prompt, _build_file_task,
    OrchestrationContext, PHASE_PLAN, PHASE_CODE, PHASE_REVIEW, PHASE_FIX,
)
from core import memory_api as mem
from core.ai_link_db import init_schema, get_db_path


def _ctx(task="тест", project_dir="/tmp"):
    return OrchestrationContext(
        original_task=task,
        project_dir=project_dir,
    )


def _clean_test_pattern(db):
    """Убирает тестовые паттерны."""
    con = sqlite3.connect(str(db))
    try:
        con.execute(
            "DELETE FROM learning_patterns "
            "WHERE signature LIKE 'test-int-%'"
        )
        con.commit()
    finally:
        con.close()


def _add_active_pattern(db, sig="test-int-1"):
    """Создаёт активный паттерн. Возвращает id."""
    r = mem.remember_lesson(
        signature=sig,
        title=f"Title {sig}",
        content=f"Content {sig}",
        fix=f"Fix {sig}",
        db_path=db,
    )
    mem.promote(r["pattern_id"], db)
    return r["pattern_id"]


# --------------------------------------------------------------------------- #
# базовые (без паттернов)
# --------------------------------------------------------------------------- #
def test_text_prompt_no_patterns():
    """Без паттернов в памяти — промпт без блока MEMORY."""
    ctx = _ctx()
    p = _build_text_prompt(ctx, PHASE_PLAN)
    assert "=== MEMORY" not in p
    assert ctx.applied_pattern_ids == []


def test_file_task_no_patterns():
    ctx = _ctx()
    t = _build_file_task(ctx, PHASE_CODE)
    assert "=== MEMORY" not in t
    assert ctx.applied_pattern_ids == []


# --------------------------------------------------------------------------- #
# с паттернами (реальная БД)
# --------------------------------------------------------------------------- #
def test_text_prompt_with_pattern():
    """Активный паттерн — попадает в промпт plan."""
    db = get_db_path()
    init_schema(db)
    _clean_test_pattern(db)
    pid = _add_active_pattern(db, "test-int-1")
    try:
        ctx = _ctx()
        p = _build_text_prompt(ctx, PHASE_PLAN)
        assert "=== MEMORY" in p, "блок MEMORY не найден"
        assert "test-int-1" in p, "signature паттерна не найден"
        assert pid in ctx.applied_pattern_ids, "id не сохранён в context"
    finally:
        _clean_test_pattern(db)


def test_file_task_with_pattern():
    """Активный паттерн — попадает в промпт code."""
    db = get_db_path()
    init_schema(db)
    _clean_test_pattern(db)
    pid = _add_active_pattern(db, "test-int-2")
    try:
        ctx = _ctx()
        t = _build_file_task(ctx, PHASE_CODE)
        assert "=== MEMORY" in t
        assert "test-int-2" in t
        assert pid in ctx.applied_pattern_ids
    finally:
        _clean_test_pattern(db)


def test_review_prompt_with_pattern():
    """Review тоже получает паттерны."""
    db = get_db_path()
    init_schema(db)
    _clean_test_pattern(db)
    pid = _add_active_pattern(db, "test-int-3")
    try:
        ctx = _ctx()
        p = _build_text_prompt(ctx, PHASE_REVIEW)
        assert "=== MEMORY" in p
        assert pid in ctx.applied_pattern_ids
    finally:
        _clean_test_pattern(db)


def test_fix_task_with_pattern():
    """Fix получает паттерны."""
    db = get_db_path()
    init_schema(db)
    _clean_test_pattern(db)
    pid = _add_active_pattern(db, "test-int-4")
    try:
        ctx = _ctx()
        t = _build_file_task(ctx, PHASE_FIX)
        assert "=== MEMORY" in t
        assert pid in ctx.applied_pattern_ids
    finally:
        _clean_test_pattern(db)


def test_candidate_not_in_prompt():
    """Candidate-паттерн НЕ попадает в промпт (только active)."""
    db = get_db_path()
    init_schema(db)
    _clean_test_pattern(db)
    # Создаём паттерн БЕЗ promote (остаётся candidate)
    r = mem.remember_lesson(
        signature="test-int-candidate",
        title="Should not appear",
        content="candidate",
        fix="candidate fix",
        db_path=db,
    )
    try:
        ctx = _ctx()
        p = _build_text_prompt(ctx, PHASE_PLAN)
        assert "test-int-candidate" not in p
        assert ctx.applied_pattern_ids == []
    finally:
        _clean_test_pattern(db)


def test_multiple_patterns_in_prompt():
    """Несколько active паттернов — все в промпте."""
    db = get_db_path()
    init_schema(db)
    _clean_test_pattern(db)
    pids = []
    for i in range(3):
        pids.append(_add_active_pattern(db, f"test-int-m{i}"))
    try:
        ctx = _ctx()
        p = _build_text_prompt(ctx, PHASE_PLAN)
        for pid in pids:
            assert pid in ctx.applied_pattern_ids
        assert "=== MEMORY (3 patterns) ===" in p
    finally:
        _clean_test_pattern(db)


def test_applied_pattern_ids_reset_on_new_prompt():
    """Второй вызов _build_*_prompt перезаписывает список.

    После retire НАШ паттерн не должен быть в списке.
    (Другие active паттерны в реальной БД — допустимы.)
    """
    db = get_db_path()
    init_schema(db)
    _clean_test_pattern(db)
    pid1 = _add_active_pattern(db, "test-int-r1")
    try:
        ctx = _ctx()
        _build_text_prompt(ctx, PHASE_PLAN)
        assert pid1 in ctx.applied_pattern_ids

        # Убираем НАШ паттерн (retire) → в новом списке его быть не должно
        mem.retire(pid1, db)
        _build_text_prompt(ctx, PHASE_PLAN)
        assert pid1 not in ctx.applied_pattern_ids
    finally:
        _clean_test_pattern(db)


# --------------------------------------------------------------------------- #
# record_outcome (после фазы)
# --------------------------------------------------------------------------- #
def test_record_outcome_success_updates_confidence():
    """record_outcome success → confidence растёт."""
    db = get_db_path()
    init_schema(db)
    _clean_test_pattern(db)
    pid = _add_active_pattern(db, "test-int-out1")
    try:
        from core import memory_patterns as mp
        before = mp.get_pattern(pid, db)
        # Записываем 2 успеха через memory_api (как делает оркестратор)
        mem.record_outcome(pid, "success", run_id="r1",
                           model="browser_deepseek", injected=True,
                           db_path=db)
        mem.record_outcome(pid, "success", run_id="r2",
                           model="browser_deepseek", injected=True,
                           db_path=db)
        after = mp.get_pattern(pid, db)
        assert after["success_count"] == 2
        assert after["evidence_count"] == 2
        assert after["confidence"] > before["confidence"]
    finally:
        _clean_test_pattern(db)


def test_record_outcome_failure_lowers_confidence():
    """FIX 1.1.1 (17.10.2026): failure+used=True → confidence падает.

    Раньше: failure без used → legacy weight_f=1.0 (штраф).
    Теперь: штраф только при used=True. Это правильно — паттерн
    применили и не помогло, значит плохой.
    """
    db = get_db_path()
    init_schema(db)
    _clean_test_pattern(db)
    pid = _add_active_pattern(db, "test-int-out2")
    try:
        from core import memory_patterns as mp
        # Сначала успех (legacy → alpha += 1.0)
        mem.record_outcome(pid, "success", db_path=db)
        conf_mid = mp.get_pattern(pid, db)["confidence"]
        # Потом провал, паттерн БЫЛ использован → штраф
        mem.record_outcome(pid, "failure", used=True, db_path=db)
        conf_after = mp.get_pattern(pid, db)["confidence"]
        assert conf_after < conf_mid
    finally:
        _clean_test_pattern(db)


def test_record_outcome_failure_without_used_soft_penalty():
    """B+ (19.10.2026): failure+used=False → МЯГКИЙ штраф (0.1).

    Раньше confidence не падал. Теперь — мягко падает (weight_f=0.1).
    """
    db = get_db_path()
    init_schema(db)
    _clean_test_pattern(db)
    pid = _add_active_pattern(db, "test-int-out3")
    try:
        from core import memory_patterns as mp
        mem.record_outcome(pid, "success", db_path=db)
        conf_mid = mp.get_pattern(pid, db)["confidence"]
        # Провал БЕЗ used → мягкий штраф
        mem.record_outcome(pid, "failure", used=False, db_path=db)
        conf_after = mp.get_pattern(pid, db)["confidence"]
        assert conf_after < conf_mid, (
            f"conf должен упасть: {conf_mid} -> {conf_after}"
        )
        # Мягко: падение < 0.05
        assert (conf_mid - conf_after) < 0.05
    finally:
        _clean_test_pattern(db)


def test_context_has_applied_pattern_ids_field():
    """OrchestrationContext имеет поле applied_pattern_ids."""
    ctx = _ctx()
    assert hasattr(ctx, "applied_pattern_ids")
    assert isinstance(ctx.applied_pattern_ids, list)
    assert ctx.applied_pattern_ids == []
