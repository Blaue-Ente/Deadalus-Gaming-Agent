"""A3: the run ledger core - states, events, ownership, orphan recovery, gateway trace."""
from __future__ import annotations

import asyncio

import pytest
from a0_support import a0_tmp  # noqa: F401


@pytest.fixture
def runs(a0_tmp):
    from daedalus.harness import runs

    runs.reset()
    yield runs
    runs.reset()


def test_open_run_records_state_event_and_budget_snapshot(runs):
    rid = runs.open_run("chat", slug="s1", ref="chat:abc", summary="hello", effort="craft", owner_session="sess")
    row = runs.get(rid)
    assert row["kind"] == "chat" and row["state"] == "running" and row["live"] is True
    assert (row["slug"], row["ref"], row["owner_session"], row["effort"]) == ("s1", "chat:abc", "sess", "craft")
    ev = runs.events(rid)
    assert [e["seq"] for e in ev] == [1] and ev[0]["type"] == "state" and ev[0]["payload"]["to"] == "running"
    assert runs.find_by_ref("chat:abc")["id"] == rid


def test_unknown_kind_and_state_are_rejected(runs):
    with pytest.raises(runs.RunError):
        runs.open_run("nonsense")
    rid = runs.open_run("chat")
    with pytest.raises(runs.RunError):
        runs.set_state(rid, "flying")
    with pytest.raises(runs.RunError):
        runs.set_state("missing", "stopped")
    with pytest.raises(runs.RunError):
        runs.set_state(rid, "queued")  # no way back to queued


def test_state_machine_pause_resume_and_terminal_is_final(runs):
    rid = runs.open_run("autopilot", slug="s")
    assert runs.set_state(rid, "paused", reason="checkpoint") is True
    assert runs.set_state(rid, "paused") is False                    # no-op
    assert runs.set_state(rid, "running") is True
    assert runs.set_state(rid, "failed", error="boom") is True
    assert runs.get(rid)["error"] == "boom" and runs.get(rid)["live"] is False
    # a finished run is never reopened or re-labelled: no late "succeeded"
    assert runs.set_state(rid, "succeeded") is False and runs.set_state(rid, "running") is False
    assert runs.get(rid)["state"] == "failed"
    states = [e["payload"]["to"] for e in runs.events(rid) if e["type"] == "state"]
    assert states == ["running", "paused", "running", "failed"]


def test_events_have_increasing_seq_resume_and_cap(runs, monkeypatch):
    rid = runs.open_run("build", slug="s")
    for i in range(5):
        assert runs.event(rid, "phase", {"n": i}) == i + 2
    assert [e["seq"] for e in runs.events(rid, after=3)] == [4, 5, 6]
    assert runs.events(rid, after=6) == []
    monkeypatch.setattr(runs, "MAX_EVENTS_PER_RUN", 8)
    assert runs.event(rid, "phase", {"n": 9}) == 7 and runs.event(rid, "phase", {"n": 10}) == 8
    assert runs.event(rid, "phase", {"n": 11}) == 0                  # cap reached: dropped, never raises
    runs.set_state(rid, "stopped")                                    # state changes are always recorded
    assert runs.events(rid)[-1]["type"] == "state"


def test_large_payload_is_truncated_not_stored_whole(runs):
    rid = runs.open_run("chat")
    runs.event(rid, "big", {"text": "x" * 50_000})
    payload = runs.events(rid)[-1]["payload"]
    assert payload["truncated"] is True and len(str(payload)) < 4200


def test_one_owner_per_slug_for_build_kinds(runs):
    a = runs.open_run("autopilot", slug="game")
    assert runs.owner_of("game")["id"] == a
    chat = runs.open_run("chat", slug="game")                         # chat is not build-type: no effect
    assert runs.get(a)["state"] == "running"
    other = runs.open_run("autopilot", slug="other")                  # other slug untouched
    b = runs.open_run("guided", slug="game")                          # a newer build run takes the slug
    assert runs.get(a)["state"] == "stopped"
    assert any(e["payload"].get("reason") == f"superseded by {b}" for e in runs.events(a))
    assert runs.owner_of("game")["id"] == b and runs.get(chat)["state"] == "running"
    assert runs.get(other)["state"] == "running"
    assert runs.owner_of("nothing") is None


def test_recover_orphans_marks_live_runs_unknown_never_success(runs):
    live = [runs.open_run("autopilot", slug=f"s{i}") for i in range(2)]
    paused = runs.open_run("guided", slug="g")
    runs.set_state(paused, "paused")
    done = runs.open_run("chat")
    runs.set_state(done, "succeeded")
    failed = runs.open_run("chat")
    runs.set_state(failed, "failed")
    assert runs.recover_orphans() == 3
    for rid in live + [paused]:
        row = runs.get(rid)
        assert row["state"] == "unknown" and "restarted" in row["error"] and row["live"] is False
    assert runs.get(done)["state"] == "succeeded" and runs.get(failed)["state"] == "failed"
    assert runs.recover_orphans() == 0                                 # idempotent


def test_orphans_survive_a_real_connection_restart(runs):
    """The ledger is on disk: closing the connection (a process restart) keeps the runs."""
    rid = runs.open_run("autopilot", slug="s")
    runs.event(rid, "phase", {"n": 1})
    runs.reset()
    assert runs.get(rid)["state"] == "running" and len(runs.events(rid)) == 2
    assert runs.recover_orphans() == 1 and runs.get(rid)["state"] == "unknown"


def test_list_filters(runs):
    a = runs.open_run("chat", slug="x")
    b = runs.open_run("autopilot", slug="y")
    runs.set_state(a, "succeeded")
    assert {r["id"] for r in runs.list_runs()} == {a, b}
    assert [r["id"] for r in runs.list_runs(slug="y")] == [b]
    assert [r["id"] for r in runs.list_runs(kind="chat")] == [a]
    assert [r["id"] for r in runs.list_runs(state="succeeded")] == [a]
    assert [r["id"] for r in runs.list_runs(live_only=True)] == [b]
    assert len(runs.list_runs(limit=1)) == 1


def test_proof_level_only_goes_up(runs):
    rid = runs.open_run("verify", slug="s")
    runs.set_proof_level(rid, 2)
    runs.set_proof_level(rid, 1)
    assert runs.get(rid)["proof_level"] == 2
    runs.set_proof_level(rid, 4)
    assert runs.get(rid)["proof_level"] == 4


def test_safe_hooks_never_raise(runs, monkeypatch):
    assert runs.safe_open("nonsense") == ""                            # bad kind -> '' not an exception
    runs.safe_event("", "x")                                           # no run: ignored
    runs.safe_state("missing", "stopped")                              # unknown run: ignored
    monkeypatch.setattr(runs, "_conn", lambda: (_ for _ in ()).throw(sqlite_error()))
    assert runs.safe_open("chat") == ""


def sqlite_error():
    import sqlite3
    return sqlite3.OperationalError("database is locked")


def test_sync_job_maps_statuses_and_pause(runs):
    class Job:
        status = "running"
        paused = False
        error = None
        run_id = ""

    job = Job()
    job.run_id = runs.open_run("autopilot", slug="s")
    job.paused = True
    runs.sync_job(job, "autopilot")
    assert runs.get(job.run_id)["state"] == "paused"
    job.paused = False
    runs.sync_job(job, "autopilot")
    assert runs.get(job.run_id)["state"] == "running"
    job.status, job.error = "error", "boom"
    runs.sync_job(job, "autopilot")
    assert runs.get(job.run_id)["state"] == "failed" and runs.get(job.run_id)["error"] == "boom"
    runs.sync_job(job, "autopilot")                                    # idempotent
    g = Job()
    g.run_id = runs.open_run("guided", slug="g")
    g.status = "checkpoint"
    runs.sync_job(g, "guided")
    assert runs.get(g.run_id)["state"] == "paused"


def test_on_task_done_reconciles_a_job_that_ended_without_a_terminal_status(runs):
    class Job:
        status = "running"
        error = None
        run_id = ""

    async def go(kind):
        job = Job()
        job.run_id = runs.open_run("chat", slug="")
        if kind == "cancel":
            async def body():
                await asyncio.sleep(10)
        elif kind == "crash":
            async def body():
                raise RuntimeError("kaboom")
        else:
            async def body():
                job.status = "done"
        task = asyncio.create_task(body())
        task.add_done_callback(runs.on_task_done(job, "chat"))
        if kind == "cancel":
            await asyncio.sleep(0)
            task.cancel()
        try:
            await task
        except BaseException:
            pass
        await asyncio.sleep(0)
        return runs.get(job.run_id)

    assert asyncio.run(go("cancel"))["state"] == "stopped"
    crashed = asyncio.run(go("crash"))
    assert crashed["state"] == "failed" and "kaboom" in crashed["error"]
    assert asyncio.run(go("ok"))["state"] == "succeeded"


# ---- gateway trace link ----------------------------------------------------------------------------------------

def test_gateway_events_attach_to_the_current_run(runs, monkeypatch):
    import sys
    import types

    from daedalus.harness import gateway

    monkeypatch.setattr("daedalus.llm_config.get_config", lambda: {
        "provider": "openrouter", "model": "m/x", "api_key": "k", "base_url": "http://x", "sdk": "openai"})

    class Client:
        def __init__(self, **kw):
            self.chat = types.SimpleNamespace(completions=types.SimpleNamespace(create=self.create))

        async def create(self, **kw):
            msg = types.SimpleNamespace(content="hi")
            return types.SimpleNamespace(choices=[types.SimpleNamespace(message=msg)], usage=None)

    monkeypatch.setitem(sys.modules, "openai", types.SimpleNamespace(AsyncOpenAI=Client))
    runs.install()
    rid = runs.open_run("chat", slug="s")

    async def go():
        token = runs.bind(rid)
        try:
            return await gateway.call("persona", [{"role": "user", "content": "SECRET PROMPT"}])
        finally:
            runs.unbind(token)

    try:
        assert asyncio.run(go()).ok
    finally:
        gateway.remove_trace_sink(runs._gateway_sink)
    llm = [e for e in runs.events(rid) if e["type"] == "llm"]
    assert len(llm) == 1 and llm[0]["payload"]["model"] == "m/x" and llm[0]["payload"]["ok"] is True
    assert "SECRET PROMPT" not in repr(llm)                            # content is never recorded
    assert runs.current_run_id() == ""                                 # unbound again
