"""Project State — память проекта для AI Link.
Хранит состояние проекта между сессиями.
Файл: <project_dir>/.ai_link/state.json
"""

import json
import os
from pathlib import Path
from typing import Dict, Any, List, Optional
from datetime import datetime

STATE_VERSION = "1.0"


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _state_path(project_dir: str) -> Path:
    return Path(project_dir) / ".ai_link" / "state.json"


def _default_state(project_dir: str) -> Dict[str, Any]:
    return {
        "version": STATE_VERSION,
        "project_dir": str(project_dir),
        "created_at": _now(),
        "updated_at": _now(),
        "work": {
            "current_task": "",
            "current_step": "",
            "current_status": "idle",
            "queue": [],
            "known_issues": [],
        },
        "structure": {
            "files": [],
            "symbols": {},
            "dependencies": {},
        },
        "decisions": [],
        "agent_stats": {},
    }


def load_state(project_dir: str) -> Dict[str, Any]:
    """Загружает state.json. Если файла нет — создаёт с пустым состоянием."""
    path = _state_path(project_dir)
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if data.get("version") != STATE_VERSION:
                # Заготовка под будущую миграцию
                data["version"] = STATE_VERSION
            return data
        except Exception:
            pass
    data = _default_state(project_dir)
    save_state(project_dir, data)
    return data


def save_state(project_dir: str, data: Dict[str, Any]) -> None:
    """Атомарно сохраняет state.json (через tmp + os.replace)."""
    path = _state_path(project_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    data["updated_at"] = _now()
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def update_work(project_dir: str, **kwargs) -> None:
    """Обновляет секцию work.

    Примеры:
        update_work(pd, current_task="Добавить фильтр")
        update_work(pd, current_status="in_progress", current_step="правлю ui.py")
    """
    data = load_state(project_dir)
    data.setdefault("work", {})
    for k, v in kwargs.items():
        data["work"][k] = v
    save_state(project_dir, data)


def set_queue(project_dir: str, queue: List[str]) -> None:
    """Устанавливает очередь задач."""
    data = load_state(project_dir)
    data.setdefault("work", {})["queue"] = list(queue)
    save_state(project_dir, data)


def add_known_issue(project_dir: str, issue: str) -> None:
    """Добавляет известную проблему."""
    data = load_state(project_dir)
    data.setdefault("work", {}).setdefault("known_issues", []).append(issue)
    save_state(project_dir, data)


def add_decision(project_dir: str, what: str, why: str, context: str = "") -> None:
    """Добавляет запись в журнал решений (state.json + decision_log.jsonl)."""
    data = load_state(project_dir)
    data.setdefault("decisions", []).append({
        "date": _now(),
        "what": what,
        "why": why,
        "context": context,
    })
    save_state(project_dir, data)
    log_decision(project_dir, what, why, context)


def record_agent_result(project_dir: str, agent: str,
                        success: bool, seconds: float) -> None:
    """Обновляет статистику агента."""
    data = load_state(project_dir)
    stats = data.setdefault("agent_stats", {})
    s = stats.setdefault(agent, {
        "tasks_done": 0,
        "tasks_failed": 0,
        "total_seconds": 0.0,
        "avg_seconds": 0.0,
    })
    if success:
        s["tasks_done"] = s.get("tasks_done", 0) + 1
    else:
        s["tasks_failed"] = s.get("tasks_failed", 0) + 1
    s["total_seconds"] = round(s.get("total_seconds", 0.0) + float(seconds), 2)
    total = s["tasks_done"] + s["tasks_failed"]
    s["avg_seconds"] = round(s["total_seconds"] / total, 2) if total else 0.0
    save_state(project_dir, data)


def update_structure(project_dir: str, files: List[Dict[str, Any]],
                     symbols: Dict[str, Any],
                     dependencies: Dict[str, Any]) -> None:
    """Обновляет секцию structure целиком.

    files        — список {path, purpose, size_bytes, hash, last_modified, status}
    symbols      — {path: {classes: [...], functions: [...], imports: [...]}}
    dependencies — {path: [path1, path2]}
    """
    data = load_state(project_dir)
    data["structure"] = {
        "files": files,
        "symbols": symbols,
        "dependencies": dependencies,
    }
    save_state(project_dir, data)


def get_summary(project_dir: str) -> Dict[str, Any]:
    """Краткая сводка о состоянии проекта (для UI и промптов)."""
    data = load_state(project_dir)
    work = data.get("work", {})
    structure = data.get("structure", {})
    return {
        "current_task": work.get("current_task", ""),
        "current_status": work.get("current_status", "idle"),
        "current_step": work.get("current_step", ""),
        "files_count": len(structure.get("files", [])),
        "queue": work.get("queue", []),
        "known_issues": work.get("known_issues", []),
        "decisions_count": len(data.get("decisions", [])),
        "agents": list(data.get("agent_stats", {}).keys()),
    }


def log_decision(project_dir: str, what: str, why: str, context: str = "") -> None:
    """Дописывает решение в decision_log.jsonl (append-only)."""
    log_path = Path(project_dir) / ".ai_link" / "decision_log.jsonl"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "ts": _now(),
        "what": what,
        "why": why,
        "context": context,
    }
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def load_decisions(project_dir: str, limit: int = 20) -> List[Dict[str, Any]]:
    """Читает последние N решений из decision_log.jsonl."""
    log_path = Path(project_dir) / ".ai_link" / "decision_log.jsonl"
    if not log_path.exists():
        return []
    lines = log_path.read_text(encoding="utf-8").splitlines()
    result = []
    for line in lines[-limit:]:
        line = line.strip()
        if not line:
            continue
        try:
            result.append(json.loads(line))
        except Exception:
            continue
    return result


def build_context(project_dir: str, budget_chars: int = 8000) -> str:
    """Собирает текстовую сводку о проекте для промпта.

    Возвращает строку с секциями: работа, структура, решения,
    очередь, известные проблемы.

    budget_chars — ограничение на длину итоговой строки.
    Если контент не влезает — обрезается по приоритету:
      1. Текущая задача + статус
      2. Известные проблемы
      3. Очередь
      4. Последние решения (до 5)
      5. Ключевые файлы (до 10)
    """
    data = load_state(project_dir)
    work = data.get("work", {})
    structure = data.get("structure", {})
    decisions = data.get("decisions", [])

    lines: List[str] = []
    lines.append("=== ПРОЕКТ ===")
    lines.append(f"Директория: {project_dir}")

    current_task = work.get("current_task", "") or "(не задана)"
    current_status = work.get("current_status", "idle")
    current_step = work.get("current_step", "") or "(нет)"

    lines.append(f"Текущая задача: {current_task}")
    lines.append(f"Статус: {current_status}")
    lines.append(f"Текущий шаг: {current_step}")
    lines.append("")

    known_issues = work.get("known_issues", [])
    if known_issues:
        lines.append("=== ИЗВЕСТНЫЕ ПРОБЛЕМЫ ===")
        for issue in known_issues[:10]:
            lines.append(f"- {issue}")
        lines.append("")

    queue = work.get("queue", [])
    if queue:
        lines.append("=== ОЧЕРЕДЬ ===")
        for i, item in enumerate(queue[:10], 1):
            lines.append(f"{i}. {item}")
        lines.append("")

    if decisions:
        lines.append("=== ПОСЛЕДНИЕ РЕШЕНИЯ ===")
        for d in decisions[-5:]:
            date = d.get("date", "")
            what = d.get("what", "")
            why = d.get("why", "")
            lines.append(f"- [{date}] {what}")
            if why:
                lines.append(f"  Причина: {why}")
        lines.append("")

    # Правила (глобальные + проекта)
    # Лимиты: до 12 правил всего, каждое до 200 символов,
    # общий блок правил до 1500 символов.
    try:
        from core.rules_manager import all_rules
        rules = all_rules(project_dir)
        # AA2: подняли лимиты (было 8/8). Правила про ярлыки
        # запуска — №14, 15 — терялись. Теперь все влезают.
        global_rules = rules.get("global", [])[:20]
        project_rules = rules.get("project", [])[:15]

        def _fmt_rule(entry):
            txt = (entry.get("text", "") or "").strip()
            if len(txt) > 200:
                txt = txt[:197] + "..."
            return f"- {txt}"

        rules_lines = []
        if global_rules or project_rules:
            rules_lines.append("=== ПРАВИЛА ===")
            if global_rules:
                rules_lines.append("Глобальные (для всех проектов):")
                for r in global_rules:
                    rules_lines.append(_fmt_rule(r))
            if project_rules:
                if global_rules:
                    rules_lines.append("")
                rules_lines.append("Проекта:")
                for r in project_rules:
                    rules_lines.append(_fmt_rule(r))

            # Общий лимит на блок правил
            block = "\n".join(rules_lines)
            # AA2: увеличили бюджет блока (было 1500).
            if len(block) > 3000:
                block = block[:2970] + "\n... [правила обрезаны]"
            lines.append(block)
            lines.append("")
    except Exception:
        pass

    files = structure.get("files", [])
    if files:
        lines.append("=== СТРУКТУРА ===")
        lines.append(f"Всего файлов: {len(files)}")
        py_files = [f for f in files if f.get("path", "").endswith(".py")]
        lines.append(f"Python-модулей: {len(py_files)}")
        key_files = [f["path"] for f in files[:10]]
        if key_files:
            lines.append("Ключевые файлы:")
            for p in key_files:
                lines.append(f"  {p}")

    text = "\n".join(lines)

    if len(text) > budget_chars:
        text = text[:budget_chars - 30] + "\n... [обрезано по бюджету]"

    return text
