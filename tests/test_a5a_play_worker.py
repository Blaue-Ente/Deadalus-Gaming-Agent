"""A5a: the native play worker. Real engine + display + input where the tools exist; honest SKIP where they do not.

Set DAEDALUS_REQUIRE_PLAY=1 (CI does) to make missing prerequisites a FAILURE instead of a skip, so the rendered
proof can never go green by not running.
"""
from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest
from a0_support import a0_tmp  # noqa: F401
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "tests" / "fixtures" / "godot_play"


def _prereq() -> tuple[bool, str]:
    from daedalus.harness.play_worker import engines
    st = engines.tools_status()
    missing = [k for k, v in st["tools"].items() if not v]
    if not st["engine"].get("ok"):
        missing.append("godot (set GODOT_BIN)")
    return (not missing), ", ".join(missing)


_OK, _WHY = _prereq()
REQUIRE = os.environ.get("DAEDALUS_REQUIRE_PLAY") == "1"
linux_only = pytest.mark.skipif(sys.platform != "linux", reason="uses Linux process groups / Xvfb / pgrep")
needs_play = pytest.mark.skipif(not _OK and not REQUIRE, reason=f"play prerequisites missing: {_WHY}")


def test_prerequisites_are_present_when_required():
    if REQUIRE:
        assert _OK, f"DAEDALUS_REQUIRE_PLAY=1 but missing: {_WHY}"


@pytest.fixture
def play_env(a0_tmp):  # noqa: F811
    projects = a0_tmp / "projects"
    projects.mkdir(parents=True, exist_ok=True)
    shutil.copytree(FIXTURE, projects / "fx")
    return projects


def _fake_running(self):
    """Stand-in for PlaySession.start in manager tests: running, with a game process that reports alive."""
    import types
    self.state = "running"
    self.game = types.SimpleNamespace(proc=types.SimpleNamespace(poll=lambda: None, pid=1))
    return self.status()


def _black_project(root: Path) -> Path:
    p = root / "dark"
    p.mkdir(parents=True)
    (p / "project.godot").write_text(
        'config_version=5\n[application]\nconfig/name="Dark"\nrun/main_scene="res://main.tscn"\n'
        '[rendering]\nrenderer/rendering_method="gl_compatibility"\n', encoding="utf-8")
    (p / "main.tscn").write_text(
        '[gd_scene format=3]\n[node name="Main" type="ColorRect"]\nanchors_preset = 15\nanchor_right = 1.0\n'
        'anchor_bottom = 1.0\ncolor = Color(0, 0, 0, 1)\n', encoding="utf-8")
    return p


def _animated_project(root: Path) -> Path:
    """A scene that changes every frame on its own: input cannot be told apart from the idle drift."""
    p = root / "anim"
    p.mkdir(parents=True)
    (p / "project.godot").write_text(
        'config_version=5\n[application]\nconfig/name="Anim"\nrun/main_scene="res://main.tscn"\n'
        '[rendering]\nrenderer/rendering_method="gl_compatibility"\n', encoding="utf-8")
    (p / "main.tscn").write_text(
        '[gd_scene load_steps=2 format=3]\n[ext_resource type="Script" path="res://main.gd" id="1"]\n'
        '[node name="Main" type="ColorRect"]\nanchors_preset = 15\nanchor_right = 1.0\nanchor_bottom = 1.0\n'
        'script = ExtResource("1")\n[node name="Bar" type="ColorRect" parent="."]\noffset_right = 40.0\n'
        'offset_bottom = 480.0\ncolor = Color(1, 1, 1, 1)\n', encoding="utf-8")
    (p / "main.gd").write_text(
        'extends ColorRect\nvar t := 0.0\nfunc _process(d: float) -> void:\n\tt += d\n'
        '\tcolor = Color(0.5 + 0.5 * sin(t * 6.0), 0.5 + 0.5 * cos(t * 4.0), 0.5 + 0.5 * sin(t * 9.0))\n'
        '\t$Bar.offset_left = fmod(t * 200.0, 600.0)\n', encoding="utf-8")
    return p


def _alive(pid: int | None) -> bool:
    if not pid:
        return False
    if sys.platform == "win32":      # os.kill(pid, 0) would send Ctrl-C there: ask the task list instead
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True).stdout
        return str(pid) in out
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    try:  # a zombie is not alive
        return Path(f"/proc/{pid}/stat").read_text().split()[2] != "Z"
    except OSError:
        return False


def _tree_hash(root: Path) -> dict[str, str]:
    from daedalus.harness.play_worker.capture import sha256_file
    return {str(p.relative_to(root)): sha256_file(p) for p in sorted(root.rglob("*")) if p.is_file()}


# ---------------------------------------------------------------------------------------------- no engine needed
def test_missing_engine_is_blocked_not_passed(play_env, monkeypatch):
    from daedalus.harness.play_worker.errors import PlayError
    from daedalus.harness.play_worker.session import PlaySession

    monkeypatch.setenv("GODOT_BIN", str(play_env / "nope"))
    with pytest.raises(PlayError) as exc:
        PlaySession(play_env / "fx").start()
    assert exc.value.code == "engine_missing"
    assert exc.value.payload()["ok"] is False


def test_not_a_godot_project_is_start_failed(play_env, monkeypatch):
    from daedalus.harness.play_worker import engines
    from daedalus.harness.play_worker.errors import PlayError
    from daedalus.harness.play_worker.session import PlaySession

    if not engines.find_godot().get("ok"):
        monkeypatch.setenv("GODOT_BIN", sys.executable)  # exists and is executable; only project.godot is checked
    (play_env / "plain").mkdir()
    with pytest.raises(PlayError) as exc:
        PlaySession(play_env / "plain").start()
    assert exc.value.code == "start_failed"


def test_session_limit_default_is_one_and_clamped(a0_tmp, monkeypatch):  # noqa: F811
    from daedalus.harness.play_worker.manager import max_sessions

    from daedalus.harness.play_worker.backend import platform_cap

    cap = platform_cap()                                  # Linux 3; Windows 1 (one desktop, pointer, keyboard)
    monkeypatch.delenv("DAEDALUS_PLAY_MAX", raising=False)
    assert max_sessions() == 1
    for raw, want in (("3", min(3, cap)), ("2", min(2, cap)), ("9", min(3, cap)), ("0", 1), ("-4", 1), ("many", 1)):
        monkeypatch.setenv("DAEDALUS_PLAY_MAX", raw)
        assert max_sessions() == want, raw


def test_manager_refuses_past_the_limit_and_unknown_engine(play_env, monkeypatch):
    from daedalus.harness.play_worker import manager as mgr_mod
    from daedalus.harness.play_worker.errors import PlayError
    from daedalus.harness.play_worker.session import PlaySession

    started = []

    def fake_start(self, **_k):
        started.append(self.id)
        return _fake_running(self)

    monkeypatch.setattr(PlaySession, "start", fake_start)
    m = mgr_mod.PlayManager(limit=1)
    m.start("fx", play_env / "fx")
    with pytest.raises(PlayError) as exc:
        m.start("fx", play_env / "fx")
    assert exc.value.code == "session_limit"
    with pytest.raises(PlayError) as exc2:
        m.start("fx", play_env / "fx", engine="unreal")
    assert exc2.value.code == "unsupported_engine"
    # a stopped session frees its slot
    monkeypatch.setattr(PlaySession, "stop", lambda self: setattr(self, "state", "stopped") or {"ok": True})
    m.stop(started[0])
    m.start("fx", play_env / "fx")


def test_idle_reaper_stops_only_idle_sessions(play_env, monkeypatch):
    from daedalus.harness.play_worker import manager as mgr_mod
    from daedalus.harness.play_worker.session import PlaySession

    monkeypatch.setattr(PlaySession, "start", lambda self, **_k: _fake_running(self))
    stopped = []
    monkeypatch.setattr(PlaySession, "stop", lambda self: stopped.append(self.id) or setattr(self, "state", "stopped") or {"ok": True})
    m = mgr_mod.PlayManager(limit=3, idle_timeout=100)
    a = m.start("fx", play_env / "fx")["id"]
    b = m.start("fx", play_env / "fx")["id"]
    m.get(a).last_used = time.time() - 500
    assert m.reap_idle() == [a]
    assert stopped == [a] and m.get(b).state == "running"


def test_frame_math_and_blank_judgement(a0_tmp):  # noqa: F811
    from daedalus.harness.play_worker import capture
    from daedalus.png_codec import encode_rgba

    def png(rgb, name):
        p = a0_tmp / name
        px = bytearray()
        for i in range(64 * 48):
            c = rgb(i)
            px += bytes((*c, 255))
        p.write_bytes(encode_rgba(64, 48, bytes(px)))
        return p

    black = png(lambda i: (0, 0, 0), "black.png")
    flat = png(lambda i: (200, 30, 30), "flat.png")
    busy = png(lambda i: ((i * 7) % 256, (i * 13) % 256, (i * 29) % 256), "busy.png")
    assert capture.describe_frame(black)["blank"] is True
    assert capture.describe_frame(flat)["blank"] is True            # one flat colour is not a rendered scene
    assert capture.describe_frame(busy)["blank"] is False
    assert capture.changed_fraction(busy, busy) == 0.0
    assert capture.changed_fraction(black, busy) > 0.9
    assert len(capture.describe_frame(busy)["sha256"]) == 64


def _palette_png() -> bytes:
    """A valid 8-bit PALETTE png (colour type 3), hand-built so no external tool is needed on any platform."""
    import struct
    import zlib

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", 8, 8, 8, 3, 0, 0, 0)
    rows = b"".join(b"\x00" + b"\x00" * 8 for _ in range(8))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"PLTE", bytes([255, 0, 0]))
            + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))


def test_unreadable_capture_is_an_error_not_a_blank_frame(a0_tmp):  # noqa: F811
    from daedalus.harness.play_worker import capture
    from daedalus.harness.play_worker.errors import PlayError

    pal = a0_tmp / "pal.png"
    pal.write_bytes(_palette_png())
    with pytest.raises(PlayError) as exc:
        capture.describe_frame(pal)
    assert exc.value.code == "frame_unreadable"


def test_max_sessions_unparsable_pin_falls_back_to_the_saved_value(a0_tmp, monkeypatch):  # noqa: F811
    from daedalus import settings_file
    from daedalus.harness.play_worker.manager import max_sessions

    from daedalus.harness.play_worker.backend import platform_cap

    cap = platform_cap()
    settings_file.update({"play_max_sessions": 3})
    monkeypatch.setenv("DAEDALUS_PLAY_MAX", "abc")
    assert max_sessions() == min(3, cap)
    monkeypatch.setenv("DAEDALUS_PLAY_MAX", "2")
    assert max_sessions() == min(2, cap)                          # a valid pin wins over the saved value


def test_manager_forgets_old_finished_sessions_but_keeps_live_ones(play_env, monkeypatch):
    from daedalus.harness.play_worker import manager as mgr_mod
    from daedalus.harness.play_worker.session import PlaySession

    monkeypatch.setattr(PlaySession, "start", lambda self, **_k: _fake_running(self))
    monkeypatch.setattr(PlaySession, "stop", lambda self: setattr(self, "state", "stopped") or {"ok": True})
    monkeypatch.setattr(mgr_mod, "KEEP_FINISHED", 3)
    m = mgr_mod.PlayManager(limit=1)
    ids = []
    for _ in range(8):
        ids.append(m.start("fx", play_env / "fx")["id"])
        m.stop(ids[-1])
    live = m.start("fx", play_env / "fx")["id"]
    kept = {row["id"] for row in m.list()}
    assert live in kept and len(kept) <= 5 and ids[0] not in kept


@linux_only
def test_kill_group_reaches_children_after_the_leader_was_reaped(a0_tmp):
    """GodotSession.close() reaps the leader first; stragglers in its group must still die."""
    from daedalus.harness.play_worker.display import _group_alive, kill_group

    proc = subprocess.Popen(["sh", "-c", "sleep 60 & exit 0"], start_new_session=True,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    proc.wait(timeout=5)                                          # leader gone and reaped, `sleep` still in the group
    assert _group_alive(proc.pid)
    kill_group(proc, grace=1.0)
    assert not _group_alive(proc.pid)


def test_a_temp_copy_that_is_briefly_locked_is_still_removed(tmp_path, monkeypatch):
    """Windows keeps an exiting game's files locked for a moment; the cleanup must retry, not leave the copy behind."""
    from daedalus.harness.play_worker import session as sess

    target = tmp_path / "copy"
    (target / "sub").mkdir(parents=True)
    (target / "sub" / "f.txt").write_text("x")
    real = shutil.rmtree
    calls = {"n": 0}

    def flaky(path, ignore_errors=False, **kw):
        calls["n"] += 1
        if calls["n"] < 3:
            return None                                   # still locked: nothing deleted
        return real(path, ignore_errors=ignore_errors, **kw)

    monkeypatch.setattr(sess.shutil, "rmtree", flaky)
    monkeypatch.setattr(sess.time, "sleep", lambda s: None)
    sess._remove_tree(target)
    assert not target.exists() and calls["n"] == 3


def test_slug_validation_blocks_traversal(play_env):
    from daedalus.harness.play_worker.errors import PlayError
    from daedalus.harness.play_worker.server import project_dir_for

    assert project_dir_for("fx").name == "fx"
    for bad in ("../fx", "a/b", "", "..", ".hidden", "x" * 120):
        with pytest.raises(PlayError):
            project_dir_for(bad)
    with pytest.raises(PlayError) as exc:
        project_dir_for("nothere")
    assert exc.value.code == "not_found"


def test_server_requires_the_token_and_reports_typed_errors(play_env, monkeypatch):
    from daedalus.harness.play_worker.manager import PlayManager
    from daedalus.harness.play_worker.server import create_app

    with pytest.raises(ValueError):
        create_app("short")
    monkeypatch.setenv("GODOT_BIN", str(play_env / "nope"))
    c = TestClient(create_app("t" * 24, PlayManager(limit=1)))
    assert c.get("/health").json() == {"ok": True, "service": "daedalus-play-worker"}
    for headers in ({"Authorization": "Bearer wrong-token-value-xx"}, {"Authorization": ""}):
        assert c.get("/v1/status", headers=headers).status_code == 401
        assert c.post("/v1/sessions", json={"slug": "fx"}, headers=headers).status_code == 401
    ok = {"Authorization": "Bearer " + "t" * 24}
    st = c.get("/v1/status", headers=ok)
    assert st.status_code == 200 and st.json()["limit"] == 1 and "tools" in st.json()
    assert c.get("/v1/sessions/nope", headers=ok).status_code == 404
    res = c.post("/v1/sessions", json={"slug": "fx"}, headers=ok)
    assert res.status_code == 424 and res.json()["error"] == "engine_missing" and res.json()["ok"] is False
    assert c.post("/v1/sessions", json={"slug": "../x"}, headers=ok).status_code == 400
    assert c.post("/v1/sessions", json={"slug": "fx", "engine": "unreal"}, headers=ok).status_code == 400


# ---------------------------------------------------------------------------------------- real engine, real display
@needs_play
def test_rendered_frame_is_a_real_non_blank_window(play_env):
    from daedalus.harness.play_worker.session import PlaySession

    s = PlaySession(play_env / "fx", slug="fx")
    try:
        st = s.start()
        assert st["state"] == "running" and st["level"] == 1 and st["alive"] is True
        info = s.frame("window")
        assert info["blank"] is False and info["unique_colors"] >= 4
        assert info["width"] and info["height"] and len(info["sha256"]) == 64
        assert s.level == 3
        from daedalus.harness.play_worker.capture import sha256_file
        assert sha256_file(s.artifacts / info["file"]) == info["sha256"]
        fps = s.fps()
        assert fps["ok"] and fps["headless"] is False             # truly rendered, not the headless display server
        assert s.tree()["ok"] is True and s.errors()["ok"] is True
    finally:
        s.stop()


@needs_play
def test_real_keyboard_and_mouse_change_the_frame_and_the_game_state(play_env):
    from daedalus.harness.play_worker.session import PlaySession

    s = PlaySession(play_env / "fx", slug="fx")
    try:
        s.start()
        key = s.probe_input(keys=["space"])
        assert key["observed_change"] is True and key["changed_fraction"] > 0.2
        assert key["before"]["sha256"] != key["after"]["sha256"]
        assert s.game_state()["result"]["presses"] == 1
        click = s.probe_input(mouse={"kind": "click", "x": 100, "y": 100})
        assert click["observed_change"] is True
        state = s.game_state()["result"]
        assert state["clicks"] == 1 and "100,100" in state["label"]   # the pointer really was where we said
        assert s.level == 4
        log = [json.loads(x) for x in (s.artifacts / "input-log.jsonl").read_text().splitlines()]
        assert {"input", "frame", "probe"} <= {e["event"] for e in log}
    finally:
        s.stop()


@needs_play
def test_bridge_input_route_reaches_the_game(play_env):
    from daedalus.harness.play_worker.session import PlaySession

    s = PlaySession(play_env / "fx", slug="fx")
    try:
        s.start()
        assert s.probe_input(keys=["space"], via="bridge")["observed_change"] is True
        assert s.probe_input(mouse={"kind": "click", "x": 40, "y": 60}, via="bridge")["observed_change"] is True
        assert "40,60" in s.game_state()["result"]["label"]
        eng = s.frame("engine")
        assert eng["blank"] is False and eng["source"] == "engine"
    finally:
        s.stop()


@needs_play
def test_input_the_game_ignores_is_not_proof(play_env):
    from daedalus.harness.play_worker.session import PlaySession

    s = PlaySession(play_env / "fx", slug="fx")
    try:
        s.start()
        s.frame("window")
        assert s.level == 3
        res = s.probe_input(keys=["q"])                          # the fixture only reacts to space and clicks
        assert res["observed_change"] is False and res["changed_fraction"] < 0.002
        assert s.level == 3                                       # never promoted to L4 on an unobserved effect
    finally:
        s.stop()


@needs_play
def test_a_black_window_is_not_a_rendered_game(play_env):
    from daedalus.harness.play_worker.session import PlaySession

    s = PlaySession(_black_project(play_env), slug="dark")
    try:
        s.start()
        info = s.frame("window")
        assert info["blank"] is True
        assert s.level == 1                                       # started, but no rendered proof
        res = s.probe_input(keys=["space"])
        assert res["observed_change"] is False and s.level == 1
    finally:
        s.stop()


@linux_only
@needs_play
def test_stop_kills_the_whole_tree_and_leaves_the_project_alone(play_env):
    from daedalus.harness.play_worker.session import PlaySession

    before = _tree_hash(play_env / "fx")
    s = PlaySession(play_env / "fx", slug="fx")
    s.start()
    gpid, xpid, work = s.game.proc.pid, s.display.proc.pid, s.workdir
    assert _alive(gpid) and _alive(xpid) and work.is_dir()
    s.frame("window")
    out = s.stop()
    assert out["state"] == "stopped"
    deadline = time.time() + 5
    while time.time() < deadline and (_alive(gpid) or _alive(xpid)):
        time.sleep(0.1)
    assert not _alive(gpid) and not _alive(xpid)
    assert not work.exists()
    assert _tree_hash(play_env / "fx") == before                  # no bridge script, no .godot, nothing injected
    man = json.loads((s.artifacts / "manifest.json").read_text())
    assert man["state"] == "stopped" and any(f["name"].startswith("frame_") for f in man["files"])
    assert all(len(f["sha256"]) == 64 for f in man["files"])
    assert s.stop()["ok"] is True                                 # idempotent
    from daedalus.harness.play_worker.errors import PlayError
    with pytest.raises(PlayError) as exc:
        s.frame("window")
    assert exc.value.code == "session_stopped"


@linux_only
@needs_play
def test_a_crashed_game_is_reported_not_hidden(play_env):
    from daedalus.harness.play_worker.errors import PlayError
    from daedalus.harness.play_worker.session import PlaySession

    s = PlaySession(play_env / "fx", slug="fx")
    try:
        s.start()
        os.killpg(os.getpgid(s.game.proc.pid), signal.SIGKILL)
        s.game.proc.wait(timeout=5)
        with pytest.raises(PlayError) as exc:
            s.frame("window")
        assert exc.value.code == "session_stopped" and s.state == "failed"
    finally:
        s.stop()


@linux_only
@needs_play
def test_two_sessions_are_isolated_from_each_other(play_env):
    from daedalus.harness.play_worker.manager import PlayManager

    m = PlayManager(limit=2)
    a = b = None
    try:
        a = m.get(m.start("fx", play_env / "fx")["id"])
        b = m.get(m.start("fx", play_env / "fx")["id"])
        assert a.display.name != b.display.name and a.game.port != b.game.port and a.workdir != b.workdir
        a.probe_input(keys=["space"])
        assert a.game_state()["result"]["presses"] == 1
        assert b.game_state()["result"]["presses"] == 0            # A's keyboard never reached B
        b.probe_input(mouse={"kind": "click", "x": 50, "y": 50})
        assert a.game_state()["result"]["clicks"] == 0
        assert b.game_state()["result"]["clicks"] == 1
        a.stop()
        assert b.frame("window")["blank"] is False                 # stopping A does not disturb B
    finally:
        m.stop_all()


def _http(method, url, token, body=None):
    req = urllib.request.Request(url, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


@linux_only
@needs_play
def test_worker_process_end_to_end_and_kill_tree_on_sigterm(play_env, monkeypatch):
    token = "w" * 32
    env = {**os.environ, "DAEDALUS_PLAY_TOKEN": token, "PYTHONPATH": str(ROOT)}
    proc = subprocess.Popen([sys.executable, "-m", "daedalus.harness.play_worker", "--port", "0"], env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd=str(ROOT))
    gpid = xpid = None
    try:
        line = proc.stdout.readline()
        assert line.startswith("PLAY_WORKER_READY "), (line, proc.stderr.read() if proc.poll() else "")
        base = f"http://127.0.0.1:{int(line.split()[1])}"
        assert _http("GET", base + "/v1/status", "bad-token-bad-token-bad")[0] == 401
        code, st = _http("GET", base + "/v1/status", token)
        assert code == 200 and st["ready"] is True and st["limit"] == 1
        code, sess = _http("POST", base + "/v1/sessions", token, {"slug": "fx"})
        assert code == 200 and sess["level"] == 1, sess
        sid = sess["id"]
        assert _http("POST", base + "/v1/sessions", token, {"slug": "fx"})[0] == 409     # default limit is one
        code, res = _http("POST", base + f"/v1/sessions/{sid}/input", token, {"keys": ["space"], "probe": True})
        assert code == 200 and res["observed_change"] is True and res["level"] == 4
        assert _http("GET", base + f"/v1/sessions/{sid}/errors", token)[0] == 200
        out = subprocess.run(["pgrep", "-f", f"daedalus-play-{sid}"], capture_output=True, text=True).stdout.split()
        assert out, "the game is running while the session is live"
        gpid = int(out[0])
        xpid = int(subprocess.run(["pgrep", "-f", "Xvfb :9"], capture_output=True, text=True).stdout.split()[0])
    finally:
        proc.send_signal(signal.SIGTERM)                          # worker stops with a live session
        try:
            proc.wait(timeout=20)
        except subprocess.TimeoutExpired:  # pragma: no cover
            proc.kill()
    assert proc.returncode == 0
    deadline = time.time() + 6
    while time.time() < deadline and (_alive(gpid) or _alive(xpid)):
        time.sleep(0.1)
    assert gpid is not None and not _alive(gpid) and not _alive(xpid)


# ----------------------------------------------------------------------------- review fixes (real engine)
@needs_play
def test_a_click_at_the_window_corner_is_not_a_window_error(play_env):
    from daedalus.harness.play_worker.session import PlaySession

    s = PlaySession(play_env / "fx", slug="fx")
    try:
        s.start()
        for x, y in ((0, 0), (1, 1), (0, 5)):                     # used to become `xdotool mousemove -2 -2` -> 502
            assert s.send(mouse={"kind": "click", "x": x, "y": y})["ok"] is True
        assert s.game_state()["result"]["clicks"] == 3
    finally:
        s.stop()


@linux_only
@needs_play
def test_a_crashed_game_frees_its_slot(play_env):
    from daedalus.harness.play_worker.manager import PlayManager

    m = PlayManager(limit=1)
    try:
        first = m.get(m.start("fx", play_env / "fx")["id"])
        gpid = first.game.proc.pid
        os.killpg(os.getpgid(gpid), signal.SIGKILL)
        first.game.proc.wait(timeout=5)
        second = m.get(m.start("fx", play_env / "fx")["id"])       # no session_limit while the old one is dead
        assert second.state == "running" and first.state == "failed"
        assert not _alive(first.display.proc.pid if first.display.proc else None)   # its display was cleaned up
    finally:
        m.stop_all()


@needs_play
def test_idle_animation_is_not_credited_to_the_input(play_env):
    from daedalus.harness.play_worker.session import PlaySession

    s = PlaySession(_animated_project(play_env), slug="anim")
    try:
        s.start()
        res = s.probe_input(keys=["q"])                           # nothing in this game reads input at all
        assert res["idle_fraction"] > 0.25                        # the picture moves by itself
        assert res["conclusive"] is False and res["reason"] == "scene_animates_on_its_own"
        assert res["observed_change"] is False and s.level < 4
    finally:
        s.stop()


@needs_play
def test_fixture_probe_reports_a_quiet_idle_control(play_env):
    from daedalus.harness.play_worker.session import PlaySession

    s = PlaySession(play_env / "fx", slug="fx")
    try:
        s.start()
        res = s.probe_input(keys=["space"])
        assert res["idle_fraction"] < 0.002 and res["conclusive"] is True and res["observed_change"] is True
        assert res["before"]["sha256"] == res["idle"]["sha256"]   # no input -> identical pixels
        assert res["after"]["sha256"] != res["idle"]["sha256"]
    finally:
        s.stop()


@linux_only
@needs_play
def test_a_long_call_on_one_session_does_not_block_another_or_a_stop(play_env):
    import threading

    from daedalus.harness.play_worker.manager import PlayManager

    m = PlayManager(limit=2)
    try:
        a = m.get(m.start("fx", play_env / "fx")["id"])
        b = m.get(m.start("fx", play_env / "fx")["id"])
        t = threading.Thread(target=lambda: _swallow(lambda: a.wait(ms=12000)))
        t.start()
        time.sleep(0.5)
        t0 = time.time()
        assert b.frame("window")["blank"] is False                # B is not queued behind A's 12 s wait
        assert time.time() - t0 < 5
        t0 = time.time()
        gpid = a.game.proc.pid
        a.stop()                                                  # and A can be stopped while it is busy
        assert time.time() - t0 < 10 and not _alive(gpid)
        t.join(timeout=20)
    finally:
        m.stop_all()


def _swallow(fn):
    try:
        fn()
    except Exception:
        pass
