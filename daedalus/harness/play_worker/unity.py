"""Unity support of the play worker: find the Editor, build a (non-development) Windows player from a COPY of the
project, and launch it with the bridge. The owner's project is never modified; the player is cached by content hash."""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

from ... import godot_bridge
from .errors import PlayError

_ASSETS = Path(__file__).parent / "unity_assets"
_SKIP_ROOT = {"Library", "Temp", "Logs", "obj", "Builds", ".git", ".vs", "UserSettings"}   # project root only
_DONE = ".daedalus-complete"
_BUILD_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()
BUILD_TIMEOUT_S = 900.0


def _is_shim(path: str) -> bool:
    """The Unity Hub puts a launcher called Unity.exe in ...\\Unity\\bin; it is not an Editor and cannot build."""
    return Path(path).parent.name.lower() == "bin" and Path(path).parent.parent.name.lower() == "unity"


def _hub_bases() -> list[Path]:
    return [Path("C:/Program Files/Unity/Hub/Editor"), Path("~/Unity/Hub/Editor").expanduser(),
            Path("/Applications/Unity/Hub/Editor")]


def find_unity() -> dict[str, Any]:
    """UNITY_PATH/UNITY_EDITOR, then Hub installs (newest version first), then PATH (last: it may be a launcher shim);
    shown, never assumed."""
    env = (os.environ.get("UNITY_PATH") or os.environ.get("UNITY_EDITOR") or "").strip()
    if env:
        p = Path(env).expanduser()
        if p.is_file():
            return {"ok": True, "path": str(p), "source": "env"}
        return {"ok": False, "error": "engine_missing", "source": "env",
                "message": f"UNITY_PATH is set but is not a file: {env}"}
    bases = _hub_bases()
    exe = "Unity.exe" if os.name == "nt" else "Unity"
    for base in bases:
        if not base.is_dir():
            continue
        versions = sorted((d for d in base.iterdir() if d.is_dir()),
                          key=lambda d: [int(n) for n in re.findall(r"\d+", d.name)], reverse=True)
        for d in versions:
            for cand in (d / "Editor" / exe, d / "Unity.app" / "Contents" / "MacOS" / "Unity"):
                if cand.is_file():
                    return {"ok": True, "path": str(cand), "source": "known-dir", "version": d.name}
    hit = shutil.which("Unity") or shutil.which("unity")      # last: a PATH entry may be a Hub launcher shim, not the Editor
    if hit and not _is_shim(hit):
        return {"ok": True, "path": hit, "source": "path"}
    return {"ok": False, "error": "engine_missing", "source": "none",
            "message": "Unity Editor not found. Set UNITY_PATH. Files are not a playtest."}


def is_unity_project(root: Path) -> bool:
    return (root / "Assets").is_dir() and (root / "ProjectSettings").is_dir()


def content_hash(project: Path, editor: str) -> str:
    h = hashlib.sha256()
    h.update(editor.encode())
    for name in ("DaedalusPlayBridge.cs", "DaedalusBuild.cs"):
        h.update((_ASSETS / name).read_bytes())
    for sub in ("Assets", "Packages", "ProjectSettings"):
        base = project / sub
        for f in sorted(base.rglob("*")) if base.is_dir() else []:
            if f.is_file():
                h.update(str(f.relative_to(project)).replace("\\", "/").encode())
                with open(f, "rb") as fh:                      # streamed: textures and audio can be gigabytes
                    for chunk in iter(lambda: fh.read(1 << 20), b""):
                        h.update(chunk)
    return h.hexdigest()[:20]


def _copy_project(src: Path, dst: Path) -> None:
    def ignore(d: str, names: list[str]) -> set[str]:
        # only the project root's own caches: Assets/Build or Assets/Resources/Temp are the game's content
        return {n for n in names if n in _SKIP_ROOT} if Path(d) == src else set()
    shutil.copytree(src, dst, ignore=ignore)


def _inject(project: Path) -> None:
    target = project / "Assets" / "DaedalusHarness"
    (target / "Editor").mkdir(parents=True, exist_ok=True)
    shutil.copyfile(_ASSETS / "DaedalusPlayBridge.cs", target / "DaedalusPlayBridge.cs")
    shutil.copyfile(_ASSETS / "DaedalusBuild.cs", target / "Editor" / "DaedalusBuild.cs")


def _cached(out: Path) -> bool:
    """A cached player counts only if its completion marker is there (an interrupted copy has none)."""
    return (out / "Game.exe").is_file() and (out / _DONE).is_file()


def build_player(project: Path, cache_root: Path, editor: str, *, timeout: float = BUILD_TIMEOUT_S,
                 log: Path | None = None) -> dict[str, Any]:
    """Return {ok, exe, cached, build_seconds}. A failed build is a typed error with the log tail; never a pass.
    One build per content key at a time; the result is published atomically (copy, mark complete, rename)."""
    key = content_hash(project, editor)
    out = cache_root / key
    if _cached(out):
        return {"ok": True, "exe": str(out / "Game.exe"), "cached": True, "key": key, "build_seconds": 0.0}
    if os.name != "nt":
        raise PlayError("unsupported_platform", "the Unity play harness builds a Windows player; Windows only for now")
    with _LOCKS_GUARD:
        lock = _BUILD_LOCKS.setdefault(key, threading.Lock())
    with lock:
        if _cached(out):                                  # another session built it while we waited
            return {"ok": True, "exe": str(out / "Game.exe"), "cached": True, "key": key, "build_seconds": 0.0}
        return _build_locked(project, cache_root, editor, key, timeout, log)


def _build_locked(project: Path, cache_root: Path, editor: str, key: str, timeout: float,
                  log: Path | None) -> dict[str, Any]:
    import tempfile
    out = cache_root / key
    work = Path(tempfile.mkdtemp(prefix="daedalus-unity-build-"))
    started = time.time()
    try:
        copy = work / "project"
        _copy_project(project, copy)
        _inject(copy)
        build_log = log or (work / "build.log")
        tmp_out = work / "out"
        cmd = [editor, "-batchmode", "-nographics", "-quit", "-projectPath", str(copy),
               "-buildTarget", "StandaloneWindows64", "-executeMethod", "Daedalus.PlayBuild.BuildWin64",
               "-daedalusBuildOutput", str(tmp_out), "-logFile", str(build_log)]
        try:
            proc = subprocess.run(cmd, timeout=timeout, capture_output=True, text=True)
        except subprocess.TimeoutExpired:
            raise PlayError("build_failed", f"Unity build exceeded {int(timeout)}s")
        tail = ""
        try:
            tail = Path(build_log).read_text(encoding="utf-8", errors="replace")[-1500:]
        except OSError:
            pass
        built = tmp_out / "Game.exe"
        if proc.returncode != 0 or not built.is_file():
            lic = "licen" in tail.lower() and ("no valid" in tail.lower() or "not activated" in tail.lower())
            raise PlayError("licence_missing" if lic else "build_failed",
                            "Unity licence is not active for batch mode" if lic else f"Unity build failed (exit {proc.returncode})",
                            returncode=proc.returncode, log_tail=tail[-800:])
        cache_root.mkdir(parents=True, exist_ok=True)
        staging = cache_root / f"{key}.tmp-{os.getpid()}-{threading.get_ident()}"
        shutil.rmtree(staging, ignore_errors=True)
        shutil.copytree(tmp_out, staging)
        (staging / _DONE).write_text(str(time.time()), encoding="utf-8")      # last: only a whole copy carries it
        if out.exists():
            shutil.rmtree(out, ignore_errors=True)                           # an incomplete earlier copy
        try:
            os.replace(staging, out)
        except OSError:
            if not _cached(out):
                raise
            shutil.rmtree(staging, ignore_errors=True)
        return {"ok": True, "exe": str(out / "Game.exe"), "cached": False, "key": key,
                "build_seconds": round(time.time() - started, 1)}
    finally:
        shutil.rmtree(work, ignore_errors=True)


class UnityGame(godot_bridge.GodotSession):
    """Same JSON-lines client as the Godot bridge; launches the built player instead of the engine."""

    def __init__(self, root: Path, *, size: tuple[int, int] = (640, 480), out_dir: Path | None = None) -> None:
        super().__init__(root)
        self.size = size
        self.out_dir = Path(out_dir) if out_dir else Path(root)
        self.player_log = self.out_dir / "player.log"       # a Windows player logs to a file, not to stdout

    def stdout_tail(self, n: int = 40) -> str:
        text = super().stdout_tail(n)
        try:
            lines = self.player_log.read_text(encoding="utf-8", errors="replace").splitlines()[-n:]
            text += "\n".join(lines)
        except OSError:
            pass
        return text

    def start(self, *, binary: str, timeout: float = 60.0, headless: bool = False, env: dict[str, str] | None = None,  # type: ignore[override]
              extra_args: tuple[str, ...] = (), new_session: bool = False, creationflags: int = 0) -> dict[str, Any]:
        import socket
        if not self.port:
            self.port = godot_bridge.free_port()
        cmd = [binary, "-screen-width", str(self.size[0]), "-screen-height", str(self.size[1]), "-screen-fullscreen", "0",
               "-window-mode", "windowed", "-logFile", str(self.player_log),
               "--daedalus-bridge", str(self.port), "--daedalus-out", str(self.out_dir)]
        self.command = " ".join(cmd)
        try:
            self.proc = subprocess.Popen(cmd, cwd=str(Path(binary).parent), stdout=subprocess.PIPE,
                                         stderr=subprocess.STDOUT, text=True, bufsize=1, env=env,
                                         creationflags=creationflags)
        except OSError as exc:
            return {"ok": False, "error": str(exc), "command": self.command}
        threading.Thread(target=self._pump_stdout, daemon=True).start()
        deadline = time.time() + max(5.0, timeout)
        last = "connect"
        while time.time() < deadline:
            if self.proc.poll() is not None:
                return {"ok": False, "error": "unity player exited before bridge listened",
                        "returncode": self.proc.returncode, "stdout": self.stdout_tail()}
            try:
                sock = socket.create_connection(("127.0.0.1", self.port), timeout=0.4)
                sock.settimeout(15.0)
                self._sock = sock
                ping = self.request("ping")
                if ping.get("ok") and ping.get("pong"):
                    ping["port"] = self.port
                    return ping
                last = str(ping.get("error") or "ping failed")
            except OSError as exc:
                last = str(exc)
                time.sleep(0.25)
        return {"ok": False, "error": f"bridge not reachable: {last}", "stdout": self.stdout_tail()}
