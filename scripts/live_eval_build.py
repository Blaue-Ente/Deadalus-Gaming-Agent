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
import json
import logging
import os
import shutil
import sys
import time
from pathlib import Path

CANDIDATES = (
    "minimax/minimax-m2.7",
    "deepseek/deepseek-v3.2",
)

# Owner-cheap pool — no Claude. Probe MiniMax M3 first, then DeepSeek V4 / GLM 5.2.
CHEAP_CANDIDATES = (
    "minimax/minimax-m3",
    "deepseek/deepseek-v4-flash",
    "z-ai/glm-5.2",
    "minimax/minimax-m2.7",
    "poolside/laguna-s-2.1:free",
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
    "(kind=static, slug dungeon-keys). Три стаи свързани с врати. В първата "
    "стая има златен ключ. Последната врата е заключена докато играчът няма "
    "ключа. Един slime враг с HP, играчът се движи със стрелки, атака с "
    "Space, HP лента, game over при 0 HP, победа при изхода. Една завършена "
    "playable среза: input → update → render → win/lose. Не прави змия, "
    "tetris или pong."
)

SLERP = (
    "Направи нова Playground 3D среза (kind=static, slug slerp-lookat): "
    "WebGL или Three.js от CDN, без vite. Сива кутия: под, няколко блока и "
    "една ярка цел. Камерата прави quaternion SLERP към целта и look-at към нея. "
    "Клавиши 1/2/3 сменят целта. Извикай math3d с конкретни claimed числа "
    "(quat_slerp и look_at) преди да пишеш формулите в JS. "
    "Една playable среза: видима 3D сцена, не черен екран. "
    "Не прави змия, tetris или pong."
)

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


def _export_screenshots(slug: str, dest: Path) -> list[str]:
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
    for src in candidates:
        out = dest / f"{slug}-{src.name}"
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

    print(f"build model={chosen} playbook-probe…", flush=True)
    build = await run_build(args.prompt, args.max_rounds)
    snap = _project_snapshot(str(build.get("slug") or ""))
    shots_dir = Path(args.screenshots) if args.screenshots else ARTIFACT_SHOTS
    try:
        shots = _export_screenshots(str(build.get("slug") or ""), shots_dir)
    except Exception as exc:
        shots = []
        logging.warning("screenshot export failed: %s", exc)
    report = {
        "probes": probes,
        "chosen": chosen,
        "prompt": args.prompt,
        "build": build,
        "project": snap,
        "screenshots": shots,
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
        "tools": [r["tool"] for r in build.get("tool_log") or []],
        "files": [f["path"] for f in snap.get("files") or []],
        "proof_on_disk": snap.get("proof_dir") or [],
        "screenshots": shots,
        "usage": usage,
        "report": str(dest),
    }, ensure_ascii=False, indent=2))
    return 0 if build.get("kernel_proof_ran") or build.get("complete") else 1


def main() -> None:
    parser = argparse.ArgumentParser(description="Live Playground build (OpenRouter)")
    parser.add_argument("--model", default="", help="OpenRouter id; empty = probe the candidate pool")
    parser.add_argument("--preset", choices=sorted(PROMPTS), default="", help="dungeon or slerp prompt")
    parser.add_argument("--prompt", default="")
    parser.add_argument("--max-rounds", type=int, default=12)
    parser.add_argument("--better", action="store_true", help="Probe Claude / GLM / Kimi instead of MiniMax")
    parser.add_argument("--screenshots", default=str(ARTIFACT_SHOTS), help="Directory for copied proof PNGs")
    args = parser.parse_args()
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
