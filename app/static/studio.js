(() => {
  const $ = (id) => document.getElementById(id);
  const state = {
    slug: localStorage.getItem("dx.slug") || null,
    session: localStorage.getItem("dx.session") || ("studio-" + Math.random().toString(16).slice(2, 10)),
    effort: localStorage.getItem("dx.effort") || "auto",
    projects: [],
    lastCwd: "",
    chatJob: null,
    chatSource: null,
    skillIndex: [],
    pinSkills: [],
    pinMcp: [],
    turnSkills: [],
    turnMcp: [],
    attachments: [],
    ctxPane: "",
    addons: [],
    kindFilter: "all",
    gameSlugs: null,
    gameIndex: "pending",
    projectsReady: false,
  };
  const slugWatchers = [];
  const APP_KINDS = { fastapi: 1, streamlit: 1, node: 1, expo: 1 };
  localStorage.setItem("dx.session", state.session);

  const msgs = $("msgs");

  function t(key, vars) {
    let s = (window.DaedalusI18n && DaedalusI18n.t) ? DaedalusI18n.t(key, vars) : key;
    if (vars && typeof s === "string") {
      Object.keys(vars).forEach((k) => {
        s = s.split("{" + k + "}").join(String(vars[k] ?? ""));
      });
    }
    return s;
  }
  if (window.DaedalusI18n && DaedalusI18n.apply) DaedalusI18n.apply();

  function text(node, value) {
    node.textContent = value == null ? "" : String(value);
  }

  function errText(err) {
    if (!err) return t("studio.unknownError");
    if (typeof err === "string") return err;
    return err.message || String(err);
  }

  function busy(btn, on, label) {
    if (!btn) return;
    if (on) {
      if (!btn.dataset.idle) btn.dataset.idle = btn.textContent;
      btn.disabled = true;
      if (label) btn.textContent = label;
    } else {
      btn.disabled = false;
      btn.textContent = btn.dataset.idle || label || btn.textContent;
    }
  }

  async function api(path, opts) {
    const options = opts ? Object.assign({}, opts) : {};
    const timeoutMs = options.timeoutMs == null ? 45000 : options.timeoutMs;
    delete options.timeoutMs;
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), timeoutMs);
    options.signal = ctrl.signal;
    const headers = new Headers(options.headers || {});
    if (options.body && !headers.has("Content-Type")) {
      headers.set("Content-Type", "application/json");
    }
    options.headers = headers;
    try {
      const res = await fetch(path, options);
      const raw = await res.text();
      let data = {};
      if (raw) {
        try { data = JSON.parse(raw); }
        catch { data = { detail: raw.slice(0, 400) }; }
      }
      if (!res.ok) {
        if (res.status === 403 && data.error === "role_forbidden") {     // gap 3: say it in words, not a code
          throw new Error(t("studio.roleForbidden", { role: t("studio.role." + data.role), needs: t("studio.role." + data.needs) }));
        }
        const msg = data.detail || data.error || ("HTTP " + res.status);
        throw new Error(typeof msg === "string" ? msg : JSON.stringify(msg));
      }
      return data;
    } catch (e) {
      if (e && e.name === "AbortError") {
        throw new Error(t("studio.timeout"));
      }
      throw e;
    } finally {
      clearTimeout(timer);
    }
  }

  function itemEl(title, sub, active) {
    const el = document.createElement("div");
    el.className = "item" + (active ? " active" : "");
    const b = document.createElement("b");
    text(b, title);
    const s = document.createElement("small");
    text(s, sub);
    el.append(b, s);
    return el;
  }

  function add(role, body, meta) {
    const d = document.createElement("div");
    d.className = "msg " + role;
    text(d, body);
    if (meta) {
      const m = document.createElement("span");
      m.className = "meta";
      text(m, meta);
      d.appendChild(m);
    }
    msgs.appendChild(d);
    msgs.scrollTop = msgs.scrollHeight;
    return d;
  }

  // The live line, in the owner's language: a known phase by its key, a tool by its name, plus how long it has run.
  // (The server's own label is a fallback for old clients; its previews are not shown.)
  function liveLabel(d) {
    const key = "live." + (d.tool || "");
    const known = t(key);
    const base = d.tool ? (known !== key ? known : "⚙️ " + d.tool) : t("studio.thinking");
    return d.elapsed > 3 ? base + " · " + d.elapsed + "s" : base;
  }

  function setLiveLabel(node, label) {
    let meta = node.querySelector(".meta");
    if (!meta) {
      meta = document.createElement("span");
      meta.className = "meta";
      node.appendChild(meta);
    }
    text(meta, label);
  }

  // What Daedalus is doing while a turn runs: a timer and one line per step (tools, model calls, the Mentor's plan,
  // review and what it saw), streamed from the Run ledger. Never a silent wait.
  function activityPanel(after) {
    const wrap = document.createElement("div");
    wrap.className = "activity";
    const det = document.createElement("details");
    det.open = true;
    const sum = document.createElement("summary");
    const list = document.createElement("ol");
    det.append(sum, list);
    wrap.appendChild(det);
    after.after(wrap);
    const started = Date.now();
    let steps = 0;
    let lastRow = Date.now();
    let ended = null;
    const clock = (ms) => {
      const s = Math.max(0, Math.round(ms / 1000));
      return Math.floor(s / 60) + ":" + String(s % 60).padStart(2, "0");
    };
    const paint = () => {
      const bits = [t("act.working"), clock(Date.now() - started)];
      if (steps) bits.push(t("act.steps", { n: steps }));
      if (Date.now() - lastRow > 25000) bits.push(t("act.quiet"));
      text(sum, bits.join(" · "));
    };
    const timer = setInterval(paint, 1000);
    paint();
    const line = (icon, cls, label, at, extra) => {
      const li = document.createElement("li");
      if (cls) li.className = cls;
      const i = document.createElement("span");
      i.className = "i";
      text(i, icon);
      const l = document.createElement("span");
      text(l, label);
      const tt = document.createElement("span");
      tt.className = "t";
      text(tt, at);
      li.append(i, l, tt);
      if (extra) li.appendChild(extra);
      list.appendChild(li);
      steps += 1;
      lastRow = Date.now();
    };
    const bullets = (head, items) => {
      const x = document.createElement("div");
      x.className = "x";
      if (head) x.appendChild(document.createTextNode(head));
      if (items && items.length) {
        const ul = document.createElement("ul");
        items.forEach((it) => { const li = document.createElement("li"); text(li, it); ul.appendChild(li); });
        x.appendChild(ul);
      }
      return x;
    };
    const secs = (ms) => (ms == null ? "" : (ms >= 1000 ? (ms / 1000).toFixed(1) + "s" : ms + "ms"));
    let lastSeq = 0;
    function addRows(rows) {
      (rows || []).forEach((r) => {
        // A reconnecting stream sends the history again from the start: show each ledger row once.
        if (r.seq != null) {
          if (r.seq <= lastSeq) return;
          lastSeq = r.seq;
        }
        const at = r.ts ? clock(r.ts * 1000 - started) : "";
        if (r.type === "tool") {
          const bad = r.blocked ? t("act.refused") : (r.ok ? "" : t("act.failed"));
          const label = [r.tool, r.target].filter(Boolean).join(" · ") + (bad ? " — " + bad : "");
          line(r.ok ? "✓" : (r.blocked ? "⊘" : "✗"), r.ok ? "good" : "bad", label, secs(r.ms) || at,
            !r.ok && r.note ? bullets(r.note) : null);
        } else if (r.type === "llm") {
          line("·", "dim", [t("act.llm"), r.intent, r.model].filter(Boolean).join(" · ")
            + (r.error ? " — " + r.error : ""), secs(r.ms) || at);
        } else if (r.type === "mentor_plan") {
          if (r.skipped) line("🧭", "dim", t("act.mentorPlanSkipped", { why: r.skipped }), at);
          else line("🧭", "good", t("act.mentorPlan") + (r.model ? " · " + r.model : ""), at,
            bullets(r.brief, r.checklist));
        } else if (r.type === "mentor_review") {
          if (r.skipped) { line("🧭", "dim", t("act.mentorReviewSkipped", { why: r.skipped }), at); return; }
          const verdict = r.accept ? t("act.mentorAccepted") : t("act.mentorMissing", { n: (r.missing || []).length });
          const extra = (r.missing || []).concat((r.unverified || []).map((u) => t("act.unverified") + ": " + u));
          line("🧭", r.accept ? "good" : "bad", t("act.mentorReview") + ": " + verdict
            + (r.saw_frames ? " · " + t("act.mentorSaw", { n: r.saw_frames }) : ""), at,
            extra.length ? bullets("", extra) : null);
        } else if (r.type === "mentor_perception") {
          line("👁", "", t("act.mentorSaw", { n: r.frames || 0 }) + (r.next_tool ? " → " + r.next_tool : ""), at,
            bullets(r.reason, r.observations));
        } else if (r.type === "self_review") {
          line("⚑", "bad", t("act.selfReview"), at, bullets(r.issue, r.fix ? [r.fix] : []));
        } else if (r.type === "playtest") {
          const how = r.mode === "played" ? t("act.playtestPlayed", { n: r.frames || 0 })
            : r.mode === "source" ? t("act.playtestSource") : t("act.playtestHeuristic");
          line("🎮", r.players && r.would_quit * 2 >= r.players ? "bad" : "good",
            t("act.playtest", { n: r.players || 0, q: r.would_quit || 0 }) + " · " + how, at,
            bullets("", (r.quotes || []).concat((r.fixes || []).map((f) => "→ " + f))));
        } else if (r.type === "project_tests") {
          const what = r.ran ? t("act.testsRan", { p: r.passed || 0, f: r.failed || 0 }) : (r.note || t("act.testsNone"));
          const regs = (r.regressions || []).length;
          line("🧪", r.ok ? "good" : "bad", t("act.tests") + ": " + what
            + (r.baseline ? " · " + t("act.testsRegressions", { n: regs }) : ""), at,
            bullets("", (r.failures || []).concat(r.regressions || [])));
        } else if (r.type === "truncated") {
          line("…", "dim", t("act.truncated"), at);
        }
      });
    }
    function finish(status) {
      if (ended) return;
      ended = Date.now();
      clearInterval(timer);
      wrap.classList.add("idle");
      const bits = [status === "done" ? t("act.done") : t("act.stopped"), clock(ended - started)];
      if (steps) bits.push(t("act.steps", { n: steps }));
      text(sum, bits.join(" · "));
      if (!steps) wrap.remove();
      else det.open = false;
    }
    return { addRows, finish };
  }

  function setEffort(next) {
    state.effort = next;
    localStorage.setItem("dx.effort", next);
    document.querySelectorAll(".effort button").forEach((btn) => {
      btn.setAttribute("aria-pressed", btn.dataset.effort === next ? "true" : "false");
    });
    previewEffort();
  }

  const headerErrors = {};
  function renderHeaderErrors() {
    const el = $("header-status");
    if (!el) return;
    const lines = Object.keys(headerErrors).map((key) => headerErrors[key]);
    el.hidden = lines.length === 0;
    text(el, lines.join("\n"));
  }
  function reportHeaderError(key, label, err) {
    headerErrors[key] = label + ": " + errText(err);
    renderHeaderErrors();
  }
  function clearHeaderError(key) {
    delete headerErrors[key];
    renderHeaderErrors();
  }

  function paintMentor(data) {
    clearHeaderError("mentor");
    const on = !!(data && data.enabled);
    const locked = !!(data && data.env_locked);
    document.querySelectorAll(".mentor button").forEach((btn) => {
      const wantOn = btn.dataset.mentor === "on";
      btn.setAttribute("aria-pressed", (wantOn ? on : !on) ? "true" : "false");
      btn.disabled = locked;
    });
    const group = document.querySelector(".mentor");
    if (group) {
      group.title = locked ? t("studio.mentorLocked") : t("studio.mentorHint");
    }
    const llm = (data && data.llm) || {};
    const catalog = Array.isArray(llm.catalog) ? llm.catalog : [];
    const sel = $("mentor-choice");
    if (sel) {
      const choice = llm.choice || {};
      const cur = String(choice.id || "auto");
      const groups = { recommended: [], also: [], choice: [] };
      catalog.forEach((row) => {
        const g = row.group && groups[row.group] ? row.group : "choice";
        groups[g].push(row);
      });
      Object.keys(groups).forEach((g) => {
        groups[g].sort((a, b) => (Number(a.rank) || 100) - (Number(b.rank) || 100));
      });
      sel.innerHTML = "";
      [
        ["recommended", t("studio.mentorGroup.recommended")],
        ["also", t("studio.mentorGroup.also")],
        ["choice", t("studio.mentorGroup.choice")],
      ].forEach(([key, label]) => {
        const rows = groups[key] || [];
        if (!rows.length) return;
        const og = document.createElement("optgroup");
        og.label = label;
        rows.forEach((row) => {
          const opt = document.createElement("option");
          opt.value = row.id;
          opt.dataset.provider = row.provider || "";
          opt.dataset.model = row.model || "";
          let name = row.label || row.id;
          if (row.available === false && row.needs_key) name += " · " + t("studio.mentorNoKey");
          opt.textContent = name;
          opt.title = row.why || "";
          og.appendChild(opt);
        });
        sel.appendChild(og);
      });
      if (cur === "custom" && !catalog.some((r) => r.id === "custom")) {
        const opt = document.createElement("option");
        opt.value = "custom";
        opt.dataset.provider = choice.provider || "";
        opt.dataset.model = choice.model || "";
        opt.textContent = choice.label || choice.model || "custom";
        sel.appendChild(opt);
      }
      sel.value = cur;
      sel.disabled = !!llm.choice_locked;
    }
    const list = $("mentor-model-list");
    if (list) {
      const extra = Array.isArray(llm.presets) ? llm.presets : [];
      const seen = new Set();
      list.innerHTML = "";
      (catalog.concat(extra)).forEach((row) => {
        const model = row && row.model;
        if (!model || seen.has(row.provider + ":" + model)) return;
        seen.add(row.provider + ":" + model);
        const opt = document.createElement("option");
        opt.value = model;
        opt.label = (row.label || model) + (row.provider ? " · " + row.provider : "");
        list.appendChild(opt);
      });
    }
    const spendEl = $("mentor-spend");
    if (spendEl) {
      const spend = llm.spend || {};
      const used = Number(spend.tokens) || 0;
      const cap = Number(spend.cap) || 0;
      const fmt = (n) => {
        if (n >= 10000) return Math.round(n / 1000) + "k";
        if (n >= 1000) return (n / 1000).toFixed(1).replace(/\.0$/, "") + "k";
        return String(n);
      };
      spendEl.textContent = cap ? t("studio.mentorSpend", { used: fmt(used), cap: fmt(cap) }) : "";
      spendEl.classList.toggle("warn", cap > 0 && used >= cap * 0.8);
      const nFix = Number(data.pending_corrections) || 0;
      if (nFix) {
        spendEl.textContent = (spendEl.textContent ? spendEl.textContent + " · " : "")
          + t("studio.mentorFixes", { n: nFix });
        spendEl.classList.add("warn");
      }
      const bits = [];
      if (llm.choice && llm.choice.warn) bits.push(llm.choice.warn);
      if (llm.recommended && llm.recommended.why) bits.push(llm.recommended.why);
      const firstFix = Array.isArray(data.corrections) ? data.corrections[0] : null;
      if (firstFix && (firstFix.hint || firstFix.description)) {
        bits.push(firstFix.hint || firstFix.description);
      }
      spendEl.title = bits.join(" ");
    }
  }

  async function loadMentor() {
    try {
      const data = await api("/api/v1/daedalus/mentor", { timeoutMs: 8000 });
      paintMentor(data);
    } catch (e) {
      reportHeaderError("mentor", t("studio.mentorAria"), e);
    }
  }

  function paintAssist(data) {
    clearHeaderError("assist");
    const mode = String((data && data.mode) || "auto");
    const locked = !!(data && data.env_locked);
    document.querySelectorAll(".assist button").forEach((btn) => {
      btn.setAttribute("aria-pressed", btn.dataset.assist === mode ? "true" : "false");
      btn.disabled = locked;
    });
    const group = document.querySelector(".assist");
    if (group) {
      group.title = locked ? t("studio.assistLocked") : t("studio.assistHint");
    }
  }

  async function loadAssist() {
    try {
      const data = await api("/api/v1/daedalus/writer-assist", { timeoutMs: 8000 });
      paintAssist(data);
    } catch (e) {
      reportHeaderError("assist", t("studio.assistAria"), e);
    }
  }

  async function setAssist(mode) {
    try {
      const data = await api("/api/v1/daedalus/writer-assist", {
        method: "POST",
        body: JSON.stringify({ mode: mode || "off" }),
        timeoutMs: 8000,
      });
      paintAssist(data);
    } catch (e) {
      add("bot", t("studio.assistAria") + ": " + errText(e));
    }
  }

  function fmtUsd(n) {
    const x = Number(n);
    if (!Number.isFinite(x) || x <= 0) return "$0";
    if (x < 0.01) return "$" + x.toFixed(4);
    return "$" + x.toFixed(2);
  }

  function paintBudget(data) {
    clearHeaderError("budget");
    const usd = String((data && data.usd != null) ? data.usd : 0);
    const locked = !!(data && data.env_locked);
    document.querySelectorAll(".budget button").forEach((btn) => {
      btn.setAttribute("aria-pressed", btn.dataset.budget === usd ? "true" : "false");
      btn.disabled = locked;
    });
    const group = document.querySelector(".budget");
    const spend = (data && data.spend) || {};
    const spent = Number(spend.spent_usd) || 0;
    const cap = Number((data && data.usd) || spend.cap_usd) || 0;
    const el = $("budget-spend");
    if (el) {
      el.textContent = cap ? t("studio.budgetSpend", { spent: fmtUsd(spent), cap: fmtUsd(cap) }) : "";
      el.classList.toggle("warn", cap > 0 && (spend.exhausted || spent >= cap * 0.8));
      const bits = [];
      if (spend.unknown_cost_calls) bits.push("some calls had no OpenRouter cost");
      if (spend.last_model) bits.push(spend.last_model);
      el.title = bits.join(" · ");
    }
    if (group) {
      const plan = (data && data.plan) || {};
      group.title = locked ? t("studio.budgetLocked") : (plan.note || t("studio.budgetHint"));
    }
  }

  async function loadBudget() {
    try {
      const data = await api("/api/v1/daedalus/run-budget", { timeoutMs: 8000 });
      paintBudget(data);
    } catch (e) {
      reportHeaderError("budget", t("studio.budgetAria"), e);
    }
  }

  async function setBudget(usd) {
    try {
      const data = await api("/api/v1/daedalus/run-budget", {
        method: "POST",
        body: JSON.stringify({ usd: Number(usd) || 0 }),
        timeoutMs: 8000,
      });
      paintBudget(data);
    } catch (e) {
      add("bot", t("studio.budgetAria") + ": " + errText(e));
    }
  }

  async function setMentor(on) {
    try {
      const data = await api("/api/v1/daedalus/mentor", {
        method: "POST",
        body: JSON.stringify({ enabled: !!on }),
        timeoutMs: 8000,
      });
      paintMentor(data);
    } catch (e) {
      add("bot", t("studio.mentorAria") + ": " + errText(e));
    }
  }

  async function setMentorChoice(choiceId) {
    try {
      const data = await api("/api/v1/daedalus/mentor", {
        method: "POST",
        body: JSON.stringify({ choice_id: choiceId }),
        timeoutMs: 8000,
      });
      paintMentor(data);
    } catch (e) {
      add("bot", t("studio.mentorAria") + ": " + errText(e));
    }
  }

  async function setMentorCustom(provider, model) {
    try {
      const data = await api("/api/v1/daedalus/mentor", {
        method: "POST",
        body: JSON.stringify({ provider, model }),
        timeoutMs: 8000,
      });
      paintMentor(data);
    } catch (e) {
      add("bot", t("studio.mentorAria") + ": " + errText(e));
    }
  }

  function selectedProject() {
    return state.projects.find((p) => p.slug === state.slug) || null;
  }

  function syncProjectChrome() {
    const p = selectedProject();
    const chip = $("projchip");
    chip.classList.toggle("empty", !p);
    chip.title = p ? t("studio.clickToClear") : "";
    text(chip, p ? (p.name || p.slug) : t("studio.noProject"));
    const locked = !state.slug;
    ["pcapsule", "ppub", "plive", "build", "ptest", "dissectbtn"].forEach((id) => {
      const n = $(id);
      if (n) n.disabled = locked;
    });
    $("cwd").textContent = state.slug
      ? "cwd · playground/projects/" + state.slug
      : t("studio.cwdJail");
  }

  $("projchip").onclick = () => {
    if (!state.slug) return;
    state.slug = null;
    localStorage.removeItem("dx.slug");
    refreshProjects();
  };

  async function previewEffort() {
    const q = $("prompt").value.trim();
    try {
      const data = await api("/api/v1/daedalus/effort", {
        method: "POST",
        body: JSON.stringify({
          text: q.slice(0, 8000),
          effort: state.effort || "auto",
        }),
        timeoutMs: 8000,
      });
      const tier = data.tier || "whisper";
      text(
        $("effortnote"),
        t("studio.effort." + (state.effort || "auto")) + " — " + t("studio.effortNote." + tier)
      );
    } catch (e) {
      text($("effortnote"), t("studio.effortPreviewErr") + ": " + errText(e));
    }
  }

  let effortTimer = 0;
  function scheduleEffortPreview() {
    clearTimeout(effortTimer);
    effortTimer = setTimeout(previewEffort, 280);
  }

  document.querySelectorAll(".tabs button").forEach((btn) => {
    btn.onclick = () => {
      document.querySelectorAll(".tabs button").forEach((b) => {
        b.classList.remove("on");
        b.setAttribute("aria-selected", "false");
      });
      document.querySelectorAll(".pane").forEach((p) => p.classList.remove("on"));
      btn.classList.add("on");
      btn.setAttribute("aria-selected", "true");
      $("pane-" + btn.dataset.pane).classList.add("on");
      if (btn.dataset.pane === "world" && typeof refreshLabs === "function") refreshLabs();   // the lab list probes the host: only when shown
    };
  });
  document.querySelectorAll(".effort button").forEach((btn) => {
    btn.onclick = () => setEffort(btn.dataset.effort);
  });
  document.querySelectorAll(".mentor button").forEach((btn) => {
    btn.onclick = () => setMentor(btn.dataset.mentor === "on");
  });
  document.querySelectorAll(".assist button").forEach((btn) => {
    btn.onclick = () => setAssist(btn.dataset.assist);
  });
  document.querySelectorAll(".budget button").forEach((btn) => {
    btn.onclick = () => setBudget(btn.dataset.budget);
  });
  const mentorChoice = $("mentor-choice");
  if (mentorChoice) {
    mentorChoice.onchange = () => setMentorChoice(mentorChoice.value);
  }
  const mentorCustomBtn = $("mentor-custom");
  const mentorDlg = $("mentor-custom-dlg");
  if (mentorCustomBtn && mentorDlg) {
    mentorCustomBtn.onclick = () => {
      const selected = mentorChoice && mentorChoice.selectedOptions && mentorChoice.selectedOptions[0];
      const prov = $("mentor-prov");
      const model = $("mentor-model");
      if (selected && prov && selected.dataset.provider) prov.value = selected.dataset.provider;
      if (selected && model && selected.dataset.model) model.value = selected.dataset.model;
      if (typeof mentorDlg.showModal === "function") mentorDlg.showModal();
      else mentorDlg.setAttribute("open", "open");
    };
  }
  const mentorCancel = $("mentor-custom-cancel");
  if (mentorCancel && mentorDlg) {
    mentorCancel.onclick = () => {
      if (typeof mentorDlg.close === "function") mentorDlg.close();
      else mentorDlg.removeAttribute("open");
    };
  }
  const mentorForm = $("mentor-custom-form");
  if (mentorForm) {
    mentorForm.onsubmit = (ev) => {
      ev.preventDefault();
      const provider = ($("mentor-prov") && $("mentor-prov").value) || "openrouter";
      const model = (($("mentor-model") && $("mentor-model").value) || "").trim();
      if (!model) return;
      setMentorCustom(provider, model);
      if (mentorDlg && typeof mentorDlg.close === "function") mentorDlg.close();
    };
  }

  async function refreshHealth() {
    try {
      const [h, llm, local, w] = await Promise.all([
        api("/health", { timeoutMs: 8000 }),
        api("/api/v1/llm/status", { timeoutMs: 8000 }).catch(() => ({})),
        api("/api/v1/llm/local", { timeoutMs: 8000 }).catch(() => ({})),
        api("/api/v1/llm/wardrobe", { timeoutMs: 8000 }).catch(() => ({})),
      ]);
      const pills = $("pills");
      pills.replaceChildren();
      const addPill = (label, cls) => {
        const s = document.createElement("span");
        s.className = "pill" + (cls ? " " + cls : "");
        text(s, label);
        pills.appendChild(s);
      };
      addPill(h.standalone ? "standalone" : "host");
      if (w.wardrobe && w.wardrobe.offline) addPill("offline", "warn");
      const brainP = (llm.brain && llm.brain.provider) || (h.llm && h.llm.brain_provider) || "";
      const brainM = (llm.brain && llm.brain.model) || (h.llm && h.llm.brain_model) || "";
      const turnP = llm.provider || "";
      const turnM = llm.model || "";
      const ov = llm.overlays || {};
      const sameTurn =
        !ov.this_turn ||
        (ov.this_turn.provider === brainP && ov.this_turn.model === brainM);
      if (llm.ok && brainP && brainM) {
        addPill(t("studio.brainPill", { provider: brainP, model: brainM }));
        if (!sameTurn && turnP && turnM) {
          addPill(t("studio.thisTurnPill", { provider: turnP, model: turnM }), "warn");
        }
      } else if (llm.ok && turnP && turnM) {
        addPill(turnP + " · " + turnM);
      } else addPill(t("studio.noLlm"), "err");
      const backends = (local.backends || []).filter((b) => b.ok).map((b) => b.provider);
      if (backends.length) addPill("local " + backends.join("+"));

      const lines = [];
      const prof = w.profile || {};
      lines.push(t("studio.machine") + ": " + (prof.class || "?") + "  RAM " + (prof.ram_gb || "?") + "G");
      if (prof.note) lines.push(prof.note);
      lines.push("ollama: " + (w.ollama ? t("studio.ollamaYes") : t("studio.ollamaNo")));
      const eff = local.effective || {};
      const locBrain = local.brain || {};
      lines.push(
        t("studio.brainLine") +
          ": " +
          (locBrain.provider || brainP || "?") +
          " / " +
          (locBrain.model || brainM || "?"),
      );
      if ((locBrain.provider && eff.provider && locBrain.model && eff.model) &&
          (locBrain.provider !== eff.provider || locBrain.model !== eff.model)) {
        lines.push(t("studio.thisTurnLine") + ": " + (eff.provider || "?") + " / " + (eff.model || "?"));
      }
      lines.push(t("studio.active") + ": " + (eff.provider || "?") + " / " + (eff.model || "?"));
      (local.backends || []).forEach((b) => lines.push((b.ok ? "UP  " : "DOWN") + " " + b.provider));
      text($("llmbox"), lines.join("\n"));

      const box = $("wardrobe");
      box.replaceChildren();
      (w.catalog || []).forEach((row) => {
        const mark = row.installed ? t("studio.localMark") : (row.fits ? t("studio.fits") : t("studio.heavy"));
        const el = itemEl(row.label, row.role + " · " + row.ollama + " · " + mark, !!row.recommended);
        el.title = row.blurb || "";
        el.onclick = async () => {
          try {
            if (!row.installed && w.ollama) {
              if (!confirm(t("studio.pullConfirm", { name: row.ollama }))) return;
              text($("llmbox"), t("studio.pulling", { name: row.ollama }));
              await api("/api/v1/llm/wardrobe/pull", {
                method: "POST",
                body: JSON.stringify({ id: row.id, confirm: true }),
                timeoutMs: 180000,
              });
            } else if (!row.installed && !w.ollama) {
              add("bot", t("studio.installOllama", { name: row.ollama }));
              return;
            }
            await api("/api/v1/llm/wardrobe/activate", {
              method: "POST",
              body: JSON.stringify({ id: row.id }),
            });
            refreshHealth();
          } catch (e) {
            add("bot", t("studio.wardrobeErr") + ": " + errText(e));
          }
        };
        box.appendChild(el);
      });
      const offlineOn = !!(w.wardrobe && w.wardrobe.offline);
      $("offon").dataset.on = offlineOn ? "1" : "0";
      $("offon").textContent = offlineOn ? t("studio.offlineOn") : t("studio.offlineKit");
    } catch {
      $("pills").replaceChildren();
      const s = document.createElement("span");
      s.className = "pill err";
      text(s, "offline");
      $("pills").appendChild(s);
    }
  }

  function projectBucket(project) {
    if (state.gameIndex !== "ready") return "";
    const slug = project && project.slug;
    if (state.gameSlugs && slug && state.gameSlugs.has(slug)) return "games";
    const kind = String((project && project.kind) || "").toLowerCase();
    if (APP_KINDS[kind]) return "apps";
    return "games";
  }

  // selected: this render follows an explicit pick of a project (a list click or selectSlug), even of the one already
  // open; a passive re-render (the game index arriving, a filter click) is not one, and panels need not reload for it.
  // The flag travels with the render the pick started (refreshProjects -> renderProjectList -> notifySlug), so another
  // render running meanwhile can neither take it nor lend it.
  function slugSnapshot(selected) {
    return {
      slug: state.slug,
      projects: state.projects.slice(),
      ready: state.projectsReady,
      selected: !!selected,
    };
  }
  // A watcher may pick a project itself (the deep link ?slug=x does). That pick must reach every watcher as one newer
  // snapshot after the current round, never interleaved with it: so a notify made during a round is run when it ends.
  let notifying = false;
  let notifyAgain = null;
  function notifySlug(opts) {
    const selected = !!(opts && opts.selected);
    if (notifying) {
      notifyAgain = { selected: selected || !!(notifyAgain && notifyAgain.selected) };
      return;
    }
    notifying = true;
    try {
      const snap = slugSnapshot(selected);
      slugWatchers.slice().forEach((fn) => {
        try { fn(snap); } catch (err) { /* a panel watcher must not break the list */ }
      });
    } finally {
      notifying = false;
    }
    if (notifyAgain) {
      const next = notifyAgain;
      notifyAgain = null;
      notifySlug(next);
    }
  }

  // An explicit pick tells the panels at once, with the list as it is now, and then refreshes the list passively.
  // The panels' reload therefore begins at the pick, before anything the owner can click next; if it began only when
  // the refreshed list arrived, it would start after such a click and replace what the click reported.
  function pickProject(slug) {
    state.slug = slug || null;
    if (state.slug) localStorage.setItem("dx.slug", state.slug);
    else localStorage.removeItem("dx.slug");
    renderProjectList({ selected: true });
    syncProjectChrome();
    refreshProjects();
  }

  function renderProjectList(opts) {
    const box = $("plist");
    box.replaceChildren();
    const indexed = state.gameIndex === "ready";
    const visible = state.projects.filter((p) => {
      if (!indexed || state.kindFilter === "all") return true;
      return projectBucket(p) === state.kindFilter;
    });
    if (!visible.length) {
      const empty = document.createElement("div");
      empty.className = "empty";
      text(empty, state.projects.length ? t("studio.noMatch") : t("studio.noProjects"));
      box.appendChild(empty);
    }
    visible.forEach((p) => {
      const el = itemEl(p.name || p.slug, p.kind + " · " + p.slug, p.slug === state.slug);
      el.dataset.slug = p.slug;
      el.dataset.bucket = indexed ? projectBucket(p) : "pending";
      el.onclick = () => {
        pickProject(p.slug);
      };
      box.appendChild(el);
    });
    notifySlug(opts);
  }

  async function refreshProjects(opts) {
    try {
      const data = await api("/api/v1/playground/projects");
      state.projects = data.projects || [];
    } catch (e) {
      add("bot", t("studio.projectsErr") + ": " + errText(e));
      state.projects = state.projects || [];
    }
    state.projectsReady = true;
    renderProjectList(opts);
    syncProjectChrome();
    refreshWorld();
    refreshSessionPins();
  }

  async function refreshPrefs() {
    try {
      const data = await api("/api/v1/daedalus/preferences");
      const box = $("preflist");
      box.replaceChildren();
      const items = data.items || [];
      if (!items.length) {
        const empty = document.createElement("div");
        empty.className = "empty";
        text(empty, t("studio.noPrefs"));
        box.appendChild(empty);
      }
      items.slice(0, 12).forEach((it) => {
        const el = itemEl(it.text || "", it.kind || "preference", false);
        el.title = t("studio.clickToDelete");
        el.onclick = async () => {
          try {
            await api("/api/v1/daedalus/preferences/" + it.id, { method: "DELETE" });
            refreshPrefs();
          } catch (e) {
            add("bot", t("studio.prefErr") + ": " + errText(e));
          }
        };
        box.appendChild(el);
      });
    } catch (e) {
      text($("preflist"), t("studio.prefErr") + ": " + errText(e));
    }
  }

  async function refreshLessons() {
    const box = $("lessonlist");
    if (!box) return;
    try {
      const data = await api("/api/v1/daedalus/learned-skills");
      box.replaceChildren();
      const pending = data.pending || [];
      if (!pending.length) {
        const empty = document.createElement("div");
        empty.className = "empty";
        text(empty, t("studio.noLessons"));
        box.appendChild(empty);
        return;
      }
      pending.slice(0, 8).forEach((it) => {
        const el = document.createElement("div");
        el.className = "item lesson";
        const b = document.createElement("b");
        text(b, it.name || it.id || t("studio.lesson"));
        const s = document.createElement("small");
        const extra = it.extra || {};
        const nextBit = extra.next_tool ? ("NEXT " + extra.next_tool) : "";
        text(s, [nextBit, it.description || it.origin || "pending"].filter(Boolean).join(" · "));
        const row = document.createElement("div");
        row.className = "row tight";
        const okBtn = document.createElement("button");
        okBtn.className = "primary";
        okBtn.type = "button";
        text(okBtn, t("studio.approve"));
        okBtn.onclick = async (ev) => {
          ev.stopPropagation();
          try {
            await api("/api/v1/daedalus/learned-skills/" + it.id + "/approve", { method: "POST" });
            refreshLessons();
          } catch (e) {
            add("bot", t("studio.lessonErr") + ": " + errText(e));
          }
        };
        const noBtn = document.createElement("button");
        noBtn.type = "button";
        text(noBtn, t("studio.reject"));
        noBtn.onclick = async (ev) => {
          ev.stopPropagation();
          try {
            await api("/api/v1/daedalus/learned-skills/" + it.id + "/reject", { method: "POST" });
            refreshLessons();
          } catch (e) {
            add("bot", t("studio.lessonErr") + ": " + errText(e));
          }
        };
        row.append(okBtn, noBtn);
        el.append(b, s, row);
        box.appendChild(el);
      });
    } catch (e) {
      box.replaceChildren();
      const empty = document.createElement("div");
      empty.className = "empty";
      text(empty, t("studio.lessonErr") + ": " + errText(e));
      box.appendChild(empty);
    }
  }

  function sessionId() {
    return state.slug || state.session;
  }

  function skillById(id) {
    return state.skillIndex.find((s) => s.id === id || s.name === id) || null;
  }

  function renderSkillSelect() {
    const sel = $("skillpick");
    if (!sel) return;
    const keep = sel.value;
    sel.replaceChildren();
    const blank = document.createElement("option");
    blank.value = "";
    text(blank, t("studio.ctxSkills"));
    sel.appendChild(blank);
    state.skillIndex.forEach((s) => {
      const o = document.createElement("option");
      o.value = s.id;
      text(o, (s.name || s.id) + (s.origin === "installed" ? " · catalog" : ""));
      sel.appendChild(o);
    });
    if (keep && [...sel.options].some((o) => o.value === keep)) sel.value = keep;
  }

  function renderSessionPins() {
    const box = $("skillpins");
    if (!box) return;
    box.replaceChildren();
    if (!state.pinSkills.length) {
      const empty = document.createElement("div");
      empty.className = "empty";
      text(empty, t("studio.ctxEmpty"));
      box.appendChild(empty);
      return;
    }
    state.pinSkills.forEach((id) => {
      const s = skillById(id);
      const el = itemEl(s ? (s.name || id) : id, s ? (s.description || s.origin || "") : "pinned", true);
      el.title = t("studio.unpin");
      el.onclick = () => unpinSkill(id);
      box.appendChild(el);
    });
  }

  function renderChips() {
    const box = $("ctxchips");
    if (!box) return;
    box.replaceChildren();
    const bits = [];
    state.pinSkills.forEach((id) => bits.push({ kind: "skill", id, pin: true, label: (skillById(id) || {}).name || id }));
    state.turnSkills.forEach((id) => {
      if (!state.pinSkills.includes(id)) bits.push({ kind: "skill", id, pin: false, label: (skillById(id) || {}).name || id });
    });
    state.attachments.forEach((a) => bits.push({ kind: a.kind === "image" ? "image" : "file", id: a.id, pin: false, label: a.name || a.id }));
    const mcp = [...new Set([...state.pinMcp, ...state.turnMcp])];
    mcp.forEach((id) => bits.push({ kind: "mcp", id, pin: state.pinMcp.includes(id), label: id }));
    if (!bits.length) {
      box.hidden = true;
      return;
    }
    box.hidden = false;
    bits.forEach((bit) => {
      const chip = document.createElement("span");
      chip.className = "ctx-chip" + (bit.pin ? "" : " turn");
      const lab = document.createElement("span");
      text(lab, bit.label);
      const x = document.createElement("button");
      x.type = "button";
      text(x, "×");
      x.onclick = () => dropChip(bit);
      chip.append(lab, x);
      box.appendChild(chip);
    });
    const sn = $("ctxskilln");
    const mn = $("ctxmcpn");
    if (sn) text(sn, String([...new Set([...state.pinSkills, ...state.turnSkills])].length));
    if (mn) text(mn, String(mcp.length));
  }

  function dropChip(bit) {
    if (bit.kind === "file" || bit.kind === "image") {
      state.attachments = state.attachments.filter((a) => a.id !== bit.id);
    } else if (bit.kind === "mcp") {
      state.turnMcp = state.turnMcp.filter((x) => x !== bit.id);
      if (state.pinMcp.includes(bit.id)) unpinMcp(bit.id);
    } else if (bit.pin) {
      unpinSkill(bit.id);
    } else {
      state.turnSkills = state.turnSkills.filter((x) => x !== bit.id);
    }
    renderChips();
  }

  async function persistPins() {
    try {
      await api("/api/v1/daedalus/session-context", {
        method: "PUT",
        body: JSON.stringify({
          session_id: sessionId(),
          skill_ids: state.pinSkills,
          mcp_ids: state.pinMcp,
        }),
      });
    } catch (e) {
      add("bot", t("studio.catalogErr") + ": " + errText(e));
    }
  }

  async function pinSkill(id) {
    if (!id || state.pinSkills.includes(id)) return;
    state.pinSkills = state.pinSkills.concat(id);
    state.turnSkills = state.turnSkills.filter((x) => x !== id);
    await persistPins();
    renderSessionPins();
    renderChips();
  }

  async function unpinSkill(id) {
    state.pinSkills = state.pinSkills.filter((x) => x !== id);
    await persistPins();
    renderSessionPins();
    renderChips();
  }

  async function unpinMcp(id) {
    state.pinMcp = state.pinMcp.filter((x) => x !== id);
    await persistPins();
    renderChips();
  }

  async function refreshSkillIndex() {
    try {
      const data = await api("/api/v1/daedalus/learned-skills");
      state.skillIndex = data.skills || [];
      renderSkillSelect();
      renderSessionPins();
      renderChips();
    } catch {
      state.skillIndex = [];
    }
  }

  async function refreshSessionPins() {
    try {
      const data = await api("/api/v1/daedalus/session-context?session_id=" + encodeURIComponent(sessionId()));
      state.pinSkills = data.skill_ids || [];
      state.pinMcp = data.mcp_ids || [];
    } catch {
      state.pinSkills = [];
      state.pinMcp = [];
    }
    renderSessionPins();
    renderChips();
  }

  async function refreshAddons() {
    try {
      const data = await api("/api/v1/daedalus/addons");
      state.addons = data.addons || [];
    } catch {
      state.addons = [];
    }
  }

  function setSheet(open) {
    const sheet = $("ctxsheet");
    const plus = $("ctxplus");
    if (!sheet || !plus) return;
    sheet.hidden = !open;
    plus.setAttribute("aria-expanded", open ? "true" : "false");
    if (open) {
      text($("ctxmode"), t("studio.effort." + state.effort) + " — " + t("studio.effortNote." + (state.effort === "auto" ? "auto" : state.effort)));
      if (state.ctxPane) openCtxPane(state.ctxPane);
    }
  }

  function ctxRow(title, sub, actions) {
    const row = document.createElement("div");
    row.className = "ctx-row";
    const grow = document.createElement("div");
    grow.className = "grow";
    const b = document.createElement("b");
    text(b, title);
    const s = document.createElement("small");
    text(s, sub || "");
    grow.append(b, s);
    row.appendChild(grow);
    (actions || []).forEach((btn) => row.appendChild(btn));
    return row;
  }

  function miniBtn(label, fn, primary) {
    const btn = document.createElement("button");
    btn.type = "button";
    if (primary) btn.className = "primary";
    text(btn, label);
    btn.onclick = fn;
    return btn;
  }

  async function openCtxPane(pane) {
    state.ctxPane = pane;
    const sub = $("ctxsub");
    if (!sub) return;
    sub.hidden = false;
    sub.replaceChildren();
    if (pane === "files") {
      $("ctxfile").click();
      sub.hidden = true;
      return;
    }
    if (pane === "images") {
      $("ctximage").click();
      sub.hidden = true;
      return;
    }
    if (pane === "skills") {
      const search = document.createElement("input");
      search.type = "search";
      search.placeholder = t("studio.ctxSearch");
      sub.appendChild(search);
      const list = document.createElement("div");
      sub.appendChild(list);
      const draw = (rows) => {
        list.replaceChildren();
        if (!rows.length) {
          const empty = document.createElement("div");
          empty.className = "empty";
          text(empty, t("studio.ctxEmpty"));
          list.appendChild(empty);
          return;
        }
        rows.slice(0, 40).forEach((s) => {
          const id = s.id || s.name;
          const on = state.pinSkills.includes(id) || state.turnSkills.includes(id);
          list.appendChild(ctxRow(
            (s.name || s.title || id) + (on ? " ✓" : ""),
            s.description || s.text || s.origin || s.source || "",
            [
              miniBtn(t("studio.ctxAddTurn"), () => {
                if (!state.turnSkills.includes(id) && !state.pinSkills.includes(id)) {
                  state.turnSkills = state.turnSkills.concat(id);
                  renderChips();
                  draw(rows);
                }
              }),
              miniBtn(t("studio.ctxPinSession"), () => pinSkill(id), true),
            ]
          ));
        });
      };
      draw(state.skillIndex);
      let timer = 0;
      search.addEventListener("input", () => {
        clearTimeout(timer);
        timer = setTimeout(async () => {
          const q = search.value.trim().toLowerCase();
          if (!q) {
            draw(state.skillIndex);
            return;
          }
          const local = state.skillIndex.filter((s) =>
            ((s.name || "") + " " + (s.description || "") + " " + (s.id || "")).toLowerCase().includes(q)
          );
          let remote = [];
          try {
            const cat = await api("/api/v1/daedalus/skill-catalog?q=" + encodeURIComponent(q), { timeoutMs: 12000 });
            remote = (cat.results || []).map((r) => ({
              id: r.id,
              name: r.title,
              description: (r.text || "").split("\n")[0],
              source: r.source,
              catalog: true,
            }));
          } catch { /* offline catalog */ }
          const box = [];
          local.forEach((s) => box.push(s));
          remote.forEach((r) => {
            if (!box.some((s) => s.id === r.id || s.name === r.name)) box.push(r);
          });
          list.replaceChildren();
          if (!box.length) {
            const empty = document.createElement("div");
            empty.className = "empty";
            text(empty, t("studio.ctxEmpty"));
            list.appendChild(empty);
            return;
          }
          box.slice(0, 40).forEach((s) => {
            const id = s.id || s.name;
            const actions = s.catalog
              ? [miniBtn(t("studio.ctxInstall"), async () => {
                  try {
                    const out = await api("/api/v1/daedalus/skill-catalog/install", {
                      method: "POST",
                      body: JSON.stringify({ id }),
                    });
                    add("bot", out.pending ? t("studio.installPending") : t("studio.installOk") + ": " + (out.name || id));
                    await refreshSkillIndex();
                    if (!out.pending && out.id) await pinSkill(out.id);
                    await refreshLessons();
                  } catch (e) {
                    add("bot", t("studio.installErr") + ": " + errText(e));
                  }
                }, true)]
              : [
                  miniBtn(t("studio.ctxAddTurn"), () => {
                    if (!state.turnSkills.includes(id) && !state.pinSkills.includes(id)) {
                      state.turnSkills = state.turnSkills.concat(id);
                      renderChips();
                    }
                  }),
                  miniBtn(t("studio.ctxPinSession"), () => pinSkill(id), true),
                ];
            list.appendChild(ctxRow(s.name || s.title || id, s.description || s.source || "", actions));
          });
        }, 220);
      });
      const refresh = miniBtn(t("studio.ctxRefresh"), async () => {
        refresh.disabled = true;
        try {
          await api("/api/v1/daedalus/skill-catalog/refresh?force=true", { method: "POST", timeoutMs: 120000 });
          add("bot", t("studio.ctxRefresh"));
        } catch (e) {
          add("bot", t("studio.catalogErr") + ": " + errText(e));
        } finally {
          refresh.disabled = false;
        }
      });
      sub.appendChild(refresh);
      return;
    }
    if (pane === "project") {
      if (!state.slug) {
        const empty = document.createElement("div");
        empty.className = "empty";
        text(empty, t("studio.ctxNoProject"));
        sub.appendChild(empty);
        return;
      }
      try {
        const data = await api("/api/v1/playground/projects/" + encodeURIComponent(state.slug));
        const files = data.files || [];
        if (!files.length) {
          const empty = document.createElement("div");
          empty.className = "empty";
          text(empty, t("studio.ctxEmpty"));
          sub.appendChild(empty);
          return;
        }
        files.slice(0, 80).forEach((f) => {
          const path = f.path || f;
          sub.appendChild(ctxRow(path, String(f.size || ""), [
            miniBtn(t("studio.ctxAddTurn"), async () => {
              try {
                const att = await api("/api/v1/daedalus/chat/attachments/project", {
                  method: "POST",
                  body: JSON.stringify({ session_id: sessionId(), slug: state.slug, path }),
                });
                state.attachments.push(att);
                renderChips();
              } catch (e) {
                add("bot", t("studio.catalogErr") + ": " + errText(e));
              }
            }, true),
          ]));
        });
      } catch (e) {
        const empty = document.createElement("div");
        empty.className = "empty";
        text(empty, errText(e));
        sub.appendChild(empty);
      }
      return;
    }
    if (pane === "mcp") {
      await refreshAddons();
      const mcp = (state.addons || []).filter((a) => a.kind === "mcp");
      if (!mcp.length) {
        const empty = document.createElement("div");
        empty.className = "empty";
        text(empty, t("studio.ctxEmpty"));
        sub.appendChild(empty);
        return;
      }
      mcp.forEach((a) => {
        const on = state.pinMcp.includes(a.id) || state.turnMcp.includes(a.id);
        const actions = [];
        if (!a.enabled) {
          actions.push(miniBtn(t("studio.ctxEnable"), async () => {
            try {
              await api("/api/v1/daedalus/addons", { method: "POST", body: JSON.stringify({ id: a.id }) });
              await refreshAddons();
              openCtxPane("mcp");
            } catch (e) {
              add("bot", errText(e));
            }
          }));
        } else {
          actions.push(miniBtn(t("studio.ctxAddTurn"), () => {
            if (!state.turnMcp.includes(a.id) && !state.pinMcp.includes(a.id)) {
              state.turnMcp = state.turnMcp.concat(a.id);
              renderChips();
              openCtxPane("mcp");
            }
          }));
          actions.push(miniBtn(t("studio.ctxPinSession"), async () => {
            if (!state.pinMcp.includes(a.id)) {
              state.pinMcp = state.pinMcp.concat(a.id);
              state.turnMcp = state.turnMcp.filter((x) => x !== a.id);
              await persistPins();
              renderChips();
              openCtxPane("mcp");
            }
          }, true));
        }
        sub.appendChild(ctxRow(
          (a.label || a.id) + (on ? " ✓" : ""),
          a.enabled ? (a.blurb || "") : t("studio.mcpOff") + " — " + (a.blurb || ""),
          actions
        ));
      });
    }
  }

  async function uploadFiles(fileList, asImage) {
    const files = [...fileList];
    for (const file of files) {
      const fd = new FormData();
      fd.append("file", file);
      fd.append("session_id", sessionId());
      try {
        const res = await fetch("/api/v1/daedalus/chat/attachments", { method: "POST", body: fd });
        const raw = await res.text();
        let data = {};
        try { data = raw ? JSON.parse(raw) : {}; } catch { data = { detail: raw.slice(0, 200) }; }
        if (!res.ok) throw new Error(data.detail || data.error || ("HTTP " + res.status));
        if (asImage && data.kind !== "image") data.kind = data.kind || "file";
        state.attachments.push(data);
      } catch (e) {
        add("bot", t("studio.catalogErr") + ": " + errText(e));
      }
    }
    renderChips();
  }

  $("skillpin") && ($("skillpin").onclick = () => {
    const id = $("skillpick").value;
    if (id) pinSkill(id);
  });
  $("ctxplus") && ($("ctxplus").onclick = () => setSheet($("ctxsheet").hidden));
  $("ctxclose") && ($("ctxclose").onclick = () => setSheet(false));
  document.querySelectorAll("[data-ctx]").forEach((btn) => {
    btn.onclick = () => openCtxPane(btn.getAttribute("data-ctx"));
  });
  $("ctxfile") && $("ctxfile").addEventListener("change", (ev) => {
    uploadFiles(ev.target.files || [], false);
    ev.target.value = "";
  });
  $("ctximage") && $("ctximage").addEventListener("change", (ev) => {
    uploadFiles(ev.target.files || [], true);
    ev.target.value = "";
  });
  const composer = document.querySelector(".composer");
  if (composer) {
    composer.addEventListener("dragover", (ev) => { ev.preventDefault(); });
    composer.addEventListener("drop", (ev) => {
      ev.preventDefault();
      const files = ev.dataTransfer && ev.dataTransfer.files;
      if (files && files.length) uploadFiles(files, false);
    });
  }

  $("pcreate").onclick = async () => {
    const name = $("pname").value.trim();
    if (!name) return;
    const description = ($("pdesc").value.trim() || $("prompt").value.trim() || name);
    busy($("pcreate"), true, "…");
    try {
      const res = await api("/api/v1/playground/projects", {
        method: "POST",
        body: JSON.stringify({
          name,
          kind: $("pkind").value,
          description,
          effort: state.effort,
        }),
      });
      if (!res.project) {
        add("bot", res.detail || t("studio.createFailed"));
        return;
      }
      state.slug = res.project.slug;
      localStorage.setItem("dx.slug", state.slug);
      $("pname").value = "";
      $("pdesc").value = "";
      await refreshProjects();
      if (state.effort === "epic" || /кампани|необят|персонаж|сюжет/i.test(description)) {
        add("bot", t("studio.epicCreated"));
      }
    } catch (e) {
      add("bot", t("studio.projectErr") + ": " + errText(e));
    } finally {
      busy($("pcreate"), false);
    }
  };

  $("prefadd").onclick = async () => {
    const value = $("preftext").value.trim();
    if (!value) return;
    busy($("prefadd"), true, "…");
    try {
      await api("/api/v1/daedalus/preferences", {
        method: "POST",
        body: JSON.stringify({ text: value }),
      });
      $("preftext").value = "";
      refreshPrefs();
    } catch (e) {
      add("bot", t("studio.prefErr") + ": " + errText(e));
    } finally {
      busy($("prefadd"), false);
    }
  };

  function chatBusy(on) {
    $("send").disabled = on;
    $("stop").hidden = !on;
    if (!on) {
      state.chatJob = null;
      if (state.chatSource) {
        try { state.chatSource.close(); } catch { /* already closed */ }
        state.chatSource = null;
      }
    }
  }

  function applyChatResult(last, res) {
    const body = (res && (res.result || res)) || {};
    text(last, body.reply || body.message || body.error || JSON.stringify(body, null, 2));
    const bits = [];
    if (body.effort && body.effort.label) bits.push(body.effort.label);
    if (body.complete === false) bits.push(t("studio.incomplete"));
    if (body.complete === true) bits.push(t("studio.done"));
    if (body.model) bits.push(body.model);
    if (body.elapsed_seconds != null) bits.push(body.elapsed_seconds + "s");
    const billed = body.run_budget && body.run_budget.spend;
    if (billed && Number(body.run_budget.usd) > 0) {
      bits.push(fmtUsd(billed.spent_usd) + " / " + fmtUsd(body.run_budget.usd));
    }
    if (bits.length) setLiveLabel(last, bits.join(" · "));
    if (body.run_budget) paintBudget(body.run_budget);
  }

  async function sendChat() {
    const message = $("prompt").value.trim();
    if ($("send").disabled) return;
    if (!message && !state.attachments.length) {
      add("bot", t("studio.needMessage"));
      return;
    }
    $("prompt").value = "";
    const metaBits = [];
    state.attachments.forEach((a) => metaBits.push((a.kind === "image" ? "🖼 " : "📄 ") + (a.name || a.id)));
    [...new Set([...state.pinSkills, ...state.turnSkills])].forEach((id) => metaBits.push("skill:" + id));
    add("user", message || t("studio.context"), metaBits.join(" · ") || undefined);
    const last = add("bot", t("studio.thinking"));
    const act = activityPanel(last);
    chatBusy(true);
    const skillIds = [...new Set([...state.pinSkills, ...state.turnSkills])];
    const mcpIds = [...new Set([...state.pinMcp, ...state.turnMcp])];
    const attachmentIds = state.attachments.map((a) => a.id);
    state.turnSkills = [];
    state.turnMcp = [];
    state.attachments = [];
    renderChips();
    try {
      if (window.DaedalusAuth && DaedalusAuth.ensureToken) {
        await DaedalusAuth.ensureToken();
      }
      const job = await api("/api/v1/daedalus/chat/jobs", {
        method: "POST",
        body: JSON.stringify({
          message: message || "(see attached context)",
          session_id: sessionId(),
          effort: state.effort,
          skill_ids: skillIds,
          mcp_ids: mcpIds,
          attachment_ids: attachmentIds,
          project: projectChatSlug(),
        }),
        timeoutMs: 20000,
      });
      state.chatJob = job.job_id;
      const streamPath = "/api/v1/daedalus/chat/jobs/" + job.job_id + "/stream";
      const src = (window.DaedalusAuth && DaedalusAuth.eventSource)
        ? await DaedalusAuth.eventSource(streamPath)
        : new EventSource(streamPath);
      state.chatSource = src;
      let finished = false;
      const finish = (payload, err) => {
        if (finished) return;
        finished = true;
        try { src.close(); } catch { /* ignore */ }
        act.finish(!err && payload && payload.status === "done" ? "done" : "stopped");
        if (err) text(last, t("studio.errorPrefix") + ": " + errText(err));
        else if (payload && payload.status === "cancelled") text(last, t("studio.stopped"));
        else if (payload && payload.status === "error") text(last, t("studio.errorPrefix") + ": " + (payload.error || t("studio.chatStopped")));
        else applyChatResult(last, payload);
        if (payload && payload.project) renderTurnChanges(payload);
        chatBusy(false);
        refreshPrefs();
        refreshLessons();
        previewEffort();
        loadBudget();
      };
      src.addEventListener("live", (ev) => {
        try {
          const d = JSON.parse(ev.data);
          setLiveLabel(last, liveLabel(d));
        } catch { /* ignore */ }
      });
      src.addEventListener("activity", (ev) => {
        try { act.addRows(JSON.parse(ev.data).rows); } catch { /* ignore */ }
      });
      src.addEventListener("done", (ev) => {
        try { finish(JSON.parse(ev.data)); }
        catch (e) { finish(null, e); }
      });
      src.addEventListener("error", (ev) => {
        if (finished) return;
        try {
          const d = JSON.parse(ev.data);
          finish(null, d.error || t("studio.streamStopped"));
        } catch {
          // EventSource also fires error on disconnect after done.
          if (!finished) {
            api("/api/v1/daedalus/chat/jobs/" + job.job_id, { timeoutMs: 8000 })
              .then((row) => finish(row))
              .catch((e) => finish(null, e));
          }
        }
      });
      src.onerror = () => {
        if (finished) { try { src.close(); } catch { /* ignore */ } return; }
        api("/api/v1/daedalus/chat/jobs/" + job.job_id, { timeoutMs: 8000 })
          .then((row) => {
            if (row.status === "running") return;
            finish(row);
          })
          .catch((e) => finish(null, e));
      };
    } catch (e) {
      act.finish("stopped");
      text(last, t("studio.errorPrefix") + ": " + errText(e));
      chatBusy(false);
    }
  }

  // Project chat (the classic project chat): the turn sees the project's files and keeps the project's own history.
  // What the turn changed is listed under the reply, with each diff and a Revert that asks first.
  function projectChatSlug() {
    return state.slug && $("projchat") && $("projchat").checked ? state.slug : "";
  }

  let projectChatFor = null;
  function syncProjectChat() {
    const wrap = $("projchatwrap");
    if (!wrap) return;
    wrap.hidden = !state.slug;
    // Another project: the switch goes off, so a turn never becomes a project turn on a project nobody chose for it.
    if (projectChatFor !== null && projectChatFor !== state.slug && $("projchat")) $("projchat").checked = false;
    projectChatFor = state.slug || "";
    const name = $("projchatslug");
    if (name) text(name, state.slug ? "· " + state.slug : "");
  }
  slugWatchers.push(syncProjectChat);

  const REVERT_ERR = { changed_since: "studio.revertChangedSince", already_reverted: "studio.revertAlready",
    not_revertible: "studio.revertNotPossible", still_running: "studio.revertRunning", not_found: "studio.revertGone" };

  function renderTurnChanges(job) {
    const list = job.changes;
    const box = document.createElement("div");
    box.className = "msg bot turn-changes";
    box.dataset.job = job.id || "";
    const head = document.createElement("b");
    if (!Array.isArray(list)) text(head, t("studio.changesUnknown"));
    else if (!list.length) text(head, t("studio.changesNone", { slug: job.project }));
    else text(head, t("studio.changesHead", { n: list.length, slug: job.project }));
    box.appendChild(head);
    (Array.isArray(list) ? list : []).forEach((c) => {
      const row = document.createElement("div");
      row.className = "change";
      row.dataset.path = c.path;
      const label = document.createElement("span");
      text(label, c.path + " · " + t("studio.change_" + c.status) + (c.secret ? " · " + t("studio.changeSecret") : ""));
      row.appendChild(label);
      const note = document.createElement("small");
      if (c.diff) {
        const pre = document.createElement("pre");
        pre.hidden = true;
        text(pre, c.diff);
        const show = document.createElement("button");
        show.type = "button";
        show.dataset.role = "diff";
        text(show, t("studio.changeDiff"));
        show.onclick = () => { pre.hidden = !pre.hidden; };
        row.append(show, pre);
      }
      if (c.revertible && !c.reverted) {
        const undo = document.createElement("button");
        undo.type = "button";
        undo.dataset.role = "revert";
        text(undo, t("studio.changeRevert"));
        undo.onclick = async () => {
          if (!window.confirm(t(c.status === "created" ? "studio.revertCreatedConfirm" : "studio.revertConfirm", { path: c.path }))) return;
          busy(undo, true, "…");
          try {
            const res = await api("/api/v1/daedalus/chat/jobs/" + encodeURIComponent(job.id) + "/revert", {
              method: "POST", body: JSON.stringify({ path: c.path, confirm: true }), timeoutMs: 20000 });
            undo.remove();
            text(note, t(res.reverted === "removed" ? "studio.revertRemoved" : "studio.revertRestored"));
          } catch (e) {
            busy(undo, false);
            const key = REVERT_ERR[String((e && e.message) || "")];
            text(note, key ? t(key) : t("studio.errorPrefix") + ": " + errText(e));
          }
        };
        row.appendChild(undo);
      } else if (c.reverted) {
        text(note, t(c.reverted === "removed" ? "studio.revertRemoved" : "studio.revertRestored"));
      } else if (!c.secret) {
        text(note, t("studio.revertNotPossible"));
      }
      row.appendChild(note);
      box.appendChild(row);
    });
    msgs.appendChild(box);
    msgs.scrollTop = msgs.scrollHeight;
  }

  async function stopChat() {
    const id = state.chatJob;
    if (state.chatSource) {
      try { state.chatSource.close(); } catch { /* ignore */ }
    }
    if (id) {
      try {
        await api("/api/v1/daedalus/chat/jobs/" + id + "/cancel", { method: "POST", timeoutMs: 8000 });
      } catch { /* UI stops waiting regardless */ }
    }
    chatBusy(false);
    const last = msgs.querySelector(".msg.bot:last-child");
    if (last && /thinking|мисля|Анализирам|Дедал мисли/i.test(last.textContent || "")) {
      text(last, t("studio.stopped"));
    }
  }

  $("send").onclick = sendChat;
  $("stop").onclick = stopChat;
  $("prompt").addEventListener("keydown", (ev) => {
    if (ev.key === "Enter" && !ev.shiftKey) { ev.preventDefault(); sendChat(); }
  });
  $("prompt").addEventListener("input", scheduleEffortPreview);

  $("build").onclick = async () => {
    if (!state.slug) { add("bot", t("studio.needProject")); return; }
    document.querySelector('[data-pane="flight"]').click();
    const description = $("prompt").value.trim() || ("Build " + state.slug);
    busy($("build"), true, "…");
    try {
      if (window.DaedalusAuth && DaedalusAuth.ensureToken) {
        await DaedalusAuth.ensureToken();
      }
      const res = await api("/api/v1/playground/projects/" + state.slug + "/autopilot", {
        method: "POST",
        body: JSON.stringify({ description, mode: "full", effort: state.effort }),
        timeoutMs: 30000,
      });
      text($("log"), "job " + (res.job && res.job.id) + "\n");
      const streamPath = "/api/v1/playground/projects/" + state.slug + "/autopilot/stream";
      const src = (window.DaedalusAuth && DaedalusAuth.eventSource)
        ? await DaedalusAuth.eventSource(streamPath)
        : new EventSource(streamPath);
      src.onmessage = (ev) => { $("log").textContent += ev.data + "\n"; };
      src.addEventListener("log", (ev) => { $("log").textContent += ev.data + "\n"; });
      src.addEventListener("phase", (ev) => { $("log").textContent += "phase " + ev.data + "\n"; });
      src.addEventListener("done", (ev) => {
        $("log").textContent += "DONE " + ev.data + "\n";
        src.close();
      });
      src.addEventListener("error", () => { src.close(); });
      src.onerror = () => {
        $("log").textContent += t("studio.streamCut") + "\n";
        src.close();
      };
    } catch (e) {
      text($("log"), t("studio.flightErr") + ": " + errText(e));
      add("bot", t("studio.flightErr") + ": " + errText(e));
    } finally {
      busy($("build"), false);
      syncProjectChrome();
    }
  };

  $("shrun").onclick = async () => {
    const command = $("shcmd").value.trim();
    if (!command) return;
    text($("shout"), "$ " + command + "\n…");
    busy($("shrun"), true, "…");
    try {
      const res = await api("/api/v1/daedalus/shell", {
        method: "POST",
        body: JSON.stringify({ command, slug: state.slug || "" }),
        timeoutMs: 120000,
      });
      state.lastCwd = res.cwd || "";
      if (state.lastCwd) text($("cwd"), "cwd · " + state.lastCwd);
      text($("shout"),
        "$ " + command + "  (exit " + res.exit_code + ", " + (res.elapsed_ms || 0) + "ms)\n" +
        (res.cwd ? "# " + res.cwd + "\n" : "") +
        (res.stdout || "") + (res.stderr ? "\n" + res.stderr : "") +
        (res.error && !res.ok ? "\n" + res.error : ""));
    } catch (e) {
      text($("shout"), "$ " + command + "\n" + errText(e));
    } finally {
      busy($("shrun"), false);
    }
  };
  $("shcmd").addEventListener("keydown", (ev) => {
    if (ev.key === "Enter") { ev.preventDefault(); $("shrun").click(); }
  });

  async function appendBench(box) {
    try {
      const b = await api("/api/v1/daedalus/bench");
      const passed = b.passed || 0;
      const total = b.total || 0;
      const ok = !!b.ok;
      box.appendChild(itemEl(t("studio.honesty"), passed + "/" + total + " · " + t("studio.notBakeoff"), ok));
    } catch (e) {
      box.appendChild(itemEl(t("studio.honesty"), errText(e), false));
    }
  }

  async function appendUnity(box) {
    if (!state.slug) return;
    try {
      const u = await api("/api/v1/playground/projects/" + state.slug + "/unity");
      if (!u.unity) return;
      const promise = u.promise || "local_hub";
      let sub = t("studio.unityHub");
      if (promise === "mcp") sub = t("studio.unityMcp");
      else if (promise === "batchmode") sub = t("studio.unityBatch");
      box.appendChild(itemEl("unity", sub, promise === "mcp"));
    } catch (e) {
      box.appendChild(itemEl("unity", errText(e), false));
    }
  }

  async function appendLayer(box) {
    if (!state.slug) return;
    try {
      const prod = await api("/api/v1/playground/projects/" + state.slug + "/production");
      const layer = prod.layer || "prototype";
      const wall = prod.wall || "prototype";
      const done = layer === "production" && wall === "clear";
      // The game-studio page said this too: until the production wall is clear, it is a prototype, not a store build.
      box.appendChild(itemEl(t("studio.layer"), layer + " · " + wall + (done ? "" : " · " + t("studio.notStoreBuild")), done));
    } catch (e) {
      box.appendChild(itemEl(t("studio.layer"), errText(e), false));
    }
  }

  async function refreshWorld() {
    const box = $("worldkit");
    if (!box) return;
    box.replaceChildren();
    await appendBench(box);
    if (!state.slug) {
      const empty = document.createElement("div");
      empty.className = "empty";
      text(empty, t("studio.pickProjectEpic"));
      box.appendChild(empty);
      return;
    }
    try {
      const data = await api("/api/v1/playground/projects/" + state.slug + "/world");
      const files = (data.world && data.world.files) || {};
      const keys = Object.keys(files);
      if (!keys.length) {
        const empty = document.createElement("div");
        empty.className = "empty";
        text(empty, t("studio.noKit"));
        box.appendChild(empty);
        await appendLayer(box);
        await appendUnity(box);
        return;
      }
      keys.forEach((k) => {
        box.appendChild(itemEl(k, files[k] ? t("studio.hasFile") : t("studio.missingFile"), !!files[k]));
      });
      const runtime = !!(data.world && data.world.runtime);
      box.appendChild(itemEl("runtime", runtime ? t("studio.runtimeLive") : t("studio.runtimeHollow"), runtime));
      await appendLayer(box);
      await appendUnity(box);
    } catch (e) {
      const empty = document.createElement("div");
      empty.className = "empty";
      text(empty, "world: " + errText(e));
      box.appendChild(empty);
    }
  }

  function loopbackViewer(u) {
    try {
      const x = new URL(u, location.origin);
      return x.hostname === "127.0.0.1" || x.hostname === "localhost";
    } catch {
      return false;
    }
  }

  function renderComputer(data) {
    const box = $("computerbox");
    if (!box) return;
    box.replaceChildren();
    const live = (data && data.live) || [];
    const host = (data && data.host) || {};
    const smol = host.smolvm || {};
    if (!live.length) {
      const empty = document.createElement("div");
      empty.className = "empty";
      const bits = [t("studio.computerIdle")];
      if (smol.note) bits.push(String(smol.note));
      text(empty, bits.join(" "));
      box.appendChild(empty);
      return;
    }
    live.forEach((row) => {
      const card = document.createElement("div");
      card.className = "card";
      const title = document.createElement("b");
      text(title, (row.title || row.lab_id || "lab") + " · " + (row.backend || "local"));
      card.appendChild(title);
      const links = document.createElement("div");
      if (row.url) {
        const a = document.createElement("a");
        a.href = row.url;
        a.target = "_blank";
        a.rel = "noopener";
        text(a, t("studio.playHere"));
        links.appendChild(a);
      }
      if (row.viewer_url) {
        const a = document.createElement("a");
        a.href = row.viewer_url;
        a.target = "_blank";
        a.rel = "noopener";
        if (links.childNodes.length) a.style.marginLeft = "0.7rem";
        text(a, t("studio.watch"));
        links.appendChild(a);
      }
      if (links.childNodes.length) card.appendChild(links);
      const embed = (row.viewer_url && loopbackViewer(row.viewer_url))
        ? row.viewer_url
        : (row.url && loopbackViewer(row.url) ? row.url : "");
      if (embed) {
        const frame = document.createElement("div");
        frame.className = "computer-frame";
        const ifr = document.createElement("iframe");
        ifr.src = embed;
        ifr.title = row.viewer_url ? t("studio.computerGuest") : t("studio.computerLocal");
        ifr.setAttribute("sandbox", "allow-scripts allow-same-origin allow-forms");
        frame.appendChild(ifr);
        card.appendChild(frame);
      }
      if (row.warning) {
        const small = document.createElement("small");
        text(small, row.warning);
        card.appendChild(small);
      }
      box.appendChild(card);
    });
  }

  async function refreshComputer() {
    const box = $("computerbox");
    if (!box) return;
    try {
      const data = await api("/api/v1/daedalus/computer", { timeoutMs: 8000 });
      renderComputer(data);
    } catch (e) {
      box.replaceChildren();
      const empty = document.createElement("div");
      empty.className = "empty";
      text(empty, t("studio.computerErr") + ": " + errText(e));
      box.appendChild(empty);
    }
  }
  if ($("pcputer")) {
    $("pcputer").onclick = async () => {
      document.querySelector('[data-pane="world"]').click();
      if (!state.slug) {
        add("bot", t("studio.noProject"));
        return;
      }
      try {
        const data = await api("/api/v1/daedalus/computer", {
          method: "POST",
          body: JSON.stringify({ slug: state.slug }),
          timeoutMs: 180000,
        });
        add("bot", t("studio.computerOk", { id: data.lab_id || data.id || state.slug }));
        await refreshComputer();
      } catch (e) {
        add("bot", t("studio.computerErr") + ": " + errText(e));
      }
    };
  }
  if ($("pcputerstop")) {
    $("pcputerstop").onclick = async () => {
      try {
        await api("/api/v1/daedalus/computer/stop", {
          method: "POST",
          body: JSON.stringify({}),
        });
        await refreshComputer();
      } catch (e) {
        add("bot", t("studio.computerErr") + ": " + errText(e));
      }
    };
  }

  async function refreshConnectors() {
    try {
      const data = await api("/api/v1/daedalus/connectors");
      const lines = [];
      const ch = (data.reach && data.reach.channels) || {};
      lines.push("REACH");
      Object.keys(ch).forEach((k) => {
        lines.push("  " + (ch[k].ready ? "UP  " : "DOWN") + " " + k);
      });
      const mf = data.mirofish || {};
      lines.push("", "MIROFISH " + (mf.ok ? "UP" : "DOWN"));
      const mcp = (data.mcp && data.mcp.servers) || {};
      lines.push("", "MCP  " + (Object.keys(mcp).length ? Object.keys(mcp).join(", ") : "(none)"));
      lines.push(t("studio.configureAt"));
      text($("worldbox"), lines.join("\n"));
    } catch (e) {
      text($("worldbox"), "connectors: " + errText(e));
    }
  }
  $("cxrefresh").onclick = refreshConnectors;
  $("offon").onclick = async () => {
    const cur = $("offon").dataset.on === "1";
    try {
      await api("/api/v1/daedalus/offline", {
        method: "POST",
        body: JSON.stringify({ offline: !cur }),
      });
      refreshHealth();
    } catch (e) {
      add("bot", t("studio.offlineErr") + ": " + errText(e));
    }
  };
  $("uselocal").onclick = async () => {
    try {
      await api("/api/v1/llm/wardrobe/local", {
        method: "POST",
        body: JSON.stringify({ backend: "ollama" }),
      });
      refreshHealth();
    } catch (e) {
      add("bot", t("studio.localErr") + ": " + errText(e));
    }
  };
  $("usecloud").onclick = async () => {
    try {
      await api("/api/v1/llm/wardrobe/cloud", { method: "POST" });
      refreshHealth();
    } catch (e) {
      add("bot", t("studio.cloudErr") + ": " + errText(e));
    }
  };

  async function refreshEvolution() {
    try {
      const data = await api("/api/v1/daedalus/evolution");
      const on = !!data.enabled;
      $("evoon").dataset.on = on ? "1" : "0";
      $("evoon").textContent = on ? t("studio.evoOn") : t("studio.enable");
      if (data.interval_s) $("evointerval").value = data.interval_s;
      $("evoclone").checked = !!data.clone_research;
      const lines = [
        on ? t("studio.modeOn") : t("studio.modeOff"),
        t("studio.intervalLine") + ": " + (data.interval_s || "?") + "s",
        t("studio.gardener") + ": " + (data.gardener_running ? t("studio.gardenerRun") : t("studio.gardenerSleep")),
      ];
      if (data.skip_reason) lines.push(t("studio.nowSkip", { reason: data.skip_reason }));
      const sum = data.last_summary || {};
      if (sum.studied) lines.push(t("studio.lastRepo") + ": " + sum.studied);
      text($("evobox"), lines.join("\n"));
    } catch (e) {
      text($("evobox"), "evolution: " + errText(e));
    }
  }
  $("evoon").onclick = async () => {
    const cur = $("evoon").dataset.on === "1";
    busy($("evoon"), true, "…");
    try {
      await api("/api/v1/daedalus/evolution", {
        method: "POST",
        body: JSON.stringify({
          enabled: !cur,
          interval_s: Number($("evointerval").value) || 1800,
          clone_research: !!$("evoclone").checked,
        }),
      });
      refreshEvolution();
    } catch (e) {
      text($("evobox"), "evolution: " + errText(e));
    } finally {
      busy($("evoon"), false);
    }
  };
  $("evointerval").onchange = async () => {
    try {
      await api("/api/v1/daedalus/evolution", {
        method: "POST",
        body: JSON.stringify({ interval_s: Number($("evointerval").value) || 1800 }),
      });
      refreshEvolution();
    } catch (e) {
      text($("evobox"), "evolution: " + errText(e));
    }
  };
  $("evoclone").onchange = async () => {
    try {
      await api("/api/v1/daedalus/evolution", {
        method: "POST",
        body: JSON.stringify({ clone_research: !!$("evoclone").checked }),
      });
    } catch (e) {
      text($("evobox"), "evolution: " + errText(e));
    }
  };
  $("evocycle").onclick = async () => {
    text($("evobox"), t("studio.cycle"));
    busy($("evocycle"), true, "…");
    try {
      await api("/api/v1/daedalus/evolution/cycle", {
        method: "POST",
        body: JSON.stringify({ force: true }),
        timeoutMs: 120000,
      });
      refreshEvolution();
    } catch (e) {
      text($("evobox"), "evolution: " + errText(e));
    } finally {
      busy($("evocycle"), false);
    }
  };

  $("pcapsule").onclick = () => {
    if (!state.slug) return;
    const href = "/api/v1/playground/projects/" + state.slug + "/capsule";
    window.location = (window.DaedalusAuth && DaedalusAuth.url)
      ? DaedalusAuth.url(href)
      : href;
  };
  $("ppub").onclick = async () => {
    if (!state.slug) return;
    try {
      const data = await api("/api/v1/playground/projects/" + state.slug + "/publish-local", { method: "POST" });
      add("bot", data.ok ? (t("studio.published") + ": " + (data.url || "/apps/" + state.slug + "/")) : (data.detail || JSON.stringify(data)));
    } catch (e) { add("bot", "publish: " + errText(e)); }
  };
  $("plive").onclick = async () => {
    if (!state.slug) return;
    const last = add("bot", t("studio.goingLive"));
    try {
      const data = await api("/api/v1/playground/projects/" + state.slug + "/live", {
        method: "POST",
        body: JSON.stringify({ backend: "auto" }),
        timeoutMs: 120000,
      });
      const bits = [];
      if (data.local_url) bits.push(t("studio.here") + ": " + data.local_url);
      if (data.url) bits.push(t("studio.internet") + ": " + data.url);
      if (data.backend) bits.push(data.backend);
      text(last, (data.note || t("studio.done")) + (bits.length ? "\n" + bits.join("\n") : ""));
    } catch (e) { text(last, t("studio.liveErr") + ": " + errText(e)); }
  };
  $("ptest").onclick = async () => {
    if (!state.slug) return;
    document.querySelector('[data-pane="world"]').click();
    text($("worldbox"), "playtest " + state.slug + "…");
    try {
      const data = await api("/api/v1/daedalus/playtest", {
        method: "POST",
        body: JSON.stringify({ slug: state.slug, n: 4, use_browser: false, with_llm: false }),
        timeoutMs: 120000,
      });
      text($("worldbox"), data.summary || JSON.stringify(data, null, 2));
      add("bot", data.summary || "playtest ready", data.ship_ready ? "ship?" : "blockers");
    } catch (e) {
      text($("worldbox"), "playtest: " + errText(e));
    }
  };
  $("dissectbtn").onclick = async () => {
    if (!state.slug) return;
    const source = ($("dissectsrc").value || "").trim();
    if (!source) { $("dissectsrc").focus(); return; }
    document.querySelector('[data-pane="world"]').click();
    text($("worldbox"), t("studio.dissecting", { source: source }));
    try {
      const data = await api("/api/v1/daedalus/dissect", {
        method: "POST",
        body: JSON.stringify({ source, slug: state.slug, live: true }),
        timeoutMs: 120000,
      });
      text($("worldbox"), data.specimen_block || data.summary || JSON.stringify(data, null, 2));
      add("bot", data.ok ? (t("studio.specimen") + ": " + (data.title || source)) : (data.detail || data.error || t("studio.dissectFail")));
      refreshSavedDissection(true);
      refreshLabs();
    } catch (e) {
      text($("worldbox"), t("studio.dissect") + ": " + errText(e));
    }
  };
  // The project's saved dissection (what the last dissection of this project found) and the labs on this machine: reads only.
  let dissectSlug;
  let dissectGen = 0;
  function emptyRow(box, msg) {
    const empty = document.createElement("div");
    empty.className = "empty";
    text(empty, msg);
    box.appendChild(empty);
  }
  async function refreshSavedDissection(force) {
    const box = $("dissectsaved");
    if (!box) return;
    const slug = state.slug || "";
    if (!force && slug === dissectSlug) return;
    dissectSlug = slug;
    const gen = ++dissectGen;
    box.replaceChildren();
    if (!slug) { emptyRow(box, t("studio.noProject")); return; }
    try {
      const data = await api("/api/v1/playground/projects/" + encodeURIComponent(slug) + "/dissection", { timeoutMs: 8000 });
      if (gen !== dissectGen) return;
      const d = data && data.dissection;
      if (!d) { emptyRow(box, t("studio.dissectNone")); return; }
      const card = itemEl(d.title || d.source || slug, [d.source_kind, d.stack && d.stack.engine].filter(Boolean).join(" · "), false);
      const p = document.createElement("small");
      text(p, d.summary || "");
      card.appendChild(p);
      card.dataset.dissection = slug;
      box.appendChild(card);
    } catch (e) {
      if (gen !== dissectGen) return;
      emptyRow(box, t("studio.dissectReadFail") + ": " + errText(e));
    }
  }
  let labsGen = 0;
  async function refreshLabs() {
    const box = $("labsbox");
    if (!box) return;
    const gen = ++labsGen;                                  // a slower, older answer never repaints over a newer one
    try {
      const data = await api("/api/v1/daedalus/labs", { timeoutMs: 8000 });
      if (gen !== labsGen) return;
      box.replaceChildren();
      const rows = data.labs || [];
      if (!rows.length) { emptyRow(box, t("studio.labsNone")); return; }
      rows.forEach((lab) => {
        const sub = [lab.id, lab.source_kind, lab.playable ? t("studio.labPlayable", { file: lab.playable }) : t("studio.labNotPlayable"),
          lab.vm && lab.vm.status === "running" ? t("studio.labServing") : ""]
          .filter(Boolean).join(" · ");
        const card = itemEl(lab.title || lab.id, sub, false);
        card.dataset.lab = lab.id || "";
        box.appendChild(card);
      });
    } catch (e) {
      if (gen !== labsGen) return;
      box.replaceChildren();
      emptyRow(box, t("studio.labsReadFail") + ": " + errText(e));
    }
  }
  slugWatchers.push(() => refreshSavedDissection(false));

  $("labbtn").onclick = async () => {
    document.querySelector('[data-pane="world"]').click();
    text($("worldbox"), t("studio.labbing"));
    try {
      const data = await api("/api/v1/daedalus/labs", {
        method: "POST",
        body: JSON.stringify({ title: (selectedProject() && selectedProject().name) || "specimen" }),
      });
      const host = await api("/api/v1/daedalus/labs/status");
      text($("worldbox"), JSON.stringify({ lab: data, host }, null, 2));
      add("bot", data.ok ? t("studio.labOk", { id: data.id }) : (data.detail || t("studio.labFail")));
      refreshComputer();
      refreshLabs();
    } catch (e) {
      text($("worldbox"), t("studio.lab") + ": " + errText(e));
    }
  };

  setEffort(state.effort);
  loadMentor();
  loadAssist();
  loadBudget();
  function showGreeting() {
    const existing = msgs.querySelector(".msg.bot[data-greeting]");
    if (existing) { text(existing, t("studio.greeting")); return; }
    const d = add("bot", t("studio.greeting"));
    d.dataset.greeting = "1";
  }
  function relabelChrome() {
    document.querySelectorAll("button[data-idle]").forEach((btn) => { delete btn.dataset.idle; });
    showGreeting();
    syncProjectChrome();
    refreshHealth();
    refreshProjects();
    refreshPrefs();
    refreshLessons();
    refreshSkillIndex();
    refreshSessionPins();
    refreshAddons();
    refreshEvolution();
    refreshWorld();
    refreshComputer();
    previewEffort();
    loadMentor();
    loadAssist();
    loadBudget();
    const shout = $("shout");
    if (shout && !state.lastCwd && (shout.textContent === "Ready." || shout.textContent === "Готов." || shout.textContent === t("studio.ready"))) {
      text(shout, t("studio.ready"));
    }
    const log = $("log");
    if (log && (log.textContent === "No active autopilot." || log.textContent === "Няма активен autopilot." || log.textContent === t("studio.noAutopilot"))) {
      text(log, t("studio.noAutopilot"));
    }
  }
  document.addEventListener("daedalus:langchange", relabelChrome);
  window.dxStudio = {
    api,
    t,
    text,
    errText,
    busy,
    getSlug() { return state.slug; },
    projects() { return state.projects.slice(); },
    selectSlug(slug) {
      pickProject(slug);
    },
    watchSlug(fn) {
      slugWatchers.push(fn);
      fn(slugSnapshot(false));
    },
    setKindFilter(filter) {
      state.kindFilter = filter === "games" || filter === "apps" ? filter : "all";
      renderProjectList();
      syncProjectChrome();
    },
    getKindFilter() { return state.kindFilter; },
    setGameSlugs(slugs) {
      state.gameSlugs = new Set(slugs || []);
      state.gameIndex = "ready";
      renderProjectList();
    },
    failGameIndex() {
      if (state.gameIndex === "ready") return "ready";
      state.gameIndex = "failed";
      state.gameSlugs = null;
      renderProjectList();
      return "failed";
    },
    gameIndexState() { return state.gameIndex; },
    kindOf: projectBucket,
  };
  showGreeting();
  refreshHealth();
  refreshProjects();
  refreshPrefs();
  refreshLessons();
  refreshSkillIndex();
  refreshSessionPins();
  refreshAddons();
  refreshConnectors();
  refreshEvolution();
  refreshComputer();
})();
