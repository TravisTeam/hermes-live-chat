from __future__ import annotations

from openai import OpenAI

from .config import Settings, get_settings


def complete_turn(user_text: str, history: list[dict[str, str]] | None = None, settings: Settings | None = None) -> str:
    settings = settings or get_settings()
    history = history or []
    client = OpenAI(base_url=settings.llm_base_url, api_key=settings.llm_api_key)
    messages = [{"role": "system", "content": settings.system_prompt}, *history, {"role": "user", "content": user_text}]
    response = client.chat.completions.create(
        model=settings.llm_model,
        messages=messages,
        temperature=0.7,
        max_tokens=450,
    )
    return (response.choices[0].message.content or "").strip()
