"""B+ (19.10.2026): research-промпт содержит схему v1.1 (acceptance[]).

Проверяем, что _build_text_prompt для PHASE_RESEARCH:
  1. Содержит 'acceptance' и все 4 типа проверок.
  2. Содержит 'files_in_scope' / 'files_forbidden'.
  3. Содержит 'outcome' / 'severity'.
  4. Содержит пример схемы с "targets": [...].
  5. НЕ содержит старую схему "check_kind" в формате примера
     (хотя может упоминать в других контекстах).
  6. Нет дублирования строки про P0.
"""
from pathlib import Path

from core.orchestrator import (
    OrchestrationContext,
    PHASE_RESEARCH,
    _build_text_prompt,
)


def _prompt(tmp_path=None):
    ctx = OrchestrationContext(
        original_task="почини",
        project_dir=str(tmp_path) if tmp_path else "/tmp",
    )
    return _build_text_prompt(ctx, PHASE_RESEARCH)


def test_prompt_has_acceptance():
    p = _prompt()
    assert "acceptance" in p


def test_prompt_has_all_check_types():
    p = _prompt()
    assert "grep_absent" in p
    assert "grep_present" in p
    assert "file_exists" in p
    assert "python_compile" in p


def test_prompt_has_scope_fields():
    p = _prompt()
    assert "files_in_scope" in p
    assert "files_forbidden" in p


def test_prompt_has_outcome_and_severity():
    p = _prompt()
    assert "outcome" in p
    assert "severity" in p


def test_prompt_has_targets_example():
    p = _prompt()
    assert '"targets"' in p
    # Пример — с acceptance[]
    assert '"acceptance"' in p


def test_prompt_no_duplicate_p0_line():
    p = _prompt()
    # Раньше строка дублировалась. Теперь — один раз.
    assert p.count("Если P0 в отчётах нет") == 1


def test_prompt_no_old_check_kind_in_example():
    """Старая схема check_kind не должна быть в примере формата.

    Допустимо упоминание в тексте (для совместимости), но
    в блоке "Верни строго JSON" — только v1.1.
    """
    p = _prompt()
    # Найдём блок "Верни строго JSON"
    idx = p.find("Верни строго JSON")
    assert idx >= 0
    # Возьмём кусок после него до "ФОРМАТ ПОЛЕЙ"
    end = p.find("ФОРМАТ ПОЛЕЙ", idx)
    assert end > idx
    block = p[idx:end]
    assert "check_kind" not in block, (
        "в примере JSON всё ещё старая схема check_kind"
    )
    assert "acceptance" in block
