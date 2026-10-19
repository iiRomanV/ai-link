"""Действия над проблемами аудита.

Для каждой проблемы — варианты действий. Пользователь выбирает,
Link выполняет.

ВАЖНО: действие "fix" здесь — ЗАГЛУШКА. Полная реализация
авто-исправления — отдельный шаг (6.7.4b).
"""

from pathlib import Path
from typing import Dict, Any, List


# --- Варианты действий -------------------------------------------------

ACTION_KEEP = "keep"       # оставить как есть
ACTION_SKIP = "skip"       # отложить
ACTION_SHOW = "show"       # показать файл и строку
ACTION_IMPACT = "impact"   # показать, кто зависит
ACTION_FIX = "fix"         # авто-исправление (заглушка)


def prepare_actions(issue: Dict[str, Any]) -> List[Dict[str, str]]:
    """Возвращает список возможных действий для проблемы.

    Формат: [{"id": "fix", "label": "Исправить автоматически"}, ...]
    """
    category = issue.get("category", "")
    severity = issue.get("severity", "low")

    actions: List[Dict[str, str]] = []

    # Авто-исправление — только для "механических" проблем
    if category in ("syntax", "import"):
        actions.append({"id": ACTION_FIX, "label": "Исправить автоматически"})

    # Для всех — посмотреть влияние
    if issue.get("file"):
        actions.append({"id": ACTION_IMPACT, "label": "Показать, кто зависит от файла"})
        actions.append({"id": ACTION_SHOW, "label": "Открыть файл, показать строку"})

    actions.append({"id": ACTION_KEEP, "label": "Оставить как есть"})
    actions.append({"id": ACTION_SKIP, "label": "Отложить (спросить позже)"})

    return actions


# --- Реализации действий -----------------------------------------------

def action_show(project_dir: str, issue: Dict[str, Any]) -> Dict[str, Any]:
    """Возвращает содержимое файла и нужную строку."""
    f = issue.get("file", "")
    if not f:
        return {"success": False, "error": "нет файла в проблеме"}
    path = Path(project_dir) / f
    if not path.exists():
        return {"success": False, "error": f"файл не найден: {f}"}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except Exception as e:
        return {"success": False, "error": str(e)}

    line_no = int(issue.get("line", 0) or 0)
    start = max(0, line_no - 3)
    end = min(len(lines), line_no + 2)

    snippet_lines = []
    for i in range(start, end):
        marker = ">> " if (i + 1) == line_no else "   "
        snippet_lines.append(f"{marker}{i + 1}: {lines[i]}")

    return {
        "success": True,
        "file": f,
        "line": line_no,
        "total_lines": len(lines),
        "snippet": "\n".join(snippet_lines),
    }


def action_impact(project_dir: str, issue: Dict[str, Any]) -> Dict[str, Any]:
    """Показывает, кто зависит от файла и от кого зависит он сам."""
    f = issue.get("file", "")
    if not f:
        return {"success": False, "error": "нет файла в проблеме"}
    try:
        from core.project_indexer import get_dependencies, get_dependents
        deps = get_dependencies(project_dir, f)
        dependents = get_dependents(project_dir, f)
        return {
            "success": True,
            "file": f,
            "dependencies": deps,
            "dependents": dependents,
            "dependents_count": len(dependents),
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


def action_keep(project_dir: str, issue: Dict[str, Any]) -> Dict[str, Any]:
    """Помечает проблему как «принято решение оставить»."""
    try:
        from core.project_state import add_decision
        add_decision(
            project_dir,
            what=f"Оставлено как есть: {issue.get('file')}:{issue.get('line')}",
            why=issue.get("message", ""),
            context="audit_action_keep",
        )
        return {"success": True, "action": "keep"}
    except Exception as e:
        return {"success": False, "error": str(e)}


def action_skip(project_dir: str, issue: Dict[str, Any]) -> Dict[str, Any]:
    """Помечает проблему как «отложено»."""
    try:
        from core.project_state import add_known_issue
        add_known_issue(
            project_dir,
            f"Отложено (аудит): {issue.get('file')}:{issue.get('line')} — "
            f"{issue.get('message', '')}",
        )
        return {"success": True, "action": "skip"}
    except Exception as e:
        return {"success": False, "error": str(e)}


def _build_fix_prompt(file_path: str, original: str,
                      issue: Dict[str, Any]) -> str:
    line = issue.get("line", 0)
    msg = issue.get("message", "")
    cat = issue.get("category", "")
    return (
        f"В проекте найдена проблема:\n"
        f"  Файл: {file_path}\n"
        f"  Строка: {line}\n"
        f"  Категория: {cat}\n"
        f"  Сообщение: {msg}\n\n"
        f"Вот текущее содержимое файла:\n"
        f"```\n{original}\n```\n\n"
        f"Исправь проблему. Верни ответ как JSON в блоке ```json:\n"
        f"  {{\"tool\": \"write_file\", \"path\": \"{file_path}\", "
        f"\"content\": \"<полное исправленное содержимое>\"}}\n\n"
        f"Если не можешь исправить — верни "
        f"{{\"tool\": \"task_complete\", \"message\": \"почему не можешь\"}}.\n"
        f"Никаких объяснений вокруг JSON."
    )


def action_fix(project_dir: str, issue: Dict[str, Any],
               agent_mode: str = "browser_deepseek") -> Dict[str, Any]:
    """Авто-исправление через ИИ с защитой и откатом.

    Шаги:
      1. Проверка: только syntax / import
      2. Чтение файла
      3. Промпт -> ИИ
      4. Валидация через response_validator
      5. Сохранение .bak
      6. Применение
      7. py_compile всего проекта
      8. Если сломано — откат
    """
    f = issue.get("file", "")
    if not f:
        return {"success": False, "error": "нет файла в проблеме"}

    category = issue.get("category", "")
    if category not in ("syntax", "import"):
        return {
            "success": False,
            "error": f"авто-исправление не поддерживается для {category}",
        }

    path = Path(project_dir) / f
    if not path.exists():
        return {"success": False, "error": f"файл не найден: {f}"}

    try:
        original = path.read_text(encoding="utf-8")
    except Exception as e:
        return {"success": False, "error": f"не прочитать файл: {e}"}

    site_map = {
        "browser_qwen": "qwen",
        "browser_deepseek": "deepseek",
    }
    site = site_map.get((agent_mode or "").lower())
    if not site:
        return {"success": False, "error": f"неизвестный агент: {agent_mode}"}

    prompt = _build_fix_prompt(f, original, issue)

    try:
        from core.agents.browser_agent import query_browser_ai
        r = query_browser_ai(site, prompt)
    except Exception as e:
        return {"success": False, "error": f"ошибка вызова агента: {e}"}

    if not r.get("success"):
        return {"success": False, "error": r.get("error", "агент не ответил")}

    ai_text = r.get("text", "")

    from core.response_validator import validate_response
    validation = validate_response(ai_text, project_dir, task="fix")
    if not validation.get("ok"):
        return {
            "success": False,
            "error": f"ответ ИИ не валиден: {validation.get('error')}",
        }

    if validation.get("kind") == "task_complete":
        return {
            "success": False,
            "error": "ИИ не смог исправить",
            "message": validation.get("message", ""),
        }

    action = validation.get("action", {})
    action_type = action.get("type")
    if action_type not in ("create_file", "write_file", "replace_in_file"):
        return {"success": False, "error": f"неожиданный тип: {action_type}"}

    # .bak
    bak_path = path.with_suffix(path.suffix + ".bak")
    try:
        bak_path.write_text(original, encoding="utf-8")
    except Exception as e:
        return {"success": False, "error": f"не создать .bak: {e}"}

    # Применение
    try:
        if action_type in ("create_file", "write_file"):
            new_content = action.get("content", "")
            path.write_text(new_content, encoding="utf-8")
        else:
            find_text = action.get("find", "")
            replace_text = action.get("replace", "")
            if find_text not in original:
                return {
                    "success": False,
                    "error": "текст для замены не найден в файле",
                }
            new_content = original.replace(find_text, replace_text, 1)
            path.write_text(new_content, encoding="utf-8")
    except Exception as e:
        try:
            path.write_text(original, encoding="utf-8")
        except Exception:
            pass
        return {"success": False, "error": f"не записать файл: {e}"}

    # Проверка синтаксиса всего проекта
    try:
        from core.auditor import check_syntax
        syntax_errors = check_syntax(project_dir)
    except Exception:
        syntax_errors = []

    if syntax_errors:
        try:
            path.write_text(original, encoding="utf-8")
        except Exception:
            pass
        return {
            "success": False,
            "error": "после правки появились синтаксические ошибки — откат",
            "remaining_errors": syntax_errors[:3],
        }

    return {
        "success": True,
        "action": "fix",
        "file": f,
        "applied": action_type,
        "bak_path": str(bak_path.relative_to(project_dir)),
    }


# --- Точка входа -------------------------------------------------------

_ACTION_HANDLERS = {
    ACTION_SHOW: action_show,
    ACTION_IMPACT: action_impact,
    ACTION_KEEP: action_keep,
    ACTION_SKIP: action_skip,
    ACTION_FIX: action_fix,
}


def apply_action(project_dir: str, issue: Dict[str, Any],
                 action_id: str) -> Dict[str, Any]:
    """Применяет действие к проблеме."""
    handler = _ACTION_HANDLERS.get(action_id)
    if handler is None:
        return {"success": False, "error": f"неизвестное действие: {action_id}"}
    return handler(project_dir, issue)
