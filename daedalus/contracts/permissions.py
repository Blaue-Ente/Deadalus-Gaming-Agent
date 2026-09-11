"""Permission catalog snapshot. Copied from outbox; do not import outbox."""
from __future__ import annotations

from enum import Enum

from ._base import ContractModel


class PermissionGroup(str, Enum):
    DB = "db"
    FILES = "files"
    EXTERNAL = "external"
    WORKERS = "workers"


class PermissionClass(ContractModel):
    id: str
    label: str
    group: PermissionGroup


class PermissionGrant(ContractModel):
    perm_class: str
    granted_at: int
    expires_at: int
    hours: float = 24.0
    granted_by: str = ""
    active: bool = False
    remaining_min: int = 0


class PermissionDecision(ContractModel):
    allowed: bool
    perm_class: str = ""
    fail_open: bool = False
    reason: str = ""


# Snapshot of outbox.KNOWN_PERMISSION_CLASSES — copy, do not import.
PERMISSION_CATALOG: tuple[PermissionClass, ...] = (
    PermissionClass(id="insert_trading_concepts", label="INSERT concepts", group=PermissionGroup.DB),
    PermissionClass(id="insert_signal_criteria", label="INSERT criteria", group=PermissionGroup.DB),
    PermissionClass(id="insert_patterns", label="INSERT patterns", group=PermissionGroup.DB),
    PermissionClass(id="insert_data", label="INSERT (общо)", group=PermissionGroup.DB),
    PermissionClass(id="execute_raw_sql", label="Raw SQL", group=PermissionGroup.DB),
    PermissionClass(id="modify_files", label="Промяна файлове", group=PermissionGroup.FILES),
    PermissionClass(id="create_files", label="Нови файлове", group=PermissionGroup.FILES),
    PermissionClass(id="auto_approve", label="Auto-approve", group=PermissionGroup.FILES),
    PermissionClass(id="web_search", label="Web search", group=PermissionGroup.EXTERNAL),
    PermissionClass(id="github_access", label="GitHub", group=PermissionGroup.EXTERNAL),
    PermissionClass(id="send_outbox", label="Outbox изпращане", group=PermissionGroup.EXTERNAL),
    PermissionClass(id="deep_worker", label="Deep worker", group=PermissionGroup.WORKERS),
    PermissionClass(id="analysis_worker", label="Analysis worker", group=PermissionGroup.WORKERS),
    PermissionClass(id="claude_worker", label="Claude worker", group=PermissionGroup.WORKERS),
    PermissionClass(id="planner_auto_run", label="Auto planner", group=PermissionGroup.WORKERS),
    PermissionClass(id="mesh_call", label="Iyveris Mesh", group=PermissionGroup.EXTERNAL),
)

# native_tools.PERM_MAP also uses postgres_query, which is not in the outbox catalog.
KNOWN_PERMISSION_IDS: frozenset[str] = frozenset(p.id for p in PERMISSION_CATALOG) | frozenset(
    {"postgres_query"}
)
