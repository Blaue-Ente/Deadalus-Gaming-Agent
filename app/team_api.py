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
    member = team.current() or team.OWNER        # loopback without a token never reaches here (middleware)
    return {"ok": True, **team.public(member), "roles": list(team.ROLES)}


@router.get("/members")
async def list_members() -> dict:
    return {"ok": True, "owner": team.public(team.OWNER), "members": [team.public(m) for m in team.members()]}


@router.post("/members")
async def invite(body: InviteBody) -> dict:
    try:
        member, token = team.invite(body.name, body.role)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    # The token is shown once, here; only its hash is kept.
    return {"ok": True, "member": team.public(member), "token": token, "shown_once": True}


@router.post("/members/{member_id}")
async def update(member_id: str, body: UpdateBody) -> dict:
    try:
        member = team.update(member_id, role=body.role, disabled=body.disabled, name=body.name)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="member_not_found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "member": team.public(member)}


@router.get("/audit")
async def audit(limit: int = 200) -> dict:
    return {"ok": True, "entries": team.audit_tail(limit)}
