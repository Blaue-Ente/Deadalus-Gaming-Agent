"""Playable flags — stub, canvas trap, WASD, ship gate."""
from __future__ import annotations

from daedalus.playable import inspect_tree, may_ship


def test_canvas_game_webgl_is_a_trap(tmp_path):
    (tmp_path / "index.html").write_text(
        '<canvas id="game"></canvas><script type="module" src="/src/main.js"></script>',
        encoding="utf-8",
    )
    src = tmp_path / "src"
    src.mkdir()
    (src / "main.js").write_text(
        "import Phaser from 'phaser';\n"
        "new Phaser.Game({ type: Phaser.WEBGL, parent: 'game' });\n",
        encoding="utf-8",
    )
    flags = inspect_tree(tmp_path, kind="vite")
    assert flags["canvas_game_trap"] is True
    assert flags["source_playable"] is False


def test_canvas_parent_traps_auto_too(tmp_path):
    (tmp_path / "index.html").write_text(
        '<canvas id="game"></canvas><script type="module" src="/src/main.js"></script>',
        encoding="utf-8",
    )
    src = tmp_path / "src"
    src.mkdir()
    (src / "main.js").write_text(
        "import Phaser from 'phaser';\n"
        "new Phaser.Game({ type: Phaser.AUTO, parent: 'game' });\n",
        encoding="utf-8",
    )
    flags = inspect_tree(tmp_path, kind="vite")
    assert flags["canvas_game_trap"] is True
    assert flags["source_playable"] is False
    (tmp_path / "index.html").write_text('<div id="game"></div>', encoding="utf-8")
    scenes = tmp_path / "src" / "scenes"
    scenes.mkdir(parents=True)
    (tmp_path / "src" / "main.js").write_text(
        "import Phaser from 'phaser';\n"
        "import { Play } from './scenes/Play.js';\n"
        "new Phaser.Game({ type: Phaser.WEBGL, parent: 'game', scene: [Play] });\n",
        encoding="utf-8",
    )
    (scenes / "Play.js").write_text(
        "import Phaser from 'phaser';\n"
        "export class Play extends Phaser.Scene {\n"
        "  constructor() { super('Play'); }\n"
        "  create() { this.add.text(24, 24, 'Play — fill this scene'); }\n"
        "}\n",
        encoding="utf-8",
    )
    flags = inspect_tree(tmp_path, kind="vite")
    assert flags["is_game"] is True
    assert flags["play_stub"] is True
    assert flags["source_playable"] is False


def test_wasd_play_is_source_playable(tmp_path):
    (tmp_path / "index.html").write_text('<div id="game"></div>', encoding="utf-8")
    scenes = tmp_path / "src" / "scenes"
    scenes.mkdir(parents=True)
    (tmp_path / "src" / "main.js").write_text(
        "import Phaser from 'phaser';\n"
        "import { Play } from './scenes/Play.js';\n"
        "new Phaser.Game({ type: Phaser.WEBGL, parent: 'game', scene: [Play] });\n",
        encoding="utf-8",
    )
    (scenes / "Play.js").write_text(
        "import Phaser from 'phaser';\n"
        "export class Play extends Phaser.Scene {\n"
        "  constructor() { super('Play'); }\n"
        "  create() {\n"
        "    this.player = this.physics.add.sprite(80, 80, 'walker');\n"
        "    this.cursors = this.input.keyboard.addKeys('W,A,S,D');\n"
        "  }\n"
        "}\n",
        encoding="utf-8",
    )
    flags = inspect_tree(tmp_path, kind="vite")
    assert flags["play_stub"] is False
    assert flags["play_incomplete"] is False
    assert flags["source_playable"] is True
    assert flags["has_input"] is True


class _Loop:
    play_bot_ok = False
    play_bot_gap = ""


def test_may_ship_needs_computer_for_games():
    loop = _Loop()
    assert may_ship(source_playable=True, is_game=True, loop=loop, proof_ok=True) is False
    loop.play_bot_ok = True
    assert may_ship(source_playable=True, is_game=True, loop=loop, proof_ok=True) is True
    assert may_ship(source_playable=True, is_game=False, loop=_Loop(), proof_ok=True) is True
