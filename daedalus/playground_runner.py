"""Дедал Playground — subprocess runner (dev sandbox testing).

Вдига Python/Node app като отделен процес на свободен порт, с **sanitized
env** (без продукционни тайни), capture на логове и auto-kill. Само за
DEV тестване — в публикувания ADN spawn-ването на портове е ограничено
(static apps се serve-ват in-process вместо това).

State: in-memory registry на живи процеси (по slug). Логове: rolling buffer.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import shlex
import shutil
import socket
import time
import urllib.parse
from collections import deque
from pathlib import Path
from typing import Any

from . import playground as pg
from .playground_runtime import PlaygroundRuntime, is_local_only_kind, local_only_kind_message
from .proc import argv_for_command, argv_from_resolved_shlex, terminate_process_tree

log = logging.getLogger(__name__)

# slug -> running process record
_RUNNING: dict[str, dict[str, Any]] = {}

MAX_LOG_LINES = 400
IDLE_KILL_SEC = 1800  # auto-kill after 30 min idle

def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _child_env(port: int) -> dict[str, str]:
    return PlaygroundRuntime.child_env(port)


def _resolve_cmd(
    proj: dict[str, Any],
    port: int,
    *,
    tunnel: bool = False,
    preview_base: str = "",
) -> str:
    return PlaygroundRuntime.resolve_cmd(
        proj, port, tunnel=tunnel, preview_base=preview_base,
    )


async def _pump_logs(slug: str, proc: asyncio.subprocess.Process) -> None:
    rec = _RUNNING.get(slug)
    if not rec or proc.stdout is None:
        return
    buf: deque = rec["logs"]
    try:
        async for raw in proc.stdout:
            line = raw.decode("utf-8", errors="replace").rstrip("\n")
            buf.append(f"{time.strftime('%H:%M:%S')} {line}")
    except Exception:
        pass


async def start(slug: str, *, install_deps: bool = False, tunnel: bool = False) -> dict[str, Any]:
    """Spawn the project as a subprocess. Returns run record."""
    proj = await pg.get_project(slug)
    if not proj:
        raise ValueError(f"project '{slug}' not found")
    slug = proj["slug"]
    if is_local_only_kind(proj.get("kind")):
        raise ValueError(local_only_kind_message(proj.get("kind", "")))
    if proj.get("kind") == "static":
        raise ValueError("static apps don't need a runner — use preview directly")

    await stop(slug)  # ensure clean slate

    root = pg.project_dir(slug)

    # Auto-recreate scaffold if project dir is missing or empty (e.g. after redeploy)
    if not root.exists() or not any(root.iterdir()):
        log.warning("Playground: project dir missing/empty for %s — re-scaffolding", slug)
        root.mkdir(parents=True, exist_ok=True)
        pg._write_scaffold(
            root, proj.get("kind", "static"),
            proj.get("name", slug), slug,
            proj.get("description", ""),
        )
    port = _free_port()
    preview_base = PlaygroundRuntime.admin_preview_base(slug)
    cmd = _resolve_cmd(proj, port, tunnel=tunnel, preview_base=preview_base)
    if not cmd:
        raise ValueError(f"no run command for kind={proj.get('kind')}")

    # optional dependency install (capped; result captured for smoke test)
    install_result: dict[str, Any] | None = None
    if install_deps:
        install_result = await _install_deps(proj, root)

    env = _child_env(port)
    argv = argv_from_resolved_shlex(cmd, env=env)
    if not argv:
        raise ValueError("empty run command after resolution")
    proc = await asyncio.create_subprocess_exec(
        *argv, cwd=str(root), env=env,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        start_new_session=True,
    )
    rec = {
        "slug": slug, "pid": proc.pid, "port": port, "cmd": cmd,
        "started_at": time.time(), "last_seen": time.time(),
        "status": "starting", "logs": deque(maxlen=MAX_LOG_LINES),
        "_proc": proc, "install": install_result, "tunnel": tunnel,
        "expo_url": "", "qr_url": "",
    }
    _RUNNING[slug] = rec
    asyncio.create_task(_pump_logs(slug, proc))
    await pg.update_project(slug, status="running")

    _kind = proj.get("kind")
    if tunnel and _kind == "expo":
        # Tunnel does not bind a local preview port — poll logs for exp:// instead.
        rec["status"] = "running"
        asyncio.create_task(_poll_expo_tunnel(slug))
    else:
        # health: wait briefly for the port to open
        # Expo Metro needs ~40s (first run with install); Streamlit ~20s; rest ~14s.
        _health_timeout = 45.0 if _kind == "expo" else 22.0 if _kind == "streamlit" else 14.0
        ok = await _wait_port(port, timeout=_health_timeout)
        rec["status"] = "running" if ok else "starting"
    log.info("Playground: started %s pid=%s port=%s tunnel=%s status=%s",
             slug, proc.pid, port, tunnel, rec["status"])
    return _public(rec)


async def _install_deps(proj: dict[str, Any], root: Path) -> dict[str, Any]:
    """Install project dependencies. Captures combined output + ok flag so the
    smoke test can surface install failures (ModuleNotFoundError, npm ERR, etc.)
    instead of swallowing them. Returns {ran, ok, output}.
    """
    kind = proj.get("kind")
    cmd: list[str] | None = None
    timeout = 300
    if kind == "expo" and (root / "package.json").exists():
        from .playground_expo_scaffold import repair_expo_package_json
        repair_expo_package_json(root)
        # --legacy-peer-deps needed — expo/react-native peer dep conflicts
        cmd = argv_for_command(
            "npm", "install", "--no-audit", "--no-fund", "--legacy-peer-deps",
            env=_child_env(0),
        )
        timeout = 600  # expo is heavy
    elif kind in ("vite", "vite-react", "node") and (root / "package.json").exists():
        cmd = argv_for_command("npm", "install", "--no-audit", "--no-fund", env=_child_env(0))
    elif kind in ("fastapi", "streamlit") and (root / "requirements.txt").exists():
        cmd = ["pip", "install", "-q", "-r", "requirements.txt"]
    if not cmd:
        return {"ran": False, "ok": True, "output": ""}
    try:
        # Sanitized env (allowlist) — AI-generated package.json may carry malicious
        # postinstall scripts; never expose production secrets to them.
        p = await asyncio.create_subprocess_exec(
            *cmd, cwd=str(root), env=_child_env(0),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
            start_new_session=True,
        )
        try:
            out, _ = await asyncio.wait_for(p.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            await terminate_process_tree(p, timeout=12.0)
            log.warning("Playground: dep install for %s timed out", proj["slug"])
            return {"ran": True, "ok": False, "output": "dependency install timed out"}
        text = (out or b"").decode("utf-8", errors="replace")
        ok = p.returncode == 0
        if not ok:
            log.warning("Playground: dep install for %s exited %s", proj["slug"], p.returncode)
        # Filter npm WARN / deprecation notices — they are not actionable errors.
        # Only keep lines that signal real problems (npm ERR!, pip ERROR, etc.)
        _warn_prefixes = ("npm warn ", "npm WARN ", "deprecated ", "no longer maintained",
                          "this is no longer ", "consider using ", "see npm-audit",)
        filtered = "\n".join(
            ln for ln in text.splitlines()
            if not any(ln.strip().lower().startswith(p.lower()) for p in _warn_prefixes)
        )
        return {"ran": True, "ok": ok, "output": filtered[-4000:]}
    except Exception as exc:
        log.warning("Playground: dep install for %s failed: %s", proj["slug"], exc)
        return {"ran": True, "ok": False, "output": str(exc)}


async def _wait_port(port: int, *, timeout: float = 10.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            r, w = await asyncio.open_connection("127.0.0.1", port)
            w.close()
            return True
        except Exception:
            await asyncio.sleep(0.4)
    return False


async def stop(slug: str) -> bool:
    rec = _RUNNING.pop(slug, None)
    if not rec:
        return False
    proc = rec.get("_proc")
    if proc and proc.returncode is None:
        await terminate_process_tree(proc, timeout=6.0)
    await pg.update_project(slug, status="idle")
    log.info("Playground: stopped %s", slug)
    return True


async def restart(slug: str, *, install_deps: bool = False) -> dict[str, Any]:
    await stop(slug)
    return await start(slug, install_deps=install_deps)


def status(slug: str) -> dict[str, Any] | None:
    rec = _RUNNING.get(slug)
    return _public(rec) if rec else None


def get_port(slug: str) -> int | None:
    rec = _RUNNING.get(slug)
    if not rec:
        return None
    rec["last_seen"] = time.time()
    proc = rec.get("_proc")
    if proc and proc.returncode is not None:
        return None
    return rec.get("port")


def logs(slug: str, *, tail: int = 200) -> list[str]:
    rec = _RUNNING.get(slug)
    if not rec:
        return []
    return list(rec["logs"])[-tail:]


def list_running() -> list[dict[str, Any]]:
    return [_public(r) for r in _RUNNING.values()]


_EXP_URL_RE = re.compile(r"exp://[^\s\"'<>]+")
_EXP_DIRECT_RE = re.compile(r"https://[a-z0-9-]+\.exp\.direct[^\s\"'<>]*", re.I)


def extract_expo_url(slug: str) -> str | None:
    """Parse exp:// or exp.direct URL from Metro tunnel logs."""
    text = "\n".join(logs(slug, tail=400))
    if not text:
        return None
    m = _EXP_URL_RE.search(text) or _EXP_DIRECT_RE.search(text)
    return m.group(0) if m else None


def _qr_url_for(exp_url: str) -> str:
    if not exp_url:
        return ""
    return (
        "https://api.qrserver.com/v1/create-qr-code/?size=220x220&data="
        + urllib.parse.quote(exp_url, safe="")
    )


async def _poll_expo_tunnel(slug: str, *, timeout: float = 120.0) -> None:
    """Background: watch tunnel logs until exp:// appears."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        rec = _RUNNING.get(slug)
        if not rec or not rec.get("tunnel"):
            return
        exp = extract_expo_url(slug)
        if exp:
            rec["expo_url"] = exp
            rec["qr_url"] = _qr_url_for(exp)
            log.info("Playground: expo tunnel URL for %s: %s", slug, exp[:80])
            return
        await asyncio.sleep(2.0)


def _public(rec: dict[str, Any]) -> dict[str, Any]:
    proc = rec.get("_proc")
    alive = bool(proc and proc.returncode is None)
    out: dict[str, Any] = {
        "slug": rec["slug"], "pid": rec["pid"], "port": rec["port"],
        "cmd": rec["cmd"], "status": "running" if alive else "stopped",
        "started_at": rec["started_at"], "uptime_sec": int(time.time() - rec["started_at"]),
        "log_lines": len(rec["logs"]),
        "tunnel": bool(rec.get("tunnel")),
    }
    exp = rec.get("expo_url") or extract_expo_url(rec["slug"])
    if exp:
        out["expo_url"] = exp
        out["qr_url"] = rec.get("qr_url") or _qr_url_for(exp)
    return out


_ERR_PATTERNS = (
    "Traceback (most recent call last)", "ModuleNotFoundError", "ImportError",
    "SyntaxError", "IndentationError", "NameError", "AttributeError",
    "TypeError", "ValueError", "KeyError", "RuntimeError", "Exception",
    "Error:", "error TS", "Cannot find module", "Cannot find package",
    "npm ERR", "ERR_MODULE_NOT_FOUND", "Failed to resolve", "Could not resolve",
    "failed to load config", "address already in use", "EADDRINUSE",
    "Uncaught", "uvicorn.error", "panic", "command not found",
)


def _extract_error_from_logs(lines: list[str]) -> str:
    """Pull the most diagnostic slice out of a log buffer for a repair prompt."""
    if not lines:
        return ""
    for i, ln in enumerate(lines):
        if "Traceback (most recent call last)" in ln:
            return "\n".join(lines[i:i + 40])
    hits = [ln for ln in lines if any(p.lower() in ln.lower() for p in _ERR_PATTERNS)]
    if hits:
        return "\n".join(hits[-25:])
    return "\n".join(lines[-30:])


async def _expo_export_check(root: Path) -> tuple[bool, str]:
    """NativeBot-style compile gate: ``expo export --platform web`` without dev server."""
    out_dir = root / ".playground-verify-export"
    shutil.rmtree(out_dir, ignore_errors=True)
    try:
        cmd = argv_for_command(
            "npx", "expo", "export", "--platform", "web",
            "--output-dir", str(out_dir),
            env=_child_env(0),
        )
        p = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(root), env=_child_env(0),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
            start_new_session=True,
        )
        try:
            out, _ = await asyncio.wait_for(p.communicate(), timeout=180)
        except asyncio.TimeoutError:
            await terminate_process_tree(p, timeout=12.0)
            return False, "expo export timed out (180s)"
        text = (out or b"").decode("utf-8", errors="replace")
        return p.returncode == 0, text
    except Exception as exc:
        return False, str(exc)
    finally:
        shutil.rmtree(out_dir, ignore_errors=True)


async def expo_compile_gate(slug: str) -> dict[str, Any]:
    """Install deps + expo export — used by smoke_test and autopilot verify."""
    proj = await pg.get_project(slug)
    if not proj or proj.get("kind") != "expo":
        return {"ok": True, "skipped": True, "install_ok": True, "logs": [], "error": ""}
    root = pg.project_dir(slug)
    install = await _install_deps(proj, root)
    log_lines: list[str] = []
    if install.get("output"):
        log_lines += [f"[install] {l}" for l in install["output"].splitlines()[-50:]]
    if not install.get("ok", True):
        return {
            "ok": False, "skipped": False, "install_ok": False,
            "logs": log_lines, "error": _extract_error_from_logs(log_lines),
            "export_ok": False,
        }
    export_ok, export_out = await _expo_export_check(root)
    log_lines += [f"[export] {l}" for l in export_out.splitlines()[-80:]]
    return {
        "ok": export_ok,
        "skipped": False,
        "install_ok": True,
        "export_ok": export_ok,
        "logs": log_lines,
        "error": "" if export_ok else _extract_error_from_logs(log_lines),
    }


async def _vite_build_check(root: Path) -> tuple[bool, str]:
    """Compile gate for vite/vite-react: `vite build` fails on import/syntax/TS
    errors — unlike the dev server, which happily serves a broken module graph.
    Sanitized env (allowlist) so generated code never sees production secrets.
    """
    try:
        cmd = argv_for_command("npx", "vite", "build", "--base", "./", env=_child_env(0))
        p = await asyncio.create_subprocess_exec(
            *cmd, cwd=str(root), env=_child_env(0),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
            start_new_session=True,
        )
        try:
            out, _ = await asyncio.wait_for(p.communicate(), timeout=180)
        except asyncio.TimeoutError:
            await terminate_process_tree(p, timeout=12.0)
            return False, "vite build timed out (180s)"
        return p.returncode == 0, (out or b"").decode("utf-8", errors="replace")
    except Exception as exc:
        return False, str(exc)


_STAGE_ASSET_SUFFIX = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".ogg", ".mp3", ".wav", ".json"}


def stage_web_assets(root: Path, dist: Path | None = None) -> int:
    """Copy playground ``assets/`` into ``dist/assets/`` so Chromium proof can load them.

    Vite hashes only *imported* files. Phaser ``this.load.image('x', 'assets/foo.png')``
    404s when proof serves ``dist/`` alone — five broken requests, dark frame.
    Recurse into ``sfx/`` and similar. Skip ``_*`` verify screenshots.
    Returns how many files were copied.
    """
    src = root / "assets"
    dest = (dist or root / "dist") / "assets"
    if not src.is_dir() or not (dest.parent).is_dir():
        return 0
    dest.mkdir(parents=True, exist_ok=True)
    n = 0
    for path in src.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(src)
        if any(part.startswith("_") for part in rel.parts):
            continue
        if path.suffix.lower() not in _STAGE_ASSET_SUFFIX:
            continue
        target = dest / rel
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
            n += 1
        except OSError:
            continue
    return n


async def vite_compile_gate(root: Path, *, slug: str = "", kind: str = "vite") -> dict[str, Any]:
    """npm install + vite build from a project directory (no Playground DB).

    Visual proof used to serve the source tree when ``dist/`` was missing.
    That skips the compile check: Phaser ``import`` fails in Chromium and
    the page still "renders" because a stub ``<canvas>`` is in index.html.
    """
    if not (root / "package.json").exists():
        return {"ok": True, "skipped": True, "install_ok": True, "logs": [], "error": ""}
    kind = (kind or "vite").strip().lower() or "vite"
    proj = {"kind": kind, "slug": slug or root.name}
    log_lines: list[str] = []
    install = await _install_deps(proj, root)
    if install.get("output"):
        log_lines += [f"[install] {l}" for l in str(install["output"]).splitlines()[-50:]]
    if not bool(install.get("ok", True)):
        err = _extract_error_from_logs(log_lines) or "npm install failed"
        return {
            "ok": False, "skipped": False, "install_ok": False,
            "logs": log_lines, "error": err,
        }
    build_ok, build_out = await _vite_build_check(root)
    log_lines += [f"[build] {l}" for l in build_out.splitlines()[-80:]]
    if build_ok:
        copied = stage_web_assets(root)
        if copied:
            log_lines.append(f"[assets] staged {copied} files into dist/assets/")
    return {
        "ok": build_ok,
        "skipped": False,
        "install_ok": True,
        "logs": log_lines,
        "error": "" if build_ok else (_extract_error_from_logs(log_lines) or "vite build failed"),
    }


async def smoke_test(slug: str, *, settle: float = 3.5) -> dict[str, Any]:
    """Actually verify the app builds/starts — the real signal the autopilot
    build loop was missing.

    • vite / vite-react → install + `vite build` compile gate (catches import/
      syntax errors the dev server would silently swallow).
    • fastapi / streamlit / node / expo → start the process, confirm it stays
      up (import-time crashes surface here), HTTP-probe key endpoints, then stop.

    Returns {ok, skipped, healthy, exited, returncode, install_ok, logs, error,
             http_ok, http_path}.
    Static kinds are skipped — they have no runtime (served directly in preview).
    """
    proj = await pg.get_project(slug)
    kind = proj.get("kind", "static") if proj else "static"
    if not proj or kind == "static":
        return {"ok": True, "skipped": True, "healthy": True, "exited": False,
                "install_ok": True, "logs": [], "error": ""}

    # ── Expo: compile gate (NativeBot self-heal pattern) ───────────────────
    if kind == "expo":
        gate = await expo_compile_gate(slug)
        if gate.get("skipped"):
            return {"ok": True, "skipped": True, "healthy": True, "exited": False,
                    "install_ok": True, "logs": [], "error": ""}
        ok = bool(gate.get("ok"))
        return {
            "ok": ok, "skipped": False, "healthy": ok, "exited": not ok,
            "returncode": 0 if ok else 1,
            "install_ok": bool(gate.get("install_ok", True)),
            "logs": gate.get("logs") or [],
            "error": gate.get("error") or "",
            "export_ok": gate.get("export_ok"),
        }

    # ── Unity: Editor batchmode required (scaffold is not a playtest) ─────
    if kind == "unity":
        from .engine_runners import run_unity
        return run_unity(slug)

    if kind == "godot":
        from .engine_runners import run_godot
        return run_godot(slug)

    # ── Vite kinds: compile gate (no long-lived dev server) ──────────────
    if kind in ("vite", "vite-react"):
        root = pg.project_dir(slug)
        install = await _install_deps(proj, root)
        log_lines: list[str] = []
        if install.get("output"):
            log_lines += [f"[install] {l}" for l in install["output"].splitlines()[-50:]]
        install_ok = bool(install.get("ok", True))
        if not install_ok:
            return {"ok": False, "skipped": False, "healthy": False, "exited": True,
                    "returncode": None, "install_ok": False, "logs": log_lines,
                    "error": _extract_error_from_logs(log_lines)}
        build_ok, build_out = await _vite_build_check(root)
        log_lines += [f"[build] {l}" for l in build_out.splitlines()[-80:]]
        return {"ok": build_ok, "skipped": False, "healthy": build_ok, "exited": not build_ok,
                "returncode": 0 if build_ok else 1, "install_ok": True, "logs": log_lines,
                "error": "" if build_ok else _extract_error_from_logs(log_lines)}

    # ── Process kinds: start, confirm alive, stop (guaranteed cleanup) ───
    started = False
    try:
        try:
            rec = await start(slug, install_deps=True)
            started = True
        except Exception as exc:
            return {"ok": False, "skipped": False, "healthy": False, "exited": True,
                    "returncode": None, "install_ok": False, "logs": [], "error": str(exc)}
        # Give an import-time / startup crash a moment to surface after the port wait.
        await asyncio.sleep(settle)
        r = _RUNNING.get(slug)
        proc = r.get("_proc") if r else None
        exited = bool(proc and proc.returncode is not None)
        returncode = proc.returncode if proc else None
        healthy = (rec.get("status") == "running") and not exited
        install = (r or {}).get("install") or {}
        install_ok = bool(install.get("ok", True))

        log_lines = logs(slug, tail=140)
        if install.get("output"):
            log_lines = (
                [f"[install] {l}" for l in install["output"].splitlines()[-50:]]
                + log_lines
            )

        port = rec.get("port") or (r or {}).get("port")
        http_ok, http_path, http_err = True, "", ""
        if port and healthy and not exited:
            paths, hints = PlaygroundRuntime.http_probe_paths(kind)
            if paths:
                http_ok, http_path, http_err = await PlaygroundRuntime.http_probe(
                    port, paths, body_hints=hints,
                )
            if http_ok and http_path:
                log_lines.append(f"[http] OK {http_path}")
            elif http_err:
                log_lines.append(f"[http] FAIL {http_err}")

        ok = healthy and not exited and install_ok and http_ok
        error = "" if ok else (http_err or _extract_error_from_logs(log_lines))
        return {"ok": ok, "skipped": False, "healthy": healthy, "exited": exited,
                "returncode": returncode, "install_ok": install_ok,
                "logs": log_lines, "error": error,
                "http_ok": http_ok, "http_path": http_path}
    finally:
        if started:
            try:
                await stop(slug)
            except Exception:
                pass


async def reap_idle() -> int:
    """Kill processes idle longer than IDLE_KILL_SEC. Returns count killed."""
    now = time.time()
    killed = 0
    for slug in list(_RUNNING.keys()):
        rec = _RUNNING.get(slug)
        if rec and now - rec.get("last_seen", now) > IDLE_KILL_SEC:
            await stop(slug)
            killed += 1
    return killed
