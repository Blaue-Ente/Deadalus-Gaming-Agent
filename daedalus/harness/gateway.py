"""LLM Gateway: one entry point for model calls.

``call(intent, messages, ...)`` resolves *which* model serves an intent (env lock, owner override,
brain config, registry default), refuses once the run budget is spent, sends text **and images**
as real image parts (never ``str()``-ed), meters the spend, classifies failures into typed errors
and emits one trace event per call. It never returns made-up reply text: a failure is
``ok=False`` with an ``error``; legacy call sites keep their old text through the shims at the
bottom of this module.

Message content is a ``str`` or a list of parts:
``{"type": "text", "text": ...}`` and ``{"type": "image", "media_type": ..., "data": <base64>}`` (or
``"path"``). OpenAI ``image_url`` data URLs and Anthropic ``image`` blocks are accepted and
normalised, so existing callers need no change.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import os
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

log = logging.getLogger(__name__)

# --- intents and where each one gets its model ---------------------------------------------------
INTENTS = (
    "persona", "writer", "tools", "mentor", "critic", "planner", "navigator",
    "summary", "scout", "asset", "worker", "test",
)
# The owner can pick a model for these in Settings. "persona" shares the writer's selector.
OVERRIDABLE = ("writer", "mentor", "critic", "planner")
_SELECTOR = {"persona": "writer"}

# Registry defaults for intents that today hard-code a model. Others follow the brain config.
# The same constants the call sites used before; now declared once and owner-visible.
INTENT_DEFAULTS: dict[str, str] = {
    "scout": "alltoken:deepseek-v3.2",
    "asset": "alltoken:deepseek-v3.2",
}

KV_OVERRIDES = "adn:daedalus_intent_models"

# Legacy call-site intent names -> gateway intent (``mesh.route(intent="vision")`` is the critic).
_LEGACY_INTENT = {"vision": "critic", "chat": "persona", "json": "planner", "plan": "planner", "code": "writer"}


def canonical_intent(intent: str) -> str:
    low = (intent or "").strip().lower()
    low = _LEGACY_INTENT.get(low, low)
    return low or "persona"


def path_enabled(path: str) -> bool:
    """Rollback flag per migrated call path: ``DAEDALUS_GATEWAY_<PATH>=0`` restores the old code."""
    raw = (os.environ.get(f"DAEDALUS_GATEWAY_{path.upper()}") or "").strip().lower()
    return raw not in {"0", "false", "off", "no"}


# --- typed errors --------------------------------------------------------------------------------
ERROR_TYPES = ("timeout", "auth", "quota", "budget_blocked", "provider_error", "unsupported_modality",
               "routing", "invalid_request")


@dataclass(frozen=True)
class GatewayError:
    type: str
    message: str

    def as_dict(self) -> dict[str, str]:
        return {"type": self.type, "message": self.message}


class GatewayCallError(RuntimeError):
    """Raised by the legacy shims so existing ``except`` / rate-limit string checks keep working."""

    def __init__(self, error: GatewayError):
        super().__init__(error.message)
        self.error = error


def classify_error(exc: BaseException) -> GatewayError:
    from ..llm_client import is_no_image_support

    text = str(exc)[:500]
    low = text.lower()
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)) or "timed out" in low or "timeout" in low:
        return GatewayError("timeout", text)
    if is_no_image_support(exc):
        return GatewayError("unsupported_modality", text)
    if "401" in low or "403" in low or "unauthorized" in low or "invalid_api_key" in low or "incorrect api key" in low:
        return GatewayError("auth", text)
    if "429" in low or "rate limit" in low or "quota" in low or "insufficient" in low or "credit" in low:
        return GatewayError("quota", text)
    return GatewayError("provider_error", text)


# --- parts ---------------------------------------------------------------------------------------
def text_part(text: str) -> dict[str, Any]:
    return {"type": "text", "text": str(text)}


def image_part(*, media_type: str = "image/png", data: str | bytes | None = None,
               path: str | Path | None = None) -> dict[str, Any]:
    """An image part with its sha256 (of the raw bytes), from base64 ``data`` or a file ``path``."""
    if data is None and path is not None:
        raw = Path(path).read_bytes()
    elif isinstance(data, bytes):
        raw = data
    elif isinstance(data, str):
        raw = base64.b64decode(data)
    else:
        raise ValueError("image_part needs data or path")
    return {
        "type": "image",
        "media_type": media_type or "image/png",
        "data": base64.b64encode(raw).decode("ascii"),
        "sha256": hashlib.sha256(raw).hexdigest(),
    }


_DATA_URL = re.compile(r"^data:([\w/+.\-]+);base64,(.*)$", re.S)


def _normalise_block(block: Any) -> dict[str, Any] | None:
    if isinstance(block, str):
        return text_part(block)
    if not isinstance(block, dict):
        return text_part(str(block))
    kind = str(block.get("type") or "")
    if kind == "text" or (not kind and "text" in block):
        return text_part(block.get("text") or "")
    if kind == "image":
        src = block.get("source")
        if isinstance(src, dict) and src.get("type") == "base64":  # Anthropic shape
            return image_part(media_type=str(src.get("media_type") or "image/png"), data=str(src.get("data") or ""))
        if block.get("data") or block.get("path"):
            return image_part(media_type=str(block.get("media_type") or "image/png"),
                              data=block.get("data"), path=block.get("path"))
        return None
    if kind == "image_url" or "image_url" in block:  # OpenAI shape
        ref = block.get("image_url")
        url = ref.get("url") if isinstance(ref, dict) else ref
        m = _DATA_URL.match(str(url or ""))
        if m:
            return image_part(media_type=m.group(1), data=m.group(2))
        return None  # remote URLs are not fetched: the gateway only sends bytes it holds
    return text_part(json.dumps(block, ensure_ascii=False, default=str)) if block else None


def normalise_content(content: Any) -> str | list[dict[str, Any]]:
    """``str`` stays ``str``; a list becomes canonical parts (no stringified images)."""
    if isinstance(content, str) or content is None:
        return content or ""
    if isinstance(content, list):
        parts = [p for p in (_normalise_block(b) for b in content) if p is not None]
        return parts if any(p["type"] == "image" for p in parts) else (
            "\n".join(p["text"] for p in parts) if parts else "")
    one = _normalise_block(content)
    return one["text"] if one and one["type"] == "text" else ([one] if one else "")


def normalise_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{"role": str(m.get("role") or "user"), "content": normalise_content(m.get("content"))}
            for m in (messages or [])]


def image_hashes(messages: list[dict[str, Any]]) -> list[str]:
    out: list[str] = []
    for m in messages:
        content = m.get("content")
        if isinstance(content, list):
            out.extend(p["sha256"] for p in content if p.get("type") == "image" and p.get("sha256"))
    return out


def _to_openai(messages: list[dict[str, Any]], system: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = [{"role": "system", "content": system}] if system else []
    for m in messages:
        content = m["content"]
        if isinstance(content, list):
            blocks: list[dict[str, Any]] = []
            for p in content:
                if p["type"] == "image":
                    blocks.append({"type": "image_url",
                                   "image_url": {"url": f"data:{p['media_type']};base64,{p['data']}"}})
                else:
                    blocks.append({"type": "text", "text": p["text"]})
            content = blocks
        out.append({"role": m["role"], "content": content})
    return out


def _to_anthropic(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for m in messages:
        content = m["content"]
        if isinstance(content, list):
            blocks = []
            for p in content:
                if p["type"] == "image":
                    blocks.append({"type": "image", "source": {
                        "type": "base64", "media_type": p["media_type"], "data": p["data"]}})
                else:
                    blocks.append({"type": "text", "text": p["text"]})
            content = blocks
        out.append({"role": "assistant" if m["role"] == "assistant" else "user", "content": content})
    return out


def _split_system(messages: list[dict[str, Any]], system: str) -> tuple[str, list[dict[str, Any]]]:
    sys_parts = [system] if system else []
    rest: list[dict[str, Any]] = []
    for m in messages:
        if m["role"] == "system":
            c = m["content"]
            sys_parts.append(c if isinstance(c, str) else "\n".join(p.get("text", "") for p in c))
        else:
            rest.append(m)
    return "\n\n".join(p for p in sys_parts if p), rest


# --- model resolution ----------------------------------------------------------------------------
@dataclass(frozen=True)
class Resolution:
    intent: str
    provider: str
    model: str
    source: str               # call-site | env-lock | owner-override | registry-default | brain | slot
    locked: bool = False
    routing_error: str = ""
    cfg: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)

    def public(self) -> dict[str, Any]:
        return {"intent": self.intent, "provider": self.provider, "model": self.model,
                "source": self.source, "locked": self.locked, "routing_error": self.routing_error or None}


def model_locked_by_env() -> bool:
    return bool((os.environ.get("DAEDALUS_MODEL") or "").strip())


def get_overrides() -> dict[str, str]:
    try:
        from .. import kv
        raw = kv.get(KV_OVERRIDES)
        data = json.loads(raw) if isinstance(raw, str) else raw
    except Exception:
        return {}
    return {k: str(v) for k, v in (data or {}).items() if k in OVERRIDABLE and str(v).strip()}


def set_override(intent: str, model_tag: str) -> dict[str, str]:
    """Save (or clear with an empty tag) the owner's model for a core intent."""
    from ..env_lock import EnvLockedError
    from ..model_router import parse_provider_model_tag

    sel = _SELECTOR.get(intent, intent)
    if sel not in OVERRIDABLE:
        raise ValueError(f"intent {intent!r} is not owner-selectable")
    if model_locked_by_env():
        raise EnvLockedError(sel, "DAEDALUS_MODEL")
    tag = (model_tag or "").strip()
    if tag:
        prov, mid = parse_provider_model_tag(tag)
        if not (prov and mid):
            raise ValueError("model must look like provider:model")
    from .. import kv
    current = get_overrides()
    if tag:
        current[sel] = tag
    else:
        current.pop(sel, None)
    kv.set(KV_OVERRIDES, json.dumps(current))
    return current


def model_tag_for(intent: str) -> str | None:
    """The explicit ``provider:model`` tag an intent resolves to before brain fallback, if any."""
    intent = canonical_intent(intent)
    if not model_locked_by_env():
        tag = get_overrides().get(_SELECTOR.get(intent, intent))
        if tag:
            return tag
    return INTENT_DEFAULTS.get(intent)


def resolve(intent: str, *, model_hint: str | None = None, route: dict[str, Any] | None = None) -> Resolution:
    """Which model serves ``intent``: hint > env lock > owner override > registry default > brain."""
    from ..llm_config import get_config
    from ..model_router import config_for_explicit_model

    intent = canonical_intent(intent)
    if route:  # an already-resolved route (worker slot): used as is
        cfg = dict(route)
        return Resolution(intent, str(cfg.get("provider") or ""), str(cfg.get("model") or ""), "slot", cfg=cfg)
    locked = model_locked_by_env()
    if model_hint and str(model_hint).strip():
        tag, source = str(model_hint).strip(), "call-site"
    elif locked:
        tag, source = None, "env-lock"
    else:
        tag = model_tag_for(intent)
        source = ("owner-override" if tag and tag == get_overrides().get(_SELECTOR.get(intent, intent))
                  else "registry-default" if tag else "brain")
    cfg = config_for_explicit_model(get_config(), tag, source="gateway")
    return Resolution(intent, str(cfg.get("provider") or ""), str(cfg.get("model") or ""), source,
                      locked=locked, routing_error=str(cfg.get("routing_error") or ""), cfg=cfg)


# --- trace ----------------------------------------------------------------------------------------
_TRACE_SINKS: list[Callable[[dict[str, Any]], None]] = []


def add_trace_sink(sink: Callable[[dict[str, Any]], None]) -> None:
    if sink not in _TRACE_SINKS:
        _TRACE_SINKS.append(sink)


def remove_trace_sink(sink: Callable[[dict[str, Any]], None]) -> None:
    if sink in _TRACE_SINKS:
        _TRACE_SINKS.remove(sink)


def _emit(event: dict[str, Any]) -> None:
    log.info("gateway %s %s/%s ok=%s err=%s ms=%s images=%d", event.get("intent"), event.get("provider"),
             event.get("model"), event.get("ok"), event.get("error_type"), event.get("latency_ms"),
             len(event.get("image_sha256") or []))
    for sink in list(_TRACE_SINKS):
        try:
            sink(event)
        except Exception:  # a broken sink must never fail a model call
            log.debug("gateway trace sink failed", exc_info=True)


# --- result -----------------------------------------------------------------------------------------
@dataclass
class GatewayResult:
    ok: bool
    text: str = ""
    resolution: Resolution | None = None
    error: GatewayError | None = None
    budget_stopped: bool = False
    usage: dict[str, Any] = field(default_factory=dict)
    cost_usd: float | None = None
    cost_known: bool = False
    image_sha256: list[str] = field(default_factory=list)
    latency_ms: int = 0

    @property
    def provider(self) -> str:
        return self.resolution.provider if self.resolution else ""

    @property
    def model(self) -> str:
        return self.resolution.model if self.resolution else ""

    def as_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "text": self.text, "error": self.error.as_dict() if self.error else None,
                "budget_stopped": self.budget_stopped, "usage": self.usage, "cost_usd": self.cost_usd,
                "cost_known": self.cost_known, "image_sha256": self.image_sha256, "latency_ms": self.latency_ms,
                "resolution": self.resolution.public() if self.resolution else None}


def _api_key(cfg: dict[str, Any]) -> str:
    key = str(cfg.get("api_key") or "")
    if key:
        return key
    from ..llm_config import is_loopback_gateway
    from ..local_llm import dummy_api_key, is_local_provider

    provider = str(cfg.get("provider") or "")
    if is_local_provider(provider) or cfg.get("local") or is_loopback_gateway(provider):
        return dummy_api_key(provider) if is_local_provider(provider) else "omniroute"
    return ""


async def _execute(cfg: dict[str, Any], system: str, messages: list[dict[str, Any]], *, max_tokens: int,
                   temperature: float | None, json_mode: bool, use_cache: bool,
                   timeout: float | None, reasoning: bool = False) -> tuple[str, Any]:
    """One provider call. Returns (text, raw response). Raises on provider failure."""
    from ..llm_config import openai_compat_extra_body, openai_compat_headers

    provider = str(cfg.get("provider") or "")
    model = str(cfg.get("model") or "")
    base_url = str(cfg.get("base_url") or "")
    api_key = _api_key(cfg)
    system, msgs = _split_system(messages, system)

    if (cfg.get("sdk") or "openai") == "anthropic":
        from anthropic import AsyncAnthropic

        kwargs: dict[str, Any] = {"api_key": api_key, "max_retries": 0}
        if base_url:
            kwargs["base_url"] = base_url
        client = AsyncAnthropic(**kwargs)
        sys_param: Any = system
        if use_cache and system:
            sys_param = [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]
        create: dict[str, Any] = {"model": model, "max_tokens": max_tokens, "messages": _to_anthropic(msgs)}
        if sys_param:
            create["system"] = sys_param
        if temperature is not None:
            create["temperature"] = temperature
        coro = client.messages.create(**create)
        resp = await (asyncio.wait_for(coro, timeout) if timeout else coro)
        text = "".join(getattr(b, "text", "") or "" for b in (resp.content or []) if getattr(b, "type", "text") == "text")
        return text, resp

    from openai import AsyncOpenAI

    client = AsyncOpenAI(api_key=api_key, base_url=base_url or None, max_retries=1,
                         default_headers=openai_compat_headers(provider) or None)
    oa = _to_openai(msgs, system)
    # Reasoning models spend hidden tokens: OpenAI-compatible APIs take max_completion_tokens for them.
    body: dict[str, Any] = {"model": model, "messages": oa,
                            ("max_completion_tokens" if reasoning else "max_tokens"): max_tokens}
    if temperature is not None:
        body["temperature"] = temperature
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    extra_body = openai_compat_extra_body(provider, with_tools=False)
    if extra_body:
        body["extra_body"] = extra_body

    async def _once(payload: dict[str, Any]) -> Any:
        coro = client.chat.completions.create(**payload)
        return await (asyncio.wait_for(coro, timeout) if timeout else coro)

    # A model without image input fails with a typed ``unsupported_modality`` error; the caller decides
    # whether to retry without frames (nothing is silently dropped here).
    try:
        resp = await _once(body)
    except Exception:
        if not json_mode:
            raise
        # Some backends reject response_format; retry once without it (the mentor relied on this).
        body.pop("response_format", None)
        resp = await _once(body)
    choice = resp.choices[0] if getattr(resp, "choices", None) else None
    text = (getattr(getattr(choice, "message", None), "content", None) or "") if choice else ""
    if isinstance(text, list):
        text = "".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in text)
    return str(text), resp


async def call(
    intent: str,
    messages: list[dict[str, Any]],
    *,
    system: str = "",
    max_tokens: int = 4096,
    temperature: float | None = None,
    json_mode: bool = False,
    model_hint: str | None = None,
    route: dict[str, Any] | None = None,
    overlay: dict[str, Any] | None = None,
    use_cache: bool = True,
    timeout: float | None = None,
    reasoning: bool = False,
    client_id: str = "",
    slug: str | None = None,
    run_id: str | None = None,
) -> GatewayResult:
    """Resolve, gate on budget, send, meter, trace. Never raises for provider failures."""
    from .. import run_budget
    from ..llm_cost import parse_usage

    intent_c = canonical_intent(intent)
    norm = normalise_messages(messages)
    hashes = image_hashes(norm)
    t0 = time.perf_counter()

    res = resolve(intent_c, model_hint=model_hint, route=route)
    cfg = dict(res.cfg)
    if overlay:  # explicit effort/writer overlay for background runs (no ContextVar needed)
        from ..model_router import overlay_effort
        cfg = overlay_effort(cfg, overlay)
        res = Resolution(res.intent, str(cfg.get("provider") or res.provider), str(cfg.get("model") or res.model),
                         res.source, res.locked, res.routing_error, cfg)

    def _finish(result: GatewayResult) -> GatewayResult:
        result.resolution = res
        result.image_sha256 = hashes
        result.latency_ms = int((time.perf_counter() - t0) * 1000)
        _emit({
            "intent": intent_c, "provider": res.provider, "model": res.model, "source": res.source,
            "ok": result.ok, "error_type": result.error.type if result.error else None,
            "budget_stopped": result.budget_stopped, "latency_ms": result.latency_ms,
            "prompt_tokens": result.usage.get("prompt_tokens"), "completion_tokens": result.usage.get("completion_tokens"),
            "cost_usd": result.cost_usd, "cost_known": result.cost_known, "image_sha256": hashes,
            "client_id": client_id, "slug": slug, "run_id": run_id,
        })
        return result

    if res.routing_error:
        return _finish(GatewayResult(False, error=GatewayError("routing", res.routing_error)))
    if run_budget.would_block(res.provider):
        return _finish(GatewayResult(False, budget_stopped=True,
                                     error=GatewayError("budget_blocked", run_budget.stop_text())))
    if not _api_key(cfg):
        return _finish(GatewayResult(False, error=GatewayError(
            "auth", f"No API key for provider {res.provider!r}; set the key or use a local model.")))
    try:
        text, raw = await _execute(cfg, system, norm, max_tokens=max_tokens, temperature=temperature,
                                   json_mode=json_mode, use_cache=use_cache, timeout=timeout,
                                   reasoning=reasoning)
    except Exception as exc:
        return _finish(GatewayResult(False, error=classify_error(exc)))
    parsed = parse_usage(getattr(raw, "usage", None), raw)
    try:
        run_budget.note_usage(parsed, model=res.model, role=intent_c)
    except Exception:  # metering trouble must not fail a successful, billed call
        log.debug("gateway metering skipped", exc_info=True)
    usage = {"prompt_tokens": int(parsed.get("prompt_tokens") or 0),
             "completion_tokens": int(parsed.get("completion_tokens") or 0)}
    known = bool(parsed.get("cost_known")) and parsed.get("cost_usd") is not None
    return _finish(GatewayResult(True, text=text, usage=usage, cost_known=known,
                                 cost_usd=float(parsed["cost_usd"]) if known else None))


# --- explicit routes for the cheap helper models ---------------------------------------------------------
def route_for(provider: str, model: str, api_key: str, base_url: str, sdk: str = "openai") -> dict[str, Any]:
    """A fully specified route (used for helper models that carry their own key and endpoint)."""
    return {"provider": provider, "model": model, "api_key": api_key, "base_url": base_url, "sdk": sdk,
            "source": "slot"}


def _provider_from_url(base_url: str) -> str:
    low = (base_url or "").lower()
    for needle, name in (("nvidia", "nvidia"), ("openrouter", "openrouter"), ("novita", "novita")):
        if needle in low:
            return name
    return ""


def cheap_route() -> dict[str, Any] | None:
    """The wrap-up / navigator helper model (``cheap_llm``) as a route, or None when no key is set."""
    from ..cheap_llm import resolve_cheap_openai

    cheap = resolve_cheap_openai()
    if not cheap:
        return None
    return route_for(_provider_from_url(cheap["base_url"]), cheap["model"], cheap["api_key"], cheap["base_url"])


# --- sync bridge (navigator/continuity/reviewer call sites are synchronous) -----------------------
def call_sync(*args: Any, **kwargs: Any) -> GatewayResult:
    """``call`` for synchronous code: runs on a private loop in a helper thread when a loop is running."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(call(*args, **kwargs))
    box: dict[str, Any] = {}

    def _runner() -> None:
        try:
            box["r"] = asyncio.run(call(*args, **kwargs))
        except BaseException as exc:  # pragma: no cover - surfaced below
            box["e"] = exc

    t = threading.Thread(target=_runner, daemon=True)
    t.start()
    t.join()
    if "e" in box:
        raise box["e"]
    return box["r"]


# --- shims for legacy call sites ----------------------------------------------------------------------
def to_text_or_raise(result: GatewayResult) -> str:
    """For call sites that returned a reply string and raised on failure (the persona path)."""
    if result.ok:
        return result.text
    if result.budget_stopped:
        return result.error.message if result.error else ""  # legacy behaviour: the stop text is the reply
    raise GatewayCallError(result.error or GatewayError("provider_error", "LLM call failed"))


def to_legacy_openai(result: GatewayResult) -> dict[str, Any]:
    """The dict shape ``llm_client.complete`` / ``mesh.route`` return."""
    text = result.text if result.ok else (result.error.message if result.error else "")
    out: dict[str, Any] = {
        "ok": result.ok, "provider": result.provider, "model": result.model,
        "cost_usd": result.cost_usd or 0.0, "reply": text,
        "content": [{"type": "text", "text": text}],
        "choices": [{"message": {"role": "assistant", "content": text}}],
        "usage": {**result.usage, "cost_usd": result.cost_usd},
    }
    if result.budget_stopped:
        out["budget_stopped"] = True
    if result.error and result.error.type == "routing":
        out["routing_failed"] = True
        out["routing_error"] = result.error.message
    if result.error:
        out["error"] = result.error.as_dict()
    return out
