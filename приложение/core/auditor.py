"""Аудит проекта — поиск проблем без изменения файлов.

Собирает отчёт: список {severity, category, file, line, message}.
Файлы НЕ меняются — только чтение и компиляция в памяти.
"""

from pathlib import Path
from typing import Dict, Any, List


SKIP_PARTS = {
    "__pycache__", ".ai_link", ".git", ".venv", "venv",
    "node_modules", ".pytest_cache", ".mypy_cache", ".ruff_cache",
}


def _should_skip(path: Path, base: Path) -> bool:
    try:
        rel = path.relative_to(base)
    except ValueError:
        return True
    for part in rel.parts:
        if part in SKIP_PARTS:
            return True
        if part.startswith(".") and part not in (".", ""):
            return True
    return False


def check_syntax(project_dir: str) -> List[Dict[str, Any]]:
    """Проверяет синтаксис всех .py через compile() (без .pyc)."""
    issues: List[Dict[str, Any]] = []
    base = Path(project_dir).resolve()
    for py in base.rglob("*.py"):
        if _should_skip(py, base):
            continue
        try:
            src = py.read_text(encoding="utf-8")
        except Exception:
            continue
        try:
            compile(src, str(py), "exec")
        except SyntaxError as e:
            issues.append({
                "severity": "high",
                "category": "syntax",
                "file": str(py.relative_to(base)),
                "line": e.lineno or 0,
                "message": str(e.msg or "syntax error"),
            })
        except Exception:
            continue
    return issues


def check_empty_files(project_dir: str) -> List[Dict[str, Any]]:
    """Находит пустые .py (кроме __init__.py)."""
    issues: List[Dict[str, Any]] = []
    base = Path(project_dir).resolve()
    for py in base.rglob("*.py"):
        if _should_skip(py, base):
            continue
        if py.name == "__init__.py":
            continue
        try:
            if py.stat().st_size == 0:
                issues.append({
                    "severity": "medium",
                    "category": "empty",
                    "file": str(py.relative_to(base)),
                    "line": 0,
                    "message": "пустой файл",
                })
        except Exception:
            continue
    return issues


def check_todos(project_dir: str) -> List[Dict[str, Any]]:
    """Ищет TODO / FIXME / XXX / HACK в настоящих комментариях.

    Использует tokenize — корректно отличает комментарии от строковых литералов.
    """
    import tokenize
    issues: List[Dict[str, Any]] = []
    base = Path(project_dir).resolve()
    markers = ("TODO", "FIXME", "XXX", "HACK")
    for py in base.rglob("*.py"):
        if _should_skip(py, base):
            continue
        if py.name == "auditor.py":
            continue
        try:
            with open(py, "r", encoding="utf-8") as f:
                for tok in tokenize.generate_tokens(f.readline):
                    if tok.type != tokenize.COMMENT:
                        continue
                    for m in markers:
                        if m in tok.string:
                            issues.append({
                                "severity": "low",
                                "category": "todo",
                                "file": str(py.relative_to(base)),
                                "line": tok.start[0],
                                "message": tok.string.strip()[:120],
                            })
                            break
        except Exception:
            continue
    return issues



def check_broken_links(project_dir: str) -> List[Dict[str, Any]]:
    """Обёртка над find_broken_links из индексатора."""
    issues: List[Dict[str, Any]] = []
    try:
        from core.project_indexer import find_broken_links
        for b in find_broken_links(project_dir):
            issues.append({
                "severity": "high",
                "category": "import",
                "file": b.get("file", ""),
                "line": 0,
                "message": f"битый импорт: {b.get('import', '')}",
            })
    except Exception:
        pass
    return issues


def run_audit(project_dir: str) -> Dict[str, Any]:
    """Собирает все проверки вместе.

    Возвращает:
        {
          "total": int,
          "by_severity": {"high": N, "medium": N, "low": N},
          "by_category": {"syntax": N, ...},
          "issues": [ ... ]
        }
    """
    issues: List[Dict[str, Any]] = []
    issues.extend(check_syntax(project_dir))
    issues.extend(check_empty_files(project_dir))
    issues.extend(check_todos(project_dir))
    issues.extend(check_broken_links(project_dir))

    by_severity = {"high": 0, "medium": 0, "low": 0}
    by_category: Dict[str, int] = {}
    for i in issues:
        sev = i.get("severity", "low")
        by_severity[sev] = by_severity.get(sev, 0) + 1
        cat = i.get("category", "other")
        by_category[cat] = by_category.get(cat, 0) + 1

    return {
        "total": len(issues),
        "by_severity": by_severity,
        "by_category": by_category,
        "issues": issues,
    }
