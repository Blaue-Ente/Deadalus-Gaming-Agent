"""VerificationController — verify spine façade. No live LLM."""
from __future__ import annotations

import ast
import asyncio
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from daedalus.contracts import RunStage, StageRow
from daedalus.verification_controller import (
    VerificationController,
    VerifyRequest,
    VerifyResult,
)

ROOT = Path(__file__).resolve().parents[1]

SNAKE = (
    "Направи нова Playground игра snake: HTML canvas, стрелки, расте при ябълка, "
    "game over при стена/опашка. Една завършена среза."
)


def test_import_controller_does_not_load_agent():
    env_code = (
        "import daedalus.verification_controller as vc\n"
        "import sys\n"
        "bad = [n for n in sys.modules if n == 'daedalus.agent' or n.startswith('daedalus.agent.')]\n"
        "assert not bad, bad\n"
        "assert vc.VerificationController is not None\n"
    )
    env = os.environ.copy()
    env["DAEDALUS_STANDALONE"] = "1"
    env["PYTHONPATH"] = str(ROOT) + (os.pathsep + env.get("PYTHONPATH", ""))
    result = subprocess.run(
        [sys.executable, "-c", env_code],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout


def test_leaf_does_not_import_agent_or_finalizer():
    tree = ast.parse((ROOT / "daedalus" / "verification_controller.py").read_text(encoding="utf-8"))
    forbidden_modules = {
        "daedalus.agent",
        "daedalus.native_tools",
        "daedalus.tool_loop",
        "daedalus.improvements",
        "daedalus.batch_verify",
        "daedalus.kernel.full_autopilot",
        "daedalus.game_studio_verify",
        "daedalus.run_finalizer",
    }
    forbidden_names = {
        "agent",
        "Daedalus",
        "review_gate_critique",
        "execute_verify",
        "record_outcome",
        "ToolLoop",
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name not in forbidden_modules
                assert not alias.name.startswith("daedalus.agent.")
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            assert module not in forbidden_modules
            assert not module.startswith("daedalus.agent")
            for alias in node.names:
                assert alias.name not in forbidden_names


def test_legacy_adapter_does_not_import_controller():
    tree = ast.parse((ROOT / "daedalus" / "legacy_adapter.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name != "daedalus.verification_controller"
                assert "verification_controller" not in alias.name
        elif isinstance(node, ast.ImportFrom) and node.module:
            assert node.module != "daedalus.verification_controller"
            assert not node.module.endswith(".verification_controller")
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                assert alias.name != "verification_controller"
                assert alias.name != "VerificationController"


def test_tool_loop_does_not_import_controller():
    src = (ROOT / "daedalus" / "tool_loop.py").read_text(encoding="utf-8")
    assert "verification_controller" not in src
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            assert node.module != "daedalus.verification_controller"
            for alias in node.names:
                assert alias.name != "VerificationController"


def test_agent_wires_finalizer_and_keeps_leftover_verify():
    src = (ROOT / "daedalus" / "agent.py").read_text(encoding="utf-8")
    assert "RunFinalizer" in src
    assert "after_loop" in src
    assert "finish_chat" in src
    assert "execute_verify" in src
    assert "NEED_VERIFY" in src
    fin = (ROOT / "daedalus" / "run_finalizer.py").read_text(encoding="utf-8")
    assert "VerificationController" in fin
    assert ".accept(" in fin
    assert "finish_nudge" in fin
    assert "empty-reply guard" in fin
    assert "review_gate_critique" in fin


def test_stage_row_skipped_is_not_ok():
    with pytest.raises(ValidationError):
        StageRow(stage="visual_verify", ok=True, skipped=True)


def test_accept_matches_job_contract_static_preview():
    dod = VerificationController().accept(
        VerifyRequest(
            ask=SNAKE,
            reply="Готово.",
            tool_calls=[{"tool": "write_file", "ok": True}],
            kind="static",
        )
    )
    assert dod.coding_task is True
    assert dod.checks["wrote_files"] is True
    assert dod.checks["verified"] is True
    assert dod.complete is True


def test_accept_chat_is_not_a_coding_task():
    dod = VerificationController().accept(
        VerifyRequest(ask="как си днес?", reply="Добре, благодаря.")
    )
    assert dod.coding_task is False
    assert dod.complete is True


def test_accept_coding_without_smoke_is_not_done():
    dod = VerificationController().accept(
        VerifyRequest(ask="напиши snake игра", reply="Готово.", tool_calls=[])
    )
    assert dod.coding_task is True
    assert dod.complete is False
    assert dod.blockers


def test_accept_browser_ok_false_blocks_visual_kind():
    dod = VerificationController().accept(
        VerifyRequest(
            ask=SNAKE,
            reply="Готово.",
            tool_calls=[{"tool": "write_file", "ok": True}],
            kind="static",
            browser_ok=False,
        )
    )
    assert dod.complete is False
    assert dod.checks["browser"] is False


def test_accept_injected_visual_uses_evaluate_build(monkeypatch):
    dod = VerificationController().accept(
        VerifyRequest(
            ask=SNAKE,
            reply="Готово.",
            tool_calls=[{"tool": "write_file", "ok": True}],
            kind="godot",
            visual={"ok": False, "skipped": True, "summary": "godot missing"},
        )
    )
    assert dod.complete is False


def test_accept_skips_qa_when_no_evidence(monkeypatch):
    hits: list[str] = []

    def _qa(*_a, **_k):
        hits.append("qa")
        return {"ok": False, "release": {"approved": False, "blockers": ["x"]}}

    monkeypatch.setattr("daedalus.capability.load_evidence", lambda _root: None)
    monkeypatch.setattr("daedalus.playground.project_dir", lambda slug: Path("/tmp"))
    monkeypatch.setattr("daedalus.platform_acceptance.run_qa", _qa)
    dod = VerificationController().accept(
        VerifyRequest(
            ask=SNAKE,
            reply="Готово.",
            tool_calls=[{"tool": "write_file", "ok": True}],
            kind="static",
            slug="snake",
        )
    )
    assert hits == []
    assert dod.complete is True


def test_accept_merges_qa_blockers_when_evidence_exists(monkeypatch):
    monkeypatch.setattr("daedalus.capability.load_evidence", lambda _root: {"target": "web"})
    monkeypatch.setattr("daedalus.playground.project_dir", lambda slug: Path("/tmp"))
    monkeypatch.setattr(
        "daedalus.platform_acceptance.run_qa",
        lambda *_a, **_k: {
            "ok": False,
            "release": {"approved": False, "blockers": ["no_gameplay_proof"]},
        },
    )
    dod = VerificationController().accept(
        VerifyRequest(
            ask=SNAKE,
            reply="Готово.",
            tool_calls=[{"tool": "write_file", "ok": True}],
            kind="static",
            slug="snake",
        )
    )
    assert dod.complete is False
    assert "no_gameplay_proof" in dod.blockers


def test_static_check_lint_blockers_fail(monkeypatch):
    monkeypatch.setattr(
        "daedalus.game_lint.check",
        lambda *_a, **_k: {
            "ok": False,
            "blockers": [{"code": "js-dup", "message": "duplicate let"}],
            "warnings": [],
        },
    )
    row = asyncio.run(
        VerificationController().static_check(VerifyRequest(files=["game.js"], slug="snake"))
    )
    assert row.stage == RunStage.STATIC_VERIFY.value
    assert row.ok is False
    assert "duplicate let" in row.blockers


def test_static_check_warnings_do_not_fail(monkeypatch):
    monkeypatch.setattr(
        "daedalus.game_lint.check",
        lambda *_a, **_k: {
            "ok": True,
            "blockers": [],
            "warnings": [{"message": "style"}],
        },
    )
    row = asyncio.run(
        VerificationController().static_check(VerifyRequest(files=["game.js"], slug="snake"))
    )
    assert row.ok is True
    assert row.skipped is False


def test_static_check_scan_warnings_do_not_fail(monkeypatch):
    monkeypatch.setattr(
        "daedalus.game_engineer.scan_project",
        lambda slug: {"ok": False, "warnings": ["hot path O(N^2)"]},
    )
    row = asyncio.run(VerificationController().static_check(VerifyRequest(slug="snake")))
    assert row.ok is True


def test_run_tests_skip_is_not_ok():
    row = asyncio.run(
        VerificationController().run_tests(
            VerifyRequest(slug="snake", kind="static", smoke={"ok": True, "skipped": True})
        )
    )
    assert row.ok is False
    assert row.skipped is True
    assert row.stage == RunStage.ENGINE_TEST.value


def test_visual_proof_chromium_gap(monkeypatch):
    async def _proof(slug, kind="static"):
        return SimpleNamespace(
            ok=False,
            skipped=True,
            kind=kind,
            slug=slug,
            tool="browser",
            summary="no chrome",
            honest_gap="chromium_missing",
            error="chromium_missing",
            as_dict=lambda: {
                "ok": False,
                "skipped": True,
                "summary": "no chrome",
                "error": "chromium_missing",
                "honest_gap": "chromium_missing",
            },
        )

    monkeypatch.setattr("daedalus.visual_proof.run_visual_proof", _proof)
    row = asyncio.run(
        VerificationController().visual_proof(VerifyRequest(slug="snake-game", kind="static"))
    )
    assert row.ok is False
    assert row.skipped is True
    assert "chromium" in (row.error + " " + " ".join(row.blockers)).lower()


def test_game_verify_failure(monkeypatch):
    async def _gameplay(slug, kind, **_k):
        return {"ok": False, "skipped": False, "summary": "no input"}

    monkeypatch.setattr("daedalus.engine_runners.gameplay_for_kind", _gameplay)
    row = asyncio.run(
        VerificationController().game_verify(VerifyRequest(slug="snake", kind="static"))
    )
    assert row.ok is False
    assert "no input" in row.blockers


def test_run_stops_at_first_failed_required_stage(monkeypatch):
    monkeypatch.setattr(
        "daedalus.game_lint.check",
        lambda *_a, **_k: {
            "ok": False,
            "blockers": [{"message": "syntax"}],
            "warnings": [],
        },
    )
    compile_hits: list[str] = []

    async def _compile(slug, kind):
        compile_hits.append(slug)
        return {"ok": True, "skipped": False}

    monkeypatch.setattr("daedalus.verification_controller._compile_kind", _compile)
    result = asyncio.run(
        VerificationController().run(
            VerifyRequest(slug="snake", kind="static", files=["game.js"], ask=SNAKE)
        )
    )
    assert result.ok is False
    assert result.stages[0].stage == RunStage.STATIC_VERIFY.value
    assert result.stages[0].ok is False
    assert len(result.stages) == 1
    assert compile_hits == []
    assert result.next_stage is RunStage.STATIC_VERIFY


def test_run_omits_visual_for_fastapi(monkeypatch):
    proof_hits: list[str] = []

    async def _proof(*_a, **_k):
        proof_hits.append("proof")
        raise AssertionError("fastapi must not claim visual proof")

    async def _smoke(slug, **_k):
        return {"ok": True, "skipped": False}

    monkeypatch.setattr("daedalus.visual_proof.run_visual_proof", _proof)
    monkeypatch.setattr("daedalus.playground_runner.smoke_test", _smoke)
    result = asyncio.run(
        VerificationController().run(
            VerifyRequest(slug="api", kind="fastapi", ask="add a health endpoint")
        )
    )
    assert proof_hits == []
    assert not any(row.stage == RunStage.VISUAL_VERIFY.value for row in result.stages)
    assert any(row.stage == RunStage.ENGINE_TEST.value for row in result.stages)


def test_run_result_shape():
    result = VerifyResult(ok=True, stages=[], blockers=[])
    assert result.dod is None
    assert result.qa is None
    assert result.next_stage is None
