"""Тесты MEMORY-SEMANTIC (ONNX MiniLM, English-only)."""

import struct
from core.ai_link_db import init_schema
from core import memory_semantic as sem


def _db(tmp_path):
    p = tmp_path / "sem.db"
    init_schema(p)
    return p


# --------------------------------------------------------------------------- #
# доступность
# --------------------------------------------------------------------------- #
def test_is_available():
    """ONNX + chromadb + модель загружаются."""
    assert sem.is_available() is True


def test_get_model_name():
    assert sem.get_model_name() == "all-MiniLM-L6-v2"


# --------------------------------------------------------------------------- #
# embed
# --------------------------------------------------------------------------- #
def test_embed_text_returns_bytes():
    blob = sem.embed_text("playwright timeout")
    assert isinstance(blob, bytes)
    assert len(blob) == sem.EMBED_BYTES  # 384 * 4 = 1536


def test_embed_text_empty_returns_none():
    assert sem.embed_text("") is None
    assert sem.embed_text("   ") is None


def test_embed_batch():
    blobs = sem.embed_batch(["hello", "", "world"])
    assert len(blobs) == 3
    assert blobs[0] is not None
    assert blobs[1] is None
    assert blobs[2] is not None


def test_embed_batch_empty():
    assert sem.embed_batch([]) == []


def test_vec_bytes_roundtrip():
    vec = [1.0, 2.0, 3.0]
    blob = sem._vec_to_bytes(vec)
    assert len(blob) == 12
    back = sem._bytes_to_vec(blob)
    assert back == vec


# --------------------------------------------------------------------------- #
# cosine
# --------------------------------------------------------------------------- #
def test_cosine_identical():
    a = [1.0, 0.0, 0.0]
    assert abs(sem._cosine(a, a) - 1.0) < 0.001


def test_cosine_opposite():
    a = [1.0, 0.0]
    b = [-1.0, 0.0]
    assert sem._cosine(a, b) == -1.0


def test_cosine_different_lengths():
    assert sem._cosine([1.0, 2.0], [1.0]) == 0.0


def test_cosine_zero_vector():
    assert sem._cosine([0.0, 0.0], [1.0, 1.0]) == 0.0


# --------------------------------------------------------------------------- #
# index + count
# --------------------------------------------------------------------------- #
def test_index_memory_ok(tmp_path):
    p = _db(tmp_path)
    assert sem.index_memory("m1", "playwright timeout", db_path=p) is True
    assert sem.count(p) == 1


def test_index_memory_empty(tmp_path):
    p = _db(tmp_path)
    assert sem.index_memory("m1", "", db_path=p) is False
    assert sem.index_memory("", "text", db_path=p) is False


def test_index_memory_idempotent(tmp_path):
    p = _db(tmp_path)
    sem.index_memory("m1", "first text", db_path=p)
    sem.index_memory("m1", "second text", db_path=p)
    # Должно быть 1 запись (INSERT OR REPLACE)
    assert sem.count(p) == 1


def test_delete_embedding(tmp_path):
    p = _db(tmp_path)
    sem.index_memory("m1", "text", db_path=p)
    assert sem.delete_embedding("m1", p) is True
    assert sem.count(p) == 0


def test_delete_missing(tmp_path):
    p = _db(tmp_path)
    assert sem.delete_embedding("nope", p) is False


# --------------------------------------------------------------------------- #
# search
# --------------------------------------------------------------------------- #
def test_find_semantic_english(tmp_path):
    p = _db(tmp_path)
    sem.index_memory("m1", "Playwright timeout on networkidle wait",
                     db_path=p)
    sem.index_memory("m2", "ImportError no module named sqlite3",
                     db_path=p)
    sem.index_memory("m3", "PySide6 widget crashed on close",
                     db_path=p)

    # Технический термин — должен найти m1
    r = sem.find_semantic("playwright timeout", db_path=p)
    assert len(r) >= 1
    assert r[0]["memory_id"] == "m1"
    assert r[0]["score"] > 0.5


def test_find_semantic_importerror(tmp_path):
    p = _db(tmp_path)
    sem.index_memory("m1", "Playwright timeout", db_path=p)
    sem.index_memory("m2", "ImportError no module named sqlite3",
                     db_path=p)

    r = sem.find_semantic("module import error", db_path=p)
    assert len(r) >= 1
    assert r[0]["memory_id"] == "m2"


def test_find_semantic_russian_returns_empty(tmp_path):
    """Русский — модель English-only → мусор (пусто выше порога)."""
    p = _db(tmp_path)
    sem.index_memory("m1", "playwright timeout", db_path=p)
    sem.index_memory("m2", "ImportError", db_path=p)
    r = sem.find_semantic("ошибка модуля", db_path=p)
    # Может быть пусто или мусор — но НЕ должно быть уверенного попадания
    if r:
        assert r[0]["score"] < 0.5  # не должно быть "уверенного"


def test_find_semantic_empty_query(tmp_path):
    p = _db(tmp_path)
    sem.index_memory("m1", "text", db_path=p)
    assert sem.find_semantic("", db_path=p) == []


def test_find_semantic_limit(tmp_path):
    p = _db(tmp_path)
    for i in range(5):
        sem.index_memory(f"m{i}", f"playwright timeout {i}", db_path=p)
    r = sem.find_semantic("playwright", limit=2, db_path=p)
    assert len(r) <= 2


def test_find_semantic_min_score(tmp_path):
    p = _db(tmp_path)
    sem.index_memory("m1", "playwright timeout", db_path=p)
    # Порог 1.5 — гарантированно выше любого cosine (max=1.0)
    r = sem.find_semantic("playwright timeout",
                          min_score=1.5, db_path=p)
    assert r == []


# --------------------------------------------------------------------------- #
# rebuild
# --------------------------------------------------------------------------- #
def test_rebuild_from_fts(tmp_path):
    from core import memory_search as ms
    p = _db(tmp_path)
    # Заполняем memory_fts
    ms.index_memory("m1", "lesson", "global",
                    "Playwright timeout", "networkidle wait",
                    db_path=p)
    ms.index_memory("m2", "lesson", "global",
                    "ImportError", "no module sqlite3",
                    db_path=p)
    # Ребилд
    r = sem.rebuild_from_fts(db_path=p)
    assert r["indexed"] == 2
    assert r["total"] == 2
    assert sem.count(p) == 2


def test_rebuild_empty_fts(tmp_path):
    p = _db(tmp_path)
    r = sem.rebuild_from_fts(db_path=p)
    assert r["total"] == 0
