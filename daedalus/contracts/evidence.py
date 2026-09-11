"""DoD and capability evidence records. Not a replacement for JobContract."""
from __future__ import annotations

from pydantic import Field, model_validator

from ._base import ContractModel


class DodVerdict(ContractModel):
    """Mirrors job_contract.JobContract.as_dict() — a score, not a job."""

    coding_task: bool
    complete: bool
    checks: dict[str, bool] = Field(default_factory=dict)
    blockers: list[str] = Field(default_factory=list)
    summary: str = ""


class StageRow(ContractModel):
    stage: str
    ok: bool
    skipped: bool = False
    blockers: list[str] = Field(default_factory=list)
    error: str = ""

    @model_validator(mode="after")
    def skipped_implies_not_ok(self) -> StageRow:
        if self.skipped and self.ok:
            raise ValueError("skipped stage cannot be ok")
        return self


class CapabilityEvidenceRecord(ContractModel):
    capability: str
    status: str
    project_id: str
    revision: str
    artifact_hash: str
    details: str = ""
    runner_version: str = ""
    checks: dict[str, bool] = Field(default_factory=dict)
