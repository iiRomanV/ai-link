"""Тесты COST-LEDGER."""

import json
from pathlib import Path

from core import cost_ledger as cl
from core import ai_link_db as adb


def _db(tmp_path):
    return tmp_path / "ledger.db"


def _mk_project(tmp_path, events=None):
    p = tmp_path / "proj"
    (p / ".ai_link").mkdir(parents=True, exist_ok=True)
    if events:
        (p / ".ai_link" / "events.jsonl").write_text(
            "\n".join(json.dumps(e, ensure_ascii=False) for e in events)
            + "\n", encoding="utf-8")
    return p


def test_resource_class_browser():
    assert cl.resource_class_for("browser_qwen") == cl.RC_BROWSER_FREE
    assert cl.resource_class_for("browser_deepseek") == cl.RC_BROWSER_FREE


def test_resource_class_api():
    assert cl.resource_class_for("gigachat") == cl.RC_API_PAID
    assert cl.resource_class_for("yandex") == cl.RC_API_PAID


def test_resource_class_local():
    assert cl.resource_class_for("ollama") == cl.RC_LOCAL_FREE


def test_resource_class_unknown():
    assert cl.resource_class_for("mystery") == cl.RC_UNKNOWN
    assert cl.resource_class_for("") == cl.RC_UNKNOWN


def test_record_basic(tmp_path):
    path = _db(tmp_path)
    adb.init_schema(path)
    rid = cl.record(run_id="r1", model="browser_qwen", role="review",
                    duration_seconds=12.5, requests=1, success=True,
                    db_path=path)
    assert rid is not None
    s = cl.summary(run_id="r1", db_path=path)
    assert s["total_rows"] == 1
    assert s["total_seconds"] == 12.5
    assert s["by_model"][0]["model"] == "browser_qwen"


def test_record_auto_resource_class(tmp_path):
    path = _db(tmp_path)
    adb.init_schema(path)
    cl.record(run_id="r1", model="browser_deepseek",
              db_path=path)
    s = cl.summary(run_id="r1", db_path=path)
    assert s["by_model"][0]["resource_class"] == cl.RC_BROWSER_FREE


def test_record_fail(tmp_path):
    path = _db(tmp_path)
    adb.init_schema(path)
    cl.record(run_id="r1", model="browser_qwen", success=False,
              db_path=path)
    s = cl.summary(run_id="r1", db_path=path)
    assert s["total_fail"] == 1
    assert s["total_ok"] == 0


def test_ingest_from_events(tmp_path):
    path = _db(tmp_path)
    adb.init_schema(path)
    p = _mk_project(tmp_path, events=[
        {"ts": "2026-09-22T12:00:00", "run_id": "r1",
         "phase_id": "code", "event_type": "model_call",
         "payload_json": {"agent": "browser_deepseek",
                          "prompt_len": 100}},
        {"ts": "2026-09-22T12:00:05", "run_id": "r1",
         "phase_id": "code", "event_type": "model_response",
         "payload_json": {"agent": "browser_deepseek",
                          "success": True, "elapsed": 5.2,
                          "text_len": 500}},
    ])
    cl.ingest_from_events(str(p), db_path=path)
    s = cl.summary(run_id="r1", db_path=path)
    assert s["total_rows"] == 1
    assert s["total_seconds"] == 5.2


def test_ingest_idempotent(tmp_path):
    path = _db(tmp_path)
    adb.init_schema(path)
    p = _mk_project(tmp_path, events=[
        {"ts": "2026-09-22T12:00:00", "run_id": "r1",
         "phase_id": "code", "event_type": "model_call",
         "payload_json": {"agent": "browser_qwen"}},
        {"ts": "2026-09-22T12:00:02", "run_id": "r1",
         "phase_id": "code", "event_type": "model_response",
         "payload_json": {"agent": "browser_qwen", "success": True,
                          "elapsed": 2.0}},
    ])
    cl.ingest_from_events(str(p), db_path=path)
    cl.ingest_from_events(str(p), db_path=path)
    s = cl.summary(run_id="r1", db_path=path)
    assert s["total_rows"] == 1


def test_ingest_empty(tmp_path):
    path = _db(tmp_path)
    adb.init_schema(path)
    p = _mk_project(tmp_path)
    r = cl.ingest_from_events(str(p), db_path=path)
    assert r["added"] == 0


def test_summary_for_llm_empty(tmp_path):
    path = _db(tmp_path)
    adb.init_schema(path)
    s = cl.summary_for_llm(db_path=path)
    assert "пусто" in s


def test_summary_for_llm_with_data(tmp_path):
    path = _db(tmp_path)
    adb.init_schema(path)
    cl.record(run_id="r1", model="browser_qwen", role="review",
              duration_seconds=3.5, success=True, db_path=path)
    s = cl.summary_for_llm(db_path=path)
    assert "COST-LEDGER" in s
    assert "browser_qwen" in s


def test_reset(tmp_path):
    path = _db(tmp_path)
    adb.init_schema(path)
    cl.record(run_id="r1", model="browser_qwen", db_path=path)
    cl.reset_ledger(path)
    s = cl.summary(run_id="r1", db_path=path)
    assert s["total_rows"] == 0
