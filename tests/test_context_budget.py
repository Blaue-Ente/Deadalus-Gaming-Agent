"""ContextBudget leaf — window account / optional trim. No live LLM."""
from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from daedalus.contracts import (
    ContextBudgetReport,
    ContextSlice,
    ContextWindow,
    Run,
    RunCheckpoint,
)
from daedalus.context_budget import (
    DEFAULT_RESERVE_OUTPUT,
    BudgetRequest,
    ContextBudget,
    estimate,
)
from daedalus.paths import data_root, playground_projects_dir

ROOT = Path(__file__).resolve().parents[1]
LEAF = ROOT / "daedalus" / "context_budget.py"
FORBIDDEN_IMPORTS = frozenset({
    "daedalus.agent",
    "daedalus.native_tools",
    "daedalus.job_ledger",
    "daedalus.run_engine",
    "daedalus.scratch_journal",
    "daedalus.context_assembler",
    "daedalus.model_registry",
    "daedalus.model_gateway",
})


def _isolate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DAEDALUS_STANDALONE", "1")
    monkeypatch.setenv("DAEDALUS_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("DAEDALUS_PLAYGROUND", str(tmp_path / "projects"))
    monkeypatch.delenv("DAEDALUS_CONTEXT_WINDOW", raising=False)
    monkeypatch.delenv("DAEDALUS_BENCH_LIVE", raising=False)
    data_root.cache_clear()
    playground_projects_dir.cache_clear()


def _usable(window_tokens: int, reserve_output: int = DEFAULT_RESERVE_OUTPUT) -> int:
    headroom = max(4000, int(window_tokens * 0.10))
    return window_tokens - reserve_output - headroom


def test_contract_skip_cannot_be_ok():
    with pytest.raises(ValidationError):
        ContextSlice(name="memory", kind="memory", tokens_est=1, skipped=True, ok=True)
    with pytest.raises(ValidationError):
        ContextWindow(window_tokens=32_000, skipped=True, ok=True)
    with pytest.raises(ValidationError):
        ContextBudgetReport(
            window=ContextWindow(window_tokens=32_000, ok=True),
            ok=True,
            skipped=True,
        )
    with pytest.raises(ValidationError):
        ContextSlice.model_validate(
            {"name": "owner", "kind": "owner", "tokens_est": 1, "nope": 1}
        )
    row = ContextSlice(name="owner", kind="owner", tokens_est=1, skipped=True, ok=False)
    assert row.schema_version == 1
    assert ContextSlice.model_validate(row.model_dump(mode="json")) == row
    window = ContextWindow(
        model="x",
        window_tokens=32_000,
        reserve_output=8192,
        reserve_headroom=4000,
        usable_tokens=19_808,
        source="request",
        skipped=False,
        ok=True,
    )
    assert window.schema_version == 1
    report = ContextBudgetReport(window=window, ok=True, skipped=False)
    assert report.schema_version == 1
    assert ContextBudgetReport.model_validate(report.model_dump(mode="json")) == report


def test_run_schema_unchanged():
    assert "context" not in Run.model_fields
    assert "context_budget" not in Run.model_fields
    assert "context" not in RunCheckpoint.model_fields
    assert "context_budget" not in RunCheckpoint.model_fields
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
        "scratch_journal",
        "context_assembler",
        "model_registry",
        "model_gateway",
        "native_tools",
        "job_ledger",
        "run_engine",
    ):
        assert f"daedalus.{bad}" not in src
        assert f"from .{bad}" not in src
    code = (
        "import daedalus.context_budget as cb\n"
        "import sys\n"
        "bad = [n for n in sys.modules if n in "
        "('daedalus.agent', 'daedalus.native_tools', 'daedalus.job_ledger', "
        "'daedalus.run_engine', 'daedalus.scratch_journal', "
        "'daedalus.context_assembler', 'daedalus.model_registry', "
        "'daedalus.model_gateway') "
        "or n.startswith('daedalus.agent.') "
        "or n.startswith('daedalus.native_tools.') "
        "or n.startswith('daedalus.job_ledger.') "
        "or n.startswith('daedalus.run_engine.') "
        "or n.startswith('daedalus.scratch_journal.') "
        "or n.startswith('daedalus.context_assembler.') "
        "or n.startswith('daedalus.model_registry.') "
        "or n.startswith('daedalus.model_gateway.')]\n"
        "assert not bad, bad\n"
        "assert cb.estimate('abcd') == 1\n"
        "print('import-ok')\n"
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


def test_agent_and_loops_untouched():
    agent = (ROOT / "daedalus" / "agent.py").read_text(encoding="utf-8")
    native = (ROOT / "daedalus" / "native_tools.py").read_text(encoding="utf-8")
    loop = (ROOT / "daedalus" / "tool_loop.py").read_text(encoding="utf-8")
    spend = (ROOT / "daedalus" / "run_budget.py").read_text(encoding="utf-8")
    assembler = (ROOT / "daedalus" / "context_assembler.py").read_text(encoding="utf-8")
    for blob in (agent, native, loop, spend, assembler):
        assert "context_budget" not in blob
        assert "ContextBudget" not in blob
    assert "NEED_PROPOSE_START" in agent
    assert "NEED_SANDBOX" in agent
    assert "NEED_VERIFY" in agent


def test_estimate_char_quarter():
    assert estimate("abcd") == 1
    assert estimate("") == 0
    assert estimate(None) == 0
    assert ContextBudget().estimate("abcdefgh") == 2


def test_resolve_window_request_env_table_unknown(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    leaf = ContextBudget()
    requested = leaf.resolve_window(BudgetRequest(window_tokens=64_000, model="ignored"))
    assert requested.source == "request"
    assert requested.window_tokens == 64_000
    assert requested.usable_tokens == _usable(64_000)
    assert requested.skipped is False
    assert requested.ok is True

    monkeypatch.setenv("DAEDALUS_CONTEXT_WINDOW", "48000")
    env_row = leaf.resolve_window(BudgetRequest(model="claude-sonnet-4-6"))
    assert env_row.source == "env"
    assert env_row.window_tokens == 48_000
    assert env_row.ok is True
    monkeypatch.delenv("DAEDALUS_CONTEXT_WINDOW")

    table = leaf.resolve_window(BudgetRequest(model="anthropic/claude-sonnet-4-5"))
    assert table.source == "table"
    assert table.window_tokens == 200_000
    assert table.usable_tokens == _usable(200_000)
    assert table.ok is True

    unknown = leaf.resolve_window(BudgetRequest(model="totally-unknown-xyz"))
    assert unknown.source == "unknown"
    assert unknown.skipped is True
    assert unknown.ok is False


def test_account_fits_under_window(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    report = ContextBudget().account(
        BudgetRequest(
            window_tokens=32_000,
            parts={"owner": "hi", "system": "persona"},
        )
    )
    assert report.ok is True
    assert report.skipped is False
    assert report.overflow == 0
    assert report.trimmed is False
    assert report.tokens_est == estimate("hi") + estimate("persona")
    assert report.tokens_kept == report.tokens_est
    assert report.window.usable_tokens == _usable(32_000)
    assert report.persisted == ""
    names = [row.name for row in report.slices]
    assert names[-1] == "reserve"


def test_account_overflow_keeps_slices(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    memory = "m" * 100_000
    report = ContextBudget().account(
        BudgetRequest(
            window_tokens=32_000,
            parts={"owner": "ask", "system": "persona", "memory": memory},
            trim=False,
        )
    )
    assert report.ok is False
    assert report.skipped is False
    assert report.trimmed is False
    assert report.overflow > 0
    assert "overflow" in report.blockers
    by_name = {row.name: row for row in report.slices}
    assert by_name["memory"].kept is True
    assert by_name["owner"].kept is True
    assert by_name["system"].kept is True
    assert report.tokens_kept == report.tokens_est


def test_pack_trim_drops_low_priority(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    parts = {
        "owner": "ask",
        "system": "persona",
        "memory": "m" * 100_000,
        "history": "h" * 20_000,
    }
    texts, report = ContextBudget().pack(
        BudgetRequest(window_tokens=32_000, parts=parts, trim=True)
    )
    assert report.trimmed is True
    assert report.skipped is False
    assert report.ok is True
    assert report.overflow == 0
    by_name = {row.name: row for row in report.slices}
    assert by_name["owner"].kept is True
    assert by_name["system"].kept is True
    assert by_name["memory"].kept is False
    assert by_name["history"].kept is False
    assert "owner" in texts
    assert "system" in texts
    assert "memory" not in texts
    assert "history" not in texts
    assert report.tokens_est > report.tokens_kept
    assert report.tokens_kept <= report.window.usable_tokens


def test_pack_never_drops_owner(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    texts, report = ContextBudget().pack(
        BudgetRequest(
            window_tokens=32_000,
            parts={"owner": "O" * 100_000},
            trim=True,
        )
    )
    by_name = {row.name: row for row in report.slices}
    assert by_name["owner"].kept is True
    assert "owner" in texts
    assert texts["owner"]
    assert report.ok is False
    assert report.skipped is False
    assert report.overflow > 0


def test_empty_pack_skip_not_ok(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    report = ContextBudget().account(BudgetRequest(window_tokens=32_000, parts={}))
    assert report.skipped is True
    assert report.ok is False
    assert "empty_pack" in report.blockers
    blank = ContextBudget().account(
        BudgetRequest(window_tokens=32_000, parts={"owner": ""})
    )
    assert blank.skipped is True
    assert blank.ok is False


def test_persist_under_data_context(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    report = ContextBudget().account(
        BudgetRequest(
            run_id="run-ctx-1",
            window_tokens=32_000,
            parts={"owner": "hi"},
            persist=True,
        )
    )
    path = Path(report.persisted)
    assert path.is_file()
    assert path == data_root() / "context" / "run-ctx-1.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["run_id"] == "run-ctx-1"
    assert payload["ok"] is True
    assert payload["schema_version"] == 1


def test_persist_false_writes_nothing(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    report = ContextBudget().account(
        BudgetRequest(
            run_id="run-ctx-off",
            window_tokens=32_000,
            parts={"owner": "hi"},
            persist=False,
        )
    )
    assert report.persisted == ""
    assert not (data_root() / "context" / "run-ctx-off.json").exists()
