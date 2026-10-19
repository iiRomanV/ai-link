"""B (18.10.2026): research-триггер в brain-пути.

Проблема (найдена в прогоне 18.10): run_task.py передаёт
brain_switch=True. Все прогоны через CLI идут через brain-путь.
Наш research-триггер был вставлен ТОЛЬКО в «старую логику» —
поэтому вживую research не запускался.

Проверяем:
  1. brain_switch=True, brain → run_code → research запускается.
  2. research был → brain снова → run_code → сразу code,
     research НЕ повторяется.
  3. research успешно → research_notes.json создан.
"""
import json
from pathlib import Path
from unittest.mock import patch

from core import orchestrator as orch


def _make_result(phase, status, output=""):
    r = orch.PhaseResult(
        phase=phase, agent="fake",
        status=status, started_at="now", finished_at="now",
    )
    r.output = output
    return r


def test_brain_path_runs_research_before_code(tmp_path):
    """brain_switch=True → plan success → brain run_code →
    research запускается (один раз), потом code."""
    calls = []

    def fake_run_phase(ctx, phase, agent_override=""):
        calls.append(phase)
        if phase == orch.PHASE_PLAN:
            return _make_result(phase, "success", output=(
                "НАЗВАНИЕ: test\nЦЕЛЬ: test\n"
                "ЭТАП: A\n- a1\n"
            ))
        if phase == orch.PHASE_RESEARCH:
            return _make_result(
                phase, "success",
                output='{"targets": []}',
            )
        if phase == orch.PHASE_CODE:
            return _make_result(phase, "success")
        if phase == orch.PHASE_REVIEW:
            return _make_result(phase, "success", output="STATUS: OK")
        return _make_result(phase, "success")

    # brain всегда говорит run_code после plan
    def fake_brain(ctx, phase, phase_result, phase_status="",
                   extra_state=None):
        return {"decision": "run_code", "mode": "rule",
                "confidence": 0.9, "reason": "test"}

    with patch("core.orchestrator.run_phase", side_effect=fake_run_phase), \
         patch("core.orchestrator._brain_decide_for_transition",
               side_effect=fake_brain):
        r = orch.orchestrate(
            "Задача", str(tmp_path),
            cross_verify=False, brain_switch=True,
        )

    assert orch.PHASE_RESEARCH in calls, (
        f"research не запустился в brain-пути. calls={calls}"
    )
    assert calls.count(orch.PHASE_RESEARCH) == 1, (
        f"research запустился более одного раза: {calls.count(orch.PHASE_RESEARCH)}"
    )
    # research должен быть ДО первого code
    idx_r = calls.index(orch.PHASE_RESEARCH)
    idx_c = calls.index(orch.PHASE_CODE)
    assert idx_r < idx_c, (
        f"research после code: research={idx_r}, code={idx_c}"
    )


def test_brain_path_does_not_repeat_research(tmp_path):
    """Если research уже был — brain → run_code сразу идёт в code."""
    calls = []

    def fake_run_phase(ctx, phase, agent_override=""):
        calls.append(phase)
        if phase == orch.PHASE_PLAN:
            return _make_result(phase, "success", output=(
                "НАЗВАНИЕ: test\nЦЕЛЬ: test\nЭТАП: A\n- a1\n"
            ))
        if phase == orch.PHASE_RESEARCH:
            return _make_result(phase, "success",
                                output='{"targets": []}')
        if phase == orch.PHASE_CODE:
            return _make_result(phase, "success")
        if phase == orch.PHASE_REVIEW:
            return _make_result(phase, "success", output="STATUS: OK")
        return _make_result(phase, "success")

    def fake_brain(ctx, phase, phase_result, phase_status="",
                   extra_state=None):
        return {"decision": "run_code", "mode": "rule",
                "confidence": 0.9, "reason": "test"}

    with patch("core.orchestrator.run_phase", side_effect=fake_run_phase), \
         patch("core.orchestrator._brain_decide_for_transition",
               side_effect=fake_brain):
        orch.orchestrate(
            "Задача", str(tmp_path),
            cross_verify=False, brain_switch=True,
        )

    assert calls.count(orch.PHASE_RESEARCH) == 1, (
        f"research запустился {calls.count(orch.PHASE_RESEARCH)} раз, "
        f"ожидался 1: {calls}"
    )


def test_brain_path_research_saves_notes(tmp_path):
    """Research success → research_notes.json создан."""
    def fake_run_phase(ctx, phase, agent_override=""):
        if phase == orch.PHASE_PLAN:
            return _make_result(phase, "success", output=(
                "НАЗВАНИЕ: test\nЦЕЛЬ: test\nЭТАП: A\n- a1\n"
            ))
        if phase == orch.PHASE_RESEARCH:
            return _make_result(phase, "success", output=json.dumps({
                "targets": [{
                    "id": "P0-1", "file": "config.py",
                    "pattern": r"ADMIN_PASSWORD\s*=",
                    "check_kind": "not_contains",
                    "action": "убрать пароль",
                }],
            }))
        if phase == orch.PHASE_CODE:
            return _make_result(phase, "success")
        if phase == orch.PHASE_REVIEW:
            return _make_result(phase, "success", output="STATUS: OK")
        return _make_result(phase, "success")

    def fake_brain(ctx, phase, phase_result, phase_status="",
                   extra_state=None):
        return {"decision": "run_code", "mode": "rule",
                "confidence": 0.9, "reason": "test"}

    with patch("core.orchestrator.run_phase", side_effect=fake_run_phase), \
         patch("core.orchestrator._brain_decide_for_transition",
               side_effect=fake_brain):
        orch.orchestrate(
            "Задача", str(tmp_path),
            cross_verify=False, brain_switch=True,
        )

    path = tmp_path / ".ai_link" / "research_notes.json"
    assert path.exists(), "research_notes.json не создан"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert len(data["targets"]) == 1
    assert data["targets"][0]["id"] == "P0-1"
