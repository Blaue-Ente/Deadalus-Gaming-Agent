"""Adapters for legacy tool strings/dataclasses. No agent-loop wiring."""
from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

from daedalus.contracts import (
    ErrorClass,
    Observation,
    ToolLogRow,
    ToolObservation,
    ToolRequest,
    ToolResult,
    ToolSource,
)
from daedalus.offline import NETWORK_TOOLS
from daedalus.outbox import format_permission_denied
from daedalus.tool_adapt import (
    contract_request,
    contract_request_from_marker,
    contract_result,
    error_from_legacy_text,
    legacy_log_flags,
    live_specs,
    log_row_from_legacy,
    observation_from_mapping,
    protocol_ok,
    result_from_legacy_text,
    spec_from_native,
)
from daedalus.tool_protocol import ToolRequest as RuntimeRequest
from daedalus.tool_protocol import ToolResult as RuntimeResult

ROOT = Path(__file__).resolve().parents[1]


def test_legacy_blocked_code_search():
    text = "── code_search BLOCKED ──\nPLAYGROUND JAIL"
    assert legacy_log_flags(text) == (True, True)
    err = error_from_legacy_text("code_search", text)
    assert err is not None
    assert err.error_class is ErrorClass.TOOL_BLOCKED
    result = result_from_legacy_text("code_search", text)
    assert result.ok is True
    assert result.blocked is True
    assert result.error is not None
    assert result.error.error_class is ErrorClass.TOOL_BLOCKED


def test_legacy_failed_run_command():
    text = "── run_command FAILED exit=1 ──"
    assert legacy_log_flags(text) == (False, False)
    err = error_from_legacy_text("run_command", text)
    assert err is not None
    assert err.error_class is ErrorClass.TOOL_FAILED
    row = log_row_from_legacy("run_command", {"command": "boom"}, text)
    assert row.ok is False
    assert row.blocked is False


def test_legacy_error_read_file():
    text = "── read_file ERROR: disk exploded ──"
    assert legacy_log_flags(text) == (False, False)
    err = error_from_legacy_text("read_file", text)
    assert err is not None
    assert err.error_class is ErrorClass.TOOL_FAILED
    assert "disk exploded" in err.message


def test_unknown_tool_heuristic_split():
    text = "── unknown native tool: not_a_real_tool ──"
    assert legacy_log_flags(text) == (True, False)
    assert protocol_ok(text) is False
    err = error_from_legacy_text("not_a_real_tool", text)
    assert err is not None
    assert err.error_class is ErrorClass.UNKNOWN_TOOL
    result = result_from_legacy_text("not_a_real_tool", text)
    assert result.ok is True
    assert result.blocked is False
    assert result.error is not None
    assert result.error.error_class is ErrorClass.UNKNOWN_TOOL


def test_permission_denied_flags_ok_typed_error():
    text = format_permission_denied("web_search", tool="native:web_search")
    assert "PERMISSION DENIED" in text
    assert legacy_log_flags(text) == (True, False)
    err = error_from_legacy_text("web_search", text)
    assert err is not None
    assert err.error_class is ErrorClass.PERMISSION_DENIED
    assert err.perm_class == "web_search"
    result = result_from_legacy_text("web_search", text)
    assert result.ok is True
    assert result.blocked is False
    assert result.error is not None
    assert result.error.error_class is ErrorClass.PERMISSION_DENIED


def test_offline_kit_flags_ok_typed_error(monkeypatch):
    monkeypatch.setenv("DAEDALUS_OFFLINE", "1")
    from daedalus import offline

    text = offline.deny("web_search")
    assert text is not None
    assert "OFFLINE KIT" in text
    assert legacy_log_flags(text) == (True, False)
    err = error_from_legacy_text("web_search", text)
    assert err is not None
    assert err.error_class is ErrorClass.OFFLINE
    result = result_from_legacy_text("web_search", text)
    assert result.ok is True
    assert result.blocked is False
    assert result.error is not None
    assert result.error.error_class is ErrorClass.OFFLINE


def test_contract_request_from_runtime_marker():
    runtime = RuntimeRequest(name="web_search", args={"query": "tetris"}, source="marker", marker="NEED_WEB")
    req = contract_request(runtime)
    assert isinstance(req, ToolRequest)
    assert req.name == "web_search"
    assert req.args["query"] == "tetris"
    assert req.source is ToolSource.MARKER
    assert req.marker == "NEED_WEB"
    assert contract_request(req) is req


def test_contract_request_from_marker_web():
    req = contract_request_from_marker("NEED_WEB", "tetris sprites")
    assert req is not None
    assert req.name == "web_search"
    assert req.args["query"] == "tetris sprites"
    assert req.source is ToolSource.MARKER
    assert req.marker == "NEED_WEB"


def test_contract_request_from_marker_unknown():
    assert contract_request_from_marker("NEED_MADE_UP", "x") is None


def test_contract_result_fills_blocked():
    runtime = RuntimeResult(name="code_search", ok=True, text="── code_search BLOCKED ──\nJAIL", source="native")
    result = contract_result(runtime)
    assert isinstance(result, ToolResult)
    assert result.blocked is True
    assert result.ok is True
    assert result.error is not None
    assert result.error.error_class is ErrorClass.TOOL_BLOCKED
    filled = contract_result(runtime, text="── code_search FAILED ──")
    assert filled.text == "── code_search FAILED ──"
    assert filled.blocked is False
    assert filled.ok is True  # runtime ok is preserved
    assert filled.error is not None
    assert filled.error.error_class is ErrorClass.TOOL_FAILED


def test_log_row_from_legacy_blocked_ok():
    row = log_row_from_legacy("code_search", {"query": "x"}, "── code_search BLOCKED ──")
    assert isinstance(row, ToolLogRow)
    assert row.ok is True
    assert row.blocked is True


def test_observation_from_mapping():
    obs = observation_from_mapping(
        {"tool": "code_search", "ok": True, "blocked": True, "input": {"query": "x"}, "head": "BLOCKED"}
    )
    assert isinstance(obs, Observation)
    assert Observation is ToolObservation
    assert obs.tool == "code_search"
    assert obs.ok is True
    assert obs.blocked is True
    assert obs.args["query"] == "x"
    assert obs.error is not None
    assert obs.error.error_class is ErrorClass.TOOL_BLOCKED


def test_live_specs_match_native():
    from daedalus.native_tools import NATIVE_TOOL_SPECS

    specs = live_specs()
    names = [s.name for s in specs]
    native = [str(t["name"]) for t in NATIVE_TOOL_SPECS]
    assert names == native
    assert len(specs) == len(NATIVE_TOOL_SPECS)


def test_live_specs_network_and_perm():
    from daedalus.native_tools import PERM_MAP

    by = {s.name: s for s in live_specs()}
    for name, spec in by.items():
        assert spec.network is (name in NETWORK_TOOLS)
        mapped = PERM_MAP.get(name)
        if mapped:
            assert spec.permission is not None
            assert spec.permission.perm_class == mapped
            assert spec.permission.fail_open is True
        else:
            assert spec.permission is None
    assert by["web_search"].network is True
    assert by["read_file"].network is False
    assert by["generate_image"].network is True
    assert by["git_forge"].permission is not None
    assert by["git_forge"].permission.perm_class == "github_access"


def test_spec_from_native_unknown_defaults():
    spec = spec_from_native({"name": "not_a_real_tool", "description": "x"})
    assert spec.side_effect.value == "read"
    assert spec.risk.value == "low"
    assert spec.permission is None
    assert spec.network is False


def test_import_adapt_does_not_load_agent():
    env = os.environ.copy()
    env["DAEDALUS_STANDALONE"] = "1"
    env["PYTHONPATH"] = str(ROOT) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    code = (
        "import daedalus.tool_adapt\n"
        "import sys\n"
        "bad = [n for n in sys.modules if n == 'daedalus.agent' or n.startswith('daedalus.agent.')]\n"
        "assert not bad, bad\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout


def test_agent_and_native_tools_do_not_import_adapt():
    for rel in ("daedalus/agent.py", "daedalus/native_tools.py"):
        tree = ast.parse((ROOT / rel).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name != "daedalus.tool_adapt"
                    assert "tool_adapt" not in alias.name
            elif isinstance(node, ast.ImportFrom) and node.module:
                assert node.module != "daedalus.tool_adapt"
                assert not node.module.endswith(".tool_adapt")
                assert "tool_adapt" not in (node.module or "")
            elif isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    assert alias.name != "tool_adapt"
