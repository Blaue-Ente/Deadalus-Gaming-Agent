"""Job ledger, sub-agent roles, and hard-job Definition of Done."""
from __future__ import annotations

import pytest

from daedalus.job_contract import evaluate
from daedalus.job_ledger import (
    add_artifact,
    completion_verdict,
    ensure_job,
    format_pack,
    ingest_report,
    mark_task,
    next_open_task,
    open_count_for_session,
    parse_artifacts,
)
from daedalus.subagents import parse_subagent_markers, tools_for_role


@pytest.fixture
def harness_tmp(tmp_path, monkeypatch):
    monkeypatch.setenv("DAEDALUS_STANDALONE", "1")
    monkeypatch.setenv("DAEDALUS_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("DAEDALUS_PLAYGROUND", str(tmp_path / "projects"))
    from daedalus.paths import data_root, playground_projects_dir
    data_root.cache_clear()
    playground_projects_dir.cache_clear()
    yield tmp_path


def test_ledger_roundtrip_and_artifacts(harness_tmp):
    job = ensure_job(
        slug="snake",
        original_ask="Add a pause key that freezes the snake",
        session_id="sess-1",
        acceptance=["P pauses", "P resumes", "score still visible"],
    )
    assert job["job_id"].startswith("job_")
    assert next_open_task(job)["id"] == "t1"
    add_artifact(job["job_id"], "input_handler", "keydown in main.js line 40", source="explorer")
    job = add_artifact(job["job_id"], "pause_flag", "let paused = false", source="t1")
    pack = format_pack(job, refs=["input_handler"])
    assert "Add a pause key" in pack
    assert "input_handler" in pack
    assert "P pauses" in pack
    mark_task(job["job_id"], "t1", "done", note="pause wired")
    assert open_count_for_session("sess-1") == 0


def test_parse_artifacts_and_contradiction():
    text = (
        "Found the loop.\n"
        "===ARTIFACT: game_loop===\n"
        "update() in main.js\n"
        "===END===\n"
        "TASK_COMPLETE\n"
        "could not complete without a human\n"
    )
    arts = parse_artifacts(text)
    assert arts == [("game_loop", "update() in main.js")]
    assert completion_verdict(text) == "contradiction"
    assert completion_verdict("TASK_COMPLETE\nfiles written") == "complete"
    assert completion_verdict("TASK_BLOCKED: missing asset") == "blocked"
    assert completion_verdict("still looking") == "none"


def test_ingest_marks_failed_on_contradiction(harness_tmp):
    job = ensure_job(slug="arena", original_ask="Fix collision", session_id="s2")
    ingest_report(
        job["job_id"],
        {"summary": "TASK_COMPLETE\nneeds human to finish the shader"},
        task_id="t1",
    )
    from daedalus.job_ledger import load
    job = load(job["job_id"])
    assert job["tasks"][0]["status"] == "failed"
    assert open_count_for_session("s2") == 1


def test_subagent_marker_role_and_tools():
    raw = "<NEED_SUBAGENT: snake | add a pause key>\n<NEED_SUBAGENT: snake | map input | explorer>"
    calls = parse_subagent_markers(raw)
    assert calls[0] == {"slug": "snake", "task": "add a pause key"}
    assert calls[1]["role"] == "explorer"
    assert "write_file" in tools_for_role("coder")
    assert "write_file" not in tools_for_role("explorer")
    assert "write_file" not in tools_for_role("verify")
    assert "read_file" in tools_for_role("explorer")


def test_explorer_complete_does_not_close_coder_slice(harness_tmp):
    job = ensure_job(slug="lab", original_ask="Add dash", session_id="s3")
    ingest_report(
        job["job_id"],
        {"summary": "TASK_COMPLETE\n===ARTIFACT: map===\nok\n===END===", "role": "explorer"},
    )
    assert open_count_for_session("s3") == 1
    ingest_report(
        job["job_id"],
        {"summary": "TASK_COMPLETE\ndash added", "role": "coder"},
    )
    assert open_count_for_session("s3") == 0


def test_coder_subagent_counts_as_write():
    from daedalus.job_contract import evaluate
    row = evaluate(
        "поправи collision в играта",
        "Срезът е готов.",
        tool_calls=[{"tool": "subagent", "ok": True, "roles": ["coder"]}],
    )
    assert row.checks["wrote_files"] is True
    assert row.checks["verified"] is True
    explorer_only = evaluate(
        "поправи collision в играта",
        "Картата е готова.",
        tool_calls=[{"tool": "subagent", "ok": True, "roles": ["explorer"]}],
    )
    assert explorer_only.checks["wrote_files"] is False
    honest = evaluate(
        "напиши pause в snake",
        "Готово, but I could not complete the input wiring.",
        tool_calls=[{"tool": "write_file", "ok": True}, {"tool": "run_command", "ok": True}],
    )
    assert honest.coding_task is True
    assert honest.complete is False
    assert any("провал" in b or "признава" in b for b in honest.blockers)

    open_job = evaluate(
        "напиши pause в snake",
        "Готово.",
        tool_calls=[{"tool": "write_file", "ok": True}, {"tool": "run_command", "ok": True}],
        open_slices=2,
    )
    assert open_job.complete is False
    assert any("среза" in b for b in open_job.blockers)


def test_spawn_schema_and_hard_job_skill(harness_tmp):
    from daedalus.native_tools import NATIVE_TOOL_SPECS
    spec = next(t for t in NATIVE_TOOL_SPECS if t["name"] == "spawn_subagent")
    props = spec["input_schema"]["properties"]
    for key in ("role", "return_contexts", "context_refs", "original_ask"):
        assert key in props

    from daedalus import improvements
    improvements._SKILLS_LOADED = False
    improvements._SKILLS_CACHE.clear()
    names = improvements.select_skills(
        "трудна задача в съществуващия проект, spawn_subagent explorer then coder",
        "build",
    )
    assert "hard_jobs" in names


def test_standalone_persona_still_compact(monkeypatch):
    monkeypatch.setenv("DAEDALUS_STANDALONE", "1")
    monkeypatch.delenv("DAEDALUS_FULL_PERSONA", raising=False)
    from daedalus.identity import build_persona
    text = build_persona()
    assert "return_contexts" in text
    assert "Argos Vault — central encrypted" not in text
    assert len(text) < 12_000
    assert text.count("\n") < 200
