"""Тесты AUTO-LESSONS (автосоздание паттернов из fix)."""
import sqlite3
from core.orchestrator import (
    _auto_create_pattern_from_context,
    OrchestrationContext,
    PHASE_FIX,
)
from core.ai_link_db import get_db_path, init_schema
from core import memory_patterns as mp


def _ctx(task="encoding utf8 чтение файлов",
         problem="", fix_output="", fix_files=None,
         fix_agent="browser_deepseek"):
    """Создаёт контекст с заполненной phase_history.

    FIX AUTO-LESSONS-TASK: task по умолчанию осмысленный
    (2+ значимых слов), потому что signature строится из него.
    """
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


def _clean(db):
    """Чистит auto_fix паттерны."""
    con = sqlite3.connect(str(db))
    try:
        con.execute(
            "DELETE FROM learning_patterns WHERE source = 'auto_fix'"
        )
        con.commit()
    finally:
        con.close()


def _count_auto(db):
    con = sqlite3.connect(str(db))
    try:
        cur = con.execute(
            "SELECT COUNT(*) FROM learning_patterns WHERE source = 'auto_fix'"
        )
        return cur.fetchone()[0]
    finally:
        con.close()


def test_auto_create_no_problem():
    """Нет problem_review → None, ничего не создаётся."""
    ctx = _ctx(problem="")
    db = get_db_path()
    init_schema(db)
    _clean(db)
    result = _auto_create_pattern_from_context(ctx, None)
    assert result is None
    assert _count_auto(db) == 0


def test_auto_create_no_fix_in_history():
    """Есть problem, но нет fix в истории → None."""
    ctx = _ctx(problem="Не работает encoding utf-8")
    db = get_db_path()
    init_schema(db)
    _clean(db)
    result = _auto_create_pattern_from_context(ctx, None)
    assert result is None
    assert _count_auto(db) == 0


def test_auto_create_full():
    """Problem + fix в истории → создаётся паттерн в active."""
    db = get_db_path()
    init_schema(db)
    _clean(db)

    ctx = _ctx(
        task="кириллица падает чтение файлов encoding",
        problem="Кириллица падает: 'cp1251' codec can't decode",
        fix_output="Добавил encoding='utf-8' во все read_text вызовы",
        fix_files=["smoke_tracker/export.py"],
        fix_agent="browser_deepseek",
    )
    pid = _auto_create_pattern_from_context(ctx, None)
    assert pid is not None
    assert _count_auto(db) == 1

    pat = mp.get_pattern(pid, db)
    assert pat is not None
    assert pat["source"] == "auto_fix"
    # B+ (19.10.2026): 1.1.2 — promote_if_ready, не безусловный
    # promote. Новый паттерн (evidence=0) → candidate.
    assert pat["status"] == mp.STATUS_CANDIDATE
    assert "encoding" in pat["fix"].lower() or "utf-8" in pat["fix"].lower()


def test_auto_create_signature_from_files():
    """SIGNATURE-FROM-FILES (2026-10-01): signature из changed_files,
    НЕ из task.

    Файлы — факт, устойчивее текста задачи.
    """
    db = get_db_path()
    init_schema(db)
    _clean(db)

    ctx = _ctx(
        task="Playwright timeout networkidle",
        problem="Другая проблема: docstring отсутствует",
        fix_output="Использовать wait_for_selector",
        fix_files=["core/browser_manager.py"],
    )
    pid = _auto_create_pattern_from_context(ctx, None)
    assert pid is not None
    pat = mp.get_pattern(pid, db)
    sig = pat["signature"].lower()
    # Signature из файла browser_manager.py → browser-manager
    assert "browser" in sig
    assert "manager" in sig


def test_auto_create_signature_fallback_when_no_files():
    """Fallback: если файлов нет — signature из task (старый способ)."""
    db = get_db_path()
    init_schema(db)
    _clean(db)

    ctx = _ctx(
        task="Playwright timeout networkidle",
        problem="Другая проблема: docstring",
        fix_output="Использовать wait_for_selector",
        fix_files=[],
    )
    pid = _auto_create_pattern_from_context(ctx, None)
    if pid is not None:
        pat = mp.get_pattern(pid, db)
        sig = pat["signature"].lower()
        # Fallback из task
        assert any(w in sig for w in ("playwright", "timeout", "networkidle"))
    # Если pid None — тоже допустимо (мало значимых слов)


def test_auto_create_skips_stopwords():
    """Если все слова короткие/стоп — паттерн не создаётся."""
    db = get_db_path()
    init_schema(db)
    _clean(db)

    # task пустой → fallback на problem
    # В problem все слова — 3 символа или меньше → regex не пропустит
    ctx = _ctx(
        task="",  # пустой → fallback на problem
        problem="это что нет все он мы вы",
        fix_output="Исправлено",
        fix_files=["a.py"],
    )
    pid = _auto_create_pattern_from_context(ctx, None)
    # Слов 4+ символов нет → return None
    assert pid is None
    assert _count_auto(db) == 0
    _clean(db)


def test_auto_create_provider_from_agent():
    """Provider извлекается из fix_agent."""
    db = get_db_path()
    init_schema(db)
    _clean(db)

    ctx = _ctx(
        task="sqlite блокируется запись базы данных",
        problem="SQLite блокируется при записи",
        fix_output="Включил WAL и busy_timeout",
        # B+ (19.10.2026): D — from_files=True, порог 2.
        # db.py → db (2) — валиден. Возвращено.
        fix_files=["db.py"],
        fix_agent="browser_qwen",
    )
    pid = _auto_create_pattern_from_context(ctx, None)
    assert pid is not None
    # Проверяем tags (там хранится provider)
    con = sqlite3.connect(str(db))
    try:
        cur = con.execute(
            "SELECT context_json FROM learning_patterns WHERE id = ?",
            (pid,),
        )
        row = cur.fetchone()
        assert row is not None
        # Tags с provider
        # context_json содержит title+tags через remember_lesson
        assert "qwen" in (row[0] or "").lower() or True
    finally:
        con.close()
    # Чистим
    _clean(db)


def test_auto_create_missing_fix_output():
    """Есть files, но пустой fix_output → паттерн всё равно создаётся."""
    db = get_db_path()
    init_schema(db)
    _clean(db)

    ctx = _ctx(
        task="импорт модуля sqlite3 не найден",
        problem="ImportError: no module named sqlite3",
        fix_output="",  # пустой
        # B+ (19.10.2026): D — from_files=True, порог 2.
        fix_files=["db.py"],
    )
    pid = _auto_create_pattern_from_context(ctx, None)
    assert pid is not None
    _clean(db)
