"""Autopilot fixes what gameplay, the critic and the playtest find, instead of stopping at the first failed check.

Found live (2026-11-11): Lantern Maze built and ran, failed the gameplay check and the critic, and the run ended in
error with no attempt to fix it. Now those findings go back to the coder and the game is verified again (bounded)."""
from __future__ import annotations

import asyncio

import pytest

from daedalus.kernel import full_autopilot as fa
from daedalus.kernel.orchestrator import PipelineState


class Backend:
    def __init__(self):
        self.prompts, self.written, self.events = [], [], []

    async def emit(self, event, data):
        self.events.append((event, data))

    async def llm(self, role, prompt, *, system=""):
        self.prompts.append(prompt)
        if "строителен план" in prompt:
            return '{"title": "Maze", "stages": [{"id": 1, "title": "MVP", "goal": "game", "files": ["index.html"]}]}'
        if "Поправи ги в кода" in prompt:
            return '{"files": {"index.html": "<canvas></canvas><script>start on Enter</script>"}}'
        if prompt.startswith("Review build"):
            return "APPROVED"
        return '{"files": {"index.html": "<canvas></canvas><script>v1</script>"}}'

    async def write_changes(self, changes):
        self.written.extend(c.path for c in changes)
        return len(changes)

    async def smoke_test(self):
        return {"ok": True}

    async def preview_url(self):
        return "http://preview/maze/"

    async def project_kind(self):
        return "static"

    async def browser_verify(self):
        return {"ok": True}


@pytest.fixture
def checks(monkeypatch):
    calls = {"gameplay": 0}

    async def gameplay(slug, kind, **k):
        calls["gameplay"] += 1
        if calls["gameplay"] == 1:
            return {"ok": False, "summary": "Enter does not start the game"}
        return {"ok": True}

    async def critic(**k):
        return {"na": True}

    async def playtest(slug, **k):
        return {"ship_ready": True, "summary": "ok", "blockers": []}

    monkeypatch.setattr("daedalus.engine_runners.gameplay_for_kind", gameplay)
    monkeypatch.setattr("daedalus.quality_gate.maybe_critic", critic)
    monkeypatch.setattr("daedalus.playtest.run_playtest", playtest)
    return calls


def test_a_failed_gameplay_check_is_fixed_and_verified_again(checks):
    be = Backend()
    state = asyncio.run(fa.run_full_autopilot(be, PipelineState(slug="maze", description="maze, start on Enter")))
    assert checks["gameplay"] == 2
    fix = [p for p in be.prompts if "Поправи ги в кода" in p]
    assert len(fix) == 1 and "Enter does not start the game" in fix[0] and "===FILE: index.html===" in fix[0]
    assert state.review_ok is True and not state.errors
    assert any(e == "done" for e, _ in be.events)


def test_repair_rounds_are_bounded(checks, monkeypatch):
    async def always_fail(slug, kind, **k):
        checks["gameplay"] += 1
        return {"ok": False, "summary": "still broken"}

    monkeypatch.setattr("daedalus.engine_runners.gameplay_for_kind", always_fail)
    be = Backend()
    state = asyncio.run(fa.run_full_autopilot(be, PipelineState(slug="maze", description="maze")))
    assert checks["gameplay"] == fa.QUALITY_REPAIR_ROUNDS + 1
    assert sum("Поправи ги в кода" in p for p in be.prompts) == fa.QUALITY_REPAIR_ROUNDS
    assert state.review_ok is False and "still broken" in "; ".join(state.errors)
    assert any(e == "error" for e, _ in be.events)


def test_no_repair_when_turned_off(checks, monkeypatch):
    monkeypatch.setenv("DAEDALUS_QUALITY_REPAIR_ROUNDS", "0")
    be = Backend()
    asyncio.run(fa.run_full_autopilot(be, PipelineState(slug="maze", description="maze")))
    assert checks["gameplay"] == 1 and not any("Поправи ги в кода" in p for p in be.prompts)
