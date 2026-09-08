"""Kernel visual proof + durable Playground README status."""
from __future__ import annotations

import asyncio
from pathlib import Path

from daedalus import native_tools
from daedalus.native_tools import to_anthropic_messages, to_openai_messages
from daedalus.project_status import STATUS_MARK, ensure_status_readme, record_proof
from daedalus.visual_proof import ProofResult, is_visual_kind, run_visual_proof, vision_blocks_for_paths


def test_kinds():
    assert is_visual_kind("static")
    assert is_visual_kind("godot")
    assert is_visual_kind("vite")
    assert not is_visual_kind("fastapi")
    assert not is_visual_kind("unity")


def test_status_readme_create_and_proof_line(tmp_path: Path):
    assert ensure_status_readme(tmp_path, name="Snake", kind="static", description="canvas")
    text = (tmp_path / "README.md").read_text(encoding="utf-8")
    assert "# Snake" in text
    assert STATUS_MARK in text
    assert "| Name | Description | Size |" in text
    assert ensure_status_readme(tmp_path, name="Snake", kind="static") is False
    record_proof(tmp_path, "Chromium ok", frames=["studio/proof/frame_000.png"])
    text = (tmp_path / "README.md").read_text(encoding="utf-8")
    assert "Chromium ok" in text
    assert "studio/proof/frame_000.png" in text


def test_status_readme_appends_to_existing(tmp_path: Path):
    (tmp_path / "README.md").write_text("# Already\n\nHello.\n", encoding="utf-8")
    assert ensure_status_readme(tmp_path, name="G", kind="godot")
    text = (tmp_path / "README.md").read_text(encoding="utf-8")
    assert text.startswith("# Already")
    assert STATUS_MARK in text
    assert "godot" in text


def test_missing_chromium_gap(monkeypatch, tmp_path: Path):
    async def _verify(slug, kind):
        return {
            "ok": False, "skipped": True, "error": "chromium_not_found",
            "summary": "no chrome", "blank_screen": False, "screenshot_path": None,
        }

    monkeypatch.setattr("daedalus.playground_browser.verify_app", _verify)
    monkeypatch.setattr("daedalus.playground.project_dir", lambda slug: tmp_path)
    result = asyncio.run(run_visual_proof("snake-game", "static"))
    assert result.ok is False
    assert result.skipped is True
    assert result.honest_gap == "chromium_missing"
    assert result.tool == "browser"


def test_godot_missing_binary_gap(monkeypatch, tmp_path: Path):
    monkeypatch.setattr("daedalus.godot_runtime_probe.godot_bin", lambda: "")
    monkeypatch.setattr("daedalus.playground.project_dir", lambda slug: tmp_path)
    result = asyncio.run(run_visual_proof("dungeon", "godot"))
    assert result.ok is False
    assert result.honest_gap == "godot_missing"
    assert result.tool == "engine"
    assert "Play" in result.summary or "PATH" in result.summary


def test_vision_blocks_and_anthropic_convert(tmp_path: Path):
    png = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01"
        b"\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    shot = tmp_path / "frame.png"
    shot.write_bytes(png)
    blocks = vision_blocks_for_paths(tmp_path, ["frame.png"])
    assert blocks and blocks[0]["type"] == "image_url"
    messages = [{
        "role": "user",
        "content": [{"type": "text", "text": "look"}, blocks[0]],
    }]
    oa = to_openai_messages("", messages)
    assert oa[-1]["role"] == "user"
    assert isinstance(oa[-1]["content"], list)
    anth = to_anthropic_messages(messages)
    kinds = [b.get("type") for b in anth[0]["content"]]
    assert "image" in kinds
    assert "text" in kinds


CLEAN_CANVAS = (
    "<!DOCTYPE html><html><body>"
    '<canvas id="g" width="320" height="240"></canvas>'
    "<script>function loop(t){requestAnimationFrame(loop);}"
    "requestAnimationFrame(loop);</script>"
    "</body></html>"
)


def test_write_file_result_names_the_slug_path(monkeypatch):
    async def fake_get(slug):
        return {"slug": slug, "kind": "static"}

    monkeypatch.setattr("daedalus.playground.get_project", fake_get)
    monkeypatch.setattr("daedalus.playground.write_file", lambda *a, **k: None)
    monkeypatch.setattr("daedalus.project_status.record_built", lambda *a, **k: None)

    text = asyncio.run(native_tools.execute_native_tool(
        "write_file",
        {"slug": "snake-game", "path": "index.html", "content": CLEAN_CANVAS},
        memory=None, session_id="", read_file_fn=lambda *a, **k: {},
    ))
    assert "FAILED" not in text
    assert "playground/projects/snake-game/index.html" in text
    assert "Do not find /workspace" in text
    assert "visual proof" in text.lower()


def test_write_file_accepts_filename_alias(monkeypatch):
    async def fake_get(slug):
        return {"slug": slug, "kind": "static"}

    monkeypatch.setattr("daedalus.playground.get_project", fake_get)
    monkeypatch.setattr("daedalus.playground.write_file", lambda *a, **k: None)
    monkeypatch.setattr("daedalus.project_status.record_built", lambda *a, **k: None)

    text = asyncio.run(native_tools.execute_native_tool(
        "write_file",
        {"project": "dungeon-keys", "filename": "index.html", "code": CLEAN_CANVAS},
        memory=None, session_id="", read_file_fn=lambda *a, **k: {},
    ))
    assert "FAILED" not in text
    assert "dungeon-keys/index.html" in text


def test_browser_playground_url_runs_visual_proof(monkeypatch, tmp_path):
    from daedalus.visual_proof import ProofResult

    async def fake_get(slug):
        return {"slug": slug, "kind": "static"}

    async def fake_proof(slug, kind="static"):
        return ProofResult(
            ok=True, skipped=False, kind=kind, slug=slug, tool="browser",
            summary="kernel opened the canvas", frames=["studio/proof/frame_000.png"],
        )

    monkeypatch.setattr("daedalus.playground.get_project", fake_get)
    monkeypatch.setattr("daedalus.visual_proof.run_visual_proof", fake_proof)
    text = asyncio.run(native_tools.execute_native_tool(
        "browser",
        {"action": "goto", "url": "https://file///playground/projects/dungeon-keys/index.html"},
        memory=None, session_id="playground:live-eval", read_file_fn=lambda *a, **k: {},
    ))
    assert "visual_proof OK" in text
    assert "dungeon-keys" in text


def test_new_project_gets_status_readme(tmp_path, monkeypatch):
    monkeypatch.setenv("DAEDALUS_STANDALONE", "1")
    monkeypatch.setenv("DAEDALUS_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("DAEDALUS_PLAYGROUND", str(tmp_path / "projects"))
    from daedalus.paths import data_root, playground_projects_dir, product_root, repo_root
    data_root.cache_clear()
    playground_projects_dir.cache_clear()
    product_root.cache_clear()
    repo_root.cache_clear()
    from daedalus.store import reset_sqlite_pool
    reset_sqlite_pool()
    from daedalus import playground as pg
    root = tmp_path / "projects"
    root.mkdir(parents=True, exist_ok=True)
    pg.PROJECTS_DIR = root

    async def _run():
        created = await pg.create_project("Snake Game", kind="static", description="canvas")
        pdir = pg.project_dir(created["slug"])
        text = (pdir / "README.md").read_text(encoding="utf-8")
        assert STATUS_MARK in text
        assert "static" in text
        godot = await pg.create_project("Dungeon", kind="godot", description="tscn")
        gtext = (pg.project_dir(godot["slug"]) / "README.md").read_text(encoding="utf-8")
        assert STATUS_MARK in gtext
        assert "godot" in gtext

    asyncio.run(_run())
    reset_sqlite_pool()


def test_proof_result_tool_text_and_nudge():
    r = ProofResult(
        ok=False, skipped=False, kind="static", slug="s", tool="browser",
        summary="blank", blank_screen=True, frames=["studio/proof/frame_000.png"],
    )
    assert "FAILED" in r.as_tool_text()
    assert "VISUAL PROOF" in r.nudge_block()
    assert "blank" in r.nudge_block().lower() or "nothing" in r.nudge_block().lower()
