"""Answer the styled Studio confirm (`dx.confirm`, `#dx-confirm`) from a Playwright test.

The native `confirm()` dialogs are gone, so `page.on("dialog", ...)` never fires. This helper watches the
`#dx-confirm` dialog and answers it by clicking `#dx-confirm-ok` (accept) or `#dx-confirm-cancel` (dismiss),
exactly as a person would. Every question is recorded (title and body) so tests can still assert the text.
"""
from __future__ import annotations

import json

_SCRIPT = """
(() => {
  if (window.__dxAnswer) return;
  const st = window.__dxAnswer = { accept: %s, queue: %s, seen: [], titles: [] };
  const arm = () => {
    const dlg = document.getElementById("dx-confirm");
    if (!dlg) { document.addEventListener("DOMContentLoaded", arm, { once: true }); return; }
    const answer = () => {
      if (!dlg.open || dlg.dataset.answered === "1") return;
      dlg.dataset.answered = "1";
      st.seen.push(document.getElementById("dx-confirm-body").textContent);
      st.titles.push(document.getElementById("dx-confirm-title").textContent);
      const ok = st.queue.length ? st.queue.shift() : st.accept;
      dlg.dataset.answered = "";
      document.getElementById(ok ? "dx-confirm-ok" : "dx-confirm-cancel").click();   // as a person would
    };
    new MutationObserver(answer).observe(dlg, { attributes: true, attributeFilter: ["open"] });
    answer();
  };
  arm();
})();
"""


class ConfirmAnswerer:
    def __init__(self, page, accept: bool = True, decisions=None):
        """`decisions` is an optional per-question queue of True/False; `accept` answers once it runs out."""
        self.page = page
        script = _SCRIPT % ("true" if accept else "false", json.dumps([bool(d) for d in (decisions or [])]))
        page.add_init_script(script)
        try:
            page.evaluate(script)
        except Exception:   # not on a document yet: the init script covers the next navigation
            pass

    def set(self, accept: bool) -> "ConfirmAnswerer":
        """Answer the next questions with OK (True) or Cancel (False)."""
        self.page.evaluate("(a) => { if (window.__dxAnswer) window.__dxAnswer.accept = a; }", bool(accept))
        return self

    @property
    def messages(self) -> list:
        return self.page.evaluate("() => (window.__dxAnswer ? window.__dxAnswer.seen.slice() : [])")

    @property
    def titles(self) -> list:
        return self.page.evaluate("() => (window.__dxAnswer ? window.__dxAnswer.titles.slice() : [])")


class AskedList:
    """List-like view of the questions asked so far (read live from the page)."""

    def __init__(self, answerer: ConfirmAnswerer):
        self._a = answerer

    def __len__(self):
        return len(self._a.messages)

    def __getitem__(self, i):
        return self._a.messages[i]

    def __iter__(self):
        return iter(self._a.messages)

    def __eq__(self, other):
        return self._a.messages == other

    def clear(self):
        self._a.page.evaluate("() => { if (window.__dxAnswer) { window.__dxAnswer.seen.length = 0; window.__dxAnswer.titles.length = 0; } }")

    def __repr__(self):
        return repr(self._a.messages)


def answer_confirms(page, decisions=None, accept: bool = True) -> AskedList:
    """Install an answerer and return the live list of questions it saw."""
    return AskedList(ConfirmAnswerer(page, accept=accept, decisions=decisions))


class FlagDict(dict):
    """`accept = FlagDict(on=False)`; `accept["on"] = True` flips the answer the page gets (OK vs Cancel)."""

    _answerer = None

    def bind(self, page) -> AskedList:
        self._answerer = ConfirmAnswerer(page, accept=bool(self["on"]))
        return AskedList(self._answerer)

    def __setitem__(self, key, value):
        super().__setitem__(key, value)
        if key == "on" and self._answerer is not None:
            self._answerer.set(bool(value))
