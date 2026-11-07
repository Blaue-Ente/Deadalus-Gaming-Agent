"""A1: the read-only capability registry and the no-loss parity table."""
from __future__ import annotations

import json
import warnings

import pytest
from a0_support import a0_tmp  # noqa: F401
from fastapi.testclient import TestClient


@pytest.fixture
def app_and_caps(a0_tmp):
    from app.main import app
    from daedalus.harness.registry import build_registry

    return app, build_registry(app)


def test_every_native_tool_is_a_brain_exposed_capability(app_and_caps):
    from daedalus.native_tools import NATIVE_TOOL_SPECS, PERM_MAP

    _app, caps = app_and_caps
    tools = {c.id: c for c in caps if c.kind == "tool"}
    assert set(tools) == {f"tool:{s['name']}" for s in NATIVE_TOOL_SPECS}
    for spec in NATIVE_TOOL_SPECS:
        cap = tools[f"tool:{spec['name']}"]
        assert cap.brain["exposed"] is True and cap.brain["tool_name"] == spec["name"]
        assert cap.permission_class == PERM_MAP.get(spec["name"])
        assert cap.status == "implemented" and cap.cost in ("none", "llm", "external")


def test_playbooks_listed_for_tools_match_task_playbooks(app_and_caps):
    from daedalus import task_playbooks as tp

    _app, caps = app_and_caps
    core = {c.id for c in caps if c.id in ("tool:write_file", "tool:read_file")}
    assert core == {"tool:write_file", "tool:read_file"}
    ids = {pb.id for pb in tp.PLAYBOOKS}
    for cap in caps:
        if cap.kind == "tool":
            assert set(cap.brain["playbooks"]) <= ids
    write = next(c for c in caps if c.id == "tool:write_file")
    assert write.brain["playbooks"] == sorted(ids)  # write_file is in the core set of every build subset


def test_every_served_route_is_registered(app_and_caps):
    """Cross-check against the app's own OpenAPI schema, which does not depend on router internals."""
    import re

    app, caps = app_and_caps
    # OpenAPI drops path converters ({path:path} -> {path}); compare on the stripped form.
    strip = lambda text: re.sub(r"\{(\w+):\w+\}", r"{\1}", text)  # noqa: E731
    registered = {strip(h) for c in caps if c.kind == "route" for h in c.http}
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        paths = app.openapi()["paths"]
    missing = [
        f"{m.upper()} {p}"
        for p, ops in paths.items()
        for m in ops
        if m.upper() in ("GET", "POST", "PUT", "PATCH", "DELETE") and f"{m.upper()} {p}" not in registered
    ]
    assert missing == []


def test_catch_all_is_marked_stub_and_new_routes_present(app_and_caps):
    _app, caps = app_and_caps
    by_id = {c.id: c for c in caps}
    stub = by_id["route:GET /admin/daedalus/{rest:path}"]
    assert stub.status == "stub" and "404" in stub.title
    assert "route:GET /api/v1/harness/capabilities" in by_id
    assert by_id["route:POST /admin/daedalus/outbox/{item_id}/apply"].status == "partial"
    assert by_id["route:POST /api/v1/daedalus/mentor"].ui_home == "panel:settings"
    assert by_id["route:GET /api/v1/playground/projects"].surface == "api-v1"
    assert by_id["route:GET /admin/daedalus/blockers"].surface == "legacy-admin"


def test_every_parity_row_resolves(app_and_caps):
    from daedalus.harness.parity import PARITY_ROWS, check_parity

    _app, caps = app_and_caps
    assert check_parity(caps) == []
    assert len({r.id for r in PARITY_ROWS}) == len(PARITY_ROWS) >= 45


def test_parity_detects_a_broken_row(app_and_caps, monkeypatch):
    from daedalus.harness import parity

    _app, caps = app_and_caps
    bad = parity.ParityRow("x", "x", ("classic",), "panel:nope", ("GET /nope",), "wat")
    monkeypatch.setattr(parity, "PARITY_ROWS", parity.PARITY_ROWS + (bad,))
    problems = parity.check_parity(caps)
    assert any("home 'panel:nope'" in p for p in problems)
    assert any("route not served: GET /nope" in p for p in problems)
    assert any("unknown disposition" in p for p in problems)


def test_every_legacy_only_function_has_a_planned_or_held_home(app_and_caps):
    """No-loss: a legacy-only row names a home that exists, and in_studio rows name an implemented one."""
    from daedalus.harness.parity import PARITY_ROWS

    _app, caps = app_and_caps
    status = {c.id: c.status for c in caps}
    for row in PARITY_ROWS:
        if row.disposition == "in_studio":
            assert status[row.home] in ("implemented", "partial"), row.id
        elif row.disposition.startswith(("port:", "held:")):
            assert status[row.home] in ("planned", "implemented", "partial"), row.id
        else:
            assert row.disposition.startswith("retire:") and row.home is None


def test_registry_is_deterministic_and_json_safe(app_and_caps):
    from daedalus.harness.registry import build_registry, registry_payload

    app, caps = app_and_caps
    again = build_registry(app)
    assert [c.id for c in caps] == [c.id for c in again]
    payload = registry_payload(app)
    assert json.loads(json.dumps(payload))["count"] == len(caps)
    kinds = {c.kind for c in caps}
    assert kinds == {"tool", "route", "panel", "setting", "workflow"}
    assert len({c.id for c in caps}) == len(caps)


def test_endpoints_serve_and_filter(a0_tmp):
    from app.main import app

    client = TestClient(app)
    res = client.get("/api/v1/harness/capabilities")
    assert res.status_code == 200 and res.json()["ok"] is True
    body = res.json()
    assert body["count"] == sum(body["by_kind"].values())
    tools = client.get("/api/v1/harness/capabilities?kind=tool").json()
    assert tools["count"] == body["by_kind"]["tool"] and all(c["kind"] == "tool" for c in tools["capabilities"])
    planned = client.get("/api/v1/harness/capabilities?status=planned").json()
    assert planned["count"] > 0 and all(c["status"] == "planned" for c in planned["capabilities"])
    parity = client.get("/api/v1/harness/parity").json()
    assert parity["ok"] is True and parity["problems"] == []
    assert parity["by_disposition"]["in_studio"] >= 20 and parity["by_disposition"]["retire"] == 4


def test_endpoints_require_the_owner_token(a0_tmp, raw_http):
    from app.main import app

    client = TestClient(app)
    for path in ("/api/v1/harness/capabilities", "/api/v1/harness/parity"):
        assert client.get(path).status_code == 401
