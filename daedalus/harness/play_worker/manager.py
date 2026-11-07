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
KEEP_FINISHED = 20


def max_sessions() -> int:
    """Default 1, configurable to 3 (``DAEDALUS_PLAY_MAX`` or ``play_max_sessions`` in settings.json)."""
    def parse(raw: Any) -> int | None:
        try:
            return max(1, min(3, int(str(raw).strip())))
        except (TypeError, ValueError):
            return None

    from .backend import platform_cap

    cap = platform_cap()
    pinned = parse(os.environ.get("DAEDALUS_PLAY_MAX") or "")
    if pinned is not None:
        return min(pinned, cap)            # an unparsable pin is ignored (it does not lock), not treated as 1
    try:
        from ... import settings_file
        saved = parse(settings_file.load().get("play_max_sessions"))
    except Exception:
        saved = None
    return min(saved, cap) if saved is not None else 1


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
        if engine not in {"godot", "unity"}:
            raise PlayError("unsupported_engine", f"{engine!r} has no play worker", engine=engine)
        self.reap_idle()
        self.reap_dead()
        with self._lock:
            live = [s for s in self._sessions.values() if s.refresh() in {"new", "running"}]
            if len(live) >= self.limit:
                raise PlayError("session_limit", f"{len(live)} of {self.limit} play sessions already running",
                                running=[s.id for s in live])
            sess = PlaySession(project_dir, slug=slug, size=size, engine=engine)
            self._sessions[sess.id] = sess
            self._prune()
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

    def _prune(self) -> None:
        """Forget old finished sessions (their artifacts stay on disk); the dict must not grow forever."""
        done = [s for s in self._sessions.values() if s.state in {"stopped", "failed"}]
        for s in sorted(done, key=lambda x: x.started_at or 0)[: max(0, len(done) - KEEP_FINISHED)]:
            self._sessions.pop(s.id, None)

    def reap_dead(self) -> list[str]:
        """A game that crashed leaves its display behind: clean it up and free the slot."""
        with self._lock:
            dead = [s for s in self._sessions.values() if s.state == "running" and s.refresh() == "failed"]
        for s in dead:
            s.stop()
        return [s.id for s in dead]

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
