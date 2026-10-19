"""Модель данных диалога AI Link с пользователем.

Диалог привязан к проекту. Хранится в <project_dir>/.ai_link/dialog.jsonl
(append-only — как review_log).

Пока только данные. UI и фоновые потоки — в следующих шагах.
"""

import json
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Dict, Any, List, Optional
from datetime import datetime


# Роли в диалоге
ROLE_USER = "user"           # пользователь
ROLE_LINK = "link"           # Link (сам ассистент)
ROLE_SYSTEM = "system"       # системные события (start, stop, warning)


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _dialog_path(project_dir: str) -> Path:
    return Path(project_dir) / ".ai_link" / "dialog.jsonl"


# === Датаклассы ===

@dataclass
class DialogMessage:
    """Одно сообщение в диалоге."""
    role: str              # user | link | system
    text: str
    ts: str = ""
    kind: str = "text"     # text | question | warning | stop | start
    meta: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not self.ts:
            self.ts = _now()


@dataclass
class DialogSession:
    """Сессия диалога — все сообщения одного проекта."""
    project_dir: str
    messages: List[DialogMessage] = field(default_factory=list)
    created_at: str = ""

    def __post_init__(self):
        if not self.created_at:
            self.created_at = _now()

    def add(self, message: DialogMessage) -> None:
        """Добавить сообщение в сессию (в памяти)."""
        self.messages.append(message)


# === Функции ===

def create_session(project_dir: str) -> DialogSession:
    """Создаёт новую сессию в памяти (не пишет на диск)."""
    return DialogSession(project_dir=project_dir)


def add_message(session: DialogSession, role: str, text: str,
                kind: str = "text",
                meta: Optional[Dict[str, Any]] = None) -> DialogMessage:
    """Добавляет сообщение в сессию и возвращает его."""
    msg = DialogMessage(
        role=role, text=text, kind=kind,
        meta=meta or {},
    )
    session.add(msg)
    return msg


def save_message(project_dir: str, message: DialogMessage) -> None:
    """Записывает одно сообщение в dialog.jsonl (append-only)."""
    path = _dialog_path(project_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(message), ensure_ascii=False) + "\n")
    except Exception:
        pass


def save_session(project_dir: str, session: DialogSession) -> None:
    """Сохраняет все сообщения сессии (те, что ещё не на диске)."""
    # Простая стратегия: пишем только сообщения из session.messages,
    # которых ещё нет в файле. Мы не отслеживаем, что записано —
    # поэтому используем флаг _saved на каждом сообщении.
    for msg in session.messages:
        if not getattr(msg, "_saved", False):
            save_message(project_dir, msg)
            try:
                msg._saved = True
            except Exception:
                pass


def load_messages(project_dir: str, limit: int = 200) -> List[DialogMessage]:
    """Читает последние N сообщений из dialog.jsonl."""
    path = _dialog_path(project_dir)
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except Exception:
        return []
    result: List[DialogMessage] = []
    for line in lines[-limit:]:
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
        except Exception:
            continue
        try:
            msg = DialogMessage(
                role=data.get("role", "system"),
                text=data.get("text", ""),
                ts=data.get("ts", ""),
                kind=data.get("kind", "text"),
                meta=data.get("meta", {}) or {},
            )
            msg._saved = True  # с диска — уже сохранено
            result.append(msg)
        except Exception:
            continue
    return result


def clear_dialog(project_dir: str) -> None:
    """Удаляет всю историю диалога проекта."""
    path = _dialog_path(project_dir)
    if path.exists():
        try:
            path.unlink()
        except Exception:
            pass


def count_messages(project_dir: str) -> int:
    """Сколько сообщений в диалоге проекта."""
    return len(load_messages(project_dir, limit=100000))
