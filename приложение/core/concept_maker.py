"""CONCEPT: генерация concept.md по диалогу проекта.

Отделяет «мозг концепции» от браузерного диалога.
Link берёт dialog.jsonl сам, зовёт выбранного агента
(из agent_assignments.json → concept), пишет concept.md.

Не занимает браузер, не засоряет dialog.jsonl.
"""
import json
import logging
import re
from pathlib import Path
from typing import Tuple

logger = logging.getLogger(__name__)


CONCEPT_PROMPT = (
    "⚠️ ВАЖНО: ответь ТОЛЬКО текстом в формате markdown.\n"
    "  • НЕ оборачивай ответ в JSON.\n"
    "  • НЕ пиши [{{\"action\": \"save\", \"data\": ...}}] — "
    "это запрещено.\n"
    "  • НЕ используй code-блоки ```json / ```markdown.\n"
    "  • Первая строка ответа должна быть «## Платформа».\n"
    "  • Никаких вводных слов, объяснений, предисловий.\n"
    "\n"
    "Ты — Link. Ниже диалог пользователя с ассистентом "
    "по проекту. Извлеки из него СТРУКТУРИРОВАННУЮ "
    "КОНЦЕПЦИЮ проекта в формате markdown.\n"
    "\n"
    "ФОРМАТ (строго такой, заголовки именно через ##):\n"
    "## Платформа\n"
    "## Пользователи\n"
    "## Функции первой версии\n"
    "## Хранение данных\n"
    "## Ограничения\n"
    "## Приоритеты\n"
    "\n"
    "Если чего-то нет в диалоге — пиши «(уточнить)».\n"
    "Никаких пояснений, кода, JSON, вводных слов.\n"
    "\n"
    "ПРИМЕР ПРАВИЛЬНОГО НАЧАЛА ОТВЕТА (не копируй, это образец):\n"
    "## Платформа\n"
    "Linux, десктопное GUI-приложение на Qt.\n"
    "\n"
    "## Пользователи\n"
    "Личное использование.\n"
    "\n"
    "=== ДИАЛОГ ===\n"
    "{dialog}\n"
    "=== КОНЕЦ ДИАЛОГА ===\n"
    "\n"
    "⚠️ НАПОМИНАЮ: ответ — только markdown, без JSON-обёртки.\n"
    "Первая строка: «## Платформа».\n"
)


def _read_dialog(project_dir: str, limit: int = 30) -> str:
    """Читает dialog.jsonl, фильтрует служебные сообщения."""
    p = Path(project_dir) / ".ai_link" / "dialog.jsonl"
    if not p.exists():
        return ""
    lines = p.read_text(encoding="utf-8").splitlines()
    out = []
    skip = {
        "Думаю над ответом...",
        "📝 Формирую концепцию проекта...",
        "✅ Концепция сохранена в concept.md",
    }
    for line in lines[-limit:]:
        try:
            m = json.loads(line)
        except Exception:
            continue
        role = m.get("role", "")
        text = (m.get("text") or "").strip()
        if not text:
            continue
        if role == "link" and (text in skip or len(text) < 5):
            continue
        who = "Вы" if role == "user" else "Link"
        out.append(f"{who}: {text}")
    return "\n\n".join(out)


def _clean_concept(text: str) -> str:
    """Убирает reasoning, служебные строки и JSON-обёртку.

    GigaChat иногда возвращает концепцию в JSON-конверте:
        [{"action": "save", "data": "# Платформа\n..."}]
    Распаковываем поле "data".
    """
    if not text:
        return ""
    text = re.sub(
        r"^\s*Thought for\s+\d+\s+seconds?\s*\n+",
        "", text, flags=re.IGNORECASE,
    )
    text = re.sub(
        r"^\s*Thinking[\.\:]+\s*\n+", "", text,
        flags=re.IGNORECASE,
    )

    # UNWRAP: JSON-обёртка от GigaChat.
    # Сначала убираем markdown-fence ```json ... ``` (если есть),
    # потом проверяем, не JSON ли это.
    stripped = text.strip()

    # Срезаем markdown-fence в начале и конце
    stripped = re.sub(
        r"^```(?:json|markdown)?\s*\n?", "", stripped,
        flags=re.IGNORECASE,
    )
    stripped = re.sub(r"\n?```\s*$", "", stripped).strip()

    # Теперь проверяем JSON
    if stripped.startswith("[") or stripped.startswith("{"):
        try:
            data = json.loads(stripped)
            if isinstance(data, list) and data:
                data = data[0]
            if isinstance(data, dict):
                inner = data.get("data")
                if isinstance(inner, str) and inner.strip():
                    text = inner.strip()
        except Exception:
            pass

    return text.strip()


def generate_concept(project_dir: str) -> Tuple[str, str]:
    """Генерирует концепцию.

    Возвращает (text, source_agent_id).

    Raises:
        RuntimeError: если агент не выбран или не смог ответить.
    """
    from core.orchestrator import get_phase_agent, PHASE_CONCEPT

    agent = get_phase_agent(PHASE_CONCEPT)
    if not agent:
        raise RuntimeError(
            "Агент для концепции не выбран. "
            "Настройки → Назначения агентов → «Концепция»."
        )

    dialog = _read_dialog(project_dir)
    if not dialog:
        raise RuntimeError(
            "Не удалось прочитать историю диалога (dialog.jsonl)."
        )

    prompt = CONCEPT_PROMPT.format(dialog=dialog)

    if agent == "gigachat":
        from core.gigachat_adapter import query_gigachat
        r = query_gigachat(prompt)
    elif agent == "ollama":
        from core.ollama_adapter import query_ollama
        r = query_ollama(prompt)
    elif agent.startswith("browser_"):
        from core.agents.browser_agent import query_browser_ai
        site = agent[len("browser_"):]
        r = query_browser_ai(site, prompt)
    else:
        raise RuntimeError(f"Неизвестный агент для концепции: {agent}")

    if not r.get("success"):
        err = r.get("error") or r.get("reason") or "unknown"
        raise RuntimeError(
            f"Агент {agent} не смог сформировать концепцию: {err}"
        )

    text = _clean_concept(r.get("text", "") or "")
    if not text:
        raise RuntimeError(f"Агент {agent} вернул пустой ответ.")

    return text, agent
