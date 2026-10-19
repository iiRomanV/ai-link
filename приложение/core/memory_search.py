"""MEMORY-SEARCH — поиск по памяти (FTS5 + score).

FIX MEMORY-V1 (2026-09-30):
  • FTS5-поиск по memory_fts
  • BM25-ранжирование + нормализация
  • Score-формула: BM25 + confidence + recency + salience
  • Без внешних зависимостей (только SQLite)

API:
  index_memory(id, kind, scope, title, content, tags) → bool
  search(query, scope, kind, limit) → List[Dict]
  count() → int
  clear() → None
"""
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.ai_link_db import get_db_path, init_schema


# ─────────────────────────────────────────────────────────────
#  Score-формула (Perplexity, MEMORY-V1)
# ─────────────────────────────────────────────────────────────

W_BM25 = 0.55
W_CONFIDENCE = 0.20
W_RECENCY = 0.15
W_SALIENCE = 0.10


def _normalize_bm25(bm25: float) -> float:
    """BM25 в SQLite отрицательный (меньше = лучше).
    Нормализуем в [0, 1]: чем ближе к 1 — тем релевантнее.
    """
    if bm25 >= 0:
        return 0.0
    # BM25 около -10..-1 для типичных запросов
    return min(1.0, -bm25 / 10.0)


def _recency_score(updated_at: str) -> float:
    """1.0 — только что, 0.0 — очень старое (>90 дней)."""
    if not updated_at:
        return 0.5
    try:
        dt = datetime.fromisoformat(updated_at.replace("Z", ""))
    except Exception:
        return 0.5
    days = (datetime.now() - dt).total_seconds() / 86400.0
    if days <= 1:
        return 1.0
    if days >= 90:
        return 0.0
    return 1.0 - (days / 90.0)


def score(bm25: float,
          confidence: float = 0.5,
          recency: float = 0.5,
          salience: float = 0.5) -> float:
    """Итоговый score = 0.55*bm25 + 0.20*conf + 0.15*rec + 0.10*sal."""
    return (
        W_BM25 * _normalize_bm25(bm25)
        + W_CONFIDENCE * max(0.0, min(1.0, confidence))
        + W_RECENCY * max(0.0, min(1.0, recency))
        + W_SALIENCE * max(0.0, min(1.0, salience))
    )


# ─────────────────────────────────────────────────────────────
#  Безопасный запрос для FTS5
# ─────────────────────────────────────────────────────────────

_FTS_SPECIAL = re.compile(r'["\*\(\)\:\^\-]')


def _safe_fts_query(q: str) -> str:
    """Экранирует спецсимволы FTS5, оборачивает токены в кавычки."""
    if not q or not q.strip():
        return ""
    q = _FTS_SPECIAL.sub(" ", q)
    tokens = [t for t in q.split() if t]
    if not tokens:
        return ""
    return " ".join(f'"{t}"' for t in tokens)


# ─────────────────────────────────────────────────────────────
#  index_memory — вставка в memory_fts
# ─────────────────────────────────────────────────────────────

def index_memory(
    memory_id: str,
    kind: str,
    scope: str,
    title: str,
    content: str,
    tags: str = "",
    db_path: Optional[Path] = None,
) -> bool:
    """Добавляет запись в memory_fts. True если успех.

    kind:  lesson | pattern | fact | session
    scope: global | provider:qwen | project:<dir> | language:python
    """
    if not memory_id or not title:
        return False
    path = init_schema(db_path)
    con = sqlite3.connect(str(path))
    try:
        # Удаляем старую запись с тем же id (идемпотентность)
        con.execute("DELETE FROM memory_fts WHERE memory_id = ?",
                    (memory_id,))
        con.execute(
            "INSERT INTO memory_fts "
            "(memory_id, kind, scope, title, content, tags) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (memory_id, kind or "", scope or "",
             title, content or "", tags or ""),
        )
        con.commit()
        return True
    except Exception:
        return False
    finally:
        con.close()


# ─────────────────────────────────────────────────────────────
#  search — FTS5 + score
# ─────────────────────────────────────────────────────────────

def search(
    query: str,
    scope: Optional[str] = None,
    kind: Optional[str] = None,
    limit: int = 10,
    db_path: Optional[Path] = None,
    min_score: float = 0.0,
) -> List[Dict[str, Any]]:
    """Поиск по памяти. Возвращает список dict-ов:
    [{memory_id, kind, scope, title, content, bm25, score}].

    scope: точное совпадение (global / provider:qwen / ...)
    kind:  точное совпадение (lesson / pattern / ...)
    """
    q_safe = _safe_fts_query(query)
    if not q_safe:
        return []

    path = get_db_path() if db_path is None else db_path
    if not path.exists():
        return []

    con = sqlite3.connect(str(path))
    try:
        sql = (
            "SELECT memory_id, kind, scope, title, content, "
            "       bm25(memory_fts) AS bm25_score "
            "FROM memory_fts "
            "WHERE memory_fts MATCH ? "
        )
        params: List[Any] = [q_safe]
        if scope:
            sql += "AND scope = ? "
            params.append(scope)
        if kind:
            sql += "AND kind = ? "
            params.append(kind)
        # bm25 отрицательный, ASC = лучшее сверху
        sql += "ORDER BY bm25_score ASC LIMIT ?"
        params.append(max(1, int(limit)) * 3)  # берём с запасом

        rows = con.execute(sql, params).fetchall()
    except Exception:
        return []
    finally:
        con.close()

    out: List[Dict[str, Any]] = []
    for r in rows:
        bm25_score = float(r[5] or 0.0)
        s = score(bm25=bm25_score, confidence=0.5, recency=0.5,
                  salience=0.5)
        if s < min_score:
            continue
        out.append({
            "memory_id": r[0],
            "kind": r[1] or "",
            "scope": r[2] or "",
            "title": r[3] or "",
            "content": r[4] or "",
            "bm25": bm25_score,
            "score": round(s, 4),
        })

    out.sort(key=lambda x: x["score"], reverse=True)
    return out[:max(1, int(limit))]


# ─────────────────────────────────────────────────────────────
#  count / clear
# ─────────────────────────────────────────────────────────────

def count(db_path: Optional[Path] = None) -> int:
    """Сколько записей в memory_fts."""
    path = get_db_path() if db_path is None else db_path
    if not path.exists():
        return 0
    con = sqlite3.connect(str(path))
    try:
        cur = con.execute("SELECT COUNT(*) FROM memory_fts")
        return cur.fetchone()[0]
    except Exception:
        return 0
    finally:
        con.close()


def clear(db_path: Optional[Path] = None) -> None:
    """Очищает memory_fts."""
    path = get_db_path() if db_path is None else db_path
    if not path.exists():
        return
    con = sqlite3.connect(str(path))
    try:
        con.execute("DELETE FROM memory_fts")
        con.commit()
    finally:
        con.close()
