"""OpenAI-compatible LLM client for standalone Daedalus (no MeshRouter)."""
from __future__ import annotations

import json
import logging
from typing import Any

log = logging.getLogger(__name__)

_TOOL_MAX_TOKENS = 12288


def is_no_image_support(exc: BaseException) -> bool:
    text = str(exc).lower()
    return (
        "image input" in text
        or "filter by image support" in text
        or "does not support image" in text
        or "vision is not supported" in text
        or "no endpoints found that support image" in text
    )


def _block_is_image(block: Any) -> bool:
    if not isinstance(block, dict):
        return False
    kind = str(block.get("type") or "")
    if kind in {"image_url", "image"}:
        return True
    return "image_url" in block and kind != "text"


def content_has_images(content: Any) -> bool:
    if isinstance(content, list):
        return any(_block_is_image(b) or content_has_images(b) for b in content)
    if isinstance(content, dict):
        if _block_is_image(content):
            return True
        return content_has_images(content.get("content"))
    return False


def messages_have_images(messages: list[dict[str, Any]]) -> bool:
    return any(content_has_images(m.get("content")) for m in messages)


def strip_images_from_content(content: Any) -> Any:
    if not isinstance(content, list):
        return content
    kept = [b for b in content if not _block_is_image(b)]
    if kept:
        return kept
    return (
        "Visual proof frame omitted — this model has no image input. "
        "Use the text summary of the proof (uncaught / blank / compile)."
    )


def strip_images_from_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for msg in messages:
        row = dict(msg)
        row["content"] = strip_images_from_content(msg.get("content"))
        out.append(row)
    return out


def _usage_dict(usage: Any) -> dict[str, Any]:
    from .llm_cost import parse_usage
    parsed = parse_usage(usage)
    out: dict[str, Any] = {
        "prompt_tokens": int(parsed.get("prompt_tokens") or 0),
        "completion_tokens": int(parsed.get("completion_tokens") or 0),
    }
    if parsed.get("cost_known"):
        out["cost_usd"] = parsed.get("cost_usd")
    return out


def _meter(resp: Any, *, model: str, role: str) -> dict[str, Any]:
    from .llm_cost import parse_usage
    from . import run_budget
    parsed = parse_usage(getattr(resp, "usage", None), resp)
    run_budget.note_usage(parsed, model=model, role=role)
    return parsed


def _budget_stop_complete(*, provider: str, model: str) -> dict[str, Any]:
    from . import run_budget
    text = run_budget.stop_text()
    out = _as_openai_response(text, provider=provider, model=model)
    out["ok"] = False
    out["budget_stopped"] = True
    return out


def _budget_stop_tools() -> dict[str, Any]:
    from . import run_budget
    text = run_budget.stop_text()
    return {
        "stop_reason": "end_turn",
        "content": [{"type": "text", "text": text}],
        "budget_stopped": True,
        "usage": {},
    }


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


def mark_cut_tool_input(args: Any, *, cut: bool, max_tokens: int) -> Any:
    """A tool call the model was still writing when the output limit hit is incomplete: say so in its input.

    Found live (2026-11-11): asked for a full game, the writer put it all in one write_file, hit the output limit, and
    the cut call failed with no reason the model could act on. ``native_tools`` refuses a marked call with the fix:
    write the game as several smaller files."""
    if not cut:
        return args
    out = dict(args) if isinstance(args, dict) else {"raw": str(args)}
    out["_cut_at_output_limit"] = int(max_tokens or 0)
    return out


def mark_empty_reply(out: dict[str, Any], *, max_tokens: int) -> dict[str, Any]:
    """An answer with no text is not a success. Found live (2026-11-11): reasoning models given a small max_tokens
    spend every token thinking and return nothing, and that came back as ok:true. ``truncated`` says the token
    budget ran out (the likely cause), so a caller can retry with more room or say so."""
    if str(out.get("reply") or "").strip():
        return out
    used = int(((out.get("usage") or {}).get("completion_tokens")) or 0)
    out["ok"] = False
    out["error"] = "empty_reply"
    out["truncated"] = bool(max_tokens and used >= int(max_tokens))
    return out


def _is_recoverable_llm_error(text: str) -> bool:
    """True when failing the primary call might succeed on another backend (credits, auth, missing model)."""
    t = (text or "").lower()
    return any(
        phrase in t
        for phrase in (
            "401", "403", "429", "rate", "limit", "unauthorized", "permission",
            "insufficient", "credit", "quota", "timeout", "timed out", "connection",
            "unavailable", "503", "502", "invalid_api_key", "does not exist",
            "not found", "incorrect api key", "no such model",
        )
    )


async def _complete_with_cfg(
    cfg: dict[str, Any],
    *,
    system: str,
    user: str,
    intent: str,
    client_id: str,
    max_tokens: int,
    temperature: float | None,
    json_mode: bool,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """One completion attempt with a fully-resolved cfg dict. Returns (openai-shaped dict, billed)."""
    from . import llm_routing_watchdog as routing_watchdog
    from .llm_config import openai_compat_extra_body, openai_compat_headers

    provider = cfg.get("provider") or "openai"
    api_key = cfg.get("api_key") or ""
    base_url = cfg.get("base_url") or ""
    sdk = cfg.get("sdk") or "openai"
    used_model = str(cfg.get("model") or "")

    if not api_key:
        from .llm_config import is_loopback_gateway
        from .local_llm import dummy_api_key, is_local_provider
        if is_local_provider(provider) or cfg.get("local") or is_loopback_gateway(provider):
            api_key = dummy_api_key(provider) if is_local_provider(provider) else (api_key or "omniroute")
        else:
            raise RuntimeError(
                "Няма API ключ. Задай ALLTOKEN_API_KEY, OPENROUTER_API_KEY, "
                "NOVITA_API_KEY или DAEDALUS_API_KEY, или пусни локален LLM "
                "(DAEDALUS_PROVIDER=ollama)."
            )

    from . import run_budget
    if run_budget.would_block(provider):
        return _budget_stop_complete(provider=provider, model=used_model), {}

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
        billed = _meter(resp, model=used_model, role="writer")
    else:
        from openai import AsyncOpenAI

        extra = openai_compat_headers(provider)
        extra_body = openai_compat_extra_body(provider, with_tools=False)
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
        if extra_body:
            body["extra_body"] = extra_body
        resp = await client.chat.completions.create(**body)
        text = (resp.choices[0].message.content if resp.choices else "") or ""
        used_model = getattr(resp, "model", None) or used_model
        billed = _meter(resp, model=used_model, role="writer")

    log.info(
        "standalone LLM %s/%s intent=%s client=%s chars=%d cost=%s",
        provider, used_model, intent, client_id, len(text),
        billed.get("cost_usd"),
    )
    out = _as_openai_response(text, provider=provider, model=used_model)
    if billed.get("cost_known"):
        out["cost_usd"] = billed.get("cost_usd") or 0.0
    out["usage"] = {
        "prompt_tokens": int(billed.get("prompt_tokens") or 0),
        "completion_tokens": int(billed.get("completion_tokens") or 0),
        "cost_usd": billed.get("cost_usd"),
    }
    mark_empty_reply(out, max_tokens=max_tokens)
    try:
        routing_watchdog.note_success(source="llm_client._complete_with_cfg")
    except Exception:
        pass
    return out, billed


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
    from . import llm_routing_watchdog as routing_watchdog
    from .llm_config import get_config
    from .local_llm import is_local_provider, resolve_local_config
    from .model_router import config_for_explicit_model

    base = get_config()
    cfg = config_for_explicit_model(base, model, source="llm_client.complete")
    err_note = str(cfg.get("routing_error") or "").strip()
    if err_note:
        log.warning("LLM routing: %s", err_note)
        try:
            routing_watchdog.note_failure(
                error=err_note,
                requested_model=model,
                source="llm_client.complete",
            )
        except Exception:
            pass
        nouser = (
            "Cannot use the requested model: " + err_note + " "
            "Fix credentials or use a local model (wardrobe / DAEDALUS_PROVIDER=ollama)."
        )
        out = _as_openai_response(
            nouser, provider=str(base.get("provider") or ""), model=str(model or ""),
        )
        out["ok"] = False
        out["routing_failed"] = True
        out["routing_error"] = err_note
        return out

    first_err: str | None = None
    try:
        out, _billed = await _complete_with_cfg(
            cfg, system=system, user=user, intent=intent, client_id=client_id,
            max_tokens=max_tokens, temperature=temperature, json_mode=json_mode,
        )
        return out
    except Exception as exc:
        first_err = str(exc)[:500]
        log.warning("LLM primary call failed: %s", first_err)

    # Fallback: explicit non-local request failed → try active local Ollama once (explicit only)
    if model and str(model).strip():
        try:
            prov_explicit, _mid_explicit = str(model).split(":", 1) if ":" in str(model) else ("", "")
            prov_explicit = prov_explicit.strip().lower()
            local_cfg = resolve_local_config("ollama")
            if (
                local_cfg
                and prov_explicit
                and not is_local_provider(prov_explicit)
                and _is_recoverable_llm_error(first_err or "")
            ):
                fb = dict(local_cfg)
                log.warning(
                    "LLM fallback to local Ollama after failure on %s (explicit request)",
                    model,
                )
                out, _b = await _complete_with_cfg(
                    fb, system=system, user=user, intent=intent, client_id=client_id + "/fallback-local",
                    max_tokens=max_tokens, temperature=temperature, json_mode=json_mode,
                )
                out["routing_fallback"] = "local_ollama"
                out["routing_fallback_reason"] = first_err or "primary_failed"
                out["requested_model"] = model
                return out
        except Exception as exc2:
            log.warning("LLM local fallback failed: %s", exc2)

    if first_err:
        try:
            routing_watchdog.note_failure(
                error=first_err,
                requested_model=model,
                source="llm_client.complete",
            )
        except Exception:
            pass
        raise RuntimeError(
            "LLM call failed for "
            f"{cfg.get('provider')}/{cfg.get('model')}: {first_err}. "
            "If this is a missing API key or credits, set the key or switch to a local model."
        ) from None
    raise RuntimeError("LLM call failed without detail")


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
    from . import llm_routing_watchdog as routing_watchdog
    from .llm_config import get_config, is_loopback_gateway, openai_compat_extra_body, openai_compat_headers
    from .local_llm import dummy_api_key, is_local_provider, normalize_provider
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
        elif is_loopback_gateway(provider):
            api_key = "omniroute"
        else:
            return {"stop_reason": "end_turn", "content": []}

    from . import run_budget
    if run_budget.would_block(provider):
        return _budget_stop_tools()

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
        _blocks = list(resp.content or [])
        _last_use = max((i for i, b in enumerate(_blocks) if b.type == "tool_use"), default=-1)
        for _i, block in enumerate(_blocks):
            if block.type == "text":
                content.append({"type": "text", "text": block.text})
            elif block.type == "tool_use":
                content.append({
                    "type": "tool_use",
                    "id": block.id,
                    "name": block.name,
                    "input": mark_cut_tool_input(block.input, cut=resp.stop_reason == "max_tokens" and _i == _last_use,
                                                 max_tokens=max_tokens),
                })
        billed = _meter(resp, model=model, role="writer")
        try:
            routing_watchdog.note_success(source="llm_client.call_with_tools")
        except Exception:
            pass
        return {
            "stop_reason": resp.stop_reason,
            "content": content,
            "usage": _usage_dict(getattr(resp, "usage", None)),
            "cost_usd": billed.get("cost_usd"),
            "cut_at_output_limit": max_tokens if resp.stop_reason == "max_tokens" else 0,
        }

    try:
        from openai import AsyncOpenAI
    except ImportError:
        return {"stop_reason": "end_turn", "content": []}
    if normalize_provider(provider) == "ollama":
        from .local_prompt import fit_ollama_turn
        fitted = fit_ollama_turn(
            system,
            tools,
            messages=messages,
            model=model,
            base_url=base_url,
            max_tokens=max_tokens,
        )
        system = fitted["system"]
        tools = list(fitted["tools"])
        messages = list(fitted["messages"])
        max_tokens = int(fitted["max_tokens"])

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
        if is_no_image_support(exc) and messages_have_images(oa_messages):
            log.warning("tool LLM %s/%s has no vision; retrying without proof frames", provider, model)
            create_kw["messages"] = strip_images_from_messages(oa_messages)
            try:
                resp = await client.chat.completions.create(**create_kw)
            except Exception as exc2:
                log.warning("tool LLM %s/%s failed: %s", provider, model, exc2)
                try:
                    routing_watchdog.note_failure(
                        error=str(exc2)[:800],
                        requested_model=f"{provider}:{model}",
                        source="llm_client.call_with_tools",
                    )
                except Exception:
                    pass
                return {
                    "stop_reason": "end_turn",
                    "content": [{"type": "text", "text": f"LLM error: {exc2}"[:800]}],
                }
        else:
            log.warning("tool LLM %s/%s failed: %s", provider, model, exc)
            try:
                routing_watchdog.note_failure(
                    error=str(exc)[:800],
                    requested_model=f"{provider}:{model}",
                    source="llm_client.call_with_tools",
                )
            except Exception:
                pass
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
    _calls = list(getattr(msg, "tool_calls", None) or [])
    _cut = getattr(choice, "finish_reason", "") == "length"
    for _i, tc in enumerate(_calls):
        args: Any = {}
        raw_args = getattr(getattr(tc, "function", None), "arguments", "") or "{}"
        try:
            args = json.loads(raw_args)
        except json.JSONDecodeError:
            args = {"raw": raw_args}
        # Only the call the limit interrupted (the last one) is incomplete; the earlier calls are whole.
        args = mark_cut_tool_input(args, cut=_cut and _i == len(_calls) - 1, max_tokens=max_tokens)
        content.append({
            "type": "tool_use",
            "id": tc.id,
            "name": tc.function.name,
            "input": args,
        })
    stop = "tool_use" if any(b.get("type") == "tool_use" for b in content) else "end_turn"
    billed = _meter(resp, model=model, role="writer")
    try:
        routing_watchdog.note_success(source="llm_client.call_with_tools")
    except Exception:
        pass
    try:
        log.info(
            "tool LLM %s/%s stop=%s tools=%d prompt=%s completion=%s cost=%s",
            provider, model, stop,
            sum(1 for b in content if b.get("type") == "tool_use"),
            billed.get("prompt_tokens"),
            billed.get("completion_tokens"),
            billed.get("cost_usd"),
        )
    except Exception:
        pass
    return {
        "stop_reason": stop,
        "content": content,
        "usage": _usage_dict(getattr(resp, "usage", None)),
        "cost_usd": billed.get("cost_usd"),
        "cut_at_output_limit": max_tokens if getattr(choice, "finish_reason", "") == "length" else 0,
    }
