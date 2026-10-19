"""Вкладка «Карта проекта» — дерево плана текущего проекта + кнопка «Архивировать»."""

import tarfile
from datetime import datetime
from pathlib import Path

from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout,
                               QTreeWidget, QTreeWidgetItem, QLabel,
                               QPushButton, QMessageBox, QFileDialog,
                               QApplication, QMenu)
from PySide6.QtCore import Qt, Signal, QFileSystemWatcher, QTimer
from PySide6.QtGui import QBrush, QColor, QFont

from core.plan_parser import (
    status_icon, status_label, parse_project_plan,
)


class KnowledgeTab(QWidget):
    """Вкладка «Карта проекта» — план текущего проекта."""

    # Сигнал: пользователь выбрал проект из галереи — нужно загрузить
    project_loaded = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._project_dir = ""
        # LR-CARD: режим просмотра по умолчанию
        self._edit_mode = False
        # B7 (2026-10-07): QTimer вместо QFileSystemWatcher.
        # Watcher в Linux (inotify) ненадёжен: теряет файл после
        # атомарной записи, не видит создания файла.
        # QTimer каждые 2 сек проверяет mtime plan.json.
        self._watcher = None  # оставлено для совместимости
        self._last_plan_mtime = 0.0
        self._plan_timer = QTimer(self)
        self._plan_timer.setInterval(2000)  # 2 сек
        self._plan_timer.timeout.connect(self._check_plan_mtime)
        self._plan_timer.start()
        self._setup_ui()
        self._reload_plan()

    @property
    def _app_dir(self) -> Path:
        return Path(__file__).resolve().parent.parent.parent

    @property
    def _ailink_dir(self) -> Path:
        return self._app_dir.parent

    @property
    def _archives_dir(self) -> Path:
        return self._ailink_dir / "архивы"

    def set_project_dir(self, project_dir: str):
        """При смене проекта — перечитываем план и watcher."""
        if not project_dir:
            return
        self._project_dir = project_dir
        # B7: сбрасываем отслеживание mtime — при следующем
        # тике таймера перечитаем план.
        self._last_plan_mtime = 0.0
        self._reload_plan()

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        title = QLabel("Карта проекта")
        title.setStyleSheet("font-size: 16px; font-weight: bold;")
        layout.addWidget(title)

        # LR-CARD: баннер режима (виден только в edit)
        self._mode_banner = QLabel("")
        self._mode_banner.setStyleSheet(
            "background: #fff3cd; color: #856404; "
            "border: 1px solid #ffeeba; border-radius: 4px; "
            "padding: 8px; font-weight: bold;"
        )
        self._mode_banner.setWordWrap(True)
        self._mode_banner.setVisible(False)
        layout.addWidget(self._mode_banner)

        self.hint = QLabel("Загрузка...")
        self.hint.setStyleSheet("color: gray; padding: 4px;")
        self.hint.setWordWrap(True)
        layout.addWidget(self.hint)

        self.tree = QTreeWidget()
        # LR-CARD вариант B: одна колонка (эмодзи в начале).
        self.tree.setHeaderLabels(["План проекта"])
        self.tree.setColumnWidth(0, 820)
        # LR-CARD: клик — только в режиме правки
        self.tree.itemClicked.connect(self._on_item_clicked)
        layout.addWidget(self.tree, stretch=1)

        btn_layout = QHBoxLayout()
        self.projects_btn = QPushButton("📂 Проекты")
        self.projects_btn.setToolTip(
            "Открыть список всех проектов AI Link"
        )
        self.projects_btn.clicked.connect(self._on_open_projects)
        btn_layout.addWidget(self.projects_btn)

        self.refresh_btn = QPushButton("Обновить")
        self.refresh_btn.setToolTip("Перечитать plan.json и обновить дерево")
        self.refresh_btn.clicked.connect(self._reload_plan)
        btn_layout.addWidget(self.refresh_btn)

        # LR-CARD: вход в режим правки без жёлтой панели
        # (для завершённых проектов).
        self.edit_btn = QPushButton("✏️ Редактировать план")
        self.edit_btn.setToolTip(
            "Войти в режим переделки.\n"
            "Клик на этап → «Переделать отсюда»."
        )
        self.edit_btn.clicked.connect(self._on_edit_plan_clicked)
        btn_layout.addWidget(self.edit_btn)

        self.archive_btn = QPushButton("Архивировать")
        self.archive_btn.setToolTip(
            "Создать архив: приложение + планы в одном файле"
        )
        self.archive_btn.clicked.connect(self._on_archive)
        btn_layout.addWidget(self.archive_btn)

        self.export_btn = QPushButton("Экспорт плана")
        self.export_btn.setToolTip(
            "Сохранить план проекта в файл (.md или .json)"
        )
        self.export_btn.clicked.connect(self._on_export_plan)
        btn_layout.addWidget(self.export_btn)

        self.copy_btn = QPushButton("Копировать")
        self.copy_btn.setToolTip(
            "Скопировать план как текст в буфер обмена"
        )
        self.copy_btn.clicked.connect(self._on_copy_plan)
        btn_layout.addWidget(self.copy_btn)

        btn_layout.addStretch()
        layout.addLayout(btn_layout)

    def enter_edit_mode(self):
        """LR-CARD: включить режим правки (вызывается из жёлтой панели)."""
        self._edit_mode = True
        self._mode_banner.setText(
            "✏️ Режим переделки: кликни на пункт, откуда начать заново. "
            "Все шаги после выбранного будут сброшены в ожидание. "
            "Для выхода — закрой вкладку или нажми «Обновить»."
        )
        self._mode_banner.setVisible(True)
        self.tree.setStyleSheet(
            "QTreeWidget::item:hover { background: #fff3cd; }"
        )

    def exit_edit_mode(self):
        """LR-CARD: выключить режим правки."""
        self._edit_mode = False
        self._mode_banner.setVisible(False)
        self.tree.setStyleSheet("")

    def _on_item_clicked(self, item, column):
        """LR-CARD: клик по пункту. Работает только в edit mode."""
        if not self._edit_mode:
            return
        # Собираем цепочку: root → этап → шаг
        text = item.text(0) or ""
        status = item.text(1) or ""
        # Показываем контекстное меню
        menu = QMenu(self)
        menu.setToolTipsVisible(True)
        act_rebuild = menu.addAction("↶ Переделать отсюда")
        act_rebuild.setToolTip(
            "Сбросить этот этап (или шаг) и все следующие.\n"
            "Их статус станет «ожидание», их нужно будет "
            "выполнить заново при следующем запуске."
        )
        act_done = menu.addAction("✅ Поставить галочку")
        act_done.setToolTip(
            "Вручную пометить шаг как выполненный.\n"
            "Работает только для отдельных шагов, не для этапов."
        )
        act_pending = menu.addAction("⏳ Снять галочку")
        act_pending.setToolTip(
            "Вручную пометить шаг как невыполненный.\n"
            "Работает только для отдельных шагов."
        )
        menu.addSeparator()
        act_cancel = menu.addAction("Отмена")

        chosen = menu.exec(self.tree.mapToGlobal(
            self.tree.visualItemRect(item).bottomLeft()
        ))
        if chosen is None or chosen == act_cancel:
            return
        if chosen == act_rebuild:
            self._rebuild_from(item)
        elif chosen == act_done:
            self._mark_item(item, "done")
        elif chosen == act_pending:
            self._mark_item(item, "pending")

    def _find_item_path(self, item):
        """LR-CARD: определить позицию пункта в дереве.

        Возвращает:
          ("root", None, None)          — корень (весь проект)
          ("stage", stage_idx, None)    — этап
          ("item", stage_idx, item_idx) — шаг
          (None, None, None)            — не найдено
        """
        if item is None:
            return (None, None, None)
        parent = item.parent()
        if parent is None:
            return ("root", None, None)
        grand = parent.parent()
        if grand is None:
            # item — прямой потомок root. Это может быть
            # "stage" или "задача" (task_text). Проверим
            # по тексту — если начинается с "Задача:" — пропускаем.
            text = item.text(0) or ""
            if text.startswith("Задача:"):
                return (None, None, None)
            # Ищем этот этап в списке: grand = root, дети root
            # — задача (если есть) + этапы. Проходим по детям.
            root = parent
            children = [root.child(i) for i in range(root.childCount())]
            # Отсеиваем "Задача:" и корень
            stages_only = [
                ch for ch in children
                if ch is not item and not (ch.text(0) or "").startswith("Задача:")
            ]
            # item — тоже stage, находим его индекс среди stages
            stages_list = []
            for ch in children:
                txt = ch.text(0) or ""
                if txt.startswith("Задача:"):
                    continue
                stages_list.append(ch)
            try:
                stage_idx = stages_list.index(item)
            except ValueError:
                return (None, None, None)
            return ("stage", stage_idx, None)
        # item — прямой потомок stage, а grand.parent = root
        root = grand
        children = [root.child(i) for i in range(root.childCount())]
        stages_list = []
        for ch in children:
            txt = ch.text(0) or ""
            if txt.startswith("Задача:"):
                continue
            stages_list.append(ch)
        try:
            stage_idx = stages_list.index(parent)
        except ValueError:
            return (None, None, None)
        items_list = [parent.child(i) for i in range(parent.childCount())]
        try:
            item_idx = items_list.index(item)
        except ValueError:
            return (None, None, None)
        return ("item", stage_idx, item_idx)

    def _rebuild_from(self, item):
        """LR-CARD: реальный сброс plan.json с выбранного места.

        Все шаги после выбранного (включительно) → pending.
        """
        if not self._project_dir:
            QMessageBox.warning(self, "Ошибка", "Проект не выбран.")
            return
        plan_path = Path(self._project_dir) / ".ai_link" / "plan.json"
        if not plan_path.exists():
            QMessageBox.warning(self, "Ошибка", "plan.json не найден.")
            return

        kind, stage_idx, item_idx = self._find_item_path(item)
        if kind is None:
            QMessageBox.information(
                self, "Нельзя",
                "Эта строка не соответствует этапу или шагу. "
                "Кликни на этап или шаг."
            )
            return

        # Описание для диалога
        if kind == "root":
            desc = "весь проект с самого начала"
        elif kind == "stage":
            desc = f"с этапа: {item.text(0)}"
        else:
            desc = f"с шага: {item.text(0)}"

        reply = QMessageBox.question(
            self, "Переделать с этого места",
            f"Сбросить всё после:\n  {desc}\n\n"
            "Все шаги, отмеченные как «done» после этой точки, "
            "станут «pending».\n"
            "После сброса запусти «Продолжить» в панели Диалога.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        # Сбрасываем plan.json
        try:
            import json as _json
            data = _json.loads(plan_path.read_text(encoding="utf-8"))
        except Exception as e:
            QMessageBox.critical(
                self, "Ошибка",
                f"Не удалось прочитать plan.json:\n{e}"
            )
            return

        stages = data.get("stages", [])
        _dropped = 0

        if kind == "root":
            # Сбросить всё
            for s in stages:
                for it in s.get("items", []):
                    if it.get("status") != "pending":
                        it["status"] = "pending"
                        _dropped += 1

        elif kind == "stage":
            # Сбросить весь выбранный этап и все следующие
            for si, s in enumerate(stages):
                if si < stage_idx:
                    continue
                for it in s.get("items", []):
                    if it.get("status") != "pending":
                        it["status"] = "pending"
                        _dropped += 1

        elif kind == "item":
            # Сбросить выбранный шаг + все после в этом этапе
            # + все следующие этапы целиком
            for si, s in enumerate(stages):
                if si < stage_idx:
                    continue
                items = s.get("items", [])
                for ii, it in enumerate(items):
                    if si == stage_idx and ii < item_idx:
                        continue
                    if it.get("status") != "pending":
                        it["status"] = "pending"
                        _dropped += 1

        # Финалим план: статус не done, сбрасываем finished_at
        data["status"] = "in_progress"
        if "finished_at" in data:
            data.pop("finished_at", None)

        try:
            plan_path.write_text(
                _json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as e:
            QMessageBox.critical(
                self, "Ошибка",
                f"Не удалось записать plan.json:\n{e}"
            )
            return

        # Обновляем дерево
        self._reload_plan()

        QMessageBox.information(
            self, "Готово",
            f"Сброшено шагов: {_dropped}.\n"
            "Закрой карту и нажми «▶ Продолжить» в панели Диалога."
        )

    def _mark_item(self, item, new_status):
        """LR-CARD: пометить пункт вручную + сохранить в plan.json."""
        if not self._project_dir:
            return
        plan_path = Path(self._project_dir) / ".ai_link" / "plan.json"
        if not plan_path.exists():
            return

        kind, stage_idx, item_idx = self._find_item_path(item)
        if kind != "item":
            QMessageBox.information(
                self, "Нельзя",
                "Метки работают только для шагов. "
                "Кликни на конкретный шаг внутри этапа."
            )
            return

        try:
            import json as _json
            data = _json.loads(plan_path.read_text(encoding="utf-8"))
        except Exception:
            return

        stages = data.get("stages", [])
        try:
            stages[stage_idx]["items"][item_idx]["status"] = new_status
        except Exception:
            return

        try:
            plan_path.write_text(
                _json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception:
            return
        self._reload_plan()

    def _check_plan_mtime(self):
        """B7 (2026-10-07): проверяет mtime plan.json каждые 2 сек.

        Если файл изменился — перечитывает план.
        Если проект не выбран — ничего не делает.
        """
        if not self._project_dir:
            return
        try:
            plan_path = Path(self._project_dir) / ".ai_link" / "plan.json"
            if not plan_path.exists():
                return
            mtime = plan_path.stat().st_mtime
            if mtime > self._last_plan_mtime:
                self._last_plan_mtime = mtime
                self._reload_plan()
        except Exception:
            pass

    def _on_edit_plan_clicked(self):
        """LR-CARD: кнопка «Редактировать план» — вход в режим правки."""
        if not self._project_dir:
            QMessageBox.information(
                self, "Нельзя",
                "Сначала выбери проект (вкладка «Проект»)."
            )
            return
        plan_path = Path(self._project_dir) / ".ai_link" / "plan.json"
        if not plan_path.exists():
            QMessageBox.information(
                self, "Нельзя",
                "В проекте нет плана (plan.json). "
                "Сначала запусти задачу через «Диалог»."
            )
            return
        if self._edit_mode:
            # Уже в режиме — выйти
            self.exit_edit_mode()
            self.edit_btn.setText("✏️ Редактировать план")
        else:
            self.enter_edit_mode()
            self.edit_btn.setText("✅ Выйти из режима")

    def _on_open_projects(self):
        """Открывает диалог галереи проектов."""
        try:
            from ui.dialogs.projects_dialog import ProjectsDialog
        except Exception as e:
            QMessageBox.warning(self, "Ошибка",
                                f"Не удалось открыть диалог: {e}")
            return
        dlg = ProjectsDialog(self)
        dlg.project_selected.connect(self._on_project_chosen)
        dlg.exec()

    def _on_project_chosen(self, path: str):
        """Пользователь выбрал проект в галерее — эмитим сигнал наружу."""
        if path:
            self.project_loaded.emit(path)

    def _reload_plan(self):
        """Перечитывает план текущего проекта с этапами."""
        self.tree.clear()

        if not self._project_dir:
            self.hint.setText(
                "Проект не выбран. Выберите папку во вкладке «Проект»."
            )
            return

        data = parse_project_plan(self._project_dir)
        if not data:
            self.hint.setText(
                f"План проекта пока не создан.\n"
                f"Он появится после первой задачи через вкладку «Диалог».\n"
                f"Ожидаемый файл: {self._project_dir}/.ai_link/plan.json"
            )
            return

        stages = data.get("stages", [])
        total_items = sum(len(s.get("items", [])) for s in stages)
        goal = data.get("goal", "")
        created_at = (data.get("created_at", "") or "").replace("T", " ")[:16]
        finished_at = (data.get("finished_at", "") or "").replace("T", " ")[:16]
        date_line = ""
        if created_at:
            date_line = f"Создан: {created_at}"
            if finished_at:
                date_line += f"  |  Завершён: {finished_at}"
        self.hint.setText(
            f"Проект: {self._project_dir}\n"
            f"Цель: {goal[:140]}\n"
            f"Этапов: {len(stages)}  |  шагов: {total_items}  "
            f"|  статус: {data.get('status', '?')}"
            + (f"\n{date_line}" if date_line else "")
        )

        root = QTreeWidgetItem(self.tree, [
            f"📋 {data.get('title', 'Проект')}",
        ])
        _root_font = root.font(0)
        _root_font.setBold(True)
        _root_font.setPointSize(_root_font.pointSize() + 1)
        root.setFont(0, _root_font)
        root.setForeground(0, QBrush(QColor("#212529")))

        task_text = data.get("task", "")
        if task_text:
            QTreeWidgetItem(root, [f"📝 Задача: {task_text[:150]}"])

        # B8 (2026-10-07): определяем "текущий этап" — первый,
        # у которого не все items = done. Он будет СИНИМ (идёт).
        _current_stage_idx = -1
        for _i, _st in enumerate(stages):
            _items = _st.get("items", [])
            if not _items:
                continue
            _done = sum(1 for it in _items if it.get("status") == "done")
            if _done < len(_items):
                _current_stage_idx = _i
                break

        for stage_i, stage in enumerate(stages):
            name = stage.get("name", "(без названия)")
            items = stage.get("items", [])
            _origin = stage.get("origin", "initial")
            # Считаем % прогресса этапа
            done = sum(1 for it in items if it.get("status") == "done")
            total = len(items) or 1
            pct = int(done / total * 100)
            # Есть ли failed в items?
            has_failed = any(
                it.get("status") == "failed" for it in items
            )

            # RENDER-ORIGIN (2026-10-10): убранный/добавленный этап
            # имеет приоритет над B8. Убранный — 🔴 и зачёркнут.
            # Добавленный — 🔵 и 🆕.
            _strike = False
            if _origin == "removed":
                _icon = "🔴"
                _color = QColor("#dc3545")
                _origin_mark = "[УБРАН] "
                _strike = True
            elif _origin == "added":
                _icon = "🔵"
                _color = QColor("#4A90E2")
                _origin_mark = "🆕 "
            elif has_failed:
                # B8: 4 состояния.
                #   ❌ есть failed → красный (ошибка)
                #   ✅ pct=100 → зелёный (готово)
                #   🔄 текущий → синий (идёт)
                #   ⏳ остальные → серый (впереди)
                _icon = "❌"
                _color = QColor("#dc3545")
                _origin_mark = ""
            elif pct == 100:
                _icon = "✅"
                _color = QColor("#28a745")
                _origin_mark = ""
            elif stage_i == _current_stage_idx:
                _icon = "🔄"
                _color = QColor("#4A90E2")
                _origin_mark = ""
            else:
                _icon = "⏳"
                _color = QColor("#6c757d")
                _origin_mark = ""
            # LR-CARD вариант B: эмодзи в начале, одна колонка.
            stage_text = (
                f"{_origin_mark}{_icon} {name} "
                f"({done}/{total}) [{pct}%]"
            )
            stage_node = QTreeWidgetItem(root, [stage_text])
            stage_node.setForeground(0, QBrush(_color))
            _f = stage_node.font(0)
            _f.setBold(True)
            if _strike:
                _f.setStrikeOut(True)
            stage_node.setFont(0, _f)

            for idx, item in enumerate(items, 1):
                st = item.get("status", "unknown")
                _txt = item.get("text", "")
                _ic = status_icon(st)
                item_text = f"{_ic} {idx}. {_txt}"
                item_node = QTreeWidgetItem(stage_node, [item_text])
                # B8 (2026-10-07): 4 цвета по статусу.
                if st == "done":
                    _ic_color = QColor("#28a745")   # зелёный
                elif st == "failed":
                    _ic_color = QColor("#dc3545")   # красный
                elif st == "in_progress":
                    _ic_color = QColor("#4A90E2")   # синий
                else:
                    _ic_color = QColor("#6c757d")   # серый
                item_node.setForeground(0, QBrush(_ic_color))
            stage_node.setExpanded(True)

        root.setExpanded(True)
        # B7: запоминаем mtime, чтобы следующий тик таймера
        # не перечитывал зря.
        try:
            _plan_path = Path(self._project_dir) / ".ai_link" / "plan.json"
            if _plan_path.exists():
                self._last_plan_mtime = _plan_path.stat().st_mtime
        except Exception:
            pass

    def _plan_as_markdown(self) -> str:
        """Возвращает план в виде markdown-текста."""
        if not self._project_dir:
            return ""
        data = parse_project_plan(self._project_dir)
        if not data:
            return ""
        lines = []
        lines.append(f"# {data.get('title', 'План проекта')}")
        lines.append("")
        goal = data.get("goal", "")
        if goal:
            lines.append(f"**Цель:** {goal}")
            lines.append("")
        for stage in data.get("stages", []):
            lines.append(f"## {stage.get('name', '')}")
            lines.append("")
            for item in stage.get("items", []):
                status = item.get("status", "pending")
                icon = {
                    "done": "✅",
                    "in_progress": "🔄",
                    "pending": "⏳",
                }.get(status, "•")
                lines.append(f"- {icon} {item.get('text', '')}")
            lines.append("")
        return "\n".join(lines)

    def _on_export_plan(self):
        """Сохраняет план в файл .md или .json."""
        if not self._project_dir:
            QMessageBox.warning(self, "Ошибка",
                                "Проект не выбран.")
            return

        data = parse_project_plan(self._project_dir)
        if not data:
            QMessageBox.warning(self, "Ошибка",
                                "План проекта не найден.\n"
                                "Сначала запустите задачу.")
            return

        default_name = "plan.md"
        path, _ = QFileDialog.getSaveFileName(
            self, "Сохранить план",
            default_name,
            "Markdown (*.md);;JSON (*.json)"
        )
        if not path:
            return

        try:
            if path.endswith(".json"):
                import json as _json
                # Готовим JSON только из нужных полей
                clean = {
                    "title": data.get("title", ""),
                    "goal": data.get("goal", ""),
                    "status": data.get("status", ""),
                    "stages": data.get("stages", []),
                }
                with open(path, "w", encoding="utf-8") as f:
                    _json.dump(clean, f, ensure_ascii=False, indent=2)
            else:
                with open(path, "w", encoding="utf-8") as f:
                    f.write(self._plan_as_markdown())
        except Exception as e:
            QMessageBox.critical(self, "Ошибка",
                                 f"Не удалось сохранить:\n{e}")
            return

        QMessageBox.information(self, "Готово",
                                f"План сохранён:\n{path}")

    def _on_copy_plan(self):
        """Копирует план в буфер обмена."""
        text = self._plan_as_markdown()
        if not text:
            QMessageBox.warning(self, "Ошибка",
                                "Нечего копировать — план не найден.")
            return
        try:
            QApplication.clipboard().setText(text)
            QMessageBox.information(self, "Готово",
                                    "План скопирован в буфер обмена.")
        except Exception as e:
            QMessageBox.critical(self, "Ошибка",
                                 f"Не удалось скопировать:\n{e}")

    def _on_archive(self):
        """Создаёт архив: приложение + планы. Спрашивает место сохранения."""
        app_dir = self._app_dir
        archives_dir = self._archives_dir

        if not app_dir.exists():
            QMessageBox.critical(self, "Ошибка",
                                 f"Папка приложения не найдена: {app_dir}")
            return

        # Запоминаем последнее место сохранения
        last_dir = str(archives_dir)
        try:
            from config.config_manager import get_setting
            saved = get_setting("last_archive_dir", "")
            if saved:
                last_dir = saved
        except Exception:
            pass

        if not Path(last_dir).exists():
            last_dir = str(archives_dir)
            archives_dir.mkdir(parents=True, exist_ok=True)

        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        default_name = f"ai_link_{stamp}.tar.gz"
        default_path = str(Path(last_dir) / default_name)

        # Диалог выбора места
        path, _ = QFileDialog.getSaveFileName(
            self, "Куда сохранить архив",
            default_path,
            "Tar GZ (*.tar.gz);;Все файлы (*)"
        )
        if not path:
            return  # отмена — ничего не создаём

        # Запоминаем папку
        try:
            from config.config_manager import set_setting
            set_setting("last_archive_dir", str(Path(path).parent))
        except Exception:
            pass

        archive_path = Path(path)
        # Гарантируем .tar.gz
        if not str(archive_path).endswith(".tar.gz"):
            archive_path = Path(str(archive_path) + ".tar.gz")

        skipped_dirs = ("__pycache__", ".pytest_cache",
                        ".diagnostics", ".git", ".mypy_cache")

        def _filter(tarinfo):
            name = tarinfo.name
            for sd in skipped_dirs:
                if f"/{sd}/" in name or name.endswith(f"/{sd}"):
                    return None
            return tarinfo

        try:
            with tarfile.open(archive_path, "w:gz") as tar:
                tar.add(str(app_dir), arcname="приложение",
                        filter=_filter)
                plans_dir = self._ailink_dir / "планы"
                if plans_dir.exists():
                    tar.add(str(plans_dir), arcname="планы",
                            filter=_filter)
        except Exception as e:
            QMessageBox.critical(self, "Ошибка архивации",
                                 f"Не удалось создать архив:\n{e}")
            return

        size_kb = archive_path.stat().st_size // 1024

        # Открываем папку с архивом
        try:
            subprocess.Popen(["xdg-open", str(archive_path.parent)])
        except Exception:
            pass

        QMessageBox.information(
            self, "Архив создан",
            f"Файл: {archive_path.name}\n"
            f"Размер: {size_kb} КБ\n"
            f"Папка: {archive_path.parent}"
        )
