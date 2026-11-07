"""Run ledger: one durable record of every job the harness starts.

The six job systems (chat, build, autopilot, guided, full autopilot, planner) keep their own
routes, streams and in-memory state. This ledger *observes* them: each start opens a Run, each state
change and notable event is recorded with a sequence number, and a restart marks anything that was
running as ``unknown``. It never changes how a job runs, and every hook is exception-safe.

States: queued, running, paused, stopping | stopped, succeeded, failed, unknown (terminal).
A lost or timed-out run is ``unknown``; nothing here ever invents a success.

Storage: tables ``harness_runs`` and ``harness_run_events`` in the standalone ``daedalus.sqlite``
(additive, own connection, created on first use). Writes are small synchronous commits (milestone events
only, not every stream chunk).

Assumptions: Daedalus runs as ONE server process per data directory (the desktop launcher and the
container both do). ``recover_orphans`` therefore treats every live run at startup as lost; with several
workers sharing one data directory a starting worker would wrongly mark another's live runs ``unknown``.
The job systems stay the executors: the ledger records, it does not cancel. A newer run only supersedes an
older one of the SAME kind, which is what each job system actually cancels itself.
"""
from __future__ import annotations

import contextvars
import json
import logging
import sqlite3
import threading
import time
import uuid
from typing import Any, Callable

log = logging.getLogger(__name__)

KINDS = ("chat", "build", "autopilot", "guided", "full_autopilot", "planner", "verify", "play_session", "publish")
# Kinds that write a project's files: only one may be live per slug (the latest owns it).
BUILD_KINDS = frozenset({"build", "autopilot", "guided", "full_autopilot"})
STATES = ("queued", "running", "paused", "stopping", "stopped", "succeeded", "failed", "unknown")
LIVE = frozenset({"queued", "running", "paused", "stopping"})
TERMINAL = frozenset({"stopped", "succeeded", "failed", "unknown"})

MAX_EVENTS_PER_RUN = 5000
_MAX_PAYLOAD = 4000

_SCHEMA = """
CREATE TABLE IF NOT EXISTS harness_runs (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    slug TEXT NOT NULL DEFAULT '',
    ref TEXT NOT NULL DEFAULT '',
    parent_id TEXT,
    state TEXT NOT NULL,
    created REAL NOT NULL,
    updated REAL NOT NULL,
    owner_session TEXT NOT NULL DEFAULT '',
    effort TEXT NOT NULL DEFAULT '',
    budget_snapshot TEXT NOT NULL DEFAULT '',
    proof_level INTEGER,
    summary TEXT NOT NULL DEFAULT '',
    error TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ix_harness_runs_slug ON harness_runs (slug, created);
CREATE INDEX IF NOT EXISTS ix_harness_runs_ref ON harness_runs (ref);
CREATE INDEX IF NOT EXISTS ix_harness_runs_state ON harness_runs (state);
CREATE TABLE IF NOT EXISTS harness_run_events (
    run_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    ts REAL NOT NULL,
    type TEXT NOT NULL,
    payload TEXT NOT NULL DEFAULT '{}',
    artifact_path TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (run_id, seq)
);
"""

_lock = threading.RLock()
_conns: dict[str, sqlite3.Connection] = {}

# The run the current task belongs to; the gateway reads it so model calls attach to their run.
_CURRENT: contextvars.ContextVar[str] = contextvars.ContextVar("daedalus_run_id", default="")


class RunError(ValueError):
    """An invalid ledger operation (unknown run, illegal transition)."""


def _conn() -> sqlite3.Connection:
    from ..store import db_path

    path = str(db_path())
    with _lock:
        conn = _conns.get(path)
        if conn is None:
            conn = sqlite3.connect(path, check_same_thread=False, timeout=10)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA busy_timeout = 5000")
            conn.execute("PRAGMA synchronous = NORMAL")  # this connection only; keeps per-event commits cheap
            conn.executescript(_SCHEMA)
            conn.commit()
            _conns[path] = conn
        return conn


def reset() -> None:
    """Close cached connections (tests change the data root between runs)."""
    with _lock:
        for conn in _conns.values():
            try:
                conn.close()
            except Exception:
                pass
        _conns.clear()


def _now() -> float:
    return time.time()


def _row(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    out = dict(row)
    out["live"] = out["state"] in LIVE
    return out


# ---- current-run context ---------------------------------------------------------------------------
def bind(run_id: str) -> contextvars.Token:
    return _CURRENT.set(run_id or "")


def unbind(token: contextvars.Token) -> None:
    _CURRENT.reset(token)


def current_run_id() -> str:
    return _CURRENT.get()


# ---- core ------------------------------------------------------------------------------------------
def open_run(kind: str, *, slug: str = "", ref: str = "", summary: str = "", effort: str = "",
             owner_session: str = "", parent_id: str | None = None, state: str = "running") -> str:
    if kind not in KINDS:
        raise RunError(f"unknown run kind {kind!r}")
    if state not in ("queued", "running"):
        raise RunError("a run opens as queued or running")
    run_id = uuid.uuid4().hex[:16]
    snapshot = ""
    try:
        from .. import run_budget
        st = run_budget.status()
        snapshot = json.dumps({"usd": st.get("usd"), "spent": (st.get("spend") or {}).get("spent_usd")})
    except Exception:
        pass
    now = _now()
    with _lock:
        conn = _conn()
        conn.execute(
            "INSERT INTO harness_runs (id, kind, slug, ref, parent_id, state, created, updated, owner_session, "
            "effort, budget_snapshot, summary) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (run_id, kind, slug or "", ref or "", parent_id, state, now, now, owner_session or "", effort or "",
             snapshot, (summary or "")[:500]),
        )
        conn.commit()
    event(run_id, "state", {"from": None, "to": state, "reason": "opened"})
    if kind in BUILD_KINDS and slug:
        _supersede(slug, keep=run_id, kind=kind)
    return run_id


def _supersede(slug: str, *, keep: str, kind: str) -> None:
    """A newer run of the same kind on a slug replaces the older live one.

    Only the same kind: that is what each job system cancels itself (start_autopilot cancels autopilot,
    start_guided cancels guided, ...). Different kinds can still run together; ``owner_of`` reports the
    latest live one, and the ledger does not claim to have stopped a job it cannot stop.
    """
    with _lock:
        rows = _conn().execute(
            "SELECT id FROM harness_runs WHERE slug=? AND id<>? AND kind=? "
            "AND state IN ('queued','running','paused','stopping')", (slug, keep, kind)).fetchall()
    for r in rows:
        try:
            set_state(r["id"], "stopped", reason=f"superseded by {keep}")
        except RunError:
            pass


def get(run_id: str) -> dict[str, Any] | None:
    with _lock:
        return _row(_conn().execute("SELECT * FROM harness_runs WHERE id=?", (run_id,)).fetchone())


def find_by_ref(ref: str) -> dict[str, Any] | None:
    if not ref:
        return None
    with _lock:
        return _row(_conn().execute(
            "SELECT * FROM harness_runs WHERE ref=? ORDER BY created DESC LIMIT 1", (ref,)).fetchone())


def list_runs(*, slug: str = "", kind: str = "", state: str = "", live_only: bool = False,
              limit: int = 50) -> list[dict[str, Any]]:
    where, args = [], []
    if slug:
        where.append("slug=?"); args.append(slug)
    if kind:
        where.append("kind=?"); args.append(kind)
    if state:
        where.append("state=?"); args.append(state)
    if live_only:
        where.append("state IN ('queued','running','paused','stopping')")
    sql = "SELECT * FROM harness_runs" + (" WHERE " + " AND ".join(where) if where else "")
    sql += " ORDER BY created DESC LIMIT ?"
    args.append(max(1, min(int(limit), 500)))
    with _lock:
        return [_row(r) for r in _conn().execute(sql, args).fetchall()]


def owner_of(slug: str) -> dict[str, Any] | None:
    """The live build-type run that currently owns ``slug``, if any."""
    with _lock:
        return _row(_conn().execute(
            "SELECT * FROM harness_runs WHERE slug=? AND state IN ('queued','running','paused','stopping') "
            "AND kind IN ('build','autopilot','guided','full_autopilot') ORDER BY created DESC LIMIT 1",
            (slug,)).fetchone())


def event(run_id: str, type: str, payload: dict[str, Any] | None = None, *, artifact_path: str = "") -> int:
    """Append an event; returns its sequence number (0 when the per-run cap is reached)."""
    body = json.dumps(payload or {}, ensure_ascii=False, default=str)
    if len(body) > _MAX_PAYLOAD:
        body = json.dumps({"truncated": True, "preview": body[:_MAX_PAYLOAD - 80]}, ensure_ascii=False)
    with _lock:
        conn = _conn()
        seq = (conn.execute("SELECT COALESCE(MAX(seq),0)+1 FROM harness_run_events WHERE run_id=?",
                            (run_id,)).fetchone()[0])
        if seq > MAX_EVENTS_PER_RUN + 200:
            return 0  # hard ceiling, even for state events
        if seq > MAX_EVENTS_PER_RUN and type != "state":
            if seq == MAX_EVENTS_PER_RUN + 1:  # say once that later events were dropped
                conn.execute("INSERT INTO harness_run_events (run_id, seq, ts, type, payload, artifact_path) "
                             "VALUES (?,?,?,?,?,?)",
                             (run_id, seq, _now(), "truncated",
                              json.dumps({"reason": f"event cap {MAX_EVENTS_PER_RUN} reached"}), ""))
                conn.commit()
            return 0
        conn.execute("INSERT INTO harness_run_events (run_id, seq, ts, type, payload, artifact_path) VALUES (?,?,?,?,?,?)",
                     (run_id, seq, _now(), type, body, artifact_path or ""))
        conn.execute("UPDATE harness_runs SET updated=? WHERE id=?", (_now(), run_id))
        conn.commit()
        return int(seq)


def events(run_id: str, *, after: int = 0, limit: int = 500) -> list[dict[str, Any]]:
    with _lock:
        rows = _conn().execute(
            "SELECT seq, ts, type, payload, artifact_path FROM harness_run_events WHERE run_id=? AND seq>? "
            "ORDER BY seq LIMIT ?", (run_id, int(after), max(1, min(int(limit), 2000)))).fetchall()
    out = []
    for r in rows:
        try:
            payload = json.loads(r["payload"])
        except Exception:
            payload = {"raw": r["payload"]}
        out.append({"seq": r["seq"], "ts": r["ts"], "type": r["type"], "payload": payload,
                    "artifact_path": r["artifact_path"] or None})
    return out


def set_state(run_id: str, state: str, *, reason: str = "", error: str = "", summary: str | None = None) -> bool:
    """Move a run to ``state``. Terminal runs never change; returns False when nothing changed."""
    if state not in STATES:
        raise RunError(f"unknown state {state!r}")
    with _lock:
        conn = _conn()
        row = conn.execute("SELECT state FROM harness_runs WHERE id=?", (run_id,)).fetchone()
        if row is None:
            raise RunError(f"unknown run {run_id!r}")
        old = row["state"]
        if old == state:
            return False
        if old in TERMINAL:
            return False  # a finished run is never reopened or re-labelled (no late "succeeded")
        if state == "queued":
            raise RunError("a run cannot go back to queued")
        sets, args = ["state=?", "updated=?"], [state, _now()]
        if error:
            sets.append("error=?"); args.append(error[:500])
        if summary is not None:
            sets.append("summary=?"); args.append(summary[:500])
        conn.execute(f"UPDATE harness_runs SET {', '.join(sets)} WHERE id=?", (*args, run_id))
        conn.commit()
    event(run_id, "state", {"from": old, "to": state, "reason": reason or None, "error": error[:300] or None})
    return True


def set_proof_level(run_id: str, level: int) -> None:
    with _lock:
        conn = _conn()
        conn.execute("UPDATE harness_runs SET proof_level=?, updated=? WHERE id=? AND (proof_level IS NULL OR proof_level<?)",
                     (int(level), _now(), run_id, int(level)))
        conn.commit()


def recover_orphans(reason: str = "server restarted while the run was live") -> int:
    """After a restart no task owns a live run any more: mark them ``unknown`` (never success)."""
    with _lock:
        ids = [r["id"] for r in _conn().execute(
            "SELECT id FROM harness_runs WHERE state IN ('queued','running','paused','stopping')").fetchall()]
    n = 0
    for run_id in ids:
        try:
            if set_state(run_id, "unknown", reason=reason, error=reason):
                n += 1
        except RunError:
            pass
    if n:
        log.warning("run ledger: %d orphaned run(s) marked unknown", n)
    return n


# ---- exception-safe hooks for the job systems ---------------------------------------------------------
def safe(fn: Callable[..., Any], *args: Any, default: Any = None, **kwargs: Any) -> Any:
    try:
        return fn(*args, **kwargs)
    except Exception:  # a ledger failure must never affect a job
        log.debug("run ledger hook failed", exc_info=True)
        return default


def safe_open(kind: str, **kw: Any) -> str:
    return safe(open_run, kind, default="", **kw) or ""


def safe_event(run_id: str, type: str, payload: dict[str, Any] | None = None, **kw: Any) -> None:
    if run_id:
        safe(event, run_id, type, payload, **kw)


def safe_state(run_id: str, state: str, **kw: Any) -> None:
    if run_id:
        safe(set_state, run_id, state, **kw)


# job.status -> run state, per job system
STATUS_MAP: dict[str, dict[str, str]] = {
    "autopilot": {"running": "running", "done": "succeeded", "error": "failed", "interrupted": "stopped"},
    "guided": {"running": "running", "checkpoint": "paused", "done": "succeeded", "error": "failed",
               "interrupted": "stopped"},
    "build": {"running": "running", "done": "succeeded", "error": "failed", "interrupted": "stopped"},
    "chat": {"running": "running", "done": "succeeded", "error": "failed", "cancelled": "stopped"},
}


def ensure_job_run(job: Any, kind: str, *, slug: str, ref: str, summary: str = "") -> str:
    """The job's live run id, opening a new linked run when it has none or its run already finished.

    A guided job resumed after a restart (its old run is ``unknown``) must not run untracked.
    """
    run_id = getattr(job, "run_id", "") or ""
    cur = safe(get, run_id) if run_id else None
    if cur and cur.get("live"):
        return run_id
    return safe_open(kind, slug=slug, ref=ref, summary=summary, parent_id=run_id or None) or run_id


def sync_job(job: Any, kind: str) -> None:
    """Bring the run in line with the job's own status (idempotent, exception-safe)."""
    run_id = getattr(job, "run_id", "") if not isinstance(job, dict) else job.get("run_id", "")
    if not run_id:
        return
    status = job.get("status") if isinstance(job, dict) else getattr(job, "status", "")
    state = STATUS_MAP.get(kind, {}).get(str(status))
    if not state:
        return
    paused = bool(getattr(job, "paused", False)) if not isinstance(job, dict) else False
    if state == "running" and paused:
        state = "paused"
    err = (job.get("error") if isinstance(job, dict) else getattr(job, "error", None)) or ""
    safe_state(run_id, state, error=str(err) if state in ("failed", "stopped") else "")


def on_task_done(job: Any, kind: str) -> Callable[[Any], None]:
    """Done-callback for a job's asyncio task: a task that ends without a terminal job status is reconciled."""
    def _cb(task: Any) -> None:
        run_id = getattr(job, "run_id", "") if not isinstance(job, dict) else job.get("run_id", "")
        if not run_id:
            return
        sync_job(job, kind)
        cur = safe(get, run_id) or {}
        if cur.get("state") in LIVE and cur.get("state") != "paused":
            if task.cancelled():
                safe_state(run_id, "stopped", reason="task cancelled")
            elif task.exception() is not None:
                safe_state(run_id, "failed", error=str(task.exception())[:300])
            else:  # the task ended but the job never reached a terminal status: never claim success
                safe_state(run_id, "unknown", reason="task ended without a terminal job status",
                           error="task ended without a terminal job status")
    return _cb


# ---- gateway trace sink ------------------------------------------------------------------------------
def _gateway_sink(ev: dict[str, Any]) -> None:
    run_id = ev.get("run_id") or ""
    # A task spawned inside a run inherits its id; once that run is finished its calls are not its events.
    if run_id and (safe(get, run_id) or {}).get("live"):
        keep = ("intent", "provider", "model", "source", "ok", "error_type", "budget_stopped", "latency_ms",
                "prompt_tokens", "completion_tokens", "cost_usd", "cost_known", "image_sha256")
        safe_event(run_id, "llm", {k: ev.get(k) for k in keep})


def install() -> None:
    """Attach the ledger to the gateway trace (idempotent)."""
    from . import gateway

    gateway.add_trace_sink(_gateway_sink)
