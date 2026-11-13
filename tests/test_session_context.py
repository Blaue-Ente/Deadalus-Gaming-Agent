"""Session pins, attachments, and owner-selected skill injection."""
from __future__ import annotations

import time
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]

import pytest


@pytest.fixture
def harness_tmp(tmp_path, monkeypatch):
    monkeypatch.setenv("DAEDALUS_STANDALONE", "1")
    monkeypatch.setenv("DAEDALUS_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("DAEDALUS_PLAYGROUND", str(tmp_path / "projects"))
    monkeypatch.delenv("DAEDALUS_FULL_PERSONA", raising=False)
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
    from daedalus.chat_jobs import reset
    reset()


def test_karpathy_is_builtin(harness_tmp):
    from daedalus.learned_skills import list_skills, load_skill

    names = {s["id"] for s in list_skills()}
    assert "karpathy" in names
    loaded = load_skill("karpathy")
    assert loaded and "Think before coding" in (loaded.get("body") or "")


def test_disclose_pins_load_multiple_bodies(harness_tmp):
    from daedalus.learned_skills import disclose

    block = disclose("hello", pin_ids=["godot", "karpathy", "shell"])
    assert "SKILL CATALOG" in block
    assert "[pinned]" in block
    assert block.count("━━━ SKILL BODY:") == 3
    assert "Owner-pinned" in block


def test_disclose_extra_ids_still_one_body(harness_tmp):
    from daedalus.improvements import render_skills_block
    from daedalus.learned_skills import disclose

    dumped = render_skills_block(["godot", "asset_pipeline", "gameplay_bot"])
    assert dumped.count("━━━ SKILL BODY:") <= 1
    auto = disclose("godot characterbody", extra_ids=["godot"])
    assert auto.count("━━━ SKILL BODY:") <= 1


def test_pending_cannot_be_pinned(harness_tmp):
    from daedalus.learned_skills import disclose, draft_from_failure, list_pending

    draft_from_failure(kind="static", errors=["SyntaxError: boom"], context="x")
    rec = list_pending()[0]
    block = disclose("static boom", pin_ids=[rec["id"], rec.get("name")])
    assert "SyntaxError" not in block
    assert "Do this next time" not in block


def test_installed_skill_lists_and_discloses(harness_tmp):
    from daedalus.learned_skills import disclose, list_skills, write_installed

    path = write_installed(
        name="tdd-lite",
        description="Write a failing test first.",
        body="## When\nAny behavior change.\n\nRed, green, refactor.",
        source="addyosmani/agent-skills",
    )
    assert path.is_file()
    assert any(s["id"] == "tdd-lite" and s.get("origin") == "installed" for s in list_skills())
    live = disclose("tests", pin_ids=["tdd-lite"])
    assert "Red, green, refactor" in live


def test_session_pins_persist(harness_tmp):
    from daedalus.session_context import load, save

    row = save("studio-ab", skill_ids=["godot", "karpathy"], mcp_ids=["blender-mcp"])
    assert row["skill_ids"] == ["godot", "karpathy"]
    again = load("studio-ab")
    assert again["mcp_ids"] == ["blender-mcp"]


def test_mcp_allowed_respects_pins(harness_tmp):
    from daedalus.session_context import TurnPack, bind, mcp_allowed

    pack = TurnPack(session_id="s", mcp_ids=("blender-mcp",))
    with bind(pack):
        ok, _ = mcp_allowed("blender-mcp")
        deny, why = mcp_allowed("github-mcp")
    assert ok is True
    assert deny is False
    assert "github-mcp" in why
    free, _ = mcp_allowed("github-mcp")
    assert free is True


def test_attachment_text_and_image(harness_tmp):
    from daedalus.chat_attachments import prompt_block, save_bytes, vision_blocks, load_many

    txt = save_bytes("s1", "notes.md", b"# hello\npin this", content_type="text/markdown")
    assert txt["ok"] is True
    png = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00"
        b"\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    img = save_bytes("s1", "dot.png", png, content_type="image/png")
    assert img["kind"] == "image"
    rows = load_many("s1", [txt["id"], img["id"]])
    block = prompt_block(rows)
    assert "hello" in block
    assert "ATTACHED IMAGE" in block
    vis = vision_blocks(rows)
    assert vis and vis[0]["type"] == "image_url"
    assert vis[0]["image_url"]["url"].startswith("data:image/png;base64,")


def test_http_session_context_and_catalog_status(harness_tmp):
    from app.main import app

    client = TestClient(app)
    put = client.put(
        "/api/v1/daedalus/session-context",
        json={"session_id": "s1", "skill_ids": ["karpathy"], "mcp_ids": []},
    )
    assert put.status_code == 200
    assert put.json()["skill_ids"] == ["karpathy"]
    got = client.get("/api/v1/daedalus/session-context?session_id=s1")
    assert got.json()["skill_ids"] == ["karpathy"]

    cat = client.get("/api/v1/daedalus/skill-catalog")
    assert cat.status_code == 200
    body = cat.json()
    assert "status" in body or "items" in body

    caps = client.get("/capabilities")
    assert "session-context" in caps.json()["capabilities"]
    assert "skill-catalog" in caps.json()["capabilities"]


def test_chat_job_forwards_skill_ids(harness_tmp, monkeypatch):
    seen: dict = {}

    class Fake:
        async def chat(self, **kwargs):
            seen.update(kwargs)
            return {"ok": True, "reply": "ok", "session_id": kwargs.get("session_id")}

    monkeypatch.setattr("daedalus.agent.get_daedalus", lambda: Fake())
    from daedalus.chat_jobs import reset
    reset()
    from app.main import app
    client = TestClient(app)
    started = client.post(
        "/api/v1/daedalus/chat/jobs",
        json={
            "message": "use tdd",
            "session_id": "s1",
            "effort": "whisper",
            "skill_ids": ["karpathy"],
            "mcp_ids": ["blender-mcp"],
        },
    )
    assert started.status_code == 200
    jid = started.json()["job_id"]
    row = None
    for _ in range(50):
        row = client.get(f"/api/v1/daedalus/chat/jobs/{jid}").json()
        if row.get("status") in ("done", "error", "cancelled"):
            break
        time.sleep(0.05)
    assert row and row["status"] == "done"
    assert seen.get("skill_ids") == ["karpathy"]
    assert seen.get("mcp_ids") == ["blender-mcp"]


def test_attachment_upload_http(harness_tmp):
    from app.main import app

    client = TestClient(app)
    res = client.post(
        "/api/v1/daedalus/chat/attachments",
        data={"session_id": "up1"},
        files={"file": ("readme.md", b"# attached\n", "text/markdown")},
    )
    assert res.status_code == 200
    body = res.json()
    assert body.get("ok") is True
    assert body.get("id")


def test_studio_has_context_sheet():
    html = (ROOT / "app" / "static" / "studio.html").read_text(encoding="utf-8")
    js = (ROOT / "app" / "static" / "studio.js").read_text(encoding="utf-8")
    i18n = (ROOT / "app" / "static" / "i18n.js").read_text(encoding="utf-8")
    assert 'id="ctxplus"' in html
    assert 'id="ctxsheet"' in html
    assert 'data-ctx="skills"' in html           # skills are pinned from the context sheet, not the sidebar
    assert 'id="skillpins"' not in html
    assert "skill_ids" in js
    assert "attachment_ids" in js
    assert "studio.sessionSkills" in i18n
    assert "Умения за сесията" in i18n
    assert "studio.ctxMcp" in i18n


def test_catalog_sources_include_addy_and_voltagent():
    from daedalus.skill_catalog import TRUSTED_REPOS, _SKILL_SOURCES

    repos = {s["repo"] for s in _SKILL_SOURCES}
    assert "addyosmani/agent-skills" in repos
    assert any(s.get("root") == "skills" for s in _SKILL_SOURCES)
    assert "addyosmani/agent-skills" in TRUSTED_REPOS


def test_untrusted_install_queues_pending(harness_tmp, monkeypatch):
    from daedalus import skill_catalog as sc
    from daedalus.learned_skills import list_pending, list_skills

    monkeypatch.setattr(sc, "get_catalog_entry", lambda sid: {
        "id": sid,
        "title": "xlsx",
        "source": "some-random/repo",
        "rel": "",
        "url": "",
        "trusted": False,
        "text": "---\nname: xlsx\ndescription: sheets\n---\n\n# XLSX\nMake spreadsheets.\n" + ("more " * 40),
    })
    monkeypatch.setattr(sc, "_github_skill_md", lambda repo, rel: "")
    out = sc.install_skill("index:voltagent/xlsx")
    assert out.get("ok") is True
    assert out.get("pending") is True
    assert list_pending()
    assert not any(s["id"] == "xlsx" for s in list_skills())


def test_trusted_install_goes_live(harness_tmp, monkeypatch):
    from daedalus import skill_catalog as sc
    from daedalus.learned_skills import list_skills

    body = "---\nname: code-review-lite\ndescription: Review before merge.\n---\n\n# Review\nCheck tests and diffs.\n"
    monkeypatch.setattr(sc, "get_catalog_entry", lambda sid: {
        "id": sid,
        "title": "code-review-lite",
        "source": "addyosmani/agent-skills",
        "rel": "skills/code-review-lite/SKILL.md",
        "url": "",
        "trusted": True,
        "text": body,
    })
    monkeypatch.setattr(sc, "_github_skill_md", lambda repo, rel: body)
    out = sc.install_skill("skill:addyosmani/agent-skills/code-review-lite")
    assert out.get("ok") is True
    assert out.get("pending") is False
    assert any(s["id"] == "code-review-lite" for s in list_skills())
