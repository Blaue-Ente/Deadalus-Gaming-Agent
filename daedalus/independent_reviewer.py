"""Independent second-pass sign-off.

Leaf module. Do not import agent.py, native_tools, tool_loop, run_finalizer,
mentor, mentor_helm, mentor_graph, workers, improvements, model_gateway,
llm_client, cheap_llm, subagents, kernel, verification_controller, otel,
context_budget, or memory_provenance.
Do not write studio/*.json. skip ≠ ok. Run schema v1 is unchanged.

``review()`` is an identity no-op when ``DAEDALUS_INDEPENDENT_REVIEW`` is unset.
Checklist is the default when opted in. Optional LLM only via an injected
``llm_fn`` plus ``DAEDALUS_INDEPENDENT_REVIEW_LLM``. Missing fn / bad JSON /
offline is skip ≠ ok, not fail-open. Checklist blockers win over LLM approved.
Optional persist under ``$DAEDALUS_DATA/review/{run_id}.json``.
"""
from __future__ import annotations

import json
import logging
import os
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from daedalus.contracts.evidence import DodVerdict, StageRow
from daedalus.contracts.review import (
    ReviewFinding,
    ReviewMode,
    ReviewReport,
    ReviewSubjectKind,
    ReviewVerdict,
)
from daedalus.paths import data_root

log = logging.getLogger(__name__)

FLAG_ENV = "DAEDALUS_INDEPENDENT_REVIEW"
LLM_ENV = "DAEDALUS_INDEPENDENT_REVIEW_LLM"
OFFLINE_ENV = "DAEDALUS_OFFLINE"
WRITE_TOOLS = frozenset({"write_file", "edit_file", "playground_new"})
SELF_SOURCES = frozenset({"writer", "mentor", "review_gate"})
BLOCK_SEVERITIES = frozenset({"critical", "high"})
_TRUTHY = frozenset({"1", "true", "yes", "on"})
_LLM_SYSTEM = (
    "You are an independent reviewer. Read-only. Do not write files. "
    "Reply with JSON only: "
    '{"verdict":"approved|blocked|skipped","findings":'
    '[{"severity":"critical|high|medium|low","code":"...","detail":"..."}]}. '
    "APPROVED only if shippable. Skip is not approval."
)


@dataclass
class ReviewRequest:
    run_id: str = ""
    session_id: str = ""
    subject: str = ReviewSubjectKind.RUN.value
    diff: str = ""
    plan: dict[str, Any] | None = None
    stages: list[Any] = field(default_factory=list)
    dod: Any = None
    writer_model: str = ""
    reviewer_model: str = ""
    tools: list[Any] = field(default_factory=list)
    findings: list[Any] = field(default_factory=list)
    source: str = ""
    persist: bool = False
    llm_fn: Callable[[str, str], str] | None = None


def _env_on(name: str) -> bool:
    return (os.environ.get(name) or "").strip().lower() in _TRUTHY


def review_enabled() -> bool:
    return _env_on(FLAG_ENV)


def llm_enabled() -> bool:
    return _env_on(LLM_ENV)


def _offline() -> bool:
    return _env_on(OFFLINE_ENV)


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


def _has_subject(req: ReviewRequest) -> bool:
    if (req.run_id or "").strip():
        return True
    if (req.diff or "").strip():
        return True
    plan = req.plan
    return isinstance(plan, dict) and bool(plan)


def _as_stage(raw: object) -> StageRow | None:
    if isinstance(raw, StageRow):
        return raw
    if not isinstance(raw, dict):
        return None
    stage = str(raw.get("stage") or "").strip()
    if not stage:
        return None
    skipped = bool(raw.get("skipped"))
    ok = bool(raw.get("ok", False))
    if skipped:
        ok = False
    blockers = [str(item).strip() for item in (raw.get("blockers") or []) if str(item).strip()]
    return StageRow(
        stage=stage,
        ok=ok,
        skipped=skipped,
        blockers=blockers,
        error=str(raw.get("error") or ""),
    )


def _stages(req: ReviewRequest) -> list[StageRow]:
    out: list[StageRow] = []
    for item in req.stages or []:
        row = _as_stage(item)
        if row is not None:
            out.append(row)
    return out


def _as_dod(raw: object) -> DodVerdict | None:
    if isinstance(raw, DodVerdict):
        return raw
    if not isinstance(raw, dict):
        return None
    if "coding_task" not in raw and "complete" not in raw:
        return None
    blockers = [str(item).strip() for item in (raw.get("blockers") or []) if str(item).strip()]
    checks_raw = raw.get("checks")
    checks = dict(checks_raw) if isinstance(checks_raw, dict) else {}
    return DodVerdict(
        coding_task=bool(raw.get("coding_task")),
        complete=bool(raw.get("complete")),
        checks=checks,
        blockers=blockers,
        summary=str(raw.get("summary") or ""),
    )


def _as_finding(raw: object) -> ReviewFinding | None:
    if isinstance(raw, ReviewFinding):
        return raw
    if not isinstance(raw, dict):
        return None
    severity = str(raw.get("severity") or "").strip().lower()
    code = str(raw.get("code") or "").strip()
    if not severity and not code:
        return None
    skipped = bool(raw.get("skipped"))
    ok = bool(raw.get("ok", not skipped))
    if skipped:
        ok = False
    return ReviewFinding(
        severity=severity or "low",
        code=code or "finding",
        detail=str(raw.get("detail") or "")[:500],
        skipped=skipped,
        ok=ok,
    )


def _findings(raw_rows: list[Any] | None) -> list[ReviewFinding]:
    out: list[ReviewFinding] = []
    for item in raw_rows or []:
        row = _as_finding(item)
        if row is not None:
            out.append(row)
    return out


def _tool_name(item: object) -> str:
    if isinstance(item, str):
        return item.strip()
    if isinstance(item, dict):
        return str(item.get("name") or item.get("tool") or "").strip()
    return ""


def _tools(req: ReviewRequest) -> list[str]:
    return [name for name in (_tool_name(item) for item in req.tools or []) if name]


def _parse_llm(raw: str) -> dict[str, Any] | None:
    text = (raw or "").strip()
    if not text:
        return None
    clean = re.sub(r"^```(?:json)?|```$", "", text, flags=re.MULTILINE).strip()
    payload: Any = None
    try:
        payload = json.loads(clean)
    except Exception:
        match = re.search(r"\{[\s\S]*\}", clean)
        if not match:
            return None
        try:
            payload = json.loads(match.group(0))
        except Exception:
            return None
    if not isinstance(payload, dict):
        return None
    verdict = str(payload.get("verdict") or "").strip().lower()
    if verdict not in {
        ReviewVerdict.APPROVED.value,
        ReviewVerdict.BLOCKED.value,
        ReviewVerdict.SKIPPED.value,
    }:
        return None
    findings = payload.get("findings")
    if findings is None:
        findings = []
    if not isinstance(findings, list):
        return None
    return {"verdict": verdict, "findings": findings}


def _llm_user_prompt(req: ReviewRequest) -> str:
    stages = [
        {"stage": row.stage, "ok": row.ok, "skipped": row.skipped, "blockers": list(row.blockers)}
        for row in _stages(req)
    ]
    dod = _as_dod(req.dod)
    body = {
        "run_id": req.run_id,
        "session_id": req.session_id,
        "subject": req.subject or ReviewSubjectKind.RUN.value,
        "diff": (req.diff or "")[:4000],
        "plan": req.plan if isinstance(req.plan, dict) else {},
        "stages": stages,
        "dod": dod.model_dump(mode="json") if dod is not None else None,
        "writer_model": req.writer_model,
        "reviewer_model": req.reviewer_model,
        "tools": _tools(req),
    }
    return json.dumps(body, ensure_ascii=False)


def _persist(run_id: str, report: ReviewReport) -> str:
    safe = _safe_run_id(run_id)
    if not safe:
        return ""
    dest = data_root() / "review"
    dest.mkdir(parents=True, exist_ok=True)
    path = dest / f"{safe}.json"
    path.write_text(
        json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return str(path)


def _with_persist(req: ReviewRequest, report: ReviewReport) -> ReviewReport:
    if not req.persist:
        return report
    try:
        persisted = _persist(req.run_id, report)
    except Exception as exc:
        log.debug("independent review persist skipped: %s", exc)
        return report
    if not persisted:
        return report
    return report.model_copy(update={"persisted": persisted})


def _off_report(req: ReviewRequest) -> ReviewReport:
    return ReviewReport(
        run_id=req.run_id,
        session_id=req.session_id,
        subject=(req.subject or ReviewSubjectKind.RUN.value),
        mode=ReviewMode.OFF.value,
        source="none",
        writer_model=req.writer_model,
        reviewer_model=req.reviewer_model,
        verdict=ReviewVerdict.NOT_RUN.value,
        ok=True,
        skipped=False,
    )


class IndependentReviewer:
    """Stateless façade. Read-only checklist, optional injected LLM overlay."""

    def review(self, req: ReviewRequest) -> ReviewReport:
        if not review_enabled():
            return _with_persist(req, _off_report(req))

        blockers: list[str] = []
        findings: list[ReviewFinding] = []
        skipped = False
        mode = ReviewMode.CHECKLIST.value
        source = "checklist"
        writer = (req.writer_model or "").strip()
        reviewer = (req.reviewer_model or "").strip() or "checklist"

        if not _has_subject(req):
            blockers.append("empty_subject")
            skipped = True

        for row in _stages(req):
            if row.skipped:
                blockers.append("stage_skipped")
                findings.append(
                    ReviewFinding(
                        severity="high",
                        code="stage_skipped",
                        detail=row.stage,
                        skipped=False,
                        ok=False,
                    )
                )
            elif not row.ok:
                blockers.append("stage_failed")
                findings.append(
                    ReviewFinding(
                        severity="high",
                        code="stage_failed",
                        detail=row.stage,
                        skipped=False,
                        ok=False,
                    )
                )

        dod = _as_dod(req.dod)
        if dod is not None and dod.coding_task and (not dod.complete or list(dod.blockers)):
            blockers.append("dod_incomplete")

        if writer and (req.reviewer_model or "").strip() and writer == (req.reviewer_model or "").strip():
            blockers.append("same_writer")

        if WRITE_TOOLS.intersection(_tools(req)):
            blockers.append("write_tools")

        for row in _findings(req.findings):
            findings.append(row)
            if row.severity in BLOCK_SEVERITIES:
                blockers.append("finding_block")

        if (req.source or "").strip().lower() in SELF_SOURCES:
            blockers.append("self_source")

        if llm_enabled():
            mode = ReviewMode.LLM.value
            source = "llm"
            if _offline():
                blockers.append("offline")
                skipped = True
            elif req.llm_fn is None:
                blockers.append("missing_reviewer")
                skipped = True
            else:
                parsed: dict[str, Any] | None
                try:
                    parsed = _parse_llm(req.llm_fn(_LLM_SYSTEM, _llm_user_prompt(req)))
                except Exception as exc:
                    log.debug("independent review llm failed: %s", exc)
                    parsed = None
                if parsed is None:
                    blockers.append("llm_parse")
                    skipped = True
                else:
                    extra = _findings(parsed.get("findings") if isinstance(parsed.get("findings"), list) else [])
                    for row in extra:
                        findings.append(row)
                        if row.severity in BLOCK_SEVERITIES:
                            blockers.append("finding_block")
                    llm_verdict = str(parsed.get("verdict") or "")
                    if llm_verdict == ReviewVerdict.SKIPPED.value:
                        blockers.append("llm_skipped")
                        skipped = True
                    elif llm_verdict == ReviewVerdict.BLOCKED.value:
                        blockers.append("llm_blocked")
                    if (req.reviewer_model or "").strip():
                        reviewer = (req.reviewer_model or "").strip()

        blockers = _uniq(blockers)
        if skipped:
            verdict = ReviewVerdict.SKIPPED.value
            ok = False
        elif blockers:
            verdict = ReviewVerdict.BLOCKED.value
            ok = False
        else:
            verdict = ReviewVerdict.APPROVED.value
            ok = True

        report = ReviewReport(
            run_id=req.run_id,
            session_id=req.session_id,
            subject=(req.subject or ReviewSubjectKind.RUN.value),
            mode=mode,
            source=source,
            writer_model=writer,
            reviewer_model=reviewer,
            verdict=verdict,
            findings=findings,
            ok=ok,
            skipped=skipped,
            blockers=blockers,
        )
        return _with_persist(req, report)


def review(req: ReviewRequest) -> ReviewReport:
    return IndependentReviewer().review(req)
