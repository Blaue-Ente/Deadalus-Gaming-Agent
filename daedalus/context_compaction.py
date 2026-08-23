"""Pi-inspired context compaction for Daedalus sessions.

When session history grows beyond a threshold, older turns are summarized into a
compact block with file-tracking (paths read/edited during the session).

Inspired by pi's compaction + file-tracking pattern — keeps recent turns verbatim
while preserving actionable file context for multi-step coding tasks.
"""
from __future__ import annotations

import logging
import os
import re
import time
from typing import Any

log = logging.getLogger(__name__)

_RDB_PREFIX = "adn:daedalus:compact:"
_RDB_FILES_PREFIX = "adn:daedalus:files:"

# Defaults — override via env
_DEFAULT_TURN_THRESHOLD = 8
_DEFAULT_CHAR_THRESHOLD = 12_000
_KEEP_RECENT = 4


def _rdb_get(key: str) -> Any:
    from . import kv
    return kv.get(key)


def _rdb_set(key: str, value: Any) -> None:
    from . import kv
    kv.set(key, value)


def _threshold_turns() -> int:
    try:
        return max(4, int(os.environ.get("DAEDALUS_COMPACT_TURNS", str(_DEFAULT_TURN_THRESHOLD))))
    except (ValueError, TypeError):
        return _DEFAULT_TURN_THRESHOLD


def _threshold_chars() -> int:
    try:
        return max(4000, int(os.environ.get("DAEDALUS_COMPACT_CHARS", str(_DEFAULT_CHAR_THRESHOLD))))
    except (ValueError, TypeError):
        return _DEFAULT_CHAR_THRESHOLD


_FILE_PATH_RE = re.compile(
    r"(?:argos_data_nexus|artifacts|lib|scripts)/[\w./\-]+\.(?:py|ts|tsx|js|jsx|md|json|yaml|yml|html|css)"
)


def track_files_from_text(session_id: str, text: str, *, action: str = "read") -> list[str]:
    """Record file paths mentioned in tool results or markers."""
    if not session_id or not text:
        return []
    found = list(dict.fromkeys(_FILE_PATH_RE.findall(text)))
    if not found:
        return []
    key = _RDB_FILES_PREFIX + session_id
    state: dict[str, Any] = _rdb_get(key) or {"read": [], "edited": []}
    bucket = "edited" if action == "edit" else "read"
    existing = set(state.get(bucket, []))
    for p in found:
        existing.add(p)
    state[bucket] = sorted(existing)[-50:]
    state["updated_ts"] = int(time.time())
    _rdb_set(key, state)
    return found


def get_tracked_files(session_id: str) -> dict[str, list[str]]:
    return _rdb_get(_RDB_FILES_PREFIX + session_id) or {"read": [], "edited": []}


def _summarize_turns(turns: list[dict[str, Any]]) -> str:
    """Deterministic local summary (no LLM) — pi-style bullet compaction."""
    bullets: list[str] = []
    for t in turns:
        meta = t.get("metadata", {})
        role = meta.get("role", "?")
        content = (t.get("content") or "").strip()
        if not content:
            continue
        # Strip NEED_* blocks from summary — keep human-visible prose
        preview = re.sub(r"<NEED_[^>]+>", "[tool]", content)
        preview = re.sub(r"<NEED_\w+>.*?</NEED_\w+>", "[block]", preview, flags=re.DOTALL)
        preview = " ".join(preview.split())
        if len(preview) > 220:
            preview = preview[:217] + "…"
        bullets.append(f"  • [{role}] {preview}")
    return "\n".join(bullets) if bullets else "  (no substantive turns)"


def format_compaction_block(
    summary: str,
    files: dict[str, list[str]],
    compacted_count: int,
) -> str:
    """Pi-style compaction header for prompt injection."""
    read_files = files.get("read", [])[:20]
    edited_files = files.get("edited", [])[:20]
    lines = [
        "━━━ COMPACTED SESSION CONTEXT (pi-style) ━━━",
        f"Older turns summarized ({compacted_count} turn(s) compacted).",
        "",
        "### Files touched this session",
    ]
    if read_files:
        lines.append("Read: " + ", ".join(read_files))
    else:
        lines.append("Read: (none tracked)")
    if edited_files:
        lines.append("Edited: " + ", ".join(edited_files))
    else:
        lines.append("Edited: (none tracked)")
    lines.append("")
    lines.append("### Summary of earlier turns")
    lines.append(summary)
    lines.append("━━━ END COMPACTED CONTEXT ━━━")
    return "\n".join(lines)


def get_compaction_snapshot(session_id: str) -> dict[str, Any] | None:
    return _rdb_get(_RDB_PREFIX + session_id)


def maybe_compact_session_context(
    session_id: str,
    session_recent: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], str | None]:
    """Return (possibly trimmed recent turns, optional compaction block).

    If under threshold → returns input unchanged, None.
    If over threshold → keeps last N turns verbatim, summarizes the rest.
    """
    if not session_id or not session_recent:
        return session_recent, None

    total_chars = sum(len(t.get("content", "")) for t in session_recent)
    n_turns = len(session_recent)
    if n_turns < _threshold_turns() and total_chars < _threshold_chars():
        return session_recent, None

    keep = min(_KEEP_RECENT, n_turns)
    older = session_recent[:-keep] if keep < n_turns else []
    recent = session_recent[-keep:] if keep else session_recent

    summary = _summarize_turns(older)
    files = get_tracked_files(session_id)
    block = format_compaction_block(summary, files, len(older))

    # Cache compaction snapshot (for session branching)
    _rdb_set(_RDB_PREFIX + session_id, {
        "summary": summary,
        "files": files,
        "compacted_count": len(older),
        "ts": int(time.time()),
    })

    log.info(
        "context compaction: session=%s compacted=%d kept=%d chars=%d→%d",
        session_id[:10], len(older), keep, total_chars,
        sum(len(t.get("content", "")) for t in recent),
    )
    return recent, block
