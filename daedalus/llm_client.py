"""OpenAI-compatible LLM client for standalone Daedalus (no MeshRouter)."""
from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)


def _as_openai_response(text: str, *, provider: str, model: str) -> dict[str, Any]:
    return {
        "ok": True,
        "provider": provider,
        "model": model,
        "cost_usd": 0.0,
        "reply": text,
        "content": [{"type": "text", "text": text}],
        "choices": [{"message": {"role": "assistant", "content": text}}],
    }


async def complete(
    *,
    system: str = "",
    user: str = "",
    intent: str = "chat",
    client_id: str = "daedalus/standalone",
    max_tokens: int = 4096,
    temperature: float | None = None,
    model: str | None = None,
    use_cache: bool = True,
    json_mode: bool = False,
) -> dict[str, Any]:
    """Call the configured OpenAI-compatible (or Anthropic) provider directly."""
    from .llm_config import get_config

    cfg = get_config()
    provider = cfg.get("provider") or "openai"
    api_key = cfg.get("api_key") or ""
    base_url = cfg.get("base_url") or ""
    sdk = cfg.get("sdk") or "openai"
    used_model = model or cfg.get("model") or ""
    if not api_key:
        from .local_llm import dummy_api_key, is_local_provider
        if is_local_provider(provider) or cfg.get("local"):
            api_key = dummy_api_key(provider)
        else:
            raise RuntimeError(
                "Няма API ключ. Задай ALLTOKEN_API_KEY, OPENROUTER_API_KEY, "
                "NOVITA_API_KEY или DAEDALUS_API_KEY, или пусни локален LLM "
                "(DAEDALUS_PROVIDER=ollama)."
            )

    messages: list[dict[str, str]] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": user})

    text = ""
    if sdk == "anthropic":
        from anthropic import AsyncAnthropic

        client = AsyncAnthropic(api_key=api_key, base_url=base_url or None, max_retries=1)
        kwargs: dict[str, Any] = {
            "model": used_model,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": user}],
        }
        if system:
            kwargs["system"] = system
        if temperature is not None:
            kwargs["temperature"] = temperature
        resp = await client.messages.create(**kwargs)
        text = "".join(getattr(b, "text", "") or "" for b in (resp.content or []))
    else:
        from openai import AsyncOpenAI

        client = AsyncOpenAI(api_key=api_key, base_url=base_url or None, max_retries=1)
        body: dict[str, Any] = {
            "model": used_model,
            "messages": messages,
            "max_tokens": max_tokens,
        }
        if temperature is not None:
            body["temperature"] = temperature
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        resp = await client.chat.completions.create(**body)
        text = (resp.choices[0].message.content if resp.choices else "") or ""
        used_model = getattr(resp, "model", None) or used_model

    log.info(
        "standalone LLM %s/%s intent=%s client=%s chars=%d",
        provider, used_model, intent, client_id, len(text),
    )
    return _as_openai_response(text, provider=provider, model=used_model)
