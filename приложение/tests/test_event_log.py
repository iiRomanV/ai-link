"""Тесты EVENT-LOG (durable event log)."""

import json
from pathlib import Path

from core import event_log as el


def test_log_event_creates_file(tmp_path):
    el.reset_log(str(tmp_path))
    el.log_event(str(tmp_path), "run_started", {"task": "demo"})
    path = Path(tmp_path) / ".ai_link" / "events.jsonl"
    assert path.exists()
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["event_type"] == "run_started"
    assert rec["payload_json"] == {"task": "demo"}
    assert "ts" in rec


def test_log_event_append(tmp_path):
    el.reset_log(str(tmp_path))
    el.log_event(str(tmp_path), "a")
    el.log_event(str(tmp_path), "b")
    el.log_event(str(tmp_path), "c")
    events = el.load_events(str(tmp_path))
    assert [e["event_type"] for e in events] == ["a", "b", "c"]


def test_context_passed_to_event(tmp_path):
    el.reset_log(str(tmp_path))
    el.set_run_context(run_id="run-42", project_dir=str(tmp_path),
                       phase_id="code", iteration=3)
    el.log_event("", "model_call", {"agent": "deepseek"})
    events = el.load_events(str(tmp_path))
    assert len(events) == 1
    e = events[0]
    assert e["run_id"] == "run-42"
    assert e["phase_id"] == "code"
    assert e["iteration"] == 3
    el.clear_context()


def test_update_context_phase(tmp_path):
    el.reset_log(str(tmp_path))
    el.set_run_context(run_id="r", project_dir=str(tmp_path),
                       phase_id="plan", iteration=0)
    el.update_context(phase_id="review", iteration=7)
    el.log_event("", "phase_started")
    e = el.load_events(str(tmp_path))[-1]
    assert e["phase_id"] == "review"
    assert e["iteration"] == 7
    el.clear_context()


def test_load_events_limit(tmp_path):
    el.reset_log(str(tmp_path))
    for i in range(10):
        el.log_event(str(tmp_path), f"e{i}")
    events = el.load_events(str(tmp_path), limit=3)
    assert [e["event_type"] for e in events] == ["e7", "e8", "e9"]


def test_count_events(tmp_path):
    el.reset_log(str(tmp_path))
    el.log_event(str(tmp_path), "phase_started")
    el.log_event(str(tmp_path), "phase_completed")
    el.log_event(str(tmp_path), "phase_started")
    assert el.count_events(str(tmp_path)) == 3
    assert el.count_events(str(tmp_path), "phase_started") == 2
    assert el.count_events(str(tmp_path), "unknown") == 0
