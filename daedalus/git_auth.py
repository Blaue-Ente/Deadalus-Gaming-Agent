"""Authenticate git over HTTPS without putting a token in a URL, argv or .git/config.

The credential travels only in the child process environment, as a per-host
``http.<url>.extraheader`` set through ``GIT_CONFIG_COUNT`` / ``GIT_CONFIG_KEY_n`` /
``GIT_CONFIG_VALUE_n`` (git >= 2.31). Remote URLs therefore stay clean.
"""
from __future__ import annotations

import base64
import os
from typing import Mapping


def auth_env(host: str, username: str, token: str, base: Mapping[str, str] | None = None) -> dict[str, str]:
    """Return ``base`` (default: ``os.environ``) plus the extraheader config for ``host``."""
    env = dict(base if base is not None else os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"
    tok = (token or "").strip()
    host = (host or "").strip()
    if not tok or not host:
        return env
    try:
        n = int(env.get("GIT_CONFIG_COUNT") or 0)
    except ValueError:
        n = 0
    blob = base64.b64encode(f"{username}:{tok}".encode("utf-8")).decode("ascii")
    env[f"GIT_CONFIG_KEY_{n}"] = f"http.https://{host}/.extraheader"
    env[f"GIT_CONFIG_VALUE_{n}"] = f"AUTHORIZATION: basic {blob}"
    env["GIT_CONFIG_COUNT"] = str(n + 1)
    return env


def scrub(text: str, token: str, username: str = "") -> str:
    """Remove the token and its base64 basic-auth form from log/error text."""
    tok = (token or "").strip()
    if not tok or not text:
        return text
    text = text.replace(tok, "***")
    for user in {username, "x-access-token", "oauth2"} - {""}:
        blob = base64.b64encode(f"{user}:{tok}".encode("utf-8")).decode("ascii")
        text = text.replace(blob, "***")
    return text
