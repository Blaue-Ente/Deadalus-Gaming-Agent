"""A0-c in the real Studio page: public publish and forced push need an explicit confirm."""
from __future__ import annotations

import json

from dx_confirm_support import ConfirmAnswerer
from test_studio_phase2_workspace import _fulfill, studio_server  # noqa: F401  (fixture)


def _bodies(calls):
    return [json.loads(c.post_data or "{}") for c in calls]


def test_live_publish_asks_before_public_backends(studio_server):
    from playwright.sync_api import sync_playwright

    game = studio_server["game"]
    calls = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 800})

        def live(route):
            calls.append(route.request)
            body = json.loads(route.request.post_data or "{}")
            if body.get("confirm_public"):
                _fulfill(route, 200, {"ok": True, "backend": "netlify_anon", "url": "https://tmp.example"})
            else:
                _fulfill(route, 200, {
                    "ok": True, "backend": "local", "url": None, "local_url": f"/apps/{game}/",
                    "needs_confirm": ["gitlab_pages", "netlify_anon"],
                })

        page.route("**/api/v1/playground/projects/" + game + "/live", live)
        page.goto(studio_server["url"] + f"/?panel=preview&slug={game}", wait_until="domcontentloaded")
        page.locator(f"#plist [data-slug='{game}']").wait_for()

        # Decline: exactly one request, no confirm flag, no public link claimed.
        answer = ConfirmAnswerer(page, accept=False)   # clicks #dx-confirm-cancel
        page.locator("#preview-live").click()
        page.wait_for_function(
            "() => document.getElementById('preview-status').textContent.includes('No public link')"
        )
        assert len(calls) == 1 and "confirm_public" not in _bodies(calls)[0]
        page.wait_for_function("() => window.__dxAnswer.seen.length === 1")
        assert answer.messages and "Anonymous Netlify" in answer.messages[0]

        # Accept: a second request carrying confirm_public, then the live link is reported.
        answer.set(True)                                # clicks #dx-confirm-ok
        page.locator("#preview-live").click()
        page.wait_for_function(
            "() => document.getElementById('preview-status').textContent === 'Live link is ready.'"
        )
        bodies = _bodies(calls)
        assert len(calls) == 3
        assert "confirm_public" not in bodies[1] and bodies[2].get("confirm_public") is True
        browser.close()


def test_git_push_force_only_after_confirm(studio_server):
    from playwright.sync_api import sync_playwright

    game = studio_server["game"]
    calls = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 800})

        def push(route):
            calls.append(route.request)
            body = json.loads(route.request.post_data or "{}")
            if body.get("force") is True:
                _fulfill(route, 200, {"ok": True, "provider": "github", "repo": "o/r"})
            else:
                _fulfill(route, 409, {
                    "ok": False, "error": "non_fast_forward",
                    "detail": "non_fast_forward: remote has commits", "needs_force": True,
                })

        page.route("**/admin/daedalus/playground/api/projects/" + game + "/push", push)
        page.goto(studio_server["url"] + f"/?panel=preview&slug={game}", wait_until="domcontentloaded")
        page.locator(f"#plist [data-slug='{game}']").wait_for()

        page.locator("#git-menu summary").click()
        answer = ConfirmAnswerer(page, accept=False)    # clicks #dx-confirm-cancel
        page.locator("#git-push").click()
        page.wait_for_function(
            "() => document.getElementById('git-status').textContent.includes('non_fast_forward')"
        )
        assert len(calls) == 1 and _bodies(calls)[0].get("force") is None

        answer.set(True)                                # clicks #dx-confirm-ok
        page.locator("#git-push").click()
        page.wait_for_function(
            "() => document.getElementById('git-status').textContent === 'Saved.'"
        )
        bodies = _bodies(calls)
        assert len(calls) == 3 and bodies[2].get("force") is True and bodies[1].get("force") is None
        browser.close()
