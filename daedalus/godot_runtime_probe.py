"""Godot runtime probe — headless first, skip if no binary.

Static DoD always runs. When ``GODOT_BIN`` (or ``godot`` on PATH) exists,
the TCP bridge must also ping, move the player, and show a clean console.
The old ``--quit-after --probe`` path remains as a fallback if the bridge
cannot connect.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .godot_dod import dod_v2_static, merge_runtime

# Headless dummy renderer / shutdown noise is not a script bug.
_IGNORE_SUBSTR = (
    'parameter "m" is null',
    "mesh_get_surface_count",
    "objectdb instances leaked",
)


def godot_bin() -> str:
    env = (os.environ.get("GODOT_BIN") or os.environ.get("GODOT_BINARY") or "").strip()
    if env and Path(env).exists():
        return env
    for name in ("godot", "godot4", "Godot_v4.3", "godot4.3"):
        found = shutil.which(name)
        if found:
            return found
    return ""


def run_probe(pdir: Path, *, frames: int = 180) -> dict[str, Any]:
    """Import + short headless run. Never raises on missing Godot."""
    root = Path(pdir)
    static = dod_v2_static(root)
    info: dict[str, Any] = {
        "ok": static["ok"],
        "ran": False,
        "dod": static,
        "command": "",
        "skipped": "",
    }
    binary = godot_bin()
    if not binary:
        info["skipped"] = "Godot not on PATH; static DoD only. Owner: GODOT_BIN or godot in PATH."
        return info
    cmd = [
        binary,
        "--headless",
        "--path",
        str(root),
        "--quit-after",
        str(max(1, frames)),
        "--",
        "--probe",
    ]
    info["command"] = " ".join(cmd)
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=90,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        info["ok"] = False
        info["error"] = str(exc)
        return info
    info["ran"] = True
    info["returncode"] = proc.returncode
    stdout = proc.stdout or ""
    stderr = proc.stderr or ""
    info["stdout"] = stdout[-2500:]
    info["stderr"] = stderr[-1500:]
    console_issues = _script_errors(stdout, stderr)
    info["console_clean"] = not console_issues
    info["console_flags"] = console_issues
    info["ok"] = static["ok"] and proc.returncode == 0 and not console_issues
    probe_file = root / "studio" / "probe.json"
    if probe_file.is_file():
        info["probe_json"] = probe_file.as_posix()
    return info


def run_runtime(pdir: Path, *, frames: int = 24) -> dict[str, Any]:
    """Static + signal DoD, then live bridge when Godot is installed."""
    root = Path(pdir)
    static = dod_v2_static(root)
    info: dict[str, Any] = {
        "ok": static["ok"],
        "ran": False,
        "bridge": False,
        "dod": static,
        "command": "",
        "skipped": "",
    }
    binary = godot_bin()
    if not binary:
        info["skipped"] = "Godot not on PATH; static DoD only. Owner: GODOT_BIN or godot in PATH."
        return info

    from .godot_bridge import GodotSession, ensure_bridge, ensure_import_sidecars, live_dod, run_import

    ensure_bridge(root)
    ensure_import_sidecars(root)
    imported = run_import(root, binary)
    info["import"] = {"ok": imported.get("ok"), "command": imported.get("command")}

    session = GodotSession(root)
    started = session.start(binary=binary)
    info["command"] = started.get("command") or session.command
    if started.get("ok"):
        info["ran"] = True
        info["bridge"] = True
        live = live_dod(session)
        flags = _script_errors(session.stdout_tail(80), "")
        info["console_flags"] = flags
        info["console_clean"] = not flags
        info["dod"] = merge_runtime(static, live, flags)
        info["ok"] = bool(info["dod"]["ok"])
        info["live"] = live
        session.close()
        return info

    session.close()
    info["bridge_error"] = started.get("error")
    fallback = run_probe(root, frames=max(60, frames * 4))
    info["ran"] = bool(fallback.get("ran"))
    info["fallback_probe"] = True
    flags = list(fallback.get("console_flags") or [])
    live_fail = {
        "ok": False,
        "blocking": ["bridge_ping", "player_moved"],
        "checks": [
            {"id": "bridge_ping", "ok": False, "detail": started.get("error")},
            {"id": "player_moved", "ok": False, "detail": "bridge unavailable"},
        ],
        "stdout": started.get("stdout") or fallback.get("stdout"),
    }
    info["dod"] = merge_runtime(static, live_fail, flags)
    info["ok"] = False
    info["console_flags"] = flags
    info["stdout"] = (started.get("stdout") or "")[-1500:] or fallback.get("stdout")
    return info


def _script_errors(stdout: str, stderr: str) -> list[str]:
    flags: list[str] = []
    for raw in (stdout + "\n" + stderr).splitlines():
        line = raw.strip()
        low = line.lower()
        if any(tok in low for tok in _IGNORE_SUBSTR):
            continue
        if low.startswith("error:") or "script error" in low or "failed to load" in low:
            flags.append(line[:160])
    return flags
