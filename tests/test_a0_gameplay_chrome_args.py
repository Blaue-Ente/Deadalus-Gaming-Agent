"""A0-k: the gameplay check launches Chromium with the same WebGL-safe flags as browser_verify."""
from __future__ import annotations

import asyncio
import sys
import types


def test_gameplay_verify_uses_shared_chrome_args(monkeypatch):
    from daedalus import playground_gameplay_verify as gv
    from daedalus.playground_browser import CHROME_ARGS

    seen = {}

    class FakeChromium:
        async def launch(self, **kwargs):
            seen.update(kwargs)
            raise RuntimeError("stop after launch")  # the function reports it; no browser needed

    class FakePW:
        chromium = FakeChromium()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(gv, "_find_chrome", lambda: "/fake/chrome")
    monkeypatch.setitem(sys.modules, "playwright.async_api", types.SimpleNamespace(async_playwright=lambda: FakePW()))
    out = asyncio.run(gv._run_gameplay_on_url("s", "http://127.0.0.1:1/", kind="vite", milestone_id="m0"))
    assert seen["args"] == list(CHROME_ARGS)
    assert "--disable-gpu" not in seen["args"]
    assert out["ok"] is False  # the stubbed launch failure is reported, not hidden
