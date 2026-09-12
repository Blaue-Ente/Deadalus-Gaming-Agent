"""OpenTelemetry provider setup.

Leaf module. Do not import agent.py, native_tools, job_ledger, run_engine,
context_budget, memory_provenance, context_assembler, model_gateway, or event_log.
Do not write studio/*.json. skip ≠ ok. Run schema v1 is unchanged.

``setup()`` is an identity no-op when ``DAEDALUS_OTEL`` is unset. Off does not
call ``set_tracer_provider``. ``probe()`` emits ``daedalus.otel.probe``.
``annotate()`` copies an allowlist only. Optional persist under
``$DAEDALUS_DATA/otel/{run_id}.json``.
"""
from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from daedalus.contracts.otel import (
    OtelMetricRecord,
    OtelMode,
    OtelReport,
    OtelSpanRecord,
)
from daedalus.paths import data_root

log = logging.getLogger(__name__)

FLAG_ENV = "DAEDALUS_OTEL"
OFFLINE_ENV = "DAEDALUS_OFFLINE"
SDK_DISABLED_ENV = "OTEL_SDK_DISABLED"
ENDPOINT_ENV = "OTEL_EXPORTER_OTLP_ENDPOINT"
SERVICE_ENV = "OTEL_SERVICE_NAME"
DEFAULT_SERVICE = "daedalus"
PROBE_SPAN = "daedalus.otel.probe"
PROBE_METRIC = "daedalus.otel.probes"

ANNOTATE_KEYS = (
    "daedalus.run_id",
    "daedalus.session_id",
    "daedalus.context.overflow",
    "daedalus.context.tokens_est",
    "daedalus.context.tokens_kept",
    "daedalus.memory.labeled",
    "daedalus.memory.skipped_count",
)
_SHORT_KEYS = {
    "run_id": "daedalus.run_id",
    "session_id": "daedalus.session_id",
    "overflow": "daedalus.context.overflow",
    "tokens_est": "daedalus.context.tokens_est",
    "tokens_kept": "daedalus.context.tokens_kept",
    "labeled": "daedalus.memory.labeled",
    "skipped_count": "daedalus.memory.skipped_count",
}
_TRUTHY = frozenset({"1", "true", "yes", "on"})


@dataclass
class OtelRequest:
    run_id: str = ""
    session_id: str = ""
    persist: bool = False
    exporter: str = ""
    attributes: dict[str, Any] = field(default_factory=dict)


def _env_on(name: str) -> bool:
    return (os.environ.get(name) or "").strip().lower() in _TRUTHY


def otel_enabled() -> bool:
    return _env_on(FLAG_ENV)


def _offline() -> bool:
    return _env_on(OFFLINE_ENV)


def _sdk_disabled() -> bool:
    return _env_on(SDK_DISABLED_ENV)


def _endpoint() -> str:
    return (os.environ.get(ENDPOINT_ENV) or "").strip()


def _service_name() -> str:
    raw = (os.environ.get(SERVICE_ENV) or "").strip()
    return raw or DEFAULT_SERVICE


def _safe_run_id(run_id: str) -> str:
    return re.sub(r"[^a-zA-Z0-9._-]+", "-", (run_id or "").strip())[:80]


def _uniq(rows: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for row in rows:
        text = str(row or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out


def _as_attr(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def annotate(attrs: dict[str, Any] | None) -> dict[str, str]:
    """Copy reserved keys only. Drops prompt / api_key / unknown fields."""
    raw = dict(attrs or {})
    out: dict[str, str] = {}
    allowed = frozenset(ANNOTATE_KEYS)
    for key, value in raw.items():
        dest = key if key in allowed else _SHORT_KEYS.get(str(key))
        if dest is None:
            continue
        text = _as_attr(value)
        if text == "":
            continue
        out[dest] = text
    return out


def _load_sdk() -> dict[str, Any]:
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import InMemoryMetricReader
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    return {
        "MeterProvider": MeterProvider,
        "InMemoryMetricReader": InMemoryMetricReader,
        "Resource": Resource,
        "TracerProvider": TracerProvider,
        "SimpleSpanProcessor": SimpleSpanProcessor,
        "InMemorySpanExporter": InMemorySpanExporter,
    }


def _load_otlp() -> dict[str, Any]:
    from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    return {
        "OTLPMetricExporter": OTLPMetricExporter,
        "OTLPSpanExporter": OTLPSpanExporter,
        "PeriodicExportingMetricReader": PeriodicExportingMetricReader,
        "BatchSpanProcessor": BatchSpanProcessor,
    }


def _resolve(req: OtelRequest) -> tuple[str, str, list[str], bool]:
    """Return (mode, exporter, blockers, skipped)."""
    exp = (req.exporter or "").strip().lower()
    flagged = otel_enabled()
    if not flagged and exp not in {"memory", "otlp"}:
        return OtelMode.OFF.value, "", [], False
    if flagged and _offline():
        return OtelMode.OFF.value, "", ["offline"], True
    if exp == "otlp" and _offline():
        return OtelMode.OFF.value, "", ["offline"], True
    if flagged and _sdk_disabled():
        return OtelMode.OFF.value, "", ["sdk_disabled"], True
    if exp == "memory":
        return OtelMode.MEMORY.value, "memory", [], False
    if exp == "otlp" or flagged:
        endpoint = _endpoint()
        if not endpoint:
            return OtelMode.OFF.value, "", ["missing_endpoint"], True
        return OtelMode.OTLP.value, "otlp", [], False
    return OtelMode.OFF.value, "", [], False


def _persist(run_id: str, report: OtelReport) -> str:
    safe = _safe_run_id(run_id)
    if not safe:
        return ""
    dest = data_root() / "otel"
    dest.mkdir(parents=True, exist_ok=True)
    path = dest / f"{safe}.json"
    path.write_text(
        json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return str(path)


def _with_persist(req: OtelRequest, report: OtelReport) -> OtelReport:
    if not req.persist:
        return report
    try:
        persisted = _persist(req.run_id, report)
    except Exception as exc:
        log.debug("otel persist skipped: %s", exc)
        return report
    if not persisted:
        return report
    return report.model_copy(update={"persisted": persisted})


class OpenTelemetry:
    """Stateless-looking façade. Holds a provider only after setup()."""

    def __init__(self) -> None:
        self._req = OtelRequest()
        self._mode = OtelMode.OFF.value
        self._exporter = ""
        self._enabled = False
        self._skipped = False
        self._blockers: list[str] = []
        self._service_name = DEFAULT_SERVICE
        self._endpoint = ""
        self._attrs: dict[str, str] = {}
        self._spans: list[OtelSpanRecord] = []
        self._metrics: list[OtelMetricRecord] = []
        self._probed = 0
        self._global = False
        self._set_global = False
        self._provider: Any = None
        self._meter_provider: Any = None
        self._tracer: Any = None
        self._counter: Any = None
        self._prev_trace: Any = None
        self._prev_meter: Any = None
        self._setup_done = False

    def annotate(self, attrs: dict[str, Any] | None) -> dict[str, str]:
        return annotate(attrs)

    def _report(self) -> OtelReport:
        return OtelReport(
            run_id=self._req.run_id,
            session_id=self._req.session_id,
            mode=self._mode,
            exporter=self._exporter,
            enabled=self._enabled,
            service_name=self._service_name,
            endpoint=self._endpoint,
            spans=list(self._spans),
            metrics=list(self._metrics),
            probed=self._probed,
            ok=not self._skipped,
            skipped=self._skipped,
            blockers=_uniq(self._blockers),
            persisted="",
            global_provider=self._global,
        )

    def _reset_provider(self) -> None:
        if self._provider is not None and hasattr(self._provider, "shutdown"):
            try:
                self._provider.shutdown()
            except Exception as exc:
                log.debug("otel tracer shutdown: %s", exc)
        if self._meter_provider is not None and hasattr(self._meter_provider, "shutdown"):
            try:
                self._meter_provider.shutdown()
            except Exception as exc:
                log.debug("otel meter shutdown: %s", exc)
        if self._set_global:
            try:
                from opentelemetry import metrics as otel_metrics
                from opentelemetry import trace as otel_trace

                if self._prev_trace is not None:
                    otel_trace.set_tracer_provider(self._prev_trace)
                if self._prev_meter is not None:
                    otel_metrics.set_meter_provider(self._prev_meter)
            except Exception as exc:
                log.debug("otel restore provider: %s", exc)
        self._set_global = False
        self._global = False
        self._provider = None
        self._meter_provider = None
        self._tracer = None
        self._counter = None
        self._prev_trace = None
        self._prev_meter = None

    def _bind_sdk(self, mode: str, endpoint: str) -> None:
        sdk = _load_sdk()
        resource = sdk["Resource"].create({"service.name": self._service_name})
        if mode == OtelMode.MEMORY.value:
            trace_exporter = sdk["InMemorySpanExporter"]()
            provider = sdk["TracerProvider"](resource=resource)
            provider.add_span_processor(sdk["SimpleSpanProcessor"](trace_exporter))
            reader = sdk["InMemoryMetricReader"]()
            meter_provider = sdk["MeterProvider"](metric_readers=[reader], resource=resource)
            set_global = False
        else:
            otlp = _load_otlp()
            provider = sdk["TracerProvider"](resource=resource)
            provider.add_span_processor(
                otlp["BatchSpanProcessor"](otlp["OTLPSpanExporter"](endpoint=endpoint))
            )
            reader = otlp["PeriodicExportingMetricReader"](
                otlp["OTLPMetricExporter"](endpoint=endpoint)
            )
            meter_provider = sdk["MeterProvider"](metric_readers=[reader], resource=resource)
            set_global = True
        tracer = provider.get_tracer("daedalus.otel")
        meter = meter_provider.get_meter("daedalus.otel")
        counter = meter.create_counter(PROBE_METRIC, unit="1")
        if set_global:
            from opentelemetry import metrics as otel_metrics
            from opentelemetry import trace as otel_trace

            self._prev_trace = otel_trace.get_tracer_provider()
            self._prev_meter = otel_metrics.get_meter_provider()
            otel_trace.set_tracer_provider(provider)
            otel_metrics.set_meter_provider(meter_provider)
            self._set_global = True
            self._global = True
        self._provider = provider
        self._meter_provider = meter_provider
        self._tracer = tracer
        self._counter = counter

    def setup(self, req: OtelRequest) -> OtelReport:
        self._reset_provider()
        self._req = req
        self._spans = []
        self._metrics = []
        self._probed = 0
        self._attrs = annotate(req.attributes)
        self._service_name = _service_name()
        self._endpoint = _endpoint()
        mode, exporter, blockers, skipped = _resolve(req)
        self._mode = mode
        self._exporter = exporter
        self._blockers = list(blockers)
        self._skipped = skipped
        self._enabled = False
        self._setup_done = True
        if skipped:
            return _with_persist(req, self._report())
        if mode in {OtelMode.MEMORY.value, OtelMode.OTLP.value}:
            try:
                self._bind_sdk(mode, self._endpoint)
            except Exception as exc:
                log.debug("otel sdk setup failed: %s", exc)
                self._reset_provider()
                self._mode = OtelMode.OFF.value
                self._exporter = ""
                self._skipped = True
                self._enabled = False
                self._blockers = _uniq([*self._blockers, "sdk_unavailable"])
                return _with_persist(req, self._report())
            self._enabled = True
        return _with_persist(req, self._report())

    def probe(self) -> OtelSpanRecord:
        if not self._setup_done:
            self.setup(OtelRequest())
        if self._skipped or not self._enabled or self._tracer is None:
            row = OtelSpanRecord(
                name=PROBE_SPAN,
                attributes=dict(self._attrs),
                skipped=True,
                ok=False,
                blockers=_uniq([*self._blockers, "not_enabled"] if not self._blockers else self._blockers),
            )
            if not self._skipped:
                # Off no-op: observational probe is ok and not a skip-fail.
                row = OtelSpanRecord(
                    name=PROBE_SPAN,
                    attributes=dict(self._attrs),
                    skipped=False,
                    ok=True,
                )
            self._spans.append(row)
            self._probed += 1
            if self._req.persist:
                _with_persist(self._req, self._report())
            return row
        attrs = dict(self._attrs)
        if self._req.run_id and "daedalus.run_id" not in attrs:
            attrs["daedalus.run_id"] = self._req.run_id
        if self._req.session_id and "daedalus.session_id" not in attrs:
            attrs["daedalus.session_id"] = self._req.session_id
        with self._tracer.start_as_current_span(PROBE_SPAN) as span:
            for key, value in attrs.items():
                span.set_attribute(key, value)
            ctx = span.get_span_context()
            trace_id = format(ctx.trace_id, "032x") if getattr(ctx, "trace_id", 0) else ""
            span_id = format(ctx.span_id, "016x") if getattr(ctx, "span_id", 0) else ""
        if self._counter is not None:
            try:
                self._counter.add(1)
            except Exception as exc:
                log.debug("otel probe metric: %s", exc)
        row = OtelSpanRecord(
            name=PROBE_SPAN,
            trace_id=trace_id,
            span_id=span_id,
            attributes=attrs,
            skipped=False,
            ok=True,
        )
        self._spans.append(row)
        self._metrics.append(
            OtelMetricRecord(name=PROBE_METRIC, value=1.0, unit="1", skipped=False, ok=True)
        )
        self._probed += 1
        if self._req.persist:
            _with_persist(self._req, self._report())
        return row

    def shutdown(self) -> OtelReport:
        self._reset_provider()
        self._enabled = False
        if self._mode in {OtelMode.MEMORY.value, OtelMode.OTLP.value} and not self._skipped:
            self._mode = OtelMode.OFF.value
            self._exporter = ""
        report = _with_persist(self._req, self._report())
        return report


def otel_dir() -> Path:
    path = data_root() / "otel"
    path.mkdir(parents=True, exist_ok=True)
    return path


def setup(req: OtelRequest) -> OtelReport:
    return OpenTelemetry().setup(req)
