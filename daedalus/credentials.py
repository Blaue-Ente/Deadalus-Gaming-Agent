"""Whose key pays: each team member's own keys, the owner's only where the owner shares them (gap 3c).

Owner, 2026-11-12 (plan approved): Daedalus is not only for the owner; people who download it or join a team use
their own keys and their own credits. A member's model call uses, in order:

1. the member's own key for that service (their vault: ``team_keys/<id>.json``, owner-only file permissions);
2. the owner's key, only if the owner shares that key with the team, and only up to the member's spending cap;
3. nothing: the call says which key to add. Never the owner's key by accident.

The owner (and a server task with no person behind it) uses the environment exactly as before. Values are never
returned by any API: a member sees which keys they have set, not the keys.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

DEFAULT_CAP_USD = 2.0                     # per member, on the owner's shared keys, until the owner sets another
SERVICE_KEYS = ("ELEVENLABS_API_KEY",)    # keys a member may hold besides the model providers'
_NAME = re.compile(r"^[A-Z][A-Z0-9_]{2,63}$")
_lock = threading.Lock()


def member_key_names() -> tuple[str, ...]:
    from .llm_config import PROVIDER_ENV_KEYS
    return tuple(sorted(set(PROVIDER_ENV_KEYS.values()) | set(SERVICE_KEYS)))


def _dir() -> Path:
    from .paths import data_root
    return data_root() / "team_keys"


def _write_private(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(data, indent=2))
    os.replace(tmp, path)


def _read(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        log.error("%s cannot be read: it is never overwritten", path.name, exc_info=True)
        raise


def _vault(member_id: str) -> Path:
    if not re.fullmatch(r"[a-z0-9]{4,32}", member_id or ""):
        raise ValueError("bad_member_id")
    return _dir() / f"{member_id}.json"


# ── a member's own keys ───────────────────────────────────────────────────────────────────────────────────────────
def set_key(member_id: str, name: str, value: str) -> None:
    """Store (or, with an empty value, remove) one of a member's keys. Write-only from the outside."""
    if name not in member_key_names() or not _NAME.match(name):
        raise ValueError("unknown_key_name")
    path = _vault(member_id)
    with _lock:
        data = _read(path)
        keys = dict(data.get("keys") or {})
        value = (value or "").strip()
        if value:
            keys[name] = value
        else:
            keys.pop(name, None)
        _write_private(path, {**data, "keys": keys})


def own_key(member_id: str, name: str) -> str:
    try:
        return str((_read(_vault(member_id)).get("keys") or {}).get(name) or "")
    except (OSError, ValueError):
        return ""


def own_key_names(member_id: str) -> list[str]:
    try:
        return sorted((_read(_vault(member_id)).get("keys") or {}).keys())
    except (OSError, ValueError):
        return []


# ── what the owner shares, and each member's cap and spend ────────────────────────────────────────────────────────
def _sharing_path() -> Path:
    return _dir() / "_sharing.json"


def _shape(data: dict[str, Any]) -> dict[str, Any]:
    return {"shared": sorted(set(data.get("shared") or []) & set(member_key_names())),
            "caps": {str(k): float(v) for k, v in (data.get("caps") or {}).items()},
            "spend": {str(k): float(v) for k, v in (data.get("spend") or {}).items()}}


def sharing() -> dict[str, Any]:
    """For reading. An unreadable file shares nothing (fail closed); writers below refuse to replace it."""
    try:
        return _shape(_read(_sharing_path()))
    except (OSError, ValueError):
        return _shape({})


def set_sharing(*, shared: list[str] | None = None, caps: dict[str, float] | None = None,
                reset_spend: list[str] | None = None) -> dict[str, Any]:
    with _lock:
        current = _shape(_read(_sharing_path()))         # raises on an unreadable file: never overwritten
        if shared is not None:
            unknown = set(shared) - set(member_key_names())
            if unknown:
                raise ValueError("unknown_key_name")
            current["shared"] = sorted(set(shared))
        for mid, usd in (caps or {}).items():
            current["caps"][str(mid)] = max(0.0, min(float(usd), 10_000.0))
        for mid in reset_spend or []:
            current["spend"].pop(str(mid), None)
        _write_private(_sharing_path(), current)
        return current


def cap_for(member_id: str) -> float:
    return float(sharing()["caps"].get(member_id, DEFAULT_CAP_USD))


def spent_on_shared(member_id: str) -> float:
    return float(sharing()["spend"].get(member_id, 0.0))


def note_shared_spend(member_id: str, usd: float) -> None:
    """Count what a member spent on the owner's shared keys. Never raises."""
    if not usd or usd <= 0:
        return
    try:
        with _lock:
            current = _shape(_read(_sharing_path()))
            current["spend"][member_id] = round(current["spend"].get(member_id, 0.0) + float(usd), 6)
            _write_private(_sharing_path(), current)
    except Exception:
        log.warning("shared spend for %s not counted", member_id, exc_info=True)


# ── the one question every caller asks ────────────────────────────────────────────────────────────────────────────
def _member():
    from .team import current
    m = current()
    return None if m is None or m.role == "owner" else m


def env(name: str, default: str = "") -> str:
    """The value of key ``name`` for whoever this call runs for. The owner and server tasks: the environment."""
    m = _member()
    if m is None:
        return os.environ.get(name, default)
    own = own_key(m.id, name)
    if own:
        return own
    if name in sharing()["shared"] and spent_on_shared(m.id) < cap_for(m.id):
        return os.environ.get(name, default)
    return default


def for_call(env_name: str, resolved_key: str) -> tuple[str, str, str]:
    """For the gateway: (key to use, whose: "owner" | "own" | "shared" | "none", why if none).

    ``resolved_key`` is what the owner's configuration resolved (environment or a saved config key)."""
    m = _member()
    if m is None:
        return resolved_key, "owner", ""
    own = own_key(m.id, env_name) if env_name else ""
    if own:
        return own, "own", ""
    if env_name and env_name in sharing()["shared"]:
        if spent_on_shared(m.id) >= cap_for(m.id):
            return "", "none", (f"{m.name} has used the ${cap_for(m.id):.2f} the owner allows on the shared "
                                f"{env_name}. Add your own key in Settings → Team, or ask the owner to raise the cap.")
        return resolved_key, "shared", ""
    return "", "none", (f"No key for this model: add your own {env_name or 'API key'} in Settings → Team "
                        "(the owner's keys are used only when the owner shares them).")


def status(member_id: str) -> dict[str, Any]:
    """What a member may know: names of their keys, what is shared, their cap and spend. Never a value."""
    share = sharing()
    return {"own": own_key_names(member_id), "available": list(member_key_names()), "shared": share["shared"],
            "cap_usd": cap_for(member_id), "spent_shared_usd": round(spent_on_shared(member_id), 4),
            "at": int(time.time())}
