"""Portable full autopilot — staged plan/build/test/verify without ADN.

Used by standalone Workshop ``mode=full``. ADN keeps its richer pipeline in
admin.py (research, assets, learning); this module covers the core loop:
  plan → staged code → smoke/repair → optional browser verify → review → done
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Protocol, runtime_checkable

from .orchestrator import (
    AgentRole,
    BuilderBackend,
    FileChange,
    PipelineState,
    _parse_files_json,
    _upsert_change,
)
from .standalone_runtime import VISUAL_KINDS

log = logging.getLogger(__name__)

_KIND_RULES: dict[str, str] = {
    "static": (
        "STATIC HTML/CSS/JS. Single or multi-file. No ES modules without bundler. "
        "CDN libs OK. Entry: index.html."
    ),
    "vite": (
        "VITE vanilla JS. Required: index.html, main.js, vite.config.js with base:'./'. "
        "ES modules in main.js."
    ),
    "vite-react": (
        "VITE+REACT 18. Required: index.html, src/main.jsx, src/App.jsx, vite.config.js "
        "with @vitejs/plugin-react. React from npm only."
    ),
    "fastapi": (
        "FASTAPI Python. main.py with `app = FastAPI()`, requirements.txt with fastapi+uvicorn."
    ),
    "streamlit": (
        "STREAMLIT Python. streamlit_app.py with `import streamlit as st`, requirements.txt."
    ),
    "node": (
        "NODE.JS. index.js + package.json. Optional GET /health for smoke."
    ),
    "expo": (
        "EXPO React Native. App.jsx, app.json, package.json, babel.config.js. "
        "Only RN components — no HTML."
    ),
}


@runtime_checkable
class FullAutopilotBackend(BuilderBackend, Protocol):
    async def project_kind(self) -> str: ...

    async def browser_verify(self) -> dict[str, Any]: ...


def _extract_json(text: str) -> dict | None:
    for candidate in (
        text,
        re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.MULTILINE),
    ):
        try:
            return json.loads(candidate)
        except Exception:
            pass
    m = re.search(r"\{[\s\S]+\}", text)
    if m:
        try:
            return json.loads(m.group(0))
        except Exception:
            pass
    return None


def _parse_file_blocks(text: str) -> dict[str, str]:
    """Parse ===FILE: path=== ... ===END=== blocks (ADN autopilot format)."""
    files: dict[str, str] = {}
    pattern = re.compile(
        r"===FILE:\s*(.+?)\s*===\r?\n(.*?)\r?\n?===END===",
        re.DOTALL,
    )
    for m in pattern.finditer(text):
        path = m.group(1).strip().lstrip("/")
        if path:
            files[path] = m.group(2)
    return files


_FENCE_LANG_PATH = {
    "html": "index.html",
    "htm": "index.html",
    "css": "style.css",
    "js": "main.js",
    "javascript": "main.js",
    "jsx": "src/App.jsx",
    "json": "package.json",
}


def _parse_fenced_files(text: str) -> dict[str, str]:
    """Accept markdown fences from weaker models (```html / ```index.html)."""
    files: dict[str, str] = {}
    pattern = re.compile(r"```([^\n`]*)\n(.*?)```", re.DOTALL)
    for m in pattern.finditer(text or ""):
        info = (m.group(1) or "").strip()
        body = (m.group(2) or "").strip("\n")
        if not body.strip():
            continue
        token = (info.split()[0] if info else "").strip().lstrip("./")
        path = ""
        low = token.lower()
        if "/" in token or any(low.endswith(ext) for ext in (".html", ".htm", ".js", ".css", ".jsx", ".json", ".py")):
            path = token
        elif low in _FENCE_LANG_PATH:
            path = _FENCE_LANG_PATH[low]
        elif "<html" in body.lower() or "<!doctype html" in body.lower():
            path = "index.html"
        if path:
            files[path.lstrip("/")] = body
    return files


def _parse_loose_html(text: str) -> dict[str, str]:
    t = (text or "").strip()
    low = t.lower()
    if low.startswith("<!doctype html") or low.startswith("<html"):
        return {"index.html": t}
    return {}


def extract_coder_files(raw: str, slug: str = "") -> dict[str, str]:
    """FILE blocks, then JSON {files}, then markdown fences, then raw HTML."""
    files = _parse_file_blocks(raw)
    if files:
        return files
    files = {c.path: c.content for c in _parse_files_json(raw, slug)}
    if files:
        return files
    files = _parse_fenced_files(raw)
    if files:
        return files
    return _parse_loose_html(raw)


async def _get_kind(backend: BuilderBackend) -> str:
    if isinstance(backend, FullAutopilotBackend):
        return await backend.project_kind()
    fn = getattr(backend, "project_kind", None)
    if callable(fn):
        return await fn()
    return "vite-react"


def _run_id(backend: BuilderBackend) -> str:
    job = getattr(backend, "job", None)
    return str(getattr(job, "id", "") or "")


def _save_stage_checkpoint(backend: BuilderBackend, state: PipelineState, *, kind: str, stage: int) -> None:
    run_id = _run_id(backend)
    if not run_id:
        return
    try:
        from daedalus.run_engine import record
        record(
            run_id,
            "stage",
            {"i": stage, "slug": state.slug},
            slug=state.slug,
            checkpoint={
                "stage": stage,
                "slug": state.slug,
                "kind": kind,
                "phase": "build",
                "plan": state.plan,
            },
        )
    except Exception:
        log.debug("stage checkpoint skipped", exc_info=True)


async def _browser_verify(backend: BuilderBackend) -> dict[str, Any]:
    fn = getattr(backend, "browser_verify", None)
    if callable(fn):
        return await fn()
    return {"ok": False, "skipped": True, "summary": "browser verify unavailable — not evidence"}


async def run_full_autopilot(
    backend: BuilderBackend,
    state: PipelineState,
    *,
    max_repair_rounds: int = 3,
    max_stages: int = 8,
    resume_from_stage: int = 0,
    saved_plan: dict[str, Any] | None = None,
) -> PipelineState:
    """Staged full pipeline for standalone (and portable) backends."""
    kind = await _get_kind(backend)
    kind_rules = _KIND_RULES.get(kind, _KIND_RULES["static"])
    lessons = ""
    try:
        from daedalus.learned_skills import disclose
        lessons = disclose(f"{kind} {state.description}", limit=2)
    except Exception:
        lessons = ""
    specimen = ""
    try:
        from daedalus.dissection import prompt_block_for_slug
        specimen = prompt_block_for_slug(state.slug)
    except Exception:
        specimen = ""

    state.seed_agents()
    await backend.emit("phase", {"n": 0, "label": "Чете задачата"})
    await backend.emit("log", {"line": f"Проект: {state.slug} · Тип: {kind}", "level": "dim"})

    # ── PLAN (multi-stage JSON) ──────────────────────────────────────────
    await backend.emit("phase", {"n": 1, "label": "Планиране"})
    state.set_agent_status(AgentRole.PLANNER, "running")

    if saved_plan:
        stages = saved_plan.get("stages") if isinstance(saved_plan.get("stages"), list) else []
        if not stages:
            stages = [{"id": 1, "title": "MVP", "goal": "Working app", "instructions": state.description, "files": []}]
        stages = stages[:max_stages]
        state.plan = saved_plan
        await backend.emit("log", {"line": f"План от checkpoint: {len(stages)} етапа", "level": "info"})
    else:
        plan_sys = (
            "Ти си строителен плановик. Връщаш САМО валиден JSON.\n"
            f"ПЛАТФОРМА: {kind}\n{kind_rules}\n\n"
            'ФОРМАТ: {"title":"...","stages":[{"id":1,"title":"...","goal":"...",'
            '"instructions":"...","files":["src/App.jsx"]}]}\n'
            "3-8 етапа, всеки с 1-3 файла в 'files'."
            + (f"\n\n{lessons}" if lessons else "")
            + (f"\n\n{specimen}" if specimen else "")
        )
        plan_raw = await backend.llm(
            AgentRole.PLANNER,
            f"Проект: {state.description}\n\nСъздай строителен план.",
            system=plan_sys,
        )
        plan = _extract_json(plan_raw) or {}
        stages = plan.get("stages") if isinstance(plan.get("stages"), list) else []
        if not stages:
            stages = [{"id": 1, "title": "MVP", "goal": "Working app", "instructions": state.description, "files": []}]
        stages = stages[:max_stages]
        state.plan = {"title": plan.get("title", state.slug), "stages": stages, "kind": kind}
        await backend.emit("log", {"line": f"План: {len(stages)} етапа — {plan.get('title', state.slug)}", "level": "info"})
    state.set_agent_status(AgentRole.PLANNER, "done")
    if resume_from_stage <= 0:
        _save_stage_checkpoint(backend, state, kind=kind, stage=0)

    # ── STAGED BUILD ─────────────────────────────────────────────────────
    await backend.emit("phase", {"n": 2, "label": "Строене"})
    state.set_agent_status(AgentRole.CODER, "running")
    build_sys = (
        f"Senior engineer. ПЛАТФОРМА: {kind}. {kind_rules}\n"
        "Връщай или JSON {\"files\": {\"path\": \"content\"}} "
        "или блокове ===FILE: path===\\n...\\n===END===.\n"
        "Пълни, синтактично коректни файлове."
        + (f"\n\n{specimen}" if specimen else "")
    )

    for i, stage in enumerate(stages, 1):
        title = stage.get("title", f"Stage {i}")
        if i <= resume_from_stage:
            await backend.emit("log", {"line": f"⏭ Етап {i}/{len(stages)} вече е в checkpoint — пропускам", "level": "dim"})
            continue
        await backend.emit("log", {"line": f"▶ Етап {i}/{len(stages)}: {title}", "level": "info"})
        stage_files = stage.get("files") or []
        prompt = (
            f"Етап {i}: {title}\n"
            f"Цел: {stage.get('goal', '')}\n"
            f"Инструкции: {stage.get('instructions', '')}\n"
            f"Файлове за този етап: {stage_files}\n\n"
            f"Общо описание: {state.description[:1500]}"
        )
        raw = await backend.llm(AgentRole.CODER, prompt, system=build_sys)
        files_map = extract_coder_files(raw, state.slug)
        if files_map:
            changes = [
                FileChange(path=p, action="create", content=c, summary=f"stage-{i}")
                for p, c in files_map.items()
            ]
            n = await backend.write_changes(changes)
            for c in changes:
                _upsert_change(state, c)
            await backend.emit("log", {"line": f"  ✓ {n} файл(а)", "level": "dim"})
        _save_stage_checkpoint(backend, state, kind=kind, stage=i)

    state.set_agent_status(AgentRole.CODER, "done")

    # ── TEST + REPAIR ────────────────────────────────────────────────────
    await backend.emit("phase", {"n": 3, "label": "Тестване"})
    state.set_agent_status(AgentRole.TESTER, "running")

    for attempt in range(1, max_repair_rounds + 1):
        result = await backend.smoke_test()
        if result.get("ok"):
            state.test_ok = True
            break
        if result.get("skipped"):
            err = (result.get("error") or result.get("summary") or "smoke skipped — not evidence")[:500]
            await backend.emit("log", {"line": f"⚠ Тест skipped — not evidence: {err[:120]}", "level": "warn"})
            state.errors.append(err)
            break
        err = (result.get("error") or "")[:2500]
        logs = "\n".join((result.get("logs") or [])[-35:])
        await backend.emit("log", {"line": f"⚠ Тест {attempt}/{max_repair_rounds}: {err[:120]}", "level": "warn"})
        fix_raw = await backend.llm(
            AgentRole.CODER,
            f"Поправи build грешката.\n\nГрешка:\n{err}\n\nЛогове:\n{logs}\n\n"
            f"Платформа: {kind}. Върни JSON files или ===FILE=== блокове.",
            system=build_sys,
        )
        fix_map = extract_coder_files(fix_raw, state.slug)
        if fix_map:
            changes = [FileChange(path=p, action="update", content=c) for p, c in fix_map.items()]
            await backend.write_changes(changes)
            for c in changes:
                _upsert_change(state, c)

    state.set_agent_status(AgentRole.TESTER, "done" if state.test_ok else "blocked")

    # ── BROWSER VERIFY (visual kinds) ────────────────────────────────────
    from daedalus.game_build_contract import skip_is_not_ok, verified
    from daedalus.quality_gate import critic_counts_as_pass, maybe_critic

    browser_ok = True
    bresult: dict[str, Any] = {}
    visual = kind in VISUAL_KINDS
    if state.test_ok and visual:
        await backend.emit("phase", {"n": 4, "label": "Browser verify"})
        bresult = skip_is_not_ok(await _browser_verify(backend))
        browser_ok = verified(bresult)
        if not browser_ok:
            err = (bresult.get("summary") or "browser verify failed")[:500]
            if bresult.get("skipped"):
                err = "browser verify skipped — not evidence"
            await backend.emit("log", {"line": f"⚠ Browser: {err}", "level": "warn"})
            state.errors.append(err)
        else:
            await backend.emit("log", {"line": "✓ Browser verify OK", "level": "info"})

    # ── PLAYWRIGHT GAMEPLAY (interaction, not screenshot-only) ───────────
    gameplay_ok = True
    if state.test_ok and visual and browser_ok:
        await backend.emit("phase", {"n": 4, "label": "Gameplay"})
        try:
            from daedalus.engine_runners import gameplay_for_kind
            gp = await gameplay_for_kind(state.slug, kind)
            gameplay_ok = verified(gp)
            if not gameplay_ok:
                err = (gp.get("summary") or "gameplay verify failed")[:500]
                await backend.emit("log", {"line": f"⚠ Gameplay: {err}", "level": "warn"})
                state.errors.append(err)
            else:
                await backend.emit("log", {"line": "✓ Gameplay interaction OK", "level": "info"})
        except Exception as exc:
            gameplay_ok = False
            state.errors.append(f"gameplay verify error: {exc}"[:200])
            await backend.emit("log", {"line": f"⚠ Gameplay: {exc}", "level": "warn"})

    # ── QUALITY CRITIC (default on; unparsed is not a pass) ──────────────
    quality_ok = True
    if state.test_ok and visual and browser_ok:
        critic = await maybe_critic(
            slug=state.slug,
            kind=kind,
            description=state.description,
            plan=state.plan,
            bv=bresult,
            visual=True,
        )
        if critic.get("na"):
            quality_ok = True
        else:
            quality_ok = critic_counts_as_pass(critic)
            score = critic.get("overall")
            await backend.emit(
                "log",
                {
                    "line": (
                        f"Quality critic overall={score} parsed={critic.get('parsed')} "
                        f"passed={quality_ok}"
                    ),
                    "level": "info" if quality_ok else "warn",
                },
            )
            if not quality_ok:
                err = critic.get("error") or critic.get("summary") or "quality critic did not pass"
                state.errors.append(str(err)[:300])

    # ── PLAYTEST personas (extra signal, not a substitute for Playwright) ─
    playtest_block = ""
    if visual:
        await backend.emit("phase", {"n": 4, "label": "Playtest personas"})
        try:
            from daedalus.playtest import format_for_prompt, run_playtest

            async def _play_llm(system: str, user: str) -> str:
                return await backend.llm(AgentRole.REVIEWER, user, system=system)

            report = await run_playtest(
                state.slug, kind=kind, n=3, llm_fn=_play_llm,
            )
            playtest_block = format_for_prompt(report)
            await backend.emit("log", {"line": report.get("summary") or "playtest", "level": "info"})
            if not report.get("ship_ready"):
                for b in (report.get("blockers") or [])[:4]:
                    state.errors.append(str(b)[:200])
        except Exception as exc:
            log.debug("persona playtest skipped: %s", exc)
            await backend.emit("log", {"line": f"persona playtest skipped: {exc}", "level": "dim"})

    # ── REVIEW ───────────────────────────────────────────────────────────
    await backend.emit("phase", {"n": 5, "label": "Review"})
    state.set_agent_status(AgentRole.REVIEWER, "running")
    review = await backend.llm(
        AgentRole.REVIEWER,
        f"Review build for {state.slug} ({kind}). test_ok={state.test_ok} "
        f"browser_ok={browser_ok} gameplay_ok={gameplay_ok} quality_ok={quality_ok}. "
        f"Files: {[c.path for c in state.changes]}. Reply APPROVED or list blockers."
        + (f"\n\n{playtest_block}" if playtest_block else ""),
        system="Code reviewer. APPROVED only if shippable. Skip/screenshot is not a playtest.",
    )
    state.review_ok = (
        "APPROVED" in review.upper()
        and state.test_ok
        and browser_ok
        and gameplay_ok
        and quality_ok
    )
    state.set_agent_status(AgentRole.REVIEWER, "done" if state.review_ok else "blocked")

    # ── DELIVERABLE ──────────────────────────────────────────────────────
    preview = await backend.preview_url()
    state.preview_url = preview
    deliverable = bool(preview and state.review_ok)

    if deliverable:
        state.set_agent_status(AgentRole.PUBLISHER, "done")
        try:
            from daedalus.learned_skills import draft_from_success
            if state.test_ok:
                draft_from_success(kind=kind, summary=(state.description or "")[:400])
        except Exception:
            pass
        await backend.emit(
            "done",
            {
                "files": [c.path for c in state.changes],
                "assets": [],
                "preview_url": preview,
                "verified": state.test_ok,
            },
        )
    else:
        msg = "; ".join(state.errors) or "blocked at test/review/deliverable"
        state.errors.append(msg)
        try:
            from daedalus.learned_skills import draft_from_failure
            draft_from_failure(kind=kind, errors=state.errors, context=state.description[:400])
        except Exception:
            pass
        await backend.emit("error", {"message": msg})

    return state
