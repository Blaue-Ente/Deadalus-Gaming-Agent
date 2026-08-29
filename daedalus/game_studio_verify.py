"""Game Studio post-milestone verify — runtime smoke, browser proof, auto-fix (GS-D)."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from . import playground as pg
from . import playground_browser as pbrowser
from . import playground_runner as runner
from . import playground_verify_fix as pvf
from . import playground_gameplay_verify as pgplay
from .game_build_contract import skip_is_not_ok
from .game_studio_build import milestone_progress, resolve_current_milestone
from .playground_browser import VISUAL_KINDS
from .playground_runtime import verify_unity_scaffold, local_only_kind_message

log = logging.getLogger(__name__)

VERIFY_MAX_FIX_ATTEMPTS = 5


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _current_milestone_id(config: dict[str, Any] | None, plan: dict[str, Any] | None) -> str:
    cfg = config or {}
    current = (cfg.get("current_milestone") or "").strip()
    if not current:
        current = resolve_current_milestone(cfg, plan)
    return current


def can_advance_milestone(config: dict[str, Any] | None, plan: dict[str, Any] | None) -> bool:
    cfg = config or {}
    current = _current_milestone_id(cfg, plan)
    prog = milestone_progress(plan, current)
    if not prog["complete"]:
        return False
    verify = (cfg.get("verify") or {}).get(current) or {}
    if not verify.get("ok"):
        return False
    if verify.get("proof_incomplete"):
        return False
    return True


def can_advance_with_unity_waiver(config: dict[str, Any] | None, plan: dict[str, Any] | None) -> bool:
    """Allow advance when Unity scaffold/runtime passed but full cloud gameplay proof is impossible."""
    cfg = config or {}
    current = _current_milestone_id(cfg, plan)
    prog = milestone_progress(plan, current)
    if not prog["complete"]:
        return False
    verify = (cfg.get("verify") or {}).get(current) or {}
    if not verify:
        return False
    if verify.get("ok") and not verify.get("proof_incomplete"):
        return False
    if verify.get("chromium_missing"):
        return False
    if not verify.get("unity_local_required"):
        return False
    if not verify.get("runtime_ok"):
        return False
    if not verify.get("unity_scaffold_ok"):
        return False
    return True


def can_advance_with_proof_waiver(config: dict[str, Any] | None, plan: dict[str, Any] | None) -> bool:
    """Studio full-auto: runtime passed but headless browser proof unavailable on host."""
    cfg = config or {}
    current = _current_milestone_id(cfg, plan)
    prog = milestone_progress(plan, current)
    if not prog["complete"]:
        return False
    verify = (cfg.get("verify") or {}).get(current) or {}
    if not verify:
        return False
    if verify.get("ok") and not verify.get("proof_incomplete"):
        return False
    if not verify.get("runtime_ok"):
        return False
    return bool(verify.get("chromium_missing") or verify.get("proof_incomplete"))


def verify_summary_for_milestone(config: dict[str, Any] | None, milestone_id: str) -> dict[str, Any] | None:
    cfg = config or {}
    raw = (cfg.get("verify") or {}).get(milestone_id)
    return raw if isinstance(raw, dict) else None


async def _build_fix_context(slug: str, proj: dict[str, Any], description: str) -> tuple[str, str, str]:
    from .build_prompts import build_combined_sys, resolve_build_model_for_project
    from .game_platform_guide import is_likely_game

    kind = proj.get("kind", "static")
    coding_mode = proj.get("coding_mode", "lite")
    combined_sys = build_combined_sys(kind, coding_mode)
    is_game = is_likely_game(f"{proj.get('name', slug)} {description}")
    build_model, _ = resolve_build_model_for_project(
        description, is_game=is_game, coding_mode=coding_mode,
    )
    if not build_model:
        build_model = "anthropic/claude-sonnet-4"
    return combined_sys, build_model, kind


async def _runtime_verify_with_fix(
    slug: str,
    *,
    kind: str,
    combined_sys: str,
    build_model: str,
    route_fn: Callable[..., Awaitable[Any]],
    on_log: Callable[[str, str], Awaitable[None] | None] | None = None,
) -> tuple[bool, dict[str, Any], int]:
    last_smoke: dict[str, Any] = {}
    attempts = 0

    for attempt in range(VERIFY_MAX_FIX_ATTEMPTS):
        attempts = attempt + 1
        try:
            last_smoke = await runner.smoke_test(slug)
        except Exception as exc:
            last_smoke = {"ok": False, "error": str(exc), "logs": []}

        if last_smoke.get("ok"):
            if on_log:
                await _emit(on_log, "✅ Runtime smoke мина", "ok")
            return True, last_smoke, attempts

        # skip ≠ ok: missing Godot/Unity binary is not a compile error to LLM-repair.
        if last_smoke.get("skipped"):
            err = (
                last_smoke.get("error")
                or last_smoke.get("summary")
                or "runtime skipped — not evidence"
            )
            if on_log:
                await _emit(on_log, f"❌ Runtime skipped — not evidence: {str(err)[:160]}", "err")
            return False, last_smoke, attempts

        err = (
            last_smoke.get("error")
            or "\n".join((last_smoke.get("logs") or [])[-25:])
            or "runtime smoke failed"
        ).strip()
        first = next((ln for ln in err.splitlines() if ln.strip()), err[:140])
        if on_log:
            await _emit(on_log, f"❌ Runtime (опит {attempts}/{VERIFY_MAX_FIX_ATTEMPTS}): {first[:160]}", "warn")

        if attempt + 1 >= VERIFY_MAX_FIX_ATTEMPTS:
            break

        extra = ""
        if kind == "expo":
            from .expo_platform_guide import expo_fix_addendum
            extra = expo_fix_addendum()
        if on_log:
            await _emit(on_log, f"🔧 Поправка {attempts}/{VERIFY_MAX_FIX_ATTEMPTS}…", "info")
        fix_files = await pvf.llm_fix_from_error(
            slug,
            kind=kind,
            error=err[:2500],
            combined_sys=combined_sys,
            build_model=build_model,
            route_fn=route_fn,
            client_id=f"gs-verify-runtime/{slug}/a{attempts}",
            problem_label="GAME STUDIO VERIFY: приложението НЕ СТАРТИРА след milestone build.",
            extra_addendum=extra,
        )
        written = pvf.write_fix_files(slug, fix_files) if fix_files else []
        if on_log:
            if written:
                await _emit(on_log, f"  📝 Поправени: {', '.join(written[:6])}", "dim")
            else:
                await _emit(on_log, "  ⚠️ Поправката не върна файлове", "warn")

    return False, last_smoke, attempts


async def _browser_verify_with_fix(
    slug: str,
    *,
    kind: str,
    combined_sys: str,
    build_model: str,
    route_fn: Callable[..., Awaitable[Any]],
    on_log: Callable[[str, str], Awaitable[None] | None] | None = None,
) -> tuple[bool, dict[str, Any], int]:
    if kind not in VISUAL_KINDS:
        return True, {
            "ok": True,
            "skipped": False,
            "na": True,
            "summary": f"browser N/A ({kind})",
        }, 0

    last_bv: dict[str, Any] = {}
    attempts = 0

    for attempt in range(VERIFY_MAX_FIX_ATTEMPTS):
        attempts = attempt + 1
        try:
            last_bv = await pbrowser.verify_app(slug, kind)
        except Exception as exc:
            last_bv = {
                "ok": False,
                "skipped": False,
                "proof_incomplete": True,
                "summary": str(exc)[:200],
                "console_errors": [],
                "page_errors": [],
            }

        if last_bv.get("skipped"):
            err_code = (last_bv.get("error") or "").strip()
            if err_code in ("chromium_not_found", "playwright_unavailable"):
                if on_log:
                    await _emit(
                        on_log,
                        "❌ Browser verify НЕВЪЗМОЖЕН — липсва Chromium/Playwright. "
                        "Инсталирай Chromium за пълен visual proof.",
                        "err",
                    )
                return False, {
                    **last_bv,
                    "proof_incomplete": True,
                    "ok": False,
                }, attempts
            if on_log:
                await _emit(
                    on_log,
                    f"❌ Browser verify неочакван skip: {last_bv.get('summary', '')[:120]}",
                    "err",
                )
            return False, {
                **last_bv,
                "proof_incomplete": True,
                "ok": False,
            }, attempts

        if last_bv.get("ok"):
            if on_log:
                await _emit(on_log, f"✅ Browser: {last_bv.get('summary', 'OK')[:120]}", "ok")
            return True, last_bv, attempts

        summary = last_bv.get("summary") or "browser verify failed"
        if on_log:
            await _emit(on_log, f"❌ Browser (опит {attempts}/{VERIFY_MAX_FIX_ATTEMPTS}): {summary[:160]}", "warn")

        if attempt + 1 >= VERIFY_MAX_FIX_ATTEMPTS:
            break

        err_block = pbrowser.format_errors_for_fix(last_bv)
        if on_log:
            await _emit(on_log, f"🔧 Визуална поправка {attempts}/{VERIFY_MAX_FIX_ATTEMPTS}…", "info")
        fix_files = await pvf.llm_fix_from_error(
            slug,
            kind=kind,
            error=err_block[:2500],
            combined_sys=combined_sys,
            build_model=build_model,
            route_fn=route_fn,
            client_id=f"gs-verify-browser/{slug}/a{attempts}",
            problem_label=(
                "GAME STUDIO VERIFY: compile мина, но в РЕАЛЕН БРАУЗЪР има проблем "
                "(бял екран / console errors):"
            ),
        )
        written = pvf.write_fix_files(slug, fix_files) if fix_files else []
        if written and kind in ("vite", "vite-react"):
            await pvf.ensure_vite_build_after_edits(
                slug,
                kind=kind,
                combined_sys=combined_sys,
                build_model=build_model,
                route_fn=route_fn,
                max_attempts=VERIFY_MAX_FIX_ATTEMPTS,
                client_prefix=f"gs-verify-vite/{slug}",
            )
        if on_log and written:
            await _emit(on_log, f"  📝 Поправени: {', '.join(written[:6])}", "dim")

    return False, last_bv, attempts


async def _unity_browser_verify(
    slug: str,
    on_log: Callable[[str, str], Awaitable[None] | None] | None = None,
) -> tuple[bool, dict[str, Any]]:
    """Browser verify on studio/preview bridge when present."""
    root = pg.project_dir(slug)
    preview = root / "studio" / "preview"
    if not (preview.is_dir() and (preview / "index.html").is_file()):
        uv = verify_unity_scaffold(slug)
        msg = (
            "Unity: няма web preview (studio/preview). "
            "Scaffold "
            + ("OK — отвори в Unity Hub." if uv.get("ok") else f"FAIL: {uv.get('error', '')}")
        )
        if on_log:
            await _emit(on_log, f"📦 {msg}", "warn" if uv.get("ok") else "err")
        return False, {
            "ok": False,
            "skipped": True,
            "summary": msg,
            "unity_local_required": True,
            "unity_scaffold_ok": bool(uv.get("ok")),
            "proof_incomplete": True,
        }
    from .playground_browser import _serve_static, browser_verify
    base, stop = _serve_static(preview)
    try:
        if on_log:
            await _emit(on_log, "🖥️ Unity web preview bridge — browser verify…", "info")
        bv = await browser_verify(slug, base + "/", kind="static")
        return bool(bv.get("ok")), bv
    finally:
        stop()


async def _gameplay_verify_with_fix(
    slug: str,
    *,
    kind: str,
    milestone_id: str,
    combined_sys: str,
    build_model: str,
    route_fn: Callable[..., Awaitable[Any]],
    on_log: Callable[[str, str], Awaitable[None] | None] | None = None,
) -> tuple[bool, dict[str, Any], int]:
    last_gp: dict[str, Any] = {}
    attempts = 0

    for attempt in range(VERIFY_MAX_FIX_ATTEMPTS):
        attempts = attempt + 1
        try:
            last_gp = await pgplay.gameplay_verify_app(slug, kind, milestone_id=milestone_id)
        except Exception as exc:
            last_gp = {"ok": False, "summary": str(exc)[:200], "checks": []}

        if last_gp.get("skipped") and last_gp.get("unity_local_required"):
            if on_log:
                await _emit(on_log, f"📦 {last_gp.get('summary', '')[:160]}", "warn")
            return False, skip_is_not_ok(last_gp), attempts

        if last_gp.get("skipped"):
            if kind not in VISUAL_KINDS:
                if on_log:
                    await _emit(on_log, f"Gameplay N/A for {kind}", "dim")
                return True, last_gp, attempts
            if on_log:
                await _emit(
                    on_log,
                    f"❌ Gameplay skipped — not evidence: {last_gp.get('summary', 'skip')[:120]}",
                    "err",
                )
            return False, skip_is_not_ok(last_gp), attempts

        if last_gp.get("proof_incomplete"):
            if on_log:
                await _emit(on_log, f"❌ Gameplay: {last_gp.get('summary', '')[:160]}", "err")
            return False, last_gp, attempts

        if last_gp.get("ok"):
            if on_log:
                await _emit(on_log, "✅ Gameplay interaction checks passed", "ok")
            return True, last_gp, attempts

        failed = [c for c in (last_gp.get("checks") or []) if not c.get("ok")]
        summary = last_gp.get("summary") or "gameplay failed"
        if on_log:
            await _emit(
                on_log,
                f"❌ Gameplay (опит {attempts}/{VERIFY_MAX_FIX_ATTEMPTS}): {summary[:120]}",
                "warn",
            )

        if attempt + 1 >= VERIFY_MAX_FIX_ATTEMPTS:
            break

        err_block = pgplay.format_gameplay_for_fix(last_gp)
        if on_log:
            await _emit(on_log, f"🔧 Gameplay поправка {attempts}/{VERIFY_MAX_FIX_ATTEMPTS}…", "info")
        fix_files = await pvf.llm_fix_from_error(
            slug,
            kind=kind,
            error=err_block[:2500],
            combined_sys=combined_sys,
            build_model=build_model,
            route_fn=route_fn,
            client_id=f"gs-verify-gameplay/{slug}/a{attempts}",
            problem_label="GAME STUDIO GAMEPLAY: играта се зарежда, но не е интерактивна/играема.",
        )
        pvf.write_fix_files(slug, fix_files)
        if kind in ("vite", "vite-react"):
            await pvf.ensure_vite_build_after_edits(
                slug,
                kind=kind,
                combined_sys=combined_sys,
                build_model=build_model,
                route_fn=route_fn,
                max_attempts=VERIFY_MAX_FIX_ATTEMPTS,
                client_prefix=f"gs-verify-gp-vite/{slug}",
            )

    return False, last_gp, attempts


async def _emit(
    on_log: Callable[[str, str], Awaitable[None] | None] | None,
    line: str,
    level: str = "info",
) -> None:
    if not on_log:
        return
    try:
        res = on_log(line, level)
        if res is not None and hasattr(res, "__await__"):
            await res
    except Exception:
        pass


async def run_milestone_verify(
    slug: str,
    milestone_id: str | None = None,
    *,
    on_log: Callable[[str, str], Awaitable[None] | None] | None = None,
) -> dict[str, Any]:
    """Smoke + browser verify with up to VERIFY_MAX_FIX_ATTEMPTS repairs each."""
    from . import daedalus_mesh as mesh

    from .game_studio import load_config_from_root, save_config

    proj = await pg.get_project(slug)
    if not proj:
        raise ValueError("project not found")

    cfg = load_config_from_root(pg.project_dir(slug)) or {}
    plan: dict[str, Any] = {}
    try:
        loaded_plan = await pg.load_autopilot_plan(slug)
        if isinstance(loaded_plan, dict):
            plan = loaded_plan
    except Exception:
        pass

    mid = (milestone_id or cfg.get("current_milestone") or resolve_current_milestone(cfg, plan)).strip()
    if not mid:
        mid = "M0"

    prog = milestone_progress(plan, mid)
    if not prog["complete"]:
        pending = prog["pending"]
        total = prog["total"]
        if total == 0:
            raise ValueError(
                f"milestone {mid} has no assigned stages — reload workspace or regenerate Studio plan"
            )
        raise ValueError(
            f"milestone {mid} still has {pending} pending stage(s) — finish Guided build first"
        )
    done_count = sum(
        1 for s in (plan.get("stages") or [])
        if isinstance(s, dict) and s.get("milestone_id") == mid and s.get("status") == "done"
    )
    if done_count == 0:
        raise ValueError(
            f"milestone {mid} has no completed builds (only skipped?) — run Guided build before Verify"
        )

    description = proj.get("description", "") or f"Game Studio {slug}"
    combined_sys, build_model, kind = await _build_fix_context(slug, proj, description)

    if on_log:
        await _emit(on_log, f"🔍 Verify milestone {mid} (до {VERIFY_MAX_FIX_ATTEMPTS} поправки на фаза)", "info")

    if kind == "unity":
        if on_log:
            await _emit(on_log, f"📦 {local_only_kind_message('unity')}", "dim")

    runtime_ok, smoke, runtime_attempts = await _runtime_verify_with_fix(
        slug,
        kind=kind,
        combined_sys=combined_sys,
        build_model=build_model,
        route_fn=mesh.route,
        on_log=on_log,
    )

    browser_ok = False
    browser: dict[str, Any] = {}
    browser_attempts = 0
    proof_incomplete = False
    if runtime_ok and kind == "unity":
        browser_ok, browser = await _unity_browser_verify(slug, on_log=on_log)
        proof_incomplete = bool(browser.get("proof_incomplete"))
        browser_attempts = 1
    elif runtime_ok:
        browser_ok, browser, browser_attempts = await _browser_verify_with_fix(
            slug,
            kind=kind,
            combined_sys=combined_sys,
            build_model=build_model,
            route_fn=mesh.route,
            on_log=on_log,
        )
        proof_incomplete = bool(browser.get("proof_incomplete"))
    elif kind == "static":
        paths = [t["path"] for t in pg.read_tree(slug)]
        browser_ok = any(p == "index.html" or p.endswith("/index.html") for p in paths)
        browser = {"ok": browser_ok, "summary": "static entry check"}

    gameplay_ok = False
    gameplay: dict[str, Any] = {}
    gameplay_attempts = 0
    if runtime_ok and browser_ok and not proof_incomplete:
        gameplay_ok, gameplay, gameplay_attempts = await _gameplay_verify_with_fix(
            slug,
            kind=kind,
            milestone_id=mid,
            combined_sys=combined_sys,
            build_model=build_model,
            route_fn=mesh.route,
            on_log=on_log,
        )
        if gameplay.get("proof_incomplete"):
            proof_incomplete = True
    elif kind == "unity" and browser.get("unity_local_required"):
        gameplay_ok = False
        gameplay = skip_is_not_ok({
            "ok": False,
            "skipped": True,
            "unity_local_required": True,
            "summary": browser.get("summary") or "Unity local Editor required — scaffold is not a playtest",
        })
    else:
        gameplay_ok = False
        gameplay = skip_is_not_ok({
            "ok": False,
            "skipped": True,
            "summary": "gameplay skipped — not evidence",
        })

    from .playground_browser import VISUAL_KINDS
    from .quality_gate import critic_counts_as_pass, maybe_critic

    critic = await maybe_critic(
        slug=slug,
        kind=kind,
        description=description,
        plan=plan,
        bv=gameplay if gameplay.get("screenshot_path") else browser,
        visual=kind in VISUAL_KINDS,
    )
    quality_ok = bool(critic.get("na") or critic_counts_as_pass(critic))
    if on_log:
        if critic.get("na"):
            await _emit(on_log, "Visual quality: N/A for this project kind", "dim")
        else:
            level = "ok" if quality_ok else "err"
            await _emit(
                on_log,
                f"{'✅' if quality_ok else '❌'} Visual quality: "
                f"{critic.get('overall', 'unscored')}",
                level,
            )

    overall_ok = runtime_ok and browser_ok and gameplay_ok and quality_ok and not proof_incomplete
    screenshot = (
        gameplay.get("screenshot_path")
        or (browser.get("screenshot_path") if browser else None)
    )
    preview_url = f"/apps/{slug}/"

    unity_scaffold_ok = bool(browser.get("unity_scaffold_ok"))
    if kind == "unity" and not unity_scaffold_ok:
        for c in (gameplay.get("checks") or []):
            if c.get("id") == "unity_scaffold":
                unity_scaffold_ok = bool(c.get("ok"))
        if not unity_scaffold_ok:
            unity_scaffold_ok = bool(verify_unity_scaffold(slug).get("ok"))

    chromium_missing = any(
        part.get("error") in ("chromium_not_found", "playwright_unavailable")
        for part in (browser, gameplay)
        if isinstance(part, dict)
    )

    record = {
        "milestone_id": mid,
        "ok": overall_ok,
        "runtime_ok": runtime_ok,
        "browser_ok": browser_ok,
        "runtime_attempts": runtime_attempts,
        "browser_attempts": browser_attempts,
        "max_fix_attempts": VERIFY_MAX_FIX_ATTEMPTS,
        "screenshot_path": screenshot,
        "preview_url": preview_url,
        "browser_summary": (browser.get("summary") or "")[:500],
        "runtime_error": (smoke.get("error") or "")[:500] if not runtime_ok else "",
        "browser_skipped": bool(browser.get("skipped")),
        "has_canvas": bool(browser.get("has_canvas")),
        "gameplay_ok": gameplay_ok,
        "gameplay_attempts": gameplay_attempts,
        "gameplay_checks": (gameplay.get("checks") or [])[:12],
        "gameplay_summary": (gameplay.get("summary") or "")[:500],
        "quality_ok": quality_ok,
        "quality": {
            key: critic.get(key)
            for key in ("ok", "na", "parsed", "passed", "overall", "summary", "error")
            if key in critic
        },
        "proof_incomplete": proof_incomplete,
        "unity_local_required": bool(gameplay.get("unity_local_required") or browser.get("unity_local_required")),
        "unity_scaffold_ok": unity_scaffold_ok,
        "chromium_missing": chromium_missing,
        "at": _now_iso(),
    }

    verify_map = dict(cfg.get("verify") or {})
    verify_map[mid] = record
    record["can_unity_waiver"] = can_advance_with_unity_waiver(
        {**cfg, "verify": verify_map},
        plan,
    )
    cfg["verify"] = verify_map
    cfg["last_verify_at"] = record["at"]
    save_config(slug, cfg)

    if on_log:
        if overall_ok:
            await _emit(on_log, f"✅ Milestone {mid} verify PASS — preview: {preview_url}", "ok")
        else:
            await _emit(on_log, f"❌ Milestone {mid} verify FAIL — оправи и пусни отново", "err")

    return {
        **record,
        "smoke": {k: smoke.get(k) for k in ("ok", "error", "skipped") if k in smoke},
        "browser": {
            k: browser.get(k)
            for k in ("ok", "skipped", "summary", "console_errors", "page_errors", "blank_screen")
            if k in browser
        },
        "gameplay": {
            k: gameplay.get(k)
            for k in ("ok", "skipped", "summary", "checks", "unity_local_required")
            if k in gameplay
        },
    }
