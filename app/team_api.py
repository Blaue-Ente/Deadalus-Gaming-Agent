"""Team: who is signed in, and the owner's management of members (gap 3). Roles are enforced in OwnerAuthMiddleware
(daedalus.team.required_role): every route here but /me is the owner's."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from daedalus import team

router = APIRouter(prefix="/api/v1/team", tags=["team"])


class InviteBody(BaseModel):
    name: str = Field(..., min_length=1, max_length=60)
    role: str = Field(..., pattern="^(designer|tester)$")


class UpdateBody(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=60)
    role: str | None = Field(default=None, pattern="^(designer|tester)$")
    disabled: bool | None = None


@router.get("/me")
async def me() -> dict:
    member = team.current()
    if member is None:                            # fail closed: only the middleware says who this is
        raise HTTPException(status_code=401, detail="not signed in")
    return {"ok": True, **team.public(member), "roles": list(team.ROLES)}


@router.get("/members")
async def list_members() -> dict:
    return {"ok": True, "owner": team.public(team.OWNER), "members": [team.public(m) for m in team.members()]}


@router.post("/members")
async def invite(body: InviteBody) -> dict:
    try:
        member, token = team.invite(body.name, body.role)
    except team.TeamFileError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    # The token is shown once, here; only its hash is kept.
    trust = ("A designer's builds run code on this machine (tests, builds, game checks) until projects run in a "
             "sandbox: invite only someone you trust with it." if member.role == "designer" else
             "A tester reads and plays; nothing they do runs on this machine.")
    return {"ok": True, "member": team.public(member), "token": token, "shown_once": True, "trust": trust}


@router.post("/members/{member_id}")
async def update(member_id: str, body: UpdateBody) -> dict:
    try:
        member = team.update(member_id, role=body.role, disabled=body.disabled, name=body.name)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="member_not_found") from exc
    except team.TeamFileError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "member": team.public(member)}


@router.get("/audit")
async def audit(limit: int = 200) -> dict:
    return {"ok": True, "entries": team.audit_tail(limit)}


# ── personal keys and sharing (gap 3c) ──────────────────────────────────────────────────────────────────────────
class KeyBody(BaseModel):
    name: str = Field(..., min_length=3, max_length=64)
    value: str = Field(default="", max_length=4096)       # empty removes the key


class SharingBody(BaseModel):
    shared: list[str] | None = None
    caps: dict[str, float] | None = None
    reset_spend: list[str] | None = None


def _me() -> team.Member:
    member = team.current()
    if member is None:
        raise HTTPException(status_code=401, detail="not signed in")
    return member


@router.get("/keys")
async def my_keys() -> dict:
    """A member's own key names, what the owner shares, their cap and spend. Never a value."""
    from daedalus import credentials

    member = _me()
    if member.role == "owner":
        return {"ok": True, "owner": True, "available": list(credentials.member_key_names()),
                **credentials.sharing()}
    return {"ok": True, "owner": False, **credentials.status(member.id)}


@router.post("/keys")
async def set_my_key(body: KeyBody) -> dict:
    from daedalus import credentials

    member = _me()
    if member.role == "owner":
        raise HTTPException(status_code=400, detail="the owner's keys are set under Connectors")
    try:
        credentials.set_key(member.id, body.name, body.value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=409, detail="your key file cannot be read; nothing was changed") from exc
    return {"ok": True, **credentials.status(member.id)}


@router.get("/sharing")
async def get_sharing() -> dict:
    from daedalus import credentials

    return {"ok": True, "available": list(credentials.member_key_names()), "default_cap_usd":
            credentials.DEFAULT_CAP_USD, **credentials.sharing()}


@router.post("/sharing")
async def set_sharing(body: SharingBody) -> dict:
    from daedalus import credentials

    try:
        data = credentials.set_sharing(shared=body.shared, caps=body.caps, reset_spend=body.reset_spend)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=409, detail="the sharing file cannot be read; nothing was changed") from exc
    return {"ok": True, **data}
