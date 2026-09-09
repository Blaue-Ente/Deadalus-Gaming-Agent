"""Parse billed USD from OpenRouter (and similar) chat usage objects.

OpenRouter returns ``usage.cost`` when the request sets
``extra_body.usage.include = true``. The OpenAI SDK often parks unknown
fields on ``model_extra``, so this walker accepts dicts, SDK objects, and
raw response extras. Missing cost is ``None`` — never a catalog guess.
"""
from __future__ import annotations

from typing import Any


def _mapping(obj: Any) -> dict[str, Any]:
    if obj is None:
        return {}
    if isinstance(obj, dict):
        return dict(obj)
    out: dict[str, Any] = {}
    dump = getattr(obj, "model_dump", None)
    if callable(dump):
        try:
            row = dump()
            if isinstance(row, dict):
                out.update(row)
        except Exception:
            pass
    extra = getattr(obj, "model_extra", None)
    if isinstance(extra, dict):
        out.update(extra)
    for key in (
        "prompt_tokens", "completion_tokens", "total_tokens",
        "input_tokens", "output_tokens",
        "cost", "cost_usd", "total_cost", "native_cost",
        "cost_details",
    ):
        if key not in out and hasattr(obj, key):
            try:
                out[key] = getattr(obj, key)
            except Exception:
                pass
    return out


def _money(raw: Any) -> float | None:
    if raw is None or raw is False:
        return None
    if isinstance(raw, bool):
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    if value < 0 or value != value:  # NaN
        return None
    return value


def parse_usage(usage: Any, resp: Any = None) -> dict[str, Any]:
    """Return tokens + billed USD. ``cost_usd`` is None when the provider omitted it."""
    row = _mapping(usage)
    if resp is not None and not row:
        row = _mapping(getattr(resp, "usage", None))
    extra = _mapping(getattr(resp, "model_extra", None) if resp is not None else None)
    if isinstance(extra.get("usage"), dict):
        merged = dict(extra["usage"])
        merged.update(row)
        row = merged

    prompt = row.get("prompt_tokens", row.get("input_tokens"))
    completion = row.get("completion_tokens", row.get("output_tokens"))
    try:
        prompt_i = int(prompt) if prompt is not None else 0
    except (TypeError, ValueError):
        prompt_i = 0
    try:
        completion_i = int(completion) if completion is not None else 0
    except (TypeError, ValueError):
        completion_i = 0

    cost = _money(row.get("cost"))
    source = "openrouter.usage.cost" if cost is not None else ""
    if cost is None:
        cost = _money(row.get("cost_usd"))
        if cost is not None:
            source = "usage.cost_usd"
    if cost is None:
        cost = _money(row.get("total_cost"))
        if cost is not None:
            source = "usage.total_cost"
    if cost is None:
        details = row.get("cost_details")
        if isinstance(details, dict):
            cost = _money(details.get("upstream_inference_cost"))
            if cost is not None:
                source = "usage.cost_details"

    tokens: dict[str, Any] = {
        "prompt_tokens": prompt_i,
        "completion_tokens": completion_i,
        "cost_usd": cost,
        "cost_known": cost is not None,
        "source": source or "missing",
    }
    if prompt_i or completion_i:
        tokens["total_tokens"] = prompt_i + completion_i
    return tokens
