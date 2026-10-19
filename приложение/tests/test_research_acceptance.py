"""B (18.10.2026): acceptance gate по research_notes.json.

Проверяем:
  1. _compute_research_p0_status:
     • нет файла → unknown
     • пустой targets → unknown
     • все P0 выполнены (contains) → fulfilled
     • P0 не выполнен (contains, pattern не найден) → not_fulfilled
     • P0 не выполнен (not_contains, pattern найден) → not_fulfilled
     • P0 выполнен (not_contains, pattern НЕ найден) → fulfilled
     • битый regex → not_fulfilled (лучше заблокировать)
     • файл из target не существует → not_fulfilled

  2. _compute_acceptance(rcpt, project_dir):
     • P0 не выполнен → blocked (даже если rcpt=verified)
     • P0 выполнен → старая логика (verified → accepted)
     • research_notes нет → старая логика (unknown без rcpt)
"""
import json
from pathlib import Path

import pytest

from core.orchestrator import (
    _compute_research_p0_status,
    _compute_acceptance,
)


def _write_notes(project_dir, targets):
    d = Path(project_dir) / ".ai_link"
    d.mkdir(parents=True, exist_ok=True)
    (d / "research_notes.json").write_text(
        json.dumps({"version": 1, "targets": targets}, ensure_ascii=False),
        encoding="utf-8",
    )


def _write_file(project_dir, rel, text):
    p = Path(project_dir) / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


# ─── _compute_research_p0_status ───────────────────────────────

def test_p0_status_no_file_returns_unknown(tmp_path):
    assert _compute_research_p0_status(str(tmp_path)) == "unknown"


def test_p0_status_empty_targets_returns_unknown(tmp_path):
    _write_notes(tmp_path, [])
    assert _compute_research_p0_status(str(tmp_path)) == "unknown"


def test_p0_status_all_fulfilled_contains(tmp_path):
    _write_file(tmp_path, "a.py", "def foo():\n    return 42\n")
    _write_notes(tmp_path, [{
        "id": "P0-1", "file": "a.py",
        "pattern": r"def foo", "check_kind": "contains",
        "action": "x",
    }])
    assert _compute_research_p0_status(str(tmp_path)) == "fulfilled"


def test_p0_status_not_fulfilled_contains_missing(tmp_path):
    _write_file(tmp_path, "a.py", "def bar():\n    pass\n")
    _write_notes(tmp_path, [{
        "id": "P0-1", "file": "a.py",
        "pattern": r"def foo", "check_kind": "contains",
        "action": "x",
    }])
    assert _compute_research_p0_status(str(tmp_path)) == "not_fulfilled"


def test_p0_status_not_fulfilled_not_contains_found(tmp_path):
    # В файле ЕСТЬ плохой пароль → P0 не выполнен
    _write_file(tmp_path, "config.py", 'ADMIN_PASSWORD = "123"\n')
    _write_notes(tmp_path, [{
        "id": "P0-1", "file": "config.py",
        "pattern": r"ADMIN_PASSWORD\s*=",
        "check_kind": "not_contains",
        "action": "убрать пароль",
    }])
    assert _compute_research_p0_status(str(tmp_path)) == "not_fulfilled"


def test_p0_status_fulfilled_not_contains_missing(tmp_path):
    # Пароля НЕТ → P0 выполнен
    _write_file(tmp_path, "config.py", 'import os\nADMIN_PASSWORD = os.environ.get("PW")\n')
    _write_notes(tmp_path, [{
        "id": "P0-1", "file": "config.py",
        "pattern": r'ADMIN_PASSWORD\s*=\s*["\']\d+["\']',
        "check_kind": "not_contains",
        "action": "убрать пароль",
    }])
    assert _compute_research_p0_status(str(tmp_path)) == "fulfilled"


def test_p0_status_missing_file_is_not_fulfilled(tmp_path):
    _write_notes(tmp_path, [{
        "id": "P0-1", "file": "missing.py",
        "pattern": r"x", "check_kind": "contains",
    }])
    assert _compute_research_p0_status(str(tmp_path)) == "not_fulfilled"


def test_p0_status_bad_regex_is_not_fulfilled(tmp_path):
    _write_file(tmp_path, "a.py", "x")
    _write_notes(tmp_path, [{
        "id": "P0-1", "file": "a.py",
        "pattern": r"[unclosed", "check_kind": "contains",
    }])
    assert _compute_research_p0_status(str(tmp_path)) == "not_fulfilled"


# ─── _compute_acceptance ──────────────────────────────────────

def _rcpt_verified():
    return {
        "status": "verified",
        "syntax": {"status": "ok"},
        "imports": {"status": "ok"},
        "tests": {"status": "ok", "failed": 0},
    }


def test_acceptance_blocked_when_p0_not_fulfilled(tmp_path):
    """P0 не выполнен → blocked, даже если VERIFY=verified."""
    _write_file(tmp_path, "config.py", 'ADMIN_PASSWORD = "123"\n')
    _write_notes(tmp_path, [{
        "id": "P0-1", "file": "config.py",
        "pattern": r"ADMIN_PASSWORD\s*=",
        "check_kind": "not_contains",
    }])
    assert _compute_acceptance(_rcpt_verified(), str(tmp_path)) == "blocked"


def test_acceptance_accepted_when_p0_fulfilled(tmp_path):
    """P0 выполнен → старая логика (verified → accepted)."""
    _write_file(tmp_path, "config.py", "import os\nADMIN_PASSWORD = os.environ.get('PW')\n")
    _write_notes(tmp_path, [{
        "id": "P0-1", "file": "config.py",
        "pattern": r'ADMIN_PASSWORD\s*=\s*["\']\d+["\']',
        "check_kind": "not_contains",
    }])
    assert _compute_acceptance(_rcpt_verified(), str(tmp_path)) == "accepted"


def test_acceptance_no_notes_uses_old_logic(tmp_path):
    """research_notes нет → старая логика."""
    # rcpt=verified → accepted
    assert _compute_acceptance(_rcpt_verified(), str(tmp_path)) == "accepted"
    # rcpt=None → unknown
    assert _compute_acceptance(None, str(tmp_path)) == "unknown"


def test_acceptance_without_project_dir_uses_old_logic():
    """Если project_dir не передан — старая логика."""
    assert _compute_acceptance(_rcpt_verified()) == "accepted"
    assert _compute_acceptance(None) == "unknown"

# ─── v1.1: acceptance[] (B+ 19.10.2026) ──────────────────────

def test_p0_status_v11_acceptance_fulfilled(tmp_path):
    """v1.1: acceptance[].grep_absent, пароля нет → fulfilled."""
    _write_file(tmp_path, "config.py",
                "import os\nPW = os.environ.get('PW')\n")
    _write_notes(tmp_path, [{
        "id": "P0-1", "severity": "P0",
        "acceptance": [{
            "type": "grep_absent",
            "pattern": r'ADMIN_PASSWORD\s*=\s*["\']',
            "glob": "**/*.py",
        }],
    }])
    assert _compute_research_p0_status(str(tmp_path)) == "fulfilled"


def test_p0_status_v11_acceptance_not_fulfilled(tmp_path):
    """v1.1: acceptance[].grep_absent, пароль ЕСТЬ → not_fulfilled."""
    _write_file(tmp_path, "config.py", 'ADMIN_PASSWORD = "123"\n')
    _write_notes(tmp_path, [{
        "id": "P0-1", "severity": "P0",
        "acceptance": [{
            "type": "grep_absent",
            "pattern": r'ADMIN_PASSWORD\s*=\s*["\']',
            "glob": "**/*.py",
        }],
    }])
    assert _compute_research_p0_status(str(tmp_path)) == "not_fulfilled"


def test_p0_status_v11_file_exists(tmp_path):
    """v1.1: file_exists — .gitignore есть → fulfilled."""
    _write_file(tmp_path, ".gitignore", ".env\n")
    _write_notes(tmp_path, [{
        "id": "P0-1", "severity": "P0",
        "acceptance": [{"type": "file_exists", "path": ".gitignore"}],
    }])
    assert _compute_research_p0_status(str(tmp_path)) == "fulfilled"


def test_p0_status_mixed_old_and_new_schema(tmp_path):
    """Смесь: один target по v1.1, второй по v1.

    Оба должны проверяться. Если хоть один не выполнен → not_fulfilled.
    """
    _write_file(tmp_path, "config.py", 'ADMIN_PASSWORD = "123"\n')
    _write_file(tmp_path, "a.py", "x = 1\n")
    _write_notes(tmp_path, [
        # v1.1 — пароль есть → не выполнен
        {"id": "P0-1", "severity": "P0",
         "acceptance": [{"type": "grep_absent",
                         "pattern": r"ADMIN_PASSWORD",
                         "glob": "**/*.py"}]},
        # v1 — pattern/check_kind (старая схема)
        {"id": "P0-2", "file": "a.py",
         "pattern": r"x = 1", "check_kind": "contains"},
    ])
    assert _compute_research_p0_status(str(tmp_path)) == "not_fulfilled"


def test_acceptance_v11_blocks_when_not_fulfilled(tmp_path):
    """_compute_acceptance с v1.1: P0 не выполнен → blocked."""
    _write_file(tmp_path, "config.py", 'ADMIN_PASSWORD = "123"\n')
    _write_notes(tmp_path, [{
        "id": "P0-1", "severity": "P0",
        "acceptance": [{"type": "grep_absent",
                        "pattern": r"ADMIN_PASSWORD",
                        "glob": "**/*.py"}],
    }])
    assert _compute_acceptance(_rcpt_verified(), str(tmp_path)) == "blocked"


def test_acceptance_v11_accepted_when_fulfilled(tmp_path):
    """_compute_acceptance с v1.1: P0 выполнен → accepted."""
    _write_file(tmp_path, "config.py",
                "import os\nPW = os.environ.get('PW')\n")
    _write_notes(tmp_path, [{
        "id": "P0-1", "severity": "P0",
        "acceptance": [{"type": "grep_absent",
                        "pattern": r'ADMIN_PASSWORD\s*=\s*["\']',
                        "glob": "**/*.py"}],
    }])
    assert _compute_acceptance(_rcpt_verified(), str(tmp_path)) == "accepted"

