from PySide6.QtWidgets import (QMainWindow, QTabWidget, QMessageBox,
                                   QLabel, QPushButton)
from PySide6.QtCore import QThread, Signal, Slot, Qt, QTimer
from PySide6.QtGui import QShortcut, QKeySequence
from ui.tabs.project_tab import ProjectTab
from ui.tabs.dialog_tab import DialogTab
from ui.dialogs.chat_dialog import ChatDialog
from ui.tabs.logs_tab import LogsTab
from ui.tabs.agents_tab import AgentsTab
from ui.tabs.settings_tab import SettingsTab
from ui.tabs.command_tab import CommandTab
from ui.tabs.knowledge_tab import KnowledgeTab
from ui.tabs.resources_tab import ResourcesTab
from core.resource_manager import resource_manager
from core.memory import (
    load_project_memory,
    save_project_memory,
    append_project_history
)
from config.config_manager import get_setting
from core.state import AgentState
from db.database import save_task
from core.agents.browser_manager import close_browser
import traceback
import time


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("AI Link")
        # GEOMETRY: восстанавливаем размер/позицию из QSettings.
        # Если сохранёнки нет — дефолт 1200x800 (нормально, не мельчит).
        self._settings = None
        try:
            from PySide6.QtCore import QSettings
            self._settings = QSettings("AiLink", "MainWindow")
            geom = self._settings.value("geometry")
            if geom is not None:
                self.restoreGeometry(geom)
            else:
                self.resize(1200, 800)
        except Exception:
            self.resize(1200, 800)
        # Минимум — не мельчить и не ломать интерфейс
        try:
            self.setMinimumSize(600, 400)
        except Exception:
            pass

        self.tabs = QTabWidget()
        self.setCentralWidget(self.tabs)

        # Определяем режим
        app_mode = get_setting("app_mode", "minimal")

        # Создаём все вкладки
        self.project_tab = ProjectTab()
        self.command_tab = CommandTab()
        self.dialog_tab = DialogTab()
        # LR-UI-FINAL: «Открыть карту» из жёлтой панели
        try:
            self.dialog_tab.open_map_requested.connect(
                self._on_open_project_map
            )
        except Exception:
            pass
        # B2.1 (2026-10-07): вкладки Логи / Карта / Ресурсы убраны.
        #   • Логи → в командном пункте (там LogWidget).
        #   • Карта → встроена в командный пункт (embedded_tree).
        #   • Ресурсы → CPU/RAM в статус-баре.
        self.agents_tab = AgentsTab()
        self.settings_tab = SettingsTab()

        # PROXY (B3, 2026-10-07): кнопки в Проекте → методы Диалога.
        # Логика пока живёт в Диалоге, вызываем её через прокси.
        # Когда Диалог будет удалён — методы переедут сюда.
        try:
            self.project_tab.btn_start.clicked.connect(
                self.dialog_tab._on_start
            )
            self.project_tab.btn_improve.clicked.connect(
                self.dialog_tab._on_improve
            )
            self.project_tab.btn_continue_plan.clicked.connect(
                self.dialog_tab._on_continue_plan
            )
            self.project_tab.btn_stop.clicked.connect(
                self.dialog_tab._on_stop
            )
            self.project_tab.btn_history.clicked.connect(
                self.dialog_tab._on_show_history
            )
            self.project_tab.btn_audit.clicked.connect(
                self.dialog_tab._on_audit
            )
            self.project_tab.btn_open_report.clicked.connect(
                self.dialog_tab._on_open_audit_report
            )
            self.project_tab.btn_plan.clicked.connect(
                self.dialog_tab._on_open_audit_plan
            )
        except Exception as _e:
            print(f"[PROXY] ошибка подключения кнопок: {_e}")

        # UI-VISION (2026-10-10): кнопки КП → dialog_tab.
        try:
            self.command_tab.pause_btn.clicked.connect(
                self.dialog_tab._on_pause
            )
            self.command_tab.resume_btn.clicked.connect(
                self.dialog_tab._on_pause
            )
            self.command_tab.send_btn.clicked.connect(
                self._on_cmd_send
            )
            self.command_tab.open_project_btn.clicked.connect(
                self._on_cmd_open_project
            )
            self.command_tab.send_btn.setEnabled(False)
            self.command_tab.pause_btn.setEnabled(False)
            self.command_tab.resume_btn.setEnabled(False)
        except Exception as _e:
            print(f"[UI-VISION] ошибка подключения кнопок КП: {_e}")

        # Добавляем их в QTabWidget
        # B2 (2026-10-07): DialogTab больше не отдельная вкладка —
        # встроен в «Проект» под кнопками управления.
        try:
            self.project_tab.embed_widget(self.dialog_tab, stretch=1)
        except Exception as _e:
            print(f"[EMBED] ошибка встраивания DialogTab: {_e}")

        self.tabs.addTab(self.command_tab, "Командный пункт")
        self.tabs.addTab(self.project_tab, "Проект")
        # Диалог встроен в Проект, Логи/Карта/Ресурсы — тоже не вкладки.
        self.tabs.addTab(self.agents_tab, "Агенты")
        self.tabs.addTab(self.settings_tab, "Настройки")

        # Сохраняем индексы для удобства
        # B2.1: только 4 вкладки.
        # Логи/Карта/Ресурсы/Диалог — не вкладки.
        self.tab_indices = {
            "command": 0,
            "project": 1,
            "agents": 2,
            "settings": 3,
        }

        # Скрываем вкладки в зависимости от режима
        if app_mode == "minimal":
            # В minimal скрываем: Агенты, Настройки.
            for idx in ["agents", "settings"]:
                self.tabs.setTabVisible(self.tab_indices[idx], False)
        # medium / maximal: все видны

        # Горячая клавиша для показа настроек (если скрыты)
        self.shortcut_settings = QShortcut(QKeySequence("Ctrl+P"), self)
        self.shortcut_settings.activated.connect(self.show_settings_tab)

        # Подключаем сигнал запуска

        # Храним ссылку на текущий поток
        self.current_worker = None

        # Статус-бар с загрузкой системы
        self.statusBar().showMessage("Готов")
        # A3 (2026-10-07): имя пользователя в статус-баре.
        #   Клик → диалог ввода. Сохраняется в config.json.
        self.user_btn = QPushButton("👤 Гость")
        self.user_btn.setFlat(True)
        self.user_btn.setToolTip(
            "Кликните, чтобы ввести своё имя.\n"
            "Используется для различения своих и чужих проектов."
        )
        self.user_btn.setStyleSheet(
            "QPushButton { border: none; color: #444; padding: 2px 6px; }"
            "QPushButton:hover { background: #e0e0e0; border-radius: 3px; }"
        )
        self.user_btn.clicked.connect(self._on_change_user)
        self.statusBar().addPermanentWidget(self.user_btn)

        self.resource_label = QLabel("CPU: --% | RAM: --%")
        self.statusBar().addPermanentWidget(self.resource_label)

        # Индикатор задачи в статус-баре
        self.task_status_label = QLabel("")
        self.task_status_label.setStyleSheet(
            "padding: 2px 8px; color: #333; font-weight: bold;"
        )
        self.statusBar().addPermanentWidget(self.task_status_label)

        # Кнопка «Поболтать» в статус-баре
        self.chat_btn = QPushButton("💬 Поболтать")
        self.chat_btn.setToolTip("Открыть окно для простого разговора с ИИ")
        self.chat_btn.clicked.connect(self._open_chat)
        self.statusBar().addPermanentWidget(self.chat_btn)

        # Обновляем метку имени (из config.json)
        try:
            self._update_user_label()
        except Exception:
            pass

        # Подключаем статус задачи из вкладки Диалог
        try:
            self.dialog_tab.status_changed.connect(self._on_task_status_changed)
        except Exception:
            pass

        # Дублируем строки фаз из «Диалога» в «Лог проекта».
        # (Вкладка «Логи» наполняется через QtLogHandler из logging —
        #  там же, где и остальные технические события.)
        try:
            # B4 (2026-10-07): логи из Диалога больше не идут в Проект
            # (там чёрный лог убран). Проксируем в командный пункт.
            self.dialog_tab.log_line.connect(self.command_tab.append_log)
        except Exception:
            pass

        # Таймер обновления ресурсов (запустим при показе окна)
        self.resource_timer = QTimer(self)
        self.resource_timer.timeout.connect(self.update_resource_status)
        # Не запускаем здесь, ждём showEvent

        # Связка вкладок: смена папки в Проекте → обновление Диалога
        try:
            self.project_tab.folder_edit.textChanged.connect(
                self._on_project_folder_changed
            )
            _initial = self.project_tab.folder_edit.text().strip()
            if _initial:
                self.dialog_tab.set_project_dir(_initial)
                if hasattr(self, "command_tab"):
                    self.command_tab.set_project_dir(_initial)
        except Exception:
            pass

        # Связка: выбор проекта в галерее → загрузка в «Проект»
        try:
            # B2.1: карта теперь в command_tab.embedded_tree.
            self.command_tab.embedded_tree.project_loaded.connect(
                self._on_project_loaded_from_gallery
            )
        except Exception:
            pass

    def _on_project_folder_changed(self, path: str):
        """При смене папки проекта — обновляем DialogTab и Карту проекта."""
        try:
            self.dialog_tab.set_project_dir(path.strip())
        except Exception:
            pass
        try:
            if hasattr(self, "knowledge_tab"):
                self.command_tab.embedded_tree.set_project_dir(path.strip())
            if hasattr(self, "command_tab"):
                self.command_tab.set_project_dir(path.strip())
        except Exception:
            pass
        # A3 (2026-10-07): обновляем состояние кнопок в Проекте.
        try:
            if hasattr(self, "project_tab"):
                self.project_tab._update_buttons_by_folder()
        except Exception:
            pass


    # === A3 (2026-10-07): имя пользователя ===
    def _update_user_label(self):
        """Обновляет текст кнопки имени из config.json."""
        try:
            from config.config_manager import get_setting
            name = (get_setting("user_name", "") or "").strip()
        except Exception:
            name = ""
        if name:
            self.user_btn.setText(f"👤 {name}")
        else:
            self.user_btn.setText("👤 Гость")

    def _on_change_user(self):
        """Диалог ввода имени пользователя."""
        from PySide6.QtWidgets import QInputDialog
        try:
            from config.config_manager import get_setting, set_setting
            current = (get_setting("user_name", "") or "").strip()
        except Exception:
            current = ""

        name, ok = QInputDialog.getText(
            self,
            "Имя пользователя",
            "Как к вам обращаться?\n"
            "(используется для различения своих и чужих проектов)",
            text=current,
        )
        if not ok:
            return
        try:
            from config.config_manager import set_setting
            set_setting("user_name", name.strip())
        except Exception as e:
            print(f"[USER] не удалось сохранить: {e}")
        self._update_user_label()

    @Slot(str)
    def on_log(self, message: str):
        """Передаёт логи в обе вкладки."""
        self.project_tab.append_log(message)
        self.command_tab.append_log(message)

    @Slot(dict)
    def on_success(self, result: dict):
        duration = time.time() - getattr(self, "task_start_time", time.time())
        # Если задача диалоговая, выводим только текст ответа
        if result.get("execution_result", {}).get("type") == "dialog":
            response_text = result.get("execution_result", {}).get("response", "")
            self.project_tab.append_log(f"Ответ:\n{response_text}")
            self.project_tab.set_status("Готово")
            self.project_tab.set_progress_visible(False)
            # Записываем статистику
            task_text = result.get("task", "")
            resource_manager.record_task(True, duration, task_text)
            # Сохраняем в БД
            try:
                save_task(task_text, result.get("project_dir", ""), response_text, "")
                self.command_tab.append_log("Диалоговая задача сохранена в историю.")
            except Exception as e:
                self.command_tab.append_log(f"Ошибка сохранения в БД: {e}")
            return
        # Выводим план, если он есть
        plan = result.get("plan", [])
        if plan:
            from core.planner import format_plan_for_display
            self.project_tab.append_log("План:\n" + format_plan_for_display(plan))
            # Сохраняем план в проектную память
            try:
                memory = load_project_memory(result.get("project_dir", ""))
                memory["plan"] = plan
                save_project_memory(result.get("project_dir", ""), memory)
            except Exception as e:
                self.command_tab.append_log(f"Ошибка сохранения плана в память: {e}")
        files = result.get("files_created", [])
        # Записываем успех в историю проекта
        try:
            append_project_history(
                result.get("project_dir", ""),
                {
                    "event": "success",
                    "files": files,
                    "duration": duration,
                    "time": time.strftime("%Y-%m-%d %H:%M:%S")
                }
            )
        except Exception as e:
            self.command_tab.append_log(f"Ошибка записи в память проекта: {e}")
        if files:
            files_str = "\n".join(f"  📄 {f}" for f in files)
            self.project_tab.append_log(f"Успех! Созданы файлы:\n{files_str}")
        else:
            files = result.get("files_created", [])
        if files:
            files_str = "\n".join(f"  📄 {f}" for f in files)
            self.project_tab.append_log(f"Успех! Созданы файлы:\n{files_str}")
        else:
            self.project_tab.append_log("Успех! Файлы созданы.")
        self.project_tab.set_status("Готово")
        self.project_tab.set_progress_visible(False)

        # Записываем статистику
        task_text = result.get("task", "")
        resource_manager.record_task(True, duration, task_text)

        # Сохраняем в БД
        task = result.get("task", "")
        project_dir = result.get("project_dir", "")
        execution_result = result.get("execution_result", "")
        error = result.get("error", "")
        try:
            save_task(task, project_dir, str(execution_result), error)
            self.command_tab.append_log("Задача сохранена в историю.")
        except Exception as e:
            self.command_tab.append_log(f"Ошибка сохранения в БД: {e}")

    @Slot(str)
    def on_error(self, error_msg: str):
        duration = time.time() - getattr(self, "task_start_time", time.time())
        # Записываем ошибку в историю проекта
        try:
            append_project_history(
                self.current_worker.project_dir if self.current_worker else "",
                {
                    "event": "error",
                    "error": error_msg,
                    "duration": duration,
                    "time": time.strftime("%Y-%m-%d %H:%M:%S")
                }
            )
        except Exception as e:
            self.command_tab.append_log(f"Ошибка записи в память проекта: {e}")
        self.project_tab.append_log(f"Ошибка: {error_msg}")
        self.project_tab.set_status("Ошибка")
        self.project_tab.set_progress_visible(False)

        # Записываем статистику
        task_text = self.current_worker.task if self.current_worker else ""
        resource_manager.record_task(False, duration, task_text)

        # Сохраняем ошибку в БД (task и project_dir возьмём из worker)
        if self.current_worker:
            try:
                save_task(self.current_worker.task, self.current_worker.project_dir, "", error_msg)
                self.command_tab.append_log("Ошибка сохранена в историю.")
            except Exception as e:
                self.command_tab.append_log(f"Ошибка сохранения в БД: {e}")

    def update_resource_status(self):
        """Обновляет отображение загрузки системы в статус-баре."""
        usage = resource_manager.get_system_usage()
        self.resource_label.setText(f"CPU: {usage['cpu']:.0f}% | RAM: {usage['memory']:.0f}%")

    def show_settings_tab(self):
        """Показывает вкладку Настройки, если она скрыта, и переключается на неё."""
        idx = self.tab_indices["settings"]
        if not self.tabs.isTabVisible(idx):
            self.tabs.setTabVisible(idx, True)
        self.tabs.setCurrentIndex(idx)

    def _on_cmd_send(self):
        """UI-VISION: отправить сообщение из КП → dialog_tab."""
        try:
            text = self.command_tab.input_field.text().strip()
            if not text:
                return
            self.dialog_tab._on_send_message(text)
            self.command_tab.input_field.clear()
        except Exception as _e:
            print(f"[UI-VISION] ошибка отправки из КП: {_e}")

    def _on_cmd_open_project(self):
        """UI-VISION: открыть папку проекта."""
        try:
            self.project_tab.open_folder()
        except Exception:
            pass

    def _on_task_status_changed(self, text: str):
        """Обновляет индикатор задачи в статус-баре + кнопки КП."""
        if not text or text == "ожидание":
            self.task_status_label.setText("")
            _s = ""
        else:
            self.task_status_label.setText(f"⚙ {text}")
            _s = (text or "").lower()

        _running = "выполняется" in _s
        _paused = "пауза" in _s
        _waiting = "ждёт ответа" in _s

        try:
            self.command_tab.pause_btn.setEnabled(_running)
            self.command_tab.resume_btn.setEnabled(_paused)
            self.command_tab.send_btn.setEnabled(_paused or _waiting)
            _ph = "Уточнение к задаче (на паузе)..."
            if _waiting:
                _ph = "❓ Ответ на вопрос Link..."
            self.command_tab.input_field.setPlaceholderText(_ph)
        except Exception:
            pass

        # BTN-STOP (2026-10-11): кнопка «Стоп» в Проекте активна
        # только когда прогон идёт или на паузе. Раньше — всегда.
        _active = _running or _paused or _waiting
        try:
            self.project_tab.btn_stop.setEnabled(_active)
        except Exception:
            pass

    def _on_project_loaded_from_gallery(self, path: str):
        """Пользователь выбрал проект в галерее — устанавливаем папку."""
        if not path:
            return
        try:
            # Установка пути в project_tab вызовет textChanged →
            # _on_project_folder_changed → обновит dialog_tab и knowledge_tab
            self.project_tab.folder_edit.setText(path)
        except Exception:
            pass
        # Переключаемся на вкладку «Проект», чтобы пользователь видел смену
        try:
            self.tabs.setCurrentIndex(self.tab_indices["project"])
        except Exception:
            pass

    def _open_chat(self):
        """Открывает окно «Поболтать»."""
        if not hasattr(self, "_chat_dialog") or self._chat_dialog is None:
            self._chat_dialog = ChatDialog(self)
        self._chat_dialog.show()
        self._chat_dialog.raise_()
        self._chat_dialog.activateWindow()

    def _on_open_project_map(self):
        """LR-UI-FINAL: переключить на «Карту проекта» в режиме правки."""
        try:
            idx = self.tabs.indexOf(self.knowledge_tab)
            if idx >= 0:
                self.tabs.setCurrentIndex(idx)
            # Передать knowledge_tab сигнал «режим редактирования»
            try:
                if hasattr(self.knowledge_tab, "enter_edit_mode"):
                    self.command_tab.embedded_tree.enter_edit_mode()
            except Exception:
                pass
        except Exception:
            pass

    def showEvent(self, event):
        """Запускаем таймеры при отображении окна."""
        if not self.resource_timer.isActive():
            self.resource_timer.start(2000)
        # B2.1: resources_tab убран (CPU/RAM в статус-баре).
        super().showEvent(event)

    def closeEvent(self, event):
        """При закрытии окна останавливаем поток и освобождаем ресурсы."""
        # Останавливаем фоновую задачу из DialogTab
        try:
            if hasattr(self, "dialog_tab") and self.dialog_tab._task:
                if self.dialog_tab._task.is_alive():
                    self.dialog_tab._task.stop()
                    self.dialog_tab._task.wait(timeout=3.0)
        except Exception:
            pass

        if hasattr(self, "resource_timer"):
            self.resource_timer.stop()
        try:
            close_browser()
        except Exception:
            pass
        # Добиваем висящие Chromium от наших профилей.
        # Сторонние Chromium не задеваем — pkill фильтрует
        # по пути ~/.ai_link/browser_profiles/.
        try:
            from core.browser_cleanup import cleanup_stale_browsers
            cleanup_stale_browsers()
        except Exception:
            pass
        if self.current_worker and self.current_worker.isRunning():
            # Даём потоку 2 секунды на завершение
            self.current_worker.quit()
            self.current_worker.wait(2000)
            if self.current_worker.isRunning():
                self.current_worker.terminate()
                self.current_worker.wait(1000)
        event.accept()
