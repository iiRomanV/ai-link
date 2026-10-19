"""
Автокалибровка селекторов для Browser Agent.
Перебирает варианты селекторов и сохраняет рабочие в browser_sites.json.
"""

import time
from pathlib import Path
from typing import Dict, Any, List, Optional

from core.agents.browser_sites_manager import get_site, update_site

# Кандидаты селекторов для поля ввода
INPUT_CANDIDATES = [
    "textarea[placeholder*='message']",
    "textarea[placeholder*='Message']",
    "textarea[placeholder*='сообщение']",
    "textarea[placeholder*='Ask']",
    "textarea[placeholder*='ask']",
    "textarea[placeholder*='вопрос']",
    "textarea",
    "div[contenteditable='true']",
    "[contenteditable='true']",
]

# Кандидаты селекторов для ответа
RESPONSE_CANDIDATES = [
    ".markdown-body",
    "[class*='ds-']",
    "[class*='assistant']",
    "[class*='bot']",
    "[class*='answer']",
    "[class*='response']",
    "[class*='message']",
    "[class*='markdown']",
    ".markdown",
    ".prose",
    ".response-content",
    ".chat-message",
    "[class*='markdown']",
    "[class*='message-content']",
    "[class*='response']",
    "[class*='answer']",
    "[class*='prose']",
    "article",
    "main",
]

BROWSER_PROFILE_DIR = Path.home() / ".ai_link" / "browser_profiles" / "ai_link_browser"


def _start_browser(site_id: str = "_default", headless: bool = False):
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return None, None, None, "Playwright не установлен"

    # Профиль по сайту
    sub = "_default"
    try:
        if site_id and site_id != "_default":
            from core.agents.browser_sites_manager import get_site
            s = get_site(site_id)
            if s:
                sub = s.get("profile_subdir") or (site_id + "_profile")
    except Exception:
        pass
    profile_dir = Path.home() / ".ai_link" / "browser_profiles" / sub
    profile_dir.mkdir(parents=True, exist_ok=True)

    try:
        playwright = sync_playwright().start()
        context = playwright.chromium.launch_persistent_context(
            user_data_dir=str(profile_dir),
            headless=headless,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        page = context.new_page() if not context.pages else context.pages[0]
        return playwright, context, page, None
    except Exception as e:
        return None, None, None, f"Ошибка запуска браузера: {e}"


def _safe_close(playwright, context):
    try:
        if context:
            context.close()
        if playwright:
            playwright.stop()
    except Exception:
        pass


def _try_selector(page, selector: str, timeout_ms: int = 8000) -> bool:
    try:
        page.wait_for_selector(selector, timeout=timeout_ms)
        el = page.query_selector(selector)
        return el is not None
    except Exception:
        return False


def calibrate_site(site_id: str, headless: bool = False, save: bool = True, startup_delay: int = 180) -> Dict[str, Any]:
    """
    Калибрует селекторы для сайта.
    startup_delay — максимальное время ожидания входа пользователя (секунды).
    Как только поле ввода найдено, ожидание прекращается.
    """
    site = get_site(site_id)
    if not site:
        return {"success": False, "error": f"Сайт '{site_id}' не найден",
                "input_selector": "", "response_selector": ""}

    url = site.get("url")
    timeout = site.get("timeout", 60)

    playwright, context, page, err = _start_browser(site_id=site_id, headless=headless)
    if err:
        return {"success": False, "error": err, "input_selector": "", "response_selector": ""}

    result = {"input_selector": "", "response_selector": "", "success": False, "error": ""}
    try:
        page.goto(url, timeout=timeout * 1000)
        try:
            page.wait_for_load_state("domcontentloaded", timeout=timeout * 1000)
        except Exception:
            pass

        # Ждём появления поля ввода до startup_delay секунд
        deadline = time.time() + max(startup_delay, 10)
        found_input = None
        while time.time() < deadline:
            for sel in INPUT_CANDIDATES:
                if _try_selector(page, sel, timeout_ms=2000):
                    found_input = sel
                    break
            if found_input:
                break
            time.sleep(3)

        if not found_input:
            return {"success": False,
                    "error": f"Не найдено поле ввода за {startup_delay}с. Войдите в аккаунт и попробуйте снова.",
                    "input_selector": "", "response_selector": ""}

        result["input_selector"] = found_input

        # Тестовый промпт
        try:
            input_el = page.query_selector(found_input)
            input_el.click()
            input_el.fill("Скажи одно слово: OK")
            time.sleep(0.5)
            page.keyboard.press("Enter")
        except Exception as e:
            return {"success": False, "error": f"Не удалось отправить тестовый запрос: {e}",
                    "input_selector": found_input, "response_selector": ""}

        # Ждём ответа и ищем селектор до timeout секунд
        wait_deadline = time.time() + max(timeout, 60)
        found_response = None
        while time.time() < wait_deadline:
            for sel in RESPONSE_CANDIDATES:
                try:
                    els = page.query_selector_all(sel)
                    if els:
                        txt = els[-1].inner_text().strip()
                        if len(txt) >= 2 and "OK" in txt.upper() and "Скажи" not in txt:
                            found_response = sel
                            break
                except Exception:
                    continue
            if found_response:
                break
            time.sleep(2)

        if not found_response:
            # Пробуем хотя бы найти непустой ответ
            for sel in RESPONSE_CANDIDATES:
                try:
                    els = page.query_selector_all(sel)
                    if els:
                        txt = els[-1].inner_text().strip()
                        if len(txt) > 0 and "Скажи" not in txt:
                            found_response = sel
                            break
                except Exception:
                    continue

        if not found_response:
            # ЧАСТИЧНЫЙ УСПЕХ: поле ввода нашли — сохраняем его, даже если ответ не найден
            if save and found_input:
                update_site(site_id, {"input_selector": found_input})
            return {
                "success": False,
                "error": "Найдено поле ввода, но селектор ответа не найден. Проверьте, вошли ли вы в аккаунт.",
                "input_selector": found_input,
                "response_selector": "",
                "partial": True,
            }

        result["response_selector"] = found_response
        result["success"] = True

        if save:
            update_site(site_id, {
                "input_selector": found_input,
                "response_selector": found_response,
                "wait_selector": found_response,
            })

        return result

    except Exception as e:
        return {"success": False, "error": str(e),
                "input_selector": result.get("input_selector", ""),
                "response_selector": result.get("response_selector", "")}
    finally:
        _safe_close(playwright, context)


def calibrate_all(headless: bool = False) -> Dict[str, Any]:
    """Калибрует все включённые сайты."""
    from core.agents.browser_sites_manager import get_enabled_sites
    results = {}
    for site in get_enabled_sites():
        results[site["id"]] = calibrate_site(site["id"], headless=headless)
    return results


if __name__ == "__main__":
    import sys

    headless = "--headless" in sys.argv
    args = [a for a in sys.argv[1:] if not a.startswith("--")]

    if args:
        site_id = args[0]
        print(f"Калибровка сайта '{site_id}' (headless={headless})...")
        res = calibrate_site(site_id, headless=headless)
        print(f"Результат: {res}")
        sys.exit(0 if res.get("success") else 1)
    else:
        print(f"Калибровка всех включённых сайтов (headless={headless})...")
        results = calibrate_all(headless=headless)
        for sid, res in results.items():
            status = "OK" if res.get("success") else "FAIL"
            print(f"  [{status}] {sid}: input='{res.get('input_selector')}' response='{res.get('response_selector')}' {res.get('error') or ''}")
        ok = all(r.get("success") for r in results.values()) if results else False
        sys.exit(0 if ok else 1)
