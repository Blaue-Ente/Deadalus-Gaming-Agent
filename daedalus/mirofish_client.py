"""HTTP client for an optional MiroFish-Offline sidecar.

Upstream: https://github.com/nikmcfly/MiroFish-Offline (AGPL-3.0).

MiroFish is a local multi-agent social simulation (Neo4j + Ollama): upload a
press release / GDD, spawn hundreds of personas, get a public-reaction report.

Daedalus MUST NOT copy that backend. We talk to it over HTTP if the owner
runs the Docker stack themselves. AGPL obligations stay with the sidecar
process, not this MIT tree.
"""
from __future__ import annotations

import logging
import os
from typing import Any

log = logging.getLogger(__name__)

DEFAULT_API = "http://127.0.0.1:5001"
DEFAULT_UI = "http://127.0.0.1:3000"
_TIMEOUT = 20.0


def api_base() -> str:
    return (os.environ.get("MIROFISH_URL") or DEFAULT_API).rstrip("/")


def ui_base() -> str:
    return (os.environ.get("MIROFISH_UI") or DEFAULT_UI).rstrip("/")


def _headers() -> dict[str, str]:
    token = (os.environ.get("MIROFISH_TOKEN") or "").strip()
    h = {"Accept": "application/json"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


async def ping() -> dict[str, Any]:
    """Probe Flask ``/health`` then the UI port. Never raises."""
    import httpx
    api = api_base()
    ui = ui_base()
    out: dict[str, Any] = {
        "ok": False,
        "api": api,
        "ui": ui,
        "source": "https://github.com/nikmcfly/MiroFish-Offline",
        "license": "AGPL-3.0 — run as a separate process, do not vendor",
    }
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:
            try:
                r = await client.get(api + "/health", headers=_headers())
                out["health"] = r.json() if "json" in (r.headers.get("content-type") or "") else {"status_code": r.status_code}
                out["ok"] = r.status_code < 400
            except Exception as exc:
                out["health_error"] = str(exc)[:200]
            try:
                ui_r = await client.get(ui + "/", headers={"Accept": "text/html"})
                out["ui_up"] = ui_r.status_code < 500
            except Exception as exc:
                out["ui_error"] = str(exc)[:160]
                out["ui_up"] = False
    except Exception as exc:
        out["error"] = str(exc)[:200]
    return out


async def list_projects(limit: int = 20) -> dict[str, Any]:
    import httpx
    url = f"{api_base()}/api/graph/project/list"
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            r = await client.get(url, params={"limit": limit}, headers=_headers())
            data = r.json() if r.content else {}
            return {"ok": r.status_code < 400, "status_code": r.status_code, **(data if isinstance(data, dict) else {"data": data})}
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:200], "reachable": False}


async def list_simulations() -> dict[str, Any]:
    import httpx
    url = f"{api_base()}/api/simulation/list"
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            r = await client.get(url, headers=_headers())
            data = r.json() if r.content else {}
            return {"ok": r.status_code < 400, "status_code": r.status_code, **(data if isinstance(data, dict) else {"data": data})}
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:200], "reachable": False}


def press_release_form(
    text: str,
    *,
    requirement: str = "",
    project_name: str = "Daedalus GDD",
) -> tuple[dict[str, str], dict[str, tuple[str, bytes, str]]]:
    """Build the multipart body for ``POST /api/graph/ontology/generate``."""
    body = (text or "").strip()
    if not body:
        raise ValueError("text required")
    req = (requirement or "").strip() or (
        "Simulate public reaction to this game design / press release. "
        "Track sentiment, confusion, and whether people would try the game."
    )
    form = {
        "simulation_requirement": req,
        "project_name": project_name or "Daedalus GDD",
    }
    files = {"files": ("brief.md", body.encode("utf-8"), "text/markdown")}
    return form, files


async def submit_document(
    text: str,
    *,
    requirement: str = "",
    project_name: str = "Daedalus GDD",
) -> dict[str, Any]:
    """Upload GDD / press-release text to the sidecar (ontology step).

    Full swarm (prepare → start → report) stays in MiroFish. We only kick off
    the first API step so the owner can continue in the UI at :3000.
    """
    import httpx
    form, files = press_release_form(text, requirement=requirement, project_name=project_name)
    url = f"{api_base()}/api/graph/ontology/generate"
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            r = await client.post(url, data=form, files=files, headers=_headers())
            data = r.json() if r.content else {}
            ok = r.status_code < 400 and (not isinstance(data, dict) or data.get("success", True))
            return {
                "ok": ok,
                "status_code": r.status_code,
                "ui": ui_base(),
                "next": "Open the MiroFish UI to build the graph and run the swarm.",
                **(data if isinstance(data, dict) else {"data": data}),
            }
    except Exception as exc:
        return {
            "ok": False,
            "error": str(exc)[:240],
            "reachable": False,
            "hint": f"Start MiroFish-Offline (Docker) so {api_base()} /health answers.",
        }


def format_status(ping_result: dict[str, Any]) -> str:
    if ping_result.get("ok"):
        return f"MiroFish sidecar UP at {ping_result.get('api')} (UI {ping_result.get('ui')})"
    err = ping_result.get("health_error") or ping_result.get("error") or "unreachable"
    return f"MiroFish sidecar DOWN ({err}). AGPL sidecar — clone nikmcfly/MiroFish-Offline, docker compose up."
