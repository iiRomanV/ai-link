"""Общие фикстуры для тестов AI Link.

TGUI: автоматически мокаем QMessageBox, чтобы тесты не висели
в ожидании нажатия «ОК». Работает для всех тестов (autouse).
"""
import pytest


# AI Link: добавляем корень проекта в sys.path для импортов.
# Это заменяет старые sys.path.insert() в каждом тесте.
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).parent.parent))

@pytest.fixture(autouse=True)
def _mock_message_boxes(monkeypatch):
    """Мокает все модальные окна QMessageBox на no-op.

    Без этого тесты, вызывающие UI-методы (например,
    DialogTab._on_retry без project_dir), показывают окно
    и ждут нажатия — автопрогон висит.
    """
    try:
        from PySide6.QtWidgets import QMessageBox
    except Exception:
        yield
        return

    def _noop(*args, **kwargs):
        # Для question() возвращаем No — безопасный дефолт.
        return QMessageBox.No

    monkeypatch.setattr(QMessageBox, "information", _noop)
    monkeypatch.setattr(QMessageBox, "warning", _noop)
    monkeypatch.setattr(QMessageBox, "critical", _noop)
    monkeypatch.setattr(QMessageBox, "question", _noop)

    yield


# ═══════════════════════════════════════════════════════════════
#  FIX 2026-09-24: блокировка реальных LLM-вызовов в тестах
# ═══════════════════════════════════════════════════════════════
#  Причина: тесты, забывшие set_test_llm_stub/set_test_logic_stub,
#  дёргали реальный Qwen/DeepSeek через браузер.
#  Autouse-фикстура ставит заглушки по умолчанию для ВСЕХ тестов.
#  Если тест хочет свой мок — он перезапишет (set_test_llm_stub).


@pytest.fixture(autouse=True)
def _block_real_llm_calls():
    """Не даём тестам лезть в реальный LLM и браузер."""
    try:
        from core import audit_project as ap
    except Exception:
        yield
        return

    # Заглушки по умолчанию — если тест не поставил свои
    default_llm = lambda agent, prompt: {
        "success": True, "text": "[]", "error": None}
    default_logic = lambda d, mode, agent: {
        "success": True, "issues": [], "analyzed_files": [],
        "errors": [], "skipped_secrets": [], "total": 0}

    had_llm = ap._TEST_LLM_STUB
    had_logic = ap._TEST_LOGIC_STUB

    if had_llm is None:
        ap.set_test_llm_stub(default_llm)
    if had_logic is None:
        ap.set_test_logic_stub(default_logic)

    yield

    # Восстанавливаем как было
    ap._TEST_LLM_STUB = had_llm
    ap._TEST_LOGIC_STUB = had_logic


# ═══════════════════════════════════════════════════════════════
#  qapp: единый QApplication на всю сессию тестов
# ═══════════════════════════════════════════════════════════════
#  Нужен для GUI-тестов. Без него PySide6 segfault'ит, если
#  QApplication создаётся впервые внутри теста с виджетами.


@pytest.fixture(scope="session")
def qapp():
    """Создаёт QApplication один раз на сессию."""
    try:
        from PySide6.QtWidgets import QApplication
    except Exception:
        yield None
        return
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


# ═══════════════════════════════════════════════════════════════
#  FIX 2026-09-25: блокировка реального pip-audit в тестах
# ═══════════════════════════════════════════════════════════════
#  Причина: pip-audit лезет в OSV.dev (сеть) → 10-12 сек/тест.
#  Autouse-заглушка: тесты не звонят в сеть.
#  Реальная проверка доступна через явный вызов в проде.

@pytest.fixture(autouse=True)
def _block_real_pip_audit(monkeypatch):
    """Не даём тестам реально звать pip-audit."""
    try:
        from core import audit_project as ap
    except Exception:
        yield
        return
    # Мокаем проверку доступности — считаем, что pip-audit нет
    # (быстрая ветка: available=False)
    monkeypatch.setattr(
        ap, "_pip_audit_available", lambda: False)
    yield


# ═══════════════════════════════════════════════════════════════
#  ISOLATE-DB (2026-10-02): тесты НЕ чистят продакшн-БД
# ═══════════════════════════════════════════════════════════════
#  Причина: тесты test_auto_lessons.py (и ещё 5 файлов) вызывали
#  _clean(get_db_path()) — а get_db_path возвращал продакшн.
#  Каждый pytest удалял все auto_fix паттерны из ~/.ai_link/ai_link.db.
#  Результат: 183 сироты в pattern_evidence.
#
#  Фикс: подменяем AI_LINK_DB_PATH через monkeypatch + изолируем
#  каждую БД в tmp_path. Продакшн остаётся нетронутым.

@pytest.fixture(autouse=True)
def _isolate_db(monkeypatch, tmp_path):
    """ISOLATE-DB (2026-10-02): БД изолирована в tmp_path."""
    test_db = tmp_path / "ai_link_test.db"
    monkeypatch.setenv("AI_LINK_DB_PATH", str(test_db))
    yield
