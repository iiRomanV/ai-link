"""B+ (19.10.2026): acceptance gate в brain-пути code → review.

Проблема (прогон 19.10): brain перехватывал code → review
(`_dec == "run_review"` → `current_phase = PHASE_REVIEW; continue`).
Наша проверка `_compute_research_p0_status` была ПОСЛЕ brain-блока
→ не доходила. `research_notes.json` оставался `pending`.

Фикс: проверка P0 ВСТАВЛЕНА внутрь brain-блока, перед
`current_phase = PHASE_REVIEW`.
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


def _write_notes(project_dir, targets):
    d = Path(project_dir) / ".ai_link"
    d.mkdir(parents=True, exist_ok=True)
    (d / "research_notes.json").write_text(
        json.dumps({"version": 1, "targets": targets}, ensure_ascii=False),
        encoding="utf-8",
    )


def _write_file(project_dir, rel, text):
    p = Path(project_dir) / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def test_brain_code_to_fix_when_p0_not_fulfilled(tmp_path):
    """brain_switch=True, P0 не выполнен → code → fix, не review."""
    calls = []

    _write_file(tmp_path, "config.py", 'ADMIN_PASSWORD = "123"\n')
    _p0_json = json.dumps({
        "version": 1,
        "targets": [{
            "id": "P0-1",
            "severity": "P0",
            "acceptance": [{
                "type": "grep_absent",
                "pattern": r"ADMIN_PASSWORD",
                "glob": "**/*.py",
            }],
        }],
    }, ensure_ascii=False)

    def fake_run_phase(ctx, phase, agent_override=""):
        calls.append(phase)
        if phase == orch.PHASE_PLAN:
            return _make_result(phase, "success", output=(
                "НАЗВАНИЕ: test\nЦЕЛЬ: test\nЭТАП: A\n- a1\n"
            ))
        if phase == orch.PHASE_RESEARCH:
            # Возвращаем ТОТ ЖЕ JSON с P0, иначе
            # _save_research_notes перезапишет пустым.
            return _make_result(phase, "success", output=_p0_json)
        if phase == orch.PHASE_CODE:
            return _make_result(phase, "success")
        if phase == orch.PHASE_REVIEW:
            return _make_result(phase, "success", output="STATUS: OK")
        if phase == orch.PHASE_FIX:
            return _make_result(phase, "success")
        return _make_result(phase, "success")

    def fake_brain(ctx, phase, phase_result, phase_status="",
                   extra_state=None):
        return {"decision": "run_review", "mode": "rule",
                "confidence": 0.9, "reason": "test"}

    with patch("core.orchestrator.run_phase", side_effect=fake_run_phase), \
         patch("core.orchestrator._brain_decide_for_transition",
               side_effect=fake_brain):
        orch.orchestrate(
            "Задача", str(tmp_path),
            cross_verify=False, brain_switch=True,
        )

    assert orch.PHASE_FIX in calls, (
        f"fix не запустился при not_fulfilled. calls={calls}"
    )


def test_brain_code_to_review_when_p0_fulfilled(tmp_path):
    """brain_switch=True, P0 выполнен → code → review."""
    calls = []

    _write_file(tmp_path, "config.py",
                "import os\nADMIN_PASSWORD = os.environ.get('PW')\n")
    _p0_json = json.dumps({
        "version": 1,
        "targets": [{
            "id": "P0-1",
            "severity": "P0",
            "acceptance": [{
                "type": "grep_absent",
                "pattern": r'ADMIN_PASSWORD\s*=\s*["\']\d',
                "glob": "**/*.py",
            }],
        }],
    }, ensure_ascii=False)

    def fake_run_phase(ctx, phase, agent_override=""):
        calls.append(phase)
        if phase == orch.PHASE_PLAN:
            return _make_result(phase, "success", output=(
                "НАЗВАНИЕ: test\nЦЕЛЬ: test\nЭТАП: A\n- a1\n"
            ))
        if phase == orch.PHASE_RESEARCH:
            return _make_result(phase, "success", output=_p0_json)
        if phase == orch.PHASE_CODE:
            return _make_result(phase, "success")
        if phase == orch.PHASE_REVIEW:
            return _make_result(phase, "success", output="STATUS: OK")
        if phase == orch.PHASE_FIX:
            return _make_result(phase, "success")
        return _make_result(phase, "success")

    def fake_brain(ctx, phase, phase_result, phase_status="",
                   extra_state=None):
        return {"decision": "run_review", "mode": "rule",
                "confidence": 0.9, "reason": "test"}

    with patch("core.orchestrator.run_phase", side_effect=fake_run_phase), \
         patch("core.orchestrator._brain_decide_for_transition",
               side_effect=fake_brain):
        orch.orchestrate(
            "Задача", str(tmp_path),
            cross_verify=False, brain_switch=True,
        )

    assert orch.PHASE_REVIEW in calls, (
        f"review не запустился при fulfilled. calls={calls}"
    )
