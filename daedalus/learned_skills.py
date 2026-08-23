"""Closed learning loop — skills born from failures and non-trivial successes.

Compatible with a slim agentskills.io shape (YAML frontmatter + markdown).
Harness code patches stay owner-gated in ``self_improve``; these skills are
procedural memory the agent may load on the next similar task.
"""
from __future__ import annotations

import logging
import re
import time
from pathlib import Path
from typing import Any

from .paths import data_root, package_root

log = logging.getLogger(__name__)

MAX_INJECT = 3
MAX_SKILL_CHARS = 1600


def learned_dir() -> Path:
    p = data_root() / "learned_skills"
    p.mkdir(parents=True, exist_ok=True)
    return p


def builtin_dir() -> Path:
    return package_root() / "skills"


def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return (s[:48] or "skill")


def _parse_frontmatter(raw: str) -> tuple[dict[str, str], str]:
    if not raw.startswith("---"):
        return {}, raw
    end = raw.find("\n---", 3)
    if end < 0:
        return {}, raw
    meta: dict[str, str] = {}
    for line in raw[3:end].splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            meta[k.strip()] = v.strip().strip('"')
    body = raw[end + 4:].lstrip("\n")
    return meta, body


def list_skills() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for root, origin in ((builtin_dir(), "builtin"), (learned_dir(), "learned")):
        if not root.is_dir():
            continue
        for path in sorted(root.glob("*.md")):
            try:
                raw = path.read_text(encoding="utf-8")
            except Exception:
                continue
            meta, body = _parse_frontmatter(raw)
            rows.append({
                "id": path.stem,
                "name": meta.get("name") or path.stem,
                "description": meta.get("description") or body.splitlines()[0][:180] if body else "",
                "origin": origin,
                "path": str(path),
                "updated_at": path.stat().st_mtime,
            })
    return rows


def load_skill(skill_id: str) -> dict[str, Any] | None:
    for root in (learned_dir(), builtin_dir()):
        path = root / f"{skill_id}.md"
        if path.is_file():
            raw = path.read_text(encoding="utf-8")
            meta, body = _parse_frontmatter(raw)
            return {"id": skill_id, "meta": meta, "body": body, "raw": raw, "path": str(path)}
    return None


def write_skill(*, name: str, description: str, body: str, origin: str = "learned") -> Path:
    slug = _slug(name)
    target = learned_dir() / f"{slug}.md"
    n = 2
    while target.exists() and origin == "learned":
        target = learned_dir() / f"{slug}-{n}.md"
        n += 1
        if n > 20:
            break
    text = (
        f"---\nname: {slug}\ndescription: {description[:240]}\n"
        f"origin: {origin}\nupdated: {int(time.time())}\n---\n\n{body.strip()}\n"
    )
    target.write_text(text, encoding="utf-8")
    log.info("learned skill written: %s", target.name)
    return target


def draft_from_failure(
    *,
    kind: str,
    errors: list[str],
    context: str = "",
) -> Path | None:
    errs = [e.strip() for e in (errors or []) if e and e.strip()]
    if not errs:
        return None
    signature = errs[0][:80]
    name = f"repair-{kind}-{_slug(signature)[:24]}"
    body = (
        f"## When\nPlayground/autopilot `{kind}` failed with:\n"
        + "\n".join(f"- {e[:240]}" for e in errs[:6])
        + "\n\n## Do this next time\n"
        "- Read the exact error; fix the cited file only.\n"
        "- Re-run smoke (and browser verify for visual kinds) before claiming done.\n"
        "- Do not emit 'готово' while tests fail.\n"
    )
    if context:
        body += f"\n## Context\n{context[:800]}\n"
    return write_skill(
        name=name,
        description=f"Repair pattern for {kind}: {signature}",
        body=body,
        origin="failure",
    )


def draft_from_success(*, kind: str, summary: str) -> Path | None:
    if not (summary or "").strip() or not kind:
        return None
    return write_skill(
        name=f"win-{kind}",
        description=f"What worked for a {kind} build",
        body=(
            f"## When\nBuilding a `{kind}` project.\n\n"
            f"## What worked\n{summary.strip()[:1200]}\n"
        ),
        origin="success",
    )


def format_block(query: str, *, limit: int = MAX_INJECT) -> str:
    q = (query or "").lower()
    scored: list[tuple[int, dict[str, Any]]] = []
    for row in list_skills():
        blob = f"{row.get('name','')} {row.get('description','')}".lower()
        score = sum(1 for tok in re.findall(r"[a-zа-я0-9]{3,}", q) if tok in blob)
        if row.get("origin") == "learned":
            score += 1
        if score:
            scored.append((score, row))
    scored.sort(key=lambda x: x[0], reverse=True)
    picks = [r for _, r in scored[:limit]]
    if not picks:
        # still surface the latest learned skill so the loop is visible
        learned = [r for r in list_skills() if r.get("origin") == "learned"]
        picks = learned[:1]
    if not picks:
        return ""
    lines = ["━━━ SKILLS (load only what this task needs) ━━━"]
    for row in picks:
        loaded = load_skill(row["id"])
        if not loaded:
            continue
        body = loaded["body"][:MAX_SKILL_CHARS]
        lines.append(f"### {loaded['meta'].get('name') or row['id']}")
        lines.append(body)
    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    return "\n".join(lines)
