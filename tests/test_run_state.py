"""Construction, serialization, migration, and transitions for daedalus.run_state."""
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
    CONTRACT_SCHEMA_VERSION,
    NamedArtifact,
    Run,
    RunBudgetSpend,
    RunCheckpoint,
    RunStatus,
    Slice,
    SliceStatus,
)
from daedalus.run_state import (
    SCHEMA_VERSION,
    RunState,
    RunStateError,
    apply_slice,
    apply_status,
    derive_status,
    dumps,
    from_checkpoint,
    from_job,
    from_run,
    loads,
    migrate,
    to_job_dict,
    with_checkpoint,
)

ROOT = Path(__file__).resolve().parents[1]
RUN_STATE_PATH = ROOT / "daedalus" / "run_state.py"
FORBIDDEN_IMPORTS = frozenset({
    "daedalus.agent",
    "daedalus.native_tools",
    "daedalus.job_ledger",
    "daedalus.run_engine",
})


def _job(**overrides: object) -> dict:
    body: dict = {
        "job_id": "job_ledger1",
        "slug": "arena",
        "session_id": "chat-1",
        "original_ask": "Fix collision",
        "acceptance": ["no overlap"],
        "tasks": [
            {
                "id": "t1",
                "title": "Fix collision",
                "detail": "Fix collision",
                "role": "coder",
                "status": "open",
                "verify": True,
            }
        ],
        "status": "in_progress",
        "created_at": 10.0,
        "updated_at": 11.0,
    }
    body.update(overrides)
    return body


def _run_dump(**overrides: object) -> dict:
    body: dict = {
        "schema_version": 1,
        "run_id": "job_abc123",
        "job_id": "job_abc123",
        "slug": "snake",
        "session_id": "sess-1",
        "original_ask": "Add a pause key",
        "acceptance": ["P pauses", "P resumes"],
        "slices": [
            {
                "id": "t1",
                "title": "Add a pause key",
                "detail": "Add a pause key",
                "role": "coder",
                "status": "open",
                "verify": True,
            }
        ],
        "status": "in_progress",
        "created_at": 1.0,
        "updated_at": 2.0,
    }
    body.update(overrides)
    return body


def test_schema_version_defaults_to_1():
    state = from_job(_job())
    assert SCHEMA_VERSION == 1
    assert CONTRACT_SCHEMA_VERSION == 1
    assert state.schema_version == 1
    assert state.run.schema_version == 1


def test_from_job_maps_ledger_keys():
    job = _job()
    state = from_job(job)
    assert state.run.run_id == job["job_id"]
    assert state.run.job_id == job["job_id"]
    assert state.run.slug == job["slug"]
    assert state.run.session_id == job["session_id"]
    assert state.run.original_ask == job["original_ask"]
    assert state.run.acceptance == job["acceptance"]
    assert state.run.slices[0].id == "t1"
    assert state.run.slices[0].status is SliceStatus.OPEN
    assert state.run.status.value == job["status"]
    assert state.run.created_at == job["created_at"]
    assert state.run.updated_at == job["updated_at"]
    assert state.artifacts == {}
    assert state.progress == ()
    assert state.checkpoint is None


def test_from_job_artifacts_and_progress():
    job = _job(
        artifacts={
            "input_handler": {
                "id": "input_handler",
                "content": "keydown",
                "source": "explorer",
                "updated_at": 12.0,
            }
        },
        progress=[{"ts": 13.0, "note": "mapped input"}],
    )
    state = from_job(job)
    assert set(state.artifacts) == {"input_handler"}
    art = state.artifacts["input_handler"]
    assert isinstance(art, NamedArtifact)
    assert art.content == "keydown"
    assert art.source == "explorer"
    assert art.updated_at == 12.0
    assert len(state.progress) == 1
    assert state.progress[0].ts == 13.0
    assert state.progress[0].note == "mapped input"


def test_to_job_dict_is_ledger_shaped():
    state = from_job(_job())
    dumped = to_job_dict(state)
    assert dumped["schema_version"] == 1
    assert "slices" not in dumped
    assert dumped["tasks"][0]["id"] == "t1"
    assert dumped["tasks"][0]["role"] == "coder"
    assert dumped["artifacts"] == {}
    assert dumped["progress"] == []
    assert dumped["job_id"] == "job_ledger1"
    assert dumped["status"] == "in_progress"
    again = from_job(dumped)
    assert again.run.run_id == state.run.run_id
    assert again.run.slices[0].id == state.run.slices[0].id
    assert again.run.status is state.run.status


def test_to_job_dict_roundtrip_with_extras():
    job = _job(
        artifacts={"game_loop": {"id": "game_loop", "content": "update()", "source": "t1", "updated_at": 1.0}},
        progress=[{"ts": 2.0, "note": "found loop"}],
    )
    state = from_job(job)
    back = to_job_dict(state)
    assert back["tasks"][0]["title"] == "Fix collision"
    assert back["artifacts"]["game_loop"]["content"] == "update()"
    assert "schema_version" not in back["artifacts"]["game_loop"]
    assert back["progress"] == [{"ts": 2.0, "note": "found loop"}]
    assert from_job(back).artifacts["game_loop"].content == "update()"


def test_json_roundtrip():
    state = from_job(_job())
    again = loads(dumps(state))
    assert again == state


def test_loads_p0_02_run_dump():
    raw = _run_dump(
        effort="craft",
        model="cohere/north-mini-code:free",
        complete=False,
        stop_reason="",
        spend=RunBudgetSpend(cap_usd=5, spent_usd=0.0, remaining_usd=5.0).model_dump(),
    )
    state = loads(raw)
    assert state.run.run_id == "job_abc123"
    assert state.run.effort.value == "craft"
    assert state.run.spend is not None
    assert state.run.spend.cap_usd == 5
    assert loads(json.dumps(raw)) == state


def test_migrate_v0_job_without_schema_version():
    job = _job()
    assert "schema_version" not in job
    canonical = migrate(job)
    assert canonical["schema_version"] == 1
    assert canonical["run"]["slices"][0]["id"] == "t1"
    assert "tasks" not in canonical["run"]
    state = loads(job)
    assert state.run.slices[0].title == "Fix collision"


def test_from_checkpoint_drops_unknown_keys():
    raw = {
        "run_id": "job-abc",
        "stage": 3,
        "plan": {"title": "x"},
        "slug": "snake",
        "updated": 9.0,
        "kind": "static",
        "phase": "build",
        "error": "",
    }
    ckpt = from_checkpoint(raw)
    assert isinstance(ckpt, RunCheckpoint)
    assert ckpt.stage == 3
    assert ckpt.plan == {"title": "x"}
    assert ckpt.slug == "snake"
    dumped = ckpt.model_dump()
    assert "kind" not in dumped
    assert "phase" not in dumped
    state = from_run(Run.model_validate(_run_dump(run_id="job-abc", job_id="job-abc")))
    attached = with_checkpoint(state, raw)
    assert attached.checkpoint is not None
    assert attached.checkpoint.stage == 3
    assert attached.checkpoint.run_id == "job-abc"


def test_unknown_schema_version_rejected():
    with pytest.raises(RunStateError, match="unsupported schema_version"):
        migrate({"schema_version": 2, "run": _run_dump()})
    with pytest.raises(RunStateError, match="unsupported schema_version"):
        loads({"schema_version": 2, "run": _run_dump()})


def test_extra_field_on_v1_canonical_rejected():
    canonical = migrate(_job())
    canonical["nope"] = 1
    with pytest.raises(ValidationError):
        loads(canonical)


def test_illegal_status_running_rejected():
    with pytest.raises(ValidationError):
        from_job(_job(status="running"))
    state = from_job(_job())
    with pytest.raises(RunStateError, match="unknown run status"):
        apply_status(state, "running")


def test_apply_status_legal_and_idempotent():
    state = from_job(_job())
    blocked = apply_status(state, "blocked")
    assert blocked.run.status is RunStatus.BLOCKED
    assert apply_status(blocked, RunStatus.BLOCKED) is blocked
    done = apply_status(from_job(_job()), RunStatus.DONE)
    assert done.run.status is RunStatus.DONE
    interrupted = apply_status(state, "interrupted", reason="owner_stop")
    assert interrupted.run.status is RunStatus.INTERRUPTED
    assert interrupted.run.stop_reason == "owner_stop"
    queued = from_run(Run.model_validate(_run_dump(status="queued")))
    assert apply_status(queued, "in_progress").run.status is RunStatus.IN_PROGRESS
    cleared = apply_status(done, "cleared")
    assert cleared.run.status is RunStatus.CLEARED


def test_illegal_transition_raises():
    done = apply_status(from_job(_job()), "done")
    with pytest.raises(RunStateError, match="illegal transition"):
        apply_status(done, "in_progress")
    cleared = apply_status(done, "cleared")
    for nxt in ("queued", "in_progress", "blocked", "done", "failed", "interrupted"):
        with pytest.raises(RunStateError, match="illegal transition"):
            apply_status(cleared, nxt)


def test_apply_slice_recomputes_status():
    job = _job(
        tasks=[
            {"id": "t1", "title": "one", "detail": "one", "role": "coder", "status": "open", "verify": True},
            {"id": "t2", "title": "two", "detail": "two", "role": "verify", "status": "open", "verify": True},
        ]
    )
    state = from_job(job)
    after_one = apply_slice(state, "t1", "done", note="pause wired")
    assert after_one.run.slices[0].status is SliceStatus.DONE
    assert after_one.run.slices[1].status is SliceStatus.OPEN
    assert after_one.run.status is RunStatus.IN_PROGRESS
    assert after_one.progress[-1].note == "pause wired"
    failed = apply_slice(after_one, "t2", SliceStatus.FAILED)
    assert failed.run.status is RunStatus.BLOCKED
    finished = apply_slice(failed, "t2", "done")
    assert finished.run.status is RunStatus.DONE


def test_apply_slice_unknown_id():
    state = from_job(_job())
    with pytest.raises(RunStateError, match="slice not found"):
        apply_slice(state, "missing", "done")


def test_derive_status_matches_mark_task():
    open_slice = Slice(id="t1", title="a")
    done_slice = Slice(id="t1", title="a", status=SliceStatus.DONE)
    failed_slice = Slice(id="t2", title="b", status=SliceStatus.FAILED)
    skipped = Slice(id="t3", title="c", status=SliceStatus.SKIPPED)
    assert derive_status([open_slice]) is RunStatus.IN_PROGRESS
    assert derive_status([done_slice, open_slice]) is RunStatus.IN_PROGRESS
    assert derive_status([failed_slice]) is RunStatus.BLOCKED
    assert derive_status([done_slice, failed_slice]) is RunStatus.BLOCKED
    assert derive_status([done_slice, skipped]) is RunStatus.DONE
    assert derive_status([]) is RunStatus.DONE
    assert derive_status([{"id": "t1", "status": "failed"}]) is RunStatus.BLOCKED


def test_from_run_wraps_contract():
    run = Run.model_validate(_run_dump())
    state = from_run(run)
    assert state.run == run
    assert state.artifacts == {}


def test_run_state_does_not_import_runtime():
    tree = ast.parse(RUN_STATE_PATH.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name not in FORBIDDEN_IMPORTS
                assert not any(alias.name == bad or alias.name.startswith(bad + ".") for bad in FORBIDDEN_IMPORTS)
        elif isinstance(node, ast.ImportFrom) and node.module:
            assert node.module not in FORBIDDEN_IMPORTS
            assert not any(node.module == bad or node.module.startswith(bad + ".") for bad in FORBIDDEN_IMPORTS)

    code = (
        "import daedalus.run_state\n"
        "import sys\n"
        "bad = [n for n in sys.modules if n in "
        "('daedalus.agent', 'daedalus.native_tools', 'daedalus.job_ledger', 'daedalus.run_engine') "
        "or n.startswith('daedalus.agent.') "
        "or n.startswith('daedalus.native_tools.') "
        "or n.startswith('daedalus.job_ledger.') "
        "or n.startswith('daedalus.run_engine.')]\n"
        "assert not bad, bad\n"
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
