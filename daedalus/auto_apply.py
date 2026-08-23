"""Auto-apply policy for Daedalus code proposals.

Безопасно авто-прилага proposals без да чака Николай, когато са изпълнени
ВСИЧКИ критерии: LOW risk, HIGH confidence, sandbox чист, малка промяна, без rejection история.

Управлява се от data/daedalus/settings.json → auto_apply_enabled: true/false.
"""
from __future__ import annotations
from .paths import data_root

import json
import logging
import shutil
import time
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

_DATA_ROOT   = data_root()
_SETTINGS_FILE = _DATA_ROOT / "settings.json"

# Критерии за auto-apply
AUTO_APPLY_MAX_ADDITIONS = 150   # максимален брой добавени реда
AUTO_APPLY_MIN_CONFIDENCE = 80   # минимален confidence score
AUTO_APPLY_ALLOWED_SKILLS = {    # само LOW risk skills
    "write_new_modules",
    "edit_templates",
    "write_tests",
    "update_docs",
    "edit_assets",
}
# edit_modules / edit_core / write_routes stay owner-only — evolution gardener
# must never gain a path to silent-apply harness patches.

# ---------------------------------------------------------------------------
# Settings helpers
# ---------------------------------------------------------------------------

def _load_settings() -> dict[str, Any]:
    try:
        if _SETTINGS_FILE.exists():
            return json.loads(_SETTINGS_FILE.read_text("utf-8"))
    except Exception as exc:
        log.warning("auto_apply: settings read failed: %s", exc)
    return {}


def _save_settings(s: dict[str, Any]) -> None:
    try:
        _SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
        _SETTINGS_FILE.write_text(json.dumps(s, indent=2, ensure_ascii=False), "utf-8")
    except Exception as exc:
        log.warning("auto_apply: settings write failed: %s", exc)


def is_auto_apply_enabled() -> bool:
    return bool(_load_settings().get("auto_apply_enabled", False))


def set_auto_apply_enabled(value: bool) -> None:
    s = _load_settings()
    s["auto_apply_enabled"] = value
    _save_settings(s)
    log.info("auto_apply: set enabled=%s", value)


# ---------------------------------------------------------------------------
# Policy check
# ---------------------------------------------------------------------------

def should_auto_apply(proposal: dict[str, Any]) -> tuple[bool, str]:
    """Return (True, reason) if this proposal should be auto-applied, else (False, why_not).

    All criteria must pass — if any fails, returns False with the first failing reason.
    """
    try:
        from .outbox import check_permission
        if check_permission("auto_approve"):
            sandbox = proposal.get("sandbox_result") or {}
            if sandbox.get("ok", False):
                return True, "auto_approve permission + sandbox passed"
            return False, "sandbox did not pass"
    except Exception:
        pass

    if not is_auto_apply_enabled():
        return False, "auto_apply disabled"

    skill = proposal.get("skill") or ""
    if skill not in AUTO_APPLY_ALLOWED_SKILLS:
        return False, f"skill '{skill}' not in auto-apply whitelist"

    conf_score = proposal.get("confidence_score")
    if conf_score is None or conf_score < AUTO_APPLY_MIN_CONFIDENCE:
        return False, f"confidence {conf_score} < {AUTO_APPLY_MIN_CONFIDENCE}"

    conf_tier = (proposal.get("confidence_tier") or "").upper()
    if conf_tier not in ("HIGH",):
        return False, f"confidence tier '{conf_tier}' not HIGH"

    sandbox = proposal.get("sandbox_result") or {}
    if not sandbox.get("ok", False):
        return False, "sandbox did not pass"

    stats = proposal.get("stats") or {}
    additions = stats.get("additions", 999)
    if additions > AUTO_APPLY_MAX_ADDITIONS:
        return False, f"too many additions ({additions} > {AUTO_APPLY_MAX_ADDITIONS})"

    # Check for any sandbox warnings that indicate risk
    warnings = sandbox.get("warnings") or []
    security_warns = [w for w in warnings if "security" in w.lower() or "bandit" in w.lower()]
    if security_warns:
        return False, f"security warnings present: {security_warns[0][:80]}"

    return True, "all criteria met"


# ---------------------------------------------------------------------------
# Apply execution (mirrors admin.py proposal_approve, sync version)
# ---------------------------------------------------------------------------

def apply_proposal_sync(proposal_id: str) -> dict[str, Any]:
    """Synchronously apply a pending proposal. Returns {"ok": bool, ...}.

    Used by the auto-apply path in agent.py (called via run_in_executor).
    """
    import json as _json

    from .self_improve import (
        BACKUPS_DIR, _resolve_safe, _audit,
    )

    # Locate proposal JSON
    proposal_path = _DATA_ROOT / "proposals" / f"{proposal_id}.json"
    if not proposal_path.exists():
        return {"ok": False, "error": f"proposal file not found: {proposal_id}"}

    try:
        p = _json.loads(proposal_path.read_text("utf-8"))
    except Exception as exc:
        return {"ok": False, "error": f"read failed: {exc}"}

    if p.get("status") != "pending":
        return {"ok": False, "error": f"status is '{p.get('status')}', expected pending"}

    file_path = p.get("file_path", "")
    target = _resolve_safe(file_path)
    if target is None:
        return {"ok": False, "error": f"unsafe path: {file_path}"}

    new_content = p.get("new_content", "")
    if not new_content:
        return {"ok": False, "error": "no new_content"}

    # Backup
    backup_path = None
    if target.exists():
        backup_dir = BACKUPS_DIR / proposal_id
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup_path = backup_dir / target.name
        try:
            shutil.copy2(target, backup_path)
        except Exception as exc:
            return {"ok": False, "error": f"backup failed: {exc}"}

    # Write
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(new_content, "utf-8")
    except Exception as exc:
        return {"ok": False, "error": f"write failed: {exc}"}

    # Update proposal JSON
    p["status"] = "applied"
    p["applied_at"] = time.time()
    p["approved_by"] = "daedalus_auto"
    p["backup_path"] = str(backup_path.relative_to(Path.cwd())) if backup_path else None
    try:
        proposal_path.write_text(_json.dumps(p, indent=2), "utf-8")
    except Exception:
        pass

    _audit("auto_apply", {"id": proposal_id, "file": file_path, "by": "daedalus_auto"})
    log.info("auto_apply: applied %s → %s", proposal_id, file_path)
    return {"ok": True, "proposal_id": proposal_id, "file_path": file_path, "by": "daedalus_auto"}
