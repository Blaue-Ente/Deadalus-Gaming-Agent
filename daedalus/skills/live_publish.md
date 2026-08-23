---
name: live-publish
description: Publish a playground game to /apps/ and a public https URL (Netlify or GitLab Pages).
---

# SKILL: Наживо

**When**: owner says "публикувай", "наживо", "дай линк", "качи в интернет".

**How**:
1. Studio → **Наживо** (or `POST /api/v1/playground/projects/{slug}/live`).
2. Always materialize `/apps/{slug}/` first.
3. Auto backend: Netlify token → GitLab Pages → Netlify anonymous (1 hour).
4. Static / vite / expo-web only. Unity → Git push. FastAPI stays a process.
5. Durable Netlify: `NETLIFY_AUTH_TOKEN` from https://app.netlify.com/user/applications
6. GitLab Pages creates a **public** project `daedalus-{slug}` — the game is visible.

**Anti-patterns**:
- Do not promise a free `.com`.
- Do not treat a 1-hour anonymous URL as permanent.
- Do not enable offline kit and expect a public link.
- Do not copy the whole playground (with node_modules) to Pages — export `public/`.
