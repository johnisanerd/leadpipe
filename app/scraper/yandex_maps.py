import re
import time
from urllib.parse import parse_qs, quote_plus, unquote, urlparse

from playwright.sync_api import Page

from app.config import (
    ACTION_DELAY_MS,
    CAPTCHA_WAIT_MS,
    DEFAULT_MAX_PAGES,
    HEADED_CLOSE_DELAY_MS,
    HEADLESS,
    MAPS_SCROLL_PAUSE_MS,
    NAVIGATION_TIMEOUT_MS,
)
from app.models import SearchResultItem
from app.scraper.exceptions import ScrapeBlockedError
from app.scraper.search_utils import build_search_phrase
from app.scraper.resilient import ScrapeMeta, run_with_proxy_retries

YANDEX_MAPS_URL = "https://yandex.ru/maps/"

SKIP_DOMAINS = {
    "yandex.ru",
    "ya.ru",
    "yandex.com",
    "yandex.by",
    "yandex.kz",
    "google.com",
    "google.ru",
    "youtube.com",
    "vk.com",
    "facebook.com",
    "instagram.com",
    "t.me",
    "passport.yandex.ru",
}

CARD_SELECTORS = (
    ".search-snippet-view",
    ".search-business-snippet-view",
    "[class*='search-snippet-view']",
)

DETAIL_SELECTORS = (
    ".business-card-view",
    ".card",
    ".sidebar-view__panel",
    "[class*='business-card']",
    "[class*='orgpage']",
)

WEBSITE_SELECTORS = (
    "a.business-urls-view__link",
    "a[aria-label*='Сайт']",
    "a:has-text('Сайт')",
    "a[href^='http']:not([href*='yandex'])",
    "a[href^='//']:not([href*='yandex'])",
)


def normalize_url(raw_url: str) -> str | None:
    if not raw_url or raw_url.startswith("javascript:"):
        return None

    raw_url = raw_url.strip()
    if raw_url.startswith("//"):
        raw_url = f"https:{raw_url}"

    if "redirect" in raw_url.lower() and "url=" in raw_url.lower():
        parsed = urlparse(raw_url)
        query = parse_qs(parsed.query)
        redirected = query.get("url", [None])[0]
        if redirected:
            raw_url = unquote(redirected)

    parsed = urlparse(raw_url)
    if parsed.scheme not in {"http", "https"}:
        return None

    host = parsed.netloc.lower().removeprefix("www.")
    if any(host == domain or host.endswith(f".{domain}") for domain in SKIP_DOMAINS):
        return None

    return raw_url


def is_blocked_page(page: Page) -> bool:
    url = page.url.lower()
    if "captcha" in url or "showcaptcha" in url:
        return True
    html = page.content().lower()
    return "smartcaptcha" in html or "подтвердите, что запросы отправляете вы" in html


def dismiss_consent_if_present(page: Page) -> None:
    selectors = [
        'button:has-text("Принять")',
        'button:has-text("Accept")',
        'button:has-text("Согласен")',
        'button:has-text("OK")',
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


def _card_locator(page: Page):
    return page.locator(", ".join(CARD_SELECTORS))


def count_map_snippets(page: Page) -> int:
    return _card_locator(page).count()


def wait_for_map_results(page: Page, *, headless: bool) -> None:
    timeout_ms = CAPTCHA_WAIT_MS if not headless else NAVIGATION_TIMEOUT_MS
    started = time.monotonic()

    while (time.monotonic() - started) * 1000 < timeout_ms:
        if count_map_snippets(page) > 0:
            page.wait_for_timeout(ACTION_DELAY_MS)
            return

        if is_blocked_page(page):
            if headless:
                raise ScrapeBlockedError(
                    "Яндекс.Карты показали CAPTCHA. Запустите с headless=false и пройдите проверку."
                )
            page.wait_for_timeout(2000)
            continue

        try:
            page.wait_for_selector(", ".join(CARD_SELECTORS), timeout=2000)
            return
        except Exception:
            page.wait_for_timeout(1000)

    if is_blocked_page(page):
        raise ScrapeBlockedError("CAPTCHA на Яндекс.Картах не пройдена за отведённое время.")

    if count_map_snippets(page) == 0:
        raise ScrapeBlockedError("Карточки организаций на Яндекс.Картах не загрузились.")


def scroll_results_list(page: Page, scroll_rounds: int) -> None:
    scroll_selectors = [
        ".scroll__container",
        ".sidebar-view__panel",
        ".search-list-view__list",
        "[class*='scroll__container']",
    ]

    for _ in range(scroll_rounds):
        scrolled = False
        for selector in scroll_selectors:
            container = page.locator(selector).first
            try:
                if container.count() > 0 and container.is_visible(timeout=1000):
                    container.evaluate("(el) => { el.scrollTop = el.scrollHeight; }")
                    scrolled = True
                    break
            except Exception:
                continue

        if not scrolled:
            page.mouse.wheel(0, 2500)

        page.wait_for_timeout(MAPS_SCROLL_PAUSE_MS)


def _extract_text(node) -> str:
    try:
        return node.inner_text(timeout=1500).strip()
    except Exception:
        return ""


def _detail_root(page: Page):
    for selector in DETAIL_SELECTORS:
        node = page.locator(selector).first
        if node.count() > 0 and node.is_visible(timeout=500):
            return node
    return page.locator("body")


def _extract_website_from_scope(scope) -> str | None:
    for selector in WEBSITE_SELECTORS:
        links = scope.locator(selector)
        for index in range(links.count()):
            href = links.nth(index).get_attribute("href") or ""
            normalized = normalize_url(href)
            if normalized:
                return normalized

    html = ""
    try:
        html = scope.inner_html(timeout=1500)
    except Exception:
        pass

    for match in re.findall(r'href=["\']([^"\']+)["\']', html, flags=re.IGNORECASE):
        normalized = normalize_url(match)
        if normalized:
            return normalized

    for match in re.findall(r"https?://[^\s\"'<>]+", html):
        normalized = normalize_url(match.rstrip(".,;)"))
        if normalized:
            return normalized

    return None


def _extract_title_from_scope(scope) -> str:
    title_selectors = (
        ".card-title-view__title",
        ".business-card-title-view__title",
        ".search-business-snippet-view__title",
        "h1",
        "[class*='title']",
    )
    for selector in title_selectors:
        node = scope.locator(selector).first
        if node.count() > 0:
            title = _extract_text(node)
            if title and len(title) < 200:
                return title
    return ""


def _extract_phone_from_scope(scope) -> str | None:
    tel = scope.locator('a[href^="tel:"]').first
    if tel.count() > 0:
        href = tel.get_attribute("href") or ""
        return href.removeprefix("tel:").strip()

    text = _extract_text(scope)
    match = re.search(
        r"(?:\+7|8)[\s\-]?\(?\d{3}\)?[\s\-]?\d{3}[\s\-]?\d{2}[\s\-]?\d{2}",
        text,
    )
    return match.group(0).strip() if match else None


def _extract_address_from_scope(scope) -> str | None:
    address_selectors = (
        ".business-contacts-view__address",
        ".search-business-snippet-view__address",
        "[class*='address']",
    )
    for selector in address_selectors:
        node = scope.locator(selector).first
        if node.count() > 0:
            address = _extract_text(node)
            if address:
                return address
    return None


def _extract_from_opened_card(page: Page, list_card) -> tuple[str, str | None, str | None, str | None]:
    title = _extract_title_from_scope(list_card) or _extract_title_from_scope(_detail_root(page))
    detail = _detail_root(page)
    website = _extract_website_from_scope(detail) or _extract_website_from_scope(list_card)
    phone = _extract_phone_from_scope(detail) or _extract_phone_from_scope(list_card)
    address = _extract_address_from_scope(detail) or _extract_address_from_scope(list_card)
    return title, website, phone, address


def extract_results_from_maps(
    page: Page,
    *,
    scroll_rounds: int,
    max_cards: int | None = None,
) -> list[SearchResultItem]:
    scroll_results_list(page, scroll_rounds)

    cards_count = _card_locator(page).count()
    if cards_count == 0:
        return []

    limit = max_cards or min(cards_count, 15 * max(scroll_rounds, 1))
    limit = min(limit, cards_count)

    items: list[SearchResultItem] = []
    seen_urls: set[str] = set()
    seen_titles: set[str] = set()

    for index in range(limit):
        cards = _card_locator(page)
        if index >= cards.count():
            break

        card = cards.nth(index)
        try:
            card.scroll_into_view_if_needed(timeout=5000)
            card.click(timeout=5000)
            page.wait_for_timeout(1200)
        except Exception:
            continue

        title, website, phone, address = _extract_from_opened_card(page, card)
        if not title or not website:
            continue

        title_key = title.casefold()
        if website in seen_urls or title_key in seen_titles:
            continue

        snippet_parts = [part for part in (address, phone) if part]
        snippet = " | ".join(snippet_parts) if snippet_parts else None

        seen_urls.add(website)
        seen_titles.add(title_key)
        items.append(
            SearchResultItem(
                position=len(items) + 1,
                title=title,
                url=website,
                snippet=snippet,
                page=1,
            )
        )

    return items


def scrape_yandex_maps_on_page(
    page: Page,
    query: str,
    city: str,
    max_scroll_rounds: int = DEFAULT_MAX_PAGES,
    *,
    headless: bool = HEADLESS,
    raise_if_empty: bool = True,
) -> tuple[str, list[SearchResultItem]]:
    search_phrase = build_search_phrase(query, city)
    url = f"{YANDEX_MAPS_URL}?text={quote_plus(search_phrase)}"

    page.goto(url, wait_until="domcontentloaded")
    dismiss_consent_if_present(page)
    wait_for_map_results(page, headless=headless)

    results = extract_results_from_maps(page, scroll_rounds=max_scroll_rounds)
    cards_seen = count_map_snippets(page)

    if raise_if_empty and not results:
        if cards_seen > 0:
            raise RuntimeError(
                f"Найдено карточек в списке: {cards_seen}, но с внешним сайтом — 0. "
                "У многих организаций на Яндекс.Картах сайт не указан. "
                "Попробуйте headless=false или другой запрос/город."
            )
        raise RuntimeError("Карточки организаций на Яндекс.Картах не загрузились.")

    return search_phrase, results


def scrape_yandex_maps_batch(
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
            search_phrase, results, meta = scrape_yandex_maps(
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
                    "error_message": None if results else "Нет организаций с сайтом в карточках",
                    "total_results": len(results),
                    "pages_scraped": max_pages if results else 0,
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


def scrape_yandex_maps(
    query: str,
    city: str,
    max_pages: int = DEFAULT_MAX_PAGES,
    *,
    headless: bool = HEADLESS,
    allow_empty: bool = False,
) -> tuple[str, list[SearchResultItem], ScrapeMeta]:
    def playwright_scrape(page: Page) -> tuple[str, list[SearchResultItem]]:
        search_phrase, results = scrape_yandex_maps_on_page(
            page,
            query,
            city,
            max_scroll_rounds=max_pages,
            headless=headless,
            raise_if_empty=not allow_empty,
        )
        if not headless:
            page.wait_for_timeout(HEADED_CLOSE_DELAY_MS)
        return search_phrase, results

    def apify_scrape() -> tuple[str, list[SearchResultItem]]:
        from app.scraper.apify_client import apify_yandex_maps

        return apify_yandex_maps(query, city, max_pages)

    (search_phrase, results), meta = run_with_proxy_retries(
        playwright_scrape,
        headless=headless,
        apify_fallback=apify_scrape,
    )
    return search_phrase, results, meta
