from PySide6.QtWidgets import QPushButton
from PySide6.QtCore import Qt

class TooltipButton(QPushButton):
    """Кнопка с встроенной всплывающей подсказкой при наведении."""
    def __init__(self, text: str, tooltip: str, parent=None):
        super().__init__(text, parent)
        self.setToolTip(tooltip)
        self.setCursor(Qt.PointingHandCursor)
        # Дополнительно можно задать минимальный размер, если нужно
        # self.setMinimumHeight(30)
