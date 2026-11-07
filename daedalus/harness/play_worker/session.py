"""One rendered play session: its own project copy, display, port, process groups and artifacts."""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from ... import godot_bridge
from . import capture, engines
from .backend import get_backend
from .errors import PlayError

_COPY_SKIP = shutil.ignore_patterns(".git", "node_modules", "__pycache__", "studio", "*.tmp")
MAX_FRAMES = 200          # artifacts kept per session
CHANGE_MIN = 0.002        # share of pixels that must differ to count as an observed change
NOISY = 0.25              # idle drift above this: the scene moves on its own, input cannot be told apart


def artifact_root() -> Path:
    from ..context import data_dir
    return data_dir() / "play"


_artifact_root = artifact_root          # kept for existing callers


_SESSION_META: dict[tuple[str, float], tuple[str, float]] = {}


def window_frames(slug: str) -> list[tuple[float, Path, str, float]]:
    """(mtime, file, session id, session start) of every window frame a session of ``slug`` wrote; the one place
    that knows the on-disk layout (session.json + frame_NNNN_window.png) so readers do not copy it."""
    root = artifact_root()
    out: list[tuple[float, Path, str, float]] = []
    if not root.is_dir():
        return out
    for sess in root.iterdir():
        meta = sess / "session.json"
        try:
            key = (str(meta), meta.stat().st_mtime)
        except OSError:
            continue
        if key not in _SESSION_META:                       # session.json never changes: read it once
            try:
                info = json.loads(meta.read_text(encoding="utf-8"))
                _SESSION_META[key] = (str(info.get("slug") or ""), float(info.get("started_at") or 0.0))
            except (OSError, ValueError, TypeError):
                _SESSION_META[key] = ("", 0.0)
            if len(_SESSION_META) > 512:
                _SESSION_META.pop(next(iter(_SESSION_META)))
        sess_slug, started = _SESSION_META[key]
        if sess_slug != slug:
            continue
        for f in sess.glob("frame_*_window.png"):
            try:
                out.append((f.stat().st_mtime, f, sess.name, started))
            except OSError:
                continue
    return out


class PlaySession:
    def __init__(self, project_dir: Path, *, slug: str = "", size: tuple[int, int] = (640, 480),
                 session_id: str = "", artifacts: Path | None = None, env_extra: dict[str, str] | None = None) -> None:
        self.env_extra = dict(env_extra or {})
        self.id = session_id or uuid.uuid4().hex[:12]
        self.slug = slug
        self.source = Path(project_dir)
        self.size = (max(160, min(int(size[0]), 1920)), max(120, min(int(size[1]), 1080)))
        self.artifacts = (artifacts or (_artifact_root() / self.id))
        self.workdir: Path | None = None
        self.backend = get_backend(self.size)       # where the game is drawn and how we look at / drive it
        self.display = getattr(self.backend, "display", None)     # Linux: the Xvfb display (None on Windows)
        self.game: godot_bridge.GodotSession | None = None
        self.window_id = ""
        self.state = "new"           # new -> running -> stopped | failed
        self.level = 0               # proof level reached by what was OBSERVED
        self.started_at = 0.0
        self.last_used = time.time()
        self._frames: list[dict[str, Any]] = []
        self._events: list[dict[str, Any]] = []
        self._lock = threading.RLock()

    # ---- lifecycle -------------------------------------------------------------------------------------
    def start(self, *, timeout: float = 30.0) -> dict[str, Any]:
        with self._lock:
            if self.state != "new":
                raise PlayError("bad_request", f"session is {self.state}")
            eng = engines.find_godot()
            if not eng.get("ok"):
                self.state = "failed"
                raise PlayError("engine_missing", eng.get("message", "engine missing"), source=eng.get("source"))
            if not (self.source / "project.godot").is_file():
                self.state = "failed"
                raise PlayError("start_failed", "not a Godot project (no project.godot)", slug=self.slug)
            try:
                self.artifacts.mkdir(parents=True, exist_ok=True)
                (self.artifacts / "session.json").write_text(
                    json.dumps({"id": self.id, "slug": self.slug, "started_at": time.time()}), encoding="utf-8")
                self.workdir = Path(tempfile.mkdtemp(prefix=f"daedalus-play-{self.id}-"))
                proj = self.workdir / "project"
                shutil.copytree(self.source, proj, ignore=_COPY_SKIP)   # the owner's project is never touched
                imp = godot_bridge.run_import(proj, eng["path"], timeout=120)
                if imp.get("ok") is False and imp.get("error"):
                    self._event("import_warning", error=str(imp.get("error"))[:200])
                missing = [k for k, ok in self.backend.tools().items() if not ok]
                if missing:
                    raise PlayError("tool_missing", f"a rendered session needs: {', '.join(missing)}", tools=missing)
                backend_env = self.backend.start(self.size)
                env = {**os.environ, **backend_env, **self.env_extra}
                env.pop("WAYLAND_DISPLAY", None)
                self.game = godot_bridge.GodotSession(proj)
                ping = self.game.start(
                    binary=eng["path"], timeout=timeout, headless=False, env=env,
                    extra_args=self.backend.engine_args(self.size), **self.backend.popen_flags(),
                )
                if not ping.get("ok"):
                    raise PlayError("start_failed", str(ping.get("error") or "bridge did not answer"),
                                    stdout=str(ping.get("stdout", ""))[-600:])
                self.window_id = self.backend.find_window(self.game.proc.pid if self.game and self.game.proc else 0)
                self.state = "running"
                self.started_at = time.time()
                self.level = 1
                self._event("started", engine=eng["path"], display=self.backend.label(), window=self.window_id,
                            backend=self.backend.name,
                            headless=bool((self.game.request("fps_sample") or {}).get("headless")))
            except PlayError:
                self.stop()
                self.state = "failed"
                raise
            except Exception as exc:
                self.stop()
                self.state = "failed"
                raise PlayError("start_failed", f"{type(exc).__name__}: {exc}") from exc
            return self.status()

    def stop(self) -> dict[str, Any]:
        """Idempotent. Kills Godot's whole process group, then Xvfb's, then removes the temp copy."""
        # A long wait/input holds the session lock; stopping must still work, so after a short wait we kill the
        # processes anyway (the in-flight call then fails with session_stopped) instead of blocking behind it.
        got = self._lock.acquire(timeout=2.0)
        try:
            was = self.state
            try:
                if self.game is not None:
                    try:
                        self.game.close()          # asks the bridge to quit, then terminates
                    finally:
                        self.backend.kill(self.game.proc, 1.0)
                self.backend.stop()
            finally:
                if self.workdir is not None:
                    shutil.rmtree(self.workdir, ignore_errors=True)
                    self.workdir = None
                if was != "failed":
                    self.state = "stopped"
                self._write_manifest()
            return {"ok": True, "state": self.state, "level": self.level, "artifacts": str(self.artifacts)}
        finally:
            if got:
                self._lock.release()

    # ---- observation -----------------------------------------------------------------------------------
    def refresh(self) -> str:
        """Mark a session whose game process died as failed (a crash must not hold a slot)."""
        if self.state == "running" and (self.game is None or self.game.proc is None or self.game.proc.poll() is not None):
            self.state = "failed"
        return self.state

    def _need_running(self) -> None:
        if self.state != "running" or self.game is None:
            raise PlayError("session_stopped", f"session is {self.state}")
        if self.game.proc is None or self.game.proc.poll() is not None:
            self.state = "failed"
            raise PlayError("session_stopped", "the game process exited", returncode=getattr(self.game.proc, "returncode", None))
        self.last_used = time.time()

    def frame(self, source: str = "window") -> dict[str, Any]:
        """``window``: what the X server shows (a player's view). ``engine``: the viewport the engine rendered."""
        with self._lock:
            self._need_running()
            n = len(self._frames) + 1
            if n > MAX_FRAMES:
                raise PlayError("bad_request", f"frame limit ({MAX_FRAMES}) reached for this session")
            out = self.artifacts / f"frame_{n:04d}_{source}.png"
            if source == "window":
                info = self.backend.capture(self.window_id, out)
            elif source == "engine":
                shot = self.game.request("screenshot")
                if not shot.get("ok"):
                    raise PlayError("window_missing", f"engine viewport unavailable: {shot.get('error')}")
                src = (self.workdir / "project" / str(shot.get("path", ""))) if self.workdir else None
                if src is None or not src.is_file():
                    raise PlayError("window_missing", "engine screenshot file not found")
                shutil.copyfile(src, out)
                info = capture.describe_frame(out)
            else:
                raise PlayError("bad_request", f"unknown frame source: {source!r}")
            info.update(index=n, source=source, ts=time.time())
            self._frames.append(info)
            if source == "window" and not info["blank"] and self.level < 3:
                self.level = 3
            self._event("frame", index=n, source=source, sha256=info["sha256"], blank=info["blank"])
            return {"ok": True, **{k: v for k, v in info.items() if k != "path"},
                    "file": out.name, "level": self.level}

    def send(self, *, keys: list[str] | None = None, mouse: dict[str, Any] | None = None,
             hold_ms: int = 80, via: str = "os") -> dict[str, Any]:
        with self._lock:
            self._need_running()
            sent: list[dict[str, Any]] = []
            if via == "os":
                for k in keys or []:
                    sent.append(self.backend.key(str(k), hold_ms))
                if mouse:
                    sent.append(self.backend.mouse(str(mouse.get("kind", "click")), int(mouse.get("x", 0)),
                                                   int(mouse.get("y", 0)), str(mouse.get("button", "left")), hold_ms))
            elif via == "bridge":
                assert self.game is not None
                for k in keys or []:
                    for pressed in (True, False):
                        r = self.game.request("send_key", key=str(k), pressed=pressed)
                        if not r.get("ok"):
                            raise PlayError("bad_request", f"bridge refused key {k!r}: {r.get('error')}")
                        if pressed:
                            time.sleep(max(0, min(hold_ms, 5000)) / 1000.0)
                    sent.append({"via": "bridge", "type": "key", "key": str(k), "hold_ms": hold_ms})
                if mouse:
                    r = self.game.request("send_mouse", x=float(mouse.get("x", 0)), y=float(mouse.get("y", 0)),
                                          button=str(mouse.get("button", "left")), kind=str(mouse.get("kind", "click")))
                    if not r.get("ok"):
                        raise PlayError("bad_request", f"bridge refused mouse: {r.get('error')}")
                    sent.append({"via": "bridge", "type": "mouse", **{k: mouse.get(k) for k in ("kind", "x", "y", "button")}})
            else:
                raise PlayError("bad_request", f"unknown input route: {via!r}")
            for s in sent:
                self._event("input", **{("input_" + k if k == "type" else k): v for k, v in s.items()})
            return {"ok": True, "sent": sent}

    def probe_input(self, *, keys: list[str] | None = None, mouse: dict[str, Any] | None = None,
                    via: str = "os", settle_ms: int = 400, hold_ms: int = 80) -> dict[str, Any]:
        """Frame, idle control, input, frame. L4 only when the input moved the picture MORE than the scene moves on
        its own: the effect (idle frame -> after frame) must clear the idle drift (before -> idle) by a margin, and a
        scene that animates heavily by itself is reported inconclusive instead of being credited with the input."""
        with self._lock:
            before = self.frame("window")
            self.wait(ms=settle_ms)
            idle = self.frame("window")                       # control: same scene, no input yet
            idle_frac = capture.changed_fraction(self.artifacts / before["file"], self.artifacts / idle["file"])
            sent = self.send(keys=keys, mouse=mouse, via=via, hold_ms=hold_ms)
            self.wait(ms=settle_ms)
            after = self.frame("window")
            effect = capture.changed_fraction(self.artifacts / idle["file"], self.artifacts / after["file"])
            noisy = idle_frac >= NOISY
            blank = before["blank"] or idle["blank"] or after["blank"]
            changed = (not noisy) and (not blank) and effect >= max(CHANGE_MIN, 2 * idle_frac)
            reason = ("scene_animates_on_its_own" if noisy else "blank_frame" if blank else "")
            if changed and self.level < 4:
                self.level = 4
            self._event("probe", changed=changed, effect=round(effect, 4), idle=round(idle_frac, 4), via=via,
                        conclusive=not noisy and not blank, before=before["sha256"], after=after["sha256"])
            return {"ok": True, "observed_change": changed, "changed_fraction": round(effect, 4),
                    "idle_fraction": round(idle_frac, 4), "conclusive": not noisy and not blank, "reason": reason,
                    "before": before, "idle": idle, "after": after, "sent": sent["sent"], "level": self.level}

    def wait(self, *, frames: int = 0, ms: int = 0) -> dict[str, Any]:
        with self._lock:
            self._need_running()
            if frames:
                r = self.game.request("wait_frames", n=max(1, min(int(frames), 180)))
                if not r.get("ok"):
                    raise PlayError("bad_request", str(r.get("error")))
                return {"ok": True, "waited_frames": r.get("waited"), "fps": r.get("fps")}
            time.sleep(max(0, min(int(ms), 30000)) / 1000.0)
            return {"ok": True, "waited_ms": int(ms)}

    def _bridge(self, op: str, **kw: Any) -> dict[str, Any]:
        with self._lock:
            self._need_running()
            r = self.game.request(op, **kw)
            self.last_used = time.time()
            return r

    def tree(self) -> dict[str, Any]:
        return self._bridge("scene_tree")

    def errors(self) -> dict[str, Any]:
        return {**self._bridge("errors"), "stdout_tail": self.game.stdout_tail(20) if self.game else ""}

    def fps(self) -> dict[str, Any]:
        return self._bridge("fps_sample")

    def game_state(self, path: str = "/root/Main") -> dict[str, Any]:
        return self._bridge("call_method", path=path, method="state", args=[])

    # ---- reporting -------------------------------------------------------------------------------------
    def _event(self, etype: str, **fields: Any) -> None:
        self._events.append({"ts": round(time.time(), 3), **fields, "event": etype})
        try:
            self.artifacts.mkdir(parents=True, exist_ok=True)
            with open(self.artifacts / "input-log.jsonl", "a", encoding="utf-8") as fh:
                fh.write(json.dumps(self._events[-1], ensure_ascii=False) + "\n")
        except OSError:
            pass

    def status(self) -> dict[str, Any]:
        alive = bool(self.game and self.game.proc and self.game.proc.poll() is None)
        return {"ok": True, "id": self.id, "slug": self.slug, "state": self.state, "level": self.level,
                "alive": alive, "size": list(self.size), "window": self.window_id,
                "display": self.backend.label(), "backend": self.backend.name,
                "frames": len(self._frames), "uptime_s": round(time.time() - self.started_at, 1) if self.started_at else 0}

    def _write_manifest(self) -> None:
        try:
            self.artifacts.mkdir(parents=True, exist_ok=True)
            if self.game is not None:
                (self.artifacts / "engine.log").write_text(self.game.stdout_tail(400), encoding="utf-8", errors="replace")
            files = []
            for p in sorted(self.artifacts.iterdir()):
                if p.is_file() and p.name != "manifest.json":
                    files.append({"name": p.name, "bytes": p.stat().st_size, "sha256": capture.sha256_file(p)})
            (self.artifacts / "manifest.json").write_text(json.dumps(
                {"session": self.id, "slug": self.slug, "state": self.state, "level": self.level, "files": files},
                indent=2), encoding="utf-8")
        except OSError:
            pass
