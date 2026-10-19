"""B+ (18.10.2026): research-промпт содержит СОДЕРЖИМОЕ отчётов.

Проблема, найденная в прогоне 18.10 (17:29):
  research-фаза запустилась, но targets=0, потому что промпт
  давал только СПИСОК имён файлов, а TEXT_PHASE не умеет читать.
  LLM ответила: «я не имею содержимого файлов».

Фикс: _build_text_prompt для PHASE_RESEARCH читает содержимое
отчётов (первые 3500 символов каждого, всего ≤12000) и вставляет
в промпт.

Проверяем:
  1. Содержимое ИНВЕНТАРИЗАЦИЯ_ПРОБЛЕМ.md попадает в промпт.
  2. Содержимое ошибки.md попадает в промпт.
  3. Приоритет: ИНВЕНТАРИЗАЦИЯ идёт раньше ДИАГНОСТИКА.
  4. Лимит: если отчёт огромный — обрезается до 3500.
  5. Если отчётов нет — промпт не падает.
"""
from pathlib import Path

from core.orchestrator import (
    OrchestrationContext,
    PHASE_RESEARCH,
    _build_text_prompt,
)


def _write(project_dir, name, text):
    p = Path(project_dir) / name
    p.write_text(text, encoding="utf-8")
    return p


def test_prompt_contains_inventarizaciya_content(tmp_path):
    _write(tmp_path, "ИНВЕНТАРИЗАЦИЯ_ПРОБЛЕМ.md", (
        "## Приоритеты на этап 3\n"
        "1. **P0 (критично):** п.1 — надёжная инициализация БД\n"
        "2. **P0 (правила):** C — секреты через env\n"
    ))
    ctx = OrchestrationContext(
        original_task="почини",
        project_dir=str(tmp_path),
    )
    p = _build_text_prompt(ctx, PHASE_RESEARCH)
    assert "=== ИНВЕНТАРИЗАЦИЯ_ПРОБЛЕМ.md ===" in p
    assert "P0 (критично)" in p
    assert "секреты через env" in p


def test_prompt_contains_oshibki_content(tmp_path):
    _write(tmp_path, "ошибки.md", (
        "# Журнал ошибок\n"
        "## Критические ошибки\n"
        "### 4. Отсутствие таблицы plan\n"
        "Файл: database.py\n"
    ))
    ctx = OrchestrationContext(
        original_task="почини",
        project_dir=str(tmp_path),
    )
    p = _build_text_prompt(ctx, PHASE_RESEARCH)
    assert "=== ошибки.md ===" in p
    assert "Отсутствие таблицы plan" in p
    assert "database.py" in p


def test_prompt_priority_order(tmp_path):
    """ИНВЕНТАРИЗАЦИЯ должна идти раньше ДИАГНОСТИКА."""
    _write(tmp_path, "ИНВЕНТАРИЗАЦИЯ_ПРОБЛЕМ.md", "MARK_INVENTAR")
    _write(tmp_path, "ДИАГНОСТИКА_ЭТАП2.md", "MARK_DIAGNOST")
    ctx = OrchestrationContext(
        original_task="t",
        project_dir=str(tmp_path),
    )
    p = _build_text_prompt(ctx, PHASE_RESEARCH)
    idx_inv = p.find("MARK_INVENTAR")
    idx_diag = p.find("MARK_DIAGNOST")
    assert idx_inv >= 0
    assert idx_diag >= 0
    assert idx_inv < idx_diag, (
        f"ИНВЕНТАРИЗАЦИЯ должна идти раньше ДИАГНОСТИКА: "
        f"inv={idx_inv}, diag={idx_diag}"
    )


def test_prompt_truncates_large_report(tmp_path):
    """Отчёт 10000 символов обрезается до 3500."""
    _write(tmp_path, "ошибки.md", "X" * 10000)
    ctx = OrchestrationContext(
        original_task="t",
        project_dir=str(tmp_path),
    )
    p = _build_text_prompt(ctx, PHASE_RESEARCH)
    # В промпте должно быть не больше ~3500 подряд идущих X
    # (плюс обвязка). Если обрезки нет — будет 10000 X.
    x_run = "X" * 3600
    assert x_run not in p, "обрезание до 3500 не сработало"
    # Хотя бы 3000 X должно быть (первые 3500 минус обвязка)
    assert "X" * 3000 in p


def test_prompt_no_reports_does_not_crash(tmp_path):
    """Нет отчётов — промпт собирается без ошибок."""
    ctx = OrchestrationContext(
        original_task="новый проект",
        project_dir=str(tmp_path),
    )
    p = _build_text_prompt(ctx, PHASE_RESEARCH)
    assert "ТЫ — ФАЗА RESEARCH" in p
    assert "targets" in p
