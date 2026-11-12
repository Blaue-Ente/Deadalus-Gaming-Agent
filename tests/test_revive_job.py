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
    """Browser checks pass without launching Chromium; records which folder was opened."""
    opened = []

    async def open_(slug, folder, how):
        opened.append(folder)
        return {"ok": True, "how": how, "error": ""}

    monkeypatch.setattr(revive_job, "_open_in_browser", open_)
    return opened


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
    assert no_browser[0].name == "dist"                                # the built output is what gets opened
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
    assert seen["confirm_public"] is False and callable(seen["local_fn"])  # owner gate in live_publish; no rebuild


def test_a_site_that_was_not_published_is_not_a_success(a0_tmp, no_browser, monkeypatch):  # noqa: F811
    async def live(slug, **kw):
        return {"ok": True, "url": None, "backend": "local", "note": "no hosting token"}

    monkeypatch.setattr("daedalus.live_publish.publish_live", live)
    job = _run(revive_job.ReviveJob(url="https://github.com/me/tiny", publish="site"), {"index.html": GAME})
    assert job.status == "failed" and job.local_url and "site was not published" in job.error


def test_a_repo_with_nothing_to_run_says_so(a0_tmp, no_browser):  # noqa: F811
    job = _run(revive_job.ReviveJob(url="https://github.com/me/notes", publish="none"), {"README.md": "# notes"})
    assert job.steps[1]["step"] == "analyze" and job.steps[1]["ok"] is False
    assert "No index.html" in job.steps[1]["detail"]


@pytest.mark.parametrize("pkg, files, framework", [
    ({"scripts": {"build": "react-scripts build"}, "dependencies": {"react-scripts": "5"}}, {}, "react-scripts"),
    ({"scripts": {"build": "next build"}, "dependencies": {"next": "14"}}, {"next.config.js": "module.exports={output:'export'}"}, "next"),
    ({"scripts": {"build": "next build"}, "dependencies": {"next": "14"}}, {}, None),           # SSR app
    ({"scripts": {"build": "next build"}, "dependencies": {"next": "14"}},                      # review: substring
     {"next.config.js": "module.exports = { output: 'standalone' }"}, None),
    ({"scripts": {"build": "tsc"}, "dependencies": {"commander": "11"}}, {}, None),            # a CLI, not a site
    ({"scripts": {"build": "node build.js"}}, {"public/index.html": "<html>"}, "unknown"),     # a site with HTML
    ({"scripts": {"build": "tsc"}, "dependencies": {"express": "4"}}, {}, None),               # server
    ({"scripts": {"start": "node ."}}, {}, None),                                              # no build
])
def test_static_build_plans(tmp_path, pkg, files, framework):
    (tmp_path / "package.json").write_text(json.dumps(pkg))
    for rel, text in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text)
    plan = static_build.plan(tmp_path)
    assert (plan or {}).get("framework") == framework
    if framework == "react-scripts":
        assert plan["outputs"][0] == "build"


def test_angular_nested_output_is_found(tmp_path):
    (tmp_path / "dist" / "app" / "browser").mkdir(parents=True)
    (tmp_path / "dist" / "app" / "browser" / "index.html").write_text("<html>")
    assert static_build.find_output(tmp_path, ["dist"]) == tmp_path / "dist" / "app" / "browser"
    assert static_build.find_output(tmp_path, ["dist"], since=9e9) is None     # a stale committed build is not ours


def test_a_fresh_build_is_reused_not_rebuilt(tmp_path):
    import os
    import time

    (tmp_path / "package.json").write_text(json.dumps({"scripts": {"build": "x"}, "dependencies": {"vite": "5"}}))
    (tmp_path / "dist").mkdir()
    (tmp_path / "dist" / "index.html").write_text("<html>")
    later = time.time() + 5
    os.utime(tmp_path / "dist" / "index.html", (later, later))
    assert static_build.fresh_output(tmp_path) == tmp_path / "dist"
    (tmp_path / "main.js").write_text("changed")
    os.utime(tmp_path / "main.js", (later + 5, later + 5))
    assert static_build.fresh_output(tmp_path) is None


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


def test_the_browser_opens_the_build_where_it_will_be_published(tmp_path, monkeypatch):
    """Live 2026-11-12: verify served the build at "/", so absolute /assets/... paths passed and broke under /apps/."""
    from daedalus import playground_browser as pb

    (tmp_path / "index.html").write_text("<html>")
    seen = {}

    async def bv(slug, url, kind="static", **kw):
        import urllib.request
        seen["url"] = url
        seen["body"] = urllib.request.urlopen(url, timeout=5).read().decode()
        return {"ok": True}

    monkeypatch.setattr(pb, "browser_verify", bv)
    monkeypatch.setattr(pb, "chromium_executable", lambda: "/bin/true")
    res = asyncio.run(revive_job._open_in_browser("tiny", tmp_path, "browser"))
    assert res["ok"] and seen["url"].endswith("/apps/tiny/") and seen["body"] == "<html>"


def test_vite_builds_with_relative_assets(tmp_path, monkeypatch):
    import asyncio as aio

    (tmp_path / "package.json").write_text(json.dumps({"scripts": {"build": "vite build"},
                                                       "devDependencies": {"vite": "5"}}))
    (tmp_path / "index.html").write_text("<html>")
    argvs = []

    class Proc:
        returncode = 0

        async def communicate(self):
            if "build" in argvs[-1]:
                (tmp_path / "dist").mkdir(exist_ok=True)
                (tmp_path / "dist" / "index.html").write_text("<html>")
            return b"", b""

    async def fake_exec(*argv, **kw):
        argvs.append(tuple(argv))
        return Proc()

    monkeypatch.setattr(aio, "create_subprocess_exec", fake_exec)
    out = asyncio.run(static_build.build(tmp_path))
    assert out == tmp_path / "dist"
    assert argvs[-1][-3:] == ("build", "--", "--base=./")          # a plain vite build script


def test_the_classic_route_runs_the_same_job_and_keeps_its_answer(a0_tmp, monkeypatch):  # noqa: F811
    """One revive path. The classic page waits and reads {project, onboard, compat}, and sends name, branch and
    repair_on_fail (review 2026-11-12: the alias first returned only a job id and dropped those fields)."""
    from fastapi.testclient import TestClient

    from app.main import app
    from daedalus import playground_revive

    started = []

    async def fake_run(job, clone_fn=None):
        from daedalus import playground as pg
        started.append(job)
        job.slug = (await pg.create_project(job.name or "tiny", kind="static"))["slug"]
        job.steps = [{"step": "verify", "ok": True}]
        job.status = "done"
        return job

    monkeypatch.setattr(revive_job, "run", fake_run)
    monkeypatch.setenv("DAEDALUS_TOKEN", "t")
    c = TestClient(app, headers={"x-daedalus-token": "t"})
    r = c.post("/admin/daedalus/playground/api/revive",
               json={"url": "https://github.com/me/tiny", "name": "Lamp", "branch": "dev", "repair_on_fail": False})
    body = r.json()
    assert r.status_code == 200 and body["project"]["slug"] == "lamp" and body["onboard"]["ok"] is True
    assert "compat" in body and body["job"]["status"] == "done"
    job = started[0]
    assert (job.publish, job.name, job.branch, job.repair_rounds) == ("none", "Lamp", "dev", 0)
    assert c.post("/admin/daedalus/playground/api/revive", json={}).status_code == 400
    assert not hasattr(playground_revive, "revive_pipeline")


def test_name_and_branch_reach_the_clone(a0_tmp, no_browser):  # noqa: F811
    seen = {}
    inner = _clone_from({"index.html": GAME})

    async def clone(url, **kw):
        seen.update(kw)
        return await inner(url, **kw)

    job = asyncio.run(revive_job.run(revive_job.ReviveJob(url="https://github.com/me/tiny", name="Lamp",
                                                          branch="dev"), clone_fn=clone))
    assert job.status == "done" and seen["name"] == "Lamp" and seen["branch"] == "dev"


@pytest.mark.parametrize("script, manager, cmd", [
    ("vite build", "npm", ("npm", "run", "build", "--", "--base=./")),
    ("vite build", "pnpm", ("npx", "--yes", "pnpm", "run", "build", "--base=./")),
    ("vite build --mode prod", "yarn", ("npx", "--yes", "yarn", "run", "build", "--base=./")),
    # Chained (The-Labirinth): the vite step runs directly with relative assets.
    ("tsc --noEmit && tsc --noEmit -p api/tsconfig.json && vite build", "pnpm",
     ("npx", "--yes", "pnpm", "exec", "vite", "build", "--base=./")),
    ("tsc && vite build --mode web", "npm", ("npx", "--no-install", "vite", "build", "--mode", "web", "--base=./")),
    ("vite build --base /x/ && node post.js", "npm", ("npm", "run", "build")),       # the owner chose a base
    ("tsc && node build.js", "npm", ("npm", "run", "build")),                        # no vite step
])
def test_vite_builds_with_relative_assets_wherever_it_can(tmp_path, script, manager, cmd):
    (tmp_path / "package.json").write_text(json.dumps({"scripts": {"build": script}, "devDependencies": {"vite": "5"}}))
    assert static_build.build_command(tmp_path, {"framework": "vite"}, manager) == cmd


def test_an_old_webpack_build_gets_the_legacy_openssl_switch(tmp_path, monkeypatch):
    """Live 2026-11-12 (ahfarmer/calculator, react-scripts 3 on Node 22): ERR_OSSL_EVP_UNSUPPORTED. A known runtime
    switch fixes it; no model needed."""
    import asyncio as aio

    # An undetectable webpack 4 setup: only the error tells (a react-scripts 3 project gets the switch up front).
    (tmp_path / "package.json").write_text(json.dumps({"scripts": {"build": "node scripts/build.js"},
                                                       "dependencies": {"react": "16"}}))
    (tmp_path / "public").mkdir()
    (tmp_path / "public" / "index.html").write_text("<html>")
    envs = []

    class Proc:
        def __init__(self, argv, env):
            self.argv, self.env, self.returncode = argv, env, 0

        async def communicate(self):
            if "build" in self.argv:
                envs.append(self.env.get("NODE_OPTIONS", ""))
                if "--openssl-legacy-provider" not in self.env.get("NODE_OPTIONS", ""):
                    self.returncode = 1
                    return (b"Creating an optimized production build...\nError: error:0308010C:digital envelope "
                            b"routines::unsupported\n    at new Hash (node:internal/crypto/hash:79:19)\n"), b""
                (tmp_path / "build").mkdir(exist_ok=True)
                (tmp_path / "build" / "index.html").write_text("<html>")
            return b"", b""

    async def fake_exec(*argv, env=None, **kw):
        return Proc(argv, env or {})

    monkeypatch.setattr(aio, "create_subprocess_exec", fake_exec)
    assert asyncio.run(static_build.build(tmp_path)) == tmp_path / "build"
    assert len(envs) == 2 and "--openssl-legacy-provider" not in envs[0] and "--openssl-legacy-provider" in envs[1]


def test_error_excerpts_keep_the_cause_not_the_stack():
    out = ("Error: the real cause\n" + "".join(f"    at frame{i} (x.js:{i}:1)\n" for i in range(200))
           + "    at /app/node_modules/webpack/lib/NormalModule.js:452:10\n    at async run (node:internal/x:1:2)\n")
    assert static_build.digest(out) == "Error: the real cause"
    frame = "SyntaxError: src/App.js: Unexpected token (3:4)\n  2 | let x =\n> 3 |     }\n    |     ^\n  at least one fix"
    assert static_build.digest(frame) == frame                 # code-frame carets and prose starting with "at" stay


@pytest.mark.parametrize("deps, node, legacy", [
    ({"react-scripts": "^3.0.1"}, 22, True), ({"react-scripts": "5.0.1"}, 22, False),
    ({"webpack": "4.46.0"}, 22, True), ({"@vue/cli-service": "~4.5.0"}, 20, True),
    ({"react-scripts": "^3.0.1"}, 16, False),                  # Node 16 rejects the flag
])
def test_old_webpack_apps_get_the_switch_for_npm_start_too(tmp_path, monkeypatch, deps, node, legacy):
    (tmp_path / "package.json").write_text(json.dumps({"dependencies": deps}))
    monkeypatch.setattr(static_build, "node_major", lambda: node)
    env = static_build.legacy_openssl_env({"NODE_OPTIONS": "--use-openssl-ca"}, tmp_path)
    assert ("--openssl-legacy-provider" in env["NODE_OPTIONS"]) is legacy and "--use-openssl-ca" in env["NODE_OPTIONS"]


def test_a_revived_app_that_renders_runs_despite_its_backend_calls():
    """Live 2026-11-12, The-Labirinth: full title screen rendered; failed only for /api/config and /sw.js 404s."""
    raw = {"ok": False, "reachable": True, "blank_screen": False, "page_errors": [],
           "failed_requests": ["404 fetch: http://x/api/config"],
           "console_errors": ["A bad HTTP response code (404) was received when fetching the script."]}
    res = revive_job.judge_render(raw, "browser")
    assert res["ok"] and "api/config" in res["note"] and "need a server" in res["note"]


@pytest.mark.parametrize("raw", [
    {"blank_screen": True},
    {"page_errors": ["TypeError: x is undefined"]},
    {"failed_requests": ["404 script: http://x/assets/index.js"]},
    {"failed_requests": ["404 stylesheet: http://x/assets/index.css"]},
    {"reachable": False},
])
def test_a_revived_app_that_does_not_render_still_fails(raw):
    assert revive_job.judge_render({"reachable": True, **raw}, "browser")["ok"] is False


def test_a_publish_that_raises_is_a_step_with_its_reason(a0_tmp, no_browser, monkeypatch):  # noqa: F811
    """Live 2026-11-12: npm install crashed inside publishing and the job ended with a bare RuntimeError."""
    async def boom(slug):
        raise RuntimeError("npm install failed (rc=1):\nnpm error Cannot read properties of null (reading 'matches')")

    monkeypatch.setattr("daedalus.live_publish.ensure_local", boom)
    job = _run(revive_job.ReviveJob(url="https://github.com/me/tiny"), {"index.html": GAME})
    assert job.status == "failed" and job.steps[-1]["step"] == "publish local" and job.steps[-1]["ok"] is False
    assert "Cannot read properties" in job.steps[-1]["detail"] and job.error.startswith("runs, but it was not published")


def test_a_root_absolute_build_is_not_reused(tmp_path):
    import os
    import time

    (tmp_path / "package.json").write_text(json.dumps({"scripts": {"build": "vite build"}, "dependencies": {"vite": "5"}}))
    (tmp_path / "dist").mkdir()
    later = time.time() + 5
    for html, reused in (('<script src="/assets/a.js"></script>', False), ('<script src="./assets/a.js"></script>', True),
                         ('<script src="//cdn.x/a.js"></script>', True)):
        (tmp_path / "dist" / "index.html").write_text(html)
        os.utime(tmp_path / "dist" / "index.html", (later, later))
        assert (static_build.fresh_output(tmp_path) is not None) is reused, html
