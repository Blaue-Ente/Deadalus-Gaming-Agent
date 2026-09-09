"""Ready-solution book — every known fail has a next move.

Live Ember Walk (North Mini, 2026-09): Off painted black because
``<canvas id="game">`` + ``Phaser.WEBGL``; Kernel painted Menu; Mentor GLM Flash
shipped that Menu while ``Play.js`` stayed ``fill this scene``.

Kernel + Mentor consult this registry instead of stalling. Mentor evaluates
and returns to the writer/architect; it does not invent an org chart.
"""
from __future__ import annotations

from typing import Any, NamedTuple


class Protocol(NamedTuple):
    id: str
    flag: str
    next_tool: str
    forbid: tuple[str, ...]
    path: str
    return_to: str
    hint: str
    do: str


# First matching flag wins. Keep hints ≤140 for Mentor JSON.
REGISTRY: tuple[Protocol, ...] = (
    Protocol(
        id="chromium-failed",
        flag="proof_failed",
        next_tool="write_file",
        forbid=("browser", "engine", "visual_proof"),
        path="src/main.js",
        return_to="writer",
        hint="Chromium failed — fix the cited file, do not reopen the page",
        do=(
            "RETURN TO WRITER. Read the proof error. Fix that file "
            "(WEBGL type, import Phaser, mount id). Do not call browser again."
        ),
    ),
    Protocol(
        id="canvas-game-webgl-black",
        flag="canvas_game_trap",
        next_tool="write_file",
        forbid=("browser",),
        path="index.html",
        return_to="writer",
        hint="Replace <canvas id=game> with <div id=game> — WEBGL paints black on a canvas parent",
        do=(
            "REWRITE index.html: body holds <div id=\"game\"></div>, never "
            "<canvas id=\"game\">. Phaser.WEBGL creates its own canvas. Then fill Play.js."
        ),
    ),
    Protocol(
        id="vite-hello-blank",
        flag="scaffold_vite_app_div",
        next_tool="write_file",
        forbid=("browser", "generate_image"),
        path="src/main.js",
        return_to="writer",
        hint="Overwrite the Vite #app scaffold with Phaser.Game (WEBGL, parent game)",
        do=(
            "OVERWRITE src/main.js with new Phaser.Game, type Phaser.WEBGL, "
            "parent 'game'. Do not keep the Hello Vite #app counter."
        ),
    ),
    Protocol(
        id="needs-npm",
        flag="needs_npm",
        next_tool="run_command",
        forbid=("browser",),
        path="",
        return_to="writer",
        hint="npm install (no pipes / &&)",
        do='run_command command="npm install" — no pipes, no &&. Then npm run build.',
    ),
    Protocol(
        id="mount-mismatch",
        flag="mount_mismatch",
        next_tool="write_file",
        forbid=("browser",),
        path="index.html",
        return_to="writer",
        hint="Phaser parent id must exist in index.html as a div, not a leftover canvas",
        do="Add <div id=\"{parent}\"></div> matching Phaser parent. Prefer id=game.",
    ),
    Protocol(
        id="missing-scenes",
        flag="missing_scenes",
        next_tool="write_file",
        forbid=("browser",),
        path="src/scenes/Boot.js",
        return_to="writer",
        hint="write the missing scene file named by inspect",
        do="write_file src/scenes/Boot.js then Menu.js and Play.js. Import Phaser in each.",
    ),
    Protocol(
        id="missing-import-file",
        flag="missing_imports",
        next_tool="write_file",
        forbid=("browser",),
        path="",
        return_to="writer",
        hint="write the missing file named by inspect — it is imported but not on disk",
        do="write_file the first missing import path. Mentor does not write.",
    ),
    Protocol(
        id="phaser-no-import",
        flag="phaser_no_import",
        next_tool="write_file",
        forbid=("browser",),
        path="src/scenes/Boot.js",
        return_to="writer",
        hint="import Phaser from 'phaser' in every scene file",
        do="Add `import Phaser from 'phaser'` at the top of each scene that uses Phaser.",
    ),
    Protocol(
        id="phaser-auto-canvas",
        flag="phaser_auto_canvas",
        next_tool="write_file",
        forbid=("browser",),
        path="src/main.js",
        return_to="writer",
        hint="set type: Phaser.WEBGL (AUTO + canvas throws in Vite ESM)",
        do="In src/main.js set type: Phaser.WEBGL (or CANVAS). Remove config.canvas.",
    ),
    Protocol(
        id="play-js-stub",
        flag="play_stub",
        next_tool="write_file",
        forbid=("browser",),
        path="src/scenes/Play.js",
        return_to="writer",
        hint="Play.js is still a stub — Menu paint is not a playable game, do not ship",
        do=(
            "REWRITE src/scenes/Play.js: WASD/cursors, a player body, pickups, HUD. "
            "Do not leave “fill this scene”. Do not ship on Menu proof."
        ),
    ),
    Protocol(
        id="play-js-no-input",
        flag="play_incomplete",
        next_tool="write_file",
        forbid=("browser",),
        path="src/scenes/Play.js",
        return_to="writer",
        hint="Play scene has no input — write WASD/cursors + a body, then the computer plays it",
        do=(
            "REWRITE src/scenes/Play.js so a player moves (WASD or cursors) and "
            "the world changes. Kernel will run playtest/scenario.json after that."
        ),
    ),
    Protocol(
        id="scenario-missing",
        flag="scenario_missing",
        next_tool="write_file",
        forbid=("browser",),
        path="playtest/scenario.json",
        return_to="writer",
        hint="Write playtest/scenario.json — Enter then WASD must change the canvas",
        do=(
            "WRITE playtest/scenario.json: wait, hold Enter, hold WASD, "
            "expect canvas_changed. Kernel computer uses this as the playable gate."
        ),
    ),
    Protocol(
        id="computer-play-failed",
        flag="play_bot_failed",
        next_tool="write_file",
        forbid=("browser",),
        path="src/scenes/Play.js",
        return_to="writer",
        hint="Computer played it — canvas did not change. Fix Play.js input/bodies, do not ship",
        do=(
            "The virtual computer ran playtest/scenario.json and the canvas stayed still "
            "(or win/lose never fired). RETURN TO WRITER: Play.js must move a body on WASD."
        ),
    ),
    Protocol(
        id="blank-frame",
        flag="proof_blank",
        next_tool="write_file",
        forbid=("browser",),
        path="src/main.js",
        return_to="writer",
        hint="blank frame — fix the boot, not another browser",
        do="Fix the Phaser/rAF boot and the mount. Do not reopen Chromium until the files change.",
    ),
    Protocol(
        id="image-retry-capped",
        flag="image_retry_capped",
        next_tool="write_file",
        forbid=("generate_image",),
        path="src/scenes/Boot.js",
        return_to="writer",
        hint="Boot canvas textures; do not retry generate_image",
        do="Draw Boot/Play textures with graphics.generateTexture. Stop calling generate_image.",
    ),
)


def match(flags: dict[str, Any] | None) -> Protocol | None:
    """First protocol whose inspect flag is true — always a next move."""
    flags = flags or {}
    for row in REGISTRY:
        val = flags.get(row.flag)
        if val:
            return row
    return None


def as_recovery(flags: dict[str, Any] | None) -> dict[str, Any] | None:
    row = match(flags)
    if row is None:
        return None
    return {
        "if": row.flag,
        "next": row.next_tool,
        "forbid": list(row.forbid),
        "hint": row.hint,
        "path": row.path,
        "return_to": row.return_to,
        "protocol": row.id,
        "do": row.do,
    }


def catalog() -> list[dict[str, Any]]:
    return [
        {
            "id": p.id,
            "flag": p.flag,
            "next_tool": p.next_tool,
            "path": p.path,
            "return_to": p.return_to,
            "hint": p.hint,
        }
        for p in REGISTRY
    ]
