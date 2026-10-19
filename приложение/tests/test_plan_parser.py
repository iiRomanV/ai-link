"""Тесты для core.plan_parser."""

from pathlib import Path
from core.plan_parser import (
    detect_status, status_icon, status_label,
    parse_plan_file, parse_all_plans, count_by_status,
    MARKER_DONE, MARKER_IN_PROGRESS, MARKER_PENDING,
)


def _write(path: Path, content: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_detect_status_done():
    assert detect_status("✅ сделано") == "done"


def test_detect_status_in_progress():
    assert detect_status("🔄 в работе") == "in_progress"


def test_detect_status_pending():
    assert detect_status("⏳ ожидает") == "pending"


def test_detect_status_unknown():
    assert detect_status("обычный текст") == "unknown"
    assert detect_status("") == "unknown"
    assert detect_status(None) == "unknown"


def test_status_icon():
    # LR-CARD: emoji вместо [OK]
    assert status_icon("done") == "✅"
    assert status_icon("in_progress") == "🔄"
    assert status_icon("pending") == "⏳"
    assert status_icon("unknown") == "•"


def test_status_label():
    assert status_label("done") == "выполнено"
    assert status_label("in_progress") == "в работе"
    assert status_label("unknown") == "неизвестно"


def test_parse_plan_file_basic(tmp_path):
    _write(tmp_path / "этап_test.md", """# Этап 7.5. Диалог

Статус: 🔄 В РАБОТЕ

## Прогресс

- ✅ 7.5.1. core/dialog.py
- ✅ 7.5.2. core/background_task.py
- 🔄 7.5.3. UI-вкладка
- ⏳ 7.5.4. Уточнения
""")
    r = parse_plan_file(tmp_path / "этап_test.md")
    assert r is not None
    assert r["title"] == "Этап 7.5. Диалог"
    assert r["status"] == "in_progress"
    assert len(r["items"]) == 4
    assert r["items"][0]["status"] == "done"
    assert r["items"][2]["status"] == "in_progress"
    assert r["items"][3]["status"] == "pending"
    # Проверяем, что эмодзи убраны из текста
    assert "✅" not in r["items"][0]["text"]
    assert "7.5.1" in r["items"][0]["text"]


def test_parse_plan_file_no_title(tmp_path):
    _write(tmp_path / "no_title.md", "просто текст без заголовка")
    r = parse_plan_file(tmp_path / "no_title.md")
    assert r is None


def test_parse_plan_file_missing(tmp_path):
    r = parse_plan_file(tmp_path / "nope.md")
    assert r is None


def test_parse_all_plans(tmp_path):
    _write(tmp_path / "основной.md", "# AI Link\nСтатус: 🔄\n")
    _write(tmp_path / "этап_6.md", "# Этап 6. Аудит\nСтатус: ✅\n- ✅ 6.1\n")
    _write(tmp_path / "этап_7.md", "# Этап 7. Оркестрация\nСтатус: 🔄\n- 🔄 7.1\n")
    plans = parse_all_plans(str(tmp_path))
    assert len(plans) == 3
    # Основной идёт первым, потом этапы по порядку
    assert plans[0]["title"] == "AI Link — основной план"
    assert plans[1]["title"] == "Этап 6. Аудит"
    assert plans[2]["title"] == "Этап 7. Оркестрация"


def test_parse_all_plans_empty(tmp_path):
    plans = parse_all_plans(str(tmp_path))
    assert plans == []


def test_parse_all_plans_missing_dir(tmp_path):
    plans = parse_all_plans(str(tmp_path / "нет_такой_папки"))
    assert plans == []


def test_count_by_status():
    plans = [
        {"items": [
            {"text": "a", "status": "done"},
            {"text": "b", "status": "done"},
            {"text": "c", "status": "in_progress"},
            {"text": "d", "status": "pending"},
        ]},
    ]
    counts = count_by_status(plans)
    assert counts["done"] == 2
    assert counts["in_progress"] == 1
    assert counts["pending"] == 1
