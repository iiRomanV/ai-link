from PySide6.QtWidgets import QWidget, QVBoxLayout
from ui.widgets.log_widget import LogWidget

class LogsTab(QWidget):
    """Вкладка "Логи": отображение журнала работы приложения."""
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        self.log_widget = LogWidget()
        layout.addWidget(self.log_widget)

    def append_log(self, level_or_msg: str, message: str = ""):
        """Поддерживает оба формата: (msg) и (level, msg)."""
        self.log_widget.append_log(level_or_msg, message)
