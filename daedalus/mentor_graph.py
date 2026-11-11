"""Compact tool graph for Mentor — nodes, NEXT edges, checks, recovery.

The director LLM sees this map, not the full playbook YAML. Kernel ``steer()``
already encodes the same recoveries; the graph is the shared contract so a
strong model cannot invent tools or reopen Chromium after a failed proof.

Correction proposals (``record_outcome``) reuse ``recovery`` so the owner sees
the same NEXT the writer should take. Patches still wait for Approve.
"""
from __future__ import annotations

from typing import Any

from .task_playbooks import get as get_playbook

# Proof tools that must not be retried after a failed Chromium/Godot pass.
PROOF_REOPEN = frozenset({"browser", "engine", "visual_proof"})

_CORE_NEXT = frozenset({
    "write_file", "run_command", "browser", "generate_image",
    "playground_new", "skill", "read_file",
})

_STANDARDS: dict[str, tuple[str, ...]] = {
    "playground.vite": (
        "type: Phaser.WEBGL — never AUTO with a pre-created canvas",
        "every Phaser.Scene file: import Phaser from 'phaser'",
        "parent id must exist in index.html",
        "generate_image filename has no assets/ prefix",
        "npm install then npm run build before browser",
        "HUD lives on the canvas; DOM text is not proof",
        "painted Menu is not playable — Play.js must move, then the computer plays it",
    ),
    "playground.math3d": (
        "Call math3d with claimed numbers before writing shaders",
        "WebGL 1: uniformMatrix*fv transpose must be false",
        "Static preview — no Vite dev server",
    ),
    "playground.arcade": (
        "index.html has a canvas and a real rAF / Phaser loop",
        "Kernel Chromium proof after the last write",
    ),
    "playground.godot": (
        "kind=godot never falls to Vite/HTML",
        "Missing Godot 4.3 is FAIL, not skip",
    ),
    "playground.crowd": (
        "Call spatial before writing many-body JS",
        "Kernel Chromium proof after the last write",
    ),
    "harness.fix": (
        "NEED_PROPOSE / Approve — never write_file into daedalus/",
        "Self-improve patches wait for owner Approve",
    ),
}

_FLOW: dict[str, str] = {
    "playground.vite": (
        "playground_new → generate_image? → write_file → "
        "run_command(npm/build) → browser → computer playtest → ship"
    ),
    "playground.math3d": "math3d → playground_new → write_file → browser → ship",
    "playground.arcade": "playground_new → write_file → browser → ship",
    "playground.godot": "playground_new → write_file → engine → ship",
    "playground.crowd": "spatial → playground_new → write_file → browser → ship",
    "harness.fix": "read_file → propose_patch (Approve) — never silent harness edit",
}

_EDGES: dict[str, tuple[str, ...]] = {
    "playground.vite": (
        "playground_new → write_file",
        "write_file → run_command (npm/build)",
        "run_command → browser",
        "browser fail → write_file (never browser again)",
        "browser ok → computer playtest (scenario.json) → ship",
        "Menu paint / Play.js stub → write_file Play.js, never ship",
        "generate_image ×2 fail → write_file + forbid generate_image",
    ),
    "playground.math3d": (
        "math3d → write_file",
        "write_file → browser",
        "browser fail → write_file",
        "browser ok → ship",
    ),
}

# First matching inspect flag wins. Kernel steer() follows the same order.
_RECOVERY: tuple[tuple[str, str, tuple[str, ...], str], ...] = (
    (
        "proof_failed", "write_file",
        ("browser", "engine", "visual_proof"),
        "Chromium failed — fix the cited file, do not reopen the page",
    ),
    (
        "phaser_auto_canvas", "write_file",
        ("browser",),
        "set type: Phaser.WEBGL (AUTO + canvas throws in Vite ESM)",
    ),
    (
        "phaser_no_import", "write_file",
        ("browser",),
        "import Phaser from 'phaser' in every scene file",
    ),
    (
        "missing_imports", "write_file",
        ("browser",),
        "add the missing from './scenes/…' import in main.js",
    ),
    (
        "missing_scenes", "write_file",
        ("browser",),
        "write the missing scene file named by inspect",
    ),
    (
        "mount_mismatch", "write_file",
        ("browser",),
        "Phaser parent id must exist in index.html",
    ),
    (
        "needs_npm", "run_command",
        ("browser",),
        "npm install (no pipes / &&)",
    ),
    (
        "scaffold_vite_app_div", "write_file",
        ("browser", "generate_image"),
        "overwrite the Vite #app scaffold with Phaser.Game",
    ),
    (
        "image_retry_capped", "write_file",
        ("generate_image",),
        "Boot canvas textures; do not retry generate_image",
    ),
    (
        "proof_blank", "write_file",
        ("browser",),
        "blank frame — fix the boot, not another browser",
    ),
)


def pick_recovery(flags: dict[str, Any] | None) -> dict[str, Any] | None:
    """Return the first recovery whose inspect flag is true."""
    from .protocols import as_recovery
    rec = as_recovery(flags)
    if rec:
        return rec
    flags = flags or {}
    for flag, nxt, forbid, hint in _RECOVERY:
        if flags.get(flag):
            return {
                "if": flag,
                "next": nxt,
                "forbid": list(forbid),
                "hint": hint,
            }
    return None


def for_inspect(playbook_id: str, *, flags: dict[str, Any] | None = None) -> dict[str, Any]:
    """Tiny JSON graph for the director digest (~few hundred tokens)."""
    pid = (playbook_id or "").strip()
    pb = get_playbook(pid)
    tools = list(pb.tools) if pb is not None else list(_CORE_NEXT)
    # Always keep writer recoveries even if a playbook omitted them.
    for name in ("write_file", "run_command", "browser"):
        if name not in tools:
            tools.append(name)
    std = _STANDARDS.get(pid)
    if std is None:
        bits = (pb.notes.split(". ") if pb is not None and pb.notes else [])
        std = tuple(s.strip() for s in bits if s.strip())[:3]
    edges = _EDGES.get(pid) or (
        "playground_new → write_file",
        "write_file → browser",
        "browser fail → write_file",
        "browser ok → ship",
    )
    out: dict[str, Any] = {
        "playbook": pid or "unknown",
        "tools": tools[:18],
        "flow": _FLOW.get(pid) or (pb.notes[:160] if pb is not None and pb.notes else ""),
        "edges": list(edges),
        "standards": list(std),
        "checks": [
            "failed check → writer (write_file / npm), never another browser",
            "Mentor does not write game files",
            "harness patches wait for Approve",
            "Mentor evaluates playable yes/no — does not assign workers",
        ],
    }
    rec = pick_recovery(flags)
    if rec:
        out["recovery"] = rec
    return out


def guard_steer(
    parsed: dict[str, Any],
    inspect: dict[str, Any],
    rules_steer: dict[str, Any],
) -> dict[str, Any]:
    """Clamp a director (or rules) steer to the graph. Mutates and returns parsed."""
    graph = inspect.get("graph") if isinstance(inspect.get("graph"), dict) else {}
    flags = inspect.get("flags") if isinstance(inspect.get("flags"), dict) else {}
    allowed = set(graph.get("tools") or ()) | set(_CORE_NEXT)
    rec = graph.get("recovery") if isinstance(graph.get("recovery"), dict) else {}
    action = str(parsed.get("action") or "continue")
    nxt = str(parsed.get("next_tool") or "")

    def apply_recovery() -> None:
        parsed["next_tool"] = rec.get("next") or rules_steer.get("next_tool") or "write_file"
        parsed["next_args"] = dict(rules_steer.get("next_args") or parsed.get("next_args") or {})
        parsed["action"] = "call_tool"
        parsed["forbid_tools"] = list(dict.fromkeys(
            list(parsed.get("forbid_tools") or []) + list(rec.get("forbid") or [])
        ))
        parsed["reason"] = str(rec.get("hint") or parsed.get("reason") or rules_steer.get("reason") or "")[:140]

    def take_rules() -> None:
        parsed["next_tool"] = rules_steer.get("next_tool")
        parsed["next_args"] = dict(rules_steer.get("next_args") or {})
        parsed["action"] = rules_steer.get("action") or "continue"
        parsed["reason"] = str(parsed.get("reason") or rules_steer.get("reason") or "")[:140]

    if nxt == "playground_new" and (inspect.get("project_exists") or inspect.get("slug")):
        # The project already exists (project chat, or created earlier this turn): never start a second one.
        parsed["next_tool"] = "write_file"
        parsed["next_args"] = {"slug": inspect.get("slug")} if inspect.get("slug") else {}
        parsed["action"] = "call_tool"
        parsed["forbid_tools"] = list(dict.fromkeys(list(parsed.get("forbid_tools") or []) + ["playground_new"]))
        parsed["reason"] = "Project exists; write into it."
        return parsed

    if nxt in PROOF_REOPEN and inspect.get("wrote_source") is False:
        # Nothing written this turn yet: there is no new game to open (found live 2026-11-11: "browser" before any
        # write_file, after a reply was cut off at the output limit).
        parsed["next_tool"] = "write_file"
        parsed["next_args"] = {"slug": inspect.get("slug")} if inspect.get("slug") else {}
        parsed["action"] = "call_tool"
        parsed["reason"] = "Nothing written yet; write the game first."
        return parsed

    if flags.get("proof_failed") and nxt in PROOF_REOPEN:
        if rec.get("next"):
            apply_recovery()
        else:
            take_rules()
        return parsed

    if rec.get("next") and nxt in set(rec.get("forbid") or ()):
        apply_recovery()
        return parsed

    if action not in {"stop", "ship"} and nxt and nxt not in allowed:
        take_rules()
        return parsed

    is_game = flags.get("is_game")
    if is_game is None:
        kind = str(inspect.get("kind") or "")
        is_game = kind in {"vite", "vite-react", "phaser", "godot", "unity"}

    if action == "ship" and (
        flags.get("play_stub")
        or flags.get("play_incomplete")
        or flags.get("canvas_game_trap")
        or flags.get("play_bot_failed")
        or (is_game and not flags.get("play_bot_ok"))
    ):
        if rec.get("next"):
            apply_recovery()
        else:
            parsed["action"] = "call_tool"
            parsed["next_tool"] = rules_steer.get("next_tool") or "write_file"
            parsed["next_args"] = dict(
                rules_steer.get("next_args") or parsed.get("next_args") or {}
            )
            parsed["reason"] = (
                "Not playable yet — Menu paint is not the computer playing it."
            )
        return parsed

    painted = (
        bool(flags.get("kernel_proof_ran"))
        and not bool(flags.get("proof_failed"))
        and bool(inspect.get("loop_ok"))
        and not rec
    )
    playable = bool(flags.get("play_bot_ok")) or not is_game
    if painted and playable and action in {"call_tool", "continue"}:
        parsed["action"] = "ship"
        parsed["next_tool"] = None
        parsed["next_args"] = {}
        parsed["reason"] = "The computer played the slice — ship it."
        return parsed

    return parsed
