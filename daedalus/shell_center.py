"""Sandboxed shell for the Studio command center and native ``run_command`` tool.

Commands run in a cwd jail (product / playground / data). Secrets are stripped
from the child environment. Destructive host-wide patterns are refused.
"""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any

from .paths import data_root, playground_projects_dir, product_root

log = logging.getLogger(__name__)

DEFAULT_TIMEOUT_S = 30
MAX_TIMEOUT_S = 180
MAX_OUTPUT_CHARS = 24_000
HISTORY_LIMIT = 200

_BLOCKED = (
    re.compile(r"\bsudo\b", re.I),
    re.compile(r"\bsu\b"),
    re.compile(r"\b(shutdown|reboot|halt|poweroff)\b", re.I),
    re.compile(r"\bmkfs\b", re.I),
    re.compile(r"\bfdisk\b", re.I),
    re.compile(r":\(\)\s*\{"),
    re.compile(r"\brm\s+(-[a-zA-Z]*f[a-zA-Z]*\s+)*(/|~|/home|/etc|/usr|/var)\b"),
    re.compile(r"\bdd\s+.*\bof=/dev/"),
    re.compile(r"\bchmod\s+.*\s/\s*$"),
    re.compile(r"\bkill\s+-9\s+1\b"),
    re.compile(r"\bcrontab\s+-r\b"),
)

_SECRET_ENV = re.compile(r"(API_KEY|SECRET|TOKEN|PASSWORD|PRIVATE_KEY|CREDENTIAL)$", re.I)

_SAFE_ENV = {
    "PATH", "HOME", "LANG", "LC_ALL", "TERM", "TZ", "USER", "TMPDIR",
    "PYTHONUNBUFFERED", "PYTHONDONTWRITEBYTECODE", "VIRTUAL_ENV",
    "NODE_ENV", "NPM_CONFIG_CACHE", "npm_config_cache",
    "XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_DATA_HOME",
    "SHELL", "PWD",
}


def _history_path() -> Path:
    p = data_root() / "shell_history.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def jail_roots() -> list[Path]:
    roots = [product_root().resolve(), playground_projects_dir().resolve(), data_root().resolve()]
    extra = (os.environ.get("DAEDALUS_SHELL_ROOT") or "").strip()
    if extra:
        roots.append(Path(extra).resolve())
    # unique while preserving order
    seen: set[Path] = set()
    out: list[Path] = []
    for r in roots:
        if r not in seen:
            seen.add(r)
            out.append(r)
    return out


def resolve_cwd(cwd: str | None = None, *, slug: str = "") -> Path:
    if slug:
        root = (playground_projects_dir() / slug).resolve()
        if root.is_dir():
            return root
    if cwd:
        cand = Path(cwd).expanduser()
        if not cand.is_absolute():
            cand = (product_root() / cand).resolve()
        else:
            cand = cand.resolve()
        for root in jail_roots():
            try:
                cand.relative_to(root)
                return cand if cand.is_dir() else root
            except ValueError:
                continue
        raise ValueError(f"cwd outside jail: {cwd}")
    return product_root()


def _blocked(command: str) -> str | None:
    text = command.strip()
    if not text:
        return "empty command"
    for pat in _BLOCKED:
        if pat.search(text):
            return f"blocked by safety policy ({pat.pattern})"
    return None


def _child_env() -> dict[str, str]:
    pass_secrets = (os.environ.get("DAEDALUS_SHELL_PASS_SECRETS") or "").strip().lower() in (
        "1", "true", "yes", "on",
    )
    env: dict[str, str] = {}
    for key, val in os.environ.items():
        if key in _SAFE_ENV:
            env[key] = val
            continue
        if pass_secrets:
            env[key] = val
            continue
        if _SECRET_ENV.search(key):
            continue
        if key.startswith("DAEDALUS_") and "KEY" not in key.upper():
            env[key] = val
    env.setdefault("PYTHONUNBUFFERED", "1")
    env["DAEDALUS_SHELL"] = "1"
    return env


def _append_history(row: dict[str, Any]) -> None:
    path = _history_path()
    try:
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception as exc:
        log.debug("shell history write failed: %s", exc)


def history(limit: int = 40) -> list[dict[str, Any]]:
    path = _history_path()
    if not path.is_file():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except Exception:
        return []
    rows: list[dict[str, Any]] = []
    for line in lines[-max(1, min(limit, HISTORY_LIMIT)):]:
        try:
            rows.append(json.loads(line))
        except Exception:
            continue
    return rows


def run_command(
    command: str,
    *,
    cwd: str | None = None,
    slug: str = "",
    timeout: int | None = None,
) -> dict[str, Any]:
    """Run ``command`` via the system shell inside the cwd jail."""
    t0 = time.perf_counter()
    reason = _blocked(command)
    if reason:
        return {
            "ok": False,
            "blocked": True,
            "error": reason,
            "command": command,
            "stdout": "",
            "stderr": "",
            "exit_code": 126,
            "cwd": "",
            "elapsed_ms": 0,
        }
    try:
        workdir = resolve_cwd(cwd, slug=slug)
    except ValueError as exc:
        return {
            "ok": False,
            "blocked": True,
            "error": str(exc),
            "command": command,
            "stdout": "",
            "stderr": "",
            "exit_code": 126,
            "cwd": cwd or "",
            "elapsed_ms": 0,
        }

    timeout_s = DEFAULT_TIMEOUT_S if timeout is None else int(timeout)
    timeout_s = max(1, min(timeout_s, MAX_TIMEOUT_S))
    try:
        proc = subprocess.run(
            command,
            shell=True,
            cwd=str(workdir),
            env=_child_env(),
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
        stdout = (proc.stdout or "")[:MAX_OUTPUT_CHARS]
        stderr = (proc.stderr or "")[:MAX_OUTPUT_CHARS]
        row = {
            "ok": proc.returncode == 0,
            "blocked": False,
            "error": "" if proc.returncode == 0 else f"exit {proc.returncode}",
            "command": command,
            "stdout": stdout,
            "stderr": stderr,
            "exit_code": proc.returncode,
            "cwd": str(workdir),
            "elapsed_ms": int((time.perf_counter() - t0) * 1000),
        }
    except subprocess.TimeoutExpired:
        row = {
            "ok": False,
            "blocked": False,
            "error": f"timeout after {timeout_s}s",
            "command": command,
            "stdout": "",
            "stderr": "",
            "exit_code": 124,
            "cwd": str(workdir),
            "elapsed_ms": int((time.perf_counter() - t0) * 1000),
        }
    except Exception as exc:
        row = {
            "ok": False,
            "blocked": False,
            "error": str(exc)[:300],
            "command": command,
            "stdout": "",
            "stderr": "",
            "exit_code": 1,
            "cwd": str(workdir),
            "elapsed_ms": int((time.perf_counter() - t0) * 1000),
        }
    _append_history({
        "ts": time.time(),
        "command": command[:500],
        "cwd": row.get("cwd"),
        "ok": row.get("ok"),
        "exit_code": row.get("exit_code"),
        "elapsed_ms": row.get("elapsed_ms"),
    })
    return row
