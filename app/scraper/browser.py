from __future__ import annotations

from playwright.sync_api import BrowserContext, Page, Playwright

from app.config import BROWSER_CHANNEL, BROWSER_PROFILE_DIR, NAVIGATION_TIMEOUT_MS
from app.scraper.proxy import ProxyConfig

STEALTH_INIT_SCRIPT = """
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
window.chrome = { runtime: {} };
"""


def open_browser_context(
    playwright: Playwright,
    *,
    headless: bool,
    proxy: ProxyConfig | None = None,
) -> tuple[BrowserContext, Page]:
    if proxy is None:
        BROWSER_PROFILE_DIR.mkdir(parents=True, exist_ok=True)
        launch_kwargs: dict = {
            "user_data_dir": str(BROWSER_PROFILE_DIR),
            "headless": headless,
            "locale": "ru-RU",
            "viewport": {"width": 1366, "height": 768},
            "args": ["--disable-blink-features=AutomationControlled"],
        }
        if BROWSER_CHANNEL:
            launch_kwargs["channel"] = BROWSER_CHANNEL
        context = playwright.chromium.launch_persistent_context(**launch_kwargs)
    else:
        launch_kwargs = {
            "headless": headless,
            "args": ["--disable-blink-features=AutomationControlled"],
        }
        if BROWSER_CHANNEL:
            launch_kwargs["channel"] = BROWSER_CHANNEL
        browser = playwright.chromium.launch(**launch_kwargs)
        context = browser.new_context(
            proxy=proxy.to_playwright(),
            locale="ru-RU",
            viewport={"width": 1366, "height": 768},
        )

    context.add_init_script(STEALTH_INIT_SCRIPT)
    page = context.pages[0] if context.pages else context.new_page()
    page.set_default_timeout(NAVIGATION_TIMEOUT_MS)
    return context, page
