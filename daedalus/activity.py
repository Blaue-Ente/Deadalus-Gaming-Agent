"""What Daedalus is doing, as the owner sees it in the Studio chat while a turn runs.

The Run Ledger already records each step of a turn: tool calls, model calls, the Mentor's plan, review and what it saw.
``compact`` turns one ledger event into a small, secret-free row for the chat stream. Model-written text (the Mentor's
brief and checklist) is passed through as-is and capped; UI words are translated in the Studio (EN and BG)."""
from __future__ import annotations

from typing import Any

_SHOWN = ("tool", "llm", "mentor_plan", "mentor_review", "mentor_perception", "state", "truncated")


def _strs(val: Any, n: int, width: int = 160) -> list[str]:
    if not isinstance(val, list):
        return []
    return [str(x)[:width] for x in val[:n] if str(x).strip()]


def compact(ev: dict[str, Any]) -> dict[str, Any] | None:
    kind = ev.get("type")
    if kind not in _SHOWN:
        return None
    p = ev.get("payload") if isinstance(ev.get("payload"), dict) else {}
    row: dict[str, Any] = {"seq": ev.get("seq"), "ts": ev.get("ts"), "type": kind}
    if kind == "tool":
        row.update({k: p.get(k) for k in ("tool", "target", "ok", "blocked", "ms")})
        row["note"] = str(p.get("note") or "")[:140]
    elif kind == "llm":
        row.update({"intent": p.get("intent"), "model": p.get("model"), "ok": p.get("ok"),
                    "ms": p.get("latency_ms"), "tokens": (p.get("prompt_tokens") or 0) + (p.get("completion_tokens") or 0),
                    "error": p.get("error_type") or ("budget" if p.get("budget_stopped") else "")})
    elif kind == "mentor_plan":
        if p.get("skipped"):
            row["skipped"] = str(p["skipped"])[:80]
        else:
            row.update({"brief": str(p.get("brief") or "")[:400], "checklist": _strs(p.get("checklist"), 10),
                        "steps": _strs(p.get("steps"), 8)})
        row["model"] = p.get("model")
    elif kind == "mentor_review":
        if p.get("skipped"):
            row["skipped"] = str(p["skipped"])[:80]
        else:
            row.update({"accept": bool(p.get("accept")), "met": _strs(p.get("met"), 10),
                        "missing": _strs(p.get("missing"), 8), "unverified": _strs(p.get("unverified"), 6),
                        "saw_frames": int(p.get("saw_frames") or 0)})
        row["model"] = p.get("model")
    elif kind == "mentor_perception":
        advice = p.get("advice") if isinstance(p.get("advice"), dict) else {}
        row.update({"observations": _strs(p.get("observations"), 3),
                    "frames": len(p.get("frame_sha256s") or []), "next_tool": advice.get("next_tool"),
                    "reason": str(advice.get("reason") or "")[:200]})
    elif kind == "state":
        row["state"] = p.get("state") or p.get("to")
    return row


def since(run_id: str, after: int, *, limit: int = 60) -> tuple[list[dict[str, Any]], int]:
    """New activity rows after ``after``, and the last ledger seq read (rows that are not shown still advance it)."""
    if not run_id:
        return [], after
    from .harness import runs

    evs = runs.safe(runs.events, run_id, after=after, limit=limit, default=[]) or []
    rows = [r for r in (compact(e) for e in evs) if r]
    return rows, (int(evs[-1]["seq"]) if evs else after)
