"""Availability-aware model profiles for Studio routing."""
from __future__ import annotations

from typing import Any


def _model_ref(resource: dict[str, Any], model: dict[str, Any]) -> dict[str, str]:
    return {
        "provider": str(resource["id"]),
        "model": str(model["id"]),
    }


def _pick_model(
    resources: list[dict[str, Any]],
    *,
    tiers: tuple[str, ...] = (),
    terms: tuple[str, ...] = (),
) -> dict[str, str] | None:
    for tier in (*tiers, ""):
        for resource in resources:
            models = sorted(
                resource.get("models") or [],
                key=lambda model: not bool(model.get("selected")),
            )
            for model in models:
                model_id = str(model.get("id") or "")
                if tier and model.get("tier") != tier:
                    continue
                if terms and not any(term in model_id.lower() for term in terms):
                    continue
                return _model_ref(resource, model)
    return None


def _profile(
    profile_id: str,
    label: str,
    description: str,
    candidates: list[dict[str, Any]],
    *,
    master_tiers: tuple[str, ...] = ("fast", "power", "cheap"),
) -> dict[str, Any]:
    master = _pick_model(candidates, tiers=master_tiers)
    if not master:
        return {
            "id": profile_id,
            "label": label,
            "description": description,
            "available": False,
            "reason": "Няма достъпен provider с модел за този профил.",
            "slots": {},
        }
    fast = _pick_model(candidates, tiers=("cheap", "fast")) or master
    balanced = _pick_model(candidates, tiers=("fast", "cheap", "power")) or master
    deep = _pick_model(candidates, tiers=("power", "fast")) or master
    coder = _pick_model(
        candidates,
        tiers=("power", "fast", "cheap"),
        terms=("coder", "code", "kimi", "deepseek"),
    ) or balanced
    reviewer = _pick_model(
        candidates,
        tiers=("power", "fast"),
        terms=("claude", "sonnet", "opus", "mistral", "reason"),
    ) or deep
    return {
        "id": profile_id,
        "label": label,
        "description": description,
        "available": True,
        "reason": "",
        "slots": {
            "master": master,
            "fast": fast,
            "balanced": balanced,
            "deep": deep,
            "coder": coder,
            "reviewer": reviewer,
        },
    }


def available_profiles(resources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Build practical profiles only from resources usable on this machine."""
    ready: list[dict[str, Any]] = []
    for row in resources:
        if not row.get("configured"):
            continue
        models = list(row.get("models") or [])
        if row.get("kind") == "local":
            models = [model for model in models if model.get("source") == "discovered"]
        if models:
            ready.append({**row, "models": models})
    local = [row for row in ready if row.get("kind") == "local"]
    remote = [
        row for row in ready
        if row.get("kind") in ("cloud", "gateway", "custom")
    ]
    selected = [row for row in ready if row.get("selected")]
    automatic = selected + [row for row in ready if not row.get("selected")]
    balanced = sorted(
        remote,
        key=lambda row: (
            row.get("id") not in ("openrouter", "alltoken", "novita", "nvidia"),
            row.get("id", ""),
        ),
    )
    quality = sorted(
        remote,
        key=lambda row: (
            not any(model.get("tier") == "power" for model in row.get("models") or []),
            row.get("id", ""),
        ),
    )
    return [
        _profile(
            "auto",
            "Auto",
            "Използва активния provider и разпределя наличните модели по роли.",
            automatic,
            master_tiers=(),
        ),
        _profile(
            "private",
            "Private",
            "Само модели от достижим local runtime; не добавя cloud fallback.",
            local,
        ),
        _profile(
            "balanced",
            "Balanced",
            "Разделя евтини, бързи и мощни модели между worker ролите.",
            balanced,
        ),
        _profile(
            "maximum-quality",
            "Maximum Quality",
            "Предпочита power модели за master, review и сложен synthesis.",
            quality,
            master_tiers=("power", "fast", "cheap"),
        ),
    ]


def profile_by_id(profile_id: str, resources: list[dict[str, Any]]) -> dict[str, Any] | None:
    return next(
        (profile for profile in available_profiles(resources) if profile["id"] == profile_id),
        None,
    )
