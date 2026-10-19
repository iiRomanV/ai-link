"""Agentic loop для AI Link.
Модель предлагает одно действие — Link проверяет — HandLink выполняет —
результат возвращается модели. Цикл до task_complete или лимитов.
"""

import time
from typing import Dict, Any, List

from core.hand_link import HandLink
from core.response_validator import validate_response
from core.protocol import SYSTEM_PROMPT

# SMART-EXIT: жёсткий предохранитель вместо «рабочего» лимита.
# Рабочий выход — по no_progress (idle) или loop_detected.
MAX_ITERATIONS = 50          # было 15
REFRESH_EVERY = 6
MAX_TIME_SEC = 1800          # было 600 (30 мин вместо 10)
MAX_HISTORY_ITEMS = 8
MAX_SAME_ERRORS = 3
IDLE_THRESHOLD = 5           # 5 итераций без tool-calls → стоп
LOOP_WINDOW = 5              # окно детектора зацикливания


class _LoopDetector:
    """Детектор зацикливания: 5 одинаковых действий подряд → стоп."""
    def __init__(self, window: int = LOOP_WINDOW):
        self.window = window
        self.history: List[tuple] = []

    def add(self, action: Dict[str, Any]) -> bool:
        """Возвращает True, если обнаружено зацикливание."""
        # Б73 (15.10.2026): ключ должен включать ВСЕ значимые поля action.
        # Раньше для run_command сигнатура была ("run_command", "", "") —
        # одинаковая для РАЗНЫХ команд (grep, sed, ls). Детектор ложно
        # считал разведку зацикливанием и прерывал работу.
        sig = (
            str(action.get("type", "")),
            str(action.get("path", ""))[:200],
            str(action.get("command", "") or "")[:200],
            str(action.get("find", "") or "")[:100],
            str(action.get("replace", "") or "")[:100],
            str(action.get("content", "") or "")[:100],
        )
        self.history.append(sig)
        if len(self.history) > self.window:
            self.history.pop(0)
        if len(self.history) >= self.window:
            return len(set(self.history[-self.window:])) == 1
        return False


class _SmartTermination:
    """Умный выход из code-цикла.

    Условия остановки:
      • max_iterations (жёсткий предохранитель, 50)
      • no_progress (IDLE_THRESHOLD итераций без tool-calls)
    Успех (task_complete) обрабатывается отдельно — там свой выход.
    Зацикливание — через _LoopDetector.
    """
    def __init__(self, max_iterations: int = MAX_ITERATIONS,
                 idle_threshold: int = IDLE_THRESHOLD):
        self.max_iterations = max_iterations
        self.idle_threshold = idle_threshold
        self.iteration = 0
        self.idle_count = 0
        self.actions_count = 0
        self.files_created = 0

    def step(self) -> int:
        self.iteration += 1
        return self.iteration

    def record_action(self) -> None:
        """Было полезное действие (create/replace/run)."""
        self.idle_count = 0
        self.actions_count += 1

    def record_idle(self) -> None:
        """Итерация без полезного действия."""
        self.idle_count += 1

    def record_file(self) -> None:
        self.files_created += 1

    def should_stop(self) -> tuple:
        """Возвращает (stop: bool, reason: str)."""
        if self.iteration >= self.max_iterations:
            return True, "max_iterations"
        if self.idle_count >= self.idle_threshold:
            return True, "no_progress"
        return False, "continue"


def _call_agent(agent_mode: str, prompt: str) -> Dict[str, Any]:
    """Универсальный вызов адаптера по agent_mode."""
    import time as _t
    mode = (agent_mode or "").lower()

    # EVENT-LOG: model_call
    _ev = None
    try:
        from core.event_log import log_event
        _ev = log_event
        _ev(event_type="model_call", payload={
            "agent": mode,
            "prompt_len": len(prompt or ""),
        })
    except Exception:
        pass

    _start = _t.time()
    _result: Dict[str, Any] = {"success": False, "text": "", "error": ""}

    try:
        if mode == "gigachat":
            from core.gigachat_adapter import query_gigachat
            r = query_gigachat(prompt)
            _result = {"success": r.get("success"), "text": r.get("text", ""), "error": r.get("error")}
        elif mode == "ollama":
            from core.ollama_adapter import query_ollama
            r = query_ollama(prompt)
            _result = {"success": r.get("success"), "text": r.get("text", ""), "error": r.get("error")}
        elif mode in ("browser_qwen", "browser_perplexity", "browser_deepseek"):
            from core.agents.browser_agent import query_browser_ai
            site_map = {
                "browser_qwen": "qwen",
                "browser_perplexity": "perplexity",
                "browser_deepseek": "deepseek",
            }
            site = site_map.get(mode, "qwen")
            r = query_browser_ai(site, prompt)
            _result = {"success": r.get("success"), "text": r.get("text", ""), "error": r.get("error")}
        else:
            _result = {"success": False, "text": "", "error": f"Неизвестный агент: {agent_mode}"}
    finally:
        # EVENT-LOG: model_response
        if _ev is not None:
            try:
                _ev(event_type="model_response", payload={
                    "agent": mode,
                    "success": bool(_result.get("success")),
                    "elapsed": round(_t.time() - _start, 2),
                    "text_len": len(_result.get("text") or ""),
                    "error": (_result.get("error") or "")[:200],
                })
            except Exception:
                pass

    return _result


TOOLS_REMINDER = """(напоминание: ответ — один JSON в блоке ```json, или task_complete)

ДОСТУПНЫЕ ИНСТРУМЕНТЫ (выбирай подходящий):
  • create_file(path, content)       — создать/перезаписать файл
  • replace_in_file(path, find, replace) — точечная замена в файле
  • read_file(path)                  — прочитать файл
  • list_directory(path)             — показать содержимое папки
  • run_command(command, timeout?)   — shell (python3, pytest, ls, grep)
  • show_info(message)               — ответ пользователю без файлов
  • task_complete(message)           — завершить задачу

ВАЖНО: для записи файлов используй create_file или replace_in_file,
НЕ пиши файлы через run_command (echo > file, cat <<EOF)."""


def _build_prompt(task: str, project_dir: str, history: List[Dict[str, Any]],
                  iteration: int = 0, force_refresh: bool = False) -> str:
    is_refresh = (iteration == 0) or (iteration % REFRESH_EVERY == 0) or force_refresh

    lines: List[str] = []

    if is_refresh:
        lines.append(SYSTEM_PROMPT)
        lines.append("")
        try:
            from core.project_state import build_context
            ctx = build_context(project_dir, budget_chars=2500)
            if ctx:
                lines.append(ctx)
                lines.append("")
        except Exception:
            pass
    else:
        lines.append(TOOLS_REMINDER)
        lines.append("")

    lines.append(f"РАБОЧАЯ ПАПКА: {project_dir}")
    lines.append("")

    # A.4 (2026-10-05): окружение — что установлено на машине.
    # Показываем только на первом refresh, чтобы не дублировать
    # в каждом шаге цикла (экономия контекста).
    if is_refresh:
        try:
            from core.env_check import environment_summary_for_prompt
            _env_text = environment_summary_for_prompt()
            if _env_text:
                lines.append(_env_text)
                lines.append("")
        except Exception:
            pass

    lines.append("ЗАДАЧА:")
    lines.append(task)
    lines.append("")

    if history:
        for item in history[-MAX_HISTORY_ITEMS:]:
            if "action" in item:
                a = item["action"]
                r = item["result"]
                tool_name = a.get("type", "unknown")
                success = r.get("success", False)
                status = "SUCCESS" if success else "ERROR"

                lines.append(f"[TOOL RESULT: {tool_name} | {status}]")

                if success:
                    files = r.get("created_files", [])
                    if files:
                        lines.append(f"Созданные файлы: {files}")
                    verified = r.get("verified_files", [])
                    if verified:
                        for v in verified[:3]:
                            lines.append(f"  {v.get('path')} exists={v.get('exists')} size={v.get('size')}")
                    # FIX SAFE-REPLACE (2026-09-28, шаг 5):
                    # предупреждение об удалении def/class, которые
                    # вызываются в других файлах. Модель видит и
                    # может сама решить: обновить вызовы / вернуть.
                    _sw = r.get("safe_replace_warnings") or []
                    if _sw:
                        lines.append("")
                        lines.append("⚠️ SAFE-REPLACE WARNING:")
                        for _w in _sw[:5]:
                            _f = _w.get("file", "?")
                            lines.append(f"  Файл: {_f}")
                            for _b in _w.get("broken_refs", [])[:5]:
                                _sym = _b.get("symbol", "?")
                                _us = _b.get("usages", [])
                                lines.append(
                                    f"    УДАЛЕНА def/class: {_sym}"
                                )
                                for _u in _us[:5]:
                                    lines.append(
                                        f"      вызвана в {_u.get('file')}"
                                        f":{_u.get('line')}"
                                        f" ({_u.get('kind')})"
                                    )
                        lines.append("")
                        lines.append(
                            "  РЕКОМЕНДАЦИЯ: обнови вызовы в этих "
                            "файлах ИЛИ верни функцию. Если удаление "
                            "намеренно — оставь как есть, но проверь."
                        )
                        lines.append("")
                    reads = r.get("read_results", {})
                    if reads:
                        # W2: увеличенный лимит + умная обрезка.
                        # Если файл больше лимита — показываем
                        # начало (60%) и конец (40%) — самое важное.
                        _PER_FILE = 20000
                        _TOTAL_LIMIT = 60000
                        _total_read = 0
                        for path, content in reads.items():
                            if _total_read >= _TOTAL_LIMIT:
                                lines.append(
                                    f"Прочитан файл {path}: "
                                    f"[не показан — превышен общий лимит "
                                    f"прочитанных файлов, это ограничение "
                                    f"промпта, а не проблема]"
                                )
                                continue
                            _n = len(content)
                            if _n <= _PER_FILE:
                                lines.append(f"Прочитан файл {path}:")
                                lines.append(content)
                                _total_read += _n
                            else:
                                # Умная обрезка: начало + конец
                                _head_n = int(_PER_FILE * 0.6)
                                _tail_n = _PER_FILE - _head_n
                                _head = content[:_head_n]
                                _tail = content[-_tail_n:]
                                _lines_total = content.count("\n") + 1
                                _lines_head = _head.count("\n") + 1
                                _lines_tail = _tail.count("\n") + 1
                                lines.append(
                                    f"Прочитан файл {path} "
                                    f"({_lines_total} строк):"
                                )
                                lines.append(_head)
                                lines.append(
                                    f"\n... [СЕРЕДИНА ОБРЕЗАНА: показано "
                                    f"{_lines_head + _lines_tail} из "
                                    f"{_lines_total} строк. Показаны "
                                    f"начало и конец. Если нужна конкретная "
                                    f"функция из середины — открой файл "
                                    f"и прочитай её отдельно, или работай "
                                    f"с тем, что видно. ЭТО ОГРАНИЧЕНИЕ "
                                    f"ПРОМПТА, а не ошибка.]\n"
                                )
                                lines.append(_tail)
                                _total_read += _PER_FILE
                    lists = r.get("list_results", {})
                    if lists:
                        for path, items in lists.items():
                            lines.append(f"Содержимое {path}: {items}")
                    # FIX 2026-09-25: показываем результаты run_command
                    cmds = r.get("command_results", {})
                    if cmds:
                        for cmd, info in cmds.items():
                            rc = info.get("rc", 0)
                            out = info.get("stdout", "")
                            err = info.get("stderr", "")
                            if rc == 0:
                                lines.append(
                                    f"Команда `{cmd[:80]}` → rc=0")
                                if out.strip():
                                    lines.append("Вывод:")
                                    lines.append(out[:1000])
                            else:
                                lines.append(
                                    f"Команда `{cmd[:80]}` → rc={rc} "
                                    f"(ненулевой код, но не ошибка "
                                    f"инструмента)")
                                if err.strip():
                                    lines.append(
                                        f"stderr: {err[:500]}")
                                elif out.strip():
                                    lines.append(
                                        f"stdout: {out[:500]}")
                    lines.append("Операция выполнена успешно.")
                else:
                    err = r.get("error", "неизвестная ошибка")
                    lines.append(f"Ошибка: {err}")

                lines.append("[END TOOL RESULT]")
                lines.append("")
                lines.append("Continue with the next step, or provide your final response if the task is complete.")
                lines.append("")
            elif "error" in item:
                lines.append(f"[TOOL RESULT: validation | ERROR]")
                lines.append(f"Ошибка: {item['error']}")
                lines.append("[END TOOL RESULT]")
                lines.append("")
                lines.append("Continue with the next step, or provide your final response if the task is complete.")
                lines.append("")

    lines.append("ТВОЙ СЛЕДУЮЩИЙ ШАГ (один tool_call):")
    return "\n".join(lines)


def _save_diag(iteration, status, text=None, error=None, tb=None, elapsed=None,
               extra=None, prompt=None, agent=None, phase=None):
    """Сохраняет диагностику итерации в .diagnostics/agentic/ при любом исходе.

    AI-RESPONSE-LOG (2026-10-02): расширено — теперь сохраняем также
    prompt (если передан), agent и phase. Это даёт ПОЛНУЮ пару
    «промпт + ответ + метаданные» для разбора сбоев.
    """
    try:
        import os as _os
        import json as _json
        import datetime as _dt
        diag_dir = str(__import__("pathlib").Path.home() / ".ai_link" / "diagnostics" / "agentic")
        _os.makedirs(diag_dir, exist_ok=True)
        ts = _dt.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        meta_path = _os.path.join(diag_dir, f"{ts}_meta.json")
        raw_path = _os.path.join(diag_dir, f"{ts}_raw.txt")
        prompt_path = _os.path.join(diag_dir, f"{ts}_prompt.txt")
        payload = {
            "ts": ts,
            "iteration": iteration,
            "status": status,
            "elapsed": elapsed,
            "error": error,
            "traceback": tb,
            "agent": agent,
            "phase": phase,
            "prompt_len": len(prompt) if prompt else 0,
        }
        if extra:
            payload.update(extra)
        with open(meta_path, "w", encoding="utf-8") as f:
            _json.dump(payload, f, ensure_ascii=False, indent=2)
        if text is not None:
            with open(raw_path, "w", encoding="utf-8") as f:
                f.write(text)
        # AI-RESPONSE-LOG: сохраняем промпт отдельно
        if prompt is not None:
            with open(prompt_path, "w", encoding="utf-8") as f:
                f.write(prompt)
    except Exception:
        pass


def _track_state(func):
    """Обёртка: после завершения задачи пишет статистику и статус в state.json."""
    def wrapper(task, project_dir, agent_mode):
        start = time.time()
        try:
            result = func(task, project_dir, agent_mode)
        except Exception:
            elapsed = time.time() - start
            try:
                from core.project_state import record_agent_result, update_work
                record_agent_result(project_dir, agent_mode, False, elapsed)
                update_work(project_dir, current_status="exception")
            except Exception:
                pass
            raise
        elapsed = time.time() - start
        try:
            from core.project_state import record_agent_result, update_work
            success = bool(result.get("success"))
            record_agent_result(project_dir, agent_mode, success, elapsed)
            status = result.get("status", "unknown")
            update_work(project_dir, current_status=status)
        except Exception:
            pass
        return result
    wrapper.__name__ = func.__name__
    return wrapper


@_track_state
def run_agentic_loop(task: str, project_dir: str, agent_mode: str) -> Dict[str, Any]:
    """Запускает цикл. Возвращает результат с files_created и history."""
    import traceback as _tb
    hand = HandLink(project_dir)
    history: List[Dict[str, Any]] = []
    all_created_files: List[str] = []
    # B55 (2026-10-13): параллельные списки для честной оценки
    # работы фазы. changed_files — правки существующих файлов;
    # *_by_command — реальные изменения от run_command (снапшот).
    all_changed_files: List[str] = []
    all_created_by_cmd: List[str] = []
    all_changed_by_cmd: List[str] = []
    # FIX SAFE-REPLACE (2026-09-28): предупреждения об удалении
    # def/class, которые вызываются в других файлах.
    all_safe_warnings: List[Dict[str, Any]] = []
    start = time.time()
    consecutive_errors = 0
    last_error = None

    # SMART-EXIT: умное управление циклом
    term = _SmartTermination()
    loop_det = _LoopDetector()
    stop_reason = "unknown"

    while True:
        iteration = term.step()
        elapsed = time.time() - start
        if elapsed > MAX_TIME_SEC:
            _save_diag(iteration=iteration, status="timeout_total",
                       error=f"Превышен таймаут ({MAX_TIME_SEC}s)",
                       elapsed=elapsed)
            return {
                "success": False,
                "status": "timeout_total",
                "error": f"Превышен таймаут ({MAX_TIME_SEC}s)",
                "files_created": all_created_files,

                "files_changed": all_changed_files,

                "created_by_command": all_created_by_cmd,

                "changed_by_command": all_changed_by_cmd,
                "history": history,
                "iterations": iteration,
                "elapsed": elapsed,
            }

        force_refresh = (consecutive_errors >= 2)
        prompt = _build_prompt(task, project_dir, history, iteration, force_refresh)

        try:
            result = _call_agent(agent_mode, prompt)
        except Exception as e:
            tb = _tb.format_exc()
            _save_diag(iteration=iteration, status="exception",
                       error=str(e), tb=tb, elapsed=time.time() - start)
            return {
                "success": False,
                "status": "exception",
                "error": f"Исключение в _call_agent: {e}",
                "traceback": tb,
                "files_created": all_created_files,

                "files_changed": all_changed_files,

                "created_by_command": all_created_by_cmd,

                "changed_by_command": all_changed_by_cmd,
                "history": history,
                "iterations": iteration,
                "elapsed": time.time() - start,
            }

        if not result.get("success"):
            err = result.get("error", "неизвестная ошибка агента")
            _save_diag(iteration=iteration, status="agent_error",
                       error=err, text=result.get("text", ""),
                       elapsed=time.time() - start)
            return {
                "success": False,
                "status": "agent_error",
                "error": f"Ошибка агента: {err}",
                "files_created": all_created_files,

                "files_changed": all_changed_files,

                "created_by_command": all_created_by_cmd,

                "changed_by_command": all_changed_by_cmd,
                "history": history,
                "iterations": iteration,
                "elapsed": time.time() - start,
            }

        text = result.get("text", "")
        _save_diag(iteration=iteration, status="got_response",
                   text=text, elapsed=time.time() - start,
                   prompt=prompt, agent=agent_mode,
                   phase="code_or_fix")

        validation = validate_response(text, project_dir, task)

        if not validation.get("ok"):
            err = validation.get("error", "неизвестная ошибка валидации")
            if err == last_error:
                consecutive_errors += 1
            else:
                consecutive_errors = 1
                last_error = err
            history.append({"error": err})
            term.record_idle()
            _save_diag(iteration=iteration, status="validation_error",
                       error=err, elapsed=time.time() - start)
            if consecutive_errors >= MAX_SAME_ERRORS:
                _save_diag(iteration=iteration, status="too_many_errors",
                           error=f"Повторяющаяся ошибка: {err}",
                           elapsed=time.time() - start)
                return {
                    "success": False,
                    "status": "too_many_errors",
                    "error": f"Повторяющаяся ошибка: {err}",
                    "files_created": all_created_files,

                    "files_changed": all_changed_files,

                    "created_by_command": all_created_by_cmd,

                    "changed_by_command": all_changed_by_cmd,
                    "history": history,
                    "iterations": iteration + 1,
                    "elapsed": time.time() - start,
                }
            continue

        consecutive_errors = 0
        last_error = None

        # SMART-EXIT: если ответ валидный, но без действия —
        # это idle-итерация (размышление, пустой ответ и т.п.)
        if validation["kind"] not in ("task_complete", "action"):
            term.record_idle()

        # FIX QUESTIONS-FALLBACK: модель задала вопросы —
        # возвращаем needs_clarification, не крутим idle.
        if validation["kind"] == "questions":
            _save_diag(iteration=iteration, status="questions",
                       text=validation.get("questions", "")[:500],
                       elapsed=time.time() - start)
            return {
                "success": False,
                "status": "needs_clarification",
                "clarification_question":
                    validation.get("questions", "")[:2000],
                "clarification_target": "user",
                "files_created": all_created_files,

                "files_changed": all_changed_files,

                "created_by_command": all_created_by_cmd,

                "changed_by_command": all_changed_by_cmd,
                "history": history,
                "iterations": iteration + 1,
                "elapsed": time.time() - start,
            }

        if validation["kind"] == "task_complete":
            _save_diag(iteration=iteration, status="task_complete",
                       elapsed=time.time() - start)
            return {
                "success": True,
                "status": "success",
                "task_complete": True,
                "message": validation.get("message", ""),
                "files_created": all_created_files,

                "files_changed": all_changed_files,

                "created_by_command": all_created_by_cmd,

                "changed_by_command": all_changed_by_cmd,
                "history": history,
                "safe_replace_warnings": all_safe_warnings,
                "iterations": iteration + 1,
                "elapsed": time.time() - start,
            }

        if validation["kind"] == "action":
            action = validation["action"]

            # SMART-EXIT: детектор зацикливания
            if loop_det.add(action):
                _save_diag(iteration=iteration,
                           status="loop_detected",
                           elapsed=time.time() - start,
                           extra={"action_type": action.get("type")})
                return {
                    "success": False,
                    "status": "loop_detected",
                    "stop_reason": "loop_detected",
                    "error": "Обнаружено зацикливание (5 одинаковых действий)",
                    "files_created": all_created_files,

                    "files_changed": all_changed_files,

                    "created_by_command": all_created_by_cmd,

                    "changed_by_command": all_changed_by_cmd,
                    "history": history,
                    "iterations": term.iteration,
                    "elapsed": time.time() - start,
                }

            exec_result = hand.execute([action])
            history.append({"action": action, "result": exec_result})
            # FIX SAFE-REPLACE: собираем предупреждения
            _sw = exec_result.get("safe_replace_warnings") or []
            if _sw:
                all_safe_warnings.extend(_sw)
            if exec_result.get("created_files"):
                all_created_files.extend(exec_result["created_files"])
                for _f in exec_result["created_files"]:
                    term.record_file()
            # B55: собираем правки и изменения от run_command
            if exec_result.get("changed_files"):
                all_changed_files.extend(exec_result["changed_files"])
            if exec_result.get("created_by_command"):
                all_created_by_cmd.extend(
                    exec_result["created_by_command"]
                )
            if exec_result.get("changed_by_command"):
                all_changed_by_cmd.extend(
                    exec_result["changed_by_command"]
                )

            # SMART-EXIT: прогресс = успешное действие
            if exec_result.get("success"):
                term.record_action()
            else:
                term.record_idle()

            _save_diag(iteration=iteration, status="action_executed",
                       elapsed=time.time() - start,
                       extra={
                           "action_type": action.get("type"),
                           "exec_success": exec_result.get("success"),
                       })

            if len(history) >= 3:
                last3 = history[-3:]
                keys = []
                for h in last3:
                    a = h.get("action", {})
                    # Б73 (15.10.2026): ключ должен включать ВСЕ значимые
                    # поля action. Раньше для run_command ключ был
                    # ("run_command", "", "") — одинаковый для РАЗНЫХ
                    # команд. Детектор ложно считал разведку
                    # зацикливанием и прерывал работу.
                    k = (
                        a.get("type"),
                        str(a.get("path", "") or "")[:200],
                        str(a.get("command", "") or "")[:200],
                        str(a.get("find", "") or "")[:100],
                        str(a.get("replace", "") or "")[:100],
                        str(a.get("content", "") or "")[:100],
                    )
                    keys.append(k)
                if len(set(keys)) == 1 and exec_result.get("success"):
                    _save_diag(iteration=iteration,
                               status="auto_complete_loop_protection",
                               elapsed=time.time() - start)
                    return {
                        "success": True,
                        "status": "success",
                        # ROLLBACK-15.10.2026: убрано task_complete=True.
                        # auto_complete может сработать ПРЕЖДЕВРЕМЕННО
                        # (см. Б73 — детектор путал разные run_command).
                        # Это НЕ признак «работа сделана».
                        "message": "Задача завершена автоматически (действие повторялось успешно)",
                        "files_created": all_created_files,

                        "files_changed": all_changed_files,

                        "created_by_command": all_created_by_cmd,

                        "changed_by_command": all_changed_by_cmd,
                        "history": history,
                "safe_replace_warnings": all_safe_warnings,
                        "iterations": iteration + 1,
                        "elapsed": time.time() - start,
                    }

        # SMART-EXIT: проверка условий остановки после итерации
        _stop, stop_reason = term.should_stop()
        if _stop:
            _save_diag(iteration=term.iteration, status=stop_reason,
                       elapsed=time.time() - start,
                       extra={
                           "idle_count": term.idle_count,
                           "actions_count": term.actions_count,
                           "files_created": term.files_created,
                       })
            if stop_reason == "no_progress":
                return {
                    "success": False,
                    "status": "no_progress",
                    "stop_reason": stop_reason,
                    "error": (
                        f"Нет прогресса: {term.idle_count} итераций "
                        f"без действий"
                    ),
                    "files_created": all_created_files,

                    "files_changed": all_changed_files,

                    "created_by_command": all_created_by_cmd,

                    "changed_by_command": all_changed_by_cmd,
                    "history": history,
                    "iterations": term.iteration,
                    "elapsed": time.time() - start,
                }
            # max_iterations
            return {
                "success": False,
                "status": "max_iterations",
                "stop_reason": stop_reason,
                "error": (
                    f"Превышено число итераций ({term.max_iterations})"
                ),
                "files_created": all_created_files,

                "files_changed": all_changed_files,

                "created_by_command": all_created_by_cmd,

                "changed_by_command": all_changed_by_cmd,
                "history": history,
                "iterations": term.iteration,
                "elapsed": time.time() - start,
            }
