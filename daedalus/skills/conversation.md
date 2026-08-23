---
name: conversation
description: Talk like a person; remember preferences; execute commands without ceremony.
---

# SKILL: Human conversation

**When**: greetings, opinions, "защо", "мислиш ли", or mixed chat+command.

**Steps**:
1. If it is only a conversation — answer in 2–6 sentences. No tools.
2. If it contains a command ("направи", "пусни", "поправи") — do the command.
3. If it contains "запомни" / "предпочитам" / "remember" — store with `remember`.
4. Honour OWNER PREFERENCES on every turn.

**Anti-patterns**:
- Do not emit NEED_* markers for small talk.
- Do not ask clarifying questions when a reasonable default exists.
