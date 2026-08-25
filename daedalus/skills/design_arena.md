---
name: design-arena
description: Use Design Arena practices (playable brief, A/B of two builds) without vendoring their harness.
---

# SKILL: Design Arena (practices, not a dump)

**When**: the owner mentions designarena.ai, "как правят агентите игри там", A/B of two builds, or a one-line game/app prompt that needs a real brief.

**Upstream**: https://designarena.ai — crowdsourced ELO arena. Open harness (MIT): https://github.com/Design-Arena/agent-runner. Methodology: https://notes.designarena.ai/methodology/

**Do not** vendor Agent Runner. Do not `pip install agent-runner` unless the owner asks to *evaluate* a model, not to replace Daedalus. Do not copy their published enhancement system prompts.

**How Daedalus uses the useful bits**:
1. Before files: native/HTTP `design_brief` / `expand_brief` — loop, controls, win/lose, art, platform, M0 feasibility.
2. After two playable slugs exist: `playtest` with `slug_b` (pairwise). Winner is the artifact with fewer blockers / more playable signals — not brand names.
3. Autopilot already feeds errors back. Prefer smoke/`vite build` over hanging `npm run dev` inside the agent loop.
4. Evolution radar includes Design Arena notes + agent-runner as study sources.

**Not useful here**: Vercel/Supabase tool belt, crowdsourced Elo UI, single-file HTML as the default game, private lab prompts.
