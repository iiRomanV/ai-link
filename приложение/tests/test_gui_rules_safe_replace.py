"""Тесты для GUI-RULES и SAFE-REPLACE (2026-09-28).

GUI-RULES: системные правила в _build_file_task (QMessageBox
запрещены, ошибки в logging, тесты через pytest).

SAFE-REPLACE: pre-check в replace_in_file — предупреждает,
если удаляются def/class, которые вызываются в других файлах.
НЕ блокирует запись, только информирует.
"""

from pathlib import Path

from core.orchestrator import (
    OrchestrationContext,
    _build_file_task,
    PHASE_CODE,
    PHASE_FIX,
)
from core.hand_link import HandLink


# --------------------------------------------------------------------------- #
# GUI-RULES
# --------------------------------------------------------------------------- #
def _ctx(tmp_path, task="test task"):
    return OrchestrationContext(
        original_task=task,
        project_dir=str(tmp_path),
    )


def test_gui_rules_in_code_prompt(tmp_path):
    """_RULES попадают в промпт для code-фазы."""
    prompt = _build_file_task(_ctx(tmp_path), PHASE_CODE)
    assert "[ОБЩИЕ ПРАВИЛА" in prompt
    assert "QMessageBox" in prompt
    assert "logging" in prompt
    assert "pytest" in prompt


def test_gui_rules_in_fix_prompt(tmp_path):
    """_RULES попадают в промпт для fix-фазы."""
    prompt = _build_file_task(_ctx(tmp_path), PHASE_FIX)
    assert "[ОБЩИЕ ПРАВИЛА" in prompt
    assert "QMessageBox" in prompt


def test_gui_rules_before_task(tmp_path):
    """Правила идут ДО задачи (модель видит их первыми)."""
    marker = "уникальная_задача_xyz_987"
    prompt = _build_file_task(_ctx(tmp_path, marker), PHASE_CODE)
    pos_rules = prompt.find("[ОБЩИЕ ПРАВИЛА")
    pos_task = prompt.find(marker)
    assert pos_rules >= 0
    assert pos_task > pos_rules


def test_gui_rules_contain_three_points(tmp_path):
    """В правилах ровно 3 пункта (GUI, исключение, тесты)."""
    prompt = _build_file_task(_ctx(tmp_path), PHASE_CODE)
    # Ищем блок между [ОБЩИЕ ПРАВИЛА] и [/ОБЩИЕ ПРАВИЛА]
    start = prompt.find("[ОБЩИЕ ПРАВИЛА")
    end = prompt.find("[/ОБЩИЕ ПРАВИЛА]")
    block = prompt[start:end]
    assert "1." in block
    assert "2." in block
    assert "3." in block


# --------------------------------------------------------------------------- #
# SAFE-REPLACE — pre-check в replace_in_file
# --------------------------------------------------------------------------- #
def _setup_used_def(tmp_path):
    """settings.py с is_work_time, которую вызывает caller.py."""
    (tmp_path / "settings.py").write_text(
        "def is_work_time(h):\n    return 9 <= h <= 18\n\n"
        "def other():\n    return 'x'\n"
    )
    (tmp_path / "caller.py").write_text(
        "from settings import is_work_time\n\n"
        "def record():\n    return is_work_time(10)\n"
    )


def test_safe_replace_warns_on_used_def(tmp_path):
    """Удаление def с внешним вызовом → warning."""
    _setup_used_def(tmp_path)
    hl = HandLink(str(tmp_path))
    r = hl.execute([{
        "type": "replace_in_file",
        "path": "settings.py",
        "find": "def is_work_time(h):\n    return 9 <= h <= 18\n\n",
        "replace": "",
    }])
    assert r.get("success") is True
    warnings = r.get("safe_replace_warnings") or []
    assert len(warnings) == 1
    assert warnings[0]["file"] == "settings.py"
    symbols = {b["symbol"] for b in warnings[0]["broken_refs"]}
    assert "is_work_time" in symbols
    # usages непусты
    for b in warnings[0]["broken_refs"]:
        assert len(b["usages"]) >= 1


def test_safe_replace_no_warn_on_unused_def(tmp_path):
    """Удаление def БЕЗ внешних вызовов → без warning."""
    (tmp_path / "a.py").write_text(
        "def unused():\n    return 1\n\ndef kept():\n    return 2\n"
    )
    hl = HandLink(str(tmp_path))
    r = hl.execute([{
        "type": "replace_in_file",
        "path": "a.py",
        "find": "def unused():\n    return 1\n\n",
        "replace": "",
    }])
    assert r.get("success") is True
    assert (r.get("safe_replace_warnings") or []) == []


def test_safe_replace_no_warn_on_add(tmp_path):
    """Добавление def (не удаление) → без warning."""
    (tmp_path / "a.py").write_text("x = 1\n")
    hl = HandLink(str(tmp_path))
    r = hl.execute([{
        "type": "replace_in_file",
        "path": "a.py",
        "find": "x = 1\n",
        "replace": "x = 1\n\ndef new_func():\n    return 42\n",
    }])
    assert r.get("success") is True
    assert (r.get("safe_replace_warnings") or []) == []


def test_safe_replace_does_not_block_file_write(tmp_path):
    """Pre-check НЕ блокирует запись — файл меняется."""
    _setup_used_def(tmp_path)
    hl = HandLink(str(tmp_path))
    hl.execute([{
        "type": "replace_in_file",
        "path": "settings.py",
        "find": "def is_work_time(h):\n    return 9 <= h <= 18\n\n",
        "replace": "",
    }])
    content = (tmp_path / "settings.py").read_text()
    assert "is_work_time" not in content
    assert "def other" in content


def test_safe_replace_warn_contains_usages(tmp_path):
    """Warning содержит файл+строку usages."""
    _setup_used_def(tmp_path)
    hl = HandLink(str(tmp_path))
    r = hl.execute([{
        "type": "replace_in_file",
        "path": "settings.py",
        "find": "def is_work_time(h):\n    return 9 <= h <= 18\n\n",
        "replace": "",
    }])
    warnings = r.get("safe_replace_warnings") or []
    assert warnings
    usages = warnings[0]["broken_refs"][0]["usages"]
    files = {u["file"] for u in usages}
    assert "caller.py" in files


def test_safe_replace_no_warn_when_fragment_not_found(tmp_path):
    """Если find не найден — replace_in_file падает, без warning."""
    (tmp_path / "a.py").write_text("x = 1\n")
    hl = HandLink(str(tmp_path))
    r = hl.execute([{
        "type": "replace_in_file",
        "path": "a.py",
        "find": "НЕТ_ТАКОГО_ФРАГМЕНТА",
        "replace": "",
    }])
    # success=False (фрагмент не найден)
    assert r.get("success") is False
    assert (r.get("safe_replace_warnings") or []) == []
