"""Cross-platform process helpers — PATH resolution and sanitized child env.

Windows runners need ``npm.cmd`` / ``npx.cmd`` (PATHEXT), and Node's child
processes expect ``SYSTEMROOT``, ``TEMP``, ``COMSPEC``, etc. Subprocess code
should use ``resolve_exe`` + ``child_env_from_allowlist`` instead of bare
``["npm", ...]`` with a stripped environment.
"""
from __future__ import annotations

import asyncio
import os
import signal
import shutil
from collections.abc import Mapping
from pathlib import Path
from typing import Any

WINDOWS = os.name == "nt"

# Always propagate these on Windows (and harmless elsewhere) so npm/node work.
WIN_EXTRA_ENV_KEYS = frozenset(
    {
        "SYSTEMROOT",
        "WINDIR",
        "TEMP",
        "TMP",
        "APPDATA",
        "LOCALAPPDATA",
        "USERPROFILE",
        "PATHEXT",
        "COMSPEC",
        "ProgramFiles",
        "ProgramFiles(x86)",
        "ProgramW6432",
    }
)


def resolve_exe(
    name: str,
    *,
    env: Mapping[str, str] | None = None,
) -> Path | None:
    """Resolve a command name to an executable path (PATHEXT / Windows-aware).

    When ``name`` has no path separators, search ``PATH`` from ``env`` (or
    ``os.environ``). On Windows, append extensions from ``PATHEXT`` in order
    so ``npm`` → ``…/npm.cmd`` when that file exists first.
    """
    raw = (name or "").strip()
    if not raw:
        return None
    environ = dict(env) if env is not None else os.environ
    if os.path.sep in raw or (os.altsep and os.altsep in raw):
        p = Path(raw)
        try:
            p = p.expanduser()
        except Exception:
            pass
        if p.is_file():
            return p.resolve()
        return None

    path_var = environ.get("PATH", "") or environ.get("Path", "")
    path_dirs = [d.strip('"') for d in path_var.split(os.pathsep) if d.strip()]

    pathext_raw = environ.get("PATHEXT", "")
    use_pathext = WINDOWS or bool(pathext_raw.strip())
    if use_pathext:
        # Windows PATHEXT uses ';' even when PATH uses ':' (e.g. cross-env tests).
        _sep = ";" if ";" in pathext_raw else os.pathsep
        exts = [e.strip().lower() for e in pathext_raw.split(_sep) if e.strip()]
        if not exts:
            exts = [".exe", ".cmd", ".bat", ".com"] if WINDOWS else []
        base_names = [raw]
        if exts and not any(raw.lower().endswith(e) for e in exts):
            base_names = [raw + e for e in exts]
    else:
        base_names = [raw]

    for directory in path_dirs:
        try:
            base = Path(directory)
        except Exception:
            continue
        if not base.is_dir():
            continue
        for bn in base_names:
            cand = base / bn
            try:
                if not cand.is_file():
                    bn_cf = bn.casefold()
                    for p in base.iterdir():
                        if p.is_file() and p.name.casefold() == bn_cf:
                            cand = p
                            break
                if cand.is_file():
                    return cand.resolve()
            except OSError:
                continue

    # Fallback: stdlib which (may still find npm.cmd on Windows)
    w = shutil.which(raw, path=path_var or None)
    if w:
        return Path(w).resolve()
    return None


def argv_for_command(first: str, *rest: str, env: Mapping[str, str] | None = None) -> list[str]:
    """Build argv with a resolved executable as argv[0] when possible."""
    resolved = resolve_exe(first, env=env)
    if resolved is not None:
        return [str(resolved), *rest]
    return [first, *rest]


def argv_from_resolved_shlex(cmd: str, env: Mapping[str, str] | None = None) -> list[str]:
    """``shlex.split`` then resolve argv[0] for Windows PATHEXT / PATH."""
    import shlex

    try:
        parts = shlex.split(cmd or "", posix=os.name != "nt")
    except ValueError:
        parts = (cmd or "").split()
    if not parts:
        return []
    merged_env: dict[str, str] = dict(os.environ)
    if env is not None:
        merged_env.update(env)
    first = resolve_exe(parts[0], env=merged_env)
    if first is not None:
        return [str(first), *parts[1:]]
    return parts


def child_env_from_allowlist(
    allow: frozenset[str],
    port: int,
    *,
    base: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Copy allowed keys from ``base`` (default ``os.environ``), set PORT, merge Windows extras."""
    src = dict(os.environ) if base is None else dict(base)
    out: dict[str, str] = {}
    for k, v in src.items():
        if k in allow:
            out[k] = v
    if WINDOWS:
        for k in WIN_EXTRA_ENV_KEYS:
            if k in src and k not in out:
                out[k] = src[k]
    out["PORT"] = str(port)
    out["PYTHONUNBUFFERED"] = "1"
    return out


async def terminate_process_tree(proc: asyncio.subprocess.Process, *, timeout: float = 6.0) -> None:
    """SIGTERM process group on Unix (start_new_session); kill / fallback elsewhere."""
    if proc.returncode is not None:
        return
    pid = proc.pid
    try:
        if hasattr(os, "killpg"):
            try:
                pgid = os.getpgid(pid)
                os.killpg(pgid, signal.SIGTERM)
            except (ProcessLookupError, PermissionError, OSError):
                proc.terminate()
        else:
            proc.terminate()
        await asyncio.wait_for(proc.wait(), timeout=timeout)
    except asyncio.TimeoutError:
        try:
            if hasattr(os, "killpg"):
                try:
                    os.killpg(os.getpgid(pid), signal.SIGKILL)
                except Exception:
                    proc.kill()
            else:
                proc.kill()
        except Exception:
            proc.kill()
        try:
            await asyncio.wait_for(proc.wait(), timeout=3.0)
        except Exception:
            pass
    except ProcessLookupError:
        pass


def sync_terminate_process_tree(proc: Any, *, timeout: float = 6.0) -> None:
    """``subprocess.Popen`` tree kill: SIGTERM group then SIGKILL."""
    if proc.poll() is not None:
        return
    pid = proc.pid
    try:
        if hasattr(os, "killpg"):
            try:
                os.killpg(os.getpgid(pid), signal.SIGTERM)
            except Exception:
                proc.terminate()
        else:
            proc.terminate()
        proc.wait(timeout=timeout)
    except Exception:
        try:
            if hasattr(os, "killpg"):
                try:
                    os.killpg(os.getpgid(pid), signal.SIGKILL)
                except Exception:
                    proc.kill()
            else:
                proc.kill()
        except Exception:
            pass
