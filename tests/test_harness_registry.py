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
    from daedalus.harness.registry import COSTS
    from daedalus.native_tools import NATIVE_TOOL_SPECS, PERM_MAP

    _app, caps = app_and_caps
    tools = {c.id: c for c in caps if c.kind == "tool"}
    assert set(tools) == {f"tool:{s['name']}" for s in NATIVE_TOOL_SPECS}
    for spec in NATIVE_TOOL_SPECS:
        cap = tools[f"tool:{spec['name']}"]
        assert cap.brain["exposed"] is True and cap.brain["tool_name"] == spec["name"]
        assert cap.permission_class == PERM_MAP.get(spec["name"])
        assert cap.status == "implemented" and cap.cost in COSTS


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
    assert "route:POST /admin/daedalus/outbox/{item_id}/apply" not in by_id          # R2 retired (owner sign-off)
    # ui_home comes from the parity table: in_studio rows get their home, legacy-only routes get none.
    assert by_id["route:POST /api/v1/daedalus/mentor"].ui_home == "setting:mentor"
    assert by_id["route:GET /admin/daedalus/memory/stats"].ui_home == "panel:review/memory"        # home since A6.2 (gate A6.5b)
    assert by_id["route:POST /admin/daedalus/permissions/grant-all"].ui_home == "panel:review/permissions"
    assert by_id["route:POST /admin/daedalus/playground/api/projects/{slug}/suggest"].ui_home == "panel:build"     # home since A6.5c
    assert by_id["route:GET /admin/daedalus/playground/api/projects/{slug}/errors"].ui_home is None            # a stub: still waiting
    assert by_id["route:POST /admin/daedalus/permissions/grant"].ui_home == "panel:review/permissions"
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


def test_retire_rows_skip_route_check_and_in_studio_needs_a_route(app_and_caps, monkeypatch):
    from daedalus.harness import parity

    _app, caps = app_and_caps
    gone = parity.ParityRow("r", "r", ("classic",), None, ("POST /already/removed",), "retire:R9")
    empty = parity.ParityRow("e", "e", ("classic",), "panel:chat", (), "in_studio")
    monkeypatch.setattr(parity, "PARITY_ROWS", (gone, empty))
    assert parity.check_parity(caps) == ["e: an in_studio row must name at least one route"]


def test_manifest_tables_name_real_tools(app_and_caps):
    """Hand-written cost/proof tables must not drift from the real tool names."""
    from daedalus import native_tools
    from daedalus.harness import manifest

    names = {s["name"] for s in native_tools.NATIVE_TOOL_SPECS}
    assert set(manifest.TOOL_COST) <= names
    assert set(manifest.TOOL_PROOF) <= names
    assert set(manifest.ROUTE_STATUS) <= {h for c in app_and_caps[1] if c.kind == "route" for h in c.http}


def test_router_import_failure_is_reported_not_swallowed(a0_tmp, monkeypatch):
    from app.main import app
    from daedalus.harness import manifest
    from daedalus.harness.registry import registry_payload

    monkeypatch.setattr(manifest, "ROUTER_SOURCES", manifest.ROUTER_SOURCES + (("no.such.module", "router"),))
    payload = registry_payload(app)
    assert payload["ok"] is False and any("no.such.module" in e for e in payload["errors"])
    parity = TestClient(app).get("/api/v1/harness/parity").json()
    assert parity["ok"] is False and any("no.such.module" in p for p in parity["problems"])


def test_route_without_name_or_doc_does_not_break_the_registry():
    from functools import partial

    from daedalus.harness import registry

    class Callable_:
        def __call__(self):  # pragma: no cover - never called
            return None

    for endpoint in (partial(print), Callable_()):
        assert registry._endpoint_owner(endpoint)
        assert registry._title(endpoint)


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
            assert row.disposition.startswith(("retire:", "retired:")) and row.home is None


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
    assert parity["by_disposition"]["in_studio"] >= 20
    assert sum(v for k, v in parity["by_disposition"].items() if k.startswith(("retire:", "retired:"))) == 4
    # A6.5b: every row ported in A6.1-A6.4 is home after its real-click check; what still waits is named.
    assert parity["by_disposition"]["port:A6.5c"] == 1 and parity["by_disposition"]["port:A6.1"] == 1
    assert not [k for k in parity["by_disposition"] if k in ("port:A6.2", "port:A6.3", "port:A6.4")]
    assert parity["by_disposition"]["held:MR126"] == 1


def test_endpoints_require_the_owner_token(a0_tmp, raw_http):
    from app.main import app

    client = TestClient(app)
    for path in ("/api/v1/harness/capabilities", "/api/v1/harness/parity"):
        assert client.get(path).status_code == 401


def test_a_carried_out_retirement_fails_the_parity_check_while_its_route_is_still_served():
    from types import SimpleNamespace

    from daedalus.harness import parity

    row = parity.ParityRow("x", "x", ("classic",), None, ("POST /admin/daedalus/outbox/{item_id}/apply",), "retired:R2")
    caps = [SimpleNamespace(id="route:x", kind="route", http=["POST /admin/daedalus/outbox/{item_id}/apply"])]
    original = parity.PARITY_ROWS
    try:
        parity.PARITY_ROWS = (row,)
        assert parity.check_parity(caps) == ["x: retired but still served: POST /admin/daedalus/outbox/{item_id}/apply"]
        assert parity.check_parity([]) == []
    finally:
        parity.PARITY_ROWS = original
