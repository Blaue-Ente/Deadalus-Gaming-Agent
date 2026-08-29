---
name: telegram-gateway
description: Talk to Daedalus from Telegram. BotFather token, pairing, not a user account.
---

# SKILL: Telegram gateway

**When**: the owner asks to command Daedalus from the phone, Telegram, a channel, or a chat bot.

**How**:
1. They create a bot with @BotFather. `DAEDALUS_TELEGRAM_BOT_TOKEN` in `.env`. Restart `daedalus serve`.
2. Unknown DMs get a pairing code. Approve: `daedalus gateway pair CODE` or Studio → Свят → Одобри.
3. Optional `DAEDALUS_TELEGRAM_ALLOWLIST` (numeric user ids) skips pairing.
4. Optional channel: add the bot as admin, set `DAEDALUS_TELEGRAM_CHANNEL_ID`, `daedalus gateway say …`.
5. Same agent as Studio. `/help` `/status` `/new` `/stop` `/whoami`.

**Do not**:
- Log into a personal Telegram account (userbot). Against Telegram ToS.
- Expose port 8787 only so Telegram works — polling is outbound.
- Auto-approve strangers. Tools write files and run shell.
