"""Godot runtime probe — headless first, skip if no binary.

Runs the main scene briefly, captures stdout, optional probe.json.
Screenshots/FPS from a live Editor are later; planted-defect static checks
run even in the cloud.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .godot_dod import dod_v2_static

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
