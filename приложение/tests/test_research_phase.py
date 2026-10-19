"""B (18.10.2026): research-фаза — чеклист целей перед code.

Проверяем:
  1. Ветка промпта для PHASE_RESEARCH существует и содержит
     инструкции: targets, ЗАПРЕТ НА СЕКРЕТЫ, "ты research".
  2. PHASE_RESEARCH в PHASES и в PHASE_TO_CATEGORY.
  3. Парсер _parse_research_json разбирает:
     • чистый JSON
     • ```json ... ```
     • текст с JSON внутри
     • пустой ответ → targets: []
  4. _normalize_research:
     • отбрасывает targets без file/pattern
     • маскирует длинные значения секретов в pattern
  5. _save_research_notes пишет файл в .ai_link/research_notes.json
"""
import json
from pathlib import Path

import pytest

from core.orchestrator import (
    PHASES,
    PHASE_RESEARCH,
    PHASE_TO_CATEGORY,
    PHASE_DESCRIPTIONS,
    OrchestrationContext,
    _build_text_prompt,
    _parse_research_json,
    _normalize_research,
    _save_research_notes,
    _mask_secrets_for_research,
)


# ─── 1. Ветка промпта ────────────────────────────────────────────

def test_research_phase_in_phases():
    assert PHASE_RESEARCH == "research"
    assert PHASE_RESEARCH in PHASES
    assert PHASE_TO_CATEGORY.get(PHASE_RESEARCH) == "planning"
    assert PHASE_RESEARCH in PHASE_DESCRIPTIONS


def test_research_prompt_contains_key_markers(tmp_path):
    ctx = OrchestrationContext(
        original_task="почини приложение",
        project_dir=str(tmp_path),
    )
    p = _build_text_prompt(ctx, PHASE_RESEARCH)
    assert "targets" in p
    assert "ЗАПРЕТ НА СЕКРЕТЫ" in p
    assert "ТЫ — ФАЗА RESEARCH" in p
    # Должна быть явная инструкция не писать код
    assert "read-only" in p or "read only" in p.lower() or "НЕ пишешь код" in p
    # Инструкция про пустой targets (чтобы не ломать старые прогоны)
    assert "targets" in p and "[]" in p


def test_research_prompt_mentions_reports_in_project(tmp_path):
    """Если в проекте есть ИНВЕНТАРИЗАЦИЯ_ПРОБЛЕМ.md — промпт его упомянет."""
    (tmp_path / "ИНВЕНТАРИЗАЦИЯ_ПРОБЛЕМ.md").write_text("# test", encoding="utf-8")
    (tmp_path / "ошибки.md").write_text("# test", encoding="utf-8")
    ctx = OrchestrationContext(
        original_task="почини",
        project_dir=str(tmp_path),
    )
    p = _build_text_prompt(ctx, PHASE_RESEARCH)
    assert "ИНВЕНТАРИЗАЦИЯ_ПРОБЛЕМ.md" in p
    assert "ошибки.md" in p


# ─── 2. Парсер _parse_research_json ──────────────────────────────

def test_parse_clean_json():
    raw = json.dumps({
        "targets": [
            {"id": "P0-1", "priority": "P0", "file": "config.py",
             "pattern": r"ADMIN_PASSWORD\s*=", "check_kind": "not_contains",
             "action": "убрать"},
        ]
    })
    out = _parse_research_json(raw)
    assert len(out["targets"]) == 1
    assert out["targets"][0]["id"] == "P0-1"
    assert out["targets"][0]["file"] == "config.py"


def test_parse_json_in_code_fence():
    raw = '```json\n{"targets": [{"id":"P0-1","file":"a.py","pattern":"x"}]}\n```'
    out = _parse_research_json(raw)
    assert len(out["targets"]) == 1


def test_parse_json_inside_text():
    raw = 'Вот результат:\n{"targets":[{"id":"P0-1","file":"a.py","pattern":"x"}]}\nКонец.'
    out = _parse_research_json(raw)
    assert len(out["targets"]) == 1


def test_parse_garbage_returns_empty():
    assert _parse_research_json("") == {"version": 1, "targets": []}
    assert _parse_research_json("просто текст без json") == {"version": 1, "targets": []}
    assert _parse_research_json("{не json}") == {"version": 1, "targets": []}


# ─── 3. Нормализация ─────────────────────────────────────────────

def test_normalize_drops_targets_without_file_or_pattern():
    data = {
        "targets": [
            {"id": "P0-1", "file": "a.py", "pattern": "x", "check_kind": "contains"},
            {"id": "P0-2", "file": "", "pattern": "y"},   # нет file
            {"id": "P0-3", "file": "b.py", "pattern": ""},  # нет pattern
            "мусор",
            None,
        ]
    }
    out = _normalize_research(data)
    assert len(out["targets"]) == 1
    assert out["targets"][0]["id"] == "P0-1"


def test_normalize_masks_secret_in_pattern():
    """Если LLM случайно записала реальный пароль в pattern — маскируем."""
    data = {
        "targets": [{
            "id": "P0-1",
            "file": "config.py",
            "pattern": 'ADMIN_PASSWORD = "123"',
            "check_kind": "not_contains",
            "action": "убрать",
        }]
    }
    out = _normalize_research(data)
    pat = out["targets"][0]["pattern"]
    assert "123" not in pat, f"пароль не замаскирован: {pat!r}"
    assert "***" in pat or "ADMIN_PASSWORD" in pat


def test_normalize_invalid_check_kind_falls_back_to_contains():
    data = {"targets": [{
        "id": "P0-1", "file": "a.py", "pattern": "x",
        "check_kind": "что-то-странное",
    }]}
    out = _normalize_research(data)
    assert out["targets"][0]["check_kind"] == "contains"


# ─── 4. Сохранение ───────────────────────────────────────────────

def test_save_research_notes_writes_file(tmp_path):
    data = {
        "version": 1,
        "targets": [{
            "id": "P0-1", "priority": "P0", "file": "config.py",
            "pattern": r"ADMIN_PASSWORD\s*=", "check_kind": "not_contains",
            "action": "убрать",
        }],
    }
    _save_research_notes(str(tmp_path), data)
    path = tmp_path / ".ai_link" / "research_notes.json"
    assert path.exists()
    loaded = json.loads(path.read_text(encoding="utf-8"))
    assert loaded["targets"][0]["id"] == "P0-1"


def test_save_research_notes_masks_secrets_in_file(tmp_path):
    """Защита: даже если в data случайно попал секрет — в файле его не будет."""
    data = {
        "version": 1,
        "targets": [{
            "id": "P0-1", "file": "config.py",
            "pattern": 'ADMIN_PASSWORD = "supersecret123"',
            "check_kind": "not_contains",
            "action": "убрать",
        }],
    }
    _save_research_notes(str(tmp_path), data)
    text = (tmp_path / ".ai_link" / "research_notes.json").read_text(encoding="utf-8")
    assert "supersecret123" not in text, "секрет утёк в файл"


# ─── 5. Сама маскировка ──────────────────────────────────────────

def test_mask_secrets_replaces_quoted_values():
    masked = _mask_secrets_for_research('ADMIN_PASSWORD = "secret123"')
    assert "secret123" not in masked
    masked2 = _mask_secrets_for_research('API_KEY = "abcdef123456"')
    assert "abcdef123456" not in masked2

# ─── _normalize_research: v1.1 (acceptance[]) ────────────────
# B+ (19.10.2026): поддержка двух схем.

def test_normalize_research_v11_acceptance():
    """v1.1: acceptance[] сохраняется, status=pending, severity=P0."""
    data = {"targets": [{
        "id": "P0-1",
        "severity": "P0",
        "outcome": "No plaintext admin credential remains in source",
        "files_in_scope": ["config.py"],
        "files_forbidden": ["tests/**"],
        "acceptance": [{
            "type": "grep_absent",
            "pattern": r'ADMIN_PASSWORD\s*=\s*["\']',
            "glob": "**/*.py",
        }],
    }]}
    out = _normalize_research(data)
    assert len(out["targets"]) == 1
    t = out["targets"][0]
    assert t["id"] == "P0-1"
    assert t["severity"] == "P0"
    assert t["status"] == "pending"
    assert t["attempts"] == 0
    assert t["max_attempts"] == 3
    assert t["on_exhausted"] == "blocked"
    assert len(t["acceptance"]) == 1
    assert t["acceptance"][0]["type"] == "grep_absent"
    assert t["files_in_scope"] == ["config.py"]
    assert t["files_forbidden"] == ["tests/**"]


def test_normalize_research_v11_multiple_checks():
    """v1.1: несколько checks в acceptance[] — все сохраняются."""
    data = {"targets": [{
        "id": "P0-1", "severity": "P0",
        "acceptance": [
            {"type": "grep_absent", "pattern": "ADMIN", "glob": "**/*.py"},
            {"type": "file_exists", "path": ".gitignore"},
            {"type": "python_compile", "path": "config.py"},
        ],
    }]}
    out = _normalize_research(data)
    assert len(out["targets"]) == 1
    assert len(out["targets"][0]["acceptance"]) == 3


def test_normalize_research_v11_drops_bad_checks():
    """v1.1: неизвестный type отбрасывается, остальные остаются."""
    data = {"targets": [{
        "id": "P0-1", "severity": "P0",
        "acceptance": [
            {"type": "grep_absent", "pattern": "x", "glob": "**/*.py"},
            {"type": "unknown_type", "path": "y"},
        ],
    }]}
    out = _normalize_research(data)
    assert len(out["targets"]) == 1
    assert len(out["targets"][0]["acceptance"]) == 1
    assert out["targets"][0]["acceptance"][0]["type"] == "grep_absent"


def test_normalize_research_v11_empty_acceptance_drops():
    """v1.1: пустой acceptance[] → target отбрасывается."""
    data = {"targets": [{
        "id": "P0-1", "severity": "P0", "acceptance": [],
    }]}
    out = _normalize_research(data)
    assert len(out["targets"]) == 0


def test_normalize_research_old_schema_still_works():
    """Старая схема pattern/check_kind продолжает работать."""
    data = {"targets": [{
        "id": "P0-1", "priority": "P0", "file": "config.py",
        "pattern": r"ADMIN_PASSWORD\s*=", "check_kind": "not_contains",
        "action": "убрать пароль",
    }]}
    out = _normalize_research(data)
    assert len(out["targets"]) == 1
    t = out["targets"][0]
    assert t["file"] == "config.py"
    assert t["check_kind"] == "not_contains"
    assert "acceptance" not in t


def test_normalize_research_mixed_schemas():
    """Смесь: один v1.1, один v1 — оба сохраняются."""
    data = {"targets": [
        {"id": "P0-1", "severity": "P0",
         "acceptance": [{"type": "file_exists", "path": ".gitignore"}]},
        {"id": "P0-2", "file": "config.py",
         "pattern": "x", "check_kind": "contains"},
    ]}
    out = _normalize_research(data)
    assert len(out["targets"]) == 2
    # v1.1
    assert "acceptance" in out["targets"][0]
    assert "file" not in out["targets"][0]
    # v1
    assert "file" in out["targets"][1]
    assert "acceptance" not in out["targets"][1]

