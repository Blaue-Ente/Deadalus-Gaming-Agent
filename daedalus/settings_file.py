"""The one writer of ``$DAEDALUS_DATA/settings.json``.

Evolution and auto-apply both keep flags in this file. Each used to read the whole file, change a key and
write it back, so a write based on a stale read could silently undo the other's key. Every change now goes
through ``update`` (lock, re-read, merge, atomic replace); nothing else writes the file.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

_lock = threading.RLock()


def path() -> Path:
    from .paths import data_root

    p = data_root() / "settings.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def load() -> dict[str, Any]:
    try:
        p = path()
        if p.exists():
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
    except Exception as exc:
        log.warning("settings.json read failed: %s", exc)
    return {}


def update(patch: dict[str, Any]) -> dict[str, Any]:
    """Merge ``patch`` into the file under a lock and return the new content (atomic replace)."""
    with _lock:
        data = load()
        data.update(patch)
        p = path()
        fd, tmp = tempfile.mkstemp(prefix=".settings-", suffix=".json", dir=str(p.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=2, ensure_ascii=False)
            os.replace(tmp, p)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        return data
