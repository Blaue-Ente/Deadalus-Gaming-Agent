"""P0 owner token, loopback bind, argv-first shell — characterization + regressions."""
from __future__ import annotations

import stat
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def auth_tmp(tmp_path, monkeypatch):
    monkeypatch.setenv("DAEDALUS_STANDALONE", "1")
    monkeypatch.setenv("DAEDALUS_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("DAEDALUS_PLAYGROUND", str(tmp_path / "projects"))
    monkeypatch.delenv("DAEDALUS_USE_ADN_DB", raising=False)
    monkeypatch.delenv("DAEDALUS_USE_MESH_ROUTER", raising=False)
    from daedalus.paths import data_root, playground_projects_dir, product_root, repo_root
    data_root.cache_clear()
    playground_projects_dir.cache_clear()
    product_root.cache_clear()
    repo_root.cache_clear()
    from daedalus.store import reset_sqlite_pool
    reset_sqlite_pool()
    yield tmp_path
    reset_sqlite_pool()


def test_default_serve_host_is_loopback(monkeypatch):
    monkeypatch.delenv("HOST", raising=False)
    from daedalus.owner_auth import default_serve_host
    assert default_serve_host() == "127.0.0.1"


def test_cli_serve_help_mentions_loopback(capsys):
    from app.cli import main
    with pytest.raises(SystemExit) as caught:
        main(["serve", "--help"])
    assert caught.value.code == 0
    out = capsys.readouterr().out
    assert "127.0.0.1" in out
    assert "DAEDALUS_TOKEN" in out


def test_refuse_public_bind_without_token(tmp_path, monkeypatch):
    monkeypatch.delenv("DAEDALUS_TOKEN", raising=False)
    monkeypatch.setenv("DAEDALUS_DATA", str(tmp_path / "empty-data"))
    from daedalus.paths import data_root
    data_root.cache_clear()
    from daedalus.owner_auth import assert_bind_allowed
    with pytest.raises(SystemExit) as caught:
        assert_bind_allowed("0.0.0.0")
    assert "owner token" in str(caught.value)


def test_public_bind_ok_when_token_configured(monkeypatch):
    monkeypatch.setenv("DAEDALUS_TOKEN", "explicit-public-token")
    from daedalus.owner_auth import assert_bind_allowed
    assert_bind_allowed("0.0.0.0")


def test_owner_token_file_is_private(tmp_path, monkeypatch):
    monkeypatch.delenv("DAEDALUS_TOKEN", raising=False)
    monkeypatch.setenv("DAEDALUS_DATA", str(tmp_path / "data"))
    from daedalus.paths import data_root
    data_root.cache_clear()
    from daedalus.owner_auth import get_or_create_token, token_path
    token = get_or_create_token()
    path = token_path()
    assert token
    assert path.is_file()
    mode = stat.S_IMODE(path.stat().st_mode)
    assert mode == 0o600
    assert get_or_create_token() == token


def test_unauthenticated_shell_is_401(auth_tmp, raw_http):
    from app.main import app
    client = TestClient(app)
    res = client.post("/api/v1/daedalus/shell", json={"command": "echo x"})
    assert res.status_code == 401
    assert res.json().get("error") == "owner token required"


def test_unauthenticated_chat_is_401(auth_tmp, raw_http):
    from app.main import app
    client = TestClient(app)
    res = client.post("/api/v1/daedalus/chat", json={"message": "hi there long enough"})
    assert res.status_code == 401


def test_unauthenticated_admin_json_is_401(auth_tmp, raw_http):
    from app.main import app
    client = TestClient(app)
    res = client.get("/admin/daedalus/planner/plans")
    assert res.status_code == 401


def test_health_and_studio_stay_public(auth_tmp, raw_http):
    from app.main import app
    client = TestClient(app)
    health = client.get("/health")
    assert health.status_code == 200
    assert health.json().get("ok") is True
    caps = client.get("/capabilities")
    assert "owner-token" in caps.json()["capabilities"]
    home = client.get("/")
    assert home.status_code == 200
    assert "owner-auth.js" in home.text
    js = client.get("/static/owner-auth.js")
    assert js.status_code == 200
    assert "DaedalusAuth" in js.text


def test_bootstrap_from_loopback_returns_token(auth_tmp, raw_http, monkeypatch):
    monkeypatch.delenv("DAEDALUS_TOKEN", raising=False)
    from daedalus.paths import data_root
    data_root.cache_clear()
    from app.main import app
    client = TestClient(app)
    res = client.get("/api/v1/auth/bootstrap")
    assert res.status_code == 200
    body = res.json()
    assert body.get("ok") is True
    assert len(body.get("token") or "") >= 16


def test_bootstrap_from_remote_is_403(auth_tmp, raw_http):
    from app.main import app
    client = TestClient(app, client=("203.0.113.9", 4321))
    res = client.get("/api/v1/auth/bootstrap")
    assert res.status_code == 403


def test_shell_ok_with_bearer(auth_tmp, raw_http):
    from app.main import app
    client = TestClient(app)
    res = client.post(
        "/api/v1/daedalus/shell",
        json={"command": "echo studio-auth"},
        headers={"Authorization": "Bearer pytest-owner-token"},
    )
    assert res.status_code == 200
    assert "studio-auth" in res.json().get("stdout", "")


def test_shell_ok_with_x_token_header(auth_tmp, raw_http):
    from app.main import app
    client = TestClient(app)
    res = client.post(
        "/api/v1/daedalus/shell",
        json={"command": "echo hdr"},
        headers={"X-Daedalus-Token": "pytest-owner-token"},
    )
    assert res.status_code == 200
    assert "hdr" in res.json().get("stdout", "")


def test_query_token_works_for_get(auth_tmp, raw_http):
    from app.main import app
    client = TestClient(app)
    res = client.get("/api/v1/daedalus/preferences", params={"daedalus_token": "pytest-owner-token"})
    assert res.status_code == 200


def test_wrong_token_is_401(auth_tmp, raw_http):
    from app.main import app
    client = TestClient(app)
    res = client.post(
        "/api/v1/daedalus/shell",
        json={"command": "echo nope"},
        headers={"Authorization": "Bearer wrong-token-value-here"},
    )
    assert res.status_code == 401


def test_published_apps_kv_stays_public(auth_tmp, raw_http):
    from app.main import app
    client = TestClient(app)
    put = client.post("/apps/maze/db/score", json={"value": 3})
    assert put.status_code == 200
    got = client.get("/apps/maze/db/score")
    assert got.json().get("value") == 3


def test_shell_metacharacters_refused(auth_tmp):
    from daedalus.shell_center import run_command
    piped = run_command("echo hello | wc -l")
    assert piped["ok"] is False
    assert piped["blocked"] is True
    assert "metacharacters" in piped["error"]
    subst = run_command("echo $(id)")
    assert subst["blocked"] is True
    chained = run_command("echo a; echo b")
    assert chained["blocked"] is True


def test_shell_legacy_opt_in_allows_pipe(auth_tmp, monkeypatch):
    monkeypatch.setenv("DAEDALUS_SHELL_LEGACY", "1")
    from daedalus.shell_center import run_command
    piped = run_command("echo hello-legacy")
    assert piped["ok"] is True
    assert "hello-legacy" in piped["stdout"]


def test_cwd_outside_jail_is_blocked(auth_tmp):
    from daedalus.shell_center import resolve_cwd, run_command
    with pytest.raises(ValueError, match="outside jail"):
        resolve_cwd("/etc")
    bad = run_command("pwd", cwd="/etc")
    assert bad["blocked"] is True
    assert "jail" in bad["error"]


def test_argv_python_dash_c(auth_tmp):
    from daedalus.shell_center import run_command
    row = run_command('python3 -c "print(1+1)"')
    assert row["ok"] is True
    assert "2" in row["stdout"]


def test_public_bind_disables_shell_without_isolation(auth_tmp, monkeypatch):
    monkeypatch.setenv("DAEDALUS_BIND_HOST", "0.0.0.0")
    monkeypatch.delenv("DAEDALUS_SHELL_ISOLATION", raising=False)
    from daedalus.shell_center import run_command, shell_enabled
    assert shell_enabled() is False
    row = run_command("echo should-not-run")
    assert row["disabled"] is True
    assert row["ok"] is False
    from app.main import app
    client = TestClient(app)
    res = client.post("/api/v1/daedalus/shell", json={"command": "echo x"})
    assert res.status_code == 403


def test_public_bind_shell_with_isolation_flag(auth_tmp, monkeypatch):
    monkeypatch.setenv("DAEDALUS_BIND_HOST", "0.0.0.0")
    monkeypatch.setenv("DAEDALUS_SHELL_ISOLATION", "1")
    from daedalus.shell_center import run_command, shell_enabled
    assert shell_enabled() is True
    row = run_command("echo isolated")
    assert row["ok"] is True
    assert "isolated" in row["stdout"]


def test_studio_js_attaches_auth_helpers(auth_tmp):
    root = Path(__file__).resolve().parents[1]
    html = (root / "app" / "static" / "studio.html").read_text(encoding="utf-8")
    js = (root / "app" / "static" / "studio.js").read_text(encoding="utf-8")
    nav = (root / "ui" / "templates" / "_daedalus_nav.html").read_text(encoding="utf-8")
    assert "owner-auth.js" in html
    assert "DaedalusAuth" in js
    assert "owner-auth.js" in nav


def test_canonical_shell_pages_are_public_but_their_apis_are_protected(auth_tmp):
    from daedalus.owner_auth import is_public_request

    for path in (
        "/admin/daedalus/projects",
        "/admin/daedalus/projects/game-studio",
        "/admin/daedalus/agent",
        "/admin/daedalus/review",
        "/admin/daedalus/settings",
    ):
        assert is_public_request("GET", path)
    assert not is_public_request("GET", "/admin/daedalus/playground/api/projects")
