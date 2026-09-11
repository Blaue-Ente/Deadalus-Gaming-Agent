"""Tool protocol envelopes mirroring tool_protocol and native tool_log rows."""
from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import Field

from ._base import ContractModel


class ToolSource(str, Enum):
    NATIVE = "native"
    MARKER = "marker"
    DUMP = "dump"


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
