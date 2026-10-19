"""Логический анализ проекта через ИИ.

Отправляет .py файлы в DeepSeek/Qwen, просит найти:
  • неиспользуемые функции и классы
  • дублирование логики
  • несоответствия именований
  • потенциальные баги
  • нарушения архитектуры

ВАЖНО: это ПРЕДЛОЖЕНИЯ для рассмотрения человеком,
не автоправки. ИИ может ошибаться.
"""

import json
import re
from pathlib import Path
from typing import Dict, Any, List


SKIP_PARTS = {
    "__pycache__", ".ai_link", ".git", ".venv", "venv",
    "node_modules", ".pytest_cache", ".mypy_cache",
    ".ruff_cache", ".idea", ".vscode", "архивы",
}

MAX_FILE_CHARS = 6000  # файл обрезаем, если он больше


def _should_skip(path: Path, base: Path) -> bool:
    try:
        rel = path.relative_to(base)
    except ValueError:
        return True
    for part in rel.parts:
        if part in SKIP_PARTS:
            return True
        if part.startswith(".") and part not in (".", ""):
            return True
    return False


def _build_analyze_prompt(file_path: str, content: str,
                          context_files: List[str]) -> str:
    if len(content) > MAX_FILE_CHARS:
        content = content[:MAX_FILE_CHARS] + "\n... [файл обрезан]"
    ctx = "\n".join(f"  {f}" for f in context_files[:15]) or "  (нет данных)"
    return (
        "Ты — опытный код-ревьюер. Проанализируй ОДИН файл из проекта.\n\n"
        f"Файл: {file_path}\n\n"
        "Содержимое:\n"
        "```\n"
        f"{content}\n"
        "```\n\n"
        "Другие файлы проекта (для контекста):\n"
        f"{ctx}\n\n"
        "Найди ЛОГИЧЕСКИЕ проблемы:\n"
        "1. Неиспользуемые функции / классы / переменные\n"
        "2. Дублирование логики\n"
        "3. Несоответствия именований\n"
        "4. Потенциальные баги (пустые except, необработанные None и т.п.)\n"
        "5. Нарушения архитектуры\n\n"
        "Отвечай ТОЛЬКО JSON-массивом без пояснений:\n"
        "[\n"
        '  {"severity": "high|medium|low", "line": 15, '
        '"message": "короткое описание"}\n'
        "]\n\n"
        "Если проблем нет — верни [].\n"
        "Это ПРЕДЛОЖЕНИЯ для рассмотрения человеком, не автоправки. "
        "Будь строг — не придирайся по мелочам."
    )


def _parse_ai_response(text: str,
                       return_ok: bool = False):
    """Извлекает JSON-массив из ответа ИИ.

    FIX 2026-09-24: различает «чисто» от «невалидный ответ».
      • ok=True + [] — модель ответила «проблем нет»
      • ok=False + [] — мусор (json/123/etc.)
      • ok=True + [items] — список проблем

    По умолчанию — только list (обратная совместимость).
    С return_ok=True — (list, ok: bool).
    """
    def _ret(items, ok):
        if return_ok:
            return (items, ok)
        return items

    if not text:
        return _ret([], False)

    # Убираем маркер размышления
    for marker in ["Завершено размышление", "Размышление завершено",
                   "Thinking finished", "Thought for"]:
        idx = text.rfind(marker)
        if idx >= 0:
            nl = text.find("\n", idx)
            if nl != -1:
                text = text[nl + 1:]
            break

    # Ищем JSON-массив
    m = re.search(r"\[.*\]", text, re.DOTALL)
    if not m:
        return _ret([], False)

    try:
        data = json.loads(m.group(0))
    except Exception:
        return _ret([], False)

    if not isinstance(data, list):
        return _ret([], False)

    if not data:
        return _ret([], True)  # явное «проблем нет»

    # Элементы должны быть dict с "message"
    for item in data:
        if isinstance(item, dict) and item.get("message"):
            return _ret(data, True)
    return _ret([], False)


def analyze_file(project_dir: str, file_path: str,
                 agent_mode: str = "browser_deepseek") -> Dict[str, Any]:
    """Анализ одного файла. Возвращает {success, issues, error}."""
    path = Path(project_dir) / file_path
    if not path.exists():
        return {"success": False, "issues": [],
                "error": f"файл не найден: {file_path}"}

    try:
        content = path.read_text(encoding="utf-8")
    except Exception as e:
        return {"success": False, "issues": [], "error": str(e)}

    # Контекст: другие файлы проекта
    context_files: List[str] = []
    try:
        from core.project_state import load_state
        state = load_state(project_dir)
        files = state.get("structure", {}).get("files", [])
        context_files = [f["path"] for f in files
                         if f["path"] != file_path and f["path"].endswith(".py")][:15]
    except Exception:
        pass

    site_map = {"browser_qwen": "qwen", "browser_deepseek": "deepseek"}
    site = site_map.get((agent_mode or "").lower())
    if not site:
        return {"success": False, "issues": [],
                "error": f"неизвестный агент: {agent_mode}"}

    prompt = _build_analyze_prompt(file_path, content, context_files)

    try:
        from core.agents.browser_agent import query_browser_ai
        r = query_browser_ai(site, prompt)
    except Exception as e:
        return {"success": False, "issues": [], "error": str(e)}

    if not r.get("success"):
        return {"success": False, "issues": [],
                "error": r.get("error", "агент не ответил")}

    # FIX 2026-09-24: различаем «чисто» и «невалидный ответ»
    raw_issues, parsed_ok = _parse_ai_response(
        r.get("text", ""), return_ok=True)

    if not parsed_ok:
        return {
            "success": False, "issues": [],
            "error": "невалидный ответ LLM (не JSON-массив)",
            "raw_preview": (r.get("text", "") or "")[:200],
        }

    normalized: List[Dict[str, Any]] = []
    for item in raw_issues:
        if not isinstance(item, dict):
            continue
        normalized.append({
            "severity": item.get("severity", "low"),
            "category": "logic",
            "file": file_path,
            "line": int(item.get("line", 0) or 0),
            "message": str(item.get("message", ""))[:200],
        })

    return {"success": True, "issues": normalized, "error": None}


def analyze_project(project_dir: str,
                    agent_mode: str = "browser_deepseek",
                    max_files: int = 5,
                    include_tests: bool = False) -> Dict[str, Any]:
    """Анализирует несколько .py файлов. Возвращает агрегированный отчёт."""
    base = Path(project_dir).resolve()
    py_files: List[str] = []
    for py in sorted(base.rglob("*.py")):
        if _should_skip(py, base):
            continue
        rel = py.relative_to(base).as_posix()
        if not include_tests and rel.startswith("tests/"):
            continue
        py_files.append(rel)

    selected = py_files[:max_files]
    all_issues: List[Dict[str, Any]] = []
    errors: List[Dict[str, str]] = []

    for f in selected:
        r = analyze_file(project_dir, f, agent_mode)
        if r.get("success"):
            all_issues.extend(r["issues"])
        else:
            errors.append({"file": f, "error": r.get("error", "")})

    return {
        "analyzed_files": selected,
        "total_py_files": len(py_files),
        "issues": all_issues,
        "errors": errors,
        "total": len(all_issues),
    }
