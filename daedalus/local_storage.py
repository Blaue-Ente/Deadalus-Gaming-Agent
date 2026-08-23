"""Filesystem object storage for standalone Docker (no Replit GCS).

ADN keeps using ``argos_data_nexus.storage_service``. This module is only
returned from ``host.storage()`` when Daedalus runs without ADN.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any


def _root() -> Path:
    from .paths import data_root
    path = data_root() / "objects"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _safe(object_name: str) -> Path:
    rel = (object_name or "").lstrip("/")
    target = (_root() / rel).resolve()
    root = _root().resolve()
    if root not in target.parents and target != root:
        raise ValueError(f"object path escapes storage root: {object_name}")
    return target


class LocalObjectStorage:
    def is_configured(self) -> bool:
        return True

    def upload_bytes(
        self,
        data: bytes,
        object_name: str,
        content_type: str = "",
    ) -> str:
        path = _safe(object_name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return object_name

    def download_bytes(self, object_name: str) -> bytes:
        path = _safe(object_name)
        if not path.is_file():
            raise FileNotFoundError(object_name)
        return path.read_bytes()

    def list_objects(self, prefix: str = "") -> list[dict[str, Any]]:
        root = _root()
        out: list[dict[str, Any]] = []
        if not root.is_dir():
            return out
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            name = path.relative_to(root).as_posix()
            if prefix and not name.startswith(prefix):
                continue
            out.append({"name": name, "size": path.stat().st_size})
        return out
