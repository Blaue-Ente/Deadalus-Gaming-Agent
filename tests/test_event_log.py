"""Construction, migration, seq inference, and replay for daedalus.event_log."""
from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from daedalus.contracts import CONTRACT_SCHEMA_VERSION, EventKind, RunEvent
from daedalus.event_log import (
    SCHEMA_VERSION,
    EventLog,
    EventLogError,
    assign_seq,
    from_engine_row,
    from_row,
    from_sse_pair,
    mint,
    migrate,
    new_correlation_id,
    replay,
    rows_from_engine,
    to_engine_payload,
    to_row,
    write_via_engine,
)

ROOT = Path(__file__).resolve().parents[1]
EVENT_LOG_PATH = ROOT / "daedalus" / "event_log.py"
FORBIDDEN_IMPORTS = frozenset({
    "daedalus.agent",
    "daedalus.native_tools",
    "daedalus.job_ledger",
})


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("DAEDALUS_STANDALONE", "1")
    monkeypatch.setenv("DAEDALUS_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("DAEDALUS_PLAYGROUND", str(tmp_path / "projects"))
    from daedalus.paths import data_root, playground_projects_dir

    data_root.cache_clear()
    playground_projects_dir.cache_clear()
    yield tmp_path
    data_root.cache_clear()
    playground_projects_dir.cache_clear()


def _v0_row(**overrides: object) -> dict:
    body: dict = {"ts": 12.5, "event": "phase", "data": {"n": 2, "label": "Строене"}}
    body.update(overrides)
    return body


def test_schema_version_defaults_to_1():
    ev = mint("log", {"line": "ok"}, run_id="job-a", seq=1, ts=1.0)
    assert SCHEMA_VERSION == 1
    assert CONTRACT_SCHEMA_VERSION == 1
    assert ev.schema_version == 1


def test_mint_and_event_log_append_assigns_seq_and_correlation():
    cid = new_correlation_id()
    log = EventLog("job-abc", correlation_id=cid)
    first = log.append("phase", {"n": 0, "label": "Чете задачата"}, ts=10.0)
    second = log.append(EventKind.LOG, {"line": "go"}, ts=11.0)
    assert log.run_id == "job-abc"
    assert log.correlation_id == cid
    assert first.seq == 1
    assert second.seq == 2
    assert first.correlation_id == cid
    assert second.correlation_id == cid
    assert first.run_id == "job-abc"
    assert second.event == EventKind.LOG or second.event == "log"
    events = log.events()
    assert events == (first, second)
    assert isinstance(events, tuple)


def test_mint_requires_run_id_and_positive_seq():
    with pytest.raises(EventLogError, match="run_id required"):
        mint("phase", run_id="", seq=1)
    with pytest.raises(EventLogError, match="seq must be >= 1"):
        mint("phase", run_id="job-a", seq=0)
    with pytest.raises(EventLogError, match="run_id required"):
        EventLog("")


def test_migrate_v0_engine_row():
    raw = _v0_row()
    assert "schema_version" not in raw
    assert "seq" not in raw
    canonical = migrate(raw)
    assert canonical["schema_version"] == 1
    assert canonical["seq"] == 0
    assert canonical["correlation_id"] == ""
    assert canonical["run_id"] == ""
    assert canonical["event"] == "phase"
    ev = from_row(raw)
    assert ev.ts == 12.5
    assert ev.data["n"] == 2
    assert ev.seq == 0


def test_from_row_to_row_roundtrip():
    ev = mint("stage", {"i": 3}, run_id="job-a", seq=4, correlation_id="corr1", ts=9.0)
    dumped = to_row(ev)
    assert dumped["seq"] == 4
    assert dumped["correlation_id"] == "corr1"
    assert dumped["schema_version"] == 1
    again = from_row(dumped)
    assert again == ev
    assert from_row(json.dumps(dumped)) == ev


def test_unknown_schema_version_rejected():
    with pytest.raises(EventLogError, match="unsupported schema_version"):
        migrate({"schema_version": 2, "ts": 1.0, "event": "log", "data": {}})
    with pytest.raises(EventLogError, match="unsupported schema_version"):
        from_row({"schema_version": 2, "ts": 1.0, "event": "log"})


def test_open_event_strings_validate():
    stage = from_row({"ts": 1.0, "event": "stage", "data": {"i": 1}})
    asset = from_row({"ts": 2.0, "event": "asset", "data": {"path": "a.png"}})
    assert stage.event == "stage"
    assert asset.event == "asset"


def test_assign_seq_infers_and_preserves():
    rows = [
        _v0_row(ts=1.0, event="log", data={"line": "a"}),
        {"ts": 2.0, "event": "phase", "data": {}, "seq": 9},
        _v0_row(ts=3.0, event="done", data={}),
    ]
    assigned = assign_seq(rows, run_id="job-z")
    assert [ev.seq for ev in assigned] == [1, 9, 10]
    assert all(ev.run_id == "job-z" for ev in assigned)
    with pytest.raises(EventLogError, match="start must be >= 1"):
        assign_seq(rows, start=0)


def test_replay_orders_by_seq():
    rows = [
        {"ts": 30.0, "event": "done", "data": {}, "seq": 3},
        {"ts": 10.0, "event": "phase", "data": {"n": 0}, "seq": 1},
        {"ts": 20.0, "event": "log", "data": {"line": "x"}, "seq": 2},
    ]
    out = replay(rows)
    assert [ev.event if isinstance(ev.event, str) else ev.event.value for ev in out] == [
        "phase",
        "log",
        "done",
    ]
    assert [ev.seq for ev in out] == [1, 2, 3]


def test_replay_require_contiguous_gap_and_duplicate():
    gap = [
        {"ts": 1.0, "event": "phase", "data": {}, "seq": 1},
        {"ts": 2.0, "event": "log", "data": {}, "seq": 3},
    ]
    with pytest.raises(EventLogError, match="seq gap"):
        replay(gap, require_contiguous=True)
    dup = [
        {"ts": 1.0, "event": "phase", "data": {}, "seq": 1},
        {"ts": 2.0, "event": "log", "data": {}, "seq": 1},
    ]
    with pytest.raises(EventLogError, match="duplicate seq"):
        replay(dup, require_contiguous=True)
    ok = replay(
        [
            {"ts": 1.0, "event": "phase", "data": {}, "seq": 1},
            {"ts": 2.0, "event": "log", "data": {}, "seq": 2},
        ],
        require_contiguous=True,
    )
    assert [ev.seq for ev in ok] == [1, 2]


def test_replay_skips_corrupt_lines():
    rows = [
        _v0_row(ts=1.0, event="phase", data={"n": 0}),
        "{not-json",
        {"event": "log"},
        _v0_row(ts=2.0, event="done", data={}),
    ]
    out = replay(rows)
    assert [ev.event if isinstance(ev.event, str) else ev.event.value for ev in out] == [
        "phase",
        "done",
    ]
    assert [ev.seq for ev in out] == [1, 2]
    with pytest.raises(EventLogError):
        replay(["{not-json"], skip_corrupt=False)


def test_wrap_run_engine_append_and_replay(isolated):
    from daedalus.run_engine import append_event, load_events

    append_event("job-abc", "phase", {"n": 2, "label": "Строене"})
    append_event("job-abc", "log", {"line": "ok"})
    raw = load_events("job-abc")
    assert raw[0]["event"] == "phase"
    assert "seq" not in raw[0]
    out = replay(raw, run_id="job-abc")
    assert [ev.seq for ev in out] == [1, 2]
    assert out[0].run_id == "job-abc"
    assert out[0].data["n"] == 2
    assert out[1].event == EventKind.LOG or out[1].event == "log"
    via = rows_from_engine("job-abc")
    assert [ev.seq for ev in via] == [1, 2]
    assert via[0].run_id == "job-abc"


def test_to_engine_payload_feeds_append_event(isolated):
    from daedalus.run_engine import load_events

    ev = mint("stage", {"i": 3, "slug": "snake"}, run_id="job-w", seq=1, ts=5.0)
    event, data = to_engine_payload(ev)
    assert event == "stage"
    assert data == {"i": 3, "slug": "snake"}
    write_via_engine("job-w", ev)
    raw = load_events("job-w")
    assert raw[-1]["event"] == "stage"
    assert raw[-1]["data"]["i"] == 3
    replayed = replay(raw, run_id="job-w")
    assert replayed[-1].seq == 1
    assert replayed[-1].data["i"] == 3


def test_from_sse_pair_synthesizes_ts():
    ev = from_sse_pair("log", {"line": "hi"}, run_id="job-s", ts=7.5)
    assert ev.ts == 7.5
    assert ev.seq == 0
    assert ev.event == EventKind.LOG or ev.event == "log"
    assigned = assign_seq([from_sse_pair("phase", {"n": 0}, run_id="job-s")])
    assert assigned[0].seq == 1
    assert assigned[0].ts > 0


def test_from_engine_row_fills_run_id():
    ev = from_engine_row(_v0_row(), run_id="job-fill")
    assert ev.run_id == "job-fill"
    assert ev.seq == 0
    kept = from_engine_row({**_v0_row(), "run_id": "kept"}, run_id="other")
    assert kept.run_id == "kept"


def test_write_via_engine_refuses_empty_run_id():
    ev = mint("log", run_id="job-a", seq=1, ts=1.0)
    with pytest.raises(EventLogError, match="run_id required"):
        write_via_engine("", ev)


def test_v0_run_event_still_validates():
    raw = {"ts": 12.5, "event": "phase", "data": {"n": 2, "label": "Строене"}}
    ev = RunEvent.model_validate(raw)
    assert ev.seq == 0
    assert ev.correlation_id == ""
    assert ev.run_id == ""


def test_event_log_does_not_import_runtime():
    tree = ast.parse(EVENT_LOG_PATH.read_text(encoding="utf-8"))
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

    code = (
        "import daedalus.event_log\n"
        "import sys\n"
        "bad = [n for n in sys.modules if n in "
        "('daedalus.agent', 'daedalus.native_tools', 'daedalus.job_ledger', "
        "'daedalus.run_engine') "
        "or n.startswith('daedalus.agent.') "
        "or n.startswith('daedalus.native_tools.') "
        "or n.startswith('daedalus.job_ledger.') "
        "or n.startswith('daedalus.run_engine.')]\n"
        "assert not bad, bad\n"
        "assert daedalus.event_log.SCHEMA_VERSION == 1\n"
    )
    env = os.environ.copy()
    env["DAEDALUS_STANDALONE"] = "1"
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
