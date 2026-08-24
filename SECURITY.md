# Security — Daedalus

## Reporting

If you find a vulnerability in Daedalus (RCE outside the jail, key leakage, auth bypass on Studio when it is exposed on a network):

1. Open a **confidential** GitLab issue: https://gitlab.com/Blaue-Ente/deadalus-agent-0/-/issues/new
2. Or write the maintainer on GitLab (do not post a PoC in a public ticket).

Do not commit `.env`, tokens, or Chroma dumps with private chats.

## What Daedalus already does

- Shell commands run in a jail; secrets are stripped from env before `run_command`.
- `write_file` writes a Playground project, not the harness. Patches to `daedalus/` go through proposals + Approve.
- The Windows app binds `127.0.0.1:8787` only.
- Offline kit refuses search, Jina, and image-gen.

## What you should do

- On a laptop: `daedalus serve --host 127.0.0.1`.
- On a VPS: HTTPS reverse proxy, a changed peer secret from `.env.example`, a volume only you persist.
- Do not expose port 8787 to the internet without TLS and a changed secret.
- Keep `GITLAB_TOKEN` / `NETLIFY_AUTH_TOKEN` / LLM keys out of git.

## Supported versions

We support the `main` branch of this repo. There is no separate LTS. Update with `git pull` and `pip install -e .`.
