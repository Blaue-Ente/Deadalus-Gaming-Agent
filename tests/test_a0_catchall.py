"""A0-g: unknown /admin/daedalus/* routes answer an honest 404 (still JSON, still wired:false)."""
from __future__ import annotations

import pytest
from a0_support import a0_tmp  # noqa: F401
from fastapi.testclient import TestClient

DEAD = [  # buttons the legacy Playground page calls that no handler serves (audit §1.1)
    ("POST", "/admin/daedalus/playground/api/ai-build"),
    ("POST", "/admin/daedalus/playground/api/projects/x/revive-pipeline"),
    ("POST", "/admin/daedalus/playground/api/projects/x/netlify-deploy"),
    ("GET", "/admin/daedalus/playground/api/projects/x/assets/raw"),
    ("POST", "/admin/daedalus/playground/api/projects/x/publish"),
    ("DELETE", "/admin/daedalus/definitely-not-a-route"),
]


@pytest.mark.parametrize("method,path", DEAD)
def test_unknown_admin_route_is_404_json(a0_tmp, method, path):
    res = TestClient(__import__("app.main", fromlist=["app"]).app).request(method, path)
    assert res.status_code == 404, (method, path, res.status_code)
    body = res.json()
    assert body["ok"] is False and body["wired"] is False and body["error"] == "not_wired"
    assert body["path"] == path and body["method"] == method
    assert body["items"] == [] and "does not exist" in body["message"]


def test_real_routes_are_not_shadowed(a0_tmp):
    from app.main import app

    client = TestClient(app)
    for path in (
        "/admin/daedalus/mentor",
        "/admin/daedalus/run-budget",
        "/admin/daedalus/writer-assist",
        "/admin/daedalus/skills/api",
        "/admin/daedalus/proposals/api",
        "/admin/daedalus/bundles/api",
        "/admin/daedalus/permissions",
        "/admin/daedalus/outbox/api",
    ):
        res = client.get(path)
        assert res.status_code == 200, (path, res.status_code)
        assert res.json().get("wired") is not False, path
