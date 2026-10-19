from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel,
                               QTableWidget, QTableWidgetItem, QHeaderView,
                               QPushButton, QProgressBar)
from PySide6.QtCore import Qt, QTimer
from core.resource_manager import resource_manager

class ResourcesTab(QWidget):
    """Вкладка отображения загрузки системы и статистики выполнения."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setup_ui()
        self.refresh()

        # Таймер обновления каждые 2 секунды (запустим при показе вкладки)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh_system_usage)
        # Не запускаем здесь, ждём showEvent

    def setup_ui(self):
        layout = QVBoxLayout(self)

        # Секция системных ресурсов
        sys_label = QLabel("Системные ресурсы")
        sys_label.setStyleSheet("font-weight: bold;")
        layout.addWidget(sys_label)

        self.cpu_bar = QProgressBar()
        self.cpu_bar.setRange(0, 100)
        self.cpu_bar.setFormat("CPU: %p%")
        layout.addWidget(self.cpu_bar)

        self.mem_bar = QProgressBar()
        self.mem_bar.setRange(0, 100)
        self.mem_bar.setFormat("Память: %p%")
        layout.addWidget(self.mem_bar)

        # Секция статистики выполнения
        stats_label = QLabel("Статистика выполнения")
        stats_label.setStyleSheet("font-weight: bold;")
        layout.addWidget(stats_label)

        self.stats_table = QTableWidget()
        self.stats_table.setColumnCount(2)
        self.stats_table.setHorizontalHeaderLabels(["Показатель", "Значение"])
        self.stats_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.stats_table.verticalHeader().setVisible(False)
        layout.addWidget(self.stats_table)

        # Таблица последних результатов
        self.results_table = QTableWidget()
        self.results_table.setColumnCount(4)
        self.results_table.setHorizontalHeaderLabels(["Время", "Задача", "Успех", "Длит. (с)"])
        self.results_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.results_table.verticalHeader().setVisible(False)
        layout.addWidget(self.results_table)

        # Кнопка обновления
        refresh_btn = QPushButton("Обновить")
        refresh_btn.clicked.connect(self.refresh)
        layout.addWidget(refresh_btn)

    def refresh_system_usage(self):
        usage = resource_manager.get_system_usage()
        self.cpu_bar.setValue(int(usage["cpu"]))
        self.mem_bar.setValue(int(usage["memory"]))

    def refresh(self):
        self.refresh_system_usage()
        stats = resource_manager.get_statistics()

        # Заполняем общую статистику
        stats_data = [
            ("Всего задач", str(stats["task_count"])),
            ("Успешных", str(stats["success_count"])),
            ("Проваленных", str(stats["failure_count"])),
            ("Общее время (с)", str(stats["total_duration"])),
            ("Среднее время (с)", str(stats["average_duration"]))
        ]
        self.stats_table.setRowCount(len(stats_data))
        for row, (name, value) in enumerate(stats_data):
            self.stats_table.setItem(row, 0, QTableWidgetItem(name))
            self.stats_table.setItem(row, 1, QTableWidgetItem(value))

        # Заполняем таблицу последних результатов
        results = stats["last_results"]
        self.results_table.setRowCount(len(results))
        for row, entry in enumerate(results):
            self.results_table.setItem(row, 0, QTableWidgetItem(entry["time"]))
            self.results_table.setItem(row, 1, QTableWidgetItem(entry["task"]))
            success_text = "Да" if entry["success"] else "Нет"
            self.results_table.setItem(row, 2, QTableWidgetItem(success_text))
            self.results_table.setItem(row, 3, QTableWidgetItem(str(entry["duration"])))

    def showEvent(self, event):
        """Запускаем таймер при отображении вкладки."""
        if not self.timer.isActive():
            self.timer.start(2000)
        super().showEvent(event)

    def closeEvent(self, event):
        self.timer.stop()
        super().closeEvent(event)
