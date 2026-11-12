"""Game/app capsule — a zip you can copy to a USB stick or send as a file.

Publishing to GitHub/GitLab and /apps/ still exists. Capsules are the offline
sibling: no account, no CDN, unzip and open ``index.html`` (static) or keep
the tree for vite/fastapi.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from . import playground as pg
from .playground_publish import _zip_dir, excluded_secret_files


def capsule_bytes(slug: str) -> bytes:
    root = pg.project_dir(slug)
    if not root.is_dir():
        raise FileNotFoundError(slug)
    return _zip_dir(root)


def capsule_meta(slug: str) -> dict[str, Any]:
    root = pg.project_dir(slug)
    exists = root.is_dir()
    index = (root / "index.html").is_file() if exists else False
    excluded = excluded_secret_files(root) if exists else []
    return {
        "ok": exists,
        "slug": slug,
        "has_index": index,
        "excluded_secrets": len(excluded),
        "hint": (
            "Unzip and open index.html: it works without a server."
            if index else
            "No index.html: the capsule is a source tree (vite/fastapi), not a double-click game."
        ),
    }
