"""Daedalus mesh awareness layer.

Gives Daedalus live, read-only visibility into the Iyveris ecosystem:
- Which of the 8 sister apps are up right now (from health_monitor cache)
- What capabilities each one advertises
- Recent alerts (services that went down/recovered)

Injected as a compact snapshot into the LLM context on every chat turn,
so Daedalus naturally references real ecosystem state — e.g. "StockArgos
is degraded right now, want me to wait?"

Daedalus does NOT auto-route requests through the mesh from here; routing
is the job of /api/v1/mesh/ask (which has explicit user intent).
"""
from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)


def get_mesh_snapshot() -> dict[str, Any]:
    """Return a compact, fast snapshot of the Iyveris mesh state.

    Uses already-cached data from mesh_registry + health_monitor (no live HTTP).
    Safe to call on every turn.
    """
    try:
        from argos_data_nexus import mesh, health_monitor  # type: ignore
    except Exception as exc:
        return {"available": False, "error": f"mesh modules unavailable: {exc}"}

    try:
        reg = mesh._load_registry()
    except Exception as exc:
        return {"available": False, "error": f"registry load failed: {exc}"}

    services = reg.get("services", {})
    status_snapshot = health_monitor.get_status_snapshot()
    recent_alerts = health_monitor.get_alerts(limit=8)
    last_poll = health_monitor.get_last_poll()

    services_summary: list[dict[str, Any]] = []
    online = 0
    for svc_id, svc in services.items():
        if not svc.get("enabled", True):
            continue
        live_status = status_snapshot.get(svc_id) or svc.get("status", "unknown")
        if live_status in ("online", "active", "reachable_no_mesh"):
            online += 1
        services_summary.append({
            "id": svc_id,
            "name": svc.get("name", svc_id),
            "status": live_status,
            "capabilities": svc.get("capabilities", [])[:6],
            "description": (svc.get("description") or "")[:140],
        })

    return {
        "available": True,
        "total_services": len(services_summary),
        "online": online,
        "last_poll_unix": int(last_poll),
        "services": services_summary,
        "recent_alerts": [
            {
                "service": a.get("service"),
                "from": a.get("from"),
                "to": a.get("to"),
                "ts": a.get("ts"),
            }
            for a in recent_alerts
        ],
    }


def format_mesh_for_prompt(snap: dict[str, Any]) -> str:
    """Render the mesh snapshot as a compact text block for LLM injection."""
    if not snap or not snap.get("available"):
        return ""
    lines = [
        "━━━ IYVERIS MESH — LIVE STATE ━━━",
        f"Services: {snap.get('online', 0)}/{snap.get('total_services', 0)} up",
    ]
    for s in snap.get("services", []):
        caps = ", ".join(s.get("capabilities", [])[:4])
        lines.append(
            f"  • {s['id']:<14} [{s['status']:<18}] {s['name']} — {caps}"
        )
    alerts = snap.get("recent_alerts", [])
    if alerts:
        lines.append("Recent state changes:")
        for a in alerts[:5]:
            lines.append(f"  ! {a.get('service')}: {a.get('from')} → {a.get('to')}")
    return "\n".join(lines)
