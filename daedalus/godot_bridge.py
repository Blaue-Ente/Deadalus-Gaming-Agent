"""Headless Godot runtime bridge — JSON-lines over TCP on 127.0.0.1.

The agent talks to a running Godot process, not only to files. Autoload
``DaedalusBridge`` listens when the project is launched with
``--daedalus-bridge <port>``. Missing Godot is not a pass (see engine_runners).
"""
from __future__ import annotations

import json
import socket
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

DEFAULT_FPS_BUDGET = 20.0
BRIDGE_REL = "scripts/autoload/daedalus_bridge.gd"
BRIDGE_AUTOLOAD = "DaedalusBridge"
CONNECT_PAUSE = 0.25

# Godot 4.3 autoload. Typed. No Editor. Stays up until op=quit.
BRIDGE_GDSCRIPT = r'''extends Node
## Daedalus headless bridge — JSON lines on 127.0.0.1. Do not edit by hand.

const DEFAULT_PORT := 18765
const MAX_NODES := 240

var _server: TCPServer
var _peer: StreamPeerTCP
var _buf: String = ""
var _busy: bool = false
var _port: int = DEFAULT_PORT
var _armed: bool = false
var _errors: PackedStringArray = []
var _notes: PackedStringArray = []

func _ready() -> void:
	# Keep answering while the game pauses its tree (start menus and pause screens do: live test 2026-11-13).
	process_mode = Node.PROCESS_MODE_ALWAYS
	var args := OS.get_cmdline_user_args()
	for i in args.size():
		if args[i] == "--daedalus-bridge":
			_armed = true
			if i + 1 < args.size():
				_port = int(args[i + 1])
	if not _armed:
		set_process(false)
		return
	_server = TCPServer.new()
	var err := _server.listen(_port, "127.0.0.1")
	if err != OK:
		push_error("DaedalusBridge listen failed on %d: %s" % [_port, err])
		_note("listen_failed:%s" % err)
		return
	_note("listening:%d" % _port)

func _process(_dt: float) -> void:
	if not _armed or _server == null:
		return
	if _peer == null and _server.is_connection_available():
		_peer = _server.take_connection()
		_buf = ""
	if _peer == null:
		return
	_peer.poll()
	var st := _peer.get_status()
	if st != StreamPeerTCP.STATUS_CONNECTED:
		if st == StreamPeerTCP.STATUS_NONE or st == StreamPeerTCP.STATUS_ERROR:
			_peer = null
			_buf = ""
		return
	var n := _peer.get_available_bytes()
	if n > 0:
		_buf += _peer.get_utf8_string(n)
	if _busy:
		return
	_drain_one()

func _drain_one() -> void:
	var nl := _buf.find("\n")
	if nl < 0:
		return
	var line := _buf.substr(0, nl).strip_edges()
	_buf = _buf.substr(nl + 1)
	if line.is_empty():
		return
	_handle_coro(line)

func _handle_coro(line: String) -> void:
	_busy = true
	var payload: Variant = JSON.parse_string(line)
	var result: Dictionary
	if typeof(payload) != TYPE_DICTIONARY:
		result = {"ok": false, "error": "not_json_object"}
	else:
		result = await _dispatch(payload)
	_reply(result)
	_busy = false

func _dispatch(data: Dictionary) -> Dictionary:
	var op := str(data.get("op", ""))
	var result := {"id": data.get("id", 0), "ok": true, "op": op}
	match op:
		"ping":
			result["pong"] = true
			result["fps"] = Engine.get_frames_per_second()
			result["game"] = _game_state()
		"scene_tree":
			result["tree"] = _dump_tree(get_tree().root, "/root", 0)
		"find_node":
			var want := str(data.get("name", data.get("path", "")))
			result["path"] = _find_named(get_tree().root, want, "/root")
		"inspect_node":
			result.merge(_inspect(str(data.get("path", ""))), true)
		"set_prop":
			result.merge(_set_prop(str(data.get("path", "")), str(data.get("prop", "")), data.get("value")), true)
		"call_method":
			result.merge(_call_method(str(data.get("path", "")), str(data.get("method", "")), data.get("args")), true)
		"send_input":
			result.merge(_send_action(str(data.get("action", "")), bool(data.get("pressed", true))), true)
		"send_key":
			result.merge(_send_key(str(data.get("key", "")), bool(data.get("pressed", true))), true)
		"send_mouse":
			result.merge(_send_mouse(float(data.get("x", 0)), float(data.get("y", 0)), str(data.get("button", "left")), str(data.get("kind", "click"))), true)
		"wait_frames":
			var frames := clampi(int(data.get("n", 1)), 1, 180)
			for _i in frames:
				await get_tree().physics_frame
			result["waited"] = frames
			result["fps"] = Engine.get_frames_per_second()
		"screenshot":
			result.merge(await _screenshot(), true)
		"errors":
			result["errors"] = Array(_errors)
			result["notes"] = Array(_notes)
			result["game"] = _game_state()
		"fps_sample":
			result["fps"] = Engine.get_frames_per_second()
			result["headless"] = DisplayServer.get_name() == "headless"
		"quit":
			get_tree().quit()
		_:
			result["ok"] = false
			result["error"] = "unknown_op"
	return result

func _reply(payload: Dictionary) -> void:
	if _peer == null:
		return
	var line := JSON.stringify(payload) + "\n"
	_peer.put_data(line.to_utf8_buffer())

func _note(msg: String) -> void:
	_notes.append(msg)
	if _notes.size() > 40:
		_notes.remove_at(0)

func _game_state() -> Dictionary:
	var g := get_node_or_null("/root/Game")
	if g == null:
		return {}
	var st := {"score": g.get("score"), "failed": bool(g.get("is_failed"))}
	var won: Variant = g.get("is_won")
	if won != null:
		st["won"] = bool(won)
	return st

func _dump_tree(node: Node, path: String, count: int) -> Array:
	var out: Array = []
	if node == null or count >= MAX_NODES:
		return out
	out.append({
		"name": node.name,
		"type": node.get_class(),
		"path": path,
	})
	count += 1
	for child in node.get_children():
		if count >= MAX_NODES:
			break
		var kid: Array = _dump_tree(child, path + "/" + str(child.name), count)
		count += kid.size()
		out.append_array(kid)
	return out

func _find_named(node: Node, want: String, path: String) -> String:
	if node == null or want.is_empty():
		return ""
	if str(node.name) == want:
		return path
	for child in node.get_children():
		var hit := _find_named(child, want, path + "/" + str(child.name))
		if hit != "":
			return hit
	return ""

func _node_at(path: String) -> Node:
	if path.is_empty():
		return null
	return get_node_or_null(path)

func _inspect(path: String) -> Dictionary:
	var node := _node_at(path)
	if node == null:
		return {"ok": false, "error": "node_not_found", "path": path}
	var props := {
		"name": str(node.name),
		"type": node.get_class(),
		"path": path,
		"visible": node.get("visible") if node.get("visible") != null else true,
	}
	var pos: Variant = node.get("global_position")
	if pos == null:
		pos = node.get("position")
	props["position"] = _json_val(pos)
	var vel: Variant = node.get("velocity")
	if vel != null:
		props["velocity"] = _json_val(vel)
	return {"ok": true, "node": props}

func _set_prop(path: String, prop: String, value: Variant) -> Dictionary:
	var node := _node_at(path)
	if node == null:
		return {"ok": false, "error": "node_not_found"}
	if prop.is_empty():
		return {"ok": false, "error": "missing_prop"}
	var cur: Variant = node.get(prop)
	node.set(prop, _coerce(cur, value))
	return {"ok": true, "prop": prop, "value": _json_val(node.get(prop))}

func _call_method(path: String, method: String, args: Variant) -> Dictionary:
	var node := _node_at(path)
	if node == null:
		return {"ok": false, "error": "node_not_found"}
	if method.is_empty() or not node.has_method(method):
		return {"ok": false, "error": "missing_method"}
	var list: Array = args if typeof(args) == TYPE_ARRAY else []
	var ret: Variant = node.callv(method, list)
	return {"ok": true, "result": _json_val(ret)}

func _send_action(action: String, pressed: bool) -> Dictionary:
	if action.is_empty():
		return {"ok": false, "error": "missing_action"}
	var ev := InputEventAction.new()
	ev.action = action
	ev.pressed = pressed
	ev.strength = 1.0
	Input.parse_input_event(ev)
	if pressed:
		Input.action_press(action)
	else:
		Input.action_release(action)
	return {"ok": true, "action": action, "pressed": pressed}

func _send_key(key: String, pressed: bool) -> Dictionary:
	var code := _key_code(key)
	if code == KEY_NONE:
		return {"ok": false, "error": "unknown_key"}
	var ev := InputEventKey.new()
	ev.pressed = pressed
	ev.keycode = code
	ev.physical_keycode = code
	Input.parse_input_event(ev)
	return {"ok": true, "key": key, "pressed": pressed}

func _mouse_button(name: String) -> int:
	match name.strip_edges().to_lower():
		"right": return MOUSE_BUTTON_RIGHT
		"middle": return MOUSE_BUTTON_MIDDLE
		_: return MOUSE_BUTTON_LEFT

func _send_mouse(x: float, y: float, button: String, kind: String) -> Dictionary:
	var pos := Vector2(x, y)
	var idx := _mouse_button(button)
	if kind == "move":
		var mv := InputEventMouseMotion.new()
		mv.position = pos
		mv.global_position = pos
		Input.parse_input_event(mv)
		return {"ok": true, "kind": "move", "x": x, "y": y}
	if kind != "click" and kind != "down" and kind != "up":
		return {"ok": false, "error": "unknown_mouse_kind"}
	var steps: Array = []
	if kind == "click" or kind == "down":
		steps.append(true)
	if kind == "click" or kind == "up":
		steps.append(false)
	for pressed in steps:
		var ev := InputEventMouseButton.new()
		ev.position = pos
		ev.global_position = pos
		ev.button_index = idx
		ev.pressed = pressed
		Input.parse_input_event(ev)
	return {"ok": true, "kind": kind, "x": x, "y": y, "button": button}

func _key_code(key: String) -> int:
	match key.strip_edges().to_upper():
		"W": return KEY_W
		"A": return KEY_A
		"S": return KEY_S
		"D": return KEY_D
		"SPACE", "UI_ACCEPT": return KEY_SPACE
		"LEFT", "UI_LEFT": return KEY_LEFT
		"RIGHT", "UI_RIGHT": return KEY_RIGHT
		"UP", "UI_UP": return KEY_UP
		"DOWN", "UI_DOWN": return KEY_DOWN
		_: return KEY_NONE

func _screenshot() -> Dictionary:
	await RenderingServer.frame_post_draw
	var vp := get_viewport()
	if vp == null:
		return {"ok": false, "error": "no_viewport"}
	var tex := vp.get_texture()
	if tex == null:
		return {"ok": false, "error": "no_texture"}
	var img := tex.get_image()
	if img == null or img.get_width() < 2:
		return {"ok": false, "error": "empty_headless_image"}
	DirAccess.make_dir_recursive_absolute(ProjectSettings.globalize_path("res://studio"))
	var rel := "res://studio/bridge_shot.png"
	var err := img.save_png(rel)
	if err != OK:
		return {"ok": false, "error": "save_failed:%s" % err}
	return {"ok": true, "path": "studio/bridge_shot.png", "w": img.get_width(), "h": img.get_height()}

func _coerce(cur: Variant, value: Variant) -> Variant:
	if typeof(cur) == TYPE_VECTOR2 and typeof(value) == TYPE_DICTIONARY:
		return Vector2(float(value.get("x", 0)), float(value.get("y", 0)))
	if typeof(cur) == TYPE_VECTOR3 and typeof(value) == TYPE_DICTIONARY:
		return Vector3(float(value.get("x", 0)), float(value.get("y", 0)), float(value.get("z", 0)))
	return value

func _json_val(v: Variant) -> Variant:
	match typeof(v):
		TYPE_VECTOR2:
			return {"x": v.x, "y": v.y}
		TYPE_VECTOR3:
			return {"x": v.x, "y": v.y, "z": v.z}
		TYPE_BOOL, TYPE_INT, TYPE_FLOAT, TYPE_STRING, TYPE_NIL:
			return v
		TYPE_DICTIONARY, TYPE_ARRAY:
			return v
		_:
			return str(v)
'''

_IMPORT_TEXTURE = """\
[remap]

importer="texture"
type="CompressedTexture2D"
uid="uid://d{uid}"

[deps]

source_file="res://{rel}"

[params]

compress/mode=0
mipmaps/generate=false
process/fix_alpha_border=true
"""

_IMPORT_SCENE = """\
[remap]

importer="scene"
type="PackedScene"
uid="uid://d{uid}"

[deps]

source_file="res://{rel}"

[params]

nodes/root_type=""
"""

_IMPORT_AUDIO_GENERIC = """\
[remap]

importer="oggvorbisstr"
type="AudioStreamOggVorbis"
uid="uid://d{uid}"

[deps]

source_file="res://{rel}"
"""


def _uid(rel: str) -> str:
    import hashlib
    return hashlib.sha1(rel.encode("utf-8")).hexdigest()[:12]


def ensure_bridge(pdir: Path) -> dict[str, Any]:
    """Write the autoload and register it in project.godot. Safe to call often."""
    root = Path(pdir)
    script = root / BRIDGE_REL
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text(BRIDGE_GDSCRIPT, encoding="utf-8")
    proj = root / "project.godot"
    patched = False
    if proj.is_file():
        text = proj.read_text(encoding="utf-8")
        new = ensure_autoload_line(text, BRIDGE_AUTOLOAD, f"res://{BRIDGE_REL}")
        if new != text:
            proj.write_text(new, encoding="utf-8")
            patched = True
    return {"ok": True, "script": BRIDGE_REL, "patched_project": patched}


def ensure_autoload_line(project_godot: str, name: str, res_path: str) -> str:
    line = f'{name}="*{res_path}"'
    if f"{name}=" in project_godot:
        return project_godot
    if "[autoload]" in project_godot:
        return project_godot.replace("[autoload]", f"[autoload]\n{line}", 1)
    return project_godot.rstrip() + f"\n\n[autoload]\n{line}\n"


def ensure_import_sidecars(pdir: Path) -> list[str]:
    """Write missing .import remaps so a dropped PNG/GLB is not invisible."""
    root = Path(pdir)
    written: list[str] = []
    skip_dirs = {".godot", ".git"}
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        parts = set(path.relative_to(root).parts)
        if parts & skip_dirs:
            continue
        rel = path.relative_to(root).as_posix()
        ext = path.suffix.lower()
        sidecar = path.with_name(path.name + ".import")
        if sidecar.exists():
            continue
        uid = _uid(rel)
        if ext in {".png", ".svg", ".jpg", ".jpeg", ".webp"}:
            body = _IMPORT_TEXTURE.format(uid=uid, rel=rel)
        elif ext in {".glb", ".gltf"}:
            body = _IMPORT_SCENE.format(uid=uid, rel=rel)
        elif ext in {".ogg", ".mp3"}:
            body = _IMPORT_AUDIO_GENERIC.format(uid=uid, rel=rel)
        elif ext == ".wav":
            body = (
                _IMPORT_AUDIO_GENERIC
                .replace('importer="oggvorbisstr"', 'importer="wav"')
                .replace("AudioStreamOggVorbis", "AudioStreamWAV")
                .format(uid=uid, rel=rel)
            )
        else:
            continue
        sidecar.write_text(body, encoding="utf-8")
        written.append(rel + ".import")
    return written


def run_import(pdir: Path, binary: str, *, timeout: int = 90) -> dict[str, Any]:
    """``godot --headless --import``. Fail-open on unknown flags; fail-closed on script errors."""
    cmd = [binary, "--headless", "--path", str(pdir), "--import"]
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(pdir),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "import timeout", "command": " ".join(cmd)}
    except OSError as exc:
        return {"ok": False, "error": str(exc), "command": " ".join(cmd)}
    out = (proc.stdout or "") + "\n" + (proc.stderr or "")
    # Older Godot prints "Unknown command line argument" and still exits 0 sometimes.
    if "unknown command line argument" in out.lower() and "--import" in out.lower():
        cmd2 = [binary, "--headless", "--editor", "--quit", "--path", str(pdir)]
        try:
            proc = subprocess.run(cmd2, cwd=str(pdir), capture_output=True, text=True, timeout=timeout)
            cmd = cmd2
            out = (proc.stdout or "") + "\n" + (proc.stderr or "")
        except (OSError, subprocess.TimeoutExpired) as exc:
            return {"ok": False, "error": str(exc), "command": " ".join(cmd2)}
    return {
        "ok": proc.returncode == 0,
        "returncode": proc.returncode,
        "command": " ".join(cmd),
        "tail": out[-1500:],
    }


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class GodotSession:
    """One running Godot + one TCP client. Sequential JSON-line requests."""

    def __init__(self, root: Path, *, port: int | None = None) -> None:
        self.root = Path(root)
        self.port = int(port or 0)
        self.proc: subprocess.Popen[str] | None = None
        self._sock: socket.socket | None = None
        self._buf = b""
        self._lock = threading.Lock()
        self._stdout: list[str] = []
        self._id = 0
        self.command: str = ""

    def start(
        self,
        *,
        binary: str,
        timeout: float = 20.0,
        headless: bool = True,
        env: dict[str, str] | None = None,
        extra_args: tuple[str, ...] = (),
        new_session: bool = False,
        creationflags: int = 0,
    ) -> dict[str, Any]:
        """Launch Godot and wait for the bridge. ``headless=False`` renders on the ``DISPLAY`` in ``env`` (the
        play worker); ``new_session`` puts Godot in its own process group so the whole tree can be killed."""
        ensure_bridge(self.root)
        ensure_import_sidecars(self.root)
        if not self.port:
            self.port = free_port()
        cmd = [binary]
        if headless:
            cmd.append("--headless")
        cmd += ["--audio-driver", "Dummy", *extra_args, "--path", str(self.root), "--",
                "--daedalus-bridge", str(self.port)]
        self.command = " ".join(cmd)
        try:
            self.proc = subprocess.Popen(
                cmd,
                cwd=str(self.root),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                env=env,
                start_new_session=new_session,
                creationflags=creationflags,
            )
        except OSError as exc:
            return {"ok": False, "error": str(exc), "command": self.command}
        threading.Thread(target=self._pump_stdout, daemon=True).start()
        deadline = time.time() + max(3.0, timeout)
        last_err = "connect"
        while time.time() < deadline:
            if self.proc.poll() is not None:
                return {
                    "ok": False,
                    "error": "godot exited before bridge listened",
                    "returncode": self.proc.returncode,
                    "stdout": self.stdout_tail(),
                    "command": self.command,
                }
            try:
                sock = socket.create_connection(("127.0.0.1", self.port), timeout=0.4)
                sock.settimeout(15.0)
                self._sock = sock
                ping = self.request("ping")
                if ping.get("ok") and ping.get("pong"):
                    ping["command"] = self.command
                    ping["port"] = self.port
                    return ping
                last_err = str(ping.get("error") or "ping failed")
            except OSError as exc:
                last_err = str(exc)
                time.sleep(CONNECT_PAUSE)
        return {
            "ok": False,
            "error": f"bridge not reachable: {last_err}",
            "stdout": self.stdout_tail(),
            "command": self.command,
            "port": self.port,
        }

    def attach(self, host: str = "127.0.0.1") -> None:
        sock = socket.create_connection((host, self.port), timeout=2.0)
        sock.settimeout(15.0)
        self._sock = sock

    def request(self, op: str, **fields: Any) -> dict[str, Any]:
        if self._sock is None:
            return {"ok": False, "error": "not_connected", "op": op}
        self._id += 1
        payload = {"id": self._id, "op": op}
        payload.update({k: v for k, v in fields.items() if v is not None})
        line = json.dumps(payload, ensure_ascii=False) + "\n"
        with self._lock:
            try:
                self._sock.sendall(line.encode("utf-8"))
                raw = self._read_line()
            except OSError as exc:
                return {"ok": False, "error": str(exc), "op": op}
        if not raw:
            return {"ok": False, "error": "empty_reply", "op": op}
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return {"ok": False, "error": "bad_json", "raw": raw[:240], "op": op}
        if not isinstance(data, dict):
            return {"ok": False, "error": "reply_not_object", "op": op}
        return data

    def close(self) -> None:
        try:
            if self._sock is not None:
                try:
                    self.request("quit")
                except Exception:
                    pass
        finally:
            if self._sock is not None:
                try:
                    self._sock.close()
                except OSError:
                    pass
                self._sock = None
            if self.proc is not None and self.proc.poll() is None:
                try:
                    self.proc.terminate()
                    self.proc.wait(timeout=4)
                except Exception:
                    try:
                        self.proc.kill()
                    except Exception:
                        pass
            self.proc = None

    def stdout_tail(self, n: int = 40) -> str:
        return "".join(self._stdout[-n:])

    def _pump_stdout(self) -> None:
        proc = self.proc
        if proc is None or proc.stdout is None:
            return
        try:
            for line in proc.stdout:
                self._stdout.append(line)
                if len(self._stdout) > 400:
                    del self._stdout[:200]
        except Exception:
            return

    def _read_line(self) -> str:
        assert self._sock is not None
        while b"\n" not in self._buf:
            chunk = self._sock.recv(65536)
            if not chunk:
                break
            self._buf += chunk
        if b"\n" not in self._buf:
            text = self._buf.decode("utf-8", errors="replace")
            self._buf = b""
            return text.strip()
        line, self._buf = self._buf.split(b"\n", 1)
        return line.decode("utf-8", errors="replace").strip()


def player_path_from_tree(tree: Any) -> str:
    rows = tree if isinstance(tree, list) else []
    named = ""
    typed = ""
    for row in rows:
        if not isinstance(row, dict):
            continue
        name = str(row.get("name") or "")
        ntype = str(row.get("type") or "")
        path = str(row.get("path") or "")
        if name == "Player" and path:
            named = path
        if ntype in {"CharacterBody2D", "CharacterBody3D"} and path and not typed:
            typed = path
    return named or typed


def _vec(value: Any) -> tuple[float, float, float]:
    if isinstance(value, dict):
        return (
            float(value.get("x") or 0),
            float(value.get("y") or 0),
            float(value.get("z") or 0),
        )
    return (0.0, 0.0, 0.0)


def position_delta(before: dict[str, Any], after: dict[str, Any]) -> float:
    b = _vec((before.get("node") or {}).get("position") if isinstance(before.get("node"), dict) else None)
    a = _vec((after.get("node") or {}).get("position") if isinstance(after.get("node"), dict) else None)
    return abs(a[0] - b[0]) + abs(a[1] - b[1]) + abs(a[2] - b[2])


def movement_probe(session: GodotSession, *, frames: int = 24) -> dict[str, Any]:
    """Press ui_right, wait, confirm the player node moved."""
    tree = session.request("scene_tree")
    path = player_path_from_tree(tree.get("tree"))
    if not path:
        found = session.request("find_node", name="Player")
        path = str(found.get("path") or "")
    if not path:
        return {"ok": False, "moved": False, "error": "player_node_missing", "tree_ok": tree.get("ok")}
    before = session.request("inspect_node", path=path)
    session.request("send_input", action="ui_right", pressed=True)
    session.request("wait_frames", n=frames)
    after = session.request("inspect_node", path=path)
    session.request("send_input", action="ui_right", pressed=False)
    delta = position_delta(before, after)
    return {
        "ok": delta >= 0.01,
        "moved": delta >= 0.01,
        "delta": round(delta, 4),
        "path": path,
        "before": before.get("node"),
        "after": after.get("node"),
    }


def live_dod(
    session: GodotSession,
    *,
    fps_budget: float = DEFAULT_FPS_BUDGET,
) -> dict[str, Any]:
    ping = session.request("ping")
    errors = session.request("errors")
    fps = session.request("fps_sample")
    move = movement_probe(session)
    fps_val = float(fps.get("fps") or ping.get("fps") or 0)
    headless = bool(fps.get("headless"))
    # Headless often reports 0 FPS; that is not a fail. A tiny positive FPS still counts.
    fps_ok = fps_val >= fps_budget or (headless and fps_val <= 0)
    console_flags = list(errors.get("errors") or [])
    checks = [
        {"id": "bridge_ping", "ok": bool(ping.get("ok") and ping.get("pong"))},
        {"id": "player_moved", "ok": bool(move.get("moved")), "detail": move.get("path") or move.get("error")},
        {"id": "console_bridge", "ok": not console_flags, "detail": console_flags[:5]},
        {"id": "fps", "ok": fps_ok, "detail": fps_val, "warn_only": headless and fps_val <= 0},
    ]
    blocking = [c["id"] for c in checks if not c["ok"] and not c.get("warn_only") and c["id"] != "fps"]
    if not fps_ok and not (headless and fps_val <= 0):
        blocking.append("fps")
    return {
        "ok": not blocking,
        "checks": checks,
        "blocking": blocking,
        "move": move,
        "fps": fps_val,
        "game": ping.get("game") or errors.get("game") or {},
        "stdout": session.stdout_tail(20),
    }
