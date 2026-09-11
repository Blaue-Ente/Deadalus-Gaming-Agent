"""Serializable kernel plan shapes mirroring task_playbooks and tool_router."""
from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import Field

from ._base import ContractModel


class PlanStepStatus(str, Enum):
    PENDING = "pending"
    DONE = "done"
    FAILED = "failed"


class PlanStep(ContractModel):
    id: str
    tool: str | None = None
    required: bool = False
    auto: bool = False
    args: dict[str, Any] = Field(default_factory=dict)
    docs: tuple[str, ...] = ()
    why: str = ""
    depends_on: tuple[str, ...] = ()
    accept_any: tuple[str, ...] = ()
    status: PlanStepStatus = PlanStepStatus.PENDING
    detail: str = ""


class Plan(ContractModel):
    playbook_id: str
    title: str
    kind: str = "static"
    tools: tuple[str, ...] = ()
    steps: tuple[PlanStep, ...] = ()
    n: int = 0
    dim: int = 3


class PlanScore(ContractModel):
    playbook_id: str
    complete: bool
    missing: list[str] = Field(default_factory=list)
    used: list[str] = Field(default_factory=list)
    blocker: str = ""


class BuildVerdict(ContractModel):
    complete: bool
    blockers: list[str] = Field(default_factory=list)
    missing_steps: list[str] = Field(default_factory=list)
    next_step: str = ""
    failed_calls: list[str] = Field(default_factory=list)
