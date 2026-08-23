"""Playground workspace durability — zip backups to object storage.

Ephemeral FS under ``playground/projects/{slug}/`` is lost on redeploy.
This module mirrors ``playground_publish`` durability for *unpublished* workspaces.
"""
from __future__ import annotations

import logging
from typing import Any

from . import playground as pg
from .playground_publish import _unzip_to, _zip_dir

log = logging.getLogger(__name__)

_OBJ_PREFIX = "playground/workspaces"


def backup_object_path(slug: str) -> str:
    return f"{_OBJ_PREFIX}/{pg.slugify(slug)}.zip"


def workspace_file_count(slug: str) -> int:
    try:
        return len(pg.read_tree(slug))
    except Exception:
        return 0


def workspace_is_empty(slug: str) -> bool:
    return workspace_file_count(slug) == 0


async def backup_workspace(slug: str, *, trigger: str = "manual") -> dict[str, Any]:
    """Zip project workspace and upload to object storage (when configured)."""
    safe = pg.slugify(slug)
    root = pg.project_dir(safe)
    if not root.exists() or not any(root.iterdir()):
        return {"ok": False, "skipped": True, "reason": "empty_workspace", "trigger": trigger}

    data = _zip_dir(root)
    object_path = backup_object_path(safe)
    stored = False
    try:
        from .host import storage as _storage
        svc = _storage()
        if not svc.is_configured():
            return {
                "ok": False,
                "skipped": True,
                "reason": "storage_not_configured",
                "trigger": trigger,
            }
        svc.upload_bytes(data, object_path, content_type="application/zip")
        stored = True
    except Exception as exc:
        log.warning("workspace backup failed for %s: %s", safe, exc)
        return {"ok": False, "error": str(exc), "trigger": trigger}

    log.info(
        "Playground workspace backup: %s (%d bytes, trigger=%s)",
        safe, len(data), trigger,
    )
    return {
        "ok": True,
        "slug": safe,
        "size_bytes": len(data),
        "durable": stored,
        "object_path": object_path,
        "trigger": trigger,
    }


async def try_restore_workspace(slug: str) -> dict[str, Any]:
    """Restore workspace from object storage when local dir is empty."""
    safe = pg.slugify(slug)
    if not workspace_is_empty(safe):
        return {"ok": True, "restored": False, "reason": "workspace_not_empty"}

    object_path = backup_object_path(safe)
    try:
        from .host import storage as _storage
        svc = _storage()
        if not svc.is_configured():
            return {"ok": False, "restored": False, "reason": "storage_not_configured"}

        exists = any(o.get("name") == object_path for o in svc.list_objects(prefix=object_path))
        if not exists:
            return {"ok": True, "restored": False, "reason": "no_backup"}

        data = svc.download_bytes(object_path)
    except Exception as exc:
        log.warning("workspace restore failed for %s: %s", safe, exc)
        return {"ok": False, "restored": False, "error": str(exc)}

    root = pg.project_dir(safe)
    _unzip_to(data, root)
    log.info("Playground workspace restored %s from %s", safe, object_path)
    return {
        "ok": True,
        "restored": True,
        "slug": safe,
        "object_path": object_path,
        "file_count": workspace_file_count(safe),
    }


async def restore_all_workspaces_on_startup() -> dict[str, Any]:
    """On ADN startup: restore empty workspaces from object storage backups."""
    restored: list[str] = []
    errors: list[dict[str, str]] = []
    try:
        projects = await pg.list_projects()
    except Exception as exc:
        return {"ok": False, "error": str(exc)}

    for proj in projects:
        slug = proj["slug"]
        if not workspace_is_empty(slug):
            continue
        result = await try_restore_workspace(slug)
        if result.get("restored"):
            restored.append(slug)
        elif result.get("error"):
            errors.append({"slug": slug, "error": result["error"]})

    if restored:
        log.info("Playground workspace startup restore: %s", ", ".join(restored))
    return {"ok": True, "restored": restored, "errors": errors}


def workspace_warning(slug: str) -> str:
    """Human-readable warning when registry exists but workspace has no files."""
    if not workspace_is_empty(slug):
        return ""
    return (
        "Workspace е празен — файловете може да са изгубени при redeploy. "
        "Пусни Autopilot отново или Publish (ако вече е бил готов)."
    )
