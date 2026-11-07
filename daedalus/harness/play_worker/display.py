"""One X display per session: Xvfb on a free display number, in its own process group."""
from __future__ import annotations

import os
import shutil
import signal
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

from .errors import PlayError


def _group_alive(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
    except (ProcessLookupError, PermissionError, OSError):
        return False
    return True


def kill_group(proc: subprocess.Popen | None, *, grace: float = 3.0) -> None:
    """SIGTERM then SIGKILL the whole process group (the child was started with start_new_session)."""
    if proc is None:
        return
    # Every child here was started with start_new_session, so its group id IS its pid, and the group outlives a
    # reaped leader (GodotSession.close() waits on the leader first). killpg on an empty group is a no-op error.
    pgid: int | None = proc.pid
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(pgid, sig)
        except (ProcessLookupError, PermissionError, OSError):
            if proc.poll() is not None:
                break                                   # leader reaped and the group is already empty
        deadline = time.time() + (grace if sig == signal.SIGTERM else 2.0)
        while time.time() < deadline and _group_alive(pgid):
            time.sleep(0.05)
        if not _group_alive(pgid):
            break
    try:
        proc.wait(timeout=1)
    except Exception:
        pass


class XvfbDisplay:
    def __init__(self, size: tuple[int, int] = (640, 480)) -> None:
        self.size = size
        self.number: int | None = None
        self.proc: subprocess.Popen | None = None
        self._log: Any = None

    @property
    def name(self) -> str:
        return f":{self.number}"

    def _free_number(self) -> int:
        for n in range(90, 400):
            if Path(f"/tmp/.X11-unix/X{n}").exists():
                continue
            lock = Path(f"/tmp/.X{n}-lock")
            if lock.exists():
                try:                                     # a lock whose server is dead is stale (Xvfb replaces it)
                    os.kill(int(lock.read_text().strip()), 0)
                    continue
                except (ValueError, OSError):
                    pass
            return n
        raise PlayError("display_unavailable", "no free X display number")

    def start(self, timeout: float = 8.0) -> str:
        xvfb = shutil.which("Xvfb")
        if not xvfb:
            raise PlayError("tool_missing", "Xvfb is not installed; a rendered session needs a display", tool="Xvfb")
        w, h = self.size
        last = ""
        for _attempt in range(3):  # two sessions may race for the same number: take the next one
            self.number = self._free_number()
            self._log = tempfile.TemporaryFile()                 # a file, not a pipe nobody drains
            self.proc = subprocess.Popen(
                [xvfb, self.name, "-screen", "0", f"{w}x{h}x24", "-nolisten", "tcp", "-noreset"],
                stdout=subprocess.DEVNULL, stderr=self._log, start_new_session=True,
            )
            deadline = time.time() + timeout
            sock = Path(f"/tmp/.X11-unix/X{self.number}")
            while time.time() < deadline:
                if self.proc.poll() is not None:
                    self._log.seek(0)
                    last = self._log.read().decode("utf-8", "replace")[-300:]
                    break
                if sock.exists():
                    return self.name
                time.sleep(0.05)
            kill_group(self.proc)
        raise PlayError("display_unavailable", f"Xvfb did not start: {last or 'timeout'}")

    def stop(self) -> None:
        kill_group(self.proc)
        self.proc = None
        if self._log is not None:
            try:
                self._log.close()
            except OSError:
                pass
            self._log = None
