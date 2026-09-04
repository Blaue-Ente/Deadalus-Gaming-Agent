"""Minimal MCP client (JSON-RPC 2.0 over stdio or HTTP).

Hermes-class agents speak MCP. Daedalus did not. This module lists and calls
tools on optional servers declared in ``$DAEDALUS_DATA/mcp.json``.

No npm runtime dependency — stdlib + httpx. Fail open if a server is missing.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any

log = logging.getLogger(__name__)

PROTOCOL = "2024-11-05"
CLIENT_INFO = {"name": "daedalus", "version": "0.3.1"}


def encode_message(payload: dict[str, Any]) -> bytes:
    """LSP/MCP stdio frame: Content-Length header + JSON body."""
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
    return header + body


def decode_buffer(buf: bytes) -> tuple[list[dict[str, Any]], bytes]:
    """Parse zero or more framed MCP messages; return leftover bytes."""
    messages: list[dict[str, Any]] = []
    while True:
        sep = buf.find(b"\r\n\r\n")
        if sep < 0:
            break
        header = buf[:sep].decode("ascii", errors="replace")
        length = None
        for line in header.split("\r\n"):
            if line.lower().startswith("content-length:"):
                try:
                    length = int(line.split(":", 1)[1].strip())
                except ValueError:
                    length = None
        if length is None:
            buf = buf[sep + 4:]
            continue
        start = sep + 4
        if len(buf) < start + length:
            break
        chunk = buf[start:start + length]
        buf = buf[start + length:]
        try:
            messages.append(json.loads(chunk.decode("utf-8")))
        except json.JSONDecodeError:
            log.warning("mcp: skipped malformed JSON frame")
    return messages, buf


def rpc(method: str, params: dict[str, Any] | None = None, *, id: int | str = 1) -> dict[str, Any]:
    msg: dict[str, Any] = {"jsonrpc": "2.0", "id": id, "method": method}
    if params is not None:
        msg["params"] = params
    return msg


def notification(method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    msg: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
    if params is not None:
        msg["params"] = params
    return msg


def config_path():
    from .paths import data_root
    env = os.environ.get("DAEDALUS_MCP_CONFIG", "").strip()
    if env:
        from pathlib import Path
        return Path(env)
    return data_root() / "mcp.json"


def load_servers() -> dict[str, dict[str, Any]]:
    """Return ``{name: spec}``. Spec is ``command``+``args`` or ``url``."""
    path = config_path()
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("mcp.json unreadable: %s", exc)
        return {}
    servers = data.get("servers") if isinstance(data, dict) else None
    if isinstance(servers, dict):
        return {str(k): v for k, v in servers.items() if isinstance(v, dict)}
    if isinstance(servers, list):
        out = {}
        for row in servers:
            if isinstance(row, dict) and row.get("name"):
                out[str(row["name"])] = row
        return out
    return {}


def save_servers(servers: dict[str, dict[str, Any]]) -> None:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"servers": servers}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def upsert_server(name: str, spec: dict[str, Any]) -> dict[str, Any]:
    """Create or replace one MCP server entry in mcp.json."""
    key = (name or "").strip()
    if not key or "/" in key or ".." in key:
        return {"ok": False, "error": "invalid server name"}
    command = spec.get("command")
    url = spec.get("url")
    if not command and not url:
        return {"ok": False, "error": "command or url required"}
    servers = load_servers()
    row: dict[str, Any] = {}
    if url:
        row["url"] = str(url).strip()
    if command:
        row["command"] = command
        if spec.get("args"):
            row["args"] = list(spec["args"])
    env = spec.get("env")
    if isinstance(env, dict):
        cleaned = {str(k): str(v) for k, v in env.items() if str(k).strip() and str(v).strip()}
        if cleaned:
            row["env"] = cleaned
    servers[key] = row
    save_servers(servers)
    return {"ok": True, "name": key, "config": str(config_path())}


def set_server_env(name: str, env: dict[str, str], *, merge: bool = True) -> dict[str, Any]:
    servers = load_servers()
    spec = servers.get(name)
    if not spec:
        return {"ok": False, "error": f"unknown MCP server {name!r}"}
    current = dict(spec.get("env") or {}) if merge else {}
    for k, v in env.items():
        key = str(k).strip()
        val = str(v or "").strip()
        if not key:
            continue
        if val:
            current[key] = val
        else:
            current.pop(key, None)
    if current:
        spec["env"] = current
    else:
        spec.pop("env", None)
    servers[name] = spec
    save_servers(servers)
    return {"ok": True, "name": name, "env_keys": list(current)}


async def _http_rpc(url: str, payload: dict[str, Any], timeout: float = 25.0) -> dict[str, Any]:
    import httpx
    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.post(url, json=payload, headers={"Content-Type": "application/json", "Accept": "application/json"})
        r.raise_for_status()
        data = r.json()
        if not isinstance(data, dict):
            raise RuntimeError("MCP HTTP did not return a JSON object")
        return data


async def _stdio_session(spec: dict[str, Any], calls: list[dict[str, Any]], timeout: float = 25.0) -> list[dict[str, Any]]:
    command = spec.get("command")
    if isinstance(command, str):
        argv = [command] + list(spec.get("args") or [])
    elif isinstance(command, list):
        argv = [str(x) for x in command]
    else:
        raise ValueError("stdio MCP server needs command")
    env = os.environ.copy()
    extra = spec.get("env") or {}
    if isinstance(extra, dict):
        env.update({str(k): str(v) for k, v in extra.items()})

    proc = await asyncio.create_subprocess_exec(
        *argv,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    assert proc.stdin and proc.stdout
    replies: list[dict[str, Any]] = []
    buf = b""
    try:
        for msg in calls:
            proc.stdin.write(encode_message(msg))
        await proc.stdin.drain()
        proc.stdin.close()
        deadline = asyncio.get_event_loop().time() + timeout
        needed = sum(1 for m in calls if "id" in m)
        while len(replies) < needed and asyncio.get_event_loop().time() < deadline:
            chunk = await asyncio.wait_for(proc.stdout.read(65536), timeout=max(0.1, deadline - asyncio.get_event_loop().time()))
            if not chunk:
                break
            buf += chunk
            msgs, buf = decode_buffer(buf)
            replies.extend(m for m in msgs if "id" in m)
    finally:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        try:
            await asyncio.wait_for(proc.wait(), timeout=2)
        except Exception:
            pass
    return replies


async def handshake_payloads() -> list[dict[str, Any]]:
    return [
        rpc("initialize", {
            "protocolVersion": PROTOCOL,
            "capabilities": {},
            "clientInfo": CLIENT_INFO,
        }, id=1),
        notification("notifications/initialized"),
    ]


async def list_tools(server: str) -> dict[str, Any]:
    spec = load_servers().get(server)
    if not spec:
        return {"ok": False, "error": f"unknown MCP server {server!r}", "servers": list(load_servers())}
    try:
        calls = await handshake_payloads()
        calls.append(rpc("tools/list", {}, id=2))
        if spec.get("url"):
            # HTTP servers typically keep session themselves; send tools/list after init.
            await _http_rpc(str(spec["url"]), calls[0])
            data = await _http_rpc(str(spec["url"]), calls[2])
            tools = ((data.get("result") or {}).get("tools")) if isinstance(data.get("result"), dict) else data.get("tools")
            return {"ok": True, "server": server, "tools": tools or data}
        replies = await _stdio_session(spec, calls)
        listed = next((r for r in replies if r.get("id") == 2), replies[-1] if replies else {})
        result = listed.get("result") if isinstance(listed, dict) else {}
        tools = (result or {}).get("tools") if isinstance(result, dict) else []
        return {"ok": True, "server": server, "tools": tools or []}
    except Exception as exc:
        return {"ok": False, "server": server, "error": str(exc)[:240]}


async def call_tool(server: str, tool: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    spec = load_servers().get(server)
    if not spec:
        return {"ok": False, "error": f"unknown MCP server {server!r}"}
    params = {"name": tool, "arguments": arguments or {}}
    try:
        if spec.get("url"):
            await _http_rpc(str(spec["url"]), (await handshake_payloads())[0])
            data = await _http_rpc(str(spec["url"]), rpc("tools/call", params, id=2))
            if data.get("error"):
                return {"ok": False, "server": server, "tool": tool, "error": data["error"]}
            return {"ok": True, "server": server, "tool": tool, "result": data.get("result", data)}
        calls = await handshake_payloads()
        calls.append(rpc("tools/call", params, id=2))
        replies = await _stdio_session(spec, calls)
        listed = next((r for r in replies if r.get("id") == 2), replies[-1] if replies else {})
        if listed.get("error"):
            return {"ok": False, "server": server, "tool": tool, "error": listed["error"]}
        return {"ok": True, "server": server, "tool": tool, "result": listed.get("result", listed)}
    except Exception as exc:
        return {"ok": False, "server": server, "tool": tool, "error": str(exc)[:240]}


def status() -> dict[str, Any]:
    servers = load_servers()
    return {
        "ok": True,
        "config": str(config_path()),
        "servers": {
            name: {
                "transport": "http" if spec.get("url") else "stdio",
                "url": spec.get("url"),
                "command": spec.get("command"),
                "env_keys": list((spec.get("env") or {}).keys()) if isinstance(spec.get("env"), dict) else [],
            }
            for name, spec in servers.items()
        },
    }
