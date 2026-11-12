"""Node web apps that build to static files (Create React App, Vue CLI, webpack, Parcel, Svelte, Next/Astro export).

Revived repos are often such apps. Before, they were kind ``node``: run with ``npm start`` and never publishable (or,
worse, published as raw source). ``plan(root)`` says whether a project builds to a static folder and how; ``build(root)``
runs it and returns the folder with ``index.html``. Server apps (express, koa, fastify, nest, next without export,
nuxt, remix, socket.io) have no static plan: they need a running server, not a static site.
"""
from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

SERVER_DEPS = frozenset({
    "express", "koa", "fastify", "@hapi/hapi", "hapi", "@nestjs/core", "nuxt", "@remix-run/node", "@remix-run/serve",
    "socket.io", "restify", "sails", "loopback", "@sveltejs/adapter-node", "@astrojs/node",
})
# Where common tools put their static output, most specific first.
OUTPUT_DIRS = ("dist", "build", "out", "public", "docs", "_site", "www")


def _package(root: Path) -> dict[str, Any] | None:
    try:
        data = json.loads((root / "package.json").read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def _deps(pkg: dict[str, Any]) -> set[str]:
    out: set[str] = set()
    for key in ("dependencies", "devDependencies"):
        if isinstance(pkg.get(key), dict):
            out |= set(pkg[key])
    return out


def _next_exports(root: Path) -> bool:
    for name in ("next.config.js", "next.config.mjs", "next.config.ts"):
        p = root / name
        if p.is_file():
            text = p.read_text(encoding="utf-8", errors="replace")
            if "output" in text and "export" in text:
                return True
    return False


def plan(root: Path) -> dict[str, Any] | None:
    """{script, manager, framework, outputs} when the project builds to static files; None otherwise."""
    pkg = _package(root)
    if not pkg:
        return None
    scripts = pkg.get("scripts") if isinstance(pkg.get("scripts"), dict) else {}
    if "build" not in scripts:
        return None
    deps = _deps(pkg)
    if deps & SERVER_DEPS:
        return None
    if "next" in deps and not _next_exports(root):
        return None                                   # a Next server app (SSR) is not a static site
    framework = next((f for f in ("react-scripts", "vite", "@vue/cli-service", "parcel", "webpack", "svelte",
                                  "astro", "next", "gatsby", "@angular/core") if f in deps), "unknown")
    manager = "pnpm" if (root / "pnpm-lock.yaml").is_file() else "yarn" if (root / "yarn.lock").is_file() else "npm"
    outputs = list(OUTPUT_DIRS)
    if framework == "react-scripts":
        outputs = ["build", *[o for o in outputs if o != "build"]]
    elif framework == "@angular/core":
        outputs = ["dist", *outputs]
    return {"script": "build", "manager": manager, "framework": framework, "outputs": outputs}


def find_output(root: Path, outputs: list[str]) -> Path | None:
    """The built folder that holds an index.html (Angular nests it one level: dist/<app>/)."""
    for name in outputs:
        d = root / name
        if (d / "index.html").is_file():
            return d
        if d.is_dir():
            nested = sorted(p.parent for p in d.glob("*/index.html"))
            if nested:
                return nested[0]
            nested = sorted(p.parent for p in d.glob("*/browser/index.html"))
            if nested:
                return nested[0]
    return None


async def build(root: Path, *, timeout_install: float = 600, timeout_build: float = 600) -> Path:
    """Install and build; returns the static output folder. Raises RuntimeError with the tool's own words."""
    from .playground_runtime import PlaygroundRuntime
    from .proc import argv_for_command, terminate_process_tree

    p = plan(root)
    if not p:
        raise RuntimeError("not a static-build project (no build script, or a server app)")

    async def run(*cmd: str, timeout: float) -> tuple[int, str]:
        env = PlaygroundRuntime.child_env(0)
        env.setdefault("PUBLIC_URL", ".")             # CRA: relative asset paths so /apps/<slug>/ works
        env.setdefault("CI", "false")                 # CRA treats warnings as errors when CI=true
        argv = argv_for_command(cmd[0], *cmd[1:], env=env)
        proc = await asyncio.create_subprocess_exec(*argv, cwd=str(root), env=env, stdout=asyncio.subprocess.PIPE,
                                                    stderr=asyncio.subprocess.STDOUT, start_new_session=True)
        try:
            raw, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            await terminate_process_tree(proc, timeout=12.0)
            return -1, f"timeout after {timeout}s"
        return (proc.returncode or 0), (raw or b"").decode("utf-8", errors="replace")

    manager = p["manager"]
    install = {"npm": ("npm", "install", "--no-audit", "--no-fund", "--legacy-peer-deps"),
               "yarn": ("npx", "--yes", "yarn", "install"), "pnpm": ("npx", "--yes", "pnpm", "install")}[manager]
    rc, out = await run(*install, timeout=timeout_install)
    if rc != 0:
        raise RuntimeError(f"{manager} install failed (rc={rc}):\n{out[-1500:]}")
    build_cmd = ("npm", "run", "build") if manager == "npm" else ("npx", "--yes", manager, "run", "build")
    rc, out = await run(*build_cmd, timeout=timeout_build)
    if rc != 0:
        raise RuntimeError(f"build failed (rc={rc}):\n{out[-1500:]}")
    found = find_output(root, p["outputs"])
    if not found:
        raise RuntimeError(f"build finished but no folder with index.html in {', '.join(p['outputs'])}:\n{out[-600:]}")
    return found
