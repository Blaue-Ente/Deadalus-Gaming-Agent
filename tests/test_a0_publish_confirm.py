"""A0-c: public publishes and force pushes need explicit owner confirmation."""
from __future__ import annotations

import asyncio

import pytest
from a0_support import a0_tmp  # noqa: F401
from fastapi.testclient import TestClient


@pytest.fixture
def chain(monkeypatch):
    monkeypatch.setattr(
        "daedalus.live_publish.pick_chain",
        lambda requested="auto": [requested] if requested not in ("", "auto") else
        ["netlify", "gitlab_pages", "netlify_anon"],
    )


async def _local(slug):
    return {"ok": True, "dir": None, "local_url": f"/apps/{slug}/"}


def test_effective_chain_drops_confirm_only_backends(chain):
    from daedalus.live_publish import effective_chain

    assert effective_chain("auto") == ["netlify"]
    assert effective_chain("auto", confirm_public=True) == ["netlify", "gitlab_pages", "netlify_anon"]


@pytest.mark.parametrize("backend", ["netlify_anon", "gitlab_pages"])
def test_explicit_confirm_only_backend_refused_without_confirm(chain, backend):
    from daedalus.live_publish import publish_live

    calls = []

    async def deploy_fn(name, static_dir, slug):
        calls.append(name)
        return {"ok": True, "backend": name, "url": "https://x"}

    out = asyncio.run(publish_live("s", backend=backend, local_fn=_local, deploy_fn=deploy_fn))
    assert out["ok"] is False and out["error"] == "confirm_required"
    assert calls == []


def test_auto_without_confirm_skips_public_only_backends(chain, tmp_path, monkeypatch):
    from daedalus.live_publish import publish_live

    monkeypatch.setattr("daedalus.live_publish.pick_chain", lambda requested="auto": ["gitlab_pages", "netlify_anon"])
    calls = []

    async def local_fn(slug):
        return {"ok": True, "dir": tmp_path, "local_url": f"/apps/{slug}/"}

    async def deploy_fn(name, static_dir, slug):
        calls.append(name)
        return {"ok": True, "backend": name, "url": "https://x"}

    monkeypatch.setattr("daedalus.live_publish.record_site", lambda *a, **k: None)
    out = asyncio.run(publish_live("s", backend="auto", local_fn=local_fn, deploy_fn=deploy_fn))
    assert calls == []
    assert out["url"] is None and out["needs_confirm"] == ["gitlab_pages", "netlify_anon"]
    out = asyncio.run(publish_live("s", backend="auto", local_fn=local_fn, deploy_fn=deploy_fn, confirm_public=True))
    assert calls == ["gitlab_pages"] and out["url"] == "https://x"


def test_live_route_409_and_confirm_flag(a0_tmp, monkeypatch):
    from app.main import app

    seen = {}

    async def fake_live(slug, backend="auto", confirm_public=False, **kw):
        seen["confirm"] = confirm_public
        if backend == "netlify_anon" and not confirm_public:
            return {"ok": False, "error": "confirm_required", "backend": backend}
        return {"ok": True, "slug": slug, "local_url": f"/apps/{slug}/", "url": None, "backend": "local"}

    monkeypatch.setattr("daedalus.live_publish.publish_live", fake_live)
    client = TestClient(app)
    res = client.post("/api/v1/playground/projects/p/live", json={"backend": "netlify_anon"})
    assert res.status_code == 409
    ok = client.post("/api/v1/playground/projects/p/live", json={"backend": "netlify_anon", "confirm_public": True})
    assert ok.status_code == 200 and seen["confirm"] is True


# ---- force push -----------------------------------------------------------

def test_push_args_and_rejection_mapping():
    from daedalus.git_auth import NonFastForward, push_args, raise_if_rejected

    assert "-f" not in push_args("main")
    assert "-f" in push_args("main", force=True)
    with pytest.raises(NonFastForward):
        raise_if_rejected("! [rejected] main -> main (non-fast-forward)", force=False)
    raise_if_rejected("! [rejected] main -> main (non-fast-forward)", force=True)  # force never maps
    raise_if_rejected("fatal: unable to access", force=False)  # other errors are not mapped


def _fake_run_factory(record, reject_push):
    async def fake_run(args, *, cwd=None, timeout=180):
        record.append(list(args))
        if args[1:2] == ["push"] and reject_push and "-f" not in args:
            return 1, "! [rejected] main -> main (non-fast-forward)"
        return 0, ""
    return fake_run


@pytest.mark.parametrize("module", ["daedalus.playground_github", "daedalus.playground_gitlab"])
def test_project_push_not_forced_by_default(a0_tmp, monkeypatch, module):
    import importlib
    from daedalus import playground as pg
    from daedalus.git_auth import NonFastForward

    mod = importlib.import_module(module)
    monkeypatch.setenv("GITHUB_TOKEN", "t")
    monkeypatch.setenv("GITLAB_TOKEN", "t")
    monkeypatch.setattr(mod, "_token", lambda: "t", raising=False)
    proj = asyncio.run(pg.create_project("fp", kind="static"))
    root = pg.project_dir(proj["slug"])
    (root / "index.html").write_text("x", encoding="utf-8")
    record: list[list[str]] = []
    monkeypatch.setattr(mod, "_run", _fake_run_factory(record, reject_push=True))
    name = "o/r"
    with pytest.raises(NonFastForward):
        asyncio.run(mod.push_project(proj["slug"], repo_full_name=name, create_if_missing=False))
    assert all("-f" not in a for a in record if a[1:2] == ["push"])
    record.clear()
    asyncio.run(mod.push_project(proj["slug"], repo_full_name=name, create_if_missing=False, force=True))
    assert any("-f" in a for a in record if a[1:2] == ["push"])


def test_push_route_409_needs_force(a0_tmp, monkeypatch):
    from app.main import app
    from daedalus.git_auth import NonFastForward

    seen = {}

    async def fake_push(slug, **kw):
        seen["force"] = kw.get("force")
        if not kw.get("force"):
            raise NonFastForward("non_fast_forward: nope")
        return {"ok": True, "provider": "github"}

    monkeypatch.setattr("daedalus.playground_git_push.push_project_remote", fake_push)
    client = TestClient(app)
    url = "/admin/daedalus/playground/api/projects/p/push"
    res = client.post(url, json={"provider": "github"})
    assert res.status_code == 409 and res.json()["error"] == "non_fast_forward" and res.json()["needs_force"] is True
    ok = client.post(url, json={"provider": "github", "force": True})
    assert ok.status_code == 200 and seen["force"] is True
    # truthy strings are not force
    client.post(url, json={"provider": "github", "force": "yes"})
    assert seen["force"] is False


def test_kernel_push_not_forced_by_default(a0_tmp, monkeypatch, tmp_path):
    from daedalus import playground_github as gh
    from daedalus.git_auth import NonFastForward

    monkeypatch.setattr(gh, "is_configured", lambda: True)
    monkeypatch.setattr(gh, "_token", lambda: "t")
    (tmp_path / "ws").mkdir()
    (tmp_path / "ws" / "a.txt").write_text("x", encoding="utf-8")
    monkeypatch.setattr(gh, "_WORKSPACE_ROOT", tmp_path / "ws")
    record: list[list[str]] = []
    monkeypatch.setattr(gh, "_run", _fake_run_factory(record, reject_push=True))
    with pytest.raises(NonFastForward):
        asyncio.run(gh.push_kernel(repo_full_name="o/r", paths=["a.txt"]))
    asyncio.run(gh.push_kernel(repo_full_name="o/r", paths=["a.txt"], force=True))
    assert any("-f" in a for a in record if a[1:2] == ["push"])


def test_only_real_divergence_offers_force():
    """Review fix: generic push failures (protected branch, hook, permissions) never invite a force push."""
    from daedalus.git_auth import NonFastForward, raise_if_rejected

    for text in (
        "error: failed to push some refs to 'https://github.com/o/r.git'",
        " ! [remote rejected] main -> main (protected branch hook declined)",
        "remote: Permission to o/r.git denied",
    ):
        raise_if_rejected(text, force=False)  # not a divergence: left for the normal error path
    for text in (
        " ! [rejected] main -> main (non-fast-forward)",
        " ! [rejected] main -> main (fetch first)",
        "hint: Updates were rejected because the tip of your current branch is behind",
    ):
        with pytest.raises(NonFastForward):
            raise_if_rejected(text, force=False)
