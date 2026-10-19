"""SMOKE-TEST-AFTER-RUN (2026-10-12, Б47): проверка работоспособности.

После успешного прогона — запускаем приложение на N сек.
Если падает с Traceback — status=broken, не success.

Логика:
  • timeout N python main.py
  • headless для GUI (DISPLAY="")
  • rc=0        → ok (CLI завершился)
  • rc=124      → ok (timeout, значит приложение работало)
  • Traceback   → fail (приложение сломано)
  • rc≠0 + без Traceback → возможно ok (бизнес-логика)
"""
import logging
import os
import subprocess
from pathlib import Path
from typing import Any, Dict

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 15


def _find_entry(project_dir: str):
    """Ищет точку входа через launcher_maker."""
    try:
        from core.launcher_maker import _find_entry_deep
        return _find_entry_deep(project_dir)
    except Exception as e:
        logger.warning("smoke: launcher не доступен: %s", e)
        return None


def _env_for_run(kind: str) -> Dict[str, str]:
    """Окружение для запуска.

    Б78 (16.10.2026): для GUI НЕ обнуляем DISPLAY — Tkinter при
    DISPLAY="" падает с TclError (не 'Traceback при запуске',
    а реальный сбой проверки). Используем Xephyr :99, если он
    запущен, иначе — текущий DISPLAY.
    """
    env = os.environ.copy()
    if kind == "gui":
        # Xephyr :99 в приоритете (свёрнут, не мешает пользователю).
        _disp = env.get("DISPLAY", "") or ""
        try:
            from core import xephyr_manager as _xeph
            if _xeph.is_alive():
                _disp = _xeph.DISPLAY_NUM
        except Exception:
            pass
        if not _disp:
            _disp = ":0"
        env["DISPLAY"] = _disp
        # Для Qt — offscreen, для Tkinter — игнорируется.
        env["QT_QPA_PLATFORM"] = "offscreen"
    return env


def run_smoke_test(project_dir: str,
                   timeout: int = _DEFAULT_TIMEOUT) -> Dict[str, Any]:
    """Запускает приложение и анализирует результат."""
    out: Dict[str, Any] = {
        "ok": False, "status": "skipped", "rc": -1,
        "error": "", "output_tail": "", "cmd": "",
    }
    base = Path(project_dir)
    if not base.exists():
        out["error"] = f"project_dir не существует: {project_dir}"
        return out

    entry = _find_entry(project_dir)
    if not entry:
        out["status"] = "skipped"
        out["error"] = "точка входа не найдена"
        return out

    cmd_list, wdir, kind = entry
    # cmd_list = ["python3", "main.py"] — нужно подставить venv
    try:
        from core.launcher_maker import _venv_python
        py = _venv_python(wdir)
        if cmd_list and cmd_list[0] in ("python3", "python"):
            cmd_list = [py] + cmd_list[1:]
    except Exception:
        pass

    out["cmd"] = " ".join(cmd_list)
    env = _env_for_run(kind)

    logger.info(
        "smoke: запуск %s в %s (kind=%s, timeout=%d)",
        out["cmd"], wdir, kind, timeout,
    )
    try:
        r = subprocess.run(
            cmd_list,
            capture_output=True, text=True,
            timeout=timeout, cwd=wdir, env=env,
        )
    except subprocess.TimeoutExpired as te:
        # timeout — приложение работало, но не завершилось
        _so = te.stdout or b""
        _se = te.stderr or b""
        if isinstance(_so, bytes):
            _so = _so.decode(errors="ignore")
        if isinstance(_se, bytes):
            _se = _se.decode(errors="ignore")
        combined = (_so + "\n" + _se).strip()
        has_tb = "Traceback" in combined
        out["rc"] = 124
        out["output_tail"] = combined[-500:]
        if has_tb:
            out["ok"] = False
            out["status"] = "broken"
            out["error"] = "Traceback при запуске (timeout)"
        else:
            out["ok"] = True
            out["status"] = "ok_timeout"
        return out
    except Exception as e:
        out["error"] = f"ошибка запуска: {e}"
        out["status"] = "unknown"
        return out

    output = (r.stdout or "") + "\n" + (r.stderr or "")
    out["rc"] = r.returncode
    out["output_tail"] = output.strip()[-500:]

    has_tb = "Traceback" in output
    if has_tb:
        out["ok"] = False
        out["status"] = "broken"
        out["error"] = "Traceback при запуске"
        return out

    if r.returncode == 0:
        out["ok"] = True
        out["status"] = "ok"
    else:
        # rc≠0 без Traceback — возможно бизнес-логика
        out["ok"] = True
        out["status"] = "ok_nonzero"
        out["error"] = f"rc={r.returncode} без Traceback"
    return out
