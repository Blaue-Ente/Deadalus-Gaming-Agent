"""The quality critic judges the game during play, not its start screen (live 2026-11-12: Autopilot with Sonnet built a
working lantern maze and the critic rejected it as "a bare black start menu" from the first-paint screenshot)."""
from __future__ import annotations

import asyncio
import os
import time

from a0_support import a0_tmp  # noqa: F401


def _project(slug):
    from daedalus import playground as pg

    asyncio.run(pg.create_project(slug, kind="static"))
    root = pg.project_dir(slug)
    (root / "assets").mkdir(exist_ok=True)
    (root / "assets" / "_verify_screenshot.png").write_bytes(b"START-SCREEN")
    (root / "assets" / "_gameplay_screenshot.png").write_bytes(b"IN-PLAY")
    return root


def _critic(slug, root):
    from daedalus import playground_quality as pq

    seen = {}

    async def route(messages, **kw):
        seen["msgs"] = messages
        return {"text": '{"scores": {}, "overall": 9, "summary": "ok", "issues": [], "patch_targets": []}'}

    asyncio.run(pq.evaluate(slug=slug, kind="static", description="A maze game", plan_goals=[],
                    bv={"screenshot_path": "assets/_verify_screenshot.png"}, route_fn=route,
                    extract_text_fn=lambda r: r["text"], read_tree_fn=lambda s: [], is_sensitive_fn=lambda p: False,
                    project_dir=root, client_id="t"))
    user = seen["msgs"][-1]["content"]
    return user[0]["text"], user[1]["image_url"]["url"]


def test_the_critic_gets_the_play_frame(a0_tmp):  # noqa: F811
    import base64

    root = _project("maze")
    text, url = _critic("maze", root)
    assert base64.b64decode(url.split(",", 1)[1]) == b"IN-PLAY" and "DURING PLAY" in text


def test_a_play_frame_older_than_the_code_is_not_used(a0_tmp):  # noqa: F811
    import base64

    root = _project("maze2")
    old = time.time() - 60
    os.utime(root / "assets" / "_gameplay_screenshot.png", (old, old))
    (root / "game.js").write_text("changed")
    text, url = _critic("maze2", root)
    assert base64.b64decode(url.split(",", 1)[1]) == b"START-SCREEN" and "DURING PLAY" not in text
