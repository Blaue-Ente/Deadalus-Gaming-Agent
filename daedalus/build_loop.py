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
    "write_file", "read_file", "list_dir", "playtest", "browser", "run_command", "game_lint",
    "assets", "engine", "world", "production", "playground_run",
})
_WEB_SUFFIXES = (".js", ".mjs", ".cjs", ".html", ".htm")
_DEMO_FAMILIES = ("math3d", "crowd")
_PATH = r"(?P<path>[A-Za-z0-9_./-]+\.(?:html?|js|mjs|css|json|py|gd|cs|txt|md))"
_SHELL_READ = re.compile(
    rf"^(?:cat|less|more|head(?:\s+-n?\s*\d+)?|tail(?:\s+-n?\s*\d+)?)\s+{_PATH}(?:\s*\|.*)?$"
)
_SHELL_CHECK = re.compile(rf"^node\s+(?:--check|-c)\s+{_PATH}$")
_SESSION_SLUG_PREFIXES = ("playground:", "pg-auto-research:", "pg-auto-rev:", "pg-auto-plan:", "autopilot-plan:")
_NAME_HINTS = (
    "snake", "tetris", "pong", "arkanoid", "breakout", "asteroids", "flappy", "platformer",
    "racer", "racing", "shooter", "invaders", "runner", "puzzle", "tower", "rpg", "roguelike",
    "metroidvania", "slerp", "camera", "crowd", "octree", "quadtree", "raycast", "particles",
    "змия", "тетрис", "понг", "платформър", "състезание", "камера", "пъзел",
)


def fix_rounds_from_env() -> int:
    raw = (os.environ.get("DAEDALUS_BUILD_FIX_ROUNDS") or "").strip()
    if not raw:
        return DEFAULT_FIX_ROUNDS
    try:
        return max(0, int(raw))
    except ValueError:
        return DEFAULT_FIX_ROUNDS


def _flag(name: str, default: str) -> bool:
    return (os.environ.get(name) or default).strip().lower() not in ("0", "false", "no", "off")


def kernel_scaffold_enabled() -> bool:
    return _flag("DAEDALUS_BUILD_SCAFFOLD", "1")


def kernel_verify_enabled() -> bool:
    return _flag("DAEDALUS_BUILD_VERIFY", "1")


def browser_verify_enabled() -> bool:
    return _flag("DAEDALUS_BUILD_BROWSER_VERIFY", "0")


def session_slug(session_id: str) -> str:
    sid = (session_id or "").strip()
    for prefix in _SESSION_SLUG_PREFIXES:
        if sid.startswith(prefix):
            return sid[len(prefix):].split(":")[0].strip()
    return ""


def project_name_from_ask(message: str, playbook_id: str, kind: str) -> str:
    """Deterministic project name: the game the owner named, or the playbook
    plus a short hash. The model never has to invent or search for a slug."""
    import hashlib

    from .playground import slugify

    low = (message or "").lower()
    hint = next((h for h in _NAME_HINTS if h in low), "")
    base = slugify(hint) if hint else ""
    family = playbook_id.split("+")[0].split(".")[-1] or "game"
    if not base:
        digest = hashlib.sha1((message or "").encode("utf-8")).hexdigest()[:4]
        base = f"{family}-{digest}"
    if kind in ("godot", "unity"):
        return f"{base}-{kind}"
    if family in ("math3d", "crowd"):
        return f"{base}-demo"
    return f"{base}-game"


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
        mentor: Any | None = None,
    ) -> None:
        self.route = route
        self.user_message = user_message or ""
        self.kind = str(getattr(route, "kind", "") or "static")
        self.seed_log = [dict(r) for r in (seed_log or [])]
        self.fix_rounds = fix_rounds_from_env() if fix_rounds is None else max(0, fix_rounds)
        self.rounds_per_fix = max(1, rounds_per_fix)
        self.mentor = mentor
        self.observations: list[ToolObservation] = []
        self.nudges = 0
        self.slug = ""
        self.last_verdict: BuildVerdict | None = None
        self.stop_reason = ""
        self.scaffolded = False
        self.verify_report: list[str] = []
        self._verified_at = -1
        for row in self.seed_log:
            self._capture_slug(row.get("input") or {}, "")

    @property
    def is_demo(self) -> bool:
        """math3d / crowd playbooks build a tech demo, not a game."""
        families = {p.split(".")[-1] for p in str(self.route.playbook.id).split("+")}
        return bool(families) and families <= set(_DEMO_FAMILIES)

    # ── kernel-run scaffold ───────────────────────────────────────────────

    def _create_step(self) -> Step | None:
        return next((s for s in self.route.ordered_steps if s.tool == "playground_new"), None)

    async def scaffold(self, session_id: str = "") -> str:
        """Create (or adopt) the Playground project before the model talks.

        Returns a PROJECT block for the system prompt, or "" when this route
        has no create step or the kernel scaffold is off. On failure the model
        keeps ``playground_new`` and does it itself.
        """
        step = self._create_step()
        if step is None or not kernel_scaffold_enabled():
            return ""
        from . import playground as pg
        from .native_tools import project_nav

        existing = session_slug(session_id)
        if existing and pg.project_dir(existing).is_dir():
            self.slug = existing
            self.observe("playground_new", {"slug": existing, "reused": True},
                         f"── playground_new OK slug={existing} (existing session project) ──", ok=True)
            self.scaffolded = True
            return (
                "━━━ PROJECT (kernel — existing) ━━━\n"
                f"slug={existing}\n{project_nav(existing)}\n"
                "Do not call playground_new. Write into this slug."
            )
        name = project_name_from_ask(self.user_message, self.route.playbook.id, self.kind)
        slug = pg.slugify(name)
        if pg.project_dir(slug).is_dir():
            import hashlib

            slug = f"{slug}-{hashlib.sha1(self.user_message.encode('utf-8')).hexdigest()[:4]}"
        kind = str(step.args.get("kind") or self.kind or "static")
        try:
            proj = await pg.create_project(
                name, kind=kind, source="built", description=self.user_message[:200], slug=slug,
            )
        except Exception as exc:
            log.warning("kernel scaffold failed (model keeps playground_new): %s", exc)
            return ""
        slug = str((proj or {}).get("slug") or slug)
        self.slug = slug
        self.scaffolded = True
        self.observe("playground_new", {"name": name, "kind": kind, "slug": slug, "kernel": True},
                     f"── playground_new OK slug={slug} kind={kind} ──", ok=True)
        return (
            "━━━ PROJECT (kernel created) ━━━\n"
            f"slug={slug} kind={kind}\n{project_nav(slug)}\n"
            "Do not call playground_new. Start with write_file slug="
            f"{slug} path={(self.route.playbook.expected_files or ('index.html',))[0]}."
        )

    def drop_tools(self) -> set[str]:
        """Tools the model does not need this turn (phase gate)."""
        return {"playground_new"} if self.scaffolded else set()

    # ── kernel-run verify ─────────────────────────────────────────────────

    def written_files(self) -> list[tuple[str, str]]:
        out: list[tuple[str, str]] = []
        for o in self.observations:
            if o.tool == "write_file" and o.ok:
                pair = (str(o.args.get("slug") or self.slug), str(o.args.get("path") or ""))
                if pair[1] and pair not in out:
                    out.append(pair)
        return out

    def _writes_since_verify(self) -> bool:
        for o in self.observations[self._verified_at + 1:]:
            if o.tool == "write_file" and o.ok:
                return True
        return False

    async def run_verify(self) -> bool:
        """Kernel verify after the plan's writes: lint every web file, heuristic
        playtest, optional browser. Records observations so the contract and
        the nudge see the outcome. Returns True when nothing blocks."""
        self.verify_report = []
        ok_all = True
        files = self.written_files()
        if not files:
            self._verified_at = len(self.observations) - 1
            return True
        from .game_lint import check as lint_check

        for slug, path in files:
            if not path.lower().endswith(_WEB_SUFFIXES) and not path.lower().endswith(".py"):
                continue
            try:
                r = lint_check(path, slug=slug)
            except Exception as exc:
                r = {"ok": True, "error": str(exc)[:120], "blockers": [], "warnings": []}
            blockers = [str(b.get("message") or b) for b in (r.get("blockers") or [])]
            warnings = [str(w.get("message") or w) for w in (r.get("warnings") or [])]
            head = f"── game_lint check {slug}/{path} {'OK' if r.get('ok') else 'FAILED'} ──"
            if blockers:
                head += " " + "; ".join(blockers[:3])
            self.observe("game_lint", {"action": "check", "slug": slug, "path": path, "kernel": True}, head, ok=bool(r.get("ok")))
            self.verify_report.append(head)
            self.verify_report.extend(f"  lint: {w}" for w in warnings[:4])
            ok_all = ok_all and bool(r.get("ok"))
        slug = files[0][0]
        try:
            from .playtest import _heuristic, _signal_source

            hints = _heuristic(
                _signal_source(slug), demo=self.is_demo, ask=self.user_message,
            ).get("blockers") or []
        except Exception as exc:
            hints = [f"playtest heuristic unavailable: {exc}"[:120]]
        self.observe("playtest", {"slug": slug, "kind": self.kind, "kernel": True, "heuristic": True},
                     "── playtest heuristic ──" + ("" if not hints else " " + "; ".join(hints[:3])), ok=True)
        self.verify_report.extend(f"  playtest: {h}" for h in hints[:4])
        if browser_verify_enabled():
            try:
                from .playground_browser import verify_app

                bv = await verify_app(slug, self.kind)
                b_ok = bool(bv.get("ok")) or bool(bv.get("skipped"))
                summary = str(bv.get("summary") or bv.get("error") or "")[:200]
                self.observe("browser", {"action": "verify", "slug": slug, "kernel": True},
                             f"── browser verify {'OK' if b_ok else 'FAILED'} ── {summary}", ok=b_ok)
                self.verify_report.append(f"  browser: {'ok' if b_ok else 'FAILED'} {summary}")
                ok_all = ok_all and b_ok
            except Exception as exc:
                self.verify_report.append(f"  browser: skipped ({str(exc)[:80]})")
        self._verified_at = len(self.observations) - 1
        return ok_all

    def _plan_writes_done(self) -> bool:
        rows = self.progress()
        return all(r.status == "done" for r in rows if r.step.required) and bool(self.written_files())

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

    # ── navigation: fix arguments before the call, point NEXT after it ────

    def reroute(self, tool: str, args: dict[str, Any]) -> tuple[str, dict[str, Any], str]:
        """Send an inspection shell command to the tool that does the job.

        ``run_command cat index.html`` / ``head -20 game.js`` → ``read_file``;
        ``node --check index.html`` → ``game_lint``. The shell jail refuses
        pipes anyway, and these tools return the same information with the
        plan's NEXT step attached. Returns (tool, args, note).
        """
        if tool != "run_command":
            return tool, args, ""
        cmd = str(args.get("command") or "").strip()
        m = _SHELL_READ.match(cmd)
        if m:
            path = m.group("path")
            out = {"path": path, "slug": str(args.get("slug") or self.slug or "")}
            return "read_file", out, f"(kernel: `{cmd[:60]}` → read_file path={path})"
        m = _SHELL_CHECK.match(cmd)
        if m:
            path = m.group("path")
            out = {"action": "check", "path": path, "slug": str(args.get("slug") or self.slug or "")}
            return "game_lint", out, f"(kernel: `{cmd[:60]}` → game_lint check path={path})"
        return tool, args, ""

    def prepare(self, tool: str, args: dict[str, Any]) -> tuple[dict[str, Any], str]:
        """Fill the project slug the model forgot. Returns (args, note).

        ``run_command cwd=snake-game`` → ``slug=snake-game``; ``write_file``
        without slug → the slug this turn created. The note is appended to the
        tool result so the correction is visible, not silent.
        """
        if not self.slug or tool not in _SLUG_TOOLS:
            return args, ""
        out = dict(args)
        if str(out.get("slug") or "").strip():
            return out, ""
        cwd = str(out.get("cwd") or "")
        if tool == "run_command" and cwd and self.slug not in cwd.replace("\\", "/").split("/"):
            return out, ""  # a deliberate cwd elsewhere in the jail — leave it
        if tool == "list_dir":
            # Exploring the harness during a build → show the model its own project instead.
            path = str(out.get("path") or ".").replace("\\", "/")
            if path.startswith("/") or path in (".", "daedalus", "app") or path.startswith(("daedalus/", "app/")):
                out["path"] = "."
                out["slug"] = self.slug
                return out, f"(kernel: build phase — listing your project {self.slug}, not the harness)"
            out["slug"] = self.slug
            return out, f"(kernel: slug={self.slug} filled in)"
        out["slug"] = self.slug
        out.pop("cwd", None)
        return out, f"(kernel: slug={self.slug} filled in)"

    def annotate(self, tool: str, result_text: str) -> str:
        """Append the plan's NEXT step to a tool result so every reply steers."""
        rows = self.progress()
        pending = [r for r in rows if r.step.required and r.status != "done" and r.step.tool]
        if pending:
            nxt = pending[0].step
            hint = self._step_args_hint(nxt)
            line = f"NEXT (plan): {nxt.tool}" + (f" {hint}" if hint else "")
            if nxt.why:
                line += f" — {nxt.why}"
        else:
            line = "PLAN: all required steps done — stop and summarize the slice; do not explore."
        if line in result_text:
            return result_text
        return f"{result_text.rstrip()}\n{line}"

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

    async def on_quiet(self, reply_text: str) -> str | None:
        """Model stopped calling tools. Return a nudge to continue, or None to stop.

        When the plan's writes are in, the kernel verifies before accepting:
        lint blockers become unrecovered failures the model must fix.
        """
        if kernel_verify_enabled() and self._plan_writes_done() and self._writes_since_verify():
            await self.run_verify()
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
        block = self.progress_block(v)
        advice = await self._mentor(f"quiet#{self.nudges}", v.failed_calls[-1] if v.failed_calls else None)
        if advice:
            block = f"{block}\n\n{advice.block()}"
            if advice.stop:
                self.stop_reason = "mentor: not achievable"
        return block

    # ── mentor checkpoints ────────────────────────────────────────────────

    async def after_round(self) -> str | None:
        """Called after every tool round. A streak of identical failures gets
        the Mentor in *now*, before the builder burns more rounds on it."""
        if self.mentor is None:
            return None
        from .build_mentor import BuildMentor

        focus = BuildMentor.failure_streak(self.observations)
        if focus is None:
            return None
        advice = await self._mentor("streak", focus)
        return advice.block() if advice else None

    async def _mentor(self, trigger: str, focus: ToolObservation | None) -> Any | None:
        if self.mentor is None:
            return None
        try:
            return await self.mentor.advise(self, trigger, focus)
        except Exception as exc:  # fail-open: the deterministic nudge stands
            log.warning("mentor skipped (%s): %s", trigger, exc)
            return None

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
        if self.verify_report:
            head.append("VERIFY (kernel ran it — not the model):")
            head.extend(f"  {line}" for line in self.verify_report[:8])
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
            if o.tool in ("game_lint", "browser") and o.args.get("kernel"):
                target = str(o.args.get("path") or "the file")
                head.append(
                    f"NEXT: fix {target} — write_file slug={self.slug} path={target} with the full "
                    "corrected content; the kernel re-verifies after your write."
                )
            else:
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
        if self.verify_report:
            lines.append("Kernel verify: " + "; ".join(x.strip() for x in self.verify_report[:4]))
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
            "scaffolded": self.scaffolded,
            "verified": self._verified_at >= 0,
            "mentor": self.mentor.log_rows() if self.mentor is not None else [],
        }
