---
name: long-horizon
description: Keep working across days. Resume from journal. Do not restart lore or reconnaissance.
---

# SKILL: Long-horizon work

**When**: epic games, multi-day apps, or the owner says "продължи" / "resume".

**How**:
1. On start — read `world/journal.md` and the last `NEED_PROGRESS` / scratch journal. Do not redo discovery.
2. Name the slice in one sentence. Ship only that slice.
3. After the slice: append the journal (date, files, open threads, next slice). Emit progress.
4. If a step fails twice — mark blocked, continue the next independent slice.
5. Spawn a tool-using sub-agent for isolated jobs (one level, one character pass). Keep the parent as the architect.
6. Definition of Done is per slice, not per universe.

**Anti-patterns**:
- Re-planning the whole campaign every turn.
- Claiming "готово" for the world when one room works.
- Silent harness edits. Proposals still wait for Approve.
