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
  };
  localStorage.setItem("dx.session", state.session);

  const msgs = $("msgs");

  function t(key, vars) {
    return (window.DaedalusI18n && DaedalusI18n.t) ? DaedalusI18n.t(key, vars) : key;
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

  function setLiveLabel(node, label) {
    let meta = node.querySelector(".meta");
    if (!meta) {
      meta = document.createElement("span");
      meta.className = "meta";
      node.appendChild(meta);
    }
    text(meta, label);
  }

  function setEffort(next) {
    state.effort = next;
    localStorage.setItem("dx.effort", next);
    document.querySelectorAll(".effort button").forEach((btn) => {
      btn.setAttribute("aria-pressed", btn.dataset.effort === next ? "true" : "false");
    });
    previewEffort();
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
      const data = await api(
        "/api/v1/daedalus/effort?effort=" + encodeURIComponent(state.effort) +
        "&q=" + encodeURIComponent(q.slice(0, 500)),
        { timeoutMs: 8000 }
      );
      text($("effortnote"), t("studio.effort." + (data.tier || "auto")) + " — " + t("studio.effortNote." + (data.tier || "whisper")));
    } catch {
      text($("effortnote"), "");
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
    };
  });
  document.querySelectorAll(".effort button").forEach((btn) => {
    btn.onclick = () => setEffort(btn.dataset.effort);
  });

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
      if (llm.ok) addPill((llm.provider || "?") + " · " + (llm.model || "?"));
      else addPill(t("studio.noLlm"), "err");
      const backends = (local.backends || []).filter((b) => b.ok).map((b) => b.provider);
      if (backends.length) addPill("local " + backends.join("+"));

      const lines = [];
      const prof = w.profile || {};
      lines.push(t("studio.machine") + ": " + (prof.class || "?") + "  RAM " + (prof.ram_gb || "?") + "G");
      if (prof.note) lines.push(prof.note);
      lines.push("ollama: " + (w.ollama ? t("studio.ollamaYes") : t("studio.ollamaNo")));
      const eff = local.effective || {};
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

  async function refreshProjects() {
    try {
      const data = await api("/api/v1/playground/projects");
      state.projects = data.projects || [];
    } catch (e) {
      add("bot", t("studio.projectsErr") + ": " + errText(e));
      state.projects = state.projects || [];
    }
    const box = $("plist");
    box.replaceChildren();
    if (!state.projects.length) {
      const empty = document.createElement("div");
      empty.className = "empty";
      text(empty, t("studio.noProjects"));
      box.appendChild(empty);
    }
    state.projects.forEach((p) => {
      const el = itemEl(p.name || p.slug, p.kind + " · " + p.slug, p.slug === state.slug);
      el.onclick = () => {
        state.slug = p.slug;
        localStorage.setItem("dx.slug", state.slug);
        refreshProjects();
      };
      box.appendChild(el);
    });
    syncProjectChrome();
    refreshWorld();
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
        text(s, it.description || it.origin || "pending");
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
    if (bits.length) setLiveLabel(last, bits.join(" · "));
  }

  async function sendChat() {
    const message = $("prompt").value.trim();
    if (!message || $("send").disabled) return;
    $("prompt").value = "";
    add("user", message);
    const last = add("bot", t("studio.thinking"));
    chatBusy(true);
    try {
      if (window.DaedalusAuth && DaedalusAuth.ensureToken) {
        await DaedalusAuth.ensureToken();
      }
      const job = await api("/api/v1/daedalus/chat/jobs", {
        method: "POST",
        body: JSON.stringify({
          message,
          session_id: state.slug || state.session,
          effort: state.effort,
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
        if (err) text(last, t("studio.errorPrefix") + ": " + errText(err));
        else if (payload && payload.status === "cancelled") text(last, t("studio.stopped"));
        else if (payload && payload.status === "error") text(last, t("studio.errorPrefix") + ": " + (payload.error || t("studio.chatStopped")));
        else applyChatResult(last, payload);
        chatBusy(false);
        refreshPrefs();
        refreshLessons();
        previewEffort();
      };
      src.addEventListener("live", (ev) => {
        try {
          const d = JSON.parse(ev.data);
          setLiveLabel(last, d.label || d.tool || t("studio.thinking"));
        } catch { /* ignore */ }
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
      text(last, t("studio.errorPrefix") + ": " + errText(e));
      chatBusy(false);
    }
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
      box.appendChild(itemEl(t("studio.layer"), layer + " · " + wall, layer === "production" && wall === "clear"));
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
    } catch (e) {
      text($("worldbox"), t("studio.dissect") + ": " + errText(e));
    }
  };
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
    } catch (e) {
      text($("worldbox"), t("studio.lab") + ": " + errText(e));
    }
  };

  setEffort(state.effort);
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
    refreshEvolution();
    refreshWorld();
    previewEffort();
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
  showGreeting();
  refreshHealth();
  refreshProjects();
  refreshPrefs();
  refreshLessons();
  refreshConnectors();
  refreshEvolution();
})();
