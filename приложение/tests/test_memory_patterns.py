"""Тесты MEMORY-PATTERNS (state machine + caps + retirement)."""

from core.ai_link_db import init_schema
from core import memory_patterns as mp


def _db(tmp_path):
    p = tmp_path / "pat.db"
    init_schema(p)
    return p


# --------------------------------------------------------------------------- #
# add_pattern
# --------------------------------------------------------------------------- #
def test_add_pattern_candidate(tmp_path):
    """CONFIDENCE-PRIOR (2026-10-11): conf = 0.5 при создании.

    Beta(1,1) prior mean = 1/(1+1) = 0.5.
    Раньше было 0.0 (баг).
    """
    p = _db(tmp_path)
    pid = mp.add_pattern("sig1", "ctx", "fix", db_path=p)
    assert isinstance(pid, int)
    pat = mp.get_pattern(pid, p)
    assert pat["status"] == mp.STATUS_CANDIDATE
    assert pat["confidence"] == 0.5
    assert pat["evidence_count"] == 0


def test_add_pattern_idempotent(tmp_path):
    p = _db(tmp_path)
    id1 = mp.add_pattern("sig1", "ctx1", "fix1", db_path=p)
    id2 = mp.add_pattern("sig1", "ctx2", "fix2", db_path=p)
    assert id1 == id2
    # контекст не перезаписан
    pat = mp.get_pattern(id1, p)
    assert pat["context_json"] == "ctx1"


def test_add_pattern_empty(tmp_path):
    p = _db(tmp_path)
    assert mp.add_pattern("", db_path=p) is None
    assert mp.add_pattern("   ", db_path=p) is None


# --------------------------------------------------------------------------- #
# evidence + confidence
# --------------------------------------------------------------------------- #
def test_record_evidence_success(tmp_path):
    p = _db(tmp_path)
    pid = mp.add_pattern("sig1", db_path=p)
    ok = mp.record_evidence(pid, "success", run_id="r1", db_path=p)
    assert ok is True
    pat = mp.get_pattern(pid, p)
    assert pat["success_count"] == 1
    assert pat["evidence_count"] == 1
    # FIX ATTRIBUTION (Beta): 1 success, 0 fail → alpha=2, beta=1
    # conf = 2/3 ≈ 0.667
    assert abs(pat["confidence"] - 2/3) < 0.01


def test_record_evidence_failure(tmp_path):
    """FIX 1.1.1 (17.10.2026): failure+used=True штрафует (beta += 0.5).

    Раньше: failure без used → weight_f=1.0 (legacy), beta=2 → conf=1/3.
    Теперь: failure+used=True → weight_f=0.5, beta=1.5 → conf=0.4.
    Смысл: паттерн применили → не помогло → наказан, но не убит.
    """
    p = _db(tmp_path)
    pid = mp.add_pattern("sig1", db_path=p)
    mp.record_evidence(pid, "failure", used=True, db_path=p)
    pat = mp.get_pattern(pid, p)
    assert pat["failure_count"] == 1
    # alpha=1, beta=1+0.5=1.5 → conf = 1/2.5 = 0.4
    assert abs(pat["confidence"] - 0.4) < 0.01


def test_record_evidence_failure_without_used_soft_penalty(tmp_path):
    """B+ (19.10.2026): failure+used=False → МЯГКИЙ штраф (0.1).

    Раньше weight_f=0.0 — confidence не падал. Но export
    (0 success, 2 failure) оставался 0.5. Теперь — мягкий штраф,
    conf падает, но не сильно.
    """
    p = _db(tmp_path)
    pid = mp.add_pattern("sig1", db_path=p)
    conf_before = mp.get_pattern(pid, p)["confidence"]
    mp.record_evidence(pid, "failure", used=False, db_path=p)
    pat = mp.get_pattern(pid, p)
    assert pat["failure_count"] == 1
    # weight_f=0.1 → beta=1.1, conf = 1/2.1 ≈ 0.476 (было 0.5)
    assert pat["confidence"] < conf_before, (
        f"conf должен упасть: {conf_before} -> {pat['confidence']}"
    )
    # Но мягко: не ниже 0.4 (один мягкий штраф не убивает)
    assert pat["confidence"] > 0.4


def test_record_evidence_mixed(tmp_path):
    """FIX 1.1.1 (17.10.2026): mixed success+failure, failure с used=True.

    alpha = 1 + 2 = 3 (два success, verified_effect=False → weight_s=0.25*2=0.5,
    но по факту legacy/without attr → weight_s=1.0*2=2.0).
    beta = 1 + 0.5 = 1.5.
    conf = 3 / 4.5 = 0.667.
    """
    p = _db(tmp_path)
    pid = mp.add_pattern("sig1", db_path=p)
    mp.record_evidence(pid, "success", db_path=p)
    mp.record_evidence(pid, "success", db_path=p)
    mp.record_evidence(pid, "failure", used=True, db_path=p)
    pat = mp.get_pattern(pid, p)
    assert pat["success_count"] == 2
    assert pat["failure_count"] == 1
    # alpha=1+2=3, beta=1+0.5=1.5 → conf = 3/4.5 ≈ 0.667
    assert abs(pat["confidence"] - 0.667) < 0.01


def test_record_evidence_invalid_outcome(tmp_path):
    p = _db(tmp_path)
    pid = mp.add_pattern("sig1", db_path=p)
    assert mp.record_evidence(pid, "wrong", db_path=p) is False


# --------------------------------------------------------------------------- #
# state transitions
# --------------------------------------------------------------------------- #
def test_promote_manual(tmp_path):
    p = _db(tmp_path)
    pid = mp.add_pattern("sig1", db_path=p)
    assert mp.promote(pid, p) is True
    assert mp.get_pattern(pid, p)["status"] == mp.STATUS_ACTIVE


def test_promote_if_ready_not_ready(tmp_path):
    p = _db(tmp_path)
    pid = mp.add_pattern("sig1", db_path=p)
    # evidence 0 — рано
    assert mp.promote_if_ready(pid, p) is False
    assert mp.get_pattern(pid, p)["status"] == mp.STATUS_CANDIDATE


def test_promote_if_ready_ok(tmp_path):
    p = _db(tmp_path)
    pid = mp.add_pattern("sig1", db_path=p)
    mp.record_evidence(pid, "success", db_path=p)
    mp.record_evidence(pid, "success", db_path=p)
    # evidence=2, conf=1.0 → готов
    assert mp.promote_if_ready(pid, p) is True
    assert mp.get_pattern(pid, p)["status"] == mp.STATUS_ACTIVE


def test_promote_if_ready_low_conf(tmp_path):
    """FIX 1.1.1 (17.10.2026): обновлён под новую логику штрафа.

    Раньше: failure без used → weight_f=1.0 (legacy), beta рос.
    Теперь: штраф только при used=True. Здесь паттерн
    применили (used=True) и не помогло — confidence падает.
    """
    p = _db(tmp_path)
    pid = mp.add_pattern("sig1", db_path=p)
    mp.record_evidence(pid, "success", db_path=p)
    mp.record_evidence(pid, "failure", used=True, db_path=p)
    mp.record_evidence(pid, "failure", used=True, db_path=p)
    # alpha=1+1=2, beta=1+0.5+0.5=2 → conf = 2/4 = 0.5 < 0.6 → не готов
    assert mp.promote_if_ready(pid, p) is False


def test_demote(tmp_path):
    p = _db(tmp_path)
    pid = mp.add_pattern("sig1", db_path=p)
    mp.promote(pid, p)
    assert mp.demote(pid, p) is True
    assert mp.get_pattern(pid, p)["status"] == mp.STATUS_SHADOW


def test_demote_not_active(tmp_path):
    p = _db(tmp_path)
    pid = mp.add_pattern("sig1", db_path=p)
    # candidate → demote не должен сработать
    assert mp.demote(pid, p) is False


def test_retire_from_shadow(tmp_path):
    p = _db(tmp_path)
    pid = mp.add_pattern("sig1", db_path=p)
    mp.promote(pid, p)
    mp.demote(pid, p)
    assert mp.retire(pid, p) is True
    assert mp.get_pattern(pid, p)["status"] == mp.STATUS_RETIRED


def test_quarantine(tmp_path):
    p = _db(tmp_path)
    pid = mp.add_pattern("sig1", db_path=p)
    mp.promote(pid, p)
    assert mp.quarantine(pid, p) is True
    assert mp.get_pattern(pid, p)["status"] == mp.STATUS_QUARANTINED


# --------------------------------------------------------------------------- #
# caps
# --------------------------------------------------------------------------- #
def test_enforce_caps_global(tmp_path):
    p = _db(tmp_path)
    # создаём больше CAP_GLOBAL паттернов
    for i in range(mp.CAP_GLOBAL + 5):
        pid = mp.add_pattern(f"sig{i}", db_path=p)
        mp.promote(pid, p)
    assert mp.count_by_status(mp.STATUS_ACTIVE, db_path=p) == mp.CAP_GLOBAL + 5
    res = mp.enforce_caps(p)
    assert res["demoted"] >= 5
    assert mp.count_by_status(mp.STATUS_ACTIVE, db_path=p) == mp.CAP_GLOBAL


def test_count_by_status(tmp_path):
    p = _db(tmp_path)
    for i in range(3):
        pid = mp.add_pattern(f"sig{i}", db_path=p)
        mp.promote(pid, p)
    assert mp.count_by_status(mp.STATUS_ACTIVE, db_path=p) == 3


# --------------------------------------------------------------------------- #
# auto_retire
# --------------------------------------------------------------------------- #
def test_auto_retire_low_confidence(tmp_path):
    p = _db(tmp_path)
    pid = mp.add_pattern("sig1", db_path=p)
    mp.promote(pid, p)
    # 5 провалов подряд
    for _ in range(5):
        mp.record_evidence(pid, "failure", db_path=p)
    res = mp.auto_retire(db_path=p)
    assert res["to_shadow"] >= 1
    assert mp.get_pattern(pid, p)["status"] == mp.STATUS_SHADOW


# --------------------------------------------------------------------------- #
# get_for_prompt
# --------------------------------------------------------------------------- #
def test_get_for_prompt_only_active(tmp_path):
    p = _db(tmp_path)
    # candidate — не попадёт
    mp.add_pattern("sig_cand", db_path=p)
    # active — попадёт
    pid2 = mp.add_pattern("sig_act", db_path=p)
    mp.promote(pid2, p)
    r = mp.get_for_prompt(db_path=p)
    assert len(r) == 1
    assert r[0]["signature"] == "sig_act"


def test_get_for_prompt_limit(tmp_path):
    p = _db(tmp_path)
    for i in range(mp.CAP_PROMPT + 5):
        pid = mp.add_pattern(f"sig{i}", db_path=p)
        mp.promote(pid, p)
    r = mp.get_for_prompt(db_path=p)
    assert len(r) == mp.CAP_PROMPT


def test_get_for_prompt_scope(tmp_path):
    p = _db(tmp_path)
    pid1 = mp.add_pattern("sig_g", scope="global", db_path=p)
    mp.promote(pid1, p)
    pid2 = mp.add_pattern("sig_p", scope="provider",
                          scope_value="qwen", db_path=p)
    mp.promote(pid2, p)
    r = mp.get_for_prompt(scope="provider", db_path=p)
    assert len(r) == 1
    assert r[0]["signature"] == "sig_p"


# --------------------------------------------------------------------------- #
# stats
# --------------------------------------------------------------------------- #
def test_stats(tmp_path):
    p = _db(tmp_path)
    pid1 = mp.add_pattern("sig1", db_path=p)
    pid2 = mp.add_pattern("sig2", db_path=p)
    mp.promote(pid1, p)
    mp.record_evidence(pid1, "success", db_path=p)
    s = mp.stats(p)
    assert s["candidate"] == 1
    assert s["active"] == 1
    assert s["evidence_total"] == 1
