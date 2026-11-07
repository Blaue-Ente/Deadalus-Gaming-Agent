"""Settings -> workers, provider test, connection tests, MCP toggles, durability, vision test.

Everything here answers with CODES and numbers, not prose, so the Studio can say it in the owner's language. No
secret is ever returned (a key is reported only as "set"), and every model call goes through the gateway, so the
budget gate, the typed errors and the ledger all apply."""
from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any


# ---- workers ---------------------------------------------------------------------------------------------
_inflight: set[str] = set()          # one billed test of each kind at a time: a double click or a script cannot drain the budget


def _keyless_ok(cfg: dict[str, Any]) -> bool:
    """No key stored, but the gateway would still call (a local or loopback provider takes none)."""
    from . import gateway as gw

    return (not cfg.get("api_key")) and gw.has_credentials(cfg)


def workers_view() -> dict[str, Any]:
    from ..llm_config import get_config
    from ..workers import WORKER_SLOTS, get_worker_config

    try:
        master = get_config()
    except Exception as exc:
        master = {"error": str(exc)[:120]}
    rows = [{"slot": "master", "label": "master", "provider": str(master.get("provider") or ""),
             "model": str(master.get("model") or ""), "enabled": bool(master.get("model")),
             "source": str(master.get("source") or ""), "key_set": bool(master.get("api_key")),
             "keyless_ok": _keyless_ok(master)}]
    try:
        cfg = get_worker_config()
    except Exception:
        cfg = {}
    for slot, meta in WORKER_SLOTS.items():
        sc = cfg.get(slot) or {}
        if not sc.get("model"):
            continue
        rows.append({"slot": slot, "label": str(meta.get("label") or slot), "provider": str(sc.get("provider") or ""),
                     "model": str(sc.get("model") or ""), "enabled": bool(sc.get("enabled", True)),
                     "source": str(sc.get("source") or ""), "key_set": bool(sc.get("api_key")),
                     "keyless_ok": _keyless_ok(sc)})
    return {"ok": True, "slots": rows}


async def test_provider(slot: str = "master") -> dict[str, Any]:
    """One tiny call through the gateway (intent ``test``) with the SAVED slot configuration; never a key from the
    browser. Typed outcome: ok, or {error: {type, message}} (auth, quota, timeout, budget_blocked, ...)."""
    from . import gateway as gw

    slot = (slot or "master").strip()
    key = "provider:" + slot
    if key in _inflight:
        return {"ok": False, "slot": slot, "error": {"type": "busy", "message": "a test is already running"}}
    _inflight.add(key)
    try:
        return await _test_provider(gw, slot)
    finally:
        _inflight.discard(key)


async def _test_provider(gw: Any, slot: str) -> dict[str, Any]:
    route = None
    if slot != "master":
        from ..workers import get_worker_config

        sc = (get_worker_config() or {}).get(slot) or {}
        if not sc.get("model"):
            return {"ok": False, "slot": slot, "error": {"type": "not_configured", "message": "no model for this slot"}}
        if not sc.get("enabled", True):
            return {"ok": False, "slot": slot, "error": {"type": "disabled", "message": "slot is disabled"}}
        if not gw.has_credentials(sc):
            return {"ok": False, "slot": slot, "error": {"type": "not_configured", "message": "no key for this slot"}}
        route = gw.route_for(str(sc.get("provider") or ""), str(sc["model"]), str(sc.get("api_key") or ""),
                             str(sc.get("base_url") or ""), str(sc.get("sdk") or "openai"))
    else:
        try:
            from ..llm_config import get_config

            cfg = get_config()
        except Exception as exc:
            return {"ok": False, "slot": slot, "error": {"type": "not_configured", "message": str(exc)[:120]}}
        if not cfg.get("model") or not gw.has_credentials(cfg):
            return {"ok": False, "slot": slot, "error": {"type": "not_configured", "message": "no model or key"}}
    res = await gw.call("test", [{"role": "user", "content": "Reply with exactly: OK"}], max_tokens=16,
                        temperature=0.0, route=route, use_cache=False, client_id="daedalus/settings-test",
                        timeout=25.0)
    out: dict[str, Any] = {"ok": bool(res.ok), "slot": slot, "provider": res.provider, "model": res.model,
                           "latency_ms": res.latency_ms, "cost_known": res.cost_known}
    if res.ok:
        out["reply"] = (res.text or "").strip()[:80]
    else:
        out["budget_stopped"] = bool(res.budget_stopped)
        out["error"] = res.error.as_dict() if res.error else {"type": "provider_error", "message": "call failed"}
    return out


# ---- git connections -------------------------------------------------------------------------------------
def _code(result: dict[str, Any]) -> str:
    """The connector tests report a structured ``code``; prose is never parsed back into one."""
    code = str(result.get("code") or "")
    if code in {"ok", "not_configured", "invalid_token", "forbidden", "api_error", "network"}:
        return code
    return "ok" if result.get("ok") else ("network" if result.get("configured") else "not_configured")


def _run_blocking(coro_fn: Any) -> dict[str, Any]:
    return asyncio.run(coro_fn())              # the connector tests use blocking HTTP: run them on their own loop


async def test_git(target: str) -> dict[str, Any]:
    from .. import connectors_config as cc

    if target == "github":
        raw = await asyncio.to_thread(_run_blocking, cc.test_github_connection)
        extra = {"login": raw.get("login"), "can_push": raw.get("can_push"), "source": raw.get("source")}
    elif target == "gitlab":
        raw = await asyncio.to_thread(_run_blocking, cc.test_gitlab_connection)
        extra = {"username": raw.get("username"), "host": raw.get("host")}
    else:
        raise KeyError(target)
    return {"ok": bool(raw.get("ok")), "target": target, "code": _code(raw), "configured": bool(raw.get("configured")),
            **{k: v for k, v in extra.items() if v not in (None, "")}}


# ---- MCP -------------------------------------------------------------------------------------------------
def mcp_view() -> dict[str, Any]:
    from .. import addons

    rows = [{"id": r["id"], "enabled": bool(r.get("enabled")),
             "blocked_offline": bool(r.get("blocked_offline"))}
            for r in addons.catalog() if r.get("kind") == "mcp"]
    return {"ok": True, "servers": rows}


def set_mcp(addon_id: str, enabled: bool) -> dict[str, Any]:
    """Catalog servers only: the browser can never name a command to run."""
    from .. import addons

    rows = {r["id"]: r for r in addons.catalog() if r.get("kind") == "mcp"}
    if addon_id not in rows:
        raise KeyError(addon_id)
    if enabled and rows[addon_id].get("blocked_offline"):
        return {"ok": False, "id": addon_id, "error": "blocked_offline"}      # the UI disables the button; the server holds too
    res = addons.enable(addon_id) if enabled else addons.disable(addon_id)
    if not res.get("ok"):
        return {"ok": False, "id": addon_id, "error": str(res.get("error") or "failed")[:160]}
    return {"ok": True, "id": addon_id, "enabled": bool(enabled), **mcp_view()}


# ---- durability ------------------------------------------------------------------------------------------
async def durability_view() -> dict[str, Any]:
    from .. import playground as pg
    from .. import playground_workspace_backup as backup
    from ..connectors_config import storage_status

    projects = await pg.list_projects()

    def count_empty() -> int:
        empty = 0
        for p in projects:
            try:
                if backup.workspace_file_count(str(p.get("slug") or "")) == 0:
                    empty += 1
            except Exception:
                continue
        return empty

    empty = await asyncio.to_thread(count_empty)      # a recursive scan per project: not on the event loop
    published = 0
    try:
        from ..playground_publish import list_published

        published = len(await list_published(enabled_only=False))
    except Exception:
        pass
    storage = storage_status()
    return {"ok": True, "storage_configured": bool(storage.get("configured")), "projects": len(projects),
            "empty_workspaces": empty, "published": published}


# ---- vision ----------------------------------------------------------------------------------------------
async def vision_test() -> dict[str, Any]:
    """Render a random label, ask the CURRENT mentor model to read it (one model call through the gateway) and store
    the verdict. The label is not returned; ``answer`` is what the model said."""
    if "vision" in _inflight:
        return {"ok": False, "perceived": False, "error": "busy", "recorded": False, "provider": "", "model": "",
                "answer": "", "latency_ms": 0}
    _inflight.add("vision")
    try:
        return await _vision_test()
    finally:
        _inflight.discard("vision")


async def _vision_test() -> dict[str, Any]:
    import tempfile

    from .. import mentor_helm
    from . import label_image

    label = label_image.new_label()
    with tempfile.TemporaryDirectory(prefix="daedalus-vision-") as tmp:
        png = Path(tmp) / "probe.png"
        png.write_bytes(label_image.render_png(label))
        started = time.time()
        res = await mentor_helm.probe_vision(png, label)
    return {"ok": bool(res.get("ok")), "perceived": bool(res.get("perceived")),
            "provider": str(res.get("provider") or ""), "model": str(res.get("model") or ""),
            "recorded": bool(res.get("recorded")), "answer": str(res.get("answer") or "")[:80],
            "error": str(res.get("error") or ""), "latency_ms": int((time.time() - started) * 1000)}
