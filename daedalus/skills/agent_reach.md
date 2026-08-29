---
name: agent-reach
description: Read the live web via Jina; search DuckDuckGo; find Reddit threads; optional yt-dlp, gh, rdt, agent-reach doctor.
---

# SKILL: Agent Reach (capability layer)

**When**: the owner asks to read a page, search the web, look on Reddit, summarize a GitHub repo, pull YouTube captions, or "какво пише на този линк".

**Do not** vendor or `pip install` the Agent Reach CLI unless the owner asks. The PyPI package named `agent-reach` is **unrelated**. Daedalus already implements the zero-config web / search / Reddit-via-search channels. Upstream: https://github.com/Panniantong/Agent-Reach (MIT).

**How**:
1. Readable article → native tool `web_read` (Jina `https://r.jina.ai/{url}`, no key).
2. Web search → `reach_search` with `channel=web` (DuckDuckGo HTML, no key). `web_search` is the older alias.
3. Reddit → `reach_search` with `channel=reddit` (optional `subreddit=gamedev`).
   - Anonymous Reddit `.json` is blocked (403). That is expected.
   - Default: DuckDuckGo `site:reddit.com` (public threads, no login).
   - Stronger: owner installs `rdt-cli` and runs `rdt login`, or sets `DAEDALUS_REDDIT_COOKIE` from Cookie-Editor (`reddit_session` only). Cookies stay on this machine.
4. RSS/Atom URL → `web_read` (stdlib parse).
5. YouTube → `yt-dlp` if installed; otherwise tell the owner to install it or Agent Reach.
6. GitHub → `gh repo view owner/repo` if `gh` is on PATH.
7. Health → `reach_status` or `agent-reach doctor` when the CLI exists.

**Evolution**: when Studio → Еволюция is on, the gardener runs two rotating Reach queries per tick (web + Reddit) and journals titles. It still never silent-edits the harness.

**Install (optional, owner-driven)**:
https://raw.githubusercontent.com/Panniantong/agent-reach/main/docs/install.md

**Anti-patterns**:
- Do not scrape logged-in Twitter/Reddit/Xiaohongshu. Those need the owner's own cookies via Agent Reach / rdt — not a Daedalus dump of their CLI.
- Do not wrap Jina behind a paid proxy.
- Do not claim Reddit JSON works without a session. Be honest and use the DuckDuckGo fallback.
