"""Kernel build loop — the plan drives the model, not the other way round.

``tool_router`` picks the playbook. This module walks it: every native tool
result is observed, every plan step is marked done / failed / pending, and
when the model goes quiet the Definition of Done is checked *inside* the
native round loop. Incomplete → the kernel injects PLAN PROGRESS as the next
user turn and grants a bounded number of fix rounds. Only then does the
build turn end, and the summary says honestly what is still missing.

Before this module the DoD ran once, after the reply was final, and its
verdict was appended as text nobody could act on.
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any

from .task_playbooks import Step
from .tool_catalog import canonical

log = logging.getLogger(__name__)

DEFAULT_FIX_ROUNDS = 3
ROUNDS_PER_FIX = 2
_HEAD_CHARS = 240
_SLUG_RE = re.compile(r"\bslug=([A-Za-z0-9_.-]+)")
_SLUG_TOOLS = frozenset({
    "write_file", "read_file", "playtest", "browser", "run_command", "game_lint",
    "assets", "engine", "world", "production", "playground_run",
})


def fix_rounds_from_env() -> int:
    raw = (os.environ.get("DAEDALUS_BUILD_FIX_ROUNDS") or "").strip()
    if not raw:
        return DEFAULT_FIX_ROUNDS
    try:
        return max(0, int(raw))
    except ValueError:
        return DEFAULT_FIX_ROUNDS


@dataclass
class ToolObservation:
    tool: str
    ok: bool
    blocked: bool
    args: dict[str, Any]
    head: str

    def as_call(self) -> dict[str, Any]:
        return {"tool": self.tool, "ok": self.ok, "blocked": self.blocked, "input": self.args}


@dataclass
class StepProgress:
    step: Step
    status: str  # done | failed | pending
    detail: str = ""

    @property
    def mark(self) -> str:
        return {"done": "✓", "failed": "✗"}.get(self.status, "☐")


@dataclass
class BuildVerdict:
    complete: bool
    blockers: list[str] = field(default_factory=list)
    missing_steps: list[str] = field(default_factory=list)
    next_step: Step | None = None
    failed_calls: list[ToolObservation] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "complete": self.complete,
            "blockers": list(self.blockers),
            "missing_steps": list(self.missing_steps),
            "next_step": self.next_step.id if self.next_step else "",
            "failed_calls": [o.tool for o in self.failed_calls],
        }


def _names(step: Step) -> list[str]:
    out = [n for n in (step.tool, *step.accept_any) if n]
    return [canonical(n).lower() for n in out]


def _head(text: str) -> str:
    one = " ".join((text or "").split())
    return one[:_HEAD_CHARS]


class BuildLoop:
    """State for one build turn: plan steps × observed tool calls × fix budget."""

    def __init__(
        self,
        route: Any,
        user_message: str,
        *,
        seed_log: list[dict[str, Any]] | None = None,
        fix_rounds: int | None = None,
        rounds_per_fix: int = ROUNDS_PER_FIX,
    ) -> None:
        self.route = route
        self.user_message = user_message or ""
        self.kind = str(getattr(route, "kind", "") or "static")
        self.seed_log = [dict(r) for r in (seed_log or [])]
        self.fix_rounds = fix_rounds_from_env() if fix_rounds is None else max(0, fix_rounds)
        self.rounds_per_fix = max(1, rounds_per_fix)
        self.observations: list[ToolObservation] = []
        self.nudges = 0
        self.slug = ""
        self.last_verdict: BuildVerdict | None = None
        self.stop_reason = ""
        for row in self.seed_log:
            self._capture_slug(row.get("input") or {}, "")

    # ── observation ───────────────────────────────────────────────────────

    @property
    def active(self) -> bool:
        return self.fix_rounds > 0

    def observe(
        self,
        tool: str,
        args: dict[str, Any] | None,
        result_text: str,
        *,
        ok: bool,
        blocked: bool = False,
    ) -> None:
        args = dict(args or {})
        args.pop("content", None)
        self.observations.append(
            ToolObservation(tool=tool, ok=bool(ok), blocked=bool(blocked), args=args, head=_head(result_text))
        )
        self._capture_slug(args, result_text)

    def _capture_slug(self, args: dict[str, Any], result_text: str) -> None:
        if self.slug:
            return
        cand = str(args.get("slug") or "").strip()
        if not cand:
            m = _SLUG_RE.search(result_text or "")
            cand = m.group(1) if m else ""
        if cand:
            self.slug = cand

    def tool_calls(self) -> list[dict[str, Any]]:
        return [*self.seed_log, *(o.as_call() for o in self.observations)]

    # ── plan bookkeeping ──────────────────────────────────────────────────

    def progress(self) -> list[StepProgress]:
        seed_ok = {
            canonical(str(r.get("tool") or "")).lower()
            for r in self.seed_log
            if r.get("ok") and r.get("tool")
        }
        rows: list[StepProgress] = []
        for step in self.route.ordered_steps:
            names = _names(step)
            if not names:
                rows.append(StepProgress(step, "done"))
                continue
            hits = [o for o in self.observations if canonical(o.tool).lower() in names]
            if any(o.ok or o.blocked for o in hits) or (seed_ok & set(names)):
                rows.append(StepProgress(step, "done"))
            elif hits:
                rows.append(StepProgress(step, "failed", hits[-1].head))
            else:
                rows.append(StepProgress(step, "pending"))
        return rows

    def unrecovered_failures(self) -> list[ToolObservation]:
        from .job_contract import unrecovered_failure_indexes

        idx = unrecovered_failure_indexes([o.as_call() for o in self.observations])
        return [self.observations[i] for i in idx]

    def verdict(self, reply_text: str = "") -> BuildVerdict:
        from .job_contract import evaluate

        contract = evaluate(
            self.user_message,
            reply_text or "",
            tool_calls=self.tool_calls(),
            kind=self.kind,
            strict=True,
        )
        rows = self.progress()
        missing = [f"{r.step.id} ({r.step.tool or r.step.id})" for r in rows if r.step.required and r.status != "done"]
        nxt = next((r.step for r in rows if r.step.required and r.status != "done"), None)
        if nxt is None:
            nxt = next((r.step for r in rows if r.status == "pending" and r.step.tool), None)
        v = BuildVerdict(
            complete=bool(contract.complete),
            blockers=list(contract.blockers),
            missing_steps=missing,
            next_step=nxt,
            failed_calls=self.unrecovered_failures(),
        )
        self.last_verdict = v
        return v

    # ── control ───────────────────────────────────────────────────────────

    def on_quiet(self, reply_text: str) -> str | None:
        """Model stopped calling tools. Return a nudge to continue, or None to stop."""
        v = self.verdict(reply_text)
        if v.complete:
            self.stop_reason = "complete"
            return None
        if not self.active:
            self.stop_reason = "passive"
            return None
        if self.nudges >= self.fix_rounds:
            self.stop_reason = "fix budget exhausted"
            return None
        self.nudges += 1
        log.info(
            "build loop nudge %d/%d playbook=%s missing=%s failed=%s",
            self.nudges, self.fix_rounds, self.route.playbook.id, v.missing_steps,
            [o.tool for o in v.failed_calls],
        )
        return self.progress_block(v)

    def _step_args_hint(self, step: Step) -> str:
        args = dict(step.args or {})
        if step.tool in _SLUG_TOOLS and self.slug:
            args.setdefault("slug", self.slug)
        if step.tool == "playtest":
            args.setdefault("kind", self.kind)
        if step.tool == "playground_new" and not self.slug:
            args.setdefault("kind", self.kind)
        if not args:
            return ""
        return " ".join(f"{k}={v}" for k, v in args.items())

    def progress_block(self, v: BuildVerdict | None = None) -> str:
        v = v or self.last_verdict or self.verdict()
        left = max(0, self.fix_rounds - self.nudges)
        head = [
            "━━━ PLAN PROGRESS (kernel — not the model) ━━━",
            f"Playbook: {self.route.playbook.id}"
            + (f" · slug: {self.slug}" if self.slug else " · slug: not created yet")
            + f" · fix rounds left: {left}",
        ]
        for r in self.progress():
            tool = r.step.tool or "(docs)"
            flag = " REQUIRED" if r.step.required and r.status != "done" else ""
            line = f"  {r.mark} {r.step.id}: {tool}{flag}"
            if r.detail:
                line += f" — {r.detail}"
            head.append(line)
        if v.failed_calls:
            head.append("Failed calls not yet recovered:")
            for o in v.failed_calls[-3:]:
                head.append(f"  ✗ {o.tool} {o.args or ''} — {o.head}")
        if v.blockers:
            head.append("Not done yet:")
            head.extend(f"  • {b}" for b in v.blockers)
        if v.next_step is not None and v.next_step.tool:
            hint = self._step_args_hint(v.next_step)
            head.append(
                f"NEXT: call {v.next_step.tool}" + (f" with {hint}" if hint else "")
                + (f" — {v.next_step.why}" if v.next_step.why else "")
            )
        elif v.failed_calls:
            o = v.failed_calls[-1]
            head.append(
                f"NEXT: repeat {o.tool} with corrected arguments"
                + (f" (slug={self.slug})" if self.slug and o.tool in _SLUG_TOOLS else "")
                + ", then finish the plan."
            )
        head.append(
            "Call the tool now; do not describe it. Do not explore the harness "
            "(no list_dir / find outside the slug). No NEED_* markers, no SQL."
        )
        return "\n".join(head)

    def summary_block(self) -> str:
        """Kernel verdict for the persona turn / final reply — honest, compact."""
        v = self.last_verdict or self.verdict()
        rows = self.progress()
        done = sum(1 for r in rows if r.status == "done")
        lines = [
            "━━━ BUILD LOOP (kernel) ━━━",
            f"Playbook {self.route.playbook.id}: {done}/{len(rows)} steps done, "
            f"{self.nudges} fix round(s) used"
            + (f", stopped: {self.stop_reason}" if self.stop_reason else "")
            + (f", slug {self.slug}" if self.slug else "")
            + ".",
        ]
        if v.complete:
            lines.append("Plan complete — summarize what was built and how to open it.")
        else:
            lines.append("Plan NOT complete. Say so plainly; list what remains:")
            lines.extend(f"  • {b}" for b in v.blockers)
        return "\n".join(lines)

    def log_row(self) -> dict[str, Any]:
        v = self.last_verdict or self.verdict()
        return {
            "tool": "build_loop",
            "ok": True,
            "complete": v.complete,
            "playbook": self.route.playbook.id,
            "nudges": self.nudges,
            "stop_reason": self.stop_reason,
            "missing": list(v.missing_steps),
            "slug": self.slug,
        }
