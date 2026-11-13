"""Opt-in "use the local model when the cloud fails" policy.

``fallback.mode``: ``off`` (default, never retry elsewhere), ``ask`` (do not retry, but tell the caller a local
retry is available) or ``auto`` (retry once on local Ollama). ``fallback.triggers``: ``both``, ``credits`` or
``offline`` decide which failures count. Saved in ``settings.json``; ``DAEDALUS_FALLBACK`` pins the mode.
"""
from __future__ import annotations

import os
import re

MODES = ("off", "ask", "auto")
TRIGGERS = ("both", "credits", "offline")
DEFAULT_MODE = "off"
DEFAULT_TRIGGERS = "both"

_CREDIT_CODES = re.compile(r"\b(401|402|403|429)\b")
_OFFLINE_CODES = re.compile(r"\b(502|503)\b")
_CREDIT_WORDS = ("quota", "credit", "insufficient", "unauthorized", "invalid_api_key",
                 "incorrect api key", "permission", "rate limit", "billing")
_OFFLINE_WORDS = ("connection", "timeout", "timed out", "dns", "name resolution", "unreachable", "econnrefused",
                  "unavailable")


def _saved(key: str) -> str | None:
    try:
        from . import settings_file

        raw = settings_file.load().get(key)
    except Exception:
        return None
    return str(raw).strip().lower() if raw is not None else None


def mode_env() -> str | None:
    raw = (os.environ.get("DAEDALUS_FALLBACK") or "").strip().lower()
    return raw if raw in MODES else None


def mode() -> str:
    env = mode_env()
    if env:
        return env
    saved = _saved("fallback_mode")
    return saved if saved in MODES else DEFAULT_MODE


def triggers() -> str:
    saved = _saved("fallback_triggers")
    return saved if saved in TRIGGERS else DEFAULT_TRIGGERS


def classify(error: str) -> str | None:
    """``credits``, ``offline`` or None for an error text."""
    t = (error or "").lower()
    if _CREDIT_CODES.search(t) or any(w in t for w in _CREDIT_WORDS):
        return "credits"
    if _OFFLINE_CODES.search(t) or any(w in t for w in _OFFLINE_WORDS):
        return "offline"
    return None


def matches(error: str) -> str | None:
    """The kind of failure (``credits``/``offline``) when it is one the owner enabled, else None."""
    kind = classify(error)
    if kind is None:
        return None
    want = triggers()
    return kind if want in ("both", kind) else None
