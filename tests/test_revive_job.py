"""Revive: repo → analyse → install and verify → repair → publish, as a job with visible steps.

Owner's ask (2026-11-11): give Daedalus a repo address and it analyses it, installs what it needs, makes it run and
publishes it locally or as a site. Before, Revive only cloned and smoke-tested: the repair was never called, nothing was
published, and a static site counted as working without being opened. These tests use small repos written here."""
from __future__ import annotations

import asyncio
import json
import shutil

import pytest
from a0_support import a0_tmp  # noqa: F401

from daedalus import revive_job, static_build


def _clone_from(files: dict[str, str]):
    async def clone(url, **kw):
        from daedalus import playground as pg

        proj = await pg.create_project(url.rsplit("/", 1)[-1], kind="static", source="revived")
        root = pg.project_dir(proj["slug"])
        for p in root.iterdir():                                   # a clone replaces the scaffold
            shutil.rmtree(p) if p.is_dir() else p.unlink()
        for rel, text in files.items():
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
            (root / rel).write_text(text)
        kind, run_cmd, entry = pg.detect_kind(root)
        await pg.update_project(proj["slug"], kind=kind, run_cmd=run_cmd, entry=entry)
        return {**proj, "kind": kind}
    return clone


GAME = ("<!doctype html><title>Tiny</title><canvas id=c width=200 height=120></canvas><script>"
        "const g=c.getContext('2d');g.fillStyle='#3a8';g.fillRect(0,0,200,120);g.fillStyle='#fff';"
        "g.fillText('tiny game',60,60)</script>")
BUILD_JS = ("const fs=require('fs');fs.mkdirSync('dist',{recursive:true});"
            "fs.writeFileSync('dist/index.html', fs.readFileSync('src/page.html','utf8'));")


def _pkg(**extra) -> str:
    return json.dumps({"name": "x", "version": "1.0.0", "scripts": {"build": "node build.js"}, **extra})


def _run(job, files, monkeypatch=None):
    return asyncio.run(revive_job.run(job, clone_fn=_clone_from(files)))


@pytest.fixture
def no_browser(monkeypatch):
    """Browser checks pass without launching Chromium (one test below uses the real one)."""
    from daedalus.visual_proof import ProofResult

    async def proof(slug, kind="static"):
        return ProofResult(ok=True, skipped=False, kind=kind, slug=slug, tool="browser", summary="ok")

    monkeypatch.setattr("daedalus.visual_proof.run_visual_proof", proof)


def test_a_static_site_is_opened_and_published_locally(a0_tmp, no_browser):  # noqa: F811
    job = _run(revive_job.ReviveJob(url="https://github.com/me/tiny"), {"index.html": GAME})
    assert job.status == "done", job.steps
    assert [s["step"] for s in job.steps] == ["clone", "analyze", "verify", "publish local"]
    assert job.steps[2]["how"] == "browser" and job.local_url == f"/apps/{job.slug}/"


def test_an_app_that_builds_to_static_files_is_built_and_its_output_published(a0_tmp, no_browser):  # noqa: F811
    from daedalus import playground_publish as pub

    files = {"package.json": _pkg(), "build.js": BUILD_JS, "src/page.html": GAME}
    job = _run(revive_job.ReviveJob(url="https://github.com/me/built"), files)
    assert job.status == "done", job.steps
    assert job.analysis["kind"] == "node" and job.analysis["static_build"]["framework"] == "unknown"
    assert job.steps[2]["how"].startswith("build → dist/")
    published = pub.app_dir(job.slug)
    assert (published / "index.html").read_text().startswith("<!doctype html>")
    assert not (published / "build.js").exists()                      # the output, never the raw source


def test_a_broken_build_is_repaired_then_verified(a0_tmp, no_browser, monkeypatch):  # noqa: F811
    files = {"package.json": _pkg(), "build.js": "this is not javascript (", "src/page.html": GAME}
    fixes = []

    async def repair(slug, kind, error):
        from daedalus import playground as pg
        fixes.append(error)
        (pg.project_dir(slug) / "build.js").write_text(BUILD_JS)
        return ["build.js"]

    monkeypatch.setattr(revive_job, "_repair", repair)
    job = _run(revive_job.ReviveJob(url="https://github.com/me/broken"), files)
    assert job.status == "done", job.steps
    assert [s["step"] for s in job.steps][2:5] == ["verify", "repair 1", "verify after repair 1"]
    assert job.steps[2]["ok"] is False and "build failed" in fixes[0]


def test_it_stops_honestly_when_repairs_do_not_help(a0_tmp, no_browser, monkeypatch):  # noqa: F811
    async def repair(slug, kind, error):
        return []

    monkeypatch.setattr(revive_job, "_repair", repair)
    files = {"package.json": _pkg(), "build.js": "nope (", "src/page.html": GAME}
    job = _run(revive_job.ReviveJob(url="https://github.com/me/broken"), files)
    assert job.status == "failed" and "does not run yet" in job.error and job.local_url == ""
    assert job.steps[-1]["step"] == "repair 1" and job.steps[-1]["detail"] == "the model returned no fix"


def test_a_server_app_runs_but_is_not_published_as_a_static_site(a0_tmp, monkeypatch):  # noqa: F811
    async def smoke(slug):
        return {"ok": True, "skipped": False}

    monkeypatch.setattr("daedalus.playground_runner.smoke_test", smoke)
    files = {"package.json": json.dumps({"name": "api", "scripts": {"start": "node index.js", "build": "tsc"},
                                         "dependencies": {"express": "^4"}}), "index.js": "require('express')"}
    job = _run(revive_job.ReviveJob(url="https://github.com/me/api"), files)
    assert job.analysis["server"] == ["express"] and "static_build" not in job.analysis
    assert job.status == "failed" and job.steps[-1]["step"] == "publish local" and job.steps[-1]["ok"] is False
    assert "cannot be published as a static site" in job.error


def test_publishing_as_a_site_goes_through_live_publish(a0_tmp, no_browser, monkeypatch):  # noqa: F811
    seen = {}

    async def live(slug, **kw):
        seen.update(kw)
        return {"ok": True, "url": "https://tiny.netlify.app", "backend": "netlify"}

    monkeypatch.setattr("daedalus.live_publish.publish_live", live)
    job = _run(revive_job.ReviveJob(url="https://github.com/me/tiny", publish="site"), {"index.html": GAME})
    assert job.status == "done" and job.site_url == "https://tiny.netlify.app"
    assert seen == {"confirm_public": False}                          # the owner gate stays in live_publish


def test_a_repo_with_nothing_to_run_says_so(a0_tmp, no_browser):  # noqa: F811
    job = _run(revive_job.ReviveJob(url="https://github.com/me/notes", publish="none"), {"README.md": "# notes"})
    assert job.steps[1]["step"] == "analyze" and job.steps[1]["ok"] is False
    assert "No index.html" in job.steps[1]["detail"]


@pytest.mark.parametrize("pkg, files, framework", [
    ({"scripts": {"build": "react-scripts build"}, "dependencies": {"react-scripts": "5"}}, {}, "react-scripts"),
    ({"scripts": {"build": "next build"}, "dependencies": {"next": "14"}}, {"next.config.js": "module.exports={output:'export'}"}, "next"),
    ({"scripts": {"build": "next build"}, "dependencies": {"next": "14"}}, {}, None),           # SSR app
    ({"scripts": {"build": "tsc"}, "dependencies": {"express": "4"}}, {}, None),               # server
    ({"scripts": {"start": "node ."}}, {}, None),                                              # no build
])
def test_static_build_plans(tmp_path, pkg, files, framework):
    (tmp_path / "package.json").write_text(json.dumps(pkg))
    for rel, text in files.items():
        (tmp_path / rel).write_text(text)
    plan = static_build.plan(tmp_path)
    assert (plan or {}).get("framework") == framework
    if framework == "react-scripts":
        assert plan["outputs"][0] == "build"


def test_angular_nested_output_is_found(tmp_path):
    (tmp_path / "dist" / "app" / "browser").mkdir(parents=True)
    (tmp_path / "dist" / "app" / "browser" / "index.html").write_text("<html>")
    assert static_build.find_output(tmp_path, ["dist"]) == tmp_path / "dist" / "app" / "browser"


def test_the_routes_start_and_report_a_job(a0_tmp, monkeypatch):  # noqa: F811
    from fastapi.testclient import TestClient

    from app.main import app

    async def fake_run(job, clone_fn=None):
        job.status = "done"
        return job

    monkeypatch.setattr(revive_job, "run", fake_run)
    monkeypatch.setenv("DAEDALUS_TOKEN", "t")
    c = TestClient(app, headers={"x-daedalus-token": "t"})
    assert c.post("/api/v1/playground/revive", json={"url": "x/y", "publish": "web"}).status_code == 400
    r = c.post("/api/v1/playground/revive", json={"url": "https://github.com/me/tiny", "publish": "local"})
    assert r.status_code == 200 and r.json()["job"]["status"] in ("running", "done")
    jid = r.json()["job"]["id"]
    assert c.get(f"/api/v1/playground/revive/{jid}").json()["job"]["url"] == "https://github.com/me/tiny"
    assert c.get("/api/v1/playground/revive/nope").status_code == 404
