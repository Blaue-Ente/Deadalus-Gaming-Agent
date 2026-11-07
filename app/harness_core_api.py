"""Harness Core HTTP API (read-only in A1): the capability registry and the no-loss parity table."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

router = APIRouter(tags=["harness-core"])


@router.get("/api/v1/harness/capabilities")
async def harness_capabilities(request: Request, kind: str = "", status: str = "") -> dict[str, Any]:
    """Every tool, route, panel, setting and workflow, generated from the code that defines them."""
    from daedalus.harness.registry import registry_payload

    return registry_payload(request.app, kind=kind.strip(), status=status.strip())


@router.get("/api/v1/harness/parity")
async def harness_parity(request: Request) -> dict[str, Any]:
    """The no-loss parity table and any rows that do not resolve against the registry."""
    from daedalus.harness.parity import PARITY_ROWS, check_parity
    from daedalus.harness.registry import build_registry

    problems = check_parity(build_registry(request.app))
    by_disposition: dict[str, int] = {}
    for row in PARITY_ROWS:
        key = row.disposition.split(":")[0] if row.disposition != "in_studio" else "in_studio"
        by_disposition[key] = by_disposition.get(key, 0) + 1
    return {
        "ok": not problems,
        "problems": problems,
        "count": len(PARITY_ROWS),
        "by_disposition": by_disposition,
        "rows": [r.as_dict() for r in PARITY_ROWS],
    }
