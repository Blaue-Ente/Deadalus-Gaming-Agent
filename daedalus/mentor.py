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
_MAX_GRANTS = 2

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
_PHASER_PARENT_RE = re.compile(r"parent\s*:\s*['\"]([^'\"]+)['\"]", re.I)
_HTML_ID_RE = re.compile(r"\bid\s*=\s*['\"]([^'\"]+)['\"]", re.I)
_RAF_RE = re.compile(r"requestanimationframe|new\s+pixi\.application", re.I)
_CANVAS_RE = re.compile(r"<canvas\b|id=[\"']game[\"']", re.I)
_REL_IMPORT_RE = re.compile(
    r"""(?:from|import)\s+['\"](\.[^'\"]+)['\"]""",
    re.I,
)
_VITE_KINDS = frozenset({"vite", "vite-react", "phaser"})


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
        log.exception("mentor director status unavailable")
        out["llm"] = {"enabled": False, "catalog": [], "spend": {}}
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
    needs_npm: bool = False
    phaser_parent: str = ""
    html_ids: tuple[str, ...] = ()
    mount_mismatch: bool = False
    scene_files: tuple[str, ...] = ()
    missing_scenes: bool = False
    missing_imports: tuple[str, ...] = ()

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


def _missing_src_imports(root: Path, *, main_path: str, main_js: str) -> tuple[str, ...]:
    """Relative ``from './x.js'`` in src/ that do not exist on disk."""
    src = root / "src"
    try:
        root_res = root.resolve()
        main_res = (root / main_path).resolve() if main_path else None
    except OSError:
        return ()
    files: list[Path] = []
    seen: set[Path] = set()
    candidates: list[Path] = []
    if main_path:
        candidates.append(root / main_path)
    if src.is_dir():
        candidates.extend(
            p for p in src.rglob("*")
            if p.is_file() and p.suffix.lower() in {".js", ".ts"}
        )
    for path in candidates:
        try:
            key = path.resolve()
        except OSError:
            continue
        if key in seen or not path.is_file():
            continue
        seen.add(key)
        files.append(path)
    missing: list[str] = []
    miss_seen: set[str] = set()
    for js in files:
        text = main_js if main_res is not None and js.resolve() == main_res and main_js else _read(js)
        for m in _REL_IMPORT_RE.finditer(text or ""):
            spec = m.group(1).split("?")[0].split("#")[0]
            target = (js.parent / spec).resolve()
            if target.is_file():
                continue
            if not target.suffix:
                if any(target.with_suffix(ext).is_file() for ext in (".js", ".ts")):
                    continue
            try:
                rel = str(target.relative_to(root_res)).replace("\\", "/")
            except ValueError:
                rel = spec.lstrip("./")
            if rel in miss_seen:
                continue
            miss_seen.add(rel)
            missing.append(rel)
            if len(missing) >= 6:
                return tuple(missing)
    return tuple(missing)


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
    parent_m = _PHASER_PARENT_RE.search(view.main_js)
    view.phaser_parent = (parent_m.group(1).strip() if parent_m else "")
    view.html_ids = tuple(dict.fromkeys(_HTML_ID_RE.findall(view.index_html)))
    view.mount_mismatch = bool(view.phaser_parent) and view.phaser_parent not in view.html_ids
    scenes_dir = root / "src" / "scenes"
    if scenes_dir.is_dir():
        view.scene_files = tuple(
            sorted(
                p.name
                for p in scenes_dir.iterdir()
                if p.is_file() and p.suffix.lower() in {".js", ".ts"}
            )
        )
    view.missing_scenes = (
        not view.scaffold_main
        and bool(view.has_phaser_boot)
        and not view.scene_files
        and (
            (kind or "").lower() in _VITE_KINDS
            or "phaser" in (view.package_json or "").lower()
        )
    )
    view.missing_imports = _missing_src_imports(
        root, main_path=view.main_path, main_js=view.main_js,
    )
    pkg = view.package_json.lower()
    has_dep = "phaser" in pkg or '"vite"' in pkg or "'vite'" in pkg
    mods = root / "node_modules"
    view.needs_npm = bool(view.package_json) and (
        has_dep or (kind or "").lower() in _VITE_KINDS
    ) and not (mods / "phaser").is_dir() and not (mods / "vite").is_dir()
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

    nxt = canonical(decision.next_tool).lower()
    # Proof already failed — extra rounds are for the *writer*, not another browser.
    if nxt in {"browser", "engine", "visual_proof"} and _proof_runtime_failed(loop):
        return 0
    fails = getattr(loop, "unrecovered_failures", lambda: [])()
    if fails:
        last = canonical(fails[-1].tool).lower()
        capped = getattr(loop, "retry_capped", lambda _t: False)(fails[-1].tool)
        if nxt == last and capped:
            return 0
    return _EXTRA_ROUNDS


def _proof_runtime_failed(loop: Any) -> bool:
    """Chromium ran and the page is not a pass (uncaught / blank / failed requests)."""
    if getattr(loop, "kernel_proof_ran", False):
        if getattr(loop, "proof_gap", ""):
            return False
        return not bool(getattr(loop, "proof_ok", False))
    hits = []
    if hasattr(loop, "_proof_hits"):
        try:
            hits = list(loop._proof_hits() or [])
        except Exception:
            hits = []
    if not hits:
        return False
    return any(not o.ok for o in hits) and not any(o.ok for o in hits)


def _proof_error_head(loop: Any) -> str:
    errs = [str(x) for x in (getattr(loop, "proof_errors", None) or []) if x]
    if errs:
        return errs[0][:100]
    summary = str(getattr(loop, "proof_summary", "") or "").strip()
    return summary[:100]


def _writer_fix(
    slug: str,
    view: ProjectView,
    *,
    reason: str,
    blockers: list[str],
    rag: list[str],
    forbid: list[str],
    path: str = "",
) -> Steer:
    return Steer(
        action="continue",
        next_tool="write_file",
        next_args={"slug": slug, "path": path or view.main_path or "src/main.js"},
        reason=reason,
        blockers=blockers,
        rag_refs=rag,
        forbid_tools=forbid,
    )


def steer(loop: Any) -> Steer | None:
    """Return a helm decision, or None when Mentor is off / nothing to say.

    Checks fail closed. A failed proof does **not** mean another browser call —
    it returns the defect to the writer (write_file / npm). Mentor does not write.
    """
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
    if view.needs_npm:
        blockers.append("package.json lists Vite/Phaser but node_modules is missing — npm install first.")
    if view.mount_mismatch:
        blockers.append(
            f"Phaser parent '{view.phaser_parent}' is not an id in index.html "
            f"(have: {', '.join(view.html_ids) or 'none'})."
        )

    rag: list[str] = []
    if view.scaffold_main or kind in _VITE_KINDS:
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
    proof_failed = _proof_runtime_failed(loop)
    proof_ok = bool(
        getattr(loop, "kernel_proof_ran", False)
        and getattr(loop, "_has_successful_proof", lambda: False)()
        and not proof_blank
        and not getattr(loop, "proof_gap", "")
        and not proof_failed
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
        return _writer_fix(
            slug, view,
            reason=(
                "Overwrite the Vite #app scaffold with new Phaser.Game "
                "(scenes in src/scenes/). Do not retry generate_image."
                if "generate_image" in forbid
                else "Overwrite src/main.js so Phaser.Game (or rAF) actually boots."
            ),
            blockers=blockers, rag=rag, forbid=forbid,
        )

    if view.needs_npm:
        return Steer(
            action="continue",
            next_tool="run_command",
            next_args={"slug": slug, "command": "npm install", "timeout": 120},
            reason=(
                "RETURN TO WRITER: Vite/Phaser deps are not installed. "
                'run_command command="npm install" slug=' + (slug or "<project>")
                + " — no pipes, no &&. Then the kernel will open the game."
            ),
            blockers=blockers,
            rag_refs=rag,
            forbid_tools=forbid,
        )

    if view.mount_mismatch:
        return _writer_fix(
            slug, view,
            path="index.html",
            reason=(
                f"RETURN TO WRITER: Phaser parent '{view.phaser_parent}' is missing in HTML. "
                f"Add <div id=\"{view.phaser_parent}\"></div> (or change parent). Mentor does not write."
            ),
            blockers=blockers, rag=rag, forbid=forbid,
        )

    if view.missing_scenes:
        return _writer_fix(
            slug, view,
            path="src/scenes/Boot.js",
            reason=(
                "RETURN TO WRITER: Phaser.Game is in src/main.js but src/scenes/ is empty. "
                "write_file src/scenes/Boot.js (then Menu / Grove / Dusk / Result). Mentor does not write."
            ),
            blockers=blockers or ["missing src/scenes"],
            rag=rag, forbid=forbid,
        )

    if view.missing_imports:
        gap = view.missing_imports[0]
        return _writer_fix(
            slug, view,
            path=gap,
            reason=(
                f"RETURN TO WRITER: {gap} is imported but missing on disk. "
                "write_file that path. Mentor does not write."
            ),
            blockers=blockers or [f"missing {gap}"],
            rag=rag, forbid=forbid,
        )

    if proof_blank or proof_failed:
        err = _proof_error_head(loop)
        why = "Last proof frame was blank." if proof_blank else "Visual proof failed."
        if err:
            why = f"{why} {err}"
        return _writer_fix(
            slug, view,
            reason=(
                f"RETURN TO WRITER: {why} "
                "You write the fix (entry / mount / import). Do not open browser again. Mentor does not write."
            )[:140],
            blockers=blockers or ["visual proof failed"],
            rag=rag, forbid=forbid,
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

    from .visual_proof import PROOF_TOOLS as _PROOF

    if plan_tool and plan_tool.lower() not in _PROOF and not retry_capped(plan_tool):
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
        last = unrecovered[-1].tool
        if str(last).lower() in _PROOF:
            return _writer_fix(
                slug, view,
                reason="RETURN TO WRITER: browser/proof failed twice. Fix the files; Mentor does not write.",
                blockers=blockers, rag=rag, forbid=forbid,
            )
        return _writer_fix(
            slug, view,
            reason=(
                f"{last} failed twice — change approach "
                "(Boot canvas textures / skip PNG / fix the entry)."
            ),
            blockers=blockers, rag=rag, forbid=forbid,
        )

    if v is not None and not getattr(v, "complete", False) and plan_tool and plan_tool.lower() not in _PROOF:
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
            reason="Still no painted visual proof — kernel will open the running game.",
            blockers=blockers,
            rag_refs=rag,
            forbid_tools=forbid,
        )

    return _writer_fix(
        slug, view,
        path=view.main_path or "index.html",
        reason="Keep writing the playable slice in the Playground slug.",
        blockers=blockers, rag=rag, forbid=forbid,
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
        "needs_npm": bool(view.needs_npm),
        "mount_mismatch": bool(view.mount_mismatch),
        "missing_scenes": bool(view.missing_scenes),
        "missing_imports": bool(view.missing_imports),
        "proof_blank": bool(getattr(loop, "proof_blank", False)),
        "proof_failed": _proof_runtime_failed(loop),
        "kernel_proof_ran": bool(getattr(loop, "kernel_proof_ran", False)),
        "image_retry_capped": bool(getattr(loop, "retry_capped", lambda _t: False)("generate_image")),
    }
    errs = [str(x)[:80] for x in (getattr(loop, "proof_errors", None) or []) if x][:3]
    if errs:
        last_fails = list(last_fails) + [f"proof:{e}" for e in errs]
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
    lines: list[str] = []
    nxt = (decision.next_tool or "").lower()
    if decision.action == "continue" and nxt in {"write_file", "run_command"}:
        lines.append(
            "RETURN TO WRITER: a check failed. You (the model that writes files) "
            "must fix it. Mentor does not write game files."
        )
        if nxt == "write_file":
            lines.append(
                "Do not list_dir / playground_read / find. The check already named the defect. "
                "write_file the fix now."
            )
    lines.append(decision.next_line(slug=slug))
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
            "Steer: not done. A check failed — RETURN TO WRITER. "
            "Do not dump leftover JS into chat. Do not emit NEED_* / JSON tool dumps. "
            "Mentor does not write files. Call the NEXT tool now."
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
    elif getattr(loop, "proof_blank", False) or _proof_runtime_failed(loop):
        err = _proof_error_head(loop)
        lines.append(
            "Honest: visual proof failed"
            + (f" ({err})" if err else "")
            + ". Not store-ready. Writer must fix, then the kernel opens the game again."
        )
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
