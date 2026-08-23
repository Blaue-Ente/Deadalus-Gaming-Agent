"""NEED_EDIT — surgical search/replace edits for ADN codebase (pi edit-diff pattern).

Format (>>> delimiter allows > in content):
  <NEED_EDIT: path/to/file.py | exact_search_text ||| replacement_text >>>

Multiple NEED_EDIT markers per response are supported.
Converts successful edits into NEED_PROPOSE_START-compatible blocks for the
existing sandbox → propose pipeline.
"""
from __future__ import annotations

import logging
import re
from typing import Any

log = logging.getLogger(__name__)

NEED_EDIT_RE = re.compile(
    r"<NEED_EDIT:\s*([^|\n]{1,300})\s*\|\s*([\s\S]*?)\|\|\|([\s\S]*?)>>>",
    re.DOTALL,
)


def extract_edit_markers(raw: str) -> list[dict[str, str]]:
    """Parse all NEED_EDIT markers from LLM output."""
    if not raw or "NEED_EDIT" not in raw:
        return []
    edits: list[dict[str, str]] = []
    for m in NEED_EDIT_RE.finditer(raw):
        path = m.group(1).strip()
        search = m.group(2)
        replace = m.group(3)
        if path and search:
            edits.append({
                "file_path": path,
                "search": search,
                "replace": replace,
                "raw": m.group(0),
            })
    return edits


def apply_edit_diff(current: str, search: str, replace: str) -> tuple[str | None, str | None]:
    """Apply one search/replace. Returns (new_content, error).

    Uses exact match first; falls back to strip()-normalized match on lines.
    """
    if search not in current:
        # Try line-trimmed match (common LLM whitespace drift)
        norm_search = search.strip()
        if norm_search and norm_search in current:
            new_content = current.replace(norm_search, replace, 1)
            return new_content, None
        return None, (
            f"Search text not found in file ({len(search)} chars). "
            "Emit NEED_READ_FILE first to get exact content."
        )
    count = current.count(search)
    if count > 1:
        return None, (
            f"Search text appears {count} times — make search more specific "
            "(include surrounding lines)."
        )
    return current.replace(search, replace, 1), None


def edit_to_propose_block(
    file_path: str,
    new_content: str,
    reason: str,
    *,
    risk_level: int = 2,
) -> dict[str, Any]:
    """Convert a surgical edit into a propose-loop block dict."""
    return {
        "file_path": file_path,
        "reason": reason,
        "risk_level": risk_level,
        "risk_rationale": "surgical NEED_EDIT — partial file change",
        "test_code": None,
        "code": new_content,
        "confidence": 0.85,
        "evidence_count": 2,
        "edit_type": "diff",
    }


async def process_need_edit(
    raw: str,
    read_file_fn: Any,
    *,
    session_id: str = "",
    default_reason: str = "Surgical edit via NEED_EDIT marker",
) -> tuple[list[str], list[dict[str, Any]]]:
    """Process all NEED_EDIT markers. Returns (result_texts, propose_blocks)."""
    from .context_compaction import track_files_from_text

    edits = extract_edit_markers(raw)
    if not edits:
        return [], []

    results: list[str] = []
    blocks: list[dict[str, Any]] = []

    for edit in edits:
        path = edit["file_path"]
        r = read_file_fn(path)
        if not r.get("ok"):
            results.append(
                f"━━━ NEED_EDIT FAILED — {path} ━━━\n"
                f"Could not read file: {r.get('error')}"
            )
            continue

        current = r.get("content", "")
        new_content, err = apply_edit_diff(current, edit["search"], edit["replace"])
        if err:
            results.append(f"━━━ NEED_EDIT FAILED — {path} ━━━\n{err}")
            continue

        blocks.append(edit_to_propose_block(path, new_content, default_reason))
        if session_id:
            track_files_from_text(session_id, path, action="edit")
        results.append(
            f"━━━ NEED_EDIT OK — {path} ━━━\n"
            f"Applied surgical edit ({len(edit['search'])} → {len(edit['replace'])} chars). "
            f"Routing through sandbox validation…"
        )
        log.info("NEED_EDIT applied: %s", path)

    return results, blocks
