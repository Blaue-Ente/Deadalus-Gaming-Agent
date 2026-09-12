"""Independent reviewer snapshots. Not part of Run / RunCheckpoint.

P1.09 records a second-pass sign-off beside the run.
Schema v1 on Run stays unchanged. Do not import agent, native_tools,
mentor, workers, run_finalizer, or verification_controller.
"""
from __future__ import annotations

from enum import Enum

from pydantic import Field, model_validator

from ._base import ContractModel


class ReviewMode(str, Enum):
    OFF = "off"
    CHECKLIST = "checklist"
    LLM = "llm"


class ReviewSubjectKind(str, Enum):
    RUN = "run"
    DIFF = "diff"
    PLAN = "plan"


class ReviewVerdict(str, Enum):
    NOT_RUN = "not_run"
    APPROVED = "approved"
    BLOCKED = "blocked"
    SKIPPED = "skipped"


class ReviewFinding(ContractModel):
    """One review finding. skipped ⇒ not ok."""

    severity: str
    code: str
    detail: str = ""
    skipped: bool = False
    ok: bool = True

    @model_validator(mode="after")
    def skipped_implies_not_ok(self) -> ReviewFinding:
        if self.skipped and self.ok:
            raise ValueError("skipped review finding cannot be ok")
        return self


class ReviewReport(ContractModel):
    """Frozen second-pass sign-off. Optional persist under $DAEDALUS_DATA/review/."""

    run_id: str = ""
    session_id: str = ""
    subject: str = ReviewSubjectKind.RUN.value
    mode: str = ReviewMode.OFF.value
    source: str = "none"
    writer_model: str = ""
    reviewer_model: str = ""
    verdict: str = ReviewVerdict.NOT_RUN.value
    findings: list[ReviewFinding] = Field(default_factory=list)
    ok: bool
    skipped: bool = False
    blockers: list[str] = Field(default_factory=list)
    persisted: str = ""

    @model_validator(mode="after")
    def skipped_implies_not_ok(self) -> ReviewReport:
        if self.skipped and self.ok:
            raise ValueError("skipped independent review cannot be ok")
        if self.verdict == ReviewVerdict.APPROVED.value:
            if self.skipped or self.blockers or not self.ok:
                raise ValueError("approved review cannot be skipped or blocked")
        if self.verdict == ReviewVerdict.SKIPPED.value and not self.skipped:
            raise ValueError("skipped verdict must set skipped")
        return self


__all__ = [
    "ReviewFinding",
    "ReviewMode",
    "ReviewReport",
    "ReviewSubjectKind",
    "ReviewVerdict",
]
