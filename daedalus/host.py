"""Optional ADN host adapters.

Daedalus is a standalone product. When it runs next to ArgosDataNexus it
reuses ADN's Postgres pool, storage, auth, and MeshRouter. When ADN is
absent (or ``DAEDALUS_STANDALONE=1``), callers get SQLite + direct LLM —
never a crash at import.

ADN's own process must leave ``DAEDALUS_STANDALONE`` unset so Playground
keeps using Postgres. Do not flip that flag inside ADN.
"""
from __future__ import annotations

import importlib
import logging
import os
from typing import Any

log = logging.getLogger(__name__)


def adn_available() -> bool:
    try:
        importlib.import_module("argos_data_nexus")
        return True
    except ImportError:
        return False


def standalone_mode() -> bool:
    flag = os.environ.get("DAEDALUS_STANDALONE", "").strip().lower()
    if flag in ("1", "true", "yes", "on"):
        return True
    if flag in ("0", "false", "no", "off"):
        return False
    return not adn_available()


def use_adn_db() -> bool:
    """Postgres via ADN unless standalone (unless DAEDALUS_USE_ADN_DB=1)."""
    override = os.environ.get("DAEDALUS_USE_ADN_DB", "").strip().lower()
    if override in ("1", "true", "yes", "on"):
        return adn_available()
    if override in ("0", "false", "no", "off"):
        return False
    return adn_available() and not standalone_mode()


def _adn(mod: str) -> Any:
    return importlib.import_module(mod)


def _adn_attr(mod: str, name: str, default: Any = None) -> Any:
    try:
        return getattr(_adn(mod), name)
    except Exception:
        return default


async def get_pool():
    if use_adn_db():
        try:
            db = _adn("argos_data_nexus.db")
            return await db.get_pool()
        except Exception as exc:
            log.warning("ADN Postgres unavailable (%s); using SQLite", exc)
    from .store import get_sqlite_pool
    return await get_sqlite_pool()


async def get_top_error_patterns(*args: Any, **kwargs: Any):
    if not use_adn_db():
        return []
    db = _adn("argos_data_nexus.db")
    return await db.get_top_error_patterns(*args, **kwargs)


def pg_upsert_proposal(proposal: dict) -> None:
    if not use_adn_db():
        return None
    db = _adn("argos_data_nexus.db")
    return db.pg_upsert_proposal(proposal)


def pg_list_proposals(*args: Any, **kwargs: Any):
    if not use_adn_db():
        return []
    db = _adn("argos_data_nexus.db")
    return db.pg_list_proposals(*args, **kwargs)


def pg_get_proposal(*args: Any, **kwargs: Any):
    if not use_adn_db():
        return None
    db = _adn("argos_data_nexus.db")
    return db.pg_get_proposal(*args, **kwargs)


def query_mesh_stats(*args: Any, **kwargs: Any):
    if not use_adn_db():
        return []
    db = _adn("argos_data_nexus.db")
    return db.query_mesh_stats(*args, **kwargs)


def log_chromadb_backup(*args: Any, **kwargs: Any):
    if not use_adn_db():
        return None
    db = _adn("argos_data_nexus.db")
    return db.log_chromadb_backup(*args, **kwargs)


def upsert_discovery(*args: Any, **kwargs: Any):
    if not use_adn_db():
        return None
    db = _adn("argos_data_nexus.db")
    return db.upsert_discovery(*args, **kwargs)


def current_user(request: Any) -> Any:
    if standalone_mode() or not adn_available():
        return None
    try:
        admin = _adn("argos_data_nexus.admin")
        return admin._current_user(request)
    except Exception:
        return None


try:
    from argos_data_nexus.admin import require_admin  # FastAPI Depends
    from argos_data_nexus.auth import require_api_key
except ImportError:  # standalone — knowledge-book routes stay ADN-hosted
    def require_admin(*_a: Any, **_k: Any) -> Any:  # type: ignore[misc]
        raise RuntimeError("ADN admin auth is not available in standalone mode")

    def require_api_key(*_a: Any, **_k: Any) -> Any:  # type: ignore[misc]
        raise RuntimeError("ADN API auth is not available in standalone mode")


def storage():
    if standalone_mode() or not adn_available():
        from .local_storage import LocalObjectStorage
        return LocalObjectStorage()
    return _adn("argos_data_nexus.storage_service")


def github():
    try:
        return _adn("argos_data_nexus.github_client")
    except Exception:
        from . import playground_github as gh
        return gh


def keys():
    if standalone_mode() or not adn_available():
        raise RuntimeError("ADN keys helper is not available in standalone mode")
    return _adn("argos_data_nexus.keys")


def mesh_router():
    if standalone_mode() or not adn_available():
        raise RuntimeError("MeshRouter is ADN-hosted; standalone uses daedalus.llm_client")
    return _adn("argos_data_nexus.modules.mesh_router")
