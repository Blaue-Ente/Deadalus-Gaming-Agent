"""A0-d: the permission gate fails closed when the check itself cannot run."""
from __future__ import annotations

import asyncio

from a0_support import a0_tmp  # noqa: F401


def _boom(*_a, **_k):
    raise RuntimeError("store unavailable")


def test_gated_tool_blocked_when_check_raises(a0_tmp, monkeypatch):
    from daedalus import native_tools, outbox

    monkeypatch.setattr(outbox, "check_permission", _boom)
    ok, msg = native_tools.before_tool_call("web_search")
    assert ok is False and "fail-closed" in msg and "web_search" in msg


def test_unclassed_tool_still_allowed_and_granted_class_allowed(a0_tmp, monkeypatch):
    from daedalus import native_tools, outbox

    monkeypatch.setattr(outbox, "check_permission", _boom)
    assert native_tools.before_tool_call("read_file") == (True, "")  # no class, never consults the store
    monkeypatch.undo()
    outbox.grant_permission("web_search", hours=1)
    assert native_tools.before_tool_call("web_search") == (True, "")


def test_execute_native_tool_returns_denial_without_running(a0_tmp, monkeypatch):
    from daedalus import native_tools, outbox

    monkeypatch.setattr(outbox, "check_permission", _boom)
    ran = []

    async def reader(*a, **k):
        ran.append(1)
        return {}

    out = asyncio.run(native_tools.execute_native_tool(
        "web_search", {"query": "x"}, memory=None, session_id="s", read_file_fn=reader,
    ))
    assert "fail-closed" in out and ran == []


def test_agent_perm_gate_fails_closed(a0_tmp, monkeypatch):
    from daedalus import agent, outbox

    monkeypatch.setattr(outbox, "check_permission", _boom)
    msg = agent._perm_gate("web_search", "NEED_WEB")
    assert msg and "fail-closed" in msg
