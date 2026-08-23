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

No API key on loopback. Probe with `daedalus models`. Raise Ollama context: `OLLAMA_CONTEXT_LENGTH=64000`.
