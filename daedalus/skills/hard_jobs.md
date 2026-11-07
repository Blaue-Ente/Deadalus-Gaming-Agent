---
name: hard-jobs
description: Deliver the asked result on hard, multi-file, existing-project work. Fresh slices, named artifacts, original ask as north star.
---

# SKILL: Hard jobs

**When**: craft/epic coding on an existing slug, a multi-file change, or the owner says the last result was wrong / incomplete.

**How**:
1. Restate the owner's ask as 3–7 acceptance lines. That text is the north star — not the chat, not a weaker proxy.
2. Split into slices. One slice = one fresh sub-agent. Do not dump the whole session into it.
3. `spawn_subagent({slug, task, role:"explorer", return_contexts:[...]})` first when the tree is unknown. Explorer cannot write.
4. Then `role:"coder"` with `context_refs` of those artifacts and `original_ask` set to the owner's words. Implement that slice only.
5. `role:"verify"` (read-only) against acceptance. TASK_COMPLETE only with evidence. Contradiction ("готово" + "не мога") is a fail.
6. Smoke / browser still required. Job ledger open slices block "готово".

**Anti-patterns**:
- One giant context window that "remembers" a rotting chat.
- Fifteen specialist agents / a second orchestrator.
- Claiming done while slices remain, or while the reply admits failure.
- Greenfield Tetris — keep the normal playable-slice autopilot.
