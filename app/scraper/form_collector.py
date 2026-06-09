from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from urllib.parse import urljoin, urlparse

from playwright.sync_api import Locator, Page

CAPTCHA_MARKERS = (
    "g-recaptcha",
    "recaptcha",
    "hcaptcha",
    "smartcaptcha",
    "captcha",
)

SEARCH_FORM_MARKERS = re.compile(r"search|query|s=|/search", re.IGNORECASE)
LOGIN_MARKERS = re.compile(r"password|login|signin|auth|войти|пароль", re.IGNORECASE)
FEEDBACK_MARKERS = re.compile(
    r"обратная связь|feedback|написать нам|contact us|связаться|заявк",
    re.IGNORECASE,
)
CALLBACK_MARKERS = re.compile(r"перезвон|callback|заказать звонок", re.IGNORECASE)
ORDER_MARKERS = re.compile(r"заказать|оформить|записаться|купить", re.IGNORECASE)

PURPOSE_BY_NAME = (
    (re.compile(r"email|e-mail|mail|почт", re.I), "email"),
    (re.compile(r"phone|tel|mobile|тел|номер", re.I), "phone"),
    (re.compile(r"name|fio|fname|lname|имя|фио", re.I), "name"),
    (re.compile(r"company|org|firm|компан|организа", re.I), "company"),
    (re.compile(r"message|comment|text|body|msg|сообщ|коммент|вопрос", re.I), "message"),
    (re.compile(r"subject|theme|тема", re.I), "subject"),
    (re.compile(r"city|город", re.I), "city"),
)


@dataclass
class FormFieldData:
    name: str | None
    type: str
    selector: str
    label: str | None = None
    placeholder: str | None = None
    purpose: str = "unknown"
    required: bool = False


@dataclass
class SiteFormData:
    page_url: str
    form_index: int
    form_action: str | None
    form_method: str
    form_selector: str
    submit_selector: str | None
    fields: list[FormFieldData] = field(default_factory=list)
    form_type: str = "unknown"
    form_score: int = 0
    has_captcha: bool = False
    in_iframe: bool = False
    is_submittable: bool = True


def _build_selector(tag: str, attrs: dict[str, str | None]) -> str:
    element_id = attrs.get("id")
    name = attrs.get("name")
    if element_id:
        return f"{tag}#{element_id}"
    if name:
        return f'{tag}[name="{name}"]'
    return tag


def _guess_purpose(
    *,
    input_type: str,
    name: str | None,
    placeholder: str | None,
    label: str | None,
    is_textarea: bool = False,
) -> str:
    if is_textarea:
        return "message"
    if input_type == "email":
        return "email"
    if input_type == "tel":
        return "phone"

    haystack = " ".join(filter(None, [name, placeholder, label])).lower()
    for pattern, purpose in PURPOSE_BY_NAME:
        if pattern.search(haystack):
            return purpose
    return "unknown"


def _extract_label(form: Locator, field: Locator) -> str | None:
    field_id = field.get_attribute("id")
    if field_id:
        label_node = form.locator(f'label[for="{field_id}"]').first
        if label_node.count() > 0:
            text = label_node.inner_text().strip()
            if text:
                return text

    parent_label = field.locator("xpath=ancestor::label[1]").first
    if parent_label.count() > 0:
        text = parent_label.inner_text().strip()
        if text:
            return text

    aria = field.get_attribute("aria-label")
    return aria.strip() if aria else None


def _is_honeypot(field: Locator) -> bool:
    name = (field.get_attribute("name") or "").lower()
    if any(token in name for token in ("honeypot", "bot", "trap", "hp_")):
        return True

    try:
        if not field.is_visible(timeout=300):
            style = field.get_attribute("style") or ""
            if "display:none" in style.replace(" ", "") or "visibility:hidden" in style.replace(" ", ""):
                return True
    except Exception:
        pass
    return False


def _collect_fields(form: Locator, page_url: str) -> list[FormFieldData]:
    fields: list[FormFieldData] = []
    seen_selectors: set[str] = set()

    candidates = form.locator("input, textarea, select")
    for index in range(candidates.count()):
        node = candidates.nth(index)
        tag = node.evaluate("el => el.tagName.toLowerCase()")
        input_type = (node.get_attribute("type") or "text").lower()
        if tag == "input" and input_type in {"hidden", "submit", "button", "reset", "image", "file"}:
            continue
        if _is_honeypot(node):
            continue

        name = node.get_attribute("name")
        placeholder = node.get_attribute("placeholder")
        label = _extract_label(form, node)
        required = node.get_attribute("required") is not None
        selector = _build_selector(tag, {"id": node.get_attribute("id"), "name": name})
        if selector in seen_selectors:
            selector = f"{selector}:nth-of-type({index + 1})"
        seen_selectors.add(selector)

        purpose = _guess_purpose(
            input_type=input_type,
            name=name,
            placeholder=placeholder,
            label=label,
            is_textarea=tag == "textarea",
        )
        fields.append(
            FormFieldData(
                name=name,
                type=input_type if tag == "input" else tag,
                selector=selector,
                label=label,
                placeholder=placeholder,
                purpose=purpose,
                required=required,
            )
        )

    return fields


def _detect_form_type(form: Locator, fields: list[FormFieldData]) -> str:
    text = (form.inner_text() or "").lower()
    html = (form.inner_html() or "").lower()
    blob = f"{text} {html}"

    purposes = {field.purpose for field in fields}
    if CALLBACK_MARKERS.search(blob):
        return "callback"
    if ORDER_MARKERS.search(blob):
        return "order"
    if FEEDBACK_MARKERS.search(blob) or "message" in purposes:
        return "feedback"
    if {"email", "phone", "message"} & purposes or "message" in purposes:
        return "contact"
    if "email" in purposes or "phone" in purposes:
        return "contact"
    return "unknown"


def _score_form(fields: list[FormFieldData], *, has_captcha: bool, form_type: str) -> int:
    if has_captcha:
        return 10
    if not fields:
        return 0

    score = 20
    purposes = {field.purpose for field in fields}
    if "message" in purposes:
        score += 30
    if "email" in purposes:
        score += 15
    if "phone" in purposes:
        score += 15
    if "name" in purposes:
        score += 10
    if form_type in {"contact", "feedback", "callback"}:
        score += 10
    if len(fields) >= 3:
        score += 5
    return min(score, 100)


def _should_skip_form(form: Locator, fields: list[FormFieldData], page_url: str) -> str | None:
    html = (form.inner_html() or "").lower()
    action = (form.get_attribute("action") or "").lower()

    if form.locator('input[type="password"]').count() > 0 or LOGIN_MARKERS.search(html):
        return "login_form"
    if form.locator('input[type="search"]').count() > 0 and not fields:
        return "search_form"
    if SEARCH_FORM_MARKERS.search(action) or SEARCH_FORM_MARKERS.search(page_url):
        if len(fields) <= 1:
            return "search_form"
    if len(fields) == 1 and fields[0].purpose == "email" and fields[0].type == "email":
        return "newsletter_only"
    if not fields:
        return "no_fields"
    if not form.locator('button[type="submit"], input[type="submit"], button:not([type])').count():
        return "no_submit"
    return None


def _form_has_captcha(form: Locator) -> bool:
    html = (form.inner_html() or "").lower()
    return any(marker in html for marker in CAPTCHA_MARKERS)


def _resolve_action(page_url: str, action: str | None) -> str | None:
    if not action:
        return page_url
    return urljoin(page_url, action)


def extract_forms_from_page(page: Page, page_url: str) -> list[SiteFormData]:
    forms: list[SiteFormData] = []
    form_locator = page.locator("form")

    for index in range(form_locator.count()):
        form = form_locator.nth(index)
        fields = _collect_fields(form, page_url)
        skip_reason = _should_skip_form(form, fields, page_url)
        has_captcha = _form_has_captcha(form)
        form_type = _detect_form_type(form, fields)
        form_id = form.get_attribute("id")
        form_selector = f"form#{form_id}" if form_id else f"form:nth-of-type({index + 1})"

        submit = form.locator('button[type="submit"], input[type="submit"]').first
        submit_selector = None
        if submit.count() > 0:
            submit_id = submit.get_attribute("id")
            submit_name = submit.get_attribute("name")
            if submit_id:
                submit_selector = f"#{submit_id}"
            elif submit_name:
                submit_selector = f'[name="{submit_name}"]'
            else:
                submit_selector = f"{form_selector} button[type='submit'], {form_selector} input[type='submit']"

        item = SiteFormData(
            page_url=page_url,
            form_index=index,
            form_action=_resolve_action(page_url, form.get_attribute("action")),
            form_method=(form.get_attribute("method") or "post").lower(),
            form_selector=form_selector,
            submit_selector=submit_selector,
            fields=fields,
            form_type=form_type,
            form_score=_score_form(fields, has_captcha=has_captcha, form_type=form_type),
            has_captcha=has_captcha,
            is_submittable=skip_reason is None and not has_captcha,
        )
        if skip_reason:
            item.is_submittable = False
            item.form_score = max(item.form_score - 40, 0)
        forms.append(item)

    forms.sort(key=lambda item: item.form_score, reverse=True)
    return forms


def collect_forms_for_url(page: Page, url: str) -> list[SiteFormData]:
    parsed = urlparse(url)
    if not parsed.scheme:
        url = f"https://{url.lstrip('/')}"
    base = f"{urlparse(url).scheme}://{urlparse(url).netloc}"

    discovered: list[SiteFormData] = []
    seen_keys: set[tuple[str, int]] = set()

    paths = ("", "/contacts", "/contact", "/kontakty", "/feedback", "/obratnaya-svyaz")
    for path in paths:
        target = url if not path else urljoin(base + "/", path.lstrip("/"))
        try:
            page.goto(target, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(800)
        except Exception:
            continue

        for form in extract_forms_from_page(page, target):
            key = (form.page_url, form.form_index)
            if key in seen_keys:
                continue
            seen_keys.add(key)
            discovered.append(form)

    return discovered


def form_to_dict(form: SiteFormData) -> dict:
    payload = asdict(form)
    payload["fields"] = [asdict(field) for field in form.fields]
    return payload


_CONTACT_PATHS = (
    "/contacts",
    "/contact",
    "/kontakty",
    "/feedback",
    "/obratnaya-svyaz",
)

_WORKABLE_TYPES = frozenset({"contact", "feedback", "callback", "order"})


def _form_workability_rank(form: dict) -> tuple:
    page_url = (form.get("page_url") or "").lower()
    path_rank = 0
    for index, path in enumerate(_CONTACT_PATHS):
        if path in page_url:
            path_rank = len(_CONTACT_PATHS) - index
            break

    fields = form.get("fields") or []
    return (
        1 if form.get("is_submittable") else 0,
        0 if form.get("has_captcha") else 1,
        form.get("form_score", 0),
        1 if form.get("form_type") in _WORKABLE_TYPES else 0,
        len(fields),
        path_rank,
    )


def pick_best_form(forms: list[dict]) -> dict | None:
    if not forms:
        return None
    return max(forms, key=_form_workability_rank)


def collect_forms_for_sites(
    sites: list[dict],
    *,
    headless: bool = True,
) -> list[dict]:
    from app.config import HEADLESS
    from app.scraper.resilient import run_with_proxy_retries

    output: list[dict] = []

    for site in sites:
        site_id = str(site["id"])
        url = site["url"]
        try:
            forms, _meta = run_with_proxy_retries(
                lambda page: collect_forms_for_url(page, url),
                headless=headless if headless is not None else HEADLESS,
                retry_on_any_error=True,
            )
            output.append(
                {
                    "site_id": site_id,
                    "url": url,
                    "domain": site.get("domain") or urlparse(url).netloc,
                    "status": "done",
                    "forms": [form_to_dict(form) for form in forms],
                    "error_message": None,
                }
            )
        except Exception as exc:
            output.append(
                {
                    "site_id": site_id,
                    "url": url,
                    "domain": site.get("domain") or urlparse(url).netloc,
                    "status": "failed",
                    "forms": [],
                    "error_message": str(exc),
                }
            )

    return output
