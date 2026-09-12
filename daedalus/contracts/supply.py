"""Supply-chain snapshots. Not part of Run / RunCheckpoint.

P1.10 records lock inventory and pin checks beside the run.
Schema v1 on Run stays unchanged. Do not import agent, native_tools,
run_engine, otel, or independent_reviewer.
"""
from __future__ import annotations

from pydantic import Field, model_validator

from ._base import ContractModel


class SupplyPackage(ContractModel):
    """One row from uv.lock. Observational inventory (SBOM-lite)."""

    name: str
    version: str = ""
    source: str = "registry"
    sha256: str = ""


class SupplyCheck(ContractModel):
    """One named supply-chain slice. skipped ⇒ not ok."""

    id: str
    ok: bool
    skipped: bool = False
    detail: str = ""
    blockers: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def skipped_implies_not_ok(self) -> SupplyCheck:
        if self.skipped and self.ok:
            raise ValueError("skipped supply check cannot be ok")
        return self


class SupplyReport(ContractModel):
    """Frozen lock/pin inspection. Optional persist under $DAEDALUS_DATA/supply/."""

    run_id: str = ""
    root: str = ""
    lock_sha256: str = ""
    lock_check: str = ""
    packages: list[SupplyPackage] = Field(default_factory=list)
    checks: list[SupplyCheck] = Field(default_factory=list)
    ok: bool
    skipped: bool = False
    blockers: list[str] = Field(default_factory=list)
    persisted: str = ""

    @model_validator(mode="after")
    def skipped_implies_not_ok(self) -> SupplyReport:
        if self.skipped and self.ok:
            raise ValueError("skipped supply report cannot be ok")
        return self


__all__ = [
    "SupplyCheck",
    "SupplyPackage",
    "SupplyReport",
]
