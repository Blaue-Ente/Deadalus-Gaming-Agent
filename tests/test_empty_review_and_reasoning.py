"""An empty model reply caused by hidden reasoning is retried with room; an empty code review is not a rejection.

Found live (2026-11-11, Guided): Haiku 5.5 on OpenRouter reasons by default. The stage builder's 400-token code review
spent every token thinking and returned "", which counted as a rejection; each file was rewritten five times against
an empty reason and every stage failed. Disabling reasoning is not portable (some models refuse: "Reasoning is
mandatory for this endpoint"), so the answer gets room instead."""
from __future__ import annotations

import asyncio

from daedalus import llm_client


def test_an_empty_reply_that_used_the_whole_budget_is_retried_once_with_room(monkeypatch):
    calls = []

    async def fake(cfg, *, max_tokens, **kw):
        calls.append(max_tokens)
        if len(calls) == 1:
            return {"ok": False, "error": "empty_reply", "truncated": True, "reply": ""}, {}
        return {"ok": True, "reply": "RESULT: APPROVED"}, {}

    monkeypatch.setattr(llm_client, "_complete_with_cfg", fake)
    monkeypatch.setattr("daedalus.model_router.config_for_explicit_model", lambda base, model, source="": {})
    monkeypatch.setattr("daedalus.llm_config.get_config", lambda: {})
    out = asyncio.run(llm_client.complete(system="s", user="u", max_tokens=400))
    assert calls == [400, 1600] and out["reply"] == "RESULT: APPROVED" and out["retried_for_reasoning"] == 1600


def test_a_short_honest_empty_reply_is_not_retried(monkeypatch):
    calls = []

    async def fake(cfg, *, max_tokens, **kw):
        calls.append(max_tokens)
        return {"ok": False, "error": "empty_reply", "truncated": False, "reply": ""}, {}

    monkeypatch.setattr(llm_client, "_complete_with_cfg", fake)
    monkeypatch.setattr("daedalus.model_router.config_for_explicit_model", lambda base, model, source="": {})
    monkeypatch.setattr("daedalus.llm_config.get_config", lambda: {})
    asyncio.run(llm_client.complete(system="s", user="u", max_tokens=400))
    assert calls == [400]


def test_an_empty_code_review_accepts_on_the_build_checks():
    from daedalus.playground_stage_builder import StageBuildContext, build_stage

    reviews = []

    async def route(messages, model=None, intent="chat", **kw):
        if intent == "code":
            return {"content": [{"text": "===FILE: index.html===\n<!doctype html><canvas id=c></canvas>"
                                         "<script>let s=0;requestAnimationFrame(function f(){requestAnimationFrame(f)})"
                                         "</script>\n===END==="}], "finish_reason": "stop"}
        reviews.append(intent)
        return {"content": [{"text": ""}]}

    logs = []

    async def on_log(line, level="info"):
        logs.append(line)

    ctx = StageBuildContext(slug="x", kind="static", combined_sys="sys", build_model="m", description="d",
                            stage={"id": 1, "title": "Page", "goal": "page", "files": ["index.html"]},
                            stage_index=0, total=1, all_files={}, is_game=False, max_attempts=5)
    res = asyncio.run(build_stage(ctx, route_fn=route, on_log=on_log))
    assert res.ok is True and len(reviews) == 1 and "code review unavailable" in res.review_notes
    assert not any("Поправка" in line for line in logs)
