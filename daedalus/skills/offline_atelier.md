---
name: offline-atelier
description: Work without internet using a hardware-aware local model wardrobe and capsules.
---

# SKILL: Offline atelier

**When**: no API keys, airplane, or the owner says "офлайн", "локален модел", "Ollama".

**How**:
1. Studio → **Мозък** или Command Center → **Свързаност**. Дедал чете RAM/GPU и маркира какво събира.
2. Кликни препоръчания ред. Ако Ollama я няма — https://ollama.com/download после пак.
3. Първото дърпане иска потвърждение (няколко GB). Не дърпай 32B на 8 GB RAM.
4. **Офлайн кит** — спира Jina, търсене, картинки. Файлове, shell, localhost browser, playtest heuristic, капсули остават.
5. Капсула .zip = USB/файл. Static игра: unzip → `index.html`.

**Switch back**: бутон **Облак** (ако има ключ).

**Anti-patterns**:
- Не обещавай облачен LLM в офлайн кит.
- Не `ollama pull` 70B "за всеки случай".
