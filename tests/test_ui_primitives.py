"""PR3 UI primitives: styled confirm, readable errors, empty states."""
from __future__ import annotations

import json
import re
from pathlib import Path

from test_studio_phase1_header import studio_server  # noqa: F401  (fixture)

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "app" / "static"
I18N = (STATIC / "i18n.js").read_text(encoding="utf-8")

NATIVE_CONFIRM = re.compile(r"(?<![\w.])confirm\(|window\.confirm\(")
NEW_KEYS = [
    "studio.confirmOk", "studio.confirmCancel",
    "studio.err.noModel", "studio.err.tryAgain", "studio.err.key", "studio.err.generic",
    "studio.emptyProposals", "studio.emptySkills", "studio.emptyBundles", "studio.emptyOutbox",
    "studio.emptyPermissions", "studio.emptyFacts", "studio.emptyTasks", "studio.emptySessions",
    "studio.emptyAppData",
]


def _code_only(src: str) -> str:
    """Drop // comments so prose mentioning confirm( does not count."""
    return "\n".join(re.sub(r"(^|\s)//.*$", "", line) for line in src.split("\n"))


def test_no_native_confirm_left_in_static_scripts():
    hits = []
    for path in sorted(STATIC.glob("*.js")):
        for n, line in enumerate(_code_only(path.read_text(encoding="utf-8")).split("\n"), 1):
            if path.name == "i18n.js":
                continue
            if "function confirmDialog" in line:
                continue
            if NATIVE_CONFIRM.search(line):
                hits.append(f"{path.name}:{n}: {line.strip()[:100]}")
    assert hits == [], hits


def _block(lang: str) -> str:
    # The first block that holds studio.confirmOk for the language.
    marker = {"en": '"studio.emptyList": "Nothing here."', "bg": '"studio.emptyList": "Тук няма нищо."'}[lang]
    start = I18N.index(marker)
    return I18N[start:start + 40000]


def test_new_keys_exist_in_en_and_bg():
    names = set(re.findall(r'ask(?:Danger)?\("(\w+)"', (STATIC / "studio-workspace.js").read_text(encoding="utf-8")
                           + (STATIC / "studio.js").read_text(encoding="utf-8")))
    assert len(names) >= 50
    for lang in ("en", "bg"):
        block = _block(lang)
        for key in NEW_KEYS + ["studio.cf." + n for n in sorted(names)]:
            assert f'"{key}":' in block, (lang, key)
    assert '"studio.confirmOk": "Continue"' in I18N and '"studio.confirmCancel": "Cancel"' in I18N
    assert '"studio.confirmOk": "Продължи"' in I18N and '"studio.confirmCancel": "Отказ"' in I18N
    assert '"studio.err.noModel": "No model is set up yet. Open Settings to choose one."' in I18N
    assert '"studio.err.generic": "Нещо се обърка."' in I18N


def test_dialog_markup_present():
    html = (STATIC / "studio.html").read_text(encoding="utf-8")
    for needle in ('id="dx-confirm"', 'id="dx-confirm-title"', 'id="dx-confirm-body"',
                   'id="dx-confirm-ok"', 'id="dx-confirm-cancel"'):
        assert needle in html


def test_confirm_resolves_true_and_false(studio_server):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 780, "height": 340})
        page.goto(studio_server["url"] + "/", wait_until="domcontentloaded")
        page.wait_for_function("() => !!(window.dxStudio && window.dxStudio.confirm)")

        page.evaluate("() => { window.__p = window.dxStudio.confirm({ title: 't', body: 'b' }); }")
        page.locator("#dx-confirm").wait_for(state="visible")
        assert page.locator("#dx-confirm-title").inner_text() == "t"
        assert page.locator("#dx-confirm-body").inner_text() == "b"
        assert page.locator("#dx-confirm-ok").inner_text() == "Continue"
        assert page.evaluate("() => document.activeElement.id") == "dx-confirm-ok"
        page.locator("#dx-confirm-ok").click()
        assert page.evaluate("() => window.__p") is True
        assert not page.locator("#dx-confirm").is_visible()

        page.evaluate("() => { window.__p = window.dxStudio.confirm({ title: 't', body: 'b', danger: true }); }")
        page.locator("#dx-confirm").wait_for(state="visible")
        assert page.evaluate("() => document.activeElement.id") == "dx-confirm-cancel"
        box = page.locator("#dx-confirm").bounding_box()
        assert box and box["x"] >= 0 and box["y"] >= 0 and box["x"] + box["width"] <= 780 and box["y"] + box["height"] <= 340
        page.locator("#dx-confirm-cancel").click()
        assert page.evaluate("() => window.__p") is False

        page.evaluate("() => { window.__p = window.dxStudio.confirm({ body: 'b' }); }")
        page.locator("#dx-confirm").wait_for(state="visible")
        page.keyboard.press("Escape")
        assert page.evaluate("() => window.__p") is False
        browser.close()


def test_human_error_and_empty_state(studio_server):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page()
        page.goto(studio_server["url"] + "/", wait_until="domcontentloaded")
        page.wait_for_function("() => !!(window.dxStudio && window.dxStudio.humanError)")
        got = page.evaluate("""() => {
          const h = window.dxStudio.humanError;
          return {
            llm: h(new Error('llm_failed')), mentor: h('no_mentor_model'),
            prov: h(new Error('No LLM provider configured: set a key')),
            syn: h('synthesis_failed'), search: h('search_failed'),
            key: h(new Error('HTTP 429')), credit: h('insufficient credits'),
            gen: h(new Error('boom')), raw: h(new Error('boom'), { raw: true }),
            det: h('llm_failed', { details: true }),
          };
        }""")
        assert got["llm"] == got["mentor"] == got["prov"] == "No model is set up yet. Open Settings to choose one."
        assert got["syn"] == got["search"] == "That did not work this time. Try again."
        assert got["key"] == got["credit"] == "The model provider rejected the key or is out of credits."
        assert got["gen"] == "Something went wrong." and got["raw"] == "boom"
        assert got["det"] == {"text": "No model is set up yet. Open Settings to choose one.", "details": "llm_failed"}
        page.evaluate("""() => {
          const el = document.createElement('div'); el.id = 'probe'; document.body.appendChild(el);
          window.__clicked = 0;
          window.dxStudio.emptyState(el, { text: 'Nothing yet.', actionLabel: 'Add', onAction: () => { window.__clicked += 1; } });
        }""")
        assert page.locator("#probe p").inner_text() == "Nothing yet."
        page.locator("#probe button").click()
        assert page.evaluate("() => window.__clicked") == 1
        browser.close()
