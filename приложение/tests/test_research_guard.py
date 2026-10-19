"""B+ (19.10.2026): защита research от мусорного ответа.

Проблема (прогон 19.10): DeepSeek вернул 17 символов "Thinking"
и остановился. run_text_phase счёл success, research_notes
сохранился с targets=[]. Acceptance gate не сработал.

Фикс:
  • _research_has_content(text) — проверяет len>=10 и "targets".
  • run_text_phase для PHASE_RESEARCH: если нет content →
    status="failed" (не success).
"""
from core.orchestrator import _research_has_content


def test_thinking_is_rejected():
    assert _research_has_content("Thinking\nThinking") is False
    assert _research_has_content("Thinking") is False


def test_empty_is_rejected():
    assert _research_has_content("") is False
    assert _research_has_content(None) is False


def test_short_garbage_rejected():
    assert _research_has_content("abc") is False
    assert _research_has_content("{}") is False
    assert _research_has_content("   \n\n   ") is False


def test_valid_empty_targets_accepted():
    """{"targets": []} — валидный ответ (новый проект без P0)."""
    assert _research_has_content('{"targets": []}') is True


def test_valid_with_targets_accepted():
    assert _research_has_content(
        '{"targets": [{"id":"P0-1"}]}'
    ) is True


def test_valid_with_wrapping_text_accepted():
    """JSON внутри текста — тоже валидно."""
    assert _research_has_content(
        'Вот результат: {"targets": []} конец'
    ) is True


def test_thinking_about_targets_without_quotes_rejected():
    """'Thinking about targets' — есть слово, но НЕТ кавычек.

    Функция ищет '"targets"' (с кавычками). Без кавычек — False.
    Это правильно: LLM должна вернуть JSON-ключ, а не слово в тексте.
    """
    assert _research_has_content("Thinking about targets") is False


def test_thinking_about_targets_with_quotes_accepted():
    """Если есть '"targets"' (с кавычками) — True, даже без {}.

    Парсер потом вернёт 0 targets (JSON невалидный), но research
    не будет считаться failed. Это ок: LLM хотя бы пыталась
    вернуть JSON. А пустой targets → acceptance gate unknown.
    """
    assert _research_has_content('Thinking about "targets"') is True
