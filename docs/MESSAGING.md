# Talk to Daedalus from Telegram (or Studio)

Same agent as the Studio chat. Same files, shell, playtest. The process
**polls Telegram outbound** — Studio can stay on `127.0.0.1`. You do not
open port 8787 to the internet just to send commands from your phone.

One bot, owner pairing, no dump of a multi-platform chat mesh.

## Bot, not a personal account

Create a bot with [@BotFather](https://t.me/BotFather). Put the token in `.env`.
That bot can:

- DM you (owner commands)
- Be an **admin of a channel** and post status (`daedalus gateway say …`)

Daedalus will **not** log into your personal Telegram user (userbot / Telethon
session). That is against Telegram’s terms and would put your account at risk.

Discord **inbound** chat is not this adapter. Outbound deploy pings already use
`DISCORD_WEBHOOK_URL`. WhatsApp / Signal can wait; the inbound shape is one
`handle_inbound` if we add them later.

## Laptop or VPS

| Where Daedalus runs | How Telegram reaches it |
|---|---|
| PC, `daedalus serve` on 127.0.0.1 | Process calls `api.telegram.org`. NAT is fine. |
| Windows Start Menu app | Same — token in `%LOCALAPPDATA%\Daedalus\.env` |
| Docker / VPS | Same poller inside the container. Still no need to expose 8787 for chat. |
| Cloud Studio in a browser | Optional extra. Telegram does not depend on it. |

Kill switch: `DAEDALUS_GATEWAY=0` or the offline kit (`DAEDALUS_OFFLINE=1`).

## Setup

1. BotFather → `/newbot` → copy the token.
2. `.env`:

```
DAEDALUS_TELEGRAM_BOT_TOKEN=123456:ABC…
# optional: skip pairing for your user id (Telegram → Settings → profile, or /whoami after pairing)
# DAEDALUS_TELEGRAM_ALLOWLIST=111111111
# optional channel for outbound status (bot must be admin)
# DAEDALUS_TELEGRAM_CHANNEL_ID=-1001234567890
```

3. Restart `daedalus serve` (or `daedalus desktop`). The poller starts with Studio.
4. DM the bot. It replies with a **pairing code** (unknown users never reach tools).
5. Approve:

```bash
daedalus gateway pair XKGH5N7P
```

or Studio → **Свят** → paste the code → **Одобри**.

6. Chat normally: “направи змия”, “пусни playtest”. Slash: `/help` `/status` `/new` `/stop` `/whoami`.

Foreground poller without HTTP (rare): `daedalus gateway start`. Do not run that
**and** `daedalus serve` together — Telegram allows one `getUpdates` consumer.

## CLI

| Command | What |
|---|---|
| `daedalus gateway` / `daedalus gateway status` | Token, poller, pending codes |
| `daedalus gateway pair CODE` | Approve a user |
| `daedalus gateway list` | Paired + pending |
| `daedalus gateway revoke telegram USER_ID` | Drop access |
| `daedalus gateway say text…` | Post to the channel |
| `daedalus serve --no-gateway` | Studio only |

## Security

- Default deny. Allowlist and/or pairing. Groups ignored unless `DAEDALUS_TELEGRAM_GROUPS=1` (still per-user, not “anyone in the group”).
- Pairing codes expire in one hour. Owner-only approve (CLI / Studio token).
- A public bind of Studio still needs `DAEDALUS_TOKEN`. Telegram pairing is not a substitute for that HTTP token.
- `DAEDALUS_TELEGRAM_PAIRING=0` refuses strangers with no code (allowlist only).

## What this is not

Daedalus already has Studio, evolution (Approve, not silent edits), playtest,
and verify. The gateway is the useful slice: **talk to the same process from
Telegram**. It is not a second product and not a login to a personal account.
