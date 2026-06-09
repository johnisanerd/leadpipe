import re
import time
from urllib.parse import quote_plus, urlparse

from playwright.sync_api import Page

from app.config import (
    ACTION_DELAY_MS,
    CAPTCHA_WAIT_MS,
    DEFAULT_MAX_PAGES,
    HEADED_CLOSE_DELAY_MS,
    HEADLESS,
    NAVIGATION_TIMEOUT_MS,
    RESULTS_PER_PAGE,
)
from app.models import SearchResultItem
from app.scraper.exceptions import ScrapeBlockedError
from app.scraper.resilient import ScrapeMeta, run_with_proxy_retries
from app.scraper.search_utils import build_search_phrase

GOOGLE_SEARCH_URL = "https://www.google.com/search"

SKIP_DOMAINS = {
    "google.com",
    "google.ru",
    "youtube.com",
    "accounts.google.com",
    "support.google.com",
    "maps.google.com",
    "webcache.googleusercontent.com",
    "policies.google.com",
}

def normalize_url(raw_url: str) -> str | None:
    if not raw_url or raw_url.startswith("javascript:"):
        return None

    if raw_url.startswith("/url?"):
        match = re.search(r"[?&]q=([^&]+)", raw_url)
        if match:
            raw_url = match.group(1)

    parsed = urlparse(raw_url)
    if parsed.scheme not in {"http", "https"}:
        return None

    host = parsed.netloc.lower().removeprefix("www.")
    if any(host == domain or host.endswith(f".{domain}") for domain in SKIP_DOMAINS):
        return None

    return raw_url


def is_blocked_page(page: Page) -> bool:
    url = page.url.lower()
    if "/sorry/" in url or "google.com/sorry" in url:
        return True
    html = page.content().lower()
    return "captcha-form" in html or "g-recaptcha" in html and "recaptcha" in html


def count_serp_headings(page: Page) -> int:
    return page.locator("#search a h3, #rso a h3, #search h3, #rso h3").count()


def dismiss_consent_if_present(page: Page) -> None:
    selectors = [
        'button:has-text("Accept all")',
        'button:has-text("Принять все")',
        'button:has-text("I agree")',
        'button:has-text("Согласен")',
        "#L2AGLb",
        'form[action*="consent"] button',
    ]
    for selector in selectors:
        button = page.locator(selector).first
        try:
            if button.is_visible(timeout=1500):
                button.click()
                page.wait_for_timeout(ACTION_DELAY_MS)
                return
        except Exception:
            continue


def wait_for_serp(page: Page, *, headless: bool) -> None:
    timeout_ms = CAPTCHA_WAIT_MS if not headless else NAVIGATION_TIMEOUT_MS
    started = time.monotonic()

    while (time.monotonic() - started) * 1000 < timeout_ms:
        if count_serp_headings(page) > 0:
            page.wait_for_timeout(ACTION_DELAY_MS)
            return

        if is_blocked_page(page):
            if headless:
                raise ScrapeBlockedError(
                    "Google показал CAPTCHA. Запустите с headless=false и пройдите проверку вручную."
                )
            page.wait_for_timeout(2000)
            continue

        try:
            page.wait_for_selector(
                "#search a h3, #rso a h3, #search h3, #rso h3",
                timeout=2000,
            )
            return
        except Exception:
            page.wait_for_timeout(1000)

    if is_blocked_page(page):
        raise ScrapeBlockedError(
            "CAPTCHA не пройдена за 2 минуты. Пройдите проверку в открытом окне Chrome и повторите запрос."
        )

    if count_serp_headings(page) == 0:
        raise ScrapeBlockedError(
            "Выдача не загрузилась: на странице нет результатов. "
            "Проверьте, что в браузере открылась страница поиска Google."
        )


def _append_result(
    items: list[SearchResultItem],
    seen_urls: set[str],
    *,
    title: str,
    url: str,
    snippet: str | None,
    page_number: int,
    start_position: int,
) -> None:
    normalized = normalize_url(url)
    if not normalized or normalized in seen_urls:
        return
    title = title.strip()
    if not title:
        return
    seen_urls.add(normalized)
    items.append(
        SearchResultItem(
            position=start_position + len(items),
            title=title,
            url=normalized,
            snippet=snippet,
            page=page_number,
        )
    )


def extract_results_from_page(page: Page, page_number: int, start_position: int) -> list[SearchResultItem]:
    items: list[SearchResultItem] = []
    seen_urls: set[str] = set()

    link_selectors = [
        "#rso a:has(h3)",
        "#search a:has(h3)",
        "div.MjjYud a:has(h3)",
        "motion.div a:has(h3)",
    ]
    for selector in link_selectors:
        links = page.locator(selector)
        for index in range(links.count()):
            link = links.nth(index)
            href = link.get_attribute("href") or ""
            title_node = link.locator("h3").first
            if title_node.count() == 0:
                continue
            title = title_node.inner_text()
            block = link.locator("xpath=ancestor::motion.div[1] | ancestor::motion_div[1] | ancestor::motion-div[1] | ancestor::motion[1]").first
            snippet = None
            if block.count() > 0:
                snippet_node = block.locator(".VwiC3b, .IsZvec, .MUxGbd, [data-sncf]").first
                if snippet_node.count() > 0:
                    snippet = snippet_node.inner_text().strip() or None
            _append_result(
                items,
                seen_urls,
                title=title,
                url=href,
                snippet=snippet,
                page_number=page_number,
                start_position=start_position,
            )
        if items:
            break

    if not items:
        headings = page.locator("#search h3, #rso h3")
        for index in range(headings.count()):
            heading = headings.nth(index)
            link = heading.locator("xpath=ancestor::a[1]")
            if link.count() == 0:
                continue
            href = link.get_attribute("href") or ""
            title = heading.inner_text()
            _append_result(
                items,
                seen_urls,
                title=title,
                url=href,
                snippet=None,
                page_number=page_number,
                start_position=start_position,
            )

    return items


def scrape_google_serp_on_page(
    page: Page,
    query: str,
    city: str,
    max_pages: int = DEFAULT_MAX_PAGES,
    *,
    headless: bool = HEADLESS,
    raise_if_empty: bool = True,
) -> tuple[str, list[SearchResultItem]]:
    search_phrase = build_search_phrase(query, city)
    all_results: list[SearchResultItem] = []
    global_seen_urls: set[str] = set()

    for page_number in range(1, max_pages + 1):
        start = (page_number - 1) * RESULTS_PER_PAGE
        params = f"q={quote_plus(search_phrase)}&hl=ru&gl=ru&start={start}"
        url = f"{GOOGLE_SEARCH_URL}?{params}"

        page.goto(url, wait_until="domcontentloaded")
        dismiss_consent_if_present(page)
        wait_for_serp(page, headless=headless)

        page_results = extract_results_from_page(
            page,
            page_number=page_number,
            start_position=len(all_results) + 1,
        )

        new_results = [item for item in page_results if item.url not in global_seen_urls]
        for item in new_results:
            global_seen_urls.add(item.url)

        if not new_results:
            break

        all_results.extend(new_results)
        page.wait_for_timeout(ACTION_DELAY_MS)

    if raise_if_empty and not all_results:
        raise RuntimeError(
            "Результаты на экране есть, но парсер не смог их извлечь. "
            "Сообщите — подстроим селекторы под текущую вёрстку Google."
        )

    return search_phrase, all_results


def scrape_google_serp_batch(
    query: str,
    cities: list[str],
    max_pages: int = DEFAULT_MAX_PAGES,
    *,
    headless: bool = HEADLESS,
    stop_on_block: bool = False,
) -> list[dict]:
    if not cities:
        return []

    batch_results: list[dict] = []

    for city in cities:
        try:
            search_phrase, results, meta = scrape_google_serp(
                query,
                city,
                max_pages,
                headless=headless,
                allow_empty=True,
            )
            batch_results.append(
                {
                    "city": city,
                    "search_phrase": search_phrase,
                    "status": "completed" if results else "failed",
                    "error_message": None if results else "Пустая выдача",
                    "total_results": len(results),
                    "pages_scraped": max((item.page for item in results), default=0),
                    "results": results,
                    "scrape_source": meta.scrape_source,
                    "proxy_server": meta.proxy_server,
                }
            )
        except ScrapeBlockedError as exc:
            batch_results.append(
                {
                    "city": city,
                    "search_phrase": build_search_phrase(query, city),
                    "status": "blocked",
                    "error_message": str(exc),
                    "total_results": 0,
                    "pages_scraped": 0,
                    "results": [],
                    "scrape_source": None,
                    "proxy_server": None,
                }
            )
            if stop_on_block:
                break
        except Exception as exc:
            batch_results.append(
                {
                    "city": city,
                    "search_phrase": build_search_phrase(query, city),
                    "status": "failed",
                    "error_message": str(exc),
                    "total_results": 0,
                    "pages_scraped": 0,
                    "results": [],
                    "scrape_source": None,
                    "proxy_server": None,
                }
            )

    return batch_results


def scrape_google_serp(
    query: str,
    city: str,
    max_pages: int = DEFAULT_MAX_PAGES,
    *,
    headless: bool = HEADLESS,
    allow_empty: bool = False,
) -> tuple[str, list[SearchResultItem], ScrapeMeta]:
    def playwright_scrape(page: Page) -> tuple[str, list[SearchResultItem]]:
        search_phrase, results = scrape_google_serp_on_page(
            page,
            query,
            city,
            max_pages,
            headless=headless,
            raise_if_empty=not allow_empty,
        )
        if not headless:
            page.wait_for_timeout(HEADED_CLOSE_DELAY_MS)
        return search_phrase, results

    def apify_scrape() -> tuple[str, list[SearchResultItem]]:
        from app.scraper.apify_client import apify_google_serp

        return apify_google_serp(query, city, max_pages)

    (search_phrase, results), meta = run_with_proxy_retries(
        playwright_scrape,
        headless=headless,
        apify_fallback=apify_scrape,
    )
    return search_phrase, results, meta
