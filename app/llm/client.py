from __future__ import annotations

import json
from typing import Any

from openai import OpenAI

from app.config import OPENAI_API_KEY, OPENAI_BASE_URL, OPENAI_MODEL


class LLMError(RuntimeError):
    pass


def require_llm() -> None:
    if not OPENAI_API_KEY:
        raise LLMError(
            "OPENAI_API_KEY не задан. Скопируйте .env.example → .env и укажите ключ API."
        )


def chat_json(
    *,
    system: str,
    user: str,
    schema_name: str,
    schema: dict[str, Any],
) -> dict[str, Any]:
    require_llm()
    client = OpenAI(api_key=OPENAI_API_KEY, base_url=OPENAI_BASE_URL)
    response = client.chat.completions.create(
        model=OPENAI_MODEL,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        response_format={
            "type": "json_schema",
            "json_schema": {
                "name": schema_name,
                "strict": True,
                "schema": schema,
            },
        },
    )
    content = response.choices[0].message.content
    if not content:
        raise LLMError("Пустой ответ LLM")
    return json.loads(content)
