"""Engine host snapshots. Not part of Run / RunCheckpoint.

P1.03 records Godot/Unity discover and preflight beside the run.
Godot live run wraps ``godot_first.runtime_verify``. Unity live stays
deferred. Schema v1 on Run stays unchanged. Do not import agent,
native_tools, run_engine, or engine_session.
"""
from __future__ import annotations

from enum import Enum

from pydantic import Field, model_validator

from ._base import ContractModel


class EngineKind(str, Enum):
    GODOT = "godot"
    UNITY = "unity"


class EngineCapability(ContractModel):
    """One named host capability. Missing runner is unavailable, not a skip-pass."""

    name: str
    available: bool
    reason: str = ""


class EngineDiscover(ContractModel):
    """Observational presence of one engine on this machine / project root."""

    kind: str
    present: bool
    project: bool
    version: str = ""
    pinned: str = ""
    mcp: bool = False
    promise: str = ""
    skipped: bool = False
    ok: bool = False
    error: str = ""
    capabilities: list[EngineCapability] = Field(default_factory=list)

    @model_validator(mode="after")
    def skipped_implies_not_ok(self) -> EngineDiscover:
        if self.skipped and self.ok:
            raise ValueError("skipped engine discover cannot be ok")
        return self


class EnginePreflight(ContractModel):
    """Observational preflight. Missing binary is skip ≠ ok."""

    kind: str
    ok: bool
    skipped: bool = False
    blockers: list[str] = Field(default_factory=list)
    error: str = ""
    binary: str = ""
    version: str = ""
    pinned: str = ""
    export_templates: str = ""
    mcp_server: str = ""

    @model_validator(mode="after")
    def skipped_implies_not_ok(self) -> EnginePreflight:
        if self.skipped and self.ok:
            raise ValueError("skipped engine preflight cannot be ok")
        return self


class EngineRunResult(ContractModel):
    """Gated run row. Godot live=True wraps runtime_verify. play is never Editor Play."""

    kind: str
    ok: bool
    skipped: bool = False
    ran: bool = False
    play: bool = False
    live: bool = False
    via: str = "none"
    blockers: list[str] = Field(default_factory=list)
    error: str = ""

    @model_validator(mode="after")
    def skipped_implies_not_ok(self) -> EngineRunResult:
        if self.skipped and self.ok:
            raise ValueError("skipped engine run cannot be ok")
        return self


__all__ = [
    "EngineCapability",
    "EngineDiscover",
    "EngineKind",
    "EnginePreflight",
    "EngineRunResult",
]
