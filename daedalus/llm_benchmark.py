"""LLM benchmark profiles — curated scores vs Claude Opus 4.8 reference.

Scores are derived from preset_library._METRICS (benchmark-informed curated
factors: quality q, reasoning r, code c, latency, cost). Not live API probes —
stable, zero-cost, suitable for catalog UI and Auto Mode routing.

Reference baseline: Claude Opus 4.8 (alltoken claude-opus-4-8) = 100% composite.
"""
from __future__ import annotations

from typing import Any

from .preset_library import (
    _DEFAULT_METRIC,
    _METRICS,
    _clamp,
    _economy_score,
    _speed_score,
)

REFERENCE_MODEL_ID = "claude-opus-4-8"
REFERENCE_LABEL = "Claude Opus 4.8"
BENCHMARK_VERSION = "2026-06-18"

# Catalog / registry id → _METRICS key
_MODEL_ALIASES: dict[str, str] = {
    "claude-opus-4":              "claude-opus-4-8",
    "claude-opus-4-6":            "claude-opus-4-8",
    "anthropic/claude-opus-4":    "anthropic/claude-opus-4-5",
    "anthropic/claude-opus-4-5":  "anthropic/claude-opus-4-5",
    "anthropic/claude-sonnet-4-5": "anthropic/claude-sonnet-4-5",
    "anthropic/claude-haiku-4-5":  "anthropic/claude-haiku-4-5",
    "anthropic/claude-3-5-haiku":  "anthropic/claude-haiku-3-5",
    "claude-sonnet-4":            "claude-sonnet-4-6",
    "claude-haiku-4-5":         "claude-haiku-4-5",
    "deepseek-v3.2":              "deepseek/deepseek-v3.2",
    "deepseek-v3.1":              "deepseek/deepseek-v3",
    "deepseek-v4-flash":          "deepseek-v4-flash",
    "deepseek-v4-pro":            "deepseek-v4-pro",
    "glm-4.7-flash":              "z-ai/glm-4.7-flash",
    "glm-5-turbo":                "glm-5-turbo",
    "glm-5.1":                    "z-ai/glm-5.1",
    "gpt-5.4":                    "gpt-5.4",
    "gpt-5.4-nano":               "gpt-5.4-nano",
    "gpt-5.4-pro":                "gpt-5.4-pro",
    "gpt-5.5":                    "gpt-5.5",
    "google/gemini-2.5-flash":    "google/gemini-2.5-flash",
    "google/gemini-2.5-pro":      "google/gemini-2.5-pro",
    "meta-llama/llama-3.3-70b-instruct": "meta-llama/llama-3.3-70b-instruct",
    "meta-llama/llama-3.1-8b-instruct": "meta-llama/llama-3.1-8b-instruct",
    "inclusionai/ling-2.6-flash": "inclusionai/ling-2.6-flash",
    "poolside/laguna-m.1":        "poolside/laguna-m.1",
    # NVIDIA NIM (full integrate paths) — callable from production scan 2026-06-19
    "nvidia:mistralai/mistral-large-4-1t-a52b": "mistralai/mistral-large-4-1t-a52b",
    "mistralai/mistral-large-4-1t-a52b":        "mistralai/mistral-large-4-1t-a52b",
    "nvidia:nvidia/nemotron-3.5-lightning-30b-a3b":                 "nvidia/nemotron-3.5-lightning-30b-a3b",
    "nvidia/nemotron-3.5-lightning-30b-a3b":                        "nvidia/nemotron-3.5-lightning-30b-a3b",
    "nvidia:openai/gpt-oss-20b":     "openai/gpt-oss-20b",
    "nvidia:nvidia/nemotron-3-ultra-550b-a55b": "nvidia/nemotron-3-ultra-550b-a55b",
    "nvidia:moonshotai/kimi-k2.6":   "moonshotai/kimi-k2.6",
    "nvidia:meta/llama-3.2-90b-vision-instruct": "meta/llama-3.2-90b-vision-instruct",
    "nvidia:nvidia/nemotron-3.5-bolt-e4b": "nvidia/nemotron-3.5-bolt-e4b",
    "claude-opus-4-7":            "claude-opus-4-8",
}

_TASK_DEFS: dict[str, dict[str, Any]] = {
    "code": {
        "label_bg": "Код",
        "cap_tags": {"code", "code_review"},
        "score_fn": lambda m: float(m["c"]),
    },
    "reasoning": {
        "label_bg": "Reasoning",
        "cap_tags": {"reasoning", "analysis", "math", "planning"},
        "score_fn": lambda m: float(m["r"]),
    },
    "chat": {
        "label_bg": "Чат / общ текст",
        "cap_tags": {"writing", "summarization", "translation", "classification"},
        "score_fn": lambda m: m["q"] * 0.65 + _speed_score(m["lat"]) * 0.35,
    },
    "cheap": {
        "label_bg": "Евтин / batch",
        "cap_tags": {"cheap", "fast", "extraction", "classification"},
        "score_fn": lambda m: _economy_score(m["cin"] * 0.3 + m["cout"] * 0.7),
    },
    "fast": {
        "label_bg": "Скорост",
        "cap_tags": {"fast"},
        "score_fn": lambda m: _speed_score(m["lat"]),
    },
}

_TASK_LABEL_BG: dict[str, str] = {k: v["label_bg"] for k, v in _TASK_DEFS.items()}


def resolve_metric_id(model_id: str) -> str:
    """Map catalog/registry model id → _METRICS key."""
    mid = (model_id or "").strip()
    if not mid:
        return ""
    if mid in _METRICS:
        return mid
    if mid in _MODEL_ALIASES:
        return _MODEL_ALIASES[mid]
    bare = mid.rsplit("/", 1)[-1]
    if bare in _METRICS:
        return bare
    if bare in _MODEL_ALIASES:
        return _MODEL_ALIASES[bare]
    return mid


def model_metric(model_id: str) -> dict[str, float]:
    key = resolve_metric_id(model_id)
    return _METRICS.get(key, _DEFAULT_METRIC)


def _composite_capability(m: dict[str, float]) -> float:
    """Overall capability vs Opus — quality + reasoning + code."""
    return m["q"] * 0.35 + m["r"] * 0.40 + m["c"] * 0.25


def _ref_metric() -> dict[str, float]:
    return model_metric(REFERENCE_MODEL_ID)


def opus47_pct(model_id: str) -> int:
    """Composite similarity to Claude Opus 4.8 reference (0–100, 100 = parity)."""
    ref = _composite_capability(_ref_metric())
    if ref <= 0:
        return 0
    cur = _composite_capability(model_metric(model_id))
    return _clamp(cur / ref * 100.0)


def task_vs_opus_pct(model_id: str, task: str) -> int:
    """Task-specific % of Opus 4.7 on that dimension."""
    tdef = _TASK_DEFS.get(task)
    if not tdef:
        return 0
    ref_m = _ref_metric()
    cur_m = model_metric(model_id)
    ref_s = tdef["score_fn"](ref_m)
    cur_s = tdef["score_fn"](cur_m)
    if ref_s <= 0:
        return 0
    return _clamp(cur_s / ref_s * 100.0)


def value_index(model_id: str) -> int:
    """Quality-per-dollar index (100 ≈ Opus 4.7 value; higher = better deal)."""
    ref_m = _ref_metric()
    cur_m = model_metric(model_id)
    ref_cost = ref_m["cin"] * 0.3 + ref_m["cout"] * 0.7
    cur_cost = cur_m["cin"] * 0.3 + cur_m["cout"] * 0.7
    if cur_cost <= 0:
        return 0
    cap_ratio = _composite_capability(cur_m) / max(_composite_capability(ref_m), 1.0)
    cost_ratio = ref_cost / cur_cost
    return _clamp(cap_ratio * cost_ratio * 100.0, hi=500)


def best_tasks_for_model(model_id: str, limit: int = 3) -> list[str]:
    """Top task keys where model is strongest vs Opus on that axis."""
    scored = [(task, task_vs_opus_pct(model_id, task)) for task in _TASK_DEFS]
    scored.sort(key=lambda x: x[1], reverse=True)
    return [t for t, _ in scored[:limit]]


def best_for_summary_bg(model_id: str) -> str:
    """One-line BG recommendation for catalog UI."""
    pct = opus47_pct(model_id)
    tasks = best_tasks_for_model(model_id, 2)
    labels = [_TASK_LABEL_BG.get(t, t) for t in tasks]
    task_str = " + ".join(labels) if labels else "общи задачи"
    if pct >= 98:
        tier = "флагманско"
    elif pct >= 88:
        tier = "много близо до Opus 4.8"
    elif pct >= 75:
        tier = "силен компромис"
    elif pct >= 60:
        tier = "добър за ежедневни задачи"
    else:
        tier = "специализиран / икономичен"
    return f"{tier.capitalize()} — {task_str} (~{pct}% от Opus 4.8)"


def profile_for_model(model_id: str, *, supports_vision: bool = False) -> dict[str, Any]:
    """Full benchmark profile for one model."""
    task_pct = {task: task_vs_opus_pct(model_id, task) for task in _TASK_DEFS}
    if supports_vision:
        task_pct["vision"] = _clamp(model_metric(model_id)["q"] / max(_ref_metric()["q"], 1) * 100)
    best = best_tasks_for_model(model_id)
    return {
        "reference":        REFERENCE_MODEL_ID,
        "reference_label":  REFERENCE_LABEL,
        "opus47_pct":       opus47_pct(model_id),
        "task_pct":         task_pct,
        "best_tasks":       best,
        "best_for_bg":      best_for_summary_bg(model_id),
        "value_index":      value_index(model_id),
        "metric_key":       resolve_metric_id(model_id),
        "source":           "curated",
        "version":          BENCHMARK_VERSION,
    }


def enrich_catalog_entry(entry: dict) -> dict:
    """Attach benchmark block to a mesh catalog text model entry."""
    out = dict(entry)
    mid = entry.get("id") or ""
    provider = str(entry.get("provider") or "")
    out["benchmark"] = profile_for_model(
        mid,
        supports_vision=bool(entry.get("supports_vision")),
    )
    try:
        from argos_data_nexus.modules.llm_live_eval_store import live_eval_for_catalog

        live = live_eval_for_catalog(provider, mid)
        if live:
            out["live_eval"] = live
            b = out["benchmark"]
            if live.get("status") == "ok" and live.get("pass_pct") is not None:
                b["live_pass_pct"] = live.get("pass_pct")
                b["live_latency_ms"] = live.get("latency_ms")
                b["live_tested_at"] = live.get("tested_at")
                b["source"] = "curated+live"
    except Exception:
        pass
    return out


def rankings_by_task(task: str, models: list[dict], *, limit: int = 15) -> list[dict]:
    """Rank catalog models for a task by task_vs_opus_pct then value_index."""
    if task not in _TASK_DEFS:
        task = "chat"
    rows = []
    for m in models:
        mid = m.get("id") or ""
        rows.append({
            "id":           mid,
            "name":         m.get("name") or mid,
            "provider":     m.get("provider") or "",
            "opus47_pct":   opus47_pct(mid),
            "task_pct":     task_vs_opus_pct(mid, task),
            "value_index":  value_index(mid),
            "best_for_bg":  best_for_summary_bg(mid),
        })
    rows.sort(key=lambda r: (r["task_pct"], r["value_index"]), reverse=True)
    return rows[:limit]


def enrich_catalog_payload(catalog: dict) -> dict:
    """Add per-model benchmark + task rankings to get_catalog() response."""
    out = dict(catalog)
    text = [enrich_catalog_entry(e) for e in (catalog.get("text") or [])]
    out["text"] = text
    out["benchmark_meta"] = {
        "reference":       REFERENCE_MODEL_ID,
        "reference_label": REFERENCE_LABEL,
        "version":         BENCHMARK_VERSION,
        "method": (
            "Кураторски q/r/c фактори (preset_library) + латентност/цена. "
            "Opus 4.7 = 100% composite. Live pass% от llm_live_eval.json когато има."
        ),
        "tasks": {k: v["label_bg"] for k, v in _TASK_DEFS.items()},
    }
    out["benchmark_rankings"] = {
        task: rankings_by_task(task, catalog.get("text") or [], limit=10)
        for task in _TASK_DEFS
    }
    return out


def _caps_to_primary_task(required_caps: list[str]) -> str:
    """Pick dominant task axis from capability tags."""
    if not required_caps:
        return "chat"
    cap_set = set(required_caps)
    best_task, best_hits = "chat", 0
    for task, tdef in _TASK_DEFS.items():
        hits = len(cap_set & tdef["cap_tags"])
        if hits > best_hits:
            best_hits = hits
            best_task = task
    if "code" in cap_set or "code_review" in cap_set:
        return "code"
    if "reasoning" in cap_set or "math" in cap_set:
        return "reasoning"
    if "cheap" in cap_set or "fast" in cap_set:
        if "code" not in cap_set:
            return "cheap"
    return best_task


def auto_mode_benchmark_bonus(
    model_id: str,
    required_caps: list[str],
    budget_hint: str,
) -> float:
    """Extra score for model_registry auto selection (0–~12)."""
    if not model_id:
        return 0.0
    task = _caps_to_primary_task(required_caps)
    bonus = task_vs_opus_pct(model_id, task) / 100.0 * 6.0
    bonus += opus47_pct(model_id) / 100.0 * 2.0
    if budget_hint == "cheap":
        bonus += value_index(model_id) / 100.0 * 4.0
    elif budget_hint == "quality":
        bonus += opus47_pct(model_id) / 100.0 * 4.0
    else:
        bonus += value_index(model_id) / 100.0 * 2.0
    return bonus
