"""
Playground AI build — detached background jobs.

The LLM generation runs in a background asyncio.Task that is *independent* of any
client connection.  The SSE endpoint is only a viewer that subscribes to the job's
growing text buffer and replays it on (re)connect.

Why: a build started from a phone must survive the phone sleeping, the network
dropping, or the tab going inactive.  Previously the LLM stream ran inside the SSE
response generator and files were written only at the very end — so a dropped
connection killed the generation mid-stream and lost ALL generated code.

Lifecycle:
  start_build(...) -> BuildJob          # spawns detached task, returns immediately
  subscribe(job, from_offset) -> agen   # yields {event,data} dicts (token/status/done/error/_ping)
  get(job_id) / get_active(slug)        # lookup (memory first, Replit DB fallback)

Persistence: the job buffer is snapshotted to Replit DB so the result survives until
the user reconnects.  On a server restart a persisted "running" job has no live task,
so it is surfaced as "interrupted" (LLM streams cannot be resumed) with partial text.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from typing import Awaitable, Callable, Optional

from .harness import runs as _runs

log = logging.getLogger(__name__)

try:
    from replit import db as _rdb
except Exception:
    from .kv import db as _rdb

# ── Tunables ──────────────────────────────────────────────────────────────────
_KA_INTERVAL = 8.0            # seconds of silence before a keepalive ping
_PERSIST_EVERY = 2.0         # min seconds between Replit DB snapshots while running
_JOB_TTL = 3600.0            # keep finished jobs in memory this long
_MAX_TEXT = 2_000_000        # hard cap on accumulated text (safety)

_JOBS: dict[str, "BuildJob"] = {}
# Authoritative latest-job pointer per slug.  Acts as a generation token: a job
# may only write files while it is still the active job for its slug, so a build
# superseded by a newer one can never clobber the newer job's output.
_ACTIVE: dict[str, str] = {}


def _now() -> float:
    return time.time()


def _job_key(job_id: str) -> str:
    return f"adn:playground:buildjob:{job_id}"


def _active_key(slug: str) -> str:
    return f"adn:playground:buildjob:active:{slug}"


class BuildJob:
    """One AI build.  Accumulates code text + status; notifies subscribers."""

    def __init__(self, slug: str, mode: str, mode_label: str):
        self.id = uuid.uuid4().hex[:16]
        self.run_id = ""  # run-ledger id; set by start_build only
        self.slug = slug
        self.mode = mode
        self.mode_label = mode_label
        self.text = ""                  # accumulated code (replayable)
        self.status = "running"        # running | done | error | interrupted
        self.status_msg = f"Дедал мисли… [{mode_label}]"
        self.error: Optional[str] = None
        self.files: list[str] = []
        self.created = _now()
        self.updated = _now()
        self.cond = asyncio.Condition()
        self.task: Optional[asyncio.Task] = None
        self._last_persist = 0.0

    # ── snapshot / persistence ────────────────────────────────────────────────
    def snapshot(self) -> dict:
        return {
            "id": self.id, "slug": self.slug, "mode": self.mode,
            "mode_label": self.mode_label, "status": self.status,
            "status_msg": self.status_msg, "error": self.error,
            "files": self.files, "created": self.created, "updated": self.updated,
            "text_len": len(self.text),
        }

    def _persist(self, force: bool = False) -> None:
        if not force and self.status == "running" and \
                (_now() - self._last_persist) < _PERSIST_EVERY:
            return
        self._last_persist = _now()
        try:
            _rdb[_job_key(self.id)] = json.dumps(
                {"snapshot": self.snapshot(), "text": self.text[-_MAX_TEXT:]},
                ensure_ascii=False,
            )
            # Mirror the authoritative in-memory pointer instead of self.id, so an
            # old superseded job persisting on its way out can never erase the
            # newer running job's active pointer (race fix).
            _rdb[_active_key(self.slug)] = _ACTIVE.get(self.slug, self.id)
        except Exception as e:  # pragma: no cover
            log.warning("build job persist failed (%s): %s", self.id, e)

    # ── mutation helpers (notify subscribers) ─────────────────────────────────
    async def append_text(self, chunk: str) -> None:
        if not chunk:
            return
        async with self.cond:
            self.text += chunk
            if len(self.text) > _MAX_TEXT:
                self.text = self.text[-_MAX_TEXT:]
            self.updated = _now()
            self.cond.notify_all()
        self._persist()

    async def set_status(self, msg: str) -> None:
        async with self.cond:
            self.status_msg = msg
            self.updated = _now()
            self.cond.notify_all()

    async def complete(self, files: list[str]) -> None:
        async with self.cond:
            self.files = files
            self.status = "done"
            self.status_msg = f"✅ готово — {len(files)} файл(а)"
            self.updated = _now()
            self.cond.notify_all()
        self._persist(force=True)
        _runs.sync_job(self, "build")

    async def fail(self, error: str) -> None:
        async with self.cond:
            self.error = error
            self.status = "error"
            self.status_msg = "❌ грешка"
            self.updated = _now()
            self.cond.notify_all()
        self._persist(force=True)
        _runs.sync_job(self, "build")


async def subscribe(job: "BuildJob", from_offset: int = 0):
    """Yield {event,data} dicts from `from_offset` onward, then live until terminal.

    Events: token | status | done | error | _ping.  Never yields while holding the
    job lock, so a slow/stuck subscriber cannot block the generation task.
    """
    cursor = max(0, from_offset)
    sent_status: Optional[str] = None
    while True:
        out: Optional[dict] = None
        terminal = False
        async with job.cond:
            if len(job.text) > cursor:
                delta = job.text[cursor:]
                cursor = len(job.text)
                out = {"event": "token", "data": {"text": delta, "offset": cursor}}
            elif job.status_msg != sent_status:
                sent_status = job.status_msg
                out = {"event": "status", "data": {"msg": sent_status}}
            elif job.status == "done":
                out = {"event": "done",
                       "data": {"files": job.files, "count": len(job.files),
                                "offset": cursor}}
                terminal = True
            elif job.status in ("error", "interrupted"):
                out = {"event": "error",
                       "data": {"error": job.error or "прекъснат", "offset": cursor,
                                "interrupted": job.status == "interrupted"}}
                terminal = True
            else:
                try:
                    await asyncio.wait_for(job.cond.wait(), timeout=_KA_INTERVAL)
                except asyncio.TimeoutError:
                    out = {"event": "_ping", "data": {}}
        if out is not None:
            yield out
        if terminal:
            return


async def _run(
    job: "BuildJob",
    *,
    route_stream_factory: Callable[[], object],
    parse_files: Callable[[str], dict],
    write_files: Callable[[str, dict], object],
) -> None:
    """Background task body — drives the LLM stream to completion, then writes files."""
    _runs.bind(job.run_id)
    try:
        stream = route_stream_factory()
        async for ev in stream:
            t = ev.get("type")
            if t == "token":
                await job.append_text(ev.get("text", ""))
            elif t == "provider":
                await job.set_status(f"Пише код ({ev.get('name', '')})…")
            elif t == "thinking":
                await job.set_status("🧠 Reasoning…")
            elif t == "fallback":
                await job.set_status("Пробвам друг модел (предишният фейлна)…")
            elif t == "error":
                await job.fail(ev.get("error", "unknown"))
                return
            elif t == "done":
                break

        files = parse_files(job.text)
        if not files:
            await job.fail("Моделът не върна валидни файлове (липсва ===FILE=== формат).")
            return
        # Generation guard: only write if we are STILL the active job for this slug.
        # A newer build supersedes us → never clobber its output.
        if _ACTIVE.get(job.slug) != job.id:
            await job.fail("заменен от нов build")
            return
        try:
            write_files(job.slug, files)
        except Exception as exc:
            await job.fail(f"Запис се провали: {exc}")
            return
        await job.complete(list(files.keys()))
    except asyncio.CancelledError:
        # Detached task should not be cancelled by client disconnects, but if a new
        # build supersedes this one we mark it interrupted and re-raise.
        try:
            await job.fail("заменен от нов build")
        except Exception:
            pass
        raise
    except Exception as exc:  # pragma: no cover - defensive
        log.exception("build job crashed (%s)", job.id)
        await job.fail(str(exc))


def _gc() -> None:
    """Drop finished jobs older than the TTL from memory."""
    cutoff = _now() - _JOB_TTL
    for jid in [j for j, jb in _JOBS.items()
                if jb.status != "running" and jb.updated < cutoff]:
        _JOBS.pop(jid, None)


def start_build(
    slug: str,
    *,
    mode: str,
    mode_label: str,
    route_stream_factory: Callable[[], object],
    parse_files: Callable[[str], dict],
    write_files: Callable[[str, dict], object],
) -> "BuildJob":
    """Create a job, cancel any in-flight build for the slug, spawn detached task."""
    _gc()
    # Supersede any running build for the same slug.
    for jb in list(_JOBS.values()):
        if jb.slug == slug and jb.status == "running" and jb.task:
            jb.task.cancel()

    job = BuildJob(slug, mode, mode_label)
    _JOBS[job.id] = job
    _ACTIVE[slug] = job.id   # claim the slug before persisting / spawning
    job.run_id = _runs.safe_open("build", slug=slug, ref=f"build:{job.id}", summary=mode_label or mode)
    job._persist(force=True)
    job.task = asyncio.create_task(
        _run(job, route_stream_factory=route_stream_factory,
             parse_files=parse_files, write_files=write_files)
    )
    job.task.add_done_callback(_runs.on_task_done(job, "build"))
    return job


def cancel_active(slug: str) -> bool:
    """Cancel the running build job for a slug. Returns True if a job was cancelled."""
    cancelled = False
    for jb in list(_JOBS.values()):
        if jb.slug == slug and jb.status == "running" and jb.task:
            jb.task.cancel()
            jb.status = "interrupted"
            jb.error = "Спрян ръчно от потребителя."
            jb._persist(force=True)
            _runs.sync_job(jb, "build")
            cancelled = True
    return cancelled


def _decode_job(raw):
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        return json.loads(raw)
    return None


def get(job_id: str) -> Optional["BuildJob"]:
    """Return a job from memory, or a read-only reconstruction from persisted KV."""
    job = _JOBS.get(job_id)
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


def get_active(slug: str) -> Optional["BuildJob"]:
    """Return the active/last job for a slug (memory first, then persisted KV)."""
    live = [jb for jb in _JOBS.values() if jb.slug == slug]
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


def _restore(payload: dict) -> "BuildJob":
    """Rebuild a read-only job from a persisted snapshot (no live task)."""
    snap = payload.get("snapshot", {})
    job = BuildJob(snap.get("slug", ""), snap.get("mode", "create"),
                   snap.get("mode_label", ""))
    job.id = snap.get("id", job.id)
    job.text = payload.get("text", "")
    job.files = snap.get("files", []) or []
    job.error = snap.get("error")
    job.created = snap.get("created", job.created)
    job.updated = snap.get("updated", job.updated)
    status = snap.get("status", "interrupted")
    # A persisted "running" job has no live task after a restart → it's interrupted.
    if status == "running":
        status = "interrupted"
        job.error = "Сървърът рестартира по време на build — започни наново (кодът дотук е запазен)."
    job.status = status
    job.status_msg = snap.get("status_msg", "")
    return job
