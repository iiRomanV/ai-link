"""CLEANUP (2026-10-04): DEFAULT_VERIFIERS удалён, дефолт review_agent = ""."""
from pathlib import Path


def test_no_default_verifiers_dict():
    """DEFAULT_VERIFIERS dict удалён из кода (только комментарий)."""
    src = Path("core/cross_verifier.py").read_text(encoding="utf-8")
    # Не должно быть объявления dict
    assert "DEFAULT_VERIFIERS = {" not in src
    # Комментарий про удаление должен быть
    assert "CLEANUP (2026-10-04)" in src


def test_verify_problem_default_empty():
    """Дефолт review_agent = '' (не hardcoded)."""
    src = Path("core/cross_verifier.py").read_text(encoding="utf-8")
    assert 'review_agent: str = ""' in src
    assert 'review_agent: str = "browser_qwen"' not in src


def test_get_verifier_uses_role_not_dict():
    """get_verifier использует роль verifier, не dict."""
    src = Path("core/cross_verifier.py").read_text(encoding="utf-8")
    # Ищем вызов get_assigned_agent
    assert 'get_assigned_agent("verifier")' in src


def test_site_map_kept():
    """site_map (преобразование имя → сайт) остаётся."""
    src = Path("core/cross_verifier.py").read_text(encoding="utf-8")
    assert '"browser_qwen": "qwen"' in src
    assert '"browser_deepseek": "deepseek"' in src


def test_no_hardcoded_qwen_default_in_verify():
    """verify_problem не дефолтится на browser_qwen."""
    from core.cross_verifier import verify_problem
    import inspect
    sig = inspect.signature(verify_problem)
    assert sig.parameters["review_agent"].default == ""
