"""Memory provenance snapshots. Not part of Run / RunCheckpoint.

P1.07 labels already-retrieved memory rows beside the run.
Schema v1 on Run stays unchanged. Do not add source_ids to ContextSlice.
Do not import agent, native_tools, memory, memory_mem0, or context_assembler.
"""
from __future__ import annotations

from enum import Enum

from pydantic import Field, model_validator

from ._base import ContractModel


class MemoryKind(str, Enum):
    SESSION = "session"
    KNOWLEDGE = "knowledge"
    ARTIFACT = "artifact"
    LESSON = "lesson"
    EPISODE = "episode"
    DISCOVERY = "discovery"
    WORKING = "working"
    EPISODIC_FACT = "episodic_fact"
    PREFERENCE = "preference"
    SKILL = "skill"
    JOURNAL = "journal"
    BOOK = "book"
    UNKNOWN = "unknown"


class MemoryOrigin(str, Enum):
    CHROMA = "chroma"
    MEM0 = "mem0"
    DISK = "disk"
    OWNER = "owner"
    REFLECTION = "reflection"
    MENTOR = "mentor"
    REJECTION = "rejection"
    KNOWLEDGE_SCAN = "knowledge_scan"
    WEB = "web"
    EXPLICIT = "explicit"
    INFERRED = "inferred"
    UNKNOWN = "unknown"


class MemoryProvenanceRecord(ContractModel):
    """One labeled memory row. skipped ⇒ not ok."""

    source_id: str = ""
    kind: str
    origin: str = MemoryOrigin.UNKNOWN.value
    collection: str = ""
    locator: str = ""
    session_id: str = ""
    slice_name: str = ""
    captured_at: str = ""
    skipped: bool = False
    ok: bool = True
    blockers: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def skipped_implies_not_ok(self) -> MemoryProvenanceRecord:
        if self.skipped and self.ok:
            raise ValueError("skipped memory provenance record cannot be ok")
        return self


class MemoryProvenanceReport(ContractModel):
    """Frozen provenance labels. Optional persist under $DAEDALUS_DATA/memory/."""

    run_id: str = ""
    session_id: str = ""
    items: list[MemoryProvenanceRecord] = Field(default_factory=list)
    labeled: int = 0
    skipped_count: int = 0
    ok: bool
    skipped: bool = False
    blockers: list[str] = Field(default_factory=list)
    persisted: str = ""

    @model_validator(mode="after")
    def skipped_implies_not_ok(self) -> MemoryProvenanceReport:
        if self.skipped and self.ok:
            raise ValueError("skipped memory provenance report cannot be ok")
        return self


__all__ = [
    "MemoryKind",
    "MemoryOrigin",
    "MemoryProvenanceRecord",
    "MemoryProvenanceReport",
]
