"""ENV-CHECK (A.3, 2026-10-04): проверка окружения системы.

Проверяет ТРИ уровня:
  • mandatory   — обязательные (без них Link не работает)
  • recommended — желательные (покрывают большинство задач)
  • optional    — максимум (для специализированных задач: игры,
                  3D, голос, PDF/Word/Excel, GUI-дизайнер)

Проверяет ДВА типа:
  • binary — программы в PATH (git, node, godot, ffmpeg...)
  • python — Python-пакеты (PySide6, openpyxl, python-docx...)

Использование (CLI):
  python3 -m core.env_check              # человекочитаемо
  python3 -m core.env_check --json       # JSON
  python3 -m core.env_check --level mandatory  # только обязательные

Использование (Python):
  from core.env_check import check_all, check_program, summary
  check_all()                    # всё
  check_all(level="mandatory")   # только обязательные
  check_program("python3")       # одна
"""

import importlib.util
import json
import shutil
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional


# ============================================================
# УРОВНИ
# ============================================================
LEVELS = ("mandatory", "recommended", "optional")
LEVEL_TITLES = {
    "mandatory": "ОБЯЗАТЕЛЬНЫЕ (минимум — Link работает)",
    "recommended": "ЖЕЛАТЕЛЬНЫЕ (большинство задач)",
    "optional": "МАКСИМУМ (специализированные задачи)",
}


# ============================================================
# КАТАЛОГ
# ============================================================
# Поля:
#   kind       "binary" | "python"
#   cmd        имя команды (binary) или имя для отображения
#   args       аргументы для версии (binary)
#   import_name имя модуля Python (python)
#   category   core/build/media/game/audio/doc/gui/automation/tool/ui
#   level      mandatory/recommended/optional
#   title      человеческое имя
#   hint       что это и зачем
KNOWN_PROGRAMS: Dict[str, Dict[str, Any]] = {

    # ==========================================================
    # MANDATORY — без этого Link не работает
    # ==========================================================
    "python3": {
        "apt_pkg": "python3",
        "alt_names": ["python"],
        "kind": "binary", "cmd": "python3", "args": ["--version"],
        "category": "core", "level": "mandatory",
        "title": "Python 3",
        "hint": "Основной язык. Нужен всегда.",
    },
    "pip3": {
        "apt_pkg": "python3-pip",
        "kind": "binary", "cmd": "pip3", "args": ["--version"],
        "category": "core", "level": "mandatory",
        "title": "pip (Python)",
        "hint": "Установка Python-пакетов.",
    },
    "git": {
        "apt_pkg": "git",
        "kind": "binary", "cmd": "git", "args": ["--version"],
        "category": "core", "level": "mandatory",
        "title": "Git",
        "hint": "Контроль версий (обязательно для бэкапов).",
    },
    "sqlite3": {
        "apt_pkg": "sqlite3",
        "kind": "binary", "cmd": "sqlite3", "args": ["--version"],
        "category": "core", "level": "mandatory",
        "title": "SQLite",
        "hint": "Лёгкая БД (используется в Link).",
    },
    "xdotool": {
        "apt_pkg": "xdotool",
        "kind": "binary", "cmd": "xdotool", "args": ["--version"],
        "category": "ui", "level": "mandatory",
        "title": "xdotool",
        "hint": "Управление окнами X11 (фокус браузера).",
    },
    "wmctrl": {
        "apt_pkg": "wmctrl",
        "kind": "binary", "cmd": "wmctrl", "args": ["--version"],
        "category": "ui", "level": "mandatory",
        "title": "wmctrl",
        "hint": "Управление окнами X11 (фокус браузера).",
    },
    "systemd-inhibit": {
        "apt_pkg": "systemd",
        "kind": "binary", "cmd": "systemd-inhibit", "args": ["--version"],
        "category": "ui", "level": "mandatory",
        "title": "systemd-inhibit",
        "hint": "Держит ПК в тонусе во время прогона.",
    },
    "pyside6": {
        "pip_pkg": "PySide6",
        "kind": "python", "import_name": "PySide6",
        "category": "ui", "level": "mandatory",
        "title": "PySide6",
        "hint": "GUI-фреймворк Link (Qt для Python).",
    },
    "requests": {
        "pip_pkg": "requests",
        "kind": "python", "import_name": "requests",
        "category": "core", "level": "mandatory",
        "title": "requests",
        "hint": "HTTP-запросы (GigaChat API и др.).",
    },

    # ==========================================================
    # RECOMMENDED — покрывают большинство задач
    # ==========================================================
    "node": {
        "apt_pkg": "nodejs",
        "alt_names": ["nodejs"],
        "kind": "binary", "cmd": "node", "args": ["--version"],
        "category": "build", "level": "recommended",
        "title": "Node.js",
        "hint": "JavaScript/TypeScript-проекты, Electron.",
    },
    "npm": {
        "apt_pkg": "npm",
        "kind": "binary", "cmd": "npm", "args": ["--version"],
        "category": "build", "level": "recommended",
        "title": "npm",
        "hint": "Пакетный менеджер Node.js.",
    },
    "g++": {
        "apt_pkg": "g++",
        "kind": "binary", "cmd": "g++", "args": ["--version"],
        "category": "build", "level": "recommended",
        "title": "g++ (C++)",
        "hint": "Компилятор C++.",
    },
    "make": {
        "apt_pkg": "make",
        "kind": "binary", "cmd": "make", "args": ["--version"],
        "category": "build", "level": "recommended",
        "title": "make",
        "hint": "Сборка C/C++-проектов.",
    },
    "ffmpeg": {
        "apt_pkg": "ffmpeg",
        "kind": "binary", "cmd": "ffmpeg", "args": ["-version"],
        "category": "media", "level": "recommended",
        "title": "FFmpeg",
        "hint": "Обработка видео/аудио (обрезка, конвертация).",
    },
    # ARCHIVE (2026-10-08): распаковка чужих проектов из архивов.
    # DOCS (2026-10-08): работа с PDF/Word/Excel/MD.
    "pandoc": {
        "apt_pkg": "pandoc",
        "kind": "binary", "cmd": "pandoc", "args": ["--version"],
        "category": "doc", "level": "recommended",
        "title": "Pandoc",
        "hint": "Конвертация MD ↔ PDF ↔ DOCX ↔ HTML. "
                "Нужен для отчётов и документации.",
    },
    "pdftotext": {
        "apt_pkg": "poppler-utils",
        "kind": "binary", "cmd": "pdftotext", "args": ["-v"],
        "category": "doc", "level": "recommended",
        "title": "pdftotext (poppler-utils)",
        "hint": "Извлечение текста из PDF. Быстро и надёжно.",
    },
    "prettier": {
        "npm_pkg": "prettier",
        "alt_names": ["prettier"],
        "kind": "binary", "cmd": "prettier", "args": ["--version"],
        "category": "build", "level": "recommended",
        "title": "Prettier",
        "hint": "Форматирование JS/TS/JSON/YAML/MD. Ставится через npm.",
    },
    # OPTIONAL (2026-10-08)
    "libreoffice": {
        "apt_pkg": "libreoffice",
        "kind": "binary", "cmd": "libreoffice", "args": ["--version"],
        "category": "doc", "level": "optional",
        "title": "LibreOffice",
        "hint": "Просмотр/создание Office-файлов. Headless-конвертация "
                "(libreoffice --headless --convert-to pdf).",
    },
    "tesseract": {
        "apt_pkg": "tesseract-ocr",
        "kind": "binary", "cmd": "tesseract", "args": ["--version"],
        "category": "doc", "level": "optional",
        "title": "Tesseract OCR",
        "hint": "Распознавание текста из картинок/сканов.",
    },
    "antiword": {
        "apt_pkg": "antiword",
        "kind": "binary", "cmd": "antiword", "args": ["-h"],
        "category": "doc", "level": "optional",
        "title": "antiword",
        "hint": "Извлечение текста из старых .doc (Word 97). "
                "Для .docx — python-docx.",
    },
    "html2text": {
        "apt_pkg": "html2text",
        "kind": "binary", "cmd": "html2text", "args": ["--version"],
        "category": "doc", "level": "optional",
        "title": "html2text",
        "hint": "Конвертация HTML в Markdown-текст.",
    },
    "js-beautify": {
        "npm_pkg": "js-beautify",
        "kind": "binary", "cmd": "js-beautify", "args": ["--version"],
        "category": "build", "level": "optional",
        "title": "js-beautify",
        "hint": "Читаемое форматирование JS. Ставится через npm.",
    },
    "7z": {
        "apt_pkg": "p7zip-full",
        "kind": "binary", "cmd": "7z", "args": ["i"],
        "category": "tool", "level": "recommended",
        "title": "7-Zip",
        "hint": "Распаковка .7z/.zip/.tar/.gz/.rar. "
                "Нужен для чужих проектов из архива.",
    },
    "unrar": {
        "apt_pkg": "unrar",
        "kind": "binary", "cmd": "unrar", "args": ["--version"],
        "category": "tool", "level": "recommended",
        "title": "unrar",
        "hint": "Распаковка .rar. Часто отдельным пакетом. "
                "Без него .rar-проект не распаковать.",
    },
    "ollama": {
        "apt_pkg": "",
        "kind": "binary", "cmd": "ollama", "args": ["--version"],
        "category": "llm", "level": "recommended",
        "title": "Ollama",
        "hint": "Локальный запуск LLM (нужно ≥16 ГБ RAM для 7B).",
        "install_url": "https://ollama.com/download/linux",
        "install_hint": "curl -fsSL https://ollama.com/install.sh | sh",
    },
    "godot": {
        "apt_pkg": "godot3",
        "alt_names": ["godot3", "godot4", "Godot"],
        "kind": "binary", "cmd": "godot", "args": ["--version"],
        "category": "game", "level": "recommended",
        "title": "Godot",
        "hint": "Игровой движок (2D/3D, бесплатный).",
    },
    # Python-пакеты для документов
    "openpyxl": {
        "pip_pkg": "openpyxl",
        "kind": "python", "import_name": "openpyxl",
        "category": "doc", "level": "recommended",
        "title": "openpyxl",
        "hint": "Чтение/запись Excel (.xlsx).",
    },
    "python-docx": {
        "pip_pkg": "python-docx",
        "kind": "python", "import_name": "docx",
        "category": "doc", "level": "recommended",
        "title": "python-docx",
        "hint": "Чтение/запись Word (.docx).",
    },
    "pypdf": {
        "pip_pkg": "pypdf",
        "kind": "python", "import_name": "pypdf",
        "category": "doc", "level": "recommended",
        "title": "pypdf",
        "hint": "Чтение/склейка/разбивка PDF.",
    },
    "pdfplumber": {
        "pip_pkg": "pdfplumber",
        "kind": "python", "import_name": "pdfplumber",
        "category": "doc", "level": "recommended",
        "title": "pdfplumber",
        "hint": "Извлечение текста/таблиц из PDF.",
    },
    "pandas": {
        "pip_pkg": "pandas",
        "kind": "python", "import_name": "pandas",
        "category": "doc", "level": "recommended",
        "title": "pandas",
        "hint": "Таблицы, CSV, анализ данных.",
    },

    # ==========================================================
    # OPTIONAL — максимум, специализированные задачи
    # ==========================================================
    "cmake": {
        "apt_pkg": "cmake",
        "kind": "binary", "cmd": "cmake", "args": ["--version"],
        "category": "build", "level": "optional",
        "title": "CMake",
        "hint": "Система сборки C/C++ (крупные проекты).",
    },
    "blender": {
        "apt_pkg": "blender",
        "kind": "binary", "cmd": "blender", "args": ["--version"],
        "category": "game", "level": "optional",
        "title": "Blender",
        "hint": "3D-моделирование, рендер, анимация.",
    },
    "gimp": {
        "apt_pkg": "gimp",
        "kind": "binary", "cmd": "gimp", "args": ["--version"],
        "category": "media", "level": "optional",
        "title": "GIMP",
        "hint": "Растровая графика (фото, текстуры).",
    },
    "inkscape": {
        "apt_pkg": "inkscape",
        "kind": "binary", "cmd": "inkscape", "args": ["--version"],
        "category": "media", "level": "optional",
        "title": "Inkscape",
        "hint": "Векторная графика (SVG, иконки, лого).",
    },
    "krita": {
        "apt_pkg": "krita",
        "kind": "binary", "cmd": "krita", "args": ["--version"],
        "category": "media", "level": "optional",
        "title": "Krita",
        "hint": "Цифровая живопись, спрайты.",
    },
    "audacity": {
        "apt_pkg": "audacity",
        "kind": "binary", "cmd": "audacity", "args": ["--version"],
        "category": "audio", "level": "optional",
        "title": "Audacity",
        "hint": "Редактор аудио (запись, чистка, монтаж).",
    },
    "whisper": {
        "kind": "python", "import_name": "whisper",
        "pip_pkg": "openai-whisper",
        "category": "audio", "level": "optional",
        "title": "Whisper (STT)",
        "hint": "Распознавание речи. pip: openai-whisper. "
                "Нужен ffmpeg для аудиоформатов.",
    },
    "piper": {
        "kind": "binary", "cmd": "piper", "no_version": True,
        "apt_pkg": "",
        "alt_names": ["piper-tts"],
        "category": "audio", "level": "optional",
        "title": "Piper (TTS)",
        "hint": "Синтез речи. Ставится вручную (GitHub "
                "rhasspy/piper) или через snap.",
        "install_url": "https://github.com/rhasspy/piper/releases",
        "install_hint": "Скачай релиз, распакуй piper в /usr/local/bin",
    },
    "espeak-ng": {
        "apt_pkg": "espeak-ng",
        "kind": "binary", "cmd": "espeak-ng", "args": ["--version"],
        "category": "audio", "level": "optional",
        "title": "eSpeak NG",
        "hint": "Простой TTS (запасной вариант).",
    },
    "docker": {
        "apt_pkg": "docker.io",
        "kind": "binary", "cmd": "docker", "args": ["--version"],
        "category": "tool", "level": "optional",
        "title": "Docker",
        "hint": "Контейнеры (изолированные окружения).",
    },
    "pyside6-designer": {
        "apt_pkg": "",
        "kind": "binary", "cmd": "pyside6-designer", "no_version": True,
        "category": "gui", "level": "optional",
        "title": "Qt Designer (PySide6)",
        "hint": "Визуальный редактор GUI: перетаскивай кнопки, "
                "списки, поля — как в VBA/Visual Studio.",
        "install_url": "https://doc.qt.io/qtforpython-6/",
        "install_hint": "Уже входит в PySide6. Обнови: pip3 install --upgrade PySide6",
    },
    "playwright": {
        "pip_pkg": "playwright",
        "kind": "python", "import_name": "playwright",
        "category": "automation", "level": "optional",
        "title": "Playwright",
        "hint": "Управление браузером (альтернатива Selenium).",
    },
    "selenium": {
        "pip_pkg": "selenium",
        "kind": "python", "import_name": "selenium",
        "category": "automation", "level": "optional",
        "title": "Selenium",
        "hint": "Управление браузером (классика).",
    },
    "pyttsx3": {
        "pip_pkg": "pyttsx3",
        "kind": "python", "import_name": "pyttsx3",
        "category": "audio", "level": "optional",
        "title": "pyttsx3",
        "hint": "TTS через системные движки (просто).",
    },
    "speechrecognition": {
        "pip_pkg": "SpeechRecognition",
        "kind": "python", "import_name": "speech_recognition",
        "category": "audio", "level": "optional",
        "title": "SpeechRecognition",
        "hint": "STT через разные движки (просто).",
    },

    # ==========================================================
    # ENV-CHECK ADD-ON (2026-10-08) — линтеры/форматтеры/JSON
    # ==========================================================

    "eslint": {
        "npm_pkg": "eslint",
        "alt_names": ["eslint"],
        "kind": "binary", "cmd": "eslint", "args": ["--version"],
        "category": "build", "level": "recommended",
        "title": "ESLint",
        "hint": "Проверка JS/TS-кода на ошибки. Ставится через npm.",
    },
    "typescript": {
        "npm_pkg": "typescript",
        "alt_names": ["tsc"],
        "kind": "binary", "cmd": "tsc", "args": ["--version"],
        "category": "build", "level": "optional",
        "title": "TypeScript (tsc)",
        "hint": "Компилятор TypeScript. Ставится через npm.",
    },
    "ruff": {
        "pip_pkg": "ruff",
        "alt_names": ["ruff"],
        "kind": "binary", "cmd": "ruff", "args": ["--version"],
        "category": "build", "level": "recommended",
        "title": "Ruff (линтер Python)",
        "hint": "Быстрый линтер Python. Ставится через pip.",
    },
    "mypy": {
        "pip_pkg": "mypy",
        "alt_names": ["mypy"],
        "kind": "binary", "cmd": "mypy", "args": ["--version"],
        "category": "build", "level": "optional",
        "title": "Mypy (типы Python)",
        "hint": "Проверка типов Python. Ставится через pip.",
    },
    "shellcheck": {
        "apt_pkg": "shellcheck",
        "kind": "binary", "cmd": "shellcheck", "args": ["--version"],
        "category": "tool", "level": "optional",
        "title": "ShellCheck",
        "hint": "Проверка bash-скриптов на ошибки.",
    },
    "jq": {
        "apt_pkg": "jq",
        "kind": "binary", "cmd": "jq", "args": ["--version"],
        "category": "tool", "level": "recommended",
        "title": "jq",
        "hint": "Разбор/фильтрация JSON в командной строке.",
    },
    "yq": {
        "pip_pkg": "yq",
        "kind": "binary", "cmd": "yq", "args": ["--version"],
        "category": "tool", "level": "optional",
        "title": "yq",
        "hint": "YAML на командной строке. Ставится через pip.",
    },
}


# ============================================================
# КЭШ
# ============================================================
_CACHE: Dict[str, Dict[str, Any]] = {}
_CACHE_TTL = 300


import os as _os

# PATH-EXTEND (2026-10-08): GUI-процесс Link наследует урезанный
# PATH (без ~/.npm-global/bin, ~/.cargo/bin и т.п.). Из-за этого
# shutil.which не находит npm-пакеты, хотя они установлены.
# Расширяем PATH для проверки И для subprocess.
_EXTRA_PATH_DIRS = [
    _os.path.expanduser("~/.local/bin"),
    _os.path.expanduser("~/.npm-global/bin"),
    _os.path.expanduser("~/.cargo/bin"),
    _os.path.expanduser("~/.mimocode/bin"),
    "/usr/local/bin",
    "/snap/bin",
]


def _extended_path() -> str:
    """PATH процесса + стандартные пользовательские папки."""
    base = _os.environ.get("PATH", "")
    parts = [p for p in base.split(":") if p]
    for d in _EXTRA_PATH_DIRS:
        if d not in parts:
            parts.append(d)
    return ":".join(parts)


def _extended_env() -> dict:
    """Копия os.environ с расширенным PATH (для subprocess)."""
    env = _os.environ.copy()
    env["PATH"] = _extended_path()
    return env


def clear_cache() -> None:
    _CACHE.clear()


import re as _re

# Паттерн: X.Y или X.Y.Z (минимум 2 компонента).
# Так мы не ловим даты (2022-01-06), хеши и copyright.
_VERSION_RE = _re.compile(r"\d+(?:\.\d+){1,3}")


def _parse_version(raw: str, cmd: str) -> str:
    """Извлекает ЧИСТУЮ версию из вывода команды.

    Ищем первое X.Y или X.Y.Z. Всё остальное (имя программы,
    путь, copyright, даты, хеши) — отбрасываем.

    Примеры:
      'Python 3.10.12'                        -> '3.10.12'
      'git version 2.34.1'                    -> '2.34.1'
      'v20.10.0'                              -> '20.10.0'
      'ffmpeg 4.4.2-0ubuntu0.22.04.1 ...'     -> '4.4.2'
      'cmake version 3.22.1'                  -> '3.22.1'
      'sqlite3 3.37.2 2022-01-06 13:25:41'    -> '3.37.2'
      'blender Color management: ...'         -> '' (нет версии)
      'audacity Attempting to connect ...'    -> '' (нет версии)
    """
    if not raw:
        return ""
    m = _VERSION_RE.search(raw)
    if m:
        return m.group(0)[:30]
    return ""


def _check_python_package(import_name: str) -> Dict[str, Any]:
    """Проверяет Python-пакет через importlib (без импорта)."""
    try:
        spec = importlib.util.find_spec(import_name)
        if spec is None:
            return {"installed": False, "version": "", "path": "",
                    "raw": "", "error": "модуль не найден"}
        # Версию пробуем через importlib.metadata
        version = ""
        try:
            from importlib.metadata import version as _v
            version = _v(import_name)
        except Exception:
            try:
                mod = __import__(import_name)
                version = getattr(mod, "__version__", "")
            except Exception:
                pass
        return {
            "installed": True, "version": version or "",
            "path": getattr(spec, "origin", "") or "",
            "raw": "", "error": "",
        }
    except Exception as e:
        return {"installed": False, "version": "", "path": "",
                "raw": "", "error": str(e)[:200]}


def check_program(name: str, use_cache: bool = True) -> Dict[str, Any]:
    """Проверяет одну программу или Python-пакет."""
    meta = KNOWN_PROGRAMS.get(name, {})
    kind = meta.get("kind", "binary")
    category = meta.get("category", "other")
    level = meta.get("level", "recommended")
    title = meta.get("title", name)
    hint = meta.get("hint", "")

    if use_cache:
        cached = _CACHE.get(name)
        if cached and (time.time() - cached.get("_ts", 0)) < _CACHE_TTL:
            return {k: v for k, v in cached.items() if not k.startswith("_")}

    result: Dict[str, Any] = {
        "name": name, "kind": kind, "category": category,
        "level": level, "title": title, "hint": hint,
        "installed": False, "path": "", "version": "",
        "raw": "", "error": "",
    }

    if kind == "python":
        import_name = meta.get("import_name", name)
        r = _check_python_package(import_name)
        result.update(r)
    else:
        cmd = meta.get("cmd", name)
        # ALT-NAMES (A.3, 2026-10-04): некоторые программы
        # устанавливаются под другим именем (godot → godot3,
        # node → nodejs). Проверяем все варианты.
        candidates = [cmd] + list(meta.get("alt_names") or [])
        path = ""
        matched_cmd = cmd
        _ext_path = _extended_path()
        for cand in candidates:
            _p = shutil.which(cand, path=_ext_path)
            if _p:
                path = _p
                matched_cmd = cand
                break
        if not path:
            result["error"] = "не найдено в PATH"
        elif meta.get("no_version"):
            # no_version: GUI-программа, --version/--help открывает окно.
            # Просто фиксируем, что установлена.
            result["path"] = path
            result["installed"] = True
            result["version"] = ""
        else:
            args = list(meta.get("args") or ["--version"])
            result["path"] = path
            try:
                proc = subprocess.run(
                    [matched_cmd] + args, capture_output=True,
                    text=True, timeout=5, env=_extended_env(),
                )
                raw = (proc.stdout or proc.stderr or "").strip()
                result["raw"] = raw[:200]
                result["version"] = _parse_version(raw, cmd)
                result["installed"] = True
            except subprocess.TimeoutExpired:
                result["installed"] = True
                result["error"] = "timeout при получении версии"
            except Exception as e:
                result["installed"] = True
                result["error"] = str(e)[:200]

    _CACHE[name] = {**result, "_ts": time.time()}
    return result


def check_all(use_cache: bool = True,
              level: Optional[str] = None) -> Dict[str, Dict[str, Any]]:
    """Проверяет все (или одного уровня) программы."""
    names = [
        n for n, m in KNOWN_PROGRAMS.items()
        if level is None or m.get("level") == level
    ]
    return {n: check_program(n, use_cache=use_cache) for n in names}


def summary() -> Dict[str, Any]:
    """Сводка: сколько установлено/отсутствует, по уровням и категориям."""
    all_results = check_all(use_cache=False)
    installed = [n for n, r in all_results.items() if r["installed"]]
    missing = [n for n, r in all_results.items() if not r["installed"]]

    by_level: Dict[str, Dict[str, List[str]]] = {lvl: {"installed": [], "missing": []} for lvl in LEVELS}
    by_category: Dict[str, Dict[str, List[str]]] = {}
    for name, r in all_results.items():
        lvl = r["level"]
        cat = r["category"]
        key = "installed" if r["installed"] else "missing"
        by_level.setdefault(lvl, {"installed": [], "missing": []})
        by_level[lvl][key].append(name)
        by_category.setdefault(cat, {"installed": [], "missing": []})
        by_category[cat][key].append(name)

    return {
        "total": len(all_results),
        "installed_count": len(installed),
        "missing_count": len(missing),
        "installed": installed,
        "missing": missing,
        "by_level": by_level,
        "by_category": by_category,
    }


def missing_mandatory() -> List[str]:
    """Возвращает список ОБЯЗАТЕЛЬНЫХ, которых нет. Пусто = всё ок."""
    r = check_all(level="mandatory", use_cache=False)
    return [n for n, v in r.items() if not v["installed"]]


def has(program: str) -> bool:
    return bool(check_program(program).get("installed"))


# ============================================================
# CLI
# ============================================================
CAT_TITLES = {
    "core": "Базовое", "build": "Сборка", "media": "Графика/Медиа",
    "game": "Игры / 3D", "audio": "Аудио / Голос",
    "doc": "Документы (PDF/Word/Excel)",
    "gui": "GUI-дизайнер", "automation": "Автоматизация",
    "llm": "Локальные LLM", "tool": "Инструменты",
    "ui": "UI (для AI Link)", "other": "Прочее",
}
CAT_ORDER = ["core", "ui", "build", "doc", "media", "audio",
             "game", "gui", "automation", "llm", "tool", "other"]


def _print_human(level_filter: Optional[str] = None) -> None:
    results = check_all(use_cache=False, level=level_filter)
    s = summary()

    print("=" * 64)
    print(f"  ОКРУЖЕНИЕ: {s['installed_count']}/{s['total']} установлено")
    print("=" * 64)

    by_lvl: Dict[str, List[str]] = {lvl: [] for lvl in LEVELS}
    for name, r in results.items():
        by_lvl.setdefault(r["level"], []).append(name)

    for lvl in LEVELS:
        if level_filter and lvl != level_filter:
            continue
        names = by_lvl.get(lvl, [])
        if not names:
            continue
        inst = sum(1 for n in names if results[n]["installed"])
        print(f"\n{'─' * 64}")
        print(f"  {LEVEL_TITLES[lvl]}  [{inst}/{len(names)}]")
        print(f"{'─' * 64}")
        for cat in CAT_ORDER:
            items = [(n, results[n]) for n in names if results[n]["category"] == cat]
            if not items:
                continue
            print(f"\n  [{CAT_TITLES.get(cat, cat)}]")
            for name, r in items:
                if r["installed"]:
                    ver = r["version"] or "(версия не определена)"
                    print(f"    ✅ {name:<22} {ver}")
                    if r["path"]:
                        print(f"       {r['path']}")
                else:
                    print(f"    ❌ {name:<22} не установлено")
                    if r.get("hint"):
                        print(f"       → {r['hint']}")

    print()
    print("=" * 64)
    print(f"Итог: установлено {s['installed_count']}, отсутствует {s['missing_count']}")
    miss_mand = missing_mandatory()
    if miss_mand:
        print(f"⚠️  НЕ ХВАТАЕТ ОБЯЗАТЕЛЬНЫХ: {', '.join(miss_mand)}")
    else:
        print("✅ Все ОБЯЗАТЕЛЬНЫЕ на месте.")
    print("=" * 64)


def _print_json(level_filter: Optional[str] = None) -> None:
    print(json.dumps({
        "summary": summary(),
        "programs": check_all(use_cache=False, level=level_filter),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    level_arg = None
    if "--level" in sys.argv:
        i = sys.argv.index("--level")
        if i + 1 < len(sys.argv):
            level_arg = sys.argv[i + 1]
    if "--json" in sys.argv:
        _print_json(level_arg)
    else:
        _print_human(level_arg)

# ============================================================
# УСТАНОВКА (A.3.2)
# ============================================================
def install_commands(program_names: List[str]) -> Dict[str, Any]:
    """Разделяет программы на apt / pip / manual.

    Возвращает:
      {"apt": ["ffmpeg", "godot3", ...],
       "pip": ["pypdf", "pyttsx3", ...],
       "manual": [
         {"name": "ollama", "title": "Ollama",
          "url": "...", "hint": "curl ... | sh"},
         ...
       ]}

    Пустые apt_pkg/pip_pkg идут в manual, если у программы есть
    install_url или install_hint (A.3.3, 2026-10-05).
    """
    apt: List[str] = []
    pip: List[str] = []
    npm: List[str] = []
    manual: List[Dict[str, str]] = []
    for name in program_names:
        meta = KNOWN_PROGRAMS.get(name, {})
        kind = meta.get("kind", "binary")
        handled = False

        # INSTALL-VIA (2026-10-08): три ветки — apt / pip / npm.
        # Приоритет: apt_pkg → pip_pkg → npm_pkg. Если ничего —
        # manual (install_url / install_hint).
        if kind == "binary":
            apt_pkg = meta.get("apt_pkg") or ""
            pip_pkg = meta.get("pip_pkg") or ""
            npm_pkg = meta.get("npm_pkg") or ""
            if apt_pkg:
                if apt_pkg not in apt:
                    apt.append(apt_pkg)
                handled = True
            elif pip_pkg:
                if pip_pkg not in pip:
                    pip.append(pip_pkg)
                handled = True
            elif npm_pkg:
                if npm_pkg not in npm:
                    npm.append(npm_pkg)
                handled = True
        elif kind == "python":
            pkg = meta.get("pip_pkg")
            if pkg:
                if pkg not in pip:
                    pip.append(pkg)
                handled = True

        if handled:
            continue
        # Нет apt/pip/npm — но может быть install_url/install_hint
        url = meta.get("install_url", "")
        hint = meta.get("install_hint", "")
        if url or hint:
            manual.append({
                "name": name,
                "title": meta.get("title", name),
                "url": url,
                "hint": hint,
            })
    return {"apt": apt, "pip": pip, "npm": npm, "manual": manual}


def install_commands_string(program_names: List[str]) -> str:
    """Возвращает команду-однострочник для показа пользователю."""
    parts = install_commands(program_names)
    lines = []
    if parts["apt"]:
        lines.append("sudo apt-get install -y " + " ".join(parts["apt"]))
    if parts["pip"]:
        lines.append("pip3 install --user " + " ".join(parts["pip"]))
    if parts.get("npm"):
        lines.append("npm install -g " + " ".join(parts["npm"]))
    for m in parts.get("manual", []):
        if m.get("hint"):
            lines.append(f"# {m['name']}: {m['hint']}")
    return " && \\\n".join(lines) if lines else ""

# ============================================================
# A.4 (2026-10-05): сводка для промпта LLM.
# ============================================================
def environment_summary_for_prompt(max_chars: int = 1500) -> str:
    """Компактная сводка «что установлено» для промпта LLM.

    Пример вывода:
      ИНСТРУМЕНТЫ (установлены на этой машине):
        Языки:      python3 3.10.12, node 20.20.2, g++ 11.4.0
        Сборка:     make 4.3
        БД:         sqlite3 3.37.2
        Медиа:      ffmpeg
        Игры/3D:    godot3 3.2.3
        Документы:  pypdf, pdfplumber, pandas, openpyxl
        UI:         PySide6 6.11.2
        Инструменты: docker, ollama 0.32.13
      НЕ УСТАНОВЛЕНО: blender, cmake, whisper, piper, gimp
      ПРАВИЛО: используй ТОЛЬКО установленное. Если нужен
      отсутствующий инструмент — либо найди замену, либо верни
      NEED_CLARIFICATION[user]: "установи X (нужен для Y)".

    Использует кэш из check_all. Не зависит от браузера,
    не блокирует работу. Возвращает пустую строку при ошибке.
    """
    try:
        results = check_all(use_cache=True)
    except Exception:
        return ""

    # Группировка по категориям (только установленные)
    by_cat: Dict[str, List[str]] = {}
    missing: List[str] = []
    for name, r in results.items():
        if r["installed"]:
            cat = r["category"]
            ver = r["version"] or ""
            entry = name
            if ver:
                entry = f"{name} {ver}"
            by_cat.setdefault(cat, []).append(entry)
        else:
            # В «НЕ УСТАНОВЛЕНО» включаем только обязательные
            # и желательные — maximum не засоряем.
            if r["level"] in ("mandatory", "recommended"):
                missing.append(name)

    # Порядок категорий — как в CLI
    cat_order = ["core", "build", "doc", "media", "audio",
                 "game", "gui", "automation", "llm", "tool", "ui"]
    cat_labels = {
        "core": "Языки", "build": "Сборка", "doc": "Документы",
        "media": "Медиа", "audio": "Аудио", "game": "Игры/3D",
        "gui": "GUI", "automation": "Автоматизация",
        "llm": "Локальные LLM", "tool": "Инструменты",
        "ui": "UI",
    }

    lines = ["ИНСТРУМЕНТЫ (установлены на этой машине):"]
    for cat in cat_order:
        items = by_cat.get(cat, [])
        if not items:
            continue
        label = cat_labels.get(cat, cat)
        lines.append(f"  {label}: {', '.join(items)}")

    if missing:
        lines.append(f"НЕ УСТАНОВЛЕНО: {', '.join(missing)}")

    lines.append(
        "ПРАВИЛО: используй ТОЛЬКО установленное из списка. "
        "Если задача требует отсутствующего инструмента — "
        "либо найди замену из установленных, либо верни "
        "NEED_CLARIFICATION[user]: \"нужно установить X (для Y)\"."
    )

    text = "\n".join(lines)
    if len(text) > max_chars:
        text = text[:max_chars] + "\n... [сводка обрезана]"
    return text
