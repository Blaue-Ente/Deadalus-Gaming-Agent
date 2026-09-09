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
from .visual_proof import PROOF_TOOLS, enabled as visual_proof_enabled, is_visual_kind

log = logging.getLogger(__name__)

DEFAULT_FIX_ROUNDS = 3
ROUNDS_PER_FIX = 2
_HEAD_CHARS = 240
_SLUG_RE = re.compile(r"\bslug=([A-Za-z0-9_.-]+)")
_KIND_RE = re.compile(r"\bkind=([A-Za-z0-9_.-]+)")
_SLUG_TOOLS = frozenset({
    "write_file", "read_file", "playtest", "browser", "run_command", "game_lint",
    "assets", "engine", "world", "production", "playground_run",
})
_WRITE_TOOLS = frozenset({"write_file", "need_playground_write", "edit_file", "need_playground_edit"})


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
        self.proof_frames: list[str] = []
        self.proof_vision: list[dict[str, Any]] = []
        self.proof_gap = ""
        self.kernel_proof_ran = False
        self.proof_blank = False
        self.proof_ok = False
        self.proof_errors: list[str] = []
        self.proof_summary = ""
        self.mentor_grants = 0
        self.mentor_steer = None
        self.mentor_fp = ""
        self.mentor_llm_calls = 0
        for row in self.seed_log:
            self._capture_slug(row.get("input") or {}, "")
            self._capture_kind("playground_new", row.get("input") or {}, "")

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
        self._capture_kind(tool, args, result_text)

    def _capture_slug(self, args: dict[str, Any], result_text: str) -> None:
        if self.slug:
            return
        cand = str(args.get("slug") or "").strip()
        if not cand:
            m = _SLUG_RE.search(result_text or "")
            cand = m.group(1) if m else ""
        if cand:
            self.slug = cand

    def _capture_kind(self, tool: str, args: dict[str, Any], result_text: str) -> None:
        name = canonical(tool).lower() if tool else ""
        k = str(args.get("kind") or args.get("type") or "").strip().lower()
        if not k:
            m = _KIND_RE.search(result_text or "")
            k = (m.group(1) if m else "").strip().lower()
        if k and (name == "playground_new" or not self.kind or self.kind == "static"):
            if name == "playground_new" or k in {"vite", "vite-react", "phaser", "godot", "unity"}:
                self.kind = k

    def tool_calls(self) -> list[dict[str, Any]]:
        return [*self.seed_log, *(o.as_call() for o in self.observations)]

    def _has_writes(self) -> bool:
        for o in self.observations:
            if canonical(o.tool).lower() in _WRITE_TOOLS and o.ok:
                return True
        for row in self.seed_log:
            if canonical(str(row.get("tool") or "")).lower() in _WRITE_TOOLS and row.get("ok"):
                return True
        return False

    def _proof_hits(self) -> list[ToolObservation]:
        return [o for o in self.observations if canonical(o.tool).lower() in PROOF_TOOLS]

    def _has_successful_proof(self) -> bool:
        return any(o.ok for o in self._proof_hits())

    def _writes_after_last_proof(self) -> bool:
        last = -1
        for i, o in enumerate(self.observations):
            if canonical(o.tool).lower() in PROOF_TOOLS:
                last = i
        if last < 0:
            return True
        return any(
            canonical(o.tool).lower() in _WRITE_TOOLS and o.ok
            for o in self.observations[last + 1:]
        )

    def _required_build_pending(self) -> bool:
        for row in self.progress():
            if not row.step.required or row.status == "done":
                continue
            if (row.step.tool or "").lower() in PROOF_TOOLS:
                continue
            return True
        return False

    def wants_kernel_proof(self) -> bool:
        """Quiet round should open the running game before declaring done."""
        if not visual_proof_enabled() or not self.active:
            return False
        if not is_visual_kind(self.kind):
            return False
        if not self.slug or not self._has_writes():
            return False
        if self._required_build_pending():
            return False
        if self.proof_gap:
            return False
        if self._has_successful_proof():
            return False
        if not self.kernel_proof_ran:
            return True
        return self._writes_after_last_proof()

    def wants_exhaust_proof(self) -> bool:
        """Budget ran out while the model was still calling tools — still open the game."""
        if not visual_proof_enabled():
            return False
        if not is_visual_kind(self.kind):
            return False
        if not self.slug or not self._has_writes():
            return False
        if self.proof_gap:
            return False
        if self._has_successful_proof() and not self._writes_after_last_proof():
            return False
        return not self.kernel_proof_ran or self._writes_after_last_proof()

    def note_kernel_proof(self, result: Any) -> None:
        self.kernel_proof_ran = True
        self.proof_gap = str(getattr(result, "honest_gap", "") or "")
        self.proof_frames = list(getattr(result, "frames", None) or [])
        self.proof_blank = bool(getattr(result, "blank_screen", False))
        self.proof_ok = bool(getattr(result, "ok", False)) and not bool(getattr(result, "skipped", False))
        self.proof_summary = str(getattr(result, "summary", "") or "")
        details = getattr(result, "details", None) or {}
        errs: list[str] = []
        for key in ("page_errors", "console_errors", "failed_requests"):
            for item in (details.get(key) or [])[:4]:
                text = str(item or "").strip()
                if text:
                    errs.append(text[:160])
        self.proof_errors = errs[:6]
        try:
            self.proof_vision = list(result.vision_blocks())
        except Exception:
            self.proof_vision = []

    def browser_ok_for_dod(self) -> bool | None:
        if not visual_proof_enabled() or not is_visual_kind(self.kind):
            return None
        if not self._has_writes():
            return None
        if self.proof_gap or self.proof_blank or (self.kernel_proof_ran and not self.proof_ok):
            return False
        if self._has_successful_proof():
            return True
        return False

    def retry_capped(self, tool: str, *, n: int = 2) -> bool:
        key = canonical(tool).lower()
        fails = [
            o for o in self.observations
            if canonical(o.tool).lower() == key and not o.ok
        ]
        return len(fails) >= n

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
            browser_ok=self.browser_ok_for_dod(),
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
        if (
            visual_proof_enabled()
            and is_visual_kind(self.kind)
            and self._has_writes()
            and not missing
        ):
            if self.proof_gap:
                v.complete = False
                gap = {
                    "chromium_missing": (
                        "Chromium is not on PATH — visual proof was not taken (not a skip-pass)."
                    ),
                    "godot_missing": (
                        "Godot is not on PATH — engine proof was not taken (files are not Play)."
                    ),
                    "unity_local": "Unity Editor is not in this process. Scaffold is not Play.",
                    "no_slug": "No Playground slug — cannot open the game.",
                }.get(self.proof_gap, f"Visual proof unavailable ({self.proof_gap}).")
                if gap not in v.blockers:
                    v.blockers.append(gap)
            elif not self._has_successful_proof():
                v.complete = False
                msg = (
                    "Няма visual proof от работещата игра (browser/engine). "
                    "Compile не е доказателство."
                )
                if msg not in v.blockers:
                    v.blockers.append(msg)
                if nxt is None or (nxt.tool and nxt.tool not in PROOF_TOOLS):
                    prove = next(
                        (r.step for r in rows if (r.step.tool or "") in PROOF_TOOLS and r.status != "done"),
                        None,
                    )
                    if prove is not None:
                        v.next_step = prove
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
        if self.proof_gap and self.kernel_proof_ran:
            self.stop_reason = "visual proof unavailable"
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
        next_line = self._next_line(v)
        if next_line:
            head.append(next_line)
        head.append(
            "Call the tool now; do not describe it. Do not explore the harness "
            "(no list_dir / find outside the slug). No NEED_* markers, no SQL."
        )
        return "\n".join(head)

    def _fallback_after_cap(self, o: ToolObservation) -> str:
        tool = canonical(o.tool).lower()
        slug = f" slug={self.slug}" if self.slug else ""
        if tool == "generate_image":
            return (
                f"NEXT: call write_file{slug} path=src/main.js "
                "(or src/scenes/Boot.js canvas textures). "
                "Do not retry generate_image — same error twice."
            )
        if tool in PROOF_TOOLS:
            return (
                "NEXT: fix the files with write_file"
                + slug
                + " so the running game is not blank/broken. "
                "The kernel will open it again after you stop."
            )
        path = "src/main.js" if self.kind in {"vite", "vite-react", "phaser"} else "index.html"
        return (
            f"NEXT: call write_file{slug} path={path} — "
            f"{o.tool} failed twice with the same class of error; change approach."
        )

    def _next_line(self, v: BuildVerdict) -> str:
        try:
            from . import mentor as _mentor
            if _mentor.enabled():
                steered = _mentor.progress_next(self)
                if steered:
                    return steered
        except Exception:
            log.debug("mentor NEXT skipped", exc_info=True)
        if v.next_step is not None and v.next_step.tool:
            hint = self._step_args_hint(v.next_step)
            return (
                f"NEXT: call {v.next_step.tool}" + (f" with {hint}" if hint else "")
                + (f" — {v.next_step.why}" if v.next_step.why else "")
            )
        if v.failed_calls:
            o = v.failed_calls[-1]
            if canonical(o.tool).lower() in PROOF_TOOLS or self.retry_capped(o.tool):
                return self._fallback_after_cap(o)
            return (
                f"NEXT: repeat {o.tool} with corrected arguments"
                + (f" (slug={self.slug})" if self.slug and o.tool in _SLUG_TOOLS else "")
                + ", then finish the plan."
            )
        return ""

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
            if self.proof_frames:
                lines.append("Visual proof frames: " + ", ".join(self.proof_frames[:4]))
        else:
            lines.append("Plan NOT complete. Say so plainly; list what remains:")
            lines.extend(f"  • {b}" for b in v.blockers)
            if self.proof_frames:
                lines.append("Last proof frames: " + ", ".join(self.proof_frames[:4]))
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
            "proof_frames": list(self.proof_frames),
            "proof_gap": self.proof_gap,
            "mentor_source": (
                (self.mentor_steer or {}).get("source")
                if isinstance(self.mentor_steer, dict) else None
            ),
            "mentor_llm_calls": int(getattr(self, "mentor_llm_calls", 0) or 0),
            "mentor_next": (
                (self.mentor_steer or {}).get("next_tool")
                if isinstance(self.mentor_steer, dict) else None
            ),
            "mentor_skip": (
                (self.mentor_steer or {}).get("llm_skip")
                if isinstance(self.mentor_steer, dict) else None
            ),
            "mentor_model": (
                (self.mentor_steer or {}).get("llm_model")
                if isinstance(self.mentor_steer, dict) else None
            ),
            "mentor_reason": (
                str((self.mentor_steer or {}).get("reason") or "")[:140]
                if isinstance(self.mentor_steer, dict) else ""
            ),
        }
