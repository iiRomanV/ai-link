"""KEEP-AWAKE: не давать ПК засыпать во время прогонов.

Использует systemd-inhibit (универсально на Linux).
Запускает фоновый процесс-«держатель» (sleep infinity) с
inhibit-lock на sleep/idle. При остановке — убивает процесс.

Работает в контексте:
    with keep_awake(reason="AI Link run"):
        ...длинный прогон...

Или вручную:
    proc = start_keep_awake()
    ...
    stop_keep_awake(proc)
"""

import atexit
import logging
import shutil
import subprocess
import threading
import time
from contextlib import contextmanager
from typing import Optional, List

logger = logging.getLogger(__name__)

# Единственный активный процесс-держатель
_heartbeat_thread = None
_heartbeat_stop = None
_active_proc: Optional[subprocess.Popen] = None

# Что блокируем
_INHIBIT_WHAT = "sleep:idle"

# Доступен ли systemd-inhibit
def is_available() -> bool:
    """True, если systemd-inhibit есть в системе."""
    return shutil.which("systemd-inhibit") is not None




def _heartbeat_worker(stop_event):
    """KEEP-AWAKE-HEARTBEAT (2026-10-03): каждые 30 сек
    сбрасываем X11 idle timer через xdg-screensaver reset.

    Зачем: XFCE Power Manager имеет свой таймер inactivity
    (40 мин на AC), НЕЗАВИСИМЫЙ от systemd-inhibit. Видеоплееры
    (Firefox, VLC) ингибируют через DBus ScreenSaver — это
    XFCE уважает. Мы делаем проще — периодически сбрасываем
    idle timer, тогда XFCE PM видит «юзер активен».
    """
    _counter = 0
    while not stop_event.is_set():
        try:
            subprocess.Popen(
                ["xdg-screensaver", "reset"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            _counter += 1
            if _counter % 20 == 0:  # раз в 10 минут
                logger.debug(
                    "KEEP-AWAKE-HEARTBEAT: %d сбросов", _counter,
                )
        except Exception as _e:
            logger.debug("KEEP-AWAKE-HEARTBEAT: %s", _e)
        # Ждём 30 сек или до stop
        if stop_event.wait(timeout=30.0):
            break


def _start_heartbeat():
    """Запускает фоновый heartbeat-поток."""
    global _heartbeat_thread, _heartbeat_stop
    if _heartbeat_thread is not None and _heartbeat_thread.is_alive():
        return
    _heartbeat_stop = threading.Event()
    _heartbeat_thread = threading.Thread(
        target=_heartbeat_worker,
        args=(_heartbeat_stop,),
        daemon=True,
        name="KEEP-AWAKE-HEARTBEAT",
    )
    _heartbeat_thread.start()
    logger.info("KEEP-AWAKE-HEARTBEAT: старт (каждые 30 сек)")


def _stop_heartbeat():
    """Останавливает heartbeat."""
    global _heartbeat_thread, _heartbeat_stop
    if _heartbeat_stop is not None:
        _heartbeat_stop.set()
    if _heartbeat_thread is not None:
        try:
            _heartbeat_thread.join(timeout=2.0)
        except Exception:
            pass
    _heartbeat_thread = None
    _heartbeat_stop = None
    logger.info("KEEP-AWAKE-HEARTBEAT: остановлен")


def start_keep_awake(reason: str = "AI Link run") -> Optional[subprocess.Popen]:
    """Запускает systemd-inhibit с фоновым sleep.

    Возвращает процесс или None, если не удалось.
    Повторный вызов останавливает предыдущий.
    """
    global _active_proc

    # Если уже запущен — сначала остановим
    if _active_proc is not None and _active_proc.poll() is None:
        return _active_proc

    if not is_available():
        logger.info("KEEP-AWAKE: systemd-inhibit недоступен — пропуск")
        return None

    try:
        proc = subprocess.Popen(
            [
                "systemd-inhibit",
                f"--what={_INHIBIT_WHAT}",
                f"--who=AI Link",
                f"--why={reason[:200]}",
                "--mode=block",
                "sleep", "infinity",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        _active_proc = proc
        logger.info(
            "KEEP-AWAKE: старт (pid=%d, what=%s)",
            proc.pid, _INHIBIT_WHAT,
        )
        # KEEP-AWAKE-HEARTBEAT: сброс X11 idle (XFCE PM не смотрит
        # на systemd-inhibit, у него свой таймер).
        _start_heartbeat()
        return proc
    except Exception as e:
        logger.warning("KEEP-AWAKE: не удалось запустить: %s", e)
        return None


def stop_keep_awake(proc: Optional[subprocess.Popen] = None) -> None:
    """Останавливает процесс-держатель и heartbeat."""
    global _active_proc

    # KEEP-AWAKE-HEARTBEAT-STOP (2026-10-03): сначала гасим heartbeat,
    # потом systemd-inhibit. Иначе heartbeat продолжит дёргать
    # xdg-screensaver после «остановки» AiLink.
    _stop_heartbeat()

    target = proc or _active_proc
    if target is None:
        return
    try:
        if target.poll() is None:
            target.terminate()
            try:
                target.wait(timeout=3)
            except subprocess.TimeoutExpired:
                target.kill()
        logger.info("KEEP-AWAKE: остановлен (pid=%d)", target.pid)
    except Exception as e:
        logger.debug("KEEP-AWAKE: ошибка остановки: %s", e)
    finally:
        if target is _active_proc:
            _active_proc = None


def is_active() -> bool:
    """True, если держатель сейчас работает."""
    return _active_proc is not None and _active_proc.poll() is None


def list_active_inhibitors() -> List[str]:
    """Возвращает список активных inhibitors (для диагностики)."""
    if not is_available():
        return []
    try:
        r = subprocess.run(
            ["systemd-inhibit", "--list", "--no-pager",
             "--no-legend"],
            capture_output=True, text=True, timeout=5,
        )
        return [l for l in (r.stdout or "").splitlines() if l.strip()]
    except Exception:
        return []


@contextmanager
def keep_awake(reason: str = "AI Link run"):
    """Контекстный менеджер: держит ПК бодрым, пока внутри."""
    proc = start_keep_awake(reason)
    try:
        yield proc
    finally:
        stop_keep_awake(proc)


# На всякий случай: если программа упадёт — прибрать за собой
def cleanup_stale_keep_awake() -> int:
    """Убивает ВИСЯЩИЕ systemd-inhibit от предыдущих прогонов.

    Вызывается при старте AI Link. Если предыдущий прогон упал
    (exception / segfault), держатель мог остаться висеть.
    Прибиваем, чтобы не блокировал сон.

    Возвращает число убитых процессов.
    """
    if not is_available():
        return 0
    try:
        # Ищем процессы с нашим тегом
        r = subprocess.run(
            ["pgrep", "-f", "systemd-inhibit.*--who=AI Link"],
            capture_output=True, text=True, timeout=5,
        )
        pids = [p for p in (r.stdout or "").split() if p.strip()]
        killed = 0
        for pid in pids:
            try:
                # Убиваем сам systemd-inhibit и его sleep
                subprocess.run(
                    ["pkill", "-P", pid],  # дети
                    capture_output=True, timeout=3,
                )
                subprocess.run(
                    ["kill", pid],
                    capture_output=True, timeout=3,
                )
                killed += 1
                logger.info("KEEP-AWAKE: убит висящий pid=%s", pid)
            except Exception:
                pass
        return killed
    except Exception as e:
        logger.debug("KEEP-AWAKE cleanup: %s", e)
        return 0


def _atexit_cleanup():
    stop_keep_awake()


atexit.register(_atexit_cleanup)
