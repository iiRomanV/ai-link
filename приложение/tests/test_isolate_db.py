"""ISOLATE-DB (2026-10-02): тесты не трогают продакшн-БД."""
import os
from pathlib import Path


def test_get_db_path_reads_env(monkeypatch, tmp_path):
    """get_db_path возвращает путь из AI_LINK_DB_PATH."""
    test_db = tmp_path / "test.db"
    monkeypatch.setenv("AI_LINK_DB_PATH", str(test_db))
    # Перечитываем функцию, чтобы она увидела env
    from core.ai_link_db import get_db_path
    assert get_db_path() == test_db


def test_get_db_path_default_without_env(monkeypatch):
    """Без env — продакшн-путь."""
    monkeypatch.delenv("AI_LINK_DB_PATH", raising=False)
    from core.ai_link_db import get_db_path
    result = get_db_path()
    assert result == Path.home() / ".ai_link" / "ai_link.db"


def test_isolate_db_fixture_active():
    """Фикстура _isolate_db поставила AI_LINK_DB_PATH."""
    env_val = os.environ.get("AI_LINK_DB_PATH", "")
    assert env_val != "", "AI_LINK_DB_PATH не установлен фикстурой"
    assert "tmp" in env_val.lower() or "/tmp" in env_val
