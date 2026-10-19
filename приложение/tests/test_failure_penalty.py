"""FIX 1.1.1 (17.10.2026): проверка, что failures штрафуют паттерн.

Контекст: до этой правки в pattern_evidence все 224 записи были success.
Провалы отсекались фильтром eligible/used до записи, а weight_f
при failure был всегда 0.0 (legacy verified_effect=True давал weight_f=0.25,
но реального штрафа при used=False не было). Итог: память не училась
на провалах, state machine (5 провалов → shadow) не срабатывала.

Проверяем:
  1. failure + used=True → confidence падает (weight_f=0.5).
  2. failure + used=False → confidence НЕ меняется (weight_f=0.0).
  3. failure + eligible=True, used=False → confidence НЕ меняется.
  4. success + verified_effect=True → confidence растёт (регрессия).
"""
import pytest
from core import memory_patterns as mp


def _make_pattern(db, signature="test failure penalty pattern xyz",
                  confidence=0.5, alpha=1.0, beta=1.0):
    pid = mp.add_pattern(
        signature=signature,
        context_json="test context",
        fix="test fix",
        scope="global",
        source="test",
        db_path=db,
    )
    con = mp._con(db)
    try:
        con.execute(
            "UPDATE learning_patterns "
            "SET alpha=?, beta=?, confidence=? WHERE id=?",
            (alpha, beta, confidence, pid),
        )
        con.commit()
    finally:
        con.close()
    return pid


def _get_pattern(db, pid):
    return mp.get_pattern(pid, db_path=db)


def test_failure_with_used_penalizes(tmp_path):
    """failure + used=True → confidence падает (beta += 0.5)."""
    db = tmp_path / "t1.db"
    pid = _make_pattern(db, confidence=0.8, alpha=4.0, beta=1.0)
    conf_before = _get_pattern(db, pid)["confidence"]

    ok = mp.record_evidence(
        pattern_id=pid,
        outcome="failure",
        used=True,
        eligible=True,
        db_path=db,
    )
    assert ok, "record_evidence вернул False"

    p_after = _get_pattern(db, pid)
    assert p_after["confidence"] < conf_before, (
        f"confidence не упал: было {conf_before}, "
        f"стало {p_after['confidence']}"
    )
    assert p_after["failure_count"] == 1


def test_failure_without_used_soft_penalty(tmp_path):
    """B+ (19.10.2026): failure + used=False → МЯГКИЙ штраф (0.1).

    Раньше confidence не падал. Теперь — мягко падает.
    alpha=4, beta=1 → beta=1.1, conf = 4/5.1 ≈ 0.784 (было 0.8).
    """
    db = tmp_path / "t2.db"
    pid = _make_pattern(db, confidence=0.8, alpha=4.0, beta=1.0)
    conf_before = _get_pattern(db, pid)["confidence"]

    ok = mp.record_evidence(
        pattern_id=pid,
        outcome="failure",
        used=False,
        eligible=False,
        db_path=db,
    )
    assert ok

    p_after = _get_pattern(db, pid)
    assert p_after["confidence"] < conf_before, (
        f"conf должен упасть: {conf_before} -> {p_after['confidence']}"
    )
    # Мягко: падение < 0.05 (не как used=True, где 0.5)
    assert (conf_before - p_after["confidence"]) < 0.05, (
        f"слишком сильный штраф: {conf_before - p_after['confidence']}"
    )
    assert p_after["failure_count"] == 1


def test_failure_eligible_but_not_used_soft_penalty(tmp_path):
    """B+ (19.10.2026): failure + eligible=True, used=False → мягкий штраф.

    eligible=True, но not used — паттерн был релевантен, но модель
    не использовала. Штраф 0.1 (как used=False).
    """
    db = tmp_path / "t3.db"
    pid = _make_pattern(db, confidence=0.8, alpha=4.0, beta=1.0)
    conf_before = _get_pattern(db, pid)["confidence"]

    ok = mp.record_evidence(
        pattern_id=pid,
        outcome="failure",
        eligible=True,
        used=False,
        db_path=db,
    )
    assert ok
    p_after = _get_pattern(db, pid)
    assert p_after["confidence"] < conf_before, (
        f"conf должен упасть: {conf_before} -> {p_after['confidence']}"
    )
    assert (conf_before - p_after["confidence"]) < 0.05


def test_success_still_works(tmp_path):
    """success + verified_effect=True → confidence растёт (регрессия)."""
    db = tmp_path / "t4.db"
    pid = _make_pattern(db, confidence=0.5, alpha=1.0, beta=1.0)
    conf_before = _get_pattern(db, pid)["confidence"]

    ok = mp.record_evidence(
        pattern_id=pid,
        outcome="success",
        verified_effect=True,
        used=True,
        eligible=True,
        supported=True,
        db_path=db,
    )
    assert ok

    p_after = _get_pattern(db, pid)
    assert p_after["confidence"] > conf_before, (
        f"confidence не вырос: было {conf_before}, "
        f"стало {p_after['confidence']}"
    )
