"""Upgrade Pool Scanner — Дедал оценява нови upgrades от pool.json.

Workflow:
1. Чете .agents/upgrade-pool/pool.json
2. Филтрира candidate items за argos-data-nexus / all-services
3. Пуска background LLM задача — Дедал оценява всеки item:
   → adopt (proposal → outbox) | defer | skip
4. Резултатите отиват в Outbox + update на pool.json статус

Вика се от /admin/daedalus/upgrade-pool-scan (POST)
или директно от Дедал чрез NEED_UPGRADE_POOL_SCAN маркер.
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

from .paths import product_root, repo_root

log = logging.getLogger(__name__)

_ADN_TARGETS = {"argos-data-nexus", "all-services", "argos_data_nexus"}
_STANDALONE_TARGETS = {"daedalus", "all-services", "argos-data-nexus", "argos_data_nexus"}


def _pool_path() -> Path:
    from .host import standalone_mode
    if standalone_mode():
        return product_root() / ".agents" / "upgrade-pool" / "pool.json"
    return repo_root() / ".agents" / "upgrade-pool" / "pool.json"


def load_pool() -> dict:
    path = _pool_path()
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        log.warning("upgrade pool not found: %s", exc)
        return {"version": 1, "updated": "—", "items": []}


def save_pool(pool: dict) -> None:
    path = _pool_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(pool, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception as exc:
        log.warning("upgrade pool save failed: %s", exc)


def get_adn_candidates(pool: dict | None = None) -> list[dict]:
    """Return candidate items targeting this product (not yet adopted/rejected)."""
    from .host import standalone_mode
    if pool is None:
        pool = load_pool()
    wanted = _STANDALONE_TARGETS if standalone_mode() else _ADN_TARGETS
    items = pool.get("items", [])
    result = []
    for item in items:
        if item.get("status") not in ("candidate", "evaluating"):
            continue
        targets = set(item.get("target_projects") or [])
        if targets & wanted:
            result.append(item)
    return result


def _format_item_brief(item: dict) -> str:
    lines = [
        f"**ID:** {item.get('id', '?')}",
        f"**Заглавие:** {item.get('title', '?')}",
        f"**Приоритет:** {item.get('priority', '?')} | **Риск:** {item.get('risk_level', '?')} | **Усилие:** {item.get('effort_estimate', '?')}",
        f"**Ползи:** {item.get('benefit', '?')}",
        f"**Кога:** {item.get('when_to_apply', '?')}",
    ]
    if item.get("key_concepts"):
        lines.append(f"**Концепти:** {', '.join(item['key_concepts'][:5])}")
    return "\n".join(lines)


def update_item_status(item_id: str, new_status: str, notes: str = "") -> bool:
    """Update status of a pool item. Returns True if found."""
    pool = load_pool()
    for item in pool.get("items", []):
        if item.get("id") == item_id:
            item["status"] = new_status
            if notes:
                item["daedalus_notes"] = notes
            item["daedalus_evaluated_at"] = time.strftime("%Y-%m-%d")
            save_pool(pool)
            return True
    return False


# ---------------------------------------------------------------------------
# Main scan function (runs in background asyncio task)
# ---------------------------------------------------------------------------

async def run_upgrade_pool_scan(agent: Any, session_id: str = "bg-upgrade-scan") -> dict:
    """Дедал оценява ADN-релевантните upgrades и прави proposals за подходящите."""
    from .outbox import push_outbox

    candidates = get_adn_candidates()
    if not candidates:
        push_outbox(
            type="message",
            title="📦 Upgrade Pool Scan: няма нови кандидати",
            body="Всички ADN-релевантни upgrades са вече оценени (adopted/rejected/evaluating).",
            category="upgrade_pool",
            dedup_key="upgrade_pool_empty_scan",
        )
        return {"ok": True, "candidates": 0}

    items_brief = "\n\n---\n\n".join(_format_item_brief(c) for c in candidates[:8])

    prompt = f"""Ти си Дедал — архитект на ArgosDataNexus (ADN).

Upgrade Pool има {len(candidates)} нови кандидати за оценка. Разгледай ВСЕКИ и реши:

**ADOPT** — трябва да го реализираме скоро, конкретен следващ ход
**EVALUATING** — интересно, искаме повече информация преди решение
**DEFER** — добра идея но не сега (посочи кога)
**SKIP** — нерелевантно за ADN

=== КАНДИДАТИ ===

{items_brief}

=== ФОРМАТ НА ОТГОВОРА ===

За всеки item пиши:
```
ITEM: [id]
РЕШЕНИЕ: [ADOPT|EVALUATING|DEFER|SKIP]
ПРИЧИНА: [1-2 изречения — конкретно защо]
СЛЕДВАЩА СТЪПКА: [ако ADOPT — какво точно трябва да направим първо]
```

Бъди директен. Мисли като инженер с ограничен bandwidth — не всичко може да се прави едновременно."""

    try:
        result = await agent.chat(prompt, session_id=session_id)
        raw_reply = result.get("reply", "")

        # Parse decisions from reply
        decisions = _parse_decisions(raw_reply, candidates)

        # Update pool.json + push outbox proposals
        adopted = []
        evaluating = []
        skipped = []

        for item_id, decision, reason, next_step in decisions:
            status_map = {
                "ADOPT": "evaluating",
                "EVALUATING": "evaluating",
                "DEFER": "candidate",
                "SKIP": "rejected",
            }
            new_status = status_map.get(decision, "candidate")
            notes = f"{reason} | Next: {next_step}" if next_step else reason
            update_item_status(item_id, new_status, notes)

            if decision == "ADOPT":
                adopted.append((item_id, reason, next_step))
            elif decision == "EVALUATING":
                evaluating.append((item_id, reason))
            elif decision == "SKIP":
                skipped.append(item_id)

        # Build outbox message
        body_parts = [f"Оценени: **{len(decisions)}/{len(candidates)}** кандидата\n"]

        if adopted:
            body_parts.append("### ✅ ADOPT")
            for item_id, reason, next_step in adopted:
                body_parts.append(f"- **{item_id}**: {reason}\n  → {next_step}")

        if evaluating:
            body_parts.append("### 🔍 EVALUATING")
            for item_id, reason in evaluating:
                body_parts.append(f"- **{item_id}**: {reason}")

        if skipped:
            body_parts.append(f"### ⏭ SKIP: {', '.join(skipped)}")

        body_parts.append(f"\n*Пълен анализ:* session `{session_id}`")

        push_outbox(
            type="message",
            title=f"📦 Upgrade Pool Scan: {len(adopted)} ADOPT, {len(evaluating)} EVALUATING",
            body="\n\n".join(body_parts)[:2000],
            category="upgrade_pool",
            session_id=session_id,
            dedup_key=f"upgrade_pool_scan_{time.strftime('%Y-%m-%d')}",
        )

        return {
            "ok": True,
            "candidates": len(candidates),
            "decisions": len(decisions),
            "adopted": len(adopted),
            "evaluating": len(evaluating),
            "skipped": len(skipped),
            "session_id": session_id,
        }

    except Exception as exc:
        log.exception("upgrade pool scan failed: %s", exc)
        push_outbox(
            type="message",
            title="📦 Upgrade Pool Scan: ГРЕШКА",
            body=f"Сканирането се провали:\n```\n{exc}\n```",
            category="upgrade_pool",
        )
        return {"ok": False, "error": str(exc)}


def _parse_decisions(text: str, candidates: list[dict]) -> list[tuple[str, str, str, str]]:
    """Parse ITEM:/РЕШЕНИЕ:/ПРИЧИНА:/СЛЕДВАЩА СТЪПКА: blocks from reply."""
    import re
    results = []
    candidate_ids = {c["id"] for c in candidates}

    blocks = re.split(r'(?=ITEM:\s*\S)', text)
    for block in blocks:
        item_m = re.search(r'ITEM:\s*([^\n]+)', block)
        dec_m = re.search(r'РЕШЕНИЕ:\s*(ADOPT|EVALUATING|DEFER|SKIP)', block, re.IGNORECASE)
        reason_m = re.search(r'ПРИЧИНА:\s*([^\n]+(?:\n(?!СЛЕДВАЩА)[^\n]*)*)', block)
        next_m = re.search(r'СЛЕДВАЩА СТЪПКА:\s*([^\n]+(?:\n(?!ITEM)[^\n]*)*)', block)

        if not item_m or not dec_m:
            continue
        item_id = item_m.group(1).strip()
        if item_id not in candidate_ids:
            continue
        decision = dec_m.group(1).upper()
        reason = reason_m.group(1).strip()[:300] if reason_m else ""
        next_step = next_m.group(1).strip()[:200] if next_m else ""
        results.append((item_id, decision, reason, next_step))

    return results
