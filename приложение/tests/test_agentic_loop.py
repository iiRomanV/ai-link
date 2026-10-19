import sys
import tempfile
from unittest.mock import patch
from core import agentic_loop

def test_loop_simple_success():
    responses = [
        '{"type": "create_file", "path": "a.txt", "content": "Привет"}',
        '{"type": "task_complete", "message": "готово"}',
    ]
    idx = {"n": 0}

    def fake_call(mode, prompt):
        i = idx["n"]
        idx["n"] += 1
        if i < len(responses):
            return {"success": True, "text": responses[i], "error": None}
        return {"success": False, "text": "", "error": "нет"}

    with tempfile.TemporaryDirectory() as d:
        with patch("core.agentic_loop._call_agent", side_effect=fake_call):
            r = agentic_loop.run_agentic_loop("создай a.txt", d, "gigachat")
    assert r["success"] is True
    assert "a.txt" in r["files_created"]

def test_loop_invalid_json_retries():
    responses = [
        "не json",
        '{"type": "create_file", "path": "a.txt", "content": "x"}',
        '{"type": "task_complete", "message": "ok"}',
    ]
    idx = {"n": 0}

    def fake_call(mode, prompt):
        i = idx["n"]
        idx["n"] += 1
        if i < len(responses):
            return {"success": True, "text": responses[i], "error": None}
        return {"success": False, "text": "", "error": "нет"}

    with tempfile.TemporaryDirectory() as d:
        with patch("core.agentic_loop._call_agent", side_effect=fake_call):
            r = agentic_loop.run_agentic_loop("тест", d, "gigachat")
    assert r["success"] is True

def test_loop_antiloop():
    """Одно и то же действие 3 раза подряд -> авто-завершение."""
    response = '{"type": "create_file", "path": "a.txt", "content": "x"}'

    def fake_call(mode, prompt):
        return {"success": True, "text": response, "error": None}

    with tempfile.TemporaryDirectory() as d:
        with patch("core.agentic_loop._call_agent", side_effect=fake_call):
            r = agentic_loop.run_agentic_loop("тест", d, "gigachat")
    assert r["success"] is True
    assert "автоматически" in (r.get("message") or "")


def test_build_prompt_iteration_0_full(tmp_path):
    """На итерации 0 промпт содержит SYSTEM_PROMPT."""
    from core.agentic_loop import _build_prompt
    from core.protocol import SYSTEM_PROMPT
    pd = str(tmp_path)
    prompt = _build_prompt("test", pd, [], iteration=0)
    # первые 200 символов SYSTEM_PROMPT должны присутствовать
    head = SYSTEM_PROMPT[:200]
    assert head in prompt


def test_build_prompt_iteration_3_short(tmp_path):
    """На итерации 3 промпт НЕ содержит SYSTEM_PROMPT (короткий)."""
    from core.agentic_loop import _build_prompt
    from core.protocol import SYSTEM_PROMPT
    pd = str(tmp_path)
    prompt = _build_prompt("test", pd, [], iteration=3)
    head = SYSTEM_PROMPT[:200]
    assert head not in prompt
    assert "напоминание" in prompt


def test_build_prompt_iteration_6_refresh(tmp_path):
    """На итерации 6 (кратной REFRESH_EVERY) — снова полный SYSTEM_PROMPT."""
    from core.agentic_loop import _build_prompt, REFRESH_EVERY
    from core.protocol import SYSTEM_PROMPT
    pd = str(tmp_path)
    prompt = _build_prompt("test", pd, [], iteration=REFRESH_EVERY)
    head = SYSTEM_PROMPT[:200]
    assert head in prompt


def test_build_prompt_force_refresh(tmp_path):
    """force_refresh=True принудительно даёт полный промпт."""
    from core.agentic_loop import _build_prompt
    from core.protocol import SYSTEM_PROMPT
    pd = str(tmp_path)
    prompt = _build_prompt("test", pd, [], iteration=3, force_refresh=True)
    head = SYSTEM_PROMPT[:200]
    assert head in prompt


# ═══════════════════════════════════════════════════════════════
# Б70-v2 (15.10.2026): auto_complete_loop_protection тоже
# должен ставить task_complete=True.
# ═══════════════════════════════════════════════════════════════


def test_b70_v2_auto_complete_has_NO_task_complete():
    """Б73-v2 (15.10): auto_complete НЕ должен ставить task_complete.

    Откат Б70-v2: auto_complete может сработать преждевременно
    (см. Б73). Поэтому task_complete здесь быть НЕ должно.
    """
    from pathlib import Path
    src = Path("core/agentic_loop.py").read_text(encoding="utf-8")
    idx = src.find('status="auto_complete_loop_protection"')
    assert idx > 0
    # В радиусе 1000 символов после — task_complete НЕТ
    window = src[idx:idx + 1000]
    assert '"task_complete": True' not in window


def test_b73_detector_key_includes_command():
    """Б73 (15.10): ключ детектора включает command/find/replace."""
    from pathlib import Path
    src = Path("core/agentic_loop.py").read_text(encoding="utf-8")
    # Ищем блок формирования ключа
    idx = src.find("if len(history) >= 3:")
    assert idx > 0
    window = src[idx:idx + 1200]
    # Должны быть все поля
    assert 'a.get("command"' in window
    assert 'a.get("find"' in window
    assert 'a.get("replace"' in window


def test_b70_v2_validation_task_complete_unchanged():
    """Основная ветка task_complete не сломана."""
    from pathlib import Path
    src = Path("core/agentic_loop.py").read_text(encoding="utf-8")
    idx = src.find('if validation["kind"] == "task_complete":')
    assert idx > 0
    window = src[idx:idx + 400]
    assert '"task_complete": True' in window


# ═══════════════════════════════════════════════════════════════
# Б73-v2 (15.10.2026): _LoopDetector не должен путать разные
# run_command (grep, sed, ls) — сигнатура должна включать command.
# ═══════════════════════════════════════════════════════════════


def test_b73_loopdetector_different_commands_not_loop():
    """Три разных run_command — НЕ зацикливание."""
    from core.agentic_loop import _LoopDetector
    det = _LoopDetector(window=3)
    # Три РАЗНЫХ команды
    assert not det.add({"type": "run_command", "command": "grep key ."})
    assert not det.add({"type": "run_command", "command": "sed -n '1,10p' file"})
    assert not det.add({"type": "run_command", "command": "ls -la"})
    # После 3 разных — не сработало
    assert not det.add({"type": "run_command", "command": "grep token ."})


def test_b73_loopdetector_same_command_is_loop():
    """Одна и та же команда 5 раз — зацикливание."""
    from core.agentic_loop import _LoopDetector
    det = _LoopDetector(window=3)
    cmd = "grep -rn secret ."
    assert not det.add({"type": "run_command", "command": cmd})
    assert not det.add({"type": "run_command", "command": cmd})
    assert det.add({"type": "run_command", "command": cmd})


def test_b73_loopdetector_same_readfile_is_loop():
    """Один и тот же read_file 5 раз — зацикливание."""
    from core.agentic_loop import _LoopDetector
    det = _LoopDetector(window=3)
    a = {"type": "read_file", "path": "core/orchestrator.py"}
    assert not det.add(a)
    assert not det.add(a)
    assert det.add(a)


def test_b73_loopdetector_source_has_command_in_sig():
    """В исходнике _LoopDetector сигнатура включает command."""
    from pathlib import Path
    src = Path("core/agentic_loop.py").read_text(encoding="utf-8")
    idx = src.find("def add(self, action")
    assert idx > 0
    window = src[idx:idx + 800]
    assert 'action.get("command"' in window
    assert 'action.get("find"' in window
    assert 'action.get("replace"' in window
