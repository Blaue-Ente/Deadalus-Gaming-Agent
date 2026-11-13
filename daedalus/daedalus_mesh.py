"""Daedalus → MeshRouter bridge — unified routing, cost tracking, fallback.

Routes Daedalus workers, navigator hints, and chat fast-path through the
existing LLM Router (mesh_router) instead of direct Novita/OpenRouter SDK calls.
"""
from __future__ import annotations

import logging
import os
from typing import Any

log = logging.getLogger(__name__)

# Slot → mesh_router intent (fallback when no direct model override)
SLOT_INTENT: dict[str, str] = {
    "fast":     "cheap",
    "balanced": "chat",
    "deep":     "code",
    "analysis": "reasoning",
    "claude":   "chat",
    "planner":  "reasoning",
    "coder":    "code",
    "reviewer": "chat",
    "auto":     "chat",
}

_REASONING_MARKERS = ("r1", "qwq", "o4-mini", "o3-mini", "o4", "o3", "reasoning")


def use_mesh_router() -> bool:
    """True when ADN MeshRouter should be used.

    Default ON next to ADN, OFF in standalone (``DAEDALUS_STANDALONE=1``)
    unless ``DAEDALUS_USE_MESH_ROUTER=1`` is set explicitly.
    """
    raw = os.environ.get("DAEDALUS_USE_MESH_ROUTER")
    if raw is None:
        from .host import standalone_mode
        return not standalone_mode()
    return raw.strip().lower() not in ("0", "false", "no", "off")


def client_id(*parts: str) -> str:
    """Build tracked client_id, e.g. daedalus/worker/fast."""
    segs = ["daedalus", *[p for p in parts if p]]
    return "/".join(segs)


def extract_text(res: dict[str, Any] | None) -> str:
    """Pull assistant text from mesh_router Anthropic-compatible response."""
    if not isinstance(res, dict):
        return ""
    for blk in (res.get("content") or []):
        t = blk.get("text") or ""
        if t:
            return t
    for ch in (res.get("choices") or []):
        t = ((ch.get("message") or {}).get("content") or "")
        if t:
            return t
    return res.get("reply") or ""


def slot_to_intent(slot: str, model: str = "") -> str:
    if slot in SLOT_INTENT:
        intent = SLOT_INTENT[slot]
        if slot == "auto" and model:
            ml = model.lower()
            if any(m in ml for m in _REASONING_MARKERS):
                return "reasoning"
            if any(m in ml for m in ("coder", "code", "deepseek", "qwen3-coder")):
                return "code"
        return intent
    ml = (model or slot).lower()
    if any(m in ml for m in _REASONING_MARKERS):
        return "reasoning"
    if any(m in ml for m in ("coder", "code", "deepseek-v", "qwen3-coder")):
        return "code"
    if any(m in ml for m in ("flash", "mini", "ling", "lite", "nano")):
        return "cheap"
    return "chat"


_MESH_NATIVE_PROVIDERS = frozenset({"novita", "openrouter", "alltoken", "poolside", "nvidia", "anthropic", "openai"})


def should_route_worker_via_mesh(provider: str, model: str) -> bool:
    """True when mesh_router can honor the configured provider+model (or native gateway)."""
    if not use_mesh_router():
        return False
    if direct_model_override(provider, model) is not None:
        return True
    return (provider or "").lower().strip() in _MESH_NATIVE_PROVIDERS


def direct_model_override(provider: str, model: str) -> str | None:
    """Map Daedalus worker provider+model to mesh_router direct model string."""
    if not model:
        return None
    p = (provider or "").lower().strip()
    m = model.strip()
    if not m:
        return None

    if p in ("novita", "openrouter", "alltoken", "poolside", "nvidia"):
        return f"{p}:{m}"
    if p == "anthropic":
        bare = m.split("/")[-1] if "/" in m else m
        return f"anthropic:{bare}"
    if p == "openai":
        bare = m.split("/")[-1] if "/" in m else m
        return f"openai:{bare}"
    if p in ("groq", "gemini", "replit_gemini", "replit_openai", "custom"):
        # No native gateway — let intent routing pick the pool
        return None
    # Literal model id (e.g. deepseek/deepseek-v3.2) — OpenRouter passthrough
    if "/" in m:
        return m
    return None


async def _get_pool():
    try:
        from .host import get_pool
        return await get_pool()
    except Exception:
        return None


_PROVIDER_PREFIXES = (
    "novita:", "openrouter:", "alltoken:", "poolside:", "nvidia:",
    "openai:", "anthropic:", "groq:", "gemini:",
)


def bare_model(model: str | None) -> str | None:
    """Strip ``provider:`` prefix used by MeshRouter direct overrides."""
    if not model:
        return None
    lower = model.lower()
    for prefix in _PROVIDER_PREFIXES:
        if lower.startswith(prefix):
            return model[len(prefix):]
    return model


def _content_text(content: Any) -> str:
    """Readable text of structured content: text parts joined, images named, never a Python repr."""
    parts = content if isinstance(content, list) else [content]
    out: list[str] = []
    for part in parts:
        if isinstance(part, str):
            out.append(part)
        elif isinstance(part, dict):
            kind = str(part.get("type") or "")
            if kind == "text" or (not kind and "text" in part):
                out.append(str(part.get("text") or ""))
            elif kind in ("image", "image_url") or "image_url" in part:
                out.append("[image omitted: the text-only route cannot carry it]")
            else:
                out.append(str(part))
        else:
            out.append(str(part))
    return "\n".join(x for x in out if x)


def _split_messages(messages: list[dict[str, Any]]) -> tuple[str, str]:
    system_parts: list[str] = []
    user_parts: list[str] = []
    for msg in messages or []:
        role = (msg.get("role") or "user").lower()
        content = msg.get("content") or ""
        if not isinstance(content, str):
            content = _content_text(content)
        if role == "system":
            system_parts.append(content)
        else:
            user_parts.append(content)
    return "\n\n".join(system_parts), "\n\n".join(user_parts) or " "


async def complete(
    *,
    system: str,
    user: str,
    intent: str = "chat",
    client_id: str = "daedalus/unknown",
    max_tokens: int = 4096,
    temperature: float | None = None,
    model: str | None = None,
    use_cache: bool = True,
    json_mode: bool = False,
) -> dict[str, Any]:
    """Call mesh_router.route when ADN is the host; else OpenAI-compat directly."""
    if use_mesh_router():
        try:
            from argos_data_nexus.modules import mesh_router as mr

            messages: list[dict[str, str]] = []
            if system:
                messages.append({"role": "system", "content": system})
            messages.append({"role": "user", "content": user})

            pool = await _get_pool()
            return await mr.route(
                messages=messages,
                intent=intent,
                model=model,
                max_tokens=max_tokens,
                temperature=temperature,
                client_id=client_id,
                use_cache=use_cache,
                json_mode=json_mode,
                pool=pool,
            )
        except Exception as exc:
            log.warning("mesh_router unavailable (%s); standalone LLM", exc)

    from . import llm_client
    # Pass through full ``provider:model`` tags so standalone resolves the same gateway + key
    # as mesh_router (bare_model strips only for legacy id-only call sites).
    return await llm_client.complete(
        system=system,
        user=user,
        intent=intent,
        client_id=client_id,
        max_tokens=max_tokens,
        temperature=temperature,
        model=model,
        use_cache=use_cache,
        json_mode=json_mode,
    )


async def route(
    *,
    messages: list[dict[str, Any]],
    intent: str = "chat",
    model: str | None = None,
    max_tokens: int = 4096,
    temperature: float | None = None,
    client_id: str = "daedalus/unknown",
    use_cache: bool = True,
    json_mode: bool = False,
    pool: Any = None,
    **_ignored: Any,
) -> dict[str, Any]:
    """mesh_router.route-compatible entry used by Playground builders."""
    if use_mesh_router():
        try:
            from argos_data_nexus.modules import mesh_router as mr
            kwargs: dict[str, Any] = dict(
                messages=messages,
                intent=intent,
                model=model,
                max_tokens=max_tokens,
                temperature=temperature,
                client_id=client_id,
                use_cache=use_cache,
                json_mode=json_mode,
            )
            if pool is not None:
                kwargs["pool"] = pool
            return await mr.route(**kwargs)
        except Exception as exc:
            log.warning("mesh_router.route unavailable (%s); standalone LLM", exc)
    from .harness import gateway
    from .llm_client import messages_have_images

    if gateway.path_enabled("mesh") and messages_have_images(messages):
        # Images must reach the model as image parts. The text-only route below would drop them.
        result = await gateway.call(
            intent or "chat", messages, model_hint=model, max_tokens=max_tokens,
            temperature=temperature, json_mode=json_mode, use_cache=use_cache, client_id=client_id,
        )
        return gateway.to_legacy_openai(result)
    system, user = _split_messages(messages)
    return await complete(
        system=system,
        user=user,
        intent=intent or "chat",
        model=model,
        max_tokens=max_tokens,
        temperature=temperature,
        client_id=client_id,
        use_cache=use_cache,
        json_mode=json_mode,
    )


async def generate_image(**kwargs: Any) -> dict[str, Any]:
    if use_mesh_router():
        try:
            from argos_data_nexus.modules import mesh_router as mr
            return await mr.generate_image(**kwargs)
        except Exception as exc:
            log.warning("mesh_router.generate_image unavailable (%s); standalone", exc)
    from . import image_client
    return await image_client.generate_image(**kwargs)


def resolve_image_model(preferred: str = "") -> str:
    if use_mesh_router():
        try:
            from argos_data_nexus.modules import mesh_router as mr
            return mr.resolve_image_model(preferred)
        except Exception:
            pass
    from . import image_client
    return image_client.resolve_image_model(preferred)


async def complete_text(**kwargs: Any) -> str:
    """Convenience wrapper — returns assistant text only."""
    res = await complete(**kwargs)
    return extract_text(res)


async def worker_complete(
    *,
    slot: str,
    system: str,
    user: str,
    provider: str = "",
    model: str = "",
    max_tokens: int = 4096,
    temperature: float | None = None,
) -> dict[str, Any]:
    """Worker-shaped call with slot→intent mapping and daedalus/worker/* client_id."""
    intent = slot_to_intent(slot, model)
    direct = direct_model_override(provider, model)
    return await complete(
        system=system,
        user=user,
        intent=intent,
        model=direct,
        max_tokens=max_tokens,
        temperature=temperature,
        client_id=client_id("worker", slot),
        use_cache=False,  # workers are task-specific — avoid stale reuse
    )
