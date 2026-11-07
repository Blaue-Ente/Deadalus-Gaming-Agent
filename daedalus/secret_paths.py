"""Paths that must never leave the machine inside a zip, bundle or publish.

Used by the capsule/publish zipper and by bundle path validation. Matching is by
file name only (never by content), so no secret value is ever read here.
"""
from __future__ import annotations

import fnmatch
from pathlib import PurePosixPath

_ALLOWED_ENV_TEMPLATES = frozenset({".env.example", ".env.sample", ".env.template"})

_SECRET_NAME_GLOBS = (
    ".env",
    ".env.*",
    "secrets.env",
    "*.pem",
    "*.key",
    "*.p12",
    "*.pfx",
    "*.keystore",
    "id_rsa*",
    "id_ed25519*",
    ".npmrc",
    ".netrc",
)


def is_secret_name(name: str) -> bool:
    low = (name or "").strip().lower()
    if not low:
        return False
    if low in _ALLOWED_ENV_TEMPLATES:
        return False
    return any(fnmatch.fnmatchcase(low, pat) for pat in _SECRET_NAME_GLOBS)


def is_secret_path(rel) -> bool:
    """True when any component of the relative path names a secret file."""
    parts = PurePosixPath(str(rel).replace("\\", "/")).parts
    return any(is_secret_name(p) for p in parts)
