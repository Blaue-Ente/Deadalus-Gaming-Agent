"""SupplyChain leaf — lock/pin inspect. No live audit scanners."""
from __future__ import annotations

import ast
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from daedalus.contracts import (
    Run,
    RunCheckpoint,
    SupplyCheck,
    SupplyPackage,
    SupplyReport,
)
from daedalus.paths import data_root, playground_projects_dir, product_root
from daedalus.supply_chain import (
    OTEL_PINS,
    SupplyChain,
    SupplyRequest,
    inspect,
)

ROOT = Path(__file__).resolve().parents[1]
LEAF = ROOT / "daedalus" / "supply_chain.py"
FORBIDDEN_IMPORTS = frozenset({
    "daedalus.agent",
    "daedalus.native_tools",
    "daedalus.tool_loop",
    "daedalus.run_finalizer",
    "daedalus.mentor",
    "daedalus.mentor_helm",
    "daedalus.mentor_graph",
    "daedalus.otel",
    "daedalus.independent_reviewer",
    "daedalus.verification_controller",
    "p0_release_gate",
})
UNTOUCHED = (
    ROOT / "daedalus" / "agent.py",
    ROOT / "daedalus" / "native_tools.py",
    ROOT / "daedalus" / "tool_loop.py",
    ROOT / "daedalus" / "run_finalizer.py",
    ROOT / "daedalus" / "mentor.py",
    ROOT / "app" / "main.py",
    ROOT / "daedalus" / "standalone_boot.py",
    ROOT / "daedalus" / "verification_controller.py",
    ROOT / "daedalus" / "otel.py",
    ROOT / "daedalus" / "independent_reviewer.py",
    ROOT / "scripts" / "p0_release_gate.py",
)
HOOKS_CI = "uv-lock:\nsecret-scan:\npip-audit:\ntrivy-fs:\n"


def _isolate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DAEDALUS_STANDALONE", "1")
    monkeypatch.setenv("DAEDALUS_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("DAEDALUS_PLAYGROUND", str(tmp_path / "projects"))
    monkeypatch.delenv("DAEDALUS_SUPPLY_CHAIN", raising=False)
    monkeypatch.delenv("DAEDALUS_BENCH_LIVE", raising=False)
    data_root.cache_clear()
    playground_projects_dir.cache_clear()


def _by_id(report: SupplyReport) -> dict[str, SupplyCheck]:
    return {row.id: row for row in report.checks}


def _lock_text(versions: dict[str, str] | None = None) -> str:
    pins = dict(OTEL_PINS)
    if versions:
        pins.update(versions)
    lines = [
        "version = 1",
        "revision = 3",
        'requires-python = ">=3.11"',
        "",
        "[[package]]",
        'name = "fastapi"',
        'version = "0.115.0"',
        'source = { registry = "https://pypi.org/simple" }',
        "",
        "[[package]]",
        'name = "pytest"',
        'version = "8.0.0"',
        'source = { registry = "https://pypi.org/simple" }',
        "",
    ]
    for name, version in pins.items():
        lines.extend(
            [
                "[[package]]",
                f'name = "{name}"',
                f'version = "{version}"',
                'source = { registry = "https://pypi.org/simple" }',
                "",
            ]
        )
    return "\n".join(lines)


def _write_fixture(
    root: Path,
    *,
    lock: str | None = "",
    extra_dep: str = "",
    ci: str | None = HOOKS_CI,
    dockerfile: str | None = "FROM python:3.12-slim\n",
    gha: str | None = None,
) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    pyproject = (
        "[project]\n"
        'name = "fixture"\n'
        'version = "0.0.1"\n'
        'requires-python = ">=3.11"\n'
        'dependencies = ["fastapi>=0.115.0"'
        + (f', "{extra_dep}"' if extra_dep else "")
        + "]\n\n"
        "[project.optional-dependencies]\n"
        'dev = ["pytest>=8.0.0"]\n'
    )
    (root / "pyproject.toml").write_text(pyproject, encoding="utf-8")
    if lock is not None:
        (root / "uv.lock").write_text(lock if lock else _lock_text(), encoding="utf-8")
    if ci is not None:
        (root / ".gitlab-ci.yml").write_text(ci, encoding="utf-8")
    if dockerfile is not None:
        (root / "Dockerfile").write_text(dockerfile, encoding="utf-8")
    if gha is not None:
        dest = root / ".github" / "workflows"
        dest.mkdir(parents=True, exist_ok=True)
        (dest / "docker.yml").write_text(gha, encoding="utf-8")
    return root


def test_contract_skip_cannot_be_ok():
    with pytest.raises(ValidationError):
        SupplyCheck(id="lock_present", ok=True, skipped=True)
    with pytest.raises(ValidationError):
        SupplyReport(ok=True, skipped=True)
    with pytest.raises(ValidationError):
        SupplyCheck.model_validate({"id": "x", "ok": True, "nope": 1})
    row = SupplyCheck(id="lock_present", ok=False, skipped=True)
    assert row.schema_version == 1
    assert SupplyCheck.model_validate(row.model_dump(mode="json")) == row
    pkg = SupplyPackage(name="fastapi", version="0.115.0")
    assert pkg.schema_version == 1
    report = SupplyReport(ok=True, skipped=False)
    assert report.schema_version == 1
    assert SupplyReport.model_validate(report.model_dump(mode="json")) == report


def test_run_schema_unchanged():
    assert "supply" not in Run.model_fields
    assert "sbom" not in Run.model_fields
    assert "inventory" not in Run.model_fields
    assert "supply" not in RunCheckpoint.model_fields
    assert "sbom" not in RunCheckpoint.model_fields
    assert "inventory" not in RunCheckpoint.model_fields
    with pytest.raises(ValidationError):
        Run.model_validate({"run_id": "r", "nope": 1})


def test_leaf_does_not_import_forbidden():
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
    for bad in (
        "native_tools",
        "tool_loop",
        "run_finalizer",
        "mentor_helm",
        "mentor_graph",
        "independent_reviewer",
        "verification_controller",
        "p0_release_gate",
    ):
        assert f"daedalus.{bad}" not in src
        assert f"from .{bad}" not in src
    assert "from daedalus.agent import" not in src
    assert "from daedalus.mentor import" not in src
    assert "from daedalus.otel import" not in src
    assert "import p0_release_gate" not in src
    code = (
        "import daedalus.supply_chain as sc\n"
        "import sys\n"
        "bad = [n for n in sys.modules if n in "
        "('daedalus.agent', 'daedalus.native_tools', 'daedalus.tool_loop', "
        "'daedalus.run_finalizer', 'daedalus.mentor', 'daedalus.mentor_helm', "
        "'daedalus.mentor_graph', 'daedalus.otel', "
        "'daedalus.independent_reviewer', 'daedalus.verification_controller', "
        "'p0_release_gate') "
        "or n.startswith('daedalus.agent.') "
        "or n.startswith('daedalus.native_tools.') "
        "or n.startswith('daedalus.tool_loop.') "
        "or n.startswith('daedalus.mentor.') "
        "or n.startswith('daedalus.otel.') "
        "or n.startswith('daedalus.independent_reviewer.') "
        "or n.startswith('daedalus.verification_controller.')]\n"
        "assert not bad, bad\n"
        "from daedalus.supply_chain import SupplyRequest\n"
        "rep = sc.inspect(SupplyRequest(run_id='r'))\n"
        "assert rep.ok and (not rep.skipped)\n"
        "print('import-ok')\n"
    )
    env = os.environ.copy()
    env["DAEDALUS_STANDALONE"] = "1"
    env.pop("DAEDALUS_SUPPLY_CHAIN", None)
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


def test_agent_and_loops_untouched():
    agent = (ROOT / "daedalus" / "agent.py").read_text(encoding="utf-8")
    for path in UNTOUCHED:
        blob = path.read_text(encoding="utf-8")
        assert "SupplyChain" not in blob
        assert "daedalus.supply_chain" not in blob
        assert "from daedalus.supply_chain" not in blob
        assert "DAEDALUS_SUPPLY_CHAIN" not in blob
    assert "NEED_PROPOSE_START" in agent
    assert "NEED_SANDBOX" in agent
    assert "NEED_VERIFY" in agent


def test_unset_env_is_noop_ok(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    report = inspect(SupplyRequest(run_id="run-off"))
    assert report.ok is True
    assert report.skipped is False
    assert report.checks == []
    assert report.packages == []
    assert report.persisted == ""
    assert not (data_root() / "supply" / "run-off.json").exists()


def test_enabled_missing_lock_skip_not_ok(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    monkeypatch.setenv("DAEDALUS_SUPPLY_CHAIN", "1")
    root = _write_fixture(tmp_path / "proj", lock=None)
    report = SupplyChain().inspect(SupplyRequest(run_id="no-lock", root=root))
    assert report.skipped is True
    assert report.ok is False
    rows = _by_id(report)
    assert rows["lock_present"].skipped is True
    assert rows["lock_present"].ok is False
    assert "lock_missing" in report.blockers


def test_enabled_product_root(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    monkeypatch.setenv("DAEDALUS_SUPPLY_CHAIN", "1")
    root = product_root()
    report = SupplyChain().inspect(SupplyRequest(run_id="product", root=root))
    rows = _by_id(report)
    digest = hashlib.sha256((root / "uv.lock").read_bytes()).hexdigest()
    assert rows["lock_present"].ok is True
    assert rows["lock_hash"].ok is True
    assert report.lock_sha256 == digest
    assert rows["pyproject_in_lock"].ok is True
    assert rows["otel_pin"].ok is True
    assert rows["otel_pin"].skipped is False
    assert rows["unpinned_install"].ok is True
    assert rows["audit_hooks"].ok is True
    assert OTEL_PINS["opentelemetry-sdk"] == "1.44.0"
    names = {row.name for row in report.packages}
    assert "opentelemetry-sdk" in names
    lock_row = rows["lock_check"]
    if lock_row.ok:
        assert report.lock_check == "pass"
        assert report.ok is True
        assert report.skipped is False
    else:
        assert lock_row.skipped is True
        assert report.lock_check == "unavailable"
        assert report.skipped is True
        assert report.ok is False


def test_otel_pin_mismatch_skip_not_ok(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    monkeypatch.setenv("DAEDALUS_SUPPLY_CHAIN", "1")
    root = _write_fixture(
        tmp_path / "otel",
        lock=_lock_text({"opentelemetry-sdk": "1.43.0"}),
    )
    report = SupplyChain().inspect(SupplyRequest(run_id="otel-pin", root=root))
    row = _by_id(report)["otel_pin"]
    assert row.skipped is True
    assert row.ok is False
    assert "otel_unpinned" in report.blockers
    assert report.ok is False


def test_unpinned_install_not_ok(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    monkeypatch.setenv("DAEDALUS_SUPPLY_CHAIN", "1")
    root = _write_fixture(
        tmp_path / "unpin",
        ci=HOOKS_CI + "  - pip install ruff\n",
    )
    report = SupplyChain().inspect(SupplyRequest(run_id="unpin", root=root))
    row = _by_id(report)["unpinned_install"]
    assert row.ok is False
    assert row.skipped is False
    assert "unpinned_install" in report.blockers
    assert "ruff" in row.detail


def test_frozen_and_eq_install_ok(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    monkeypatch.setenv("DAEDALUS_SUPPLY_CHAIN", "1")
    ci = (
        HOOKS_CI
        + "  - pip install --no-cache-dir uv==0.12.12\n"
        + "  - python3 -m uv sync --frozen --extra dev\n"
        + "  - python3 -m uv pip install pip-audit==2.10.1 --python .venv/bin/python\n"
    )
    docker = (
        "FROM python:3.12-slim\n"
        "RUN pip install --no-cache-dir uv==0.12.12 && uv sync --frozen --no-dev\n"
    )
    gha = "      - run: pip install uv==0.12.12\n"
    root = _write_fixture(tmp_path / "pins", ci=ci, dockerfile=docker, gha=gha)
    report = SupplyChain().inspect(SupplyRequest(run_id="pins", root=root))
    assert _by_id(report)["unpinned_install"].ok is True
    assert _by_id(report)["audit_hooks"].ok is True


def test_uv_missing_lock_check_skip_not_ok(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    monkeypatch.setenv("DAEDALUS_SUPPLY_CHAIN", "1")
    root = _write_fixture(tmp_path / "no-uv")
    monkeypatch.setattr("daedalus.supply_chain.shutil.which", lambda _name: None)
    monkeypatch.setattr(
        "daedalus.supply_chain.sys.executable",
        str(tmp_path / "missing-python"),
    )
    report = SupplyChain().inspect(SupplyRequest(run_id="no-uv", root=root))
    row = _by_id(report)["lock_check"]
    assert row.skipped is True
    assert row.ok is False
    assert "uv_unavailable" in report.blockers
    assert report.skipped is True
    assert report.ok is False


def test_pyproject_missing_from_lock_not_ok(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    monkeypatch.setenv("DAEDALUS_SUPPLY_CHAIN", "1")
    root = _write_fixture(tmp_path / "gap", extra_dep="chromadb>=1.0.0")
    report = SupplyChain().inspect(SupplyRequest(run_id="gap", root=root))
    row = _by_id(report)["pyproject_in_lock"]
    assert row.ok is False
    assert row.skipped is False
    assert "pyproject_not_in_lock" in report.blockers
    assert "chromadb" in row.detail


def test_audit_hooks_missing_not_ok(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    monkeypatch.setenv("DAEDALUS_SUPPLY_CHAIN", "1")
    root = _write_fixture(tmp_path / "hooks", ci="ruff:\n  script: []\n")
    report = SupplyChain().inspect(SupplyRequest(run_id="hooks", root=root))
    row = _by_id(report)["audit_hooks"]
    assert row.ok is False
    assert row.skipped is False
    assert "hooks_missing" in report.blockers


def test_audit_hooks_unavailable_skip_not_ok(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    monkeypatch.setenv("DAEDALUS_SUPPLY_CHAIN", "1")
    root = _write_fixture(tmp_path / "no-ci", ci=None, dockerfile=None)
    report = SupplyChain().inspect(SupplyRequest(run_id="no-ci", root=root))
    row = _by_id(report)["audit_hooks"]
    assert row.skipped is True
    assert row.ok is False
    assert "hooks_unavailable" in report.blockers


def test_persist_under_data_supply(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    report = inspect(SupplyRequest(run_id="run-supply-1", persist=True))
    path = Path(report.persisted)
    assert path.is_file()
    assert path == data_root() / "supply" / "run-supply-1.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["run_id"] == "run-supply-1"
    assert payload["ok"] is True
    assert payload["schema_version"] == 1


def test_persist_false_writes_nothing(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    report = inspect(SupplyRequest(run_id="run-supply-off", persist=False))
    assert report.persisted == ""
    assert not (data_root() / "supply" / "run-supply-off.json").exists()


def test_cli_json_product_root():
    env = os.environ.copy()
    env["DAEDALUS_STANDALONE"] = "1"
    env.pop("DAEDALUS_SUPPLY_CHAIN", None)
    env["PYTHONPATH"] = str(ROOT) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "daedalus.supply_chain",
            "--json",
            "--run-id",
            "cli-1",
            "--root",
            str(product_root()),
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    payload = json.loads(result.stdout)
    assert payload["run_id"] == "cli-1"
    rows = {row["id"]: row for row in payload["checks"]}
    assert rows["lock_present"]["ok"] is True
    assert rows["unpinned_install"]["ok"] is True
    assert rows["audit_hooks"]["ok"] is True
    if payload["ok"]:
        assert result.returncode == 0
    else:
        assert result.returncode == 1
        assert payload["skipped"] is True


def test_cli_missing_lock_exits_1(tmp_path):
    root = _write_fixture(tmp_path / "cli-miss", lock=None)
    env = os.environ.copy()
    env["DAEDALUS_STANDALONE"] = "1"
    env["PYTHONPATH"] = str(ROOT) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    result = subprocess.run(
        [sys.executable, "-m", "daedalus.supply_chain", "--json", "--root", str(root)],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert payload["skipped"] is True
