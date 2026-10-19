"""Диалог «Поболтать» — простое окно для разговора с ИИ.

Два выпадающих списка:
  • Провайдер: GigaChat (API) / Ollama (локально)
  • Модель: зависит от провайдера

Не работает с браузерными агентами — они только для проектных задач.
"""

from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout,
                               QTextEdit, QPushButton, QComboBox,
                               QLabel)
from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QTextCursor, QShortcut, QKeySequence


# Модели GigaChat (официальный список)
GIGACHAT_MODELS = [
    ("GigaChat", "GigaChat:latest"),
    ("GigaChat-Pro", "GigaChat-Pro:latest"),
    ("GigaChat-Max", "GigaChat-Max:latest"),
]


class _ChatWorker(QThread):
    """Поток для отправки сообщения — не блокирует UI."""
    got_answer = Signal(str)
    got_error = Signal(str)

    def __init__(self, prompt: str, provider: str, model: str):
        super().__init__()
        self.prompt = prompt
        self.provider = provider
        self.model = model

    def run(self):
        try:
            if self.provider == "gigachat":
                from core.gigachat_adapter import query_gigachat
                r = query_gigachat(self.prompt, model=self.model,
                                   chat_mode=True)
            elif self.provider == "ollama":
                from core.ollama_adapter import query_ollama
                r = query_ollama(self.prompt, model=self.model)
            else:
                self.got_error.emit(f"Провайдер {self.provider} не поддержан")
                return

            if r.get("success"):
                self.got_answer.emit(r.get("text", "(пусто)"))
            else:
                self.got_error.emit(r.get("error", "неизвестная ошибка"))
        except Exception as e:
            self.got_error.emit(str(e))


class ChatDialog(QDialog):
    """Всплывающее окно «Поболтать»."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Поболтать с ИИ")
        self.resize(600, 500)
        self._worker = None
        self._messages = []
        self._thinking = False
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        # === Панель выбора модели ===
        top = QHBoxLayout()

        top.addWidget(QLabel("Провайдер:"))
        self.provider_combo = QComboBox()
        self.provider_combo.setMinimumWidth(140)
        self.provider_combo.setMaxVisibleItems(8)
        self.provider_combo.addItem("GigaChat (API)", "gigachat")
        self.provider_combo.addItem("Ollama (локально)", "ollama")
        self.provider_combo.currentIndexChanged.connect(
            self._on_provider_changed
        )
        top.addWidget(self.provider_combo)

        top.addSpacing(12)
        top.addWidget(QLabel("Модель:"))
        self.model_combo = QComboBox()
        self.model_combo.setMinimumWidth(200)
        self.model_combo.setMaxVisibleItems(8)
        top.addWidget(self.model_combo)
        top.addStretch()

        hint = QLabel("Просто поболтать. Не пишет в память.")
        hint.setStyleSheet("color: gray; font-size: 10pt;")
        top.addWidget(hint)
        layout.addLayout(top)

        # Подсказка «текущая модель»
        self.model_hint = QLabel("")
        self.model_hint.setStyleSheet(
            "color: #666; font-size: 9pt; padding: 2px 4px;"
        )
        layout.addWidget(self.model_hint)

        # === История ===
        self.history = QTextEdit()
        self.history.setReadOnly(True)
        # MESSENGER (2026-10-01): стиль мессенджера
        self.history.setStyleSheet(
            "background-color: #ffffff; padding: 8px; font-size: 10pt;"
        )
        layout.addWidget(self.history, stretch=1)

        # === Ввод ===
        self.input_field = QTextEdit()
        self.input_field.setMaximumHeight(80)
        self.input_field.setPlaceholderText(
            "Напишите сообщение и нажмите Ctrl+Enter"
        )
        layout.addWidget(self.input_field)

        # === Кнопки ===
        btns = QHBoxLayout()
        self.send_btn = QPushButton("Отправить")
        self.send_btn.clicked.connect(self._send)
        btns.addWidget(self.send_btn)

        self.clear_btn = QPushButton("Очистить")
        self.clear_btn.clicked.connect(self._clear)
        btns.addWidget(self.clear_btn)

        btns.addStretch()

        self.close_btn = QPushButton("Закрыть")
        self.close_btn.clicked.connect(self.close)
        btns.addWidget(self.close_btn)
        layout.addLayout(btns)

        # Горячие клавиши
        QShortcut(QKeySequence("Ctrl+Return"), self, self._send)
        QShortcut(QKeySequence("Escape"), self, self.close)

        # Первое наполнение — по умолчанию GigaChat
        self._on_provider_changed()

    # === Смена провайдера → обновление списка моделей ===
    def _on_provider_changed(self):
        """Заполняет список моделей в зависимости от провайдера."""
        provider = self.provider_combo.currentData()
        self.model_combo.clear()

        if provider == "gigachat":
            for label, model_id in GIGACHAT_MODELS:
                self.model_combo.addItem(label, model_id)
        elif provider == "ollama":
            try:
                from core.ollama_adapter import get_available_models
                for m in get_available_models():
                    self.model_combo.addItem(m, m)
            except Exception:
                self.model_combo.addItem(
                    "(Ollama не запущена)", ""
                )

        # Сбрасываем на первый
        if self.model_combo.count() > 0:
            self.model_combo.setCurrentIndex(0)

        # Подключаем обновление подсказки (без повторных disconnect)
        if not getattr(self, "_hint_connected", False):
            try:
                self.model_combo.currentIndexChanged.connect(
                    self._update_model_hint
                )
                self._hint_connected = True
            except Exception:
                pass
        self._update_model_hint()

    def _update_model_hint(self):
        """Обновляет подсказку «текущая модель»."""
        provider = self.provider_combo.currentData()
        model = self.model_combo.currentData()
        if not provider or not model:
            self.model_hint.setText("")
            return
        if provider == "gigachat":
            self.model_hint.setText(
                f"Текущая: GigaChat API, модель {model}"
            )
        else:
            self.model_hint.setText(
                f"Текущая: Ollama локально, модель {model}"
            )

    # === Внутреннее представление ===
    def _redraw(self):
        """MESSENGER (2026-10-01): стиль мессенджера.

        Пользователь — справа, серый текст на светло-сером фоне.
        ИИ — слева, чёрный текст на очень светлом фоне.
        """
        self.history.clear()
        cursor = self.history.textCursor()
        cursor.movePosition(QTextCursor.End)

        for who, text in self._messages:
            safe = (text.replace("&", "&amp;")
                        .replace("<", "&lt;")
                        .replace(">", "&gt;")
                        .replace("\n", "<br>"))

            if who == "Вы":
                # Справа: серый пузырь
                cursor.insertHtml(
                    '<table width="100%" cellspacing="0" '
                    'cellpadding="0" style="margin: 4px 0;">'
                    '<tr>'
                    '<td width="30%"></td>'
                    '<td bgcolor="#e8e8e8" align="left" '
                    'style="padding: 8px 12px;">'
                    '<span style="color: #555555;">' + safe + '</span>'
                    '</td>'
                    '</tr></table>'
                )
            else:
                # Слева: светлый пузырь, чёрный текст
                cursor.insertHtml(
                    '<table width="100%" cellspacing="0" '
                    'cellpadding="0" style="margin: 4px 0;">'
                    '<tr>'
                    '<td bgcolor="#f5f5f5" align="left" '
                    'style="padding: 8px 12px;">'
                    '<span style="color: #000000;">' + safe + '</span>'
                    '</td>'
                    '<td width="30%"></td>'
                    '</tr></table>'
                )

        if self._thinking:
            cursor.insertHtml(
                '<table width="100%" cellspacing="0" cellpadding="0" '
                'style="margin: 4px 0;">'
                '<tr>'
                '<td bgcolor="#f5f5f5" align="left" '
                'style="padding: 8px 12px;">'
                '<span style="color: #999999;"><i>... думает ...</i>'
                '</span>'
                '</td>'
                '<td width="30%"></td>'
                '</tr></table>'
            )

        self.history.moveCursor(QTextCursor.End)

    def _handle_rule_command(self, original: str, rule: dict):
        """Обрабатывает команду «сохрани как правило» в чате Поболтать.

        В этом окне нет проекта — сохраняем только глобальные правила.
        """
        try:
            from core.rules_manager import add_global_rule
        except Exception as e:
            self._messages.append(("Вы", original))
            self._messages.append(("ИИ", f"❌ Ошибка: {e}"))
            self._redraw()
            self.input_field.clear()
            return

        rule_text = rule.get("text", "")
        entry = add_global_rule(rule_text, source="user")

        self._messages.append(("Вы", original))
        if entry:
            self._messages.append((
                "ИИ",
                f"✅ Глобальное правило сохранено: «{entry['text']}»\n"
                f"(будет применяться ко всем проектам)"
            ))
        else:
            self._messages.append(("ИИ", "❌ Не удалось сохранить правило"))

        self._redraw()
        self.input_field.clear()

    def _send(self):
        if self._thinking:
            return
        text = self.input_field.toPlainText().strip()
        if not text:
            return

        # Проверка: команда «сохрани как правило»
        try:
            from core.rules_parser import parse_rule_command
            rule = parse_rule_command(text)
        except Exception:
            rule = {"is_rule": False}

        if rule.get("is_rule"):
            self._handle_rule_command(text, rule)
            return

        provider = self.provider_combo.currentData()
        model = self.model_combo.currentData()
        if not provider or not model:
            return

        self._messages.append(("Вы", text))
        self._thinking = True
        self._redraw()

        self.input_field.clear()
        self.send_btn.setEnabled(False)

        self._worker = _ChatWorker(text, provider=provider, model=model)
        self._worker.got_answer.connect(self._on_answer)
        self._worker.got_error.connect(self._on_error)
        self._worker.start()

    def _on_answer(self, text: str):
        self._thinking = False
        self._messages.append(("ИИ", text))
        self._redraw()
        self.send_btn.setEnabled(True)

    def _on_error(self, err: str):
        self._thinking = False
        self._messages.append(("ИИ", f"[ошибка: {err}]"))
        self._redraw()
        self.send_btn.setEnabled(True)

    def _clear(self):
        self._messages = []
        self._thinking = False
        self._redraw()
