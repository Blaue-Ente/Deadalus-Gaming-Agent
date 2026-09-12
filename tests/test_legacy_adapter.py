"""Leaf adapter around Daedalus.chat. No agent-loop rewrite."""
from __future__ import annotations

import ast
import asyncio
import os
import subprocess
import sys
from pathlib import Path

import pytest

from daedalus.contracts import EventKind, RunStage, RunStatus, TaskAction
from daedalus.legacy_adapter import (
    LegacyTurnRequest,
    adapter_enabled,
    run_turn,
    unwrap_chat,
)
from daedalus.stage_machine import peek_stage
from tests.characterization_fakes import (
    CHAT_RETURN_KEYS,
    PLAYGROUND_SID,
    ScriptedClaude,
    bare_daedalus,
    drain_background,
    isolate_paths,
    stub_navigate_build,
    stub_navigate_chat,
    stub_native_pass,
    stub_tools_quiet,
)

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def char_env(tmp_path, monkeypatch):
    isolate_paths(tmp_path, monkeypatch)
    monkeypatch.setattr("daedalus.navigator.navigate", stub_navigate_build)
    monkeypatch.setattr("daedalus.agent.run_native_discovery_pass", stub_native_pass)
    monkeypatch.setattr("daedalus.agent._call_claude_with_tools", stub_tools_quiet)
    monkeypatch.delenv("DAEDALUS_LEGACY_ADAPTER", raising=False)
    yield tmp_path


def _patch_claude(monkeypatch, script: ScriptedClaude) -> ScriptedClaude:
    monkeypatch.setattr("daedalus.agent._call_claude", script)
    return script


def _kind(ev) -> str:
    return ev.event.value if hasattr(ev.event, "value") else str(ev.event)


def _chat_payload(**overrides: object) -> dict:
    body: dict = {
        "agent": "Daedalus",
        "session_id": "chat-fake",
        "for_self": False,
        "reply": "ok",
        "context": {"tool_calls": [], "navigator": {"intent": "chat"}},
        "pending_proposal": None,
        "task_events": [],
        "model": "test-model",
        "effort": {
            "tier": "whisper",
            "label": "whisper",
            "role": "",
            "budget": "",
            "note": "",
            "world_kit": False,
            "source": "",
            "model": "",
            "model_source": "",
        },
        "elapsed_seconds": 0.1,
        "timestamp": "2026-09-11T00:00:00+00:00",
        "complete": None,
        "dod": None,
        "run_budget": {"usd": 0, "active": False, "spend": {"cap_usd": 0, "spent_usd": 0.0}},
    }
    body.update(overrides)
    return body


class FakeAgent:
    def __init__(self, payload: dict | None = None, exc: BaseException | None = None) -> None:
        self.payload = payload or _chat_payload()
        self.exc = exc
        self.calls: list[dict] = []

    async def chat(self, **kwargs):
        self.calls.append(dict(kwargs))
        if self.exc is not None:
            raise self.exc
        out = dict(self.payload)
        if kwargs.get("session_id"):
            out["session_id"] = kwargs["session_id"]
        else:
            out.setdefault("session_id", "chat-fake")
        return out


async def _turn(agent, **kwargs):
    try:
        return await run_turn(LegacyTurnRequest(**kwargs), agent=agent)
    finally:
        await drain_background(agent)


def test_run_turn_passthrough_chat_keys(char_env, monkeypatch):
    agent = bare_daedalus()
    _patch_claude(monkeypatch, ScriptedClaude(["shape-ok"]))
    result = asyncio.run(_turn(agent, message="ping", session_id=PLAYGROUND_SID))
    for key in CHAT_RETURN_KEYS:
        assert key in result.chat, f"missing {key}"
    assert result.chat["agent"] == "Daedalus"
    assert "shape-ok" in result.chat["reply"]
    assert unwrap_chat(result) is result.chat
    assert result.chat["session_id"] == PLAYGROUND_SID


def test_empty_message_raises_bad_request(char_env):
    from daedalus.agent import DaedalusBadRequest

    agent = bare_daedalus()
    with pytest.raises(DaedalusBadRequest, match="message cannot be empty"):
        asyncio.run(_turn(agent, message=""))
    with pytest.raises(DaedalusBadRequest, match="message cannot be empty"):
        asyncio.run(_turn(agent, message="   \n"))


def test_injected_agent_skips_singleton(char_env, monkeypatch):
    monkeypatch.setattr(
        "daedalus.agent.get_daedalus",
        lambda: (_ for _ in ()).throw(AssertionError("singleton")),
    )
    fake = FakeAgent(_chat_payload(reply="from-fake"))
    result = asyncio.run(run_turn(LegacyTurnRequest(message="ping", session_id="s1"), agent=fake))
    assert fake.calls and fake.calls[0]["message"] == "ping"
    assert result.chat["reply"] == "from-fake"
    assert result.state.run.session_id == "s1"
    assert result.state.run.original_ask == "ping"
    assert result.state.run.effort is not None
    assert result.state.run.effort.value == "whisper"


def test_cheap_path_maps_to_intake(char_env, monkeypatch):
    async def _cheap_complete(**_k):
        return {"reply": "cheap-hello"}

    monkeypatch.setattr("daedalus.llm_config.persona_via_master_enabled", lambda: False)
    monkeypatch.setattr("daedalus.llm_config.is_nvidia_free_only", lambda: False)
    monkeypatch.setattr("daedalus.navigator.navigate", stub_navigate_chat)
    monkeypatch.setattr("daedalus.mode.get_mode", lambda: "auto")
    monkeypatch.setattr("daedalus.daedalus_mesh.use_mesh_router", lambda: True)
    monkeypatch.setattr("daedalus.daedalus_mesh.complete", _cheap_complete)
    agent = bare_daedalus()
    script = _patch_claude(monkeypatch, ScriptedClaude(["full-path-should-not-run"]))
    result = asyncio.run(_turn(agent, message="hello there friend", session_id="chat-plain"))
    assert script.calls == []
    assert result.chat["reply"] == "cheap-hello"
    assert result.chat["context"].get("routed_to") in {"mesh_router_chat", "chat_fast_path"}
    assert result.stage is RunStage.INTAKE
    assert result.state.run.status is RunStatus.IN_PROGRESS
    assert [_kind(ev) for ev in result.events].count("tool") == 0
    assert _kind(result.events[0]) == EventKind.STATUS.value
    assert peek_stage(result.events) is RunStage.INTAKE


def test_tool_row_emits_tool_event_and_implement(char_env, monkeypatch):
    agent = bare_daedalus()
    script = _patch_claude(
        monkeypatch,
        ScriptedClaude(
            [
                "<NEED_READ_FILE: tests/conftest.py>",
                "File looks fine.",
            ]
        ),
    )
    result = asyncio.run(
        _turn(agent, message="please read tests/conftest.py", session_id=PLAYGROUND_SID)
    )
    assert "File looks fine." in result.chat["reply"]
    reads = [c for c in result.chat["context"]["tool_calls"] if c.get("tool") == "read_file"]
    assert reads
    assert result.stage is RunStage.IMPLEMENT
    tools = [ev for ev in result.events if _kind(ev) == EventKind.TOOL.value]
    read_ev = next((ev for ev in tools if ev.data.get("tool") == "read_file"), None)
    assert read_ev is not None
    if "ok" in reads[0]:
        assert read_ev.data.get("ok") == reads[0]["ok"]
    else:
        assert "ok" not in read_ev.data
    assert peek_stage(result.events) is RunStage.IMPLEMENT
    assert len(script.calls) >= 2


def test_complete_coding_maps_to_review_not_accepted(char_env):
    fake = FakeAgent(
        _chat_payload(
            complete=True,
            dod={
                "coding_task": True,
                "complete": True,
                "checks": {"wrote_files": True},
                "blockers": [],
                "summary": "",
            },
            context={
                "tool_calls": [{"tool": "write_file", "ok": True}],
                "navigator": {"intent": "code_change"},
                "build_kind": "static",
            },
        )
    )
    result = asyncio.run(run_turn(LegacyTurnRequest(message="ship it", session_id="s"), agent=fake))
    assert result.stage is RunStage.REVIEW
    assert result.stage is not RunStage.ACCEPTED
    assert result.state.run.status is RunStatus.IN_PROGRESS
    assert result.state.run.complete is True
    kinds = [_kind(ev) for ev in result.events]
    assert EventKind.DONE.value in kinds
    assert EventKind.TOOL.value in kinds
    assert peek_stage(result.events) is RunStage.REVIEW


def test_task_events_emit_task(char_env):
    fake = FakeAgent(
        _chat_payload(
            task_events=[{"action": "done", "ok": True, "task": {"id": "t1", "title": "Pause"}}],
        )
    )
    result = asyncio.run(run_turn(LegacyTurnRequest(message="close it", session_id="s"), agent=fake))
    tasks = [ev for ev in result.events if _kind(ev) == EventKind.TASK.value]
    assert tasks
    assert tasks[0].data.get("action") == TaskAction.DONE.value
    assert tasks[0].data.get("ok") is True
    assert (tasks[0].data.get("task") or {}).get("id") == "t1"


def test_hard_block_maps_to_blocked(char_env):
    fake = FakeAgent(
        _chat_payload(
            reply="TASK_BLOCKED: need owner key",
            complete=False,
            dod={"coding_task": True, "complete": False, "blockers": ["x"], "summary": "TASK_BLOCKED"},
            context={"tool_calls": [{"tool": "write_file", "ok": False}], "navigator": {"intent": "code_change"}},
        )
    )
    result = asyncio.run(run_turn(LegacyTurnRequest(message="build", session_id="s"), agent=fake))
    assert result.stage is RunStage.BLOCKED
    assert result.state.run.status is RunStatus.BLOCKED
    assert peek_stage(result.events) is RunStage.BLOCKED


def test_exception_maps_to_failed(char_env):
    fake = FakeAgent(exc=RuntimeError("llm down"))
    result = asyncio.run(run_turn(LegacyTurnRequest(message="ping", session_id="boom"), agent=fake))
    assert result.chat == {}
    assert result.stage is RunStage.FAILED
    assert result.state.run.status is RunStatus.FAILED
    assert result.error is not None
    assert "llm down" in result.error.message
    kinds = [_kind(ev) for ev in result.events]
    assert EventKind.ERROR.value in kinds
    assert peek_stage(result.events) is RunStage.FAILED


def test_import_adapter_does_not_load_agent():
    env = os.environ.copy()
    env["DAEDALUS_STANDALONE"] = "1"
    env["PYTHONPATH"] = str(ROOT) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    code = (
        "import daedalus.legacy_adapter\n"
        "import sys\n"
        "bad = [n for n in sys.modules if n == 'daedalus.agent' or n.startswith('daedalus.agent.')]\n"
        "assert not bad, bad\n"
        "assert daedalus.legacy_adapter.adapter_enabled() in (True, False)\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout


def test_agent_and_native_tools_do_not_import_adapter():
    for rel in ("daedalus/agent.py", "daedalus/native_tools.py"):
        tree = ast.parse((ROOT / rel).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name != "daedalus.legacy_adapter"
                    assert "legacy_adapter" not in alias.name
            elif isinstance(node, ast.ImportFrom) and node.module:
                assert node.module != "daedalus.legacy_adapter"
                assert not node.module.endswith(".legacy_adapter")
                assert "legacy_adapter" not in (node.module or "")
            elif isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    assert alias.name != "legacy_adapter"


def test_run_turn_does_not_persist(char_env, monkeypatch):
    def _boom(*_a, **_k):
        raise AssertionError("run_engine should not persist")

    import daedalus.run_engine as run_engine

    monkeypatch.setattr(run_engine, "append_event", _boom)
    monkeypatch.setattr(run_engine, "record", _boom)
    fake = FakeAgent(_chat_payload(reply="no-disk"))
    result = asyncio.run(run_turn(LegacyTurnRequest(message="ping", session_id="s"), agent=fake))
    assert result.chat["reply"] == "no-disk"
    data = char_env / "data"
    assert not (data / "runs").exists() or not any((data / "runs").iterdir())
    assert not (data / "jobs").exists() or not any((data / "jobs").rglob("*.json"))


def test_adapter_enabled_defaults_off(monkeypatch):
    monkeypatch.delenv("DAEDALUS_LEGACY_ADAPTER", raising=False)
    assert adapter_enabled() is False
    monkeypatch.setenv("DAEDALUS_LEGACY_ADAPTER", "0")
    assert adapter_enabled() is False
    monkeypatch.setenv("DAEDALUS_LEGACY_ADAPTER", "off")
    assert adapter_enabled() is False
    monkeypatch.setenv("DAEDALUS_LEGACY_ADAPTER", "1")
    assert adapter_enabled() is True


def test_chat_jobs_flag_default_off(char_env, monkeypatch):
    hits: list[str] = []

    class Fake:
        async def chat(self, **kwargs):
            hits.append("chat")
            return _chat_payload(reply="pong", session_id=kwargs.get("session_id"))

    async def _boom(*_a, **_k):
        raise AssertionError("adapter should not run")

    monkeypatch.delenv("DAEDALUS_LEGACY_ADAPTER", raising=False)
    monkeypatch.setattr("daedalus.agent.get_daedalus", lambda: Fake())
    monkeypatch.setattr("daedalus.legacy_adapter.run_turn", _boom)

    async def _go():
        from daedalus.chat_jobs import get_job, reset, start_chat

        reset()
        started = await start_chat(message="hello from studio", session_id="s1")
        job_id = started["job_id"]
        row = None
        for _ in range(50):
            row = get_job(job_id)
            if row and row["status"] in ("done", "error", "cancelled"):
                break
            await asyncio.sleep(0.05)
        return row

    row = asyncio.run(_go())
    assert hits == ["chat"]
    assert row is not None
    assert row["status"] == "done"
    assert (row.get("result") or {}).get("reply") == "pong"


def test_chat_jobs_flag_on_preserves_chat_dict(char_env, monkeypatch):
    class Fake:
        async def chat(self, **kwargs):
            return _chat_payload(reply="via-adapter", session_id=kwargs.get("session_id"))

    monkeypatch.setenv("DAEDALUS_LEGACY_ADAPTER", "1")
    monkeypatch.setattr("daedalus.agent.get_daedalus", lambda: Fake())

    async def _go():
        from daedalus.chat_jobs import get_job, reset, start_chat

        reset()
        started = await start_chat(message="hello from studio", session_id="s2", effort="whisper")
        job_id = started["job_id"]
        row = None
        for _ in range(50):
            row = get_job(job_id)
            if row and row["status"] in ("done", "error", "cancelled"):
                break
            await asyncio.sleep(0.05)
        return row

    row = asyncio.run(_go())
    assert row is not None
    assert row["status"] == "done"
    result = row.get("result") or {}
    for key in CHAT_RETURN_KEYS:
        assert key in result, f"missing {key}"
    assert result["reply"] == "via-adapter"
    assert result["session_id"] == "s2"
