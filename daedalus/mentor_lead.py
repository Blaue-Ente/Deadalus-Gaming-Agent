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
import re
import logging
import time
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Iterator

log = logging.getLogger("daedalus.mentor_lead")

LEAD_FIX_ROUNDS = 2          # extra writer rounds the lead may ask for after the kernel said "complete"
EPIC_FIX_ROUNDS = 4          # epic promises depth: the lead, the audience and the tests share more rounds
MAX_REVIEWS = LEAD_FIX_ROUNDS + 1   # craft's lead reviews per turn; in general fix_rounds() + 1 (epic: 5)


def fix_rounds() -> int:
    """Fix rounds for this turn (live 2026-11-12: lead and audience used both craft rounds, the tests never ran)."""
    try:
        from .task_effort import current
        if str((current() or {}).get("tier") or "") == "epic":
            return EPIC_FIX_ROUNDS
    except Exception:
        pass
    return LEAD_FIX_ROUNDS
PLAN_MAX_OUT = 900
REVIEW_MAX_OUT = 1400   # 700 cut a 10-item review mid-JSON (live 2026-11-12)
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


def take_request() -> str:
    """The owner's words, once: the first agent turn reads them and clears them, so a nested turn started inside it
    (a subagent quoting the request, the planner, a self-review) is routed and planned on its own text."""
    value = _REQUEST.get()
    if value:
        _REQUEST.set("")
    return value


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
               client_id: str, _retry: bool = False) -> tuple[dict[str, Any] | None, dict[str, Any]]:
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
    meta["tokens"] = meta.get("tokens", 0) + tokens
    data = _parse_json(res.text or "")
    if data is None:
        out_tokens = int((res.usage or {}).get("completion_tokens") or (res.usage or {}).get("output_tokens") or 0)
        # The gateway already retried an empty reply with more room; its usage is the sum of both calls.
        cut = not getattr(res, "retried_max_tokens", 0) and out_tokens >= int(max_tokens * 0.95)
        meta["error"] = "cut_at_limit" if cut else "bad_json"
        log.info("mentor lead %s: %s (out=%s of %s) %r", client_id, meta["error"], out_tokens, max_tokens,
                 (res.text or "")[:200])
        if cut and not _retry:
            return await _ask(system, user, max_tokens=max_tokens * 2, images=images, client_id=client_id,
                              _retry=True)
    return data, meta


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
    import os

    out: list[tuple[str, int]] = []
    for base, dirs, files in os.walk(root):          # prune node_modules & co. before descending (not rglob + sort)
        dirs[:] = sorted(d for d in dirs if d not in _SKIP_DIRS and not d.startswith("."))
        for name in sorted(files):
            if not (name.lower().endswith(_SOURCE_EXT) or name == "README.md"):
                continue
            path = Path(base) / name
            try:
                out.append((path.relative_to(root).as_posix(), path.stat().st_size))
            except OSError:
                continue
            if len(out) >= limit:
                return out
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
    """The lead's plan for this build turn, or None (reason in the log and Run ledger). Never raises: the build must
    run whether or not the lead could plan it."""
    try:
        return await _kickoff(request, slug=slug, kind=kind)
    except Exception:
        log.warning("mentor lead plan crashed", exc_info=True)
        _event("mentor_plan", {"skipped": "error"})
        return None


async def _kickoff(request: str, *, slug: str = "", kind: str = "") -> dict[str, Any] | None:
    request = (request or "").strip()[-REQUEST_MAX_CHARS:]
    if not request:
        return None
    why = unavailable_reason()
    if why:
        log.info("mentor lead plan skipped: %s", why)
        _event("mentor_plan", {"skipped": why})
        return None
    files = await asyncio.to_thread(project_files, slug)
    user = (f"OWNER REQUEST:\n{request}\n\nPROJECT: {slug or '(new)'} · kind {kind or 'unknown'}\n"
            "FILES NOW: " + (", ".join(f"{p} ({n} B)" for p, n in files) or "(none)"))
    from .project_bible import text as bible_text
    bible = await asyncio.to_thread(bible_text, slug) if slug else ""
    if bible:   # earlier sessions: plan this turn as the next step of that game, not a new one (2026-11-12)
        user += ("\n\nPROJECT BIBLE (kept across sessions; continue from it, do not contradict it unless the owner "
                 "asks; its session log says what is still open):\n" + bible)
    try:
        data, meta = await asyncio.wait_for(
            _ask(PLAN_SYSTEM, user, max_tokens=PLAN_MAX_OUT, client_id="daedalus/mentor-plan"),
            _timeout() * 2 + 10)                       # room for one retry when a reply was cut
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

# "shot" takes a frame mid-play. Up hops matter too: found live, a crossing game's frog never moved under a
# platformer-only script, so the lead judged scoring and lives from the code alone.
# Clicks across the play area too ("click@x,y"): a click game (pop the bubbles) only ever saw one click in the
# centre, and its players said nothing reacted (live 2026-11-12).
PLAY_SCRIPT = (("Enter", 0), ("click", 0), ("Space", 0), ("ArrowUp", 0), ("ArrowUp", 0), ("ArrowUp", 0),
               ("click@300,260", 0), ("click@660,260", 0), ("click@480,420", 0),
               ("shot", 0), ("ArrowRight", 900), ("Space", 0), ("ArrowUp", 0), ("ArrowUp", 0), ("ArrowRight", 700),
               ("click@240,360", 0), ("click@720,360", 0), ("click@480,180", 0), ("ArrowLeft", 500))


_INTERACTIVE_AT = """([x, y]) => {
  const e = document.elementFromPoint(x, y);
  return !!(e && e.closest('a[href], button, input, select, textarea, label, [role=button], [role=link]'));
}"""


def playable_dir(slug: str, kind: str = "static") -> Path | None:
    """The folder that is the game as it is now (served as-is in a browser), or None."""
    root = _project_root(slug)
    if root is None:
        return None
    if kind in {"vite", "vite-react", "phaser"}:
        # Only a build of the current source is the game: raw /src is not runnable, an old dist is an old game.
        dist = root / "dist" / "index.html"
        try:
            from .harness.perception import _project_edit_mtime
            fresh = dist.is_file() and dist.stat().st_mtime >= _project_edit_mtime(slug)
        except Exception:
            fresh = False
        if not fresh:
            return None
        serve_dir = root / "dist"
    elif kind in {"static", "", "html"}:
        serve_dir = root
    else:
        return None
    return serve_dir if (serve_dir / "index.html").is_file() else None


@contextlib.asynccontextmanager
async def game_page(serve_dir: Path, *, width: int = 960, height: int = 640) -> Any:
    """A headless Chromium page on the game, served from a throwaway local server, closed on exit. Raises when no
    browser can start (callers catch). Shared by the lead's play (play_frames) and the goal session (goal_play)."""
    from playwright.async_api import async_playwright

    from .playground_browser import CHROME_ARGS, _serve_static, chromium_executable

    base, stop = _serve_static(serve_dir)
    try:
        async with async_playwright() as p:
            exe = chromium_executable()
            browser = await p.chromium.launch(args=list(CHROME_ARGS), **({"executable_path": exe} if exe else {}))
            try:
                page = await browser.new_page(viewport={"width": width, "height": height})
                await page.goto(base + "/", wait_until="load", timeout=20_000)
                yield page
            finally:
                await browser.close()
    finally:
        stop()


async def play_frames(slug: str, kind: str = "static") -> tuple[list[dict[str, Any]], list[str]]:
    """Frames the lead can judge a game by: the start screen, then a few seconds of play after pressing start keys.

    The kernel's proof frame is the first paint (often a title screen); found live, the lead could not see a single
    platform. Web projects with an index.html only; anything else returns no frames (the stored proof frames are used).
    """
    serve_dir = playable_dir(slug, kind)
    if serve_dir is None:
        return [], []
    from .harness import gateway as gw

    shots: list[bytes] = []
    try:
        async with game_page(serve_dir) as page:
            await page.wait_for_timeout(1200)
            shots.append(await page.screenshot(type="png"))
            for key, hold in PLAY_SCRIPT:
                if key == "shot":
                    shots.append(await page.screenshot(type="png"))
                    continue
                if key == "click" or key.startswith("click@"):
                    x, y = (480, 320) if key == "click" else map(int, key[6:].split(","))
                    # The sweep never presses a link, button or field (a help link or a pause button would
                    # change what the frames show: review 2026-11-12). The centre click may press Start.
                    if key != "click" and await page.evaluate(_INTERACTIVE_AT, [x, y]):
                        continue
                    await page.mouse.click(x, y)
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
    except Exception:
        log.info("lead play frames failed for %s", slug, exc_info=True)
        return [], []
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
    loop.lead_play_frames = (images, shas)                     # the audience test reuses this play of this build
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
                 client_id="daedalus/mentor-review"), _timeout() * 2 + 10)   # room for one retry when a reply was cut
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
    if done >= fix_rounds() + 1:
        return None
    loop.lead_reviews = done + 1
    rev = await review(loop, request)
    loop.lead_review = rev
    if rev is None or rev["accept"]:
        if rev is not None:
            loop.lead_accepted = True
        return None
    if used >= fix_rounds():
        loop.lead_accepted = False
        return None
    loop.lead_fix_used = used + 1
    # stop_reason stays "complete": that is the kernel's verdict and it stands; the lead's own verdict is reported
    # separately, so a writer still fixing when the rounds run out is not turned into "round budget exhausted".
    return review_block(rev, rounds_left=fix_rounds() - loop.lead_fix_used)


GAME_PLAYBOOKS = {"playground.arcade", "playground.math3d", "playground.crowd", "playground.epic"}
# Narrow on purpose (review 2026-11-12): "player", "level" or "play" also name music players and dashboards.
_GAME_WORDS = re.compile(r"\b(games?|arcade)\b|игр[аиуё]|spiel|\bjeu|juego", re.I)


def is_game(loop: Any, request: str) -> bool:
    """A game: a game playbook, the owner asked for one, or the project draws on a canvas in a loop and takes input."""
    route = getattr(loop, "route", None)
    pb = str(getattr(getattr(route, "playbook", None), "id", "") or "")
    return is_game_build(pb, request, str(getattr(loop, "slug", "") or ""))


def is_game_build(playbook_id: str, request: str, slug: str = "") -> bool:
    """The one game test, for the build prompt and the gates alike (review 2026-11-12: two copies disagreed)."""
    if playbook_id in GAME_PLAYBOOKS or _GAME_WORDS.search(request or ""):
        return True
    if not slug:
        return False
    try:
        from .playtest import _heuristic, _project_snapshot
        sig = _heuristic(_project_snapshot(slug))["signals"]
        return bool(sig.get("canvas") and sig.get("input") and sig.get("loop"))
    except Exception:
        return False


async def audience_gate(loop: Any, request: str) -> str | None:
    """After the lead accepts a game: simulated players play it once. If half or more would quit and a fix round is
    left, the writer gets their words and fixes (None = done). Owner, 2026-11-12: a game Daedalus makes must be really
    playable and good, like The-Labirinth; the lead alone accepted games whose players could not tell why they died.
    Shares the lead's fix rounds, so a build never loops."""
    if loop is None or not getattr(loop, "lead_accepted", False) or getattr(loop, "audience_passed", False):
        return None
    slug = str(getattr(loop, "slug", "") or "")
    kind = str(getattr(loop, "kind", "") or "static")
    if not slug or kind not in {"static", "", "html", "vite", "vite-react", "phaser"} or not is_game(loop, request):
        return None
    from .task_effort import current

    tier = str((current() or {}).get("tier") or "")
    if tier == "whisper":
        return None
    from .playtest import gateway_see, run_playtest

    session = None
    if tier == "epic":       # gap 5: a longer, goal-directed session (one model call a turn) instead of the script
        from .goal_play import goal_play
        session = await goal_play(slug, kind, see=gateway_see)
        if not session.get("frames"):
            session = None
    frames = getattr(loop, "lead_play_frames", None)          # what the lead just played, on this exact build
    if frames is None and session is None:
        frames = await play_frames(slug, kind)
    if session is None and not frames[0]:
        return None                                           # no play frames: no audience, and no calls spent
    try:
        report = await run_playtest(slug, kind=kind, n=4, see_fn=gateway_see, frames=frames, session=session)
    except Exception:
        log.warning("audience test for %s failed", slug, exc_info=True)
        return None
    keep = not report.get("many_quit")       # the players' verdict, not the source heuristics (review 2026-11-12)
    loop.audience = {**{k: report.get(k) for k in ("mode", "summary", "quotes", "fixes", "saw_frames", "goals")},
                     "ship_ready": keep}
    if report.get("mode") != "played" or keep:
        loop.audience_passed = report.get("mode") == "played"
        return None
    used = int(getattr(loop, "lead_fix_used", 0) or 0)
    if used >= fix_rounds():
        return None
    loop.lead_fix_used = used + 1
    loop.lead_accepted = False                # accepted again only after the fix is reviewed
    quotes = "\n".join(f"- {q}" for q in (report.get("quotes") or [])[:4])
    fixes = "\n".join(f"- {f}" for f in (report.get("fixes") or [])[:4])
    goals = report.get("goals") or {}
    if goals:            # a goal session: its frames are not the ones the writer saw, so say what was seen
        played = (f"A player just played the game for {goals.get('turns')} turns toward goals (start, score, lose, "
                  "restart); you did not see its frames. Simulated players judged them. Most would quit.\n")
        if goals.get("missed"):
            played += "Goals never reached: " + "; ".join(goals["missed"]) + "\n"
        if goals.get("notes"):
            played += "What the player saw last:\n" + "\n".join(f"- {n}" for n in goals["notes"]) + "\n"
        if goals.get("errors"):
            played += "JavaScript errors while playing: " + "; ".join(goals["errors"]) + "\n"
    else:
        played = "Simulated players just played the game (the frames you saw are what they saw). Most would quit.\n"
    return ("━━━ AUDIENCE TEST: NOT YET ━━━\n"
            f"{report.get('summary')}\n"
            f"{played}"
            f"What they said:\n{quotes or '- (no quotes)'}\n"
            f"What would keep them:\n{fixes or '- (no fixes)'}\n"
            "Fix the causes they name in the game's files (write_file), keep everything that already works, then "
            f"stop. Fix rounds left after this one: {fix_rounds() - loop.lead_fix_used}.")


async def tests_gate(loop: Any, request: str) -> str | None:
    """After the lead (and the audience) accepted a game: its own tests. Logic tests that fail or a regression against
    the recorded baseline return the turn (one shared fix round); an accepted game the players did not reject, with
    passing tests, records its baseline; a game with no tests is asked for them once, and the tests it then writes
    run. With no round left the tests still run, for the report. None = done. Owner, 2026-11-12: games a team keeps
    building on, like The-Labirinth with its `pnpm verify` before every push."""
    if loop is None or getattr(loop, "tests_done", False):
        return None
    slug = str(getattr(loop, "slug", "") or "")
    if not slug or not is_game(loop, request):
        return None
    try:
        from .task_effort import current
        if str((current() or {}).get("tier") or "") == "whisper":
            return None
    except Exception:
        pass
    from . import project_tests

    if await asyncio.to_thread(project_tests.upstream_repo, slug):
        return None            # a cloned or revived repo keeps its own tests and structure (review 2026-11-12)
    play = getattr(loop, "play_bot_raw", None)
    used = int(getattr(loop, "lead_fix_used", 0) or 0)
    rounds_left = used < fix_rounds()
    accepted = bool(getattr(loop, "lead_accepted", False))
    if not accepted and rounds_left:
        return None                              # the lead's own review comes first
    report = await project_tests.run(slug, play=play)
    loop.project_tests = {k: report.get(k) for k in ("ok", "summary", "regressions", "baseline")}
    logic = report.get("logic") or {}
    passed = bool(report["ok"] and logic.get("ran"))
    if not accepted or (not rounds_left and not passed):
        loop.tests_done = True                   # report only: not accepted with no round left, or failing with none
        return None
    if passed:
        loop.tests_done = True
        if not report["baseline"] and _players_kept(loop):
            project_tests.record_baseline(slug, logic, play)
        return None
    if not logic.get("ran") and not report["regressions"]:
        if logic.get("ok") is False or getattr(loop, "tests_asked", False):
            loop.tests_done = True               # no Node here, or already asked once: report, do not loop
            return None
        loop.tests_asked = True                  # asked once; the tests it then writes still run (not tests_done)
        loop.lead_fix_used = used + 1
        loop.lead_accepted = False
        return ("━━━ PROJECT TESTS: NONE YET ━━━\n" + project_tests.GUIDANCE +
                "\nKeep the game working exactly as it is; only move the rules into logic.mjs and test them. Then stop.")
    loop.lead_fix_used = used + 1
    loop.lead_accepted = False                  # reviewed again after the fix
    lines = ["━━━ PROJECT TESTS: FAILING ━━━", report["summary"]]
    lines += [f"- failing: {f}" for f in (logic.get("failures") or [])[:6]]
    lines += [f"- regression: {r}" for r in report["regressions"][:6]]
    if logic.get("note"):
        lines.append(f"- {logic['note']}")
    lines.append("Fix the game so the tests pass (change a test only if the test itself is wrong, and say why). "
                 "Keep everything that already works. Then stop.")
    return "\n".join(lines)


def _juice_target(loop: Any, request: str) -> tuple[Path, str] | None:
    """(project root, real kind) of a web game this turn builds that should have sound; None for an app, whisper,
    a cloned repo, or an Expo/Godot/Unity game (sfx.js is browser code: review 2026-11-12)."""
    slug = str(getattr(loop, "slug", "") or "")
    if not slug or not is_game(loop, request):
        return None
    try:
        from .task_effort import current
        if str((current() or {}).get("tier") or "") == "whisper":
            return None
    except Exception:
        pass
    from .game_juice import WEB_KINDS, project_kind
    from .project_tests import upstream_repo

    root = _project_root(slug)
    if root is None or upstream_repo(slug):
        return None
    kind = project_kind(root, str(getattr(loop, "kind", "") or "static"))
    return (root, kind) if kind in WEB_KINDS else None


def place_sound(loop: Any) -> bool:
    """Gap 4a: put sfx.js into a silent web game (never over the game's copy, never into a game with its own
    sound). True if it wrote. Never raises; blocking: call off the event loop."""
    try:
        target = _juice_target(loop, str(getattr(loop, "user_message", "") or ""))
        if target is None:
            return False
        from .game_juice import ensure_sfx, has_sound
        root, kind = target
        return False if has_sound(root) else ensure_sfx(root, kind)
    except Exception:
        log.debug("sfx.js not placed", exc_info=True)
        return False


async def juice_gate(loop: Any, request: str) -> str | None:
    """Right after the lead accepts: a web game that plays no sound at all is asked once to add it (sfx.js is in the
    project). It runs before the audience and the tests, so they judge and test the game with its sound. Shares the
    lead's fix rounds; with none left, or after asking once, it only reports. None = done. Gap 4a."""
    if loop is None or getattr(loop, "juice_done", False):
        return None
    target = await asyncio.to_thread(_juice_target, loop, request)
    if target is None:
        return None
    from .game_juice import has_sound, sfx_path

    root, kind = target
    sound = await asyncio.to_thread(has_sound, root)
    loop.juice = {"sound": sound}                # kept current even when the lead has not accepted (yet)
    if not getattr(loop, "lead_accepted", False):
        return None
    used = int(getattr(loop, "lead_fix_used", 0) or 0)
    if sound or getattr(loop, "juice_asked", False) or used >= fix_rounds():
        loop.juice_done = True
        return None
    await asyncio.to_thread(place_sound, loop)
    loop.juice_asked = True
    loop.lead_fix_used = used + 1
    loop.lead_accepted = False                  # reviewed again after the change, like every gate
    return ("━━━ SOUND: NONE YET ━━━\n"
            f"The game plays no sound. {sfx_path(kind)} is in the project: "
            "import { sfx } from it and call sfx.jump(), sfx.score(), sfx.hit(), sfx.lose(), sfx.win() and "
            "sfx.click() on the game's events, and say in the help text that M mutes. Keep everything else exactly "
            "as it is. Then stop.")


def _players_kept(loop: Any) -> bool:
    """A baseline is what an accepted game passes: not one the audience played and rejected (review 2026-11-12)."""
    aud = getattr(loop, "audience", None)
    return not (isinstance(aud, dict) and aud.get("mode") == "played" and not aud.get("ship_ready"))


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
            lines.append(f"Lead review: ACCEPTED after looking at {rev.get('saw_frames', 0)} frame(s). "
                         "Say so plainly in the reply.")
        else:
            lines.append("Lead review: NOT accepted. Still missing: " + "; ".join(rev.get("missing") or []) +
                         ". Tell the owner plainly.")
    pt = getattr(loop, "project_tests", None)
    if isinstance(pt, dict) and pt.get("summary"):
        lines.append(f"Project tests: {pt['summary']}" + (" (failing)" if not pt.get("ok") else "") +
                     ". Tell the owner plainly.")
    aud = getattr(loop, "audience", None)
    if isinstance(aud, dict) and aud.get("summary"):
        verdict = "players would keep playing" if aud.get("ship_ready") else "most players would still quit"
        seen = (f" The players' {aud.get('saw_frames')} frames were taken WHILE PLAYING, after pressing start (the "
                "image attached to this reply is only the first paint, not what they saw)."
                if aud.get("mode") == "played" else "")
        lines.append(f"Audience test ({aud.get('mode')}): {aud.get('summary')} — {verdict}.{seen} "
                     "Tell the owner plainly.")
    return lines
