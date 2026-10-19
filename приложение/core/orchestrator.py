"""Оркестратор Link — управляет фазами задачи.

Фазы: plan → code → review → fix → fallback.

На этом шаге (7.1) — только модель данных и константы.
Логика запуска фаз — в 7.2.
Логика цепочки и роутинга — в 7.3.
"""

import logging
from dataclasses import dataclass, field, asdict
from typing import Dict, Any, List, Optional
from datetime import datetime
from core.event_log import (
    set_run_context, update_context, clear_context,
    log_event, get_context,
)

logger = logging.getLogger(__name__)


# === Фазы ===

PHASE_PLAN = "plan"
PHASE_CODE = "code"
PHASE_REVIEW = "review"
PHASE_FIX = "fix"
PHASE_FALLBACK = "fallback"
PHASE_RESEARCH = "research"  # B (18.10.2026): разведка перед code — чеклист целей
PHASE_CONCEPT = "concept"   # вне основного цикла — генерация концепции
PHASE_DIALOG = "dialog"     # вне основного цикла — обсуждение концепции

PHASES = [PHASE_PLAN, PHASE_RESEARCH, PHASE_CODE, PHASE_REVIEW, PHASE_FIX, PHASE_FALLBACK]


# === Роли по умолчанию ===
# Когда появится Capability Profile (7.7) — будет выбираться по нему.

# FIX ROLES-NOT-NAMES (2026-09-29): убрали жёсткие имена агентов.
# Раньше: PHASE_PLAN="browser_qwen", PHASE_REVIEW="browser_qwen"
# Скрытая мина: если assignments.json очистится — Qwen вернётся.
# Теперь: пустой dict. Роль → агент ТОЛЬКО через assignments.
PHASE_DEFAULT_AGENT: Dict[str, str] = {}


# Маппинг фаз оркестратора на категории бенчмарка.
# Используется для выбора агента по профилю (Capability Profile)
# и для назначений в agent_assignments.json.
PHASE_TO_CATEGORY: Dict[str, str] = {
    PHASE_PLAN: "planning",
    PHASE_RESEARCH: "planning",  # B: research идёт через planning-агента
    PHASE_CODE: "coding",
    PHASE_REVIEW: "review",  # ROLES (2026-10-01): отдельная роль
    PHASE_FIX: "coding",
    # CONCEPT: мапится на ключ "concept" в agent_assignments.
    # Без этого get_phase_agent не видит назначение.
    PHASE_CONCEPT: "concept",
    # DIALOG: мапится на ключ "dialog".
    PHASE_DIALOG: "dialog",
    # ROLES (2026-10-01): PHASE_FALLBACK → brain.
    # Раньше был спец-ключ "fallback", но мы его убрали (объединили с brain).
    PHASE_FALLBACK: "brain",
}


PHASE_DESCRIPTIONS: Dict[str, str] = {
    PHASE_PLAN: "Разбор задачи, построение плана и структуры проекта",
    PHASE_RESEARCH: "Разведка: составить чеклист целей (research_notes.json)",
    PHASE_CODE: "Написание кода по плану",
    PHASE_REVIEW: "Проверка кода, поиск проблем и ошибок",
    PHASE_FIX: "Исправление по замечаниям review",
    PHASE_FALLBACK: "Резервный агент, если основная фаза не справилась",
    PHASE_CONCEPT: "Генерация концепции проекта по истории диалога",
    PHASE_DIALOG: "Обсуждение концепции с пользователем",
}


# === Датаклассы ===

@dataclass
class PhaseResult:
    """Результат одной фазы."""
    phase: str
    agent: str
    status: str = "pending"   # pending | success | failed | skipped
                              # | needs_clarification
    output: str = ""
    error: str = ""
    seconds: float = 0.0
    iterations: int = 0
    started_at: str = ""
    finished_at: str = ""
    clarification_question: str = ""  # если фаза запросила уточнение
    clarification_target: str = "user"  # code|plan|review|user|fallback
    # FIX 2026-09-25: файлы, созданные в этой фазе.
    files_created: List[str] = field(default_factory=list)
    # B55 (2026-10-13): правки существующих файлов и изменения
    # от run_command (снапшот до/после). Честная оценка работы.
    files_changed: List[str] = field(default_factory=list)
    # Б70 (14.10.2026): фаза завершилась через task_complete —
    # LLM осознанно сказала «работа сделана». Нужно чтобы
    # отличать «не смог» (needs_rework) от «не нужно» (done).
    task_complete: bool = False
    created_by_command: List[str] = field(default_factory=list)
    changed_by_command: List[str] = field(default_factory=list)


@dataclass
class OrchestrationContext:
    """Пакет контекста, который передаётся между фазами."""
    original_task: str
    project_dir: str
    current_phase: str = ""
    plan: Dict[str, Any] = field(default_factory=dict)
    errors: List[str] = field(default_factory=list)
    phase_history: List[Dict[str, Any]] = field(default_factory=list)
    user_answers: List[Dict[str, str]] = field(default_factory=list)
    # FIX: было 10. Для больших проектов (MT5: 12+ агентов,
    # 4-6 этапов) 10 фаз не хватает — цикл обрывается на
    # середине. HARD_LIMIT = 100 — общий предохранитель,
    # а реальный лимит считается динамически после plan-фазы.
    max_phases: int = 100
    # S2: вопрос, адресованный другой фазе (code/plan/review).
    # {"from": "review", "target": "code", "question": "..."}
    pending_clarification: Dict[str, Any] = field(default_factory=dict)
    # PHASES-1: индекс текущего этапа в плане (для code/fix).
    # 0 — если этапов нет или оркестратор ещё не начал
    # цикл по этапам.
    current_stage_index: int = 0
    # FIX MEMORY-INTEGRATION (2026-09-30): id паттернов,
    # которые были вставлены в промпт текущей фазы.
    # После завершения фазы — записываем outcome (success/failure).
    applied_pattern_ids: List[int] = field(default_factory=list)
    # FIX AUTO-LESSONS (2026-09-30): текст review с PROBLEMS,
    # ждущий успешного fix. После следующего review=OK
    # создаём паттерн.
    pending_problem_review: str = ""
    # ACCEPTANCE-GATE (2026-10-07): последний verification-receipt.
    # Нужен при fix×2 — чтобы отличить «нормальный fix×2»
    # от «провал этапа» (и вызвать brain).
    last_verification: Dict[str, Any] = field(default_factory=dict)
    # B55 (2026-10-13): были ли за текущий этап затронуты файлы
    # (created + changed + by_command). Сбрасывается при переходе
    # на новый этап. Используется в _mark_stage_done.
    stage_had_files: bool = False
    # Б64 (14.10.2026): была ли в этапе WRITE-фаза (code/fix/fallback).
    # Если была, но файлы не менялись → needs_rework.
    stage_had_write_phase: bool = False
    # Б70 (14.10.2026): была ли в этапе фаза, завершённая через
    # task_complete. Если да — LLM осознанно закончила, не провал.
    stage_had_task_complete: bool = False
    # Б62 (2026-10-14): сколько раз review дал неясный вердикт
    # (unknown) подряд. Если 1 — переспрашиваем. Если >= 1 снова
    # unknown — идём в fix как fallback.
    review_unknown_retries: int = 0

    def add_phase_result(self, result: PhaseResult) -> None:
        self.phase_history.append(asdict(result))


@dataclass
class OrchestrationResult:
    """Итог всей оркестрации."""
    success: bool
    status: str = "unknown"     # success | failed | timeout | cancelled
                                # | stopped | needs_clarification
    final_phase: str = ""
    phases_done: int = 0
    error: str = ""
    phase_history: List[Dict[str, Any]] = field(default_factory=list)
    files_created: List[str] = field(default_factory=list)
    seconds: float = 0.0
    clarification_question: str = ""  # если status=needs_clarification
    clarification_phase: str = ""     # на какой фазе остановились
    clarification_target: str = "user"  # кому адресован вопрос
    # LONG-RUN: снимок run_state.json, если при старте нашли
    # незавершённую задачу (status="has_checkpoint")
    checkpoint: Dict[str, Any] = field(default_factory=dict)
    # SMOKE-TEST-AFTER-RUN (Б47): результат проверки
    # работоспособности приложения.
    # {"ok": bool, "status": str, "rc": int, "cmd": str,
    #  "error": str, "output_tail": str}
    smoke_test: Dict[str, Any] = field(default_factory=dict)
    # B48-SECRETS-REPORT (2026-10-12): найденные секреты после
    # прогона. Список [{"file", "line", "kind"}]. Показывается
    # пользователю оранжевым — «есть вопросики».
    secrets_found: List[Dict[str, Any]] = field(default_factory=list)


# === Функции ===

def create_context(task: str, project_dir: str,
                   max_phases: int = 100) -> OrchestrationContext:
    """Создаёт новый контекст оркестрации."""
    return OrchestrationContext(
        original_task=task,
        project_dir=project_dir,
        max_phases=max_phases,
    )


def get_phase_agent(phase: str,
                    overrides: Optional[Dict[str, str]] = None) -> str:
    """Возвращает агента для фазы.

    Приоритет:
      1. Явное переопределение (overrides из orchestrate).
      2. Назначение из agent_assignments.json
         (только если назначенный агент включён).
      3. Capability Profile — лучший по категории фазы.
      4. Жёсткая роль PHASE_DEFAULT_AGENT.

    Всё работает даже без capabilities.json и без assignments —
    в этом случае поведение как раньше.
    """
    # 1. Явное переопределение
    if overrides and phase in overrides:
        return overrides[phase]

    # 2. Назначение (agent_assignments.json)
    try:
        from core.capability_benchmark import get_assigned_agent
        # ROLES (2026-10-01): fallback объединён с brain.
        if phase == PHASE_FALLBACK:
            assigned = get_assigned_agent("brain")
        else:
            category = PHASE_TO_CATEGORY.get(phase)
            assigned = get_assigned_agent(category) if category else None
        if assigned:
            return assigned
    except Exception:
        pass

    # 3. ROLES (2026-10-01): автоподбор УБРАН.
    # Раньше: get_best_agent_for_category подтягивал Gigachat
    # (1/5 в тестах) даже если пользователь не назначал.
    # Теперь — только явные назначения пользователя.

    # 4. FIX ROLES-NOT-NAMES: пустой dict → возвращаем "".
    #    Вызывающий код ОБЯЗАН обработать пустого агента
    #    (явная ошибка пользователю, а не молчаливый fallback).
    return PHASE_DEFAULT_AGENT.get(phase, "")


def describe_phase(phase: str) -> str:
    """Человеческое описание фазы."""
    return PHASE_DESCRIPTIONS.get(phase, f"Фаза {phase}")


def is_valid_phase(phase: str) -> bool:
    """Проверяет, что фаза известна."""
    return phase in PHASES


def summarize_history(context: OrchestrationContext,
                      limit: int = 5) -> str:
    """Краткая текстовая сводка последних фаз — для промпта."""
    if not context.phase_history:
        return "(история пуста)"
    lines = []
    for item in context.phase_history[-limit:]:
        phase = item.get("phase", "?")
        status = item.get("status", "?")
        agent = item.get("agent", "?")
        seconds = item.get("seconds", 0.0)
        lines.append(f"- {phase} [{status}] агентом {agent} ({seconds:.1f}s)")
    return "\n".join(lines)


# === Фазы, работающие через текст (без файлов) ===
TEXT_PHASES = {PHASE_PLAN, PHASE_RESEARCH, PHASE_REVIEW}

# === Фазы, работающие через файлы (run_agentic_loop) ===
FILE_PHASES = {PHASE_CODE, PHASE_FIX, PHASE_FALLBACK}


# === Сборка промпта для текстовой фазы ===

# Задача W: имена главных файлов — они идут в промпт review
# первыми, чтобы review увидел их целиком.
_PRIORITY_FILE_NAMES = {
    "main.py", "app.py", "cli.py", "run.py",
    "models.py", "storage.py", "services.py", "reports.py",
    "api.py", "core.py", "utils.py",
    "README.md",
}


def _collect_code_for_review(project_dir: str,
                             max_files: int = 15,
                             max_chars: int = 30000) -> str:
    """Собирает содержимое файлов проекта для review.

    Задача W:
      • лимиты увеличены (было 6000 / 8)
      • приоритет главным файлам (main.py, app.py, cli.py и т.п.)
      • при обрезке — явная пометка со счётчиком строк
      • тесты в конце (они не так важны для ревью кода)
    """
    from pathlib import Path as _P
    base = _P(project_dir)
    if not base.exists():
        return ""

    # Собираем кандидатов с приоритетами.
    # prio 0 — главные файлы
    # prio 1 — обычные .py и .md
    # prio 2 — тесты
    candidates = []
    # FIX: .sql — схемы БД; .json/.yaml/.toml — конфиги;
    # без них review не видит schema.sql и кричит CRITICAL ложно.
    patterns = [
        "*.py", "*.md", "*.txt", "*.desktop",
        "*.sql", "*.json", "*.yaml", "*.yml", "*.toml",
    ]
    # SECRETS (2026-10-07): имена, которые нельзя показывать LLM.
    _secret_names = {
        ".env", ".env.local", ".env.production", ".env.prod",
        ".env.dev", ".env.development", ".env.staging",
        "secrets.json", "credentials.json",
        "secrets.yaml", "secrets.yml",
        "credentials.yaml", "credentials.yml",
        "id_rsa", "id_ed25519", "id_dsa", "id_ecdsa",
    }
    _secret_exts = {".key", ".pem", ".pfx", ".p12"}

    def _is_secret(name: str, suffix: str) -> bool:
        n = name.lower()
        if n == ".env.example" or n.endswith(".example"):
            return False
        if n in _secret_names:
            return True
        if suffix.lower() in _secret_exts:
            return True
        if n.startswith(".env."):
            return True
        return False

    for pat in patterns:
        for f in base.rglob(pat):
            if "__pycache__" in f.parts or ".ai_link" in f.parts:
                continue
            # SECRETS: пропускаем .env, secrets.json и т.п.
            if _is_secret(f.name, f.suffix):
                continue
            name = f.name
            if name in _PRIORITY_FILE_NAMES:
                prio = 0
            elif name.startswith("test_") or "test" in f.parts:
                prio = 2
            else:
                prio = 1
            candidates.append((prio, f))

    # Сортируем: сначала по prio, потом по пути
    candidates.sort(key=lambda x: (x[0], str(x[1])))

    parts = []
    total = 0
    for prio, f in candidates:
        if len(parts) >= max_files or total >= max_chars:
            break
        try:
            ftext = f.read_text(encoding="utf-8")
        except Exception:
            continue
        # SECRETS (2026-10-07): маскируем ключи в тексте.
        try:
            from core.hand_link import _mask_secrets
            ftext, _hits = _mask_secrets(ftext)
            if _hits:
                logger.info(
                    "SECRETS: в review замаскировано %d секретов в %s",
                    _hits, f.name,
                )
        except Exception:
            pass
        rel = f.relative_to(base).as_posix()
        total_lines = ftext.count("\n") + 1

        block = f"--- {rel} ---\n{ftext}\n"
        if total + len(block) > max_chars:
            cut = max_chars - total - 150
            if cut < 500:
                # Не хватает места — пропускаем, но помечаем.
                parts.append(
                    f"--- {rel} ---\n"
                    f"[файл не показан: {total_lines} строк, "
                    f"не хватило места в промпте — "
                    f"это ограничение длины, а не проблема кода.]\n"
                )
                continue
            truncated = ftext[:cut]
            lines_shown = truncated.count("\n") + 1
            block = (
                f"--- {rel} ---\n{truncated}\n"
                f"... [обрезано: показано {lines_shown} "
                f"из {total_lines} строк. Это ограничение длины "
                f"промпта, а не проблема кода.]\n"
            )
        parts.append(block)
        total += len(block)

    return "\n".join(parts)


def _mask_secrets_for_research(text: str) -> str:
    """B (18.10.2026): маскирует возможные секреты в research-ответе.

    LLM не должна писать реальные пароли в pattern. Но на всякий
    случай — если написала, заменяем длинные строковые литералы
    в кавычках внутри JSON на ***.
    """
    import re as _re
    # Маскируем присвоения вида PASSWORD = "..." / TOKEN = "..." / KEY = "..."
    text = _re.sub(
        r'(?i)((?:password|passwd|pwd|token|secret|api[_-]?key|auth)'
        r'\s*=\s*)["\x27][^"\x27]{2,}["\x27]',
        r'\1"***"',
        text,
    )
    return text


def _research_has_content(text: str) -> bool:
    """B+ (19.10.2026): проверяет, что ответ research осмысленный.

    Проблема (прогон 19.10): DeepSeek вернул 17 символов "Thinking"
    и остановился. run_text_phase счёл это success, research_notes
    сохранился с targets=[]. Ложный success.

    Критерии:
      • len(text) >= 50 (иначе — обрыв).
      • В тексте есть "targets" (ключ JSON).
    """
    if not text:
        return False
    if len(text) < 10:
        return False
    # B+ (19.10.2026): не только "targets". DeepSeek может
    # писать текстом — с JSON-объектами {"type":...} или {"id":...}.
    if '"targets"' in text or "'targets'" in text:
        return True
    if "{" in text and "}" in text:
        if '"type"' in text or "'type'" in text:
            return True
        if '"id"' in text or "'id'" in text:
            return True
    return False


def _parse_research_json(output: str) -> dict:
    """B (18.10.2026): парсит ответ research-фазы в research_notes.json.

    Принимает:
      • чистый JSON
      • ```json ... ```
      • текст с JSON внутри (ищет первую { ... } с targets)

    Возвращает {"version": 1, "targets": [...]} или {"version":1,"targets":[]}.
    """
    import json as _json
    import re as _re

    raw = (output or "").strip()
    if not raw:
        return {"version": 1, "targets": []}

    # Убираем code-обёртки
    raw = _re.sub(r'^```(?:json)?\s*', '', raw)
    raw = _re.sub(r'\s*```\s*$', '', raw)

    # Пытаемся как есть
    try:
        data = _json.loads(raw)
        if isinstance(data, dict) and "targets" in data:
            return _normalize_research(data)
    except Exception:
        pass

    # B+ (19.10.2026): fallback — если нет "targets", но есть
    # отдельные JSON-объекты {"type":...} или {"id":...} —
    # собрать их в targets. DeepSeek часто пишет ответ текстом
    # (без обёртки "targets": [...]), а JSON-объекты — внутри
    # рассуждения или отдельными строками.
    if '"targets"' not in raw and "'targets'" not in raw:
        _objs = []
        _i = 0
        while _i < len(raw):
            _s = raw.find("{", _i)
            if _s < 0:
                break
            _depth = 0
            _in_str = False
            _esc = False
            _e = -1
            for _j in range(_s, len(raw)):
                _ch = raw[_j]
                if _esc:
                    _esc = False
                    continue
                if _ch == "\\":
                    _esc = True
                    continue
                if _ch == '"':
                    _in_str = not _in_str
                    continue
                if _in_str:
                    continue
                if _ch == "{":
                    _depth += 1
                elif _ch == "}":
                    _depth -= 1
                    if _depth == 0:
                        _e = _j + 1
                        break
            if _e > 0:
                try:
                    _obj = _json.loads(raw[_s:_e])
                    if isinstance(_obj, dict):
                        if "type" in _obj and "id" not in _obj:
                            _objs.append(_obj)
                        elif "id" in _obj:
                            _objs.append(_obj)
                except Exception:
                    pass
            _i = _e if _e > 0 else _s + 1

        if _objs:
            _acceptance = [o for o in _objs if "type" in o]
            _targets = [o for o in _objs if "id" in o]
            if _targets:
                return _normalize_research({"targets": _targets})
            if _acceptance:
                return _normalize_research({"targets": [{
                    "id": "P0-1",
                    "severity": "P0",
                    "acceptance": _acceptance,
                }]})

    # Ищем первый {...} c targets — с балансировкой скобок,
    # иначе вложенные объекты обрезаются по первой '}'.
    _start = raw.find("{")
    while _start >= 0:
        depth = 0
        _in_str = False
        _esc = False
        _end = -1
        for _i in range(_start, len(raw)):
            ch = raw[_i]
            if _esc:
                _esc = False
                continue
            if ch == "\\":
                _esc = True
                continue
            if ch == '"':
                _in_str = not _in_str
                continue
            if _in_str:
                continue
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    _end = _i + 1
                    break
        if _end > 0:
            _chunk = raw[_start:_end]
            try:
                data = _json.loads(_chunk)
                if isinstance(data, dict) and "targets" in data:
                    return _normalize_research(data)
            except Exception:
                # B+ (19.10.2026): repair для НЕвалидного JSON.
                # Порядок 1 → 2 → 3. lambda: 2 слэша.
                try:
                    # 1. \' → '
                    _fixed = _re.sub(r"(?<!\\)\\'", "'", _chunk)
                    # 2. Одиночные \ → \\ (кроме валидных + u)
                    _fixed = _re.sub(
                        r'(?<!\\)\\(?!["\\/bfnrtu])',
                        lambda m: "\\\\",
                        _fixed,
                    )
                    # 3. \u без 4 hex → \\u
                    _fixed = _re.sub(
                        r'(?<!\\)\\u(?![0-9a-fA-F]{4})',
                        lambda m: "\\\\u",
                        _fixed,
                    )
                    data = _json.loads(_fixed)
                    if isinstance(data, dict) and "targets" in data:
                        return _normalize_research(data)
                except Exception:
                    pass
        _start = raw.find("{", _start + 1)

    return {"version": 1, "targets": []}


def _normalize_research(data: dict) -> dict:
    """B+ (19.10.2026): нормализует research-targets — ДВЕ схемы.

    Схема v1.1 (новая, Perplexity):
      {"id", "severity", "outcome", "files_in_scope",
       "files_forbidden", "acceptance": [{type, ...}],
       "status", "attempts", "max_attempts", "on_exhausted", ...}

    Схема v1 (старая, наша):
      {"id", "priority", "file", "pattern", "check_kind", "action"}

    Приоритет: если есть acceptance[] — это v1.1.
    Иначе — v1 (требует file + pattern).
    Маскирует секреты в pattern.
    """
    out = []
    for t in (data.get("targets") or []):
        if not isinstance(t, dict):
            continue
        fid = (t.get("id") or "").strip() or f"P0-{len(out)+1}"

        # === Схема v1.1: acceptance[] ===
        if t.get("acceptance"):
            acc = []
            for ch in (t.get("acceptance") or []):
                if not isinstance(ch, dict):
                    continue
                ctype = (ch.get("type") or "").strip()
                if ctype not in (
                    "grep_absent", "grep_present",
                    "file_exists", "python_compile",
                ):
                    continue
                c = {"type": ctype}
                if ch.get("pattern"):
                    c["pattern"] = _mask_secrets_for_research(
                        str(ch["pattern"])
                    )
                if ch.get("glob"):
                    c["glob"] = str(ch["glob"])
                if ch.get("path"):
                    c["path"] = str(ch["path"])
                acc.append(c)
            if not acc:
                continue
            severity = (t.get("severity") or "P0").strip().upper()
            if severity not in ("P0", "P1", "P2"):
                severity = "P0"
            out.append({
                "id": fid,
                "severity": severity,
                "outcome": (t.get("outcome") or "").strip(),
                "files_in_scope": list(t.get("files_in_scope") or []),
                "files_forbidden": list(t.get("files_forbidden") or []),
                "acceptance": acc,
                "status": "pending",
                "attempts": 0,
                "max_attempts": int(t.get("max_attempts") or 3),
                "on_exhausted": (
                    (t.get("on_exhausted") or "blocked").strip()
                ),
                "claimed_done_by_llm": False,
                "verified_by": None,
                "last_verdict": None,
                "last_error": "",
            })
            continue

        # === Схема v1: pattern/check_kind ===
        file_ = (t.get("file") or "").strip()
        pattern = (t.get("pattern") or "").strip()
        check_kind = (t.get("check_kind") or "contains").strip()
        action = (t.get("action") or "").strip()
        priority = (t.get("priority") or "P0").strip()
        if not file_ or not pattern:
            continue
        if check_kind not in ("contains", "not_contains"):
            check_kind = "contains"
        pattern = _mask_secrets_for_research(pattern)
        out.append({
            "id": fid,
            "priority": priority,
            "file": file_,
            "pattern": pattern,
            "check_kind": check_kind,
            "action": action,
        })

    return {"version": 1, "targets": out}


def _save_research_notes(project_dir: str, data: dict) -> None:
    """B (18.10.2026): сохраняет research_notes.json в .ai_link/.

    Перед записью прогоняет весь текст через маскировку секретов.
    """
    import json as _json
    from pathlib import Path as _P
    out_dir = _P(project_dir) / ".ai_link"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "research_notes.json"
    # Маскируем ДО сериализации — иначе JSON-экранирование
    # ломает regex (кавычки становятся \" и не матчатся).
    _safe = {"version": data.get("version", 1), "targets": []}
    for _t in (data.get("targets") or []):
        if not isinstance(_t, dict):
            continue
        _tc = dict(_t)
        if "pattern" in _tc and isinstance(_tc["pattern"], str):
            _tc["pattern"] = _mask_secrets_for_research(_tc["pattern"])
        if "action" in _tc and isinstance(_tc["action"], str):
            _tc["action"] = _mask_secrets_for_research(_tc["action"])
        _safe["targets"].append(_tc)
    text = _json.dumps(_safe, ensure_ascii=False, indent=2)
    try:
        path.write_text(text, encoding="utf-8")
    except Exception:
        pass


def _build_text_prompt(context: OrchestrationContext,
                       phase: str) -> str:
    """Промпт для plan/review — вернуть текст, не создавать файлы."""
    desc = describe_phase(phase)
    history = summarize_history(context, limit=5)

    # FIX MEMORY-INTEGRATION (2026-09-30): вставляем топ-5 активных
    # паттернов из памяти. Сохраняем их id в context — после фазы
    # запишем outcome (success/failure) через memory_api.
    _patterns_text = ""
    _pattern_ids = []
    try:
        from core import memory_api as _mem
        _pats = _mem.get_active_patterns(limit=5)
        if _pats:
            _pattern_ids = [p.get("id") for p in _pats
                            if p.get("id") is not None]
            _proj_name = __import__("os").path.basename(
                (context.project_dir or "").rstrip("/"))
            _patterns_text = _mem.render_for_prompt(
                project_name=_proj_name, limit=5)
    except Exception as _e:
        logger.debug("MEMORY: паттерны не получены: %s", _e)
    # FIX MEMORY-INTEGRATION: ВСЕГДА сбрасываем поле (даже если пусто).
    # Иначе оркестратор запишет outcome для устаревших pid.
    context.applied_pattern_ids = _pattern_ids

    plan_text = ""
    _stage_info = ""
    if context.plan:
        try:
            import json as _json
            stages = context.plan.get("stages") or []
            _stage_idx = getattr(context, "current_stage_index", 0) or 0
            # FIX BUG-REVIEW-ПО-ЭТАПУ (2026-09-28):
            # для REVIEW — даём ТЕКУЩИЙ ЭТАП, не весь план.
            # Иначе review требует «всю задачу целиком» на этапе 1.
            if phase == PHASE_REVIEW and stages:
                if 0 <= _stage_idx < len(stages):
                    _stage = stages[_stage_idx]
                    _clean_stage = {
                        "name": _stage.get("name", ""),
                        "items": [
                            it.get("text", "")
                            for it in _stage.get("items", [])
                            if it.get("text")
                        ],
                    }
                    plan_text = _json.dumps(
                        {
                            "title": context.plan.get("title", ""),
                            "goal": context.plan.get("goal", ""),
                            "current_stage": _clean_stage,
                            "stage_number": _stage_idx + 1,
                            "total_stages": len(stages),
                        },
                        ensure_ascii=False,
                    )[:3500]
                    _stage_info = (
                        # PROMPT-REVIEW-FIX (2026-10-02): слово "Анализ"
                        # в названии этапа сбивало Qwen → писал план
                        # анализа вместо review. Явно говорим: ты REVIEW.
                        f"⚠️ Твоя задача — ПРОВЕРИТЬ код этапа "
                        f"{_stage_idx + 1} из {len(stages)}: "
                        f"«{_stage.get('name', '')}».\n"
                        f"Это фаза REVIEW, а не PLAN. НЕ пиши план, "
                        f"НЕ пиши анализ этапов — только проверка кода.\n"
                        f"НЕ требуй от автора реализации всей задачи "
                        f"целиком — она будет делаться по этапам.\n"
                        f"Смотри только то, что относится к этапу "
                        f"{_stage_idx + 1}.\n"
                    )
                else:
                    plan_text = _json.dumps(
                        {"title": context.plan.get("title", ""),
                         "goal": context.plan.get("goal", "")},
                        ensure_ascii=False,
                    )[:1000]
            else:
                # Для plan — весь план целиком (нужен для пересмотра)
                plan_text = _json.dumps(
                    context.plan, ensure_ascii=False
                )[:1500]
        except Exception:
            plan_text = str(context.plan)[:1500]

    errors_text = "\n".join(f"- {e}" for e in context.errors[-5:]) or "(нет)"

    prompt = (
        (_patterns_text + "\n\n" if _patterns_text else "")
        + "⚠️ Весь необходимый контекст — НИЖЕ. Если история "
        "чата пуста или непонятна, используй ТОЛЬКО информацию "
        "из этого сообщения.\n\n"
        f"Ты — AI Link, работаешь в фазе «{phase}».\n"
        f"Смысл фазы: {desc}\n\n"
        f"ОРИГИНАЛЬНАЯ ЗАДАЧА (общая цель):\n"
        f"{context.original_task}\n\n"
        f"ИСТОРИЯ ФАЗ:\n{history}\n\n"
    )

    # A.4 (2026-10-05): окружение — что установлено на машине.
    # LLM видит список инструментов и выбирает подход.
    # Без этого Link «слепой» — пишет код, который падает
    # на отсутствующей команде.
    try:
        from core.env_check import environment_summary_for_prompt
        _env_text = environment_summary_for_prompt()
        if _env_text:
            prompt += _env_text + "\n\n"
    except Exception as _e:
        logger.debug("ENV-SUMMARY: не получено: %s", _e)

    if _stage_info:
        prompt += _stage_info + "\n"
    if plan_text:
        prompt += f"ТЕКУЩИЙ ПЛАН:\n{plan_text}\n\n"
    if context.errors:
        prompt += f"ОШИБКИ:\n{errors_text}\n\n"

    # S2: если этой фазе адресован вопрос от другой роли
    pc = getattr(context, "pending_clarification", None) or {}
    if pc and pc.get("target") == phase:
        prompt += (
            f"=== ВОПРОС ОТ ФАЗЫ «{pc.get('from','?')}» ==="
            f"\n{pc.get('question','')}\n"
            "=== КОНЕЦ ВОПРОСА ===\n"
            "⚠️ Ответь на этот вопрос в рамках своей роли. "
            "Если нужно уточнить у пользователя — используй "
            "NEED_CLARIFICATION[user]: <вопрос>.\n\n"
        )

    if context.user_answers:
        prompt += "ОТВЕТЫ ПОЛЬЗОВАТЕЛЯ НА УТОЧНЕНИЯ:\n"
        for qa in context.user_answers[-5:]:
            q = (qa.get("question", "") or "")[:200]
            a = (qa.get("answer", "") or "")[:500]
            prompt += f"  Вопрос: {q}\n  Ответ: {a}\n"
        prompt += (
            "\n"
            "⚠️ ВАЖНО (AJ): пользователь УЖЕ ответил на эти вопросы.\n"
            "  • НЕ повторяй тот же вопрос.\n"
            "  • Если ответа достаточно — ВЫПОЛНИ работу фазы.\n"
            "  • NEED_CLARIFICATION используй ТОЛЬКО если ответ\n"
            "    непонятен или его категорически не хватает.\n"
            "  • Повторно спрашивать одно и то же — ЗАПРЕЩЕНО.\n"
            "\n"
        )

    if phase == PHASE_PLAN:
        # AO: подтягиваем концепцию проекта, если есть
        try:
            from pathlib import Path as _P
            concept = _P(context.project_dir) / ".ai_link" / "concept.md"
            if concept.exists():
                ctext = concept.read_text(encoding="utf-8")[:3000]
                if ctext.strip():
                    prompt += (
                        "=== КОНЦЕПЦИЯ ПРОЕКТА ===\n"
                        f"{ctext}\n"
                        "Учитывай её при планировании.\n\n"
                    )
        except Exception:
            pass
        # REPO-MAP: карта существующего проекта (если .py уже есть).
        # Не весь проект — компактная сводка модулей и зависимостей.
        try:
            from core.repo_map import (
                load_repo_map, save_repo_map, render_for_llm,
            )
            _rm = load_repo_map(context.project_dir)
            if not _rm or _rm.get("status") != "ok":
                save_repo_map(context.project_dir)
                _rm = load_repo_map(context.project_dir)
            if _rm.get("py_files", 0) > 0:
                _rtext = render_for_llm(context.project_dir, max_chars=2500)
                if _rtext.strip():
                    prompt += (
                        "=== КАРТА СУЩЕСТВУЮЩЕГО ПРОЕКТА ===\n"
                        f"{_rtext}\n"
                        "Учитывай существующие модули — не дублируй их.\n\n"
                    )
        except Exception:
            pass
        prompt += (
            "ТВОЯ ЗАДАЧА:\n"
            "Составь ПЛАН ПРОЕКТА. Не список действий — а структуру:\n"
            "  • что это за проект,\n"
            "  • какая у него цель,\n"
            "  • из каких этапов состоит работа,\n"
            "  • какие шаги в каждом этапе.\n\n"
            "ФОРМАТ ОТВЕТА — СТРОГО ТАКОЙ (без других заголовков):\n\n"
            "НАЗВАНИЕ: <короткое название проекта>\n"
            "ЦЕЛЬ: <1-2 предложения, что делает проект>\n"
            "\n"
            "ЭТАП: <название этапа 1>\n"
            "- <шаг 1.1>\n"
            "- <шаг 1.2>\n"
            "\n"
            "ЭТАП: <название этапа 2>\n"
            "- <шаг 2.1>\n"
            "\n"
            "ПРАВИЛА:\n"
            "  • 2-5 этапов\n"
            "  • В каждом этапе 2-5 шагов\n"
            "  • Никаких пояснений вокруг\n"
            "  • Только план, без JSON, без создания файлов\n\n"
            "ЕСЛИ НЕ МОЖЕШЬ СОСТАВИТЬ ПЛАН БЕЗ УТОЧНЕНИЯ ОТ ПОЛЬЗОВАТЕЛЯ — "
            "верни РОВНО ОДНУ строку в формате:\n"
            "  NEED_CLARIFICATION: (здесь напиши свой настоящий вопрос)\n"
            "Пиши живой вопрос на русском, без угловых скобок и placeholder-ов.\n"
            "Спрашивай только если без ответа не можешь продолжать."
        )
    elif phase == PHASE_REVIEW:
        # Передаём код проекта — иначе review не может проверить
        try:
            _code = _collect_code_for_review(context.project_dir)
            if _code:
                prompt += f"ФАЙЛЫ ПРОЕКТА:\n{_code}\n\n"
        except Exception:
            pass
        prompt += (
            "⚠️ ПРАВИЛА ПРОВЕРКИ (задача Y):\n"
            "\n"
            "1. НЕ ЖАЛУЙСЯ НА ОБРЕЗКУ. Если видишь «... [обрезано: показано N из M строк]» —\n"
            "   это ОГРАНИЧЕНИЕ ДЛИНЫ ПРОМПТА, а НЕ проблема кода.\n"
            "   Не пиши «файл обрезан», «пришлите код ещё раз», «не могу проверить».\n"
            "   Проверяй только то, что ВИДИШЬ.\n"
            "\n"
            "2. НЕ ТРЕБУЙ ПРИСЛАТЬ КОД. Код уже в промпте. Если чего-то не хватает —\n"
            "   это ограничение промпта, а не ошибка автора кода.\n"
            "\n"
            "3. НЕ ПРИДИРАЙСЯ К СТИЛЮ:\n"
            "   • порядок импортов — не важно\n"
            "   • длина строк (PEP8) — не важно\n"
            "   • пробелы и отступы — не важно\n"
            "   • документация к очевидному — не важно\n"
            "   Проверяй только: логику, ошибки, противоречия, безопасность.\n"
            "\n"
            "4. ЕСЛИ ПРОБЛЕМ НЕТ — скажи «STATUS: OK» без выдумывания.\n"
            "   Лучше честно OK, чем придумать придирку.\n"
            "\n"
            "ТВОЯ ЗАДАЧА:\n"
            "Посмотри на код проекта и найди РЕАЛЬНЫЕ проблемы:\n"
            "  • Логические ошибки (что-то не так работает)\n"
            "  • Противоречия между модулями\n"
            "  • Ошибки обработки данных (пустые, None, границы)\n"
            "  • Проблемы безопасности (пути, команды, SQL)\n"
            "  • Несоответствие требованиям задачи\n"
            "\n"
            # PROMPT-REVIEW-FIX (2026-10-02): формат стал
            # ШАБЛОНОМ (как в plan), а не текстовым описанием.
            # Qwen игнорировал описание, но следует шаблону.
            "ФОРМАТ ОТВЕТА — СТРОГО ТАКОЙ (без других заголовков):\n"
            "\n"
            "ВАРИАНТ A (проблем нет):\n"
            "STATUS: OK\n"
            "\n"
            "ВАРИАНТ B (есть проблемы):\n"
            "STATUS: PROBLEMS\n"
            "CRITICAL: <что сломано, падает, угроза безопасности>\n"
            "MAJOR: <код работает, но нарушает правила проекта>\n"
            "MINOR: <стиль, оформление, необязательные улучшения>\n"
            "\n"
            "НАЧНИ ОТВЕТ РОВНО С \"STATUS:\" — без вводных слов,\n"
            "без размышлений, без плана. Ты ПРОВЕРЯЕШЬ, а не планируешь.\n"
            "\n"
            "УРОВНИ (жёстко):\n"
            "  CRITICAL — код падает, логика неверна, уязвимость.\n"
            "  MAJOR    — нет обработки ошибок, нет type hints,\n"
            "             отсутствуют тесты для логики, нарушение\n"
            "             правил проекта.\n"
            "  MINOR    — PEP8, docstrings, порядок импортов,\n"
            "             длина строк, именование.\n"
            "\n"
            "ВАЖНО: если все проблемы — только MINOR, всё равно\n"
            "пиши STATUS: PROBLEMS и метки MINOR. Это нормально.\n"
            "Не смешивай уровни. Не пиши MINOR, если это MAJOR.\n"
            "\n"
            "Никаких JSON, никаких code-блоков, только текст.\n"
            "\n"
            "ЕСЛИ НУЖНО УТОЧНЕНИЕ ОТ ПОЛЬЗОВАТЕЛЯ — верни РОВНО ОДНУ строку:\n"
            "  NEED_CLARIFICATION: (твой настоящий вопрос)\n"
            "Пиши живой вопрос на русском, без placeholder-ов.\n"
            "\n"
            # PROMPT-REVIEW-FIX-2 (2026-10-02): общая защита —
            # даже без stages говорим: ты REVIEW, не PLAN.
            "⚠️ Ты работаешь в фазе REVIEW. Это НЕ фаза PLAN.\n"
            "НЕ пиши план, НЕ пиши анализ этапов, НЕ пиши \"НАЗВАНИЕ:/ЭТАП:\".\n"
            "Только проверка кода и STATUS: в первой строке."
        )
    elif phase == PHASE_RESEARCH:
        # B (18.10.2026): research-фаза — read-only разведка.
        # Читает отчёты о проблемах в проекте (ИНВЕНТАРИЗАЦИЯ,
        # ДИАГНОСТИКА, ошибки.md, АУДИТ) и превращает их в
        # структурированный чеклист целей research_notes.json.
        # Code/fix идут по нему как по списку обязательств.
        _reports_hint = ""
        try:
            from pathlib import Path as _PR
            base = _PR(context.project_dir)
            _cands = []
            if base.exists():
                for _f in base.iterdir():
                    if not _f.is_file() or _f.suffix.lower() != ".md":
                        continue
                    _n = _f.name.upper()
                    if (
                        "ИНВЕНТАРИЗАЦИЯ" in _n
                        or "ДИАГНОСТИКА" in _n
                        or "АУДИТ" in _n
                        or _n.startswith("ОШИБКИ")
                        or "ОТЧЕТ_ПОЧИНКА" in _n
                        or "ОТЧЁТ_ПОЧИНКА" in _n
                    ):
                        _cands.append(_f.name)
            if _cands:
                # B+ (18.10.2026): вставляем СОДЕРЖИМОЕ отчётов
                # (первые N символов каждого). TEXT_PHASE не умеет
                # читать файлы, поэтому даём содержимое в промпт.
                # Приоритет файлам, где обычно P0.
                # B+ (19.10.2026): АУДИТ_ЭТАП1 убран — он
                # противоречит ИНВЕНТАРИЗАЦИИ («исправлено» vs P0).
                # DeepSeek верил АУДИТу → пароль не попадал в targets.
                _priority = [
                    "ИНВЕНТАРИЗАЦИЯ_ПРОБЛЕМ.md",
                    "ДИАГНОСТИКА_ЭТАП2.md",
                    "ошибки.md",
                    "ОТЧЕТ_ПОЧИНКА.md",
                    "ОТЧЁТ_ПОЧИНКА.md",
                ]
                _ordered = []
                for _p in _priority:
                    if _p in _cands:
                        _ordered.append(_p)
                for _c in _cands:
                    if _c not in _ordered:
                        _ordered.append(_c)
                _PER_FILE = 3500
                _TOTAL_LIMIT = 12000
                _parts = []
                _total = 0
                for _fname in _ordered:
                    if _total >= _TOTAL_LIMIT:
                        break
                    try:
                        _txt = (base / _fname).read_text(
                            encoding="utf-8", errors="replace"
                        )[:_PER_FILE]
                    except Exception:
                        continue
                    _chunk = (
                        f"=== {_fname} ===\n"
                        f"{_txt}\n"
                        f"=== КОНЕЦ {_fname} ===\n"
                    )
                    _parts.append(_chunk)
                    _total += len(_chunk)
                _reports_hint = (
                    "=== СОДЕРЖИМОЕ ФАЙЛОВ-ОТЧЁТОВ В ПРОЕКТЕ ===\n"
                    "Это содержимое отчётов о проблемах. Прочитай "
                    "его внимательно и выпиши P0 в targets.\n\n"
                    + "\n".join(_parts)
                    + "\n"
                )
        except Exception:
            pass

        prompt += (
            _reports_hint
            + "=== ТЫ — ФАЗА RESEARCH (РАЗВЕДКА) ===\n"
            "Это read-only фаза. Ты НЕ пишешь код, НЕ создаёшь файлы, "
            "НЕ правишь проект. Ты только СОСТАВЛЯЕШЬ ЧЕКЛИСТ ЦЕЛЕЙ.\n\n"
            "ЗАЧЕМ: следующая фаза CODE пойдёт по твоему чеклисту "
            "как по списку обязательств. Если ты пропустишь P0 — "
            "он не будет сделан, и задача закроется как \"выполненная\" "
            "ложно. Поэтому твой список — критичен.\n\n"
            "ЧТО ДЕЛАТЬ:\n"
            "1. Прочитай отчёты-файлы в проекте (см. выше) — "
            "это ИНВЕНТАРИЗАЦИЯ_ПРОБЛЕМ.md, ДИАГНОСТИКА*.md, "
            "АУДИТ_*.md, ошибки.md, ОТЧЕТ_ПОЧИНКА.md (если есть).\n"
            "2. ⚠️ ВАЖНО: в отчётах выше явно помечены приоритеты "
            "P0 / P1 / P2 (например, «Приоритеты на этап 3», "
            "«P0 (критично)»). Выпиши ВСЕ пункты с пометкой P0 "
            "в targets — даже если их 3-5 и они выглядят "
            "разнородно. Каждый P0 = отдельный target.\n"
            "Если P0 в отчётах нет — выведи P0 из плана и задачи "
            "(что обязательно должно быть сделано, чтобы задача "
            "считалась выполненной).\n"
            "3. Для каждого P0 — определи: файл, в котором должно "
            "быть изменение, и regex-паттерн, который однозначно "
            "показывает \"сделано / не сделано\".\n"
            "4. Верни строго JSON (без markdown-обёртки, без текста "
            "вокруг) — схема v1.1:\n"
            "{\n"
            "  \"targets\": [\n"
            "    {\"id\": \"P0-1\",\n"
            "     \"severity\": \"P0\",\n"
            "     \"outcome\": \"No plaintext admin credential remains in source\",\n"
            "     \"files_in_scope\": [\"config.py\"],\n"
            "     \"files_forbidden\": [\"tests/**\"],\n"
            "     \"acceptance\": [\n"
            "       {\"type\": \"grep_absent\",\n"
            "        \"pattern\": \"ADMIN_PASSWORD\\\\s*=\\\\s*[\\\"'][^$]\",\n"
            "        \"glob\": \"**/*.py\"}\n"
            "     ]}\n"
            "  ]\n"
            "}\n\n"
            "ФОРМАТ ПОЛЕЙ:\n"
            "  • id — P0-1, P0-2, ...\n"
            "  • severity — P0 / P1 / P2 (в targets — только P0)\n"
            "  • outcome — ЧТО должно быть достигнуто (одна фраза, "
            "результат, не способ). Пример: «No plaintext admin "
            "credential remains in source».\n"
            "  • files_in_scope — список файлов/папок, которые "
            "МОЖНО менять (для этого target).\n"
            "  • files_forbidden — список файлов/папок, которые "
            "НЕЛЬЗЯ менять (например, tests/**).\n"
            "  • acceptance — СПИСОК проверок (все должны пройти). "
            "Типы:\n"
            "      • grep_absent   — regex НЕ должен найтись в glob.\n"
            "      • grep_present  — regex должен найтись в glob/path.\n"
            "      • file_exists   — файл по path существует.\n"
            "      • python_compile — файл по path компилируется.\n"
            "    Каждая проверка — словарь с полями:\n"
            "      type     — один из 4 типов выше.\n"
            "      pattern  — regex (для grep_absent/grep_present). "
            "Терпимый к пробелам и кавычкам.\n"
            "      glob     — маска файлов (например, **/*.py).\n"
            "      path     — конкретный файл (для file_exists / "
            "python_compile / grep_present).\n\n"
            "⚠️ ЗАПРЕТ НА СЕКРЕТЫ (важно):\n"
            "  • НЕ ПИШИ реальные значения паролей, токенов, ключей "
            "в поле pattern.\n"
            "  • ПЛОХО:  ADMIN_PASSWORD\\\\s*=\\\\s*[\\\"']123[\\\"']\n"
            "  • ХОРОШО: ADMIN_PASSWORD\\\\s*=\\\\s*[\\\"'][^\\\"']+[\\\"']\n"
            "  • В pattern — только структура: имя переменной + форма "
            "значения. Реальные значения задаст пользователь через .env.\n"
            "  • В action пиши «заменить на os.environ.get(...)» — "
            "без указания конкретного пароля.\n\n"
            "⚠️ ЕСЛИ НЕЧЕГО ИСКАТЬ:\n"
            "  • Если в проекте нет отчётов о проблемах и план "
            "не содержит явных P0 — верни {\"targets\": []}.\n"
            "  • Пустой targets — это нормально: code/review работают "
            "как раньше, без чеклиста.\n\n"
            "⚠️ Ты работаешь в фазе RESEARCH. Это НЕ фаза PLAN, "
            "НЕ фаза CODE. Не пиши план, не пиши код. "
            "Только JSON с targets в первой строке ответа."
        )
    else:
        prompt += "ТВОЯ ЗАДАЧА:\nВыполни работу фазы. Верни текстом."

    return prompt


def _signature_from_files(files: List[str]) -> str:
    """SIGNATURE-FROM-FILES (2026-10-01): signature из changed_files.

    Пример:
      ["gui/main_window.py", "my_project/export.py"]
      → "main-window-export"

    Почему так:
      • Файлы — факт, а не текст задачи (устойчиво к формулировкам,
        опечаткам, языку).
      • Автоматически разное для «добавь кнопку» и «исправь импорт».
      • Не нужен STOP-список.

    Fallback: если файлов нет — возвращает "".
    """
    if not files:
        return ""
    parts: List[str] = []
    seen = set()
    for f in files:
        if not f:
            continue
        # Последняя часть пути без расширения
        base = f.replace("\\", "/").split("/")[-1]
        if "." in base:
            base = base.rsplit(".", 1)[0]
        base = base.lower().replace("_", "-").strip("-")
        if not base:
            continue
        # Общие имена, которые не различают задачи
        if base in ("init", "pycache", "test", "tests"):
            continue
        if base in seen:
            continue
        seen.add(base)
        parts.append(base)
        if len(parts) >= 3:
            break
    return "-".join(parts)[:60]


def _is_valid_lesson_signature(sig: str, from_files: bool = False) -> bool:
    """B+ (19.10.2026): проверяет, что signature — урок, не лог.

    from_files=True: sig построен из changed_files (db, a, export).
      Порог 2 — db.py → db (2), a.py → a (1 — нет).
    from_files=False: sig из task. Порог 6 — app, cli (3) — нет.

    Отклоняет всегда:
      • имена этапов: stage\d+, этап\d+, analysis-\w+
      • имена файлов-мусор: *_dialog, *_test, test-*, .env,
        config-*, app-*, cli-*
    """
    _min_len = 2 if from_files else 6
    if not sig or len(sig) < _min_len:
        return False
    low = sig.lower()
    bad = (
        "stage", "этап", "analysis-",
        "_dialog", "_test", "test-",
        ".env", "config-", "-config",
        "app-", "-app", "cli-", "-cli",
    )
    for b in bad:
        if b in low:
            return False
    # B+ (19.10.2026): НЕ требуем глагол. Signature из файлов
    # (browser-manager-test) — валидна, если длина >= 20 и нет bad.
    # Мусор (config-cli, app, cli) — короткие или bad → отсекаются.
    return True


def _auto_create_pattern_from_context(
    ctx: OrchestrationContext, review_result
) -> Optional[int]:
    """AUTO-LESSONS (2026-09-30): создаёт паттерн из успешного fix.

    Вызывается когда review=OK после review=PROBLEMS с fix.
    Берёт problem_text из ctx.pending_problem_review и fix из
    последнего PHASE_FIX в ctx.phase_history.
    """
    import re as _re
    from core import memory_api as _mem

    problem_text = (ctx.pending_problem_review or "").strip()
    if not problem_text:
        return None
    # FIX AUTO-LESSONS-TASK (2026-09-30): signature строится из
    # ЗАДАЧИ пользователя, а не из reasoning review.
    # Review часто пишет "code stage plan..." как reasoning —
    # это мусор, не относится к делу.
    _task_text = (ctx.original_task or "").strip()

    # Ищем последний fix в истории фаз
    fix_output = ""
    fix_files: List[str] = []
    fix_agent = ""
    for ph in reversed(ctx.phase_history):
        if ph.get("phase") == PHASE_FIX:
            fix_output = (ph.get("output") or "")[:1000]
            fix_files = list(ph.get("files_created") or [])
            fix_agent = ph.get("agent") or ""
            break
    if not fix_output and not fix_files:
        return None

    # Trigger terms из problem_text
    STOP = {
        "the", "and", "for", "with", "this", "that", "from",
        "для", "что", "это", "как", "или", "при", "его", "её",
        "они", "был", "была", "все", "нет", "если", "то",
        "есть", "только", "нужно", "можно", "также", "было",
        # FIX AUTO-LESSONS-MEMORY (2026-09-30): reasoning-префикс
        # DeepSeek "Thought for N seconds" ловился как problem.
        "thought", "seconds", "second", "thinking", "thoughts",
        "reasoning", "analyzing", "analyze", "analysis",
        "step", "steps", "пропустить", "skip", "reviewing",
        "processing", "checking", "let", "me",
        # FIX AUTO-LESSONS-VERBS (2026-09-30): глаголы-команды
        # из задачи ("доработай", "добавь") — не термины.
        # Русские:
        "доработай", "добавь", "сделай", "создай", "исправь",
        "напиши", "измени", "удали", "обнови", "прочитай",
        "запусти", "проверь", "поправь", "переделай",
        "доделай", "почини", "продолжи", "используй",
        "убедись", "уточни", "улучши", "оптимизируй",
        # Английские:
        "add", "create", "make", "fix", "update", "delete",
        "read", "run", "check", "write", "implement",
        "continue", "improve", "optimize", "use", "ensure",
        "verify", "refactor", "remove",
    }
    # Приоритет: task_text (если есть) → fallback problem_text
    _sig_source = _task_text if _task_text else problem_text
    words = _re.findall(
        r"[A-Za-zА-Яа-яЁё_][A-Za-zА-Яа-яЁё0-9_]{3,}",
        _sig_source.lower(),
    )
    seen: List[str] = []
    for w in words:
        if w in STOP or w in seen:
            continue
        # FIX AUTO-LESSONS-MEMORY: пропускаем чистые числа
        if w.isdigit():
            continue
        # FIX AUTO-LESSONS-MEMORY: слишком короткие
        if len(w) < 4:
            continue
        seen.append(w)
        if len(seen) >= 8:
            break
    # FIX AUTO-LESSONS-MEMORY: если меньше 2 значимых — не паттерн
    if len(seen) < 2:
        logger.debug(
            "AUTO-LESSONS: пропуск (мало значимых слов: %d)", len(seen)
        )
        return None

    # SIGNATURE-FROM-FILES (2026-10-01): приоритет — changed_files.
    # Они устойчивее текста задачи (не зависят от формулировки).
    sig = _signature_from_files(fix_files)
    if not sig:
        # SIGNATURE-NO-FILES (2026-10-02): если fix не менял файлы
        # (или только общие — __init__, test) — паттерн бесполезен.
        # Раньше падали в fallback на task → мусор типа
        # "my_project-кнопку-показать" (паттерн 570).
        # Паттерн без changed_files нельзя применить к будущим
        # задачам — не создаём.
        logger.debug(
            "AUTO-LESSONS: пропуск (files=%d, sig пустой)",
            len(fix_files),
        )
        return None

    # B+ (19.10.2026): валидация signature (1.1.4).
    # sig — из changed_files → from_files=True (порог 2).
    # db.py → db (2) — валиден. a.py → a (1) — нет.
    if not _is_valid_lesson_signature(sig, from_files=True):
        logger.debug(
            "AUTO-LESSONS: пропуск (невалидный sig: %r)", sig[:80]
        )
        return None

    fix_line = ""
    for line in (fix_output or "").split("\n"):
        line = line.strip()
        if len(line) > 15 and not line.startswith(("#", "//", "```")):
            fix_line = line[:200]
            break

    component = ""
    if fix_files:
        component = fix_files[0].split("/")[-1].replace(".py", "")

    provider = fix_agent.replace("browser_", "") if fix_agent else ""

    # MEMORY-PROJECT-SCOPE (2026-10-11): паттерн привязан к проекту.
    # Иначе все паттерны — глобальные и идут во все промпты (мусор).
    import os as _os_p
    _proj_name = _os_p.path.basename(
        (ctx.project_dir or "").rstrip("/")) or ""
    r = _mem.remember_lesson(
        signature=sig,
        title=f"Auto: {sig}",
        content=problem_text[:500],
        fix=fix_line,
        scope="project" if _proj_name else "global",
        scope_value=_proj_name,
        tags=f"auto,{provider},{component}",
        source="auto_fix",
    )
    pid = r.get("pattern_id")
    if pid:
        # B+ (19.10.2026): promote только через правило
        # (evidence >= 2, confidence >= 0.6). Иначе — candidate.
        # Раньше promote() был безусловным → мусор (config-cli, app)
        # попадал в active, в промпт, но никогда не проверялся.
        try:
            _mem.promote_if_ready(pid)
        except Exception:
            pass
        # AUTO-LESSON-NO-CREDIT (2026-10-03): «одна удача — один учёт».
        # Паттерн создан и попадёт в промпт — это каталогизация,
        # НЕ заслуга. Credit паттерн получит только когда реально
        # использован в fix (rule_v1, verified_effect=1).
        # Раньше здесь писался auto_lesson evidence с verified=1 —
        # инфляция confidence (паттерн 622: alpha 5.5 при 4 успехах).
        logger.info(
            "AUTO-LESSONS: создан паттерн %d '%s' (files=%d, provider=%s) — "
            "credit пойдёт через rule_v1 при использовании",
            pid, sig, len(fix_files), provider or "?",
        )
    return pid


def _collect_last_review_text(context: OrchestrationContext,
                              max_chars: int = 4000) -> str:
    """№24 (2026-10-07): берёт текст последнего review из phase_history.

    Зачем: fix сейчас видит только статус "problems", но не текст
    замечаний. В итоге чинит вслепую → лишние fix-циклы.

    Что делает:
      • ищет в phase_history последний phase="review" с непустым output;
      • обрезает reasoning-префикс («Thought for N seconds...»);
      • ограничивает длину max_chars.

    Возвращает пустую строку, если нечего передать.
    """
    if not context.phase_history:
        return ""

    for item in reversed(context.phase_history):
        if item.get("phase") != "review":
            continue
        text = (item.get("output") or "").strip()
        if not text:
            continue
        # Отрезаем reasoning DeepSeek/Qwen:
        # «Thought for N seconds...», «Thinking...», «The tool calls...»
        # Ищем STATUS: и берём с него.
        import re as _re
        m = _re.search(
            r"(STATUS\s*:.*)",
            text,
            _re.IGNORECASE | _re.DOTALL,
        )
        if m:
            text = m.group(1).strip()
        # Если reasoning начинается без STATUS — грубо режем до 4000.
        if len(text) > max_chars:
            text = text[:max_chars] + "\n... [обрезано]"
        return text

    return ""


def _build_file_task(context: OrchestrationContext,
                     phase: str) -> str:
    """Задача для run_agentic_loop — встраиваем контекст фазы."""
    desc = describe_phase(phase)
    history = summarize_history(context, limit=5)

    # FIX MEMORY-INTEGRATION (2026-09-30): топ-5 паттернов для code/fix.
    _patterns_text = ""
    _pattern_ids = []
    try:
        from core import memory_api as _mem
        _pats = _mem.get_active_patterns(limit=5)
        if _pats:
            _pattern_ids = [p.get("id") for p in _pats
                            if p.get("id") is not None]
            _proj_name = __import__("os").path.basename(
                (context.project_dir or "").rstrip("/"))
            _patterns_text = _mem.render_for_prompt(
                project_name=_proj_name, limit=5)
    except Exception as _e:
        logger.debug("MEMORY: паттерны не получены: %s", _e)
    # FIX MEMORY-INTEGRATION: ВСЕГДА сбрасываем поле (даже если пусто).
    context.applied_pattern_ids = _pattern_ids

    # PHASES-1: вместо обрезки ВСЕГО плана до 1500 символов
    # (из-за чего code-фаза видела только первый этап) —
    # передаём этапы ПОЛНОСТЬЮ, но по приоритету:
    #   • code/fix — текущий этап (stages[stage_index])
    #   • если stage_index не задан — все этапы целиком (до 4000)
    plan_text = ""
    if context.plan:
        try:
            import json as _json
            stages = context.plan.get("stages") or []
            _stage_idx = getattr(context, "current_stage_index", 0) or 0
            if phase in (PHASE_CODE, PHASE_FIX) and stages:
                # Только текущий этап
                if 0 <= _stage_idx < len(stages):
                    _stage = stages[_stage_idx]
                    # CHAT-LIFECYCLE: не передаём status (done/pending) —
                    # модель видит "done done done" и решает, что работа
                    # уже сделана. Оставляем только text.
                    _clean_stage = {
                        "name": _stage.get("name", ""),
                        "items": [
                            it.get("text", "")
                            for it in _stage.get("items", [])
                            if it.get("text")
                        ],
                    }
                    plan_text = _json.dumps(
                        {
                            "title": context.plan.get("title", ""),
                            "goal": context.plan.get("goal", ""),
                            "current_stage": _clean_stage,
                            "stage_number": _stage_idx + 1,
                            "total_stages": len(stages),
                        },
                        ensure_ascii=False,
                    )[:3500]
                else:
                    plan_text = _json.dumps(
                        {"title": context.plan.get("title", ""),
                         "goal": context.plan.get("goal", "")},
                        ensure_ascii=False,
                    )[:1000]
            else:
                # Для других фаз — весь план, но щедрее (4000)
                plan_text = _json.dumps(
                    context.plan, ensure_ascii=False
                )[:4000]
        except Exception:
            plan_text = str(context.plan)[:4000]

    errors_text = "\n".join(f"- {e}" for e in context.errors[-5:]) or "(нет)"

    # FIX GUI-RULES (2026-09-28): системные правила для code/fix.
    # Один раз прописано — работает во всех проектах.
    _RULES = (
        "[ОБЩИЕ ПРАВИЛА — ОБЯЗАТЕЛЬНО]\n"
        "1. GUI: НЕ использовать блокирующие модалки "
        "(QMessageBox.critical/warning/information). "
        "Ошибки — в logging.error/warning/info с записью "
        "в файл <project>.log.\n"
        "2. Исключение: подтверждение выхода "
        "(QMessageBox.question).\n"
        "3. Авто-тесты — через pytest, БЕЗ запуска GUI.\n"
        "4. СЕКРЕТЫ: НИКОГДА не пиши в код API-ключи, "
        "токены, пароли, логины, приватные URL. Только через "
        "os.environ.get(\"KEY\"). Создавай .env.example с "
        "примерами, не создавай .env с реальными ключами.\n"
        "5. GITIGNORE: в корне проекта создай .gitignore со "
        "строками: .env, .env.*, !.env.example, secrets.json, "
        "*.key, *.pem, __pycache__/, *.pyc, .pytest_cache/. "
        "Если .gitignore есть — допиши недостающее.\n"
        "[/ОБЩИЕ ПРАВИЛА]\n\n"
    )

    task = _RULES + (
        (_patterns_text + "\n\n" if _patterns_text else "")
        + "⚠️ Весь необходимый контекст — НИЖЕ. Если история "
        "чата пуста или непонятна, используй ТОЛЬКО информацию "
        "из этого сообщения.\n\n"
        f"[ФАЗА: {phase}]\n"
        f"Смысл фазы: {desc}\n\n"
        f"[ОРИГИНАЛЬНАЯ ЗАДАЧА]\n{context.original_task}\n\n"
        f"[ИСТОРИЯ ФАЗ]\n{history}\n\n"
    )
    if plan_text:
        if phase in (PHASE_CODE, PHASE_FIX) and context.plan.get("stages"):
            _si = getattr(context, "current_stage_index", 0) or 0
            _total = len(context.plan["stages"])
            task += (
                f"[ПЛАН — ЭТАП {_si + 1} ИЗ {_total}]\n"
                f"{plan_text}\n"
                f"⚠️ Сейчас работаешь ТОЛЬКО над этапом {_si + 1} "
                f"из {_total}. Не забегай вперёд.\n\n"
            )
        else:
            task += f"[ПЛАН]\n{plan_text}\n\n"

    # AO: подтягиваем концепцию проекта (если есть) —
    # чтобы code/fix/fallback тоже следовали концепции,
    # а не выдумывали своё.
    if phase in (PHASE_CODE, PHASE_FIX, PHASE_FALLBACK):
        try:
            from pathlib import Path as _P
            concept = _P(context.project_dir) / ".ai_link" / "concept.md"
            if concept.exists():
                ctext = concept.read_text(encoding="utf-8")[:3000]
                if ctext.strip():
                    task += (
                        "[КОНЦЕПЦИЯ ПРОЕКТА]\n"
                        f"{ctext}\n"
                        "Следуй этой концепции. Не меняй стек, "
                        "структуру или функции без явной просьбы.\n\n"
                    )
        except Exception:
            pass

    # №24 (2026-10-07): для fix-фазы добавляем ТЕКСТ последнего review.
    # Иначе fix видит только статус "problems" и чинит вслепую.
    if phase == PHASE_FIX:
        _review_text = _collect_last_review_text(context)
        if _review_text:
            task += (
                "[ЗАМЕЧАНИЯ REVIEW — что именно чинить]\n"
                f"{_review_text}\n"
                "Сфокусируйся на этих замечаниях. "
                "Не переделывай то, что review одобрил.\n\n"
            )

    if context.errors:
        task += f"[ОШИБКИ]\n{errors_text}\n\n"

    # S2: если этой фазе адресован вопрос от другой роли
    pc = getattr(context, "pending_clarification", None) or {}
    if pc and pc.get("target") == phase:
        task += (
            f"=== ВОПРОС ОТ ФАЗЫ «{pc.get('from','?')}» ===\n"
            f"{pc.get('question','')}\n"
            "=== КОНЕЦ ВОПРОСА ===\n"
            "⚠️ Ответь на этот вопрос в рамках своей роли.\n\n"
        )

    if context.user_answers:
        task += "[ОТВЕТЫ ПОЛЬЗОВАТЕЛЯ НА УТОЧНЕНИЯ]\n"
        for qa in context.user_answers[-5:]:
            q = (qa.get("question", "") or "")[:200]
            a = (qa.get("answer", "") or "")[:500]
            task += f"  Вопрос: {q}\n  Ответ: {a}\n"
        task += (
            "\n"
            "⚠️ ВАЖНО: пользователь УЖЕ ответил. НЕ повторяй "
            "вопросы, выполни работу фазы.\n"
            "\n"
        )

    if phase == PHASE_CODE:
        task += (
            "[ТВОЯ ЗАДАЧА В ЭТОЙ ФАЗЕ]\n"
            "Напиши код по плану. Создавай файлы, "
            "следуй структуре из плана. "
            "Если что-то неясно — сделай разумное предположение."
        )
    elif phase == PHASE_FIX:
        task += (
            "[ТВОЯ ЗАДАЧА В ЭТОЙ ФАЗЕ]\n"
            "В разделе [ОШИБКИ] находится отчёт review. "
            "Прочитай его ВНИМАТЕЛЬНО и сделай ИМЕННО то, что там "
            "написано: если просят добавить строку — добавь её; "
            "если просят убрать — убери; если переименовать — "
            "переименуй. Меняй минимально, не трогай то, что работает.\n"
            "\n"
            "SEVERITY (задача SEVERITY):\n"
            "  • CRITICAL и MAJOR — ИСПРАВЛЯЙ обязательно.\n"
            "  • MINOR — ИГНОРИРУЙ. Это стиль, docstrings, PEP8.\n"
            "    НЕ трогай код ради MINOR-замечаний.\n"
            "\n"
            "ВАЖНО: сначала прочитай актуальный файл через read_file, "
            "затем меняй через replace_in_file (точечно), "
            "не переписывай файл целиком через write_file."
        )
    elif phase == PHASE_FALLBACK:
        task += (
            "[ТВОЯ ЗАДАЧА В ЭТОЙ ФАЗЕ]\n"
            "Предыдущие агенты не справились. Попробуй свой подход. "
            "Проанализируй ошибки, предложи альтернативное решение."
        )
    else:
        task += "[ТВОЯ ЗАДАЧА В ЭТОЙ ФАЗЕ]\nВыполни работу."

    return task


# === Запуск фазы ===

def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _plan_has_structure(text: str) -> bool:
    """FIX №7: есть ли в ответе plan-фазы структура.

    Достаточно одного из: «НАЗВАНИЕ:», «ЭТАП:», «ЦЕЛЬ:».
    Пустой/огрызок стриминга — без них.
    """
    if not text:
        return False
    up = text.upper()
    return any(m in up for m in ("НАЗВАНИЕ:", "ЭТАП:", "ЦЕЛЬ:"))


def _save_ai_pair(
    phase: str,
    agent: str,
    task: str,
    prompt: str,
    response: str,
    success: bool,
    elapsed: float,
    error: str = "",
) -> None:
    """AI-RESPONSE-LOG (2026-10-02): сохраняет пару «промпт + ответ».

    Зачем: при разборе сбоев (Qwen, DeepSeek) нужен ПОЛНЫЙ ответ,
    а не output_head. Формат — .diagnostics/ai_responses/<ts>.jsonl.

    Пишет только если debug_logs=True (как и другие диагностики).
    """
    try:
        from config.config_manager import get_setting
        if not get_setting("debug_logs", False):
            return
    except Exception:
        return
    try:
        import json as _json
        from datetime import datetime as _dt
        from pathlib import Path as _P
        _dir = _P(__file__).resolve().parent.parent / ".diagnostics" / "ai_responses"
        _dir.mkdir(parents=True, exist_ok=True)
        _ts = _dt.now().strftime("%Y%m%d_%H%M%S_%f")
        _fname = f"{_ts}_{phase}_{agent.replace('browser_', '')}.jsonl"
        _entry = {
            "ts": _dt.now().isoformat(timespec="seconds"),
            "phase": phase,
            "agent": agent,
            "task": (task or "")[:500],
            "prompt_len": len(prompt or ""),
            "prompt": prompt or "",
            "response_len": len(response or ""),
            "response": response or "",
            "success": bool(success),
            "elapsed": round(float(elapsed or 0), 1),
            "error": (error or "")[:500],
        }
        (_dir / _fname).write_text(
            _json.dumps(_entry, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception:
        pass


def run_text_phase(context: OrchestrationContext, phase: str,
                   agent_override: str = "") -> PhaseResult:
    """Запускает фазу через query_browser_ai (текстовый ответ)."""
    import time as _time

    agent = agent_override or get_phase_agent(phase)

    # FIX ROLES-NOT-NAMES: явная проверка «роль не назначена».
    if not agent:
        _cat = PHASE_TO_CATEGORY.get(phase, phase)
        result = PhaseResult(
            phase=phase, agent="(не назначен)",
            status="failed", started_at=_now(),
        )
        result.error = (
            f"Роль для фазы '{phase}' не назначена. "
            f"Откройте: Настройки → Назначения агентов → "
            f"выберите агента для категории '{_cat}'."
        )
        result.finished_at = _now()
        logger.error(
            "phase %s: агент не назначен (категория=%s)",
            phase, _cat,
        )
        return result

    result = PhaseResult(
        phase=phase, agent=agent,
        status="pending", started_at=_now(),
    )

    # AGENT-DISPATCH (A.1.5, 2026-10-04): тип агента из профиля.
    # Имена моделей не зашиты — только тип: browser/api/local.
    # Если роль назначена — работает независимо от модели.
    from core.agent_profiles import get_agent_profile
    _profile = get_agent_profile(agent) or {}
    _ptype = (_profile.get("type") or "").lower()

    # Fallback по префиксу для агентов, которых нет в профилях
    if not _ptype:
        if agent.startswith("browser_"):
            _ptype = "browser"
        elif agent == "gigachat":
            _ptype = "api"
        elif agent == "ollama" or agent.startswith("ollama_"):
            _ptype = "local"

    # site — только для browser-агентов (какой сайт открыть)
    _site_map = {
        "browser_qwen": "qwen",
        "browser_deepseek": "deepseek",
        "browser_perplexity": "perplexity",
    }
    site = _site_map.get(agent) if _ptype == "browser" else None

    if _ptype == "browser" and site is None:
        result.status = "failed"
        result.error = (
            f"браузерный агент {agent} не имеет маппинга site — "
            f"добавьте в _site_map или в профиль"
        )
        result.finished_at = _now()
        logger.info("phase %s: провал — нет site для %s", phase, agent)
        return result
    if _ptype not in ("browser", "api", "local"):
        result.status = "failed"
        result.error = (
            f"агент {agent} не поддерживает текстовую фазу "
            f"(тип={_ptype or 'unknown'})"
        )
        result.finished_at = _now()
        logger.info("phase %s: провал — тип агента %s = %s",
                    phase, agent, _ptype or "unknown")
        return result

    # AC: если фаза review и в проекте НЕТ файлов кода —
    # не звать модель. Иначе review просит «пришлите код»
    # и создаёт цикл уточнений.
    if phase == PHASE_REVIEW:
        try:
            from pathlib import Path as _P
            base = _P(context.project_dir)
            has_code = False
            if base.exists():
                for f in base.rglob("*"):
                    if not f.is_file():
                        continue
                    if ".ai_link" in f.parts:
                        continue
                    if "__pycache__" in f.parts:
                        continue
                    if f.suffix in (".py", ".js", ".txt", ".md",
                                    ".html", ".css", ".json"):
                        has_code = True
                        break
            if not has_code:
                result.status = "failed"
                result.error = "review: нечего проверять — в проекте нет файлов"
                result.finished_at = _now()
                result.seconds = 0.0
                logger.info(
                    "phase review: пропущена — нет файлов в проекте"
                )
                return result
        except Exception:
            pass

    logger.info("phase %s: старт (агент=%s, тип=%s%s)",
                phase, agent, _ptype,
                f", site={site}" if site else "")
    prompt = _build_text_prompt(context, phase)

    # EVENT-LOG: model_call для TEXT_PHASES (plan/review).
    # agentic_loop._call_agent — только для FILE_PHASES,
    # а эти идут через query_browser_ai напрямую.
    try:
        log_event(event_type="model_call", payload={
            "agent": agent,
            "agent_type": _ptype,
            "site": site,
            "prompt_len": len(prompt or ""),
            "phase_kind": "text",
        })
    except Exception:
        pass

    start = _time.time()
    try:
        if _ptype == "browser":
            from core.agents.browser_agent import query_browser_ai
            r = query_browser_ai(site, prompt)
        elif _ptype == "api":
            # TEXT-PHASE (A.1.5, 2026-10-04): api-агенту нужен
            # ЧИСТЫЙ ТЕКСТ, а не JSON. У GigaChat это chat_mode=True.
            from core.gigachat_adapter import query_gigachat
            r = query_gigachat(prompt, chat_mode=True)
        elif _ptype == "local":
            from core.ollama_adapter import query_ollama
            r = query_ollama(prompt)
        else:
            r = {"success": False, "text": "",
                 "error": f"unknown type: {_ptype}"}
    except Exception as e:
        result.status = "failed"
        result.error = str(e)[:300]
        result.seconds = round(_time.time() - start, 2)
        result.finished_at = _now()
        try:
            log_event(event_type="model_response", payload={
                "agent": agent, "site": site,
                "success": False,
                "elapsed": round(_time.time() - start, 2),
                "error": str(e)[:200],
                "phase_kind": "text",
            })
        except Exception:
            pass
        return result

    # EVENT-LOG: model_response для TEXT_PHASES
    try:
        _rtext = (r.get("text") or "")
        log_event(event_type="model_response", payload={
            "agent": agent, "site": site,
            "success": bool(r.get("success")),
            "elapsed": round(_time.time() - start, 2),
            "text_len": len(_rtext),
            "head": _rtext[:200],
            "error": (r.get("error") or "")[:200],
            "phase_kind": "text",
        })
    except Exception:
        pass

    # AI-RESPONSE-LOG v2 (2026-10-02): сохраняем пару «промпт + ответ».
    # Работает для plan и review. В файл идут ПОЛНЫЕ данные —
    # это позволяет разбирать сбои Qwen (галлюцинации, timeout).
    _save_ai_pair(
        phase=phase,
        agent=agent,
        task=context.original_task or "",
        prompt=prompt,
        response=r.get("text", "") or "",
        success=bool(r.get("success")),
        elapsed=_time.time() - start,
        error=r.get("error", "") or "",
    )

    result.seconds = round(_time.time() - start, 2)
    result.finished_at = _now()

    if r.get("success"):
        text = r.get("text", "") or ""
        _target, q = parse_clarification_target(text)
        if q:
            result.status = "needs_clarification"
            result.clarification_question = q
            result.clarification_target = _target
            result.output = text
            logger.info("phase %s: уточнение (%s)", phase, q[:80])
        elif phase == PHASE_PLAN and not _plan_has_structure(text):
            # FIX №7: plan без структуры (НАЗВАНИЕ/ЭТАП) — провал.
            # Qwen иногда отдаёт огрызок стриминга (4-10 символов).
            result.status = "failed"
            result.error = (
                "plan: в ответе нет структуры (нет НАЗВАНИЕ/ЭТАП). "
                "Похоже, детектор ответа взял огрызок."
            )
            result.output = text
            logger.warning(
                "phase plan: failed — нет структуры (len=%d, head=%r)",
                len(text), text[:80],
            )
        elif phase == PHASE_REVIEW and parse_review_status(text) == "unknown":
            # FIX №7: review без маркера STATUS (OK/PROBLEMS) — провал.
            result.status = "failed"
            result.error = (
                "review: ответ без маркера STATUS (OK/PROBLEMS). "
                "Похоже, детектор ответа взял огрызок."
            )
            result.output = text
            logger.warning(
                "phase review: failed — нет STATUS (len=%d, head=%r)",
                len(text), text[:80],
            )
        elif phase == PHASE_RESEARCH and not _research_has_content(text):
            # B+ (19.10.2026): research вернул мусор (обрыв DeepSeek).
            # Не считаем это success — иначе research_notes.json
            # сохранится с targets=[] и acceptance gate не сработает.
            result.status = "failed"
            result.error = (
                f"research: мусорный ответ (len={len(text)}). "
                f"Ожидался JSON с ключом targets."
            )
            result.output = text
            logger.warning(
                "phase research: failed — нет targets (len=%d, head=%r)",
                len(text), text[:100],
            )
        else:
            result.status = "success"
            result.output = text
            logger.info("phase %s: success (%.1fs, %d символов)",
                        phase, result.seconds, len(text))
    else:
        result.status = "failed"
        result.error = (r.get("error") or r.get("reason") or "unknown")[:300]
        logger.info("phase %s: failed — %s", phase, result.error[:120])

    return result


def run_file_phase(context: OrchestrationContext, phase: str,
                   agent_override: str = "") -> PhaseResult:
    """Запускает фазу через run_agentic_loop (работа с файлами)."""
    import time as _time

    agent = agent_override or get_phase_agent(phase)

    # FIX ROLES-NOT-NAMES: явная проверка «роль не назначена».
    if not agent:
        _cat = PHASE_TO_CATEGORY.get(phase, phase)
        result = PhaseResult(
            phase=phase, agent="(не назначен)",
            status="failed", started_at=_now(),
        )
        result.error = (
            f"Роль для фазы '{phase}' не назначена. "
            f"Откройте: Настройки → Назначения агентов → "
            f"выберите агента для категории '{_cat}'."
        )
        result.finished_at = _now()
        logger.error(
            "phase %s: агент не назначен (категория=%s)",
            phase, _cat,
        )
        return result

    result = PhaseResult(
        phase=phase, agent=agent,
        status="pending", started_at=_now(),
    )

    task = _build_file_task(context, phase)

    logger.info("phase %s: старт (агент=%s, файловый цикл)", phase, agent)
    start = _time.time()
    try:
        from core.agentic_loop import run_agentic_loop
        r = run_agentic_loop(
            task=task,
            project_dir=context.project_dir,
            agent_mode=agent,
        )
    except Exception as e:
        result.status = "failed"
        result.error = str(e)[:300]
        result.seconds = round(_time.time() - start, 2)
        result.finished_at = _now()
        logger.info("phase %s: исключение — %s", phase, result.error[:120])
        return result

    result.seconds = round(_time.time() - start, 2)
    result.finished_at = _now()
    result.iterations = int(r.get("iterations") or 0)

    # FIX QUESTIONS-FALLBACK (run_file_phase): агент запросил
    # уточнение — возвращаем needs_clarification наверх.
    if r.get("status") == "needs_clarification":
        result.status = "needs_clarification"
        result.clarification_question = (
            r.get("clarification_question", "") or ""
        )
        result.clarification_target = (
            r.get("clarification_target", "user") or "user"
        )
        result.output = result.clarification_question[:2000]
        logger.info(
            "phase %s: уточнение от агента (%.2000s)",
            phase, result.clarification_question,
        )
        return result

    if r.get("success"):
        files = r.get("files_created", []) or []
        iters = int(r.get("iterations") or 0)

        # Задача P: code без файлов = FAIL, не «success».
        # Только для фазы code. Для fix/fallback файл может уже
        # существовать и не меняться — это не провал.
        # Б55 (2026-10-13): ОТКАЧЕНО. files_created==0 — не всегда
        # провал: Link может править файлы через replace_in_file
        # или создавать через run_command (тогда created_files не
        # растёт). Возвращаемся к iters < 2 — это ловит только
        # «ничего не делал» (1 итерация = прочитал и сдался).
        # B55 (2026-10-13): читаем ВСЕ четыре списка. code без
        # created_files может всё равно править/создавать через
        # replace_in_file или run_command. Проверяем сумму.
        changed = r.get("files_changed", []) or []
        created_by_cmd = r.get("created_by_command", []) or []
        changed_by_cmd = r.get("changed_by_command", []) or []
        _total_touched = (
            len(files) + len(changed)
            + len(created_by_cmd) + len(changed_by_cmd)
        )
        if (phase == PHASE_CODE and _total_touched == 0
                and iters < 2):
            result.status = "failed"
            result.error = (
                "code не затронул ни одного файла "
                f"(iterations={iters})"
            )
            logger.info(
                "phase %s: FAIL — 0 файлов (created+changed+by_cmd), "
                "%d итераций", phase, iters,
            )
        else:
            result.status = "success"
            result.output = str(r.get("message", ""))[:2000]
            result.files_created = list(files)
            result.task_complete = bool(
                r.get("task_complete", False)
            )
            result.files_changed = list(changed)
            result.created_by_command = list(created_by_cmd)
            result.changed_by_command = list(changed_by_cmd)
            # FIX SAFE-REPLACE: логируем предупреждения об удалении
            _sw = r.get("safe_replace_warnings") or []
            if _sw:
                logger.warning(
                    "SAFE-REPLACE: %d предупреждений об удалении "
                    "def/class с внешними вызовами",
                    len(_sw),
                )
                for _w in _sw[:5]:
                    _syms = ", ".join(
                        b.get("symbol", "?") for b in _w.get("broken_refs", [])
                    )
                    logger.warning(
                        "SAFE-REPLACE: %s → %s",
                        _w.get("file", "?"), _syms,
                    )
            logger.info(
                "phase %s: success (%.1fs, итераций=%d, файлов=%d)",
                phase, result.seconds, result.iterations, len(files),
            )
    else:
        result.status = "failed"
        result.error = str(r.get("error", "unknown"))[:300]
        logger.info("phase %s: failed — %s",
                    phase, result.error[:120])

    return result


def run_phase(context: OrchestrationContext, phase: str,
              agent_override: str = "") -> PhaseResult:
    """Универсальный запуск фазы — выбирает text или file."""
    if not is_valid_phase(phase):
        r = PhaseResult(phase=phase, agent="",
                        status="failed", started_at=_now())
        r.error = f"неизвестная фаза: {phase}"
        r.finished_at = _now()
        return r

    if phase in TEXT_PHASES:
        result = run_text_phase(context, phase, agent_override)
    else:
        result = run_file_phase(context, phase, agent_override)

    context.current_phase = phase
    context.add_phase_result(result)
    return result


# === Разбор маркера review ===

def _cut_to_real_answer(output: str) -> str:
    """Обрезает всё до последнего маркера размышления.

    Qwen (и подобные) сначала повторяют наш промпт, потом пишут
    «Завершено размышление», и только потом — реальный ответ.
    """
    if not output:
        return ""
    markers = [
        "Завершено размышление",
        "Размышление завершено",
        "Thinking finished",
        "Thought for",
        "Thinking...",
    ]
    cut = output
    for marker in markers:
        idx = cut.rfind(marker)
        if idx >= 0:
            nl = cut.find("\n", idx)
            if nl != -1:
                cut = cut[nl + 1:]
            else:
                cut = cut[idx + len(marker):]
            # Продолжаем поиск других маркеров в остатке — но обычно хватает
            break
    return cut.strip()


# Б56 (2026-10-13): reasoning-префиксы review, которые не
# обёрнуты в "Thinking finished". Если ответ начинается с них —
# это размышление, а не вердикт. В [ОШИБКИ] fix такое нельзя.
_REASONING_PREFIXES = (
    "thought for",
    "thinking",
    "we need",
    "we should",
    "we are",
    "the tool calls",
    "the task is",
    "the task to",
    "let me think",
    "let me analyze",
    "i need to",
    "i should",
    "the user",
    "user wants",
    "user asks",
    "user provided",
    "user said",
    "мы должны",
    "надо ",
    "нужно ",
)


def _looks_like_reasoning(text: str) -> bool:
    """Б56: начинается ли текст с reasoning-префикса.

    Смотрим только первые 300 символов и только начало строки.
    """
    if not text:
        return False
    head = text.lstrip()[:300].lower()
    return any(head.startswith(p) for p in _REASONING_PREFIXES)


def _extract_review_for_fix(output: str) -> str:
    """Б56: чистый текст review для [ОШИБКИ] fix.

    Логика:
      • Сначала обычная обрезка (_cut_to_real_answer).
      • Если результат начинается с reasoning —
        берём только часть от STATUS: (если он есть).
      • Если STATUS нет и это reasoning — возвращаем "".
        Лучше ничего, чем reasoning: fix иначе чинит не то.
      • Чистый ответ без reasoning — возвращаем как есть.
    """
    if not output:
        return ""
    cut = _cut_to_real_answer(output)
    if not cut:
        cut = output
    cut = cut.strip()
    if not cut:
        return ""
    if not _looks_like_reasoning(cut):
        return cut
    st_idx = cut.find("STATUS:")
    if st_idx >= 0:
        return cut[st_idx:].strip()
    return ""


_VALID_CLARIFICATION_TARGETS = {"code", "plan", "review", "user", "fallback"}


def parse_clarification_target(output: str):
    """Возвращает (target, question).

    Формат: NEED_CLARIFICATION[code]: вопрос
      • target ∈ {code, plan, review, user, fallback}
      • Без [xxx] → target="user" (обратная совместимость)
      • Неизвестный [xxx] → target="user"

    Возвращает ("user", "") если вопроса нет.
    """
    if not output:
        return ("user", "")
    real = _cut_to_real_answer(output)
    if not real:
        real = output
    for line in real.splitlines():
        s = line.strip()
        up = s.upper()
        if not up.startswith("NEED_CLARIFICATION"):
            continue
        rest = s[len("NEED_CLARIFICATION"):]
        target = "user"
        # Опциональный адрес [code]
        if rest.startswith("["):
            end = rest.find("]")
            if end != -1:
                t_raw = rest[1:end].strip().lower()
                if t_raw in _VALID_CLARIFICATION_TARGETS:
                    target = t_raw
                rest = rest[end + 1:]
        # Двоеточие опционально
        rest = rest.lstrip()
        if rest.startswith(":"):
            q = rest[1:].strip()
        else:
            q = rest.strip()
        # Защита от placeholder
        if not q:
            return (target, "")
        if q.startswith("<") and q.endswith(">"):
            return (target, "")
        q_low = q.lower()
        if "твой вопрос" in q_low:
            return (target, "")
        if "здесь напиши" in q_low:
            return (target, "")
        if "настоящий вопрос" in q_low:
            return (target, "")
        return (target, q)
    return ("user", "")


def parse_clarification_request(output: str) -> str:
    """Обёртка для обратной совместимости.

    Возвращает только текст вопроса (без адреса).
    Для работы с адресом используй parse_clarification_target().
    """
    return parse_clarification_target(output)[1]


# REV: маркеры проблемы в тексте review.
# Если в тексте есть хотя бы один — считаем, что review нашёл
# реальную проблему, даже без маркера STATUS.
_PROBLEM_MARKERS = (
    "проблем", "ошибк", "баг", "не так", "исправить", "сломан",
    "нарушен", "некорректн", "неверн", "неправильн", "уязвим",
    "problem", "error", "bug", "wrong", "incorrect", "flaw",
    "issue", "broken", "vulnerab", "fix", "should be",
)


# REV: строки-кнопки UI Qwen, которые попадают в ответ модели.
_QWEN_UI_NOISE = {
    "пропустить",
    "skip",
    "ответ 1",
    "ответ 2",
    "ответ 1/2",
    "ответ 2/2",
    "предпочитаю этот ответ",
    "copy",
    "download",
    "json",
    # FIX #16: расширено по реальным логам Qwen Studio
    "этот отзыв поможет нам оценить и улучшить работу qwen studio.",
    "этот отзыв поможет нам оценить и улучшить работу qwen studio",
    "какой ответ вы предпочитаете? выберите один, чтобы продолжить.",
    "какой ответ вы предпочитаете? выберите один, чтобы продолжить",
    "какой ответ вы предпочитаете?",
    "выберите один, чтобы продолжить",
    "выберите один, чтобы продолжить.",
    "оцените ответ",
    "оцените этот ответ",
    "спасибо за отзыв",
    "thanks for your feedback",
    "thanks for your feedback.",
    "which response do you prefer?",
    "choose one to continue",
}


def _strip_qwen_ui_noise(text: str) -> str:
    """Убирает строки-кнопки UI Qwen ('Пропустить', 'Ответ 1/2' и т.п.).

    Возвращает только содержательные строки. Если ничего не осталось —
    пустая строка.
    """
    if not text:
        return ""
    kept = []
    for line in text.splitlines():
        s = line.strip().lower()
        if not s:
            continue
        if s in _QWEN_UI_NOISE:
            continue
        # «Ответ 1», «Ответ 2» и т.п.
        if s.startswith("ответ ") and len(s) <= 12:
            continue
        kept.append(line)
    return "\n".join(kept).strip()


def parse_review_status(output: str) -> str:
    """Возвращает 'ok' | 'problems' | 'unknown' по маркеру в выводе review.

    Логика (REV):
      1. Если есть явный маркер STATUS: PROBLEMS → problems
      2. Если есть явный маркер STATUS: OK → ok
      3. Если маркера нет — убираем UI-мусор Qwen и смотрим:
         • пусто (только «Пропустить») → ok (review не нашёл проблем)
         • меньше 20 символов → ok (ничего существенного)
         • больше 20 символов содержательного текста → problems
         (review нашёл что-то, но забыл маркер — считаем проблемой)
    """
    if not output:
        return "unknown"

    real = _cut_to_real_answer(output)
    if not real:
        real = output

    # 1. Явные маркеры — приоритет
    head = real[:800].upper()
    if "STATUS: PROBLEMS" in head or "STATUS:PROBLEMS" in head:
        return "problems"
    if "STATUS: OK" in head or "STATUS:OK" in head:
        return "ok"

    # 1.5. FIX №7: дополнительные маркеры OK/чисто (без STATUS).
    # Qwen иногда пишет «Проверено, ошибок нет» / «всё ок».
    _low = real.lower()
    _ok_markers = (
        "ошибок нет", "проблем нет", "всё ок", "все ок",
        "всё чисто", "все чисто", "нарушений нет",
        "замечаний нет", "не обнаружено проблем",
        "no errors", "no issues", "no problems", "looks good",
    )
    if any(m in _low for m in _ok_markers):
        # Но только если НЕ упомянуто слово «проблема» с отрицанием
        # впереди. Пока просто — если маркер OK есть, и нет маркера
        # STATUS: PROBLEMS (уже проверен выше) — считаем OK.
        return "ok"

    # 2. Маркера нет — чистим мусор Qwen
    cleaned = _strip_qwen_ui_noise(real)

    # FIX #16: если исходный текст БЫЛ непустым, а после чистки
    # ничего не осталось — это не «OK», а мусор (UI Qwen,
    # ошибка соединения). Модель НЕ проверила код → unknown.
    if real.strip() and not cleaned:
        return "unknown"

    # 3. Если после чистки пусто или мало — считаем OK
    if len(cleaned) < 20:
        return "ok"

    # 4. Проверяем маркеры проблемы (REV2).
    # Если в тексте есть «проблем», «ошибк», «flaw», «wrong» и т.п. —
    # review реально нашёл проблему, даже если забыл маркер STATUS.
    low = cleaned.lower()
    has_problem = any(m in low for m in _PROBLEM_MARKERS)
    if not has_problem:
        # Длинный текст без маркеров проблемы — это описание,
        # не проблема.
        return "ok"

    return "problems"


def parse_review_severity(output: str) -> str:
    """Возвращает самый высокий уровень проблем в ответе review.

    Уровни: "critical" | "major" | "minor" | "none" | "unknown".
    "unknown" — маркеров уровня нет (старый формат / забыл метки).
    """
    if not output:
        return "unknown"

    real = _cut_to_real_answer(output) or output
    up = real.upper()

    # Точные маркеры: CRITICAL:, MAJOR:, MINOR:
    # (также допускаем [CRITICAL] / (CRITICAL) на всякий случай)
    import re as _re
    has_crit = bool(_re.search(
        r"(^|\n)\s*(?:[\[\(]?)\s*CRITICAL\b", up
    ))
    has_major = bool(_re.search(
        r"(^|\n)\s*(?:[\[\(]?)\s*MAJOR\b", up
    ))
    has_minor = bool(_re.search(
        r"(^|\n)\s*(?:[\[\(]?)\s*MINOR\b", up
    ))

    if has_crit:
        return "critical"
    if has_major:
        return "major"
    if has_minor:
        return "minor"
    return "unknown"


# AJ2 v2: семантическое сравнение вопросов.
_STOP_WORDS = {
    "и", "или", "а", "но", "что", "как", "где", "когда", "кто",
    "это", "в", "на", "с", "по", "для", "из", "к", "у", "о",
    "же", "бы", "ли", "не", "да", "нет", "то", "все", "весь",
    "быть", "есть", "был", "была", "было", "будут",
    "the", "a", "an", "is", "are", "was", "were", "of", "to",
    "in", "on", "at", "by", "for", "with", "and", "or",
}


def _stem(word: str) -> str:
    """Очень простой стеммер: длинные слова → до 5 символов.

    «хранения» и «хранение» → «хран».
    «проверить» → «прове».
    Короткие слова (≤5) не трогаем.
    """
    if len(word) > 5:
        return word[:5]
    return word


def _significant_words(text: str) -> set:
    """Значимые слова из текста (без стоп-слов, lowercase,
    с лёгким стеммингом)."""
    import re as _re
    words = _re.findall(r"[а-яёa-z0-9]+", text.lower())
    return {
        _stem(w) for w in words
        if len(w) >= 3 and w not in _STOP_WORDS
    }


def _questions_similar(q1: str, q2: str, threshold: float = 0.5) -> bool:
    """Считает вопросы похожими, если пересечение значимых слов
    ≥ threshold от меньшего множества.

    AJ/AJ2 v2: точное сравнение не работает — Qwen пишет
    «Что именно проверить?» vs «Уточни, что проверить» —
    это один вопрос, но тексты разные.
    """
    if not q1 or not q2:
        return False
    w1 = _significant_words(q1)
    w2 = _significant_words(q2)
    if not w1 or not w2:
        return False
    smaller = w1 if len(w1) <= len(w2) else w2
    if not smaller:
        return False
    common = w1 & w2
    return (len(common) / len(smaller)) >= threshold


def _is_question_in_past(q: str, past: set) -> bool:
    """Проверяет, есть ли похожий вопрос среди прошлых."""
    if not q:
        return False
    for old_q in past:
        if _questions_similar(q, old_q):
            return True
    return False


def _reset_browser_for_agent(agent_id: str,
                              wait_after: float = 5.0) -> None:
    """CHAT-LIFECYCLE: сбрасывает чат браузера для агента.

    Если агент не browser_* — ничего не делаем (GigaChat/Ollama
    не имеют чата в браузере). Ошибки — тихо логируем.

    FIX 2026-09-28: после reset ждём wait_after секунд,
    пока страница полностью загрузится. Иначе детектор
    схватывает UI-элемент «Авто» (4 символа) вместо
    реального ответа (2000+ символов).
    """
    if not agent_id or not agent_id.startswith("browser_"):
        return
    site_id = agent_id[len("browser_"):]
    try:
        from core.agents.browser_manager import reset_chat
        ok = reset_chat(site_id)
        logger.info(
            "CHAT-LIFECYCLE: reset чата %s → %s",
            site_id, "ok" if ok else "skip",
        )
        if ok and wait_after > 0:
            import time as _t
            logger.info(
                "CHAT-LIFECYCLE: пауза %.1f сек после reset "
                "(для загрузки страницы)",
                wait_after,
            )
            _t.sleep(wait_after)
    except Exception as _e:
        logger.warning(
            "CHAT-LIFECYCLE: reset_chat(%s) не удался: %s",
            site_id, _e,
        )


def _try_advance_stage(ctx, use_stages: bool, total_stages: int) -> bool:
    """PHASES: перейти к следующему этапу, если есть.

    Возвращает True, если переключились — caller должен сбросить
    fix_count/consecutive_failures и сделать continue.

    Не трогает локальные переменные orchestrate — только ctx.
    """
    if not use_stages or total_stages <= 0:
        return False
    _next_idx = ctx.current_stage_index + 1
    if _next_idx >= total_stages:
        # LAST-STAGE-DONE (2026-10-07): последний этап — перехода
        # нет, но пометить его как done НАДО. Иначе этап висит
        # pending навсегда.
        try:
            _mark_stage_done(
                ctx.project_dir, ctx.current_stage_index,
                touched=getattr(ctx, "stage_had_files", False),
                had_write_phase=getattr(
                    ctx, "stage_had_write_phase", False
                ),
                task_complete=getattr(
                    ctx, "stage_had_task_complete", False
                ),
            )
        except Exception:
            pass
        return False

    logger.info(
        "PHASES-2: этап %d/%d завершён, перехожу к этапу %d/%d",
        ctx.current_stage_index + 1, total_stages,
        _next_idx + 1, total_stages,
    )
    try:
        _mark_stage_done(
            ctx.project_dir, ctx.current_stage_index,
            touched=getattr(ctx, "stage_had_files", False),
            had_write_phase=getattr(
                ctx, "stage_had_write_phase", False
            ),
            task_complete=getattr(
                ctx, "stage_had_task_complete", False
            ),
        )
    except Exception:
        pass
    try:
        _code_agent = get_phase_agent(PHASE_CODE)
        _reset_browser_for_agent(_code_agent)
    except Exception:
        pass
    ctx.current_stage_index = _next_idx
    ctx.errors = []
    # B55 + Б64: сбрасываем флаги для нового этапа.
    ctx.stage_had_files = False
    ctx.stage_had_write_phase = False
    ctx.stage_had_task_complete = False
    return True


# ═══════════════════════════════════════════════════════════════
# LONG-RUN-AGENT. Чекпоинты для длительных прогонов.
# ═══════════════════════════════════════════════════════════════

def _checkpoint_path(project_dir: str):
    """Путь к run_state.json."""
    from pathlib import Path as _P
    return _P(project_dir) / ".ai_link" / "run_state.json"


def _save_checkpoint(ctx, result, run_id: str, start_ts: float,
                     status_override: str = "") -> None:
    """LONG-RUN: сохраняет состояние прогона в run_state.json.

    Вызывается после каждой фазы и при финале.
    Не падает при ошибках записи — тихо логирует.
    """
    import json as _json
    import time as _time
    from datetime import datetime as _dt

    try:
        from pathlib import Path as _P
        # Если project_dir не существует — не создаём мусор
        if not _P(ctx.project_dir).exists():
            return
        path = _checkpoint_path(ctx.project_dir)
        path.parent.mkdir(parents=True, exist_ok=True)

        stages = (ctx.plan or {}).get("stages") or []
        _si = getattr(ctx, "current_stage_index", 0) or 0
        _stage_name = ""
        if 0 <= _si < len(stages):
            _stage_name = stages[_si].get("name", "") or ""

        # completed_stages: все, где все items = done
        _completed = [
            i for i, s in enumerate(stages)
            if s.get("items") and all(
                it.get("status") == "done"
                for it in s.get("items", [])
            )
        ]

        # Финальный статус чекпоинта
        _status = status_override
        if not _status:
            if result.status == "success":
                _status = "completed"
            elif result.status in ("failed", "timeout", "stopped"):
                _status = "failed"
            else:
                _status = "running"

        _resume = ""
        if _status in ("paused", "failed"):
            if _si < len(stages):
                _resume = (
                    f"Продолжить с этапа {_si + 1} из "
                    f"{len(stages)} ({_stage_name})"
                )
            else:
                _resume = "Продолжить с последней фазы"

        data = {
            "run_id": run_id,
            "project_dir": ctx.project_dir,
            "task": ctx.original_task,
            "created_at": _dt.now().isoformat(timespec="seconds"),
            "updated_at": _dt.now().isoformat(timespec="seconds"),
            "status": _status,
            "phase": {
                "current": ctx.current_phase,
                "stage_index": _si,
                "stage_name": _stage_name,
                "total_stages": len(stages),
            },
            "completed_stages": _completed,
            "counters": {
                "total_elapsed": round(_time.time() - start_ts, 2),
                "phases_done": result.phases_done,
            },
            "user_answers": list(ctx.user_answers or []),
            "phase_history": list(ctx.phase_history[-5:]),
            "last_error": (result.error or "")[:300],
            "resume_instruction": _resume,
        }
        path.write_text(
            _json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.info(
            "LONG-RUN: чекпоинт сохранён (статус=%s, фаза=%s, "
            "этап=%d/%d, фаз=%d)",
            _status, ctx.current_phase, _si + 1, len(stages),
            result.phases_done,
        )
        # EVENT-LOG: снимок состояния
        try:
            from core.event_log import log_event as _log_ev
            _log_ev(event_type="checkpoint_saved", payload={
                "status": _status,
                "phase": ctx.current_phase,
                "stage_index": _si,
                "total_stages": len(stages),
                "phases_done": result.phases_done,
            })
        except Exception:
            pass
    except Exception as _e:
        logger.warning("LONG-RUN: не удалось сохранить чекпоинт: %s", _e)


def _load_checkpoint(project_dir: str) -> dict:
    """LONG-RUN: читает run_state.json или {} при отсутствии."""
    import json as _json
    try:
        path = _checkpoint_path(project_dir)
        if not path.exists():
            return {}
        return _json.loads(path.read_text(encoding="utf-8"))
    except Exception as _e:
        logger.warning("LONG-RUN: чекпоинт не прочитан: %s", _e)
        return {}


def _clear_checkpoint(project_dir: str) -> None:
    """LONG-RUN: удаляет run_state.json (при успехе)."""
    try:
        path = _checkpoint_path(project_dir)
        if path.exists():
            path.unlink()
            logger.info("LONG-RUN: чекпоинт очищен (задача успешна)")
    except Exception as _e:
        logger.warning("LONG-RUN: не удалось очистить чекпоинт: %s", _e)


def _normalize_task(task: str) -> str:
    """TASK-NORM (2026-10-01): нормализация текста задачи.

    Для сравнения «та же задача или нет» — приводим к общему виду:
      • lowercased
      • схлопнутые пробелы (включая \n, \t)
      • убраны ведущие/хвостовые знаки препинания

    Иначе "добавь кнопку" и "добавь кнопку." считаются разными.
    """
    import re as _re
    if not task:
        return ""
    t = task.lower().strip()
    t = _re.sub(r"\s+", " ", t)
    t = t.strip(" .,;:!?—-")
    return t


def _reset_stale_state(project_dir: str, task: str,
                       force: bool = False) -> bool:
    """STALE-STATE (2026-10-01): сброс если чекпоинт от ДРУГОЙ задачи.

    Смотрим run_state.json. Если task отличается от текущего —
    это новая задача над тем же проектом. Убираем:
      - run_state.json -> run_state.json.stale_<ts>
      - plan.json      -> plan.json.stale_<ts>

    Б59 (13.10.2026): новый параметр force.
      • force=True — сбрасываем ВСЕГДА, независимо от task.
        Используется при --new (force_new=True от CLI/GUI).
      • force=False — старое поведение (только при смене task).

    Возвращает True, если сброс был.
    """
    import json as _json
    from datetime import datetime as _dt
    from pathlib import Path as _P

    _cp_path = _checkpoint_path(project_dir)
    _cp_exists = _cp_path.exists()
    # При force=True сбрасываем даже без чекпоинта (но с plan.json).
    if not force and not _cp_exists:
        return False
    _cp = {}
    if _cp_exists:
        try:
            _cp = _json.loads(_cp_path.read_text(encoding="utf-8"))
        except Exception:
            _cp = {}
    if not force:
        _cp_task = (_cp.get("task") or "").strip()
        _cur_task = (task or "").strip()
        if not _cp_task or not _cur_task:
            return False
        # TASK-NORM (2026-10-01): сравниваем нормализованные версии.
        if _normalize_task(_cp_task) == _normalize_task(_cur_task):
            return False
    _ts = _dt.now().strftime("%Y%m%d_%H%M%S")
    _ai_dir = _P(project_dir) / ".ai_link"
    try:
        _cp_path.rename(_cp_path.with_name(f"run_state.json.stale_{_ts}"))
    except Exception as _e:
        logger.warning("STALE-STATE: run_state не убран: %s", _e)
    _plan_path = _ai_dir / "plan.json"
    if _plan_path.exists():
        try:
            _plan_path.rename(_plan_path.with_name(f"plan.json.stale_{_ts}"))
        except Exception as _e:
            logger.warning("STALE-STATE: plan.json не убран: %s", _e)
    if force and not _cp_exists:
        logger.info(
            "STALE-STATE (Б59): force_new=True — сброшен "
            "plan.json без run_state"
        )
    elif force:
        logger.info(
            "STALE-STATE (Б59): force_new=True — сброшено "
            "принудительно (задача совпадает)"
        )
    else:
        _cp_task = (_cp.get("task") or "").strip()
        _cur_task = (task or "").strip()
        logger.info(
            "STALE-STATE: новая задача над проектом - сброшено "
            "(было: %r, стало: %r)",
            _cp_task[:80], _cur_task[:80],
        )
    return True


def _mark_stage_in_progress(project_dir: str,
                             stage_index: int) -> None:
    """STAGE-IN-PROGRESS (2026-10-07): помечает все pending-items
    этапа как in_progress. Вызывается при старте code-фазы этапа.

    Зачем: показывать в дереве, что этап «идёт сейчас». Все items
    переходят в in_progress сразу (LLM делает их пачкой).
    При завершении этапа _mark_stage_done переведёт их в done.
    """
    import json as _json
    from pathlib import Path as _P

    path = _P(project_dir) / ".ai_link" / "plan.json"
    if not path.exists():
        return
    try:
        data = _json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return
    stages = data.get("stages") or []
    if not (0 <= stage_index < len(stages)):
        return
    changed = False
    for item in stages[stage_index].get("items", []):
        if item.get("status") == "pending":
            item["status"] = "in_progress"
            changed = True
    if not changed:
        return
    try:
        path.write_text(
            _json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.info(
            "STAGE-IN-PROGRESS: этап %d — items переведены в in_progress",
            stage_index + 1,
        )
    except Exception as _e:
        logger.debug("STAGE-IN-PROGRESS: %s", _e)


def _count_done_items(project_dir: str) -> int:
    """PROGRESS (2026-10-10): сколько items done+rejected в plan.json.

    Используется для определения прогресса прогона: если число
    выросло — фаза что-то сделала.
    """
    import json as _json
    from pathlib import Path as _P
    try:
        _p = _P(project_dir) / ".ai_link" / "plan.json"
        if not _p.exists():
            return 0
        _d = _json.loads(_p.read_text(encoding="utf-8"))
        _n = 0
        for s in _d.get("stages", []) or []:
            for it in s.get("items", []) or []:
                if it.get("status") in ("done", "rejected"):
                    _n += 1
        return _n
    except Exception:
        return 0


def _load_plan_stages(ctx, reset_index: bool = False):
    """PHASES-STARTUP (2026-10-09): подтягивает этапы из plan.json.

    Возвращает (use_stages, total_stages, recommended_max).
    Вызывается:
      • при старте orchestrate — если plan.json уже есть (кнопка
        «📋 По плану» / continue_project)
      • после фазы plan — когда план только что создан

    Без этого: при старте с code/fix _use_stages=False → переход
    между этапами не работает → после первого этапа сразу finish.
    """
    import json as _json
    from pathlib import Path as _P

    try:
        _plan_path = _P(ctx.project_dir) / ".ai_link" / "plan.json"
        if not _plan_path.exists():
            return (False, 0, 0)
        _plan_data = _json.loads(_plan_path.read_text(encoding="utf-8"))
        _stages = _plan_data.get("stages") or []
        if not _stages:
            return (False, 0, 0)
        ctx.plan["stages"] = _stages
        ctx.plan["title"] = _plan_data.get("title", "")
        ctx.plan["goal"] = _plan_data.get("goal", "")
        logger.info(
            "PHASES: загружено этапов из plan.json: %d", len(_stages)
        )
        if reset_index:
            ctx.current_stage_index = 0
        _total_items = sum(len(s.get("items", [])) for s in _stages)
        _use_stages = (len(_stages) >= 3 or _total_items >= 10)
        _total_stages = len(_stages)
        # PHASES-DYNAMIC-MAX (2026-10-09): рекомендованный лимит фаз
        # (по 7 на этап + 5 запас). Возвращаем третьим числом.
        _recommended_max = 5 + _total_stages * 7
        return (_use_stages, _total_stages, _recommended_max)
    except Exception as _e:
        logger.warning("PHASES: загрузка plan.json не удалась: %s", _e)
        return (False, 0, 0)


def _mark_stage_done(project_dir: str, stage_index: int,
                     touched: bool = True,
                     had_write_phase: bool = False,
                     task_complete: bool = False) -> None:
    """PHASES-2 + HONEST (2026-10-09): честно помечает этап.

    ROLLBACK-13.10.2026: параметр touched сохранён для совместимости,
    но не используется. Рабочая логика — FIX-INPROGRESS + HONEST.

    Логика:
      • Все items done/rejected → status="done" всему этапу.
      • Есть rejected + pending → частично (stage_status="partial").
      • Все items pending → status="skipped" (LLM не работал).
      • Ничего не меняем, если stage_index выходит за границы.

    НЕ превращает rejected в done. НЕ ставит done для pending items.
    """
    import json as _json
    from pathlib import Path as _P

    path = _P(project_dir) / ".ai_link" / "plan.json"
    if not path.exists():
        return
    try:
        data = _json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return
    stages = data.get("stages") or []
    if not (0 <= stage_index < len(stages)):
        return

    # FIX-REMOVED (2026-10-10): убранные этапы не трогаем.
    if stages[stage_index].get("origin") == "removed":
        return

    items = stages[stage_index].get("items") or []

    # Б64 + Б70 (14.10.2026): WRITE-этап без правок.
    # Различаем:
    #   • task_complete=True — LLM осознанно завершила, правки
    #     не нужны → это НЕ провал, обрабатываем как done.
    #   • task_complete=False — LLM не смогла / не пыталась
    #     → needs_rework.
    if not touched and had_write_phase and not task_complete:
        stages[stage_index]["status"] = "needs_rework"
        logger.warning(
            "Б64: этап %d — WRITE-фаза, но файлы не изменены "
            "(task_complete=False). Помечен needs_rework (не done).",
            stage_index + 1,
        )
        # Б64: план не done — есть незавершённый этап.
        data["status"] = "partial"
        try:
            path.write_text(
                _json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception:
            pass
        return

    # ROLLBACK-13.10.2026: откат B55 (skip при 0 файлов).
    # Возвращаемся к рабочей связке FIX-INPROGRESS + HONEST (10.10).
    # Параметр touched оставлен для совместимости, но не используется.
    # TODO: заменить на расширенный критерий (read/command/iterations)
    # в отдельной задаче.

    # FIX-INPROGRESS (2026-10-10): при финализации этапа
    # in_progress items → done. Иначе stage=done, а items висят
    # in_progress (Б34: визуальная несогласованность).
    for _it in items:
        if _it.get("status") == "in_progress":
            _it["status"] = "done"
    _st = [it.get("status", "pending") for it in items]
    _done_n = sum(1 for s in _st if s in ("done", "rejected"))
    _pending_n = sum(1 for s in _st if s == "pending")

    # HONEST: определяем статус этапа.
    if _pending_n == 0:
        # Все items закрыты или rejected → done
        stages[stage_index]["status"] = "done"
    elif _done_n == 0:
        # Все pending → skipped (LLM не работал)
        stages[stage_index]["status"] = "skipped"
        logger.warning(
            "STAGE-HONEST: этап %d — все items pending, "
            "помечен skipped (LLM не работал)",
            stage_index + 1,
        )
    else:
        # Смесь: часть сделана, часть pending
        stages[stage_index]["status"] = "partial"
        logger.warning(
            "STAGE-HONEST: этап %d — частично (%d done, %d pending)",
            stage_index + 1, _done_n, _pending_n,
        )

    # Б64: если есть needs_rework — план НЕ done, а partial.
    _has_rework = any(
        s.get("status") == "needs_rework" for s in stages
    )
    if _has_rework:
        data["status"] = "partial"
        logger.warning(
            "Б64: план не done — есть этапы needs_rework"
        )
    elif all(
        s.get("status") == "done"
        for s in stages
    ):
        data["status"] = "done"
    try:
        path.write_text(
            _json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception:
        pass


def _user_wants_only_plan(task: str) -> bool:
    """Проверяет, просил ли пользователь ТОЛЬКО план (без кода).

    Маркеры в исходной задаче означают: остановиться после plan,
    не идти в code. См. задача O в планы/бэклог.md.

    Список маркеров — намеренно узкий, чтобы не срабатывать
    случайно. Если в задаче явно «код не пиши» — сработает.
    Если «план и код» — не сработает (там нет маркеров).
    """
    if not task:
        return False
    low = task.lower()
    markers = [
        "код не пиши",
        "код не писать",
        "не пиши код",
        "не писать код",
        "только план",
        "только план проекта",
        "сначала обсудим",
        "пока не пиши",
        "пока не пиши код",
        "без кода",
        "only plan",
    ]
    return any(m in low for m in markers)


def _mark_plan_done(project_dir: str) -> None:
    """PHASES-2 + HONEST (2026-10-09): финализация плана.

    Честно считает статус каждого этапа по items:
      • все items done/rejected → stage="done"
      • есть pending, есть done → stage="partial"
      • все pending → stage="skipped"

    Общий plan.status:
      • "done"           — все этапы done
      • "success_partial" — есть partial/skipped
      • "in_progress"    — если ни один этап не закрыт
    """
    import json as _json
    from pathlib import Path as _P
    from datetime import datetime as _dt

    path = _P(project_dir) / ".ai_link" / "plan.json"
    if not path.exists():
        return
    try:
        data = _json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return

    stages = data.get("stages", []) or []
    _n_done = 0
    _n_partial = 0
    _n_skipped = 0

    _n_rework = 0
    for stage in stages:
        # FIX-REMOVED (2026-10-10): убранные этапы не трогаем.
        if stage.get("origin") == "removed":
            continue
        # Б64: needs_rework НЕ перезаписываем в done при финализации.
        if stage.get("status") == "needs_rework":
            _n_rework += 1
            continue
        items = stage.get("items") or []
        if not items:
            continue
        # FIX-INPROGRESS (2026-10-10): при финализации плана
        # in_progress → done (см. Б34).
        for _it in items:
            if _it.get("status") == "in_progress":
                _it["status"] = "done"
        _st = [it.get("status", "pending") for it in items]
        _done_n = sum(1 for s in _st if s in ("done", "rejected"))
        _pending_n = sum(1 for s in _st if s == "pending")

        if _pending_n == 0:
            stage["status"] = "done"
            _n_done += 1
        elif _done_n == 0:
            stage["status"] = "skipped"
            _n_skipped += 1
        else:
            stage["status"] = "partial"
            _n_partial += 1

    data["finished_at"] = _dt.now().isoformat(timespec="seconds")

    # Б64: если есть needs_rework — план partial, не done.
    if _n_rework > 0:
        data["status"] = "success_partial"
    elif _n_partial == 0 and _n_skipped == 0 and _n_done > 0:
        data["status"] = "done"
    elif _n_done == 0 and _n_partial == 0 and _n_skipped == 0:
        data["status"] = "in_progress"
    else:
        data["status"] = "success_partial"

    logger.info(
        "PLAN-HONEST: done=%d, partial=%d, skipped=%d, rework=%d "
        "→ status=%s",
        _n_done, _n_partial, _n_skipped, _n_rework, data["status"],
    )

    try:
        path.write_text(
            _json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception:
        pass


def _clean_plan_text(raw: str) -> str:
    """Обрезает всё до последнего «Завершено размышление».

    Fallback: если маркер размышления не найден, но в тексте есть
    эхо промпта (наш SYSTEM_PROMPT в начале), отрезаем всё до
    первого «НАЗВАНИЕ:» — это начало реального плана.
    """
    if not raw:
        return ""
    text = raw
    for marker in ["Завершено размышление", "Размышление завершено",
                   "Thinking finished", "Thought for"]:
        idx = text.rfind(marker)
        if idx >= 0:
            nl = text.find("\n", idx)
            if nl != -1:
                text = text[nl + 1:]
            else:
                text = text[idx + len(marker):]
            break
    text = text.strip()

    # Fallback: если в тексте есть «НАЗВАНИЕ:» не с самого начала —
    # значит перед ним эхо промпта. Отрезаем всё до НЕГО.
    #
    # Задача K: Qwen иногда пишет «НАЗВАНИЕ:» дважды — в начале
    # размышления (мусор: «Надо написать НАЗВАНИЕ: ...») и в конце
    # (настоящий план). Берём ПОСЛЕДНЕЕ вхождение — оно финальное.
    if "НАЗВАНИЕ:" in text and not text.startswith("НАЗВАНИЕ:"):
        idx = text.rfind("НАЗВАНИЕ:")
        if idx > 0:
            text = text[idx:].strip()

    # Срез хвостового мусора Qwen Studio — после плана он иногда
    # добавляет свои рассуждения о пользователе.
    for tail_marker in [
        "Пользователь показал отличные результаты",
        "Давай разберу",
    ]:
        idx = text.find(tail_marker)
        if idx > 0:
            text = text[:idx].rstrip()
            break

    return text


def _save_project_plan(project_dir: str, plan_text: str, task: str) -> None:
    """Сохраняет план от фазы plan в <project_dir>/.ai_link/plan.json."""
    import json as _json
    from pathlib import Path as _Path
    from datetime import datetime as _dt
    # B51 (2026-10-12): буквы этапов (А, Б, В...).
    try:
        from core.audit_planner import _letter as _letter_fn
    except Exception:
        def _letter_fn(i):
            _B = "АБВГДЕЖЗИКЛМНОПРСТУФХЦЧШЩЭЮЯ"
            return _B[i] if 0 <= i < len(_B) else f"Э{i+1}"

    # 1) Единая очистка — та же, что для ctx.plan.
    #    Убирает эхо промпта, «Завершено размышление» и хвостовой мусор.
    raw = _clean_plan_text(plan_text)

    out_dir = _Path(project_dir) / ".ai_link"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "plan.json"

    title = ""
    goal = ""
    stages = []
    current_stage = None

    for line in raw.splitlines():
        s = line.strip()
        if not s:
            continue
        upper = s.upper()

        # Пропускаем строки с шаблонными заглушками <...>
        if "<" in s and ">" in s:
            continue

        if upper.startswith("НАЗВАНИЕ:"):
            title = s[len("НАЗВАНИЕ:"):].strip()
            continue
        if upper.startswith("ЦЕЛЬ:"):
            goal = s[len("ЦЕЛЬ:"):].strip()
            continue
        if upper.startswith("ЭТАП:"):
            name = s[len("ЭТАП:"):].strip()
            if name and not name.startswith("<"):
                # B51: letter + origin + status для рендера.
                current_stage = {
                    "letter": _letter_fn(len(stages)),
                    "name": name,
                    "items": [],
                    "origin": "initial",
                    "status": "pending",
                }
                stages.append(current_stage)
            continue

        # Пропускаем разделы ПРАВИЛА и ниже
        if upper.startswith("ПРАВИЛА:"):
            current_stage = None
            continue

        # Подпункт с дефисом
        if s.startswith(("- ", "* ", "• ")):
            body = s[2:].strip()
            if body and current_stage is not None:
                # B51: num для рендера.
                current_stage["items"].append({
                    "num": len(current_stage["items"]) + 1,
                    "text": body[:300],
                    "status": "pending",
                })
            continue

        # Подпункт БЕЗ дефиса — просто текст внутри этапа
        if current_stage is not None:
            # Отсекаем мусор
            if len(s) < 4:
                continue
            # Если это не заголовок и не команда — считаем шагом
            current_stage["items"].append({
                "num": len(current_stage["items"]) + 1,
                "text": s[:300], "status": "pending",
            })

    # Защита: если в ответе plan-фазы нет ни НАЗВАНИЕ, ни ЭТАП, ни ЦЕЛЬ —
    # это не план (эхо промпта или мусор). Не перезаписываем plan.json.
    _has_title = "НАЗВАНИЕ:" in raw
    _has_stage = "ЭТАП:" in raw
    _has_goal = "ЦЕЛЬ:" in raw
    if not (_has_title or _has_stage or _has_goal):
        import logging as _logging
        _logging.getLogger(__name__).warning(
            "plan: пропускаю сохранение — в ответе нет структуры "
            "(нет НАЗВАНИЕ/ЭТАП/ЦЕЛЬ)"
        )
        return

    if not title:
        title = f"Проект: {task[:60]}"
    if not goal:
        goal = task[:200]

    # PLAN-REPLACE (2026-10-01): логика зависит от статуса старого плана.
    #   • status="done" → старая задача завершена, это НОВАЯ работа
    #     → ЗАМЕНЯЕМ план полностью (старые этапы → .stale)
    #   • status="in_progress" → доработка
    #     → ДОПИСЫВАЕМ (origin="added")
    #   • файла нет → первый план
    _existing_stages = []
    _existing_title = title
    _existing_goal = goal
    _existing_created = _dt.now().isoformat(timespec="seconds")
    _existing_user = ""
    _old_status = ""
    if path.exists():
        try:
            _old = _json.loads(path.read_text(encoding="utf-8"))
            _old_status = (_old.get("status") or "").strip()
            _existing_stages = _old.get("stages") or []
            _existing_title = _old.get("title") or title
            _existing_goal = _old.get("goal") or goal
            _existing_created = _old.get("created_at") or _existing_created
            # created_by_user сохраняем из старого плана (если есть).
            _existing_user = _old.get("created_by_user") or ""
            for _st in _existing_stages:
                if "origin" not in _st:
                    _st["origin"] = "initial"
        except Exception:
            _existing_stages = []

    # Определяем: заменять или дописывать
    _should_replace = (
        not _existing_stages
        or _old_status == "done"
    )

    if _should_replace:
        # Новая задача → сохраняем старый план в .stale и заменяем
        if _existing_stages and path.exists():
            try:
                _ts = _dt.now().strftime("%Y%m%d_%H%M%S")
                _stale = path.with_name(f"plan.json.stale_{_ts}")
                path.rename(_stale)
                import logging as _logging
                _logging.getLogger(__name__).info(
                    "PLAN-REPLACE: старый план (status=%s) → %s",
                    _old_status or "?",
                    _stale.name,
                )
            except Exception as _e:
                import logging as _logging
                _logging.getLogger(__name__).warning(
                    "PLAN-REPLACE: не удалось сохранить старый план: %s", _e
                )
        for _st in stages:
            _st["origin"] = "initial"
        _merged_stages = stages
        # Новый план — новый created_at
        _existing_created = _dt.now().isoformat(timespec="seconds")
        # PLAN-REPLACE-TITLE (2026-10-02): при замене title/goal
        # берём НОВЫЕ, а не старые. Иначе в промпт уходит цель
        # от прошлой задачи (баг: Qwen на review находил
        # противоречие «задача vs план»).
        _existing_title = title
        _existing_goal = goal
    else:
        # Доработка → дописываем
        for _st in stages:
            _st["origin"] = "added"
        _merged_stages = _existing_stages + stages

    # A3 (2026-10-07): created_by_user — чтобы различать свои
    # и чужие Link-проекты. Если план уже был — сохраняем прежнего
    # автора (доработка не меняет владельца).
    # A3 (2026-10-07): имя пользователя из config (если задано),
    # иначе — системное.
    _cur_user = ""
    try:
        from config.config_manager import get_setting
        _cur_user = (get_setting("user_name", "") or "").strip()
    except Exception:
        pass
    if not _cur_user:
        try:
            _cur_user = getpass.getuser() or ""
        except Exception:
            _cur_user = ""
    _final_user = _existing_user or _cur_user

    data = {
        "title": _existing_title,
        "goal": _existing_goal,
        "task": task,
        "status": "in_progress",
        "created_at": _existing_created,
        "created_by_user": _final_user,
        "updated_at": _dt.now().isoformat(timespec="seconds"),
        "stages": _merged_stages,
        "raw_plan": raw[:5000],
    }
    try:
        path.write_text(
            _json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception:
        pass



# ─────────────────────────────────────────────────────────────
#  Детектор "новой работы" (2026-09-26)
# ─────────────────────────────────────────────────────────────

# Маркеры новой работы — если в задаче есть хотя бы один,
# и план done → запускаем plan заново для нового плана.
_NEW_WORK_MARKERS = (
    "добавь", "добавить", "создай", "создать", "сделай",
    "сделать", "реализуй", "реализовать", "внедри", "внедрить",
    "напиши", "написать", "построй", "построить",
    "add ", "create ", "make ", "implement ", "build ",
    "добавить функцию", "новая функция", "новый модуль",
    "новую функцию", "новый функционал",
)

# Маркеры "продолжения старой работы" — если есть, продолжаем
# как раньше (review/fix), НЕ создаём новый план.
_CONTINUE_WORK_MARKERS = (
    "проверь", "проверить", "исправь", "исправить", "почини",
    "починить", "fix ", "check ", "доделай", "доделать",
    "продолжи", "продолжить", "verify ", "finish ",
)


def _is_new_work(task: str) -> bool:
    """True, если задача — НОВАЯ работа (создать/добавить),
    а не продолжение старой (проверить/исправить).
    """
    if not task:
        return False
    low = task.lower()
    # Явное "продолжи" побеждает
    for m in _CONTINUE_WORK_MARKERS:
        if m in low:
            return False
    # Маркеры новой работы
    for m in _NEW_WORK_MARKERS:
        if m in low:
            return True
    return False


def _detect_continue_phase(project_dir: str, task: str = ""):
    """PHASES-4: возвращает (phase, stage_idx).

    stage_idx — индекс первого этапа с незавершёнными шагами.
    0 — если все этапы done или данных нет.

    Логика:
      • Плана нет → (None, 0) — обычный путь plan → code
      • План done + кода нет → (PHASE_CODE, first_pending)
      • План done + код есть → (PHASE_REVIEW, first_pending)
      • План не завершён → (None, 0)
    """
    import json as _json
    from pathlib import Path as _P

    # LONG-RUN ч.4: если есть run_state.json с stage_index —
    # он ТОЧНЕЕ, чем plan.json (сохранён после каждой фазы).
    _cp = _load_checkpoint(project_dir)

    # FIX STOP-FIX (2026-09-29): status="stopped" НЕ продолжаем
    # с фазы. STOP обнуляет фазу — следующая задача стартует
    # с plan. PAUSE (status="paused") — продолжаем с фазы.
    if _cp and _cp.get("status") == "stopped":
        logger.info(
            "STOP-FIX: чекпоинт status=stopped → старт с plan "
            "(фаза игнорируется)"
        )
        return (PHASE_PLAN, 0)

    if _cp and _cp.get("status") in ("paused", "running"):
        _cp_phase = _cp.get("phase") or {}
        _cp_cur = _cp_phase.get("current") or ""
        _cp_si = _cp_phase.get("stage_index") or 0
        # SYNC-STAGE (2026-10-01): сверяем run_state.stage_index с
        # РЕАЛЬНЫМ состоянием plan.json. Если run_state отстаёт
        # (этапы уже done, а stage_index старый) — поднимаем до
        # первого незакрытого. Чинит баг 30.09 (2 часа на 2 кнопки).
        try:
            _sync_plan_path = _P(project_dir) / ".ai_link" / "plan.json"
            if _sync_plan_path.exists():
                _sync_data = _json.loads(
                    _sync_plan_path.read_text(encoding="utf-8")
                )
                _sync_stages = _sync_data.get("stages") or []
                _first_pending = None
                for _si, _st in enumerate(_sync_stages):
                    _items = _st.get("items") or []
                    if any(
                        (it.get("status") or "pending") != "done"
                        for it in _items
                    ):
                        _first_pending = _si
                        break
                if (
                    _first_pending is not None
                    and _cp_si < _first_pending
                ):
                    logger.info(
                        "SYNC-STAGE: run_state отстаёт "
                        "(stage_index=%d, реальный первый "
                        "незакрытый=%d) — поднимаю до %d",
                        _cp_si, _first_pending, _first_pending,
                    )
                    _cp_si = _first_pending
        except Exception as _e:
            logger.warning("SYNC-STAGE: сверка не удалась: %s", _e)
        # Определяем фазу по имени
        _phase_map = {
            "plan": PHASE_PLAN,
            "code": PHASE_CODE,
            "review": PHASE_REVIEW,
            "fix": PHASE_FIX,
        }
        _detected = _phase_map.get(_cp_cur)
        if _detected:
            # PHASE-RESET (2026-10-09): если plan.json имеет pending,
            # а чекпоинт — review/fix, стартуем заново с code на
            # первом незакрытом этапе. Иначе оркестратор крутится в
            # review, план не двигается.
            if _cp_cur in ("review", "fix"):
                try:
                    _sync2 = _P(project_dir) / ".ai_link" / "plan.json"
                    if _sync2.exists():
                        _sd2 = _json.loads(
                            _sync2.read_text(encoding="utf-8"))
                        _stages2 = _sd2.get("stages") or []
                        # Смотрим items ТОЛЬКО этапа из чекпоинта.
                        # Если там всё ещё pending — code не выполнялся,
                        # сбрасываем на code. Если done/in_progress —
                        # продолжаем review/fix как и было.
                        if 0 <= _cp_si < len(_stages2):
                            _its_cp = (_stages2[_cp_si].get("items")
                                       or [])
                            _all_pending = bool(_its_cp) and all(
                                (it.get("status") or "pending")
                                == "pending"
                                for it in _its_cp
                            )
                            if _all_pending:
                                logger.info(
                                    "PHASE-RESET: чекпоинт=%s, но items "
                                    "этапа %d все pending — старт с code",
                                    _cp_cur, _cp_si + 1,
                                )
                                return (PHASE_CODE, _cp_si)
                except Exception as _e2:
                    logger.warning("PHASE-RESET: %s", _e2)
            logger.info(
                "LONG-RUN ч.4: продолжаю из чекпоинта — "
                "фаза=%s, этап=%d",
                _detected, _cp_si + 1,
            )
            return (_detected, _cp_si)

    plan_path = _P(project_dir) / ".ai_link" / "plan.json"
    if not plan_path.exists():
        return (None, 0)

    try:
        plan = _json.loads(plan_path.read_text(encoding="utf-8"))
    except Exception:
        return (None, 0)

    # Считаем «файлы в проекте» — игнорируем .ai_link и служебное.
    base = _P(project_dir)
    code_files = []
    if base.exists():
        for f in base.rglob("*"):
            if not f.is_file():
                continue
            if ".ai_link" in f.parts:
                continue
            if "__pycache__" in f.parts:
                continue
            if f.suffix in (".py", ".js", ".txt", ".md", ".json", ".html", ".css"):
                code_files.append(f)
    has_code = len(code_files) > 0

    plan_status = plan.get("status", "in_progress")
    stages = plan.get("stages") or []

    # PHASES-4: индекс первого этапа с незавершёнными шагами
    _first_pending = 0
    for i, stage in enumerate(stages):
        items = stage.get("items", [])
        if any(it.get("status") != "done" for it in items):
            _first_pending = i
            break

    if plan_status == "done" and not has_code:
        return (PHASE_CODE, _first_pending)

    # B50 (2026-10-12): если план done — ЛЮБАЯ задача = новая работа,
    # кроме явного "продолжи"/"доделай". Раньше "исправь X" ловилось
    # маркером "исправь" из _CONTINUE_WORK_MARKERS → Link думал
    # "продолжение" → крутил старый план в review/fix.
    if plan_status == "done" and has_code:
        _low = (task or "").lower()
        _explicit_continue = any(
            m in _low for m in (
                "продолжи", "продолжить", "доделай", "доделать",
                "продолжаем", "продолжай",
            )
        )
        if not _explicit_continue:
            logger.info(
                "B50: план done → старт с plan (задача=%r)",
                (task or "")[:80],
            )
            return (PHASE_PLAN, 0)
        if _is_new_work(task):
            logger.info(
                "continue_project: план done, но задача — НОВАЯ РАБОТА "
                "→ запускаю plan заново для нового плана"
            )
            return (PHASE_PLAN, 0)
        return (PHASE_REVIEW, _first_pending)

    # FIX #15: план есть, но status=in_progress.
    # Если этапы есть и хотя бы один не-done — продолжаем
    # с code с первого незавершённого этапа. НЕ начинаем
    # с plan заново (план уже составлен).
    if plan_status == "in_progress" and stages and has_code:
        logger.info(
            "LONG-RUN ч.15: план есть (in_progress), "
            "продолжаем с code (этап %d)",
            _first_pending + 1,
        )
        return (PHASE_CODE, _first_pending)

    return (None, 0)


def _verify_target(target: dict, project_dir: str) -> Dict[str, Any]:
    """B+ (19.10.2026): проверяет ОДИН target через acceptance[].

    target:
      {
        "id": "P0-1",
        "acceptance": [
          {"type": "grep_absent", "pattern": "...", "glob": "**/*.py"},
          {"type": "grep_present", "pattern": "...", "path": "config.py"},
          {"type": "file_exists", "path": ".gitignore"},
          {"type": "python_compile", "path": "database.py"},
        ]
      }

    Возвращает:
      {"ok": True,  "verifier": "code", "reason": ""}
      {"ok": False, "verifier": "code", "reason": "..."}

    Все checks в acceptance[] должны пройти (AND-семантика).
    Если acceptance[] нет — пробуем старую схему pattern/check_kind.
    """
    import re as _re
    import fnmatch as _fnmatch
    import py_compile as _py_compile
    from pathlib import Path as _P

    tid = (target.get("id") or "?") if isinstance(target, dict) else "?"
    base = _P(project_dir)
    checks = (target.get("acceptance") or []) if isinstance(target, dict) else []
    if not checks:
        return {"ok": False, "verifier": "code",
                "reason": f"{tid}: пустой acceptance[]"}

    for ch in checks:
        if not isinstance(ch, dict):
            return {"ok": False, "verifier": "code",
                    "reason": f"{tid}: check не dict"}
        ctype = (ch.get("type") or "").strip()
        pattern = ch.get("pattern") or ""
        glob_pat = ch.get("glob") or ""
        path_ = ch.get("path") or ""

        # === grep_absent ===
        if ctype == "grep_absent":
            if not pattern:
                return {"ok": False, "verifier": "code",
                        "reason": f"{tid}: grep_absent без pattern"}
            files = _iter_files(base, glob_pat or "**/*.py")
            for fp in files:
                try:
                    text = fp.read_text(encoding="utf-8", errors="replace")
                except Exception:
                    continue
                try:
                    if _re.search(pattern, text, _re.MULTILINE):
                        return {
                            "ok": False, "verifier": "code",
                            "reason": (
                                f"{tid}: grep_absent — pattern {pattern!r} "
                                f"найден в {fp.name}"
                            ),
                        }
                except _re.error as _e:
                    return {"ok": False, "verifier": "code",
                            "reason": f"{tid}: битый regex: {_e}"}
            continue

        # === grep_present ===
        if ctype == "grep_present":
            if not pattern:
                return {"ok": False, "verifier": "code",
                        "reason": f"{tid}: grep_present без pattern"}
            if path_:
                files = [base / path_]
            else:
                files = _iter_files(base, glob_pat or "**/*.py")
            found = False
            for fp in files:
                if not fp.exists():
                    continue
                try:
                    text = fp.read_text(encoding="utf-8", errors="replace")
                except Exception:
                    continue
                try:
                    if _re.search(pattern, text, _re.MULTILINE):
                        found = True
                        break
                except _re.error as _e:
                    return {"ok": False, "verifier": "code",
                            "reason": f"{tid}: битый regex: {_e}"}
            if not found:
                return {
                    "ok": False, "verifier": "code",
                    "reason": (
                        f"{tid}: grep_present — pattern {pattern!r} "
                        f"не найден"
                    ),
                }
            continue

        # === file_exists ===
        if ctype == "file_exists":
            if not path_:
                return {"ok": False, "verifier": "code",
                        "reason": f"{tid}: file_exists без path"}
            fp = base / path_
            if not fp.exists():
                return {
                    "ok": False, "verifier": "code",
                    "reason": f"{tid}: file_exists — {path_} не существует",
                }
            continue

        # === python_compile ===
        if ctype == "python_compile":
            if not path_:
                return {"ok": False, "verifier": "code",
                        "reason": f"{tid}: python_compile без path"}
            fp = base / path_
            if not fp.exists():
                return {
                    "ok": False, "verifier": "code",
                    "reason": f"{tid}: python_compile — {path_} не существует",
                }
            try:
                _py_compile.compile(str(fp), doraise=True)
            except _py_compile.PyCompileError as _e:
                return {
                    "ok": False, "verifier": "code",
                    "reason": f"{tid}: python_compile — {_e.msg[:200]}",
                }
            except Exception as _e:
                return {
                    "ok": False, "verifier": "code",
                    "reason": f"{tid}: python_compile — {str(_e)[:200]}",
                }
            continue

        # === неизвестный тип ===
        return {"ok": False, "verifier": "code",
                "reason": f"{tid}: неизвестный type={ctype!r}"}

    return {"ok": True, "verifier": "code", "reason": ""}


def _iter_files(base, glob_pat: str):
    """Итератор файлов по glob-паттерну (с исключением .ai_link,
    __pycache__, .git)."""
    from pathlib import Path as _P
    try:
        for fp in _P(base).glob(glob_pat):
            if not fp.is_file():
                continue
            parts = set(fp.parts)
            if ".ai_link" in parts or "__pycache__" in parts:
                continue
            if ".git" in parts:
                continue
            yield fp
    except Exception:
        return


def _compute_research_p0_status(project_dir: str) -> str:
    """B (18.10.2026): проверяет P0 из research_notes.json.

    Возвращает:
      "fulfilled"      — все targets выполнены.
      "not_fulfilled"  — хоть один P0 не выполнен.
      "unknown"        — файла нет / targets пусто / ошибка.
    """
    import json as _json
    import re as _re
    from pathlib import Path as _P
    try:
        path = _P(project_dir) / ".ai_link" / "research_notes.json"
        if not path.exists():
            return "unknown"
        data = _json.loads(path.read_text(encoding="utf-8"))
        targets = data.get("targets") or []
        if not targets:
            return "unknown"
        base = _P(project_dir)
        for t in targets:
            if not isinstance(t, dict):
                continue
            # B+ (19.10.2026): если есть acceptance[] — используем
            # детерминированный _verify_target. Иначе — старая схема
            # pattern/check_kind (совместимость).
            if t.get("acceptance"):
                _v = _verify_target(t, project_dir)
                if not _v.get("ok"):
                    logger.warning(
                        "RESEARCH-ACCEPT: %s — NOT DONE (%s)",
                        t.get("id") or "?",
                        (_v.get("reason") or "")[:200],
                    )
                    return "not_fulfilled"
                continue
            file_ = (t.get("file") or "").strip()
            pattern = (t.get("pattern") or "").strip()
            check_kind = (t.get("check_kind") or "contains").strip()
            tid = t.get("id") or "?"
            if not file_ or not pattern:
                continue
            fpath = base / file_
            if not fpath.exists():
                logger.warning(
                    "RESEARCH-ACCEPT: %s — файл не найден: %s",
                    tid, file_,
                )
                return "not_fulfilled"
            try:
                text = fpath.read_text(encoding="utf-8", errors="replace")
            except Exception as _e:
                logger.warning(
                    "RESEARCH-ACCEPT: %s — не читается %s: %s",
                    tid, file_, _e,
                )
                return "not_fulfilled"
            try:
                found = _re.search(pattern, text, _re.MULTILINE) is not None
            except Exception as _e:
                logger.warning(
                    "RESEARCH-ACCEPT: %s — битый regex %r: %s",
                    tid, pattern, _e,
                )
                return "not_fulfilled"
            if check_kind == "not_contains":
                if found:
                    logger.warning(
                        "RESEARCH-ACCEPT: %s — NOT DONE "
                        "(pattern найден в %s: %r)",
                        tid, file_, pattern,
                    )
                    return "not_fulfilled"
            else:  # contains
                if not found:
                    logger.warning(
                        "RESEARCH-ACCEPT: %s — NOT DONE "
                        "(pattern не найден в %s: %r)",
                        tid, file_, pattern,
                    )
                    return "not_fulfilled"
        logger.info(
            "RESEARCH-ACCEPT: все P0 выполнены (%d targets)",
            len(targets),
        )
        return "fulfilled"
    except Exception as _e:
        logger.warning("RESEARCH-ACCEPT: ошибка: %s", _e)
        return "unknown"


def _compute_acceptance(rcpt: Optional[Dict[str, Any]],
                        project_dir: str = "") -> str:
    """ACCEPTANCE-GATE (2026-10-07): определяет статус принятия этапа.

    Возвращает:
      "accepted"             — VERIFY=verified, всё зелёное.
      "accepted_with_debt"   — partial, но не critical → можно дальше.
      "blocked"              — failed syntax/imports/tests → провал.
      "unknown"              — нет данных.

    Логика:
      • syntax=failed        → blocked (файлы не компилируются).
      • imports=failed       → blocked (битые импорты).
      • tests=failed (N>0)   → blocked (тесты падают).
      • status=verified      → accepted.
      • status=partial       → accepted_with_debt.
      • иначе                → unknown.

    B (18.10.2026): добавлена проверка P0 из research_notes.json.
      • Все P0 выполнены / файла нет / targets пусто → старая логика.
      • Хоть один P0 не выполнен → "blocked" (независимо от rcpt).
    """
    # B: P0-проверка — приоритетнее, чем syntax/tests.
    if project_dir:
        _p0 = _compute_research_p0_status(project_dir)
        if _p0 == "not_fulfilled":
            return "blocked"
    if not rcpt:
        return "unknown"
    status = (rcpt.get("status") or "").lower()
    syntax = (rcpt.get("syntax") or {}).get("status") or ""
    imports = (rcpt.get("imports") or {}).get("status") or ""
    tests_d = rcpt.get("tests") or {}
    tests = tests_d.get("status") or ""
    failed = int(tests_d.get("failed") or 0)

    if syntax == "failed" or imports == "failed":
        return "blocked"
    if tests == "failed" and failed > 0:
        return "blocked"
    if status == "verified":
        return "accepted"
    if status == "partial":
        return "accepted_with_debt"
    return "unknown"


def _call_brain_by_role(prompt: str) -> Dict[str, Any]:
    """APOCALYPSE (2026-10-07): универсальный вызов brain по РОЛИ.

    Берёт назначение для роли "brain", определяет тип агента
    (browser / api / local) и вызывает адаптер. Никаких имён
    моделей в коде — только роль.
    """
    try:
        from core.capability_benchmark import get_assigned_agent
        from core.agent_profiles import get_agent_profile
    except Exception as e:
        return {"success": False, "text": "", "error": f"import: {e}"}

    agent = get_assigned_agent("brain")
    if not agent:
        return {"success": False, "text": "",
                "error": "роль brain не назначена"}

    prof = get_agent_profile(agent) or {}
    ptype = (prof.get("type") or "").lower()
    if not ptype:
        if agent.startswith("browser_"):
            ptype = "browser"
        elif agent.startswith("ollama"):
            ptype = "local"
        else:
            ptype = "api"

    try:
        if ptype == "browser":
            site = agent.replace("browser_", "", 1)
            from core.agents.browser_agent import query_browser_ai
            r = query_browser_ai(site, prompt)
        elif ptype == "api":
            from core.gigachat_adapter import query_gigachat
            r = query_gigachat(prompt, chat_mode=True)
        elif ptype == "local":
            from core.ollama_adapter import query_ollama
            r = query_ollama(prompt)
        else:
            return {"success": False, "text": "",
                    "error": f"неизвестный тип агента: {ptype}"}
        return {
            "success": bool(r.get("success")),
            "text": r.get("text", "") or "",
            "error": (r.get("error") or "")[:200],
        }
    except Exception as e:
        return {"success": False, "text": "", "error": str(e)[:200]}


def _brain_apocalypse_decide(ctx, phase_result,
                              max_review_chars: int = 2500
                              ) -> Optional[Dict[str, Any]]:
    """APOCALYPSE: brain вызывает LLM для решения после fix×2.

    Собирает богатый state (задача + план + review + ошибки)
    и просит brain решить: продолжать / сменить подход / стоп.
    """
    try:
        from core.brain import decide as _bd
        from core.repo_map import count_py_files as _cpf
    except Exception as _e:
        logger.warning("APOCALYPSE: import error: %s", _e)
        return None

    _stages = (getattr(ctx, "plan", {}) or {}).get("stages") or []
    _review_text = ""
    try:
        _review_text = _collect_last_review_text(
            ctx, max_chars=max_review_chars
        )
    except Exception:
        pass

    _errors_tail = "\n".join(
        str(e)[:300] for e in (ctx.errors or [])[-3:]
    )

    _state = {
        "phase": "fix",
        "phase_status": "failed",
        "files_created": _cpf(ctx.project_dir),
        "fix_cycles": 2,
        "has_stages": bool(_stages),
        "stage_index": int(
            getattr(ctx, "current_stage_index", 0) or 0
        ),
        "total_stages": len(_stages),
        "task": (ctx.original_task or "")[:400],
        "review_text": _review_text,
        "errors_tail": _errors_tail,
    }

    try:
        _d = _bd(
            _state,
            use_llm=True,
            qwen_caller=_call_brain_by_role,
        )
    except Exception as _e:
        logger.warning("APOCALYPSE: decide error: %s", _e)
        return None

    try:
        log_event(event_type="brain_apocalypse", payload={
            "decision": _d.get("decision"),
            "reason": _d.get("reason"),
            "confidence": _d.get("confidence"),
            "mode": _d.get("mode"),
            "requires_user": _d.get("requires_user"),
        })
    except Exception:
        pass

    return _d


def _brain_decide_for_transition(ctx, phase: str, phase_result,
                                  phase_status: str = "",
                                  extra_state: Optional[Dict[str, Any]] = None
                                  ) -> Optional[Dict[str, Any]]:
    """BRAIN-SWITCH: спрашивает brain о следующем шаге.

    Возвращает решение brain (dict) или None, если brain не смог.
    Логирует решение как brain_decision (не shadow).
    """
    try:
        from core.brain import decide as _bd
        from core.repo_map import count_py_files as _cpf
        from pathlib import Path as _Pb
        _stages = (getattr(ctx, "plan", {}) or {}).get("stages") or []
        # BRAIN-PLAN (2026-10-09): fallback — читаем plan.json с диска.
        # Иначе ctx.plan пустой → total_stages=0 → brain думает
        # «план выполнен» и сразу finish.
        if not _stages:
            try:
                _pp = _Pb(ctx.project_dir) / ".ai_link" / "plan.json"
                if _pp.exists():
                    import json as _jb
                    _pd = _jb.loads(_pp.read_text(encoding="utf-8"))
                    _stages = _pd.get("stages") or []
            except Exception:
                _stages = []
        # FILES-REAL (2026-10-09): считаем только .py, изменённые
        # в этом прогоне (по mtime). Иначе count_py_files вернёт
        # 44 файла старого проекта → brain решит «работа сделана».
        _files_recent = 0
        try:
            import time as _tb
            from datetime import datetime as _dtb
            _t0 = 0.0
            _ctx_t0 = (getattr(ctx, "started_at", "") or
                       getattr(ctx, "run_started_at", ""))
            if _ctx_t0:
                try:
                    _t0 = _dtb.fromisoformat(str(_ctx_t0)).timestamp()
                except Exception:
                    _t0 = 0.0
            if _t0 <= 0:
                _t0 = _tb.time() - 4 * 3600
            _base_b = _Pb(ctx.project_dir)
            for _f in _base_b.rglob("*.py"):
                if ".ai_link" in _f.parts or "__pycache__" in _f.parts:
                    continue
                try:
                    if _f.stat().st_mtime > _t0:
                        _files_recent += 1
                except Exception:
                    pass
        except Exception:
            _files_recent = _cpf(ctx.project_dir)
        _state = {
            "phase": phase,
            "phase_status": phase_status or (phase_result.status or ""),
            "files_created": _files_recent,
            "iterations": int(getattr(phase_result, "iterations", 0) or 0),
            "fix_cycles": 0,
            "has_stages": bool(_stages),
            "stage_index": int(getattr(ctx, "current_stage_index", 0) or 0),
            "total_stages": len(_stages),
        }
        if extra_state:
            _state.update(extra_state)
        _d = _bd(_state, use_llm=False)
        try:
            from core.event_log import log_event
            log_event(event_type="brain_decision", payload={
                "phase": phase,
                "decision": _d.get("decision"),
                "reason": _d.get("reason"),
                "confidence": _d.get("confidence"),
                "mode": _d.get("mode"),
                "requires_user": _d.get("requires_user"),
                "next_phase": _d.get("next_phase"),
            })
        except Exception:
            pass
        return _d
    except Exception as _e:
        logger.warning("BRAIN-SWITCH: decide error: %s", _e)
        return None


def orchestrate(task: str, project_dir: str,
                max_phases: int = 100,       # HARD_LIMIT
                timeout: float = 14400.0,    # 4 часа (было 30 мин)
                phase_overrides: Optional[Dict[str, str]] = None,
                progress_callback: Optional[Any] = None,
                cross_verify: bool = True,
                should_stop: Optional[Any] = None,
                user_answers: Optional[List[Dict[str, str]]] = None,
                resume_from: str = "",
                continue_project: bool = False,
                force_new: bool = False,
                check_checkpoint: bool = False,
                brain_switch: bool = False) -> OrchestrationResult:
    """Запускает задачу по цепочке фаз.

    Логика переходов:
        plan      success → code
        code      success → review
        review    ok      → success (готово)
        review    problems → fix
        fix       success → review
        fix       fail 2 раза → fallback
        fallback  success → review
        fallback  fail    → стоп, ошибка
    """
    import time as _time
    from datetime import datetime as _dt

    start = _time.time()
    # LONG-RUN: уникальный run_id и старт для чекпоинтов
    _proj_name = project_dir.rstrip("/").split("/")[-1] or "project"
    _run_id = f"{_dt.now().strftime('%Y%m%d_%H%M%S')}_{_proj_name}"
    # EVENT-LOG: контекст прогона + старт
    set_run_context(run_id=_run_id, project_dir=project_dir)
    log_event(event_type="run_started", payload={
        "task": task[:300],
        "project_dir": project_dir,
        "resume_from": resume_from or "",
        "continue_project": bool(continue_project),
        "force_new": bool(force_new),
    })

    # ROLES-CONFLICT (A.1, 2026-10-04): проверяем конфликты ролей.
    # brain/dialog — монополисты. Совпадают с рабочей ролью — прерываем.
    from core.capability_benchmark import check_role_conflicts
    _conf = check_role_conflicts()
    if _conf.get("blocking"):
        _msg = "Конфликт ролей (ROLES-CONFLICT): " + "; ".join(_conf["blocking"])
        logger.error(_msg)
        log_event(event_type="role_conflict_blocking", payload={
            "blocking": _conf["blocking"],
            "warnings": _conf.get("warnings", []),
        })
        _r = OrchestrationResult(success=False, status="failed")
        _r.error = _msg
        return _r
    for _w in _conf.get("warnings", []):
        logger.warning("ROLES-CONFLICT: %s", _w)

    # STALE-STATE (2026-10-01): если чекпоинт от ДРУГОЙ задачи -
    # сбрасываем его и plan.json, чтобы не подхватить старые
    # этапы. Чинит баг 2 часа на 2 кнопки (30.09.2026).
    # Б59: force_new=True из CLI/GUI означает «начать с нуля» —
    # старый план и run_state уходят в stale, даже если task совпал.
    if _reset_stale_state(project_dir, task, force=force_new):
        continue_project = False
        force_new = True

    # Б63 Уровень A (14.10.2026): проверяем, изменился ли проект
    # с прошлого прогона. Если да — история устарела, архивируем
    # и сбрасываем done → needs_recheck. Это защита от ложного
    # success: LLM видит записи «работа сделана», а файлы откатили.
    _proj_changed = False
    try:
        from core.project_fingerprint import (
            compare as _fp_compare, save as _fp_save,
        )
        from core.history_archive import on_project_changed as _on_changed
        _fp = _fp_compare(project_dir)
        if _fp.get("first_run"):
            # Первый прогон — snapshot сохраним В КОНЦЕ.
            # Иначе при следующем старте увидим «изменения»,
            # которые на самом деле сделал сам Link.
            logger.info(
                "PROJECT-SNAPSHOT: первый прогон — снимок будет "
                "сохранён в конце (%d файлов, root=%s)",
                len(_fp["snapshot"].get("files", {})),
                (_fp.get("root_hash") or "")[:12],
            )
        elif _fp.get("changed"):
            _proj_changed = True
            logger.warning(
                "PROJECT-CHANGED: проект изменился с прошлого прогона "
                "(added=%d, removed=%d, modified=%d, git_rollback=%s)",
                len(_fp.get("added", [])),
                len(_fp.get("removed", [])),
                len(_fp.get("modified", [])),
                _fp.get("git_rolled_back"),
            )
            _rep = _on_changed(project_dir, _fp)
            logger.warning(
                "PROJECT-CHANGED: archived=%d, done→needs_recheck=%d, "
                "run_state_stale=%s",
                len(_rep.get("archived", [])),
                _rep.get("needs_recheck", 0),
                _rep.get("run_state_stale", False),
            )
            _fp_save(project_dir, _fp["snapshot"])
        else:
            logger.info(
                "PROJECT-SNAPSHOT: без изменений (root=%s)",
                (_fp.get("root_hash") or "")[:12],
            )
    except Exception as _fpe:
        logger.debug("PROJECT-SNAPSHOT: %s", _fpe)

    # LONG-RUN ч.3: если есть незавершённый чекпоинт — не начинать
    # новую задачу, а вернуть флаг has_checkpoint. Пользователь
    # в GUI решит: продолжить или запустить заново (force_new=True).
    _cp = _load_checkpoint(project_dir) if check_checkpoint else {}
    if (
        _cp
        and not force_new
        and not resume_from
        and not continue_project
        and _cp.get("status") in ("paused", "running")
    ):
        _cp_phase = (_cp.get("phase") or {})
        logger.info(
            "LONG-RUN: найден чекпоинт (статус=%s, фаза=%s, "
            "этап=%d/%d). Возвращаю has_checkpoint.",
            _cp.get("status"),
            _cp_phase.get("current"),
            (_cp_phase.get("stage_index") or 0) + 1,
            _cp_phase.get("total_stages") or 0,
        )
        _r = OrchestrationResult(
            success=False, status="has_checkpoint"
        )
        _r.error = "Найдена незавершённая задача"
        _r.checkpoint = _cp
        return _r

    ctx = create_context(task, project_dir, max_phases=max_phases)
    # Если пришли ответы пользователя — добавляем их в context
    if user_answers:
        ctx.user_answers = list(user_answers)
    result = OrchestrationResult(success=False, status="unknown")

    # KEEP-AWAKE: управляется из main.py (BUG-KEEP-AWAKE-PERSISTENT,
    # 2026-09-28). Оркестратор больше НЕ стартует/стопает держатель.
    # Это нужно, чтобы ПК не засыпал, пока приложение открыто,
    # даже если прогон уже закончился (или упал).
    _keep_awake_proc = None  # оставлено для совместимости

    logger.info("=" * 60)
    logger.info("orchestrate: старт задачи (%d симв.), проект=%s",
                len(task), project_dir)
    if resume_from:
        logger.info("orchestrate: возобновление с фазы %s", resume_from)
    if user_answers:
        logger.info("orchestrate: с ответами пользователя (%d)",
                    len(user_answers))

    # AJ2: считаем ПОВТОРЫ, а не количество.
    # Пользователь может отвечать на много РАЗНЫХ вопросов
    # (обсуждение проекта). Но если Link повторяет один и тот же
    # вопрос — это цикл, надо остановить.
    _past_questions = set()
    if user_answers:
        for _qa in user_answers:
            _q = (_qa.get("question", "") or "").strip().lower()[:200]
            if _q:
                _past_questions.add(_q)
    _clarification_attempts = len(user_answers) if user_answers else 0
    # Жёсткий лимит сверху — защита от бесконечного цикла
    # разных вопросов (бывает редко).
    _max_clarifications = 10

    # PHASES-4: индекс этапа для continue_project (0 по умолчанию).
    _resume_stage_index = 0

    # Если указана фаза возобновления — начинаем с неё
    if resume_from and is_valid_phase(resume_from):
        current_phase = resume_from
    elif continue_project:
        # Задача T: осознанное продолжение.
        # Читаем plan.json и определяем фазу + этап.
        detected, detected_stage = _detect_continue_phase(project_dir, task)
        if detected:
            current_phase = detected
            _resume_stage_index = detected_stage or 0
            logger.info(
                "orchestrate: continue_project — продолжаем с фазы %s "
                "(этап %d)",
                detected, _resume_stage_index + 1,
            )
        else:
            # Плана нет или не завершён — обычный путь с plan
            current_phase = PHASE_PLAN
            logger.info(
                "orchestrate: continue_project — плана нет, "
                "начинаем с plan",
            )
    else:
        current_phase = PHASE_PLAN

    # CHAT-LIFECYCLE: сбрасываем чат агента plan-фазы,
    # чтобы он не тащил контекст прошлой задачи.
    # FIX 2026-09-28: сброс нужен ТАКЖЕ когда:
    #   • новая задача (не continue, не resume)
    #   • continue_project, НО план done → стартуем plan заново
    #     (иначе Qwen видит старое сообщение review в DOM)
    _is_new_task = (not resume_from) and (not continue_project)
    _need_reset_for_plan = _is_new_task
    if continue_project and current_phase == PHASE_PLAN:
        _need_reset_for_plan = True
    if _need_reset_for_plan:
        try:
            _plan_agent = get_phase_agent(PHASE_PLAN)
            _reset_browser_for_agent(_plan_agent)
            logger.info(
                "CHAT-LIFECYCLE: сброс для plan-фазы "
                "(new_task=%s, continue+plan=%s)",
                _is_new_task,
                continue_project and current_phase == PHASE_PLAN,
            )
        except Exception:
            pass

    consecutive_failures: Dict[str, int] = {}
    plan_retries = 0
    fix_count = 0
    max_same_failures = 2
    max_fix_cycles = 2
    # STAGE-IN-PROGRESS (2026-10-07): чтобы не помечать один этап
    # по несколько раз.
    _last_marked_stage = -1
    pending_review: Optional[Dict[str, Any]] = None  # для логирования

    # PHASES-2: флаг «работаем по этапам». Включается, если
    # план большой (≥3 этапов или ≥10 шагов). Маленькие проекты
    # идут одним проходом code → review → fix.
    _use_stages = False
    _total_stages = 0
    # PHASES-STARTUP (2026-10-09): если plan.json уже есть на диске —
    # подтягиваем этапы. Иначе при старте с code/fix (PHASE-RESET)
    # _use_stages остаётся False и переходы между этапами не работают.
    try:
        _us, _ts, _rec_max = _load_plan_stages(ctx, reset_index=False)
        if _ts > 0:
            _use_stages = _us
            _total_stages = _ts
            if _rec_max > max_phases:
                max_phases = _rec_max
                logger.info(
                    "PHASES-STARTUP: max_phases поднят до %d "
                    "(этапов: %d)", max_phases, _total_stages,
                )
            logger.info(
                "PHASES-STARTUP: план с диска — %d этапов, use_stages=%s",
                _total_stages, _use_stages,
            )
    except Exception as _e:
        logger.warning("PHASES-STARTUP: %s", _e)

    # PHASES-4: применим вычисленный этап (continue_project).
    if _resume_stage_index > 0:
        ctx.current_stage_index = _resume_stage_index
        logger.info(
            "PHASES-4: продолжаем с этапа %d",
            _resume_stage_index + 1,
        )

    # FIX №9: range(max_phases) вычисляется ОДИН раз при входе.
    # Динамический лимит (после plan-фазы) не мог увеличить цикл.
    # Теперь range(HARD_LIMIT), а внутри проверяем step >= max_phases.
    _HARD_LIMIT = 1000   # абсолютный максимум (защита от беды)

    # B (18.10.2026): research запускается ОДИН раз за прогон,
    # перед первым write-этапом (code). После — флаг True.
    _research_done = False

    # PROGRESS (2026-10-10): счётчик фаз БЕЗ прогресса.
    # Прогресс = выросло число done+rejected items ИЛИ stage_index.
    # При прогрессе — сбрасываем и продлеваем max_phases.
    # Стоп при 10 фазах подряд без прогресса (защита от зацикливания).
    _PROGRESS_LIMIT = 10
    _phases_without_progress = 0
    _last_done_count = _count_done_items(project_dir)
    _last_stage_index = int(getattr(ctx, "current_stage_index", 0) or 0)

    for step in range(_HARD_LIMIT):
        # === PROGRESS (2026-10-10): проверка прогресса ===
        _cur_done = _count_done_items(project_dir)
        _cur_stage = int(getattr(ctx, "current_stage_index", 0) or 0)
        if (_cur_done > _last_done_count
                or _cur_stage > _last_stage_index):
            # Прогресс есть — сбрасываем счётчик, продлеваем лимит.
            _phases_without_progress = 0
            _last_done_count = _cur_done
            _last_stage_index = _cur_stage
            max_phases += 3
            logger.debug(
                "PROGRESS: шаг %d, done=%d, stage=%d, "
                "max_phases продлён до %d",
                step, _cur_done, _cur_stage, max_phases,
            )
        else:
            _phases_without_progress += 1

        if _phases_without_progress >= _PROGRESS_LIMIT:
            result.status = "no_progress"
            result.error = (
                f"нет прогресса {_PROGRESS_LIMIT} фаз подряд "
                f"(done={_cur_done}, stage={_cur_stage})"
            )
            logger.info(
                "orchestrate: стоп по PROGRESS (шаг %d, "
                "без прогресса %d фаз)",
                step, _phases_without_progress,
            )
            break

        # FIX №9: динамический лимит проверяется КАЖДУЮ итерацию
        if step >= max_phases:
            result.status = "max_phases"
            result.error = f"достигнут лимит фаз ({max_phases})"
            logger.info(
                "orchestrate: max_phases достигнут на шаге %d",
                step,
            )
            break

        # Проверка остановки пользователем
        if should_stop is not None:
            try:
                if should_stop():
                    result.status = "stopped"
                    result.error = "остановлено пользователем"
                    break
            except Exception:
                pass

        if _time.time() - start > timeout:
            result.status = "timeout"
            result.error = f"превышен таймаут оркестрации ({timeout}s)"
            break

        # STAGE-IN-PROGRESS (2026-10-07): при старте code-фазы
        # помечаем все pending-items текущего этапа как in_progress.
        # Дерево сразу показывает, что этап идёт.
        if current_phase in (PHASE_CODE, PHASE_FIX) and _use_stages:
            try:
                _si = int(getattr(ctx, "current_stage_index", 0) or 0)
                if _si != _last_marked_stage:
                    _mark_stage_in_progress(ctx.project_dir, _si)
                    _last_marked_stage = _si
            except Exception as _e:
                logger.debug("STAGE-IN-PROGRESS: %s", _e)

        # Запускаем фазу
        agent_override = ""
        if phase_overrides and current_phase in phase_overrides:
            agent_override = phase_overrides[current_phase]

        _phase_agent = agent_override or get_phase_agent(current_phase)
        # LOG-PHASE (2026-10-01): переименовано "шаг" → "фаза",
        # чтобы не путать с шагами плана внутри этапа.
        logger.info("orchestrate: фаза %d/%d — %s, агент %s",
                    step + 1, max_phases, current_phase, _phase_agent)

        # Задача R: fix_count инкрементируется при КАЖДОМ запуске fix,
        # а не только при успехе. Иначе цикл review→fix не сходится.
        if current_phase == PHASE_FIX:
            fix_count += 1
            logger.info("orchestrate: fix_count → %d (max=%d)",
                        fix_count, max_fix_cycles)

        # LONG-RUN ч.4b: синхронизируем ctx.current_phase
        # (иначе чекпоинт пишет пустую фазу).
        ctx.current_phase = current_phase

        # EVENT-LOG: старт фазы
        update_context(phase_id=current_phase)
        log_event(event_type="phase_started", payload={
            "phase": current_phase,
            "agent": _phase_agent,
            "step": step,
        })

        # Б63 (14.10.2026): REPO-MAP-REFRESH — обновляем карту
        # проекта перед code/fix-фазами. Иначе LLM видит устаревший
        # repo_map из прошлых прогонов и думает, что работа уже
        # сделана. Особенно важно, если проект откатывали вручную.
        if current_phase in (PHASE_CODE, PHASE_FIX):
            try:
                from core.repo_map import save_repo_map as _srm
                _rmp = _srm(ctx.project_dir)
                if _rmp:
                    logger.info(
                        "REPO-MAP-REFRESH: обновлён перед %s",
                        current_phase,
                    )
            except Exception as _rme:
                logger.debug("REPO-MAP-REFRESH: %s", _rme)

        phase_result = run_phase(ctx, current_phase, agent_override)
        result.phases_done += 1

        # FIX ATTRIBUTION (2026-09-30): записываем outcome с
        # 5-уровневой моделью credit assignment.
        #   plan/code → не пишем (проблем нет)
        #   review    → не пишем (это проверка)
        #   fix       → пишем с eligible / used / verified
        # Это устраняет confidence inflation.
        _ATTRIB_PHASES = (PHASE_FIX,)
        if current_phase in _ATTRIB_PHASES:
            try:
                _pids = getattr(ctx, "applied_pattern_ids", None) or []
                if _pids:
                    from core import memory_api as _mem
                    from core import memory_patterns as _mp
                    from core import memory_attribution as _attr

                    _changed = list(
                        getattr(phase_result, "files_created", None)
                        or []
                    )
                    _problem_text = (
                        getattr(phase_result, "output", "") or ""
                    )[:2000]
                    _agent = getattr(phase_result, "agent", "") or ""
                    _provider = _agent.replace("browser_", "")
                    _component = ""
                    if _changed:
                        _component = (
                            _changed[0].split("/")[-1]
                            .replace(".py", "")
                            .replace(".bas", "")
                        )
                    _verify_ok = (phase_result.status == "success")
                    _review_ok = (phase_result.status == "success")
                    _run_id_short = str(_run_id or "")[:40]

                    _recorded = 0
                    _skipped = 0
                    for _pid in _pids:
                        _pat = _mp.get_pattern(_pid)
                        if not _pat:
                            _skipped += 1
                            continue
                        _ev = _attr.classify_evidence(
                            _pat,
                            phase=current_phase,
                            phase_status=phase_result.status,
                            problem_text=_problem_text,
                            changed_files=_changed,
                            provider=_provider,
                            component=_component,
                            verify_passed=_verify_ok,
                            next_review_ok=_review_ok,
                        )
                        # FIX 1.1.1 (17.10.2026): failures пишем ВСЕГДА,
                        # даже если eligible=False и used=False.
                        # Иначе state machine памяти (5 провалов → shadow)
                        # не может сработать: провалы отсекаются до записи.
                        _is_failure = (phase_result.status != "success")
                        if not _is_failure and not (_ev["eligible"] or _ev["used"]):
                            _skipped += 1
                            continue
                        _outcome = (
                            "failure"
                            if _is_failure
                            else "success"
                        )
                        if _mem.record_outcome(
                            pattern_id=_pid,
                            outcome=_outcome,
                            run_id=_run_id_short,
                            model=_agent,
                            injected=True,
                            phase=current_phase,
                            problem_keywords=_problem_text[:500],
                            relevance_score=_ev["relevance_score"],
                            retrieved=_ev["retrieved"],
                            eligible=_ev["eligible"],
                            used=_ev["used"],
                            supported=_ev["supported"],
                            verified_effect=_ev["verified_effect"],
                            attribution_method=_ev["attribution_method"],
                        ):
                            _recorded += 1
                    if _recorded or _skipped:
                        logger.info(
                            "MEMORY-ATTR: recorded=%d skipped=%d "
                            "(phase=%s, files=%d)",
                            _recorded, _skipped, current_phase,
                            len(_changed),
                        )
                # Сбрасываем для следующей фазы
                ctx.applied_pattern_ids = []
            except Exception as _e:
                logger.debug("MEMORY-ATTR: ошибка: %s", _e)
        else:
            # plan/code/review — просто сбрасываем
            ctx.applied_pattern_ids = []

        # EVENT-LOG: конец фазы
        log_event(event_type="phase_completed", payload={
            "phase": current_phase,
            "status": phase_result.status,
            "seconds": round(float(getattr(phase_result, "seconds", 0) or 0), 2),
        })

        # BRAIN-RULE-FIRST (shadow): brain советует — оркестратор
        # решает. Логируем решение brain и сравниваем с фактическим
        # поведением. НЕ меняем поток (переключение — отдельный шаг).
        try:
            from core.brain import decide as _brain_decide
            _fc = getattr(phase_result, "files_created", None)
            if isinstance(_fc, list):
                _fc_n = len(_fc)
            else:
                _fc_n = int(_fc or 0)
            # FIX: если этот проход не создал файлов, но проект уже
            # имеет .py — считаем, что файлы есть (PHASES-2 крутит
            # code по этапам, на 2-3 этапе файлы уже созданы).
            if _fc_n == 0:
                try:
                    from core.repo_map import load_repo_map
                    _rm = load_repo_map(ctx.project_dir)
                    if _rm:
                        _fc_n = int(_rm.get("py_files", 0) or 0)
                except Exception:
                    pass
            _status_str = phase_result.status or ""
            # FIX: review_status и severity — из output, а не из
            # phase_result.status (там всегда success).
            _review_status = ""
            _review_severity = ""
            if current_phase == PHASE_REVIEW:
                try:
                    _review_status = parse_review_status(
                        phase_result.output or ""
                    )
                    _review_severity = parse_review_severity(
                        phase_result.output or ""
                    )
                except Exception:
                    pass
            _state_brain = {
                "phase": current_phase,
                "phase_status": _status_str,
                "files_created": _fc_n,
                "iterations": int(getattr(phase_result, "iterations", 0) or 0),
                "review_status": _review_status,
                "review_severity": _review_severity,
                "verification_status": "",
                "stop_reason": getattr(phase_result, "stop_reason", "") or "",
                "same_failures": consecutive_failures.get(current_phase, 0),
                "fix_cycles": fix_count,
                "has_stages": bool(_use_stages),
                "stage_index": int(getattr(ctx, "current_stage_index", 0) or 0),
                "total_stages": int(_total_stages or 0),
            }
            _brain_d = _brain_decide(_state_brain, use_llm=False)
            log_event(event_type="brain_shadow", payload={
                "actual_phase_status": _status_str,
                "brain_decision": _brain_d.get("decision"),
                "brain_reason": _brain_d.get("reason"),
                "brain_confidence": _brain_d.get("confidence"),
                "brain_mode": _brain_d.get("mode"),
                "files_created": _fc_n,
            })
        except Exception as _be:
            logger.debug("BRAIN shadow: ошибка %s", _be)

        # VERIFICATION-RECEIPT: формальный отчёт после code/fix/fallback.
        # LLM предлагает — инструменты доказывают.
        if current_phase in (PHASE_CODE, PHASE_FIX, PHASE_FALLBACK):
            try:
                from core.verification import build_and_save as _vb
                _rcpt = _vb(ctx.project_dir, phase=current_phase,
                            run_id=_run_id)
                # ACCEPTANCE-GATE (2026-10-07): сохраняем receipt,
                # чтобы fix×2 мог использовать его для решения.
                try:
                    ctx.last_verification = dict(_rcpt)
                except Exception:
                    pass
                logger.info(
                    "VERIFY: отчёт %s (status=%s, syntax=%s, "
                    "tests=%s, imports=%s)",
                    current_phase, _rcpt.get("status"),
                    _rcpt.get("syntax", {}).get("status"),
                    _rcpt.get("tests", {}).get("status"),
                    _rcpt.get("imports", {}).get("status"),
                )
                log_event(event_type="verification_receipt", payload={
                    "phase": current_phase,
                    "status": _rcpt.get("status"),
                    "syntax": _rcpt.get("syntax", {}).get("status"),
                    "tests": _rcpt.get("tests", {}).get("status"),
                    "imports": _rcpt.get("imports", {}).get("status"),
                    "saved_to": _rcpt.get("saved_to", ""),
                })
                # VERIFY-FAIL (2026-10-02): если инструменты
                # показали failed — фаза НЕ success, даже если
                # LLM сказал "готово". Чинит баг 01.10.
                if current_phase in (PHASE_CODE, PHASE_FIX):
                    _vs = _rcpt.get("status") or ""
                    _ts = _rcpt.get("tests", {}).get("status") or ""
                    _syn = _rcpt.get("syntax", {}).get("status") or ""
                    _imp = _rcpt.get("imports", {}).get("status") or ""
                    _verify_failed = (
                        _vs == "failed"
                        or _ts == "failed"
                        or _syn == "failed"
                        or _imp == "failed"
                    )
                    if _verify_failed and phase_result.status == "success":
                        _prev = phase_result.status
                        phase_result.status = "failed"
                        phase_result.error = (
                            f"VERIFY-FAIL: status={_vs}, "
                            f"tests={_ts}, syntax={_syn}, imports={_imp}"
                        )
                        logger.warning(
                            "VERIFY-FAIL (2026-10-02): фаза %s "
                            "была '%s' -> 'failed' "
                            "(tests=%s, syntax=%s, imports=%s)",
                            current_phase, _prev, _ts, _syn, _imp,
                        )
                        # VERIFY-FAIL-DETAIL (2026-10-03): добавляем
                        # детали в context.errors, чтобы следующая
                        # попытка fix ВИДЕЛА, что именно упало.
                        # Иначе fix переделывает то же самое (и снова
                        # падает на тех же тестах).
                        try:
                            _tests = _rcpt.get("tests", {}) or {}
                            if _ts == "failed":
                                _failed_n = _tests.get("failed", "?")
                                _passed_n = _tests.get("passed", "?")
                                _tail = (
                                    _tests.get("output_tail") or ""
                                ).strip()[-800:]
                                _detail = (
                                    f"VERIFY-FAIL (tests): "
                                    f"failed={_failed_n}, "
                                    f"passed={_passed_n}. "
                                    f"Вывод pytest (последние строки):\n"
                                    f"{_tail}"
                                )
                                context.errors.append(_detail)
                                logger.info(
                                    "VERIFY-FAIL-DETAIL (tests): "
                                    "добавлено в context.errors (%d симв)",
                                    len(_detail),
                                )
                            elif _syn == "failed":
                                _syn_info = _rcpt.get("syntax", {}) or {}
                                _errors = _syn_info.get("errors", []) or []
                                _errs_text = "; ".join(
                                    str(e)[:200] for e in _errors[:5]
                                ) or "(нет деталей)"
                                _detail = (
                                    f"VERIFY-FAIL (syntax): {_errs_text}"
                                )
                                context.errors.append(_detail)
                                logger.info(
                                    "VERIFY-FAIL-DETAIL (syntax): "
                                    "добавлено в context.errors",
                                )
                            elif _imp == "failed":
                                _imp_info = _rcpt.get("imports", {}) or {}
                                _broken = _imp_info.get("broken", []) or []
                                _broken_text = "; ".join(
                                    str(b)[:200] for b in _broken[:5]
                                ) or "(нет деталей)"
                                _detail = (
                                    f"VERIFY-FAIL (imports): "
                                    f"{_broken_text}"
                                )
                                context.errors.append(_detail)
                                logger.info(
                                    "VERIFY-FAIL-DETAIL (imports): "
                                    "добавлено в context.errors",
                                )
                        except Exception as _de:
                            logger.debug(
                                "VERIFY-FAIL-DETAIL: ошибка: %s", _de,
                            )
            except Exception as _e:
                logger.warning("VERIFY: не удалось собрать отчёт: %s", _e)

        logger.info("orchestrate: фаза %s → %s (%.1fs)",
                    current_phase, phase_result.status,
                    phase_result.seconds)

        # LONG-RUN: чекпоинт после каждой фазы
        try:
            _save_checkpoint(ctx, result, _run_id, start,
                             status_override="running")
        except Exception:
            pass

        if progress_callback:
            try:
                progress_callback(current_phase, phase_result)
            except Exception:
                pass

        # Фаза запросила уточнение у пользователя — останавливаемся
        if phase_result.status == "needs_clarification":
            # LOG-FULL (2026-10-01): сохраняем полный ответ модели,
            # чтобы видеть, ЧТО она написала (для диагностики
            # tool-calls, которые не прошли валидацию).
            try:
                from pathlib import Path as _P
                from datetime import datetime as _dt
                _diag = _P(context.project_dir) / ".ai_link" / "diagnostics"
                _diag.mkdir(parents=True, exist_ok=True)
                _ts = _dt.now().strftime("%Y%m%d_%H%M%S")
                _fname = f"clarification_{_ts}_{current_phase}.txt"
                (_diag / _fname).write_text(
                    f"=== PHASE: {current_phase} ===\n"
                    f"=== AGENT: {getattr(phase_result, 'agent', '?')} ===\n"
                    f"=== STATUS: {phase_result.status} ===\n"
                    f"=== QUESTION: {getattr(phase_result, 'clarification_question', '')} ===\n"
                    f"=== ERROR: {getattr(phase_result, 'error', '')} ===\n"
                    f"=== FULL OUTPUT ===\n"
                    f"{phase_result.output or ''}\n",
                    encoding="utf-8",
                )
                logger.info("LOG-FULL: сохранён %s", _fname)
            except Exception as _e:
                logger.warning("LOG-FULL: не сохранил: %s", _e)

            _new_q_raw = (phase_result.clarification_question or "").strip()
            _new_q_norm = _new_q_raw.lower()[:200]

            # AJ2 v2: семантическая проверка ПОВТОРА.
            # Сначала точное совпадение (быстро), потом
            # пересечение значимых слов (Qwen перефразирует).
            _is_repeat = bool(_new_q_norm) and (
                _new_q_norm in _past_questions
                or _is_question_in_past(_new_q_raw, _past_questions)
            )

            # AJ2: жёсткий лимит на количество разных вопросов
            _over_limit = _clarification_attempts >= _max_clarifications

            if _is_repeat or _over_limit:
                result.status = "failed"
                result.success = False
                result.final_phase = current_phase
                if _is_repeat:
                    result.error = (
                        f"Link повторяет тот же вопрос "
                        f"(всего {_clarification_attempts} уточнений). "
                        f"Попробуйте переформулировать задачу."
                    )
                    logger.info(
                        "orchestrate: AJ2 — повтор вопроса, стоп "
                        "(уточнений=%d)",
                        _clarification_attempts,
                    )
                else:
                    result.error = (
                        f"Link не смог составить план после "
                        f"{_clarification_attempts} уточнений. "
                        f"Попробуйте переформулировать задачу."
                    )
                    logger.info(
                        "orchestrate: AJ2 — превышен лимит уточнений "
                        "(%d)",
                        _clarification_attempts,
                    )
                break

            # S2: маршрутизация — если адрес не user,
            # передаём вопрос другой фазе, не останавливаемся.
            _target = getattr(
                phase_result, "clarification_target", "user"
            ) or "user"
            _transfer_count = getattr(
                result, "_clarification_transfers", 0
            )
            _max_transfers = 5
            if _target != "user" and _transfer_count < _max_transfers:
                # Меняем фазу на target, кладём вопрос в контекст
                _from = current_phase
                ctx.pending_clarification = {
                    "from": _from,
                    "target": _target,
                    "question": phase_result.clarification_question,
                }
                result._clarification_transfers = _transfer_count + 1
                logger.info(
                    "orchestrate: S2 — маршрутизация вопроса "
                    "%s → %s (передача %d/%d)",
                    _from, _target,
                    _transfer_count + 1, _max_transfers,
                )
                # Если target валидная фаза — переключаемся
                if is_valid_phase(_target):
                    current_phase = _target
                    continue
                # Иначе (target = fallback?) — обычная остановка
                logger.info(
                    "orchestrate: S2 — target=%s не валидная фаза, "
                    "останавливаемся", _target
                )

            # Обычная остановка — вопрос адресован пользователю
            result.status = "needs_clarification"
            result.success = False
            result.final_phase = current_phase
            result.clarification_question = phase_result.clarification_question
            result.clarification_phase = current_phase
            result.clarification_target = _target
            result.error = "требуется уточнение от пользователя"
            break

        # Обработка сбоя фазы
        if phase_result.status != "success":
            consecutive_failures[current_phase] = consecutive_failures.get(current_phase, 0) + 1
            fails = consecutive_failures[current_phase]

            # AD.H: умная обработка reason через route_rules.decide_action.
            # Используем ТОЛЬКО для reason из DEFAULT_RULES — для
            # остальных работает старая общая логика (2 подряд).
            try:
                from core.route_rules import (
                    extract_reason, get_fallback_agents,
                    decide_action, DEFAULT_RULES,
                )
                _reason = extract_reason(phase_result.error)
                if _reason and _reason in DEFAULT_RULES:
                    _attempts_made = fails - 1  # 1-й провал = 0 попыток
                    _decision = decide_action(
                        current_phase, _reason, _attempts_made
                    )
                    _action = _decision.get("action", "")
                    logger.info(
                        "orchestrate: route_rules — reason=%s, "
                        "attempts=%d/%d, action=%s",
                        _reason, _attempts_made,
                        _decision.get("max_attempts", 0),
                        _action,
                    )

                    if _action == "skip":
                        # login_required — особое сообщение
                        if _reason == "login_required":
                            result.error = (
                                f"{current_phase}: требуется вход "
                                f"(agent={phase_result.agent})"
                            )
                        else:
                            result.error = (
                                f"{current_phase}: {_reason} — "
                                f"пропущено "
                                f"({_decision.get('note', '')})"
                            )
                        result.status = "failed"
                        break

                    if _action == "stop":
                        result.status = "failed"
                        result.error = (
                            f"{current_phase}: {_reason} — "
                            f"исчерпаны попытки "
                            f"({_decision.get('note', '')})"
                        )
                        break

                    if _action == "switch_agent":
                        _fb_list = get_fallback_agents(current_phase)
                        _next = None
                        try:
                            _idx = _fb_list.index(phase_result.agent)
                            if _idx + 1 < len(_fb_list):
                                _next = _fb_list[_idx + 1]
                        except ValueError:
                            _next = _fb_list[0] if _fb_list else None
                            if _next == phase_result.agent:
                                _next = (
                                    _fb_list[1]
                                    if len(_fb_list) > 1 else None
                                )
                        if _next:
                            if phase_overrides is None:
                                phase_overrides = {}
                            phase_overrides[current_phase] = _next
                            # MODEL-SUBSTITUTION: логируем замену
                        else:
                            # ROLES (2026-10-01): fallback невозможен.
                            # Раньше тихо брал "что-то" из hardcoded.
                            # Теперь — явный стоп с понятной ошибкой.
                            result.status = "failed"
                            result.error = (
                                f"{current_phase}: {_reason} — "
                                f"fallback невозможен. "
                                f"Назначьте роль «Мозг» в Настройках → "
                                f"Роли, чтобы включить аварийный режим."
                            )
                            logger.warning(
                                "orchestrate: switch_agent, но fallback "
                                "пусто (brain не назначен) — стоп"
                            )
                            break
                            try:
                                from core.model_substitution import (
                                    role_for_phase, log_substitution,
                                )
                                log_substitution(
                                    ctx.project_dir,
                                    role=role_for_phase(current_phase),
                                    old_agent=phase_result.agent,
                                    new_agent=_next,
                                    reason=_reason or "switch_agent",
                                )
                            except Exception:
                                pass
                            continue
                        # Резервных нет — падаем к общей логике ниже

                    if _action == "retry":
                        continue
            except Exception:
                pass

            # Фаза plan: retry один раз, потом ошибка
            if current_phase == PHASE_PLAN:
                if plan_retries >= 1:
                    result.status = "failed"
                    result.error = f"plan не справился: {phase_result.error}"
                    break
                plan_retries += 1
                # FIX 2026-09-28: перед retry — сброс DOM,
                # иначе второй заход увидит тот же старый ответ
                try:
                    _plan_agent = get_phase_agent(PHASE_PLAN)
                    _reset_browser_for_agent(_plan_agent)
                    logger.info(
                        "CHAT-LIFECYCLE: сброс перед retry plan"
                    )
                except Exception:
                    pass
                continue  # повторить plan

            # Задача R + ACCEPTANCE-GATE (2026-10-07):
            # если fix_count >= max_fix_cycles — не «принимаем
            # результат вслепую», а проверяем acceptance.
            #   • accepted / accepted_with_debt → идём дальше.
            #   • blocked → вызываем brain (LLM-арбитр).
            if current_phase == PHASE_FIX and fix_count >= max_fix_cycles:
                _acceptance = _compute_acceptance(ctx.last_verification)
                logger.info(
                    "ACCEPTANCE: fix×2 → %s (status=%s, syntax=%s, "
                    "imports=%s, tests=%s)",
                    _acceptance,
                    (ctx.last_verification or {}).get("status"),
                    ((ctx.last_verification or {}).get("syntax") or {}).get("status"),
                    ((ctx.last_verification or {}).get("imports") or {}).get("status"),
                    ((ctx.last_verification or {}).get("tests") or {}).get("status"),
                )
                try:
                    log_event(event_type="acceptance_decision", payload={
                        "stage_index": ctx.current_stage_index,
                        "fix_count": fix_count,
                        "acceptance": _acceptance,
                    })
                except Exception:
                    pass

                # === BLOCKED → brain (апокалипсис) ===
                if _acceptance == "blocked":
                    logger.warning(
                        "ACCEPTANCE: этап %d — blocked. "
                        "Вызываю brain (LLM-арбитр).",
                        ctx.current_stage_index + 1,
                    )
                    _bd = _brain_apocalypse_decide(ctx, phase_result)
                    _dec = (_bd or {}).get("decision", "")
                    logger.info(
                        "ACCEPTANCE: brain → %s (%s, conf=%.2f)",
                        _dec, (_bd or {}).get("mode", "?"),
                        float((_bd or {}).get("confidence", 0) or 0),
                    )
                    if _dec in ("run_fix", "continue_phase"):
                        # Brain считает, что нужна ещё попытка fix.
                        # Даём ещё 1 цикл (лимит = max_fix + 1).
                        logger.info(
                            "ACCEPTANCE: brain → ещё fix (лимит +1)"
                        )
                        max_fix_cycles += 1
                        consecutive_failures = {}
                        continue
                    if _dec in ("next_stage", "continue_with_debt",
                                "finish"):
                        # Brain принял долг — идём дальше.
                        logger.info(
                            "ACCEPTANCE: brain принял долг → далее"
                        )
                        if _try_advance_stage(
                            ctx, _use_stages, _total_stages
                        ):
                            fix_count = 0
                            consecutive_failures = {}
                            current_phase = PHASE_CODE
                            continue
                        result.success = True
                        result.status = "success_partial"
                        result.error = (
                            "brain принял долг: "
                            + ((_bd or {}).get("reason") or "")
                        )
                        break
                    if _dec in ("pause", "abort"):
                        result.success = False
                        result.status = "paused" if _dec == "pause" else "failed"
                        result.error = (
                            "brain (blocked): "
                            + ((_bd or {}).get("reason") or _dec)
                        )
                        break
                    # Неизвестное решение — трактуем как pause.
                    result.success = False
                    result.status = "paused"
                    result.error = (
                        "brain: неизвестное решение "
                        f"{_dec!r} при blocked"
                    )
                    break

                # === accepted / accepted_with_debt → идём дальше ===
                # ФИКС №3: если многоэтапный проект — идём дальше.
                if _try_advance_stage(ctx, _use_stages, _total_stages):
                    logger.info(
                        "PHASES-2: fix исчерпан на этапе %d "
                        "(%s), идём к следующему",
                        ctx.current_stage_index, _acceptance,
                    )
                    fix_count = 0
                    consecutive_failures = {}
                    current_phase = PHASE_CODE
                    continue
                result.success = True
                result.status = "success_partial"
                result.error = (
                    f"fix×2 ({_acceptance}) — принимаем результат"
                )
                logger.info(
                    "orchestrate: fix_count=%d >= max=%d → "
                    "success_partial (%s)",
                    fix_count, max_fix_cycles, _acceptance,
                )
                break

            # Фаза fix: два провала подряд → fallback
            if current_phase == PHASE_FIX and fails >= max_same_failures:
                current_phase = PHASE_FALLBACK
                continue

            # B54 (2026-10-12): code failed → fix, не повторяем
            # code вслепую. VERIFY-FAIL знает что сломано —
            # fix может починить.
            if current_phase == PHASE_CODE:
                logger.info(
                    "B54: code failed (%s) → fix",
                    (phase_result.error or "")[:80],
                )
                current_phase = PHASE_FIX
                consecutive_failures[current_phase] = 0
                continue

            # Фаза fallback: провал → стоп
            if current_phase == PHASE_FALLBACK:
                result.status = "failed"
                result.error = f"fallback не справился: {phase_result.error}"
                break

            # Любая другая фаза: пробуем ещё раз (до 2 подряд)
            if fails < max_same_failures:
                continue
            else:
                result.status = "failed"
                result.error = f"фаза {current_phase} провалилась {fails} раз: {phase_result.error}"
                break

        # Успех фазы — сбрасываем счётчик
        consecutive_failures[current_phase] = 0

        # B55 (2026-10-13): фиксируем, были ли затронуты файлы
        # за текущий этап. Используется в _mark_stage_done
        # чтобы не закрывать этап как done при 0 работе.
        if current_phase in (PHASE_CODE, PHASE_FIX, PHASE_FALLBACK):
            # Б64: факт наличия WRITE-фазы в этом этапе.
            ctx.stage_had_write_phase = True
            # Б70: если фаза завершилась через task_complete —
            # LLM осознанно сказала «готово».
            if getattr(phase_result, "task_complete", False):
                ctx.stage_had_task_complete = True
            _touched_any = bool(
                (getattr(phase_result, "files_created", None) or [])
                or (getattr(phase_result, "files_changed", None) or [])
                or (getattr(phase_result, "created_by_command", None) or [])
                or (getattr(phase_result, "changed_by_command", None) or [])
            )
            if _touched_any:
                ctx.stage_had_files = True

        # Переходы
        if current_phase == PHASE_PLAN:
            # Сохраняем в context ОБРЕЗАННЫЙ текст (без копии промпта)
            ctx.plan = {"text": _clean_plan_text(phase_result.output)}
            # Пишем план в файл проекта, чтобы он был виден в «Карте проекта»
            try:
                _save_project_plan(ctx.project_dir, phase_result.output, task)
            except Exception:
                pass
            # PHASES-1.5: подтянуть структуру (stages) из plan.json
            # в ctx.plan. Иначе PHASES-1 не работает — видит только
            # {"text": ...} без этапов.
            try:
                import json as _json
                from pathlib import Path as _P
                _plan_path = _P(ctx.project_dir) / ".ai_link" / "plan.json"
                if _plan_path.exists():
                    _plan_data = _json.loads(
                        _plan_path.read_text(encoding="utf-8")
                    )
                    _stages = _plan_data.get("stages") or []
                    if _stages:
                        ctx.plan["stages"] = _stages
                        ctx.plan["title"] = _plan_data.get("title", "")
                        ctx.plan["goal"] = _plan_data.get("goal", "")
                        logger.info(
                            "PHASES: загружено этапов из plan.json: %d",
                            len(_stages),
                        )
                        # Сбрасываем индекс этапа (PHASES-2 будет крутить)
                        ctx.current_stage_index = 0
                        # PHASES-2: решаем — дробить или нет.
                        # Большой проект: ≥3 этапов ИЛИ ≥10 шагов.
                        _total_items = sum(
                            len(s.get("items", [])) for s in _stages
                        )
                        _use_stages = (
                            len(_stages) >= 3 or _total_items >= 10
                        )
                        _total_stages = len(_stages)
                        # FIX: динамический лимит фаз по числу
                        # этапов. По 7 фаз на этап (code + 2×(review+fix)
                        # + review + запас) + 5 сверху.
                        if _total_stages > 0:
                            _dynamic_max = 5 + _total_stages * 7
                            if _dynamic_max > max_phases:
                                max_phases = _dynamic_max
                                logger.info(
                                    "PHASES: динамический лимит фаз "
                                    "поднят до %d (этапов: %d)",
                                    max_phases, _total_stages,
                                )
                            # FIX №10: динамический timeout — по 20
                            # минут на этап + 10 мин запас, минимум
                            # 1 час. Локальная переменная, чтобы
                            # не портить аргумент.
                            _dyn_timeout = max(
                                3600.0, _total_stages * 1200.0 + 600.0
                            )
                            if _dyn_timeout > timeout:
                                timeout = _dyn_timeout
                                logger.info(
                                    "PHASES: динамический timeout "
                                    "поднят до %.0f сек (%.1f ч, "
                                    "этапов: %d)",
                                    timeout, timeout / 3600,
                                    _total_stages,
                                )
                        if _use_stages:
                            logger.info(
                                "PHASES-2: проект большой "
                                "(%d этапов, %d шагов) — "
                                "работаем по этапам",
                                len(_stages), _total_items,
                            )
                        else:
                            logger.info(
                                "PHASES-2: проект маленький "
                                "(%d этапов, %d шагов) — "
                                "один проход",
                                len(_stages), _total_items,
                            )
            except Exception as _e:
                logger.warning(
                    "PHASES: не удалось загрузить stages: %s", _e
                )

            # Задача O: если пользователь просил «только план» —
            # завершаем после успешного plan, не идём в code.
            if _user_wants_only_plan(task):
                result.success = True
                result.status = "success"
                result.final_phase = PHASE_PLAN
                # ctx.current_phase тоже фиксируем — иначе в финале
                # orchestrate перезапишет final_phase пустой строкой.
                ctx.current_phase = PHASE_PLAN
                logger.info(
                    "orchestrate: остановка после plan — "
                    "пользователь просил «только план»"
                )
                break

            # BRAIN-SWITCH: спрашиваем brain о переходе после plan.
            if brain_switch:
                _bd = _brain_decide_for_transition(
                    ctx, PHASE_PLAN, phase_result,
                    phase_status="success",
                )
                if _bd:
                    _dec = _bd.get("decision", "")
                    logger.info(
                        "BRAIN-SWITCH: plan → %s (%s, conf=%.2f)",
                        _dec, _bd.get("mode", "?"),
                        float(_bd.get("confidence", 0) or 0),
                    )
                    if _dec in ("run_code", "continue_phase",
                                "next_stage"):
                        # B (18.10.2026): research ПЕРЕД первым code
                        # в brain-пути. Guard: если уже в research —
                        # сразу code (защита от зацикливания).
                        if (not _research_done
                                and current_phase != PHASE_RESEARCH):
                            current_phase = PHASE_RESEARCH
                            logger.info(
                                "RESEARCH-TRIGGER: brain → research "
                                "(перед code)"
                            )
                            try:
                                log_event(
                                    event_type="research_triggered",
                                    payload={
                                        "source": "brain_path",
                                        "phase_before": PHASE_PLAN,
                                    },
                                )
                            except Exception:
                                pass
                        else:
                            current_phase = PHASE_CODE
                        continue
                    if _dec == "finish":
                        result.success = True
                        result.status = "success"
                        result.final_phase = PHASE_PLAN
                        break
                    if _dec == "pause":
                        result.success = False
                        result.status = "paused"
                        result.error = (
                            "brain: " + (_bd.get("reason") or "pause")
                        )
                        break
                    if _dec == "abort":
                        result.success = False
                        result.status = "failed"
                        result.error = (
                            "brain: " + (_bd.get("reason") or "abort")
                        )
                        break
                    logger.warning(
                        "BRAIN-SWITCH: неизвестное решение %r "
                        "— используем старую логику", _dec,
                    )

            # B (18.10.2026): после plan success — research (один раз),
            # потом code. Research читает отчёты и пишет
            # research_notes.json. Если уже был — сразу code.
            if not _research_done:
                current_phase = PHASE_RESEARCH
                logger.info(
                    "RESEARCH-TRIGGER: plan → research (перед code)"
                )
            else:
                current_phase = PHASE_CODE
            continue

        if current_phase == PHASE_RESEARCH:
            # B (18.10.2026): research завершён.
            # Сохраняем research_notes.json и идём на code.
            _research_done = True
            try:
                _text = (getattr(phase_result, "output", "") or "")
                if _text.strip():
                    _rdata = _parse_research_json(_text)
                    _save_research_notes(ctx.project_dir, _rdata)
                    _ntargets = len(_rdata.get("targets") or [])
                    logger.info(
                        "RESEARCH: сохранён research_notes.json "
                        "(targets=%d)", _ntargets,
                    )
                else:
                    logger.info(
                        "RESEARCH: пустой ответ — research_notes.json "
                        "не создан (code пойдёт без чеклиста)"
                    )
            except Exception as _re:
                logger.warning("RESEARCH: сохранение notes: %s", _re)
            current_phase = PHASE_CODE
            continue

        if current_phase == PHASE_CODE:
            # BRAIN-SWITCH ч.2: спрашиваем brain о переходе после code.
            if brain_switch:
                _bd = _brain_decide_for_transition(
                    ctx, PHASE_CODE, phase_result,
                    phase_status="success",
                )
                if _bd:
                    _dec = _bd.get("decision", "")
                    logger.info(
                        "BRAIN-SWITCH: code → %s (%s, conf=%.2f)",
                        _dec, _bd.get("mode", "?"),
                        float(_bd.get("confidence", 0) or 0),
                    )
                    if _dec in ("run_review", "next_stage",
                                "continue_phase"):
                        # B+ (19.10.2026): перед review — проверяем P0.
                        # Brain перехватывает code → review, наша
                        # проверка ниже не доходит. Вставляем сюда.
                        try:
                            _p0_status = _compute_research_p0_status(
                                ctx.project_dir
                            )
                        except Exception as _p0e:
                            logger.debug("RESEARCH-ACCEPT: %s", _p0e)
                            _p0_status = "unknown"
                        if _p0_status == "not_fulfilled":
                            logger.warning(
                                "RESEARCH-ACCEPT: P0 не выполнены — "
                                "brain code → fix"
                            )
                            ctx.errors.append(
                                "P0 не выполнены (см. "
                                ".ai_link/research_notes.json). "
                                "Исправь каждый P0: прочитай файл, "
                                "примени pattern-проверку, "
                                "убедись что изменение реально в коде."
                            )
                            current_phase = PHASE_FIX
                            continue
                        current_phase = PHASE_REVIEW
                        continue
                    if _dec == "run_fix":
                        current_phase = PHASE_FIX
                        continue
                    if _dec == "finish":
                        result.success = True
                        result.status = "success"
                        break
                    if _dec == "pause":
                        result.success = False
                        result.status = "paused"
                        result.error = (
                            "brain: " + (_bd.get("reason") or "pause")
                        )
                        break
                    if _dec == "abort":
                        result.success = False
                        result.status = "failed"
                        result.error = (
                            "brain: " + (_bd.get("reason") or "abort")
                        )
                        break
                    logger.warning(
                        "BRAIN-SWITCH: неизвестное решение %r "
                        "на code — старая логика", _dec,
                    )
            # B (18.10.2026): перед уходом на review — проверяем
            # P0 из research_notes.json. Если хоть один не выполнен —
            # fix, а не review. Иначе этап закроется ложным success.
            try:
                _p0_status = _compute_research_p0_status(ctx.project_dir)
            except Exception as _p0e:
                logger.debug("RESEARCH-ACCEPT: %s", _p0e)
                _p0_status = "unknown"
            if _p0_status == "not_fulfilled":
                logger.warning(
                    "RESEARCH-ACCEPT: P0 не выполнены — code → fix"
                )
                ctx.errors.append(
                    "P0 не выполнены (см. .ai_link/research_notes.json). "
                    "Исправь каждый P0: прочитай файл, примени pattern-проверку, "
                    "убедись что изменение реально в коде."
                )
                current_phase = PHASE_FIX
                continue

            # Старая логика
            current_phase = PHASE_REVIEW
            continue

        if current_phase == PHASE_REVIEW:
            status = parse_review_status(phase_result.output)

            # Б62: сбрасываем счётчик unknown при чётком вердикте.
            if status != "unknown":
                ctx.review_unknown_retries = 0

            # SEVERITY: если review нашёл только MINOR-замечания —
            # не запускаем fix, принимаем как OK.
            # CROSS-REVIEW-2: _severity передаётся во все log_review.
            _severity = parse_review_severity(phase_result.output)
            if status == "problems" and _severity == "minor":
                logger.info(
                    "orchestrate: SEVERITY — только MINOR, "
                    "принимаем как OK (fix не нужен)"
                )
                status = "ok"

            # FIX AUTO-LESSONS (2026-09-30): сохраняем review
            # с PROBLEMS — если fix его исправит, создадим паттерн.
            if status == "problems":
                ctx.pending_problem_review = (
                    phase_result.output or ""
                )[:3000]

            # Запоминаем данные для лога
            try:
                from core.review_log import count_syntax_errors
                _syn_before = count_syntax_errors(ctx.project_dir)
            except Exception:
                _syn_before = 0
            pending_review = {
                "agent": phase_result.agent,
                "verdict": status,
                "output": phase_result.output,
                "syntax_errors_before": _syn_before,
            }

            if status == "ok":
                # Логируем сразу, fix не нужен
                try:
                    from core.review_log import log_review
                    log_review(
                        ctx.project_dir,
                        agent=phase_result.agent,
                        verdict=status,
                        output=phase_result.output,
                        syntax_errors_before=_syn_before,
                        fix_applied=False,
                        syntax_errors_after=_syn_before,
                        severity=_severity,
                    )
                except Exception:
                    pass

                # FIX AUTO-LESSONS (2026-09-30): если до этого был
                # PROBLEMS и fix исправил — создаём паттерн из fix.
                if ctx.pending_problem_review:
                    try:
                        _auto_create_pattern_from_context(
                            ctx, phase_result
                        )
                    except Exception as _e:
                        logger.debug("AUTO-LESSONS: %s", _e)
                    ctx.pending_problem_review = ""

                # PHASES-2: если это большой проект и есть ещё
                # этапы — переходим к следующему.
                if _try_advance_stage(ctx, _use_stages, _total_stages):
                    fix_count = 0
                    consecutive_failures = {}
                    current_phase = PHASE_CODE
                    continue

                # Все этапы пройдены (или проект маленький)
                result.success = True
                result.status = "success"
                break

            # Если дошли до лимита fix — принимаем результат
            if fix_count >= max_fix_cycles:
                try:
                    from core.review_log import log_review
                    log_review(
                        ctx.project_dir,
                        agent=phase_result.agent,
                        verdict=status,
                        output=phase_result.output,
                        syntax_errors_before=_syn_before,
                        fix_applied=False,
                        syntax_errors_after=_syn_before,
                        severity=_severity,
                    )
                except Exception:
                    pass

                # ФИКС №3: если это многоэтапный проект — не
                # бросаем всю задачу, а идём к следующему этапу.
                if _try_advance_stage(ctx, _use_stages, _total_stages):
                    logger.info(
                        "PHASES-2: review-problems на этапе %d, "
                        "fix исчерпан — идём к следующему этапу",
                        ctx.current_stage_index,
                    )
                    fix_count = 0
                    consecutive_failures = {}
                    current_phase = PHASE_CODE
                    continue

                # Все этапы пройдены — принимаем результат
                result.success = True
                result.status = "success_partial"
                result.error = (
                    f"review продолжает находить проблемы, "
                    f"но fix выполнялся {fix_count} раз — принимаем результат"
                )
                break

            # CROSS-REVIEW: cross-verify через вторую модель.
            # FIX №5: убрали _skip_cross для CRITICAL — реальный
            # прогон показал, что Qwen кричит ложный CRITICAL.
            # Теперь проверяем ВСЕ severity. Pre-check в
            # cross_verifier отклоняет ложные «файл отсутствует»
            # без вызова LLM.
            _severity_cr = parse_review_severity(phase_result.output)

            if cross_verify and _syn_before == 0:
                try:
                    from core.cross_verifier import verify_problem
                    cv = verify_problem(
                        ctx.project_dir,
                        phase_result.output,
                        review_agent=phase_result.agent,
                    )
                    _verdict = cv.get("verdict", "unknown")
                    _verifier = cv.get("verifier", "?")

                    if _verdict == "rejected":
                        # Вторая модель: придирка
                        try:
                            from core.review_log import log_review
                            log_review(
                                ctx.project_dir,
                                agent=phase_result.agent,
                                verdict="problems_rejected",
                                output=phase_result.output,
                                syntax_errors_before=_syn_before,
                                fix_applied=False,
                                syntax_errors_after=_syn_before,
                                validated_override=False,
                            )
                        except Exception:
                            pass
                        # FIX №11: rejected — не break, а проверь,
                        # есть ли ещё этапы. Если да — идём к нему.
                        if _try_advance_stage(
                            ctx, _use_stages, _total_stages
                        ):
                            logger.info(
                                "CROSS-REVIEW: %s отверг проблемы, "
                                "но этапы ещё есть — перехожу "
                                "к этапу %d/%d",
                                _verifier,
                                ctx.current_stage_index + 1,
                                _total_stages,
                            )
                            fix_count = 0
                            consecutive_failures = {}
                            current_phase = PHASE_CODE
                            continue
                        result.success = True
                        result.status = "success"
                        result.error = (
                            f"cross-verify: {_verifier} отверг "
                            f"проблемы как придирки"
                        )
                        logger.info(
                            "CROSS-REVIEW: %s отверг проблемы %s "
                            "(все этапы пройдены)",
                            _verifier, phase_result.agent,
                        )
                        break
                    elif _verdict == "confirmed":
                        # Вторая модель подтвердила — точно чиним
                        try:
                            from core.review_log import log_review
                            log_review(
                                ctx.project_dir,
                                agent=phase_result.agent,
                                verdict="problems_confirmed",
                                output=phase_result.output,
                                syntax_errors_before=_syn_before,
                                fix_applied=False,
                                syntax_errors_after=_syn_before,
                                validated_override=True,
                            )
                        except Exception:
                            pass
                        logger.info(
                            "CROSS-REVIEW: %s подтвердил проблемы %s",
                            _verifier, phase_result.agent,
                        )
                    else:
                        # unknown — не смог проверить
                        try:
                            from core.review_log import log_review
                            log_review(
                                ctx.project_dir,
                                agent=phase_result.agent,
                                verdict="problems_unknown",
                                output=phase_result.output,
                                syntax_errors_before=_syn_before,
                                fix_applied=False,
                                syntax_errors_after=_syn_before,
                                validated_override=None,
                            )
                        except Exception:
                            pass
                except Exception:
                    pass

            # Записываем замечания review в ctx.errors,
            # чтобы fix-фаза знала, что чинить.
            # Обрезаем эхо промпта (до «Завершено размышление»).
            try:
                _clean_out = _extract_review_for_fix(phase_result.output)
                if _clean_out:
                    ctx.errors.append(_clean_out[:2000])
                else:
                    logger.warning(
                        "Б56: review вернул reasoning без STATUS — "
                        "не пишем в [ОШИБКИ] (%d симв)",
                        len(phase_result.output or ""),
                    )
            except Exception:
                pass

            # Б62 (14.10.2026): если review дал неясный вердикт
            # (unknown — reasoning без STATUS) — НЕ запускаем fix
            # вслепую. Переспрашиваем review ОДИН раз с явным
            # требованием чёткого STATUS.
            if status == "unknown":
                if ctx.review_unknown_retries < 1:
                    ctx.review_unknown_retries += 1
                    logger.warning(
                        "Б62: review дал unknown (reasoning без STATUS) — "
                        "повторный запрос с требованием STATUS "
                        "(попытка %d/1)",
                        ctx.review_unknown_retries,
                    )
                    # Логируем unknown
                    try:
                        from core.review_log import log_review as _lr
                        _lr(
                            ctx.project_dir,
                            agent=phase_result.agent,
                            verdict="problems_unknown",
                            output=phase_result.output,
                            syntax_errors_before=_syn_before,
                            fix_applied=False,
                            syntax_errors_after=_syn_before,
                            severity=_severity,
                        )
                    except Exception:
                        pass
                    # Очищаем ctx.errors и кладём инструкцию для review
                    ctx.errors = [
                        "REVIEW-RETRY (Б62): твой предыдущий ответ "
                        "не содержал чёткого STATUS. Сейчас дай "
                        "ТОЛЬКО ОДНУ из двух строк, без reasoning, "
                        "без рассуждений:\n"
                        "  STATUS: OK\n"
                        "или\n"
                        "  STATUS: PROBLEMS [critical|major|minor]: "
                        "<кратко что не так>"
                    ]
                    current_phase = PHASE_REVIEW
                    continue
                else:
                    logger.warning(
                        "Б62: review дважды дал unknown — "
                        "идём в fix как fallback"
                    )

            # Иначе — снова fix
            current_phase = PHASE_FIX
            continue

        if current_phase == PHASE_FIX:
            # fix_count уже инкрементирован ПЕРЕД run_phase (задача R).
            # Здесь только логируем pending review.
            # Логируем pending review с данными после fix
            if pending_review is not None:
                try:
                    from core.review_log import (log_review,
                                                 count_syntax_errors)
                    _syn_after = count_syntax_errors(ctx.project_dir)
                    log_review(
                        ctx.project_dir,
                        agent=pending_review["agent"],
                        verdict=pending_review["verdict"],
                        output=pending_review["output"],
                        syntax_errors_before=pending_review["syntax_errors_before"],
                        fix_applied=True,
                        syntax_errors_after=_syn_after,
                        severity=_severity,
                    )
                except Exception:
                    pass
                pending_review = None

            # BRAIN-SWITCH ч.2: спрашиваем brain о переходе после fix.
            if brain_switch:
                _bd = _brain_decide_for_transition(
                    ctx, PHASE_FIX, phase_result,
                    phase_status="success",
                )
                if _bd:
                    _dec = _bd.get("decision", "")
                    logger.info(
                        "BRAIN-SWITCH: fix → %s (%s, conf=%.2f)",
                        _dec, _bd.get("mode", "?"),
                        float(_bd.get("confidence", 0) or 0),
                    )
                    if _dec in ("run_review", "next_stage"):
                        current_phase = PHASE_REVIEW
                        continue
                    if _dec in ("run_fix", "continue_phase"):
                        current_phase = PHASE_FIX
                        continue
                    if _dec == "run_code":
                        current_phase = PHASE_CODE
                        continue
                    if _dec == "finish":
                        result.success = True
                        result.status = "success"
                        break
                    if _dec == "pause":
                        result.success = False
                        result.status = "paused"
                        result.error = (
                            "brain: " + (_bd.get("reason") or "pause")
                        )
                        break
                    if _dec == "abort":
                        result.success = False
                        result.status = "failed"
                        result.error = (
                            "brain: " + (_bd.get("reason") or "abort")
                        )
                        break
                    logger.warning(
                        "BRAIN-SWITCH: неизвестное решение %r "
                        "на fix — старая логика", _dec,
                    )
            # Старая логика
            current_phase = PHASE_REVIEW
            continue

        if current_phase == PHASE_FALLBACK:
            current_phase = PHASE_REVIEW
            continue

    if result.status == "unknown":
        result.status = "max_phases"
        result.error = f"достигнут лимит фаз ({max_phases})"

    # Если задача завершена успешно — отмечаем план выполненным
    if result.success:
        try:
            _mark_plan_done(ctx.project_dir)
        except Exception:
            pass

        # B48-SECRETS-REPORT (2026-10-12): после прогона проверим,
        # остались ли секреты в проекте. Не блокирует, только
        # уведомляет пользователя.
        try:
            from core.audit_project import check_secrets
            _sec = check_secrets(ctx.project_dir, max_files=500)
            _found = _sec.get("findings") or []
            if _found:
                result.secrets_found = _found
                logger.info(
                    "SECRETS-REPORT: найдено %d секретов в проекте",
                    len(_found),
                )
                try:
                    from core.event_log import log_event as _log_ev2
                    _log_ev2(event_type="secrets_found", payload={
                        "total": len(_found),
                        "files": list({
                            f.get("file") for f in _found
                        })[:10],
                    })
                except Exception:
                    pass
        except Exception as _e:
            logger.warning("SECRETS-REPORT: %s", _e)

        # AA: создаём ярлыки запуска (Запуск.desktop +
        # Запуск_с_терминалом.desktop). Постобработка —
        # не зависит от модели.
        try:
            from core.launcher_maker import create_launcher_shortcuts
            _shortcuts = create_launcher_shortcuts(ctx.project_dir)
            if _shortcuts.get("created"):
                logger.info(
                    "AA: ярлыки созданы: %s",
                    ", ".join(_shortcuts["created"]),
                )
        except Exception as _e:
            logger.warning("AA: не удалось создать ярлыки: %s", _e)

        # SMOKE-TEST-AFTER-RUN (2026-10-12, Б47): проверка
        # работоспособности. Запускаем приложение на 15 сек.
        # Если Traceback — status=broken, не success.
        try:
            from core.smoke_test import run_smoke_test
            _smoke = run_smoke_test(ctx.project_dir, timeout=15)
            result.smoke_test = _smoke
            try:
                from core.event_log import log_event as _log_ev
                _log_ev(event_type="smoke_test", payload={
                    "ok": _smoke.get("ok"),
                    "status": _smoke.get("status"),
                    "rc": _smoke.get("rc"),
                    "error": _smoke.get("error", "")[:200],
                })
            except Exception:
                pass
            # SMOKE-SKIP (2026-10-12): 'skipped' (нет точки входа)
            # НЕ считается провалом. Только 'broken' (Traceback).
            _smoke_status = _smoke.get("status", "")
            if _smoke_status == "broken":
                # Приложение сломано — меняем статус прогона.
                result.success = False
                result.status = "broken"
                _orig_err = result.error or ""
                _parts = [
                    "SMOKE-TEST: приложение не запускается.",
                    str(_smoke.get("error", "")),
                    "Команда: " + str(_smoke.get("cmd", "")),
                    _orig_err,
                ]
                result.error = "\n".join(_parts)
                logger.error(
                    "SMOKE-TEST: FAIL. %s (cmd=%s)",
                    _smoke.get("error", ""), _smoke.get("cmd", ""),
                )
            else:
                logger.info(
                    "SMOKE-TEST: OK (status=%s, rc=%s)",
                    _smoke.get("status"), _smoke.get("rc"),
                )
        except Exception as _e:
            logger.warning("SMOKE-TEST: не удалось запустить: %s", _e)

    result.seconds = round(_time.time() - start, 2)
    result.phase_history = list(ctx.phase_history)
    result.final_phase = ctx.current_phase

    # FIX 2026-09-25: собрать files_created из всех фаз.
    # phase_history — список dict (asdict(PhaseResult)),
    # каждый может содержать files_created.
    # FIX 2026-09-25b: проверяем, что файл РЕАЛЬНО существует
    # на диске (мог быть переименован/удалён в fix-фазе).
    try:
        import os as _os
        _proj = ctx.project_dir
        _all_files: List[str] = []
        _seen: set = set()
        for _ph in ctx.phase_history:
            if not isinstance(_ph, dict):
                continue
            for _f in (_ph.get("files_created") or []):
                if not _f or _f in _seen:
                    continue
                _seen.add(_f)
                # Проверка: файл существует в проекте
                _path = _f if _os.path.isabs(_f) else _os.path.join(_proj, _f)
                if _os.path.exists(_path):
                    _all_files.append(_f)
                else:
                    logger.debug(
                        "files_created: пропущен %s (нет на диске)", _f
                    )
        result.files_created = _all_files
    except Exception as _e:
        logger.debug("сбор files_created: %s", _e)

    # LONG-RUN: финальный чекпоинт — save или clear
    # FIX STOP-FIX (2026-09-29): различаем STOP и PAUSE.
    #   • success  → clear
    #   • stopped  → сохраняем status="stopped" (фаза игнорируется
    #                при следующей задаче — старт с plan)
    #   • paused / failed / timeout → status="paused" (продолжение)
    try:
        if result.success:
            _clear_checkpoint(ctx.project_dir)
        else:
            _status_override = "paused"
            if result.status == "stopped":
                _status_override = "stopped"
            _save_checkpoint(ctx, result, _run_id, start,
                             status_override=_status_override)
    except Exception:
        pass

    # Б63 Уровень A (14.10.2026): сохраняем snapshot в КОНЦЕ
    # прогона. Тогда все правки Link'а учтены. Следующий прогон
    # сравнит с этим состоянием — если изменений нет, значит
    # никто не трогал проект извне.
    try:
        from core.project_fingerprint import (
            compute as _fp_compute, save as _fp_save_end,
        )
        _fp_end = _fp_compute(ctx.project_dir)
        _fp_save_end(ctx.project_dir, _fp_end)
        logger.info(
            "PROJECT-SNAPSHOT: сохранён в конце (%d файлов, root=%s)",
            len(_fp_end.get("files", {})),
            (_fp_end.get("root_hash") or "")[:12],
        )
    except Exception as _fpe_end:
        logger.debug("PROJECT-SNAPSHOT: end save: %s", _fpe_end)

    logger.info("orchestrate: итог=%s, фаз=%d, время=%.1fs",
                result.status, result.phases_done, result.seconds)
    # EVENT-LOG: финал прогона
    try:
        log_event(event_type="run_finished", payload={
            "status": result.status,
            "success": bool(result.success),
            "phases_done": result.phases_done,
            "seconds": result.seconds,
            "error": (result.error or "")[:300],
        })
        clear_context()
    except Exception:
        pass

    # BAK-CLEANUP (2026-10-07): после успешного прогона удаляем
    # *.bak файлы в проекте — это мусор от безопасного replace_in_file.
    # Оставляем только если прогон УСПЕШЕН (status=success).
    if result.status == "success":
        try:
            from pathlib import Path as _P
            _base = _P(project_dir)
            _removed = 0
            for _bak in _base.rglob("*.bak"):
                # Не трогаем .ai_link (там свои .stale файлы).
                if ".ai_link" in _bak.parts:
                    continue
                # Не трогаем скрытые папки (.git, .venv).
                if any(part.startswith(".") for part in _bak.parts
                       if part not in (_base.name,)):
                    if ".ai_link" not in _bak.parts:
                        # Разрешаем внутри проекта, но не в .git
                        continue
                try:
                    _bak.unlink()
                    _removed += 1
                except Exception:
                    pass
            if _removed:
                logger.info("BAK-CLEANUP: удалено %d .bak файлов", _removed)
        except Exception as _e:
            logger.debug("BAK-CLEANUP: %s", _e)

    # SQLITE-INDEX: наливаем events.jsonl в глобальный индекс
    # + upsert прогона. Никогда не падает — при ошибке пропускаем.
    try:
        from core.ai_link_db import ingest_events, save_run
        _ing = ingest_events(ctx.project_dir)
        save_run(
            run_id=_run_id,
            project_dir=ctx.project_dir,
            task=ctx.original_task,
            status=result.status,
            final_phase=result.final_phase,
            phases_done=result.phases_done,
            seconds=result.seconds,
            error=(result.error or "")[:500],
            started_at=getattr(result, "started_at", "") or "",
        )
        logger.info(
            "SQLITE-INDEX: ingest events (added=%d, skipped=%d), "
            "run сохранён (%s)",
            _ing.get("added", 0), _ing.get("skipped", 0), _run_id,
        )
    except Exception as _e:
        logger.debug("SQLITE-INDEX: ошибка ingest: %s", _e)

    # COST-LEDGER: разбор events.jsonl в cost_ledger
    # (model_call → model_response пары). Никогда не падает.
    try:
        from core.cost_ledger import ingest_from_events as _cl_ing
        _cl_ing(ctx.project_dir)
    except Exception as _e:
        logger.debug("COST-LEDGER: ошибка ingest: %s", _e)

    if result.error:
        logger.info("orchestrate: причина=%s", result.error[:200])
    logger.info("=" * 60)

    # KEEP-AWAKE: НЕ останавливаем — управляется из main.py.
    # См. BUG-KEEP-AWAKE-PERSISTENT (2026-09-28).

    return result
