"""Unity support of the play worker: find the Editor, build a development Windows player from a COPY of the project,
and launch it with the bridge. The owner's project is never modified; the player is cached by content hash."""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from ... import godot_bridge
from .errors import PlayError

_ASSETS = Path(__file__).parent / "unity_assets"
_SKIP_DIRS = {"Library", "Temp", "Logs", "obj", "Builds", "Build", ".git", ".vs", "UserSettings"}
BUILD_TIMEOUT_S = 900.0


def _is_shim(path: str) -> bool:
    """The Unity Hub puts a launcher called Unity.exe in ...\\Unity\\bin; it is not an Editor and cannot build."""
    return Path(path).parent.name.lower() == "bin" and Path(path).parent.parent.name.lower() == "unity"


def find_unity() -> dict[str, Any]:
    """UNITY_PATH/UNITY_EDITOR, PATH, then Hub installs (newest version first); shown, never assumed."""
    env = (os.environ.get("UNITY_PATH") or os.environ.get("UNITY_EDITOR") or "").strip()
    if env:
        p = Path(env).expanduser()
        if p.is_file():
            return {"ok": True, "path": str(p), "source": "env"}
        return {"ok": False, "error": "engine_missing", "source": "env",
                "message": f"UNITY_PATH is set but is not a file: {env}"}
    bases = [Path("C:/Program Files/Unity/Hub/Editor"), Path("~/Unity/Hub/Editor").expanduser(),
             Path("/Applications/Unity/Hub/Editor")]
    exe = "Unity.exe" if os.name == "nt" else "Unity"
    for base in bases:
        if not base.is_dir():
            continue
        versions = sorted((d for d in base.iterdir() if d.is_dir()),
                          key=lambda d: [int(n) for n in re.findall(r"\d+", d.name)], reverse=True)
        for d in versions:
            cand = d / "Editor" / exe
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
                h.update(f.read_bytes())
    return h.hexdigest()[:20]


def _copy_project(src: Path, dst: Path) -> None:
    def ignore(d: str, names: list[str]) -> set[str]:
        return {n for n in names if n in _SKIP_DIRS}
    shutil.copytree(src, dst, ignore=ignore)


def _inject(project: Path) -> None:
    target = project / "Assets" / "DaedalusHarness"
    (target / "Editor").mkdir(parents=True, exist_ok=True)
    shutil.copyfile(_ASSETS / "DaedalusPlayBridge.cs", target / "DaedalusPlayBridge.cs")
    shutil.copyfile(_ASSETS / "DaedalusBuild.cs", target / "Editor" / "DaedalusBuild.cs")


def build_player(project: Path, cache_root: Path, editor: str, *, timeout: float = BUILD_TIMEOUT_S,
                 log: Path | None = None) -> dict[str, Any]:
    """Return {ok, exe, cached, build_seconds}. A failed build is a typed error with the log tail; never a pass."""
    key = content_hash(project, editor)
    out = cache_root / key
    exe = out / "Game.exe"
    if exe.is_file():
        return {"ok": True, "exe": str(exe), "cached": True, "key": key, "build_seconds": 0.0}
    if os.name != "nt":
        raise PlayError("unsupported_platform", "the Unity play harness builds a Windows player; Windows only for now")
    import tempfile
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
        shutil.rmtree(out, ignore_errors=True)
        shutil.copytree(tmp_out, out)
        return {"ok": True, "exe": str(out / "Game.exe"), "cached": False, "key": key,
                "build_seconds": round(time.time() - started, 1)}
    finally:
        shutil.rmtree(work, ignore_errors=True)


class UnityGame(godot_bridge.GodotSession):
    """Same JSON-lines client as the Godot bridge; launches the built player instead of the engine."""

    def start(self, *, binary: str, timeout: float = 60.0, headless: bool = False, env: dict[str, str] | None = None,  # type: ignore[override]
              extra_args: tuple[str, ...] = (), new_session: bool = False, creationflags: int = 0,
              out_dir: Path | None = None) -> dict[str, Any]:
        import socket
        import threading
        if not self.port:
            self.port = godot_bridge.free_port()
        cmd = [binary, "-screen-width", str(self._w), "-screen-height", str(self._h), "-screen-fullscreen", "0",
               "-window-mode", "windowed",
               "--daedalus-bridge", str(self.port), "--daedalus-out", str(out_dir or self.root)]
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

    _w = 640
    _h = 480
