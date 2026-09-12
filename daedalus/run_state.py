"""Typed RunState — versioned snapshot of a run, not the agent loop.

Construction, JSON serialization, ledger/checkpoint adapters, and
deterministic status transitions. Do not import agent, native_tools,
job_ledger, or run_engine. Persistence stays in those modules.
"""
from __future__ import annotations

import json
import time
from collections.abc import Mapping, Sequence
from typing import Any, Literal

from pydantic import Field

from daedalus.contracts import (
    ContractModel,
    NamedArtifact,
    Run,
    RunCheckpoint,
    RunStatus,
    Slice,
    SliceStatus,
)

SCHEMA_VERSION = 1

TRANSITIONS: dict[RunStatus, frozenset[RunStatus]] = {
    RunStatus.QUEUED: frozenset({RunStatus.IN_PROGRESS, RunStatus.CLEARED}),
    RunStatus.IN_PROGRESS: frozenset({
        RunStatus.BLOCKED,
        RunStatus.DONE,
        RunStatus.FAILED,
        RunStatus.INTERRUPTED,
        RunStatus.CLEARED,
    }),
    RunStatus.BLOCKED: frozenset({
        RunStatus.IN_PROGRESS,
        RunStatus.FAILED,
        RunStatus.CLEARED,
    }),
    RunStatus.INTERRUPTED: frozenset({
        RunStatus.IN_PROGRESS,
        RunStatus.FAILED,
        RunStatus.CLEARED,
    }),
    RunStatus.FAILED: frozenset({RunStatus.IN_PROGRESS, RunStatus.CLEARED}),
    RunStatus.DONE: frozenset({RunStatus.CLEARED}),
    RunStatus.CLEARED: frozenset(),
}


class RunStateError(Exception):
    """Illegal transition, unknown schema, or unreadable payload."""


class ProgressNote(ContractModel):
    ts: float = 0.0
    note: str = ""


class RunState(ContractModel):
    schema_version: Literal[1] = 1
    run: Run
    artifacts: dict[str, NamedArtifact] = Field(default_factory=dict)
    progress: tuple[ProgressNote, ...] = ()
    checkpoint: RunCheckpoint | None = None


def from_run(run: Run) -> RunState:
    return RunState(run=run)


def dumps(state: RunState) -> str:
    return json.dumps(state.model_dump(mode="json"), ensure_ascii=False)


def loads(raw: str | dict[str, Any]) -> RunState:
    if isinstance(raw, str):
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RunStateError(f"invalid JSON: {exc}") from exc
    elif isinstance(raw, dict):
        payload = raw
    else:
        raise RunStateError("payload must be JSON text or an object")
    return RunState.model_validate(migrate(payload))


def migrate(raw: dict[str, Any]) -> dict[str, Any]:
    """Normalize v0 ledger/Run dumps and v1 payloads to a canonical RunState dict."""
    if not isinstance(raw, dict):
        raise RunStateError("payload must be an object")
    version = _schema_version(raw)
    if version > SCHEMA_VERSION:
        raise RunStateError(f"unsupported schema_version: {version}")

    if "run" in raw and isinstance(raw["run"], dict):
        if version == SCHEMA_VERSION:
            return raw
        return _canonical(
            run=_run_payload_from_flat(raw["run"]),
            artifacts=_artifacts_from_raw(raw),
            progress=_progress_from_raw(raw),
            checkpoint=_checkpoint_payload(raw.get("checkpoint")),
        )

    if _looks_like_run_or_job(raw):
        return _canonical(
            run=_run_payload_from_flat(raw),
            artifacts=_artifacts_from_raw(raw),
            progress=_progress_from_raw(raw),
            checkpoint=_checkpoint_payload(raw.get("checkpoint")),
        )

    raise RunStateError("unrecognized RunState payload")


def from_job(job: dict[str, Any]) -> RunState:
    if not isinstance(job, dict):
        raise RunStateError("job must be an object")
    return RunState.model_validate(migrate(job))


def to_job_dict(state: RunState) -> dict[str, Any]:
    run = state.run
    tasks = [
        {
            "id": sl.id,
            "title": sl.title,
            "detail": sl.detail,
            "role": sl.role.value if hasattr(sl.role, "value") else sl.role,
            "status": sl.status.value if hasattr(sl.status, "value") else sl.status,
            "verify": sl.verify,
        }
        for sl in run.slices
    ]
    artifacts: dict[str, dict[str, Any]] = {}
    for key, art in state.artifacts.items():
        artifacts[key] = {
            "id": art.id,
            "content": art.content,
            "source": art.source,
            "updated_at": art.updated_at,
        }
    progress = [{"ts": row.ts, "note": row.note} for row in state.progress]
    return {
        "schema_version": SCHEMA_VERSION,
        "job_id": run.job_id or run.run_id,
        "slug": run.slug,
        "session_id": run.session_id,
        "original_ask": run.original_ask,
        "acceptance": list(run.acceptance),
        "tasks": tasks,
        "artifacts": artifacts,
        "progress": progress,
        "created_at": run.created_at,
        "updated_at": run.updated_at,
        "status": run.status.value if hasattr(run.status, "value") else run.status,
    }


def from_checkpoint(ckpt: dict[str, Any]) -> RunCheckpoint:
    if not isinstance(ckpt, dict):
        raise RunStateError("checkpoint must be an object")
    run_id = str(ckpt.get("run_id") or "")
    if not run_id:
        raise RunStateError("run_id required")
    plan = ckpt.get("plan")
    if not isinstance(plan, dict):
        plan = {}
    return RunCheckpoint(
        run_id=run_id,
        stage=_as_int(ckpt.get("stage"), 0),
        plan=plan,
        status=str(ckpt.get("status") or ""),
        slug=str(ckpt.get("slug") or ""),
        updated=_as_float(ckpt.get("updated"), 0.0),
        error=str(ckpt.get("error") or ""),
    )


def with_checkpoint(state: RunState, ckpt: RunCheckpoint | dict[str, Any]) -> RunState:
    parsed = ckpt if isinstance(ckpt, RunCheckpoint) else from_checkpoint(ckpt)
    return state.model_copy(update={"checkpoint": parsed})


def apply_status(
    state: RunState,
    status: RunStatus | str,
    *,
    reason: str = "",
) -> RunState:
    new = _as_run_status(status)
    old = state.run.status
    if new is old:
        return state
    allowed = TRANSITIONS.get(old, frozenset())
    if new not in allowed:
        raise RunStateError(f"illegal transition: {old.value} → {new.value}")
    updates: dict[str, Any] = {"status": new}
    if reason:
        updates["stop_reason"] = reason
    return state.model_copy(update={"run": state.run.model_copy(update=updates)})


def apply_slice(
    state: RunState,
    slice_id: str,
    status: SliceStatus | str,
    *,
    note: str = "",
) -> RunState:
    new_slice_status = _as_slice_status(status)
    found = False
    slices: list[Slice] = []
    for sl in state.run.slices:
        if sl.id == slice_id:
            slices.append(sl.model_copy(update={"status": new_slice_status}))
            found = True
        else:
            slices.append(sl)
    if not found:
        raise RunStateError(f"slice not found: {slice_id}")
    progress = state.progress
    if note and note.strip():
        progress = state.progress + (ProgressNote(ts=time.time(), note=note.strip()),)
    new_run = state.run.model_copy(
        update={"slices": slices, "status": derive_status(slices)},
    )
    return state.model_copy(update={"run": new_run, "progress": progress})


def derive_status(slices: Sequence[Slice | Mapping[str, Any]]) -> RunStatus:
    """Mirror job_ledger.mark_task: open → in_progress; else failed → blocked; else done."""
    open_any = False
    failed_any = False
    for item in slices or ():
        value = _slice_status_value(item)
        if value == SliceStatus.OPEN.value:
            open_any = True
        elif value == SliceStatus.FAILED.value:
            failed_any = True
    if open_any:
        return RunStatus.IN_PROGRESS
    if failed_any:
        return RunStatus.BLOCKED
    return RunStatus.DONE


def _schema_version(raw: Mapping[str, Any]) -> int:
    raw_ver = raw.get("schema_version", 0)
    if raw_ver is None or raw_ver == "":
        return 0
    try:
        return int(raw_ver)
    except (TypeError, ValueError) as exc:
        raise RunStateError(f"invalid schema_version: {raw_ver!r}") from exc


def _looks_like_run_or_job(raw: Mapping[str, Any]) -> bool:
    if raw.get("run_id") or raw.get("job_id"):
        return True
    return any(key in raw for key in ("tasks", "slices", "original_ask", "acceptance"))


def _run_payload_from_flat(raw: Mapping[str, Any]) -> dict[str, Any]:
    job_id = str(raw.get("job_id") or "")
    run_id = str(raw.get("run_id") or job_id or "")
    if not run_id:
        raise RunStateError("run_id required")
    slices = raw.get("slices")
    if slices is None:
        slices = raw.get("tasks") or []
    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "job_id": job_id or run_id,
        "slug": raw.get("slug") or "",
        "session_id": raw.get("session_id") or "",
        "original_ask": raw.get("original_ask") or "",
        "acceptance": list(raw.get("acceptance") or []),
        "slices": list(slices or []),
        "status": raw.get("status") or RunStatus.IN_PROGRESS.value,
        "created_at": _as_float(raw.get("created_at"), 0.0),
        "updated_at": _as_float(raw.get("updated_at"), 0.0),
    }
    for opt in ("effort", "model", "complete", "stop_reason", "spend"):
        if opt in raw:
            payload[opt] = raw[opt]
    return payload


def _artifacts_from_raw(raw: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    arts = raw.get("artifacts") or {}
    if not isinstance(arts, dict):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for key, val in arts.items():
        if not isinstance(val, dict):
            continue
        aid = str(val.get("id") or key)
        out[aid] = {
            "id": aid,
            "content": val.get("content") or "",
            "source": val.get("source") or "",
            "updated_at": _as_float(val.get("updated_at"), 0.0),
        }
    return out


def _progress_from_raw(raw: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows = raw.get("progress") or []
    if not isinstance(rows, list):
        return []
    out: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        out.append({"ts": _as_float(row.get("ts"), 0.0), "note": str(row.get("note") or "")})
    return out


def _checkpoint_payload(raw: Any) -> dict[str, Any] | None:
    if raw is None:
        return None
    if isinstance(raw, RunCheckpoint):
        return raw.model_dump(mode="json")
    if not isinstance(raw, dict):
        raise RunStateError("checkpoint must be an object")
    return from_checkpoint(raw).model_dump(mode="json")


def _canonical(
    *,
    run: dict[str, Any],
    artifacts: dict[str, dict[str, Any]],
    progress: list[dict[str, Any]],
    checkpoint: dict[str, Any] | None,
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "run": run,
        "artifacts": artifacts,
        "progress": progress,
        "checkpoint": checkpoint,
    }


def _as_run_status(status: RunStatus | str) -> RunStatus:
    if isinstance(status, RunStatus):
        return status
    try:
        return RunStatus(status)
    except ValueError as exc:
        raise RunStateError(f"unknown run status: {status!r}") from exc


def _as_slice_status(status: SliceStatus | str) -> SliceStatus:
    if isinstance(status, SliceStatus):
        return status
    try:
        return SliceStatus(status)
    except ValueError as exc:
        raise RunStateError(f"unknown slice status: {status!r}") from exc


def _slice_status_value(item: Slice | Mapping[str, Any]) -> str:
    if isinstance(item, Slice):
        st = item.status
        return st.value if isinstance(st, SliceStatus) else str(st)
    return str(item.get("status") or SliceStatus.OPEN.value)


def _as_int(raw: Any, default: int) -> int:
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def _as_float(raw: Any, default: float) -> float:
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default
