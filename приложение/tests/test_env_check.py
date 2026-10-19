"""Тесты для core.env_check (A.3, 2026-10-04)."""

import json
import subprocess
import sys
from pathlib import Path

from core.env_check import (
    KNOWN_PROGRAMS, check_program, check_all, summary, has, clear_cache,
)


def test_known_programs_not_empty():
    """Каталог известных программ не пуст и содержит базовые."""
    assert len(KNOWN_PROGRAMS) >= 10
    assert "python3" in KNOWN_PROGRAMS
    assert "git" in KNOWN_PROGRAMS


def test_check_program_python3_installed():
    """python3 — точно есть (мы на нём тесты запускаем)."""
    clear_cache()
    r = check_program("python3")
    assert r["installed"] is True
    assert r["path"]  # непустой
    assert r["version"]  # версия определена
    assert r["category"] == "core"


def test_check_program_git_installed():
    """git — точно есть (в проекте используется)."""
    clear_cache()
    r = check_program("git")
    assert r["installed"] is True


def test_check_program_nonexistent():
    """Несуществующая программа → installed=False, без падения."""
    clear_cache()
    r = check_program("nonexistent_xyz_12345")
    assert r["installed"] is False
    assert r["error"]


def test_check_program_returns_dict_with_required_keys():
    """Все ключи на месте."""
    clear_cache()
    r = check_program("python3")
    for k in ("name", "installed", "path", "version",
              "category", "title", "hint", "error", "raw"):
        assert k in r, f"нет ключа {k}"


def test_check_all_returns_all_known():
    """check_all возвращает по ключу на каждую программу из KNOWN_PROGRAMS."""
    clear_cache()
    all_r = check_all()
    assert set(all_r.keys()) == set(KNOWN_PROGRAMS.keys())


def test_summary_counts():
    """summary считает установленные/отсутствующие корректно."""
    clear_cache()
    s = summary()
    assert s["total"] == len(KNOWN_PROGRAMS)
    assert s["installed_count"] + s["missing_count"] == s["total"]
    assert isinstance(s["missing"], list)
    assert isinstance(s["installed"], list)


def test_has_python3():
    clear_cache()
    assert has("python3") is True


def test_has_nonexistent():
    clear_cache()
    assert has("nonexistent_xyz_12345") is False


def test_cache_speeds_up(monkeypatch):
    """Второй вызов check_program идёт из кэша (не дёргает subprocess)."""
    clear_cache()
    calls = {"n": 0}
    real_run = subprocess.run

    def fake_run(*a, **kw):
        calls["n"] += 1
        return real_run(*a, **kw)

    # Первый вызов — кэшируем
    check_program("python3")
    # Подменяем subprocess.run и вызываем снова — из кэша, не должно быть вызова
    before = calls["n"]
    monkeypatch.setattr(subprocess, "run", fake_run)
    check_program("python3")
    after = calls["n"]
    assert after == before, "кэш не сработал — был вызов subprocess"


def test_clear_cache(monkeypatch):
    """clear_cache сбрасывает кэш."""
    clear_cache()
    check_program("python3")
    # После clear следующий вызов пойдёт в subprocess заново
    clear_cache()
    calls = {"n": 0}
    real_run = subprocess.run

    def fake_run(*a, **kw):
        calls["n"] += 1
        return real_run(*a, **kw)

    monkeypatch.setattr(subprocess, "run", fake_run)
    check_program("python3")
    assert calls["n"] >= 1, "clear_cache не сбросил кэш"


def test_cli_json_output():
    """CLI: python3 -m core.env_check --json выдаёт валидный JSON."""
    r = subprocess.run(
        [sys.executable, "-m", "core.env_check", "--json"],
        capture_output=True, text=True, timeout=60,
        cwd=str(Path(__file__).resolve().parent.parent),
    )
    assert r.returncode == 0, f"CLI упал: {r.stderr[:300]}"
    data = json.loads(r.stdout)
    assert "summary" in data
    assert "programs" in data
    assert "python3" in data["programs"]


# === A.3 (расширение): уровни и Python-пакеты ===

def test_levels_defined():
    """Три уровня: mandatory, recommended, optional."""
    from core.env_check import LEVELS
    assert set(LEVELS) == {"mandatory", "recommended", "optional"}


def test_all_programs_have_level():
    """Каждая программа имеет level из разрешённых."""
    from core.env_check import KNOWN_PROGRAMS, LEVELS
    for name, meta in KNOWN_PROGRAMS.items():
        assert meta.get("level") in LEVELS, f"{name}: нет level"


def test_all_programs_have_kind():
    """Каждая программа имеет kind: binary или python."""
    from core.env_check import KNOWN_PROGRAMS
    for name, meta in KNOWN_PROGRAMS.items():
        assert meta.get("kind") in ("binary", "python"), f"{name}: плохой kind"


def test_mandatory_level_present():
    """Обязательные есть и их не меньше 5."""
    from core.env_check import check_all
    m = check_all(level="mandatory", use_cache=False)
    assert len(m) >= 5
    assert "python3" in m
    assert "git" in m


def test_python_package_check():
    """PySide6 — python-пакет, должен быть установлен."""
    from core.env_check import check_program, clear_cache
    clear_cache()
    r = check_program("pyside6")
    assert r["kind"] == "python"
    assert r["installed"] is True


def test_python_package_nonexistent():
    """Несуществующий python-пакет → installed=False."""
    from core.env_check import KNOWN_PROGRAMS, check_program, clear_cache
    KNOWN_PROGRAMS["__test_fake_pkg__"] = {
        "kind": "python", "import_name": "__nonexistent_xyz__",
        "category": "other", "level": "optional",
        "title": "Fake", "hint": "test",
    }
    try:
        clear_cache()
        r = check_program("__test_fake_pkg__")
        assert r["installed"] is False
    finally:
        KNOWN_PROGRAMS.pop("__test_fake_pkg__", None)
        clear_cache()


def test_summary_by_level():
    """summary возвращает by_level."""
    from core.env_check import summary, clear_cache
    clear_cache()
    s = summary()
    assert "by_level" in s
    for lvl in ("mandatory", "recommended", "optional"):
        assert lvl in s["by_level"]
        assert "installed" in s["by_level"][lvl]
        assert "missing" in s["by_level"][lvl]


def test_missing_mandatory():
    """missing_mandatory возвращает список (обычно пустой)."""
    from core.env_check import missing_mandatory, clear_cache
    clear_cache()
    m = missing_mandatory()
    assert isinstance(m, list)
    assert "python3" not in m  # мы на нём тесты запускаем


def test_check_all_filtered_by_level():
    """check_all(level=...) возвращает только нужный уровень."""
    from core.env_check import check_all, clear_cache
    clear_cache()
    m = check_all(level="mandatory", use_cache=False)
    for name, r in m.items():
        assert r["level"] == "mandatory"


def test_cli_level_filter():
    """CLI --level mandatory работает."""
    r = subprocess.run(
        [sys.executable, "-m", "core.env_check", "--level", "mandatory", "--json"],
        capture_output=True, text=True, timeout=60,
        cwd=str(Path(__file__).resolve().parent.parent),
    )
    assert r.returncode == 0, f"CLI упал: {r.stderr[:300]}"
    data = json.loads(r.stdout)
    for name, info in data["programs"].items():
        assert info["level"] == "mandatory"


# === A.4 (2026-10-05): сводка для промпта ===

def test_environment_summary_returns_string():
    """Функция возвращает непустую строку."""
    from core.env_check import environment_summary_for_prompt, clear_cache
    clear_cache()
    txt = environment_summary_for_prompt()
    assert isinstance(txt, str)
    assert len(txt) > 0


def test_environment_summary_mentions_python3():
    """Python3 точно есть — должен упоминаться."""
    from core.env_check import environment_summary_for_prompt, clear_cache
    clear_cache()
    txt = environment_summary_for_prompt()
    assert "python3" in txt


def test_environment_summary_has_rule():
    """В сводке есть ПРАВИЛО про использование только установленного."""
    from core.env_check import environment_summary_for_prompt, clear_cache
    clear_cache()
    txt = environment_summary_for_prompt()
    assert "ПРАВИЛО" in txt


def test_environment_summary_max_chars():
    """Сводка не превышает max_chars (с запасом под обрезку)."""
    from core.env_check import environment_summary_for_prompt, clear_cache
    clear_cache()
    txt = environment_summary_for_prompt(max_chars=200)
    assert len(txt) <= 250  # с запасом под "... [сводка обрезана]"


def test_environment_summary_empty_on_error(monkeypatch):
    """При ошибке возвращает пустую строку (не падает)."""
    from core import env_check as _ec
    monkeypatch.setattr(_ec, "check_all",
                        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("boom")))
    txt = _ec.environment_summary_for_prompt()
    assert txt == ""


# === A.4 (2026-10-05): _parse_version — строгий regex ===

def test_parse_version_python():
    from core.env_check import _parse_version
    assert _parse_version("Python 3.10.12", "python3") == "3.10.12"


def test_parse_version_git():
    from core.env_check import _parse_version
    assert _parse_version("git version 2.34.1", "git") == "2.34.1"


def test_parse_version_node_v_prefix():
    from core.env_check import _parse_version
    assert _parse_version("v20.10.0", "node") == "20.10.0"


def test_parse_version_ffmpeg_ubuntu_suffix():
    from core.env_check import _parse_version
    r = _parse_version("ffmpeg 4.4.2-0ubuntu0.22.04.1 Copyright", "ffmpeg")
    assert r == "4.4.2"


def test_parse_version_sqlite_date():
    """Дата НЕ должна попасть в версию."""
    from core.env_check import _parse_version
    r = _parse_version("3.37.2 2022-01-06 13:25:41 872ba256", "sqlite3")
    assert r == "3.37.2"


def test_parse_version_no_version():
    """Нет версии → пустая строка."""
    from core.env_check import _parse_version
    assert _parse_version("Attempting to connect...", "audacity") == ""
    assert _parse_version("Color management: fallback", "blender") == ""


def test_parse_version_empty():
    from core.env_check import _parse_version
    assert _parse_version("", "x") == ""
    assert _parse_version(None, "x") == ""


# === A.3.3 (2026-10-05): install_url / install_hint / manual ===

def test_install_url_and_hint_for_ollama():
    """Ollama — install_url и install_hint заданы."""
    from core.env_check import KNOWN_PROGRAMS
    m = KNOWN_PROGRAMS["ollama"]
    assert m.get("install_url", "").startswith("http")
    assert "curl" in m.get("install_hint", "")


def test_install_url_for_piper():
    """Piper — есть URL на GitHub."""
    from core.env_check import KNOWN_PROGRAMS
    m = KNOWN_PROGRAMS["piper"]
    assert "github.com" in m.get("install_url", "")


def test_install_url_for_designer():
    """Pyside6-designer — есть URL на Qt."""
    from core.env_check import KNOWN_PROGRAMS
    m = KNOWN_PROGRAMS["pyside6-designer"]
    assert "qt.io" in m.get("install_url", "")


def test_install_commands_returns_manual():
    """Для ollama install_commands вернёт manual, не пустоту."""
    from core.env_check import install_commands
    parts = install_commands(["ollama"])
    assert parts["apt"] == []
    assert parts["pip"] == []
    assert len(parts["manual"]) == 1
    assert parts["manual"][0]["name"] == "ollama"


def test_install_commands_mixed():
    """Смесь: ffmpeg (apt), pypdf (pip), ollama (manual)."""
    from core.env_check import install_commands
    parts = install_commands(["ffmpeg", "pypdf", "ollama", "piper"])
    assert "ffmpeg" in parts["apt"]
    assert "pypdf" in parts["pip"]
    assert len(parts["manual"]) == 2
    names = {m["name"] for m in parts["manual"]}
    assert names == {"ollama", "piper"}


def test_install_commands_all_have_url_or_hint():
    """У каждой manual-программы есть url или hint."""
    from core.env_check import install_commands
    parts = install_commands(["ollama", "piper", "pyside6-designer"])
    for m in parts["manual"]:
        assert m.get("url") or m.get("hint"), \
            f"{m['name']} без url и без hint"


def test_install_commands_string_includes_manual_comment():
    """install_commands_string упоминает manual в виде комментария."""
    from core.env_check import install_commands_string
    txt = install_commands_string(["ffmpeg", "ollama"])
    assert "apt-get install" in txt
    assert "ollama" in txt
    assert "#" in txt  # комментарий для manual


def test_install_commands_no_duplicates():
    """Дубликаты apt/pip-пакетов не попадают дважды."""
    from core.env_check import install_commands
    parts = install_commands(["ffmpeg", "ffmpeg", "pypdf", "pypdf"])
    assert parts["apt"].count("ffmpeg") == 1
    assert parts["pip"].count("pypdf") == 1
