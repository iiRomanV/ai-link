from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
                               QTableWidget, QTableWidgetItem, QHeaderView,
                               QMessageBox, QDialog, QFormLayout, QLineEdit,
                               QComboBox, QCheckBox, QTextEdit, QDialogButtonBox,
                               QLabel, QGroupBox)
from PySide6.QtCore import Qt, QTimer
from core.agent_registry import (load_agents, save_agents, add_agent, remove_agent,
                                 set_agent_enabled, get_agent)
from core.secure_store import save_secret, get_secret
from core.browser_warmup import (
    get_all_statuses, warmup_site_async, cancel_warmup,
    force_reset_site,
    ST_DISABLED, ST_IDLE, ST_LOADING, ST_READY, ST_LOGIN, ST_ERROR,
)
import json

class AgentDialog(QDialog):
    """Диалог добавления/редактирования агента."""
    def __init__(self, agent=None, parent=None):
        super().__init__(parent)
        self.agent = agent
        self.setWindowTitle("Агент")
        self.setup_ui()
        if agent:
            self.load_agent_data()

    def setup_ui(self):
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.id_edit = QLineEdit()
        self.id_edit.setReadOnly(False)
        self.id_edit.setEnabled(True)
        self.id_edit.setPlaceholderText("уникальный id, например perplexity")
        form.addRow("ID:", self.id_edit)
        self.name_edit = QLineEdit()
        self.name_edit.setReadOnly(False)
        self.name_edit.setEnabled(True)
        form.addRow("Название:", self.name_edit)
        self.type_combo = QComboBox()
        self.type_combo.addItems(["external", "api", "local"])
        form.addRow("Тип:", self.type_combo)
        self.enabled_check = QCheckBox("Включен")
        self.enabled_check.setChecked(True)
        form.addRow(self.enabled_check)
        self.config_edit = QTextEdit()
        self.config_edit.setReadOnly(False)
        self.config_edit.setEnabled(True)
        self.config_edit.setPlaceholderText('JSON-конфигурация, например {"path": "/usr/bin/..."} или {"api_key": "..."}')
        form.addRow("Конфигурация (JSON):", self.config_edit)

        # Дополнительные поля для GigaChat
        self.gigachat_auth_edit = QLineEdit()
        self.gigachat_auth_edit.setPlaceholderText("Base64 Authorization для GigaChat")
        form.addRow("GigaChat Authorization:", self.gigachat_auth_edit)
        self.gigachat_rquid_edit = QLineEdit()
        self.gigachat_rquid_edit.setPlaceholderText("Необязательно, будет сгенерирован автоматически")
        form.addRow("GigaChat RqUID:", self.gigachat_rquid_edit)

        # Универсальное поле для API-ключа
        self.secret_edit = QLineEdit()
        self.secret_edit.setPlaceholderText("API-ключ (будет сохранён в secure_store)")
        form.addRow("Секрет (API-ключ):", self.secret_edit)
        layout.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.validate_and_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def load_agent_data(self):
        self.id_edit.setText(self.agent["id"])
        self.name_edit.setText(self.agent.get("name", ""))
        idx = self.type_combo.findText(self.agent.get("type", "external"))
        if idx >= 0:
            self.type_combo.setCurrentIndex(idx)
        self.enabled_check.setChecked(self.agent.get("enabled", True))
        self.config_edit.setPlainText(json.dumps(self.agent.get("config", {}), ensure_ascii=False, indent=2))
        # Загружаем сохранённые ключи GigaChat (пустые, если их нет)
        auth = get_secret("gigachat", "authorization")
        rquid = get_secret("gigachat", "rquid")
        if auth:
            self.gigachat_auth_edit.setText(auth)
        if rquid:
            self.gigachat_rquid_edit.setText(rquid)
        # Универсальный секрет
        secret = get_secret("agent_secrets", self.agent.get("id", ""))
        if secret:
            self.secret_edit.setText(secret)

    def validate_and_accept(self):
        agent_id = self.id_edit.text().strip()
        name = self.name_edit.text().strip()
        agent_type = self.type_combo.currentText()
        enabled = self.enabled_check.isChecked()
        config_text = self.config_edit.toPlainText().strip()
        if not agent_id or not name:
            QMessageBox.warning(self, "Ошибка", "ID и название обязательны")
            return
        try:
            config = json.loads(config_text) if config_text else {}
        except json.JSONDecodeError:
            QMessageBox.warning(self, "Ошибка", "Конфигурация должна быть валидным JSON")
            return
        self.result_data = {
            "id": agent_id,
            "name": name,
            "type": agent_type,
            "enabled": enabled,
            "config": config
        }
        # Сохраняем ключи GigaChat, если поля заполнены (независимо от id/имени)
        auth = self.gigachat_auth_edit.text().strip()
        rquid = self.gigachat_rquid_edit.text().strip()
        if auth:
            save_secret("gigachat", "authorization", auth)
        if rquid:
            save_secret("gigachat", "rquid", rquid)
        # Сохраняем универсальный секрет
        secret = self.secret_edit.text().strip()
        if secret:
            save_secret("agent_secrets", agent_id, secret)
        self.accept()

    def get_data(self):
        return getattr(self, "result_data", None)


class AgentsTab(QWidget):
    """Вкладка управления агентами."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setup_ui()
        self.refresh()

    def setup_ui(self):
        layout = QVBoxLayout(self)

        # === Секция «Реестр агентов» ===
        agents_box = QGroupBox("Реестр агентов")
        agents_layout = QVBoxLayout(agents_box)

        self.table = QTableWidget()
        self.table.setColumnCount(4)
        self.table.setHorizontalHeaderLabels(["ID", "Название", "Тип", "Включен"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        agents_layout.addWidget(self.table)

        btn_layout = QHBoxLayout()
        self.add_btn = QPushButton("Добавить")
        self.add_btn.clicked.connect(self.add_agent)
        self.edit_btn = QPushButton("Редактировать")
        self.edit_btn.clicked.connect(self.edit_agent)
        self.delete_btn = QPushButton("Удалить")
        self.delete_btn.clicked.connect(self.delete_agent)
        self.toggle_btn = QPushButton("Включить/Выключить")
        self.toggle_btn.clicked.connect(self.toggle_agent)
        self.refresh_btn = QPushButton("Обновить")
        self.refresh_btn.clicked.connect(self.refresh)
        btn_layout.addWidget(self.add_btn)
        btn_layout.addWidget(self.edit_btn)
        btn_layout.addWidget(self.delete_btn)
        btn_layout.addWidget(self.toggle_btn)
        btn_layout.addWidget(self.refresh_btn)
        agents_layout.addLayout(btn_layout)
        layout.addWidget(agents_box)

        self._setup_browser_sites(layout)

    def _setup_browser_sites(self, layout):
        """Секция «Браузерные сайты» со статусами и кнопками."""
        sites_box = QGroupBox("Браузерные сайты (прогрев)")
        sites_outer = QVBoxLayout(sites_box)

        # AD.G: кнопка «Перезапустить все браузеры»
        restart_layout = QHBoxLayout()
        self.restart_all_btn = QPushButton("🔄 Перезапустить все браузеры")
        self.restart_all_btn.setToolTip(
            "Закрыть все Chromium, очистить Singleton, прогреть заново.\n"
            "Используй, если браузер завис, достигнут лимит чата или "
            "нужен чистый контекст."
        )
        self.restart_all_btn.clicked.connect(self._on_restart_all_browsers)
        restart_layout.addWidget(self.restart_all_btn)

        # BROWSER-CACHE-CLEANUP: очистка кэша профилей
        self.cache_cleanup_btn = QPushButton("🧹 Очистить кэш браузеров")
        self.cache_cleanup_btn.setToolTip(
            "Очищает безопасный кэш профилей Chromium "
            "(Cache, Code Cache, GPUCache, Service Worker).\n"
            "Освобождает место и ускоряет загрузку Qwen/DeepSeek.\n\n"
            "НЕ трогает: Cookies, Local Storage, IndexedDB, "
            "Login Data — сессия и логины остаются."
        )
        self.cache_cleanup_btn.clicked.connect(
            self._on_clean_browser_cache)
        restart_layout.addWidget(self.cache_cleanup_btn)

        restart_layout.addStretch()
        sites_outer.addLayout(restart_layout)

        self._browser_sites_container = QWidget()
        self._browser_sites_layout = QVBoxLayout(self._browser_sites_container)
        self._browser_sites_layout.setContentsMargins(0, 0, 0, 0)
        sites_outer.addWidget(self._browser_sites_container)
        layout.addWidget(sites_box)

        self._browser_rows = {}

        self._browser_timer = QTimer(self)
        self._browser_timer.timeout.connect(self._refresh_browser_statuses)
        self._browser_timer.start(2000)

        self._refresh_browser_statuses()

    def _on_restart_all_browsers(self):
        """AD.G: перезапускает все браузеры (kill + cleanup + warmup)."""
        reply = QMessageBox.question(
            self,
            "Перезапустить все браузеры?",
            "Все Chromium будут закрыты, Singleton очищены, "
            "затем браузеры прогреются заново.\n\n"
            "Контекст чатов с моделями будет сброшен.\n"
            "Продолжить?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        # 1. Закрыть Playwright-контексты
        try:
            from core.agents.browser_manager import close_browser
            close_browser()
        except Exception as e:
            QMessageBox.warning(
                self, "Ошибка",
                f"Не удалось закрыть браузер: {e}"
            )

        # 2. Добить висящие Chromium + почистить Singleton
        try:
            from core.browser_cleanup import cleanup_stale_browsers
            cleanup_stale_browsers()
        except Exception as e:
            QMessageBox.warning(
                self, "Ошибка",
                f"Не удалось очистить Chromium: {e}"
            )

        # 3. Сбросить статусы прогрева в idle
        try:
            from core.browser_warmup import reset_all
            reset_all()
        except Exception:
            pass

        # 4. Прогреть заново
        try:
            from core.browser_warmup import warmup_all_async
            warmup_all_async(callback=self._on_warmup_status)
        except Exception as e:
            QMessageBox.warning(
                self, "Ошибка",
                f"Не удалось запустить прогрев: {e}"
            )
            return

        QMessageBox.information(
            self, "Перезапуск",
            "Браузеры очищены. Прогрев запущен в фоне.\n"
            "Статусы обновятся через несколько секунд."
        )

    def _on_clean_browser_cache(self):
        """BROWSER-CACHE-CLEANUP: очистка безопасного кэша."""
        from PySide6.QtWidgets import QMessageBox as _QMB
        reply = _QMB.question(
            self,
            "Очистить кэш браузеров",
            "Очистить безопасный кэш всех профилей "
            "(Cache, Code Cache, GPUCache, Service Worker)?\n\n"
            "Освободит место и ускорит загрузку страниц.\n"
            "Cookies, Local Storage и логины НЕ трогаются.\n\n"
            "Рекомендуется делать, когда браузеры закрыты.",
            _QMB.Yes | _QMB.No,
        )
        if reply != _QMB.Yes:
            return
        try:
            from core.browser_cleanup import clean_browser_cache
            r = clean_browser_cache()
            freed_mb = r["freed_bytes"] / (1024 * 1024)
            msg = (
                f"Очищено папок: {r['cleaned_dirs']}\n"
                f"Освобождено: {freed_mb:.1f} МБ\n"
                f"Профилей затронуто: {len(r['profiles'])}"
            )
            if r["errors"]:
                msg += f"\n\n⚠️ Ошибок: {len(r['errors'])}"
                for e in r["errors"][:3]:
                    msg += f"\n  • {e[:120]}"
            _QMB.information(self, "Готово", msg)
        except Exception as e:
            _QMB.warning(
                self, "Ошибка",
                f"Не удалось очистить кэш:\n{e}"
            )

    def _on_warmup_status(self, site_id, info):
        """Колбэк прогрева — обновить UI (безопасно, через таймер)."""
        # Просто игнорируем — таймер _browser_timer обновит статусы
        pass

    def _status_text(self, status):
        return {
            ST_DISABLED: "⚪ выключен",
            ST_IDLE: "⚪ не прогрет",
            ST_LOADING: "🟡 загружается",
            ST_READY: "🟢 готов",
            ST_LOGIN: "🔴 нужен вход",
            ST_ERROR: "⚫ ошибка",
        }.get(status, f"❔ {status}")

    def _refresh_browser_statuses(self):
        try:
            statuses = get_all_statuses()
        except Exception:
            return
        for site_id, info in statuses.items():
            if site_id not in self._browser_rows:
                row = QWidget()
                h = QHBoxLayout(row)
                h.setContentsMargins(0, 0, 0, 0)
                name_lbl = QLabel(site_id)
                name_lbl.setMinimumWidth(120)
                status_lbl = QLabel("-")
                status_lbl.setMinimumWidth(180)
                warm_btn = QPushButton("Прогреть")
                warm_btn.setMaximumWidth(100)
                warm_btn.clicked.connect(
                    lambda _checked=False, s=site_id: self._warmup_one(s))
                cancel_btn = QPushButton("Отключить")
                cancel_btn.setMaximumWidth(100)
                cancel_btn.clicked.connect(
                    lambda _checked=False, s=site_id: self._cancel_one(s))
                h.addWidget(name_lbl)
                h.addWidget(status_lbl)
                h.addStretch()
                h.addWidget(warm_btn)
                h.addWidget(cancel_btn)
                self._browser_sites_layout.addWidget(row)
                self._browser_rows[site_id] = {
                    "status_lbl": status_lbl,
                    "warm_btn": warm_btn,
                }
            row_info = self._browser_rows[site_id]
            status = info.get("status", ST_IDLE)
            text = self._status_text(status)
            msg = info.get("message", "")
            if msg and status not in (ST_READY, ST_IDLE, ST_DISABLED, ST_LOADING):
                text = f"{text}  ({msg[:60]})"
            row_info["status_lbl"].setText(text)

            # Кнопка [Прогреть] активна только когда не идёт загрузка
            # и сайт не выключен
            btn = row_info.get("warm_btn")
            if btn is not None:
                if status == ST_LOADING:
                    btn.setEnabled(False)
                    btn.setText("Загружается...")
                elif status == ST_DISABLED:
                    btn.setEnabled(False)
                    btn.setText("Выключен")
                else:
                    btn.setEnabled(True)
                    btn.setText("Прогреть")

    def _warmup_one(self, site_id):
        try:
            # Если сайт завис в ST_LOADING больше 2 минут — сбрасываем
            status = get_all_statuses().get(site_id, {}).get("status", "")
            if status == ST_LOADING:
                # Пользователь жмёт Прогреть вручную — считаем это сбросом
                force_reset_site(site_id)
            warmup_site_async(site_id)
        except Exception as e:
            QMessageBox.warning(self, "Ошибка",
                                f"Не удалось запустить прогрев: {e}")

    def _cancel_one(self, site_id):
        try:
            cancel_warmup(site_id)
        except Exception:
            pass

    def refresh(self):
        agents = load_agents()
        self.table.setRowCount(len(agents))
        for row, agent in enumerate(agents):
            self.table.setItem(row, 0, QTableWidgetItem(agent["id"]))
            self.table.setItem(row, 1, QTableWidgetItem(agent.get("name", "")))
            self.table.setItem(row, 2, QTableWidgetItem(agent.get("type", "")))
            enabled_item = QTableWidgetItem("Да" if agent.get("enabled", False) else "Нет")
            enabled_item.setTextAlignment(Qt.AlignCenter)
            self.table.setItem(row, 3, enabled_item)

    def get_selected_agent_id(self):
        row = self.table.currentRow()
        if row < 0:
            return None
        return self.table.item(row, 0).text()

    def add_agent(self):
        dialog = AgentDialog(parent=self)
        if dialog.exec() == QDialog.Accepted:
            data = dialog.get_data()
            if data:
                try:
                    add_agent(data)
                    self.refresh()
                except ValueError as e:
                    QMessageBox.critical(self, "Ошибка", str(e))

    def edit_agent(self):
        agent_id = self.get_selected_agent_id()
        if not agent_id:
            QMessageBox.warning(self, "Ошибка", "Выберите агента")
            return
        agent = get_agent(agent_id)
        if agent:
            dialog = AgentDialog(agent=agent, parent=self)
            if dialog.exec() == QDialog.Accepted:
                data = dialog.get_data()
                if data:
                    # Удаляем старого и добавляем нового (простой способ обновления)
                    remove_agent(agent_id)
                    try:
                        add_agent(data)
                    except ValueError:
                        # если id не менялся, всё ок
                        pass
                    self.refresh()

    def delete_agent(self):
        agent_id = self.get_selected_agent_id()
        if not agent_id:
            QMessageBox.warning(self, "Ошибка", "Выберите агента")
            return
        reply = QMessageBox.question(self, "Подтверждение", f"Удалить агента '{agent_id}'?",
                                     QMessageBox.Yes | QMessageBox.No)
        if reply == QMessageBox.Yes:
            remove_agent(agent_id)
            self.refresh()

    def toggle_agent(self):
        agent_id = self.get_selected_agent_id()
        if not agent_id:
            QMessageBox.warning(self, "Ошибка", "Выберите агента")
            return
        agent = get_agent(agent_id)
        if agent:
            new_enabled = not agent.get("enabled", False)
            set_agent_enabled(agent_id, new_enabled)
            self.refresh()
