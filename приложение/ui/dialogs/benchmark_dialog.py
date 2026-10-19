"""Диалог тестирования ИИ-моделей (Capability Profile).

Прогоняет 10 коротких задач через выбранные агенты, строит
таблицу pass_rate по категориям и сохраняет в
~/.ai_link/brain/capabilities.json.
"""

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QCheckBox,
    QPushButton, QPlainTextEdit, QMessageBox,
    QProgressBar, QTreeWidget, QTreeWidgetItem, QTabWidget,
    QWidget, QScrollArea, QToolButton,
)
from PySide6.QtCore import QThread, Signal, Slot, Qt
from PySide6.QtGui import QColor, QBrush

from core.capability_benchmark import (
    BENCHMARK_TASKS, CATEGORIES, GROUP_TITLES,
    CATEGORY_INFO, category_tooltip,
    list_available_agents, run_benchmark, save_results,
)


class BenchmarkWorker(QThread):
    """Прогон в фоне. UI не блокируется."""
    progress = Signal(int, int, dict)   # done, total, result
    log = Signal(str)
    finished_ok = Signal(dict)          # data
    finished_error = Signal(str)

    def __init__(self, agent_ids):
        super().__init__()
        self.agent_ids = agent_ids

    def run(self):
        try:
            def on_prog(done, total, r):
                self.progress.emit(done, total, r)
                mark = "✓" if r.get("passed") else "✗"
                self.log.emit(
                    f"{mark} {r['agent']} / {r['task_id']} "
                    f"({r['seconds']:.1f}s)"
                    + (f"  error={r['error'][:60]}" if r.get("error") else "")
                )

            data = run_benchmark(agent_ids=self.agent_ids, on_progress=on_prog)
            try:
                save_results(data)
            except Exception as e:
                self.log.emit(f"⚠ Не удалось сохранить: {e}")
            self.finished_ok.emit(data)
        except Exception as e:
            self.finished_error.emit(str(e))


class BenchmarkDialog(QDialog):
    """Окно тестирования моделей."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Тест моделей ИИ")
        self.resize(820, 640)
        self.worker = None
        self._checkbox_by_id = {}   # agent_id → QCheckBox
        self._group_boxes = {}      # group → QGroupBox
        self._setup_ui()
        self._load_agents()

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        intro = QLabel(
            "Отметьте модели для тестирования. Прогон долгий "
            "(10–30 мин), запускайте когда есть время."
        )
        intro.setStyleSheet("color: gray; padding: 4px;")
        intro.setWordWrap(True)
        layout.addWidget(intro)

        # === Группы агентов (со сворачиванием и прокруткой) ===
        self.groups_container = QWidget()
        self.groups_layout = QVBoxLayout(self.groups_container)
        self.groups_layout.setContentsMargins(0, 0, 0, 0)
        self.groups_layout.setSpacing(4)

        self.groups_scroll = QScrollArea()
        self.groups_scroll.setWidgetResizable(True)
        self.groups_scroll.setWidget(self.groups_container)
        self.groups_scroll.setMinimumHeight(180)
        self.groups_scroll.setMaximumHeight(340)
        self.groups_scroll.setFrameShape(QScrollArea.NoFrame)
        layout.addWidget(self.groups_scroll)

        # === Сводка ===
        self.summary_label = QLabel("")
        self.summary_label.setStyleSheet("font-weight: bold; padding: 4px;")
        layout.addWidget(self.summary_label)

        # === Кнопки ===
        btns = QHBoxLayout()
        self.run_btn = QPushButton("Начать тест")
        self.run_btn.clicked.connect(self._on_run)
        btns.addWidget(self.run_btn)

        self.assign_btn = QPushButton("Настроить вручную")
        self.assign_btn.setToolTip(
            "Явно указать, какой агент работает на какой фазе. "
            "Приоритет выше автоматического выбора по бенчмарку."
        )
        self.assign_btn.clicked.connect(self._on_open_assignments)
        btns.addWidget(self.assign_btn)

        self.close_btn = QPushButton("Закрыть")
        self.close_btn.clicked.connect(self.reject)
        btns.addWidget(self.close_btn)
        btns.addStretch()
        layout.addLayout(btns)

        # === Прогресс ===
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        layout.addWidget(self.progress_bar)

        # === Вкладки: Лог / Результат ===
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, stretch=1)

        # Лог
        log_page = QWidget()
        log_layout = QVBoxLayout(log_page)
        self.log_area = QPlainTextEdit()
        self.log_area.setReadOnly(True)
        self.log_area.setStyleSheet("""
            QPlainTextEdit {
                background-color: #1e1e1e;
                color: #d4d4d4;
                font-family: monospace;
                font-size: 11px;
            }
        """)
        log_layout.addWidget(self.log_area)
        self.tabs.addTab(log_page, "Лог")

        # Результаты — дерево агент → категория → задачи
        result_page = QWidget()
        result_layout = QVBoxLayout(result_page)
        self.result_tree = QTreeWidget()
        self.result_tree.setHeaderLabels(
            ["Агент / категория / задача", "Результат", "Сек"]
        )
        self.result_tree.setColumnWidth(0, 480)
        result_layout.addWidget(self.result_tree)
        self.tabs.addTab(result_page, "Результат")

        # Сырые ответы — отдельная вкладка
        raw_page = QWidget()
        raw_layout = QVBoxLayout(raw_page)
        self.raw_area = QPlainTextEdit()
        self.raw_area.setReadOnly(True)
        self.raw_area.setStyleSheet("""
            QPlainTextEdit {
                background-color: #fafafa;
                font-family: monospace;
                font-size: 11px;
            }
        """)
        raw_layout.addWidget(self.raw_area)
        self.tabs.addTab(raw_page, "Сырые ответы")

    # === Список агентов ===

    def _load_agents(self):
        agents = list_available_agents()
        if not agents:
            QMessageBox.warning(self, "Ошибка",
                                "Не удалось загрузить список агентов.")
            return

        # Группируем
        by_group = {}
        for a in agents:
            by_group.setdefault(a["group"], []).append(a)

        for group in ("browser", "api", "local"):
            items = by_group.get(group, [])
            if not items:
                continue

            # Контейнер группы: заголовок-переключатель + содержимое.
            group_container = QWidget()
            group_outer = QVBoxLayout(group_container)
            group_outer.setContentsMargins(0, 0, 0, 0)
            group_outer.setSpacing(2)

            # Заголовок-переключатель (стрелка + название).
            toggle = QToolButton()
            toggle.setCheckable(True)
            toggle.setChecked(True)
            toggle.setArrowType(Qt.DownArrow)
            toggle.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
            toggle.setText(GROUP_TITLES.get(group, group))
            toggle.setStyleSheet(
                "QToolButton {"
                "  font-weight: bold;"
                "  border: none;"
                "  padding: 4px;"
                "  text-align: left;"
                "}"
            )
            group_outer.addWidget(toggle)

            # Контент группы — то, что скрывается при сворачивании.
            content = QWidget()
            content_layout = QVBoxLayout(content)
            content_layout.setContentsMargins(20, 0, 0, 6)
            content_layout.setSpacing(2)

            # Чекбокс «Выбрать все»
            group_cb = QCheckBox("Выбрать все")
            group_cb.setChecked(group != "local")  # local по умолчанию off
            content_layout.addWidget(group_cb)

            # Чекбоксы конкретных моделей
            for a in items:
                cb = QCheckBox(f"{a['name']}  ({a['id']})")
                cb.setChecked(a["enabled"] and group != "local")
                if not a["enabled"]:
                    cb.setEnabled(False)
                    cb.setText(cb.text() + " — выключен в реестре")
                cb.stateChanged.connect(self._update_summary)
                content_layout.addWidget(cb)
                self._checkbox_by_id[a["id"]] = cb

            group_outer.addWidget(content)

            # Связь заголовка со сворачиванием.
            def make_toggle_handler(c, b):
                def handler(checked):
                    c.setVisible(checked)
                    b.setArrowType(Qt.DownArrow if checked else Qt.RightArrow)
                return handler
            toggle.toggled.connect(make_toggle_handler(content, toggle))

            # Связь «Выбрать все» с индивидуальными.
            def make_group_handler(cbs):
                def handler(state):
                    on = bool(state)
                    for c in cbs:
                        if c.isEnabled():
                            c.setChecked(on)
                return handler
            group_cb.stateChanged.connect(
                make_group_handler([self._checkbox_by_id[a["id"]] for a in items])
            )

            self._group_boxes[group] = group_container
            self.groups_layout.addWidget(group_container)

        self._update_summary()

    def _selected_agents(self):
        result = []
        for aid, cb in self._checkbox_by_id.items():
            if cb.isChecked() and cb.isEnabled():
                result.append(aid)
        return result

    def _update_summary(self):
        n_agents = len(self._selected_agents())
        n_tasks = len(BENCHMARK_TASKS)
        total = n_agents * n_tasks
        if n_agents == 0:
            self.summary_label.setText("Ни одна модель не выбрана.")
            self.summary_label.setStyleSheet(
                "font-weight: bold; padding: 4px; color: #c62828;")
        else:
            self.summary_label.setText(
                f"Задач: {n_tasks} × {n_agents} модел. = {total}. "
                f"Примерное время: {total * 20 // 60}–{total * 30 // 60} мин."
            )
            self.summary_label.setStyleSheet(
                "font-weight: bold; padding: 4px; color: #2e7d32;")

    # === Запуск ===

    def _on_run(self):
        if self.worker and self.worker.isRunning():
            QMessageBox.warning(self, "Внимание", "Тест уже идёт.")
            return
        agents = self._selected_agents()
        if not agents:
            QMessageBox.warning(self, "Внимание",
                                "Выберите хотя бы одну модель.")
            return

        self.log_area.clear()
        self.result_tree.clear()
        self.raw_area.clear()
        self.progress_bar.setValue(0)
        self.tabs.setCurrentIndex(0)
        self.run_btn.setEnabled(False)
        for cb in self._checkbox_by_id.values():
            cb.setEnabled(False)

        self._log(f"Запускаю тест: {len(agents)} моделей, "
                  f"{len(BENCHMARK_TASKS)} задач каждая.")

        self.worker = BenchmarkWorker(agents)
        self.worker.progress.connect(self._on_progress)
        self.worker.log.connect(self._log)
        self.worker.finished_ok.connect(self._on_finished)
        self.worker.finished_error.connect(self._on_error)
        self.worker.start()

    @Slot(int, int, dict)
    def _on_progress(self, done, total, result):
        if total > 0:
            self.progress_bar.setValue(int(done * 100 / total))

    @Slot(str)
    def _log(self, text):
        self.log_area.appendPlainText(text)

    @Slot(dict)
    def _on_finished(self, data):
        self.progress_bar.setValue(100)
        self.run_btn.setEnabled(True)
        for cb in self._checkbox_by_id.values():
            cb.setEnabled(True)
        self._build_result_tree(data)
        self.tabs.setCurrentIndex(1)
        self._log("✓ Тест завершён. Результаты сохранены.")

    @Slot(str)
    def _on_error(self, err):
        self._log(f"✗ Ошибка теста: {err}")
        self.run_btn.setEnabled(True)
        for cb in self._checkbox_by_id.values():
            cb.setEnabled(True)
        QMessageBox.critical(self, "Ошибка", err)

    def _on_open_assignments(self):
        """Открывает диалог ручных назначений."""
        try:
            from ui.dialogs.assignments_dialog import AssignmentsDialog
        except Exception as e:
            QMessageBox.warning(self, "Ошибка",
                                f"Не удалось открыть диалог: {e}")
            return
        dlg = AssignmentsDialog(parent=self)
        dlg.exec()

    # === Построение дерева результатов ===

    def _build_result_tree(self, data):
        self.result_tree.clear()
        raw_lines = []
        results = data.get("results", {})
        for agent_id, res in results.items():
            summary = res.get("summary", {})
            agent_item = QTreeWidgetItem(self.result_tree,
                                         [agent_id, "", ""])
            agent_item.setExpanded(True)
            for cat in CATEGORIES:
                cat_data = summary.get(cat, {})
                passed = cat_data.get("passed", 0)
                failed = cat_data.get("failed", 0)
                total = passed + failed
                rate = cat_data.get("pass_rate", 0.0)
                avg = cat_data.get("avg_sec", 0.0)
                # Русское название категории + tooltip с описанием
                cat_title = CATEGORY_INFO.get(cat, {}).get("title", cat)
                label = (f"{cat_title}:  {passed}/{total} "
                         f"({int(rate * 100)}%), {avg:.1f}s")
                cat_item = QTreeWidgetItem(agent_item, [label, "", ""])
                cat_item.setToolTip(0, category_tooltip(cat))
                cat_item.setExpanded(True)

                for r in cat_data.get("raw", []):
                    mark = "✓" if r.get("passed") else "✗"
                    tid = r.get("task_id", "?")
                    sec = r.get("seconds", 0.0)
                    leaf = QTreeWidgetItem(
                        cat_item,
                        [f"{mark} {tid}", mark, f"{sec:.1f}"],
                    )
                    if not r.get("passed"):
                        leaf.setForeground(1, QBrush(QColor("#c62828")))
                    else:
                        leaf.setForeground(1, QBrush(QColor("#2e7d32")))

                    raw_lines.append(
                        f"===== {agent_id} / {tid} / "
                        f"{'PASS' if r.get('passed') else 'FAIL'} =====\n"
                        f"error: {r.get('error') or '—'}\n"
                        f"raw:\n{r.get('raw') or '(пусто)'}\n\n"
                    )
        self.raw_area.setPlainText("".join(raw_lines) or "(нет данных)")

    # === Защита от закрытия во время теста ===

    def closeEvent(self, event):
        if self.worker and self.worker.isRunning():
            reply = QMessageBox.question(
                self, "Внимание",
                "Тест ещё идёт. Закрыть окно? "
                "(текущий прогон прервётся после текущей задачи)",
                QMessageBox.Yes | QMessageBox.No
            )
            if reply != QMessageBox.Yes:
                event.ignore()
                return
        event.accept()
