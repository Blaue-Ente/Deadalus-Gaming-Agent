"""Kernel Mentor — helm for the Playground build loop.

The model writes game files. Mentor does not. It inspects the slug, reads a
little RAG, and returns a structured ``Steer`` so PLAN PROGRESS NEXT is not
"repeat the last failed tool".

Always-on correctness (Phaser-as-loop, vite playbook, proof on exhaust, retry
cap) lives in the write gate / router / build loop. This module is the
**opt-out helm**: extra recover rounds, RAG-backed NEXT, persona wrap-up.

Toggle (highest wins):

1. ``DAEDALUS_MENTOR=0/false/off`` — off for this process (tests/CI).
2. ``DAEDALUS_MENTOR=1/true/on`` — on, even if Studio saved off.
3. KV ``adn:daedalus_mentor`` — Studio persist. Default **on** when unset.

The director LLM (owner-chosen strong model, tight token budget) lives in
``daedalus.mentor_helm``. ``steer()`` stays deterministic so tests and the
unambiguous Vite-scaffold path never spend tokens.
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

_DB_KEY = "adn:daedalus_mentor"
_DEFAULT_ON = True
_EXTRA_ROUNDS = 2
_MAX_GRANTS = 1

_MAIN_CANDIDATES = (
    "src/main.js",
    "src/main.ts",
    "src/game.js",
    "src/game.ts",
    "main.js",
    "game.js",
)
_SCAFFOLD_RE = re.compile(
    r"""querySelector\(\s*['\"]#app['\"]\)|getElementById\(\s*['\"]app['\"]\)"""
    r"""|hello\s+vite|vite\.svg""",
    re.I,
)
_PHASER_BOOT_RE = re.compile(r"new\s+phaser\.game\b", re.I)
_RAF_RE = re.compile(r"requestanimationframe|new\s+pixi\.application", re.I)
_CANVAS_RE = re.compile(r"<canvas\b|id=[\"']game[\"']", re.I)


def env_override() -> bool | None:
    raw = (os.environ.get("DAEDALUS_MENTOR") or "").strip().lower()
    if raw in {"0", "false", "off", "no"}:
        return False
    if raw in {"1", "true", "on", "yes"}:
        return True
    return None


def _kv_raw() -> Any:
    try:
        from . import kv
        return kv.get(_DB_KEY)
    except Exception:
        return None


def persisted_enabled() -> bool:
    raw = _kv_raw()
    if raw is None or str(raw).strip() == "":
        return _DEFAULT_ON
    return str(raw).strip().lower() not in {"0", "false", "off", "no"}


def enabled() -> bool:
    ov = env_override()
    if ov is not None:
        return ov
    return persisted_enabled()


def set_enabled(flag: bool) -> bool:
    from . import kv
    kv.set(_DB_KEY, "1" if flag else "0")
    log.info("Daedalus mentor set → %s", "on" if flag else "off")
    return enabled()


def status() -> dict[str, Any]:
    ov = env_override()
    raw = _kv_raw()
    persisted: bool | None
    if raw is None or str(raw).strip() == "":
        persisted = None
    else:
        persisted = persisted_enabled()
    out: dict[str, Any] = {
        "enabled": enabled(),
        "env_locked": ov is not None,
        "env_override": ov,
        "persisted": persisted,
        "default": _DEFAULT_ON,
    }
    try:
        from . import mentor_helm
        out["llm"] = mentor_helm.status_llm()
    except Exception:
        out["llm"] = {"enabled": False}
    return out


def apply_update(body: dict[str, Any] | None) -> dict[str, Any]:
    """Studio POST: toggle and/or director choice / daily token cap."""
    body = body or {}
    if body.get("enabled") is not None:
        set_enabled(bool(body["enabled"]))
    cid = str(body.get("choice_id") or body.get("id") or "").strip()
    provider = str(body.get("provider") or "").strip()
    model = str(body.get("model") or "").strip()
    if cid or (provider and model):
        from . import mentor_helm
        mentor_helm.set_choice(body)
    cap = body.get("day_token_cap")
    if cap is not None and cap != "":
        from . import mentor_helm
        mentor_helm.set_day_token_cap(int(cap))
    return status()


@dataclass
class ProjectView:
    slug: str = ""
    kind: str = ""
    index_html: str = ""
    main_path: str = ""
    main_js: str = ""
    package_json: str = ""
    has_canvas: bool = False
    has_phaser_boot: bool = False
    has_raf: bool = False
    scaffold_main: bool = False
    dist_exists: bool = False
    exists: bool = False

    @property
    def entry_boots(self) -> bool:
        return self.has_phaser_boot or self.has_raf


@dataclass
class Steer:
    action: str  # continue | ship | stop
    next_tool: str = ""
    next_args: dict[str, Any] = field(default_factory=dict)
    reason: str = ""
    blockers: list[str] = field(default_factory=list)
    rag_refs: list[str] = field(default_factory=list)
    forbid_tools: list[str] = field(default_factory=list)

    def next_line(self, *, slug: str = "") -> str:
        if self.action == "ship":
            return "NEXT: stop and summarize the slice. Do not dump leftover JS."
        if self.action == "stop":
            return f"NEXT: stop — {self.reason}".rstrip()
        tool = self.next_tool or "write_file"
        args = dict(self.next_args or {})
        if slug and tool in {
            "write_file", "read_file", "run_command", "browser", "playtest",
            "production", "game_lint", "assets",
        }:
            args.setdefault("slug", slug)
        hint = " ".join(f"{k}={v}" for k, v in args.items() if v not in (None, ""))
        line = f"NEXT: call {tool}" + (f" with {hint}" if hint else "")
        if self.reason:
            line += f" — {self.reason}"
        return line


def _read(path: Path, limit: int = 12000) -> str:
    try:
        if path.is_file():
            return path.read_text(encoding="utf-8", errors="replace")[:limit]
    except OSError:
        return ""
    return ""


def inspect_project(slug: str, *, kind: str = "") -> ProjectView:
    view = ProjectView(slug=slug or "", kind=kind or "")
    if not slug:
        return view
    try:
        from .playground import project_dir
        root = project_dir(slug)
    except Exception:
        return view
    if not root.is_dir():
        return view
    view.exists = True
    view.index_html = _read(root / "index.html")
    view.package_json = _read(root / "package.json")
    view.dist_exists = (root / "dist").is_dir()
    for rel in _MAIN_CANDIDATES:
        text = _read(root / rel)
        if text:
            view.main_path = rel
            view.main_js = text
            break
    blob = f"{view.index_html}\n{view.main_js}"
    view.has_canvas = bool(_CANVAS_RE.search(view.index_html) or _CANVAS_RE.search(view.main_js))
    view.has_phaser_boot = bool(_PHASER_BOOT_RE.search(blob))
    view.has_raf = bool(_RAF_RE.search(blob))
    view.scaffold_main = bool(view.main_js) and bool(_SCAFFOLD_RE.search(view.main_js)) and not view.has_phaser_boot
    return view


def _rag_hits(query: str) -> list[str]:
    try:
        from .agent import get_daedalus
        rows = get_daedalus().memory.retrieve_knowledge(query, top_k=3) or []
    except Exception:
        return []
    out: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        text = str(row.get("text") or row.get("document") or row.get("content") or "").strip()
        if text:
            out.append(text[:400])
    return out


def _failed_tools(loop: Any) -> list[str]:
    from .tool_catalog import canonical

    names: list[str] = []
    for o in getattr(loop, "unrecovered_failures", lambda: [])():
        names.append(canonical(o.tool).lower())
    return names


def extra_round_budget(loop: Any) -> int:
    """Bounded recover rounds after native budget exhaust. 0 when off."""
    if not enabled() or loop is None:
        return 0
    if getattr(loop, "mentor_grants", 0) >= _MAX_GRANTS:
        return 0
    if not getattr(loop, "_has_writes", lambda: False)():
        return 0
    decision = _decision_for(loop)
    if decision is None or decision.action != "continue" or not decision.next_tool:
        return 0
    from .tool_catalog import canonical

    fails = getattr(loop, "unrecovered_failures", lambda: [])()
    if fails:
        last = canonical(fails[-1].tool).lower()
        nxt = canonical(decision.next_tool).lower()
        capped = getattr(loop, "retry_capped", lambda _t: False)(fails[-1].tool)
        if nxt == last and capped:
            return 0
    return _EXTRA_ROUNDS


def steer(loop: Any) -> Steer | None:
    """Return a helm decision, or None when Mentor is off / nothing to say."""
    if not enabled() or loop is None:
        return None
    slug = str(getattr(loop, "slug", "") or "")
    kind = str(getattr(loop, "kind", "") or "")
    view = inspect_project(slug, kind=kind)
    forbid: list[str] = []
    retry_capped = getattr(loop, "retry_capped", lambda _t: False)
    if retry_capped("generate_image"):
        forbid.append("generate_image")

    blockers: list[str] = []
    if view.scaffold_main:
        blockers.append(
            f"{view.main_path or 'src/main.js'} is still the Vite scaffold — "
            "the canvas will stay blank until it boots Phaser.Game (or rAF)."
        )
    elif view.has_canvas and view.main_js and not view.entry_boots:
        blockers.append("index.html has a canvas but the JS entry never boots a game loop.")
    elif view.has_canvas and not view.main_js:
        blockers.append("canvas in index.html but no src/main.js (or game.js) yet.")

    rag: list[str] = []
    if view.scaffold_main or kind in {"vite", "vite-react", "phaser"}:
        rag = _rag_hits("vite phaser src/main.js Phaser.Game entry not scaffold")

    v = getattr(loop, "last_verdict", None) or (
        loop.verdict() if hasattr(loop, "verdict") else None
    )
    plan_tool = ""
    plan_args: dict[str, Any] = {}
    plan_why = ""
    nxt = getattr(v, "next_step", None) if v is not None else None
    if nxt is not None and getattr(nxt, "tool", None):
        plan_tool = str(nxt.tool)
        plan_args = dict(nxt.args or {})
        plan_why = str(nxt.why or "")

    proof_blank = bool(getattr(loop, "proof_blank", False))
    proof_ok = bool(
        getattr(loop, "kernel_proof_ran", False)
        and getattr(loop, "_has_successful_proof", lambda: False)()
        and not proof_blank
        and not getattr(loop, "proof_gap", "")
    )
    unrecovered = [
        o for o in getattr(loop, "unrecovered_failures", lambda: [])()
        if getattr(o, "tool", "").lower() not in set(forbid)
    ]

    if (
        view.entry_boots
        and proof_ok
        and not blockers
        and not unrecovered
        and not (getattr(v, "missing_steps", None) or [])
    ):
        return Steer(
            action="ship",
            reason="Entry boots and visual proof painted. Summarize honestly (prototype vs store).",
            rag_refs=rag,
            forbid_tools=forbid,
        )

    if view.scaffold_main or (view.has_canvas and not view.entry_boots):
        return Steer(
            action="continue",
            next_tool="write_file",
            next_args={
                "slug": slug,
                "path": view.main_path or "src/main.js",
            },
            reason=(
                "Overwrite the Vite #app scaffold with new Phaser.Game "
                "(scenes in src/scenes/). Do not retry generate_image."
                if "generate_image" in forbid
                else "Overwrite src/main.js so Phaser.Game (or rAF) actually boots."
            ),
            blockers=blockers,
            rag_refs=rag,
            forbid_tools=forbid,
        )

    if getattr(loop, "_has_writes", lambda: False)() and not getattr(loop, "kernel_proof_ran", False):
        return Steer(
            action="continue",
            next_tool="browser",
            next_args={"slug": slug, "action": "goto"},
            reason="Files exist — open the running game. Compile is not proof.",
            blockers=blockers,
            rag_refs=rag,
            forbid_tools=forbid,
        )

    if proof_blank:
        return Steer(
            action="continue",
            next_tool="write_file",
            next_args={"slug": slug, "path": view.main_path or "src/main.js"},
            reason="Last proof frame was blank. Fix the entry, do not repeat generate_image.",
            blockers=blockers or ["blank screen"],
            rag_refs=rag,
            forbid_tools=forbid,
        )

    if plan_tool and not retry_capped(plan_tool):
        return Steer(
            action="continue",
            next_tool=plan_tool,
            next_args=plan_args,
            reason=plan_why or f"Required plan step {plan_tool}.",
            blockers=blockers,
            rag_refs=rag,
            forbid_tools=forbid,
        )

    if unrecovered and retry_capped(unrecovered[-1].tool):
        return Steer(
            action="continue",
            next_tool="write_file",
            next_args={"slug": slug, "path": view.main_path or "src/main.js"},
            reason=(
                f"{unrecovered[-1].tool} failed twice — change approach "
                "(Boot canvas textures / skip PNG / fix the entry)."
            ),
            blockers=blockers,
            rag_refs=rag,
            forbid_tools=forbid,
        )

    if v is not None and not getattr(v, "complete", False) and plan_tool:
        return Steer(
            action="continue",
            next_tool=plan_tool,
            next_args=plan_args,
            reason=plan_why or "Finish the plan.",
            blockers=blockers,
            rag_refs=rag,
            forbid_tools=forbid,
        )

    if getattr(loop, "_has_writes", lambda: False)() and not proof_ok:
        return Steer(
            action="continue",
            next_tool="browser",
            next_args={"slug": slug},
            reason="Still no painted visual proof.",
            blockers=blockers,
            rag_refs=rag,
            forbid_tools=forbid,
        )

    return Steer(
        action="continue",
        next_tool="write_file",
        next_args={"slug": slug, "path": view.main_path or "index.html"},
        reason="Keep writing the playable slice in the Playground slug.",
        blockers=blockers,
        rag_refs=rag,
        forbid_tools=forbid,
    )


def inspect_digest(loop: Any) -> dict[str, Any]:
    """Tiny JSON brief for the director LLM — flags and last fails, not source."""
    slug = str(getattr(loop, "slug", "") or "")
    kind = str(getattr(loop, "kind", "") or "")
    view = inspect_project(slug, kind=kind)
    last_fails: list[str] = []
    for o in getattr(loop, "unrecovered_failures", lambda: [])()[:4]:
        tool = str(getattr(o, "tool", "") or "")
        snippet = str(getattr(o, "head", "") or "")[:80]
        last_fails.append(f"{tool}:{snippet}" if snippet else tool)
    flags = {
        "scaffold_vite_app_div": bool(view.scaffold_main),
        "has_canvas": bool(view.has_canvas),
        "entry_boots": bool(view.entry_boots),
        "has_phaser_boot": bool(view.has_phaser_boot),
        "proof_blank": bool(getattr(loop, "proof_blank", False)),
        "kernel_proof_ran": bool(getattr(loop, "kernel_proof_ran", False)),
        "image_retry_capped": bool(getattr(loop, "retry_capped", lambda _t: False)("generate_image")),
    }
    return {
        "kind": kind,
        "scaffold": bool(view.scaffold_main),
        "loop_ok": bool(view.entry_boots),
        "has_src_main": bool(view.main_js),
        "has_index_html": bool(view.index_html),
        "play_url": "",
        "proof_blank": bool(getattr(loop, "proof_blank", False)),
        "flags": flags,
        "last_fails": last_fails,
        "native_hint": str(getattr(loop, "stop_reason", "") or ""),
    }


def steer_payload(decision: Steer | None) -> dict[str, Any]:
    if decision is None:
        return {
            "ok": False,
            "action": "continue",
            "next_tool": None,
            "next_args": {},
            "reason": "",
            "forbid_tools": [],
            "blockers": [],
            "source": "rules",
        }
    action = decision.action
    if action == "continue" and decision.next_tool:
        action = "call_tool"
    return {
        "ok": True,
        "action": action,
        "next_tool": decision.next_tool or None,
        "next_args": dict(decision.next_args or {}),
        "reason": (decision.reason or "")[:140],
        "forbid_tools": list(decision.forbid_tools or []),
        "blockers": list(decision.blockers or []),
        "source": "rules",
        "rag_refs": list(decision.rag_refs or [])[:3],
    }


def steer_from_payload(payload: dict[str, Any]) -> Steer:
    action = str(payload.get("action") or "continue")
    if action == "call_tool":
        action = "continue"
    if action not in {"continue", "ship", "stop"}:
        action = "continue"
    return Steer(
        action=action,
        next_tool=str(payload.get("next_tool") or ""),
        next_args=dict(payload.get("next_args") or {}) if isinstance(payload.get("next_args"), dict) else {},
        reason=str(payload.get("reason") or "")[:140],
        blockers=[str(x) for x in (payload.get("blockers") or []) if x][:8],
        forbid_tools=[str(x) for x in (payload.get("forbid_tools") or []) if x][:12],
        rag_refs=[str(x) for x in (payload.get("rag_refs") or []) if x][:3],
    )


def _decision_for(loop: Any) -> Steer | None:
    if not enabled() or loop is None:
        return None
    cached = getattr(loop, "mentor_steer", None)
    if isinstance(cached, dict) and cached.get("ok"):
        return steer_from_payload(cached)
    return steer(loop)


async def refresh_helm(loop: Any) -> dict[str, Any] | None:
    """Ask the director (or skip) and cache on the loop. Safe to await often."""
    if not enabled() or loop is None:
        return None
    from . import mentor_helm

    inspect = inspect_digest(loop)
    rules = steer_payload(steer(loop))
    fp = mentor_helm.digest_fingerprint(inspect)
    cached = getattr(loop, "mentor_steer", None)
    if isinstance(cached, dict) and cached.get("ok") and getattr(loop, "mentor_fp", None) == fp:
        return cached
    turn_calls = int(getattr(loop, "mentor_llm_calls", 0) or 0)
    out = await mentor_helm.consult(inspect, rules, turn_calls=turn_calls)
    loop.mentor_steer = out
    loop.mentor_fp = fp
    if out.get("source") == "llm":
        loop.mentor_llm_calls = turn_calls + 1
    return out


def progress_next(loop: Any, *, fallback: str = "") -> str:
    """PLAN PROGRESS NEXT line. Empty string → caller uses the old heuristic."""
    decision = _decision_for(loop)
    if decision is None:
        return fallback
    slug = str(getattr(loop, "slug", "") or "")
    lines = [decision.next_line(slug=slug)]
    if decision.forbid_tools:
        lines.append("FORBID: " + ", ".join(decision.forbid_tools) + " (same error twice — change approach).")
    if decision.blockers:
        lines.extend(f"  • {b}" for b in decision.blockers[:3])
    return "\n".join(lines)


def persona_wrapup(loop: Any) -> str:
    if not enabled() or loop is None:
        return ""
    decision = _decision_for(loop)
    cached = getattr(loop, "mentor_steer", None)
    director = isinstance(cached, dict) and cached.get("source") == "llm"
    lines = ["━━━ MENTOR (director) ━━━" if director else "━━━ MENTOR (kernel helm) ━━━"]
    if decision is None:
        return ""
    if decision.action == "ship":
        lines.append(
            "Steer: ship. Short summary of the slice. "
            "Do not claim store/App Store quality unless the entry boots and proof painted."
        )
    elif decision.action == "stop":
        lines.append(f"Steer: stop — {decision.reason}")
    else:
        lines.append(
            "Steer: not done. Do not dump leftover JS into chat. "
            "Do not emit NEED_* / JSON tool dumps. The kernel already wrote files."
        )
        lines.append(decision.next_line(slug=str(getattr(loop, "slug", "") or "")))
        if decision.forbid_tools:
            lines.append("Do not retry: " + ", ".join(decision.forbid_tools) + ".")
        if decision.blockers:
            lines.extend(f"  • {b}" for b in decision.blockers[:3])
    view = inspect_project(str(getattr(loop, "slug", "") or ""), kind=str(getattr(loop, "kind", "") or ""))
    if view.scaffold_main:
        lines.append(
            "Honest: src/main.js is still the Vite scaffold — Chromium will show a blank page."
        )
    elif getattr(loop, "proof_blank", False):
        lines.append("Honest: last proof frame was blank. Prototype, not store-ready.")
    return "\n".join(lines)


def maybe_capture_lesson(loop: Any, memory: Any = None) -> None:
    if not enabled() or loop is None or memory is None:
        return
    decision = _decision_for(loop)
    if decision is None or not decision.forbid_tools:
        return
    try:
        memory.capture_lesson(
            f"Mentor: skip {', '.join(decision.forbid_tools)} after repeated failure; "
            f"NEXT {decision.next_tool} ({decision.reason})",
            kind="mentor_steer",
        )
    except Exception:
        log.debug("mentor lesson capture skipped", exc_info=True)
