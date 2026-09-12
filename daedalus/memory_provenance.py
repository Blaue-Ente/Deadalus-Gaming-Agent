"""Observational labels for already-retrieved memory rows.

Leaf module. Do not import agent.py, native_tools, job_ledger, run_engine,
memory, memory_mem0, knowledge, knowledge_books, context_assembler,
context_budget, scratch_journal, reflection, learned_skills, preferences,
model_registry, or model_gateway.
Do not write studio/*.json. skip ≠ ok. Run schema v1 is unchanged.
Do not add source_ids to ContextSlice.

``label()`` is observational. Metadata is classified first, then id prefixes.
``ep:`` is used by both session episodes and Mem0 facts — disambiguate with
metadata and ``collection``, do not migrate ids.
Optional persist under ``$DAEDALUS_DATA/memory/{run_id}.json``.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from daedalus.contracts.memory import (
    MemoryKind,
    MemoryOrigin,
    MemoryProvenanceRecord,
    MemoryProvenanceReport,
)
from daedalus.paths import data_root

log = logging.getLogger(__name__)

_WEB_SOURCES = frozenset({"duckduckgo", "github"})
_REFLECTION_KINDS = frozenset({
    "preference", "fact", "mistake", "pattern", "decision", "general",
})
_LESSON_KINDS = _REFLECTION_KINDS | frozenset({
    "mentor_steer", "tool_error_pattern", "style_adaptation", "discovery",
})
_SKILL_ORIGINS = frozenset({
    "learned", "installed", "builtin", "failure", "success", "mentor_correction",
})
_COLLECTIONS = {
    MemoryKind.SESSION.value: "daedalus_sessions",
    MemoryKind.KNOWLEDGE.value: "daedalus_global",
    MemoryKind.ARTIFACT.value: "daedalus_artifacts",
    MemoryKind.LESSON.value: "daedalus_lessons",
    MemoryKind.EPISODE.value: "daedalus_episodes",
    MemoryKind.DISCOVERY.value: "daedalus_discoveries",
    MemoryKind.WORKING.value: "",
    MemoryKind.EPISODIC_FACT.value: "daedalus_episodic",
    MemoryKind.PREFERENCE.value: "",
    MemoryKind.SKILL.value: "",
    MemoryKind.JOURNAL.value: "",
    MemoryKind.BOOK.value: "daedalus_books",
    MemoryKind.UNKNOWN.value: "",
}


@dataclass
class ProvenanceRequest:
    run_id: str = ""
    session_id: str = ""
    items: list[dict] = field(default_factory=list)
    persist: bool = False


def _safe_run_id(run_id: str) -> str:
    return re.sub(r"[^a-zA-Z0-9._-]+", "-", (run_id or "").strip())[:80]


def _uniq(rows: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for row in rows:
        text = str(row or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out


def _meta(row: dict) -> dict:
    raw = row.get("metadata") if isinstance(row, dict) else None
    return raw if isinstance(raw, dict) else {}


def _source_id(row: dict) -> str:
    return str((row or {}).get("id") or "").strip()


def _tier(row: dict) -> str:
    return str((row or {}).get("source") or "").strip().lower()


def _path_like(value: object) -> bool:
    text = str(value or "").strip()
    if not text or text == "?":
        return False
    return "/" in text or "\\" in text or text.endswith((".md", ".json", ".txt", ".py"))


def _has_key(meta: dict, *keys: str) -> bool:
    return any(key in meta for key in keys)


def _captured_at(meta: dict) -> str:
    for key in ("timestamp", "stored_at", "ended_at", "created_at"):
        value = meta.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _locator(kind: str, meta: dict, session_id: str) -> str:
    if kind == MemoryKind.KNOWLEDGE.value:
        return str(meta.get("source") or "")
    if kind == MemoryKind.DISCOVERY.value:
        return str(meta.get("url") or "")
    if kind == MemoryKind.ARTIFACT.value:
        return str(meta.get("filename") or "")
    if kind == MemoryKind.BOOK.value:
        return str(meta.get("source") or "")
    if kind == MemoryKind.SKILL.value:
        return str(meta.get("path") or meta.get("source") or "")
    if kind in {
        MemoryKind.SESSION.value,
        MemoryKind.EPISODE.value,
        MemoryKind.WORKING.value,
        MemoryKind.EPISODIC_FACT.value,
    }:
        return session_id
    return ""


def _origin_for(kind: str, meta: dict, *, forced: str = "") -> str:
    if forced:
        return forced
    if kind == MemoryKind.KNOWLEDGE.value:
        return MemoryOrigin.KNOWLEDGE_SCAN.value
    if kind == MemoryKind.DISCOVERY.value:
        src = str(meta.get("source") or "").strip().lower()
        return MemoryOrigin.WEB.value if src in _WEB_SOURCES else MemoryOrigin.INFERRED.value
    if kind == MemoryKind.PREFERENCE.value:
        src = str(meta.get("source") or "").strip().lower()
        if src == "explicit":
            return MemoryOrigin.EXPLICIT.value
        if src in {"chat", "owner"}:
            return MemoryOrigin.OWNER.value
        return MemoryOrigin.DISK.value
    if kind in {MemoryKind.WORKING.value, MemoryKind.EPISODIC_FACT.value}:
        return MemoryOrigin.MEM0.value
    if kind in {
        MemoryKind.SESSION.value,
        MemoryKind.ARTIFACT.value,
        MemoryKind.EPISODE.value,
    }:
        return MemoryOrigin.CHROMA.value
    if kind == MemoryKind.LESSON.value:
        lk = str(meta.get("kind") or "").strip()
        if lk == "mentor_steer":
            return MemoryOrigin.MENTOR.value
        if lk == "discovery":
            return MemoryOrigin.WEB.value
        if meta.get("session_id") and lk in _REFLECTION_KINDS:
            return MemoryOrigin.REFLECTION.value
        return MemoryOrigin.INFERRED.value
    if kind in {
        MemoryKind.SKILL.value,
        MemoryKind.JOURNAL.value,
        MemoryKind.BOOK.value,
    }:
        return MemoryOrigin.DISK.value
    return MemoryOrigin.UNKNOWN.value


def _classify(row: dict, req: ProvenanceRequest) -> MemoryProvenanceRecord:
    meta = _meta(row)
    sid = _source_id(row)
    tier = _tier(row)
    session_id = str(meta.get("session_id") or req.session_id or "").strip()
    slice_name = str((row or {}).get("slice_name") or "").strip()
    kind = MemoryKind.UNKNOWN.value
    origin_forced = ""

    if str(meta.get("type") or "") == "preference" or sid.startswith("pref_"):
        kind = MemoryKind.PREFERENCE.value
    elif str(meta.get("type") or "") == "rejection_lesson" or sid.startswith("reject-"):
        kind = MemoryKind.LESSON.value
        origin_forced = MemoryOrigin.REJECTION.value
    elif meta.get("category") and _path_like(meta.get("source")):
        kind = MemoryKind.KNOWLEDGE.value
    elif meta.get("url") and meta.get("status"):
        kind = MemoryKind.DISCOVERY.value
    elif meta.get("filename") and meta.get("language"):
        kind = MemoryKind.ARTIFACT.value
    elif _has_key(meta, "turn_count", "started_at", "stored_at"):
        kind = MemoryKind.EPISODE.value
    elif _has_key(meta, "access_count", "importance") and "ts" in meta:
        kind = MemoryKind.EPISODIC_FACT.value
    elif meta.get("role") and meta.get("timestamp"):
        kind = MemoryKind.SESSION.value
    elif tier == "working" or sid.startswith("wm:"):
        kind = MemoryKind.WORKING.value
    elif sid.startswith("les:") or str(meta.get("kind") or "") in _LESSON_KINDS:
        kind = MemoryKind.LESSON.value
    elif sid.startswith("art:"):
        kind = MemoryKind.ARTIFACT.value
    elif sid.startswith("disc:"):
        kind = MemoryKind.DISCOVERY.value
    elif tier == "episodic":
        kind = MemoryKind.EPISODIC_FACT.value
    elif tier == "semantic":
        kind = MemoryKind.SESSION.value
    elif tier == "lesson":
        kind = MemoryKind.LESSON.value
    elif tier == "global":
        kind = MemoryKind.KNOWLEDGE.value
    elif str(meta.get("origin") or "") in _SKILL_ORIGINS:
        kind = MemoryKind.SKILL.value
    elif "doc_id" in meta and "chunk" in meta:
        kind = MemoryKind.BOOK.value
    elif _has_key(meta, "step") and _has_key(meta, "tokens_est"):
        kind = MemoryKind.JOURNAL.value

    blockers: list[str] = []
    skipped = False
    ok = True
    if not sid:
        skipped = True
        ok = False
        blockers.append("missing_id")

    origin = _origin_for(kind, meta, forced=origin_forced)
    return MemoryProvenanceRecord(
        source_id=sid,
        kind=kind,
        origin=origin,
        collection=_COLLECTIONS.get(kind, ""),
        locator=_locator(kind, meta, session_id),
        session_id=session_id,
        slice_name=slice_name,
        captured_at=_captured_at(meta),
        skipped=skipped,
        ok=ok,
        blockers=blockers,
    )


def _persist(run_id: str, report: MemoryProvenanceReport) -> str:
    safe = _safe_run_id(run_id)
    if not safe:
        return ""
    dest = data_root() / "memory"
    dest.mkdir(parents=True, exist_ok=True)
    path = dest / f"{safe}.json"
    path.write_text(
        json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return str(path)


def _with_persist(req: ProvenanceRequest, report: MemoryProvenanceReport) -> MemoryProvenanceReport:
    if not req.persist:
        return report
    try:
        persisted = _persist(req.run_id, report)
    except Exception as exc:
        log.debug("memory provenance persist skipped: %s", exc)
        return report
    if not persisted:
        return report
    return report.model_copy(update={"persisted": persisted})


class MemoryProvenance:
    """Stateless façade. Labels retrieved memory rows without touching stores."""

    def label(self, req: ProvenanceRequest) -> MemoryProvenanceReport:
        rows = list(req.items or [])
        if not rows:
            report = MemoryProvenanceReport(
                run_id=req.run_id,
                session_id=req.session_id,
                items=[],
                labeled=0,
                skipped_count=0,
                ok=False,
                skipped=True,
                blockers=["empty_pack"],
                persisted="",
            )
            return _with_persist(req, report)
        items = [_classify(row if isinstance(row, dict) else {}, req) for row in rows]
        labeled = sum(1 for row in items if row.ok)
        skipped_count = sum(1 for row in items if row.skipped)
        blockers = _uniq([flag for row in items for flag in row.blockers])
        report = MemoryProvenanceReport(
            run_id=req.run_id,
            session_id=req.session_id,
            items=items,
            labeled=labeled,
            skipped_count=skipped_count,
            ok=skipped_count == 0,
            skipped=False,
            blockers=blockers,
            persisted="",
        )
        return _with_persist(req, report)


def memory_dir() -> Path:
    path = data_root() / "memory"
    path.mkdir(parents=True, exist_ok=True)
    return path


def label(req: ProvenanceRequest) -> MemoryProvenanceReport:
    return MemoryProvenance().label(req)
