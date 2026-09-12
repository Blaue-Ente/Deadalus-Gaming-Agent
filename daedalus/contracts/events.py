"""Run and task event envelopes mirroring run_engine JSONL and task_events."""
from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import Field

from ._base import ContractModel


class EventKind(str, Enum):
    PHASE = "phase"
    LOG = "log"
    TOKEN = "token"
    STATUS = "status"
    DONE = "done"
    ERROR = "error"
    PING = "_ping"
    FILE = "file"
    INTERRUPTED = "interrupted"
    TOOL = "tool"
    TASK = "task"
    CHECKPOINT = "checkpoint"
    STAGE = "stage"


class TaskAction(str, Enum):
    OPEN = "open"
    DONE = "done"
    REWORK = "rework"
    TOUCH = "touch"
    MODE = "mode"


class RunEvent(ContractModel):
    ts: float
    event: EventKind | str
    data: dict[str, Any] = Field(default_factory=dict)
    run_id: str = ""
    seq: int = 0
    correlation_id: str = ""


class TaskEvent(ContractModel):
    action: TaskAction
    ok: bool
    task: dict[str, Any] | None = None
    error: str = ""
    mode: str = ""
