"""Тесты MEMORY-API (фасад над search + patterns)."""

from core.ai_link_db import init_schema
from core import memory_api as api


def _db(tmp_path):
    p = tmp_path / "api.db"
    init_schema(p)
    return p


# --------------------------------------------------------------------------- #
# init
# --------------------------------------------------------------------------- #
def test_init_creates_schema(tmp_path):
    p = tmp_path / "fresh.db"
    api.init(p)
    assert p.exists()


# --------------------------------------------------------------------------- #
# remember_lesson
# --------------------------------------------------------------------------- #
def test_remember_lesson_creates_both(tmp_path):
    p = _db(tmp_path)
    r = api.remember_lesson(
        signature="playwright-timeout",
        title="Playwright timeout",
        content="networkidle зависает на Qwen",
        fix="wait_for_selector вместо networkidle",
        db_path=p,
    )
    assert r["ok"] is True
    assert r["pattern_id"] is not None
    assert r["memory_id"] == "lesson:playwright-timeout"

    # Проверяем FTS
    from core import memory_search as ms
    assert ms.count(p) == 1

    # Проверяем pattern
    from core import memory_patterns as mp
    pat = mp.get_pattern(r["pattern_id"], p)
    assert pat["signature"] == "playwright-timeout"
    assert pat["status"] == mp.STATUS_CANDIDATE


def test_remember_lesson_idempotent(tmp_path):
    p = _db(tmp_path)
    r1 = api.remember_lesson("sig1", "T1", "C1", db_path=p)
    r2 = api.remember_lesson("sig1", "T2", "C2", db_path=p)
    assert r1["pattern_id"] == r2["pattern_id"]
    from core import memory_search as ms
    assert ms.count(p) == 1


def test_remember_lesson_with_scope(tmp_path):
    p = _db(tmp_path)
    r = api.remember_lesson(
        "sig1", "T", "C",
        scope="provider", scope_value="qwen",
        db_path=p,
    )
    from core import memory_search as ms
    hits = ms.search("T", scope="provider:qwen", db_path=p)
    assert len(hits) == 1


# --------------------------------------------------------------------------- #
# find_similar
# --------------------------------------------------------------------------- #
def test_find_similar(tmp_path):
    p = _db(tmp_path)
    api.remember_lesson("sig1", "Playwright timeout",
                        "networkidle зависает", db_path=p)
    api.remember_lesson("sig2", "SQLite lock",
                        "БД блокируется", db_path=p)
    r = api.find_similar("playwright", db_path=p)
    assert len(r) == 1
    assert "sig1" in r[0]["memory_id"]


def test_find_similar_empty(tmp_path):
    p = _db(tmp_path)
    r = api.find_similar("", db_path=p)
    assert r == []


# --------------------------------------------------------------------------- #
# get_active_patterns
# --------------------------------------------------------------------------- #
def test_get_active_patterns_empty(tmp_path):
    p = _db(tmp_path)
    api.remember_lesson("sig1", "T", "C", db_path=p)
    # все ещё candidate
    r = api.get_active_patterns(db_path=p)
    assert r == []


def test_get_active_patterns_after_promote(tmp_path):
    p = _db(tmp_path)
    r = api.remember_lesson("sig1", "T", "C",
                            fix="Делай X", db_path=p)
    api.promote(r["pattern_id"], p)
    pats = api.get_active_patterns(db_path=p)
    assert len(pats) == 1
    assert pats[0]["signature"] == "sig1"


# --------------------------------------------------------------------------- #
# render_for_prompt
# --------------------------------------------------------------------------- #
def test_render_for_prompt_empty(tmp_path):
    p = _db(tmp_path)
    text = api.render_for_prompt(db_path=p)
    assert text == ""


def test_render_for_prompt_has_patterns(tmp_path):
    p = _db(tmp_path)
    r1 = api.remember_lesson("sig1", "T1", "C1",
                             fix="fix1", db_path=p)
    r2 = api.remember_lesson("sig2", "T2", "C2",
                             fix="fix2", db_path=p)
    api.promote(r1["pattern_id"], p)
    api.promote(r2["pattern_id"], p)
    text = api.render_for_prompt(db_path=p)
    assert "MEMORY (2 patterns)" in text
    assert "sig1" in text
    assert "sig2" in text
    assert "fix1" in text
    assert "fix2" in text


def test_render_for_prompt_truncates(tmp_path):
    p = _db(tmp_path)
    for i in range(15):
        r = api.remember_lesson(
            f"sig{i}", f"T{i}", "C" * 500,
            fix="F" * 200, db_path=p,
        )
        api.promote(r["pattern_id"], p)
    text = api.render_for_prompt(limit=20, max_chars=500, db_path=p)
    assert len(text) <= 520
    assert "обрезано" in text


# --------------------------------------------------------------------------- #
# record_outcome + auto lifecycle
# --------------------------------------------------------------------------- #
def test_record_outcome(tmp_path):
    p = _db(tmp_path)
    r = api.remember_lesson("sig1", "T", "C", db_path=p)
    pid = r["pattern_id"]
    assert api.record_outcome(pid, "success", db_path=p) is True
    assert api.record_outcome(pid, "success", db_path=p) is True
    from core import memory_patterns as mp
    pat = mp.get_pattern(pid, p)
    assert pat["success_count"] == 2
    # FIX ATTRIBUTION (Beta): alpha=1+2=3, beta=1 → conf=3/4=0.75
    assert abs(pat["confidence"] - 0.75) < 0.01


# --------------------------------------------------------------------------- #
# управление
# --------------------------------------------------------------------------- #
def test_promote_demote_retire_quarantine(tmp_path):
    p = _db(tmp_path)
    r = api.remember_lesson("sig1", "T", "C", db_path=p)
    pid = r["pattern_id"]
    from core import memory_patterns as mp

    assert api.promote(pid, p) is True
    assert mp.get_pattern(pid, p)["status"] == mp.STATUS_ACTIVE

    assert api.demote(pid, p) is True
    assert mp.get_pattern(pid, p)["status"] == mp.STATUS_SHADOW

    assert api.retire(pid, p) is True
    assert mp.get_pattern(pid, p)["status"] == mp.STATUS_RETIRED


def test_enforce_caps(tmp_path):
    p = _db(tmp_path)
    from core import memory_patterns as mp
    for i in range(mp.CAP_GLOBAL + 3):
        r = api.remember_lesson(f"sig{i}", "T", "C", db_path=p)
        api.promote(r["pattern_id"], p)
    res = api.enforce_caps(p)
    assert res["demoted"] >= 3


def test_auto_retire(tmp_path):
    p = _db(tmp_path)
    r = api.remember_lesson("sig1", "T", "C", db_path=p)
    api.promote(r["pattern_id"], p)
    for _ in range(5):
        api.record_outcome(r["pattern_id"], "failure", db_path=p)
    res = api.auto_retire(p)
    assert res["to_shadow"] >= 1


# --------------------------------------------------------------------------- #
# stats
# --------------------------------------------------------------------------- #
def test_stats(tmp_path):
    p = _db(tmp_path)
    r1 = api.remember_lesson("sig1", "T1", "C1", db_path=p)
    api.remember_lesson("sig2", "T2", "C2", db_path=p)
    api.promote(r1["pattern_id"], p)
    s = api.stats(p)
    assert s["candidate"] == 1
    assert s["active"] == 1
    assert s["fts_count"] == 2
