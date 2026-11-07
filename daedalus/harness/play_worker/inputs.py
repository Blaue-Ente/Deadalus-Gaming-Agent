"""OS-level input (XTest via xdotool), always against one session's own display."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
from typing import Any

from .errors import PlayError

_KEY_RE = re.compile(r"^[A-Za-z0-9_+\-]{1,40}$")
_BUTTONS = {"left": "1", "middle": "2", "right": "3"}


class XInput:
    def __init__(self, display: str, window_id: str) -> None:
        self.display = display
        self.window_id = str(window_id)
        self.tool = shutil.which("xdotool")
        if not self.tool:
            raise PlayError("tool_missing", "xdotool is needed to send real keyboard/mouse input", tool="xdotool")

    def _run(self, *args: str, timeout: float = 10.0) -> str:
        env = {**os.environ, "DISPLAY": self.display}
        try:
            res = subprocess.run([self.tool, *args], env=env, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            raise PlayError("window_missing", f"xdotool {args[0]} timed out") from None
        if res.returncode != 0:
            raise PlayError("window_missing", (res.stderr or "xdotool failed").strip()[-200:])
        return res.stdout

    def geometry(self) -> dict[str, int]:
        out = self._run("getwindowgeometry", "--shell", self.window_id)
        vals = dict(line.split("=", 1) for line in out.split() if "=" in line)
        return {k.lower(): int(v) for k, v in vals.items() if k in {"X", "Y", "WIDTH", "HEIGHT"}}

    def focus(self) -> None:
        self._run("windowfocus", self.window_id)

    def key(self, name: str, *, hold_ms: int = 80) -> dict[str, Any]:
        if not _KEY_RE.match(name or ""):
            raise PlayError("bad_request", f"not a key name: {name!r}")
        self.focus()
        self._run("keydown", name)
        time.sleep(max(0, min(hold_ms, 5000)) / 1000.0)
        self._run("keyup", name)
        return {"via": "os", "type": "key", "key": name, "hold_ms": hold_ms}

    def mouse(self, kind: str, x: int, y: int, *, button: str = "left", hold_ms: int = 80) -> dict[str, Any]:
        """``x``/``y`` are window coordinates; the pointer is moved in two steps so the engine sees motion first."""
        if kind not in {"move", "down", "up", "click"}:
            raise PlayError("bad_request", f"unknown mouse kind: {kind!r}")
        btn = _BUTTONS.get((button or "left").lower())
        if btn is None:
            raise PlayError("bad_request", f"unknown mouse button: {button!r}")
        g = self.geometry()
        sx, sy = g.get("x", 0) + int(x), g.get("y", 0) + int(y)
        self.focus()
        self._run("mousemove", str(sx - 2), str(sy - 2))
        time.sleep(0.12)
        self._run("mousemove", str(sx), str(sy))
        time.sleep(0.12)
        if kind in {"down", "click"}:
            self._run("mousedown", btn)
        if kind == "click":
            time.sleep(max(0, min(hold_ms, 5000)) / 1000.0)
        if kind in {"up", "click"}:
            self._run("mouseup", btn)
        return {"via": "os", "type": "mouse", "kind": kind, "x": int(x), "y": int(y), "button": button}

    def find_window(self, pid: int, timeout: float = 15.0) -> str:
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                out = self._run("search", "--pid", str(pid), timeout=5).split()
            except PlayError:
                out = []
            if out:
                self.window_id = out[-1]
                return self.window_id
            time.sleep(0.2)
        raise PlayError("window_missing", "the game opened no window on its display", pid=pid)
