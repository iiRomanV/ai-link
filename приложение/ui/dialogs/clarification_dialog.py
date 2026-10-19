"""CLARIFICATION-DIALOG (2026-10-10, Б41): модальное окно уточнения.

Появляется, когда Link запросил уточнение (needs_clarification).
Не надо искать вопрос в чате — окно само всплывает с текстом.

Кнопки:
  • ✓ Ответить и продолжить — отправить ответ, оркестратор продолжит
  • ✕ Отмена — ничего не делаем, кнопка «Ответить» в диалоге остаётся
"""
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QPlainTextEdit,
)
from PySide6.QtCore import Qt, Signal


class ClarificationDialog(QDialog):
    """Модальное окно для ответа на уточнение Link."""

    answer_submitted = Signal(str)

    def __init__(self, question: str, phase: str = "",
                 parent=None):
        super().__init__(parent)
        self.setWindowTitle("❓ Link уточняет")
        self.resize(640, 360)
        self._question = question or "(вопрос пуст)"
        self._phase = phase or ""
        self._setup_ui()

    def _setup_ui(self):
        root = QVBoxLayout(self)

        # Заголовок
        head = QLabel("🤖 <b>Link не может продолжить без уточнения</b>")
        head.setStyleSheet("font-size: 13pt; padding: 4px;")
        root.addWidget(head)

        # Фаза
        if self._phase:
            ph = QLabel(f"<i>Фаза: {self._phase}</i>")
            ph.setStyleSheet("color: #666; padding: 2px 4px;")
            root.addWidget(ph)

        # Вопрос
        q = QLabel(self._question)
        q.setWordWrap(True)
        q.setTextInteractionFlags(Qt.TextSelectableByMouse)
        q.setStyleSheet(
            "background: #fff8e1; border: 1px solid #ffc107; "
            "border-radius: 4px; padding: 8px; "
            "font-size: 12pt; color: #333;"
        )
        root.addWidget(q)

        # Поле ввода
        hint = QLabel("Ваш ответ:")
        hint.setStyleSheet("padding-top: 6px;")
        root.addWidget(hint)

        self.edit = QPlainTextEdit()
        self.edit.setPlaceholderText(
            "Напишите ответ. Enter — отправить, "
            "Shift+Enter — новая строка."
        )
        self.edit.setStyleSheet("font-size: 11pt;")
        root.addWidget(self.edit, stretch=1)

        # Кнопки
        btns = QHBoxLayout()
        btns.addStretch()

        cancel = QPushButton("✕ Отмена")
        cancel.setToolTip(
            "Закрыть без ответа. Вопрос останется в чате — "
            "можно ответить через поле ввода в Проекте."
        )
        cancel.clicked.connect(self.reject)

        submit = QPushButton("✓ Ответить и продолжить")
        submit.setDefault(True)
        submit.setToolTip(
            "Отправить ответ и продолжить работу Link."
        )
        submit.clicked.connect(self._on_submit)

        btns.addWidget(cancel)
        btns.addWidget(submit)
        root.addLayout(btns)

        self.edit.setFocus()

    def _on_submit(self):
        ans = self.edit.toPlainText().strip()
        if not ans:
            # Пустой ответ — не отправляем, подсвечиваем поле.
            self.edit.setStyleSheet(
                "font-size: 11pt; border: 1px solid #cc0000;"
            )
            return
        self.answer_submitted.emit(ans)
        self.accept()

    def keyPressEvent(self, event):
        """Ctrl+Enter / Enter без Shift — отправить."""
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            if not (event.modifiers() & Qt.ShiftModifier):
                self._on_submit()
                return
        super().keyPressEvent(event)
