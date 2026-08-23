"""Files that turn a playground project into a campaign, not a toy.

An epic game is a living folder: bible, map, people, levels, journal.
Autopilot fills these across sessions instead of dumping a 400-line index.html.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any


def paths(root: Path) -> dict[str, Path]:
    return {
        "bible": root / "world" / "bible.md",
        "map": root / "world" / "map.json",
        "factions": root / "world" / "factions.json",
        "journal": root / "world" / "journal.md",
        "characters": root / "characters" / "README.md",
        "levels": root / "levels" / "graph.json",
        "campaign": root / "CAMPAIGN.md",
    }


def needed(description: str = "", *, effort: str = "auto") -> bool:
    from .task_effort import classify
    dec = classify(description, requested=effort)
    return bool(dec.get("world_kit"))


def write(root: Path, *, name: str, slug: str, description: str = "") -> list[str]:
    """Create missing world-kit files. Never overwrite owner edits."""
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
        "## Regions\n\n1. —\n\n"
        "## Factions\n\n- —\n\n"
        "## Unanswered questions\n\n- —\n"
    )
    mapping = (
        '{"regions":[{"id":"hearth","name":"Огнище","exits":["wilds"]}],'
        '"start":"hearth","notes":"Fill exits as the campaign grows."}\n'
    )
    factions = '{"factions":[],"alliances":[],"conflicts":[]}\n'
    graph = '{"acts":[{"id":"act-1","title":"Първи праг","levels":["hearth"]}],"edges":[]}\n'
    characters = (
        "# Characters\n\n"
        "One file per person: `characters/{slug}.md`\n\n"
        "- Want, wound, tell, secret.\n"
        "- They must change a level, not only talk.\n"
    )
    journal = (
        f"# Campaign journal — {name}\n\n"
        "Each session: date, slice shipped, open threads, next slice.\n\n"
    )
    campaign = (
        f"# Campaign — {name}\n\n"
        "Long-horizon rules for Daedalus:\n\n"
        "1. One playable slice per session (a room, a quest beat, a boss).\n"
        "2. Update `world/bible.md` and `world/journal.md` before claiming done.\n"
        "3. New region = new files under `levels/` plus an edge in `levels/graph.json`.\n"
        "4. Never flatten the world into a single `index.html`.\n"
        "5. Owner says „продължи“ — resume from the journal, do not restart lore.\n"
    )
    contents: dict[str, str] = {
        "bible": bible,
        "map": mapping,
        "factions": factions,
        "journal": journal,
        "characters": characters,
        "levels": graph,
        "campaign": campaign,
    }
    for key, path in files.items():
        if path.exists():
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents[key], encoding="utf-8")
        written.append(str(path.relative_to(root)))
    return written


def status(root: Path) -> dict[str, Any]:
    files = paths(Path(root))
    present = {k: p.is_file() for k, p in files.items()}
    return {
        "ok": True,
        "ready": all(present.values()),
        "files": present,
        "missing": [k for k, ok in present.items() if not ok],
    }
