(() => {
  const $ = (id) => document.getElementById(id);
  const state = {
    slug: localStorage.getItem("dx.slug") || null,
    session: localStorage.getItem("dx.session") || ("studio-" + Math.random().toString(16).slice(2, 10)),
    effort: localStorage.getItem("dx.effort") || "auto",
    projects: [],
    lastCwd: "",
  };
  localStorage.setItem("dx.session", state.session);

  const msgs = $("msgs");

  function text(node, value) {
    node.textContent = value == null ? "" : String(value);
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
    chip.title = p ? "Кликни за изчистване" : "";
    text(chip, p ? (p.name || p.slug) : "Няма избран проект");
    chip.onclick = () => {
      if (!state.slug) return;
      state.slug = null;
      localStorage.removeItem("dx.slug");
      refreshProjects();
    };
    const locked = !state.slug;
    ["pcapsule", "ppub", "plive", "build", "ptest", "dissectbtn"].forEach((id) => {
      const n = $(id);
      if (n) n.disabled = locked;
    });
    $("cwd").textContent = state.slug
      ? "cwd · playground/projects/" + state.slug
      : "cwd · продукт (избери проект за jail в него)";
  }

  async function previewEffort() {
    const q = $("prompt").value.trim();
    try {
      const data = await (await fetch(
        "/api/v1/daedalus/effort?effort=" + encodeURIComponent(state.effort) +
        "&q=" + encodeURIComponent(q.slice(0, 500))
      )).json();
      text($("effortnote"), (data.label || "") + " — " + (data.note || ""));
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
      document.querySelectorAll(".tabs button").forEach((b) => b.classList.remove("on"));
      document.querySelectorAll(".pane").forEach((p) => p.classList.remove("on"));
      btn.classList.add("on");
      $("pane-" + btn.dataset.pane).classList.add("on");
    };
  });
  document.querySelectorAll(".effort button").forEach((btn) => {
    btn.onclick = () => setEffort(btn.dataset.effort);
  });

  async function refreshHealth() {
    try {
      const [h, llm, local, w] = await Promise.all([
        fetch("/health").then((r) => r.json()),
        fetch("/api/v1/llm/status").then((r) => r.json()).catch(() => ({})),
        fetch("/api/v1/llm/local").then((r) => r.json()).catch(() => ({})),
        fetch("/api/v1/llm/wardrobe").then((r) => r.json()).catch(() => ({})),
      ]);
      const pills = $("pills");
      pills.replaceChildren();
      const addPill = (label, cls) => {
        const s = document.createElement("span");
        s.className = "pill" + (cls ? " " + cls : "");
        text(s, label);
        pills.appendChild(s);
      };
      addPill(h.standalone ? "standalone" : "adn");
      if (w.wardrobe && w.wardrobe.offline) addPill("offline", "warn");
      if (llm.ok) addPill((llm.provider || "?") + " · " + (llm.model || "?"));
      else addPill("няма LLM", "err");
      const backends = (local.backends || []).filter((b) => b.ok).map((b) => b.provider);
      if (backends.length) addPill("local " + backends.join("+"));

      const lines = [];
      const prof = w.profile || {};
      lines.push("машина: " + (prof.class || "?") + "  RAM " + (prof.ram_gb || "?") + "G");
      if (prof.note) lines.push(prof.note);
      lines.push("ollama: " + (w.ollama ? "да" : "не — ollama.com/download"));
      const eff = local.effective || {};
      lines.push("активен: " + (eff.provider || "?") + " / " + (eff.model || "?"));
      (local.backends || []).forEach((b) => lines.push((b.ok ? "UP  " : "DOWN") + " " + b.provider));
      text($("llmbox"), lines.join("\n"));

      const box = $("wardrobe");
      box.replaceChildren();
      (w.catalog || []).forEach((row) => {
        const mark = row.installed ? "локално" : (row.fits ? "събира се" : "тежък");
        const el = itemEl(row.label, row.role + " · " + row.ollama + " · " + mark, !!row.recommended);
        el.title = row.blurb || "";
        el.onclick = async () => {
          if (!row.installed && w.ollama) {
            if (!confirm("Да сваля " + row.ollama + " през Ollama? Може да е няколко GB.")) return;
            text($("llmbox"), "дърпам " + row.ollama + "…");
            await fetch("/api/v1/llm/wardrobe/pull", {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ id: row.id, confirm: true }),
            });
          } else if (!row.installed && !w.ollama) {
            add("bot", "Инсталирай Ollama, после кликни пак. ollama pull " + row.ollama);
            return;
          }
          await fetch("/api/v1/llm/wardrobe/activate", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ id: row.id }),
          });
          refreshHealth();
        };
        box.appendChild(el);
      });
      $("offon").textContent = (w.wardrobe && w.wardrobe.offline) ? "Офлайн ВКЛ" : "Офлайн кит";
    } catch {
      $("pills").replaceChildren();
      const s = document.createElement("span");
      s.className = "pill err";
      text(s, "offline");
      $("pills").appendChild(s);
    }
  }

  async function refreshProjects() {
    const data = await (await fetch("/api/v1/playground/projects")).json();
    state.projects = data.projects || [];
    const box = $("plist");
    box.replaceChildren();
    if (!state.projects.length) {
      const empty = document.createElement("div");
      empty.className = "empty";
      text(empty, "Няма проекти. Ново име вляво — Дедал прави папка, не презентация.");
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
    const data = await (await fetch("/api/v1/daedalus/preferences")).json();
    const box = $("preflist");
    box.replaceChildren();
    const items = data.items || [];
    if (!items.length) {
      const empty = document.createElement("div");
      empty.className = "empty";
      text(empty, "Няма записани предпочитания.");
      box.appendChild(empty);
    }
    items.slice(0, 12).forEach((it) => {
      const el = itemEl(it.text || "", it.kind || "preference", false);
      el.title = "Кликни за изтриване";
      el.onclick = async () => {
        await fetch("/api/v1/daedalus/preferences/" + it.id, { method: "DELETE" });
        refreshPrefs();
      };
      box.appendChild(el);
    });
  }

  $("pcreate").onclick = async () => {
    const name = $("pname").value.trim();
    if (!name) return;
    const description = ($("pdesc").value.trim() || $("prompt").value.trim() || name);
    const res = await (await fetch("/api/v1/playground/projects", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        name,
        kind: $("pkind").value,
        description,
        effort: state.effort,
      }),
    })).json();
    if (!res.project) {
      add("bot", res.detail || "Не създаде проект.");
      return;
    }
    state.slug = res.project.slug;
    localStorage.setItem("dx.slug", state.slug);
    $("pname").value = "";
    $("pdesc").value = "";
    await refreshProjects();
    if (state.effort === "epic" || /кампани|необят|персонаж|сюжет/i.test(description)) {
      add("bot", "Епос: има world kit (bible, карта, персонажи, нива). Пиши една среза, не целия континент.");
    }
  };

  $("prefadd").onclick = async () => {
    const value = $("preftext").value.trim();
    if (!value) return;
    await fetch("/api/v1/daedalus/preferences", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: value }),
    });
    $("preftext").value = "";
    refreshPrefs();
  };

  async function sendChat() {
    const message = $("prompt").value.trim();
    if (!message || $("send").disabled) return;
    $("prompt").value = "";
    $("send").disabled = true;
    add("user", message);
    const last = add("bot", "…мисля");
    try {
      const res = await (await fetch("/api/v1/daedalus/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          message,
          session_id: state.slug || state.session,
          effort: state.effort,
        }),
      })).json();
      text(last, res.reply || res.message || JSON.stringify(res, null, 2));
      const bits = [];
      if (res.effort && res.effort.label) bits.push(res.effort.label);
      if (res.complete === false) bits.push("незавършено");
      if (res.complete === true) bits.push("готово");
      if (res.model) bits.push(res.model);
      if (res.elapsed_seconds != null) bits.push(res.elapsed_seconds + "s");
      if (bits.length) {
        const m = document.createElement("span");
        m.className = "meta";
        text(m, bits.join(" · "));
        last.appendChild(m);
      }
      refreshPrefs();
      previewEffort();
    } catch (e) {
      text(last, "Грешка: " + e);
    } finally {
      $("send").disabled = false;
    }
  }

  $("send").onclick = sendChat;
  $("prompt").addEventListener("keydown", (ev) => {
    if (ev.key === "Enter" && !ev.shiftKey) { ev.preventDefault(); sendChat(); }
  });
  $("prompt").addEventListener("input", scheduleEffortPreview);

  $("build").onclick = async () => {
    if (!state.slug) { add("bot", "Избери или създай проект."); return; }
    document.querySelector('[data-pane="flight"]').click();
    const description = $("prompt").value.trim() || ("Build " + state.slug);
    const res = await (await fetch("/api/v1/playground/projects/" + state.slug + "/autopilot", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ description, mode: "full", effort: state.effort }),
    })).json();
    text($("log"), "job " + (res.job && res.job.id) + "\n");
    const src = new EventSource("/api/v1/playground/projects/" + state.slug + "/autopilot/stream");
    src.onmessage = (ev) => { $("log").textContent += ev.data + "\n"; };
    src.addEventListener("log", (ev) => { $("log").textContent += ev.data + "\n"; });
    src.addEventListener("phase", (ev) => { $("log").textContent += "phase " + ev.data + "\n"; });
    src.addEventListener("done", (ev) => { $("log").textContent += "DONE " + ev.data + "\n"; src.close(); });
    src.addEventListener("error", () => { src.close(); });
  };

  $("shrun").onclick = async () => {
    const command = $("shcmd").value.trim();
    if (!command) return;
    text($("shout"), "$ " + command + "\n…");
    const res = await (await fetch("/api/v1/daedalus/shell", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ command, slug: state.slug || "" }),
    })).json();
    state.lastCwd = res.cwd || "";
    if (state.lastCwd) text($("cwd"), "cwd · " + state.lastCwd);
    text($("shout"),
      "$ " + command + "  (exit " + res.exit_code + ", " + (res.elapsed_ms || 0) + "ms)\n" +
      (res.cwd ? "# " + res.cwd + "\n" : "") +
      (res.stdout || "") + (res.stderr ? "\n" + res.stderr : "") +
      (res.error && !res.ok ? "\n" + res.error : ""));
  };
  $("shcmd").addEventListener("keydown", (ev) => {
    if (ev.key === "Enter") { ev.preventDefault(); $("shrun").click(); }
  });

  async function refreshWorld() {
    const box = $("worldkit");
    if (!box) return;
    box.replaceChildren();
    if (!state.slug) {
      const empty = document.createElement("div");
      empty.className = "empty";
      text(empty, "Избери проект. Епос пише kit при създаване.");
      box.appendChild(empty);
      return;
    }
    try {
      const data = await (await fetch("/api/v1/playground/projects/" + state.slug + "/world")).json();
      const files = (data.world && data.world.files) || {};
      const keys = Object.keys(files);
      if (!keys.length) {
        const empty = document.createElement("div");
        empty.className = "empty";
        text(empty, "Няма kit. Епос + ново име, или Autopilot с кампания.");
        box.appendChild(empty);
        return;
      }
      keys.forEach((k) => {
        box.appendChild(itemEl(k, files[k] ? "има" : "липсва", !!files[k]));
      });
    } catch (e) {
      const empty = document.createElement("div");
      empty.className = "empty";
      text(empty, "world: " + e);
      box.appendChild(empty);
    }
  }

  async function refreshConnectors() {
    try {
      const data = await (await fetch("/api/v1/daedalus/connectors")).json();
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
      text($("worldbox"), lines.join("\n"));
    } catch (e) {
      text($("worldbox"), "connectors: " + e);
    }
  }
  $("cxrefresh").onclick = refreshConnectors;
  $("offon").onclick = async () => {
    const cur = ($("offon").textContent || "").indexOf("ВКЛ") >= 0;
    await fetch("/api/v1/daedalus/offline", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ offline: !cur }),
    });
    refreshHealth();
  };
  $("usecloud").onclick = async () => {
    await fetch("/api/v1/llm/wardrobe/cloud", { method: "POST" });
    refreshHealth();
  };

  async function refreshEvolution() {
    try {
      const data = await (await fetch("/api/v1/daedalus/evolution")).json();
      const on = !!data.enabled;
      $("evoon").textContent = on ? "Еволюция ВКЛ" : "Включи";
      if (data.interval_s) $("evointerval").value = data.interval_s;
      $("evoclone").checked = !!data.clone_research;
      const lines = [
        on ? "режим: включен" : "режим: изключен",
        "интервал: " + (data.interval_s || "?") + "s",
        "градинар: " + (data.gardener_running ? "работи" : "спи"),
      ];
      if (data.skip_reason) lines.push("сега: skip (" + data.skip_reason + ")");
      const sum = data.last_summary || {};
      if (sum.studied) lines.push("последно репо: " + sum.studied);
      text($("evobox"), lines.join("\n"));
    } catch (e) {
      text($("evobox"), "evolution: " + e);
    }
  }
  $("evoon").onclick = async () => {
    const cur = ($("evoon").textContent || "").indexOf("ВКЛ") >= 0;
    await fetch("/api/v1/daedalus/evolution", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        enabled: !cur,
        interval_s: Number($("evointerval").value) || 1800,
        clone_research: !!$("evoclone").checked,
      }),
    });
    refreshEvolution();
  };
  $("evointerval").onchange = async () => {
    await fetch("/api/v1/daedalus/evolution", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ interval_s: Number($("evointerval").value) || 1800 }),
    });
    refreshEvolution();
  };
  $("evoclone").onchange = async () => {
    await fetch("/api/v1/daedalus/evolution", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ clone_research: !!$("evoclone").checked }),
    });
  };
  $("evocycle").onclick = async () => {
    text($("evobox"), "цикъл…");
    await fetch("/api/v1/daedalus/evolution/cycle", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ force: true }),
    });
    refreshEvolution();
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
      const data = await (await fetch("/api/v1/playground/projects/" + state.slug + "/publish-local", { method: "POST" })).json();
      add("bot", data.ok ? ("Публикувано: " + (data.url || "/apps/" + state.slug + "/")) : (data.detail || JSON.stringify(data)));
    } catch (e) { add("bot", "publish: " + e); }
  };
  $("plive").onclick = async () => {
    if (!state.slug) return;
    const last = add("bot", "Качвам наживо…");
    try {
      const data = await (await fetch("/api/v1/playground/projects/" + state.slug + "/live", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ backend: "auto" }),
      })).json();
      const bits = [];
      if (data.local_url) bits.push("тук: " + data.local_url);
      if (data.url) bits.push("интернет: " + data.url);
      if (data.backend) bits.push(data.backend);
      text(last, (data.note || "готово") + (bits.length ? "\n" + bits.join("\n") : ""));
    } catch (e) { text(last, "наживо: " + e); }
  };
  $("ptest").onclick = async () => {
    if (!state.slug) return;
    document.querySelector('[data-pane="world"]').click();
    text($("worldbox"), "playtest " + state.slug + "…");
    try {
      const data = await (await fetch("/api/v1/daedalus/playtest", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ slug: state.slug, n: 4, use_browser: false, with_llm: false }),
      })).json();
      text($("worldbox"), data.summary || JSON.stringify(data, null, 2));
      add("bot", data.summary || "playtest готов", data.ship_ready ? "ship?" : "blockers");
    } catch (e) {
      text($("worldbox"), "playtest: " + e);
    }
  };
  $("dissectbtn").onclick = async () => {
    if (!state.slug) return;
    const source = ($("dissectsrc").value || "").trim();
    if (!source) { $("dissectsrc").focus(); return; }
    document.querySelector('[data-pane="world"]').click();
    text($("worldbox"), "дисекция " + source + "…");
    try {
      const data = await (await fetch("/api/v1/daedalus/dissect", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ source, slug: state.slug, live: true }),
      })).json();
      text($("worldbox"), data.specimen_block || data.summary || JSON.stringify(data, null, 2));
      add("bot", data.ok ? ("Мостра: " + (data.title || source)) : (data.detail || data.error || "дисекцията не мина"));
    } catch (e) {
      text($("worldbox"), "дисекция: " + e);
    }
  };
  $("labbtn").onclick = async () => {
    document.querySelector('[data-pane="world"]').click();
    text($("worldbox"), "лаборатория…");
    try {
      const data = await (await fetch("/api/v1/daedalus/labs", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title: (selectedProject() && selectedProject().name) || "specimen" }),
      })).json();
      const host = await (await fetch("/api/v1/daedalus/labs/status")).json();
      text($("worldbox"), JSON.stringify({ lab: data, host }, null, 2));
      add("bot", data.ok ? ("Лаборатория " + data.id + " — инсталирай HTML5/zip в нея, после дисекция.") : (data.detail || "лабораторията не се създаде"));
    } catch (e) {
      text($("worldbox"), "лаборатория: " + e);
    }
  };

  setEffort(state.effort);
  add("bot", "Здравей. Аз съм Дедал. Шепот за разговор, занаят за една играема среза, епос за свят с глави и персонажи.");
  refreshHealth();
  refreshProjects();
  refreshPrefs();
  refreshConnectors();
  refreshEvolution();
})();
