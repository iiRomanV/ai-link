"""B+ (19.10.2026): фильтр reasoning в browser_manager.

Проблема (прогон 19.10): DeepSeek сначала показывает «Thinking»
(17 байт), потом через 5-12 секунд дописывает JSON (939 байт).
Link брал «Thinking» как ответ → research_notes.json с targets=[].

Фикс (Forge-логика):
  • _is_reasoning_only(text) — «Thinking» / «Thought for» — не ответ.
  • _is_generating(page) — пока Stop / loader видимы — генерация идёт.
  • В цикле: если _is_generating — сброс стабильности, ждать.
  • В _new_candidates: reasoning — не кандидат.
"""
from core.agents.browser_manager import _is_reasoning_only


# ─── _is_reasoning_only ────────────────────────────────────────

def test_thinking_is_reasoning():
    assert _is_reasoning_only("Thinking") is True
    assert _is_reasoning_only("Thinking\nThinking") is True
    assert _is_reasoning_only("thinking") is True


def test_thought_for_is_reasoning():
    assert _is_reasoning_only("Thought for 7 seconds") is True
    assert _is_reasoning_only("Thought for 15 seconds\n\nLet me...") is True


def test_russian_reasoning():
    assert _is_reasoning_only("Нужно составить план") is True
    assert _is_reasoning_only("Мы должны проверить") is True


def test_empty_is_reasoning():
    assert _is_reasoning_only("") is True
    assert _is_reasoning_only("   ") is True
    assert _is_reasoning_only(None) is True


def test_json_is_not_reasoning():
    assert _is_reasoning_only('{"targets": []}') is False
    assert _is_reasoning_only('{"targets": [{"id":"P0-1"}]}') is False


def test_status_is_not_reasoning():
    assert _is_reasoning_only("STATUS: OK") is False
    assert _is_reasoning_only("STATUS: PROBLEMS\nMAJOR: ...") is False


def test_text_with_targets_inside_is_not_reasoning():
    """Если reasoning-префикс, но внутри JSON — это ответ."""
    assert _is_reasoning_only(
        'The user wants... {"targets": [{"id": "P0-1"}]}'
    ) is False


def test_plain_text_is_not_reasoning():
    assert _is_reasoning_only("Просто текст ответа") is False
    assert _is_reasoning_only("Это результат работы") is False


def test_thought_with_json_inside_is_not_reasoning():
    """«Thought for 7s ... {"targets": [...]}» — это ответ."""
    assert _is_reasoning_only(
        'Thought for 7 seconds\n\n{"targets": []}'
    ) is False
