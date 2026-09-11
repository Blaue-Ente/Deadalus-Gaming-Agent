"""Tool protocol envelopes mirroring tool_protocol and native tool_log rows.

P0.03 adds spec / risk / side-effect metadata and optional result fields.
Do not import agent or native_tools.
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import Field, model_validator

from ._base import ContractModel
from .errors import ContractError
from .permissions import KNOWN_PERMISSION_IDS


class ToolSource(str, Enum):
    NATIVE = "native"
    MARKER = "marker"
    DUMP = "dump"


class SideEffectClass(str, Enum):
    READ = "read"
    WRITE = "write"
    SHELL = "shell"
    NETWORK = "network"
    DATABASE = "database"
    SPAWN = "spawn"
    ENGINE = "engine"
    MEMORY = "memory"
    MCP = "mcp"


class ToolRisk(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class PermissionRequirement(ContractModel):
    """Per-tool permission gate. Empty ToolSpec.permission means none."""

    perm_class: str
    fail_open: bool = True
    required: bool = True

    @model_validator(mode="after")
    def perm_class_must_be_known(self) -> PermissionRequirement:
        if self.perm_class not in KNOWN_PERMISSION_IDS:
            raise ValueError(f"unknown perm_class: {self.perm_class}")
        return self


class ArtifactRef(ContractModel):
    """Tool-touched path/slug. Not BuildArtifactRef (no artifact_hash required)."""

    kind: Literal["file", "image", "proof", "url", "other"] = "file"
    path: str = ""
    slug: str = ""
    uri: str = ""


class ToolError(ContractError):
    """Tool-scoped envelope. Reuses ErrorClass — no second taxonomy."""

    retryable: bool = False


class ToolRequest(ContractModel):
    name: str
    args: dict[str, Any] = Field(default_factory=dict)
    source: ToolSource = ToolSource.NATIVE
    marker: str = ""


class ToolResult(ContractModel):
    name: str
    ok: bool
    text: str
    data: dict[str, Any] = Field(default_factory=dict)
    source: ToolSource = ToolSource.NATIVE
    blocked: bool = False
    error: ToolError | None = None
    artifacts: tuple[ArtifactRef, ...] = ()
    risk: ToolRisk | None = None


class ToolLogRow(ContractModel):
    """native_tools tool_log row. blocked=True may pair with ok=True (P0.01)."""

    tool: str
    input: dict[str, Any] = Field(default_factory=dict)
    ok: bool
    blocked: bool = False


class ToolObservation(ContractModel):
    tool: str
    ok: bool
    blocked: bool
    args: dict[str, Any] = Field(default_factory=dict)
    head: str = ""
    error: ToolError | None = None


Observation = ToolObservation


class ToolSpec(ContractModel):
    name: str
    description: str = ""
    input_schema: dict[str, Any] = Field(default_factory=dict)
    side_effect: SideEffectClass = SideEffectClass.READ
    risk: ToolRisk = ToolRisk.LOW
    permission: PermissionRequirement | None = None
    aliases: tuple[str, ...] = ()
    need_marker: str = ""
    tags: tuple[str, ...] = ()
    module: str = ""
    when: str = ""
    network: bool = False


# name -> (side_effect, risk, perm_class). perm_class="" means no gate.
# Keys must stay ⊆ NATIVE_TOOL_SPECS / architecture-inventory native_tools.
TOOL_SPEC_OVERLAY: dict[str, tuple[SideEffectClass, ToolRisk, str]] = {
    "read_file": (SideEffectClass.READ, ToolRisk.LOW, ""),
    "list_dir": (SideEffectClass.READ, ToolRisk.LOW, ""),
    "write_file": (SideEffectClass.WRITE, ToolRisk.HIGH, ""),
    "edit_file": (SideEffectClass.WRITE, ToolRisk.HIGH, ""),
    "playground_new": (SideEffectClass.WRITE, ToolRisk.MEDIUM, ""),
    "run_command": (SideEffectClass.SHELL, ToolRisk.HIGH, ""),
    "web_search": (SideEffectClass.NETWORK, ToolRisk.MEDIUM, "web_search"),
    "web_fetch": (SideEffectClass.NETWORK, ToolRisk.MEDIUM, "web_search"),
    "generate_image": (SideEffectClass.WRITE, ToolRisk.HIGH, ""),
    "remember": (SideEffectClass.MEMORY, ToolRisk.LOW, ""),
    "code_search": (SideEffectClass.READ, ToolRisk.LOW, ""),
    "postgres_query": (SideEffectClass.DATABASE, ToolRisk.HIGH, "postgres_query"),
    "list_endpoints": (SideEffectClass.READ, ToolRisk.LOW, ""),
    "list_modules": (SideEffectClass.READ, ToolRisk.LOW, ""),
    "web_read": (SideEffectClass.NETWORK, ToolRisk.MEDIUM, "web_search"),
    "reach_status": (SideEffectClass.READ, ToolRisk.LOW, ""),
    "browser": (SideEffectClass.ENGINE, ToolRisk.HIGH, ""),
    "mcp_call": (SideEffectClass.MCP, ToolRisk.HIGH, ""),
    "spawn_subagent": (SideEffectClass.SPAWN, ToolRisk.HIGH, ""),
    "playtest": (SideEffectClass.ENGINE, ToolRisk.MEDIUM, ""),
    "mirofish": (SideEffectClass.NETWORK, ToolRisk.MEDIUM, ""),
    "play_lab": (SideEffectClass.ENGINE, ToolRisk.HIGH, ""),
    "git_forge": (SideEffectClass.NETWORK, ToolRisk.HIGH, "github_access"),
    "dissect_game": (SideEffectClass.READ, ToolRisk.MEDIUM, ""),
    "engine": (SideEffectClass.ENGINE, ToolRisk.HIGH, ""),
    "world": (SideEffectClass.WRITE, ToolRisk.HIGH, ""),
    "assets": (SideEffectClass.WRITE, ToolRisk.MEDIUM, ""),
    "skill": (SideEffectClass.MEMORY, ToolRisk.LOW, ""),
    "production": (SideEffectClass.ENGINE, ToolRisk.HIGH, ""),
    "math3d": (SideEffectClass.READ, ToolRisk.LOW, ""),
    "spatial": (SideEffectClass.READ, ToolRisk.LOW, ""),
    "game_lint": (SideEffectClass.READ, ToolRisk.LOW, ""),
    "profiler": (SideEffectClass.READ, ToolRisk.LOW, ""),
    "game_canon": (SideEffectClass.READ, ToolRisk.LOW, ""),
}


def overlay_for(name: str) -> tuple[SideEffectClass, ToolRisk, str]:
    """Lookup overlay; unknown native names default to read / low / no perm."""
    return TOOL_SPEC_OVERLAY.get(name, (SideEffectClass.READ, ToolRisk.LOW, ""))


__all__ = [
    "TOOL_SPEC_OVERLAY",
    "ArtifactRef",
    "Observation",
    "PermissionRequirement",
    "SideEffectClass",
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
