"""Логирование AI Link.

Пишем в три места:
  1. stdout (StreamHandler)
  2. ~/.ai_link/logs/ai_link.log (FileHandler)
  3. Qt-сигнал для UI (QtLogHandler) — подключается в main.py

QtLogHandler — мост из logging в UI. Каждое сообщение эмитит
сигнал (level_name, message). UI обновляет вкладку «Логи».
"""

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from config.settings import LOG_DIR


class QtLogHandler(QObject, logging.Handler):
    """Handler, который эмитит каждое сообщение как Qt-сигнал.

    Подключается в main.py:
        handler = QtLogHandler()
        setup_logging(qt_handler=handler)
        handler.message_logged.connect(window.logs_tab.append_log)
    """

    # level, message  (level — уже строка: INFO / WARNING / ERROR)
    message_logged = Signal(str, str)

    def __init__(self):
        QObject.__init__(self)
        logging.Handler.__init__(self)

    def emit(self, record):
        try:
            msg = self.format(record)
            self.message_logged.emit(record.levelname, msg)
        except Exception:
            self.handleError(record)


def setup_logging(qt_handler: "QtLogHandler | None" = None):
    """Настраивает логирование.

    qt_handler — если передан, добавляется в корневой логгер.
    Возвращает qt_handler (или None), чтобы main.py мог подключить.
    """
    log_file = LOG_DIR / "ai_link.log"

    # Ротация: активный файл до 5 МБ + 3 бэкапа.
    # Максимум ~20 МБ на диске, старые автоматически удаляются.
    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=5_000_000,
        backupCount=3,
        encoding="utf-8",
    )
    handlers = [
        logging.StreamHandler(sys.stdout),
        file_handler,
    ]
    if qt_handler is not None:
        qt_handler.setLevel(logging.INFO)
        qt_handler.setFormatter(logging.Formatter(
            "%(asctime)s [%(levelname)-7s] %(name)s: %(message)s",
            datefmt="%H:%M:%S",
        ))
        handlers.append(qt_handler)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)-7s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=handlers,
        force=True,   # перезаписываем конфиг, если уже был
    )
    return qt_handler


def get_logger(name: str):
    return logging.getLogger(name)
