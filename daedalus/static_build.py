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


# Real stack frames only: "at fn (file:1:2)", "at file:1:2", "at async fn (…)", node internals. Indented prose
# ("  at least one entry …") and code-frame carets stay (review 2026-11-12).
_STACK_LINE = re.compile(r"^\s+at (?:async )?(?:\S+ \(.*:\d+:\d+\)|\S+:\d+:\d+|\S+ \(node:[^)]*\)|<anonymous>)\s*$",
                         re.M)
_OSSL_MARKS = ("ERR_OSSL_EVP_UNSUPPORTED", "digital envelope routines::unsupported", "error:0308010C")


def digest(out: str, limit: int = 1500) -> str:
    """The tool's own words without stack frames: a tail of "at …" lines hid the real error (live 2026-11-12, a CRA
    build said only "at NormalModule.js:452" while the cause, ERR_OSSL_EVP_UNSUPPORTED, sat above)."""
    text = _STACK_LINE.sub("", out or "")
    text = re.sub(r"\n{2,}", "\n", text).strip()
    return text[-limit:]


_WEBPACK4 = (("react-scripts", 5), ("webpack", 5), ("@vue/cli-service", 5))


def _major(spec: Any) -> int | None:
    m = re.search(r"(\d+)", str(spec or ""))
    return int(m.group(1)) if m else None


def needs_legacy_openssl(root: Path) -> bool:
    """webpack 4 era (react-scripts < 5, webpack < 5, Vue CLI < 5): on Node >= 17 it needs the legacy OpenSSL provider,
    for `npm start` as much as for a build (review 2026-11-12)."""
    pkg = _package(root)
    if not pkg:
        return False
    specs: dict[str, Any] = {}
    for key in ("dependencies", "devDependencies"):
        if isinstance(pkg.get(key), dict):
            specs.update(pkg[key])
    for name, below in _WEBPACK4:
        major = _major(specs.get(name)) if name in specs else None
        if major is not None and major < below:
            return True
    return False


_NODE_MAJOR: int | None = None


def node_major() -> int:
    global _NODE_MAJOR
    if _NODE_MAJOR is None:
        try:
            import subprocess
            out = subprocess.run(["node", "--version"], capture_output=True, text=True, timeout=10).stdout
            _NODE_MAJOR = _major(out) or 0
        except Exception:
            _NODE_MAJOR = 0
    return _NODE_MAJOR


def legacy_openssl_env(env: dict[str, str], root: Path) -> dict[str, str]:
    """Add --openssl-legacy-provider for a webpack-4-era project on Node >= 17 (older Node rejects the flag)."""
    if needs_legacy_openssl(root) and node_major() >= 17 and "--openssl-legacy-provider" not in env.get(
            "NODE_OPTIONS", ""):
        env["NODE_OPTIONS"] = (env.get("NODE_OPTIONS", "") + " --openssl-legacy-provider").strip()
    return env


def _relative_base_args(root: Path, p: dict[str, Any], manager: str) -> tuple[str, ...]:
    """Vite with relative assets (the site is served under /apps/<slug>/). Only for a plain ``vite build`` script:
    in a chained script the flag would reach the last command, not vite. npm needs ``--`` to pass it on; pnpm and
    yarn pass extra arguments themselves and would hand a literal ``--`` to vite (review 2026-11-12)."""
    if p.get("framework") != "vite":
        return ()
    script = str(((_package(root) or {}).get("scripts") or {}).get("build") or "").strip()
    if not script.startswith("vite build") or any(op in script for op in ("&&", "||", ";", "|")):
        return ()
    return ("--", "--base=./") if manager == "npm" else ("--base=./",)


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

    legacy_openssl = needs_legacy_openssl(root) and node_major() >= 17     # known webpack 4: no failed build first

    async def run(*cmd: str, timeout: float) -> tuple[int, str]:
        env = PlaygroundRuntime.child_env(0)
        if legacy_openssl:
            env["NODE_OPTIONS"] = (env.get("NODE_OPTIONS", "") + " --openssl-legacy-provider").strip()
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
        raise RuntimeError(f"{manager} install failed (rc={rc}):\n{digest(out)}")
    build_cmd = ("npm", "run", "build") if manager == "npm" else ("npx", "--yes", manager, "run", "build")
    build_cmd += _relative_base_args(root, p, manager)
    rc, out = await run(*build_cmd, timeout=timeout_build)
    if rc != 0 and any(m in out for m in _OSSL_MARKS):
        # webpack 4 (react-scripts <= 4, Vue CLI 4) on Node >= 17: a known runtime switch, not a code fix.
        legacy_openssl = True
        rc, out = await run(*build_cmd, timeout=timeout_build)
    if rc != 0:
        raise RuntimeError(f"build failed (rc={rc}):\n{digest(out)}")
    found = find_output(root, p["outputs"], since=started)
    if not found:
        raise RuntimeError(f"build finished but no folder with index.html in {', '.join(p['outputs'])}:\n{digest(out, 600)}")
    return found
