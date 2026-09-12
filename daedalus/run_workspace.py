"""Git worktree per coding run.

Leaf module. Do not import agent.py, native_tools, job_ledger, run_engine,
or scratch_journal. Persistence for the handle is ``meta.json`` under
``$DAEDALUS_DATA/worktrees/{run_id}/``. Callers may copy
``checkpoint_block`` into the untyped run_engine checkpoint; Run / RunCheckpoint
schema v1 is unchanged.

``DAEDALUS_WORKTREE`` default off: open/close are identity, no git mutation.
``bind`` / ``current_root`` exist so a later ticket can redirect I/O.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from contextlib import contextmanager
from contextvars import ContextVar, Token
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from daedalus.contracts.workspace import DirtyBaseline, RunWorkspace
from daedalus.paths import data_root

GIT_TIMEOUT_S = 30
_BRANCH_PREFIX = "daedalus/run-"
_current: ContextVar[RunWorkspace | None] = ContextVar(
    "daedalus_run_workspace", default=None,
)


class RunWorkspaceError(Exception):
    """Missing git, not a repo, unknown SHA, or a worktree that cannot be used."""


def worktree_enabled() -> bool:
    flag = (os.environ.get("DAEDALUS_WORKTREE") or "").strip().lower()
    return flag in ("1", "true", "yes", "on")


def worktrees_dir() -> Path:
    path = data_root() / "worktrees"
    path.mkdir(parents=True, exist_ok=True)
    return path


def snapshot_dirty(repo: Path) -> DirtyBaseline:
    """Read the main checkout. Does not create a worktree."""
    root = _as_repo(repo)
    _require_git()
    if not _is_git_repo(root):
        raise RunWorkspaceError(f"not a git repository: {root}")
    head = _git("rev-parse", "HEAD", cwd=root).strip()
    porcelain = _git("status", "--porcelain=v1", cwd=root)
    return DirtyBaseline(
        head_sha=head,
        porcelain=porcelain,
        dirty=bool(porcelain.strip()),
    )


def open_workspace(
    run_id: str,
    repo: Path | str,
    *,
    sha: str = "",
    slug: str = "",
) -> RunWorkspace:
    """Create (or reuse) a worktree at ``sha`` when the flag is on."""
    safe = _safe_run_id(run_id)
    root = _as_repo(repo)
    if not worktree_enabled():
        dirty = _dirty_or_empty(root)
        return RunWorkspace(
            run_id=run_id or safe,
            repo=str(root),
            worktree=str(root),
            base_sha=(dirty.head_sha if dirty else ""),
            branch="",
            enabled=False,
            slug=slug or "",
            dirty=dirty,
        )

    _require_git()
    if not _is_git_repo(root):
        raise RunWorkspaceError(f"not a git repository: {root}")
    base_sha = _resolve_sha(root, sha)
    dirty = snapshot_dirty(root)
    branch = _BRANCH_PREFIX + safe
    dest = worktrees_dir() / safe / "tree"

    if dest.exists():
        existing = _load_meta(safe)
        if (
            existing is not None
            and existing.enabled
            and existing.base_sha == base_sha
            and existing.run_id == (run_id or safe)
            and _is_worktree(dest)
        ):
            return existing
        raise RunWorkspaceError(
            f"worktree path exists for a different SHA or run: {dest}",
        )

    dest.parent.mkdir(parents=True, exist_ok=True)
    _add_worktree(root, dest, branch, base_sha)
    ws = RunWorkspace(
        run_id=run_id or safe,
        repo=str(root.resolve()),
        worktree=str(dest.resolve()),
        base_sha=base_sha,
        branch=branch,
        enabled=True,
        slug=slug or "",
        dirty=dirty,
    )
    _write_meta(safe, ws)
    return ws


def reopen_workspace(run_id: str) -> RunWorkspace | None:
    """Resume an enabled worktree. Missing meta is None; stale tree fails closed."""
    if not worktree_enabled():
        return None
    safe = _safe_run_id(run_id)
    meta_path = _meta_path(safe)
    if not meta_path.is_file():
        return None
    ws = _load_meta(safe)
    if ws is None:
        return None
    tree = Path(ws.worktree)
    if not _is_worktree(tree):
        raise RunWorkspaceError(f"worktree missing or invalid: {tree}")
    return ws


def export_patch(ws: RunWorkspace) -> str:
    """Diff of the worktree versus ``base_sha``, including new files."""
    if not ws.enabled:
        return ""
    tree = Path(ws.worktree)
    if not _is_worktree(tree):
        raise RunWorkspaceError(f"worktree missing or invalid: {tree}")
    _git("add", "-A", cwd=tree)
    try:
        diff = _git("diff", "--cached", "--binary", ws.base_sha, cwd=tree)
    finally:
        _git("reset", "-q", cwd=tree)
    patch_path = Path(ws.worktree).parent / "patch.diff"
    patch_path.write_text(diff, encoding="utf-8")
    return diff


def commit_workspace(ws: RunWorkspace, message: str) -> str:
    """Commit every change in the worktree. Does not push. Does not move main."""
    if not ws.enabled:
        raise RunWorkspaceError("worktrees disabled")
    tree = Path(ws.worktree)
    if not _is_worktree(tree):
        raise RunWorkspaceError(f"worktree missing or invalid: {tree}")
    msg = (message or "").strip() or "Daedalus run"
    _git("add", "-A", cwd=tree)
    try:
        _git(
            "-c", "user.name=Daedalus",
            "-c", "user.email=daedalus@local",
            "commit", "-m", msg,
            cwd=tree,
        )
    except RunWorkspaceError as exc:
        text = str(exc).lower()
        if "nothing to commit" in text:
            raise RunWorkspaceError("nothing to commit") from exc
        raise
    return _git("rev-parse", "HEAD", cwd=tree).strip()


def close_workspace(ws: RunWorkspace, *, keep: bool = False) -> None:
    """Remove the worktree (and branch) without touching the main checkout."""
    if not ws.enabled:
        return
    repo = Path(ws.repo)
    tree = Path(ws.worktree)
    if _is_git_repo(repo) or _is_worktree(repo):
        try:
            _git("worktree", "remove", "--force", str(tree), cwd=repo)
        except RunWorkspaceError:
            pass
        try:
            _git("worktree", "prune", cwd=repo)
        except RunWorkspaceError:
            pass
        if not keep and ws.branch:
            try:
                _git("branch", "-D", ws.branch, cwd=repo)
            except RunWorkspaceError:
                pass
    if keep:
        return
    run_dir = tree.parent if tree.name == "tree" else tree
    if run_dir.is_dir() and _owned_run_dir(run_dir):
        shutil.rmtree(run_dir, ignore_errors=True)


def journal_note(ws: RunWorkspace) -> str:
    """Compact line for ``scratch_journal.write_step`` (8000-char data cap)."""
    dirty = bool(ws.dirty and ws.dirty.dirty)
    sha = (ws.base_sha or "")[:12]
    note = (
        f"worktree run={ws.run_id} enabled={int(ws.enabled)} "
        f"sha={sha} dirty={int(dirty)} branch={ws.branch} tree={ws.worktree}"
    )
    return note[:8000]


def checkpoint_block(ws: RunWorkspace) -> dict[str, Any]:
    """Untyped payload for ``run_engine`` checkpoint['workspace']. Not RunCheckpoint."""
    dirty: dict[str, Any] | None = None
    if ws.dirty is not None:
        dirty = ws.dirty.model_dump(mode="json")
    return {
        "run_id": ws.run_id,
        "repo": ws.repo,
        "worktree": ws.worktree,
        "base_sha": ws.base_sha,
        "branch": ws.branch,
        "enabled": ws.enabled,
        "slug": ws.slug,
        "dirty": dirty,
    }


@contextmanager
def bind(ws: RunWorkspace) -> Iterator[RunWorkspace]:
    token: Token[RunWorkspace | None] = _current.set(ws)
    try:
        yield ws
    finally:
        _current.reset(token)


def current() -> RunWorkspace | None:
    return _current.get()


def current_root() -> Path | None:
    """Worktree path when an *enabled* workspace is bound. Else None."""
    ws = current()
    if ws is None or not ws.enabled:
        return None
    return Path(ws.worktree)


def _safe_run_id(run_id: str) -> str:
    cleaned = "".join(
        c if c.isalnum() or c in "-_" else "-" for c in (run_id or "")[:80]
    )
    return cleaned or "run"


def _as_repo(repo: Path | str) -> Path:
    return Path(repo).expanduser().resolve()


def _require_git() -> None:
    if shutil.which("git") is None:
        raise RunWorkspaceError("git CLI missing")


def _git(*args: str, cwd: Path, timeout: int = GIT_TIMEOUT_S) -> str:
    cmd = ["git", "--no-optional-locks", *args]
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError as exc:
        raise RunWorkspaceError("git CLI missing") from exc
    except subprocess.TimeoutExpired as exc:
        raise RunWorkspaceError(f"git timeout after {timeout}s") from exc
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()
        raise RunWorkspaceError(err[:400] or f"git {' '.join(args)} failed")
    return proc.stdout


def _is_git_repo(path: Path) -> bool:
    if not path.is_dir():
        return False
    try:
        return _git("rev-parse", "--is-inside-work-tree", cwd=path).strip() == "true"
    except RunWorkspaceError:
        return False


def _is_worktree(path: Path) -> bool:
    if not path.is_dir():
        return False
    gitdir = path / ".git"
    if not gitdir.exists():
        return False
    try:
        return _git("rev-parse", "--is-inside-work-tree", cwd=path).strip() == "true"
    except RunWorkspaceError:
        return False


def _resolve_sha(repo: Path, sha: str) -> str:
    ref = (sha or "").strip() or "HEAD"
    try:
        return _git("rev-parse", "--verify", f"{ref}^{{commit}}", cwd=repo).strip()
    except RunWorkspaceError as exc:
        raise RunWorkspaceError(f"unknown SHA: {ref}") from exc


def _dirty_or_empty(repo: Path) -> DirtyBaseline | None:
    if not _is_git_repo(repo):
        return DirtyBaseline(head_sha="", porcelain="", dirty=False)
    try:
        return snapshot_dirty(repo)
    except RunWorkspaceError:
        return DirtyBaseline(head_sha="", porcelain="", dirty=False)


def _branch_sha(repo: Path, branch: str) -> str | None:
    try:
        return _git("rev-parse", "--verify", f"refs/heads/{branch}", cwd=repo).strip()
    except RunWorkspaceError:
        return None


def _add_worktree(repo: Path, dest: Path, branch: str, sha: str) -> None:
    existing = _branch_sha(repo, branch)
    if existing is None:
        _git("worktree", "add", "-b", branch, str(dest), sha, cwd=repo)
        return
    if existing != sha:
        raise RunWorkspaceError(
            f"branch {branch} exists at different SHA {existing[:12]}",
        )
    _git("worktree", "add", str(dest), branch, cwd=repo)


def _meta_path(safe_id: str) -> Path:
    return worktrees_dir() / safe_id / "meta.json"


def _write_meta(safe_id: str, ws: RunWorkspace) -> None:
    path = _meta_path(safe_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(ws.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _load_meta(safe_id: str) -> RunWorkspace | None:
    path = _meta_path(safe_id)
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict):
        return None
    try:
        return RunWorkspace.model_validate(raw)
    except Exception:
        return None


def _owned_run_dir(path: Path) -> bool:
    """Refuse to delete anything outside ``$DAEDALUS_DATA/worktrees``."""
    try:
        path.resolve().relative_to(worktrees_dir().resolve())
    except ValueError:
        return False
    return True


__all__ = [
    "RunWorkspaceError",
    "bind",
    "checkpoint_block",
    "close_workspace",
    "commit_workspace",
    "current",
    "current_root",
    "export_patch",
    "journal_note",
    "open_workspace",
    "reopen_workspace",
    "snapshot_dirty",
    "worktree_enabled",
    "worktrees_dir",
]
