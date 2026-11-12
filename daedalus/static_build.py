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
import os
import re
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

SERVER_DEPS = frozenset({
    "express", "koa", "fastify", "@hapi/hapi", "hapi", "@nestjs/core", "nuxt", "@remix-run/node", "@remix-run/serve",
    "socket.io", "restify", "sails", "loopback", "@sveltejs/adapter-node", "@astrojs/node",
})
# Where common tools put their static output, most specific first. Not "public": for CRA/Vue it is the source
# template (an unbuilt index.html with %PUBLIC_URL%), never the output (review 2026-11-11).
OUTPUT_DIRS = ("dist", "build", "out", "docs", "_site", "www")
WEB_FRAMEWORKS = ("react-scripts", "vite", "@vue/cli-service", "parcel", "webpack", "svelte", "astro", "next",
                  "gatsby", "@angular/core")
_SKIP_DIRS = {"node_modules", ".git", ".next", ".cache", ".expo", *OUTPUT_DIRS}
_NEXT_EXPORT = re.compile(r"""output\s*:\s*['"]export['"]""")


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
    """next.config sets ``output: 'export'`` (a substring test matched every ``module.exports``: review 2026-11-11)."""
    for name in ("next.config.js", "next.config.mjs", "next.config.ts"):
        p = root / name
        if p.is_file() and _NEXT_EXPORT.search(p.read_text(encoding="utf-8", errors="replace")):
            return True
    return False


def _has_html_source(root: Path) -> bool:
    return any(next(d.glob("*.html"), None) for d in (root, root / "public", root / "src") if d.is_dir())


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
    framework = next((f for f in WEB_FRAMEWORKS if f in deps), "unknown")
    if framework == "unknown" and not _has_html_source(root):
        return None                                   # a CLI or library ("build": "tsc") is not a website
    manager = "pnpm" if (root / "pnpm-lock.yaml").is_file() else "yarn" if (root / "yarn.lock").is_file() else "npm"
    outputs = list(OUTPUT_DIRS)
    if framework == "react-scripts":
        outputs = ["build", *[o for o in outputs if o != "build"]]
    elif framework == "@angular/core":
        outputs = ["dist", *outputs]
    return {"script": "build", "manager": manager, "framework": framework, "outputs": outputs}


def find_output(root: Path, outputs: list[str], *, since: float = 0.0) -> Path | None:
    """The built folder that holds an index.html (Angular nests it: dist/<app>/[browser/]). With ``since``, only a
    folder written by this build counts, never a stale one committed to the repo."""
    for name in outputs:
        d = root / name
        cands = [d / "index.html", *sorted(d.glob("*/index.html")), *sorted(d.glob("*/browser/index.html"))] \
            if d.is_dir() else []
        for index in cands:
            if index.is_file() and index.stat().st_mtime >= since:
                return index.parent
    return None


def _newest_source(root: Path) -> float:
    newest = 0.0
    for base, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
        for f in files:
            try:
                newest = max(newest, os.stat(os.path.join(base, f)).st_mtime)
            except OSError:
                pass
    return newest


def fresh_output(root: Path) -> Path | None:
    """A built folder newer than every source file: this exact code is already built (no second npm install)."""
    p = plan(root)
    if not p:
        return None
    return find_output(root, p["outputs"], since=_newest_source(root))


async def build(root: Path, *, timeout_install: float = 600, timeout_build: float = 600) -> Path:
    """Install and build; returns the static output folder. Raises RuntimeError with the tool's own words."""
    from .playground_runtime import PlaygroundRuntime
    from .proc import argv_for_command, terminate_process_tree

    p = plan(root)
    if not p:
        raise RuntimeError("not a static-build project (no build script, or a server app)")
    if (ready := fresh_output(root)) is not None:
        return ready                                  # verify, publish and site publish share one build
    import time
    started = time.time() - 1.0

    async def run(*cmd: str, timeout: float) -> tuple[int, str]:
        env = PlaygroundRuntime.child_env(0)
        env.setdefault("PUBLIC_URL", ".")             # CRA: relative asset paths so /apps/<slug>/ works
        env["CI"] = "false"                           # child_env sets CI=1; CRA would treat warnings as errors
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
    found = find_output(root, p["outputs"], since=started)
    if not found:
        raise RuntimeError(f"build finished but no folder with index.html in {', '.join(p['outputs'])}:\n{out[-600:]}")
    return found
