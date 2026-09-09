#!/usr/bin/env python3
"""A/B sim: writer assist Off vs Kernel-scaffold on a short Phaser slice.

Ember Walk is not Glass Orchard — craft budget, Mentor off, isolated
``$DAEDALUS_DATA``. Same writer model in both cells.

  DAEDALUS_STANDALONE=1 python3 scripts/sim_writer_assist.py --pair
  DAEDALUS_STANDALONE=1 python3 scripts/sim_writer_assist.py --assist kernel-scaffold \\
      --model cohere/north-mini-code:free
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
import sys
import time
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_SCRIPTS))

import live_eval_agent_chat as _chat  # noqa: E402
import live_eval_build as _leb  # noqa: E402

log = logging.getLogger("sim_writer_assist")

# Free-first writer pool. Blog slugs qwen/qwen3-coder:free and
# poolside/laguna-m.1:free are gone from the OpenRouter catalog (2026-09).
CANDIDATES = (
    "cohere/north-mini-code:free",
    "nex-agi/nex-n2.5-mini:free",
    "poolside/laguna-xs-2.1:free",
    "qwen/qwen3-coder-30b-a3b-instruct",
    "openai/gpt-oss-120b",
    "z-ai/glm-5.3-flash",
)

EMBER = (
    "Направи нова Playground игра: kind=vite, slug=ember-walk, име Ember Walk. "
    "Движок Phaser 3 през npm (phaser ^3.80) заедно с Vite vanilla (не React, не Vue). "
    "Първо playground_new({kind:\"vite\", slug:\"ember-walk\", name:\"Ember Walk\"}). "
    "src/main.js: new Phaser.Game, type: Phaser.WEBGL, parent: 'game', scale.FIT, pixelArt. "
    "Сцени Boot, Menu, Play — всяка в src/scenes/, import Phaser from 'phaser'. "
    "index.html държи #game, не #app. package.json има vite и phaser. "
    "Геймплей: WASD разходка, събери 3 топли орба, HUD брои 0/3, победа при 3. "
    "Menu рисува заглавие Ember Walk; Enter започва. Vite Hello/#app броячът е забранен. "
    "Ред: playground_new → пиши Play.js (не връщай main.js към Vite Hello) → "
    "run_command command=\"npm install\" → run_command command=\"npm run build\" → browser. "
    "Не прави Godot. Не прави Expo. Не прави змия, tetris или pong."
)

_HELLO_RE = re.compile(
    r"querySelector\(\s*['\"]#app['\"]\)|hello\s+vite|Vite \+ Vanilla JS",
    re.I,
)
_WASD_RE = re.compile(r"\bWASD\b|cursors|keyboard\.addKeys|createCursorKeys", re.I)
_ORB_RE = re.compile(r"\borb|collect|harvest|score\b", re.I)


def _isolate(data: Path, assist: str, *, mentor: str = "") -> None:
    _chat._isolate_env(data)
    os.environ["DAEDALUS_WRITER_ASSIST"] = assist
    os.environ["DAEDALUS_MENTOR"] = "1" if mentor.strip() else "0"
    os.environ.setdefault("DAEDALUS_NATIVE_TOOLS", "1")
    os.environ.setdefault("DAEDALUS_VISUAL_PROOF", "1")
    os.environ.setdefault("DAEDALUS_WRITE_GATE", "1")


def _assist_quality(slug: str) -> dict:
    from daedalus import playground as pg

    root = pg.project_dir(slug) if slug else None
    if not root or not root.is_dir():
        return {"ok": False, "reason": "no_project"}
    main = root / "src" / "main.js"
    html = root / "index.html"
    play = root / "src" / "scenes" / "Play.js"
    menu = root / "src" / "scenes" / "Menu.js"
    pkg = root / "package.json"
    marker = root / ".daedalus-writer-assist.json"
    main_txt = main.read_text(encoding="utf-8") if main.is_file() else ""
    html_txt = html.read_text(encoding="utf-8") if html.is_file() else ""
    play_txt = play.read_text(encoding="utf-8") if play.is_file() else ""
    menu_txt = menu.read_text(encoding="utf-8") if menu.is_file() else ""
    pkg_txt = pkg.read_text(encoding="utf-8") if pkg.is_file() else ""
    blob = "\n".join((html_txt, main_txt, play_txt, menu_txt, pkg_txt))
    stub_play = "fill this scene" in play_txt
    return {
        "ok": True,
        "kernel_seeded": marker.is_file(),
        "has_game_mount": 'id="game"' in html_txt,
        "has_app_mount": 'id="app"' in html_txt,
        "vite_hello": bool(_HELLO_RE.search(main_txt)),
        "phaser_webgl": "Phaser.WEBGL" in blob,
        "phaser_auto": "Phaser.AUTO" in blob,
        "import_phaser": "from 'phaser'" in blob or 'from "phaser"' in blob,
        "pkg_phaser": "phaser" in pkg_txt.lower(),
        "play_wasd": bool(_WASD_RE.search(play_txt)),
        "play_orbs": bool(_ORB_RE.search(play_txt)),
        "play_still_stub": stub_play,
        "menu_title": "Ember Walk" in menu_txt or "Ember Walk" in main_txt,
        "dist": (root / "dist" / "index.html").is_file(),
        "node_modules": (root / "node_modules" / "phaser").is_dir(),
    }


async def _pick_model(chosen: str) -> tuple[str, list[dict]]:
    probes = []
    pool = (chosen,) if chosen else CANDIDATES
    picked = ""
    for mid in pool:
        try:
            row = await _leb.probe_tools(mid)
        except Exception as exc:
            row = {"model": mid, "ok": False, "error": str(exc)[:400]}
        probes.append(row)
        print(
            f"probe {mid}: ok={row.get('ok')} tools={row.get('tools')} "
            f"ms={row.get('ms')} err={row.get('error', '')[:80]}",
            flush=True,
        )
        if row.get("ok"):
            picked = mid
            break
    return picked, probes


async def run_cell(
    *,
    assist: str,
    model: str,
    data: Path,
    effort: str,
    shots: Path,
    mentor: str = "",
) -> dict:
    _isolate(data, assist, mentor=mentor)
    _chat._reset_path_caches()
    _leb._pin_openrouter(model)
    _isolate(data, assist, mentor=mentor)
    _chat._reset_path_caches()
    mentor_pin = None
    if mentor.strip():
        mentor_pin = _chat._pin_mentor(mentor.strip())

    playbook = _chat._preflight_route(EMBER)
    print(
        f"cell assist={assist} model={model} mentor={mentor or 'off'} "
        f"playbook={playbook.get('playbook')} kind={playbook.get('kind')}",
        flush=True,
    )
    t0 = time.time()
    chat = await _chat.run_chat(EMBER, effort)
    elapsed = round(time.time() - t0, 2)

    slug = str((chat.get("build_loop") or {}).get("slug") or "")
    if not slug:
        for c in chat.get("tool_calls") or []:
            if c.get("slug"):
                slug = str(c["slug"])
                break
    if not slug:
        from daedalus.paths import playground_projects_dir
        roots = list(playground_projects_dir().iterdir()) if playground_projects_dir().is_dir() else []
        dirs = [p.name for p in roots if p.is_dir()]
        if "ember-walk" in dirs:
            slug = "ember-walk"
        elif len(dirs) == 1:
            slug = dirs[0]

    snap = _leb._project_snapshot(slug)
    quality = _chat._inspect_quality(slug)
    assist_q = _assist_quality(slug)
    try:
        screenshots = _leb._export_screenshots(slug, shots)
    except Exception as exc:
        screenshots = []
        log.warning("screenshot export failed: %s", exc)

    loop = chat.get("build_loop") or {}
    return {
        "assist": assist,
        "model": model,
        "mentor": mentor_pin or {"enabled": False},
        "elapsed_s": elapsed,
        "playbook_preflight": playbook,
        "slug": slug,
        "complete": chat.get("complete"),
        "chat": {
            "elapsed_s": chat.get("elapsed_s"),
            "effort": chat.get("effort"),
            "tool_names": chat.get("tool_names"),
            "browser_or_proof": chat.get("browser_or_proof"),
            "reply": (chat.get("reply") or "")[:1500],
        },
        "build_loop": {
            k: loop.get(k)
            for k in (
                "complete", "stop_reason", "playbook", "nudges", "missing",
                "proof_frames", "proof_gap", "kind",
                "mentor_source", "mentor_llm_calls", "mentor_next",
                "mentor_skip", "mentor_model", "mentor_reason",
            )
        },
        "project": snap,
        "quality": quality,
        "assist_quality": assist_q,
        "screenshots": screenshots,
        "files": [f["path"] for f in snap.get("files") or []],
    }


def _summary(cell: dict) -> dict:
    aq = cell.get("assist_quality") or {}
    loop = cell.get("build_loop") or {}
    return {
        "assist": cell.get("assist"),
        "model": cell.get("model"),
        "elapsed_s": cell.get("elapsed_s"),
        "complete": cell.get("complete") or loop.get("complete"),
        "stop_reason": loop.get("stop_reason"),
        "mentor_next": loop.get("mentor_next"),
        "mentor_source": loop.get("mentor_source"),
        "kernel_seeded": aq.get("kernel_seeded"),
        "vite_hello": aq.get("vite_hello"),
        "phaser_webgl": aq.get("phaser_webgl"),
        "play_wasd": aq.get("play_wasd"),
        "play_still_stub": aq.get("play_still_stub"),
        "node_modules": aq.get("node_modules"),
        "dist": aq.get("dist"),
        "proof_frames": loop.get("proof_frames"),
        "screenshots": cell.get("screenshots"),
    }


async def main_async(args: argparse.Namespace) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    chosen, probes = await _pick_model(args.model.strip())
    if not chosen:
        print(json.dumps({"error": "no_tool_calling_model", "probes": probes}, ensure_ascii=False, indent=2))
        return 2

    assists = ("off", "kernel-scaffold") if args.pair else (args.assist,)
    shots = Path(args.screenshots)
    cells = []
    for assist in assists:
        data = Path(args.data).resolve()
        if args.pair:
            data = data / assist
        if data.exists() and args.fresh:
            import shutil
            shutil.rmtree(data)
        print(f"\n=== writer assist {assist} / {chosen} ===", flush=True)
        cell = await run_cell(
            assist=assist,
            model=chosen,
            data=data,
            effort=args.effort,
            shots=shots / assist if args.pair else shots,
            mentor=args.mentor_choice,
        )
        cells.append(cell)
        print(json.dumps(_summary(cell), ensure_ascii=False, indent=2), flush=True)

    report = {
        "kind": "writer_assist_sim",
        "probes": probes,
        "chosen": chosen,
        "prompt": EMBER,
        "cells": cells,
        "summaries": [_summary(c) for c in cells],
    }
    out_dir = Path(args.data).resolve() / "live_eval"
    out_dir.mkdir(parents=True, exist_ok=True)
    dest = out_dir / "writer_assist_latest.json"
    dest.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps({
        "chosen": chosen,
        "report": str(dest),
        "summaries": report["summaries"],
    }, ensure_ascii=False, indent=2, default=str))
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Writer assist Off vs Kernel-scaffold sim")
    parser.add_argument("--model", default="", help="Pin a writer. Default: probe CANDIDATES.")
    parser.add_argument(
        "--assist",
        default="off",
        choices=("off", "auto", "kernel-scaffold"),
    )
    parser.add_argument(
        "--pair",
        action="store_true",
        help="Run Off then Kernel-scaffold with the same model.",
    )
    parser.add_argument("--effort", default="craft")
    parser.add_argument("--data", default="/tmp/daedalus-writer-assist-sim")
    parser.add_argument("--screenshots", default=str(_leb.ARTIFACT_SHOTS / "writer-assist"))
    parser.add_argument("--fresh", action="store_true", help="Wipe --data before each cell.")
    parser.add_argument(
        "--mentor-choice",
        default="",
        help="Pin Mentor director (or-glm-53-flash, or-fable-51, rules). Empty = Mentor off.",
    )
    args = parser.parse_args()
    raise SystemExit(asyncio.run(main_async(args)))


if __name__ == "__main__":
    main()
