"""Mentor helm — toggle, inspect, steer. No live LLM."""
from __future__ import annotations

from daedalus.build_loop import BuildLoop
from daedalus.tool_router import route

LUMEN = (
    "Направи нова Playground игра Lumen Bloom, kind=vite, Phaser 3. "
    "Boot Menu Play scenes, particles, one playable slice."
)
SCAFFOLD = (
    "import './style.css'\n"
    "document.querySelector('#app').innerHTML = `\n"
    "  <div>\n"
    "    <img src=\"/vite.svg\" class=\"logo\" alt=\"Vite logo\" />\n"
    "    <h1>Hello Vite!</h1>\n"
    "  </div>\n"
    "`\n"
)
PHASER_BOOT = (
    "import Phaser from 'phaser';\n"
    "import { Boot } from './scenes/Boot.js';\n"
    "new Phaser.Game({ type: Phaser.AUTO, parent: 'game', scene: [Boot] });\n"
)
BOOT_SCENE = (
    "import Phaser from 'phaser';\n"
    "export class Boot extends Phaser.Scene { constructor() { super('Boot'); } }\n"
)


def _with_boot_scene(root):
    d = root / "src" / "scenes"
    d.mkdir(parents=True, exist_ok=True)
    (d / "Boot.js").write_text(BOOT_SCENE, encoding="utf-8")


def test_mentor_env_off_skips_steer(monkeypatch):
    from daedalus import mentor

    monkeypatch.setenv("DAEDALUS_MENTOR", "0")
    assert mentor.enabled() is False
    rt = route(LUMEN)
    loop = BuildLoop(rt, LUMEN)
    assert mentor.steer(loop) is None
    assert mentor.extra_round_budget(loop) == 0


def test_mentor_env_on_beats_kv(tmp_path, monkeypatch):
    from daedalus import mentor
    from daedalus.paths import data_root

    monkeypatch.setenv("DAEDALUS_DATA", str(tmp_path))
    monkeypatch.setenv("DAEDALUS_MENTOR", "1")
    data_root.cache_clear()
    mentor.set_enabled(False)
    assert mentor.enabled() is True
    st = mentor.status()
    assert st["env_locked"] is True
    assert st["enabled"] is True


def test_mentor_default_on_when_unset(tmp_path, monkeypatch):
    from daedalus import mentor
    from daedalus.paths import data_root

    monkeypatch.setenv("DAEDALUS_DATA", str(tmp_path))
    monkeypatch.delenv("DAEDALUS_MENTOR", raising=False)
    data_root.cache_clear()
    assert mentor.enabled() is True
    mentor.set_enabled(False)
    assert mentor.enabled() is False


def test_mentor_scaffold_steers_write_main(tmp_path, monkeypatch):
    from daedalus import mentor

    monkeypatch.setenv("DAEDALUS_MENTOR", "1")
    monkeypatch.setattr("daedalus.mentor._rag_hits", lambda q: ["phaser.Game in src/main.js"])
    slug = "lumen-bloom"
    root = tmp_path / slug
    (root / "src").mkdir(parents=True)
    (root / "index.html").write_text(
        '<!doctype html><canvas id="lumen-canvas"></canvas><script type="module" src="/src/main.js"></script>',
        encoding="utf-8",
    )
    (root / "src" / "main.js").write_text(SCAFFOLD, encoding="utf-8")
    monkeypatch.setattr("daedalus.playground.project_dir", lambda s: root)
    rt = route(LUMEN)
    loop = BuildLoop(rt, LUMEN)
    loop.slug = slug
    loop.kind = "vite"
    view = mentor.inspect_project(slug, kind="vite")
    assert view.scaffold_main is True
    assert view.has_canvas is True
    assert view.entry_boots is False
    decision = mentor.steer(loop)
    assert decision is not None
    assert decision.action == "continue"
    assert decision.next_tool == "write_file"
    assert "main.js" in str(decision.next_args.get("path") or "")
    nxt = mentor.progress_next(loop)
    assert "write_file" in nxt
    assert "repeat generate_image" not in nxt


def test_mentor_image_fail_twice_forbids(tmp_path, monkeypatch):
    from daedalus import mentor

    monkeypatch.setenv("DAEDALUS_MENTOR", "1")
    monkeypatch.setattr("daedalus.mentor._rag_hits", lambda q: [])
    slug = "lumen-bloom"
    root = tmp_path / slug
    (root / "src").mkdir(parents=True)
    (root / "index.html").write_text('<canvas id="g"></canvas>', encoding="utf-8")
    (root / "src" / "main.js").write_text(SCAFFOLD, encoding="utf-8")
    monkeypatch.setattr("daedalus.playground.project_dir", lambda s: root)
    rt = route(LUMEN)
    loop = BuildLoop(rt, LUMEN)
    loop.slug = slug
    loop.kind = "vite"
    loop.observe("generate_image", {"prompt": "sprite"}, "── generate_image FAILED 403 ──", ok=False)
    loop.observe("generate_image", {"prompt": "sprite"}, "── generate_image FAILED 403 ──", ok=False)
    decision = mentor.steer(loop)
    assert decision is not None
    assert "generate_image" in decision.forbid_tools
    assert "generate_image" not in decision.next_tool


def test_mentor_booted_entry_can_ship(tmp_path, monkeypatch):
    from daedalus import mentor
    from daedalus.visual_proof import ProofResult

    monkeypatch.setenv("DAEDALUS_MENTOR", "1")
    monkeypatch.setattr("daedalus.mentor._rag_hits", lambda q: [])
    slug = "lumen-bloom"
    root = tmp_path / slug
    (root / "src").mkdir(parents=True)
    (root / "index.html").write_text('<div id="game"></div>', encoding="utf-8")
    (root / "src" / "main.js").write_text(PHASER_BOOT, encoding="utf-8")
    _with_boot_scene(root)
    monkeypatch.setattr("daedalus.playground.project_dir", lambda s: root)
    rt = route(LUMEN)
    loop = BuildLoop(rt, LUMEN, fix_rounds=3)
    loop.slug = slug
    loop.kind = "vite"
    loop.observe("playground_new", {"name": slug, "kind": "vite"}, f"── playground_new OK slug={slug} kind=vite ──", ok=True)
    loop.observe("write_file", {"slug": slug, "path": "src/main.js"}, "── write_file OK ──", ok=True)
    loop.observe("browser", {"slug": slug}, "── visual_proof OK ──", ok=True)
    loop.note_kernel_proof(ProofResult(
        ok=True, skipped=False, kind="vite", slug=slug, tool="browser",
        summary="painted", frames=["studio/proof/frame_000.png"],
    ))
    decision = mentor.steer(loop)
    assert decision is not None
    assert decision.action == "ship"


def test_mentor_http_toggle(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app
    from daedalus.paths import data_root

    monkeypatch.setenv("DAEDALUS_DATA", str(tmp_path))
    monkeypatch.delenv("DAEDALUS_MENTOR", raising=False)
    data_root.cache_clear()
    client = TestClient(app)
    got = client.get("/api/v1/daedalus/mentor")
    assert got.status_code == 200
    assert got.json()["enabled"] is True
    off = client.post("/api/v1/daedalus/mentor", json={"enabled": False})
    assert off.status_code == 200
    assert off.json()["enabled"] is False
    assert off.json()["ok"] is True
    on = client.post("/api/v1/daedalus/mentor", json={"enabled": True})
    assert on.json()["enabled"] is True

def test_mentor_missing_npm_returns_to_writer_install(tmp_path, monkeypatch):
    from daedalus import mentor

    monkeypatch.setenv("DAEDALUS_MENTOR", "1")
    monkeypatch.setattr("daedalus.mentor._rag_hits", lambda q: [])
    slug = "glass-orchard"
    root = tmp_path / slug
    (root / "src").mkdir(parents=True)
    (root / "index.html").write_text('<div id="game"></div>', encoding="utf-8")
    (root / "src" / "main.js").write_text(PHASER_BOOT, encoding="utf-8")
    (root / "package.json").write_text(
        '{"dependencies":{"phaser":"^3.80.1"},"devDependencies":{"vite":"^5.2.0"}}',
        encoding="utf-8",
    )
    monkeypatch.setattr("daedalus.playground.project_dir", lambda s: root)
    rt = route(LUMEN)
    loop = BuildLoop(rt, LUMEN, fix_rounds=3)
    loop.slug = slug
    loop.kind = "vite"
    loop.observe("playground_new", {"kind": "vite"}, f"── playground_new OK slug={slug} kind=vite ──", ok=True)
    loop.observe("write_file", {"slug": slug, "path": "src/main.js"}, "── write_file OK ──", ok=True)
    view = mentor.inspect_project(slug, kind="vite")
    assert view.needs_npm is True
    assert view.entry_boots is True
    decision = mentor.steer(loop)
    assert decision is not None
    assert decision.next_tool == "run_command"
    assert "npm install" in str(decision.next_args.get("command") or "")
    nxt = mentor.progress_next(loop)
    assert "RETURN TO WRITER" in nxt
    assert "run_command" in nxt


def test_mentor_mount_mismatch_returns_to_writer(tmp_path, monkeypatch):
    from daedalus import mentor

    monkeypatch.setenv("DAEDALUS_MENTOR", "1")
    monkeypatch.setattr("daedalus.mentor._rag_hits", lambda q: [])
    slug = "glass-orchard"
    root = tmp_path / slug
    (root / "src").mkdir(parents=True)
    (root / "node_modules" / "phaser").mkdir(parents=True)
    (root / "index.html").write_text(
        '<canvas id="game-canvas"></canvas><script type="module" src="/src/main.js"></script>',
        encoding="utf-8",
    )
    (root / "src" / "main.js").write_text(
        "import Phaser from 'phaser';\n"
        "new Phaser.Game({ type: Phaser.AUTO, parent: 'app', scene: [] });\n",
        encoding="utf-8",
    )
    (root / "package.json").write_text('{"dependencies":{"phaser":"^3.80"}}', encoding="utf-8")
    monkeypatch.setattr("daedalus.playground.project_dir", lambda s: root)
    rt = route(LUMEN)
    loop = BuildLoop(rt, LUMEN, fix_rounds=3)
    loop.slug = slug
    loop.kind = "vite"
    loop.observe("write_file", {"slug": slug, "path": "src/main.js"}, "── write_file OK ──", ok=True)
    view = mentor.inspect_project(slug, kind="vite")
    assert view.mount_mismatch is True
    assert view.phaser_parent == "app"
    decision = mentor.steer(loop)
    assert decision is not None
    assert decision.next_tool == "write_file"
    assert decision.next_args.get("path") == "index.html"
    assert "RETURN TO WRITER" in (decision.reason or "")


def test_mentor_failed_proof_returns_to_writer_not_browser(tmp_path, monkeypatch):
    from daedalus import mentor
    from daedalus.visual_proof import ProofResult

    monkeypatch.setenv("DAEDALUS_MENTOR", "1")
    monkeypatch.setattr("daedalus.mentor._rag_hits", lambda q: [])
    slug = "glass-orchard"
    root = tmp_path / slug
    (root / "src").mkdir(parents=True)
    (root / "node_modules" / "phaser").mkdir(parents=True)
    (root / "index.html").write_text('<div id="game"></div>', encoding="utf-8")
    (root / "src" / "main.js").write_text(PHASER_BOOT, encoding="utf-8")
    _with_boot_scene(root)
    (root / "package.json").write_text('{"dependencies":{"phaser":"^3.80"}}', encoding="utf-8")
    monkeypatch.setattr("daedalus.playground.project_dir", lambda s: root)
    rt = route(LUMEN)
    loop = BuildLoop(rt, LUMEN, fix_rounds=3)
    loop.slug = slug
    loop.kind = "vite"
    loop.observe("write_file", {"slug": slug, "path": "src/main.js"}, "── write_file OK ──", ok=True)
    loop.note_kernel_proof(ProofResult(
        ok=False, skipped=False, kind="vite", slug=slug, tool="browser",
        summary="1 uncaught", blank_screen=False,
        details={"page_errors": ["Failed to resolve 'phaser'"]},
    ))
    loop.observe("browser", {"slug": slug}, "── visual_proof FAILED ──", ok=False)
    decision = mentor.steer(loop)
    assert decision is not None
    assert decision.next_tool == "write_file"
    assert "RETURN TO WRITER" in (decision.reason or "")
    extra = mentor.extra_round_budget(loop)
    assert extra == 2
    nxt = mentor.progress_next(loop)
    assert "write_file" in nxt
    assert "list_dir" in nxt or "playground_read" in nxt


def test_mentor_compile_fail_proof_still_returns_to_writer(tmp_path, monkeypatch):
    from daedalus import mentor
    from daedalus.visual_proof import ProofResult

    monkeypatch.setenv("DAEDALUS_MENTOR", "1")
    monkeypatch.setattr("daedalus.mentor._rag_hits", lambda q: [])
    slug = "glass-orchard"
    root = tmp_path / slug
    (root / "src").mkdir(parents=True)
    (root / "node_modules" / "phaser").mkdir(parents=True)
    (root / "index.html").write_text('<div id="game"></div>', encoding="utf-8")
    (root / "src" / "main.js").write_text(PHASER_BOOT, encoding="utf-8")
    _with_boot_scene(root)
    (root / "package.json").write_text('{"dependencies":{"phaser":"^3.80"}}', encoding="utf-8")
    monkeypatch.setattr("daedalus.playground.project_dir", lambda s: root)
    rt = route(LUMEN)
    loop = BuildLoop(rt, LUMEN, fix_rounds=3)
    loop.slug = slug
    loop.kind = "vite"
    loop.observe("write_file", {"slug": slug, "path": "src/main.js"}, "── write_file OK ──", ok=True)
    loop.note_kernel_proof(ProofResult(
        ok=False, skipped=False, kind="vite", slug=slug, tool="browser",
        summary="Vite install/build failed", blank_screen=False,
        details={"page_errors": ["vite compile gate: Could not resolve ./scenes/Grove.js"]},
    ))
    decision = mentor.steer(loop)
    assert decision is not None
    assert decision.next_tool == "write_file"
    assert "RETURN TO WRITER" in (decision.reason or "")
    assert decision.next_tool != "browser"


def test_mentor_missing_scenes_returns_to_writer(tmp_path, monkeypatch):
    from daedalus import mentor

    monkeypatch.setenv("DAEDALUS_MENTOR", "1")
    monkeypatch.setattr("daedalus.mentor._rag_hits", lambda q: [])
    slug = "glass-orchard"
    root = tmp_path / slug
    (root / "src").mkdir(parents=True)
    (root / "node_modules" / "phaser").mkdir(parents=True)
    (root / "index.html").write_text('<div id="game"></div>', encoding="utf-8")
    (root / "src" / "main.js").write_text(PHASER_BOOT, encoding="utf-8")
    (root / "package.json").write_text('{"dependencies":{"phaser":"^3.80"}}', encoding="utf-8")
    monkeypatch.setattr("daedalus.playground.project_dir", lambda s: root)
    rt = route(LUMEN)
    loop = BuildLoop(rt, LUMEN, fix_rounds=3)
    loop.slug = slug
    loop.kind = "vite"
    loop.observe("write_file", {"slug": slug, "path": "src/main.js"}, "── write_file OK ──", ok=True)
    view = mentor.inspect_project(slug, kind="vite")
    assert view.missing_scenes is True
    assert view.scene_files == ()
    decision = mentor.steer(loop)
    assert decision is not None
    assert decision.next_tool == "write_file"
    assert decision.next_args.get("path") == "src/scenes/Boot.js"
    assert "RETURN TO WRITER" in (decision.reason or "")
    nxt = mentor.progress_next(loop)
    assert "Boot.js" in nxt
    assert "list_dir" in nxt or "playground_read" in nxt


def test_mentor_missing_import_returns_to_writer(tmp_path, monkeypatch):
    from daedalus import mentor

    monkeypatch.setenv("DAEDALUS_MENTOR", "1")
    monkeypatch.setattr("daedalus.mentor._rag_hits", lambda q: [])
    slug = "glass-orchard"
    root = tmp_path / slug
    (root / "src" / "scenes").mkdir(parents=True)
    (root / "node_modules" / "phaser").mkdir(parents=True)
    (root / "index.html").write_text('<div id="game"></div>', encoding="utf-8")
    (root / "src" / "main.js").write_text(
        "import Phaser from 'phaser';\n"
        "import BootScene from './scenes/BootScene.js';\n"
        "import PlayScene from './scenes/PlayScene.js';\n"
        "new Phaser.Game({ type: Phaser.AUTO, parent: 'game', scene: [BootScene, PlayScene] });\n",
        encoding="utf-8",
    )
    (root / "src" / "scenes" / "BootScene.js").write_text(
        "export default class BootScene extends Phaser.Scene { constructor(){ super('Boot'); } }\n",
        encoding="utf-8",
    )
    (root / "package.json").write_text('{"dependencies":{"phaser":"^3.80"}}', encoding="utf-8")
    monkeypatch.setattr("daedalus.playground.project_dir", lambda s: root)
    rt = route(LUMEN)
    loop = BuildLoop(rt, LUMEN, fix_rounds=3)
    loop.slug = slug
    loop.kind = "vite"
    loop.observe("write_file", {"slug": slug, "path": "src/main.js"}, "── write_file OK ──", ok=True)
    view = mentor.inspect_project(slug, kind="vite")
    assert "PlayScene.js" in " ".join(view.missing_imports)
    decision = mentor.steer(loop)
    assert decision is not None
    assert decision.next_tool == "write_file"
    assert "PlayScene.js" in str(decision.next_args.get("path") or "")
    assert "RETURN TO WRITER" in (decision.reason or "")


def test_mentor_phaser_without_import_returns_to_writer(tmp_path, monkeypatch):
    from daedalus import mentor

    monkeypatch.setenv("DAEDALUS_MENTOR", "1")
    monkeypatch.setattr("daedalus.mentor._rag_hits", lambda q: [])
    slug = "glass-orchard"
    root = tmp_path / slug
    (root / "src" / "scenes").mkdir(parents=True)
    (root / "node_modules" / "phaser").mkdir(parents=True)
    (root / "index.html").write_text('<div id="game"></div>', encoding="utf-8")
    (root / "src" / "main.js").write_text(PHASER_BOOT, encoding="utf-8")
    _with_boot_scene(root)
    (root / "src" / "scenes" / "Menu.js").write_text(
        "export default class Menu extends Phaser.Scene { constructor(){ super('Menu'); } }\n",
        encoding="utf-8",
    )
    (root / "package.json").write_text('{"dependencies":{"phaser":"^3.80"}}', encoding="utf-8")
    monkeypatch.setattr("daedalus.playground.project_dir", lambda s: root)
    rt = route(LUMEN)
    loop = BuildLoop(rt, LUMEN, fix_rounds=3)
    loop.slug = slug
    loop.kind = "vite"
    loop.observe("write_file", {"slug": slug, "path": "src/main.js"}, "── write_file OK ──", ok=True)
    view = mentor.inspect_project(slug, kind="vite")
    assert view.missing_imports == ()
    assert any(p.endswith("Menu.js") for p in view.phaser_no_import)
    decision = mentor.steer(loop)
    assert decision is not None
    assert decision.next_tool == "write_file"
    assert "Menu.js" in str(decision.next_args.get("path") or "")
    assert "import Phaser" in (decision.reason or "")


def test_mentor_phaser_auto_with_canvas_returns_to_writer(tmp_path, monkeypatch):
    from daedalus import mentor

    monkeypatch.setenv("DAEDALUS_MENTOR", "1")
    monkeypatch.setattr("daedalus.mentor._rag_hits", lambda q: [])
    slug = "glass-orchard"
    root = tmp_path / slug
    (root / "src" / "scenes").mkdir(parents=True)
    (root / "node_modules" / "phaser").mkdir(parents=True)
    (root / "index.html").write_text(
        '<div id="game"><canvas id="game-canvas"></canvas></div>',
        encoding="utf-8",
    )
    (root / "src" / "main.js").write_text(
        "import Phaser from 'phaser';\n"
        "import { Boot } from './scenes/Boot.js';\n"
        "new Phaser.Game({\n"
        "  type: Phaser.AUTO,\n"
        "  parent: 'game',\n"
        "  canvas: document.getElementById('game-canvas'),\n"
        "  scene: [Boot],\n"
        "});\n",
        encoding="utf-8",
    )
    _with_boot_scene(root)
    (root / "package.json").write_text('{"dependencies":{"phaser":"^3.80"}}', encoding="utf-8")
    monkeypatch.setattr("daedalus.playground.project_dir", lambda s: root)
    rt = route(LUMEN)
    loop = BuildLoop(rt, LUMEN, fix_rounds=3)
    loop.slug = slug
    loop.kind = "vite"
    loop.observe("write_file", {"slug": slug, "path": "src/main.js"}, "── write_file OK ──", ok=True)
    view = mentor.inspect_project(slug, kind="vite")
    assert view.phaser_auto_canvas is True
    decision = mentor.steer(loop)
    assert decision is not None
    assert decision.next_tool == "write_file"
    assert "main.js" in str(decision.next_args.get("path") or "")
    assert "WEBGL" in (decision.reason or "")
