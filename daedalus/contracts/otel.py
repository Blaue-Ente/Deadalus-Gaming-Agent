"""OpenTelemetry snapshots. Not part of Run / RunCheckpoint.

P1.08 records tracer/meter setup beside the run.
Schema v1 on Run stays unchanged. Do not import agent, native_tools,
run_engine, context_budget, or memory_provenance.
"""
from __future__ import annotations

from enum import Enum

from pydantic import Field, model_validator

from ._base import ContractModel


class OtelMode(str, Enum):
    OFF = "off"
    NOOP = "noop"
    MEMORY = "memory"
    OTLP = "otlp"


class OtelSpanRecord(ContractModel):
    """One observational span. skipped ⇒ not ok."""

    name: str
    trace_id: str = ""
    span_id: str = ""
    attributes: dict[str, str] = Field(default_factory=dict)
    skipped: bool = False
    ok: bool = True
    blockers: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def skipped_implies_not_ok(self) -> OtelSpanRecord:
        if self.skipped and self.ok:
            raise ValueError("skipped otel span cannot be ok")
        return self


class OtelMetricRecord(ContractModel):
    """One observational metric. skipped ⇒ not ok."""

    name: str
    value: float = 0.0
    unit: str = ""
    skipped: bool = False
    ok: bool = True
    blockers: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def skipped_implies_not_ok(self) -> OtelMetricRecord:
        if self.skipped and self.ok:
            raise ValueError("skipped otel metric cannot be ok")
        return self


class OtelReport(ContractModel):
    """Frozen provider setup. Optional persist under $DAEDALUS_DATA/otel/."""

    run_id: str = ""
    session_id: str = ""
    mode: str = OtelMode.OFF.value
    exporter: str = ""
    enabled: bool = False
    service_name: str = "daedalus"
    endpoint: str = ""
    spans: list[OtelSpanRecord] = Field(default_factory=list)
    metrics: list[OtelMetricRecord] = Field(default_factory=list)
    probed: int = 0
    ok: bool
    skipped: bool = False
    blockers: list[str] = Field(default_factory=list)
    persisted: str = ""
    global_provider: bool = False

    @model_validator(mode="after")
    def skipped_implies_not_ok(self) -> OtelReport:
        if self.skipped and self.ok:
            raise ValueError("skipped otel report cannot be ok")
        return self


__all__ = [
    "OtelMetricRecord",
    "OtelMode",
    "OtelReport",
    "OtelSpanRecord",
]
