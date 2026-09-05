"""Capacitor ship layer — wrap web Playground games, do not rewrite them."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from daedalus.playground_capacitor import (
    cap_layout,
    capacitor_config,
    merge_package_json,
    plugins_manifest,
    ship_advice,
    vite_needs_relative_base,
)


@pytest.fixture
def harness_tmp(tmp_path, monkeypatch):
    monkeypatch.setenv("DAEDALUS_STANDALONE", "1")
    monkeypatch.setenv("DAEDALUS_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("DAEDALUS_PLAYGROUND", str(tmp_path / "projects"))
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


def test_ship_advice_keeps_web_kind():
    web = ship_advice("vite")
    assert web["path"] == "capacitor"
    assert web["rewrite"] is False
    assert "android" in web["platforms"] and "ios" in web["platforms"]
    assert ship_advice("expo")["path"] == "eas"
    assert ship_advice("godot")["path"] == "editor-export"
    assert ship_advice("fastapi")["path"] == "none"


def test_ionic_is_optional_and_off_by_default():
    off = plugins_manifest(with_ionic=False)
    assert off["ionic_ui"]["enabled"] is False
    assert off["ionic_ui"]["packages"] == []
    on = plugins_manifest(with_ionic=True)
    assert "@ionic/core" in on["ionic_ui"]["packages"]
    ids = {p["id"] for p in off["recommended"]}
    assert "@capacitor/haptics" in ids
    assert "ionicframework.com" in off["upstream"]["ionic"]


def test_layout_hybrid_vs_vite_vs_static(tmp_path: Path):
    hybrid = tmp_path / "hyb"
    (hybrid / "hybrid").mkdir(parents=True)
    (hybrid / "hybrid" / "package.json").write_text("{}", encoding="utf-8")
    lay = cap_layout(hybrid)
    assert lay["workdir"] == "hybrid"
    assert lay["web_dir"] == "dist"
    assert lay["config_rel"].startswith("hybrid/")

    vite = tmp_path / "vite"
    vite.mkdir()
    (vite / "package.json").write_text("{}", encoding="utf-8")
    lay2 = cap_layout(vite)
    assert lay2["workdir"] == "."
    assert lay2["web_dir"] == "dist"

    static = tmp_path / "st"
    static.mkdir()
    (static / "index.html").write_text("<canvas></canvas>", encoding="utf-8")
    lay3 = cap_layout(static)
    assert lay3["web_dir"] == "www"


def test_merge_package_json_preserves_game_deps():
    existing = {
        "name": "racer",
        "scripts": {"dev": "vite", "build": "vite build"},
        "dependencies": {"three": "^0.170.0"},
    }
    merged = merge_package_json(existing, slug="racer", at_root=True)
    assert merged["dependencies"]["three"] == "^0.170.0"
    assert merged["dependencies"]["@capacitor/core"].startswith("^7")
    assert merged["scripts"]["build"] == "vite build"
    assert merged["scripts"]["cap:sync"] == "bash scripts/capacitor-sync.sh"
    assert "@ionic/core" not in merged["dependencies"]
    ionic = merge_package_json(existing, slug="racer", with_ionic=True, at_root=False)
    assert ionic["dependencies"]["@ionic/core"].startswith("^8")
    assert ionic["scripts"]["cap:sync"] == "bash ../scripts/capacitor-sync.sh"


def test_vite_relative_base_hint():
    assert vite_needs_relative_base("export default defineConfig({ plugins: [] })") is True
    assert vite_needs_relative_base("export default defineConfig({ base: './' })") is False


def test_capacitor_config_shape():
    cfg = capacitor_config(app_id="com.daedalus.mist", app_name="Mist", web_dir="dist")
    assert cfg["appId"] == "com.daedalus.mist"
    assert cfg["webDir"] == "dist"
    assert "store" not in json.dumps(cfg).lower()


def test_init_and_export_vite_project(harness_tmp):
    from daedalus import playground as pg
    from daedalus.playground_capacitor import export_capacitor, init_capacitor

    async def _run():
        created = await pg.create_project("Mist Racer", kind="vite", description="webgpu hover")
        slug = created["slug"]
        pg.write_file(slug, "package.json", json.dumps({
            "name": "mist",
            "scripts": {"dev": "vite", "build": "vite build"},
            "dependencies": {"three": "^0.170.0"},
        }, indent=2) + "\n")
        pg.write_file(slug, "vite.config.js", "import { defineConfig } from 'vite';\nexport default defineConfig({});\n")
        result = await init_capacitor(slug, bundle_id="com.studio.mist")
        assert result["ok"] is True
        assert result["skipped"] is False
        assert result["bundle_id"] == "com.studio.mist"
        assert result["vite_needs_relative_base"] is True
        root = pg.project_dir(slug)
        cfg = json.loads((root / "capacitor.config.json").read_text(encoding="utf-8"))
        assert cfg["webDir"] == "dist"
        pkg = json.loads((root / "package.json").read_text(encoding="utf-8"))
        assert pkg["dependencies"]["three"] == "^0.170.0"
        assert "@ionic/core" not in pkg["dependencies"]
        readme = (root / "studio/capacitor/README.md").read_text(encoding="utf-8")
        assert "store-ready" in readme
        assert "Do not wrap Daedalus Studio" in readme or "wrap Daedalus Studio" in readme
        assert (root / "src/native/daedalus-cap.js").is_file()
        script = (root / "scripts/capacitor-sync.sh").read_text(encoding="utf-8")
        assert "npx cap add" in script
        assert "npx cap sync" in script
        again = await init_capacitor(slug)
        assert "capacitor.config.json" not in again["written"]
        exported = await export_capacitor(slug)
        man = exported["manifest"]
        assert man["store_ready"] is False
        assert man["dist_ready"] is False
        assert man["ship"]["path"] == "capacitor"
        assert any("store" in s.lower() or "App Store" in s for s in man["next_steps"])

    asyncio.run(_run())


def test_init_skips_expo_and_godot(harness_tmp):
    from daedalus import playground as pg
    from daedalus.playground_capacitor import init_capacitor

    async def _run():
        expo = await pg.create_project("Native RN", kind="expo", description="skia")
        skipped = await init_capacitor(expo["slug"])
        assert skipped["ok"] is True
        assert skipped["skipped"] is True
        godot = await pg.create_project("Veins", kind="godot", description="tscn")
        skipped2 = await init_capacitor(godot["slug"])
        assert skipped2["skipped"] is True

    asyncio.run(_run())


def test_hybrid_layout_after_framework7(harness_tmp):
    from daedalus import playground as pg
    from daedalus.playground_capacitor import init_capacitor
    from daedalus.playground_hybrid_mobile import init_hybrid_mobile

    async def _run():
        created = await pg.create_project("Shell App", kind="vite", description="hybrid")
        slug = created["slug"]
        await init_hybrid_mobile(slug, bundle_id="com.studio.shell")
        result = await init_capacitor(slug, bundle_id="com.studio.shell")
        assert result["ok"] is True
        assert result["layout"]["workdir"] == "hybrid"
        root = pg.project_dir(slug)
        assert (root / "hybrid/capacitor.config.json").is_file()
        pkg = json.loads((root / "hybrid/package.json").read_text(encoding="utf-8"))
        assert "framework7" in pkg["dependencies"]
        assert "@capacitor/core" in pkg["dependencies"]

    asyncio.run(_run())


def test_infer_preset_capacitor():
    from daedalus.playground_studio_mode import infer_studio_preset
    assert infer_studio_preset("wrap with capacitor for android") == "hybrid_mobile"
    assert infer_studio_preset("ionic lists and settings around a canvas game") == "hybrid_mobile"
    from daedalus.game_platform_guide import suggest_kind_heuristic

    kind, reason = suggest_kind_heuristic("Pocket", "iOS Android capacitor wrap of the canvas game")
    assert kind == "vite"
    assert "Capacitor" in reason or "capacitor" in reason.lower()
    expo_kind, _ = suggest_kind_heuristic("Go", "expo react native skia mobile")
    assert expo_kind == "expo"


def test_hybrid_preset_lists_capacitor():
    from daedalus.playground_integration_catalog import get_integration, integrations_for_preset

    ids = integrations_for_preset("hybrid_mobile")
    assert "capacitor" in ids
    assert "capacitor_plugins" in ids
    cap = get_integration("capacitor")
    assert cap and "ionic-team/capacitor" in cap["url"]
    ionic = get_integration("ionic")
    assert ionic and ionic.get("optional") is True


def test_select_skills_hits_capacitor_ship():
    from daedalus.improvements import select_skills

    names = select_skills("wrap the vite game with capacitor for ios and android", "build")
    assert "capacitor_ship" in names
