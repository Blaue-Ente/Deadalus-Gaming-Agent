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
UNKNOWN_PRICE_PER_TOKEN = 10.0 / 1_000_000  # a call whose price is unknown still counts (review 2026-11-12)
# The keys Daedalus's model calls actually read for a member (review 2026-11-12: only offer what takes effect).
MEMBER_KEYS = ("OPENROUTER_API_KEY", "ALLTOKEN_API_KEY", "NOVITA_API_KEY", "NVIDIA_API_KEY", "ANTHROPIC_API_KEY",
               "OPENAI_API_KEY", "GEMINI_API_KEY", "GROQ_API_KEY",
               "ELEVENLABS_API_KEY",               # paid_assets.generate_sound (gap 4b): own key only
               "SUBQ_API_KEY")                     # subq_judge: SubQ Snap decisions (shadow mode)
_NAME = re.compile(r"^[A-Z][A-Z0-9_]{2,63}$")
_lock = threading.Lock()
_cache: dict[str, tuple[tuple[int, int], dict[str, Any]]] = {}


def member_key_names() -> tuple[str, ...]:
    return MEMBER_KEYS


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
    _cache.pop(str(path), None)


def _read(path: Path) -> dict[str, Any]:
    """A JSON file, re-read only when it changed (model calls ask on every call)."""
    try:
        st = path.stat()
        stamp = (st.st_mtime_ns, st.st_size)
        hit = _cache.get(str(path))
        if hit and hit[0] == stamp:
            return json.loads(json.dumps(hit[1]))
        data = json.loads(path.read_text(encoding="utf-8"))
        data = data if isinstance(data, dict) else {}
        _cache[str(path)] = (stamp, data)
        return json.loads(json.dumps(data))
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
    """The value of key ``name`` for whoever this call runs for. The owner and server tasks: the environment.

    A member gets only their own key here. These readers call a provider directly, outside the metering that
    counts a member's spend, so a shared owner key never reaches them (review 2026-11-12); shared keys apply to
    the metered model calls (``member_config`` and the gateway)."""
    m = _member()
    if m is None:
        return os.environ.get(name, default)
    return own_key(m.id, name) or default


def _under_cap(member_id: str, share: dict[str, Any]) -> bool:
    return float(share["spend"].get(member_id, 0.0)) < float(share["caps"].get(member_id, DEFAULT_CAP_USD))


# The provider a member's own key can serve the owner's model through when the owner's provider has no key of
# theirs (review 2026-11-12: a designer with an OpenRouter key got 'add your ANTHROPIC_API_KEY').
_SWITCH = (("openrouter", "OPENROUTER_API_KEY"), ("alltoken", "ALLTOKEN_API_KEY"))


def member_config(cfg: dict[str, Any]) -> dict[str, Any]:
    """The model configuration a member's call runs with: their own key for the owner's provider; else the owner's
    model through a provider they hold a key for; else the owner's key if shared and under their cap; else no key
    (the call then fails and says which key to add). The owner and server tasks: ``cfg`` unchanged."""
    m = _member()
    if m is None or not isinstance(cfg, dict):
        return cfg
    from .llm_config import PROVIDER_ENV_KEYS, is_loopback_gateway, provider_default_base_url
    from .local_llm import is_local_provider

    provider = str(cfg.get("provider") or "").strip().lower()
    if is_local_provider(provider) or cfg.get("local") or is_loopback_gateway(provider):
        return cfg                                           # a model on this machine: no key, no credits
    out = dict(cfg)
    env_name = PROVIDER_ENV_KEYS.get(provider, "")
    own = own_key(m.id, env_name) if env_name else ""
    if own:
        out.update(api_key=own, key_owner="own")
        return out
    for other, other_env in _SWITCH:
        mine = own_key(m.id, other_env)
        if mine and other != provider:
            from .model_defaults import OPENROUTER_DEFAULT_MODEL
            model = str(cfg.get("model") or "")
            out.update(provider=other, api_key=mine, base_url=provider_default_base_url(other), sdk="openai",
                       model=model if "/" in model else OPENROUTER_DEFAULT_MODEL, key_owner="own")
            return out
    share = sharing()
    if env_name and env_name in share["shared"] and _under_cap(m.id, share):
        out.update(api_key=str(cfg.get("api_key") or "") or os.environ.get(env_name, ""), key_owner="shared")
        return out
    out.update(api_key="", key_owner="none")
    return out


def note_member_usage(parsed: dict[str, Any] | None, provider: str) -> None:
    """Count a member's metered call on the owner's shared key (called by run_budget.note_usage for every billed
    call). A call whose price is unknown counts at ``UNKNOWN_PRICE_PER_TOKEN``. Never raises."""
    m = _member()
    if m is None:
        return
    try:
        from .llm_config import PROVIDER_ENV_KEYS
        env_name = PROVIDER_ENV_KEYS.get((provider or "").strip().lower(), "")
        if not env_name or own_key(m.id, env_name) or env_name not in sharing()["shared"]:
            return                                          # their own key, or not a shared one: not the owner's money
        parsed = parsed or {}
        cost = parsed.get("cost_usd") if parsed.get("cost_known") else None
        if cost is None:
            tokens = int(parsed.get("prompt_tokens") or 0) + int(parsed.get("completion_tokens") or 0)
            cost = tokens * UNKNOWN_PRICE_PER_TOKEN
        note_shared_spend(m.id, float(cost))
    except Exception:
        log.warning("member usage not counted", exc_info=True)


def for_call(env_name: str, resolved_key: str) -> tuple[str, str, str]:
    """For the gateway: (key to use, whose: "owner" | "own" | "shared" | "none", why if none).

    ``resolved_key`` is what the owner's configuration resolved (environment or a saved config key)."""
    m = _member()
    if m is None:
        return resolved_key, "owner", ""
    own = own_key(m.id, env_name) if env_name else ""
    if own:
        return own, "own", ""
    share = sharing()
    if env_name and env_name in share["shared"]:
        if not _under_cap(m.id, share):
            return "", "none", (f"{m.name} has used the ${cap_for(m.id):.2f} the owner allows on the shared "
                                f"{env_name}. Add your own key in Settings → Team, or ask the owner to raise the cap.")
        return resolved_key or os.environ.get(env_name, ""), "shared", ""
    return "", "none", (f"No key for this model: add your own {env_name or 'API key'} in Settings → Team "
                        "(the owner's keys are used only when the owner shares them).")


def status(member_id: str) -> dict[str, Any]:
    """What a member may know: names of their keys, what is shared, their cap and spend. Never a value."""
    share = sharing()
    return {"own": own_key_names(member_id), "available": list(member_key_names()), "shared": share["shared"],
            "cap_usd": cap_for(member_id), "spent_shared_usd": round(spent_on_shared(member_id), 4),
            "at": int(time.time())}
