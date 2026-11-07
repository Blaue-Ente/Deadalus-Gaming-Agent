"""Engine session facade — Godot headless first, Unity MCP or honest refuse.

One interface so native ``engine`` shares ops: scene_tree, inspect, input,
screenshot, errors, fps, quit.

``DAEDALUS_GODOT_MCP`` / ``DAEDALUS_UNITY_MCP`` name servers in
``$DAEDALUS_DATA/mcp.json``. Godot falls back to the headless TCP bridge.
Unity falls back to ``UNITY_PATH`` batchmode (not Play), else an honest
non-promise. We do not vendor an Editor.
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

log = logging.getLogger(__name__)

_SESSIONS: dict[str, Any] = {}

MCP_OP_TOOLS: dict[str, tuple[str, ...]] = {
    "scene_tree": ("get_scene_tree", "scene_tree_dump", "game_scene_tree"),
    "inspect": ("inspect_node", "get_node_properties", "game_get_properties"),
    "errors": ("get_runtime_errors", "get_errors", "get_debugger_errors", "get_console_log"),
    "screenshot": ("screenshot", "game_screenshot"),
    "input": ("send_input_action", "send_key_event"),
    "play": ("run_scene", "play_scene", "run_project"),
}


def mcp_server_name() -> str:
    return (os.environ.get("DAEDALUS_GODOT_MCP") or "").strip()


def _slug_root(slug: str):
    from . import playground as pg
    return pg.project_dir(slug)


def _is_godot_project(slug: str) -> bool:
    try:
        root = _slug_root(slug)
    except Exception:
        return False
    return (root / "project.godot").is_file()


def close_session(slug: str) -> dict[str, Any]:
    sess = _SESSIONS.pop(slug, None)
    if sess is None:
        return {"ok": True, "closed": False}
    try:
        sess.close()
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:200], "closed": True}
    return {"ok": True, "closed": True}


def _godot_bin() -> str:
    from .godot_runtime_probe import godot_bin
    return godot_bin()


def _ensure_session(slug: str) -> tuple[Any, dict[str, Any]]:
    sess = _SESSIONS.get(slug)
    if sess is not None:
        return sess, {"ok": True, "reused": True}
    binary = _godot_bin()
    if not binary:
        return None, {
            "ok": False,
            "skipped": True,
            "error": "Godot not on PATH; set GODOT_BIN. Files are not a playtest.",
        }
    from .godot_bridge import GodotSession

    root = _slug_root(slug)
    sess = GodotSession(root)
    started = sess.start(binary=binary)
    if not started.get("ok"):
        sess.close()
        return None, started
    _SESSIONS[slug] = sess
    return sess, started


PIPELINE_ACTIONS = frozenset({
    "preflight", "contract", "context", "slice", "dod",
    "pipeline", "godot_first", "review", "export", "publish", "runtime_dod",
})


def godot_action(action: str, slug: str, **fields: Any) -> dict[str, Any]:
    """Synchronous Godot bridge dispatch (used by the runner and tests)."""
    act = (action or "").strip().lower()
    if not slug:
        return {"ok": False, "error": "slug required"}
    if act in PIPELINE_ACTIONS:
        from .godot_first import engine_pipeline_action
        return engine_pipeline_action(act, slug, **fields)
    if not _is_godot_project(slug) and act not in {"quit"}:
        return {"ok": False, "error": f"not a Godot project: {slug}"}

    if act == "quit":
        return close_session(slug)

    sess, boot = _ensure_session(slug)
    if sess is None:
        return boot

    if act in {"run", "start"}:
        tree = sess.request("scene_tree")
        ping = sess.request("ping")
        nodes = tree.get("tree") if isinstance(tree.get("tree"), list) else []
        return {
            "ok": bool(ping.get("ok") and tree.get("ok", True)),
            "action": "run",
            "port": getattr(sess, "port", 0),
            "pong": ping.get("pong"),
            "fps": ping.get("fps"),
            "game": ping.get("game"),
            "node_count": len(nodes),
            "command": getattr(sess, "command", ""),
        }

    if act == "tree":
        return sess.request("scene_tree")
    if act == "inspect":
        return sess.request("inspect_node", path=str(fields.get("path") or ""))
    if act == "set":
        return sess.request(
            "set_prop",
            path=str(fields.get("path") or ""),
            prop=str(fields.get("prop") or ""),
            value=fields.get("value"),
        )
    if act == "call":
        return sess.request(
            "call_method",
            path=str(fields.get("path") or ""),
            method=str(fields.get("method") or ""),
            args=fields.get("args") if isinstance(fields.get("args"), list) else [],
        )
    if act == "input":
        key = str(fields.get("key") or "")
        pressed = fields.get("pressed")
        if pressed is None:
            pressed = True
        if key:
            return sess.request("send_key", key=key, pressed=bool(pressed))
        return sess.request(
            "send_input",
            action=str(fields.get("action_name") or fields.get("input_action") or "ui_right"),
            pressed=bool(pressed),
        )
    if act == "screenshot":
        return sess.request("screenshot")
    if act == "errors":
        return sess.request("errors")
    if act == "fps":
        return sess.request("fps_sample")
    if act == "wait":
        return sess.request("wait_frames", n=int(fields.get("frames") or 8))
    if act == "find":
        return sess.request("find_node", name=str(fields.get("path") or fields.get("name") or "Player"))
    return {"ok": False, "error": f"unknown engine action {act!r}"}


def map_mcp_tool(op: str, available: list[str]) -> str:
    names = set(available)
    for cand in MCP_OP_TOOLS.get(op, ()):
        if cand in names:
            return cand
    return ""


async def mcp_action(action: str, slug: str, **fields: Any) -> dict[str, Any]:
    server = mcp_server_name()
    if not server:
        return {"ok": False, "fallback": True, "error": "no DAEDALUS_GODOT_MCP"}
    from . import mcp_client

    listed = await mcp_client.list_tools(server)
    if not listed.get("ok"):
        return {"ok": False, "fallback": True, "error": listed.get("error") or "mcp list failed"}
    tools = listed.get("tools") or listed.get("result", {}).get("tools") or []
    names = []
    for t in tools:
        if isinstance(t, dict) and t.get("name"):
            names.append(str(t["name"]))
        elif isinstance(t, str):
            names.append(t)
    act = (action or "").strip().lower()
    op = {
        "run": "play",
        "start": "play",
        "tree": "scene_tree",
        "inspect": "inspect",
        "errors": "errors",
        "screenshot": "screenshot",
        "input": "input",
    }.get(act, "")
    if not op:
        return {"ok": False, "fallback": True, "error": f"mcp has no mapping for {act}"}
    tool = map_mcp_tool(op, names)
    if not tool:
        return {"ok": False, "fallback": True, "error": f"no MCP tool for {op}", "tools": names[:20]}
    arguments: dict[str, Any] = {"slug": slug}
    if fields.get("path"):
        arguments["path"] = fields["path"]
    if fields.get("action_name"):
        arguments["action"] = fields["action_name"]
    called = await mcp_client.call_tool(server, tool, arguments)
    called.setdefault("mcp_server", server)
    called.setdefault("mcp_tool", tool)
    return called


async def run_action(action: str, slug: str, **fields: Any) -> dict[str, Any]:
    from .unity_runtime import is_unity_slug, run_engine_action as unity_engine

    act = (action or "").strip().lower()
    if act in PIPELINE_ACTIONS:
        from .godot_first import engine_pipeline_action
        return await asyncio.to_thread(engine_pipeline_action, act, slug, **fields)
    if is_unity_slug(slug):
        return await unity_engine(action, slug, **fields)
    if mcp_server_name():
        try:
            mcp = await mcp_action(action, slug, **fields)
        except Exception as exc:
            log.warning("godot MCP adapter failed open to headless: %s", exc)
            mcp = {"ok": False, "fallback": True, "error": str(exc)[:200]}
        if mcp.get("ok") and not mcp.get("fallback"):
            return mcp
        if not mcp.get("fallback") and mcp.get("ok") is False and "unknown MCP server" not in str(mcp.get("error") or ""):
            # Real MCP error — still try headless so cloud/CI keeps working.
            log.info("godot MCP miss (%s); headless fallback", mcp.get("error"))
    return await asyncio.to_thread(godot_action, action, slug, **fields)
