"""Typed RunStage table — pipeline position, not the agent loop.

Lookup-table transitions plus optional dual-path mappers from current
stage strings and phase events. Do not import agent, native_tools,
job_ledger, run_engine, build_loop, or full_autopilot. Do not fold
events into RunState. RunCheckpoint.stage stays an int (plan index).
"""
from __future__ import annotations

from collections.abc import Sequence

from daedalus.contracts import EventKind, RunEvent, RunStage, RunStatus

SPINE: tuple[RunStage, ...] = (
    RunStage.INTAKE,
    RunStage.DISCOVER,
    RunStage.PLAN,
    RunStage.IMPLEMENT,
    RunStage.STATIC_VERIFY,
    RunStage.BUILD,
    RunStage.ENGINE_TEST,
    RunStage.PLAYTEST,
    RunStage.VISUAL_VERIFY,
    RunStage.REVIEW,
    RunStage.ACCEPTED,
)

WORK_STAGES: frozenset[RunStage] = frozenset({
    RunStage.INTAKE,
    RunStage.DISCOVER,
    RunStage.PLAN,
    RunStage.IMPLEMENT,
    RunStage.STATIC_VERIFY,
    RunStage.BUILD,
    RunStage.ENGINE_TEST,
    RunStage.PLAYTEST,
    RunStage.VISUAL_VERIFY,
    RunStage.REVIEW,
    RunStage.REPAIR,
})

TERMINAL_STAGES: frozenset[RunStage] = frozenset({
    RunStage.ACCEPTED,
    RunStage.CANCELLED,
})

OPTIONAL_SKIP_STAGES: frozenset[RunStage] = frozenset({
    RunStage.ENGINE_TEST,
    RunStage.PLAYTEST,
    RunStage.VISUAL_VERIFY,
})

_EXITS: frozenset[RunStage] = frozenset({
    RunStage.REPAIR,
    RunStage.BLOCKED,
    RunStage.FAILED,
    RunStage.CANCELLED,
})

_SPINE_NEXT: dict[RunStage, RunStage] = {
    SPINE[i]: SPINE[i + 1] for i in range(len(SPINE) - 1)
}

TRANSITIONS: dict[RunStage, frozenset[RunStage]] = {
    **{stage: frozenset({_SPINE_NEXT[stage]}) | _EXITS for stage in SPINE[:-1]},
    RunStage.REPAIR: frozenset({
        RunStage.IMPLEMENT,
        RunStage.STATIC_VERIFY,
        RunStage.BUILD,
        RunStage.ENGINE_TEST,
        RunStage.PLAYTEST,
        RunStage.VISUAL_VERIFY,
        RunStage.REVIEW,
        RunStage.BLOCKED,
        RunStage.FAILED,
        RunStage.CANCELLED,
    }),
    RunStage.BLOCKED: frozenset({
        RunStage.REPAIR,
        RunStage.IMPLEMENT,
        RunStage.FAILED,
        RunStage.CANCELLED,
    }),
    RunStage.FAILED: frozenset({
        RunStage.IMPLEMENT,
        RunStage.INTAKE,
        RunStage.CANCELLED,
    }),
    RunStage.ACCEPTED: frozenset(),
    RunStage.CANCELLED: frozenset(),
}

_LEGACY: dict[str, RunStage] = {
    "idea": RunStage.INTAKE,
    "intake": RunStage.INTAKE,
    "queued": RunStage.INTAKE,
    "preflight": RunStage.DISCOVER,
    "contract": RunStage.DISCOVER,
    "context": RunStage.DISCOVER,
    "recon": RunStage.DISCOVER,
    "explorer": RunStage.DISCOVER,
    "discover": RunStage.DISCOVER,
    "plan": RunStage.PLAN,
    "code": RunStage.IMPLEMENT,
    "slice": RunStage.IMPLEMENT,
    "coding": RunStage.IMPLEMENT,
    "implement": RunStage.IMPLEMENT,
    "static_verify": RunStage.STATIC_VERIFY,
    "game_lint": RunStage.STATIC_VERIFY,
    "build": RunStage.BUILD,
    "test": RunStage.ENGINE_TEST,
    "runtime": RunStage.ENGINE_TEST,
    "engine": RunStage.ENGINE_TEST,
    "engine_test": RunStage.ENGINE_TEST,
    "gameplay": RunStage.PLAYTEST,
    "playtest": RunStage.PLAYTEST,
    "visual": RunStage.VISUAL_VERIFY,
    "preview": RunStage.VISUAL_VERIFY,
    "visual_verify": RunStage.VISUAL_VERIFY,
    "review": RunStage.REVIEW,
    "repair": RunStage.REPAIR,
    "export": RunStage.ACCEPTED,
    "publish": RunStage.ACCEPTED,
    "done": RunStage.ACCEPTED,
    "accepted": RunStage.ACCEPTED,
    "blocked": RunStage.BLOCKED,
    "failed": RunStage.FAILED,
    "cleared": RunStage.CANCELLED,
    "interrupted": RunStage.CANCELLED,
    "cancelled": RunStage.CANCELLED,
}

_PHASE_N: dict[int, RunStage] = {
    0: RunStage.INTAKE,
    1: RunStage.PLAN,
    2: RunStage.BUILD,
    3: RunStage.ENGINE_TEST,
    4: RunStage.VISUAL_VERIFY,
    5: RunStage.REVIEW,
}


class StageMachineError(Exception):
    """Illegal transition or unknown stage."""


def apply_stage(current: RunStage, nxt: RunStage | str) -> RunStage:
    new = _as_run_stage(nxt)
    if new is current:
        return current
    allowed = TRANSITIONS.get(current, frozenset())
    if new not in allowed:
        raise StageMachineError(f"illegal transition: {current.value} → {new.value}")
    return new


def skip_stage(current: RunStage, nxt: RunStage | str) -> RunStage:
    """Record an optional hop to the next spine neighbor only."""
    new = _as_run_stage(nxt)
    if new is current:
        return current
    if _SPINE_NEXT.get(current) is not new:
        raise StageMachineError(f"illegal skip: {current.value} → {new.value}")
    if new not in OPTIONAL_SKIP_STAGES and current not in OPTIONAL_SKIP_STAGES:
        raise StageMachineError(f"illegal skip: {current.value} → {new.value}")
    return new


def from_legacy_stage(raw: str | int) -> RunStage | None:
    """Map current stage strings. Checkpoint ints / digit-only tokens are not stages."""
    if isinstance(raw, bool) or isinstance(raw, int):
        return None
    token = str(raw or "").strip().lower()
    if not token or token.isdigit():
        return None
    return _LEGACY.get(token)


def from_phase_event(
    n: int | None = None,
    stage: str = "",
    label: str = "",
) -> RunStage | None:
    """Map full_autopilot / orchestrator phase payloads. n=4 uses label."""
    if stage:
        mapped = from_legacy_stage(stage)
        if mapped is not None:
            return mapped
    if n is not None:
        if n == 4:
            lab = (label or "").lower()
            if "playtest" in lab or "gameplay" in lab:
                return RunStage.PLAYTEST
            if "engine" in lab:
                return RunStage.ENGINE_TEST
            return RunStage.VISUAL_VERIFY
        return _PHASE_N.get(n)
    if label:
        return from_legacy_stage(label)
    return None


def status_for_stage(stage: RunStage) -> RunStatus:
    """Pure dual-path hint. Does not call apply_status or touch RunState."""
    if stage is RunStage.ACCEPTED:
        return RunStatus.DONE
    if stage is RunStage.BLOCKED:
        return RunStatus.BLOCKED
    if stage is RunStage.FAILED:
        return RunStatus.FAILED
    if stage is RunStage.CANCELLED:
        return RunStatus.INTERRUPTED
    return RunStatus.IN_PROGRESS


def peek_stage(events: Sequence[RunEvent]) -> RunStage | None:
    """Last phase event only. Ignores plan-index ``stage`` / ``{i}`` rows."""
    last: RunEvent | None = None
    for ev in events:
        if _event_name(ev.event) != EventKind.PHASE.value:
            continue
        last = ev
    if last is None:
        return None
    data = last.data or {}
    return from_phase_event(
        n=_as_optional_int(data.get("n")),
        stage=str(data.get("stage") or ""),
        label=str(data.get("label") or ""),
    )


def _as_run_stage(stage: RunStage | str) -> RunStage:
    if isinstance(stage, RunStage):
        return stage
    try:
        return RunStage(stage)
    except ValueError as exc:
        raise StageMachineError(f"unknown run stage: {stage!r}") from exc


def _event_name(event: EventKind | str) -> str:
    return event.value if isinstance(event, EventKind) else str(event)


def _as_optional_int(raw: object) -> int | None:
    if raw is None or raw == "":
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None
