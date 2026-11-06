---
name: local-llm
description: Use Ollama, LM Studio, llama.cpp, or vLLM when DAEDALUS_PROVIDER is local.
---

# SKILL: Local LLM

**When**: owner wants privacy, offline work, or `DAEDALUS_LOCAL_LLM=1`.

**Setup**:
- Ollama: `ollama serve` then `DAEDALUS_PROVIDER=ollama` (optional `OLLAMA_HOST`, `DAEDALUS_MODEL`).
- LM Studio: start server on port 1234, `DAEDALUS_PROVIDER=lmstudio`.
- llama.cpp: `llama-server` on 8080, `DAEDALUS_PROVIDER=llamacpp`.
- vLLM: OpenAI API on 8000, `DAEDALUS_PROVIDER=vllm`.

No API key on loopback. Probe with `daedalus models`.

**Context and tools.** Daedalus asks Ollama `POST /api/show` for `capabilities` (including `tools`) and `GET /api/ps` for the loaded context. The OpenAI `/v1` route ignores `num_ctx`. The server default window is 4096. A tool-bearing build prompt is about 4k tokens before the user turn, so that default silently drops the system prompt. When the loaded window is too small, Connectors warns and the build turn uses a slim system prompt and a short tool list. A model that does not advertise `tools` is a warning, not a quiet empty tool list.

**Whisper / craft vs epic.** Whisper and craft stay on the local wardrobe model (`qwen2.5-coder:7b` unless you pick another). Epic uses a cloud provider for that turn only when a cloud key is set and offline mode is off. That does not rewrite `wardrobe.json` and does not `ollama pull`. With no cloud key, epic stays local and the slim profile applies when the window is small.

Raise the Ollama process window with `OLLAMA_CONTEXT_LENGTH=32768` (64000 if the machine has the RAM) and restart `ollama serve`. Daedalus reads the loaded window from `/api/ps`; setting the variable only in the Daedalus shell is not enough unless the daemon inherited it.
