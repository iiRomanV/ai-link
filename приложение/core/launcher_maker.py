"""AA: создаёт ярлыки запуска для проекта.

Два ярлыка:
  • Запуск.desktop — обычный запуск
    (GUI: Terminal=false, CLI: Terminal=true)
  • Запуск_с_терминалом.desktop — отладка
    (Terminal=true всегда, видно ошибки)

Вызывается оркестратором после успешного завершения задачи.
Не зависит от модели — постобработка.
"""
import logging
import os
import re
from pathlib import Path
from typing import Optional, Tuple

logger = logging.getLogger(__name__)


_GUI_MARKERS = (
    "pyside", "pyqt", "tkinter", "gtk", "wxpython",
    "qapplication", "mainwindow", "qtwidgets",
)


def _is_gui(project_dir: str) -> bool:
    """Определяет, GUI-приложение или CLI."""
    root = Path(project_dir)

    # 1. pyproject.toml — зависимости
    pt = root / "pyproject.toml"
    if pt.exists():
        try:
            t = pt.read_text(encoding="utf-8").lower()
            if any(m in t for m in _GUI_MARKERS):
                return True
        except Exception:
            pass

    # 2. requirements.txt
    rt = root / "requirements.txt"
    if rt.exists():
        try:
            t = rt.read_text(encoding="utf-8").lower()
            if any(m in t for m in _GUI_MARKERS):
                return True
        except Exception:
            pass

    # 3. main.py / src/*/__main__.py — импорты
    candidates = [root / "main.py"]
    src = root / "src"
    if src.exists():
        for pkg in src.iterdir():
            if pkg.is_dir():
                candidates.append(pkg / "__main__.py")
    for f in candidates:
        if f.exists():
            try:
                t = f.read_text(encoding="utf-8").lower()
                if any(m in t for m in _GUI_MARKERS):
                    return True
            except Exception:
                pass

    return False


def _detect_entry_point(project_dir: str) -> Optional[Tuple[str, str, str]]:
    """Ищет точку входа.

    Возвращает (exec_command, working_dir, "gui"|"cli") или None.
    """
    root = Path(project_dir)
    is_gui = _is_gui(project_dir)
    kind = "gui" if is_gui else "cli"

    # 1. main.py в корне
    if (root / "main.py").exists():
        return ("python3 main.py", str(root), kind)

    # 2. run.py / app.py
    for fname in ("run.py", "app.py"):
        if (root / fname).exists():
            return (f"python3 {fname}", str(root), kind)

    # 3. src/<pkg>/__main__.py
    src = root / "src"
    if src.exists() and src.is_dir():
        for pkg in sorted(src.iterdir()):
            if not pkg.is_dir():
                continue
            if pkg.name.startswith((".", "_")):
                continue
            if (pkg / "__main__.py").exists():
                return (
                    f"python3 -m {pkg.name}",
                    str(src),
                    kind,
                )

    return None

# ============================================================
# LAUNCHER-V2 (2026-10-10): ярлыки в меню Linux Mint
# ============================================================

import subprocess as _subprocess


def _find_entry_deep(project_dir: str):
    """LAUNCHER-V2: ищет точку входа ВЕЗДЕ (включая подпапки).

    Возвращает (cmd, work_dir, kind) или None.
    """
    root = Path(project_dir)

    # Порядок: корень → первая подпапка с main.py → src/
    candidates_dirs = [root]
    try:
        for d in sorted(root.iterdir()):
            if d.is_dir() and not d.name.startswith("."):
                candidates_dirs.append(d)
    except Exception:
        pass

    # 1. main.py в корне или в первой подпапке
    for d in candidates_dirs:
        if (d / "main.py").exists():
            kind = "gui" if _is_gui(str(d)) else "cli"
            return ("python3 main.py", str(d), kind)

    # 2. run.py / app.py
    for d in candidates_dirs:
        for fname in ("run.py", "app.py"):
            if (d / fname).exists():
                kind = "gui" if _is_gui(str(d)) else "cli"
                return (f"python3 {fname}", str(d), kind)

    # 3. run.sh
    for d in candidates_dirs:
        if (d / "run.sh").exists():
            return ("./run.sh", str(d), "cli")

    # 4. src/<pkg>/__main__.py
    for d in candidates_dirs:
        src_dir = d / "src"
        if src_dir.exists():
            for pkg in sorted(src_dir.iterdir()):
                if pkg.is_dir() and (pkg / "__main__.py").exists():
                    kind = "gui" if _is_gui(str(d)) else "cli"
                    return (f"python3 -m {pkg.name}", str(d), kind)

    return None


def _venv_python(project_dir: str) -> str:
    """LAUNCHER-V2: путь к python из venv, если он есть."""
    for venv_name in (".venv", "venv", "env"):
        p = Path(project_dir) / venv_name / "bin" / "python"
        if p.exists():
            return str(p)
    return "python3"


def _project_icon(project_dir: str) -> str:
    """LAUNCHER-V2: своя иконка проекта, иначе системная."""
    root = Path(project_dir)
    for icon_name in ("icon.png", "icon.svg", "logo.png",
                      "logo.svg", "icon.ico"):
        if (root / icon_name).exists():
            return str(root / icon_name)
        # в подпапке
        for sub in root.iterdir():
            if sub.is_dir() and not sub.name.startswith("."):
                if (sub / icon_name).exists():
                    return str(sub / icon_name)
    return "utilities-terminal"


def _slug(name: str) -> str:
    """LAUNCHER-V2: slug из имени для имени файла."""
    import re as _re
    s = (name or "project").lower()
    s = _re.sub(r"[^a-z0-9а-яё]+", "-", s)
    s = s.strip("-")[:40]
    return s or "project"


def _project_title(project_dir: str) -> str:
    """LAUNCHER-V2: имя из plan.json.title, иначе из папки."""
    import json as _json
    try:
        p = Path(project_dir) / ".ai_link" / "plan.json"
        if p.exists():
            d = _json.loads(p.read_text(encoding="utf-8"))
            t = (d.get("title") or "").strip()
            if t:
                return t[:60]
    except Exception:
        pass
    # Имя папки
    return Path(project_dir).name

# ============================================================
# LAUNCHER-V3 (2026-10-10): ярлыки в папке проекта
# ============================================================

def _project_icon(project_dir: str) -> str:
    """LAUNCHER-V3: своя иконка проекта, иначе системная."""
    root = Path(project_dir)
    for icon_name in ("icon.png", "icon.svg", "logo.png",
                      "logo.svg", "icon.ico"):
        if (root / icon_name).exists():
            return str(root / icon_name)
        for sub in root.iterdir():
            if sub.is_dir() and not sub.name.startswith("."):
                if (sub / icon_name).exists():
                    return str(sub / icon_name)
    return "utilities-terminal"


def _project_title(project_dir: str) -> str:
    """LAUNCHER-V3: имя из plan.json.title, иначе из папки."""
    import json as _json
    try:
        p = Path(project_dir) / ".ai_link" / "plan.json"
        if p.exists():
            d = _json.loads(p.read_text(encoding="utf-8"))
            t = (d.get("title") or "").strip()
            if t:
                return t[:60]
    except Exception:
        pass
    return Path(project_dir).name


def _find_entry_deep(project_dir: str):
    """LAUNCHER-V3: ищет точку входа ВЕЗДЕ (включая подпапки).

    Возвращает (cmd_list, work_dir, kind) или None.
    cmd_list — список аргументов для Exec (например ["python3", "main.py"]).
    """
    root = Path(project_dir)

    candidates_dirs = [root]
    try:
        for d in sorted(root.iterdir()):
            if d.is_dir() and not d.name.startswith("."):
                candidates_dirs.append(d)
    except Exception:
        pass

    # 1. main.py
    for d in candidates_dirs:
        if (d / "main.py").exists():
            kind = "gui" if _is_gui(str(d)) else "cli"
            return (["python3", "main.py"], str(d), kind)

    # 2. run.py / app.py
    for d in candidates_dirs:
        for fname in ("run.py", "app.py"):
            if (d / fname).exists():
                kind = "gui" if _is_gui(str(d)) else "cli"
                return (["python3", fname], str(d), kind)

    # 3. run.sh
    for d in candidates_dirs:
        if (d / "run.sh").exists():
            return (["./run.sh"], str(d), "cli")

    # 4. src/<pkg>/__main__.py
    for d in candidates_dirs:
        src_dir = d / "src"
        if src_dir.exists():
            for pkg in sorted(src_dir.iterdir()):
                if pkg.is_dir() and (pkg / "__main__.py").exists():
                    kind = "gui" if _is_gui(str(d)) else "cli"
                    return (["python3", "-m", pkg.name], str(d), kind)

    return None


def _venv_python(project_dir: str) -> str:
    """LAUNCHER-V3: путь к python из venv, если он есть."""
    for venv_name in (".venv", "venv", "env"):
        p = Path(project_dir) / venv_name / "bin" / "python"
        if p.exists():
            return str(p)
    return "python3"

# ============================================================
# LAUNCHER-V4 (2026-10-11): debug_run.sh + детект окружения
# ============================================================

def _detect_env(project_dir: str) -> str:
    """LAUNCHER-V4: определяет окружение проекта.

    Возвращает команду запуска:
      • .venv/bin/python main.py
      • poetry run python main.py
      • pipenv run python main.py
      • python3 main.py
    """
    root = Path(project_dir)

    # 1. venv
    for name in (".venv", "venv", "env"):
        py = root / name / "bin" / "python"
        if py.exists():
            return f"{py}"

    # 2. poetry (pyproject.toml с [tool.poetry])
    pyproject = root / "pyproject.toml"
    if pyproject.exists():
        try:
            t = pyproject.read_text(encoding="utf-8")
            if "[tool.poetry]" in t:
                return "poetry run python"
        except Exception:
            pass

    # 3. pipenv (Pipfile)
    if (root / "Pipfile").exists():
        return "pipenv run python"

    # 4. system
    return "python3"


def _make_debug_sh(project_dir: str, work_dir: str,
                   exec_line: str, title: str) -> str:
    """LAUNCHER-V4-FIX: скрипт с паузой в конце.

    Собирается через list + join — надёжнее чем f-строка с
    экранированием скобок.
    """
    lines = [
        "#!/bin/bash",
        "# AUTO-GENERATED by AI Link LAUNCHER-V4",
        f"# {title}",
        "# Не редактируй — при следующем прогоне перезапишется.",
        "",
        f'cd "{work_dir}" 2>/dev/null || {{',
        f'  echo "!!! Папка не найдена: {work_dir}"',
        '  echo "Нажми Enter для выхода"',
        "  read",
        "  exit 1",
        "}",
        "",
        f'echo "=== ЗАПУСК: {title} ==="',
        f'echo "Команда:   {exec_line}"',
        f'echo "Папка:     {work_dir}"',
        'echo ""',
        "",
        exec_line,
        "RC=$?",
        "",
        'echo ""',
        'echo "=== Код возврата: $RC ==="',
        "if [ $RC -ne 0 ]; then",
        '  echo "!!! Приложение завершилось с ошибкой."',
        '  echo "Проверь вывод выше."',
        "fi",
        'echo ""',
        'echo "Нажми Enter для выхода..."',
        "read",
    ]
    return "\n".join(lines) + "\n"


def create_launcher_shortcuts(project_dir: str) -> dict:
    """LAUNCHER-V4 (2026-10-11): 2 ярлыка + debug_run.sh.

    Создаёт в папке проекта:
      • Запуск.desktop                — обычный запуск
      • Запуск_с_терминалом.desktop   — запуск через debug_run.sh
      • debug_run.sh                  — скрипт с паузой в конце
    """
    root = Path(project_dir)
    if not root.exists():
        return {"created": [], "skipped": "no project dir"}

    detected = _find_entry_deep(project_dir)
    if not detected:
        return {"created": [], "skipped": "entry not found"}

    cmd_list, wdir, kind = detected
    title = _project_title(project_dir)
    icon = _project_icon(project_dir)

    # Детект окружения + venv
    env_cmd = _detect_env(wdir)
    if cmd_list and cmd_list[0] in ("python3", "python"):
        cmd_list = [env_cmd] + cmd_list[1:]
    exec_line = " ".join(cmd_list)

    # 1. debug_run.sh (в папке проекта, рядом с .desktop)
    sh_content = _make_debug_sh(
        project_dir, wdir, exec_line, title
    )
    sh_path = root / "debug_run.sh"
    sh_created = False
    try:
        if not sh_path.exists() or sh_path.read_text(encoding="utf-8") != sh_content:
            sh_path.write_text(sh_content, encoding="utf-8")
            os.chmod(sh_path, 0o755)
            sh_created = True
    except Exception as e:
        logger.warning("launcher-v4: debug_run.sh: %s", e)

    # 2. Exec для терминального ярлыка: bash + debug_run.sh
    # Путь в двойных кавычках — работают с пробелами.
    sh_for_exec = str(sh_path)
    term_exec = f'bash "{sh_for_exec}"'

    def _make(name, exec_str, terminal):
        return (
            "[Desktop Entry]\n"
            "Version=1.0\n"
            "Type=Application\n"
            f"Name={name}\n"
            f"Exec={exec_str}\n"
            f"Path={wdir}\n"
            f"Icon={icon}\n"
            f"Terminal={'true' if terminal else 'false'}\n"
        )

    std_content = _make(title, exec_line, terminal=False)
    term_content = _make(f"{title} (терминал)", term_exec,
                         terminal=True)

    created = []
    if sh_created:
        created.append(str(sh_path))

    for fname, content in [
        ("Запуск.desktop", std_content),
        ("Запуск_с_терминалом.desktop", term_content),
    ]:
        path = root / fname
        try:
            if path.exists():
                old = path.read_text(encoding="utf-8")
                if old == content:
                    continue
            path.write_text(content, encoding="utf-8")
            os.chmod(path, 0o755)
            created.append(str(path))
        except Exception as e:
            logger.warning("launcher-v4: не создал %s: %s", fname, e)

    logger.info(
        "launcher-v4: %d файлов в %s (kind=%s, env=%s)",
        len(created), root.name, kind, env_cmd,
    )
    return {
        "created": created,
        "kind": kind,
        "exec": exec_line,
        "env": env_cmd,
        "title": title,
        "work_dir": wdir,
    }
