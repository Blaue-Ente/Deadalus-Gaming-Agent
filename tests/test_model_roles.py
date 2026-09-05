"""Cursor-style model roles: auto-assign, 10¢ Mentor upgrade, edit_file, ask_mentor."""
from __future__ import annotations

import asyncio

import pytest

from daedalus.model_catalog import (
    CatalogModel,
    auto_assign,
    clear_assign_cache,
    remap_to_keyed,
    upgrade_below_usd,
)
from daedalus.tool_catalog import canonical

SNAKE = "Направи нова Playground игра snake: HTML canvas, стрелки. Една завършена среза."

GLM = CatalogModel("z-ai/glm-5.2", "openrouter", "GLM 5.2", 0.35, 1.40, 200_000, 16_384, True)
KIMI = CatalogModel("moonshotai/kimi-k2.5", "openrouter", "Kimi K2.5", 0.45, 2.20, 256_000, 16_384, True)
HAIKU = CatalogModel("anthropic/claude-haiku-4.5", "openrouter", "Haiku", 1.00, 5.00, 200_000, 8_192, True)
SONNET = CatalogModel("anthropic/claude-sonnet-4.6", "openrouter", "Sonnet", 3.00, 15.00, 200_000, 16_384, True)
LLAMA = CatalogModel("meta-llama/llama-3.1-8b-instruct", "openrouter", "Llama 8B", 0.02, 0.05, 128_000, 8_192, False)

CHEAP_BUILD_POOL = [GLM, HAIKU, SONNET, LLAMA]
DEAR_BUILD_POOL = [KIMI, HAIKU, SONNET, LLAMA]


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("DAEDALUS_STANDALONE", "1")
    monkeypatch.setenv("DAEDALUS_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("DAEDALUS_PLAYGROUND", str(tmp_path / "projects"))
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test-not-real")
    monkeypatch.delenv("DAEDALUS_MODEL_BUILD", raising=False)
    monkeypatch.delenv("DAEDALUS_MODEL_WRAP_UP", raising=False)
    monkeypatch.delenv("DAEDALUS_MODEL_MENTOR", raising=False)
    monkeypatch.delenv("DAEDALUS_PROVIDER_BUILD", raising=False)
    monkeypatch.delenv("DAEDALUS_PROVIDER_WRAP_UP", raising=False)
    monkeypatch.delenv("DAEDALUS_PROVIDER_MENTOR", raising=False)
    monkeypatch.delenv("DAEDALUS_MENTOR_UPGRADE_BELOW_USD", raising=False)
    monkeypatch.setenv("DAEDALUS_ROLE_AUTO", "1")
    from daedalus.paths import data_root, playground_projects_dir

    data_root.cache_clear()
    playground_projects_dir.cache_clear()
    clear_assign_cache()
    yield tmp_path
    clear_assign_cache()
    data_root.cache_clear()
    playground_projects_dir.cache_clear()


def test_glm_typical_build_is_under_ten_cents():
    """R3: 80k in / 40k out on GLM 5.2 is ~8.4¢, so Mentor may be Sonnet."""
    assert GLM.estimate_build_usd() < 0.10
    assert KIMI.estimate_build_usd() >= 0.10
    assert upgrade_below_usd() == 0.10
    from daedalus.model_catalog import find
    live = find("z-ai/glm-5.2", "openrouter")
    assert live is not None and live.prompt_per_m > 0
    assert live.estimate_build_usd() < 0.10


def test_auto_mentor_is_sonnet_when_typical_build_is_under_10c():
    picks = auto_assign(models=CHEAP_BUILD_POOL, providers=["openrouter"])
    assert picks["build"].model == "z-ai/glm-5.2"
    assert picks["build"].build_usd < 0.10
    assert "sonnet" in picks["mentor"].model
    assert "Sonnet mentor" in picks["mentor"].why
    assert picks["wrap_up"].model == "meta-llama/llama-3.1-8b-instruct"


def test_auto_mentor_stays_haiku_when_typical_build_is_10c_or_more():
    picks = auto_assign(models=DEAR_BUILD_POOL, providers=["openrouter"])
    assert picks["build"].model == "moonshotai/kimi-k2.5"
    assert picks["build"].build_usd >= 0.10
    assert "haiku" in picks["mentor"].model
    assert "Haiku mentor" in picks["mentor"].why


def test_mentor_upgrade_cap_is_overridable(monkeypatch):
    monkeypatch.setenv("DAEDALUS_MENTOR_UPGRADE_BELOW_USD", "0.05")
    picks = auto_assign(models=CHEAP_BUILD_POOL, providers=["openrouter"])
    assert GLM.estimate_build_usd() >= 0.05
    assert "haiku" in picks["mentor"].model

    monkeypatch.setenv("DAEDALUS_MENTOR_UPGRADE_BELOW_USD", "0.20")
    picks = auto_assign(models=DEAR_BUILD_POOL, providers=["openrouter"])
    assert KIMI.estimate_build_usd() < 0.20
    assert "sonnet" in picks["mentor"].model


def test_auto_assign_without_keys_falls_back_to_primary():
    picks = auto_assign(
        models=CHEAP_BUILD_POOL,
        providers=[],
        primary={"provider": "openrouter", "model": "primary/x"},
    )
    assert picks["build"].model == "primary/x"
    assert picks["mentor"].model == "primary/x"


def test_wrap_up_skips_sentinel_and_unknown_zero_prices():
    auto = CatalogModel("openrouter/auto", "openrouter", "Auto", -1.0, -1.0, 0, 0, False, "openrouter")
    stub = CatalogModel("anthropic/claude-sonnet-4-5", "openrouter", "Sonnet stub", 0.0, 0.0, 0, 0, True, "preset")
    picks = auto_assign(models=CHEAP_BUILD_POOL + [auto, stub], providers=["openrouter"])
    assert picks["wrap_up"].model == "meta-llama/llama-3.1-8b-instruct"


def test_remap_native_claude_through_openrouter_when_anthropic_has_no_key():
    p, m = remap_to_keyed("anthropic", "claude-haiku-4-5", providers=["openrouter"])
    assert p == "openrouter" and m == "anthropic/claude-haiku-4.5"
    p, m = remap_to_keyed("openrouter", "z-ai/glm-5.2", providers=["openrouter"])
    assert p == "openrouter" and m == "z-ai/glm-5.2"
    p, m = remap_to_keyed("", "strong/tool-caller", providers=["openrouter"])
    assert p == "" and m == "strong/tool-caller"


def test_set_roles_persists_manual_and_auto(isolated):
    from daedalus import llm_config
    from daedalus.llm_router_config import router_write_context

    with router_write_context():
        out = llm_config.set_roles({
            "build": {"mode": "manual", "provider": "openrouter", "model": "z-ai/glm-5.2"},
            "mentor": {"mode": "auto"},
        })
    assert out["roles"]["build"]["source"] == "db"
    assert out["roles"]["build"]["model"] == "z-ai/glm-5.2"
    assert out["roles"]["build"]["mode"] == "manual"
    assert out["roles"]["mentor"]["mode"] == "auto"
    assert "api_key" not in str(out)


def test_freeze_roles_pins_build_but_mentor_stays_live(isolated, monkeypatch):
    from daedalus import llm_config

    monkeypatch.setenv("DAEDALUS_ROLE_AUTO", "0")
    monkeypatch.setenv("DAEDALUS_MODEL_BUILD", "builder-a")
    monkeypatch.setenv("DAEDALUS_MODEL_MENTOR", "anthropic/claude-sonnet-4.6")
    monkeypatch.setattr(
        llm_config,
        "get_config",
        lambda: {"provider": "openrouter", "model": "chat-x", "api_key": "k", "sdk": "openai"},
    )
    tok = llm_config.freeze_roles()
    try:
        frozen = llm_config._FROZEN_ROLES.get() or {}
        assert "build" in frozen and "mentor" not in frozen
        monkeypatch.setenv("DAEDALUS_MODEL_BUILD", "builder-b")
        monkeypatch.setenv("DAEDALUS_MODEL_MENTOR", "anthropic/claude-haiku-4.5")
        assert llm_config.config_for_role("build")["model"] == "builder-a"
        assert "haiku" in llm_config.config_for_role("mentor")["model"]
    finally:
        llm_config.unfreeze_roles(tok)
    assert llm_config.config_for_role("build")["model"] == "builder-b"


def test_spend_cap_drops_auto_sonnet_mentor_to_haiku(isolated, monkeypatch):
    from daedalus import llm_config, llm_cost

    monkeypatch.setattr(
        llm_config,
        "get_config",
        lambda: {
            "provider": "openrouter",
            "model": "z-ai/glm-5.2",
            "api_key": "k",
            "sdk": "openai",
            "base_url": "https://openrouter.ai/api/v1",
        },
    )

    def _assign(role):
        if role != "mentor":
            return {"provider": "", "model": "", "mode": "auto", "source": "primary", "why": ""}
        return {
            "provider": "openrouter",
            "model": "anthropic/claude-sonnet-4.6",
            "mode": "auto",
            "source": "auto",
            "why": "typical build under cap",
        }

    monkeypatch.setattr(llm_config, "role_assignment", _assign)
    led = llm_cost.begin_turn()
    try:
        cheap = llm_config.config_for_role("mentor")
        assert "sonnet" in cheap["model"]
        led.record(
            role="build", provider="openrouter", model="z-ai/glm-5.2",
            prompt_tokens=10, completion_tokens=10, cost_usd=0.15,
        )
        assert led.below_mentor_upgrade() is False
        capped = llm_config.config_for_role("mentor")
        assert "haiku" in capped["model"]
        assert "spend-cap" in (capped.get("source") or "")
    finally:
        llm_cost.end_turn()


def test_cost_ledger_estimates_and_summarises():
    from daedalus import llm_cost

    led = llm_cost.begin_turn()
    try:
        led.record(
            role="build", provider="openrouter", model="z-ai/glm-5.2",
            prompt_tokens=80_000, completion_tokens=40_000,
        )
        assert led.rows[0].source == "estimate"
        assert led.total_usd() > 0
        assert led.below_mentor_upgrade() is True
        led.record(
            role="mentor", provider="openrouter", model="anthropic/claude-sonnet-4.6",
            prompt_tokens=1, completion_tokens=1, cost_usd=0.08,
        )
        assert "LLM spend this turn" in led.summary()
        assert led.as_dict()["calls"]
    finally:
        llm_cost.end_turn()


def test_edit_file_is_surgical_and_gated(isolated, monkeypatch):
    from daedalus import playground as pg
    from daedalus.native_tools import execute_native_tool
    from daedalus.paths import playground_projects_dir

    root = isolated / "projects"
    (root / "snake-game").mkdir(parents=True)
    (root / "snake-game" / "game.js").write_text("const SPEED = 5;\n", encoding="utf-8")
    monkeypatch.setenv("DAEDALUS_PLAYGROUND", str(root))
    playground_projects_dir.cache_clear()
    monkeypatch.setattr(pg, "get_project", lambda slug: {"slug": slug, "kind": "static"})

    async def _run(name, args):
        return await execute_native_tool(
            name, args, memory=None, session_id="playground:x", read_file_fn=lambda *a: {},
        )

    miss = asyncio.run(_run("edit_file", {
        "slug": "snake-game", "path": "game.js", "search": "nope", "replace": "x",
    }))
    assert miss.startswith("── edit_file FAILED") and "не е намерен" in miss

    ok = asyncio.run(_run("edit_file", {
        "slug": "snake-game", "path": "game.js",
        "search": "const SPEED = 5;", "replace": "const SPEED = 12;",
    }))
    assert ok.startswith("── edit_file OK snake-game/game.js")
    assert (root / "snake-game" / "game.js").read_text(encoding="utf-8") == "const SPEED = 12;\n"

    bad = asyncio.run(_run("edit_file", {
        "slug": "snake-game", "path": "game.js",
        "search": "const SPEED = 12;",
        "replace": "let gameLoopId = 1;\nlet gameLoopId = 2;",
    }))
    assert bad.startswith("── edit_file FAILED syntax")
    blocked = asyncio.run(_run("ask_mentor", {"question": "help"}))
    assert blocked.startswith("── ask_mentor BLOCKED")


def test_edit_file_repairs_a_failed_write():
    from daedalus.job_contract import unrecovered_failure_indexes

    calls = [
        {"tool": "write_file", "ok": False, "input": {"slug": "s", "path": "game.js"}},
        {"tool": "edit_file", "ok": True, "input": {"slug": "s", "path": "game.js"}},
    ]
    assert unrecovered_failure_indexes(calls) == []


def test_ask_mentor_runs_inside_a_build_turn():
    from daedalus.build_loop import BuildLoop
    from daedalus.build_mentor import BuildMentor
    from daedalus.native_tools import _ask_mentor
    from daedalus.tool_router import route

    async def _call(system, messages, role="mentor"):
        assert role == "mentor"
        assert "BUILDER QUESTION" in messages[0]["content"]
        return (
            '{"diagnosis": "fovy was in degrees", '
            '"instruction": "call math3d perspective with radians", "consult": null}'
        )

    loop = BuildLoop(route(SNAKE), SNAKE, fix_rounds=1)
    loop.mentor = BuildMentor(_call)
    out = asyncio.run(_ask_mentor(loop, {"question": "perspective args?"}))
    assert "MENTOR" in out and "degrees" in out and "math3d" in out
    again = asyncio.run(_ask_mentor(loop, {"question": "perspective args?"}))
    assert "already answered" in again or "out of budget" in again


def test_native_catalog_maps_edit_and_mentor():
    from daedalus.native_tools import NATIVE_TOOL_SPECS
    from daedalus.task_playbooks import PLAYBOOKS
    from daedalus.tool_protocol import MARKER_TO_NATIVE

    names = {t["name"] for t in NATIVE_TOOL_SPECS}
    assert {"edit_file", "ask_mentor"} <= names
    assert canonical("NEED_PLAYGROUND_EDIT") == "edit_file"
    assert MARKER_TO_NATIVE["NEED_PLAYGROUND_EDIT"] == "edit_file"
    arcade = next(pb for pb in PLAYBOOKS if pb.id == "playground.arcade")
    assert "edit_file" in arcade.tools and "ask_mentor" in arcade.tools


def test_roles_http_api(isolated):
    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app)
    listing = client.get("/api/v1/llm/catalog?limit=20")
    assert listing.status_code == 200
    body = listing.json()
    assert body["ok"] is True and "models" in body
    assert body["upgrade_below_usd"] == 0.10

    roles = client.get("/api/v1/llm/roles")
    assert roles.status_code == 200
    data = roles.json()
    assert "roles" in data and "upgrade_below_usd" in data
    assert "api_key" not in str(data)

    posted = client.post("/api/v1/llm/roles", json={
        "build": {"mode": "manual", "provider": "openrouter", "model": "z-ai/glm-5.2"},
    })
    assert posted.status_code == 200
    saved = posted.json()["roles"]["build"]
    assert saved["model"] == "z-ai/glm-5.2" and saved["source"] == "db"

    status = client.get("/api/v1/llm/status")
    assert status.status_code == 200
    assert "roles" in status.json()
