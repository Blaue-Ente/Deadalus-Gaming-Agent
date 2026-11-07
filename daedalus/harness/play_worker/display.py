"""One X display per session: Xvfb on a free display number, in its own process group."""
from __future__ import annotations

import os
import shutil
import signal
import subprocess
import time
from pathlib import Path

from .errors import PlayError


def kill_group(proc: subprocess.Popen | None, *, grace: float = 3.0) -> None:
    """SIGTERM then SIGKILL the whole process group (the child was started with start_new_session)."""
    if proc is None:
        return
    try:
        pgid = os.getpgid(proc.pid)
    except (ProcessLookupError, OSError):
        pgid = None
    for sig in (signal.SIGTERM, signal.SIGKILL):
        if proc.poll() is not None and sig == signal.SIGKILL:
            break
        try:
            if pgid is not None:
                os.killpg(pgid, sig)
            elif proc.poll() is None:
                proc.send_signal(sig)
        except (ProcessLookupError, PermissionError, OSError):
            pass
        deadline = time.time() + (grace if sig == signal.SIGTERM else 2.0)
        while time.time() < deadline and proc.poll() is None:
            time.sleep(0.05)
        if proc.poll() is not None:
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

    @property
    def name(self) -> str:
        return f":{self.number}"

    def _free_number(self) -> int:
        for n in range(90, 400):
            if not Path(f"/tmp/.X11-unix/X{n}").exists() and not Path(f"/tmp/.X{n}-lock").exists():
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
            self.proc = subprocess.Popen(
                [xvfb, self.name, "-screen", "0", f"{w}x{h}x24", "-nolisten", "tcp", "-noreset"],
                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, start_new_session=True,
            )
            deadline = time.time() + timeout
            sock = Path(f"/tmp/.X11-unix/X{self.number}")
            while time.time() < deadline:
                if self.proc.poll() is not None:
                    last = (self.proc.stderr.read() or b"").decode("utf-8", "replace")[-300:] if self.proc.stderr else ""
                    break
                if sock.exists():
                    return self.name
                time.sleep(0.05)
            kill_group(self.proc)
        raise PlayError("display_unavailable", f"Xvfb did not start: {last or 'timeout'}")

    def stop(self) -> None:
        kill_group(self.proc)
        self.proc = None
