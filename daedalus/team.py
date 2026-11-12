"""A team in one Daedalus: people, their roles, what each role may call, and who did what.

Owner, 2026-11-12 (gap 3, plan approved): several people work with Daedalus in one Studio. Before, one secret (the
owner token) opened everything. Now:

- The owner is the person with the owner token (``owner_auth``), exactly as before: a one-person install changes
  nothing.
- The owner invites people. Each gets a personal token, shown once; only its SHA-256 is kept (``team.json`` in the
  data folder, owner-only file permissions). A person can be given another role or disabled at any time.
- Roles, from most to least: ``owner`` (everything), ``designer`` (builds games: chat, files, previews, the build and
  the audience test, never settings, keys, publishing, git, deleting, or running a cloned repo) and ``tester`` (reads
  projects, plays them, runs the audience test and reads player numbers; changes no file).
- Default deny: a route no rule below names is the owner's. ``tests/test_team.py`` walks every route of the app, so
  a new route is the owner's until someone decides otherwise here.
- Every change (any method but GET/HEAD) is written to the audit log with who made it.

All projects are visible to every member for now (owner's choice); membership per project can be added later.
"""
from __future__ import annotations

import contextvars
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

ROLES = ("owner", "designer", "tester")
_RANK = {"tester": 1, "designer": 2, "owner": 3}
TOKEN_PREFIX = "dt_"
AUDIT_MAX_LINES = 5000
_lock = threading.Lock()


@dataclass(frozen=True)
class Member:
    id: str
    name: str
    role: str
    created: int = 0
    disabled: bool = False


OWNER = Member(id="owner", name="Owner", role="owner")
_current: contextvars.ContextVar[Member | None] = contextvars.ContextVar("daedalus_member", default=None)


def current() -> Member | None:
    """Who made the request this code runs for (copied into tasks the request starts), or None outside a request."""
    return _current.get()


def bind(member: Member | None) -> contextvars.Token:
    return _current.set(member)


def unbind(token: contextvars.Token) -> None:
    _current.reset(token)


# ── who may call what ────────────────────────────────────────────────────────────────────────────────────────────
# (methods, path regex, lowest role). The first rule that matches decides; no match = owner only.
_R = "GET|HEAD"
_W = "POST|PUT|PATCH|DELETE"
_PG = r"/admin/daedalus/playground/api"
_P = r"/projects/[^/]+"
RULES: tuple[tuple[str, str, str], ...] = (
    # who am I (every member)
    (_R, r"/api/v1/team/me", "tester"),
    # reading projects, their files, previews, runs and live streams
    (_R, r"/api/v1/playground/projects", "tester"),
    (_R, rf"/api/v1/playground{_P}(/(file|world|dissection|unity|production|live|autopilot|autopilot/stream))?",
     "tester"),
    (_R, rf"/api/v1/playground{_P}/preview/.*", "tester"),
    (_R, rf"/api/v1/playground{_P}/capsule(\.json)?", "designer"),
    (_R, rf"{_PG}/projects", "tester"),
    (_R, rf"{_PG}{_P}(/(file|files-snapshot|logs|assets|reference|dissection|autopilot/active|autopilot/resume-info|"
         r"autopilot/stream|guided/active|guided/stream))?", "tester"),
    (_R, rf"{_PG}/(published|game-hints|studio/presets|game-studio/catalog|unity/templates|learning/(advice|recent|"
         r"stats))", "tester"),
    (_R, r"/api/v1/harness/runs(/[^/]+(/events)?)?", "tester"),
    (_R, r"/api/v1/harness/build/[^/]+/(controls|full-auto)", "tester"),
    (_R, r"/api/v1/daedalus/chat/(live|jobs/[^/]+(/stream)?)", "tester"),
    (_R, r"/api/v1/daedalus/(identity|effort|tools|tools/.*|playtest/personas)", "tester"),
    (_R, r"/api/v1/llm/status", "tester"),
    (_R, r"/apps/api/[^/]+/analytics", "tester"),
    # the audience test
    ("POST", r"/api/v1/daedalus/playtest", "tester"),
    # building: chat, files, previews, the build controls, assets, the game studio
    ("POST", r"/api/v1/daedalus/chat(/attachments(/project)?|/jobs|/jobs/[^/]+/(cancel|revert))?", "designer"),
    ("POST", r"/admin/daedalus/chat", "designer"),
    ("POST", r"/api/v1/playground/projects", "designer"),
    ("POST", rf"/api/v1/playground{_P}/(assets/generate|assets/pipeline|autopilot)", "designer"),
    ("POST", rf"{_PG}/projects", "designer"),
    ("POST", rf"{_PG}{_P}/(file|chat|chat/apply|diff-preview|run|stop|suggest|set-mode|backup-workspace|"
             r"autopilot|autopilot/(pause|resume|stop|clear-plan)|guided/(start|continue|stop)|plan/create|"
             r"plan/expand-ultra|assets/(generate|pipeline|auto-pack)|reference-scout)", "designer"),
    ("POST", rf"{_PG}/(game-studio/(brainstorm|gdd|start)|reference-scout|ai-gen-desc)", "designer"),
    ("POST", r"/api/v1/harness/build/[^/]+/(autopilot/(pause|resume)|full-auto|full-auto/stop|plan/(clear|create|"
             r"expand))", "designer"),
)
_COMPILED = tuple((frozenset(m.split("|")), re.compile(rf"^{p}/?$"), role) for m, p, role in RULES)


def required_role(method: str, path: str) -> str:
    """The lowest role that may call ``method path``: ``owner`` unless a rule says otherwise."""
    m = (method or "GET").upper()
    p = (path or "").split("?", 1)[0]
    for methods, rx, role in _COMPILED:
        if m in methods and rx.match(p):
            return role
    return "owner"


def allowed(member: Member, method: str, path: str) -> bool:
    if member.disabled:
        return False
    return _RANK.get(member.role, 0) >= _RANK[required_role(method, path)]


# ── people ────────────────────────────────────────────────────────────────────────────────────────────────────────
def _file() -> Path:
    from .paths import data_root
    return data_root() / "team.json"


def _load() -> dict[str, Any]:
    try:
        data = json.loads(_file().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) and isinstance(data.get("members"), list) else {"members": []}
    except (OSError, ValueError):
        return {"members": []}


def _save(data: dict[str, Any]) -> None:
    path = _file()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    os.replace(tmp, path)


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _member(row: dict[str, Any]) -> Member:
    return Member(id=str(row["id"]), name=str(row.get("name") or ""), role=str(row.get("role") or "tester"),
                  created=int(row.get("created") or 0), disabled=bool(row.get("disabled")))


def members() -> list[Member]:
    return [_member(r) for r in _load()["members"]]


def _clean_name(name: str) -> str:
    name = " ".join(str(name or "").split())[:60]
    if not name:
        raise ValueError("name_required")
    return name


def invite(name: str, role: str) -> tuple[Member, str]:
    """A new member and their personal token. The token is returned once and never stored."""
    if role not in ("designer", "tester"):
        raise ValueError("role_must_be_designer_or_tester")    # there is one owner: the owner token's holder
    token = TOKEN_PREFIX + secrets.token_urlsafe(32)
    row = {"id": secrets.token_hex(6), "name": _clean_name(name), "role": role, "created": int(time.time()),
           "disabled": False, "token_sha256": _hash(token)}
    with _lock:
        data = _load()
        data["members"].append(row)
        _save(data)
    return _member(row), token


def update(member_id: str, *, role: str | None = None, disabled: bool | None = None,
           name: str | None = None) -> Member:
    if role is not None and role not in ("designer", "tester"):
        raise ValueError("role_must_be_designer_or_tester")
    with _lock:
        data = _load()
        for row in data["members"]:
            if row.get("id") == member_id:
                if role is not None:
                    row["role"] = role
                if disabled is not None:
                    row["disabled"] = bool(disabled)
                if name is not None:
                    row["name"] = _clean_name(name)
                _save(data)
                return _member(row)
    raise KeyError(member_id)


def resolve(token: str) -> Member | None:
    """Who holds ``token``: the owner, an active member, or None. Constant-time comparisons."""
    got = (token or "").strip()
    if not got:
        return None
    from .owner_auth import token_ok
    if token_ok(got):
        return OWNER
    if not got.startswith(TOKEN_PREFIX):
        return None
    digest = _hash(got)
    found = None
    for row in _load()["members"]:
        if hmac.compare_digest(str(row.get("token_sha256") or ""), digest):
            found = _member(row)
    return found if found and not found.disabled else None


# ── who did what ──────────────────────────────────────────────────────────────────────────────────────────────────
def _audit_file() -> Path:
    from .paths import data_root
    return data_root() / "team_audit.jsonl"


def audit(member: Member, method: str, path: str, status: int | None = None) -> None:
    """One line per change. Never raises; trims itself to the last ``AUDIT_MAX_LINES``."""
    try:
        line = json.dumps({"ts": int(time.time()), "member": member.id, "name": member.name, "role": member.role,
                           "method": method, "path": path[:300], **({"status": status} if status else {})})
        path_ = _audit_file()
        path_.parent.mkdir(parents=True, exist_ok=True)
        with _lock:
            with path_.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
            if path_.stat().st_size > AUDIT_MAX_LINES * 400:
                lines = path_.read_text(encoding="utf-8").splitlines()[-AUDIT_MAX_LINES:]
                path_.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except Exception:
        log.debug("audit line not written", exc_info=True)


def audit_tail(limit: int = 200) -> list[dict[str, Any]]:
    try:
        lines = _audit_file().read_text(encoding="utf-8").splitlines()[-max(1, min(limit, 1000)):]
    except OSError:
        return []
    out = []
    for line in reversed(lines):
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def public(member: Member) -> dict[str, Any]:
    return asdict(member)
