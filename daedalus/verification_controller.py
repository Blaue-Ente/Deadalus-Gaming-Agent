"""Static / build / test / game / visual / acceptance façade.

Leaf module. Do not import agent.py. Leftover NEED_* if-arms and NEED_PROPOSE
stay on Daedalus. RunFinalizer is a sibling leaf (empty-reply, review-gate,
session record_outcome, chat payload). ToolLoop keeps in-loop kernel proof.
Execute stays in write_gate / game_lint / playground_runner / visual_proof /
gameplay / platform_acceptance / job_contract.

VerificationController is the sequencer the future Run Engine can call.
It does not replace full_autopilot, game_studio_verify, or the leftover ladder.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from daedalus.contracts import DodVerdict, RunStage, StageRow
from daedalus.game_build_contract import evaluate_build, skip_is_not_ok, verified
from daedalus.stage_machine import OPTIONAL_SKIP_STAGES, SPINE

_NO_COMPILE = frozenset({"", "static", "streamlit"})
_VITE_COMPILE = frozenset({"vite", "vite-react", "phaser"})
_PLAYTEST_KINDS = frozenset({"godot", "expo", "unity", "phaser"})


@dataclass
class VerifyRequest:
    slug: str = ""
    kind: str = ""
    ask: str = ""
    reply: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    files: list[str] = field(default_factory=list)
    open_slices: int = 0
    intent: str = ""
    through: RunStage = RunStage.REVIEW
    smoke: dict[str, Any] | None = None
    visual: Any | None = None
    gameplay: dict[str, Any] | None = None
    browser_ok: bool | None = None


@dataclass
class VerifyResult:
    ok: bool
    stages: list[StageRow] = field(default_factory=list)
    dod: DodVerdict | None = None
    qa: dict[str, Any] | None = None
    blockers: list[str] = field(default_factory=list)
    next_stage: RunStage | None = None


class VerificationController:
    """Stateless façade. Sequences verify leaves. Sibling of RunFinalizer."""

    async def static_check(self, req: VerifyRequest) -> StageRow:
        blockers: list[str] = []
        if req.files:
            from .game_lint import check as lint_check

            for path in req.files:
                report = lint_check(path, slug=req.slug)
                for issue in report.get("blockers") or []:
                    text = str(issue.get("message") or issue.get("code") or "").strip()
                    if text:
                        blockers.append(text)
            return _row(RunStage.STATIC_VERIFY, ok=not blockers, blockers=blockers)
        if req.slug:
            from .game_engineer import scan_project

            scan_project(req.slug)
            # Project-scan warnings are advisory; they do not fail the row.
            return _row(RunStage.STATIC_VERIFY, ok=True)
        return _row(RunStage.STATIC_VERIFY, ok=True)

    async def build(self, req: VerifyRequest) -> StageRow:
        if not req.slug:
            return _row(
                RunStage.BUILD,
                ok=False,
                skipped=True,
                error="no_slug",
                blockers=["no_slug"],
            )
        raw = skip_is_not_ok(await _compile_kind(req.slug, req.kind))
        return _row_from_raw(RunStage.BUILD, raw)

    async def run_tests(self, req: VerifyRequest) -> StageRow:
        if req.smoke is not None:
            raw = skip_is_not_ok(req.smoke)
            return _row_from_raw(RunStage.ENGINE_TEST, raw)
        if not req.slug:
            return _row(
                RunStage.ENGINE_TEST,
                ok=False,
                skipped=True,
                error="no_slug",
                blockers=["no_slug"],
            )
        from .playground_runner import smoke_test

        raw = skip_is_not_ok(await smoke_test(req.slug))
        return _row_from_raw(RunStage.ENGINE_TEST, raw)

    async def game_verify(self, req: VerifyRequest) -> StageRow:
        if req.gameplay is not None:
            return _row_from_raw(RunStage.PLAYTEST, req.gameplay)
        if not req.slug:
            return _row(
                RunStage.PLAYTEST,
                ok=False,
                skipped=True,
                error="no_slug",
                blockers=["no_slug"],
            )
        from .engine_runners import gameplay_for_kind

        raw = await gameplay_for_kind(req.slug, req.kind or "static")
        return _row_from_raw(RunStage.PLAYTEST, raw)

    async def visual_proof(self, req: VerifyRequest) -> StageRow:
        if req.visual is not None:
            return _row_from_raw(RunStage.VISUAL_VERIFY, _visual_as_dict(req.visual))
        from .visual_proof import run_visual_proof

        result = await run_visual_proof(req.slug, req.kind or "static")
        return _row_from_raw(RunStage.VISUAL_VERIFY, _visual_as_dict(result))

    def accept(
        self,
        req: VerifyRequest,
        stages: Sequence[StageRow] | None = None,
    ) -> DodVerdict:
        from .job_contract import evaluate

        contract = _score_request(req, evaluate)
        qa = _maybe_qa(req)
        if qa is not None:
            release = qa.get("release") if isinstance(qa.get("release"), dict) else {}
            approved = bool(qa.get("ok")) and bool(release.get("approved"))
            if not approved:
                contract.complete = False if contract.coding_task else contract.complete
                for item in release.get("blockers") or []:
                    text = str(item).strip()
                    if text and text not in contract.blockers:
                        contract.blockers.append(text)
                if contract.coding_task and contract.blockers:
                    contract.summary = "; ".join(contract.blockers)
        if stages:
            extra = _stage_blockers(stages)
            if extra:
                if contract.coding_task:
                    contract.complete = False
                for text in extra:
                    if text not in contract.blockers:
                        contract.blockers.append(text)
                if contract.coding_task and contract.blockers:
                    contract.summary = "; ".join(contract.blockers)
        return DodVerdict(
            coding_task=contract.coding_task,
            complete=contract.complete,
            checks=dict(contract.checks),
            blockers=list(contract.blockers),
            summary=contract.summary,
        )

    async def run(self, req: VerifyRequest) -> VerifyResult:
        rows: list[StageRow] = []
        stop_at = _spine_index(req.through)
        for stage, fn in (
            (RunStage.STATIC_VERIFY, self.static_check),
            (RunStage.BUILD, self.build),
            (RunStage.ENGINE_TEST, self.run_tests),
            (RunStage.PLAYTEST, self.game_verify),
            (RunStage.VISUAL_VERIFY, self.visual_proof),
        ):
            if _spine_index(stage) > stop_at:
                break
            if stage in OPTIONAL_SKIP_STAGES and not _stage_applies(stage, req):
                continue
            row = await fn(req)
            rows.append(row)
            if not row.ok:
                break

        dod = None
        if _spine_index(RunStage.REVIEW) <= stop_at:
            dod = self.accept(req, rows)
        qa = _maybe_qa(req)

        blockers = _stage_blockers(rows)
        if dod is not None:
            for text in dod.blockers:
                if text and text not in blockers:
                    blockers.append(text)

        next_stage = None
        for row in rows:
            if not row.ok:
                next_stage = _as_run_stage(row.stage)
                break
        if next_stage is None and dod is not None and dod.coding_task and not dod.complete:
            next_stage = RunStage.REVIEW

        ok = all(row.ok for row in rows)
        if dod is not None and dod.coding_task:
            ok = ok and dod.complete
        return VerifyResult(
            ok=ok,
            stages=rows,
            dod=dod,
            qa=qa,
            blockers=blockers,
            next_stage=next_stage,
        )


def _score_request(req: VerifyRequest, evaluate: Any) -> Any:
    test_ok = verified(req.smoke) if req.smoke is not None else None
    if req.visual is not None:
        browser_result = _visual_as_dict(req.visual)
        contract = evaluate_build(
            req.ask,
            req.reply,
            tool_calls=req.tool_calls,
            test_ok=test_ok,
            browser_result=browser_result,
            kind=req.kind,
            intent=req.intent,
        )
        if req.open_slices:
            extra = evaluate(
                req.ask,
                req.reply,
                tool_calls=req.tool_calls,
                test_ok=test_ok,
                browser_ok=verified(browser_result),
                kind=req.kind,
                intent=req.intent,
                open_slices=req.open_slices,
            )
            if extra.blockers:
                contract.complete = extra.complete
                for text in extra.blockers:
                    if text not in contract.blockers:
                        contract.blockers.append(text)
                contract.checks.update(extra.checks)
                contract.summary = extra.summary
        return contract
    return evaluate(
        req.ask,
        req.reply,
        tool_calls=req.tool_calls,
        test_ok=test_ok,
        browser_ok=req.browser_ok,
        kind=req.kind,
        intent=req.intent,
        open_slices=req.open_slices,
    )


async def _compile_kind(slug: str, kind: str) -> dict[str, Any]:
    k = (kind or "").strip().lower()
    if k in _NO_COMPILE:
        return {"ok": True, "skipped": False, "summary": "static: no compile gate"}
    if k == "expo":
        from .playground_runner import expo_compile_gate

        return await expo_compile_gate(slug)
    if k == "godot":
        from .engine_runners import run_godot

        return run_godot(slug)
    if k == "unity":
        from .engine_runners import run_unity

        return run_unity(slug)
    if k in _VITE_COMPILE:
        from .playground_runner import smoke_test

        return await smoke_test(slug)
    return {"ok": True, "skipped": False, "summary": f"{k}: no dedicated compile gate"}


def _stage_applies(stage: RunStage, req: VerifyRequest) -> bool:
    from .visual_proof import is_visual_kind

    kind = (req.kind or "").strip().lower()
    if stage is RunStage.ENGINE_TEST:
        return bool(req.slug or req.smoke is not None)
    if stage is RunStage.PLAYTEST:
        return is_visual_kind(kind) or kind in _PLAYTEST_KINDS
    if stage is RunStage.VISUAL_VERIFY:
        return is_visual_kind(kind) or kind == "unity"
    return True


def _maybe_qa(req: VerifyRequest) -> dict[str, Any] | None:
    if not req.slug:
        return None
    try:
        from . import playground as pg
        from .capability import load_evidence

        doc = load_evidence(pg.project_dir(req.slug))
    except Exception:
        return None
    if not doc:
        return None
    from .platform_acceptance import run_qa

    return run_qa(req.slug, kind=req.kind, text=req.ask)


def _visual_as_dict(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return dict(raw)
    if hasattr(raw, "as_dict"):
        data = dict(raw.as_dict())
        gap = str(getattr(raw, "honest_gap", "") or "")
        if gap and not data.get("honest_gap"):
            data["honest_gap"] = gap
        return data
    return {
        "ok": bool(getattr(raw, "ok", False)),
        "skipped": bool(getattr(raw, "skipped", False)),
        "summary": str(getattr(raw, "summary", "") or ""),
        "error": str(getattr(raw, "error", "") or ""),
        "honest_gap": str(getattr(raw, "honest_gap", "") or ""),
    }


def _row_from_raw(stage: RunStage, raw: dict[str, Any] | None) -> StageRow:
    payload = dict(raw or {})
    if payload.get("na"):
        ok = bool(payload.get("ok", True))
        return _row(
            stage,
            ok=ok,
            skipped=False,
            error=str(payload.get("error") or ""),
            blockers=_payload_blockers(payload),
        )
    norm = skip_is_not_ok(payload)
    ok = verified(payload)
    skipped = bool(norm.get("skipped"))
    error = str(norm.get("error") or norm.get("honest_gap") or "")
    blockers = _payload_blockers(norm)
    if not ok and not blockers:
        summary = str(norm.get("summary") or error or "verify failed").strip()
        if summary:
            blockers.append(summary)
    return _row(stage, ok=ok, skipped=skipped, error=error, blockers=blockers)


def _payload_blockers(raw: dict[str, Any]) -> list[str]:
    out: list[str] = []
    items = raw.get("blockers")
    if isinstance(items, list):
        for item in items:
            text = str(item).strip()
            if text:
                out.append(text)
    return out


def _row(
    stage: RunStage,
    *,
    ok: bool,
    skipped: bool = False,
    blockers: list[str] | None = None,
    error: str = "",
) -> StageRow:
    if skipped:
        ok = False
    return StageRow(
        stage=stage.value,
        ok=ok,
        skipped=skipped,
        blockers=list(blockers or []),
        error=error,
    )


def _stage_blockers(stages: Sequence[StageRow]) -> list[str]:
    out: list[str] = []
    for row in stages:
        if row.ok:
            continue
        for text in row.blockers:
            if text and text not in out:
                out.append(text)
        if row.error and row.error not in out:
            out.append(row.error)
    return out


def _spine_index(stage: RunStage) -> int:
    try:
        return SPINE.index(stage)
    except ValueError:
        return SPINE.index(RunStage.REVIEW)


def _as_run_stage(raw: str) -> RunStage:
    try:
        return RunStage(raw)
    except ValueError:
        return RunStage.REPAIR
