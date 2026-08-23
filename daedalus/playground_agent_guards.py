"""Playground agent guards — implements Daedalus outbox self-improvement rules.

Centralises runtime checks so agent.py and admin autopilot share one policy.
"""
from __future__ import annotations

import re
from typing import Any

_SIMPLE_GAME_RE = re.compile(
    r"\b(snake|tetris|pong|tic[- ]?tac|memory\s+game|classic\s+arcade)\b",
    re.I,
)

_ENTRYPOINTS: dict[str, list[str]] = {
    "vite": ["index.html", "src/main.js", "vite.config.js"],
    "vite-react": ["index.html", "src/main.jsx", "vite.config.js"],
    "static": ["index.html", "game.js", "script.js", "style.css"],
    "expo": ["App.jsx", "app.json", "package.json"],
}

_NON_ENTRYPOINT_READS: dict[str, list[str]] = {
    "vite-react": ["src/App.jsx", "App.jsx"],
    "vite": ["src/scenes/GameScene.js", "src/game/phaserBoot.js"],
}

def outbox_guardrails_block(*, slug: str = "", kind: str = "static") -> str:
    """Prompt block injected into Playground / Autopilot sessions."""
    slug_line = f"ФИКСИРАН SLUG: `{slug}` — ползвай САМО него за READ/WRITE/RUN/PUBLISH.\n" if slug else ""
    return (
        "━━━ PLAYGROUND GUARDRAILS (outbox) ━━━\n"
        f"{slug_line}"
        "• 1 сесия = 1 проект — НЕ създавай NEED_PLAYGROUND_NEW ако slug вече е зададен.\n"
        "• РЕД: NEED_PLAYGROUND_WRITE преди NEED_PLAYGROUND_READ (не чети файл преди да го напишеш).\n"
        "• static проекти: НЕ ползвай NEED_PLAYGROUND_RUN — preview директно.\n"
        "• vite/vite-react: провери entrypoint (index.html → main.js/jsx) преди App.jsx/GameScene.\n"
        "• Expo/React Native: НЕ пиши index.html/style.css — ползвай View/Text или Git push.\n"
        "• Прости игри (Snake/Tetris/Pong): НЕ ползвай NEED_WEB_FETCH — пиши директно.\n"
        "• След проучване (≤3 tool calls) → BUILD, не оставай в recon.\n"
        f"• Платформа: {kind} — не смесвай web и mobile toolchain.\n"
    )


def is_simple_arcade_game(kind: str, text: str) -> bool:
    return kind in ("static", "vite") and bool(_SIMPLE_GAME_RE.search(text or ""))


def should_block_web_fetch(*, session_id: str, kind: str, project_text: str) -> str | None:
    """Outbox ob_f9082c2ca2 — skip web_fetch for well-known arcade games."""
    sid = (session_id or "").lower()
    if not sid.startswith(("pg-", "pg_", "playground:", "autopilot", "pg_auto")):
        return None
    if not is_simple_arcade_game(kind, project_text):
        return None
    return (
        "NEED_WEB_FETCH е блокиран за проста arcade игра — знаеш алгоритъма "
        "(canvas, keyboard, grid, collision). Пиши код директно с NEED_PLAYGROUND_WRITE."
    )


def playground_run_block_reason(kind: str) -> str | None:
    """Outbox ob_6eb6d7aeb7 — static apps don't need a dev runner."""
    if (kind or "").strip() == "static":
        return (
            "static проекти се serve-ват директно — НЕ ползвай NEED_PLAYGROUND_RUN.\n"
            "Preview: /admin/daedalus/playground/preview/{slug}/\n"
            "Публикувай с NEED_PLAYGROUND_PUBLISH след write."
        )
    return None


def entrypoint_read_warning(kind: str, path: str) -> str | None:
    """Outbox ob_4cfcb4b186 — read entrypoint chain before deep files."""
    k = (kind or "static").strip()
    rel = (path or "").strip().lstrip("/")
    if not rel:
        return None
    non_entry = _NON_ENTRYPOINT_READS.get(k, [])
    if rel not in non_entry:
        return None
    chain = _ENTRYPOINTS.get(k, [])
    if not chain:
        return None
    return (
        f"⚠️ `{rel}` не е entrypoint. Първо провери: {', '.join(chain)}.\n"
        "Ако файлът липсва — NEED_PLAYGROUND_WRITE, не READ."
    )


def read_file_guard(slug: str, path: str) -> str | None:
    """Return error message if read should be blocked; None if OK."""
    from . import playground as pg

    rel = (path or "").strip().lstrip("/")
    if not rel:
        return None
    root = pg.project_dir(slug)
    target = root / rel
    if target.is_file():
        return None
    return (
        f"Файлът `{rel}` не съществува в проекта `{slug}`.\n"
        "ПРАВИЛО (write-before-read): първо NEED_PLAYGROUND_WRITE с пълен файл, "
        "след това READ за проверка.\n"
        f"Провери дървото с NEED_PLAYGROUND_READ: {slug} (без path)."
    )


def expo_web_kind_mismatch(kind: str, tree_paths: list[str]) -> str | None:
    """Outbox ob_4a30c9d571 — Expo project treated as static/vite."""
    k = (kind or "").strip()
    paths = tree_paths or []
    joined = " ".join(paths[:40])
    if k == "expo":
        if "index.html" in paths and any(p.endswith((".jsx", ".tsx")) for p in paths):
            return (
                "Проектът е Expo — НЕ пиши index.html/style.css в static web режим.\n"
                "Варианти: NEED_PLAYGROUND_PUSH (Git → Expo Go) или одобрена конверсия към vite-react."
            )
        return None
    if k in ("static", "vite", "vite-react"):
        if "app.json" in joined and any(p.endswith(".tsx") for p in paths):
            return (
                "Проектът е web kind, но има Expo файлове (app.json, .tsx).\n"
                "Смени kind на expo ИЛИ ползвай NEED_PLAYGROUND_PUSH за mobile."
            )
    return None


async def resolve_existing_project_slug(name: str) -> dict[str, Any] | None:
    """Before playground_new — reuse existing project if slug matches."""
    from . import playground as pg

    slug = pg.slugify(name or "")
    if not slug:
        return None
    return await pg.get_project(slug)


async def build_repair_context(slug: str, proj: dict[str, Any]) -> tuple[str, str, str]:
    """combined_sys, build_model, kind for LLM repair helpers."""
    from .build_prompts import build_combined_sys, resolve_build_model_for_project
    from .game_platform_guide import is_likely_game

    kind = proj.get("kind", "static")
    coding_mode = proj.get("coding_mode", "lite")
    desc = proj.get("description", "") or proj.get("name", slug)
    combined_sys = build_combined_sys(kind, coding_mode)
    build_model, _ = resolve_build_model_for_project(
        desc,
        is_game=is_likely_game(f"{proj.get('name', slug)} {desc}"),
        coding_mode=coding_mode,
    )
    if not build_model:
        build_model = "anthropic/claude-sonnet-4"
    return combined_sys, build_model, kind


async def publish_with_repair(slug: str, *, icon: str = "🎮") -> dict[str, Any]:
    """Publish with vite compile + runtime repair (manual + agent path)."""
    from . import playground as pg
    from . import playground_publish as pub
    from .playground_verify_fix import repair_runtime_after_build, repair_vite_before_publish
    from . import daedalus_mesh as mesh

    proj = await pg.get_project(slug)
    if not proj:
        raise ValueError(f"project '{slug}' not found")
    kind = proj.get("kind", "static")
    repaired: list[str] = []
    if kind in ("vite", "vite-react"):
        from .playground_asset_verify import ensure_missing_preload_placeholders

        placeholders = ensure_missing_preload_placeholders(slug)
        repaired.extend(placeholders)
        combined_sys, build_model, _ = await build_repair_context(slug, proj)
        ok, err, files = await repair_vite_before_publish(
            slug, kind=kind, combined_sys=combined_sys,
            build_model=build_model, route_fn=mesh.route, max_attempts=5,
        )
        repaired.extend(files)
        if not ok:
            raise RuntimeError(err or "vite repair failed")
        rt_ok, rt_err, rt_files = await repair_runtime_after_build(
            slug, kind=kind, combined_sys=combined_sys,
            build_model=build_model, route_fn=mesh.route, max_attempts=2,
        )
        repaired.extend(rt_files)
        if not rt_ok:
            raise RuntimeError(rt_err or "runtime repair failed")
    res = await pub.publish(slug, icon=icon)
    if repaired:
        res["repaired_files"] = list(dict.fromkeys(repaired))
    return res
