"""Unified Vite web build for proof, publish, and standalone paths.

Always runs ``vite build --base ./`` and stages playground ``assets/`` into
``dist/assets/`` so Phaser and other loaders resolve under ``/apps/<slug>/``.
"""
from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path
from typing import Iterable

from .playground_runtime import PlaygroundRuntime
from .proc import argv_for_command, terminate_process_tree

log = logging.getLogger(__name__)

_ASSET_STAGE_SUFFIX = frozenset({
    ".png", ".jpg", ".jpeg", ".webp", ".gif", ".ogg", ".mp3", ".wav", ".json",
})

# References that break when the app is mounted under ``/apps/<slug>/``.
_ABS_ASSET_PATTERNS = (
    re.compile(r'(["\'])/assets/'),
    re.compile(r"url\(\s*/assets/"),
)


def stage_web_assets(root: Path, dist: Path | None = None) -> int:
    """Copy playground ``assets/`` into ``dist/assets/`` for proof/publish.

    Vite hashes only *imported* files. Phaser ``this.load.image('x', 'assets/foo.png')``
    404s when ``dist/`` is served alone without a copy of ``assets/``.
    Skip ``_*`` verify screenshots. Returns how many files were copied.
    """
    src = root / "assets"
    dest = (dist or root / "dist") / "assets"
    if not src.is_dir() or not (dest.parent).is_dir():
        return 0
    dest.mkdir(parents=True, exist_ok=True)
    import shutil

    n = 0
    for path in src.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(src)
        if any(part.startswith("_") for part in rel.parts):
            continue
        if path.suffix.lower() not in _ASSET_STAGE_SUFFIX:
            continue
        target = dest / rel
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
            n += 1
        except OSError:
            continue
    return n


def scan_absolute_asset_refs(dist: Path) -> list[str]:
    """Return human-readable hits for root-absolute ``/assets/`` references."""
    hits: list[str] = []
    if not dist.is_dir():
        return hits
    for p in _dist_text_files(dist):
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line_no, line in enumerate(text.splitlines(), 1):
            if "/assets/" in line and any(
                pat.search(line) for pat in _ABS_ASSET_PATTERNS
            ):
                hits.append(f"{p.relative_to(dist)}:{line_no}:{line.strip()[:120]}")
    return hits


def rewrite_absolute_assets_in_dist(dist: Path) -> int:
    """Rewrite ``\"/assets/\"`` → ``\"./assets/\"`` in built files. Returns files changed."""
    changed = 0
    for p in _dist_text_files(dist):
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        new = text
        for pat in _ABS_ASSET_PATTERNS:
            new = pat.sub(lambda m: m.group(0).replace("/assets/", "./assets/"), new)
        if new != text:
            try:
                p.write_text(new, encoding="utf-8")
                changed += 1
            except OSError:
                continue
    return changed


def _dist_text_files(dist: Path) -> Iterable[Path]:
    for p in dist.rglob("*"):
        if not p.is_file():
            continue
        if p.suffix.lower() in {".html", ".js", ".css", ".svg", ".json", ".map"}:
            yield p


async def build_web(
    root: Path,
    *,
    timeout_install: float = 120,
    timeout_build: float = 180,
    skip_npm_install: bool = False,
) -> Path:
    """``npm install`` + ``vite build --base ./`` → non-empty ``dist/``.

    Raises ``RuntimeError`` on failure (same contract as publish).
    """
    async def _run(*cmd: str, timeout: float = 180) -> tuple[int, str]:
        env = PlaygroundRuntime.child_env(0)
        argv = argv_for_command(cmd[0], *cmd[1:], env=env) if cmd else []
        proc = await asyncio.create_subprocess_exec(
            *argv, cwd=str(root), env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            start_new_session=True,
        )
        try:
            raw, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            await terminate_process_tree(proc, timeout=12.0)
            return -1, f"timeout after {timeout}s"
        text = raw.decode("utf-8", errors="replace") if raw else ""
        return (proc.returncode or 0), text

    pkg = root / "package.json"
    if not pkg.exists():
        raise RuntimeError("no package.json found — is this a vite project?")

    if not skip_npm_install:
        log.info("build_web: npm install in %s", root)
        rc, out = await _run("npm", "install", "--no-audit", "--no-fund", timeout=timeout_install)
        if rc != 0:
            raise RuntimeError(f"npm install failed (rc={rc}):\n{out[-2000:]}")

    log.info("build_web: vite build --base ./ in %s", root)
    rc, out = await _run("npx", "--yes", "vite", "build", "--base", "./", timeout=timeout_build)
    if rc != 0:
        raise RuntimeError(f"vite build failed (rc={rc}):\n{out[-2000:]}")

    dist = root / "dist"
    if not dist.exists() or not any(dist.iterdir()):
        raise RuntimeError(
            f"vite build exited 0 but dist/ is missing/empty.\nOutput:\n{out[-1000:]}"
        )

    copied = stage_web_assets(root, dist)
    if copied:
        log.info("build_web: staged %d asset files into dist/assets/", copied)
    rewrite_absolute_assets_in_dist(dist)
    bad = scan_absolute_asset_refs(dist)
    if bad:
        log.warning("build_web: absolute /assets/ refs after rewrite — %s", bad[:5])

    log.info(
        "build_web: dist/ ready (%d files)",
        sum(1 for _ in dist.rglob("*") if _.is_file()),
    )
    return dist


def build_web_sync(
    root: Path,
    *,
    timeout_install: int = 300,
    timeout_build: int = 180,
    skip_npm_install: bool = False,
) -> Path:
    """Synchronous ``build_web`` for non-async callers (standalone kernel)."""
    import subprocess

    if not (root / "package.json").exists():
        raise RuntimeError("no package.json")

    env = PlaygroundRuntime.child_env(0)
    if not skip_npm_install:
        r = subprocess.run(
            argv_for_command("npm", "install", "--no-audit", "--no-fund", env=env),
            cwd=root, capture_output=True, text=True, timeout=timeout_install, env=env,
        )
        if r.returncode != 0:
            raise RuntimeError(f"npm install failed: {(r.stdout or '')[-1500:]}")

    r2 = subprocess.run(
        argv_for_command("npx", "--yes", "vite", "build", "--base", "./", env=env),
        cwd=root, capture_output=True, text=True, timeout=timeout_build, env=env,
    )
    out = (r2.stdout or "") + (r2.stderr or "")
    if r2.returncode != 0:
        raise RuntimeError(f"vite build failed: {out[-2000:]}")

    dist = root / "dist"
    if not dist.exists() or not any(dist.iterdir()):
        raise RuntimeError("dist/ missing after build")

    stage_web_assets(root, dist)
    rewrite_absolute_assets_in_dist(dist)
    return dist
