"""A3: the job systems feed the run ledger without changing how they run; API, SSE and restart recovery."""
from __future__ import annotations

import asyncio
import json
import types

import pytest
from a0_support import a0_tmp  # noqa: F401
from fastapi.testclient import TestClient


@pytest.fixture
def runs(a0_tmp):
    from daedalus.harness import runs

    runs.reset()
    yield runs
    runs.reset()


def run(coro):
    return asyncio.run(coro)


async def _settle(n=3):
    for _ in range(n):
        await asyncio.sleep(0)


# ---- chat ------------------------------------------------------------------------------------------------

def _fake_agent(monkeypatch, behaviour):
    class Agent:
        async def chat(self, **kw):
            return await behaviour(**kw)

    monkeypatch.setattr("daedalus.agent.get_daedalus", lambda: Agent())


def test_chat_job_is_tracked_succeeded_and_binds_its_run(runs, monkeypatch):
    from daedalus import chat_jobs

    seen = {}

    async def chat(**kw):
        seen["run"] = runs.current_run_id()  # model calls inside the job attach to its run
        return {"reply": "hi"}

    _fake_agent(monkeypatch, chat)

    async def go():
        out = await chat_jobs.start_chat(message="hello world", session_id="sess-1", effort="craft")
        await chat_jobs._jobs[out["job_id"]]["task"]
        await _settle()
        return out

    out = run(go())
    row = runs.find_by_ref(f"chat:{out['job_id']}")
    assert row["kind"] == "chat" and row["state"] == "succeeded" and row["owner_session"] == "sess-1"
    assert row["effort"] == "craft" and row["summary"] == "hello world"
    assert seen["run"] == row["id"]
    assert chat_jobs.get_job(out["job_id"])["status"] == "done"       # the job's own contract is unchanged


def test_chat_job_error_and_cancel_are_recorded_honestly(runs, monkeypatch):
    from daedalus import chat_jobs

    async def boom(**kw):
        raise RuntimeError("model exploded")

    _fake_agent(monkeypatch, boom)

    async def go_error():
        out = await chat_jobs.start_chat(message="x", session_id="s")
        await chat_jobs._jobs[out["job_id"]]["task"]
        await _settle()
        return out

    out = run(go_error())
    row = runs.find_by_ref(f"chat:{out['job_id']}")
    assert row["state"] == "failed" and "model exploded" in row["error"]

    async def slow(**kw):
        await asyncio.sleep(30)

    _fake_agent(monkeypatch, slow)

    async def go_cancel():
        out = await chat_jobs.start_chat(message="x", session_id="s")
        chat_jobs.cancel(out["job_id"])
        await asyncio.sleep(0.05)
        return out

    out = run(go_cancel())
    assert runs.find_by_ref(f"chat:{out['job_id']}")["state"] == "stopped"


# ---- autopilot / full autopilot ---------------------------------------------------------------------------

def _gen(*events):
    async def factory():
        for name, data in events:
            yield (name, data)
            await asyncio.sleep(0)
    return factory


def _drive(job):
    async def _wait():
        await job.task
        await _settle()
    return _wait()


def test_autopilot_run_follows_the_job_and_records_milestones_only(runs):
    from daedalus import autopilot_jobs as aj

    async def go():
        job = aj.start_autopilot("game-a", "make a maze", _gen(
            ("phase", {"n": 1, "label": "plan"}), ("log", {"line": "noise"}), ("file", {"path": "index.html"}),
            ("done", {"files": ["index.html"], "assets": [], "exit_signal": True,
                      "completion_indicators": ["files_written", "tests_pass", "review_ok"]})))
        await _drive(job)
        return job

    job = run(go())
    row = runs.get(job.run_id)
    assert row["kind"] == "autopilot" and row["state"] == "succeeded" and row["slug"] == "game-a"
    types_ = [e["type"] for e in runs.events(job.run_id)]
    assert "phase" in types_ and "file" in types_ and "done" in types_
    assert "log" not in types_                                         # chatty events are not mirrored
    assert [e for e in runs.events(job.run_id) if e["type"] == "file"][0]["payload"]["path"] == "index.html"


def test_a_done_the_loop_guard_refuses_is_a_stopped_run_not_a_success(runs):
    """The job's own guard (no exit signal) interrupts it; the ledger must say stopped, never succeeded."""
    from daedalus import autopilot_jobs as aj

    async def go():
        job = aj.start_autopilot("guarded", "x", _gen(("done", {"files": ["a"], "assets": []})))
        await _drive(job)
        return job

    job = run(go())
    assert job.status == "interrupted"
    row = runs.get(job.run_id)
    assert row["state"] == "stopped" and row["error"]


def test_autopilot_failure_pause_resume_and_stop(runs):
    from daedalus import autopilot_jobs as aj

    async def go_fail():
        job = aj.start_autopilot("g1", "x", _gen(("phase", {"n": 1}), ("error", {"message": "build broke"})))
        await _drive(job)
        return job

    failed = run(go_fail())
    assert runs.get(failed.run_id)["state"] == "failed" and "build broke" in runs.get(failed.run_id)["error"]

    async def go_control():
        async def never():
            yield ("phase", {"n": 1})
            await asyncio.sleep(30)
        job = aj.start_autopilot("g2", "x", never)
        await asyncio.sleep(0.05)
        assert await job.pause() is True
        paused = runs.get(job.run_id)["state"]
        assert await job.resume() is True
        resumed = runs.get(job.run_id)["state"]
        await job.mark_interrupted("owner stopped")
        job.task.cancel()
        await asyncio.sleep(0.05)
        return job, paused, resumed

    job, paused, resumed = run(go_control())
    assert (paused, resumed) == ("paused", "running")
    row = runs.get(job.run_id)
    assert row["state"] == "stopped" and "owner stopped" in row["error"]


def test_a_new_autopilot_supersedes_the_old_run_on_the_same_slug(runs):
    from daedalus import autopilot_jobs as aj

    async def go():
        async def never():
            yield ("phase", {"n": 1})
            await asyncio.sleep(30)
        first = aj.start_autopilot("same", "one", never)
        await asyncio.sleep(0.02)
        second = aj.start_autopilot("same", "two", never)
        await asyncio.sleep(0.05)
        owner = runs.owner_of("same")
        for j in (first, second):
            j.task.cancel()
        await asyncio.sleep(0.02)
        return first, second, owner

    first, second, owner = run(go())
    assert runs.get(first.run_id)["state"] == "stopped" and owner["id"] == second.run_id


def test_full_autopilot_is_labelled_full_autopilot(runs, monkeypatch):
    from daedalus import builder_runtime

    async def fake_full(backend, state, **kw):
        state.test_ok = state.review_ok = True

    monkeypatch.setattr("daedalus.kernel.full_autopilot.run_full_autopilot", fake_full)

    async def go():
        job = builder_runtime._start_full_kernel_run("fa-game", "make it")
        await _drive(job)
        return job

    job = run(go())
    assert runs.get(job.run_id)["kind"] == "full_autopilot"


# ---- guided ------------------------------------------------------------------------------------------------------

def test_guided_job_states_follow_checkpoints(runs):
    from daedalus import guided_jobs as gj

    async def go():
        job = gj.GuidedJob("gslug", "desc", {"stages": []})
        job.run_id = runs.open_run("guided", slug="gslug", ref=f"guided:{job.id}")
        await job.enter_checkpoint({"stage": 1})
        s1 = runs.get(job.run_id)["state"]
        job.status = "running"
        runs.sync_job(job, "guided")                                   # what _run_guided_step does on continue
        s2 = runs.get(job.run_id)["state"]
        await job.complete()
        return job, s1, s2

    job, s1, s2 = run(go())
    assert (s1, s2, runs.get(job.run_id)["state"]) == ("paused", "running", "succeeded")

    async def go_fail():
        j = gj.GuidedJob("g2", "d", {"stages": []})
        j.run_id = runs.open_run("guided", slug="g2")
        await j.fail("no files written")
        k = gj.GuidedJob("g3", "d", {"stages": []})
        k.run_id = runs.open_run("guided", slug="g3")
        await k.mark_interrupted("stopped by owner")
        return j, k

    j, k = run(go_fail())
    assert runs.get(j.run_id)["state"] == "failed" and "no files written" in runs.get(j.run_id)["error"]
    assert runs.get(k.run_id)["state"] == "stopped"


def test_guided_milestone_complete_path_closes_the_run(runs, monkeypatch):
    """continue_job's milestone-complete branch sets status done without starting a task: it must sync itself."""
    from daedalus import guided_jobs as gj

    async def go():
        job = gj.GuidedJob("ms", "d", {"stages": [{"id": "a", "status": "pending"}]})
        job.milestone_scope = "M1"
        job.run_id = runs.open_run("guided", slug="ms")
        await job.enter_checkpoint({"stage": 0})
        assert runs.get(job.run_id)["state"] == "paused"
        async with job.cond:
            job.status = "done"
        runs.sync_job(job, "guided")   # the exact call continue_job's branch makes
        return job

    job = run(go())
    assert runs.get(job.run_id)["state"] == "succeeded"
    src = open(gj.__file__, encoding="utf-8").read()
    branch = src.split('job.status = "done"', 2)[1]
    assert "_runs.sync_job(job, \"guided\")" in branch.split("await job.append_event", 1)[0]


def test_a_resumed_guided_job_after_restart_is_tracked_by_a_new_linked_run(runs, monkeypatch):
    from daedalus import guided_jobs as gj

    job = gj.GuidedJob("rs", "desc", {"stages": []})
    job.run_id = runs.open_run("guided", slug="rs", ref=f"guided:{job.id}")
    runs.recover_orphans()                                              # the server restarted
    seen = {}

    async def fake_project(slug):
        seen["run"] = runs.current_run_id()
        return None                                                     # makes _run_guided_step fail fast

    monkeypatch.setattr(gj.pg, "get_project", fake_project)
    run(gj._run_guided_step(job))
    assert job.run_id and runs.get(job.run_id)["parent_id"] and seen["run"] == job.run_id
    assert runs.get(job.run_id)["state"] == "failed"                    # tracked to its own end, not left untracked


def test_final_event_is_recorded_before_the_run_turns_terminal(runs):
    """So a streaming client that sees 'terminal' has already been able to read the done/error event."""
    from daedalus import autopilot_jobs as aj

    async def go():
        job = aj.start_autopilot("order", "x", _gen(("phase", {"n": 1}), ("error", {"message": "late boom"})))
        await _drive(job)
        return job

    job = run(go())
    evs = runs.events(job.run_id)
    err_seq = next(e["seq"] for e in evs if e["type"] == "error")
    failed_seq = next(e["seq"] for e in evs if e["type"] == "state" and e["payload"]["to"] == "failed")
    assert err_seq < failed_seq


def test_start_guided_opens_a_run_and_continue_reuses_it(runs, monkeypatch):
    from daedalus import guided_jobs as gj

    async def fake_step(job):
        runs.bind(job.run_id)
        runs.sync_job(job, "guided")
        await job.enter_checkpoint({"stage": 0})

    monkeypatch.setattr(gj, "_run_guided_step", fake_step)
    monkeypatch.setattr(gj, "_hydrate_job_from_workspace", lambda job: 0)

    async def go():
        job = gj.start_guided("gs", "desc", {"stages": []})
        await job.task
        await _settle()
        return job

    job = run(go())
    row = runs.get(job.run_id)
    assert row["kind"] == "guided" and row["ref"] == f"guided:{job.id}" and row["state"] == "paused"
    assert runs.owner_of("gs")["id"] == job.run_id


# ---- build -----------------------------------------------------------------------------------------------------------

def test_build_job_success_and_failure(runs):
    from daedalus import build_jobs as bj

    async def stream_ok():
        yield {"type": "token", "text": "code"}
        yield {"type": "done"}

    async def stream_err():
        yield {"type": "error", "error": "provider down"}

    async def go():
        ok = bj.start_build("b1", mode="create", mode_label="Create", route_stream_factory=stream_ok,
                            parse_files=lambda t: {"a.txt": t}, write_files=lambda s, f: None)
        bad = bj.start_build("b2", mode="create", mode_label="Create", route_stream_factory=stream_err,
                             parse_files=lambda t: {}, write_files=lambda s, f: None)
        for j in (ok, bad):
            await j.task
        await _settle()
        return ok, bad

    ok, bad = run(go())
    assert runs.get(ok.run_id)["state"] == "succeeded" and runs.get(ok.run_id)["kind"] == "build"
    assert runs.get(bad.run_id)["state"] == "failed" and "provider down" in runs.get(bad.run_id)["error"]


def test_restoring_a_persisted_job_does_not_open_a_run(runs):
    """Rebuilding a read-only job from storage (after a restart) must not create ledger rows."""
    from daedalus import build_jobs as bj

    before = len(runs.list_runs())
    job = bj._restore({"snapshot": {"slug": "x", "id": "old", "status": "running"}, "text": ""})
    assert job.run_id == "" and len(runs.list_runs()) == before


# ---- API and SSE ---------------------------------------------------------------------------------------------------------

def _stream(client, url, headers=None, **kw):
    """client.stream bypasses the conftest token injection, so send the owner token explicitly."""
    import os

    hdrs = {"Authorization": "Bearer " + os.environ["DAEDALUS_TOKEN"], **(headers or {})}
    out = []
    with client.stream("GET", url, headers=hdrs, **kw) as resp:
        assert resp.status_code == 200 and resp.headers["content-type"].startswith("text/event-stream")
        block = {}
        for line in resp.iter_lines():
            if line == "":
                if block:
                    out.append(block)
                block = {}
            elif line.startswith(":"):
                continue
            else:
                k, _, v = line.partition(": ")
                block[k] = v
    return out


def test_runs_api_list_get_and_404(runs):
    from app.main import app

    client = TestClient(app)
    a = runs.open_run("chat", slug="s1")
    b = runs.open_run("autopilot", slug="s2")
    runs.set_state(a, "succeeded")
    body = client.get("/api/v1/harness/runs").json()
    assert body["ok"] and {r["id"] for r in body["runs"]} >= {a, b}
    assert [r["id"] for r in client.get("/api/v1/harness/runs?live=true&slug=s2").json()["runs"]] == [b]
    assert [r["id"] for r in client.get("/api/v1/harness/runs?kind=chat").json()["runs"]] == [a]
    one = client.get(f"/api/v1/harness/runs/{a}").json()
    assert one["run"]["state"] == "succeeded" and one["events"][-1]["type"] == "state"
    assert client.get("/api/v1/harness/runs/nope").status_code == 404
    assert client.get("/api/v1/harness/runs/nope/events").status_code == 404


def test_sse_replays_then_ends_and_resumes_by_seq(runs):
    from app.main import app

    client = TestClient(app)
    rid = runs.open_run("autopilot", slug="s")
    for n in range(3):
        runs.event(rid, "phase", {"n": n})
    runs.set_state(rid, "succeeded", reason="done")
    blocks = _stream(client, f"/api/v1/harness/runs/{rid}/events")
    seqs = [int(b["id"]) for b in blocks if "id" in b]
    assert seqs == list(range(1, 6)) and blocks[-1]["event"] == "end"
    assert json.loads(blocks[-1]["data"]) == {"state": "succeeded", "last_seq": 5}

    resumed = _stream(client, f"/api/v1/harness/runs/{rid}/events?after=3")
    assert [int(b["id"]) for b in resumed if "id" in b] == [4, 5]
    via_header = _stream(client, f"/api/v1/harness/runs/{rid}/events", headers={"Last-Event-ID": "4"})
    assert [int(b["id"]) for b in via_header if "id" in b] == [5]
    beyond = _stream(client, f"/api/v1/harness/runs/{rid}/events?after=99")
    assert [b["event"] for b in beyond] == ["end"]


def test_sse_follows_a_live_run_until_it_finishes(runs):
    from app.main import app

    client = TestClient(app)
    rid = runs.open_run("autopilot", slug="live")
    import threading
    import time

    def later():
        time.sleep(0.4)
        runs.event(rid, "phase", {"n": 7})
        time.sleep(0.2)
        runs.set_state(rid, "failed", error="late failure")

    t = threading.Thread(target=later)
    t.start()
    blocks = _stream(client, f"/api/v1/harness/runs/{rid}/events?poll=0.1")
    t.join()
    kinds = [b["event"] for b in blocks]
    assert "phase" in kinds and kinds[-1] == "end" and json.loads(blocks[-1]["data"])["state"] == "failed"


def test_sse_max_seconds_bounds_a_live_stream(runs):
    from app.main import app

    rid = runs.open_run("autopilot", slug="bounded")
    blocks = _stream(TestClient(app), f"/api/v1/harness/runs/{rid}/events?poll=0.1&max_seconds=0.3")
    end = json.loads(blocks[-1]["data"])
    assert blocks[-1]["event"] == "end" and end["timeout"] is True and end["state"] == "running"


def test_runs_endpoints_need_the_owner_token(runs, raw_http):
    from app.main import app

    client = TestClient(app)
    for path in ("/api/v1/harness/runs", "/api/v1/harness/runs/x", "/api/v1/harness/runs/x/events"):
        assert client.get(path).status_code == 401
    with client.stream("GET", "/api/v1/harness/runs/x/events") as resp:  # the stream path is gated too
        assert resp.status_code == 401


# ---- restart ----------------------------------------------------------------------------------------------------------------

def test_startup_marks_leftover_live_runs_unknown(runs):
    """A real app start (lifespan) after a 'crash': live runs become unknown, finished ones are untouched."""
    from app.main import app

    live = runs.open_run("autopilot", slug="crashed")
    paused = runs.open_run("guided", slug="g")
    runs.set_state(paused, "paused")
    done = runs.open_run("chat")
    runs.set_state(done, "succeeded")
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
        assert runs.get(live)["state"] == "unknown" and runs.get(paused)["state"] == "unknown"
        assert runs.get(done)["state"] == "succeeded"
        assert "restarted" in runs.get(live)["error"]
        listed = client.get("/api/v1/harness/runs?state=unknown").json()["runs"]
        assert {r["id"] for r in listed} >= {live, paused}
