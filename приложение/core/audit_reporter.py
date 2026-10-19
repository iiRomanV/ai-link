"""Перевод технического отчёта аудита на человеческий язык через ИИ.

Использует браузерный агент (DeepSeek / Qwen) для объяснения проблем
простыми словами. Результат сохраняется в state.json.
"""

import json
from pathlib import Path
from typing import Dict, Any, List, Optional
from datetime import datetime

from core.project_state import load_state, save_state


def format_issues_for_ai(issues: List[Dict[str, Any]]) -> str:
    """Формирует текстовый список проблем для промпта ИИ."""
    if not issues:
        return "(проблем не найдено)"
    lines = []
    for i, issue in enumerate(issues, 1):
        sev = issue.get("severity", "low")
        cat = issue.get("category", "other")
        f = issue.get("file", "?")
        line = issue.get("line", 0)
        msg = issue.get("message", "")
        lines.append(f"{i}. [{sev}/{cat}] {f}:{line} — {msg}")
    return "\n".join(lines)


def _build_humanize_prompt(project_dir: str, issues: List[Dict[str, Any]]) -> str:
    issues_text = format_issues_for_ai(issues)
    return (
        "Ты — технический писатель. Помоги объяснить проблемы проекта "
        "обычному человеку (не программисту).\n\n"
        f"Проект: {project_dir}\n"
        f"Найдено проблем: {len(issues)}\n\n"
        "Вот список технических проблем:\n"
        f"{issues_text}\n\n"
        "Для каждой проблемы:\n"
        "- объясни простыми словами, что не так;\n"
        "- предложи, что делать (исправить/оставить/удалить);\n"
        "- если это не критично — скажи прямо.\n\n"
        "Отвечай кратко, без программистского жаргона. "
        "Ответ — обычный текст, НЕ JSON, без code-блоков."
    )


def humanize_audit(project_dir: str,
                   technical_report: Dict[str, Any],
                   agent_mode: str = "browser_deepseek") -> Dict[str, Any]:
    """Отправляет технический отчёт ИИ, получает человеческое объяснение.

    Возвращает:
        {"success": bool, "text": str, "error": str | None}
    """
    issues = technical_report.get("issues", [])
    if not issues:
        return {
            "success": True,
            "text": "Проблем не найдено — проект чистый.",
            "error": None,
        }

    site_map = {
        "browser_qwen": "qwen",
        "browser_perplexity": "perplexity",
        "browser_deepseek": "deepseek",
    }
    site = site_map.get((agent_mode or "").lower())
    if not site:
        return {
            "success": False,
            "text": "",
            "error": f"Неизвестный агент для аудита: {agent_mode}",
        }

    try:
        from core.agents.browser_agent import query_browser_ai
        prompt = _build_humanize_prompt(project_dir, issues)
        r = query_browser_ai(site, prompt)
        return {
            "success": bool(r.get("success")),
            "text": r.get("text", ""),
            "error": r.get("error"),
        }
    except Exception as e:
        return {"success": False, "text": "", "error": str(e)}


def save_audit_result(project_dir: str,
                      human_report: str,
                      technical_report: Dict[str, Any]) -> None:
    """Сохраняет результат аудита в state.json (ключ last_audit)."""
    data = load_state(project_dir)
    data["last_audit"] = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "total": technical_report.get("total", 0),
        "by_severity": technical_report.get("by_severity", {}),
        "by_category": technical_report.get("by_category", {}),
        "human_report": human_report,
        "issues": technical_report.get("issues", []),
    }
    save_state(project_dir, data)


def load_audit_result(project_dir: str) -> Optional[Dict[str, Any]]:
    """Читает последний аудит из state.json."""
    data = load_state(project_dir)
    return data.get("last_audit")
