"""Studio top-10: chat jobs, mesh fail-closed, sandbox strip, published list."""
from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def standalone_tmp(tmp_path, monkeypatch):
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
    from daedalus.chat_jobs import reset
    reset()


def test_mesh_ask_without_key_is_401(standalone_tmp, raw_http, monkeypatch):
    monkeypatch.delenv("IYVERIS_MESH_KEY", raising=False)
    from app.main import app
    client = TestClient(app)
    res = client.post("/api/iyveris", json={"action": "ask", "payload": {"message": "hello there"}})
    assert res.status_code == 401
    assert "mesh key" in (res.json().get("detail") or "").lower()


def test_mesh_ping_loopback_without_key_ok(standalone_tmp, raw_http, monkeypatch):
    monkeypatch.delenv("IYVERIS_MESH_KEY", raising=False)
    from app.main import app
    client = TestClient(app)
    res = client.post("/api/iyveris", json={"action": "ping"})
    assert res.status_code == 200
    assert res.json().get("ok") is True


def test_mesh_wrong_key_is_401(standalone_tmp, raw_http, monkeypatch):
    monkeypatch.setenv("IYVERIS_MESH_KEY", "correct-mesh-key-value-32ok")
    from app.main import app
    client = TestClient(app)
    bad = client.post(
        "/api/iyveris",
        json={"action": "ping"},
        headers={"X-Iyveris-Mesh-Key": "short"},
    )
    assert bad.status_code == 401
    good = client.post(
        "/api/iyveris",
        json={"action": "ping"},
        headers={"X-Iyveris-Mesh-Key": "correct-mesh-key-value-32ok"},
    )
    assert good.status_code == 200


def test_sandbox_snippet_strips_llm_key(standalone_tmp, monkeypatch):
    monkeypatch.setenv("ALLTOKEN_API_KEY", "secret-should-not-leak")
    from daedalus.sandbox import run_snippet
    row = run_snippet("import os\nprint(os.environ.get('ALLTOKEN_API_KEY') or 'ABSENT')")
    blob = " ".join(str(row.get(k) or "") for k in ("stdout", "stderr", "error"))
    assert "secret-should-not-leak" not in blob
    assert "ABSENT" in (row.get("stdout") or "") or row.get("ok") is True


def test_chat_jobs_mocked_llm(standalone_tmp, monkeypatch):
    class Fake:
        async def chat(self, **kwargs):
            return {
                "ok": True,
                "reply": "pong:" + str(kwargs.get("message") or ""),
                "session_id": kwargs.get("session_id"),
            }

    monkeypatch.setattr("daedalus.agent.get_daedalus", lambda: Fake())
    from daedalus.chat_jobs import reset
    reset()
    from app.main import app
    client = TestClient(app)
    started = client.post(
        "/api/v1/daedalus/chat/jobs",
        json={"message": "hello from studio", "session_id": "s1", "effort": "whisper"},
    )
    assert started.status_code == 200
    jid = started.json()["job_id"]
    row = None
    for _ in range(50):
        got = client.get(f"/api/v1/daedalus/chat/jobs/{jid}")
        assert got.status_code == 200
        row = got.json()
        if row.get("status") in ("done", "error", "cancelled"):
            break
        time.sleep(0.05)
    assert row is not None
    assert row["status"] == "done"
    assert (row.get("result") or {}).get("reply", "").startswith("pong:")


def test_chat_job_cancel(standalone_tmp, monkeypatch):
    class Slow:
        async def chat(self, **kwargs):
            await asyncio.sleep(20)
            return {"ok": True, "reply": "late"}

    monkeypatch.setattr("daedalus.agent.get_daedalus", lambda: Slow())
    from daedalus.chat_jobs import reset
    reset()
    from app.main import app
    client = TestClient(app)
    started = client.post(
        "/api/v1/daedalus/chat/jobs",
        json={"message": "please wait a while", "session_id": "slow"},
    )
    jid = started.json()["job_id"]
    stopped = client.post(f"/api/v1/daedalus/chat/jobs/{jid}/cancel")
    assert stopped.status_code == 200
    assert stopped.json().get("status") in ("cancelled", "done")


def test_published_list_returns_apps(standalone_tmp):
    async def seed():
        from daedalus.store import get_sqlite_pool
        pool = await get_sqlite_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO playground_published_apps (slug, name, kind, enabled) "
                "VALUES ($1, $2, $3, TRUE)",
                "labyrinth",
                "Labyrinth",
                "static",
            )

    asyncio.run(seed())
    from app.main import app
    client = TestClient(app)
    res = client.get("/admin/daedalus/playground/api/published")
    assert res.status_code == 200
    body = res.json()
    assert body.get("ok") is True
    slugs = [a.get("slug") for a in (body.get("apps") or [])]
    assert "labyrinth" in slugs
    assert slugs != []


def test_studio_fetch_helper_and_stop_control():
    html = (ROOT / "app" / "static" / "studio.html").read_text(encoding="utf-8")
    js = (ROOT / "app" / "static" / "studio.js").read_text(encoding="utf-8")
    assert 'id="stop"' in html
    assert "async function api(" in js
    assert "res.ok" in js
    assert "AbortError" in js
    assert "chat/jobs" in js


def test_cc_escapes_skill_fields():
    skills = (ROOT / "ui" / "templates" / "skills.html").read_text(encoding="utf-8")
    assert "escHtml(s.label)" in skills
    assert "onclick=\"toggle('${s.name}')\"" not in skills
    proposals = (ROOT / "ui" / "templates" / "proposals.html").read_text(encoding="utf-8")
    assert "escHtml(p.file_path)" in proposals
    daedalus = (ROOT / "ui" / "templates" / "daedalus.html").read_text(encoding="utf-8")
    assert "https?:" in daedalus
    hub = (ROOT / "ui" / "templates" / "daedalus_hub.html").read_text(encoding="utf-8")
    assert "ArgosDataNexus" not in hub
    assert "cdn.tailwindcss.com" not in (ROOT / "ui" / "templates" / "base.html").read_text(encoding="utf-8")
    nav = (ROOT / "ui" / "templates" / "_daedalus_nav.html").read_text(encoding="utf-8")
    assert "data-i18n=\"nav.studio\"" in nav
    assert "Model Registry" not in nav
    assert "Worker Named Configs" not in nav
    assert "dx-lang" in nav
    css = (ROOT / "ui" / "static" / "admin.css").read_text(encoding="utf-8")
    assert "--argos-accent: #c9a227" in css
    assert "dx-shell-sidebar" in css
    i18n = (ROOT / "app" / "static" / "i18n.js").read_text(encoding="utf-8")
    assert '"en"' in i18n and '"bg"' in i18n
    assert "dx.lang" in i18n
    html = (ROOT / "app" / "static" / "studio.html").read_text(encoding="utf-8")
    assert 'lang="en"' in html
    assert "/static/i18n.js" in html
    assert "Дедал / Daedalus" not in html
    assert "Умения — Skills" not in (ROOT / "ui" / "templates" / "skills.html").read_text(encoding="utf-8")
    import re
    cyr = re.compile(r"[А-Яа-яЁё]")
    playground = (ROOT / "ui" / "templates" / "daedalus_playground.html").read_text(encoding="utf-8")
    gs = (ROOT / "ui" / "templates" / "daedalus_game_studio.html").read_text(encoding="utf-8")
    pg_chrome = playground.split("<script>", 1)[0]
    gs_chrome = gs.split("<script>", 1)[0]
    assert not cyr.search(pg_chrome), "Playground HTML chrome must be English"
    assert not cyr.search(gs_chrome), "Game Studio HTML chrome must be English"
    assert 'data-i18n="pg.wizardTitle"' in playground
    assert 'data-i18n="gs.honesty"' in gs
    assert '"pg.wizardTitle"' in i18n
    assert "Честност" in i18n
    assert "не bake-off" in i18n
