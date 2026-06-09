from __future__ import annotations

from app.config import LLM_RAW_TEXT_MAX_CHARS
from app.llm.client import chat_json

QUALIFY_SCHEMA = {
    "type": "object",
    "properties": {
        "is_fit": {"type": "boolean"},
        "score": {"type": "integer"},
        "business_type": {"type": "string"},
        "business_name": {"type": "string"},
        "city_guess": {"type": "string"},
        "services": {
            "type": "array",
            "items": {"type": "string"},
        },
        "reason": {"type": "string"},
        "red_flags": {
            "type": "array",
            "items": {"type": "string"},
        },
    },
    "required": [
        "is_fit",
        "score",
        "business_type",
        "business_name",
        "city_guess",
        "services",
        "reason",
        "red_flags",
    ],
    "additionalProperties": False,
}

SYSTEM_PROMPT = """Ты аналитик B2B-лидов для холодного outreach.
Оцени, подходит ли сайт для коммерческого предложения по критериям пользователя.
Будь консервативен: агрегаторы, каталоги, блоги, СМИ, маркетплейсы, чужие города — не подходят.
score: 0–100 (насколько сайт похож на целевой бизнес).
is_fit: true только если score >= 60 и нет критичных red_flags.
Отвечай только JSON по схеме."""


def _trim_text(value: str | None, limit: int = LLM_RAW_TEXT_MAX_CHARS) -> str:
    if not value:
        return ""
    text = value.strip()
    if len(text) <= limit:
        return text
    return text[:limit] + "…"


def qualify_site(
    site: dict,
    *,
    target_query: str = "стоматология",
    target_description: str = "стоматологическая клиника",
) -> dict:
    raw_text = _trim_text(site.get("raw_text"))
    serp_snippet = _trim_text(site.get("serp_snippet"), 500)
    meta = _trim_text(site.get("meta_description"), 500)

    user_prompt = f"""Целевая ниша: {target_query}
Описание целевого клиента: {target_description}

Сайт:
- URL: {site.get("url")}
- Домен: {site.get("domain")}
- Title (SERP): {site.get("title") or "—"}
- Title (страница): {site.get("page_title") or "—"}
- Meta: {meta or "—"}
- Город из поиска: {site.get("city") or "неизвестен"}
- Snippet из выдачи: {serp_snippet or "—"}
- Email: {", ".join(site.get("emails") or []) or "—"}
- Телефоны: {", ".join(site.get("phones") or []) or "—"}
- Есть форма обратной связи: {site.get("has_contact_form")}
- Текст со страницы:
{raw_text or "—"}
"""
    result = chat_json(
        system=SYSTEM_PROMPT,
        user=user_prompt,
        schema_name="site_qualify",
        schema=QUALIFY_SCHEMA,
    )
    result["score"] = max(0, min(100, int(result.get("score", 0))))
    if result["score"] < 60:
        result["is_fit"] = False
    return result
