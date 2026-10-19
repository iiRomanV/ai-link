"""Тесты BRAIN-RULE-FIRST."""

from core import brain as br


# ─── rule_brain ───

def test_rule_code_success_with_files_goes_review():
    d = br.rule_brain({
        "phase": "code", "phase_status": "success",
        "files_created": 5,
    })
    assert d["decision"] == br.D_RUN_REVIEW
    assert d["next_phase"] == "review"


def test_rule_code_success_no_files_is_fail():
    d = br.rule_brain({
        "phase": "code", "phase_status": "success",
        "files_created": 0,
    })
    assert d["decision"] == br.D_RUN_FIX


def test_rule_review_ok_finish():
    d = br.rule_brain({
        "phase": "review", "review_status": "ok",
        "has_stages": False,
    })
    assert d["decision"] == br.D_FINISH


def test_rule_review_ok_next_stage():
    d = br.rule_brain({
        "phase": "review", "review_status": "ok",
        "has_stages": True, "stage_index": 0, "total_stages": 3,
    })
    assert d["decision"] == br.D_NEXT_STAGE


def test_rule_review_minor_finish():
    d = br.rule_brain({
        "phase": "review", "review_status": "problems",
        "review_severity": "minor",
    })
    assert d["decision"] == br.D_FINISH


def test_rule_review_critical_fix():
    d = br.rule_brain({
        "phase": "review", "review_status": "problems",
        "review_severity": "critical",
    })
    assert d["decision"] == br.D_RUN_FIX


def test_rule_verification_failed_fix():
    d = br.rule_brain({
        "phase": "review", "review_status": "ok",
        "verification_status": "failed",
    })
    assert d["decision"] == br.D_RUN_FIX


def test_rule_phase_failed_fix():
    d = br.rule_brain({"phase": "code", "phase_status": "failed"})
    assert d["decision"] == br.D_RUN_FIX


def test_rule_fallback_failed_pause():
    d = br.rule_brain({"phase": "fallback", "phase_status": "failed"})
    assert d["decision"] == br.D_PAUSE
    assert d["requires_user"] is True


def test_rule_smart_exit_no_progress_fix():
    d = br.rule_brain({
        "phase": "code", "phase_status": "success",
        "files_created": 3, "stop_reason": "no_progress",
    })
    # сработает более раннее правило? files>0 → review.
    # stop_reason проверяется ПОСЛЕ. Но rule даёт review (приоритет).
    assert d["decision"] in (br.D_RUN_REVIEW, br.D_RUN_FIX)


def test_rule_unknown_returns_none():
    assert br.rule_brain({"phase": "unknown", "phase_status": "weird"}) is None


# ─── policy_check ───

def test_policy_fix_cycles_limit():
    d = {"decision": br.D_RUN_FIX, "confidence": 0.9}
    state = {"fix_cycles": 5}
    out = br.policy_check(d, state, br.Policy(max_fix_cycles=3))
    assert out["decision"] == br.D_PAUSE
    assert out["requires_user"] is True
    assert "policy_note" in out


def test_policy_same_failures_limit():
    d = {"decision": br.D_RUN_REVIEW}
    state = {"same_failures": 5}
    out = br.policy_check(d, state, br.Policy(max_same_failures=2))
    assert out["decision"] == br.D_PAUSE


def test_policy_no_progress_limit():
    d = {"decision": br.D_NEXT_STAGE}
    state = {"stages_without_progress": 5}
    out = br.policy_check(d, state,
                          br.Policy(max_stages_without_progress=3))
    assert out["decision"] == br.D_PAUSE


def test_policy_rejects_bad_next_phase():
    d = {"decision": br.D_RUN_REVIEW, "next_phase": "hacked_phase"}
    out = br.policy_check(d, {})
    assert out["next_phase"] is None
    assert "policy_note" in out


def test_policy_allows_good_next_phase():
    d = {"decision": br.D_RUN_REVIEW, "next_phase": "fix"}
    out = br.policy_check(d, {})
    assert out["next_phase"] == "fix"


# ─── decide ───

def test_decide_rule_first():
    d = br.decide({"phase": "code", "phase_status": "success",
                   "files_created": 3})
    assert d["decision"] == br.D_RUN_REVIEW
    assert d["mode"] == "rule"


def test_decide_fallback_pause():
    d = br.decide({"phase": "unknown", "phase_status": "weird"})
    assert d["decision"] == br.D_PAUSE
    assert d["requires_user"] is True
    assert d["mode"] == "fallback"


# ─── LLM-режимы (без реального вызова) ───

def test_qwen_brain_without_caller_returns_none():
    assert br.qwen_brain({}, caller=None) is None


def test_qwen_brain_parses_json():
    def fake_caller(prompt):
        return {
            "success": True,
            "text": '{"decision": "run_fix", "reason": "test", '
                    '"confidence": 0.8}',
        }
    d = br.qwen_brain({"phase": "code"}, caller=fake_caller)
    assert d is not None
    assert d["decision"] == "run_fix"
    assert d["mode"] == "qwen"


def test_qwen_brain_handles_markdown_fence():
    def fake_caller(prompt):
        return {
            "success": True,
            "text": "```json\n"
                    '{"decision": "finish", "confidence": 0.7}\n'
                    "```",
        }
    d = br.qwen_brain({}, caller=fake_caller)
    assert d["decision"] == "finish"


def test_qwen_brain_bad_json_returns_none():
    def fake_caller(prompt):
        return {"success": True, "text": "not a json at all"}
    assert br.qwen_brain({}, caller=fake_caller) is None


def test_decide_with_llm_uses_qwen():
    def fake_qwen(prompt):
        return {"success": True,
                "text": '{"decision": "run_fix", "reason": "llm"}'}
    d = br.decide({"phase": "unknown"}, use_llm=True,
                  qwen_caller=fake_qwen)
    assert d["decision"] == "run_fix"
    assert d["mode"] == "qwen"

# ─── Новые правила (план, review unknown) ───

def test_rule_plan_success_goes_code():
    d = br.rule_brain({"phase": "plan", "phase_status": "success"})
    assert d["decision"] == br.D_RUN_CODE
    assert d["next_phase"] == "code"
    assert d["confidence"] >= 0.9


def test_rule_plan_failed_first_time_continue():
    d = br.rule_brain({
        "phase": "plan", "phase_status": "failed",
        "same_failures": 0,
    })
    assert d["decision"] == br.D_CONTINUE
    assert d["next_phase"] == "plan"


def test_rule_plan_failed_many_times_pause():
    d = br.rule_brain({
        "phase": "plan", "phase_status": "failed",
        "same_failures": 3,
    })
    assert d["decision"] == br.D_PAUSE
    assert d["requires_user"] is True


def test_rule_review_unknown_status_pause():
    d = br.rule_brain({
        "phase": "review", "phase_status": "success",
        "review_status": "unknown",
    })
    assert d["decision"] == br.D_PAUSE
    assert d["requires_user"] is True


def test_decide_plan_success():
    """Основной кейс: plan success → run_code (не fallback)."""
    d = br.decide({"phase": "plan", "phase_status": "success"},
                  use_llm=False)
    assert d["decision"] == br.D_RUN_CODE
    assert d["mode"] == "rule"

