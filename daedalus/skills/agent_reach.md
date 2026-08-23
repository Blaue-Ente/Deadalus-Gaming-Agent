---
name: agent-reach
description: Read the live web via Jina Reader; optional yt-dlp, gh, and agent-reach doctor.
---

# SKILL: Agent Reach (capability layer)

**When**: the owner asks to read a page, summarize a GitHub repo, pull YouTube captions, or "какво пише на този линк".

**Do not** vendor or `pip install` the whole Agent Reach CLI unless the owner asks. Daedalus already implements the zero-config web channel.

**How**:
1. Readable article → native tool `web_read` (Jina `https://r.jina.ai/{url}`, no key).
2. RSS/Atom URL → same tool (stdlib parse).
3. YouTube → `yt-dlp` if installed; otherwise tell the owner to install it or Agent Reach.
4. GitHub → `gh repo view owner/repo` if `gh` is on PATH.
5. Health → `reach_status` or `agent-reach doctor` when the CLI exists.

**Install (optional, owner-driven)**:
https://raw.githubusercontent.com/Panniantong/agent-reach/main/docs/install.md

**Anti-patterns**:
- Do not scrape logged-in Twitter/Reddit/Xiaohongshu. Those need the owner's own cookies via Agent Reach — not Daedalus.
- Do not wrap Jina behind a paid proxy.
