"""All sessions of one worker: the concurrency limit, the idle reaper, and kill-everything."""
from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from typing import Any

from .errors import PlayError
from .session import PlaySession

IDLE_TIMEOUT_S = 600.0


def max_sessions() -> int:
    """Default 1, configurable to 3 (``DAEDALUS_PLAY_MAX`` or ``play_max_sessions`` in settings.json)."""
    raw = (os.environ.get("DAEDALUS_PLAY_MAX") or "").strip()
    if not raw:
        try:
            from ... import settings_file
            raw = str(settings_file.load().get("play_max_sessions") or "")
        except Exception:
            raw = ""
    try:
        return max(1, min(3, int(raw)))
    except ValueError:
        return 1


class PlayManager:
    def __init__(self, *, limit: int | None = None, idle_timeout: float = IDLE_TIMEOUT_S) -> None:
        self._limit = limit
        self.idle_timeout = idle_timeout
        self._sessions: dict[str, PlaySession] = {}
        self._lock = threading.RLock()

    @property
    def limit(self) -> int:
        return self._limit if self._limit is not None else max_sessions()

    def start(self, slug: str, project_dir: Path, *, engine: str = "godot", size: tuple[int, int] = (640, 480)) -> dict[str, Any]:
        if engine != "godot":
            raise PlayError("unsupported_engine", f"{engine!r} has no play worker yet (Godot first)", engine=engine)
        self.reap_idle()
        with self._lock:
            live = [s for s in self._sessions.values() if s.state in {"new", "running"}]
            if len(live) >= self.limit:
                raise PlayError("session_limit", f"{len(live)} of {self.limit} play sessions already running",
                                running=[s.id for s in live])
            sess = PlaySession(project_dir, slug=slug, size=size)
            self._sessions[sess.id] = sess
        return sess.start()

    def get(self, sid: str) -> PlaySession:
        with self._lock:
            sess = self._sessions.get(sid)
        if sess is None:
            raise PlayError("not_found", f"no play session {sid!r}")
        return sess

    def stop(self, sid: str) -> dict[str, Any]:
        return self.get(sid).stop()

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            return [s.status() for s in self._sessions.values()]

    def reap_idle(self) -> list[str]:
        now = time.time()
        reaped: list[str] = []
        with self._lock:
            idle = [s for s in self._sessions.values() if s.state == "running" and now - s.last_used > self.idle_timeout]
        for s in idle:
            s.stop()
            reaped.append(s.id)
        return reaped

    def stop_all(self) -> int:
        with self._lock:
            sessions = list(self._sessions.values())
        for s in sessions:
            try:
                s.stop()
            except Exception:
                pass
        return len(sessions)
