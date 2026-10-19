"""AI-RESPONSE-LOG (2026-10-02): сохранение пары «промпт + ответ»."""
import json
import os
from pathlib import Path

import pytest


def test_save_ai_pair_creates_file(tmp_path, monkeypatch):
    """_save_ai_pair создаёт JSONL с полным промптом и ответом."""
    from config import config_manager
    monkeypatch.setattr(
        config_manager, "get_setting",
        lambda k, d=None: True if k == "debug_logs" else d,
    )
    # Подменяем путь к .diagnostics
    import core.orchestrator as orch
    fake_root = tmp_path / "app"
    fake_diag = fake_root / ".diagnostics" / "ai_responses"
    # Патчим __file__ модуля
    monkeypatch.setattr(orch, "__file__", str(fake_root / "core" / "orchestrator.py"))

    orch._save_ai_pair(
        phase="review",
        agent="browser_qwen",
        task="Тест",
        prompt="Проверь этот код...",
        response="STATUS: PROBLEMS\nCRITICAL: ...",
        success=True,
        elapsed=12.3,
        error="",
    )
    assert fake_diag.exists()
    files = list(fake_diag.glob("*.jsonl"))
    assert len(files) == 1
    data = json.loads(files[0].read_text(encoding="utf-8"))
    assert data["phase"] == "review"
    assert data["agent"] == "browser_qwen"
    assert data["prompt"] == "Проверь этот код..."
    assert data["response"] == "STATUS: PROBLEMS\nCRITICAL: ..."
    assert data["success"] is True
    assert data["prompt_len"] > 0
    assert data["response_len"] > 0


def test_save_ai_pair_skips_without_debug(tmp_path, monkeypatch):
    """Без debug_logs=True — файл не создаётся."""
    from config import config_manager
    monkeypatch.setattr(
        config_manager, "get_setting",
        lambda k, d=None: False if k == "debug_logs" else d,
    )
    import core.orchestrator as orch
    fake_root = tmp_path / "app"
    fake_diag = fake_root / ".diagnostics" / "ai_responses"
    monkeypatch.setattr(orch, "__file__", str(fake_root / "core" / "orchestrator.py"))

    orch._save_ai_pair(
        phase="review", agent="browser_qwen", task="",
        prompt="x", response="y", success=False, elapsed=0.0,
    )
    assert not fake_diag.exists() or len(list(fake_diag.glob("*.jsonl"))) == 0


def test_save_ai_pair_error_recorded(tmp_path, monkeypatch):
    """Ошибка сохраняется в error-поле."""
    from config import config_manager
    monkeypatch.setattr(
        config_manager, "get_setting",
        lambda k, d=None: True if k == "debug_logs" else d,
    )
    import core.orchestrator as orch
    fake_root = tmp_path / "app"
    monkeypatch.setattr(orch, "__file__", str(fake_root / "core" / "orchestrator.py"))

    orch._save_ai_pair(
        phase="review", agent="browser_qwen", task="",
        prompt="x", response="", success=False, elapsed=390.0,
        error="Превышено время ожидания",
    )
    files = list((fake_root / ".diagnostics" / "ai_responses").glob("*.jsonl"))
    assert len(files) == 1
    data = json.loads(files[0].read_text(encoding="utf-8"))
    assert data["success"] is False
    assert "Превышено время" in data["error"]
