"""REPO-MAP — сжатая карта проекта для LLM.

Строит <project>/.ai_link/repo_map.json на базе
core.project_indexer (ast-парсинг + граф зависимостей).

Зачем: brain не должен открывать весь проект. В LLM
подаётся карта + релевантные файлы, не всё.

API:
  build_repo_map(project_dir)     → dict
  save_repo_map(project_dir)      → путь
  load_repo_map(project_dir)      → dict
  render_for_llm(project_dir)     → компактный текст
  relevant_files(project_dir, q)  → список файлов под задачу
  map_summary(project_dir)        → короткая сводка
"""

import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List, Optional


def _map_path(project_dir: str) -> Path:
    return Path(project_dir) / ".ai_link" / "repo_map.json"


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def count_py_files(project_dir: str) -> int:
    """Быстро считает .py файлы в проекте (без полного парсинга).

    Нужно для brain-shadow: определить, есть ли .py в проекте,
    когда phase_result.files_created == 0 (PHASES-2 крутит code
    по этапам, на 2-3 проходе файлы уже есть).
    Пропускает __pycache__, .ai_link, скрытые папки.
    """
    p = Path(project_dir)
    if not p.exists():
        return 0
    count = 0
    for py in p.rglob("*.py"):
        try:
            parts = py.relative_to(p).parts
        except Exception:
            continue
        if any(part.startswith(".") or part == "__pycache__"
               for part in parts):
            continue
        count += 1
    return count


def build_repo_map(project_dir: str) -> Dict[str, Any]:
    """Строит карту проекта на базе project_indexer.index_project."""
    p = Path(project_dir)
    if not p.exists():
        return {
            "ts": _now(), "project_dir": str(p),
            "status": "failed",
            "error": f"project_dir не существует: {project_dir}",
            "files_total": 0, "py_files": 0,
            "modules": {}, "stats": {},
        }

    try:
        from core.project_indexer import index_project
        idx = index_project(project_dir)
    except Exception as e:
        return {
            "ts": _now(), "project_dir": str(p),
            "status": "failed",
            "error": f"index_project: {e}",
            "files_total": 0, "py_files": 0,
            "modules": {}, "stats": {},
        }

    files = idx.get("files", [])
    symbols = idx.get("symbols", {})
    deps = idx.get("dependencies", {})

    # Собираем модули — только .py
    modules: Dict[str, Any] = {}
    for f in files:
        path = f.get("path", "")
        if not path.endswith(".py"):
            continue
        sym = symbols.get(path, {})
        modules[path] = {
            "classes": sym.get("classes", []),
            "functions": sym.get("functions", []),
            "imports": sym.get("imports", []),
            "dependencies": deps.get(path, []),
            "size_bytes": f.get("size_bytes", 0),
            "hash": f.get("hash", ""),
        }

    # Статистика
    by_ext: Counter = Counter()
    for f in files:
        ext = Path(f["path"]).suffix or "(no ext)"
        by_ext[ext] += 1

    # Кто больше всего импортируется
    imported: Counter = Counter()
    for mod, data in modules.items():
        for dep in data.get("dependencies", []):
            imported[dep] += 1
    top_imported = [
        {"path": k, "count": v}
        for k, v in imported.most_common(15)
    ]

    return {
        "ts": _now(),
        "project_dir": str(p),
        "status": "ok",
        "files_total": len(files),
        "py_files": len(modules),
        "modules": modules,
        "stats": {
            "by_extension": dict(by_ext),
            "top_imported": top_imported,
        },
    }


def save_repo_map(project_dir: str) -> str:
    """Строит карту и пишет в <project>/.ai_link/repo_map.json."""
    data = build_repo_map(project_dir)
    try:
        path = _map_path(project_dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return str(path)
    except Exception:
        return ""


def load_repo_map(project_dir: str) -> Dict[str, Any]:
    """Читает repo_map.json ({} если нет)."""
    path = _map_path(project_dir)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def map_summary(project_dir: str) -> Dict[str, Any]:
    """Короткая сводка карты (без полного содержимого)."""
    m = load_repo_map(project_dir)
    if not m:
        m = build_repo_map(project_dir)
    modules = m.get("modules", {}) or {}
    # Группы по первому уровню пути
    groups: Counter = Counter()
    for path in modules:
        head = path.split("/", 1)[0] if "/" in path else "(root)"
        groups[head] += 1
    return {
        "files_total": m.get("files_total", 0),
        "py_files": m.get("py_files", 0),
        "status": m.get("status", "unknown"),
        "ts": m.get("ts", ""),
        "groups": dict(groups.most_common(20)),
        "top_imported": m.get("stats", {}).get("top_imported", [])[:10],
    }


def render_for_llm(project_dir: str, max_chars: int = 3000) -> str:
    """Компактное текстовое представление карты — для промпта.

    НЕ включает полный код. Только:
      • сводку (сколько файлов, группы)
      • топ-20 модулей (по числу зависимостей/размеру)
      • топ-10 по «кто чаще импортируется»
    """
    m = load_repo_map(project_dir)
    if not m:
        m = build_repo_map(project_dir)
    modules = m.get("modules", {}) or {}

    if not modules:
        return "КАРТА ПРОЕКТА: пусто (нет .py файлов)"

    # Сортируем модули: по количеству зависимостей ↓, потом по размеру ↓
    items = sorted(
        modules.items(),
        key=lambda kv: (len(kv[1].get("dependencies", [])),
                        kv[1].get("size_bytes", 0)),
        reverse=True,
    )

    lines: List[str] = []
    lines.append(
        f"КАРТА ПРОЕКТА: {m.get('files_total', 0)} файлов, "
        f"{m.get('py_files', 0)} .py"
    )

    # Группы
    groups: Counter = Counter()
    for path in modules:
        head = path.split("/", 1)[0] if "/" in path else "(root)"
        groups[head] += 1
    if groups:
        g_str = ", ".join(f"{k}({v})" for k, v in groups.most_common(12))
        lines.append(f"Группы: {g_str}")

    # Топ-импортируемые
    top_imp = m.get("stats", {}).get("top_imported", [])[:10]
    if top_imp:
        lines.append("Часто импортируются:")
        for t in top_imp:
            lines.append(f"  • {t['path']} ({t['count']})")

    # Топ модулей (до 20)
    lines.append("Ключевые модули (топ-20):")
    for path, data in items[:20]:
        cls = data.get("classes", [])
        fns = data.get("functions", [])
        dep_n = len(data.get("dependencies", []))
        # Компактно: классы — до 4, функции — до 4
        cls_s = ", ".join(cls[:4]) + ("..." if len(cls) > 4 else "")
        fns_s = ", ".join(fns[:4]) + ("..." if len(fns) > 4 else "")
        head = f"  • {path} (dep={dep_n})"
        extra = []
        if cls_s:
            extra.append(f"классы: {cls_s}")
        if fns_s:
            extra.append(f"функции: {fns_s}")
        if extra:
            head += " — " + "; ".join(extra)
        lines.append(head)

    text = "\n".join(lines)
    if len(text) > max_chars:
        text = text[:max_chars - 40] + "\n... [КАРТА ОБРЕЗАНА]"
    return text


def relevant_files(project_dir: str,
                   query: str,
                   top_k: int = 5) -> List[str]:
    """Ищет top_k файлов, релевантных задаче (простой скоринг).

    Скоринг: вхождения слов из query в ИМЕНИ файла (×5),
    в символах (×3), в содержимом (×1).
    """
    if not query:
        return []
    words = [w.lower() for w in query.split() if len(w) >= 3]
    if not words:
        return []

    m = load_repo_map(project_dir)
    modules = m.get("modules", {}) or {}
    if not modules:
        return []

    base = Path(project_dir)
    scores: Dict[str, int] = {}

    for path, data in modules.items():
        score = 0
        low_path = path.lower()
        for w in words:
            if w in low_path:
                score += 5
            for c in data.get("classes", []):
                if w in c.lower():
                    score += 3
            for fn in data.get("functions", []):
                if w in fn.lower():
                    score += 3
        # Вхождения в содержимом
        try:
            content = (base / path).read_text(encoding="utf-8").lower()
            for w in words:
                score += content.count(w)
        except Exception:
            pass
        if score > 0:
            scores[path] = score

    return [p for p, _ in sorted(scores.items(), key=lambda kv: -kv[1])[:top_k]]


def reset_repo_map(project_dir: str) -> None:
    """Удаляет repo_map.json (для тестов)."""
    path = _map_path(project_dir)
    if path.exists():
        try:
            path.unlink()
        except Exception:
            pass
