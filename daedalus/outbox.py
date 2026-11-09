"""Daedalus Outbox — async proposals, data batches, progress, permissions.

Persistent storage via Replit DB. Powers:
- Data proposals (trading_concepts / signal_criteria batch inserts w/o code sandbox)
- Progress tracking (live UI bar while Дедал works)
- Granted permissions (skip re-asking for same task class)
- Async messages to Николай even without an active chat session
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Replit DB helpers (shared pattern with continuity.py)
# ---------------------------------------------------------------------------

def _rdb_get(key: str) -> Any:
    from . import kv
    return kv.get(key)




def _rdb_set(key: str, value: Any) -> None:
    from . import kv
    kv.set(key, value)




def _rdb_delete(key: str) -> None:
    from . import kv
    kv.delete(key)




# ---------------------------------------------------------------------------
# Outbox — async proposals / messages from Дедал
# Types: "data_batch" | "message" | "question"
# ---------------------------------------------------------------------------

_OUTBOX_PREFIX = "adn:outbox:"
_OUTBOX_INDEX  = "adn:outbox:index"
_OUTBOX_DEDUP  = "adn:outbox:dedup:"   # dedup_key → item_id


def push_outbox(
    *,
    type: str,
    title: str,
    body: str = "",
    category: str = "",
    payload: Any = None,
    session_id: str | None = None,
    dedup_key: str | None = None,
    require_send_perm: bool = False,
) -> dict:
    """Create an outbox item (Дедал → Николай, no active session required).

    dedup_key: ако е подаден и вече има "pending" item с този ключ —
               връща съществуващия без да създава дубликат.
               Използвай stable string: напр. "proposal:{sha256_of_title}".

    require_send_perm: when True, blocks proactive messages unless send_outbox is granted.
    System notifications (planner, bg errors) should pass require_send_perm=False.
    """
    if require_send_perm and not check_permission("send_outbox"):
        log.info("outbox: blocked push — send_outbox not granted (%r)", title[:40])
        return {
            "id": "",
            "type": type,
            "title": title,
            "body": body,
            "status": "blocked",
            "error": "send_outbox permission not granted",
        }
    # ── Dedup check ──────────────────────────────────────────────────────────
    if dedup_key:
        existing_id = _rdb_get(_OUTBOX_DEDUP + dedup_key)
        if existing_id:
            existing = _rdb_get(_OUTBOX_PREFIX + existing_id)
            if existing and existing.get("status") == "pending":
                log.debug("outbox dedup hit: key=%r → %s", dedup_key, existing_id)
                return existing
            # Key exists but item is gone/applied — clean up stale key
            _rdb_delete(_OUTBOX_DEDUP + dedup_key)

    item_id = "ob_" + uuid.uuid4().hex[:10]
    now = int(time.time())
    item: dict[str, Any] = {
        "id": item_id,
        "type": type,          # "data_batch" | "message" | "question"
        "title": title.strip()[:120],
        "body": body.strip()[:2000],
        "category": category,  # e.g. "trading_concepts", "signal_criteria"
        "payload": payload,    # JSON-serializable data for data_batch
        "status": "pending",   # pending | applied | dismissed
        "session_id": session_id or "",
        "created_at": now,
        "updated_at": now,
        "apply_result": None,
        "dedup_key": dedup_key or "",
    }
    _rdb_set(_OUTBOX_PREFIX + item_id, item)
    index: list[str] = _rdb_get(_OUTBOX_INDEX) or []
    index = [item_id] + [i for i in index if i != item_id]
    _rdb_set(_OUTBOX_INDEX, index[:500])
    if dedup_key:
        _rdb_set(_OUTBOX_DEDUP + dedup_key, item_id)
    log.info("outbox: pushed %s (%s) %r", item_id, type, title[:40])
    return item


def get_outbox_item(item_id: str) -> dict | None:
    return _rdb_get(_OUTBOX_PREFIX + item_id)


def list_outbox(status: str | None = None, limit: int = 50) -> list[dict]:
    index: list[str] = _rdb_get(_OUTBOX_INDEX) or []
    out: list[dict] = []
    for oid in index[:300]:
        item = get_outbox_item(oid)
        if not item:
            continue
        if status and item.get("status") != status:
            continue
        out.append(item)
        if len(out) >= limit:
            break
    return out


def update_outbox_item(item_id: str, **kwargs) -> dict | None:
    item = get_outbox_item(item_id)
    if not item:
        return None
    for k, v in kwargs.items():
        item[k] = v
    item["updated_at"] = int(time.time())
    _rdb_set(_OUTBOX_PREFIX + item_id, item)
    # Clean up dedup key when item is no longer pending
    new_status = item.get("status", "pending")
    if new_status != "pending" and item.get("dedup_key"):
        _rdb_delete(_OUTBOX_DEDUP + item["dedup_key"])
    return item


def pending_count() -> int:
    return sum(1 for item in list_outbox(status="pending", limit=200))


# R2 (retired, owner sign-off 2026-11-09): applying a data_batch item wrote into the old ADN host's trading tables
# (and its "raw_sql" category ran arbitrary SQL). Standalone Daedalus has neither, so items are listed and dismissed only.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Progress tracking — live status while Дедал works
# ---------------------------------------------------------------------------

_PROGRESS_KEY = "adn:progress:current"


def set_progress(
    task_name: str,
    pct: float,
    message: str,
    session_id: str = "",
) -> None:
    """Record current Дедал progress. Called from NEED_PROGRESS marker handler."""
    _rdb_set(_PROGRESS_KEY, {
        "task_name": task_name,
        "pct": max(0.0, min(1.0, float(pct))),
        "message": message.strip()[:200],
        "session_id": session_id,
        "updated_at": int(time.time()),
        "active": pct < 1.0,
    })


def get_progress() -> dict:
    p = _rdb_get(_PROGRESS_KEY) or {}
    # Auto-expire after 10 min of silence
    if p and (int(time.time()) - p.get("updated_at", 0)) > 600:
        p["active"] = False
    return p


def clear_progress() -> None:
    _rdb_delete(_PROGRESS_KEY)


# ---------------------------------------------------------------------------
# Granted permissions — Дедал skips asking for pre-approved task classes
# ---------------------------------------------------------------------------

_PERMS_KEY = "adn:granted_perms"

# Canonical permission classes — shown in UI + bulk grant.
KNOWN_PERMISSION_CLASSES: list[dict[str, str]] = [
    # DB inserts
    {"id": "insert_trading_concepts", "label": "INSERT concepts", "group": "db"},
    {"id": "insert_signal_criteria",  "label": "INSERT criteria", "group": "db"},
    {"id": "insert_patterns",         "label": "INSERT patterns", "group": "db"},
    {"id": "insert_data",             "label": "INSERT (общо)",   "group": "db"},
    {"id": "execute_raw_sql",         "label": "Raw SQL",         "group": "db"},
    # Files / code
    {"id": "modify_files",            "label": "Промяна файлове", "group": "files"},
    {"id": "create_files",            "label": "Нови файлове",   "group": "files"},
    {"id": "auto_approve",            "label": "Auto-approve",    "group": "files"},
    # External
    {"id": "web_search",              "label": "Web search",     "group": "external"},
    {"id": "github_access",           "label": "GitHub",         "group": "external"},
    {"id": "send_outbox",             "label": "Outbox изпращане", "group": "external"},
    # Workers / planner
    {"id": "deep_worker",             "label": "Deep worker",    "group": "workers"},
    {"id": "analysis_worker",         "label": "Analysis worker", "group": "workers"},
    {"id": "claude_worker",           "label": "Claude worker",  "group": "workers"},
    {"id": "planner_auto_run",        "label": "Auto planner",   "group": "workers"},
    # Mesh
    {"id": "mesh_call",               "label": "Iyveris Mesh",   "group": "external"},
    # Builder tools. On by default for Playground projects (see DEFAULT_GRANTED_CLASSES); revocable.
    {"id": "run_command",             "label": "Run commands",   "group": "builder"},
    {"id": "write_file",              "label": "Write files",    "group": "builder"},
    {"id": "spawn_subagent",          "label": "Sub-agents",     "group": "builder"},
]

# Classes granted by default for Playground work so a build keeps its tools without a prior click. The default
# is shown in Permissions as an active row (granted_by "default:playground", no expiry) and the owner can revoke
# it; revoking survives restarts. It never covers a path outside the Playground (see native_tools.before_tool_call).
DEFAULT_GRANTED_CLASSES: frozenset[str] = frozenset({"run_command", "write_file", "spawn_subagent"})
_DEFAULT_REVOKED_KEY = "adn:default_perm_revoked"


def _default_revoked() -> set[str]:
    raw = _rdb_get(_DEFAULT_REVOKED_KEY)
    return {str(x) for x in raw} if isinstance(raw, (list, tuple)) else set()


def default_grant_active(perm_class: str) -> bool:
    return perm_class in DEFAULT_GRANTED_CLASSES and perm_class not in _default_revoked()

# Worker slot → permission class (enforced in workers.py)
WORKER_SLOT_PERMISSIONS: dict[str, str] = {
    "deep": "deep_worker",
    "analysis": "analysis_worker",
    "claude": "claude_worker",
}

KNOWN_PERMISSION_IDS: list[str] = [p["id"] for p in KNOWN_PERMISSION_CLASSES]


def permission_catalog() -> list[dict[str, str]]:
    """Return known permission classes for admin UI."""
    return list(KNOWN_PERMISSION_CLASSES)


def grant_permission(perm_class: str, hours: float = 24.0, granted_by: str = "owner") -> dict:
    """Grant Дедал permission to perform perm_class actions without re-asking.

    Examples of perm_class:
      insert_trading_concepts   — INSERT into trading_concepts without confirm
      insert_signal_criteria    — INSERT into signal_criteria without confirm
      insert_patterns           — INSERT into asset_behavior_patterns
      execute_raw_sql           — execute arbitrary read SQL (write still needs confirm)
    """
    perms: dict = _rdb_get(_PERMS_KEY) or {}
    now = int(time.time())
    if perm_class in DEFAULT_GRANTED_CLASSES:
        revoked = _default_revoked()
        if perm_class in revoked:  # an explicit grant after a revoke switches the default back on
            revoked.discard(perm_class)
            _rdb_set(_DEFAULT_REVOKED_KEY, sorted(revoked))
    perms[perm_class] = {
        "granted_at": now,
        "expires_at": int(now + hours * 3600),
        "granted_by": granted_by,
        "hours": hours,
    }
    _rdb_set(_PERMS_KEY, perms)
    log.info("outbox: permission granted %r for %.1fh", perm_class, hours)
    return perms[perm_class]


def revoke_permission(perm_class: str) -> bool:
    perms: dict = _rdb_get(_PERMS_KEY) or {}
    changed = False
    if perm_class in perms:
        del perms[perm_class]
        _rdb_set(_PERMS_KEY, perms)
        changed = True
    if default_grant_active(perm_class):
        revoked = _default_revoked()
        revoked.add(perm_class)
        _rdb_set(_DEFAULT_REVOKED_KEY, sorted(revoked))
        changed = True
    if changed:
        log.info("outbox: permission revoked %r", perm_class)
    return changed


def check_permission(perm_class: str, *, default_ok: bool = True) -> bool:
    """True if perm_class is granted and not expired, or covered by a still-active default grant.

    ``default_ok=False`` asks for an explicit grant only (used when a call leaves the Playground).
    """
    perms: dict = _rdb_get(_PERMS_KEY) or {}
    p = perms.get(perm_class)
    if p and int(time.time()) <= p.get("expires_at", 0):
        return True
    # An expired explicit grant is left in place (list_permissions shows it as inactive, renew replaces it): a
    # read check must not rewrite the whole permission map and race a concurrent grant or revoke.
    return bool(default_ok and default_grant_active(perm_class))


def has_insert_permission(category: str) -> bool:
    """True if insert for category is allowed (specific or generic insert_data)."""
    if check_permission("insert_data"):
        return True
    cat = (category or "").strip()
    if not cat:
        return False
    return check_permission(f"insert_{cat}")


def format_permission_denied(perm_class: str, *, tool: str = "") -> str:
    """Standard tool-result block when a permission gate blocks an action."""
    tool_line = f"Tool       : {tool}\n" if tool else ""
    return (
        f"━━━ TOOL RESULTS — PERMISSION DENIED ━━━\n"
        f"{tool_line}"
        f"Permission : {perm_class}\n"
        f"Status     : NOT GRANTED\n\n"
        f"Emit <NEED_CHECK_PERM: {perm_class}> or ask Nikolai to grant from Permissions panel."
    )


def list_permissions() -> list[dict]:
    perms: dict = _rdb_get(_PERMS_KEY) or {}
    now = int(time.time())
    out = []
    for cls, p in perms.items():
        expires = p.get("expires_at", 0)
        out.append({
            "perm_class": cls,
            "granted_at": p.get("granted_at"),
            "expires_at": expires,
            "hours": p.get("hours", 24),
            "granted_by": p.get("granted_by", ""),
            "active": now < expires,
            "remaining_min": max(0, (expires - now) // 60),
        })
    out.sort(key=lambda x: x.get("granted_at", 0), reverse=True)
    return out


def default_grants() -> list[dict]:
    """The standing Playground defaults, listed beside (never mixed into) the owner's explicit grants."""
    revoked = _default_revoked()
    explicit = _rdb_get(_PERMS_KEY) or {}
    return [
        {
            "perm_class": cls, "granted_by": "default:playground", "default": True,
            "active": cls not in revoked, "expires_at": None,
            "overridden_by_explicit_grant": cls in explicit,
        }
        for cls in sorted(DEFAULT_GRANTED_CLASSES)
    ]


def grant_permissions_bulk(
    perm_classes: list[str],
    hours: float = 24.0,
    granted_by: str = "owner",
) -> dict[str, Any]:
    """Grant multiple permission classes at once."""
    granted: list[str] = []
    for cls in perm_classes:
        c = (cls or "").strip()
        if not c:
            continue
        grant_permission(c, hours=hours, granted_by=granted_by)
        granted.append(c)
    return {"ok": True, "granted": granted, "hours": hours, "count": len(granted)}


def grant_all_known_permissions(
    hours: float = 24.0,
    granted_by: str = "owner",
) -> dict[str, Any]:
    """Grant every known permission class (Разреши всички)."""
    return grant_permissions_bulk(KNOWN_PERMISSION_IDS, hours=hours, granted_by=granted_by)


def renewable_permission_ids() -> list[str]:
    """Known classes that are missing or expired (a standing default that already covers one is not renewable)."""
    now = int(time.time())
    perms: dict = _rdb_get(_PERMS_KEY) or {}
    out: list[str] = []
    for pid in KNOWN_PERMISSION_IDS:
        p = perms.get(pid)
        if default_grant_active(pid) and not p:
            continue  # the standing default already covers it; do not turn it into a 24h grant
        if not p or now >= int(p.get("expires_at", 0)):
            out.append(pid)
    return out


def renew_expired_permissions(hours: float = 24.0, granted_by: str = "owner") -> dict[str, Any]:
    """Re-grant all known classes that are missing or expired."""
    to_renew = renewable_permission_ids()
    if not to_renew:
        return {"ok": True, "granted": [], "hours": hours, "count": 0, "message": "nothing to renew"}
    return grant_permissions_bulk(to_renew, hours=hours, granted_by=granted_by)
