"""Тесты SQLITE-INDEX (ai_link_db)."""

import json
import sqlite3
from pathlib import Path

from core import ai_link_db as db


def _db(tmp_path):
    """Изолированная БД в tmp."""
    return tmp_path / "index.db"


def _mk_project(tmp_path, events=None):
    p = tmp_path / "proj"
    (p / ".ai_link").mkdir(parents=True, exist_ok=True)
    if events:
        lines = [json.dumps(e, ensure_ascii=False) for e in events]
        (p / ".ai_link" / "events.jsonl").write_text(
            "\n".join(lines) + "\n", encoding="utf-8")
    return p


def test_init_schema(tmp_path):
    path = db.init_schema(_db(tmp_path))
    assert path.exists()
    import sqlite3
    con = sqlite3.connect(str(path))
    cur = con.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")
    names = {r[0] for r in cur.fetchall()}
    con.close()
    for t in ("runs", "events", "phases", "model_calls",
              "tool_calls", "reviews"):
        assert t in names


def test_ingest_empty(tmp_path):
    p = _mk_project(tmp_path)
    r = db.ingest_events(str(p), db_path=_db(tmp_path))
    assert r["added"] == 0
    assert r["total"] == 0


def test_ingest_basic(tmp_path):
    p = _mk_project(tmp_path, events=[
        {"ts": "2026-09-22T12:00:00", "run_id": "r1",
         "phase_id": "plan", "iteration": 0,
         "event_type": "run_started", "payload_json": {"task": "demo"}},
        {"ts": "2026-09-22T12:00:01", "run_id": "r1",
         "phase_id": "plan", "iteration": 0,
         "event_type": "phase_started", "payload_json": {}},
        {"ts": "2026-09-22T12:00:02", "run_id": "r1",
         "phase_id": "code", "iteration": 3,
         "event_type": "phase_completed", "payload_json": {}},
    ])
    r = db.ingest_events(str(p), db_path=_db(tmp_path))
    assert r["added"] == 3
    assert r["skipped"] == 0
    assert r["total"] == 3


def test_ingest_idempotent(tmp_path):
    p = _mk_project(tmp_path, events=[
        {"ts": "2026-09-22T12:00:00", "run_id": "r1",
         "phase_id": "plan", "iteration": 0,
         "event_type": "run_started", "payload_json": {}},
    ])
    path = _db(tmp_path)
    db.ingest_events(str(p), db_path=path)
    r2 = db.ingest_events(str(p), db_path=path)
    assert r2["added"] == 0
    assert r2["skipped"] == 1


def test_save_and_list_runs(tmp_path):
    path = _db(tmp_path)
    db.save_run("r-1", "/proj/a", task="demo",
                status="success", final_phase="review",
                phases_done=3, seconds=42.5,
                started_at="2026-09-22T12:00:00",
                db_path=path)
    runs = db.list_runs(db_path=path)
    assert len(runs) == 1
    assert runs[0]["run_id"] == "r-1"
    assert runs[0]["phases_done"] == 3


def test_save_run_upsert(tmp_path):
    path = _db(tmp_path)
    db.save_run("r-1", "/proj/a", status="running",
                started_at="2026-09-22T12:00:00", db_path=path)
    db.save_run("r-1", "/proj/a", status="success",
                phases_done=5, db_path=path)
    runs = db.list_runs(db_path=path)
    assert len(runs) == 1
    assert runs[0]["status"] == "success"
    assert runs[0]["phases_done"] == 5


def test_query_events(tmp_path):
    p = _mk_project(tmp_path, events=[
        {"ts": "2026-09-22T12:00:00", "run_id": "r1",
         "phase_id": "plan", "event_type": "run_started",
         "payload_json": {"task": "x"}},
        {"ts": "2026-09-22T12:00:01", "run_id": "r2",
         "phase_id": "code", "event_type": "run_started",
         "payload_json": {}},
    ])
    path = _db(tmp_path)
    db.ingest_events(str(p), db_path=path)
    evs = db.query_events("r1", db_path=path)
    assert len(evs) == 1
    assert evs[0]["event_type"] == "run_started"
    assert evs[0]["payload"]["task"] == "x"


def test_stats(tmp_path):
    p = _mk_project(tmp_path, events=[
        {"ts": "2026-09-22T12:00:00", "run_id": "r1",
         "event_type": "run_started", "payload_json": {}},
    ])
    path = _db(tmp_path)
    db.ingest_events(str(p), db_path=path)
    s = db.stats(db_path=path)
    assert s["exists"] is True
    assert s["events"] == 1


def test_reset_index(tmp_path):
    path = _db(tmp_path)
    db.init_schema(path)
    db.save_run("r-1", "/p", db_path=path)
    assert db.stats(db_path=path)["runs"] == 1
    db.reset_index(path)
    s = db.stats(db_path=path)
    assert s.get("runs") is None or s.get("runs") == 0


def test_missing_project_dir(tmp_path):
    r = db.ingest_events("/tmp/__no_such_proj__", db_path=_db(tmp_path))
    assert r["added"] == 0


# --------------------------------------------------------------------------- #
# FIX MEMORY-V1 (2026-09-30): FTS5, session_summaries, pattern_evidence
# --------------------------------------------------------------------------- #
def test_memory_fts_exists(tmp_path):
    """FTS5-таблица memory_fts создаётся."""
    db_file = tmp_path / "test.db"
    db.init_schema(db_file)
    con = sqlite3.connect(str(db_file))
    try:
        cur = con.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name='memory_fts'"
        )
        assert cur.fetchone() is not None
    finally:
        con.close()


def test_memory_fts_insert_search(tmp_path):
    """FTS5 работает: вставка + поиск."""
    db_file = tmp_path / "test.db"
    db.init_schema(db_file)
    con = sqlite3.connect(str(db_file))
    try:
        con.execute(
            "INSERT INTO memory_fts "
            "(memory_id, kind, scope, title, content, tags) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("m1", "lesson", "global", "Playwright timeout",
             "Браузер зависает на networkidle", "playwright,browser"),
        )
        con.execute(
            "INSERT INTO memory_fts "
            "(memory_id, kind, scope, title, content, tags) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("m2", "lesson", "global", "SQLite lock",
             "БД блокируется при параллельной записи", "sqlite,db"),
        )
        con.commit()
        cur = con.execute(
            "SELECT memory_id FROM memory_fts "
            "WHERE memory_fts MATCH ? ORDER BY rank LIMIT 5",
            ("playwright",),
        )
        rows = cur.fetchall()
        assert len(rows) == 1
        assert rows[0][0] == "m1"
    finally:
        con.close()


def test_session_summaries_exists(tmp_path):
    """Таблица session_summaries создаётся."""
    db_file = tmp_path / "test.db"
    db.init_schema(db_file)
    con = sqlite3.connect(str(db_file))
    try:
        cur = con.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name='session_summaries'"
        )
        assert cur.fetchone() is not None
        con.execute(
            "INSERT INTO session_summaries "
            "(id, project_id, run_id, summary_json, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            ("s1", "proj1", "run1",
             '{"goal":"test","done":[]}', "2026-09-30T08:00:00"),
        )
        con.commit()
        cur = con.execute("SELECT COUNT(*) FROM session_summaries")
        assert cur.fetchone()[0] == 1
    finally:
        con.close()


def test_pattern_evidence_exists(tmp_path):
    """Таблица pattern_evidence создаётся."""
    db_file = tmp_path / "test.db"
    db.init_schema(db_file)
    con = sqlite3.connect(str(db_file))
    try:
        cur = con.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name='pattern_evidence'"
        )
        assert cur.fetchone() is not None
    finally:
        con.close()


def test_learning_patterns_has_new_columns(tmp_path):
    """learning_patterns имеет новые колонки (memory-v1)."""
    db_file = tmp_path / "test.db"
    db.init_schema(db_file)
    con = sqlite3.connect(str(db_file))
    try:
        cur = con.execute("PRAGMA table_info(learning_patterns)")
        cols = {row[1] for row in cur.fetchall()}
        # Старые
        assert "signature" in cols
        assert "fix" in cols
        # Новые (memory-v1)
        assert "status" in cols
        assert "scope" in cols
        assert "scope_value" in cols
        assert "source" in cols
        assert "evidence_count" in cols
        assert "salience" in cols
    finally:
        con.close()


def test_learning_patterns_migration_old_db(tmp_path):
    """Миграция: старая БД без новых колонок → новые добавляются."""
    db_file = tmp_path / "old.db"
    # Создаём СТАРУЮ схему (без новых колонок)
    con = sqlite3.connect(str(db_file))
    try:
        con.execute('''
            CREATE TABLE learning_patterns (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                signature      TEXT UNIQUE NOT NULL,
                context_json   TEXT,
                fix            TEXT,
                success_count  INTEGER DEFAULT 0,
                failure_count  INTEGER DEFAULT 0,
                confidence     REAL DEFAULT 0.0,
                updated_at     TEXT
            )
        ''')
        con.commit()
    finally:
        con.close()
    # Теперь init_schema должен добавить колонки
    db.init_schema(db_file)
    con = sqlite3.connect(str(db_file))
    try:
        cur = con.execute("PRAGMA table_info(learning_patterns)")
        cols = {row[1] for row in cur.fetchall()}
        assert "status" in cols, "миграция не добавила status"
        assert "scope" in cols, "миграция не добавила scope"
        assert "salience" in cols, "миграция не добавила salience"
    finally:
        con.close()


def test_wal_mode_enabled(tmp_path):
    """WAL включён после init_schema."""
    db_file = tmp_path / "test.db"
    db.init_schema(db_file)
    con = sqlite3.connect(str(db_file))
    try:
        cur = con.execute("PRAGMA journal_mode")
        mode = cur.fetchone()[0]
        assert mode.lower() == "wal", f"WAL не включён: {mode}"
    finally:
        con.close()


def test_stats_includes_new_tables(tmp_path):
    """stats() возвращает новые таблицы."""
    db_file = tmp_path / "test.db"
    db.init_schema(db_file)
    s = db.stats(db_file)
    assert "session_summaries" in s
    assert "pattern_evidence" in s
    assert "memory_fts" in s
