from __future__ import annotations

from app.llm.client import chat_json

FORM_FILL_SCHEMA = {
    "type": "object",
    "properties": {
        "fields": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "selector": {"type": "string"},
                    "value": {"type": "string"},
                },
                "required": ["selector", "value"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["fields"],
    "additionalProperties": False,
}

SYSTEM_PROMPT = """Ты помогаешь заполнить HTML-форму на сайте.
Для каждого поля верни CSS-селектор и значение для ввода.
Используй только переданные селекторы. Не меняй селекторы.
Для checkbox/radio ставь "on" или "true" если нужно отметить согласие.
Телефон — в формате +7 (XXX) XXX-XX-XX если поле российское.
Отвечай только JSON по схеме."""


def _default_value(
    field: dict,
    *,
    sender_name: str,
    sender_phone: str,
    sender_email: str,
    message_text: str,
) -> str | None:
    purpose = field.get("purpose", "unknown")
    if purpose == "name":
        return sender_name
    if purpose == "phone":
        return sender_phone
    if purpose == "email":
        return sender_email
    if purpose in {"message", "subject"}:
        return message_text
    return None


def build_form_fill_plan(
    form_fields: list[dict],
    *,
    message_text: str,
    message_subject: str | None,
    sender_name: str,
    sender_phone: str,
    sender_email: str,
) -> list[dict]:
    known: list[dict] = []
    unknown: list[dict] = []

    for field in form_fields:
        if field.get("type") in {"hidden", "submit", "button", "reset", "file"}:
            continue
        purpose = field.get("purpose", "unknown")
        if purpose == "subject" and message_subject:
            known.append({"selector": field["selector"], "value": message_subject, "purpose": purpose})
            continue
        value = _default_value(
            field,
            sender_name=sender_name,
            sender_phone=sender_phone,
            sender_email=sender_email,
            message_text=message_text,
        )
        if value is not None and purpose != "unknown":
            known.append({"selector": field["selector"], "value": value, "purpose": purpose})
        else:
            unknown.append(field)

    if not unknown:
        return known

    field_lines = "\n".join(
        f"- selector={f['selector']}, type={f.get('type')}, purpose={f.get('purpose')}, "
        f"label={f.get('label') or '—'}, placeholder={f.get('placeholder') or '—'}, "
        f"required={f.get('required', False)}"
        for f in unknown
    )
    user_prompt = f"""Сообщение для отправки:
{message_text}

Отправитель: {sender_name}, {sender_email}, {sender_phone}
Тема (если нужна): {message_subject or "—"}

Поля без автозаполнения:
{field_lines}
"""
    llm_result = chat_json(
        system=SYSTEM_PROMPT,
        user=user_prompt,
        schema_name="form_fill",
        schema=FORM_FILL_SCHEMA,
    )
    by_selector = {item["selector"]: item["value"] for item in llm_result.get("fields", [])}
    for field in unknown:
        selector = field["selector"]
        if selector in by_selector:
            known.append(
                {
                    "selector": selector,
                    "value": by_selector[selector],
                    "purpose": field.get("purpose", "unknown"),
                }
            )
    return known
