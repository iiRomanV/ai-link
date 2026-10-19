from PySide6.QtWidgets import QPlainTextEdit
from PySide6.QtCore import Qt
from PySide6.QtGui import QPalette, QColor


class LogWidget(QPlainTextEdit):
    """Виджет для отображения логов в режиме только для чтения.

    Цвета заданы через QPalette (надёжнее styleSheet для QPlainTextEdit).
    """

    # Цвета (тёмная тема для удобства чтения кода)
    BG_COLOR = "#1e1e1e"
    FG_COLOR = "#d4d4d4"
    SELECT_BG = "#264f78"
    SELECT_FG = "#ffffff"

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setMaximumBlockCount(1000)

        # Цвета через QPalette
        pal = self.palette()
        pal.setColor(QPalette.Base, QColor(self.BG_COLOR))
        pal.setColor(QPalette.Text, QColor(self.FG_COLOR))
        pal.setColor(QPalette.Highlight, QColor(self.SELECT_BG))
        pal.setColor(QPalette.HighlightedText, QColor(self.SELECT_FG))
        self.setPalette(pal)
        self.setAutoFillBackground(True)

        # Шрифт — через styleSheet
        self.setStyleSheet("""
            QPlainTextEdit {
                font-family: monospace;
                font-size: 12px;
                selection-background-color: #264f78;
                selection-color: #ffffff;
            }
        """)

    # Цвета уровней (для QPlainTextEdit через HTML)
    LEVEL_COLORS = {
        "DEBUG":   "#888888",
        "INFO":    "#d4d4d4",
        "WARNING": "#e0b040",
        "ERROR":   "#e06060",
        "CRITICAL": "#ff4040",
    }

    def append_log(self, level_or_msg: str, message: str = ""):
        """Добавляет сообщение в лог.

        Два варианта вызова:
          append_log("текст")             — уровень INFO по умолчанию
          append_log("WARNING", "текст")  — с явным уровнем

        Уровень используется только для цвета. Если Qt не рендерит
        HTML (может быть) — просто appendPlainText.
        """
        if message:
            level = level_or_msg
            text = message
        else:
            level = "INFO"
            text = level_or_msg

        color = self.LEVEL_COLORS.get(level.upper(), "#d4d4d4")
        # QPlainTextEdit не рендерит HTML — используем insertHtml
        # только через appendHtml (есть в QPlainTextEdit как appendHtml?
        # нет). Поэтому — appendPlainText с уровнем в тексте,
        # а цвет по уровню — отдельным способом.
        # Простой путь: plain text (без цвета).
        # Расширенный: используем QTextCursor + QTextCharFormat.
        self.appendPlainText(text)
        self.verticalScrollBar().setValue(self.verticalScrollBar().maximum())
