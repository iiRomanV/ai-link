"""AUDIT-PROJECT — аудит существующего проекта.

Сценарий: пользователь открывает проект (свой или чужой) →
Link сканирует → отчёт. На Шаге 1 — только технический аудит
(без LLM): синтаксис, секреты, тесты, снапшот.

Принцип (от Perplexity):
  снимок → доказательства → (потом LLM) → отчёт.

Три режима (пока реализован "fast"):
  • fast — технический аудит (без LLM). 30 сек – 5 мин.
  • full — + LLM-анализ топ-N файлов + описание + идеи.
  • deep — + LLM всех файлов + smoke test.

API:
  run_full_audit(project_dir, mode="fast", max_time_min=30, ...)
  render_audit_md(audit_result) → str
  load_latest_audit(project_dir) → dict

Структура файлов:
  <project>/.ai_link/audits/<audit_id>/
    manifest.json
    technical_checks.json
    security_report.json
    tests_report.json
    audit_report.md
    progress.json
  <project>/.ai_link/latest_audit.json
"""

import hashlib
import json
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List, Optional


# ─────────────────────────────────────────────────────────────
#  Утилиты
# ─────────────────────────────────────────────────────────────

def _audits_dir(project_dir: str) -> Path:
    return Path(project_dir) / ".ai_link" / "audits"


def _latest_path(project_dir: str) -> Path:
    return Path(project_dir) / ".ai_link" / "latest_audit.json"


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _audit_id() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _fingerprint(project_dir: str) -> str:
    """sha256 от отсортированного списка (путь + размер) всех файлов.

    Пропускает: .ai_link, __pycache__, .git, .venv, .pytest_cache.
    """
    skip = {".ai_link", "__pycache__", ".git", ".venv", "venv",
            "node_modules", ".pytest_cache", ".mypy_cache",
            ".ruff_cache"}
    base = Path(project_dir).resolve()
    items: List[str] = []
    try:
        for p in sorted(base.rglob("*")):
            if not p.is_file():
                continue
            rel = p.relative_to(base)
            if any(part in skip or part.startswith(".") for part in rel.parts):
                continue
            # FIX: хешируем СОДЕРЖИМОЕ (первые 4 КБ) + размер.
            # Иначе x=1 и x=2 дают одинаковый fingerprint
            # (оба 6 байт) — баг.
            try:
                size = p.stat().st_size
                try:
                    with open(p, "rb") as f:
                        content = f.read(4096)
                    chash = hashlib.md5(content).hexdigest()[:8]
                except Exception:
                    chash = "err"
                items.append(f"{rel.as_posix()}:{size}:{chash}")
            except Exception:
                continue
    except Exception:
        pass
    h = hashlib.sha256("\n".join(items).encode("utf-8"))
    return h.hexdigest()[:16]


def _git_info(project_dir: str) -> Dict[str, Any]:
    """git commit + dirty, если проект под git."""
    base = Path(project_dir)
    if not (base / ".git").exists():
        return {"git": False, "commit": "", "dirty": False}
    try:
        r1 = subprocess.run(
            ["git", "-C", str(base), "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=5,
        )
        commit = (r1.stdout or "").strip()[:12]
        r2 = subprocess.run(
            ["git", "-C", str(base), "status", "--short"],
            capture_output=True, text=True, timeout=5,
        )
        dirty = bool((r2.stdout or "").strip())
        return {"git": True, "commit": commit, "dirty": dirty}
    except Exception:
        return {"git": False, "commit": "", "dirty": False}


def _count_files(project_dir: str) -> int:
    """Считает файлы (без служебных)."""
    skip = {".ai_link", "__pycache__", ".git", ".venv", "venv",
            "node_modules", ".pytest_cache", ".mypy_cache",
            ".ruff_cache"}
    base = Path(project_dir).resolve()
    n = 0
    try:
        for p in base.rglob("*"):
            if not p.is_file():
                continue
            rel = p.relative_to(base)
            if any(part in skip or part.startswith(".") for part in rel.parts):
                continue
            n += 1
    except Exception:
        pass
    return n


# ─────────────────────────────────────────────────────────────
#  READ-ONLY GUARD: аудит пишет ТОЛЬКО в <project>/.ai_link/
# ─────────────────────────────────────────────────────────────

class AuditWriteViolation(Exception):
    """Аудит попытался записать вне <project>/.ai_link/."""
    pass


def _assert_inside_ai_link(project_dir: str, target: Path) -> None:
    """Проверяет, что target внутри <project>/.ai_link/.

    Вызывается перед любой записью в проекте (кроме
    apply_generated_tests, который по согласию пишет в tests/).
    """
    try:
        base = (Path(project_dir) / ".ai_link").resolve()
        tgt = Path(target).resolve()
        tgt.relative_to(base)
    except Exception:
        raise AuditWriteViolation(
            f"Аудит попытался записать вне .ai_link: {target}"
        )

# ─────────────────────────────────────────────────────────────
#  Снапшот
# ─────────────────────────────────────────────────────────────

def create_snapshot(project_dir: str, audit_id: str) -> Dict[str, Any]:
    """Создаёт manifest.json аудита."""
    manifest = {
        "audit_id": audit_id,
        "project_dir": str(Path(project_dir).resolve()),
        "started_at": _now(),
        "file_count": _count_files(project_dir),
        "project_fingerprint": _fingerprint(project_dir),
    }
    manifest.update(_git_info(project_dir))
    # Сохранить (страж: только .ai_link/)
    d = _audits_dir(project_dir) / audit_id
    _assert_inside_ai_link(project_dir, d)
    try:
        d.mkdir(parents=True, exist_ok=True)
        (d / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception:
        pass
    return manifest


def _files_changed(project_dir: str, old_fp: str) -> bool:
    """True, если файлы изменились с момента снимка."""
    return _fingerprint(project_dir) != old_fp


# ─────────────────────────────────────────────────────────────
#  Проверка секретов
# ─────────────────────────────────────────────────────────────

# Regex-паттерны известных секретов. НЕ отправляем в LLM!
# FIX 2026-09-24: 
#   1. Разрешить кавычки ПОСЛЕ имени поля ("password": "..." вместо password: "...")
#   2. Добавить русские имена: пароль, ключ, токен, секрет, логин
#   3. Ослабить минимум длины значения (6 символов вместо 8)
_SECRET_PATTERNS = [
    ("aws_access_key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("google_api_key", re.compile(r"AIza[0-9A-Za-z_\-]{35}")),
    ("slack_token", re.compile(r"xox[baprs]-[0-9A-Za-z\-]{10,}")),
    ("github_token", re.compile(r"gh[pousr]_[A-Za-z0-9]{36,}")),
    # B52-FINAL (2026-10-12): два формата значения:
    #   • .env-стиль:   KEY=value        (= БЕЗ пробела перед value)
    #   • код/JSON:     key : "value"    (кавычки ОБЯЗАТЕЛЬНЫ)
    # Ловит литералы, не ловит переменные и выражения.
    #   SECRET_KEY=RealSecret123      → ✅ (.env)
    #   password = "hunter2"          → ✅ (кавычки)
    #   "api_key": "eyJjdki..."       → ✅ (JSON)
    #   token = api_key_header        → ❌ (пробел + = без кавычек)
    #   api_key = os.environ.get(...) → ❌ (вызов)
    #   password = keyring.get_pass() → ❌ (вызов)
    ("api_key", re.compile(
        r"(?i)\b(api[_\-]?key|apikey|ключ|токен|token)\b['\"]?"
        r"(?:=(?!\s)([A-Za-z0-9][A-Za-z0-9_\-]{5,})"
        r"|\s*[:=]\s*['\"]([^'\"()\[\]{}\s,=]{6,})['\"])")),
    ("secret", re.compile(
        r"(?i)\b(secret[_\-]?key|secretkey|client[_\-]?secret|"
        r"api[_\-]?secret|db[_\-]?password|database[_\-]?password|"
        r"secret|password|passwd|pwd|пароль|секрет|логин|pass)\b['\"]?"
        r"(?:=(?!\s)([A-Za-z0-9][A-Za-z0-9_\-]{5,})"
        r"|\s*[:=]\s*['\"]([^'\"()\[\]{}\s,=]{6,})['\"])")),
    ("bearer_token", re.compile(r"(?i)bearer\s+[A-Za-z0-9_\-\.]{20,}")),
    ("private_key", re.compile(r"-----BEGIN [A-Z ]+PRIVATE KEY-----")),
]


_SECRET_SKIP_PARTS = {
    "__pycache__", ".ai_link", ".git", ".venv", "venv",
    "node_modules", ".pytest_cache",
}

# FIX 2026-09-24: скрытые файлы, которые НАДО проверять.
# .env — главный файл с секретами, раньше пропускался!
_ALLOW_HIDDEN = {
    ".env", ".env.local", ".env.production", ".env.development",
    ".npmrc", ".pypirc", ".netrc", ".htpasswd", ".dockercfg",
}


# FIX 2026-09-24: false-positive фильтр — демо/плейсхолдеры.
# Безопасность: лучше пропустить placeholder, чем упустить
# реальный секрет. Список минимальный — только очевидное.
# FIX 2026-09-24 (уточнён): только ОЧЕВИДНЫЕ placeholder'ы.
# НЕ включаем: test, demo, пример, xxx, yyy — они могут быть
# частью реального секрета. Лучше false positive, чем пропуск.
_PLACEHOLDER_WORDS = (
    "example", "sample", "placeholder", "changeme", "change_me",
    "dummy", "your_", "your-", "insert_",
)
# Только символы маски (без букв x/X/o/O — они могут быть в ключе)
_MASK_CHARS = set("•·*×●○◯")


def _is_env_var_name(value: str) -> bool:
    """B53 (2026-10-12): True, если значение похоже на ИМЯ env-переменной.

    Примеры (НЕ секреты, а ссылки):
      "GIGACHAT_KEY"        → True
      "MARKETAUX_KEY"       → True
      "NEWS_API_KEY"        → True
      "api_key_var"         → False (lowercase)
      "eyJjdkiOiJqd3Qi..."  → False (не только uppercase)

    Используется для фильтрации маппингов вида
    {"api_key": "GIGACHAT_KEY"} — там имя переменной, не значение.
    """
    if not value or len(value) > 50:
        return False
    # Только A-Z, 0-9, _ — и начинается с буквы
    import re as _re
    return bool(_re.fullmatch(r"[A-Z][A-Z0-9_]+", value))


def _is_placeholder(value: str) -> bool:
    """True, если значение — маска или placeholder.

    Не претендует на полноту: false positive → пропуск,
    что допустимо (лучше меньше шума, чем пропустить реальное).

    ВАЖНО: это эвристика. Она может пропустить реальный
    секрет, если он похож на placeholder (например «test_key_1»).
    Поэтому проверяем ТОЛЬКО когда есть явные признаки.
    """
    if not value:
        return True
    v = value.strip().strip("'\"")
    if len(v) < 4:
        return True
    # Маска: ••••••, ***, xxx
    if all(c in _MASK_CHARS for c in v):
        return True
    # Явные placeholder-слова в значении
    vl = v.lower()
    for w in _PLACEHOLDER_WORDS:
        if w in vl:
            return True
    return False


def check_secrets(project_dir: str,
                  max_files: int = 500) -> Dict[str, Any]:
    """Ищет потенциальные секреты. B52-AST: для .py — AST,
    для остальных — regex через secrets_scanner.

    Возвращает:
      {"findings": [{file, line, kind}], "total": N, "checked_files": M}
    kind — тип секрета: api_key, secret, token, github_token,
    aws_access_key, jwt, ...

    Значения НЕ сохраняются — только факт находки.
    """
    try:
        from core.secrets_scanner import scan_project
        return scan_project(project_dir, max_files=max_files)
    except Exception as e:
        logger.warning("check_secrets: %s", e)
        return {
            "findings": [],
            "total": 0,
            "checked_files": 0,
            "error": str(e)[:200],
        }


# ─────────────────────────────────────────────────────────────
#  Проверка тестов
# ─────────────────────────────────────────────────────────────

def check_tests(project_dir: str,
                timeout: int = 120) -> Dict[str, Any]:
    """Наличие tests/ + запуск pytest, если есть.

    Возвращает:
      {"status": "skipped|passed|failed|unknown",
       "tests_found": N, "passed": N, "failed": N,
       "output_tail": str, "error": str}
    """
    base = Path(project_dir)
    # TESTS-NESTED (2026-10-11, Б46): ищем tests/ в корне и 1 уровне.
    _SKIP = {"node_modules", ".venv", "venv", "__pycache__",
             ".git", ".ai_link", ".pytest_cache"}
    _test_dirs = []
    if (base / "tests").is_dir():
        _test_dirs.append(base / "tests")
    try:
        for _sub in sorted(base.iterdir()):
            if not _sub.is_dir():
                continue
            if _sub.name.startswith(".") or _sub.name in _SKIP:
                continue
            if (_sub / "tests").is_dir():
                _test_dirs.append(_sub / "tests")
    except Exception:
        pass

    if not _test_dirs:
        return {
            "status": "skipped",
            "tests_found": 0, "passed": 0, "failed": 0,
            "output_tail": "", "error": "",
        }

    _rels = []
    for _d in _test_dirs:
        try:
            _rels.append(str(_d.relative_to(base)))
        except Exception:
            _rels.append(str(_d))

    try:
        r = subprocess.run(
            [sys.executable, "-m", "pytest"] + _rels + ["-q",
             "--no-header", "--tb=no"],
            capture_output=True, text=True,
            timeout=timeout, cwd=str(base),
        )
    except subprocess.TimeoutExpired:
        return {
            "status": "failed",
            "tests_found": 0, "passed": 0, "failed": 0,
            "output_tail": f"pytest timeout ({timeout}s)", "error": "",
        }
    except Exception as e:
        return {
            "status": "unknown",
            "tests_found": 0, "passed": 0, "failed": 0,
            "output_tail": "", "error": str(e)[:200],
        }

    output = (r.stdout or "") + (r.stderr or "")
    passed = failed = 0
    for line in output.splitlines():
        line = line.strip()
        if "passed" in line and " in " in line:
            m = re.search(r"(\d+)\s+passed", line)
            if m:
                passed = int(m.group(1))
            m = re.search(r"(\d+)\s+failed", line)
            if m:
                failed = int(m.group(1))
    return {
        "status": "passed" if r.returncode == 0 else "failed",
        "tests_found": passed + failed,
        "passed": passed,
        "failed": failed,
        "output_tail": output[-300:],
        "error": "",
    }




# ─────────────────────────────────────────────────────────────
#  Выбор агента по роли (из agent_assignments.json)
# ─────────────────────────────────────────────────────────────

# Дефолты — если роль не назначена в agent_assignments.json
# ROLES (2026-10-01): hardcoded дефолты УБРАНЫ.
# Только явные назначения пользователя. Не назначено → "".
_DEFAULT_AGENTS_BY_ROLE = {}


def _agent_for_role(role: str,
                    fallback: Optional[str] = None) -> str:
    """Возвращает agent_id для роли.

    Приоритет:
      1. agent_assignments.json → assignments[role]
      2. fallback (если передан)
      3. _DEFAULT_AGENTS_BY_ROLE[role]
      4. "browser_deepseek"

    Пример: _agent_for_role("comprehension") → "browser_qwen"
    (если в UI назначено именно так).
    """
    try:
        from core.capability_benchmark import get_assigned_agent
        assigned = get_assigned_agent(role)
        if assigned:
            return assigned
    except Exception:
        pass
    if fallback:
        return fallback
    return _DEFAULT_AGENTS_BY_ROLE.get(role, "")


# ─────────────────────────────────────────────────────────────
#  LLM-помощники (Шаг 2)
# ─────────────────────────────────────────────────────────────
# ─────────────────────────────────────────────────────────────

# Мок для тестов — если не None, _llm_call возвращает его
_TEST_LLM_STUB = None


def _llm_call(agent_mode: str, prompt: str,
              timeout: int = 120) -> Dict[str, Any]:
    """Обёртка над _call_agent. Возвращает {success, text, error}.

    Используется для всех LLM-вызовов в аудите.
    В тестах подменяется через set_test_llm_stub().
    """
    if _TEST_LLM_STUB is not None:
        return _TEST_LLM_STUB(agent_mode, prompt)
    try:
        from core.agentic_loop import _call_agent
        r = _call_agent(agent_mode, prompt)
        raw = r.get("text", "") or ""
        # FIX 2026-09-24: очистка reasoning Qwen/DeepSeek
        clean = _clean_llm_text(raw)
        return {
            "success": bool(r.get("success")),
            "text": clean,
            "error": r.get("error", ""),
        }
    except Exception as e:
        return {"success": False, "text": "", "error": str(e)[:200]}


# Маркеры reasoning — Qwen/DeepSeek добавляют в начало
_REASONING_MARKERS = (
    "Завершено размышление",
    "Размышление завершено",
    "Thought for",
    "Thinking...",
    "Thinking:",
)

# Маркеры НАЧАЛА reasoning-абзаца (DeepSeek/русские)
_REASONING_START = (
    # English
    "we need", "we should", "we have", "we can", "we must",
    "we will", "let me", "let's", "i need", "i should",
    "i have to", "i will", "the user", "the task",
    "first, let", "first, we", "okay,", "alright,",
    # English (PLANNER-FIX 2026-10-08)
    "secrets are", "the secrets are", "need to", "needs to",
    "stages:", "keep concrete", "keep it concrete",
    "here is", "here's", "i'll", "i can",
    "looking at", "based on", "after reading",
    "the audit", "the report", "the plan",
    # Russian
    "мы должны", "мы можем", "мы видим", "мы хотим",
    "мы будем", "нужно описать", "нужно ответить",
    "нужно написать", "надо описать", "надо ответить",
    "рассмотрим", "попробуем", "давайте", "итак,",
    # Russian (PLANNER-FIX)
    "секреты в", "секрет в", "надо ", "нужно ",
    "на основе", "по результатам", "в проекте",
)


def _clean_llm_text(text: str) -> str:
    """Убирает служебный reasoning-блок в начале ответа LLM.

    Qwen часто пишет «Завершено размышление» + N абзацев
    размышлений, и только потом — реальный ответ.
    """
    if not text:
        return text
    import re as _re

    # Thought for N seconds
    text = _re.sub(
        r"^\s*Thought for\s+\d+\s+seconds?\s*\n+",
        "", text, flags=_re.IGNORECASE,
    )
    # Thinking...
    text = _re.sub(
        r"^\s*Thinking[\.\:]+\s*\n+", "", text,
        flags=_re.IGNORECASE,
    )
    # «Завершено размышление» + следующая строка-заголовок
    # берём ВСЁ после последнего вхождения
    for marker in _REASONING_MARKERS:
        idx = text.rfind(marker)
        if idx >= 0:
            after = text[idx + len(marker):]
            after = after.lstrip("\n\r\t :")
            if after:
                text = after
                break

    # DeepSeek: убираем reasoning-абзацы в начале.
    # Если первый абзац начинается с «Мы должны», «We need»
    # и т.п. — ищем первый абзац, который НЕ начинается с них.
    paras = text.split("\n\n")
    if len(paras) > 1:
        first_low = paras[0].strip().lower()
        is_reasoning_first = any(
            first_low.startswith(m) for m in _REASONING_START)
        if is_reasoning_first:
            for i in range(1, len(paras)):
                cur = paras[i].strip()
                if not cur:
                    continue
                cur_low = cur.lower()
                if any(cur_low.startswith(m)
                       for m in _REASONING_START):
                    continue  # ещё reasoning
                # Нашли первый не-reasoning абзац
                text = "\n\n".join(paras[i:])
                break

    # Дополнительно: если внутри абзаца есть «ДЛЯ ЧЕГО:»,
    # «ЧТО ДЕЛАЕТ:», «ЧТО УМЕЕТ:» — оставляем от первого
    # из этих маркеров (это начало структурированного ответа).
    import re as _re2
    m_struct = _re2.search(
        r"\b(ДЛЯ ЧЕГО|ЧТО ДЕЛАЕТ|ЧТО УМЕЕТ)\s*[:\-]",
        text)
    if m_struct and m_struct.start() > 20:
        # Отрезаем всё до маркера (там reasoning)
        text = text[m_struct.start():]

    return text.strip()


def set_test_llm_stub(fn):
    """Для тестов: подменить _llm_call."""
    global _TEST_LLM_STUB
    _TEST_LLM_STUB = fn


def _content_has_secrets(text: str) -> bool:
    """True, если в тексте есть потенциальные секреты.

    Использует те же _SECRET_PATTERNS, что и check_secrets.
    Placeholder'ы (example, dummy, маски) игнорируются.
    """
    for kind, rx in _SECRET_PATTERNS:
        for m in rx.finditer(text):
            # B52-FINAL: значение в группе 2 (.env) или 3 (код).
            try:
                val = m.group(2) or m.group(3) or ""
            except Exception:
                val = ""
            # Если значения нет (matched только ключ) — пропуск.
            if not val:
                continue
            if _is_placeholder(val):
                continue
            # B53: имя env-переменной — не секрет.
            if _is_env_var_name(val):
                continue
            return True
    return False


def _safe_text(project_dir: str, max_chars: int = 8000) -> str:
    """Безопасная выборка файлов для описания (без data.json, .env)."""
    base = Path(project_dir).resolve()
    skip = {".ai_link", "__pycache__", ".git", ".venv", "venv",
            "node_modules", ".pytest_cache"}
    # Не показываем содержимое этих файлов
    never_read = {".env", ".env.local", ".npmrc", ".pypirc",
                  "data.json", "secrets.json", "credentials.json",
                  "id_rsa", "id_ed25519"}
    parts: List[str] = []
    total = 0
    # Только .py и .md, чтобы описание было релевантным
    for ext in (".py", ".md"):
        for p in sorted(base.rglob(f"*{ext}")):
            if not p.is_file():
                continue
            rel = p.relative_to(base)
            if any(part in skip or part.startswith(".") 
                   for part in rel.parts):
                continue
            if p.name in never_read:
                continue
            try:
                txt = p.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            # FIX 2026-09-24: если в файле есть потенциальные
            # секреты — НЕ показываем содержимое LLM.
            if _content_has_secrets(txt):
                chunk = (
                    f"\n### {rel.as_posix()}\n"
                    "[ФАЙЛ НЕ ПОКАЗАН LLM: содержит "
                    "потенциальные секреты]\n"
                )
            else:
                chunk = f"\n### {rel.as_posix()}\n{txt[:2000]}\n"
            if total + len(chunk) > max_chars:
                break
            parts.append(chunk)
            total += len(chunk)
    return "".join(parts)


def describe_project(project_dir: str,
                     agent_mode: Optional[str] = None
                     ) -> Dict[str, Any]:
    if not agent_mode:
        agent_mode = _agent_for_role("comprehension")
    """LLM: описывает проект — для чего, что делает, что умеет."""
    files_sample = _safe_text(project_dir, max_chars=8000)
    if not files_sample.strip():
        return {"success": True,
                "text": "(файлы не найдены для описания)",
                "error": None}

    prompt = (
        "Опиши проект по его файлам.\n\n"
        "Фрагменты кода:\n"
        f"{files_sample}\n\n"
        "ВАЖНО: НЕ пересказывай это задание, не пиши «Мы должны…», "
        "«We need…», «Нужно описать…». Начни СРАЗУ с ответа.\n"
        "Первая строка должна быть: «ДЛЯ ЧЕГО: …»\n\n"
        "Формат (ровно три блока):\n"
        "ДЛЯ ЧЕГО: <одно предложение>\n"
        "ЧТО ДЕЛАЕТ: <2-3 предложения>\n"
        "ЧТО УМЕЕТ: <2-3 предложения>\n\n"
        "Без JSON, без code-блоков. По-русски, простыми словами."
    )
    return _llm_call(agent_mode, prompt)


def suggest_ideas(project_dir: str,
                  agent_mode: Optional[str] = None
                  ) -> Dict[str, Any]:
    if not agent_mode:
        agent_mode = _agent_for_role("planning")
    """LLM: что можно добавить/улучшить в проекте."""
    files_sample = _safe_text(project_dir, max_chars=8000)
    if not files_sample.strip():
        return {"success": True,
                "text": "(файлы не найдены)",
                "error": None}

    prompt = (
        "Предложи 5-10 идей развития проекта: какие функции, "
        "улучшения, интеграции можно добавить.\n\n"
        "Фрагменты проекта:\n"
        f"{files_sample}\n\n"
        "ВАЖНО: НЕ пересказывай задание, не пиши «We need…», "
        "«Мы должны…», «Нужно предложить…». "
        "Начни СРАЗУ с первой идеи.\n\n"
        "Формат: пронумерованный список (1. … 2. …). "
        "Без JSON, без code-блоков. По-русски, простыми словами."
    )
    return _llm_call(agent_mode, prompt)


def classify_issues(issues: List[Dict[str, Any]],
                    agent_mode: Optional[str] = None
                    ) -> Dict[str, Any]:
    if not agent_mode:
        agent_mode = _agent_for_role("brain")
    """LLM: классифицирует проблемы по уровням.

    Правило (от Perplexity): CRITICAL только если есть evidence
    (traceback, failing test, scanner). Иначе — MAJOR и ниже.

    Возвращает:
      {"success", "classified": [{i, level, reason}], "error"}
    """
    if not issues:
        return {"success": True, "classified": [], "error": None}

    lines = []
    for i, iss in enumerate(issues[:50], 1):
        lines.append(
            f"{i}. [{iss.get('severity','?')}/{iss.get('category','?')}] "
            f"{iss.get('file','?')}:{iss.get('line',0)} — "
            f"{(iss.get('message') or '')[:150]}"
        )
    text = "\n".join(lines)

    prompt = (
        "Ты — senior-ревьюер. Классифицируй проблемы проекта.\n\n"
        "ПРАВИЛА:\n"
        "  CRITICAL — только если есть evidence: traceback, "
        "падение теста, сканер секретов, проверяемая цепочка.\n"
        "  MAJOR — основная функция не работает / ломает логику.\n"
        "  MINOR — измеримый дефект, но не блокирует.\n"
        "  COSMETIC — стиль, форматирование, именование.\n\n"
        "Проблемы:\n"
        f"{text}\n\n"
        "Ответь ТОЛЬКО JSON-массивом:\n"
        '[{"i": 1, "level": "MAJOR", "reason": "кратко"}]\n'
        "Без markdown, без пояснений вне JSON."
    )
    r = _llm_call(agent_mode, prompt)
    if not r.get("success"):
        return {"success": False, "classified": [],
                "error": r.get("error", "LLM не ответил")}

    # Парсим JSON
    import json as _json
    raw = r.get("text", "") or ""
    # Снимаем fence
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1] if "\n" in raw else raw
        if raw.endswith("```"):
            raw = raw[:-3]
        raw = raw.strip()
    try:
        parsed = _json.loads(raw)
    except Exception:
        # Попробуем найти массив в тексте
        import re as _re
        m = _re.search(r"\[.*\]", raw, _re.DOTALL)
        if not m:
            return {"success": False, "classified": [],
                    "error": "не удалось распарсить JSON"}
        try:
            parsed = _json.loads(m.group(0))
        except Exception as e:
            return {"success": False, "classified": [],
                    "error": f"JSON: {e}"}

    if not isinstance(parsed, list):
        return {"success": False, "classified": [],
                "error": "ответ не список"}

    # Нормализуем
    valid_levels = {"CRITICAL", "MAJOR", "MINOR", "COSMETIC"}
    out: List[Dict[str, Any]] = []
    for item in parsed:
        if not isinstance(item, dict):
            continue
        lvl = str(item.get("level", "")).upper()
        if lvl not in valid_levels:
            lvl = "MINOR"
        out.append({
            "i": int(item.get("i", 0) or 0),
            "level": lvl,
            "reason": str(item.get("reason", ""))[:200],
        })

    # FIX 2026-09-24: секреты всегда CRITICAL (evidence — regex)
    for i, iss in enumerate(issues, 1):
        if iss.get("category") == "secret":
            for c in out:
                if c["i"] == i:
                    c["level"] = "CRITICAL"
                    if not c.get("reason"):
                        c["reason"] = "потенциальный секрет"
                    break
            else:
                out.append({
                    "i": i, "level": "CRITICAL",
                    "reason": "потенциальный секрет "
                              "(regex-сканер)",
                })

    return {"success": True, "classified": out, "error": None}


# ─────────────────────────────────────────────────────────────
#  Логический анализ (logic_analyzer с защитой от утечек)
# ─────────────────────────────────────────────────────────────

_TEST_LOGIC_STUB = None


def set_test_logic_stub(fn):
    """Для тестов: подменить _run_logic_analysis."""
    global _TEST_LOGIC_STUB
    _TEST_LOGIC_STUB = fn


def _run_logic_analysis(project_dir: str,
                        mode: str = "full",
                        agent_mode: Optional[str] = None,
                        deadline: float = 0.0
                        ) -> Dict[str, Any]:
    """Логический анализ через logic_analyzer.analyze_file.

    Своя обёртка: logic_analyzer НЕ фильтрует секреты.
    Если файл содержит _SECRET_PATTERNS — в LLM не шлём.

    Приоритет: main.py/app.py → __init__.py → остальные.
    Лимит файлов: full=5, deep=20.
    """
    if _TEST_LOGIC_STUB is not None:
        return _TEST_LOGIC_STUB(project_dir, mode, agent_mode)
    if not agent_mode:
        agent_mode = _agent_for_role("comprehension")

    max_n = 20 if mode == "deep" else 5
    base = Path(project_dir).resolve()
    skip = {".ai_link", "__pycache__", ".git", ".venv", "venv",
            "node_modules", ".pytest_cache", "tests"}

    candidates = []
    for py in sorted(base.rglob("*.py")):
        if not py.is_file():
            continue
        rel = py.relative_to(base)
        if any(part in skip or part.startswith(".")
               for part in rel.parts):
            continue
        name = py.name
        priority = 0
        if name in ("main.py", "app.py", "run.py"):
            priority = -100
        elif name == "__init__.py":
            priority = -50
        candidates.append((priority, rel.as_posix()))

    candidates.sort()
    selected = [f for _, f in candidates[:max_n]]

    all_issues: List[Dict[str, Any]] = []
    errors: List[Dict[str, str]] = []
    skipped_secrets: List[str] = []

    try:
        from core.logic_analyzer import analyze_file
    except Exception as e:
        return {"success": False, "issues": [], "errors": [],
                "skipped_secrets": [],
                "error": f"logic_analyzer недоступен: {e}"}

    for rel in selected:
        if deadline and time.time() > deadline:
            errors.append({"file": rel,
                           "error": "лимит времени исчерпан"})
            break
        try:
            txt = (base / rel).read_text(
                encoding="utf-8", errors="ignore")
        except Exception:
            continue
        if _content_has_secrets(txt):
            skipped_secrets.append(rel)
            continue
        try:
            r = analyze_file(project_dir, rel, agent_mode=agent_mode)
            if r.get("success"):
                all_issues.extend(r.get("issues", []))
            else:
                errors.append({
                    "file": rel,
                    "error": r.get("error", "")[:200],
                })
        except Exception as e:
            errors.append({"file": rel, "error": str(e)[:200]})

    return {
        "success": True,
        "analyzed_files": selected,
        "issues": all_issues,
        "errors": errors,
        "skipped_secrets": skipped_secrets,
        "total": len(all_issues),
    }






# ─────────────────────────────────────────────────────────────
#  Приоритизация тестируемых функций (A15-FILTER)
# ─────────────────────────────────────────────────────────────

# GUI-маркеры: если в теле — skip (нужен дисплей)
_GUI_MARKERS = (
    ".pack(", ".grid(", ".place(", "ttk.", "tkinter",
    "tk.Tk", "tk.Toplevel", "messagebox.", "filedialog.",
    "simpledialog.", "QWidget", "QLayout", "QApplication",
    "QPushButton", "QMessageBox",
)

# I/O-маркеры: если в теле — skip (сложно изолировать)
_IO_MARKERS = (
    "open(", "subprocess.", "os.system(", "socket.",
    "requests.", "urllib.", "shutil.copy", "shutil.move",
    "os.remove(", "os.rmdir(", "psycopg", "sqlite3.connect",
)




def _is_gui_class(node, source_lines: list) -> bool:
    """True, если класс — GUI (Tkinter / PySide / etc).

    Смотрим:
      • базовые классы (QWidget, QMainWindow, tk.Frame, ...)
      • содержимое __init__ (Tk(), QApplication(), ...)
      • любой метод класса содержит tkinter/ttk/PySide вызовы
    """
    import ast as _ast

    # Базы
    for base in node.bases:
        try:
            base_name = _ast.unparse(base)
        except Exception:
            base_name = ""
        low = base_name.lower()
        for kw in ("qwidget", "qmainwindow", "qdialog",
                   "qframe", "qtabwidget", "frame", "tk(",
                   "toplevel", "ctk"):
            if kw in low:
                return True

    # Поиск в теле любого метода
    try:
        end = getattr(node, "end_lineno", node.lineno + 50)
        body_src = "\n".join(
            source_lines[node.lineno - 1:end])
    except Exception:
        body_src = ""

    low_body = body_src.lower()
    for kw in ("tk.tk(", "tk.toplevel(", "qapplication(",
               "qmainwindow(", "= tk(", "root = tk",
               "self.root = tk", "qtabwidget(",
               ".addtab(", ".pack(", ".grid(", ".place("):
        if kw in low_body:
            return True
    return False


def _func_priority(node, source_lines: list,
                   class_name: str) -> str:
    """Возвращает 'high' | 'medium' | 'low' | 'skip'.

    skip — не тестируем (GUI/IO)
    low  — тривиальный getter/сеттер
    medium — обычная функция
    high — метод класса с логикой (≥3 строк тела)
    """
    body = node.body
    if not body:
        return "skip"

    # Собираем исходник тела функции
    try:
        end = getattr(node, "end_lineno",
                      node.lineno + 1) or (node.lineno + 1)
        body_src = "\n".join(source_lines[node.lineno - 1:end])
    except Exception:
        body_src = ""

    # Skip: GUI
    if any(m in body_src for m in _GUI_MARKERS):
        return "skip"
    # Skip: I/O
    if any(m in body_src for m in _IO_MARKERS):
        return "skip"

    # Low: 1 строка = return ...
    if len(body) == 1 and isinstance(body[0], (__import__(
            "ast").Return,)):
        return "low"

    # Low: всё тело ≤2 строк и без вызовов (getter)
    has_call = False
    for stmt in body:
        for sub in __import__("ast").walk(stmt):
            if isinstance(sub, __import__("ast").Call):
                has_call = True
                break
        if has_call:
            break
    if len(body) <= 2 and not has_call:
        return "low"

    # High: метод класса с логикой.
    # FIX: считаем по СТРОКАМ, не по AST-узлам (if/return = 2 узла,
    # но 5 строк исходника).
    if class_name and has_call:
        try:
            span = (getattr(node, "end_lineno", 0) or 0) - node.lineno
        except Exception:
            span = len(body)
        if span >= 3:
            return "high"

    return "medium"


# ─────────────────────────────────────────────────────────────
#  A15. Генерация тестов для проекта (AST-разведка)
# ─────────────────────────────────────────────────────────────
# ─────────────────────────────────────────────────────────────

def _list_testable_functions(project_dir: str,
                             max_n: int = 10) -> List[Dict[str, Any]]:
    """Находит функции-кандидаты для тестов (AST, без LLM).

    Отбирает:
      • top-level функции с 1-3 параметрами
      • методы классов с 1-2 параметрами (без self)
      • ПРОПУСКАЕТ: __dunder__, _private, main, test_*,
        асинхронные, без параметров, с *args/**kwargs

    Приоритет: main.py/app.py → остальные.
    Возвращает список dict:
      {file, name, args, lineno, is_method, class_name}
    """
    import ast

    base = Path(project_dir).resolve()
    skip = {".ai_link", "__pycache__", ".git", ".venv", "venv",
            "node_modules", ".pytest_cache", "tests"}
    cands: List[tuple] = []

    for py in sorted(base.rglob("*.py")):
        if not py.is_file():
            continue
        rel = py.relative_to(base)
        if any(part in skip or part.startswith(".")
               for part in rel.parts):
            continue
        try:
            src_text = py.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        # Файл с секретами не трогаем
        if _content_has_secrets(src_text):
            continue
        try:
            tree = ast.parse(src_text, filename=str(py))
        except SyntaxError:
            continue

        rel_path = rel.as_posix()
        # Приоритет файла (main → -100)
        file_priority = 0
        if py.name in ("main.py", "app.py", "run.py"):
            file_priority = -100

        # Разбить исходник на строки для _func_priority
        source_lines = src_text.split("\n")

        # A15-FILTER: список GUI-классов в этом файле
        gui_classes: set = set()
        for _node in ast.walk(tree):
            if isinstance(_node, ast.ClassDef):
                if _is_gui_class(_node, source_lines):
                    gui_classes.add(_node.name)

        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) or \
               isinstance(node, ast.AsyncFunctionDef):
                # Пропускаем async (не тестируем тут)
                if isinstance(node, ast.AsyncFunctionDef):
                    continue
                name = node.name
                # Пропуск dunder, private, test_*, main
                if name.startswith("__") and name.endswith("__"):
                    continue
                if name.startswith("_"):
                    continue
                if name.startswith("test_"):
                    continue
                if name in ("main", "run", "cli"):
                    continue
                # Аргументы
                args = [a.arg for a in node.args.args
                        if a.arg != "self"]
                if not (1 <= len(args) <= 3):
                    continue
                if node.args.vararg or node.args.kwarg:
                    continue
                if node.args.kwonlyargs:
                    continue
                # Проверка: есть ли хоть что-то в теле?
                if not node.body:
                    continue
                is_method = any(
                    isinstance(p, ast.ClassDef)
                    for p in ast.walk(tree)
                    if node in ast.walk(p)
                )
                class_name = ""
                for p in ast.walk(tree):
                    if isinstance(p, ast.ClassDef):
                        for sub in p.body:
                            if sub is node:
                                class_name = p.name
                                is_method = True
                                break

                # A15-FILTER: если класс GUI — все методы skip
                if class_name and class_name in gui_classes:
                    continue

                # A15-FILTER: приоритет функции
                func_priority = _func_priority(
                    node, source_lines, class_name)
                if func_priority == "skip":
                    continue

                # Числовая сортировка: high=-3, medium=-2, low=-1
                prio_map = {"high": -3, "medium": -2, "low": -1}
                prio_num = prio_map.get(func_priority, -2)

                cands.append((
                    file_priority + prio_num,
                    rel_path, name, args,
                    node.lineno, is_method, class_name,
                    func_priority,
                ))

    cands.sort()
    out: List[Dict[str, Any]] = []
    for (prio, rel, name, args, lineno,
         is_method, class_name, func_priority) in cands[:max_n]:
        out.append({
            "file": rel,
            "name": name,
            "args": args,
            "lineno": lineno,
            "is_method": is_method,
            "class_name": class_name,
            "priority": func_priority,
        })
    return out




def _validate_test_in_isolation(test_code: str,
                                 project_dir: str,
                                 test_filename: str = "test_generated.py",
                                 timeout: int = 30
                                 ) -> Dict[str, Any]:
    """Прогоняет тест в ИЗОЛИРОВАННОЙ копии проекта.

    Не трогает оригинал:
      1. tempfile.mkdtemp → копия файлов проекта (без .git,
         __pycache__, .ai_link, node_modules)
      2. Кладём test_filename в tests/ или в корень
      3. Запускаем pytest
      4. Возвращаем результат, tempfile удаляем

    Возвращает:
      {"ok": bool, "reason": str, "output": str}
      reason: "passed" | "failed" | "error" | "empty_test"
    """
    import tempfile
    import shutil

    # Пустая проверка — тест без assert это не тест
    code_stripped = (test_code or "").strip()
    if not code_stripped:
        return {"ok": False, "reason": "empty_test", "output": ""}
    # Явные пустышки
    if code_stripped in ("pass", "assert True"):
        return {"ok": False, "reason": "empty_test", "output": ""}
    if "assert " not in code_stripped and "pytest.raises" not in code_stripped:
        return {"ok": False, "reason": "empty_test",
                "output": "нет assert"}

    base = Path(project_dir).resolve()
    if not base.exists():
        return {"ok": False, "reason": "error",
                "output": f"project_dir не существует"}

    skip_parts = {".ai_link", "__pycache__", ".git", ".venv",
                  "venv", "node_modules", ".pytest_cache",
                  ".mypy_cache", ".ruff_cache"}

    tmp = tempfile.mkdtemp(prefix="ai_link_test_")
    try:
        # Копируем только .py файлы (без мусора)
        for src_p in base.rglob("*"):
            if not src_p.is_file():
                continue
            rel = src_p.relative_to(base)
            if any(part in skip_parts
                   or part.startswith(".")
                   for part in rel.parts):
                continue
            dst = Path(tmp) / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            try:
                shutil.copy2(src_p, dst)
            except Exception:
                continue

        # Кладём тест в tests/ (или корень)
        tests_dir = Path(tmp) / "tests"
        tests_dir.mkdir(exist_ok=True)
        (tests_dir / "__init__.py").touch()
        test_path = tests_dir / test_filename
        test_path.write_text(test_code, encoding="utf-8")

        # Запускаем pytest
        try:
            r = subprocess.run(
                [sys.executable, "-m", "pytest", str(test_path),
                 "-q", "--no-header", "--tb=short"],
                capture_output=True, text=True,
                timeout=timeout, cwd=tmp,
            )
        except subprocess.TimeoutExpired:
            return {"ok": False, "reason": "error",
                    "output": f"pytest timeout ({timeout}s)"}
        except Exception as e:
            return {"ok": False, "reason": "error",
                    "output": str(e)[:200]}

        output = ((r.stdout or "") + (r.stderr or ""))[-500:]
        if r.returncode == 0:
            return {"ok": True, "reason": "passed", "output": output}
        # Отличаем collection error от падения assert
        if ("errors during collection" in output.lower()
                or "ImportError" in output
                or "ModuleNotFoundError" in output
                or "SyntaxError" in output):
            return {"ok": False, "reason": "error", "output": output}
        return {"ok": False, "reason": "failed", "output": output}
    finally:
        try:
            shutil.rmtree(tmp, ignore_errors=True)
        except Exception:
            pass




# ─────────────────────────────────────────────────────────────
#  A15.3. Генерация тестов для проекта (LLM + валидация)
# ─────────────────────────────────────────────────────────────



def _extract_python_code(raw: str) -> str:
    """Извлекает чистый Python-код из ответа LLM.

    Проблема: DeepSeek мешает reasoning ВНУТРЬ кода
    (между строками, без отступа).

    Стратегия:
      1. Снять markdown-fence
      2. Построчная фильтрация: оставляем только строки,
         которые могут быть Python-кодом
      3. Валидация через compile()
    """
    if not raw:
        return ""
    import re as _re
    text = raw.strip()

    # Шаг 0: снимаем префиксы-маркеры (идея из browser_manager):
    # «json\n123», «Copy\n», «Download\n» — мусор Qwen/DeepSeek.
    text = _re.sub(
        r"^(json|javascript|python|tool_call|bash|sql)\s*\n",
        "", text, flags=_re.IGNORECASE,
    )
    text = _re.sub(
        r"^(Copy|Download|Копировать)\s*\n", "", text,
        flags=_re.IGNORECASE | _re.MULTILINE,
    )

    # Шаг 1: markdown-fence ```python ... ```
    m = _re.search(r"```(?:python)?\s*\n(.*?)```", text, _re.DOTALL)
    if m:
        text = m.group(1).strip()
    else:
        # Убираем одиночные ``` в начале/конце
        if text.startswith("```"):
            text = text.split("\n", 1)[-1]
        if text.endswith("```"):
            text = text.rsplit("```", 1)[0]

    lines = text.split("\n")
    out_lines: list = []
    started = False

    # Маркеры reasoning (начало строки без отступа)
    skip_starts = (
        "we need", "we should", "we can", "we must", "we'll",
        "let me", "let's", "i need", "i'll", "first,",
        "мы должны", "мы можем", "рассмотрим", "итак,",
        "should we", "that's", "this is", "now,", "okay,",
        "alright,", "actually,", "wait,", "but ", "and then",
        "note:", "важно:", "hmm",
    )

    for ln in lines:
        s = ln.rstrip()
        stripped = s.strip()

        # Пустая строка — оставляем как разделитель
        if not stripped:
            if started:
                out_lines.append("")
            continue

        # Строка с отступом (пробел/tab) — внутри функции.
        # Проверяем содержимое: если reasoning — выкидываем.
        if s.startswith((" ", "\t")):
            low = stripped.lower()
            # Reasoning с отступом (внутри функции)
            if any(low.startswith(mk) for mk in skip_starts):
                continue
            # Строка без кода и без символов Python
            # (заканчивается на . ! ? и нет = ( ) [ ] :)
            if stripped.endswith((".", "!", "?")) and not any(
                    c in stripped for c in "=()[]{}"):
                continue
            out_lines.append(s)
            started = True
            continue

        # Top-level: import / from / def / class / @decorator
        if stripped.startswith(("import ", "from ", "def ",
                                 "class ", "@pytest",
                                 "@pytest.mark")):
            out_lines.append(s)
            started = True
            continue

        # Reasoning? — если начинается с маркера или заканчивается
        # на . ! ? и не содержит символов кода
        low = stripped.lower()
        if any(low.startswith(mk) for mk in skip_starts):
            continue
        if stripped.endswith((".", "!", "?")) and \
           not any(c in stripped for c in "=()[]{}"):
            continue
        # Строка без отступа, но похожа на код (например,
        # "assert x == 1" вне функции — редкость) — оставить
        if any(kw in stripped for kw in
               ("import ", "def ", "assert ", "class ",
                "return ", "raise ")):
            out_lines.append(s)
            started = True
            continue
        # Остальное — reasoning, выкинуть

    result = "\n".join(out_lines).strip()
    # Схлопываем 3+ пустых строк в 2
    result = _re.sub(r"\n{3,}", "\n\n", result)

    # Финальная валидация: пробуем скомпилировать
    if not result:
        return ""
    try:
        compile(result, "<test>", "exec")
    except SyntaxError:
        return ""  # не скомпилировалось — не наш код
    return result




def _fix_python_indentation(code: str) -> str:
    """Восстанавливает отступы в коде без отступов.

    DeepSeek в reasoning-режиме пишет код без отступов:
        def test_x():
        assert True
    Восстанавливаем эвристикой: считаем уровень по ':'.
    """
    if not code:
        return code
    lines = code.split("\n")
    out = []
    level = 0
    for raw in lines:
        s = raw.rstrip()
        stripped = s.strip()
        if not stripped:
            out.append("")
            continue
        # Top-level (без отступа)
        if stripped.startswith(("def ", "class ", "@",
                                 "import ", "from ")):
            level = 0
        # elif/else/except/finally — откат на уровень
        if stripped.startswith(("elif ", "else", "except",
                                 "finally")):
            level = max(0, level - 1)
        out.append("    " * level + stripped)
        # Если строка кончается на ':' — следующий блок +1
        if stripped.endswith(":"):
            level += 1
    # Схлопнуть 3+ пустых
    import re as _re
    text = "\n".join(out)
    text = _re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _strip_ui_noise(code: str) -> str:
    """Убирает UI-мусор DeepSeek/Qwen в начале кода."""
    import re as _re
    if not code:
        return code
    text = code
    # Многострочный префикс: python\nCopy\nDownload\n...
    text = _re.sub(
        r"^(python|json|javascript|bash|sql|tool_call|"
        r"Copy|Download|Копировать|Скачать)\s*\n",
        "", text, flags=_re.IGNORECASE,
    )
    # Повторяем, пока чистится (бывает 2-3 подряд)
    for _ in range(5):
        new = _re.sub(
            r"^(python|json|javascript|bash|sql|tool_call|"
            r"Copy|Download|Копировать|Скачать)\s*\n",
            "", text, flags=_re.IGNORECASE,
        )
        if new == text:
            break
        text = new
    # Markdown fence
    text = _re.sub(r"^```(?:python)?\s*\n", "", text)
    text = _re.sub(r"\n```\s*$", "", text)
    return text.strip()


def _generate_test_for(func: Dict[str, Any],
                       agent_mode: Optional[str] = None,
                       project_dir: str = ""
                       ) -> Dict[str, Any]:
    """Просит LLM написать pytest-тест для одной функции.

    Возвращает {success, test_code, error}.
    НЕ применяет тест — только генерирует.
    """
    if not agent_mode:
        agent_mode = _agent_for_role("coding")

    file_rel = func.get("file", "?")
    name = func.get("name", "?")
    args = func.get("args", [])
    class_name = func.get("class_name", "")
    is_method = func.get("is_method", False)

    # FIX 2026-09-24: берём ИМЕННО ЭТУ функцию через AST,
    # а не первые N символов файла. Работает на файлах
    # любого размера (100k строк — AST обходит за секунду).
    snippet = ""
    if project_dir:
        try:
            full = (Path(project_dir) / file_rel).read_text(
                encoding="utf-8", errors="ignore")
            # Защита: файл с секретами — не отправляем
            if _content_has_secrets(full):
                return {"success": False, "test_code": "",
                        "raw_text": "",
                        "error": "файл содержит секреты — "
                                 "генерация пропущена"}

            # AST-вырезка функции
            import ast as _ast
            try:
                tree = _ast.parse(full)
            except SyntaxError:
                tree = None

            if tree is not None:
                found_node = None
                for node in _ast.walk(tree):
                    if (isinstance(node, _ast.FunctionDef)
                            and node.name == name):
                        # Проверим, что это тот же класс (если метод)
                        if class_name:
                            for parent in _ast.walk(tree):
                                if isinstance(parent, _ast.ClassDef):
                                    if parent.name != class_name:
                                        continue
                                    for sub in parent.body:
                                        if sub is node:
                                            found_node = node
                                            break
                                    if found_node:
                                        break
                            if not found_node:
                                found_node = node
                        else:
                            found_node = node
                            break

                if found_node is not None:
                    lines = full.split("\n")
                    start = max(0, found_node.lineno - 1)
                    end = getattr(found_node, "end_lineno",
                                  found_node.lineno + 30) or (
                                      found_node.lineno + 30)
                    snippet_lines = lines[start:end]
                    snippet = "\n".join(snippet_lines)
                    # Функция огромная? Обрезаем
                    if len(snippet) > 2000:
                        snippet = snippet[:2000] + "\n# ... (обрезано)"
            # Fallback: первые 1500
            if not snippet:
                snippet = full[:1500]
        except Exception:
            pass

    # Путь импорта для теста
    if file_rel.endswith(".py"):
        module_path = file_rel[:-3].replace("/", ".")
    else:
        module_path = file_rel

    if is_method and class_name:
        call_hint = (f"Создай экземпляр `{class_name}()` "
                     f"и вызови метод `{name}`.")
        target = f"{class_name}.{name}"
    else:
        call_hint = f"Импортируй `{name}` из `{module_path}`."
        target = name

    prompt = (
        f"Напиши pytest-тест для `{target}`.\n"
        f"Файл: `{file_rel}`, аргументы: "
        f"{', '.join(args) if args else 'нет'}\n\n"
        "Код функции:\n"
        f"```python\n{snippet}\n```\n\n"
        f"{call_hint}\n\n"
        "ТРЕБОВАНИЯ: один тест = один assert; "
        "используй ОДИНАРНЫЕ кавычки для строк "
        "(внутри Python не должно быть двойных кавычек); "
        "не используй random/time/файлы/сеть; "
        "не тестируй GUI/async/main.\n\n"
        "ФОРМАТ ОТВЕТА:\n"
        "1) Сначала можешь рассуждать (как хочешь).\n"
        "2) Затем — строка ровно: ===TEST_CODE===\n"
        "3) Затем — Python-код теста (много строк).\n"
        "4) Конец — строка ровно: ===END===\n\n"
        "Если невозможно — вместо кода: ===SKIP=== причина\n"
    )

    r = _llm_call(agent_mode, prompt)
    if not r.get("success"):
        return {"success": False, "test_code": "",
                "error": r.get("error", "LLM не ответил")}

    raw_original = (r.get("text") or "").strip()

    # FIX 2026-09-24: маркерная разметка (надёжнее JSON)
    # ===TEST_CODE=== ... ===END===
    import re as _re
    m = _re.search(
        r"===TEST_CODE===\s*\n(.*?)(?:\n===END===|$)",
        raw_original, _re.DOTALL,
    )
    if m:
        code = m.group(1).strip()
        # FIX: чистим UI-мусор + восстанавливаем отступы
        code = _strip_ui_noise(code)
        if code:
            try:
                compile(code, "<test>", "exec")
            except SyntaxError:
                # Пробуем нормализовать отступы
                code = _fix_python_indentation(code)
                try:
                    compile(code, "<test>", "exec")
                except SyntaxError as e:
                    return {"success": False, "test_code": "",
                            "raw_text": raw_original,
                            "error": f"syntax: {e}"}
            return {"success": True, "test_code": code,
                    "raw_text": raw_original, "error": None}

    # Skip-маркер
    m = _re.search(r"===SKIP===\s*(.*?)(?:\n|$)",
                   raw_original)
    if m:
        return {"success": False, "test_code": "",
                "raw_text": raw_original,
                "error": f"skip: {m.group(1)[:150]}"}

    # Fallback 1: JSON (может пригодиться)
    import json as _json
    mj = _re.search(r'\{\s*"test_code".*', raw_original, _re.DOTALL)
    if mj:
        try:
            data = _json.loads(mj.group(0).rsplit("\n", 1)[0]
                              if "\n}" not in mj.group(0) else mj.group(0))
            code = data.get("test_code", "")
            if code:
                compile(code, "<test>", "exec")
                return {"success": True, "test_code": code,
                        "raw_text": raw_original, "error": None}
        except Exception:
            pass

    # Fallback 2: построчная экстракция
    raw = _extract_python_code(raw_original)
    if not raw:
        return {"success": False, "test_code": "",
                "raw_text": raw_original,
                "error": "не удалось извлечь код (нет ===TEST_CODE===)"}

    return {"success": True, "test_code": raw,
            "raw_text": raw_original, "error": None}


def generate_tests_for_project(project_dir: str,
                               max_functions: int = 5,
                               agent_mode: Optional[str] = None,
                               deadline: float = 0.0
                               ) -> Dict[str, Any]:
    """A15: полный цикл генерации тестов.

    Шаги:
      1. AST: _list_testable_functions
      2. Для каждой: LLM пишет тест
      3. Валидация в изоляции (_validate_test_in_isolation)
      4. Прошедшие → кандидаты (НЕ применены автоматически)

    Возвращает:
      {
        "success": bool,
        "candidates": [{func, test_code, file_name}],
        "rejected": [{func, reason, error}],
        "total_considered": N,
        "total_candidates": N,
        "total_rejected": N,
      }
    """
    if not agent_mode:
        agent_mode = _agent_for_role("coding")

    fns = _list_testable_functions(project_dir,
                                    max_n=max_functions)
    if not fns:
        return {
            "success": True,
            "candidates": [],
            "rejected": [],
            "total_considered": 0,
            "total_candidates": 0,
            "total_rejected": 0,
            "note": "не найдено функций-кандидатов",
        }

    candidates: List[Dict[str, Any]] = []
    rejected: List[Dict[str, Any]] = []

    for fn in fns:
        if deadline and time.time() > deadline:
            rejected.append({
                "func": fn,
                "reason": "deadline",
                "error": "лимит времени",
            })
            break

        # 1. LLM пишет тест
        gen = _generate_test_for(fn, agent_mode, project_dir)
        if not gen.get("success"):
            rejected.append({
                "func": fn,
                "reason": "generation_failed",
                "error": gen.get("error", ""),
            })
            continue

        test_code = gen["test_code"]

        # 2. Валидация в изоляции
        fname = f"test_generated_{fn['name']}.py"
        val = _validate_test_in_isolation(
            test_code, project_dir, test_filename=fname)
        if not val.get("ok"):
            rejected.append({
                "func": fn,
                "reason": val.get("reason", "failed"),
                "error": val.get("output", "")[:300],
                "test_code": test_code,
                # FIX 2026-09-24: сырой ответ LLM для отладки
                "raw_llm": (gen.get("raw_text") or "")[:500],
            })
            continue

        candidates.append({
            "func": fn,
            "file_name": fname,
            "test_code": test_code,
            "validation_output": val.get("output", "")[:200],
        })

    return {
        "success": True,
        "candidates": candidates,
        "rejected": rejected,
        "total_considered": len(fns),
        "total_candidates": len(candidates),
        "total_rejected": len(rejected),
    }


def apply_generated_tests(project_dir: str,
                          candidates: List[Dict[str, Any]]
                          ) -> Dict[str, Any]:
    """Применяет прошедшие тесты — пишет файлы в tests/.

    Возвращает {applied: [...], failed: [...]}.
    """
    applied: List[str] = []
    failed: List[Dict[str, str]] = []

    tests_dir = Path(project_dir) / "tests"
    try:
        tests_dir.mkdir(parents=True, exist_ok=True)
        if not (tests_dir / "__init__.py").exists():
            (tests_dir / "__init__.py").touch()
    except Exception as e:
        return {"applied": [], "failed": [
            {"file": "tests/", "error": str(e)}]}

    for cand in candidates:
        fname = cand.get("file_name", "")
        code = cand.get("test_code", "")
        if not fname or not code:
            continue
        target = tests_dir / fname
        if target.exists():
            failed.append({"file": fname,
                           "error": "уже существует"})
            continue
        try:
            target.write_text(code, encoding="utf-8")
            applied.append(fname)
        except Exception as e:
            failed.append({"file": fname, "error": str(e)[:200]})

    return {"applied": applied, "failed": failed}


# ─────────────────────────────────────────────────────────────
#  DEPENDENCY SCAN: какие пакеты указаны vs установлены
# ─────────────────────────────────────────────────────────────

def check_dependencies(project_dir: str) -> Dict[str, Any]:
    """Проверяет файлы зависимостей проекта.

    Находит:
      • requirements.txt / requirements*.txt
      • pyproject.toml (PEP 621 + poetry)
      • Pipfile, poetry.lock, uv.lock
      • setup.py / setup.cfg

    Для requirements.txt — сравнивает с установленными пакетами.
    Не запускает pip-audit (он может отсутствовать).

    Возвращает:
      {"files": [найденные файлы],
       "packages": {name: version},
       "missing": [пакеты, не установленные],
       "installed_ok": N, "total": N,
       "error": ""}
    """
    import re as _re
    import json as _json

    base = Path(project_dir).resolve()
    if not base.exists():
        return {"files": [], "packages": {}, "missing": [],
                "installed_ok": 0, "total": 0,
                "error": "project_dir не существует"}

    found_files: List[str] = []
    packages: Dict[str, str] = {}

    # requirements*.txt
    for req in base.glob("requirements*.txt"):
        if not req.is_file():
            continue
        found_files.append(req.name)
        try:
            txt = req.read_text(encoding="utf-8",
                                errors="ignore")
        except Exception:
            continue
        for line in txt.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            # name==version / name>=version / name
            m = _re.match(
                r"^([A-Za-z0-9_\-\.]+)\s*"
                r"(?:[=<>!~]+\s*([^\s;#,]+))?",
                line,
            )
            if m:
                name = m.group(1).lower().replace("_", "-")
                ver = (m.group(2) or "").strip()
                packages[name] = ver

    # pyproject.toml
    pyproj = base / "pyproject.toml"
    if pyproj.is_file():
        found_files.append("pyproject.toml")
        try:
            txt = pyproj.read_text(encoding="utf-8",
                                    errors="ignore")
            # Простой regex (без toml-парсера):
            # dependencies = ["requests>=2.0", ...]
            m = _re.search(
                r"dependencies\s*=\s*\[(.*?)\]",
                txt, _re.DOTALL,
            )
            if m:
                for dep in _re.findall(r'"([^"]+)"', m.group(1)):
                    dm = _re.match(
                        r"([A-Za-z0-9_\-\.]+).*", dep)
                    if dm:
                        name = dm.group(1).lower().replace("_", "-")
                        packages.setdefault(name, "")
        except Exception:
            pass

    # Pipfile
    pf = base / "Pipfile"
    if pf.is_file():
        found_files.append("Pipfile")
        try:
            txt = pf.read_text(encoding="utf-8", errors="ignore")
            m = _re.search(
                r"\[packages\](.*?)(?:\[|$)",
                txt, _re.DOTALL,
            )
            if m:
                for line in m.group(1).splitlines():
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    nm = line.split("=")[0].strip().strip('"')
                    if nm:
                        packages.setdefault(
                            nm.lower().replace("_", "-"), "")
        except Exception:
            pass

    # poetry.lock / uv.lock — просто отметить наличие
    for lf in ("poetry.lock", "uv.lock"):
        if (base / lf).is_file():
            found_files.append(lf)

    # setup.py / setup.cfg — просто отметить
    for sf in ("setup.py", "setup.cfg"):
        if (base / sf).is_file():
            found_files.append(sf)

    if not found_files:
        return {"files": [], "packages": {}, "missing": [],
                "installed_ok": 0, "total": 0, "error": ""}

    # Установленные пакеты
    installed: set = set()
    try:
        r = subprocess.run(
            [sys.executable, "-m", "pip", "list",
             "--format=json"],
            capture_output=True, text=True, timeout=30,
        )
        if r.returncode == 0:
            data = _json.loads(r.stdout)
            installed = {
                item["name"].lower().replace("_", "-")
                for item in data
            }
    except Exception:
        pass

    missing: List[str] = []
    installed_ok = 0
    for name in packages:
        if name in installed:
            installed_ok += 1
        else:
            missing.append(name)

    return {
        "files": sorted(set(found_files)),
        "packages": packages,
        "missing": sorted(missing),
        "installed_ok": installed_ok,
        "total": len(packages),
        "error": "",
    }


# ─────────────────────────────────────────────────────────────
#  BASELINE + COMPARE: сохранить эталон и сравнить
# ─────────────────────────────────────────────────────────────

def _baseline_path(project_dir: str) -> Path:
    return Path(project_dir) / ".ai_link" / "baseline.json"


def _summary_for_baseline(result: Dict[str, Any]) -> Dict[str, Any]:
    """Собирает краткую сводку для baseline."""
    tech = result.get("technical", {})
    sec = result.get("security", {})
    tests = result.get("tests", {})
    logic = result.get("logic_analysis", {})
    deps = result.get("dependencies", {})
    m = result.get("manifest", {})
    return {
        "audit_id": result.get("audit_id", ""),
        "mode": result.get("mode", "fast"),
        "ts": result.get("finished_at",
                        result.get("started_at", "")),
        "fingerprint": m.get("project_fingerprint", ""),
        # FIX: file_count в baseline (для «стало больше/меньше файлов»)
        "file_count": m.get("file_count", 0),
        # Метрики
        "tech_total": tech.get("total", 0),
        "tech_high": tech.get("by_severity", {}).get("high", 0),
        "tech_medium": tech.get("by_severity", {}).get(
            "medium", 0),
        "tech_low": tech.get("by_severity", {}).get("low", 0),
        "secrets_total": sec.get("total", 0),
        "tests_passed": tests.get("passed", 0),
        "tests_failed": tests.get("failed", 0),
        "logic_total": logic.get("total", 0),
        "deps_missing": len(deps.get("missing", [])),
    }


def set_baseline(project_dir: str,
                 audit_id: str = "") -> Dict[str, Any]:
    """Сохраняет аудит как baseline.

    audit_id="" — берёт latest_audit.
    Возвращает {ok, path, error}.
    """
    if not audit_id:
        latest = _latest_path(project_dir)
        if not latest.exists():
            return {"ok": False, "path": "",
                    "error": "нет latest_audit"}
        try:
            info = json.loads(latest.read_text(encoding="utf-8"))
            audit_id = info.get("audit_id", "")
        except Exception as e:
            return {"ok": False, "path": "",
                    "error": f"latest: {e}"}
    if not audit_id:
        return {"ok": False, "path": "",
                "error": "audit_id не задан"}

    audit_dir = _audits_dir(project_dir) / audit_id
    audit_json = audit_dir / "audit.json"
    if not audit_json.exists():
        return {"ok": False, "path": "",
                "error": f"audit.json не найден: {audit_id}"}

    try:
        result = json.loads(audit_json.read_text(encoding="utf-8"))
    except Exception as e:
        return {"ok": False, "path": "",
                "error": f"read: {e}"}

    summary = _summary_for_baseline(result)
    summary["saved_at"] = _now()
    bp = _baseline_path(project_dir)
    try:
        bp.parent.mkdir(parents=True, exist_ok=True)
        bp.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return {"ok": True, "path": str(bp), "error": ""}
    except Exception as e:
        return {"ok": False, "path": "", "error": str(e)}


def get_baseline(project_dir: str) -> Dict[str, Any]:
    """Читает baseline.json ({} если нет)."""
    bp = _baseline_path(project_dir)
    if not bp.exists():
        return {}
    try:
        return json.loads(bp.read_text(encoding="utf-8"))
    except Exception:
        return {}


def clear_baseline(project_dir: str) -> bool:
    """Удаляет baseline.json."""
    bp = _baseline_path(project_dir)
    try:
        if bp.exists():
            bp.unlink()
            return True
    except Exception:
        pass
    return False


def compare_with_baseline(project_dir: str,
                          current_result: Dict[str, Any]
                          ) -> Dict[str, Any]:
    """Сравнивает текущий аудит с baseline.

    Возвращает:
      {"has_baseline": bool,
       "delta": {metric: {before, after, change}},
       "improved": [metrics],
       "worsened": [metrics],
       "error": ""}
    """
    base = get_baseline(project_dir)
    if not base:
        return {"has_baseline": False, "delta": {},
                "improved": [], "worsened": [], "error": ""}

    cur = _summary_for_baseline(current_result)

    # Метрики: чем МЕНЬШЕ, тем лучше (bad_high = чем выше — плохо)
    bad_high_metrics = [
        ("tech_total", "Технические проблемы"),
        ("tech_high", "High-проблемы"),
        ("tech_medium", "Medium-проблемы"),
        ("tech_low", "Low-проблемы"),
        ("secrets_total", "Секреты"),
        ("tests_failed", "Падающие тесты"),
        ("logic_total", "Логические замечания"),
        ("deps_missing", "Отсутствующие пакеты"),
    ]
    # Метрики: чем БОЛЬШЕ, тем лучше
    good_high_metrics = [
        ("tests_passed", "Проходящие тесты"),
    ]
    # Нейтральные метрики (просто показать изменение)
    neutral_metrics = [
        ("file_count", "Файлов в проекте"),
    ]

    delta: Dict[str, Dict[str, Any]] = {}
    improved: List[str] = []
    worsened: List[str] = []

    for key, label in bad_high_metrics:
        b = base.get(key, 0)
        a = cur.get(key, 0)
        ch = a - b
        delta[key] = {"label": label, "before": b,
                      "after": a, "change": ch}
        if ch < 0:
            improved.append(key)
        elif ch > 0:
            worsened.append(key)

    for key, label in good_high_metrics:
        b = base.get(key, 0)
        a = cur.get(key, 0)
        ch = a - b
        delta[key] = {"label": label, "before": b,
                      "after": a, "change": ch}
        if ch > 0:
            improved.append(key)
        elif ch < 0:
            worsened.append(key)

    # Нейтральные: просто показать before/after, не в improved/worsened
    for key, label in neutral_metrics:
        b = base.get(key, 0)
        a = cur.get(key, 0)
        ch = a - b
        delta[key] = {"label": label, "before": b,
                      "after": a, "change": ch,
                      "neutral": True}

    return {
        "has_baseline": True,
        "baseline_audit_id": base.get("audit_id", ""),
        "baseline_ts": base.get("ts", ""),
        "current_audit_id": cur.get("audit_id", ""),
        "delta": delta,
        "improved": improved,
        "worsened": worsened,
        "error": "",
    }


# ─────────────────────────────────────────────────────────────
#  PIP-AUDIT (опционально): уязвимости зависимостей
# ─────────────────────────────────────────────────────────────

def _pip_audit_available() -> bool:
    """True, если pip-audit установлен."""
    try:
        r = subprocess.run(
            [sys.executable, "-m", "pip_audit", "--version"],
            capture_output=True, text=True, timeout=10,
        )
        return r.returncode == 0
    except Exception:
        return False


def check_vulnerabilities(project_dir: str,
                          timeout: int = 60) -> Dict[str, Any]:
    """Запускает pip-audit по requirements.txt (если есть).

    pip-audit — ОПЦИОНАЛЬНО. Если не установлен — вернёт
    {"available": False, ...}, не падает.

    Возвращает:
      {"available": bool,
       "vulnerabilities": [{name, version, id, fix_versions}],
       "total": N, "error": ""}
    """
    base = Path(project_dir).resolve()
    if not base.exists():
        return {"available": False, "vulnerabilities": [],
                "total": 0, "error": "project_dir не существует"}

    if not _pip_audit_available():
        return {"available": False, "vulnerabilities": [],
                "total": 0,
                "error": "pip-audit не установлен "
                         "(pip install pip-audit)"}

    # Есть ли requirements?
    req = base / "requirements.txt"
    if not req.exists():
        # Ищем любой requirements*.txt
        candidates = list(base.glob("requirements*.txt"))
        if not candidates:
            return {"available": True, "vulnerabilities": [],
                    "total": 0,
                    "error": "нет requirements.txt"}
        req = candidates[0]

    try:
        r = subprocess.run(
            [sys.executable, "-m", "pip_audit",
             "-r", str(req), "--format=json"],
            capture_output=True, text=True,
            timeout=timeout, cwd=str(base),
        )
    except subprocess.TimeoutExpired:
        return {"available": True, "vulnerabilities": [],
                "total": 0,
                "error": f"pip-audit timeout ({timeout}s)"}
    except Exception as e:
        return {"available": True, "vulnerabilities": [],
                "total": 0, "error": str(e)[:200]}

    if r.returncode not in (0, 1):  # 0 = OK, 1 = найдены уязвимости
        return {"available": True, "vulnerabilities": [],
                "total": 0,
                "error": f"pip-audit rc={r.returncode}: "
                         f"{(r.stderr or '')[:200]}"}

    import json as _json
    try:
        data = _json.loads(r.stdout)
    except Exception as e:
        return {"available": True, "vulnerabilities": [],
                "total": 0, "error": f"JSON parse: {e}"}

    vulns: List[Dict[str, Any]] = []
    # pip-audit JSON: {"dependencies": [...]}
    deps = data.get("dependencies", []) if isinstance(data, dict) \
        else data
    for dep in deps:
        if not isinstance(dep, dict):
            continue
        name = dep.get("name", "?")
        version = dep.get("version", "?")
        for v in dep.get("vulns", []) or []:
            if not isinstance(v, dict):
                continue
            vulns.append({
                "name": name,
                "version": version,
                "id": v.get("id", "?"),
                "fix_versions": v.get("fix_versions", []),
                "description": (v.get("description") or "")[:150],
            })

    return {
        "available": True,
        "vulnerabilities": vulns,
        "total": len(vulns),
        "error": "",
    }


# ─────────────────────────────────────────────────────────────
#  Главный вход
# ─────────────────────────────────────────────────────────────
# ─────────────────────────────────────────────────────────────
# ─────────────────────────────────────────────────────────────
# ─────────────────────────────────────────────────────────────
# ─────────────────────────────────────────────────────────────
# ─────────────────────────────────────────────────────────────
# ─────────────────────────────────────────────────────────────
# ─────────────────────────────────────────────────────────────

def run_full_audit(project_dir: str,
                   mode: str = "fast",
                   max_time_min: int = 30,
                   max_files_llm: int = 20,
                   generate_tests: bool = False,
                   max_test_functions: int = 5,
                   progress_cb: Optional[Any] = None
                   ) -> Dict[str, Any]:
    """Запускает аудит. На Шаге 1 — только fast-режим.

    mode: "fast" (без LLM) — пока только он.
          "full" / "deep" — заглушка, вернут fast + пометку.

    max_time_min: мягкий лимит (1-180). При превышении —
      сохраняем partial и возвращаем.

    progress_cb(stage_name, pct): колбэк для UI.
    """
    base = Path(project_dir)
    if not base.exists():
        return {
            "status": "failed",
            "error": f"project_dir не существует: {project_dir}",
        }

    # Нормализуем лимит
    try:
        max_time_min = int(max_time_min)
    except Exception:
        max_time_min = 30
    max_time_min = max(1, min(180, max_time_min))
    deadline = time.time() + max_time_min * 60

    _cb = progress_cb or (lambda *a, **kw: None)

    audit_id = _audit_id()
    result: Dict[str, Any] = {
        "status": "running",
        "audit_id": audit_id,
        "mode": mode,
        "max_time_min": max_time_min,
        "started_at": _now(),
    }

    # 1. Снапшот
    _cb("snapshot", 0)
    manifest = create_snapshot(project_dir, audit_id)
    result["manifest"] = manifest

    # 2. Технический аудит
    _cb("technical", 10)
    try:
        from core.auditor import run_audit
        tech = run_audit(project_dir)
    except Exception as e:
        tech = {"total": 0, "by_severity": {}, "by_category": {},
                "issues": [], "error": str(e)[:200]}
    result["technical"] = tech

    # 3. Секреты
    _cb("secrets", 25)
    result["security"] = check_secrets(project_dir)

    # 4. Тесты
    _cb("tests", 40)
    result["tests"] = check_tests(project_dir)

    # Dependency scan (быстро, без LLM)
    _cb("dependencies", 45)
    result["dependencies"] = check_dependencies(project_dir)

    # pip-audit (опционально) — только если есть requirements
    if result["dependencies"].get("files"):
        _cb("vulnerabilities", 48)
        result["vulnerabilities"] = check_vulnerabilities(
            project_dir, timeout=60)

    # Проверка времени
    if time.time() > deadline:
        result["status"] = "partial"
        result["error"] = "достигнут лимит времени"
        _save_audit(project_dir, audit_id, result)
        return result

    # LLM-режимы (Шаг 2)
    # Каждый шаг берёт агента по СВОЕЙ роли из agent_assignments.json
    if mode in ("full", "deep"):
        _cb("llm_description", 50)
        if time.time() > deadline:
            result["status"] = "partial"
            result["error"] = "лимит времени на LLM-описании"
            _save_audit(project_dir, audit_id, result)
            return result
        result["llm_description"] = describe_project(project_dir)

        _cb("llm_ideas", 65)
        if time.time() > deadline:
            result["status"] = "partial"
            result["error"] = "лимит времени на идеях"
            _save_audit(project_dir, audit_id, result)
            return result
        result["llm_ideas"] = suggest_ideas(project_dir)

        _cb("llm_logic", 65)
        if time.time() > deadline:
            result["status"] = "partial"
            result["error"] = "лимит времени на логике"
            _save_audit(project_dir, audit_id, result)
            return result
        result["logic_analysis"] = _run_logic_analysis(
            project_dir, mode=mode, deadline=deadline)

        _cb("llm_classify", 85)
        # FIX 2026-09-24: секреты тоже идут в классификацию.
        # Они автоматически CRITICAL — это evidence (regex).
        issues = list(result.get("technical", {}).get("issues", []))
        for f in result.get("security", {}).get("findings", []):
            issues.append({
                "severity": "high",
                "category": "secret",
                "file": f.get("file", "?"),
                "line": f.get("line", 0),
                "message": f"потенциальный секрет ({f.get('kind','?')})",
            })
        if time.time() > deadline:
            result["status"] = "partial"
            result["error"] = "лимит времени на классификации"
            _save_audit(project_dir, audit_id, result)
            return result
        result["llm_classification"] = classify_issues(issues)

    elif mode == "deep":
        # deep = full + все файлы (позже)
        result["llm_analysis"] = {
            "status": "deep_todo",
            "note": "deep-режим будет в Шаге 2b",
        }

    # A15: генерация тестов (только full/deep, по флагу)
    if generate_tests and mode in ("full", "deep"):
        _cb("generate_tests", 92)
        if time.time() > deadline:
            result["status"] = "partial"
            result["error"] = "лимит времени на генерации тестов"
            _save_audit(project_dir, audit_id, result)
            return result
        try:
            gen = generate_tests_for_project(
                project_dir,
                max_functions=max_test_functions,
                deadline=deadline,
            )
            result["tests_generated"] = gen
        except Exception as e:
            result["tests_generated"] = {
                "success": False,
                "error": str(e)[:200],
                "candidates": [],
                "rejected": [],
            }

    # 5. Проверка race condition
    if _files_changed(project_dir, manifest["project_fingerprint"]):
        result["race_warning"] = (
            "Проект изменился во время проверки. Отчёт "
            "относится к состоянию на момент начала аудита."
        )

    # Сравнение с baseline (если есть)
    try:
        result["baseline_compare"] = compare_with_baseline(
            project_dir, result)
    except Exception as e:
        result["baseline_compare"] = {
            "has_baseline": False, "error": str(e)[:200]}

    result["status"] = "success"
    result["finished_at"] = _now()
    result["seconds"] = round(time.time() - deadline + max_time_min * 60, 1)
    _save_audit(project_dir, audit_id, result)
    _cb("done", 100)
    return result


# ─────────────────────────────────────────────────────────────
#  Сохранение
# ─────────────────────────────────────────────────────────────

def _save_audit(project_dir: str, audit_id: str,
                result: Dict[str, Any]) -> str:
    """Сохраняет результат аудита. Возвращает путь (пусто при ошибке)."""
    try:
        d = _audits_dir(project_dir) / audit_id
        _assert_inside_ai_link(project_dir, d)
        d.mkdir(parents=True, exist_ok=True)
        # Полный результат
        (d / "audit.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        # Отдельные файлы
        if "technical" in result:
            (d / "technical_checks.json").write_text(
                json.dumps(result["technical"], ensure_ascii=False,
                           indent=2),
                encoding="utf-8",
            )
        if "security" in result:
            (d / "security_report.json").write_text(
                json.dumps(result["security"], ensure_ascii=False,
                           indent=2),
                encoding="utf-8",
            )
        if "tests" in result:
            (d / "tests_report.json").write_text(
                json.dumps(result["tests"], ensure_ascii=False,
                           indent=2),
                encoding="utf-8",
            )
        # Markdown-отчёт
        md = render_audit_md(result)
        (d / "audit_report.md").write_text(md, encoding="utf-8")
        # latest
        _latest_path(project_dir).write_text(
            json.dumps({
                "audit_id": audit_id,
                "path": str(d),
                "status": result.get("status"),
                "finished_at": result.get("finished_at",
                                          result.get("started_at")),
            }, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return str(d)
    except Exception:
        return ""


def load_latest_audit(project_dir: str) -> Dict[str, Any]:
    """Читает latest_audit.json ({} если нет)."""
    p = _latest_path(project_dir)
    if not p.exists():
        return {}
    try:
        info = json.loads(p.read_text(encoding="utf-8"))
        path = Path(info.get("path", ""))
        full = path / "audit.json"
        if full.exists():
            return json.loads(full.read_text(encoding="utf-8"))
        return info
    except Exception:
        return {}


# ─────────────────────────────────────────────────────────────
#  Markdown-отчёт
# ─────────────────────────────────────────────────────────────

def render_audit_md(result: Dict[str, Any]) -> str:
    """Рендерит audit_report.md (человекочитаемый)."""
    lines: List[str] = []
    lines.append(f"# Аудит проекта")
    lines.append("")
    lines.append(f"- **audit_id:** `{result.get('audit_id', '?')}`")
    lines.append(f"- **режим:** {result.get('mode', 'fast')}")
    lines.append(f"- **статус:** {result.get('status', '?')}")
    lines.append(f"- **начат:** {result.get('started_at', '?')}")
    if result.get("finished_at"):
        lines.append(f"- **завершён:** {result['finished_at']}")
    lines.append(f"- **лимит времени:** {result.get('max_time_min', '?')} мин")
    lines.append("")

    # Race warning
    if result.get("race_warning"):
        lines.append(f"⚠️ **{result['race_warning']}**")
        lines.append("")

    # Снапшот
    m = result.get("manifest", {})
    lines.append("## Снапшот")
    lines.append(f"- файлов: {m.get('file_count', '?')}")
    lines.append(f"- fingerprint: `{m.get('project_fingerprint', '?')}`")
    if m.get("git"):
        lines.append(f"- git commit: `{m.get('commit', '?')}`"
                     f"{' (dirty)' if m.get('dirty') else ''}")
    else:
        lines.append("- git: не репозиторий")
    lines.append("")

    # ═══════════════════════════════════════════════════════════
    #  РАЗДЕЛ 1: 🔴 ПОДТВЕРЖДЕНО (evidence, не LLM)
    # ═══════════════════════════════════════════════════════════
    tech = result.get("technical", {})
    sec = result.get("security", {})
    tests = result.get("tests", {})
    tech_issues = tech.get("issues", [])

    syntax_issues = [i for i in tech_issues
                     if i.get("category") == "syntax"]
    empty_issues = [i for i in tech_issues
                    if i.get("category") == "empty"]
    link_issues = [i for i in tech_issues
                   if i.get("category") == "broken_link"]
    sec_findings = sec.get("findings", [])
    tests_failed = tests.get("failed", 0)

    confirmed_total = (len(syntax_issues) + len(empty_issues) +
                      len(link_issues) + len(sec_findings) +
                      (1 if tests_failed else 0))

    lines.append(f"## 🟢 ПОДТВЕРЖДЕНО (Python): {confirmed_total}")
    lines.append("_Проверено кодом — не может ошибиться._")
    lines.append("")

    if syntax_issues:
        lines.append(f"### Синтаксические ошибки: "
                     f"{len(syntax_issues)}")
        for iss in syntax_issues[:10]:
            lines.append(
                f"- `{iss.get('file','?')}:{iss.get('line',0)}` — "
                f"{(iss.get('message') or '')[:120]}"
            )
        lines.append("")

    if tests_failed:
        lines.append(f"### Падающие тесты: {tests_failed}")
        lines.append(f"- passed: {tests.get('passed', 0)}, "
                     f"failed: {tests_failed}")
        lines.append("")

    if sec_findings:
        lines.append(f"### Потенциальные секреты: "
                     f"{len(sec_findings)}")
        lines.append("⚠️ **Значения скрыты.** Только факты.")
        for f in sec_findings[:20]:
            lines.append(
                f"- `{f.get('file','?')}:{f.get('line','?')}` — "
                f"{f.get('kind','?')}"
            )
        if len(sec_findings) > 20:
            lines.append(f"… и ещё {len(sec_findings) - 20}.")
        lines.append("")

    if empty_issues:
        lines.append(f"### Пустые файлы: {len(empty_issues)}")
        for iss in empty_issues[:5]:
            lines.append(f"- `{iss.get('file','?')}`")
        lines.append("")

    if link_issues:
        lines.append(f"### Битые ссылки: {len(link_issues)}")
        for iss in link_issues[:5]:
            lines.append(
                f"- `{iss.get('file','?')}:{iss.get('line',0)}` — "
                f"{(iss.get('message') or '')[:120]}"
            )
        lines.append("")

    if confirmed_total == 0:
        lines.append("✅ Подтверждённых проблем не найдено.")
        lines.append("")

    # ═══════════════════════════════════════════════════════════
    #  РАЗДЕЛ 2: 🟠 ЛОГИКА (LLM, требует проверки)
    # ═══════════════════════════════════════════════════════════
    logic = result.get("logic_analysis", {})
    logic_issues = logic.get("issues", [])
    logic_skipped = logic.get("skipped_secrets", [])
    logic_analyzed = logic.get("analyzed_files", [])

    lines.append(f"## 🟠 ЛОГИКА (LLM, может ошибаться): "
                 f"{len(logic_issues)}")
    if logic_analyzed:
        lines.append(f"_Проанализировано файлов: "
                     f"{len(logic_analyzed)}_")
    if logic_skipped:
        lines.append(f"_Пропущено (содержат секреты): "
                     f"{len(logic_skipped)}_")
    lines.append("")
    for iss in logic_issues[:20]:
        lines.append(
            f"- `{iss.get('file','?')}:{iss.get('line',0)}` — "
            f"{(iss.get('message') or '')[:150]}"
        )
    if len(logic_issues) > 20:
        lines.append(f"… и ещё {len(logic_issues) - 20}.")
    if not logic_issues and logic_analyzed:
        lines.append("✅ Логических проблем не найдено.")
    elif not logic_analyzed:
        lines.append("_Не запускалось (fast-режим)._")
    lines.append("")

    # ═══════════════════════════════════════════════════════════
    #  РАЗДЕЛ 3: ⚪ ТЕХДОЛГ (TODO/FIXME)
    # ═══════════════════════════════════════════════════════════
    todo_issues = [i for i in tech_issues
                   if i.get("category") == "todo"]
    lines.append(f"## ⚪ ТЕХДОЛГ: {len(todo_issues)}")
    for iss in todo_issues[:15]:
        lines.append(
            f"- `{iss.get('file','?')}:{iss.get('line',0)}` — "
            f"{(iss.get('message') or '')[:120]}"
        )
    if not todo_issues:
        lines.append("✅ Чисто.")
    lines.append("")

    # ═══════════════════════════════════════════════════════════
    #  ТЕСТЫ — краткая сводка
    # ═══════════════════════════════════════════════════════════
    # 📦 Зависимости
    deps = result.get("dependencies", {})
    if deps and deps.get("files"):
        lines.append("## 📦 Зависимости")
        lines.append(f"- файлов: {len(deps['files'])}")
        for f in deps["files"]:
            lines.append(f"  • `{f}`")
        if deps.get("total"):
            lines.append(f"- пакетов указано: {deps['total']}")
            lines.append(
                f"- установлено: {deps.get('installed_ok', 0)}")
            if deps.get("missing"):
                lines.append(
                    f"- ⚠️ **не установлено: "
                    f"{len(deps['missing'])}**")
                for m in deps["missing"][:10]:
                    lines.append(f"  • `{m}`")
                if len(deps["missing"]) > 10:
                    lines.append(
                        f"  … и ещё "
                        f"{len(deps['missing']) - 10}")
        lines.append("")

    # 🔒 Уязвимости (pip-audit, опционально)
    vuln = result.get("vulnerabilities", {})
    if vuln:
        if vuln.get("available") and vuln.get("total", 0) > 0:
            lines.append(f"## 🔒 Уязвимости: {vuln['total']}")
            lines.append(
                "⚠️ Найдены известные CVE в зависимостях:"
            )
            for v in vuln["vulnerabilities"][:15]:
                fix = v.get("fix_versions", [])
                fix_str = (f" → исправить: "
                           f"{', '.join(fix[:3])}" if fix else "")
                lines.append(
                    f"- `{v['name']}=={v['version']}` "
                    f"— {v['id']}{fix_str}"
                )
            if vuln["total"] > 15:
                lines.append(
                    f"… и ещё {vuln['total'] - 15}.")
            lines.append("")
        elif vuln.get("available") and vuln.get("total", 0) == 0:
            lines.append("## 🔒 Уязвимости: 0")
            lines.append("✅ Известных уязвимостей не найдено.")
            lines.append("")
        elif not vuln.get("available"):
            # pip-audit не установлен — не пишем в отчёт
            pass

    lines.append(f"## Тесты: {tests.get('status', 'unknown')}")
    lines.append(f"- найдено: {tests.get('tests_found', 0)}, "
                 f"passed: {tests.get('passed', 0)}, "
                 f"failed: {tests.get('failed', 0)}")
    if tests.get("error"):
        lines.append(f"- ошибка: {tests['error']}")
    lines.append("")

    # LLM-описание
    if "llm_description" in result:
        d = result["llm_description"]
        lines.append("## Описание проекта (LLM)")
        if d.get("success"):
            lines.append(d.get("text", "").strip())
        else:
            lines.append(f"❌ {d.get('error', 'ошибка')}")
        lines.append("")

    # LLM-идеи
    if "llm_ideas" in result:
        d = result["llm_ideas"]
        lines.append("## Идеи развития (LLM)")
        if d.get("success"):
            lines.append(d.get("text", "").strip())
        else:
            lines.append(f"❌ {d.get('error', 'ошибка')}")
        lines.append("")

    # LLM-классификация
    if "llm_classification" in result:
        d = result["llm_classification"]
        lines.append("## 📊 Итого по уровням (CRITICAL / MAJOR / MINOR / COSMETIC)")
        if d.get("success"):
            cls = d.get("classified", [])
            from collections import Counter
            by_level = Counter(c["level"] for c in cls)
            lines.append(f"- CRITICAL: {by_level.get('CRITICAL', 0)}")
            lines.append(f"- MAJOR: {by_level.get('MAJOR', 0)}")
            lines.append(f"- MINOR: {by_level.get('MINOR', 0)}")
            lines.append(f"- COSMETIC: {by_level.get('COSMETIC', 0)}")
            lines.append("")

            # FIX 2026-09-24: группировка секретов — вместо 22
            # одинаковых строк одна «22 секрета в data.json».
            # Берём исходные issues, чтобы понять, какие i — секреты.
            all_issues_for_cls = []
            tech_iss = result.get("technical", {}).get("issues", [])
            all_issues_for_cls.extend(tech_iss)
            for f in result.get("security", {}).get("findings", []):
                all_issues_for_cls.append({
                    "category": "secret",
                    "file": f.get("file", "?"),
                })
            for iss in result.get("logic_analysis", {}).get("issues", []):
                all_issues_for_cls.append(iss)

            # Индекс i (1-based) → issue
            idx_to_issue = {
                i: iss
                for i, iss in enumerate(all_issues_for_cls, 1)
            }

            # Секреты — группируем по файлу
            secrets_by_file = {}
            other_critical = []
            for c in cls:
                if c["level"] not in ("CRITICAL", "MAJOR"):
                    continue
                iss = idx_to_issue.get(c["i"])
                cat = (iss or {}).get("category", "")
                if cat == "secret":
                    f = (iss or {}).get("file", "?")
                    secrets_by_file[f] = secrets_by_file.get(f, 0) + 1
                else:
                    other_critical.append(c)

            # Строки: группированные секреты
            for fname, n in sorted(secrets_by_file.items()):
                lines.append(
                    f"- 🔴 **CRITICAL** — Потенциальные секреты: "
                    f"{n} шт. в `{fname}` (значения скрыты)"
                )
            # Остальные критичные/важные
            for c in other_critical:
                lines.append(
                    f"- #{c['i']} [{c['level']}] {c['reason']}"
                )
        else:
            lines.append(f"❌ {d.get('error', 'ошибка')}")
        lines.append("")

    # 📌 Сравнение с baseline
    cmp = result.get("baseline_compare", {})
    if cmp and cmp.get("has_baseline"):
        lines.append("## 📌 Сравнение с baseline")
        lines.append(
            f"_Baseline: `{cmp.get('baseline_audit_id','?')}` "
            f"({cmp.get('baseline_ts','?')})_"
        )
        lines.append("")
        delta = cmp.get("delta", {})
        for key, info in delta.items():
            ch = info["change"]
            if info.get("neutral"):
                mark = f"{ch:+d}" if ch else "—"
            elif ch == 0:
                mark = "—"
            elif (key == "tests_passed" and ch > 0) or \
                 (key != "tests_passed" and ch < 0):
                mark = f"✅ {ch:+d}"
            else:
                mark = f"⚠️ {ch:+d}"
            lines.append(
                f"- {info['label']}: "
                f"{info['before']} → {info['after']} "
                f"({mark})"
            )
        if cmp.get("improved"):
            lines.append(
                f"\n✅ Улучшилось: {len(cmp['improved'])} "
                f"метрик"
            )
        if cmp.get("worsened"):
            lines.append(
                f"⚠️ Ухудшилось: {len(cmp['worsened'])} "
                f"метрик"
            )
        lines.append("")

    # A15: сгенерированные тесты
    if "tests_generated" in result:
        tg = result["tests_generated"]
        lines.append("## 🧪 Тесты-кандидаты (A15)")
        if not tg.get("success"):
            lines.append(f"❌ {tg.get('error', 'ошибка')}")
        else:
            cand = tg.get("total_candidates", 0)
            rej = tg.get("total_rejected", 0)
            cons = tg.get("total_considered", 0)
            lines.append(f"- рассмотрено функций: {cons}")
            lines.append(f"- прошли валидацию: {cand}")
            lines.append(f"- отброшено: {rej}")
            if tg.get("candidates"):
                lines.append("")
                lines.append("**Кандидаты:**")
                for c in tg["candidates"][:10]:
                    fn = c.get("func", {})
                    lines.append(
                        f"- `{fn.get('file','?')}:"
                        f"{fn.get('lineno',0)}` — "
                        f"`{fn.get('name','?')}` → "
                        f"`{c.get('file_name','?')}`"
                    )
            if tg.get("note"):
                lines.append(f"_{tg['note']}_")
        lines.append("")

    # Заглушка deep
    if "llm_analysis" in result:
        lines.append("## LLM-анализ")
        lines.append(f"- {result['llm_analysis'].get('note', '—')}")
        lines.append("")

    lines.append("---")
    lines.append(f"_Отчёт сгенерирован {_now()}_")
    return "\n".join(lines)
