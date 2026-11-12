"""
Autopilot background jobs — detached, survives client disconnect.

The multi-phase pipeline (research → plan → assets → build → verify) runs in a
detached asyncio.Task independent of any client connection.  The SSE endpoint is
only a viewer that replays the growing event list on (re)connect.

Why: a 5-10 minute autonomous build started from a phone must survive the phone
sleeping, the tab going inactive, or the network dropping.  Previously the pipeline
ran inside the SSE StreamingResponse generator, so any disconnect killed it.

Lifecycle:
  start_autopilot(slug, gen_factory) → AutopilotJob  # detached task, returns immediately
  subscribe(job, from_offset)         → async gen     # yields {event,data} dicts
  get(job_id) / get_active(slug)                      # lookup
  await pause_active / resume_active / cancel_active   # control

Control model:
  • Pause is COOPERATIVE — it takes effect between pipeline steps (before the next
    LLM call), not mid-call.  status stays "running" but .paused=True.
  • Stop cancels the detached task → status "interrupted".
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from typing import AsyncIterator, Callable, Optional

from .harness import runs as _runs

log = logging.getLogger(__name__)

from .kv import db as _rdb  # explicit backend: kv decides (DAEDALUS_KV)

# ── Tunables ──────────────────────────────────────────────────────────────────
_KA_INTERVAL   = 8.0     # seconds of silence before a keepalive ping
_PERSIST_EVERY = 3.0     # min seconds between Replit DB snapshots while running
_JOB_TTL       = 7200.0  # keep finished jobs in memory this long (2 h)
_MAX_EVENTS    = 4000    # hard cap on buffered events (safety)
_TERMINAL      = ("done", "error")
_ORPHAN_MSG = (
    "Сървърът рестартира по време на Автопилот. "
    "Има event log и checkpoint под data/runs — продължи с resume, не започвай от нула."
)

_AP_JOBS:   dict[str, "AutopilotJob"] = {}
_AP_ACTIVE: dict[str, str] = {}   # slug → job_id


def _now() -> float:
    return time.time()


def _job_key(job_id: str) -> str:
    return f"adn:playground:autopilotjob:{job_id}"


def _active_key(slug: str) -> str:
    return f"adn:playground:autopilotjob:active:{slug}"


class AutopilotJob:
    """One autopilot run. Accumulates SSE events; notifies subscribers."""

    def __init__(self, slug: str, description: str):
        from .autopilot_loop_guard import RalphLoopGuard

        self.id          = uuid.uuid4().hex[:16]
        self.run_id      = ""           # run-ledger id; set by start_autopilot only
        self.slug        = slug
        self.description = description[:200]
        self.events: list[dict] = []   # [{event:str, data:dict}]
        self.status      = "running"   # running | done | error | interrupted
        self.paused      = False
        self.error: Optional[str] = None
        self.files: list[str]   = []
        self.assets: list[str]  = []
        self.created     = _now()
        self.updated     = _now()
        self.cond        = asyncio.Condition()
        self.task: Optional[asyncio.Task] = None
        self._cancelled  = False        # True once a user/replace stop was requested
        self._pause_gate = asyncio.Event()
        self._pause_gate.set()          # set = running, cleared = paused
        self._last_persist = 0.0
        self.loop_guard    = RalphLoopGuard()

    # ── persistence ──────────────────────────────────────────────────────────
    def snapshot(self) -> dict:
        return {
            "id": self.id, "slug": self.slug,
            "description": self.description,
            "status": self.status, "paused": self.paused, "error": self.error,
            "files": self.files, "assets": self.assets,
            "event_count": len(self.events),
            "created": self.created, "updated": self.updated,
        }

    def _persist(self, force: bool = False) -> None:
        if not force and self.status == "running" and \
                (_now() - self._last_persist) < _PERSIST_EVERY:
            return
        self._last_persist = _now()
        try:
            _rdb[_job_key(self.id)] = json.dumps(
                {"snapshot": self.snapshot(), "events": self.events[-_MAX_EVENTS:]},
                ensure_ascii=False,
            )
            _rdb[_active_key(self.slug)] = _AP_ACTIVE.get(self.slug, self.id)
        except Exception as exc:
            log.warning("autopilot job persist failed (%s): %s", self.id, exc)

    # ── mutation helpers (all lock + notify) ──────────────────────────────────
    async def append_event(self, event: str, data: dict) -> None:
        async with self.cond:
            if len(self.events) < _MAX_EVENTS:
                self.events.append({"event": event, "data": data})
            self.updated = _now()
            self.cond.notify_all()
        self._persist()
        self._durable(event, data if isinstance(data, dict) else {})

    def _durable(self, event: str, data: dict) -> None:
        if event in ("phase", "done", "error", "file", "interrupted") and self.run_id:
            _runs.safe_event(self.run_id, event, {
                "label": data.get("label"), "n": data.get("n") if isinstance(data.get("n"), int) else None,
                "path": data.get("path"), "message": str(data.get("message") or "")[:300] or None,
                "files": len(data.get("files") or []) if isinstance(data.get("files"), list) else None})
        try:
            from .run_engine import record
            phase = data.get("n") if isinstance(data.get("n"), int) else data.get("label")
            ckpt = {
                "status": self.status,
                "slug": self.slug,
                "phase": phase,
                "error": self.error,
                "event": event,
            }
            if event in ("phase", "done", "error", "file", "interrupted"):
                record(self.id, event, data, slug=self.slug, checkpoint=ckpt)
            else:
                record(self.id, event, data, slug=self.slug)
        except Exception:
            log.debug("durable run log skipped", exc_info=True)

    async def complete(self, files: list[str], assets: list[str]) -> None:
        async with self.cond:
            self.files   = files
            self.assets  = assets
            self.status  = "done"
            self.paused  = False
            self.updated = _now()
            self.cond.notify_all()
        self._persist(force=True)
        self._durable("done", {"files": files, "assets": assets})
        _runs.sync_job(self, "autopilot")  # after the final event, so a streaming client never misses it

    async def fail(self, error: str) -> None:
        async with self.cond:
            # Don't clobber a deliberate stop with a generic error.
            if self.status == "interrupted":
                self.cond.notify_all()
                return
            self.error   = error
            self.status  = "error"
            self.paused  = False
            self.updated = _now()
            self.cond.notify_all()
        self._persist(force=True)
        self._durable("error", {"message": error})
        _runs.sync_job(self, "autopilot")

    async def mark_interrupted(self, reason: str) -> None:
        async with self.cond:
            self.status  = "interrupted"
            self.error   = reason
            self.paused  = False
            self._cancelled = True
            self._pause_gate.set()   # release any pause wait so the task can exit
            self.updated = _now()
            self.cond.notify_all()
        self._persist(force=True)
        self._durable("interrupted", {"message": reason})
        _runs.sync_job(self, "autopilot")

    async def pause(self) -> bool:
        async with self.cond:
            if self.status != "running" or self.paused:
                return False   # re-check under lock: no post-terminal mutation
            self.paused = True
            self._pause_gate.clear()
            self.events.append({"event": "log",
                                "data": {"line": "⏸ Пауза заявена — влиза в сила след текущата стъпка.",
                                         "level": "warn"}})
            self.updated = _now()
            self.cond.notify_all()
        self._persist(force=True)
        _runs.sync_job(self, "autopilot")
        return True

    async def resume(self) -> bool:
        async with self.cond:
            if self.status != "running" or not self.paused:
                return False   # re-check under lock: no post-terminal mutation
            self.paused = False
            self._pause_gate.set()
            self.events.append({"event": "log",
                                "data": {"line": "▶️ Продължавам…", "level": "ok"}})
            self.updated = _now()
            self.cond.notify_all()
        self._persist(force=True)
        _runs.sync_job(self, "autopilot")
        return True

    async def wait_if_paused(self) -> None:
        """Cooperative pause point — blocks while paused, returns when resumed."""
        await self._pause_gate.wait()


async def subscribe(job: "AutopilotJob", from_offset: int = 0) -> AsyncIterator[dict]:
    """Yield buffered events from from_offset, then live events, then terminal.

    Yields {event, data} dicts and {event:"_ping"} keepalives. Never yields while
    holding the cond lock. Emits at most ONE terminal: if the buffer already ends
    in a done/error event it is delivered from the buffer (no synthetic dup).
    """
    cursor = max(0, from_offset)
    while True:
        out: Optional[dict] = None
        stop = False
        async with job.cond:
            if cursor < len(job.events):
                out = job.events[cursor]
                cursor += 1
            elif job.status in ("done", "error", "interrupted"):
                last = job.events[-1] if job.events else None
                if last and last.get("event") in _TERMINAL:
                    stop = True                       # terminal already delivered
                elif job.status == "done":
                    out = {"event": "done",
                           "data": {"files": job.files, "assets": job.assets}}
                    stop = True
                else:
                    out = {"event": "error",
                           "data": {"message": job.error or "interrupted",
                                    "interrupted": job.status == "interrupted"}}
                    stop = True
            else:
                try:
                    await asyncio.wait_for(job.cond.wait(), timeout=_KA_INTERVAL)
                except asyncio.TimeoutError:
                    out = {"event": "_ping", "data": {}}
        if out is not None:
            yield out
        if stop:
            return


async def _run_job(
    job: "AutopilotJob",
    gen_factory: Callable[[], AsyncIterator[tuple[str, dict]]],
) -> None:
    """Background task body — drives the pipeline generator to completion.

    Manual iteration so we can insert a cooperative pause point BEFORE each
    generator advancement (i.e. before each LLM call).
    """
    agen = gen_factory()
    _runs.bind(job.run_id)
    try:
        while True:
            await job.wait_if_paused()
            try:
                event_name, data = await agen.__anext__()
            except StopAsyncIteration:
                break

            halt = job.loop_guard.observe_event(event_name, data)
            if halt and not halt.allow:
                await job.append_event("log", {
                    "line": f"🛑 Ralph guard ({halt.code}): {halt.reason}",
                    "level": "err",
                })
                await job.mark_interrupted(halt.reason)
                return

            await job.append_event(event_name, data)

            if event_name == "done":
                verdict = job.loop_guard.evaluate_done(data)
                if verdict.allow:
                    await job.complete(data.get("files") or [], data.get("assets") or [])
                else:
                    await job.append_event("log", {
                        "line": f"🛑 Done блокиран ({verdict.code}): {verdict.reason}",
                        "level": "err",
                    })
                    await job.mark_interrupted(verdict.reason)
                return
            if event_name == "error":
                await job.fail(data.get("message") or "pipeline error")
                return
        # Generator exhausted without done/error → treat as done.
        await job.complete(job.files, job.assets)
    except asyncio.CancelledError:
        # Stop requested. cancel_active() already set status=interrupted (with its
        # own reason); a replacement from start_autopilot() only cancelled the task,
        # so finalize here. mark_interrupted is idempotent; skip only if already
        # terminal so we don't clobber the user-stop reason.
        if job.status not in _TERMINAL and job.status != "interrupted":
            await job.mark_interrupted("Заменен от нов Автопилот.")
        raise
    except Exception as exc:
        log.exception("autopilot job crashed (%s)", job.id)
        await job.fail(str(exc)[:300])
    finally:
        try:
            await agen.aclose()
        except Exception:
            pass


def _gc() -> None:
    cutoff = _now() - _JOB_TTL
    for jid in [j for j, jb in _AP_JOBS.items()
                if jb.status != "running" and jb.updated < cutoff]:
        _AP_JOBS.pop(jid, None)


def start_autopilot(
    slug: str,
    description: str,
    gen_factory: Callable[[], AsyncIterator[tuple[str, dict]]],
    *,
    run_kind: str = "autopilot",
) -> "AutopilotJob":
    """Create a job, cancel any in-flight autopilot for the slug, spawn detached task."""
    _gc()
    for jb in list(_AP_JOBS.values()):
        if jb.slug == slug and jb.status == "running" and jb.task:
            jb._cancelled = True
            jb.task.cancel()

    job = AutopilotJob(slug, description)
    _AP_JOBS[job.id] = job
    _AP_ACTIVE[slug] = job.id
    job.run_id = _runs.safe_open(run_kind, slug=slug, ref=f"autopilot:{job.id}", summary=description)
    job._persist(force=True)
    job.task = asyncio.create_task(_run_job(job, gen_factory))
    job.task.add_done_callback(_runs.on_task_done(job, "autopilot"))
    return job


async def cancel_active(slug: str) -> bool:
    cancelled = False
    for jb in list(_AP_JOBS.values()):
        if jb.slug == slug and jb.status == "running":
            await jb.mark_interrupted("Спрян от потребителя.")
            if jb.task:
                jb.task.cancel()
            cancelled = True
    return cancelled


async def pause_active(slug: str) -> bool:
    job = get_active(slug)
    return bool(job and await job.pause())


async def resume_active(slug: str) -> bool:
    job = get_active(slug)
    return bool(job and await job.resume())


def _decode_job(raw):
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        return json.loads(raw)
    return None


def get(job_id: str) -> Optional["AutopilotJob"]:
    job = _AP_JOBS.get(job_id)
    if job is not None:
        return job
    try:
        raw = _rdb.get(_job_key(job_id))
    except Exception:
        raw = None
    payload = _decode_job(raw)
    if not payload:
        return None
    return _restore(payload)


def any_running() -> bool:
    """True when an in-memory autopilot job is still in flight."""
    return any(getattr(jb, "status", "") == "running" for jb in _AP_JOBS.values())


def get_active(slug: str) -> Optional["AutopilotJob"]:
    live = [jb for jb in _AP_JOBS.values() if jb.slug == slug]
    if live:
        live.sort(key=lambda j: j.created, reverse=True)
        return live[0]
    try:
        jid = _rdb.get(_active_key(slug))
    except Exception:
        jid = None
    if not jid:
        return None
    return get(jid)


def _restore(payload: dict) -> "AutopilotJob":
    snap = payload.get("snapshot", {})
    job = AutopilotJob(snap.get("slug", ""), snap.get("description", ""))
    job.id      = snap.get("id", job.id)
    job.events  = payload.get("events", [])
    job.files   = snap.get("files", []) or []
    job.assets  = snap.get("assets", []) or []
    job.error   = snap.get("error")
    job.created = snap.get("created", job.created)
    job.updated = snap.get("updated", job.updated)
    status      = snap.get("status", "interrupted")
    if status == "running":
        # Server restarted mid-run — the in-memory task is gone.
        status    = "interrupted"
        job.error = _ORPHAN_MSG
    job.status = status
    return job


def recover_orphaned_jobs() -> int:
    """Called once at startup — scans Replit DB for jobs still marked 'running'
    (their asyncio task died with the previous server process) and marks them
    'interrupted' so the UI shows them correctly and the user can restart.

    Returns the number of jobs that were recovered.
    """
    recovered = 0
    _PREFIX = "adn:playground:autopilotjob:"
    _ACTIVE  = _PREFIX + "active:"
    try:
        keys = list(_rdb.keys())
    except Exception:
        return 0
    for k in keys:
        if not k.startswith(_PREFIX) or k.startswith(_ACTIVE):
            continue
        try:
            raw = _rdb.get(k)
            if not raw:
                continue
            payload = json.loads(raw) if isinstance(raw, str) else dict(raw)
            snap = payload.get("snapshot", {})
            if snap.get("status") != "running":
                continue
            # Orphan found — mark interrupted directly in DB (sync, no asyncio needed)
            snap["status"]  = "interrupted"
            snap["error"]   = _ORPHAN_MSG
            snap["updated"] = _now()
            payload["snapshot"] = snap
            _rdb[k] = json.dumps(payload, ensure_ascii=False)
            recovered += 1
            log.info(
                "autopilot orphan recovered: job=%s slug=%s",
                snap.get("id", "?"), snap.get("slug", "?"),
            )
        except Exception as exc:
            log.warning("autopilot orphan recovery error for key %s: %s", k, exc)
    return recovered
