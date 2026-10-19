#!/usr/bin/env python3
"""CLI-запуск задачи AiLink без GUI.

Использование:
  python3 run_task.py "текст задачи" [путь_к_проекту] [--new]

  --new — запустить как НОВУЮ задачу (не продолжать старую).
          Без флага — continue_project=True (как было).

Пример:
  python3 run_task.py "Добавь docstrings в проект" \
    "/path/to/your/project"
"""
import sys
import os
from pathlib import Path

# Настройка логирования в stdout
import logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stdout,
)


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    task = sys.argv[1]
    if len(sys.argv) >= 3:
        project_dir = sys.argv[2]
    else:
        project_dir = str(Path.cwd())

    project_dir = str(Path(project_dir).resolve())
    if not Path(project_dir).exists():
        print(f"❌ Проект не найден: {project_dir}")
        sys.exit(1)

    # CLI flags (A v8): --new — новая задача (force_new=True),
    # остальные — продолжить (continue_project=True).
    force_new = "--new" in sys.argv
    if force_new:
        sys.argv.remove("--new")

    print(f"=== AiLink CLI ===")
    print(f"Задача:   {task}")
    print(f"Проект:   {project_dir}")
    print(f"Режим:    {'НОВАЯ (--new)' if force_new else 'продолжить'}")
    print()

    # Инициализация
    from db.database import init_db
    from core.memory import load_global_memory
    init_db()
    load_global_memory()

    # Очищаем старые браузеры
    try:
        from core.browser_cleanup import cleanup_stale_browsers
        cleanup_stale_browsers()
    except Exception:
        pass

    # KEEP-AWAKE-CLI (2026-10-02): держим ПК в тонусе во время прогона.
    # В GUI это делает main.py (пока окно открыто). В CLI — забыли.
    # Симптом: ПК засыпал на 40-мин прогоне → fix timeout ×2 (13 мин).
    try:
        from core.keep_awake import (
            start_keep_awake, stop_keep_awake, cleanup_stale_keep_awake,
        )
        _stale = cleanup_stale_keep_awake()
        if _stale:
            print(f"[KEEP-AWAKE] убито висящих: {_stale}")
        _ka_proc = start_keep_awake(reason="AI Link CLI: run_task")
        if _ka_proc:
            print("[KEEP-AWAKE] ПК не уснёт до конца прогона")
        else:
            print("[KEEP-AWAKE] systemd-inhibit недоступен — пропуск")
    except Exception as _e:
        print(f"[KEEP-AWAKE] ошибка запуска: {_e}")
        _ka_proc = None

    # Запускаем оркестратор
    from core.orchestrator import orchestrate

    try:
        result = orchestrate(
            task=task,
            project_dir=project_dir,
            max_phases=100,
            timeout=14400.0,  # 4 часа
            cross_verify=True,
            continue_project=not force_new,
            force_new=force_new,
            brain_switch=True,
        )

        print()
        print("=" * 60)
        print(f"ИТОГ: status={result.status}")
        print(f"      success={result.success}")
        print(f"      phases_done={result.phases_done}")
        print(f"      seconds={result.seconds:.1f}")
        if result.error:
            print(f"      error={result.error}")
        print("=" * 60)

        sys.exit(0 if result.success else 1)
    except KeyboardInterrupt:
        print("\n⏸ Прервано пользователем (Ctrl+C)")
        sys.exit(130)
    except Exception as e:
        print(f"\n❌ Ошибка: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    finally:
        # KEEP-AWAKE-CLI: обязательно останавливаем держатель
        try:
            if _ka_proc:
                stop_keep_awake(_ka_proc)
                print("[KEEP-AWAKE] держатель остановлен")
        except Exception:
            pass


if __name__ == "__main__":
    main()
