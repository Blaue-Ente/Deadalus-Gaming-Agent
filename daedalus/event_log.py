"""Typed append-only event log — history beside run_engine, not the agent loop.

Construction, migration, sequence inference, correlation ids, and replay over
plain rows. Persistence stays in run_engine; adapters lazy-import it.
Do not import agent, native_tools, or job_ledger. Do not fold events into RunState.
"""
from __future__ import annotations

import json
import time
import uuid
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from daedalus.contracts import EventKind, RunEvent

SCHEMA_VERSION = 1


class EventLogError(Exception):
    """Unreadable row, unknown schema, or illegal sequence."""


def new_correlation_id() -> str:
    return uuid.uuid4().hex


def mint(
    event: EventKind | str,
    data: Mapping[str, Any] | None = None,
    *,
    run_id: str,
    seq: int,
    correlation_id: str = "",
    ts: float | None = None,
) -> RunEvent:
    if not run_id:
        raise EventLogError("run_id required")
    if seq < 1:
        raise EventLogError("seq must be >= 1")
    return RunEvent(
        ts=time.time() if ts is None else float(ts),
        event=event,
        data=dict(data or {}),
        run_id=run_id,
        seq=seq,
        correlation_id=correlation_id,
    )


def migrate(raw: dict[str, Any]) -> dict[str, Any]:
    """Normalize v0 run_engine rows and v1 envelopes to a canonical RunEvent dict."""
    if not isinstance(raw, dict):
        raise EventLogError("payload must be an object")
    version = _schema_version(raw)
    if version > SCHEMA_VERSION:
        raise EventLogError(f"unsupported schema_version: {version}")
    if "event" not in raw:
        raise EventLogError("event required")
    if raw.get("ts") is None or raw.get("ts") == "":
        raise EventLogError("ts required")
    data = raw.get("data")
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise EventLogError("data must be an object")
    return {
        "schema_version": SCHEMA_VERSION,
        "ts": _as_float(raw.get("ts"), None),
        "event": raw["event"],
        "data": dict(data),
        "run_id": str(raw.get("run_id") or ""),
        "seq": _as_int(raw.get("seq"), 0),
        "correlation_id": str(raw.get("correlation_id") or ""),
    }


def from_row(raw: str | dict[str, Any] | RunEvent) -> RunEvent:
    if isinstance(raw, RunEvent):
        return raw
    if isinstance(raw, str):
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise EventLogError(f"invalid JSON: {exc}") from exc
    elif isinstance(raw, dict):
        payload = raw
    else:
        raise EventLogError("payload must be JSON text or an object")
    return RunEvent.model_validate(migrate(payload))


def to_row(ev: RunEvent) -> dict[str, Any]:
    return ev.model_dump(mode="json")


def to_engine_payload(ev: RunEvent) -> tuple[str, dict[str, Any]]:
    return _event_name(ev.event), dict(ev.data)


def from_sse_pair(
    event: str,
    data: Mapping[str, Any] | None = None,
    *,
    run_id: str = "",
    ts: float | None = None,
) -> RunEvent:
    """Unused adapter: in-memory / SSE `{event, data}` has no ts or seq."""
    return RunEvent(
        ts=time.time() if ts is None else float(ts),
        event=event,
        data=dict(data or {}),
        run_id=run_id,
        seq=0,
        correlation_id="",
    )


def from_engine_row(raw: Mapping[str, Any], *, run_id: str = "") -> RunEvent:
    ev = from_row(dict(raw))
    if run_id and not ev.run_id:
        return ev.model_copy(update={"run_id": run_id})
    return ev


def assign_seq(
    rows: Sequence[str | dict[str, Any] | RunEvent],
    *,
    start: int = 1,
    run_id: str = "",
) -> list[RunEvent]:
    """Fill missing/0 seq from file order. Explicit seq (>= 1) is kept."""
    if start < 1:
        raise EventLogError("start must be >= 1")
    next_seq = start
    out: list[RunEvent] = []
    for row in rows:
        ev = from_row(row)
        if ev.seq <= 0:
            ev = ev.model_copy(update={"seq": next_seq})
            next_seq += 1
        elif ev.seq >= next_seq:
            next_seq = ev.seq + 1
        if run_id and not ev.run_id:
            ev = ev.model_copy(update={"run_id": run_id})
        out.append(ev)
    return out


def replay(
    rows: Iterable[str | dict[str, Any] | RunEvent],
    *,
    require_contiguous: bool = False,
    skip_corrupt: bool = True,
    run_id: str = "",
) -> list[RunEvent]:
    """Typed, ordered history. Does not apply events to RunState."""
    parsed: list[RunEvent] = []
    for row in rows:
        try:
            ev = from_row(row)
        except (EventLogError, json.JSONDecodeError, TypeError, ValueError):
            if skip_corrupt:
                continue
            raise
        if run_id and not ev.run_id:
            ev = ev.model_copy(update={"run_id": run_id})
        parsed.append(ev)

    assigned = assign_seq(parsed, run_id=run_id)
    ordered = sorted(assigned, key=lambda ev: (ev.seq, ev.ts))
    if require_contiguous:
        _assert_contiguous(ordered)
    return ordered


def rows_from_engine(run_id: str, *, limit: int = 400) -> list[RunEvent]:
    from daedalus.run_engine import load_events

    return replay(load_events(run_id, limit=limit), run_id=run_id, require_contiguous=False)


def write_via_engine(run_id: str, ev: RunEvent) -> None:
    """Thin adapter: persist `{event, data}` through run_engine.append_event.

    Seq / correlation / schema are inferred again on read. Empty run_id is refused.
    """
    if not run_id:
        raise EventLogError("run_id required")
    from daedalus.run_engine import append_event

    event, data = to_engine_payload(ev)
    append_event(run_id, event, data)


class EventLog:
    """In-memory clock for one run. Append-only; not a state machine."""

    def __init__(self, run_id: str, *, correlation_id: str = "") -> None:
        if not run_id:
            raise EventLogError("run_id required")
        self._run_id = run_id
        self._correlation_id = correlation_id or new_correlation_id()
        self._next_seq = 1
        self._events: list[RunEvent] = []

    @property
    def run_id(self) -> str:
        return self._run_id

    @property
    def correlation_id(self) -> str:
        return self._correlation_id

    def append(
        self,
        event: EventKind | str,
        data: Mapping[str, Any] | None = None,
        *,
        ts: float | None = None,
    ) -> RunEvent:
        ev = mint(
            event,
            data,
            run_id=self._run_id,
            seq=self._next_seq,
            correlation_id=self._correlation_id,
            ts=ts,
        )
        self._events.append(ev)
        self._next_seq += 1
        return ev

    def events(self) -> tuple[RunEvent, ...]:
        return tuple(self._events)


def _assert_contiguous(events: Sequence[RunEvent]) -> None:
    seqs = [ev.seq for ev in events]
    if any(seq < 1 for seq in seqs):
        raise EventLogError("seq must be >= 1")
    if len(seqs) != len(set(seqs)):
        raise EventLogError("duplicate seq")
    if seqs and seqs != list(range(1, len(seqs) + 1)):
        raise EventLogError("seq gap")


def _event_name(event: EventKind | str) -> str:
    return event.value if isinstance(event, EventKind) else str(event)


def _schema_version(raw: Mapping[str, Any]) -> int:
    raw_ver = raw.get("schema_version", 0)
    if raw_ver is None or raw_ver == "":
        return 0
    try:
        return int(raw_ver)
    except (TypeError, ValueError) as exc:
        raise EventLogError(f"invalid schema_version: {raw_ver!r}") from exc


def _as_int(raw: Any, default: int) -> int:
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def _as_float(raw: Any, default: float | None) -> float:
    if raw is None or raw == "":
        if default is None:
            raise EventLogError("ts required")
        return default
    try:
        return float(raw)
    except (TypeError, ValueError) as exc:
        raise EventLogError(f"invalid ts: {raw!r}") from exc
