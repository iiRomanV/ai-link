import sys
from PySide6.QtWidgets import QApplication
from ui.main_window import MainWindow
from log_utils.logger import setup_logging
from db.database import init_db
from core.memory import load_global_memory
from core.ollama_adapter import is_ollama_running
from core.gigachat_adapter import refresh_gigachat_token_if_needed
from core.browser_cleanup import cleanup_stale_browsers
import subprocess
import threading
import time
from PySide6.QtCore import QLockFile

def main():
    cleanup_stale_browsers()
    # Б77 (16.10.2026): Xephyr — виртуальный дисплей для браузеров.
    # Запуск при старте, остановка при выходе (через atexit).
    try:
        from core import xephyr_manager as _xeph
        import atexit as _atexit
        _disp = _xeph.ensure_running()
        if _disp:
            _atexit.register(_xeph.stop)
            print(f"XEPHYR: {_disp} запущен")
    except Exception as _xe:
        print(f"XEPHYR: ошибка (обычный режим): {_xe}")
    # KEEP-AWAKE: прибираем висящие systemd-inhibit от упавших прогонов
    try:
        from core.keep_awake import cleanup_stale_keep_awake
        _k = cleanup_stale_keep_awake()
        if _k:
            print(f"[KEEP-AWAKE] убито висящих держателей: {_k}")
    except Exception:
        pass

    # FIX BUG-KEEP-AWAKE-PERSISTENT (2026-09-28):
    # Держатель живёт ПОКА ОТКРЫТО ПРИЛОЖЕНИЕ (не оркестратор).
    # Закрыл AI Link — ПК может спать.
    # atexit в keep_awake.py остановит при выходе.
    try:
        from core.keep_awake import start_keep_awake
        _proc = start_keep_awake(
            reason="AI Link: приложение открыто")
        if _proc:
            print(f"[KEEP-AWAKE] ПК не будет засыпать "
                  f"(pid={_proc.pid})")
    except Exception as _e:
        print(f"[KEEP-AWAKE] не удалось запустить: {_e}")

    # BROWSER-FOCUS: ОТКЛЮЧЕНО 2026-09-26.
    # Постоянный watchdog дёргает фокус (Chromium перезапрашивает
    # каждые 8 сек) — получается хаос. Правильное решение:
    # вернуть фокус ОДИН РАЗ после старта Chromium. Ждёт реализации.
    init_db()  # создаём БД при первом запуске
    load_global_memory()  # создаём глобальную память, если её нет

    # Автозапуск Ollama, если она не запущена (не блокируем основной поток)
    if not is_ollama_running():
        subprocess.Popen(["ollama", "serve"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    # Проверка, что приложение ещё не запущено
    lock_file = QLockFile("/tmp/ai_link.lock")
    lock_file.setStaleLockTime(0)
    if not lock_file.tryLock(100):
        print("Приложение AI Link уже запущено.")
        sys.exit(0)

    app = QApplication(sys.argv)
    app.setApplicationName("AI Link")
    app.setOrganizationName("AI Link")

    # Логгер — после QApplication (QtLogHandler — это QObject).
    # Подключаем сигнал к вкладке «Логи» ПОСЛЕ создания окна.
    from log_utils.logger import QtLogHandler
    qt_handler = QtLogHandler()
    setup_logging(qt_handler=qt_handler)

    window = MainWindow()
    try:
        qt_handler.message_logged.connect(window.logs_tab.append_log)
    except Exception:
        pass
    window.show()

    # Прогрев браузеров при старте (если включено в настройках)
    try:
        from config.config_manager import get_setting
        if get_setting("warmup_on_start", True):
            from core.browser_warmup import warmup_all_async
            warmup_all_async()
    except Exception:
        pass

    # Фоновый поток для автоматического обновления токена GigaChat
    def gigachat_token_keeper():
        while True:
            time.sleep(300)  # каждые 5 минут
            try:
                refresh_gigachat_token_if_needed()
            except Exception:
                pass

    token_thread = threading.Thread(target=gigachat_token_keeper, daemon=True)
    token_thread.start()
    exit_code = app.exec()
    try:
        from core.agents.browser_manager import close_browser
        close_browser()
    except Exception:
        pass
    lock_file.unlock()
    sys.exit(exit_code)

if __name__ == "__main__":
    main()
