#!/usr/bin/env python3
"""Live Playground build against OpenRouter.

Probes tool-calling first, then runs the kernel native build loop on a slice
that is not Snake. Writes a JSON report under $DAEDALUS_DATA and copies
Chromium proof frames to --screenshots (default: /opt/cursor/artifacts/screenshots).

  DAEDALUS_STANDALONE=1 python3 scripts/live_eval_build.py --preset slerp
"""
from __future__ import annotations

import argparse
import asyncio
import concurrent.futures
import json
import logging
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

CANDIDATES = (
    "minimax/minimax-m2.7",
    "deepseek/deepseek-v3.2",
)

# Stronger than MiniMax M2.7, still inside a $2–3 Playground budget.
BETTER_CANDIDATES = (
    "anthropic/claude-sonnet-4.6",
    "anthropic/claude-sonnet-4.5",
    "z-ai/glm-5.1",
    "moonshotai/kimi-k2.6",
)

_RATES_USD_PER_1M = {
    "anthropic/claude-sonnet-4.6": (3.0, 15.0),
    "anthropic/claude-sonnet-4.5": (3.0, 15.0),
    "z-ai/glm-5.1": (0.98, 3.08),
    "moonshotai/kimi-k2.6": (0.73, 3.49),
    "minimax/minimax-m2.7": (0.28, 1.20),
    "deepseek/deepseek-v3.2": (0.27, 1.10),
}

DUNGEON = (
    "Направи нова Playground игра: top-down dungeon crawler на HTML canvas "
    "(kind=static, slug dungeon-keys). Един файл index.html — canvas 2D, без CDN. "
    "Това е ИГРА, не greybox demo. "
    "Три ВИЗУАЛНО РАЗЛИЧНИ стаи (различен цвят на пода: кафяв / зелен / лилав), "
    "свързани с врати. Играчът се вижда като цветно квадратче. "
    "В стая 1: златен ключ на пода. Последната врата е заключена докато няма ключ. "
    "Един slime с HP в стая 2 (не в стая 1). Стрелки = движение, Space = атака. "
    "HP лента, KEY, ROOM в HUD горе — но ЦЕНТЪРЪТ на screenshot трябва да е плочките "
    "на стаята, не само текст. Game over при 0 HP, победа при изхода след ключа. "
    "JS само ASCII минус: -1. "
    "Playable loop: input → update → render → win/lose. Не прави змия, tetris или pong."
)

SLERP = (
    "Направи нова Playground 3D среза (kind=static, slug slerp-lookat). "
    "Само raw WebGL1 в index.html — без Three.js, без CDN, без vite. "
    "Продуктът е геометрия в центъра на Chromium screenshot, не HUD. "
    "На първия кадър (t=0, преди SLERP) нарисувай: под (12x12, тъмно зелено) "
    "и три кутии около origin — червена (0,0.5,0), лайм (-2.2,0.5,0), "
    "златна (2.2,0.5,0). Камера eye=(0,5,12) look-at origin, up=(0,1,0). "
    "clearColor navy е ОК; центърът на кадъра трябва да е цветните кутии. "
    "WebGL1: uniformMatrix4fv(loc, false, Float32Array) — никога transpose=true. "
    "Клавиши 1/2/3 сменят целта; камерата прави quaternion SLERP към целта. "
    "Извикай math3d с claimed числа (quat_slerp и look_at) преди JS формулите. "
    "За евтин чеклист можеш <NEED_WORKER: fast | WebGL1 traps>, но файловете "
    "се пишат само с write_file. JS само ASCII минус: -1, никога unicode − . "
    "Не прави змия, tetris или pong."
)

CHEAP_WORKER = "minimax/minimax-m2.7"
CHEAP_SLOTS = ("fast", "balanced", "deep", "coder", "planner", "reviewer")

PROMPTS = {"dungeon": DUNGEON, "slerp": SLERP}

ARTIFACT_SHOTS = Path("/opt/cursor/artifacts/screenshots")


def _pin_openrouter(model: str) -> None:
    key = (os.environ.get("OPENROUTER_API_KEY") or "").strip()
    if not key:
        raise SystemExit("OPENROUTER_API_KEY is missing")
    os.environ["DAEDALUS_STANDALONE"] = "1"
    os.environ["DAEDALUS_PROVIDER"] = "openrouter"
    os.environ["DAEDALUS_MODEL"] = model
    os.environ["DAEDALUS_API_KEY"] = key
    os.environ["DAEDALUS_BASE_URL"] = "https://openrouter.ai/api/v1"
    os.environ.setdefault("DAEDALUS_NATIVE_TOOLS", "1")
    os.environ.setdefault("DAEDALUS_VISUAL_PROOF", "1")
    os.environ.setdefault("DAEDALUS_WRITE_GATE", "1")
    os.environ["DAEDALUS_WRITE_REVIEW"] = "1"
    os.environ["DAEDALUS_WORKERS"] = "1"
    os.environ["DAEDALUS_RAG"] = "1"
    os.environ.setdefault("DAEDALUS_LAB_BACKEND", "local")


def _client():
    from openai import AsyncOpenAI
    from daedalus.llm_config import openai_compat_extra_body, openai_compat_headers

    key = os.environ["OPENROUTER_API_KEY"]
    extra = openai_compat_headers("openrouter")
    return AsyncOpenAI(
        api_key=key,
        base_url="https://openrouter.ai/api/v1",
        max_retries=1,
        default_headers=extra or None,
    ), openai_compat_extra_body("openrouter", with_tools=True)


async def probe_tools(model: str) -> dict:
    client, extra_body = _client()
    t0 = time.time()
    resp = await client.chat.completions.create(
        model=model,
        max_tokens=256,
        messages=[{
            "role": "user",
            "content": (
                "Call playground_new with name=probe-tools and kind=static. "
                "Do not write a game. Tool call only."
            ),
        }],
        tools=[{
            "type": "function",
            "function": {
                "name": "playground_new",
                "description": "Create a Playground project.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "kind": {"type": "string"},
                    },
                    "required": ["name"],
                },
            },
        }],
        tool_choice="auto",
        extra_body=extra_body or None,
    )
    msg = resp.choices[0].message if resp.choices else None
    calls = list(getattr(msg, "tool_calls", None) or []) if msg else []
    usage = getattr(resp, "usage", None)
    return {
        "model": model,
        "ok": bool(calls),
        "tools": [getattr(getattr(c, "function", None), "name", "") for c in calls],
        "text_head": ((getattr(msg, "content", None) or "") or "")[:240],
        "prompt_tokens": getattr(usage, "prompt_tokens", None) if usage else None,
        "completion_tokens": getattr(usage, "completion_tokens", None) if usage else None,
        "ms": int((time.time() - t0) * 1000),
    }


def _pin_cheap_workers(data_dir: Path) -> dict:
    """Master is --model. Workers stay MiniMax — not a second Sonnet bill."""
    cfg = {
        slot: {
            "model": CHEAP_WORKER,
            "provider": "openrouter",
            "enabled": True,
            "base_url": "https://openrouter.ai/api/v1",
        }
        for slot in CHEAP_SLOTS
    }
    dest = data_dir / "worker_config.json"
    dest.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
    return cfg


def _trim_workers(rows: list | None) -> list[dict]:
    out = []
    for r in rows or []:
        out.append({
            "slot": r.get("slot"),
            "model": r.get("model"),
            "ok": r.get("ok"),
            "task_preview": str(r.get("task_preview") or "")[:160],
            "result_head": str(r.get("result") or "")[:700],
        })
    return out


def _knowledge_report(rt, extra: str, prompt: str = "") -> dict:
    chroma: dict
    try:
        import chromadb  # noqa: F401
        chroma = {"import": True, "version": getattr(chromadb, "__version__", ""), "error": ""}
    except Exception as exc:
        chroma = {"import": False, "version": "", "error": str(exc)[:200]}
    hits: list[dict] = []
    try:
        from daedalus.tool_router import retrieve_rag

        for h in retrieve_rag(prompt or "HTML canvas dungeon crawler"):
            meta = h.get("metadata") or {}
            hits.append({
                "id": h.get("id"),
                "title": meta.get("title"),
                "category": meta.get("category"),
                "distance": h.get("distance"),
            })
    except Exception as exc:
        chroma["error"] = (chroma.get("error") or "") or str(exc)[:200]
    docs: list[str] = []
    auto: list[str] = []
    for s in rt.ordered_steps:
        docs.extend(s.docs or ())
        if s.auto and s.tool:
            auto.append(s.tool)
    return {
        "chroma": chroma,
        "hits": hits,
        "consult_auto": auto,
        "plan_docs": list(dict.fromkeys(docs)),
        "extra_has_glm": "glm" in (extra or "").lower(),
        "extra_head": (extra or "")[:1400],
        "note": (
            "Semantic RAG (Chroma daedalus_global) is injected into the native "
            "system prompt when DAEDALUS_RAG!=0 and chromadb is installed. "
            "PLAN DOCS + AUTO consult still run."
        ),
    }


def _ensure_local_bin_on_path() -> str:
    local_bin = str(Path.home() / ".local" / "bin")
    path = os.environ.get("PATH") or ""
    if local_bin not in path.split(":"):
        os.environ["PATH"] = f"{local_bin}:{path}" if path else local_bin
    return os.environ["PATH"]


def _smolvm_doctor() -> dict:
    exe = shutil.which("smolvm") or ""
    if not exe:
        fallback = Path.home() / ".local" / "bin" / "smolvm"
        exe = str(fallback) if fallback.is_file() else ""
    if not exe:
        return {"ok": False, "error": "smolvm_bin_not_on_path"}
    try:
        proc = subprocess.run(
            [exe, "doctor"],
            capture_output=True,
            text=True,
            timeout=25,
        )
        text = ((proc.stdout or "") + "\n" + (proc.stderr or "")).strip()
        return {
            "ok": proc.returncode == 0,
            "bin": exe,
            "exit": proc.returncode,
            "head": text[-1500:],
        }
    except Exception as exc:
        return {"ok": False, "bin": exe, "error": str(exc)[:200]}


def _try_smolvm_guest(slug: str, timeout_s: float = 45.0) -> dict:
    """Best-effort guest boot. Must not fail the game if the image download hangs."""
    try:
        from daedalus.computer_smolvm import probe as smol_probe, start_lab_browser
        from daedalus import playground as pg
    except Exception as exc:
        return {"tried": False, "ok": False, "error": str(exc)[:200]}
    probe = smol_probe()
    if not probe.get("ok"):
        return {"tried": False, "ok": False, "probe": probe, "error": "smolvm_not_ready"}
    root = pg.project_dir(slug)
    if not root.is_dir():
        return {"tried": False, "ok": False, "error": "project_missing"}

    def _boot() -> dict:
        return start_lab_browser(
            f"eval-{slug}",
            serve_url="",
            mount=root,
            playable="index.html",
            live=False,
        )

    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            fut = pool.submit(_boot)
            row = fut.result(timeout=timeout_s)
        return {
            "tried": True,
            "ok": bool(row.get("ok")),
            "viewer_url": row.get("viewer_url") or "",
            "cdp_url": row.get("cdp_url") or "",
            "guest_url": row.get("guest_url") or "",
            "warning": row.get("warning") or "",
        }
    except concurrent.futures.TimeoutError:
        return {"tried": True, "ok": False, "error": f"timeout_{int(timeout_s)}s"}
    except Exception as exc:
        return {"tried": True, "ok": False, "error": str(exc)[:400]}


def _warm_chroma(query: str) -> dict:
    from daedalus.memory import DaedalusMemory

    DaedalusMemory._instance = None
    try:
        import chromadb
        version = str(getattr(chromadb, "__version__", "") or "")
    except Exception as exc:
        return {"ok": False, "import": False, "error": str(exc)[:300]}
    try:
        from daedalus.knowledge import DaedalusKnowledge

        mem = DaedalusMemory()
        index = DaedalusKnowledge(memory=mem).load_all(force=False)
        hits = mem.retrieve_knowledge(query, top_k=4)
        return {
            "ok": True,
            "import": True,
            "version": version,
            "chunks": mem.global_col.count(),
            "index": {
                "loaded": index.get("loaded"),
                "skipped": index.get("skipped"),
                "errors": index.get("errors"),
            } if isinstance(index, dict) else index,
            "hits": [
                {
                    "id": h.get("id"),
                    "title": (h.get("metadata") or {}).get("title"),
                    "category": (h.get("metadata") or {}).get("category"),
                    "distance": h.get("distance"),
                }
                for h in hits
            ],
        }
    except Exception as exc:
        return {"ok": False, "import": True, "version": version, "error": str(exc)[:400]}


def _host_report() -> dict:
    try:
        from daedalus.computer import probe
        row = probe()
    except Exception as exc:
        row = {"error": str(exc)[:200]}
    row["doctor"] = _smolvm_doctor()
    row["path_has_local_bin"] = str(Path.home() / ".local" / "bin") in (os.environ.get("PATH") or "")
    try:
        import chromadb
        row["chromadb"] = {
            "import": True,
            "version": getattr(chromadb, "__version__", ""),
        }
    except Exception as exc:
        row["chromadb"] = {"import": False, "error": str(exc)[:200]}
    try:
        import smolvm
        row["smolvm_pkg"] = {
            "import": True,
            "version": getattr(smolvm, "__version__", ""),
        }
    except Exception as exc:
        row["smolvm_pkg"] = {"import": False, "error": str(exc)[:200]}
    return row


async def probe_worker() -> dict:
    from daedalus.workers import call_workers_parallel, get_worker_config

    cfg = get_worker_config()
    fast = (cfg.get("fast") or {})
    t0 = time.time()
    try:
        rows = await call_workers_parallel([
            {"slot": "fast", "task": "Reply with the single word PONG. Nothing else."},
        ])
    except Exception as exc:
        return {
            "ok": False,
            "error": str(exc)[:400],
            "configured_model": fast.get("model"),
            "ms": int((time.time() - t0) * 1000),
        }
    row = rows[0] if rows else {}
    return {
        "ok": bool(row.get("ok")),
        "slot": row.get("slot"),
        "model": row.get("model") or fast.get("model"),
        "result_head": str(row.get("result") or "")[:240],
        "ms": int((time.time() - t0) * 1000),
        "configured_model": fast.get("model"),
        "source": fast.get("source"),
    }


async def _shot_url(url: str, dest: Path) -> dict:
    from daedalus.playground_browser import _find_chrome

    chrome = _find_chrome()
    if not chrome:
        return {"ok": False, "error": "chromium_not_found"}
    try:
        from playwright.async_api import async_playwright
    except Exception as exc:
        return {"ok": False, "error": f"playwright: {exc}"[:200]}
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path("/tmp") / f"daedalus-lab-{dest.name}"
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True, executable_path=chrome,
                args=[
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--ignore-gpu-blocklist",
                    "--enable-webgl",
                    "--enable-unsafe-swiftshader",
                    "--use-gl=angle",
                    "--use-angle=swiftshader",
                ],
            )
            page = await browser.new_page(viewport={"width": 1280, "height": 800})
            await page.goto(url, wait_until="domcontentloaded", timeout=25000)
            await page.wait_for_timeout(2800)
            await page.screenshot(path=str(tmp), full_page=False)
            await browser.close()
        try:
            shutil.copy2(tmp, dest)
            out = dest
        except OSError:
            out = tmp
        return {"ok": out.is_file(), "path": str(out), "bytes": out.stat().st_size if out.is_file() else 0}
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:300]}


async def _playtest_url(url: str, dest: Path) -> dict:
    """Arrow keys + Space — prove the loop is more than a still HUD."""
    from daedalus.playground_browser import _find_chrome

    chrome = _find_chrome()
    if not chrome:
        return {"ok": False, "error": "chromium_not_found"}
    try:
        from playwright.async_api import async_playwright
    except Exception as exc:
        return {"ok": False, "error": f"playwright: {exc}"[:200]}
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path("/tmp") / f"daedalus-play-{dest.name}"
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True, executable_path=chrome,
                args=[
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--ignore-gpu-blocklist",
                    "--enable-webgl",
                    "--enable-unsafe-swiftshader",
                    "--use-gl=angle",
                    "--use-angle=swiftshader",
                ],
            )
            page = await browser.new_page(viewport={"width": 1280, "height": 800})
            await page.goto(url, wait_until="domcontentloaded", timeout=25000)
            await page.wait_for_timeout(400)
            await page.mouse.click(640, 400)
            await page.keyboard.down("ArrowRight")
            await page.wait_for_timeout(1400)
            await page.keyboard.up("ArrowRight")
            await page.keyboard.press("Space")
            await page.wait_for_timeout(200)
            await page.keyboard.down("ArrowRight")
            await page.wait_for_timeout(800)
            await page.keyboard.up("ArrowRight")
            await page.keyboard.press("ArrowDown")
            await page.wait_for_timeout(200)
            await page.screenshot(path=str(tmp), full_page=False)
            await browser.close()
        try:
            shutil.copy2(tmp, dest)
            out = dest
        except OSError:
            out = tmp
        return {"ok": out.is_file(), "path": str(out), "bytes": out.stat().st_size if out.is_file() else 0}
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:300]}


def _open_lab(slug: str) -> dict:
    from daedalus.computer import open_project_computer

    try:
        return open_project_computer(slug)
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:300]}


def _estimate_usd(model: str, prompt_tokens: int, completion_tokens: int) -> float | None:
    rates = _RATES_USD_PER_1M.get(model)
    if not rates:
        return None
    cin, cout = rates
    return (prompt_tokens / 1_000_000.0) * cin + (completion_tokens / 1_000_000.0) * cout


async def run_build(prompt: str, max_rounds: int) -> dict:
    from daedalus.build_loop import BuildLoop
    from daedalus.llm_client import call_with_tools
    from daedalus.llm_config import get_config
    from daedalus.native_tools import run_native_discovery_pass
    from daedalus.tool_router import format_system, native_specs, route, run_consult

    usage_acc = {"prompt_tokens": 0, "completion_tokens": 0, "rounds": 0}

    async def tracked_call(system, messages, tools, **kwargs):
        row = await call_with_tools(system, messages, tools, **kwargs)
        usage_acc["rounds"] += 1
        u = row.get("usage") or {}
        usage_acc["prompt_tokens"] += int(u.get("prompt_tokens") or 0)
        usage_acc["completion_tokens"] += int(u.get("completion_tokens") or 0)
        return row

    cfg = get_config()
    rt = route(prompt, session_id="playground:live-eval")
    consult_log, extra = run_consult(rt)
    specs = native_specs(rt)
    system = format_system(rt, extra=extra, user_message=prompt)
    loop = BuildLoop(rt, prompt, seed_log=consult_log)
    t0 = time.time()
    text, tool_log = await run_native_discovery_pass(
        system,
        prompt,
        tracked_call,
        memory=None,
        session_id="playground:live-eval",
        read_file_fn=lambda *a, **k: {},
        max_rounds=max_rounds,
        tools=specs,
        loop=loop,
    )
    row = loop.log_row()
    model = str(cfg.get("model") or "")
    est = _estimate_usd(model, usage_acc["prompt_tokens"], usage_acc["completion_tokens"])
    return {
        "provider": cfg.get("provider"),
        "model": model,
        "playbook": rt.playbook.id,
        "kind": rt.kind,
        "ms": int((time.time() - t0) * 1000),
        "stop_reason": loop.stop_reason,
        "complete": row.get("complete"),
        "missing": row.get("missing"),
        "blockers": row.get("blockers"),
        "proof_gap": loop.proof_gap,
        "proof_frames": list(loop.proof_frames),
        "kernel_proof_ran": loop.kernel_proof_ran,
        "slug": loop.slug,
        "nudges": loop.nudges,
        "workers": row.get("workers") or [],
        "worker_log": _trim_workers(loop.worker_log),
        "knowledge": {
            **_knowledge_report(rt, extra, prompt),
            "rag_in_system": "Chroma daedalus_global" in (system or ""),
        },
        "usage": {**usage_acc, "estimated_usd": est},
        "tool_log": [
            {"tool": r.get("tool"), "ok": r.get("ok"), "blocked": r.get("blocked")}
            for r in (tool_log or [])
        ],
        "result_head": (text or "")[:2500],
    }


def _project_snapshot(slug: str) -> dict:
    if not slug:
        return {"files": []}
    from daedalus import playground as pg
    root = pg.project_dir(slug)
    files = []
    if root.is_dir():
        for p in sorted(root.rglob("*")):
            if p.is_file() and ".git" not in p.parts:
                rel = str(p.relative_to(root))
                files.append({"path": rel, "bytes": p.stat().st_size})
    readme = root / "README.md"
    proof = root / "studio" / "proof"
    extra_shot = root / "assets" / "_verify_screenshot.png"
    return {
        "root": str(root),
        "files": files,
        "readme": readme.read_text(encoding="utf-8")[:1500] if readme.is_file() else "",
        "proof_dir": sorted(x.name for x in proof.iterdir()) if proof.is_dir() else [],
        "verify_screenshot": extra_shot.is_file(),
    }


def _export_screenshots(slug: str, dest: Path, tag: str = "") -> list[str]:
    if not slug:
        return []
    from daedalus import playground as pg
    root = pg.project_dir(slug)
    dest.mkdir(parents=True, exist_ok=True)
    copied: list[str] = []
    candidates: list[Path] = []
    proof = root / "studio" / "proof"
    if proof.is_dir():
        candidates.extend(sorted(proof.glob("*.png")))
    extra = root / "assets" / "_verify_screenshot.png"
    if extra.is_file():
        candidates.append(extra)
    prefix = f"{slug}-{tag}-" if tag else f"{slug}-"
    for src in candidates:
        out = dest / f"{prefix}{src.name}"
        shutil.copy2(src, out)
        copied.append(str(out))
    return copied


async def main_async(args: argparse.Namespace) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    probes = []
    chosen = args.model.strip() if args.model else ""
    pool = BETTER_CANDIDATES if args.better else CANDIDATES
    if chosen:
        row = await probe_tools(chosen)
        probes.append(row)
        if not row["ok"]:
            print(json.dumps({"error": "probe_failed", "probes": probes}, ensure_ascii=False, indent=2))
            return 2
    else:
        for mid in pool:
            try:
                row = await probe_tools(mid)
            except Exception as exc:
                row = {"model": mid, "ok": False, "error": str(exc)[:400]}
            probes.append(row)
            print(f"probe {mid}: ok={row.get('ok')} tools={row.get('tools')} ms={row.get('ms')}", flush=True)
            if row.get("ok"):
                chosen = mid
                break
        if not chosen:
            print(json.dumps({"error": "no_tool_calling_model", "probes": probes}, ensure_ascii=False, indent=2))
            return 2

    _pin_openrouter(chosen)
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
    worker_cfg = _pin_cheap_workers(data_root())
    print(f"workers pinned → {CHEAP_WORKER} slots={list(worker_cfg)}", flush=True)
    print("chroma warmup…", flush=True)
    chroma_warm = _warm_chroma(args.prompt)
    print(
        f"chroma ok={chroma_warm.get('ok')} chunks={chroma_warm.get('chunks')} "
        f"hits={[h.get('title') for h in (chroma_warm.get('hits') or [])]}",
        flush=True,
    )
    worker_probe = await probe_worker()
    print(
        f"worker probe ok={worker_probe.get('ok')} model={worker_probe.get('model')} "
        f"ms={worker_probe.get('ms')}",
        flush=True,
    )
    host = _host_report()
    print(
        f"host chromadb={host.get('chromadb')} smolvm={(host.get('smolvm') or {}).get('ok')} "
        f"doctor={(host.get('doctor') or {}).get('ok')}",
        flush=True,
    )

    print(f"build model={chosen} playbook-probe…", flush=True)
    build = await run_build(args.prompt, args.max_rounds)
    if isinstance(build.get("knowledge"), dict):
        build["knowledge"]["warmup"] = chroma_warm
    snap = _project_snapshot(str(build.get("slug") or ""))
    shots_dir = Path(args.screenshots) if args.screenshots else ARTIFACT_SHOTS
    tag = (args.shot_tag or "").strip()
    try:
        shots = _export_screenshots(str(build.get("slug") or ""), shots_dir, tag=tag)
    except Exception as exc:
        shots = []
        logging.warning("screenshot export failed: %s", exc)

    lab = {}
    lab_shot = {}
    lab_play = {}
    smol_guest = {}
    slug = str(build.get("slug") or "")
    if slug:
        lab = _open_lab(slug)
        url = str((lab or {}).get("url") or "")
        if url:
            lab_png = shots_dir / f"{slug}-{tag + '-' if tag else ''}lab.png"
            lab_shot = await _shot_url(url, lab_png)
            if lab_shot.get("ok"):
                shots.append(str(lab_shot.get("path") or lab_png))
            if "dungeon" in slug.lower() or "dungeon" in (args.prompt or "").lower():
                play_png = shots_dir / f"{slug}-{tag + '-' if tag else ''}play.png"
                lab_play = await _playtest_url(url, play_png)
                if lab_play.get("ok"):
                    shots.append(str(lab_play.get("path") or play_png))
        smol_guest = _try_smolvm_guest(slug)
        print(
            f"smolvm guest tried={smol_guest.get('tried')} ok={smol_guest.get('ok')} "
            f"err={smol_guest.get('error')}",
            flush=True,
        )

    from daedalus.frame_geometry import frame_shows_scene
    geometry = []
    for p in shots:
        try:
            geometry.append({"path": p, "shows_scene": frame_shows_scene(p)})
        except Exception as exc:
            geometry.append({"path": p, "error": str(exc)[:120]})

    report = {
        "probes": probes,
        "chosen": chosen,
        "prompt": args.prompt,
        "worker_probe": worker_probe,
        "worker_config": {k: {"model": v.get("model"), "provider": v.get("provider")} for k, v in worker_cfg.items()},
        "host": host,
        "lab": {
            "ok": lab.get("ok"),
            "lab_id": lab.get("lab_id") or lab.get("id"),
            "url": lab.get("url"),
            "backend": (lab.get("computer") or lab).get("backend") if isinstance(lab.get("computer"), dict) else lab.get("backend"),
            "error": lab.get("error"),
            "shot": lab_shot,
            "play": lab_play,
            "smolvm_guest": smol_guest,
        },
        "build": build,
        "project": snap,
        "screenshots": shots,
        "geometry": geometry,
    }
    out_dir = Path(os.environ.get("DAEDALUS_DATA") or "data/daedalus") / "live_eval"
    out_dir.mkdir(parents=True, exist_ok=True)
    dest = out_dir / "latest.json"
    dest.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    usage = build.get("usage") or {}
    print(json.dumps({
        "chosen": chosen,
        "playbook": build.get("playbook"),
        "complete": build.get("complete"),
        "stop_reason": build.get("stop_reason"),
        "slug": build.get("slug"),
        "kernel_proof_ran": build.get("kernel_proof_ran"),
        "proof_gap": build.get("proof_gap"),
        "proof_frames": build.get("proof_frames"),
        "workers": build.get("workers"),
        "worker_probe": worker_probe,
        "lab": report["lab"],
        "knowledge": {
            "chroma": (build.get("knowledge") or {}).get("chroma"),
            "hits": (build.get("knowledge") or {}).get("hits"),
            "rag_in_system": (build.get("knowledge") or {}).get("rag_in_system"),
        },
        "host_smolvm": (host.get("smolvm") or {}),
        "smolvm_doctor": host.get("doctor"),
        "tools": [r["tool"] for r in build.get("tool_log") or []],
        "files": [f["path"] for f in snap.get("files") or []],
        "proof_on_disk": snap.get("proof_dir") or [],
        "screenshots": shots,
        "geometry": geometry,
        "usage": usage,
        "report": str(dest),
    }, ensure_ascii=False, indent=2))
    painted = any(g.get("shows_scene") for g in geometry)
    return 0 if painted or build.get("complete") else 1


def main() -> None:
    parser = argparse.ArgumentParser(description="Live Playground build (OpenRouter)")
    parser.add_argument("--model", default="", help="OpenRouter id; empty = probe the candidate pool")
    parser.add_argument("--preset", choices=sorted(PROMPTS), default="", help="dungeon or slerp prompt")
    parser.add_argument("--prompt", default="")
    parser.add_argument("--max-rounds", type=int, default=12)
    parser.add_argument("--better", action="store_true", help="Probe Claude / GLM / Kimi instead of MiniMax")
    parser.add_argument("--screenshots", default=str(ARTIFACT_SHOTS), help="Directory for copied proof PNGs")
    parser.add_argument("--shot-tag", default="dungeon2", help="Filename prefix so we do not overwrite earlier proofs")
    args = parser.parse_args()
    _ensure_local_bin_on_path()
    if args.preset and not args.prompt:
        args.prompt = PROMPTS[args.preset]
    if not args.prompt:
        args.prompt = SLERP if args.better else DUNGEON
    data = os.environ.get("DAEDALUS_DATA") or str(Path("/tmp/daedalus-live-eval"))
    os.environ.setdefault("DAEDALUS_DATA", data)
    os.environ.setdefault("DAEDALUS_PLAYGROUND", str(Path(data) / "projects"))
    os.environ.setdefault("DAEDALUS_STANDALONE", "1")
    raise SystemExit(asyncio.run(main_async(args)))


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    main()
