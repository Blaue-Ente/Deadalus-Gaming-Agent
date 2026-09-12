"""P0.22 release-gate aggregator. No live LLM, no DinD, no live engines."""
from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = ROOT / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import p0_release_gate as gate  # noqa: E402

from daedalus.game_agent_bench import P0_22_HOOKS


def test_gate_evaluate_ok():
    report = gate.evaluate(root=ROOT)
    assert report["ok"] is True, report.get("failed")
    assert report["bake_off"] is False
    assert report["live_required_for_release"] is False
    assert report["gate"] == "p0_release_gate"
    assert report["failed"] == []
    assert report["criteria"]["benchmark_no_worse"] is True
    assert report["criteria"]["false_success_reduced"] is True
    assert report["criteria"]["command_isolated"] is True
    assert report["criteria"]["run_engine_resume"] is True
    assert report["criteria"]["typed_protocol"] is True
    assert report["criteria"]["critical_tests_green"] is True
    assert report["criteria"]["old_public_api"] is True
    assert report["human_merge"]["status"] == "pending"
    assert report["branch_protection"]["status"] == "pending"
    ids = [row["id"] for row in report["checks"]]
    assert ids == [
        "p0_00_pack",
        "honesty_15",
        "game_v1_20",
        "skip_is_not_ok",
        "docker_refuse",
        "run_engine_resume",
        "typed_protocol",
        "mcp_enforce",
        "public_api",
        "human_merge",
        "branch_protection",
    ]
    by_id = {row["id"]: row for row in report["checks"]}
    assert by_id["human_merge"]["blocking"] is False
    assert by_id["branch_protection"]["blocking"] is False


def test_gate_cli_zero(capsys):
    code = gate.main(["--json"])
    out = capsys.readouterr().out
    assert code == 0
    body = json.loads(out)
    assert body["ok"] is True
    assert body["failed"] == []
    assert "p0_release_gate" in body["gate"]


def test_game_snapshot_committed():
    path = ROOT / P0_22_HOOKS["game_artifact"]
    assert path.is_file(), "commit artifacts/baseline/game-benchmark-results.json"
    snap = json.loads(path.read_text(encoding="utf-8"))
    assert snap["ok"] is True
    assert snap["bake_off"] is False
    assert snap["live"] is False
    assert snap["total"] == P0_22_HOOKS["game_task_count"] == 20
    assert snap["failed"] == []
    assert "path" not in snap


def test_hooks_note_names_aggregator():
    note = str(P0_22_HOOKS["note"]).lower()
    assert "scripts/p0_release_gate.py" in note
    assert "does not implement" not in note
    assert P0_22_HOOKS["live_required_for_release"] is False
    assert P0_22_HOOKS["honesty_case_count"] == 15
    assert P0_22_HOOKS["game_task_count"] == 20


def test_missing_game_snapshot_fails(tmp_path):
    row = gate.check_game_v1(tmp_path, dict(P0_22_HOOKS))
    assert row.ok is False
    assert row.blocking is True
    assert "missing game snapshot" in row.detail


def test_human_rows_do_not_fail_gate():
    merge = gate.check_human_merge()
    prot = gate.check_branch_protection()
    assert merge.ok is True and merge.blocking is False
    assert prot.ok is True and prot.blocking is False
    assert merge.extra.get("status") == "pending"
    assert prot.extra.get("status") == "pending"


def test_leftover_ladder_still_in_tree():
    loop = (ROOT / "daedalus" / "tool_loop.py").read_text(encoding="utf-8")
    assert "execute_leftover_fn" in loop
    assert "NEED_PROPOSE_START" in loop
    row = gate.check_typed_protocol(ROOT)
    assert row.ok is True
    assert "leftover present" in row.detail


def test_gate_module_does_not_import_agent():
    tree = ast.parse((ROOT / "scripts" / "p0_release_gate.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name != "daedalus.agent"
                assert not alias.name.startswith("daedalus.agent.")
        elif isinstance(node, ast.ImportFrom) and node.module:
            assert node.module != "daedalus.agent"
            assert not node.module.startswith("daedalus.agent.")


def test_chat_return_keys_match_characterization():
    row = gate.check_public_api(ROOT)
    assert row.ok is True
    assert list(gate.EXPECTED_CHAT_RETURN_KEYS) == [
        "agent",
        "session_id",
        "for_self",
        "reply",
        "context",
        "pending_proposal",
        "task_events",
        "model",
        "effort",
        "elapsed_seconds",
        "timestamp",
        "complete",
        "dod",
        "run_budget",
    ]


def test_gate_does_not_persist_bench(tmp_path, monkeypatch):
    monkeypatch.setenv("DAEDALUS_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("DAEDALUS_STANDALONE", "1")
    from daedalus.paths import data_root

    data_root.cache_clear()
    report = gate.evaluate(root=ROOT)
    assert report["ok"] is True
    leftover = tmp_path / "data" / "bench"
    assert not leftover.exists()
    data_root.cache_clear()
