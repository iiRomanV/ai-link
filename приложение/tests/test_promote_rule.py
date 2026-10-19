"""B+ (19.10.2026): promote только через правило (1.1.2).

Проблема: _auto_create_pattern_from_context вызывал _mem.promote(pid)
безусловно. Мусор (config-cli, app, .env) попадал в active
с confidence=0.5, evidence=0 — в промпт, но никогда не проверялся.

Фикс: _mem.promote_if_ready(pid). Правило:
  candidate → active: evidence >= 2 И confidence >= 0.6.
  Иначе — остаётся candidate.
"""
import pytest
from core import memory_patterns as mp


def _make_pattern(db, signature="test promote rule pattern xyz",
                  confidence=0.5, alpha=1.0, beta=1.0,
                  evidence_count=0):
    pid = mp.add_pattern(
        signature=signature,
        context_json="ctx",
        fix="fix",
        scope="global",
        source="test",
        db_path=db,
    )
    con = mp._con(db)
    try:
        con.execute(
            "UPDATE learning_patterns "
            "SET alpha=?, beta=?, confidence=?, evidence_count=? "
            "WHERE id=?",
            (alpha, beta, confidence, evidence_count, pid),
        )
        con.commit()
    finally:
        con.close()
    return pid


def test_promote_if_ready_low_evidence(tmp_path):
    """evidence_count=1 < 2 → не active."""
    db = tmp_path / "t1.db"
    pid = _make_pattern(db, confidence=0.9, evidence_count=1)
    assert mp.promote_if_ready(pid, db_path=db) is False
    p = mp.get_pattern(pid, db_path=db)
    assert p["status"] == mp.STATUS_CANDIDATE


def test_promote_if_ready_low_conf(tmp_path):
    """confidence=0.5 < 0.6 → не active."""
    db = tmp_path / "t2.db"
    pid = _make_pattern(db, confidence=0.5, evidence_count=5)
    assert mp.promote_if_ready(pid, db_path=db) is False
    p = mp.get_pattern(pid, db_path=db)
    assert p["status"] == mp.STATUS_CANDIDATE


def test_promote_if_ready_ok(tmp_path):
    """evidence>=2, conf>=0.6 → active."""
    db = tmp_path / "t3.db"
    pid = _make_pattern(db, confidence=0.7, evidence_count=3)
    assert mp.promote_if_ready(pid, db_path=db) is True
    p = mp.get_pattern(pid, db_path=db)
    assert p["status"] == mp.STATUS_ACTIVE


def test_auto_create_uses_promote_if_ready():
    """Проверяем, что в orchestrator.py нет безусловного promote."""
    from pathlib import Path
    src = Path("core/orchestrator.py").read_text(encoding="utf-8")
    # Не должно быть _mem.promote(pid) — только promote_if_ready
    assert "_mem.promote(" not in src, (
        "в orchestrator.py остался безусловный _mem.promote()"
    )
    assert "promote_if_ready" in src
