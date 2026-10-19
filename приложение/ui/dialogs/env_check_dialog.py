"""ENV-CHECK диалог (A.3.2, 2026-10-04).

Три вкладки по уровням: ОБЯЗАТЕЛЬНЫЕ / ЖЕЛАТЕЛЬНЫЕ / МАКСИМУМ.
Внутри каждой — список программ с чекбоксами, кнопки установки.

Установка — через терминал: apt сам покажет весь прогресс,
включая "Get N/M" (сколько всего и какой сейчас).
"""
import shutil
import subprocess
from typing import Dict, List, Optional

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QListWidget, QListWidgetItem, QMessageBox, QTabWidget,
    QWidget, QApplication,
)
from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QColor, QDesktopServices

from core.env_check import (
    check_all, summary, LEVELS, LEVEL_TITLES,
    CAT_TITLES, CAT_ORDER, install_commands,
    clear_cache, missing_mandatory,
)


TERMINALS = [
    ("x-terminal-emulator", ["-e", "bash", "-c"]),
    ("gnome-terminal",       ["--",  "bash", "-c"]),
    ("konsole",              ["-e",  "bash", "-c"]),
    ("xfce4-terminal",       ["-e",  "bash -c"]),
    ("xterm",                ["-e",  "bash", "-c"]),
]


def _open_terminal(script: str) -> bool:
    script_with_pause = (
        script
        + '; echo; echo "=== Готово. Нажмите Enter, чтобы закрыть ==="; '
        + 'read _'
    )
    for term, args in TERMINALS:
        path = shutil.which(term)
        if not path:
            continue
        try:
            subprocess.Popen([path] + args + [script_with_pause])
            return True
        except Exception:
            continue
    return False


class EnvCheckDialog(QDialog):
    """Проверка окружения: 3 вкладки по уровням."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Проверка окружения")
        self.resize(920, 720)
        self._results: Dict[str, dict] = {}
        self._level_lists: Dict[str, QListWidget] = {}
        self._setup_ui()
        self._reload()

    # ---- UI ----
    def _setup_ui(self):
        layout = QVBoxLayout(self)

        hint = QLabel(
            "Отметьте программы для установки и нажмите "
            "«Установить в терминале». apt спросит пароль один раз "
            "и покажет весь прогресс (Get/Unpacking/Setting up "
            "для каждого пакета). Ctrl+C — отмена."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: gray; padding: 4px;")
        layout.addWidget(hint)

        self.summary_lbl = QLabel("…")
        self.summary_lbl.setStyleSheet("padding: 4px; font-weight: bold;")
        layout.addWidget(self.summary_lbl)

        # === Вкладки по уровням ===
        self.tabs = QTabWidget()
        for lvl in LEVELS:
            page = QWidget()
            page_layout = QVBoxLayout(page)

            lst = QListWidget()
            page_layout.addWidget(lst, stretch=1)
            self._level_lists[lvl] = lst

            btn_row = QHBoxLayout()
            btn = QPushButton("Установить в терминале")
            btn.clicked.connect(
                lambda _c=False, L=lvl: self._on_install(L)
            )
            btn_row.addWidget(btn)

            copy_btn = QPushButton("Скопировать команду")
            copy_btn.setToolTip(
                "Скопировать команду установки в буфер — "
                "можно вставить в свой терминал."
            )
            copy_btn.clicked.connect(
                lambda _c=False, L=lvl: self._on_copy(L)
            )
            btn_row.addWidget(copy_btn)

            btn_row.addStretch()
            page_layout.addLayout(btn_row)

            self.tabs.addTab(page, LEVEL_TITLES[lvl])

        layout.addWidget(self.tabs, stretch=1)

        # Низ
        bottom = QHBoxLayout()
        refresh_btn = QPushButton("Обновить")
        refresh_btn.setToolTip(
            "Перечитать состояние системы. Нажмите после закрытия "
            "терминала с установкой."
        )
        refresh_btn.clicked.connect(self._reload)
        bottom.addWidget(refresh_btn)

        close_btn = QPushButton("Закрыть")
        close_btn.clicked.connect(self.accept)
        bottom.addWidget(close_btn)

        bottom.addStretch()
        layout.addLayout(bottom)

    # ---- Логика ----
    def _reload(self):
        clear_cache()
        self._results = check_all(use_cache=False)
        s = summary()
        miss_mand = missing_mandatory()

        text = (f"Установлено: {s['installed_count']} / {s['total']}   "
                f"Отсутствует: {s['missing_count']}")
        if miss_mand:
            text += f"   ⚠ Нет обязательных: {', '.join(miss_mand)}"
        self.summary_lbl.setText(text)

        for lvl in LEVELS:
            self._fill_list(lvl)
            self._update_tab_title(lvl)

    def _update_tab_title(self, level: str):
        """Обновляет заголовок вкладки: сколько установлено из общего."""
        total = sum(1 for r in self._results.values()
                    if r["level"] == level)
        inst = sum(1 for r in self._results.values()
                   if r["level"] == level and r["installed"])
        base = LEVEL_TITLES[level]
        idx = LEVELS.index(level)
        self.tabs.setTabText(idx, f"{base}   [{inst}/{total}]")

    def _fill_list(self, level: str):
        lst = self._level_lists[level]
        lst.clear()

        items_by_cat: Dict[str, list] = {}
        for name, r in self._results.items():
            if r["level"] != level:
                continue
            items_by_cat.setdefault(r["category"], []).append((name, r))

        for cat in CAT_ORDER:
            entries = items_by_cat.get(cat, [])
            if not entries:
                continue
            hdr = QListWidgetItem(f"— {CAT_TITLES.get(cat, cat)} —")
            hdr.setFlags(Qt.NoItemFlags)
            hdr.setForeground(QColor("#888"))
            lst.addItem(hdr)

            for name, r in entries:
                if r["installed"]:
                    ver = r["version"] or ""
                    txt = f"  ✅ {r['title']}"
                    if ver:
                        txt += f"  ({ver})"
                    it = QListWidgetItem(txt)
                    it.setFlags(Qt.NoItemFlags)
                    it.setForeground(QColor("#2e7d32"))
                    it.setData(Qt.UserRole, name)
                    lst.addItem(it)
                else:
                    txt = f"  ❌ {r['title']}"
                    if r.get("hint"):
                        txt += f"   — {r['hint']}"
                    it = QListWidgetItem(txt)
                    it.setFlags(
                        Qt.ItemIsUserCheckable | Qt.ItemIsEnabled
                    )
                    it.setCheckState(Qt.Checked)
                    it.setForeground(QColor("#c62828"))
                    it.setData(Qt.UserRole, name)
                    lst.addItem(it)

    def _checked_names(self, level: str) -> List[str]:
        lst = self._level_lists[level]
        names: List[str] = []
        for i in range(lst.count()):
            it = lst.item(i)
            if it.flags() & Qt.ItemIsUserCheckable:
                if it.checkState() == Qt.Checked:
                    name = it.data(Qt.UserRole)
                    if name:
                        names.append(name)
        return names

    def _install_plan(self, level: str) -> Optional[dict]:
        """Собирает план установки. Возвращает None если нечего.

        Возвращает dict:
          {"apt": [...], "pip": [...], "manual": [...],
           "cmd": "pip3 ... && pkexec apt-get ..."}
        """
        names = self._checked_names(level)
        if not names:
            QMessageBox.information(
                self, "Нечего устанавливать",
                f"В блоке «{LEVEL_TITLES[level]}» не отмечено "
                f"ни одной программы."
            )
            return None
        parts = install_commands(names)
        cmds = []
        if parts["pip"]:
            cmds.append("pip3 install --user " + " ".join(parts["pip"]))
        if parts["apt"]:
            cmds.append("pkexec apt-get install -y "
                        + " ".join(parts["apt"]))
        if parts.get("npm"):
            cmds.append("npm install -g " + " ".join(parts["npm"]))
        cmd = " && ".join(cmds) if cmds else ""
        return {
            "apt": parts.get("apt", []),
            "pip": parts.get("pip", []),
            "npm": parts.get("npm", []),
            "manual": parts.get("manual", []),
            "cmd": cmd,
        }

    def _show_manual_dialog(self, plan: dict) -> None:
        """Диалог ручной установки (A.3.3)."""
        lines = ["Следующие программы ставятся вручную:", ""]
        for m in plan["manual"]:
            lines.append(f"• {m['title']}")
            if m.get("hint"):
                lines.append(f"   {m['hint']}")
            if m.get("url"):
                lines.append(f"   Сайт: {m['url']}")
            lines.append("")

        dlg = QDialog(self)
        dlg.setWindowTitle("Ручная установка")
        dlg.resize(640, 420)
        lay = QVBoxLayout(dlg)

        info = QLabel("\n".join(lines))
        info.setWordWrap(True)
        info.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lay.addWidget(info, stretch=1)

        btn_row = QHBoxLayout()

        first_url = next(
            (m["url"] for m in plan["manual"] if m.get("url")), ""
        )
        if first_url:
            open_btn = QPushButton("Открыть сайт")
            open_btn.setToolTip(first_url)
            open_btn.clicked.connect(
                lambda _c=False, u=first_url:
                    QDesktopServices.openUrl(QUrl(u))
            )
            btn_row.addWidget(open_btn)

        first_hint = next(
            (m["hint"] for m in plan["manual"] if m.get("hint")), ""
        )
        if first_hint:
            copy_btn = QPushButton("Скопировать команду")
            copy_btn.clicked.connect(
                lambda _c=False, t=first_hint:
                    QApplication.clipboard().setText(t)
            )
            btn_row.addWidget(copy_btn)

        btn_row.addStretch()
        close_btn = QPushButton("Закрыть")
        close_btn.clicked.connect(dlg.accept)
        btn_row.addWidget(close_btn)
        lay.addLayout(btn_row)

        dlg.exec()

    def _on_copy(self, level: str):
        plan = self._install_plan(level)
        if not plan:
            return
        # Собираем текст: apt/pip + manual
        pieces = []
        if plan["cmd"]:
            pieces.append(plan["cmd"])
        for m in plan["manual"]:
            if m.get("hint"):
                pieces.append(f"# {m['name']}: {m['hint']}")
            if m.get("url"):
                pieces.append(f"# {m['name']}: {m['url']}")
        if not pieces:
            QMessageBox.information(
                self, "Нечего копировать",
                "Ни у одной программы нет команды или ссылки."
            )
            return
        text = "\n".join(pieces)
        QApplication.clipboard().setText(text)
        QMessageBox.information(
            self, "Скопировано",
            "Команды и ссылки скопированы в буфер обмена.\n"
            "Вставьте в терминал (Ctrl+Shift+V) или в браузер."
        )

    def _on_install(self, level: str):
        plan = self._install_plan(level)
        if not plan:
            return

        # Если есть manual — сначала показать диалог с инструкциями
        if plan["manual"]:
            self._show_manual_dialog(plan)

        # Если есть apt/pip — запустить в терминале
        if not plan["cmd"]:
            return

        ok = _open_terminal(plan["cmd"])
        if not ok:
            QMessageBox.warning(
                self, "Не нашёл терминал",
                "Не удалось открыть терминал автоматически.\n"
                "Нажмите «Скопировать команду» и вставьте "
                "её в свой терминал вручную."
            )
            return

        QMessageBox.information(
            self, "Установка запущена",
            "Открыт терминал. Дождитесь окончания установки.\n\n"
            "apt спросит пароль один раз и покажет прогресс по "
            "каждому пакету. Ctrl+C — отмена.\n\n"
            "После закрытия терминала нажмите «Обновить» здесь, "
            "чтобы увидеть изменения."
        )
