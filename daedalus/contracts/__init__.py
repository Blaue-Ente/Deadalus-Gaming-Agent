"""Leaf domain contracts for the future Run Engine / tool protocol.

Construction and serialization only. Do not import agent or native_tools.
"""
from __future__ import annotations

from ._base import CONTRACT_SCHEMA_VERSION, ContractModel
from .artifacts import BuildArtifactRef, NamedArtifact, ReleaseManifest
from .errors import ContractError, ErrorClass
from .events import EventKind, RunEvent, TaskAction, TaskEvent
from .evidence import CapabilityEvidenceRecord, DodVerdict, StageRow
from .permissions import (
    KNOWN_PERMISSION_IDS,
    PERMISSION_CATALOG,
    PermissionClass,
    PermissionDecision,
    PermissionGrant,
    PermissionGroup,
)
from .plan import BuildVerdict, Plan, PlanScore, PlanStep, PlanStepStatus
from .run import (
    EffortTier,
    Run,
    RunBudgetSpend,
    RunCheckpoint,
    RunStatus,
    Slice,
    SliceRole,
    SliceStatus,
)
from .tools import ToolLogRow, ToolObservation, ToolRequest, ToolResult, ToolSource

__all__ = [
    "CONTRACT_SCHEMA_VERSION",
    "BuildArtifactRef",
    "BuildVerdict",
    "CapabilityEvidenceRecord",
    "ContractError",
    "ContractModel",
    "DodVerdict",
    "EffortTier",
    "ErrorClass",
    "EventKind",
    "KNOWN_PERMISSION_IDS",
    "NamedArtifact",
    "PERMISSION_CATALOG",
    "PermissionClass",
    "PermissionDecision",
    "PermissionGrant",
    "PermissionGroup",
    "Plan",
    "PlanScore",
    "PlanStep",
    "PlanStepStatus",
    "ReleaseManifest",
    "Run",
    "RunBudgetSpend",
    "RunCheckpoint",
    "RunEvent",
    "RunStatus",
    "Slice",
    "SliceRole",
    "SliceStatus",
    "StageRow",
    "TaskAction",
    "TaskEvent",
    "ToolLogRow",
    "ToolObservation",
    "ToolRequest",
    "ToolResult",
    "ToolSource",
]
