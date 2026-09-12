"""Unity runtime — real MCP adapter, or an honest non-promise.

This cloud does not vendor a Unity Editor. ``DAEDALUS_UNITY_MCP`` names a
server in ``$DAEDALUS_DATA/mcp.json``. ``UNITY_PATH`` batchmode imports the
project; it is not Play mode. Without either, the engine tool refuses.
A C# scaffold is not a playtest.
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Any

_BATCHMODE_TIMEOUT_S = 180

HONEST = (
    "No Unity Editor in this cloud. Set UNITY_PATH for batchmode or "
    "DAEDALUS_UNITY_MCP for a local Editor MCP. Scaffold is not Play. "
    "We do not vendor Unity."
)

LIVE_ACTIONS = frozenset({
    "inspect", "input", "screenshot", "tree", "errors", "fps",
    "wait", "set", "call", "find",
})

MCP_OP_TOOLS: dict[str, tuple[str, ...]] = {
    "play": (
        "manage_editor", "play", "enter_play_mode", "run_project",
        "editor_play", "start_game",
    ),
    "stop": ("stop", "exit_play_mode", "editor_stop", "stop_game"),
    "scene_tree": (
        "manage_scene", "get_hierarchy", "get_scene_hierarchy",
        "scene_hierarchy", "get_gameobjects",
    ),
    "inspect": (
        "manage_gameobject", "get_gameobject", "inspect_gameobject",
        "get_components",
    ),
    "errors": (
        "read_console", "get_console_logs", "get_console",
        "read_logs", "get_editor_log",
    ),
    "screenshot": ("screenshot", "capture_screenshot", "game_screenshot"),
    "input": ("send_input", "simulate_input", "send_key", "send_keyboard"),
}

_CLOUD_CLAIM = re.compile(
    r"unity editor in the cloud|in[- ]cloud unity|"
    r"opened unity hub (here|in (the )?cloud)|"
    r"pressed play in unity|"
    r"unity play mode (is )?(running|done|ready)|"
    r"we (do not|don't) need unity hub|"
    r"не ни трябва unity hub|"
    r"editor is running on the server",
    re.I,
)


def looks_like_cloud_editor_claim(text: str) -> bool:
    """Strong in-cloud Editor language. Bare 'unity' in a GDD is not a claim."""
    return bool(_CLOUD_CLAIM.search(text or ""))


def mcp_server_name() -> str:
    return (os.environ.get("DAEDALUS_UNITY_MCP") or "").strip()


def is_unity_root(root: Path | str) -> bool:
    base = Path(root)
    if not base.is_dir():
        return False
    if (base / "project.godot").is_file():
        return False
    return (
        (base / "Assets").is_dir()
        or (base / "ProjectSettings" / "ProjectVersion.txt").is_file()
        or (base / "Packages" / "manifest.json").is_file()
    )


def is_unity_slug(slug: str) -> bool:
    if not slug:
        return False
    try:
        from . import playground as pg
        return is_unity_root(pg.project_dir(slug))
    except Exception:
        return False


def map_unity_mcp_tool(op: str, available: list[str]) -> str:
    names = set(available)
    for cand in MCP_OP_TOOLS.get(op, ()):
        if cand in names:
            return cand
    return ""


def honest_refuse(action: str = "") -> dict[str, Any]:
    return {
        "ok": False,
        "honest": True,
        "need": "unity_local",
        "unity_local_required": True,
        "play": False,
        "via": "none",
        "action": action or "",
        "error": HONEST,
    }


def status(root: Path | str | None = None, *, slug: str = "") -> dict[str, Any]:
    base = Path(root) if root else None
    if base is None and slug:
        from . import playground as pg
        base = pg.project_dir(slug)
    unity = bool(base and is_unity_root(base))
    from .engine_runners import unity_bin
    editor = bool(unity_bin())
    mcp = bool(mcp_server_name())
    if not unity:
        promise = "n/a"
    elif mcp:
        promise = "mcp"
    elif editor:
        promise = "batchmode"
    else:
        promise = "local_hub"
    return {
        "ok": True,
        "unity": unity,
        "scaffold": unity,
        "editor": editor,
        "mcp": mcp,
        "mcp_server": mcp_server_name() or "",
        "promise": promise,
        "play": False,
        "honest": promise != "cloud",
        "message": (
            HONEST if unity and promise == "local_hub"
            else (
                "Unity MCP adapter — local Editor, not vendored"
                if promise == "mcp"
                else (
                    "UNITY_PATH batchmode imports; it is not Play mode"
                    if promise == "batchmode"
                    else "not a Unity project"
                )
            )
        ),
    }


def prove_unity(
    root: Path | str,
    *,
    claim: str = "",
    kind: str = "",
) -> dict[str, Any]:
    """Fail closed only on fake in-cloud Editor claims. Racing is N/A."""
    base = Path(root)
    unity = is_unity_root(base) or str(kind).lower() == "unity"
    errors: list[str] = []
    warnings: list[str] = []
    st = status(base)
    if not unity:
        return {
            "ok": True,
            "na": True,
            "errors": [],
            "warnings": [],
            "status": st,
        }
    if looks_like_cloud_editor_claim(claim) and st["promise"] == "local_hub":
        errors.append("prototype wall — Unity Editor is not in this cloud")
        errors.append(HONEST)
    elif st["promise"] == "local_hub":
        warnings.append("Unity Hub / MCP is local — scaffold is not Play")
    elif st["promise"] == "batchmode":
        warnings.append("batchmode import is not Play mode")
    return {
        "ok": not errors,
        "na": False,
        "errors": errors,
        "warnings": warnings,
        "status": st,
    }


async def mcp_action(action: str, slug: str, **fields: Any) -> dict[str, Any]:
    server = mcp_server_name()
    if not server:
        return {"ok": False, "fallback": True, "error": "no DAEDALUS_UNITY_MCP"}
    from . import mcp_client

    listed = await mcp_client.list_tools(server)
    if not listed.get("ok"):
        return {
            "ok": False,
            "fallback": True,
            "error": listed.get("error") or "mcp list failed",
        }
    tools = listed.get("tools") or []
    names: list[str] = []
    for t in tools:
        if isinstance(t, dict) and t.get("name"):
            names.append(str(t["name"]))
        elif isinstance(t, str):
            names.append(t)
    act = (action or "").strip().lower()
    op = {
        "run": "play",
        "start": "play",
        "play": "play",
        "quit": "stop",
        "stop": "stop",
        "tree": "scene_tree",
        "inspect": "inspect",
        "errors": "errors",
        "screenshot": "screenshot",
        "input": "input",
    }.get(act, "")
    if not op:
        return {"ok": False, "fallback": True, "error": "mcp has no mapping for " + act}
    tool = map_unity_mcp_tool(op, names)
    if not tool:
        return {
            "ok": False,
            "fallback": True,
            "error": "no Unity MCP tool for " + op,
            "tools": names[:20],
        }
    arguments: dict[str, Any] = {"slug": slug}
    if fields.get("path"):
        arguments["path"] = fields["path"]
    if fields.get("action_name"):
        arguments["action"] = fields["action_name"]
    if fields.get("key"):
        arguments["key"] = fields["key"]
    called = await mcp_client.call_tool(server, tool, arguments)
    called.setdefault("mcp_server", server)
    called.setdefault("mcp_tool", tool)
    called.setdefault("via", "mcp")
    called.setdefault("play", op == "play")
    return called


def batchmode_run(slug: str) -> dict[str, Any]:
    from .engine_runners import run_unity, unity_bin

    if not unity_bin():
        return honest_refuse("run")
    out = run_unity(slug)
    out = dict(out)
    out["via"] = "batchmode"
    out["play"] = False
    out["honest"] = True
    if out.get("ok"):
        out["note"] = "batchmode import is not Play mode"
    return out


def runtime_verify(root: Path | str) -> dict[str, Any]:
    """Host probe for EngineAdapter. Path batchmode import; not Play. Skip ≠ ok.

    Does not call ``run_unity(slug)``, MCP, or ``engine_session``. Missing
    Editor is ``unity_local``. ``DAEDALUS_UNITY_MCP`` is not this leaf.
    """
    from .engine_runners import unity_bin

    base = Path(root)
    binary = unity_bin() or ""
    if not binary:
        return {
            "ok": False,
            "skipped": True,
            "ran": False,
            "play": False,
            "via": "none",
            "blockers": ["unity_local"],
            "error": "unity_local",
            "preflight": {
                "binary": "",
                "ok": False,
                "skipped": True,
                "blockers": ["unity_local"],
            },
        }
    out = _batchmode_at(base, binary)
    ran = bool(out.get("ran"))
    ok = bool(out.get("ok")) and ran
    error = str(out.get("error") or "")
    blockers: list[str] = []
    if not ok:
        blockers.append(error or "unity_batchmode")
    return {
        "ok": ok,
        "skipped": False,
        "ran": ran,
        "play": False,
        "via": "batchmode" if ran else "none",
        "blockers": blockers,
        "error": error,
        "preflight": {
            "binary": binary,
            "ok": True,
            "skipped": False,
            "blockers": [],
        },
    }


def _batchmode_at(root: Path, binary: str, *, timeout: int = _BATCHMODE_TIMEOUT_S) -> dict[str, Any]:
    cmd = [
        binary,
        "-batchmode",
        "-nographics",
        "-quit",
        "-projectPath",
        str(root),
    ]
    if root.is_dir():
        log_file = root / "Logs" / "daedalus-adapter.log"
        try:
            log_file.parent.mkdir(parents=True, exist_ok=True)
            cmd.extend(["-logFile", str(log_file)])
        except OSError:
            pass
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(root) if root.is_dir() else None,
            capture_output=True,
            text=True,
            timeout=max(30, timeout),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "ran": False, "error": str(exc)[:400]}
    ok = proc.returncode == 0
    return {
        "ok": ok,
        "ran": True,
        "error": "" if ok else (proc.stderr or f"unity rc={proc.returncode}")[:400],
        "returncode": proc.returncode,
    }


async def run_engine_action(action: str, slug: str, **fields: Any) -> dict[str, Any]:
    """Native ``engine`` dispatch for Unity playground slugs."""
    act = (action or "status").strip().lower()
    if not slug:
        return {"ok": False, "error": "slug required"}
    if act == "status":
        from . import playground as pg
        return status(pg.project_dir(slug), slug=slug)

    if mcp_server_name():
        try:
            mcp = await mcp_action(act, slug, **fields)
        except Exception as exc:
            mcp = {"ok": False, "fallback": True, "error": str(exc)[:200]}
        if mcp.get("ok") and not mcp.get("fallback"):
            return mcp
        if act in LIVE_ACTIONS:
            refuse = honest_refuse(act)
            refuse["mcp_error"] = mcp.get("error")
            return refuse

    if act in ("run", "start", "play"):
        return batchmode_run(slug)
    if act in ("quit", "stop"):
        return {"ok": True, "closed": False, "via": "none", "play": False}
    return honest_refuse(act)


def run_action(action: str, slug: str, **fields: Any) -> dict[str, Any]:
    """Sync HTTP helper. Live MCP calls go through ``run_engine_action``."""
    import asyncio

    act = (action or "status").strip().lower()
    if act == "status":
        from . import playground as pg
        root = pg.project_dir(slug) if slug else Path(".")
        return {"ok": True, **status(root, slug=slug)}
    if act == "prove":
        from . import playground as pg
        root = pg.project_dir(slug) if slug else Path(".")
        return prove_unity(
            root,
            claim=str(fields.get("claim") or ""),
            kind=str(fields.get("kind") or ""),
        )
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop and loop.is_running():
        return {
            "ok": False,
            "error": "use engine tool for live Unity MCP actions",
            "hint": HONEST,
        }
    return asyncio.run(run_engine_action(act, slug, **fields))
