"""OpenAI-compatible LLM client for standalone Daedalus (no MeshRouter)."""
from __future__ import annotations

import json
import logging
from typing import Any

log = logging.getLogger(__name__)

_TOOL_MAX_TOKENS = 8192


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
    from .llm_config import get_config, openai_compat_headers

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

        extra = openai_compat_headers(provider)
        client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url or None,
            max_retries=1,
            default_headers=extra or None,
        )
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


async def call_with_tools(
    system: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    *,
    max_tokens: int = _TOOL_MAX_TOKENS,
) -> dict[str, Any]:
    """One native-tool round. Does not import memory/chroma.

    Returns the unified ``{stop_reason, content}`` shape used by
    ``run_native_discovery_pass`` (text + tool_use blocks).
    """
    from .llm_config import get_config, openai_compat_extra_body, openai_compat_headers
    from .local_llm import dummy_api_key, is_local_provider
    from .native_tools import openai_tool_specs, to_anthropic_messages, to_openai_messages

    cfg = get_config()
    api_key = cfg.get("api_key", "")
    base_url = cfg.get("base_url", "")
    model = cfg.get("model", "")
    sdk = cfg.get("sdk") or "openai"
    provider = cfg.get("provider") or ""
    if not api_key:
        if is_local_provider(provider) or cfg.get("local"):
            api_key = dummy_api_key(provider)
        else:
            return {"stop_reason": "end_turn", "content": []}

    if sdk == "anthropic":
        try:
            from anthropic import AsyncAnthropic
        except ImportError:
            return {"stop_reason": "end_turn", "content": []}
        kwargs: dict[str, Any] = dict(api_key=api_key, max_retries=0)
        if base_url:
            kwargs["base_url"] = base_url
        client = AsyncAnthropic(**kwargs)
        anthropic_tools = [
            {
                "name": t["name"],
                "description": t["description"],
                "input_schema": t["input_schema"],
            }
            for t in tools
        ]
        resp = await client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=to_anthropic_messages(messages),
            tools=anthropic_tools,
        )
        content = []
        for block in resp.content:
            if block.type == "text":
                content.append({"type": "text", "text": block.text})
            elif block.type == "tool_use":
                content.append({
                    "type": "tool_use",
                    "id": block.id,
                    "name": block.name,
                    "input": block.input,
                })
        return {"stop_reason": resp.stop_reason, "content": content}

    try:
        from openai import AsyncOpenAI
    except ImportError:
        return {"stop_reason": "end_turn", "content": []}
    extra = openai_compat_headers(provider)
    client = AsyncOpenAI(
        api_key=api_key,
        base_url=base_url or None,
        max_retries=1,
        default_headers=extra or None,
    )
    oa_messages = to_openai_messages(system, messages)
    create_kw: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": oa_messages,
        "tools": openai_tool_specs(tools),
        "tool_choice": "auto",
    }
    extra_body = openai_compat_extra_body(provider, with_tools=True)
    if extra_body:
        create_kw["extra_body"] = extra_body
    try:
        resp = await client.chat.completions.create(**create_kw)
    except Exception as exc:
        log.warning("tool LLM %s/%s failed: %s", provider, model, exc)
        return {
            "stop_reason": "end_turn",
            "content": [{"type": "text", "text": f"LLM error: {exc}"[:800]}],
        }
    choice = resp.choices[0] if resp.choices else None
    msg = choice.message if choice else None
    content: list[dict[str, Any]] = []
    text = getattr(msg, "content", None) if msg else None
    if isinstance(text, str) and text:
        content.append({"type": "text", "text": text})
    elif isinstance(text, list):
        for block in text:
            if isinstance(block, dict) and block.get("type") == "text" and block.get("text"):
                content.append({"type": "text", "text": str(block.get("text") or "")})
            elif isinstance(block, str) and block:
                content.append({"type": "text", "text": block})
    for tc in (getattr(msg, "tool_calls", None) or []):
        args: Any = {}
        raw_args = getattr(getattr(tc, "function", None), "arguments", "") or "{}"
        try:
            args = json.loads(raw_args)
        except json.JSONDecodeError:
            args = {"raw": raw_args}
        content.append({
            "type": "tool_use",
            "id": tc.id,
            "name": tc.function.name,
            "input": args,
        })
    stop = "tool_use" if any(b.get("type") == "tool_use" for b in content) else "end_turn"
    try:
        usage = getattr(resp, "usage", None)
        if usage:
            log.info(
                "tool LLM %s/%s stop=%s tools=%d prompt=%s completion=%s",
                provider, model, stop,
                sum(1 for b in content if b.get("type") == "tool_use"),
                getattr(usage, "prompt_tokens", None),
                getattr(usage, "completion_tokens", None),
            )
    except Exception:
        pass
    return {"stop_reason": stop, "content": content}
