"""A late background refresh must never overwrite the result of something the owner just did.

Root cause of the intermittent Studio browser tests: selecting a project (and entering the Preview panel) starts a
``/logs`` request whose answer used to be written into ``#preview-status`` unconditionally. When it answered after a
click's result was already shown, it replaced "preview down" / "Live link is ready." with "No preview yet", and a
test (or a person) waiting for the real result never saw it. Slow machines (GitHub runners) make the late answer
likely; this test makes it certain by holding the response.
"""
from __future__ import annotations

from test_studio_phase2_workspace import _fulfill, studio_server  # noqa: F401  (fixture)


def _status(page):
    return page.evaluate("() => document.getElementById('preview-status').textContent")


def test_late_logs_answer_does_not_overwrite_an_action_result(studio_server):
    """Only a refresh that BEGAN before the action's result is held back; one the page starts afterwards is newer
    information and may update the line. The page records when each /logs fetch began so the test can tell them apart."""
    from playwright.sync_api import sync_playwright

    game = studio_server["game"]
    held = {}                                   # fetch sequence number -> its held route
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        page.add_init_script("""
          window.__logStart = {}; window.__resultAt = 0;
          let n = 0; const realFetch = window.fetch;
          window.fetch = function (input, init) {
            const url = typeof input === 'string' ? input : input.url;
            if (url.split('?')[0].endsWith('/logs')) {
              n += 1; window.__logStart[n] = performance.now();
              init = Object.assign({}, init, {headers: Object.assign({}, (init || {}).headers, {'x-seq': String(n)})});
            }
            return realFetch.call(this, input, init);
          };
          document.addEventListener('DOMContentLoaded', () => {
            const el = document.getElementById('preview-status');
            new MutationObserver(() => {
              if (!window.__resultAt && el.textContent.includes('preview down')) window.__resultAt = performance.now();
            }).observe(el, {childList: true, characterData: true, subtree: true});
          });
        """)

        def logs(route):
            held[int(route.request.headers.get("x-seq", "0"))] = route

        page.route("**/admin/daedalus/playground/api/projects/" + game + "/logs", logs)
        page.route("**/admin/daedalus/playground/api/projects/" + game + "/run",
                   lambda route: _fulfill(route, 500, {"detail": "preview down"}))
        page.goto(studio_server["url"] + f"/?panel=preview&slug={game}", wait_until="domcontentloaded")
        page.locator(f"#plist [data-slug='{game}']").wait_for()
        page.locator("#preview-logs").click()    # a slow refresh is certainly in flight now
        for _ in range(100):
            if held:
                break
            page.wait_for_timeout(50)
        assert held, "the page never asked for the preview logs, so the race cannot be reproduced"
        page.locator("#preview-run").click()
        page.wait_for_function("() => window.__resultAt > 0")
        starts = page.evaluate("() => window.__logStart")
        result_at = page.evaluate("() => window.__resultAt")
        early = [seq for seq in held if seq and starts.get(str(seq), 1e18) < result_at]
        assert early, (starts, result_at)
        for seq in early:                        # their slow answers finally arrive, AFTER the action's result
            _fulfill(held[seq], 200, {"status": None})
        page.wait_for_timeout(800)
        assert "preview down" in _status(page), _status(page)
        browser.close()


def test_build_buttons_are_not_greyed_out_again_after_the_workspace_loaded(studio_server):
    """Startup asks for the workspace twice. The second ask used to disable the buttons the first had just enabled,
    so a click in that gap was lost with no request and no message (the trace of the intermittent CI failures)."""
    from playwright.sync_api import sync_playwright

    game = studio_server["game"]
    workspace_gets = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        page.add_init_script("""
          window.__flips = [];
          document.addEventListener('DOMContentLoaded', () => {
            const b = document.getElementById('build-guided');
            let was = b.disabled;
            new MutationObserver(() => {
              if (b.disabled !== was) { window.__flips.push(b.disabled ? 'disabled' : 'enabled'); was = b.disabled; }
            }).observe(b, {attributes: true, attributeFilter: ['disabled']});
          });
        """)

        def workspace(route):
            path = route.request.url.split("?", 1)[0].rstrip("/")
            if route.request.method != "GET" or not path.endswith("/game-studio/api/projects/" + game):
                route.fallback()
                return
            workspace_gets.append(path)
            _fulfill(route, 200, {"ok": True, "can_guided": True, "can_autopilot": False, "can_advance": False,
                                  "milestone_summary": {"milestones": []}})

        page.route(f"**/game-studio/api/projects/{game}", workspace)
        page.goto(studio_server["url"] + f"/?panel=build&slug={game}", wait_until="domcontentloaded")
        page.wait_for_function("() => !document.getElementById('build-guided').disabled")
        page.wait_for_timeout(1500)                     # long enough for any second load to start and answer
        flips = page.evaluate("() => window.__flips")
        assert flips[:1] == ["enabled"] and "disabled" not in flips[1:], flips      # enabled once, never greyed again
        assert len(workspace_gets) == 1, workspace_gets       # the startup asks were coalesced into one request
        browser.close()


def test_a_click_right_after_the_buttons_enable_always_sends_its_request(studio_server):
    from playwright.sync_api import sync_playwright

    game = studio_server["game"]
    posts = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 800})

        def workspace(route):
            path = route.request.url.split("?", 1)[0].rstrip("/")
            if route.request.method != "GET" or not path.endswith("/game-studio/api/projects/" + game):
                route.fallback()
                return
            _fulfill(route, 200, {"ok": True, "can_guided": True, "can_autopilot": False, "can_advance": False,
                                  "milestone_summary": {"milestones": []}})

        def guided(route):
            posts.append(route.request.post_data)
            _fulfill(route, 200, {"ok": True, "harness_check": True})

        page.route(f"**/game-studio/api/projects/{game}", workspace)
        page.route(f"**/game-studio/api/projects/{game}/build/guided", guided)
        page.goto(studio_server["url"] + f"/?panel=build&slug={game}", wait_until="domcontentloaded")
        page.locator("#build-guided").click()           # Playwright clicks the instant the button is actionable
        page.wait_for_function("() => document.getElementById('build-status').textContent.length > 0")
        assert len(posts) == 1
        browser.close()


def test_startup_resumes_the_build_once(studio_server):
    """Two resume passes made the second supersede the first: its answer was dropped and the stop button never showed."""
    from playwright.sync_api import sync_playwright

    game = studio_server["game"]
    actives = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        page.route(f"**/playground/api/projects/{game}/guided/active",
                   lambda route: (actives.append(route.request.url), _fulfill(route, 200, {"active": False}))[1])
        page.goto(studio_server["url"] + f"/?panel=build&slug={game}", wait_until="domcontentloaded")
        page.wait_for_function("() => !document.getElementById('build-guided').disabled || true")
        page.wait_for_timeout(2000)
        assert len(actives) == 1, actives
        browser.close()
