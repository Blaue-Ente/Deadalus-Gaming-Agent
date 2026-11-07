"""Windows per-user install layout (no Administrator required).

Install (code + venv):  %LOCALAPPDATA%\\Programs\\Daedalus
Data (memory, games):   %LOCALAPPDATA%\\Daedalus

The desktop launcher sets ``DAEDALUS_WINDOWS_APP=1`` and the data/playground
env vars so a git checkout on Linux is unchanged.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Mapping, MutableMapping


APP_NAME = "Daedalus"
DISPLAY_NAME = "Daedalus"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8787


def local_app_data(environ: Mapping[str, str] | None = None) -> Path:
    env = environ if environ is not None else os.environ
    raw = (env.get("LOCALAPPDATA") or "").strip()
    if raw:
        return Path(raw)
    home = env.get("HOME") or env.get("USERPROFILE") or str(Path.home())
    return Path(home) / "AppData" / "Local"


def layout(environ: Mapping[str, str] | None = None) -> dict[str, Any]:
    root = local_app_data(environ)
    data = root / APP_NAME
    install = root / "Programs" / APP_NAME
    host = DEFAULT_HOST
    port = DEFAULT_PORT
    env = environ if environ is not None else os.environ
    try:
        port = int((env.get("PORT") or "").strip() or DEFAULT_PORT)
    except ValueError:
        port = DEFAULT_PORT
    host_env = (env.get("HOST") or "").strip()
    if host_env and host_env not in ("0.0.0.0", "::"):
        host = host_env
    return {
        "local_app_data": root,
        "data": data,
        "playground": data / "playground",
        "env_file": data / ".env",
        "install": install,
        "venv": install / ".venv",
        "python": install / ".venv" / "Scripts" / "python.exe",
        "pythonw": install / ".venv" / "Scripts" / "pythonw.exe",
        "host": host,
        "port": port,
        "studio_url": f"http://{host}:{port}/",
        "display_name": DISPLAY_NAME,
        "app_name": APP_NAME,
    }


def env_mapping(loc: dict[str, Any] | None = None) -> dict[str, str]:
    loc = loc or layout()
    return {
        "DAEDALUS_STANDALONE": "1",
        "DAEDALUS_WINDOWS_APP": "1",
        "DAEDALUS_DATA": str(loc["data"]),
        "DAEDALUS_PLAYGROUND": str(loc["playground"]),
        "HOST": str(loc["host"]),
        "PORT": str(loc["port"]),
    }


def apply_layout(
    environ: MutableMapping[str, str] | None = None,
    *,
    overwrite: bool = False,
    create: bool = True,
) -> dict[str, Any]:
    """Apply Windows-app paths to ``environ`` (default: ``os.environ``).

    Existing keys win unless ``overwrite`` is True — tests and explicit
    ``DAEDALUS_DATA`` keep working.
    """
    target: MutableMapping[str, str] = environ if environ is not None else os.environ
    loc = layout(target)
    for key, value in env_mapping(loc).items():
        if overwrite or not (target.get(key) or "").strip():
            target[key] = value
    if create:
        Path(loc["data"]).mkdir(parents=True, exist_ok=True)
        Path(loc["playground"]).mkdir(parents=True, exist_ok=True)
    loc["applied"] = env_mapping(loc)
    return loc


def is_windows_app(environ: Mapping[str, str] | None = None) -> bool:
    env = environ if environ is not None else os.environ
    flag = (env.get("DAEDALUS_WINDOWS_APP") or "").strip().lower()
    return flag in ("1", "true", "yes", "on")
