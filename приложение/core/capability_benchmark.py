"""Capability Profile — бенчмарк браузерных моделей AI Link.

Прогоняет одинаковые короткие задачи через каждую включённую
браузерную модель и записывает: прошла/не прошла + время.
Результат — в ~/.ai_link/brain/capabilities.json.

Запускается ТОЛЬКО вручную (кнопка в UI). Не автоматически.
Задачи подобраны с проверяемым ответом (regex по тексту ответа),
без LLM-судьи — оценка объективная.

Используется оркестратором (фаза 3): если в capabilities.json
есть pass_rate агента по нужной категории, оркестратор может
выбрать агента по нему, а не по жёсткому PHASE_DEFAULT_AGENT.
"""

import json
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional


CAPABILITIES_FILE = Path.home() / ".ai_link" / "brain" / "capabilities.json"

CATEGORIES = ["basic", "planning", "reasoning", "coding", "comprehension"]


# === Тестовые задачи ===
# Каждая: id, category, prompt, regex (что искать в ответе).
# По умолчанию regex case-insensitive; можно переопределить
# через regex_flags (например, re.DOTALL для многострочного).
BENCHMARK_TASKS: List[Dict[str, Any]] = [
    # === basic ===
    # Цепочка арифметики: надо удержать 3 значения и посчитать сумму.
    {
        "id": "b1",
        "category": "basic",
        "prompt": (
            "У Ани 5 яблок. У Бори в 3 раза больше, чем у Ани. "
            "У Веры на 4 яблока меньше, чем у Ани и Бори вместе. "
            "Сколько яблок у всех троих вместе? "
            "Ответь ровно одним числом."
        ),
        "regex": r"\b36\b",
    },
    # === planning (1) ===
    # План из 5 этапов с зависимостями — надо удержать структуру.
    {
        "id": "p1",
        "category": "planning",
        "prompt": (
            "Составь план разработки проекта «телефонная книга» "
            "из 5 этапов. Каждый этап пронумеруй (1., 2., 3., 4., 5.). "
            "В каждом этапе укажи, от чего он зависит (например: "
            "«зависит от этапа 2»). Если этап не зависит ни от чего — "
            "напиши «не зависит»."
        ),
        # Не требуем цифр (1., 2., ...) — модель может писать
        # «Зависит: не зависит» без нумерации. Требуем СМЫСЛ:
        # минимум 5 упоминаний «зависит» + есть «не зависит» + тема.
        "regex": r"(зависит[\s\S]*?){5,}",
        "regex_flags": re.DOTALL | re.IGNORECASE,
        "semantic_groups": [
            r"не\s+зависит",
            r"зависит\s+от\s+этапа",
            r"телефон",
        ],
        "semantic_min_matches": 2,
    },
    # === planning (2) ===
    # Вопросы пользователю про приложение — проверяем умение
    # собирать требования (7 разных аспектов).
    {
        "id": "p2",
        "category": "planning",
        "prompt": (
            "Пользователь пишет: «Хочу приложение для учёта расходов».\n\n"
            "Задай ему список вопросов, которые нужно прояснить, "
            "ПРЕЖДЕ чем начать разработку. Не пиши код. Не пиши план. "
            "Только вопросы.\n\n"
            "Требования:\n"
            "  • ровно 7 вопросов\n"
            "  • каждый вопрос с новой строки\n"
            "  • каждый вопрос начинается с цифры и точки "
            "(1., 2., ..., 7.)\n"
            "  • вопросы должны раскрывать РАЗНЫЕ аспекты: "
            "платформа, пользователи, функции, данные, интеграции, "
            "безопасность, критерии готовности"
        ),
        # Структура: минимум 7 вопросительных знаков.
        # Модель может не нумеровать вопросы цифрами — важно,
        # что она задаёт 7 разных вопросов.
        "regex": r"([?][\s\S]*?){6,}[?]",
        "regex_flags": re.DOTALL,
        # Смысловые группы — проверяются отдельно (см. _check_answer_semantic)
        "semantic_groups": [
            r"платформ|мобильн|десктоп|веб|сайт",
            r"пользоват|аудитор|клиент",
            r"функци|возможност|сценари",
            r"данн|храни|база|бд",
            r"интеграц|синхрониз|экспорт|импорт",
            r"безопасн|авториз|вход|парол",
            r"готов|завершен|тест|критери",
        ],
        "semantic_min_matches": 5,   # минимум 5 групп из 7
    },
    # === reasoning ===
    # Противоречие: 1 + 3 => Мария пьёт кофе, 2 => не пьёт. → Да.
    {
        "id": "r1",
        "category": "reasoning",
        "prompt": (
            "Три утверждения:\n"
            "1. Все программисты пьют кофе.\n"
            "2. Мария не пьёт кофе.\n"
            "3. Мария — программист.\n\n"
            "Есть ли противоречие между этими утверждениями? "
            "Ответь ровно одним словом: Да или Нет."
        ),
        "regex": r"\bда\b",
    },
    # === coding ===
    # FizzBuzz со строгим требованием порядка: % 15 должен идти
    # до % 3 (иначе кратные 15 попадут в Fizz, а не в FizzBuzz).
    {
        "id": "c1",
        "category": "coding",
        "prompt": (
            "Напиши на Python функцию fizzbuzz(n), которая "
            "возвращает список чисел от 1 до n, где:\n"
            "  - кратные 3 заменены на \"Fizz\",\n"
            "  - кратные 5 — на \"Buzz\",\n"
            "  - кратные и 3, и 5 — на \"FizzBuzz\",\n"
            "  - остальные — само число.\n"
            "Только код функции, без примеров и пояснений."
        ),
        # Требуем правильный ПОРЯДОК: FizzBuzz -> Fizz -> Buzz.
        # Формат проверки не важен: `% 15 == 0` или
        # `% 3 == 0 and % 5 == 0` — оба корректны.
        "regex": r"def\s+fizzbuzz[\s\S]*?FizzBuzz[\s\S]*?Fizz[\s\S]*?Buzz",
        "regex_flags": re.DOTALL | re.IGNORECASE,
    },
    # === comprehension ===
    # Надо «прокрутить» цикл в голове: [1,2,3,4] → [2,2,4,4].
    {
        "id": "k1",
        "category": "comprehension",
        "prompt": (
            "Что вернёт этот код при вызове mystery([1, 2, 3, 4])?\n\n"
            "def mystery(items):\n"
            "    result = []\n"
            "    for i in range(len(items)):\n"
            "        if items[i] % 2 == 0:\n"
            "            result.append(items[i])\n"
            "        else:\n"
            "            result.append(items[i] + 1)\n"
            "    return result\n\n"
            "Ответь ровно одним списком в формате [a, b, c, d]."
        ),
        "regex": r"\[\s*2\s*,\s*2\s*,\s*4\s*,\s*4\s*\]",
    },
]


# === Вспомогательное ===

def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _check_answer(text: str, task: Dict[str, Any]) -> bool:
    """Проверяет ответ задачи.

    1. Обязательно — совпадение по основному regex.
    2. Если у задачи есть semantic_groups — дополнительно
       проверяем, что найдено не меньше semantic_min_matches групп.
    """
    if not text:
        return False
    flags = task.get("regex_flags", re.IGNORECASE)
    pattern = task["regex"]
    try:
        if re.search(pattern, text, flags) is None:
            return False
    except re.error:
        return False

    # Дополнительная смысловая проверка (для задач, где надо
    # не просто структуру, а содержание).
    groups = task.get("semantic_groups")
    if groups:
        need = int(task.get("semantic_min_matches", len(groups)))
        matched = 0
        for g in groups:
            try:
                if re.search(g, text, re.IGNORECASE):
                    matched += 1
            except re.error:
                pass
        if matched < need:
            return False

    return True


def _run_one(agent_id: str, task: Dict[str, Any]) -> Dict[str, Any]:
    """Прогоняет одну задачу через агента. Возвращает результат."""
    start = time.time()
    result = {
        "task_id": task["id"],
        "category": task["category"],
        "agent": agent_id,
        "passed": False,
        "seconds": 0.0,
        "raw": "",
        "error": "",
    }
    try:
        from core.agentic_loop import _call_agent
        r = _call_agent(agent_id, task["prompt"])
    except Exception as e:
        result["seconds"] = round(time.time() - start, 2)
        result["error"] = str(e)[:300]
        return result

    result["seconds"] = round(time.time() - start, 2)
    if not r.get("success"):
        result["error"] = str(r.get("error") or "unknown")[:300]
        return result

    text = r.get("text", "") or ""
    result["raw"] = text[:2000]
    result["passed"] = _check_answer(text, task)
    return result


def _get_enabled_browser_agents() -> List[str]:
    """Возвращает id включённых браузерных агентов из ~/.ai_link/agents.json.

    Исключает browser_perplexity (отложен из-за Cloudflare).
    Если файла нет — возвращает дефолтную пару.
    """
    path = Path.home() / ".ai_link" / "agents.json"
    if not path.exists():
        return ["browser_qwen", "browser_deepseek"]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return ["browser_qwen", "browser_deepseek"]
    result: List[str] = []
    for a in data if isinstance(data, list) else []:
        if not a.get("enabled", False):
            continue
        aid = a.get("id", "")
        if aid.startswith("browser_") and aid != "browser_perplexity":
            result.append(aid)
    return result or ["browser_qwen", "browser_deepseek"]


def _aggregate(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Собирает статистику по категориям для одного агента."""
    by_cat: Dict[str, Dict[str, Any]] = {}
    for cat in CATEGORIES:
        by_cat[cat] = {"passed": 0, "failed": 0, "total_sec": 0.0, "raw": []}

    for r in results:
        cat = r["category"]
        if cat not in by_cat:
            continue
        if r["passed"]:
            by_cat[cat]["passed"] += 1
        else:
            by_cat[cat]["failed"] += 1
        by_cat[cat]["total_sec"] += r["seconds"]
        by_cat[cat]["raw"].append({
            "task_id": r["task_id"],
            "passed": r["passed"],
            "seconds": r["seconds"],
            "raw": r["raw"][:800],
            "error": r["error"],
        })

    summary: Dict[str, Any] = {}
    for cat, s in by_cat.items():
        total = s["passed"] + s["failed"]
        summary[cat] = {
            "passed": s["passed"],
            "failed": s["failed"],
            "pass_rate": round(s["passed"] / total, 2) if total else 0.0,
            "avg_sec": round(s["total_sec"] / total, 2) if total else 0.0,
            "raw": s["raw"],
        }

    return {
        "summary": summary,
        "tested_at": _now(),
    }


# === Публичное API ===

def run_benchmark(agent_ids: Optional[List[str]] = None,
                  on_progress: Optional[Callable] = None) -> Dict[str, Any]:
    """Прогоняет бенчмарк по указанным агентам.

    agent_ids — список id; если None — берём все включённые браузерные.
    on_progress(done, total, current_result) — колбэк прогресса.
        done — сколько задач уже сделано
        total — сколько всего задач
        current_result — результат последней задачи (dict)

    Возвращает: {"started_at", "finished_at", "results": {agent: {...}}}
    """
    if agent_ids is None:
        agent_ids = _get_enabled_browser_agents()

    started_at = _now()
    all_results: Dict[str, Any] = {}
    total = len(agent_ids) * len(BENCHMARK_TASKS)
    done = 0

    for agent_id in agent_ids:
        agent_results: List[Dict[str, Any]] = []
        for task in BENCHMARK_TASKS:
            r = _run_one(agent_id, task)
            agent_results.append(r)
            done += 1
            if on_progress:
                try:
                    on_progress(done, total, r)
                except Exception:
                    pass
        all_results[agent_id] = _aggregate(agent_results)

    return {
        "started_at": started_at,
        "finished_at": _now(),
        "results": all_results,
    }


def save_results(data: Dict[str, Any]) -> None:
    """Сохраняет результаты в capabilities.json, сливая с существующими."""
    existing = load_results() or {"version": "1.0", "results": {}}
    existing["updated_at"] = data.get("finished_at", _now())

    for agent, res in data.get("results", {}).items():
        existing["results"][agent] = res

    CAPABILITIES_FILE.parent.mkdir(parents=True, exist_ok=True)
    try:
        CAPABILITIES_FILE.write_text(
            json.dumps(existing, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception:
        pass


def load_results() -> Optional[Dict[str, Any]]:
    """Читает capabilities.json. None, если файла нет или он битый."""
    if not CAPABILITIES_FILE.exists():
        return None
    try:
        return json.loads(CAPABILITIES_FILE.read_text(encoding="utf-8"))
    except Exception:
        return None


def get_agent_pass_rate(agent_id: str, category: str) -> Optional[float]:
    """Pass rate агента по категории (0.0-1.0) или None, если нет данных.

    Используется оркестратором для выбора агента на фазу.
    """
    data = load_results()
    if not data:
        return None
    agents = data.get("results", {})
    if agent_id not in agents:
        return None
    summary = agents[agent_id].get("summary", {})
    cat = summary.get(category)
    if not cat:
        return None
    return cat.get("pass_rate")


def get_best_agent_for_category(category: str,
                                candidates: Optional[List[str]] = None
                                ) -> Optional[str]:
    """Возвращает id агента с лучшим pass_rate по категории.

    Если у нескольких одинаковый pass_rate — берёт первого.
    Если данных нет — возвращает None (оркестратор упадёт на
    свой жёсткий PHASE_DEFAULT_AGENT).
    """
    if candidates is None:
        candidates = _get_enabled_browser_agents()
    best_agent: Optional[str] = None
    best_rate = -1.0
    for aid in candidates:
        rate = get_agent_pass_rate(aid, category)
        if rate is not None and rate > best_rate:
            best_rate = rate
            best_agent = aid
    return best_agent


# === Группировка агентов для UI ===

GROUP_BROWSER = "browser"
GROUP_API = "api"
GROUP_LOCAL = "local"

GROUP_TITLES = {
    GROUP_BROWSER: "Браузерные",
    GROUP_API: "API",
    GROUP_LOCAL: "Локальные (Ollama — нагрузка на RAM)",
}


def _agent_group(agent: Dict[str, Any]) -> str:
    """Определяет группу агента по type из agents.json."""
    t = (agent.get("type") or "").lower()
    if t in ("browser", "api", "local"):
        return t
    # Фолбэк по префиксу id
    aid = agent.get("id", "")
    if aid.startswith("browser_"):
        return GROUP_BROWSER
    if aid == "ollama":
        return GROUP_LOCAL
    return GROUP_API


def list_available_agents() -> List[Dict[str, Any]]:
    """Возвращает всех агентов из agents.json с полем group.

    Формат каждого:
        {"id": "...", "name": "...", "group": "browser|api|local",
         "enabled": true/false}
    Исключает browser_perplexity (отложен).
    """
    path = Path.home() / ".ai_link" / "agents.json"
    if not path.exists():
        return [
            {"id": "browser_qwen", "name": "Qwen (браузер)",
             "group": GROUP_BROWSER, "enabled": True},
            {"id": "browser_deepseek", "name": "DeepSeek (браузер)",
             "group": GROUP_BROWSER, "enabled": True},
        ]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    result: List[Dict[str, Any]] = []
    for a in data if isinstance(data, list) else []:
        aid = a.get("id", "")
        if not aid or aid == "browser_perplexity":
            continue
        result.append({
            "id": aid,
            "name": a.get("name", aid),
            "group": _agent_group(a),
            "enabled": bool(a.get("enabled", False)),
        })
    return result


def get_agents_by_group(group: str) -> List[str]:
    """Список id агентов указанной группы."""
    return [a["id"] for a in list_available_agents() if a["group"] == group]


# === Явные назначения агентов на фазы (agent_assignments.json) ===
#
# Пользователь может явно указать, какой агент работает на какой
# фазе. Приоритет этого файла выше автоматики (Capability Profile)
# и выше жёстких ролей PHASE_DEFAULT_AGENT.
#
# Ключи — категории: planning, coding, comprehension.
# Плюс спец-ключ "fallback" — аварийная фаза (не имеет категории).
#
# Значение — agent_id или null ("пусть решает автоматика").
#
# Если назначенный агент ВЫКЛЮЧЕН в agents.json — назначение
# игнорируется, идём дальше по цепочке приоритетов.

ASSIGNMENTS_FILE = Path.home() / ".ai_link" / "brain" / "agent_assignments.json"

# ROLES (2026-10-01): 7 ролей.
# Убраны concept (→ planning), fallback (→ brain).
# Добавлены review (отдельно от comprehension) и verifier (cross_verify).
ASSIGNMENT_KEYS = [
    "planning", "coding", "review", "verifier",
    "dialog", "comprehension", "brain",
]

# ROLES (2026-10-01): роли-монополисты. Если модель назначена сюда,
# она не может быть назначена на другие роли — в UI показывается
# серой внизу списка, недоступной для выбора.
MONOPOLIST_ROLES = {"brain", "dialog"}


def _migrate_assignments(data: Dict[str, Any]) -> bool:
    """MIGRATE (2026-10-01): миграция старых назначений.

    Что делает:
      1. concept → planning (если planning пусто), удалить concept
      2. fallback — удалить (роль влилась в brain)

    A.1 (2026-10-04): авто-снятие конфликтов монополистов УДАЛЕНО.
      Теперь конфликты видит check_role_conflicts(), а orchestrate()
      возвращает failed с понятной ошибкой. См. test_role_conflicts.py.

    Возвращает True, если что-то изменилось.
    """
    a = data.get("assignments")
    if not isinstance(a, dict):
        return False
    changed = False

    # 1. concept → planning
    concept_val = a.pop("concept", None)
    if concept_val is not None:
        changed = True
        if not a.get("planning"):
            a["planning"] = concept_val

    # 2. fallback — удалить
    if "fallback" in a:
        a.pop("fallback", None)
        changed = True

    # A.1 (2026-10-04): блок авто-снятия монополистов УДАЛЁН.
    # Конфликты видит check_role_conflicts() в orchestrate().

    return changed


def load_assignments(auto_migrate: bool = True) -> Dict[str, Any]:
    """Читает agent_assignments.json. Возвращает {"assignments": {...}}.

    MIGRATE (2026-10-01): авто-миграция старых ключей (concept, fallback)
    и конфликтов монополистов. Мигрированный файл сохраняется.
    """
    empty = {"version": "1.0", "assignments": {}, "updated_at": ""}
    if not ASSIGNMENTS_FILE.exists():
        return empty
    try:
        data = json.loads(ASSIGNMENTS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return empty
    if not isinstance(data, dict):
        return empty
    if "assignments" not in data or not isinstance(data["assignments"], dict):
        data["assignments"] = {}
    if auto_migrate:
        if _migrate_assignments(data):
            try:
                save_assignments(data)
            except Exception:
                pass
    return data


def save_assignments(data: Dict[str, Any]) -> None:
    """Сохраняет структуру в agent_assignments.json."""
    ASSIGNMENTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    data["updated_at"] = _now()
    try:
        ASSIGNMENTS_FILE.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception:
        pass


def check_role_conflicts() -> Dict[str, List[str]]:
    """ROLES-CONFLICT (2026-10-04): проверка конфликтов ролей.

    Правила:
      • brain — монополист. Если brain = coding (или review/fix/
        planning) → блокирующая ошибка: модель не может
        контролировать сама себя.
      • dialog — монополист (занимает браузер). Если dialog =
        coding (или другая рабочая роль) → предупреждение:
        диалог будет недоступен, пока идёт прогон.
      • coding = review = fix — разрешено (разные фазы,
        чат сбрасывается между ними).

    Возвращает:
      {
        "blocking": [<строки>],   # прерывать запуск
        "warnings": [<строки>],   # только логировать
      }
    """
    data = load_assignments()
    a = data.get("assignments", {}) or {}
    brain = a.get("brain")
    dialog = a.get("dialog")

    blocking: List[str] = []
    warnings: List[str] = []

    # A.1 (2026-10-04): все рабочие роли. brain/dialog — монополисты

    # (им нужен браузер для периодического чата). Если brain/dialog

    # НЕ назначены — конфликтов нет. coding=review=fix — ок.

    _work_roles = (

        "coding", "review", "fix", "planning",

        "verifier", "comprehension",

    )
    if brain:
        for other in _work_roles:
            if a.get(other) == brain:
                blocking.append(
                    f"brain={other}: {brain} не может "
                    f"контролировать сам себя (одна модель)"
                )

    if dialog:
        for other in _work_roles:
            if a.get(other) == dialog:
                warnings.append(
                    f"dialog={other}: {dialog} может занять "
                    f"браузер — диалог будет недоступен"
                )

    return {"blocking": blocking, "warnings": warnings}


def get_assignment(key: str) -> Optional[str]:
    """Возвращает agent_id для указанной категории или None."""
    data = load_assignments()
    val = data.get("assignments", {}).get(key)
    if val is None or val == "":
        return None
    return str(val)


def set_assignment(key: str, agent_id: Optional[str]) -> bool:
    """Устанавливает назначение. agent_id=None — удаляет запись."""
    if key not in ASSIGNMENT_KEYS:
        return False
    data = load_assignments()
    assignments = data.setdefault("assignments", {})
    if agent_id is None or agent_id == "":
        assignments.pop(key, None)
    else:
        assignments[key] = str(agent_id)
    save_assignments(data)
    return True


def clear_assignment(key: str) -> bool:
    """Убирает назначение по ключу (возврат к автоматике)."""
    return set_assignment(key, None)


def _is_agent_enabled(agent_id: str) -> bool:
    """Проверяет, включён ли агент в agents.json.

    Если файла нет или агент не найден — считаем включённым,
    чтобы не блокировать тестовые окружения.
    """
    path = Path.home() / ".ai_link" / "agents.json"
    if not path.exists():
        return True
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return True
    if not isinstance(data, list):
        return True
    for a in data:
        if a.get("id") == agent_id:
            return bool(a.get("enabled", False))
    return True


def get_assigned_agent(key: str) -> Optional[str]:
    """Возвращает agent_id из назначений, ТОЛЬКО если он включён.

    Если назначенный агент выключен — возвращает None (автоматика).

    ROLES (2026-10-01): миграция. Если роль 'review' не назначена,
    но 'comprehension' — используем её (раньше это была одна роль).
    """
    aid = get_assignment(key)
    if not aid and key == "review":
        aid = get_assignment("comprehension")
    if not aid:
        return None
    if not _is_agent_enabled(aid):
        return None
    return aid


# === Информация о категориях и фазах (для UI) ===

CATEGORY_INFO: Dict[str, Dict[str, str]] = {
    "basic": {
        "title": "Базовое",
        "desc": "Совсем элементарное: «Скажи Привет, Мир», "
                "«Сколько 2+2». Проверка, что модель вообще "
                "отвечает на простые вопросы и не сбоит.",
    },
    "planning": {
        "title": "Планирование",
        "desc": "Построить план из N шагов, структурировать задачу.",
    },
    "reasoning": {
        "title": "Логика",
        "desc": "Логические выводы: «Если A>B и B>C — что больше?»",
    },
    "coding": {
        "title": "Код",
        "desc": "Написать короткую функцию.",
    },
    "comprehension": {
        "title": "Понимание",
        "desc": "Прочитать код и объяснить, что он делает / "
                "что произойдёт.",
    },
}

PHASE_INFO: Dict[str, Dict[str, str]] = {
    "planning": {
        "title": "План",
        "desc": "Разбор задачи, построение плана и структуры проекта.",
    },
    "coding": {
        "title": "Код",
        "desc": "Написание кода по плану, исправление замечаний.",
    },
    "comprehension": {
        "title": "Проверка",
        "desc": "Поиск проблем и ошибок в готовом коде.",
    },
    "fallback": {
        "title": "Резерв",
        "desc": "Аварийный агент, если фаза «Исправление» "
                "не справилась два раза подряд.",
    },
    "concept": {
        "title": "Концепция",
        "desc": "Генерация concept.md по истории диалога. "
                "Не занимает браузер, не участвует в основном "
                "цикле фаз. ВАЖНО: нужно выбрать агента явно — "
                "«Авто» для концепции не работает (её нет "
                "в бенчмарке).",
    },
    "dialog": {
        "title": "Диалог",
        "desc": "Обсуждение концепции с пользователем. "
                "Модель видит память проекта, concept.md и "
                "историю диалога. НЕ пишет код, только "
                "задаёт вопросы и предлагает варианты.",
    },
    "brain": {
        "title": "Мозг",
        "desc": "Арбитр между фазами (rule_brain → LLM). "
                "Решает: продолжать / чинить / стоп / "
                "сменить подход. НЕ пишет код.",
    },

}


def category_tooltip(category: str) -> str:
    """Возвращает текст подсказки для категории (для UI)."""
    info = CATEGORY_INFO.get(category)
    if not info:
        return category
    return f"{info['title']}: {info['desc']}"


def phase_tooltip(key: str) -> str:
    """Возвращает текст подсказки для фазы/категории назначения.

    FIX: устойчиво к отсутствию title/desc (не падает на KeyError).
    """
    info = PHASE_INFO.get(key)
    if not info:
        return key
    _title = info.get("title") or info.get("name") or key
    _desc = info.get("desc") or info.get("role") or ""
    if not _desc:
        return _title
    return f"{_title}: {_desc}"


# ROLES (2026-10-01): обновление PHASE_INFO для 7 ролей.
if "PHASE_INFO" in globals():
    PHASE_INFO.update({
        "planning": {
            "title": "Планирование",
            "desc": "Строит план проекта. Лучше умная модель.",
        },
        "coding": {
            "title": "Код",
            "desc": "Пишет и правит код (code + fix). Лучше умная.",
        },
        "review": {
            "title": "Проверка кода",
            "desc": "Проверяет логику после написания. Желательно ≠ coding.",
        },
        "verifier": {
            "title": "Второе мнение",
            "desc": "Проверяет проблемы от review. Часто Perplexity.",
        },
        "dialog": {
            "title": "Поболтать",
            "desc": "Обсуждение проекта. Занимает браузер — монополист.",
        },
        "comprehension": {
            "title": "Понимание",
            "desc": "Понимает проект для аудита. Не для review.",
        },
        "brain": {
            "title": "Мозг (аварийный)",
            "desc": "Зовётся в патовых ситуациях. Монополист.",
        },
    })

    def phase_tooltip(key: str) -> str:
        """ROLES (2026-10-01): tooltip для роли."""
        info = PHASE_INFO.get(key, {})
        return info.get("desc", "")
