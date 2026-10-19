"""Парсер планов AI Link.

Читает файлы `планы/этап_*.md` и превращает их в структуру:
  {
    "title": "Этап 7.5. Диалог с пользователем",
    "status": "in_progress",
    "items": [
      {"text": "7.5.1. core/dialog.py", "status": "done"},
      {"text": "7.5.3. UI-вкладка «Диалог»", "status": "in_progress"},
      ...
    ]
  }

Эмодзи в тексте сохраняются, но для UI используем `status_icon()`,
который возвращает ASCII-метку — Qt лучше её рендерит.
"""

import re
from pathlib import Path
from typing import Dict, Any, List, Optional


# Эмодзи-маркеры статусов (как в планах)
MARKER_DONE = "✅"
MARKER_IN_PROGRESS = "🔄"
MARKER_PENDING = "⏳"
MARKER_CANCELLED = "❌"
MARKER_WARNING = "⚠"

STATUS_MARKERS = [
    (MARKER_DONE, "done"),
    (MARKER_IN_PROGRESS, "in_progress"),
    (MARKER_PENDING, "pending"),
    (MARKER_CANCELLED, "cancelled"),
    (MARKER_WARNING, "warning"),
]


def detect_status(text: str) -> str:
    """Определяет статус по эмодзи-маркеру в строке."""
    if not text:
        return "unknown"
    for marker, status in STATUS_MARKERS:
        if marker in text:
            return status
    return "unknown"


def status_icon(status: str) -> str:
    """Иконка статуса для дерева карты проекта.

    LR-CARD: emoji — красивее, чем [OK]. На Linux Mint
    со шрифтами Noto Color Emoji рендерится корректно.
    """
    return {
        "done": "✅",
        "in_progress": "🔄",
        "pending": "⏳",
        "cancelled": "❌",
        "warning": "⚠️",
        "unknown": "•",
    }.get(status, "•")


def status_label(status: str) -> str:
    """Человеческая метка статуса."""
    return {
        "done": "выполнено",
        "in_progress": "в работе",
        "pending": "ожидает",
        "cancelled": "отменено",
        "warning": "проблема",
    }.get(status, "неизвестно")


def _extract_title(text: str) -> str:
    """Извлекает первый H1-заголовок."""
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("# "):
            return line[2:].strip()
    return ""


def _extract_items(text: str) -> List[Dict[str, str]]:
    """Извлекает подпункты.

    Поддерживает два формата:
      1. '- ✅ 7.5.1. core/dialog.py'
      2. '  ✅ 7.5.1. core/dialog.py'  (с отступом, без дефиса)

    Второй формат часто получается при дописывании планов.
    """
    items: List[Dict[str, str]] = []
    for line in text.splitlines():
        s = line.strip()
        if not s:
            continue

        # Проверяем, начинается ли с маркера
        starts_with_marker = False
        for marker, _ in STATUS_MARKERS:
            if s.startswith(marker):
                starts_with_marker = True
                break

        # Формат 1: "- ..."
        if s.startswith("- "):
            body = s[2:].strip()
        # Формат 2: начинается с маркера статуса
        elif starts_with_marker:
            body = s
        else:
            continue

        status = detect_status(body)
        # Убираем эмодзи из текста
        for marker, _ in STATUS_MARKERS:
            body = body.replace(marker, "").strip()
        # Убираем ведущие разделители
        body = body.lstrip("—-: ").strip()
        if body and len(body) > 3:
            items.append({"text": body, "status": status})
    return items


def parse_plan_file(path: Path) -> Optional[Dict[str, Any]]:
    """Парсит один файл плана."""
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        return None
    title = _extract_title(text)
    if not title:
        return None
    # Статус этапа — из строки «Статус: ...» или из заголовка
    status = "unknown"
    for line in text.splitlines():
        if line.strip().lower().startswith("статус:"):
            status = detect_status(line)
            break
    if status == "unknown":
        status = detect_status(title)
    items = _extract_items(text)
    return {
        "title": title,
        "status": status,
        "items": items,
        "path": str(path),
    }


def parse_all_plans(plans_dir: str) -> List[Dict[str, Any]]:
    """Парсит все `этап_*.md` и `основной.md` в директории планов.

    Сортирует по имени файла — этапы идут по порядку.
    """
    base = Path(plans_dir)
    if not base.exists():
        return []
    result: List[Dict[str, Any]] = []

    # 1. Основной план — как первый этап в дереве
    main_md = base / "основной.md"
    if main_md.exists():
        parsed = parse_plan_file(main_md)
        if parsed:
            parsed["title"] = "AI Link — основной план"
            result.append(parsed)

    # 2. Все этап_*.md, отсортированные по имени
    for f in sorted(base.glob("этап_*.md")):
        parsed = parse_plan_file(f)
        if parsed:
            result.append(parsed)

    return result


def count_by_status(plans: List[Dict[str, Any]]) -> Dict[str, int]:
    """Считает подпункты по статусам (для сводки)."""
    counts = {"done": 0, "in_progress": 0, "pending": 0,
              "cancelled": 0, "warning": 0, "unknown": 0}
    for p in plans:
        for item in p.get("items", []):
            s = item.get("status", "unknown")
            counts[s] = counts.get(s, 0) + 1
    return counts


def parse_project_plan(project_dir: str) -> Optional[Dict[str, Any]]:
    """Читает <project_dir>/.ai_link/plan.json — план пользовательского проекта.

    Возвращает {title, goal, status, stages, items, task} или None.
    stages = [{"name": "...", "items": [{"text": "...", "status": "..."}]}]
    """
    import json as _json
    path = Path(project_dir) / ".ai_link" / "plan.json"
    if not path.exists():
        return None
    try:
        data = _json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(data, dict):
        return None

    stages = data.get("stages", [])
    # Если stages нет, а есть items — превращаем items в один этап
    if not stages and data.get("items"):
        stages = [{"name": "Задачи", "items": data["items"]}]

    return {
        "title": data.get("title", "План проекта"),
        "goal": data.get("goal", ""),
        "status": data.get("status", "in_progress"),
        "stages": stages,
        "items": data.get("items", []),
        "task": data.get("task", ""),
        "created_at": data.get("created_at", ""),
        "finished_at": data.get("finished_at", ""),
        "path": str(path),
    }
