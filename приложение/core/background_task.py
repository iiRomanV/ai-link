"""Фоновая задача для оркестрации.

Запускает orchestrate() в отдельном потоке.
Даёт UI возможность:
  • start()  — запустить задачу
  • pause()  — приостановить (orchestrate замрёт между фазами)
  • resume() — продолжить
  • stop()   — остановить (orchestrate корректно завершится)
  • status() — текущий статус и результат

Технически: threading.Thread + 2 флага (pause, stop) + колбэки.
"""

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional


# Статусы
ST_IDLE = "idle"
ST_RUNNING = "running"
ST_PAUSED = "paused"
ST_STOPPING = "stopping"
ST_DONE = "done"
ST_FAILED = "failed"
ST_STOPPED = "stopped"
ST_NEEDS_CLARIFICATION = "needs_clarification"


@dataclass
class BackgroundTaskState:
    """Снимок состояния задачи для UI."""
    status: str = ST_IDLE
    task: str = ""
    project_dir: str = ""
    phase_results: List[Dict[str, Any]] = field(default_factory=list)
    result: Optional[Dict[str, Any]] = None
    error: str = ""
    started_at: float = 0.0
    finished_at: float = 0.0
    clarification_question: str = ""
    clarification_phase: str = ""


class BackgroundTask:
    """Фоновая задача оркестрации."""

    def __init__(self, task: str, project_dir: str,
                 max_phases: int = 100,
                 timeout: float = 14400.0,   # 4 часа (было 30 мин)
                 cross_verify: bool = True,
                 on_progress: Optional[Callable] = None,
                 on_finish: Optional[Callable] = None,
                 user_answers: Optional[list] = None,
                 resume_from: str = "",
                 continue_project: bool = False,
                 force_new: bool = False,
                 check_checkpoint: bool = False):
        self.task = task
        self.project_dir = project_dir
        self.max_phases = max_phases
        self.timeout = timeout
        self.cross_verify = cross_verify
        self.on_progress = on_progress
        self.on_finish = on_finish
        self.user_answers = user_answers or []
        self.resume_from = resume_from
        self.continue_project = continue_project
        self.force_new = force_new
        self.check_checkpoint = check_checkpoint

        self._state = BackgroundTaskState(
            status=ST_IDLE,
            task=task,
            project_dir=project_dir,
        )
        self._thread: Optional[threading.Thread] = None
        self._pause_flag = threading.Event()
        self._stop_flag = threading.Event()
        self._lock = threading.Lock()

    # === Управление ===

    def start(self) -> bool:
        """Запускает задачу в фоне. Возвращает False если уже запущена."""
        with self._lock:
            if self._state.status in (ST_RUNNING, ST_PAUSED, ST_STOPPING):
                return False
            self._pause_flag.clear()
            self._stop_flag.clear()
            self._state.status = ST_RUNNING
            self._state.started_at = time.time()
            self._state.phase_results = []
            self._state.result = None
            self._state.error = ""

        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return True

    def pause(self) -> None:
        """Ставит на паузу. Orchestrate замрёт между фазами."""
        with self._lock:
            if self._state.status == ST_RUNNING:
                self._pause_flag.set()
                self._state.status = ST_PAUSED

    def resume(self) -> None:
        """Продолжает после паузы."""
        with self._lock:
            if self._state.status == ST_PAUSED:
                self._pause_flag.clear()
                self._state.status = ST_RUNNING

    def stop(self) -> None:
        """Помечает задачу как останавливаемую. Orchestrate завершится корректно."""
        self._stop_flag.set()
        self._pause_flag.clear()  # если была пауза — снимаем, чтобы не зависнуть
        with self._lock:
            if self._state.status in (ST_RUNNING, ST_PAUSED):
                self._state.status = ST_STOPPING

    def is_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def wait(self, timeout: Optional[float] = None) -> None:
        """Ждёт завершения потока (для тестов)."""
        if self._thread is not None:
            self._thread.join(timeout=timeout)

    def status(self) -> BackgroundTaskState:
        """Возвращает снимок состояния."""
        with self._lock:
            return BackgroundTaskState(
                status=self._state.status,
                task=self._state.task,
                project_dir=self._state.project_dir,
                phase_results=list(self._state.phase_results),
                result=self._state.result,
                error=self._state.error,
                started_at=self._state.started_at,
                finished_at=self._state.finished_at,
                clarification_question=getattr(
                    self._state, "clarification_question", ""
                ),
                clarification_phase=getattr(
                    self._state, "clarification_phase", ""
                ),
            )

    # === Внутренние методы ===

    def _should_stop(self) -> bool:
        """Колбэк для orchestrate.

        Если пауза — блокирует до resume или stop.
        Если stop_flag — возвращает True.
        """
        # При паузе — ждём (не более, чем пока не снимут паузу или стоп)
        while self._pause_flag.is_set() and not self._stop_flag.is_set():
            time.sleep(0.3)
        return self._stop_flag.is_set()

    def _on_phase(self, phase: str, phase_result: Any) -> None:
        """Колбэк прогресса: обновляет состояние + зовёт on_progress."""
        with self._lock:
            self._state.phase_results.append({
                "phase": phase,
                "status": getattr(phase_result, "status", "?"),
                "agent": getattr(phase_result, "agent", "?"),
                "seconds": getattr(phase_result, "seconds", 0.0),
                "error": getattr(phase_result, "error", ""),
            })
        if self.on_progress:
            try:
                self.on_progress(phase, phase_result)
            except Exception:
                pass

    def _run(self) -> None:
        """Внутри потока: вызывает orchestrate."""
        try:
            from core.orchestrator import orchestrate
            r = orchestrate(
                task=self.task,
                project_dir=self.project_dir,
                max_phases=self.max_phases,
                timeout=self.timeout,
                cross_verify=self.cross_verify,
                progress_callback=self._on_phase,
                should_stop=self._should_stop,
                user_answers=self.user_answers or None,
                resume_from=self.resume_from or "",
                continue_project=self.continue_project,
                force_new=self.force_new,
                check_checkpoint=self.check_checkpoint,
                # FIX BUG-BRAIN-SWITCH (2026-09-28): включаем
                # решения brain. Brain работает на правилах
                # (use_llm=False) — быстро и без LLM.
                # Точки врезки: plan→?, code→?, fix→?
                brain_switch=True,
            )

            with self._lock:
                self._state.result = {
                    "success": r.success,
                    "status": r.status,
                    "final_phase": r.final_phase,
                    "phases_done": r.phases_done,
                    "error": r.error,
                    "seconds": r.seconds,
                    # B48-SECRETS-REPORT (2026-10-12)
                    "secrets_found": list(
                        getattr(r, "secrets_found", []) or []
                    ),
                    # B47-SMOKE-TEST (2026-10-12)
                    "smoke_test": dict(
                        getattr(r, "smoke_test", {}) or {}
                    ),
                }
                self._state.finished_at = time.time()

                if r.status == "has_checkpoint":
                    self._state.status = "has_checkpoint"
                    self._state.result["checkpoint"] = (
                        getattr(r, "checkpoint", {}) or {}
                    )
                elif r.status == "stopped":
                    self._state.status = ST_STOPPED
                elif r.status == "needs_clarification":
                    self._state.status = ST_NEEDS_CLARIFICATION
                    # Сохраняем вопрос отдельно — UI его покажет
                    self._state.clarification_question = (
                        getattr(r, "clarification_question", "") or ""
                    )
                    self._state.clarification_phase = (
                        getattr(r, "clarification_phase", "") or ""
                    )
                elif r.success:
                    self._state.status = ST_DONE
                else:
                    self._state.status = ST_FAILED
                    self._state.error = r.error or "задача не выполнена"

        except Exception as e:
            with self._lock:
                self._state.status = ST_FAILED
                self._state.error = str(e)[:500]
                self._state.finished_at = time.time()

        if self.on_finish:
            try:
                self.on_finish(self.status())
            except Exception:
                pass
