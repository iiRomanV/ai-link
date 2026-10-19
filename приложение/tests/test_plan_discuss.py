"""PLAN-DISCUSS (2026-10-08, Шаг 4): тесты чистых функций.

Покрываем:
- _parse_plan_text: буквы, дедупликация, срез [Х], очистка префиксов
- _strip_reasoning_lead: маркер «Ответ:» + fallback reasoning
- _stages_similar / _words: нечёткий матч имён этапов
- _mark_diff: in-place merge (removed на месте, added в конце)
- _plan_to_text / _history_to_text: сериализация
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.audit_planner import (
    _parse_plan_text, _plan_to_text, _history_to_text,
    _strip_reasoning_lead, _stages_similar, _words, _mark_diff,
)


# ============================================================
# _parse_plan_text
# ============================================================

def test_parse_basic():
    text = """НАЗВАНИЕ: Тест
ЦЕЛЬ: Проверка

ЭТАП: Первый
Делаем раз
Делаем два

ЭТАП: Второй
Пункт один
"""
    p = _parse_plan_text(text)
    assert p["title"] == "Тест"
    assert p["goal"] == "Проверка"
    assert len(p["stages"]) == 2
    assert p["stages"][0]["letter"] == "А"
    assert p["stages"][1]["letter"] == "Б"
    assert p["stages"][0]["items"][0]["num"] == 1
    assert p["stages"][0]["items"][1]["num"] == 2
    assert p["stages"][0]["items"][0]["text"] == "Делаем раз"


def test_parse_dedup_plan():
    """LLM пишет план дважды — берём второй."""
    text = """НАЗВАНИЕ: Первый
ЦЕЛЬ: A

ЭТАП: Этап 1
Пункт

НАЗВАНИЕ: Второй
ЦЕЛЬ: B

ЭТАП: Финальный
Финальный пункт
"""
    p = _parse_plan_text(text)
    assert p["title"] == "Второй"
    assert len(p["stages"]) == 1
    assert p["stages"][0]["name"] == "Финальный"


def test_parse_strip_bracket_letter():
    """Имя этапа с [А] — срезаем."""
    text = """НАЗВАНИЕ: Тест
ЭТАП: Удаление секрета [А]
Пункт

ЭТАП: Защита [Б]
Пункт 2
"""
    p = _parse_plan_text(text)
    assert p["stages"][0]["name"] == "Удаление секрета"
    assert p["stages"][1]["name"] == "Защита"


def test_parse_clean_item_prefixes():
    """Пункты с буквенно-цифровым префиксом — срезаем."""
    text = """НАЗВАНИЕ: Тест
ЭТАП: Один
А1. Делаем раз
Г2) Делаем два
Б10 - Делаем три
Просто пункт
"""
    p = _parse_plan_text(text)
    texts = [it["text"] for it in p["stages"][0]["items"]]
    assert texts == ["Делаем раз", "Делаем два", "Делаем три", "Просто пункт"]


def test_parse_empty_returns_defaults():
    p = _parse_plan_text("")
    assert p["title"] == "План доработки"
    assert p["stages"] == []
    assert p["status"] == "draft"


# ============================================================
# _strip_reasoning_lead
# ============================================================

def test_strip_marker_last():
    """Если «Ответ:» два раза — берём после последнего."""
    s = "Ответ: первый. Ответ: финальный."
    assert _strip_reasoning_lead(s) == "финальный."


def test_strip_marker_simple():
    s = "Reasoning: думаю...\n\nОтвет: Вот мой ответ."
    assert _strip_reasoning_lead(s) == "Вот мой ответ."


def test_strip_fallback_reasoning():
    s = "User says skip A1-A3. Respond briefly.\n\nПонял, этап А пропускаем."
    assert _strip_reasoning_lead(s) == "Понял, этап А пропускаем."


def test_strip_no_reasoning():
    s = "Просто нормальный ответ без reasoning."
    assert _strip_reasoning_lead(s) == "Просто нормальный ответ без reasoning."


def test_strip_multiple_reasoning_lines():
    s = ("User asks why. Answer briefly.\n"
         "We need to think about this.\n"
         "The user wants a short response.\n"
         "\n"
         "Реальный ответ.")
    assert _strip_reasoning_lead(s) == "Реальный ответ."


# ============================================================
# _words / _stages_similar
# ============================================================

def test_words_extract_significant():
    w = _words("Ротация и проверка секрета в проекте")
    assert "ротация" in w
    # стоп-слова отброшены
    assert "секрет" not in w
    assert "проект" not in w


def test_stages_similar_rename():
    """«Ротация и проверка» → «Ротация» — похожи."""
    assert _stages_similar("Ротация и проверка", "Ротация")


def test_stages_similar_different():
    assert not _stages_similar("Очистка истории", "Проверка результата")


def test_stages_similar_empty():
    assert not _stages_similar("", "Что-то")
    assert not _stages_similar("Этап", "")


# ============================================================
# _mark_diff — in-place merge
# ============================================================

def _plan(stages):
    return {"stages": stages}


def test_mark_diff_no_changes():
    old = _plan([
        {"name": "А", "items": [{"text": "п1"}, {"text": "п2"}]},
    ])
    new = _plan([
        {"name": "А", "items": [{"text": "п1"}, {"text": "п2"}]},
    ])
    stats = _mark_diff(old, new)
    assert stats == {"removed_stages": 0, "removed_items": 0,
                     "added_items": 0}
    assert new["stages"][0]["items"][0]["status"] == "pending"


def test_mark_diff_removed_stage_stays_in_place():
    """Убранный этап остаётся НА СВОЁМ МЕСТЕ."""
    old = _plan([
        {"name": "Первый", "items": [{"text": "а"}]},
        {"name": "Второй", "items": [{"text": "б"}]},
        {"name": "Третий", "items": [{"text": "в"}]},
    ])
    new = _plan([
        {"name": "Первый", "items": [{"text": "а"}]},
        {"name": "Третий", "items": [{"text": "в"}]},
    ])
    stats = _mark_diff(old, new)
    assert stats["removed_stages"] == 1
    stages = new["stages"]
    assert len(stages) == 3
    assert stages[1]["name"] == "Второй"
    assert stages[1]["status"] == "removed"
    assert stages[1]["items"][0]["status"] == "rejected"


def test_mark_diff_removed_item_stays():
    old = _plan([
        {"name": "А", "items": [{"text": "1"}, {"text": "2"}, {"text": "3"}]},
    ])
    new = _plan([
        {"name": "А", "items": [{"text": "1"}, {"text": "3"}]},
    ])
    stats = _mark_diff(old, new)
    assert stats["removed_items"] == 1
    items = new["stages"][0]["items"]
    assert [it["status"] for it in items] == ["pending", "rejected", "pending"]


def test_mark_diff_added_item():
    old = _plan([{"name": "А", "items": [{"text": "1"}]}])
    new = _plan([{"name": "А", "items": [{"text": "1"}, {"text": "новая"}]}])
    stats = _mark_diff(old, new)
    assert stats["added_items"] == 1
    items = new["stages"][0]["items"]
    assert items[0]["status"] == "pending"
    assert items[1]["status"] == "added"


def test_mark_diff_new_stage_at_end():
    old = _plan([{"name": "А", "items": [{"text": "1"}]}])
    new = _plan([
        {"name": "А", "items": [{"text": "1"}]},
        {"name": "Новый", "items": [{"text": "х"}, {"text": "у"}]},
    ])
    stats = _mark_diff(old, new)
    assert stats["added_items"] == 2
    assert new["stages"][1]["status"] == "added"
    assert new["stages"][1]["items"][0]["status"] == "added"


def test_mark_diff_rename_stage():
    """«Ротация и проверка» → «Ротация» — тот же этап, не удаление."""
    old = _plan([
        {"name": "Ротация и проверка",
         "items": [{"text": "Отозвать"}, {"text": "Проверить"}]},
    ])
    new = _plan([
        {"name": "Ротация",
         "items": [{"text": "Отозвать"}, {"text": "Проверить"}]},
    ])
    stats = _mark_diff(old, new)
    assert stats["removed_stages"] == 0
    assert stats["removed_items"] == 0
    assert len(new["stages"]) == 1
    assert new["stages"][0]["name"] == "Ротация"


def test_mark_diff_letters_renumbered():
    """После merge буквы пересобираются по порядку."""
    old = _plan([
        {"name": "Первый", "items": [{"text": "a"}]},
        {"name": "Второй", "items": [{"text": "b"}]},
    ])
    new = _plan([
        {"name": "Первый", "items": [{"text": "a"}]},
        {"name": "Второй", "items": [{"text": "b"}]},
        {"name": "Третий", "items": [{"text": "c"}]},
    ])
    _mark_diff(old, new)
    letters = [s["letter"] for s in new["stages"]]
    assert letters == ["А", "Б", "В"]
    # num пересобраны
    assert new["stages"][2]["items"][0]["num"] == 1


# ============================================================
# _plan_to_text / _history_to_text
# ============================================================

def test_plan_to_text_no_letters_in_items():
    plan = {
        "title": "T", "goal": "G",
        "stages": [{
            "letter": "А", "name": "Этап",
            "items": [
                {"num": 1, "text": "А1. Мусор"},
                {"num": 2, "text": "чистый"},
            ],
        }],
    }
    txt = _plan_to_text(plan)
    assert "А1." not in txt
    assert "- Мусор" in txt
    assert "- чистый" in txt


def test_plan_to_text_strips_bracket_from_name():
    plan = {
        "title": "T", "goal": "G",
        "stages": [{"letter": "А", "name": "Удаление [А]", "items": []}],
    }
    txt = _plan_to_text(plan)
    assert "[А]" not in txt
    assert "ЭТАП: Удаление" in txt


def test_history_to_text():
    h = [
        {"role": "user", "text": "привет"},
        {"role": "llm", "text": "здравствуй"},
    ]
    txt = _history_to_text(h)
    assert "ПОЛЬЗОВАТЕЛЬ: привет" in txt
    assert "LLM: здравствуй" in txt
