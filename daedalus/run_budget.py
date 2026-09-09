"""Per-run USD envelope — pick writer + mentor so the slice fits $5 / $20 / $50.

Not a magic last-model wand. Relative catalog (OpenRouter-ish, 2026-09), not
a live bill. Off leaves Studio Brain and the Mentor picker alone.

Env ``DAEDALUS_RUN_BUDGET=5|20|50|off`` locks the envelope (tests / sims).
``DAEDALUS_MODEL`` still wins for the writer (live eval pins). Local wardrobe
is never stolen onto the cloud.
"""
from __future__ import annotations

import contextvars
import logging
import os
from contextlib import contextmanager
from typing import Any, Iterator

log = logging.getLogger(__name__)

_DB_KEY = "adn:daedalus_run_budget"
TIERS = (0, 5, 20, 50)
_bound: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar(
    "daedalus_run_budget", default=None
)

# Relative USD for one craft Vite/Phaser slice (~8 writer rounds + 2 Mentor JSON).
# Free pool is preferred on $5. Numbers are planning estimates, not invoices.
WRITERS: tuple[dict[str, Any], ...] = (
    {
        "id": "north-mini-free",
        "provider": "openrouter",
        "model": "cohere/north-mini-code:free",
        "usd": 0.0,
        "note": "Free coder. Weak — needs kernel Phaser seed.",
    },
    {
        "id": "nex-mini-free",
        "provider": "openrouter",
        "model": "nex-agi/nex-n2.5-mini:free",
        "usd": 0.0,
        "note": "Free coder backup.",
    },
    {
        "id": "qwen-30b",
        "provider": "openrouter",
        "model": "qwen/qwen3-coder-30b-a3b-instruct",
        "usd": 0.45,
        "note": "Small paid coder. Fits $5 leftover after Mentor.",
    },
    {
        "id": "gpt-oss-120b",
        "provider": "openrouter",
        "model": "openai/gpt-oss-120b",
        "usd": 0.9,
        "note": "Larger open coder. $20 slice.",
    },
    {
        "id": "kimi-k3-writer",
        "provider": "openrouter",
        "model": "moonshotai/kimi-k3",
        "usd": 4.5,
        "note": "Strong agent writer. $20–$50.",
    },
)

MENTORS: tuple[dict[str, Any], ...] = (
    {
        "id": "rules",
        "choice_id": "rules",
        "provider": "",
        "model": "",
        "usd": 0.0,
        "note": "Kernel protocols only. No director LLM.",
    },
    {
        "id": "glm-flash",
        "choice_id": "or-glm-53-flash",
        "provider": "openrouter",
        "model": "z-ai/glm-5.3-flash",
        "usd": 0.35,
        "note": "Cheap evaluator JSON. Default $5 / $20 director.",
    },
    {
        "id": "kimi-k3",
        "choice_id": "or-kimi-k3",
        "provider": "openrouter",
        "model": "moonshotai/kimi-k3",
        "usd": 1.8,
        "note": "Stronger evaluator. $20+.",
    },
    {
        "id": "fable",
        "choice_id": "or-fable-51",
        "provider": "openrouter",
        "model": "anthropic/claude-fable-5.1",
        "usd": 8.0,
        "note": "Frontier director. $50 envelope.",
    },
)


def normalize_usd(raw: Any) -> int:
    text = str(raw or "").strip().lower().replace("€", "").replace("$", "")
    if text in {"", "off", "none", "0", "false", "no"}:
        return 0
    try:
        n = int(float(text))
    except (TypeError, ValueError):
        return 0
    if n <= 0:
        return 0
    for tier in TIERS:
        if n <= tier:
            return int(tier)
    return 50


def env_override() -> int | None:
    raw = (os.environ.get("DAEDALUS_RUN_BUDGET") or "").strip()
    if not raw:
        return None
    return normalize_usd(raw)


def _kv_raw() -> Any:
    try:
        from . import kv
        return kv.get(_DB_KEY)
    except Exception:
        return None


def persisted_usd() -> int | None:
    raw = _kv_raw()
    if raw is None or str(raw).strip() == "":
        return None
    return normalize_usd(raw)


def usd() -> int:
    ov = env_override()
    if ov is not None:
        return ov
    stored = persisted_usd()
    return stored if stored is not None else 0


def set_usd(raw: Any) -> int:
    from . import kv
    nxt = normalize_usd(raw)
    kv.set(_DB_KEY, str(nxt))
    log.info("Daedalus run budget set → $%s", nxt)
    return usd()


def _plan_pair(cap: int) -> tuple[dict[str, Any], dict[str, Any]]:
    """Explicit envelopes — not “best model that still fits the leftover.”"""
    by_w = {w["id"]: w for w in WRITERS}
    by_m = {m["id"]: m for m in MENTORS}
    if cap <= 5:
        return by_w["north-mini-free"], by_m["glm-flash"]
    if cap <= 20:
        return by_w["gpt-oss-120b"], by_m["kimi-k3"]
    return by_w["kimi-k3-writer"], by_m["fable"]


def plan(cap_usd: int | None = None) -> dict[str, Any]:
    """Relative spend plan. ``cap_usd=0`` → do not override Brain / Mentor."""
    cap = int(TIERS[0] if cap_usd is None else cap_usd)
    if cap <= 0:
        return {
            "ok": True,
            "usd": 0,
            "active": False,
            "apply_writer": False,
            "apply_mentor": False,
            "estimate_usd": 0.0,
            "fits": True,
            "writer": None,
            "mentor": None,
            "note": "Off. Studio Brain and the Mentor picker stay as you set them.",
        }
    writer, mentor = _plan_pair(cap)
    estimate = round(float(writer["usd"]) + float(mentor["usd"]), 2)
    if estimate > cap + 0.01:
        mentor = [m for m in MENTORS if m["id"] == "glm-flash"][0]
        estimate = round(float(writer["usd"]) + float(mentor["usd"]), 2)
    return {
        "ok": True,
        "usd": cap,
        "active": True,
        "apply_writer": True,
        "apply_mentor": True,
        "estimate_usd": estimate,
        "fits": estimate <= cap + 0.01,
        "writer": {
            "id": writer["id"],
            "provider": writer["provider"],
            "model": writer["model"],
            "usd": writer["usd"],
            "note": writer["note"],
        },
        "mentor": {
            "id": mentor["id"],
            "choice_id": mentor["choice_id"],
            "provider": mentor["provider"],
            "model": mentor["model"],
            "usd": mentor["usd"],
            "note": mentor["note"],
        },
        "note": (
            f"${cap} envelope. Writer {writer['model']} (~${writer['usd']}) + "
            f"Mentor {mentor['id']} (~${mentor['usd']}) ≈ ${estimate}. "
            "Estimates, not a live invoice."
        ),
    }


def plan_for_turn() -> dict[str, Any]:
    return plan(usd())


def current() -> dict[str, Any] | None:
    return _bound.get()


def applied_writer() -> dict[str, Any] | None:
    """Overlay payload for llm_config. None when off, env-pinned, or local."""
    row = _bound.get()
    if not row or not row.get("apply_writer"):
        return None
    if (os.environ.get("DAEDALUS_MODEL") or "").strip():
        return None
    try:
        from .local_llm import local_requested
        if local_requested():
            return None
    except Exception:
        pass
    writer = row.get("writer") or {}
    model = str(writer.get("model") or "")
    if not model:
        return None
    return {
        "model": model,
        "provider": str(writer.get("provider") or "openrouter"),
        "model_source": "run-budget",
        "tier": f"budget-{row.get('usd')}",
        "apply_model": True,
    }


def applied_mentor() -> dict[str, Any] | None:
    row = _bound.get()
    if not row or not row.get("apply_mentor"):
        return None
    if (os.environ.get("DAEDALUS_MENTOR_MODEL") or "").strip():
        return None
    mentor = row.get("mentor") or {}
    cid = str(mentor.get("choice_id") or "")
    if not cid:
        return None
    return {
        "choice_id": cid,
        "provider": str(mentor.get("provider") or ""),
        "model": str(mentor.get("model") or ""),
        "source": "run-budget",
        "usd": row.get("usd"),
    }


@contextmanager
def bind(decision: dict[str, Any] | None) -> Iterator[dict[str, Any] | None]:
    token = _bound.set(decision)
    try:
        yield decision
    finally:
        _bound.reset(token)


def briefing(decision: dict[str, Any] | None) -> str:
    if not decision or not decision.get("active"):
        return ""
    return (
        f"[RUN BUDGET ${decision.get('usd')} — estimate ${decision.get('estimate_usd')}. "
        f"{decision.get('note')}]"
    )


def status() -> dict[str, Any]:
    ov = env_override()
    stored = persisted_usd()
    cap = usd()
    return {
        "usd": cap,
        "active": cap > 0,
        "env_locked": ov is not None,
        "env_override": ov,
        "persisted": stored,
        "default": 0,
        "tiers": [0, 5, 20, 50],
        "plan": plan(cap),
        "writers": [
            {"id": w["id"], "model": w["model"], "usd": w["usd"]} for w in WRITERS
        ],
        "mentors": [
            {"id": m["id"], "choice_id": m["choice_id"], "usd": m["usd"]} for m in MENTORS
        ],
    }


def apply_update(body: dict[str, Any] | None) -> dict[str, Any]:
    body = body or {}
    raw = body.get("usd")
    if raw is None or str(raw).strip() == "":
        raw = body.get("budget")
    if raw is None and body.get("enabled") is False:
        raw = 0
    if raw is not None and str(raw).strip() != "":
        set_usd(raw)
    return status()
