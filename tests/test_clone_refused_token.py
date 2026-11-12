"""A refused token must not block cloning a public repo (live revive 2026-11-12: a placeholder GITHUB_TOKEN made
every public clone fail with "could not read Username"). The clone retries once without the credential."""
from __future__ import annotations

import asyncio

import pytest
from a0_support import a0_tmp  # noqa: F401

from daedalus import git_auth


@pytest.mark.parametrize("out, refused", [
    ("fatal: could not read Username for 'https://github.com': terminal prompts disabled", True),
    ("remote: HTTP Basic: Access denied\nfatal: Authentication failed for 'https://gitlab.com/x/y.git/'", True),
    ("fatal: unable to access 'https://github.com/x/y/': The requested URL returned error: 403", True),
    ("fatal: repository 'https://github.com/x/nope/' not found", False),
    ("fatal: unable to access: Could not resolve host: github.com", False),
])
def test_auth_failed(out, refused):
    assert git_auth.auth_failed(out) is refused


@pytest.mark.parametrize("module, token_attr", [("playground_github", "_token"), ("playground_gitlab", "_token")])
def test_clone_retries_without_a_refused_token(a0_tmp, monkeypatch, module, token_attr):  # noqa: F811
    import importlib

    mod = importlib.import_module(f"daedalus.{module}")
    calls = []

    async def fake_run(args, *, cwd=None, timeout=0, anonymous=False):
        calls.append(anonymous)
        if args[1] == "clone":
            if not anonymous:
                return 128, "fatal: could not read Username for 'https://x': terminal prompts disabled"
            dest = args[-1]
            import pathlib
            pathlib.Path(dest).mkdir(parents=True, exist_ok=True)
            (pathlib.Path(dest) / "index.html").write_text("<html>")
            return 0, ""
        return 1, ""

    monkeypatch.setattr(mod, token_attr, lambda: "placeholder-token")
    monkeypatch.setattr(mod, "_run", fake_run)
    url = "https://github.com/me/tiny" if module == "playground_github" else "https://gitlab.com/me/tiny"
    proj = asyncio.run(mod.clone_repo(url))
    assert proj["slug"] and calls[:2] == [False, True]


def test_anonymous_run_sends_no_credential(monkeypatch):
    from daedalus import playground_github as gh

    seen = {}

    async def fake_exec(*args, env=None, **kw):
        seen.update(env)
        raise RuntimeError("stop")

    monkeypatch.setattr(gh, "_token", lambda: "secret-token-value")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.delenv("GIT_CONFIG_COUNT", raising=False)
    with pytest.raises(RuntimeError):
        asyncio.run(gh._run(["git", "status"], anonymous=True))
    assert not any("AUTHORIZATION" in str(v) for v in seen.values())
