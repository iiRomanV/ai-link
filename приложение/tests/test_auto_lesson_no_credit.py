"""AUTO-LESSON-NO-CREDIT (2026-10-03): auto_lesson не даёт credit.

Правило: одна удача — один учёт.
Паттерн создаётся, но credit получает только через rule_v1
при реальном использовании в fix.
"""
import sqlite3

from core.ai_link_db import get_db_path, init_schema
from core import memory_patterns as mp
from core.orchestrator import (
    _auto_create_pattern_from_context,
    OrchestrationContext,
    PHASE_FIX,
)


def _clean(db):
    con = sqlite3.connect(str(db))
    try:
        con.execute("DELETE FROM learning_patterns WHERE source = 'auto_fix'")
        con.execute(
            "DELETE FROM pattern_evidence "
            "WHERE attribution_method = 'auto_lesson'"
        )
        con.commit()
    finally:
        con.close()


def _ctx(task="encoding utf8 чтение файлов",
         problem="", fix_output="", fix_files=None,
         fix_agent="browser_deepseek"):
    """Создаёт контекст с заполненной phase_history."""
    ctx = OrchestrationContext(
        original_task=task,
        project_dir="/tmp",
    )
    ctx.pending_problem_review = problem
    if fix_output or fix_files:
        ctx.phase_history = [{
            "phase": PHASE_FIX,
            "agent": fix_agent,
            "output": fix_output,
            "files_created": fix_files or [],
        }]
    return ctx


def test_auto_lesson_creates_pattern():
    """Паттерн создаётся, попадает в active."""
    db = get_db_path()
    init_schema(db)
    _clean(db)

    ctx = _ctx(
        task="кириллица падает encoding",
        problem="Кириллица падает: 'cp1251' codec can't decode",
        fix_output="Добавил encoding='utf-8' во все read_text",
        fix_files=["smoke_tracker/export.py"],
    )
    pid = _auto_create_pattern_from_context(ctx, None)
    assert pid is not None
    pat = mp.get_pattern(pid, db)
    assert pat is not None
    # B+ (19.10.2026): 1.1.2 — candidate, не active
    assert pat["status"] == mp.STATUS_CANDIDATE
    assert pat["source"] == "auto_fix"


def test_auto_lesson_does_not_write_evidence():
    """AUTO-LESSON-NO-CREDIT: auto_lesson evidence НЕ пишется."""
    db = get_db_path()
    init_schema(db)
    _clean(db)

    ctx = _ctx(
        task="кириллица падает encoding",
        problem="Кириллица падает: 'cp1251' codec can't decode",
        fix_output="Добавил encoding='utf-8'",
        fix_files=["smoke_tracker/export.py"],
    )
    pid = _auto_create_pattern_from_context(ctx, None)
    assert pid is not None

    con = sqlite3.connect(str(db))
    try:
        cur = con.execute(
            "SELECT COUNT(*) FROM pattern_evidence "
            "WHERE pattern_id = ? AND attribution_method = 'auto_lesson'",
            (pid,),
        )
        count = cur.fetchone()[0]
    finally:
        con.close()

    assert count == 0, f"auto_lesson evidence записан ({count})"


def test_auto_lesson_alpha_stays_base():
    """AUTO-LESSON-NO-CREDIT: alpha=1.0 (base), нет credit."""
    db = get_db_path()
    init_schema(db)
    _clean(db)

    ctx = _ctx(
        task="кириллица падает encoding",
        problem="Кириллица падает: 'cp1251' codec can't decode",
        fix_output="Добавил encoding='utf-8'",
        fix_files=["smoke_tracker/export.py"],
    )
    pid = _auto_create_pattern_from_context(ctx, None)
    assert pid is not None

    # get_pattern может не включать alpha/success_count — читаем SQL.
    con = sqlite3.connect(str(db))
    try:
        cur = con.execute(
            "SELECT alpha, beta, success_count, evidence_count "
            "FROM learning_patterns WHERE id = ?",
            (pid,),
        )
        row = cur.fetchone()
    finally:
        con.close()

    assert row is not None
    alpha, beta, sc, ec = row
    assert abs(alpha - 1.0) < 0.01, f"alpha={alpha}, ожидалось 1.0"
    assert abs(beta - 1.0) < 0.01, f"beta={beta}, ожидалось 1.0"
    assert sc == 0, f"success_count={sc}, ожидалось 0"
    assert ec == 0, f"evidence_count={ec}, ожидалось 0"
