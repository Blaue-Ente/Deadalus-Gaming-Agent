"""Mentor as lead programmer: plans the build and accepts or returns it. It never writes code.

Owner's design (2026-11-11): the Mentor is the expensive, stronger model. It does not write code. It supervises
the process like a lead programmer. The model chosen for Daedalus is the main model: it does the work, calls the
tools and hands tasks to its workers.

Found live the same day: a cheap writer built "Star Catcher" with half the request missing (no meteors, no particles,
no neon, wrong restart key). The kernel's checks passed, because they test that the game runs, not that it is what the
owner asked for. The director was shown neither the request nor the project name, so it advised ``playground_new`` on
a project that already existed.

Two calls per build turn at most, both through the gateway (intent ``mentor``, budget-gated, metered):

* ``kickoff``: the owner's request becomes a plan for the writer: a short brief, a checklist of what the owner asked
  for, the look, and ordered steps.
* ``review``: when the kernel says "complete", the lead looks at the newest frames of the running game and the main
  source, then checks them against the checklist. If items are missing it sends the turn back with concrete fixes
  (``LEAD_FIX_ROUNDS``). Otherwise it accepts.

When the lead cannot run (Mentor off, rules-only, no key, day cap, run budget, an error), nothing is blocked: the
kernel verdict stands and the reason is recorded. The turn never pretends a review happened.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Iterator

log = logging.getLogger("daedalus.mentor_lead")

LEAD_FIX_ROUNDS = 2          # extra writer rounds the lead may ask for after the kernel said "complete"
MAX_REVIEWS = LEAD_FIX_ROUNDS + 1   # lead reviews per turn: one per fix round, plus the final look
PLAN_MAX_OUT = 900
REVIEW_MAX_OUT = 700
LEAD_TIMEOUT_MIN_S = 45.0
SOURCE_MAX_CHARS = 40_000    # ~10k tokens: the lead must see every file it judges (found live: 14k cut half a game)
REQUEST_MAX_CHARS = 1_500
_SOURCE_EXT = (".html", ".js", ".mjs", ".ts", ".jsx", ".tsx", ".css", ".gd", ".cs", ".py", ".json")
_SKIP_DIRS = {"node_modules", ".git", "dist", "build", "studio", "playtest", ".godot", "assets"}

PLAN_SYSTEM = (
    "You are the Daedalus Mentor: the lead programmer of a small game studio. You never write code and you never call "
    "tools. A builder model writes the files; your job is to plan its work so the result is exactly what the owner "
    "asked for, plays well and looks good. Read the owner's request and the project's current files. Return JSON only, "
    "no markdown: {\"brief\": \"<= 400 chars: the game in one paragraph\", \"checklist\": [\"every concrete thing "
    "the owner asked for, one short testable item each, max 12\"], \"look\": [\"visual direction, max 4 items\"], "
    "\"steps\": [\"ordered build steps for the builder, max 6\"]}. Keep the owner's words for controls, keys and "
    "rules. Do not add features the owner did not ask for, except what any finished game needs (start, restart, "
    "clear feedback). Write in the owner's language."
)

REVIEW_SYSTEM = (
    "You are the Daedalus Mentor: the lead programmer. You never write code. The builder says the work is done. Check "
    "it against the checklist using the frames of the running game (images in order: start screen, then after the "
    "kernel pressed start and played a few seconds) and the source. Return JSON only, no markdown: {\"accept\": "
    "true|false, \"met\": [\"checklist items that are met\"], \"missing\": [\"real defects only: an item that is "
    "absent or broken in the code or on screen, max 8\"], \"unverified\": [\"items you could not check from these "
    "frames and files, max 6\"], \"fixes\": [\"one concrete instruction per missing item: the file and what to "
    "change\"], \"observations\": [\"up to 3 short things visibly on screen\"]}. Missing means the builder must "
    "change something; lack of evidence is 'unverified', never 'missing'. Accept when nothing is missing and the game "
    "looks finished. Never claim to see what is not in the images. Write in the owner's language."
)


_REQUEST: ContextVar[str] = ContextVar("daedalus_owner_request", default="")


@contextlib.contextmanager
def owner_request(text: str) -> Iterator[None]:
    """The owner's own words for this turn (project chat wraps them in file context before the agent sees them)."""
    token = _REQUEST.set((text or "").strip())
    try:
        yield
    finally:
        _REQUEST.reset(token)


def request_text(fallback: str = "") -> str:
    return _REQUEST.get() or (fallback or "").strip()[-REQUEST_MAX_CHARS:]


# ── availability ─────────────────────────────────────────────────────────────

def unavailable_reason() -> str | None:
    """Why the lead cannot run now, or None."""
    try:
        from . import mentor, mentor_helm
    except Exception:
        return "import_error"
    if not mentor.enabled():
        return "mentor_off"
    if not mentor_helm.llm_enabled():
        return "llm_disabled"
    if mentor_helm.get_choice().get("id") == "rules":
        return "rules_only"
    if mentor_helm.over_day_cap():
        return "day_cap"
    try:
        from .run_budget import would_block
        if would_block():
            return "run_budget"
    except Exception:
        pass
    if mentor_helm._resolve_endpoint() is None:
        return "no_key"
    return None


def _timeout() -> float:
    from . import mentor_helm
    return max(LEAD_TIMEOUT_MIN_S, mentor_helm.llm_timeout_s())


async def _ask(system: str, user: str, *, max_tokens: int, images: list[dict[str, Any]] | None = None,
               client_id: str) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """One JSON call to the Mentor model. Returns (parsed, meta)."""
    from . import mentor_helm
    from .harness import gateway as gw
    from .scratch_journal import estimate_tokens

    provider, model = mentor_helm._resolve_endpoint() or ("", "")
    meta: dict[str, Any] = {"provider": provider, "model": model, "images": len(images or [])}
    route = mentor_helm._director_route(provider, model)
    content: Any = [gw.text_part(user), *images] if images else user
    t0 = time.monotonic()
    res = await gw.call("mentor", [{"role": "user", "content": content}], system=system, max_tokens=max_tokens,
                        temperature=0.2, json_mode=True, route=route, timeout=_timeout(), use_cache=False,
                        client_id=client_id)
    meta["ms"] = int((time.monotonic() - t0) * 1000)
    if not res.ok:
        err = res.error
        meta["error"] = getattr(err, "type", "") or "provider_error"
        if images and getattr(err, "type", "") == "unsupported_modality":
            meta["error"] = "model_has_no_vision"
        return None, meta
    tokens = estimate_tokens(system) + estimate_tokens(user) + estimate_tokens(res.text or "") + 800 * len(images or [])
    mentor_helm.record_spend(tokens)
    meta["tokens"] = tokens
    return _parse_json(res.text or ""), meta


def _parse_json(text: str) -> dict[str, Any] | None:
    blob = (text or "").strip()
    if blob.startswith("```"):
        blob = blob.strip("`")
        blob = blob[blob.find("{"):] if "{" in blob else blob
    start, end = blob.find("{"), blob.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(blob[start:end + 1])
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _strs(value: Any, limit: int, width: int = 200) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(x).strip()[:width] for x in value if str(x).strip()][:limit]


def _event(kind: str, payload: dict[str, Any]) -> None:
    try:
        from .harness import runs
        rid = runs.current_run_id()
        if rid:
            runs.safe_event(rid, kind, payload)
    except Exception:
        log.debug("mentor lead event skipped", exc_info=True)


# ── project view ─────────────────────────────────────────────────────────────

def _project_root(slug: str) -> Path | None:
    if not slug:
        return None
    try:
        from . import playground as pg
        root = pg.project_dir(slug)
        return root if root.is_dir() else None
    except Exception:
        return None


def project_files(slug: str, limit: int = 30) -> list[tuple[str, int]]:
    """(relative path, size) of the project's source files; tooling and asset folders are left out."""
    root = _project_root(slug)
    if root is None:
        return []
    out: list[tuple[str, int]] = []
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root)
        if any(part in _SKIP_DIRS or part.startswith(".") for part in rel.parts[:-1]) or not p.is_file():
            continue
        if p.suffix.lower() in _SOURCE_EXT or p.name == "README.md":
            out.append((rel.as_posix(), p.stat().st_size))
        if len(out) >= limit:
            break
    return out


def main_source(slug: str, budget: int = SOURCE_MAX_CHARS) -> str:
    """The project's code the lead reads: entry file first, then the largest scripts, within ``budget`` chars."""
    root = _project_root(slug)
    if root is None:
        return ""
    files = [f for f in project_files(slug, limit=60) if not f[0].endswith((".json", ".md"))]
    order = sorted(files, key=lambda f: (f[0] not in ("index.html", "project.godot", "src/main.js", "src/main.ts"),
                                         -f[1]))
    chunks: list[str] = []
    left = budget
    for rel, _size in order:
        if left <= 200:
            break
        try:
            text = (root / rel).read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        piece = text[: left - 60]
        chunks.append(f"=== {rel} ===\n{piece}" + ("\n…(truncated)" if len(piece) < len(text) else ""))
        left -= len(chunks[-1])
    return "\n\n".join(chunks)


# ── kickoff ─────────────────────────────────────────────────────────────────

async def kickoff(request: str, *, slug: str = "", kind: str = "") -> dict[str, Any] | None:
    """The lead's plan for this build turn, or None (reason in the log and Run ledger)."""
    request = (request or "").strip()[-REQUEST_MAX_CHARS:]
    if not request:
        return None
    why = unavailable_reason()
    if why:
        log.info("mentor lead plan skipped: %s", why)
        _event("mentor_plan", {"skipped": why})
        return None
    files = project_files(slug)
    user = (f"OWNER REQUEST:\n{request}\n\nPROJECT: {slug or '(new)'} · kind {kind or 'unknown'}\n"
            "FILES NOW: " + (", ".join(f"{p} ({n} B)" for p, n in files) or "(none)"))
    try:
        data, meta = await asyncio.wait_for(
            _ask(PLAN_SYSTEM, user, max_tokens=PLAN_MAX_OUT, client_id="daedalus/mentor-plan"), _timeout() + 5)
    except Exception as exc:
        data, meta = None, {"error": type(exc).__name__}
    if not data:
        log.info("mentor lead plan failed: %s", meta.get("error") or "bad_json")
        _event("mentor_plan", {"skipped": meta.get("error") or "bad_json", "model": meta.get("model")})
        return None
    plan = {
        "brief": str(data.get("brief") or "").strip()[:500],
        "checklist": _strs(data.get("checklist"), 12),
        "look": _strs(data.get("look"), 4),
        "steps": _strs(data.get("steps"), 6, 300),
        "model": meta.get("model"), "provider": meta.get("provider"), "ms": meta.get("ms"),
    }
    if not plan["checklist"]:
        _event("mentor_plan", {"skipped": "empty_checklist", "model": meta.get("model")})
        return None
    log.info("mentor lead plan ok model=%s items=%d ms=%s", plan["model"], len(plan["checklist"]), plan["ms"])
    _event("mentor_plan", {k: plan[k] for k in ("brief", "checklist", "look", "steps", "model")})
    return plan


def plan_block(plan: dict[str, Any] | None) -> str:
    if not plan:
        return ""
    lines = ["━━━ LEAD PLAN (Mentor — lead programmer; you build it) ━━━"]
    if plan.get("brief"):
        lines.append(plan["brief"])
    if plan.get("checklist"):
        lines.append("The owner asked for ALL of this; the lead will check each item in the running game:")
        lines.extend(f"  [ ] {c}" for c in plan["checklist"])
    if plan.get("look"):
        lines.append("Look: " + "; ".join(plan["look"]))
    if plan.get("steps"):
        lines.append("Steps:")
        lines.extend(f"  {i}. {s}" for i, s in enumerate(plan["steps"], 1))
    lines.append("Build every checklist item before you stop. Write complete files, not stubs.")
    lines.append("Each reply has an output limit: keep every file under ~400 lines and split the code across files "
                 "(for example index.html + game.js + entities.js + effects.js), one write_file per file.")
    return "\n".join(lines)


# ── review ──────────────────────────────────────────────────────────────────

PLAY_SCRIPT = (("Enter", 0), ("click", 0), ("Space", 0), ("ArrowRight", 900), ("Space", 0), ("ArrowRight", 700),
               ("ArrowLeft", 500))


async def play_frames(slug: str, kind: str = "static") -> tuple[list[dict[str, Any]], list[str]]:
    """Frames the lead can judge a game by: the start screen, then a few seconds of play after pressing start keys.

    The kernel's proof frame is the first paint (often a title screen); found live, the lead could not see a single
    platform. Web projects with an index.html only; anything else returns no frames (the stored proof frames are used).
    """
    root = _project_root(slug)
    if root is None:
        return [], []
    serve_dir = root / "dist" if (root / "dist" / "index.html").is_file() else root
    if not (serve_dir / "index.html").is_file() or kind in {"godot", "unity", "expo"}:
        return [], []
    try:
        from playwright.async_api import async_playwright

        from .harness import gateway as gw
        from .playground_browser import CHROME_ARGS, _serve_static, chromium_executable
    except Exception:
        return [], []
    base, stop = _serve_static(serve_dir)
    shots: list[bytes] = []
    try:
        async with async_playwright() as p:
            exe = chromium_executable()
            browser = await p.chromium.launch(args=list(CHROME_ARGS), **({"executable_path": exe} if exe else {}))
            try:
                page = await browser.new_page(viewport={"width": 960, "height": 640})
                await page.goto(base + "/", wait_until="load", timeout=20_000)
                await page.wait_for_timeout(1200)
                shots.append(await page.screenshot(type="png"))
                for key, hold in PLAY_SCRIPT:
                    if key == "click":
                        await page.mouse.click(480, 320)
                    elif hold:
                        await page.keyboard.down(key)
                        await page.wait_for_timeout(hold)
                        await page.keyboard.up(key)
                    else:
                        await page.keyboard.press(key)
                    await page.wait_for_timeout(150)
                await page.wait_for_timeout(600)
                shots.append(await page.screenshot(type="png"))
                await page.wait_for_timeout(1800)
                shots.append(await page.screenshot(type="png"))
            finally:
                await browser.close()
    except Exception:
        log.info("lead play frames failed for %s", slug, exc_info=True)
        return [], []
    finally:
        stop()
    parts = [gw.image_part(media_type="image/png", data=b) for b in shots if b and len(b) > 64]
    return parts, [str(part.get("sha256") or "") for part in parts]


def _frames(slug: str) -> tuple[list[dict[str, Any]], list[str]]:
    try:
        from .harness import perception
        fs = perception.latest_frames(slug)
        if not fs:
            return [], []
        parts = fs.parts()
        return parts, [r.sha256 for r in fs.refs][: len(parts)]
    except Exception:
        log.debug("lead frames unavailable", exc_info=True)
        return [], []


async def review(loop: Any, request: str) -> dict[str, Any] | None:
    """The lead's acceptance check of what the builder made. None when it could not run."""
    slug = str(getattr(loop, "slug", "") or "")
    plan = getattr(loop, "lead_plan", None) or {}
    checklist = list(plan.get("checklist") or [])
    request = (request or "").strip()[-REQUEST_MAX_CHARS:]
    if not slug or not (checklist or request):
        return None
    why = unavailable_reason()
    if why:
        log.info("mentor lead review skipped: %s", why)
        _event("mentor_review", {"skipped": why})
        return None
    images, shas = await play_frames(slug, str(getattr(loop, "kind", "") or "static"))
    if not images:
        images, shas = await asyncio.to_thread(_frames, slug)
    source = await asyncio.to_thread(main_source, slug)
    user = (f"OWNER REQUEST:\n{request}\n\nCHECKLIST:\n" + ("\n".join(f"- {c}" for c in checklist) or "(derive it from "
            "the request)") + f"\n\nPROJECT {slug}. Kernel checks: {getattr(loop, 'proof_summary', '') or 'n/a'}; "
            f"play bot: {getattr(loop, 'play_bot_summary', '') or 'n/a'}.\n"
            f"FRAMES ATTACHED: {len(images)}\n\nMAIN SOURCE:\n{source or '(none)'}")
    try:
        data, meta = await asyncio.wait_for(
            _ask(REVIEW_SYSTEM, user, max_tokens=REVIEW_MAX_OUT, images=images or None,
                 client_id="daedalus/mentor-review"), _timeout() + 5)
    except Exception as exc:
        data, meta = None, {"error": type(exc).__name__}
    if not data:
        log.info("mentor lead review failed: %s", meta.get("error") or "bad_json")
        _event("mentor_review", {"skipped": meta.get("error") or "bad_json", "model": meta.get("model")})
        return None
    missing = _strs(data.get("missing"), 8)
    out = {
        # Accepted exactly when no real defect is named: a bare "no" with nothing to fix only burns a writer round
        # (found live), and a "yes" that still lists missing items is not a yes.
        "accept": not missing,
        "met": _strs(data.get("met"), 12),
        "missing": missing,
        "unverified": _strs(data.get("unverified"), 6),
        "fixes": _strs(data.get("fixes"), 6, 300),
        "observations": _strs(data.get("observations"), 3, 160) if images else [],
        "saw_frames": len(images), "frame_sha256s": shas,
        "model": meta.get("model"), "ms": meta.get("ms"),
    }
    log.info("mentor lead review model=%s accept=%s missing=%d frames=%d ms=%s", out["model"], out["accept"],
             len(missing), len(images), out["ms"])
    _event("mentor_review", {k: out[k] for k in ("accept", "met", "missing", "unverified", "fixes", "observations",
                                                 "saw_frames",
                                                 "frame_sha256s", "model")})
    return out


def review_block(rev: dict[str, Any], *, rounds_left: int) -> str:
    lines = ["━━━ LEAD REVIEW (Mentor — not accepted yet) ━━━",
             f"The lead checked the running game ({rev.get('saw_frames', 0)} frame(s)) and the source against the "
             f"owner's request. Fix rounds left: {rounds_left}."]
    if rev.get("observations"):
        lines.append("On screen: " + "; ".join(rev["observations"]))
    if rev.get("missing"):
        lines.append("Missing:")
        lines.extend(f"  [ ] {m}" for m in rev["missing"])
    if rev.get("unverified"):
        lines.append("The lead could not check (make them visible and working, but do not chase them): "
                     + "; ".join(rev["unverified"]))
    if rev.get("fixes"):
        lines.append("Do this now:")
        lines.extend(f"  {i}. {f}" for i, f in enumerate(rev["fixes"], 1))
    lines.append("Call write_file / edit_file now. Do not explain; build the missing items.")
    return "\n".join(lines)


async def acceptance_nudge(loop: Any, request: str) -> str | None:
    """After the kernel said "complete": the lead accepts (None) or returns the turn (nudge text)."""
    if loop is None:
        return None
    done = int(getattr(loop, "lead_reviews", 0) or 0)
    used = int(getattr(loop, "lead_fix_used", 0) or 0)
    if done >= MAX_REVIEWS:
        return None
    loop.lead_reviews = done + 1
    rev = await review(loop, request)
    loop.lead_review = rev
    if rev is None or rev["accept"]:
        if rev is not None:
            loop.lead_accepted = True
        return None
    if used >= LEAD_FIX_ROUNDS:
        loop.lead_accepted = False
        return None
    loop.lead_fix_used = used + 1
    loop.stop_reason = ""
    return review_block(rev, rounds_left=LEAD_FIX_ROUNDS - loop.lead_fix_used)


def summary_lines(loop: Any) -> list[str]:
    """For the persona's final reply: what the lead decided, honestly."""
    if loop is None:
        return []
    rev = getattr(loop, "lead_review", None)
    plan = getattr(loop, "lead_plan", None)
    lines: list[str] = []
    if plan:
        lines.append(f"Lead plan ({plan.get('model')}): {len(plan.get('checklist') or [])} checklist items.")
    if isinstance(rev, dict):
        if rev.get("accept"):
            lines.append(f"Lead review: ACCEPTED after looking at {rev.get('saw_frames', 0)} frame(s).")
        else:
            lines.append("Lead review: NOT accepted. Still missing: " + "; ".join(rev.get("missing") or []) +
                         ". Tell the owner plainly.")
    return lines
