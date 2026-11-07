"""Files that turn a playground project into a campaign, not a toy.

An epic game is a living folder: bible, map, people, levels, journal —
plus a **runtime** (save schema, quests, dialogue, gated graph). Empty JSON
is not Play. Autopilot fills these across sessions instead of dumping a
400-line index.html.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def paths(root: Path) -> dict[str, Path]:
    return {
        "bible": root / "world" / "bible.md",
        "map": root / "world" / "map.json",
        "factions": root / "world" / "factions.json",
        "journal": root / "world" / "journal.md",
        "quests": root / "world" / "quests.json",
        "save_schema": root / "world" / "save.schema.json",
        "characters": root / "characters" / "README.md",
        "levels": root / "levels" / "graph.json",
        "campaign": root / "CAMPAIGN.md",
        "runtime_js": root / "src" / "world" / "runtime.js",
    }


def needed(description: str = "", *, effort: str = "auto") -> bool:
    from .task_effort import classify
    dec = classify(description, requested=effort)
    return bool(dec.get("world_kit"))


def _default_map() -> dict[str, Any]:
    return {
        "start": "hearth",
        "regions": [
            {"id": "hearth", "name": "Огнище", "exits": ["wilds"]},
            {
                "id": "wilds",
                "name": "Дивеч",
                "exits": ["hearth"],
                "require_flags": ["gate_open"],
            },
        ],
        "notes": "Wilds stay locked until quest open_gate rewards gate_open.",
    }


def _default_graph() -> dict[str, Any]:
    return {
        "start": "hearth",
        "acts": [{"id": "act-1", "title": "Първи праг", "levels": ["hearth", "wilds"]}],
        "edges": [
            {
                "from": "hearth",
                "to": "wilds",
                "require_flags": ["gate_open"],
                "autosave": True,
            },
            {"from": "wilds", "to": "hearth", "autosave": True},
        ],
    }


def _default_quests() -> dict[str, Any]:
    return {
        "quests": [
            {
                "id": "open_gate",
                "title": "Отвори портата",
                "giver": "keeper",
                "start_flags": [],
                "complete_when": {"flags": ["keeper_trust"]},
                "reward_flags": ["gate_open"],
            }
        ]
    }


def _default_dialogue() -> dict[str, Any]:
    return {
        "id": "keeper",
        "start": "hello",
        "nodes": [
            {
                "id": "hello",
                "speaker": "keeper",
                "line": "The wilds eat the unready.",
                "choices": [
                    {"text": "I will go anyway.", "next": "warn"},
                    {
                        "text": "Then teach me the gate.",
                        "next": "trust",
                        "set_flags": ["keeper_trust"],
                    },
                ],
            },
            {
                "id": "warn",
                "speaker": "keeper",
                "line": "Come back when you mean it.",
                "choices": [{"text": "Leave.", "next": None}],
            },
            {
                "id": "trust",
                "speaker": "keeper",
                "line": "The latch is yours.",
                "choices": [{"text": "Leave.", "next": None}],
            },
        ],
    }


def _dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2) + "\n"


def write(root: Path, *, name: str, slug: str, description: str = "", kind: str = "") -> list[str]:
    """Create missing world-kit files. Never overwrite owner edits."""
    from .world_runtime import RUNTIME_GD, RUNTIME_JS, SAVE_SCHEMA

    root = Path(root)
    files = paths(root)
    written: list[str] = []
    desc = (description or name).strip()
    bible = (
        f"# {name}\n\n"
        f"Slug: `{slug}`\n\n"
        f"## Premise\n\n{desc or '—'}\n\n"
        "## Tone\n\n(quiet / mythic / brutal — pick one and keep it)\n\n"
        "## Player fantasy\n\nWho are they, and what do they *do* every minute?\n\n"
        "## Regions\n\n1. Огнище (hearth) — 2. Дивеч (wilds, gated)\n\n"
        "## Factions\n\n- Keepers of the latch\n\n"
        "## Unanswered questions\n\n- —\n"
    )
    characters = (
        "# Characters\n\n"
        "One file per person: `characters/{slug}.md` plus "
        "`characters/{slug}.dialogue.json`.\n\n"
        "- Want, wound, tell, secret.\n"
        "- They must change a level, not only talk.\n"
        "- A choice that never sets a flag is flavour, not a verb.\n"
    )
    keeper_md = (
        "# Keeper\n\n"
        "- **Want:** keep the unready out of the wilds.\n"
        "- **Wound:** lost a ward beyond the gate.\n"
        "- **Tell:** watches the latch, not your eyes.\n"
        "- **Secret:** the latch opens for anyone who asks to learn.\n"
    )
    journal = (
        f"# Campaign journal — {name}\n\n"
        "Each session: date, slice shipped, open threads, next slice.\n\n"
        "- M0: hearth, keeper dialogue, gated wilds (`gate_open`).\n"
    )
    campaign = (
        f"# Campaign — {name}\n\n"
        "Long-horizon rules for Daedalus:\n\n"
        "1. One playable slice per session (a room, a quest beat, a boss).\n"
        "2. Update `world/bible.md` and `world/journal.md` before claiming done.\n"
        "3. New region = new files under `levels/` plus an edge in `levels/graph.json`.\n"
        "4. Never flatten the world into a single `index.html`.\n"
        "5. Owner says „продължи“ — resume from the journal, do not restart lore.\n"
        "6. `world({action: prove, slug})` must pass. Empty JSON is not a campaign.\n"
        "7. Save document: `{version, region, act, flags, inventory, quests}` "
        "in `src/world/runtime.js` (or Godot helper).\n"
    )
    factions = {
        "factions": [
            {"id": "keepers", "name": "Keepers", "want": "bar the unready", "flag": "keeper_trust"}
        ],
        "alliances": [],
        "conflicts": [],
    }
    contents: dict[str, str] = {
        "bible": bible,
        "map": _dumps(_default_map()),
        "factions": _dumps(factions),
        "journal": journal,
        "quests": _dumps(_default_quests()),
        "save_schema": _dumps(SAVE_SCHEMA),
        "characters": characters,
        "levels": _dumps(_default_graph()),
        "campaign": campaign,
        "runtime_js": RUNTIME_JS.lstrip("\n"),
    }
    extras: dict[str, str] = {
        "characters/keeper.md": keeper_md,
        "characters/keeper.dialogue.json": _dumps(_default_dialogue()),
    }
    if (kind or "").strip().lower() == "godot":
        extras["scripts/world_runtime.gd"] = RUNTIME_GD.lstrip("\n")

    for key, path in files.items():
        if path.exists():
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents[key], encoding="utf-8")
        written.append(str(path.relative_to(root)))
    for rel, text in extras.items():
        path = root / rel
        if path.exists():
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        written.append(rel)
    return written


def status(root: Path) -> dict[str, Any]:
    files = paths(Path(root))
    present = {k: p.is_file() for k, p in files.items()}
    proof: dict[str, Any] = {}
    runtime_ok = False
    if present.get("map"):
        from .world_runtime import prove_project
        proof = prove_project(root)
        runtime_ok = bool(proof.get("ok"))
    return {
        "ok": True,
        "ready": all(present.values()),
        "runtime": runtime_ok,
        "errors": list(proof.get("errors") or []),
        "files": present,
        "missing": [k for k, ok in present.items() if not ok],
        "regions": proof.get("regions") or [],
        "dialogues": proof.get("dialogues") or [],
    }
