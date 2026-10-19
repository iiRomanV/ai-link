"""Пост-проверка после действий HandLink.

После создания или правки файла:
  • py_compile для .py — 100% точная проверка
  • pytest для проекта, если есть tests/ (с таймаутом)

При падении py_compile — вызывающий может откатить файл из .bak.
При падении pytest — только сообщение, без отката.
"""

import subprocess
import sys
from pathlib import Path
from typing import Dict, Any, Optional


PYTEST_TIMEOUT = 60       # секунд
PY_COMPILE_TIMEOUT = 15   # секунд


def check_py_compile(file_path: Path) -> Dict[str, Any]:
    """Проверяет, компилируется ли .py файл."""
    try:
        r = subprocess.run(
            [sys.executable, "-m", "py_compile", str(file_path)],
            capture_output=True,
            text=True,
            timeout=PY_COMPILE_TIMEOUT,
        )
        if r.returncode == 0:
            return {"ok": True, "error": None}
        return {
            "ok": False,
            "error": (r.stderr or r.stdout or "unknown")[:500],
        }
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "py_compile timeout"}
    except Exception as e:
        return {"ok": False, "error": str(e)[:300]}


def check_project_tests(project_dir: Path) -> Dict[str, Any]:
    """Запускает pytest в проекте, если есть tests/."""
    tests_dir = project_dir / "tests"
    if not tests_dir.exists() or not tests_dir.is_dir():
        return {"ok": True, "skipped": True, "error": None, "output": ""}

    try:
        r = subprocess.run(
            [sys.executable, "-m", "pytest", "tests/", "-q", "--no-header"],
            capture_output=True,
            text=True,
            timeout=PYTEST_TIMEOUT,
            cwd=str(project_dir),
        )
        output = (r.stdout + r.stderr)[:1500]
        if r.returncode == 0:
            return {"ok": True, "skipped": False, "error": None,
                    "output": output[-500:]}
        return {
            "ok": False,
            "skipped": False,
            "error": "pytest failed",
            "output": output[-1000:],
        }
    except subprocess.TimeoutExpired:
        return {"ok": False, "skipped": False,
                "error": "pytest timeout", "output": ""}
    except Exception as e:
        return {"ok": False, "skipped": False,
                "error": str(e)[:300], "output": ""}


def check_file(file_path: Path, project_dir: Path) -> Dict[str, Any]:
    """Полная проверка одного изменённого файла."""
    result: Dict[str, Any] = {
        "file": str(file_path.name),
        "py_compile": None,
        "pytest": None,
        "rolled_back": False,
    }

    # 1) py_compile — только для .py
    if file_path.suffix == ".py":
        result["py_compile"] = check_py_compile(file_path)

    # 2) pytest — только если файл НЕ в tests/
    try:
        rel = file_path.resolve().relative_to(project_dir.resolve())
        in_tests = rel.parts and rel.parts[0] == "tests"
    except Exception:
        in_tests = False

    if not in_tests:
        result["pytest"] = check_project_tests(project_dir)

    return result
