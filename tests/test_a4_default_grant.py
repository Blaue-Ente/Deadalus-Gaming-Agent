"""A4: run_command / write_file / spawn_subagent have permission classes with a visible, revocable default."""
from __future__ import annotations

import pytest
from a0_support import a0_tmp  # noqa: F401

BUILD_SEQUENCE = ["list_dir", "read_file", "write_file", "edit_file", "run_command", "spawn_subagent", "web_search_free"]


def test_classes_exist_and_are_mapped(a0_tmp):
    from daedalus import native_tools, outbox

    assert native_tools.PERM_MAP["run_command"] == "run_command"
    assert native_tools.PERM_MAP["write_file"] == "write_file"
    assert native_tools.PERM_MAP["edit_file"] == "write_file"
    assert native_tools.PERM_MAP["spawn_subagent"] == "spawn_subagent"
    for cls in ("run_command", "write_file", "spawn_subagent"):
        assert cls in outbox.KNOWN_PERMISSION_IDS


def test_build_style_sequence_works_with_no_grants(a0_tmp):
    from daedalus import native_tools

    for tool in ("list_dir", "read_file", "write_file", "edit_file", "run_command", "spawn_subagent"):
        ok, msg = native_tools.before_tool_call(tool, args={"slug": "demo"})
        assert ok, (tool, msg)


def test_default_grant_is_visible_in_the_permission_list(a0_tmp):
    from daedalus import outbox

    rows = {r["perm_class"]: r for r in outbox.default_grants()}
    assert set(rows) == {"run_command", "write_file", "spawn_subagent"}
    for cls, row in rows.items():
        assert row["active"] is True and row["default"] is True
        assert row["granted_by"] == "default:playground" and row["expires_at"] is None
    assert outbox.list_permissions() == []          # explicit grants stay separate: a default is not a grant
    from app.main import app
    from fastapi.testclient import TestClient
    body = TestClient(app).get("/admin/daedalus/permissions").json()
    assert {r["perm_class"] for r in body["default_grants"]} == set(rows)


def test_revoke_blocks_and_survives_reload(a0_tmp):
    from daedalus import native_tools, outbox

    assert outbox.revoke_permission("write_file") is True
    ok, msg = native_tools.before_tool_call("write_file")
    assert not ok and "PERMISSION DENIED" in msg and "write_file" in msg
    assert native_tools.before_tool_call("edit_file")[0] is False  # same class
    assert native_tools.before_tool_call("run_command")[0] is True  # others untouched
    row = next(r for r in outbox.default_grants() if r["perm_class"] == "write_file")
    assert row["active"] is False
    # the revoke lives in the kv store, not in process memory
    from daedalus import kv
    assert "write_file" in (kv.get("adn:default_perm_revoked") or [])


def test_explicit_grant_switches_it_back_on(a0_tmp):
    from daedalus import native_tools, outbox

    outbox.revoke_permission("run_command")
    assert native_tools.before_tool_call("run_command")[0] is False
    outbox.grant_permission("run_command", hours=1)
    assert native_tools.before_tool_call("run_command")[0] is True
    assert "run_command" not in (outbox._default_revoked())


def test_expired_explicit_grant_falls_back_to_the_default(a0_tmp, monkeypatch):
    from daedalus import native_tools, outbox

    outbox.grant_permission("write_file", hours=1)
    perms = outbox._rdb_get(outbox._PERMS_KEY)
    perms["write_file"]["expires_at"] = 1
    outbox._rdb_set(outbox._PERMS_KEY, perms)
    assert native_tools.before_tool_call("write_file")[0] is True


def test_renew_expired_does_not_turn_the_default_into_a_24h_grant(a0_tmp):
    from daedalus import outbox

    res = outbox.renew_expired_permissions()
    assert not ({"run_command", "write_file", "spawn_subagent"} & set(res.get("granted", [])))
    assert "write_file" not in (outbox._rdb_get(outbox._PERMS_KEY) or {})


def test_default_never_covers_a_directory_outside_the_playground(a0_tmp, tmp_path):
    from daedalus import native_tools, outbox
    from daedalus.paths import playground_projects_dir

    inside = playground_projects_dir() / "demo"
    inside.mkdir(parents=True, exist_ok=True)
    assert native_tools.before_tool_call("run_command", args={"cwd": str(inside)})[0] is True
    assert native_tools.before_tool_call("run_command", args={"cwd": "demo"})[0] is True
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    ok, msg = native_tools.before_tool_call("run_command", args={"cwd": str(outside)})
    assert not ok and "PERMISSION DENIED" in msg
    assert native_tools.before_tool_call("run_command", args={"cwd": "../../etc"})[0] is False
    outbox.grant_permission("run_command", hours=1)  # an explicit grant is the owner's decision
    assert native_tools.before_tool_call("run_command", args={"cwd": str(outside)})[0] is True


def test_execute_native_tool_forwards_args_to_the_gate(a0_tmp, tmp_path):
    import asyncio

    from daedalus import native_tools

    out = tmp_path / "elsewhere"
    out.mkdir()
    res = asyncio.run(native_tools.execute_native_tool(
        "run_command", {"command": "echo hi", "cwd": str(out)},
        memory=None, session_id="s", read_file_fn=lambda *a, **k: {},
    ))
    assert "PERMISSION DENIED" in res


def test_fail_closed_still_holds_for_the_new_classes(a0_tmp, monkeypatch):
    from daedalus import native_tools, outbox

    def boom(*_a, **_k):
        raise RuntimeError("store unavailable")

    monkeypatch.setattr(outbox, "check_permission", boom)
    ok, msg = native_tools.before_tool_call("write_file")
    assert ok is False and "fail-closed" in msg
