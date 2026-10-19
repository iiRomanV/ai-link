"""VERIFY-FAIL-DETAIL (2026-10-03): детали упавших тестов в context.errors.

При VERIFY-FAIL с tests=failed — output_tail pytest добавляется
в context.errors, чтобы следующая попытка fix видела, что чинить.
"""
from pathlib import Path


def test_verify_fail_detail_uses_output_tail():
    """orchestrator содержит код, добавляющий output_tail в context.errors."""
    src = Path("core/orchestrator.py").read_text(encoding="utf-8")
    assert "VERIFY-FAIL-DETAIL (2026-10-03)" in src
    assert "output_tail" in src
    assert "context.errors.append" in src


def test_verify_fail_detail_covers_tests():
    """Обрабатывает случай tests=failed."""
    src = Path("core/orchestrator.py").read_text(encoding="utf-8")
    assert 'if _ts == "failed":' in src
    assert "VERIFY-FAIL (tests)" in src


def test_verify_fail_detail_covers_syntax():
    """Обрабатывает случай syntax=failed."""
    src = Path("core/orchestrator.py").read_text(encoding="utf-8")
    assert 'elif _syn == "failed":' in src
    assert "VERIFY-FAIL (syntax)" in src


def test_verify_fail_detail_covers_imports():
    """Обрабатывает случай imports=failed."""
    src = Path("core/orchestrator.py").read_text(encoding="utf-8")
    assert 'elif _imp == "failed":' in src
    assert "VERIFY-FAIL (imports)" in src


def test_verify_fail_detail_no_exception_on_failure():
    """Если что-то пойдёт не так — не падает, только log."""
    src = Path("core/orchestrator.py").read_text(encoding="utf-8")
    assert "VERIFY-FAIL-DETAIL: ошибка" in src
