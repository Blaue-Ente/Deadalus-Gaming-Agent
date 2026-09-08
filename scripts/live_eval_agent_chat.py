#!/usr/bin/env python3
"""Live Daedalus *chat* eval — full agent path, not kernel-only BuildLoop.

Unlike ``scripts/live_eval_build.py`` this run covers:
  Navigator briefing → RAG (Chroma) → skill disclose → native BuildLoop
  → persona turn → NEED_WORKER / NEED_SUBAGENT → review-gate critique → DoD.

Isolated ``$DAEDALUS_DATA`` so the repo playground is not polluted.

  DAEDALUS_STANDALONE=1 python3 scripts/live_eval_agent_chat.py --better
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
import shutil
import sys
import time
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_SCRIPTS))
import live_eval_build as _leb  # noqa: E402

ARTIFACT_SHOTS = _leb.ARTIFACT_SHOTS
BETTER_CANDIDATES = _leb.BETTER_CANDIDATES
CHEAP_CANDIDATES = _leb.CHEAP_CANDIDATES
CANDIDATES = _leb.CANDIDATES
_export_screenshots = _leb._export_screenshots
_pin_openrouter = _leb._pin_openrouter
_project_snapshot = _leb._project_snapshot
probe_tools = _leb.probe_tools

log = logging.getLogger("live_eval_agent_chat")

# Avoid playbook traps: arcade matches 2d/canvas/snake/tetris/pong/змия/аркад;
# mobile matches "app store"/ios/android/expo; epic matches campaign/quest graph.
LUMEN = (
    "Направи нова Playground игра: kind=vite, slug=lumen-bloom, име Lumen Bloom. "
    "Движок Phaser 3 през npm (phaser ^3.80) + Vite vanilla (не React). "
    "src/main.js пуска new Phaser.Game със сцени Boot, Menu, Play. "
    "Геймплей: светещо семе събира падащ звезден прах с клавиши и пойнтер, "
    "избягва сенчести вихри, цъфти при пълна луна. Меню, HUD (прах и живот), "
    "пауза, победа и загуба, частици и screen shake, Web Audio. "
    "Качество като премиум boutique заглавие от 2026: PNG спрайтове чрез "
    "generate_image (семе, прах, вихър, фон, UI), записани в assets/ и "
    "заредени в Phaser — не цветни правоъгълници и не CSS градиент вместо спрайт. "
    "Ред: playground_new({kind:\"vite\", slug:\"lumen-bloom\"}) → generate_image "
    "→ пиши сцените → npm install и npm run build → playtest → browser. "
    "Ако можеш, извикай NEED_WORKER reviewer за визуален преглед. "
    "Не прави Godot. Не прави Expo."
)

# Long GDD for Mentor director evals. Keep "kind=vite" + Phaser 3 + Vite
# close together so the kernel matches playground.vite (not generic/static).
GLASS = (
    "Направи нова Playground игра: kind=vite, slug=glass-orchard, име Glass Orchard. "
    "Движок Phaser 3 през npm (phaser ^3.80) заедно с Vite vanilla (не React, не Vue). "
    "Първо playground_new({kind:\"vite\", slug:\"glass-orchard\", name:\"Glass Orchard\"}). "
    "После overwrite на src/main.js — Vite Hello/#app броячът е празен екран, забранен е. "
    "new Phaser.Game в src/main.js със scale.FIT, pixelArt true, фон #0b1020, "
    "сцени Boot, Menu, Grove, Dusk, Result. index.html държи #game mount, не #app. "
    "package.json има зависимости vite и phaser. "
    "\n\n"
    "Светът: терасирана стъклена градина над мъгла. Играчът е пазител на фенер — "
    "малка фигура с топъл конус светлина. Целта на среза е една вечерна беритба, "
    "не отворен свят. Събираш узрели стъклени плода (cyan / amber / violet), "
    "отрязваш blight motes преди да заразят клон, и прибираш реколтата преди "
    "луната да залезе. Тон: тих, скъп boutique title от 2026 — меко светене, "
    "без мем-UI, без placeholder геометрични петна вместо герой. "
    "\n\n"
    "Управление: стрелки или WASD за ходене по терасите; пойнтер / тъч влачи "
    "фенерния конус; Space или клик бере плод в обхват; Shift подрязва blight. "
    "Esc пауза с Resume / Restart. Menu: заглавие Glass Orchard, подзаглавие "
    "„One harvest before moonset“, бутон Begin harvest (клавиш Enter). "
    "HUD винаги: Harvest (0–12), Lantern heat (бар, който пада на студ и се "
    "пълни край brazier), Moon timer (90s), малък компас към следващия зрял плод. "
    "Grove е учебната тераса (3 плода, 1 blight, без таймер-fail). Dusk е "
    "истинската среза: 12 плода на три тераси, 4 blight motes, brazier в средата. "
    "Победа: 12/12 преди timer=0 → Result „Orchard held“. "
    "Загуба: timer=0 или heat=0 → Result „Frost took the grove“ с Restart. "
    "Частици при беритба, screen shake при blight hit, Web Audio: мек дрон, "
    "chime при плод, нисък thud при hit. "
    "\n\n"
    "Арт: PNG през generate_image, записани в assets/ и load.image в Boot — "
    "keeper (32x48, side view), lantern cone glow, glass fruit x3 hues, "
    "blight mote, terrace background, UI panel. Не цветни правоъгълници, "
    "не CSS градиент вместо спрайт. Ако generate_image падне два пъти с еднаква "
    "грешка, спри да я викаш и направи процедурни Phaser текстури в Boot "
    "(fill + glow), после продължи към сцените. "
    "\n\n"
    "Файлове (задължителни): src/main.js, src/scenes/Boot.js, Menu.js, Grove.js, "
    "Dusk.js, Result.js, src/systems/Lantern.js, Harvest.js, src/ui/Hud.js, "
    "assets/*.png. Всяка сцена е отделен файл. Не слагай цялата игра в main.js. "
    "\n\n"
    "Ред: playground_new kind=vite → generate_image за ключовите спрайтове → "
    "пиши Boot така че да стартира Phaser.Game (не Vite scaffold) → Menu/Grove/"
    "Dusk/Result → run_command command=\"npm install\" (без | и &&) → "
    "run_command command=\"npm run build\" → playtest → browser. "
    "production({action:\"pipeline\", slug:\"glass-orchard\"}) ако е в tool subset. "
    "Ако можеш, NEED_WORKER reviewer за визуален преглед на proof кадъра. "
    "Не прави Godot. Не прави Expo."
)

PROMPTS = {"lumen": LUMEN, "glass": GLASS}
MENTOR_CHOICES = ("or-glm-53", "or-glm-53-flash", "or-astra", "or-fable-51", "or-grok-46", "or-kimi-k3", "rules")

_RECT_RE = re.compile(
    r"fillRect|fillStyle\s*=|background:\s*linear-gradient|ctx\.fillRect|"
    r"graphics\.fill|Phaser\.Geom\.Rectangle|makeRectangle",
    re.I,
)
_SPRITE_RE = re.compile(
    r"load\.image|add\.image|this\.add\.sprite|textures\.add|"
    r"new Image\(|drawImage",
    re.I,
)


def _isolate_env(data: Path) -> None:
    os.environ["DAEDALUS_STANDALONE"] = "1"
    os.environ["DAEDALUS_DATA"] = str(data)
    os.environ["DAEDALUS_PLAYGROUND"] = str(data / "projects")
    os.environ.setdefault("DAEDALUS_NATIVE_TOOLS", "1")
    os.environ.setdefault("DAEDALUS_VISUAL_PROOF", "1")
    os.environ.setdefault("DAEDALUS_WRITE_GATE", "1")
    os.environ.setdefault("DAEDALUS_NAVIGATOR_ENABLED", "1")
    data.mkdir(parents=True, exist_ok=True)
    (data / "projects").mkdir(parents=True, exist_ok=True)


def _reset_path_caches() -> None:
    from daedalus.paths import data_root, playground_projects_dir, product_root, repo_root
    data_root.cache_clear()
    playground_projects_dir.cache_clear()
    product_root.cache_clear()
    repo_root.cache_clear()
    from daedalus.store import reset_sqlite_pool
    reset_sqlite_pool()
    from daedalus import playground as pg
    pg.PROJECTS_DIR = playground_projects_dir()
    playground_projects_dir().mkdir(parents=True, exist_ok=True)


def _pin_mentor(choice_id: str) -> dict:
    """Lock Mentor on and pin the director. Isolated $DAEDALUS_DATA must be set."""
    os.environ["DAEDALUS_MENTOR"] = "1"
    os.environ["DAEDALUS_MENTOR_LLM"] = "0" if choice_id == "rules" else "1"
    os.environ.setdefault("DAEDALUS_MENTOR_TIMEOUT", "30")
    os.environ.setdefault("DAEDALUS_MENTOR_DAY_CAP", "80000")
    from daedalus import mentor, mentor_helm

    if choice_id == "rules":
        os.environ.pop("DAEDALUS_MENTOR_MODEL", None)
        os.environ.pop("DAEDALUS_MENTOR_PROVIDER", None)
        mentor_helm.set_choice({"choice_id": "rules"})
    else:
        row = mentor_helm.find_row(choice_id)
        if not row or not row.get("model"):
            raise SystemExit(f"unknown mentor choice_id: {choice_id}")
        os.environ["DAEDALUS_MENTOR_PROVIDER"] = str(row["provider"])
        os.environ["DAEDALUS_MENTOR_MODEL"] = str(row["model"])
    status = mentor.status()
    llm = status.get("llm") or {}
    choice = llm.get("choice") or {}
    print(
        f"mentor on={status.get('enabled')} choice={choice.get('id')} "
        f"model={choice.get('model')} timeout={llm.get('timeout_s')}s "
        f"warn={choice.get('warn') or ''}",
        flush=True,
    )
    return {
        "enabled": status.get("enabled"),
        "choice": choice,
        "spend": llm.get("spend"),
        "timeout_s": llm.get("timeout_s"),
        "warn": choice.get("warn"),
    }


def _mentor_snapshot() -> dict:
    from daedalus import mentor

    status = mentor.status()
    llm = status.get("llm") or {}
    choice = llm.get("choice") or {}
    return {
        "enabled": status.get("enabled"),
        "choice_id": choice.get("id"),
        "model": choice.get("model"),
        "provider": choice.get("provider"),
        "source": choice.get("source"),
        "warn": choice.get("warn"),
        "spend": llm.get("spend"),
        "timeout_s": llm.get("timeout_s"),
    }


def _preflight_route(prompt: str) -> dict:
    from daedalus.tool_router import route
    rt = route(prompt, session_id="playground:live-eval-chat")
    return rt.as_dict()


def _tool_names(calls: list[dict]) -> list[str]:
    return [str(c.get("tool") or c.get("name") or "") for c in (calls or [])]


def _inspect_quality(slug: str) -> dict:
    from daedalus import playground as pg
    root = pg.project_dir(slug) if slug else None
    if not root or not root.is_dir():
        return {"ok": False, "reason": "no_project"}
    files = [p for p in root.rglob("*") if p.is_file() and ".git" not in p.parts]
    js = []
    png = []
    for p in files:
        rel = str(p.relative_to(root))
        if p.suffix.lower() in {".js", ".ts", ".jsx", ".tsx", ".html", ".css"}:
            try:
                js.append((rel, p.read_text(encoding="utf-8", errors="replace")))
            except Exception:
                pass
        if p.suffix.lower() == ".png" and "studio/proof" not in rel.replace("\\", "/"):
            png.append({"path": rel, "bytes": p.stat().st_size})
    sprite_png = [p for p in png if "font" not in p["path"].lower()]
    blob = "\n".join(t for _, t in js)
    pkg = root / "package.json"
    pkg_txt = pkg.read_text(encoding="utf-8") if pkg.is_file() else ""
    has_phaser = "phaser" in pkg_txt.lower() or "from 'phaser'" in blob or 'from "phaser"' in blob
    has_vite = (root / "vite.config.js").is_file() or '"vite"' in pkg_txt
    dist = (root / "dist" / "index.html").is_file()
    rect_hits = _RECT_RE.findall(blob)[:12]
    sprite_hits = _SPRITE_RE.findall(blob)[:12]
    boutique = bool(sprite_png) and bool(sprite_hits) and has_phaser and has_vite
    rectangles_only = bool(rect_hits) and not sprite_png and not sprite_hits
    return {
        "root": str(root),
        "kind_guess": "vite" if has_vite else "static",
        "has_package_json": pkg.is_file(),
        "has_phaser": has_phaser,
        "has_vite": has_vite,
        "has_dist": dist,
        "png_assets": png,
        "png_count": len(png),
        "sprite_png_count": len(sprite_png),
        "sprite_api_hits": sprite_hits,
        "rectangle_hits": rect_hits,
        "boutique_heuristic": boutique,
        "rectangles_only": rectangles_only,
        "src_files": [rel for rel, _ in js],
    }


def _knowledge_titles(ctx: dict) -> dict:
    nav = ctx.get("navigator") or {}
    return {
        "knowledge_used": ctx.get("knowledge_used"),
        "past_memory_used": ctx.get("past_memory_used"),
        "similar_artifacts": ctx.get("similar_artifacts"),
        "lessons_used": ctx.get("lessons_used"),
        "episodes_used": ctx.get("episodes_used"),
        "navigator_intent": nav.get("intent") if isinstance(nav, dict) else None,
        "navigator_hint": (nav.get("hint") if isinstance(nav, dict) else None),
        "navigator_tools": (nav.get("tools") if isinstance(nav, dict) else None),
        "mem0": ctx.get("mem0"),
    }


async def run_chat(prompt: str, effort: str) -> dict:
    from daedalus.agent import Daedalus

    t0 = time.time()
    agent = Daedalus()
    knowledge = await agent.ensure_knowledge_loaded()
    t_know = time.time()
    result = await agent.chat(
        prompt,
        session_id="playground:live-eval-chat",
        effort=effort,
    )
    for task in list(getattr(agent, "_background_tasks", ()) or ()):
        task.cancel()
    ctx = result.get("context") or {}
    calls = ctx.get("tool_calls") or []
    names = _tool_names(calls)
    return {
        "elapsed_s": round(time.time() - t0, 2),
        "knowledge_load_s": round(t_know - t0, 2),
        "knowledge_index": knowledge,
        "session_id": result.get("session_id"),
        "model": result.get("model"),
        "effort": result.get("effort"),
        "complete": result.get("complete"),
        "dod": result.get("dod"),
        "reply": (result.get("reply") or "")[:4000],
        "reply_len": len(result.get("reply") or ""),
        "rag": _knowledge_titles(ctx),
        "tool_calls": calls,
        "tool_names": names,
        "workers_emitted": any(n == "workers" for n in names),
        "subagents_emitted": any(n == "subagent" for n in names),
        "playtest_called": any(n == "playtest" for n in names),
        "generate_image_called": any(n == "generate_image" for n in names),
        "production_called": any(n == "production" for n in names),
        "skill_called": any(n == "skill" for n in names),
        "browser_or_proof": any(n in {"browser", "visual_proof", "engine"} for n in names),
        "build_loop": next((c for c in calls if c.get("tool") == "build_loop"), None),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Live Daedalus chat eval (RAG + workers)")
    parser.add_argument("--model", default="")
    parser.add_argument("--preset", choices=sorted(PROMPTS), default="lumen")
    parser.add_argument("--prompt", default="")
    parser.add_argument("--better", action="store_true")
    parser.add_argument(
        "--cheap",
        action="store_true",
        help="Probe MiniMax M3 / DeepSeek V4 / GLM 5.2 / Poolside — never Claude.",
    )
    parser.add_argument("--effort", default="craft")
    parser.add_argument("--screenshots", default=str(ARTIFACT_SHOTS))
    parser.add_argument("--data", default="/tmp/daedalus-live-eval-chat")
    parser.add_argument(
        "--mentor-choice",
        default="",
        help="Pin Mentor director: " + ", ".join(MENTOR_CHOICES),
    )
    args = parser.parse_args()
    prompt = args.prompt or PROMPTS[args.preset]

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    data = Path(args.data).resolve()
    _isolate_env(data)

    async def _go() -> int:
        probes = []
        chosen = args.model.strip() if args.model else ""
        pool = CHEAP_CANDIDATES if args.cheap else (BETTER_CANDIDATES if args.better else CANDIDATES)
        if chosen:
            row = await probe_tools(chosen)
            probes.append(row)
            if not row.get("ok"):
                print(json.dumps({"error": "probe_failed", "probes": probes}, ensure_ascii=False, indent=2))
                return 2
        else:
            for mid in pool:
                try:
                    row = await probe_tools(mid)
                except Exception as exc:
                    row = {"model": mid, "ok": False, "error": str(exc)[:400]}
                probes.append(row)
                print(
                    f"probe {mid}: ok={row.get('ok')} tools={row.get('tools')} ms={row.get('ms')}",
                    flush=True,
                )
                if row.get("ok"):
                    chosen = mid
                    break
            if not chosen:
                print(json.dumps({"error": "no_tool_calling_model", "probes": probes}, ensure_ascii=False, indent=2))
                return 2

        _pin_openrouter(chosen)
        _isolate_env(data)
        _reset_path_caches()
        mentor_pin = None
        if args.mentor_choice.strip():
            mentor_pin = _pin_mentor(args.mentor_choice.strip())

        playbook = _preflight_route(prompt)
        print(f"playbook={playbook.get('playbook')} kind={playbook.get('kind')} tools={playbook.get('tools')}", flush=True)

        capture: list[str] = []

        class _H(logging.Handler):
            def emit(self, record: logging.LogRecord) -> None:
                msg = record.getMessage()
                if any(
                    k in msg.lower()
                    for k in (
                        "review-gate", "skills disclosed", "need_worker",
                        "navigator", "native-tools", "build loop", "worker",
                        "knowledge", "mentor", "helm", "director",
                    )
                ):
                    capture.append(f"{record.name}: {msg[:400]}")

        logging.getLogger().addHandler(_H())

        print(f"chat model={chosen} effort={args.effort} …", flush=True)
        chat = await run_chat(prompt, args.effort)

        slug = ""
        loop_row = chat.get("build_loop") or {}
        slug = str(loop_row.get("slug") or "")
        if not slug:
            for c in chat.get("tool_calls") or []:
                s = str(c.get("slug") or "")
                if s:
                    slug = s
                    break
        if not slug:
            # Native tool results may only live on disk.
            from daedalus.paths import playground_projects_dir
            roots = list(playground_projects_dir().iterdir()) if playground_projects_dir().is_dir() else []
            dirs = [p.name for p in roots if p.is_dir()]
            if len(dirs) == 1:
                slug = dirs[0]
            elif "lumen-bloom" in dirs:
                slug = "lumen-bloom"
            elif "glass-orchard" in dirs:
                slug = "glass-orchard"

        snap = _project_snapshot(slug)
        quality = _inspect_quality(slug)
        shots_dir = Path(args.screenshots)
        try:
            shots = _export_screenshots(slug, shots_dir)
        except Exception as exc:
            shots = []
            log.warning("screenshot export failed: %s", exc)

        mentor_after = _mentor_snapshot()
        report = {
            "kind": "agent_chat",
            "probes": probes,
            "chosen": chosen,
            "prompt": prompt,
            "mentor_pin": mentor_pin,
            "mentor": mentor_after,
            "playbook_preflight": playbook,
            "chat": chat,
            "slug": slug,
            "project": snap,
            "quality": quality,
            "screenshots": shots,
            "log_hits": capture[-80:],
        }
        out_dir = Path(os.environ["DAEDALUS_DATA"]) / "live_eval"
        out_dir.mkdir(parents=True, exist_ok=True)
        dest = out_dir / "agent_chat_latest.json"
        dest.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
        print(json.dumps({
            "chosen": chosen,
            "playbook": playbook.get("playbook"),
            "route_kind": playbook.get("kind"),
            "slug": slug,
            "complete": chat.get("complete"),
            "effort": chat.get("effort"),
            "elapsed_s": chat.get("elapsed_s"),
            "rag": chat.get("rag"),
            "workers_emitted": chat.get("workers_emitted"),
            "subagents_emitted": chat.get("subagents_emitted"),
            "playtest_called": chat.get("playtest_called"),
            "generate_image_called": chat.get("generate_image_called"),
            "production_called": chat.get("production_called"),
            "browser_or_proof": chat.get("browser_or_proof"),
            "tool_names": chat.get("tool_names"),
            "files": [f["path"] for f in snap.get("files") or []],
            "quality": {
                "has_vite": quality.get("has_vite"),
                "has_phaser": quality.get("has_phaser"),
                "png_count": quality.get("png_count"),
                "boutique_heuristic": quality.get("boutique_heuristic"),
                "rectangles_only": quality.get("rectangles_only"),
            },
            "mentor": mentor_after,
            "build_loop": {
                k: (chat.get("build_loop") or {}).get(k)
                for k in (
                    "complete", "stop_reason", "playbook", "nudges",
                    "missing", "proof_frames", "proof_gap",
                    "mentor_source", "mentor_llm_calls", "mentor_next",
                    "mentor_skip", "mentor_model", "mentor_reason",
                )
            },
            "screenshots": shots,
            "report": str(dest),
        }, ensure_ascii=False, indent=2, default=str))
        boutique = bool(quality.get("boutique_heuristic"))
        wrote = bool(snap.get("files"))
        return 0 if wrote and (boutique or chat.get("browser_or_proof")) else 1

    raise SystemExit(asyncio.run(_go()))


if __name__ == "__main__":
    main()
