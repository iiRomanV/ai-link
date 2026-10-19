"""Тесты для core.agents.browser_manager.

Фокус: фильтр UI-шума Qwen (_is_qwen_ui_noise + _QWEN_UI_EXACT).
FIX BUG-DOM-AUTO-V2 (2026-09-28): «Авто», «Модель не выбрана»,
«Пропустить» — постоянные UI-элементы Qwen, ловятся селектором
[class*='message'] и могут перебить реальный ответ.

ВАЖНО: функция использует точное совпадение (frozenset), а не
подстроку — иначе «автоматически», «автоматизация» тоже отсеются.
"""

from core.agents.browser_manager import (
    _is_qwen_ui_noise,
    _QWEN_UI_EXACT,
)


# --------------------------------------------------------------------------- #
# _QWEN_UI_EXACT — сам набор
# --------------------------------------------------------------------------- #
def test_exact_is_frozenset():
    """_QWEN_UI_EXACT — frozenset (immutable, для in-проверки)."""
    assert isinstance(_QWEN_UI_EXACT, frozenset)


def test_exact_contains_expected():
    """Содержит 3 известные UI-плашки Qwen."""
    assert "авто" in _QWEN_UI_EXACT
    assert "модель не выбрана" in _QWEN_UI_EXACT
    assert "пропустить" in _QWEN_UI_EXACT


def test_exact_does_not_contain_common_words():
    """Не содержит опасных подстрок (иначе отсеет реальные ответы)."""
    assert "автоматически" not in _QWEN_UI_EXACT
    assert "автоматизация" not in _QWEN_UI_EXACT
    assert "ok" not in _QWEN_UI_EXACT


# --------------------------------------------------------------------------- #
# _is_qwen_ui_noise — точные совпадения (FIX BUG-DOM-AUTO-V2)
# --------------------------------------------------------------------------- #
def test_ui_noise_avto():
    """«Авто» — кнопка режима Qwen → UI-шум."""
    assert _is_qwen_ui_noise("Авто") is True


def test_ui_noise_model_not_selected():
    """«Модель не выбрана» — плашка Qwen → UI-шум."""
    assert _is_qwen_ui_noise("Модель не выбрана") is True


def test_ui_noise_skip():
    """«Пропустить» — кнопка Skip во время стрима → UI-шум."""
    assert _is_qwen_ui_noise("Пропустить") is True


def test_ui_noise_case_insensitive():
    """Регистр не важен: «АВТО» / «авто» / «АвТо»."""
    assert _is_qwen_ui_noise("АВТО") is True
    assert _is_qwen_ui_noise("авто") is True
    assert _is_qwen_ui_noise("АвТо") is True


def test_ui_noise_strip_whitespace():
    """Лишние пробелы/переносы по краям — отсекаются."""
    assert _is_qwen_ui_noise("  Авто  ") is True
    assert _is_qwen_ui_noise("\nПропустить\n") is True
    assert _is_qwen_ui_noise(" Модель не выбрана ") is True


# --------------------------------------------------------------------------- #
# _is_qwen_ui_noise — НЕ должно отсеивать реальные ответы
# --------------------------------------------------------------------------- #
def test_ui_noise_does_not_filter_avtomaticheski():
    """КРИТИЧНО: «автоматически» НЕ UI-шум.

    Если использовать подстроку вместо точного совпадения —
    реальные ответы с «автоматически» будут отсеяны.
    """
    assert _is_qwen_ui_noise("автоматически") is False
    assert _is_qwen_ui_noise("Автоматизация процессов") is False
    assert _is_qwen_ui_noise("Автозапуск при старте") is False


def test_ui_noise_does_not_filter_real_response():
    """Реальный ответ STATUS: ... — не UI-шум."""
    assert _is_qwen_ui_noise("STATUS: OK") is False
    assert _is_qwen_ui_noise("STATUS: PROBLEMS\nCRITICAL: ...") is False


def test_ui_noise_does_not_filter_plain_text():
    """Обычный текст — не UI-шум."""
    assert _is_qwen_ui_noise("НАЗВАНИЕ: Smoke Tracker") is False
    assert _is_qwen_ui_noise("Продолжаю работу над этапом 3") is False


# --------------------------------------------------------------------------- #
# _is_qwen_ui_noise — старые маркеры (подстрока)
# --------------------------------------------------------------------------- #
def test_ui_noise_old_marker_substring():
    """Старые маркеры работают по подстроке (без изменений)."""
    assert _is_qwen_ui_noise(
        "Этот отзыв поможет нам оценить качество ответа"
    ) is True
    assert _is_qwen_ui_noise(
        "Какой ответ вы предпочитаете?"
    ) is True


# --------------------------------------------------------------------------- #
# _is_qwen_ui_noise — краевые случаи
# --------------------------------------------------------------------------- #
def test_ui_noise_empty_string():
    """Пустая строка → False (не UI-шум)."""
    assert _is_qwen_ui_noise("") is False


def test_ui_noise_none():
    """None → False (не падает)."""
    assert _is_qwen_ui_noise(None) is False


def test_ui_noise_whitespace_only():
    """Только пробелы → False (после strip — пусто)."""
    assert _is_qwen_ui_noise("   ") is False
    assert _is_qwen_ui_noise("\n\t") is False


# --------------------------------------------------------------------------- #
# Дополнительные UI-плашки (из логов 28.09)
# --------------------------------------------------------------------------- #
def test_ui_noise_add_files():
    """«Добавить файлы» — кнопка загрузки Qwen."""
    assert _is_qwen_ui_noise("Добавить файлы") is True
    assert _is_qwen_ui_noise("добавить файлы") is True
    assert "добавить файлы" in _QWEN_UI_EXACT


def test_ui_noise_edit():
    """«Редактировать» — кнопка редактирования Qwen."""
    assert _is_qwen_ui_noise("Редактировать") is True
    assert "редактировать" in _QWEN_UI_EXACT


def test_ui_noise_init_empty():
    """«Инициализировать пустой» — кнопка быстрого действия Qwen."""
    assert _is_qwen_ui_noise("Инициализировать пустой") is True
    assert "инициализировать пустой" in _QWEN_UI_EXACT


def test_ui_noise_does_not_filter_real_truncated():
    """КРИТИЧНО: обрывок реального ответа НЕ UI-шум."""
    # «Создать папку tests с» — обрывок плана, не кнопка
    assert _is_qwen_ui_noise("Создать папку tests с") is False
    assert "создать папку" not in _QWEN_UI_EXACT
