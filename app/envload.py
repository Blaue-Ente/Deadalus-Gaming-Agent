"""Tiny .env loader (no extra dependency)."""
from __future__ import annotations

import os
from pathlib import Path


def env_file_candidates(path: Path | None = None) -> list[Path]:
    """Search order: explicit file, then user data, then product / cwd."""
    if path is not None:
        return [Path(path)]
    found: list[Path] = []
    data = (os.environ.get("DAEDALUS_DATA") or "").strip()
    if data:
        found.append(Path(data) / ".env")
    local = (os.environ.get("LOCALAPPDATA") or "").strip()
    if local:
        found.append(Path(local) / "Daedalus" / ".env")
    here = Path(__file__).resolve().parent.parent
    found.extend((here / ".env", Path.cwd() / ".env"))
    seen: set[str] = set()
    unique: list[Path] = []
    for candidate in found:
        key = str(candidate)
        if key in seen:
            continue
        seen.add(key)
        unique.append(candidate)
    return unique


def _apply_secrets() -> None:
    try:
        from daedalus.connectivity import apply_secrets
        apply_secrets()
    except Exception:
        pass


def _apply_file(path: Path) -> None:
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip("'").strip('"')
        if key and key not in os.environ:
            os.environ[key] = val


def load_env_file(path: Path | None = None) -> None:
    for candidate in env_file_candidates(path):
        if candidate.is_file():
            _apply_file(candidate)
            if path is not None:
                return
    _apply_secrets()
