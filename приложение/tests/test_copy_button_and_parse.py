"""B+ (19.10.2026): Copy/Download + парсер текстовых ответов.

Проблема (прогон 12:02): DeepSeek ответил 20306 символов reasoning,
БЕЗ "targets" (но с JSON-объектами внутри). _is_reasoning_only
отсёк всё (Stopped). _research_has_content не нашёл "targets".

Фикс:
  • _has_copy_button — Copy/Download = ответ готов.
  • _parse_research_json — fallback: собрать JSON-объекты из текста.
  • _research_has_content — принимать {...} с "type"/"id".
"""
from core.orchestrator import (
    _parse_research_json,
    _research_has_content,
)


# ─── _parse_research_json: fallback ────────────────────────────

def test_parse_targets_standard():
    """Стандартный JSON с targets — работает как раньше."""
    raw = '{"targets": [{"id":"P0-1","file":"a.py","pattern":"x"}]}'
    r = _parse_research_json(raw)
    assert len(r["targets"]) == 1


def test_parse_text_with_acceptance_blocks():
    """Текст с отдельными {"type": ...} — собирается в targets."""
    raw = '''Let me analyze. Here are acceptance checks:
{"type":"grep_absent","pattern":"ADMIN_PASSWORD","glob":"**/*.py"}
{"type":"file_exists","path":".gitignore"}
Done.'''
    r = _parse_research_json(raw)
    assert len(r["targets"]) >= 1, f"не собрал: {r}"
    # Должен быть acceptance
    t = r["targets"][0]
    assert t.get("acceptance"), f"нет acceptance: {t}"


def test_parse_text_with_target_blocks():
    """Текст с {"id": "P0-1", ...} — собирается в targets."""
    raw = '''Analyzing reports...
{"id":"P0-1","severity":"P0","acceptance":[{"type":"file_exists","path":".gitignore"}]}
Done.'''
    r = _parse_research_json(raw)
    assert len(r["targets"]) == 1
    assert r["targets"][0]["id"] == "P0-1"


def test_parse_garbage_returns_empty():
    assert _parse_research_json("") == {"version": 1, "targets": []}
    assert _parse_research_json("просто текст") == {"version": 1, "targets": []}


# ─── _research_has_content ────────────────────────────────────

def test_has_content_targets():
    assert _research_has_content('{"targets": []}') is True


def test_has_content_type_block():
    """Текст с {"type": ...} — валидный ответ."""
    raw = 'Text {"type":"grep_absent","pattern":"x"} more'
    assert _research_has_content(raw) is True


def test_has_content_id_block():
    raw = 'Text {"id":"P0-1","severity":"P0"} more'
    assert _research_has_content(raw) is True


def test_has_content_no_json():
    assert _research_has_content("Thinking\nThinking") is False
    assert _research_has_content("Stopped\n\nLet me analyze...") is False
