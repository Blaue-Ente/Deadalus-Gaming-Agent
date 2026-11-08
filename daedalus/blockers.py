"""Daedalus Blocker Tracker — P4.

Когато Дедал не може да продължи сам (3+ неуспешни опита, липсваща зависимост,
нерешим конфликт), записва блокер в Replit DB вместо да замлъква.

Маркер: <NEED_BLOCKED: reason | tried_approaches | suggested_next_steps>

UI: sidebar панел "🚧 Blockers" в /admin/daedalus
API:
  GET  /admin/daedalus/blockers          → списък
  POST /admin/daedalus/blockers/{id}/resolve  → маркира като resolved
"""
from __future__ import annotations

import json
import logging
import re
import time
import uuid
from typing import Any

log = logging.getLogger(__name__)

_BLOCKER_PREFIX = "adn:blocker:"
_BLOCKER_INDEX  = "adn:blockers:index"


# ---------------------------------------------------------------------------
# Replit DB helpers (shared pattern with outbox.py)
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
# Blocker CRUD
# ---------------------------------------------------------------------------

def push_blocker(
    reason: str,
    tried: str = "",
    next_steps: str = "",
    file_path: str = "",
    session_id: str = "",
) -> str:
    """Record a new blocker. Returns blocker_id."""
    blocker_id = f"blk_{uuid.uuid4().hex[:10]}"
    blocker: dict[str, Any] = {
        "id": blocker_id,
        "created_at": time.time(),
        "status": "open",
        "reason": reason[:1000],
        "tried": tried[:2000],
        "next_steps": next_steps[:1000],
        "file_path": file_path[:300],
        "session_id": session_id[:100],
        "resolved_at": None,
        "resolution_note": None,
    }
    _rdb_set(f"{_BLOCKER_PREFIX}{blocker_id}", blocker)

    # Update index
    index: list[str] = _rdb_get(_BLOCKER_INDEX) or []
    if blocker_id not in index:
        index.insert(0, blocker_id)
        index = index[:200]  # keep last 200
    _rdb_set(_BLOCKER_INDEX, index)

    log.info("Daedalus blocker recorded: %s — %s", blocker_id, reason[:80])
    return blocker_id


def list_blockers(status: str | None = None, *, enrich: bool = False) -> list[dict[str, Any]]:
    """Return blockers, newest first. Optionally filter by status."""
    index: list[str] = _rdb_get(_BLOCKER_INDEX) or []
    result = []
    for bid in index:
        b = _rdb_get(f"{_BLOCKER_PREFIX}{bid}")
        if b is None:
            continue
        if status and b.get("status") != status:
            continue
        if enrich:
            row = dict(b)
            row["analysis"] = analyze_blocker(b)
            result.append(row)
        else:
            result.append(b)
    return result


def resolve_blocker(blocker_id: str, note: str = "") -> dict[str, Any]:
    """Mark a blocker as resolved."""
    key = f"{_BLOCKER_PREFIX}{blocker_id}"
    b = _rdb_get(key)
    if b is None:
        return {"ok": False, "error": "blocker not found"}
    b["status"] = "resolved"
    b["resolved_at"] = time.time()
    b["resolution_note"] = note[:500]
    _rdb_set(key, b)
    log.info("Daedalus blocker resolved: %s", blocker_id)
    return {"ok": True, "blocker_id": blocker_id}


def count_open_blockers() -> int:
    """Fast count of unresolved blockers (для badge в UI)."""
    index: list[str] = _rdb_get(_BLOCKER_INDEX) or []
    count = 0
    for bid in index:
        b = _rdb_get(f"{_BLOCKER_PREFIX}{bid}")
        if b and b.get("status") == "open":
            count += 1
    return count


# ── Auto-fix heuristics (permission grants) ─────────────────────────────────

_PERM_EXTRACT_RE = re.compile(
    r"(?:NEED_CHECK_PERM:\s*|grant(?:-ne)?\s*[`'\"]?|permission\s*[`'\"]?)"
    r"([a-z][a-z0-9_]{2,48})",
    re.IGNORECASE,
)
_PERM_TOKEN_RE = re.compile(
    r"\b((?:insert|execute|modify|create|deep|planner|auto|send|web|github)[a-z0-9_]{2,40})\b",
    re.IGNORECASE,
)

# Known ids we can safely auto-grant
_KNOWN_PERM_IDS: frozenset[str] = frozenset()


def _known_perm_ids() -> frozenset[str]:
    global _KNOWN_PERM_IDS
    if not _KNOWN_PERM_IDS:
        try:
            from .outbox import KNOWN_PERMISSION_IDS
            _KNOWN_PERM_IDS = frozenset(KNOWN_PERMISSION_IDS)
        except Exception:
            _KNOWN_PERM_IDS = frozenset()
    return _KNOWN_PERM_IDS


def _blocker_blob(blocker: dict[str, Any]) -> str:
    return " ".join(
        str(blocker.get(k) or "")
        for k in ("reason", "tried", "next_steps", "file_path")
    )


def extract_permission_hints(blocker: dict[str, Any]) -> list[str]:
    """Parse blocker text for grantable permission class names."""
    blob = _blocker_blob(blocker)
    found: list[str] = []
    seen: set[str] = set()
    known = _known_perm_ids()

    def _add(raw: str) -> None:
        tok = raw.strip().lower().replace("-", "_")
        if not tok or tok in seen:
            return
        if known and tok not in known:
            # Allow dynamic insert_* even if not in catalog
            if not tok.startswith("insert_"):
                return
        seen.add(tok)
        found.append(tok)

    for m in _PERM_EXTRACT_RE.finditer(blob):
        _add(m.group(1))
    for m in _PERM_TOKEN_RE.finditer(blob):
        _add(m.group(1))

    # Common phrases
    low = blob.lower()
    if "deep worker" in low or "deep_worker" in low:
        _add("deep_worker")
    if "planner" in low and ("auto" in low or "план" in low):
        _add("planner_auto_run")
    if "raw sql" in low or "execute_raw" in low:
        _add("execute_raw_sql")
    if "web search" in low or "търсене" in low:
        _add("web_search")
    if "github" in low:
        _add("github_access")
    if "analysis worker" in low or "analysis_worker" in low:
        _add("analysis_worker")
    if "claude worker" in low or "claude_worker" in low:
        _add("claude_worker")
    if "auto approve" in low or "auto_approve" in low:
        _add("auto_approve")
    if "mesh" in low and ("call" in low or "iyveris" in low):
        _add("mesh_call")
    if any(
        p in low
        for p in (
            "modify file",
            "modify_files",
            "промяна файл",
            "edit file",
            "write file",
            "cannot write",
            "patch file",
            "fix ",
            ".js",
            ".tsx",
            ".py",
        )
    ):
        _add("modify_files")
    if any(
        p in low
        for p in ("create file", "create_files", "нов файл", "new file", "scaffold")
    ):
        _add("create_files")
    if "publish" in low or "playground publish" in low or "vite build" in low:
        _add("auto_approve")

    return found


_SLUG_RE = re.compile(
    r"(?:/admin/daedalus/playground/|playground/|slug[:\s]+|project[:\s]+)"
    r"([a-z0-9][a-z0-9-]{1,64})",
    re.IGNORECASE,
)
_SLUG_INLINE_RE = re.compile(r"\b([a-z][a-z0-9-]{2,48})\b")


def extract_playground_slug(blocker: dict[str, Any]) -> str:
    """Best-effort slug from blocker text (e.g. shooting-ducks)."""
    blob = _blocker_blob(blocker)
    for m in _SLUG_RE.finditer(blob):
        slug = m.group(1).strip().lower()
        if slug not in ("static", "vite", "expo", "unity", "projects", "playground"):
            return slug
    # Fallback: explicit /apps/{slug}/ style
    m = re.search(r"/apps/([a-z0-9][a-z0-9-]{1,64})/", blob, re.I)
    if m:
        return m.group(1).lower()
    return ""


def classify_blocker(blocker: dict[str, Any]) -> str:
    """permission | code | infra | owner | unknown"""
    blob = _blocker_blob(blocker).lower()
    if extract_permission_hints(blocker):
        return "permission"
    if any(
        p in blob
        for p in (
            "package-firewall",
            "firewall blocks npm",
            "npm install failed",
            "gateway timeout",
            "http 504",
            "dns error",
            "package-firewall.replit",
        )
    ):
        return "infra"
    if any(
        p in blob
        for p in (
            "николай",
            "owner",
            "ръчно",
            "manually",
            "ui at",
            "отвори",
            "потвърди",
            "confirm",
        )
    ):
        return "owner"
    if any(
        p in blob
        for p in (
            "syntax",
            "variable:",
            "cannot access",
            "traceback",
            ".js",
            ".tsx",
            ".py",
            "compile",
            "lint",
            "gamescene",
        )
    ):
        return "code"
    return "unknown"


def suggest_remediation(blocker: dict[str, Any]) -> str:
    """Human-readable next step when auto-fix cannot run."""
    cat = classify_blocker(blocker)
    perms = extract_permission_hints(blocker)
    slug = extract_playground_slug(blocker)
    if cat == "permission" and perms:
        return f"Grant: {', '.join(perms)} (Permissions панел или Auto-fix)."
    if cat == "infra":
        if slug:
            return (
                f"Replit npm firewall: смени `{slug}` на Kind=static в Playground "
                f"(или натисни Remediate ако има index.html без npm)."
            )
        return (
            "Replit npm firewall: смени проекта на Kind=static или публикувай без npm build."
        )
    if cat == "code":
        fp = (blocker.get("file_path") or "").strip()
        if fp:
            return f"Кодов fix в `{fp}` — Дедал трябва modify_files + create_files."
        return "Кодов fix — дай modify_files, create_files, auto_approve и пусни Дедал отново."
    if cat == "owner":
        return (blocker.get("next_steps") or "Следвай стъпките в „Нужно“.").strip()
    return blocker.get("next_steps") or "Resolve ръчно след като проблемът е отстранен."


def analyze_blocker(blocker: dict[str, Any]) -> dict[str, Any]:
    """Enrich a blocker row for admin UI."""
    perms = extract_permission_hints(blocker)
    cat = classify_blocker(blocker)
    return {
        "category": cat,
        "suggested_permissions": perms,
        "auto_fixable": bool(perms),
        "infra_remediable": cat == "infra" and bool(extract_playground_slug(blocker)),
        "playground_slug": extract_playground_slug(blocker),
        "remediation": suggest_remediation(blocker),
    }


def blockers_summary(blockers: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Aggregate open blocker stats for dashboard."""
    rows = blockers if blockers is not None else list_blockers(status="open")
    by_cat: dict[str, int] = {}
    fixable = 0
    infra = 0
    for b in rows:
        if b.get("status") != "open":
            continue
        a = analyze_blocker(b)
        by_cat[a["category"]] = by_cat.get(a["category"], 0) + 1
        if a["auto_fixable"]:
            fixable += 1
        if a["infra_remediable"]:
            infra += 1
    return {
        "open_count": len(rows),
        "by_category": by_cat,
        "permission_auto_fixable": fixable,
        "infra_remediable": infra,
        "grant_all_recommended": [
            "modify_files",
            "create_files",
            "auto_approve",
            "deep_worker",
            "web_search",
            "mesh_call",
            "planner_auto_run",
        ],
    }


async def remediate_infra_blocker(blocker_id: str, *, dry_run: bool = False) -> dict[str, Any]:
    """For npm-firewall blockers: switch playground project to static kind.

    ``dry_run`` answers what WOULD change ({ok, slug, old_kind, kind, changes_kind}) and changes nothing."""
    key = f"{_BLOCKER_PREFIX}{blocker_id}"
    b = _rdb_get(key)
    if b is None:
        return {"ok": False, "error": "blocker not found"}
    if b.get("status") != "open":
        return {"ok": False, "error": "blocker already resolved"}

    slug = extract_playground_slug(b)
    if not slug:
        return {"ok": False, "error": "no_playground_slug", "message": "Не намерих playground slug в текста."}

    blob = _blocker_blob(b).lower()
    if classify_blocker(b) != "infra" and "npm" not in blob and "firewall" not in blob:
        return {
            "ok": False,
            "error": "not_infra",
            "message": "Remediate работи само за npm/firewall blockers.",
        }

    try:
        from . import playground as pg

        proj = await pg.get_project(slug)
        if not proj:
            return {"ok": False, "error": "project_not_found", "slug": slug}
        old_kind = proj.get("kind", "static")
        if dry_run:
            return {"ok": True, "dry_run": True, "slug": slug, "old_kind": old_kind, "kind": "static",
                    "changes_kind": old_kind != "static"}
        if old_kind == "static":
            note = f"infra-remediate: {slug} вече е static"
            resolve_blocker(blocker_id, note=note)
            return {"ok": True, "slug": slug, "kind": "static", "already_static": True, "note": note}

        await pg.update_project(slug, kind="static")
        note = f"infra-remediate: {slug} {old_kind}→static (npm firewall workaround)"
        resolve_blocker(blocker_id, note=note)
        return {
            "ok": True,
            "slug": slug,
            "old_kind": old_kind,
            "kind": "static",
            "note": note,
        }
    except Exception as exc:
        log.warning("infra remediate failed for %s: %s", blocker_id, exc)
        return {"ok": False, "error": str(exc)[:300], "slug": slug}


def auto_fix_blocker(blocker_id: str, *, hours: float = 8.0) -> dict[str, Any]:
    """Try to resolve a blocker by granting detected permissions."""
    key = f"{_BLOCKER_PREFIX}{blocker_id}"
    b = _rdb_get(key)
    if b is None:
        return {"ok": False, "error": "blocker not found"}
    if b.get("status") != "open":
        return {"ok": False, "error": "blocker already resolved", "status": b.get("status")}

    hints = extract_permission_hints(b)
    if not hints:
        analysis = analyze_blocker(b)
        return {
            "ok": False,
            "error": "no_auto_fix",
            "message": "Не открих permission за автоматично grant — опитай Resolve ръчно.",
            "blocker_id": blocker_id,
            "analysis": analysis,
        }

    from .outbox import grant_permissions_bulk

    grant_permissions_bulk(hints, hours=hours, granted_by="blocker_autofix")
    note = f"auto-fix: granted {', '.join(hints)} ({hours}ч)"
    resolve_blocker(blocker_id, note=note)
    return {
        "ok": True,
        "blocker_id": blocker_id,
        "granted": hints,
        "hours": hours,
        "note": note,
    }


def auto_fix_all_open(*, hours: float = 8.0) -> dict[str, Any]:
    """Auto-fix all open blockers where permission hints are detected."""
    open_blockers = list_blockers(status="open")
    fixed: list[dict] = []
    skipped: list[dict] = []
    for b in open_blockers:
        bid = b.get("id", "")
        if not bid:
            continue
        res = auto_fix_blocker(bid, hours=hours)
        if res.get("ok"):
            fixed.append({"id": bid, "granted": res.get("granted", [])})
        else:
            skipped.append({"id": bid, "error": res.get("error"), "message": res.get("message", "")})
    return {
        # ok when there was nothing to fix, or at least one blocker was actually fixed.
        "ok": (not open_blockers) or bool(fixed),
        **({"error": "no_fix_available"} if open_blockers and not fixed else {}),
        "fixed_count": len(fixed),
        "skipped_count": len(skipped),
        "fixed": fixed,
        "skipped": skipped,
    }
