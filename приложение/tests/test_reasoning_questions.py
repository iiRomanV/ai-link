"""REASONING-QUESTIONS (2026-10-02): reasoning не должен
классифицироваться как kind='questions'.

Оригинальная логика _looks_like_questions требует:
  • длина >= 80
  • >= 2 знаков вопроса
  • >= 2 маркеров уточнения
"""

from core.response_validator import _looks_like_questions


# ── Reasoning: 2+ ? + 2+ маркеров, НО начинается с reasoning ──

def test_thought_for_returns_false():
    """Reasoning из лога: 'Thought for N seconds' + вопросы внутри."""
    t = ("Thought for 5 seconds. The user asks which option is better. "
         "Should I use CSV? Please clarify the format?")
    assert _looks_like_questions(t) is False


def test_tool_calls_failing_returns_false():
    """Реальный кейс из прогона 01.10."""
    t = ("Thought for 2 seconds. The tool calls keep failing validation. "
         "Should I try again? Which option should I use?")
    assert _looks_like_questions(t) is False


def test_we_need_returns_false():
    t = ("We need to decide. Should I use option A or B? "
         "Which option is better? Please confirm the approach.")
    assert _looks_like_questions(t) is False


def test_let_me_think_returns_false():
    t = ("Let me think. The user asks about the format. "
         "Should I use CSV? Please clarify which option to pick?")
    assert _looks_like_questions(t) is False


def test_russian_reasoning_returns_false():
    t = ("Мы должны проверить. Какой формат использовать? "
         "Какой лучше для этой задачи? Это важно для работы.")
    assert _looks_like_questions(t) is False


# ── Нормальный вопрос модели: 80+ симв, 2+ ?, 2+ маркеров ──

def test_real_question_returns_true():
    t = ("Уточните, пожалуйста, какой формат использовать? "
         "CSV, JSON или XML? Это важно для дальнейшей работы.")
    assert _looks_like_questions(t) is True


def test_real_question_with_which_option():
    t = ("Пожалуйста, уточните задачу. Какой вариант использовать? "
         "Which option do you prefer? Это критично для проекта.")
    assert _looks_like_questions(t) is True


# ── Оригинальное поведение (не должно меняться) ──

def test_empty_returns_false():
    assert _looks_like_questions("") is False
    assert _looks_like_questions(None) is False


def test_short_question_returns_false():
    """Оригинал требует >= 80 символов — короткий вопрос → False."""
    t = "Какой файл менять?"
    assert _looks_like_questions(t) is False


def test_one_question_mark_returns_false():
    """Оригинал требует >= 2 знаков вопроса."""
    t = ("Уточните, пожалуйста, какой формат использовать для работы? "
         "Это важно для дальнейшей работы над проектом.")
    # 1 знак вопроса
    assert _looks_like_questions(t) is False
