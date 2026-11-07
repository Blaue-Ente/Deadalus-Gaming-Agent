"""A5b: the Windows play backend. Pure and fake-driven tests run everywhere; live tests run only on Windows with Godot.

The live tests open a real window on the machine's desktop and send real keyboard/mouse input, so they are skipped
unless the owner's machine runs them (DAEDALUS_REQUIRE_PLAY=1 turns a missing prerequisite into a failure there).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import types
from pathlib import Path

import pytest
from a0_support import a0_tmp  # noqa: F401

from daedalus.harness.play_worker import win32
from daedalus.harness.play_worker.errors import PlayError

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "tests" / "fixtures" / "godot_play"


# ------------------------------------------------------------------------------------------------ pure helpers
def test_key_names_map_to_virtual_keys_and_unknown_ones_are_refused():
    assert win32.vk_for("space") == (0x20, False)
    assert win32.vk_for("Left") == (0x25, True)          # arrows are extended keys
    assert win32.vk_for("w") == (ord("W"), False)
    assert win32.vk_for("7") == (ord("7"), False)
    assert win32.vk_for("f5") == (0x74, False)
    for bad in ("", "f13", "ctrl+c", "ü", "super", "a b"):
        with pytest.raises(PlayError) as exc:
            win32.vk_for(bad)
        assert exc.value.code == "bad_request"


def test_bgra_becomes_opaque_rgba():
    bgra = bytes([10, 20, 30, 0,  1, 2, 3, 77])          # B,G,R,A pixels
    assert win32.bgra_to_rgba(bgra) == bytes([30, 20, 10, 255,  3, 2, 1, 255])
    assert win32.bgra_to_rgba(b"") == b""
    assert len(win32.bgra_to_rgba(bytes(4 * 640 * 480))) == 4 * 640 * 480


def test_the_window_owner_is_found_through_the_process_tree():
    pairs = [(1, 0), (10, 1), (11, 10), (12, 11), (20, 1), (21, 20), (30, 12)]
    assert win32.tree_pids(pairs, 10) == {10, 11, 12, 30}      # wrapper -> engine -> anything it started
    assert win32.tree_pids(pairs, 20) == {20, 21}
    assert win32.tree_pids(pairs, 99) == {99}                  # unknown pid: just itself, never everything
    assert win32.tree_pids([], 5) == {5}
    assert win32.tree_pids([(7, 7), (8, 7)], 7) == {7, 8}      # a self-parent entry cannot loop forever


def test_absolute_mouse_coordinates_land_on_the_exact_pixel():
    """Windows maps norm back with pixel = norm * size // 65536; every pixel must round-trip, edges included."""
    for left, top, width, height in ((0, 0, 1920, 1080), (-1920, 0, 3840, 1080), (0, 0, 2560, 1440), (0, -1080, 1920, 2160)):
        pts = [(left, top), (left + width - 1, top + height - 1), (left + 1, top + 1), (left + width // 2, top + height // 2),
               (left + 40, top + 40), (left + width - 2, top + 5)]
        for x, y in pts:
            nx, ny = win32.normalise_absolute(x, y, left, top, width, height)
            assert 0 <= nx <= 65535 and 0 <= ny <= 65535
            assert left + nx * width // 65536 == x and top + ny * height // 65536 == y, (x, y, nx, ny)
    assert win32.normalise_absolute(99999, -5, 0, 0, 1920, 1080) == (65535, 0)     # clamped


def test_platform_cap_and_backend_choice(monkeypatch):
    from daedalus.harness.play_worker import backend, manager

    assert backend.platform_cap() == (1 if sys.platform == "win32" else 3)
    monkeypatch.setattr(sys, "platform", "win32")
    assert backend.platform_cap() == 1                   # one desktop, one pointer, one keyboard
    monkeypatch.setenv("DAEDALUS_PLAY_MAX", "3")
    assert manager.max_sessions() == 1                   # the owner's setting cannot exceed what the platform can do
    monkeypatch.setattr(sys, "platform", "darwin")
    with pytest.raises(PlayError) as exc:
        backend.get_backend()
    assert exc.value.code == "unsupported_platform"


def test_windows_engine_discovery_prefers_the_console_build(tmp_path):
    from daedalus.harness.play_worker import engines

    base = tmp_path / "Godot"
    (base / "4.3").mkdir(parents=True)
    (base / "4.3" / "Godot_v4.3-stable_win64.exe").write_bytes(b"x")
    (base / "4.3" / "Godot_v4.3-stable_win64_console.exe").write_bytes(b"x")
    assert engines._windows_binary(base).name == "Godot_v4.3-stable_win64_console.exe"
    assert engines._windows_binary(tmp_path / "nothing") is None
    (base / "4.3" / "Godot_v4.3-stable_win64_console.exe").unlink()
    assert engines._windows_binary(base).name == "Godot_v4.3-stable_win64.exe"


# ------------------------------------------------------------------------------ safety rules, driven with fakes
def _fake_backend(foreground: int, hwnd: int = 7, client=(100, 100, 640, 480), can_focus: bool = True):
    be = win32.WindowsBackend.__new__(win32.WindowsBackend)        # skips the "only on Windows" constructor guard
    be.hwnd = hwnd
    be.sent = []
    be.u32 = types.SimpleNamespace(GetForegroundWindow=lambda: foreground, MapVirtualKeyW=lambda vk, kind: 0x39)
    be._client = lambda: client

    def focus():
        if not can_focus:
            raise PlayError("focus_lost", "could not bring the game window to the front; input was NOT sent")
        be.u32.GetForegroundWindow = lambda: hwnd

    be._focus = focus
    be._send = lambda *items: be.sent.append(items)
    be._move_to = lambda x, y: be.sent.append(("move", x, y))
    return be


def test_no_key_is_sent_when_the_game_window_cannot_take_focus():
    be = _fake_backend(foreground=999, can_focus=False)
    with pytest.raises(PlayError) as exc:
        be.key("space", 10)
    assert exc.value.code == "focus_lost" and be.sent == []        # nothing reached whatever else was in front


def test_a_click_outside_the_game_window_is_refused_before_any_movement():
    be = _fake_backend(foreground=7)
    for x, y in ((640, 10), (-1, 10), (10, 480), (9999, 9999)):
        with pytest.raises(PlayError) as exc:
            be.mouse("click", x, y, "left", 10)
        assert exc.value.code == "bad_request" and "NOT sent" in str(exc.value)
    assert be.sent == []


def test_a_click_inside_moves_first_then_presses_and_releases(monkeypatch):
    monkeypatch.setattr(win32.time, "sleep", lambda s: None)
    be = _fake_backend(foreground=7)
    info = be.mouse("click", 100, 50, "left", 10)
    kinds = [item[0] if isinstance(item[0], str) else "button" for item in be.sent]
    assert kinds == ["move", "move", "button", "button"]            # motion first, then down, then up
    assert be.sent[1] == ("move", 200, 150)                         # client origin (100,100) + (100,50)
    assert info["backend"] == "win32" and (info["x"], info["y"]) == (100, 50)
    with pytest.raises(PlayError):
        be.mouse("click", 5, 5, "thumb", 10)                        # unknown button


def test_focus_is_retaken_once_when_another_window_stole_it(monkeypatch):
    monkeypatch.setattr(win32.time, "sleep", lambda s: None)
    be = _fake_backend(foreground=999)                              # someone else is in front; focus() can fix it
    be.key("space", 0)
    assert len(be.sent) == 2 and be.u32.GetForegroundWindow() == 7  # key down, key up, both while the game was in front


@pytest.mark.skipif(sys.platform != "win32", reason="struct layout is only meaningful on Windows")
def test_input_structure_matches_the_win32_abi():
    import ctypes
    assert ctypes.sizeof(win32.INPUT) == (40 if ctypes.sizeof(ctypes.c_void_p) == 8 else 28)


# ------------------------------------------------------------------------------------------ live (Windows only)
def _prereq() -> tuple[bool, str]:
    if sys.platform != "win32":
        return False, "not Windows"
    from daedalus.harness.play_worker import engines
    st = engines.tools_status()
    return st["ready"], "" if st["ready"] else str(st["engine"].get("message") or st["tools"])


_OK, _WHY = _prereq()
REQUIRE = os.environ.get("DAEDALUS_REQUIRE_PLAY") == "1" and sys.platform == "win32"
live = pytest.mark.skipif(not _OK and not REQUIRE, reason=f"Windows live prerequisites missing: {_WHY}")


@pytest.fixture
def game_project(a0_tmp):  # noqa: F811
    projects = a0_tmp / "projects"
    projects.mkdir(parents=True, exist_ok=True)
    shutil.copytree(FIXTURE, projects / "fx")
    return projects / "fx"


@live
def test_windows_frame_is_a_real_window_drawn_by_the_real_gpu(game_project):
    from daedalus.harness.play_worker.session import PlaySession

    s = PlaySession(game_project, slug="fx")
    try:
        st = s.start()
        assert st["backend"] == "windows" and st["level"] == 1
        info = s.frame("window")
        assert info["blank"] is False and info["unique_colors"] >= 4
        assert info["capture_method"] in {"screen", "printwindow"} and s.level == 3
        assert s.fps()["headless"] is False
    finally:
        s.stop()
    log = (s.artifacts / "engine.log").read_text(encoding="utf-8", errors="replace")
    assert "llvmpipe" not in log.lower()                          # this is not the software renderer of the Linux CI
    print("GPU line:", [ln for ln in log.splitlines() if "OpenGL API" in ln or "Vulkan API" in ln][:1])


@live
def test_windows_stop_closes_the_game_and_removes_its_copy(game_project):
    from daedalus.harness.play_worker.capture import sha256_file
    from daedalus.harness.play_worker.session import PlaySession

    before = {str(p.relative_to(game_project)): sha256_file(p) for p in sorted(game_project.rglob("*")) if p.is_file()}
    s = PlaySession(game_project, slug="fx")
    s.start()
    pid, work = s.game.proc.pid, s.workdir
    assert subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True).stdout.count(str(pid))
    s.frame("window")
    assert s.stop()["state"] == "stopped"
    out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True).stdout
    assert str(pid) not in out and not work.exists()
    after = {str(p.relative_to(game_project)): sha256_file(p) for p in sorted(game_project.rglob("*")) if p.is_file()}
    assert after == before                                          # the owner's project was never touched
    man = json.loads((s.artifacts / "manifest.json").read_text())
    assert man["state"] == "stopped" and all(len(f["sha256"]) == 64 for f in man["files"])
