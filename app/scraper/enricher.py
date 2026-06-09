import json
import re
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse

from playwright.sync_api import Page

from app.config import ACTION_DELAY_MS, HEADLESS, NAVIGATION_TIMEOUT_MS
from app.scraper.exceptions import ScrapeBlockedError
from app.scraper.resilient import run_with_proxy_retries

EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
PHONE_RE = re.compile(
    r"(?:\+7|8)[\s\-]?\(?\d{3}\)?[\s\-]?\d{3}[\s\-]?\d{2}[\s\-]?\d{2}"
    r"|\+?\d{1,3}[\s\-]?\(?\d{2,4}\)?[\s\-]?\d{2,4}[\s\-]?\d{2,4}[\s\-]?\d{0,4}"
)

CONTACT_PATHS = (
    "",
    "/contacts",
    "/contact",
    "/kontakty",
    "/kontakt",
    "/about/contacts",
    "/svyaz",
    "/feedback",
    "/obratnaya-svyaz",
)

EMAIL_SKIP_FRAGMENTS = (
    "example.com",
    "wixpress.com",
    "sentry.io",
    "yourdomain",
    "email.com",
    "domain.com",
    "2x.webp",
    "png",
    "jpg",
    "jpeg",
    "svg",
)

WIDGET_MARKERS = (
    "jivosite",
    "jivo.ru",
    "bitrix24",
    "tawk.to",
    "intercom",
    "carrotquest",
    "callibri",
    "verbox",
    "livechat",
    "envybox",
    "redhelper",
    "cleversite",
    "webim",
    "usedesk",
)

ORDER_BUTTON_TEXT = re.compile(
    r"заказать|оставить заявку|обратная связь|написать нам|связаться|"
    r"получить консультацию|отправить заявку|записаться",
    re.IGNORECASE,
)

FEEDBACK_TEXT = re.compile(r"обратная связь|feedback|написать нам|contact us", re.IGNORECASE)


@dataclass
class EnrichmentData:
    emails: list[str] = field(default_factory=list)
    phones: list[str] = field(default_factory=list)
    messengers: dict[str, str] = field(default_factory=dict)
    has_contact_form: bool = False
    has_feedback_form: bool = False
    has_online_widget: bool = False
    has_order_button: bool = False
    page_title: str | None = None
    meta_description: str | None = None
    raw_text: str | None = None


def _normalize_phone(raw: str) -> str:
    digits = re.sub(r"\D", "", raw)
    if len(digits) == 11 and digits.startswith("8"):
        digits = "7" + digits[1:]
    if len(digits) == 10:
        digits = "7" + digits
    if len(digits) == 11 and digits.startswith("7"):
        return f"+7 ({digits[1:4]}) {digits[4:7]}-{digits[7:9]}-{digits[9:11]}"
    return raw.strip()


def _is_valid_email(email: str) -> bool:
    lowered = email.lower()
    if any(fragment in lowered for fragment in EMAIL_SKIP_FRAGMENTS):
        return False
    if lowered.endswith((".png", ".jpg", ".jpeg", ".webp", ".svg", ".gif")):
        return False
    return True


def _extract_emails(text: str) -> list[str]:
    found = []
    seen = set()
    for match in EMAIL_RE.findall(text):
        email = match.lower().strip(".")
        if not _is_valid_email(email) or email in seen:
            continue
        seen.add(email)
        found.append(email)
    return found


def _extract_phones(text: str) -> list[str]:
    found = []
    seen = set()
    for match in PHONE_RE.findall(text):
        normalized = _normalize_phone(match)
        key = re.sub(r"\D", "", normalized)
        if len(key) < 10 or key in seen:
            continue
        seen.add(key)
        found.append(normalized)
    return found


def _extract_messengers(html: str, links: list[str]) -> dict[str, str]:
    messengers: dict[str, str] = {}
    sources = links + re.findall(r'href=["\']([^"\']+)["\']', html, flags=re.IGNORECASE)

    for href in sources:
        lowered = href.lower()
        if "wa.me" in lowered or "whatsapp.com" in lowered or "api.whatsapp" in lowered:
            messengers.setdefault("whatsapp", href)
        if "t.me/" in lowered or "telegram.me/" in lowered or "telegram.org" in lowered:
            messengers.setdefault("telegram", href)
        if lowered.startswith("viber://") or "viber.com" in lowered:
            messengers.setdefault("viber", href)

    return messengers


def _page_links(page: Page) -> list[str]:
    hrefs: list[str] = []
    for link in page.locator("a[href]").all():
        href = link.get_attribute("href")
        if href:
            hrefs.append(href)
    return hrefs


def _detect_forms(page: Page) -> tuple[bool, bool]:
    has_contact_form = False
    has_feedback_form = False

    forms = page.locator("form")
    for index in range(forms.count()):
        form = forms.nth(index)
        html = (form.inner_html() or "").lower()
        text = (form.inner_text() or "").lower()
        has_email = form.locator('input[type="email"], input[name*="email" i], input[name*="mail" i]').count() > 0
        has_phone = form.locator('input[type="tel"], input[name*="phone" i], input[name*="tel" i]').count() > 0
        has_textarea = form.locator("textarea").count() > 0
        has_submit = form.locator('button[type="submit"], input[type="submit"]').count() > 0

        if has_textarea or has_email or has_phone:
            has_contact_form = True
        if FEEDBACK_TEXT.search(text) or FEEDBACK_TEXT.search(html):
            has_feedback_form = True
        elif has_textarea and has_submit:
            has_feedback_form = True

    return has_contact_form, has_feedback_form


def _detect_widget(html: str) -> bool:
    lowered = html.lower()
    return any(marker in lowered for marker in WIDGET_MARKERS)


def _detect_order_button(page: Page) -> bool:
    candidates = page.locator("a, button, input[type='button'], input[type='submit']")
    for index in range(min(candidates.count(), 200)):
        node = candidates.nth(index)
        try:
            text = node.inner_text(timeout=500).strip()
        except Exception:
            text = node.get_attribute("value") or ""
        if text and ORDER_BUTTON_TEXT.search(text):
            return True
    return False


def _load_page(page: Page, url: str) -> str | None:
    try:
        response = page.goto(url, wait_until="domcontentloaded", timeout=NAVIGATION_TIMEOUT_MS)
        page.wait_for_timeout(ACTION_DELAY_MS)
        if response and response.status >= 400:
            return f"HTTP {response.status}"
        return None
    except Exception as exc:
        return str(exc)


def _merge_unique(existing: list[str], new_items: list[str]) -> list[str]:
    seen = set(existing)
    merged = list(existing)
    for item in new_items:
        if item not in seen:
            seen.add(item)
            merged.append(item)
    return merged


def enrich_single_url(page: Page, url: str) -> EnrichmentData:
    result = EnrichmentData()
    texts: list[str] = []
    html_parts: list[str] = []
    all_links: list[str] = []
    errors: list[str] = []

    parsed = urlparse(url)
    if not parsed.scheme:
        url = f"https://{url.lstrip('/')}"

    base = f"{urlparse(url).scheme}://{urlparse(url).netloc}"

    for path in CONTACT_PATHS:
        target = url if not path else urljoin(base + "/", path.lstrip("/"))
        error = _load_page(page, target)
        if error:
            errors.append(f"{target}: {error}")
            continue

        content = page.content()
        body_text = page.locator("body").inner_text(timeout=5000)
        texts.append(body_text)
        html_parts.append(content)
        all_links.extend(_page_links(page))

        result.emails = _merge_unique(result.emails, _extract_emails(body_text))
        result.phones = _merge_unique(result.phones, _extract_phones(body_text))

        if not result.page_title:
            result.page_title = page.title()
            meta = page.locator('meta[name="description"]').first
            if meta.count() > 0:
                result.meta_description = meta.get_attribute("content")

        contact_form, feedback_form = _detect_forms(page)
        result.has_contact_form = result.has_contact_form or contact_form
        result.has_feedback_form = result.has_feedback_form or feedback_form
        result.has_online_widget = result.has_online_widget or _detect_widget(content)
        result.has_order_button = result.has_order_button or _detect_order_button(page)

        if path == "":
            continue

        if result.emails and result.phones and result.has_contact_form:
            break

    combined_text = "\n".join(texts)
    combined_html = "\n".join(html_parts)

    tel_links = re.findall(r"tel:([+\d\s\-()]+)", combined_html, flags=re.IGNORECASE)
    result.phones = _merge_unique(result.phones, [_normalize_phone(item) for item in tel_links])

    result.messengers = _extract_messengers(combined_html, all_links)
    result.raw_text = combined_text[:8000] if combined_text else None

    if not texts and errors:
        raise RuntimeError("; ".join(errors[:3]))

    return result


def enrich_sites(
    sites: list[dict],
    *,
    headless: bool = HEADLESS,
) -> list[dict]:
    if not sites:
        return []

    output: list[dict] = []

    for site in sites:
        site_id = str(site["id"])
        url = site["url"]
        try:
            data, _meta = run_with_proxy_retries(
                lambda page: enrich_single_url(page, url),
                headless=headless,
                retry_on_any_error=True,
            )
            output.append(
                {
                    "site_id": site_id,
                    "url": url,
                    "domain": site.get("domain") or urlparse(url).netloc,
                    "status": "done",
                    "error_message": None,
                    **data.__dict__,
                }
            )
        except (ScrapeBlockedError, Exception) as exc:
            output.append(
                {
                    "site_id": site_id,
                    "url": url,
                    "domain": site.get("domain") or urlparse(url).netloc,
                    "status": "failed",
                    "error_message": str(exc),
                    "emails": [],
                    "phones": [],
                    "messengers": {},
                    "has_contact_form": False,
                    "has_feedback_form": False,
                    "has_online_widget": False,
                    "has_order_button": False,
                    "page_title": None,
                    "meta_description": None,
                    "raw_text": None,
                }
            )

    return output
