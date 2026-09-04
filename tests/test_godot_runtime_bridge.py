"""Godot runtime bridge — protocol, sidecars, signal DoD, native engine tool."""
from __future__ import annotations

import json
import socket
import threading
from pathlib import Path

from daedalus.engine_session import godot_action, map_mcp_tool
from daedalus.godot_bridge import (
    BRIDGE_GDSCRIPT,
    BRIDGE_REL,
    GodotSession,
    ensure_autoload_line,
    ensure_bridge,
    ensure_import_sidecars,
    live_dod,
    movement_probe,
    player_path_from_tree,
)
from daedalus.godot_dod import dod_signals_static, dod_v2_static
from daedalus.playground_godot_scaffold import write_godot_scaffold
from daedalus.subagents import tools_for_role


MIN_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000a49444154789c63000100000500010d0a2db40000000049454e44ae426082"
)


def _fake_godot(port: int, stop: threading.Event) -> None:
    """Minimal JSON-lines Godot: Player moves when ui_right is pressed."""
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", port))
    srv.listen(1)
    srv.settimeout(0.3)
    x = 0.0
    pressed = False
    conn: socket.socket | None = None
    buf = b""
    try:
        while not stop.is_set():
            if conn is None:
                try:
                    conn, _addr = srv.accept()
                    conn.settimeout(0.3)
                    buf = b""
                except socket.timeout:
                    continue
            try:
                chunk = conn.recv(65536)
            except socket.timeout:
                continue
            except OSError:
                conn = None
                continue
            if not chunk:
                conn.close()
                conn = None
                continue
            buf += chunk
            while b"\n" in buf:
                raw, buf = buf.split(b"\n", 1)
                if not raw.strip():
                    continue
                req = json.loads(raw.decode("utf-8"))
                op = req.get("op")
                reply: dict = {"id": req.get("id"), "ok": True, "op": op}
                if op == "ping":
                    reply["pong"] = True
                    reply["fps"] = 60
                    reply["game"] = {"score": 0, "failed": False}
                elif op == "scene_tree":
                    reply["tree"] = [
                        {"name": "root", "type": "Window", "path": "/root"},
                        {"name": "Main", "type": "Node2D", "path": "/root/Main"},
                        {"name": "Player", "type": "CharacterBody2D", "path": "/root/Main/Player"},
                    ]
                elif op == "find_node":
                    reply["path"] = "/root/Main/Player"
                elif op == "inspect_node":
                    if pressed:
                        x += 2.4
                    reply["node"] = {
                        "name": "Player",
                        "type": "CharacterBody2D",
                        "path": req.get("path"),
                        "position": {"x": x, "y": 420.0},
                    }
                elif op == "send_input":
                    pressed = bool(req.get("pressed"))
                    reply["action"] = req.get("action")
                    reply["pressed"] = pressed
                elif op == "wait_frames":
                    if pressed:
                        x += float(req.get("n") or 1) * 0.4
                    reply["waited"] = req.get("n")
                    reply["fps"] = 60
                elif op == "errors":
                    reply["errors"] = []
                    reply["game"] = {"score": 0, "failed": False}
                elif op == "fps_sample":
                    reply["fps"] = 60
                    reply["headless"] = True
                elif op == "quit":
                    conn.sendall((json.dumps(reply) + "\n").encode("utf-8"))
                    return
                else:
                    reply["ok"] = False
                    reply["error"] = "unknown_op"
                conn.sendall((json.dumps(reply) + "\n").encode("utf-8"))
    finally:
        if conn is not None:
            try:
                conn.close()
            except OSError:
                pass
        srv.close()


def test_bridge_script_is_godot4_tcp():
    assert "TCPServer" in BRIDGE_GDSCRIPT
    assert "--daedalus-bridge" in BRIDGE_GDSCRIPT
    assert "send_input" in BRIDGE_GDSCRIPT
    assert "CharacterBody2D" not in BRIDGE_GDSCRIPT or "extends Node" in BRIDGE_GDSCRIPT
    assert "KinematicBody" not in BRIDGE_GDSCRIPT


def test_ensure_bridge_and_sidecars(tmp_path: Path):
    (tmp_path / "project.godot").write_text(
        '[application]\nconfig/name="X"\nrun/main_scene="res://scenes/main.tscn"\n',
        encoding="utf-8",
    )
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "hero.png").write_bytes(MIN_PNG)
    (assets / "crate.glb").write_bytes(b"glTF")
    info = ensure_bridge(tmp_path)
    assert info["ok"]
    assert (tmp_path / BRIDGE_REL).is_file()
    proj = (tmp_path / "project.godot").read_text(encoding="utf-8")
    assert "DaedalusBridge=" in proj
    written = ensure_import_sidecars(tmp_path)
    assert any(p.endswith("hero.png.import") for p in written)
    assert any(p.endswith("crate.glb.import") for p in written)
    png_imp = (assets / "hero.png.import").read_text(encoding="utf-8")
    assert 'source_file="res://assets/hero.png"' in png_imp
    assert "importer=\"texture\"" in png_imp
    glb_imp = (assets / "crate.glb.import").read_text(encoding="utf-8")
    assert 'importer="scene"' in glb_imp


def test_autoload_not_duplicated():
    base = '[autoload]\nGame="*res://scripts/autoload/game.gd"\n'
    once = ensure_autoload_line(base, "DaedalusBridge", "res://scripts/autoload/daedalus_bridge.gd")
    twice = ensure_autoload_line(once, "DaedalusBridge", "res://scripts/autoload/daedalus_bridge.gd")
    assert once.count("DaedalusBridge=") == 1
    assert twice == once


def test_scaffold_installs_bridge(tmp_path: Path):
    written = write_godot_scaffold(tmp_path, "Veins", "veins", "2D RPG")
    assert BRIDGE_REL in written
    proj = (tmp_path / "project.godot").read_text(encoding="utf-8")
    assert "DaedalusBridge=" in proj
    assert (tmp_path / BRIDGE_REL).is_file()
    assert "TCPServer" in (tmp_path / BRIDGE_REL).read_text(encoding="utf-8")


def test_broken_tscn_connection_fails_static_dod(tmp_path: Path):
    write_godot_scaffold(tmp_path, "Veins", "veins", "2D RPG")
    tscn = tmp_path / "scenes" / "main.tscn"
    body = tscn.read_text(encoding="utf-8")
    body += (
        '\n[connection signal="body_entered" from="Pit" to="Player" '
        'method="_on_missing_handler"]\n'
    )
    tscn.write_text(body, encoding="utf-8")
    sig = dod_signals_static(tmp_path)
    assert sig["ok"] is False
    assert any("_on_missing_handler" in x for x in sig["issues"])
    dod = dod_v2_static(tmp_path)
    assert dod["ok"] is False
    assert "signals" in dod["blocking"]


def test_valid_tscn_connection_passes(tmp_path: Path):
    write_godot_scaffold(tmp_path, "Veins", "veins", "2D RPG")
    tscn = tmp_path / "scenes" / "main.tscn"
    body = tscn.read_text(encoding="utf-8")
    body += (
        '\n[connection signal="body_entered" from="Pit" to="Player" '
        'method="_on_pit"]\n'
    )
    tscn.write_text(body, encoding="utf-8")
    sig = dod_signals_static(tmp_path)
    assert sig["ok"] is True
    assert sig["connections"] == 1
    assert dod_v2_static(tmp_path)["ok"] is True


def test_session_protocol_and_movement_against_fake_godot():
    import time

    from daedalus.godot_bridge import free_port

    port = free_port()
    stop = threading.Event()
    thread = threading.Thread(target=_fake_godot, args=(port, stop), daemon=True)
    thread.start()
    sess = GodotSession(Path("."), port=port)
    last_err = None
    try:
        for _ in range(30):
            try:
                sess.attach()
                last_err = None
                break
            except OSError as exc:
                last_err = exc
                time.sleep(0.05)
        assert last_err is None, last_err
        ping = sess.request("ping")
        assert ping.get("pong") is True
        tree = sess.request("scene_tree")
        path = player_path_from_tree(tree.get("tree"))
        assert path.endswith("Player")
        move = movement_probe(sess, frames=10)
        assert move["moved"] is True
        assert move["delta"] >= 0.01
        live = live_dod(sess)
        assert live["ok"] is True
        assert "player_moved" not in live["blocking"]
    finally:
        try:
            sess.request("quit")
        except Exception:
            pass
        stop.set()
        if sess._sock:
            try:
                sess._sock.close()
            except OSError:
                pass
        thread.join(timeout=2)


def test_engine_tool_and_verify_role():
    from daedalus.native_tools import NATIVE_TOOL_SPECS

    spec = next(t for t in NATIVE_TOOL_SPECS if t["name"] == "engine")
    assert "run" in spec["description"]
    props = spec["input_schema"]["properties"]
    assert "action" in props and "slug" in props
    assert "engine" in tools_for_role("verify")
    assert "engine" in tools_for_role("explorer")
    assert "engine" in tools_for_role("coder")
    assert "write_file" not in tools_for_role("verify")


def test_mcp_tool_mapping():
    names = ["get_runtime_errors", "scene_tree_dump", "send_input_action"]
    assert map_mcp_tool("errors", names) == "get_runtime_errors"
    assert map_mcp_tool("scene_tree", names) == "scene_tree_dump"
    assert map_mcp_tool("input", names) == "send_input_action"
    assert map_mcp_tool("screenshot", names) == ""


def test_godot_api_skill_selected():
    from daedalus import improvements

    improvements._SKILLS_LOADED = False
    improvements._SKILLS_CACHE.clear()
    names = improvements.select_skills(
        "godot 4.3 CharacterBody2D and .import sidecars",
        "build",
    )
    assert "godot_api" in names or "godot" in names


def test_godot_action_missing_project(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("DAEDALUS_PLAYGROUND", str(tmp_path))
    from daedalus.paths import playground_projects_dir

    playground_projects_dir.cache_clear()
    (tmp_path / "not-godot").mkdir()
    (tmp_path / "not-godot" / "index.html").write_text("hi", encoding="utf-8")
    out = godot_action("run", "not-godot")
    assert out.get("ok") is False
    playground_projects_dir.cache_clear()
