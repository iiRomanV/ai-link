"""AUDIT-PLAN-DIALOG (2026-10-08, Этап 4.2): просмотр и согласование плана.

Открывается кнопкой «📋 План» в Проекте, если в .ai_link/audits/
есть audit_plan.json.

Что видно:
  • Слева — дерево плана (этапы + items).
  • Цвета: ⚪ pending, 🟢 done, 🔴 rejected, 🔵 added.
  • Комментарий под item (если есть) — серым.

Кнопки:
  • ✓ Принять — превращает audit_plan.json в plan.json проекта.
  • ✕ Отменить — оставляет как есть (статус rejected).
  • 💬 Обсудить — отправляет в чат (пока заготовка).
"""
import json
from pathlib import Path
from typing import Any, Dict, Optional

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTreeWidget, QTreeWidgetItem, QMessageBox, QPlainTextEdit,
    QSplitter, QWidget, QMenu, QInputDialog,
)
from PySide6.QtCore import Qt, QPoint
from PySide6.QtGui import QColor, QFont, QAction


class AuditPlanDialog(QDialog):
    """Просмотр и согласование предварительного плана."""

    def __init__(self, project_dir: str, audit_id: str = "",
                 parent=None):
        super().__init__(parent)
        self.setWindowTitle("План доработки")
        self.resize(900, 640)
        self._project_dir = project_dir
        self._audit_id = audit_id or self._find_latest()
        self._data: Dict[str, Any] = {}
        self._setup_ui()
        self._load()

    # === Логика ===
    def _audits_dir(self) -> Path:
        return Path(self._project_dir) / ".ai_link" / "audits"

    def _find_latest(self) -> str:
        d = self._audits_dir()
        if not d.exists():
            return ""
        dirs = [p.name for p in d.iterdir()
                if p.is_dir()]
        if not dirs:
            return ""
        return sorted(dirs, reverse=True)[0]

    def _plan_path(self) -> Optional[Path]:
        if not self._audit_id:
            return None
        return (self._audits_dir() / self._audit_id
                / "audit_plan.json")

    def _load(self):
        p = self._plan_path()
        if p is None or not p.exists():
            QMessageBox.information(
                self, "План",
                "Предварительный план ещё не создан.\n"
                "Сначала прогоните аудит (режим full или deep)."
            )
            self.reject()
            return
        try:
            self._data = json.loads(p.read_text(encoding="utf-8"))
        except Exception as e:
            QMessageBox.warning(
                self, "Ошибка", f"Не удалось прочитать план: {e}"
            )
            self.reject()
            return
        self._render()

    def _render(self):
        self.tree.clear()
        plan = self._data.get("plan") or {}
        title = plan.get("title") or "План доработки"
        goal = plan.get("goal") or ""
        stages = plan.get("stages") or []

        root = QTreeWidgetItem(self.tree, [f"📋 {title}"])
        _f = root.font(0)
        _f.setBold(True)
        _f.setPointSize(_f.pointSize() + 1)
        root.setFont(0, _f)
        root.setForeground(0, QColor("#212529"))

        if goal:
            QTreeWidgetItem(root, [f"🎯 Цель: {goal[:200]}"])

        for si, s in enumerate(stages):
            name = s.get("name") or "(без названия)"
            items = s.get("items") or []
            done = sum(1 for it in items if it.get("status") == "done")
            total = len(items) or 1
            pct = int(done / total * 100)
            _letter = s.get("letter") or ""
            _stage_prefix = f"{_letter}. " if _letter else ""
            # RENDER-ORIGIN (2026-10-10): origin важнее status.
            # Убранный/добавленный этап → свой цвет, даже если
            # status='done' после финализации.
            _origin = s.get("origin") or ""
            _st_status = s.get("status") or ""
            if _origin == "removed":
                _st_icon = "🔴"
                _st_color = QColor("#dc3545")
            elif _origin == "added":
                _st_icon = "🔵"
                _st_color = QColor("#4A90E2")
            elif _st_status == "done":
                _st_icon = "🟢"
                _st_color = QColor("#28a745")
            elif _st_status == "partial":
                _st_icon = "🟡"
                _st_color = QColor("#ffc107")
            elif _st_status == "skipped":
                _st_icon = "⏭"
                _st_color = QColor("#6c757d")
            else:
                _st_icon = "⚪"
                _st_color = QColor("#6c757d")

            stage_node = QTreeWidgetItem(
                root,
                [f"{_st_icon} {_stage_prefix}{name} "
                 f"({done}/{total}) [{pct}%]"]
            )
            _sf = stage_node.font(0)
            _sf.setBold(True)
            stage_node.setFont(0, _sf)
            stage_node.setForeground(0, _st_color)

            for i, it in enumerate(items, 1):
                st = it.get("status", "pending")
                txt = it.get("text", "")
                icon = {
                    "done": "🟢", "rejected": "🔴",
                    "in_progress": "🔵", "added": "🔵",
                }.get(st, "⚪")
                _num = it.get("num")
                if _num and _letter:
                    _ip = f"{_letter}{_num}."
                elif _num:
                    _ip = f"{_num}."
                else:
                    _ip = f"{i}."
                _note = it.get("user_note", "") or ""
                _note_icon = "💬 " if _note else ""
                item_node = QTreeWidgetItem(
                    stage_node, [f"{_note_icon}{icon} {_ip} {txt}"]
                )
                # STEP3: пометка для правого клика.
                item_node.setData(0, Qt.UserRole,
                                  ("item", si, i - 1))
                _color = {
                    "done": "#28a745", "rejected": "#dc3545",
                    "in_progress": "#4A90E2", "added": "#4A90E2",
                }.get(st, "#6c757d")
                item_node.setForeground(0, QColor(_color))
                # Зачёркивание для rejected.
                if st == "rejected":
                    _itf = item_node.font(0)
                    _itf.setStrikeOut(True)
                    item_node.setFont(0, _itf)
                # Комментарий под item.
                cmt = it.get("user_comment", "")
                if cmt:
                    c_node = QTreeWidgetItem(
                        item_node, [f"   💬 {cmt[:200]}"]
                    )
                    c_node.setForeground(0, QColor("#999999"))
                    _cf = c_node.font(0)
                    _cf.setItalic(True)
                    c_node.setFont(0, _cf)
            stage_node.setExpanded(True)
        root.setExpanded(True)

        # Статус.
        st = self._data.get("status") or "draft"
        st_ru = {
            "draft": "⚪ Черновик (не согласован)",
            "accepted": "🟢 Принят",
            "rejected": "🔴 Отклонён",
        }.get(st, st)
        self.status_lbl.setText(
            f"Статус: {st_ru}  |  audit_id: {self._audit_id}"
        )

    # === UI ===
    def _setup_ui(self):
        root = QVBoxLayout(self)

        self.status_lbl = QLabel("Загрузка…")
        self.status_lbl.setStyleSheet(
            "padding: 4px; font-weight: bold;"
        )
        root.addWidget(self.status_lbl)

        hint = QLabel(
            "Это ПРЕДВАРИТЕЛЬНЫЙ план. Пока не нажмёте «✓ Принять» "
            "— ничего не применяется."
        )
        hint.setStyleSheet("color: gray; padding: 4px;")
        hint.setWordWrap(True)
        root.addWidget(hint)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["План доработки"])
        self.tree.setColumnWidth(0, 800)
        root.addWidget(self.tree, stretch=1)

        # STEP3: правый клик по item'у → заметка пользователя.
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(
            self._on_tree_context_menu
        )

        # Кнопки.
        btns = QHBoxLayout()
        self.btn_accept = QPushButton("✓ Принять")
        self.btn_accept.setToolTip(
            "Превратить в plan.json проекта и активировать «🛠 Доработать»."
        )
        self.btn_accept.clicked.connect(self._on_accept)

        self.btn_reject = QPushButton("✕ Отменить")
        self.btn_reject.setToolTip(
            "Отклонить план. Файл остаётся как история."
        )
        self.btn_reject.clicked.connect(self._on_reject)

        self.btn_discuss = QPushButton("💬 Обсудить")
        self.btn_discuss.setToolTip(
            "Обсуждение плана с LLM (пока — через поле ввода Проекта)."
        )
        self.btn_discuss.clicked.connect(self._on_discuss)

        btns.addWidget(self.btn_accept)
        btns.addWidget(self.btn_reject)
        btns.addWidget(self.btn_discuss)
        btns.addStretch()
        root.addLayout(btns)

    # === Обработчики ===
    def _on_accept(self):
        p = self._plan_path()
        if p is None or not p.exists():
            return
        # Копируем plan в plan.json проекта.
        project_plan = (Path(self._project_dir)
                        / ".ai_link" / "plan.json")
        plan = self._data.get("plan") or {}

        # Дополняем обязательные поля.
        import getpass
        try:
            from config.config_manager import get_setting
            _user = (get_setting("user_name", "") or "").strip()
            if not _user:
                _user = getpass.getuser() or ""
        except Exception:
            _user = ""

        plan["status"] = "in_progress"
        plan["created_by_user"] = _user
        plan["updated_at"] = _now()
        plan.setdefault("task", self._data.get("user_hint") or "")

        # Если plan.json уже есть — спросим.
        if project_plan.exists():
            ans = QMessageBox.question(
                self, "Перезаписать план?",
                "В проекте уже есть plan.json.\n"
                "Перезаписать его новым планом из аудита?",
                QMessageBox.Yes | QMessageBox.No,
            )
            if ans != QMessageBox.Yes:
                return

        try:
            project_plan.parent.mkdir(parents=True, exist_ok=True)
            project_plan.write_text(
                json.dumps(plan, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as e:
            QMessageBox.warning(
                self, "Ошибка", f"Не сохранить plan.json: {e}"
            )
            return

        # Обновляем статус audit_plan.json.
        self._data["status"] = "accepted"
        try:
            p.write_text(
                json.dumps(self._data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception:
            pass

        QMessageBox.information(
            self, "Готово",
            "План принят. Теперь доступна кнопка «🛠 Доработать»."
        )
        self.accept()

    def _on_reject(self):
        p = self._plan_path()
        if p is None or not p.exists():
            return
        ans = QMessageBox.question(
            self, "Отклонить план?",
            "Отклонить предварительный план?\n"
            "Файл сохранится в audits/ как история.",
            QMessageBox.Yes | QMessageBox.No,
        )
        if ans != QMessageBox.Yes:
            return
        self._data["status"] = "rejected"
        try:
            p.write_text(
                json.dumps(self._data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception:
            pass
        QMessageBox.information(
            self, "Отклонено",
            "План отклонён. Остаётся в audits/ как история."
        )
        self.accept()

    def _on_discuss(self):
        """PLAN-DISCUSS: открыть чат-обсуждение с LLM."""
        plan = self._data.get("plan") or {}
        if not plan.get("stages"):
            QMessageBox.information(
                self, "Нет плана",
                "План пуст — нечего обсуждать."
            )
            return

        from ui.dialogs.plan_discuss_dialog import PlanDiscussDialog
        dlg = PlanDiscussDialog(plan, parent=self)
        if dlg.exec() != QDialog.Accepted:
            return

        new_plan = dlg.get_new_plan()
        if not new_plan:
            return

        # Сохраняем старую версию в versions/
        self._save_version()

        # Обновляем данные
        self._data["plan"] = new_plan
        self._data["status"] = "draft"  # снова черновик — не согласован
        self._data["updated_at"] = _now()

        # Пишем на диск
        self._save_plan()
        # Обновляем дерево
        self._render()

        QMessageBox.information(
            self, "Готово",
            "План обновлён по итогам обсуждения.\n"
            "Проверьте изменения и нажмите «✓ Принять», "
            "когда всё устроит."
        )

    def _save_version(self):
        """Сохраняет текущий план в versions/ (до 5 версий)."""
        p = self._plan_path()
        if p is None or not p.exists():
            return
        try:
            vdir = p.parent / "versions"
            vdir.mkdir(exist_ok=True)

            # Имя: audit_plan_v<N>_<HHMMSS>.json
            existing = sorted(vdir.glob("audit_plan_v*.json"))
            n = len(existing) + 1
            from datetime import datetime
            ts = datetime.now().strftime("%H%M%S")
            vfile = vdir / f"audit_plan_v{n}_{ts}.json"

            # Копируем текущий
            vfile.write_text(
                json.dumps(self._data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            print(f"[PLAN-DISCUSS] сохранена версия: {vfile.name}")

            # Чистим старые (оставляем 5 свежих)
            all_v = sorted(vdir.glob("audit_plan_v*.json"))
            for old_v in all_v[:-5]:
                try:
                    old_v.unlink()
                except Exception:
                    pass
        except Exception as e:
            print(f"[PLAN-DISCUSS] ошибка сохранения версии: {e}")


    # === STEP3: заметки пользователя ===
    def _save_plan(self):
        """Сохраняет текущий audit_plan.json на диск."""
        p = self._plan_path()
        if p is None:
            return
        try:
            p.write_text(
                json.dumps(self._data, ensure_ascii=False,
                           indent=2),
                encoding="utf-8",
            )
        except Exception as e:
            QMessageBox.warning(
                self, "Ошибка",
                f"Не удалось сохранить план: {e}"
            )

    def _on_tree_context_menu(self, pos: QPoint):
        """Правый клик по дереву: меню заметки для item'а."""
        item = self.tree.itemAt(pos)
        if item is None:
            return
        data = item.data(0, Qt.UserRole)
        if not isinstance(data, tuple) or len(data) != 3:
            return
        kind, si, ii = data
        if kind != "item":
            return

        plan = self._data.get("plan") or {}
        stages = plan.get("stages") or []
        if si >= len(stages):
            return
        items = stages[si].get("items") or []
        if ii >= len(items):
            return

        note = (items[ii].get("user_note") or "").strip()

        menu = QMenu(self)
        if note:
            act_edit = QAction("✏ Изменить заметку", self)
            act_del = QAction("🗑 Удалить заметку", self)
            menu.addAction(act_edit)
            menu.addAction(act_del)
        else:
            act_edit = QAction("📝 Добавить заметку", self)
            act_del = None
            menu.addAction(act_edit)

        chosen = menu.exec(self.tree.viewport().mapToGlobal(pos))
        if chosen is None:
            return
        if chosen == act_edit:
            self._edit_note(si, ii)
        elif act_del is not None and chosen == act_del:
            self._clear_note(si, ii)

    def _edit_note(self, si: int, ii: int):
        """Диалог редактирования заметки. Пустая строка = удалить."""
        plan = self._data.get("plan") or {}
        stages = plan.get("stages") or []
        items = stages[si].get("items") or []
        old = items[ii].get("user_note") or ""

        text, ok = QInputDialog.getMultiLineText(
            self, "Заметка пользователя",
            "Только для вас. Link её НЕ читает.\n"
            "Оставьте пустой — заметка удалится.",
            old,
        )
        if not ok:
            return
        new = (text or "").strip()
        if new:
            items[ii]["user_note"] = new
        else:
            items[ii].pop("user_note", None)
        self._save_plan()
        self._render()

    def _clear_note(self, si: int, ii: int):
        """Удалить заметку без диалога."""
        plan = self._data.get("plan") or {}
        stages = plan.get("stages") or []
        items = stages[si].get("items") or []
        items[ii].pop("user_note", None)
        self._save_plan()
        self._render()


def _now() -> str:
    from datetime import datetime
    return datetime.now().isoformat(timespec="seconds")
