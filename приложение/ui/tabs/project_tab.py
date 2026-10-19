import subprocess
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel,
                               QLineEdit, QTextEdit, QFileDialog,
                               QProgressBar, QMessageBox, QCheckBox,
                               QSpinBox, QPushButton, QComboBox, QGroupBox)
from PySide6.QtCore import Qt, Signal
from ui.widgets.tooltip_button import TooltipButton
from config.settings import get_research_enabled, get_max_attempts
from core.agent_registry import load_agents
import os
import re


class ProjectTab(QWidget):
    """Вкладка "Проект": ввод задачи, выбор папки, запуск выполнения."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setup_ui()

    def setup_ui(self):
        layout = QVBoxLayout(self)
        self._main_layout = layout  # для embed_widget

        # Заголовок
        title = QLabel("Проект: настройки")
        title.setStyleSheet("font-size: 16px; font-weight: bold;")
        layout.addWidget(title)

        # === Секция «Папка проекта» ===
        folder_box = QGroupBox("Папка проекта")
        folder_layout_outer = QVBoxLayout(folder_box)
        folder_layout = QHBoxLayout()
        self.folder_edit = QLineEdit()
        self.folder_edit.setPlaceholderText("Путь к папке проекта")
        self.folder_edit.setToolTip("Выберите папку, где будут создаваться файлы")
        self.folder_edit.setReadOnly(True)
        self.browse_btn = TooltipButton("Обзор...", "Выбрать папку для проекта")
        self.browse_btn.clicked.connect(self.browse_folder)
        self.open_btn = TooltipButton("Открыть",
                                       "Открыть папку проекта в файловом менеджере")
        self.open_btn.clicked.connect(self.open_folder)
        folder_layout.addWidget(self.folder_edit)
        folder_layout.addWidget(self.browse_btn)
        folder_layout.addWidget(self.open_btn)
        folder_layout_outer.addLayout(folder_layout)
        layout.addWidget(folder_box)

        # === Секция «Управление проектом» ===
        ctrl_box = QGroupBox("Управление проектом")
        ctrl_box_layout = QVBoxLayout(ctrl_box)

        row1 = QHBoxLayout()
        self.btn_start = QPushButton("➕ Новый проект")
        self.btn_start.setToolTip(
            "Создать новый проект с нуля. Если есть незавершённый "
            "чекпоинт — Link спросит: продолжить или начать заново."
        )
        self.btn_improve = QPushButton("🛠 Доработать")
        self.btn_improve.setToolTip(
            "Продолжить работу над существующим проектом: "
            "добавить функцию, исправить баг, проверить.\n"
            "Требует, чтобы у проекта уже был план (plan.json)."
        )
        # CONTINUE-PLAN (2026-10-09): запуск по принятому plan.json.
        self.btn_continue_plan = QPushButton("📋 По плану")
        self.btn_continue_plan.setToolTip(
            "Продолжить работу по принятому плану (plan.json).\n"
            "Не спрашивает задачу — просто запускает выполнение\n"
            "первого незакрытого этапа."
        )
        self.btn_stop = QPushButton("⏹ Стоп")
        self.btn_stop.setToolTip("Прервать текущий прогон.")
        self.btn_stop.setStyleSheet("font-weight: bold; color: #cc0000;")
        # BTN-STOP (2026-10-11): по умолчанию серый. Включится
        # при старте прогона через status_changed.
        self.btn_stop.setEnabled(False)
        row1.addWidget(self.btn_start)
        row1.addWidget(self.btn_improve)
        row1.addWidget(self.btn_continue_plan)
        row1.addWidget(self.btn_stop)
        ctrl_box_layout.addLayout(row1)

        row2 = QHBoxLayout()
        self.btn_history = QPushButton("📜 История")
        self.btn_history.setToolTip(
            "Открыть папку .ai_link — там dialog.jsonl, plan.json, state.json."
        )
        self.btn_audit = QPushButton("🔍 Проверить проект")
        self.btn_audit.setToolTip(
            "Технический аудит проекта (без LLM): "
            "синтаксис, пустые файлы, TODO, импорты."
        )
        self.btn_open_report = QPushButton("🗂 Отчёт")
        self.btn_open_report.setToolTip(
            "Открыть папку с последним audit_report.md и JSON."
        )
        # AUDIT-PLANNER (4.2, 2026-10-08): просмотр предварительного
        # плана из аудита (audit_plan.json). Активна, если он есть.
        self.btn_plan = QPushButton("📋 План")
        self.btn_plan.setToolTip(
            "Посмотреть ПРЕДВАРИТЕЛЬНЫЙ план доработки из аудита.\n"
            "Согласовать / отменить / обсудить."
        )
        row2.addWidget(self.btn_history)
        row2.addWidget(self.btn_audit)
        row2.addWidget(self.btn_open_report)
        row2.addWidget(self.btn_plan)
        row2.addStretch()
        ctrl_box_layout.addLayout(row2)

        layout.addWidget(ctrl_box)

        # Статус
        self.status_label = QLabel("Готов")
        self.status_label.setStyleSheet("color: gray;")
        layout.addWidget(self.status_label)

        # Скрытые заглушки для совместимости
        self.task_input = QTextEdit()
        self.task_input.setVisible(False)
        self.research_checkbox = QCheckBox()
        self.research_checkbox.setVisible(False)
        self.research_checkbox.setChecked(get_research_enabled())
        self.agent_combo = QComboBox()
        self.agent_combo.setVisible(False)
        self.attempts_spin = QSpinBox()
        self.attempts_spin.setVisible(False)
        self.attempts_spin.setRange(1, 10)
        self.attempts_spin.setValue(get_max_attempts())
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)

        # Проверка готовности полей


    def refresh_agent_combo(self):
        """Заполняет выпадающий список агентов только включёнными."""
        self.agent_combo.clear()
        # Первый пункт — "Авто" (Link сам выбирает лучшего агента)
        self.agent_combo.addItem("Авто (лучший для задачи)", "auto")
        agents = load_agents()
        for agent in agents:
            if agent.get("enabled", False):
                self.agent_combo.addItem(agent.get("name", agent.get("id")), agent.get("id"))

    def showEvent(self, event):
        """При отображении вкладки обновляем список агентов."""
        self.refresh_agent_combo()
        # A3 (2026-10-07): определяем тип папки — блокируем кнопки.
        try:
            self._update_buttons_by_folder()
        except Exception:
            pass
        super().showEvent(event)

    # === A3 (2026-10-07): определение типа папки ===
    def _detect_folder_type(self) -> str:
        """Определяет тип папки проекта.

        Возвращает:
          "empty"   — пустая папка (нет файлов кроме служебных).
          "mine"    — наш проект (есть .ai_link/plan.json +
                      created_by_user == текущий, или пусто = старый свой).
          "foreign" — чужая папка (есть файлы, но нет нашего плана).
        """
        import json as _json
        from pathlib import Path as _P

        folder = self.folder_edit.text().strip()
        if not folder:
            return "empty"
        root = _P(folder)
        if not root.exists() or not root.is_dir():
            return "empty"

        # Считаем файлы (без служебных).
        _skip = {"__pycache__", ".git", ".pytest_cache", ".venv",
                 "venv", "node_modules", ".mypy_cache", ".ruff_cache"}
        files = [
            f for f in root.iterdir()
            if f.name not in _skip and not f.name.startswith(".")
        ]
        # Скрытые, кроме .ai_link, тоже пропускаем для оценки.
        if not files:
            return "empty"

        # Есть .ai_link/plan.json?
        plan = root / ".ai_link" / "plan.json"
        if not plan.exists():
            return "foreign"

        # Читаем created_by_user.
        try:
            data = _json.loads(plan.read_text(encoding="utf-8"))
            owner = (data.get("created_by_user") or "").strip()
        except Exception:
            owner = ""

        # Пусто = старый свой проект (до A3) → считаем своим.
        if not owner:
            return "mine"

        # Сверяем с текущим пользователем.
        try:
            from config.config_manager import get_setting
            import getpass
            current = (get_setting("user_name", "") or "").strip()
            if not current:
                current = getpass.getuser() or ""
        except Exception:
            current = ""

        if owner == current:
            return "mine"
        return "foreign"

    def _update_buttons_by_folder(self):
        """Блокирует/активирует кнопки по типу папки.

        Логика:
          empty   → ➕ активна, 🛠 ❌, 🔍 ❌, 🗂 ❌
          mine    → ➕ ❌, 🛠 активна, 🔍 активна, 🗂 активна (если отчёт есть)
          foreign → ➕ ❌, 🛠 ❌, 🔍 активна, 🗂 ❌
        """
        from pathlib import Path as _P

        ftype = self._detect_folder_type()

        # ➕ Новый проект
        self.btn_start.setEnabled(ftype == "empty")
        # 🛠 Доработать
        self.btn_improve.setEnabled(ftype == "mine")
        # 📋 По плану — активна если plan.json есть и status != done
        _cp_ok = False
        if ftype == "mine":
            folder = self.folder_edit.text().strip()
            if folder:
                _plan_p = _P(folder) / ".ai_link" / "plan.json"
                if _plan_p.exists():
                    try:
                        import json as _json
                        _pd = _json.loads(
                            _plan_p.read_text(encoding="utf-8"))
                        _cp_ok = _pd.get("status") != "done"
                    except Exception:
                        _cp_ok = False
        self.btn_continue_plan.setEnabled(_cp_ok)
        # 🔍 Проверить
        self.btn_audit.setEnabled(ftype in ("mine", "foreign"))
        # 🗂 Открыть отчёт
        _report_ok = False
        if ftype in ("mine", "foreign"):
            folder = self.folder_edit.text().strip()
            if folder:
                # FIX (2026-10-08): папка называется `audits` (с s).
                # В audit_project.py: _audits_dir = .ai_link/audits/.
                audit_dir = _P(folder) / ".ai_link" / "audits"
                _report_ok = audit_dir.exists() and any(audit_dir.iterdir())
        self.btn_open_report.setEnabled(_report_ok)
        # 📋 План — активна, если есть audit_plan.json в audits/.
        _plan_ok = False
        if ftype in ("mine", "foreign"):
            folder = self.folder_edit.text().strip()
            if folder:
                _ad = _P(folder) / ".ai_link" / "audits"
                if _ad.exists():
                    for _sub in _ad.iterdir():
                        if (_sub.is_dir()
                                and (_sub / "audit_plan.json").exists()):
                            _plan_ok = True
                            break
        try:
            self.btn_plan.setEnabled(_plan_ok)
        except Exception:
            pass
        # 📜 История — если есть .ai_link/
        _hist_ok = False
        folder = self.folder_edit.text().strip()
        if folder:
            _hist_ok = (_P(folder) / ".ai_link").exists()
        self.btn_history.setEnabled(_hist_ok)

        # Tooltip с пояснением.
        _tips = {
            "empty": "Пустая папка → доступен ➕ Новый проект.",
            "mine": "Свой проект → 🛠 Доработать или 🔍 Проверить.",
            "foreign": ("Чужая папка → сначала 🔍 Проверить проект. "
                        "После аудита появится план."),
        }
        self.status_label.setText(_tips.get(ftype, ""))

        # Иконки-индикаторы на кнопках (необязательно).
        return ftype

    def browse_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Выберите папку проекта")
        if folder:
            self.folder_edit.setText(folder)

    def open_folder(self):
        """Открывает папку проекта в файловом менеджере."""
        folder = self.folder_edit.text().strip()
        if not folder:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.warning(self, "Ошибка", "Папка проекта не выбрана.")
            return
        try:
            subprocess.Popen(["xdg-open", folder])
        except Exception as e:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.warning(self, "Ошибка",
                                f"Не удалось открыть папку:\n{e}")


    # === EMBED (B2, 2026-10-07): встраивание виджетов ===
    def embed_widget(self, widget, stretch: int = 1):
        """Встраивает внешний виджет в layout Проекта.

        Используется, чтобы встроить DialogTab прямо во вкладку
        «Проект» (Диалог перестаёт быть отдельной вкладкой).
        """
        # self._main_layout сохраняется в setup_ui.
        if hasattr(self, "_main_layout"):
            self._main_layout.addWidget(widget, stretch=stretch)
            return True
        return False

    # Методы для управления из главного окна
    def append_log(self, message: str):
        """Заглушка: логи идут в командный пункт, не в Проект."""
        pass

    def set_status(self, text: str):
        self.status_label.setText(text)

    def set_progress_visible(self, visible: bool):
        self.progress_bar.setVisible(visible)
        if visible:
            self.progress_bar.setRange(0, 0)  # бесконечная анимация
        else:
            self.progress_bar.setRange(0, 100)
            self.progress_bar.setValue(0)

