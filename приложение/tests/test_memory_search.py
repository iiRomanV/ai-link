"""Тесты MEMORY-SEARCH (core.memory_search)."""

import sqlite3

from core.ai_link_db import init_schema
from core import memory_search as ms


def _db(tmp_path):
    p = tmp_path / "memory.db"
    init_schema(p)
    return p


# --------------------------------------------------------------------------- #
# score / normalize
# --------------------------------------------------------------------------- #
def test_normalize_bm25_bounds():
    assert ms._normalize_bm25(0.0) == 0.0
    assert ms._normalize_bm25(5.0) == 0.0  # положительный не бывает, но пусть
    assert ms._normalize_bm25(-1.0) > 0.0
    assert ms._normalize_bm25(-10.0) == 1.0
    assert ms._normalize_bm25(-100.0) == 1.0


def test_recency_score_recent():
    from datetime import datetime
    now = datetime.now().isoformat()
    assert ms._recency_score(now) > 0.9


def test_recency_score_old():
    assert ms._recency_score("2020-01-01T00:00:00") == 0.0


def test_recency_score_empty():
    assert ms._recency_score("") == 0.5


def test_score_formula():
    # все максимумы → 1.0
    s = ms.score(bm25=-10.0, confidence=1.0, recency=1.0, salience=1.0)
    assert abs(s - 1.0) < 0.001
    # все минимумы → 0.0
    s = ms.score(bm25=0.0, confidence=0.0, recency=0.0, salience=0.0)
    assert s == 0.0


# --------------------------------------------------------------------------- #
# safe query
# --------------------------------------------------------------------------- #
def test_safe_fts_query_empty():
    assert ms._safe_fts_query("") == ""
    assert ms._safe_fts_query("   ") == ""


def test_safe_fts_query_removes_special():
    q = ms._safe_fts_query('playwright OR "sqlite"')
    assert "playwright" in q
    assert "sqlite" in q
    assert '"' in q  # мы добавляем кавычки


# --------------------------------------------------------------------------- #
# index + search
# --------------------------------------------------------------------------- #
def test_index_and_count(tmp_path):
    p = _db(tmp_path)
    ok = ms.index_memory("m1", "lesson", "global",
                         "Playwright timeout", "networkidle зависает",
                         tags="playwright", db_path=p)
    assert ok is True
    assert ms.count(p) == 1


def test_index_idempotent(tmp_path):
    p = _db(tmp_path)
    ms.index_memory("m1", "lesson", "global", "T1", "C1", db_path=p)
    ms.index_memory("m1", "lesson", "global", "T2", "C2", db_path=p)
    assert ms.count(p) == 1  # не задваивает


def test_search_finds(tmp_path):
    p = _db(tmp_path)
    ms.index_memory("m1", "lesson", "global", "Playwright timeout",
                    "networkidle зависает", db_path=p)
    ms.index_memory("m2", "lesson", "global", "SQLite lock",
                    "БД блокируется", db_path=p)
    r = ms.search("playwright", db_path=p)
    assert len(r) == 1
    assert r[0]["memory_id"] == "m1"


def test_search_empty_query(tmp_path):
    p = _db(tmp_path)
    ms.index_memory("m1", "lesson", "global", "Test", "content", db_path=p)
    r = ms.search("", db_path=p)
    assert r == []


def test_search_no_results(tmp_path):
    p = _db(tmp_path)
    ms.index_memory("m1", "lesson", "global", "Test", "content", db_path=p)
    r = ms.search("nonexistent_word_xyz", db_path=p)
    assert r == []


def test_search_scope_filter(tmp_path):
    p = _db(tmp_path)
    ms.index_memory("m1", "lesson", "global", "Playwright T", "x",
                    db_path=p)
    ms.index_memory("m2", "lesson", "provider:qwen", "Playwright T", "x",
                    db_path=p)
    r = ms.search("playwright", scope="provider:qwen", db_path=p)
    assert len(r) == 1
    assert r[0]["memory_id"] == "m2"


def test_search_kind_filter(tmp_path):
    p = _db(tmp_path)
    ms.index_memory("m1", "lesson", "global", "Playwright T", "x",
                    db_path=p)
    ms.index_memory("m2", "pattern", "global", "Playwright T", "x",
                    db_path=p)
    r = ms.search("playwright", kind="pattern", db_path=p)
    assert len(r) == 1
    assert r[0]["memory_id"] == "m2"


def test_search_limit(tmp_path):
    p = _db(tmp_path)
    for i in range(5):
        ms.index_memory(f"m{i}", "lesson", "global",
                        f"Playwright timeout {i}",
                        f"networkidle {i}", db_path=p)
    r = ms.search("playwright", limit=2, db_path=p)
    assert len(r) == 2


def test_search_has_score(tmp_path):
    p = _db(tmp_path)
    ms.index_memory("m1", "lesson", "global", "Playwright T", "x",
                    db_path=p)
    r = ms.search("playwright", db_path=p)
    assert r[0]["score"] > 0
    assert r[0]["bm25"] < 0


def test_clear(tmp_path):
    p = _db(tmp_path)
    ms.index_memory("m1", "lesson", "global", "Test", "x", db_path=p)
    assert ms.count(p) == 1
    ms.clear(p)
    assert ms.count(p) == 0
