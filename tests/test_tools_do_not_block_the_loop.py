"""Slow native tools run off the event loop.

Found live (2026-11-11): during one app build the server stopped answering for 180 s twice: run_command ran
`npm install` with a blocking subprocess inside the async tool dispatcher, so every Studio request waited for it."""
from __future__ import annotations

import asyncio
import time

import pytest

from daedalus import native_tools


def _slow(*a, **k):
    time.sleep(0.6)
    return {"ok": True, "stdout": "done", "stderr": "", "ran": "x"}


@pytest.mark.parametrize("tool, target, args", [
    ("run_command", "daedalus.shell_center.run_command", {"command": "npm install"}),
    ("world", "daedalus.world_runtime.run_action", {"action": "status", "slug": "x"}),
    ("assets", "daedalus.asset_pipeline.run_action", {"action": "status", "slug": "x"}),
    ("production", "daedalus.production_layer.run_action", {"action": "status", "slug": "x"}),
    ("play_lab", "daedalus.play_lab.probe_host", {"action": "status"}),
])
def test_a_slow_tool_leaves_the_loop_free(monkeypatch, tool, target, args):
    monkeypatch.setattr(target, _slow)
    monkeypatch.setattr(native_tools, "before_tool_call", lambda *a, **k: (True, ""))
    ticks = []

    async def ticker():
        while True:
            ticks.append(time.monotonic())
            await asyncio.sleep(0.05)

    async def go():
        t = asyncio.create_task(ticker())
        await asyncio.sleep(0)
        out = await native_tools.execute_native_tool(tool, args, memory=None, session_id="s",
                                                     read_file_fn=lambda *a, **k: {})
        t.cancel()
        return out

    out = asyncio.run(go())
    assert out                                     # the tool ran and answered
    assert len(ticks) >= 6, f"{tool}: the event loop was blocked ({len(ticks)} ticks in 0.6 s)"
