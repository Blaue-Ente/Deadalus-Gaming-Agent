"""Portable playground runtime for standalone Workshop (no ADN imports)."""
from __future__ import annotations

import asyncio
import os
import shlex
import signal
import socket
import subprocess
import sys
from pathlib import Path
from typing import Any

VISUAL_KINDS = ("static", "vite", "vite-react", "streamlit", "expo")

_ENV_ALLOW = {
    "PATH", "HOME", "LANG", "LC_ALL", "TERM", "TZ", "TMPDIR", "USER",
    "PYTHONUNBUFFERED", "PYTHONDONTWRITEBYTECODE",
    "NODE_ENV", "NPM_CONFIG_CACHE",
    "XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_DATA_HOME",
}

HTTP_PROBE_CONFIG: dict[str, tuple[list[str], tuple[str, ...]]] = {
    "fastapi": (["/docs", "/openapi.json", "/"], ("swagger", "openapi", "fastapi")),
    "streamlit": (["/"], ("streamlit", "stapp")),
    "node": (["/health", "/api/health", "/"], ()),
    "expo": (["/", "/index.bundle"], ("expo", "react", "bundle", "root")),
}


def child_env(port: int) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k in _ENV_ALLOW}
    env["PORT"] = str(port)
    env["PYTHONUNBUFFERED"] = "1"
    home = env.get("HOME", os.path.expanduser("~"))
    env.setdefault("XDG_CONFIG_HOME", f"{home}/.config")
    env.setdefault("XDG_CACHE_HOME", f"{home}/.cache")
    return env


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def resolve_cmd(kind: str, root: Path, port: int) -> str:
    if kind == "fastapi":
        main = "main"
        for cand in ("main", "app", "server"):
            if (root / f"{cand}.py").exists():
                main = cand
                break
        return f"{sys.executable} -m uvicorn {main}:app --host 127.0.0.1 --port {port}"
    if kind == "streamlit":
        target = "streamlit_app.py"
        for cand in ("streamlit_app.py", "app.py", "main.py"):
            if (root / cand).exists():
                target = cand
                break
        return (
            f"{sys.executable} -m streamlit run {target} --server.headless true "
            f"--server.port {port} --server.address 127.0.0.1 "
            f"--browser.gatherUsageStats false"
        )
    if kind in ("vite", "vite-react"):
        return f"npx --yes vite --host 127.0.0.1 --port {port} --base ./"
    if kind == "node":
        for cand in ("index.js", "server.js", "app.js", "main.js"):
            if (root / cand).exists():
                return f"node {cand}"
        return "node index.js"
    if kind == "expo":
        return f"npx expo start --web --port {port} --non-interactive --no-dev-client"
    return ""


async def http_probe(
    port: int,
    paths: list[str],
    *,
    body_hints: tuple[str, ...] = (),
    timeout: float = 8.0,
) -> tuple[bool, str, str]:
    import httpx

    errors: list[str] = []
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as cx:
        for path in paths:
            try:
                r = await cx.get(f"http://127.0.0.1:{port}{path}")
                body = (r.text or "")[:8000]
                if r.status_code >= 500:
                    errors.append(f"{path}: HTTP {r.status_code}")
                    continue
                if body_hints and not any(h.lower() in body.lower() for h in body_hints):
                    errors.append(f"{path}: missing expected content")
                    continue
                return True, path, ""
            except Exception as exc:
                errors.append(f"{path}: {exc}")
    return False, "", "; ".join(errors[:3])


def _vite_build(root: Path) -> tuple[bool, list[str]]:
    logs: list[str] = []
    if not (root / "package.json").exists():
        return False, ["no package.json"]
    if not (root / "node_modules").is_dir():
        r = subprocess.run(
            ["npm", "install", "--prefer-offline", "--no-audit"],
            cwd=root, capture_output=True, text=True, timeout=300,
            env=child_env(0),
        )
        logs.extend((r.stdout or "").splitlines()[-15:])
        if r.returncode != 0:
            return False, logs + ["npm install failed"]
    r = subprocess.run(
        ["npx", "vite", "build", "--base", "./"],
        cwd=root, capture_output=True, text=True, timeout=180,
        env=child_env(0),
    )
    logs.extend((r.stdout or r.stderr or "").splitlines()[-30:])
    return r.returncode == 0, logs


async def smoke_test(root: Path, kind: str) -> dict[str, Any]:
    """Compile/start gate for standalone workspaces."""
    logs: list[str] = []
    if not root.is_dir():
        return {"ok": False, "error": "project missing", "logs": logs}

    if kind == "static":
        ok = (root / "index.html").exists()
        return {"ok": ok, "error": "" if ok else "index.html missing", "logs": logs}

    if kind in ("vite", "vite-react"):
        ok, build_logs = await asyncio.get_event_loop().run_in_executor(None, _vite_build, root)
        logs.extend(build_logs)
        return {"ok": ok, "error": "" if ok else "vite build failed", "logs": logs}

    if kind in ("fastapi", "streamlit", "node", "expo"):
        port = _free_port()
        cmd = resolve_cmd(kind, root, port)
        if not cmd:
            return {"ok": False, "error": f"no command for {kind}", "logs": logs}
        proc = await asyncio.create_subprocess_exec(
            *shlex.split(cmd), cwd=str(root), env=child_env(port),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
            start_new_session=True,
        )
        try:
            await asyncio.sleep(4.0 if kind == "expo" else 2.5)
            if proc.returncode is not None:
                out, _ = await proc.communicate()
                logs.extend((out or b"").decode(errors="replace").splitlines()[-40:])
                return {"ok": False, "error": "process exited on start", "logs": logs}
            paths, hints = HTTP_PROBE_CONFIG.get(kind, ([], ()))
            http_ok, hit, http_err = (
                await http_probe(port, paths, body_hints=hints) if paths else (True, "", "")
            )
            if hit:
                logs.append(f"[http] OK {hit}")
            return {"ok": http_ok, "error": http_err, "logs": logs, "http_ok": http_ok}
        finally:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            except Exception:
                proc.kill()
            try:
                await asyncio.wait_for(proc.wait(), timeout=5.0)
            except Exception:
                pass

    if (root / "index.html").exists():
        return {"ok": True, "error": "", "logs": ["static fallback ok"]}
    return {"ok": False, "error": "no buildable artifacts", "logs": logs}


def preview_path_for(root: Path, kind: str) -> str:
    """Relative preview path under /generated/{slug}/."""
    dist = root / "dist" / "index.html"
    if dist.exists():
        return "dist/index.html"
    if (root / "index.html").exists():
        return "index.html"
    return "index.html"
