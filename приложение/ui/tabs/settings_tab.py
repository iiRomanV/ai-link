from PySide6.QtWidgets import (QWidget, QVBoxLayout, QFormLayout, QLineEdit,
                               QCheckBox, QSpinBox, QComboBox, QPushButton,
                               QMessageBox, QHBoxLayout, QGroupBox, QFileDialog,
                               QLabel)
from PySide6.QtGui import QColor
from ui.dialogs.calibration_dialog import CalibrationDialog
from ui.dialogs.benchmark_dialog import BenchmarkDialog
from ui.dialogs.env_check_dialog import EnvCheckDialog
from PySide6.QtCore import Qt
import subprocess
from pathlib import Path as _Path
from config.config_manager import load_config, save_config

# ROLES (2026-10-01): роли моделей
from core.capability_benchmark import (
    ASSIGNMENT_KEYS, PHASE_INFO, phase_tooltip,
    list_available_agents, load_assignments, save_assignments,
    set_assignment, MONOPOLIST_ROLES,
)

class SettingsTab(QWidget):
    """Вкладка "Настройки": редактирование глобальных параметров."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setup_ui()
        self.load_values()
        # ROLES (2026-10-01): гарантированный пересчёт combo ролей
        # ПОСЛЕ загрузки значений — иначе монополисты не подсветятся.
        try:
            self._refresh_all_role_combos()
        except Exception:
            pass

    def setup_ui(self):
        layout = QVBoxLayout(self)

        # === Секция «Общие» ===
        general_box = QGroupBox("Общие настройки")
        general_form = QFormLayout(general_box)

        self.research_checkbox = QCheckBox("Включить поиск (DuckDuckGo) по умолчанию [эксперимент]")
        self.research_checkbox.setToolTip("Будет использоваться при запуске, если не изменено на вкладке Проект")
        general_form.addRow(self.research_checkbox)

        self.warmup_check = QCheckBox("Прогревать браузеры при запуске приложения")
        self.warmup_check.setToolTip("При старте AI Link откроет браузеры для включённых сайтов (DeepSeek, Qwen). Прогрев идёт в фоне.")
        general_form.addRow(self.warmup_check)

        self.minimize_browser_check = QCheckBox(
            "Сворачивать окно браузера, чтобы не мешало работать"
        )
        self.minimize_browser_check.setToolTip(
            "Если включено — при работе AI Link окна Chromium "
            "будут свёрнуты. Отключите, если нужно видеть, что "
            "происходит в браузере (например, вводите капчу)."
        )
        general_form.addRow(self.minimize_browser_check)

        self.debug_logs_check = QCheckBox("Сохранять отладочные логи браузера")
        self.debug_logs_check.setToolTip(
            "Если включено — AI Link сохраняет sent_*.log, cycle_*.log, "
            "poll_*.log в .diagnostics/. По умолчанию выключено, "
            "чтобы не забивать диск."
        )
        general_form.addRow(self.debug_logs_check)

        # Кнопка открытия папки с логами
        logs_layout = QHBoxLayout()
        self.open_logs_btn = QPushButton("Открыть папку с логами")
        self.open_logs_btn.setToolTip(
            "Открывает папку .diagnostics/ в файловом менеджере."
        )
        self.open_logs_btn.clicked.connect(self._open_logs_folder)
        logs_layout.addWidget(self.open_logs_btn)
        logs_layout.addStretch()
        general_form.addRow("", self._wrap_layout(logs_layout))

        layout.addWidget(general_box)

        # === Секция «Проекты» ===
        projects_box = QGroupBox("Проекты")
        projects_form = QFormLayout(projects_box)

        self.projects_dir_edit = QLineEdit()
        self.projects_dir_edit.setPlaceholderText(
            "Папка, где AI Link ищет проекты (галерея)"
        )
        self.projects_dir_edit.setToolTip(
            "Здесь AI Link ищет проекты для галереи «Карты проекта». "
            "Внутри этой папки находятся подпапки с .ai_link/plan.json."
        )
        projects_form.addRow("Папка проектов:", self.projects_dir_edit)

        projects_btn_layout = QHBoxLayout()
        self.projects_browse_btn = QPushButton("Обзор...")
        self.projects_browse_btn.clicked.connect(self._browse_projects_dir)
        self.projects_open_btn = QPushButton("Открыть")
        self.projects_open_btn.clicked.connect(self._open_projects_dir)
        projects_btn_layout.addWidget(self.projects_browse_btn)
        projects_btn_layout.addWidget(self.projects_open_btn)
        projects_btn_layout.addStretch()
        projects_form.addRow("", self._wrap_layout(projects_btn_layout))

        layout.addWidget(projects_box)

        # === Секция «Research» ===
        research_box = QGroupBox("Поиск (research) [эксперимент]")
        research_form = QFormLayout(research_box)

        self.engine_combo = QComboBox()
        self.engine_combo.addItems(["duckduckgo"])
        self.engine_combo.setToolTip(
            "Движок поиска для research-фазы. "
            "Пока экспериментальный — research в оркестраторе не активирован. "
            "Планируется в этапе 8.5 (Brave, Google, Perplexity)."
        )
        self.engine_combo.setEnabled(False)
        research_form.addRow("Поисковый движок:", self.engine_combo)

        self.max_attempts_spin = QSpinBox()
        self.max_attempts_spin.setRange(1, 10)
        self.max_attempts_spin.setToolTip("Максимальное количество попыток выполнения по умолчанию")
        research_form.addRow("Макс. попыток:", self.max_attempts_spin)

        layout.addWidget(research_box)

        # === Секция «Режим работы» ===
        mode_box = QGroupBox("Режим работы")
        mode_form = QFormLayout(mode_box)

        self.mode_combo = QComboBox()
        self.mode_combo.addItems(["minimal", "medium", "maximal"])
        self.mode_combo.setToolTip("Режим работы (влияет на видимость вкладок)")
        mode_form.addRow("Режим:", self.mode_combo)

        layout.addWidget(mode_box)

        # === Секция «Инструменты» ===
        tools_box = QGroupBox("Инструменты")
        tools_layout = QHBoxLayout(tools_box)
        self.calibrate_btn = QPushButton("Калибровать браузерные сайты")
        self.calibrate_btn.setToolTip("Открыть браузер и подобрать селекторы для Qwen/Perplexity/др.")
        self.calibrate_btn.clicked.connect(self._open_calibration)
        tools_layout.addWidget(self.calibrate_btn)

        self.benchmark_btn = QPushButton("Тест моделей ИИ")
        self.benchmark_btn.setToolTip(
            "Прогнать 10 коротких задач через выбранные модели. "
            "Результат — в ~/.ai_link/brain/capabilities.json."
        )
        self.benchmark_btn.clicked.connect(self._open_benchmark)
        tools_layout.addWidget(self.benchmark_btn)

        self.env_check_btn = QPushButton("Проверить окружение")
        self.env_check_btn.setToolTip(
            "Показать, что установлено в системе: git, node, godot, "
            "ffmpeg, PDF/Word/Excel-библиотеки, голосовые движки и т.д. "
            "Можно отметить отсутствующее и установить."
        )
        self.env_check_btn.clicked.connect(self._open_env_check)
        tools_layout.addWidget(self.env_check_btn)

        tools_layout.addStretch()
        layout.addWidget(tools_box)

        # === Секция «Роли» (ROLES 2026-10-01) ===
        roles_box = QGroupBox("Роли моделей")
        roles_form = QFormLayout(roles_box)

        roles_hint = QLabel(
            "Выберите модель для каждой роли. «Мозг» и «Поболтать» — "
            "монополисты: если модель занята ими, она больше не доступна "
            "для других ролей (серая внизу списка)."
        )
        roles_hint.setWordWrap(True)
        roles_hint.setStyleSheet("color: gray; padding: 2px;")
        roles_form.addRow(roles_hint)

        self._role_combos = {}
        for _key in ASSIGNMENT_KEYS:
            _info = PHASE_INFO.get(_key, {})
            _label_text = _info.get("title", _key)
            _tip = phase_tooltip(_key)

            _combo = QComboBox()
            _combo.setMinimumWidth(280)
            _combo.setToolTip(_tip)

            _lbl = QLabel(_label_text + ":")
            _lbl.setToolTip(_tip)

            _combo.currentIndexChanged.connect(
                lambda _idx, k=_key: self._on_role_changed(k)
            )
            self._role_combos[_key] = _combo
            roles_form.addRow(_lbl, _combo)

        layout.addWidget(roles_box)
        self._roles_box = roles_box

        # === Кнопки управления ===
        buttons_layout = QHBoxLayout()
        self.save_btn = QPushButton("Сохранить")
        self.save_btn.clicked.connect(self.save_settings)
        self.reload_btn = QPushButton("Сбросить")
        self.reload_btn.clicked.connect(self.load_values)
        buttons_layout.addWidget(self.save_btn)
        buttons_layout.addWidget(self.reload_btn)
        buttons_layout.addStretch()
        layout.addLayout(buttons_layout)

        layout.addStretch()


    def _on_role_changed(self, key: str):
        """ROLES (2026-10-01): сохраняет назначение при смене combo."""
        combo = self._role_combos.get(key)
        if combo is None:
            return
        value = combo.currentData()
        try:
            ok = set_assignment(key, value)
        except Exception as e:
            QMessageBox.warning(self, "Ошибка",
                                f"Не удалось сохранить роль {key}: {e}")
            return
        if not ok:
            QMessageBox.warning(self, "Ошибка",
                                f"Роль {key} не сохранилась (неизвестный ключ).")
            return
        # Пересчитываем все combo (монополисты могли измениться)
        try:
            self._refresh_all_role_combos()
        except Exception:
            pass

    def _refresh_all_role_combos(self):
        """ROLES (2026-10-01): пересчитывает все combo ролей.

        - Свободные модели сверху
        - Со скобками "(coding,review)" — где ещё используется
        - Монополисты (brain, dialog) — серые, внизу, нельзя выбрать
        """
        data = load_assignments()
        assignments = data.get("assignments", {}) or {}
        brain_agent = assignments.get("brain")
        dialog_agent = assignments.get("dialog")
        agents = list_available_agents()

        for key, combo in self._role_combos.items():
            combo.blockSignals(True)
            current = assignments.get(key)
            combo.clear()
            combo.addItem("Не выбрано", None)

            avail = []
            blocked = []
            for a in agents:
                aid = a["id"]
                name = a["name"]

                # Монополист?
                is_monopolist = False
                if key not in ("brain", "dialog"):
                    if aid and (aid == brain_agent or aid == dialog_agent):
                        is_monopolist = True

                # RU-NAMES (2026-10-01): русские названия ролей
                # в скобках вместо ключей (coding → Код).
                used_in_keys = sorted(
                    k for k, v in assignments.items()
                    if v == aid and k != key
                )
                if used_in_keys:
                    used_in_titles = []
                    for _k in used_in_keys:
                        _t = PHASE_INFO.get(_k, {}).get("title", _k)
                        used_in_titles.append(_t)
                    name = name + " (" + ",".join(used_in_titles) + ")"

                if not a.get("enabled", True):
                    name = name + "  [выкл]"

                if is_monopolist:
                    blocked.append((name, aid))
                else:
                    avail.append((name, aid))

            for n, aid in avail:
                combo.addItem(n, aid)
            for n, aid in blocked:
                combo.addItem(n, aid)
                model = combo.model()
                it = model.item(combo.count() - 1)
                it.setEnabled(False)
                it.setForeground(QColor("gray"))
                # A.2 (2026-10-04): tooltip с причиной блокировки.
                # Модель занята монопольной ролью (Мозг/Диалог).
                if aid and aid == brain_agent:
                    _why = "роль «Мозг»"
                elif aid and aid == dialog_agent:
                    _why = "роль «Диалог»"
                else:
                    _why = "монопольную роль"
                _tip_blocked = (
                    f"Недоступно для роли «{_label_text}»:\n"
                    f"модель уже занимает {_why}.\n\n"
                    f"«Мозг» и «Диалог» — монополисты, занимают "
                    f"браузер (для периодического чата). "
                    f"Снимите модель с {_why}, чтобы использовать "
                    f"её здесь."
                )
                it.setToolTip(_tip_blocked)
                # ROLES (2026-10-01): пометка для тестов/отладки
                it.setData("__monopolist__", Qt.UserRole + 100)

            idx = 0
            if current:
                for i in range(combo.count()):
                    if combo.itemData(i) == current:
                        idx = i
                        break
            combo.setCurrentIndex(idx)
            combo.blockSignals(False)

    def _browse_projects_dir(self):
        """Выбор папки проектов через диалог."""
        current = self.projects_dir_edit.text().strip()
        folder = QFileDialog.getExistingDirectory(
            self, "Папка проектов AI Link",
            current or str(_Path.home())
        )
        if folder:
            self.projects_dir_edit.setText(folder)

    def _open_projects_dir(self):
        """Открывает папку проектов в файловом менеджере."""
        folder = self.projects_dir_edit.text().strip()
        if not folder:
            QMessageBox.warning(self, "Ошибка",
                                "Папка проектов не выбрана.")
            return
        if not _Path(folder).exists():
            QMessageBox.warning(self, "Ошибка",
                                f"Папка не существует:\n{folder}")
            return
        try:
            import subprocess
            subprocess.Popen(["xdg-open", folder])
        except Exception as e:
            QMessageBox.warning(self, "Ошибка",
                                f"Не удалось открыть папку:\n{e}")

    def _wrap_layout(self, layout):
        """Оборачивает QHBoxLayout в QWidget, чтобы можно было в QFormLayout."""
        from PySide6.QtWidgets import QWidget as _W
        w = _W()
        w.setLayout(layout)
        return w

    def _open_logs_folder(self):
        """Открывает папку .diagnostics/ в файловом менеджере."""
        diagnostics = _Path.home() / ".."  # placeholder
        # Правильный путь — относительно проекта AI Link
        app_dir = _Path(__file__).resolve().parent.parent.parent
        diag_dir = app_dir / ".diagnostics"
        if not diag_dir.exists():
            diag_dir.mkdir(parents=True, exist_ok=True)
        try:
            subprocess.Popen(["xdg-open", str(diag_dir)])
        except Exception as e:
            QMessageBox.warning(self, "Ошибка",
                                f"Не удалось открыть папку: {e}")

    def load_values(self):
        """Загружает текущие настройки из конфига в поля."""
        config = load_config()
        self.research_checkbox.setChecked(config.get("research_enabled", True))
        self.warmup_check.setChecked(config.get("warmup_on_start", True))
        self.minimize_browser_check.setChecked(config.get("minimize_browser", True))
        self.debug_logs_check.setChecked(config.get("debug_logs", False))
        self.projects_dir_edit.setText(config.get("projects_dir", ""))
        engine = config.get("research_engine", "duckduckgo")
        idx = self.engine_combo.findText(engine)
        if idx >= 0:
            self.engine_combo.setCurrentIndex(idx)
        self.max_attempts_spin.setValue(config.get("max_attempts", 1))
        mode = config.get("app_mode", "minimal")
        idx_mode = self.mode_combo.findText(mode)
        if idx_mode >= 0:
            self.mode_combo.setCurrentIndex(idx_mode)
        # ROLES (2026-10-01): обновляем combo ролей
        try:
            self._refresh_all_role_combos()
        except Exception:
            pass

    def save_settings(self):
        """Сохраняет настройки, СОХРАНЯЯ остальные ключи конфига."""
        config = load_config()  # читаем текущий, чтобы не потерять agents и др.
        config.update({
            "research_enabled": self.research_checkbox.isChecked(),
            "research_engine": self.engine_combo.currentText(),
            "max_attempts": self.max_attempts_spin.value(),
            "app_mode": self.mode_combo.currentText(),
            "use_firejail": self.firejail_check.isChecked(),
            "warmup_on_start": self.warmup_check.isChecked(),
            "minimize_browser": self.minimize_browser_check.isChecked(),
            "debug_logs": self.debug_logs_check.isChecked(),
            "projects_dir": self.projects_dir_edit.text().strip(),
        })
        try:
            save_config(config)
            QMessageBox.information(self, "Успех", "Настройки сохранены.\nНекоторые параметры применятся после перезапуска приложения.")
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", f"Не удалось сохранить: {e}")

    def _open_calibration(self):
        dlg = CalibrationDialog(parent=self)
        dlg.exec()

    def _open_env_check(self):
        """A.3.2: диалог проверки окружения."""
        try:
            dlg = EnvCheckDialog(self)
            dlg.exec()
        except Exception as e:
            QMessageBox.warning(self, "Ошибка",
                                f"Не удалось открыть проверку окружения: {e}")

    def _open_benchmark(self):
        dlg = BenchmarkDialog(parent=self)
        dlg.exec()
