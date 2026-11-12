"""Kernel tool router — match ask → playbook graph → tool subset + verify.

The LLM is not the router. Cheap models dump JSON in the wrong place or
search ``daedalus/agent.py`` for tools that already exist. This module
picks the graph first, injects the plan, optionally runs consult tools
(docs, spatial advise, game_canon study), then exposes only those native
functions for the build turn.
"""
from __future__ import annotations

import logging
import functools
import re
from dataclasses import dataclass, field
from typing import Any

from .task_playbooks import PLAYBOOKS, Playbook, Step, get as get_playbook
from .tool_catalog import canonical, excerpt

_KIND_EQ = re.compile(r"kind\s*=\s*([a-z0-9_-]+)", re.I)
_ARCADE_TITLE = re.compile(r"\b(snake|tetris|pong|змия)\b", re.I)
_EXPLICIT_WEB_KIND = re.compile(r"kind\s*=\s*(?:vite|phaser)\b", re.I)

log = logging.getLogger(__name__)

_N_BODIES = re.compile(
    r"\b(\d{2,5})\s*(?:bodies|entities|colliders|actors|агенти|тела|частици)\b",
    re.I,
)
_N_EQ = re.compile(r"\bn\s*[=:]?\s*(\d{2,5})\b", re.I)


@dataclass
class Route:
    playbook: Playbook
    ordered_steps: list[Step]
    tool_names: tuple[str, ...]
    kind: str
    n: int = 0
    dim: int = 3
    use_build_subset: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {
            "playbook": self.playbook.id,
            "title": self.playbook.title,
            "kind": self.kind,
            "tools": list(self.tool_names),
            "n": self.n,
            "dim": self.dim,
            "steps": [
                {"id": s.id, "tool": s.tool, "required": s.required, "auto": s.auto, "why": s.why}
                for s in self.ordered_steps
            ],
        }


@dataclass
class PlanScore:
    playbook_id: str
    complete: bool
    missing: list[str] = field(default_factory=list)
    used: list[str] = field(default_factory=list)
    blocker: str = ""


def infer_n(text: str, default: int = 64) -> int:
    blob = text or ""
    m = _N_BODIES.search(blob) or _N_EQ.search(blob)
    if m:
        return max(2, int(m.group(1)))
    return default


def infer_dim(text: str) -> int:
    low = (text or "").lower()
    if any(k in low for k in ("2d", "canvas", "змия", "snake", "tetris")) and "3d" not in low:
        return 2
    return 3


def infer_kind(message: str, playbook_kind: str = "", kind: str = "") -> str:
    """Prefer an explicit kind= in the ask over the playbook default.

    ``kind=phaser`` is the Vite + Phaser seed, so the route reports ``vite``.
    """
    from .playground import normalize_engine_kind

    if (kind or "").strip():
        raw = kind.strip().lower()
    else:
        m = _KIND_EQ.search(message or "")
        if m:
            raw = m.group(1).lower()
        else:
            raw = (playbook_kind or "static").strip().lower() or "static"
    return normalize_engine_kind(raw)


def _topo(steps: tuple[Step, ...]) -> list[Step]:
    by_id = {s.id: s for s in steps}
    pending = list(steps)
    done: list[str] = []
    out: list[Step] = []
    guard = 0
    while pending and guard < 64:
        guard += 1
        progressed = False
        rest: list[Step] = []
        for s in pending:
            if all(d in done or d not in by_id for d in s.depends_on):
                out.append(s)
                done.append(s.id)
                progressed = True
            else:
                rest.append(s)
        pending = rest
        if not progressed:
            out.extend(pending)
            break
    return out


def _kw_hit(pb: Playbook, blob: str, kind: str) -> bool:
    if kind and pb.kinds and kind.lower() in {k.lower() for k in pb.kinds}:
        return True
    low = _strip_negations(blob).lower()
    if pb.regex and re.search(pb.regex, _strip_negations(blob), re.I):
        return True
    return any(_keyword_re(k).search(low) for k in pb.keywords)


@functools.lru_cache(maxsize=512)
def _keyword_re(keyword: str) -> "re.Pattern[str]":
    """A keyword is a word, not a substring (found live: "eas" in "please" picked the mobile build, "urp" in
    "purpose" Unity, "quest" in "request" the epic one). It must start a word; a Latin keyword must also end one,
    while a Cyrillic one is a stem ("камер" matches "камерата"). Symbols at either end (".tscn", "levels/", "c#")
    are their own boundary."""
    k = keyword.lower()
    start = r"(?<!\w)" if k[:1].isalnum() or k[:1] == "_" else ""
    latin = all(ord(ch) < 128 for ch in k)
    end = r"(?!\w)" if latin and (k[-1:].isalnum() or k[-1:] == "_") else ""
    return re.compile(start + re.escape(k) + end)


_NEGATED = re.compile(
    r"(?:не\s+чети|don't\s+read|do\s+not\s+read|do\s+not\s+open|"
    r"не\s+прави|don't\s+(?:make|build|write)|do\s+not\s+(?:make|build|write))\s+"
    r".{0,160}",
    re.I,
)


def _strip_negations(blob: str) -> str:
    """Drop 'don't read daedalus/agent.py' so it cannot pull harness.fix into a game."""
    return _NEGATED.sub(" ", blob or "")


def _copy_step(step: Step, new_id: str) -> Step:
    return Step(
        id=new_id,
        tool=step.tool,
        required=step.required,
        auto=step.auto,
        args=dict(step.args or {}),
        docs=step.docs,
        why=step.why,
        depends_on=step.depends_on,
        accept_any=step.accept_any,
    )


def _merge_playbooks(hits: list[Playbook]) -> Playbook:
    """Union graphs when the ask hits more than one kind (SLERP + octree).

    A game playbook plus harness.fix is not a union — the model was told
    *not* to read daedalus/agent.py. Keep the playground graph.
    """
    playground = [pb for pb in hits if pb.id.startswith("playground.")]
    if playground and any(pb.id == "harness.fix" for pb in hits):
        hits = [pb for pb in hits if pb.id != "harness.fix"]
        if len(hits) == 1:
            return hits[0]
    primary = hits[0]
    tools: list[str] = []
    for pb in hits:
        for name in pb.tools:
            if name not in tools:
                tools.append(name)
    files: list[str] = []
    for pb in hits:
        for rel in pb.expected_files:
            if rel not in files:
                files.append(rel)
    steps: list[Step] = []
    seen_ids: set[str] = set()
    for pb in hits:
        prefix = pb.id.split(".")[-1]
        for s in pb.steps:
            sid = s.id if s.id not in seen_ids else f"{prefix}_{s.id}"
            seen_ids.add(sid)
            steps.append(s if sid == s.id else _copy_step(s, sid))
    notes = " ".join(pb.notes for pb in hits if pb.notes)
    return Playbook(
        id="+".join(pb.id for pb in hits),
        title=" + ".join(pb.title for pb in hits),
        priority=primary.priority,
        kind=primary.kind,
        keywords=(),
        regex="",
        kinds=primary.kinds,
        tools=tuple(tools),
        steps=tuple(steps),
        expected_files=tuple(files),
        notes=notes,
        use_build_subset=any(pb.use_build_subset for pb in hits),
    )


def match_playbook(
    message: str,
    *,
    session_id: str = "",
    kind: str = "",
    intent: str = "",
) -> Playbook:
    from .game_engineer import looks_like_game
    from .playground_agent_routing import is_playground_work_session, treat_as_conversational

    blob = f"{message or ''} {kind or ''} {intent or ''}"
    pg = is_playground_work_session(session_id, message or "")
    game = looks_like_game(message or "", kind=kind, intent=intent)
    k = (kind or "").strip().lower()

    ranked = sorted(PLAYBOOKS, key=lambda p: p.priority, reverse=True)
    hits = [
        pb for pb in ranked
        if pb.id not in {"talk", "app.build", "playground.generic"}
        and _kw_hit(pb, blob, k)
    ]
    # Vite / Phaser briefs must not lose to canvas arcade on leftover keywords.
    # Named arcade titles (snake, tetris, pong) stay on the arcade graph.
    web_engine = (
        k in {"vite", "phaser", "vite-react"}
        or bool(_EXPLICIT_WEB_KIND.search(blob))
        or any(pb.id == "playground.vite" for pb in hits)
    )
    if hits and web_engine and not _ARCADE_TITLE.search(blob):
        hits = [pb for pb in hits if pb.id != "playground.arcade"] or hits
    if hits:
        return _merge_playbooks(hits) if len(hits) > 1 else hits[0]

    if (
        not pg
        and not game
        and treat_as_conversational(message or "", nav_intent=intent)
    ):
        found = get_playbook("talk")
        if found:
            return found

    if pg or game:
        generic = get_playbook("playground.generic")
        if generic:
            return generic
    app = get_playbook("app.build")
    assert app is not None
    return app


def route(
    message: str,
    *,
    session_id: str = "",
    kind: str = "",
    intent: str = "",
) -> Route:
    pb = match_playbook(message, session_id=session_id, kind=kind, intent=intent)
    n = infer_n(message)
    dim = infer_dim(message)
    kind_out = infer_kind(message, pb.kind, kind)
    names = pb.tools
    return Route(
        playbook=pb,
        ordered_steps=_topo(pb.steps),
        tool_names=names,
        kind=kind_out,
        n=n,
        dim=dim,
        use_build_subset=pb.use_build_subset and bool(names),
    )


def native_specs(rt: Route) -> list[dict[str, Any]] | None:
    """OpenAI tool subset for this route, or None to keep the full catalog."""
    if not rt.use_build_subset:
        return None
    from .native_tools import NATIVE_TOOL_SPECS

    want = set(rt.tool_names)
    specs = [t for t in NATIVE_TOOL_SPECS if t["name"] in want]
    if not specs:
        return None
    return specs


def _docs_block(steps: list[Step]) -> str:
    seen: set[str] = set()
    parts: list[str] = []
    for s in steps:
        for rel in s.docs:
            if rel in seen:
                continue
            seen.add(rel)
            body = excerpt(rel, limit=1100)
            if body:
                parts.append(f"── {rel} ──\n{body}")
        if len(parts) >= 3:
            break
    return "\n\n".join(parts)


def reply_language(text: str) -> str:
    """How to name the reply language: that of the request itself. Live 2026-11-12: English requests got Bulgarian
    replies ("the owner's language" read as Bulgarian, because the owner is). Only a request in plain ASCII letters is
    named English; any other script or accented text says "the same language the request is written in", so Bulgarian,
    Russian, Spanish or Chinese are each answered in kind, and code around the request cannot outvote it (review)."""
    letters = re.findall(r"[^\W\d_]", text or "")
    if not letters or all(ch.isascii() for ch in letters):
        return "English"
    return "the same language the request is written in"


def format_system(rt: Route, *, extra: str = "", user_message: str = "", offered: Any = None, slug: str = "") -> str:
    from .game_engineer import attach_to_sys

    pb = rt.playbook
    lines = [
        "You are Daedalus BUILDING now. Follow the TASK PLAN. Do not map the harness.",
        "Do not read daedalus/agent.py, kernel, or native_tools. Do not find / across the disk.",
        f"Playbook: {pb.id} — {pb.title}",
        f"Playground kind: {rt.kind}",
    ]
    if pb.notes:
        lines.append(pb.notes)
    lines.append("TASK PLAN (kernel — call these tools in order):")
    for i, s in enumerate(rt.ordered_steps, 1):
        tool = s.tool or "(docs)"
        flag = "REQUIRED" if s.required else ("AUTO" if s.auto else "optional")
        lines.append(f"  {i}. [{flag}] {s.id}: {tool} — {s.why or ''}".rstrip())
        if s.docs:
            lines.append(f"      docs: {', '.join(s.docs)}")
    lines.append("When the slice files exist, stop and summarize. No NEED_* dumps, no SQL.")
    from .mentor_lead import is_game_build                     # the same game test as the gates
    from .project_tests import GUIDANCE, upstream_repo
    from .task_effort import current as _effort_now
    # A game is kept and built on: its rules get tests. Not a cloned or revived repo: its structure is the upstream's.
    if (is_game_build(pb.id, user_message, slug) and str((_effort_now() or {}).get("tier") or "") != "whisper"
            and not (slug and upstream_repo(slug))):
        lines.append(GUIDANCE)
    lines.append(f"Write your replies to the owner in {reply_language(user_message)} (the owner's request). "
                 "Instructions here may be in another language; that is not the owner's.")
    from .harness.brain_tools import NAMES as _harness_tools, SYSTEM_HINT as _harness_hint
    names = {str(s.get("name") if isinstance(s, dict) else s) for s in offered} if offered is not None else set(pb.tools)
    if _harness_tools & names:                # A7: say what the harness tools are for whenever they are offered
        lines.append(_harness_hint)
    sys = "\n".join(lines)
    docs = _docs_block(rt.ordered_steps)
    if docs:
        sys = f"{sys}\n\n━━━ PLAN DOCS (harness, not RAG guesswork) ━━━\n{docs}"
    if extra:
        sys = f"{sys}\n\n{extra}"
    if rt.playbook.id != "playground.arcade":
        sys = attach_to_sys(sys, text=user_message or pb.title, kind=rt.kind)
    return sys



def used_tools(tool_calls: list[dict[str, Any]] | None) -> set[str]:
    names: set[str] = set()
    for c in tool_calls or []:
        raw = str(c.get("tool") or c.get("name") or "")
        if not raw:
            continue
        names.add(canonical(raw).lower())
        names.add(raw.lower())
    return names


def step_satisfied(step: Step, used: set[str]) -> bool:
    if not step.tool and not step.accept_any:
        return True
    candidates = [step.tool, *step.accept_any]
    for name in candidates:
        if not name:
            continue
        key = canonical(name).lower()
        if key in used or name.lower() in used:
            return True
    return False


def score_plan(rt: Route, tool_calls: list[dict[str, Any]] | None) -> PlanScore:
    if rt.playbook.id == "talk":
        return PlanScore(playbook_id=rt.playbook.id, complete=True)
    used = used_tools(tool_calls)
    missing: list[str] = []
    for s in rt.ordered_steps:
        if not s.required:
            continue
        if not step_satisfied(s, used):
            label = s.tool or s.id
            missing.append(f"{s.id} ({label})")
    blocker = ""
    if missing:
        blocker = (
            f"План {rt.playbook.id}: липсват задължителни стъпки "
            f"{', '.join(missing)} — извикай инструментите от TASK PLAN."
        )
    return PlanScore(
        playbook_id=rt.playbook.id,
        complete=not missing,
        missing=missing,
        used=sorted(used),
        blocker=blocker,
    )


def _consult_args(step: Step, rt: Route) -> dict[str, Any]:
    args = dict(step.args or {})
    if step.tool == "spatial" and args.get("action", "advise") == "advise":
        args.setdefault("n", rt.n or 64)
        args.setdefault("dim", rt.dim)
    return args


def run_consult(rt: Route) -> tuple[list[dict[str, Any]], str]:
    """Execute AUTO consult nodes (no LLM). Results go into the plan prompt + tool log."""
    log_rows: list[dict[str, Any]] = [
        {
            "tool": "task_plan",
            "ok": True,
            "playbook": rt.playbook.id,
            "kind": rt.kind,
        }
    ]
    chunks: list[str] = []
    for step in rt.ordered_steps:
        if not step.auto or not step.tool:
            continue
        args = _consult_args(step, rt)
        text, ok = _exec_consult(step.tool, args)
        log_rows.append({
            "tool": step.tool,
            "ok": ok,
            "auto": True,
            "step": step.id,
        })
        if text:
            chunks.append(f"── AUTO {step.tool} ({step.id}) ──\n{text}")
    extra = "\n\n".join(chunks)
    return log_rows, extra


def _exec_consult(name: str, args: dict[str, Any]) -> tuple[str, bool]:
    try:
        if name == "spatial":
            from .spatial import run_action
            r = run_action(str(args.get("action") or "advise"), **{
                k: v for k, v in args.items() if k != "action"
            })
            return _clip(r), bool(r.get("ok"))
        if name == "game_canon":
            from .game_canon import run_action
            r = run_action(
                str(args.get("action") or "study"),
                id=str(args.get("id") or ""),
            )
            return _clip(r), bool(r.get("ok"))
        if name == "skill":
            from .learned_skills import load_skill
            sid = str(args.get("id") or args.get("name") or "")
            loaded = load_skill(sid)
            if not loaded:
                return f"skill {sid} not found", False
            body = (loaded.get("body") or "")[:1600]
            return body, True
        if name == "engine":
            from .godot_first import preflight
            r = preflight(None)
            return _clip(r), bool(r.get("ok")) and not r.get("skipped")
        if name == "math3d":
            from .math3d import run_action
            op = str(args.get("op") or args.get("action") or "catalog")
            payload = {k: v for k, v in args.items() if k not in ("action", "op")}
            r = run_action(op, **payload)
            return _clip(r), bool(r.get("ok"))
    except Exception as exc:
        log.debug("consult %s failed: %s", name, exc)
        return str(exc)[:300], False
    return "", True


def _clip(data: Any, limit: int = 2500) -> str:
    import json
    try:
        text = json.dumps(data, ensure_ascii=False, default=str)
    except Exception:
        text = str(data)
    if len(text) > limit:
        return text[:limit] + "…"
    return text
