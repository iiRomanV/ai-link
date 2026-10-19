"""B (18.10.2026): триггер research-фазы перед первым code.

Проверяем:
  1. PHASE_RESEARCH входит в TEXT_PHASES (run_phase направит в
     run_text_phase, а не в run_file_phase).
  2. run_phase(ctx, PHASE_RESEARCH) вызывает run_text_phase.
  3. После успешного research _save_research_notes пишет файл
     (это делает orchestrate; здесь проверяем сам механизм
     через явный вызов _parse_research_json + _save_research_notes).
"""
import json
from pathlib import Path

import pytest

from core import orchestrator as orch


def test_research_in_text_phases():
    assert orch.PHASE_RESEARCH in orch.TEXT_PHASES
    assert orch.PHASE_RESEARCH not in orch.FILE_PHASES


def test_run_phase_research_calls_text(monkeypatch, tmp_path):
    """run_phase(PHASE_RESEARCH) → run_text_phase, не run_file_phase."""
    called = {"text": 0, "file": 0}

    def _fake_text(ctx, phase, agent_override=""):
        called["text"] += 1
        r = orch.PhaseResult(
            phase=phase, agent="fake",
            status="success", started_at="now", finished_at="now",
        )
        r.output = '{"targets": []}'
        return r

    def _fake_file(ctx, phase, agent_override=""):
        called["file"] += 1
        r = orch.PhaseResult(
            phase=phase, agent="fake",
            status="success", started_at="now", finished_at="now",
        )
        return r

    monkeypatch.setattr(orch, "run_text_phase", _fake_text)
    monkeypatch.setattr(orch, "run_file_phase", _fake_file)

    ctx = orch.OrchestrationContext(
        original_task="test",
        project_dir=str(tmp_path),
    )
    res = orch.run_phase(ctx, orch.PHASE_RESEARCH)
    assert called["text"] == 1, "research должен идти через run_text_phase"
    assert called["file"] == 0, "research НЕ должен идти через run_file_phase"
    assert res.status == "success"


def test_research_notes_saved_from_output(tmp_path):
    """Парсим output research-фазы и сохраняем research_notes.json."""
    raw = json.dumps({
        "targets": [
            {"id": "P0-1", "priority": "P0", "file": "config.py",
             "pattern": r"ADMIN_PASSWORD\s*=",
             "check_kind": "not_contains",
             "action": "убрать пароль"},
        ]
    })
    data = orch._parse_research_json(raw)
    orch._save_research_notes(str(tmp_path), data)

    path = tmp_path / ".ai_link" / "research_notes.json"
    assert path.exists()
    loaded = json.loads(path.read_text(encoding="utf-8"))
    assert len(loaded["targets"]) == 1
    assert loaded["targets"][0]["id"] == "P0-1"
    assert loaded["targets"][0]["file"] == "config.py"


def test_research_empty_targets_still_saves(tmp_path):
    """Если LLM вернула пустой targets — файл создаётся, но пустой.

    Это нормальный сценарий: новый проект без P0. code пойдёт
    без чеклиста, как раньше.
    """
    data = orch._parse_research_json('{"targets": []}')
    orch._save_research_notes(str(tmp_path), data)
    path = tmp_path / ".ai_link" / "research_notes.json"
    assert path.exists()
    loaded = json.loads(path.read_text(encoding="utf-8"))
    assert loaded["targets"] == []
