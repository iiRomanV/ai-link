"""PROMPT-REVIEW-FIX (2026-10-02): review-промпт — шаблон + явно про REVIEW."""
import pytest


def _ctx():
    from core.orchestrator import create_context
    return create_context("Доработай smoke_tracker: добавь кнопку Сброс.", "/tmp/t")


def test_plan_prompt_unchanged():
    """Plan-промпт не тронут — содержит свой шаблон."""
    from core.orchestrator import _build_text_prompt, PHASE_PLAN
    p = _build_text_prompt(_ctx(), PHASE_PLAN)
    assert "Составь ПЛАН ПРОЕКТА" in p
    assert "ФОРМАТ ОТВЕТА — СТРОГО ТАКОЙ" in p
    assert "ОБЯЗАТЕЛЬНЫЙ ФОРМАТ" not in p


def test_review_prompt_has_template():
    """Review-промпт — теперь шаблон, как plan."""
    from core.orchestrator import _build_text_prompt, PHASE_REVIEW
    p = _build_text_prompt(_ctx(), PHASE_REVIEW)
    assert "ФОРМАТ ОТВЕТА — СТРОГО ТАКОЙ" in p
    assert "ОБЯЗАТЕЛЬНЫЙ ФОРМАТ" not in p


def test_review_prompt_has_variant_a():
    """Review-промпт содержит ВАРИАНТ A (проблем нет)."""
    from core.orchestrator import _build_text_prompt, PHASE_REVIEW
    p = _build_text_prompt(_ctx(), PHASE_REVIEW)
    assert "ВАРИАНТ A" in p
    assert "STATUS: OK" in p


def test_review_prompt_has_variant_b():
    """Review-промпт содержит ВАРИАНТ B (проблемы)."""
    from core.orchestrator import _build_text_prompt, PHASE_REVIEW
    p = _build_text_prompt(_ctx(), PHASE_REVIEW)
    assert "ВАРИАНТ B" in p
    assert "STATUS: PROBLEMS" in p


def test_review_prompt_start_with_status():
    """Review-промпт требует начать ответ с STATUS:."""
    from core.orchestrator import _build_text_prompt, PHASE_REVIEW
    p = _build_text_prompt(_ctx(), PHASE_REVIEW)
    assert "НАЧНИ ОТВЕТ РОВНО С" in p
    assert "Ты ПРОВЕРЯЕШЬ, а не планируешь" in p


def test_review_prompt_treats_qwen_stage():
    """PROMPT-REVIEW-FIX-2: общая защита — даже без stages."""
    from core.orchestrator import _build_text_prompt, PHASE_REVIEW
    p = _build_text_prompt(_ctx(), PHASE_REVIEW)
    # FIX-2: фраза всегда в промпте (в конце)
    assert "Ты работаешь в фазе REVIEW" in p
    assert "Это НЕ фаза PLAN" in p
    assert "НЕ пиши план" in p
    assert 'НЕ пиши "НАЗВАНИЕ:/ЭТАП:"' in p


def test_review_prompt_forbids_plan_format():
    """PROMPT-REVIEW-FIX-2: явный запрет на НАЗВАНИЕ/ЭТАП."""
    from core.orchestrator import _build_text_prompt, PHASE_REVIEW
    p = _build_text_prompt(_ctx(), PHASE_REVIEW)
    assert "НАЗВАНИЕ:/ЭТАП:" in p


def test_stage_info_does_not_have_strict_review_phrase():
    """PROMPT-REVIEW-FIX-2: _stage_info упрощён — нет дубля фразы про REVIEW."""
    from core.orchestrator import _build_text_prompt, PHASE_REVIEW
    p = _build_text_prompt(_ctx(), PHASE_REVIEW)
    # Фраза «Это фаза REVIEW» встречается РОВНО ОДИН РАЗ (в конце)
    assert p.count("Это НЕ фаза PLAN") == 1
