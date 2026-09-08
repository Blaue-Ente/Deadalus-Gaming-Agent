#!/usr/bin/env python3
"""Live Playground build against OpenRouter (MiniMax / DeepSeek).

Probes tool-calling first, then runs the kernel native build loop (S1+S2)
on a slice that is not Snake. Writes a JSON report under $DAEDALUS_DATA.

  DAEDALUS_STANDALONE=1 python3 scripts/live_eval_build.py
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from pathlib import Path

CANDIDATES = (
    "minimax/minimax-m2.7",
    "deepseek/deepseek-v3.2",
)

DUNGEON = (
    "Направи нова Playground игра: top-down dungeon crawler на HTML canvas "
    "(kind=static, slug dungeon-keys). Три стаи свързани с врати. В първата "
    "стая има златен ключ. Последната врата е заключена докато играчът няма "
    "ключа. Един slime враг с HP, играчът се движи със стрелки, атака с "
    "Space, HP лента, game over при 0 HP, победа при изхода. Една завършена "
    "playable среза: input → update → render → win/lose. Не прави змия, "
    "tetris или pong. Не чети daedalus/agent.py."
)


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


async def run_build(prompt: str, max_rounds: int) -> dict:
    from daedalus.agent import _call_claude_with_tools
    from daedalus.build_loop import BuildLoop
    from daedalus.llm_config import get_config
    from daedalus.native_tools import run_native_discovery_pass
    from daedalus.tool_router import format_system, native_specs, route, run_consult

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
        _call_claude_with_tools,
        memory=None,
        session_id="playground:live-eval",
        read_file_fn=lambda *a, **k: {},
        max_rounds=max_rounds,
        tools=specs,
        loop=loop,
    )
    row = loop.log_row()
    return {
        "provider": cfg.get("provider"),
        "model": cfg.get("model"),
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
    return {
        "root": str(root),
        "files": files,
        "readme": readme.read_text(encoding="utf-8")[:1500] if readme.is_file() else "",
        "proof_dir": sorted(x.name for x in proof.iterdir()) if proof.is_dir() else [],
    }


async def main_async(args: argparse.Namespace) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    probes = []
    chosen = args.model.strip() if args.model else ""
    if chosen:
        row = await probe_tools(chosen)
        probes.append(row)
        if not row["ok"]:
            print(json.dumps({"error": "probe_failed", "probes": probes}, ensure_ascii=False, indent=2))
            return 2
    else:
        for mid in CANDIDATES:
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
    report = {
        "probes": probes,
        "chosen": chosen,
        "prompt": args.prompt,
        "build": build,
        "project": snap,
    }
    out_dir = Path(os.environ.get("DAEDALUS_DATA") or "data/daedalus") / "live_eval"
    out_dir.mkdir(parents=True, exist_ok=True)
    dest = out_dir / "latest.json"
    dest.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
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
        "report": str(dest),
    }, ensure_ascii=False, indent=2))
    return 0 if build.get("kernel_proof_ran") or build.get("complete") else 1


def main() -> None:
    parser = argparse.ArgumentParser(description="Live Playground build (OpenRouter)")
    parser.add_argument("--model", default="", help="OpenRouter id; empty = probe MiniMax then DeepSeek")
    parser.add_argument("--prompt", default=DUNGEON)
    parser.add_argument("--max-rounds", type=int, default=10)
    args = parser.parse_args()
    data = os.environ.get("DAEDALUS_DATA") or str(Path("/tmp/daedalus-live-eval"))
    os.environ.setdefault("DAEDALUS_DATA", data)
    os.environ.setdefault("DAEDALUS_PLAYGROUND", str(Path(data) / "projects"))
    os.environ.setdefault("DAEDALUS_STANDALONE", "1")
    raise SystemExit(asyncio.run(main_async(args)))


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    main()
