"""Командный пункт (A.5, 2026-10-05).

Этап 1: каркас на QDockWidget.

Особенности:
  • Шапка (фаза/агент/CPU) — фиксированная, наверху.
  • 3 панели-дока: Дерево плана, Сцена, Логи.
  • Панели можно: отцепить (🔲), перетащить, развернуть на весь экран,
    свернуть обратно. Расположение сохраняется между запусками.
  • Панель управления (кнопки + поле ввода) — центральная.
"""
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QCheckBox,
    QFrame, QPlainTextEdit, QPushButton, QLineEdit,
    QMainWindow, QDockWidget, QSizePolicy,
)
from PySide6.QtCore import Qt, QSize, QTimer
from PySide6.QtGui import QFont, QPixmap, QMovie

from ui.tabs.knowledge_tab import KnowledgeTab
from ui.widgets.log_widget import LogWidget


class CommandTab(QWidget):
    """Обёртка: шапка + QMainWindow с доками."""

    def __init__(self, parent=None):
        super().__init__(parent)
        # Рабочий проект (тот, над которым Link работает).
        # «Проекты» — только предпросмотр, не меняет это значение.
        self._work_project_dir = ""
        # 6.6 (2026-10-07): текущие активные слоты сцены.
        # None → все «спят». Иначе список id-слотов.
        self._current_active_slot = None
        # BUG-FD-LEAK (2026-10-07): кэш QMovie — чтобы не создавать
        # заново для того же файла каждые 2 сек (утечка fd!).
        self._movie_cache: dict = {}
        self._setup_ui()

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(6)

        # Шапка — фиксирована сверху
        root.addWidget(self._make_header())

        # Внутренний QMainWindow — только для доков
        self._inner = QMainWindow()
        self._inner.setDockOptions(
            QMainWindow.AllowNestedDocks
            | QMainWindow.AllowTabbedDocks
            | QMainWindow.AnimatedDocks
        )
        self._inner.setDocumentMode(True)

        # Разделители между доками — тонкие, пунктирные, hover-подсветка
        self._inner.setStyleSheet("""
            QMainWindow::separator {
                background: #d8d8d8;
                width: 3px;
                height: 3px;
            }
            QMainWindow::separator:hover {
                background: #808080;
            }
        """)

        # Центральный виджет — ПУСТАЯ заглушка с минимальным sizeHint.
        # Если его не задать — Qt странно ведёт себя с доками.
        _placeholder = QWidget()
        _placeholder.setSizePolicy(
            QSizePolicy.Ignored, QSizePolicy.Ignored
        )
        _placeholder.setMinimumSize(0, 0)
        self._inner.setCentralWidget(_placeholder)

        # Док 1: Дерево плана (слева)
        self._tree_dock = self._make_dock(
            "📋 Дерево плана", self._make_tree_panel()
        )
        self._inner.addDockWidget(Qt.LeftDockWidgetArea, self._tree_dock)
        # Иконка ↻ в заголовке дока
        self._add_refresh_to_dock_title(self._tree_dock)
        # Кнопки дерева — только при развороте
        self._bind_expand_buttons(
            self._tree_dock, self._tree_extra_btns
        )
        # При прицепке обратно — вернуть план РАБОЧЕГО проекта
        # (если был предпросмотр чужого).
        self._tree_dock.topLevelChanged.connect(
            self._on_tree_dock_float
        )

        # Док 2: Сцена (справа)
        self._scene_dock = self._make_dock(
            "🎭 Сцена", self._make_scene_panel()
        )
        self._inner.addDockWidget(Qt.RightDockWidgetArea, self._scene_dock)
        # Кнопки сцены — только при развороте
        self._bind_expand_buttons(
            self._scene_dock, self._scene_extra_btns
        )

        # Док 3: Логи (снизу)
        self._log_dock = self._make_dock(
            "📜 Логи", self._make_log_panel()
        )
        self._inner.addDockWidget(Qt.BottomDockWidgetArea, self._log_dock)
        # Кнопки логов — только при развороте
        self._bind_expand_buttons(
            self._log_dock, self._logs_extra_btns
        )

        # Начальные пропорции — сцена шире дерева
        self._inner.resizeDocks(
            [self._tree_dock, self._scene_dock], [440, 660], Qt.Horizontal
        )
        self._inner.resizeDocks(
            [self._log_dock], [180], Qt.Vertical
        )

        root.addWidget(self._inner, stretch=1)

        # Управление — фиксированная полоса СНИЗУ (вне QMainWindow)
        root.addWidget(self._make_control())

        # === 6.5 (2026-10-06): Live-подсветка активной роли ===
        # QTimer каждые 2 сек читает последнее событие из events.jsonl
        # рабочего проекта и подсвечивает активный слот на сцене.
        self._active_highlight_timer = QTimer(self)
        self._active_highlight_timer.setInterval(2000)  # 2 сек
        self._active_highlight_timer.timeout.connect(
            self._check_active_phase
        )
        self._active_highlight_timer.start()
        # При старте — сразу проверить (не ждать 2 сек)
        QTimer.singleShot(500, self._check_active_phase)

    @staticmethod
    def _make_dock(title: str, widget: QWidget) -> QDockWidget:
        """Создаёт QDockWidget: отцепляется, перемещается, закрывается."""
        dock = QDockWidget(title)
        dock.setWidget(widget)
        dock.setFeatures(
            QDockWidget.DockWidgetMovable
            | QDockWidget.DockWidgetFloatable
            | QDockWidget.DockWidgetClosable
        )
        dock.setAllowedAreas(Qt.AllDockWidgetAreas)
        return dock

    @staticmethod
    def _bind_expand_buttons(dock: QDockWidget, widgets: list):
        """Показывает widgets ТОЛЬКО когда док отцеплен (развёрнут)."""
        def _on_float(top: bool):
            for w in widgets:
                w.setVisible(top)
        for w in widgets:
            w.setVisible(False)
        dock.topLevelChanged.connect(_on_float)

    def _add_refresh_to_dock_title(self, dock: QDockWidget):
        """Иконка ↻ в заголовке дока — перечитать plan.json."""
        title_bar = QWidget()
        h = QHBoxLayout(title_bar)
        h.setContentsMargins(4, 2, 4, 2)
        h.setSpacing(4)

        lbl = QLabel(dock.windowTitle())
        lbl.setStyleSheet("font-weight: bold;")
        h.addWidget(lbl)
        h.addStretch()

        refresh_btn = QPushButton("↻")
        refresh_btn.setFlat(True)
        refresh_btn.setFixedSize(22, 22)
        refresh_btn.setStyleSheet(
            "QPushButton { font-size: 14pt; color: #333; border: none; }"
            "QPushButton:hover { background: #e0e0e0; border-radius: 3px; }"
        )
        refresh_btn.setToolTip("Обновить (перечитать plan.json)")
        refresh_btn.clicked.connect(self._on_tree_refresh)
        h.addWidget(refresh_btn)

        float_btn = QPushButton("🗗")
        float_btn.setFlat(True)
        float_btn.setFixedSize(22, 22)
        float_btn.setToolTip("Отцепить / прицепить")
        float_btn.clicked.connect(
            lambda _c=False, d=dock: d.setFloating(not d.isFloating())
        )
        h.addWidget(float_btn)

        close_btn = QPushButton("✕")
        close_btn.setFlat(True)
        close_btn.setFixedSize(22, 22)
        close_btn.setToolTip("Скрыть панель")
        close_btn.clicked.connect(dock.close)
        h.addWidget(close_btn)

        dock.setTitleBarWidget(title_bar)

    def _on_tree_refresh(self):
        """Перечитать plan.json в дереве."""
        if hasattr(self, "embedded_tree"):
            try:
                self.embedded_tree._reload_plan()
            except Exception:
                pass

    # === ШАПКА ===
    def _refresh_memory_icon(self):
        """B+ (19.10.2026): обновить 🧠 в шапке из memory_api.stats().

        Сохраняет текущий score в файл (.ai_link/memory_score_history.json),
        читает прошлый для сравнения.
        """
        try:
            from core import memory_api as _mem
            _s = _mem.stats()
        except Exception:
            return
        _score = float(_s.get("score") or 0)
        # Читаем прошлый score из файла
        _prev = _score
        try:
            import json as _j
            from pathlib import Path as _P
            _hf = _P.home() / ".ai_link" / "memory_score_history.json"
            if _hf.exists():
                _hist = _j.loads(_hf.read_text(encoding="utf-8"))
                if _hist:
                    _prev = float(_hist[-1].get("score", _score))
        except Exception:
            pass
        # Сохраняем текущий (если отличается от последнего)
        try:
            import json as _j
            from pathlib import Path as _P
            from datetime import datetime as _dt
            _hf = _P.home() / ".ai_link" / "memory_score_history.json"
            _hist = []
            if _hf.exists():
                try:
                    _hist = _j.loads(_hf.read_text(encoding="utf-8"))
                except Exception:
                    _hist = []
            if not _hist or _hist[-1].get("score") != _score:
                _hist.append({
                    "ts": _dt.now().isoformat(timespec="seconds"),
                    "score": _score,
                    "hit_rate": _s.get("hit_rate"),
                    "useful": _s.get("useful_hit_rate"),
                    "active": _s.get("active"),
                })
                # Последние 50
                _hist = _hist[-50:]
                _hf.parent.mkdir(parents=True, exist_ok=True)
                _hf.write_text(
                    _j.dumps(_hist, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
        except Exception:
            pass
        if _score > _prev:
            _color = "#2e7d32"  # зелёный — лучше
        elif _score < _prev:
            _color = "#c62828"  # красный — хуже
        else:
            _color = "#1565c0"  # синий — без изменений
        self._mem_score_prev = _score
        self.mem_lbl.setText(
            f'🧠 <span style="color:{_color}; font-weight:bold;">'
            f'{_score:.1f}</span> '
            f'<span style="color:#888;">/ {_prev:.1f}</span>'
        )
        self.mem_lbl.setTextFormat(Qt.RichText)

    def _on_memory_clicked(self):
        """B+ (19.10.2026): клик по 🧠 — детальная таблица метрик."""
        from PySide6.QtWidgets import (
            QDialog, QVBoxLayout, QLabel, QPushButton, QWidget,
        )
        from PySide6.QtCore import Qt

        dlg = QDialog(self)
        dlg.setWindowTitle("🧠 Память — метрики")
        dlg.setMinimumWidth(520)
        lay = QVBoxLayout(dlg)

        rows_w = QWidget()
        rows_lay = QVBoxLayout(rows_w)
        rows_lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(rows_w)

        def _build_rows():
            # Очищаем старые строки
            while rows_lay.count():
                item = rows_lay.takeAt(0)
                if item.widget():
                    item.widget().deleteLater()
            try:
                from core import memory_api as _mem
                _s = _mem.stats()
            except Exception:
                _s = {}

            def _pct(v):
                return f"{(float(v or 0) * 100):.1f}%"

            def _num(v):
                return f"{float(v or 0):.1f}"

            _prev = {}
            metrics = [
                ("🏆", "Польза памяти",
                 _pct(_s.get("hit_rate")),
                 _pct(_prev.get("hit_rate", _s.get("hit_rate"))),
                 "Сколько раз память реально помогла (verified / retrieved). Цель: 15.0%"),
                ("🎯", "Применение паттернов",
                 _pct(_s.get("useful_hit_rate")),
                 _pct(_prev.get("useful_hit_rate", _s.get("useful_hit_rate"))),
                 "Сколько раз паттерн взяли в работу (used / retrieved)"),
                ("⚒️", "Активных",
                 _num(_s.get("active")),
                 _num(_prev.get("active", _s.get("active"))),
                 "Сколько паттернов сейчас в работе (в промпте)"),
                ("⏳", "Кандидатов",
                 _num(_s.get("candidate")),
                 _num(_prev.get("candidate", _s.get("candidate"))),
                 "Сколько ждут проверки (не в промпте)"),
                ("🌘", "В тени",
                 _num(_s.get("shadow")),
                 _num(_prev.get("shadow", _s.get("shadow"))),
                 "Отстранены (бесполезные, 10+ раз не использованы)"),
                ("🧙", "Устаревших",
                 _pct(_s.get("stale_rate")),
                 _pct(_prev.get("stale_rate", _s.get("stale_rate"))),
                 "В активных, но не используются >60 дней (забыты)"),
                ("📚", "Всего записей",
                 _num(_s.get("evidence_total")),
                 _num(_prev.get("evidence_total", _s.get("evidence_total"))),
                 "Всего записей в истории (pattern_evidence)"),
            ]
            _max_name = max(len(m[1]) for m in metrics)
            for icon, name, now, prev, tip in metrics:
                _pad = " " * (_max_name - len(name) + 3)
                row = QLabel(
                    f"{icon}  {name}{_pad}{now}   /   {prev}"
                )
                row.setTextFormat(Qt.PlainText)
                row.setToolTip(tip)
                row.setStyleSheet(
                    "font-size: 11pt; padding: 3px; font-family: monospace;"
                )
                rows_lay.addWidget(row)

        _build_rows()

        btn = QPushButton("🔄 Обновить")
        def _on_refresh():
            _build_rows()
            self._refresh_memory_icon()
        btn.clicked.connect(_on_refresh)
        lay.addWidget(btn)

        dlg.exec()

    def _make_header(self):
        header = QFrame()
        header.setFrameShape(QFrame.StyledPanel)
        header.setStyleSheet(
            "QFrame { background: #f5f5f5; border-radius: 4px; }"
        )
        h = QHBoxLayout(header)
        h.setContentsMargins(10, 6, 10, 6)
        self.phase_lbl = QLabel("Фаза: —")
        self.phase_lbl.setStyleSheet("font-weight: bold;")
        h.addWidget(self.phase_lbl)
        h.addWidget(QLabel("│"))
        self.agent_lbl = QLabel("Агент: —")
        h.addWidget(self.agent_lbl)
        h.addWidget(QLabel("│"))
        self.time_lbl = QLabel("00:00")
        h.addWidget(self.time_lbl)
        h.addStretch()
        # B+ (19.10.2026): 🧠 по центру. Цвет по динамике.
        # 8.8 — текущий (зелёный/красный/синий). 7.4 — серый.
        self.mem_lbl = QLabel(
            '🧠 <span style="color:#2e7d32; font-weight:bold;">8.8</span>'
            ' <span style="color:#888;">/ 7.4</span>'
        )
        self.mem_lbl.setTextFormat(Qt.RichText)
        self.mem_lbl.setToolTip(
            "Память: текущий Score / прошлый Score (0-100).\n"
            "Зелёный — стало лучше. Красный — хуже. Синий — без изменений.\n"
            "Клик — детальные метрики."
        )
        self.mem_lbl.setStyleSheet("font-size: 11pt;")
        self.mem_lbl.mousePressEvent = (
            lambda e: self._on_memory_clicked()
        )
        h.addWidget(self.mem_lbl)
        h.addStretch()
        # B+ (19.10.2026): обновить 🧠 при старте (из stats).
        try:
            self._refresh_memory_icon()
        except Exception:
            pass
        self.anim_check = QCheckBox("Анимация")
        self.anim_check.setToolTip(
            "☑ — персонажи двигаются.\n☐ — статика (только подсветка)."
        )
        h.addWidget(self.anim_check)

        # 👁 Показать браузер — иконка-глаз рядом с галочкой.
        self.show_browser_btn = QPushButton("👁")
        self.show_browser_btn.setToolTip(
            "Показать браузер поверх окон.\n"
            "Нужно для ввода логина, капчи или просмотра ответа."
        )
        self.show_browser_btn.setFixedWidth(32)
        self.show_browser_btn.setStyleSheet(
            "QPushButton { border: none; font-size: 14pt; }"
            "QPushButton:hover { background: #e0e0e0; border-radius: 3px; }"
        )
        self.show_browser_btn.clicked.connect(self._on_show_browser)
        h.addWidget(self.show_browser_btn)
        # CPU/RAM убраны из шапки — они в статус-баре снизу.
        return header

    # === ДЕРЕВО ===
    def _make_tree_panel(self) -> QWidget:
        panel = QWidget()
        panel.setObjectName("treePanel")
        panel.setStyleSheet("#treePanel { background: #f4faf4; }")
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(0, 0, 0, 0)

        # Встроенный KnowledgeTab — вся логика дерева уже работает.
        self.embedded_tree = KnowledgeTab()

        # Прячем ВСЕ штатные кнопки KnowledgeTab.
        for btn_attr in ("projects_btn", "archive_btn",
                         "export_btn", "copy_btn",
                         "edit_btn", "refresh_btn"):
            b = getattr(self.embedded_tree, btn_attr, None)
            if b is not None:
                b.setVisible(False)

        # Заголовок «Карта проекта» — тоже прячем (дубль с доком).
        try:
            self.embedded_tree.findChild(QLabel, "").setVisible(False)
        except Exception:
            pass

        lay.addWidget(self.embedded_tree, stretch=1)

        # === Наши кнопки — видны ТОЛЬКО при развороте ===
        extra_w = QWidget()
        e_lay = QHBoxLayout(extra_w)
        e_lay.setContentsMargins(6, 4, 6, 4)
        e_lay.setSpacing(6)

        self._btn_tree_refresh = QPushButton("Обновить")
        self._btn_tree_refresh.setToolTip("Перечитать plan.json")
        self._btn_tree_refresh.clicked.connect(self._on_tree_refresh)

        self._btn_tree_projects = QPushButton("Проекты")
        self._btn_tree_projects.setToolTip(
            "Посмотреть план другого проекта.\n"
            "Только предпросмотр — рабочий проект НЕ меняется.\n"
            "При прицепке панели вернётся план рабочего проекта."
        )
        self._btn_tree_projects.clicked.connect(
            self.embedded_tree._on_open_projects
        )
        # Сигнал от диалога → временно показать чужой план
        try:
            self.embedded_tree.project_loaded.connect(
                self._on_preview_project
            )
        except Exception:
            pass

        self._btn_tree_copy = QPushButton("Копировать")
        self._btn_tree_copy.setToolTip("Скопировать план в буфер")
        self._btn_tree_copy.clicked.connect(
            self.embedded_tree._on_copy_plan
        )

        e_lay.addWidget(self._btn_tree_refresh)
        e_lay.addWidget(self._btn_tree_projects)
        e_lay.addWidget(self._btn_tree_copy)
        e_lay.addStretch()
        lay.addWidget(extra_w)

        # Ссылки для внешнего доступа
        self.tree_refresh_btn = self._btn_tree_refresh
        self._tree_extra_btns = [
            self._btn_tree_refresh,
            self._btn_tree_projects,
            self._btn_tree_copy,
        ]
        return panel

    def set_project_dir(self, project_dir: str):
        """Рабочий проект: устанавливается из main_window.

        Запоминается как «рабочий» — чтобы при возврате из предпросмотра
        вернуть именно его.
        """
        self._work_project_dir = project_dir or ""
        if hasattr(self, "embedded_tree"):
            self.embedded_tree.set_project_dir(project_dir)

    def _on_preview_project(self, path: str):
        """Временный предпросмотр чужого плана.

        Рабочий проект (self._work_project_dir) НЕ меняется.
        При прицепке панели — вернётся план рабочего проекта.
        """
        if path and hasattr(self, "embedded_tree"):
            self.embedded_tree.set_project_dir(path)

    def _on_tree_dock_float(self, top: bool):
        """Реакция на отцепление/прицепление дока дерева.

        top=True  — док отцепился (развёрнут): не трогаем.
        top=False — док прицепился обратно: возвращаем план РАБОЧЕГО
                    проекта. Если рабочий не выбран — очищаем дерево
                    (не оставляем чужой предпросмотр).
        """
        if top:
            return
        if not hasattr(self, "embedded_tree"):
            return
        try:
            if self._work_project_dir:
                self.embedded_tree.set_project_dir(self._work_project_dir)
            else:
                # Рабочий проект не выбран — сбросить дерево.
                self.embedded_tree._project_dir = ""
                self.embedded_tree._reload_plan()
        except Exception:
            pass

    # === СЦЕНА ===
    _ROLE_RU = {
        "planning": "ПЛАН",
        "coding": "КОД",
        "review": "РЕВЬЮ",
        "verifier": "ПРОВЕРКА",
        "comprehension": "ОСМЫСЛЕНИЕ",
        "dialog": "ДИАЛОГ",
    }

    def _make_scene_panel(self) -> QWidget:
        panel = QWidget()
        panel.setObjectName("scenePanel")
        panel.setStyleSheet(
            "#scenePanel { background: #f2f6fb; }"
        )
        outer = QVBoxLayout(panel)
        outer.setContentsMargins(6, 6, 6, 6)

        inner = QWidget()
        lay = QVBoxLayout(inner)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        # Ссылки на слоты: slot_id → dict с элементами (img, label, model).
        self._scene_slots = {}

        def _slot(slot_id, text, w=88, h=100):
            f = QFrame()
            f.setFixedSize(w, h)
            # Без рамки/фона — просто фигурка + подписи.
            f.setStyleSheet("QFrame { background: transparent; }")
            v = QVBoxLayout(f)
            v.setContentsMargins(2, 2, 2, 2)
            v.setSpacing(1)

            # Картинка модели (или «—»)
            img = QLabel("—")
            img.setAlignment(Qt.AlignCenter)
            img.setStyleSheet(
                "color: #ccc; font-size: 18pt; border: none; "
                "background: transparent;"
            )
            img.setFixedHeight(56)
            v.addWidget(img)

            # Название роли
            lbl = QLabel(text)
            lbl.setAlignment(Qt.AlignCenter)
            lbl.setStyleSheet(
                "color: #333; font-size: 8pt; font-weight: bold; "
                "border: none; background: transparent;"
            )
            lbl.setWordWrap(True)
            v.addWidget(lbl)

            # Имя ИИ (или — )
            m = QLabel("—")
            m.setAlignment(Qt.AlignCenter)
            m.setStyleSheet(
                "color: #888; font-size: 7pt; border: none; "
                "background: transparent;"
            )
            m.setWordWrap(True)
            v.addWidget(m)

            self._scene_slots[slot_id] = {
                "frame": f, "img": img, "label": lbl, "model": m,
            }
            return f

        lay.addStretch(1)

        # Ряд 1: 5 рабочих ролей
        roles_row = QHBoxLayout()
        roles_row.setSpacing(10)
        roles_row.addStretch()
        for r, title in (
            ("planning", "ПЛАН"),
            ("coding", "КОД"),
            ("review", "РЕВЬЮ"),
            ("verifier", "ПРОВЕРКА"),
            ("comprehension", "ОСМЫСЛЕНИЕ"),
        ):
            roles_row.addWidget(_slot(r, title))
        roles_row.addStretch()
        lay.addLayout(roles_row)
        lay.addSpacing(24)

        # Ряд 2: РУКИ
        arms_row = QHBoxLayout()
        arms_row.addStretch()
        arms_row.addWidget(_slot("arms", "РУКИ", w=80, h=90))
        arms_row.addStretch()
        lay.addLayout(arms_row)
        lay.addSpacing(12)

        # Ряд 3: LINK
        link_row = QHBoxLayout()
        link_row.addStretch()
        link_row.addWidget(_slot("link", "LINK", w=100, h=100))
        link_row.addStretch()
        lay.addLayout(link_row)
        lay.addSpacing(24)

        # Ряд 4: МОЗГ | ПОЛЬЗОВАТЕЛЬ
        bottom_row = QHBoxLayout()
        bottom_row.addStretch(1)
        bottom_row.addWidget(_slot("brain", "МОЗГ", w=90, h=100))
        bottom_row.addStretch(2)
        bottom_row.addWidget(_slot("user", "ПОЛЬЗОВАТЕЛЬ", w=110, h=100))
        bottom_row.addStretch(1)
        lay.addLayout(bottom_row)
        lay.addStretch(1)

        outer.addWidget(inner, stretch=1)

        # === Панель кнопок (видна ТОЛЬКО при развороте дока) ===
        btns_w = QWidget()
        b_lay = QHBoxLayout(btns_w)
        b_lay.setContentsMargins(0, 4, 0, 0)
        b_lay.setSpacing(6)

        self._btn_scene_settings = QPushButton("⚙ Настройка")
        self._btn_scene_settings.setToolTip(
            "Выбрать модели и назначить их на роли.\n"
            "Там же — загрузка новых моделей и сохранение."
        )
        self._btn_scene_settings.clicked.connect(self._on_scene_settings)

        self._btn_scene_help = QPushButton("📖 Инструкция")
        self._btn_scene_help.setToolTip(
            "Как сделать модель и какого формата."
        )
        self._btn_scene_help.clicked.connect(self._on_scene_help)

        b_lay.addWidget(self._btn_scene_settings)
        b_lay.addWidget(self._btn_scene_help)
        b_lay.addStretch()
        outer.addWidget(btns_w)

        # Кнопки, видимые только при развороте.
        self._scene_extra_btns = [
            self._btn_scene_settings,
            self._btn_scene_help,
        ]

        # Первичное заполнение слотов из scene.json
        try:
            self._reload_scene_models()
        except Exception:
            pass
        return panel

    # === СЦЕНА — обработчики кнопок (заглушки) ===
    def _on_scene_settings(self):
        """Открыть диалог настройки сцены."""
        try:
            from ui.dialogs.scene_settings_dialog import (
                SceneSettingsDialog,
            )
            dlg = SceneSettingsDialog(self)
            if dlg.exec():
                # После «Сохранить» — перерисовать сцену.
                self._reload_scene_models()
        except Exception as e:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.warning(
                self, "Ошибка",
                f"Не удалось открыть настройку сцены: {e}"
            )

    # Роли, под которыми подписывается ИИ-модель.
    # LINK/РУКИ/ПОЛЬЗОВАТЕЛЬ — не ИИ-роли, там просто «—».
    _AI_SLOTS = {
        "brain", "planning", "coding", "review",
        "verifier", "comprehension",
    }

    def _reload_scene_models(self):
        """Перерисовать слоты сцены: иконка модели + имя ИИ.

        Иконка берётся из ~/.ai_link/scene_models/ (scene.json).
        Подпись под ролью:
          • для ИИ-ролей — название агента (DeepSeek, Qwen, ...);
          • для LINK/РУКИ/ПОЛЬЗОВАТЕЛЬ — «—».
        """
        if not hasattr(self, "_scene_slots"):
            return
        try:
            from core.scene_manager import (
                load_assignments, MODELS_DIR,
            )
        except Exception:
            return

        try:
            assigns = load_assignments()
        except Exception:
            assigns = {}

        # Имена агентов: agent_id → красивое название.
        # browser_deepseek → «DeepSeek», browser_qwen → «Qwen» и т.п.
        agent_names = {}
        try:
            from core.capability_benchmark import (
                list_available_agents, load_assignments as _la,
            )
            for a in list_available_agents():
                agent_names[a["id"]] = a.get("name", a["id"])
            # Роли (какая модель на какой ИИ-роли)
            role_agents = (_la().get("assignments") or {})
        except Exception:
            role_agents = {}

        for slot_id, w in self._scene_slots.items():
            model_name = assigns.get(slot_id, "") or ""
            img_lbl = w["img"]
            name_lbl = w["model"]

            # Иконка — файл из scene_models
            path = None
            if model_name:
                p = MODELS_DIR / model_name
                if p.exists():
                    path = str(p)

            # Сбрасываем предыдущий QMovie (если был) —
            # иначе GIF продолжит играть в фоне.
            if hasattr(img_lbl, "_movie") and img_lbl._movie is not None:
                try:
                    img_lbl._movie.stop()
                except Exception:
                    pass
                img_lbl._movie = None
            img_lbl.setMovie(None)

            if path:
                ext = path.lower().rsplit(".", 1)[-1]
                if ext == "gif":
                    # GIF — через QMovie, чтобы играл.
                    movie = QMovie(path)
                    movie.setScaledSize(QSize(52, 52))
                    # ВАЖНО: храним ссылку на movie на самом label,
                    # иначе сборщик мусора Qt остановит анимацию.
                    img_lbl._movie = movie
                    img_lbl.setMovie(movie)
                    img_lbl.setText("")
                    img_lbl.setStyleSheet(
                        "border: none; background: transparent;"
                    )
                    movie.start()
                else:
                    # PNG — обычный QPixmap.
                    pix = QPixmap(path)
                    if not pix.isNull():
                        scaled = pix.scaled(
                            52, 52,
                            Qt.KeepAspectRatio,
                            Qt.SmoothTransformation,
                        )
                        img_lbl.setPixmap(scaled)
                        img_lbl.setText("")
                        img_lbl.setStyleSheet(
                            "border: none; background: transparent;"
                        )
                    else:
                        img_lbl.setPixmap(QPixmap())
                        img_lbl.setText("?")
                        img_lbl.setStyleSheet(
                            "color: #c00; font-size: 18pt; border: none; "
                            "background: transparent;"
                        )
            else:
                img_lbl.setPixmap(QPixmap())
                img_lbl.setText("—")
                img_lbl.setStyleSheet(
                    "color: #ccc; font-size: 18pt; border: none; "
                    "background: transparent;"
                )

            # Подпись — имя ИИ для ИИ-ролей, иначе «—».
            if slot_id in self._AI_SLOTS:
                aid = role_agents.get(slot_id, "")
                ai_name = agent_names.get(aid, "")
                # Короткое имя без префикса browser_
                if ai_name:
                    ai_name = ai_name.replace(" (браузер)", "")
                    ai_name = ai_name.replace(" (Sber API)", "")
                name_lbl.setText(ai_name or "—")
                name_lbl.setStyleSheet(
                    "color: #555; font-size: 7pt; font-weight: bold; "
                    "border: none; background: transparent;"
                )
            else:
                name_lbl.setText("")
                name_lbl.setStyleSheet(
                    "color: #888; font-size: 7pt; border: none; "
                    "background: transparent;"
                )

        # 6.6: после базовой отрисовки — применить визуальный режим
        # (активные яркие / остальные приглушены / все спят).
        # _current_active_slot теперь список (или None).
        try:
            self._set_active_slots(self._current_active_slot)
        except Exception:
            pass

    # === 6.6 (2026-10-07): визуальный режим (сон / работа) ===

    def _apply_visual_mode(self, active_slots):
        """6.6: сон (все спят) или работа (активные яркие, остальные — 40%).

        active_slots=None → все слоты показывают модель сна.
        active_slots=[id] → активные — свои модели + opacity 1.0,
                             остальные — свои + opacity 0.4.
        """
        if not hasattr(self, "_scene_slots"):
            return

        # Нормализуем: None / list.
        if active_slots is None:
            active_set = set()
        else:
            active_set = set(active_slots)

        from core.scene_manager import (
            load_assignments, get_sleep_model_path, MODELS_DIR,
        )

        # === РЕЖИМ СНА (пауза / завершено / нет прогона) ===
        if not active_set:
            sleep_path = get_sleep_model_path()
            if not sleep_path:
                # Нет модели сна — просто все прозрачные.
                for w in self._scene_slots.values():
                    self._set_slot_opacity(w, 0.5)
                return
            for sid, w in self._scene_slots.items():
                self._render_slot_image(w, sleep_path)
                self._set_slot_opacity(w, 1.0)
            return

        # === РЕЖИМ РАБОТЫ ===
        try:
            assigns = load_assignments()
        except Exception:
            assigns = {}
        # BUG-FD-LEAK: ставим movie на паузу для неактивных,
        # возобновляем для активного. Не создаём новые.
        for sid, w in self._scene_slots.items():
            model_name = assigns.get(sid, "") or ""
            path = None
            if model_name:
                pth = MODELS_DIR / model_name
                if pth.exists():
                    path = str(pth)
            if path:
                self._render_slot_image(w, path)
            self._set_slot_opacity(
                w, 1.0 if sid in active_set else 0.4
            )
            # BUG-FD-LEAK: неактивные — на паузе (не жрут CPU).
            # Активные — играют.
            img_lbl = w.get("img")
            movie = getattr(img_lbl, "_movie", None) if img_lbl else None
            if movie is not None:
                try:
                    if sid in active_set:
                        if movie.state() != QMovie.Running:
                            movie.start()
                    else:
                        if movie.state() == QMovie.Running:
                            movie.setPaused(True)
                except Exception:
                    pass

    def _render_slot_image(self, w, path):
        """Нарисовать картинку в слот (GIF → QMovie, PNG → QPixmap).

        BUG-FD-LEAK (2026-10-07): используем КЭШ QMovie.
        QMovie держит файл открытым. Если создавать новый QMovie
        каждые 2 сек на 9 слотов — за минуты добиваем лимит fd
        (1024) и падает всё приложение.
        """
        img_lbl = w["img"]

        # Если уже показываем то же — ничего не делаем.
        cur = getattr(img_lbl, "_current_path", None)
        if cur == path and path:
            return

        # Останавливаем прежний movie (не удаляем — он в кэше).
        if hasattr(img_lbl, "_movie") and img_lbl._movie is not None:
            try:
                img_lbl._movie.stop()
            except Exception:
                pass
            img_lbl._movie = None
        img_lbl.setMovie(None)
        img_lbl.setPixmap(QPixmap())
        img_lbl._current_path = path

        if not path:
            img_lbl.setText("—")
            img_lbl.setStyleSheet(
                "color: #ccc; font-size: 18pt; border: none; "
                "background: transparent;"
            )
            return

        ext = path.lower().rsplit(".", 1)[-1]
        if ext == "gif":
            # Берём из кэша или создаём 1 раз.
            movie = self._movie_cache.get(path)
            if movie is None:
                movie = QMovie(path)
                movie.setScaledSize(QSize(52, 52))
                self._movie_cache[path] = movie
            img_lbl._movie = movie
            img_lbl.setMovie(movie)
            img_lbl.setText("")
            img_lbl.setStyleSheet(
                "border: none; background: transparent;"
            )
            if movie.state() != QMovie.Running:
                movie.start()
        else:
            pix = QPixmap(path)
            if not pix.isNull():
                scaled = pix.scaled(
                    52, 52, Qt.KeepAspectRatio,
                    Qt.SmoothTransformation,
                )
                img_lbl.setPixmap(scaled)
                img_lbl.setText("")
                img_lbl.setStyleSheet(
                    "border: none; background: transparent;"
                )
            else:
                img_lbl.setText("?")
                img_lbl.setStyleSheet(
                    "color: #c00; font-size: 18pt; border: none; "
                    "background: transparent;"
                )

    def _set_slot_opacity(self, w, opacity):
        """Прозрачность картинки слота через QGraphicsOpacityEffect."""
        try:
            from PySide6.QtWidgets import QGraphicsOpacityEffect
        except Exception:
            return
        img_lbl = w["img"]
        # Удаляем прежний эффект, чтобы не копились.
        img_lbl.setGraphicsEffect(None)
        if opacity >= 0.999:
            return
        eff = QGraphicsOpacityEffect(img_lbl)
        eff.setOpacity(opacity)
        img_lbl.setGraphicsEffect(eff)

    # === 6.5 (2026-10-06): Live-подсветка активной роли ===

    # Фаза оркестратора → СПИСОК активных слотов (B6+B7).
    #   • LINK активен ВСЕГДА во время прогона — он оркестратор.
    #   • arms (Руки) активны при code/fix — HandLink создаёт файлы.
    _PHASE_TO_SLOTS = {
        "plan":          ["planning", "link"],
        "code":          ["coding", "arms", "link"],
        "fix":           ["coding", "arms", "link"],
        "review":        ["review", "link"],
        "verify":        ["verifier", "link"],
        "verification":  ["verifier", "link"],
        "comprehension": ["comprehension", "link"],
    }

    def _check_active_phase(self):
        """Читает последнее событие events.jsonl и обновляет подсветку.

        Логика:
          • нет рабочего проекта → все слоты серые;
          • последнее событие = phase_started → подсветить слот по phase;
          • последнее событие = phase_completed / run_finished → снять.

        Читает ПОСЛЕДНИЕ 5 строк файла (хвост), чтобы не парсить весь.
        """
        if not hasattr(self, "_scene_slots"):
            return

        active_slots = None

        try:
            from pathlib import Path as _P
            # FIX 2026-10-06: если рабочий проект не передан из
            # main_window (пустое поле «Проект»), берём путь из
            # встроенного дерева — оно уже знает проект.
            proj_dir = self._work_project_dir
            if not proj_dir and hasattr(self, "embedded_tree"):
                proj_dir = getattr(
                    self.embedded_tree, "_project_dir", ""
                ) or ""
            if not proj_dir:
                raise ValueError("нет рабочего проекта")
            events_path = (
                _P(proj_dir) / ".ai_link" / "events.jsonl"
            )
            if not events_path.exists():
                raise ValueError("нет events.jsonl")

            # Читаем хвост файла — последние ~64 КБ
            with open(events_path, "rb") as f:
                f.seek(0, 2)
                size = f.tell()
                f.seek(max(0, size - 65536))
                tail = f.read().decode("utf-8", errors="ignore")
            lines = [ln for ln in tail.splitlines() if ln.strip()]

            # FIX 2026-10-06: сначала найти run_id ТЕКУЩЕГО прогона —
            # это run_id последнего события. Иначе путаем старые
            # phase_started от других прогонов.
            import json as _json
            current_run = ""
            for ln in reversed(lines):
                try:
                    e = _json.loads(ln)
                    rid = e.get("run_id")
                    if rid:
                        current_run = rid
                        break
                except Exception:
                    continue

            # Ищем последнее значимое событие ТОЛЬКО текущего прогона.
            for ln in reversed(lines):
                try:
                    e = _json.loads(ln)
                except Exception:
                    continue
                # Пропускаем события других прогонов.
                if current_run and e.get("run_id") != current_run:
                    continue
                et = e.get("event_type", "")
                if et == "phase_started":
                    ph = (e.get("payload_json") or {}).get("phase", "")
                    active_slots = self._PHASE_TO_SLOTS.get(ph)
                    break
                if et in ("phase_completed", "run_finished",
                          "run_paused", "run_stopped"):
                    # Завершилось — все серые.
                    active_slots = None
                    break
        except Exception:
            active_slots = None

        self._set_active_slots(active_slots)

    def _set_active_slots(self, slot_ids):
        """Подсветить СПИСОК слотов; все остальные — серые.

        slot_ids=None → все серые (сон).
        slot_ids=["coding","arms","link"] → три слота зелёные.
        """
        if not hasattr(self, "_scene_slots"):
            return
        # 6.6: запоминаем текущий активный набор.
        self._current_active_slot = slot_ids

        # Приводим к множеству для быстрой проверки.
        active_set = set(slot_ids) if slot_ids else set()

        for sid, w in self._scene_slots.items():
            frame = w.get("frame")
            if frame is None:
                continue
            if sid in active_set:
                # Активный: зелёный фон + рамка.
                frame.setStyleSheet(
                    "QFrame { background: #d4edda; "
                    "border: 2px solid #28a745; border-radius: 6px; }"
                )
            else:
                # Неактивный: без рамки, прозрачный.
                frame.setStyleSheet(
                    "QFrame { background: transparent; }"
                )

        # 6.6: применить визуальный режим (сон / работа).
        self._apply_visual_mode(slot_ids)

    def _on_scene_help(self):
        """Открыть окно инструкции (подэтап 6.3)."""
        from PySide6.QtWidgets import QMessageBox
        QMessageBox.information(
            self, "Инструкция",
            "Как сделать модель:\n"
            "  • Формат: PNG (статика) или GIF (анимация).\n"
            "  • Размер: рекомендуется 64×64 или 96×96.\n"
            "  • Фон: прозрачный (PNG) — красивее.\n"
            "  • Имя файла — любое.\n\n"
            "Подробная инструкция с примерами — позже."
        )

    # === ЛОГИ ===
    def _make_log_panel(self) -> QWidget:
        panel = QWidget()
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(6, 6, 6, 6)
        # Тот же виджет, что в старой вкладке «Логи» — тёмная тема,
        # уровни, автоскролл.
        self.log_widget = LogWidget()
        lay.addWidget(self.log_widget, stretch=1)

        # Кнопки логов — видны ТОЛЬКО при развороте дока.
        extra = QWidget()
        e_lay = QHBoxLayout(extra)
        e_lay.setContentsMargins(0, 4, 0, 0)
        e_lay.setSpacing(6)

        self._btn_logs_folder = QPushButton("📁 Папка логов")
        self._btn_logs_folder.setToolTip(
            "Открыть папку с логами проекта (.ai_link/)."
        )
        self._btn_logs_folder.clicked.connect(self._on_open_logs_folder)
        e_lay.addWidget(self._btn_logs_folder)

        self._btn_logs_clear = QPushButton("🧹 Очистить окно")
        self._btn_logs_clear.setToolTip(
            "Очистить окно логов (файлы на диске не удаляются)."
        )
        self._btn_logs_clear.clicked.connect(self._on_clear_logs)
        e_lay.addWidget(self._btn_logs_clear)
        e_lay.addStretch()

        lay.addWidget(extra)
        self._logs_extra_btns = [
            self._btn_logs_folder,
            self._btn_logs_clear,
        ]
        return panel

    def _on_clear_logs(self):
        """Очистить окно логов (файлы на диске не трогаем)."""
        try:
            self.log_widget.clear()
        except Exception:
            pass

    def _on_show_browser(self):
        """Вернуть окно Chromium на экран (поверх других)."""
        try:
            from core.agents.browser_manager import (
                restore_browser_window,
            )
            restore_browser_window()
        except Exception as e:
            logger.warning("SHOW-BROWSER: %s", e)

    def _on_open_logs_folder(self):
        """Открыть папку логов рабочего проекта или ~/.ai_link/logs."""
        try:
            from PySide6.QtGui import QDesktopServices
            from PySide6.QtCore import QUrl
            from pathlib import Path as _P

            # Приоритет: .ai_link в проекте → ~/.ai_link/logs
            candidates = []
            if self._work_project_dir:
                candidates.append(_P(self._work_project_dir) / ".ai_link")
            candidates.append(_P.home() / ".ai_link" / "logs")

            for c in candidates:
                if c.exists():
                    QDesktopServices.openUrl(
                        QUrl.fromLocalFile(str(c))
                    )
                    return
        except Exception:
            pass

    def append_log(self, level_or_msg: str, message: str = ""):
        """Принимает логи от main_window. Проксирует в LogWidget."""
        if hasattr(self, "log_widget"):
            self.log_widget.append_log(level_or_msg, message)

    # === УПРАВЛЕНИЕ ===
    def _make_control(self) -> QWidget:
        panel = QWidget()
        p = QVBoxLayout(panel)
        p.setContentsMargins(6, 6, 6, 6)
        p.setSpacing(6)

        btns = QHBoxLayout()
        self.pause_btn = QPushButton("⏸ Пауза")
        self.resume_btn = QPushButton("▶ Продолжить")
        self.resume_btn.setEnabled(False)
        btns.addWidget(self.pause_btn)
        btns.addWidget(self.resume_btn)
        btns.addStretch()
        self.open_project_btn = QPushButton("📁 Папка проекта")
        btns.addWidget(self.open_project_btn)
        p.addLayout(btns)

        inp = QHBoxLayout()
        self.input_field = QLineEdit()
        self.input_field.setPlaceholderText(
            "Уточнение к задаче (во время паузы)..."
        )
        self.send_btn = QPushButton("→")
        self.send_btn.setFixedWidth(40)
        inp.addWidget(self.input_field, stretch=1)
        inp.addWidget(self.send_btn)
        p.addLayout(inp)

        return panel
