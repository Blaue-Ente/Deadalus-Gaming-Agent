"""Find the engine binary. ``GODOT_BIN`` wins, then PATH, then known install folders; the result is shown, not assumed."""
from __future__ import annotations

import os
import re
import shutil
from pathlib import Path
from typing import Any

_KNOWN_DIRS = (
    "~/.local/bin", "/usr/local/bin", "/usr/bin", "/opt/godot", "/opt/Godot", "~/Applications",
    "C:/Program Files/Godot", "C:/Program Files (x86)/Steam/steamapps/common/Godot Engine",
    "~/scoop/apps/godot/current", "~/AppData/Local/Microsoft/WinGet/Packages",
    "~/AppData/Local/Programs/Godot",
)
# Windows installs are versioned folders holding Godot_v<ver>-stable_win64[_console].exe; the console build is
# preferred because its stdout can be captured (it is launched without a console window).
_WIN_PATTERNS = ("Godot_v*win64_console.exe", "Godot_v*win64.exe", "godot*.exe")


def _version_key(path: Path) -> list[int]:
    return [int(n) for n in re.findall(r"\d+", path.name)]            # 4.10 is newer than 4.9


def _windows_binary(base: Path) -> Path | None:
    folders = [base] + sorted((p for p in base.glob("*") if p.is_dir()), key=_version_key, reverse=True)
    for pattern in _WIN_PATTERNS:
        for folder in folders:
            for hit in sorted(folder.glob(pattern), key=_version_key, reverse=True):
                if hit.is_file():
                    return hit
    return None
_NAMES = ("godot", "godot4", "godot4.3", "Godot_v4.3", "Godot", "Godot_v4.3-stable_linux.x86_64")


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
        if os.name == "nt":
            win = _windows_binary(d)
            if win is not None:
                return {"ok": True, "path": str(win), "source": "known-dir"}
    return {"ok": False, "error": "engine_missing", "source": "none",
            "message": "Godot 4.x not found. Set GODOT_BIN or put godot on PATH. Files are not a playtest."}


def tools_status() -> dict[str, Any]:
    """What a rendered session needs besides the engine; shown in Settings later (A6.1)."""
    try:
        from .backend import get_backend
        need = get_backend().tools()
    except Exception as exc:                       # an unsupported platform is reported, not guessed at
        need = {f"supported platform ({type(exc).__name__})": False}
    eng = find_godot()
    if eng.get("ok"):
        try:
            from ...godot_first import godot_version  # the one version parser the harness already has
            eng["version"] = godot_version(eng["path"])
        except Exception:
            pass
    return {"engine": eng, "tools": {k: bool(v) for k, v in need.items()},
            "ready": bool(eng.get("ok")) and all(need.values())}
