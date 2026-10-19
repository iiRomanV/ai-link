"""B+ (19.10.2026): авто-понижение (1.1.6).

Проблема: export (0 success, 2 failure) — active. Паттерны,
которые retrieved=1, used=0 много раз — мусор в промпте.
Нет авто-понижения.

Фикс: _count_consecutive_unused + в record_evidence:
10 раз подряд retrieved=1, used=0 → shadow (порог 10,
было 5 — test-models ушёл в shadow после 5 unused).
"""
import pytest
from core import memory_patterns as mp


def _make_pattern(db, signature="test auto demote pattern xyz"):
    pid = mp.add_pattern(
        signature=signature,
        context_json="ctx",
        fix="fix",
        scope="global",
        source="test",
        db_path=db,
    )
    return pid


def _get(db, pid):
    return mp.get_pattern(pid, db_path=db)


def test_consecutive_unused_count(tmp_path):
    """5 evidence retrieved=1, used=0 → count=5."""
    db = tmp_path / "t1.db"
    pid = _make_pattern(db)
    for _ in range(5):
        mp.record_evidence(
            pattern_id=pid, outcome="success",
            retrieved=True, used=False, eligible=True,
            db_path=db,
        )
    n = mp._count_consecutive_unused(pid, db_path=db)
    assert n == 5


def test_consecutive_unused_breaks_on_used(tmp_path):
    """Если был used=True — счётчик сбрасывается."""
    db = tmp_path / "t2.db"
    pid = _make_pattern(db)
    # 3 unused
    for _ in range(3):
        mp.record_evidence(
            pattern_id=pid, outcome="success",
            retrieved=True, used=False, eligible=True,
            db_path=db,
        )
    # 1 used
    mp.record_evidence(
        pattern_id=pid, outcome="success",
        retrieved=True, used=True, supported=True,
        db_path=db,
    )
    # 1 unused
    mp.record_evidence(
        pattern_id=pid, outcome="success",
        retrieved=True, used=False, eligible=True,
        db_path=db,
    )
    # После used — счётчик = 1 (только последний)
    n = mp._count_consecutive_unused(pid, db_path=db)
    assert n == 1


def test_auto_demote_after_10_unused(tmp_path):
    """B+ (19.10.2026): 10 × retrieved=1, used=0 → shadow.

    Порог 5 → 10: test-models (86 evidence, used=11) ушёл
    в shadow после 5 unused подряд. 10 — баланс.
    """
    db = tmp_path / "t3.db"
    pid = _make_pattern(db)
    mp.promote(pid, db_path=db)
    assert _get(db, pid)["status"] == mp.STATUS_ACTIVE
    for _ in range(10):
        mp.record_evidence(
            pattern_id=pid, outcome="success",
            retrieved=True, used=False, eligible=True,
            db_path=db,
        )
    p = _get(db, pid)
    assert p["status"] == mp.STATUS_SHADOW, (
        f"после 10 unused статус должен быть shadow, "
        f"а он {p['status']}"
    )


def test_no_demote_before_10_unused(tmp_path):
    """9 × unused — ещё active (не shadow)."""
    db = tmp_path / "t4.db"
    pid = _make_pattern(db)
    mp.promote(pid, db_path=db)
    for _ in range(9):
        mp.record_evidence(
            pattern_id=pid, outcome="success",
            retrieved=True, used=False, eligible=True,
            db_path=db,
        )
    p = _get(db, pid)
    assert p["status"] == mp.STATUS_ACTIVE, (
        f"после 9 unused не должен быть shadow: {p['status']}"
    )
