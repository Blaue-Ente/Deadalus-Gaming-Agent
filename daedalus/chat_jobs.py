"""Studio chat jobs — start the agent loop in the background, stream live status.

The blocking ``POST /api/v1/daedalus/chat`` contract stays for mesh / API clients.
Studio uses a job id + SSE so the composer is not frozen on ``…мисля``.
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Any

from .harness import runs as _runs
from .harness import turn_changes as _tc

log = logging.getLogger(__name__)

_JOB_TTL_S = 30 * 60
_MAX_JOBS = 48
_jobs: dict[str, dict[str, Any]] = {}


def _gc() -> None:
    now = time.time()
    for key, row in list(_jobs.items()):
        if now - float(row.get("created") or 0) > _JOB_TTL_S:
            _jobs.pop(key, None)
    extra = len(_jobs) - _MAX_JOBS
    if extra <= 0:
        return
    oldest = sorted(_jobs.items(), key=lambda kv: float(kv[1].get("created") or 0))
    for key, _ in oldest[:extra]:
        _jobs.pop(key, None)


def get_job(job_id: str) -> dict[str, Any] | None:
    _gc()
    row = _jobs.get((job_id or "").strip())
    if not row:
        return None
    return {
        "id": row["id"],
        "status": row["status"],
        "session_id": row["session_id"],
        "created": row["created"],
        "result": row.get("result"),
        "error": row.get("error"),
        "cancelled": bool(row.get("cancelled")),
        "project": row.get("project") or None,
        "changes": None if row.get("changes") is None else _tc.public(row["changes"]),
    }


async def start_chat(
    *,
    message: str,
    session_id: str,
    effort: str = "auto",
    skill_ids: list[str] | None = None,
    mcp_ids: list[str] | None = None,
    attachment_ids: list[str] | None = None,
    project: str | None = None,
) -> dict[str, Any]:
    _gc()
    job_id = uuid.uuid4().hex[:12]
    sid = (session_id or "").strip() or f"studio-{job_id}"
    before: dict[str, Any] | None = None
    if project:
        # Project chat (the classic page's project chat): the turn sees the project's files, keeps the project's own
        # history (pg-<slug>, which also puts the agent in project mode), and what it changes is recorded for review.
        message, sid = await project_message(project, message)
        before = await asyncio.to_thread(_tc.snapshot, project)
    row: dict[str, Any] = {
        "id": job_id,
        "status": "running",
        "session_id": sid,
        "created": time.time(),
        "result": None,
        "error": None,
        "cancelled": False,
        "task": None,
        "project": project or None,
        "changes": None,
    }
    _jobs[job_id] = row
    row["run_id"] = _runs.safe_open("chat", ref=f"chat:{job_id}", summary=message[:200], effort=effort,
                                    owner_session=sid)

    def _record_changes() -> None:
        """Before ANY final status: the stream's "done" event carries the job row, so the changes must be in it."""
        if not project or before is None:
            return
        try:
            row["changes"] = _tc.compare(before, _tc.snapshot(project))
        except Exception:
            log.exception("turn changes for %s failed", project)
            row["changes_error"] = True

    async def _run() -> None:
        from daedalus.stream import clear_live, set_live

        _runs.bind(row.get("run_id", ""))
        try:
            set_live(sid, "thinking", 0, "Започвам…")
            from daedalus.agent import get_daedalus

            result = await get_daedalus().chat(
                message=message,
                session_id=sid,
                effort=effort,
                skill_ids=skill_ids,
                mcp_ids=mcp_ids,
                attachment_ids=attachment_ids,
            )
            if project:
                await asyncio.to_thread(_record_changes)
            if row.get("cancelled"):
                row["status"] = "cancelled"
                _runs.sync_job(row, "chat")
                return
            row["result"] = result
            row["status"] = "done"
            _runs.sync_job(row, "chat")
        except asyncio.CancelledError:
            _record_changes()                                  # a stopped turn may still have written files
            row["status"] = "cancelled"
            row["error"] = "спряно"
            _runs.sync_job(row, "chat")
            raise
        except Exception as exc:
            log.exception("chat job %s failed", job_id)
            _record_changes()
            row["error"] = str(exc)[:400]
            row["status"] = "error"
            _runs.sync_job(row, "chat")
        finally:
            clear_live(sid)

    row["task"] = asyncio.create_task(_run())
    row["task"].add_done_callback(_runs.on_task_done(row, "chat"))
    await asyncio.sleep(0)
    return {"ok": True, "job_id": job_id, "session_id": sid}


async def project_message(slug: str, message: str) -> tuple[str, str]:
    """The classic project-chat prompt: project header, file tree and the most relevant files, then the message."""
    from . import playground as pg
    from .playground_chat_context import build_chat_file_context

    proj = await pg.get_project(slug)
    if not proj:
        raise ValueError("project not found")
    try:
        tree_summary, file_ctx = build_chat_file_context(slug)
    except Exception:
        tree_summary, file_ctx = "", ""
    full = f"[PLAYGROUND {slug} · {proj.get('kind')} · standalone]\n{tree_summary}\n{file_ctx}\n\n{message}"
    return full, f"pg-{slug}"


def revert_change(job_id: str, path: str) -> dict[str, Any]:
    row = _jobs.get((job_id or "").strip())
    if not row:
        return {"ok": False, "error": "not_found"}
    if row["status"] == "running":
        return {"ok": False, "error": "still_running"}
    change = next((c for c in (row.get("changes") or []) if c["path"] == path), None)
    if not row.get("project") or change is None:
        return {"ok": False, "error": "not_found"}
    if change.get("reverted"):
        return {"ok": False, "error": "already_reverted"}
    res = _tc.revert(row["project"], change)
    if res.get("ok"):
        change["reverted"] = res["reverted"]
    return res


def cancel(job_id: str) -> bool:
    row = _jobs.get((job_id or "").strip())
    if not row:
        return False
    row["cancelled"] = True
    task = row.get("task")
    if task is not None and not task.done():
        task.cancel()
    if row["status"] == "running":
        row["status"] = "cancelled"
        row["error"] = row.get("error") or "спряно"
        _runs.sync_job(row, "chat")
    return True


def reset() -> None:
    """Drop in-memory jobs (tests). Running tasks are cancelled."""
    for row in list(_jobs.values()):
        task = row.get("task")
        if task is not None and not task.done():
            task.cancel()
    _jobs.clear()
