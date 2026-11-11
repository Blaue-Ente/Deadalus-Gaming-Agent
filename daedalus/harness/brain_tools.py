"""A7: the harness as brain tools.

One table (``SPECS``) defines what the brain may call; ``native_tools`` appends it to ``NATIVE_TOOL_SPECS`` and the
registry exposes it from there, so the brain, the router and the registry all see the same list.

- ``milestones``: state, gates, verify, advance. Advance never waives a check (no force, no Unity or proof waiver: those
  are the owner's buttons) and nothing that builds runs while Full Auto owns the project.
- ``preview``: run, stop, status, logs of the project's dev server. Never a public tunnel from the brain.
- ``play``: the native play worker in this process (start, frame, input, wait, state, errors, status, stop, list). A
  missing engine or display is an honest ``blocked`` error, never a pass.
- ``runs``: the run ledger, read only (list, get, events).
- ``ship_request``: publish or push is never done by the brain. It records a request (slug, kind, note) that the owner
  confirms or declines in the Studio, through the Studio's own buttons and their questions. The request has no force
  or public flag, and the brain cannot resolve it.
"""
from __future__ import annotations

import json
import threading
import time
import uuid
from typing import Any

SHIP_KINDS = ("local", "live", "push")
_SHIP_KEY = "adn:harness:ship_requests"
_SHIP_KEEP = 200
_ship_lock = threading.Lock()

SPECS: tuple[dict[str, Any], ...] = (
    {
        "name": "milestones",
        "description": (
            "Game Studio milestones of a project. action: state | gates | verify | advance. "
            "state reads where the project is (current milestone, gates, can_advance). gates runs the gate checks; "
            "verify runs the milestone verify (proof level included). advance moves to the next milestone only when "
            "the checks allow it: it never waives a check, and the owner's waivers are not available here. "
            "Nothing runs while Full Auto owns the project."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "description": "state | gates | verify | advance"},
                "slug": {"type": "string"},
                "gates": {"type": "array", "items": {"type": "string"}, "description": "gates: only these gate ids"},
                "milestone_id": {"type": "string", "description": "verify: a milestone other than the current one"},
            },
            "required": ["action", "slug"],
        },
    },
    {
        "name": "preview",
        "description": (
            "The project's preview server. action: status | run | stop | logs. run starts the dev server on this "
            "machine (no public tunnel); logs returns the last lines. A failed start is an error, never a preview."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "description": "status | run | stop | logs"},
                "slug": {"type": "string"},
                "install_deps": {"type": "boolean", "description": "run: install dependencies first"},
                "tail": {"type": "integer", "description": "logs: how many lines (max 400)"},
            },
            "required": ["action", "slug"],
        },
    },
    {
        "name": "play",
        "description": (
            "Play the native game (Godot or Unity) like a player. action: start | status | frame | input | wait | "
            "state | errors | stop | list. start opens a rendered session and returns its id; frame captures what is "
            "on screen (sha256, blank or not, proof level); input sends keys and mouse; state reads game state. "
            "A missing engine or display is blocked, never a pass. Stop the session when done."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "description": "start | status | frame | input | wait | state | errors | stop | list"},
                "slug": {"type": "string", "description": "start: the project"},
                "id": {"type": "string", "description": "the session id from start"},
                "engine": {"type": "string", "description": "start: godot | unity (default godot)"},
                "source": {"type": "string", "description": "frame: window | engine"},
                "keys": {"type": "array", "items": {"type": "string"}},
                "mouse": {"type": "object", "description": "{kind: click|move|down|up, x, y, button}"},
                "hold_ms": {"type": "integer"},
                "via": {"type": "string", "description": "input: os | bridge"},
                "frames": {"type": "integer"},
                "ms": {"type": "integer"},
                "path": {"type": "string", "description": "state: a node or field path"},
            },
            "required": ["action"],
        },
    },
    {
        "name": "runs",
        "description": (
            "The run ledger, read only. action: list | get | events. A run is a build, guided, autopilot, verify, "
            "play or chat job with its state (queued, running, succeeded, failed, unknown...) and its events."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "description": "list | get | events"},
                "id": {"type": "string"},
                "slug": {"type": "string"},
                "kind": {"type": "string"},
                "state": {"type": "string"},
                "after": {"type": "integer", "description": "events: after this sequence number"},
                "limit": {"type": "integer"},
            },
            "required": ["action"],
        },
    },
    {
        "name": "ship_request",
        "description": (
            "Ask the owner to publish or push. You cannot publish or push yourself. action: request | list. "
            "request records {slug, kind: local | live | push, note}; the owner sees it in the Studio and confirms "
            "or declines it with the Studio's own buttons and questions. Say in the note why it is ready."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "description": "request | list"},
                "slug": {"type": "string"},
                "kind": {"type": "string", "description": "local (a local copy) | live (a live link) | push (git push)"},
                "note": {"type": "string", "description": "why it is ready (shown to the owner)"},
                "status": {"type": "string", "description": "list: pending | approved | declined"},
            },
            "required": ["action"],
        },
    },
)

NAMES = frozenset(s["name"] for s in SPECS)

# Said to the model whenever a route offers these tools. Found live (2026-11-11, Haiku 5.5): offered but never named,
# they went unused; asked for preview logs and runs, the model searched the project files for log files instead.
SYSTEM_HINT = (
    "HARNESS TOOLS (use them; do not look for log or status files on disk):\n"
    "- preview: status | run | stop | logs of this project's preview server.\n"
    "- runs: list | get | events of build, verify, play and chat runs (read only).\n"
    "- milestones: state | gates | verify | advance (Game Studio projects; advance never waives a check).\n"
    "- play: a rendered native game session (frames, keys, mouse, state); stop it when done.\n"
    "- ship_request: you cannot publish or push. Ask the owner with ship_request (kind local | live | push, "
    "and a note why). Never claim something was published or pushed."
)
# What a tool can do decides its permission class (checked by before_tool_call, like every native tool):
# verify and advance rewrite project files (verify runs repair attempts); preview and play start processes.
# In a Playground build these classes are granted by default and stay revocable; runs and ship requests only read
# or record.
PERMS: dict[str, str | None] = {
    "milestones": "write_file",
    "preview": "run_command",
    "play": "run_command",
    "runs": None,
    "ship_request": None,
}
META: dict[str, dict[str, Any]] = {
    "milestones": {"module": "daedalus.harness.brain_tools", "tags": ("game", "studio", "verify", "milestone"),
                   "when": "Check gates, verify a milestone, or advance when the checks allow it"},
    "preview": {"module": "daedalus.harness.brain_tools", "tags": ("preview", "run", "logs"),
                "when": "Start or stop the project's preview server and read its logs"},
    "play": {"module": "daedalus.harness.brain_tools", "tags": ("play", "godot", "unity", "playtest", "frame"),
             "when": "Play the native game: frames, keys and mouse, game state"},
    "runs": {"module": "daedalus.harness.brain_tools", "tags": ("runs", "jobs", "status"),
             "when": "See which build, verify or play runs exist and how they ended"},
    "ship_request": {"module": "daedalus.harness.brain_tools", "tags": ("publish", "push", "ship"),
                     "when": "Ask the owner to publish or push; the owner confirms in the Studio"},
}


class ToolRefused(Exception):
    """A refusal the brain should read: its code and a plain message."""

    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(message or code)
        self.code = code


def _ok(**fields: Any) -> dict[str, Any]:
    return {"ok": True, **fields}


def _slug(args: dict[str, Any]) -> str:
    slug = str(args.get("slug") or "").strip()
    if not slug:
        raise ToolRefused("slug_required", "slug is required")
    return slug


async def _need_project(slug: str) -> dict[str, Any]:
    from .. import playground as pg

    proj = await pg.get_project(slug)
    if not proj:
        raise ToolRefused("not_found", "project not found")
    return proj


def _refuse_if_full_auto(slug: str) -> None:
    from . import full_auto

    if full_auto.owns(slug):
        raise ToolRefused("full_auto_running", "Full Auto owns this project; wait until it ends")


def _action(args: dict[str, Any], allowed: tuple[str, ...]) -> str:
    action = str(args.get("action") or "").strip().lower()
    if action not in allowed:
        raise ToolRefused("bad_action", "action must be " + " | ".join(allowed))
    return action


# ---- milestones -----------------------------------------------------------------------------------------------------

_STATE_FIELDS = ("current_milestone", "milestone_summary", "milestones", "gates", "gates_passed", "can_guided",
                 "can_autopilot", "can_advance", "verify", "current_verify", "project_complete", "studio_preset", "build_mode")


async def _milestones(args: dict[str, Any]) -> dict[str, Any]:
    from .. import game_studio

    action = _action(args, ("state", "gates", "verify", "advance"))
    slug = _slug(args)
    _refuse_if_full_auto(slug)              # gates and state also write the project config Full Auto is writing
    try:
        if action == "state":
            ctx = await game_studio.get_workspace_context(slug)
            return _ok(slug=slug, **{k: ctx.get(k) for k in _STATE_FIELDS})
        if action == "gates":
            ids = args.get("gates")
            res = await game_studio.run_project_gates(slug, gate_ids=ids if isinstance(ids, list) else None)
            return _ok(slug=slug, **(res or {}))
        if action == "verify":
            from .. import game_studio_verify

            logs: list[dict[str, str]] = []

            async def on_log(line: str, level: str = "info") -> None:
                logs.append({"line": str(line), "level": str(level)})

            res = await game_studio_verify.run_milestone_verify(slug, args.get("milestone_id") or None, on_log=on_log)
            return _ok(slug=slug, logs=logs[-60:], **(res if isinstance(res, dict) else {"result": res}))
        # advance: the brain never waives a check; force and both waivers are the owner's buttons in the Studio
        res = await game_studio.advance_project_milestone(slug, force=False, unity_waiver=False, proof_waiver=False)
        return _ok(slug=slug, **(res if isinstance(res, dict) else {}))
    except ToolRefused:
        raise
    except ValueError as exc:
        raise ToolRefused("refused", str(exc)[:400]) from exc


# ---- preview --------------------------------------------------------------------------------------------------------

def _public_run(rec: dict[str, Any] | None) -> dict[str, Any] | None:
    if not rec:
        return rec
    return {k: v for k, v in rec.items() if not str(k).startswith("_")}


async def _preview(args: dict[str, Any]) -> dict[str, Any]:
    from .. import playground_runner as runner

    action = _action(args, ("status", "run", "stop", "logs"))
    slug = _slug(args)
    if action == "status":
        return _ok(slug=slug, status=_public_run(runner.status(slug)))
    if action == "logs":
        tail = max(1, min(400, int(args.get("tail") or 80)))
        return _ok(slug=slug, logs=runner.logs(slug, tail=tail), status=_public_run(runner.status(slug)))
    proj = await _need_project(slug)
    _refuse_if_full_auto(slug)              # its verify uses this server: the brain must not stop or restart it
    if action == "run" and args.get("install_deps"):
        from .. import offline

        if offline.enabled():
            raise ToolRefused("offline", "offline mode is on: installing dependencies needs the network")
    if action == "stop":
        return _ok(slug=slug, stopped=bool(await runner.stop(slug)))
    from ..playground_runtime import is_local_only_kind, local_only_kind_message

    if is_local_only_kind(proj.get("kind")):
        raise ToolRefused("local_only", local_only_kind_message(proj.get("kind", "")))
    try:
        rec = await runner.start(slug, install_deps=bool(args.get("install_deps")), tunnel=False)   # never public
    except Exception as exc:
        raise ToolRefused("start_failed", str(exc)[:400]) from exc
    return _ok(slug=slug, run=_public_run(rec))


# ---- play -----------------------------------------------------------------------------------------------------------

_manager = None
_manager_lock = threading.Lock()


def _play_manager():
    global _manager
    with _manager_lock:
        if _manager is None:
            from .play_worker.manager import PlayManager

            _manager = PlayManager()
        return _manager


def stop_play_sessions() -> int:
    """Called when the server stops: no game or display outlives it."""
    if _manager is None:
        return 0
    try:
        return int(_manager.stop_all() or 0)
    except Exception:
        return 0


def reap_play_sessions() -> int:
    """Stop sessions the brain left idle (the server's reaper calls this; so does every play call)."""
    if _manager is None:
        return 0
    try:
        return len(_manager.reap_idle() or [])
    except Exception:
        return 0


def _bounded(value: Any, low: int, high: int, default: int) -> int:
    try:
        return max(low, min(high, int(value)))
    except (TypeError, ValueError):
        return default


def _play_sync(args: dict[str, Any], action: str, project_dir: Any) -> dict[str, Any]:
    mgr = _play_manager()
    reap_play_sessions()
    if action == "list":
        return _ok(sessions=mgr.list())
    if action == "start":
        engine = str(args.get("engine") or "godot").strip().lower()
        return mgr.start(str(args.get("slug")), project_dir, engine=engine)
    sid = str(args.get("id") or "").strip()
    if not sid:
        raise ToolRefused("id_required", "id (the session id from start) is required")
    if action == "stop":
        return mgr.stop(sid)
    sess = mgr.get(sid)
    if action == "status":
        return sess.status()
    if action == "frame":
        res = sess.frame(str(args.get("source") or "window"))
        return {k: v for k, v in res.items() if k != "path"}
    if action == "input":
        mouse = args.get("mouse") if isinstance(args.get("mouse"), dict) else None
        keys = [str(k)[:32] for k in (args.get("keys") or [])] if isinstance(args.get("keys"), list) else None
        if keys and len(keys) > 32:            # the worker's HTTP API has the same limit
            raise ToolRefused("bad_request", "at most 32 keys per input")
        return sess.send(keys=keys, mouse=mouse, hold_ms=_bounded(args.get("hold_ms"), 0, 5000, 80),
                         via=str(args.get("via") or "os"))
    if action == "wait":
        return sess.wait(frames=_bounded(args.get("frames"), 0, 600, 0), ms=_bounded(args.get("ms"), 0, 10000, 0))
    if action == "state":
        return sess.game_state(str(args.get("path") or ""))
    return sess.errors()


async def _play(args: dict[str, Any]) -> dict[str, Any]:
    import asyncio

    from .play_worker.errors import PlayError

    action = _action(args, ("start", "status", "frame", "input", "wait", "state", "errors", "stop", "list"))
    project_dir = None
    if action == "start":
        from .. import playground as pg

        slug = _slug(args)
        await _need_project(slug)
        _refuse_if_full_auto(slug)
        project_dir = pg.project_dir(slug)
    try:
        return await asyncio.to_thread(_play_sync, args, action, project_dir)   # sessions block: never on the loop
    except PlayError as exc:
        return exc.payload()


# ---- runs -----------------------------------------------------------------------------------------------------------

async def _runs(args: dict[str, Any]) -> dict[str, Any]:
    from . import runs

    action = _action(args, ("list", "get", "events"))
    limit = max(1, min(200, int(args.get("limit") or 50)))
    if action == "list":
        rows = runs.list_runs(slug=str(args.get("slug") or ""), kind=str(args.get("kind") or ""),
                              state=str(args.get("state") or ""), limit=limit)
        return _ok(runs=rows)
    rid = str(args.get("id") or "").strip()
    run = runs.get(rid) if rid else None
    if not run:
        raise ToolRefused("not_found", "no run with that id")
    if action == "get":
        return _ok(run=run)
    return _ok(run_id=rid, events=runs.events(rid, after=int(args.get("after") or 0), limit=limit))


# ---- ship requests --------------------------------------------------------------------------------------------------

class _ShipLock:
    """The ship list is one JSON value that the brain (maybe in another process) and the Studio both update:
    the read-modify-write holds this thread lock and, where the platform has it, a file lock beside the KV data."""

    def __enter__(self):
        _ship_lock.acquire()
        self._fh = None
        try:
            import fcntl

            from ..paths import data_root

            path = data_root() / "kv" / "ship_requests.lock"
            path.parent.mkdir(parents=True, exist_ok=True)
            self._fh = open(path, "a+")
            fcntl.flock(self._fh, fcntl.LOCK_EX)
        except ImportError:                    # Windows: one server process, the thread lock is the guard
            self._fh = None
        except Exception:
            if self._fh:
                self._fh.close()
            self._fh = None
        return self

    def __exit__(self, *exc):
        try:
            if self._fh:
                import fcntl

                fcntl.flock(self._fh, fcntl.LOCK_UN)
                self._fh.close()
        finally:
            _ship_lock.release()


def _ship_load() -> list[dict[str, Any]]:
    from .. import kv

    rows = kv.get(_SHIP_KEY)
    return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []


def _ship_save(rows: list[dict[str, Any]]) -> None:
    from .. import kv

    # Pending requests are never dropped; only the oldest answered ones go when the list grows.
    pending = [r for r in rows if r.get("status") == "pending"]
    answered = [r for r in rows if r.get("status") != "pending"]
    keep_answered = answered[-max(0, _SHIP_KEEP - len(pending)):] if len(pending) < _SHIP_KEEP else []
    keep = {id(r) for r in pending + keep_answered}
    kv.set(_SHIP_KEY, [r for r in rows if id(r) in keep])


def list_ship_requests(*, slug: str = "", status: str = "") -> list[dict[str, Any]]:
    rows = _ship_load()
    if slug:
        rows = [r for r in rows if r.get("slug") == slug]
    if status:
        rows = [r for r in rows if r.get("status") == status]
    return list(reversed(rows))                     # newest first


def resolve_ship_request(req_id: str, outcome: str) -> dict[str, Any]:
    """The owner's answer (Studio route only). ``approved`` means the owner chose to run it with the Studio's own button;
    that action asks its own questions and shows its own result, which this record does not claim."""
    if outcome not in ("approved", "declined"):
        return {"ok": False, "error": "bad_outcome"}
    with _ShipLock():
        rows = _ship_load()
        row = next((r for r in rows if r.get("id") == req_id), None)
        if row is None:
            return {"ok": False, "error": "not_found"}
        if row.get("status") != "pending":
            return {"ok": False, "error": "already_resolved", "request": row}
        row["status"] = outcome
        row["resolved_at"] = time.time()
        _ship_save(rows)
    return {"ok": True, "request": row}


async def _ship(args: dict[str, Any], session_id: str) -> dict[str, Any]:
    action = _action(args, ("request", "list"))
    if action == "list":
        return _ok(requests=list_ship_requests(slug=str(args.get("slug") or ""), status=str(args.get("status") or "")))
    slug = _slug(args)
    kind = str(args.get("kind") or "").strip().lower()
    if kind not in SHIP_KINDS:
        raise ToolRefused("bad_kind", "kind must be " + " | ".join(SHIP_KINDS))
    await _need_project(slug)
    with _ShipLock():
        rows = _ship_load()
        same = next((r for r in rows if r.get("status") == "pending" and r.get("slug") == slug and r.get("kind") == kind), None)
        if same is not None:                   # asking again does not stack up requests for the owner
            return _ok(request=same, note="Already asked; the owner has not answered yet. Nothing was published.")
        if sum(1 for r in rows if r.get("status") == "pending") >= _SHIP_KEEP:
            raise ToolRefused("too_many_pending", "too many requests wait for the owner; ask again after they answer")
        row = {"id": "ship-" + uuid.uuid4().hex[:10], "slug": slug, "kind": kind,
               "note": str(args.get("note") or "")[:500], "status": "pending", "created_at": time.time(),
               "session_id": str(session_id or "")[:80]}
        rows.append(row)
        _ship_save(rows)
    return _ok(request=row, note="Recorded. The owner confirms or declines it in the Studio; nothing was published.")


# ---- entry ----------------------------------------------------------------------------------------------------------

async def run(name: str, args: dict[str, Any], *, session_id: str = "") -> dict[str, Any]:
    args = args if isinstance(args, dict) else {}
    try:
        if name == "milestones":
            return await _milestones(args)
        if name == "preview":
            return await _preview(args)
        if name == "play":
            return await _play(args)
        if name == "runs":
            return await _runs(args)
        if name == "ship_request":
            return await _ship(args, session_id)
        return {"ok": False, "error": "unknown_tool"}
    except ToolRefused as exc:
        return {"ok": False, "error": exc.code, "message": str(exc)}


def format_result(name: str, args: dict[str, Any], res: dict[str, Any]) -> str:
    """The text the brain reads: a header line with OK or FAILED, then the JSON."""
    status = "OK" if res.get("ok") else "FAILED"
    action = str((args or {}).get("action") or "")
    return f"── {name} {action} {status} ──\n" + json.dumps(res, ensure_ascii=False, default=str)[:8000]
