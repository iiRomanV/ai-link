"""SQLITE-INDEX — глобальный индекс проектов в SQLite.

Файлы остаются источником истины. SQLite — индекс + аналитика.
База: ~/.ai_link/ai_link.db (та же, что db/database.py).

НЕ трогаем существующие таблицы (tasks/agents/patterns).
Добавляем свои:
  runs, phases, events, model_calls, tool_calls, reviews,
  learning_patterns, files, cost_ledger

Главная операция — ingest_events(project_dir): читает
<project>/.ai_link/events.jsonl и наливает в таблицу events
(идемпотентно: повторный ingest не задваивает).

API:
  get_db_path()                    → Path
  init_schema()                    → создаёт таблицы
  ingest_events(project_dir)       → сколько новых событий
  save_run / save_phase            → прямые вставки
  list_runs(limit)                 → последние прогоны
  query_events(run_id, limit)      → события прогона
  stats()                          → сводка
"""

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List, Optional


# FIX MEMORY-V1: индексы на новые колонки — создаём ПОСЛЕ миграций.
# Иначе на старых БД: "no such column: status".
INDEXES_AFTER_MIGRATIONS = [
    """
    CREATE INDEX IF NOT EXISTS idx_patterns_status
        ON learning_patterns(status)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_patterns_scope
        ON learning_patterns(scope, scope_value)
    """,
]


# FIX MEMORY-V1 (2026-09-30): миграции для существующих БД.
# SQLite не умеет ADD COLUMN IF NOT EXISTS — проверяем через PRAGMA.
_MIGRATIONS = {
    "learning_patterns": [
        ("status", "TEXT DEFAULT 'candidate'"),
        ("scope", "TEXT DEFAULT 'global'"),
        ("scope_value", "TEXT DEFAULT ''"),
        ("source", "TEXT DEFAULT 'observed'"),
        ("evidence_count", "INTEGER DEFAULT 0"),
        ("salience", "REAL DEFAULT 0.5"),
        ("last_used_at", "TEXT"),
        ("expires_at", "TEXT"),
        # FIX ATTRIBUTION (2026-09-30): Beta posterior.
        # confidence = (alpha+S) / (alpha+beta+S+F)
        ("alpha", "REAL DEFAULT 1.0"),
        ("beta", "REAL DEFAULT 1.0"),
    ],
    # FIX ATTRIBUTION (2026-09-30): 5-уровневая модель.
    "pattern_evidence": [
        ("phase", "TEXT DEFAULT ''"),
        ("problem_keywords", "TEXT DEFAULT ''"),
        ("relevance_score", "REAL DEFAULT 0.0"),
        ("retrieved", "INTEGER DEFAULT 0"),
        ("eligible", "INTEGER DEFAULT 0"),
        ("used", "INTEGER DEFAULT 0"),
        ("supported", "INTEGER DEFAULT 0"),
        ("verified_effect", "INTEGER DEFAULT 0"),
        ("attribution_method", "TEXT DEFAULT ''"),
    ],
}


def _apply_migrations(con) -> None:
    """Добавляет колонки в существующие таблицы, если их нет."""
    for table, columns in _MIGRATIONS.items():
        try:
            cur = con.execute(f"PRAGMA table_info({table})")
            existing = {row[1] for row in cur.fetchall()}
        except Exception:
            continue
        if not existing:
            continue
        for col_name, col_def in columns:
            if col_name not in existing:
                try:
                    con.execute(
                        f"ALTER TABLE {table} ADD COLUMN "
                        f"{col_name} {col_def}"
                    )
                except Exception:
                    pass


def get_db_path() -> Path:
    """Путь к глобальной БД. Та же, что у db.database.

    ISOLATE-DB (2026-10-02): если задана env-переменная
    AI_LINK_DB_PATH — используем её. Это позволяет тестам
    работать на tmp_path и НЕ чистить продакшн-БД.

    Симптом бага: тесты test_auto_lessons.py чистили
    ~/.ai_link/ai_link.db → паттерны исчезали (183 сироты).
    """
    import os as _os
    _env = _os.environ.get("AI_LINK_DB_PATH")
    if _env:
        return Path(_env)
    return Path.home() / ".ai_link" / "ai_link.db"


# ─────────────────────────────────────────────────────────────
#  Схема
# ─────────────────────────────────────────────────────────────

SCHEMA = [
    # Прогоны (run_id уникален)
    """
    CREATE TABLE IF NOT EXISTS runs (
        run_id      TEXT PRIMARY KEY,
        project_dir TEXT NOT NULL,
        task        TEXT,
        status      TEXT,
        started_at  TEXT,
        finished_at TEXT,
        final_phase TEXT,
        phases_done INTEGER DEFAULT 0,
        seconds     REAL DEFAULT 0,
        error       TEXT
    )
    """,
    # Фазы прогона (составной ключ)
    """
    CREATE TABLE IF NOT EXISTS phases (
        run_id        TEXT NOT NULL,
        phase_id      TEXT NOT NULL,
        status        TEXT,
        seconds       REAL DEFAULT 0,
        stage_index   INTEGER DEFAULT 0,
        created_at    TEXT DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (run_id, phase_id, created_at)
    )
    """,
    # События (идемпотентно: unique по run_id+ts+event_type)
    """
    CREATE TABLE IF NOT EXISTS events (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id       TEXT,
        phase_id     TEXT,
        iteration    INTEGER DEFAULT 0,
        event_type   TEXT NOT NULL,
        payload_json TEXT,
        ts           TEXT NOT NULL,
        project_dir  TEXT,
        UNIQUE (run_id, ts, event_type, phase_id, iteration)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_events_run ON events(run_id)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_events_type ON events(event_type)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts)
    """,
    # Вызовы модели
    """
    CREATE TABLE IF NOT EXISTS model_calls (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id       TEXT,
        phase_id     TEXT,
        agent        TEXT,
        success      INTEGER,
        elapsed      REAL,
        text_len     INTEGER,
        error        TEXT,
        ts           TEXT NOT NULL
    )
    """,
    # Вызовы инструментов
    """
    CREATE TABLE IF NOT EXISTS tool_calls (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id       TEXT,
        phase_id     TEXT,
        tool_types   TEXT,
        success      INTEGER,
        created_count INTEGER,
        error        TEXT,
        ts           TEXT NOT NULL
    )
    """,
    # Отчёты review
    """
    CREATE TABLE IF NOT EXISTS reviews (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id     TEXT,
        phase_id   TEXT,
        verdict    TEXT,
        severity   TEXT,
        validated  INTEGER,
        ts         TEXT NOT NULL
    )
    """,
    # Паттерны обучения (LEARNING-PATTERNS, пока пусто)
    """
    CREATE TABLE IF NOT EXISTS learning_patterns (
        id             INTEGER PRIMARY KEY AUTOINCREMENT,
        signature      TEXT UNIQUE NOT NULL,
        context_json   TEXT,
        fix            TEXT,
        success_count  INTEGER DEFAULT 0,
        failure_count  INTEGER DEFAULT 0,
        confidence     REAL DEFAULT 0.0,
        updated_at     TEXT,
        -- FIX MEMORY-V1 (2026-09-30):
        status         TEXT DEFAULT 'candidate',
        scope          TEXT DEFAULT 'global',
        scope_value    TEXT DEFAULT '',
        source         TEXT DEFAULT 'observed',
        evidence_count INTEGER DEFAULT 0,
        salience       REAL DEFAULT 0.5,
        last_used_at   TEXT,
        expires_at     TEXT,
        -- FIX ATTRIBUTION (2026-09-30): Beta posterior
        alpha          REAL DEFAULT 1.0,
        beta           REAL DEFAULT 1.0
    )
    """,
    # Cost-ledger (COST-LEDGER, пока пусто)
    """
    CREATE TABLE IF NOT EXISTS cost_ledger (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id          TEXT,
        model           TEXT,
        role            TEXT,
        duration_seconds REAL,
        requests        INTEGER,
        estimated_cost  REAL,
        resource_class  TEXT,
        success         INTEGER,
        ts              TEXT NOT NULL
    )
    """,
    # FIX MEMORY-V1 (2026-09-30): FTS5 для поиска по памяти
    """
    CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(
        memory_id UNINDEXED,
        kind      UNINDEXED,
        scope     UNINDEXED,
        title,
        content,
        tags,
        tokenize = 'unicode61 remove_diacritics 1'
    )
    """,
    # FIX MEMORY-V1: session summaries (уровень 2 памяти)
    """
    CREATE TABLE IF NOT EXISTS session_summaries (
        id           TEXT PRIMARY KEY,
        project_id   TEXT,
        run_id       TEXT,
        summary_json TEXT NOT NULL,
        created_at   TEXT NOT NULL
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_sessions_project
        ON session_summaries(project_id)
    """,
    # FIX MEMORY-V1: pattern evidence log (для confidence)
    """
    CREATE TABLE IF NOT EXISTS pattern_evidence (
        id                TEXT PRIMARY KEY,
        pattern_id        INTEGER NOT NULL,
        run_id            TEXT,
        model             TEXT,
        injected          INTEGER NOT NULL DEFAULT 0,
        outcome           TEXT NOT NULL,
        verification_json TEXT,
        created_at        TEXT NOT NULL,
        phase             TEXT DEFAULT '',
        problem_keywords  TEXT DEFAULT '',
        relevance_score   REAL DEFAULT 0.0,
        retrieved         INTEGER DEFAULT 0,
        eligible          INTEGER DEFAULT 0,
        used              INTEGER DEFAULT 0,
        supported         INTEGER DEFAULT 0,
        verified_effect   INTEGER DEFAULT 0,
        attribution_method TEXT DEFAULT ''
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_pattern_ev_pattern
        ON pattern_evidence(pattern_id)
    """,
    # FIX MEMORY-SEMANTIC (2026-09-30): embeddings для semantic-поиска.
    # 384-мерный вектор (float32) = 1536 байт на запись.
    """
    CREATE TABLE IF NOT EXISTS memory_embeddings (
        memory_id  TEXT PRIMARY KEY,
        model_name TEXT NOT NULL DEFAULT 'all-MiniLM-L6-v2',
        dim        INTEGER NOT NULL DEFAULT 384,
        embedding  BLOB NOT NULL,
        created_at TEXT NOT NULL
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_embeddings_model
        ON memory_embeddings(model_name)
    """,
]


def init_schema(db_path: Optional[Path] = None) -> Path:
    """Создаёт все таблицы (IF NOT EXISTS). Возвращает путь.

    FIX MEMORY-V1 (2026-09-30): включён WAL, busy_timeout,
    применены миграции для старых БД.
    """
    path = db_path or get_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(path))
    try:
        con.execute("PRAGMA journal_mode = WAL")
        con.execute("PRAGMA foreign_keys = ON")
        con.execute("PRAGMA busy_timeout = 5000")
        for sql in SCHEMA:
            con.execute(sql)
        _apply_migrations(con)
        for sql in INDEXES_AFTER_MIGRATIONS:
            try:
                con.execute(sql)
            except Exception:
                pass
        con.commit()
    finally:
        con.close()
    return path


# ─────────────────────────────────────────────────────────────
#  Ingest events.jsonl
# ─────────────────────────────────────────────────────────────

def _events_path(project_dir: str) -> Path:
    return Path(project_dir) / ".ai_link" / "events.jsonl"


def ingest_events(project_dir: str,
                  db_path: Optional[Path] = None) -> Dict[str, Any]:
    """Читает events.jsonl и добавляет новые события в SQLite.

    Возвращает: {"added": N, "skipped": M, "total": K}.
    Никогда не падает — при ошибке возвращает 0.
    """
    src = _events_path(project_dir)
    if not src.exists():
        return {"added": 0, "skipped": 0, "total": 0}

    try:
        lines = src.read_text(encoding="utf-8").splitlines()
    except Exception:
        return {"added": 0, "skipped": 0, "total": 0}

    path = init_schema(db_path)
    added = skipped = total = 0
    con = sqlite3.connect(str(path))
    try:
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except Exception:
                continue
            total += 1
            ts = ev.get("ts") or ""
            run_id = ev.get("run_id") or ""
            evt = ev.get("event_type") or ""
            if not evt or not ts:
                skipped += 1
                continue
            try:
                con.execute(
                    "INSERT OR IGNORE INTO events "
                    "(run_id, phase_id, iteration, event_type, "
                    " payload_json, ts, project_dir) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        run_id,
                        ev.get("phase_id") or "",
                        int(ev.get("iteration") or 0),
                        evt,
                        json.dumps(ev.get("payload_json") or {},
                                   ensure_ascii=False),
                        ts,
                        str(project_dir),
                    ),
                )
                if con.total_changes > 0:
                    added += 1
                else:
                    skipped += 1
            except sqlite3.IntegrityError:
                skipped += 1
            except Exception:
                skipped += 1
        con.commit()
    finally:
        con.close()
    return {"added": added, "skipped": skipped, "total": total}


# ─────────────────────────────────────────────────────────────
#  Прямые вставки / чтение
# ─────────────────────────────────────────────────────────────

def save_run(run_id: str,
             project_dir: str,
             task: str = "",
             status: str = "",
             final_phase: str = "",
             phases_done: int = 0,
             seconds: float = 0.0,
             error: str = "",
             started_at: str = "",
             db_path: Optional[Path] = None) -> None:
    """Upsert прогона (по run_id)."""
    if not run_id:
        return
    path = init_schema(db_path)
    con = sqlite3.connect(str(path))
    try:
        con.execute(
            "INSERT INTO runs (run_id, project_dir, task, status, "
            "started_at, finished_at, final_phase, phases_done, "
            "seconds, error) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(run_id) DO UPDATE SET "
            "  status=excluded.status, "
            "  finished_at=excluded.finished_at, "
            "  final_phase=excluded.final_phase, "
            "  phases_done=excluded.phases_done, "
            "  seconds=excluded.seconds, "
            "  error=excluded.error",
            (
                run_id, project_dir, task[:1000] if task else "",
                status, started_at,
                datetime.now().isoformat(timespec="seconds"),
                final_phase, int(phases_done or 0),
                float(seconds or 0), (error or "")[:1000],
            ),
        )
        con.commit()
    finally:
        con.close()


def list_runs(limit: int = 20,
              db_path: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Последние N прогонов."""
    path = get_db_path() if db_path is None else db_path
    if not path.exists():
        return []
    con = sqlite3.connect(str(path))
    con.row_factory = sqlite3.Row
    try:
        cur = con.execute(
            "SELECT run_id, project_dir, task, status, started_at, "
            "finished_at, final_phase, phases_done, seconds, error "
            "FROM runs ORDER BY started_at DESC, rowid DESC LIMIT ?",
            (limit,),
        )
        return [dict(r) for r in cur.fetchall()]
    except Exception:
        return []
    finally:
        con.close()


def query_events(run_id: str,
                 limit: int = 200,
                 db_path: Optional[Path] = None) -> List[Dict[str, Any]]:
    """События конкретного прогона."""
    path = get_db_path() if db_path is None else db_path
    if not path.exists():
        return []
    con = sqlite3.connect(str(path))
    con.row_factory = sqlite3.Row
    try:
        cur = con.execute(
            "SELECT run_id, phase_id, iteration, event_type, "
            "payload_json, ts FROM events WHERE run_id = ? "
            "ORDER BY id ASC LIMIT ?",
            (run_id, limit),
        )
        rows = []
        for r in cur.fetchall():
            d = dict(r)
            try:
                d["payload"] = json.loads(d.get("payload_json") or "{}")
            except Exception:
                d["payload"] = {}
            rows.append(d)
        return rows
    except Exception:
        return []
    finally:
        con.close()


def stats(db_path: Optional[Path] = None) -> Dict[str, Any]:
    """Сводка по индексу."""
    path = get_db_path() if db_path is None else db_path
    if not path.exists():
        return {"db_path": str(path), "exists": False}
    out: Dict[str, Any] = {"db_path": str(path), "exists": True}
    con = sqlite3.connect(str(path))
    try:
        for tbl in ("runs", "phases", "events", "model_calls",
                    "tool_calls", "reviews", "learning_patterns",
                    "cost_ledger", "session_summaries",
                    "pattern_evidence", "memory_fts"):
            try:
                cur = con.execute(f"SELECT COUNT(*) FROM {tbl}")
                out[tbl] = cur.fetchone()[0]
            except Exception:
                out[tbl] = None
    finally:
        con.close()
    return out


def reset_index(db_path: Optional[Path] = None) -> None:
    """Удаляет все наши таблицы (для тестов). НЕ трогает tasks/agents/patterns."""
    path = get_db_path() if db_path is None else db_path
    if not path.exists():
        return
    con = sqlite3.connect(str(path))
    try:
        # FIX MEMORY-V1: + memory_fts, session_summaries, pattern_evidence
        for t in ("runs", "phases", "events", "model_calls",
                  "tool_calls", "reviews", "learning_patterns",
                  "cost_ledger", "session_summaries",
                  "pattern_evidence"):
            try:
                con.execute(f"DROP TABLE IF EXISTS {t}")
            except Exception:
                pass
        try:
            con.execute("DROP TABLE IF EXISTS memory_fts")
        except Exception:
            pass
        # Далее — старый код удаления (цикл уже не нужен, но оставим заглушку)
        for t in ():
            try:
                con.execute(f"DROP TABLE IF EXISTS {t}")
            except Exception:
                pass
        con.commit()
    finally:
        con.close()
