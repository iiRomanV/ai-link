"""Диалог ручных назначений агентов на фазы.

Пользователь явно указывает, какой агент работает на какой фазе.
Приоритет этих назначений выше автоматического выбора по бенчмарку.

Ключи (совпадают с категориями бенчмарка + fallback):
  planning      — фаза plan
  coding        — фазы code, fix
  comprehension — фаза review
  fallback      — аварийная фаза

Значение "Авто" (None) — вернуть фазу к автоматике.
"""

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QLabel,
    QComboBox, QPushButton, QMessageBox,
)
from PySide6.QtCore import Qt

from core.capability_benchmark import (
    ASSIGNMENT_KEYS, PHASE_INFO, phase_tooltip,
    list_available_agents, load_assignments, save_assignments,
)


AUTO_LABEL = "Авто (по бенчмарку)"


class AssignmentsDialog(QDialog):
    """Диалог ручных назначений агентов на фазы."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Ручные назначения агентов")
        self.resize(520, 320)
        self._combos = {}   # key → QComboBox
        self._setup_ui()
        self._load_values()

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        intro = QLabel(
            "Явно укажите, какой агент работает на какой фазе. "
            "Эти назначения имеют приоритет над автоматическим "
            "выбором по результатам теста. "
            "Выберите «Авто», чтобы вернуть фазу к автоматике."
        )
        intro.setWordWrap(True)
        intro.setStyleSheet("color: gray; padding: 4px;")
        layout.addWidget(intro)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight)

        for key in ASSIGNMENT_KEYS:
            info = PHASE_INFO.get(key, {})
            label_text = info.get("title", key)
            # Tooltip — с описанием фазы
            tip = phase_tooltip(key)

            combo = QComboBox()
            combo.setMinimumWidth(220)
            combo.setToolTip(tip)
            # Первый пункт — «Авто»
            combo.addItem(AUTO_LABEL, None)
            # Затем все агенты
            for a in list_available_agents():
                label = f"{a['name']}  ({a['id']})"
                if not a.get("enabled", True):
                    label += "  [выключен]"
                combo.addItem(label, a["id"])
            self._combos[key] = combo

            # Метка с tooltip — чтобы при наведении на подпись
            # тоже всплывало описание
            lbl = QLabel(label_text + ":")
            lbl.setToolTip(tip)
            form.addRow(lbl, combo)

        layout.addLayout(form)

        # Подсказка про fallback
        hint = QLabel(
            "«Резерв» — аварийная фаза: запускается, если "
            "«Исправление» провалилось два раза подряд."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: gray; font-size: 10pt; padding: 4px;")
        layout.addWidget(hint)

        layout.addStretch()

        # Кнопки
        btns = QHBoxLayout()
        btns.addStretch()
        save_btn = QPushButton("Сохранить")
        save_btn.clicked.connect(self._on_save)
        btns.addWidget(save_btn)

        cancel_btn = QPushButton("Отмена")
        cancel_btn.clicked.connect(self.reject)
        btns.addWidget(cancel_btn)
        layout.addLayout(btns)

    def _load_values(self):
        """Загружает текущие назначения в комбобоксы."""
        data = load_assignments()
        assignments = data.get("assignments", {})
        for key, combo in self._combos.items():
            current = assignments.get(key)
            # Ищем индекс с этим agent_id
            idx = 0  # по умолчанию — «Авто»
            if current:
                for i in range(combo.count()):
                    if combo.itemData(i) == current:
                        idx = i
                        break
            combo.setCurrentIndex(idx)

    def _on_save(self):
        """Сохраняет назначения из комбобоксов."""
        data = load_assignments()
        assignments = data.setdefault("assignments", {})
        # Очищаем — потом заполняем заново только непустыми
        assignments.clear()
        for key, combo in self._combos.items():
            value = combo.currentData()
            if value:
                assignments[key] = value

        try:
            save_assignments(data)
        except Exception as e:
            QMessageBox.critical(
                self, "Ошибка",
                f"Не удалось сохранить назначения:\n{e}"
            )
            return

        QMessageBox.information(
            self, "Готово",
            "Назначения сохранены. Они применятся к следующей задаче."
        )
        self.accept()
