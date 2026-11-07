"""Dissection + Play Lab — specimen games for original builds."""
from __future__ import annotations

import json
import zipfile
from io import BytesIO
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def harness_tmp(tmp_path, monkeypatch):
    monkeypatch.setenv("DAEDALUS_STANDALONE", "1")
    monkeypatch.setenv("DAEDALUS_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("DAEDALUS_PLAYGROUND", str(tmp_path / "projects"))
    monkeypatch.delenv("DAEDALUS_USE_ADN_DB", raising=False)
    monkeypatch.delenv("DAEDALUS_LAB_PULL", raising=False)
    from daedalus.paths import data_root, playground_projects_dir, product_root, repo_root
    data_root.cache_clear()
    playground_projects_dir.cache_clear()
    product_root.cache_clear()
    repo_root.cache_clear()
    from daedalus.store import reset_sqlite_pool
    reset_sqlite_pool()
    yield tmp_path
    from daedalus.play_lab import stop_all_http
    stop_all_http()
    reset_sqlite_pool()


def test_classify_source_kinds():
    from daedalus.dissection import classify_source
    assert classify_source("https://fm1.moises.cloud/")[0] == "url"
    assert classify_source("https://github.com/foo/bar")[0] == "git"
    assert classify_source("owner/repo")[0] == "git"
    assert classify_source("/tmp/missing-game.html")[0] == "path"
    assert classify_source("Flappy Bird") == ("name", "Flappy Bird")
    assert classify_source("lab-abc12345")[0] == "lab"


def test_fingerprint_three_webgpu_not_phaser():
    from daedalus.dissection import fingerprint_stack
    html = "<script type=module>import {WebGPURenderer} from 'three'</script>"
    stack = fingerprint_stack(html)
    assert stack["engine"] == "three.js"
    assert stack["renderer"] == "WebGPU"
    assert "phaser" not in stack["markers"]


def test_specimen_block_forbids_cloning():
    from daedalus.dissection import LEGAL_LINE, format_specimen_block
    block = format_specimen_block({
        "title": "Mist Demo",
        "summary": "Hover racer.",
        "mechanics": ["boost", "fail"],
        "core_loop": "race → boost → fail",
        "steal": ["camera follow"],
        "do_not_copy": ["ships.png"],
        "stack": {"engine": "three.js", "renderer": "WebGPU", "markers": ["three.js"]},
        "lab_id": "lab-test-1",
    })
    assert "ДИСЕКЦИЯ" in block
    assert "Mist Demo" in block
    assert "three.js" in block
    assert LEGAL_LINE in block
    assert "ships.png" in block


def test_create_lab_install_zip_and_http(harness_tmp):
    from daedalus.play_lab import (
        create_lab,
        destroy_lab,
        install_into_lab,
        list_labs,
        probe_host,
        start_local_http,
        stop_local_http,
    )

    host = probe_host()
    assert host["filesystem"] is True
    assert host["local_http"] is True
    assert "docker" in host

    lab = create_lab(title="Tiny Racer", source="test", source_kind="install")
    lab_id = lab["id"]
    assert lab_id.startswith("lab-")
    assert any(row["id"] == lab_id for row in list_labs())

    zbuf = BytesIO()
    with zipfile.ZipFile(zbuf, "w") as zf:
        zf.writestr(
            "index.html",
            "<html><body><canvas></canvas><script>requestAnimationFrame(()=>{})</script></body></html>",
        )
        zf.writestr("game.js", "console.log('loop')")
    zpath = Path(harness_tmp) / "tiny.zip"
    zpath.write_bytes(zbuf.getvalue())

    installed = install_into_lab(lab_id, zpath)
    assert installed["ok"] is True
    assert installed["playable"] == "index.html"

    served = start_local_http(lab_id)
    assert served["ok"] is True
    assert served["port"] > 0
    import httpx
    page = httpx.get(served["url"], timeout=5)
    assert page.status_code == 200
    assert "<canvas" in page.text
    stop_local_http(lab_id)
    assert destroy_lab(lab_id)["ok"] is True


def test_zip_slip_and_native_installer_refused(harness_tmp):
    from daedalus.play_lab import create_lab, install_into_lab

    lab = create_lab(title="Trap")
    lab_id = lab["id"]
    zbuf = BytesIO()
    with zipfile.ZipFile(zbuf, "w") as zf:
        zf.writestr("../escape.txt", "nope")
    zpath = Path(harness_tmp) / "slip.zip"
    zpath.write_bytes(zbuf.getvalue())
    with pytest.raises(ValueError, match="zip slip"):
        install_into_lab(lab_id, zpath)

    exe = Path(harness_tmp) / "game.exe"
    exe.write_bytes(b"MZ")
    with pytest.raises(ValueError, match="native installer"):
        install_into_lab(lab_id, exe)


def test_save_load_dissection_and_gdd(harness_tmp):
    from daedalus.dissection import (
        format_specimen_block,
        load_dissection,
        prompt_block_for_slug,
        save_dissection,
    )
    from daedalus.game_studio import generate_gdd_lite
    from daedalus.paths import playground_projects_dir

    slug = "mist-run"
    (playground_projects_dir() / slug).mkdir(parents=True)
    specimen = {
        "title": "Formula Minus One",
        "summary": "Hover racer в браузъра.",
        "mechanics": ["turbo", "fail"],
        "core_loop": "lap → boost → integrity",
        "stack": {"engine": "three.js", "renderer": "WebGPU", "markers": ["three.js", "webgpu"]},
        "steal": ["vehicle profiles"],
        "do_not_copy": ["ships"],
    }
    specimen["specimen_block"] = format_specimen_block(specimen)
    save_dissection(slug, specimen)
    loaded = load_dissection(slug)
    assert loaded and loaded["title"] == "Formula Minus One"
    block = prompt_block_for_slug(slug)
    assert "SPECIMEN" in block
    assert "ДИСЕКЦИЯ" in block
    md = (playground_projects_dir() / slug / "design" / "dissection.md").read_text(encoding="utf-8")
    assert "Дисекция" in md

    gdd = generate_gdd_lite(
        {"title": "Mist Run", "genre": "racing", "idea": "night city hover"},
        studio_preset="racing",
        kind="vite",
        dissection=loaded,
    )
    assert "Дисекция" in gdd or "ДИСЕКЦИЯ" in gdd
    assert "Formula Minus One" in gdd


def test_dissect_local_html_without_network(harness_tmp):
    import asyncio
    from daedalus.dissection import dissect
    from daedalus.play_lab import create_lab, write_install_file

    lab = create_lab(title="Canvas Toy")
    write_install_file(
        lab["id"],
        "index.html",
        "<html><body><canvas id=c></canvas>"
        "<script src='https://cdn.example/phaser.min.js'></script></body></html>",
    )
    result = asyncio.run(dissect(
        lab_id=lab["id"],
        create_lab=False,
        live=False,
        project_name="My Game",
        description="platformer",
    ))
    assert result["ok"] is True
    assert result["lab_id"] == lab["id"]
    assert result["stack"]["engine"] == "phaser"
    assert "specimen_block" in result
    assert "НЕ копирай" in result["specimen_block"]


def test_native_tools_include_dissection():
    from daedalus.native_tools import NATIVE_TOOL_SPECS
    names = {t["name"] for t in NATIVE_TOOL_SPECS}
    assert "dissect_game" in names
    assert "play_lab" in names


def test_dissection_http_and_capabilities(harness_tmp):
    from app.main import app
    client = TestClient(app)

    caps = client.get("/capabilities")
    assert "dissection" in caps.json()["capabilities"]
    assert "play-lab" in caps.json()["capabilities"]

    host = client.get("/api/v1/daedalus/labs/status")
    assert host.status_code == 200
    assert host.json()["local_http"] is True

    created = client.post("/api/v1/daedalus/labs", json={"title": "HTTP Lab"})
    assert created.status_code == 200
    lab_id = created.json()["id"]
    assert lab_id.startswith("lab-")

    html = Path(harness_tmp) / "index.html"
    html.write_text("<html><canvas></canvas><script>THREE.WebGPURenderer</script></html>", encoding="utf-8")
    installed = client.post(
        f"/api/v1/daedalus/labs/{lab_id}/install",
        json={"path": str(html)},
    )
    assert installed.status_code == 200
    assert installed.json()["ok"] is True

    dissected = client.post(
        "/api/v1/daedalus/dissect",
        json={"lab_id": lab_id, "create_lab": False, "live": False},
    )
    assert dissected.status_code == 200
    body = dissected.json()
    assert body["ok"] is True
    assert body["stack"]["engine"] == "three.js"

    listed = client.get("/api/v1/daedalus/labs")
    assert any(r["id"] == lab_id for r in listed.json()["labs"])


def test_skill_select_dissection():
    from daedalus.improvements import _SKILLS_CACHE, select_skills
    from daedalus import improvements as imp
    imp._SKILLS_LOADED = False
    _SKILLS_CACHE.clear()
    names = select_skills("Направи дисекция на тази игра като мостра", "build")
    assert "dissection" in names
