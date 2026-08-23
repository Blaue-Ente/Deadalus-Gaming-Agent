"""Safe command runner — allowlisted commands inside a project directory only."""
from __future__ import annotations

import asyncio
import shlex
from pathlib import Path
from typing import Any

ALLOWED_PREFIXES: tuple[str, ...] = (
    "pnpm install",
    "pnpm run typecheck",
    "pnpm run build",
    "pnpm run test",
    "pnpm test",
    "npm install",
    "npm run build",
    "npm run test",
    "npx vite build",
    "python -m pytest",
    "python -m uvicorn",
)


def is_allowed(command: str) -> bool:
    cmd = command.strip()
    return any(cmd.startswith(p) for p in ALLOWED_PREFIXES)


async def run_in_project(
    project_root: Path,
    command: str,
    *,
    timeout: float = 180.0,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Run an allowlisted shell command with cwd locked to project_root."""
    root = project_root.resolve()
    if not root.is_dir():
        return {"ok": False, "error": f"project root missing: {root}"}
    if not is_allowed(command):
        return {"ok": False, "error": f"command not allowed: {command!r}"}

    proc = await asyncio.create_subprocess_exec(
        *shlex.split(command),
        cwd=str(root),
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        start_new_session=True,
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        return {"ok": False, "error": f"timeout after {timeout}s", "output": ""}

    text = (out or b"").decode("utf-8", errors="replace")
    return {
        "ok": proc.returncode == 0,
        "returncode": proc.returncode,
        "output": text,
        "error": "" if proc.returncode == 0 else text[-2000:],
    }
