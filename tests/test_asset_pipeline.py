"""F4 — asset pipeline v2: atlas, SFX, font, fail-closed mesh jobs."""
from __future__ import annotations

from pathlib import Path

from daedalus.asset_contract import check as contract_check
from daedalus.asset_pipeline import prove_assets, run_action
from daedalus.atlas import frames_overlap, pack_sprites, paint_tile, write_atlas, write_tileset
from daedalus.game_studio_gates import run_asset_check, run_gates
from daedalus.mesh_jobs import greybox_glb_bytes, providers, submit, write_greybox
from daedalus.native_tools import NATIVE_TOOL_SPECS
from daedalus.png_codec import decode_png, encode_rgba
from daedalus.sfx_kit import glyph_bits, render_font, render_sfx, wav_ok, write_font, write_sfx
from daedalus.subagents import tools_for_role


def test_png_roundtrip_and_atlas_frames():
    w, h = 8, 4
    px = bytes((x * 7 + y * 13) % 256 for y in range(h) for x in range(w) for _ in range(4))
    raw = encode_rgba(w, h, px)
    dw, dh, out = decode_png(raw)
    assert (dw, dh, out) == (w, h, px)
    sprites = [(n, 16, 16, paint_tile(n)) for n in ("floor", "wall", "door")]
    png, meta = pack_sprites(sprites)
    aw, ah, _ = decode_png(png)
    assert aw >= 16 and ah >= 16
    assert not frames_overlap(meta["frames"])
    names = {f["name"] for f in meta["frames"]}
    assert names == {"floor", "wall", "door"}
    for fr in meta["frames"]:
        assert 0 <= fr["x"] < aw
        assert fr["x"] + fr["w"] <= aw
        assert fr["y"] + fr["h"] <= ah


def test_tileset_grid_math(tmp_path: Path):
    result = write_tileset(tmp_path)
    assert result["ok"] is True
    meta_path = tmp_path / "assets" / "tileset.json"
    import json
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    w, h, _ = decode_png((tmp_path / "assets" / "tileset.png").read_bytes())
    assert w == meta["columns"] * meta["tilewidth"]
    assert len(meta["tiles"]) == 8
    assert meta["tiles"][0]["name"] == "floor"


def test_sfx_wav_and_font_glyph(tmp_path: Path):
    sfx = write_sfx(tmp_path)
    assert sfx["ok"] is True
    for name in ("blip", "hit", "boost", "pickup", "fail", "ui"):
        data = (tmp_path / "assets" / "sfx" / f"{name}.wav").read_bytes()
        assert wav_ok(data)
        assert len(data) > 44
    assert wav_ok(render_sfx("blip"))
    font = write_font(tmp_path)
    assert font["glyphs"] == 95
    png, meta = render_font()
    w, h, px = decode_png(png)
    glyph = meta["glyphs"]["A"]
    lit = 0
    for gy in range(glyph["h"]):
        for gx in range(glyph["w"]):
            i = ((glyph["y"] + gy) * w + (glyph["x"] + gx)) * 4
            if px[i] > 0:
                lit += 1
    assert lit > 8
    assert glyph_bits(ord("A")) != 0
    assert glyph_bits(32) == 0


def test_mesh_submit_fails_closed_without_keys(tmp_path, monkeypatch):
    monkeypatch.setenv("DAEDALUS_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("DAEDALUS_PLAYGROUND", str(tmp_path / "projects"))
    for key in ("FAL_KEY", "FAL_API_KEY", "TRIPO_API_KEY", "MESHY_API_KEY", "SUNO_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    from daedalus.paths import data_root, playground_projects_dir
    data_root.cache_clear()
    playground_projects_dir.cache_clear()
    caps = providers()
    assert caps["fal"] is False
    assert caps["tripo"] is False
    assert caps["meshy"] is False
    assert caps["any_mesh"] is False
    assert caps["suno_submit"] is False
    slug = "kit"
    (playground_projects_dir() / slug).mkdir(parents=True)
    r = submit(slug, "a hover ship")
    assert r["ok"] is False
    assert r["need_key"] is True
    glbs = list((playground_projects_dir() / slug / "assets").glob("*.glb"))
    assert glbs == []
    music = run_action("music", slug)
    assert music["ok"] is False
    assert music["handoff"] is True
    assert "music.mp3" in music["drop"]


def test_mesh_poll_with_mock_transport(tmp_path, monkeypatch):
    monkeypatch.setenv("DAEDALUS_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("DAEDALUS_PLAYGROUND", str(tmp_path / "projects"))
    monkeypatch.setenv("FAL_KEY", "test-not-a-real-key")
    for key in ("FAL_API_KEY", "TRIPO_API_KEY", "MESHY_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    from daedalus.paths import data_root, playground_projects_dir
    data_root.cache_clear()
    playground_projects_dir.cache_clear()
    slug = "ship"
    (playground_projects_dir() / slug).mkdir(parents=True)
    glb = greybox_glb_bytes()
    calls: list[str] = []

    def post_json(url, headers, body):
        calls.append("post")
        assert "fal.run" in url
        assert "Key test-not-a-real-key" in headers.get("Authorization", "")
        assert body.get("prompt")
        return {
            "request_id": "req-1",
            "status_url": "https://queue.fal.run/status/req-1",
            "response_url": "https://queue.fal.run/res/req-1",
        }

    def get_json(url, headers):
        calls.append("get_json")
        if "status" in url:
            return {"status": "COMPLETED"}
        return {"model_mesh": {"url": "https://cdn.example/hero.glb"}}

    def get_bytes(url, headers):
        calls.append("get_bytes")
        assert url.endswith(".glb")
        return glb

    queued = run_action(
        "mesh",
        slug,
        prompt="hero hover ship",
        name="hero",
        transport={"post_json": post_json, "get_json": get_json, "get_bytes": get_bytes},
    )
    assert queued["ok"] is True
    assert queued["status"] == "running"
    done = run_action(
        "status",
        slug,
        job_id=queued["id"],
        transport={"post_json": post_json, "get_json": get_json, "get_bytes": get_bytes},
    )
    assert done["ok"] is True
    assert done["status"] == "done"
    path = playground_projects_dir() / slug / "assets" / "hero.glb"
    assert path.is_file()
    assert path.read_bytes()[:4] == b"glTF"
    assert "post" in calls and "get_bytes" in calls


def test_greybox_is_not_a_hero(tmp_path: Path):
    r = write_greybox(tmp_path)
    assert r["ok"] is True
    assert r["hero"] is False
    assert r["greybox"] is True
    glb = tmp_path / "assets" / "greybox_proxy.glb"
    assert glb.read_bytes()[:4] == b"glTF"
    checked = contract_check(r["contract"]["contract"])
    assert checked["ok"] is True
    proof = prove_assets(tmp_path)
    assert proof["ok"] is True
    assert "greybox_proxy.glb" in proof["glb"]


def test_claimed_empty_glb_fails_prove(tmp_path: Path):
    assets = tmp_path / "assets"
    assets.mkdir()
    studio = tmp_path / "studio"
    studio.mkdir()
    (assets / "hero.glb").write_bytes(b"")
    (studio / "asset_contract.json").write_text(
        '{"assets":[{"name":"hero","export_target":"res://assets/hero.glb"}]}\n',
        encoding="utf-8",
    )
    proof = prove_assets(tmp_path, wants_3d=True)
    assert proof["ok"] is False
    blob = " ".join(proof["errors"])
    assert "hero.glb" in blob


def test_native_assets_coder_only():
    spec = next(t for t in NATIVE_TOOL_SPECS if t["name"] == "assets")
    assert "mesh" in spec["description"]
    assert "assets" in tools_for_role("coder")
    assert "assets" not in tools_for_role("explorer")
    assert "assets" not in tools_for_role("verify")


def test_asset_check_pre_build_does_not_block():
    ctx = {
        "gdd": "GDD " + ("x" * 80),
        "config": {
            "studio_preset": "racing",
            "kind": "vite",
            "brainstorm": {"genre": "racing", "idea": "F-Zero night city"},
            "build_mode": "guided",
        },
        "plan": {"stages": [{"title": "boot", "goal": "menu"}] * 3},
        "project": {"kind": "vite", "slug": "mist"},
    }
    row = run_asset_check(ctx)
    assert row["gate"] == "asset-check"
    assert row["passed"] is True
    assert row["blocking"] is False
    combined = run_gates(ctx)
    assert combined["blocking"] is False
    assert combined["gates"]["asset-check"]["passed"] is True


def test_pipeline_writes_kit(tmp_path, monkeypatch):
    monkeypatch.setenv("DAEDALUS_PLAYGROUND", str(tmp_path / "projects"))
    monkeypatch.setenv("DAEDALUS_DATA", str(tmp_path / "data"))
    for key in ("FAL_KEY", "FAL_API_KEY", "TRIPO_API_KEY", "MESHY_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    from daedalus.paths import data_root, playground_projects_dir
    data_root.cache_clear()
    playground_projects_dir.cache_clear()
    slug = "dungeon"
    root = playground_projects_dir() / slug
    root.mkdir(parents=True)
    atlas = run_action("atlas", slug)
    assert atlas["ok"] is True
    assert (root / "assets" / "atlas.png").is_file()
    sfx = run_action("sfx", slug)
    assert len(sfx["files"]) == 6
    font = run_action("font", slug)
    assert font["ok"] is True
    st = run_action("status", slug)
    assert st["atlas"] is True
    assert st["font"] is True
    write_atlas(root)
    proof = prove_assets(root)
    assert proof["ok"] is True


def test_godot_sidecars_for_kit(tmp_path: Path):
    (tmp_path / "project.godot").write_text("[application]\n", encoding="utf-8")
    write_sfx(tmp_path)
    write_atlas(tmp_path)
    from daedalus.godot_bridge import ensure_import_sidecars
    written = ensure_import_sidecars(tmp_path)
    rels = set(written)
    assert any(r.endswith("atlas.png.import") for r in rels)
    assert any("blip.wav.import" in r for r in rels)
