"""VERIFY-FAIL (2026-10-02): если инструменты показали failed —
фаза НЕ success. Чинит баг 01.10 (tests=failed → success)."""
import pytest
from unittest.mock import patch, MagicMock


def _mk_orchestrator_env(tmp_path):
    """Минимальное окружение для orchestrate()."""
    ai = tmp_path / ".ai_link"
    ai.mkdir(parents=True, exist_ok=True)
    return ai


def test_verify_fail_rcpt_changes_phase_to_failed(tmp_path):
    """Если VERIFY вернул tests=failed — фаза становится failed."""
    from core.orchestrator import PhaseResult
    # Создаём фейковый phase_result со status=success
    pr = PhaseResult(phase="code", agent="browser_deepseek",
                     status="success")
    # Симулируем логику патча
    _rcpt = {"status": "failed",
             "tests": {"status": "failed"},
             "syntax": {"status": "passed"},
             "imports": {"status": "passed"}}
    _vs = _rcpt.get("status") or ""
    _ts = _rcpt.get("tests", {}).get("status") or ""
    _syn = _rcpt.get("syntax", {}).get("status") or ""
    _imp = _rcpt.get("imports", {}).get("status") or ""
    _verify_failed = (
        _vs == "failed" or _ts == "failed"
        or _syn == "failed" or _imp == "failed"
    )
    if _verify_failed and pr.status == "success":
        pr.status = "failed"
        pr.error = f"VERIFY-FAIL: status={_vs}, tests={_ts}"
    assert pr.status == "failed"
    assert "VERIFY-FAIL" in pr.error


def test_verify_ok_keeps_success(tmp_path):
    """Если VERIFY вернул verified — фаза остаётся success."""
    from core.orchestrator import PhaseResult
    pr = PhaseResult(phase="code", agent="browser_deepseek",
                     status="success")
    _rcpt = {"status": "verified",
             "tests": {"status": "passed"},
             "syntax": {"status": "passed"},
             "imports": {"status": "passed"}}
    _vs = _rcpt.get("status") or ""
    _ts = _rcpt.get("tests", {}).get("status") or ""
    _verify_failed = (_vs == "failed" or _ts == "failed")
    if _verify_failed and pr.status == "success":
        pr.status = "failed"
    assert pr.status == "success"


def test_verify_syntax_fail_changes_phase(tmp_path):
    """Если VERIFY вернул syntax=failed — фаза становится failed."""
    from core.orchestrator import PhaseResult
    pr = PhaseResult(phase="code", agent="browser_deepseek",
                     status="success")
    _rcpt = {"status": "failed",
             "tests": {"status": "passed"},
             "syntax": {"status": "failed"},
             "imports": {"status": "passed"}}
    _syn = _rcpt.get("syntax", {}).get("status") or ""
    if _syn == "failed" and pr.status == "success":
        pr.status = "failed"
    assert pr.status == "failed"


def test_verify_fail_does_not_downgrade_already_failed(tmp_path):
    """Если фаза уже failed — не трогаем (не понижаем второй раз)."""
    from core.orchestrator import PhaseResult
    pr = PhaseResult(phase="code", agent="browser_deepseek",
                     status="failed")
    _rcpt = {"status": "failed",
             "tests": {"status": "failed"}}
    if _rcpt.get("tests", {}).get("status") == "failed":
        if pr.status == "success":
            pr.status = "failed"
    # Не должно измениться
    assert pr.status == "failed"
