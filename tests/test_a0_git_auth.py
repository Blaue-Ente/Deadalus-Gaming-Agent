"""A0-b: git tokens travel in the child env only — never in argv, URLs or .git/config."""
from __future__ import annotations

import asyncio
import base64
import subprocess

import pytest

TOKEN = "tok_SECRET_123"


def _headers(env):
    """{config key: value} for every GIT_CONFIG entry (the host may already set some)."""
    n = int(env.get("GIT_CONFIG_COUNT") or 0)
    return {env[f"GIT_CONFIG_KEY_{i}"]: env[f"GIT_CONFIG_VALUE_{i}"] for i in range(n)}


def test_auth_env_sets_per_host_header_only():
    from daedalus.git_auth import auth_env

    env = auth_env("github.com", "x-access-token", TOKEN, {"PATH": "/bin"})
    assert env["GIT_CONFIG_COUNT"] == "1"
    assert env["GIT_CONFIG_KEY_0"] == "http.https://github.com/.extraheader"
    blob = base64.b64encode(f"x-access-token:{TOKEN}".encode()).decode()
    assert env["GIT_CONFIG_VALUE_0"] == f"AUTHORIZATION: basic {blob}"
    assert env["GIT_TERMINAL_PROMPT"] == "0"
    assert TOKEN not in "".join(v for k, v in env.items() if k != "GIT_CONFIG_VALUE_0")


def test_auth_env_appends_to_existing_config_count_and_skips_empty_token():
    from daedalus.git_auth import auth_env

    env = auth_env("h.example", "u", TOKEN, {"GIT_CONFIG_COUNT": "2"})
    assert env["GIT_CONFIG_COUNT"] == "3" and "GIT_CONFIG_KEY_2" in env
    plain = auth_env("h.example", "u", "", {})
    assert "GIT_CONFIG_COUNT" not in plain


def test_scrub_removes_token_and_basic_blob():
    from daedalus.git_auth import scrub

    blob = base64.b64encode(f"oauth2:{TOKEN}".encode()).decode()
    out = scrub(f"fatal {TOKEN} and AUTHORIZATION: basic {blob}", TOKEN, "oauth2")
    assert TOKEN not in out and blob not in out


def test_auth_url_functions_return_clean_urls(monkeypatch):
    from daedalus import playground_github as gh, playground_gitlab as gl

    monkeypatch.setenv("GITHUB_TOKEN", TOKEN)
    monkeypatch.setenv("GITLAB_TOKEN", TOKEN)
    assert gh._auth_url("https://github.com/o/r.git") == "https://github.com/o/r.git"
    assert gl._auth_url(f"https://{gl._host()}/o/r.git") == f"https://{gl._host()}/o/r.git"


@pytest.mark.parametrize("module,host,user,envname", [
    ("daedalus.playground_github", "github.com", "x-access-token", "GITHUB_TOKEN"),
    ("daedalus.playground_gitlab", None, "oauth2", "GITLAB_TOKEN"),
])
def test_run_passes_token_via_env_not_argv(monkeypatch, module, host, user, envname):
    import importlib

    mod = importlib.import_module(module)
    monkeypatch.setenv(envname, TOKEN)
    monkeypatch.delenv("GITHUB_INTEGRATION_TOKEN", raising=False)
    if module.endswith("github"):
        monkeypatch.setattr(mod, "_token", lambda: TOKEN)
    seen = {}

    class FakeProc:
        returncode = 1

        async def communicate(self):
            return (f"fatal: could not read {TOKEN}".encode(), None)

    async def fake_exec(*args, **kwargs):
        seen["args"] = args
        seen["env"] = kwargs["env"]
        return FakeProc()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    code, out = asyncio.run(mod._run(["git", "push", "origin", "main"]))
    assert code == 1
    assert TOKEN not in out and "***" in out
    assert all(TOKEN not in str(a) for a in seen["args"])
    want_host = host or mod._host()
    blob = base64.b64encode(f"{user}:{TOKEN}".encode()).decode()
    assert blob in _headers(seen["env"])[f"http.https://{want_host}/.extraheader"]


def test_unity_shallow_clone_uses_env_and_clean_url(monkeypatch, tmp_path):
    from daedalus import playground_unity_init as ui

    monkeypatch.setenv("GITHUB_TOKEN", TOKEN)
    seen = {}

    class FakeProc:
        returncode = 0

        async def communicate(self):
            return (b"", b"")

    async def fake_exec(*args, **kwargs):
        seen["args"] = args
        seen["env"] = kwargs["env"]
        return FakeProc()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    asyncio.run(ui._shallow_clone("https://github.com/o/r", tmp_path / "d"))
    assert all(TOKEN not in str(a) for a in seen["args"])
    assert "http.https://github.com/.extraheader" in _headers(seen["env"])


def test_remote_url_in_git_config_stays_clean(monkeypatch, tmp_path):
    """Real git: add origin with the (clean) _auth_url and confirm .git/config has no token."""
    from daedalus import playground_github as gh

    monkeypatch.setattr(gh, "_token", lambda: TOKEN)
    repo = tmp_path / "p"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    url = gh._auth_url("https://github.com/o/r.git")
    subprocess.run(["git", "remote", "add", "origin", url], cwd=repo, check=True)
    assert TOKEN not in (repo / ".git" / "config").read_text()
