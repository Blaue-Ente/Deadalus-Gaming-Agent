"""The platform seam of the play worker: everything that differs between Linux and Windows lives behind this.

A backend owns "where the game is drawn and how we look at it and drive it": a virtual display + xdotool on Linux,
the owner's real desktop + Win32 on Windows. The session, proof levels, artifacts and honesty rules are shared.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any, Protocol

from .errors import PlayError


class Backend(Protocol):
    name: str
    max_concurrent: int            # sessions that can run at once on this platform

    def tools(self) -> dict[str, bool]: ...
    def start(self, size: tuple[int, int]) -> dict[str, str]: ...      # -> extra environment for the game
    def stop(self) -> None: ...
    def label(self) -> str: ...                                        # shown in status (display name, "desktop")
    def engine_args(self, size: tuple[int, int]) -> tuple[str, ...]: ...
    def popen_flags(self) -> dict[str, Any]: ...                       # extra Popen kwargs for the game process
    def find_window(self, pid: int) -> str: ...
    def capture(self, window: str, out: Path) -> dict[str, Any]: ...
    def key(self, name: str, hold_ms: int) -> dict[str, Any]: ...
    def mouse(self, kind: str, x: int, y: int, button: str, hold_ms: int) -> dict[str, Any]: ...
    def kill(self, proc: subprocess.Popen | None, grace: float) -> None: ...


class LinuxBackend:
    """Xvfb display per session, xdotool input, ImageMagick `import` capture (the original A5a code, unchanged)."""

    name = "linux"
    max_concurrent = 3

    def __init__(self, size: tuple[int, int] = (640, 480)) -> None:
        from .display import XvfbDisplay

        self.display = XvfbDisplay(size)
        self.xin: Any = None
        self._window = ""

    def tools(self) -> dict[str, bool]:
        import shutil
        return {"Xvfb": bool(shutil.which("Xvfb")), "xdotool": bool(shutil.which("xdotool")),
                "import": bool(shutil.which("import"))}

    def start(self, size: tuple[int, int]) -> dict[str, str]:
        name = self.display.start()
        return {"DISPLAY": name, "LIBGL_ALWAYS_SOFTWARE": "1"}

    def stop(self) -> None:
        self.display.stop()

    def label(self) -> str:
        return self.display.name if self.display.number is not None else ""

    def engine_args(self, size: tuple[int, int]) -> tuple[str, ...]:
        w, h = size
        return ("--rendering-driver", "opengl3", "--resolution", f"{w}x{h}")

    def popen_flags(self) -> dict[str, Any]:
        return {"new_session": True}

    def find_window(self, pid: int) -> str:
        from .inputs import XInput

        self.xin = XInput(self.display.name, "")
        self._window = self.xin.find_window(pid)
        return self._window

    def capture(self, window: str, out: Path) -> dict[str, Any]:
        from . import capture
        return capture.capture_window(self.display.name, window, out)

    def key(self, name: str, hold_ms: int) -> dict[str, Any]:
        return self.xin.key(name, hold_ms=hold_ms)

    def mouse(self, kind: str, x: int, y: int, button: str, hold_ms: int) -> dict[str, Any]:
        return self.xin.mouse(kind, x, y, button=button, hold_ms=hold_ms)

    def kill(self, proc: subprocess.Popen | None, grace: float) -> None:
        from .display import kill_group
        kill_group(proc, grace=grace)


def platform_cap() -> int:
    """How many sessions this platform can run at once (Windows: one desktop, one pointer, one keyboard)."""
    return 1 if sys.platform == "win32" else 3


def get_backend(size: tuple[int, int] = (640, 480)) -> Backend:
    if sys.platform == "win32":
        from .win32 import WindowsBackend
        return WindowsBackend()
    if sys.platform.startswith("linux"):
        return LinuxBackend(size)
    raise PlayError("unsupported_platform", f"no play backend for {sys.platform} yet (Linux and Windows only)")
