"""Вкладка «Диалог» — общение с Link + управление задачей.

Показывает переписку с Link. Позволяет:
  • запустить задачу в фоне (Start)
  • приостановить (Pause)
  • остановить (Stop)
  • просто написать сообщение Link (пока — заглушка, без ответа)
"""

from pathlib import Path
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout,
                               QTextEdit, QPushButton, QLabel,
                               QMessageBox, QFileDialog,
                               QDialog, QRadioButton, QButtonGroup,
                               QFrame)
from PySide6.QtCore import Qt, QTimer, Signal, QThread
from PySide6.QtGui import QTextCursor, QFont
from datetime import datetime

import subprocess
from core.dialog import (
    ROLE_USER, ROLE_LINK, ROLE_SYSTEM,
    create_session, add_message, save_session, load_messages,
)
from core.background_task import (
    BackgroundTask,
    ST_IDLE, ST_RUNNING, ST_PAUSED, ST_STOPPING,
    ST_DONE, ST_FAILED, ST_STOPPED, ST_NEEDS_CLARIFICATION,
)


# Цвета для ролей (используем простой HTML)
COLOR_USER = "#0066cc"
COLOR_LINK = "#008844"
COLOR_SYSTEM = "#888888"


class _DialogWorker(QThread):
    """Отправляет вопрос ИИ как текст (без оркестратора).

    AM: если передан project_dir — модель видит контекст проекта:
      • память проекта (build_context)
      • план (plan.json)
      • историю диалога (последние 10 сообщений)
      • концепцию (concept.md, если есть)

    Режим обсуждения — модель отвечает как консультант,
    задаёт вопросы, предлагает варианты, НЕ пишет код.
    """
    got_answer = Signal(str)
    got_error = Signal(str)

    def __init__(self, task: str, site: str = "deepseek",
                 project_dir: str = "",
                 save_as_concept: bool = False,
                 agent: str = ""):
        super().__init__()
        self.task = task
        self.site = site
        self.project_dir = project_dir
        # AO: сохранить ответ в concept.md (а не просто показать)
        self.save_as_concept = save_as_concept
        # AM: какой агент использовать (gigachat / ollama /
        # browser_* / пусто → старый site). Если agent задан —
        # используем его через _call_agent.
        self.agent = (agent or "").strip()

    @staticmethod
    def _clean_answer(text: str) -> str:
        """AT + AV + AV2: убирает служебные строки и reasoning-блок
        DeepSeek в начале ответа.

        Убираем:
          • «Thought for N seconds», «Thinking...»
          • AV/AV2: reasoning на английском и русском:
              «We need...», «The user...», «Мы должны...»,
              «Сформулируем ответ», «Напишем ответ»,
              «Plan phases:», «Structure:», «Let's propose» и др.

        AV2: если reasoning многострочный (несколько абзацев),
        ищем первый абзац, который начинается с приветствия
        или вводного слова — это и есть реальный ответ.
        """
        if not text:
            return text
        import re as _re
        # 1. Thought for N seconds
        text = _re.sub(
            r"^\s*Thought for\s+\d+\s+seconds?\s*\n+",
            "", text, flags=_re.IGNORECASE,
        )
        # 2. Thinking...
        text = _re.sub(
            r"^\s*Thinking[\.\:]+\s*\n+", "", text,
            flags=_re.IGNORECASE,
        )

        # 3. AV/AV2: маркеры reasoning — начало строки
        _reason_markers = _re.compile(
            # EN
            r"^(We need|We should|We are |The user|"
            r"User (wants|asks|provided|said|says)|"
            r"User's request|I need|I should|I will|"
            r"Let me |Let's |Plan phases|Structure|"
            r"Now I |So I |OK, |Okay, |Alright,|"
            # RU
            r"Мы должны|Надо |Нужно |"
            r"Сформулируем|Напишем|Теперь наш|"
            r"Ответ должен|Также можно |"
            r"Структура:|План:|Этапы:|"
            r"Проверим|Подумаем|Разберём|"
            r"Итак, |План работ|Ключевые|"
            r"Можно предложить|Возможно|"
            r"После этого|Затем|Далее)",
            _re.IGNORECASE,
        )

        # 4. AV2: маркеры НАЧАЛА ОТВЕТА
        _answer_starters = _re.compile(
            r"^(Привет|Здравствуй|Отлично|Понял|Понятно|"
            r"Спасибо|Хорошо|Согласен|Давай|Начнём|Начнем|"
            r"Смотрю|Вижу|Рад|Окей|Ок\.|"
            r"Hi |Hello|Great|Sure|Of course|"
            r"Here's |Here is |Let me help)",
            _re.IGNORECASE,
        )

        # Сначала режем строки-маркеры reasoning подряд
        lines = text.split("\n")
        answer_start = 0
        for i, line in enumerate(lines):
            s = line.strip()
            if not s:
                continue
            if _reason_markers.match(s):
                continue
            answer_start = i
            break
        text = "\n".join(lines[answer_start:]).strip()

        # AV2: если остался ещё reasoning-абзац —
        # ищем первый абзац, начинающийся с answer_starter
        if "\n\n" in text:
            paragraphs = text.split("\n\n")
            for i, para in enumerate(paragraphs):
                s = para.strip()
                if not s:
                    continue
                if _answer_starters.match(s):
                    text = "\n\n".join(paragraphs[i:]).strip()
                    break

        # AV3: если после AV2.1 всё ещё длинный текст, начинается
        # с reasoning и НЕ содержит пустой строки — DeepSeek
        # записал весь ответ в ОДНОМ абзаце. Пробуем вытащить
        # ответ явными регекспами.
        if (len(text) > 100
                and _reason_markers.match(text[:120])):
            _extracted = _DialogWorker._extract_short_answer(text)
            if _extracted:
                text = _extracted

        return text

    @staticmethod
    def _extract_short_answer(text: str):
        """AV3: вытаскивает ответ из однострочного reasoning.

        Ищет явные маркеры ответа: Answer: X / Ответ: X /
        Just "X" / Верный ответ: X.
        Возвращает найденную строку или None.
        """
        import re as _re
        # Кавычки внутри регекспов — используем одинарные
        # внешние строки, чтобы не конфликтовать с двойными.
        patterns = [
            r'Ответ:\s*([^\n\.\!\?]+)',
            r'Answer:\s*([^\n\.\!\?]+)',
            r'Just\s+"([^"]+)"',
            r'Верный ответ:\s*([^\n\.\!\?]+)',
            r'Итог:\s*([^\n\.\!\?]+)',
        ]
        for pat in patterns:
            m = _re.search(pat, text, flags=_re.IGNORECASE)
            if m:
                cand = (m.group(1) or "").strip()
                # Отсекаем мусор: слишком длинное — не ответ.
                if 1 <= len(cand) <= 80:
                    return cand
        return None

    @staticmethod
    def _sanitize_status(text: str) -> str:
        """AS: заменяет «плохие» статусы на нейтральное.

        Иначе модель может взять строку «Статус: too_many_errors»
        и отдать её как ответ (эхо контекста).
        """
        bad = ("too_many_errors", "exception", "timeout",
               "failed", "max_phases", "success_partial")
        out = []
        for line in text.splitlines():
            s = line.strip().lower()
            if s.startswith("статус:"):
                val = s.split(":", 1)[1].strip()
                if any(b in val for b in bad):
                    out.append("Статус: idle")
                    continue
            out.append(line)
        return "\n".join(out)

    def _run_concept_mode(self):
        """CONCEPT: генерация concept.md через выбранного агента.

        Не занимает браузер, не зависит от сессии DeepSeek.
        Агент берётся из agent_assignments.json → concept.
        """
        try:
            from core.concept_maker import generate_concept
            text, source = generate_concept(self.project_dir)
        except Exception as e:
            self.got_error.emit(str(e))
            return

        try:
            from pathlib import Path as _P
            concept = (
                _P(self.project_dir) / ".ai_link" / "concept.md"
            )
            concept.parent.mkdir(parents=True, exist_ok=True)
            concept.write_text(text, encoding="utf-8")
        except Exception as e:
            self.got_error.emit(
                f"Не удалось сохранить concept.md: {e}"
            )
            return

        # Результат показываем пользователю. В dialog.jsonl
        # это писать НЕ надо — концепция артефакт, не реплика.
        self.got_answer.emit(
            f"✅ Концепция сохранена (агент: {source})\n\n"
            + text
        )

    def _collect_context(self) -> str:
        """Собирает контекст проекта для модели."""
        parts = []

        # 1. Память проекта (build_context)
        try:
            from core.project_state import build_context
            ctx = build_context(self.project_dir, budget_chars=2500)
            if ctx:
                # AS: фильтруем «плохой» статус, чтобы модель его не
                # повторила как ответ
                ctx = self._sanitize_status(ctx)
                parts.append("=== ПАМЯТЬ ПРОЕКТА ===")
                parts.append(ctx)
                parts.append("")
        except Exception:
            pass

        # 2. Концепция (concept.md, задача AO — пока не создаётся)
        try:
            from pathlib import Path as _P
            concept = _P(self.project_dir) / ".ai_link" / "concept.md"
            if concept.exists():
                ctext = concept.read_text(encoding="utf-8")[:2500]
                if ctext.strip():
                    parts.append("=== КОНЦЕПЦИЯ ПРОЕКТА ===")
                    parts.append(ctext)
                    parts.append("")
        except Exception:
            pass

        # 3. Последние 10 сообщений диалога
        try:
            from core.dialog import load_messages
            msgs = load_messages(self.project_dir, limit=10)
            if msgs:
                parts.append("=== ИСТОРИЯ ДИАЛОГА (последние) ===")
                # AR-3: фильтр мусора — эхо контекста и
                # служебные сообщения не должны попадать
                # обратно в промпт (иначе цикл замыкается).
                _skip_texts = {
                    ".md", "requirements.txt",
                    "Думаю над ответом...",
                    "📝 Формирую концепцию проекта...",
                }
                for m in msgs[-10:]:
                    role = "Вы" if m.role == "user" else "Link"
                    txt = (m.text or "").strip()[:300]
                    if not txt:
                        continue
                    if role == "Link" and txt in _skip_texts:
                        continue
                    # Мусор: короткие ответы Link без смысла
                    if role == "Link" and len(txt) < 5:
                        continue
                    parts.append(f"{role}: {txt}")
                parts.append("")
        except Exception:
            pass

        return "\n".join(parts)

    def run(self):
        try:
            from core.agents.browser_agent import query_browser_ai

            # CONCEPT: генерируем концепцию через выбранного
            # агента (не через браузер). См. core/concept_maker.py.
            # _run_concept_mode сам эмитит got_answer/got_error
            # и завершает поток.
            if self.save_as_concept and self.project_dir:
                self._run_concept_mode()
                return
            # AM: если есть проект — режим обсуждения концепции
            if self.project_dir:
                context = self._collect_context()
                prompt = (
                    "Ты — Link, помощник-разработчик. "
                    "Идёт ОБСУЖДЕНИЕ КОНЦЕПЦИИ проекта.\n\n"
                    "Твоя задача:\n"
                    "  • понять, что хочет пользователь\n"
                    "  • задавать уточняющие вопросы\n"
                    "  • предлагать варианты\n"
                    "  • помогать сформулировать требования\n"
                    "  • НЕ писать код\n"
                    "  • отвечать по-русски, обычным текстом, "
                    "без JSON и без tool_call\n\n"
                    "AM: если пользователь явно согласился с "
                    "чем-то («да», «согласен», «давай так») — "
                    "в КОНЦЕ ответа добавь короткий блок:\n"
                    "  ✅ Решено: <одна строка — что зафиксировано>\n"
                    "Если ничего не решили — блок НЕ добавляй.\n\n"
                    "=== ТЕКУЩЕЕ СООБЩЕНИЕ ПОЛЬЗОВАТЕЛЯ ===\n"
                    f"{self.task}\n"
                    "=== КОНЕЦ ТЕКУЩЕГО СООБЩЕНИЯ ===\n\n"
                    "=== СПРАВОЧНЫЙ КОНТЕКСТ (НЕ КОПИРУЙ ЕГО) ===\n"
                    f"{context}\n"
                    "=== КОНЕЦ КОНТЕКСТА ===\n\n"
                    "ВАЖНО: ответь ТОЛЬКО на ТЕКУЩЕЕ СООБЩЕНИЕ "
                    "ПОЛЬЗОВАТЕЛЯ. НЕ цитируй контекст, "
                    "НЕ копируй директорию проекта, "
                    "НЕ пересказывай статус.\n"
                )
            else:
                # Простой чат без проекта
                prompt = (
                    "Ответь на вопрос обычным текстом, без JSON, "
                    "без формата tool_call. Просто ответь по-русски.\n\n"
                    f"Вопрос: {self.task}"
                )

            # AM: если задан agent — используем универсальный
            # _call_agent (gigachat / ollama / browser_*).
            # Иначе — старый путь через site.
            if self.agent:
                from core.agentic_loop import _call_agent
                r = _call_agent(self.agent, prompt)
            else:
                r = query_browser_ai(self.site, prompt)
            if r.get("success"):
                raw = r.get("text", "") or "(пусто)"
                # AT: убираем «Thought for N seconds»
                clean = self._clean_answer(raw)

                # AO: сохранить в concept.md
                if self.save_as_concept and self.project_dir:
                    try:
                        from pathlib import Path as _P
                        concept = (
                            _P(self.project_dir)
                            / ".ai_link" / "concept.md"
                        )
                        concept.parent.mkdir(
                            parents=True, exist_ok=True,
                        )
                        concept.write_text(clean, encoding="utf-8")
                        self.got_answer.emit(
                            "✅ Концепция сохранена в "
                            "concept.md\n\n" + clean
                        )
                    except Exception as e:
                        self.got_error.emit(
                            f"Не удалось сохранить концепцию: {e}"
                        )
                else:
                    self.got_answer.emit(clean)
            else:
                self.got_error.emit(
                    r.get("error") or r.get("reason") or "Не получилось"
                )
        except Exception as e:
            self.got_error.emit(str(e))



class _TestGenWorker(QThread):
    """A15: генерация тестов в фоне (без зависания UI)."""

    got_result = Signal(object)
    got_error = Signal(str)

    def __init__(self, project_dir: str,
                 max_functions: int = 5,
                 agent_mode: str = "browser_deepseek"):
        super().__init__()
        self.project_dir = project_dir
        self.max_functions = max_functions
        self.agent_mode = agent_mode

    def run(self):
        try:
            from core.audit_project import generate_tests_for_project
            r = generate_tests_for_project(
                self.project_dir,
                max_functions=self.max_functions,
                agent_mode=self.agent_mode,
            )
            self.got_result.emit(r)
        except Exception as e:
            self.got_error.emit(str(e))


class _AuditWorker(QThread):
    """AUDIT-WORKER (2026-10-08): аудит + план доработки в фоне,
    чтобы GUI не замирал на 20-40 минут."""

    progress   = Signal(str)
    got_result = Signal(dict)
    got_error  = Signal(str)

    def __init__(self, project_dir: str, mode: str,
                 max_time_min: int, user_hint: str = ""):
        super().__init__()
        self.project_dir  = project_dir
        self.mode         = mode
        self.max_time_min = max_time_min
        self.user_hint    = user_hint

    def _cb(self, *args):
        try:
            if len(args) >= 2:
                self.progress.emit(f"  [{args[1]}%] {args[0]}")
            elif args:
                self.progress.emit(f"  {args[0]}")
        except Exception:
            pass

    def run(self):
        try:
            from core.audit_project import run_full_audit
            audit = run_full_audit(
                self.project_dir,
                mode=self.mode,
                max_time_min=self.max_time_min,
                progress_cb=self._cb,
            )
        except Exception as e:
            self.got_error.emit(f"Аудит не удался: {e}")
            return

        plan = None
        if (audit.get("status") != "failed"
                and audit.get("audit_id")
                and self.mode in ("full", "deep")):
            try:
                self.progress.emit("📋 Строю план доработки...")
                from core.audit_planner import build_plan_from_audit
                plan = build_plan_from_audit(
                    self.project_dir,
                    audit["audit_id"],
                    user_hint=self.user_hint,
                )
            except Exception as _e:
                plan = {"ok": False, "error": str(_e)[:200]}

        self.got_result.emit({"audit": audit, "plan": plan})


class _RetryPhaseDialog(QDialog):
    """AG: диалог выбора фазы для «Попробовать ещё раз».

    Показывает фазы с описаниями, рекомендованную — сверху с ⭐.
    """

    PHASES = [
        ("review", "проверить готовый код",
         "Если код уже есть — review найдёт проблемы"),
        ("code", "писать / дописать код",
         "Если кода нет или он неполный"),
        ("plan", "переделать план",
         "Если план плохой или его нет"),
        ("fix", "исправить замечания review",
         "Если review нашёл проблемы"),
        ("fallback", "резервный агент",
         "Если основные агенты не справляются"),
    ]

    def __init__(self, task_text: str, recommended: str,
                 parent=None):
        super().__init__(parent)
        self.setWindowTitle("Попробовать ещё раз")
        self.resize(540, 420)
        self._choice = ""
        self._group = QButtonGroup(self)
        self._setup_ui(task_text, recommended)

    def _setup_ui(self, task_text: str, recommended: str):
        layout = QVBoxLayout(self)

        title = QLabel(f"<b>Задача:</b> {task_text[:150]}")
        title.setWordWrap(True)
        layout.addWidget(title)

        hint = QLabel(
            "Выбери фазу, с которой продолжить.<br>"
            "<b>Рекомендация:</b> если код уже есть — "
            "выбирай <b>review</b>."
        )
        hint.setStyleSheet(
            "color: #333; padding: 6px; "
            "background: #f5f5f5; border-radius: 4px;"
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)

        layout.addWidget(QLabel("<b>С какой фазы начать?</b>"))

        # Рекомендацию — в начало списка
        ordered = []
        for phase in self.PHASES:
            if phase[0] == recommended:
                ordered.insert(0, phase)
            else:
                ordered.append(phase)

        first_checked = False
        for phase_id, what, why in ordered:
            is_rec = (phase_id == recommended)

            row = QWidget()
            row_layout = QVBoxLayout(row)
            row_layout.setContentsMargins(0, 2, 0, 6)
            row_layout.setSpacing(2)

            label = f"<b>{phase_id}</b> — {what}"
            if is_rec:
                label = "⭐ " + label + "  <i>(рекомендуется)</i>"

            rb = QRadioButton()
            rb.setText(label)
            rb.setToolTip(why)
            rb.setProperty("phase_id", phase_id)
            self._group.addButton(rb)
            row_layout.addWidget(rb)

            why_lbl = QLabel(f"    {why}")
            why_lbl.setStyleSheet(
                "color: #666; font-size: 9pt; padding-left: 24px;"
            )
            why_lbl.setWordWrap(True)
            row_layout.addWidget(why_lbl)

            layout.addWidget(row)

            if is_rec and not first_checked:
                rb.setChecked(True)
                first_checked = True

        if not first_checked and self._group.buttons():
            self._group.buttons()[0].setChecked(True)

        layout.addStretch()

        btns = QHBoxLayout()
        btns.addStretch()
        ok_btn = QPushButton("OK")
        ok_btn.setDefault(True)
        ok_btn.clicked.connect(self._on_ok)
        btns.addWidget(ok_btn)
        cancel_btn = QPushButton("Отмена")
        cancel_btn.clicked.connect(self.reject)
        btns.addWidget(cancel_btn)
        layout.addLayout(btns)

    def _on_ok(self):
        btn = self._group.checkedButton()
        if btn:
            self._choice = btn.property("phase_id") or ""
        self.accept()

    def get_choice(self) -> str:
        return self._choice



class _AuditModeDialog(QDialog):
    """Диалог выбора режима аудита.

    Возвращает:
      mode: "fast" | "full" | "deep"
      max_time_min: int (1-180)
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Аудит проекта")
        self.resize(420, 320)
        self._mode = "fast"
        self._max_time = 5

        layout = QVBoxLayout(self)

        intro = QLabel(
            "Выберите режим аудита.\n"
            "Технический — мгновенно, без LLM.\n"
            "LLM-режимы (full/deep) — в разработке."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        # === Радиокнопки режима ===
        self._group = QButtonGroup(self)
        options = [
            ("fast", "Быстрый (технический, без LLM)"),
            ("full", "Полный (+ LLM: описание, идеи) [скоро]"),
            ("deep", "Глубокий (+ все файлы, smoke) [скоро]"),
        ]
        for i, (val, label) in enumerate(options):
            rb = QRadioButton(label)
            rb.setProperty("val", val)
            if val == "fast":
                rb.setChecked(True)
            self._group.addButton(rb, i)
            layout.addWidget(rb)

        # === Лимит времени ===
        time_layout = QHBoxLayout()
        time_layout.addWidget(QLabel("Макс. время:"))
        self._time_edit = QTextEdit()
        self._time_edit.setMaximumHeight(30)
        self._time_edit.setPlainText("5")
        time_layout.addWidget(self._time_edit)
        time_layout.addWidget(QLabel("мин"))
        layout.addLayout(time_layout)

        warn = QLabel(
            "⚠️ Если время выйдет — аудит остановится, "
            "результат сохранится."
        )
        warn.setStyleSheet("color: gray; font-size: 10px;")
        warn.setWordWrap(True)
        layout.addWidget(warn)

        # === Кнопки ===
        btns = QHBoxLayout()
        ok = QPushButton("▶ Начать")
        ok.clicked.connect(self._on_ok)
        btns.addWidget(ok)
        cancel = QPushButton("Отмена")
        cancel.clicked.connect(self.reject)
        btns.addWidget(cancel)
        btns.addStretch()
        layout.addLayout(btns)

    def _on_ok(self):
        # Режим из выбранной радиокнопки
        for rb in self._group.buttons():
            if rb.isChecked():
                self._mode = rb.property("val") or "fast"
                break
        # Лимит времени
        try:
            t = int((self._time_edit.toPlainText() or "5").strip())
        except Exception:
            t = 5
        if t < 1:
            t = 1
        if t > 180:
            t = 180
        self._max_time = t
        self.accept()

    def get_result(self):
        return {"mode": self._mode, "max_time_min": self._max_time}



class DialogTab(QWidget):
    """Вкладка «Диалог»: переписка с Link + панель управления."""

    # Сигналы для безопасного обновления UI из фонового потока
    _sig_progress = Signal(str, object)
    _sig_finish = Signal(object)
    # Сигналы для диалогового ответа
    _sig_dialog_answer = Signal(str)
    _sig_dialog_error = Signal(str)
    # Сигнал для главного окна — статус задачи
    status_changed = Signal(str)
    # Сигнал для дублирования строк в «Логи» и «Лог проекта»
    log_line = Signal(str)
    # LR-UI-FINAL: запрос «Открыть карту» → main_window
    # переключит на вкладку «Карта проекта» в режиме редактирования
    open_map_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._session = None
        self._task: BackgroundTask = None
        self._project_dir = ""
        # Состояние уточнения
        self._pending_question = ""
        self._pending_phase = ""
        self._pending_task_text = ""
        self._pending_answers = []
        self._pending_checkpoint = {}
        # AE: запоминаем последнюю фаз/задачу для «Попробовать ещё раз»
        self._last_phase = ""
        self._last_task_text = ""
        self._last_failed = False
        # Шаг 3b: последний audit_id — для кнопок «Открыть отчёт»
        # и «Работать по плану»
        self._last_audit_id = ""
        # A15: воркер генерации тестов (живёт пока идёт)
        self._testgen_worker = None
        self._setup_ui()

        # Подключаем сигналы к слотам (в главном потоке)
        self._sig_progress.connect(self._do_progress)
        self._sig_finish.connect(self._do_finish)
        self._sig_dialog_answer.connect(self._do_dialog_answer)
        self._sig_dialog_error.connect(self._do_dialog_error)
        # Диалоговый воркер (создаётся по запросу)
        self._dialog_worker: _DialogWorker = None

        # Таймер для обновления статуса
        self._status_timer = QTimer(self)
        self._status_timer.timeout.connect(self._refresh_status)
        self._status_timer.start(1000)

    # === Установка проекта ===

    def set_project_dir(self, project_dir: str):
        """Загружает диалог для проекта. Вызывается из main_window."""
        if not project_dir:
            return
        if project_dir == self._project_dir:
            # LR-UI: тот же проект, но панель могла быть закрыта —
            # проверяем чекпоинт/план заново и показываем, если надо.
            try:
                self._check_checkpoint_on_project_open(project_dir)
            except Exception:
                pass
            return

        # FIX-AUDIT-LATEST (2026-10-08): при смене проекта сбрасываем
        # кэш audit_id. Следующий запрос найдёт последний с диска.
        self._last_audit_id = ""

        # Даже при смене проекта — сначала проверим чекпоинт (панель).
        # _project_dir поменяется ниже; но проверка работает
        # по переданному пути, а не по self._project_dir.

        # AD.D: смена проекта → очистка браузеров.
        # Модель в браузере помнит контекст старого проекта.
        # Новый проект — новый чат, чистые браузеры.
        # Первый проект (self._project_dir был пустой) не чистим —
        # там ещё нечего чистить.
        if self._project_dir:
            try:
                from core.agents.browser_manager import close_browser
                from core.browser_cleanup import cleanup_stale_browsers
                close_browser()
                cleanup_stale_browsers()
                self._append_system(
                    "Смена проекта — браузеры очищены для нового чата."
                )
            except Exception:
                pass

        self._project_dir = project_dir
        self._session = create_session(project_dir)

        # AS: если в state.json «битый» статус — сбросить в idle.
        # Иначе он попадёт в контекст диалога и модель его повторит.
        try:
            from core.project_state import load_state, save_state
            _st = load_state(project_dir)
            _w = _st.get("work", {})
            _bad = ("too_many_errors", "exception", "timeout",
                    "failed", "max_phases")
            if _w.get("current_status") in _bad:
                _w["current_status"] = "idle"
                _w["current_task"] = ""
                _w["current_step"] = ""
                save_state(project_dir, _st)
        except Exception:
            pass

        # Очищаем окно перед загрузкой новой истории
        self.output.clear()
        # Загружаем историю с диска
        msgs = load_messages(project_dir)
        if msgs:
            self._append_message_html("system",
                f"Проект: {project_dir}", "system")
            for m in msgs:
                self._append_message_html(m.role, m.text, m.kind, m.ts)
        else:
            self._append_message_html("system",
                f"Проект: {project_dir} — новый диалог.", "system")

    # === UI ===

    def showEvent(self, event):
        """LR-UI: при показе вкладки — обновить панель чекпоинта.

        Пользователь мог переключиться на Диалог — там уже
        может быть незавершённая задача.
        """
        super().showEvent(event)
        try:
            if self._project_dir:
                self._check_checkpoint_on_project_open(self._project_dir)
        except Exception:
            pass

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        # Панель статуса
        status_layout = QHBoxLayout()
        self.status_label = QLabel("Статус: —")
        self.status_label.setStyleSheet("font-weight: bold;")
        status_layout.addWidget(self.status_label)
        status_layout.addStretch()
        layout.addLayout(status_layout)

        # Область вывода сообщений — СОЗДАЁМ здесь, но добавим
        # в layout ниже, ПОСЛЕ поля ввода (B5, 2026-10-07).
        self.output = QTextEdit()
        self.output.setReadOnly(True)
        font = QFont("Monospace")
        font.setStyleHint(QFont.Monospace)
        self.output.setFont(font)
        self.output.setStyleSheet("background-color: #fafafa; padding: 8px;")

        # LONG-RUN: панель «Незавершённая задача» — появляется,
        # когда есть чекпоинт (status="has_checkpoint").
        # Скрыта по умолчанию. Заменяет QMessageBox (тот рендерится
        # inline внутри QTabWidget и кнопки не работают).
        self._checkpoint_panel = QFrame()
        self._checkpoint_panel.setFrameShape(QFrame.StyledPanel)
        self._checkpoint_panel.setStyleSheet(
            "QFrame { background: #fff8e1; border: 1px solid #ffc107; "
            "border-radius: 4px; padding: 8px; }"
            "QLabel { background: transparent; }"
        )
        cp_layout = QVBoxLayout(self._checkpoint_panel)
        cp_layout.setContentsMargins(10, 8, 10, 8)
        self._cp_label = QLabel("")
        self._cp_label.setWordWrap(True)
        self._cp_label.setStyleSheet(
            "font-size: 12pt; background: transparent; color: #333;"
        )
        cp_layout.addWidget(self._cp_label)
        # LR-UI-FINAL-LOGIC: 2 кнопки + крестик
        cp_btns = QHBoxLayout()
        self._cp_continue_btn = QPushButton("▶ Продолжить")
        self._cp_continue_btn.setToolTip(
            "Продолжить с сохранённого места.\n"
            "Если в поле ввода есть текст — он станет уточнением\n"
            "к текущей задаче (Link решит: вложить или новый этап)."
        )
        self._cp_open_map_btn = QPushButton("📋 Открыть карту")
        self._cp_open_map_btn.setToolTip(
            "Открыть «Карту проекта» в режиме редактирования.\n"
            "Там можно кликнуть на этап/шаг и выбрать\n"
            "«Переделать отсюда»."
        )
        self._cp_close_btn = QPushButton("✕")
        self._cp_close_btn.setFixedWidth(32)
        self._cp_close_btn.setToolTip(
            "Скрыть панель. Чекпоинт остаётся — задача на паузе."
        )
        self._cp_continue_btn.clicked.connect(self._on_cp_continue)
        self._cp_open_map_btn.clicked.connect(self._on_cp_open_map)
        self._cp_close_btn.clicked.connect(self._on_cp_close)
        cp_btns.addWidget(self._cp_continue_btn)
        cp_btns.addWidget(self._cp_open_map_btn)
        cp_btns.addStretch()
        cp_btns.addWidget(self._cp_close_btn)
        cp_layout.addLayout(cp_btns)
        self._checkpoint_panel.setVisible(False)
        layout.addWidget(self._checkpoint_panel)

        # Панель управления
        ctrl_layout = QHBoxLayout()
        self.start_btn = QPushButton("▶ Старт")
        self.start_btn.setToolTip(
            "Запустить задачу. Link сам определит: новая или продолжение."
        )
        self.start_btn.clicked.connect(self._on_start)

        # FIX 2026-09-26: явная кнопка «Доработать» — форсирует
        # continue_project=True. Для случая, когда пользователь хочет
        # гарантированно продолжить проект (добавить/изменить/проверить).
        self.improve_btn = QPushButton("🛠 Доработать")
        self.improve_btn.setToolTip(
            "Продолжить работу над проектом явно:\n"
            "  • добавить функцию / модуль / GUI\n"
            "  • изменить поведение\n"
            "  • исправить баг\n"
            "  • проверить работу\n\n"
            "Работает только если проект уже создан (есть plan.json)."
        )
        self.improve_btn.clicked.connect(self._on_improve)
        self.pause_btn = QPushButton("Пауза")
        self.pause_btn.clicked.connect(self._on_pause)
        self.pause_btn.setEnabled(False)
        self.stop_btn = QPushButton("СТОП")
        self.stop_btn.clicked.connect(self._on_stop)
        self.stop_btn.setEnabled(False)
        self.stop_btn.setStyleSheet("font-weight: bold; color: #cc0000;")
        self.clear_btn = QPushButton("Очистить")
        self.clear_btn.clicked.connect(self._on_clear)

        ctrl_layout.addWidget(self.start_btn)
        ctrl_layout.addWidget(self.improve_btn)
        ctrl_layout.addWidget(self.pause_btn)
        ctrl_layout.addWidget(self.stop_btn)

        # AE: кнопка «Попробовать ещё раз» — после failed/stopped/partial
        self.retry_btn = QPushButton("🔄 Попробовать ещё раз")
        self.retry_btn.setToolTip(
            "Перезапустить последнюю упавшую фазу. Доступно после\n"
            "ошибки, остановки или частичного завершения задачи."
        )
        self.retry_btn.clicked.connect(self._on_retry)
        self.retry_btn.setEnabled(False)
        ctrl_layout.addWidget(self.retry_btn)
        ctrl_layout.addStretch()

        self.open_folder_btn = QPushButton("📁 Открыть папку")
        self.open_folder_btn.setToolTip(
            "Открыть папку проекта в файловом менеджере"
        )
        self.open_folder_btn.clicked.connect(self._on_open_folder)
        ctrl_layout.addWidget(self.open_folder_btn)

        self.show_browser_btn = QPushButton("👁 Показать браузер")
        self.show_browser_btn.setToolTip(
            "Вернуть окно Chromium на экран. Оно уходит за границы "
            "экрана, чтобы не мешать. Нажми, если нужно ввести "
            "капчу или посмотреть, что происходит."
        )
        self.show_browser_btn.clicked.connect(self._on_show_browser)
        ctrl_layout.addWidget(self.show_browser_btn)

        self.history_btn = QPushButton("📜 История")
        self.history_btn.setToolTip(
            "Открыть папку .ai_link — там dialog.jsonl, plan.json, state.json"
        )
        self.history_btn.clicked.connect(self._on_show_history)
        ctrl_layout.addWidget(self.history_btn)

        # AUDIT: быстрая проверка проекта без LLM
        self.audit_btn = QPushButton("🔍 Проверить проект")
        self.audit_btn.setToolTip(
            "Технический аудит проекта (мгновенно, без LLM):\n"
            "  • синтаксис Python\n"
            "  • пустые файлы\n"
            "  • TODO / FIXME\n"
            "  • битые импорты"
        )
        self.audit_btn.clicked.connect(self._on_audit)
        ctrl_layout.addWidget(self.audit_btn)

        # Шаг 3b: кнопки для работы с готовым отчётом
        self.audit_open_btn = QPushButton("🗂 Открыть отчёт")
        self.audit_open_btn.setToolTip(
            "Открыть папку с последним audit_report.md "
            "и всеми JSON-файлами аудита."
        )
        self.audit_open_btn.clicked.connect(self._on_open_audit_report)
        self.audit_open_btn.setEnabled(False)
        ctrl_layout.addWidget(self.audit_open_btn)

        self.audit_work_btn = QPushButton("📋 Работать по плану")
        self.audit_work_btn.setToolTip(
            "Запустить Link на исправление проблем аудита "
            "через обычный цикл plan → code → review → fix. "
            "Требуется успешный LLM-аудит (full/deep)."
        )
        self.audit_work_btn.clicked.connect(self._on_work_on_audit)
        self.audit_work_btn.setEnabled(False)
        ctrl_layout.addWidget(self.audit_work_btn)

        # A15: генерация тестов для проекта
        self.audit_gen_btn = QPushButton("🧪 Сгенерировать тесты")
        self.audit_gen_btn.setToolTip(
            "LLM напишет pytest-тесты для функций проекта.\n"
            "Проверяет их в изоляции. Показывает кандидатов.\n"
            "Работает через DeepSeek (роль «Код»).\n\n"
            "Требует: было полное (full/deep) аудита.\n"
            "Время: 30 сек × N функций."
        )
        self.audit_gen_btn.clicked.connect(self._on_generate_tests)
        self.audit_gen_btn.setEnabled(False)
        ctrl_layout.addWidget(self.audit_gen_btn)

        # Baseline: сохранить эталон / сравнить
        self.audit_baseline_btn = QPushButton("📌 Baseline")
        self.audit_baseline_btn.setToolTip(
            "Сохранить текущий аудит как эталон (baseline).\n"
            "Следующий аудит будет автоматически сравниваться\n"
            "с ним: что улучшилось, что ухудшилось."
        )
        self.audit_baseline_btn.clicked.connect(
            self._on_set_baseline)
        self.audit_baseline_btn.setEnabled(False)
        ctrl_layout.addWidget(self.audit_baseline_btn)

        self.audit_compare_btn = QPushButton("📊 Сравнить")
        self.audit_compare_btn.setToolTip(
            "Показать разницу между последним аудитом\n"
            "и baseline (если он сохранён)."
        )
        self.audit_compare_btn.clicked.connect(
            self._on_compare_baseline)
        self.audit_compare_btn.setEnabled(False)
        ctrl_layout.addWidget(self.audit_compare_btn)

        ctrl_layout.addWidget(self.clear_btn)
        # B4 (2026-10-07): управление перенесено в «Проект»/КП.
        # ctrl_layout НЕ добавляем в layout — кнопки скрыты, но
        # созданы и доступны для прокси-вызовов из main_window.
        # layout.addLayout(ctrl_layout)

        # Поле ввода
        input_layout = QHBoxLayout()
        self.input_field = QTextEdit()
        self.input_field.setMaximumHeight(80)
        self.input_field.setPlaceholderText(
            "Задача → «Старт». "
            "Правило → «запомни глобально: ...» → «Отправить»."
        )
        input_layout.addWidget(self.input_field, stretch=1)

        self.send_btn = QPushButton("Отправить")
        self.send_btn.clicked.connect(self._on_send_message)
        self.send_btn.setToolTip(
            "Три функции:\n"
            "\n"
            "1. Записать правило:\n"
            "   • «запомни глобально: ...» — для всех проектов\n"
            "   • «запомни для проекта: ...» — только этот проект\n"
            "   • «сохрани как правило: ...» — с уточнением области\n"
            "\n"
            "2. Обсудить концепцию проекта\n"
            "   Модель видит план, память, историю диалога.\n"
            "   Просто пиши, что хочешь обсудить.\n"
            "\n"
            "3. Сохранить концепцию в файл\n"
            "   Напиши: «запомни концепцию»\n"
            "   Link соберёт решения из диалога и создаст\n"
            "   файл concept.md через выбранного агента\n"
            "   (Настройки → Назначения → «Концепция»).\n"
            "\n"
            "Ответ на уточнение — через кнопку «Ответить» (слева).\n"
            "Простой чат — через «💬 Поболтать» (в статус-баре)."
        )
        input_layout.addWidget(self.send_btn)

        # B5 (2026-10-07): порядок — сначала ввод + Отправить,
        # потом большое окно статуса (output).
        layout.addLayout(input_layout)

        # Область вывода — БОЛЬШОЕ окно статуса ПОД вводом.
        layout.addWidget(self.output, stretch=1)

        # Первое системное сообщение
        self._append_system("Добро пожаловать. Выберите проект и напишите задачу.")

    # === Работа с сообщениями ===

    def _append_message_html(self, role: str, text: str,
                             kind: str = "text", ts: str = ""):
        """Добавляет сообщение в окно вывода."""
        if not ts:
            ts = datetime.now().strftime("%H:%M:%S")

        if role == ROLE_USER:
            prefix = "Вы"
            color = COLOR_USER
        elif role == ROLE_LINK:
            prefix = "Link"
            color = COLOR_LINK
        else:
            prefix = "•"
            color = COLOR_SYSTEM

        # Экранируем HTML
        safe = (text.replace("&", "&amp;")
                    .replace("<", "&lt;")
                    .replace(">", "&gt;")
                    .replace("\n", "<br>"))

        html = (
            f'<div style="margin: 6px 0;">'
            f'<span style="color:#999; font-size:9pt;">[{ts}]</span> '
            f'<span style="color:{color}; font-weight:bold;">{prefix}:</span><br>'
            f'<span style="color:#222;">{safe}</span>'
            f'</div>'
        )
        self.output.moveCursor(QTextCursor.End)
        self.output.insertHtml(html)
        self.output.insertPlainText("\n")
        self.output.moveCursor(QTextCursor.End)

    def _append_system(self, text: str):
        """Системное сообщение — в UI и в файл."""
        if self._project_dir:
            msg = add_message(self._session, ROLE_SYSTEM, text, kind="system")
            save_session(self._project_dir, self._session)
            self._append_message_html(ROLE_SYSTEM, text, "system", msg.ts)
        else:
            self._append_message_html(ROLE_SYSTEM, text, "system")

    def _append_link(self, text: str, kind: str = "text"):
        if self._project_dir:
            msg = add_message(self._session, ROLE_LINK, text, kind=kind)
            save_session(self._project_dir, self._session)
            self._append_message_html(ROLE_LINK, text, kind, msg.ts)
        else:
            self._append_message_html(ROLE_LINK, text, kind)

    def _append_user(self, text: str):
        if self._project_dir:
            msg = add_message(self._session, ROLE_USER, text)
            save_session(self._project_dir, self._session)
            self._append_message_html(ROLE_USER, text, "text", msg.ts)
        else:
            self._append_message_html(ROLE_USER, text)

    # === Обработчики кнопок ===

    def _on_improve(self):
        """FIX 2026-09-26: «Доработать» — форсирует continue_project.

        Логика та же, что _on_start, но:
          • Не вызываем classify_task — сразу continue_project=True
          • Проверяем, что plan.json существует
        """
        if not self._project_dir:
            QMessageBox.warning(
                self, "Ошибка",
                "Сначала выберите проект (вкладка Проект)."
            )
            return
        task = self.input_field.toPlainText().strip()
        if not task:
            QMessageBox.warning(
                self, "Ошибка",
                "Опишите, что доработать:\n"
                "  «Добавь графики»\n"
                "  «Исправь баг в stats»\n"
                "  «Проверь работу export»"
            )
            return

        from pathlib import Path as _P
        plan_path = _P(self._project_dir) / ".ai_link" / "plan.json"
        if not plan_path.exists():
            QMessageBox.information(
                self, "Доработка",
                "В этом проекте ещё нет плана.\n"
                "Сначала создайте проект через «Старт»."
            )
            return

        if self._task and self._task.is_alive():
            QMessageBox.warning(
                self, "Ошибка", "Задача уже выполняется.")
            return

        self._append_user(task)
        self.input_field.clear()
        self._pending_answers = []
        self._pending_task_text = task
        self._last_task_text = task
        self._last_failed = False

        self._append_link(
            "🛠 Дорабатываю проект...", kind="start")

        self._task = BackgroundTask(
            task=task,
            project_dir=self._project_dir,
            on_progress=self._on_progress,
            on_finish=self._on_finish,
            continue_project=True,
            check_checkpoint=True,
        )
        self._task.start()
        self._refresh_status()
        self._update_buttons()

    def _on_continue_plan(self):
        """CONTINUE-PLAN (2026-10-09): запустить по принятому плану.

        Не показывает диалог ввода. Берёт task =
        «Выполни принятый план доработки.» — чтобы STALE-STATE
        увидел совпадение и не сбросил plan.json.
        """
        if not self._project_dir:
            QMessageBox.warning(
                self, "Ошибка",
                "Сначала выберите проект (вкладка Проект)."
            )
            return

        from pathlib import Path as _P
        plan_path = _P(self._project_dir) / ".ai_link" / "plan.json"
        if not plan_path.exists():
            QMessageBox.information(
                self, "По плану",
                "Нет plan.json. Сначала примите план "
                "(«📋 План» → «✓ Принять»)."
            )
            return

        try:
            import json as _json
            _pd = _json.loads(plan_path.read_text(encoding="utf-8"))
            if _pd.get("status") == "done":
                QMessageBox.information(
                    self, "По плану",
                    "План уже выполнен (status=done)."
                )
                return
        except Exception as _e:
            QMessageBox.warning(
                self, "Ошибка", f"plan.json не читается: {_e}"
            )
            return

        if self._task and self._task.is_alive():
            QMessageBox.warning(
                self, "Ошибка", "Задача уже выполняется.")
            return

        task = "Выполни принятый план доработки."

        self._append_user(task)
        self.input_field.clear()
        self._pending_answers = []
        self._pending_task_text = task
        self._last_task_text = task
        self._last_failed = False

        self._append_link(
            "📋 Запускаю по принятому плану...", kind="start")

        self._task = BackgroundTask(
            task=task,
            project_dir=self._project_dir,
            on_progress=self._on_progress,
            on_finish=self._on_finish,
            continue_project=True,
            check_checkpoint=True,
        )
        self._task.start()
        self._refresh_status()
        self._update_buttons()

    def _on_start(self):
        """Запуск задачи в фоне.

        Сначала проверяет: это команда правила или настоящая задача?
        """
        if not self._project_dir:
            QMessageBox.warning(self, "Ошибка",
                                "Сначала выберите проект (вкладка Проект).")
            return

        task = self.input_field.toPlainText().strip()
        if not task:
            QMessageBox.warning(self, "Ошибка", "Введите задачу.")
            return

        # Если ждём ответа на уточнение — это ответ
        if self._pending_question:
            self._handle_clarification_answer(task)
            return

        # Если это команда правила — сохраняем, задачу не запускаем
        try:
            from core.rules_parser import parse_rule_command
            rule = parse_rule_command(task)
            if rule.get("is_rule"):
                self._handle_rule_command(task, rule)
                return
        except Exception:
            pass

        # A2b (2026-10-07): «Новый проект» = ВСЕГДА новый.
        #   • Доработка → отдельная кнопка «🛠 Доработать».
        #   • Поболтать → «💬 Поболтать» в статус-баре.
        # Не гадаем через classify_task — явное действие.
        continue_project = False
        force_new = True

        if self._task and self._task.is_alive():
            QMessageBox.warning(self, "Ошибка", "Задача уже выполняется.")
            return

        # Записываем задачу как сообщение пользователя
        self._append_user(task)
        self.input_field.clear()

        # Сбрасываем предыдущие ответы — это новая задача
        self._pending_answers = []
        self._pending_task_text = task
        # AE: запоминаем задачу для retry
        self._last_task_text = task
        self._last_failed = False

        # Создаём и запускаем фоновую задачу
        self._task = BackgroundTask(
            task=task,
            project_dir=self._project_dir,
            on_progress=self._on_progress,
            on_finish=self._on_finish,
            continue_project=False,
            force_new=True,
            # LONG-RUN: если есть незавершённый чекпоинт —
            # Link сам спросит «Продолжить / Заново».
            check_checkpoint=True,
        )
        self._task.start()

        self._append_link("Запускаю новый проект...", kind="start")
        self._refresh_status()
        self._update_buttons()

    def _handle_dialog_task(self, task: str):
        """AM: отправляет сообщение в модель с контекстом проекта.

        Режим обсуждения концепции (если проект выбран).
        """
        if self._dialog_worker and self._dialog_worker.isRunning():
            QMessageBox.information(
                self, "Подождите",
                "Предыдущий вопрос ещё обрабатывается."
            )
            return
        self._append_user(task)
        self.input_field.clear()
        self._append_link("Думаю над ответом...", kind="text")

        # AM: берём агента для диалога из назначений.
        # Если «Авто» — fallback: browser_deepseek.
        _dialog_agent = ""
        try:
            from core.orchestrator import get_phase_agent, PHASE_DIALOG
            _dialog_agent = get_phase_agent(PHASE_DIALOG) or ""
        except Exception:
            _dialog_agent = ""
        if not _dialog_agent:
            _dialog_agent = "browser_deepseek"

        self._dialog_worker = _DialogWorker(
            task, site="deepseek",
            project_dir=self._project_dir or "",
            agent=_dialog_agent,
        )
        self._dialog_worker.got_answer.connect(self._sig_dialog_answer.emit)
        self._dialog_worker.got_error.connect(self._sig_dialog_error.emit)
        self._dialog_worker.start()

    def _do_dialog_answer(self, text: str):
        """Слот главного потока — показывает ответ ИИ."""
        self._append_link(text, kind="text")

    def _do_dialog_error(self, err: str):
        """Слот главного потока — показывает ошибку."""
        self._append_link(f"❌ Не удалось получить ответ: {err}",
                          kind="warning")

    def _on_retry(self):
        """AE: перезапустить последнюю упавшую фазу."""
        if not self._project_dir:
            QMessageBox.warning(
                self, "Ошибка",
                "Проект не выбран."
            )
            return
        if not self._last_task_text:
            QMessageBox.warning(
                self, "Ошибка",
                "Нет задачи для перезапуска."
            )
            return

        # AG: кастомный диалог с описаниями фаз.
        last = self._last_phase or "review"
        dlg = _RetryPhaseDialog(
            task_text=self._last_task_text,
            recommended=last,
            parent=self,
        )
        if dlg.exec() != QDialog.Accepted:
            return
        choice = dlg.get_choice()
        if not choice:
            return

        self._append_link(
            f"🔄 Повтор с фазы «{choice}»...",
            kind="start",
        )

        # Сбрасываем флаг и запускаем
        self._last_failed = False
        # AI fix: сохранить задачу для возможного уточнения
        self._pending_task_text = self._last_task_text
        self._task = BackgroundTask(
            task=self._last_task_text,
            project_dir=self._project_dir,
            on_progress=self._on_progress,
            on_finish=self._on_finish,
            continue_project=True,
            resume_from=choice,
        )
        self._task.start()
        self._refresh_status()
        self._update_buttons()

    def _on_pause(self):
        if self._task and self._task.is_alive():
            s = self._task.status().status
            if s == ST_RUNNING:
                self._task.pause()
                self._append_link("Задача приостановлена.", kind="warning")
            elif s == ST_PAUSED:
                self._task.resume()
                self._append_link("Задача продолжена.", kind="text")
            self._refresh_status()
            self._update_buttons()

    def _on_stop(self):
        if self._task and self._task.is_alive():
            self._task.stop()
            self._append_link("Отправлена команда остановки.", kind="stop")
            self._refresh_status()

    def _on_open_folder(self):
        """Открывает папку проекта в файловом менеджере."""
        if not self._project_dir:
            QMessageBox.warning(self, "Ошибка",
                                "Проект не выбран (вкладка Проект).")
            return
        try:
            subprocess.Popen(["xdg-open", self._project_dir])
        except Exception as e:
            QMessageBox.warning(self, "Ошибка",
                                f"Не удалось открыть папку:\n{e}")

    def _on_show_browser(self):
        """Возвращает окно Chromium на экран."""
        try:
            from core.agents.browser_manager import restore_browser_window
            restore_browser_window()
            self._append_link(
                "👁 Окно браузера возвращено на экран.",
                kind="text"
            )
        except Exception as e:
            self._append_link(
                f"❌ Не удалось показать браузер: {e}",
                kind="warning"
            )

    def _sync_project_folder(self, new_path: str) -> None:
        """ARCHIVE-EXTRACT (2026-10-08): синхронизирует папку проекта
        с главным окном. Меняет folder_edit в ProjectTab, что
        триггерит _on_project_folder_changed → обновит кнопки,
        дерево, сцену.

        Вызывается после распаковки архива или смены проекта
        из _on_audit.
        """
        try:
            main = self.window()
            if main is None:
                return
            if hasattr(main, "project_tab"):
                main.project_tab.folder_edit.setText(new_path)
        except Exception:
            pass

    def _is_foreign_project(self, path: str) -> bool:
        """PROJECT-COPY (2026-10-08): True, если папка — чужая.

        Чужая = нет .ai_link/IDENTITY (нашей копии) И нет
        plan.json с нашим created_by_user.
        """
        import json as _json
        from pathlib import Path as _P

        if not path:
            return False
        root = _P(path)
        if not root.exists() or not root.is_dir():
            return False

        # 1. Наша копия?
        if (root / ".ai_link" / "IDENTITY").exists():
            return False

        # 2. План с нашим user?
        plan = root / ".ai_link" / "plan.json"
        if plan.exists():
            try:
                data = _json.loads(plan.read_text(encoding="utf-8"))
                owner = (data.get("created_by_user") or "").strip()
            except Exception:
                owner = ""
            # Пусто = старый свой (до A3).
            if not owner:
                return False
            try:
                from config.config_manager import get_setting
                import getpass
                current = (
                    (get_setting("user_name", "") or "").strip()
                    or getpass.getuser()
                )
            except Exception:
                current = ""
            if owner == current:
                return False
        return True

    def _on_audit(self):
        """AUDIT: полный аудит проекта (fast/full/deep)."""
        if not self._project_dir:
            QMessageBox.warning(
                self, "Ошибка",
                "Сначала выберите проект (вкладка «Проект»)."
            )
            return

        # ARCHIVE-EXTRACT (2026-10-08): если в папке есть архивы —
        # предложить распаковать перед аудитом.
        try:
            from core.archive_extract import (
                find_archives_in, extract_archive,
            )
            _archives = find_archives_in(self._project_dir)
        except Exception as _e:
            _archives = []

        if _archives:
            _names = "\n".join(
                f"  • {Path(a).name}" for a in _archives[:5]
            )
            _ans = QMessageBox.question(
                self, "Найдены архивы",
                f"В папке найдены архивы:\n{_names}\n\n"
                f"Распаковать перед аудитом?\n"
                f"Каждый → в <имя>_extracted/ рядом с архивом.",
                QMessageBox.Yes | QMessageBox.No,
            )
            if _ans == QMessageBox.Yes:
                for _a in _archives:
                    self._append_link(
                        f"📦 Распаковываю: {Path(_a).name}",
                        kind="text",
                    )
                    try:
                        _r = extract_archive(_a)
                    except Exception as _e:
                        self._append_link(
                            f"  ❌ Ошибка: {_e}", kind="warning"
                        )
                        continue
                    if not _r.get("ok"):
                        self._append_link(
                            f"  ❌ {_r.get('error', '?')}",
                            kind="warning",
                        )
                        continue
                    self._append_link(
                        f"  ✅ Распаковано → "
                        f"{_r['extracted_to']} "
                        f"(файлов: {_r.get('extracted', '?')})",
                        kind="text",
                    )
                    # Сразу подменить проект на распакованное.
                    self.set_project_dir(_r["extracted_to"])
                    # ARCHIVE-EXTRACT (2026-10-08): синхронизируем
                    # с главным окном — чтобы кнопки Проекта
                    # пересчитались (появятся «🗂 Открыть отчёт» и др.).
                    self._sync_project_folder(_r["extracted_to"])
                    try:
                        from PySide6.QtWidgets import QApplication
                        QApplication.processEvents()
                    except Exception:
                        pass

        # PROJECT-COPY (2026-10-08): если чужой проект → предложить
        # создать копию C (точка отката). Аудит идёт по оригиналу.
        if self._is_foreign_project(self._project_dir):
            _ans = QMessageBox.question(
                self, "Чужой проект",
                "Это чужой проект (не создан вами).\n\n"
                "Создать рабочую копию для отката?\n"
                "Оригинал НЕ изменяется, копия — рядом с ним.\n\n"
                "Копия: <имя>_source_YYYYMMDD_HHMMSS/",
                QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel,
            )
            if _ans == QMessageBox.Cancel:
                return
            if _ans == QMessageBox.Yes:
                self._append_link(
                    "📁 Создаю копию проекта...", kind="text"
                )
                try:
                    from core.project_copy import copy_project_for_audit
                    from config.config_manager import get_setting
                    _user = (get_setting("user_name", "") or "").strip()
                    _r = copy_project_for_audit(
                        self._project_dir, user_name=_user
                    )
                except Exception as _e:
                    self._append_link(
                        f"❌ Ошибка копирования: {_e}", kind="warning"
                    )
                    return
                if not _r.get("ok"):
                    self._append_link(
                        f"❌ Копия не создана: {_r.get('error', '?')}",
                        kind="warning",
                    )
                    return
                self._append_link(
                    f"✅ Копия создана: {_r['copy_path']}\n"
                    f"   файлов: {_r['copied']}, "
                    f"пропущено: {_r['skipped']}",
                    kind="text",
                )

        # Диалог выбора режима
        dlg = _AuditModeDialog(self)
        if dlg.exec() != QDialog.Accepted:
            return
        opts = dlg.get_result()
        mode = opts["mode"]
        max_time = opts["max_time_min"]

        self._append_link(
            f"🔍 Аудит ({mode}, до {max_time} мин)...",
            kind="start",
        )

        # AUDIT-WORKER (2026-10-08): запуск аудита в фоне.
        _hint = ""
        try:
            _hint = self.input_field.toPlainText().strip()
        except Exception:
            _hint = ""
        self._audit_worker = _AuditWorker(
            project_dir=self._project_dir,
            mode=mode,
            max_time_min=max_time,
            user_hint=_hint,
        )
        self._audit_worker.progress.connect(self._on_audit_progress)
        self._audit_worker.got_result.connect(self._on_audit_finished)
        self._audit_worker.got_error.connect(self._on_audit_error)
        self._audit_worker.start()

    def _on_audit_progress(self, line: str):
        """AUDIT-WORKER: строка прогресса → в чат."""
        self._append_link(line, kind="text")

    def _on_audit_error(self, msg: str):
        """AUDIT-WORKER: ошибка аудита."""
        self._append_link(
            f"❌ Аудит не удался: {msg}", kind="warning"
        )

    def _on_audit_finished(self, data: dict):
        """AUDIT-WORKER: результат аудита из воркера.

        data = {"audit": result_dict, "plan": plan_dict_or_None}
        """
        result = data.get("audit") or {}
        plan   = data.get("plan")

        if result.get("status") == "failed":
            self._append_link(
                f"❌ Ошибка: {result.get('error', '?')}",
                kind="warning",
            )
            return

        # Сводка
        tech = result.get("technical", {})
        sec = result.get("security", {})
        tests = result.get("tests", {})
        m = result.get("manifest", {})

        msg = (
            f"✅ Аудит завершён ({result.get('status')})\n"
            f"  audit_id: {result.get('audit_id')}\n"
            f"  файлов: {m.get('file_count', '?')}\n"
            f"  технический: {tech.get('total', 0)} проблем\n"
            f"  секреты: {sec.get('total', 0)} находок\n"
            f"  тесты: {tests.get('status', '?')} "
            f"(passed={tests.get('passed', 0)}, "
            f"failed={tests.get('failed', 0)})"
        )
        self._append_link(msg, kind="text")

        # Race warning
        if result.get("race_warning"):
            self._append_link(
                f"⚠️ {result['race_warning']}", kind="warning"
            )

        # Первые 5 проблем
        issues = tech.get("issues", [])[:5]
        for i, iss in enumerate(issues, 1):
            sev = iss.get("severity", "?")
            path = iss.get("file", "?")
            text = (iss.get("message") or "")[:100]
            self._append_link(
                f"  {i}. [{sev}] {path}: {text}",
                kind="text",
            )

        # Шаг 3b: сохраняем audit_id, включаем кнопки
        self._last_audit_id = result.get("audit_id", "")
        try:
            self.audit_open_btn.setEnabled(bool(self._last_audit_id))
        except Exception:
            pass
        # ARCHIVE-EXTRACT (2026-10-08): обновляем кнопки в Проекте
        # (btn_open_report активируется, если есть .ai_link/audits/).
        try:
            main = self.window()
            if main and hasattr(main, "project_tab"):
                main.project_tab._update_buttons_by_folder()
        except Exception:
            pass
        # «Работать по плану» — только если был LLM-аудит (full/deep)
        mode_used = result.get("mode", "fast")
        has_llm_plan = bool(
            result.get("llm_classification", {}).get("classified")
            or result.get("llm_ideas", {}).get("success")
        )
        try:
            self.audit_work_btn.setEnabled(
                mode_used in ("full", "deep") and has_llm_plan)
        except Exception:
            pass

        if self._last_audit_id:
            self._append_link(
                f"💡 Полный отчёт: "
                f".ai_link/audits/{self._last_audit_id}/"
                f"audit_report.md",
                kind="text",
            )
            self._append_link(
                "💡 Кнопки «🗂 Открыть отчёт» и "
                "«📋 Работать по плану» — под полем ввода.",
                kind="text",
            )

        # AUDIT-WORKER: план пришёл из воркера.
        if plan is not None:
            if plan.get("ok"):
                _pl = plan.get("plan") or {}
                _stages = _pl.get("stages") or []
                _items = sum(len(s.get("items") or [])
                             for s in _stages)
                self._append_link(
                    f"✅ План готов: {len(_stages)} этапов, "
                    f"{_items} шагов.\n"
                    f"   Просмотр — кнопка «📋 План» в Проекте.",
                    kind="text",
                )
                try:
                    from core.event_log import log_event
                    log_event(event_type="audit_plan_ready", payload={
                        "audit_id": self._last_audit_id,
                        "stages": len(_stages),
                        "items": _items,
                    })
                except Exception:
                    pass
            else:
                self._append_link(
                    f"⚠️ Не удалось построить план: "
                    f"{plan.get('error', '?')}",
                    kind="warning",
                )

        # Обновляем кнопки Проекта (могла появиться «📋 План»).
        try:
            main = self.window()
            if main and hasattr(main, "project_tab"):
                main.project_tab._update_buttons_by_folder()
        except Exception:
            pass

    def _on_generate_tests(self):
        """A15: сгенерировать тесты через LLM (в фоне)."""
        if not self._project_dir or not self._last_audit_id:
            QMessageBox.information(
                self, "Генерация тестов",
                "Сначала сделайте полный (full) или глубокий "
                "(deep) аудит."
            )
            return
        from PySide6.QtWidgets import QInputDialog
        n, ok = QInputDialog.getInt(
            self, "Генерация тестов",
            "Сколько функций взять?\n(1 функция ≈ 30 сек)",
            5, 1, 20, 1,
        )
        if not ok:
            return
        self.audit_gen_btn.setEnabled(False)
        self.audit_gen_btn.setText("🧪 Генерирую…")
        self._append_link(
            f"🧪 Генерирую тесты (до {n} функций через DeepSeek)...",
            kind="start",
        )
        self._testgen_worker = _TestGenWorker(
            self._project_dir,
            max_functions=n,
            agent_mode="browser_deepseek",
        )
        self._testgen_worker.got_result.connect(
            self._on_testgen_done)
        self._testgen_worker.got_error.connect(
            self._on_testgen_error)
        self._testgen_worker.start()

    def _on_testgen_done(self, result: dict):
        """A15: показать кандидатов, спросить применить."""
        try:
            self.audit_gen_btn.setEnabled(True)
            self.audit_gen_btn.setText("🧪 Сгенерировать тесты")
        except Exception:
            pass
        cons = result.get("total_considered", 0)
        cand = result.get("total_candidates", 0)
        rej = result.get("total_rejected", 0)
        self._append_link(
            f"✅ Готово: рассмотрено {cons}, "
            f"прошли {cand}, отброшено {rej}.",
            kind="text",
        )
        for c in result.get("candidates", [])[:10]:
            fn = c.get("func", {})
            self._append_link(
                f"  ✔ {fn.get('name')} → {c.get('file_name')}",
                kind="text",
            )
        if not cand:
            self._append_link(
                "⚠️ Ни один не прошёл валидацию.", kind="warning")
            return
        reply = QMessageBox.question(
            self, "Применить тесты?",
            f"Прошло: {cand}. Записать в <проект>/tests/?",
            QMessageBox.Yes | QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        try:
            from core.audit_project import apply_generated_tests
            ap = apply_generated_tests(
                self._project_dir, result.get("candidates", []))
            for f in ap.get("applied", []):
                self._append_link(f"  + {f}", kind="text")
            for f in ap.get("failed", []):
                self._append_link(
                    f"  ✗ {f.get('file')}: {f.get('error')}",
                    kind="warning",
                )
        except Exception as e:
            QMessageBox.warning(
                self, "Ошибка", f"Не записать тесты:\n{e}")

    def _on_testgen_error(self, err: str):
        try:
            self.audit_gen_btn.setEnabled(True)
            self.audit_gen_btn.setText("🧪 Сгенерировать тесты")
        except Exception:
            pass
        self._append_link(
            f"❌ Ошибка: {err}", kind="warning")

    def _on_set_baseline(self):
        """Baseline: сохранить текущий аудит как эталон."""
        if not self._project_dir or not self._last_audit_id:
            QMessageBox.information(
                self, "Baseline",
                "Сначала сделайте аудит."
            )
            return
        reply = QMessageBox.question(
            self, "Сохранить baseline?",
            f"Сохранить аудит {self._last_audit_id} как эталон?\n\n"
            f"Следующие аудиты будут сравниваться с ним.",
            QMessageBox.Yes | QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        try:
            from core.audit_project import set_baseline
            r = set_baseline(self._project_dir, self._last_audit_id)
            if r.get("ok"):
                self._append_link(
                    f"📌 Baseline сохранён: {self._last_audit_id}",
                    kind="text",
                )
                self.audit_compare_btn.setEnabled(True)
            else:
                self._append_link(
                    f"❌ Не удалось: {r.get('error')}",
                    kind="warning",
                )
        except Exception as e:
            QMessageBox.warning(self, "Ошибка", f"Baseline: {e}")

    def _on_compare_baseline(self):
        """Baseline: показать разницу с эталоном."""
        if not self._project_dir:
            QMessageBox.information(self, "Сравнение",
                                    "Сначала выберите проект.")
            return
        try:
            from core.audit_project import (
                get_baseline, load_latest_audit,
                compare_with_baseline,
            )
            base = get_baseline(self._project_dir)
            if not base:
                self._append_link(
                    "📊 Baseline не сохранён. Нажмите «📌 Baseline».",
                    kind="warning",
                )
                return
            cur = load_latest_audit(self._project_dir)
            if not cur:
                self._append_link(
                    "📊 Нет свежего аудита.", kind="warning")
                return
            cmp = compare_with_baseline(self._project_dir, cur)
            self._append_link(
                f"📊 Baseline `{cmp.get('baseline_audit_id','?')}` "
                f"vs текущий `{cmp.get('current_audit_id','?')}`",
                kind="start",
            )
            for key, info in cmp.get("delta", {}).items():
                ch = info["change"]
                if ch == 0:
                    mark = "—"
                elif (key == "tests_passed" and ch > 0) or \
                     (key != "tests_passed" and ch < 0):
                    mark = f"✅ {ch:+d}"
                else:
                    mark = f"⚠️ {ch:+d}"
                self._append_link(
                    f"  {info['label']}: "
                    f"{info['before']} → {info['after']} ({mark})",
                    kind="text",
                )
            if cmp.get("improved"):
                self._append_link(
                    f"✅ Улучшилось: {len(cmp['improved'])} метрик",
                    kind="text",
                )
            if cmp.get("worsened"):
                self._append_link(
                    f"⚠️ Ухудшилось: {len(cmp['worsened'])} метрик",
                    kind="warning",
                )
        except Exception as e:
            QMessageBox.warning(self, "Ошибка", f"Сравнение: {e}")

    def _find_latest_audit_id(self) -> str:
        """FIX-AUDIT-LATEST (2026-10-08): находит последний audit_id
        в .ai_link/audits/. Имена в формате YYYYMMDD_HHMMSS —
        лексикографическая сортировка = сортировка по дате.
        """
        from pathlib import Path as _P
        if not self._project_dir:
            return ""
        audits_dir = _P(self._project_dir) / ".ai_link" / "audits"
        if not audits_dir.exists() or not audits_dir.is_dir():
            return ""
        try:
            dirs = [
                d.name for d in audits_dir.iterdir()
                if d.is_dir()
            ]
            if not dirs:
                return ""
            return sorted(dirs, reverse=True)[0]
        except Exception:
            return ""

    def _on_open_audit_report(self):
        """Шаг 3b: открыть папку ПОСЛЕДНЕГО аудита.

        FIX-AUDIT-LATEST (2026-10-08): если _last_audit_id пуст
        (GUI перезапущен, аудит был в прошлой сессии) — берём
        последний с диска по имени.
        """
        if not self._project_dir:
            QMessageBox.information(
                self, "Открыть отчёт",
                "Сначала выберите проект."
            )
            return
        # Если не знаем id — ищем последний.
        audit_id = self._last_audit_id or self._find_latest_audit_id()
        if not audit_id:
            QMessageBox.information(
                self, "Открыть отчёт",
                "Аудитов пока нет. Нажмите «🔍 Проверить проект»."
            )
            return
        # Запоминаем, чтобы не искать в следующий раз.
        self._last_audit_id = audit_id
        from pathlib import Path as _P
        audit_dir = (_P(self._project_dir) / ".ai_link"
                     / "audits" / audit_id)
        if not audit_dir.exists():
            QMessageBox.warning(
                self, "Открыть отчёт",
                f"Папка не найдена:\n{audit_dir}"
            )
            return
        try:
            subprocess.Popen(["xdg-open", str(audit_dir)])
        except Exception as e:
            QMessageBox.warning(
                self, "Ошибка",
                f"Не удалось открыть папку:\n{e}"
            )

    def _on_open_audit_plan(self):
        """AUDIT-PLANNER (4.2): открыть диалог просмотра плана."""
        if not self._project_dir:
            QMessageBox.information(
                self, "План",
                "Сначала выберите проект (вкладка Проект)."
            )
            return
        audit_id = self._last_audit_id or self._find_latest_audit_id()
        try:
            from ui.dialogs.audit_plan_dialog import AuditPlanDialog
            dlg = AuditPlanDialog(
                self._project_dir, audit_id=audit_id, parent=self,
            )
            dlg.exec()
        except Exception as e:
            QMessageBox.warning(
                self, "Ошибка",
                f"Не удалось открыть план: {e}"
            )
            return
        # После диалога обновляем кнопки Проекта (мог быть «Принять»).
        try:
            main = self.window()
            if main and hasattr(main, "project_tab"):
                main.project_tab._update_buttons_by_folder()
        except Exception:
            pass

    def _on_work_on_audit(self):
        """Шаг 3b: запустить Link на исправление проблем аудита."""
        if not self._project_dir:
            QMessageBox.information(
                self, "Работа по плану",
                "Сначала выберите проект."
            )
            return
        audit_id = self._last_audit_id or self._find_latest_audit_id()
        if not audit_id:
            QMessageBox.information(
                self, "Работа по плану",
                "Сначала сделайте полный аудит (full или deep)."
            )
            return
        self._last_audit_id = audit_id
        from pathlib import Path as _P
        audit_dir = (_P(self._project_dir) / ".ai_link"
                     / "audits" / audit_id)
        plan_path = audit_dir / "audit_plan.json"
        report_path = audit_dir / "audit_report.md"
        task_text = ""
        if plan_path.exists():
            try:
                import json as _json
                plan_data = _json.loads(
                    plan_path.read_text(encoding="utf-8"))
                task_text = plan_data.get("summary", "")
                steps = plan_data.get("steps", [])
                if steps:
                    task_text += "\n\nШаги:\n" + "\n".join(
                        f"  {i}. {s}"
                        for i, s in enumerate(steps, 1)
                    )
            except Exception as e:
                task_text = f"Исправить проблемы аудита ({e})"
        elif report_path.exists():
            task_text = (
                "Исправь проблемы, найденные в аудите. "
                f"Отчёт: {report_path}"
            )
        else:
            QMessageBox.warning(
                self, "Работа по плану",
                "Не найден ни audit_plan.json, "
                "ни audit_report.md."
            )
            return
        try:
            self.input_field.setPlainText(
                f"Исправь проблемы аудита "
                f"{self._last_audit_id}:\n\n{task_text}"
            )
            self._append_link(
                "📋 Текст задачи вставлен в поле ввода. "
                "Проверьте и нажмите «Старт».",
                kind="text",
            )
        except Exception as e:
            QMessageBox.warning(
                self, "Ошибка",
                f"Не удалось вставить текст:\n{e}"
            )

    def _on_show_history(self):
        """Открывает папку .ai_link с файлами истории проекта."""
        if not self._project_dir:
            QMessageBox.warning(self, "Ошибка",
                                "Проект не выбран (вкладка Проект).")
            return
        from pathlib import Path as _P
        ai_link_dir = _P(self._project_dir) / ".ai_link"
        if not ai_link_dir.exists():
            QMessageBox.information(
                self, "История",
                f"В проекте ещё нет папки .ai_link.\n"
                f"Она появится после первой задачи.\n\n"
                f"Ожидаемый путь: {ai_link_dir}"
            )
            return
        try:
            subprocess.Popen(["xdg-open", str(ai_link_dir)])
        except Exception as e:
            QMessageBox.warning(self, "Ошибка",
                                f"Не удалось открыть папку:\n{e}")

    def _on_clear(self):
        """Просто очистить визуальное окно (файл не трогаем)."""
        reply = QMessageBox.question(
            self, "Очистить",
            "Очистить окно диалога? (история в файле останется)",
            QMessageBox.Yes | QMessageBox.No,
        )
        if reply == QMessageBox.Yes:
            self.output.clear()

    def _on_send_message(self, text: str = ""):
        """Отправить сообщение в диалог.

        UI-VISION (2026-10-10): text может прийти извне (из КП).
        Если не передан — берём из self.input_field.

        AL: кнопка «Отправить» теперь делает ДВЕ вещи:
          1. Правило — если текст распознан как «запомни ...»
          2. Обсуждение концепции проекта — временно заглушка
             (полноценный режим — задача AM)

        УБРАНО (дубли):
          • Ответ на уточнение (только через «Старт» → «Ответить»)
          • Простой чат с DeepSeek (только через «💬 Поболтать»)
        """
        if not text:
            text = self.input_field.toPlainText().strip()
        if not text:
            return

        # ─── 1. Правило (AN расширен) ───
        try:
            from core.rules_parser import parse_rule_command
            rule = parse_rule_command(text)
        except Exception:
            rule = {"is_rule": False}

        if rule.get("is_rule"):
            self._handle_rule_command(text, rule)
            return

        # ─── 2. «Запомни концепцию» / «сохраняй» / «готовь проект» ───
        # AO: формируем concept.md по диалогу.
        _concept_triggers = (
            "запомни концепцию", "сохрани концепцию",
            "запиши концепцию", "запомни требования",
            "сохрани концепт", "сохраняй концепцию",
            "готовь проект", "запомни концепт",
        )
        low = text.lower()
        if self._project_dir and any(t in low for t in _concept_triggers):
            self._save_concept(text)
            return

        # ─── 3. Обсуждение концепции проекта (AM) ───
        # Если проект выбран — модель видит контекст.
        # Если нет — простой чат.
        self._handle_dialog_task(text)

    def _save_concept(self, user_text: str):
        """AO: формирует concept.md через DeepSeek по диалогу."""
        if self._dialog_worker and self._dialog_worker.isRunning():
            QMessageBox.information(
                self, "Подождите",
                "Предыдущая операция ещё выполняется."
            )
            return
        self._append_user(user_text)
        self.input_field.clear()
        self._append_link(
            "📝 Формирую концепцию проекта...",
            kind="start",
        )
        self._dialog_worker = _DialogWorker(
            task=user_text, site="deepseek",
            project_dir=self._project_dir,
            save_as_concept=True,
        )
        self._dialog_worker.got_answer.connect(self._sig_dialog_answer.emit)
        self._dialog_worker.got_error.connect(self._sig_dialog_error.emit)
        self._dialog_worker.start()

    def _show_clarification_dialog(self, question: str, phase: str = ""):
        """B41 (2026-10-10): модальное окно уточнения."""
        try:
            from ui.dialogs.clarification_dialog import ClarificationDialog
        except Exception as _e:
            print(f"[B41] import error: {_e}")
            return
        dlg = ClarificationDialog(question, phase, parent=self)
        dlg.answer_submitted.connect(self._handle_clarification_answer)
        self._clarification_dialog = dlg
        dlg.exec()

    def _handle_clarification_answer(self, answer: str):
        """Обрабатывает ответ пользователя на уточнение.

        Добавляет пару (вопрос, ответ) в _pending_answers и
        перезапускает оркестратор с resume_from = фазы.
        """
        question = self._pending_question
        phase = self._pending_phase
        original_task = self._pending_task_text

        # Записываем в диалог
        self._append_user(answer)

        # Сохраняем пару
        self._pending_answers.append({
            "question": question,
            "answer": answer,
        })

        # Чистим состояние уточнения
        self._pending_question = ""
        self._pending_phase = ""
        self.start_btn.setText("Старт")
        self.start_btn.setStyleSheet("")
        self.input_field.clear()
        self.input_field.setPlaceholderText(
            "Введите задачу и нажмите Старт. "
            "Или напишите сообщение Link."
        )

        # Если задача потерялась — не можем продолжить
        if not original_task:
            self._append_link(
                "⚠ Не помню оригинальную задачу. Запустите её заново.",
                kind="warning"
            )
            self._pending_answers = []
            return

        # Запускаем продолжение
        self._append_link(
            f"Продолжаю с фазы «{phase}»...",
            kind="start"
        )

        self._task = BackgroundTask(
            task=original_task,
            project_dir=self._project_dir,
            on_progress=self._on_progress,
            on_finish=self._on_finish,
            user_answers=list(self._pending_answers),
            resume_from=phase or "",
        )
        self._task.start()
        self._refresh_status()
        self._update_buttons()

    def _check_checkpoint_on_project_open(self, project_dir: str):
        """LR-UI: при открытии проекта — обновить жёлтую панель.

        Логика (приоритеты):
          0. FIX 2026-09-26: если задача УЖЕ идёт (self._task.is_alive()) —
             панель не нужна, скрываем. Иначе панель появлялась
             во время активного прогона («найдена незавершённая задача»).
          1. Есть run_state.json со status=paused/running →
             панель с resume-контекстом (фаза, этап, ошибка).
          2. Иначе есть plan.json со status != done →
             панель со сводкой плана (этапы, прогресс).
          3. Иначе → панель скрыта.
        """
        # FIX: активный прогон — панель не показываем
        try:
            if self._task is not None and self._task.is_alive():
                self._hide_checkpoint_panel()
                return
        except Exception:
            pass

        # ─── Приоритет 1: run_state.json ───
        try:
            from core.orchestrator import _load_checkpoint
            cp = _load_checkpoint(project_dir)
        except Exception:
            cp = {}

        _cp_status = (cp or {}).get("status")

        # Устаревший чекпоинт completed → чистим
        if _cp_status == "completed":
            try:
                from core.orchestrator import _clear_checkpoint
                _clear_checkpoint(project_dir)
            except Exception:
                pass
            cp = {}

        if cp and _cp_status in ("paused", "running"):
            self._show_checkpoint_panel_from_cp(cp)
            return

        # ─── Приоритет 2: plan.json со status != done ───
        try:
            from core.plan_parser import parse_project_plan
            plan = parse_project_plan(project_dir) or {}
        except Exception:
            plan = {}

        _plan_status = plan.get("status", "")
        if plan and _plan_status and _plan_status != "done":
            self._show_plan_panel(plan)
            return

        # ─── Ничего нет → скрыть панель ───
        try:
            self._hide_checkpoint_panel()
        except Exception:
            pass

    def _render_checkpoint_label(self, cp: dict) -> str:
        """CP-INFO (2026-10-10): HTML для панели чекпоинта.

        Использует ВСЕ поля run_state.json: task, stage_name,
        completed_stages, total_elapsed, resume_instruction.
        """
        phase = cp.get("phase") or {}
        _si = phase.get("stage_index")
        _total = phase.get("total_stages")
        _stage_name = phase.get("stage_name") or ""
        _cur = phase.get("current") or "—"
        _task = (cp.get("task") or "").strip()[:120]
        _completed = cp.get("completed_stages") or []
        _counters = cp.get("counters") or {}
        _elapsed = _counters.get("total_elapsed") or 0
        _phases = _counters.get("phases_done") or 0
        _err = (cp.get("last_error") or "")[:120]
        _resume = (cp.get("resume_instruction") or "")[:200]

        # Этап: «этап 3 из 5 — Ротация секретов»
        if _si is not None and _total:
            _stage_str = f"этап {int(_si) + 1} из {int(_total)}"
            if _stage_name:
                _stage_str += f" — «{_stage_name}»"
        elif _si is not None:
            _stage_str = f"этап {int(_si) + 1}"
            if _stage_name:
                _stage_str += f" — «{_stage_name}»"
        else:
            _stage_str = "—"

        # Прогресс: «готово 2 из 5, 12 фаз, 24 мин»
        _prog_parts = []
        if _total:
            _prog_parts.append(f"готово {len(_completed)} из {int(_total)}")
        if _phases:
            _prog_parts.append(f"{int(_phases)} фаз")
        if _elapsed:
            _min = int(_elapsed) // 60
            _sec = int(_elapsed) % 60
            _prog_parts.append(f"{_min} мин {_sec} сек")
        _prog_str = ", ".join(_prog_parts) if _prog_parts else "—"

        lines = ["🔔 <b>Найдена незавершённая задача</b>", ""]
        if _task:
            lines.append(f"<b>Задача:</b> {_task}")
            lines.append("")
        lines.append(f"<b>Этап:</b> {_stage_str}")
        lines.append(f"<b>Фаза:</b> «{_cur}»")
        lines.append(f"<b>Прогресс:</b> {_prog_str}")
        if _err:
            lines.append(f"<b>Ошибка:</b> {_err}")
        lines.append("")
        if _resume:
            lines.append(f"💡 {_resume}")
            lines.append("")
        lines.append("<b>Что сделать?</b>")
        return "<br>".join(lines)

    def _show_checkpoint_panel_from_cp(self, cp: dict):
        """Показать панель с данными чекпоинта."""
        self._pending_checkpoint = cp
        phase = cp.get("phase") or {}
        # CP-INFO (2026-10-10): полная картина из helper.
        self._cp_label.setText(self._render_checkpoint_label(cp))
        self._checkpoint_panel.setVisible(True)
        try:
            self._update_buttons()
        except Exception:
            pass

    def _show_plan_panel(self, plan: dict):
        """Показать панель со сводкой плана (когда чекпоинта нет,
        но план не завершён).
        """
        self._pending_checkpoint = {}
        stages = plan.get("stages", []) or []
        total = len(stages)
        done = 0
        for s in stages:
            items = s.get("items", [])
            if items and all(
                it.get("status") == "done" for it in items
            ):
                done += 1
        _title = plan.get("title", "Проект")
        _status = plan.get("status", "")

        self._cp_label.setText(
            "📋 <b>Проект в работе — план не завершён</b><br><br>"
            f"{_title}<br>"
            f"Этапов: {done} из {total} завершено<br>"
            f"Статус плана: {_status}<br><br>"
            "Что сделать? Нажми «▶ Продолжить» — Link начнёт "
            "с первого незавершённого этапа."
        )
        self._checkpoint_panel.setVisible(True)
        try:
            self._update_buttons()
        except Exception:
            pass

    def _handle_has_checkpoint(self, snap: dict):
        """LONG-RUN: показать панель «Незавершённая задача»."""
        cp = (snap.get("result") or {}).get("checkpoint") or {}
        self._pending_checkpoint = cp
        phase = cp.get("phase") or {}
        # CP-INFO (2026-10-10): полная картина из helper.
        self._cp_label.setText(self._render_checkpoint_label(cp))
        self._checkpoint_panel.setVisible(True)
        # LR-UI-STATE: обновить кнопки (Старт блокируется)
        try:
            self._update_buttons()
        except Exception:
            pass

    def _hide_checkpoint_panel(self):
        """Скрыть панель чекпоинта."""
        try:
            self._checkpoint_panel.setVisible(False)
        except Exception:
            pass
        self._pending_checkpoint = {}
        # LR-UI-STATE: обновить кнопки (Старт снова активен)
        try:
            self._update_buttons()
        except Exception:
            pass

    def _on_cp_continue(self):
        """Кнопка «Продолжить» на панели.

        FIX #14: два случая:
          • Есть чекпоинт (панель «🔔») — resume через
            _start_task_resume(cp).
          • Нет чекпоинта, но есть план (панель «📋») —
            continue_project=True. Link сам определит,
            с какой фазы/этапа продолжить.
        """
        cp = getattr(self, "_pending_checkpoint", {}) or {}
        # Берём текст из поля ввода (если есть)
        try:
            _extra = self.input_field.toPlainText().strip()
        except Exception:
            _extra = ""

        self._hide_checkpoint_panel()

        # Если есть текст — сохраняем как user_answers к resume
        if _extra:
            self._append_user(_extra)
            try:
                self.input_field.clear()
            except Exception:
                pass
            self._pending_answers.append({
                "question": "(уточнение при resume)",
                "answer": _extra,
            })

        if cp:
            # Панель чекпоинта — обычный resume
            self._start_task_resume(cp)
            return

        # FIX #14: панель плана (без чекпоинта) — continue_project
        # Задачу берём из plan.json проекта.
        _task = ""
        try:
            from core.plan_parser import parse_project_plan
            plan = parse_project_plan(self._project_dir) or {}
            _task = (plan.get("task") or "").strip()
        except Exception:
            pass

        if not _task:
            QMessageBox.warning(
                self, "Ошибка",
                "Не нашёл задачу в plan.json.\n"
                "Введи задачу в поле ввода и запусти через «Старт»."
            )
            return

        self._pending_task_text = _task
        self._append_link(
            "▶ Продолжаю работу над проектом (план → code)...",
            kind="start",
        )
        self._task = BackgroundTask(
            task=_task,
            project_dir=self._project_dir,
            on_progress=self._on_progress,
            on_finish=self._on_finish,
            user_answers=list(self._pending_answers) or None,
            continue_project=True,
        )
        self._pending_answers = []
        self._task.start()
        self._refresh_status()
        self._update_buttons()

    def _on_cp_open_map(self):
        """LR-UI-FINAL-LOGIC: открыть «Карту проекта» в режиме правки."""
        self.open_map_requested.emit()

    def _on_cp_close(self):
        """LR-UI-FINAL-LOGIC: скрыть панель (чекпоинт сохраняется)."""
        self._hide_checkpoint_panel()

    def _start_task_resume(self, cp: dict):
        """LONG-RUN: продолжает с этапа N (continue_project)."""
        task = self._last_task_text or (cp.get("task") or "")
        if not task:
            QMessageBox.warning(
                self, "Ошибка",
                "Не помню оригинальную задачу. Запустите заново."
            )
            return
        self._pending_task_text = task
        self._append_link(
            "🔄 Продолжаю с сохранённого этапа...", kind="start"
        )
        self._task = BackgroundTask(
            task=task,
            project_dir=self._project_dir,
            on_progress=self._on_progress,
            on_finish=self._on_finish,
            continue_project=True,
        )
        self._task.start()
        self._refresh_status()
        self._update_buttons()

    def _start_task_new(self, cp: dict):
        """LONG-RUN: запускает заново с force_new=True."""
        task = cp.get("task") or self._last_task_text or ""
        if not task:
            QMessageBox.warning(
                self, "Ошибка",
                "Не помню оригинальную задачу. Введите вручную."
            )
            return
        self._append_user(task)
        self.input_field.clear()
        self._pending_answers = []
        self._pending_task_text = task
        self._append_link(
            "🔄 Запускаю заново (старый чекпоинт будет перезаписан)...",
            kind="start",
        )
        self._task = BackgroundTask(
            task=task,
            project_dir=self._project_dir,
            on_progress=self._on_progress,
            on_finish=self._on_finish,
            force_new=True,
        )
        self._task.start()
        self._refresh_status()
        self._update_buttons()

    def _handle_rule_command(self, original: str, rule: dict):
        """Сохраняет правило — в проект или глобально."""
        rule_text = rule.get("text", "")
        scope = rule.get("scope", "unknown")

        # Если проект не выбран — только глобально
        if not self._project_dir:
            scope = "global"

        try:
            if scope == "global":
                from core.rules_manager import add_global_rule
                entry = add_global_rule(rule_text, source="user")
                where = "глобально (для всех проектов)"
            else:
                from core.rules_manager import add_project_rule
                entry = add_project_rule(self._project_dir, rule_text,
                                          source="user")
                where = "в этот проект"
        except Exception as e:
            self._append_user(original)
            self._append_link(f"❌ Ошибка сохранения правила: {e}",
                              kind="warning")
            self.input_field.clear()
            return

        self._append_user(original)
        if entry:
            self._append_link(
                f"✅ Правило сохранено {where}: «{entry['text']}»",
                kind="text"
            )
        else:
            self._append_link("❌ Не удалось сохранить правило",
                              kind="warning")
        self.input_field.clear()

    # === Колбэки от фоновой задачи ===

    def _on_progress(self, phase: str, result):
        """Вызывается из фонового потока — только emit сигнала."""
        snap = {
            "status": getattr(result, "status", ""),
            "agent": getattr(result, "agent", "?"),
            "seconds": getattr(result, "seconds", 0.0),
        }
        self._sig_progress.emit(phase, snap)

    def _do_progress(self, phase: str, snap: dict):
        """Слот в главном потоке — обновляет UI."""
        # AE: запоминаем последнюю фазу
        self._last_phase = phase
        status_icon = "✓" if snap.get("status") == "success" else "✗"
        agent = snap.get("agent", "?")
        seconds = snap.get("seconds", 0.0)
        text = f"{status_icon} [{phase}] {agent} ({seconds:.1f}s)"
        self._append_link(text, kind="progress")
        # Дублируем в «Логи» и «Лог проекта» (через MainWindow)
        try:
            self.log_line.emit(text)
        except Exception:
            pass
        self._refresh_status()

    def _on_finish(self, state):
        """Вызывается из фонового потока — только emit сигнала."""
        # B48-SECRETS-REPORT: state = BackgroundTaskState, а
        # secrets_found лежит в state.result (dict от OrchestrationResult).
        _result = getattr(state, "result", None) or {}
        snap = {
            "status": getattr(state, "status", ""),
            "error": getattr(state, "error", ""),
            "clarification_question": getattr(state, "clarification_question", ""),
            "clarification_phase": getattr(state, "clarification_phase", ""),
            "secrets_found": list(_result.get("secrets_found") or []),
            "smoke_test": dict(_result.get("smoke_test") or {}),
        }
        self._sig_finish.emit(snap)

    def _append_secrets_warning(self, findings: list):
        """B48-SECRETS-REPORT (2026-10-12): оранжевое уведомление
        о найденных секретах после прогона. Ничего не блокирует.
        """
        _n = len(findings)
        _lines = [
            f"🟡 ВНИМАНИЕ: найдено {_n} секретов в проекте:",
        ]
        for f in findings[:10]:
            _kind = f.get("kind", "?")
            _file = f.get("file", "?")
            _line = f.get("line", "?")
            _lines.append(f"   • [{_kind}] {_file}:{_line}")
        if _n > 10:
            _lines.append(f"   ... и ещё {_n - 10}")
        _lines.append("")
        _lines.append("Что делать? Напиши в поле ввода и нажми «🛠 Доработать»:")
        _lines.append("   • «создай .env с этими ключами»")
        _lines.append("   • «добавь поле ввода в настройках»")
        _lines.append("   • «оставь как есть» (тогда забудь)")
        _text = "\n".join(_lines)
        self._append_link(_text, kind="warning")
        try:
            self.log_line.emit(
                f"[SECRETS] Найдено {_n} секретов после прогона"
            )
        except Exception:
            pass

    def _do_finish(self, snap: dict):
        """Слот в главном потоке — обновляет UI."""
        status = snap.get("status", "")
        # AE: запоминаем, что задача упала/остановлена — доступен retry
        if status in (ST_FAILED, ST_STOPPED):
            self._last_failed = True
        elif status == ST_DONE:
            # Если partial — считаем что можно попробовать
            # (в snap может быть "partial", но UI видит ST_DONE)
            self._last_failed = False
        if status == ST_DONE:
            msg = "✅ Задача выполнена."
            self._append_link(msg, kind="text")
            try:
                self.log_line.emit(msg)
            except Exception:
                pass
            # B48-SECRETS-REPORT (2026-10-12): оранжевое уведомление
            # о найденных секретах. Не блокирует — просто «есть
            # вопросики, реши сам».
            _sec = snap.get("secrets_found") or []
            if _sec:
                self._append_secrets_warning(_sec)
            self._clear_pending_clarification()
        elif status == ST_STOPPED:
            msg = "⏹ Задача остановлена."
            self._append_link(msg, kind="stop")
            try:
                self.log_line.emit(msg)
            except Exception:
                pass
            self._clear_pending_clarification()
        elif status == "has_checkpoint":
            # LONG-RUN: найдена незавершённая задача.
            self._handle_has_checkpoint(snap)
        elif status == ST_FAILED:
            err = snap.get("error") or "неизвестная ошибка"
            msg = f"❌ Задача завершилась с ошибкой: {err}"
            self._append_link(msg, kind="warning")
            try:
                self.log_line.emit(msg)
            except Exception:
                pass
            self._clear_pending_clarification()

            # AD.F: если ошибка похожа на «браузер завис / чат
            # не отвечает» — показать диалог с предложением
            # перезапустить браузеры.
            _err_low = str(err).lower()
            _is_stuck = any(m in _err_low for m in (
                "chat_stuck", "не отвечает после",
                "браузер завис", "browser_stuck",
                "перезапустите браузеры",
            ))
            if _is_stuck:
                try:
                    from PySide6.QtWidgets import QMessageBox
                    QMessageBox.warning(
                        self,
                        "Похоже, браузер завис",
                        "Модель не отвечает после нескольких "
                        "попыток с перезапуском чата.\n\n"
                        "Что можно сделать:\n"
                        "  • Откройте вкладку «Агенты» и нажмите\n"
                        "    «🔄 Перезапустить все браузеры».\n"
                        "  • Или вручную закройте окно Chromium\n"
                        "    и попробуйте снова.\n"
                        "  • Проверьте капчу / вход в браузере\n"
                        "    (кнопка «Показать браузер» в Диалоге).\n\n"
                        "После этого нажмите «🔄 Попробовать ещё раз»."
                    )
                except Exception:
                    pass
        elif status == ST_NEEDS_CLARIFICATION or status == "needs_clarification":
            # Link запросил уточнение
            q = snap.get("clarification_question", "")
            phase = snap.get("clarification_phase", "")
            self._pending_question = q
            self._pending_phase = phase
            # AI fix: если задача не запомнена — взять из _last_task_text
            if not self._pending_task_text and self._last_task_text:
                self._pending_task_text = self._last_task_text
            # Показываем вопрос
            msg = f"❓ Уточнение (фаза {phase}): {q}"
            self._append_link(
                f"{msg}\nОтветьте в поле ввода и нажмите «Ответить».",
                kind="question"
            )
            try:
                self.log_line.emit(msg)
            except Exception:
                pass
            # B41 (2026-10-10): модальное окно.
            try:
                self._show_clarification_dialog(q, phase)
            except Exception as _e:
                print(f"[B41] ошибка окна: {_e}")

            # Меняем подпись кнопки (запасной способ)
            self.start_btn.setText("Ответить")
            self.start_btn.setStyleSheet(
                "font-weight: bold; background-color: #eef;"
            )
            self.input_field.setPlaceholderText(
                "Введите ответ на вопрос Link..."
            )
        self._refresh_status()
        self._update_buttons()

    def _clear_pending_clarification(self):
        """Сбрасывает состояние ожидания ответа."""
        self._pending_question = ""
        self._pending_phase = ""
        self._pending_task_text = ""
        self.start_btn.setText("Старт")
        self.start_btn.setStyleSheet("")
        self.input_field.setPlaceholderText(
            "Введите задачу и нажмите Старт. "
            "Или напишите сообщение Link."
        )

    # === Обновление статуса и кнопок ===

    def _refresh_status(self):
        if self._task is None:
            self.status_label.setText("Статус: ожидание")
            try:
                self.status_changed.emit("")
            except Exception:
                pass
            return
        s = self._task.status()
        labels = {
            ST_IDLE: "ожидание",
            ST_RUNNING: "🟢 выполняется",
            ST_PAUSED: "⏸ пауза",
            ST_STOPPING: "⏹ останавливается",
            ST_DONE: "✅ готово",
            ST_FAILED: "❌ ошибка",
            ST_STOPPED: "⏹ остановлено",
            ST_NEEDS_CLARIFICATION: "❓ ждёт ответа",
        }
        text = f"Статус: {labels.get(s.status, s.status)}"
        self.status_label.setText(text)
        try:
            self.status_changed.emit(text.replace("Статус: ", ""))
        except Exception:
            pass

    def _update_buttons(self):
        """LR-UI-STATE: активность кнопок по состояниям.

        Таблица:
          Кнопка       idle  running  paused  checkpoint
          ▶ Старт       ✅     ❌       ✅       ❌
          ⏸ Пауза       ❌     ✅       ❌       ❌
          📤 Отправить   ✅     ❌       ✅       ✅
          ✕ СТОП        ❌     ✅       ✅       ❌
        """
        try:
            _panel_visible = self._checkpoint_panel.isVisible()
        except Exception:
            _panel_visible = False

        def _send_enabled(val):
            try:
                self.send_btn.setEnabled(val)
            except Exception:
                pass

        # Состояние 1: панель чекпоинта открыта
        if _panel_visible:
            self.start_btn.setEnabled(False)
            self.pause_btn.setEnabled(False)
            self.stop_btn.setEnabled(False)
            self.pause_btn.setText("Пауза")
            _send_enabled(True)
            try:
                self.retry_btn.setEnabled(False)
            except Exception:
                pass
            return

        # Состояние 2: нет активной задачи
        if self._task is None or not self._task.is_alive():
            self.start_btn.setEnabled(True)
            self.pause_btn.setEnabled(False)
            self.stop_btn.setEnabled(False)
            self.pause_btn.setText("Пауза")
            _send_enabled(True)
            try:
                self.retry_btn.setEnabled(bool(self._last_failed))
            except Exception:
                pass
            return

        # Состояние 3: задача идёт (running или paused)
        s = self._task.status().status

        if s == ST_PAUSED:
            # Пауза: Старт доступен, Отправить доступна (уточнить)
            self.start_btn.setEnabled(True)
            self.pause_btn.setEnabled(False)
            self.stop_btn.setEnabled(True)
            self.pause_btn.setText("Продолжить")
            _send_enabled(True)
        else:
            # Running или stopping: Старт и Отправить неактивны
            self.start_btn.setEnabled(False)
            self.pause_btn.setEnabled(s in (ST_RUNNING,))
            self.stop_btn.setEnabled(s in (ST_RUNNING, ST_PAUSED))
            self.pause_btn.setText("Пауза")
            _send_enabled(False)

        try:
            self.retry_btn.setEnabled(bool(self._last_failed))
        except Exception:
            pass
