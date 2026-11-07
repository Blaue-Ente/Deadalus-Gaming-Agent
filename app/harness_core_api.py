"""Harness Core HTTP API (read-only in A1): the capability registry and the no-loss parity table."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

router = APIRouter(tags=["harness-core"])


@router.get("/api/v1/harness/capabilities")
def harness_capabilities(request: Request, kind: str = "", status: str = "") -> dict[str, Any]:
    """Every tool, route, panel, setting and workflow, generated from the code that defines them."""
    from daedalus.harness.registry import registry_payload

    # Plain def on purpose: building the registry imports modules, so FastAPI runs it in a threadpool.
    return registry_payload(request.app, kind=kind.strip(), status=status.strip())


@router.get("/api/v1/harness/parity")
def harness_parity(request: Request) -> dict[str, Any]:
    """The no-loss parity table and any rows that do not resolve against the registry."""
    from daedalus.harness.parity import PARITY_ROWS, check_parity
    from daedalus.harness.registry import build_registry

    errors: list[str] = []
    problems = errors + check_parity(build_registry(request.app, errors))
    by_disposition: dict[str, int] = {}
    for row in PARITY_ROWS:
        by_disposition[row.disposition] = by_disposition.get(row.disposition, 0) + 1
    return {
        "ok": not problems,
        "problems": problems,
        "count": len(PARITY_ROWS),
        "by_disposition": by_disposition,
        "rows": [r.as_dict() for r in PARITY_ROWS],
    }


class IntentBody(BaseModel):
    intent: str = Field(..., min_length=1, max_length=40)
    model: str = Field(default="", max_length=200)  # "provider:model"; empty clears the owner's choice


@router.get("/api/v1/harness/intents")
def harness_intents() -> dict[str, Any]:
    """Which model serves each LLM intent, and which of them the owner may change."""
    from daedalus.harness import gateway as gw

    rows: list[dict[str, Any]] = []
    overrides = gw.get_overrides()
    for intent in gw.INTENTS:
        sel = gw._SELECTOR.get(intent, intent)
        row: dict[str, Any] = {"intent": intent, "selectable": sel in gw.OVERRIDABLE or intent == "mentor"}
        if intent == "mentor":
            # The mentor keeps its own choice (Studio header); this view reports it and POST delegates to it.
            from daedalus import mentor_helm

            choice = mentor_helm.get_choice()
            row.update({"owner_choice": choice.get("id"), "resolved": {
                "provider": choice.get("provider"), "model": choice.get("model"),
                "source": "mentor-choice" if choice.get("source") != "env" else "env-lock",
                "locked": choice.get("source") == "env"}})
        else:
            res = gw.resolve(intent)
            row.update({"owner_choice": overrides.get(sel), "default": gw.INTENT_DEFAULTS.get(intent),
                        "resolved": res.public()})
        rows.append(row)
    return {"ok": True, "env_locked": gw.model_locked_by_env(), "intents": rows}


@router.post("/api/v1/harness/intents")
def harness_intents_set(body: IntentBody) -> Any:
    """Set (or clear) the owner's model for writer/persona, critic, planner, or the mentor."""
    from daedalus.env_lock import EnvLockedError
    from daedalus.harness import gateway as gw

    intent = gw.canonical_intent(body.intent)
    try:
        if intent == "mentor":
            from daedalus.mentor import apply_update
            from daedalus.model_router import parse_provider_model_tag

            if not body.model.strip():
                apply_update({"choice_id": "auto"})  # clearing the owner's choice = the mentor's automatic pick
            else:
                prov, mid = parse_provider_model_tag(body.model)
                if not (prov and mid):
                    raise ValueError("model must look like provider:model")
                apply_update({"provider": prov, "model": mid})
        else:
            gw.set_override(intent, body.model)
    except EnvLockedError as exc:
        return JSONResponse(exc.payload(), status_code=409)
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
    return harness_intents()
