"""Regression: sync work must not block the asyncio loop in async handlers / run_cycle.

Scope: concurrency hardening only. Does NOT claim to fix Windows 12s health timeouts
(multi-process / client topology unrelated).
"""
from __future__ import annotations

import asyncio
import threading
import time

import httpx
import pytest
from httpx import ASGITransport


@pytest.fixture
def conc_tmp(tmp_path, monkeypatch):
    monkeypatch.setenv("DAEDALUS_STANDALONE", "1")
    monkeypatch.setenv("DAEDALUS_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("DAEDALUS_PLAYGROUND", str(tmp_path / "projects"))
    monkeypatch.delenv("DAEDALUS_USE_ADN_DB", raising=False)
    monkeypatch.delenv("DAEDALUS_USE_MESH_ROUTER", raising=False)
    from daedalus.paths import data_root, playground_projects_dir, product_root, repo_root
    data_root.cache_clear()
    playground_projects_dir.cache_clear()
    product_root.cache_clear()
    repo_root.cache_clear()
    from daedalus.store import reset_sqlite_pool
    reset_sqlite_pool()
    yield tmp_path
    reset_sqlite_pool()


def test_health_allows_concurrent_public_route_during_slow_llm_snapshot(conc_tmp, monkeypatch):
    """While /health waits on thread-pool LLM resolution, another request must still complete."""

    async def _run():
        import app.main as main_mod

        entered = threading.Event()
        barrier = threading.Event()

        def slow_snapshot():
            entered.set()
            barrier.wait(timeout=5.0)
            return {
                "provider": "p",
                "model": "m",
                "has_key": True,
                "brain_provider": "bp",
                "brain_model": "bm",
            }

        monkeypatch.setattr(main_mod, "_health_llm_snapshot", slow_snapshot)

        transport = ASGITransport(app=main_mod.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            health_task = asyncio.create_task(client.get("/health"))
            # Wait until slow snapshot runs on worker thread (not for health response).
            deadline = time.time() + 2.0
            while not entered.is_set() and time.time() < deadline:
                await asyncio.sleep(0.01)
            assert entered.is_set(), "slow _health_llm_snapshot never started"

            t0 = time.perf_counter()
            cap = await client.get("/capabilities")
            cap_ms = (time.perf_counter() - t0) * 1000.0
            assert cap.status_code == 200
            assert cap_ms < 800.0, f"/capabilities should respond during LLM snapshot stall, took {cap_ms:.0f}ms"

            barrier.set()
            health = await asyncio.wait_for(health_task, timeout=5.0)
        assert health.status_code == 200
        body = health.json()
        assert body.get("ok") is True
        assert body.get("llm", {}).get("provider") == "p"

    asyncio.run(_run())


def test_evolution_run_cycle_heartbeat_during_slow_study(conc_tmp):
    """study_fn runs in a thread; event loop must stay responsive (heartbeat gaps stay small)."""
    from daedalus.evolution import run_cycle, set_enabled

    async def _run():
        set_enabled(True)

        async def fake_radar(agent, source_ids=None, *, for_self=False):
            return {
                "ok": True,
                "sources": [{"urls": ["https://github.com/harness/stub-repo"]}],
            }

        def slow_study(url: str, **kwargs):
            time.sleep(0.35)
            return {"ok": False, "error": "stub", "url": url}

        ticks: list[float] = []

        async def tick():
            while True:
                ticks.append(time.perf_counter())
                await asyncio.sleep(0.05)

        tick_task = asyncio.create_task(tick())
        await asyncio.sleep(0.06)

        await run_cycle(force=True, radar_fn=fake_radar, study_fn=slow_study, daedalus=None)

        tick_task.cancel()
        try:
            await tick_task
        except asyncio.CancelledError:
            pass

        gaps = [ticks[i] - ticks[i - 1] for i in range(1, len(ticks))]
        assert gaps, "no heartbeat ticks collected"
        assert max(gaps) < 0.15, (
            "event loop appears blocked during threaded study (heartbeat gap too large)"
        )

    asyncio.run(_run())
    from daedalus.evolution import set_enabled

    set_enabled(False)
