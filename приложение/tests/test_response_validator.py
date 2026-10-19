import sys
from core.response_validator import validate_response

def test_read_file_ok():
    r = validate_response('{"type": "read_file", "path": "main.py"}', "/tmp")
    assert r["ok"] is True
    assert r["kind"] == "action"

def test_path_escape_blocked():
    r = validate_response('{"type": "create_file", "path": "../etc/passwd", "content": "x"}', "/tmp")
    assert r["ok"] is False
    assert "вне проекта" in r["error"]

def test_forbidden_command():
    r = validate_response('{"type": "run_command", "command": "rm -rf /"}', "/tmp")
    assert r["ok"] is False
    assert "Запрещённая" in r["error"]

def test_task_complete():
    r = validate_response('{"type": "task_complete", "message": "ok"}', "/tmp")
    assert r["ok"] is True
    assert r["kind"] == "task_complete"

def test_no_json():
    r = validate_response("просто текст", "/tmp")
    assert r["ok"] is False

def test_missing_param():
    r = validate_response('{"type": "create_file", "path": "x.py"}', "/tmp")
    assert r["ok"] is False
    assert "content" in r["error"]

def test_json_in_markdown():
    text = '```json\n{"type": "read_file", "path": "a.txt"}\n```'
    r = validate_response(text, "/tmp")
    assert r["ok"] is True


# ═══════════════════════════════════════════════════════════
# Дополнительные тесты (покрытие 64% → 85%+)
# ═══════════════════════════════════════════════════════════

import json as _json
from core.response_validator import (
    validate_response, _strip_json, _is_path_inside,
    _convert_deepseek_format, _map_tool_name,
    MAX_CONTENT_SIZE,
)


# === _is_path_inside ===

def test_is_path_inside_true(tmp_path):
    from pathlib import Path
    base = tmp_path
    target = tmp_path / "sub" / "file.py"
    assert _is_path_inside(target, base) is True


def test_is_path_inside_false(tmp_path):
    from pathlib import Path
    base = tmp_path / "project"
    base.mkdir()
    target = tmp_path / "other" / "file.py"
    assert _is_path_inside(target, base) is False


# === _strip_json — маркеры размышления ===

def test_strip_json_with_thinking_finished():
    text = "Завершено размышление\n{\"type\": \"read_file\", \"path\": \"a.py\"}"
    assert _strip_json(text) == '{"type": "read_file", "path": "a.py"}'


def test_strip_json_with_thought_for():
    text = "Thought for 3 seconds\n{\"type\": \"task_complete\"}"
    assert _strip_json(text) == '{"type": "task_complete"}'


def test_strip_json_with_thinking_ellipsis():
    text = "Thinking...\n{\"type\": \"read_file\", \"path\": \"a.py\"}"
    assert _strip_json(text) == '{"type": "read_file", "path": "a.py"}'


# === _strip_json — служебные строки ===

def test_strip_json_with_lang_and_copy():
    text = "json\nCopy\nDownload\n{\"type\": \"read_file\", \"path\": \"a.py\"}"
    result = _strip_json(text)
    assert result is not None
    assert result.startswith("{")
    assert "read_file" in result


def test_strip_json_with_line_numbers():
    text = "1\n2\n3\n{\"type\": \"read_file\", \"path\": \"a.py\"}"
    assert _strip_json(text) == '{"type": "read_file", "path": "a.py"}'


# === _strip_json — прочие форматы ===

def test_strip_json_tool_call_prefix():
    text = "tool_call\n{\"name\": \"read_file\", \"args\": {}}"
    result = _strip_json(text)
    assert result == '{"name": "read_file", "args": {}}'


def test_strip_json_bare_object():
    text = '{"type": "read_file", "path": "a.py"}'
    assert _strip_json(text) == text


def test_strip_json_empty():
    assert _strip_json("") is None
    assert _strip_json(None) is None


def test_strip_json_no_json():
    assert _strip_json("просто текст без json") is None


# === validate_response — краевые случаи ===

def test_validate_empty():
    r = validate_response("", "/tmp/proj")
    assert not r["ok"]
    assert "Пустой" in r["error"] or "ответ" in r["error"].lower()


def test_validate_whitespace_only():
    r = validate_response("   \n  \n", "/tmp/proj")
    assert not r["ok"]


def test_validate_no_json_object():
    r = validate_response("просто текст", "/tmp/proj")
    assert not r["ok"]
    assert "JSON" in r["error"]


def test_validate_json_array_not_object(tmp_path):
    r = validate_response("[1, 2, 3]", str(tmp_path))
    assert not r["ok"]
    assert "объект" in r["error"].lower()


def test_validate_no_action_type(tmp_path):
    r = validate_response('{"path": "a.py"}', str(tmp_path))
    assert not r["ok"]
    assert "тип действия" in r["error"].lower() or "тип" in r["error"].lower()


def test_validate_unknown_action_type(tmp_path):
    r = validate_response('{"type": "неверный_тип"}', str(tmp_path))
    assert not r["ok"]
    assert "Неизвестный" in r["error"] or "неизвестн" in r["error"].lower()


def test_validate_content_not_string(tmp_path):
    r = validate_response(
        '{"type": "create_file", "path": "a.py", "content": 123}',
        str(tmp_path)
    )
    assert not r["ok"]
    assert "content" in r["error"].lower() or "строкой" in r["error"].lower()


def test_validate_content_too_large(tmp_path):
    big = "x" * (MAX_CONTENT_SIZE + 10)
    payload = _json.dumps({"type": "create_file", "path": "a.py",
                            "content": big})
    r = validate_response(payload, str(tmp_path))
    assert not r["ok"]
    assert "большой" in r["error"].lower() or "размер" in r["error"].lower()


def test_validate_path_empty_string(tmp_path):
    r = validate_response(
        '{"type": "read_file", "path": ""}', str(tmp_path)
    )
    assert not r["ok"]
    assert "путь" in r["error"].lower()


def test_validate_replace_in_file_empty_find(tmp_path):
    r = validate_response(
        '{"type": "replace_in_file", "path": "a.py", "find": "", "replace": "x"}',
        str(tmp_path)
    )
    assert not r["ok"]
    assert "find" in r["error"].lower() or "пуст" in r["error"].lower()


def test_validate_replace_in_file_ok(tmp_path):
    r = validate_response(
        '{"type": "replace_in_file", "path": "a.py", "find": "old", "replace": "new"}',
        str(tmp_path)
    )
    assert r["ok"]
    assert r["kind"] == "action"
    assert r["action"]["type"] == "replace_in_file"
    assert r["action"]["find"] == "old"
    assert r["action"]["replace"] == "new"


def test_validate_run_command_not_string(tmp_path):
    r = validate_response(
        '{"type": "run_command", "command": 123}', str(tmp_path)
    )
    assert not r["ok"]
    assert "команда" in r["error"].lower()


def test_validate_run_command_empty(tmp_path):
    r = validate_response(
        '{"type": "run_command", "command": "   "}', str(tmp_path)
    )
    assert not r["ok"]
    assert "команда" in r["error"].lower() or "пустой" in r["error"].lower()


def test_validate_run_command_ok(tmp_path):
    r = validate_response(
        '{"type": "run_command", "command": "ls -la"}', str(tmp_path)
    )
    assert r["ok"]
    assert r["action"]["command"] == "ls -la"


def test_validate_list_directory(tmp_path):
    r = validate_response(
        '{"type": "list_directory", "path": "."}', str(tmp_path)
    )
    assert r["ok"]
    assert r["action"]["type"] == "list_directory"


def test_validate_show_info(tmp_path):
    r = validate_response(
        '{"type": "show_info", "message": "привет"}', str(tmp_path)
    )
    assert r["ok"]
    assert r["action"]["message"] == "привет"


# === _convert_deepseek_format ===

def test_convert_tool_call_wrapped():
    data = {
        "tool_call": {
            "name": "write_file",
            "arguments": {"path": "a.py", "content": "x = 1"}
        }
    }
    r = _convert_deepseek_format(data)
    assert r["type"] == "create_file"
    assert r["path"] == "a.py"
    assert r["content"] == "x = 1"


def test_convert_tool_call_with_tool_key():
    data = {
        "tool_call": {
            "tool": "read_file",
            "args": {"path": "a.py"}
        }
    }
    r = _convert_deepseek_format(data)
    assert r["type"] == "read_file"
    assert r["path"] == "a.py"


def test_convert_already_our_format():
    data = {"type": "read_file", "path": "a.py"}
    r = _convert_deepseek_format(data)
    assert r == data


def test_convert_tool_with_args():
    data = {"tool": "write_file", "args": {"path": "a.py", "content": "x"}}
    r = _convert_deepseek_format(data)
    assert r["type"] == "create_file"
    assert r["path"] == "a.py"
    assert r["content"] == "x"


def test_convert_tool_flat_args():
    data = {"tool": "write_file", "path": "a.py", "content": "x = 1"}
    r = _convert_deepseek_format(data)
    assert r["type"] == "create_file"
    assert r["path"] == "a.py"
    assert r["content"] == "x = 1"


def test_convert_action_format():
    data = {"action": "read_file", "path": "a.py"}
    r = _convert_deepseek_format(data)
    assert r["type"] == "read_file"
    assert r["path"] == "a.py"


def test_convert_name_with_args():
    data = {"name": "write_file", "args": {"path": "a.py", "content": "x"}}
    r = _convert_deepseek_format(data)
    assert r["type"] == "create_file"
    assert r["path"] == "a.py"


def test_convert_non_dict():
    assert _convert_deepseek_format("строка") == "строка"
    assert _convert_deepseek_format(None) is None


def test_convert_unknown_format():
    data = {"неизвестное": "поле"}
    r = _convert_deepseek_format(data)
    assert r == data


# === _map_tool_name ===

def test_map_tool_name_write():
    assert _map_tool_name("write_file") == "create_file"


def test_map_tool_name_create():
    assert _map_tool_name("create_file") == "create_file"


def test_map_tool_name_append():
    assert _map_tool_name("append_to_file") == "create_file"


def test_map_tool_name_read():
    assert _map_tool_name("read_file") == "read_file"


def test_map_tool_name_unknown():
    # Неизвестное имя остаётся как есть
    assert _map_tool_name("unknown_tool") == "unknown_tool"


def test_map_tool_name_task_complete():
    assert _map_tool_name("task_complete") == "task_complete"

# ─── FIX: task_complete в свободной форме ───

def test_task_complete_bare_text():
    from core.response_validator import validate_response
    r = validate_response("task_complete", "/tmp")
    assert r["ok"] is True
    assert r["kind"] == "task_complete"


def test_task_complete_with_message():
    from core.response_validator import validate_response
    r = validate_response("task_complete Готово", "/tmp")
    assert r["ok"] is True
    assert r["kind"] == "task_complete"


def test_task_complete_action_field():
    from core.response_validator import validate_response
    r = validate_response(
        '{"action": "task_complete", "message": "ok"}', "/tmp")
    assert r["ok"] is True
    assert r["kind"] == "task_complete"


def test_task_complete_tool_field():
    from core.response_validator import validate_response
    r = validate_response(
        '{"tool": "task_complete", "message": "ok"}', "/tmp")
    assert r["ok"] is True
    assert r["kind"] == "task_complete"



# --------------------------------------------------------------------------- #
# FIX QUESTIONS-FALLBACK (2026-09-29)
# --------------------------------------------------------------------------- #
from core.response_validator import _looks_like_questions


def test_looks_like_questions_real_questions():
    """Реальный пример: DeepSeek задал 5 вопросов."""
    text = (
        "Принято. Спецификация детальна.\n\n"
        "Вопросы для уточнения:\n"
        "1. Логика сохранения — создаём tblDocuments?\n"
        "2. Округление Qri — кастомная функция?\n"
        "3. Поиск в ComboBox — стандартный?\n"
    )
    assert _looks_like_questions(text) is True


def test_looks_like_questions_short():
    """Короткий текст (< 80 симв) — не вопросы."""
    assert _looks_like_questions("Как дела?") is False


def test_looks_like_questions_no_marks():
    """Много текста, но без знаков вопроса — не вопросы."""
    text = "Длинный текст без вопросов. " * 10
    assert _looks_like_questions(text) is False


def test_looks_like_questions_one_marker():
    """Один вопрос + мало маркеров — не вопросы."""
    text = (
        "Привет. Это длинный текст, в котором есть один вопрос? "
        "Но больше маркеров уточнения нет. Просто рассказ."
    )
    assert _looks_like_questions(text) is False


def test_looks_like_questions_english():
    """Английские вопросы."""
    text = (
        "I have several questions for clarification:\n"
        "1. Which option should I use?\n"
        "2. Please confirm the format?\n"
        "3. What about the questions list?\n"
    )
    assert _looks_like_questions(text) is True


def test_validate_questions_returns_kind():
    """validate_response возвращает kind=questions для вопросов."""
    text = (
        "Принято. Вопросы для уточнения:\n"
        "1. Какую структуру использовать?\n"
        "2. Какие поля нужны?\n"
        "3. Подтвердите формат?\n"
    )
    r = validate_response(text, "/tmp")
    assert r.get("ok") is True
    assert r.get("kind") == "questions"
    assert "структуру" in r.get("questions", "")


def test_validate_questions_not_applied_to_json():
    """JSON-ответ НЕ переопределяется как questions."""
    r = validate_response(
        '```json\n{"type": "task_complete", "message": "ok"}\n```',
        "/tmp",
    )
    assert r.get("kind") == "task_complete"
