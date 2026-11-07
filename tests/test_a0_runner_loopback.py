"""A0-j: run apps bind to loopback by default; the idle reaper is wired into the app lifespan."""
from __future__ import annotations

import asyncio
import time

import pytest
from a0_support import a0_tmp  # noqa: F401


@pytest.fixture
def project(a0_tmp):
    from daedalus import playground as pg

    def make(kind):
        proj = asyncio.run(pg.create_project(f"rt-{kind}", kind=kind))
        return proj

    return make


@pytest.mark.parametrize("kind", ["fastapi", "streamlit", "vite"])
def test_commands_bind_loopback_by_default(project, monkeypatch, kind):
    from daedalus.playground_runtime import PlaygroundRuntime

    monkeypatch.delenv("DAEDALUS_RUNNER_HOST", raising=False)
    cmd = PlaygroundRuntime.resolve_cmd(project(kind), 5555)
    assert "127.0.0.1" in cmd and "0.0.0.0" not in cmd


def test_runner_host_opt_in_and_validation(monkeypatch):
    from daedalus.playground_runtime import runner_host

    monkeypatch.setenv("DAEDALUS_RUNNER_HOST", "0.0.0.0")
    assert runner_host() == "0.0.0.0"
    monkeypatch.setenv("DAEDALUS_RUNNER_HOST", "evil; rm -rf /")
    assert runner_host() == "127.0.0.1"
    monkeypatch.setenv("DAEDALUS_RUNNER_HOST", "")
    assert runner_host() == "127.0.0.1"


def test_reap_idle_stops_only_idle_apps(monkeypatch):
    from daedalus import playground_runner as pr

    stopped = []

    async def fake_stop(slug):
        stopped.append(slug)
        pr._RUNNING.pop(slug, None)

    now = time.time()
    monkeypatch.setattr(pr, "stop", fake_stop)
    monkeypatch.setattr(pr, "_RUNNING", {
        "idle": {"last_seen": now - pr.IDLE_KILL_SEC - 5},
        "busy": {"last_seen": now},
    })
    assert asyncio.run(pr.reap_idle()) == 1
    assert stopped == ["idle"]


def test_idle_reaper_task_runs_and_cancels(monkeypatch):
    from daedalus import playground_runner as pr

    ticks = []

    async def fake_reap():
        ticks.append(1)
        return 0

    monkeypatch.setattr(pr, "reap_idle", fake_reap)

    async def go():
        task = pr.start_idle_reaper(interval=0.01)
        await asyncio.sleep(0.08)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        return len(ticks)

    assert asyncio.run(go()) >= 2


def test_lifespan_starts_and_cancels_the_reaper(a0_tmp, monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import app
    from daedalus import playground_runner as pr

    state = {"started": 0, "cancelled": False}

    class FakeTask:
        def cancel(self):
            state["cancelled"] = True

        def __await__(self):
            async def _w():
                raise asyncio.CancelledError()
            return _w().__await__()

    def fake_start(interval=None):
        state["started"] += 1
        return FakeTask()

    monkeypatch.setattr(pr, "start_idle_reaper", fake_start)
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
        assert state["started"] == 1
    assert state["cancelled"] is True
