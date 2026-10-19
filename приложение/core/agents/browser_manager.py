"""
Постоянный менеджер браузера для AI Link.
Один браузер, много вкладок. Живёт всё время работы приложения.
"""


import logging

logger = logging.getLogger(__name__)
import os
import queue
import threading
import time
from pathlib import Path
from typing import Dict, Any, Optional

from core.agents.browser_sites_manager import get_site
from config import timeouts  # TIMEOUTS-IMPORT (2026-10-02)
import atexit

BROWSER_PROFILE_DIR = Path.home() / ".ai_link" / "browser_profiles" / "ai_link_browser"


# CHAT-LIFECYCLE: маркеры переполнения чата. Если модель
# (или UI сайта) показывает один из них — считаем чат
# переполненным, нужен reset.
# FIX ECHO: маркеры нашего промпта. Если элемент DOM содержит
# фрагмент промпта (29k байт) — это эхо, а не ответ модели.
# Симптом: max(len) берёт НАШ промпт вместо ответа (промпт длиннее).
_ECHO_MARKERS = (
    "[tool result",
    "[end tool result]",
    "=== проект ===",
    "твой следующий шаг",
    "рабочая папка:",
    "[фаза:",
    "смысл фазы:",
    "оригинальная задача:",
    "история фаз:",
    "текущий план:",
    "=== правила ===",
    "[tool result: validation",
    "(напоминание: ответ",
)


def _is_prompt_echo(text: str, prompt: str) -> bool:
    """True, если text — эхо нашего промпта, а не ответ модели."""
    if not text or not prompt:
        return False
    t_low = text.lower()
    m_count = sum(1 for m in _ECHO_MARKERS if m in t_low)
    if m_count >= 2:
        return True
    if len(prompt) > 200 and len(text) > 200:
        if text[:200].strip() == prompt[:200].strip():
            return True
    if len(prompt) > 800 and len(text) > 800:
        for start in (0, 1000, 3000, 6000, 10000, 15000, 20000):
            if start + 400 > len(prompt):
                break
            chunk = prompt[start:start + 400]
            if len(chunk) >= 300 and chunk in text:
                return True
    return False


# FIX QWEN-UI: маркеры UI-плашки Qwen Studio, которая появляется
# ПОСЛЕ ответа («Этот отзыв поможет... Какой ответ вы предпочитаете?
# Выберите один»). Это НЕ ответ модели, а UI-навязчивость.
# Если кандидат — эта плашка, отбрасываем его: иначе он выигрывает
# по длине у настоящего ответа (188 > 10 символов).
_QWEN_UI_MARKERS = (
    "этот отзыв поможет нам оценить",
    "какой ответ вы предпочитаете",
    "выберите один, чтобы продолжить",
    "оцените этот ответ",
    "thanks for your feedback",
    "which response do you prefer",
    "choose one to continue",
)

# FIX BUG-DOM-AUTO-V2 (2026-09-28): точное совпадение для
# коротких UI-плашек. НЕ подстрока — иначе «авто» отсеет
# «автоматически», «автозапуск» и т.п.
_QWEN_UI_EXACT = frozenset({
    "авто",
    "модель не выбрана",
    "пропустить",
    "добавить файлы",
    "редактировать",
    "инициализировать пустой",
})

# FIX QWEN-UI-DEDUP (2026-09-28): хэш плашки → время сохранения.
# Не даём сохранять одну и ту же диагностику каждые 0.5с.
_UI_DIAG_SEEN: dict = {}


def _is_qwen_ui_noise(text: str) -> bool:
    """True, если текст — UI-плашка Qwen Studio, а не ответ."""
    if not text:
        return False
    low = text.lower().strip()
    # FIX BUG-DOM-AUTO-V2: точное совпадение — «Авто»,
    # «Модель не выбрана» иначе ловятся как ответ.
    if low in _QWEN_UI_EXACT:
        return True
    for m in _QWEN_UI_MARKERS:
        if m in low:
            return True
    return False


_CHAT_LIMIT_MARKERS = (
    # Переполнение контекста
    "начните новый чат", "начать новый чат", "начни новый чат",
    "start a new chat", "start new chat",
    "чат переполнен", "контекст переполнен",
    "length limit reached", "context limit",
    "maximum context length", "context length exceeded",
    "достигнут лимит контекста",
    # Б75 (15.10.2026): DeepSeek иногда обрывает генерацию
    # с этим маркером. Считаем это чат-лимитом → reset_chat.
    "server is temporarily unavailable",
    # FIX #16: ошибки модели/сети (по реальным логам Qwen)
    "oops!",
    "internal error",
    "there was an issue connecting",
    "issue connecting to",
    "connection error",
    "network error",
    "failed to load",
    # CHAT-LIMIT-REASONING (2026-10-02): "problem with" убран —
    # слишком общий. Ловился в reasoning DeepSeek
    # ("problem with the visible code") → ложный chat_limit.
    # См. _detect_chat_limit — добавлена проверка reasoning.
)


def _has_copy_button(page, site_id: str = "") -> bool:
    """B+ (19.10.2026): есть ли Copy/Download у ПОСЛЕДНЕГО сообщения.

    Признак готового ответа. Не заменяет _is_generating,
    а дополняет: если Copy есть, ответ точно готов.
    """
    try:
        # Ищем в последнем message
        _last_msg = None
        for sel in ["[class*='message']", "[class*='assistant']"]:
            els = page.query_selector_all(sel)
            if els:
                _last_msg = els[-1]
                break
        if _last_msg is None:
            return False
        # Внутри последнего сообщения ищем Copy/Download
        _selectors = [
            "button[aria-label*='Copy' i]",
            "button[aria-label*='Download' i]",
            "button[title*='Copy' i]",
            "button[title*='Download' i]",
            "button[aria-label*='Копировать' i]",
            "button[aria-label*='Скачать' i]",
            "[class*='copy-button']",
            "[class*='download-button']",
            "[class*='copy-btn']",
            "[class*='download-btn']",
        ]
        for sel in _selectors:
            try:
                el = _last_msg.query_selector(sel)
                if el and el.is_visible():
                    return True
            except Exception:
                continue
        return False
    except Exception:
        return False


def _is_generating(page) -> bool:
    """B+ (19.10.2026): Forge-логика. Пока DeepSeek генерирует — true.

    Проблема (прогон 19.10): Link завершал research по стабильности
    «Thinking» (17 байт). А DeepSeek через 5-12 секунд дописывал
    JSON (939 байт). Link уже не смотрел.

    Forge решает это через _isGenerating: проверяет кнопку Stop
    и loader-индикаторы. Пока они видимы — генерация идёт, ответ
    не принимаем.

    Логика взята из другого браузерного приложения (deepseek-адаптер).
    """
    try:
        result = page.evaluate("""() => {
            const stopSelectors = [
                'button[aria-label*="Stop" i]',
                '[aria-label*="stop generating" i]',
                '[data-testid="stop-button"]',
                '[class*="stop-btn"]',
                '[class*="stopBtn"]',
                '[class*="stop-gen"]',
                '[class*="stopGen"]',
                '[class*="generating"]',
            ];
            for (const sel of stopSelectors) {
                const el = document.querySelector(sel);
                if (el) {
                    const s = window.getComputedStyle(el);
                    if (s.display !== 'none' && s.visibility !== 'hidden'
                        && s.opacity !== '0') return true;
                }
            }
            const loaderSelectors = [
                '[class*="typing"]',
                '[class*="loading"]',
                '[class*="spinner"]',
                '[class*="blink"]',
                '[class*="cursor"]',
                '[class*="pulsing"]',
                'svg[class*="loading"]',
                'svg[class*="spinner"]',
            ];
            for (const sel of loaderSelectors) {
                const el = document.querySelector(sel);
                if (el) {
                    const s = window.getComputedStyle(el);
                    if (s.display !== 'none' && s.visibility !== 'hidden') return true;
                }
            }
            return false;
        }""")
        return bool(result)
    except Exception:
        return False


def _is_reasoning_only(text: str) -> bool:
    """B+ (19.10.2026): текст — только reasoning-заглушка.

    «Thinking», «Thought for N seconds» — это НЕ финальный ответ.
    DeepSeek показывает reasoning, потом дописывает content.
    Пока видим только reasoning — ждать.

    ВАЖНО: проверка '"targets"' / 'STATUS:' — ДО префиксного
    матча. Иначе JSON, начинающийся с «The user wants...»,
    отфильтруется.
    """
    if not text:
        return True
    _t = text.strip()
    if not _t:
        return True
    # Если внутри уже есть признаки финального ответа — не reasoning.
    if '"targets"' in _t or "STATUS:" in _t.upper():
        return False
    _h = _t.lower()[:120]
    for m in ("thinking", "thought for", "we need", "let me ",
              "let's ", "i need to", "i should", "the user ",
              "нужно ", "мы должны"):
        if _h.startswith(m):
            return True
    return False


def _detect_chat_limit(text: str) -> bool:
    """CHAT-LIFECYCLE: True, если в тексте маркеры переполнения.

    CHAT-LIMIT-REASONING (2026-10-02): если текст НАЧИНАЕТСЯ
    с reasoning-маркера — это размышления модели, не ошибка.
    DeepSeek пишет "Thought for N seconds\n\nLet me...",
    внутри reasoning может встретиться "problem with" и т.п.
    Ложный chat_limit → reset чата → потеря контекста.
    """
    if not text:
        return False
    _head = text.strip().lower()[:200]
    _reasoning_markers = (
        "thought for",
        "thinking",
        "we need",
        "let me ",
        "let's ",
        "i need to",
        "i should",
        "the user ",
        "мы должны",
        "нужно ",
    )
    for _m in _reasoning_markers:
        if _head.startswith(_m):
            return False
    low = text.lower()
    return any(m in low for m in _CHAT_LIMIT_MARKERS)


def _debug_logs_enabled() -> bool:
    """Проверяет, включены ли отладочные логи (настройка debug_logs)."""
    try:
        from config.config_manager import get_setting
        return bool(get_setting("debug_logs", False))
    except Exception:
        return False


def _dbg_write(path: str, content: str) -> None:
    """Пишет в лог, только если включены debug_logs."""
    if not _debug_logs_enabled():
        return
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(content)
    except Exception:
        pass


def _move_window_offscreen(page) -> None:
    """Сдвигает окно Chromium за пределы экрана.

    Работает надёжнее, чем windowState=minimized: Chromium
    не «разворачивает» окно при кликах внутри страницы,
    потому что окно не свёрнуто, а просто за экраном.
    """
    try:
        from config.config_manager import get_setting
        if not get_setting("minimize_browser", True):
            return
    except Exception:
        pass
    try:
        _ctx = page.context
        _cdp = _ctx.new_cdp_session(page)
        _win = _cdp.send("Browser.getWindowForTarget")
        _wid = _win.get("windowId")
        if _wid is not None:
            _cdp.send("Browser.setWindowBounds", {
                "windowId": _wid,
                "bounds": {"left": -3000, "top": -3000},
            })
    except Exception:
        pass


class _BrowserWorker(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self._queue = queue.Queue()
        self._playwright = None
        self._contexts: Dict[str, Any] = {}   # site_id -> browser context
        self._pages: Dict[str, Any] = {}       # site_id -> page
        self._running = True

    def submit(self, site_id: str, prompt: str, timeout: int = 180) -> Dict[str, Any]:
        holder = {"event": threading.Event(), "data": None}
        req_id = id(holder)
        self._queue.put((req_id, site_id, prompt, timeout, holder))
        # PROGRESSIVE-RETRY (2026-10-02): общий таймаут = сумма всех
        # попыток + буфер 60 сек. Иначе submit прерывает retry до того,
        # как вторая попытка успевает завершиться.
        _total_timeout = sum(timeouts.TIMEOUTS) + 60
        if not holder["event"].wait(timeout=_total_timeout):
            return {"source": "browser", "kind": "error", "success": False, "text": "", "error": "Превышено время ожидания ответа от браузера", "metadata": {"site": site_id}}
        return holder["data"] or {"success": False, "text": "", "error": "Пустой ответ"}

    def shutdown(self):
        self._running = False
        self._queue.put((None, None, None, 0, None))

    def stop_and_join(self, timeout: float = 5.0):
        """Останавливает воркер и дожидается завершения потока."""
        self._running = False
        try:
            self._queue.put((None, None, None, 0, None))
        except Exception:
            pass
        if self.is_alive():
            try:
                self.join(timeout=timeout)
            except Exception:
                pass

    def run(self):
        while self._running:
            try:
                item = self._queue.get(timeout=1)
            except queue.Empty:
                continue
            if item[0] is None:
                break
            req_id, site_id, prompt, timeout, holder = item
            # FIX №4: специальный маркер — сброс чата.
            # Выполняется в ЭТОМ (worker) потоке, не в main.
            if prompt == "__RESET_CHAT__":
                try:
                    ok = self._reset_page(site_id)
                    result = {"success": bool(ok),
                              "text": "", "reset": True}
                except Exception as e:
                    result = {"success": False, "text": "",
                              "error": str(e)}
                holder["data"] = result
                holder["event"].set()
                continue
            try:
                result = self._do_query(site_id, prompt, timeout)
            except Exception as e:
                result = {"success": False, "text": "", "error": str(e)}
            holder["data"] = result
            holder["event"].set()
        self._close_browser()

    def _start_browser(self, site_id: str = None):
        """Запускает Playwright и контекст для указанного сайта."""
        from playwright.sync_api import sync_playwright

        if self._playwright is None:
            self._playwright = sync_playwright().start()

        # Если site_id не задан — используем общий профиль
        if site_id is None:
            site_id = "_default"

        if site_id in self._contexts:
            # Проверим, что контекст ещё жив — попробуем создать тестовую страницу
            _ctx_alive = False
            try:
                _test = self._contexts[site_id].new_page()
                _test.close()
                _ctx_alive = True
            except Exception:
                _ctx_alive = False
            if _ctx_alive:
                return  # контекст живой
            # Мёртвый — закрываем что можем, убираем и пересоздаём
            try:
                self._contexts[site_id].close()
            except Exception:
                pass
            self._contexts.pop(site_id, None)
            self._pages.pop(site_id, None)

        # Получаем profile_subdir для сайта
        sub = "_default"
        try:
            if site_id != "_default":
                from core.agents.browser_sites_manager import get_site
                s = get_site(site_id)
                if s:
                    sub = s.get("profile_subdir") or site_id + "_profile"
            profile_dir = Path.home() / ".ai_link" / "browser_profiles" / sub
        except Exception:
            profile_dir = BROWSER_PROFILE_DIR

        profile_dir.mkdir(parents=True, exist_ok=True)

        # Б77 (16.10.2026): если включён Xephyr — запускаем Chromium
        # внутри виртуального дисплея :99. Окно браузера не появляется
        # на основном экране и не перехватывает фокус.
        _launch_env = None
        try:
            from config.config_manager import get_setting as _get_setting
            _use_xephyr = _get_setting("use_xephyr", False)
        except Exception:
            _use_xephyr = False
        if _use_xephyr:
            try:
                from core import xephyr_manager as _xeph
                _disp = _xeph.ensure_running()
                if _disp:
                    _launch_env = {**os.environ, "DISPLAY": _disp}
                    logger.info("XEPHYR: Chromium запускается на %s", _disp)
                    _xeph.hide()
                else:
                    logger.warning("XEPHYR: не удалось запустить :99 — "
                                   "обычный режим")
            except Exception as _xe:
                logger.warning("XEPHYR: ошибка запуска: %s", _xe)

        try:
            _launch_kwargs = dict(
                user_data_dir=str(profile_dir),
                headless=False,
                # FIX 2026-09-26: args антидетекта (из Forge Agent).
                # Убирают баннер «Chromium управляется автоматизацией»
                # и маскируют признаки бота. Логику чтения НЕ трогаем.
                args=[
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-blink-features=AutomationControlled",
                    "--no-first-run",
                    "--disable-default-apps",
                ],
                ignore_default_args=["--enable-automation"],
            )
            if _launch_env is not None:
                _launch_kwargs["env"] = _launch_env
            ctx = self._playwright.chromium.launch_persistent_context(
                **_launch_kwargs,
            )
        except Exception as e:
            raise RuntimeError(f"launch_failed: {e}")
        self._contexts[site_id] = ctx

    def _close_browser(self):
        for ctx in list(self._contexts.values()):
            try:
                ctx.close()
            except Exception:
                pass
        self._contexts = {}
        try:
            if self._playwright:
                self._playwright.stop()
        except Exception:
            pass
        self._playwright = None

    def _get_page(self, site_id: str, url: str):
        # Убеждаемся, что контекст для этого сайта запущен
        if site_id not in self._contexts:
            self._start_browser(site_id)
        ctx = self._contexts[site_id]

        page = self._pages.get(site_id)
        if page is not None:
            try:
                if not page.is_closed():
                    host = url.split('/')[2]
                    if host in page.url:
                        return page
            except Exception:
                pass
            # Закрытая или мёртвая
            try:
                page.close()
            except Exception:
                pass
            self._pages.pop(site_id, None)

        # TABS-FIX (2026-10-12): закрыть лишние about:blank
        # перед созданием новой.
        try:
            self._cleanup_blank_tabs(site_id)
        except Exception:
            pass

        try:
            page = ctx.new_page()
        except Exception as e:
            raise RuntimeError(f"context_dead: {e}")
        try:
            page.goto(url, timeout=240000)
        except Exception as e:
            # TABS-FIX: goto упал — закрыть вкладку, не оставлять мусор.
            try:
                page.close()
            except Exception:
                pass
            raise RuntimeError(f"goto_failed: {e}")
        try:
            page.wait_for_load_state("domcontentloaded", timeout=240000)
        except Exception:
            pass
        try:
            page.wait_for_load_state("networkidle", timeout=60000)
        except Exception:
            pass
        self._pages[site_id] = page
        return page

    def _cleanup_blank_tabs(self, site_id: str) -> int:
        """TABS-FIX (2026-10-12): закрывает лишние about:blank
        вкладки в контексте сайта. Оставляет только рабочую
        (self._pages[site_id]).

        Возвращает количество закрытых.
        """
        if site_id not in self._contexts:
            return 0
        ctx = self._contexts[site_id]
        keep = self._pages.get(site_id)
        closed = 0
        try:
            for p in list(ctx.pages):
                try:
                    if p is keep:
                        continue
                    if p.is_closed():
                        continue
                    url = (p.url or "").strip()
                    if url in ("about:blank", "", "about:newtab"):
                        p.close()
                        closed += 1
                except Exception:
                    continue
        except Exception:
            pass
        if closed:
            logger.info(
                "TABS-FIX: закрыто %d about:blank вкладок (%s)",
                closed, site_id,
            )
        return closed

    def _reset_page(self, site_id: str) -> bool:
        """CHAT-LIFECYCLE: закрывает текущую страницу сайта
        и открывает заново корневой URL (свежий чат).

        Возвращает True, если reset прошёл.
        """
        from core.agents.browser_sites_manager import get_site
        site = get_site(site_id)
        if not site:
            return False
        url = site.get("url", "")
        if not url:
            return False

        # Закрываем текущую страницу
        page = self._pages.pop(site_id, None)
        if page is not None:
            try:
                page.close()
            except Exception:
                pass

        # Убеждаемся, что контекст жив
        if site_id not in self._contexts:
            try:
                self._start_browser(site_id)
            except Exception as e:
                logger.warning(
                    "CHAT-LIFECYCLE: не удалось запустить браузер: %s", e
                )
                return False

        # Открываем свежую страницу
        ctx = self._contexts[site_id]

        # TABS-FIX (2026-10-12): закрыть лишние about:blank.
        try:
            self._cleanup_blank_tabs(site_id)
        except Exception:
            pass

        try:
            new_page = ctx.new_page()
        except Exception as e:
            logger.warning(
                "CHAT-LIFECYCLE: не удалось создать страницу: %s", e
            )
            return False
        try:
            new_page.goto(url, timeout=240000)
        except Exception as e:
            # TABS-FIX: goto упал — закрыть вкладку, не оставлять мусор.
            try:
                new_page.close()
            except Exception:
                pass
            logger.warning(
                "CHAT-LIFECYCLE: goto %s не удался: %s", url, e
            )
            return False

        # FIX BUG-DOM-AUTO: после reset_chat DOM пустой (React ещё
        # не отрисовал старые сообщения). Без ожидания initial_hashes
        # собирается с 0 элементов, и UI-плашка «Авто» становится
        # «новым» кандидатом → выигрывает у реального ответа.
        # Ждём стабилизации числа элементов (2 раунда подряд).
        try:
            _prev = -1
            _stable = 0
            for _ in range(15):
                time.sleep(1)
                try:
                    _cur = len(new_page.query_selector_all(
                        ".chat-message-assistant, "
                        "[class*='message'], "
                        "[class*='assistant'], "
                        "[class*='markdown-body']"
                    ))
                except Exception:
                    _cur = 0
                if _cur == _prev and _cur > 0:
                    _stable += 1
                    if _stable >= 2:
                        break
                else:
                    _stable = 0
                _prev = _cur
        except Exception:
            pass

        self._pages[site_id] = new_page
        return True

    def _do_query(self, site_id: str, prompt: str,
                  timeout: int) -> Dict[str, Any]:
        """CHAT-LIFECYCLE: запрос с автоповтором и reset_chat.

        Логика: если запрос не удался по «временной» причине
        (response_timeout / chat_limit / context_dead /
        input_not_found / send_failed) — 3 попытки с reset_chat
        между ними. Это надёжнее детектора по тексту — работает
        и при переполнении чата, и при зависании, и при
        потере соединения.
        """
        # PROGRESSIVE-RETRY (2026-10-02): таймауты по попыткам из
        # config/timeouts.py. Первая — короткая (60с), последняя —
        # длинная (1800с, на случай "свет отключили").
        _timeouts = timeouts.TIMEOUTS
        MAX_ATTEMPTS = len(_timeouts)
        RETRYABLE_REASONS = (
            "response_timeout",
            "chat_limit",
            "context_dead",
            "input_not_found",
            "send_failed",
            "agent_error",
        )
        last_result = None

        for attempt in range(1, MAX_ATTEMPTS + 1):
            _current_timeout = _timeouts[attempt - 1]
            logger.info(
                "PROGRESSIVE-RETRY: попытка %d/%d с таймаутом %d сек",
                attempt, MAX_ATTEMPTS, _current_timeout,
            )
            result = self._do_query_once(site_id, prompt,
                                          _current_timeout)
            if result.get("success"):
                if attempt > 1:
                    logger.info(
                        "CHAT-LIFECYCLE: успех с попытки %d/%d",
                        attempt, MAX_ATTEMPTS,
                    )
                return result

            reason = result.get("reason", "")
            last_result = result

            # Стоит ли повторять?
            if reason not in RETRYABLE_REASONS:
                # login_required / site_not_found / launch_failed —
                # повтор не поможет.
                return result

            if attempt < MAX_ATTEMPTS:
                logger.warning(
                    "CHAT-LIFECYCLE: попытка %d/%d для %s "
                    "не удалась (reason=%s) → reset_chat + повтор",
                    attempt, MAX_ATTEMPTS, site_id, reason,
                )
                try:
                    self._reset_page(site_id)
                except Exception as e:
                    logger.warning(
                        "CHAT-LIFECYCLE: reset_page не удался: %s", e,
                    )
                time.sleep(2)

        # Все попытки исчерпаны — сообщаем «застрял».
        _last_err = (last_result or {}).get("error", "unknown")
        logger.error(
            "CHAT-LIFECYCLE: %s не отвечает после %d попыток "
            "(последняя ошибка: %s)",
            site_id, MAX_ATTEMPTS, _last_err[:200],
        )
        return {
            "source": "browser",
            "kind": "error",
            "success": False,
            "reason": "chat_stuck",
            "text": "",
            "error": (
                f"Чат {site_id} не отвечает после {MAX_ATTEMPTS} "
                f"попыток с перезапуском. Возможно, браузер завис "
                f"или нужна ручная проверка (кнопка «Показать "
                f"браузер»). Последняя ошибка: {_last_err[:200]}"
            ),
            "metadata": {"site": site_id, "attempts": MAX_ATTEMPTS},
        }

    def _do_query_once(self, site_id: str, prompt: str,
                       timeout: int) -> Dict[str, Any]:
        """CHAT-LIFECYCLE: одна попытка запроса.

        Обёртка _do_query делает 3 попытки с reset_chat между ними.
        """
        site = get_site(site_id)
        if not site:
            return {"source": "browser", "kind": "error", "success": False, "reason": "site_not_found", "text": "", "error": f"Сайт '{site_id}' не найден", "metadata": {"site": site_id}}
        if not site.get("enabled", True):
            return {"source": "browser", "kind": "error", "success": False, "reason": "site_disabled", "text": "", "error": f"Сайт '{site_id}' отключён", "metadata": {"site": site_id}}

        from core.agents.browser_calibrator import RESPONSE_CANDIDATES
        from core.agents.browser_sites_manager import update_site

        try:
            self._start_browser(site_id)
        except Exception as e:
            return {
                "source": "browser",
                "kind": "error",
                "success": False,
                "reason": "launch_failed",
                "text": "",
                "error": f"Не удалось запустить браузер: {e}",
                "metadata": {"site": site_id},
            }
        url = site["url"]
        try:
            page = self._get_page(site_id, url)
        except Exception as e:
            # Первый провал — попробуем пересоздать контекст с нуля (один раз)
            try:
                self._contexts.pop(site_id, None)
                self._pages.pop(site_id, None)
                self._start_browser(site_id)
                page = self._get_page(site_id, url)
            except Exception as e2:
                return {
                    "source": "browser",
                    "kind": "error",
                    "success": False,
                    "reason": "launch_failed",
                    "text": "",
                    "error": f"Не удалось открыть страницу: {e2}",
                    "metadata": {"site": site_id},
                }

        # Проверка на форму логина или капчу
        try:
            _has_password = len(page.query_selector_all("input[type='password']")) > 0
            _has_captcha = (
                len(page.query_selector_all("[class*='captcha']")) > 0
                or len(page.query_selector_all("iframe[src*='challenge']")) > 0
                or len(page.query_selector_all("iframe[src*='turnstile']")) > 0
            )
            _url_now = page.url or ""
            _login_url = ("/login" in _url_now) or ("/signin" in _url_now) or ("/auth" in _url_now)
            if _has_password or _has_captcha or _login_url:
                return {
                    "source": "browser",
                    "kind": "error",
                    "success": False,
                    "reason": "login_required",
                    "text": "",
                    "error": f"Требуется вход или капча. URL={_url_now}",
                    "metadata": {"site": site_id, "url": _url_now},
                }
        except Exception:
            pass

        # Уводим окно Chromium за пределы экрана
        # (вместо минимизации — надёжнее).
        _move_window_offscreen(page)

        input_selector = site.get("input_selector", "textarea")
        submit_key = site.get("submit_key", "Enter")

        # Собираем список селекторов: основной + все кандидаты
        selectors = []
        primary = site.get("response_selector", "")
        if primary:
            selectors.append(primary)
        for sel in RESPONSE_CANDIDATES:
            if sel not in selectors:
                selectors.append(sel)

        # Запоминаем количество существующих элементов и хэш текста последнего по каждому селектору
        import hashlib as _hl
        initial = {}
        initial_last_hash = {}
        initial_hashes = {}   # set хэшей ВСЕХ начальных элементов
        for sel in selectors:
            try:
                _els = page.query_selector_all(sel)
                initial[sel] = len(_els)
                _hs = set()
                for _el in _els:
                    try:
                        _t = _el.inner_text() or ""
                        _hs.add(_hl.md5(_t.encode("utf-8", "ignore")).hexdigest())
                    except Exception:
                        pass
                initial_hashes[sel] = _hs
                if _els:
                    _t = _els[-1].inner_text() or ""
                    initial_last_hash[sel] = _hl.md5(_t.encode("utf-8", "ignore")).hexdigest()
                else:
                    initial_last_hash[sel] = None
            except Exception:
                initial[sel] = 0
                initial_last_hash[sel] = None
                initial_hashes[sel] = set()

        try:
            if not _debug_logs_enabled():
                raise RuntimeError("debug_logs disabled")
            import os as _os
            _log_dir = str(Path.home() / ".ai_link" / "diagnostics" / "browser_polls")
            _os.makedirs(_log_dir, exist_ok=True)
            _line = f"\n{time.time():.3f} === START _do_query ===\n"
            _line += f"  site={site_id}\n"
            try:
                _line += f"  url={page.url}\n"
            except Exception as _e:
                _line += f"  url=ERROR:{_e}\n"
            for _sel in selectors[:5]:
                _line += f"  initial[{_sel}]={initial.get(_sel, 0)}\n"
            _line += f"  prompt_len={len(prompt)}\n"
            _dbg_write(_os.path.join(_log_dir, f"start_{site_id}.log"), _line)
        except Exception:
            pass

        # Вводим промпт
        # AW: retry с reload и нарастающими паузами
        _RETRY_DELAYS = [10, 60, 360, 900]
        _input_found = False
        for _attempt in range(len(_RETRY_DELAYS) + 1):
            try:
                page.wait_for_selector(input_selector, timeout=120000)
                _input_found = True
                break
            except Exception as _e:
                if _attempt < len(_RETRY_DELAYS):
                    _delay = _RETRY_DELAYS[_attempt]
                    logger.warning(
                        "browser: textarea не найдена (попытка %d/%d), "
                        "reload + пауза %ds",
                        _attempt + 1, len(_RETRY_DELAYS) + 1, _delay,
                    )
                    try:
                        page.reload(timeout=60000)
                    except Exception:
                        pass
                    time.sleep(_delay)
                else:
                    return {
                        "source": "browser",
                        "kind": "error",
                        "success": False,
                        "reason": "input_not_found",
                        "text": "",
                        "error": (
                            f"Поле ввода не найдено после "
                            f"{len(_RETRY_DELAYS) + 1} попыток "
                            f"(общее ожидание ~22 мин)"
                        ),
                        "metadata": {"site": site_id},
                    }

        if not _input_found:
            return {
                "source": "browser", "kind": "error", "success": False,
                "reason": "input_not_found", "text": "",
                "error": "textarea не найдена",
                "metadata": {"site": site_id},
            }

        try:
            input_el = page.query_selector(input_selector)
            if input_el is None:
                return {"source": "browser", "kind": "error", "success": False, "reason": "input_not_found", "text": "", "error": f"Поле ввода не найдено: {input_selector}", "metadata": {"site": site_id}}
            input_el.click()
            import os as _os
            _ld = str(Path.home() / ".ai_link" / "diagnostics" / "browser_polls")
            if _debug_logs_enabled():
                try:
                    _os.makedirs(_ld, exist_ok=True)
                    _l = f"{time.time():.3f} CLICKED prompt_len={len(prompt)}\n"
                    _dbg_write(_os.path.join(_ld, f"sent_{site_id}.log"), _l)
                except Exception:
                    pass
            input_el.fill(prompt)
            if _debug_logs_enabled():
                try:
                    _ta = 0
                    try:
                        _ta = len(input_el.input_value() or "")
                    except Exception:
                        pass
                    _l = f"{time.time():.3f} FILLED prompt_len={len(prompt)} textarea_value_len={_ta}\n"
                    _dbg_write(_os.path.join(_ld, f"sent_{site_id}.log"), _l)
                except Exception:
                    pass
            time.sleep(0.5)
            page.keyboard.press(submit_key)
            if _debug_logs_enabled():
                try:
                    _ta = 0
                    try:
                        _ta = len(input_el.input_value() or "")
                    except Exception:
                        pass
                    _l = f"{time.time():.3f} PRESSED prompt_len={len(prompt)} textarea_value_len={_ta}\n"
                    _dbg_write(_os.path.join(_ld, f"sent_{site_id}.log"), _l)
                except Exception:
                    pass
            # Баг А: чистим поле ввода после отправки.
            # На DeepSeek и Qwen после press поле остаётся
            # заполненным, и следующий запрос уйдёт поверх старого.
            try:
                _el = page.query_selector(input_selector)
                if _el is not None:
                    _el.fill("")
            except Exception:
                pass
        except Exception as e:
            return {"source": "browser", "kind": "error", "success": False, "reason": "send_failed", "text": "", "error": f"Не удалось отправить промпт: {e}", "metadata": {"site": site_id}}

        # PROGRESSIVE-RETRY (2026-10-02): доверяем таймауту,
        # переданному вызывающим (из TIMEOUTS). Раньше был жёсткий
        # минимум 360 сек — он ломал прогрессивную схему.
        wait_timeout = timeout
        start = time.time()
        stable_text = None
        stable_since = None
        best_selector = None

        _dbg_enabled = _debug_logs_enabled()
        _last_window_move = time.time()
        while time.time() - start < wait_timeout:
            # Раз в 10 секунд повторно сдвигаем окно за экран,
            # если Chromium сам его вернул (при обновлении DOM).
            if time.time() - _last_window_move > 10:
                _move_window_offscreen(page)
                _last_window_move = time.time()
            if _dbg_enabled:
                try:
                    _bl = 0
                    try:
                        _bl = len(page.inner_text("body"))
                    except Exception:
                        pass
                    _cnts = []
                    for _s in selectors[:6]:
                        try:
                            _cnts.append(f"{_s[:25]}={len(page.query_selector_all(_s))}")
                        except Exception:
                            _cnts.append(f"{_s[:25]}=ERR")
                    _ta = 0
                    try:
                        _ta = len(input_el.input_value() or "")
                    except Exception:
                        pass
                    _l = f"{time.time():.3f} CYCLE body={_bl} ta={_ta} " + " ".join(_cnts) + "\n"
                    _dbg_write(_os.path.join(_ld, f"cycle_{site_id}.log"), _l)
                except Exception:
                    pass
            for sel in selectors:
                try:
                    elements = page.query_selector_all(sel)
                except Exception:
                    continue

                # Устойчивый поиск: собираем ВСЕ новые элементы,
                # отсеиваем эхо промпта, берём самый длинный.
                _new_candidates = []
                _p_norm = (prompt or "").strip()
                _p_head = _p_norm[:60] if _p_norm else ""
                for _el in elements:
                    try:
                        _t = _el.inner_text() or ""
                        _t = _t.strip()
                        # Ответ может быть и односимвольным ("4", "A").
                        # Отсеиваем только совсем пустое.
                        if not _t:
                            continue
                        # B+ (19.10.2026): «Thinking» / «Thought for N»
                        # — это reasoning, НЕ ответ. DeepSeek сначала
                        # показывает reasoning, потом дописывает JSON.
                        # Не считаем reasoning кандидатом.
                        if _is_reasoning_only(_t):
                            continue
                        # Отсев эха промпта
                        if _p_norm and (_t == _p_norm or
                                        (_p_head and _t.startswith(_p_head))):
                            continue
                        _h = _hl.md5(_t.encode("utf-8", "ignore")).hexdigest()
                        # FIX STATUS-HASH: ответ Qwen с STATUS: НЕ
                        # сверяем с initial_hashes. Qwen часто отвечает
                        # ИДЕНТИЧНО на разных review (тот же "STATUS: OK").
                        # Хеш совпадает → кандидат отбрасывается → 390с
                        # висим впустую. Review идемпотентен — можно
                        # обрабатывать повторно.
                        _is_status = "STATUS:" in _t.upper()
                        if not _is_status and _h in initial_hashes.get(sel, set()):
                            continue
                        # FIX QWEN-UI: отбрасываем UI-плашку
                        # («Этот отзыв поможет...», «Выберите один»),
                        # иначе она выиграет по длине у ответа.
                        if _is_qwen_ui_noise(_t):
                            continue
                        # FIX ECHO: отбрасываем НАШ промпт в DOM.
                        if _is_prompt_echo(_t, prompt):
                            continue
                        _new_candidates.append((_t, _h, _el))
                    except Exception:
                        continue

                best_el = None
                best_len = 0
                if _new_candidates:
                    # FIX STATUS-PRIORITY: если в DOM есть ответ со
                    # маркером "STATUS:" — это review-фаза, берём его
                    # независимо от длины. Reasoning Qwen бывает
                    # длиннее реального "STATUS: OK".
                    # Для plan/code STATUS: не встречается — работает
                    # старый max(len) как раньше.
                    _status_cands = [
                        c for c in _new_candidates
                        if "STATUS:" in c[0].upper()
                    ]
                    if _status_cands:
                        # FIX OK-PRIORITY: если Qwen дал dual-response
                        # (два ответа: STATUS: OK и STATUS: PROBLEMS) —
                        # берём OK. Иначе fix зацикливается бесконечно,
                        # потому что PROBLEMS длиннее (205 vs 32 симв).
                        # Если Qwen ТОЧНО нашёл проблемы — OK не будет
                        # ни в одном ответе.
                        _ok_cands = [
                            c for c in _status_cands
                            if "STATUS: OK" in c[0].upper()
                            or "STATUS:OK" in c[0].upper()
                        ]
                        if _ok_cands:
                            best_text, best_hash, best_el = max(
                                _ok_cands, key=lambda x: len(x[0])
                            )
                        else:
                            best_text, best_hash, best_el = max(
                                _status_cands, key=lambda x: len(x[0])
                            )
                    else:
                        # Старая логика: max(len) для plan/code/etc
                        best_text, best_hash, best_el = max(
                            _new_candidates, key=lambda x: len(x[0])
                        )
                    best_len = len(best_text)
                elif elements:
                    # FIX QWEN-UI: если после фильтра пусто, но
                    # в DOM была UI-плашка — сохраним её для разбора.
                    try:
                        for _el_d in elements:
                            try:
                                _td = (_el_d.inner_text() or "").strip()
                            except Exception:
                                continue
                            if _is_qwen_ui_noise(_td):
                                # FIX QWEN-UI-DEDUP: не сохраняем
                                # одну и ту же плашку каждые 0.5с
                                _h_diag = _hl.md5(
                                    _td.encode("utf-8", "ignore")
                                ).hexdigest()
                                _now_diag = time.time()
                                for _k in list(_UI_DIAG_SEEN.keys()):
                                    if _now_diag - _UI_DIAG_SEEN.get(_k, 0) > 60:
                                        del _UI_DIAG_SEEN[_k]
                                if _h_diag in _UI_DIAG_SEEN:
                                    break
                                _UI_DIAG_SEEN[_h_diag] = _now_diag
                                _diag = Path(
                                    str(Path.home() / ".ai_link" / "diagnostics" / "qwen_ui")
                                )
                                _diag.mkdir(parents=True, exist_ok=True)
                                _ts = time.strftime("%Y%m%d_%H%M%S")
                                try:
                                    (_diag / f"{_ts}_ui.html").write_text(
                                        _el_d.inner_html(),
                                        encoding="utf-8",
                                    )
                                except Exception:
                                    pass
                                try:
                                    (_diag / f"{_ts}_ui.txt").write_text(
                                        _td, encoding="utf-8",
                                    )
                                except Exception:
                                    pass
                                try:
                                    page.screenshot(
                                        path=str(_diag / f"{_ts}_ui.png"),
                                        full_page=False,
                                    )
                                except Exception:
                                    pass
                                logger.info(
                                    "QWEN-UI: сохранена диагностика %s",
                                    _ts,
                                )
                                break
                    except Exception:
                        pass

                # Диагностика в poll — даже если ответа ещё нет
                if _dbg_enabled:
                    try:
                        _log_dir = str(Path.home() / ".ai_link" / "diagnostics" / "browser_polls")
                        _os.makedirs(_log_dir, exist_ok=True)
                        _n_total = len(elements)
                        _n_new = len(_new_candidates)
                        _bl = best_len
                        _head = (best_text[:60] if best_el is not None
                                 else "")
                        _line = (
                            f"{time.time():.3f} sel={sel[:30]} "
                            f"total={_n_total} new={_n_new} "
                            f"best_len={_bl} head={_head!r}\n"
                        )
                        _dbg_write(_os.path.join(_log_dir,
                                                 f"poll_{site_id}.log"),
                                   _line)
                    except Exception:
                        pass

                if best_el is None:
                    continue
                _has_new = True
                if _has_new:
                    try:
                        el = best_el
                        # Приоритет 1: tool_call JSON в pre-блоках.
                        # DETECT2: ищем ТОЛЬКО pre code (не инлайн code).
                        # Инлайн <code>.txt</code> в тексте ответа —
                        # это упоминание файла, не tool_call. Берём
                        # только блоки с JSON-ключами и достаточной длиной.
                        code_text = None
                        try:
                            best_candidate = None
                            for code_sel in [
                                "pre code.language-tool_call",
                                "pre code",
                            ]:
                                code_els = el.query_selector_all(code_sel)
                                for c in code_els:
                                    try:
                                        tc = c.evaluate("node => node.textContent")
                                        if not tc:
                                            continue
                                        tc = tc.strip()
                                        # DETECT2: минимум 20 символов —
                                        # отсекаем .txt / .md / OK.
                                        if len(tc) < 20:
                                            continue
                                        if ('"name"' in tc
                                                or '"tool"' in tc
                                                or '"action"' in tc
                                                or 'write_file' in tc
                                                or 'create_file' in tc):
                                            best_candidate = tc
                                            break
                                    except Exception:
                                        pass
                                if best_candidate:
                                    break
                            # Если нашли JSON с действием — используем.
                            # НЕТ fallback «самый длинный code-блок»:
                            # без tool_call JSON берём обычный inner_text.
                            if best_candidate:
                                code_text = best_candidate
                        except Exception:
                            code_text = None

                        # Приоритет 2: обычный inner_text как fallback
                        if code_text:
                            # Убираем мусор "json\nCopy\nDownload\n" и прочие заголовки
                            import re as _re
                            code_text = _re.sub(r'^(json|javascript|python|tool_call|bash)\s*\n', '', code_text)
                            code_text = _re.sub(r'^(Copy|Download)\s*\n', '', code_text, flags=_re.MULTILINE)
                            code_text = code_text.strip()
                            text = code_text
                        else:
                            text = el.inner_text().strip()
                    except Exception:
                        continue

                    # Защита от эхо: не возвращаем наш же prompt
                    # как ответ модели. Если извлечённый текст
                    # совпадает с отправленным промптом (или
                    # начинается с него) — это user-сообщение,
                    # а не ответ ассистента. Пропускаем.
                    if text and prompt:
                        _t_norm = text.strip()
                        _p_norm = prompt.strip()
                        if _t_norm and _p_norm:
                            if _t_norm == _p_norm:
                                continue
                            _p_head = _p_norm[:60]
                            if _p_head and _t_norm.startswith(_p_head):
                                continue

                    if text:
                        if _dbg_enabled:
                            try:
                                import hashlib as _h
                                _log_dir = str(Path.home() / ".ai_link" / "diagnostics" / "browser_polls")
                                _os.makedirs(_log_dir, exist_ok=True)
                                _h_hash = _h.md5(text.encode("utf-8", "ignore")).hexdigest()[:8]
                                _line = (
                                    f"{time.time():.3f} site={site_id} sel={sel} "
                                    f"len={len(text)} hash={_h_hash} "
                                    f"src={'code' if code_text else 'inner'} "
                                    f"head={text[:80]!r}\n"
                                )
                                _dbg_write(_os.path.join(_log_dir, f"poll_{site_id}.log"), _line)
                            except Exception:
                                pass
                        if text == stable_text:
                            if stable_since and (time.time() - stable_since) > 6:
                                # B+ (19.10.2026): Forge-логика. Пока
                                # DeepSeek генерирует (Stop / loader
                                # видимы) — не берём ответ. Даже если
                                # текст стабилен. «Thinking» стабилен,
                                # но генерация идёт — ждём JSON.
                                try:
                                    _gen = _is_generating(page)
                                except Exception:
                                    _gen = False
                                # B+ (19.10.2026): Copy/Download —
                                # признак готового ответа. Если есть —
                                # берём, даже если _is_generating True.
                                _has_copy = False
                                if _gen:
                                    try:
                                        _has_copy = _has_copy_button(
                                            page, site_id
                                        )
                                    except Exception:
                                        _has_copy = False
                                if _gen and not _has_copy:
                                    logger.debug(
                                        "GENERATING: ещё пишет, "
                                        "сброс стабильности (head=%r)",
                                        text[:60],
                                    )
                                    stable_since = None
                                    continue
                                if _has_copy:
                                    logger.info(
                                        "COPY-BUTTON: ответ готов "
                                        "(gen=%s), берём", _gen,
                                    )
                                # CHAT-LIFECYCLE: детектор переполнения
                                if _detect_chat_limit(text):
                                    logger.warning(
                                        "CHAT-LIFECYCLE: маркер "
                                        "переполнения чата обнаружен "
                                        "(len=%d, head=%r)",
                                        len(text), text[:100],
                                    )
                                    return {
                                        "source": "browser",
                                        "kind": "error",
                                        "success": False,
                                        "reason": "chat_limit",
                                        "text": text,
                                        "error": (
                                            "Чат переполнен — нужен "
                                            "новый чат"
                                        ),
                                        "metadata": {"site": site_id},
                                    }
                                # Нашли стабильный ответ
                                best_selector = sel
                                # Авто-калибровка ОТКЛЮЧЕНА (задача G).
                                # Она подменяла правильный селектор
                                # (.chat-message-assistant) на более
                                # общий из RESPONSE_CANDIDATES
                                # ([class*='message-content']), что
                                # приводило к захвату user-сообщений.
                                # try:
                                #     update_site(site_id, {
                                #         "response_selector": sel,
                                #         "wait_selector": sel,
                                #     })
                                # except Exception:
                                #     pass
                                _ = update_site  # заглушка, чтобы не было warning

                                # === ДИАГНОСТИКА ===
                                try:
                                    if not _dbg_enabled:
                                        raise RuntimeError("debug_logs disabled")
                                    from pathlib import Path as _P
                                    diag_dir = _P(Path.home() / ".ai_link" / "diagnostics" / "deepseek")
                                    diag_dir.mkdir(parents=True, exist_ok=True)
                                    import datetime as _dt
                                    ts = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")

                                    # 1. Сырой HTML последнего элемента
                                    try:
                                        target_el = elements[-1]
                                        html_content = target_el.inner_html()
                                        (diag_dir / f"{ts}_raw.html").write_text(html_content, encoding="utf-8")
                                    except Exception as _e:
                                        (diag_dir / f"{ts}_raw.html").write_text(f"ERROR: {_e}", encoding="utf-8")

                                    # 2. Извлечённый inner_text
                                    (diag_dir / f"{ts}_inner_text.txt").write_text(text, encoding="utf-8")

                                    # 3. Блоки pre/code отдельно
                                    try:
                                        code_blocks = []
                                        for pre in page.query_selector_all("pre"):
                                            pre_text = pre.inner_text()
                                            code_blocks.append(pre_text)
                                        (diag_dir / f"{ts}_code_blocks.txt").write_text(
                                            "\n---\n".join(code_blocks), encoding="utf-8"
                                        )
                                    except Exception as _e:
                                        (diag_dir / f"{ts}_code_blocks.txt").write_text(f"ERROR: {_e}", encoding="utf-8")

                                    # 4. Селектор и site_id
                                    (diag_dir / f"{ts}_meta.txt").write_text(
                                        f"site_id: {site_id}\nselector: {sel}\n",
                                        encoding="utf-8"
                                    )
                                except Exception:
                                    pass
                                # === /ДИАГНОСТИКА ===

                                # Задача B: рабочий запрос успешен —
                                # синхронизируем статус прогрева в UI.
                                # Ленивый импорт: browser_warmup импортирует
                                # browser_agent, который импортирует
                                # browser_manager. Импорт на уровне модуля
                                # дал бы цикл. Внутри функции — безопасно.
                                try:
                                    from core.browser_warmup import (
                                        _set_status as _ws_set, ST_READY as _WS_READY,
                                    )
                                    _ws_set(site_id, _WS_READY, "готов (рабочий запрос)")
                                except Exception:
                                    pass
                                return {"source": "browser", "kind": "model_response", "success": True, "text": text, "error": None, "metadata": {"site": site_id}}
                        else:
                            stable_text = text
                            stable_since = time.time()
                        break
            time.sleep(2)

        return {
            "source": "browser",
            "kind": "error",
            "success": False,
            "reason": "response_timeout",
            "text": "",
            "error": "Ответ не получен в течение таймаута. Возможно, селектор ответа неверен или требуется вход."
        }


_manager: Optional[_BrowserWorker] = None
_manager_lock = threading.Lock()


def get_browser_manager() -> _BrowserWorker:
    global _manager
    with _manager_lock:
        if _manager is None or not _manager.is_alive():
            _manager = _BrowserWorker()
            _manager.start()
        return _manager


def query_browser_ai(site_id: str, prompt: str, headless: bool = False) -> Dict[str, Any]:
    """Отправляет запрос на сайт. Браузер остаётся открытым между запросами."""
    return get_browser_manager().submit(site_id, prompt)


def reset_chat(site_id: str) -> bool:
    """CHAT-LIFECYCLE: сбрасывает чат сайта (свежий URL).

    Используется при переполнении чата, смене этапа PHASES,
    или по кнопке «Новый чат» в UI.

    FIX №4: вызывается через очередь воркера (worker thread),
    а не напрямую из main — иначе greenlet.error.
    """
    global _manager
    with _manager_lock:
        if _manager is None or not _manager.is_alive():
            # Нечего сбрасывать — браузер не запущен
            return False
        try:
            r = _manager.submit(site_id, "__RESET_CHAT__", timeout=30)
            return bool(r.get("success"))
        except Exception as e:
            logger.warning("reset_chat: ошибка для %s: %s", site_id, e)
            return False


def close_browser():
    """Закрывает браузер (вызывать при выходе из приложения)."""
    global _manager
    with _manager_lock:
        if _manager is not None:
            try:
                _manager.stop_and_join(timeout=5.0)
            except Exception:
                pass
            _manager = None


def restore_browser_window() -> None:
    """Возвращает окна Chromium на экран.

    Вызывается из UI (например, по кнопке «Показать браузер»),
    чтобы посмотреть, что происходит, или ввести капчу.
    """
    global _manager
    if _manager is None:
        return
    for _site_id, page in list(_manager._pages.items()):
        try:
            if page is None or page.is_closed():
                continue
            _ctx = page.context
            _cdp = _ctx.new_cdp_session(page)
            _win = _cdp.send("Browser.getWindowForTarget")
            _wid = _win.get("windowId")
            if _wid is not None:
                _cdp.send("Browser.setWindowBounds", {
                    "windowId": _wid,
                    "bounds": {"left": 100, "top": 100},
                })
        except Exception:
            pass


import atexit as _atexit
_atexit.register(close_browser)
