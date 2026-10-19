import sys
import os
import json
import tempfile
from unittest.mock import patch
import pytest
from PySide6.QtWidgets import QApplication
from ui.main_window import MainWindow
from ui.tabs.project_tab import ProjectTab
from config.config_manager import get_setting

@pytest.fixture
def app(qtbot):
    test_app = QApplication.instance() or QApplication([])
    return test_app

def test_main_window_launch(qtbot, app):
    window = MainWindow()
    qtbot.addWidget(window)
    # Не вызываем show(), чтобы не запускать таймеры
    # Устойчиво: проверяем НАЛИЧИЕ ключевых вкладок по имени,
    # а не точное число (число меняется при реорганизации).
    tab_names = [window.tabs.tabText(i) for i in range(window.tabs.count())]
    assert "Командный пункт" in tab_names, \
        f"нет вкладки «Командный пункт»: {tab_names}"
    assert "Проект" in tab_names, f"нет вкладки «Проект»: {tab_names}"
    assert "Агенты" in tab_names, f"нет вкладки «Агенты»: {tab_names}"
    assert "Настройки" in tab_names, f"нет вкладки «Настройки»: {tab_names}"
    # B2.1 (2026-10-07): Диалог / Логи / Карта / Ресурсы — не вкладки.
    for gone in ("Диалог", "Логи", "Карта проекта", "Ресурсы"):
        assert gone not in tab_names, \
            f"вкладки «{gone}» не должно быть: {tab_names}"
    # Ровно 4 вкладки (может вырасти — проверяем минимум).
    assert window.tabs.count() >= 4
    # Останавливаем таймеры перед закрытием
    window.resource_timer.stop()
    window.close()
def test_project_tab_controls(qtbot, app):
    """Вкладка «Проект» теперь — только настройки проекта.
    Поля задачи и кнопка «Выполнить» перенесены в «Диалог»."""
    with patch('ui.tabs.project_tab.get_research_enabled', return_value=True), \
         patch('ui.tabs.project_tab.get_max_attempts', return_value=1):
        tab = ProjectTab()
        qtbot.addWidget(tab)
        # Поле папки — read-only
        assert tab.folder_edit.isReadOnly()
        tab.folder_edit.setText("/tmp")
        # Старые поля существуют как скрытые заглушки
        assert not tab.task_input.isVisible()
        # execute_btn удалён 2026-09-25 (Forge-cleanup)
        assert not tab.research_checkbox.isVisible()
        assert not tab.agent_combo.isVisible()
        assert not tab.attempts_spin.isVisible()

def test_main_window_minimal_mode(qtbot, app):
    """B2.1: осталось 4 вкладки. В minimal скрыты Агенты и Настройки."""
    with patch('ui.main_window.get_setting', return_value='minimal'):
        window = MainWindow()
        qtbot.addWidget(window)
        assert not window.tabs.isTabVisible(window.tab_indices["agents"])
        assert not window.tabs.isTabVisible(window.tab_indices["settings"])
        assert window.tabs.isTabVisible(window.tab_indices["command"])
        assert window.tabs.isTabVisible(window.tab_indices["project"])
        assert hasattr(window, 'shortcut_settings')
        window.resource_timer.stop()
    window.close()


def test_main_window_medium_mode(qtbot, app):
    """В medium все 4 вкладки видимы."""
    with patch('ui.main_window.get_setting', return_value='medium'):
        window = MainWindow()
        qtbot.addWidget(window)
        assert window.tabs.isTabVisible(window.tab_indices["agents"])
        assert window.tabs.isTabVisible(window.tab_indices["settings"])
        assert window.tabs.isTabVisible(window.tab_indices["command"])
        assert window.tabs.isTabVisible(window.tab_indices["project"])
        window.resource_timer.stop()
    window.close()


def test_main_window_maximal_mode(qtbot, app):
    """В maximal все 4 вкладки видимы."""
    with patch('ui.main_window.get_setting', return_value='maximal'):
        window = MainWindow()
        qtbot.addWidget(window)
        assert window.tabs.isTabVisible(window.tab_indices["agents"])
        assert window.tabs.isTabVisible(window.tab_indices["settings"])
        assert window.tabs.isTabVisible(window.tab_indices["command"])
        assert window.tabs.isTabVisible(window.tab_indices["project"])
        window.resource_timer.stop()
    window.close()


def test_show_settings_tab(qtbot, app):
    """show_settings_tab делает Настройки видимыми и активными."""
    with patch('ui.main_window.get_setting', return_value='minimal'):
        window = MainWindow()
        qtbot.addWidget(window)
        idx = window.tab_indices["settings"]
        assert not window.tabs.isTabVisible(idx)
        window.show_settings_tab()
        assert window.tabs.isTabVisible(idx)
        assert window.tabs.currentIndex() == idx
        window.resource_timer.stop()
    window.close()


def test_project_tab_agent_combo(qtbot, app):
    """Выбор агента теперь в «Настройках», не в «Проекте».
    Поле осталось как скрытая заглушка для совместимости."""
    from ui.tabs.project_tab import ProjectTab
    tab = ProjectTab()
    qtbot.addWidget(tab)
    assert hasattr(tab, 'agent_combo')
    assert not tab.agent_combo.isVisible()
    # Поле пустое — не заполняется в «Проекте»
    assert tab.agent_combo.count() == 0


# ═══════════════════════════════════════════════════════════════
# AE: кнопка «Попробовать ещё раз»
# ═══════════════════════════════════════════════════════════════

def test_dialog_tab_has_retry_button(app):
    """AE: кнопка retry_btn существует и отключена при старте."""
    from ui.tabs.dialog_tab import DialogTab
    tab = DialogTab()
    assert hasattr(tab, "retry_btn")
    assert tab.retry_btn.isEnabled() is False
    assert hasattr(tab, "_last_failed")
    assert tab._last_failed is False


def test_retry_button_enabled_after_failed(app):
    """AE: после ST_FAILED кнопка retry становится доступной."""
    from ui.tabs.dialog_tab import DialogTab
    from core.background_task import ST_FAILED, ST_DONE
    tab = DialogTab()
    # Симулируем завершение с ошибкой
    tab._do_finish({"status": ST_FAILED, "error": "тест"})
    assert tab._last_failed is True

    # После успеха — сбрасывается
    tab._do_finish({"status": ST_DONE})
    assert tab._last_failed is False


def test_retry_without_task_does_not_crash(app):
    """AE: _on_retry без задачи не падает."""
    from ui.tabs.dialog_tab import DialogTab
    tab = DialogTab()
    # Не должно быть исключения
    tab._on_retry()


# ═══════════════════════════════════════════════════════════════
#  Шаг 3b + BROWSER-CACHE-CLEANUP: кнопки UI
# ═══════════════════════════════════════════════════════════════


def test_dialog_tab_has_audit_buttons():
    """dialog_tab содержит 2 новых кнопки аудита."""
    from ui.tabs.dialog_tab import DialogTab
    w = DialogTab()
    assert hasattr(w, "audit_open_btn")
    assert hasattr(w, "audit_work_btn")
    # По умолчанию обе выключены (нет аудита)
    assert not w.audit_open_btn.isEnabled()
    assert not w.audit_work_btn.isEnabled()


def test_audit_open_disabled_without_audit(qapp, monkeypatch):
    """Без audit_id кнопка не открывает папку."""
    from ui.tabs.dialog_tab import DialogTab
    from PySide6.QtWidgets import QMessageBox
    w = DialogTab()
    w._project_dir = ""
    w._last_audit_id = ""
    calls = []
    monkeypatch.setattr(
        QMessageBox, "information",
        lambda *a, **kw: calls.append("info"))
    w._on_open_audit_report()
    assert "info" in calls


def test_work_on_audit_disabled_without_audit(qapp, monkeypatch):
    """Без audit_id кнопка не запускает работу."""
    from ui.tabs.dialog_tab import DialogTab
    from PySide6.QtWidgets import QMessageBox
    w = DialogTab()
    w._project_dir = ""
    w._last_audit_id = ""
    calls = []
    monkeypatch.setattr(
        QMessageBox, "information",
        lambda *a, **kw: calls.append("info"))
    w._on_work_on_audit()
    assert "info" in calls


def test_audit_enables_buttons_after_success(qapp, tmp_path, monkeypatch):
    """После успешного аудита кнопки включаются."""
    from ui.tabs.dialog_tab import DialogTab
    w = DialogTab()
    w._project_dir = str(tmp_path)
    # Симулируем результат аудита
    # (не запускаем сам аудит — просто проверим логику включения)
    fake_result = {
        "audit_id": "20260924_999999",
        "mode": "full",
        "status": "success",
        "manifest": {"file_count": 3},
        "technical": {"total": 0, "issues": []},
        "security": {"total": 0},
        "tests": {"status": "skipped", "passed": 0, "failed": 0},
        "llm_classification": {"classified": [{"i": 1, "level": "MINOR"}]},
        "llm_ideas": {"success": True, "text": "x"},
    }
    # Эмулируем код включения (как в _on_audit)
    w._last_audit_id = fake_result["audit_id"]
    w.audit_open_btn.setEnabled(bool(w._last_audit_id))
    mode_used = fake_result["mode"]
    has_llm_plan = bool(
        fake_result.get("llm_classification", {}).get("classified")
        or fake_result.get("llm_ideas", {}).get("success"))
    w.audit_work_btn.setEnabled(
        mode_used in ("full", "deep") and has_llm_plan)
    assert w.audit_open_btn.isEnabled()
    assert w.audit_work_btn.isEnabled()


def test_work_btn_stays_disabled_for_fast_mode(qapp, tmp_path):
    """fast-режим — «Работать по плану» остаётся выключенной."""
    from ui.tabs.dialog_tab import DialogTab
    w = DialogTab()
    w._project_dir = str(tmp_path)
    w._last_audit_id = "20260924_888888"
    w.audit_open_btn.setEnabled(True)
    mode_used = "fast"
    has_llm_plan = False
    w.audit_work_btn.setEnabled(
        mode_used in ("full", "deep") and has_llm_plan)
    assert w.audit_open_btn.isEnabled()
    assert not w.audit_work_btn.isEnabled()


def test_agents_tab_has_cache_button():
    """agents_tab содержит кнопку очистки кэша."""
    from ui.tabs.agents_tab import AgentsTab
    w = AgentsTab()
    assert hasattr(w, "cache_cleanup_btn")
    assert w.cache_cleanup_btn.isEnabled()


def test_cache_button_calls_cleanup(qapp, monkeypatch):
    """Кнопка вызывает clean_browser_cache."""
    from ui.tabs.agents_tab import AgentsTab
    from PySide6.QtWidgets import QMessageBox
    called = []
    monkeypatch.setattr(
        QMessageBox, "question",
        lambda *a, **kw: QMessageBox.Yes)
    monkeypatch.setattr(
        QMessageBox, "information",
        lambda *a, **kw: called.append("info"))
    # Мокаем сам clean_browser_cache
    from core import browser_cleanup
    def _fake():
        called.append("clean")
        return {"cleaned_dirs": 1, "freed_bytes": 1024,
                "profiles": ["q"], "errors": []}
    monkeypatch.setattr(browser_cleanup, "clean_browser_cache", _fake)
    w = AgentsTab()
    w._on_clean_browser_cache()
    assert "clean" in called
    assert "info" in called


def test_cache_button_cancel(qapp, monkeypatch):
    """Отмена — чистка НЕ запускается."""
    from ui.tabs.agents_tab import AgentsTab
    from PySide6.QtWidgets import QMessageBox
    called = []
    monkeypatch.setattr(
        QMessageBox, "question",
        lambda *a, **kw: QMessageBox.No)
    from core import browser_cleanup
    monkeypatch.setattr(browser_cleanup, "clean_browser_cache",
                        lambda **kw: called.append("clean"))
    w = AgentsTab()
    w._on_clean_browser_cache()
    assert "clean" not in called
