"""Тесты Capability Profile — с моками агентов, без браузеров."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from core import capability_benchmark as cb


# === _check_answer ===

def test_check_answer_match():
    t = {"regex": r"привет[\s,]+мир"}
    assert cb._check_answer("Привет, Мир", t) is True


def test_check_answer_no_match():
    t = {"regex": r"привет"}
    assert cb._check_answer("Здравствуй", t) is False


def test_check_answer_empty_text():
    t = {"regex": r".*"}
    assert cb._check_answer("", t) is False


def test_check_answer_with_dotall():
    import re as _re
    t = {"regex": r"1\..*2\..*3\.", "regex_flags": _re.DOTALL}
    assert cb._check_answer("1. a\n2. b\n3. c", t) is True


def test_check_answer_broken_regex():
    t = {"regex": r"[unclosed"}
    assert cb._check_answer("test", t) is False


# === _aggregate ===

def test_aggregate_empty():
    """Пустой список — всё по нулям, но структура корректная."""
    agg = cb._aggregate([])
    assert "summary" in agg
    assert "tested_at" in agg
    for cat in cb.CATEGORIES:
        s = agg["summary"][cat]
        assert s["passed"] == 0
        assert s["failed"] == 0
        assert s["pass_rate"] == 0.0
        assert s["avg_sec"] == 0.0


def test_aggregate_counts():
    results = [
        {"task_id": "b1", "category": "basic", "passed": True,
         "seconds": 1.0, "raw": "ok", "error": ""},
        {"task_id": "b2", "category": "basic", "passed": False,
         "seconds": 3.0, "raw": "", "error": "timeout"},
        {"task_id": "c1", "category": "coding", "passed": True,
         "seconds": 5.0, "raw": "def", "error": ""},
    ]
    agg = cb._aggregate(results)
    basic = agg["summary"]["basic"]
    assert basic["passed"] == 1
    assert basic["failed"] == 1
    assert basic["pass_rate"] == 0.5
    assert basic["avg_sec"] == 2.0
    coding = agg["summary"]["coding"]
    assert coding["passed"] == 1
    assert coding["pass_rate"] == 1.0


def test_aggregate_raw_saved():
    results = [
        {"task_id": "b1", "category": "basic", "passed": True,
         "seconds": 1.0, "raw": "Привет, Мир", "error": ""},
    ]
    agg = cb._aggregate(results)
    raw_list = agg["summary"]["basic"]["raw"]
    assert len(raw_list) == 1
    assert raw_list[0]["task_id"] == "b1"
    assert "Привет" in raw_list[0]["raw"]


def test_aggregate_raw_truncated_to_800():
    long_text = "A" * 3000
    results = [
        {"task_id": "b1", "category": "basic", "passed": True,
         "seconds": 1.0, "raw": long_text, "error": ""},
    ]
    agg = cb._aggregate(results)
    raw_list = agg["summary"]["basic"]["raw"]
    assert len(raw_list[0]["raw"]) == 800


# === _run_one (мок _call_agent) ===

def test_run_one_success():
    task = {"id": "b1", "category": "basic",
            "prompt": "Скажи привет",
            "regex": r"привет"}
    with patch("core.agentic_loop._call_agent",
               return_value={"success": True, "text": "Привет, Мир"}):
        r = cb._run_one("browser_qwen", task)
    assert r["passed"] is True
    assert r["task_id"] == "b1"
    assert r["category"] == "basic"
    assert r["error"] == ""
    assert "Привет" in r["raw"]


def test_run_one_failure_no_match():
    task = {"id": "b2", "category": "basic",
            "prompt": "Сколько 2+2?",
            "regex": r"\b4\b"}
    with patch("core.agentic_loop._call_agent",
               return_value={"success": True, "text": "Пять"}):
        r = cb._run_one("browser_qwen", task)
    assert r["passed"] is False
    assert r["error"] == ""


def test_run_one_agent_error():
    task = {"id": "b1", "category": "basic",
            "prompt": "x", "regex": r".*"}
    with patch("core.agentic_loop._call_agent",
               return_value={"success": False, "error": "login_required"}):
        r = cb._run_one("browser_qwen", task)
    assert r["passed"] is False
    assert "login_required" in r["error"]


def test_run_one_exception():
    task = {"id": "b1", "category": "basic",
            "prompt": "x", "regex": r".*"}
    with patch("core.agentic_loop._call_agent",
               side_effect=RuntimeError("boom")):
        r = cb._run_one("browser_qwen", task)
    assert r["passed"] is False
    assert "boom" in r["error"]


# === run_benchmark ===

def test_run_benchmark_mocked():
    """Прогон на одном агенте — _call_agent всегда отвечает правильно."""
    with patch("core.agentic_loop._call_agent",
               return_value={"success": True, "text": "Привет, Мир 4 1. 2. 3. ШАГ 1 ШАГ 2 ШАГ 3 ШАГ 4 A да def sum3 a + b + c [::-1] ноль 10"}):
        data = cb.run_benchmark(agent_ids=["browser_qwen"])
    assert "started_at" in data
    assert "finished_at" in data
    assert "browser_qwen" in data["results"]
    summary = data["results"]["browser_qwen"]["summary"]
    assert "basic" in summary
    assert "coding" in summary


def test_run_benchmark_progress_callback():
    calls = []
    def on_prog(done, total, r):
        calls.append((done, total, r["task_id"]))
    with patch("core.agentic_loop._call_agent",
               return_value={"success": True, "text": "4"}):
        cb.run_benchmark(agent_ids=["browser_qwen"], on_progress=on_prog)
    # Число задач теперь берём из самого бенчмарка —
    # чтобы тест не ломался при переписывании набора задач.
    from core.capability_benchmark import BENCHMARK_TASKS
    n = len(BENCHMARK_TASKS)
    assert len(calls) == n
    assert calls[0][0] == 1
    assert calls[-1][0] == n
    assert calls[-1][1] == n


# === save_results / load_results ===

def test_save_and_load_results(tmp_path, monkeypatch):
    fake_file = tmp_path / "capabilities.json"
    monkeypatch.setattr(cb, "CAPABILITIES_FILE", fake_file)

    data = {
        "started_at": "2026-09-15T11:00:00",
        "finished_at": "2026-09-15T11:15:00",
        "results": {
            "browser_qwen": {
                "summary": {
                    "basic": {"passed": 2, "failed": 0,
                              "pass_rate": 1.0, "avg_sec": 1.5, "raw": []},
                },
                "tested_at": "2026-09-15T11:15:00",
            }
        }
    }
    cb.save_results(data)

    loaded = cb.load_results()
    assert loaded is not None
    assert "browser_qwen" in loaded["results"]
    assert loaded["results"]["browser_qwen"]["summary"]["basic"]["pass_rate"] == 1.0


def test_save_merges_existing(tmp_path, monkeypatch):
    fake_file = tmp_path / "capabilities.json"
    monkeypatch.setattr(cb, "CAPABILITIES_FILE", fake_file)

    # Первый прогон — qwen
    cb.save_results({
        "finished_at": "2026-09-15T11:00:00",
        "results": {"browser_qwen": {"summary": {}, "tested_at": "t1"}},
    })
    # Второй прогон — deepseek
    cb.save_results({
        "finished_at": "2026-09-15T11:30:00",
        "results": {"browser_deepseek": {"summary": {}, "tested_at": "t2"}},
    })

    loaded = cb.load_results()
    assert "browser_qwen" in loaded["results"]
    assert "browser_deepseek" in loaded["results"]


def test_load_results_missing(tmp_path, monkeypatch):
    fake_file = tmp_path / "no_such.json"
    monkeypatch.setattr(cb, "CAPABILITIES_FILE", fake_file)
    assert cb.load_results() is None


def test_load_results_broken(tmp_path, monkeypatch):
    fake_file = tmp_path / "broken.json"
    fake_file.write_text("not json {", encoding="utf-8")
    monkeypatch.setattr(cb, "CAPABILITIES_FILE", fake_file)
    assert cb.load_results() is None


# === get_agent_pass_rate ===

def test_get_pass_rate(tmp_path, monkeypatch):
    fake_file = tmp_path / "cap.json"
    fake_file.write_text(json.dumps({
        "results": {
            "browser_qwen": {
                "summary": {
                    "coding": {"pass_rate": 0.75},
                }
            }
        }
    }), encoding="utf-8")
    monkeypatch.setattr(cb, "CAPABILITIES_FILE", fake_file)

    assert cb.get_agent_pass_rate("browser_qwen", "coding") == 0.75
    assert cb.get_agent_pass_rate("browser_qwen", "missing") is None
    assert cb.get_agent_pass_rate("nobody", "coding") is None


def test_get_pass_rate_no_file(tmp_path, monkeypatch):
    monkeypatch.setattr(cb, "CAPABILITIES_FILE", tmp_path / "no.json")
    assert cb.get_agent_pass_rate("browser_qwen", "coding") is None


# === get_best_agent_for_category ===

def test_best_agent_picks_highest(tmp_path, monkeypatch):
    fake_file = tmp_path / "cap.json"
    fake_file.write_text(json.dumps({
        "results": {
            "browser_qwen": {"summary": {"coding": {"pass_rate": 0.5}}},
            "browser_deepseek": {"summary": {"coding": {"pass_rate": 0.9}}},
        }
    }), encoding="utf-8")
    monkeypatch.setattr(cb, "CAPABILITIES_FILE", fake_file)

    best = cb.get_best_agent_for_category(
        "coding", candidates=["browser_qwen", "browser_deepseek"]
    )
    assert best == "browser_deepseek"


def test_best_agent_no_data(tmp_path, monkeypatch):
    monkeypatch.setattr(cb, "CAPABILITIES_FILE", tmp_path / "no.json")
    best = cb.get_best_agent_for_category(
        "coding", candidates=["browser_qwen", "browser_deepseek"]
    )
    assert best is None


# === _get_enabled_browser_agents ===

def test_get_enabled_browser_agents_from_file(tmp_path, monkeypatch):
    fake_agents = tmp_path / "agents.json"
    fake_agents.write_text(json.dumps([
        {"id": "gigachat", "enabled": True},
        {"id": "browser_qwen", "enabled": True},
        {"id": "browser_deepseek", "enabled": True},
        {"id": "browser_perplexity", "enabled": True},
        {"id": "browser_disabled", "enabled": False},
    ]), encoding="utf-8")

    fake_home = tmp_path
    (fake_home / ".ai_link").mkdir()
    (fake_home / ".ai_link" / "agents.json").write_text(
        fake_agents.read_text(encoding="utf-8"), encoding="utf-8"
    )

    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))

    result = cb._get_enabled_browser_agents()
    assert "browser_qwen" in result
    assert "browser_deepseek" in result
    # perplexity исключён
    assert "browser_perplexity" not in result
    # отключённый — не попал
    assert "browser_disabled" not in result
    # не-браузерный — не попал
    assert "gigachat" not in result
