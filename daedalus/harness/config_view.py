"""ConfigView: every setting the owner can see, in one shape.

Each row says what is in effect, what is saved, whether an environment variable pins it, and where the
effective value comes from. Settings keep their own storage and rules; this module reads them through their
existing status functions and writes through their existing ``apply_update`` (so an env lock raises
``EnvLockedError`` before anything is saved). No secret value is ever returned.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any, Callable

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Setting:
    key: str
    title_key: str          # i18n key for the label
    group: str              # runtime | spend | quality | evolution | storage
    kind: str               # bool | int | str | enum
    env: str                # env var that pins it ("" = none)
    read: Callable[[], dict[str, Any]]      # -> {effective, persisted, default}
    write: Callable[[Any], Any] | None      # None = read-only here
    secret: bool = False
    choices: Callable[[], list[str]] | None = None   # enum settings: the accepted values
    bounds: tuple[int, int] | None = None            # int settings: inclusive (low, high)


def _mentor_enabled() -> dict[str, Any]:
    from .. import mentor
    st = mentor.status()
    return {"effective": st["enabled"], "persisted": st["persisted"], "default": st["default"]}


def _write_mentor_enabled(v: Any) -> Any:
    from .. import mentor
    return mentor.apply_update({"enabled": bool(v)})


def _mentor_cap() -> dict[str, Any]:
    from .. import mentor_helm
    stored = mentor_helm._kv_json(mentor_helm.KV_DAY_CAP, None)
    saved: int | None = None
    if isinstance(stored, dict) and stored.get("cap") is not None:
        saved = int(stored["cap"])
    elif isinstance(stored, (int, float)):
        saved = int(stored)
    return {"effective": mentor_helm.day_token_cap(), "persisted": saved, "default": mentor_helm.DEFAULT_DAY_CAP}


def _write_mentor_cap(v: Any) -> Any:
    from .. import mentor
    return mentor.apply_update({"day_token_cap": int(v)})


def _budget() -> dict[str, Any]:
    from .. import run_budget
    st = run_budget.status()
    return {"effective": st["usd"], "persisted": st["persisted"], "default": st["default"]}


def _write_budget(v: Any) -> Any:
    from .. import run_budget
    return run_budget.apply_update({"usd": v})


def _writer_assist() -> dict[str, Any]:
    from .. import writer_assist
    st = writer_assist.status()
    return {"effective": st["mode"], "persisted": st["persisted"], "default": st["default"]}


def _write_writer_assist(v: Any) -> Any:
    from .. import writer_assist
    return writer_assist.apply_update({"mode": str(v)})


def _writer_modes() -> list[str]:
    from .. import writer_assist
    return list(writer_assist.MODES)


def _play_max() -> dict[str, Any]:
    from .. import settings_file
    from .play_worker.manager import max_sessions
    raw = settings_file.load().get("play_max_sessions")
    return {"effective": max_sessions(), "persisted": raw if isinstance(raw, int) else None, "default": 1}


def _write_play_max(v: Any) -> Any:
    from .. import settings_file
    return settings_file.update({"play_max_sessions": int(v)})


def _evolution() -> dict[str, Any]:
    from .. import evolution, settings_file
    persisted = settings_file.load().get("evolution_enabled")
    return {"effective": evolution.enabled(), "persisted": persisted, "default": False}


def _write_evolution(v: Any) -> Any:
    from .. import evolution
    return evolution.set_enabled(bool(v))


def _auto_apply() -> dict[str, Any]:
    from .. import auto_apply, settings_file
    return {"effective": auto_apply.is_auto_apply_enabled(),
            "persisted": settings_file.load().get("auto_apply_enabled"), "default": False}


def _write_auto_apply(v: Any) -> Any:
    from .. import auto_apply
    return auto_apply.set_auto_apply_enabled(bool(v))


def _kv() -> dict[str, Any]:
    from .. import kv
    b = kv.backend()
    return {"effective": b["effective"], "persisted": None, "default": "json"}


def _brain() -> dict[str, Any]:
    from . import gateway
    res = gateway.resolve("persona")
    return {"effective": f"{res.provider}:{res.model}" if res.model else "", "persisted": None, "default": ""}


SETTINGS: tuple[Setting, ...] = (
    Setting("mentor.enabled", "settings.mentorEnabled", "quality", "bool", "DAEDALUS_MENTOR", _mentor_enabled, _write_mentor_enabled),
    Setting("mentor.day_token_cap", "settings.mentorDayCap", "spend", "int", "DAEDALUS_MENTOR_DAY_CAP", _mentor_cap, _write_mentor_cap),
    Setting("run_budget.usd", "settings.runBudget", "spend", "int", "DAEDALUS_RUN_BUDGET", _budget, _write_budget),
    Setting("writer_assist.mode", "settings.writerAssist", "quality", "enum", "DAEDALUS_WRITER_ASSIST", _writer_assist, _write_writer_assist,
            choices=_writer_modes),
    Setting("evolution.enabled", "settings.evolution", "evolution", "bool", "DAEDALUS_EVOLUTION", _evolution, _write_evolution),
    Setting("auto_apply.enabled", "settings.autoApply", "evolution", "bool", "", _auto_apply, _write_auto_apply),
    Setting("play.max_sessions", "settings.playMax", "runtime", "int", "DAEDALUS_PLAY_MAX", _play_max, _write_play_max,
            bounds=(1, 3)),
    Setting("kv.backend", "settings.kvBackend", "storage", "enum", "DAEDALUS_KV", _kv, None),
    Setting("brain.model", "settings.brainModel", "runtime", "str", "DAEDALUS_MODEL", _brain, None),
)

_BY_KEY = {s.key: s for s in SETTINGS}


def _env_value(s: Setting) -> str | None:
    if not s.env or s.secret:
        return None
    raw = (os.environ.get(s.env) or "").strip()
    return raw or None


def _locked(s: Setting, env_value: str | None) -> bool:
    """Same truthiness the setting's own module uses: an unparsable pin does not lock."""
    if env_value is None:
        return False
    if s.kind == "bool":
        return env_value.lower() in {"0", "false", "off", "no", "1", "true", "on", "yes"}
    if s.kind == "int":
        try:
            int(env_value)
        except ValueError:
            return False
    return True


def _coerce(s: Setting, value: Any) -> Any:
    """Validate before anything is stored: the settings' own normalisers silently turn junk into a default."""
    if s.kind == "bool":
        if isinstance(value, bool):
            return value
        text = str(value).strip().lower()
        if text in {"1", "true", "on", "yes"}:
            return True
        if text in {"0", "false", "off", "no"}:
            return False
        raise ValueError(f"{s.key} expects true or false")
    if s.kind == "int":
        if isinstance(value, bool):
            raise ValueError(f"{s.key} expects a whole number")
        number = int(str(value).strip())      # ValueError for junk
        lo, hi = s.bounds or (0, 2**31 - 1)
        if not lo <= number <= hi:
            raise ValueError(f"{s.key} must be between {lo} and {hi}" if s.bounds else f"{s.key} must be 0 or more")
        return number
    if s.kind == "enum" and s.choices is not None:
        text = str(value).strip().lower()
        allowed = s.choices()
        if text not in allowed:
            raise ValueError(f"{s.key} must be one of: {', '.join(allowed)}")
        return text
    return value


def view_one(s: Setting) -> dict[str, Any]:
    env_value = _env_value(s)
    locked = _locked(s, env_value)
    row: dict[str, Any] = {
        "key": s.key, "title_key": s.title_key, "group": s.group, "kind": s.kind,
        "env": s.env or None, "env_locked": locked, "env_value": env_value if locked else None,
        "writable": s.write is not None and not locked,
    }
    try:
        got = s.read()
        row.update(effective=got.get("effective"), persisted=got.get("persisted"), default=got.get("default"))
        row["source"] = "env" if locked else ("saved" if got.get("persisted") is not None else "default")
    except Exception as exc:
        log.warning("config view: %s unavailable: %s", s.key, exc)
        row.update(effective=None, persisted=None, default=None, source="unavailable", error=str(exc)[:160])
    return row


def config_payload() -> dict[str, Any]:
    return {"ok": True, "settings": [view_one(s) for s in SETTINGS]}


def set_value(key: str, value: Any) -> dict[str, Any]:
    """Write one setting. Raises KeyError (unknown), PermissionError (read-only here), EnvLockedError (pinned)."""
    from ..env_lock import EnvLockedError

    s = _BY_KEY.get(key)
    if s is None:
        raise KeyError(key)
    if s.write is None:
        raise PermissionError(key)
    if _locked(s, _env_value(s)):
        raise EnvLockedError(key, s.env)  # before any write, whichever module owns the storage
    s.write(_coerce(s, value))
    return view_one(s)
