"""Тесты для core.rules_parser."""

from core.rules_parser import parse_rule_command, is_rule_command


def test_basic_with_colon():
    r = parse_rule_command("сохрани как правило: пути только относительные")
    assert r["is_rule"]
    assert r["text"] == "пути только относительные"
    assert r["scope"] == "unknown"


def test_dash_separator():
    r = parse_rule_command("запомни правило — использовать type hints")
    assert r["is_rule"]
    assert r["text"] == "использовать type hints"


def test_hyphen_separator():
    r = parse_rule_command("запомни правило - использовать type hints")
    assert r["is_rule"]
    assert r["text"] == "использовать type hints"


def test_rule_trigger():
    r = parse_rule_command("правило: всегда docstrings")
    assert r["is_rule"]
    assert r["text"] == "всегда docstrings"


def test_global_scope():
    r = parse_rule_command("добавь правило глобально: не использовать print")
    assert r["is_rule"]
    assert r["scope"] == "global"
    assert "print" in r["text"]
    assert "глобально" not in r["text"].lower()


def test_project_scope():
    r = parse_rule_command("сохрани как правило для проекта: пути через pathlib")
    assert r["is_rule"]
    assert r["scope"] == "project"
    assert "pathlib" in r["text"]


def test_all_projects_scope():
    r = parse_rule_command("запомни правило для всех проектов: type hints")
    assert r["is_rule"]
    assert r["scope"] == "global"


def test_not_a_rule():
    r = parse_rule_command("Привет, как дела?")
    assert not r["is_rule"]
    assert r["text"] == ""


def test_empty_string():
    r = parse_rule_command("")
    assert not r["is_rule"]


def test_none():
    r = parse_rule_command(None)
    assert not r["is_rule"]


def test_trigger_without_rule():
    r = parse_rule_command("сохрани как правило:")
    assert not r["is_rule"]
    assert r.get("error") == "не указан текст правила"


def test_case_insensitive():
    r = parse_rule_command("СОХРАНИ КАК ПРАВИЛО: тест")
    assert r["is_rule"]
    assert r["text"] == "тест"


def test_is_rule_command_helper():
    assert is_rule_command("правило: X") is True
    assert is_rule_command("обычный текст") is False


def test_extra_whitespace():
    r = parse_rule_command("  сохрани   как   правило:   правило с пробелами  ")
    assert r["is_rule"]
    assert "правило с пробелами" in r["text"]


def test_rule_with_commas_cleaned():
    r = parse_rule_command("правило: использовать type hints, для проекта")
    assert r["is_rule"]
    assert r["scope"] == "project"
    # Хвостовая запятая и маркер области убраны
    assert "для проекта" not in r["text"].lower()
    assert not r["text"].endswith(",")
