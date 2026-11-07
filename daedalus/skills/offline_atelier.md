---
name: offline-atelier
description: Work without internet using a hardware-aware local model wardrobe and capsules.
---

# SKILL: Offline atelier

**When**: no API keys, airplane, or the owner says "offline", "local model", "Ollama".

**How**:
1. Connectors → **Brain** (`/admin/daedalus/connectors#brain`), or Studio → **Brain** wardrobe. Daedalus reads RAM/GPU and marks what fits. This is the same brain for chat, workers, autopilot, and CLI — not Studio-only.
2. Click the recommended row. If Ollama is missing — https://ollama.com/download then try again.
3. The first pull asks for confirmation (several GB). Do not pull 32B on 8 GB RAM.
4. **Offline kit** — stops Jina, search, images. Files, shell, localhost browser, heuristic playtest, capsules stay.
5. Capsule `.zip` = USB/file. Static game: unzip → `index.html`.

**Switch back**: **Cloud** (if a key is set).

**Anti-patterns**:
- Do not promise a cloud LLM while the offline kit is on.
- Do not leave workers on OpenRouter while the owner picked Ollama.
