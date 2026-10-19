"""CHAT-LIMIT-REASONING (2026-10-02): reasoning не должен
триггерить chat_limit."""

from core.agents.browser_manager import _detect_chat_limit


def test_reasoning_with_problem_with_returns_false():
    """Reasoning с 'problem with' → НЕ chat_limit."""
    t = ("Thought for 3 seconds\n\nLet me review the visible code. "
         "There is a problem with the statistics logic. "
         "The stage-4 sp...")
    assert _detect_chat_limit(t) is False


def test_thinking_with_internal_error_returns_false():
    """Reasoning с 'internal error' → НЕ chat_limit."""
    t = ("Thinking...\n\nI see an internal error somewhere. "
         "Let me check the code again.")
    assert _detect_chat_limit(t) is False


def test_we_need_with_oops_returns_false():
    t = "We need to check. Oops! There is an issue."
    assert _detect_chat_limit(t) is False


def test_real_chat_limit_returns_true():
    """Настоящее переполнение чата → chat_limit."""
    t = "Начните новый чат, чтобы продолжить."
    assert _detect_chat_limit(t) is True


def test_real_context_limit_returns_true():
    t = "Maximum context length reached. Please start a new chat."
    assert _detect_chat_limit(t) is True


def test_internal_error_without_reasoning_returns_true():
    """'internal error' в чистом ответе → chat_limit."""
    t = "Internal error occurred while processing your request."
    assert _detect_chat_limit(t) is True


def test_empty_returns_false():
    assert _detect_chat_limit("") is False
    assert _detect_chat_limit(None) is False
