"""Mentor — the senior engineer the kernel calls when the builder is stuck.

The build loop is deterministic: it marks plan steps, verifies files and
nudges with a template. That is enough when the model merely forgot a step.
It is not enough when the model is *wrong in a loop* — nine ``perspective``
calls with the same bad argument, or three fix rounds spent on a failure that
was already repaired. A template cannot see why; a second model can.

The Mentor is that model, used sparingly and only at decision points:

* a tool fails twice in a row with the same shape (``failure_streak``);
* the builder goes quiet with the plan incomplete (``on_quiet``).

It receives the ask, the plan with status, the ledger, the failing tool's
exact schema and RAG knowledge retrieved for the *error*, and answers with a
small JSON: a diagnosis, an imperative instruction, and optionally one
read-only ``consult`` tool call (math3d, spatial, game_canon, game_lint,
read_file, list_dir). The kernel runs the consult itself and hands the result
to the builder — the right tool arrives at the right place without the weak
model having to discover it.

Bounded (``DAEDALUS_MENTOR_MAX_CALLS``, default 3 per build) and fail-open:
any error leaves the deterministic nudge in place. Model: ``DAEDALUS_MODEL_MENTOR``
(role ``mentor``), or the primary model.
"""
from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

log = logging.getLogger(__name__)

DEFAULT_MAX_CALLS = 3
STREAK = 2
CONSULT_TOOLS = frozenset({"math3d", "spatial", "game_canon", "game_lint", "read_file", "list_dir"})
_JSON_OBJ = re.compile(r"\{.*\}", re.S)
_ARGS_CHARS = 160
_HEAD_CHARS = 220
_FOCUS_CHARS = 700
_KNOWLEDGE_CHARS = 500

CallFn = Callable[..., Awaitable[str]]
RecallFn = Callable[[str, int], list[str]]
ConsultFn = Callable[[str, dict[str, Any]], Awaitable[str]]

MENTOR_SYSTEM = """You are the Mentor: a senior game/tools engineer supervising a junior builder model.
The builder can only act through tools. You see its plan, its tool ledger, the exact schema of the tool it keeps failing, and reference knowledge. You do not write the game. You unblock the builder.

Answer with ONE JSON object and nothing else:
{"diagnosis": "<one sentence: what is actually wrong>",
 "instruction": "<1-3 imperative sentences the builder must do next, naming the exact tool and exact arguments>",
 "consult": {"tool": "<math3d|spatial|game_canon|game_lint|read_file|list_dir>", "args": {...}} or null,
 "stop": false}

Rules:
- If the failure is a wrong argument shape, give the corrected call literally (tool name + JSON args).
- Use "consult" when running a read-only tool yourself would hand the builder the missing fact (a matrix, an octree recommendation, the file it wrote). The kernel runs it and passes the result on.
- If a step was already achieved by another call (the file exists, the value is known), say so and tell the builder to move on to the next plan step instead of retrying.
- Never send the builder to explore the harness, read agent code, or search the disk. Stay inside the project slug.
- Keep the owner's task exactly as asked. Do not add features. Do not soften the Definition of Done.
- "stop": true only when the task is impossible with the available tools; then the instruction says what to report."""


def mentor_enabled() -> bool:
    return (os.environ.get("DAEDALUS_BUILD_MENTOR") or "1").strip().lower() not in ("0", "false", "no", "off")


def max_calls_from_env() -> int:
    raw = (os.environ.get("DAEDALUS_MENTOR_MAX_CALLS") or "").strip()
    if not raw:
        return DEFAULT_MAX_CALLS
    try:
        return max(0, int(raw))
    except ValueError:
        return DEFAULT_MAX_CALLS


@dataclass
class MentorAdvice:
    trigger: str
    diagnosis: str
    instruction: str
    consult_tool: str = ""
    consult_args: dict[str, Any] = field(default_factory=dict)
    consult_result: str = ""
    stop: bool = False

    def block(self) -> str:
        lines = ["━━━ MENTOR (senior engineer — kernel-run, not the builder) ━━━"]
        if self.diagnosis:
            lines.append(f"Diagnosis: {self.diagnosis}")
        if self.instruction:
            lines.append(f"Do now: {self.instruction}")
        if self.consult_tool:
            args = json.dumps(self.consult_args, ensure_ascii=False)[:_ARGS_CHARS]
            head = " ".join((self.consult_result or "").split())[:900]
            lines.append(f"Mentor ran {self.consult_tool} {args} for you →")
            lines.append(f"  {head}")
        if self.stop:
            lines.append("Mentor verdict: not achievable with these tools — report that plainly.")
        return "\n".join(lines)

    def as_row(self) -> dict[str, Any]:
        return {
            "trigger": self.trigger,
            "diagnosis": self.diagnosis[:200],
            "instruction": self.instruction[:300],
            "consult": self.consult_tool,
            "stop": self.stop,
        }


def _extract_json(text: str) -> dict[str, Any] | None:
    raw = (text or "").strip()
    raw = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", raw)
    candidates = [raw]
    m = _JSON_OBJ.search(raw)
    if m:
        candidates.append(m.group(0))
    for cand in candidates:
        try:
            obj = json.loads(cand)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            return obj
    return None


def _tool_schema(name: str) -> str:
    try:
        from .native_tools import NATIVE_TOOL_SPECS
    except Exception:
        return ""
    for spec in NATIVE_TOOL_SPECS:
        if spec.get("name") == name:
            return json.dumps(
                {"description": spec.get("description", ""), "input_schema": spec.get("input_schema", {})},
                ensure_ascii=False,
            )[:2200]
    return ""


def _fmt_args(args: dict[str, Any]) -> str:
    slim = {k: v for k, v in (args or {}).items() if k not in ("content", "kernel", "heuristic")}
    return json.dumps(slim, ensure_ascii=False, default=str)[:_ARGS_CHARS]


class BuildMentor:
    def __init__(
        self,
        call_fn: CallFn,
        *,
        recall_fn: RecallFn | None = None,
        consult_fn: ConsultFn | None = None,
        max_calls: int | None = None,
    ) -> None:
        self.call_fn = call_fn
        self.recall_fn = recall_fn
        self.consult_fn = consult_fn
        self.max_calls = max_calls_from_env() if max_calls is None else max(0, max_calls)
        self.calls = 0
        self.advice: list[MentorAdvice] = []
        self._seen: set[str] = set()

    @property
    def exhausted(self) -> bool:
        return self.calls >= self.max_calls

    # ── triggers ──────────────────────────────────────────────────────────

    @staticmethod
    def failure_streak(observations: list[Any], *, streak: int = STREAK) -> Any | None:
        """The observation that ends a run of ``streak`` consecutive failures of
        the same tool (kernel verify rows excluded — those have their own nudge)."""
        run = 0
        last = None
        for o in observations:
            if o.args.get("kernel") or o.args.get("mentor"):
                continue
            if not o.ok and not o.blocked and (last is None or last.tool == o.tool):
                run += 1
            elif not o.ok and not o.blocked:
                run = 1
            else:
                run = 0
            last = o
        if run >= streak and last is not None and not last.ok:
            return last
        return None

    def _key(self, trigger: str, focus: Any | None) -> str:
        if focus is None:
            return trigger
        return f"{trigger}:{focus.tool}:{focus.head[:80]}"

    # ── the call ──────────────────────────────────────────────────────────

    async def advise(self, loop: Any, trigger: str, focus: Any | None = None, *, question: str = "") -> MentorAdvice | None:
        """One Mentor consultation. Returns None when out of budget, when this
        exact situation was already handled, or when the call fails."""
        if self.exhausted:
            return None
        key = self._key(trigger, focus) if not question else f"{trigger}:{question[:80]}"
        if key in self._seen:
            return None
        self._seen.add(key)
        self.calls += 1
        try:
            prompt = self.prompt(loop, trigger, focus, question=question)
            raw = await self.call_fn(MENTOR_SYSTEM, [{"role": "user", "content": prompt}], role="mentor")
        except Exception as exc:
            log.warning("mentor call failed (deterministic nudge stays): %s", exc)
            return None
        data = _extract_json(raw or "")
        if not data:
            log.warning("mentor returned no JSON: %r", (raw or "")[:160])
            return None
        advice = MentorAdvice(
            trigger=trigger,
            diagnosis=str(data.get("diagnosis") or "").strip()[:400],
            instruction=str(data.get("instruction") or "").strip()[:700],
            stop=bool(data.get("stop")),
        )
        consult = data.get("consult")
        if isinstance(consult, dict) and str(consult.get("tool") or "") in CONSULT_TOOLS:
            advice.consult_tool = str(consult["tool"])
            args = consult.get("args")
            advice.consult_args = dict(args) if isinstance(args, dict) else {}
            await self._run_consult(loop, advice)
        if not advice.instruction and not advice.consult_tool:
            return None
        self.advice.append(advice)
        return advice

    async def _run_consult(self, loop: Any, advice: MentorAdvice) -> None:
        if self.consult_fn is None:
            advice.consult_tool = ""
            return
        args = dict(advice.consult_args)
        slug = getattr(loop, "slug", "")
        if slug and advice.consult_tool in ("game_lint", "read_file", "list_dir"):
            args.setdefault("slug", slug)
        try:
            text = await self.consult_fn(advice.consult_tool, args)
        except Exception as exc:
            text = f"── {advice.consult_tool} FAILED: {str(exc)[:200]} ──"
        advice.consult_result = text or ""
        ok = "FAILED" not in text and "ERROR" not in text
        try:
            loop.observe(advice.consult_tool, {**args, "mentor": True}, text, ok=ok)
        except Exception:
            pass

    # ── prompt ────────────────────────────────────────────────────────────

    def prompt(self, loop: Any, trigger: str, focus: Any | None, *, question: str = "") -> str:
        parts: list[str] = []
        parts.append("TASK (owner):\n" + (loop.user_message or "")[:600])
        pb = loop.route.playbook
        plan = [f"PLAN {pb.id} · slug={loop.slug or '(none yet)'} · trigger={trigger}"]
        for r in loop.progress():
            plan.append(f"  {r.mark} {r.step.id}: {r.step.tool or '(docs)'}" + (f" — {r.detail[:120]}" if r.detail else ""))
        parts.append("\n".join(plan))
        files = loop.written_files()
        if files:
            parts.append("FILES WRITTEN: " + ", ".join(f"{s}/{p}" for s, p in files[:8]))
        ledger = ["LEDGER (last calls, newest last):"]
        for o in loop.observations[-10:]:
            status = "BLOCKED" if o.blocked else ("ok" if o.ok else "FAIL")
            ledger.append(f"  {status:7} {o.tool} {_fmt_args(o.args)} → {o.head[:_HEAD_CHARS]}")
        parts.append("\n".join(ledger))
        if focus is not None:
            parts.append(
                f"FOCUS — {focus.tool} failed {STREAK}+ times in a row:\n"
                f"  args: {_fmt_args(focus.args)}\n  result: {focus.head[:_FOCUS_CHARS]}"
            )
            schema = _tool_schema(focus.tool)
            if schema:
                parts.append(f"SCHEMA of {focus.tool}:\n{schema}")
        elif loop.last_verdict is not None:
            v = loop.last_verdict
            parts.append("KERNEL VERDICT: " + ("; ".join(v.blockers) or "incomplete")
                         + (f"\n  missing steps: {', '.join(v.missing_steps)}" if v.missing_steps else ""))
            if loop.verify_report:
                parts.append("KERNEL VERIFY:\n" + "\n".join(loop.verify_report[:6]))
        if question:
            parts.append("BUILDER QUESTION:\n" + question[:500])
        knowledge = self._recall(loop, focus)
        if knowledge:
            parts.append("KNOWLEDGE (RAG, retrieved for this error):\n" + "\n".join(f"  • {k}" for k in knowledge))
        parts.append("Answer with the JSON object only.")
        return "\n\n".join(parts)

    def _recall(self, loop: Any, focus: Any | None) -> list[str]:
        if self.recall_fn is None:
            return []
        if focus is not None:
            query = f"{focus.tool} {focus.head[:160]}"
        else:
            v = loop.last_verdict
            query = f"{loop.route.playbook.title} {'; '.join(v.blockers) if v else ''}"[:220]
        try:
            rows = self.recall_fn(query, 3) or []
        except Exception as exc:
            log.debug("mentor recall skipped: %s", exc)
            return []
        return [" ".join(str(r).split())[:_KNOWLEDGE_CHARS] for r in rows if str(r).strip()][:3]

    def log_rows(self) -> list[dict[str, Any]]:
        return [a.as_row() for a in self.advice]
