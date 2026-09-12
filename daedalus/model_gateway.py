"""Provider invoke + retries + metering + normalize.

Leaf module. Do not import agent.py. Cheap-path ROUTING and leftover NEED_*
if-arms stay on Daedalus. ToolLoop is the round driver. ModelGateway is
invoke then normalize.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)

MODEL = "claude-sonnet-4-6"
MAX_TOKENS = 8192


class DaedalusError(Exception):
    """Base error for Daedalus."""


@dataclass
class CompleteRequest:
    system: str
    messages: list[dict[str, Any]]
    use_cache: bool | None = None
    max_tokens: int | None = None
    intent: str = "chat"
    client_id: str = ""
    role: str = "writer"
    model: str | None = None
    json_mode: bool = False


@dataclass
class CompleteResult:
    text: str
    provider: str = ""
    model: str = ""
    cost_usd: float | None = None
    usage: dict[str, Any] = field(default_factory=dict)
    ok: bool = True
    budget_stopped: bool = False
    routed_via: str = ""
    raw: Any = None


@dataclass
class ToolRoundRequest:
    system: str
    messages: list[dict[str, Any]]
    tools: list[dict[str, Any]]
    max_tokens: int = MAX_TOKENS


# Fallback chain: (label, model, sdk, base_url_env, api_key_env)
FALLBACK_CHAIN: list[tuple[str, str, str, str, str]] = [
    # (label, model, sdk, base_url_env_OR_literal_https_url, api_key_env)
    # If base_url field starts with "https://", it is used as-is (literal URL).
    # Otherwise it is treated as an env var name.
    # NOTE: Replit AI Integrations (replit_openai / replit_gemini) are intentionally
    # excluded — ADN runs only on external keys (OpenRouter + Novita).
    # OpenRouter — Claude Sonnet as first external fallback (proven protocol follower)
    ("openrouter_claude", "anthropic/claude-sonnet-4-5", "openai",
     "https://openrouter.ai/api/v1", "OPENROUTER_API_KEY"),
    # Novita — cheapest meaningful fallback: DeepSeek V3.2 ($0.30/$0.90)
    # Tested ✅: deepseek/deepseek-v3.2, qwen/qwen3-235b-a22b-instruct-2507,
    #            qwen/qwen3-coder-30b-a3b-instruct, meta-llama/llama-3.1-8b-instruct
    ("novita", "deepseek/deepseek-v3.2", "openai",
     "https://api.novita.ai/v3/openai", "NOVITA_API_KEY"),
]


def _is_rate_limit_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    return ("429" in msg or "rate" in msg or "too many requests" in msg
            or "ratelimit" in msg or "overloaded" in msg or "529" in msg)


async def call_with_fallback(primary_call_fn, system: str,
                             messages: list[dict], **kwargs) -> str:
    """Try primary; on RateLimitError fall back to alt providers in order."""
    try:
        return await primary_call_fn(system, messages, **kwargs)
    except Exception as exc:
        if not _is_rate_limit_error(exc):
            raise
        try:
            from .improvements import record_rate_limit_hit
            record_rate_limit_hit("primary")
        except Exception:
            pass
        log.warning("RateLimitFallback: primary rate-limited (%s); trying alternates",
                    str(exc)[:120])

    for label, model, sdk, burl_env, akey_env in FALLBACK_CHAIN:
        try:
            # Support literal "https://..." URLs directly in the chain
            base_url = burl_env if burl_env.startswith("https://") else os.environ.get(burl_env, "")
            api_key = os.environ.get(akey_env, "")
            if not (base_url and api_key):
                log.debug("RateLimitFallback: skip %s (missing env)", label)
                continue
            from openai import AsyncOpenAI
            c = AsyncOpenAI(api_key=api_key, base_url=base_url)
            is_reasoning = model.startswith(("gpt-5", "o4", "o3"))
            k: dict = {
                "model": model,
                "messages": [{"role": "system", "content": system}] + messages,
            }
            if is_reasoning:
                k["max_completion_tokens"] = 4000
            else:
                k["max_tokens"] = 4000
            r = await c.chat.completions.create(**k)
            txt = r.choices[0].message.content or ""
            log.info("RateLimitFallback: served by %s/%s (%d chars)", label, model, len(txt))
            try:
                from .improvements import record_fallback_use
                record_fallback_use(label)
            except Exception:
                pass
            try:
                from argos_data_nexus.modules.llm_live_trace import record_llm_call
                record_llm_call(
                    channel="daedalus_fallback",
                    provider=label,
                    model=model,
                    model_requested="primary_rate_limited",
                    configured_model="(fallback)",
                    config_source="fallback_chain",
                    client_id="daedalus",
                    ok=True,
                    extra={"fallback_label": label},
                )
            except Exception:
                pass
            return f"[fallback:{label}] {txt}" if txt else txt
        except Exception as exc2:
            log.warning("RateLimitFallback: %s/%s failed: %s", label, model, exc2)
            if _is_rate_limit_error(exc2):
                try:
                    from .improvements import record_rate_limit_hit
                    record_rate_limit_hit(label)
                except Exception:
                    pass
            continue

    raise RuntimeError("Rate-limited by primary and all fallbacks failed")


async def complete_primary(system: str, messages: list[dict[str, Any]],
                           *,
                           use_cache_override: bool | None = None) -> str:
    """Direct provider call (the original logic). Used by call_with_fallback."""
    import time as _t
    try:
        from argos_data_nexus.modules.llm_live_trace import record_llm_call
    except Exception:
        def record_llm_call(*_a, **_k):  # type: ignore
            return None

    from .llm_config import get_config

    cfg = get_config()
    provider = cfg.get("provider", "anthropic")
    model = cfg.get("model", MODEL)
    api_key = cfg.get("api_key", "")
    base_url = cfg.get("base_url", "")
    sdk = cfg.get("sdk", "anthropic")
    do_cache = cfg.get("use_cache", True) if use_cache_override is None else use_cache_override
    t0 = _t.perf_counter()
    err = ""
    try:
        if sdk == "anthropic":
            try:
                from anthropic import AsyncAnthropic
            except ImportError as exc:
                raise DaedalusError("anthropic SDK not installed") from exc
            if not api_key:
                raise DaedalusError(
                    "Anthropic API key not configured "
                    "(set DAEDALUS_API_KEY env var or configure in Admin › LLM)"
                )
            kwargs: dict = dict(api_key=api_key, max_retries=0)
            if base_url:
                kwargs["base_url"] = base_url

            client = AsyncAnthropic(**kwargs)

            if do_cache:
                system_param: list | str = [
                    {"type": "text", "text": system,
                     "cache_control": {"type": "ephemeral"}}
                ]
            else:
                system_param = system

            from .native_tools import to_anthropic_messages as _to_anth_msgs

            resp = await client.messages.create(
                model=model,
                max_tokens=MAX_TOKENS,
                system=system_param,
                messages=_to_anth_msgs(messages),
            )
            block = resp.content[0]
            return block.text if block.type == "text" else ""

        # OpenAI-compatible (openai SDK) — Groq, Gemini, OpenAI, custom
        try:
            from openai import AsyncOpenAI
        except ImportError as exc:
            raise DaedalusError(
                "openai SDK not installed — run: pip install openai"
            ) from exc
        if not api_key:
            from .local_llm import dummy_api_key, is_local_provider
            if is_local_provider(provider) or cfg.get("local"):
                api_key = dummy_api_key(provider)
            else:
                raise DaedalusError(
                    f"API key not configured for provider '{provider}' "
                    "(set DAEDALUS_API_KEY env var or configure in Admin › LLM, "
                    "or DAEDALUS_PROVIDER=ollama for a local model)"
                )
        from .llm_config import openai_compat_headers
        oa_client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url or None,
            max_retries=1,
            default_headers=(openai_compat_headers(provider) or None),
        )
        oa_messages = [{"role": "system", "content": system}] + list(messages)
        resp = await oa_client.chat.completions.create(
            model=model,
            max_tokens=MAX_TOKENS,
            messages=oa_messages,
        )
        return resp.choices[0].message.content or ""
    except Exception as exc:
        err = str(exc)[:300]
        raise
    finally:
        try:
            record_llm_call(
                channel="daedalus_chat",
                provider=provider,
                model=model,
                model_requested=model,
                configured_provider=provider,
                configured_model=model,
                config_source=cfg.get("source", ""),
                client_id="daedalus",
                latency_ms=int((_t.perf_counter() - t0) * 1000),
                ok=not err,
                error=err,
            )
        except Exception:
            pass


async def complete_persona(system: str, messages: list[dict[str, Any]],
                           *,
                           use_cache_override: bool | None = None) -> str:
    """Unified async LLM call with rate-limit fallback chain.

    Tries primary provider first; on 429 / overload falls back to
    replit_openai gpt-5.4 → replit_gemini gemini-2.5-flash automatically.
    Cheap writers (DeepSeek / MiniMax / GLM) often have no image input —
    a proof PNG then 404s. Retry the same turn without vision; the text
    proof summary is enough for RETURN TO WRITER.
    """
    from .llm_client import (
        is_no_image_support,
        messages_have_images,
        strip_images_from_messages,
    )

    try:
        return await call_with_fallback(
            complete_primary, system, messages,
            use_cache_override=use_cache_override,
        )
    except Exception as exc:
        if is_no_image_support(exc) and messages_have_images(messages):
            log.warning("persona LLM has no vision; retrying without proof frames")
            stripped = strip_images_from_messages(messages)
            return await call_with_fallback(
                complete_primary, system, stripped,
                use_cache_override=use_cache_override,
            )
        raise


async def complete_with_tools(
    system: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
) -> dict[str, Any]:
    """Native tool round. Implementation lives in ``llm_client`` (no chroma)."""
    from .llm_client import call_with_tools
    return await call_with_tools(system, messages, tools, max_tokens=MAX_TOKENS)


def _user_text(messages: list[dict[str, Any]]) -> str:
    for msg in reversed(messages or []):
        if (msg.get("role") or "user").lower() != "user":
            continue
        content = msg.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = [
                str(b.get("text") or "")
                for b in content
                if isinstance(b, dict) and b.get("type") == "text"
            ]
            return "\n".join(p for p in parts if p)
        if content is None:
            return ""
        return str(content)
    return ""


async def complete_cheap(
    *,
    system: str,
    user: str,
    intent: str = "chat",
    max_tokens: int = 1200,
    client_id: str = "",
    use_cache: bool = True,
    include_master_config: bool = True,
    swallow_mesh_error: bool = False,
) -> CompleteResult:
    """mesh.complete → optional master get_config OpenAI → resolve_cheap_openai.

    Empty text is a miss, not an exception. Mesh errors propagate unless
    ``swallow_mesh_error`` (wrap-up). Master-config errors are always debug-logged.
    """
    from . import daedalus_mesh as dm

    cid = client_id or dm.client_id("chat", "fast-path")
    text = ""

    if dm.use_mesh_router():
        try:
            mesh_res = await dm.complete(
                system=system,
                user=user,
                intent=intent,
                max_tokens=max_tokens,
                client_id=cid,
                use_cache=use_cache,
            )
            text = dm.extract_text(mesh_res)
            if text:
                return CompleteResult(
                    text=text,
                    provider=str(mesh_res.get("provider") or ""),
                    model=str(mesh_res.get("model") or ""),
                    cost_usd=mesh_res.get("cost_usd"),
                    routed_via="mesh",
                    raw=mesh_res,
                )
        except Exception as exc:
            if not swallow_mesh_error:
                raise
            log.debug("empty-reply guard mesh_router failed: %s", exc)

    if include_master_config and not text:
        from .llm_config import get_config
        try:
            mcfg = get_config()
            if mcfg.get("api_key") and mcfg.get("sdk") == "openai":
                from openai import AsyncOpenAI
                hc = AsyncOpenAI(
                    api_key=mcfg["api_key"],
                    base_url=mcfg.get("base_url") or None,
                    max_retries=1,
                )
                hr = await hc.chat.completions.create(
                    model=mcfg.get("model", ""),
                    max_tokens=max_tokens,
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                )
                text = hr.choices[0].message.content or ""
                if text:
                    return CompleteResult(
                        text=text,
                        provider=str(mcfg.get("provider") or ""),
                        model=str(getattr(hr, "model", None) or mcfg.get("model") or ""),
                        routed_via="master-config",
                        raw=hr,
                    )
        except Exception as cfg_exc:
            log.debug("chat fast-path master-config fallback failed: %s", cfg_exc)

    if not text:
        from openai import AsyncOpenAI
        from .cheap_llm import resolve_cheap_openai
        cheap = resolve_cheap_openai()
        if cheap:
            hc = AsyncOpenAI(
                api_key=cheap["api_key"],
                base_url=cheap["base_url"],
                max_retries=1,
            )
            hr = await hc.chat.completions.create(
                model=cheap["model"],
                max_tokens=max_tokens,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            )
            text = hr.choices[0].message.content or ""
            if text:
                return CompleteResult(
                    text=text,
                    provider="",
                    model=str(cheap.get("model") or ""),
                    routed_via="cheap",
                    raw=hr,
                )

    return CompleteResult(text=text or "", routed_via="")


class ModelGateway:
    """Façade over persona / cheap / tool-round invoke. Stateless."""

    async def complete(self, req: CompleteRequest) -> CompleteResult:
        if req.role == "planner":
            text = await complete_primary(
                req.system, req.messages, use_cache_override=req.use_cache,
            )
            return CompleteResult(text=text, routed_via="primary")
        text = await complete_persona(
            req.system, req.messages, use_cache_override=req.use_cache,
        )
        return CompleteResult(text=text, routed_via="primary")

    async def complete_cheap(
        self,
        req: CompleteRequest,
        *,
        include_master_config: bool = True,
        swallow_mesh_error: bool = False,
    ) -> CompleteResult:
        user = _user_text(req.messages)
        use_cache = True if req.use_cache is None else req.use_cache
        return await complete_cheap(
            system=req.system,
            user=user,
            intent=req.intent,
            max_tokens=req.max_tokens if req.max_tokens is not None else 1200,
            client_id=req.client_id,
            use_cache=use_cache,
            include_master_config=include_master_config,
            swallow_mesh_error=swallow_mesh_error,
        )

    async def complete_tools(self, req: ToolRoundRequest) -> dict[str, Any]:
        return await complete_with_tools(req.system, req.messages, req.tools)
