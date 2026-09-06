"""Skill memory — agentskills progressive disclosure + owner Approve.

Catalog (name + description) is cheap. Full SKILL.md body loads only for the
best match, or via the native ``skill`` tool. Drafts from failure/success sit
in ``$DAEDALUS_DATA/learned_skills/pending/`` until Approve. They must not
enter the next turn's prompt on their own. Harness patches stay in
``self_improve``.
"""
from __future__ import annotations

import json
import logging
import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .paths import data_root, package_root

log = logging.getLogger(__name__)

MAX_CATALOG = 8
MAX_BODY = 1
MAX_PIN_BODY = 3
MAX_SKILL_CHARS = 1600
MAX_INJECT = MAX_CATALOG  # back-compat alias


def learned_dir() -> Path:
    p = data_root() / "learned_skills"
    p.mkdir(parents=True, exist_ok=True)
    return p


def pending_dir() -> Path:
    p = learned_dir() / "pending"
    p.mkdir(parents=True, exist_ok=True)
    return p


def builtin_dir() -> Path:
    return package_root() / "skills"


def installed_dir() -> Path:
    p = learned_dir().parent / "installed_skills"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return (s[:48] or "skill")


def _parse_frontmatter(raw: str) -> tuple[dict[str, str], str]:
    if not raw.startswith("---"):
        return {}, raw
    end = raw.find("\n---", 3)
    if end < 0:
        return {}, raw
    meta: dict[str, str] = {}
    for line in raw[3:end].splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            meta[k.strip()] = v.strip().strip('"')
    body = raw[end + 4:].lstrip("\n")
    return meta, body


def list_skills() -> list[dict[str, Any]]:
    """Approved + builtin + installed SKILL.md files. Pending drafts are excluded."""
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    # Learned overrides installed overrides builtin when stems collide.
    for root, origin in (
        (learned_dir(), "learned"),
        (installed_dir(), "installed"),
        (builtin_dir(), "builtin"),
    ):
        if not root.is_dir():
            continue
        for path in sorted(root.glob("*.md")):
            if path.stem in seen:
                continue
            try:
                raw = path.read_text(encoding="utf-8")
            except OSError:
                continue
            meta, body = _parse_frontmatter(raw)
            seen.add(path.stem)
            rows.append({
                "id": path.stem,
                "name": meta.get("name") or path.stem,
                "description": meta.get("description") or (body.splitlines()[0][:180] if body else ""),
                "origin": meta.get("origin") or origin,
                "source": meta.get("source") or "",
                "path": str(path),
                "updated_at": path.stat().st_mtime,
            })
    rows.sort(key=lambda r: str(r.get("name") or r["id"]))
    return rows


def load_skill(skill_id: str) -> dict[str, Any] | None:
    sid = (skill_id or "").strip()
    if not sid or "/" in sid or "\\" in sid or sid in (".", ".."):
        return None
    for root in (learned_dir(), installed_dir(), builtin_dir()):
        path = root / f"{sid}.md"
        if path.is_file():
            raw = path.read_text(encoding="utf-8")
            meta, body = _parse_frontmatter(raw)
            return {"id": sid, "meta": meta, "body": body, "raw": raw, "path": str(path)}
        # allow name frontmatter mismatch: search
    for row in list_skills():
        if row["id"] == sid or row.get("name") == sid:
            return load_skill(row["id"]) if row["id"] != sid else None
    return None


def write_skill(
    *,
    name: str,
    description: str,
    body: str,
    origin: str = "learned",
    overwrite: bool = False,
) -> Path:
    slug = _slug(name)
    target = learned_dir() / f"{slug}.md"
    if not overwrite:
        n = 2
        while target.exists() and origin != "builtin":
            target = learned_dir() / f"{slug}-{n}.md"
            n += 1
            if n > 20:
                break
    text = (
        f"---\nname: {slug}\ndescription: {description[:240]}\n"
        f"origin: {origin}\nupdated: {int(time.time())}\n---\n\n{body.strip()}\n"
    )
    target.write_text(text, encoding="utf-8")
    log.info("learned skill written: %s", target.name)
    return target


def write_installed(
    *,
    name: str,
    description: str,
    body: str,
    source: str = "",
    overwrite: bool = True,
) -> Path:
    slug = _slug(name)
    target = installed_dir() / f"{slug}.md"
    if not overwrite and target.exists():
        n = 2
        while target.exists():
            target = installed_dir() / f"{slug}-{n}.md"
            n += 1
            if n > 20:
                break
    src = (source or "").strip()[:120]
    text = (
        f"---\nname: {target.stem}\ndescription: {description[:240]}\n"
        f"origin: installed\nsource: {src}\nupdated: {int(time.time())}\n---\n\n{body.strip()}\n"
    )
    target.write_text(text, encoding="utf-8")
    log.info("installed skill written: %s", target.name)
    return target


def queue_draft(
    *,
    name: str,
    description: str,
    body: str,
    origin: str,
    kind: str = "",
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Write a pending skill. Not live until approve()."""
    slug = _slug(name)
    existing = None
    for rec in list_pending():
        if rec.get("status") == "pending" and _slug(str(rec.get("name") or "")) == slug:
            existing = rec
            break
    rec: dict[str, Any] = dict(existing or {})
    rec.update({
        "id": rec.get("id") or uuid.uuid4().hex[:12],
        "status": "pending",
        "name": slug,
        "description": (description or "")[:240],
        "body": (body or "").strip()[:4000],
        "origin": origin,
        "kind": kind,
        "updated_at": _now(),
    })
    if extra:
        rec["extra"] = extra
    rec.setdefault("created_at", _now())
    _save_pending(rec)
    return rec


def draft_from_failure(
    *,
    kind: str,
    errors: list[str],
    context: str = "",
) -> Path | None:
    errs = [e.strip() for e in (errors or []) if e and e.strip()]
    if not errs:
        return None
    signature = errs[0][:80]
    name = f"repair-{kind}-{_slug(signature)[:24]}"
    body = (
        f"## When\nPlayground/autopilot `{kind}` failed with:\n"
        + "\n".join(f"- {e[:240]}" for e in errs[:6])
        + "\n\n## Do this next time\n"
        "- Read the exact error; fix the cited file only.\n"
        "- Re-run smoke (and browser verify for visual kinds) before claiming done.\n"
        "- Do not emit 'готово' while tests fail.\n"
    )
    if context:
        body += f"\n## Context\n{context[:800]}\n"
    rec = queue_draft(
        name=name,
        description=f"Repair pattern for {kind}: {signature}",
        body=body,
        origin="failure",
        kind=kind,
        extra={"errors": errs[:6]},
    )
    return pending_dir() / f"{rec['id']}.json"


def draft_from_success(*, kind: str, summary: str) -> Path | None:
    if not (summary or "").strip() or not kind:
        return None
    rec = queue_draft(
        name=f"win-{kind}",
        description=f"What worked for a {kind} build",
        body=(
            f"## When\nBuilding a `{kind}` project.\n\n"
            f"## What worked\n{summary.strip()[:1200]}\n"
        ),
        origin="success",
        kind=kind,
    )
    return pending_dir() / f"{rec['id']}.json"


def list_pending(*, status: str = "pending") -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    root = pending_dir()
    if not root.is_dir():
        return rows
    for path in sorted(root.glob("*.json")):
        rec = _read_pending(path)
        if not rec:
            continue
        if status and rec.get("status") != status:
            continue
        rows.append(rec)
    return rows


def get_pending(draft_id: str) -> dict[str, Any] | None:
    path = pending_dir() / f"{_safe_id(draft_id)}.json"
    return _read_pending(path)


def approve(draft_id: str) -> dict[str, Any]:
    rec = get_pending(draft_id)
    if not rec:
        return {"ok": False, "error": f"unknown draft {draft_id}"}
    if rec.get("status") != "pending":
        return {"ok": False, "error": f"not pending ({rec.get('status')})"}
    path = write_skill(
        name=str(rec.get("name") or "skill"),
        description=str(rec.get("description") or ""),
        body=str(rec.get("body") or ""),
        origin=str(rec.get("origin") or "learned"),
        overwrite=True,
    )
    rec["status"] = "approved"
    rec["approved_at"] = _now()
    rec["live_path"] = str(path)
    _save_pending(rec)
    return {"ok": True, "id": rec["id"], "path": str(path), "name": rec.get("name")}


def reject(draft_id: str, *, reason: str = "") -> dict[str, Any]:
    rec = get_pending(draft_id)
    if not rec:
        return {"ok": False, "error": f"unknown draft {draft_id}"}
    if rec.get("status") != "pending":
        return {"ok": False, "error": f"not pending ({rec.get('status')})"}
    rec["status"] = "rejected"
    rec["rejected_at"] = _now()
    rec["reason"] = (reason or "")[:240]
    _save_pending(rec)
    return {"ok": True, "id": rec["id"], "status": "rejected"}


def disclose(
    query: str,
    *,
    extra_ids: list[str] | None = None,
    prefer_id: str | None = None,
    pin_ids: list[str] | None = None,
    limit: int = MAX_CATALOG,
    body_limit: int = MAX_BODY,
) -> str:
    """Progressive disclosure: catalog first, then SKILL.md bodies.

    Auto path: at most one body (``prefer_id`` wins that slot).
    Owner pins: those ids always appear in the catalog and load up to
    ``MAX_PIN_BODY`` full bodies. Pending drafts never inject.
    """
    live = list_skills()
    by_id = {r["id"]: r for r in live}
    by_name = {str(r.get("name") or ""): r for r in live}
    pins: list[dict[str, Any]] = []
    seen_pins: set[str] = set()
    for raw in pin_ids or []:
        sid = str(raw or "").strip()
        row = by_id.get(sid) or by_name.get(sid)
        if not row or row["id"] in seen_pins:
            continue
        seen_pins.add(row["id"])
        pins.append(row)
    extra = {str(x).strip() for x in (extra_ids or []) if str(x).strip()}
    extra |= seen_pins
    extra |= {str(p.get("name") or "") for p in pins}
    pref = (prefer_id or "").strip()
    if pref:
        extra.add(pref)
    scored = _score(query, extra)
    picks: list[dict[str, Any]] = list(pins)
    have = {p["id"] for p in picks}
    cap = max(limit, len(pins))
    for _, r in scored:
        if r["id"] in have:
            continue
        picks.append(r)
        have.add(r["id"])
        if len(picks) >= cap:
            break
    if pref:
        pref_row = by_id.get(pref) or by_name.get(pref)
        if pref_row and pref_row["id"] not in have:
            picks = [pref_row] + picks
            have.add(pref_row["id"])
        elif pref_row:
            picks = [pref_row] + [r for r in picks if r["id"] != pref_row["id"]]
        picks = picks[: max(1, cap)]
    if not picks:
        return ""
    lines = [
        "━━━ SKILL CATALOG (name + description) ━━━",
        "Load a full body with native tool skill action=load. Pending drafts wait for owner Approve.",
    ]
    if pins:
        lines.append("Owner-pinned skills have bodies below. Follow them this turn.")
    for row in picks:
        mark = " [pinned]" if row["id"] in seen_pins else ""
        desc = (row.get("description") or "")[:180]
        lines.append(f"- {row.get('name') or row['id']}{mark}: {desc}")
    body_rows: list[dict[str, Any]] = list(pins[:MAX_PIN_BODY])
    body_ids = {r["id"] for r in body_rows}
    if pref and len(body_rows) < MAX_PIN_BODY:
        pref_row = by_id.get(pref) or by_name.get(pref)
        if pref_row and pref_row["id"] not in body_ids:
            body_rows.append(pref_row)
            body_ids.add(pref_row["id"])
    if not pins:
        for row in picks[: max(0, body_limit)]:
            if row["id"] not in body_ids:
                body_rows.append(row)
                break
    for row in body_rows:
        loaded = load_skill(row["id"])
        if not loaded:
            continue
        body = (loaded.get("body") or "")[:MAX_SKILL_CHARS]
        if not body.strip():
            continue
        lines.append(f"━━━ SKILL BODY: {loaded['meta'].get('name') or row['id']} ━━━")
        lines.append(body)
    return "\n".join(lines)


def format_block(query: str, *, limit: int = MAX_CATALOG) -> str:
    """Back-compat wrapper — catalog + one body, never pending drafts."""
    return disclose(query, limit=limit, body_limit=MAX_BODY)


def run_action(action: str, **fields: Any) -> dict[str, Any]:
    """Native tool entry. Read-only. Approve stays on HTTP / Studio."""
    action = (action or "catalog").strip().lower()
    if action in ("catalog", "list"):
        rows = _score(str(fields.get("query") or ""), set())
        catalog = [
            {"id": r["id"], "name": r.get("name"), "description": r.get("description"), "origin": r.get("origin")}
            for _, r in rows[:MAX_CATALOG]
        ]
        if not catalog:
            catalog = [
                {"id": r["id"], "name": r.get("name"), "description": r.get("description"), "origin": r.get("origin")}
                for r in list_skills()[:MAX_CATALOG]
            ]
        return {"ok": True, "catalog": catalog, "pending": len(list_pending())}
    if action == "load":
        sid = str(fields.get("id") or fields.get("name") or "")
        loaded = load_skill(sid)
        if not loaded:
            return {"ok": False, "error": f"unknown skill {sid}"}
        return {
            "ok": True,
            "id": loaded["id"],
            "name": loaded["meta"].get("name") or loaded["id"],
            "description": loaded["meta"].get("description") or "",
            "body": (loaded.get("body") or "")[:MAX_SKILL_CHARS],
        }
    if action == "pending":
        rows = list_pending()
        compact = [
            {"id": r.get("id"), "name": r.get("name"), "description": r.get("description"), "origin": r.get("origin")}
            for r in rows[:20]
        ]
        return {"ok": True, "pending": compact, "note": "Owner Approve in Studio. The agent cannot approve itself."}
    if action in ("approve", "reject", "write"):
        return {
            "ok": False,
            "error": "Owner Approve in Studio. The agent cannot approve itself.",
        }
    return {"ok": False, "error": f"unknown action {action}"}


def _score(query: str, extra: set[str]) -> list[tuple[int, dict[str, Any]]]:
    q = (query or "").lower()
    tokens = re.findall(r"[a-zа-я0-9]{3,}", q)
    scored: list[tuple[int, dict[str, Any]]] = []
    for row in list_skills():
        blob = f"{row.get('name', '')} {row.get('description', '')} {row.get('id', '')}".lower()
        score = sum(1 for tok in tokens if tok in blob)
        if row["id"] in extra or str(row.get("name") or "") in extra:
            score += 5
        if score:
            if row.get("origin") not in ("builtin", "", None):
                score += 1
            scored.append((score, row))
    scored.sort(key=lambda x: (-x[0], str(x[1].get("name") or "")))
    return scored


def _safe_id(draft_id: str) -> str:
    return "".join(c for c in (draft_id or "") if c.isalnum() or c in "-_")


def _read_pending(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return raw if isinstance(raw, dict) else None


def _save_pending(rec: dict[str, Any]) -> None:
    path = pending_dir() / f"{_safe_id(str(rec['id']))}.json"
    path.write_text(json.dumps(rec, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
