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


async def _get_kind(backend: BuilderBackend) -> str:
    if isinstance(backend, FullAutopilotBackend):
        return await backend.project_kind()
    fn = getattr(backend, "project_kind", None)
    if callable(fn):
        return await fn()
    return "vite-react"


async def _browser_verify(backend: BuilderBackend) -> dict[str, Any]:
    fn = getattr(backend, "browser_verify", None)
    if callable(fn):
        return await fn()
    return {"ok": True, "skipped": True}


async def run_full_autopilot(
    backend: BuilderBackend,
    state: PipelineState,
    *,
    max_repair_rounds: int = 3,
    max_stages: int = 8,
) -> PipelineState:
    """Staged full pipeline for standalone (and portable) backends."""
    kind = await _get_kind(backend)
    kind_rules = _KIND_RULES.get(kind, _KIND_RULES["static"])
    lessons = ""
    try:
        from daedalus.learned_skills import format_block
        lessons = format_block(f"{kind} {state.description}", limit=2)
    except Exception:
        lessons = ""

    state.seed_agents()
    await backend.emit("phase", {"n": 0, "label": "Чете задачата"})
    await backend.emit("log", {"line": f"Проект: {state.slug} · Тип: {kind}", "level": "dim"})

    # ── PLAN (multi-stage JSON) ──────────────────────────────────────────
    await backend.emit("phase", {"n": 1, "label": "Планиране"})
    state.set_agent_status(AgentRole.PLANNER, "running")

    plan_sys = (
        "Ти си строителен плановик. Връщаш САМО валиден JSON.\n"
        f"ПЛАТФОРМА: {kind}\n{kind_rules}\n\n"
        'ФОРМАТ: {"title":"...","stages":[{"id":1,"title":"...","goal":"...",'
        '"instructions":"...","files":["src/App.jsx"]}]}\n'
        "3-8 етапа, всеки с 1-3 файла в 'files'."
        + (f"\n\n{lessons}" if lessons else "")
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
    state.set_agent_status(AgentRole.PLANNER, "done")
    await backend.emit("log", {"line": f"План: {len(stages)} етапа — {plan.get('title', state.slug)}", "level": "info"})

    # ── STAGED BUILD ─────────────────────────────────────────────────────
    await backend.emit("phase", {"n": 2, "label": "Строене"})
    state.set_agent_status(AgentRole.CODER, "running")
    build_sys = (
        f"Senior engineer. ПЛАТФОРМА: {kind}. {kind_rules}\n"
        "Връщай или JSON {\"files\": {\"path\": \"content\"}} "
        "или блокове ===FILE: path===\\n...\\n===END===.\n"
        "Пълни, синтактично коректни файлове."
    )

    for i, stage in enumerate(stages, 1):
        title = stage.get("title", f"Stage {i}")
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
        files_map = _parse_file_blocks(raw)
        if not files_map:
            changes = _parse_files_json(raw, state.slug)
            files_map = {c.path: c.content for c in changes}
        if files_map:
            changes = [
                FileChange(path=p, action="create", content=c, summary=f"stage-{i}")
                for p, c in files_map.items()
            ]
            n = await backend.write_changes(changes)
            for c in changes:
                _upsert_change(state, c)
            await backend.emit("log", {"line": f"  ✓ {n} файл(а)", "level": "dim"})

    state.set_agent_status(AgentRole.CODER, "done")

    # ── TEST + REPAIR ────────────────────────────────────────────────────
    await backend.emit("phase", {"n": 3, "label": "Тестване"})
    state.set_agent_status(AgentRole.TESTER, "running")

    for attempt in range(1, max_repair_rounds + 1):
        result = await backend.smoke_test()
        if result.get("ok"):
            state.test_ok = True
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
        fix_map = _parse_file_blocks(fix_raw) or {}
        if not fix_map:
            for c in _parse_files_json(fix_raw, state.slug):
                fix_map[c.path] = c.content
        if fix_map:
            changes = [FileChange(path=p, action="update", content=c) for p, c in fix_map.items()]
            await backend.write_changes(changes)
            for c in changes:
                _upsert_change(state, c)

    state.set_agent_status(AgentRole.TESTER, "done" if state.test_ok else "blocked")

    # ── BROWSER VERIFY (visual kinds) ────────────────────────────────────
    browser_ok = True
    if state.test_ok and kind in VISUAL_KINDS:
        await backend.emit("phase", {"n": 4, "label": "Browser verify"})
        bresult = await _browser_verify(backend)
        browser_ok = bool(bresult.get("ok", True) or bresult.get("skipped"))
        if not browser_ok:
            err = (bresult.get("summary") or "browser verify failed")[:500]
            await backend.emit("log", {"line": f"⚠ Browser: {err}", "level": "warn"})
            state.errors.append(err)
        elif not bresult.get("skipped"):
            await backend.emit("log", {"line": "✓ Browser verify OK", "level": "info"})

    # ── REVIEW ───────────────────────────────────────────────────────────
    await backend.emit("phase", {"n": 5, "label": "Review"})
    state.set_agent_status(AgentRole.REVIEWER, "running")
    review = await backend.llm(
        AgentRole.REVIEWER,
        f"Review build for {state.slug} ({kind}). test_ok={state.test_ok} browser_ok={browser_ok}. "
        f"Files: {[c.path for c in state.changes]}. Reply APPROVED or list blockers.",
        system="Code reviewer. APPROVED only if shippable.",
    )
    state.review_ok = "APPROVED" in review.upper() and state.test_ok and browser_ok
    state.set_agent_status(AgentRole.REVIEWER, "done" if state.review_ok else "blocked")

    # ── DELIVERABLE ──────────────────────────────────────────────────────
    preview = await backend.preview_url()
    state.preview_url = preview
    deliverable = bool(preview and state.review_ok)

    if deliverable:
        state.set_agent_status(AgentRole.PUBLISHER, "done")
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
