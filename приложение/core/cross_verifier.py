"""Cross-verification — второе мнение по проблемам review.

Когда review (Qwen) говорит «PROBLEMS», спрашиваем у другой модели
(DeepSeek): это реальная проблема или придирка?

Возвращает:
  confirmed — проблема реальная, нужно fix
  rejected  — так и задумано, fix не нужен
  unknown   — не удалось получить мнение (timeout и т.п.)
"""

from pathlib import Path
from typing import Dict, Any, Optional


# CLEANUP (2026-10-04): DEFAULT_VERIFIERS удалён.
# Раньше — hardcoded маппинг «кто кого проверяет». Из-за него
# Qwen дёргался через cross_verify, даже когда не назначен.
# Теперь: верификатор — только через роль 'verifier'
# в agent_assignments.json (см. get_verifier ниже).

def _is_agent_available(agent: str) -> bool:
    """FIX CROSS-VERIFY (2026-09-29): проверяет, что агент включён.

    Для browser_* смотрит site.enabled в browser_sites.json.
    Для ollama/gigachat (не через browser) — считаем доступными.
    """
    if not agent:
        return False
    if not agent.startswith("browser_"):
        return True
    site_id = agent[len("browser_"):]
    try:
        from core.agents.browser_sites_manager import get_site
        site = get_site(site_id)
        if not site:
            return False
        return bool(site.get("enabled", False))
    except Exception:
        # Не блокируем при ошибке — пусть LLM попробует.
        return True


def get_verifier(review_agent: str,
                 overrides: Optional[Dict[str, str]] = None) -> str:
    """ROLES (2026-10-01): верификатор через роль 'verifier'.

    Убран hardcoded DEFAULT_VERIFIERS (Qwen дёргался даже когда
    не назначен). Теперь — только явное назначение роли 'verifier'
    в agent_assignments.json.

    Если роль 'verifier' не назначена → "" → cross_verify
    пропускается (нет второго мнения).
    """
    # 1. Явное переопределение
    if overrides and review_agent in overrides:
        cand = overrides[review_agent]
        if _is_agent_available(cand) and cand != review_agent:
            return cand

    # 2. Роль 'verifier' из agent_assignments.json
    try:
        from core.capability_benchmark import get_assigned_agent
        assigned = get_assigned_agent("verifier")
        if assigned and assigned != review_agent:
            if _is_agent_available(assigned):
                return assigned
    except Exception:
        pass

    # 3. Не назначено → "" (cross_verify не вызывается)
    return ""


# FIX CROSS-VERIFY (2026-09-29): поддержка VBA и других языков.
# Раньше собирались только *.py → на Excel-проекте верификатор
# видел Python (эталон + тесты) и не находил VBA-код → REJECTED.
_CODE_EXTS = (
    ".py", ".bas", ".frm", ".cls", ".vbs",
    ".js", ".ts", ".sql", ".cs", ".go",
)
_SKIP_DIRS = (
    "__pycache__", ".ai_link", "_reference",
    ".git", "node_modules", ".pytest_cache",
)


def _collect_code(project_dir: str, limit_files: int = 5,
                  limit_chars: int = 3000) -> str:
    """Собирает код проекта (Python + VBA + др.) для контекста.

    FIX: собираем .py, .bas, .frm, .cls, .vbs и др.
    SKIP: _reference (эталон), __pycache__, .ai_link, .git.
    """
    base = Path(project_dir)
    result = []
    total = 0
    for path in sorted(base.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix.lower() not in _CODE_EXTS:
            continue
        if any(p in path.parts for p in _SKIP_DIRS):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        if total + len(text) > limit_chars:
            cut = limit_chars - total
            text = text[:cut] + "\n... [обрезано]"
        rel = path.relative_to(base).as_posix()
        result.append(f"--- {rel} ---\n{text}")
        total += len(text)
        if len(result) >= limit_files or total >= limit_chars:
            break
    return "\n\n".join(result) if result else "(нет кода)"


def _build_verify_prompt(review_output: str, code: str) -> str:
    return (
        "Ты — независимый код-ревьюер. Другая модель нашла проблемы "
        "в проекте. Твоя задача — проверить, реальные ли это проблемы "
        "или она придирается.\n\n"
        "ВОТ ЧТО НАШЛА ДРУГАЯ МОДЕЛЬ:\n"
        f"{review_output[:2000]}\n\n"
        "ВОТ КОД ПРОЕКТА:\n"
        f"{code}\n\n"
        "Вопрос: эти проблемы РЕАЛЬНЫЕ (код надо менять) "
        "или это придирки (код в порядке)?\n\n"
        "ОБЯЗАТЕЛЬНЫЙ ФОРМАТ ОТВЕТА:\n"
        "Первая строка — ОДИН из двух маркеров:\n"
        "  VERDICT: CONFIRMED   (проблемы реальные, надо исправлять)\n"
        "  VERDICT: REJECTED    (придирки, код в порядке)\n\n"
        "Потом 1-3 предложения объяснения.\n"
        "Без JSON, без code-блоков, только текст."
    )


def parse_verdict(text: str) -> str:
    """Разбирает маркер VERDICT: ... в ответе."""
    if not text:
        return "unknown"
    head = text[:400].upper()
    if "VERDICT: CONFIRMED" in head or "VERDICT:CONFIRMED" in head:
        return "confirmed"
    if "VERDICT: REJECTED" in head or "VERDICT:REJECTED" in head:
        return "rejected"
    return "unknown"


def _precheck_missing_files(project_dir: str,
                             review_output: str) -> str:
    """FIX №5: быстрая проверка «файл отсутствует» ДО вызова LLM.

    Если review говорит «CRITICAL: отсутствует db/schema.sql»,
    а файл на диске ЕСТЬ — это ложный CRITICAL, LLM зря тратить
    не надо.

    Возвращает:
      "false_positive" — нашли ложное «отсутствует»
      ""              — не нашли, нужна LLM
    """
    import re as _re
    from pathlib import Path as _P

    if not review_output:
        return ""
    # Маркеры «файл отсутствует»
    missing_markers = (
        "отсутствует", "missing", "не найден", "not found",
        "не существует", "does not exist", "was not created",
        "не создан", "file not",
        "не хватает", "нет файла", "no file",
    )
    low = review_output.lower()
    # Если ни одного маркера — нечего проверять
    if not any(m in low for m in missing_markers):
        return ""

    # Ищем упоминания файлов
    # FIX CROSS-VERIFY: добавлены VBA-расширения + xlsm
    file_pattern = _re.compile(
        r"[A-Za-z0-9_./\-]+\.(?:py|bas|frm|cls|vbs|sql|"
        r"md|txt|json|yaml|yml|toml|desktop|html|css|js|sh|xlsm)",
        _re.IGNORECASE,
    )
    found_files = file_pattern.findall(review_output)
    if not found_files:
        return ""

    base = _P(project_dir)
    for f in set(found_files):
        # Относительный путь от project_dir
        candidates = [
            base / f,
            base / f.lstrip("/"),
        ]
        for c in candidates:
            if c.exists() and c.is_file():
                return "false_positive"
    return ""


def verify_problem(project_dir: str,
                   review_output: str,
                   review_agent: str = "",
                   verifier_override: str = "") -> Dict[str, Any]:
    """Отправляет проблему на второе мнение.

    Возвращает:
        {
          "verdict": "confirmed" | "rejected" | "unknown",
          "verifier": "<agent>",
          "explanation": "<текст ответа>",
          "error": None | "<текст>"
        }
    """
    # FIX №5: pre-check — быстрая проверка «файл отсутствует»,
    # но файл на диске ЕСТЬ. Тогда LLM не нужен.
    _pre = _precheck_missing_files(project_dir, review_output)
    if _pre == "false_positive":
        # FIX №12: pre-check нашёл ложное «отсутствует», но в review
        # могли быть ДРУГИЕ замечания. Отклоняем только если ВСЕ
        # строки review — про «отсутствует». Иначе — не трогаем.
        import re as _re
        _lines = [
            ln.strip() for ln in review_output.splitlines()
            if ln.strip() and _re.match(
                r"^\s*(CRITICAL|MAJOR|MINOR)[\s:]",
                ln, _re.IGNORECASE,
            )
        ]
        _missing_markers = (
            "отсутствует", "missing", "не найден", "not found",
            "не существует", "does not exist", "не создан",
            "не хватает", "нет файла", "no file",
        )
        _all_missing = _lines and all(
            any(m in ln.lower() for m in _missing_markers)
            for ln in _lines
        )
        if _all_missing:
            return {
                "verdict": "rejected",
                "verifier": "precheck",
                "explanation": (
                    "Pre-check: ВСЕ замечания review — ложные "
                    "«файл отсутствует», но файлы есть на диске."
                ),
                "error": None,
            }
        # Есть другие замечания — не отклоняем, пусть LLM решит.
        logger.info(
            "precheck: есть ложные «отсутствует», но и другие "
            "замечания — оставляем для LLM"
        )

    verifier = verifier_override or get_verifier(review_agent)

    # FIX CROSS-VERIFY: если все верификаторы выключены —
    # возвращаем unknown без вызова LLM.
    if not verifier:
        return {
            "verdict": "unknown",
            "verifier": "none",
            "explanation": "",
            "error": "нет доступных агентов для cross-verify",
        }

    site_map = {
        "browser_qwen": "qwen",
        "browser_deepseek": "deepseek",
        "browser_perplexity": "perplexity",
    }
    site = site_map.get(verifier)
    if site is None:
        return {
            "verdict": "unknown",
            "verifier": verifier,
            "explanation": "",
            "error": f"агент {verifier} не поддерживает cross-verification",
        }

    code = _collect_code(project_dir)
    prompt = _build_verify_prompt(review_output, code)

    try:
        from core.agents.browser_agent import query_browser_ai
        r = query_browser_ai(site, prompt)
    except Exception as e:
        return {
            "verdict": "unknown",
            "verifier": verifier,
            "explanation": "",
            "error": str(e)[:300],
        }

    if not r.get("success"):
        return {
            "verdict": "unknown",
            "verifier": verifier,
            "explanation": "",
            "error": (r.get("error") or r.get("reason") or "unknown")[:300],
        }

    text = r.get("text", "")
    return {
        "verdict": parse_verdict(text),
        "verifier": verifier,
        "explanation": text[:1000],
        "error": None,
    }
