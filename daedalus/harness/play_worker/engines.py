"""Find the engine binary. ``GODOT_BIN`` wins, then PATH, then known install folders; the result is shown, not assumed."""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

_KNOWN_DIRS = (
    "~/.local/bin", "/usr/local/bin", "/usr/bin", "/opt/godot", "/opt/Godot", "~/Applications",
    "C:/Program Files/Godot", "C:/Program Files (x86)/Steam/steamapps/common/Godot Engine",
    "~/scoop/apps/godot/current", "~/AppData/Local/Microsoft/WinGet/Packages",
)
_NAMES = ("godot", "godot4", "Godot", "Godot_v4.3-stable_linux.x86_64")


def find_godot() -> dict[str, Any]:
    env = (os.environ.get("GODOT_BIN") or os.environ.get("GODOT_BINARY") or "").strip()
    if env:
        p = Path(env).expanduser()
        if p.is_file() and os.access(p, os.X_OK):
            return {"ok": True, "path": str(p), "source": "env"}
        return {"ok": False, "error": "engine_missing", "source": "env",
                "message": f"GODOT_BIN is set but is not an executable file: {env}"}
    for name in _NAMES:
        hit = shutil.which(name)
        if hit:
            return {"ok": True, "path": hit, "source": "path"}
    for folder in _KNOWN_DIRS:
        d = Path(folder).expanduser()
        if not d.is_dir():
            continue
        for name in _NAMES:
            cand = d / name
            if cand.is_file() and os.access(cand, os.X_OK):
                return {"ok": True, "path": str(cand), "source": "known-dir"}
    return {"ok": False, "error": "engine_missing", "source": "none",
            "message": "Godot 4.x not found. Set GODOT_BIN or put godot on PATH. Files are not a playtest."}


def godot_version(path: str) -> str:
    try:
        out = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=15)
        return (out.stdout or out.stderr).strip().splitlines()[0] if (out.stdout or out.stderr).strip() else ""
    except Exception:
        return ""


def tools_status() -> dict[str, Any]:
    """What a rendered session needs besides the engine; shown in Settings later (A6.1)."""
    need = {"Xvfb": shutil.which("Xvfb"), "xdotool": shutil.which("xdotool"), "import": shutil.which("import")}
    eng = find_godot()
    return {"engine": eng, "tools": {k: bool(v) for k, v in need.items()},
            "ready": bool(eng.get("ok")) and all(need.values())}
