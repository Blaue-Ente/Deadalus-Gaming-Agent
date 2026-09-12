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
    }


async def start_chat(
    *,
    message: str,
    session_id: str,
    effort: str = "auto",
    skill_ids: list[str] | None = None,
    mcp_ids: list[str] | None = None,
    attachment_ids: list[str] | None = None,
) -> dict[str, Any]:
    _gc()
    job_id = uuid.uuid4().hex[:12]
    sid = (session_id or "").strip() or f"studio-{job_id}"
    row: dict[str, Any] = {
        "id": job_id,
        "status": "running",
        "session_id": sid,
        "created": time.time(),
        "result": None,
        "error": None,
        "cancelled": False,
        "task": None,
    }
    _jobs[job_id] = row

    async def _run() -> None:
        from daedalus.stream import clear_live, set_live

        try:
            set_live(sid, "thinking", 0, "Започвам…")
            from daedalus.legacy_adapter import adapter_enabled

            if adapter_enabled():
                from daedalus.legacy_adapter import (
                    LegacyTurnRequest,
                    run_turn,
                    unwrap_chat,
                )

                packed = await run_turn(
                    LegacyTurnRequest(
                        message=message,
                        session_id=sid,
                        effort=effort,
                        skill_ids=skill_ids,
                        mcp_ids=mcp_ids,
                        attachment_ids=attachment_ids,
                    )
                )
                if not packed.chat:
                    msg = (
                        packed.error.message
                        if packed.error is not None
                        else "legacy adapter failed"
                    )
                    raise RuntimeError(msg[:400])
                result = unwrap_chat(packed)
            else:
                from daedalus.agent import get_daedalus

                result = await get_daedalus().chat(
                    message=message,
                    session_id=sid,
                    effort=effort,
                    skill_ids=skill_ids,
                    mcp_ids=mcp_ids,
                    attachment_ids=attachment_ids,
                )
            if row.get("cancelled"):
                row["status"] = "cancelled"
                return
            row["result"] = result
            row["status"] = "done"
        except asyncio.CancelledError:
            row["status"] = "cancelled"
            row["error"] = "спряно"
            raise
        except Exception as exc:
            log.exception("chat job %s failed", job_id)
            row["error"] = str(exc)[:400]
            row["status"] = "error"
        finally:
            clear_live(sid)

    row["task"] = asyncio.create_task(_run())
    await asyncio.sleep(0)
    return {"ok": True, "job_id": job_id, "session_id": sid}


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
    return True


def reset() -> None:
    """Drop in-memory jobs (tests). Running tasks are cancelled."""
    for row in list(_jobs.values()):
        task = row.get("task")
        if task is not None and not task.done():
            task.cancel()
    _jobs.clear()
