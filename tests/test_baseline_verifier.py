"""Pre-edit baseline snapshot. No live LLM. Temp repos only."""
from __future__ import annotations

import ast
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from daedalus.contracts import Run, RunCheckpoint
from daedalus.contracts.baseline import (
    BaselineCheck,
    BaselineReport,
    EngineProbe,
)
from daedalus.run_workspace import (
    RunWorkspaceError,
    bind,
    close_workspace,
    current_root,
    open_workspace,
    snapshot_dirty,
)
from daedalus.baseline_verifier import BaselineRequest, BaselineVerifier
from daedalus.paths import data_root, product_root

ROOT = Path(__file__).resolve().parents[1]
LEAF = ROOT / "daedalus" / "baseline_verifier.py"
GOLDEN_FAILURES = ROOT / "artifacts" / "baseline" / "known-failures.md"
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
    monkeypatch.delenv("DAEDALUS_BENCH_LIVE", raising=False)
    from daedalus.paths import data_root as _data, playground_projects_dir

    _data.cache_clear()
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


def _missing_engines(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("daedalus.godot_runtime_probe.godot_bin", lambda: "")
    monkeypatch.setattr("daedalus.engine_runners.unity_bin", lambda: "")
    monkeypatch.setattr("daedalus.blender_jobs.blender_bin", lambda: "")
    monkeypatch.setattr("daedalus.web_first.chromium_bin", lambda: "")
    monkeypatch.setattr("daedalus.baseline_verifier._godot_bin", lambda: "")
    monkeypatch.setattr("daedalus.baseline_verifier._unity_bin", lambda: "")
    monkeypatch.setattr("daedalus.baseline_verifier._blender_bin", lambda: "")
    monkeypatch.setattr("daedalus.baseline_verifier._chromium_bin", lambda: "")


def _quiet_req(**kwargs: object) -> BaselineRequest:
    body = {
        "run_id": "t",
        "bench": "off",
        "pack_check": False,
        "persist": False,
    }
    body.update(kwargs)
    return BaselineRequest(**body)  # type: ignore[arg-type]


def test_leaf_does_not_import_runtime():
    tree = ast.parse(LEAF.read_text(encoding="utf-8"))
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
    src = LEAF.read_text(encoding="utf-8")
    assert "run_godot(" not in src
    assert "run_unity(" not in src
    assert "record_baseline.record(" not in src
    assert "baseline.record(" not in src
    code = (
        "import daedalus.baseline_verifier as bv\n"
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
        "print('import-ok', bv.BaselineVerifier is not None)\n"
    )
    env = os.environ.copy()
    env["DAEDALUS_STANDALONE"] = "1"
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
    assert "baseline_verifier" not in agent
    assert "BaselineVerifier" not in agent
    assert "baseline_verifier" not in native
    assert "NEED_PROPOSE_START" in agent
    assert "NEED_SANDBOX" in agent
    assert "NEED_VERIFY" in agent


def test_contract_skip_cannot_be_ok():
    with pytest.raises(ValidationError):
        EngineProbe(name="godot", present=False, skipped=True, ok=True)
    with pytest.raises(ValidationError):
        BaselineCheck(id="tests", ok=True, skipped=True)
    with pytest.raises(ValidationError):
        EngineProbe.model_validate({"name": "godot", "present": False, "nope": 1})
    with pytest.raises(ValidationError):
        BaselineCheck.model_validate({"id": "tests", "ok": True, "nope": 1})
    row = EngineProbe(name="godot", present=False, skipped=True, ok=False, error="godot_missing")
    assert row.schema_version == 1
    assert EngineProbe.model_validate(row.model_dump(mode="json")) == row


def test_run_schema_unchanged():
    assert "baseline" not in Run.model_fields
    assert "workspace" not in Run.model_fields
    assert "baseline" not in RunCheckpoint.model_fields
    assert "workspace" not in RunCheckpoint.model_fields
    with pytest.raises(ValidationError):
        Run.model_validate({"run_id": "r", "nope": 1})


def test_resolve_root_unbound_is_product(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    assert current_root() is None
    assert BaselineVerifier().resolve_root(BaselineRequest()) == product_root()


def test_resolve_root_disabled_bind_is_not_worktree(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    from daedalus.contracts import RunWorkspace

    disabled = RunWorkspace(
        run_id="r",
        repo=str(tmp_path),
        worktree=str(tmp_path / "tree"),
        base_sha="",
        enabled=False,
    )
    with bind(disabled):
        assert current_root() is None
        assert BaselineVerifier().resolve_root(BaselineRequest()) == product_root()


@needs_git
def test_resolve_root_uses_current_root_when_bound(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    monkeypatch.setenv("DAEDALUS_WORKTREE", "1")
    repo = _init_repo(tmp_path / "repo")
    ws = open_workspace("run-base", repo)
    try:
        with bind(ws):
            assert current_root() == Path(ws.worktree)
            assert BaselineVerifier().resolve_root(BaselineRequest()) == Path(ws.worktree)
    finally:
        close_workspace(ws)


@needs_git
def test_git_status_reuses_dirty_baseline(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    repo = _init_repo(tmp_path / "repo")
    (repo / "README.md").write_text("dirty\n", encoding="utf-8")
    want = snapshot_dirty(repo)
    got = BaselineVerifier().git_status(repo)
    assert got.head_sha == want.head_sha
    assert got.dirty is True
    assert got.porcelain == want.porcelain


@needs_git
def test_git_status_missing_git_fails_closed(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    repo = _init_repo(tmp_path / "repo")
    monkeypatch.setattr("daedalus.run_workspace.shutil.which", lambda _name: None)
    with pytest.raises(RunWorkspaceError, match="git CLI missing"):
        BaselineVerifier().git_status(repo)
    report = BaselineVerifier().capture(_quiet_req(root=repo, run_id="no-git"))
    assert report.ok is False
    git_row = next(row for row in report.checks if row.id == "git")
    assert git_row.ok is False
    assert git_row.skipped is False


def test_dependencies_lock_hash():
    root = product_root()
    deps = BaselineVerifier().dependencies(root)
    digest = hashlib.sha256((root / "uv.lock").read_bytes()).hexdigest()
    assert deps.uv_lock_sha256 == digest
    assert deps.package_version
    assert deps.uv_lock_check in {"pass", "fail", "unavailable"}


def test_engine_probes_missing_bins_skip_not_ok(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    _missing_engines(monkeypatch)
    report = BaselineVerifier().capture(_quiet_req(root=tmp_path, run_id="probes"))
    names = {row.name: row for row in report.engines}
    for name in ("godot", "unity", "blender", "chromium"):
        assert names[name].skipped is True
        assert names[name].ok is False
        assert names[name].present is False
    assert report.ok is True


def test_live_engines_missing_fails_report(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    _missing_engines(monkeypatch)
    report = BaselineVerifier().capture(
        _quiet_req(root=tmp_path, run_id="live", live_engines=True)
    )
    assert report.ok is False
    engines = next(row for row in report.checks if row.id == "engines")
    assert engines.ok is False
    assert engines.skipped is True


def test_no_related_tests_is_not_ok(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    report = BaselineVerifier().capture(
        _quiet_req(root=tmp_path, run_id="none", files=["daedalus/no_such_module.py"])
    )
    tests = next(row for row in report.checks if row.id == "tests")
    assert tests.skipped is True
    assert tests.ok is False
    assert report.ok is False


def test_targeted_pytest_does_not_run_full_suite(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    repo = tmp_path / "proj"
    repo.mkdir()
    (repo / "tests").mkdir()
    (repo / "tests" / "test_tiny.py").write_text(
        "def test_tiny():\n    assert True\n",
        encoding="utf-8",
    )
    seen: list[list[str]] = []
    real = subprocess.run

    def spy(cmd, *args, **kwargs):
        argv = list(cmd) if isinstance(cmd, (list, tuple)) else []
        if argv and "-m" in argv and "pytest" in argv:
            seen.append(argv)
            return subprocess.CompletedProcess(argv, 0, "", "")
        return real(cmd, *args, **kwargs)

    monkeypatch.setattr("daedalus.baseline_verifier.subprocess.run", spy)
    report = BaselineVerifier().capture(
        _quiet_req(root=repo, run_id="tiny", tests=["tests/test_tiny.py"])
    )
    assert seen
    for argv in seen:
        assert "pytest" in argv
        assert any(str(p).endswith("test_tiny.py") for p in argv)
        assert "tests" not in argv
    tests = next(row for row in report.checks if row.id == "tests")
    assert tests.ok is True


def test_default_capture_does_not_call_record(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    before = GOLDEN_FAILURES.read_bytes()
    hits: list[str] = []

    def boom(*_a, **_k):
        hits.append("record")
        raise AssertionError("record_baseline.record() must not run")

    import daedalus.baseline_verifier as bv

    pack = bv._load_pack()
    monkeypatch.setattr(pack, "record", boom)
    report = BaselineVerifier().capture(
        _quiet_req(root=tmp_path, run_id="norecord", pack_check=True)
    )
    assert hits == []
    assert GOLDEN_FAILURES.read_bytes() == before
    assert report.persisted == ""


def test_pack_check_composes(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    report = BaselineVerifier().capture(
        _quiet_req(root=ROOT, run_id="pack", pack_check=True)
    )
    pack = next(row for row in report.checks if row.id == "pack")
    assert pack.ok is True, pack.detail


def test_honesty_bench_hermetic(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    report = BaselineVerifier().capture(
        _quiet_req(root=ROOT, run_id="honest", bench="honesty")
    )
    bench = next(row for row in report.checks if row.id == "bench")
    assert bench.ok is True, bench.detail
    assert "bake_off=False" in bench.detail
    assert not (data_root() / "bench").exists()


def test_known_failures_observational(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    repo = tmp_path / "proj"
    (repo / "tests").mkdir(parents=True)
    (repo / "tests" / "test_boom.py").write_text(
        "def test_boom():\n    assert False\n",
        encoding="utf-8",
    )
    report = BaselineVerifier().capture(
        _quiet_req(root=repo, run_id="boom", tests=["tests/test_boom.py"], persist=True)
    )
    assert report.ok is False
    ids = [row.id for row in report.known_failures]
    assert any("test_boom" in item for item in ids)
    persisted = Path(report.persisted)
    assert persisted.is_file()
    md = persisted.with_name("known-failures.md").read_text(encoding="utf-8")
    assert "not permission to delete, skip, or weaken tests" in md
    assert "test_boom" in md


def test_slug_godot_build_without_bin_fail_closed(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    _missing_engines(monkeypatch)
    hits: list[str] = []

    def boom(*_a, **_k):
        hits.append("run")
        raise AssertionError("live run_godot/run_unity must not run")

    monkeypatch.setattr("daedalus.engine_runners.run_godot", boom)
    monkeypatch.setattr("daedalus.engine_runners.run_unity", boom)
    report = BaselineVerifier().capture(
        _quiet_req(root=tmp_path, run_id="godot", slug="arena", kind="godot")
    )
    build = next(row for row in report.checks if row.id == "build")
    assert build.skipped is True
    assert build.ok is False
    assert report.ok is False
    assert hits == []
    assert "playtest" in build.detail


def test_persist_under_data_baselines(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    report = BaselineVerifier().capture(_quiet_req(root=tmp_path, run_id="keep", persist=True))
    path = data_root() / "baselines" / "keep" / "report.json"
    assert Path(report.persisted) == path
    body = json.loads(path.read_text(encoding="utf-8"))
    again = BaselineReport.model_validate(body)
    assert again.run_id == "keep"
    assert again.ok is True
    assert again.dependencies.uv_lock_sha256 == "missing"
    assert (path.parent / "known-failures.md").is_file()
