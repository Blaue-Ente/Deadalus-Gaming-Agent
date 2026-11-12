"""Gap 3 (owner, 2026-11-12, plan approved): a team in one Daedalus. Roles owner > designer > tester; default deny."""
from __future__ import annotations

import asyncio
import json
import re

import pytest
from a0_support import a0_tmp  # noqa: F401
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from daedalus import team


def _routes() -> set[tuple[str, str]]:
    from app.main import app

    rows: set[tuple[str, str]] = set()
    for x in app.routes:
        if type(x).__name__ == "_IncludedRouter":
            ctx = getattr(x, "include_context", None)
            prefix = getattr(ctx, "prefix", "") if ctx else ""
            items = [(prefix, r) for r in x.original_router.routes]
        else:
            items = [("", x)]
        for prefix, r in items:
            if isinstance(r, APIRoute):
                rows |= {(meth, prefix + r.path) for meth in r.methods}
    return rows


def _sample(path: str) -> str:
    return re.sub(r"\{[^}]+\}", "x1", path)


def test_every_rule_names_a_real_route_and_the_rest_is_the_owners():
    routes = _routes()
    assert len(routes) > 300
    for methods, rx, role in team._COMPILED:                   # no stale rule silently opening a future route
        assert any(m in methods and rx.match(_sample(p)) for m, p in routes), rx.pattern
    open_routes = {(m, p) for m, p in routes if team.required_role(m, _sample(p)) != "owner"}
    assert 40 < len(open_routes) < 120                         # a member's surface is a small, named part


@pytest.mark.parametrize("method,path", [
    ("POST", "/admin/daedalus/run-budget"), ("POST", "/api/v1/daedalus/run-budget"),
    ("POST", "/api/v1/harness/config"), ("POST", "/api/v1/harness/intents"), ("POST", "/api/v1/harness/connections"),
    ("GET", "/api/v1/harness/connections"), ("POST", "/api/v1/daedalus/shell"), ("POST", "/api/v1/playground/revive"),
    ("POST", "/api/v1/playground/projects/x1/publish-local"), ("POST", "/api/v1/playground/projects/x1/live"),
    ("DELETE", "/api/v1/playground/projects/x1"), ("POST", "/admin/daedalus/playground/api/projects/x1/delete"),
    ("POST", "/admin/daedalus/playground/api/projects/x1/push"), ("POST", "/admin/daedalus/playground/api/clone"),
    ("POST", "/apps/api/x1/delete"), ("GET", "/api/v1/team/members"), ("POST", "/api/v1/team/members"),
    ("GET", "/api/v1/team/audit"), ("POST", "/api/v1/harness/review/permissions/grant-all"),
    ("POST", "/admin/daedalus/upload-doc"), ("POST", "/api/v1/daedalus/effort"),
])
def test_settings_keys_publishing_git_and_people_are_the_owners(method, path):
    assert team.required_role(method, path) == "owner"


def test_a_designer_builds_and_a_tester_plays():
    designer, tester = team.Member("d", "Dee", "designer"), team.Member("t", "Tee", "tester")
    chat, file_w = ("POST", "/api/v1/daedalus/chat"), ("POST", "/admin/daedalus/playground/api/projects/frog/file")
    read, play = ("GET", "/api/v1/playground/projects/frog/file"), ("POST", "/api/v1/daedalus/playtest")
    assert all(team.allowed(designer, *c) for c in (chat, file_w, read, play))
    assert team.allowed(tester, *read) and team.allowed(tester, *play)
    assert not team.allowed(tester, *chat) and not team.allowed(tester, *file_w)
    assert not team.allowed(team.Member("t", "Tee", "tester", disabled=True), *read)
    assert team.allowed(team.OWNER, "POST", "/anything/new")


@pytest.fixture
def people(a0_tmp, raw_http):  # noqa: F811
    from app.main import app
    from daedalus.owner_auth import get_or_create_token

    client = TestClient(app)
    owner = {"Authorization": f"Bearer {get_or_create_token()}"}
    made = {}
    for name, role in (("Dee", "designer"), ("Tee", "tester")):
        res = client.post("/api/v1/team/members", json={"name": name, "role": role}, headers=owner)
        assert res.status_code == 200 and res.json()["shown_once"]
        made[role] = (res.json()["member"]["id"], {"Authorization": f"Bearer {res.json()['token']}"})
    return client, owner, made


def test_each_person_gets_what_their_role_allows(people):
    client, owner, made = people
    did, dh = made["designer"]
    tid, th = made["tester"]
    assert client.get("/api/v1/team/me", headers=dh).json()["role"] == "designer"
    assert client.get("/api/v1/team/me", headers=th).json()["name"] == "Tee"
    forbidden = client.post("/api/v1/daedalus/run-budget", json={"usd": 99}, headers=dh)
    assert forbidden.status_code == 403 and forbidden.json()["needs"] == "owner"
    assert client.get("/api/v1/team/members", headers=dh).status_code == 403
    assert client.get("/api/v1/playground/projects", headers=th).status_code == 200
    created = client.post("/api/v1/playground/projects", json={"name": "Frog", "kind": "static"}, headers=dh)
    assert created.status_code == 200, created.text
    slug = created.json().get("slug") or created.json().get("project", {}).get("slug")
    w = client.post(f"/admin/daedalus/playground/api/projects/{slug}/file",
                    json={"path": "index.html", "content": "<canvas></canvas>"}, headers=th)
    assert w.status_code == 403 and w.json()["role"] == "tester"
    assert client.get("/api/v1/team/members", headers=owner).status_code == 200


def test_a_disabled_member_or_an_unknown_token_is_refused(people):
    client, owner, made = people
    tid, th = made["tester"]
    assert client.post(f"/api/v1/team/members/{tid}", json={"disabled": True}, headers=owner).json()["member"][
        "disabled"]
    assert client.get("/api/v1/team/me", headers=th).status_code == 401
    assert client.get("/api/v1/team/me", headers={"Authorization": "Bearer dt_not-a-real-token"}).status_code == 401
    assert client.post("/api/v1/team/members", json={"name": "X", "role": "owner"}, headers=owner).status_code == 422


def test_tokens_are_never_stored_or_listed(people):
    client, owner, made = people
    from daedalus.paths import data_root

    listed = json.dumps(client.get("/api/v1/team/members", headers=owner).json())
    stored = (data_root() / "team.json").read_text()
    assert "dt_" not in listed and "token_sha256" not in listed and "dt_" not in stored
    assert (data_root() / "team.json").stat().st_mode & 0o077 == 0


def test_changes_are_audited_with_who_made_them(people):
    client, owner, made = people
    _, dh = made["designer"]
    client.post("/api/v1/daedalus/run-budget", json={"usd": 1}, headers=dh)        # refused: not a change
    client.post("/api/v1/playground/projects", json={"name": "Snake", "kind": "static"}, headers=dh)
    entries = client.get("/api/v1/team/audit", headers=owner).json()["entries"]
    mine = [e for e in entries if e["name"] == "Dee"]
    assert mine and mine[0]["path"] == "/api/v1/playground/projects" and mine[0]["status"] == 200
    assert not any(e["path"] == "/api/v1/daedalus/run-budget" for e in mine)


def test_the_bible_says_who_asked(a0_tmp):  # noqa: F811
    import types

    from daedalus import playground as pg
    from daedalus import project_bible as bible

    slug = asyncio.run(pg.create_project("Frog", kind="static"))["slug"]
    pg.write_file(slug, "GAME_BIBLE.md", "# Frog\n\n## Session log\n")
    tok = team.bind(team.Member("d", "Dee", "designer"))
    try:
        bible.log_turn(slug, "add lives", types.SimpleNamespace())
    finally:
        team.unbind(tok)
    bible.log_turn(slug, "faster frog", types.SimpleNamespace())
    text = bible.path(slug).read_text()
    assert "(by Dee, designer): add lives" in text and "UTC: faster frog" in text
