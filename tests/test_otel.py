"""OpenTelemetry leaf — provider setup / probe / annotate. No live collector."""
from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from daedalus.contracts import (
    OtelMetricRecord,
    OtelMode,
    OtelReport,
    OtelSpanRecord,
    Run,
    RunCheckpoint,
)
from daedalus.otel import OpenTelemetry, OtelRequest, annotate
from daedalus.paths import data_root, playground_projects_dir

ROOT = Path(__file__).resolve().parents[1]
LEAF = ROOT / "daedalus" / "otel.py"
FORBIDDEN_IMPORTS = frozenset({
    "daedalus.agent",
    "daedalus.native_tools",
    "daedalus.job_ledger",
    "daedalus.run_engine",
    "daedalus.context_budget",
    "daedalus.memory_provenance",
    "daedalus.context_assembler",
    "daedalus.model_gateway",
    "daedalus.event_log",
})


def _isolate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DAEDALUS_STANDALONE", "1")
    monkeypatch.setenv("DAEDALUS_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("DAEDALUS_PLAYGROUND", str(tmp_path / "projects"))
    monkeypatch.delenv("DAEDALUS_OTEL", raising=False)
    monkeypatch.delenv("DAEDALUS_OFFLINE", raising=False)
    monkeypatch.delenv("DAEDALUS_BENCH_LIVE", raising=False)
    monkeypatch.delenv("OTEL_SDK_DISABLED", raising=False)
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    monkeypatch.delenv("OTEL_SERVICE_NAME", raising=False)
    monkeypatch.delenv("CHROMA_OTEL_COLLECTION_ENDPOINT", raising=False)
    monkeypatch.delenv("CHROMA_OTEL_GRANULARITY", raising=False)
    data_root.cache_clear()
    playground_projects_dir.cache_clear()


def test_contract_skip_cannot_be_ok():
    with pytest.raises(ValidationError):
        OtelSpanRecord(name="daedalus.otel.probe", skipped=True, ok=True)
    with pytest.raises(ValidationError):
        OtelMetricRecord(name="daedalus.otel.probes", skipped=True, ok=True)
    with pytest.raises(ValidationError):
        OtelReport(ok=True, skipped=True)
    with pytest.raises(ValidationError):
        OtelSpanRecord.model_validate({"name": "x", "nope": 1})
    row = OtelSpanRecord(name="daedalus.otel.probe", skipped=True, ok=False)
    assert row.schema_version == 1
    assert OtelSpanRecord.model_validate(row.model_dump(mode="json")) == row
    metric = OtelMetricRecord(name="daedalus.otel.probes", value=1, unit="1")
    assert metric.schema_version == 1
    assert OtelMetricRecord.model_validate(metric.model_dump(mode="json")) == metric
    report = OtelReport(ok=True, skipped=False, mode=OtelMode.OFF.value)
    assert report.schema_version == 1
    assert OtelReport.model_validate(report.model_dump(mode="json")) == report


def test_run_schema_unchanged():
    assert "otel" not in Run.model_fields
    assert "trace" not in Run.model_fields
    assert "trace_id" not in Run.model_fields
    assert "span_id" not in Run.model_fields
    assert "otel" not in RunCheckpoint.model_fields
    assert "trace" not in RunCheckpoint.model_fields
    assert "trace_id" not in RunCheckpoint.model_fields
    with pytest.raises(ValidationError):
        Run.model_validate({"run_id": "r", "nope": 1})


def test_leaf_does_not_import_forbidden():
    tree = ast.parse(LEAF.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name not in FORBIDDEN_IMPORTS
                assert not any(
                    alias.name == bad or alias.name.startswith(bad + ".")
                    for bad in FORBIDDEN_IMPORTS
                )
        elif isinstance(node, ast.ImportFrom) and node.module:
            assert node.module not in FORBIDDEN_IMPORTS
            assert not any(
                node.module == bad or node.module.startswith(bad + ".")
                for bad in FORBIDDEN_IMPORTS
            )
    src = LEAF.read_text(encoding="utf-8")
    for bad in (
        "context_budget",
        "memory_provenance",
        "context_assembler",
        "model_gateway",
        "event_log",
        "native_tools",
        "job_ledger",
        "run_engine",
    ):
        assert f"daedalus.{bad}" not in src
        assert f"from .{bad}" not in src
    assert "from daedalus.agent import" not in src
    code = (
        "import daedalus.otel as ot\n"
        "import sys\n"
        "bad = [n for n in sys.modules if n in "
        "('daedalus.agent', 'daedalus.native_tools', 'daedalus.job_ledger', "
        "'daedalus.run_engine', 'daedalus.context_budget', "
        "'daedalus.memory_provenance', 'daedalus.context_assembler', "
        "'daedalus.model_gateway', 'daedalus.event_log') "
        "or n.startswith('daedalus.agent.') "
        "or n.startswith('daedalus.native_tools.') "
        "or n.startswith('daedalus.context_budget.') "
        "or n.startswith('daedalus.memory_provenance.') "
        "or n.startswith('daedalus.context_assembler.') "
        "or n.startswith('daedalus.model_gateway.') "
        "or n.startswith('daedalus.event_log.')]\n"
        "assert not bad, bad\n"
        "from daedalus.otel import OtelRequest\n"
        "rep = ot.setup(OtelRequest())\n"
        "assert (not rep.enabled) and rep.ok and (not rep.skipped)\n"
        "print('import-ok')\n"
    )
    env = os.environ.copy()
    env["DAEDALUS_STANDALONE"] = "1"
    env.pop("DAEDALUS_OTEL", None)
    env.pop("OTEL_EXPORTER_OTLP_ENDPOINT", None)
    env["PYTHONPATH"] = str(ROOT) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    assert "import-ok" in result.stdout


def test_agent_and_loops_untouched():
    agent = (ROOT / "daedalus" / "agent.py").read_text(encoding="utf-8")
    native = (ROOT / "daedalus" / "native_tools.py").read_text(encoding="utf-8")
    loop = (ROOT / "daedalus" / "tool_loop.py").read_text(encoding="utf-8")
    main = (ROOT / "app" / "main.py").read_text(encoding="utf-8")
    boot = (ROOT / "daedalus" / "standalone_boot.py").read_text(encoding="utf-8")
    for blob in (agent, native, loop, main, boot):
        assert "OpenTelemetry" not in blob
        assert "daedalus.otel" not in blob
        assert "from daedalus.otel" not in blob
    assert "NEED_PROPOSE_START" in agent
    assert "NEED_SANDBOX" in agent
    assert "NEED_VERIFY" in agent


def test_unset_env_is_noop_ok(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    from opentelemetry import trace

    before = trace.get_tracer_provider()
    report = OpenTelemetry().setup(OtelRequest(run_id="run-off"))
    after = trace.get_tracer_provider()
    assert before is after
    assert report.enabled is False
    assert report.ok is True
    assert report.skipped is False
    assert report.mode == OtelMode.OFF.value
    assert report.global_provider is False
    assert report.persisted == ""
    assert not (data_root() / "otel" / "run-off.json").exists()


def test_enabled_without_endpoint_skip_not_ok(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    monkeypatch.setenv("DAEDALUS_OTEL", "1")
    report = OpenTelemetry().setup(OtelRequest())
    assert report.skipped is True
    assert report.ok is False
    assert report.enabled is False
    assert "missing_endpoint" in report.blockers


def test_enabled_offline_skip_not_ok(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    monkeypatch.setenv("DAEDALUS_OTEL", "1")
    monkeypatch.setenv("DAEDALUS_OFFLINE", "1")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://127.0.0.1:4317")
    report = OpenTelemetry().setup(OtelRequest())
    assert report.skipped is True
    assert report.ok is False
    assert "offline" in report.blockers


def test_sdk_disabled_skip_not_ok(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    monkeypatch.setenv("DAEDALUS_OTEL", "1")
    monkeypatch.setenv("OTEL_SDK_DISABLED", "true")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://127.0.0.1:4317")
    report = OpenTelemetry().setup(OtelRequest())
    assert report.skipped is True
    assert report.ok is False
    assert "sdk_disabled" in report.blockers


def test_memory_exporter_probe(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    from opentelemetry import trace

    before = trace.get_tracer_provider()
    otel = OpenTelemetry()
    report = otel.setup(
        OtelRequest(
            run_id="run-mem",
            session_id="sess-1",
            exporter="memory",
            attributes={"daedalus.run_id": "run-mem", "prompt": "secret"},
        )
    )
    assert report.ok is True
    assert report.skipped is False
    assert report.enabled is True
    assert report.mode == OtelMode.MEMORY.value
    assert report.exporter == "memory"
    assert report.global_provider is False
    assert trace.get_tracer_provider() is before
    span = otel.probe()
    assert span.name == "daedalus.otel.probe"
    assert span.ok is True
    assert span.skipped is False
    assert span.trace_id
    assert span.span_id
    assert span.attributes["daedalus.run_id"] == "run-mem"
    assert "prompt" not in span.attributes
    assert otel.shutdown().ok is True


def test_annotate_allowlist():
    got = annotate(
        {
            "run_id": "r1",
            "session_id": "s1",
            "overflow": 12,
            "tokens_est": 400,
            "tokens_kept": 380,
            "labeled": 3,
            "skipped_count": 1,
            "prompt": "do not copy",
            "api_key": "sk-secret",
            "ok": True,
            "daedalus.context.overflow": 99,
        }
    )
    assert got["daedalus.run_id"] == "r1"
    assert got["daedalus.session_id"] == "s1"
    assert got["daedalus.context.overflow"] == "99"
    assert got["daedalus.context.tokens_est"] == "400"
    assert got["daedalus.context.tokens_kept"] == "380"
    assert got["daedalus.memory.labeled"] == "3"
    assert got["daedalus.memory.skipped_count"] == "1"
    assert "prompt" not in got
    assert "api_key" not in got
    assert "ok" not in got
    src = (ROOT / "daedalus" / "otel.py").read_text(encoding="utf-8")
    assert "from daedalus.context_budget" not in src
    assert "from daedalus.memory_provenance" not in src


def test_persist_under_data_otel(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    report = OpenTelemetry().setup(
        OtelRequest(run_id="run-otel-1", persist=True)
    )
    path = Path(report.persisted)
    assert path.is_file()
    assert path == data_root() / "otel" / "run-otel-1.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["run_id"] == "run-otel-1"
    assert payload["ok"] is True
    assert payload["enabled"] is False
    assert payload["schema_version"] == 1


def test_persist_false_writes_nothing(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    report = OpenTelemetry().setup(
        OtelRequest(run_id="run-otel-off", persist=False)
    )
    assert report.persisted == ""
    assert not (data_root() / "otel" / "run-otel-off.json").exists()


def test_sdk_unavailable_skip_not_ok(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    import daedalus.otel as otel_mod

    def _boom() -> dict:
        raise RuntimeError("sdk hidden")

    monkeypatch.setattr(otel_mod, "_load_sdk", _boom)
    report = OpenTelemetry().setup(OtelRequest(exporter="memory"))
    assert report.skipped is True
    assert report.ok is False
    assert "sdk_unavailable" in report.blockers
