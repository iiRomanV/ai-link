"""MEMORY-SEMANTIC — семантический поиск через ONNX MiniLM.

FIX MEMORY-SEMANTIC (2026-09-30):
  Реальный semantic search на ONNX-модели all-MiniLM-L6-v2.

  ⚠️ ВАЖНО: модель English-only!
    • Английские термины (playwright, ImportError, sqlite) — работает
    • Русские запросы — мусор (проверено: similarity ≈ -0.06)
    • Для мультиязычности — отдельная задача в плане

  Размер:
    • Модель: ~80 МБ на диске
    • Вектор: 384 float32 = 1536 байт на запись
    • 10000 записей: ~15 МБ

API:
  is_available()        → bool (onnxruntime + chromadb + модель)
  get_model_name()      → str
  embed_text(text)      → bytes (1536 байт)
  embed_batch(texts)    → list[bytes]
  index_memory(id, text) → bool
  find_semantic(query, limit) → List[Dict]
  rebuild_from_fts()    → Dict (сколько проиндексировано)
  delete_embedding(id)  → bool
  count()               → int
"""
import json
import sqlite3
import struct
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.ai_link_db import get_db_path, init_schema


MODEL_NAME = "all-MiniLM-L6-v2"
EMBED_DIM = 384
EMBED_BYTES = EMBED_DIM * 4  # float32


# ─────────────────────────────────────────────────────────────
#  Encoder (lazy loading)
# ─────────────────────────────────────────────────────────────

_encoder = None


def _get_encoder():
    """Lazy-загрузка ONNX MiniLM. Первый вызов ~1-2 сек (загрузка модели)."""
    global _encoder
    if _encoder is not None:
        return _encoder
    try:
        from chromadb.utils import embedding_functions as ef
        _encoder = ef.DefaultEmbeddingFunction()
        return _encoder
    except Exception:
        return None


def is_available() -> bool:
    """Проверяет: onnxruntime + chromadb + модель загружаются."""
    try:
        import onnxruntime  # noqa: F401
    except ImportError:
        return False
    return _get_encoder() is not None


def get_model_name() -> str:
    return MODEL_NAME


# ─────────────────────────────────────────────────────────────
#  embed
# ─────────────────────────────────────────────────────────────

def _vec_to_bytes(vec: List[float]) -> bytes:
    """float32 little-endian."""
    return struct.pack(f"<{len(vec)}f", *vec)


def _bytes_to_vec(blob: bytes) -> List[float]:
    """Обратно в список float."""
    n = len(blob) // 4
    return list(struct.unpack(f"<{n}f", blob))


def embed_text(text: str) -> Optional[bytes]:
    """Один текст → bytes(1536). None при ошибке или пустом тексте."""
    if not text or not text.strip():
        return None
    enc = _get_encoder()
    if enc is None:
        return None
    try:
        vec = enc([text.strip()])[0]
        return _vec_to_bytes(vec)
    except Exception:
        return None


def embed_batch(texts: List[str]) -> List[Optional[bytes]]:
    """Несколько текстов → список bytes (или None для пустых)."""
    if not texts:
        return []
    enc = _get_encoder()
    if enc is None:
        return [None] * len(texts)
    # Обрабатываем только непустые
    indices = [i for i, t in enumerate(texts) if t and t.strip()]
    cleaned = [texts[i].strip() for i in indices]
    if not cleaned:
        return [None] * len(texts)
    try:
        vecs = enc(cleaned)
    except Exception:
        return [None] * len(texts)
    out: List[Optional[bytes]] = [None] * len(texts)
    for j, i in enumerate(indices):
        out[i] = _vec_to_bytes(vecs[j])
    return out


# ─────────────────────────────────────────────────────────────
#  index
# ─────────────────────────────────────────────────────────────

def _con(db_path: Optional[Path]) -> sqlite3.Connection:
    path = init_schema(db_path)
    con = sqlite3.connect(str(path))
    con.execute("PRAGMA busy_timeout = 5000")
    return con


def index_memory(
    memory_id: str,
    text: str,
    db_path: Optional[Path] = None,
) -> bool:
    """Считает embedding для текста и сохраняет. Идемпотентно."""
    if not memory_id or not text:
        return False
    blob = embed_text(text)
    if blob is None:
        return False
    con = _con(db_path)
    try:
        con.execute(
            "INSERT OR REPLACE INTO memory_embeddings "
            "(memory_id, model_name, dim, embedding, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (memory_id, MODEL_NAME, EMBED_DIM, blob,
             datetime.now().isoformat(timespec="seconds")),
        )
        con.commit()
        return True
    except Exception:
        return False
    finally:
        con.close()


def delete_embedding(memory_id: str,
                     db_path: Optional[Path] = None) -> bool:
    con = _con(db_path)
    try:
        cur = con.execute(
            "DELETE FROM memory_embeddings WHERE memory_id = ?",
            (memory_id,),
        )
        con.commit()
        return cur.rowcount > 0
    finally:
        con.close()


def count(db_path: Optional[Path] = None) -> int:
    path = get_db_path() if db_path is None else db_path
    if not path.exists():
        return 0
    con = sqlite3.connect(str(path))
    try:
        cur = con.execute("SELECT COUNT(*) FROM memory_embeddings")
        return cur.fetchone()[0]
    except Exception:
        return 0
    finally:
        con.close()


# ─────────────────────────────────────────────────────────────
#  cosine similarity (чистый Python, без numpy)
# ─────────────────────────────────────────────────────────────

def _cosine(a: List[float], b: List[float]) -> float:
    """Косинус. Без numpy — просто и быстро для 384 чисел."""
    if len(a) != len(b):
        return 0.0
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na <= 0.0 or nb <= 0.0:
        return 0.0
    import math
    return dot / (math.sqrt(na) * math.sqrt(nb))


# ─────────────────────────────────────────────────────────────
#  search
# ─────────────────────────────────────────────────────────────

def find_semantic(
    query: str,
    limit: int = 10,
    min_score: float = 0.3,
    db_path: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    """Семантический поиск. Возвращает список dict-ов:
    [{memory_id, score, model}].

    Работает только для английских терминов (English-only модель).
    """
    if not query or not query.strip():
        return []
    q_blob = embed_text(query)
    if q_blob is None:
        return []
    q_vec = _bytes_to_vec(q_blob)

    path = get_db_path() if db_path is None else db_path
    if not path.exists():
        return []

    con = sqlite3.connect(str(path))
    try:
        cur = con.execute(
            "SELECT memory_id, embedding, model_name "
            "FROM memory_embeddings"
        )
        rows = cur.fetchall()
    finally:
        con.close()

    out: List[Dict[str, Any]] = []
    for mid, blob, mname in rows:
        try:
            v = _bytes_to_vec(blob)
        except Exception:
            continue
        s = _cosine(q_vec, v)
        if s < min_score:
            continue
        out.append({
            "memory_id": mid,
            "score": round(s, 4),
            "model": mname,
        })

    out.sort(key=lambda x: x["score"], reverse=True)
    return out[:max(1, int(limit))]


# ─────────────────────────────────────────────────────────────
#  rebuild from memory_fts
# ─────────────────────────────────────────────────────────────

def rebuild_from_fts(db_path: Optional[Path] = None,
                     limit: int = 0) -> Dict[str, int]:
    """Проиндексировать все записи из memory_fts в embeddings.

    limit=0 → все.
    Возвращает {"indexed": N, "skipped": M, "total": T}.
    """
    path = get_db_path() if db_path is None else db_path
    if not path.exists():
        return {"indexed": 0, "skipped": 0, "total": 0}

    con = sqlite3.connect(str(path))
    try:
        sql = (
            "SELECT memory_id, title, content FROM memory_fts"
        )
        if limit > 0:
            sql += f" LIMIT {int(limit)}"
        cur = con.execute(sql)
        rows = cur.fetchall()
    finally:
        con.close()

    indexed = skipped = 0
    for mid, title, content in rows:
        text = f"{title or ''}\n{content or ''}".strip()
        if not text:
            skipped += 1
            continue
        if index_memory(mid, text, db_path):
            indexed += 1
        else:
            skipped += 1
    return {"indexed": indexed, "skipped": skipped, "total": len(rows)}
