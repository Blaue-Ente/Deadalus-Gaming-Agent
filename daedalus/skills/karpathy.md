---
name: karpathy
description: Think before coding, simplicity first, surgical edits, verify with goals. Use when the owner wants caution over speed, or mentions Karpathy / overcomplication / drive-by refactors.
---

# SKILL: Karpathy coding bar

**When**: non-trivial implementation, ambiguous asks, a temptation to "improve" adjacent code, or the owner pins this skill.

## 1. Think before coding

- State assumptions. If two readings of the ask are plausible, name them — do not pick silently.
- Push back when a simpler approach exists.
- Stop when confused. Ask one precise question instead of guessing a thousand-line design.

## 2. Simplicity first

- Minimum code that solves the requested problem. Nothing speculative.
- No abstraction for a single call site. No config flags that were not asked for.
- If 200 lines could be 50, rewrite before claiming done.

## 3. Surgical changes

- Touch only what the ask requires. Match existing style.
- Do not drive-by reformat, rename, or delete unrelated dead code.
- Clean up only orphans **your** change created (unused imports you added, etc.).

## 4. Goal-driven execution

- Turn the ask into a verifiable goal: test, smoke, browser check, or a named file on disk.
- Loop until the check passes. "Seems right" is not done.
- Weak criteria ("make it work") need a concrete check before you start.

**Anti-patterns**: silent assumptions, bloated helpers, orthogonal cleanup, claiming done without running the project's own test/smoke command.
