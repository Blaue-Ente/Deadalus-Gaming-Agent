"""Session branching for Daedalus (Phase 3 — pi-inspired).

Fork a session to explore an alternative approach without losing the parent
conversation. Child sessions inherit recent history + compaction snapshot.
"""
from __future__ import annotations

import logging
import time
import uuid
from typing import Any

log = logging.getLogger(__name__)

_RDB_BRANCH_PREFIX = "adn:daedalus:branch:"
_RDB_TREE_PREFIX = "adn:daedalus:branch_tree:"


def _rdb_get(key: str) -> Any:
    from . import kv
    return kv.get(key)


def _rdb_set(key: str, value: Any) -> None:
    from . import kv
    kv.set(key, value)


def branch_session(
    parent_session_id: str,
    *,
    label: str = "",
    memory: Any | None = None,
) -> dict[str, Any]:
    """Create a child session forked from ``parent_session_id``.

  Returns metadata dict with new ``session_id``, parent link, and copied turn count.
    """
    child_id = f"branch-{uuid.uuid4().hex[:10]}"
    now = int(time.time())

    # Copy recent history into child session via memory layer
    copied = 0
    if memory is not None:
        try:
            history = memory.session_history(parent_session_id, limit=8)
            for turn in history:
                role = turn.get("metadata", {}).get("role", "user")
                content = turn.get("content", "")
                if content:
                    memory.capture_turn(child_id, role, content)
                    copied += 1
        except Exception as exc:
            log.warning("branch history copy failed: %s", exc)

    # Pull compaction snapshot if available
    from .context_compaction import get_compaction_snapshot
    compact_snap = get_compaction_snapshot(parent_session_id)

    meta: dict[str, Any] = {
        "session_id": child_id,
        "parent_session_id": parent_session_id,
        "label": (label or "fork")[:120],
        "created_ts": now,
        "turns_copied": copied,
        "compaction_snapshot": compact_snap,
    }
    _rdb_set(_RDB_BRANCH_PREFIX + child_id, meta)

    # Update tree index
    tree_key = _RDB_TREE_PREFIX + parent_session_id
    tree: list[str] = _rdb_get(tree_key) or []
    tree.append(child_id)
    _rdb_set(tree_key, tree[-20:])

    log.info("session branch: parent=%s child=%s copied=%d", parent_session_id[:10], child_id, copied)
    return meta


def get_branch_meta(session_id: str) -> dict[str, Any] | None:
    return _rdb_get(_RDB_BRANCH_PREFIX + session_id)


def list_child_branches(parent_session_id: str) -> list[dict[str, Any]]:
    tree_key = _RDB_TREE_PREFIX + parent_session_id
    ids: list[str] = _rdb_get(tree_key) or []
    out: list[dict[str, Any]] = []
    for sid in ids:
        m = get_branch_meta(sid)
        if m:
            out.append(m)
    return out


def build_branch_context_block(session_id: str) -> str | None:
    """Inject branch lineage into prompt if this is a forked session."""
    meta = get_branch_meta(session_id)
    if not meta:
        return None
    parent = meta.get("parent_session_id", "?")
    label = meta.get("label", "fork")
    lines = [
        "━━━ SESSION BRANCH ━━━",
        f"This session is a branch (label: {label}) forked from {parent}.",
        "You may explore an alternative approach; the parent session is unchanged.",
    ]
    snap = meta.get("compaction_snapshot")
    if snap and snap.get("summary"):
        lines.append("")
        lines.append("Inherited compaction summary:")
        lines.append(snap["summary"][:1500])
    lines.append("━━━ END SESSION BRANCH ━━━")
    return "\n".join(lines)
