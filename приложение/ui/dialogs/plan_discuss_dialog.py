"""PLAN-DISCUSS-DIALOG (2026-10-08, Шаг 4): чат с LLM по плану.

Открывается кнопкой «💬 Обсудить» в AuditPlanDialog.
Слева — LLM (синие пузыри), справа — пользователь (серые).
Внизу: поле ввода + [→] (отправить).
Кнопки: [✓ Применить изменения] — LLM перестраивает план.

План НЕ меняется, пока не нажата «Применить».
Крестик = ничего не сохранилось.
"""
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTextEdit, QLineEdit, QMessageBox, QSplitter, QWidget,
)
from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QColor, QTextCursor


class _DiscussWorker(QThread):
    """Один шаг обсуждения (вызов discuss_plan в фоне)."""

    got_reply = Signal(str)
    got_error = Signal(str)

    def __init__(self, plan: Dict[str, Any],
                 history: List[Dict[str, str]],
                 message: str):
        super().__init__()
        self.plan = plan
        self.history = list(history)
        self.message = message

    def run(self):
        try:
            from core.audit_planner import discuss_plan
            r = discuss_plan(self.plan, self.history, self.message)
            if r.get("ok"):
                self.got_reply.emit(r.get("text", ""))
            else:
                self.got_error.emit(
                    r.get("error", "LLM не ответил"))
        except Exception as e:
            self.got_error.emit(str(e)[:300])


class _ApplyWorker(QThread):
    """Перестроение плана (apply_discussion в фоне)."""

    got_plan = Signal(object)   # dict — новый plan
    got_error = Signal(str)

    def __init__(self, plan: Dict[str, Any],
                 history: List[Dict[str, str]]):
        super().__init__()
        self.plan = plan
        self.history = list(history)

    def run(self):
        try:
            from core.audit_planner import apply_discussion
            r = apply_discussion(self.plan, self.history)
            if r.get("ok"):
                self.got_plan.emit(r.get("plan"))
            else:
                self.got_error.emit(
                    r.get("error", "LLM не ответил"))
        except Exception as e:
            self.got_error.emit(str(e)[:300])


class PlanDiscussDialog(QDialog):
    """Чат-обсуждение плана. Возвращает новый plan через accept()."""

    def __init__(self, plan: Dict[str, Any], parent=None):
        super().__init__(parent)
        self.setWindowTitle("💬 Обсуждение плана")
        self.resize(760, 620)
        self._plan = dict(plan) if plan else {}
        self._history: List[Dict[str, str]] = []
        self._new_plan: Optional[Dict[str, Any]] = None
        self._worker = None
        self._setup_ui()
        self._render_history()

    # --- UI ---
    def _setup_ui(self):
        root = QVBoxLayout(self)

        hint = QLabel(
            "Обсудите план с LLM. Когда договоритесь — нажмите "
            "«✓ Применить изменения».\n"
            "Крестик = отмена, план остаётся прежним."
        )
        hint.setStyleSheet("color: gray; padding: 4px;")
        hint.setWordWrap(True)
        root.addWidget(hint)

        self.chat = QTextEdit()
        self.chat.setReadOnly(True)
        self.chat.setStyleSheet(
            "QTextEdit { background: #fafafa; font-size: 13px; "
            "padding: 8px; }"
        )
        root.addWidget(self.chat, stretch=1)

        # Поле ввода + стрелка
        row = QHBoxLayout()
        self.input = QLineEdit()
        self.input.setPlaceholderText(
            "Например: «А с 1 по 5 не делаем», "
            "«добавь этап с тестами», «верни прошлую версию»"
        )
        self.input.returnPressed.connect(self._on_send)
        row.addWidget(self.input, stretch=1)

        self.btn_send = QPushButton("→")
        self.btn_send.setFixedWidth(48)
        self.btn_send.clicked.connect(self._on_send)
        row.addWidget(self.btn_send)
        root.addLayout(row)

        # Нижний ряд — Применить
        bottom = QHBoxLayout()
        bottom.addStretch()
        self.btn_apply = QPushButton("✓ Применить изменения")
        self.btn_apply.clicked.connect(self._on_apply)
        bottom.addWidget(self.btn_apply)
        root.addLayout(bottom)

    # --- Рендер чата ---
    def _render_history(self):
        self.chat.clear()
        for msg in self._history:
            self._append_bubble(msg.get("role", "user"),
                                msg.get("text", ""))

    def _append_bubble(self, role: str, text: str):
        """Пузырь чата. user — справа (серый), LLM — слева (голубой).

        Пустая строка между пузырями + отступы ~180px с противоположной
        стороны (сдвиг пузыря к своему краю).
        """
        from PySide6.QtGui import QTextBlockFormat, QTextCursor as _TC
        cur = self.chat.textCursor()
        cur.movePosition(_TC.End)

        # Пустая строка между пузырями
        if not self.chat.document().isEmpty():
            cur.insertBlock()
            cur.insertBlock()

        fmt = QTextBlockFormat()
        fmt.setTopMargin(10)
        fmt.setBottomMargin(10)

        if role == "user":
            fmt.setAlignment(Qt.AlignRight)
            fmt.setBackground(QColor("#d0d0d0"))
            fmt.setLeftMargin(180)   # сдвиг вправо
            fmt.setRightMargin(20)
        else:
            fmt.setAlignment(Qt.AlignLeft)
            fmt.setBackground(QColor("#e3f2fd"))
            fmt.setLeftMargin(20)
            fmt.setRightMargin(180)  # сдвиг влево

        cur.setBlockFormat(fmt)
        cur.insertText(text or "")

        self.chat.setTextCursor(cur)
        self.chat.ensureCursorVisible()
    @staticmethod
    def _esc(s: str) -> str:
        return (s or "").replace("&", "&amp;").replace(
            "<", "&lt;").replace(">", "&gt;").replace("\n", "<br>")

    # --- Действия ---
    def _set_busy(self, on: bool, what: str = ""):
        self.input.setEnabled(not on)
        self.btn_send.setEnabled(not on)
        self.btn_apply.setEnabled(not on)
        if on:
            self.btn_send.setText("…")
        else:
            self.btn_send.setText("→")

    def _on_send(self):
        if self._worker and self._worker.isRunning():
            return
        msg = self.input.text().strip()
        if not msg:
            return
        self._history.append({"role": "user", "text": msg})
        self._append_bubble("user", msg)
        self.input.clear()

        self._set_busy(True)
        self._worker = _DiscussWorker(self._plan, self._history[:-1], msg)
        self._worker.got_reply.connect(self._on_reply)
        self._worker.got_error.connect(self._on_error)
        self._worker.finished.connect(lambda: self._set_busy(False))
        self._worker.start()

    def _on_reply(self, text: str):
        self._history.append({"role": "llm", "text": text})
        self._append_bubble("llm", text)

    def _on_error(self, err: str):
        QMessageBox.warning(self, "Ошибка LLM", err)

    def _on_apply(self):
        if self._worker and self._worker.isRunning():
            return
        if not self._history:
            QMessageBox.information(
                self, "Пусто",
                "Сначала обсудите план — напишите хотя бы одно "
                "сообщение."
            )
            return
        self._set_busy(True)
        self.btn_apply.setText("⏳ Перестраиваю…")
        self._worker = _ApplyWorker(self._plan, self._history)
        self._worker.got_plan.connect(self._on_apply_done)
        self._worker.got_error.connect(self._on_apply_error)
        self._worker.finished.connect(
            lambda: self.btn_apply.setText("✓ Применить изменения"))
        self._worker.start()

    def _on_apply_done(self, new_plan):
        if not new_plan or not new_plan.get("stages"):
            QMessageBox.warning(
                self, "Не получилось",
                "LLM не вернул корректный план. Попробуйте ещё раз."
            )
            self._set_busy(False)
            return
        self._new_plan = new_plan
        self.accept()

    def _on_apply_error(self, err: str):
        QMessageBox.warning(self, "Ошибка перестройки", err)
        self._set_busy(False)

    def get_new_plan(self) -> Optional[Dict[str, Any]]:
        """Новый план (после accept). None — если не применяли."""
        return self._new_plan
