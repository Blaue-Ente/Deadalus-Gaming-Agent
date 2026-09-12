"""Git worktree per run. No live LLM. Temp repos only."""
from __future__ import annotations

import ast
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from daedalus.contracts import DirtyBaseline, Run, RunCheckpoint, RunWorkspace
from daedalus.run_workspace import (
    RunWorkspaceError,
    bind,
    checkpoint_block,
    close_workspace,
    commit_workspace,
    current,
    current_root,
    export_patch,
    journal_note,
    open_workspace,
    reopen_workspace,
    snapshot_dirty,
    worktree_enabled,
    worktrees_dir,
)

ROOT = Path(__file__).resolve().parents[1]
RUN_WORKSPACE_PATH = ROOT / "daedalus" / "run_workspace.py"
FORBIDDEN_IMPORTS = frozenset({
    "daedalus.agent",
    "daedalus.native_tools",
    "daedalus.job_ledger",
    "daedalus.run_engine",
    "daedalus.scratch_journal",
})
needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git CLI required")


def _isolate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DAEDALUS_STANDALONE", "1")
    monkeypatch.setenv("DAEDALUS_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("DAEDALUS_PLAYGROUND", str(tmp_path / "projects"))
    monkeypatch.delenv("DAEDALUS_WORKTREE", raising=False)
    from daedalus.paths import data_root, playground_projects_dir

    data_root.cache_clear()
    playground_projects_dir.cache_clear()


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "--no-optional-locks", *args],
        cwd=str(repo),
        capture_output=True,
        text=True,
        check=True,
    )
    return proc.stdout


def _init_repo(path: Path, files: dict[str, str] | None = None) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "init", "-b", "main"],
        cwd=str(path),
        check=True,
        capture_output=True,
        text=True,
    )
    _git(path, "config", "user.email", "test@daedalus.local")
    _git(path, "config", "user.name", "Test")
    payload = files if files is not None else {"README.md": "hello\n"}
    for rel, content in payload.items():
        dest = path / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content, encoding="utf-8")
    _git(path, "add", "-A")
    _git(path, "commit", "-m", "init")
    return path


def _porcelain(repo: Path) -> str:
    return _git(repo, "status", "--porcelain=v1")


def _head(repo: Path) -> str:
    return _git(repo, "rev-parse", "HEAD").strip()


def test_flag_default_off(monkeypatch):
    monkeypatch.delenv("DAEDALUS_WORKTREE", raising=False)
    assert worktree_enabled() is False
    monkeypatch.setenv("DAEDALUS_WORKTREE", "0")
    assert worktree_enabled() is False
    monkeypatch.setenv("DAEDALUS_WORKTREE", "off")
    assert worktree_enabled() is False
    monkeypatch.setenv("DAEDALUS_WORKTREE", "false")
    assert worktree_enabled() is False
    monkeypatch.setenv("DAEDALUS_WORKTREE", "1")
    assert worktree_enabled() is True
    monkeypatch.setenv("DAEDALUS_WORKTREE", "true")
    assert worktree_enabled() is True


def test_flag_off_is_identity(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    folder = tmp_path / "plain"
    folder.mkdir()
    (folder / "index.html").write_text("<p>hi</p>\n", encoding="utf-8")
    before = list(folder.iterdir())
    ws = open_workspace("run-off", folder, slug="plain")
    assert ws.enabled is False
    assert ws.worktree == str(folder.resolve())
    assert ws.repo == str(folder.resolve())
    assert ws.branch == ""
    assert export_patch(ws) == ""
    close_workspace(ws)
    assert [p.name for p in folder.iterdir()] == [p.name for p in before]
    assert reopen_workspace("run-off") is None
    assert current_root() is None


@needs_git
def test_flag_off_does_not_create_worktree(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    repo = _init_repo(tmp_path / "repo")
    listed = _git(repo, "worktree", "list")
    ws = open_workspace("run-off-git", repo)
    assert ws.enabled is False
    assert ws.worktree == str(repo.resolve())
    assert export_patch(ws) == ""
    close_workspace(ws)
    assert _git(repo, "worktree", "list") == listed
    assert not (tmp_path / "data" / "worktrees" / "run-off-git" / "tree").exists()


@needs_git
def test_open_clean_repo_isolates_edits(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    monkeypatch.setenv("DAEDALUS_WORKTREE", "1")
    repo = _init_repo(tmp_path / "repo")
    main_head = _head(repo)
    main_porc = _porcelain(repo)
    ws = open_workspace("run-clean", repo, slug="demo")
    assert ws.enabled is True
    assert ws.base_sha == main_head
    assert ws.branch == "daedalus/run-run-clean"
    tree = Path(ws.worktree)
    assert tree.is_dir()
    assert tree != repo
    (tree / "README.md").write_text("changed in worktree\n", encoding="utf-8")
    (tree / "extra.txt").write_text("new\n", encoding="utf-8")
    assert (repo / "README.md").read_text(encoding="utf-8") == "hello\n"
    assert not (repo / "extra.txt").exists()
    assert _head(repo) == main_head
    assert _porcelain(repo) == main_porc
    listed = _git(repo, "worktree", "list")
    assert str(tree) in listed


@needs_git
def test_dirty_baseline_is_main_not_worktree(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    monkeypatch.setenv("DAEDALUS_WORKTREE", "1")
    repo = _init_repo(tmp_path / "repo")
    (repo / "README.md").write_text("dirty on main\n", encoding="utf-8")
    (repo / "scratch.txt").write_text("untracked\n", encoding="utf-8")
    base = snapshot_dirty(repo)
    assert base.dirty is True
    assert "README.md" in base.porcelain
    assert "scratch.txt" in base.porcelain
    ws = open_workspace("run-dirty", repo)
    assert ws.dirty is not None
    assert ws.dirty.dirty is True
    assert "README.md" in ws.dirty.porcelain
    tree = Path(ws.worktree)
    assert (tree / "README.md").read_text(encoding="utf-8") == "hello\n"
    assert not (tree / "scratch.txt").exists()


@needs_git
def test_pin_sha(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    monkeypatch.setenv("DAEDALUS_WORKTREE", "1")
    repo = _init_repo(tmp_path / "repo")
    parent = _head(repo)
    (repo / "later.txt").write_text("second\n", encoding="utf-8")
    _git(repo, "add", "later.txt")
    _git(repo, "commit", "-m", "second")
    ws = open_workspace("run-pin", repo, sha=parent)
    assert ws.base_sha == parent
    tree = Path(ws.worktree)
    assert not (tree / "later.txt").exists()
    assert (tree / "README.md").exists()


@needs_git
def test_export_patch_leaves_main_clean(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    monkeypatch.setenv("DAEDALUS_WORKTREE", "1")
    repo = _init_repo(tmp_path / "repo")
    main_porc = _porcelain(repo)
    ws = open_workspace("run-patch", repo)
    (Path(ws.worktree) / "README.md").write_text("patched\n", encoding="utf-8")
    diff = export_patch(ws)
    assert "patched" in diff
    assert "README.md" in diff
    assert _porcelain(repo) == main_porc
    assert (Path(ws.worktree).parent / "patch.diff").is_file()


@needs_git
def test_commit_stays_off_main(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    monkeypatch.setenv("DAEDALUS_WORKTREE", "1")
    repo = _init_repo(tmp_path / "repo")
    main_head = _head(repo)
    ws = open_workspace("run-commit", repo)
    (Path(ws.worktree) / "README.md").write_text("committed\n", encoding="utf-8")
    sha = commit_workspace(ws, "run change")
    assert sha
    assert sha != main_head
    assert _head(repo) == main_head
    assert _head(Path(ws.worktree)) == sha
    assert _git(repo, "branch", "--list", "daedalus/run-run-commit").strip()


@needs_git
def test_destroy_leaves_main_intact(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    monkeypatch.setenv("DAEDALUS_WORKTREE", "1")
    repo = _init_repo(tmp_path / "repo")
    (repo / "README.md").write_text("pre-existing dirt\n", encoding="utf-8")
    main_head = _head(repo)
    main_porc = _porcelain(repo)
    ws = open_workspace("run-close", repo)
    tree = Path(ws.worktree)
    (tree / "README.md").write_text("isolated\n", encoding="utf-8")
    close_workspace(ws)
    assert not tree.exists()
    assert _head(repo) == main_head
    assert _porcelain(repo) == main_porc
    assert (repo / "README.md").read_text(encoding="utf-8") == "pre-existing dirt\n"
    listed = _git(repo, "worktree", "list")
    assert str(tree) not in listed
    assert not _git(repo, "branch", "--list", "daedalus/run-run-close").strip()
    assert reopen_workspace("run-close") is None
    close_workspace(ws)


@needs_git
def test_reopen_live_and_stale(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    monkeypatch.setenv("DAEDALUS_WORKTREE", "1")
    repo = _init_repo(tmp_path / "repo")
    ws = open_workspace("run-reopen", repo)
    again = reopen_workspace("run-reopen")
    assert again is not None
    assert again.worktree == ws.worktree
    assert again.base_sha == ws.base_sha
    tree = Path(ws.worktree)
    shutil.rmtree(tree)
    with pytest.raises(RunWorkspaceError, match="missing or invalid"):
        reopen_workspace("run-reopen")
    assert current_root() is None


@needs_git
def test_not_a_git_repo_fails_when_enabled(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    monkeypatch.setenv("DAEDALUS_WORKTREE", "1")
    folder = tmp_path / "playground-slug"
    folder.mkdir()
    (folder / "index.html").write_text("x\n", encoding="utf-8")
    with pytest.raises(RunWorkspaceError, match="not a git repository"):
        open_workspace("run-nogit", folder)


def test_missing_git_fails_when_enabled(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    monkeypatch.setenv("DAEDALUS_WORKTREE", "1")
    monkeypatch.setattr("daedalus.run_workspace.shutil.which", lambda _name: None)
    with pytest.raises(RunWorkspaceError, match="git CLI missing"):
        open_workspace("run-nogitcli", tmp_path)


def test_commit_disabled_raises(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    ws = open_workspace("run-off-commit", tmp_path)
    assert ws.enabled is False
    with pytest.raises(RunWorkspaceError, match="disabled"):
        commit_workspace(ws, "nope")


@needs_git
def test_bad_sha_fails_closed(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    monkeypatch.setenv("DAEDALUS_WORKTREE", "1")
    repo = _init_repo(tmp_path / "repo")
    with pytest.raises(RunWorkspaceError, match="unknown SHA"):
        open_workspace("run-badsha", repo, sha="deadbeef" * 5)


def test_journal_note_and_checkpoint_block(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    ws = RunWorkspace(
        run_id="job_abc",
        repo="/tmp/repo",
        worktree="/tmp/repo",
        base_sha="abc123def456",
        branch="",
        enabled=False,
        slug="arena",
        dirty=DirtyBaseline(head_sha="abc123def456", porcelain=" M a", dirty=True),
    )
    note = journal_note(ws)
    assert len(note) <= 8000
    assert "run=job_abc" in note
    assert "enabled=0" in note
    assert "sha=abc123def456" in note
    assert "dirty=1" in note
    block = checkpoint_block(ws)
    assert set(block) == {
        "run_id", "repo", "worktree", "base_sha", "branch",
        "enabled", "slug", "dirty",
    }
    assert block["enabled"] is False
    assert block["dirty"]["dirty"] is True
    assert block["slug"] == "arena"


def test_current_root_none_when_unbound_or_disabled(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    assert current() is None
    assert current_root() is None
    disabled = RunWorkspace(
        run_id="r",
        repo=str(tmp_path),
        worktree=str(tmp_path / "tree"),
        base_sha="",
        enabled=False,
    )
    with bind(disabled):
        assert current() is disabled
        assert current_root() is None
    assert current() is None
    enabled = disabled.model_copy(update={"enabled": True, "worktree": str(tmp_path / "wt")})
    with bind(enabled):
        assert current_root() == Path(enabled.worktree)
    assert current_root() is None


@needs_git
def test_bind_enabled_root(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    monkeypatch.setenv("DAEDALUS_WORKTREE", "1")
    repo = _init_repo(tmp_path / "repo")
    ws = open_workspace("run-bind", repo)
    with bind(ws):
        assert current_root() == Path(ws.worktree)
    assert current_root() is None
    close_workspace(ws)


def test_contract_rejects_extra_and_run_schema_unchanged():
    with pytest.raises(ValidationError):
        DirtyBaseline.model_validate({"head_sha": "a", "nope": 1})
    with pytest.raises(ValidationError):
        RunWorkspace.model_validate({
            "run_id": "r",
            "repo": "/",
            "worktree": "/",
            "base_sha": "a",
            "nope": 1,
        })
    assert "workspace" not in Run.model_fields
    assert "workspace" not in RunCheckpoint.model_fields
    row = DirtyBaseline(head_sha="abc")
    assert row.schema_version == 1
    again = DirtyBaseline.model_validate(row.model_dump(mode="json"))
    assert again == row


def test_run_workspace_does_not_import_runtime():
    tree = ast.parse(RUN_WORKSPACE_PATH.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name not in FORBIDDEN_IMPORTS
                assert not any(
                    alias.name == bad or alias.name.startswith(bad + ".")
                    for bad in FORBIDDEN_IMPORTS
                )
        elif isinstance(node, ast.ImportFrom) and node.module:
            assert node.module not in FORBIDDEN_IMPORTS
            assert not any(
                node.module == bad or node.module.startswith(bad + ".")
                for bad in FORBIDDEN_IMPORTS
            )
    code = (
        "import daedalus.run_workspace as rw\n"
        "import sys\n"
        "bad = [n for n in sys.modules if n in "
        "('daedalus.agent', 'daedalus.native_tools', 'daedalus.job_ledger', "
        "'daedalus.run_engine', 'daedalus.scratch_journal') "
        "or n.startswith('daedalus.agent.') "
        "or n.startswith('daedalus.native_tools.') "
        "or n.startswith('daedalus.job_ledger.') "
        "or n.startswith('daedalus.run_engine.') "
        "or n.startswith('daedalus.scratch_journal.')]\n"
        "assert not bad, bad\n"
        "print('import-ok', rw.worktree_enabled())\n"
    )
    env = os.environ.copy()
    env["DAEDALUS_STANDALONE"] = "1"
    env.pop("DAEDALUS_WORKTREE", None)
    env["PYTHONPATH"] = str(ROOT) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    assert "import-ok" in result.stdout


def test_agent_and_write_path_untouched():
    agent = (ROOT / "daedalus" / "agent.py").read_text(encoding="utf-8")
    native = (ROOT / "daedalus" / "native_tools.py").read_text(encoding="utf-8")
    assert "run_workspace" not in agent
    assert "DAEDALUS_WORKTREE" not in agent
    assert "run_workspace" not in native
    assert "NEED_PROPOSE_START" in agent
    assert "NEED_SANDBOX" in agent


def test_worktrees_dir_under_data(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    path = worktrees_dir()
    assert path == (tmp_path / "data" / "worktrees")
    assert path.is_dir()
