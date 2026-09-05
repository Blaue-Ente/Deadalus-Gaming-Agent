"""S5 — one build layer: the wrap-up is a short sanitized message, model roles per phase."""
from __future__ import annotations

import asyncio
from pathlib import Path

from daedalus import llm_config
from daedalus.build_loop import BuildLoop
from daedalus.build_wrap_up import (
    WRAP_UP_RULES,
    fallback_summary,
    sanitize_reply,
    wrap_up,
    wrap_up_prompt,
)
from daedalus.tool_router import route

SNAKE = "Направи нова Playground игра snake: HTML canvas, стрелки. Една завършена среза."

MESSY_REPLY = """<think>
Let me plan: first I should list the directory...
</think>
Готово — snake е готова.

NEED_LIST_DIR /workspace
{"tool": "read_file", "arguments": {"path": "game.js"}}
<need_run_command cmd="ls"/>

Файлове: index.html, game.js в snake-game.
━━━ DEFINITION OF DONE ━━━
SELECT * FROM projects;
```json
```
Отвори preview-а и играй със стрелките.
"""


def _built_loop(*, complete: bool = True) -> BuildLoop:
    loop = BuildLoop(route(SNAKE), SNAKE, fix_rounds=1)
    loop.observe("playground_new", {"name": "snake-game"}, "── playground_new OK slug=snake-game ──", ok=True)
    loop.observe("write_file", {"slug": "snake-game", "path": "index.html"}, "── write_file OK ──", ok=True)
    if complete:
        loop.observe("write_file", {"slug": "snake-game", "path": "game.js"}, "── write_file OK ──", ok=True)
        loop.verify_report = ["── game_lint check snake-game/game.js OK ──"]
    else:
        loop.observe("write_file", {"slug": "snake-game", "path": "game.js"},
                     "── write_file FAILED syntax snake-game/game.js ── line 5: Identifier 'gameLoopId' has already been declared", ok=False)
        loop.stop_reason = "fix budget exhausted"
    loop.verdict("")
    return loop


def test_sanitize_strips_everything_the_owner_must_not_see():
    clean = sanitize_reply(MESSY_REPLY)
    assert "<think>" not in clean and "list the directory" not in clean
    assert "NEED_LIST_DIR" not in clean
    assert '"tool"' not in clean and "<need_run_command" not in clean
    assert "SELECT" not in clean and "DEFINITION OF DONE" not in clean
    assert "```" not in clean
    assert clean.startswith("Готово — snake е готова.")
    assert "Файлове: index.html, game.js в snake-game." in clean
    assert clean.endswith("Отвори preview-а и играй със стрелките.")
    assert sanitize_reply("") == ""
    assert sanitize_reply("<think>only thoughts, never closed") == ""


def test_wrap_up_prompt_is_the_ledger_not_the_whole_context():
    loop = _built_loop()
    log = [{"tool": "write_file", "ok": True}, {"tool": "write_file", "ok": True}, {"tool": "run_command", "ok": False}]
    prompt = wrap_up_prompt(loop, log, SNAKE * 20)
    assert prompt.startswith("━━━ BUILD LEDGER (kernel) ━━━")
    assert "Owner asked:" in prompt and prompt.count("Направи нова Playground") < 20  # truncated ask
    assert "Tool calls: 2 ok, 1 failed." in prompt
    assert "Files written: snake-game/index.html, snake-game/game.js" in prompt
    assert "preview: /api/v1/playground/projects/snake-game/preview/index.html" in prompt
    assert "Write the closing message now." in prompt


def test_wrap_up_calls_the_wrap_up_role_and_sanitizes():
    loop = _built_loop()
    seen: dict = {}

    async def _call(system, messages, **kw):
        seen["system"] = system
        seen["messages"] = messages
        seen["kw"] = kw
        return MESSY_REPLY

    text = asyncio.run(wrap_up(loop, [], SNAKE, _call, "PERSONA"))
    assert seen["kw"] == {"role": "wrap_up"}
    assert seen["system"].startswith("PERSONA") and WRAP_UP_RULES in seen["system"]
    assert len(seen["messages"]) == 1 and "BUILD LEDGER" in seen["messages"][0]["content"]
    assert "NEED_" not in text and "<think>" not in text and "snake е готова" in text


def test_wrap_up_falls_back_to_the_ledger_when_the_model_is_useless_or_down():
    loop = _built_loop(complete=False)

    async def _empty(system, messages, **kw):
        return "<think>hmm</think>\nNEED_LIST_DIR ."

    text = asyncio.run(wrap_up(loop, [], SNAKE, _empty, "P"))
    assert text == fallback_summary(loop)
    assert "Project: snake-game" in text
    assert "Files: index.html" in text and "game.js" not in text.split("Files:")[1].split("\n")[0]
    assert "Open: /api/v1/playground/projects/snake-game/preview/index.html" in text
    assert "Unrecovered: write_file" in text and "Stopped: fix budget exhausted." in text

    async def _boom(system, messages, **kw):
        raise RuntimeError("provider down")

    assert asyncio.run(wrap_up(loop, [], SNAKE, _boom, "P")) == fallback_summary(loop)

    done = fallback_summary(_built_loop(complete=True))
    assert "Plan complete." in done and "Kernel verify: ── game_lint check snake-game/game.js OK ──" in done


def test_model_roles_override_only_the_model(monkeypatch):
    monkeypatch.setattr(llm_config, "get_config", lambda: {"provider": "openrouter", "model": "primary/x", "api_key": "k", "sdk": "openai"})
    monkeypatch.delenv("DAEDALUS_MODEL_BUILD", raising=False)
    monkeypatch.delenv("DAEDALUS_MODEL_WRAP_UP", raising=False)
    assert llm_config.role_model("build") == "" and llm_config.config_for_role("build")["model"] == "primary/x"
    assert llm_config.role_model("persona") == ""  # unknown role → primary
    monkeypatch.setenv("DAEDALUS_MODEL_BUILD", "strong/tool-caller")
    monkeypatch.setenv("DAEDALUS_MODEL_WRAP_UP", "cheap/summary")
    build = llm_config.config_for_role("build")
    assert build["model"] == "strong/tool-caller" and build["provider"] == "openrouter" and build["api_key"] == "k"
    assert build["role"] == "build"
    assert llm_config.config_for_role("wrap-up")["model"] == "cheap/summary"
    assert llm_config.config_for_role("")["model"] == "primary/x"


def test_agent_has_one_build_layer():
    src = Path("daedalus/agent.py").read_text(encoding="utf-8")
    assert "functools.partial(_call_claude_with_tools, role=\"build\")" in src
    assert "raw = await wrap_up(_loop, _native_log, user_message, _call_claude, _call_persona)" in src
    assert "if not _planning_succeeded and not _build_wrapped:" in src
    assert "range(0 if _build_wrapped else MAX_TOOL_ITERATIONS)" in src
    assert "_tool_iter = -1" in src
    assert "NATIVE BUILD ALREADY ACTED" not in src
    assert "propose_block = None if _build_wrapped else _extract_propose_block(raw)" in src
    assert "NATIVE BUILD ALREADY ACTED" not in src
    # no second DoD sticker on a wrapped build
    assert 'c.get("tool") == "build_loop"), None)' in src
