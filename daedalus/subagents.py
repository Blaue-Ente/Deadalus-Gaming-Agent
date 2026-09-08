"""Tool-using sub-agents — isolated loops with files + shell, then return.

``workers.py`` slots are stateless LLM calls that must never emit NEED_*.
These sub-agents *do* get tools, jailed to a Playground slug, with a hard
round cap. Explorer cannot write files. Each spawn starts a fresh window.

Master emits::

    <NEED_SUBAGENT: slug | task>
    <NEED_SUBAGENT: slug | task | explorer>

or the native tool ``spawn_subagent``.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

log = logging.getLogger(__name__)

_SUB_RE = re.compile(
    r"<NEED_SUBAGENT:\s*([^|>]{1,80})\s*\|\s*([^|>]+?)"
    r"(?:\s*\|\s*(explorer|coder|verify|planner|builder|specialist|verifier|polish))?\s*>",
    re.DOTALL | re.IGNORECASE,
)

MAX_PARALLEL = 3
DEFAULT_ROUNDS = 6
CODER_TOOLS = ("read_file", "list_dir", "write_file", "edit_file", "playground_new", "run_command", "engine", "world", "assets", "skill", "production", "math3d", "spatial", "game_lint", "profiler", "game_canon")
EXPLORER_TOOLS = ("read_file", "list_dir", "run_command", "engine", "world", "skill", "production", "math3d", "spatial", "game_lint", "profiler", "game_canon")
VERIFY_TOOLS = EXPLORER_TOOLS

_BASE_SYSTEM = """You are a Daedalus sub-agent. You have tools. The master agent
delegated ONE concrete task. Finish it, then stop.

Rules:
- Stay inside the given playground slug.
- run_command is jailed; no sudo, no secrets.
- Do not chat. Do the work.
- End with TASK_COMPLETE or TASK_BLOCKED: reason.
- If you discovered a reusable fact, emit:
  ===ARTIFACT: snake_case_id===
  the fact
  ===END===
- Never claim TASK_COMPLETE if you did not finish, or if a human must intervene.
"""

_EXPLORER_SYSTEM = """
Role: explorer (read-only). You MUST NOT write or edit project files.
Investigate, map, and verify. Return named artifacts the coder will need.
If asked to verify, check the original ask / acceptance, not a weaker proxy.
skill(action=catalog|load) is read-only. Pending drafts wait for owner Approve.
production(action=status|prove) is read-only. Empty systems map is not a production layer.
"""

_CODER_SYSTEM = """
Role: coder. Implement exactly this slice — no extra features.
Use injected artifacts instead of rediscovering the tree.
2D kit: assets({action: atlas|sfx|font, slug}) — no paid API. 3D mesh jobs
fail closed without FAL_KEY/TRIPO_API_KEY/MESHY_API_KEY; greybox is a cube, not a hero.
Do not vendor a Unity Editor. engine(action=run) on Unity is MCP or batchmode or honest refuse.
production(action=seed, slug) writes design/systems.json only. Do not inject
src/layer/runtime.js into the Vite racer M0.
Game slice: math3d / spatial before custom SLERP, rays, or nested collision.
"""

_VERIFY_SYSTEM = """
Role: verify. Read-only for files. Confirm the slice matches the original ask.
Do not write game files. Evidence JSON is the adapter's job via production/engine.
For Godot: engine({action: run|inspect|input|errors, slug}) — files are not Play.
Unity: engine(action=run, slug) needs UNITY_PATH or DAEDALUS_UNITY_MCP.
Scaffold is not Play. This cloud does not vendor a Unity Editor.
For campaigns: world({action: prove, slug}) — empty JSON is not a campaign.
production(action=prove|pipeline, slug) — empty map is not a production layer;
store-ready on a prototype hits the wall. Skip is not a pass.
Game verify: spatial scan / math3d when the slice claimed custom math or physics.
Say TASK_COMPLETE only if evidence supports it; otherwise TASK_BLOCKED.
"""

_PLANNER_SYSTEM = """
Role: planner. Read-only. Describe the capability contract (target, checks, DoD).
Do not write game files. Name the adapter (Web / Engine / Mobile) and required proofs.
"""

_SPECIALIST_SYSTEM = """
Role: platform specialist. Write only adapter-specific files for this target.
Web: Vite/HTML/Phaser. Engine: .gd/.tscn. Mobile: Expo views or Capacitor wrap —
never rewrite Godot to HTML or a canvas game to Expo.
Claim exclusive files via spawn_subagent files[]. Do not rewrite the same file
as another writer.
"""

_POLISH_SYSTEM = """
Role: polish. You run only after visual quality failed. Fix the proven defect.
Do not invent a new pipeline or rewrite unrelated files.
"""


def tools_for_role(role: str) -> tuple[str, ...]:
    from .capability import normalize_role
    r = normalize_role(role or "coder")
    if r == "explorer":
        return EXPLORER_TOOLS
    if r == "verify":
        return VERIFY_TOOLS
    return CODER_TOOLS


def parse_subagent_markers(raw: str) -> list[dict[str, str]]:
    if not raw or "NEED_SUBAGENT" not in raw:
        return []
    out: list[dict[str, str]] = []
    for slug, task, role in _SUB_RE.findall(raw):
        item = {"slug": slug.strip(), "task": task.strip()}
        if role:
            item["role"] = role.strip().lower()
        out.append(item)
    return out[:MAX_PARALLEL]


def _specs_for(role: str) -> list[dict[str, Any]]:
    from .native_tools import NATIVE_TOOL_SPECS
    allowed = set(tools_for_role(role))
    return [t for t in NATIVE_TOOL_SPECS if t["name"] in allowed]


def _role_system(role: str) -> str:
    r = (role or "coder").strip().lower()
    extra = _CODER_SYSTEM
    if r in ("explorer", "planner"):
        extra = _EXPLORER_SYSTEM + _PLANNER_SYSTEM if r == "planner" else _EXPLORER_SYSTEM
    elif r in ("verify", "verifier"):
        extra = _VERIFY_SYSTEM
    elif r == "specialist":
        extra = _CODER_SYSTEM + _SPECIALIST_SYSTEM
    elif r == "polish":
        extra = _CODER_SYSTEM + _POLISH_SYSTEM
    return _BASE_SYSTEM + extra


async def _bootstrap_block(
    slug: str,
    items: list[Any] | None,
    read_file_fn: Any,
) -> str:
    if not items:
        return ""
    chunks: list[str] = []
    from . import playground as pg

    for raw in list(items)[:8]:
        if isinstance(raw, dict):
            path = str(raw.get("path") or "").strip()
            reason = str(raw.get("reason") or "").strip()
        else:
            path = str(raw or "").strip()
            reason = ""
        if not path:
            continue
        body = ""
        try:
            body = pg.read_file(slug, path, max_bytes=6000)
        except Exception:
            body = ""
        if not body and read_file_fn is not None:
            try:
                result = read_file_fn(path)
                if isinstance(result, dict):
                    body = str(result.get("content") or result.get("error") or "")
                else:
                    body = str(result or "")
            except Exception as exc:
                body = f"(read failed: {exc})"
        chunks.append(
            f"### {path}\n{reason}\n{(body or '(empty)')[:4000]}"
        )
    if not chunks:
        return ""
    return "### Bootstrapped files\n" + "\n\n".join(chunks)


async def run_subagent(
    slug: str,
    task: str,
    *,
    memory: Any = None,
    session_id: str = "",
    max_rounds: int = DEFAULT_ROUNDS,
    read_file_fn: Any = None,
    role: str = "coder",
    job_id: str = "",
    original_ask: str = "",
    return_contexts: list[str] | None = None,
    context_refs: list[str] | None = None,
    context_bootstrap: list[Any] | None = None,
    task_id: str = "",
    files: list[str] | None = None,
) -> dict[str, Any]:
    slug = (slug or "").strip()
    task = (task or "").strip()
    raw_role = (role or "coder").strip().lower()
    from .capability import (
        READ_ROLES,
        WRITER_ROLES,
        acquire_write,
        normalize_role,
        polish_allowed,
        release_write,
    )
    role = raw_role
    tool_role = normalize_role(raw_role)
    if not slug or not task:
        return {"ok": False, "error": "slug and task required"}
    if raw_role == "polish" and not polish_allowed(slug=slug):
        return {
            "ok": False,
            "slug": slug,
            "role": raw_role,
            "error": "polish only on a recorded visual-quality failure",
        }
    holder = f"{raw_role}:{task_id or session_id or 'agent'}"
    locked_ok = False
    if raw_role in WRITER_ROLES or (
        tool_role == "coder" and raw_role not in READ_ROLES
    ):
        locked = acquire_write(slug, holder=holder, files=files)
        if not locked.get("ok"):
            return {**locked, "slug": slug, "role": raw_role}
        locked_ok = True
    if read_file_fn is None:
        from .agent import _read_file_tool
        read_file_fn = _read_file_tool

    from .agent import _call_claude_with_tools
    from .native_tools import execute_native_tool
    from . import job_ledger

    try:
        job = None
        try:
            job = job_ledger.ensure_job(
                slug=slug,
                original_ask=original_ask or task,
                session_id=session_id,
                job_id=job_id,
            )
            job_id = str(job.get("job_id") or job_id)
        except Exception as exc:
            log.debug("job ledger skipped: %s", exc)
            job = None

        wanted = [c.strip() for c in (return_contexts or []) if c and str(c).strip()]
        system = _role_system(role) + f"\nPlayground slug: {slug}\nRole: {role}\n"
        if wanted:
            system += "Return these artifacts by id: " + ", ".join(wanted[:12]) + "\n"

        pack = ""
        if job:
            pack = job_ledger.format_pack(job, refs=list(context_refs or wanted), include_progress=True)
        boot = await _bootstrap_block(slug, context_bootstrap, read_file_fn)
        user_parts = [f"Task:\n{task}", "Always pass slug=" + repr(slug) + " to write_file and run_command."]
        if pack:
            user_parts.append(pack)
        if boot:
            user_parts.append(boot)
        user_parts.append(
            "When done, TASK_COMPLETE or TASK_BLOCKED. "
            "Named artifacts use ===ARTIFACT: id=== ... ===END===."
        )
        messages: list[dict[str, Any]] = [{"role": "user", "content": "\n\n".join(user_parts)}]
        log_rows: list[dict[str, Any]] = []
        final = ""
        rounds = max(1, min(int(max_rounds or DEFAULT_ROUNDS), 12))
        specs = _specs_for(role)
        allowed = set(tools_for_role(role))

        for _ in range(rounds):
            try:
                resp = await _call_claude_with_tools(system, messages, specs)
            except Exception as exc:
                return {"ok": False, "slug": slug, "role": role, "error": str(exc)[:240], "log": log_rows}
            content = resp.get("content") or []
            assistant: list[dict[str, Any]] = []
            uses: list[dict[str, Any]] = []
            texts: list[str] = []
            for block in content:
                if block.get("type") == "text":
                    texts.append(block.get("text") or "")
                    assistant.append(block)
                elif block.get("type") == "tool_use":
                    uses.append(block)
                    assistant.append(block)
            if not uses:
                final = "\n".join(texts).strip()
                break
            messages.append({"role": "assistant", "content": assistant})
            results: list[dict[str, Any]] = []
            for tu in uses:
                name = tu.get("name") or ""
                args = tu.get("input") or {}
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except json.JSONDecodeError:
                        args = {"raw": args}
                if not isinstance(args, dict):
                    args = {}
                if name not in allowed:
                    text = f"FAILED: role={role} cannot call {name}"
                else:
                    if name in ("write_file", "run_command"):
                        args["slug"] = slug
                    text = await execute_native_tool(
                        name, args,
                        memory=memory, session_id=session_id or "",
                        read_file_fn=read_file_fn,
                    )
                log_rows.append({"tool": name, "ok": "FAILED" not in text and "ERROR" not in text})
                results.append({
                    "type": "tool_result",
                    "tool_use_id": tu.get("id") or "",
                    "content": text,
                })
            messages.append({"role": "user", "content": results})
        else:
            final = final or "(round cap reached)"

        verdict = job_ledger.completion_verdict(final)
        artifacts = job_ledger.parse_artifacts(final)
        ok = True
        if verdict == "contradiction":
            ok = False
        report = {
            "ok": ok,
            "slug": slug,
            "role": role,
            "task": task[:200],
            "task_id": task_id,
            "job_id": job_id,
            "summary": final[:4000],
            "verdict": verdict,
            "artifacts": [{"id": a, "chars": len(b)} for a, b in artifacts],
            "tool_calls": log_rows,
            "rounds": len(log_rows),
        }
        if job_id:
            try:
                job_ledger.ingest_report(job_id, report, task_id=task_id)
            except Exception as exc:
                log.debug("job ingest skipped: %s", exc)
        return report
    finally:
        if locked_ok:
            release_write(slug, holder)


async def run_subagents_parallel(
    calls: list[dict[str, Any]],
    *,
    memory: Any = None,
    session_id: str = "",
    read_file_fn: Any = None,
) -> list[dict[str, Any]]:
    import asyncio
    from .capability import refuse_parallel_writers

    prepared = refuse_parallel_writers(list(calls or [])[:MAX_PARALLEL])

    async def _one(c: dict[str, Any]) -> dict[str, Any]:
        refused = c.get("_refuse")
        if isinstance(refused, dict):
            return {
                **refused,
                "slug": c.get("slug") or refused.get("slug"),
                "role": c.get("role") or "coder",
            }
        return await run_subagent(
            c.get("slug") or "",
            c.get("task") or "",
            memory=memory,
            session_id=session_id,
            read_file_fn=read_file_fn,
            max_rounds=int(c.get("max_rounds") or DEFAULT_ROUNDS),
            role=str(c.get("role") or "coder"),
            job_id=str(c.get("job_id") or ""),
            original_ask=str(c.get("original_ask") or ""),
            return_contexts=list(c.get("return_contexts") or []) or None,
            context_refs=list(c.get("context_refs") or []) or None,
            context_bootstrap=list(c.get("context_bootstrap") or []) or None,
            task_id=str(c.get("task_id") or ""),
            files=list(c.get("files") or []) or None,
        )

    jobs = [_one(c) for c in prepared]
    if not jobs:
        return []
    return list(await asyncio.gather(*jobs, return_exceptions=False))


def format_subagent_results(results: list[dict[str, Any]]) -> str:
    parts = []
    for r in results:
        role = r.get("role") or "coder"
        verdict = r.get("verdict") or ""
        head = f"[{r.get('slug')}/{role}] {'OK' if r.get('ok') else 'FAIL'}"
        if verdict:
            head += f" {verdict}"
        body = r.get("summary") or r.get("error") or ""
        arts = r.get("artifacts") or []
        if arts:
            body += "\nartifacts: " + ", ".join(a.get("id", "?") for a in arts)
        parts.append(f"{head}\n{body}")
    return "\n\n".join(parts)
