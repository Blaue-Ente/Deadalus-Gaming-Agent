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
    RunStage,
    RunStatus,
    Slice,
    SliceRole,
    SliceStatus,
)
from .commands import (
    CommandRequest,
    CommandResult,
    EnvPolicy,
    ExecutionBackend,
    FilesystemPolicy,
    NetworkPolicy,
)
from .tools import (
    TOOL_SPEC_OVERLAY,
    ArtifactRef,
    Observation,
    PermissionRequirement,
    SideEffectClass,
    ToolError,
    ToolLogRow,
    ToolObservation,
    ToolRequest,
    ToolResult,
    ToolRisk,
    ToolSource,
    ToolSpec,
    overlay_for,
)
from .workspace import DirtyBaseline, RunWorkspace
from .baseline import (
    BaselineCheck,
    BaselineReport,
    DependencySnapshot,
    EngineProbe,
    KnownFailure,
)
from .engine import (
    EngineCapability,
    EngineDiscover,
    EngineKind,
    EnginePreflight,
    EngineRunResult,
)
from .context import (
    ContextBudgetReport,
    ContextSlice,
    ContextSliceKind,
    ContextWindow,
)
from .memory import (
    MemoryKind,
    MemoryOrigin,
    MemoryProvenanceRecord,
    MemoryProvenanceReport,
)
from .otel import (
    OtelMetricRecord,
    OtelMode,
    OtelReport,
    OtelSpanRecord,
)

__all__ = [
    "CONTRACT_SCHEMA_VERSION",
    "ArtifactRef",
    "BaselineCheck",
    "BaselineReport",
    "BuildArtifactRef",
    "CommandRequest",
    "CommandResult",
    "ContextBudgetReport",
    "ContextSlice",
    "ContextSliceKind",
    "ContextWindow",
    "EnvPolicy",
    "ExecutionBackend",
    "FilesystemPolicy",
    "NetworkPolicy",
    "BuildVerdict",
    "CapabilityEvidenceRecord",
    "ContractError",
    "ContractModel",
    "DependencySnapshot",
    "DirtyBaseline",
    "DodVerdict",
    "EffortTier",
    "EngineCapability",
    "EngineDiscover",
    "EngineKind",
    "EnginePreflight",
    "EngineProbe",
    "EngineRunResult",
    "ErrorClass",
    "EventKind",
    "KNOWN_PERMISSION_IDS",
    "KnownFailure",
    "MemoryKind",
    "MemoryOrigin",
    "MemoryProvenanceRecord",
    "MemoryProvenanceReport",
    "NamedArtifact",
    "Observation",
    "OtelMetricRecord",
    "OtelMode",
    "OtelReport",
    "OtelSpanRecord",
    "PERMISSION_CATALOG",
    "PermissionClass",
    "PermissionDecision",
    "PermissionGrant",
    "PermissionGroup",
    "PermissionRequirement",
    "Plan",
    "PlanScore",
    "PlanStep",
    "PlanStepStatus",
    "ReleaseManifest",
    "Run",
    "RunBudgetSpend",
    "RunCheckpoint",
    "RunEvent",
    "RunStage",
    "RunStatus",
    "RunWorkspace",
    "Slice",
    "SliceRole",
    "SliceStatus",
    "SideEffectClass",
    "StageRow",
    "TaskAction",
    "TaskEvent",
    "TOOL_SPEC_OVERLAY",
    "ToolError",
    "ToolLogRow",
    "ToolObservation",
    "ToolRequest",
    "ToolResult",
    "ToolRisk",
    "ToolSource",
    "ToolSpec",
    "overlay_for",
]
