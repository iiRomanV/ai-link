"""Ночной режим AI Link.

Запускает список задач последовательно через оркестратор,
соблюдает лимиты, пишет чекпоинты и утренний отчёт.
"""

import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Dict, Any, List, Optional, Callable
from datetime import datetime


# === Конфигурация ===

@dataclass
class NightConfig:
    """Лимиты ночного режима."""
    max_hours: float = 8.0              # максимум часов работы
    max_tasks: int = 15                 # максимум задач за ночь
    max_consecutive_failures: int = 2   # провалов подряд → стоп
    checkpoint_every_minutes: int = 30  # периодичность чекпоинтов
    task_timeout: float = 600.0         # 10 минут на одну задачу
    phase_timeout: float = 1800.0       # 30 минут на оркестрацию


@dataclass
class NightResult:
    """Итог ночного режима."""
    success: bool = False
    status: str = "unknown"     # completed | stopped_consecutive_failures
                                # | timeout | max_tasks | exception
    tasks_done: int = 0
    tasks_failed: int = 0
    consecutive_failures: int = 0
    elapsed: float = 0.0
    started_at: str = ""
    finished_at: str = ""
    task_results: List[Dict[str, Any]] = field(default_factory=list)
    report_path: str = ""
    error: str = ""


# === Утренний отчёт ===

def _build_report(result: NightResult, config: NightConfig) -> str:
    """Собирает markdown-отчёт о ночной работе."""
    lines = [
        "# AI Link — Ночной отчёт",
        "",
        f"Начато:  {result.started_at}",
        f"Закончено: {result.finished_at}",
        f"Длительность: {result.elapsed / 60:.1f} мин",
        f"Статус: {result.status}",
        "",
        "## Итоги",
        "",
        f"- Задач выполнено: {result.tasks_done}",
        f"- Задач провалено: {result.tasks_failed}",
        f"- Максимум задач: {config.max_tasks}",
        f"- Лимит времени: {config.max_hours} ч",
        "",
    ]

    if result.error:
        lines.append(f"## Ошибка остановки")
        lines.append("")
        lines.append(result.error)
        lines.append("")

    lines.append("## Задачи")
    lines.append("")
    for i, tr in enumerate(result.task_results, 1):
        status_icon = "✅" if tr.get("success") else "❌"
        task_short = (tr.get("task", "")[:80] + "...") if len(tr.get("task", "")) > 80 else tr.get("task", "")
        lines.append(f"{i}. {status_icon} {task_short}")
        lines.append(f"   - Статус: {tr.get('status', '?')}")
        lines.append(f"   - Время: {tr.get('seconds', 0):.1f} с")
        if tr.get("error"):
            lines.append(f"   - Ошибка: {tr.get('error', '')[:200]}")
        lines.append("")

    lines.append("## Что требует решения пользователя")
    lines.append("")
    failed = [tr for tr in result.task_results if not tr.get("success")]
    if not failed:
        lines.append("- Все задачи выполнены. Можно продолжать.")
    else:
        for tr in failed:
            lines.append(f"- ⚠ Задача «{tr.get('task', '')[:60]}»: {tr.get('error', '')[:100]}")
    lines.append("")

    return "\n".join(lines)


def _save_report(project_dir: str, result: NightResult,
                 config: NightConfig) -> str:
    """Сохраняет утренний отчёт, возвращает путь."""
    report_dir = Path(project_dir) / ".ai_link"
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / "night_report.md"
    try:
        report_path.write_text(
            _build_report(result, config), encoding="utf-8"
        )
    except Exception:
        pass
    return str(report_path)


def _save_checkpoint(project_dir: str, result: NightResult) -> None:
    """Сохраняет промежуточное состояние (на случай падения)."""
    ckpt_path = Path(project_dir) / ".ai_link" / "night_checkpoint.json"
    ckpt_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        import json
        ckpt_path.write_text(
            json.dumps(asdict(result), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception:
        pass


# === Основная функция ===

def run_night(tasks: List[str], project_dir: str,
              config: Optional[NightConfig] = None,
              progress_callback: Optional[Callable] = None) -> NightResult:
    """Запускает ночной режим.

    tasks — список задач (строк).
    config — лимиты (по умолчанию NightConfig()).
    progress_callback(task_num, task, result) — для UI.
    """
    if config is None:
        config = NightConfig()

    result = NightResult(
        started_at=datetime.now().isoformat(timespec="seconds"),
    )
    start = time.time()
    last_checkpoint = start

    max_seconds = config.max_hours * 3600

    for i, task in enumerate(tasks, 1):
        if i > config.max_tasks:
            result.status = "max_tasks"
            result.error = f"достигнут лимит задач ({config.max_tasks})"
            break

        if time.time() - start > max_seconds:
            result.status = "timeout"
            result.error = f"превышен лимит времени ({config.max_hours} ч)"
            break

        # Запускаем одну задачу через оркестратор
        task_start = time.time()
        task_result: Dict[str, Any] = {
            "task": task,
            "success": False,
            "status": "unknown",
            "error": "",
            "seconds": 0.0,
        }
        try:
            from core.orchestrator import orchestrate
            r = orchestrate(
                task=task,
                project_dir=project_dir,
                max_phases=10,
                timeout=config.task_timeout,
            )
            task_result["success"] = bool(r.success)
            task_result["status"] = r.status
            task_result["error"] = r.error
        except Exception as e:
            task_result["status"] = "exception"
            task_result["error"] = str(e)[:300]

        task_result["seconds"] = round(time.time() - task_start, 2)
        result.task_results.append(task_result)

        if task_result["success"]:
            result.tasks_done += 1
            result.consecutive_failures = 0
        else:
            result.tasks_failed += 1
            result.consecutive_failures += 1

        if progress_callback:
            try:
                progress_callback(i, task, task_result)
            except Exception:
                pass

        # Проверка провалов подряд
        if result.consecutive_failures >= config.max_consecutive_failures:
            result.status = "stopped_consecutive_failures"
            result.error = (
                f"{result.consecutive_failures} провалов подряд — "
                f"останавливаюсь"
            )
            break

        # Чекпоинт по времени
        if time.time() - last_checkpoint > config.checkpoint_every_minutes * 60:
            _save_checkpoint(project_dir, result)
            last_checkpoint = time.time()

    # Финал
    if result.status == "unknown":
        if result.tasks_done + result.tasks_failed >= len(tasks):
            result.status = "completed"
        else:
            result.status = "completed_partial"

    result.success = (result.tasks_failed == 0 and result.tasks_done > 0)
    result.elapsed = round(time.time() - start, 2)
    result.finished_at = datetime.now().isoformat(timespec="seconds")
    result.report_path = _save_report(project_dir, result, config)
    _save_checkpoint(project_dir, result)

    return result
