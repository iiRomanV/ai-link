"""Тесты для core.rules_manager."""

import json
from pathlib import Path
from unittest.mock import patch
from core import rules_manager
from core.rules_manager import (
    load_global_rules, save_global_rules, add_global_rule,
    remove_global_rule,
    load_project_rules, save_project_rules, add_project_rule,
    remove_project_rule,
    all_rules, all_rules_text,
)


def setup_function(_func):
    """Перед каждым тестом — переопределяем пути во временную папку."""
    # Не трогаем реальный ~/.ai_link
    pass


# === Глобальные правила ===

def test_load_global_empty(tmp_path):
    fake = tmp_path / "brain" / "rules.json"
    with patch.object(rules_manager, "GLOBAL_RULES_FILE", fake):
        assert load_global_rules() == []


def test_add_global_rule(tmp_path):
    fake = tmp_path / "brain" / "rules.json"
    with patch.object(rules_manager, "GLOBAL_RULES_FILE", fake):
        r = add_global_rule("Всегда с докстрингами")
        assert r["text"] == "Всегда с докстрингами"
        assert r["source"] == "user"
        assert "created_at" in r
        rules = load_global_rules()
        assert len(rules) == 1


def test_add_global_rule_no_duplicate(tmp_path):
    fake = tmp_path / "brain" / "rules.json"
    with patch.object(rules_manager, "GLOBAL_RULES_FILE", fake):
        add_global_rule("Правило X")
        add_global_rule("Правило X")
        add_global_rule("  Правило X  ")  # с пробелами — тот же
        rules = load_global_rules()
        assert len(rules) == 1


def test_add_global_rule_empty(tmp_path):
    fake = tmp_path / "brain" / "rules.json"
    with patch.object(rules_manager, "GLOBAL_RULES_FILE", fake):
        r = add_global_rule("")
        assert r == {}
        r = add_global_rule("   ")
        assert r == {}
        assert load_global_rules() == []


def test_remove_global_rule(tmp_path):
    fake = tmp_path / "brain" / "rules.json"
    with patch.object(rules_manager, "GLOBAL_RULES_FILE", fake):
        add_global_rule("A")
        add_global_rule("B")
        assert remove_global_rule("A") is True
        rules = load_global_rules()
        assert len(rules) == 1
        assert rules[0]["text"] == "B"


def test_remove_global_rule_not_found(tmp_path):
    fake = tmp_path / "brain" / "rules.json"
    with patch.object(rules_manager, "GLOBAL_RULES_FILE", fake):
        assert remove_global_rule("нет такого") is False


# === Проектные правила ===

def test_add_project_rule(tmp_path):
    r = add_project_rule(str(tmp_path), "Пути только относительные")
    assert r["text"] == "Пути только относительные"
    rules = load_project_rules(str(tmp_path))
    assert len(rules) == 1
    # Файл создан
    assert (tmp_path / ".ai_link" / "project.json").exists()


def test_add_project_rule_no_duplicate(tmp_path):
    add_project_rule(str(tmp_path), "Правило Y")
    add_project_rule(str(tmp_path), "Правило Y")
    rules = load_project_rules(str(tmp_path))
    assert len(rules) == 1


def test_remove_project_rule(tmp_path):
    add_project_rule(str(tmp_path), "A")
    add_project_rule(str(tmp_path), "B")
    assert remove_project_rule(str(tmp_path), "A") is True
    rules = load_project_rules(str(tmp_path))
    assert len(rules) == 1
    assert rules[0]["text"] == "B"


def test_project_rules_preserve_other_keys(tmp_path):
    """Добавление правил не стирает другие ключи project.json."""
    path = tmp_path / ".ai_link" / "project.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"task": "X", "plan": [1, 2, 3]}, ensure_ascii=False),
        encoding="utf-8",
    )
    add_project_rule(str(tmp_path), "новое правило")
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["task"] == "X"
    assert data["plan"] == [1, 2, 3]
    assert len(data["rules"]) == 1


def test_load_project_rules_from_string_list(tmp_path):
    """Старый формат: rules = ["правило1", "правило2"]."""
    path = tmp_path / ".ai_link" / "project.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"rules": ["просто строка", "вторая"]}, ensure_ascii=False),
        encoding="utf-8",
    )
    rules = load_project_rules(str(tmp_path))
    assert len(rules) == 2
    assert rules[0]["text"] == "просто строка"


# === Объединённый список ===

def test_all_rules(tmp_path):
    fake = tmp_path / "brain" / "rules.json"
    with patch.object(rules_manager, "GLOBAL_RULES_FILE", fake):
        add_global_rule("Глобальное 1")
        add_global_rule("Глобальное 2")
        add_project_rule(str(tmp_path), "Проектное 1")
        data = all_rules(str(tmp_path))
        assert len(data["global"]) == 2
        assert len(data["project"]) == 1


def test_all_rules_text(tmp_path):
    fake = tmp_path / "brain" / "rules.json"
    with patch.object(rules_manager, "GLOBAL_RULES_FILE", fake):
        add_global_rule("Правило Г")
        add_project_rule(str(tmp_path), "Правило П")
        text = all_rules_text(str(tmp_path))
        assert "ГЛОБАЛЬНЫЕ ПРАВИЛА" in text
        assert "ПРАВИЛА ПРОЕКТА" in text
        assert "Правило Г" in text
        assert "Правило П" in text


def test_all_rules_text_empty(tmp_path):
    fake = tmp_path / "brain" / "rules.json"
    with patch.object(rules_manager, "GLOBAL_RULES_FILE", fake):
        text = all_rules_text(str(tmp_path))
        assert text == ""


def test_add_project_rule_with_source(tmp_path):
    r = add_project_rule(str(tmp_path), "правило линка", source="link")
    assert r["source"] == "link"
