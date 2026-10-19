"""Диалог калибровки браузерных сайтов."""

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QComboBox,
    QSpinBox, QPushButton, QPlainTextEdit, QMessageBox
)
from PySide6.QtCore import QThread, Signal, Slot, Qt


class CalibrationWorker(QThread):
    log = Signal(str)
    finished_ok = Signal(dict)
    finished_error = Signal(str)

    def __init__(self, site_id: str, delay: int):
        super().__init__()
        self.site_id = site_id
        self.delay = delay

    def run(self):
        try:
            self.log.emit(f"Открываю сайт '{self.site_id}'...")
            self.log.emit(f"Жду появления поля ввода (максимум {self.delay} сек)...")
            self.log.emit("Если требуется вход — войдите в аккаунт в открывшемся браузере.")

            from core.agents.browser_calibrator import calibrate_site
            result = calibrate_site(self.site_id, headless=False, save=True, startup_delay=self.delay)

            if result.get("success"):
                self.log.emit("✅ Калибровка успешна.")
                self.log.emit(f"  input_selector: {result.get('input_selector')}")
                self.log.emit(f"  response_selector: {result.get('response_selector')}")
                self.finished_ok.emit(result)
            else:
                err = result.get("error", "неизвестная ошибка")
                self.log.emit(f"❌ Ошибка калибровки: {err}")
                self.finished_error.emit(err)
        except Exception as e:
            self.log.emit(f"❌ Критическая ошибка: {e}")
            self.finished_error.emit(str(e))


class CalibrationDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Калибровка браузерных сайтов")
        self.resize(560, 420)
        self.worker = None
        self._setup_ui()
        self._load_sites()

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        layout.addWidget(QLabel("Выберите сайт для калибровки:"))

        self.site_combo = QComboBox()
        layout.addWidget(self.site_combo)

        delay_layout = QHBoxLayout()
        delay_layout.addWidget(QLabel("Ожидание входа (сек):"))
        self.delay_spin = QSpinBox()
        self.delay_spin.setRange(10, 600)
        self.delay_spin.setValue(180)
        self.delay_spin.setToolTip("Сколько секунд ждать появления поля ввода.\nЕсли вы уже вошли — калибровка начнётся раньше.")
        delay_layout.addWidget(self.delay_spin)
        delay_layout.addStretch()
        layout.addLayout(delay_layout)

        btn_layout = QHBoxLayout()
        self.run_btn = QPushButton("Начать калибровку")
        self.run_btn.clicked.connect(self._run_calibration)
        btn_layout.addWidget(self.run_btn)

        self.close_btn = QPushButton("Закрыть")
        self.close_btn.clicked.connect(self.reject)
        btn_layout.addWidget(self.close_btn)
        layout.addLayout(btn_layout)

        # Большой статус-индикатор
        self.status_big = QLabel("")
        self.status_big.setAlignment(Qt.AlignCenter)
        self.status_big.setStyleSheet("font-size: 14px; font-weight: bold; padding: 8px;")
        layout.addWidget(self.status_big)

        layout.addWidget(QLabel("Лог:"))
        self.log_area = QPlainTextEdit()
        self.log_area.setReadOnly(True)
        self.log_area.setStyleSheet("""
            QPlainTextEdit {
                background-color: #1e1e1e;
                color: #d4d4d4;
                font-family: monospace;
                font-size: 11px;
            }
        """)
        layout.addWidget(self.log_area)

    def _load_sites(self):
        try:
            from core.agents.browser_sites_manager import load_sites
            data = load_sites()
            for s in data.get("sites", []):
                self.site_combo.addItem(s.get("name", s.get("id")), s.get("id"))
            if self.site_combo.count() == 0:
                self.site_combo.addItem("(нет сайтов)", None)
        except Exception as e:
            self._append_log(f"Ошибка загрузки сайтов: {e}")

    def _append_log(self, text: str):
        self.log_area.appendPlainText(text)

    def _run_calibration(self):
        if self.worker and self.worker.isRunning():
            QMessageBox.warning(self, "Внимание", "Калибровка уже идёт.")
            return

        site_id = self.site_combo.currentData()
        if not site_id:
            QMessageBox.warning(self, "Внимание", "Сайт не выбран.")
            return

        self.log_area.clear()
        self.status_big.setText("⏳ Идёт калибровка...")
        self.status_big.setStyleSheet("font-size: 14px; font-weight: bold; padding: 8px; color: #1976d2;")
        self.run_btn.setEnabled(False)
        self.site_combo.setEnabled(False)
        self.delay_spin.setEnabled(False)

        self.worker = CalibrationWorker(site_id, self.delay_spin.value())
        self.worker.log.connect(self._append_log)
        self.worker.finished_ok.connect(self._on_ok)
        self.worker.finished_error.connect(self._on_error)
        self.worker.start()

    @Slot(dict)
    def _on_ok(self, result):
        inp = result.get("input_selector", "—")
        resp = result.get("response_selector", "—")
        self.status_big.setText("✅ УСПЕХ — селекторы сохранены")
        self.status_big.setStyleSheet("font-size: 14px; font-weight: bold; padding: 8px; color: #2e7d32;")
        self._append_log(f"input_selector:    {inp}")
        self._append_log(f"response_selector: {resp}")
        self._append_log("Результат сохранён в browser_sites.json.")
        self.run_btn.setEnabled(True)
        self.site_combo.setEnabled(True)
        self.delay_spin.setEnabled(True)

    @Slot(str)
    def _on_error(self, err):
        self.status_big.setText("❌ ОШИБКА калибровки")
        self.status_big.setStyleSheet("font-size: 14px; font-weight: bold; padding: 8px; color: #c62828;")
        self._append_log(f"Причина: {err}")
        self.run_btn.setEnabled(True)
        self.site_combo.setEnabled(True)
        self.delay_spin.setEnabled(True)

    def closeEvent(self, event):
        if self.worker and self.worker.isRunning():
            reply = QMessageBox.question(
                self, "Внимание",
                "Калибровка ещё идёт. Закрыть окно?",
                QMessageBox.Yes | QMessageBox.No
            )
            if reply != QMessageBox.Yes:
                event.ignore()
                return
        event.accept()
