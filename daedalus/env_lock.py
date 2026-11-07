"""An environment variable locks a setting: writes are refused, never silently saved."""
from __future__ import annotations


class EnvLockedError(Exception):
    """The setting is pinned by an environment variable and cannot be changed from the UI/API."""

    def __init__(self, field: str, env: str) -> None:
        super().__init__(f"{field} is locked by {env}")
        self.field = field
        self.env = env

    def payload(self) -> dict:
        return {"ok": False, "error": "env_locked", "field": self.field, "env": self.env}
