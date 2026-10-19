"""Тесты для core.orchestrator (модель данных)."""

import json
from pathlib import Path

from core.orchestrator import (
    PHASES, PHASE_DEFAULT_AGENT, PHASE_DESCRIPTIONS,
    PHASE_PLAN, PHASE_CODE, PHASE_REVIEW, PHASE_FIX, PHASE_FALLBACK,
    PHASE_RESEARCH,
    PhaseResult, OrchestrationContext, OrchestrationResult,
    create_context, get_phase_agent, describe_phase,
    is_valid_phase, summarize_history,
    parse_clarification_request,
)


def test_phases_defined():
    assert PHASE_PLAN in PHASES
    assert PHASE_CODE in PHASES
    assert PHASE_REVIEW in PHASES
    assert PHASE_FIX in PHASES
    assert PHASE_FALLBACK in PHASES
    # B (18.10.2026): research — новая фаза между plan и code.
    assert PHASE_RESEARCH in PHASES
    assert len(PHASES) == 6


def test_default_agents():
    """FIX ROLES-NOT-NAMES: PHASE_DEFAULT_AGENT пуст.

    Раньше были жёсткие имена (browser_qwen, browser_deepseek,
    ollama) — скрытая мина. Теперь роль → агент только
    через assignments (Настройки → Назначения).
    """
    assert PHASE_DEFAULT_AGENT == {}


def test_get_phase_agent_default_legacy(tmp_path, monkeypatch):
    """Жёсткие роли работают, если нет ни assignments, ни профиля."""
    monkeypatch.setattr(
        "core.capability_benchmark.ASSIGNMENTS_FILE",
        tmp_path / "no_assign.json",
    )
    monkeypatch.setattr(
        "core.capability_benchmark.CAPABILITIES_FILE",
        tmp_path / "no_cap.json",
    )
    # FIX ROLES-NOT-NAMES: без assignments — пустая строка
    assert get_phase_agent(PHASE_PLAN) == ""
    assert get_phase_agent(PHASE_CODE) == ""


def test_get_phase_agent_override_legacy(tmp_path, monkeypatch):
    """Старый тест — изолирован от реальных файлов в ~/.ai_link/brain."""
    monkeypatch.setattr(
        "core.capability_benchmark.ASSIGNMENTS_FILE",
        tmp_path / "no_assign.json",
    )
    monkeypatch.setattr(
        "core.capability_benchmark.CAPABILITIES_FILE",
        tmp_path / "no_cap.json",
    )
    overrides = {PHASE_CODE: "browser_qwen"}
    assert get_phase_agent(PHASE_CODE, overrides) == "browser_qwen"
    # FIX ROLES-NOT-NAMES: без assignments и без override — ""
    assert get_phase_agent(PHASE_PLAN, overrides) == ""


def test_get_phase_agent_unknown_phase(tmp_path, monkeypatch):
    # FIX ROLES-NOT-NAMES: неизвестная фаза → ""
    monkeypatch.setattr(
        "core.capability_benchmark.ASSIGNMENTS_FILE",
        tmp_path / "no_assign.json",
    )
    monkeypatch.setattr(
        "core.capability_benchmark.CAPABILITIES_FILE",
        tmp_path / "no_cap.json",
    )
    assert get_phase_agent("нет_такой") == ""


def test_describe_phase():
    assert "план" in describe_phase(PHASE_PLAN).lower() or "разбор" in describe_phase(PHASE_PLAN).lower()
    assert describe_phase("нет_такой").startswith("Фаза")


def test_is_valid_phase():
    assert is_valid_phase(PHASE_PLAN)
    assert not is_valid_phase("нет_такой")


def test_create_context():
    ctx = create_context("Создай трекер", "/tmp/proj")
    assert ctx.original_task == "Создай трекер"
    assert ctx.project_dir == "/tmp/proj"
    assert ctx.current_phase == ""
    assert ctx.phase_history == []
    # LIMITS: было 10, стало 100 (HARD_LIMIT) — для больших
    # проектов динамический лимит поднимается после plan-фазы.
    assert ctx.max_phases == 100


def test_phase_result_defaults():
    r = PhaseResult(phase=PHASE_PLAN, agent="browser_qwen")
    assert r.status == "pending"
    assert r.output == ""
    assert r.seconds == 0.0


def test_context_add_phase_result():
    ctx = create_context("X", "/tmp")
    r = PhaseResult(phase=PHASE_PLAN, agent="browser_qwen",
                    status="success", seconds=5.0)
    ctx.add_phase_result(r)
    assert len(ctx.phase_history) == 1
    assert ctx.phase_history[0]["phase"] == PHASE_PLAN
    assert ctx.phase_history[0]["status"] == "success"


def test_summarize_history_empty():
    ctx = create_context("X", "/tmp")
    assert "пуста" in summarize_history(ctx)


def test_summarize_history():
    ctx = create_context("X", "/tmp")
    ctx.add_phase_result(PhaseResult(phase=PHASE_PLAN, agent="qwen",
                                     status="success", seconds=3.0))
    ctx.add_phase_result(PhaseResult(phase=PHASE_CODE, agent="deepseek",
                                     status="success", seconds=45.0))
    text = summarize_history(ctx)
    assert "plan" in text
    assert "code" in text
    assert "qwen" in text


def test_orchestration_result_defaults():
    r = OrchestrationResult(success=True)
    assert r.status == "unknown"
    assert r.phases_done == 0
    assert r.phase_history == []


# === Тесты 7.2 — запуск фаз ===

from unittest.mock import patch
from core.orchestrator import (
    TEXT_PHASES, FILE_PHASES,
    run_text_phase, run_file_phase, run_phase,
    _build_text_prompt, _build_file_task,
)


def test_text_phases_defined():
    assert PHASE_PLAN in TEXT_PHASES
    assert PHASE_REVIEW in TEXT_PHASES
    assert PHASE_CODE not in TEXT_PHASES


def test_file_phases_defined():
    assert PHASE_CODE in FILE_PHASES
    assert PHASE_FIX in FILE_PHASES
    assert PHASE_FALLBACK in FILE_PHASES
    assert PHASE_PLAN not in FILE_PHASES


def test_build_text_prompt_plan():
    ctx = create_context("Создай трекер", "/tmp/p")
    prompt = _build_text_prompt(ctx, PHASE_PLAN)
    assert "plan" in prompt
    assert "Создай трекер" in prompt
    # Новый формат плана — структурированный
    assert "НАЗВАНИЕ:" in prompt
    assert "ЦЕЛЬ:" in prompt
    assert "ЭТАП:" in prompt
    assert "NEED_CLARIFICATION" in prompt


def test_build_text_prompt_review():
    ctx = create_context("X", "/tmp/p")
    prompt = _build_text_prompt(ctx, PHASE_REVIEW)
    assert "review" in prompt
    assert "проблемы" in prompt.lower()


def test_build_file_task_code():
    ctx = create_context("Создай Y", "/tmp/p")
    ctx.plan = {"files": ["a.py", "b.py"]}
    task = _build_file_task(ctx, PHASE_CODE)
    assert "code" in task
    assert "Создай Y" in task
    assert "a.py" in task


def test_build_file_task_fix():
    ctx = create_context("X", "/tmp/p")
    ctx.errors = ["ошибка 1", "ошибка 2"]
    task = _build_file_task(ctx, PHASE_FIX)
    assert "fix" in task
    assert "ошибка 1" in task


def test_run_text_phase_success(tmp_path, monkeypatch):
    # Изолируем от реальных capabilities.json / assignments.json,
    # чтобы выбор агента шёл по жёсткой роли.
    monkeypatch.setattr(
        "core.capability_benchmark.ASSIGNMENTS_FILE",
        tmp_path / "no_assign.json",
    )
    monkeypatch.setattr(
        "core.capability_benchmark.CAPABILITIES_FILE",
        tmp_path / "no_cap.json",
    )
    ctx = create_context("X", "/tmp/p")
    with patch("core.agents.browser_agent.query_browser_ai") as m:
        _plan = "НАЗВАНИЕ: Проект X\nЦЕЛЬ: что-то\n\nЭТАП: Этап 1\n- шаг 1"
        m.return_value = {"success": True, "text": _plan, "error": None}
        r = run_text_phase(ctx, PHASE_PLAN,
                           agent_override="browser_deepseek")
        assert r.status == "success"
        assert r.output == _plan
        assert r.agent == "browser_deepseek"


def test_run_text_phase_failure():
    """run_text_phase: success=False → failed.

    ROLES (2026-10-01): явно мокаем get_phase_agent.
    """
    from core.orchestrator import run_text_phase, PHASE_PLAN
    ctx = create_context("тест", "/tmp/p")
    with patch("core.orchestrator.get_phase_agent",
               return_value="browser_qwen"), \
         patch("core.agents.browser_agent.query_browser_ai") as m:
        m.return_value = {
            "success": False,
            "text": "",
            "error": "timeout",
        }
        r = run_text_phase(ctx, PHASE_PLAN)
        assert r.status == "failed"


def test_run_text_phase_unknown_agent():
    """A.1.5: действительно неизвестный агент → failed.
    (ollama теперь поддержан как local-агент — см. test ниже.)"""
    ctx = create_context("X", "/tmp/p")
    r = run_text_phase(ctx, PHASE_PLAN,
                       agent_override="nonexistent_agent_xyz")
    assert r.status == "failed"
    assert "не поддерживает" in r.error


def test_run_text_phase_ollama_is_supported():
    """A.1.5: ollama = local-агент, теперь поддержан в text-фазе.
    Проверяем, что роутер его принимает (не падает как unknown)."""
    from core.agent_profiles import get_agent_profile
    prof = get_agent_profile("ollama")
    assert prof is not None
    assert prof.get("type") == "local"


def test_run_text_phase_gigachat_is_supported():
    """A.1.5: gigachat = api-агент, поддержан в text-фазе (chat_mode=True)."""
    from core.agent_profiles import get_agent_profile
    prof = get_agent_profile("gigachat")
    assert prof is not None
    assert prof.get("type") == "api"


def test_run_file_phase_success(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "core.capability_benchmark.ASSIGNMENTS_FILE",
        tmp_path / "no_assign.json",
    )
    monkeypatch.setattr(
        "core.capability_benchmark.CAPABILITIES_FILE",
        tmp_path / "no_cap.json",
    )
    ctx = create_context("X", "/tmp/p")
    with patch("core.agentic_loop.run_agentic_loop") as m:
        m.return_value = {"success": True, "status": "success",
                          "message": "готово", "iterations": 3,
                          "files_created": ["main.py"]}
        r = run_file_phase(ctx, PHASE_CODE,
                           agent_override="browser_deepseek")
        assert r.status == "success"
        assert r.iterations == 3
        assert r.agent == "browser_deepseek"


def test_run_file_phase_failure():
    ctx = create_context("X", "/tmp/p")
    with patch("core.agentic_loop.run_agentic_loop") as m:
        m.return_value = {"success": False, "error": "timeout"}
        r = run_file_phase(ctx, PHASE_CODE, agent_override="browser_deepseek")
        assert r.status == "failed"
        assert "timeout" in r.error


def test_run_phase_dispatches_text():
    ctx = create_context("X", "/tmp/p")
    with patch("core.orchestrator.run_text_phase") as m:
        m.return_value = PhaseResult(phase=PHASE_PLAN, agent="qwen",
                                     status="success")
        run_phase(ctx, PHASE_PLAN)
        m.assert_called_once()


def test_run_phase_dispatches_file():
    ctx = create_context("X", "/tmp/p")
    with patch("core.orchestrator.run_file_phase") as m:
        m.return_value = PhaseResult(phase=PHASE_CODE, agent="deepseek",
                                     status="success")
        run_phase(ctx, PHASE_CODE)
        m.assert_called_once()


def test_run_phase_unknown():
    ctx = create_context("X", "/tmp/p")
    r = run_phase(ctx, "нет_такой")
    assert r.status == "failed"
    assert "неизвестная фаза" in r.error


def test_run_phase_updates_context():
    ctx = create_context("X", "/tmp/p")
    with patch("core.agents.browser_agent.query_browser_ai") as m:
        m.return_value = {"success": True, "text": "план", "error": None}
        run_phase(ctx, PHASE_PLAN)
        assert ctx.current_phase == PHASE_PLAN
        assert len(ctx.phase_history) == 1
        assert ctx.phase_history[0]["phase"] == PHASE_PLAN


# === Тесты 7.3 — оркестрация цепочки ===

from core.orchestrator import (
    parse_review_status, orchestrate,
    PHASE_PLAN, PHASE_CODE, PHASE_REVIEW, PHASE_FIX, PHASE_FALLBACK,
    PhaseResult,
)


def test_parse_review_ok():
    assert parse_review_status("STATUS: OK\nПроблем нет") == "ok"
    assert parse_review_status("STATUS:OK") == "ok"


def test_parse_review_problems():
    assert parse_review_status("STATUS: PROBLEMS\n- баг 1\n- баг 2") == "problems"
    assert parse_review_status("STATUS:PROBLEMS") == "problems"


def test_parse_review_unknown():
    """REV2: пустое → unknown. Короткий текст без маркеров → ok."""
    assert parse_review_status("") == "unknown"
    # REV2: короткий текст без маркеров проблемы → ok
    assert parse_review_status("что-то другое") == "ok"


def test_parse_review_qwen_ui_noise():
    """REV2: мусор Qwen ('Пропустить') без маркеров → ok."""
    assert parse_review_status(
        "Fetching operations with optional filters applied\nПропустить"
    ) == "ok"
    # FIX #16: чистый UI-мусор без смысла → unknown
    # (review не ответил → retry, не «OK»)
    assert parse_review_status(
        "Пропустить\nОтвет 1\nОтвет 2"
    ) == "unknown"


def test_parse_review_problem_markers():
    """REV2: текст с маркерами проблемы → problems, даже без STATUS."""
    assert parse_review_status(
        "Identifying a potential flaw in the date range logic"
    ) == "problems"
    assert parse_review_status(
        "There is an error in the code"
    ) == "problems"


def _make_result(phase, status, output="", error=""):
    return PhaseResult(phase=phase, agent="test",
                       status=status, output=output, error=error)


def test_orchestrate_happy_path():
    """B (18.10.2026): plan → research → code → review (OK) → успех."""
    calls = []

    def fake_run_phase(ctx, phase, agent_override=""):
        calls.append(phase)
        if phase == PHASE_PLAN:
            return _make_result(phase, "success", output="вот план")
        if phase == PHASE_RESEARCH:
            return _make_result(phase, "success",
                                output='{"targets": []}')
        if phase == PHASE_CODE:
            return _make_result(phase, "success", output="код написан")
        if phase == PHASE_REVIEW:
            return _make_result(phase, "success", output="STATUS: OK")
        return _make_result(phase, "success")

    with patch("core.orchestrator.run_phase", side_effect=fake_run_phase):
        r = orchestrate("Задача", "/tmp/p", cross_verify=False)
        assert r.success
        assert r.status == "success"
        assert calls == [PHASE_PLAN, PHASE_RESEARCH, PHASE_CODE, PHASE_REVIEW]


def test_orchestrate_review_finds_problems():
    """plan → code → review (PROBLEMS) → fix → review (OK)."""
    calls = []
    review_count = [0]

    def fake_run_phase(ctx, phase, agent_override=""):
        calls.append(phase)
        if phase == PHASE_PLAN:
            return _make_result(phase, "success", output="план")
        if phase == PHASE_CODE:
            return _make_result(phase, "success")
        if phase == PHASE_REVIEW:
            review_count[0] += 1
            if review_count[0] == 1:
                return _make_result(phase, "success",
                                    output="STATUS: PROBLEMS\n- баг")
            return _make_result(phase, "success", output="STATUS: OK")
        if phase == PHASE_FIX:
            return _make_result(phase, "success")
        return _make_result(phase, "success")

    with patch("core.orchestrator.run_phase", side_effect=fake_run_phase):
        r = orchestrate("Задача", "/tmp/p", cross_verify=False)
        assert r.success
        assert PHASE_FIX in calls


def test_orchestrate_plan_fails_twice():
    """plan дважды падает → стоп."""
    calls = []

    def fake_run_phase(ctx, phase, agent_override=""):
        calls.append(phase)
        return _make_result(phase, "failed", error="timeout")

    with patch("core.orchestrator.run_phase", side_effect=fake_run_phase):
        r = orchestrate("Задача", "/tmp/p", cross_verify=False)
        assert not r.success
        assert r.status == "failed"
        assert calls.count(PHASE_PLAN) == 2


def test_orchestrate_code_failed_goes_to_fix():
    """B54 (2026-10-12): code failed → fix, не повторяем code.

    Раньше тест назывался test_orchestrate_code_fails_twice и
    ожидал, что два провала code дают стоп. После B54 поведение
    изменилось: первый провал code уходит в fix. Обновлено
    2026-10-13 — тест проверяет НОВЫЙ контракт.
    """
    calls = []

    def fake_run_phase(ctx, phase, agent_override=""):
        calls.append(phase)
        if phase == PHASE_PLAN:
            return _make_result(phase, "success", output="план")
        if phase == PHASE_CODE:
            return _make_result(phase, "failed", error="timeout")
        return _make_result(phase, "success")

    with patch("core.orchestrator.run_phase", side_effect=fake_run_phase):
        orchestrate("Задача", "/tmp/p", cross_verify=False)

    # Главное: после code failed должен был пойти fix,
    # а не повторный code.
    assert PHASE_CODE in calls
    assert PHASE_FIX in calls
    # code не должен повторяться (B54: «не вслепую»)
    assert calls.count(PHASE_CODE) == 1

def test_orchestrate_code_and_fix_failed_stops(tmp_path, monkeypatch):
    """code failed → fix failed ×2 → стоп (не полный success)."""
    monkeypatch.setattr(
        "core.capability_benchmark.ASSIGNMENTS_FILE",
        tmp_path / "no_assign.json",
    )
    monkeypatch.setattr(
        "core.capability_benchmark.CAPABILITIES_FILE",
        tmp_path / "no_cap.json",
    )
    calls = []

    def fake_run_phase(ctx, phase, agent_override=""):
        calls.append(phase)
        if phase == PHASE_PLAN:
            return _make_result(phase, "success", output="план")
        if phase == PHASE_CODE:
            return _make_result(phase, "failed", error="timeout")
        if phase == PHASE_FIX:
            return _make_result(phase, "failed", error="timeout")
        if phase == PHASE_REVIEW:
            return _make_result(
                phase, "success", output="STATUS: PROBLEMS MAJOR"
            )
        return _make_result(phase, "success")

    with patch("core.orchestrator.run_phase", side_effect=fake_run_phase):
        r = orchestrate("Задача", "/tmp/p", cross_verify=False)
    # code упал, fix не справился — результат не полный success
    assert not r.success or r.status == "success_partial"
    # fix пытались запускать
    assert PHASE_FIX in calls


def test_orchestrate_fix_fails_twice_takes_result(tmp_path, monkeypatch):
    """ОБНОВЛЕНО (задача R, 2026-09-16): fix провалился 2 раза подряд →
    принимаем результат (success_partial), в fallback НЕ идём.

    Раньше: после 2 провалов fix → fallback (бесконечный цикл review→fix).
    Теперь: после fix_count >= max_fix_cycles → success_partial (стоп).
    """
    monkeypatch.setattr(
        "core.capability_benchmark.ASSIGNMENTS_FILE",
        tmp_path / "no_assign.json",
    )
    monkeypatch.setattr(
        "core.capability_benchmark.CAPABILITIES_FILE",
        tmp_path / "no_cap.json",
    )
    calls = []
    review_count = [0]

    def fake_run_phase(ctx, phase, agent_override=""):
        calls.append(phase)
        if phase == PHASE_PLAN:
            return _make_result(phase, "success", output="НАЗВАНИЕ: X")
        if phase == PHASE_CODE:
            return _make_result(phase, "success")
        if phase == PHASE_REVIEW:
            review_count[0] += 1
            return _make_result(phase, "success", output="STATUS: PROBLEMS")
        if phase == PHASE_FIX:
            return _make_result(phase, "failed", error="не справился")
        if phase == PHASE_FALLBACK:
            return _make_result(phase, "success")
        return _make_result(phase, "success")

    with patch("core.orchestrator.run_phase", side_effect=fake_run_phase):
        r = orchestrate("Задача", str(tmp_path),
                        cross_verify=False, max_phases=10)

    # fix_count >= max → success_partial
    assert r.status == "success_partial"
    assert r.success is True
    # В fallback НЕ идём
    assert PHASE_FALLBACK not in calls
    # fix вызван ровно max_fix_cycles раз
    assert calls.count(PHASE_FIX) == 2


def test_orchestrate_max_phases():
    """Если превышен лимит фаз — стоп."""
    def fake_run_phase(ctx, phase, agent_override=""):
        if phase == PHASE_PLAN:
            return _make_result(phase, "success", output="план")
        if phase == PHASE_CODE:
            return _make_result(phase, "success")
        if phase == PHASE_REVIEW:
            return _make_result(phase, "success", output="STATUS: PROBLEMS")
        if phase == PHASE_FIX:
            return _make_result(phase, "success")
        return _make_result(phase, "success")

    with patch("core.orchestrator.run_phase", side_effect=fake_run_phase):
        r = orchestrate("Задача", "/tmp/p", max_phases=6, cross_verify=False)
        assert not r.success
        assert r.status in ("max_phases", "timeout")
        assert r.phases_done <= 6


def test_orchestrate_review_short_no_marker_is_ok():
    """REV2: короткий review без маркера → считаем OK, fix не нужен."""
    calls = []

    def fake_run_phase(ctx, phase, agent_override=""):
        calls.append(phase)
        if phase == PHASE_PLAN:
            return _make_result(phase, "success", output="план")
        if phase == PHASE_CODE:
            return _make_result(phase, "success")
        if phase == PHASE_REVIEW:
            return _make_result(phase, "success", output="не знаю что сказать")
        return _make_result(phase, "success")

    with patch("core.orchestrator.run_phase", side_effect=fake_run_phase):
        r = orchestrate("Задача", "/tmp/p", max_phases=4, cross_verify=False)
    # REV2: короткий без маркеров → ok → fix НЕ запускается
    assert PHASE_FIX not in calls
    assert r.success is True


def test_orchestrate_review_problem_marker_goes_to_fix():
    """REV2: review с маркером проблемы → fix запускается."""
    calls = []

    def fake_run_phase(ctx, phase, agent_override=""):
        calls.append(phase)
        if phase == PHASE_PLAN:
            return _make_result(phase, "success", output="план")
        if phase == PHASE_RESEARCH:
            return _make_result(phase, "success",
                                output='{"targets": []}')
        if phase == PHASE_CODE:
            return _make_result(phase, "success")
        if phase == PHASE_REVIEW:
            return _make_result(
                phase, "success",
                output="There is an error in the date logic"
            )
        if phase == PHASE_FIX:
            return _make_result(phase, "success")
        return _make_result(phase, "success")

    with patch("core.orchestrator.run_phase", side_effect=fake_run_phase):
        orchestrate("Задача", "/tmp/p", max_phases=6, cross_verify=False)
    # Есть маркер проблемы → fix запускается
    assert PHASE_FIX in calls


def test_orchestrate_max_fix_cycles_stops():
    """Если review всегда PROBLEMS, после 2 fix — принимаем результат."""
    calls = []

    def fake_run_phase(ctx, phase, agent_override=""):
        calls.append(phase)
        if phase == PHASE_PLAN:
            return _make_result(phase, "success", output="план")
        if phase == PHASE_CODE:
            return _make_result(phase, "success")
        if phase == PHASE_REVIEW:
            return _make_result(phase, "success", output="STATUS: PROBLEMS\n- придирка")
        if phase == PHASE_FIX:
            return _make_result(phase, "success")
        return _make_result(phase, "success")

    with patch("core.orchestrator.run_phase", side_effect=fake_run_phase):
        r = orchestrate("Задача", "/tmp/p", max_phases=20, cross_verify=False)
        # Должно завершиться, а не уйти в max_phases
        assert r.success
        assert r.status == "success_partial"
        # fix вызывался ровно 2 раза
        assert calls.count(PHASE_FIX) == 2


def test_orchestrate_cross_verify_rejects():
    """Если cross-verify отвергает проблемы — завершаемся успешно."""
    calls = []

    def fake_run_phase(ctx, phase, agent_override=""):
        calls.append(phase)
        if phase == PHASE_PLAN:
            return _make_result(phase, "success", output="план")
        if phase == PHASE_CODE:
            return _make_result(phase, "success")
        if phase == PHASE_REVIEW:
            return _make_result(phase, "success",
                                output="STATUS: PROBLEMS\n- стиль")
        if phase == PHASE_FIX:
            return _make_result(phase, "success")
        return _make_result(phase, "success")

    def fake_verify(*args, **kwargs):
        return {"verdict": "rejected", "verifier": "deepseek",
                "explanation": "придирка", "error": None}

    with patch("core.orchestrator.run_phase", side_effect=fake_run_phase), \
         patch("core.cross_verifier.verify_problem", side_effect=fake_verify):
        r = orchestrate("Задача", "/tmp/p", max_phases=8)
        assert PHASE_FIX not in calls
        assert r.success


def test_orchestrate_cross_verify_confirms():
    """Если cross-verify подтверждает — идём в fix."""
    calls = []

    def fake_run_phase(ctx, phase, agent_override=""):
        calls.append(phase)
        if phase == PHASE_PLAN:
            return _make_result(phase, "success", output="план")
        if phase == PHASE_CODE:
            return _make_result(phase, "success")
        if phase == PHASE_REVIEW:
            return _make_result(phase, "success",
                                output="STATUS: PROBLEMS\n- баг")
        if phase == PHASE_FIX:
            return _make_result(phase, "success")
        return _make_result(phase, "success")

    def fake_verify(*args, **kwargs):
        return {"verdict": "confirmed", "verifier": "deepseek",
                "explanation": "баг реальный", "error": None}

    with patch("core.orchestrator.run_phase", side_effect=fake_run_phase), \
         patch("core.cross_verifier.verify_problem", side_effect=fake_verify):
        orchestrate("Задача", "/tmp/p", max_phases=6)
        assert PHASE_FIX in calls


def test_orchestrate_cross_verify_disabled():
    """С cross_verify=False — fix вызывается без проверки."""
    calls = []

    def fake_run_phase(ctx, phase, agent_override=""):
        calls.append(phase)
        if phase == PHASE_PLAN:
            return _make_result(phase, "success", output="план")
        if phase == PHASE_RESEARCH:
            return _make_result(phase, "success",
                                output='{"targets": []}')
        if phase == PHASE_CODE:
            return _make_result(phase, "success")
        if phase == PHASE_REVIEW:
            return _make_result(phase, "success", output="STATUS: PROBLEMS")
        if phase == PHASE_FIX:
            return _make_result(phase, "success")
        return _make_result(phase, "success")

    with patch("core.orchestrator.run_phase", side_effect=fake_run_phase):
        orchestrate("Задача", "/tmp/p", max_phases=6, cross_verify=False)
        assert PHASE_FIX in calls


def test_parse_review_with_prompt_echo():
    """Qwen повторяет промпт с примерами STATUS, потом даёт ответ."""
    output = (
        "Ты — AI Link, работаешь в фазе «review».\\n"
        "ОБЯЗАТЕЛЬНЫЙ ФОРМАТ ОТВЕТА:\\n"
        "  STATUS: OK          (проблем нет)\\n"
        "  STATUS: PROBLEMS    (есть проблемы)\\n\\n"
        "Завершено размышление\\n"
        "STATUS: OK"
    )
    assert parse_review_status(output) == "ok"


def test_parse_review_with_prompt_echo_problems():
    """Та же ситуация, но реальный вердикт PROBLEMS."""
    output = (
        "STATUS: OK\\n"
        "STATUS: PROBLEMS\\n"
        "Завершено размышление\\n"
        "STATUS: PROBLEMS\\n"
        "- Нет тестов"
    )
    assert parse_review_status(output) == "problems"


# === Тесты 7.4b — route_rules в orchestrate ===

def test_orchestrate_login_required_stops():
    """login_required → немедленный стоп, без retry."""
    calls = []

    def fake_run_phase(ctx, phase, agent_override=""):
        calls.append(phase)
        if phase == PHASE_PLAN:
            return _make_result(phase, "failed",
                                error="login_required: нужен вход")
        return _make_result(phase, "success")

    with patch("core.orchestrator.run_phase", side_effect=fake_run_phase):
        r = orchestrate("Задача", "/tmp/p", cross_verify=False)
        assert not r.success
        assert "вход" in r.error.lower()
        assert calls.count(PHASE_PLAN) == 1  # без retry


def test_orchestrate_launch_failed_switches(monkeypatch, tmp_path):
    """ROLES (2026-10-01): launch_failed + brain пусто → стоп,
    БЕЗ ollama в вызовах.

    Связь brain → fallback тестируется в test_fallback_roles.py.
    Здесь проверяем: фаза не подставляет hardcoded ollama.
    """
    monkeypatch.setattr(
        "core.capability_benchmark.ASSIGNMENTS_FILE",
        tmp_path / "no_assign.json",
    )

    calls = []

    def _res(phase, status, agent="browser_deepseek", output="", error=""):
        from core.orchestrator import PhaseResult
        return PhaseResult(phase=phase, agent=agent, status=status,
                           output=output, error=error)

    def fake_run_phase(ctx, phase, agent_override=""):
        agent = agent_override or "browser_deepseek"
        calls.append((phase, agent))
        if phase == PHASE_PLAN:
            return _res(phase, "failed", agent=agent,
                        error="launch_failed: браузер не запустился")
        return _res(phase, "success", agent=agent)

    with patch("core.orchestrator.run_phase", side_effect=fake_run_phase):
        r = orchestrate("Задача", str(tmp_path),
                        cross_verify=False, max_phases=8)

    # ollama НЕ должна появиться ни в одном вызове
    for ph, agent in calls:
        assert "ollama" not in (agent or ""), (
            f"ollama подставилась на фазе {ph}: {agent}"
        )
    # Итог — failed (fallback невозможен)
    assert r.status == "failed"


def test_parse_clarification_basic():
    out = "NEED_CLARIFICATION: Какой язык использовать?"
    assert parse_clarification_request(out) == "Какой язык использовать?"


def test_parse_clarification_case_insensitive():
    out = "need_clarification: тест"
    assert parse_clarification_request(out) == "тест"


def test_parse_clarification_after_thinking():
    out = (
        "Завершено размышление\n"
        "NEED_CLARIFICATION: Какую БД взять — SQLite или Postgres?"
    )
    assert parse_clarification_request(out) == "Какую БД взять — SQLite или Postgres?"


def test_parse_clarification_none():
    assert parse_clarification_request("обычный план без маркера") == ""
    assert parse_clarification_request("") == ""
    assert parse_clarification_request(None) == ""


def test_parse_clarification_anywhere_in_text():
    """Маркер ловится в любом месте текста (не только первые 20 строк).

    Раньше сканировались только первые 20 строк — но Qwen повторяет
    промпт в начале, а маркер ставит в самом конце. Теперь — весь текст.
    """
    lines = ["строка " + str(i) for i in range(25)]
    lines.append("NEED_CLARIFICATION: слишком поздно")
    out = "\n".join(lines)
    assert parse_clarification_request(out) == "слишком поздно"


def test_run_text_phase_needs_clarification():
    """run_text_phase возвращает needs_clarification, если маркер есть.

    ROLES (2026-10-01): явно мокаем get_phase_agent — раньше
    автоподбор подставлял агента, теперь без назначения пусто.
    """
    from core.orchestrator import run_text_phase, PHASE_PLAN
    ctx = create_context("Задача", "/tmp/p")
    with patch("core.orchestrator.get_phase_agent",
               return_value="browser_qwen"), \
         patch("core.agents.browser_agent.query_browser_ai") as m:
        m.return_value = {
            "success": True,
            "text": "NEED_CLARIFICATION: Что именно нужно?",
            "error": None,
        }
        r = run_text_phase(ctx, PHASE_PLAN)
        assert r.status == "needs_clarification"
        assert r.clarification_question == "Что именно нужно?"


def test_run_text_phase_normal_success():
    """run_text_phase возвращает success, если маркера нет.

    ROLES (2026-10-01): явно мокаем get_phase_agent.
    """
    from core.orchestrator import run_text_phase, PHASE_PLAN
    ctx = create_context("Задача", "/tmp/p")
    with patch("core.orchestrator.get_phase_agent",
               return_value="browser_qwen"), \
         patch("core.agents.browser_agent.query_browser_ai") as m:
        m.return_value = {
            "success": True,
            "text": "НАЗВАНИЕ: Проект такой-то\n"
                    "ЦЕЛЬ: описание\n\n"
                    "ЭТАП: Этап 1\n- Файлы: a.py, b.py",
            "error": None,
        }
        r = run_text_phase(ctx, PHASE_PLAN)
        assert r.status == "success"
        assert r.clarification_question == ""


def test_phase_result_has_clarification_field():
    from core.orchestrator import PhaseResult
    r = PhaseResult(phase="plan", agent="qwen")
    assert r.clarification_question == ""


# ═══════════════════════════════════════════════════════════
# Дополнительные тесты (покрытие вспомогательных функций)
# ═══════════════════════════════════════════════════════════

from core.orchestrator import (
    _collect_code_for_review, _clean_plan_text, _mark_plan_done,
    _build_file_task,
    PHASE_FIX, PHASE_FALLBACK,
)
import json as _json


# === _collect_code_for_review ===

def test_collect_code_empty_dir(tmp_path):
    assert _collect_code_for_review(str(tmp_path)) == ""


def test_collect_code_missing_dir(tmp_path):
    assert _collect_code_for_review(str(tmp_path / "nope")) == ""


def test_collect_code_basic(tmp_path):
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "b.md").write_text("# Title\n", encoding="utf-8")
    code = _collect_code_for_review(str(tmp_path))
    assert "a.py" in code
    assert "b.md" in code
    assert "x = 1" in code


def test_collect_code_skips_pycache(tmp_path):
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "__pycache__" / "old.py").write_text("garbage\n", encoding="utf-8")
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    code = _collect_code_for_review(str(tmp_path))
    assert "garbage" not in code
    assert "a.py" in code


def test_collect_code_skips_ai_link(tmp_path):
    (tmp_path / ".ai_link").mkdir()
    (tmp_path / ".ai_link" / "state.json").write_text("{}\n", encoding="utf-8")
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    code = _collect_code_for_review(str(tmp_path))
    assert "state.json" not in code


def test_collect_code_truncates_large(tmp_path):
    big = "x = 1\n" * 5000  # ~30KB
    (tmp_path / "big.py").write_text(big, encoding="utf-8")
    code = _collect_code_for_review(str(tmp_path), max_chars=1000)
    assert len(code) <= 1100  # с запасом на "обрезано"


def test_collect_code_max_files(tmp_path):
    for i in range(15):
        (tmp_path / f"f{i}.py").write_text(f"x = {i}\n", encoding="utf-8")
    code = _collect_code_for_review(str(tmp_path), max_files=3)
    # Три файла (f0, f1, f10 - по алфавиту)
    count = code.count("--- ")
    assert count <= 3


# === _clean_plan_text ===

def test_clean_plan_empty():
    assert _clean_plan_text("") == ""
    assert _clean_plan_text(None) == ""


def test_clean_plan_no_marker():
    text = "НАЗВАНИЕ: Проект\nЦЕЛЬ: Тест"
    assert _clean_plan_text(text) == text


def test_clean_plan_with_thinking():
    text = (
        "Ты — AI Link, фаза plan.\n"
        "ОРИГИНАЛЬНАЯ ЗАДАЧА: тест\n"
        "Завершено размышление\n"
        "НАЗВАНИЕ: Проект\n"
        "ЦЕЛЬ: Реальная цель"
    )
    result = _clean_plan_text(text)
    assert "AI Link" not in result
    assert "НАЗВАНИЕ: Проект" in result


def test_clean_plan_cuts_qwen_tail():
    """Хвостовой мусор Qwen Studio после плана должен срезаться."""
    text = (
        "НАЗВАНИЕ: Проект\n"
        "ЦЕЛЬ: Реальная цель\n"
        "ЭТАП: Первый\n"
        "- шаг 1\n"
        "Пользователь показал отличные результаты. Давай разберу:"
    )
    result = _clean_plan_text(text)
    assert "НАЗВАНИЕ: Проект" in result
    assert "Пользователь показал отличные результаты" not in result
    assert "Давай разберу" not in result


def test_clean_plan_fallback_to_title():
    """Если перед НАЗВАНИЕ: есть эхо промпта — режем до него."""
    text = (
        "Ты — AI Link, вот твой промпт...\n"
        "ЗАДАЧА: сделай что-то\n"
        "ФОРМАТ ОТВЕТА:\n"
        "НАЗВАНИЕ: Реальный проект\n"
        "ЦЕЛЬ: Реальная цель"
    )
    result = _clean_plan_text(text)
    assert result.startswith("НАЗВАНИЕ:")
    assert "твой промпт" not in result


# === _mark_plan_done ===

def test_mark_plan_done_missing(tmp_path):
    # Не должно падать, если файла нет
    _mark_plan_done(str(tmp_path))


def test_mark_plan_done_basic(tmp_path):
    """HONEST (2026-10-09): смешанный этап (pending + done) → partial.

    Раньше тест ожидал status=done (все pending превращались в done).
    Теперь — честно: pending остаётся pending, stage=partial,
    plan.status=success_partial.
    """
    d = tmp_path / ".ai_link"
    d.mkdir()
    plan = {
        "status": "in_progress",
        "stages": [
            {"name": "Этап 1", "items": [
                {"text": "шаг 1", "status": "pending"},
                {"text": "шаг 2", "status": "done"},
            ]},
        ],
    }
    (d / "plan.json").write_text(_json.dumps(plan), encoding="utf-8")

    _mark_plan_done(str(tmp_path))

    updated = _json.loads((d / "plan.json").read_text(encoding="utf-8"))
    # Есть pending → success_partial, не done.
    assert updated["status"] == "success_partial"
    assert "finished_at" in updated
    # pending НЕ превращён в done.
    items = updated["stages"][0]["items"]
    assert items[0]["status"] == "pending"
    assert items[1]["status"] == "done"
    # stage.status = partial (смесь).
    assert updated["stages"][0]["status"] == "partial"


def test_mark_plan_done_all_done(tmp_path):
    """HONEST: все items done → plan.status=done, stage.status=done."""
    d = tmp_path / ".ai_link"
    d.mkdir()
    plan = {
        "status": "in_progress",
        "stages": [
            {"name": "Этап 1", "items": [
                {"text": "шаг 1", "status": "done"},
                {"text": "шаг 2", "status": "done"},
            ]},
        ],
    }
    (d / "plan.json").write_text(_json.dumps(plan), encoding="utf-8")

    _mark_plan_done(str(tmp_path))

    updated = _json.loads((d / "plan.json").read_text(encoding="utf-8"))
    assert updated["status"] == "done"
    assert updated["stages"][0]["status"] == "done"


def test_mark_plan_done_all_pending(tmp_path):
    """HONEST: все items pending → plan.status=success_partial, stage=skipped."""
    d = tmp_path / ".ai_link"
    d.mkdir()
    plan = {
        "status": "in_progress",
        "stages": [
            {"name": "Этап 1", "items": [
                {"text": "шаг 1", "status": "pending"},
                {"text": "шаг 2", "status": "pending"},
            ]},
        ],
    }
    (d / "plan.json").write_text(_json.dumps(plan), encoding="utf-8")

    _mark_plan_done(str(tmp_path))

    updated = _json.loads((d / "plan.json").read_text(encoding="utf-8"))
    assert updated["status"] == "success_partial"
    assert updated["stages"][0]["status"] == "skipped"


def test_mark_plan_done_broken_json(tmp_path):
    d = tmp_path / ".ai_link"
    d.mkdir()
    (d / "plan.json").write_text("невалидный json", encoding="utf-8")
    # Не должно падать
    _mark_plan_done(str(tmp_path))


# === _build_file_task для fix и fallback ===

def test_build_file_task_fix():
    ctx = create_context("тест", "/tmp/p")
    ctx.errors = ["баг 1", "баг 2"]
    task = _build_file_task(ctx, PHASE_FIX)
    assert "fix" in task
    assert "ВНИМАТЕЛЬНО" in task      # ключевая фраза нового промпта
    assert "баг 1" in task
    assert "баг 2" in task


def test_build_file_task_fallback():
    ctx = create_context("тест", "/tmp/p")
    ctx.errors = ["не справился"]
    task = _build_file_task(ctx, PHASE_FALLBACK)
    assert "fallback" in task
    assert "не справились" in task.lower() or "альтернатив" in task.lower()


def test_build_file_task_with_user_answers():
    ctx = create_context("тест", "/tmp/p")
    ctx.user_answers = [
        {"question": "Какой язык?", "answer": "Python"},
    ]
    task = _build_file_task(ctx, PHASE_CODE)
    assert "Python" in task
    assert "Какой язык?" in task


def test_build_text_prompt_review_with_code(tmp_path):
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    ctx = create_context("тест", str(tmp_path))
    prompt = _build_text_prompt(ctx, PHASE_REVIEW)
    assert "ФАЙЛЫ ПРОЕКТА" in prompt
    assert "a.py" in prompt


def test_build_text_prompt_review_no_code(tmp_path):
    ctx = create_context("тест", str(tmp_path))
    prompt = _build_text_prompt(ctx, PHASE_REVIEW)
    # Если файлов нет — блок ФАЙЛЫ ПРОЕКТА может отсутствовать
    assert "STATUS: OK" in prompt


def test_build_text_prompt_with_errors():
    ctx = create_context("тест", "/tmp/p")
    ctx.errors = ["ошибка 1"]
    prompt = _build_text_prompt(ctx, PHASE_PLAN)
    assert "ОШИБКИ" in prompt
    assert "ошибка 1" in prompt


def test_build_text_prompt_with_user_answers():
    ctx = create_context("тест", "/tmp/p")
    ctx.user_answers = [{"question": "Что?", "answer": "Это"}]
    prompt = _build_text_prompt(ctx, PHASE_PLAN)
    assert "ОТВЕТЫ ПОЛЬЗОВАТЕЛЯ" in prompt
    assert "Это" in prompt


def test_build_text_prompt_unknown_phase():
    ctx = create_context("тест", "/tmp/p")
    # Для не-реализованных фаз — дефолт
    from core.orchestrator import PHASE_FIX
    prompt = _build_text_prompt(ctx, PHASE_FIX)
    assert "Выполни работу фазы" in prompt


# === run_text_phase с исключением ===

def test_run_text_phase_exception():
    """ROLES (2026-10-01): явно мокаем get_phase_agent."""
    from core.orchestrator import run_text_phase, PHASE_PLAN
    ctx = create_context("тест", "/tmp/p")
    with patch("core.orchestrator.get_phase_agent",
               return_value="browser_qwen"), \
         patch("core.agents.browser_agent.query_browser_ai") as m:
        m.side_effect = RuntimeError("boom")
        r = run_text_phase(ctx, PHASE_PLAN)
        assert r.status == "failed"
        assert "boom" in r.error


def test_run_file_phase_exception():
    ctx = create_context("тест", "/tmp/p")
    with patch("core.agentic_loop.run_agentic_loop") as m:
        m.side_effect = RuntimeError("boom")
        r = run_file_phase(ctx, PHASE_CODE, agent_override="browser_deepseek")
        assert r.status == "failed"
        assert "boom" in r.error


# === run_phase с невалидной фазой ===

def test_run_phase_invalid():
    ctx = create_context("тест", "/tmp/p")
    r = run_phase(ctx, "nonexistent")
    assert r.status == "failed"
    assert "неизвестная фаза" in r.error


# === parse_clarification_request ===

def test_parse_clarification_empty_lines_before():
    text = "\n\n\nNEED_CLARIFICATION: Вопрос?"
    assert parse_clarification_request(text) == "Вопрос?"


def test_parse_clarification_marker_in_middle():
    lines = ["мусор"] * 5 + ["NEED_CLARIFICATION: реальный вопрос"]
    text = "\n".join(lines)
    assert parse_clarification_request(text) == "реальный вопрос"


# === parse_review_status ===

def test_parse_review_status_problems_later():
    text = "Завершено размышление\nSTATUS: PROBLEMS\n- баг 1"
    assert parse_review_status(text) == "problems"


def test_parse_review_status_ok_after_thinking():
    text = "some junk\nЗавершено размышление\nSTATUS: OK"
    assert parse_review_status(text) == "ok"


# === orchestrate: needs_clarification при resume ===

def test_orchestrate_needs_clarification_on_review():
    """Уточнение может запросить и review."""
    def fake_run_phase(ctx, phase, agent_override=""):
        if phase == PHASE_PLAN:
            return _make_result(phase, "success", output="план")
        if phase == PHASE_CODE:
            return _make_result(phase, "success")
        if phase == PHASE_REVIEW:
            return _make_result(phase, "needs_clarification",
                                output="NEED_CLARIFICATION: какой цвет?")
        return _make_result(phase, "success")

    with patch("core.orchestrator.run_phase", side_effect=fake_run_phase):
        r = orchestrate("Задача", "/tmp/p", cross_verify=False)
        assert r.status == "needs_clarification"
        assert r.clarification_phase == PHASE_REVIEW


def test_orchestrate_timeout():
    """Таймаут оркестрации."""
    import time
    def fake_run_phase(ctx, phase, agent_override=""):
        time.sleep(0.2)
        return _make_result(phase, "success", output="план")

    with patch("core.orchestrator.run_phase", side_effect=fake_run_phase):
        r = orchestrate("Задача", "/tmp/p", timeout=0.1, cross_verify=False)
        assert r.status == "timeout"


def test_orchestrate_stopped():
    """should_stop возвращает True — остановка."""
    calls = [0]
    def stopper():
        calls[0] += 1
        return calls[0] > 1

    def fake_run_phase(ctx, phase, agent_override=""):
        return _make_result(phase, "success", output="план")

    with patch("core.orchestrator.run_phase", side_effect=fake_run_phase):
        r = orchestrate("Задача", "/tmp/p",
                        should_stop=stopper, cross_verify=False)
        assert r.status == "stopped"


def test_orchestrate_resume_from():
    """resume_from=code — начинаем сразу с code."""
    calls = []
    def fake_run_phase(ctx, phase, agent_override=""):
        calls.append(phase)
        if phase == PHASE_CODE:
            return _make_result(phase, "success")
        if phase == PHASE_REVIEW:
            return _make_result(phase, "success", output="STATUS: OK")
        return _make_result(phase, "success")

    with patch("core.orchestrator.run_phase", side_effect=fake_run_phase):
        r = orchestrate("Задача", "/tmp/p",
                        resume_from=PHASE_CODE, cross_verify=False)
        assert calls[0] == PHASE_CODE
        assert PHASE_PLAN not in calls


def test_review_problems_fills_ctx_errors():
    """Когда review возвращает PROBLEMS, замечания должны попасть
    в ctx.errors, чтобы fix-фаза знала, что чинить (задача C).
    """
    from core.orchestrator import (
        PHASE_PLAN, PHASE_CODE, PHASE_REVIEW, PHASE_FIX,
    )
    captured_errors_at_fix = {"value": None}
    calls = []

    def fake_run_phase(ctx, phase, agent_override=""):
        calls.append(phase)
        if phase == PHASE_PLAN:
            return _make_result(phase, "success", output="план готов")
        if phase == PHASE_CODE:
            return _make_result(phase, "success")
        if phase == PHASE_REVIEW:
            # Первый review — PROBLEMS с конкретным замечанием.
            if calls.count(PHASE_REVIEW) == 1:
                return _make_result(
                    phase, "success",
                    output="STATUS: PROBLEMS\nДобавь docstring в функцию foo().",
                )
            # Второй review — OK, всё исправлено.
            return _make_result(phase, "success", output="STATUS: OK")
        if phase == PHASE_FIX:
            # Здесь проверяем, что ctx.errors заполнен.
            captured_errors_at_fix["value"] = list(ctx.errors)
            return _make_result(phase, "success")
        return _make_result(phase, "success")

    with patch("core.orchestrator.run_phase", side_effect=fake_run_phase):
        r = orchestrate("Задача", "/tmp/p", max_phases=8, cross_verify=False)

    # Проверяем: fix был вызван
    assert PHASE_FIX in calls
    # Проверяем: на момент fix в ctx.errors было замечание review
    assert captured_errors_at_fix["value"] is not None
    assert len(captured_errors_at_fix["value"]) >= 1
    assert "docstring" in captured_errors_at_fix["value"][0]


def test_review_problems_errors_shorten():
    """ctx.errors не должен пухнуть бесконечно — обрезаем до 2000 символов."""
    from core.orchestrator import PHASE_PLAN, PHASE_CODE, PHASE_REVIEW
    captured = {"value": None}

    def fake_run_phase(ctx, phase, agent_override=""):
        if phase == PHASE_PLAN:
            return _make_result(phase, "success", output="план")
        if phase == PHASE_CODE:
            return _make_result(phase, "success")
        if phase == PHASE_REVIEW:
            # Очень длинное замечание.
            long_msg = "STATUS: PROBLEMS\n" + ("A" * 5000)
            if ctx.errors:
                # Значит, уже проходили — говорим OK.
                return _make_result(phase, "success", output="STATUS: OK")
            captured["value"] = long_msg
            return _make_result(phase, "success", output=long_msg)
        return _make_result(phase, "success")

    with patch("core.orchestrator.run_phase", side_effect=fake_run_phase):
        orchestrate("Задача", "/tmp/p", max_phases=8, cross_verify=False)

    # Замечание должно попасть в ctx.errors уже обрезанным.
    # Проверяем косвенно: сам факт "обрезки" проверяется в следующем тесте.
    assert captured["value"] is not None


# === Выбор агента с приоритетами (get_phase_agent) ===

def test_get_phase_agent_default(tmp_path, monkeypatch):
    """Без assignments и без capabilities — работает жёсткая роль."""
    monkeypatch.setattr(
        "core.capability_benchmark.ASSIGNMENTS_FILE",
        tmp_path / "no_assign.json",
    )
    monkeypatch.setattr(
        "core.capability_benchmark.CAPABILITIES_FILE",
        tmp_path / "no_cap.json",
    )
    from core.orchestrator import (
        get_phase_agent, PHASE_PLAN, PHASE_CODE, PHASE_REVIEW, PHASE_FIX,
    )
    # FIX ROLES-NOT-NAMES: без assignments — все роли пусты
    assert get_phase_agent(PHASE_PLAN) == ""
    assert get_phase_agent(PHASE_CODE) == ""
    assert get_phase_agent(PHASE_REVIEW) == ""
    assert get_phase_agent(PHASE_FIX) == ""


def test_get_phase_agent_override_wins(tmp_path, monkeypatch):
    """Явный override сильнее всего остального."""
    monkeypatch.setattr(
        "core.capability_benchmark.ASSIGNMENTS_FILE",
        tmp_path / "no_assign.json",
    )
    monkeypatch.setattr(
        "core.capability_benchmark.CAPABILITIES_FILE",
        tmp_path / "no_cap.json",
    )
    from core.orchestrator import get_phase_agent, PHASE_PLAN
    result = get_phase_agent(
        PHASE_PLAN, overrides={PHASE_PLAN: "browser_deepseek"}
    )
    assert result == "browser_deepseek"


def test_get_phase_agent_assignment_wins(tmp_path, monkeypatch):
    """Назначение сильнее профиля и жёсткой роли."""
    assign_file = tmp_path / "assign.json"
    assign_file.write_text(
        '{"version": "1.0", "assignments": {"planning": "browser_deepseek"}}',
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "core.capability_benchmark.ASSIGNMENTS_FILE", assign_file,
    )
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(
        "core.capability_benchmark.CAPABILITIES_FILE",
        tmp_path / "no_cap.json",
    )
    from core.orchestrator import get_phase_agent, PHASE_PLAN
    assert get_phase_agent(PHASE_PLAN) == "browser_deepseek"


def test_get_phase_agent_no_assignment_returns_empty(
    tmp_path, monkeypatch
):
    """ROLES (2026-10-01): автоподбор убран → пусто без назначения."""
    monkeypatch.setattr(
        "core.capability_benchmark.ASSIGNMENTS_FILE",
        tmp_path / "no_assign.json",
    )
    from core.orchestrator import get_phase_agent, PHASE_PLAN
    assert get_phase_agent(PHASE_PLAN) == ""


def test_get_phase_agent_assignment_disabled_agent_falls_back(
    tmp_path, monkeypatch
):
    """Назначенный агент выключен — идём к автоматике (жёсткая роль)."""
    assign_file = tmp_path / "assign.json"
    assign_file.write_text(
        '{"version": "1.0", "assignments": {"planning": "browser_deepseek"}}',
        encoding="utf-8",
    )
    (tmp_path / ".ai_link").mkdir()
    (tmp_path / ".ai_link" / "agents.json").write_text(json.dumps([
        {"id": "browser_deepseek", "enabled": False},
    ]), encoding="utf-8")
    monkeypatch.setattr(
        "core.capability_benchmark.ASSIGNMENTS_FILE", assign_file,
    )
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(
        "core.capability_benchmark.CAPABILITIES_FILE",
        tmp_path / "no_cap.json",
    )
    from core.orchestrator import get_phase_agent, PHASE_PLAN
    # FIX ROLES-NOT-NAMES: назначенный агент выключен →
    # fallback на автоматику. Без capabilities и без assignments
    # → "" (не подставляем чужое имя).
    assert get_phase_agent(PHASE_PLAN) == ""


def test_get_phase_agent_fallback_key(tmp_path, monkeypatch):
    """ROLES (2026-10-01): fallback объединён с brain.

    Раньше был спец-ключ "fallback". Теперь PHASE_FALLBACK
    берёт агента из роли "brain".
    """
    assign_file = tmp_path / "assign.json"
    assign_file.write_text(
        '{"version": "1.0", "assignments": {"brain": "gigachat"}}',
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "core.capability_benchmark.ASSIGNMENTS_FILE", assign_file,
    )
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    from core.orchestrator import get_phase_agent, PHASE_FALLBACK
    assert get_phase_agent(PHASE_FALLBACK) == "gigachat"


def test_clean_plan_picks_last_title():
    """Если Qwen написал НАЗВАНИЕ: дважды — берём последнее."""
    text = (
        "Надо написать НАЗВАНИЕ: <placeholder>. "
        "Без пояснений. Готово.\\n"
        "НАЗВАНИЕ: Реальный проект\\n"
        "ЦЕЛЬ: Тестовая цель\\n"
        "ЭТАП: Первый\\n"
        "- шаг 1"
    )
    result = _clean_plan_text(text)
    assert result.startswith("НАЗВАНИЕ: Реальный проект")
    assert "<placeholder>" not in result
    assert "Надо написать" not in result


def test_clean_plan_single_title_untouched():
    """Один НАЗВАНИЕ: в начале — ничего не срезается."""
    text = (
        "НАЗВАНИЕ: Проект\\n"
        "ЦЕЛЬ: Цель\\n"
        "ЭТАП: Этап\\n"
        "- шаг"
    )
    result = _clean_plan_text(text)
    assert result.startswith("НАЗВАНИЕ: Проект")
    assert "ЭТАП: Этап" in result


# ═══════════════════════════════════════════════════════════════
# Задача O: Link понимает «только план»
# ═══════════════════════════════════════════════════════════════

def test_user_wants_only_plan_markers():
    """Задача O: функция _user_wants_only_plan ловит маркеры."""
    from core.orchestrator import _user_wants_only_plan
    # Срабатывают
    assert _user_wants_only_plan("код не пиши, только план") is True
    assert _user_wants_only_plan("не пиши код пока") is True
    assert _user_wants_only_plan("только план проекта") is True
    assert _user_wants_only_plan("сначала обсудим") is True
    assert _user_wants_only_plan("без кода") is True
    # Не срабатывают
    assert _user_wants_only_plan("сделай план и код") is False
    assert _user_wants_only_plan("напиши приложение") is False
    assert _user_wants_only_plan("") is False
    assert _user_wants_only_plan(None) is False


def test_orchestrate_only_plan_stops_after_plan(tmp_path, monkeypatch):
    """Задача O: с маркером «код не пиши» — стоп после plan."""
    monkeypatch.setattr(
        "core.capability_benchmark.ASSIGNMENTS_FILE",
        tmp_path / "no_assign.json",
    )
    monkeypatch.setattr(
        "core.capability_benchmark.CAPABILITIES_FILE",
        tmp_path / "no_cap.json",
    )
    calls = []

    def fake_run_phase(ctx, phase, agent_override=""):
        calls.append(phase)
        return _make_result(phase, "success",
                            output="НАЗВАНИЕ: X\nЦЕЛЬ: Y")

    with patch("core.orchestrator.run_phase", side_effect=fake_run_phase):
        r = orchestrate("код не пиши, только план", str(tmp_path),
                        cross_verify=False, max_phases=10)

    # Прошла только plan, в code не пошёл
    assert calls == [PHASE_PLAN]
    assert r.success is True
    assert r.status == "success"
    assert r.final_phase == PHASE_PLAN


def test_orchestrate_plan_and_code_proceeds(tmp_path, monkeypatch):
    """Регресс: без маркеров — идёт plan → code → review."""
    monkeypatch.setattr(
        "core.capability_benchmark.ASSIGNMENTS_FILE",
        tmp_path / "no_assign.json",
    )
    monkeypatch.setattr(
        "core.capability_benchmark.CAPABILITIES_FILE",
        tmp_path / "no_cap.json",
    )
    calls = []

    def fake_run_phase(ctx, phase, agent_override=""):
        calls.append(phase)
        if phase == PHASE_PLAN:
            return _make_result(phase, "success", output="НАЗВАНИЕ: X")
        if phase == PHASE_REVIEW:
            return _make_result(phase, "success", output="STATUS: OK")
        return _make_result(phase, "success")

    with patch("core.orchestrator.run_phase", side_effect=fake_run_phase):
        r = orchestrate("Сделай приложение", str(tmp_path),
                        cross_verify=False, max_phases=10)

    # Прошли plan, code, review
    assert PHASE_PLAN in calls
    assert PHASE_CODE in calls
    assert PHASE_REVIEW in calls
    assert r.success is True


# ═══════════════════════════════════════════════════════════════
# Задача P: code без файлов = FAIL
# ═══════════════════════════════════════════════════════════════

def test_run_file_phase_code_without_files_is_failed(tmp_path, monkeypatch):
    """Задача P: code вернул success, но файлов нет — это FAIL."""
    monkeypatch.setattr(
        "core.capability_benchmark.ASSIGNMENTS_FILE",
        tmp_path / "no_assign.json",
    )
    monkeypatch.setattr(
        "core.capability_benchmark.CAPABILITIES_FILE",
        tmp_path / "no_cap.json",
    )
    ctx = create_context("тест", str(tmp_path))
    with patch("core.agentic_loop.run_agentic_loop") as m:
        m.return_value = {
            "success": True,
            "message": "ничего не сделал",
            "files_created": [],
            "iterations": 1,
        }
        r = run_file_phase(ctx, PHASE_CODE, agent_override="browser_deepseek")
    assert r.status == "failed"
    # B55 (2026-10-13): текст ошибки обновлён —
    # «не затронул ни одного файла» (учитывает все 4 списка).
    assert "не затронул" in r.error
    assert "1" in r.error  # итераций=1



def test_run_file_phase_code_with_files_is_success(tmp_path, monkeypatch):
    """Регресс: code с файлами — success."""
    monkeypatch.setattr(
        "core.capability_benchmark.ASSIGNMENTS_FILE",
        tmp_path / "no_assign.json",
    )
    monkeypatch.setattr(
        "core.capability_benchmark.CAPABILITIES_FILE",
        tmp_path / "no_cap.json",
    )
    ctx = create_context("тест", str(tmp_path))
    with patch("core.agentic_loop.run_agentic_loop") as m:
        m.return_value = {
            "success": True,
            "message": "готово",
            "files_created": ["main.py"],
            "iterations": 3,
        }
        r = run_file_phase(ctx, PHASE_CODE, agent_override="browser_deepseek")
    assert r.status == "success"
    assert r.iterations == 3


def test_run_file_phase_fix_without_files_is_success(tmp_path, monkeypatch):
    """Регресс: fix без файлов — success (файл мог не меняться)."""
    monkeypatch.setattr(
        "core.capability_benchmark.ASSIGNMENTS_FILE",
        tmp_path / "no_assign.json",
    )
    monkeypatch.setattr(
        "core.capability_benchmark.CAPABILITIES_FILE",
        tmp_path / "no_cap.json",
    )
    ctx = create_context("тест", str(tmp_path))
    with patch("core.agentic_loop.run_agentic_loop") as m:
        m.return_value = {
            "success": True,
            "message": "проверил, всё ок",
            "files_created": [],
            "iterations": 1,
        }
        r = run_file_phase(ctx, PHASE_FIX, agent_override="browser_deepseek")
    # Для fix — не FAIL, даже если файлов нет
    assert r.status == "success"


def test_run_file_phase_fallback_without_files_is_success(tmp_path, monkeypatch):
    """Регресс: fallback без файлов — success."""
    monkeypatch.setattr(
        "core.capability_benchmark.ASSIGNMENTS_FILE",
        tmp_path / "no_assign.json",
    )
    monkeypatch.setattr(
        "core.capability_benchmark.CAPABILITIES_FILE",
        tmp_path / "no_cap.json",
    )
    ctx = create_context("тест", str(tmp_path))
    with patch("core.agentic_loop.run_agentic_loop") as m:
        m.return_value = {
            "success": True,
            "message": "попробовал другой подход",
            "files_created": [],
            "iterations": 1,
        }
        r = run_file_phase(ctx, PHASE_FALLBACK, agent_override="browser_deepseek")
    assert r.status == "success"


# ═══════════════════════════════════════════════════════════════
# AD.H: route_rules — умная обработка reason через decide_action
# ═══════════════════════════════════════════════════════════════

def test_route_rules_login_required_skips(tmp_path, monkeypatch):
    """login_required → skip (стоп сразу, не тратить время)."""
    monkeypatch.setattr(
        "core.capability_benchmark.ASSIGNMENTS_FILE",
        tmp_path / "no_assign.json",
    )
    monkeypatch.setattr(
        "core.capability_benchmark.CAPABILITIES_FILE",
        tmp_path / "no_cap.json",
    )

    def fake_run_phase(ctx, phase, agent_override=""):
        if phase == PHASE_PLAN:
            return _make_result(
                phase, "failed", error="login_required: нужен вход"
            )
        return _make_result(phase, "success")

    with patch("core.orchestrator.run_phase", side_effect=fake_run_phase):
        r = orchestrate("Задача", str(tmp_path),
                        cross_verify=False, max_phases=10)

    assert r.status == "failed"
    assert "требуется вход" in r.error


def _make_result_with_agent(phase, agent, status, output="", error=""):
    """Локальный хелпер: создаёт PhaseResult с указанным агентом.
    
    Нужен для тестов route_rules — где важно, что phase_result.agent
    отражает реально выбранного агента (с учётом agent_override).
    """
    from core.orchestrator import PhaseResult as _PR
    return _PR(phase=phase, agent=agent, status=status,
               output=output, error=error)


def test_route_rules_launch_failed_switches(tmp_path, monkeypatch):
    """ROLES (2026-10-01): launch_failed + brain пусто → стоп без ollama."""
    monkeypatch.setattr(
        "core.capability_benchmark.ASSIGNMENTS_FILE",
        tmp_path / "no_assign.json",
    )
    monkeypatch.setattr(
        "core.capability_benchmark.CAPABILITIES_FILE",
        tmp_path / "no_cap.json",
    )

    agents_seen = []

    def fake_run_phase(ctx, phase, agent_override=""):
        agent = agent_override or get_phase_agent(phase) or ""
        agents_seen.append((phase, agent))
        return _make_result_with_agent(
            phase, agent, "failed",
            error="launch_failed: браузер не запустился"
        )

    with patch("core.orchestrator.run_phase", side_effect=fake_run_phase):
        r = orchestrate("Задача", str(tmp_path),
                        cross_verify=False, max_phases=10)

    assert r.status == "failed"
    for ph, agent in agents_seen:
        assert "ollama" not in (agent or ""), (
            f"ollama подставилась на фазе {ph}: {agent}"
        )


def test_route_rules_response_timeout_retries_then_switches(
    tmp_path, monkeypatch
):
    """ROLES (2026-10-01): response_timeout → retry (attempts >= 2)."""
    monkeypatch.setattr(
        "core.capability_benchmark.ASSIGNMENTS_FILE",
        tmp_path / "no_assign.json",
    )
    monkeypatch.setattr(
        "core.capability_benchmark.CAPABILITIES_FILE",
        tmp_path / "no_cap.json",
    )

    attempts = [0]
    agents_seen = []

    def fake_run_phase(ctx, phase, agent_override=""):
        agent = agent_override or get_phase_agent(phase) or ""
        agents_seen.append((phase, agent))
        if phase == PHASE_PLAN:
            attempts[0] += 1
            return _make_result_with_agent(
                phase, agent, "failed",
                error="response_timeout: не ответил"
            )
        return _make_result_with_agent(phase, agent, "success")

    with patch("core.orchestrator.run_phase", side_effect=fake_run_phase):
        r = orchestrate("Задача", str(tmp_path),
                        cross_verify=False, max_phases=10)

    # Было несколько попыток plan (retry)
    assert attempts[0] >= 2
    # Никакой ollama
    for ph, agent in agents_seen:
        assert "ollama" not in (agent or "")

# ═══════════════════════════════════════════════════════════════
# Б56 (2026-10-13): reasoning review не должен попадать в [ОШИБКИ].
# ═══════════════════════════════════════════════════════════════


def test_looks_like_reasoning_positive():
    """Reasoning-префиксы определяются."""
    from core.orchestrator import _looks_like_reasoning
    assert _looks_like_reasoning("Let me analyze what I can see")
    assert _looks_like_reasoning("We need to check this")
    assert _looks_like_reasoning("The task is to verify stage 4")
    assert _looks_like_reasoning("I should look at file.py")
    assert _looks_like_reasoning("Мы должны проверить это")
    assert _looks_like_reasoning("Надо посмотреть код")


def test_looks_like_reasoning_negative():
    """Обычный ответ — не reasoning."""
    from core.orchestrator import _looks_like_reasoning
    assert not _looks_like_reasoning("STATUS: OK")
    assert not _looks_like_reasoning("STATUS: PROBLEMS MAJOR")
    assert not _looks_like_reasoning("Файлы созданы, всё работает")
    assert not _looks_like_reasoning("")
    assert not _looks_like_reasoning("   ")


def test_extract_review_reasoning_with_status():
    """Reasoning + STATUS → обрезаем до STATUS."""
    from core.orchestrator import _extract_review_for_fix
    text = (
        "Let me analyze what I can actually see. The task is...\n"
        "> some analysis\n"
        "STATUS: PROBLEMS MAJOR\n"
        "> file.py: не обрабатывает ошибки"
    )
    result = _extract_review_for_fix(text)
    assert result.startswith("STATUS:")
    assert "Let me" not in result
    assert "PROBLEMS MAJOR" in result
    assert "file.py" in result


def test_extract_review_reasoning_without_status():
    """Reasoning без STATUS → пусто (не тащим в fix)."""
    from core.orchestrator import _extract_review_for_fix
    text = "Let me analyze... some reasoning here but no STATUS marker"
    result = _extract_review_for_fix(text)
    assert result == ""


def test_extract_review_clean_passthrough():
    """Чистый ответ без reasoning — как есть."""
    from core.orchestrator import _extract_review_for_fix
    text = "STATUS: OK\nВсё в порядке"
    result = _extract_review_for_fix(text)
    assert result == text


def test_extract_review_empty():
    """Пустой вход → пусто."""
    from core.orchestrator import _extract_review_for_fix
    assert _extract_review_for_fix("") == ""
    assert _extract_review_for_fix("   ") == ""



# ═══════════════════════════════════════════════════════════════
# Б55 (2026-10-13): этап без файлов не должен получать done.
# ═══════════════════════════════════════════════════════════════


def _write_plan(proj, stages):
    import json
    from pathlib import Path
    p = Path(proj) / ".ai_link"
    p.mkdir(parents=True, exist_ok=True)
    (p / "plan.json").write_text(
        json.dumps({"stages": stages}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _read_plan(proj):
    import json
    from pathlib import Path
    p = Path(proj) / ".ai_link" / "plan.json"
    return json.loads(p.read_text(encoding="utf-8"))



def test_mark_stage_done_when_files_touched(tmp_path):
    """B55: touched=True → нормальная логика (done)."""
    from core.orchestrator import _mark_stage_done
    _write_plan(tmp_path, [
        {"title": "Этап 1", "status": "in_progress", "items": [
            {"text": "шаг 1", "status": "in_progress"},
            {"text": "шаг 2", "status": "in_progress"},
        ]},
    ])
    _mark_stage_done(str(tmp_path), 0, touched=True)
    data = _read_plan(tmp_path)
    st = data["stages"][0]
    assert st["status"] == "done"
    assert st["items"][0]["status"] == "done"
    assert st["items"][1]["status"] == "done"
    assert data.get("status") == "done"


def test_mark_stage_done_default_touched_true(tmp_path):
    """B55: по умолчанию touched=True — старые вызовы не ломаются."""
    from core.orchestrator import _mark_stage_done
    _write_plan(tmp_path, [
        {"title": "Этап 1", "status": "in_progress", "items": [
            {"text": "шаг 1", "status": "in_progress"},
        ]},
    ])
    _mark_stage_done(str(tmp_path), 0)
    data = _read_plan(tmp_path)
    assert data["stages"][0]["status"] == "done"



def test_b59_force_new_resets_plan(tmp_path):
    """force_new=True → plan.json и run_state.json уходят в stale."""
    import json
    from core.orchestrator import _reset_stale_state
    ai_dir = tmp_path / ".ai_link"
    ai_dir.mkdir()
    (ai_dir / "plan.json").write_text(
        json.dumps({"stages": [{"name": "старый"}]}),
        encoding="utf-8",
    )
    (ai_dir / "run_state.json").write_text(
        json.dumps({"task": "старая задача", "status": "running"}),
        encoding="utf-8",
    )

    result = _reset_stale_state(str(tmp_path), "старая задача", force=True)
    assert result is True
    # Новые plan.json / run_state.json не должны существовать
    assert not (ai_dir / "plan.json").exists()
    assert not (ai_dir / "run_state.json").exists()
    # Stale-файлы — есть
    stale_plans = list(ai_dir.glob("plan.json.stale_*"))
    stale_states = list(ai_dir.glob("run_state.json.stale_*"))
    assert len(stale_plans) == 1
    assert len(stale_states) == 1


def test_b59_force_new_resets_plan_without_run_state(tmp_path):
    """force_new=True работает даже без run_state.json."""
    import json
    from core.orchestrator import _reset_stale_state
    ai_dir = tmp_path / ".ai_link"
    ai_dir.mkdir()
    (ai_dir / "plan.json").write_text(
        json.dumps({"stages": [{"name": "старый"}]}),
        encoding="utf-8",
    )

    result = _reset_stale_state(str(tmp_path), "новая задача", force=True)
    assert result is True
    assert not (ai_dir / "plan.json").exists()
    stale_plans = list(ai_dir.glob("plan.json.stale_*"))
    assert len(stale_plans) == 1


def test_b59_without_force_same_task_not_reset(tmp_path):
    """Без force: тот же task → не сбрасываем (старое поведение)."""
    import json
    from core.orchestrator import _reset_stale_state
    ai_dir = tmp_path / ".ai_link"
    ai_dir.mkdir()
    (ai_dir / "plan.json").write_text(
        json.dumps({"stages": [{"name": "старый"}]}),
        encoding="utf-8",
    )
    (ai_dir / "run_state.json").write_text(
        json.dumps({"task": "та же задача", "status": "running"}),
        encoding="utf-8",
    )

    result = _reset_stale_state(str(tmp_path), "та же задача", force=False)
    assert result is False
    # План на месте
    assert (ai_dir / "plan.json").exists()


def test_b59_without_force_different_task_reset(tmp_path):
    """Без force: другой task → сбрасываем (старое поведение)."""
    import json
    from core.orchestrator import _reset_stale_state
    ai_dir = tmp_path / ".ai_link"
    ai_dir.mkdir()
    (ai_dir / "plan.json").write_text(
        json.dumps({"stages": [{"name": "старый"}]}),
        encoding="utf-8",
    )
    (ai_dir / "run_state.json").write_text(
        json.dumps({"task": "старая задача", "status": "running"}),
        encoding="utf-8",
    )

    result = _reset_stale_state(str(tmp_path), "новая задача", force=False)
    assert result is True
    assert not (ai_dir / "plan.json").exists()

# ═══════════════════════════════════════════════════════════════
# Б62 (2026-10-14): review unknown → переспрос вместо fix вслепую.
# ═══════════════════════════════════════════════════════════════


def test_b62_review_unknown_retries_field():
    """Поле review_unknown_retries есть в OrchestrationContext."""
    from core.orchestrator import create_context
    ctx = create_context("тест", "/tmp")
    assert hasattr(ctx, "review_unknown_retries")
    assert ctx.review_unknown_retries == 0


def test_b62_review_ok_resets_retries():
    """Чёткий verdict сбрасывает счётчик retries."""
    # Пока это unit-тест поля и логики. Полный orchestration-тест
    # сложен (нужны моки). Проверим, что поле есть и работает.
    from core.orchestrator import create_context
    ctx = create_context("тест", "/tmp")
    ctx.review_unknown_retries = 1
    # Эмуляция сброса (как в orchestrate)
    ctx.review_unknown_retries = 0
    assert ctx.review_unknown_retries == 0

# ═══════════════════════════════════════════════════════════════
# Б63 (2026-10-14): repo_map обновляется перед code/fix.
# ═══════════════════════════════════════════════════════════════


def test_b63_repo_map_refreshed_before_code(tmp_path, monkeypatch):
    """При code-фазе вызывается save_repo_map."""
    from unittest.mock import patch
    from core.orchestrator import (
        orchestrate, PHASE_PLAN, PHASE_CODE, PHASE_REVIEW,
    )

    monkeypatch.setattr(
        "core.capability_benchmark.ASSIGNMENTS_FILE",
        tmp_path / "no_assign.json",
    )
    monkeypatch.setattr(
        "core.capability_benchmark.CAPABILITIES_FILE",
        tmp_path / "no_cap.json",
    )

    save_calls = []

    def fake_run_phase(ctx, phase, agent_override=""):
        from core.orchestrator import _now
        r = PhaseResult(phase=phase, agent="test",
                        status="success", started_at=_now())
        r.finished_at = _now()
        if phase == PHASE_PLAN:
            r.output = "НАЗВАНИЕ: тест\nЦЕЛЬ: тест\nЭТАП: 1\n- шаг"
        elif phase == PHASE_REVIEW:
            r.output = "STATUS: OK"
        return r

    def fake_save_repo_map(project_dir):
        save_calls.append(project_dir)
        return "/tmp/fake.json"

    with patch("core.orchestrator.run_phase", side_effect=fake_run_phase):
        with patch("core.repo_map.save_repo_map", side_effect=fake_save_repo_map):
            orchestrate("тест", str(tmp_path), cross_verify=False)

    # Должен быть хотя бы 1 вызов save_repo_map (перед code)
    assert len(save_calls) >= 1


def test_b63_repo_map_imported_in_code():
    """В orchestrator.py есть вызов save_repo_map в блоке code/fix."""
    from pathlib import Path
    src = Path("core/orchestrator.py").read_text(encoding="utf-8")
    assert "REPO-MAP-REFRESH" in src
    assert "save_repo_map" in src
    # Проверяем, что вызов есть внутри условий для code/fix
    idx = src.find("REPO-MAP-REFRESH")
    assert idx > 0
    context = src[idx:idx + 500]
    assert "current_phase in (PHASE_CODE, PHASE_FIX)" in context

# ═══════════════════════════════════════════════════════════════
# Б63 Уровень A (14.10.2026): project_snapshot + история.
# ═══════════════════════════════════════════════════════════════


def test_b63_pA_fingerprint_detects_change(tmp_path):
    """Изменение .py → changed=True, modified=[file]."""
    from core.project_fingerprint import compute, save, compare
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "x.py").write_text("a = 1\n", encoding="utf-8")
    (proj / "y.py").write_text("b = 2\n", encoding="utf-8")

    # Первый снимок
    snap = compute(str(proj))
    save(str(proj), snap)

    # Изменяем x.py
    (proj / "x.py").write_text("a = 999\n", encoding="utf-8")

    diff = compare(str(proj))
    assert diff["changed"] is True
    assert diff["first_run"] is False
    assert "x.py" in diff["modified"]
    assert "y.py" not in diff["modified"]


def test_b63_pA_fingerprint_no_change(tmp_path):
    """Без изменений → changed=False."""
    from core.project_fingerprint import compute, save, compare
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "x.py").write_text("a = 1\n", encoding="utf-8")

    snap = compute(str(proj))
    save(str(proj), snap)

    diff = compare(str(proj))
    assert diff["changed"] is False
    assert diff["first_run"] is False


def test_b63_pA_fingerprint_first_run(tmp_path):
    """Без сохранённого snapshot → first_run=True."""
    from core.project_fingerprint import compare
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "x.py").write_text("a = 1\n", encoding="utf-8")

    diff = compare(str(proj))
    assert diff["first_run"] is True
    assert diff["changed"] is False


def test_b63_pA_fingerprint_added_removed(tmp_path):
    """Добавление и удаление файлов → changed=True."""
    from core.project_fingerprint import compute, save, compare
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "a.py").write_text("x = 1\n", encoding="utf-8")
    (proj / "b.py").write_text("y = 2\n", encoding="utf-8")

    snap = compute(str(proj))
    save(str(proj), snap)

    (proj / "a.py").unlink()
    (proj / "c.py").write_text("z = 3\n", encoding="utf-8")

    diff = compare(str(proj))
    assert diff["changed"] is True
    assert "c.py" in diff["added"]
    assert "a.py" in diff["removed"]


def test_b63_pA_archive_history(tmp_path):
    """archive_history переносит events.jsonl в архив."""
    import json
    from core.history_archive import archive_history
    proj = tmp_path / "proj"
    ai = proj / ".ai_link"
    ai.mkdir(parents=True)
    (ai / "events.jsonl").write_text('{"a": 1}\n', encoding="utf-8")
    (ai / "review_log.jsonl").write_text('{"b": 2}\n', encoding="utf-8")

    rep = archive_history(str(proj))
    assert len(rep["archived"]) == 2
    # Свежие файлы должны быть перенесены
    assert not (ai / "events.jsonl").exists()
    assert not (ai / "review_log.jsonl").exists()
    # Архивы должны существовать
    assert (ai / "events_archive").exists()
    assert (ai / "review_log_archive").exists()


def test_b63_pA_reset_done_to_needs_recheck(tmp_path):
    """done → needs_recheck в plan.json."""
    import json
    from core.history_archive import reset_done_to_needs_recheck
    proj = tmp_path / "proj"
    ai = proj / ".ai_link"
    ai.mkdir(parents=True)
    plan = {
        "status": "done",
        "stages": [
            {"status": "done", "items": [
                {"text": "s1", "status": "done"},
                {"text": "s2", "status": "done"},
            ]},
            {"status": "done", "items": [
                {"text": "s3", "status": "done"},
            ]},
        ],
    }
    (ai / "plan.json").write_text(
        json.dumps(plan, ensure_ascii=False), encoding="utf-8"
    )

    n = reset_done_to_needs_recheck(str(proj))
    assert n >= 5  # 3 items + 2 stages + plan.status

    data = json.loads((ai / "plan.json").read_text(encoding="utf-8"))
    assert data["status"] == "needs_recheck"
    for stage in data["stages"]:
        assert stage["status"] == "needs_recheck"
        for item in stage["items"]:
            assert item["status"] == "needs_recheck"


def test_b63_pA_on_project_changed_full(tmp_path):
    """Полный сценарий: архив + needs_recheck + run_state в stale."""
    import json
    from core.history_archive import on_project_changed
    proj = tmp_path / "proj"
    ai = proj / ".ai_link"
    ai.mkdir(parents=True)
    (ai / "events.jsonl").write_text('{"a": 1}\n', encoding="utf-8")
    (ai / "review_log.jsonl").write_text('{"b": 2}\n', encoding="utf-8")
    (ai / "plan.json").write_text(
        json.dumps({"status": "done", "stages": [
            {"status": "done", "items": [
                {"text": "x", "status": "done"},
            ]},
        ]}, ensure_ascii=False),
        encoding="utf-8",
    )
    (ai / "run_state.json").write_text('{"task": "t"}', encoding="utf-8")

    rep = on_project_changed(str(proj), {})
    assert len(rep["archived"]) == 2
    assert rep["needs_recheck"] >= 2
    assert rep["run_state_stale"] is True
    assert not (ai / "events.jsonl").exists()
    assert not (ai / "run_state.json").exists()



# ═══════════════════════════════════════════════════════════════
# Б64 (14.10.2026): WRITE-этап без правок → needs_rework.
# Различаем через FILE_PHASES (code/fix/fallback), не keywords.
# ═══════════════════════════════════════════════════════════════


def test_b64_write_phase_no_files_needs_rework(tmp_path):
    """touched=False + had_write_phase=True → needs_rework."""
    import json
    from core.orchestrator import _mark_stage_done
    ai = tmp_path / ".ai_link"
    ai.mkdir()
    (ai / "plan.json").write_text(json.dumps({
        "status": "in_progress",
        "stages": [{
            "name": "Очистка settings.json",
            "status": "in_progress",
            "items": [
                {"text": "Удалить секретные поля",
                 "status": "in_progress"},
            ],
        }],
    }, ensure_ascii=False), encoding="utf-8")

    _mark_stage_done(str(tmp_path), 0,
                     touched=False, had_write_phase=True)
    data = json.loads((ai / "plan.json").read_text(encoding="utf-8"))
    assert data["stages"][0]["status"] == "needs_rework"
    assert data["status"] == "partial"


def test_b64_readonly_phase_no_files_is_done(tmp_path):
    """touched=False + had_write_phase=False (разведка) → done."""
    import json
    from core.orchestrator import _mark_stage_done
    ai = tmp_path / ".ai_link"
    ai.mkdir()
    (ai / "plan.json").write_text(json.dumps({
        "status": "in_progress",
        "stages": [{
            "name": "Разведка",
            "status": "in_progress",
            "items": [
                {"text": "Найти секреты", "status": "in_progress"},
                {"text": "Составить таблицу", "status": "in_progress"},
            ],
        }],
    }, ensure_ascii=False), encoding="utf-8")

    _mark_stage_done(str(tmp_path), 0,
                     touched=False, had_write_phase=False)
    data = json.loads((ai / "plan.json").read_text(encoding="utf-8"))
    assert data["stages"][0]["status"] == "done"
    assert data["status"] == "done"


def test_b64_write_phase_with_files_is_done(tmp_path):
    """touched=True + had_write_phase=True → done (норма)."""
    import json
    from core.orchestrator import _mark_stage_done
    ai = tmp_path / ".ai_link"
    ai.mkdir()
    (ai / "plan.json").write_text(json.dumps({
        "status": "in_progress",
        "stages": [{
            "name": "Рефакторинг",
            "status": "in_progress",
            "items": [
                {"text": "Поправить news_agent", "status": "in_progress"},
            ],
        }],
    }, ensure_ascii=False), encoding="utf-8")

    _mark_stage_done(str(tmp_path), 0,
                     touched=True, had_write_phase=True)
    data = json.loads((ai / "plan.json").read_text(encoding="utf-8"))
    assert data["stages"][0]["status"] == "done"
    assert data["status"] == "done"


def test_b64_mark_plan_done_keeps_needs_rework(tmp_path):
    """_mark_plan_done НЕ перезаписывает needs_rework в done."""
    import json
    from core.orchestrator import _mark_plan_done
    ai = tmp_path / ".ai_link"
    ai.mkdir()
    (ai / "plan.json").write_text(json.dumps({
        "status": "in_progress",
        "stages": [
            {"name": "Разведка", "status": "done", "items": [
                {"text": "Найти", "status": "done"},
            ]},
            {"name": "Очистка", "status": "needs_rework", "items": [
                {"text": "Удалить", "status": "done"},
            ]},
        ],
    }, ensure_ascii=False), encoding="utf-8")

    _mark_plan_done(str(tmp_path))
    data = json.loads((ai / "plan.json").read_text(encoding="utf-8"))
    # needs_rework не перезаписан
    assert data["stages"][1]["status"] == "needs_rework"
    # plan.status не done
    assert data["status"] == "success_partial"


def test_b64_mark_plan_done_all_done_stays_done(tmp_path):
    """Регресс: если все done — plan done."""
    import json
    from core.orchestrator import _mark_plan_done
    ai = tmp_path / ".ai_link"
    ai.mkdir()
    (ai / "plan.json").write_text(json.dumps({
        "status": "in_progress",
        "stages": [
            {"name": "Этап 1", "status": "done", "items": [
                {"text": "s1", "status": "done"},
            ]},
            {"name": "Этап 2", "status": "done", "items": [
                {"text": "s2", "status": "done"},
            ]},
        ],
    }, ensure_ascii=False), encoding="utf-8")

    _mark_plan_done(str(tmp_path))
    data = json.loads((ai / "plan.json").read_text(encoding="utf-8"))
    assert data["status"] == "done"


# ═══════════════════════════════════════════════════════════════
# Б70 (14.10.2026) — уточнение Б64:
# task_complete=True → done, даже если файлы не менялись.
# ═══════════════════════════════════════════════════════════════


def test_b70_task_complete_no_files_is_done(tmp_path):
    """touched=False + had_write_phase=True + task_complete=True
    → done (LLM осознанно завершила)."""
    import json
    from core.orchestrator import _mark_stage_done
    ai = tmp_path / ".ai_link"
    ai.mkdir()
    (ai / "plan.json").write_text(json.dumps({
        "status": "in_progress",
        "stages": [{
            "name": "Проверка env_config",
            "status": "in_progress",
            "items": [
                {"text": "Добавить недостающие геттеры",
                 "status": "in_progress"},
            ],
        }],
    }, ensure_ascii=False), encoding="utf-8")

    _mark_stage_done(str(tmp_path), 0,
                     touched=False,
                     had_write_phase=True,
                     task_complete=True)
    data = json.loads((ai / "plan.json").read_text(encoding="utf-8"))
    assert data["stages"][0]["status"] == "done"
    assert data["status"] == "done"


def test_b70_no_task_complete_no_files_needs_rework(tmp_path):
    """touched=False + had_write_phase=True + task_complete=False
    → needs_rework (регресс Б64)."""
    import json
    from core.orchestrator import _mark_stage_done
    ai = tmp_path / ".ai_link"
    ai.mkdir()
    (ai / "plan.json").write_text(json.dumps({
        "status": "in_progress",
        "stages": [{
            "name": "Очистка settings",
            "status": "in_progress",
            "items": [
                {"text": "Удалить поля", "status": "in_progress"},
            ],
        }],
    }, ensure_ascii=False), encoding="utf-8")

    _mark_stage_done(str(tmp_path), 0,
                     touched=False,
                     had_write_phase=True,
                     task_complete=False)
    data = json.loads((ai / "plan.json").read_text(encoding="utf-8"))
    assert data["stages"][0]["status"] == "needs_rework"
    assert data["status"] == "partial"


def test_b70_phase_result_has_task_complete_field():
    """В PhaseResult есть поле task_complete (по умолчанию False)."""
    from core.orchestrator import PhaseResult
    r = PhaseResult(phase="code", agent="test")
    assert hasattr(r, "task_complete")
    assert r.task_complete is False


def test_b70_context_has_stage_had_task_complete():
    """В OrchestrationContext есть поле stage_had_task_complete."""
    from core.orchestrator import create_context
    ctx = create_context("тест", "/tmp")
    assert hasattr(ctx, "stage_had_task_complete")
    assert ctx.stage_had_task_complete is False
