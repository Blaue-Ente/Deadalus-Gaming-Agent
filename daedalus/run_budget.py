"""Per-run USD envelope — pick writer + mentor, then meter OpenRouter spend.

The $5 / $20 / $50 catalog is still a relative pick (which models). The
stop is billed ``usage.cost`` from OpenRouter, not the catalog guess. Off
leaves Studio Brain and the Mentor picker alone and does not accumulate.

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
_SPEND_KEY = "adn:daedalus_run_spend"
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
    if nxt <= 0:
        clear_spend()
    else:
        _touch_ledger_cap(nxt)
    return usd()


def _empty_ledger(cap: int = 0) -> dict[str, Any]:
    remaining = max(0.0, float(cap))
    return {
        "cap_usd": int(cap),
        "spent_usd": 0.0,
        "remaining_usd": remaining,
        "calls": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "unknown_cost_calls": 0,
        "exhausted": False,
        "last_model": "",
        "last_role": "",
        "last_source": "",
    }


def _spend_raw() -> dict[str, Any]:
    try:
        from . import kv
        raw = kv.get(_SPEND_KEY)
    except Exception:
        raw = None
    return dict(raw) if isinstance(raw, dict) else {}


def _write_spend(row: dict[str, Any]) -> None:
    from . import kv
    kv.set(_SPEND_KEY, row)


def clear_spend() -> dict[str, Any]:
    row = _empty_ledger(0)
    try:
        _write_spend(row)
    except Exception:
        log.debug("run budget spend clear skipped", exc_info=True)
    return row


def _touch_ledger_cap(cap: int) -> dict[str, Any]:
    row = dict(_spend_raw())
    if not row:
        row = _empty_ledger(cap)
    row["cap_usd"] = int(cap)
    spent = max(0.0, float(row.get("spent_usd") or 0.0))
    row["spent_usd"] = round(spent, 6)
    row["remaining_usd"] = round(max(0.0, float(cap) - spent), 6)
    row["exhausted"] = bool(cap > 0 and spent >= float(cap) - 1e-12)
    try:
        _write_spend(row)
    except Exception:
        log.debug("run budget spend cap touch skipped", exc_info=True)
    return row


def ledger() -> dict[str, Any]:
    cap = usd()
    if cap <= 0:
        return _empty_ledger(0)
    row = dict(_spend_raw())
    if not row:
        return _empty_ledger(cap)
    spent = max(0.0, float(row.get("spent_usd") or 0.0))
    out = _empty_ledger(cap)
    out.update({
        "spent_usd": round(spent, 6),
        "remaining_usd": round(max(0.0, float(cap) - spent), 6),
        "calls": int(row.get("calls") or 0),
        "prompt_tokens": int(row.get("prompt_tokens") or 0),
        "completion_tokens": int(row.get("completion_tokens") or 0),
        "unknown_cost_calls": int(row.get("unknown_cost_calls") or 0),
        "exhausted": spent >= float(cap) - 1e-12,
        "last_model": str(row.get("last_model") or ""),
        "last_role": str(row.get("last_role") or ""),
        "last_source": str(row.get("last_source") or ""),
    })
    return out


def exhausted() -> bool:
    return bool(ledger().get("exhausted"))


def would_block(provider: str | None = None) -> bool:
    """True when the next cloud LLM call must not run."""
    if not exhausted():
        return False
    p = (provider or "").strip().lower()
    if not p:
        return True
    if p in {"ollama", "lmstudio", "llamacpp", "vllm", "omniroute"}:
        return False
    return True


def stop_text() -> str:
    row = ledger()
    return (
        f"OpenRouter spend ${row['spent_usd']:.4f} reached the "
        f"${row['cap_usd']} envelope. The next model call is blocked. "
        "Raise the run budget or set it Off."
    )


def note_usage(
    parsed: dict[str, Any] | None,
    *,
    model: str = "",
    role: str = "writer",
    provider: str = "",
) -> dict[str, Any]:
    """Add one billed call to the envelope. No-op when the envelope is Off."""
    try:                                     # gap 3c: a team member's spend on the owner's shared key, always
        from .credentials import note_member_usage
        if not provider:
            from .llm_config import get_brain_config
            provider = str((get_brain_config() or {}).get("provider") or "")
        note_member_usage(parsed, provider)
    except Exception:
        pass
    cap = usd()
    if cap <= 0:
        return ledger()
    parsed = parsed or {}
    cost = parsed.get("cost_usd")
    known = bool(parsed.get("cost_known")) and cost is not None
    row = dict(_spend_raw()) or _empty_ledger(cap)
    spent = max(0.0, float(row.get("spent_usd") or 0.0))
    if known:
        spent += max(0.0, float(cost))
    row["cap_usd"] = int(cap)
    row["spent_usd"] = round(spent, 6)
    row["remaining_usd"] = round(max(0.0, float(cap) - spent), 6)
    row["calls"] = int(row.get("calls") or 0) + 1
    row["prompt_tokens"] = int(row.get("prompt_tokens") or 0) + int(
        parsed.get("prompt_tokens") or 0
    )
    row["completion_tokens"] = int(row.get("completion_tokens") or 0) + int(
        parsed.get("completion_tokens") or 0
    )
    if not known:
        row["unknown_cost_calls"] = int(row.get("unknown_cost_calls") or 0) + 1
    row["exhausted"] = spent >= float(cap) - 1e-12
    row["last_model"] = str(model or row.get("last_model") or "")
    row["last_role"] = str(role or "")
    row["last_source"] = str(parsed.get("source") or "")
    try:
        _write_spend(row)
    except Exception:
        log.debug("run budget spend write skipped", exc_info=True)
    if known:
        log.info(
            "run budget +$%.6f → $%.6f / $%s role=%s model=%s",
            float(cost), spent, cap, role, model,
        )
    elif parsed.get("prompt_tokens") or parsed.get("completion_tokens"):
        log.info(
            "run budget call had tokens but no billed cost role=%s model=%s source=%s",
            role, model, parsed.get("source"),
        )
    return ledger()


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
            f"${cap} envelope. Writer {writer['model']} + Mentor {mentor['id']} "
            f"(catalog ~${estimate}). Live stop is OpenRouter usage.cost, "
            "not this catalog guess."
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
    led = ledger()
    billed = f"billed ${led['spent_usd']:.4f} / ${led['cap_usd']}"
    return (
        f"[RUN BUDGET ${decision.get('usd')} — {billed}. "
        f"{decision.get('note')}]"
    )


def status() -> dict[str, Any]:
    ov = env_override()
    stored = persisted_usd()
    cap = usd()
    spend = ledger()
    return {
        "usd": cap,
        "active": cap > 0,
        "env_locked": ov is not None,
        "env_override": ov,
        "persisted": stored,
        "default": 0,
        "tiers": [0, 5, 20, 50],
        "plan": plan(cap),
        "spend": spend,
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
        if env_override() is not None:
            from .env_lock import EnvLockedError
            raise EnvLockedError("usd", "DAEDALUS_RUN_BUDGET")
        set_usd(raw)
    if body.get("reset_spend"):
        cap_now = usd()
        clear_spend()
        if cap_now > 0:
            _touch_ledger_cap(cap_now)
    return status()
