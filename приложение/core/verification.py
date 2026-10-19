"""VERIFICATION-RECEIPT — формальный отчёт проверок проекта.

Идея: LLM (review) предлагает — локальные инструменты ДОКАЗЫВАЮТ.
Собираем структурированный JSON вместо текста от LLM:

  {
    "ts": "...",
    "phase": "code",
    "run_id": "...",
    "syntax":  {"status": "passed|failed", "checked_files": N,
                "errors": [...]},
    "tests":   {"status": "passed|failed|skipped",
                "passed": N, "failed": N, "output_tail": "..."},
    "imports": {"status": "passed|failed", "broken": [...]},
    "files":   {"py_total": N, "empty": N},
    "ruff":    {"status": "passed|failed|unavailable", "issues": N},
    "git_diff": {"status": "present|not_git", "changed_files": N},
    "status":  "verified|failed|partial"
  }

Пишется в <project>/.ai_link/verification/<phase>_<ts>.json
Плюс — последний отчёт всегда в <project>/.ai_link/verification/last.json
"""

import json
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List, Optional


def _verification_dir(project_dir: str) -> Path:
    return Path(project_dir) / ".ai_link" / "verification"


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _check_syntax(project_dir: str) -> Dict[str, Any]:
    """Синтаксис через auditor.check_syntax (уже есть)."""
    try:
        from core.auditor import check_syntax
        errors = check_syntax(project_dir)
    except Exception as e:
        return {"status": "unknown", "checked_files": 0,
                "errors": [], "note": str(e)[:200]}
    # Сколько .py файлов вообще в проекте
    try:
        from core.auditor import _should_skip  # type: ignore
        base = Path(project_dir).resolve()
        total = sum(1 for p in base.rglob("*.py")
                    if not _should_skip(p, base))
    except Exception:
        total = -1
    return {
        "status": "failed" if errors else "passed",
        "checked_files": total,
        "errors": errors[:20],
    }


def _check_imports(project_dir: str) -> Dict[str, Any]:
    """Битые импорты через auditor.check_broken_links."""
    try:
        from core.auditor import check_broken_links
        broken = check_broken_links(project_dir)
    except Exception as e:
        return {"status": "unknown", "broken": [], "note": str(e)[:200]}
    return {
        "status": "failed" if broken else "passed",
        "broken": broken[:20],
    }


def _find_test_dirs(project_dir: str) -> list:
    """TESTS-NESTED (2026-10-11, Б46): tests/ в корне и 1 уровне."""
    base = Path(project_dir)
    dirs = []
    if (base / "tests").is_dir():
        dirs.append(base / "tests")
    _SKIP = {"node_modules", ".venv", "venv", "__pycache__",
             ".git", ".ai_link", ".pytest_cache"}
    try:
        for sub in sorted(base.iterdir()):
            if not sub.is_dir():
                continue
            if sub.name.startswith(".") or sub.name in _SKIP:
                continue
            if (sub / "tests").is_dir():
                dirs.append(sub / "tests")
    except Exception:
        pass
    return dirs


def _check_tests(project_dir: str, timeout: int = 90) -> Dict[str, Any]:
    """pytest во всех найденных tests/ (корень + подпапки)."""
    dirs = _find_test_dirs(project_dir)
    if not dirs:
        return {"status": "skipped", "passed": 0, "failed": 0,
                "output_tail": ""}
    base = Path(project_dir)
    rels = []
    for d in dirs:
        try:
            rels.append(str(d.relative_to(base)))
        except Exception:
            rels.append(str(d))
    try:
        r = subprocess.run(
            [sys.executable, "-m", "pytest"] + rels + ["-q",
             "--no-header", "--tb=no"],
            capture_output=True, text=True,
            timeout=timeout, cwd=str(project_dir),
        )
    except subprocess.TimeoutExpired:
        return {"status": "failed", "passed": 0, "failed": -1,
                "output_tail": "pytest timeout"}
    except Exception as e:
        return {"status": "unknown", "passed": 0, "failed": 0,
                "output_tail": str(e)[:200]}

    output = (r.stdout or "") + (r.stderr or "")
    passed = failed = 0
    for line in output.splitlines():
        line = line.strip()
        # "12 passed in 1.2s"  |  "3 failed, 9 passed in 2s"
        if "passed" in line and " in " in line:
            import re as _re
            m = _re.search(r"(\d+)\s+passed", line)
            if m:
                passed = int(m.group(1))
            m = _re.search(r"(\d+)\s+failed", line)
            if m:
                failed = int(m.group(1))
    # BUG-VERIFY-RC5 (2026-10-07): pytest возвращает rc=5,
    # когда не собрано НИ ОДНОГО теста («no tests ran»).
    # Это НЕ провал — просто нечего проверять.
    # Также ловим случай «no tests ran» через парсинг вывода.
    _no_tests = ("no tests ran" in output) or (
        passed == 0 and failed == 0
    )
    if r.returncode == 0:
        status = "passed"
    elif r.returncode == 5 or _no_tests:
        status = "skipped"
    else:
        status = "failed"
    return {
        "status": status,
        "passed": passed,
        "failed": failed,
        "output_tail": output[-300:],
    }


def _check_files(project_dir: str) -> Dict[str, Any]:
    """Общая статистика по .py + пустые файлы."""
    try:
        from core.auditor import check_empty_files, _should_skip  # type: ignore
        empty = check_empty_files(project_dir)
        base = Path(project_dir).resolve()
        total = sum(1 for p in base.rglob("*.py")
                    if not _should_skip(p, base))
    except Exception as e:
        return {"py_total": -1, "empty": -1, "note": str(e)[:200]}
    return {"py_total": total, "empty": len(empty)}


def _check_ruff(project_dir: str, timeout: int = 30) -> Dict[str, Any]:
    """ruff, если установлен. Иначе — unavailable."""
    exe = shutil.which("ruff")
    if not exe:
        return {"status": "unavailable", "issues": 0}
    try:
        r = subprocess.run(
            [exe, "check", "--output-format=concise", str(project_dir)],
            capture_output=True, text=True, timeout=timeout,
        )
    except Exception as e:
        return {"status": "unknown", "issues": 0, "note": str(e)[:200]}
    out = (r.stdout or "") + (r.stderr or "")
    lines = [l for l in out.splitlines() if l.strip()]
    if r.returncode == 0:
        return {"status": "passed", "issues": 0}
    return {"status": "failed", "issues": len(lines),
            "sample": lines[:10]}


def _check_git(project_dir: str) -> Dict[str, Any]:
    """git diff --stat, если проект — git-репозиторий."""
    if not shutil.which("git"):
        return {"status": "not_git", "changed_files": 0}
    try:
        r = subprocess.run(
            ["git", "-C", str(project_dir), "status", "--short"],
            capture_output=True, text=True, timeout=15,
        )
        if r.returncode != 0:
            return {"status": "not_git", "changed_files": 0}
    except Exception:
        return {"status": "not_git", "changed_files": 0}
    lines = [l for l in (r.stdout or "").splitlines() if l.strip()]
    return {"status": "present", "changed_files": len(lines),
            "sample": lines[:20]}


def build_receipt(project_dir: str,
                  phase: str = "",
                  run_id: str = "") -> Dict[str, Any]:
    """Собирает формальный отчёт проверок.

    Никогда не падает: любая часть может вернуть status=unknown.
    Итог status: verified | failed | partial.
    """
    p = Path(project_dir)
    if not p.exists():
        # Полная структура — чтобы клиенты не падали на r["syntax"].
        _empty = lambda: {"status": "unknown"}
        return {
            "ts": _now(), "phase": phase, "run_id": run_id,
            "project_dir": str(p),
            "status": "failed",
            "error": f"project_dir не существует: {project_dir}",
            "syntax": {"status": "unknown", "checked_files": 0, "errors": []},
            "imports": {"status": "unknown", "broken": []},
            "tests": {"status": "unknown", "passed": 0, "failed": 0,
                      "output_tail": ""},
            "files": {"py_total": 0, "empty": 0},
            "ruff": {"status": "unknown", "issues": 0},
            "git_diff": {"status": "unknown", "changed_files": 0},
        }

    syntax = _check_syntax(project_dir)
    imports = _check_imports(project_dir)
    tests = _check_tests(project_dir)
    files = _check_files(project_dir)
    ruff = _check_ruff(project_dir)
    git_diff = _check_git(project_dir)

    # Итоговый статус:
    #  • failed  — критичная проверка провалена
    #  • partial — критичная проверка unknown ИЛИ нечего проверять
    #  • verified — критичные прошли, есть что проверять
    # Не критичны для статуса: ruff=unavailable, git=not_git,
    # tests=skipped (нет tests/), tests=passed (всё ок).
    critical = (syntax.get("status"), imports.get("status"))
    tests_status = tests.get("status")
    if "failed" in critical or tests_status == "failed":
        overall = "failed"
    elif "unknown" in critical or tests_status == "unknown":
        overall = "partial"
    elif (syntax.get("checked_files") or 0) == 0:
        # Нечего проверять — не выдаём verified впустую.
        overall = "partial"
    else:
        overall = "verified"

    return {
        "ts": _now(),
        "phase": phase,
        "run_id": run_id,
        "project_dir": str(p),
        "status": overall,
        "syntax": syntax,
        "imports": imports,
        "tests": tests,
        "files": files,
        "ruff": ruff,
        "git_diff": git_diff,
    }


def save_receipt(project_dir: str, receipt: Dict[str, Any]) -> str:
    """Сохраняет отчёт: <phase>_<ts>.json + last.json.

    Возвращает путь к основному файлу (пусто при ошибке).
    """
    try:
        d = _verification_dir(project_dir)
        d.mkdir(parents=True, exist_ok=True)
        phase = (receipt.get("phase") or "phase").replace("/", "_")
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        main = d / f"{phase}_{ts}.json"
        main.write_text(
            json.dumps(receipt, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (d / "last.json").write_text(
            json.dumps(receipt, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return str(main)
    except Exception:
        return ""


def load_last_receipt(project_dir: str) -> Dict[str, Any]:
    """Читает последний отчёт ({} если нет)."""
    p = _verification_dir(project_dir) / "last.json"
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def build_and_save(project_dir: str,
                   phase: str = "",
                   run_id: str = "") -> Dict[str, Any]:
    """Build + save за один вызов. Возвращает receipt."""
    r = build_receipt(project_dir, phase=phase, run_id=run_id)
    r["saved_to"] = save_receipt(project_dir, r)
    return r


def reset_receipts(project_dir: str) -> None:
    """Удаляет все отчёты (для тестов)."""
    d = _verification_dir(project_dir)
    if d.exists():
        try:
            for f in d.glob("*.json"):
                f.unlink()
        except Exception:
            pass
