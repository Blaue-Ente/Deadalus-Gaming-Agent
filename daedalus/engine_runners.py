"""Real engine runners — Godot headless, Unity batchmode, Playwright gameplay.

Scaffold checks are not playtests. Missing binaries fail closed (skip ≠ ok).
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .game_build_contract import skip_is_not_ok, verified

log = logging.getLogger(__name__)


def unity_bin() -> str:
    env = (os.environ.get("UNITY_PATH") or os.environ.get("UNITY_EDITOR") or "").strip()
    if env and Path(env).exists():
        return env
    found = shutil.which("Unity") or shutil.which("unity")
    if found:
        return found
    home = Path.home()
    patterns = [
        home / "Unity" / "Hub" / "Editor",
        Path("/opt/unity/Editor/Unity"),
        Path("/Applications/Unity/Hub/Editor"),
    ]
    for base in patterns:
        if base.is_file() and os.access(base, os.X_OK):
            return str(base)
        if base.is_dir():
            for cand in sorted(base.glob("*/Editor/Unity")) + sorted(
                base.glob("*/Unity.app/Contents/MacOS/Unity")
            ):
                if cand.is_file() and os.access(cand, os.X_OK):
                    return str(cand)
    return ""


def run_godot(slug: str, *, frames: int = 180) -> dict[str, Any]:
    """Headless Godot runtime DoD. No binary → skipped, not ok."""
    from . import playground as pg
    from .godot_runtime_probe import run_runtime
    from .playground_godot_scaffold import verify_godot_scaffold

    scaffold = verify_godot_scaffold(slug)
    root = pg.project_dir(slug)
    probe = run_runtime(root, frames=min(24, max(8, frames // 8)))
    logs = [f"[godot] scaffold: {scaffold.get('file_count', 0)} files, {scaffold.get('gd_count', 0)} .gd"]
    if probe.get("skipped"):
        logs.append(str(probe["skipped"]))
        return skip_is_not_ok({
            "ok": False,
            "skipped": True,
            "healthy": False,
            "exited": False,
            "install_ok": True,
            "logs": logs,
            "error": probe["skipped"],
            "summary": probe["skipped"],
            "dod": probe.get("dod"),
            "ran": False,
        })
    ok = bool(probe.get("ok") and scaffold.get("ok"))
    err = ""
    if not scaffold.get("ok"):
        err = scaffold.get("error") or "Godot scaffold incomplete"
    elif not probe.get("ok"):
        live_block = (probe.get("dod") or {}).get("blocking") or []
        err = (
            probe.get("error")
            or probe.get("bridge_error")
            or "; ".join(str(x) for x in live_block)
            or "; ".join(probe.get("console_flags") or [])
        )[:400]
    logs.append(
        f"[godot] bridge={probe.get('bridge')} ran={probe.get('ran')} "
        f"blocking={(probe.get('dod') or {}).get('blocking')}"
    )
    return {
        "ok": ok,
        "skipped": False,
        "healthy": ok,
        "exited": not ok,
        "install_ok": True,
        "logs": logs,
        "error": err,
        "ran": bool(probe.get("ran")),
        "bridge": bool(probe.get("bridge")),
        "dod": probe.get("dod"),
    }


def run_unity(slug: str, *, timeout: int = 180) -> dict[str, Any]:
    """Unity Editor batchmode import, or fail if the Editor is missing."""
    from . import playground as pg
    from .playground_runtime import verify_unity_scaffold

    uv = verify_unity_scaffold(slug)
    logs = [f"[unity] scaffold: {uv.get('file_count', 0)} files, {uv.get('cs_count', 0)} .cs"]
    if not uv.get("ok"):
        return {
            "ok": False,
            "skipped": False,
            "healthy": False,
            "exited": False,
            "install_ok": True,
            "logs": logs,
            "error": uv.get("error") or "Unity scaffold incomplete",
            "ran": False,
        }
    binary = unity_bin()
    if not binary:
        msg = "Unity Editor not found (set UNITY_PATH). Scaffold is not a playtest."
        logs.append(msg)
        return skip_is_not_ok({
            "ok": False,
            "skipped": True,
            "unity_local_required": True,
            "healthy": False,
            "exited": False,
            "install_ok": True,
            "logs": logs,
            "error": msg,
            "summary": msg,
            "ran": False,
        })
    root = pg.project_dir(slug)
    log_file = root / "Logs" / "daedalus-runner.log"
    log_file.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        binary,
        "-batchmode",
        "-nographics",
        "-quit",
        "-projectPath",
        str(root),
        "-logFile",
        str(log_file),
    ]
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=max(30, timeout),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {
            "ok": False,
            "skipped": False,
            "healthy": False,
            "exited": True,
            "install_ok": True,
            "logs": logs + [str(exc)[:300]],
            "error": str(exc)[:400],
            "ran": False,
            "command": " ".join(cmd),
        }
    tail = ""
    try:
        if log_file.is_file():
            tail = log_file.read_text(encoding="utf-8", errors="replace")[-1500:]
    except OSError:
        tail = ""
    ok = proc.returncode == 0
    logs.append(f"[unity] batchmode rc={proc.returncode}")
    if tail:
        logs.extend(tail.splitlines()[-20:])
    return {
        "ok": ok,
        "skipped": False,
        "healthy": ok,
        "exited": not ok,
        "install_ok": True,
        "logs": logs,
        "error": "" if ok else (proc.stderr or f"unity rc={proc.returncode}")[:400],
        "ran": True,
        "returncode": proc.returncode,
        "command": " ".join(cmd),
    }


async def gameplay_for_kind(slug: str, kind: str, *, milestone_id: str = "M0") -> dict[str, Any]:
    """Adapter gameplay. Chromium is not Godot; web preview is not device_smoke."""
    from .capability_registry import BROWSER_VISUAL_KINDS, platform_for_kind
    from .playground_gameplay_verify import gameplay_verify_app

    k = (kind or "").strip().lower()
    platform = platform_for_kind(k)
    if k == "unity" or platform is None and k == "unity":
        return {
            "ok": False,
            "skipped": False,
            "na": False,
            "summary": "unsupported platform",
            "blockers": ["unsupported_platform"],
        }
    if platform == "godot":
        from . import playground as pg
        from .godot_first import gameplay_probe

        root = pg.project_dir(slug)
        return skip_is_not_ok(gameplay_probe(root))
    if platform in {"expo", "capacitor"}:
        return skip_is_not_ok({
            "ok": False,
            "skipped": True,
            "summary": "web preview does not replace device_smoke",
            "blockers": ["device_smoke"],
        })
    if k not in BROWSER_VISUAL_KINDS:
        return {"ok": True, "skipped": False, "na": True, "summary": f"no gameplay for {k}"}
    raw = await gameplay_verify_app(slug, k, milestone_id=milestone_id)
    return skip_is_not_ok(raw)


def engine_verified(result: dict[str, Any] | None) -> bool:
    return verified(result)
