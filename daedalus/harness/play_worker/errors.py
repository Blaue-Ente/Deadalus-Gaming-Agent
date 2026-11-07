"""Typed, honest failures. A missing engine or tool is ``blocked``, never a pass."""
from __future__ import annotations

from typing import Any


class PlayError(Exception):
    def __init__(self, code: str, message: str = "", **extra: Any) -> None:
        super().__init__(message or code)
        self.code = code
        self.extra = extra

    def payload(self) -> dict[str, Any]:
        return {"ok": False, "error": self.code, "message": str(self), **self.extra}


# codes: engine_missing, display_unavailable, tool_missing, session_limit, not_found, start_failed,
#        unsupported_engine, bad_request, window_missing, session_stopped
