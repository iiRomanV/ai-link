"""Управление вложенным X-сервером Xephyr (Б77).

Xephyr — виртуальный дисплей :99. Браузеры запускаются внутрь него,
не перехватывают фокус на основном экране. Свернуть/развернуть окно
Xephyr — браузеры исчезают/появляются, работа не прекращается.

API:
  ensure_running() -> str    — запустить (если не запущен), вернуть DISPLAY
  stop()                     — остановить при выходе
  hide() / show()            — свернуть / развернуть окно
  is_alive() -> bool         — проверить живость
  get_display() -> str       — DISPLAY для браузеров или пусто
"""
import logging
import subprocess
import time

logger = logging.getLogger(__name__)

DISPLAY_NUM = ":99"
SCREEN_SIZE = "1280x800"
READY_TIMEOUT = 5.0
WMCTRL_TITLE = "Xephyr"


def is_alive() -> bool:
    """Проверить, что Xephyr :99 работает."""
    try:
        r = subprocess.run(
            ["xdpyinfo", "-display", DISPLAY_NUM],
            capture_output=True, timeout=3,
        )
        return r.returncode == 0
    except Exception:
        return False


def ensure_running() -> str:
    """Запустить Xephyr :99, если не запущен. Вернуть DISPLAY (или '')."""
    if is_alive():
        logger.info("XEPHYR: :99 уже запущен")
        return DISPLAY_NUM
    try:
        subprocess.Popen(
            ["Xephyr", DISPLAY_NUM,
             "-screen", SCREEN_SIZE,
             "-ac", "-br", "-noreset"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except FileNotFoundError:
        logger.error("XEPHYR: команда Xephyr не найдена (apt install xserver-xephyr)")
        return ""
    except Exception as e:
        logger.error("XEPHYR: не запустить: %s", e)
        return ""
    deadline = time.time() + READY_TIMEOUT
    while time.time() < deadline:
        if is_alive():
            logger.info("XEPHYR: :99 запущен")
            return DISPLAY_NUM
        time.sleep(0.2)
    logger.error("XEPHYR: :99 не поднялся за %.1fs", READY_TIMEOUT)
    return ""


def hide() -> None:
    """Свернуть окно Xephyr (браузеры внутри не видны)."""
    try:
        subprocess.run(
            ["wmctrl", "-r", WMCTRL_TITLE, "-b", "add,hidden"],
            capture_output=True, timeout=3,
        )
    except Exception as e:
        logger.debug("XEPHYR hide: %s", e)


def show() -> None:
    """Развернуть окно Xephyr."""
    try:
        subprocess.run(
            ["wmctrl", "-r", WMCTRL_TITLE, "-b", "remove,hidden"],
            capture_output=True, timeout=3,
        )
    except Exception as e:
        logger.debug("XEPHYR show: %s", e)


def stop() -> None:
    """Убить Xephyr."""
    try:
        subprocess.run(
            ["pkill", "-f", f"Xephyr {DISPLAY_NUM}"],
            capture_output=True, timeout=3,
        )
        logger.info("XEPHYR: остановлен")
    except Exception as e:
        logger.debug("XEPHYR stop: %s", e)


def get_display() -> str:
    """DISPLAY для браузеров. Пусто, если Xephyr недоступен."""
    return DISPLAY_NUM if is_alive() else ""
