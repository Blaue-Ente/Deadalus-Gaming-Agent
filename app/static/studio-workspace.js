(() => {
  const dx = window.dxStudio;
  if (!dx) return;

  const PANELS = ["chat", "build", "preview", "review", "settings", "world", "tools"];
  const TABS = ["proposals", "skills", "bundles", "permissions", "outbox"];
  let currentPanel = "chat";
  let currentTab = "proposals";
  let queryReady = false;
  let flight = "";
  let fileGen = 0;
  let streamGen = 0;
  let streamCtrl = null;
  let streamingSlug = "";
  let resumeToken = 0;

  function $(id) { return document.getElementById(id); }

  function failText(err) {
    if (err && (err.name === "AbortError" || /aborted|timeout/i.test(String(err.message || "")))) {
      return dx.t("studio.timeout");
    }
    return dx.errText(err);
  }

  function showStatus(id, message, bad) {
    const el = $(id);
    if (!el) return;
    const msg = message || "";
    el.hidden = !msg;
    el.dataset.state = bad ? "error" : "ok";
    el.textContent = msg;
  }

  function readQuery() {
    const q = new URLSearchParams(location.search);
    let panel = q.get("panel") || "chat";
    if (PANELS.indexOf(panel) < 0 || panel === "tools") panel = "chat";
    let tab = q.get("tab") || "proposals";
    if (TABS.indexOf(tab) < 0) tab = "proposals";
    return { panel, tab, slug: q.get("slug") || "" };
  }

  function writeQuery() {
    const q = new URLSearchParams();
    if (currentPanel !== "chat" && currentPanel !== "tools") q.set("panel", currentPanel);
    if (currentPanel === "review" && currentTab && currentTab !== "proposals") q.set("tab", currentTab);
    const slug = dx.getSlug();
    if (slug) q.set("slug", slug);
    const next = q.toString();
    history.replaceState(null, "", next ? "/?" + next : "/");
  }

  function narrow() {
    return window.matchMedia("(max-width: 1100px)").matches;
  }

  function selectRail(pane) {
    const btn = document.querySelector('.tabs button[data-pane="' + pane + '"]');
    if (btn) btn.click();
  }

  function showPanel(panel, tab) {
    if (PANELS.indexOf(panel) < 0) panel = "chat";
    currentPanel = panel;
    if (tab && TABS.indexOf(tab) >= 0) currentTab = tab;
    const shell = document.querySelector(".shell");
    const tools = panel === "tools" || (panel === "world" && narrow());
    shell.classList.toggle("tools-open", tools);
    document.querySelectorAll(".workspace-panel").forEach((el) => {
      if (panel === "world" && !tools) el.hidden = el.dataset.panel !== "chat";
      else el.hidden = tools || el.dataset.panel !== panel;
    });
    if (panel === "world") selectRail("world");
    if (panel === "tools") selectRail("shell");
    document.querySelectorAll("#workspace-nav button").forEach((btn) => {
      btn.setAttribute("aria-selected", btn.dataset.panel === panel ? "true" : "false");
    });
    document.querySelectorAll("#review-tabs button").forEach((btn) => {
      btn.setAttribute("aria-selected", btn.dataset.tab === currentTab ? "true" : "false");
    });
    if (panel === "build") loadWorkspace(dx.getSlug());
    if (panel === "preview") loadPreviewStatus(dx.getSlug());
    if (panel === "review") loadReview(currentTab);
    if (panel === "settings") loadSettings();
    if (queryReady) writeQuery();
  }

  function slugPath(slug) {
    return encodeURIComponent(slug || "");
  }

  function setKindButtons(on) {
    document.querySelectorAll("#kind-filter button[data-filter='games'], #kind-filter button[data-filter='apps']").forEach((btn) => {
      btn.disabled = !on;
    });
  }

  async function loadGameIndex() {
    const status = $("workspace-status");
    try {
      const data = await dx.api("/admin/daedalus/game-studio/api/projects");
      const rows = data && Array.isArray(data.projects) ? data.projects : null;
      if (!rows) throw new Error(dx.t("studio.requestFailed"));
      dx.setGameSlugs(rows.map((p) => p.slug).filter(Boolean));
      setKindButtons(true);
      if (status && status.dataset.reason === "index") {
        status.dataset.reason = "";
        showStatus("workspace-status", "", false);
      }
    } catch (err) {
      const kept = dx.gameIndexState() === "ready";
      if (!kept) dx.failGameIndex();
      setKindButtons(true);
      const note = kept ? dx.t("studio.requestFailed") : dx.t("studio.indexFallback");
      showStatus("workspace-status", note + " " + failText(err), true);
      if (status) status.dataset.reason = "index";
    }
  }

  function syncFilter() {
    const current = dx.getKindFilter();
    document.querySelectorAll("#kind-filter button").forEach((btn) => {
      btn.setAttribute("aria-pressed", btn.dataset.filter === current ? "true" : "false");
    });
  }

  async function loadFiles(slug) {
    const gen = ++fileGen;
    const list = $("filelist");
    const assets = $("assetlist");
    const view = $("fileview");
    $("project-delete").disabled = !slug;
    if (!slug) {
      list.replaceChildren();
      assets.replaceChildren();
      view.textContent = "";
      return;
    }
    try {
      const data = await dx.api("/admin/daedalus/playground/api/projects/" + slugPath(slug) + "/files-snapshot");
      if (gen !== fileGen) return;
      const tree = data.tree || [];
      list.replaceChildren();
      view.textContent = "";
      if (!tree.length) {
        const empty = document.createElement("div");
        empty.className = "empty";
        empty.textContent = dx.t("studio.noFiles");
        list.appendChild(empty);
      }
      tree.forEach((file) => {
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "item file";
        btn.textContent = file.path || "";
        btn.addEventListener("click", () => openFile(slug, file.path || ""));
        list.appendChild(btn);
      });
    } catch (err) {
      showStatus("file-status", dx.t("studio.requestFailed") + ": " + failText(err), true);
    }
    try {
      const data = await dx.api("/admin/daedalus/playground/api/projects/" + slugPath(slug) + "/assets");
      if (gen !== fileGen) return;
      const rows = data.assets || [];
      assets.replaceChildren();
      if (!rows.length) {
        const empty = document.createElement("div");
        empty.className = "empty";
        empty.textContent = dx.t("studio.noAssets");
        assets.appendChild(empty);
      }
      rows.forEach((asset) => {
        const row = document.createElement("div");
        row.className = "item";
        row.textContent = typeof asset === "string" ? asset : (asset.path || asset.name || "");
        assets.appendChild(row);
      });
    } catch (err) {
      showStatus("file-status", dx.t("studio.requestFailed") + ": " + failText(err), true);
    }
  }

  async function openFile(slug, path) {
    try {
      const data = await dx.api(
        "/admin/daedalus/playground/api/projects/" + slugPath(slug) + "/file?path=" + encodeURIComponent(path)
      );
      $("fileview").textContent = data.content || "";
      showStatus("file-status", "", false);
    } catch (err) {
      $("fileview").textContent = "";
      showStatus("file-status", dx.t("studio.requestFailed") + ": " + failText(err), true);
    }
  }

  function brainstormBody() {
    const idea = ($("build-idea").value || "").trim();
    const slug = dx.getSlug();
    const project = (dx.projects() || []).filter((p) => p.slug === slug)[0];
    const title = (project && project.name) || idea.slice(0, 80);
    return {
      idea,
      title,
      body: {
        name: title,
        slug: slug || "",
        build_mode: $("build-mode").value || "guided",
        brainstorm: {
          title,
          idea,
          genre: $("build-genre").value || "other",
          audience: $("build-audience").value || "casual",
          platform_hint: $("build-platform").value || "web",
        },
      },
    };
  }

  async function runAction(button, statusId, work) {
    dx.busy(button, true, "…");
    try {
      await work();
    } catch (err) {
      showStatus(statusId, dx.t("studio.requestFailed") + ": " + failText(err), true);
    } finally {
      dx.busy(button, false);
    }
  }

  function fillSelect(id, values) {
    const sel = $(id);
    const current = sel.value;
    sel.replaceChildren();
    (values || []).forEach((value) => {
      const opt = document.createElement("option");
      if (value && typeof value === "object") {
        opt.value = value.id || value.value || "";
        opt.textContent = value.label || opt.value;
      } else {
        opt.value = String(value);
        opt.textContent = String(value);
      }
      sel.appendChild(opt);
    });
    if (current) sel.value = current;
  }

  async function loadCatalog() {
    try {
      const data = await dx.api("/admin/daedalus/game-studio/api/catalog");
      fillSelect("build-genre", data.genres || []);
      fillSelect("build-audience", data.audiences || []);
      fillSelect("build-platform", data.platform_hints || []);
      fillSelect("build-mode", data.build_modes || ["guided", "autopilot", "prototype"]);
    } catch (err) {
      showStatus("build-status", dx.t("studio.requestFailed") + ": " + failText(err), true);
    }
  }

  function milestoneLabel(row) {
    if (typeof row === "string") return row;
    const id = row.id || row.name || "";
    const title = row.title || "";
    let progress = row.status || "";
    if (typeof row.done === "number" && typeof row.total === "number") {
      progress = row.done + "/" + row.total;
    }
    return [id, title, progress].filter(Boolean).join(" ");
  }

  function paintMilestones(data) {
    const list = $("build-milestones");
    list.replaceChildren();
    const summary = data.milestone_summary || {};
    const rows = Array.isArray(summary.milestones) && summary.milestones.length
      ? summary.milestones
      : (Array.isArray(data.milestones) ? data.milestones : []);
    if (!rows.length && data.current_milestone) {
      const li = document.createElement("li");
      li.textContent = String(data.current_milestone);
      list.appendChild(li);
    }
    rows.forEach((row) => {
      const li = document.createElement("li");
      li.textContent = milestoneLabel(row);
      if (row && row.current) li.dataset.current = "1";
      list.appendChild(li);
    });
    if (typeof summary.stages_done === "number" && typeof summary.stages_total === "number") {
      const li = document.createElement("li");
      li.dataset.progress = "stages";
      li.textContent = summary.stages_done + "/" + summary.stages_total;
      list.appendChild(li);
    }
    $("build-guided").disabled = !data.can_guided;
    $("build-autopilot").disabled = !data.can_autopilot;
    $("build-gates").disabled = false;
    $("build-verify").disabled = false;
    $("build-advance").disabled = !data.can_advance;
  }

  function appendBuild(line) {
    if (!line) return;
    const out = $("build-out");
    out.textContent += (out.textContent ? "\n" : "") + line;
    out.scrollTop = out.scrollHeight;
    const log = $("log");
    if (log) log.textContent += line + "\n";
  }

  function closeBuildStream() {
    streamGen += 1;
    streamingSlug = "";
    if (streamCtrl) {
      try { streamCtrl.abort(); } catch (err) { /* already closed */ }
      streamCtrl = null;
    }
  }

  function streamLine(event, data) {
    if (event === "log") return data.line || "";
    if (event === "stage" && data.stage_id) return "#" + data.stage_id;
    if (event === "stage_done" && data.stage_id) {
      return "#" + data.stage_id + " " + (data.ok === false ? "!" : dx.t("studio.done"));
    }
    if (event === "phase") return data.phase || data.name || "";
    if (event === "checkpoint") return data.review || data.message || dx.t("studio.checkpoint");
    if (event === "milestone_done") return (data.milestone_id || "") + " " + dx.t("studio.done");
    if (event === "done") return dx.t("studio.done");
    if (event === "error") return data.message || data.error || dx.t("studio.streamStopped");
    return "";
  }

  function parseSse(chunk) {
    const parts = chunk.split("\n\n");
    return parts.map((block) => {
      const ev = (block.match(/^event: (.+)/m) || [])[1] || "message";
      const raw = (block.match(/^data: (.+)/m) || [])[1] || "";
      if (!raw && ev === "message") return null;
      let data = {};
      try { data = raw ? JSON.parse(raw) : {}; }
      catch (err) { data = { line: raw }; }
      return { event: ev, data };
    }).filter(Boolean);
  }

  async function readBuildStream(url, gen) {
    const ctrl = new AbortController();
    streamCtrl = ctrl;
    const res = await fetch(url, { signal: ctrl.signal, headers: { Accept: "text/event-stream" } });
    if (!res.ok) throw new Error("HTTP " + res.status);
    let buf = "";
    let terminal = "";
    const apply = (block) => {
      const line = streamLine(block.event, block.data || {});
      if (line) appendBuild(line);
      if (block.event === "error") showStatus("build-status", line || dx.t("studio.streamStopped"), true);
      else if (line) showStatus("build-status", line, false);
      return block.event;
    };
    const take = (text, flush) => {
      const parts = text.split("\n\n");
      const rest = flush ? "" : (parts.pop() || "");
      const body = parts.join("\n\n");
      if (!body) return rest;
      for (const block of parseSse(body)) {
        terminal = apply(block);
        if (terminal === "checkpoint" || terminal === "done" || terminal === "error" || terminal === "milestone_done") {
          return rest;
        }
      }
      return rest;
    };
    const stopped = () => terminal === "checkpoint" || terminal === "done" || terminal === "error" || terminal === "milestone_done";
    if (!res.body || !res.body.getReader) {
      take((await res.text()).replace(/\r\n/g, "\n"), true);
      return terminal;
    }
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    while (gen === streamGen) {
      const step = await reader.read();
      buf += decoder.decode(step.value || new Uint8Array(), { stream: !step.done }).replace(/\r\n/g, "\n");
      buf = take(buf, !!step.done);
      if (stopped()) return terminal;
      if (step.done) break;
    }
    return terminal;
  }

  function followBuild(kind, slug, jobId) {
    closeBuildStream();
    const gen = streamGen;
    streamingSlug = slug;
    flight = kind;
    $("build-stop").hidden = false;
    $("build-out").textContent = "";
    const url = kind === "autopilot"
      ? "/api/v1/playground/projects/" + slugPath(slug) + "/autopilot/stream"
      : "/admin/daedalus/playground/api/projects/" + slugPath(slug) + "/guided/stream?job_id=" + encodeURIComponent(jobId || "") + "&from=0";
    readBuildStream(url, gen).then(async (terminal) => {
      if (gen !== streamGen) return;
      if (terminal === "done" || terminal === "error" || terminal === "milestone_done") {
        $("build-stop").hidden = true;
      }
      if (terminal === "done" || terminal === "milestone_done") await loadWorkspace(slug, { quiet: true });
    }).catch((err) => {
      if (gen !== streamGen) return;
      if (err && err.name === "AbortError") return;
      appendBuild(dx.t("studio.streamCut"));
      showStatus("build-status", dx.t("studio.streamCut") + " " + failText(err), true);
    }).finally(() => {
      if (gen === streamGen) {
        streamingSlug = "";
        streamCtrl = null;
      }
    });
  }

  async function resumeBuild(slug) {
    if (!slug || streamingSlug === slug) return;
    const token = ++resumeToken;
    try {
      const guided = await dx.api("/admin/daedalus/playground/api/projects/" + slugPath(slug) + "/guided/active");
      if (token !== resumeToken || streamingSlug) return;
      if (guided && guided.active && guided.job_id) {
        followBuild("guided", slug, guided.job_id);
        return;
      }
    } catch (err) { /* no guided job is not a failed build */ }
    if (token !== resumeToken || streamingSlug) return;
    try {
      const auto = await dx.api("/api/v1/playground/projects/" + slugPath(slug) + "/autopilot");
      if (token !== resumeToken || streamingSlug) return;
      const job = auto && auto.job;
      if (job && job.id && job.status === "running") followBuild("autopilot", slug, job.id);
    } catch (err) { /* no autopilot job */ }
  }

  async function loadWorkspace(slug, opts) {
    $("build-slug").textContent = slug ? dx.t("studio.project") + ": " + slug : "";
    $("build-guided").disabled = true;
    $("build-autopilot").disabled = true;
    $("build-gates").disabled = true;
    $("build-verify").disabled = true;
    $("build-advance").disabled = true;
    if (!slug) return;
    try {
      const data = await dx.api("/admin/daedalus/game-studio/api/projects/" + slugPath(slug));
      paintMilestones(data);
      if (!streamingSlug) showStatus("build-status", "", false);
    } catch (err) {
      $("build-milestones").replaceChildren();
      if (!streamingSlug) showStatus("build-status", failText(err), true);
    }
    if (!opts || !opts.quiet) resumeBuild(slug);
  }

  async function postJson(path, body) {
    return dx.api(path, { method: "POST", body: JSON.stringify(body || {}) });
  }

  function requireIdea() {
    if (!($("build-idea").value || "").trim()) {
      showStatus("build-status", dx.t("studio.needIdea"), true);
      return false;
    }
    return true;
  }

  function paintResult(data) {
    if (data && data.harness_check) return dx.t("studio.harnessCheck");
    if (data && data.ok === false) return dx.t("studio.notPublished") + ": " + (data.error || "");
    const bits = [];
    if (data && data.slug) bits.push(data.slug);
    if (data && data.current_milestone) bits.push(String(data.current_milestone));
    if (data && data.error) bits.push(String(data.error));
    return bits.join("\n");
  }

  async function loadPreviewStatus(slug) {
    if (!slug) {
      showStatus("preview-status", dx.t("studio.previewEmpty"), false);
      return;
    }
    try {
      const data = await dx.api("/admin/daedalus/playground/api/projects/" + slugPath(slug) + "/logs");
      const running = !!(data.status && typeof data.status === "object");
      showStatus("preview-status", running ? dx.t("studio.started") : dx.t("studio.previewEmpty"), false);
    } catch (err) {
      showStatus("preview-status", dx.t("studio.requestFailed") + ": " + failText(err), true);
    }
  }

  function emptyNote(list) {
    const empty = document.createElement("div");
    empty.className = "empty";
    empty.textContent = dx.t("studio.emptyList");
    list.appendChild(empty);
  }

  function actionButton(act, id, label) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.dataset.act = act;
    btn.dataset.id = id;
    btn.textContent = label;
    return btn;
  }

  function row(title, sub, buttons) {
    const el = document.createElement("div");
    el.className = "item";
    const b = document.createElement("b");
    b.textContent = title || "";
    const s = document.createElement("small");
    s.textContent = sub || "";
    const actions = document.createElement("div");
    actions.className = "row tight";
    buttons.forEach((btn) => actions.appendChild(btn));
    el.append(b, s, actions);
    return el;
  }

  async function loadReview(tab) {
    currentTab = tab || currentTab;
    const list = $("review-list");
    list.replaceChildren();
    showStatus("review-status", dx.t("studio.loading"), false);
    try {
      if (currentTab === "proposals") {
        const data = await dx.api("/admin/daedalus/proposals/api");
        const rows = data.proposals || [];
        if (!rows.length) emptyNote(list);
        rows.forEach((item) => {
          list.appendChild(row(item.reason || item.id || "", item.status || "", [
            actionButton("approve", item.id, dx.t("studio.approve")),
            actionButton("reject", item.id, dx.t("studio.reject")),
            actionButton("rollback", item.id, dx.t("studio.rollback")),
          ]));
        });
      } else if (currentTab === "skills") {
        const data = await dx.api("/admin/daedalus/skills/api");
        const rows = data.skills || [];
        if (!rows.length) emptyNote(list);
        rows.forEach((item) => {
          list.appendChild(row(item.label || item.name || "", item.enabled ? dx.t("studio.settingsSet") : dx.t("studio.settingsUnset"), [
            actionButton("toggle", item.name, dx.t("studio.toggle")),
          ]));
        });
      } else if (currentTab === "bundles") {
        const data = await dx.api("/admin/daedalus/bundles/api");
        const rows = data.bundles || [];
        if (!rows.length) emptyNote(list);
        rows.forEach((item) => {
          list.appendChild(row(item.title || item.name || item.id || "", item.status || "", [
            actionButton("apply", item.id, dx.t("studio.apply")),
            actionButton("bundle-reject", item.id, dx.t("studio.reject")),
          ]));
        });
      } else if (currentTab === "permissions") {
        const data = await dx.api("/admin/daedalus/permissions");
        const active = {};
        (data.permissions || []).forEach((item) => {
          if (item.active) active[item.perm_class] = true;
        });
        const catalog = data.catalog || [];
        if (!catalog.length) emptyNote(list);
        catalog.forEach((item) => {
          const on = !!active[item.id];
          list.appendChild(row(item.label || item.id, on ? dx.t("studio.settingsSet") : dx.t("studio.settingsUnset"), [
            actionButton(on ? "revoke" : "grant", item.id, dx.t(on ? "studio.revoke" : "studio.grant")),
          ]));
        });
      } else if (currentTab === "outbox") {
        const data = await dx.api("/admin/daedalus/outbox/api");
        const rows = data.items || [];
        if (!rows.length) emptyNote(list);
        rows.forEach((item) => {
          list.appendChild(row(item.title || item.id || "", item.status || "", [
            actionButton("out-apply", item.id, dx.t("studio.apply")),
            actionButton("out-dismiss", item.id, dx.t("studio.dismiss")),
          ]));
        });
      }
      showStatus("review-status", "", false);
    } catch (err) {
      list.replaceChildren();
      showStatus("review-status", dx.t("studio.requestFailed") + ": " + failText(err), true);
    }
  }

  async function reviewAct(act, id, button) {
    await runAction(button, "review-status", async () => {
      let path = "";
      let body = {};
      if (act === "approve") path = "/admin/daedalus/proposals/" + encodeURIComponent(id) + "/approve";
      else if (act === "reject") path = "/admin/daedalus/proposals/" + encodeURIComponent(id) + "/reject";
      else if (act === "rollback") path = "/admin/daedalus/proposals/" + encodeURIComponent(id) + "/rollback";
      else if (act === "toggle") path = "/admin/daedalus/skills/" + encodeURIComponent(id) + "/toggle";
      else if (act === "apply") path = "/admin/daedalus/bundles/" + encodeURIComponent(id) + "/apply";
      else if (act === "bundle-reject") path = "/admin/daedalus/bundles/" + encodeURIComponent(id) + "/reject";
      else if (act === "grant") {
        path = "/admin/daedalus/permissions/grant";
        body = { perm_class: id, hours: 24 };
      } else if (act === "revoke") path = "/admin/daedalus/permissions/" + encodeURIComponent(id) + "/revoke";
      else if (act === "out-apply") path = "/admin/daedalus/outbox/" + encodeURIComponent(id) + "/apply";
      else if (act === "out-dismiss") path = "/admin/daedalus/outbox/" + encodeURIComponent(id) + "/dismiss";
      const data = await postJson(path, body);
      if (data && data.ok === false) {
        showStatus("review-status", dx.t("studio.requestFailed") + ": " + (data.error || ""), true);
        return;
      }
      showStatus("review-status", dx.t("studio.saved"), false);
      await loadReview(currentTab);
    });
  }

  function paintBrain(data) {
    const box = $("settings-brain");
    box.replaceChildren();
    if (!data || data.ok === false) {
      showStatus("settings-status", dx.t("studio.requestFailed") + ": " + ((data && data.error) || ""), true);
      return;
    }
    const line = document.createElement("p");
    line.textContent = [data.provider || dx.t("studio.settingsUnset"), data.model || ""].filter(Boolean).join(" · ");
    const key = document.createElement("p");
    key.textContent = data.has_key ? dx.t("studio.settingsSet") : dx.t("studio.settingsUnset");
    box.append(line, key);
  }

  function paintFields(data) {
    const box = $("settings-fields");
    const select = $("secret-key");
    box.replaceChildren();
    select.replaceChildren();
    (data.groups || []).forEach((group) => {
      (group.fields || []).forEach((field) => {
        const el = document.createElement("div");
        el.className = "item";
        const b = document.createElement("b");
        b.textContent = field.label || field.key || "";
        const s = document.createElement("small");
        s.textContent = field.set ? dx.t("studio.settingsSet") : dx.t("studio.settingsUnset");
        el.append(b, s);
        box.appendChild(el);
        if (field.secret && field.key) {
          const opt = document.createElement("option");
          opt.value = field.key;
          opt.textContent = field.label || field.key;
          select.appendChild(opt);
        }
      });
    });
  }

  async function loadSettings() {
    showStatus("settings-status", dx.t("studio.loading"), false);
    $("settings-brain").replaceChildren();
    $("settings-fields").replaceChildren();
    try {
      paintBrain(await dx.api("/api/v1/llm/status"));
    } catch (err) {
      showStatus("settings-status", dx.t("studio.requestFailed") + ": " + failText(err), true);
    }
    try {
      const overview = await dx.api("/admin/daedalus/connectors/overview");
      paintFields(overview);
      const prefs = overview.prefs || {};
      $("settings-provider").value = prefs.preferred_git_provider || "auto";
      $("settings-branch").value = prefs.default_github_branch || "";
      if (!$("settings-status").dataset.state || $("settings-status").dataset.state !== "error") {
        showStatus("settings-status", "", false);
      }
    } catch (err) {
      showStatus("settings-status", dx.t("studio.requestFailed") + ": " + failText(err), true);
    }
  }

  async function gitCall(button, path, body, needsSlug) {
    const slug = dx.getSlug();
    if (needsSlug && !slug) {
      showStatus("git-status", dx.t("studio.gitNeedSlug"), true);
      return;
    }
    await runAction(button, "git-status", async () => {
      const data = await postJson(path, body);
      if (data && data.ok === false) {
        showStatus("git-status", dx.t("studio.requestFailed") + ": " + (data.error || ""), true);
        return;
      }
      if (data && data.harness_check) {
        showStatus("git-status", dx.t("studio.harnessCheck"), false);
        return;
      }
      showStatus("git-status", dx.t("studio.saved"), false);
      if (data && data.project && data.project.slug) dx.selectSlug(data.project.slug);
      else dx.selectSlug(dx.getSlug());
    });
  }

  function onSlug(snap) {
    if (streamingSlug && snap.slug !== streamingSlug) closeBuildStream();
    loadFiles(snap.slug);
    syncFilter();
    if (currentPanel === "build") loadWorkspace(snap.slug);
    if (currentPanel === "preview") loadPreviewStatus(snap.slug);
    if (!queryReady && snap.ready) {
      const q = readQuery();
      if (q.slug && q.slug !== snap.slug) {
        const known = (snap.projects || []).some((p) => p.slug === q.slug);
        if (known) {
          dx.selectSlug(q.slug);
          return;
        }
        showStatus("workspace-status", dx.t("studio.missingSlug") + ": " + q.slug, true);
      }
      queryReady = true;
      writeQuery();
      return;
    }
    if (queryReady) writeQuery();
  }

  $("workspace-nav").addEventListener("click", (ev) => {
    const btn = ev.target.closest("button[data-panel]");
    if (!btn) return;
    showPanel(btn.dataset.panel, currentTab);
  });
  $("settings-link").addEventListener("click", () => showPanel("settings", currentTab));
  $("open-review").addEventListener("click", () => showPanel("review", "proposals"));
  $("kind-filter").addEventListener("click", (ev) => {
    const btn = ev.target.closest("button[data-filter]");
    if (!btn || btn.disabled) return;
    dx.setKindFilter(btn.dataset.filter);
    syncFilter();
  });
  $("review-tabs").addEventListener("click", (ev) => {
    const btn = ev.target.closest("button[data-tab]");
    if (!btn) return;
    showPanel("review", btn.dataset.tab);
  });
  $("review-list").addEventListener("click", (ev) => {
    const btn = ev.target.closest("button[data-act]");
    if (!btn || btn.disabled) return;
    reviewAct(btn.dataset.act, btn.dataset.id, btn);
  });

  $("build-brainstorm").addEventListener("click", () => {
    if (!requireIdea()) return;
    const draft = brainstormBody();
    runAction($("build-brainstorm"), "build-status", async () => {
      const data = await postJson("/admin/daedalus/game-studio/api/brainstorm", draft.body.brainstorm);
      const suggested = data.suggested || {};
      $("build-out").textContent = dx.t("studio.suggestion") + "\n" + [
        suggested.studio_preset || "",
        suggested.kind || "",
        data.pitch || "",
      ].filter(Boolean).join("\n");
      showStatus("build-status", dx.t("studio.suggestion"), false);
    });
  });
  $("build-gdd").addEventListener("click", () => {
    if (!requireIdea()) return;
    const draft = brainstormBody();
    runAction($("build-gdd"), "build-status", async () => {
      const data = await postJson("/admin/daedalus/game-studio/api/gdd", {
        brainstorm: draft.body.brainstorm,
        studio_preset: "",
        kind: "",
      });
      $("build-out").textContent = data.gdd || "";
      showStatus("build-status", data.gdd_path || dx.t("studio.gdd"), false);
    });
  });
  $("build-start").addEventListener("click", () => {
    if (!requireIdea()) return;
    const draft = brainstormBody();
    runAction($("build-start"), "build-status", async () => {
      const data = await postJson("/admin/daedalus/game-studio/api/start", draft.body);
      $("build-out").textContent = paintResult(data);
      showStatus("build-status", data.slug || dx.t("studio.saved"), false);
      if (data.slug) dx.selectSlug(data.slug);
      await loadGameIndex();
      await loadWorkspace(data.slug || dx.getSlug());
    });
  });
  $("build-guided").addEventListener("click", () => {
    const slug = dx.getSlug();
    if (!slug) return;
    flight = "guided";
    runAction($("build-guided"), "build-status", async () => {
      const data = await postJson("/admin/daedalus/game-studio/api/projects/" + slugPath(slug) + "/build/guided", {});
      if (data.ok === false) throw new Error(data.error || dx.t("studio.requestFailed"));
      if (data.harness_check) {
        showStatus("build-status", dx.t("studio.harnessCheck"), false);
        return;
      }
      showStatus("build-status", dx.t("studio.started"), false);
      if (data.job_id) followBuild("guided", slug, data.job_id);
    });
  });
  $("build-autopilot").addEventListener("click", () => {
    const slug = dx.getSlug();
    if (!slug) return;
    const description = ($("build-idea").value || "").trim() || slug;
    flight = "autopilot";
    runAction($("build-autopilot"), "build-status", async () => {
      const data = await postJson("/api/v1/playground/projects/" + slugPath(slug) + "/autopilot", {
        description,
        mode: "full",
        effort: "auto",
      });
      if (data.harness_check) {
        showStatus("build-status", dx.t("studio.harnessCheck"), false);
        return;
      }
      showStatus("build-status", dx.t("studio.started"), false);
      const jobId = data.job && data.job.id;
      if (jobId) followBuild("autopilot", slug, jobId);
    });
  });
  $("build-stop").addEventListener("click", () => {
    const slug = dx.getSlug();
    if (!slug) return;
    const path = flight === "autopilot"
      ? "/admin/daedalus/playground/api/projects/" + slugPath(slug) + "/autopilot/stop"
      : "/admin/daedalus/playground/api/projects/" + slugPath(slug) + "/guided/stop";
    runAction($("build-stop"), "build-status", async () => {
      const data = await postJson(path, {});
      if (data.ok === false) throw new Error(data.error || dx.t("studio.requestFailed"));
      closeBuildStream();
      $("build-stop").hidden = true;
      showStatus("build-status", dx.t("studio.stopped"), false);
    });
  });
  ["gates", "verify", "advance"].forEach((name) => {
    const suffix = name === "advance" ? "/milestones/advance" : "/" + name;
    $("build-" + name).addEventListener("click", () => {
      const slug = dx.getSlug();
      if (!slug) return;
      runAction($("build-" + name), "build-status", async () => {
        const data = await postJson(
          "/admin/daedalus/game-studio/api/projects/" + slugPath(slug) + suffix,
          {}
        );
        if (data.harness_check) {
          showStatus("build-status", dx.t("studio.harnessCheck"), false);
          return;
        }
        if (data.ok === false) throw new Error(data.error || dx.t("studio.requestFailed"));
        $("build-out").textContent = paintResult(data);
        showStatus("build-status", dx.t("studio.saved"), false);
        await loadWorkspace(slug);
      });
    });
  });

  $("preview-run").addEventListener("click", () => {
    const slug = dx.getSlug();
    if (!slug) return;
    runAction($("preview-run"), "preview-status", async () => {
      const data = await postJson("/admin/daedalus/playground/api/projects/" + slugPath(slug) + "/run", {});
      if (data.harness_check) {
        showStatus("preview-status", dx.t("studio.harnessCheck"), false);
        return;
      }
      showStatus("preview-status", dx.t("studio.started"), false);
    });
  });
  $("preview-stop").addEventListener("click", () => {
    const slug = dx.getSlug();
    if (!slug) return;
    runAction($("preview-stop"), "preview-status", async () => {
      const data = await postJson("/admin/daedalus/playground/api/projects/" + slugPath(slug) + "/stop", {});
      if (!data.ok) throw new Error(data.error || dx.t("studio.requestFailed"));
      showStatus("preview-status", dx.t("studio.stopped"), false);
    });
  });
  $("preview-logs").addEventListener("click", () => loadPreviewStatus(dx.getSlug()));
  $("preview-show").addEventListener("click", async () => {
    const slug = dx.getSlug();
    const frame = $("preview-frame");
    if (!slug) return;
    const url = "/api/v1/playground/projects/" + slugPath(slug) + "/preview/index.html";
    try {
      const res = await fetch(url);
      if (!res.ok) throw new Error("HTTP " + res.status);
      frame.src = url;
      showStatus("preview-status", dx.t("studio.showPreview"), false);
    } catch (err) {
      frame.removeAttribute("src");
      showStatus("preview-status", dx.t("studio.requestFailed") + ": " + failText(err), true);
    }
  });
  $("preview-local").addEventListener("click", () => publish("/publish-local", $("preview-local")));
  $("preview-live").addEventListener("click", () => publish("/live", $("preview-live"), { backend: "auto" }));
  $("preview-capsule").addEventListener("click", () => {
    const slug = dx.getSlug();
    if (!slug) return;
    runAction($("preview-capsule"), "preview-status", async () => {
      const data = await dx.api("/api/v1/playground/projects/" + slugPath(slug) + "/capsule.json");
      showStatus(
        "preview-status",
        data.harness_check ? dx.t("studio.harnessCheck") : dx.t("studio.localReady"),
        false
      );
    });
  });

  function publish(suffix, button, body) {
    const slug = dx.getSlug();
    if (!slug) return;
    runAction(button, "preview-status", async () => {
      const data = await postJson("/api/v1/playground/projects/" + slugPath(slug) + suffix, body || {});
      if (data.harness_check) {
        showStatus("preview-status", dx.t("studio.harnessCheck"), false);
        return;
      }
      if (data.ok === false) throw new Error(data.error || dx.t("studio.notPublished"));
      showStatus("preview-status", suffix === "/live" ? dx.t("studio.localReady") : dx.t("studio.localReady"), false);
    });
  }

  $("git-clone").addEventListener("click", () => {
    const url = ($("git-url").value || "").trim();
    if (!url) { showStatus("git-status", dx.t("studio.gitNeedUrl"), true); return; }
    gitCall($("git-clone"), "/admin/daedalus/playground/api/clone", { url }, false);
  });
  $("git-revive").addEventListener("click", () => {
    const url = ($("git-url").value || "").trim();
    if (!url) { showStatus("git-status", dx.t("studio.gitNeedUrl"), true); return; }
    gitCall($("git-revive"), "/admin/daedalus/playground/api/revive", { url }, false);
  });
  $("git-push").addEventListener("click", () => {
    const slug = dx.getSlug();
    if (!slug) { showStatus("git-status", dx.t("studio.gitNeedSlug"), true); return; }
    gitCall($("git-push"), "/admin/daedalus/playground/api/projects/" + slugPath(slug) + "/push", { provider: "auto" }, true);
  });
  $("git-pr").addEventListener("click", () => {
    const slug = dx.getSlug();
    if (!slug) { showStatus("git-status", dx.t("studio.gitNeedSlug"), true); return; }
    gitCall(
      $("git-pr"),
      "/admin/daedalus/playground/api/projects/" + slugPath(slug) + "/create-upstream-pr",
      { title: slug },
      true
    );
  });
  $("project-delete").addEventListener("click", () => {
    const slug = dx.getSlug();
    if (!slug) return;
    if (!window.confirm(dx.t("studio.deleteConfirm"))) return;
    runAction($("project-delete"), "git-status", async () => {
      await dx.api("/api/v1/playground/projects/" + slugPath(slug), { method: "DELETE" });
      showStatus("git-status", dx.t("studio.saved"), false);
      dx.selectSlug(null);
    });
  });
  $("settings-prefs").addEventListener("submit", (ev) => {
    ev.preventDefault();
    runAction(ev.submitter, "settings-status", async () => {
      await postJson("/admin/daedalus/connectors/config", {
        preferred_git_provider: $("settings-provider").value,
        default_github_branch: $("settings-branch").value,
      });
      showStatus("settings-status", dx.t("studio.saved"), false);
    });
  });
  $("settings-secret").addEventListener("submit", (ev) => {
    ev.preventDefault();
    const key = $("secret-key").value;
    const value = $("secret-value").value;
    if (!key || !value) return;
    runAction(ev.submitter, "settings-status", async () => {
      const secrets = {};
      secrets[key] = value;
      await postJson("/admin/daedalus/connectors/secrets", { secrets });
      $("secret-value").value = "";
      showStatus("settings-status", dx.t("studio.saved"), false);
      await loadSettings();
    });
  });

  document.addEventListener("daedalus:langchange", () => {
    if (currentPanel === "review") loadReview(currentTab);
    if (currentPanel === "settings") loadSettings();
    if (currentPanel === "build") loadWorkspace(dx.getSlug());
    loadFiles(dx.getSlug());
  });
  window.matchMedia("(max-width: 1100px)").addEventListener("change", () => {
    if (currentPanel === "world" || currentPanel === "tools") showPanel(currentPanel, currentTab);
  });

  const opening = readQuery();
  showPanel(opening.panel, opening.tab);
  dx.watchSlug(onSlug);
  loadCatalog();
  loadGameIndex();
})();
