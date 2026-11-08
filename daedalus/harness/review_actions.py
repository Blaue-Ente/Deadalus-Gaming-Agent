"""Review panel actions: bulk permissions, blocker fixes, proposal and bundle detail, approve-all.

These move authority or change the owner's work, so every one of them:
* needs an explicit ``confirm`` (the browser asks the owner first; a script cannot do it by accident),
* decides on the SERVER what it acts on (the browser names a blocker, never a permission or a path),
* answers codes and numbers, so the Studio says it in the owner's language,
* clamps the grant length to a sane range. Bulk grants are recorded as granted by the owner; grants made through a
  blocker keep the source label the blocker code has always used (so an audit can tell them apart)."""
from __future__ import annotations

from typing import Any

HOURS_MIN, HOURS_MAX = 0.25, 72.0
DIFF_LIMIT = 60_000                      # characters of a diff shown inline; the rest is reported as truncated


def clamp_hours(hours: Any, default: float) -> float:
    try:
        value = float(hours)
    except (TypeError, ValueError):
        return default
    if value != value:                   # NaN
        return default
    return max(HOURS_MIN, min(HOURS_MAX, value))


def _need(confirm: Any) -> dict[str, Any] | None:
    return None if confirm is True else {"ok": False, "error": "confirm_required"}


# ---- permissions ------------------------------------------------------------------------------------------
def permissions_preview() -> dict[str, Any]:
    from .. import outbox

    return {"ok": True, "known": list(outbox.KNOWN_PERMISSION_IDS), "renewable": outbox.renewable_permission_ids(),
            "default_hours": 24.0, "min_hours": HOURS_MIN, "max_hours": HOURS_MAX}


def grant_all(hours: Any, confirm: Any) -> dict[str, Any]:
    from .. import outbox

    if (refused := _need(confirm)):
        return refused
    h = clamp_hours(hours, 24.0)
    res = outbox.grant_all_known_permissions(hours=h, granted_by="owner")
    return {"ok": True, "granted": res.get("granted", []), "count": res.get("count", 0), "hours": h}


def renew_expired(hours: Any, confirm: Any) -> dict[str, Any]:
    from .. import outbox

    if (refused := _need(confirm)):
        return refused
    h = clamp_hours(hours, 24.0)
    res = outbox.renew_expired_permissions(hours=h, granted_by="owner")
    return {"ok": True, "granted": res.get("granted", []), "count": res.get("count", len(res.get("granted", []))), "hours": h}


# ---- blockers ----------------------------------------------------------------------------------------------
def blockers_preview() -> dict[str, Any]:
    from .. import blockers

    s = blockers.blockers_summary()
    return {"ok": True, "open": s.get("open_count", 0), "auto_fixable": s.get("permission_auto_fixable", 0),
            "infra_remediable": s.get("infra_remediable", 0), "recommended": list(s.get("grant_all_recommended") or []),
            "default_hours": 8.0, "min_hours": HOURS_MIN, "max_hours": HOURS_MAX}


def grant_recommended(hours: Any, confirm: Any) -> dict[str, Any]:
    """Grant the recommended set. The set comes from the server's own summary, never from the request."""
    from .. import blockers, outbox

    if (refused := _need(confirm)):
        return refused
    h = clamp_hours(hours, 8.0)
    perms = list(blockers.blockers_summary().get("grant_all_recommended") or [])
    res = outbox.grant_permissions_bulk(perms, hours=h, granted_by="blocker_recommended")
    return {"ok": True, "granted": res.get("granted", []), "count": res.get("count", 0), "hours": h}


def auto_fix(blocker_id: str, hours: Any, confirm: Any, *, all_open: bool = False) -> dict[str, Any]:
    """Fix ONE named blocker, or every open one only when ``all_open`` is set explicitly. A missing id never widens
    the request: it is refused."""
    from .. import blockers

    if (refused := _need(confirm)):
        return refused
    h = clamp_hours(hours, 8.0)
    if all_open:
        res = blockers.auto_fix_all_open(hours=h)
        out = {"ok": bool(res.get("ok")), "fixed": res.get("fixed_count", 0), "skipped": res.get("skipped_count", 0), "hours": h}
        if res.get("error"):
            out["error"] = res["error"]
        return out
    if not blocker_id:
        return {"ok": False, "error": "id_required"}
    res = blockers.auto_fix_blocker(blocker_id, hours=h)
    out = {"ok": bool(res.get("ok")), "id": blocker_id, "granted": list(res.get("granted") or []), "hours": h}
    if not res.get("ok"):
        err = str(res.get("error") or "")
        out["error"] = "not_found" if "not found" in err else "already_resolved" if "resolved" in err else "no_fix_available"
    return out


_REMEDIATE_ERR = {"blocker not found": "not_found", "blocker already resolved": "already_resolved",
                  "no_playground_slug": "no_project"}


def _remediation_error(res: dict[str, Any]) -> dict[str, Any]:
    err = str(res.get("error") or "")
    return {"ok": False, "error": _REMEDIATE_ERR.get(err, err if err in {"not_infra", "project_not_found"} else "remediate_failed"),
            **({"slug": res["slug"]} if res.get("slug") else {})}


async def remediation_preview(blocker_id: str) -> dict[str, Any]:
    """What 'remediate' would do to the project, WITHOUT doing it (it is the real function in dry-run mode)."""
    from .. import blockers

    res = await blockers.remediate_infra_blocker(blocker_id, dry_run=True)
    if not res.get("ok"):
        return _remediation_error(res)
    return {"ok": True, "slug": res["slug"], "old_kind": res["old_kind"], "new_kind": "static", "changes_kind": bool(res["changes_kind"])}


async def remediate(blocker_id: str, confirm_kind_change: Any) -> dict[str, Any]:
    """Apply it. Changing a project's kind is a change to the owner's work, so it needs its own confirmation."""
    from .. import blockers

    preview = await remediation_preview(blocker_id)
    if not preview.get("ok"):
        return preview
    if preview["changes_kind"] and confirm_kind_change is not True:
        return {"ok": False, "error": "confirm_required", **{k: preview[k] for k in ("slug", "old_kind", "new_kind")}}
    res = await blockers.remediate_infra_blocker(blocker_id)
    if not res.get("ok"):
        return _remediation_error(res)
    return {"ok": True, "slug": res.get("slug"), "old_kind": res.get("old_kind", preview["old_kind"]),
            "new_kind": "static", "already_static": bool(res.get("already_static"))}


# ---- detail views (read only, trimmed) -------------------------------------------------------------------
def _cut(text: str) -> tuple[str, bool]:
    return (text[:DIFF_LIMIT], True) if len(text) > DIFF_LIMIT else (text, False)


def proposal_detail(proposal_id: str) -> dict[str, Any]:
    from ..self_improve import get_proposal

    p = get_proposal(proposal_id)
    if not p:
        return {"ok": False, "error": "not_found"}
    diff, truncated = _cut(str(p.get("diff") or ""))
    return {"ok": True, "id": p.get("id") or proposal_id, "status": p.get("status", ""), "file_path": p.get("file_path", ""),
            "reason": p.get("reason", ""), "skill": p.get("skill", ""), "proposed_by": p.get("proposed_by", ""),
            "risk_level": p.get("risk_level"), "risk_rationale": p.get("risk_rationale") or "",
            "created_at": p.get("created_at"), "stats": p.get("stats") or {}, "diff": diff, "truncated": truncated}


async def bundle_detail(bundle_id: str) -> dict[str, Any]:
    import difflib

    from ..bundle import get_bundle

    try:
        row = await get_bundle(bundle_id)
    except Exception:
        return {"ok": False, "error": "unavailable"}                  # the bundle store could not be read
    if not row:
        return {"ok": False, "error": "not_found"}
    files = []
    for f in row.get("files") or []:
        old, new = str(f.get("original_content") or ""), str(f.get("new_content") or "")
        diff = "\n".join(difflib.unified_diff(old.splitlines(), new.splitlines(), fromfile="a/" + str(f.get("file_path")),
                                              tofile="b/" + str(f.get("file_path")), lineterm="", n=3))
        shown, truncated = _cut(diff)
        files.append({"file_path": f.get("file_path", ""), "action": f.get("action", ""), "reason": f.get("reason", ""),
                      "bytes": len(new.encode("utf-8")), "diff": shown, "truncated": truncated})
    return {"ok": True, "id": row.get("id") or bundle_id, "title": row.get("title", ""), "status": row.get("status", ""),
            "files": files}


# ---- approve ------------------------------------------------------------------------------------------------
def pending_proposal_ids() -> list[str]:
    from ..self_improve import list_proposals

    return [str(p.get("proposal_id") or p.get("id")) for p in list_proposals(status="pending") if p.get("proposal_id") or p.get("id")]


def approve_proposals(ids: list[str]) -> dict[str, Any]:
    """Approve exactly these proposals. One that is no longer pending (changed since it was listed) is skipped, never
    approved on a hunch."""
    from ..self_improve import approve_proposal

    still = set(pending_proposal_ids())
    applied: list[str] = []
    failed: list[dict[str, Any]] = []
    skipped = 0
    for pid in ids:
        if pid not in still:
            skipped += 1
            continue
        try:
            res = approve_proposal(pid, approved_by="owner")
            if res.get("ok"):
                applied.append(pid)
            else:
                failed.append({"id": pid, "error": str(res.get("error") or "")[:120]})
        except Exception as exc:
            failed.append({"id": pid, "error": type(exc).__name__})
    return {"ok": not failed, "partial": bool(applied and failed), "applied": len(applied), "failed": failed, "skipped": skipped}


def approve_all_pending(ids: Any, confirm: Any) -> dict[str, Any]:
    """The Studio's approve-all: the browser sends the ids it showed in the question, and only those are approved."""
    if (refused := _need(confirm)):
        return refused
    if not isinstance(ids, list) or not ids or not all(isinstance(i, str) and i for i in ids):
        return {"ok": False, "error": "ids_required"}
    return approve_proposals(list(dict.fromkeys(ids)))
