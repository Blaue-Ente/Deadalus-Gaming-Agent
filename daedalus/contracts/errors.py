"""Typed error envelopes. Not raised exceptions and not DaedalusBadRequest."""
from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import Field

from ._base import ContractModel


class ErrorClass(str, Enum):
    BAD_REQUEST = "bad_request"
    TOOL_FAILED = "tool_failed"
    TOOL_BLOCKED = "tool_blocked"
    PERMISSION_DENIED = "permission_denied"
    OFFLINE = "offline"
    UNKNOWN_TOOL = "unknown_tool"
    BUDGET_EXHAUSTED = "budget_exhausted"
    PLATFORM_UNSUPPORTED = "platform_unsupported"


class ContractError(ContractModel):
    error_class: ErrorClass
    message: str
    tool: str = ""
    perm_class: str = ""
    details: dict[str, Any] = Field(default_factory=dict)
