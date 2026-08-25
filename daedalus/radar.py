"""Daedalus Open-Source Radar.

Periodically scans curated sources for new, interesting open-source projects
around AI, agents, MCP, automation, and dev tools. Results flow into the
standard Daedalus Discovery pipeline — pending for owner review, auto-approved
only when confidence >= threshold.

Sources (configurable):
  - GitHub Trending: AI agents, MCP, coding agents, autonomous tools
  - Web: agents-radar, HuggingFace Papers, GitHub Blog AI&ML, ByteByteGo

Scheduler: designed to be called daily (wired via main.py lifespan or
           manually via POST /admin/daedalus/radar/scan).
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# State persistence
# ---------------------------------------------------------------------------

def _state_path() -> Path:
    from .paths import data_root

    path = data_root() / "radar_state.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _load_state() -> dict[str, Any]:
    try:
        path = _state_path()
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {"last_scan": None, "last_summary": None, "scan_count": 0}


def _save_state(state: dict[str, Any]) -> None:
    try:
        path = _state_path()
        path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:
        log.warning("radar: failed to save state: %s", exc)


def get_radar_state() -> dict[str, Any]:
    return _load_state()


# ---------------------------------------------------------------------------
# Source definitions
# ---------------------------------------------------------------------------

RADAR_SOURCES: list[dict[str, Any]] = [
    # ── GitHub searches (use github_search_tool) ───────────────────────────
    {
        "id": "github-ai-agents",
        "label": "GitHub: AI Agents & Autonomous Tools",
        "type": "github",
        "query": "AI agent autonomous coding tool 2026",
        "min_stars": 100,
        "limit": 6,
        "category": "ai-agents",
    },
    {
        "id": "github-mcp",
        "label": "GitHub: MCP — Model Context Protocol",
        "type": "github",
        "query": "MCP model context protocol server tools",
        "min_stars": 50,
        "limit": 5,
        "category": "mcp",
    },
    {
        "id": "github-coding-agents",
        "label": "GitHub: Coding Agents & Dev Automation",
        "type": "github",
        "query": "autonomous coding agent open source developer tool",
        "min_stars": 200,
        "limit": 5,
        "category": "coding-agents",
    },
    {
        "id": "github-llm-tools",
        "label": "GitHub: LLM Frameworks & Orchestration",
        "type": "github",
        "query": "LLM orchestration framework agent workflow open source",
        "min_stars": 500,
        "limit": 5,
        "category": "llm-tools",
    },
    {
        "id": "github-awesome-mcp",
        "label": "GitHub: Awesome MCP List",
        "type": "github",
        "query": "awesome MCP model context protocol curated list",
        "min_stars": 10,
        "limit": 4,
        "category": "mcp",
    },
    {
        "id": "github-memory",
        "label": "GitHub: Agent Memory & RAG",
        "type": "github",
        "query": "LLM agent memory RAG long-term context open source",
        "min_stars": 80,
        "limit": 5,
        "category": "memory",
    },
    {
        "id": "github-local-llm",
        "label": "GitHub: Local LLM inference",
        "type": "github",
        "query": "ollama llama.cpp local LLM inference engine open source",
        "min_stars": 200,
        "limit": 5,
        "category": "local-llm",
    },
    {
        "id": "github-coding-harness",
        "label": "GitHub: Coding agent harnesses",
        "type": "github",
        "query": "open source coding agent SWE-bench harness tool loop",
        "min_stars": 100,
        "limit": 5,
        "category": "coding-agents",
    },
    {
        "id": "github-designarena-runner",
        "label": "GitHub: Design Arena agent-runner",
        "type": "github",
        "query": "Design-Arena agent-runner coding agent harness MIT",
        "min_stars": 0,
        "limit": 4,
        "category": "coding-agents",
    },
    # ── Web searches (use web_search_tool) ─────────────────────────────────
    {
        "id": "web-agents-radar",
        "label": "Agents Radar — Daily AI Trends",
        "type": "web",
        "query": "agents-radar github duanyytop AI open source trends 2026",
        "limit": 5,
        "category": "trend-radar",
    },
    {
        "id": "web-huggingface-papers",
        "label": "HuggingFace Papers — Latest AI Research",
        "type": "web",
        "query": "huggingface.co papers AI research 2026 new",
        "limit": 4,
        "category": "research",
    },
    {
        "id": "web-github-blog-ai",
        "label": "GitHub Blog — AI & ML",
        "type": "web",
        "query": "site:github.blog AI ML open source developer tools 2026",
        "limit": 4,
        "category": "news",
    },
    {
        "id": "web-bytebytego",
        "label": "ByteByteGo — Top AI Repositories",
        "type": "web",
        "query": "bytebytego top AI github repositories 2026",
        "limit": 4,
        "category": "curated",
    },
    {
        "id": "web-designarena-gamedev",
        "label": "Design Arena — agentic game/app practices",
        "type": "web",
        "query": "site:notes.designarena.ai agentic game development Agent Runner harness",
        "limit": 4,
        "category": "coding-agents",
    },
]

for _src in RADAR_SOURCES:
    _src.setdefault("name", _src.get("label") or _src["id"])
    _src.setdefault("url", _src.get("query") or "")


def sources_by_ids(source_ids: list[str] | None) -> list[dict[str, Any]]:
    if not source_ids:
        return list(RADAR_SOURCES)
    wanted = set(source_ids)
    picked = [src for src in RADAR_SOURCES if src["id"] in wanted]
    return picked or list(RADAR_SOURCES)


def rotate_source_ids(cursor: int = 0, n: int = 2) -> tuple[list[str], int]:
    """Pick ``n`` source ids, wrapping around the catalog."""
    ids = [src["id"] for src in RADAR_SOURCES]
    if not ids:
        return [], 0
    n = max(1, min(int(n), len(ids)))
    start = int(cursor) % len(ids)
    picked = [ids[(start + i) % len(ids)] for i in range(n)]
    nxt = (start + n) % len(ids)
    return picked, nxt


def _result_urls(result: dict[str, Any]) -> list[str]:
    urls: list[str] = []
    for row in result.get("results") or []:
        if not isinstance(row, dict):
            continue
        url = row.get("url") or row.get("html_url") or row.get("link")
        if url:
            urls.append(str(url))
    return urls[:8]


# ---------------------------------------------------------------------------
# Core scan function
# ---------------------------------------------------------------------------

async def run_radar_scan(
    daedalus_instance: Any,
    source_ids: list[str] | None = None,
    *,
    for_self: bool = False,
) -> dict[str, Any]:
    """Run a radar scan across all sources, or a subset.

    ``for_self=True`` records discoveries as pending self-improvement
    (evolution gardener). The Command Center full scan stays ``for_self=False``.
    """
    from .tools import web_search_tool, github_search_tool

    catalog = sources_by_ids(source_ids)
    memory = getattr(daedalus_instance, "memory", None)
    t0 = time.time()
    source_results: list[dict[str, Any]] = []
    total_found = 0
    total_new_discoveries = 0

    log.info("radar: starting scan over %d sources (for_self=%s)", len(catalog), for_self)

    for src in catalog:
        src_id = src["id"]
        src_type = src.get("type", "web")
        try:
            record = memory is not None
            if src_type == "github":
                result = await github_search_tool(
                    memory=memory,
                    query=src["query"],
                    limit=src.get("limit", 5),
                    min_stars=src.get("min_stars", 0),
                    record_discoveries=record,
                    for_self=for_self,
                )
            else:
                result = await web_search_tool(
                    memory=memory,
                    query=src["query"],
                    limit=src.get("limit", 5),
                    fetch_content=False,
                    record_discoveries=record,
                    for_self=for_self,
                )

            found = result.get("result_count", 0) or len(result.get("results", []))
            new_disc = result.get("discoveries_recorded", 0)
            total_found += found
            total_new_discoveries += new_disc
            urls = _result_urls(result)

            source_results.append({
                "id": src_id,
                "label": src["label"],
                "type": src_type,
                "ok": result.get("ok", True),
                "found": found,
                "new_discoveries": new_disc,
                "category": src.get("category", "general"),
                "error": result.get("error"),
                "urls": urls,
            })
            log.info("radar: %s → %d results, %d new discoveries", src_id, found, new_disc)

        except Exception as exc:
            log.warning("radar: source %s failed: %s", src_id, exc)
            source_results.append({
                "id": src_id,
                "label": src["label"],
                "type": src_type,
                "ok": False,
                "found": 0,
                "new_discoveries": 0,
                "error": str(exc),
            })

    elapsed = round(time.time() - t0, 1)
    state = _load_state()
    state["last_scan"] = int(time.time())
    state["scan_count"] = state.get("scan_count", 0) + 1
    state["last_summary"] = {
        "sources_scanned": len(catalog),
        "total_found": total_found,
        "new_discoveries": total_new_discoveries,
        "elapsed_s": elapsed,
        "for_self": for_self,
        "source_ids": [s["id"] for s in catalog],
    }
    _save_state(state)

    log.info(
        "radar: scan complete — %d sources, %d results, %d new discoveries in %.1fs",
        len(catalog), total_found, total_new_discoveries, elapsed,
    )

    return {
        "ok": True,
        "scan_count": state["scan_count"],
        "sources_scanned": len(catalog),
        "total_found": total_found,
        "new_discoveries": total_new_discoveries,
        "elapsed_s": elapsed,
        "sources": source_results,
        "ts": state["last_scan"],
        "for_self": for_self,
    }
