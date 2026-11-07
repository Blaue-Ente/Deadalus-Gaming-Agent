"""NEED_BATCH and NEED_VERIFY — Phase A autonomy upgrade for Daedalus.

NEED_BATCH  — execute N tool calls in parallel, in a single LLM iteration.
NEED_VERIFY — HTTP smoke-test ADN itself after a proposal is applied.

Both are designed to reduce the round-trip count for common coding tasks:
  - NEED_BATCH:  5 discovery calls → 1 iteration (was 5)
  - NEED_VERIFY: "did it actually work?" → self-closing loop (was 0 = never)
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
from typing import Any, Callable

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Block regex patterns — also imported by agent.py
# ---------------------------------------------------------------------------
BATCH_BLOCK_RE = re.compile(
    r"<NEED_BATCH>\s*(.*?)\s*</NEED_BATCH>",
    re.DOTALL,
)
VERIFY_BLOCK_RE = re.compile(
    r"<NEED_VERIFY>\s*\n(.*?)\n?\s*</NEED_VERIFY>",
    re.DOTALL,
)


def extract_batch_block(raw: str) -> str | None:
    if not raw or "NEED_BATCH" not in raw:
        return None
    m = BATCH_BLOCK_RE.search(raw)
    return m.group(1).strip() if m else None


def extract_verify_block(raw: str) -> str | None:
    if not raw or "NEED_VERIFY" not in raw:
        return None
    m = VERIFY_BLOCK_RE.search(raw)
    return m.group(1).strip() if m else None


# ---------------------------------------------------------------------------
# NEED_BATCH — parse + parallel execution
# ---------------------------------------------------------------------------

def _parse_batch_lines(block: str) -> list[tuple[str, str]]:
    items: list[tuple[str, str]] = []
    for line in block.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            continue
        tool, _, args = line.partition(":")
        items.append((tool.strip().lower(), args.strip()))
    return items


async def execute_batch(
    block: str,
    memory: Any,
    session_id: str,
    read_file_fn: Callable | None = None,
    logs_fn: Callable | None = None,
) -> str:
    """Execute all NEED_BATCH items in parallel, return combined result string."""
    from .tools import (
        web_search_tool, github_search_tool, postgres_query_tool,
        docs_fetch_tool, git_log_tool, git_diff_tool,
        list_endpoints_tool, list_modules_tool, describe_db_tool,
        search_code_tool, github_search_repos_tool,
    )

    items = _parse_batch_lines(block)
    if not items:
        return "━━━ NEED_BATCH: no valid lines parsed ━━━\nFormat: one 'tool: args' per line."

    async def _run_one(tool: str, args: str) -> str:
        try:
            # ── file read ──────────────────────────────────────────────────
            if tool == "read":
                if read_file_fn is None:
                    return f"── READ SKIPPED: read_file_fn not available ──"
                parts = [p.strip() for p in args.split("|", 1)]
                path = parts[0]
                line_start = line_end = None
                if len(parts) > 1:
                    rng = parts[1].split("-", 1)
                    if len(rng) == 2 and rng[0].isdigit() and rng[1].isdigit():
                        line_start, line_end = int(rng[0]), int(rng[1])
                loop = asyncio.get_event_loop()
                r = await loop.run_in_executor(None, read_file_fn, path, line_start, line_end)
                if r.get("ok"):
                    trunc = " [TRUNCATED — use NEED_READ_FILE for more]" if r.get("truncated") else ""
                    return f"── READ {r['path']}{trunc} ──\n{r['content']}"
                return f"── READ FAILED: {path}: {r.get('error')} ──"

            # ── postgres ───────────────────────────────────────────────────
            elif tool == "pg":
                r = await postgres_query_tool(args, session_id=session_id)
                if r.get("ok"):
                    rows = "\n".join(str(row) for row in (r.get("rows") or [])[:20])
                    return f"── PG ({r['row_count']} rows) ──\n{rows}"
                return f"── PG FAILED: {r.get('error')} ──"

            # ── web search ─────────────────────────────────────────────────
            elif tool == "web":
                r = await web_search_tool(memory, args, limit=4)
                results = r.get("results") or []
                lines = [f"── WEB: {args} ──"]
                for res in results:
                    lines.append(
                        f"  • {res.get('title', '')} — {res.get('url', '')}\n"
                        f"    {str(res.get('snippet', ''))[:140]}"
                    )
                return "\n".join(lines) if len(lines) > 1 else f"── WEB: no results for '{args}' ──"

            # ── github code search ─────────────────────────────────────────
            elif tool == "github":
                r = await github_search_tool(memory, args, limit=4)
                results = r.get("results") or []
                lines = [f"── GITHUB: {args} ──"]
                for res in results:
                    lines.append(
                        f"  • {res.get('title', '')} ({res.get('url', '')})\n"
                        f"    {str(res.get('snippet', ''))[:120]}"
                    )
                return "\n".join(lines) if len(lines) > 1 else f"── GITHUB: no results for '{args}' ──"

            # ── github repo search ─────────────────────────────────────────
            elif tool in ("github_repo", "repo"):
                r = await github_search_repos_tool(args, limit=4)
                repos = r.get("repos") or []
                lines = [f"── GITHUB REPOS: {args} ──"]
                for repo in repos:
                    lines.append(
                        f"  • {repo.get('full_name', '')} ★{repo.get('stars', 0)} "
                        f"({repo.get('language', '')}) — {str(repo.get('description', ''))[:100]}\n"
                        f"    {repo.get('url', '')}"
                    )
                return "\n".join(lines) if len(lines) > 1 else f"── GITHUB REPOS: no results for '{args}' ──"

            # ── capability search ──────────────────────────────────────────
            elif tool in ("capability", "cap"):
                try:
                    from argos_data_nexus.modules.capability_search import search_capabilities
                    cr = search_capabilities(args, limit=5)
                    matches = cr.get("matches") or []
                    if matches:
                        rows = "\n".join(
                            f"  [{m.get('score', 0):.2f}] {m.get('method', ''):<6} {m.get('path', '')}"
                            f"\n        → {str(m.get('summary', ''))[:100]}"
                            for m in matches
                        )
                        return f"── CAPABILITY: {args} ──\n{rows}"
                    return f"── CAPABILITY: no match for '{args}' — нещото не съществува ──"
                except Exception as exc:
                    return f"── CAPABILITY FAILED: {exc} ──"

            # ── endpoint listing ───────────────────────────────────────────
            elif tool in ("endpoints", "routes"):
                r = await list_endpoints_tool(args or "*")
                text = r.get("text") or str(r)[:2000]
                return f"── ENDPOINTS: {args or '*'} ──\n{text}"

            # ── module listing ─────────────────────────────────────────────
            elif tool == "modules":
                r = await list_modules_tool(args or "*")
                text = r.get("text") or str(r)[:2000]
                return f"── MODULES: {args or '*'} ──\n{text}"

            # ── db schema ──────────────────────────────────────────────────
            elif tool in ("db_schema", "schema"):
                r = await describe_db_tool(args or "*")
                text = r.get("text") or str(r)[:2000]
                return f"── DB_SCHEMA: {args or '*'} ──\n{text}"

            # ── code / semantic search ─────────────────────────────────────
            elif tool in ("code_search", "search", "code"):
                r = await search_code_tool(args)
                lines = [f"── CODE_SEARCH: {args} ──"]
                for m in (r.get("semantic_matches") or [])[:5]:
                    lines.append(
                        f"  [sem] {m.get('filepath', '')} → {m.get('qualified_name', '')}\n"
                        f"        {str(m.get('docstring', ''))[:80]}"
                    )
                for m in (r.get("keyword_matches") or [])[:5]:
                    lines.append(f"  [kw]  {m.get('file', '')}:{m.get('line', '')} — {str(m.get('snippet', ''))[:80]}")
                return "\n".join(lines)

            # ── recent logs ────────────────────────────────────────────────
            elif tool == "logs":
                if logs_fn is None:
                    return f"── LOGS SKIPPED: logs_fn not available ──"
                n = int(args) if (args or "").isdigit() else 30
                loop = asyncio.get_event_loop()
                r = await loop.run_in_executor(None, logs_fn, n, None)
                if r.get("ok"):
                    return f"── LOGS (last {n}) ──\n" + "\n".join(r.get("lines") or [])
                return f"── LOGS FAILED: {r.get('error')} ──"

            # ── git log ────────────────────────────────────────────────────
            elif tool in ("git_log", "gitlog"):
                parts = [p.strip() for p in args.split("|", 1)]
                n = int(parts[0]) if parts[0].isdigit() else 10
                path = parts[1] if len(parts) > 1 else None
                r = await git_log_tool(n, path)
                if r.get("ok"):
                    commits = "\n".join(
                        f"  {c['date']} [{c['hash']}] {c['author']}: {c['subject']}"
                        for c in r.get("commits") or []
                    )
                    return f"── GIT LOG ──\n{commits}"
                return f"── GIT LOG FAILED: {r.get('error')} ──"

            # ── git diff ───────────────────────────────────────────────────
            elif tool in ("git_diff", "gitdiff"):
                parts = [p.strip() for p in args.split("|", 1)]
                path = parts[0] or None
                ref = parts[1] if len(parts) > 1 else "HEAD"
                r = await git_diff_tool(path, ref)
                if r.get("ok"):
                    return f"── GIT DIFF {path or 'all'} vs {ref} ──\n{str(r.get('diff', ''))[:3000]}"
                return f"── GIT DIFF FAILED: {r.get('error')} ──"

            # ── docs fetch ─────────────────────────────────────────────────
            elif tool == "docs":
                r = await docs_fetch_tool(args)
                if r.get("ok"):
                    return f"── DOCS: {args} ──\n{str(r.get('content', ''))[:3000]}"
                return f"── DOCS FAILED: {r.get('error')} ──"

            else:
                return (
                    f"── UNKNOWN BATCH TOOL: '{tool}' — skipped ──\n"
                    f"Valid: read, pg, web, github, github_repo, capability, "
                    f"endpoints, modules, db_schema, code_search, logs, git_log, git_diff, docs"
                )

        except Exception as exc:
            log.warning("batch item '%s' failed: %s", tool, exc, exc_info=True)
            return f"── {tool.upper()} ERROR: {exc} ──"

    tasks = [_run_one(tool, args) for tool, args in items]
    results = await asyncio.gather(*tasks, return_exceptions=False)

    parts = [f"━━━ TOOL RESULTS — NEED_BATCH ({len(items)} tools, parallel) ━━━"]
    parts.extend(str(r) for r in results)
    parts.append(
        f"[{len(items)} tools completed in 1 iteration instead of {len(items)}. "
        f"Use results above to proceed directly without further discovery calls.]"
    )
    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# NEED_VERIFY — end-to-end HTTP smoke test
# ---------------------------------------------------------------------------

def _parse_verify_lines(block: str) -> dict[str, str]:
    spec: dict[str, str] = {}
    for line in block.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        k = key.strip().lower().replace(" ", "_")
        spec[k] = val.strip()
    return spec


async def execute_verify(block: str, logs_fn: Callable | None = None) -> str:
    """Execute NEED_VERIFY: wait → curl ADN → tail logs → check expectations."""
    import httpx

    spec = _parse_verify_lines(block)
    parts = ["━━━ TOOL RESULTS — NEED_VERIFY ━━━"]

    # 1. Optional wait (server settle after apply)
    wait_secs = int(spec.get("wait", "2"))
    if wait_secs > 0:
        parts.append(f"⏳ Waiting {wait_secs}s for server to settle…")
        await asyncio.sleep(wait_secs)

    # 2. HTTP request
    status_code: int | None = None
    response_body: str = ""
    curl_spec = spec.get("curl", "")
    if curl_spec:
        method, _, path = curl_spec.partition(" ")
        method = (method.upper() or "GET").strip()
        path = path.strip()
        adn_port = int(os.environ.get("PORT", "8000"))
        url = f"http://localhost:{adn_port}{path if path.startswith('/') else '/' + path}"
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.request(method, url)
            status_code = resp.status_code
            response_body = resp.text[:3000]
            status_emoji = "✅" if resp.status_code < 400 else "❌"
            parts.append(
                f"{status_emoji} {method} {url}\n"
                f"   Status : {status_code}\n"
                f"   Body   : {response_body[:600]}"
            )
        except Exception as exc:
            parts.append(f"❌ CURL FAILED: {url}\n   Error: {exc}")

    # 3. Tail recent logs
    logs_n_str = spec.get("logs_since", spec.get("logs", "0"))
    logs_n = int(logs_n_str) if str(logs_n_str).isdigit() else 0
    has_log_error = False
    if logs_n > 0 and logs_fn is not None:
        loop = asyncio.get_event_loop()
        lr = await loop.run_in_executor(None, logs_fn, logs_n, None)
        if lr.get("ok"):
            log_lines = lr.get("lines") or []
            error_lines = [l for l in log_lines if "ERROR" in l or "Exception" in l or "Traceback" in l]
            has_log_error = bool(error_lines)
            log_text = "\n".join(log_lines)
            flag = "⚠️  Errors in logs!" if has_log_error else "✅ No errors in logs."
            parts.append(f"📋 Logs (last {logs_n}):\n{log_text}\n{flag}")
            if error_lines:
                parts.append("ERROR LINES:\n" + "\n".join(error_lines[:5]))
        else:
            parts.append(f"📋 Logs unavailable: {lr.get('error')}")

    # 4. Validate expectations
    expect_str = spec.get("expect", "")
    if expect_str:
        checks: list[str] = []
        all_pass = True

        for raw_cond in [c.strip() for c in expect_str.split(",")]:
            cond = raw_cond.lower()

            if cond.startswith("status="):
                expected_status = int(cond.split("=", 1)[1])
                ok = status_code == expected_status
                checks.append(f"  {'✅' if ok else '❌'} status={expected_status} (got {status_code})")
                if not ok:
                    all_pass = False

            elif "has" in cond:
                needle = raw_cond.split("has", 1)[1].strip().strip('"').strip("'")
                ok = needle.lower() in response_body.lower()
                checks.append(f"  {'✅' if ok else '❌'} response contains '{needle}'")
                if not ok:
                    all_pass = False

            elif "no_error" in cond or "no error" in cond:
                ok = not has_log_error and (status_code is None or status_code < 400)
                checks.append(f"  {'✅' if ok else '❌'} no errors in logs/response")
                if not ok:
                    all_pass = False

            elif "json" == cond.strip():
                try:
                    import json as _json
                    _json.loads(response_body)
                    checks.append("  ✅ response is valid JSON")
                except Exception:
                    checks.append(f"  ❌ response is NOT valid JSON: {response_body[:100]}")
                    all_pass = False

        verdict = (
            "✅ ALL CHECKS PASSED — endpoint is live and working."
            if all_pass else
            "❌ SOME CHECKS FAILED — do NOT tell Nikolai it works. Fix the issue above."
        )
        checks_text = "\n".join(checks)
        parts.append(f"🔍 Expectations:\n{checks_text}\n\n{verdict}")

        if all_pass:
            parts.append(
                "NEXT: Report to Nikolai — what the endpoint does, its URL, "
                "and paste a real example response. Mark the task as done."
            )
        else:
            parts.append(
                "NEXT: Read error lines above carefully. Emit NEED_PROPOSE_START "
                "with the minimal fix. Then NEED_VERIFY again to confirm."
            )

    elif status_code is not None:
        # No explicit expectations — just report
        if status_code < 400:
            parts.append("✅ Server responded successfully. No explicit expectations to check.")
        else:
            parts.append(f"❌ Server returned {status_code}. Check error above and fix.")

    return "\n\n".join(parts)
