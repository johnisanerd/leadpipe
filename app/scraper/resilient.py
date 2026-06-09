from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TypeVar

from playwright.sync_api import Page, sync_playwright

from app.config import APIFY_API_TOKEN
from app.scraper import block_tracker
from app.scraper.browser import open_browser_context
from app.scraper.exceptions import ScrapeBlockedError, ScrapeProxyExhaustedError
from app.scraper.proxy import ProxyConfig, build_proxy_attempts

T = TypeVar("T")


def apify_enabled() -> bool:
    return bool(APIFY_API_TOKEN)


@dataclass
class ScrapeMeta:
    scrape_source: str = "playwright"
    proxy_server: str | None = None


def _proxy_label(proxy: ProxyConfig | None) -> str | None:
    if proxy is None:
        return "direct"
    return proxy.display()


def run_with_proxy_retries(
    operation: Callable[[Page], T],
    *,
    headless: bool,
    apify_fallback: Callable[[], T] | None = None,
    retry_on_any_error: bool = False,
) -> tuple[T, ScrapeMeta]:
    if apify_fallback and apify_enabled() and block_tracker.should_use_apify():
        result = apify_fallback()
        return result, ScrapeMeta(scrape_source="apify", proxy_server=None)

    attempts = build_proxy_attempts()
    if not attempts:
        attempts = [None]

    last_error: Exception | None = None

    for proxy in attempts:
        with sync_playwright() as playwright:
            context, page = open_browser_context(playwright, headless=headless, proxy=proxy)
            try:
                result = operation(page)
                block_tracker.record_success()
                return result, ScrapeMeta(scrape_source="playwright", proxy_server=_proxy_label(proxy))
            except ScrapeBlockedError as exc:
                last_error = exc
            except Exception as exc:
                if retry_on_any_error:
                    last_error = exc
                else:
                    raise
            finally:
                context.close()

    block_tracker.record_all_proxies_blocked()

    if apify_fallback and apify_enabled() and block_tracker.should_use_apify():
        result = apify_fallback()
        return result, ScrapeMeta(scrape_source="apify", proxy_server=None)

    if last_error:
        raise ScrapeProxyExhaustedError(
            f"Все прокси заблокированы ({len(attempts)} попыток). "
            f"Подряд неудачных раундов: {block_tracker.consecutive_failures()}. "
            f"Последняя ошибка: {last_error}"
        ) from last_error

    raise ScrapeProxyExhaustedError("Не удалось выполнить запрос: прокси не настроены.")
