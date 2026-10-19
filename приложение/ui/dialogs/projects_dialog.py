"""Диалог «Проекты» — галерея пользовательских проектов AI Link.

Всегда показывает ВСЕ проекты. Скрытые — серым цветом и внизу списка.
Без чекбоксов: одна кнопка «Скрыть/Показать» переключает статус.
"""

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QMessageBox,
    QInputDialog, QAbstractItemView
)
from PySide6.QtCore import Signal, Slot, Qt
from PySide6.QtGui import QColor, QBrush

from core.projects_scanner import (
    scan_projects, rename_project, clear_project_memory,
    delete_project, hide_project, unhide_project,
    get_projects_dir,
)


STATUS_LABELS = {
    "done": "✅ готово",
    "in_progress": "🔄 в работе",
    "pending": "⏳ ожидает",
    "?": "? неизвестно",
    "": "—",
}

# Стили для скрытых проектов
HIDDEN_COLOR = QColor("#999999")


class ProjectsDialog(QDialog):
    """Диалог со списком всех проектов AI Link."""

    project_selected = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Проекты AI Link")
        self.resize(860, 540)
        self._setup_ui()
        self.reload()

    # === UI ===
    def _setup_ui(self):
        layout = QVBoxLayout(self)

        # Заголовок + путь
        self.path_label = QLabel("")
        self.path_label.setStyleSheet("color: gray; padding: 2px 4px;")
        self.path_label.setWordWrap(True)
        layout.addWidget(self.path_label)

        # Подсказка
        hint = QLabel(
            "Скрытые проекты показаны серым и помечены [скрыт]. "
            "Двойной клик — загрузить проект."
        )
        hint.setStyleSheet("color: #666; font-size: 10pt; padding: 2px 4px;")
        layout.addWidget(hint)

        # Таблица
        self.table = QTableWidget()
        self.table.setColumnCount(4)
        self.table.setHorizontalHeaderLabels(
            ["Название", "Статус", "Изменён", "Папка"]
        )
        self.table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.Stretch
        )
        self.table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeToContents
        )
        self.table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeToContents
        )
        self.table.horizontalHeader().setSectionResizeMode(
            3, QHeaderView.Stretch
        )
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.doubleClicked.connect(self._on_load)
        layout.addWidget(self.table)

        # Кнопки — верхний ряд
        btn1 = QHBoxLayout()
        self.load_btn = QPushButton("Загрузить")
        self.load_btn.setToolTip(
            "Открыть проект: переключить папку во вкладке Проект"
        )
        self.load_btn.clicked.connect(self._on_load)
        btn1.addWidget(self.load_btn)

        self.rename_btn = QPushButton("Переименовать")
        self.rename_btn.setToolTip("Изменить название проекта (в plan.json)")
        self.rename_btn.clicked.connect(self._on_rename)
        btn1.addWidget(self.rename_btn)

        self.hide_btn = QPushButton("Скрыть / Показать")
        self.hide_btn.setToolTip(
            "Скрытый проект остаётся в списке, но помечается серым "
            "и уходит вниз"
        )
        self.hide_btn.clicked.connect(self._on_toggle_hide)
        btn1.addWidget(self.hide_btn)

        btn1.addStretch()

        self.reload_btn = QPushButton("Обновить")
        self.reload_btn.clicked.connect(self.reload)
        btn1.addWidget(self.reload_btn)
        layout.addLayout(btn1)

        # Нижний ряд
        btn2 = QHBoxLayout()
        self.clear_btn = QPushButton("Очистить память")
        self.clear_btn.setToolTip(
            "Удалить историю проекта (.ai_link).\n"
            "Код проекта и его название остаются."
        )
        self.clear_btn.clicked.connect(self._on_clear_memory)
        btn2.addWidget(self.clear_btn)

        self.delete_btn = QPushButton("Удалить проект")
        self.delete_btn.setToolTip("Удалить всю папку проекта (код + память)")
        self.delete_btn.setStyleSheet("color: #c62828; font-weight: bold;")
        self.delete_btn.clicked.connect(self._on_delete)
        btn2.addWidget(self.delete_btn)

        btn2.addStretch()

        self.close_btn = QPushButton("Закрыть")
        self.close_btn.clicked.connect(self.reject)
        btn2.addWidget(self.close_btn)
        layout.addLayout(btn2)

    # === Загрузка списка ===
    @Slot()
    def reload(self):
        """Пересканировать папку проектов и заполнить таблицу.

        Всегда показывает ВСЕ проекты, включая скрытые.
        """
        base = get_projects_dir()
        if not base:
            self.path_label.setText(
                "⚠ Папка проектов не указана. Откройте Настройки → Проекты."
            )
        else:
            self.path_label.setText(f"Папка проектов: {base}")

        # Всегда включаем скрытые — они отображаются серым
        projects = scan_projects(include_hidden=True)

        self.table.setRowCount(len(projects))
        for row, p in enumerate(projects):
            hidden = bool(p.get("hidden"))
            title_text = p.get("title", "") or p.get("name", "")
            if hidden:
                title_text = f"[скрыт] {title_text}"

            # Название
            title_item = QTableWidgetItem(title_text)
            title_item.setData(Qt.UserRole, p.get("path", ""))
            if hidden:
                title_item.setForeground(QBrush(HIDDEN_COLOR))
            self.table.setItem(row, 0, title_item)

            # Статус
            status_raw = p.get("status", "")
            status_text = STATUS_LABELS.get(status_raw, status_raw or "—")
            status_item = QTableWidgetItem(status_text)
            status_item.setTextAlignment(Qt.AlignCenter)
            if hidden:
                status_item.setForeground(QBrush(HIDDEN_COLOR))
            self.table.setItem(row, 1, status_item)

            # Изменён
            mtime_item = QTableWidgetItem(p.get("mtime_str", "") or "—")
            mtime_item.setTextAlignment(Qt.AlignCenter)
            if hidden:
                mtime_item.setForeground(QBrush(HIDDEN_COLOR))
            self.table.setItem(row, 2, mtime_item)

            # Папка
            path_item = QTableWidgetItem(p.get("path", ""))
            if hidden:
                path_item.setForeground(QBrush(HIDDEN_COLOR))
            self.table.setItem(row, 3, path_item)

        if not projects:
            if base:
                self.path_label.setText(
                    f"Папка проектов: {base}\nПроекты не найдены."
                )

    # === Получить выбранный проект ===
    def _selected_path(self) -> str:
        row = self.table.currentRow()
        if row < 0:
            return ""
        item = self.table.item(row, 0)
        if item is None:
            return ""
        return item.data(Qt.UserRole) or ""

    def _selected_title(self) -> str:
        row = self.table.currentRow()
        if row < 0:
            return ""
        item = self.table.item(row, 0)
        if not item:
            return ""
        text = item.text()
        # Убираем префикс [скрыт]
        if text.startswith("[скрыт] "):
            text = text[len("[скрыт] "):]
        return text

    def _selected_is_hidden(self) -> bool:
        row = self.table.currentRow()
        if row < 0:
            return False
        item = self.table.item(row, 0)
        if not item:
            return False
        return item.text().startswith("[скрыт] ")

    # === Действия ===
    @Slot()
    def _on_load(self):
        path = self._selected_path()
        if not path:
            QMessageBox.information(self, "Проекты",
                                    "Выберите проект в списке.")
            return
        self.project_selected.emit(path)
        self.accept()

    @Slot()
    def _on_rename(self):
        path = self._selected_path()
        if not path:
            QMessageBox.information(self, "Проекты",
                                    "Выберите проект.")
            return
        old_title = self._selected_title()
        new_title, ok = QInputDialog.getText(
            self, "Переименовать проект",
            "Новое название:",
            text=old_title,
        )
        if not ok:
            return
        new_title = new_title.strip()
        if not new_title or new_title == old_title:
            return
        if rename_project(path, new_title):
            self.reload()
        else:
            QMessageBox.warning(self, "Ошибка",
                                "Не удалось переименовать проект.")

    @Slot()
    def _on_toggle_hide(self):
        path = self._selected_path()
        if not path:
            QMessageBox.information(self, "Проекты",
                                    "Выберите проект.")
            return
        if self._selected_is_hidden():
            ok = unhide_project(path)
        else:
            ok = hide_project(path)
        if ok:
            self.reload()
        else:
            QMessageBox.warning(self, "Ошибка",
                                "Не удалось изменить видимость проекта.")

    @Slot()
    def _on_clear_memory(self):
        path = self._selected_path()
        if not path:
            QMessageBox.information(self, "Проекты",
                                    "Выберите проект.")
            return
        reply = QMessageBox.question(
            self, "Очистить память",
            f"Очистить историю проекта?\n\n{path}\n\n"
            f"Будут удалены: план, диалоги, история, решения.\n"
            f"Код проекта и название СОХРАНЯТСЯ.\n\n"
            f"Продолжить?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        if clear_project_memory(path):
            QMessageBox.information(self, "Готово",
                                    "Память проекта очищена.\n"
                                    "Название и код сохранены.")
            self.reload()
        else:
            QMessageBox.warning(self, "Ошибка",
                                "Не удалось очистить память.")

    @Slot()
    def _on_delete(self):
        path = self._selected_path()
        if not path:
            QMessageBox.information(self, "Проекты",
                                    "Выберите проект.")
            return
        reply = QMessageBox.warning(
            self, "Удалить проект?",
            f"Удалить папку проекта целиком?\n\n{path}\n\n"
            f"Будут удалены ВСЕ файлы и папка .ai_link.\n"
            f"Действие необратимо.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        confirm, ok = QInputDialog.getText(
            self, "Подтверждение удаления",
            "Чтобы удалить проект, введите слово УДАЛИТЬ:"
        )
        if not ok or confirm.strip().upper() != "УДАЛИТЬ":
            QMessageBox.information(self, "Отменено",
                                    "Удаление отменено.")
            return
        if delete_project(path):
            QMessageBox.information(self, "Готово",
                                    "Проект удалён.")
            self.reload()
        else:
            QMessageBox.warning(self, "Ошибка",
                                "Не удалось удалить проект.")
