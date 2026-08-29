"""Unified provider and model resources for routing and Studio discovery."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Literal

ProviderKind = Literal["local", "cloud", "gateway", "custom"]
AuthKind = Literal["none", "optional", "api_key"]


@dataclass(frozen=True)
class ProviderDefinition:
    id: str
    label: str
    kind: ProviderKind
    auth: AuthKind
    sdk: str


PROVIDERS: tuple[ProviderDefinition, ...] = (
    ProviderDefinition("ollama", "Ollama", "local", "none", "openai"),
    ProviderDefinition("lmstudio", "LM Studio", "local", "none", "openai"),
    ProviderDefinition("llamacpp", "llama.cpp", "local", "none", "openai"),
    ProviderDefinition("vllm", "vLLM", "local", "none", "openai"),
    ProviderDefinition("openrouter", "OpenRouter", "gateway", "api_key", "openai"),
    ProviderDefinition("alltoken", "AllToken", "gateway", "api_key", "openai"),
    ProviderDefinition("novita", "Novita AI", "gateway", "api_key", "openai"),
    ProviderDefinition("nvidia", "NVIDIA NIM", "gateway", "api_key", "openai"),
    ProviderDefinition("anthropic", "Anthropic", "cloud", "api_key", "anthropic"),
    ProviderDefinition("openai", "OpenAI", "cloud", "api_key", "openai"),
    ProviderDefinition("gemini", "Google Gemini", "cloud", "api_key", "openai"),
    ProviderDefinition("groq", "Groq", "cloud", "api_key", "openai"),
    ProviderDefinition("poolside", "Poolside", "cloud", "api_key", "openai"),
    ProviderDefinition("replit_openai", "Replit OpenAI", "cloud", "api_key", "openai"),
    ProviderDefinition("replit_gemini", "Replit Gemini", "cloud", "api_key", "openai"),
    ProviderDefinition("custom", "Custom OpenAI-compatible", "custom", "optional", "openai"),
)


def provider_definition(provider_id: str) -> ProviderDefinition:
    normalized = (provider_id or "").strip().lower()
    for definition in PROVIDERS:
        if definition.id == normalized:
            return definition
    return ProviderDefinition(normalized or "custom", provider_id or "Custom", "custom", "api_key", "openai")


def _model_rows(provider_id: str, discovered: list[str], current_model: str) -> list[dict[str, Any]]:
    from .llm_config import list_preset_models

    merged: dict[str, dict[str, Any]] = {}
    for row in list_preset_models(include_replit=True):
        if row.get("provider") != provider_id:
            continue
        model_id = str(row.get("id") or "").strip()
        if model_id:
            merged[model_id] = {
                "id": model_id,
                "label": row.get("label") or model_id,
                "provider_id": provider_id,
                "source": "preset",
                "tier": row.get("tier"),
            }
    for model_id in discovered:
        merged[model_id] = {
            "id": model_id,
            "label": model_id,
            "provider_id": provider_id,
            "source": "discovered",
            "tier": merged.get(model_id, {}).get("tier"),
        }
    if current_model and current_model not in merged:
        merged[current_model] = {
            "id": current_model,
            "label": current_model,
            "provider_id": provider_id,
            "source": "selected",
            "tier": None,
        }
    rows = list(merged.values())
    for row in rows:
        row["selected"] = row["id"] == current_model
    return rows


def resources(*, probe_local: bool = True) -> list[dict[str, Any]]:
    """Return provider resources grouped by what this machine can actually use."""
    from .llm_config import (
        PROVIDER_DEFAULTS,
        _read_db,
        resolve_provider_api_key,
    )
    from .local_llm import default_base_url, probe_all

    db_cfg = _read_db()
    selected_provider = str(db_cfg.get("provider") or "").strip().lower()
    selected_model = str(db_cfg.get("model") or "").strip()
    probes = {
        str(row.get("provider")): row
        for row in (probe_all() if probe_local else [])
    }
    rows: list[dict[str, Any]] = []
    for definition in PROVIDERS:
        provider_id = definition.id
        defaults = PROVIDER_DEFAULTS.get(provider_id, PROVIDER_DEFAULTS["openai"])
        local_probe = probes.get(provider_id, {})
        if definition.kind == "local":
            base_url = default_base_url(provider_id)
            configured = bool(local_probe.get("ok"))
            status = "ready" if configured else "unreachable"
            discovered = list(local_probe.get("models") or [])
        else:
            if definition.kind == "custom":
                base_url = str(
                    db_cfg.get("base_url")
                    if selected_provider == provider_id and db_cfg.get("base_url")
                    else ""
                )
            else:
                base_url = str(
                    db_cfg.get("base_url")
                    if selected_provider == provider_id and db_cfg.get("base_url")
                    else defaults.get("base_url") or ""
                )
            has_key = bool(
                (selected_provider == provider_id and db_cfg.get("api_key"))
                or resolve_provider_api_key(provider_id)
            )
            configured = bool(base_url and (has_key or definition.auth in ("none", "optional")))
            status = "ready" if configured else ("missing_url" if not base_url else "missing_key")
            discovered = []
        current_model = selected_model if selected_provider == provider_id else ""
        rows.append({
            **asdict(definition),
            "base_url": base_url,
            "configured": configured,
            "reachable": local_probe.get("ok") if definition.kind == "local" else None,
            "status": status,
            "selected": selected_provider == provider_id,
            "models": _model_rows(provider_id, discovered, current_model),
        })
    return rows


def snapshot(*, probe_local: bool = True) -> dict[str, Any]:
    from .llm_config import describe_effective_config
    from .routing_profiles import available_profiles

    rows = resources(probe_local=probe_local)
    return {
        "ok": True,
        "resources": rows,
        "groups": {
            "local": [row["id"] for row in rows if row["kind"] == "local"],
            "cloud": [row["id"] for row in rows if row["kind"] in ("cloud", "gateway")],
            "custom": [row["id"] for row in rows if row["kind"] == "custom"],
        },
        "profiles": available_profiles(rows),
        "effective": describe_effective_config().get("effective") or {},
    }
