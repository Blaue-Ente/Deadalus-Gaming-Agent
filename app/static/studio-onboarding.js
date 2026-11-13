// First steps (owner, 2026-11-13): how to start Daedalus, what this computer has and needs, which keys matter, the
// SubQ switch, and what Daedalus can do. Opens by itself until the owner closes it once; "Setup" in the header
// opens it again. The server sends ids and facts; the words are in i18n.js (onb.*), English and Bulgarian.
(() => {
  const dx = window.dxStudio;
  const dlg = document.getElementById("onb-dlg");
  if (!dx || !dlg) return;
  const $ = (id) => document.getElementById(id);
  let last = null;
  let seq = 0;

  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }

  function code(text) {
    const c = el("code", "onb-code", text);
    return c;
  }

  function section(titleKey) {
    const s = el("section", "onb-section");
    s.appendChild(el("h3", "onb-h", dx.t(titleKey)));
    return s;
  }

  function paintStart(rep) {
    const s = section("onb.start.title");
    const p = el("p", "onb-p");
    p.append(dx.t("onb.start.before"), " ", code(rep.start.command), " ", dx.t("onb.start.open"), " ",
      code(rep.start.url));
    s.appendChild(p);
    const q = el("p", "onb-p onb-muted");
    q.append(dx.t("onb.start.windows"), " ", dx.t("onb.start.check"), " ", code(rep.start.check));
    s.appendChild(q);
    return s;
  }

  function paintItems(rep) {
    const s = section("onb.check.title");
    const verdict = el("p", "onb-verdict", dx.t(rep.ready ? "onb.ready" : "onb.notReady"));
    verdict.dataset.ok = String(!!rep.ready);
    s.appendChild(verdict);
    s.appendChild(el("p", "onb-p onb-muted", rep.platform));
    const list = el("ul", "onb-list");
    rep.items.forEach((it) => {
      const li = el("li", "onb-item");
      li.dataset.ok = String(!!it.ok);
      li.dataset.level = it.level;
      const head = el("div", "onb-item-head");
      head.appendChild(el("span", "onb-mark", it.ok ? "✓" : (it.level === "required" ? "✗" : "–")));
      head.appendChild(el("strong", "", dx.t("onb.item." + it.id)));
      head.appendChild(el("span", "onb-level", dx.t("onb.level." + it.level)));
      if (it.detail) head.appendChild(el("span", "onb-detail", it.detail));
      li.appendChild(head);
      li.appendChild(el("p", "onb-why", dx.t("onb.why." + it.id)));
      if (it.id === "python" && it.outdated) {
        li.appendChild(el("p", "onb-why", dx.t("onb.pythonOld", { rec: it.recommended })));
      }
      if (it.id === "brain" && it.active) li.appendChild(el("p", "onb-why", dx.t("onb.brainActive", { model: it.active })));
      if (it.id === "brain" && it.weak) li.appendChild(el("p", "onb-needs", dx.t("onb.brainWeak")));
      if (it.id === "brain" && it.local && it.reachable === false) li.appendChild(el("p", "onb-needs", dx.t("onb.brainDown")));
      if (it.id === "ollama" && it.ok && !it.running) li.appendChild(el("p", "onb-why", dx.t("onb.ollamaStopped")));
      if (!it.ok && it.install) {
        const how = el("p", "onb-how");
        const prose = dx.t("onb.how." + it.id);              // steps in words (translated) instead of a command
        how.append(dx.t("onb.install"), " ", prose !== "onb.how." + it.id ? prose : code(it.install));
        if (it.after) how.append(" ", dx.t("onb.then"), " ", code(it.after));
        li.appendChild(how);
        if (it.url) {
          const a = el("a", "onb-link", it.url);
          a.href = it.url;
          a.target = "_blank";
          a.rel = "noopener";
          li.appendChild(a);
        }
      }
      list.appendChild(li);
    });
    s.appendChild(list);
    return s;
  }

  function paintKeys(rep) {
    const s = section("onb.keys.title");
    s.appendChild(el("p", "onb-p onb-muted", dx.t("onb.keys.hint")));
    const list = el("ul", "onb-keys");
    rep.keys.forEach((k) => {
      const li = el("li", "onb-key");
      li.dataset.ok = String(!!k.set);
      li.appendChild(code(k.name));
      li.appendChild(el("span", "onb-key-use", dx.t("onb.use." + k.use)));
      li.appendChild(el("span", "onb-key-state", dx.t(k.set ? "onb.key.set" : "onb.key.missing")));
      list.appendChild(li);
    });
    s.appendChild(list);
    return s;
  }

  function paintSubq(rep) {
    const s = section("onb.subq.title");
    s.appendChild(el("p", "onb-p", dx.t("onb.subq.text")));
    const row = el("label", "onb-switch");
    const box = el("input");
    box.type = "checkbox";
    box.id = "onb-subq";
    box.checked = !!rep.subq.on;
    box.disabled = !!rep.subq.env_locked;
    row.append(box, " ", dx.t("onb.subq.label"));
    s.appendChild(row);
    const note = el("p", "onb-p onb-muted");
    note.id = "onb-subq-note";
    if (rep.subq.env_locked) note.textContent = dx.t("onb.subq.locked", { env: rep.subq.env });
    else if (!rep.subq.has_key) note.textContent = dx.t("onb.subq.noKey");
    else note.textContent = dx.t(rep.subq.on ? "onb.subq.active" : "onb.subq.paused");
    s.appendChild(note);
    Object.entries(rep.subq.stats || {}).forEach(([q, v]) => {
      s.appendChild(el("p", "onb-p onb-muted", dx.t("onb.subq.stats",
        { question: dx.t("onb.subq.q." + q), agree: v.agree, judged: v.judged })));
    });
    box.addEventListener("change", async () => {
      box.disabled = true;
      try {
        await dx.api("/api/v1/harness/config", { method: "POST",
          body: JSON.stringify({ key: "subq.shadow", value: box.checked }) });
        note.textContent = !rep.subq.has_key ? dx.t("onb.subq.noKey") :
          dx.t(box.checked ? "onb.subq.active" : "onb.subq.paused");
        rep.subq.on = box.checked;
      } catch (err) {
        box.checked = !box.checked;
        note.textContent = dx.t("onb.subq.failed") + " " + ((err && err.message) || "");
      } finally {
        box.disabled = !!rep.subq.env_locked;
      }
    });
    return s;
  }

  function paintAbilities(rep) {
    const s = section("onb.abilities.title");
    const list = el("ul", "onb-list");
    rep.abilities.forEach((a) => {
      const li = el("li", "onb-item");
      li.dataset.ok = String(!!a.ready);
      const head = el("div", "onb-item-head");
      head.appendChild(el("span", "onb-mark", a.ready ? "✓" : "–"));
      head.appendChild(el("strong", "", dx.t("onb.ab." + a.id)));
      li.appendChild(head);
      li.appendChild(el("p", "onb-why", dx.t("onb.abd." + a.id)));
      if (a.needs.length) {
        const names = a.needs.map((n) => n.startsWith("key:")
          ? (n === "key:image" ? dx.t("onb.needImageKey") : n.slice(4)) : dx.t("onb.item." + n));
        li.appendChild(el("p", "onb-needs", dx.t("onb.needs", { what: names.join(", ") })));
      }
      list.appendChild(li);
    });
    s.appendChild(list);
    return s;
  }

  function paint(rep) {
    const body = $("onb-body");
    body.replaceChildren(paintStart(rep), paintItems(rep), paintKeys(rep), paintSubq(rep), paintAbilities(rep));
  }

  async function load() {
    const mine = ++seq;
    const body = $("onb-body");
    body.replaceChildren(el("p", "onb-p onb-muted", dx.t("onb.checking")));
    try {
      const rep = await dx.api("/api/v1/onboarding");
      if (mine !== seq) return null;
      last = rep;
      paint(rep);
      return rep;
    } catch (err) {
      if (mine !== seq) return null;
      body.replaceChildren(el("p", "onb-p onb-err", dx.t("onb.failed") + " " + ((err && err.message) || "")));
      return null;
    }
  }

  function open() {
    if (!dlg.open) {
      if (typeof dlg.showModal === "function") dlg.showModal();
      else dlg.setAttribute("open", "");
    }
    return load();
  }

  async function close() {
    if (typeof dlg.close === "function") dlg.close();
    else dlg.removeAttribute("open");
    if (last && !last.seen) {
      try {
        await dx.api("/api/v1/onboarding/seen", { method: "POST", body: "{}" });
        last.seen = true;
      } catch (err) { /* it opens again next time: nothing lost */ }
    }
  }

  $("onb-link").addEventListener("click", open);
  $("onb-recheck").addEventListener("click", load);
  $("onb-close").addEventListener("click", close);
  dlg.addEventListener("cancel", (ev) => { ev.preventDefault(); close(); });
  document.addEventListener("daedalus:langchange", () => { if (last && dlg.open) paint(last); });

  // First run: open by itself for the owner only (a team member gets 403 and sees nothing).
  (async () => {
    try {
      const me = await dx.api("/api/v1/team/me").catch(() => null);
      if (me && me.role && me.role !== "owner") return;
      const state = await dx.api("/api/v1/onboarding?quick=1");
      if (!state.seen && state.auto_open) open();
    } catch (err) { /* not signed in yet, or not the owner: the Setup button still works */ }
  })();
  window.dxOnboarding = { open, close, load };
})();
