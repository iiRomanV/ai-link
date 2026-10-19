"""Тесты для core.project_state."""

import json
from pathlib import Path

from core.project_state import (
    load_state, save_state, update_work, set_queue,
    add_known_issue, add_decision, record_agent_result,
    update_structure, get_summary, STATE_VERSION,
)


def test_load_state_creates_default(tmp_path):
    pd = str(tmp_path)
    state = load_state(pd)
    assert state["version"] == STATE_VERSION
    assert state["work"]["current_status"] == "idle"
    assert (Path(pd) / ".ai_link" / "state.json").exists()


def test_save_state_atomic(tmp_path):
    pd = str(tmp_path)
    state = load_state(pd)
    state["work"]["current_task"] = "test"
    save_state(pd, state)
    tmp_file = Path(pd) / ".ai_link" / "state.tmp"
    assert not tmp_file.exists()
    state2 = load_state(pd)
    assert state2["work"]["current_task"] == "test"


def test_update_work(tmp_path):
    pd = str(tmp_path)
    load_state(pd)
    update_work(pd, current_task="Делать X", current_status="in_progress")
    state = load_state(pd)
    assert state["work"]["current_task"] == "Делать X"
    assert state["work"]["current_status"] == "in_progress"


def test_set_queue(tmp_path):
    pd = str(tmp_path)
    load_state(pd)
    set_queue(pd, ["task1", "task2"])
    state = load_state(pd)
    assert state["work"]["queue"] == ["task1", "task2"]


def test_add_known_issue(tmp_path):
    pd = str(tmp_path)
    load_state(pd)
    add_known_issue(pd, "баг в storage")
    add_known_issue(pd, "медленный поиск")
    state = load_state(pd)
    assert len(state["work"]["known_issues"]) == 2
    assert "баг в storage" in state["work"]["known_issues"]


def test_add_decision(tmp_path):
    pd = str(tmp_path)
    load_state(pd)
    add_decision(pd, "Выбрали SQLite", "проще, чем Postgres")
    state = load_state(pd)
    assert len(state["decisions"]) == 1
    d = state["decisions"][0]
    assert d["what"] == "Выбрали SQLite"
    assert d["why"] == "проще, чем Postgres"
    assert "date" in d


def test_record_agent_result_success(tmp_path):
    pd = str(tmp_path)
    load_state(pd)
    record_agent_result(pd, "deepseek", True, 30.0)
    record_agent_result(pd, "deepseek", True, 50.0)
    state = load_state(pd)
    s = state["agent_stats"]["deepseek"]
    assert s["tasks_done"] == 2
    assert s["tasks_failed"] == 0
    assert s["total_seconds"] == 80.0
    assert s["avg_seconds"] == 40.0


def test_record_agent_result_mixed(tmp_path):
    pd = str(tmp_path)
    load_state(pd)
    record_agent_result(pd, "qwen", True, 100.0)
    record_agent_result(pd, "qwen", False, 20.0)
    state = load_state(pd)
    s = state["agent_stats"]["qwen"]
    assert s["tasks_done"] == 1
    assert s["tasks_failed"] == 1
    assert s["avg_seconds"] == 60.0


def test_update_structure(tmp_path):
    pd = str(tmp_path)
    load_state(pd)
    files = [{"path": "core/a.py", "purpose": "test",
              "size_bytes": 100, "hash": "abc",
              "last_modified": "2026-09-13T10:00:00", "status": "done"}]
    symbols = {"core/a.py": {"classes": [], "functions": ["foo"],
                              "imports": ["os"]}}
    deps = {"core/a.py": []}
    update_structure(pd, files, symbols, deps)
    state = load_state(pd)
    assert len(state["structure"]["files"]) == 1
    assert state["structure"]["symbols"]["core/a.py"]["functions"] == ["foo"]


def test_get_summary(tmp_path):
    pd = str(tmp_path)
    load_state(pd)
    update_work(pd, current_task="X", current_status="in_progress")
    set_queue(pd, ["t1", "t2"])
    add_decision(pd, "a", "b")
    record_agent_result(pd, "deepseek", True, 10.0)
    summary = get_summary(pd)
    assert summary["current_task"] == "X"
    assert summary["current_status"] == "in_progress"
    assert summary["queue"] == ["t1", "t2"]
    assert summary["decisions_count"] == 1
    assert "deepseek" in summary["agents"]


def test_state_broken_json_recovery(tmp_path):
    pd = str(tmp_path)
    load_state(pd)
    path = Path(pd) / ".ai_link" / "state.json"
    path.write_text("невалидный json", encoding="utf-8")
    state2 = load_state(pd)
    assert state2["version"] == STATE_VERSION


def test_log_decision_writes_jsonl(tmp_path):
    from core.project_state import log_decision, load_decisions
    log_decision(str(tmp_path), "Решили X", "Потому что Y")
    log_decision(str(tmp_path), "Решили Z", "Потому что W", context="модуль A")
    result = load_decisions(str(tmp_path))
    assert len(result) == 2
    assert result[0]["what"] == "Решили X"
    assert result[1]["context"] == "модуль A"


def test_load_decisions_limit(tmp_path):
    from core.project_state import log_decision, load_decisions
    for i in range(5):
        log_decision(str(tmp_path), f"Решение {i}", "причина")
    result = load_decisions(str(tmp_path), limit=3)
    assert len(result) == 3
    assert result[0]["what"] == "Решение 2"
    assert result[-1]["what"] == "Решение 4"


def test_load_decisions_empty(tmp_path):
    from core.project_state import load_decisions
    result = load_decisions(str(tmp_path))
    assert result == []


def test_add_decision_writes_both(tmp_path):
    from core.project_state import add_decision, load_decisions, load_state
    add_decision(str(tmp_path), "Выбрали SQLite", "проще")
    state = load_state(str(tmp_path))
    assert len(state["decisions"]) == 1
    assert state["decisions"][0]["what"] == "Выбрали SQLite"
    log = load_decisions(str(tmp_path))
    assert len(log) == 1
    assert log[0]["what"] == "Выбрали SQLite"


def test_build_context_empty(tmp_path):
    from core.project_state import build_context
    ctx = build_context(str(tmp_path))
    assert "=== ПРОЕКТ ===" in ctx
    assert "(не задана)" in ctx


def test_build_context_full(tmp_path):
    from core.project_state import (
        build_context, load_state, update_work, set_queue,
        add_known_issue, add_decision, update_structure,
    )
    load_state(str(tmp_path))
    update_work(str(tmp_path), current_task="Писать тесты",
                current_status="in_progress")
    set_queue(str(tmp_path), ["Задача 1", "Задача 2"])
    add_known_issue(str(tmp_path), "баг в storage")
    add_decision(str(tmp_path), "Выбрали SQLite", "проще")
    update_structure(str(tmp_path),
                    files=[{"path": "main.py", "purpose": "",
                            "size_bytes": 100, "hash": "x",
                            "last_modified": "", "status": "done"}],
                    symbols={}, dependencies={})
    ctx = build_context(str(tmp_path))
    assert "Писать тесты" in ctx
    assert "in_progress" in ctx
    assert "баг в storage" in ctx
    assert "Задача 1" in ctx
    assert "Выбрали SQLite" in ctx
    assert "main.py" in ctx


def test_build_context_budget(tmp_path):
    from core.project_state import (
        build_context, load_state, set_queue,
    )
    load_state(str(tmp_path))
    set_queue(str(tmp_path), [f"Очень длинная задача номер {i}" for i in range(100)])
    ctx = build_context(str(tmp_path), budget_chars=300)
    assert len(ctx) <= 300
    assert "обрезано" in ctx


def test_build_context_no_decisions(tmp_path):
    from core.project_state import build_context, load_state
    load_state(str(tmp_path))
    ctx = build_context(str(tmp_path))
    assert "ПОСЛЕДНИЕ РЕШЕНИЯ" not in ctx
