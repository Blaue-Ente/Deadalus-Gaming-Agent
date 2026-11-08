"""Settings -> kernel export and the upgrade-pool scan.

Both are outward or paid actions, so neither runs without a deliberate request, both answer codes (the Studio says
them in the owner's language), and neither can be pointed somewhere harmful from the browser:

* the kernel export is a fresh-history snapshot, so it only ever goes to its own branch (never main), never contains a
  secret file, and replaces an existing export branch only with an explicit ``force``;
* the upgrade-pool scan is one model-driven run at a time."""
from __future__ import annotations

import asyncio
import re
import time
import uuid
from pathlib import Path, PurePosixPath
from typing import Any

_KEY = "adn:daedalus_kernel_config"
_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}$")
_BRANCH_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,79}$")
# A branch spelled as a full ref ("refs/heads/main", "heads/main", "origin/main") can resolve to a protected branch, so
# these never pass; and the product's own state (data stores hold saved keys) is never exportable, whatever its file names.
_REF_PREFIXES = frozenset({"refs", "heads", "origin", "remotes", "tags"})
_BLOCKED_TOP = frozenset({".git", "data", "playground", "projects", "node_modules", ".venv", "venv", "secrets"})


class KernelConfigError(ValueError):
    def __init__(self, code: str, **extra: Any) -> None:
        super().__init__(code)
        self.code = code
        self.extra = extra


def _defaults() -> dict[str, Any]:
    from .. import playground_github as gh

    return {"repo": gh.DEFAULT_KERNEL_REPO, "branch": gh.DEFAULT_KERNEL_BRANCH, "paths": list(gh.DEFAULT_KERNEL_PATHS)}


def _saved() -> dict[str, Any]:
    from .. import kv

    saved = kv.get(_KEY) or {}
    return saved if isinstance(saved, dict) else {}


def kernel_config() -> dict[str, Any]:
    d, s = _defaults(), _saved()
    return {"repo": str(s.get("repo") or d["repo"]), "branch": str(s.get("branch") or d["branch"]),
            "paths": list(s.get("paths") or d["paths"])}


def validate(repo: str, branch: str, paths: Any) -> dict[str, Any]:
    from .. import playground_github as gh
    from ..paths import data_root, playground_projects_dir, repo_root
    from ..secret_paths import is_secret_path

    repo, branch = (repo or "").strip(), (branch or "").strip()
    if not _REPO_RE.match(repo):
        raise KernelConfigError("bad_repo")
    if not _BRANCH_RE.match(branch) or ".." in branch or branch.endswith(("/", ".lock")) or "//" in branch:
        raise KernelConfigError("bad_branch")
    if branch.split("/")[0].lower() in _REF_PREFIXES:
        raise KernelConfigError("bad_branch")
    if branch.lower() in gh.PROTECTED_KERNEL_BRANCHES:
        raise KernelConfigError("protected_branch", branch=branch)
    if not isinstance(paths, list) or not paths or len(paths) > 40:
        raise KernelConfigError("bad_paths")
    root = repo_root().resolve()
    clean: list[str] = []
    for raw in paths:
        rel = str(raw or "").strip().replace("\\", "/")
        pure = PurePosixPath(rel)
        if not rel or not pure.parts or pure.is_absolute() or ".." in pure.parts or rel.startswith("~") or ":" in rel[:3]:
            raise KernelConfigError("bad_paths", path=rel)
        if is_secret_path(rel):
            raise KernelConfigError("secret_path", path=rel)
        if pure.parts[0].lower() in _BLOCKED_TOP:
            raise KernelConfigError("protected_path", path=rel)
        target = (root / rel).resolve()
        for state in (data_root(), playground_projects_dir()):        # wherever the product keeps its own state
            try:
                state = state.resolve()
            except OSError:
                continue
            if target == state or state in target.parents or target in state.parents:
                raise KernelConfigError("protected_path", path=rel)
        if root not in target.parents and target != root:          # a symlink or odd path that leaves the product
            raise KernelConfigError("bad_paths", path=rel)
        if target == root:
            raise KernelConfigError("bad_paths", path=rel)
        clean.append(rel.rstrip("/"))
    return {"repo": repo, "branch": branch, "paths": list(dict.fromkeys(clean))}


def save_kernel_config(repo: str, branch: str, paths: Any) -> dict[str, Any]:
    from .. import kv

    cfg = validate(repo, branch, paths)
    kv.set(_KEY, cfg)
    return cfg


def kernel_view() -> dict[str, Any]:
    from .. import playground_github as gh
    from ..paths import repo_root

    cfg = kernel_config()
    root = repo_root()
    return {"ok": True, **cfg, "defaults": _defaults(), "token_configured": bool(gh.is_configured()),
            "missing_paths": [p for p in cfg["paths"] if not (root / p).exists()],
            "protected_branches": sorted(gh.PROTECTED_KERNEL_BRANCHES)}


async def kernel_push(*, confirm: bool, force: bool = False) -> dict[str, Any]:
    """Push the saved export. ``confirm`` must be true (the browser asks the owner first); ``force`` replaces an existing
    export branch and is a separate, explicit choice."""
    from .. import playground_github as gh
    from ..git_auth import NonFastForward

    if confirm is not True:
        return {"ok": False, "error": "confirm_required"}
    if not gh.is_configured():
        return {"ok": False, "error": "not_configured"}
    saved = kernel_config()
    try:
        cfg = validate(saved["repo"], saved["branch"], saved["paths"])
    except KernelConfigError as exc:                     # a config saved through the old page is re-checked here
        return {"ok": False, "error": exc.code, **exc.extra}
    try:
        res = await gh.push_kernel(repo_full_name=cfg["repo"], paths=cfg["paths"], branch=cfg["branch"], force=bool(force))
    except NonFastForward:
        return {"ok": False, "error": "non_fast_forward", "needs_force": True}
    except ValueError as exc:
        text = str(exc)
        return {"ok": False, "error": "protected_branch" if text.startswith("protected_branch") else "no_files"}
    except Exception:
        # a code and nothing else: the browser reads `detail` before `error`, and prose or exception names are not codes
        import logging

        logging.getLogger(__name__).exception("kernel push failed")
        return {"ok": False, "error": "push_failed"}
    return {"ok": True, "repo": res.get("repo"), "branch": cfg["branch"], "html_url": res.get("html_url"),
            "files": len(res.get("files_exported") or []), "forced": bool(force)}


# ---- upgrade pool --------------------------------------------------------------------------------------
_scan: dict[str, Any] = {"task": None, "session_id": "", "started": 0.0, "last": None}


def scan_status() -> dict[str, Any]:
    from ..upgrade_pool_scanner import get_adn_candidates

    task = _scan["task"]
    try:
        candidates = len(get_adn_candidates())
    except Exception:
        candidates = -1
    return {"ok": True, "candidates": candidates, "running": bool(task and not task.done()),
            "session_id": _scan["session_id"] if task and not task.done() else "", "last": _scan["last"]}


async def start_scan() -> dict[str, Any]:
    """One scan at a time (it makes model calls). It runs in the background; the result arrives in the Outbox."""
    from ..upgrade_pool_scanner import get_adn_candidates, run_upgrade_pool_scan

    task = _scan["task"]
    if task and not task.done():
        return {"ok": False, "error": "already_running", "session_id": _scan["session_id"]}
    candidates = get_adn_candidates()
    if not candidates:
        return {"ok": True, "started": False, "candidates": 0}
    from ..agent import get_daedalus

    agent = get_daedalus()
    session_id = f"bg-upgrade-{uuid.uuid4().hex[:8]}"

    async def run() -> None:
        started = time.time()
        try:
            res = await run_upgrade_pool_scan(agent, session_id=session_id)
            # the scanner reports its own failures in the result instead of raising: read them, never assume success
            ok = bool(res.get("ok")) if isinstance(res, dict) else True
            _scan["last"] = {"ok": ok, "finished_at": time.time(), "seconds": round(time.time() - started, 1)}
            if not ok:
                _scan["last"]["error"] = "scan_failed"
        except Exception as exc:                          # the scan itself reports through the Outbox; this is the state
            _scan["last"] = {"ok": False, "finished_at": time.time(), "error": type(exc).__name__}

    _scan["session_id"], _scan["started"] = session_id, time.time()
    _scan["task"] = asyncio.get_running_loop().create_task(run())     # kept: a dropped task can be garbage-collected mid-run
    return {"ok": True, "started": True, "candidates": len(candidates), "session_id": session_id}
