"""Диалог настройки сцены (A.5, подэтап 6.2).

Показывает список моделей (PNG/GIF из ~/.ai_link/scene_models/)
и список ролей. Позволяет:
  • Загрузить новую модель (копируется в scene_models/)
  • Удалить модель
  • Назначить модель на роль (клик по модели → клик по роли)
  • Сбросить назначение (двойной клик по роли)
"""
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QListWidget, QListWidgetItem, QMessageBox, QFileDialog,
    QWidget, QSizePolicy,
)
from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QPixmap, QIcon, QFont

from core.scene_manager import (
    SLOTS, list_models, load_assignments, save_assignments,
    add_model, delete_model,
)


class SceneSettingsDialog(QDialog):
    """Диалог настройки: модели ↔ роли."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Настройка сцены")
        self.resize(820, 560)

        self._assignments = load_assignments()
        self._selected_model = ""  # имя файла, выбранное в левом списке

        self._setup_ui()
        self._reload_models()
        self._reload_slots()

    # === UI ===
    def _setup_ui(self):
        root = QVBoxLayout(self)

        hint = QLabel(
            "Выберите модель слева → кликните на роль справа — назначится.\n"
            "Двойной клик на роль — сбросить назначение."
        )
        hint.setStyleSheet("color: gray; padding: 4px;")
        hint.setWordWrap(True)
        root.addWidget(hint)

        cols = QHBoxLayout()

        # === Левая колонка: модели ===
        left = QVBoxLayout()
        left.addWidget(QLabel("📁 Модели"))

        self.models_list = QListWidget()
        self.models_list.setIconSize(QSize(64, 64))
        self.models_list.setSpacing(4)
        self.models_list.currentItemChanged.connect(
            self._on_model_selected
        )
        left.addWidget(self.models_list, stretch=1)

        m_btns = QHBoxLayout()
        load_btn = QPushButton("📂 Загрузить")
        load_btn.clicked.connect(self._on_load)
        m_btns.addWidget(load_btn)

        del_btn = QPushButton("🗑 Удалить")
        del_btn.clicked.connect(self._on_delete)
        m_btns.addWidget(del_btn)
        m_btns.addStretch()
        left.addLayout(m_btns)

        left_w = QWidget()
        left_w.setLayout(left)
        cols.addWidget(left_w, stretch=1)

        # === Правая колонка: роли ===
        right = QVBoxLayout()
        right.addWidget(QLabel("🎭 Роли"))

        self.slots_list = QListWidget()
        self.slots_list.setIconSize(QSize(48, 48))
        self.slots_list.setSpacing(2)
        self.slots_list.itemClicked.connect(self._on_slot_clicked)
        self.slots_list.itemDoubleClicked.connect(
            self._on_slot_reset
        )
        right.addWidget(self.slots_list, stretch=1)

        clear_btn = QPushButton("Сбросить все назначения")
        clear_btn.clicked.connect(self._on_clear_all)
        right.addWidget(clear_btn)

        right_w = QWidget()
        right_w.setLayout(right)
        cols.addWidget(right_w, stretch=1)

        root.addLayout(cols, stretch=1)

        # === Низ: сохранить / отмена ===
        bottom = QHBoxLayout()
        bottom.addStretch()

        save_btn = QPushButton("💾 Сохранить")
        save_btn.setDefault(True)
        save_btn.clicked.connect(self._on_save)
        bottom.addWidget(save_btn)

        cancel_btn = QPushButton("Отмена")
        cancel_btn.clicked.connect(self.reject)
        bottom.addWidget(cancel_btn)

        root.addLayout(bottom)

    # === Модели ===
    def _reload_models(self):
        self.models_list.clear()
        models = list_models()
        for m in models:
            it = QListWidgetItem(m["name"])
            it.setData(Qt.UserRole, m["name"])
            # Иконка — превью
            try:
                pix = QPixmap(m["path"])
                if not pix.isNull():
                    it.setIcon(QIcon(pix))
            except Exception:
                pass
            it.setToolTip(
                f"{m['name']}\n"
                f"{m['size_kb']} КБ\n"
                f"{m['path']}"
            )
            self.models_list.addItem(it)

        if not models:
            placeholder = QListWidgetItem(
                "(нет моделей — нажмите «Загрузить»)"
            )
            placeholder.setFlags(Qt.NoItemFlags)
            placeholder.setForeground(Qt.gray)
            self.models_list.addItem(placeholder)

    def _on_model_selected(self, cur, _prev):
        if cur is None:
            return
        name = cur.data(Qt.UserRole)
        self._selected_model = name or ""

    def _on_load(self):
        start_dir = str(
            __import__("pathlib").Path.home() / "Изображения"
        )
        files, _ = QFileDialog.getOpenFileNames(
            self, "Загрузить модели", start_dir,
            "Изображения (*.png *.gif)"
        )
        if not files:
            return
        added = 0
        errors = []
        for f in files:
            r = add_model(f)
            if r["ok"]:
                added += 1
            else:
                errors.append(f"{f}: {r['error']}")
        self._reload_models()
        self._reload_slots()
        if errors:
            QMessageBox.warning(
                self, "Ошибки загрузки",
                f"Загружено: {added}\n\nОшибки:\n" + "\n".join(errors[:10])
            )

    def _on_delete(self):
        cur = self.models_list.currentItem()
        if cur is None:
            return
        name = cur.data(Qt.UserRole)
        if not name:
            return
        ans = QMessageBox.question(
            self, "Удалить модель",
            f"Удалить «{name}»?\n\n"
            f"Она также снимется со всех ролей.",
            QMessageBox.Yes | QMessageBox.No,
        )
        if ans != QMessageBox.Yes:
            return
        if delete_model(name):
            self._assignments = load_assignments()
            self._reload_models()
            self._reload_slots()

    # === Роли ===
    def _reload_slots(self):
        self.slots_list.clear()
        for slot, title in SLOTS.items():
            model_name = self._assignments.get(slot, "") or "—"
            it = QListWidgetItem(f"{title}:  {model_name}")
            it.setData(Qt.UserRole, slot)
            # Иконка — если есть назначенная модель
            if model_name and model_name != "—":
                from core.scene_manager import MODELS_DIR
                p = MODELS_DIR / model_name
                if p.exists():
                    pix = QPixmap(str(p))
                    if not pix.isNull():
                        it.setIcon(QIcon(pix))
            self.slots_list.addItem(it)

    def _on_slot_clicked(self, item):
        slot = item.data(Qt.UserRole)
        if not slot:
            return
        if not self._selected_model:
            QMessageBox.information(
                self, "Выберите модель",
                "Сначала выберите модель в левом списке."
            )
            return
        self._assignments[slot] = self._selected_model
        self._reload_slots()

    def _on_slot_reset(self, item):
        slot = item.data(Qt.UserRole)
        if not slot:
            return
        self._assignments[slot] = ""
        self._reload_slots()

    def _on_clear_all(self):
        ans = QMessageBox.question(
            self, "Сбросить все",
            "Снять все назначения?",
            QMessageBox.Yes | QMessageBox.No,
        )
        if ans != QMessageBox.Yes:
            return
        for slot in SLOTS:
            self._assignments[slot] = ""
        self._reload_slots()

    # === Сохранение ===
    def _on_save(self):
        try:
            save_assignments(self._assignments)
        except Exception as e:
            QMessageBox.warning(
                self, "Ошибка сохранения", str(e)[:300]
            )
            return
        self.accept()

    def result_assignments(self):
        """Возвращает текущие назначения (после accept)."""
        return dict(self._assignments)
