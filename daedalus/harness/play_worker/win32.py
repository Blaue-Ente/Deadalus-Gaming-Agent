"""Windows backend: the game runs in a real window on the owner's desktop; we look and drive through Win32.

Honesty and safety rules (this machine belongs to a person who may be using it):
- Input is sent only while the game window is the foreground window. If focus cannot be taken or is lost, the call
  fails with ``focus_lost`` instead of typing into whatever else is in front.
- Mouse targets must lie inside the game's client area.
- One session at a time: there is one desktop, one pointer, one keyboard.
- ``capture_method`` is recorded with every frame ("screen" = what the monitor shows, "printwindow" = what DWM
  renders for the window); a failed capture is an error, never a blank frame.
"""
from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import time
from ctypes import wintypes as wt
from pathlib import Path
from typing import Any

from .errors import PlayError

# ---- pure helpers (testable anywhere) --------------------------------------------------------------------------------
_NAMED_KEYS = {
    "space": (0x20, False), "enter": (0x0D, False), "return": (0x0D, False), "escape": (0x1B, False),
    "esc": (0x1B, False), "tab": (0x09, False), "backspace": (0x08, False),
    "left": (0x25, True), "up": (0x26, True), "right": (0x27, True), "down": (0x28, True),
    "shift": (0x10, False), "ctrl": (0x11, False), "control": (0x11, False), "alt": (0x12, False),
}


def vk_for(name: str) -> tuple[int, bool]:
    """(virtual-key code, is_extended) for a key name, or PlayError(bad_request) for one we do not map."""
    key = (name or "").strip().lower()
    if key in _NAMED_KEYS:
        return _NAMED_KEYS[key]
    if len(key) == 1 and key.isalnum() and key.isascii():
        return ord(key.upper()), False
    if key.startswith("f") and key[1:].isdigit() and 1 <= int(key[1:]) <= 12:
        return 0x70 + int(key[1:]) - 1, False
    raise PlayError("bad_request", f"key not supported on Windows: {name!r}")


def bgra_to_rgba(bgra: bytes) -> bytes:
    """GDI hands back BGRA; PNG wants RGBA. Alpha is forced opaque (a captured window has no meaningful alpha)."""
    n = len(bgra) - (len(bgra) % 4)
    out = bytearray(n)
    out[0::4] = bgra[2:n:4]
    out[1::4] = bgra[1:n:4]
    out[2::4] = bgra[0:n:4]
    out[3::4] = b"\xff" * (n // 4)
    return bytes(out)


def tree_pids(pairs: list[tuple[int, int]], root: int) -> set[int]:
    """``root`` and every descendant, from (pid, parent pid) pairs. The Godot console build is only a wrapper that
    starts the real engine as a child, and that child owns the window."""
    out = {root}
    grew = True
    while grew:
        grew = False
        for pid, ppid in pairs:
            if ppid in out and pid not in out:
                out.add(pid)
                grew = True
    return out


def normalise_absolute(x: int, y: int, left: int, top: int, width: int, height: int) -> tuple[int, int]:
    """Screen pixel -> the 0..65535 space SendInput uses for ABSOLUTE movement over the virtual desktop.

    Windows maps a normalised value back with ``pixel = norm * size // 65536``, so the value that lands exactly on
    ``pixel`` is ``ceil(pixel * 65536 / size)``. Truncating instead lands one pixel short, which at a window edge
    puts the pointer outside the window."""
    nx = -(-((x - left) * 65536) // max(1, width))
    ny = -(-((y - top) * 65536) // max(1, height))
    return max(0, min(65535, nx)), max(0, min(65535, ny))


# ---- Win32 structures --------------------------------------------------------------------------------------------------
ULONG_PTR = ctypes.c_size_t


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wt.WORD), ("wScan", wt.WORD), ("dwFlags", wt.DWORD), ("time", wt.DWORD),
                ("dwExtraInfo", ULONG_PTR)]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wt.LONG), ("dy", wt.LONG), ("mouseData", wt.DWORD), ("dwFlags", wt.DWORD),
                ("time", wt.DWORD), ("dwExtraInfo", ULONG_PTR)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", wt.DWORD), ("u", _INPUTUNION)]


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [("dwSize", wt.DWORD), ("cntUsage", wt.DWORD), ("th32ProcessID", wt.DWORD),
                ("th32DefaultHeapID", ULONG_PTR), ("th32ModuleID", wt.DWORD), ("cntThreads", wt.DWORD),
                ("th32ParentProcessID", wt.DWORD), ("pcPriClassBase", wt.LONG), ("dwFlags", wt.DWORD),
                ("szExeFile", wt.WCHAR * 260)]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG), ("biPlanes", wt.WORD),
                ("biBitCount", wt.WORD), ("biCompression", wt.DWORD), ("biSizeImage", wt.DWORD),
                ("biXPelsPerMeter", wt.LONG), ("biYPelsPerMeter", wt.LONG), ("biClrUsed", wt.DWORD),
                ("biClrImportant", wt.DWORD)]


INPUT_MOUSE, INPUT_KEYBOARD = 0, 1
KEYEVENTF_EXTENDEDKEY, KEYEVENTF_KEYUP, KEYEVENTF_SCANCODE = 0x1, 0x2, 0x8
MOUSEEVENTF_MOVE, MOUSEEVENTF_ABSOLUTE, MOUSEEVENTF_VIRTUALDESK = 0x1, 0x8000, 0x4000
_BUTTON_FLAGS = {"left": (0x2, 0x4), "right": (0x8, 0x10), "middle": (0x20, 0x40)}
SRCCOPY, CAPTUREBLT = 0x00CC0020, 0x40000000
PW_CLIENTONLY, PW_RENDERFULLCONTENT = 0x1, 0x2
CREATE_NO_WINDOW = 0x08000000


class WindowsBackend:
    name = "windows"
    max_concurrent = 1            # one desktop, one pointer, one keyboard

    def __init__(self) -> None:
        if sys.platform != "win32":
            raise PlayError("unsupported_platform", "the Windows backend only runs on Windows")
        self.u32 = ctypes.WinDLL("user32", use_last_error=True)
        self.g32 = ctypes.WinDLL("gdi32", use_last_error=True)
        self.k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._declare()
        try:
            self.u32.SetProcessDPIAware()          # rectangles and cursor positions are physical pixels
        except Exception:
            pass
        self.hwnd = 0
        self.method = (os.environ.get("DAEDALUS_WIN_CAPTURE") or "screen").strip().lower()

    # ---- ctypes signatures (so 64-bit handles are never truncated) ----------------------------------------------
    def _declare(self) -> None:
        u, g = self.u32, self.g32
        u.GetForegroundWindow.restype = wt.HWND
        u.SetForegroundWindow.argtypes = [wt.HWND]
        u.BringWindowToTop.argtypes = [wt.HWND]
        u.ShowWindow.argtypes = [wt.HWND, ctypes.c_int]
        u.SetWindowPos.argtypes = [wt.HWND, wt.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wt.UINT]
        u.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
        u.GetWindowThreadProcessId.restype = wt.DWORD
        u.AttachThreadInput.argtypes = [wt.DWORD, wt.DWORD, wt.BOOL]
        u.IsWindowVisible.argtypes = [wt.HWND]
        u.IsWindow.argtypes = [wt.HWND]
        u.GetClientRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
        u.ClientToScreen.argtypes = [wt.HWND, ctypes.POINTER(wt.POINT)]
        u.GetDC.argtypes = [wt.HWND]
        u.GetDC.restype = wt.HDC
        u.ReleaseDC.argtypes = [wt.HWND, wt.HDC]
        u.PrintWindow.argtypes = [wt.HWND, wt.HDC, wt.UINT]
        u.SendInput.argtypes = [wt.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
        u.SendInput.restype = wt.UINT
        u.GetSystemMetrics.argtypes = [ctypes.c_int]
        u.MapVirtualKeyW.argtypes = [wt.UINT, wt.UINT]
        u.MapVirtualKeyW.restype = wt.UINT
        g.CreateCompatibleDC.argtypes = [wt.HDC]
        g.CreateCompatibleDC.restype = wt.HDC
        g.CreateCompatibleBitmap.argtypes = [wt.HDC, ctypes.c_int, ctypes.c_int]
        g.CreateCompatibleBitmap.restype = wt.HBITMAP
        g.SelectObject.argtypes = [wt.HDC, wt.HGDIOBJ]
        g.SelectObject.restype = wt.HGDIOBJ
        g.BitBlt.argtypes = [wt.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wt.HDC,
                             ctypes.c_int, ctypes.c_int, wt.DWORD]
        g.GetDIBits.argtypes = [wt.HDC, wt.HBITMAP, wt.UINT, wt.UINT, ctypes.c_void_p,
                                ctypes.POINTER(BITMAPINFOHEADER), wt.UINT]
        g.DeleteObject.argtypes = [wt.HGDIOBJ]
        g.DeleteDC.argtypes = [wt.HDC]
        k = self.k32
        k.CreateToolhelp32Snapshot.argtypes = [wt.DWORD, wt.DWORD]
        k.CreateToolhelp32Snapshot.restype = wt.HANDLE
        k.Process32FirstW.argtypes = [wt.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
        k.Process32NextW.argtypes = [wt.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
        k.CloseHandle.argtypes = [wt.HANDLE]
        u.GetCursorPos.argtypes = [ctypes.POINTER(wt.POINT)]
        u.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]

    # ---- Backend interface ----------------------------------------------------------------------------------------
    def tools(self) -> dict[str, bool]:
        return {"win32": True}

    def start(self, size: tuple[int, int]) -> dict[str, str]:
        return {}                                   # the game uses the owner's real desktop and GPU

    def stop(self) -> None:
        self.hwnd = 0

    def label(self) -> str:
        return "desktop" if self.hwnd else ""

    def engine_args(self, size: tuple[int, int]) -> tuple[str, ...]:
        w, h = size
        return ("--resolution", f"{w}x{h}", "--position", "40,40")

    def popen_flags(self) -> dict[str, Any]:
        return {"creationflags": CREATE_NO_WINDOW}  # no console window for the console build; the game window shows

    def kill(self, proc: subprocess.Popen | None, grace: float) -> None:
        if proc is None:
            return
        try:
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True, timeout=15,
                           creationflags=CREATE_NO_WINDOW)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        try:
            proc.wait(timeout=max(1.0, grace))
        except Exception:
            pass

    # ---- window ---------------------------------------------------------------------------------------------------
    def _process_pairs(self) -> list[tuple[int, int]]:
        snap = self.k32.CreateToolhelp32Snapshot(0x2, 0)                # TH32CS_SNAPPROCESS
        pairs: list[tuple[int, int]] = []
        if not snap or snap == wt.HANDLE(-1).value:
            return pairs
        try:
            entry = PROCESSENTRY32W()
            entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
            ok = self.k32.Process32FirstW(snap, ctypes.byref(entry))
            while ok:
                pairs.append((int(entry.th32ProcessID), int(entry.th32ParentProcessID)))
                ok = self.k32.Process32NextW(snap, ctypes.byref(entry))
        finally:
            self.k32.CloseHandle(snap)
        return pairs

    def find_window(self, pid: int, timeout: float = 20.0) -> str:
        found: list[int] = []
        enum_proto = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
        owners: set[int] = {pid}

        def cb(hwnd, _lp):
            owner = wt.DWORD(0)
            self.u32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
            if owner.value in owners and self.u32.IsWindowVisible(hwnd):
                rect = wt.RECT()
                self.u32.GetClientRect(hwnd, ctypes.byref(rect))
                if rect.right - rect.left >= 100 and rect.bottom - rect.top >= 100:
                    found.append(int(hwnd))
            return True

        enum = enum_proto(cb)
        deadline = time.time() + timeout
        while time.time() < deadline:
            found.clear()
            owners.clear()
            owners.update(tree_pids(self._process_pairs(), pid))        # the wrapper and the engine it started
            self.u32.EnumWindows(enum, 0)
            if found:
                self.hwnd = found[0]
                self._focus()
                return str(self.hwnd)
            time.sleep(0.2)
        raise PlayError("window_missing", "the game opened no visible window", pid=pid)

    def _client(self) -> tuple[int, int, int, int]:
        """(screen x, screen y, width, height) of the game's client area."""
        if not self.hwnd or not self.u32.IsWindow(self.hwnd):
            raise PlayError("window_missing", "the game window is gone")
        rect = wt.RECT()
        self.u32.GetClientRect(self.hwnd, ctypes.byref(rect))
        origin = wt.POINT(0, 0)
        self.u32.ClientToScreen(self.hwnd, ctypes.byref(origin))
        return origin.x, origin.y, rect.right - rect.left, rect.bottom - rect.top

    def _focus(self) -> None:
        """Bring the game window to the front and prove it is there; never assume."""
        hwnd = self.hwnd
        self.u32.ShowWindow(hwnd, 9)                                   # SW_RESTORE
        self.u32.SetWindowPos(hwnd, wt.HWND(-1), 0, 0, 0, 0, 0x1 | 0x2 | 0x40)   # topmost, no move/size, show
        fg = self.u32.GetForegroundWindow()
        me = self.k32.GetCurrentThreadId()
        fg_thread = self.u32.GetWindowThreadProcessId(fg, None) if fg else 0
        attached = bool(fg_thread and fg_thread != me and self.u32.AttachThreadInput(me, fg_thread, True))
        try:
            self.u32.BringWindowToTop(hwnd)
            self.u32.SetForegroundWindow(hwnd)
        finally:
            if attached:
                self.u32.AttachThreadInput(me, fg_thread, False)
        deadline = time.time() + 1.5
        while time.time() < deadline:
            if self.u32.GetForegroundWindow() == hwnd:
                return
            time.sleep(0.05)
        raise PlayError("focus_lost", "could not bring the game window to the front; input was NOT sent")

    def _require_focus(self) -> None:
        self._client()                                                  # window still exists
        if self.u32.GetForegroundWindow() != self.hwnd:
            self._focus()                                               # one honest attempt, else focus_lost

    # ---- capture ----------------------------------------------------------------------------------------------------
    def capture(self, window: str, out: Path) -> dict[str, Any]:
        from . import capture as cap
        from ...png_codec import encode_rgba

        x, y, w, h = self._client()
        if w < 2 or h < 2:
            raise PlayError("window_missing", f"game window has no area ({w}x{h})")
        method = self.method if self.method in {"screen", "printwindow"} else "screen"
        if method == "screen":
            self._focus() if self.u32.GetForegroundWindow() != self.hwnd else None
        bgra = self._grab(x, y, w, h, method)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(encode_rgba(w, h, bgra_to_rgba(bgra)))
        info = cap.describe_frame(out)
        info["capture_method"] = method
        return info

    def _grab(self, x: int, y: int, w: int, h: int, method: str) -> bytes:
        u, g = self.u32, self.g32
        screen_dc = u.GetDC(None)
        mem_dc = g.CreateCompatibleDC(screen_dc)
        bmp = g.CreateCompatibleBitmap(screen_dc, w, h)
        old = g.SelectObject(mem_dc, bmp)
        try:
            if method == "printwindow":
                ok = u.PrintWindow(self.hwnd, mem_dc, PW_CLIENTONLY | PW_RENDERFULLCONTENT)
            else:
                ok = g.BitBlt(mem_dc, 0, 0, w, h, screen_dc, x, y, SRCCOPY | CAPTUREBLT)
            if not ok:
                raise PlayError("window_missing", f"{method} capture failed (win32 error {ctypes.get_last_error()})")
            info = BITMAPINFOHEADER()
            info.biSize = ctypes.sizeof(BITMAPINFOHEADER)
            info.biWidth, info.biHeight = w, -h                         # negative: top-down rows
            info.biPlanes, info.biBitCount, info.biCompression = 1, 32, 0
            buf = (ctypes.c_ubyte * (w * h * 4))()
            got = g.GetDIBits(mem_dc, bmp, 0, h, buf, ctypes.byref(info), 0)
            if got != h:
                raise PlayError("window_missing", f"GetDIBits returned {got} of {h} rows")
            return bytes(buf)
        finally:
            g.SelectObject(mem_dc, old)
            g.DeleteObject(bmp)
            g.DeleteDC(mem_dc)
            u.ReleaseDC(None, screen_dc)

    # ---- input ------------------------------------------------------------------------------------------------------
    def _send(self, *items: INPUT) -> None:
        arr = (INPUT * len(items))(*items)
        sent = self.u32.SendInput(len(items), arr, ctypes.sizeof(INPUT))
        if sent != len(items):
            raise PlayError("window_missing", f"SendInput delivered {sent} of {len(items)} events "
                                              f"(win32 error {ctypes.get_last_error()})")

    def _key_event(self, vk: int, extended: bool, up: bool) -> INPUT:
        scan = self.u32.MapVirtualKeyW(vk, 0)
        flags = KEYEVENTF_SCANCODE | (KEYEVENTF_EXTENDEDKEY if extended else 0) | (KEYEVENTF_KEYUP if up else 0)
        ev = INPUT(type=INPUT_KEYBOARD)
        ev.ki = KEYBDINPUT(0, scan, flags, 0, 0)
        return ev

    def key(self, name: str, hold_ms: int) -> dict[str, Any]:
        vk, ext = vk_for(name)
        self._require_focus()
        self._send(self._key_event(vk, ext, False))
        time.sleep(max(0, min(int(hold_ms), 5000)) / 1000.0)
        self._require_focus()                                           # never release into another window's state
        self._send(self._key_event(vk, ext, True))
        return {"via": "os", "type": "key", "key": name, "hold_ms": hold_ms, "backend": "win32"}

    def _mouse_event(self, flags: int, nx: int = 0, ny: int = 0) -> INPUT:
        ev = INPUT(type=INPUT_MOUSE)
        ev.mi = MOUSEINPUT(nx, ny, 0, flags, 0, 0)
        return ev

    def _move_to(self, sx: int, sy: int) -> None:
        left = self.u32.GetSystemMetrics(76)                            # SM_XVIRTUALSCREEN
        top = self.u32.GetSystemMetrics(77)
        width = self.u32.GetSystemMetrics(78)
        height = self.u32.GetSystemMetrics(79)
        nx, ny = normalise_absolute(sx, sy, left, top, width, height)
        self._send(self._mouse_event(MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK, nx, ny))
        if self._cursor() != (sx, sy):                                  # scaling or rounding: correct it exactly
            self.u32.SetCursorPos(sx, sy)
            if self._cursor() != (sx, sy):
                raise PlayError("window_missing", f"the pointer is at {self._cursor()}, not at {(sx, sy)}; "
                                                  "input was NOT sent")

    def _cursor(self) -> tuple[int, int]:
        pt = wt.POINT()
        self.u32.GetCursorPos(ctypes.byref(pt))
        return pt.x, pt.y

    def mouse(self, kind: str, x: int, y: int, button: str, hold_ms: int) -> dict[str, Any]:
        if kind not in {"move", "down", "up", "click"}:
            raise PlayError("bad_request", f"unknown mouse kind: {kind!r}")
        flags = _BUTTON_FLAGS.get((button or "left").lower())
        if flags is None:
            raise PlayError("bad_request", f"unknown mouse button: {button!r}")
        self._require_focus()
        cx, cy, w, h = self._client()
        if not (0 <= int(x) < w and 0 <= int(y) < h):
            raise PlayError("bad_request", f"({x},{y}) is outside the game window ({w}x{h}); input was NOT sent")
        sx, sy = cx + int(x), cy + int(y)
        self._move_to(max(cx, sx - 2), max(cy, sy - 2))                 # motion first, as a real pointer arrives
        time.sleep(0.12)
        self._move_to(sx, sy)
        time.sleep(0.12)
        if kind in {"down", "click"}:
            self._require_focus()
            self._send(self._mouse_event(flags[0]))
        if kind == "click":
            time.sleep(max(0, min(int(hold_ms), 5000)) / 1000.0)
        if kind in {"up", "click"}:
            self._send(self._mouse_event(flags[1]))
        return {"via": "os", "type": "mouse", "kind": kind, "x": int(x), "y": int(y), "button": button,
                "backend": "win32"}
