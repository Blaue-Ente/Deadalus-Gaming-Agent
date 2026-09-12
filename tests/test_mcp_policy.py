"""P0.19 MCP security policy — install / connect / call. No live LLM, no npm."""
from __future__ import annotations

import ast
import asyncio
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def harness_tmp(tmp_path, monkeypatch):
    monkeypatch.setenv("DAEDALUS_STANDALONE", "1")
    monkeypatch.setenv("DAEDALUS_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("DAEDALUS_PLAYGROUND", str(tmp_path / "projects"))
    monkeypatch.delenv("DAEDALUS_MCP_POLICY", raising=False)
    monkeypatch.delenv("DAEDALUS_MCP_POLICY_FILE", raising=False)
    monkeypatch.delenv("DAEDALUS_MCP_CONFIG", raising=False)
    monkeypatch.delenv("DAEDALUS_OFFLINE", raising=False)
    monkeypatch.delenv("DAEDALUS_USE_ADN_DB", raising=False)
    from daedalus.paths import data_root, playground_projects_dir, product_root, repo_root

    data_root.cache_clear()
    playground_projects_dir.cache_clear()
    product_root.cache_clear()
    repo_root.cache_clear()
    from daedalus.store import reset_sqlite_pool

    reset_sqlite_pool()
    yield tmp_path
    reset_sqlite_pool()


def test_digest_ignores_env(harness_tmp):
    from daedalus.mcp_policy import spec_digest

    a = {"command": "python3", "args": ["-m", "daedalus.blender_mcp"], "env": {"K": "1"}}
    b = {"command": "python3", "args": ["-m", "daedalus.blender_mcp"], "env": {"K": "2"}}
    c = {"command": ["python3", "-m", "daedalus.blender_mcp"]}
    assert spec_digest(a) == spec_digest(b)
    assert spec_digest(a) == spec_digest(c)
    assert spec_digest(a).startswith("sha256:")


def test_unverified_npx_detection():
    from daedalus.mcp_policy import is_unverified_npx, version_pinned

    assert is_unverified_npx({"command": "npx", "args": ["-y", "@modelcontextprotocol/server-memory"]})
    assert is_unverified_npx({"command": "npx.cmd", "args": ["--yes", "foo"]})
    assert is_unverified_npx({"command": ["npx", "-y", "@scope/pkg"]})
    assert not is_unverified_npx({"command": "npx", "args": ["-y", "@modelcontextprotocol/server-memory@0.6.2"]})
    assert not is_unverified_npx({"command": "python3", "args": ["-m", "daedalus.blender_mcp"]})
    assert not is_unverified_npx({"command": "npx", "args": ["@pkg@1.0.0"]})
    assert version_pinned({"command": "npx", "args": ["-y", "foo@1.2.3"]})


def test_catalog_blender_connects(harness_tmp):
    from daedalus.addons import enable
    from daedalus.mcp_policy import InstallTrust, evaluate_connect, load_overlay

    r = enable("blender-mcp")
    assert r["ok"] is True
    assert r["trust"] == InstallTrust.CATALOG.value
    overlay = load_overlay()
    assert overlay["blender-mcp"].trust is InstallTrust.CATALOG
    from daedalus.mcp_client import load_servers

    spec = load_servers()["blender-mcp"]
    decision = evaluate_connect("blender-mcp", spec)
    assert decision.allowed is True


def test_catalog_npx_enable_writes_but_does_not_spawn(harness_tmp, monkeypatch):
    from daedalus.addons import enable
    from daedalus.mcp_client import list_tools, load_servers
    from daedalus.mcp_policy import InstallTrust, load_overlay

    spawned = []

    async def boom(*_a, **_k):
        spawned.append(True)
        raise AssertionError("stdio spawn")

    monkeypatch.setattr("daedalus.mcp_client.asyncio.create_subprocess_exec", boom)
    r = enable("memory")
    assert r["ok"] is True
    servers = load_servers()
    assert servers["memory"]["command"] == "npx"
    assert load_overlay()["memory"].trust is InstallTrust.UNVERIFIED
    out = asyncio.run(list_tools("memory"))
    assert out["ok"] is False
    assert out.get("blocked") is True
    assert out.get("need_pin") is True
    assert spawned == []


def test_custom_unverified_npx_not_persisted(harness_tmp):
    from daedalus.mcp_client import load_servers, upsert_server

    r = upsert_server("evil", {"command": "npx", "args": ["-y", "evil-pkg"]})
    assert r["ok"] is False
    assert r.get("need_pin") is True
    assert "evil" not in load_servers()


def test_ssrf_and_file_url_refused(harness_tmp):
    from daedalus.mcp_client import load_servers, upsert_server
    from daedalus.mcp_policy import http_url_allowed

    assert http_url_allowed("http://169.254.169.254/").allowed is False
    assert http_url_allowed("file:///etc/passwd").allowed is False
    assert http_url_allowed("http://metadata.google.internal/").allowed is False
    meta = upsert_server("meta", {"url": "http://169.254.169.254/"})
    assert meta["ok"] is False
    file_url = upsert_server("fileish", {"url": "file:///etc/passwd"})
    assert file_url["ok"] is False
    assert load_servers() == {}


def test_loopback_http_install_needs_pin(harness_tmp):
    from daedalus.mcp_client import load_servers, upsert_server
    from daedalus.mcp_policy import evaluate_connect, pin_server

    r = upsert_server("remote", {"url": "http://127.0.0.1:3100/mcp"})
    assert r["ok"] is True
    spec = load_servers()["remote"]
    assert evaluate_connect("remote", spec).allowed is False
    pinned = pin_server("remote")
    assert pinned["ok"] is True
    assert evaluate_connect("remote", spec).allowed is True


def test_blender_tool_allowlist_and_write_grant(harness_tmp):
    from daedalus.addons import enable
    from daedalus.mcp_client import call_tool, load_servers
    from daedalus.mcp_policy import evaluate_call
    from daedalus.outbox import grant_permission

    enable("blender-mcp")
    spec = load_servers()["blender-mcp"]
    denied = evaluate_call("blender-mcp", spec, "create_primitive", {"name": "Cube"})
    assert denied.allowed is False
    assert denied.need_approval is True
    unknown = asyncio.run(call_tool("blender-mcp", "rm_tree", {}))
    assert unknown["ok"] is False
    assert unknown.get("blocked") is True
    assert "allowlist" in (unknown.get("error") or "")
    grant_permission("mcp_write", hours=1)
    allowed = evaluate_call("blender-mcp", spec, "create_primitive", {"name": "Cube"})
    assert allowed.allowed is True
    live = asyncio.run(call_tool("blender-mcp", "create_primitive", {
        "name": "HeroMesh",
        "kind": "cube",
        "project_dir": str(harness_tmp),
    }))
    assert live.get("ok") is True


def test_write_heuristic_play_and_manage():
    from daedalus.mcp_policy import McpToolClass, classify_tool

    assert classify_tool("unity-local", "play_scene") is McpToolClass.WRITE
    assert classify_tool("unity-local", "manage_editor") is McpToolClass.WRITE
    assert classify_tool("git", "git_status") is McpToolClass.READ
    assert classify_tool("fetch", "fetch") is McpToolClass.NETWORK


def test_session_pins_are_not_a_policy_bypass(harness_tmp):
    from daedalus.addons import enable
    from daedalus.mcp_client import list_tools
    from daedalus.session_context import TurnPack, bind, mcp_allowed

    enable("memory")
    pack = TurnPack(session_id="s", mcp_ids=())
    with bind(pack):
        ok, _ = mcp_allowed("memory")
    assert ok is True
    out = asyncio.run(list_tools("memory"))
    assert out.get("blocked") is True


def test_engine_adapters_still_patch_list_tools(harness_tmp, monkeypatch):
    src = (ROOT / "daedalus" / "unity_runtime.py").read_text(encoding="utf-8")
    assert "mcp_client.list_tools" in src
    assert "mcp_allowed" not in src
    godot = (ROOT / "daedalus" / "engine_session.py").read_text(encoding="utf-8")
    assert "mcp_client.list_tools" in godot
    assert "mcp_allowed" not in godot


def test_policy_open_escape(harness_tmp, monkeypatch):
    from daedalus.mcp_policy import evaluate_connect, is_unverified_npx

    spec = {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-memory"]}
    assert is_unverified_npx(spec)
    assert evaluate_connect("memory", spec).allowed is False
    monkeypatch.setenv("DAEDALUS_MCP_POLICY", "open")
    assert evaluate_connect("memory", spec).allowed is True


def test_default_is_enforce(harness_tmp, monkeypatch):
    monkeypatch.delenv("DAEDALUS_MCP_POLICY", raising=False)
    from daedalus.mcp_policy import evaluate_connect, policy_is_open

    assert policy_is_open() is False
    spec = {"command": "npx", "args": ["-y", "foo"]}
    assert evaluate_connect("x", spec).allowed is False


def test_child_env_strips_secrets(harness_tmp, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-secret")
    monkeypatch.setenv("ALLTOKEN_API_KEY", "tok")
    monkeypatch.setenv("PATH", "/usr/bin")
    from daedalus.mcp_policy import child_env

    env = child_env({"env": {"GITHUB_PERSONAL_ACCESS_TOKEN": "ghp_x"}})
    assert "OPENROUTER_API_KEY" not in env
    assert "ALLTOKEN_API_KEY" not in env
    assert env.get("PATH") == "/usr/bin"
    assert env["GITHUB_PERSONAL_ACCESS_TOKEN"] == "ghp_x"


def test_http_rpc_does_not_follow_redirects_or_post_denied(harness_tmp, monkeypatch):
    from daedalus import mcp_client

    posted = []

    class FakeClient:
        def __init__(self, *args, **kwargs):
            posted.append(("init", kwargs))

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_a):
            return False

        async def post(self, *args, **kwargs):
            posted.append(("post", args, kwargs))
            raise AssertionError("should not post")

    monkeypatch.setattr("httpx.AsyncClient", FakeClient)
    with pytest.raises(RuntimeError, match="not allowed"):
        asyncio.run(mcp_client._http_rpc("http://169.254.169.254/latest", {"jsonrpc": "2.0"}))
    assert posted == []
    src = (ROOT / "daedalus" / "mcp_client.py").read_text(encoding="utf-8")
    assert "follow_redirects=False" in src


def test_status_exposes_policy_not_secrets(harness_tmp):
    from daedalus.addons import enable
    from daedalus.mcp_client import set_server_env, status

    enable("github-mcp")
    set_server_env("github-mcp", {"GITHUB_PERSONAL_ACCESS_TOKEN": "ghp_secret"})
    st = status()
    assert st["policy"]["mode"] == "enforce"
    row = st["servers"]["github-mcp"]
    assert "ghp_secret" not in json.dumps(st)
    assert "GITHUB_PERSONAL_ACCESS_TOKEN" in row["env_keys"]
    assert row["policy"]["npx_yes"] is True
    assert row["policy"]["need_pin"] is True
    assert row["policy"]["digest"].startswith("sha256:")


def test_stdio_session_refuses_unverified_npx(harness_tmp, monkeypatch):
    from daedalus.mcp_client import _stdio_session

    spawned = []

    async def boom(*_a, **_k):
        spawned.append(True)
        raise AssertionError("spawn")

    monkeypatch.setattr("daedalus.mcp_client.asyncio.create_subprocess_exec", boom)
    with pytest.raises(RuntimeError, match="npx"):
        asyncio.run(_stdio_session({"command": "npx", "args": ["-y", "foo"]}, []))
    assert spawned == []


def test_import_isolation():
    env = {**dict(**{k: v for k, v in __import__("os").environ.items()}), "DAEDALUS_STANDALONE": "1"}
    code = (
        "import daedalus.mcp_policy as p\n"
        "import daedalus.mcp_client as c\n"
        "import sys\n"
        "bad = [n for n in sys.modules if n == 'daedalus.agent' "
        "or n.startswith('daedalus.agent.')]\n"
        "assert not bad, bad\n"
        "print('import-ok', p.evaluate_connect, p.evaluate_call, c.list_tools)\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    assert "import-ok" in result.stdout


def test_agent_and_leftover_untouched():
    from daedalus.tool_registry import REGISTERED_TOOLS

    agent = (ROOT / "daedalus" / "agent.py").read_text(encoding="utf-8")
    scan = (ROOT / "daedalus" / "need_scan.py").read_text(encoding="utf-8")
    native = (ROOT / "daedalus" / "native_tools.py").read_text(encoding="utf-8")
    assert "mcp_policy" not in agent
    assert "NEED_SANDBOX" in agent
    assert "NEED_PLAYGROUND_RUN" in scan
    assert '"mcp_call"' in native
    assert "mcp_call" not in REGISTERED_TOOLS
    tree = ast.parse((ROOT / "daedalus" / "mcp_policy.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            assert node.module != "daedalus.agent"
            assert not node.module.startswith("daedalus.agent.")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                assert "daedalus.agent" not in alias.name


def test_docker_and_play_lab_unused_by_policy():
    policy = (ROOT / "daedalus" / "mcp_policy.py").read_text(encoding="utf-8")
    client = (ROOT / "daedalus" / "mcp_client.py").read_text(encoding="utf-8")
    assert "docker_sandbox" not in policy
    assert "play_lab" not in policy
    assert "execution_backends" not in policy
    assert "docker_sandbox" not in client
    assert "play_lab" not in client
