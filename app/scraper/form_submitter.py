from __future__ import annotations

from playwright.sync_api import Page


def submit_form_on_page(
    page: Page,
    *,
    page_url: str,
    form_selector: str,
    submit_selector: str | None,
    fields: list[dict],
    dry_run: bool = True,
) -> dict:
    page.goto(page_url, wait_until="domcontentloaded", timeout=30_000)
    page.wait_for_timeout(800)

    filled: list[dict] = []
    for field in fields:
        selector = field["selector"]
        value = field["value"]
        locator = page.locator(selector).first
        if locator.count() == 0:
            raise RuntimeError(f"Поле не найдено на странице: {selector}")

        tag = locator.evaluate("el => el.tagName.toLowerCase()")
        input_type = (locator.get_attribute("type") or "text").lower()

        if tag == "select":
            locator.select_option(value=value)
        elif input_type in {"checkbox", "radio"}:
            if value.lower() in {"true", "on", "1", "yes"}:
                locator.check()
        elif input_type == "textarea" or tag == "textarea":
            locator.fill(value)
        else:
            locator.fill(value)

        filled.append({"selector": selector, "value": value})

    submitted = False
    if not dry_run:
        if submit_selector:
            submit = page.locator(submit_selector).first
        else:
            submit = page.locator(
                f"{form_selector} button[type='submit'], "
                f"{form_selector} input[type='submit'], "
                f"{form_selector} button:not([type])"
            ).first
        if submit.count() == 0:
            raise RuntimeError("Кнопка отправки не найдена")
        submit.click()
        page.wait_for_timeout(2_000)
        submitted = True

    return {
        "page_url": page_url,
        "dry_run": dry_run,
        "submitted": submitted,
        "fields_filled": len(filled),
        "filled": filled,
    }
