"""Standalone process boot — SQLite, published /apps, job recovery.

ADN's FastAPI lifespan in ``argos_data_nexus.main`` already does the Postgres
equivalent. This module runs only when ``DAEDALUS_STANDALONE=1`` (CLI/Docker),
so the ADN process is never redirected here.
"""
from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)


async def startup() -> dict[str, Any]:
    """Open SQLite, rematerialize /apps, restore empty workspaces, mark orphan jobs."""
    from .host import standalone_mode

    if not standalone_mode():
        return {"skipped": True, "reason": "not_standalone"}

    out: dict[str, Any] = {"skipped": False}
    from .paths import data_root, playground_projects_dir
    data_root.cache_clear()
    playground_projects_dir.cache_clear()
    from . import playground as pg
    pg.PROJECTS_DIR = playground_projects_dir()

    try:
        from .store import get_sqlite_pool
        await get_sqlite_pool()
        out["sqlite"] = True
    except Exception as extra:
        log.warning("standalone boot: sqlite %s", extra)
        out["sqlite"] = False

    try:
        from .playground_publish import materialize_all
        out["materialized"] = await materialize_all()
    except Exception as extra:
        log.warning("standalone boot: materialize %s", extra)
        out["materialized"] = 0

    try:
        from .playground_workspace_backup import restore_all_workspaces_on_startup
        out["workspaces"] = await restore_all_workspaces_on_startup()
    except Exception as extra:
        log.warning("standalone boot: workspace restore %s", extra)
        out["workspaces"] = {"ok": False, "error": str(extra)}

    try:
        from .autopilot_jobs import recover_orphaned_jobs
        out["orphans"] = recover_orphaned_jobs()
    except Exception as extra:
        log.warning("standalone boot: orphan recovery %s", extra)
        out["orphans"] = 0

    try:
        from .evolution import enabled, start_gardener
        out["evolution"] = enabled()
        start_gardener()
    except Exception as extra:
        log.warning("standalone boot: evolution gardener %s", extra)
        out["evolution"] = False

    log.info("standalone boot: %s", out)
    return out
