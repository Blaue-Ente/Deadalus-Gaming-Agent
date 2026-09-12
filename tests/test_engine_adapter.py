"""EngineAdapter leaf — discover / preflight / gated run. Live verify is mocked."""
from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from daedalus.capability_registry import PLATFORMS, REGISTRY
from daedalus.contracts import Run, RunCheckpoint
from daedalus.contracts.engine import (
    EngineCapability,
    EngineDiscover,
    EngineKind,
    EnginePreflight,
    EngineRunResult,
)
from daedalus.engine_adapter import (
    EngineRequest,
    GodotEngineAdapter,
    UnityEngineAdapter,
    adapters,
    detect_kind,
    discover,
    preflight,
    run,
)
from daedalus.godot_first import preflight as godot_preflight

ROOT = Path(__file__).resolve().parents[1]
LEAF = ROOT / "daedalus" / "engine_adapter.py"
GOLDEN_FAILURES = ROOT / "artifacts" / "baseline" / "known-failures.md"
FORBIDDEN_IMPORTS = frozenset({
    "daedalus.agent",
    "daedalus.native_tools",
    "daedalus.job_ledger",
    "daedalus.run_engine",
    "daedalus.scratch_journal",
    "daedalus.engine_session",
    "daedalus.godot_bridge",
})


def _isolate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DAEDALUS_STANDALONE", "1")
    monkeypatch.setenv("DAEDALUS_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("DAEDALUS_PLAYGROUND", str(tmp_path / "projects"))
    monkeypatch.delenv("GODOT_BIN", raising=False)
    monkeypatch.delenv("GODOT_BINARY", raising=False)
    monkeypatch.delenv("UNITY_PATH", raising=False)
    monkeypatch.delenv("UNITY_EDITOR", raising=False)
    monkeypatch.delenv("DAEDALUS_GODOT_MCP", raising=False)
    monkeypatch.delenv("DAEDALUS_UNITY_MCP", raising=False)
    monkeypatch.delenv("DAEDALUS_BENCH_LIVE", raising=False)
    from daedalus.paths import data_root, playground_projects_dir

    data_root.cache_clear()
    playground_projects_dir.cache_clear()


def _missing_bins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("daedalus.godot_runtime_probe.godot_bin", lambda: "")
    monkeypatch.setattr("daedalus.engine_runners.unity_bin", lambda: "")


def _godot_tree(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "project.godot").write_text("; godot\n", encoding="utf-8")
    return root


def _unity_tree(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "Assets").mkdir()
    return root


def _boom_live(*_a: object, **_k: object) -> None:
    raise AssertionError("live run_godot/run_unity must not run")


def _boom_runtime(*_a: object, **_k: object) -> dict:
    raise AssertionError("runtime_verify must not run when live=False")


def _tripwire_runners(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("daedalus.engine_runners.run_godot", _boom_live)
    monkeypatch.setattr("daedalus.engine_runners.run_unity", _boom_live)


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
    assert "adapter_run_deferred" not in src
    code = (
        "import daedalus.engine_adapter as ea\n"
        "import sys\n"
        "bad = [n for n in sys.modules if n in "
        "('daedalus.agent', 'daedalus.native_tools', 'daedalus.job_ledger', "
        "'daedalus.run_engine', 'daedalus.scratch_journal', "
        "'daedalus.engine_session', 'daedalus.godot_bridge') "
        "or n.startswith('daedalus.agent.') "
        "or n.startswith('daedalus.native_tools.') "
        "or n.startswith('daedalus.job_ledger.') "
        "or n.startswith('daedalus.run_engine.') "
        "or n.startswith('daedalus.scratch_journal.') "
        "or n.startswith('daedalus.engine_session.') "
        "or n.startswith('daedalus.godot_bridge.')]\n"
        "assert not bad, bad\n"
        "print('import-ok', ea.GodotEngineAdapter is not None)\n"
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
    assert "engine_adapter" not in agent
    assert "from daedalus.engine_adapter" not in native
    assert "import daedalus.engine_adapter" not in native
    assert "NEED_PROPOSE_START" in agent
    assert "NEED_SANDBOX" in agent
    assert "NEED_VERIFY" in agent
    assert REGISTRY.adapter("godot").name == "EngineAdapter"
    assert "unity" not in PLATFORMS
    assert isinstance(adapters()["godot"], GodotEngineAdapter)
    assert isinstance(adapters()["unity"], UnityEngineAdapter)
    assert adapters()["godot"].kind == EngineKind.GODOT.value
    assert adapters()["unity"].kind == EngineKind.UNITY.value


def test_contract_skip_cannot_be_ok():
    with pytest.raises(ValidationError):
        EngineDiscover(kind="godot", present=False, project=False, skipped=True, ok=True)
    with pytest.raises(ValidationError):
        EnginePreflight(kind="godot", ok=True, skipped=True)
    with pytest.raises(ValidationError):
        EngineRunResult(kind="godot", ok=True, skipped=True)
    with pytest.raises(ValidationError):
        EngineDiscover.model_validate(
            {"kind": "godot", "present": False, "project": False, "nope": 1}
        )
    row = EngineDiscover(
        kind="godot", present=False, project=False, skipped=True, ok=False, error="godot_missing"
    )
    assert row.schema_version == 1
    assert EngineDiscover.model_validate(row.model_dump(mode="json")) == row
    cap = EngineCapability(name="run", available=False, reason="godot_missing")
    assert cap.schema_version == 1


def test_run_schema_unchanged():
    assert "engine" not in Run.model_fields
    assert "adapter" not in Run.model_fields
    assert "baseline" not in Run.model_fields
    assert "workspace" not in Run.model_fields
    assert "engine" not in RunCheckpoint.model_fields
    assert "adapter" not in RunCheckpoint.model_fields
    with pytest.raises(ValidationError):
        Run.model_validate({"run_id": "r", "nope": 1})


def test_detect_kind_from_project_files(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    _missing_bins(monkeypatch)
    godot = _godot_tree(tmp_path / "godot")
    unity = _unity_tree(tmp_path / "unity")
    both = tmp_path / "both"
    _godot_tree(both)
    (both / "Assets").mkdir()
    empty = tmp_path / "empty"
    empty.mkdir()
    assert detect_kind(godot) == "godot"
    assert detect_kind(unity) == "unity"
    assert detect_kind(both) == "godot"
    assert detect_kind(empty) == ""
    assert detect_kind(godot, kind="unity") == "unity"
    assert detect_kind(empty, kind="blender") == ""


def test_discover_preflight_godot_missing_bin(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    _missing_bins(monkeypatch)
    root = _godot_tree(tmp_path / "slice")
    req = EngineRequest(kind="godot", root=root)
    found = discover(req)
    assert found.kind == "godot"
    assert found.project is True
    assert found.present is False
    assert found.skipped is True
    assert found.ok is False
    assert found.error
    flight = preflight(req)
    assert flight.skipped is True
    assert flight.ok is False
    assert "godot_missing" in flight.blockers


def test_discover_preflight_unity_missing_editor(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    _missing_bins(monkeypatch)
    root = _unity_tree(tmp_path / "hub")
    req = EngineRequest(kind="unity", root=root)
    found = discover(req)
    assert found.kind == "unity"
    assert found.project is True
    assert found.present is False
    assert found.skipped is True
    assert found.ok is False
    assert found.promise == "local_hub"
    assert found.error == "unity_local"
    names = {row.name: row for row in found.capabilities}
    assert names["create"].available is True
    assert names["run"].available is False
    flight = preflight(req)
    assert flight.skipped is True
    assert flight.ok is False
    assert "unity_local" in flight.blockers


def test_unknown_kind_fail_closed(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    _missing_bins(monkeypatch)
    req = EngineRequest(kind="blender", root=tmp_path)
    found = discover(req)
    assert found.skipped is True
    assert found.ok is False
    assert found.error == "unsupported_engine"
    flight = preflight(req)
    assert flight.skipped is True
    assert flight.ok is False
    assert "unsupported_engine" in flight.blockers
    out = run(req)
    assert out.skipped is True
    assert out.ok is False
    assert "unsupported_engine" in out.blockers
    assert out.ran is False
    assert out.play is False


def test_run_live_false_is_gated(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    _missing_bins(monkeypatch)
    _tripwire_runners(monkeypatch)
    monkeypatch.setattr("daedalus.godot_first.runtime_verify", _boom_runtime)
    monkeypatch.setattr("daedalus.unity_runtime.runtime_verify", _boom_runtime)
    root = _godot_tree(tmp_path / "arena")
    out = run(EngineRequest(kind="godot", root=root, live=False))
    assert out.skipped is True
    assert out.ok is False
    assert out.ran is False
    assert out.play is False
    assert out.live is False
    assert "live_gated" in out.blockers
    unity = run(EngineRequest(kind="unity", root=_unity_tree(tmp_path / "hub"), live=False))
    assert unity.skipped is True
    assert unity.ok is False
    assert "live_gated" in unity.blockers
    assert unity.play is False


def test_godot_run_live_false_does_not_call_runtime_verify(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    _missing_bins(monkeypatch)
    _tripwire_runners(monkeypatch)
    called: list[Path] = []

    def track(root: Path) -> dict:
        called.append(root)
        raise AssertionError("runtime_verify must not run when live=False")

    monkeypatch.setattr("daedalus.godot_first.runtime_verify", track)
    out = run(EngineRequest(kind="godot", root=_godot_tree(tmp_path / "idle"), live=False))
    assert called == []
    assert "live_gated" in out.blockers


def test_unity_run_live_false_does_not_call_runtime_verify(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    _missing_bins(monkeypatch)
    _tripwire_runners(monkeypatch)
    called: list[Path] = []

    def track(root: Path) -> dict:
        called.append(root)
        raise AssertionError("runtime_verify must not run when live=False")

    monkeypatch.setattr("daedalus.unity_runtime.runtime_verify", track)
    out = run(EngineRequest(kind="unity", root=_unity_tree(tmp_path / "idle"), live=False))
    assert called == []
    assert "live_gated" in out.blockers
    assert out.play is False


def test_unity_run_live_missing_bin_is_skip_not_deferred(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    _missing_bins(monkeypatch)
    _tripwire_runners(monkeypatch)
    unity = run(EngineRequest(kind="unity", root=_unity_tree(tmp_path / "hub"), live=True))
    assert unity.kind == "unity"
    assert unity.live is True
    assert unity.skipped is True
    assert unity.ok is False
    assert unity.ran is False
    assert unity.play is False
    assert unity.via == "none"
    assert "unity_local" in unity.blockers or "unity_local" in unity.error
    assert "adapter_run_deferred" not in unity.blockers
    assert unity.error != "adapter_run_deferred"


def test_godot_run_live_missing_bin_is_skip_not_deferred(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    _missing_bins(monkeypatch)
    _tripwire_runners(monkeypatch)
    out = run(EngineRequest(kind="godot", root=_godot_tree(tmp_path / "live"), live=True))
    assert out.kind == "godot"
    assert out.skipped is True
    assert out.ok is False
    assert out.ran is False
    assert out.play is False
    assert out.live is True
    assert out.via == "none"
    assert "godot_missing" in out.blockers or "godot_missing" in out.error
    assert "adapter_run_deferred" not in out.blockers
    assert out.error != "adapter_run_deferred"


def test_godot_run_live_wraps_runtime_verify(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    _missing_bins(monkeypatch)
    _tripwire_runners(monkeypatch)
    root = _godot_tree(tmp_path / "bridge")
    seen: list[Path] = []

    def fake_verify(path: Path) -> dict:
        seen.append(path)
        return {
            "ok": True,
            "skipped": False,
            "ran": True,
            "bridge": True,
            "blockers": [],
            "error": "",
        }

    monkeypatch.setattr("daedalus.godot_first.runtime_verify", fake_verify)
    out = run(EngineRequest(kind="godot", root=root, live=True))
    assert seen == [root]
    assert out.ok is True
    assert out.skipped is False
    assert out.ran is True
    assert out.play is False
    assert out.live is True
    assert out.via == "bridge"
    assert "adapter_run_deferred" not in out.blockers


def test_godot_run_live_skipped_probe_is_not_ok(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    _missing_bins(monkeypatch)
    _tripwire_runners(monkeypatch)

    def fake_verify(_path: Path) -> dict:
        return {
            "ok": True,
            "skipped": True,
            "ran": False,
            "bridge": False,
            "blockers": ["runtime_skipped"],
            "error": "runtime_skipped",
        }

    monkeypatch.setattr("daedalus.godot_first.runtime_verify", fake_verify)
    out = run(EngineRequest(kind="godot", root=_godot_tree(tmp_path / "skip"), live=True))
    assert out.skipped is True
    assert out.ok is False
    assert out.ran is False
    assert out.play is False
    assert out.via == "none"


def test_godot_run_live_failed_run_is_not_skip(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    _missing_bins(monkeypatch)
    _tripwire_runners(monkeypatch)

    def fake_verify(_path: Path) -> dict:
        return {
            "ok": False,
            "skipped": False,
            "ran": True,
            "bridge": True,
            "blockers": ["player_moved"],
            "error": "runtime failed",
        }

    monkeypatch.setattr("daedalus.godot_first.runtime_verify", fake_verify)
    out = run(EngineRequest(kind="godot", root=_godot_tree(tmp_path / "fail"), live=True))
    assert out.ok is False
    assert out.skipped is False
    assert out.ran is True
    assert out.play is False
    assert out.via == "bridge"
    assert "player_moved" in out.blockers


def test_godot_run_live_pin_failure_does_not_claim_ran(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    _missing_bins(monkeypatch)
    _tripwire_runners(monkeypatch)

    def fake_verify(_path: Path) -> dict:
        return {
            "ok": False,
            "skipped": False,
            "ran": False,
            "bridge": False,
            "blockers": ["godot_version:4.2 (want 4.3)"],
            "error": "godot_version:4.2 (want 4.3)",
        }

    monkeypatch.setattr("daedalus.godot_first.runtime_verify", fake_verify)
    out = run(EngineRequest(kind="godot", root=_godot_tree(tmp_path / "pin"), live=True))
    assert out.ok is False
    assert out.skipped is False
    assert out.ran is False
    assert out.play is False
    assert out.via == "none"
    assert any("godot_version" in item for item in out.blockers)


def test_godot_run_live_headless_fallback_via(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    _missing_bins(monkeypatch)
    _tripwire_runners(monkeypatch)

    def fake_verify(_path: Path) -> dict:
        return {
            "ok": False,
            "skipped": False,
            "ran": True,
            "bridge": False,
            "blockers": ["bridge_ping"],
            "error": "bridge not reachable",
        }

    monkeypatch.setattr("daedalus.godot_first.runtime_verify", fake_verify)
    out = run(EngineRequest(kind="godot", root=_godot_tree(tmp_path / "probe"), live=True))
    assert out.via == "headless"
    assert out.ran is True
    assert out.play is False
    assert out.ok is False


def test_unity_run_live_wraps_runtime_verify(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    _missing_bins(monkeypatch)
    _tripwire_runners(monkeypatch)
    root = _unity_tree(tmp_path / "batch")
    seen: list[Path] = []

    def fake_verify(path: Path) -> dict:
        seen.append(path)
        return {
            "ok": True,
            "skipped": False,
            "ran": True,
            "play": False,
            "via": "batchmode",
            "blockers": [],
            "error": "",
        }

    monkeypatch.setattr("daedalus.unity_runtime.runtime_verify", fake_verify)
    out = run(EngineRequest(kind="unity", root=root, live=True))
    assert seen == [root]
    assert out.ok is True
    assert out.skipped is False
    assert out.ran is True
    assert out.play is False
    assert out.live is True
    assert out.via == "batchmode"
    assert "adapter_run_deferred" not in out.blockers


def test_unity_run_live_skipped_probe_is_not_ok(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    _missing_bins(monkeypatch)
    _tripwire_runners(monkeypatch)

    def fake_verify(_path: Path) -> dict:
        return {
            "ok": True,
            "skipped": True,
            "ran": False,
            "play": False,
            "via": "none",
            "blockers": ["unity_local"],
            "error": "unity_local",
        }

    monkeypatch.setattr("daedalus.unity_runtime.runtime_verify", fake_verify)
    out = run(EngineRequest(kind="unity", root=_unity_tree(tmp_path / "skip"), live=True))
    assert out.skipped is True
    assert out.ok is False
    assert out.ran is False
    assert out.play is False
    assert out.via == "none"
    assert "unity_local" in out.blockers


def test_unity_run_live_failed_run_is_not_skip(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    _missing_bins(monkeypatch)
    _tripwire_runners(monkeypatch)

    def fake_verify(_path: Path) -> dict:
        return {
            "ok": False,
            "skipped": False,
            "ran": True,
            "play": False,
            "via": "batchmode",
            "blockers": ["unity rc=1"],
            "error": "unity rc=1",
        }

    monkeypatch.setattr("daedalus.unity_runtime.runtime_verify", fake_verify)
    out = run(EngineRequest(kind="unity", root=_unity_tree(tmp_path / "fail"), live=True))
    assert out.ok is False
    assert out.skipped is False
    assert out.ran is True
    assert out.play is False
    assert out.via == "batchmode"
    assert "unity rc=1" in out.blockers


def test_unity_run_live_timeout_does_not_claim_ran(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    _missing_bins(monkeypatch)
    _tripwire_runners(monkeypatch)

    def fake_verify(_path: Path) -> dict:
        return {
            "ok": False,
            "skipped": False,
            "ran": False,
            "play": False,
            "via": "none",
            "blockers": ["timed out"],
            "error": "timed out",
        }

    monkeypatch.setattr("daedalus.unity_runtime.runtime_verify", fake_verify)
    out = run(EngineRequest(kind="unity", root=_unity_tree(tmp_path / "hang"), live=True))
    assert out.ok is False
    assert out.skipped is False
    assert out.ran is False
    assert out.play is False
    assert out.via == "none"
    assert any("timed out" in item for item in out.blockers)


def test_unity_run_live_mcp_only_is_honest_refuse(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    _missing_bins(monkeypatch)
    _tripwire_runners(monkeypatch)
    monkeypatch.setenv("DAEDALUS_UNITY_MCP", "unity-local")

    async def boom_mcp(*_a: object, **_k: object) -> dict:
        raise AssertionError("leaf run must not call Unity MCP")

    monkeypatch.setattr("daedalus.mcp_client.list_tools", boom_mcp)
    monkeypatch.setattr("daedalus.mcp_client.call_tool", boom_mcp)
    out = run(EngineRequest(kind="unity", root=_unity_tree(tmp_path / "mcp"), live=True))
    assert out.skipped is True
    assert out.ok is False
    assert out.ran is False
    assert out.play is False
    assert out.via == "none"
    assert "unity_local" in out.blockers or "unity_local" in out.error
    assert "adapter_run_deferred" not in out.blockers


def test_godot_preflight_parity_missing_bin(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    _missing_bins(monkeypatch)
    root = _godot_tree(tmp_path / "parity")
    leaf = preflight(EngineRequest(kind="godot", root=root))
    row = godot_preflight(root)
    assert leaf.ok == bool(row.get("ok"))
    assert leaf.skipped == bool(row.get("skipped"))
    assert "godot_missing" in leaf.blockers
    assert "godot_missing" in (row.get("blockers") or [])


def test_golden_pack_unchanged(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    _missing_bins(monkeypatch)
    before = GOLDEN_FAILURES.read_bytes()
    _tripwire_runners(monkeypatch)
    discover(EngineRequest(kind="godot", root=tmp_path))
    preflight(EngineRequest(kind="unity", root=tmp_path))
    run(EngineRequest(kind="godot", root=tmp_path, live=True))
    run(EngineRequest(kind="unity", root=tmp_path, live=True))
    assert GOLDEN_FAILURES.read_bytes() == before
