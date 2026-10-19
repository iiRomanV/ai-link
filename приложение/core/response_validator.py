"""Проверка ответов модели перед передачей в HandLink."""

import json
import re
from pathlib import Path
from typing import Dict, Any, Optional

try:
    from json_repair import repair_json
    HAS_JSON_REPAIR = True
except ImportError:
    HAS_JSON_REPAIR = False

ACTION_SCHEMA = {
    "create_file": ["path", "content"],
    "write_file": ["path", "content"],
    "append_to_file": ["path", "content"],
    "read_file": ["path"],  # offset и limit — опциональные
    "replace_in_file": ["path", "find", "replace"],
    "list_directory": ["path"],
    "show_info": ["message"],
    "run_command": ["command"],
    # FIX 2026-09-26: новые tools (из Forge Agent).
    # Промпт НЕ трогаем — модель их пока не знает.
    # Но если сама догадается — валидатор пропустит.
    "move_file": ["source", "destination"],
    "copy_file": ["source", "destination"],
    "file_metadata": ["path"],
}

FORBIDDEN_COMMANDS = [
    "rm -rf", "sudo", "mkfs", "shutdown", "reboot",
    "> /dev/sda", "dd if=",
]

MAX_CONTENT_SIZE = 200_000


def _is_path_inside(target: Path, base: Path) -> bool:
    try:
        target.resolve().relative_to(base.resolve())
        return True
    except (ValueError, OSError):
        return False


def _try_parse_dsml(text: str) -> Optional[str]:
    """Б75 (15.10.2026): DeepSeek иногда срывается в DSML-формат:

        <||DSML|| calls>
        <||DSML|| invoke name="read_file">
        <||DSML|| parameter name="path">foo.py</parameter>
        </||DSML|| invoke>
        </||DSML|| calls>

    Парсим в наш формат: {"tool": "read_file", "path": "foo.py"}
    """
    if not text or "DSML" not in text:
        return None
    import re as _re
    import json as _json
    invoke_m = _re.search(r'invoke\s+name\s*=\s*"([^"]+)"', text)
    if not invoke_m:
        return None
    tool = invoke_m.group(1).strip()
    params: dict = {}
    for pm in _re.finditer(
        r'parameter\s+name\s*=\s*"([^"]+)"[^>]*>(.*?)</.*?parameter>',
        text, _re.DOTALL,
    ):
        key = pm.group(1).strip()
        val = pm.group(2).strip()
        params[key] = val
    if not params:
        return None
    obj = {"tool": tool}
    obj.update(params)
    return _json.dumps(obj, ensure_ascii=False)


def _strip_json(text: str) -> Optional[str]:
    if not text:
        return None
    text = text.strip()

    # Б75: DSML-формат — парсим ДО очистки, чтобы маркеры
    # <||DSML||> не ломали регулярки ниже.
    _dsml = _try_parse_dsml(text)
    if _dsml:
        return _dsml

    # 1) Срезаем всё до последнего маркера окончания размышления
    #    (Qwen DeepThink, DeepSeek R1)
    for marker in [
        "Завершено размышление",
        "Размышление завершено",
        "Thinking finished",
        "Thought for",
        "Thinking...",
    ]:
        idx = text.rfind(marker)
        if idx >= 0:
            nl = text.find("\n", idx)
            if nl != -1:
                text = text[nl + 1:]
            else:
                text = text[idx + len(marker):]
            break

    # FIX: убираем маркеры Qwen Studio: последовательность "1\n2\n3..." 
    # перед JSON. Формат: пустая строка, потом "json", потом N строк 
    # с номерами, потом JSON. Регулярка ловит блок "1\n2\n3\n4\n5\n6\n7\n"
    import re as _re
    text = _re.sub(r'(?m)^\d+\s*$', '', text)
    
    # 2) Срезаем ведущие служебные строки: пустые, языковые маркеры,
    #    номера строк (1, 2, 3...), "Copy", "Download"
    lines = text.split("\n")
    skip_words = {"json", "python", "javascript", "tool_call", "bash",
                  "copy", "download"}
    started = False
    cleaned = []
    for ln in lines:
        s = ln.strip()
        if not started:
            if s == "" or s.lower() in skip_words or s.isdigit():
                continue
            started = True
        cleaned.append(ln)
    text = "\n".join(cleaned).strip()

    # 3) Поиск JSON в тексте
    if text.lower().startswith("tool_call"):
        rest = text[len("tool_call"):].strip()
        if rest.startswith("{") or rest.startswith("["):
            return rest
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if m:
        return m.group(1).strip()
    if text.startswith("{") and text.endswith("}"):
        return text
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        return m.group(0).strip()
    return None


def _looks_like_questions(text: str) -> bool:
    """FIX QUESTIONS-FALLBACK (2026-09-29): ответ похож на уточняющие
    вопросы модели, а не на JSON.

    Признаки:
      • >= 2 знаков вопроса
      • >= 2 маркеров уточнения (вопрос/уточн/подтверд/...)
      • длина >= 80 символов

    FIX REASONING-QUESTIONS (2026-10-02): если текст начинается
    с reasoning-маркера — это размышления модели, не запрос юзеру.
    DeepSeek задаёт вопросы внутри reasoning («Should I...?»,
    «Which option...?») — они ловились как questions → стоп.
    """
    if not text:
        return False
    _head = text.strip().lower()[:200]
    _reasoning_markers = (
        "thought for",
        "thinking",
        "we need",
        "the tool calls",
        "tool calls keep failing",
        "the user asks",
        "let me think",
        "i need to",
        "i should",
        "мы должны",
    )
    for _m in _reasoning_markers:
        if _head.startswith(_m):
            return False
    if len(text) < 80:
        return False
    q_marks = text.count("?") + text.count("？")
    if q_marks < 2:
        return False
    low = text.lower()
    markers = [
        "вопрос", "уточн", "подтверд", "согласен", "какой",
        "какие", "как вы", "нужно ли", "предлагаю",
        "прошу ответить", "questions", "clarification",
        "please confirm", "please clarify", "which option",
    ]
    hits = sum(1 for m in markers if m in low)
    return hits >= 2


def validate_response(response_text: str, project_dir: str, task: str = "") -> Dict[str, Any]:
    if not response_text or not response_text.strip():
        return {"ok": False, "error": "Пустой ответ модели"}

    # FIX: голый маркер task_complete (без JSON) — валидное завершение.
    # Ищем task_complete как отдельное слово, в т.ч. с префиксом "json".
    import re as _re_tc
    _t = response_text.strip()
    # Вариант A: "task_complete" или "json\ntask_complete" (+ хвост)
    if _re_tc.match(r"^(?:json\s*\n)?\s*task_complete\b", _t) and len(_t) < 500:
        _msg = _re_tc.sub(r"^(?:json\s*\n)?\s*task_complete\s*", "", _t).strip()
        return {"ok": True, "kind": "task_complete", "message": _msg[:200]}

    json_text = _strip_json(response_text)
    if not json_text:
        # FIX QUESTIONS-FALLBACK: если ответ без JSON,
        # но похож на уточняющие вопросы — отдельный kind.
        if _looks_like_questions(response_text):
            return {"ok": True, "kind": "questions",
                    "questions": response_text.strip()[:3000]}
        return {"ok": False, "error": "В ответе нет JSON-объекта"}

    try:
        data = json.loads(json_text)
    except json.JSONDecodeError as e:
        if HAS_JSON_REPAIR:
            try:
                fixed = repair_json(json_text)
                data = json.loads(fixed)
            except Exception:
                return {"ok": False, "error": f"Некорректный JSON (json_repair не помог): {e}"}
        else:
            return {"ok": False, "error": f"Некорректный JSON: {e}"}

    if not isinstance(data, dict):
        return {"ok": False, "error": "JSON должен быть объектом"}

    data = _convert_deepseek_format(data)

    # FIX: {"status": "task_complete"} — модель шлёт как "статус".
    if data.get("status") == "task_complete" and "type" not in data:
        data["type"] = "task_complete"

    # FIX: {"task_complete": true, "summary": "..."} — DeepSeek-форма.
    if data.get("task_complete") is True and "type" not in data:
        data["type"] = "task_complete"
        if "message" not in data and "summary" in data:
            data["message"] = data["summary"]

    action_type = data.get("type")
    # FIX: модель часто шлёт {"action": "..."} или {"tool": "..."}
    # вместо {"type": "..."}. Маппим через _map_tool_name.
    if not action_type:
        _raw = data.get("action") or data.get("tool")
        if _raw:
            action_type = _map_tool_name(_raw)
            data["type"] = action_type
    if not action_type:
        return {"ok": False, "error": "Не указан тип действия"}

    if action_type == "task_complete":
        return {"ok": True, "kind": "task_complete", "message": data.get("message", "")}

    if action_type not in ACTION_SCHEMA:
        return {"ok": False, "error": f"Неизвестный тип действия: {action_type}"}

    required = ACTION_SCHEMA[action_type]
    for param in required:
        if param not in data:
            return {"ok": False, "error": f"Отсутствует параметр '{param}' для {action_type}"}

    if "path" in data:
        path_value = data["path"]
        if not isinstance(path_value, str) or not path_value.strip():
            return {"ok": False, "error": "Путь должен быть непустой строкой"}
        base = Path(project_dir)
        target = base / path_value
        if not _is_path_inside(target, base):
            return {"ok": False, "error": f"Путь вне проекта: {path_value}"}
        if action_type == "replace_in_file" and not data.get("find"):
            return {"ok": False, "error": "Параметр 'find' не может быть пустым"}

    if action_type == "create_file":
        content = data.get("content", "")
        if not isinstance(content, str):
            return {"ok": False, "error": "content должен быть строкой"}
        if len(content) > MAX_CONTENT_SIZE:
            return {"ok": False, "error": f"Слишком большой контент ({len(content)} байт)"}

    # W3: проверка offset/limit для read_file
    if action_type == "read_file":
        for param in ("offset", "limit"):
            if param in data:
                val = data[param]
                if val is not None:
                    if not isinstance(val, int) or val < 0:
                        return {
                            "ok": False,
                            "error": (
                                f"Параметр '{param}' должен быть "
                                f"неотрицательным целым числом"
                            ),
                        }

    if action_type == "run_command":
        cmd = data.get("command", "")
        if not isinstance(cmd, str) or not cmd.strip():
            return {"ok": False, "error": "Команда не может быть пустой"}
        cmd_lower = cmd.lower()
        for pattern in FORBIDDEN_COMMANDS:
            if pattern in cmd_lower:
                return {"ok": False, "error": f"Запрещённая конструкция: {pattern}"}

    approved = {k: v for k, v in data.items() if k in (["type"] + required)}
    return {"ok": True, "kind": "action", "action": approved}


def _convert_deepseek_format(data: dict) -> dict:
    """Преобразует разные форматы в наш {"type": ..., ...}."""
    if not isinstance(data, dict):
        return data

    # FIX: формат OpenAI function-call от DeepSeek:
    # {"tool_name": "write_file", "tool_args": {"path": ..., "content": ...}}
    if "tool_name" in data:
        name = data.get("tool_name", "")
        args = data.get("tool_args", {}) or {}
        result = {"type": _map_tool_name(name)}
        if isinstance(args, dict):
            result.update(args)
        return result

    # FIX: формат {"name": "write_file", "arguments": {...}}
    if "tool_name" not in data and "arguments" in data and "type" not in data:
        name = data.get("name", "")
        if name:
            args = data.get("arguments", {}) or {}
            result = {"type": _map_tool_name(name)}
            if isinstance(args, dict):
                result.update(args)
            return result

    # Разворачиваем вложенный tool_call
    if "tool_call" in data and isinstance(data["tool_call"], dict):
        inner = data["tool_call"]
        name = inner.get("name") or inner.get("tool")
        args = inner.get("arguments") or inner.get("args") or {}
        if name:
            result = {"type": _map_tool_name(name)}
            if isinstance(args, dict):
                result.update(args)
            return result

    # Уже наш формат
    if "type" in data:
        return data

    # Формат {"tool": "write_file", ...} — args могут быть внутри args или на верхнем уровне
    if "tool" in data and "name" not in data:
        name = data.get("tool", "")
        args = data.get("args", {}) or data.get("arguments", {}) or {}
        result = {"type": _map_tool_name(name)}
        if isinstance(args, dict) and args:
            result.update(args)
        else:
            # Flat args на верхнем уровне: path, content и т.д.
            for k in ("path", "content", "find", "replace", "message", "command", "timeout"):
                if k in data:
                    result[k] = data[k]
        return result

    # Формат {"action": "write_file", "path": "...", "content": "..."}
    if "action" in data and "name" not in data and "tool" not in data:
        name = data.get("action", "")
        result = {"type": _map_tool_name(name)}
        for k in ("path", "content", "find", "replace", "message", "command"):
            if k in data:
                result[k] = data[k]
        return result

    # Формат {"name": "write_file", "args": {...}}
    if "name" in data:
        name = data.get("name", "")
        args = data.get("args", {}) or data.get("arguments", {}) or {}
        result = {"type": _map_tool_name(name)}
        if isinstance(args, dict):
            result.update(args)
        return result

    return data


def _map_tool_name(name: str) -> str:
    """Маппинг имён инструментов в наш формат."""
    mapping = {
        "write_file": "create_file",
        "create_file": "create_file",
        "append_to_file": "create_file",
        "read_file": "read_file",
        "replace_in_file": "replace_in_file",
        "list_directory": "list_directory",
        "run_command": "run_command",
        "show_info": "show_info",
        "task_complete": "task_complete",
        # FIX TOOL-ALIASES: модели выдумывают имена.
        # Не ломает существующее — только добавляет алиасы.
        "bash": "run_command",
        "sh": "run_command",
        "shell": "run_command",
        "exec": "run_command",
        "execute": "run_command",
        "write": "create_file",
        "file_write": "create_file",
        "save_file": "create_file",
    }
    return mapping.get(name, name)
