"""Тесты для core.cross_verifier (FIX CROSS-VERIFY 2026-09-29)."""

from pathlib import Path
from unittest.mock import patch, MagicMock

from core.cross_verifier import (
    _is_agent_available,
    get_verifier,
    _collect_code,
)


# --------------------------------------------------------------------------- #
# _is_agent_available
# --------------------------------------------------------------------------- #
def test_is_agent_available_qwen_enabled():
    """Qwen включён → True."""
    with patch("core.agents.browser_sites_manager.get_site") as m:
        m.return_value = {"id": "qwen", "enabled": True}
        assert _is_agent_available("browser_qwen") is True


def test_is_agent_available_qwen_disabled():
    """Qwen выключен → False."""
    with patch("core.agents.browser_sites_manager.get_site") as m:
        m.return_value = {"id": "qwen", "enabled": False}
        assert _is_agent_available("browser_qwen") is False


def test_is_agent_available_non_browser():
    """Ollama / GigaChat — не browser, считаем доступными."""
    assert _is_agent_available("ollama") is True
    assert _is_agent_available("gigachat") is True


def test_is_agent_available_empty():
    """Пустое имя → False."""
    assert _is_agent_available("") is False
    assert _is_agent_available(None) is False


def test_is_agent_available_site_not_found():
    """Сайт не найден в конфиге → False."""
    with patch("core.agents.browser_sites_manager.get_site") as m:
        m.return_value = None
        assert _is_agent_available("browser_unknown") is False


# --------------------------------------------------------------------------- #
# get_verifier — fallback при выключенном предпочтительном
# --------------------------------------------------------------------------- #
def test_get_verifier_no_role_returns_empty():
    """ROLES (2026-10-01): роль 'verifier' не назначена → пусто."""
    with patch("core.capability_benchmark.get_assigned_agent",
               return_value=None):
        v = get_verifier("browser_deepseek")
        assert v == ""


def test_get_verifier_assigned_used():
    """ROLES (2026-10-01): verifier назначен → используется."""
    with patch("core.capability_benchmark.get_assigned_agent",
               return_value="browser_perplexity"), \
         patch("core.cross_verifier._is_agent_available",
               return_value=True):
        v = get_verifier("browser_deepseek")
        assert v == "browser_perplexity"


def test_get_verifier_nothing_available():
    """Ничего не доступно → пустая строка."""
    with patch("core.cross_verifier._is_agent_available",
               return_value=False):
        v = get_verifier("browser_deepseek")
        assert v == ""


def test_get_verifier_override_available():
    """Override доступен → он и выбран."""
    with patch("core.cross_verifier._is_agent_available",
               return_value=True):
        v = get_verifier("browser_deepseek",
                         overrides={"browser_deepseek": "ollama"})
        assert v == "ollama"


# --------------------------------------------------------------------------- #
# _collect_code — VBA + skip _reference
# --------------------------------------------------------------------------- #
def test_collect_code_finds_vba(tmp_path):
    """Собирает .bas/.frm/.cls, не только .py."""
    (tmp_path / "src" / "modules").mkdir(parents=True)
    (tmp_path / "src" / "forms").mkdir(parents=True)
    (tmp_path / "src" / "modules" / "modFormat.bas").write_text(
        "Public Function RoundHalfUp(...)\nEnd Function"
    )
    (tmp_path / "src" / "forms" / "frmLogin.frm").write_text(
        "Begin UserForm frmLogin\nEnd"
    )
    (tmp_path / "src" / "ThisWorkbook.cls").write_text(
        "Private Sub Workbook_Open()\nEnd Sub"
    )
    code = _collect_code(str(tmp_path))
    assert "modFormat.bas" in code
    assert "frmLogin.frm" in code
    assert "ThisWorkbook.cls" in code


def test_collect_code_skips_reference(tmp_path):
    """_reference/ игнорируется — это эталон."""
    (tmp_path / "_reference").mkdir()
    (tmp_path / "_reference" / "database.py").write_text(
        "# эталонный код, не наш\n"
    )
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "modData.bas").write_text(
        "Public Sub Test()\nEnd Sub"
    )
    code = _collect_code(str(tmp_path))
    assert "modData.bas" in code
    assert "database.py" not in code
    assert "_reference" not in code


def test_collect_code_skips_pycache(tmp_path):
    """__pycache__ игнорируется."""
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "__pycache__" / "x.py").write_text("x = 1")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "modMain.bas").write_text("Sub M\nEnd Sub")
    code = _collect_code(str(tmp_path))
    assert "modMain.bas" in code
    assert "__pycache__" not in code


def test_collect_code_empty(tmp_path):
    """Пустой проект → сообщение «нет кода»."""
    code = _collect_code(str(tmp_path))
    assert code == "(нет кода)"
