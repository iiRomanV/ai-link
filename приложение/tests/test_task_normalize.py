"""TASK-NORM (2026-10-01): нормализация task для STALE-STATE."""
import json
from pathlib import Path

from core.orchestrator import _normalize_task, _reset_stale_state, _checkpoint_path


def test_normalize_basic():
    """lowercase + схлопнутые пробелы."""
    assert _normalize_task("Добавь Кнопку") == "добавь кнопку"
    assert _normalize_task("  Добавь   кнопку  ") == "добавь кнопку"
    assert _normalize_task("Добавь\nкнопку\tCSV") == "добавь кнопку csv"


def test_normalize_strips_punctuation():
    """Хвостовые знаки препинания убраны."""
    assert _normalize_task("добавь кнопку.") == "добавь кнопку"
    assert _normalize_task("добавь кнопку!!!") == "добавь кнопку"
    assert _normalize_task("добавь кнопку...") == "добавь кнопку"
    # Внутренние знаки не трогаем
    assert _normalize_task("добавь кнопку, CSV и всё") == "добавь кнопку, csv и всё"


def test_normalize_empty():
    """None/пусто → пустая строка."""
    assert _normalize_task("") == ""
    assert _normalize_task(None) == ""
    assert _normalize_task("   ") == ""


def _write_cp(project_dir: Path, task: str) -> None:
    ai = project_dir / ".ai_link"
    ai.mkdir(parents=True, exist_ok=True)
    cp = {
        "run_id": "test",
        "task": task,
        "status": "running",
        "phase": {"current": "code", "stage_index": 1, "total_stages": 4},
    }
    _checkpoint_path(str(project_dir)).write_text(
        json.dumps(cp, ensure_ascii=False), encoding="utf-8"
    )


def test_stale_state_normalized_same_task(tmp_path):
    """Та же задача с точкой → НЕ сбрасывается."""
    _write_cp(tmp_path, "Добавь кнопку CSV")
    # Тот же текст + точка → нормализация совпадает → не сбрасываем
    assert _reset_stale_state(str(tmp_path), "Добавь кнопку CSV.") is False
    assert _checkpoint_path(str(tmp_path)).exists()


def test_stale_state_normalized_case(tmp_path):
    """Та же задача другим регистром → НЕ сбрасывается."""
    _write_cp(tmp_path, "добавь кнопку csv")
    assert _reset_stale_state(str(tmp_path), "ДОБАВЬ КНОПКУ CSV") is False
    assert _checkpoint_path(str(tmp_path)).exists()


def test_stale_state_different_task(tmp_path):
    """Разные задачи → сбрасывается."""
    _write_cp(tmp_path, "добавь кнопку csv")
    assert _reset_stale_state(str(tmp_path), "добавь кнопку json") is True
    assert not _checkpoint_path(str(tmp_path)).exists()
