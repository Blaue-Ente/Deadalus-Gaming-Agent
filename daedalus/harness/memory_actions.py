"""Review → Memory: what Daedalus remembers, what it keeps track of, and the few actions on it.

Ported from the classic page (memory stats, episodic facts, consolidation, navigator counters, the task ledger, sessions,
progress, mode, manual web/GitHub search, clarification stats, style lesson). Rules:
* a read that fails says ``available: False`` (never a row of zeros that looks like an empty memory);
* anything that loses data (delete a fact, reset the navigator counters) needs ``confirm``;
* a model-driven action (consolidate, style lesson) is confirmed, runs once at a time in the background and records how it
  ended (``ok`` only when it really did its work; nothing to do is said as such, never as success with a lesson);
* a manual search is bounded and says how many results were saved as discoveries (that is what the old route did too).
Answers are codes and numbers so the Studio can say them in the owner's language."""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

log = logging.getLogger(__name__)

QUERY_MAX, TITLE_MAX, SUMMARY_MAX = 300, 80, 400          # the ledger keeps 80/400 characters: refuse more, never cut silently
SEARCH_LIMIT_MAX, LIST_MAX = 10, 200
TASK_STATES = ("open", "done", "rework")


def _need(confirm: Any) -> dict[str, Any] | None:
    return None if confirm is True else {"ok": False, "error": "confirm_required"}


def _clamp(value: Any, hi: int, default: int) -> int:
    try:
        return max(1, min(hi, int(value)))
    except (TypeError, ValueError):
        return default


def _agent() -> Any:
    from ..agent import get_daedalus

    return get_daedalus()


# ---- reads -----------------------------------------------------------------------------------------------
def memory_stats() -> dict[str, Any]:
    try:
        d = _agent()
        base, mem0 = d.memory, getattr(d, "mem0", None)
        counts = {name: getattr(base, f"{name}_col").count()
                  for name in ("global", "sessions", "artifacts", "lessons", "episodes", "discoveries")}
        counts["episodic_facts"] = mem0.episodic.count() if mem0 else None
        return {"available": True, "counts": counts, "episodic": mem0 is not None}
    except Exception as exc:
        log.warning("memory stats unavailable: %s", exc)
        return {"available": False, "reason": type(exc).__name__}


def navigator_stats() -> dict[str, Any]:
    from ..navigator import get_navigator_stats

    return get_navigator_stats()


def mode() -> dict[str, Any]:
    from .. import mode as _md

    return {"mode": _md.get_mode(), "valid": list(_md.VALID_MODES)}


def progress() -> dict[str, Any]:
    from ..outbox import get_progress

    p = get_progress() or {}
    return {"active": bool(p.get("active")), "task": str(p.get("task_name") or ""), "message": str(p.get("message") or ""),
            "pct": p.get("pct"), "updated_at": p.get("updated_at")}


async def clarification_stats() -> dict[str, Any]:
    from ..clarification_tracker import get_clarification_stats

    stats = await get_clarification_stats()
    if not stats:                                                    # the tracker answers {} when its table cannot be read
        return {"available": False}
    return {"available": True, **stats}


def consolidations(limit: int = 5) -> list[dict[str, Any]]:
    from ..reflection import read_consolidation_log

    out = []
    for run in read_consolidation_log(limit=max(1, min(20, int(limit)))):
        out.append({k: run.get(k) for k in ("finished_at", "scanned", "merged", "deleted", "promoted", "pruned", "elapsed_seconds")}
                   | {"contradictions": len(run.get("contradictions") or []), "errors": len(run.get("errors") or [])})
    return out


def _overview_sync() -> dict[str, Any]:
    return {"memory": memory_stats(), "navigator": navigator_stats(), "mode": mode(), "progress": progress(),
            "consolidations": consolidations()}


async def overview() -> dict[str, Any]:
    local = await asyncio.to_thread(_overview_sync)                    # Chroma counts, kv and a log file: not on the event loop
    return {"ok": True, **local, "clarifications": await clarification_stats(), "jobs": jobs_status()}


SCAN_MAX = 5000                                                       # the store returns facts in insertion order, not by age


def episodic(limit: Any = 50) -> dict[str, Any]:
    n = _clamp(limit, LIST_MAX, 50)
    try:
        mem0 = getattr(_agent(), "mem0", None)
        if mem0 is None:
            return {"ok": True, "available": False, "facts": []}
        total = mem0.episodic.count()
        everything = mem0.episodic.all_facts(limit=SCAN_MAX)           # sorted newest first after the read
        facts = everything[:n]
    except Exception as exc:
        log.warning("episodic memory unavailable: %s", exc)
        return {"ok": True, "available": False, "facts": []}
    now = time.time()
    out = []
    for f in facts:
        meta = f.get("metadata") or {}
        ts = float(meta.get("ts", now) or now)
        out.append({"id": f.get("id"), "content": f.get("content") or "", "kind": meta.get("kind", "general"),
                    "importance": float(meta.get("importance", 1.0) or 0), "access_count": int(meta.get("access_count", 0) or 0),
                    "promoted": bool(meta.get("promoted_to_lesson", False)), "age_hours": round((now - ts) / 3600.0, 1)})
    return {"ok": True, "available": True, "facts": out, "total": total, "shown": len(out)}


def delete_fact(fact_id: str, confirm: Any) -> dict[str, Any]:
    if (refused := _need(confirm)):
        return refused
    try:
        mem0 = getattr(_agent(), "mem0", None)
        if mem0 is None:
            return {"ok": False, "error": "unavailable"}
        if not mem0.episodic.exists(fact_id):                       # Chroma deletes a missing id without complaint
            return {"ok": False, "error": "not_found"}
        if not mem0.episodic.delete([fact_id]):
            return {"ok": False, "error": "delete_failed"}
    except Exception as exc:
        log.warning("episodic delete unavailable: %s", exc)
        return {"ok": False, "error": "unavailable"}
    try:
        mem0.rebuild_bm25_indices()
    except Exception as exc:                                         # the fact IS gone; only the search index is behind
        log.warning("bm25 rebuild after delete failed: %s", exc)
        return {"ok": True, "fact_id": fact_id, "index_stale": True}
    return {"ok": True, "fact_id": fact_id}


def reset_navigator(confirm: Any) -> dict[str, Any]:
    from ..navigator import reset_navigator_stats

    if (refused := _need(confirm)):
        return refused
    reset_navigator_stats()
    return {"ok": True}


def set_mode(value: Any) -> dict[str, Any]:
    from .. import mode as _md

    try:
        return {"ok": True, "mode": _md.set_mode(str(value or ""))}
    except ValueError:
        return {"ok": False, "error": "bad_mode", "valid": list(_md.VALID_MODES)}


def clear_progress() -> dict[str, Any]:
    from ..outbox import clear_progress as _clear

    _clear()
    return {"ok": True}


# ---- ledger ----------------------------------------------------------------------------------------------
def tasks(status: Any = None, limit: Any = 50) -> dict[str, Any]:
    from .. import continuity

    st = str(status or "") or None
    if st and st not in TASK_STATES:
        return {"ok": False, "error": "bad_status"}
    rows = continuity.list_tasks(status=st, limit=_clamp(limit, LIST_MAX, 50))
    return {"ok": True, "tasks": [{k: t.get(k) for k in ("id", "title", "status", "summary", "created_ts", "updated_ts")} for t in rows]}


def create_task(title: Any, summary: Any) -> dict[str, Any]:
    from .. import continuity

    t, s = str(title or "").strip(), str(summary or "").strip()
    if not t:
        return {"ok": False, "error": "title_required"}
    if len(t) > TITLE_MAX or len(s) > SUMMARY_MAX:
        return {"ok": False, "error": "too_long"}
    task = continuity.create_task(title=t, summary=s)
    return {"ok": True, "task_id": task.get("id")}


def _session_store() -> Any:
    """The agent's memory, after checking its sessions store can be read (the list helpers below answer [] on any error)."""
    memory = _agent().memory
    memory.sessions_col.count()
    return memory


def sessions(limit: Any = 30) -> dict[str, Any]:
    n = _clamp(limit, LIST_MAX, 30)
    try:
        rows = _session_store().list_sessions(limit=n)
    except Exception as exc:
        log.warning("sessions unavailable: %s", exc)
        return {"ok": True, "available": False, "sessions": []}
    return {"ok": True, "available": True, "sessions": rows}


def session_turns(session_id: str, limit: Any = 100) -> dict[str, Any]:
    n = _clamp(limit, 500, 100)
    try:
        turns = _session_store().session_history(session_id, limit=n)
    except Exception as exc:
        log.warning("session history unavailable: %s", exc)
        return {"ok": False, "error": "unavailable"}
    if not turns:
        return {"ok": False, "error": "not_found"}
    return {"ok": True, "session_id": session_id,
            "turns": [{"role": (t.get("metadata") or {}).get("role", "?"), "content": str(t.get("content") or "")[:4000]} for t in turns]}


# ---- manual search ---------------------------------------------------------------------------------------
async def search(kind: str, query: Any, limit: Any = 5) -> dict[str, Any]:
    q = str(query or "").strip()
    if not q:
        return {"ok": False, "error": "query_required"}
    if len(q) > QUERY_MAX:
        return {"ok": False, "error": "too_long"}
    n = _clamp(limit, SEARCH_LIMIT_MAX, 5)
    try:
        agent = _agent()
        res = await (agent.web_search(query=q, limit=n) if kind == "web" else agent.github_search(query=q, limit=n))
    except Exception as exc:
        log.warning("%s search failed: %s", kind, exc)
        if kind != "web":
            return {"ok": False, "error": "search_failed"}
        try:                                                         # the classic route falls back the same way (nothing saved)
            from ..web_search_standalone import web_search

            res = await web_search(q, limit=n)
        except Exception as extra:
            log.warning("standalone web search failed: %s", extra)
            return {"ok": False, "error": "search_failed"}
    if not isinstance(res, dict) or res.get("ok") is False:
        return {"ok": False, "error": "search_failed"}
    rows = res.get("results") or res.get("repos") or res.get("items") or []
    out = [{"title": str(r.get("title") or r.get("full_name") or r.get("name") or ""),
            "url": str(r.get("url") or r.get("html_url") or ""),
            "snippet": str(r.get("snippet") or r.get("description") or "")[:400]} for r in rows if isinstance(r, dict)]
    return {"ok": True, "kind": kind, "results": out, "discoveries_recorded": int(res.get("discoveries_recorded") or 0)}


# ---- model-driven jobs -----------------------------------------------------------------------------------
_jobs: dict[str, asyncio.Task] = {}
_last: dict[str, dict[str, Any]] = {}


def jobs_status() -> dict[str, Any]:
    return {"running": sorted(k for k, t in _jobs.items() if not t.done()), "last": dict(_last)}


async def _consolidate() -> dict[str, Any]:
    from ..agent import _call_claude
    from ..reflection import run_consolidation_cycle

    agent = _agent()
    if getattr(agent, "mem0", None) is None:
        return {"ok": False, "error": "unavailable"}
    res = await run_consolidation_cycle(_call_claude, agent)
    errors = res.get("errors") or []
    out = {k: res.get(k) for k in ("scanned", "merged", "deleted", "promoted", "pruned")}
    if errors:
        return {"ok": False, "error": "cycle_errors", "errors": len(errors), **out}
    return {"ok": True, **out}


async def _lesson() -> dict[str, Any]:
    from ..clarification_tracker import generate_daily_lesson

    if not (await clarification_stats())["available"]:               # its read answers [] on errors: "nothing to learn" would be a lie
        return {"ok": False, "error": "unavailable"}
    res = await generate_daily_lesson(_agent())
    if res.get("ok") and not res.get("lesson"):
        return {"ok": True, "nothing_to_learn": True}
    if res.get("ok"):
        return {"ok": True, "processed": int(res.get("processed") or 0)}
    return {"ok": False, "error": "lesson_failed"}


_WORK = {"consolidate": _consolidate, "lesson": _lesson}


async def start_job(name: str, confirm: Any) -> dict[str, Any]:
    if name not in _WORK:
        return {"ok": False, "error": "not_found"}
    if (refused := _need(confirm)):
        return refused
    task = _jobs.get(name)
    if task and not task.done():
        return {"ok": False, "error": "already_running"}

    async def run() -> None:
        started = time.time()
        try:
            res = await _WORK[name]()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.exception("memory job %s failed", name)
            res = {"ok": False, "error": type(exc).__name__}
        _last[name] = {**res, "finished_at": time.time(), "seconds": round(time.time() - started, 1)}

    _last.pop(name, None)
    _jobs[name] = asyncio.get_running_loop().create_task(run())
    return {"ok": True, "started": True, "job": name}
