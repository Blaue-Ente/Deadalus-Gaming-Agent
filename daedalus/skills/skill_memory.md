---
name: skill_memory
description: Progressive skill catalog. Failure/success drafts wait for owner Approve; the agent cannot approve itself.
---

# SKILL: Skill memory

**When**: a repair pattern, a "what worked" note, or the owner mentions уроци / Approve / skill catalog.

**How**:
1. Prompt gets a catalog (name + description) and at most one SKILL.md body.
2. Load more with native tool `skill` action catalog | load | pending.
3. `draft_from_failure` / `draft_from_success` write JSON under `learned_skills/pending/`. They must not enter the next turn until Approve.
4. Owner Approve / Reject is Studio **Уроци** or HTTP. There is no native approve action.

**Anti-patterns**:
- Dumping full builtin SKILL.md files into every turn.
- Injecting a pending draft because the last build failed.
- The agent approving its own lesson.
