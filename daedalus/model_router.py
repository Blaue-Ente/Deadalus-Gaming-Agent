"""Atomic model route: provider + model + base_url + credentials together.

``task_effort`` used to swap only the model id onto the owner's provider,
so an Ollama tag could be sent to OpenRouter. Overlay is all-or-nothing.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class ModelRoute:
    provider: str
    model: str
    base_url: str
    api_key: str
    sdk: str = "openai"
    local: bool = False
    source: str = "config"

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def from_config(cfg: dict[str, Any] | None) -> ModelRoute:
    row = cfg or {}
    provider = str(row.get("provider") or "")
    try:
        from .local_llm import is_local_provider
        local = bool(row.get("local")) or is_local_provider(provider)
    except Exception:
        local = bool(row.get("local"))
    return ModelRoute(
        provider=provider,
        model=str(row.get("model") or ""),
        base_url=str(row.get("base_url") or ""),
        api_key=str(row.get("api_key") or ""),
        sdk=str(row.get("sdk") or "openai"),
        local=local,
        source=str(row.get("source") or "config"),
    )


def _route_for_provider(provider: str, model: str, *, source: str) -> ModelRoute | None:
    from .llm_config import PROVIDER_DEFAULTS, is_loopback_gateway, resolve_provider_api_key
    from .local_llm import is_local_provider, resolve_local_config

    p = (provider or "").strip().lower()
    if not p or not model:
        return None
    if is_local_provider(p):
        local = resolve_local_config(p)
        if not local:
            return None
        local = dict(local)
        local["model"] = model
        local["source"] = source
        return from_config(local)
    pd = PROVIDER_DEFAULTS.get(p) or {}
    key = resolve_provider_api_key(p)
    if not key and p not in ("ollama", "lmstudio", "llamacpp", "vllm") and not is_loopback_gateway(p):
        return None
    return ModelRoute(
        provider=p,
        model=model,
        base_url=str(pd.get("base_url") or ""),
        api_key=key,
        sdk=str(pd.get("sdk") or "openai"),
        local=False,
        source=source,
    )


def overlay_effort(cfg: dict[str, Any], extra: dict[str, Any] | None) -> dict[str, Any]:
    """Apply an effort bind only when provider+model can be resolved together."""
    out = dict(cfg or {})
    if not extra or not extra.get("model"):
        return out
    model = str(extra["model"])
    provider = str(extra.get("provider") or "").strip()
    source = str(extra.get("model_source") or "effort")
    if provider:
        route = _route_for_provider(provider, model, source=source)
        if route is None:
            out["effort"] = extra.get("tier")
            out["effort_source"] = source
            out["effort_skipped"] = "no-atomic-route"
            return out
        merged = dict(out)
        merged.update(route.as_dict())
        merged["effort"] = extra.get("tier")
        merged["effort_source"] = source
        return merged
    # Model-only bind is allowed only when the id is already on this provider.
    current = str(out.get("model") or "")
    if model == current:
        out["effort"] = extra.get("tier")
        out["effort_source"] = source
        return out
    out["effort"] = extra.get("tier")
    out["effort_source"] = source
    out["effort_skipped"] = "model-without-provider"
    return out
