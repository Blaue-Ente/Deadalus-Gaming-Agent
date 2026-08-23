"""Tiny .env loader (no extra dependency)."""
from __future__ import annotations

import os
from pathlib import Path


def load_env_file(path: Path | None = None) -> None:
    if path is None:
        here = Path(__file__).resolve().parent.parent
        for candidate in (here / ".env", Path.cwd() / ".env"):
            if candidate.is_file():
                path = candidate
                break
    if path is None or not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip("'").strip('"')
        if key and key not in os.environ:
            os.environ[key] = val
