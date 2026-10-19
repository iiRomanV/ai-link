"""B+ (19.10.2026): repair для невалидного JSON от DeepSeek.

DeepSeek пишет \\s вместо \\\\s и \\' (невалидно в JSON).
_parse_research_json: \\' → ', одиночные \\ → \\\\, \\u без hex → \\\\u.

Проверяем ТОЧНЫЕ значения после парсинга.
"""
import json
from pathlib import Path

import pytest

from core.orchestrator import _parse_research_json


def test_u_with_hex_preserved():
    """\\u1234 — валидный JSON escape, repair НЕ трогает."""
    raw = (
        '{"targets":[{"id":"P0-1","acceptance":'
        '[{"type":"grep_present","pattern":"\\u1234","path":"a.py"}]}]}'
    )
    r = _parse_research_json(raw)
    assert r["targets"], "нет targets"
    pat = r["targets"][0]["acceptance"][0]["pattern"]
    assert pat == "\u1234", f"ожидался 1 символ U+1234, получено {pat!r}"


def test_path_with_backslashes():
    """C:\\Users\\xyz — невалидный JSON (\\U, \\x), repair → C:\\Users\\xyz."""
    raw = (
        '{"targets":[{"id":"P0-1",'
        '"files_in_scope":["C:\\Users\\xyz"],'
        '"acceptance":[{"type":"file_exists","path":"a.py"}]}]}'
    )
    r = _parse_research_json(raw)
    assert r["targets"], "нет targets"
    paths = r["targets"][0]["files_in_scope"]
    assert paths == ["C:\\Users\\xyz"], f"путь изменён: {paths!r}"


def test_quote_in_pattern():
    """["\\'] — реальный паттерн. После repair → ["']."""
    raw = (
        '{"targets":[{"id":"P0-1","acceptance":'
        '[{"type":"grep_absent",'
        '"pattern":"ADMIN_PASSWORD\\s*=\\s*[\\"\\\'][^\\"\\\']+[\\"\\\']",'
        '"glob":"**/*.py"}]}]}'
    )
    r = _parse_research_json(raw)
    assert r["targets"], "нет targets"
    pat = r["targets"][0]["acceptance"][0]["pattern"]
    expected = 'ADMIN_PASSWORD\\s*=\\s*["\'][^"\']+["\']'
    assert pat == expected, f"pattern изменён: {pat!r} != {expected!r}"
