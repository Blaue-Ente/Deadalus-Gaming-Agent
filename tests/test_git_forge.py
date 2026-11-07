"""GitHub PR + GitLab clone/MR routing."""
from __future__ import annotations

import asyncio
import json

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def forge_tmp(tmp_path, monkeypatch):
    monkeypatch.setenv("DAEDALUS_STANDALONE", "1")
    monkeypatch.setenv("DAEDALUS_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("DAEDALUS_PLAYGROUND", str(tmp_path / "projects"))
    monkeypatch.delenv("DAEDALUS_USE_ADN_DB", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_INTEGRATION_TOKEN", raising=False)
    monkeypatch.delenv("GITLAB_TOKEN", raising=False)
    monkeypatch.delenv("GitLabToken", raising=False)
    monkeypatch.setenv("GITLAB_HOST", "gitlab.com")
    from daedalus.paths import data_root, playground_projects_dir, product_root, repo_root
    data_root.cache_clear()
    playground_projects_dir.cache_clear()
    product_root.cache_clear()
    repo_root.cache_clear()
    from daedalus.store import reset_sqlite_pool
    reset_sqlite_pool()
    yield tmp_path
    reset_sqlite_pool()


class _Resp:
    def __init__(self, status_code, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.text = text or json.dumps(self._payload)

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code} {self.text[:80]}")


def test_parse_and_detect_forge():
    from daedalus.playground_git_push import detect_forge
    from daedalus.playground_github import parse_github_repo
    from daedalus.playground_gitlab import parse_gitlab_repo

    assert parse_github_repo("https://github.com/octo/hello") == ("octo", "hello")
    assert parse_github_repo("octo/hello") == ("octo", "hello")
    assert parse_github_repo("https://gitlab.com/g/p") is None

    gl = parse_gitlab_repo("https://gitlab.com/group/project")
    assert gl is not None and gl[0] == "group/project"
    gl2 = parse_gitlab_repo("gitlab.com/group/project")
    assert gl2 is not None and gl2[0] == "group/project"
    gl3 = parse_gitlab_repo("git@gitlab.com:group/project.git")
    assert gl3 is not None and gl3[0] == "group/project"
    assert parse_gitlab_repo("https://github.com/octo/hello") is None
    assert parse_gitlab_repo("git@github.com:octo/hello.git") is None
    # path-only two-segment is a GitLab *parse*, but clone routing treats it as GitHub
    assert parse_gitlab_repo("octo/hello") is not None

    assert detect_forge("https://github.com/octo/hello") == "github"
    assert detect_forge("https://gitlab.com/g/p") == "gitlab"
    assert detect_forge("gitlab.com/g/p") == "gitlab"
    assert detect_forge("octo/hello") == "auto"


def test_clone_routes_by_host(forge_tmp, monkeypatch):
    from daedalus.playground_git_push import clone_project_remote

    async def gl_clone(url, **kwargs):
        return {"ok": True, "forge": "gitlab", "url": url}

    async def gh_clone(url, **kwargs):
        return {"ok": True, "forge": "github", "url": url}

    monkeypatch.setattr("daedalus.playground_gitlab.clone_repo", gl_clone)
    monkeypatch.setattr("daedalus.playground_github.clone_repo", gh_clone)

    gl = asyncio.run(clone_project_remote("https://gitlab.com/group/project"))
    assert gl["forge"] == "gitlab"
    hostless = asyncio.run(clone_project_remote("gitlab.com/group/project"))
    assert hostless["forge"] == "gitlab"
    gh = asyncio.run(clone_project_remote("octo/hello"))
    assert gh["forge"] == "github"
    gh2 = asyncio.run(clone_project_remote("https://github.com/octo/hello"))
    assert gh2["forge"] == "github"


def test_push_honours_provider(forge_tmp, monkeypatch):
    from daedalus.playground_git_push import push_project_remote

    async def gh_push(*_a, **_k):
        return {"ok": True, "repo": "octo/hello"}

    async def gl_push(*_a, **_k):
        return {"ok": True, "provider": "gitlab", "repo": "g/p"}

    monkeypatch.setattr("daedalus.playground_github.is_configured", lambda: True)
    monkeypatch.setattr("daedalus.playground_gitlab.is_configured", lambda: True)
    monkeypatch.setattr("daedalus.playground_github.push_project", gh_push)
    monkeypatch.setattr("daedalus.playground_gitlab.push_project", gl_push)

    gh = asyncio.run(push_project_remote("lab", provider="github"))
    assert gh["provider"] == "github"
    gl = asyncio.run(push_project_remote("lab", provider="gitlab"))
    assert gl["provider"] == "gitlab"
    auto = asyncio.run(push_project_remote("lab", provider="auto"))
    assert auto["provider"] == "gitlab"


def test_github_pr_create_and_existing(forge_tmp, monkeypatch):
    from daedalus.playground_github import create_pull_request

    monkeypatch.setenv("GITHUB_TOKEN", "ghp_test")
    calls = []

    def fake_post(url, **kwargs):
        calls.append(("POST", url, kwargs.get("json")))
        if len([c for c in calls if c[0] == "POST"]) == 1:
            return _Resp(201, {
                "number": 7,
                "title": "Дедал: feat",
                "html_url": "https://github.com/octo/hello/pull/7",
                "state": "open",
            })
        return _Resp(422, {"message": "Validation Failed"}, text="already exists")

    def fake_get(url, **kwargs):
        calls.append(("GET", url, kwargs.get("params")))
        return _Resp(200, [{
            "number": 7,
            "title": "Дедал: feat",
            "html_url": "https://github.com/octo/hello/pull/7",
            "state": "open",
        }])

    monkeypatch.setattr("httpx.post", fake_post)
    monkeypatch.setattr("httpx.get", fake_get)

    created = create_pull_request("octo", "hello", title="feat", head="daedalus-x", base="main")
    assert created["ok"] is True and created["number"] == 7 and created["existed"] is False
    existed = create_pull_request("octo", "hello", title="feat", head="daedalus-x", base="main")
    assert existed["existed"] is True and existed["url"].endswith("/pull/7")


def test_gitlab_mr_create_and_existing(forge_tmp, monkeypatch):
    from daedalus.playground_gitlab import create_merge_request

    monkeypatch.setenv("GITLAB_TOKEN", "glpat-test")
    posts = {"n": 0}

    def fake_post(url, **kwargs):
        posts["n"] += 1
        if posts["n"] == 1:
            return _Resp(201, {
                "iid": 3,
                "title": "Дедал: feat",
                "web_url": "https://gitlab.com/g/p/-/merge_requests/3",
                "state": "opened",
            })
        return _Resp(409, {"message": "exists"}, text="Conflict")

    def fake_get(url, **kwargs):
        return _Resp(200, [{
            "iid": 3,
            "title": "Дедал: feat",
            "web_url": "https://gitlab.com/g/p/-/merge_requests/3",
            "state": "opened",
        }])

    monkeypatch.setattr("httpx.post", fake_post)
    monkeypatch.setattr("httpx.get", fake_get)

    created = create_merge_request("g/p", source_branch="daedalus-x", title="feat")
    assert created["ok"] is True and created["number"] == 3 and created["existed"] is False
    existed = create_merge_request("g/p", source_branch="daedalus-x", title="feat")
    assert existed["existed"] is True and "/merge_requests/3" in existed["url"]


def test_upstream_review_owner_repo_is_github(forge_tmp, monkeypatch):
    from daedalus.playground_git_push import create_upstream_review

    async def get_project(slug):
        return {"name": slug, "upstream_url": "", "github_repo": "octo/hello"}

    async def pr(*_a, **_k):
        return {"ok": True, "pr": {"url": "https://github.com/octo/hello/pull/1"}}

    async def mr(*_a, **_k):
        raise AssertionError("GitLab MR must not run for owner/repo shorthand")

    monkeypatch.setattr("daedalus.playground.get_project", get_project)
    monkeypatch.setattr("daedalus.playground_github.create_upstream_pr", pr)
    monkeypatch.setattr("daedalus.playground_gitlab.create_upstream_mr", mr)
    r = asyncio.run(create_upstream_review("lab"))
    assert r["pr"]["url"].endswith("/pull/1")


def test_upstream_review_gitlab_url(forge_tmp, monkeypatch):
    from daedalus.playground_git_push import create_upstream_review

    async def get_project(slug):
        return {
            "name": slug,
            "upstream_url": "https://gitlab.com/group/project",
            "gitlab_repo": "https://gitlab.com/group/project",
        }

    async def pr(*_a, **_k):
        raise AssertionError("GitHub PR must not run for GitLab upstream")

    async def mr(*_a, **_k):
        return {"ok": True, "pr": {"url": "https://gitlab.com/group/project/-/merge_requests/2"}, "mr": {"url": "https://gitlab.com/group/project/-/merge_requests/2"}}

    monkeypatch.setattr("daedalus.playground.get_project", get_project)
    monkeypatch.setattr("daedalus.playground_github.create_upstream_pr", pr)
    monkeypatch.setattr("daedalus.playground_gitlab.create_upstream_mr", mr)
    r = asyncio.run(create_upstream_review("lab"))
    assert "merge_requests" in r["pr"]["url"]


def test_http_without_tokens(forge_tmp):
    from app.main import app
    client = TestClient(app)

    gl = client.get("/admin/daedalus/playground/api/gitlab/projects")
    assert gl.status_code == 400
    assert gl.json().get("projects") == []

    clone = client.post("/admin/daedalus/playground/api/clone", json={})
    assert clone.status_code == 400

    created = client.post(
        "/admin/daedalus/playground/api/projects",
        json={"name": "Forge Lab", "kind": "static"},
    )
    assert created.status_code == 200
    slug = created.json()["project"]["slug"]

    push = client.post(
        f"/admin/daedalus/playground/api/projects/{slug}/push",
        json={"provider": "github"},
    )
    assert push.status_code == 500
    err = push.json().get("error") or ""
    assert "Connectors" in err or "GITHUB_TOKEN" in err or "not configured" in err.lower() or "не е конфигуриран" in err.lower()

    pr = client.post(
        f"/admin/daedalus/playground/api/projects/{slug}/create-upstream-pr",
        json={},
    )
    assert pr.status_code == 500
    assert pr.json().get("ok") is False

    page = client.get("/admin/daedalus/playground")
    assert page.status_code == 200
    assert "create-upstream-pr" in page.text
    assert "PR / MR" in page.text


def test_git_forge_native_status(forge_tmp):
    import asyncio
    from daedalus.native_tools import NATIVE_TOOL_SPECS, execute_native_tool
    from daedalus.outbox import grant_permission

    spec = next(t for t in NATIVE_TOOL_SPECS if t["name"] == "git_forge")
    assert "pull_request" in spec["description"] or "Pull Request" in spec["description"]
    grant_permission("github_access", hours=1)
    text = asyncio.run(execute_native_tool(
        "git_forge", {"action": "status"},
        memory=None, session_id="t", read_file_fn=lambda *a, **k: {},
    ))
    assert "git_forge" in text
    assert "github" in text.lower()
