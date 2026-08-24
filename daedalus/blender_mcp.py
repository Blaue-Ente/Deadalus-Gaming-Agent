"""Stdio MCP server wrapping blender_jobs.

Run: ``python -m daedalus.blender_mcp``
Owner may add it to $DAEDALUS_DATA/mcp.json (Studio addons → blender-mcp).

Does not import bpy. tools/call emits a script; execute=true runs Blender if present.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from .blender_jobs import JOB_IDS, list_tool_schemas, mcp_call
from .mcp_client import PROTOCOL, decode_buffer, encode_message


SERVER_INFO = {"name": "daedalus-blender-jobs", "version": "0.1.0"}


def _result_text(payload: dict[str, Any]) -> dict[str, Any]:
    import json

    text = json.dumps(payload, ensure_ascii=False, indent=2)
    return {"content": [{"type": "text", "text": text}]}


def handle(msg: dict[str, Any]) -> dict[str, Any] | None:
    method = msg.get("method")
    msg_id = msg.get("id")
    params = msg.get("params") or {}
    if method is None:
        return None
    if msg_id is None:
        return None
    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "protocolVersion": PROTOCOL,
                "capabilities": {"tools": {}},
                "serverInfo": SERVER_INFO,
            },
        }
    if method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {"tools": list_tool_schemas()},
        }
    if method == "tools/call":
        name = str(params.get("name") or "")
        args = params.get("arguments") or {}
        pdir = Path(str(args.get("project_dir") or ".")).resolve()
        if name not in JOB_IDS:
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "error": {"code": -32601, "message": f"unknown tool {name}"},
            }
        payload = mcp_call(name, args, pdir=pdir)
        return {"jsonrpc": "2.0", "id": msg_id, "result": _result_text(payload)}
    if method == "ping":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {}}
    return {
        "jsonrpc": "2.0",
        "id": msg_id,
        "error": {"code": -32601, "message": f"unknown method {method}"},
    }


def serve() -> None:
    buf = b""
    while True:
        chunk = sys.stdin.buffer.read(4096)
        if not chunk:
            break
        buf += chunk
        messages, buf = decode_buffer(buf)
        for msg in messages:
            if not isinstance(msg, dict):
                continue
            reply = handle(msg)
            if reply is None:
                continue
            sys.stdout.buffer.write(encode_message(reply))
            sys.stdout.buffer.flush()


def main() -> None:
    serve()


if __name__ == "__main__":
    main()
