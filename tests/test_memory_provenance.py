"""MemoryProvenance leaf — label retrieved rows. No live LLM / Chroma."""
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
    ContextSlice,
    MemoryKind,
    MemoryOrigin,
    MemoryProvenanceRecord,
    MemoryProvenanceReport,
    Run,
    RunCheckpoint,
)
from daedalus.memory_provenance import MemoryProvenance, ProvenanceRequest
from daedalus.paths import data_root, playground_projects_dir

ROOT = Path(__file__).resolve().parents[1]
LEAF = ROOT / "daedalus" / "memory_provenance.py"
FORBIDDEN_IMPORTS = frozenset({
    "daedalus.agent",
    "daedalus.native_tools",
    "daedalus.job_ledger",
    "daedalus.run_engine",
    "daedalus.memory",
    "daedalus.memory_mem0",
    "daedalus.knowledge",
    "daedalus.knowledge_books",
    "daedalus.context_assembler",
    "daedalus.context_budget",
    "daedalus.scratch_journal",
    "daedalus.reflection",
    "daedalus.learned_skills",
    "daedalus.preferences",
    "daedalus.model_registry",
    "daedalus.model_gateway",
})


def _isolate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DAEDALUS_STANDALONE", "1")
    monkeypatch.setenv("DAEDALUS_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("DAEDALUS_PLAYGROUND", str(tmp_path / "projects"))
    monkeypatch.delenv("DAEDALUS_BENCH_LIVE", raising=False)
    data_root.cache_clear()
    playground_projects_dir.cache_clear()


def test_contract_skip_cannot_be_ok():
    with pytest.raises(ValidationError):
        MemoryProvenanceRecord(kind="lesson", skipped=True, ok=True)
    with pytest.raises(ValidationError):
        MemoryProvenanceReport(ok=True, skipped=True)
    with pytest.raises(ValidationError):
        MemoryProvenanceRecord.model_validate(
            {"source_id": "les:1", "kind": "lesson", "nope": 1}
        )
    row = MemoryProvenanceRecord(
        source_id="les:1",
        kind=MemoryKind.LESSON.value,
        skipped=True,
        ok=False,
    )
    assert row.schema_version == 1
    assert MemoryProvenanceRecord.model_validate(row.model_dump(mode="json")) == row
    report = MemoryProvenanceReport(ok=True, skipped=False)
    assert report.schema_version == 1
    assert MemoryProvenanceReport.model_validate(report.model_dump(mode="json")) == report


def test_run_schema_unchanged():
    assert "memory" not in Run.model_fields
    assert "provenance" not in Run.model_fields
    assert "source_ids" not in Run.model_fields
    assert "memory" not in RunCheckpoint.model_fields
    assert "provenance" not in RunCheckpoint.model_fields
    assert "source_ids" not in RunCheckpoint.model_fields
    with pytest.raises(ValidationError):
        Run.model_validate({"run_id": "r", "nope": 1})


def test_context_slice_has_no_source_ids():
    assert "source_id" not in ContextSlice.model_fields
    assert "source_ids" not in ContextSlice.model_fields
    with pytest.raises(ValidationError):
        ContextSlice.model_validate(
            {"name": "memory", "kind": "memory", "tokens_est": 1, "source_ids": ["x"]}
        )


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
        "memory_mem0",
        "knowledge_books",
        "context_assembler",
        "context_budget",
        "scratch_journal",
        "learned_skills",
        "model_registry",
        "model_gateway",
        "native_tools",
        "job_ledger",
        "run_engine",
    ):
        assert f"daedalus.{bad}" not in src
        assert f"from .{bad}" not in src
    assert "from daedalus.memory import" not in src
    assert "from daedalus.knowledge import" not in src
    assert "from daedalus.reflection import" not in src
    assert "from daedalus.preferences import" not in src
    code = (
        "import daedalus.memory_provenance as mp\n"
        "import sys\n"
        "bad = [n for n in sys.modules if n in "
        "('daedalus.agent', 'daedalus.native_tools', 'daedalus.job_ledger', "
        "'daedalus.run_engine', 'daedalus.memory', 'daedalus.memory_mem0', "
        "'daedalus.knowledge', 'daedalus.knowledge_books', "
        "'daedalus.context_assembler', 'daedalus.context_budget', "
        "'daedalus.scratch_journal', 'daedalus.reflection', "
        "'daedalus.learned_skills', 'daedalus.preferences', "
        "'daedalus.model_registry', 'daedalus.model_gateway') "
        "or n.startswith('daedalus.agent.') "
        "or n.startswith('daedalus.native_tools.') "
        "or n.startswith('daedalus.memory.') "
        "or n.startswith('daedalus.memory_mem0.') "
        "or n.startswith('daedalus.context_assembler.') "
        "or n.startswith('daedalus.context_budget.')]\n"
        "assert not bad, bad\n"
        "from daedalus.memory_provenance import ProvenanceRequest\n"
        "rep = mp.label(ProvenanceRequest(items=[]))\n"
        "assert rep.skipped and not rep.ok\n"
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
    assembler = (ROOT / "daedalus" / "context_assembler.py").read_text(encoding="utf-8")
    budget = (ROOT / "daedalus" / "context_budget.py").read_text(encoding="utf-8")
    memory = (ROOT / "daedalus" / "memory.py").read_text(encoding="utf-8")
    for blob in (agent, native, loop, assembler, budget, memory):
        assert "memory_provenance" not in blob
        assert "MemoryProvenance" not in blob
    assert "NEED_PROPOSE_START" in agent
    assert "NEED_SANDBOX" in agent
    assert "NEED_VERIFY" in agent


def test_label_knowledge_row(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    report = MemoryProvenance().label(
        ProvenanceRequest(
            items=[{
                "id": "skill:arcade_craft#0",
                "content": "body",
                "slice_name": "memory",
                "metadata": {
                    "category": "skill",
                    "title": "Arcade",
                    "source": "daedalus/skills/arcade_craft.md",
                },
            }],
        )
    )
    assert report.ok is True
    assert report.skipped is False
    assert report.labeled == 1
    row = report.items[0]
    assert row.source_id == "skill:arcade_craft#0"
    assert row.kind == MemoryKind.KNOWLEDGE.value
    assert row.origin == MemoryOrigin.KNOWLEDGE_SCAN.value
    assert row.collection == "daedalus_global"
    assert row.locator == "daedalus/skills/arcade_craft.md"
    assert row.slice_name == "memory"


def test_label_lesson_rejection_preference(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    report = MemoryProvenance().label(
        ProvenanceRequest(
            session_id="sess-1",
            items=[
                {
                    "id": "les:abc123def456",
                    "content": "Prefer Phaser",
                    "slice_name": "lessons",
                    "metadata": {
                        "kind": "preference",
                        "confidence": 0.8,
                        "session_id": "sess-1",
                        "timestamp": "2026-01-01T00:00:00Z",
                    },
                },
                {
                    "id": "reject-p1-1",
                    "content": "REJECTED",
                    "metadata": {
                        "type": "rejection_lesson",
                        "skill": "arcade",
                        "reject_reason": "no",
                    },
                },
                {
                    "id": "pref_deadbeef01",
                    "content": "OWNER PREFERENCE",
                    "metadata": {"type": "preference", "kind": "stack", "source": "explicit"},
                },
            ],
        )
    )
    assert report.ok is True
    assert report.labeled == 3
    lesson, rejection, pref = report.items
    assert lesson.kind == MemoryKind.LESSON.value
    assert lesson.origin == MemoryOrigin.REFLECTION.value
    assert lesson.collection == "daedalus_lessons"
    assert rejection.kind == MemoryKind.LESSON.value
    assert rejection.origin == MemoryOrigin.REJECTION.value
    assert pref.kind == MemoryKind.PREFERENCE.value
    assert pref.origin == MemoryOrigin.EXPLICIT.value


def test_ep_prefix_collision(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    report = MemoryProvenance().label(
        ProvenanceRequest(
            items=[
                {
                    "id": "ep:sess-1:aaaaaaaaaa",
                    "content": "session summary",
                    "slice_name": "episodes",
                    "metadata": {
                        "session_id": "sess-1",
                        "turn_count": 10,
                        "started_at": "2026-01-01T00:00:00Z",
                        "stored_at": "2026-01-01T01:00:00Z",
                    },
                },
                {
                    "id": "ep:sess-1:bbbbbbbbbbbb",
                    "content": "extracted fact",
                    "source": "episodic",
                    "slice_name": "lessons",
                    "metadata": {
                        "session_id": "sess-1",
                        "role": "user",
                        "ts": 1_700_000_000.0,
                        "access_count": 2,
                        "importance": 1.2,
                    },
                },
            ],
        )
    )
    episode, fact = report.items
    assert episode.kind == MemoryKind.EPISODE.value
    assert episode.collection == "daedalus_episodes"
    assert episode.origin == MemoryOrigin.CHROMA.value
    assert fact.kind == MemoryKind.EPISODIC_FACT.value
    assert fact.collection == "daedalus_episodic"
    assert fact.origin == MemoryOrigin.MEM0.value
    assert episode.source_id.startswith("ep:")
    assert fact.source_id.startswith("ep:")


def test_working_and_hybrid_tier(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    report = MemoryProvenance().label(
        ProvenanceRequest(
            items=[
                {
                    "id": "wm:abcd1234",
                    "content": "recent turn",
                    "source": "working",
                    "metadata": {"session_id": "sess-2"},
                },
                {
                    "id": "orphan-hybrid",
                    "content": "fused",
                    "source": "hybrid",
                    "metadata": {},
                },
            ],
        )
    )
    working, hybrid = report.items
    assert working.kind == MemoryKind.WORKING.value
    assert working.origin == MemoryOrigin.MEM0.value
    assert working.ok is True
    assert hybrid.kind == MemoryKind.UNKNOWN.value
    assert hybrid.origin == MemoryOrigin.UNKNOWN.value
    assert hybrid.ok is True
    assert hybrid.skipped is False


def test_missing_id_skip_not_ok(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    report = MemoryProvenance().label(
        ProvenanceRequest(
            items=[{"content": "no id", "metadata": {"kind": "general"}}],
        )
    )
    assert report.ok is False
    assert report.skipped is False
    assert report.skipped_count == 1
    assert "missing_id" in report.blockers
    row = report.items[0]
    assert row.skipped is True
    assert row.ok is False
    assert row.kind == MemoryKind.LESSON.value


def test_empty_pack_skip_not_ok(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    report = MemoryProvenance().label(ProvenanceRequest(items=[]))
    assert report.skipped is True
    assert report.ok is False
    assert "empty_pack" in report.blockers
    assert report.labeled == 0


def test_unknown_with_id_is_ok(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    report = MemoryProvenance().label(
        ProvenanceRequest(items=[{"id": "odd:1", "content": "?", "metadata": {}}])
    )
    assert report.ok is True
    assert report.skipped is False
    row = report.items[0]
    assert row.source_id == "odd:1"
    assert row.kind == MemoryKind.UNKNOWN.value
    assert row.origin == MemoryOrigin.UNKNOWN.value
    assert row.ok is True
    assert row.skipped is False


def test_persist_under_data_memory(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    report = MemoryProvenance().label(
        ProvenanceRequest(
            run_id="run-mem-1",
            items=[{"id": "les:1", "content": "x", "metadata": {"kind": "general"}}],
            persist=True,
        )
    )
    path = Path(report.persisted)
    assert path.is_file()
    assert path == data_root() / "memory" / "run-mem-1.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["run_id"] == "run-mem-1"
    assert payload["ok"] is True
    assert payload["schema_version"] == 1


def test_persist_false_writes_nothing(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    report = MemoryProvenance().label(
        ProvenanceRequest(
            run_id="run-mem-off",
            items=[{"id": "les:1", "content": "x", "metadata": {"kind": "general"}}],
            persist=False,
        )
    )
    assert report.persisted == ""
    assert not (data_root() / "memory" / "run-mem-off.json").exists()
