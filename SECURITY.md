# Security — Daedalus

## Reporting

If you find a vulnerability in Daedalus (RCE outside the intended workspace, key leakage, auth bypass on Studio when it is exposed on a network):

1. Open a **confidential** GitLab issue: https://gitlab.com/Blaue-Ente/deadalus-agent-0/-/issues/new
2. Or write the maintainer on GitLab (do not post a PoC in a public ticket).

Do not commit `.env`, tokens, or Chroma dumps with private chats.

## What Daedalus already does

- HTTP mutations (chat, shell, playground, proposals, admin JSON) require an **owner token**. Loopback Studio loads it from `GET /api/v1/auth/bootstrap`. Remote clients paste `DAEDALUS_TOKEN`.
- `daedalus serve` binds `127.0.0.1` unless you pass `--host`. Binding `0.0.0.0` without a token already on disk or in the environment is refused.
- Shell commands are argv-first. Secrets are stripped from the child env. Destructive host-wide patterns and shell metacharacters are refused. **This is not a container**: a child process can still open host files. On a public bind the runner stays off unless `DAEDALUS_SHELL_ISOLATION=1`.
- `write_file` writes a Playground project, not the harness. Patches to `daedalus/` go through proposals + Approve.
- The Windows app binds `127.0.0.1:8787` only.
- Offline kit refuses search, Jina, and image-gen.
- Published games under `/apps/` stay public (including their small KV store).

## What you should do

- On a laptop: `daedalus serve` (loopback). Do not bind `0.0.0.0` on a home PC.
- On a VPS: HTTPS reverse proxy, `DAEDALUS_TOKEN` set to a long random value, a changed peer secret from `.env.example`, a volume only you persist.
- Do not expose port 8787 to the internet without TLS and a token that is not the default peer secret.
- Keep `GITLAB_TOKEN` / `NETLIFY_AUTH_TOKEN` / LLM keys out of git.

## Supported versions

We support the `main` branch of this repo. There is no separate LTS. Update with `git pull` and `pip install -e .`.
