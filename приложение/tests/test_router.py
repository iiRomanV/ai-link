"""Тесты классификации задач (router.py).

Проверяем три класса: dialog / project / continue.
Особое внимание — задаче T (осознанное продолжение).
"""

from core.router import classify_task


# === dialog ===

def test_dialog_simple_question():
    assert classify_task("что такое Python?") == "dialog"


def test_dialog_explain():
    assert classify_task("объясни, как работает цикл") == "dialog"


def test_dialog_short():
    assert classify_task("привет") == "dialog"


def test_dialog_empty():
    assert classify_task("") == "dialog"
    assert classify_task("   ") == "dialog"
    assert classify_task(None) == "dialog"


# === project ===

def test_project_create_file():
    assert classify_task("создай файл test.py") == "project"


def test_project_make_app():
    assert classify_task("сделай приложение для заметок") == "project"


def test_project_short_file_noun():
    # «файл настроек» — короткая команда без глагола
    assert classify_task("файл настроек") == "project"


# === continue (задача T) ===

def test_continue_prodolzhim():
    assert classify_task("продолжим") == "continue"
    assert classify_task("продолжай") == "continue"


def test_continue_teper_kod():
    assert classify_task("теперь пиши код") == "continue"
    assert classify_task("теперь код") == "continue"


def test_continue_pishi_kod():
    assert classify_task("пиши код") == "continue"
    assert classify_task("пиши-ка код") == "continue"


def test_continue_davai_kod():
    assert classify_task("давай код") == "continue"
    assert classify_task("давай писать") == "continue"


def test_continue_proverka():
    assert classify_task("проверь") == "continue"
    assert classify_task("проверка") == "continue"


def test_continue_isprav():
    assert classify_task("исправь ошибки") == "continue"
    assert classify_task("почини") == "continue"


def test_continue_stop():
    assert classify_task("останови") == "continue"
    assert classify_task("стоп") == "continue"


def test_continue_dalshe():
    assert classify_task("дальше") == "continue"
    assert classify_task("что дальше") == "continue"


# === защита от ложных срабатываний ===

def test_no_false_continue_in_question():
    # «расскажи про код» — не продолжение, а диалог
    assert classify_task("расскажи про код") == "dialog"


def test_no_false_continue_in_new_task():
    # «создай код для задачи» — не продолжение, а новая задача
    assert classify_task("создай код для задачи") == "project"
