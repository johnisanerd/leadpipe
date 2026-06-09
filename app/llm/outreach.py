from __future__ import annotations

from app.config import LLM_RAW_TEXT_MAX_CHARS
from app.llm.client import chat_json

OUTREACH_SCHEMA = {
    "type": "object",
    "properties": {
        "message_subject": {"type": "string"},
        "message_text": {"type": "string"},
        "personalization_hook": {"type": "string"},
    },
    "required": ["message_subject", "message_text", "personalization_hook"],
    "additionalProperties": False,
}

SYSTEM_PROMPT = """Ты копирайтер B2B cold outreach на русском языке.
Пиши коротко, по делу, без канцелярита и без явного спама.
Не выдумывай факты о клинике — используй только данные из контекста.
Если данных мало, пиши нейтрально, без конкретных услуг.
message_text: 3–6 предложений, готово к отправке в форму или email.
message_subject: до 80 символов.
Отвечай только JSON по схеме."""


def _trim_text(value: str | None, limit: int = LLM_RAW_TEXT_MAX_CHARS) -> str:
    if not value:
        return ""
    text = value.strip()
    if len(text) <= limit:
        return text
    return text[:limit] + "…"


def generate_outreach_message(
    site: dict,
    *,
    offer_description: str,
    sender_name: str,
    sender_company: str,
    call_to_action: str = "Ответьте на сообщение или позвоните — обсудим детали.",
) -> dict:
    qualify = site.get("qualify") or {}
    services = qualify.get("services") or []
    business_name = qualify.get("business_name") or site.get("page_title") or site.get("domain")

    user_prompt = f"""Наше предложение:
{offer_description}

Отправитель: {sender_name}, {sender_company}
Призыв к действию: {call_to_action}

Получатель (клиника/сайт):
- Название: {business_name}
- URL: {site.get("url")}
- Город: {site.get("city") or qualify.get("city_guess") or "—"}
- Тип бизнеса: {qualify.get("business_type") or "—"}
- Услуги (если известны): {", ".join(services) if services else "—"}
- Title: {site.get("page_title") or site.get("title") or "—"}
- Краткое описание: {_trim_text(site.get("meta_description"), 400) or "—"}
- Фрагмент текста сайта:
{_trim_text(site.get("raw_text"), 2000) or "—"}
"""
    return chat_json(
        system=SYSTEM_PROMPT,
        user=user_prompt,
        schema_name="outreach_message",
        schema=OUTREACH_SCHEMA,
    )
