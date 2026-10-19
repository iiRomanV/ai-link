"""Индексатор проекта для AI Link.
Сканирует файлы проекта, извлекает символы из .py через ast,
строит граф зависимостей. Результат пишет в state.structure.
"""

import ast
import hashlib
import os
from pathlib import Path
from typing import Dict, Any, List, Set
from datetime import datetime

from core.project_state import update_structure


SKIP_DIRS = {
    "__pycache__", ".git", ".venv", "venv", "node_modules",
    ".ai_link", ".diagnostics", ".pytest_cache", ".mypy_cache",
    ".ruff_cache", ".idea", ".vscode", "архивы",
}

SKIP_EXTENSIONS = {
    ".pyc", ".pyo", ".so", ".dll", ".exe", ".bin",
    ".jpg", ".jpeg", ".png", ".gif", ".svg", ".ico",
    ".mp3", ".mp4", ".avi", ".mov",
    ".zip", ".tar", ".gz", ".rar", ".7z",
    ".pdf", ".doc", ".docx",
}

MAX_FILE_SIZE = 5 * 1024 * 1024  # 5 МБ — большие файлы только по размеру


def _should_skip_dir(dir_name: str) -> bool:
    return dir_name in SKIP_DIRS or dir_name.startswith(".")


def _should_skip_file(path: Path) -> bool:
    return path.suffix.lower() in SKIP_EXTENSIONS


def file_hash(path: Path, chunk_size: int = 65536) -> str:
    """MD5 содержимого файла. Для больших файлов — метка."""
    try:
        if path.stat().st_size > MAX_FILE_SIZE:
            return f">{MAX_FILE_SIZE // 1024 // 1024}MB"
        h = hashlib.md5()
        with open(path, "rb") as f:
            while True:
                chunk = f.read(chunk_size)
                if not chunk:
                    break
                h.update(chunk)
        return h.hexdigest()
    except Exception:
        return "error"


def extract_python_symbols(path: Path) -> Dict[str, Any]:
    """Извлекает классы, функции, импорты из .py через ast."""
    result: Dict[str, Any] = {"classes": [], "functions": [], "imports": []}
    try:
        src = path.read_text(encoding="utf-8")
    except Exception as e:
        result["error"] = f"read error: {e}"
        return result
    try:
        tree = ast.parse(src, filename=str(path))
    except SyntaxError as e:
        result["error"] = f"syntax error: {e}"
        return result
    except Exception as e:
        result["error"] = f"parse error: {e}"
        return result

    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            result["classes"].append(node.name)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            result["functions"].append(node.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                result["imports"].append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                result["imports"].append(node.module)

    result["classes"] = sorted(set(result["classes"]))
    result["functions"] = sorted(set(result["functions"]))
    result["imports"] = sorted(set(result["imports"]))
    return result


def index_project(project_dir: str) -> Dict[str, Any]:
    """Обходит проект и возвращает структуру."""
    base = Path(project_dir).resolve()
    files: List[Dict[str, Any]] = []
    symbols: Dict[str, Any] = {}
    dependencies: Dict[str, List[str]] = {}

    all_files: List[Path] = []
    for root, dirs, filenames in os.walk(base):
        dirs[:] = [d for d in dirs if not _should_skip_dir(d)]
        for fn in filenames:
            p = Path(root) / fn
            if _should_skip_file(p):
                continue
            all_files.append(p)

    # Карта: модуль (core.storage) → относительный путь (core/storage.py)
    py_map: Dict[str, str] = {}
    for p in all_files:
        rel = p.relative_to(base).as_posix()
        if p.suffix == ".py":
            mod_path = rel[:-3].replace("/", ".")
            if mod_path.endswith(".__init__"):
                mod_path = mod_path[:-9]
            py_map[mod_path] = rel

    for p in sorted(all_files):
        rel = p.relative_to(base).as_posix()
        try:
            st = p.stat()
            size = st.st_size
            mtime = datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds")
        except Exception:
            size = 0
            mtime = ""
        entry = {
            "path": rel,
            "purpose": "",
            "size_bytes": size,
            "hash": file_hash(p),
            "last_modified": mtime,
            "status": "done",
        }
        files.append(entry)

        if p.suffix == ".py":
            sym = extract_python_symbols(p)
            symbols[rel] = sym
            deps: Set[str] = set()
            for imp in sym.get("imports", []):
                if imp in py_map:
                    deps.add(py_map[imp])
                    continue
                parts = imp.split(".")
                for i in range(len(parts), 0, -1):
                    candidate = ".".join(parts[:i])
                    if candidate in py_map:
                        deps.add(py_map[candidate])
                        break
            dependencies[rel] = sorted(deps)
        else:
            dependencies[rel] = []

    return {
        "files": files,
        "symbols": symbols,
        "dependencies": dependencies,
    }


def update_project_index(project_dir: str) -> Dict[str, Any]:
    """Индексирует проект и сохраняет результат в state.structure."""
    index = index_project(project_dir)
    update_structure(
        project_dir,
        files=index["files"],
        symbols=index["symbols"],
        dependencies=index["dependencies"],
    )
    return {
        "files_count": len(index["files"]),
        "py_files": len(index["symbols"]),
    }


def get_dependencies(project_dir: str, file_path: str) -> List[str]:
    """Возвращает список файлов, от которых зависит указанный файл.

    file_path — путь относительно корня проекта (например 'ui/window.py').
    Возвращает список относительных путей.
    """
    from core.project_state import load_state
    state_path = Path(project_dir) / ".ai_link" / "state.json"
    if not state_path.exists():
        update_project_index(project_dir)
    state = load_state(project_dir)
    deps = state.get("structure", {}).get("dependencies", {})
    return list(deps.get(file_path, []))


def get_dependents(project_dir: str, file_path: str) -> List[str]:
    """Возвращает список файлов, которые зависят от указанного файла."""
    from core.project_state import load_state
    state_path = Path(project_dir) / ".ai_link" / "state.json"
    if not state_path.exists():
        update_project_index(project_dir)
    state = load_state(project_dir)
    deps = state.get("structure", {}).get("dependencies", {})
    result = []
    for src, targets in deps.items():
        if file_path in targets:
            result.append(src)
    return sorted(result)


def find_broken_links(project_dir: str) -> List[Dict[str, Any]]:
    """Ищет импорты, указывающие на несуществующие локальные модули.

    Возвращает список: [{"file": "ui/window.py", "import": "core.missing"}]
    """
    from core.project_state import load_state
    state_path = Path(project_dir) / ".ai_link" / "state.json"
    if not state_path.exists():
        update_project_index(project_dir)
    state = load_state(project_dir)
    structure = state.get("structure", {})
    files = structure.get("files", [])
    symbols = structure.get("symbols", {})

    known_modules = set()
    for f in files:
        p = f["path"]
        if p.endswith(".py"):
            mod = p[:-3].replace("/", ".")
            if mod.endswith(".__init__"):
                mod = mod[:-9]
            known_modules.add(mod)

    local_prefixes = {
        "core", "ui", "config", "db", "log_utils",
        "tests", "resources", "agents", "learning",
        "meta", "risk", "human", "mt5",
    }

    broken = []
    for src_path, sym in symbols.items():
        for imp in sym.get("imports", []):
            first = imp.split(".")[0]
            if first not in local_prefixes:
                continue
            if imp not in known_modules:
                broken.append({"file": src_path, "import": imp})
    return broken


def find_symbol_usages(project_dir: str, symbol: str,
                       skip_tests: bool = False) -> List[Dict[str, Any]]:
    """FIX SAFE-REPLACE: найти вызовы symbol по всем .py через ast.

    symbol — имя функции/класса. Возвращает список:
      [{"file": "tracker.py", "line": 42, "kind": "call"}]
    kind: "call" (func(...)), "attr" (obj.func), "name" (просто имя).

    skip_tests=True — игнорировать tests/ (для prod-кода).
    """
    import ast as _ast
    root = Path(project_dir).resolve()
    if not root.exists():
        return []
    results: List[Dict[str, Any]] = []
    for py in root.rglob("*.py"):
        try:
            rel = py.relative_to(root)
        except ValueError:
            continue
        rel_str = str(rel).replace("\\", "/")
        if "__pycache__" in rel_str or ".ai_link" in rel_str:
            continue
        if skip_tests and rel_str.startswith("tests/"):
            continue
        try:
            src_text = py.read_text(encoding="utf-8")
            tree = _ast.parse(src_text, filename=str(py))
        except Exception:
            continue
        for node in _ast.walk(tree):
            if isinstance(node, _ast.Call):
                f = node.func
                if isinstance(f, _ast.Name) and f.id == symbol:
                    results.append({"file": rel_str,
                                    "line": getattr(node, "lineno", 0),
                                    "kind": "call"})
                elif isinstance(f, _ast.Attribute) and f.attr == symbol:
                    results.append({"file": rel_str,
                                    "line": getattr(node, "lineno", 0),
                                    "kind": "attr"})
            elif isinstance(node, _ast.Name) and node.id == symbol:
                results.append({"file": rel_str,
                                "line": getattr(node, "lineno", 0),
                                "kind": "name"})
    return results

