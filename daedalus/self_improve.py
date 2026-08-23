"""Daedalus Self-Improve — Дедал предлага промени в собствения си код.

Workflow:
  1. Дедал извиква ``propose_patch(file, new_content, reason, skill)``
  2. Системата валидира че skill-ът е enabled от owner-а
  3. Proposal се записва като pending в ``data/daedalus/proposals/``
  4. Owner вижда diff в ``/admin/daedalus/proposals`` → Approve / Reject
  5. При Approve: backup на стария файл → запис на новия → status=applied
  6. Owner може да rollback-не applied proposal по всяко време

Skills (gates) — owner toggle-ва кои категории Дедал може изобщо да предлага:
  - write_routes    — нови /admin/* или /api/* routes
  - edit_modules    — промени в modules/daedalus/
  - edit_templates  — HTML шаблони
  - write_tests     — тестови файлове
  - edit_config     — JSON config (registry, settings)
  - edit_core       — main.py, auth.py (DISABLED by default — много опасно)

Безопасност:
  - Никога не auto-apply
  - Никога не пипа .env, secrets, .git
  - Винаги backup преди apply
  - Audit log на всяко действие
"""
from __future__ import annotations

import difflib
import json
import logging
import re
import shutil
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Any

# Serializes approve/reject/rollback/delete so two simultaneous owner clicks
# can't both pass the status==pending check and double-write the same file.
_STATE_LOCK = threading.Lock()

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# PostgreSQL sync helpers (fire-and-forget — JSON stays primary for writes)
# ---------------------------------------------------------------------------

def _pg_sync(proposal: dict) -> None:
    """Fire-and-forget: write proposal to PostgreSQL without blocking the caller."""
    try:
        import asyncio
        from argos_data_nexus.db import pg_upsert_proposal
        loop = asyncio.get_event_loop()
        if loop.is_running():
            loop.create_task(pg_upsert_proposal(proposal))
        else:
            loop.run_until_complete(pg_upsert_proposal(proposal))
    except Exception as exc:
        log.debug("_pg_sync proposal failed (non-fatal): %s", exc)

from .paths import adn_package_root, data_root, repo_root

# ADN package (legacy self-improve target) + repo root for relative proposal paths.
ADN_ROOT = adn_package_root()
PROJECT_ROOT = repo_root()
# Top-level dirs Daedalus may touch (harness + remaining ADN surface).
# After export, repo_root() is the product dir so daedalus/, app/, ui/ are first-class.
ALLOWED_PREFIXES = frozenset({
    "argos_data_nexus", "artifacts", "lib", "scripts", "Daedalus", "tests",
    "daedalus", "app", "ui",
})

# Data storage
DATA_ROOT = data_root()
PROPOSALS_DIR = DATA_ROOT / "proposals"
BACKUPS_DIR = DATA_ROOT / "backups"
SKILLS_FILE = DATA_ROOT / "skills.json"
AUDIT_LOG = DATA_ROOT / "self_improve_audit.log"

PROPOSALS_DIR.mkdir(parents=True, exist_ok=True)
BACKUPS_DIR.mkdir(parents=True, exist_ok=True)

# Skill definitions — what categories of changes Daedalus may propose
SKILLS: dict[str, dict[str, Any]] = {
    "write_routes": {
        "label": "Write Routes",
        "description": "Нови /admin/* или /api/* HTTP routes в admin.py / main.py",
        "patterns": [
            r"argos_data_nexus/admin\.py$",
            r"argos_data_nexus/main\.py$",
            r"^app/(main|admin_compat|playground_api|cli)\.py$",
        ],
        "default": False,
        "risk": "medium",
    },
    "edit_modules": {
        "label": "Edit Modules",
        "description": "Промени в Daedalus harness (Daedalus/daedalus/*)",
        "patterns": [
            r"Daedalus/daedalus/.*\.py$",
            r"argos_data_nexus/modules/daedalus/.*\.py$",
            r"^daedalus/.*\.py$",
        ],
        "default": False,
        "risk": "medium",
    },
    "write_new_modules": {
        "label": "Write New Modules",
        "description": "Нови Python модули в argos_data_nexus/modules/ (НЕ в daedalus/) — нова бизнес логика, изчислителни модули, helper libraries.",
        "patterns": [r"argos_data_nexus/modules/[^/]+\.py$"],
        "default": True,
        "risk": "low",
    },
    "edit_templates": {
        "label": "Edit Templates",
        "description": "HTML шаблоните в templates/",
        "patterns": [
            r"argos_data_nexus/templates/.*\.html$",
            r"^ui/templates/.*",
            r"^ui/static/.*",
        ],
        "default": True,
        "risk": "low",
    },
    "write_tests": {
        "label": "Write Tests",
        "description": "Тестови файлове (tests/*.py)",
        "patterns": [r"tests/.*\.py$", r"argos_data_nexus/tests/.*\.py$"],
        "default": True,
        "risk": "low",
    },
    "edit_config": {
        "label": "Edit Config",
        "description": "JSON config — mesh_registry.json, settings, knowledge",
        "patterns": [r".*\.json$"],
        "default": False,
        "risk": "medium",
    },
    "edit_core": {
        "label": "Edit Core",
        "description": "⚠ main.py, auth.py, mesh.py — ядрото на системата",
        "patterns": [
            r"argos_data_nexus/(main|auth|mesh)\.py$",
            r"^app/main\.py$",
        ],
        "default": False,
        "risk": "high",
    },
}

# Hard blocks — НИКОГА не могат да се пипат, дори ако skill е enabled
HARD_BLOCKS = [
    r"\.env",
    r"secrets",
    r"\.git/",
    r"private_key",
    r"\.key$",
    r"\.pem$",
    r"data/daedalus/proposals",
    r"data/daedalus/backups",
    r"data/daedalus/skills\.json$",
    r"self_improve\.py$",  # Дедал не може да пипа собственото си approval ядро
    # Dependency manifests — модификация би довела до RCE при следващ install
    r"(^|/)pyproject\.toml$",
    r"(^|/)uv\.lock$",
    r"(^|/)requirements\.txt$",
    r"(^|/)package\.json$",
    r"(^|/)package-lock\.json$",
    r"(^|/)pnpm-lock\.yaml$",
    r"(^|/)\.replit$",
    r"(^|/)replit\.nix$",
]


# ---------------------------------------------------------------------------
# Skills state
# ---------------------------------------------------------------------------

def _default_skills_state() -> dict[str, bool]:
    return {name: spec["default"] for name, spec in SKILLS.items()}


def load_skills_state() -> dict[str, bool]:
    """Load owner-configured skill enable/disable state."""
    if not SKILLS_FILE.exists():
        state = _default_skills_state()
        save_skills_state(state)
        return state
    try:
        data = json.loads(SKILLS_FILE.read_text("utf-8"))
        state = _default_skills_state()
        state.update({k: bool(v) for k, v in data.items() if k in SKILLS})
        return state
    except Exception as exc:
        log.warning("skills state read failed: %s", exc)
        return _default_skills_state()


def save_skills_state(state: dict[str, bool]) -> None:
    SKILLS_FILE.write_text(json.dumps(state, indent=2), "utf-8")


def toggle_skill(name: str) -> dict[str, Any]:
    if name not in SKILLS:
        return {"ok": False, "error": f"unknown skill: {name}"}
    state = load_skills_state()
    state[name] = not state[name]
    save_skills_state(state)
    _audit("skill_toggle", {"skill": name, "enabled": state[name]})
    return {"ok": True, "skill": name, "enabled": state[name]}


def list_skills() -> list[dict[str, Any]]:
    state = load_skills_state()
    return [
        {
            "name": name,
            "label": spec["label"],
            "description": spec["description"],
            "risk": spec["risk"],
            "enabled": state.get(name, False),
            "patterns": spec["patterns"],
        }
        for name, spec in SKILLS.items()
    ]


# ---------------------------------------------------------------------------
# Path validation
# ---------------------------------------------------------------------------

def _is_hard_blocked(rel_path: str) -> bool:
    return any(re.search(p, rel_path) for p in HARD_BLOCKS)


def _resolve_safe(rel_path: str) -> Path | None:
    """Resolve rel_path against PROJECT_ROOT and ensure it stays inside an
    allowed top-level directory.

    Rejects absolute paths, paths with ``..`` components, and any path that
    after resolution escapes the allowed prefix root (defence in depth
    against symlinks).

    Accepted prefixes (must be the FIRST component of rel_path):
      argos_data_nexus/, artifacts/, lib/, scripts/

    Backward-compat: if rel_path doesn't start with any allowed prefix but
    resolves cleanly under ADN_ROOT (e.g. legacy callers passing
    "modules/daedalus/foo.py"), accept it as ADN-package-relative.
    """
    if not rel_path or rel_path.startswith("/") or "\x00" in rel_path:
        return None
    # Reject any parent-traversal components before resolving
    parts = Path(rel_path).parts
    if not parts or any(part == ".." for part in parts):
        return None
    if _is_hard_blocked(rel_path):
        return None

    first = parts[0]
    if first in ALLOWED_PREFIXES:
        base = PROJECT_ROOT
    elif ADN_ROOT is not None:
        # Legacy / package-relative fallback (e.g. "modules/daedalus/x.py").
        base = ADN_ROOT
    else:
        return None
    try:
        target = (base / rel_path).resolve()
        target.relative_to(base.resolve())
    except (ValueError, OSError):
        return None
    return target


def _skill_for_path(rel_path: str) -> str | None:
    """Return the skill name whose patterns match this path, or None."""
    for name, spec in SKILLS.items():
        for pat in spec["patterns"]:
            if re.search(pat, rel_path):
                return name
    return None


# ---------------------------------------------------------------------------
# Proposal CRUD
# ---------------------------------------------------------------------------

def _proposal_path(proposal_id: str) -> Path:
    return PROPOSALS_DIR / f"{proposal_id}.json"


def _audit(event: str, data: dict[str, Any]) -> None:
    line = json.dumps({"ts": time.time(), "event": event, **data}) + "\n"
    try:
        AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)

        with AUDIT_LOG.open("a", encoding="utf-8") as f:
            f.write(line)
    except Exception as exc:
        log.warning("audit log write failed: %s", exc)


# ---------------------------------------------------------------------------
# Git snapshot helpers (Step 1: pre-apply safety net)
# ---------------------------------------------------------------------------

_GIT_ROOT = PROJECT_ROOT


def _git_snapshot(proposal_id: str) -> dict[str, Any]:
    """Create a git snapshot commit before applying a proposal.

    Tags the commit with 'daedalus-snapshot' prefix so it's easy to filter.
    Non-fatal — if git fails the approval is NOT blocked, just logged.
    Returns {"ok": bool, "sha": str | None, "error": str | None}.
    """
    try:
        # Stage all changes (including any drift from previous edits)
        subprocess.run(
            ["git", "add", "-A"],
            cwd=str(_GIT_ROOT),
            capture_output=True,
            timeout=15,
            check=False,
        )
        msg = f"daedalus-snapshot: pre-apply {proposal_id}"
        result = subprocess.run(
            ["git", "commit", "--allow-empty", "-m", msg],
            cwd=str(_GIT_ROOT),
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        if result.returncode == 0:
            # Extract short SHA from output like "[main abc1234] ..."
            sha_match = re.search(r"\[[\w/]+ ([0-9a-f]+)\]", result.stdout)
            sha = sha_match.group(1) if sha_match else None
            log.info("Daedalus git snapshot: %s (sha=%s)", proposal_id, sha)
            return {"ok": True, "sha": sha, "error": None}
        # "nothing to commit" is exit 1 but not a real error
        if "nothing to commit" in result.stdout + result.stderr:
            log.debug("git snapshot skipped — nothing to commit")
            return {"ok": True, "sha": None, "error": None}
        log.warning("git snapshot failed: %s", result.stderr[:300])
        return {"ok": False, "sha": None, "error": result.stderr[:300]}
    except Exception as exc:
        log.warning("git snapshot exception: %s", exc)
        return {"ok": False, "sha": None, "error": str(exc)}


def propose_patch(
    file_path: str,
    new_content: str,
    reason: str,
    proposed_by: str = "daedalus",
    skill_hint: str | None = None,
    risk_level: int | None = None,
    risk_rationale: str | None = None,
) -> dict[str, Any]:
    """Create a new proposal. Returns {ok, proposal_id, ...} or {ok=False, error}.

    Does NOT apply the change — only stores the proposal for owner review.
    """
    # 1. Path safety
    if _is_hard_blocked(file_path):
        return {"ok": False, "error": f"path is hard-blocked: {file_path}"}
    target = _resolve_safe(file_path)
    if target is None:
        return {"ok": False, "error": f"unsafe or invalid path: {file_path}"}

    # 2. Determine skill required
    skill = skill_hint or _skill_for_path(file_path)
    if not skill:
        return {
            "ok": False,
            "error": f"no skill matches path {file_path}. Owner must define a skill pattern first.",
        }

    # 3. Check skill is enabled
    state = load_skills_state()
    if not state.get(skill, False):
        return {
            "ok": False,
            "error": f"skill '{skill}' is disabled. Owner must enable it in /admin/daedalus/skills first.",
            "required_skill": skill,
        }

    # 4. Read current content (if exists)
    old_content = ""
    file_exists = target.exists()
    if file_exists:
        try:
            old_content = target.read_text("utf-8")
        except Exception as exc:
            return {"ok": False, "error": f"cannot read existing file: {exc}"}

    if old_content == new_content:
        return {"ok": False, "error": "new_content is identical to current content"}

    # 5. Build diff for preview
    diff_lines = list(difflib.unified_diff(
        old_content.splitlines(keepends=False),
        new_content.splitlines(keepends=False),
        fromfile=f"a/{file_path}",
        tofile=f"b/{file_path}",
        lineterm="",
        n=3,
    ))

    # [Phase 17A] Patch size guard — max 120 added lines per proposal
    _additions = sum(1 for ln in diff_lines if ln.startswith("+") and not ln.startswith("+++"))
    if _additions > 120:
        return {
            "ok": False,
            "error": (
                f"Patch too large: {_additions} added lines (limit: 120). "
                f"Split into smaller focused patches — max 1 feature/fix per proposal. "
                f"For multi-file or multi-phase tasks use NEED_PROJECT_PLAN."
            ),
            "patch_too_large": True,
            "additions": _additions,
        }

    # 6. Persist proposal
    proposal_id = f"prop_{int(time.time())}_{uuid.uuid4().hex[:8]}"
    proposal = {
        "id": proposal_id,
        "created_at": time.time(),
        "status": "pending",
        "file_path": file_path,
        "file_existed": file_exists,
        "skill": skill,
        "reason": reason,
        "proposed_by": proposed_by,
        "risk_level": risk_level,
        "risk_rationale": risk_rationale,
        "old_content": old_content,
        "new_content": new_content,
        "diff": "\n".join(diff_lines),
        "stats": {
            "additions": _additions,
            "deletions": sum(1 for ln in diff_lines if ln.startswith("-") and not ln.startswith("---")),
            "old_bytes": len(old_content),
            "new_bytes": len(new_content),
        },
    }
    _proposal_path(proposal_id).write_text(json.dumps(proposal, indent=2), "utf-8")
    _audit("propose", {"id": proposal_id, "file": file_path, "skill": skill, "by": proposed_by,
                       "risk_level": risk_level})
    log.info("Daedalus proposed patch %s for %s (skill=%s, risk=%s)", proposal_id, file_path, skill, risk_level)
    _pg_sync(proposal)
    return {"ok": True, "proposal_id": proposal_id, "skill": skill, "stats": proposal["stats"],
            "risk_level": risk_level}


def list_proposals(status: str | None = None) -> list[dict[str, Any]]:
    """List proposals. Tries PostgreSQL first (survives deploys), falls back to JSON files."""
    pg_rows = _pg_list_sync(status)
    if pg_rows:
        return pg_rows

    # Fallback: local JSON files (dev env without DB or DB not reachable)
    out: list[dict[str, Any]] = []
    for f in sorted(PROPOSALS_DIR.glob("prop_*.json"), reverse=True):
        try:
            p = json.loads(f.read_text("utf-8"))
        except Exception:
            continue
        if status and p.get("status") != status:
            continue
        out.append({
            "id": p["id"],
            "created_at": p.get("created_at"),
            "status": p.get("status"),
            "file_path": p.get("file_path"),
            "skill": p.get("skill"),
            "reason": p.get("reason"),
            "proposed_by": p.get("proposed_by"),
            "stats": p.get("stats", {}),
            "applied_at": p.get("applied_at"),
            "rolled_back_at": p.get("rolled_back_at"),
        })
    return out


def _pg_list_sync(status: str | None = None) -> list[dict[str, Any]]:
    """Synchronously query proposals from PostgreSQL (runs async coroutine on current loop)."""
    try:
        import asyncio
        from argos_data_nexus.db import pg_list_proposals
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # Already in async context — cannot run_until_complete. Return empty to use JSON fallback.
            # The async route handlers should call pg_list_proposals directly instead.
            return []
        rows = loop.run_until_complete(pg_list_proposals(status))
        if not rows:
            return []
        out = []
        for r in rows:
            row: dict = dict(r)
            # Normalize timestamps — asyncpg returns datetime objects
            for ts_field in ("created_at", "applied_at", "rolled_back_at"):
                v = row.get(ts_field)
                if hasattr(v, "timestamp"):
                    row[ts_field] = v.timestamp()
            out.append({
                "id": row.get("id"),
                "created_at": row.get("created_at"),
                "status": row.get("status"),
                "file_path": row.get("file_path"),
                "skill": row.get("skill"),
                "reason": row.get("reason"),
                "proposed_by": row.get("proposed_by"),
                "stats": row.get("stats") or {},
                "applied_at": row.get("applied_at"),
                "rolled_back_at": row.get("rolled_back_at"),
            })
        return out
    except Exception as exc:
        log.debug("_pg_list_sync failed, using JSON fallback: %s", exc)
        return []


def get_proposal(proposal_id: str) -> dict[str, Any] | None:
    """Get a single proposal. Tries local JSON first (has full content), then PG."""
    f = _proposal_path(proposal_id)
    if f.exists():
        try:
            return json.loads(f.read_text("utf-8"))
        except Exception:
            pass
    # JSON not found (e.g. production environment) — try PostgreSQL
    return _pg_get_sync(proposal_id)


def _pg_get_sync(proposal_id: str) -> dict[str, Any] | None:
    """Synchronously fetch a proposal from PostgreSQL."""
    try:
        import asyncio
        from argos_data_nexus.db import pg_get_proposal
        loop = asyncio.get_event_loop()
        if loop.is_running():
            return None
        row = loop.run_until_complete(pg_get_proposal(proposal_id))
        if row is None:
            return None
        row = dict(row)
        # Normalize timestamps
        for ts_field in ("applied_at", "rejected_at", "rolled_back_at", "created_at"):
            v = row.get(ts_field)
            if hasattr(v, "timestamp"):
                row[ts_field] = v.timestamp()
        return row
    except Exception as exc:
        log.debug("_pg_get_sync failed: %s", exc)
        return None


def _save_proposal(p: dict[str, Any]) -> None:
    _proposal_path(p["id"]).write_text(json.dumps(p, indent=2), "utf-8")
    _pg_sync(p)


def approve_proposal(proposal_id: str, approved_by: str = "owner") -> dict[str, Any]:
    with _STATE_LOCK:
        p = get_proposal(proposal_id)
        if not p:
            return {"ok": False, "error": "proposal not found"}
        if p["status"] != "pending":
            return {"ok": False, "error": f"proposal status is '{p['status']}', expected 'pending'"}

        # Re-verify skill still enabled at apply time (owner may have toggled off)
        state = load_skills_state()
        if not state.get(p["skill"], False):
            return {"ok": False, "error": f"skill '{p['skill']}' is currently disabled"}

        target = _resolve_safe(p["file_path"])
        if target is None:
            return {"ok": False, "error": "path no longer safe to write"}

        # Git snapshot BEFORE any write (non-fatal — we log but don't block)
        snap = _git_snapshot(proposal_id)
        snap_sha = snap.get("sha")
        if not snap.get("ok"):
            log.warning("pre-apply git snapshot failed (non-fatal): %s", snap.get("error"))

        # Backup current file (if exists)
        backup_dir = BACKUPS_DIR / proposal_id
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup_path = None
        if target.exists():
            backup_path = backup_dir / target.name
            try:
                shutil.copy2(target, backup_path)
            except Exception as exc:
                return {"ok": False, "error": f"backup failed: {exc}"}

        # Write new content
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(p["new_content"], "utf-8")
        except Exception as exc:
            return {"ok": False, "error": f"write failed: {exc}"}

        p["status"] = "applied"
        p["applied_at"] = time.time()
        p["approved_by"] = approved_by
        if backup_path:
            try:
                p["backup_path"] = str(backup_path.relative_to(PROJECT_ROOT))
            except ValueError:
                p["backup_path"] = str(backup_path)
        else:
            p["backup_path"] = None
        p["git_snapshot_sha"] = snap_sha
        _save_proposal(p)
        _audit("approve", {"id": proposal_id, "file": p["file_path"], "by": approved_by,
                           "git_sha": snap_sha})
        log.warning("Daedalus self-edit APPLIED: %s → %s (by %s, snapshot=%s)",
                    proposal_id, p["file_path"], approved_by, snap_sha or "none")
        return {"ok": True, "proposal_id": proposal_id, "file_path": p["file_path"],
                "git_snapshot_sha": snap_sha}


def reject_proposal(proposal_id: str, rejected_by: str = "owner", note: str = "") -> dict[str, Any]:
    with _STATE_LOCK:
        p = get_proposal(proposal_id)
        if not p:
            return {"ok": False, "error": "proposal not found"}
        if p["status"] != "pending":
            return {"ok": False, "error": f"can only reject pending proposals (current: {p['status']})"}
        p["status"] = "rejected"
        p["rejected_at"] = time.time()
        p["rejected_by"] = rejected_by
        p["rejection_note"] = note
        _save_proposal(p)
        _audit("reject", {"id": proposal_id, "by": rejected_by, "note": note})
        # [FIX #4] Record rejection as lesson in ChromaDB — Дедал ги чете преди следващ proposal
        try:
            from .rejection_lessons import record_rejection_lesson
            record_rejection_lesson(p, reject_reason=note or "no reason given", user_id=rejected_by)
        except Exception as exc:
            log.debug("rejection_lesson record failed (non-fatal): %s", exc)
        return {"ok": True, "proposal_id": proposal_id}


def rollback_proposal(proposal_id: str, rolled_back_by: str = "owner") -> dict[str, Any]:
    with _STATE_LOCK:
        p = get_proposal(proposal_id)
        if not p:
            return {"ok": False, "error": "proposal not found"}
        if p["status"] != "applied":
            return {"ok": False, "error": f"can only rollback applied proposals (current: {p['status']})"}

        target = _resolve_safe(p["file_path"])
        if target is None:
            return {"ok": False, "error": "path no longer safe"}

        if p.get("file_existed"):
            # Restore from backup
            backup_dir = BACKUPS_DIR / proposal_id
            backup_file = backup_dir / target.name
            if not backup_file.exists():
                return {"ok": False, "error": "backup file missing — cannot rollback"}
            try:
                shutil.copy2(backup_file, target)
            except Exception as exc:
                return {"ok": False, "error": f"restore failed: {exc}"}
        else:
            # File was newly created — delete it
            try:
                target.unlink(missing_ok=True)
            except Exception as exc:
                return {"ok": False, "error": f"delete failed: {exc}"}

        p["status"] = "rolled_back"
        p["rolled_back_at"] = time.time()
        p["rolled_back_by"] = rolled_back_by
        _save_proposal(p)
        _audit("rollback", {"id": proposal_id, "file": p["file_path"], "by": rolled_back_by})
        log.warning("Daedalus self-edit ROLLED BACK: %s → %s", proposal_id, p["file_path"])
        return {"ok": True, "proposal_id": proposal_id}


def delete_proposal(proposal_id: str) -> dict[str, Any]:
    """Delete a non-applied proposal (rejected or pending). Applied ones must be rolled back first.

    Also cleans up the associated backup directory.
    """
    with _STATE_LOCK:
        p = get_proposal(proposal_id)
        if not p:
            return {"ok": False, "error": "proposal not found"}
        if p["status"] == "applied":
            return {"ok": False, "error": "cannot delete applied proposal — rollback first"}
        try:
            _proposal_path(proposal_id).unlink(missing_ok=True)
            # Clean up backup dir if it exists (from a prior rolled_back state)
            backup_dir = BACKUPS_DIR / proposal_id
            if backup_dir.exists():
                shutil.rmtree(backup_dir, ignore_errors=True)
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True}


def propose_patch_validated(
    file_path: str,
    new_content: str,
    reason: str,
    proposed_by: str = "daedalus",
    skill_hint: str | None = None,
    test_code: str | None = None,
    sandbox_timeout: int = 12,
    risk_level: int | None = None,
    risk_rationale: str | None = None,
    changed_files: list[str] | None = None,
) -> dict[str, Any]:
    """Like propose_patch() but runs sandbox validation FIRST.

    Sandbox validates syntax + import + optional test snippet + static analysis + related tests.
    The sandbox_result and confidence score are stored on the proposal for owner visibility.
    If sandbox fails, returns {ok=False, sandbox_result, errors} WITHOUT creating a proposal.
    If sandbox passes, creates the proposal and returns {ok=True, proposal_id, sandbox_result}.
    """
    from .sandbox import validate_patch
    from .lint_gate import lint_content, format_for_daedalus as _fmt_lint

    # ── LINT GATE (runs BEFORE sandbox) ──────────────────────────────────────
    # Blocks on real pyflakes bugs (F-codes: undefined names, unused imports, etc.)
    # Style warnings (E/W) are stored on the proposal but don't block.
    lint_r = lint_content(file_path, new_content)
    if not lint_r.ok:
        return {
            "ok": False,
            "error": "lint_gate_blocked",
            "lint": lint_r.to_dict(),
            "lint_report": _fmt_lint(lint_r),
            "errors": [
                f"L{b.row}:{b.col} [{b.code}] {b.message}"
                for b in lint_r.blockers
            ],
        }
    # Warnings don't block — will be attached to proposal below
    _lint_warnings = lint_r.to_dict() if lint_r.warnings else None

    all_files = changed_files or [file_path]

    sandbox_result = validate_patch(
        file_path=file_path,
        new_content=new_content,
        test_code=test_code,
        timeout=sandbox_timeout,
        changed_files=all_files,
    )

    if not sandbox_result.get("ok"):
        return {
            "ok": False,
            "error": "sandbox_validation_failed",
            "sandbox_result": sandbox_result,
            "errors": sandbox_result.get("errors", []),
        }

    result = propose_patch(file_path, new_content, reason, proposed_by, skill_hint,
                           risk_level=risk_level, risk_rationale=risk_rationale)
    if not result.get("ok"):
        return result

    # Attach lint warnings (non-blocking) to stored proposal for owner visibility
    if _lint_warnings:
        try:
            p_path = _proposal_path(result["proposal_id"])
            p_data = json.loads(p_path.read_text("utf-8"))
            p_data["lint_warnings"] = _lint_warnings
            p_path.write_text(json.dumps(p_data, indent=2), "utf-8")
        except Exception:
            pass
    result["lint_warnings"] = _lint_warnings

    # Attach sandbox results + confidence score to the stored proposal  [FIX #2]
    proposal_id = result["proposal_id"]
    p = get_proposal(proposal_id)
    if p:
        p["sandbox_result"] = sandbox_result
        # Confidence scoring
        try:
            from .confidence_scorer import score_proposal, count_similar_rejections
            similar = count_similar_rejections(all_files, p.get("skill", ""), memory=None)
            tests_r = sandbox_result.get("tests")
            score = score_proposal(
                risk=risk_level,
                sandbox_result=sandbox_result,
                changed_files=all_files,
                tests_result=tests_r,
                similar_rejections=similar,
            )
            p["confidence_score"] = score["total"]
            p["confidence_tier"]  = score["tier"]
            p["confidence_flags"] = score["flags"]
            result["confidence_score"] = score["total"]
            result["confidence_tier"]  = score["tier"]
            result["confidence_flags"] = score["flags"]
        except Exception as exc:
            log.debug("confidence_scorer failed (non-fatal): %s", exc)
        _save_proposal(p)

    result["sandbox_result"] = sandbox_result
    return result


def post_apply_test(proposal_id: str) -> dict[str, Any]:
    """Run sandbox validation on a just-applied file to confirm it works in-place.

    Called automatically after approve_proposal() for .py files.
    Returns {ok, sandbox_result}. Does NOT rollback on failure — owner decides.
    """
    from .sandbox import validate_patch

    p = get_proposal(proposal_id)
    if not p:
        return {"ok": False, "error": "proposal not found"}
    if p.get("status") != "applied":
        return {"ok": False, "error": f"proposal status is {p.get('status')!r}, expected 'applied'"}

    file_path = p["file_path"]
    target = _resolve_safe(file_path)
    if target is None or not target.exists():
        return {"ok": False, "error": "applied file not found on disk"}

    try:
        applied_content = target.read_text("utf-8")
    except Exception as exc:
        return {"ok": False, "error": f"cannot read applied file: {exc}"}

    sandbox_result = validate_patch(
        file_path=file_path,
        new_content=applied_content,
        test_code=None,
    )

    p["post_apply_sandbox"] = sandbox_result
    _save_proposal(p)
    _audit("post_apply_test", {
        "id": proposal_id,
        "file": file_path,
        "sandbox_ok": sandbox_result.get("ok", False),
    })
    log.info("post_apply_test for %s: sandbox_ok=%s", proposal_id, sandbox_result.get("ok"))
    return {"ok": True, "sandbox_result": sandbox_result}


def stats() -> dict[str, int]:
    counts = {"pending": 0, "applied": 0, "rejected": 0, "rolled_back": 0, "total": 0}
    for p in list_proposals():
        counts["total"] += 1
        st = p.get("status", "")
        if st in counts:
            counts[st] += 1
    return counts
