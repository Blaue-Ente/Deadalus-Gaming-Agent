"""Autopilot verify/repair helpers — vite rebuild after browser fixes, LLM compile repair."""
from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from . import playground as pg
from . import playground_runner as runner
from .playground_build_utils import extract_llm_text, parse_build_files

log = logging.getLogger(__name__)

_SENSITIVE = (
    ".env", "secret", "credential", "password", "api_key", "apikey", "token",
    "private_key", ".pem", ".p12",
)

_VITE_REACT_FIX_ADDENDUM = """
━━━ VITE-REACT COMPILE / RUNTIME FIXES ━━━
• Всеки компонент (.jsx) — `export default function Name` или `export default Name`
• Импорти: пътят трябва да съвпада с файла (./Level1 за Level1.jsx)
• Няма circular imports между Level* файлове — изнеси споделено в utils/
• SproutPhase / фази: един файл, default export, props с ясни имена
• main.jsx: провери root mount (#root) и че App се import-ва коректно
• След промяна на import/export — синтактично валиден JSX, без смесване default/named
"""

_VITE_FIX_ADDENDUM = """
━━━ VITE / PHASER COMPILE FIXES ━━━
• "Could not resolve" → създай липсващия файл ИЛИ коригирай import пътя (case-sensitive!)
• Phaser scenes: ако main.js import-ва ./scenes/Level1Scene.js — файлът ТРЯБВА да съществува
• Scene файл: `export default class Level1Scene extends Phaser.Scene { ... }`
• Провери index.html: `<script type="module" src="/src/main.js">` (или коректен entry)
• package.json: phaser в dependencies; vite.config ако има base/path aliases
• updateHUD / HUD: инициализирай ВСИЧКИ text/image референции в create() — в update() ползвай optional chaining (this.hud?.score?.setText)
• Loading bar stuck: this.load.on('loaderror') + placeholder texture; this.load.on('complete', () => scene.start(...))
• Не викай updateHUD преди create() да е приключил — флаг this.ready = true в create()
"""


def _is_sensitive(path: str) -> bool:
    low = path.lower()
    return any(p in low for p in _SENSITIVE)


def collect_project_code(slug: str, *, max_bytes: int = 14000, total_cap: int = 40000) -> str:
    chunks: list[str] = []
    total = 0
    for f in pg.read_tree(slug):
        path = f.get("path") or ""
        if _is_sensitive(path):
            continue
        try:
            fc = pg.read_file(slug, path, max_bytes=max_bytes)
        except Exception:
            continue
        block = f"\n===FILE: {path}===\n{fc}\n===END===\n"
        if total + len(block) > total_cap:
            break
        chunks.append(block)
        total += len(block)
    return "".join(chunks)


async def llm_fix_from_error(
    slug: str,
    *,
    kind: str,
    error: str,
    combined_sys: str,
    build_model: str,
    route_fn: Callable[..., Awaitable[Any]],
    client_id: str,
    problem_label: str,
    extra_addendum: str = "",
) -> dict[str, str]:
    proj_code = collect_project_code(slug)
    fix_prompt = (
        f"ПЛАТФОРМА: {kind}\n"
        f"{problem_label}\n\n"
        f"{error[:2500]}\n\n"
    )
    if extra_addendum:
        fix_prompt += extra_addendum + "\n\n"
    fix_prompt += (
        f"ТЕКУЩ КОД:\n{proj_code}\n\n"
        f"Поправи ПЪРВОПРИЧИНАТА. Върни САМО променените/новите файлове "
        f"в ===FILE: path===\\n...\\n===END=== формат, всеки синтактично завършен."
    )
    try:
        fres = await route_fn(
            messages=[
                {"role": "system", "content": combined_sys},
                {"role": "user", "content": fix_prompt},
            ],
            model=build_model,
            intent="code",
            max_tokens=32000,
            temperature=0.25,
            client_id=client_id,
        )
        return parse_build_files(extract_llm_text(fres))
    except Exception as exc:
        log.warning("llm_fix_from_error failed: %s", exc)
        return {}


def write_fix_files(slug: str, fix_files: dict[str, str]) -> list[str]:
    written: list[str] = []
    for path, content in fix_files.items():
        try:
            pg.write_file(slug, path, content)
            written.append(path)
        except Exception:
            pass
    return written


def vite_fix_addendum(kind: str) -> str:
    if kind == "vite-react":
        return _VITE_REACT_FIX_ADDENDUM
    if kind == "vite":
        return _VITE_FIX_ADDENDUM
    return ""


async def ensure_vite_build_after_edits(
    slug: str,
    *,
    kind: str,
    combined_sys: str,
    build_model: str,
    route_fn: Callable[..., Awaitable[Any]],
    max_attempts: int = 5,
    client_prefix: str = "autopilot-vite-rebuild",
    problem_label: str = (
        "След визуална поправка `vite build` НЕ МИНА. "
        "Това е РЕАЛНАТА compile грешка (import/export/syntax):"
    ),
) -> tuple[bool, str, list[str]]:
    """Run vite build gate; on failure, LLM-fix compile errors and retry."""
    if kind not in ("vite", "vite-react"):
        return True, "", []

    addendum = vite_fix_addendum(kind)
    last_err = ""
    written_all: list[str] = []

    for attempt in range(max_attempts):
        try:
            sm = await runner.smoke_test(slug)
        except Exception as exc:
            sm = {"ok": False, "error": str(exc), "logs": []}

        if sm.get("ok") or sm.get("skipped"):
            return True, "", written_all

        last_err = (
            sm.get("error")
            or "\n".join((sm.get("logs") or [])[-30:])
            or "vite build failed"
        ).strip()

        if attempt + 1 >= max_attempts:
            break

        fix_files = await llm_fix_from_error(
            slug,
            kind=kind,
            error=last_err,
            combined_sys=combined_sys,
            build_model=build_model,
            route_fn=route_fn,
            client_id=f"{client_prefix}/{slug}/a{attempt + 1}",
            problem_label=problem_label,
            extra_addendum=addendum,
        )
        if not fix_files:
            break
        written_all.extend(write_fix_files(slug, fix_files))

    return False, last_err, written_all


async def repair_vite_before_publish(
    slug: str,
    *,
    kind: str,
    combined_sys: str,
    build_model: str,
    route_fn: Callable[..., Awaitable[Any]],
    max_attempts: int = 5,
) -> tuple[bool, str, list[str]]:
    """Smoke-test + LLM repair loop before manual publish/rebuild."""
    if kind not in ("vite", "vite-react"):
        return True, "", []

    repaired: list[str] = []
    ok, err, repaired = await ensure_vite_build_after_edits(
        slug,
        kind=kind,
        combined_sys=combined_sys,
        build_model=build_model,
        route_fn=route_fn,
        max_attempts=max_attempts,
        client_prefix=f"publish-vite-repair/{slug}",
        problem_label=(
            "Преди публикуване `vite build` НЕ МИНА. "
            "Поправи compile грешката (липсващ import/файл, syntax, export):"
        ),
    )
    return ok, err, repaired


async def repair_runtime_after_build(
    slug: str,
    *,
    kind: str,
    combined_sys: str,
    build_model: str,
    route_fn: Callable[..., Awaitable[Any]],
    max_attempts: int = 2,
) -> tuple[bool, str, list[str]]:
    """Browser-verify dist/workspace; on runtime errors, LLM-fix and retry."""
    if kind not in ("vite", "vite-react", "static"):
        return True, "", []

    from . import playground_browser as pbrowser

    written_all: list[str] = []
    last_err = ""

    for attempt in range(max_attempts):
        try:
            bv = await pbrowser.verify_app(slug, kind)
        except Exception as exc:
            bv = {"ok": False, "error": str(exc), "skipped": True}

        if bv.get("skipped"):
            return True, "", written_all
        if bv.get("ok"):
            return True, "", written_all

        parts: list[str] = []
        for key in ("page_errors", "console_errors", "failed_requests"):
            for item in (bv.get(key) or [])[:5]:
                parts.append(str(item))
        last_err = (
            bv.get("summary")
            or "; ".join(parts)
            or bv.get("error")
            or "runtime verify failed"
        ).strip()

        if attempt + 1 >= max_attempts:
            break

        fix_files = await llm_fix_from_error(
            slug,
            kind=kind,
            error=last_err[:2500],
            combined_sys=combined_sys,
            build_model=build_model,
            route_fn=route_fn,
            client_id=f"publish-runtime-repair/{slug}/a{attempt + 1}",
            problem_label=(
                "Играта СЕ КОМПИЛИРА, но в браузър има runtime грешка "
                "(бял екран / Loading stuck / updateHUD / undefined):"
            ),
            extra_addendum=vite_fix_addendum(kind),
        )
        if not fix_files:
            break
        written_all.extend(write_fix_files(slug, fix_files))
        if kind in ("vite", "vite-react"):
            rebuilt, rebuild_err, _ = await ensure_vite_build_after_edits(
                slug,
                kind=kind,
                combined_sys=combined_sys,
                build_model=build_model,
                route_fn=route_fn,
                max_attempts=2,
                client_prefix=f"publish-runtime-rebuild/{slug}",
                problem_label="След runtime поправка vite build не мина:",
            )
            if not rebuilt:
                last_err = rebuild_err or last_err
                break

    return False, last_err, written_all
