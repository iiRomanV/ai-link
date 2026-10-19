"""Тесты для core.task_history."""

import json
from pathlib import Path
from unittest.mock import patch

from core import task_history
from core.task_history import (
    log_task, load_history, find_similar_tasks,
    get_success_rate, get_agent_stats,
)


def _patch_history(tmp_path):
    """Патч пути к файлу истории."""
    return patch.object(task_history, "HISTORY_FILE",
                        tmp_path / "task_history.jsonl")


def test_load_empty(tmp_path):
    with _patch_history(tmp_path):
        assert load_history() == []


def test_log_and_load(tmp_path):
    with _patch_history(tmp_path):
        log_task("Задача 1", "gigachat", True, files_created=2, duration=5.5)
        log_task("Задача 2", "ollama", False, notes="ошибка")
        entries = load_history()
        assert len(entries) == 2
        assert entries[0]["task"] == "Задача 1"
        assert entries[0]["success"] is True
        assert entries[0]["files_created"] == 2
        assert entries[1]["agent"] == "ollama"
        assert entries[1]["notes"] == "ошибка"


def test_load_limit(tmp_path):
    with _patch_history(tmp_path):
        for i in range(10):
            log_task(f"Задача {i}", "test", True)
        entries = load_history(limit=3)
        assert len(entries) == 3
        assert entries[0]["task"] == "Задача 7"
        assert entries[-1]["task"] == "Задача 9"


def test_load_broken_lines_skipped(tmp_path):
    path = tmp_path / "history.jsonl"
    path.write_text(
        '{"task": "ok", "agent": "x", "success": true}\n'
        'невалидный json\n'
        '{"task": "ok2", "agent": "y", "success": false}\n',
        encoding="utf-8",
    )
    with patch.object(task_history, "HISTORY_FILE", path):
        entries = load_history()
        assert len(entries) == 2


def test_load_empty_lines_skipped(tmp_path):
    path = tmp_path / "history.jsonl"
    path.write_text(
        '\n\n{"task": "x", "agent": "y", "success": true}\n\n',
        encoding="utf-8",
    )
    with patch.object(task_history, "HISTORY_FILE", path):
        assert len(load_history()) == 1


# === find_similar_tasks ===

def test_find_similar_empty_task(tmp_path):
    with _patch_history(tmp_path):
        log_task("создать приложение", "gigachat", True)
        assert find_similar_tasks("") == []


def test_find_similar_no_short_words(tmp_path):
    with _patch_history(tmp_path):
        log_task("создать приложение", "gigachat", True)
        # Все слова < 5 символов → пусто
        assert find_similar_tasks("abc de fg") == []


def test_find_similar_match(tmp_path):
    with _patch_history(tmp_path):
        log_task("создать приложение калькулятор", "gigachat", True)
        log_task("совсем другая задача про кота", "gigachat", True)
        result = find_similar_tasks("создать приложение парсер")
        assert len(result) == 1
        assert "калькулятор" in result[0]["task"]


def test_find_similar_limit(tmp_path):
    with _patch_history(tmp_path):
        for i in range(5):
            log_task(f"создать приложение {i}", "test", True)
        result = find_similar_tasks("создать приложение", limit=2)
        assert len(result) == 2


# === get_success_rate ===

def test_success_rate_empty(tmp_path):
    with _patch_history(tmp_path):
        assert get_success_rate("nonexistent") == 0.0


def test_success_rate_all_success(tmp_path):
    with _patch_history(tmp_path):
        log_task("t1", "agent_a", True)
        log_task("t2", "agent_a", True)
        assert get_success_rate("agent_a") == 1.0


def test_success_rate_mixed(tmp_path):
    with _patch_history(tmp_path):
        log_task("t1", "agent_a", True)
        log_task("t2", "agent_a", False)
        log_task("t3", "agent_a", True)
        log_task("t4", "agent_a", False)
        rate = get_success_rate("agent_a")
        assert rate == 0.5


def test_success_rate_other_agent(tmp_path):
    with _patch_history(tmp_path):
        log_task("t1", "agent_a", True)
        log_task("t2", "agent_b", False)
        assert get_success_rate("agent_a") == 1.0
        assert get_success_rate("agent_b") == 0.0


# === get_agent_stats ===

def test_agent_stats_empty(tmp_path):
    with _patch_history(tmp_path):
        stats = get_agent_stats("nonexistent")
        assert stats["total_tasks"] == 0
        assert stats["success_rate"] == 0.0
        assert stats["avg_duration"] == 0.0


def test_agent_stats_basic(tmp_path):
    with _patch_history(tmp_path):
        log_task("t1", "agent_a", True, duration=10.0)
        log_task("t2", "agent_a", False, duration=20.0)
        log_task("t3", "agent_a", True, duration=30.0)
        stats = get_agent_stats("agent_a")
        assert stats["total_tasks"] == 3
        assert stats["success_rate"] == 0.67
        assert stats["avg_duration"] == 20.0


def test_agent_stats_only_one_agent(tmp_path):
    with _patch_history(tmp_path):
        log_task("t1", "agent_a", True, duration=10.0)
        log_task("t2", "agent_b", False, duration=100.0)
        stats = get_agent_stats("agent_a")
        assert stats["total_tasks"] == 1
        assert stats["avg_duration"] == 10.0
