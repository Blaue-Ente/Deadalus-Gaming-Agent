(() => {
  const dx = window.dxStudio;
  if (!dx) return;

  const PANELS = ["chat", "build", "preview", "review", "settings", "world", "tools"];
  const TABS = ["proposals", "skills", "bundles", "permissions", "outbox", "memory"];
  let currentPanel = "chat";
  let currentTab = "proposals";
  let queryReady = false;
  let flight = "";
  let fileGen = 0;
  let streamGen = 0;
  let streamCtrl = null;
  let streamingSlug = "";
  let resumeGen = 0;
  let explicitTicket = 0;
  let explicitFlight = null;
  let buildSlug = null;
  let previewSlug = null;      // the project the preview line was last loaded for (passive re-renders do not reload it)
  let checkpointGen = 0;
  let checkpoint = null;
  let continueInFlight = false;
  let assetGen = 0;
  let assetFlight = 0;
  let assetSlug = null;
  let assetJobIds = [];
  let shipGen = 0;
  let shipFlight = 0;
  let shipSlug = null;
  let blockerGen = 0;
  let blockerSeq = 0;
  let blockerFlight = 0;
  let planGen = 0;
  let planSeq = 0;
  let planFlight = 0;
  let planDetail = null;
  const planApproved = new Set();
  const planDeleted = new Set();
  let planGenerateOp = null;
  const planApproveOp = new Map();
  let discoveryGen = 0;
  let discoverySeq = 0;
  let discoveryFlight = 0;
  const discoveryApproved = new Set();
  const discoveryRejected = new Set();
  const discoveryApproveOp = new Map();
  let skillGen = 0;
  let skillOp = null;
  let gistFlight = false;
  let gistDone = null;                             // {id, url} of the gist published for a skill session
  let skillView = null;
  let skillFiles = null;
  let skillNote = "";
  let skillNoteBad = false;
  let skillGenerateRefused = false;
  let refGen = 0;
  let refOp = null;
  let refResult = null;
  let refNote = "";
  let refNoteBad = false;
  const ASSET_GENERATE_TIMEOUT_MS = 120000;
  const ASSET_AUTOPACK_TIMEOUT_MS = 660000;
  const ASSET_PIPELINE_TIMEOUT_MS = 60000;
  const SHIP_TIMEOUT_MS = 60000;
  const SHIP_UNITY_TIMEOUT_MS = 240000;
  const BLOCKER_TIMEOUT_MS = 20000;
  const PLAN_TIMEOUT_MS = 20000;
  let faOwnerSlug = "";                            // the project Full Auto owned at the last look (Build run controls)
  const DISCOVERY_TIMEOUT_MS = 20000;
  const SKILL_TIMEOUT_MS = 90000;
  const REF_TIMEOUT_MS = 45000;
  const SKILL_PLATFORMS = ["generic", "cursor", "claude", "codex", "harness"];
  const PLAN_GOAL_MAX = 2000;

  function $(id) { return document.getElementById(id); }

  function failText(err) {
    if (err && (err.name === "AbortError" || /aborted|timeout/i.test(String(err.message || "")))) {
      return dx.t("studio.timeout");
    }
    if (err && String(err.message || "") === "full_auto_running") return dx.t("studio.ctlErrFullAuto");
    return dx.humanError(err, { raw: true });
  }

  // Styled confirm: a short title per call (studio.cf.<name>), the existing question as the body.
  function ask(name, body) { return dx.confirm({ title: dx.t("studio.cf." + name), body }); }
  function askDanger(name, body) { return dx.confirm({ title: dx.t("studio.cf." + name), body, danger: true }); }

  // Every write to a status line bumps its stamp. A background refresh remembers the stamp when it starts and
  // writes only if nothing else has written since, so a slow answer can never replace a newer result.
  const statusStamp = {};
  function showStatus(id, message, bad) {
    const el = $(id);
    if (!el) return;
    statusStamp[id] = (statusStamp[id] || 0) + 1;
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
    if (currentPanel === "review" && panel !== "review") {
      blockerGen += 1;
      blockerFlight = 0;
      planGen += 1;
      planFlight = 0;
      discoveryGen += 1;
      discoveryFlight = 0;
      skillGen += 1;
      refGen += 1;
    }
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
    if (panel === "preview") loadShipRequests();          // what Daedalus asked to publish or push
    if (panel === "review") {
      loadReview(currentTab);
      loadBlockers();
      loadPlans();
      loadDiscoveries();
      paintSkill();
      paintRef();
    }
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
      if (!kept) {
        dx.failGameIndex();
        if (dx.getKindFilter() !== "all") dx.setKindFilter("all");
        setKindButtons(false);
      }
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
    // Full Auto drives this project (as last seen by the run controls): the manual steps would race it, so every
    // repaint keeps them off, not only the controls' own paint (which skips an unchanged answer).
    const faOwns = faOwnerSlug !== "" && faOwnerSlug === dx.getSlug();
    $("build-guided").disabled = faOwns || !data.can_guided;
    $("build-autopilot").disabled = faOwns || !data.can_autopilot;
    $("build-gates").disabled = false;
    $("build-verify").disabled = faOwns;
    $("build-advance").disabled = faOwns || !data.can_advance;
    // A waiver is offered only where the Game Studio itself allows one, and only when a plain Advance is not possible.
    $("build-waive-unity").disabled = faOwns;               // the waivers wait too, as the server would refuse them
    $("build-waive-proof").disabled = faOwns;
    $("build-waive-unity").hidden = !!data.can_advance || !data.can_unity_waiver;
    $("build-waive-proof").hidden = !!data.can_advance || !data.can_proof_waiver || !!data.can_unity_waiver;
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
    clearCheckpoint();
  }

  function clearCheckpoint() {
    checkpointGen += 1;
    checkpoint = null;
    continueInFlight = false;
    ["build-checkpoint", "flight-checkpoint"].forEach((id) => {
      const zone = $(id);
      if (!zone) return;
      zone.hidden = true;
      const review = zone.querySelector("[data-cp-review]");
      if (review) review.textContent = "";
      const options = zone.querySelector("[data-cp-options]");
      if (options) options.replaceChildren();
      const notes = zone.querySelector("textarea");
      if (notes) notes.value = "";
    });
  }

  function setCheckpointBusy(busy) {
    document.querySelectorAll("#build-checkpoint button, #flight-checkpoint button, #build-cp-notes, #flight-cp-notes").forEach((el) => {
      el.disabled = busy;
    });
  }

  function syncCheckpointNotes(source) {
    ["build-cp-notes", "flight-cp-notes"].forEach((id) => {
      const el = $(id);
      if (el && el !== source) el.value = source.value;
    });
  }

  function showCheckpoint(slug, jobId, kind, data) {
    const payload = data || {};
    checkpointGen += 1;
    const gen = checkpointGen;
    checkpoint = { gen: gen, slug: slug, jobId: jobId, kind: kind };
    continueInFlight = false;
    const review = payload.review || payload.message || dx.t("studio.checkpoint");
    const supplied = kind === "guided" && Array.isArray(payload.options) ? payload.options : [];
    ["build", "flight"].forEach((side) => {
      const zone = $(side + "-checkpoint");
      zone.hidden = false;
      zone.querySelector("[data-cp-review]").textContent = review;
      const box = zone.querySelector("[data-cp-options]");
      box.replaceChildren();
      supplied.forEach((opt) => {
        if (!opt || !opt.id) return;
        const btn = document.createElement("button");
        btn.type = "button";
        btn.dataset.action = String(opt.id);
        btn.textContent = String(opt.label || opt.id);
        if (opt.hint) btn.title = String(opt.hint);
        btn.addEventListener("click", () => {
          continueCheckpoint(gen, slug, jobId, kind, String(opt.id), side);
        });
        box.appendChild(btn);
      });
    });
    showStatus("build-status", review, false);
  }

  async function continueCheckpoint(gen, slug, jobId, kind, action, side) {
    if (continueInFlight) return;
    if (!checkpoint || checkpoint.gen !== gen || checkpoint.slug !== slug || checkpoint.jobId !== jobId) return;
    if (dx.getSlug() !== slug || kind !== "guided") return;
    const ticket = claimExplicitBuild(slug);
    continueInFlight = true;
    setCheckpointBusy(true);
    const notesEl = $(side + "-cp-notes");
    const notes = notesEl ? notesEl.value.trim() : "";
    let data = null;
    try {
      data = await postJson(
        "/admin/daedalus/playground/api/projects/" + slugPath(slug) + "/guided/continue",
        { job_id: jobId, action: action, notes: notes }
      );
    } catch (err) {
      if (explicitCurrent(ticket, slug) && gen === checkpointGen) {
        continueInFlight = false;
        setCheckpointBusy(false);
        showStatus("build-status", dx.t("studio.requestFailed") + ": " + failText(err), true);
      }
      finishExplicitBuild(ticket);
      return;
    }
    if (!explicitCurrent(ticket, slug) || gen !== checkpointGen || !checkpoint || checkpoint.jobId !== jobId) {
      finishExplicitBuild(ticket);
      return;
    }
    if (!data || data.ok !== true) {
      continueInFlight = false;
      setCheckpointBusy(false);
      showStatus("build-status", dx.t("studio.requestFailed") + ": " + ((data && data.error) || ""), true);
      finishExplicitBuild(ticket);
      return;
    }
    clearCheckpoint();
    if (data.milestone_complete === true) {
      $("build-stop").hidden = true;
      showStatus("build-status", dx.t("studio.done"), false);
      finishExplicitBuild(ticket);
      return;
    }
    followBuild(kind, slug, jobId);
    finishExplicitBuild(ticket);
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
    let terminalData = null;
    const apply = (block) => {
      const line = streamLine(block.event, block.data || {});
      if (line) appendBuild(line);
      if (block.event === "error") showStatus("build-status", line || dx.t("studio.streamStopped"), true);
      else if (line) showStatus("build-status", line, false);
      if (block.event === "checkpoint" || block.event === "done" || block.event === "error" || block.event === "milestone_done") {
        terminal = block.event;
        terminalData = block.data || {};
      }
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
      return { event: terminal, data: terminalData };
    }
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    while (gen === streamGen) {
      const step = await reader.read();
      buf += decoder.decode(step.value || new Uint8Array(), { stream: !step.done }).replace(/\r\n/g, "\n");
      buf = take(buf, !!step.done);
      if (stopped()) return { event: terminal, data: terminalData };
      if (step.done) break;
    }
    return { event: terminal, data: terminalData };
  }

  function followBuild(kind, slug, jobId) {
    if (dx.getSlug() !== slug) return;
    closeBuildStream();
    const gen = streamGen;
    streamingSlug = slug;
    flight = kind;
    $("build-stop").hidden = false;
    $("build-out").textContent = "";
    const flightTab = document.querySelector('.tabs button[data-pane="flight"]');
    if (flightTab) flightTab.click();
    const url = kind === "autopilot"
      ? "/api/v1/playground/projects/" + slugPath(slug) + "/autopilot/stream"
      : "/admin/daedalus/playground/api/projects/" + slugPath(slug) + "/guided/stream?job_id=" + encodeURIComponent(jobId || "") + "&from=0";
    readBuildStream(url, gen).then(async (result) => {
      if (gen !== streamGen) return;
      const terminal = result && result.event;
      if (terminal === "checkpoint") {
        showCheckpoint(slug, jobId, kind, (result && result.data) || {});
        return;
      }
      clearCheckpoint();
      if (terminal === "done" || terminal === "error" || terminal === "milestone_done") {
        $("build-stop").hidden = true;
      }
      if (terminal === "done" || terminal === "milestone_done") await loadWorkspace(slug, { quiet: true, force: true });
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

  function invalidatePendingBuild() {
    resumeGen += 1;
    explicitTicket += 1;
    explicitFlight = null;
  }

  function claimExplicitBuild(slug) {
    resumeGen += 1;
    explicitTicket += 1;
    explicitFlight = { ticket: explicitTicket, slug: slug };
    return explicitTicket;
  }

  function explicitCurrent(ticket, slug) {
    return !!(explicitFlight && explicitFlight.ticket === ticket && explicitFlight.slug === slug && dx.getSlug() === slug);
  }

  function finishExplicitBuild(ticket) {
    if (explicitFlight && explicitFlight.ticket === ticket) explicitFlight = null;
  }

  // Each resume bumps resumeGen, which invalidates any earlier resume: so two asks for the same project while the
  // first is still waiting used to throw the first answer away. An identical resume that is still current is shared.
  let resumeFlight = null;        // {slug, gen, promise}
  function resumeBuild(slug) {
    if (resumeFlight && resumeFlight.slug === slug && resumeFlight.gen === resumeGen) return resumeFlight.promise;
    const promise = resumeBuildNow(slug);
    const entry = { slug, gen: resumeGen, promise };
    resumeFlight = entry;
    promise.then(() => { if (resumeFlight === entry) resumeFlight = null; });
    return promise;
  }

  async function resumeBuildNow(slug) {
    if (!slug || streamingSlug === slug) return;
    if (explicitFlight && explicitFlight.slug === slug) return;
    // The stream of a paused job ends at its checkpoint. While that decision is open there is nothing to follow:
    // resuming would replay the job from the start (every log line twice) and redraw the decision under the owner.
    if (checkpoint && checkpoint.slug === slug) return;
    resumeGen += 1;
    const gen = resumeGen;
    const ticket = explicitTicket;
    const current = () => gen === resumeGen && ticket === explicitTicket && !explicitFlight && !streamingSlug && dx.getSlug() === slug;
    try {
      const guided = await dx.api("/admin/daedalus/playground/api/projects/" + slugPath(slug) + "/guided/active");
      if (!current()) return;
      if (guided && guided.active && guided.job_id) {
        followBuild("guided", slug, guided.job_id);
        return;
      }
    } catch (err) { /* no guided job is not a failed build */ }
    if (!current()) return;
    try {
      const auto = await dx.api("/api/v1/playground/projects/" + slugPath(slug) + "/autopilot");
      if (!current()) return;
      const job = auto && auto.job;
      if (job && job.id && job.status === "running") followBuild("autopilot", slug, job.id);
    } catch (err) { /* no autopilot job */ }
  }

  // The page asks for the workspace twice at startup (panel switch and slug snapshot). Two things used to go wrong:
  // each call greyed out every build button before its request, so the second call disabled what the first had
  // just enabled and a click in that gap vanished with no request and no message; and whichever response arrived
  // last painted, even if it was the older one. Now a load of the same slug that is already running is shared,
  // controls are greyed out only when the project changed, and only the newest load paints.
  // A forced load (after a build step, a save) always reloads. Resuming twice is handled in resumeBuild itself.
  let workspaceLoad = null;       // {slug, promise} while a load runs
  let workspaceSeq = 0;
  let workspacePainted = "";      // the slug the build controls currently describe
  function loadWorkspace(slug, opts) {
    const last = workspaceLoad;
    if (last && last.slug === slug && !(opts && opts.force)) return last.promise;
    const run = loadWorkspaceNow(slug, opts);
    const entry = { slug, promise: run };
    workspaceLoad = entry;
    run.then(() => { if (workspaceLoad === entry) workspaceLoad = null; });
    return run;
  }

  function setBuildControlsDisabled() {
    ["build-guided", "build-autopilot", "build-gates", "build-verify", "build-advance"].forEach((id) => {
      $(id).disabled = true;
    });
    $("build-waive-unity").hidden = true;                 // another project: its waivers are not known yet
    $("build-waive-proof").hidden = true;
  }

  async function loadWorkspaceNow(slug, opts) {
    const mine = ++workspaceSeq;
    $("build-slug").textContent = slug ? dx.t("studio.project") + ": " + slug : "";
    if (workspacePainted !== slug) {            // a different project: its controls are not known yet
      setBuildControlsDisabled();
      workspacePainted = "";
    }
    if (!slug) { loadControls(""); return; }
    const stamp = statusStamp["build-status"] || 0;
    const untouched = () => (statusStamp["build-status"] || 0) === stamp;   // nothing reported here since this began
    try {
      const data = await dx.api("/admin/daedalus/game-studio/api/projects/" + slugPath(slug));
      if (mine !== workspaceSeq) return;        // a newer load owns the screen
      paintMilestones(data);
      workspacePainted = slug;
      // Clearing the status line is a write like any other: it must not erase what a click reported meanwhile.
      if (!streamingSlug && !(checkpoint && checkpoint.slug === slug) && untouched()) showStatus("build-status", "", false);
    } catch (err) {
      if (mine !== workspaceSeq) return;
      workspacePainted = "";
      setBuildControlsDisabled();
      $("build-milestones").replaceChildren();
      if (!streamingSlug && untouched()) showStatus("build-status", failText(err), true);
    }
    if (!opts || !opts.quiet) resumeBuild(slug);
    if (mine === workspaceSeq) loadControls(slug);
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
    const stamp = statusStamp["preview-status"] || 0;
    // stale: an action reported since this began, or the owner moved to another project meanwhile
    const stale = () => (statusStamp["preview-status"] || 0) !== stamp || dx.getSlug() !== slug;
    try {
      const data = await dx.api("/admin/daedalus/playground/api/projects/" + slugPath(slug) + "/logs");
      if (stale()) return;
      const running = !!(data.status && typeof data.status === "object");
      showStatus("preview-status", running ? dx.t("studio.started") : dx.t("studio.previewEmpty"), false);
    } catch (err) {
      if (stale()) return;
      showStatus("preview-status", dx.t("studio.requestFailed") + ": " + failText(err), true);
    }
  }

  function emptyNote(list, hintKey) {
    const empty = document.createElement("div");
    empty.className = "empty";
    list.appendChild(empty);
    dx.emptyState(empty, { text: dx.t(hintKey || "studio.emptyList") });
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

  function blockerStill(gen) {
    return gen === blockerGen && currentPanel === "review";
  }

  function syncBlockerButtons() {
    const locked = blockerFlight !== 0;
    ["blocker-refresh", "blocker-autofix-all", "blocker-grant-rec", "blocker-hours"].forEach((id) => {
      const el = $(id);
      if (el) el.disabled = locked;
    });
    document.querySelectorAll("#blocker-list button").forEach((btn) => { btn.disabled = locked; });
  }

  function blockerFail(err) {
    const message = String((err && err.message) || err || "");
    if (err && (err.name === "AbortError" || /aborted|timed out|timeout|изтекло|изтече/i.test(message))) {
      return dx.t("studio.blockerUnknown");
    }
    return dx.t("studio.requestFailed") + ": " + dx.errText(err);
  }

  function renderBlockers(rows, gen) {
    const list = $("blocker-list");
    list.replaceChildren();
    if (!rows.length) {
      const empty = document.createElement("div");
      empty.className = "empty";
      empty.textContent = dx.t("studio.blockerNone");
      list.appendChild(empty);
      return;
    }
    rows.forEach((item) => {
      if (!item || typeof item !== "object") return;
      const id = String(item.id || "");
      const el = document.createElement("div");
      el.className = "item";
      const title = document.createElement("b");
      title.textContent = id;
      const sub = document.createElement("small");
      sub.textContent = [item.status, item.reason, item.tried, item.next_steps, item.file_path, item.resolution_note]
        .filter((value) => typeof value === "string" && value)
        .join("\n");
      const actions = document.createElement("div");
      actions.className = "row tight";
      if (item.status !== "resolved" && id) {
        const btn = document.createElement("button");
        btn.type = "button";
        btn.dataset.gen = String(gen);
        btn.dataset.id = id;
        btn.textContent = dx.t("studio.blockerResolve");
        btn.addEventListener("click", () => resolveBlocker(id, gen, btn));
        actions.appendChild(btn);
        blockerFixButtons(id).forEach((extra) => actions.appendChild(extra));      // defined with the other Review actions
      }
      el.append(title, sub, actions);
      list.appendChild(el);
    });
  }

  async function loadBlockers(opts) {
    if (currentPanel !== "review") return;
    if (blockerFlight) return;
    const gen = blockerGen;
    const flight = ++blockerSeq;
    blockerFlight = flight;
    syncBlockerButtons();
    try {
      const data = await dx.api("/admin/daedalus/blockers", { timeoutMs: BLOCKER_TIMEOUT_MS });
      if (!blockerStill(gen) || flight !== blockerFlight) return;
      if (!data || !Array.isArray(data.blockers)) {
        showStatus("blocker-status", dx.t("studio.blockerNotOk"), true);
        $("blocker-list").replaceChildren();
        return;
      }
      renderBlockers(data.blockers, gen);
      if (!opts || !opts.keepStatus) showStatus("blocker-status", "", false);
    } catch (err) {
      if (!blockerStill(gen) || flight !== blockerFlight) return;
      showStatus("blocker-status", blockerFail(err), true);
      $("blocker-list").replaceChildren();
    }     finally {
      if (blockerFlight === flight) {
        blockerFlight = 0;
        syncBlockerButtons();
      }
    }
  }

  async function resolveBlocker(id, gen, btn) {
    if (blockerFlight) return;
    if (currentPanel !== "review" || !btn.isConnected) return;
    if (btn.dataset.gen !== String(blockerGen) || gen !== blockerGen) return;
    const note = ($("blocker-note").value || "").trim();
    if (!await ask("blockerResolve", dx.t("studio.blockerConfirm", { id }))) return;
    if (currentPanel !== "review" || !btn.isConnected) return;
    if (btn.dataset.gen !== String(blockerGen) || gen !== blockerGen) return;
    const flight = ++blockerSeq;
    blockerFlight = flight;
    syncBlockerButtons();
    const body = note ? { note } : {};
    let refresh = false;
    dx.api("/admin/daedalus/blockers/" + encodeURIComponent(id) + "/resolve", {
      method: "POST",
      body: JSON.stringify(body),
      timeoutMs: BLOCKER_TIMEOUT_MS,
    }).then((data) => {
      if (!blockerStill(gen) || flight !== blockerFlight) return;
      if (!data || data.ok !== true) {
        const detail = data && (data.error || data.detail);
        showStatus("blocker-status", dx.t("studio.blockerNotOk") + (detail ? ": " + detail : ""), true);
        return;
      }
      showStatus("blocker-status", dx.t("studio.blockerResolved", { id }), false);
      refresh = true;
    }).catch((err) => {
      if (!blockerStill(gen) || flight !== blockerFlight) return;
      showStatus("blocker-status", blockerFail(err), true);
    }).finally(() => {
      if (blockerFlight === flight) {
        blockerFlight = 0;
        syncBlockerButtons();
      }
      if (refresh && blockerStill(gen)) loadBlockers({ keepStatus: true });
    });
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
        if (!rows.length) emptyNote(list, "studio.emptyProposals");
        else list.appendChild(approveAllToolbar(rows.filter((item) => item.status === "pending" && item.id).map((item) => String(item.id))));
        rows.forEach((item) => {
          list.appendChild(row(item.reason || item.id || "", item.status || "", [
            actionButton("detail-proposal", item.id, dx.t("studio.detail")),
            actionButton("approve", item.id, dx.t("studio.approve")),
            actionButton("reject", item.id, dx.t("studio.reject")),
            actionButton("rollback", item.id, dx.t("studio.rollback")),
          ]));
        });
      } else if (currentTab === "skills") {
        const data = await dx.api("/admin/daedalus/skills/api");
        const rows = data.skills || [];
        if (!rows.length) emptyNote(list, "studio.emptySkills");
        rows.forEach((item) => {
          list.appendChild(row(item.label || item.name || "", item.enabled ? dx.t("studio.settingsSet") : dx.t("studio.settingsUnset"), [
            actionButton("toggle", item.name, dx.t("studio.toggle")),
          ]));
        });
      } else if (currentTab === "bundles") {
        const data = await dx.api("/admin/daedalus/bundles/api");
        const rows = data.bundles || [];
        if (!rows.length) emptyNote(list, "studio.emptyBundles");
        rows.forEach((item) => {
          list.appendChild(row(item.title || item.name || item.id || "", item.status || "", [
            actionButton("detail-bundle", item.id, dx.t("studio.detail")),
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
        if (!catalog.length) emptyNote(list, "studio.emptyPermissions");
        else list.appendChild(permissionsToolbar());
        catalog.forEach((item) => {
          const on = !!active[item.id];
          list.appendChild(row(item.label || item.id, on ? dx.t("studio.settingsSet") : dx.t("studio.settingsUnset"), [
            actionButton(on ? "revoke" : "grant", item.id, dx.t(on ? "studio.revoke" : "studio.grant")),
          ]));
        });
      } else if (currentTab === "memory") {
        await loadMemory(list);
      } else if (currentTab === "outbox") {
        const data = await dx.api("/admin/daedalus/outbox/api");
        const rows = data.items || [];
        if (!rows.length) emptyNote(list, "studio.emptyOutbox");
        rows.forEach((item) => {
          list.appendChild(row(item.title || item.id || "", item.status || "", [
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

  function reviewUnresolved(err) {
    const message = String((err && err.message) || err || "");
    return !!(err && (err.name === "AbortError" || /aborted|timed out|timeout|failed to fetch|networkerror|err_timed_out|err_aborted|изтекло|изтече/i.test(message)));
  }

  function reviewFail(err, unknownKey) {
    if (reviewUnresolved(err)) return dx.t(unknownKey);
    return dx.t("studio.requestFailed") + ": " + dx.errText(err);
  }

  function planHoldMessage() {
    let unknown = planGenerateOp === "unknown";
    let pendingId = "";
    planApproveOp.forEach((state, id) => {
      if (state === "unknown") unknown = true;
      else if (!pendingId) pendingId = id;
    });
    if (unknown) return dx.t("studio.planUnknown");
    if (planGenerateOp === "pending") return dx.t("studio.planGeneratePending");
    if (pendingId) return dx.t("studio.planApprovePending", { id: pendingId });
    return "";
  }

  function showPlanListStatus(opts) {
    const hold = planHoldMessage();
    if (hold) {
      showStatus("plan-status", hold, true);
      return;
    }
    if (!opts || !opts.keepStatus) showStatus("plan-status", "", false);
  }

  function discoveryHoldMessage() {
    let unknown = false;
    let pendingId = "";
    discoveryApproveOp.forEach((state, id) => {
      if (state === "unknown") unknown = true;
      else if (!pendingId) pendingId = id;
    });
    if (unknown) return dx.t("studio.discoveryUnknown");
    if (pendingId) return dx.t("studio.discoveryApprovePending", { id: pendingId });
    return "";
  }

  function showDiscoveryListStatus(opts) {
    const hold = discoveryHoldMessage();
    if (hold) {
      showStatus("discovery-status", hold, true);
      return;
    }
    if (!opts || !opts.keepStatus) showStatus("discovery-status", "", false);
  }

  function dropRowAct(listId, id, act) {
    const list = $(listId);
    if (!list || !id) return;
    list.querySelectorAll("[data-id]").forEach((row) => {
      if (row.dataset.id !== id) return;
      row.querySelectorAll("button").forEach((btn) => {
        if (!act || btn.dataset.act === act) btn.remove();
      });
    });
  }

  function planStill(gen) {
    return gen === planGen && currentPanel === "review";
  }

  function syncPlanButtons() {
    const listLocked = planFlight !== 0;
    const generate = $("plan-generate");
    const refresh = $("plan-refresh");
    if (generate) generate.disabled = listLocked || planGenerateOp !== null;
    if (refresh) refresh.disabled = listLocked;
    document.querySelectorAll("#plan-list button").forEach((btn) => {
      btn.disabled = listLocked || btn.dataset.keep === "1" || (btn.dataset.act === "approve" && planApproveOp.has(btn.dataset.id || ""));
    });
  }

  function renderPlans(rows, gen) {
    const list = $("plan-list");
    list.replaceChildren();
    const visible = (rows || []).filter((item) => item && typeof item === "object" && !planDeleted.has(String(item.id || "")));
    if (!visible.length) {
      const empty = document.createElement("div");
      empty.className = "empty";
      empty.textContent = dx.t("studio.planNone");
      list.appendChild(empty);
      return;
    }
    visible.forEach((item) => {
      const id = String(item.id || "");
      const el = document.createElement("div");
      el.className = "item";
      el.dataset.id = id;
      el.dataset.status = String(item.status || "");
      const title = document.createElement("b");
      title.textContent = id;
      const sub = document.createElement("small");
      const lines = [item.status, item.title, item.description].filter((value) => typeof value === "string" && value);
      if (planDetail && String(planDetail.id || "") === id && Array.isArray(planDetail.steps)) {
        planDetail.steps.forEach((step) => {
          if (step && typeof step.title === "string" && step.title) lines.push(step.title);
        });
      }
      sub.textContent = lines.join("\n");
      const actions = document.createElement("div");
      actions.className = "row tight";
      if (id && item.status === "draft" && !planApproved.has(id) && !planApproveOp.has(id)) {
        const approve = document.createElement("button");
        approve.type = "button";
        approve.dataset.act = "approve";
        approve.dataset.gen = String(gen);
        approve.dataset.id = id;
        approve.textContent = dx.t("studio.planApprove");
        approve.addEventListener("click", () => approvePlan(id, gen, approve));
        actions.appendChild(approve);
      }
      if (id) {
        const remove = document.createElement("button");
        remove.type = "button";
        remove.dataset.act = "delete";
        remove.dataset.gen = String(gen);
        remove.dataset.id = id;
        remove.textContent = dx.t("studio.planDelete");
        remove.addEventListener("click", () => deletePlan(id, gen, remove));
        actions.appendChild(remove);
      }
      el.append(title, sub, actions);
      planExtras(id, String(item.status || ""), actions, el);
      list.appendChild(el);
    });
  }

  async function loadPlans(opts) {
    if (currentPanel !== "review") return;
    if (planFlight) return;
    const gen = planGen;
    const flight = ++planSeq;
    planFlight = flight;
    syncPlanButtons();
    try {
      const data = await dx.api("/admin/daedalus/planner/plans", { timeoutMs: PLAN_TIMEOUT_MS });
      if (!planStill(gen) || flight !== planFlight) return;
      if (!data || !Array.isArray(data.plans)) {
        if (!planHoldMessage()) showStatus("plan-status", dx.t("studio.planNotOk"), true);
        else showPlanListStatus();
        $("plan-list").replaceChildren();
        return;
      }
      renderPlans(data.plans, gen);
      showPlanListStatus(opts);
    } catch (err) {
      if (!planStill(gen) || flight !== planFlight) return;
      if (!planHoldMessage()) showStatus("plan-status", reviewFail(err, "studio.planUnknown"), true);
      else showPlanListStatus();
      $("plan-list").replaceChildren();
    } finally {
      if (planFlight === flight) {
        planFlight = 0;
        syncPlanButtons();
      }
    }
  }

  async function generatePlan() {
    if (planFlight || planGenerateOp || currentPanel !== "review") return;
    const goal = ($("plan-goal").value || "").trim();
    if (!goal) {
      showStatus("plan-status", dx.t("studio.planNeedGoal"), true);
      return;
    }
    if (goal.length > PLAN_GOAL_MAX) {
      showStatus("plan-status", dx.t("studio.planTooLong"), true);
      return;
    }
    const gen = planGen;
    if (!await ask("planGenerate", dx.t("studio.planGenerateConfirm", { goal }))) return;
    if (!planStill(gen) || planGenerateOp) return;
    planGenerateOp = "pending";
    syncPlanButtons();
    showStatus("plan-status", dx.t("studio.planGeneratePending"), true);
    let refresh = false;
    dx.api("/admin/daedalus/planner/plans/auto", {
      method: "POST",
      body: JSON.stringify({ goal }),
      timeoutMs: PLAN_TIMEOUT_MS,
    }).then(async (data) => {
      if (!data || data.ok !== true) {
        planGenerateOp = null;
        syncPlanButtons();
        if (!planStill(gen)) return;
        const detail = data && (data.error || data.detail);
        showStatus("plan-status", dx.t("studio.planNotOk") + (detail ? ": " + detail : ""), true);
        return;
      }
      const id = String((data.plan && data.plan.id) || data.plan_id || "");
      if (!id) {
        planGenerateOp = null;
        syncPlanButtons();
        if (planStill(gen)) showStatus("plan-status", dx.t("studio.planNotOk"), true);
        return;
      }
      planGenerateOp = null;
      syncPlanButtons();
      if (planStill(gen)) showStatus("plan-status", dx.t("studio.planDraft", { id }), false);
      try {
        const stored = await dx.api("/admin/daedalus/planner/plans/" + encodeURIComponent(id), { timeoutMs: PLAN_TIMEOUT_MS });
        if (!planStill(gen)) return;
        if (!stored || String(stored.id || "") !== id) {
          showStatus("plan-status", dx.t("studio.planMissing", { id }), true);
          return;
        }
        planDetail = stored;
        refresh = true;
      } catch (err) {
        if (!planStill(gen)) return;
        const message = String((err && err.message) || err || "");
        if (/not found|404/i.test(message)) {
          showStatus("plan-status", dx.t("studio.planMissing", { id }), true);
          return;
        }
        showStatus("plan-status", reviewFail(err, "studio.planUnknown"), true);
      }
    }).catch((err) => {
      if (reviewUnresolved(err)) {
        planGenerateOp = "unknown";
        syncPlanButtons();
        if (planStill(gen)) showStatus("plan-status", dx.t("studio.planUnknown"), true);
        return;
      }
      planGenerateOp = null;
      syncPlanButtons();
      if (planStill(gen)) showStatus("plan-status", reviewFail(err, "studio.planUnknown"), true);
    }).finally(() => {
      if (refresh && planStill(gen)) loadPlans({ keepStatus: true });
    });
  }

  async function approvePlan(id, gen, btn) {
    if (currentPanel !== "review" || !btn.isConnected) return;
    if (btn.dataset.gen !== String(planGen) || gen !== planGen) return;
    if (planApproved.has(id) || planDeleted.has(id) || planApproveOp.has(id)) return;
    if (!await ask("planApprove", dx.t("studio.planApproveConfirm", { id }))) return;
    if (!planStill(gen) || !btn.isConnected || btn.dataset.gen !== String(planGen)) return;
    if (planApproved.has(id) || planApproveOp.has(id)) return;
    planApproveOp.set(id, "pending");
    syncPlanButtons();
    showStatus("plan-status", dx.t("studio.planApprovePending", { id }), true);
    let refresh = false;
    dx.api("/admin/daedalus/planner/plans/" + encodeURIComponent(id) + "/approve", {
      method: "POST",
      timeoutMs: PLAN_TIMEOUT_MS,
    }).then((data) => {
      if (!data || data.ok !== true) {
        planApproveOp.delete(id);
        if (!planStill(gen)) return;
        const detail = data && (data.error || data.detail);
        showStatus("plan-status", dx.t("studio.planNotOk") + (detail ? ": " + detail : ""), true);
        return;
      }
      planApproveOp.delete(id);
      planApproved.add(id);
      dropRowAct("plan-list", id, "approve");
      if (!planStill(gen)) return;
      const count = Array.isArray(data.tasks_created) ? data.tasks_created.length : 0;
      showStatus("plan-status", dx.t("studio.planApproved", { id, count }), false);
      refresh = true;
    }).catch((err) => {
      if (reviewUnresolved(err)) {
        planApproveOp.set(id, "unknown");
        if (planStill(gen)) showStatus("plan-status", dx.t("studio.planUnknown"), true);
        return;
      }
      planApproveOp.delete(id);
      if (planStill(gen)) showStatus("plan-status", reviewFail(err, "studio.planUnknown"), true);
    }).finally(() => {
      syncPlanButtons();
      if (refresh && planStill(gen)) loadPlans({ keepStatus: true });
    });
  }

  async function deletePlan(id, gen, btn) {
    if (planFlight || currentPanel !== "review" || !btn.isConnected) return;
    if (btn.dataset.gen !== String(planGen) || gen !== planGen) return;
    if (planDeleted.has(id)) return;
    if (!await askDanger("planDelete", dx.t("studio.planDeleteConfirm", { id }))) return;
    if (!planStill(gen) || !btn.isConnected || btn.dataset.gen !== String(planGen)) return;
    const flight = ++planSeq;
    planFlight = flight;
    syncPlanButtons();
    let refresh = false;
    dx.api("/admin/daedalus/planner/plans/" + encodeURIComponent(id), {
      method: "DELETE",
      timeoutMs: PLAN_TIMEOUT_MS,
    }).then((data) => {
      if (!planStill(gen) || flight !== planFlight) return;
      if (!data || data.ok !== true) {
        const detail = data && (data.error || data.detail);
        showStatus("plan-status", dx.t("studio.planNotOk") + (detail ? ": " + detail : ""), true);
        return;
      }
      planDeleted.add(id);
      if (planDetail && String(planDetail.id || "") === id) planDetail = null;
      showStatus("plan-status", dx.t("studio.planDeleted", { id }), false);
      refresh = true;
    }).catch((err) => {
      if (!planStill(gen) || flight !== planFlight) return;
      showStatus("plan-status", reviewFail(err, "studio.planUnknown"), true);
    }).finally(() => {
      if (planFlight === flight) {
        planFlight = 0;
        syncPlanButtons();
      }
      if (refresh && planStill(gen)) loadPlans({ keepStatus: true });
    });
  }

  function discoveryStill(gen) {
    return gen === discoveryGen && currentPanel === "review";
  }

  function syncDiscoveryButtons() {
    const locked = discoveryFlight !== 0;
    const refresh = $("discovery-refresh");
    const filter = $("discovery-filter");
    if (refresh) refresh.disabled = locked;
    if (filter) filter.disabled = locked;
    document.querySelectorAll("#discovery-list button").forEach((btn) => {
      btn.disabled = locked || btn.dataset.keep === "1" || discoveryApproveOp.has(btn.dataset.id || "");
    });
  }

  function discoveryMeta(item) {
    const meta = item && item.metadata;
    return meta && typeof meta === "object" ? meta : {};
  }

  function renderDiscoveries(rows, gen) {
    const list = $("discovery-list");
    list.replaceChildren();
    if (!rows.length) {
      const empty = document.createElement("div");
      empty.className = "empty";
      empty.textContent = dx.t("studio.discoveryNone");
      list.appendChild(empty);
      return;
    }
    rows.forEach((item) => {
      if (!item || typeof item !== "object") return;
      const id = String(item.id || "");
      const meta = discoveryMeta(item);
      const status = String(meta.status || "");
      const el = document.createElement("div");
      el.className = "item";
      el.dataset.id = id;
      el.dataset.status = status;
      const title = document.createElement("b");
      title.textContent = id;
      const sub = document.createElement("small");
      sub.textContent = [status, meta.title, meta.url, meta.kind, item.content]
        .filter((value) => typeof value === "string" && value)
        .join("\n");
      const actions = document.createElement("div");
      actions.className = "row tight";
      const closed = discoveryApproved.has(id) || discoveryRejected.has(id) || discoveryApproveOp.has(id) || status === "approved" || status === "rejected";
      if (id && !closed) {
        const approve = document.createElement("button");
        approve.type = "button";
        approve.dataset.act = "approve";
        approve.dataset.gen = String(gen);
        approve.dataset.id = id;
        approve.textContent = dx.t("studio.discoveryApprove");
        approve.addEventListener("click", () => approveDiscovery(id, gen, approve, meta));
        const reject = document.createElement("button");
        reject.type = "button";
        reject.dataset.act = "reject";
        reject.dataset.gen = String(gen);
        reject.dataset.id = id;
        reject.textContent = dx.t("studio.discoveryReject");
        reject.addEventListener("click", () => rejectDiscovery(id, gen, reject));
        actions.append(approve, reject);
      }
      el.append(title, sub, actions);
      discoveryExtras(id, actions);
      list.appendChild(el);
    });
  }

  async function loadDiscoveries(opts) {
    if (currentPanel !== "review") return;
    if (discoveryFlight) return;
    const gen = discoveryGen;
    const flight = ++discoverySeq;
    discoveryFlight = flight;
    syncDiscoveryButtons();
    const filter = $("discovery-filter");
    const status = filter && filter.value ? filter.value : "pending";
    try {
      const data = await dx.api(
        "/admin/daedalus/discoveries?status=" + encodeURIComponent(status) + "&limit=100",
        { timeoutMs: DISCOVERY_TIMEOUT_MS }
      );
      if (!discoveryStill(gen) || flight !== discoveryFlight) return;
      if (!data || !Array.isArray(data.discoveries) || data.error) {
        if (!discoveryHoldMessage()) {
          const detail = data && data.error;
          showStatus("discovery-status", dx.t("studio.discoveryNotOk") + (detail ? ": " + detail : ""), true);
        } else showDiscoveryListStatus();
        $("discovery-list").replaceChildren();
        return;
      }
      renderDiscoveries(data.discoveries, gen);
      showDiscoveryListStatus(opts);
    } catch (err) {
      if (!discoveryStill(gen) || flight !== discoveryFlight) return;
      if (!discoveryHoldMessage()) showStatus("discovery-status", reviewFail(err, "studio.discoveryUnknown"), true);
      else showDiscoveryListStatus();
      $("discovery-list").replaceChildren();
    } finally {
      if (discoveryFlight === flight) {
        discoveryFlight = 0;
        syncDiscoveryButtons();
      }
    }
  }

  async function approveDiscovery(id, gen, btn, meta) {
    if (currentPanel !== "review" || !btn.isConnected) return;
    if (btn.dataset.gen !== String(discoveryGen) || gen !== discoveryGen) return;
    if (discoveryApproved.has(id) || discoveryRejected.has(id) || discoveryApproveOp.has(id)) return;
    let question = dx.t("studio.discoveryApproveConfirm", { id });
    if (meta && meta.for_self) question += "\n" + dx.t("studio.discoveryForSelf");
    if (!await ask("discoveryApprove", question)) return;
    if (!discoveryStill(gen) || !btn.isConnected || btn.dataset.gen !== String(discoveryGen)) return;
    if (discoveryApproved.has(id) || discoveryApproveOp.has(id)) return;
    discoveryApproveOp.set(id, "pending");
    syncDiscoveryButtons();
    showStatus("discovery-status", dx.t("studio.discoveryApprovePending", { id }), true);
    let refresh = false;
    dx.api("/admin/daedalus/discoveries/" + encodeURIComponent(id) + "/approve", {
      method: "POST",
      timeoutMs: DISCOVERY_TIMEOUT_MS,
    }).then((data) => {
      if (!data || data.approved !== true) {
        discoveryApproveOp.delete(id);
        syncDiscoveryButtons();
        if (!discoveryStill(gen)) return;
        showStatus("discovery-status", dx.t("studio.discoveryNotApproved", { id }), true);
        return;
      }
      discoveryApproveOp.delete(id);
      discoveryApproved.add(id);
      dropRowAct("discovery-list", id);
      if (!discoveryStill(gen)) return;
      const key = data.plan_id === "pending" ? "studio.discoveryScheduled" : "studio.discoveryUnscheduled";
      showStatus("discovery-status", dx.t(key, { id }), false);
      refresh = true;
    }).catch((err) => {
      if (reviewUnresolved(err)) {
        discoveryApproveOp.set(id, "unknown");
        syncDiscoveryButtons();
        if (discoveryStill(gen)) showStatus("discovery-status", dx.t("studio.discoveryUnknown"), true);
        return;
      }
      discoveryApproveOp.delete(id);
      syncDiscoveryButtons();
      if (discoveryStill(gen)) showStatus("discovery-status", reviewFail(err, "studio.discoveryUnknown"), true);
    }).finally(() => {
      syncDiscoveryButtons();
      if (refresh && discoveryStill(gen)) loadDiscoveries({ keepStatus: true });
    });
  }

  async function rejectDiscovery(id, gen, btn) {
    if (discoveryFlight || currentPanel !== "review" || !btn.isConnected) return;
    if (btn.dataset.gen !== String(discoveryGen) || gen !== discoveryGen) return;
    if (discoveryApproved.has(id) || discoveryRejected.has(id) || discoveryApproveOp.has(id)) return;
    if (!await ask("discoveryReject", dx.t("studio.discoveryRejectConfirm", { id }))) return;
    if (!discoveryStill(gen) || !btn.isConnected || btn.dataset.gen !== String(discoveryGen)) return;
    const flight = ++discoverySeq;
    discoveryFlight = flight;
    syncDiscoveryButtons();
    let refresh = false;
    dx.api("/admin/daedalus/discoveries/" + encodeURIComponent(id) + "/reject", {
      method: "POST",
      timeoutMs: DISCOVERY_TIMEOUT_MS,
    }).then((data) => {
      if (!discoveryStill(gen) || flight !== discoveryFlight) return;
      if (!data || data.rejected !== true) {
        showStatus("discovery-status", dx.t("studio.discoveryNotOk"), true);
        return;
      }
      discoveryRejected.add(id);
      showStatus("discovery-status", dx.t("studio.discoveryRejectedDone", { id }), false);
      refresh = true;
    }).catch((err) => {
      if (!discoveryStill(gen) || flight !== discoveryFlight) return;
      showStatus("discovery-status", reviewFail(err, "studio.discoveryUnknown"), true);
    }).finally(() => {
      if (discoveryFlight === flight) {
        discoveryFlight = 0;
        syncDiscoveryButtons();
      }
      if (refresh && discoveryStill(gen)) loadDiscoveries({ keepStatus: true });
    });
  }

  function skillVisible() {
    return currentPanel === "review";
  }

  function skillPlatform() {
    const value = ($("skill-platform") && $("skill-platform").value) || "generic";
    return SKILL_PLATFORMS.indexOf(value) >= 0 ? value : "generic";
  }

  function skillLines(parts) {
    return parts.filter((line) => line).join("\n");
  }

  function skillPhaseLine(data) {
    const ready = data && data.ready_for_generation === true;
    return dx.t("studio.skillPhaseLine", {
      phase: String((data && data.phase) || ""),
      ready: dx.t(ready ? "studio.skillReadyYes" : "studio.skillReadyNo"),
    });
  }

  function acceptSkillView(data) {
    skillView = {
      session_id: String((data && data.session_id) || ""),
      phase: data && data.phase,
      platform: data && data.platform,
      idea: data && data.idea,
      research_notes: data && data.research_notes,
      tool_steps: data && Array.isArray(data.tool_steps) ? data.tool_steps : [],
      skill_name: data && data.skill_name,
      summary: data && data.summary,
      clarifying_questions: data && Array.isArray(data.clarifying_questions) ? data.clarifying_questions : [],
      ready_for_generation: !!(data && data.ready_for_generation === true),
      validation_warnings: data && Array.isArray(data.validation_warnings) ? data.validation_warnings : [],
    };
  }

  function takeSkillFiles(data) {
    const raw = data && data.files;
    if (!raw || typeof raw !== "object" || Array.isArray(raw)) return false;
    const files = {};
    Object.keys(raw).forEach((path) => {
      if (typeof raw[path] === "string" && raw[path]) files[path] = raw[path];
    });
    if (!Object.keys(files).length) return false;
    skillFiles = {
      sessionId: String((data && data.session_id) || ""),
      skillName: String((data && data.skill_name) || "skill"),
      files,
    };
    return true;
  }

  function skillFileNames() {
    return skillFiles && skillFiles.files ? Object.keys(skillFiles.files) : [];
  }

  function skillMdPath() {
    return skillFileNames().find((path) => path === "SKILL.md" || path.endsWith("/SKILL.md")) || "";
  }

  function showSkillStatus() {
    if (!skillVisible()) return;
    if (skillOp && skillOp.state === "unknown") {
      showStatus("skill-status", dx.t("studio.skillUnknown"), true);
      return;
    }
    if (skillOp && skillOp.state === "pending") {
      const key = skillOp.action === "refine"
        ? "studio.skillRefinePending"
        : skillOp.action === "generate"
          ? "studio.skillGeneratePending"
          : "studio.skillStartPending";
      showStatus("skill-status", dx.t(key), true);
      return;
    }
    if (skillNote) showStatus("skill-status", skillNote, skillNoteBad);
    else showStatus("skill-status", "", false);
  }

  function paintSkill() {
    if (!skillVisible()) return;
    showSkillStatus();
    const phase = $("skill-phase");
    const research = $("skill-research");
    const questions = $("skill-questions");
    const warnings = $("skill-warnings");
    const files = $("skill-files");
    if (phase) phase.textContent = skillView ? skillPhaseLine(skillView) : "";
    if (research) research.textContent = skillView ? String(skillView.research_notes || "") : "";
    if (questions) {
      questions.replaceChildren();
      ((skillView && skillView.clarifying_questions) || []).forEach((item) => {
        const row = document.createElement("div");
        const ask = document.createElement("div");
        ask.textContent = typeof item === "string" ? item : String((item && item.question) || "");
        row.appendChild(ask);
        if (item && item.why) {
          const why = document.createElement("small");
          why.textContent = String(item.why);
          row.appendChild(why);
        }
        questions.appendChild(row);
      });
    }
    if (warnings) {
      warnings.textContent = ((skillView && skillView.validation_warnings) || []).map((item) => String(item)).join("\n");
    }
    if (files) {
      files.replaceChildren();
      skillFileNames().forEach((path) => {
        const row = document.createElement("div");
        row.textContent = path;
        files.appendChild(row);
      });
    }
    syncSkillButtons();
  }

  function syncSkillButtons() {
    const locked = skillOp !== null;
    const ready = !!(skillView && skillView.ready_for_generation === true);
    const hasSession = !!(skillView && skillView.session_id);
    const start = $("skill-start");
    const refine = $("skill-refine");
    const generate = $("skill-generate");
    const force = $("skill-force");
    if (start) start.disabled = locked;
    if (refine) refine.disabled = locked || !hasSession;
    if (generate) generate.disabled = locked || !ready;
    if (force) force.disabled = locked || !hasSession || (ready && !skillGenerateRefused);
    const download = $("skill-download");
    const bundle = $("skill-bundle");
    if (download) download.hidden = !skillMdPath();
    if (bundle) bundle.hidden = skillFileNames().length === 0;
    const gist = $("skill-gist");                         // publishing (A6.3c): only a generated skill, one request at a time
    if (gist) {
      gist.hidden = !(skillView && skillView.phase === "generated") || !!(gistDone && skillView && gistDone.id === skillView.session_id);
      gist.disabled = locked || gistFlight;
    }
  }

  function describeSkillStart(data) {
    const questions = Array.isArray(data.clarifying_questions) ? data.clarifying_questions : [];
    const ready = data.ready_for_generation === true;
    const lines = [
      dx.t("studio.skillStarted", { id: String(data.session_id || "") }),
      dx.t("studio.skillNoSearch"),
      skillPhaseLine(data),
    ];
    if (String(data.research_notes || "") === String(data.idea || "")) lines.push(dx.t("studio.skillNoteIsIdea"));
    if (!Array.isArray(data.tool_steps) || data.tool_steps.length === 0) lines.push(dx.t("studio.skillNoTools"));
    if (!questions.length && !ready && data.skill_name === "skill" && data.summary === data.idea) {
      lines.push(dx.t("studio.skillFallback"));
    }
    return skillLines(lines);
  }

  function finishSkill(note, bad) {
    skillOp = null;
    skillNote = note;
    skillNoteBad = !!bad;
    if (skillVisible()) paintSkill();
    else syncSkillButtons();
  }

  function skillCaught(action, err) {
    if (reviewUnresolved(err)) {
      skillOp = { action, state: "unknown" };
      if (skillVisible()) paintSkill();
      else syncSkillButtons();
      return;
    }
    if (action === "generate") skillGenerateRefused = true;
    finishSkill(reviewFail(err, "studio.skillUnknown"), true);
  }

  async function startSkill() {
    if (skillOp || currentPanel !== "review") return;
    const idea = ($("skill-idea").value || "").trim();
    const platform = skillPlatform();
    if (!idea) {
      showStatus("skill-status", dx.t("studio.skillNeedIdea"), true);
      return;
    }
    if (!await ask("skillStart", dx.t("studio.skillStartConfirm", { idea, platform }))) return;
    if (skillOp || currentPanel !== "review") return;
    skillOp = { action: "start", state: "pending" };
    syncSkillButtons();
    showStatus("skill-status", dx.t("studio.skillStartPending"), true);
    dx.api("/admin/daedalus/playground/api/skill-creator/start", {
      method: "POST",
      body: JSON.stringify({ idea, platform }),
      timeoutMs: SKILL_TIMEOUT_MS,
    }).then((data) => {
      if (!data || data.ok !== true) {
        const detail = data && (data.error || data.detail);
        finishSkill(dx.t("studio.skillNotOk") + (detail ? ": " + detail : ""), true);
        return;
      }
      acceptSkillView(data);
      skillGenerateRefused = false;
      finishSkill(describeSkillStart(data), false);
    }).catch((err) => skillCaught("start", err));
  }

  async function refineSkill() {
    if (skillOp || currentPanel !== "review" || !skillView || !skillView.session_id) return;
    const message = ($("skill-message").value || "").trim();
    if (!message) {
      showStatus("skill-status", dx.t("studio.skillNeedMessage"), true);
      return;
    }
    const id = skillView.session_id;
    if (!await ask("skillRefine", dx.t("studio.skillRefineConfirm", { id }))) return;
    if (skillOp || currentPanel !== "review") return;
    skillOp = { action: "refine", state: "pending" };
    syncSkillButtons();
    showStatus("skill-status", dx.t("studio.skillRefinePending"), true);
    dx.api("/admin/daedalus/playground/api/skill-creator/refine", {
      method: "POST",
      body: JSON.stringify({ session_id: id, message }),
      timeoutMs: SKILL_TIMEOUT_MS,
    }).then((data) => {
      if (!data || data.ok !== true) {
        const detail = data && (data.error || data.detail);
        finishSkill(dx.t("studio.skillNotOk") + (detail ? ": " + detail : ""), true);
        return;
      }
      acceptSkillView(data);
      skillGenerateRefused = false;
      finishSkill(skillLines([
        dx.t("studio.skillRefined", { id: String(data.session_id || id) }),
        skillPhaseLine(data),
        skillFiles ? dx.t("studio.skillFilesKept") : "",
      ]), false);
    }).catch((err) => skillCaught("refine", err));
  }

  async function generateSkill(force) {
    if (skillOp || currentPanel !== "review" || !skillView || !skillView.session_id) return;
    if (!force && skillView.ready_for_generation !== true) return;
    const id = skillView.session_id;
    const question = force
      ? dx.t("studio.skillForceConfirm", { id })
      : dx.t("studio.skillGenerateConfirm", { id });
    if (!await ask("skillGenerate", question)) return;
    if (skillOp || currentPanel !== "review") return;
    skillOp = { action: "generate", state: "pending" };
    syncSkillButtons();
    showStatus("skill-status", dx.t("studio.skillGeneratePending"), true);
    const requestBody = force
      ? JSON.stringify({ session_id: id, force: true })
      : JSON.stringify({ session_id: id });
    dx.api("/admin/daedalus/playground/api/skill-creator/generate", {
      method: "POST",
      body: requestBody,
      timeoutMs: SKILL_TIMEOUT_MS,
    }).then((data) => {
      if (!data || data.ok !== true) {
        skillGenerateRefused = true;
        const detail = data && (data.error || data.detail);
        finishSkill(dx.t("studio.skillNotOk") + (detail ? ": " + detail : ""), true);
        return;
      }
      const kept = !takeSkillFiles(data);
      acceptSkillView(data);
      skillGenerateRefused = false;
      const warnings = (skillView.validation_warnings || []).map((item) => String(item)).join("\n");
      finishSkill(skillLines([
        kept ? dx.t("studio.skillNoFiles") : dx.t("studio.skillFilesReturned"),
        kept && skillFiles ? dx.t("studio.skillFilesKept") : "",
        skillPhaseLine(data),
        warnings,
      ]), kept);
    }).catch((err) => skillCaught("generate", err));
  }

  function downloadText(filename, content, mime) {
    const blob = new Blob([content], { type: mime || "text/plain;charset=utf-8" });
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(link.href);
  }

  function downloadSkillMd() {
    const path = skillMdPath();
    if (!path || !skillFiles) return;
    downloadText(path.split("/").pop() || "SKILL.md", skillFiles.files[path], "text/markdown;charset=utf-8");
  }

  function downloadSkillBundle() {
    if (!skillFiles || !skillFileNames().length) return;
    let bundle = "# Skill bundle: " + (skillFiles.skillName || "skill") + "\n\n";
    skillFileNames().forEach((path) => {
      bundle += "\n--- " + path + " ---\n" + skillFiles.files[path] + "\n";
    });
    downloadText((skillFiles.skillName || "skill") + "-bundle.txt", bundle, "text/plain;charset=utf-8");
  }

  function refVisible() {
    return currentPanel === "review";
  }

  function refIsStub(data) {
    const mechanics = data && data.mechanics;
    return !!(data
      && [("Reference: " + String(data.reference || "") + ". Take inspiration from its mechanics and look."),
          ("Референция: " + String(data.reference || "") + ". Вдъхнови се от механиките и визията.")]
        .includes(String(data.summary || ""))
      && String(data.visual_style || "") === "match reference game art style"
      && String(data.scope_hints || "") === "Core loop first, polish later"
      && Array.isArray(mechanics) && mechanics.length === 0);
  }

  function showRefStatus() {
    if (!refVisible()) return;
    if (refOp === "unknown") {
      showStatus("ref-status", dx.t("studio.refUnknown"), true);
      return;
    }
    if (refOp === "pending") {
      showStatus("ref-status", dx.t("studio.refPending"), true);
      return;
    }
    if (refNote) showStatus("ref-status", refNote, refNoteBad);
    else showStatus("ref-status", "", false);
  }

  function paintRef() {
    if (!refVisible()) return;
    showRefStatus();
    const box = $("ref-brief");
    if (!box) return;
    box.replaceChildren();
    if (!refResult) {
      syncRefButtons();
      return;
    }
    const add = (text) => {
      const row = document.createElement("p");
      row.textContent = text;
      box.appendChild(row);
    };
    add(dx.t("studio.refUnverified"));
    if (refIsStub(refResult)) add(dx.t("studio.refStub"));
    add(String(refResult.title || ""));
    add(String(refResult.summary || ""));
    (Array.isArray(refResult.mechanics) ? refResult.mechanics : []).forEach((item) => add(String(item)));
    if (refResult.visual_style) add(String(refResult.visual_style));
    if (refResult.scope_hints) add(String(refResult.scope_hints));
    (Array.isArray(refResult.sources) ? refResult.sources : []).forEach((item) => {
      if (!item || typeof item !== "object") return;
      const bits = [item.title, item.url].filter((value) => typeof value === "string" && value);
      if (bits.length) add(bits.join(" "));
    });
    syncRefButtons();
  }

  function syncRefButtons() {
    const scout = $("ref-scout");
    if (scout) scout.disabled = refOp !== null;
  }

  async function scoutReference() {
    if (refOp || currentPanel !== "review") return;
    const reference = ($("ref-input").value || "").trim();
    if (!reference) {
      showStatus("ref-status", dx.t("studio.refNeed"), true);
      return;
    }
    if (!await ask("refScout", dx.t("studio.refConfirm", { reference }))) return;
    if (refOp || currentPanel !== "review") return;
    refOp = "pending";
    syncRefButtons();
    showStatus("ref-status", dx.t("studio.refPending"), true);
    dx.api("/admin/daedalus/playground/api/reference-scout", {
      method: "POST",
      body: JSON.stringify({ reference }),
      timeoutMs: REF_TIMEOUT_MS,
    }).then((data) => {
      if (!data || data.ok !== true) {
        refOp = null;
        const detail = data && (data.error || data.detail);
        refNote = dx.t("studio.refNotOk") + (detail ? ": " + detail : "");
        refNoteBad = true;
        if (refVisible()) paintRef();
        else syncRefButtons();
        return;
      }
      refOp = null;
      refResult = data;
      refNote = dx.t("studio.refReturned");
      refNoteBad = !!refIsStub(data);
      if (refVisible()) paintRef();
      else syncRefButtons();
    }).catch((err) => {
      if (reviewUnresolved(err)) {
        refOp = "unknown";
        if (refVisible()) paintRef();
        else syncRefButtons();
        return;
      }
      refOp = null;
      refNote = reviewFail(err, "studio.refUnknown");
      refNoteBad = true;
      if (refVisible()) paintRef();
      else syncRefButtons();
    });
  }


  // ---- A6.2a: bulk permissions, blocker fixes, details, approve-all ---------------------------------------------
  const REV_ERR = { confirm_required: "studio.revErrConfirm", not_found: "studio.revErrNotFound", already_resolved: "studio.revErrResolved",
    no_project: "studio.revErrNoProject", not_infra: "studio.revErrNotInfra", no_fix_available: "studio.revErrNoFix",
    project_not_found: "studio.revErrProjectGone", remediate_failed: "studio.revErrFailed", unavailable: "studio.revErrUnavailable",
    id_required: "studio.revErrFailed", ids_required: "studio.revErrFailed" };
  const REVIEW_HOURS = [1, 4, 8, 24, 72];

  function revErr(err) {
    const code = String((err && err.message) || "");
    return REV_ERR[code] ? dx.t(REV_ERR[code]) : failText(err);
  }

  function fillHours(sel, def) {
    REVIEW_HOURS.forEach((h) => {
      const o = document.createElement("option");
      o.value = String(h);
      o.textContent = h + " h";
      sel.appendChild(o);
    });
    sel.value = String(def);
    return sel;
  }

  function hoursSelect(def) {
    const sel = document.createElement("select");
    sel.setAttribute("aria-label", dx.t("studio.bulkHours"));
    return fillHours(sel, def);
  }

  // A row of bulk actions for a Review tab: title, hint, an optional hours choice, buttons, and a note for the outcome.
  // A button's ``run`` returns {text, bad}, or null when the owner declined and there is nothing to say.
  function bulkBox(id, titleKey, hintKey, hours, buttons) {
    const el = document.createElement("div");
    el.className = "item bulk";
    el.dataset.id = id;
    const b = document.createElement("b");
    b.textContent = dx.t(titleKey);
    el.appendChild(b);
    if (hintKey) {
      const small = document.createElement("small");
      small.textContent = dx.t(hintKey);
      el.appendChild(small);
    }
    const actions = document.createElement("div");
    actions.className = "row tight";
    if (hours) actions.appendChild(hours);
    const note = document.createElement("p");
    note.className = "cfg-note";
    note.hidden = true;
    buttons.forEach((def) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.textContent = def.label;
      btn.addEventListener("click", async () => {
        el.dataset.state = "";
        note.hidden = true;
        await runAction(btn, "review-status", async () => {
          const res = await def.run();
          if (!res) return;
          lastNotes[id] = res;
          const live = el.isConnected ? el : document.querySelector(".item.bulk[data-id='" + id + "']");
          if (live) showNote(live, res);
        });
      });
      actions.appendChild(btn);
    });
    el.append(actions, note);
    if (lastNotes[id]) showNote(el, lastNotes[id]);
    return el;
  }

  function permissionsToolbar() {
    const hours = hoursSelect(24);
    return bulkBox("bulk-permissions", "studio.bulkPermTitle", "studio.bulkPermHint", hours, [
      { label: dx.t("studio.permGrantAll"), run: async () => {
        const h = Number(hours.value);
        try {
          const prev = await dx.api("/api/v1/harness/review/permissions");
          if (!await askDanger("permGrantAll", dx.t("studio.permGrantAllConfirm", { n: (prev.known || []).length, h }))) return null;
          const r = await postJson("/api/v1/harness/review/permissions/grant-all", { hours: h, confirm: true });
          await loadReview("permissions");
          return { text: dx.t("studio.permGranted", { n: r.count, h: r.hours }), bad: false };
        } catch (err) { return { text: revErr(err), bad: true }; }
      } },
      { label: dx.t("studio.permRenew"), run: async () => {
        const h = Number(hours.value);
        try {
          const prev = await dx.api("/api/v1/harness/review/permissions");
          const n = (prev.renewable || []).length;
          if (!n) return { text: dx.t("studio.permNothingToRenew"), bad: false };
          if (!await ask("permRenew", dx.t("studio.permRenewConfirm", { n, h }))) return null;
          const r = await postJson("/api/v1/harness/review/permissions/renew-expired", { hours: h, confirm: true });
          await loadReview("permissions");
          return { text: dx.t("studio.permGranted", { n: r.count, h: r.hours }), bad: false };
        } catch (err) { return { text: revErr(err), bad: true }; }
      } },
    ]);
  }

  function approveAllToolbar(pendingIds) {
    return bulkBox("bulk-approve-all", "studio.approveAllTitle", null, null, [
      { label: dx.t("studio.approveAll"), run: async () => {
        if (!pendingIds.length) return { text: dx.t("studio.approveAllNone"), bad: false };
        if (!await askDanger("approveAll", dx.t("studio.approveAllConfirm", { n: pendingIds.length }))) return null;
        try {
          // exactly the proposals the question was about: one added meanwhile is not approved by this click
          const r = await postJson("/api/v1/harness/review/proposals/approve-all", { ids: pendingIds, confirm: true });
          await loadReview("proposals");
          if (r.ok) return { text: dx.t("studio.approveAllDone", { n: r.applied }), bad: false };
          const f = (r.failed || []).length;
          return { text: dx.t(r.applied ? "studio.approveAllPartial" : "studio.approveAllFailed", { n: r.applied, f }), bad: true };
        } catch (err) { return { text: revErr(err), bad: true }; }
      } },
    ]);
  }

  // Details: a trimmed diff shown under the row. textContent only, so nothing in a diff is ever treated as markup.
  function detailBlock(lines, diffText, truncated) {
    const pre = document.createElement("pre");
    pre.className = "detail";
    const head = lines.filter(Boolean).join("\n");
    pre.textContent = (head ? head + "\n\n" : "") + (diffText || dx.t("studio.detailNoDiff")) + (truncated ? "\n\n… " + dx.t("studio.detailTruncated") : "");
    return pre;
  }

  async function toggleDetail(act, id, button) {
    const item = button.closest(".item");
    if (!item) return;
    const open = item.querySelector(".detail");
    if (open) {
      open.remove();
      return;
    }
    let block;
    try {
      if (act === "detail-proposal") {
        const d = await dx.api("/api/v1/harness/review/proposals/" + encodeURIComponent(id));
        block = detailBlock([d.file_path, [d.status, d.risk_level != null ? dx.t("studio.detailRisk", { risk: d.risk_level }) : ""].filter(Boolean).join(" · "),
          d.reason, d.risk_rationale], d.diff, d.truncated);
      } else {
        const d = await dx.api("/api/v1/harness/review/bundles/" + encodeURIComponent(id));
        const files = d.files || [];
        const diff = files.map((f) => "# " + f.action + " " + f.file_path + (f.reason ? "  (" + f.reason + ")" : "") + "\n" + (f.diff || "")).join("\n\n");
        block = detailBlock([d.title, d.status], diff, files.some((f) => f.truncated));
      }
    } catch (err) {
      showStatus("review-status", revErr(err), true);
      return;
    }
    item.appendChild(block);
  }


  // The extra actions of an open blocker. They live here, apart from the load/resolve code above, on purpose: that code
  // stays a pure read (Resolve only marks a blocker resolved), and these buttons each ask the owner before anything is granted.
  function blockerFixButtons(id) {
    const fix = document.createElement("button");
    fix.type = "button";
    fix.dataset.id = id;
    fix.dataset.role = "autofix";
    fix.textContent = dx.t("studio.blockerAutoFix");
    fix.addEventListener("click", () => blockerAutoFix(id, fix));
    const rem = document.createElement("button");
    rem.type = "button";
    rem.dataset.id = id;
    rem.dataset.role = "remediate";
    rem.textContent = dx.t("studio.blockerRemediate");
    rem.addEventListener("click", () => blockerRemediate(id, rem));
    return [fix, rem];
  }

  // The blocker actions take the same lock as Resolve (one request at a time, buttons greyed out) and drop their
  // result when the owner has left Review, so none of them can write to a screen that is no longer theirs.
  async function blockerLocked(btn, work) {
    if (blockerFlight) return;
    const gen = blockerGen;
    const flight = ++blockerSeq;
    blockerFlight = flight;
    syncBlockerButtons();
    let refresh = false;
    try {
      await runAction(btn, "blocker-status", async () => {
        try {
          const done = await work(() => blockerStill(gen));
          refresh = done === true;
        } catch (err) {
          if (blockerStill(gen)) showStatus("blocker-status", revErr(err), true);
        }
      });
    } finally {
      if (blockerFlight === flight) {
        blockerFlight = 0;
        syncBlockerButtons();
      }
      if (refresh && blockerStill(gen)) loadBlockers({ keepStatus: true });
    }
  }

  async function blockerAutoFix(id, btn) {
    const h = Number($("blocker-hours").value);
    if (!await ask("blockerAutoFix", dx.t("studio.blockerAutoFixConfirm", { h }))) return Promise.resolve();
    return blockerLocked(btn, async (still) => {
      const r = await postJson("/api/v1/harness/review/blockers/auto-fix", { id, hours: h, confirm: true });
      if (still()) showStatus("blocker-status", dx.t("studio.blockerFixed", { n: (r.granted || []).length, h: r.hours }), false);
      return true;
    });
  }

  function blockerRemediate(id, btn) {
    return blockerLocked(btn, async (still) => {
      const prev = await dx.api("/api/v1/harness/review/blockers/" + encodeURIComponent(id) + "/remediation");
      if (!still()) return false;
      if (prev.changes_kind && !await ask("blockerRemediate", dx.t("studio.blockerRemediateConfirm", { slug: prev.slug, old: prev.old_kind }))) return false;
      const r = await postJson("/api/v1/harness/review/blockers/" + encodeURIComponent(id) + "/remediate", { confirm_kind_change: !!prev.changes_kind });
      if (still()) showStatus("blocker-status", dx.t(r.already_static ? "studio.blockerAlreadyStatic" : "studio.blockerRemediated", { slug: r.slug, old: r.old_kind || "" }), false);
      return true;
    });
  }

  function blockersAutoFixAll(btn) {
    const h = Number($("blocker-hours").value);
    return blockerLocked(btn, async (still) => {
      const prev = await dx.api("/api/v1/harness/review/blockers");
      if (!still()) return false;
      if (!await ask("blockerAutoFixAll", dx.t("studio.blockerAutoFixAllConfirm", { h, n: prev.open }))) return false;
      const r = await postJson("/api/v1/harness/review/blockers/auto-fix", { all: true, hours: h, confirm: true });
      // "fixed 0, skipped N" is a result to read, not an error to hide
      if (still()) showStatus("blocker-status", dx.t("studio.blockerFixedAll", { fixed: r.fixed, skipped: r.skipped }), r.ok === false);
      return true;
    });
  }

  function blockersGrantRecommended(btn) {
    const h = Number($("blocker-hours").value);
    return blockerLocked(btn, async (still) => {
      const prev = await dx.api("/api/v1/harness/review/blockers");
      if (!still()) return false;
      const list = prev.recommended || [];
      if (!await ask("blockerGrantRec", dx.t("studio.blockerGrantRecConfirm", { n: list.length, h, list: list.join(", ") }))) return false;
      const r = await postJson("/api/v1/harness/review/blockers/grant-recommended", { hours: h, confirm: true });
      if (still()) showStatus("blocker-status", dx.t("studio.blockerFixed", { n: r.count, h: r.hours }), false);
      return false;
    });
  }

  async function reviewAct(act, id, button) {
    if (act === "detail-proposal" || act === "detail-bundle") {
      await runAction(button, "review-status", () => toggleDetail(act, id, button));
      // runAction puts the button's original label back when it finishes, so the label is set here, after it
      const item = button.closest(".item");
      button.textContent = item && item.querySelector(".detail") ? dx.t("studio.detailHide") : dx.t("studio.detail");
      return;
    }
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
      else if (act === "out-dismiss") path = "/admin/daedalus/outbox/" + encodeURIComponent(id) + "/dismiss";
      const data = await postJson(path, body);
      if (!data || data.ok !== true) {
        showStatus("review-status", dx.t("studio.requestFailed") + ": " + ((data && data.error) || ""), true);
        return;
      }
      showStatus("review-status", dx.t("studio.saved"), false);
      await loadReview(currentTab);
    });
  }


  // ---- A6.2b: planner extras and discovery actions -----------------------------------------------------------
  // Kept apart from the plan/discovery load and approve code on purpose: that code has its own locks and contract, and
  // these actions are cheap local writes (a step, a status) or an explicit, confirmed model turn (run a step, regenerate).
  const PLN_ERR = { title_required: "studio.plnErrTitle", too_long: "studio.plnErrLong", too_many_steps: "studio.plnErrSteps",
    bad_dependency: "studio.plnErrDep", bad_state: "studio.plnErrState", use_approve: "studio.plnErrApprove",
    already_running: "studio.plnErrRunning", plan_not_active: "studio.plnErrNotActive", dependency_open: "studio.plnErrDepOpen", status_not_allowed: "studio.plnErrStatus" };
  const STEP_STATES = ["pending", "passed", "failed", "blocked"];
  const openManagers = new Set();                  // plan ids whose steps are open: a reload of the list keeps them open
  const managerTimers = {};
  const regenWatch = new Set();                    // discovery ids whose plan generation we are waiting for
  let extraFlight = false;
  let regenTimer = null;

  function plnErr(err) {
    const code = String((err && err.message) || "");
    const key = PLN_ERR[code] || REV_ERR[code];
    return key ? dx.t(key) : failText(err);
  }

  function lockExtras(on) {
    document.querySelectorAll("[data-extra]").forEach((b) => { b.disabled = on || b.dataset.keep === "1"; });
    if (!on) { syncPlanButtons(); syncDiscoveryButtons(); }
  }

  // One action at a time; its outcome goes to the panel's own status line, and only if the owner is still on Review.
  async function extraRun(btn, statusId, work) {
    if (extraFlight) return;
    const pg = planGen;
    const dg = discoveryGen;
    extraFlight = true;
    lockExtras(true);
    dx.busy(btn, true, "…");
    const report = (text, bad) => { if (currentPanel === "review" && pg === planGen && dg === discoveryGen) showStatus(statusId, text, bad); };
    try {
      await work(report);
    } catch (err) {
      report(plnErr(err), true);
    } finally {
      dx.busy(btn, false);
      extraFlight = false;
      lockExtras(false);
    }
  }

  function xbtn(label, handler) {
    const b = document.createElement("button");
    b.type = "button";
    b.dataset.extra = "1";
    b.textContent = label;
    b.addEventListener("click", () => handler(b));
    return b;
  }

  function planExtras(id, status, actions, el) {
    if (!id) return;
    const manage = xbtn(dx.t(openManagers.has(id) ? "studio.planManageHide" : "studio.planManage"), () => toggleManager(id, el, manage));
    manage.dataset.role = "steps";
    actions.appendChild(manage);
    if (status === "active") {
      const pause = xbtn(dx.t("studio.planPause"), (b) => extraRun(b, "plan-status", async (say) => {
        await postJson("/api/v1/harness/review/planner/plans/" + encodeURIComponent(id) + "/pause", {});
        say(dx.t("studio.planPaused"), false);
        loadPlans({ keepStatus: true });
      }));
      pause.dataset.role = "pause";
      actions.appendChild(pause);
    }
    if (status === "paused") {
      const resume = xbtn(dx.t("studio.planResume"), async (b) => {
        if (!await ask("planResume", dx.t("studio.planResumeConfirm"))) return;
        extraRun(b, "plan-status", async (say) => {
          await postJson("/api/v1/harness/review/planner/plans/" + encodeURIComponent(id) + "/resume", { confirm: true });
          say(dx.t("studio.planResumed"), false);
          loadPlans({ keepStatus: true });
        });
      });
      resume.dataset.role = "resume";
      actions.appendChild(resume);
    }
    if (openManagers.has(id)) renderManager(id, el);
  }

  async function toggleManager(id, el, btn) {
    const existing = el.querySelector(":scope > .manage");
    if (existing) {
      existing.remove();
      openManagers.delete(id);
      clearTimeout(managerTimers[id]);
      btn.textContent = dx.t("studio.planManage");
      return;
    }
    openManagers.add(id);
    btn.textContent = dx.t("studio.planManageHide");
    await renderManager(id, el);
  }

  async function renderManager(id, el) {
    let box = el.querySelector(":scope > .manage");
    if (!box) {
      box = document.createElement("div");
      box.className = "manage";
      box.dataset.plan = id;
      el.appendChild(box);
    }
    try {
      const data = await dx.api("/api/v1/harness/review/planner/plans/" + encodeURIComponent(id) + "/progress");
      if (!box.isConnected) return;
      paintManager(box, id, el, data);
    } catch (err) {
      if (box.isConnected) box.textContent = plnErr(err);
    }
  }

  function managerNote(box) {
    let n = box.querySelector(":scope > .note.result");
    if (!n) {
      n = document.createElement("p");
      n.className = "note result";
      n.hidden = true;
      box.appendChild(n);
    }
    return n;
  }

  function paintManager(box, id, el, data) {
    const keepNote = box.querySelector(":scope > .note.result");
    const noteText = keepNote && !keepNote.hidden ? keepNote.textContent : "";
    const noteState = keepNote ? keepNote.dataset.state : "";
    const oldForm = box.querySelector(":scope > .add-step");
    const kept = oldForm ? {
      inputs: Array.from(oldForm.querySelectorAll("input")).map((i) => i.value),
      deps: Array.from(oldForm.querySelectorAll("select option:checked")).map((o) => o.value),
      focus: oldForm.contains(document.activeElement) ? Array.from(oldForm.children).indexOf(document.activeElement) : -1,
    } : null;
    box.replaceChildren();
    const steps = data.steps || [];
    const active = data.plan_status === "active";
    if (active) {
      const loop = document.createElement("p");
      loop.className = "note";
      loop.textContent = dx.t(data.loop_running ? "studio.stepLoopOn" : "studio.stepLoopOff");
      box.appendChild(loop);
    }
    if (!steps.length) {
      const none = document.createElement("p");
      none.className = "note";
      none.textContent = dx.t("studio.stepNone");
      box.appendChild(none);
    }
    const result = managerNote(box);
    const say = (text, bad) => { result.textContent = text; result.hidden = !text; result.dataset.state = bad ? "error" : "ok"; };
    steps.forEach((st) => {
      const row = document.createElement("div");
      row.className = "step";
      row.dataset.step = st.id;
      const b = document.createElement("b");
      b.textContent = st.step_order + ". " + st.title;
      const small = document.createElement("small");
      const bits = [st.status, dx.t("studio.stepCheckpoints", { n: st.checkpoints.passed_count, total: st.checkpoints.total })];
      if (st.manual_override) bits.push(dx.t("studio.stepOverridden"));
      if (st.bg_running) bits.push(dx.t("studio.stepRunning"));
      [st.progress_note, st.error_msg].forEach((x) => { if (x) bits.push(String(x)); });
      small.textContent = bits.join(" · ");
      const actions = document.createElement("div");
      actions.className = "row tight";
      const sel = document.createElement("select");
      sel.setAttribute("aria-label", dx.t("studio.stepStatus"));
      STEP_STATES.forEach((s2) => {
        const o = document.createElement("option");
        o.value = s2;
        o.textContent = s2;
        sel.appendChild(o);
      });
      if (STEP_STATES.indexOf(st.status) >= 0) sel.value = st.status;
      const set = xbtn(dx.t("studio.stepSet"), async (btn) => {
        const status = sel.value;
        const needs = status === "passed" || (status === "pending" && active);
        if (needs && !await ask("stepSet", dx.t(status === "passed" ? "studio.stepSetPassedConfirm" : "studio.stepSetPendingConfirm"))) return;
        extraRun(btn, "plan-status", async () => {
          await postJson("/api/v1/harness/review/planner/plans/" + encodeURIComponent(id) + "/steps/" + encodeURIComponent(st.id) + "/mark", { status, confirm: needs });
          await renderManager(id, el);
          const fresh = el.querySelector(":scope > .manage");
          if (fresh) { const n = managerNote(fresh); n.textContent = dx.t("studio.stepSetDone", { status }); n.hidden = false; n.dataset.state = "ok"; }
        });
      });
      set.dataset.role = "set";
      const over = xbtn(dx.t("studio.stepOverride"), async (btn) => {
        if (!await ask("stepOverride", dx.t("studio.stepOverrideConfirm"))) return;
        extraRun(btn, "plan-status", async () => {
          await postJson("/api/v1/harness/review/planner/plans/" + encodeURIComponent(id) + "/steps/" + encodeURIComponent(st.id) + "/override", { confirm: true });
          await renderManager(id, el);
        });
      });
      over.dataset.role = "override";
      over.disabled = st.status === "passed";
      if (over.disabled) over.dataset.keep = "1";            // stays off after an unlock: nothing to override
      const run = xbtn(dx.t("studio.stepRun"), (btn) => runStep(btn, id, st.id, el));
      run.dataset.role = "run";
      run.disabled = !!st.bg_running;
      if (run.disabled) run.dataset.keep = "1";              // stays off while this step runs
      actions.append(sel, set, over, run);
      row.append(b, small, actions);
      box.appendChild(row);
    });
    const form = addStepForm(id, el, steps, active);
    if (kept) {                                              // a refresh (the running-step poll) must not wipe what the owner is typing
      form.querySelectorAll("input").forEach((i, n) => { if (kept.inputs[n] !== undefined) i.value = kept.inputs[n]; });
      form.querySelectorAll("select option").forEach((o) => { if (kept.deps.indexOf(o.value) >= 0) o.selected = true; });
      if (kept.focus >= 0 && form.children[kept.focus]) form.children[kept.focus].focus();
    }
    box.appendChild(form);
    if (noteText) say(noteText, noteState === "error");
    clearTimeout(managerTimers[id]);
    if (steps.some((st) => st.bg_running)) {                // a step is running: look again, so the row shows how it ended
      managerTimers[id] = setTimeout(() => {
        if (currentPanel === "review" && box.isConnected) renderManager(id, el);
      }, 3000);
    }
  }

  function addStepForm(id, el, steps, active) {
    const form = document.createElement("div");
    form.className = "add-step";
    const title = document.createElement("input");
    title.type = "text";
    title.autocomplete = "off";
    title.placeholder = dx.t("studio.stepAddTitle");
    title.setAttribute("aria-label", dx.t("studio.stepAddTitle"));
    const desc = document.createElement("input");
    desc.type = "text";
    desc.autocomplete = "off";
    desc.placeholder = dx.t("studio.stepAddDesc");
    desc.setAttribute("aria-label", dx.t("studio.stepAddDesc"));
    const deps = document.createElement("select");
    deps.multiple = true;
    deps.setAttribute("aria-label", dx.t("studio.stepAddDeps"));
    steps.forEach((st) => {
      const o = document.createElement("option");
      o.value = st.id;
      o.textContent = st.step_order + ". " + st.title;
      deps.appendChild(o);
    });
    if (!steps.length) deps.hidden = true;
    const add = xbtn(dx.t("studio.stepAdd"), async (btn) => {
      if (active && !await ask("stepAdd", dx.t("studio.stepAddConfirm"))) return;
      extraRun(btn, "plan-status", async () => {
        await postJson("/api/v1/harness/review/planner/plans/" + encodeURIComponent(id) + "/steps", {
          title: title.value, description: desc.value, depends_on: Array.from(deps.selectedOptions).map((o) => o.value), confirm: active,
        });
        await renderManager(id, el);
        const fresh = el.querySelector(":scope > .manage");
        if (fresh) { const n = managerNote(fresh); n.textContent = dx.t("studio.stepAdded"); n.hidden = false; n.dataset.state = "ok"; }
      });
    });
    add.dataset.role = "add-step";
    form.append(title, desc, deps, add);
    return form;
  }

  async function runStep(btn, planId, stepId, el) {
    if (!await ask("stepRun", dx.t("studio.stepRunConfirm"))) return;
    let list = [];
    try {
      list = (await dx.api("/api/v1/harness/review/blockers")).recommended || [];
    } catch (err) { list = []; }
    // A second, separate question: running the step grants nothing unless the owner says yes to this.
    const grant = list.length > 0 && await ask("stepRunGrant", dx.t("studio.stepRunGrant", { list: list.join(", ") }));
    await extraRun(btn, "plan-status", async (say) => {
      await postJson("/api/v1/harness/review/planner/plans/" + encodeURIComponent(planId) + "/steps/" + encodeURIComponent(stepId) + "/run", { confirm: true, grant_recommended: grant });
      await renderManager(planId, el);
      const fresh = el.querySelector(":scope > .manage");
      if (fresh) { const n = managerNote(fresh); n.textContent = dx.t("studio.stepRunStarted"); n.hidden = false; n.dataset.state = "ok"; }
      say("", false);
    });
  }

  async function createManualPlan(btn) {
    const title = $("plan-manual-title");
    const desc = $("plan-manual-desc");
    await extraRun(btn, "plan-status", async (say) => {
      const r = await postJson("/api/v1/harness/review/planner/plans", { title: title.value, description: desc.value });
      title.value = "";
      desc.value = "";
      say(dx.t("studio.planManualCreated", { id: r.plan_id }), false);
      loadPlans({ keepStatus: true });
    });
  }

  function discoveryExtras(id, actions) {
    if (regenWatch.size) watchRegenerations();         // back on Review after leaving it: look at the server's state again
    if (!id) return;
    const regen = xbtn(dx.t("studio.discRegen"), async (b) => {
      if (!await ask("discRegen", dx.t("studio.discRegenConfirm"))) return;
      extraRun(b, "discovery-status", async (say) => {
        await postJson("/api/v1/harness/review/discoveries/" + encodeURIComponent(id) + "/regenerate", { confirm: true });
        regenWatch.add(id);
        say(dx.t("studio.discRegenStarted"), false);
        watchRegenerations();
      });
    });
    regen.dataset.role = "regenerate";
    const del = xbtn(dx.t("studio.discDelete"), async (b) => {
      if (!await askDanger("discDelete", dx.t("studio.discDeleteConfirm"))) return;
      extraRun(b, "discovery-status", async (say) => {
        await postJson("/api/v1/harness/review/discoveries/" + encodeURIComponent(id) + "/delete", { confirm: true });
        say(dx.t("studio.discDeleted"), false);
        loadDiscoveries({ keepStatus: true });
      });
    });
    del.dataset.role = "delete";
    actions.append(regen, del);
  }

  // Plan generation runs on the server in the background: look every few seconds until it has ended, then say how.
  function watchRegenerations() {
    clearTimeout(regenTimer);
    if (!regenWatch.size) return;
    regenTimer = setTimeout(async () => {
      if (currentPanel !== "review") return;           // not on screen: nothing is lost, the next visit reads the state again
      try {
        const st = await dx.api("/api/v1/harness/review/discoveries/regenerate-status");
        const running = new Set(st.running || []);
        regenWatch.forEach((did) => {
          if (running.has(did)) return;
          regenWatch.delete(did);
          const last = (st.last || {})[did];
          if (last && last.ok) {
            showStatus("discovery-status", dx.t("studio.discRegenDone", { id: last.plan_id }), false);
            loadPlans({ keepStatus: true });
          } else {
            showStatus("discovery-status", dx.t("studio.discRegenFailed", { why: (last && last.error) || "?" }), true);
          }
        });
      } catch (err) { /* try again below */ }
      watchRegenerations();
    }, 2500);
  }

  // ---- A6.2c: Review → Memory --------------------------------------------------------------------------------
  // What Daedalus remembers and keeps track of. Reads say "unavailable" when they cannot be read (never zeros); a delete or
  // reset asks first; consolidate and the style lesson are confirmed model turns that run on the server and report how they ended.
  const MEM_ERR = { confirm_required: "studio.memErrConfirm", not_found: "studio.memErrGone", unavailable: "studio.memErrUnavailable",
    already_running: "studio.memErrRunning", search_failed: "studio.memErrSearch", query_required: "studio.memErrQuery",
    title_required: "studio.plnErrTitle", too_long: "studio.plnErrLong", bad_mode: "studio.memErrMode" };
  const MEM = "/api/v1/harness/memory";
  let memGen = 0;
  let memFlight = false;
  let memTimer = null;

  function memErr(err) {
    const key = MEM_ERR[String((err && err.message) || "")];
    return key ? dx.t(key) : failText(err);
  }

  function memOn() {
    return currentPanel === "review" && currentTab === "memory";
  }

  function mbtn(label, role, handler) {
    const b = document.createElement("button");
    b.type = "button";
    b.dataset.mem = role;
    b.textContent = label;
    b.addEventListener("click", () => handler(b));
    return b;
  }

  function mnote(text, cls) {
    const p = document.createElement("p");
    p.className = cls || "note";
    p.textContent = text;
    return p;
  }

  function msection(list, key, role) {
    const box = document.createElement("div");
    box.className = "mem-section";
    box.dataset.section = role;
    const k = document.createElement("p");
    k.className = "kicker";
    k.textContent = dx.t(key);
    box.appendChild(k);
    list.appendChild(box);
    return box;
  }

  // One memory action at a time. Its outcome goes to the Review status line, and only if the owner is still on Memory.
  async function memRun(btn, work) {
    if (memFlight) return;
    const gen = memGen;
    memFlight = true;
    document.querySelectorAll("[data-mem]").forEach((b) => { b.disabled = true; });
    dx.busy(btn, true, "…");
    const say = (text, bad) => { if (memOn() && gen === memGen) showStatus("review-status", text, bad); };
    try {
      await work(say);
    } catch (err) {
      say(memErr(err), true);
    } finally {
      dx.busy(btn, false);
      memFlight = false;
      document.querySelectorAll("[data-mem]").forEach((b) => { b.disabled = b.dataset.keep === "1"; });
    }
  }

  async function loadMemory(list) {
    const gen = ++memGen;
    // Each part is read on its own: one that fails says so in its own place, and the others still show.
    const settled = await Promise.allSettled([
      dx.api(MEM), dx.api(MEM + "/episodic?limit=50"), dx.api(MEM + "/tasks?limit=50"), dx.api(MEM + "/sessions?limit=30"),
    ]);
    if (gen !== memGen || !memOn()) return;
    if (settled.every((r) => r.status === "rejected")) throw settled[0].reason;
    const part = (i) => (settled[i].status === "fulfilled" ? settled[i].value : null);
    list.replaceChildren();
    [["studio.memOverview", "overview", paintMemOverview], ["studio.memFacts", "facts", paintMemFacts],
      ["studio.memTasks", "tasks", paintMemTasks], ["studio.memSessions", "sessions", paintMemSessions]].forEach(([key, role, paint], i) => {
      const data = part(i);
      if (data) paint(list, data);
      else msection(list, key, role).appendChild(mnote(dx.t("studio.memPartFailed", { why: memErr(settled[i].reason) }), "note mem-failed"));
    });
    paintMemSearch(list);
    paintMemLearning(list);
    watchMemJobs(part(0) && part(0).jobs);
  }

  // A refresh after an action: repaint in place and keep the action's message on the status line.
  function reloadMemory() {
    if (!memOn()) return;
    loadMemory($("review-list")).catch((err) => { if (memOn()) showStatus("review-status", memErr(err), true); });
  }

  function jobLine(name, last) {
    if (!last) return "";
    if (!last.ok) return dx.t("studio.memJobFailed", { why: MEM_ERR[last.error] ? dx.t(MEM_ERR[last.error]) : (last.error || "?") });
    if (name === "lesson") return last.nothing_to_learn ? dx.t("studio.memLessonNothing") : dx.t("studio.memLessonDone", { n: last.processed || 0 });
    return dx.t("studio.memConsolidateDone", { scanned: last.scanned || 0, merged: last.merged || 0, promoted: last.promoted || 0 });
  }

  function paintMemOverview(list, d) {
    const box = msection(list, "studio.memOverview", "overview");
    const m = d.memory || {};
    if (!m.available) box.appendChild(mnote(dx.t("studio.memUnavailable")));
    else {
      const c = m.counts || {};
      box.appendChild(mnote(dx.t("studio.memCounts", { lessons: c.lessons, episodes: c.episodes, sessions: c.sessions, discoveries: c.discoveries,
        facts: c.episodic_facts === null ? "—" : c.episodic_facts })));
    }
    // mode
    const modeRow = document.createElement("div");
    modeRow.className = "row tight";
    const sel = document.createElement("select");
    sel.setAttribute("aria-label", dx.t("studio.memMode"));
    ((d.mode && d.mode.valid) || []).forEach((v) => {
      const o = document.createElement("option");
      o.value = v;
      o.textContent = dx.t("studio.memMode_" + v);
      sel.appendChild(o);
    });
    if (d.mode) sel.value = d.mode.mode;
    const label = document.createElement("span");
    label.textContent = dx.t("studio.memMode");
    modeRow.append(label, sel, mbtn(dx.t("studio.memModeSave"), "mode", (b) => memRun(b, async (say) => {
      const res = await postJson(MEM + "/mode", { mode: sel.value });
      say(dx.t("studio.memModeSaved", { mode: dx.t("studio.memMode_" + res.mode) }), false);
    })));
    box.appendChild(modeRow);
    // progress
    const p = d.progress || {};
    const pRow = document.createElement("div");
    pRow.className = "row tight";
    pRow.appendChild(mnote(p.active ? dx.t("studio.memProgressOn", { task: p.task, pct: Math.round((p.pct || 0) * 100), msg: p.message }) : dx.t("studio.memProgressOff")));
    pRow.appendChild(mbtn(dx.t("studio.memProgressClear"), "progress-clear", (b) => memRun(b, async (say) => {
      await postJson(MEM + "/progress/clear", {});
      say(dx.t("studio.memProgressCleared"), false);
      reloadMemory();
    })));
    box.appendChild(pRow);
    // navigator
    const n = d.navigator || {};
    const nRow = document.createElement("div");
    nRow.className = "row tight";
    nRow.appendChild(mnote(dx.t("studio.memNavigator", { calls: n.total_calls || 0, fast: n.fast_path_pct || 0, ms: n.avg_latency_ms || 0,
      state: dx.t(n.enabled ? "studio.memOn" : "studio.memOff") })));
    nRow.appendChild(mbtn(dx.t("studio.memNavigatorReset"), "navigator-reset", async (b) => {
      if (!await askDanger("memNavReset", dx.t("studio.memNavigatorResetConfirm"))) return;
      memRun(b, async (say) => {
        await postJson(MEM + "/navigator/reset", { confirm: true });
        say(dx.t("studio.memNavigatorResetDone"), false);
        reloadMemory();
      });
    }));
    box.appendChild(nRow);
    // clarifications + style lesson, consolidation
    const cl = d.clarifications || {};
    box.appendChild(mnote(cl.available ? dx.t("studio.memClarifications", { total: cl.total, pending: cl.pending_lessons, week: cl.last_7d })
      : dx.t("studio.memClarificationsUnavailable")));
    const jobs = d.jobs || { running: [], last: {} };
    const jRow = document.createElement("div");
    jRow.className = "row tight";
    [["consolidate", "studio.memConsolidate", "studio.memConsolidateConfirm"], ["lesson", "studio.memLesson", "studio.memLessonConfirm"]].forEach(([name, key, askKey]) => {
      const running = (jobs.running || []).indexOf(name) >= 0;
      const b = mbtn(dx.t(running ? "studio.memJobRunning" : key), "job-" + name, async (btn) => {
        if (!await ask("memJob", dx.t(askKey))) return;
        memRun(btn, async (say) => {
          await postJson(MEM + "/jobs/" + name, { confirm: true });
          say(dx.t("studio.memJobStarted"), false);
          reloadMemory();
        });
      });
      if (running) { b.disabled = true; b.dataset.keep = "1"; }            // stays off until the job has ended
      jRow.appendChild(b);
    });
    box.appendChild(jRow);
    ["consolidate", "lesson"].forEach((name) => {
      const line = jobLine(name, (jobs.last || {})[name]);
      if (line) {
        const el = mnote(line, "note mem-job");
        el.dataset.job = name;
        el.dataset.state = (jobs.last[name] || {}).ok ? "ok" : "error";
        box.appendChild(el);
      }
    });
    const runs = d.consolidations || [];
    if (runs.length) {
      const r = runs[0];
      box.appendChild(mnote(dx.t("studio.memLastRun", { when: r.finished_at || "?", merged: r.merged || 0, promoted: r.promoted || 0, errors: r.errors || 0 })));
    }
  }

  function paintMemFacts(list, data) {
    const box = msection(list, "studio.memFacts", "facts");
    if (!data.available) { box.appendChild(mnote(dx.t("studio.memFactsUnavailable"))); return; }
    if (!(data.facts || []).length) { emptyNote(box, "studio.emptyFacts"); return; }
    if (data.total > data.shown) box.appendChild(mnote(dx.t("studio.memFactsShown", { shown: data.shown, total: data.total })));
    data.facts.forEach((f) => {
      const del = mbtn(dx.t("studio.memDelete"), "fact-delete", async (b) => {
        if (!await askDanger("memDelete", dx.t("studio.memDeleteConfirm"))) return;
        memRun(b, async (say) => {
          await postJson(MEM + "/episodic/" + encodeURIComponent(f.id) + "/delete", { confirm: true });
          say(dx.t("studio.memDeleted"), false);
          reloadMemory();
        });
      });
      const item = row(f.content, [f.kind, dx.t("studio.memFactAge", { h: f.age_hours }), dx.t("studio.memFactUsed", { n: f.access_count })]
        .concat(f.promoted ? [dx.t("studio.memFactPromoted")] : []).join(" · "), [del]);
      item.dataset.fact = f.id;
      box.appendChild(item);
    });
  }

  function paintMemTasks(list, data) {
    const box = msection(list, "studio.memTasks", "tasks");
    (data.tasks || []).forEach((t) => box.appendChild(row(t.title, [t.status, t.summary].filter(Boolean).join(" · "), [])));
    if (!(data.tasks || []).length) emptyNote(box, "studio.emptyTasks");
    const form = document.createElement("div");
    form.className = "add-step";
    const title = document.createElement("input");
    title.type = "text";
    title.autocomplete = "off";
    title.placeholder = dx.t("studio.memTaskTitle");
    title.setAttribute("aria-label", dx.t("studio.memTaskTitle"));
    const summary = document.createElement("input");
    summary.type = "text";
    summary.autocomplete = "off";
    summary.placeholder = dx.t("studio.memTaskSummary");
    summary.setAttribute("aria-label", dx.t("studio.memTaskSummary"));
    form.append(title, summary, mbtn(dx.t("studio.memTaskAdd"), "task-add", (b) => memRun(b, async (say) => {
      await postJson(MEM + "/tasks", { title: title.value, summary: summary.value });
      say(dx.t("studio.memTaskAdded"), false);
      reloadMemory();
    })));
    box.appendChild(form);
  }

  function paintMemSessions(list, data) {
    const box = msection(list, "studio.memSessions", "sessions");
    if (!data.available) { box.appendChild(mnote(dx.t("studio.memSessionsUnavailable"))); return; }
    if (!(data.sessions || []).length) { emptyNote(box, "studio.emptySessions"); return; }
    data.sessions.forEach((s) => {
      const open = mbtn(dx.t("studio.memSessionOpen"), "session-open", (b) => {
        const shown = item.querySelector(".mem-turns");
        if (shown) { shown.remove(); b.textContent = dx.t("studio.memSessionOpen"); return; }
        memRun(b, async () => {
          const res = await dx.api(MEM + "/sessions/" + encodeURIComponent(s.session_id) + "?limit=100");
          if (!item.isConnected) return;
          const turns = document.createElement("div");
          turns.className = "mem-turns";
          res.turns.forEach((t) => {
            const p = document.createElement("p");
            const who = document.createElement("b");
            who.textContent = t.role + ": ";
            p.append(who, document.createTextNode(t.content));
            turns.appendChild(p);
          });
          item.appendChild(turns);
        }).then(() => { if (item.querySelector(".mem-turns")) b.textContent = dx.t("studio.memSessionHide"); });   // after busy() put the old label back
      });
      const item = row(s.preview || s.session_id, dx.t("studio.memSessionTurns", { n: s.turn_count, id: s.session_id }), [open]);
      item.dataset.session = s.session_id;
      box.appendChild(item);
    });
  }

  function paintMemSearch(list) {
    const box = msection(list, "studio.memSearch", "search");
    box.appendChild(mnote(dx.t("studio.memSearchHint"), "hint"));
    const form = document.createElement("div");
    form.className = "add-step";
    const q = document.createElement("input");
    q.type = "text";
    q.autocomplete = "off";
    q.placeholder = dx.t("studio.memSearchQuery");
    q.setAttribute("aria-label", dx.t("studio.memSearchQuery"));
    const results = document.createElement("div");
    results.className = "mem-results";
    const go = (kind) => (b) => memRun(b, async (say) => {
      const res = await postJson(MEM + "/search/" + kind, { query: q.value, limit: 5 });
      results.replaceChildren();
      (res.results || []).forEach((r) => {
        const item = document.createElement("div");
        item.className = "item";
        const a = document.createElement("a");
        a.textContent = r.title || r.url;
        if (/^https?:\/\//i.test(r.url)) { a.href = r.url; a.target = "_blank"; a.rel = "noopener noreferrer"; }
        const s = document.createElement("small");
        s.textContent = r.snippet || "";
        item.append(a, s);
        results.appendChild(item);
      });
      say(dx.t("studio.memSearchDone", { n: (res.results || []).length, saved: res.discoveries_recorded || 0 }), false);
    });
    form.append(q, mbtn(dx.t("studio.memSearchWeb"), "search-web", go("web")), mbtn(dx.t("studio.memSearchGithub"), "search-github", go("github")));
    box.append(form, results);
  }

  // What past builds recorded (the Playground page's learning stats). Read only on request.
  function paintMemLearning(list) {
    const box = msection(list, "studio.memLearning", "learning");
    box.appendChild(mnote(dx.t("studio.memLearningHint"), "hint"));
    const results = document.createElement("div");
    results.className = "mem-results";
    box.append(mbtn(dx.t("studio.memLearningShow"), "learning", (b) => memRun(b, async (say) => {
      const stats = await dx.api("/admin/daedalus/playground/api/learning/stats");
      if (!stats || stats.ok !== true) throw new Error((stats && stats.error) || "?");     // a failed read is not "no builds"
      let recent = null;
      try { recent = await dx.api("/admin/daedalus/playground/api/learning/recent?limit=20"); } catch (err) { recent = { ok: false, error: failText(err) }; }
      results.replaceChildren();
      if (!stats.total) results.appendChild(mnote(dx.t("studio.memLearningNone")));
      else results.appendChild(mnote(dx.t("studio.memLearningStats", { total: stats.total, rate: stats.success_rate, attempts: stats.avg_attempts })));
      const models = (stats.top_models || []).map((m) => (m.model || "?") + " " + (m.success_rate || 0) + "% / " + (m.total || 0));
      if (models.length) results.appendChild(mnote(dx.t("studio.memLearningModels") + ": " + models.join(" · ")));
      const errors = (stats.common_errors || []).map((e) => (e.error || e.pattern || "?") + " ×" + (e.count || 0));
      if (errors.length) results.appendChild(mnote(dx.t("studio.memLearningErrors") + ": " + errors.join(" · ")));
      if (!recent || recent.ok === false) results.appendChild(mnote(dx.t("studio.memLearningRecentFailed", { why: (recent && recent.error) || "?" }), "note mem-failed"));
      else if ((recent.records || []).length) {
        results.appendChild(mnote(dx.t("studio.memLearningRecent"), "kicker"));
        recent.records.forEach((r) => {
          results.appendChild(mnote((r.slug || "?") + " · " + (r.kind || "?") + " · " + (r.build_model || "?") + " · "
            + dx.t(r.success ? "studio.memLearningOk" : "studio.memLearningFail") + " · " + Math.round(r.duration_sec || 0) + "s"));
        });
      }
      say(dx.t("studio.memLearningDone", { total: stats.total || 0 }), false);
    })), results);
  }

  // Consolidate and the style lesson run on the server: look every few seconds while one runs, then show how it ended.
  function watchMemJobs(jobs) {
    clearTimeout(memTimer);
    if (!jobs || !(jobs.running || []).length) return;
    const gen = memGen;
    memTimer = setTimeout(async () => {
      if (!memOn() || gen !== memGen) return;          // left Memory: the next visit reads the state again
      try {
        const st = await dx.api(MEM + "/jobs");
        if (!memOn() || gen !== memGen) return;
        if ((st.running || []).length) { watchMemJobs(st); return; }
        if (!memFlight) reloadMemory();
        else watchMemJobs(jobs);
      } catch (err) { watchMemJobs(jobs); }
    }, 2500);
  }

  // ---- A6.3a: Build run controls and Full Auto M0→M8 ------------------------------------------------------------
  // Pause/resume the autopilot, continue a half-built plan, and look after the saved plan. Full Auto runs on the
  // server: the tab can close and it keeps going; it stops on its own when a milestone does not verify.
  const CTL_ERR = { confirm_required: "studio.ctlErrConfirm", build_running: "studio.ctlErrBusy", not_running: "studio.ctlErrNotRunning",
    not_paused: "studio.ctlErrNotPaused", no_plan: "studio.ctlErrNoPlan", plan_failed: "studio.ctlErrPlanFailed",
    description_required: "studio.needIdea", already_running: "studio.ctlErrRunning", not_game_studio: "studio.ctlErrNotStudio",
    too_long: "studio.plnErrLong" };
  const FA_REASON = { no_plan: "studio.faNoPlan", gates_not_passed: "studio.faGates", verify_failed: "studio.faVerify",
    step_limit: "studio.faLimit", cancelled: "studio.faCancelled", studio_refused: "studio.faRefused", error: "studio.faError",
    guided_error: "studio.faGuidedError", guided_interrupted: "studio.faGuidedStopped", guided_lost: "studio.faGuidedLost",
    guided_stuck: "studio.faGuidedStuck" };
  const PLAN_CREATE_TIMEOUT_MS = 240000;          // a 4000-token planning turn can take minutes
  const FA_TAIL = 12;                              // Full Auto events shown (the poll asks only for these)
  let ctlGen = 0;
  let ctlPainted = "";                             // slug + the last answer painted (see loadControls)
  let ctlFlight = false;
  let faTimer = null;
  let ctlUltra = false;                            // the Ultra box survives the repaint while Full Auto is watched
  let faWasLive = "";                              // slug whose Full Auto was live at the last look

  function ctlPath(slug, rest) {
    return "/api/v1/harness/build/" + slugPath(slug) + rest;
  }

  function ctlErr(err) {
    const key = CTL_ERR[String((err && err.message) || "")];
    return key ? dx.t(key) : failText(err);
  }

  function ctlOn(slug) {
    return currentPanel === "build" && dx.getSlug() === slug;
  }

  function cbtn(label, role, handler) {
    const b = document.createElement("button");
    b.type = "button";
    b.dataset.ctl = role;
    b.textContent = label;
    b.addEventListener("click", () => handler(b));
    return b;
  }

  function crow(text, buttons) {
    const row = document.createElement("div");
    row.className = "row tight";
    const p = document.createElement("p");
    p.className = "note";
    p.textContent = text;
    row.appendChild(p);
    buttons.forEach((b) => row.appendChild(b));
    return row;
  }

  // One control at a time; the outcome goes to the controls' status line while the owner is on this project's Build panel.
  async function ctlRun(btn, slug, work) {
    if (ctlFlight) return;
    ctlFlight = true;
    document.querySelectorAll("[data-ctl]").forEach((b) => { b.disabled = true; });
    dx.busy(btn, true, "…");
    const say = (text, bad) => { if (ctlOn(slug)) showStatus("build-ctl-status", text, bad); };
    try {
      await work(say);
    } catch (err) {
      say(ctlErr(err), true);
    } finally {
      dx.busy(btn, false);
      ctlFlight = false;
      document.querySelectorAll("[data-ctl]").forEach((b) => { b.disabled = b.dataset.keep === "1"; });
    }
  }

  async function loadControls(slug) {
    const box = $("build-controls");
    if (!box) return;
    const gen = ++ctlGen;
    if (!slug) { box.hidden = true; ctlPainted = ""; return; }
    try {
      const [st, fa] = await Promise.all([dx.api(ctlPath(slug, "/controls")), dx.api(ctlPath(slug, "/full-auto?tail=" + FA_TAIL))]);
      if (gen !== ctlGen || dx.getSlug() !== slug) return;
      // The same answer again (the panel often reads it twice on open) is not repainted: a repaint replaces every button,
      // so a click or a scroll that is under way would land on a node that has just been thrown away.
      const key = slug + "\n" + JSON.stringify([st, fa.run]);
      if (key === ctlPainted && !$("build-controls").hidden) { applyFullAutoLock(slug, st.busy || ""); return; }
      ctlPainted = key;
      paintControls(slug, st, fa.run);
    } catch (err) {
      if (gen !== ctlGen) return;
      box.hidden = false;
      ctlPainted = "";
      $("build-ctl-body").replaceChildren();
      showStatus("build-ctl-status", ctlErr(err), true);
    }
  }

  function paintControls(slug, st, run) {
    const box = $("build-controls");
    const body = $("build-ctl-body");
    box.hidden = false;
    body.replaceChildren();
    const busy = st.busy || "";
    const ap = st.autopilot || {};
    if (ap.running) {
      const btn = ap.paused
        ? cbtn(dx.t("studio.ctlResume"), "resume", (b) => ctlRun(b, slug, async (say) => {
            await postJson(ctlPath(slug, "/autopilot/resume"), {});
            say(dx.t("studio.ctlResumed"), false);
            loadControls(slug);
          }))
        : cbtn(dx.t("studio.ctlPause"), "pause", (b) => ctlRun(b, slug, async (say) => {
            await postJson(ctlPath(slug, "/autopilot/pause"), {});
            say(dx.t("studio.ctlPaused"), false);
            loadControls(slug);
          }));
      body.appendChild(crow(dx.t(ap.paused ? "studio.ctlApPaused" : "studio.ctlApRunning"), [btn]));
    } else if (st.resume) {
      const r = st.resume;
      const go = cbtn(dx.t("studio.ctlContinue"), "continue", (b) => startAutopilot(true, b));
      if (busy) { go.disabled = true; go.dataset.keep = "1"; }
      body.appendChild(crow(dx.t("studio.ctlResumeInfo", { title: r.plan_title || "—", done: r.done_stages, total: r.total_stages, next: r.next_stage || "—" }), [go]));
    }
    // the saved plan
    const plan = st.plan;
    const planText = plan ? dx.t("studio.ctlPlan", { title: plan.title || "—", done: plan.done, total: plan.stages }) + (plan.ultra ? " · Ultra" : "")
      : dx.t("studio.ctlNoPlan");
    const ultra = document.createElement("label");
    ultra.className = "check";
    const ultraBox = document.createElement("input");
    ultraBox.type = "checkbox";
    ultraBox.dataset.role = "ultra";
    ultraBox.checked = ctlUltra;
    ultraBox.addEventListener("change", () => { ctlUltra = ultraBox.checked; });
    const ultraText = document.createElement("span");
    ultraText.textContent = dx.t("studio.ctlUltra");
    ultra.append(ultraBox, ultraText);
    const create = cbtn(dx.t("studio.ctlCreatePlan"), "plan-create", async (b) => {
      const description = ($("build-idea").value || "").trim();
      if (!description) { showStatus("build-ctl-status", dx.t("studio.needIdea"), true); return; }
      const replace = !!plan;
      if (!await ask("ctlCreate", dx.t(replace ? "studio.ctlCreateReplaceConfirm" : "studio.ctlCreateConfirm"))) return;
      ctlRun(b, slug, async (say) => {
        let res;
        try {
          res = await dx.api(ctlPath(slug, "/plan/create"), { method: "POST", timeoutMs: PLAN_CREATE_TIMEOUT_MS,
            body: JSON.stringify({ description, ultra: ultraBox.checked, studio: !!st.game_studio, confirm: replace }) });
        } catch (err) {
          if (!(err && (err.name === "AbortError" || /aborted|timeout/i.test(String(err.message || ""))))) throw err;
          say(dx.t("studio.ctlPlanUnknown"), true);              // the server may still finish and save it: look again
          loadWorkspace(slug, { quiet: true, force: true });
          return;
        }
        say(dx.t("studio.ctlPlanMade", { n: res.plan.stages }), false);
        loadWorkspace(slug, { quiet: true, force: true });    // repaints the milestones and these controls
      });
    });
    const buttons = [create];
    if (plan) {
      buttons.push(cbtn(dx.t("studio.ctlExpand"), "plan-expand", async (b) => {
        if (!await ask("ctlExpand", dx.t("studio.ctlExpandConfirm"))) return;
        ctlRun(b, slug, async (say) => {
          const res = await postJson(ctlPath(slug, "/plan/expand"), { confirm: true });
          say(dx.t("studio.ctlExpanded", { before: res.before.stages, after: res.plan.stages }), false);
          loadControls(slug);
        });
      }));
      buttons.push(cbtn(dx.t("studio.ctlClear"), "plan-clear", async (b) => {
        if (!await askDanger("ctlClear", dx.t("studio.ctlClearConfirm"))) return;
        ctlRun(b, slug, async (say) => {
          await postJson(ctlPath(slug, "/plan/clear"), { confirm: true });
          say(dx.t("studio.ctlCleared"), false);
          loadControls(slug);
        });
      }));
    }
    if (busy) buttons.forEach((b) => { b.disabled = true; b.dataset.keep = "1"; });
    const planRow = crow(planText, [ultra].concat(buttons));
    body.appendChild(planRow);
    const OWNER = { guided: "studio.guided", autopilot: "studio.autopilot", full_auto: "studio.faName" };
    if (busy) body.appendChild(crow(dx.t("studio.ctlBusy", { owner: OWNER[busy] ? dx.t(OWNER[busy]) : busy }), []));
    if (st.game_studio) paintFullAuto(slug, body, run, busy);
    // Full Auto drives this project: the manual build steps would race it (the server refuses them too).
    applyFullAutoLock(slug, busy);
  }

  // Remember who owns the project and switch the manual steps off now; the workspace paint reads the same flag later.
  function applyFullAutoLock(slug, busy) {
    faOwnerSlug = busy === "full_auto" ? slug : (faOwnerSlug === slug ? "" : faOwnerSlug);
    if (busy === "full_auto") {
      ["build-guided", "build-autopilot", "build-verify", "build-advance", "build-waive-unity", "build-waive-proof"].forEach((id) => { $(id).disabled = true; });
    }
  }

  function faLine(ev) {
    if (ev.type === "milestone_build") return dx.t("studio.faBuild", { m: ev.milestone });
    if (ev.type === "verify") return dx.t("studio.faVerifying", { m: ev.milestone });
    if (ev.type === "verified") return dx.t(ev.ok ? "studio.faVerified" : "studio.faNotVerified", { m: ev.milestone });
    if (ev.type === "advanced") return dx.t("studio.faAdvanced", { from: ev.previous || "?", to: ev.current || "—" });
    if (ev.type === "stop_requested") return dx.t("studio.faStopping");
    if (ev.type === "ended") return faEnd(ev.state, ev.reason);
    return "";
  }

  function faEnd(state, reason) {
    if (state === "succeeded") return dx.t("studio.faDone");
    if (state === "stopped") return dx.t("studio.faStopped");
    const why = FA_REASON[reason] ? dx.t(FA_REASON[reason]) : (reason || "?");
    return dx.t(state === "blocked" ? "studio.faBlocked" : "studio.faFailed", { why });
  }

  function paintFullAuto(slug, body, run, busy) {
    const live = !!(run && (run.state === "running" || run.state === "stopping"));
    const box = document.createElement("div");
    box.className = "full-auto";
    box.dataset.state = run ? run.state : "none";
    const btn = live
      ? cbtn(dx.t("studio.faStop"), "fa-stop", (b) => ctlRun(b, slug, async (say) => {
          await postJson(ctlPath(slug, "/full-auto/stop"), {});
          say(dx.t("studio.faStopping"), false);
          loadControls(slug);
        }))
      : cbtn(dx.t("studio.faStart"), "fa-start", async (b) => {
          if (!await ask("fullAuto", dx.t("studio.faConfirm"))) return;
          ctlRun(b, slug, async (say) => {
            await postJson(ctlPath(slug, "/full-auto"), { confirm: true });
            say(dx.t("studio.faStarted"), false);
            loadControls(slug);
          });
        });
    if (!live && busy) { btn.disabled = true; btn.dataset.keep = "1"; }
    if (live && run.state === "stopping") { btn.disabled = true; btn.dataset.keep = "1"; }
    box.appendChild(crow(live ? dx.t("studio.faRunning", { m: run.milestone || "…" }) : dx.t("studio.faIdle"), [btn]));
    if (run) {
      const log = document.createElement("ol");
      log.className = "fa-log";
      run.events.slice(-12).forEach((ev) => {
        const text = faLine(ev);
        if (!text) return;
        const li = document.createElement("li");
        li.dataset.type = ev.type;
        if (ev.type === "ended") li.dataset.state = ev.state;
        li.textContent = text;
        log.appendChild(li);
      });
      box.appendChild(log);
    }
    body.appendChild(box);
    clearTimeout(faTimer);
    if (faWasLive === slug && !live) loadWorkspace(slug, { quiet: true });   // it ended: the milestones moved
    faWasLive = live ? slug : "";
    if (live) {                                          // the run is on the server: look again while it lives
      const look = async () => {
        if (!ctlOn(slug)) return;                        // left: the next visit reads it again
        if (ctlFlight) { faTimer = setTimeout(look, 2000); return; }
        try {
          const fa = await dx.api(ctlPath(slug, "/full-auto?tail=" + FA_TAIL));
          if (!ctlOn(slug)) return;
          const still = !!(fa.run && (fa.run.state === "running" || fa.run.state === "stopping"));
          if (!still) { loadControls(slug); return; }   // it ended: everything may have changed
          const old = $("build-ctl-body").querySelector(".full-auto");
          if (old) old.remove();
          paintFullAuto(slug, $("build-ctl-body"), fa.run, "full_auto");
        } catch (err) { faTimer = setTimeout(look, 4000); }
      };
      faTimer = setTimeout(look, 2000);
    }
  }

  // ---- A6.3c: git browse, per-app data, skill gist ------------------------------------------------------------
  // Reads (git status, repos, branches, projects, compat) only fill the list and the Remote URL field. The app data
  // editor writes only to a PUBLISHED app's store; a delete or clear asks first. A gist is public, so it asks first.
  const PG_API = "/admin/daedalus/playground/api";
  let gitBrowseGen = 0;
  let appDataGen = 0;
  let appDataFlight = false;

  function gbBtn(label, role, handler) {
    const b = document.createElement("button");
    b.type = "button";
    b.dataset.gb = role;
    b.textContent = label;
    b.addEventListener("click", () => handler(b));
    return b;
  }

  function useRemote(url) {
    $("git-url").value = url || "";
    showStatus("git-browse-status", dx.t("studio.gitUsed", { url: url || "" }), false);
  }

  async function gitBrowse(btn, work) {
    const gen = ++gitBrowseGen;
    const list = $("git-browse-list");
    dx.busy(btn, true, "…");
    try {
      const out = await work();
      if (gen !== gitBrowseGen) return;
      list.replaceChildren(...out.rows);
      if (!out.rows.length) emptyNote(list);
      showStatus("git-browse-status", out.note || "", !!out.bad);
    } catch (err) {
      if (gen !== gitBrowseGen) return;
      list.replaceChildren();
      showStatus("git-browse-status", gitBrowseErr(err), true);
    } finally {
      dx.busy(btn, false);
    }
  }

  function gitBrowseErr(err) {
    const msg = String((err && err.message) || "");
    if (/GITHUB_TOKEN not configured/.test(msg)) return dx.t("studio.gitNoGithub");
    if (/GITLAB_TOKEN not configured/.test(msg)) return dx.t("studio.gitNoGitlab");
    return failText(err);
  }

  function repoRows(repos, provider) {
    return repos.map((r) => {
      const buttons = [gbBtn(dx.t("studio.gitUse"), "use", () => useRemote(r.clone_url || r.url))];
      if (provider === "github" && /^[^/\s]+\/[^/\s]+$/.test(r.full_name || "")) {
        buttons.unshift(gbBtn(dx.t("studio.gitBranches"), "branches", (b) => showBranches(b, r)));
      }
      const bits = [r.private ? dx.t("studio.gitPrivate") : dx.t("studio.gitPublic"), r.language, r.default_branch];
      if (r.compat && r.compat.level) bits.push(dx.t("studio.gitCompatLevel", { level: r.compat.level, score: r.compat.score }));
      const item = row(r.full_name || r.url, bits.filter(Boolean).join(" · "), buttons);
      item.dataset.repo = r.full_name || "";
      return item;
    });
  }

  function showBranches(btn, repo) {
    const [owner, name] = repo.full_name.split("/");
    gitBrowse(btn, async () => {
      const data = await dx.api(PG_API + "/github/repos/" + encodeURIComponent(owner) + "/" + encodeURIComponent(name) + "/branches");
      const rows = (data.branches || []).map((br) => {
        const nameText = typeof br === "string" ? br : String(br.name || "");
        return row(nameText, nameText === data.default_branch ? dx.t("studio.gitDefault") : "", []);
      });
      const tags = (data.tags || []).map((t) => (typeof t === "string" ? t : String(t.name || ""))).filter(Boolean);
      return { rows, note: dx.t("studio.gitBranchesOf", { repo: repo.full_name, n: rows.length, tags: tags.join(", ") || "—" }) };
    });
  }

  $("git-connections").addEventListener("click", (ev) => gitBrowse(ev.currentTarget, async () => {
    const st = await dx.api(PG_API + "/git/status", { timeoutMs: 30000 });
    const line = (name, part) => {
      const p = part || {};
      if (!p.configured) return row(name, dx.t("studio.gitNotConfigured"), []);
      if (p.ok) return row(name, dx.t("studio.gitConnected", { user: p.login || p.username || "?" }), []);
      // the server's own error text is not UI copy (it is not in the owner's language): say its code instead
      return row(name, dx.t("studio.gitBroken", { why: tOr(GIT_CODE_KEYS[p.code], dx.t("studio.errUnknown")) }), []);
    };
    return { rows: [line("GitHub", st.github), line("GitLab", st.gitlab)], note: "" };
  }));
  $("git-gh-repos").addEventListener("click", (ev) => gitBrowse(ev.currentTarget, async () => {
    const data = await dx.api(PG_API + "/github/repos?limit=30", { timeoutMs: 30000 });
    return { rows: repoRows(data.repos || [], "github"), note: dx.t("studio.gitRepoCount", { n: (data.repos || []).length }) };
  }));
  $("git-gl-projects").addEventListener("click", (ev) => gitBrowse(ev.currentTarget, async () => {
    const data = await dx.api(PG_API + "/gitlab/projects?limit=30", { timeoutMs: 30000 });
    return { rows: repoRows(data.projects || [], "gitlab"), note: dx.t("studio.gitRepoCount", { n: (data.projects || []).length }) };
  }));
  $("git-compat").addEventListener("click", (ev) => {
    const url = ($("git-url").value || "").trim();
    if (!url) { showStatus("git-browse-status", dx.t("studio.gitNeedUrl"), true); return; }
    gitBrowse(ev.currentTarget, async () => {
      const data = await postJson(PG_API + "/github/compat-check", { url });
      const c = data.compat || {};
      const rows = (c.warnings || []).map((w) => row(String(w), "", []));
      return { rows, note: dx.t("studio.gitCompatResult", { level: c.level || "?", score: c.score == null ? "?" : c.score, kind: c.kind || "?" }) };
    });
  });

  // -- A7: requests from Daedalus (the brain asks to publish or push; only the owner runs them) --
  const SHIP_BUTTON = { local: "preview-local", live: "preview-live", push: "git-push" };
  let shipReqGen = 0;

  // keepStatus: a reload after an action's message keeps that message on the line.
  async function loadShipRequests(keepStatus) {
    const slug = dx.getSlug();
    const list = $("ship-requests-list");
    const gen = ++shipReqGen;
    if (!slug) { list.replaceChildren(); showStatus("ship-requests-status", dx.t("studio.noProject"), true); return; }
    let res;
    try {
      res = await dx.api("/api/v1/harness/ship-requests?status=pending&slug=" + encodeURIComponent(slug));
    } catch (err) {
      if (gen === shipReqGen) showStatus("ship-requests-status", dx.t("studio.requestFailed") + ": " + failText(err), true);
      return;
    }
    if (gen !== shipReqGen || dx.getSlug() !== slug) return;
    list.replaceChildren();
    const rows = (res && res.requests) || [];
    if (!rows.length) { if (!keepStatus) showStatus("ship-requests-status", dx.t("studio.shipReqNone"), false); return; }
    rows.forEach((req) => {
      const kindLabel = dx.t("studio.shipReqKind." + req.kind);
      const row = document.createElement("div");
      row.className = "item";
      row.dataset.id = req.id;
      const head = document.createElement("span");
      head.className = "name";
      head.textContent = kindLabel;
      const note = document.createElement("small");
      note.textContent = " " + (req.note || "");
      const runBtn = document.createElement("button");
      runBtn.type = "button";
      runBtn.dataset.act = "run";
      runBtn.textContent = dx.t("studio.shipReqRun");
      const noBtn = document.createElement("button");
      noBtn.type = "button";
      noBtn.dataset.act = "decline";
      noBtn.textContent = dx.t("studio.shipReqDecline");
      const answer = async (outcome) => {
        runBtn.disabled = true;
        noBtn.disabled = true;
        try {
          await postJson("/api/v1/harness/ship-requests/" + encodeURIComponent(req.id) + "/resolve", { outcome });
        } catch (err) {
          const gone = /already_resolved|not_found/.test(String((err && err.message) || ""));
          showStatus("ship-requests-status", gone ? dx.t("studio.shipReqGone") : dx.t("studio.requestFailed") + ": " + failText(err), true);
          loadShipRequests(true);
          return false;
        }
        row.remove();
        return true;
      };
      runBtn.addEventListener("click", async () => {
        const target = $(SHIP_BUTTON[req.kind]);
        // Approve only what can run now: a missing or disabled button would leave an approval with nothing behind it.
        if (!target || target.disabled || req.slug !== dx.getSlug()) {
          showStatus("ship-requests-status", dx.t("studio.shipReqCannot", { kind: kindLabel }), true);
          return;
        }
        if (!await ask("shipReqRun", dx.t("studio.shipReqRunConfirm", { kind: kindLabel, slug: req.slug }))) return;
        if (!(await answer("approved"))) return;
        showStatus("ship-requests-status", dx.t("studio.shipReqStarted", { kind: kindLabel }), false);
        if (req.kind === "push") { const menu = $("git-menu"); if (menu) menu.open = true; }
        target.click();                              // the Studio's own action, with its own questions
      });
      noBtn.addEventListener("click", async () => {
        if (await answer("declined")) showStatus("ship-requests-status", dx.t("studio.shipReqDeclined"), false);
      });
      row.append(head, note, runBtn, noBtn);
      list.appendChild(row);
    });
    if (!keepStatus) showStatus("ship-requests-status", dx.t("studio.shipReqCount", { n: rows.length }), false);
  }

  $("ship-req-refresh").addEventListener("click", () => loadShipRequests());
  let shipReqShown = "";
  dx.watchSlug((snap) => {
    const next = snap.slug || "";
    if (next === shipReqShown) return;
    shipReqShown = next;
    shipReqGen += 1;
    $("ship-requests-list").replaceChildren();
    showStatus("ship-requests-status", "", false);
    if (currentPanel === "preview" && next) loadShipRequests();      // another project: its own requests
  });

  // -- A6.5c: what only the Playground page had (ideas, hints, description, coding mode, published apps, backup, tunnel) --
  const PGX = "/admin/daedalus/playground/api";
  let extrasGen = 0;
  let extrasFlight = false;
  let codingDirty = false;
  const codingSaved = {};          // slug -> mode saved in this page (the project list may still hold the old one)

  function projectOf(slug) {
    return (dx.projects() || []).filter((p) => p.slug === slug)[0] || null;
  }

  function uiLang() {
    return window.DaedalusI18n && window.DaedalusI18n.lang() === "bg" ? "bg" : "en";
  }

  function xline(text, cls) {
    const p = document.createElement("p");
    p.className = cls || "note";
    p.textContent = text;
    return p;
  }

  // One action at a time per box; a result that arrives after the project changed is dropped.
  async function extrasRun(boxId, btn, work) {
    const statusId = boxId + "-status";
    if (extrasFlight) return;
    const slug = dx.getSlug();
    if (!slug) { showStatus(statusId, dx.t("studio.noProject"), true); return; }
    const gen = ++extrasGen;
    extrasFlight = true;
    const controls = Array.from(document.querySelectorAll("#" + boxId + " button, #" + boxId + " select"));
    controls.forEach((c) => { c.disabled = true; });
    dx.busy(btn, true, "…");
    const live = () => gen === extrasGen && dx.getSlug() === slug;
    const say = (text, bad) => { if (live()) showStatus(statusId, text, bad); };
    try {
      await work(slug, say, live);
    } catch (err) {
      say(dx.t("studio.requestFailed") + ": " + failText(err), true);
    } finally {
      dx.busy(btn, false);
      extrasFlight = false;
      controls.forEach((c) => { if (c.isConnected) c.disabled = false; });
    }
  }

  function syncExtras(slug) {
    const project = slug ? projectOf(slug) : null;
    const select = $("build-coding-mode");
    if (!extrasFlight) select.disabled = !project;
    if (project && !codingDirty) select.value = codingSaved[slug] || project.coding_mode || "lite";
    $("preview-tunnel").hidden = !(project && project.kind === "expo");
  }

  let extrasShown = "";
  dx.watchSlug((snap) => {
    const next = snap.slug || "";
    if (next !== extrasShown) {                 // another project: what is on screen belongs to the old one
      extrasShown = next;
      extrasGen += 1;
      codingDirty = false;
      ["build-extras", "preview-extras"].forEach((id) => {
        $(id + "-out").replaceChildren();
        showStatus(id + "-status", "", false);
      });
    }
    syncExtras(next);
  });
  $("build-coding-mode").addEventListener("change", () => { codingDirty = true; });

  $("build-coding-save").addEventListener("click", (ev) => extrasRun("build-extras", ev.currentTarget, async (slug, say, live) => {
    const mode = $("build-coding-mode").value;
    const res = await postJson(PGX + "/projects/" + slugPath(slug) + "/set-mode", { mode });
    if (!res || res.ok !== true) throw new Error((res && res.error) || dx.t("studio.requestFailed"));
    codingSaved[slug] = res.coding_mode || mode;
    if (live()) codingDirty = false;
    say(dx.t("studio.codingSaved", { slug, mode: codingSaved[slug] }), false);
  }));

  $("build-ideas").addEventListener("click", async (ev) => {
    const btn = ev.currentTarget;
    const slug = dx.getSlug();
    if (slug && !await ask("ideas", dx.t("studio.ideasConfirm", { slug }))) return;
    extrasRun("build-extras", btn, async (slug2, say, live) => {
      const res = await postJson(PGX + "/projects/" + slugPath(slug2) + "/suggest", { lang: uiLang() });
      if (!live()) return;
      const ideas = (res && res.suggestions) || [];
      if (!res || res.ok !== true || !ideas.length) throw new Error((res && res.error) || dx.t("studio.requestFailed"));
      const out = $("build-extras-out");
      out.replaceChildren();
      ideas.forEach((idea) => {
        const row = document.createElement("div");
        row.className = "idea item";
        const span = document.createElement("span");
        span.textContent = String(idea);
        const use = document.createElement("button");
        use.type = "button";
        use.textContent = dx.t("studio.ideaUse");
        use.addEventListener("click", () => {            // the classic page sent an idea to project chat: same here
          showPanel("chat");
          const prompt = document.getElementById("prompt");
          if (prompt) { prompt.value = String(idea); prompt.focus(); }
        });
        row.append(span, use);
        out.appendChild(row);
      });
      // Generic ideas are not ideas about this project: say so instead of presenting them as the model's answer.
      if (res.fallback) say(dx.t("studio.ideasGeneric"), true);
      else say(dx.t("studio.ideasDone", { n: ideas.length }), false);
    });
  });

  $("build-hints").addEventListener("click", (ev) => extrasRun("build-extras", ev.currentTarget, async (slug, say, live) => {
    const project = projectOf(slug) || {};
    const q = new URLSearchParams({ kind: project.kind || "auto", name: project.name || slug, description: ($("build-idea").value || "").trim() });
    const res = await dx.api(PGX + "/game-hints?" + q.toString());
    if (!live()) return;
    if (!res || res.ok !== true) throw new Error((res && res.error) || dx.t("studio.requestFailed"));
    const out = $("build-extras-out");
    out.replaceChildren();
    if (res.ui_short) out.appendChild(xline(String(res.ui_short)));
    if (res.architecture) out.appendChild(xline(dx.t("studio.hintsArch") + ": " + res.architecture));
    if ((res.libraries || []).length) out.appendChild(xline(dx.t("studio.hintsLibs") + ": " + res.libraries.join(", ")));
    if ((res.staging || []).length) out.appendChild(xline(dx.t("studio.hintsStaging") + ": " + res.staging.join(" → ")));
    say(dx.t("studio.hintsDone", { kind: res.effective_kind || project.kind || "?" }), false);
  }));

  $("build-describe").addEventListener("click", async (ev) => {
    const btn = ev.currentTarget;
    const slug = dx.getSlug();
    if (slug && !await ask("describe", dx.t("studio.describeConfirm", { slug }))) return;
    extrasRun("build-extras", btn, async (slug2, say, live) => {
      const project = projectOf(slug2) || {};
      const res = await postJson(PGX + "/ai-gen-desc", {
        name: project.name || slug2, kind: project.kind || "static", criteria: ($("build-idea").value || "").trim(), lang: uiLang(),
      });
      if (!live()) return;
      const text = String((res && res.description) || "");
      if (!res || res.ok !== true || !text) throw new Error((res && res.error) || dx.t("studio.requestFailed"));
      const out = $("build-extras-out");
      out.replaceChildren();
      out.appendChild(xline(text));
      const use = document.createElement("button");
      use.type = "button";
      use.id = "build-describe-use";
      use.textContent = dx.t("studio.describeUse");
      use.addEventListener("click", async () => {              // the idea field is the owner's text: never replaced without a yes
        const idea = $("build-idea");
        if ((idea.value || "").trim() && !await ask("describeReplace", dx.t("studio.describeReplaceConfirm"))) return;
        idea.value = text;
      });
      out.appendChild(use);
      if (res.generated === false) say(dx.t("studio.describeTemplate"), true);
      else say(dx.t("studio.describeDone"), false);
    });
  });

  $("preview-published").addEventListener("click", (ev) => extrasRun("preview-extras", ev.currentTarget, async (slug, say, live) => {
    const res = await dx.api(PGX + "/published");
    if (!live()) return;
    const out = $("preview-extras-out");
    out.replaceChildren();
    if (!res || res.ok !== true) { say(dx.t("studio.publishedFailed", { why: (res && res.error) || "?" }), true); return; }
    const apps = res.apps || [];
    if (!apps.length) { say(dx.t("studio.publishedNone"), false); return; }
    apps.forEach((app) => {
      const row = document.createElement("div");
      row.className = "item";
      const name = document.createElement("span");
      name.className = "name";
      name.textContent = (app.icon ? app.icon + " " : "") + (app.name || app.slug || "");
      const sub = document.createElement("small");
      sub.textContent = " " + (app.slug || "") + " ";
      row.append(name, sub);
      if (app.enabled !== false && app.slug) {
        const a = document.createElement("a");
        a.href = "/apps/" + encodeURIComponent(app.slug) + "/";
        a.target = "_blank";
        a.rel = "noopener noreferrer";
        a.textContent = dx.t("studio.publishedOpen");
        row.appendChild(a);
      } else {
        const off = document.createElement("small");
        off.textContent = dx.t("studio.publishedOff");
        row.appendChild(off);
      }
      out.appendChild(row);
    });
    say(dx.t("studio.publishedDone", { n: apps.length }), false);
  }));

  $("preview-backup").addEventListener("click", async (ev) => {
    const btn = ev.currentTarget;
    const slug = dx.getSlug();
    if (slug && !await ask("backup", dx.t("studio.backupConfirm", { slug }))) return;
    extrasRun("preview-extras", btn, async (slug2, say) => {
      const res = await postJson(PGX + "/projects/" + slugPath(slug2) + "/backup-workspace", {});
      if (res && res.ok === true) say(dx.t("studio.backupDone", { kb: Math.round((res.size_bytes || 0) / 1024) }), false);
      else if (res && res.skipped) say(dx.t("studio.backupSkipped", { why: res.reason || "?" }), true);   // skipped is not done
      else throw new Error((res && (res.error || res.reason)) || dx.t("studio.requestFailed"));
    });
  });

  function paintTunnel(status) {
    const out = $("preview-extras-out");
    out.replaceChildren();
    out.appendChild(xline(String(status.expo_url), "note mono"));
    const qr = String(status.qr_url || "");
    if (!/^https:\/\/api\.qrserver\.com\//.test(qr)) return;      // only the known QR service, and only on request
    const show = document.createElement("button");
    show.type = "button";
    show.id = "preview-qr-show";
    show.textContent = dx.t("studio.qrShow");
    show.addEventListener("click", async () => {
      if (!await ask("qr", dx.t("studio.qrConfirm"))) return;
      const img = document.createElement("img");
      img.id = "preview-qr";
      img.alt = dx.t("studio.qrAlt");
      img.width = 220;
      img.height = 220;
      img.src = qr;
      show.replaceWith(img);
    });
    out.appendChild(show);
  }

  $("preview-tunnel").addEventListener("click", async (ev) => {
    const btn = ev.currentTarget;
    const slug = dx.getSlug();
    if (slug && !await ask("tunnel", dx.t("studio.tunnelConfirm", { slug }))) return;
    extrasRun("preview-extras", btn, async (slug2, say, live) => {
      const res = await postJson(PGX + "/projects/" + slugPath(slug2) + "/run", { install_deps: true, tunnel: true });
      if (!res || res.ok !== true) throw new Error((res && res.error) || dx.t("studio.requestFailed"));
      let status = res.run || {};
      say(dx.t("studio.tunnelWaiting"), false);
      for (let i = 0; i < 20 && live() && !status.expo_url; i += 1) {       // the tunnel address shows up in the logs
        if (i) await new Promise((done) => setTimeout(done, 3000));
        if (!live()) return;
        const logs = await dx.api(PGX + "/projects/" + slugPath(slug2) + "/logs");
        status = (logs && logs.status) || {};
      }
      if (!live()) return;
      if (!status.expo_url) { say(dx.t("studio.tunnelNoUrl"), true); return; }
      paintTunnel(status);
      say(dx.t("studio.tunnelReady"), false);
    });
  });

  // -- per-app data (PlaygroundDB of a published app) --
  function appDataSlug() {
    const slug = dx.getSlug();
    if (!slug) showStatus("app-data-status", dx.t("studio.noProject"), true);
    return slug;
  }

  function appDataPath(slug, key) {
    return "/apps/" + encodeURIComponent(slug) + "/db/" + (key == null ? "" : key.split("/").map(encodeURIComponent).join("/"));
  }

  function appDataErr(err) {
    const msg = String((err && err.message) || "");
    if (msg === "not a published app") return dx.t("studio.appDataNotPublished");
    if (msg === "not found") return dx.t("studio.appDataGone");
    if (/^value too large/.test(msg)) return dx.t("studio.appDataTooLarge");
    if (/^key limit/.test(msg)) return dx.t("studio.appDataLimit");
    return failText(err);
  }

  async function appDataRun(btn, work) {
    if (appDataFlight) return;
    const slug = appDataSlug();
    if (!slug) return;
    const gen = ++appDataGen;
    appDataFlight = true;
    document.querySelectorAll("#app-data button").forEach((b) => { b.disabled = true; });
    dx.busy(btn, true, "…");
    const say = (text, bad) => { if (gen === appDataGen && dx.getSlug() === slug) showStatus("app-data-status", text, bad); };
    try {
      await work(slug, say, gen);
    } catch (err) {
      say(appDataErr(err), true);
    } finally {
      dx.busy(btn, false);
      appDataFlight = false;
      document.querySelectorAll("#app-data button").forEach((b) => { b.disabled = false; });
    }
  }

  // Another project: what is on screen belongs to the old one, and every button would act on the new one. Clear it.
  let appDataShown = "";
  dx.watchSlug((snap) => {
    const next = snap.slug || "";
    if (next === appDataShown) return;
    const before = appDataShown;
    appDataShown = next;
    if (!before) return;                                   // the first project of the page: nothing of another one is shown
    appDataGen += 1;
    $("app-data-list").replaceChildren();
    $("app-data-key").value = "";
    $("app-data-value").value = "";
    showStatus("app-data-status", "", false);
  });

  async function loadAppKeys(slug, gen) {
    const data = await dx.api(appDataPath(slug, null));
    if (gen !== appDataGen || dx.getSlug() !== slug) return 0;
    const list = $("app-data-list");
    list.replaceChildren();
    const keys = data.keys || [];
    keys.forEach((key) => {
      const view = document.createElement("button");
      view.type = "button";
      view.textContent = dx.t("studio.appDataView");
      view.addEventListener("click", () => appDataRun(view, async (s, say) => {
        if (s !== slug) return;                            // listed for another project (the list is cleared on a switch)
        const res = await dx.api(appDataPath(s, key));
        $("app-data-key").value = key;
        $("app-data-value").value = JSON.stringify(res.value, null, 2);
        say(dx.t("studio.appDataShown", { key }), false);
      }));
      const del = document.createElement("button");
      del.type = "button";
      del.textContent = dx.t("studio.appDataDelete");
      del.addEventListener("click", async () => {
        if (!await askDanger("appDataDelete", dx.t("studio.appDataDeleteConfirm", { key }))) return;
        appDataRun(del, async (s, say, g) => {
          if (s !== slug) return;
          await dx.api(appDataPath(s, key), { method: "DELETE" });
          await loadAppKeys(s, g);
          say(dx.t("studio.appDataDeleted", { key }), false);
        });
      });
      const item = row(key, "", [view, del]);
      item.dataset.key = key;
      list.appendChild(item);
    });
    if (!keys.length) emptyNote(list, "studio.emptyAppData");
    return keys.length;
  }

  $("app-data-load").addEventListener("click", (ev) => appDataRun(ev.currentTarget, async (slug, say, gen) => {
    const n = await loadAppKeys(slug, gen);
    say(dx.t("studio.appDataCount", { n }), false);
  }));
  $("app-data-save").addEventListener("click", (ev) => {
    const key = ($("app-data-key").value || "").trim();
    const raw = $("app-data-value").value || "";
    if (!key) { showStatus("app-data-status", dx.t("studio.appDataNeedKey"), true); return; }
    let value;
    try { value = JSON.parse(raw); } catch (err) { showStatus("app-data-status", dx.t("studio.appDataBadJson"), true); return; }
    appDataRun(ev.currentTarget, async (slug, say, gen) => {
      await postJson(appDataPath(slug, key), { value });
      await loadAppKeys(slug, gen);
      say(dx.t("studio.appDataSaved", { key }), false);
    });
  });
  $("app-data-clear").addEventListener("click", async (ev) => {
    const btn = ev.currentTarget;
    const slug = appDataSlug();
    if (!slug || !await askDanger("appDataClear", dx.t("studio.appDataClearConfirm", { slug }))) return;
    appDataRun(btn, async (s, say, gen) => {
      const res = await dx.api(appDataPath(s, null), { method: "DELETE" });
      await loadAppKeys(s, gen);
      say(dx.t("studio.appDataCleared", { n: res.deleted || 0 }), false);
    });
  });

  // -- publish a generated skill as a PUBLIC gist --
  $("skill-gist").addEventListener("click", async () => {
    const id = skillView && skillView.session_id;
    if (!id || gistFlight || skillOp) return;
    if (!await askDanger("skillGist", dx.t("studio.skillGistConfirm"))) return;
    gistFlight = true;
    syncSkillButtons();
    try {
      const res = await postJson(PG_API + "/skill-creator/github-gist", { session_id: id, confirm_public: true });
      gistDone = { id, url: res.gist_url || res.gist_id || "?" };       // published once: the button goes, the link stays
      skillNote = dx.t("studio.skillGistDone", { url: gistDone.url });
      skillNoteBad = false;
    } catch (err) {
      const msg = String((err && err.message) || "");
      skillNote = /GITHUB_TOKEN not configured/.test(msg) ? dx.t("studio.gitNoGithub") : dx.t("studio.skillGistFailed", { why: failText(err) });
      skillNoteBad = true;
    } finally {
      gistFlight = false;
      if (skillVisible()) paintSkill();
      else syncSkillButtons();
    }
  });

  function paintBrain(data) {
    const box = $("settings-brain");
    box.replaceChildren();
    if (data && data.configured === false) {          // nothing set up yet: an empty state, not an error (PR1)
      const note = document.createElement("p");
      note.textContent = dx.t("studio.noModelYet");
      box.append(note);
      return;
    }
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

  // ---- Harness settings: every setting with its source and lock, models per task, engines, proof levels ----------
  let harnessSeq = 0;
  const SRC_KEYS = { env: "studio.srcEnv", saved: "studio.srcSaved", default: "studio.srcDefault", unavailable: "studio.srcUnavailable" };

  function tOr(key, fallback) {
    const v = dx.t(key);
    return v === key ? (fallback || key) : v;
  }

  function badge(src) {
    const b = document.createElement("span");
    b.className = "badge";
    b.dataset.src = src;
    b.textContent = tOr(SRC_KEYS[src], src);
    return b;
  }

  function cfgControl(row) {
    let el;
    if (row.kind === "bool") {
      el = document.createElement("select");
      [["true", "studio.settingsOn"], ["false", "studio.settingsOff"]].forEach(([value, key]) => {
        const o = document.createElement("option");
        o.value = value;
        o.textContent = dx.t(key);
        el.appendChild(o);
      });
      el.value = String(!!row.effective);
    } else if (row.kind === "enum" && Array.isArray(row.choices) && row.choices.length) {
      el = document.createElement("select");
      row.choices.forEach((c) => {
        const o = document.createElement("option");
        o.value = String(c);
        o.textContent = String(c);
        el.appendChild(o);
      });
      el.value = String(row.effective);
    } else if (row.kind === "int") {
      el = document.createElement("input");
      el.type = "number";
      el.step = "1";
      if (row.min != null) el.min = String(row.min);
      if (row.max != null) el.max = String(row.max);
      el.value = row.effective == null ? "" : String(row.effective);
    } else {
      el = document.createElement("input");
      el.readOnly = true;
      el.value = row.effective == null ? "" : String(row.effective);
    }
    el.disabled = !row.writable;
    el.setAttribute("aria-label", tOr(row.title_key, row.key));
    return el;
  }

  function cfgValue(row, el) {
    if (row.kind === "bool") return el.value === "true";
    if (row.kind === "int") return el.value === "" ? NaN : Number(el.value);
    return el.value;
  }

  function buildConfigRow(row) {
    const item = document.createElement("div");
    item.className = "cfg-row";
    item.dataset.key = row.key;
    item.dataset.locked = row.env_locked ? "1" : "0";
    const name = document.createElement("div");
    name.className = "cfg-name";
    const b = document.createElement("b");
    b.textContent = tOr(row.title_key, row.key);
    const small = document.createElement("small");
    small.textContent = row.key;
    name.append(b, small);
    const control = cfgControl(row);
    const wrap = document.createElement("div");
    wrap.className = "cfg-control";
    wrap.appendChild(control);
    const actions = document.createElement("div");
    actions.className = "cfg-actions";
    actions.appendChild(badge(row.source || "default"));
    const note = document.createElement("p");
    note.className = "cfg-note";
    note.hidden = true;
    if (row.env_locked) {
      note.textContent = dx.t("studio.settingsLockedBy", { env: row.env || "" });
      note.hidden = false;
    } else if (!row.writable) {
      note.textContent = dx.t("studio.settingsReadOnly") + (row.source === "unavailable" && row.error ? " " + row.error : "");
      note.hidden = false;
    }
    if (row.writable) {
      const save = document.createElement("button");
      save.type = "button";
      save.className = "primary cfg-save";
      save.textContent = dx.t("studio.settingsSave");
      save.addEventListener("click", () => saveConfigRow(row, control, note, item, save));
      actions.appendChild(save);
    }
    item.append(name, wrap, actions, note);
    return item;
  }

  function paintConfig(rows) {
    const box = $("settings-config");
    box.replaceChildren();
    (rows || []).forEach((row) => box.appendChild(buildConfigRow(row)));
  }

  function cfgError(note, item, text) {
    note.textContent = text;
    note.hidden = false;
    item.dataset.state = "error";
  }

  async function saveConfigRow(row, control, note, item, button) {
    item.dataset.state = "";
    note.hidden = true;
    const value = cfgValue(row, control);
    if (row.kind === "int" && !Number.isFinite(value)) {          // an empty field would be sent as null
      cfgError(note, item, dx.t("studio.settingsNeedNumber"));
      return;
    }
    await runAction(button, "harness-status", async () => {
      try {
        const data = await postJson("/api/v1/harness/config", { key: row.key, value });
        const fresh = (data.settings || []).find((r) => r.key === row.key);
        // Replace only the saved row: edits the owner has not saved in the other rows are theirs to keep.
        if (fresh && item.isConnected) item.replaceWith(buildConfigRow(fresh));
        showStatus("harness-status", dx.t("studio.saved"), false);
      } catch (err) {
        const msg = String((err && err.message) || "");
        cfgError(note, item, msg === "env_locked" ? dx.t("studio.settingsLockedBy", { env: row.env || "" }) : failText(err));
      }
    });
  }

  function intentModelText(row) {
    const r = row.resolved || {};
    if (r.model) return (r.provider ? r.provider + ":" : "") + r.model;
    // Nothing resolves yet (for example no API key), but the owner may already have chosen one: show the choice.
    const chosen = row.owner_choice && row.owner_choice !== "auto" ? String(row.owner_choice) : "";
    return chosen || dx.t("studio.settingsNoModel");
  }

  function intentTag(row) {
    // The mentor reports its own choice id (brain, rules, a catalog id); only a real provider:model is a tag.
    const raw = row.intent === "mentor" ? row.owner_tag : row.owner_choice;
    return raw && raw !== "auto" ? String(raw) : "";
  }

  function buildIntentRow(row, data) {
    const item = document.createElement("div");
    item.className = "cfg-row";
    item.dataset.intent = row.intent;
    const locked = !!((row.resolved && row.resolved.locked) || (row.selectable && data.env_locked && row.intent !== "mentor"));
    item.dataset.locked = locked ? "1" : "0";
    const name = document.createElement("div");
    name.className = "cfg-name";
    const b = document.createElement("b");
    b.textContent = tOr("studio.intent." + row.intent, row.intent);
    const small = document.createElement("small");
    small.textContent = intentModelText(row) + (row.resolved && row.resolved.source ? " · " + row.resolved.source : "");
    name.append(b, small);
    const wrap = document.createElement("div");
    wrap.className = "cfg-control";
    const actions = document.createElement("div");
    actions.className = "cfg-actions";
    const note = document.createElement("p");
    note.className = "cfg-note";
    note.hidden = true;
    if (row.selectable) {
      const input = document.createElement("input");
      input.autocomplete = "off";
      input.value = intentTag(row);
      input.placeholder = row.default ? dx.t("studio.intentDefault", { model: row.default }) : "provider:model";
      input.disabled = locked;
      input.setAttribute("aria-label", tOr("studio.intent." + row.intent, row.intent));
      wrap.appendChild(input);
      if (locked) {
        note.textContent = dx.t("studio.settingsLockedBy", { env: row.intent === "mentor" ? "DAEDALUS_MENTOR_MODEL" : "DAEDALUS_MODEL" });
        note.hidden = false;
        actions.appendChild(badge("env"));
      } else {
        const save = document.createElement("button");
        save.type = "button";
        save.className = "primary cfg-save";
        save.textContent = dx.t("studio.settingsSave");
        const clear = document.createElement("button");
        clear.type = "button";
        clear.className = "cfg-clear";
        clear.textContent = dx.t("studio.settingsClear");
        save.addEventListener("click", () => saveIntent(row.intent, input.value.trim(), note, item, save));
        clear.addEventListener("click", () => saveIntent(row.intent, "", note, item, clear));
        actions.append(save, clear);
        if (row.intent === "persona" || row.intent === "writer") {   // both write the one writer override
          note.textContent = dx.t("studio.intentShared");
          note.hidden = false;
        }
      }
    } else {
      note.textContent = row.default ? dx.t("studio.intentPinned", { model: row.default }) : dx.t("studio.intentFollows");
      note.hidden = false;
    }
    item.append(name, wrap, actions, note);
    return item;
  }

  function paintIntents(data) {
    const box = $("settings-intents");
    box.replaceChildren();
    ((data && data.intents) || []).forEach((row) => box.appendChild(buildIntentRow(row, data)));
  }

  async function saveIntent(intent, model, note, item, button) {
    item.dataset.state = "";
    note.hidden = true;
    await runAction(button, "harness-status", async () => {
      try {
        const data = await postJson("/api/v1/harness/intents", { intent, model });
        // Persona and Writer share one setting, so both rows are rebuilt; every other row keeps its unsaved edits.
        ((data && data.intents) || []).forEach((fresh) => {
          const same = fresh.intent === intent || (fresh.intent === "persona" && intent === "writer") || (fresh.intent === "writer" && intent === "persona");
          const el = document.querySelector("#settings-intents .cfg-row[data-intent='" + fresh.intent + "']");
          if (same && el) el.replaceWith(buildIntentRow(fresh, data));
        });
        showStatus("harness-status", dx.t("studio.saved"), false);
      } catch (err) {
        const msg = String((err && err.message) || "");
        cfgError(note, item, msg === "env_locked" ? dx.t("studio.settingsLockedBy", { env: "DAEDALUS_MODEL" }) : failText(err));
      }
    });
  }

  function listItem(left, right) {
    const li = document.createElement("li");
    const a = document.createElement("span");
    a.textContent = left;
    const c = document.createElement("span");
    c.textContent = right;
    li.append(a, c);
    return li;
  }

  function paintEngines(data) {
    const box = $("settings-engines");
    box.replaceChildren();
    if (!data || data.ok === false) return;
    const engine = data.engine || {};
    const p1 = document.createElement("p");
    p1.textContent = engine.ok ? dx.t("studio.engineFound", { path: engine.path || "" }) : dx.t("studio.engineMissing");
    box.appendChild(p1);
    const ul = document.createElement("ul");
    Object.keys(data.tools || {}).sort().forEach((name) => {
      ul.appendChild(listItem(name, data.tools[name] ? dx.t("studio.toolOk") : dx.t("studio.toolMissing")));
    });
    const head = document.createElement("p");
    head.className = "kicker";
    head.textContent = dx.t("studio.engineTools");
    box.append(head, ul);
    const p2 = document.createElement("p");
    p2.textContent = dx.t("studio.engineSessions", { n: data.max_sessions, cap: data.platform_cap });
    box.appendChild(p2);
    const v = data.vision || {};
    const p3 = document.createElement("p");
    p3.dataset.role = "vision-state";
    p3.textContent = visionLine(v);
    box.appendChild(p3);
    const list = document.createElement("div");
    list.className = "cfg-list";
    list.appendChild(actionRow({
      id: "vision-test", title: dx.t("studio.visionTest"), sub: dx.t("studio.visionTestHint"),
      buttons: [{ label: dx.t("studio.visionTest"), run: testVision }],
    }));
    box.appendChild(list);
  }

  function visionLine(v) {
    return v.model
      ? dx.t("studio.visionState", { model: (v.provider ? v.provider + ":" : "") + v.model,
          state: v.verified ? dx.t("studio.visionVerified") : dx.t("studio.visionUnverified") })
      : dx.t("studio.visionNone");
  }

  function paintProof(data) {
    const box = $("settings-proof");
    box.replaceChildren();
    const ul = document.createElement("ul");
    ((data && data.proof_levels) || []).forEach((lv) => {
      const li = listItem(tOr(lv.key, "L" + lv.level), lv.reachable ? dx.t("studio.proofReach") : dx.t("studio.proofNoReach"));
      li.dataset.level = String(lv.level);
      li.dataset.reachable = lv.reachable ? "1" : "0";
      ul.appendChild(li);
    });
    box.appendChild(ul);
  }


  // ---- A6.1b: workers, provider test, connections, MCP, durability, vision test ----------------------------------
  const ERR_KEYS = { auth: "studio.errAuth", quota: "studio.errQuota", timeout: "studio.errTimeout",
    budget_blocked: "studio.errBudget", provider_error: "studio.errProvider", unsupported_modality: "studio.errModality",
    not_configured: "studio.errNotConfigured", disabled: "studio.errDisabled", routing: "studio.errRouting",
    busy: "studio.errBusy" };
  const GIT_CODE_KEYS = { not_configured: "studio.codeNotConfigured", invalid_token: "studio.codeInvalid",
    forbidden: "studio.codeForbidden", api_error: "studio.codeApi", network: "studio.codeNetwork" };

  // One row: a name, a short state, one or more buttons, and a note under the row that carries the outcome.
  // A button's ``run`` returns {text, bad} to show, or null when there is nothing to say (declined, or row repainted).
  const lastNotes = {};                // row id -> last outcome, so a repaint never hides the result of a (billed) test
  function showNote(item, res) {
    const note = item.querySelector(".cfg-note");
    note.textContent = res.text;
    note.hidden = false;
    item.dataset.state = res.bad ? "error" : "ok";
  }

  function actionRow(o) {
    const item = document.createElement("div");
    item.className = "cfg-row";
    item.dataset.id = o.id;
    const name = document.createElement("div");
    name.className = "cfg-name";
    const b = document.createElement("b");
    b.textContent = o.title;
    const small = document.createElement("small");
    small.textContent = o.sub || "";
    name.append(b, small);
    const info = document.createElement("div");
    info.className = "cfg-control";
    info.textContent = o.info || "";
    const actions = document.createElement("div");
    actions.className = "cfg-actions";
    const note = document.createElement("p");
    note.className = "cfg-note";
    note.hidden = true;
    (o.buttons || []).forEach((def) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.textContent = def.label;
      btn.disabled = !!def.disabled;
      btn.addEventListener("click", async () => {
        item.dataset.state = "";
        note.hidden = true;
        await runAction(btn, o.statusId || "harness-status", async () => {
          const res = await def.run();
          if (!res) return;
          lastNotes[o.id] = res;
          // the row may have been repainted while the call ran: show the outcome on whichever copy is on screen now
          const live = item.isConnected ? item : document.querySelector(".cfg-row[data-id='" + o.id + "']");
          if (live) showNote(live, res);
        });
      });
      actions.appendChild(btn);
    });
    item.append(name, info, actions, note);
    if (lastNotes[o.id]) showNote(item, lastNotes[o.id]);
    return item;
  }

  function errText(err) {
    const type = err && err.type;
    return tOr(ERR_KEYS[type], dx.t("studio.errUnknown"));
  }

  async function testWorker(slot) {
    const r = await postJson("/api/v1/harness/test-provider", { slot });
    if (r.ok) return { text: dx.t("studio.workerOk", { model: r.model || "", ms: r.latency_ms == null ? "?" : r.latency_ms, reply: r.reply || "" }), bad: false };
    return { text: dx.t("studio.workerFail", { why: errText(r.error) }), bad: true };
  }

  function paintWorkers(data) {
    const box = $("settings-workers");
    box.replaceChildren();
    ((data && data.slots) || []).forEach((w) => {
      box.appendChild(actionRow({
        id: "worker-" + w.slot,
        title: w.slot,
        sub: (w.provider ? w.provider + ":" : "") + (w.model || ""),
        info: !w.enabled ? dx.t("studio.workerOff")
          : (w.key_set ? dx.t("studio.workerKeySet") : (w.keyless_ok ? dx.t("studio.workerKeyless") : dx.t("studio.workerNoKey"))),
        buttons: [{ label: dx.t("studio.workerTest"), run: () => testWorker(w.slot) }],
      }));
    });
  }

  async function testLink(target) {
    const r = await postJson("/api/v1/harness/connections/test", { target });
    if (r.ok) {
      let text = dx.t("studio.codeOk", { who: r.login || r.username || "" });
      if (typeof r.can_push === "boolean") text += " · " + dx.t(r.can_push ? "studio.canPush" : "studio.cannotPush");
      return { text, bad: false };
    }
    return { text: tOr(GIT_CODE_KEYS[r.code], dx.t("studio.errUnknown")), bad: true };
  }

  function paintLinks() {
    const box = $("settings-links");
    box.replaceChildren();
    [["github", "studio.linkGithub"], ["gitlab", "studio.linkGitlab"]].forEach(([target, key]) => {
      box.appendChild(actionRow({
        id: "link-" + target, title: dx.t(key), sub: target,
        buttons: [{ label: dx.t("studio.linkTest"), run: () => testLink(target) }],
      }));
    });
  }

  async function toggleMcp(row, enable) {
    if (enable && !await ask("mcp", dx.t("studio.mcpConfirm", { id: row.id }))) return null;
    const data = await postJson("/api/v1/harness/mcp", { id: row.id, enabled: enable });
    if (!data || data.ok === false || !Array.isArray(data.servers)) {            // a refused toggle must not empty the list
      showStatus("harness-status", dx.t("studio.requestFailed") + (data && data.error ? ": " + data.error : ""), true);
      return null;
    }
    paintMcp(data.servers);
    showStatus("harness-status", dx.t("studio.saved"), false);
    return null;
  }

  function paintMcp(servers) {
    const box = $("settings-mcp");
    box.replaceChildren();
    (servers || []).forEach((row) => {
      const blocked = row.blocked_offline && !row.enabled;
      box.appendChild(actionRow({
        id: "mcp-" + row.id, title: row.id,
        info: blocked ? dx.t("studio.mcpBlocked") : (row.enabled ? dx.t("studio.mcpOn") : dx.t("studio.mcpOffState")),
        buttons: [{ label: row.enabled ? dx.t("studio.mcpDisable") : dx.t("studio.mcpEnable"), disabled: blocked,
                    run: () => toggleMcp(row, !row.enabled) }],
      }));
    });
  }

  function paintDurability(d) {
    const box = $("settings-durability");
    box.replaceChildren();
    if (!d || d.ok === false) return;
    const p1 = document.createElement("p");
    p1.textContent = dx.t("studio.durStorage", { state: d.storage_configured ? dx.t("studio.durConfigured") : dx.t("studio.durNot") });
    const p2 = document.createElement("p");
    p2.textContent = dx.t("studio.durProjects", { n: d.projects, p: d.published, e: d.empty_workspaces });
    box.append(p1, p2);
  }

  function paintConnections(v) {
    paintMcp(v.mcp && v.mcp.ok !== false ? v.mcp.servers : []);
    paintDurability(v.durability);
  }

  async function testVision() {
    const r = await postJson("/api/v1/harness/vision-probe", {});
    try {                                         // the verdict is stored now: refresh the state line, not the whole block
      const eng = await dx.api("/api/v1/harness/engines");
      const line = document.querySelector("#settings-engines [data-role='vision-state']");
      if (line) line.textContent = visionLine(eng.vision || {});
    } catch (err) { /* the note below still tells the truth */ }
    if (!r.ok) {
      const why = r.error === "busy" ? dx.t("studio.errBusy") : (r.error || "?");
      return { text: r.error === "no_mentor_model" ? dx.t("studio.visionNoModel") : dx.t("studio.visionFailed", { why }), bad: true };
    }
    if (!r.perceived) return { text: dx.t("studio.visionNotPerceived"), bad: true };
    return r.recorded ? { text: dx.t("studio.visionPerceived"), bad: false } : { text: dx.t("studio.visionNotSaved"), bad: true };
  }


  // ---- A6.1c: kernel export and the upgrade-pool scan -----------------------------------------------------------
  const KERNEL_ERR = { bad_repo: "studio.kerrBadRepo", bad_branch: "studio.kerrBadBranch", protected_branch: "studio.kerrProtected",
    bad_paths: "studio.kerrBadPaths", secret_path: "studio.kerrSecret", protected_path: "studio.kerrProtectedPath",
    not_configured: "studio.kerrNoToken",
    no_files: "studio.kerrNoFiles", push_failed: "studio.kerrPushFailed", confirm_required: "studio.kerrConfirm" };
  let kernelSaved = null;           // what the server has: the push uses this, never the unsaved fields

  function kernelErr(err) {
    const code = String((err && err.message) || "");
    return KERNEL_ERR[code] ? dx.t(KERNEL_ERR[code]) : failText(err);
  }

  function fieldRow(id, labelKey, control, note) {
    const item = document.createElement("div");
    item.className = "cfg-row";
    item.dataset.id = id;
    const name = document.createElement("div");
    name.className = "cfg-name";
    const b = document.createElement("b");
    b.textContent = dx.t(labelKey);
    name.appendChild(b);
    const wrap = document.createElement("div");
    wrap.className = "cfg-control";
    control.setAttribute("aria-label", dx.t(labelKey));
    wrap.appendChild(control);
    item.append(name, wrap, document.createElement("div"));
    if (note) {
      const p = document.createElement("p");
      p.className = "cfg-note";
      p.textContent = note;
      item.appendChild(p);
    }
    return item;
  }

  function paintKernel(data) {
    const box = $("settings-kernel");
    // A repaint (a reload of Settings) must not throw away what the owner typed and has not saved yet.
    const typed = box.querySelector("[data-id='kernel-repo'] input") ? {
      repo: box.querySelector("[data-id='kernel-repo'] input").value,
      branch: box.querySelector("[data-id='kernel-branch'] input").value,
      paths: box.querySelector("[data-id='kernel-paths'] textarea").value,
    } : null;
    const hadUnsaved = !!(typed && kernelSaved && (typed.repo.trim() !== kernelSaved.repo || typed.branch.trim() !== kernelSaved.branch
      || typed.paths.split("\n").map((x) => x.trim()).filter(Boolean).join("\n") !== kernelSaved.paths.join("\n")));
    box.replaceChildren();
    if (!data || data.ok === false) return;
    kernelSaved = { repo: data.repo, branch: data.branch, paths: (data.paths || []).slice() };
    const repo = document.createElement("input");
    repo.autocomplete = "off";
    repo.value = data.repo || "";
    const branch = document.createElement("input");
    branch.autocomplete = "off";
    branch.value = data.branch || "";
    const paths = document.createElement("textarea");
    paths.value = (data.paths || []).join("\n");
    if (hadUnsaved) { repo.value = typed.repo; branch.value = typed.branch; paths.value = typed.paths; }
    const missing = (data.missing_paths || []).join(", ");
    box.append(fieldRow("kernel-repo", "studio.kernelRepo", repo),
               fieldRow("kernel-branch", "studio.kernelBranch", branch),
               fieldRow("kernel-paths", "studio.kernelPaths", paths, missing ? dx.t("studio.kernelMissing", { paths: missing }) : ""));
    const read = () => ({ repo: repo.value.trim(), branch: branch.value.trim(),
      paths: paths.value.split("\n").map((x) => x.trim()).filter(Boolean) });
    const same = (a, b) => a.repo === b.repo && a.branch === b.branch && a.paths.join("\n") === b.paths.join("\n");
    const target = () => (kernelSaved ? kernelSaved.repo + " → " + kernelSaved.branch : "");
    const actions = actionRow({
      id: "kernel-actions", title: target(), sub: "",
      buttons: [
        { label: dx.t("studio.kernelSave"), run: async () => {
          try {
            const fresh = await postJson("/api/v1/harness/kernel/config", read());
            kernelSaved = { repo: fresh.repo, branch: fresh.branch, paths: (fresh.paths || []).slice() };
            repo.value = fresh.repo; branch.value = fresh.branch; paths.value = (fresh.paths || []).join("\n");
            const heading = actions.querySelector(".cfg-name b");
            if (heading) heading.textContent = target();                 // what Push will do, kept current
            const gone = (fresh.missing_paths || []).join(", ");
            return { text: dx.t("studio.kernelSaved") + (gone ? " " + dx.t("studio.kernelMissing", { paths: gone }) : ""), bad: false };
          } catch (err) { return { text: kernelErr(err), bad: true }; }
        } },
        { label: dx.t("studio.kernelPush"), run: () => {
          if (!kernelSaved || !same(read(), kernelSaved)) return Promise.resolve({ text: dx.t("studio.kernelDirty"), bad: true });
          return pushKernel(kernelSaved);
        } },
      ],
    });
    box.appendChild(actions);
  }

  async function pushKernel(saved) {
    if (!await ask("kernel", dx.t("studio.kernelConfirm", { repo: saved.repo, branch: saved.branch }))) return null;
    let res;
    try {
      res = await postJson("/api/v1/harness/kernel/push", { confirm: true });
    } catch (err) {
      if (String((err && err.message) || "") !== "non_fast_forward") return { text: kernelErr(err), bad: true };
      if (!await askDanger("kernelForce", dx.t("studio.kernelForce", { branch: saved.branch }))) return { text: dx.t("studio.kernelNotPushed"), bad: true };
      try {
        res = await postJson("/api/v1/harness/kernel/push", { confirm: true, force: true });
      } catch (err2) { return { text: kernelErr(err2), bad: true }; }
    }
    if (!res || res.ok === false) return { text: kernelErr({ message: res && res.error }), bad: true };     // never a success by default
    return { text: dx.t(res.forced ? "studio.kernelPushedForced" : "studio.kernelPushed", { files: res.files, repo: res.repo, branch: res.branch }), bad: false };
  }

  let poolTimer = null;
  function paintPool(d) {
    const box = $("settings-pool");
    box.replaceChildren();
    clearTimeout(poolTimer);
    if (!d || d.ok === false) return;
    if (d.running) {                       // a scan is running: look again until it ends, so the button and result are true
      poolTimer = setTimeout(() => {
        const panel = $("panel-settings");
        if (!panel || panel.hidden) return;              // not on screen: the next time Settings opens it reads the state again
        dx.api("/api/v1/harness/upgrade-pool").then(paintPool).catch(() => {});
      }, 2500);
    }
    const unknown = d.candidates < 0;
    const last = d.last ? (d.last.ok ? dx.t("studio.poolLastOk") : dx.t("studio.poolLastFail", { why: d.last.error || "?" })) : "";
    box.appendChild(actionRow({
      id: "pool-scan", title: dx.t("studio.settingsPool"), sub: last,
      info: unknown ? dx.t("studio.poolUnknown")
        : dx.t("studio.poolState", { n: d.candidates, state: d.running ? dx.t("studio.poolRunning") : dx.t("studio.poolIdle") }),
      buttons: [{ label: dx.t("studio.poolScan"), disabled: d.running || unknown, run: scanPool }],
    }));
  }

  async function scanPool() {
    if (!await ask("pool", dx.t("studio.poolConfirm"))) return null;
    let res;
    try {
      res = await postJson("/api/v1/harness/upgrade-pool/scan", {});
    } catch (err) {
      return { text: String((err && err.message) || "") === "already_running" ? dx.t("studio.poolBusy") : failText(err), bad: true };
    }
    dx.api("/api/v1/harness/upgrade-pool").then(paintPool).catch(() => {});
    return res.started ? { text: dx.t("studio.poolStarted", { n: res.candidates }), bad: false } : { text: dx.t("studio.poolNone"), bad: false };
  }

  async function loadHarnessSettings() {
    const mine = ++harnessSeq;
    paintLinks();
    const calls = ["/api/v1/harness/config", "/api/v1/harness/intents", "/api/v1/harness/engines",
      "/api/v1/harness/workers", "/api/v1/harness/connections", "/api/v1/harness/kernel",
      "/api/v1/harness/upgrade-pool"].map((p) => dx.api(p));
    const results = await Promise.allSettled(calls);
    if (mine !== harnessSeq) return;                  // a newer load owns the screen
    const problems = [];
    const paint = (res, fn) => {
      if (res.status === "rejected") { problems.push(failText(res.reason)); return; }
      try { fn(res.value); } catch (err) { problems.push(failText(err)); }     // one broken section must not hide the rest
    };
    paint(results[0], (v) => paintConfig(v.settings || []));
    paint(results[1], paintIntents);
    paint(results[2], (v) => { paintEngines(v); paintProof(v); });
    paint(results[3], paintWorkers);
    paint(results[4], paintConnections);
    paint(results[5], paintKernel);
    paint(results[6], paintPool);
    const unique = problems.filter((p, i) => problems.indexOf(p) === i);
    showStatus("harness-status", unique.length ? dx.t("studio.requestFailed") + ": " + unique.join(" · ") : "", unique.length > 0);
  }

  // ── Team (gap 3b): who is signed in; the owner invites, re-roles and disables people ──────────────────────────
  let teamMe = null;
  let mePending = null;

  function roleLabel(role) {
    return tOr("studio.role." + role, role);
  }

  function paintChip() {
    const chip = $("team-chip");
    document.body.dataset.role = teamMe ? (teamMe.role || "") : "";
    if (!chip) return;
    chip.hidden = !teamMe || teamMe.role === "owner";
    chip.textContent = teamMe ? teamMe.name + " · " + roleLabel(teamMe.role) : "";
  }

  // Asked fresh each time Settings opens (a role the owner changed, a sign-in that changed in this page), and once
  // at a time: the boot call and the Settings call share the request in flight.
  function loadMe() {
    if (mePending) return mePending;
    mePending = (async () => {
      try {
        teamMe = await dx.api("/api/v1/team/me");
      } catch (err) {
        teamMe = null;
      }
      paintChip();
      return teamMe;
    })();
    mePending.finally(() => { mePending = null; });
    return mePending;
  }

  function paintMembers(data) {
    const box = $("team-members");
    box.replaceChildren();
    const rows = (data && data.members) || [];
    if (!rows.length) {
      const p = document.createElement("p");
      p.className = "hint";
      p.textContent = dx.t("studio.teamNobody");
      box.appendChild(p);
      return;
    }
    rows.forEach((m) => {
      const other = m.role === "designer" ? "tester" : "designer";
      box.appendChild(actionRow({
        id: "member-" + m.id,
        statusId: "team-status",
        title: m.name,
        sub: roleLabel(m.role) + (m.disabled ? " · " + dx.t("studio.teamDisabled") : "") + " · " +
          dx.t("studio.teamSpent", {
            spent: ((teamSharing && teamSharing.spend && teamSharing.spend[m.id]) || 0).toFixed(2),
            cap: Number((teamSharing && teamSharing.caps && teamSharing.caps[m.id] != null) ? teamSharing.caps[m.id]
              : (teamSharing ? teamSharing.default_cap_usd : 2)).toFixed(2),
          }),
        buttons: [
          { label: dx.t("studio.teamSetCap"), run: () => setCap(m) },
          { label: dx.t("studio.teamMakeRole", { role: roleLabel(other) }),
            run: () => updateMember(m.id, { role: other }) },
          { label: dx.t(m.disabled ? "studio.teamEnable" : "studio.teamDisable"),
            run: () => updateMember(m.id, { disabled: !m.disabled }) },
        ],
      }));
    });
  }

  async function refreshTeamLists() {
    // After a change that succeeded: a failed refresh is its own message, never the change's (review 2026-11-12:
    // an invite that worked looked failed, and a second invite made a second live sign-in).
    try {
      await loadTeamLists();
    } catch (err) {
      showStatus("team-status", dx.t("studio.teamRefreshFailed") + ": " + failText(err), true);
    }
  }

  async function updateMember(id, body) {
    await postJson("/api/v1/team/members/" + encodeURIComponent(id), body);
    refreshTeamLists();
    return { text: dx.t("studio.saved"), bad: false };
  }

  function paintAudit(data) {
    const list = $("team-audit");
    list.replaceChildren();
    ((data && data.entries) || []).slice(0, 40).forEach((e) => {
      const li = document.createElement("li");
      const when = new Date((e.ts || 0) * 1000).toLocaleString();
      li.textContent = when + " · " + e.name + " (" + roleLabel(e.role) + ") · " + e.method + " " + e.path +
        (e.status ? " → " + e.status : "");
      list.appendChild(li);
    });
  }

  function paintShare(data) {
    const box = $("team-share-list");
    box.replaceChildren();
    const shared = new Set((data && data.shared) || []);
    ((data && data.available) || []).forEach((name) => {
      const label = document.createElement("label");
      const box2 = document.createElement("input");
      box2.type = "checkbox";
      box2.checked = shared.has(name);
      box2.dataset.key = name;
      box2.addEventListener("change", () => {
        const picked = Array.from(document.querySelectorAll("#team-share-list input:checked")).map((el) => el.dataset.key);
        runAction(box2, "team-status", async () => {
          paintShare(Object.assign({}, data, await postJson("/api/v1/team/sharing", { shared: picked })));
          showStatus("team-status", dx.t("studio.saved"), false);
        });
      });
      const span = document.createElement("span");
      span.textContent = name;
      label.append(box2, span);
      box.appendChild(label);
    });
    $("team-share").hidden = false;
  }

  async function setCap(member) {
    const now = (teamSharing && teamSharing.caps && teamSharing.caps[member.id] != null)
      ? teamSharing.caps[member.id] : (teamSharing ? teamSharing.default_cap_usd : 2);
    const typed = window.prompt(dx.t("studio.teamCapPrompt", { name: member.name }), String(now));
    if (typed === null) return null;
    const usd = Number(typed);
    if (!Number.isFinite(usd) || usd < 0) return { text: dx.t("studio.teamCapBad"), bad: true };
    const caps = {};
    caps[member.id] = usd;
    await postJson("/api/v1/team/sharing", { caps });
    refreshTeamLists();
    return { text: dx.t("studio.saved"), bad: false };
  }

  let teamSharing = null;

  async function loadTeamLists() {
    teamSharing = await dx.api("/api/v1/team/sharing");
    paintShare(teamSharing);
    paintMembers(await dx.api("/api/v1/team/members"));
    try {
      paintAudit(await dx.api("/api/v1/team/audit?limit=40"));
    } catch (err) {
      showStatus("team-status", dx.t("studio.requestFailed") + ": " + failText(err), true);
    }
  }

  function paintMyKeys(data) {
    const pick = $("team-key-name");
    const keep = pick.value;
    pick.replaceChildren();
    ((data && data.available) || []).forEach((name) => {
      const opt = document.createElement("option");
      opt.value = name;
      opt.textContent = name + ((data.own || []).includes(name) ? " ✓" : "");
      pick.appendChild(opt);
    });
    if (keep) pick.value = keep;
    $("team-mykeys-state").textContent = dx.t("studio.teamMyKeysState", {
      own: (data.own || []).join(", ") || "—",
      shared: (data.shared || []).join(", ") || "—",
      spent: Number(data.spent_shared_usd || 0).toFixed(2),
      cap: Number(data.cap_usd || 0).toFixed(2),
    });
  }

  async function loadMyKeys() {
    try {
      paintMyKeys(await dx.api("/api/v1/team/keys"));
    } catch (err) {
      showStatus("team-status", dx.t("studio.requestFailed") + ": " + failText(err), true);
    }
  }

  async function saveMyKey(button, value) {
    await runAction(button, "team-status", async () => {
      paintMyKeys(await postJson("/api/v1/team/keys", { name: $("team-key-name").value, value }));
      $("team-key-value").value = "";
      showStatus("team-status", dx.t(value ? "studio.saved" : "studio.teamKeyRemoved"), false);
    });
  }

  async function loadTeam() {
    $("team-token").hidden = true;               // an invite's sign-in is shown once: never again on a later visit
    $("team-token-value").textContent = "";
    const me = await loadMe();
    $("team-me").textContent = me ? dx.t("studio.teamYou", { name: me.name, role: roleLabel(me.role) }) : "";
    const owner = !!me && me.role === "owner";
    $("team-owner").hidden = !owner;
    $("team-not-owner").hidden = owner || !me;
    $("team-share").hidden = !owner;
    $("team-mykeys").hidden = !me || owner || me.role !== "designer";
    if (me && me.role === "designer") loadMyKeys();
    $("team-signout").hidden = owner || !me;
    if (owner) {
      try {
        await loadTeamLists();
      } catch (err) {
        showStatus("team-status", dx.t("studio.requestFailed") + ": " + failText(err), true);
      }
    }
    return me;
  }

  async function loadSettings() {
    const me = await loadTeam();
    if (me && me.role !== "owner") return;           // the rest of Settings is the owner's (the server says so too)
    showStatus("settings-status", dx.t("studio.loading"), false);
    $("settings-brain").replaceChildren();
    $("settings-fields").replaceChildren();
    loadHarnessSettings();
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
      let data;
      try {
        data = await postJson(path, body);
      } catch (err) {
        // A plain push the remote rejected: force only after the owner confirms.
        if (/non_fast_forward/.test(String((err && err.message) || "")) && await askDanger("forcePush", dx.t("studio.confirmForce"))) {
          data = await postJson(path, Object.assign({}, body, { force: true }));
        } else {
          throw err;
        }
      }
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

  function assetButtons() {
    return ["asset-generate", "asset-autopack", "asset-pipeline"].map($);
  }

  function syncAssetButtons() {
    const locked = assetFlight !== 0;
    assetButtons().forEach((btn) => {
      if (btn) btn.disabled = locked || !dx.getSlug();
    });
  }

  function assetTimedOut(err) {
    const msg = String((err && err.message) || "");
    return msg === dx.t("studio.timeout") || /timed out|timeout|изтекло|изтече/i.test(msg);
  }

  function assetConfirm(kind, slug, extra) {
    if (kind === "generate") return dx.t("studio.assetGenerateConfirm", { slug, path: extra.path });
    if (kind === "autopack") return dx.t("studio.assetAutopackConfirm", { slug });
    if (kind === "mesh") {
      const named = extra && extra.provider && extra.provider !== "auto" ? extra.provider : "";
      return dx.t("studio.assetMeshConfirm", {
        slug,
        provider: named || dx.t("studio.assetMeshProviders"),
      });
    }
    if (kind === "music") return dx.t("studio.assetMusicConfirm", { slug });
    if (kind === "status" && extra && extra.job_id) {
      return dx.t("studio.assetStatusPollConfirm", { slug, id: extra.job_id });
    }
    if (kind === "status") return dx.t("studio.assetStatusLocalConfirm", { slug });
    if (kind === "providers") return dx.t("studio.assetReadConfirm", { slug, action: kind });
    return dx.t("studio.assetWriteConfirm", { slug, action: kind });
  }

  function syncAssetFields() {
    const action = ($("asset-action") && $("asset-action").value) || "status";
    const mesh = action === "mesh";
    ["asset-mesh-prompt-field", "asset-mesh-name-field", "asset-mesh-provider-field"].forEach((id) => {
      const el = $(id);
      if (el) el.hidden = !mesh;
    });
    const job = $("asset-job-field");
    if (job) job.hidden = action !== "status";
    const kit = $("asset-kit-note");
    if (kit) kit.hidden = action !== "atlas" && action !== "tileset";
  }

  function renderAssetJobs(selected) {
    const sel = $("asset-job");
    if (!sel) return;
    const current = selected == null ? sel.value : selected;
    sel.replaceChildren();
    const local = document.createElement("option");
    local.value = "";
    local.textContent = dx.t("studio.assetJobLocal");
    sel.appendChild(local);
    assetJobIds.forEach((id) => {
      const opt = document.createElement("option");
      opt.value = id;
      opt.textContent = id;
      sel.appendChild(opt);
    });
    sel.value = current && assetJobIds.indexOf(current) !== -1 ? current : "";
  }

  function noteAssetResult(body, data) {
    if (!data || typeof data !== "object") return;
    const action = body && body.action;
    if (action === "mesh" && data.ok === true && typeof data.id === "string" && data.id) {
      if (assetJobIds.indexOf(data.id) === -1) assetJobIds.push(data.id);
      renderAssetJobs(data.id);
      return;
    }
    if (action === "status" && !body.job_id && Array.isArray(data.jobs)) {
      data.jobs.forEach((job) => {
        if (job && typeof job.id === "string" && job.id && assetJobIds.indexOf(job.id) === -1) {
          assetJobIds.push(job.id);
        }
      });
      renderAssetJobs(null);
    }
  }

  function describeGenerate(data) {
    if (!data || data.ok !== true) {
      const err = data && (data.error || data.detail);
      return dx.t("studio.assetNotOk") + (err ? ": " + err : "");
    }
    if (typeof data.path !== "string" || !data.path) return dx.t("studio.assetNoFile");
    if (typeof data.bytes === "number") return dx.t("studio.assetWrote", { path: data.path, bytes: data.bytes });
    return dx.t("studio.assetWrotePath", { path: data.path });
  }

  function describeAutopack(data) {
    if (!data || data.ok !== true) {
      const err = data && (data.error || data.detail);
      return { text: dx.t("studio.assetNotOk") + (err ? ": " + err : ""), bad: true, refresh: false };
    }
    const built = Array.isArray(data.built) ? data.built.filter((p) => typeof p === "string" && p) : [];
    const errors = Array.isArray(data.errors) ? data.errors : [];
    const parts = [dx.t("studio.assetPack", {
      planned: data.planned == null ? 0 : data.planned,
      built: built.length,
      errors: errors.length,
    })];
    if (built.length) parts.push(dx.t("studio.assetPackBuilt", { paths: built.join(", ") }));
    else parts.push(dx.t("studio.assetPackNone"));
    if (errors.length) {
      const lines = errors.map((row) => {
        if (!row || typeof row !== "object") return String(row || "");
        return (row.path || "") + (row.error ? ": " + row.error : "");
      }).join("; ");
      parts.push(dx.t("studio.assetPackErrors", { lines }));
    }
    return { text: parts.join("\n"), bad: errors.length > 0, refresh: built.length > 0 };
  }

  function pipelinePaths(data) {
    const paths = [];
    if (typeof data.png === "string" && data.png) paths.push(data.png);
    if (typeof data.json === "string" && data.json) paths.push(data.json);
    if (Array.isArray(data.files)) {
      data.files.forEach((file) => {
        if (typeof file === "string" && file) paths.push(file);
      });
    }
    return paths;
  }

  function describePipeline(action, data, sent) {
    const body = data && typeof data === "object" ? data : {};
    if (action === "music" || body.handoff === true) {
      return { text: dx.t("studio.assetMusic", { drop: body.drop || "assets/music.mp3" }), bad: true, refresh: false };
    }
    if (body.need_key === true || (action === "mesh" && body.ok !== true)) {
      return { text: dx.t("studio.assetNeedKey", { error: body.error || body.detail || "" }), bad: true, refresh: false };
    }
    if (body.ok !== true) {
      const err = body.error || body.detail || "";
      return { text: dx.t("studio.assetNotOk") + (err ? ": " + err : ""), bad: true, refresh: false };
    }
    if (action === "greybox" || body.greybox === true) {
      if (body.hero !== false) return { text: dx.t("studio.assetNoFile"), bad: true, refresh: false };
      return { text: dx.t("studio.assetGreybox", { path: body.glb || "" }), bad: false, refresh: false };
    }
    if (action === "mesh") {
      if (body.status === "done" && body.output) {
        return { text: dx.t("studio.assetMeshFile", { path: body.output }), bad: false, refresh: false };
      }
      return {
        text: dx.t("studio.assetMeshPending", { id: body.id || "", status: body.status || "" }),
        bad: false,
        refresh: false,
      };
    }
    if (action === "status" && sent && sent.job_id) {
      if (body.ok === true && body.status === "done" && typeof body.output === "string" && body.output) {
        return {
          text: dx.t("studio.assetMeshFile", { path: body.output }) + " " + dx.t("studio.assetJobUpdated"),
          bad: false,
          refresh: true,
        };
      }
      return {
        text: dx.t("studio.assetStatusChecked", {
          id: body.id || sent.job_id,
          status: body.status || "",
        }),
        bad: false,
        refresh: false,
      };
    }
    if (action === "status" || action === "providers") {
      const parts = [action === "status" ? dx.t("studio.assetStatusLocal") : dx.t("studio.assetRead")];
      const jobs = Array.isArray(body.jobs) ? body.jobs : [];
      jobs.forEach((job) => {
        if (!job || typeof job !== "object") return;
        if (job.status === "done" && job.output) parts.push(dx.t("studio.assetReadFile", { path: job.output }));
        else if (job.id) parts.push(dx.t("studio.assetMeshPending", { id: job.id, status: job.status || "" }));
      });
      return { text: parts.join("\n"), bad: false, refresh: false };
    }
    const paths = pipelinePaths(body);
    if (!paths.length) return { text: dx.t("studio.assetNoFile"), bad: true, refresh: false };
    return { text: dx.t("studio.assetLocal", { paths: paths.join(", ") }), bad: false, refresh: false };
  }

  function assetStill(flight, gen, slug) {
    return flight === assetFlight && gen === assetGen && dx.getSlug() === slug;
  }

  function finishAsset(flight, gen, slug, text, bad, refresh) {
    if (!assetStill(flight, gen, slug)) return;
    showStatus("asset-status", text, bad);
    if (refresh) loadFiles(slug);
  }

  function failAsset(flight, gen, slug, err) {
    if (!assetStill(flight, gen, slug)) return;
    const text = assetTimedOut(err)
      ? dx.t("studio.assetUnknown")
      : dx.t("studio.requestFailed") + ": " + dx.errText(err);
    showStatus("asset-status", text, true);
  }

  async function startAsset(kind, path, body, timeoutMs, describe) {
    if (assetFlight) return;
    const slug = dx.getSlug();
    if (!slug) {
      showStatus("asset-status", dx.t("studio.assetNeedSlug"), true);
      return;
    }
    if (kind === "generate" && !String((body && body.prompt) || "").trim()) {
      showStatus("asset-status", dx.t("studio.assetNeedPrompt"), true);
      return;
    }
    if (kind === "autopack" && !String((body && body.description) || "").trim()) {
      showStatus("asset-status", dx.t("studio.assetNeedDesc"), true);
      return;
    }
    if (kind === "pipeline" && body && body.action === "mesh" && !String(body.prompt || "").trim()) {
      showStatus("asset-status", dx.t("studio.assetNeedMeshPrompt"), true);
      return;
    }
    const action = kind === "pipeline" ? body.action : kind;
    if (!await ask("asset", assetConfirm(action, slug, body))) return;
    const gen = assetGen;
    const flight = ++assetFlight;
    syncAssetButtons();
    dx.api("/admin/daedalus/playground/api/projects/" + slugPath(slug) + path, {
      method: "POST",
      body: JSON.stringify(body),
      timeoutMs,
    }).then((data) => {
      if (assetStill(flight, gen, slug)) noteAssetResult(body, data);
      const view = describe(data);
      if (typeof view === "string") finishAsset(flight, gen, slug, view, !(data && data.ok === true && data.path), !!(data && data.ok === true && data.path));
      else finishAsset(flight, gen, slug, view.text, view.bad, view.refresh);
    }).catch((err) => {
      failAsset(flight, gen, slug, err);
    }).finally(() => {
      if (flight === assetFlight) {
        assetFlight = 0;
        syncAssetButtons();
      }
    });
  }

  function shipButtons() {
    return [
      "ship-unity-init",
      "ship-unity-build",
      "ship-unity-attach",
      "ship-firebase",
      "ship-hybrid-init",
      "ship-hybrid-export",
      "ship-cap-init",
      "ship-cap-export",
    ].map($);
  }

  function syncShipButtons() {
    const locked = shipFlight !== 0 || !dx.getSlug();
    shipButtons().forEach((btn) => {
      if (btn) btn.disabled = locked;
    });
  }

  function shipFields() {
    const projectId = ($("ship-firebase-project") && $("ship-firebase-project").value || "").trim();
    const bundleId = ($("ship-bundle") && $("ship-bundle").value || "").trim();
    const ids = {};
    if (projectId) ids.project_id = projectId;
    if (bundleId) ids.bundle_id = bundleId;
    return {
      ids: ids,
      projectId: projectId,
      bundleId: bundleId,
      template: ($("ship-template") && $("ship-template").value || "daedalus_minimal").trim() || "daedalus_minimal",
      preset: ($("ship-preset") && $("ship-preset").value || "custom").trim() || "custom",
    };
  }

  function shipKind() {
    const slug = dx.getSlug();
    const rows = dx.projects() || [];
    for (let i = 0; i < rows.length; i += 1) {
      if (rows[i] && rows[i].slug === slug) return String(rows[i].kind || "").toLowerCase();
    }
    return "";
  }

  function unityWritesFirebase(template, preset) {
    if (template === "unity_game_framework") return false;
    if (template === "daedalus_ugf") return true;
    return preset !== "racing" && preset !== "godot_2d" && preset !== "godot_3d";
  }

  // A template can be cloned when the server catalog gives it a repository; the HTML marks those options (a test keeps them in step).
  function unityClones(template) {
    const opt = $("ship-template") && $("ship-template").querySelector("option[value='" + template + "']");
    return !!(opt && opt.dataset.clone === "1");
  }

  function syncShipVisibility() {
    const fields = shipFields();
    const kind = shipKind();
    const unity = fields.preset !== "hybrid_mobile";
    const hybrid = fields.preset === "hybrid_mobile" || kind === "vite";
    const cap = kind === "static" || kind === "vite" || kind === "vite-react" || fields.preset === "hybrid_mobile";
    [
      ["ship-unity-init", unity],
      ["ship-unity-build", unity],
      ["ship-unity-attach", unity && unityClones(fields.template)],   // only templates with a repository can be attached
      ["ship-hybrid-init", hybrid],
      ["ship-hybrid-export", hybrid],
      ["ship-cap-init", cap],
      ["ship-cap-export", cap],
    ].forEach((pair) => {
      const el = $(pair[0]);
      if (el) el.hidden = !pair[1];
    });
  }

  function shipStill(flight, gen, slug) {
    return flight === shipFlight && gen === shipGen && dx.getSlug() === slug;
  }

  function shipTimedOut(err) {
    const msg = String((err && err.message) || "");
    return msg === dx.t("studio.timeout") || /timed out|timeout|изтекло|изтече/i.test(msg);
  }

  function shipPaths(data) {
    const rows = data && Array.isArray(data.written) ? data.written : [];
    return rows.filter((path) => typeof path === "string" && path);
  }

  function shipWrote(data) {
    const paths = shipPaths(data);
    if (!paths.length) return dx.t("studio.shipNoNew");
    return dx.t("studio.shipWrote", { paths: paths.join("\n") });
  }

  function describeAttach(data) {
    if (!data || data.ok !== true) {
      const err = data && (data.error || data.detail);
      return { text: dx.t("studio.shipNotOk") + (err ? ": " + err : ""), bad: true, refresh: false };
    }
    if (data.status === "already_present") return { text: dx.t("studio.shipAttachPresent", { path: data.cloned || "" }), bad: false, refresh: false };
    return { text: dx.t("studio.shipCloned", { path: data.cloned || "" }), bad: false, refresh: true };
  }

  function describeUnity(data) {
    if (!data || data.ok !== true) {
      const err = data && (data.error || data.detail);
      return { text: dx.t("studio.shipNotOk") + (err ? ": " + err : ""), bad: true, refresh: false };
    }
    const parts = [shipWrote(data)];
    const cloned = typeof data.cloned === "string" ? data.cloned : "";
    let bad = false;
    if (cloned.indexOf("FAILED") === 0) {
      parts.push(dx.t("studio.shipCloneFailed", { detail: cloned }));
      bad = true;
    } else if (cloned) {
      parts.push(dx.t("studio.shipCloned", { path: cloned }));
    }
    parts.push(dx.t("studio.shipKind", { kind: "unity" }));
    if (typeof data.preview === "string" && data.preview) {
      parts.push(dx.t("studio.shipPreview", { path: data.preview }));
    }
    return { text: parts.join("\n"), bad: bad, refresh: shipPaths(data).length > 0 };
  }

  function describeFirebase(data) {
    if (!data || data.ok !== true) {
      const err = data && (data.error || data.detail);
      return { text: dx.t("studio.shipNotOk") + (err ? ": " + err : ""), bad: true, refresh: false };
    }
    return {
      text: shipWrote(data) + "\n" + dx.t("studio.shipPlaceholder"),
      bad: false,
      refresh: shipPaths(data).length > 0,
    };
  }

  function describeHybrid(data) {
    if (!data || data.ok !== true) {
      const err = data && (data.error || data.detail);
      return { text: dx.t("studio.shipNotOk") + (err ? ": " + err : ""), bad: true, refresh: false };
    }
    const parts = [shipWrote(data), dx.t("studio.shipKind", { kind: "vite" })];
    if (typeof data.preview === "string" && data.preview) {
      parts.push(dx.t("studio.shipPreview", { path: data.preview }));
    }
    return { text: parts.join("\n"), bad: false, refresh: shipPaths(data).length > 0 };
  }

  function describeCapInit(data) {
    if (!data || data.ok !== true) {
      const err = data && (data.error || data.detail);
      return { text: dx.t("studio.shipNotOk") + (err ? ": " + err : ""), bad: true, refresh: false };
    }
    if (data.skipped === true) {
      return { text: dx.t("studio.shipSkipped", { reason: data.reason || "" }), bad: false, refresh: false };
    }
    return {
      text: shipWrote(data) + "\n" + dx.t("studio.shipNoStore"),
      bad: false,
      refresh: shipPaths(data).length > 0,
    };
  }

  function describeHybridExport(data) {
    if (!data || data.ok !== true) {
      const err = data && (data.error || data.detail);
      return { text: dx.t("studio.shipNotOk") + (err ? ": " + err : ""), bad: true, refresh: false };
    }
    const man = data.manifest && typeof data.manifest === "object" ? data.manifest : {};
    const steps = Array.isArray(man.next_steps) ? man.next_steps.filter((step) => typeof step === "string" && step) : [];
    const parts = [shipWrote(data), dx.t("studio.shipNoIpa")];
    if (steps.length) parts.push(steps.join("\n"));
    return { text: parts.join("\n"), bad: false, refresh: shipPaths(data).length > 0 };
  }

  function describeCapExport(data) {
    if (!data || data.ok !== true) {
      const err = data && (data.error || data.detail);
      return { text: dx.t("studio.shipNotOk") + (err ? ": " + err : ""), bad: true, refresh: false };
    }
    const man = data.manifest && typeof data.manifest === "object" ? data.manifest : {};
    const steps = Array.isArray(man.next_steps) ? man.next_steps.filter((step) => typeof step === "string" && step) : [];
    const parts = [shipWrote(data), dx.t("studio.shipNoStore"), dx.t("studio.shipNoIpa")];
    if (man.skipped === true) parts.push(dx.t("studio.shipSkipped", { reason: steps[0] || "" }));
    else if (steps.length) parts.push(steps.join("\n"));
    return { text: parts.join("\n"), bad: false, refresh: shipPaths(data).length > 0 };
  }

  function describeBuild(data) {
    if (!data || data.ok !== true) {
      const err = data && (data.error || data.detail);
      return { text: dx.t("studio.shipNotOk") + (err ? ": " + err : ""), bad: true, refresh: false };
    }
    const parts = [shipWrote(data)];
    if (data.local_only === true) parts.push(dx.t("studio.shipNoStore"));
    return { text: parts.join("\n"), bad: false, refresh: shipPaths(data).length > 0 };
  }

  function shipPlan(action, fields) {
    const slug = dx.getSlug();
    const ids = Object.assign({}, fields.ids);
    if (action === "unity") {
      const extra = [
        unityWritesFirebase(fields.template, fields.preset) ? dx.t("studio.shipUnityExtraFirebase") : dx.t("studio.shipUnityExtraNoFirebase"),
        dx.t("studio.shipUnityExtraFollow"),
        unityClones(fields.template) ? dx.t("studio.shipUnityExtraClone") : dx.t("studio.shipUnityExtraNoClone"),
        dx.t("studio.shipPlaceholder"),
      ].join(" ");
      const body = { template_id: fields.template, studio_preset: fields.preset };
      if (ids.project_id) body.project_id = ids.project_id;
      if (ids.bundle_id) body.bundle_id = ids.bundle_id;
      const steps = [{ path: "/unity/init", body: body, timeoutMs: SHIP_UNITY_TIMEOUT_MS, describe: describeUnity }];
      if (ids.project_id || ids.bundle_id) {
        steps.push({ path: "/firebase/configure", body: ids, timeoutMs: SHIP_TIMEOUT_MS, describe: describeFirebase });
      }
      return {
        confirm: dx.t("studio.shipUnityConfirm", { slug: slug, template: fields.template, preset: fields.preset, extra: extra }),
        steps: steps,
      };
    }
    if (action === "build") {
      return {
        confirm: dx.t("studio.shipBuildConfirm", {
          slug: slug,
          bundle: fields.bundleId ? dx.t("studio.shipBuildBundle", { id: fields.bundleId }) : dx.t("studio.shipBuildNoBundle"),
        }),
        steps: [{
          path: "/unity/generate-build-scripts",
          body: fields.bundleId ? { bundle_id: fields.bundleId } : {},
          timeoutMs: SHIP_TIMEOUT_MS,
          describe: describeBuild,
        }],
      };
    }
    if (action === "attach") {
      return {
        confirm: dx.t("studio.shipAttachConfirm", { slug: slug, template: fields.template }),
        steps: [{ path: "/unity/attach-template", body: { template_id: fields.template }, timeoutMs: SHIP_UNITY_TIMEOUT_MS, describe: describeAttach }],
      };
    }
    if (action === "firebase") {
      return {
        confirm: dx.t("studio.shipFirebaseConfirm", { slug: slug }),
        steps: [{ path: "/firebase/configure", body: ids, timeoutMs: SHIP_TIMEOUT_MS, describe: describeFirebase }],
      };
    }
    if (action === "hybrid") {
      const steps = [{ path: "/hybrid-mobile/init", body: ids, timeoutMs: SHIP_TIMEOUT_MS, describe: describeHybrid }];
      if (fields.projectId) {
        steps.push({ path: "/firebase/configure", body: ids, timeoutMs: SHIP_TIMEOUT_MS, describe: describeFirebase });
      }
      steps.push({ path: "/capacitor/init", body: Object.assign({}, ids), timeoutMs: SHIP_TIMEOUT_MS, describe: describeCapInit });
      return {
        confirm: dx.t("studio.shipHybridConfirm", {
          slug: slug,
          firebase: fields.projectId ? dx.t("studio.shipHybridFirebase") : dx.t("studio.shipHybridNoFirebase"),
        }),
        steps: steps,
      };
    }
    if (action === "hybrid-export") {
      return {
        confirm: dx.t("studio.shipHybridExportConfirm", { slug: slug }),
        steps: [{ path: "/hybrid-mobile/export", body: null, timeoutMs: SHIP_TIMEOUT_MS, describe: describeHybridExport }],
      };
    }
    if (action === "capacitor") {
      return {
        confirm: dx.t("studio.shipCapConfirm", { slug: slug }),
        steps: [{ path: "/capacitor/init", body: Object.assign({}, ids), timeoutMs: SHIP_TIMEOUT_MS, describe: describeCapInit }],
      };
    }
    return {
      confirm: dx.t("studio.shipCapExportConfirm", { slug: slug }),
      steps: [{ path: "/capacitor/export", body: null, timeoutMs: SHIP_TIMEOUT_MS, describe: describeCapExport }],
    };
  }

  async function runShip(action) {
    if (shipFlight) return;
    const slug = dx.getSlug();
    if (!slug) {
      showStatus("ship-status", dx.t("studio.shipNeedSlug"), true);
      return;
    }
    const plan = shipPlan(action, shipFields());
    if (!await ask("ship", plan.confirm)) return;
    const gen = shipGen;
    const flight = ++shipFlight;
    syncShipButtons();
    const lines = [];
    let bad = false;
    let refresh = false;
    const steps = plan.steps;
    let index = 0;
    const next = () => {
      if (!shipStill(flight, gen, slug)) return Promise.resolve();
      if (index >= steps.length) return Promise.resolve();
      const step = steps[index];
      index += 1;
      const opts = { method: "POST", timeoutMs: step.timeoutMs };
      if (step.body != null) opts.body = JSON.stringify(step.body);
      return dx.api("/admin/daedalus/playground/api/projects/" + slugPath(slug) + step.path, opts).then((data) => {
        if (!shipStill(flight, gen, slug)) return null;
        const view = step.describe(data);
        lines.push(view.text);
        if (view.refresh) refresh = true;
        if (view.bad) {
          bad = true;
          if (index < steps.length) lines.push(dx.t("studio.shipStopped"));
          return null;
        }
        return next();
      }).catch((err) => {
        if (!shipStill(flight, gen, slug)) return null;
        lines.push(shipTimedOut(err) ? dx.t("studio.shipUnknown") : dx.t("studio.requestFailed") + ": " + dx.errText(err));
        bad = true;
        if (index < steps.length) lines.push(dx.t("studio.shipStopped"));
        return null;
      });
    };
    next().then(() => {
      if (!shipStill(flight, gen, slug)) return;
      showStatus("ship-status", lines.join("\n"), bad);
      if (refresh) loadFiles(slug);
    }).finally(() => {
      if (flight === shipFlight) {
        shipFlight = 0;
        syncShipButtons();
      }
    });
  }

  function onSlug(snap) {
    const next = snap.slug || "";
    if (next !== assetSlug) {
      assetSlug = next;
      assetGen += 1;
      assetJobIds = [];
      renderAssetJobs("");
      showStatus("asset-status", "", false);
    }
    if (next !== shipSlug) {
      shipSlug = next;
      shipGen += 1;
      showStatus("ship-status", "", false);
    }
    syncAssetButtons();
    syncShipButtons();
    syncShipVisibility();
    const switched = next !== buildSlug;
    if (switched) {
      buildSlug = next;
      invalidatePendingBuild();
    }
    if (checkpoint && snap.slug !== checkpoint.slug) clearCheckpoint();
    if (streamingSlug && snap.slug !== streamingSlug) closeBuildStream();
    loadFiles(snap.slug);
    syncFilter();
    // Every project-list render (a late game index, a filter click) notifies this watcher. Reload the build panel for
    // another project, an explicit pick (even of the open one), or while this one is not painted yet (an in-flight first
    // load is shared, not repeated). A passive re-render of the project already on screen must not reload it: that
    // reload would clear the status line a click just wrote for it.
    if (currentPanel === "build" && (switched || snap.selected || workspacePainted !== next)) loadWorkspace(snap.slug);
    // Same rule for the preview line: a passive re-render must not replace what a preview action just reported.
    const previewSwitched = next !== previewSlug;
    previewSlug = next;
    if (currentPanel === "preview" && (previewSwitched || snap.selected)) loadPreviewStatus(snap.slug);
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
      await loadWorkspace(data.slug || dx.getSlug(), { force: true });
    });
  });
  $("build-guided").addEventListener("click", () => {
    const slug = dx.getSlug();
    if (!slug) return;
    flight = "guided";
    const ticket = claimExplicitBuild(slug);
    runAction($("build-guided"), "build-status", async () => {
      try {
        const data = await postJson("/admin/daedalus/game-studio/api/projects/" + slugPath(slug) + "/build/guided", {});
        if (!explicitCurrent(ticket, slug)) return;
        if (data.ok === false) throw new Error(data.error || dx.t("studio.requestFailed"));
        if (data.harness_check) {
          showStatus("build-status", dx.t("studio.harnessCheck"), false);
          return;
        }
        showStatus("build-status", dx.t("studio.started"), false);
        if (data.job_id) followBuild("guided", slug, data.job_id);
      } finally {
        finishExplicitBuild(ticket);
      }
    });
  });
  $("build-autopilot").addEventListener("click", () => startAutopilot(false, $("build-autopilot")));
  // resume=true continues the saved plan from its first stage that is not done (the classic page's "resume" start).
  function startAutopilot(resume, btn) {
    const slug = dx.getSlug();
    if (!slug) return;
    const description = ($("build-idea").value || "").trim() || slug;
    flight = "autopilot";
    const ticket = claimExplicitBuild(slug);
    runAction(btn, "build-status", async () => {
      try {
        const data = await postJson("/api/v1/playground/projects/" + slugPath(slug) + "/autopilot", {
          description,
          mode: "full",
          effort: "auto",
          resume: !!resume,
        });
        if (!explicitCurrent(ticket, slug)) return;
        if (data.harness_check) {
          showStatus("build-status", dx.t("studio.harnessCheck"), false);
          return;
        }
        showStatus("build-status", dx.t("studio.started"), false);
        const jobId = data.job && data.job.id;
        if (jobId) followBuild("autopilot", slug, jobId);
        loadControls(slug);
      } finally {
        finishExplicitBuild(ticket);
      }
    });
  }
  $("build-stop").addEventListener("click", () => {
    const slug = dx.getSlug();
    if (!slug) return;
    invalidatePendingBuild();
    const path = flight === "autopilot"
      ? "/admin/daedalus/playground/api/projects/" + slugPath(slug) + "/autopilot/stop"
      : "/admin/daedalus/playground/api/projects/" + slugPath(slug) + "/guided/stop";
    runAction($("build-stop"), "build-status", async () => {
      const data = await postJson(path, {});
      if (data.ok === false) throw new Error(data.error || dx.t("studio.requestFailed"));
      if (dx.getSlug() !== slug) return;
      closeBuildStream();
      $("build-stop").hidden = true;
      showStatus("build-status", dx.t("studio.stopped"), false);
    });
  });
  [["build-waive-unity", "unity_waiver", "studio.waiveUnityConfirm"], ["build-waive-proof", "proof_waiver", "studio.waiveProofConfirm"]].forEach(([id, flag, askKey]) => {
    $(id).addEventListener("click", async () => {
      const slug = dx.getSlug();
      if (!slug || !await askDanger("waive", dx.t(askKey))) return;
      runAction($(id), "build-status", async () => {
        const data = await postJson("/admin/daedalus/game-studio/api/projects/" + slugPath(slug) + "/milestones/advance", { [flag]: true });
        if (data.ok === false) throw new Error(data.error || dx.t("studio.requestFailed"));
        await loadWorkspace(slug, { force: true });        // first: the reload clears an untouched status line
        showStatus("build-status", dx.t("studio.waived", { from: data.previous || "?", to: data.current_milestone || "—" }), false);
      });
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
        await loadWorkspace(slug, { force: true });
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
      if (!data || data.ok !== true) throw new Error((data && data.error) || dx.t("studio.requestFailed"));
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
      if (data && data.harness_check) {
        showStatus("preview-status", dx.t("studio.harnessCheck"), false);
        return;
      }
      if (!data || data.ok !== true) throw new Error((data && data.error) || dx.t("studio.requestFailed"));
      showStatus("preview-status", dx.t("studio.capsuleMeta"), false);
    });
  });

  function publish(suffix, button, body) {
    const slug = dx.getSlug();
    if (!slug) return;
    runAction(button, "preview-status", async () => {
      const url = "/api/v1/playground/projects/" + slugPath(slug) + suffix;
      let data = await postJson(url, body || {});
      const livePublic = !!(data && data.url && data.backend && data.backend !== "local");
      if (suffix === "/live" && data && data.needs_confirm && !livePublic) {
        // Anonymous Netlify / GitLab Pages create a public site: only after the owner confirms.
        if (await askDanger("publicLink", dx.t("studio.confirmPublic"))) {
          data = await postJson(url, Object.assign({}, body || {}, { confirm_public: true }));
        }
      }
      if (data.harness_check) {
        showStatus("preview-status", dx.t("studio.harnessCheck"), false);
        return;
      }
      if (!data || data.ok !== true) throw new Error((data && data.error) || dx.t("studio.notPublished"));
      if (suffix === "/live") {
        const published = !!(data.url && data.backend && data.backend !== "local");
        showStatus("preview-status", dx.t(published ? "studio.liveReady" : "studio.liveLocal"), false);
        return;
      }
      showStatus("preview-status", dx.t("studio.localReady"), false);
    });
  }

  $("git-clone").addEventListener("click", () => {
    const url = ($("git-url").value || "").trim();
    if (!url) { showStatus("git-status", dx.t("studio.gitNeedUrl"), true); return; }
    gitCall($("git-clone"), "/admin/daedalus/playground/api/clone", { url }, false);
  });
  // Revive: a background job (clone → analyse → install and verify → repair → publish) with its steps shown live.
  function paintReviveSteps(job) {
    const list = $("git-revive-steps");
    list.hidden = false;
    list.replaceChildren(...(job.steps || []).map((s) => {
      const li = document.createElement("li");
      li.className = s.ok === true ? "ok" : s.ok === false ? "fail" : "";
      const name = document.createElement("strong");
      name.textContent = s.step;
      const detail = document.createElement("span");
      detail.textContent = s.detail ? " · " + String(s.detail).slice(0, 300) : "";
      li.append(name, detail);
      return li;
    }));
  }
  $("git-revive").addEventListener("click", async () => {
    const url = ($("git-url").value || "").trim();
    if (!url) { showStatus("git-status", dx.t("studio.gitNeedUrl"), true); return; }
    const publish = $("git-revive-publish").value || "local";
    if (publish === "site" && !await ask("reviveSite", dx.t("studio.reviveSiteConfirm"))) return;
    runAction($("git-revive"), "git-status", async () => {
      // The owner just confirmed a public site: that answer travels with the request (review 2026-11-11).
      const body = publish === "site" ? { url, publish, confirm_public: true } : { url, publish };
      const started = await postJson("/api/v1/playground/revive", body);
      let job = started.job;
      showStatus("git-status", dx.t("studio.reviveRunning"), false);
      paintReviveSteps(job);
      while (job.status === "running") {
        await new Promise((r) => setTimeout(r, 2500));
        job = (await dx.api("/api/v1/playground/revive/" + encodeURIComponent(job.id))).job;
        paintReviveSteps(job);
      }
      if (job.slug) dx.selectSlug(job.slug);
      if (job.status !== "done") {
        showStatus("git-status", dx.t("studio.reviveFailed") + ": " + (job.error || ""), true);
        return;
      }
      showStatus("git-status", job.site_url ? dx.t("studio.reviveSite", { url: job.site_url })
        : job.local_url ? dx.t("studio.reviveLocal", { url: job.local_url }) : dx.t("studio.reviveRuns"), false);
    });
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
  $("project-delete").addEventListener("click", async () => {
    const slug = dx.getSlug();
    if (!slug) return;
    if (!await askDanger("deleteProject", dx.t("studio.deleteConfirm"))) return;
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

  document.addEventListener("daedalus:langchange", paintChip);
  document.addEventListener("daedalus:langchange", () => {
    renderAssetJobs(null);
    if (currentPanel === "review") {
      loadReview(currentTab);
      paintSkill();
      paintRef();
    }
    if (currentPanel === "settings") loadSettings();
    if (currentPanel === "build") loadWorkspace(dx.getSlug());
    loadFiles(dx.getSlug());
  });
  window.matchMedia("(max-width: 1100px)").addEventListener("change", () => {
    if (currentPanel === "world" || currentPanel === "tools") showPanel(currentPanel, currentTab);
  });

  ["build-cp-notes", "flight-cp-notes"].forEach((id) => {
    const el = $(id);
    if (!el) return;
    el.addEventListener("input", () => syncCheckpointNotes(el));
  });

  $("asset-generate").addEventListener("click", () => {
    const path = ($("asset-path").value || "").trim() || "assets/custom.png";
    const prompt = ($("asset-prompt").value || "").trim();
    const size = $("asset-size").value || "512x512";
    startAsset("generate", "/assets/generate", { path, prompt, size }, ASSET_GENERATE_TIMEOUT_MS, describeGenerate);
  });
  $("asset-autopack").addEventListener("click", () => {
    const description = ($("asset-desc").value || "").trim();
    startAsset("autopack", "/assets/auto-pack", { description }, ASSET_AUTOPACK_TIMEOUT_MS, describeAutopack);
  });
  $("asset-action").addEventListener("change", syncAssetFields);
  $("asset-pipeline").addEventListener("click", () => {
    const action = $("asset-action").value || "status";
    const body = { action };
    if (action === "mesh") {
      body.prompt = ($("asset-mesh-prompt").value || "").trim();
      const name = ($("asset-mesh-name").value || "").trim();
      if (name) body.name = name;
      body.provider = ($("asset-mesh-provider").value || "auto").trim() || "auto";
    }
    if (action === "status") {
      const jobId = ($("asset-job").value || "").trim();
      if (jobId) body.job_id = jobId;
    }
    startAsset("pipeline", "/assets/pipeline", body, ASSET_PIPELINE_TIMEOUT_MS, (data) => describePipeline(action, data, body));
  });
  syncAssetFields();
  renderAssetJobs("");
  if ($("ship-preset")) $("ship-preset").addEventListener("change", syncShipVisibility);
  if ($("ship-template")) $("ship-template").addEventListener("change", syncShipVisibility);
  [
    ["ship-unity-init", "unity"],
    ["ship-unity-build", "build"],
    ["ship-unity-attach", "attach"],
    ["ship-firebase", "firebase"],
    ["ship-hybrid-init", "hybrid"],
    ["ship-hybrid-export", "hybrid-export"],
    ["ship-cap-init", "capacitor"],
    ["ship-cap-export", "capacitor-export"],
  ].forEach((pair) => {
    const btn = $(pair[0]);
    if (btn) btn.addEventListener("click", () => runShip(pair[1]));
  });
  syncShipButtons();
  syncShipVisibility();
  const blockerRefresh = $("blocker-refresh");
  if (blockerRefresh) blockerRefresh.addEventListener("click", () => loadBlockers());
  const blockerFixAll = $("blocker-autofix-all");
  if (blockerFixAll) blockerFixAll.addEventListener("click", () => blockersAutoFixAll(blockerFixAll));
  const blockerGrantRec = $("blocker-grant-rec");
  if (blockerGrantRec) blockerGrantRec.addEventListener("click", () => blockersGrantRecommended(blockerGrantRec));
  const blockerHours = $("blocker-hours");
  if (blockerHours) fillHours(blockerHours, 8);
  const planGenerate = $("plan-generate");
  if (planGenerate) planGenerate.addEventListener("click", generatePlan);
  const planRefresh = $("plan-refresh");
  if (planRefresh) planRefresh.addEventListener("click", () => loadPlans());
  const planManualCreate = $("plan-manual-create");
  if (planManualCreate) {
    planManualCreate.dataset.extra = "1";
    planManualCreate.addEventListener("click", () => createManualPlan(planManualCreate));
  }
  const discoveryRefresh = $("discovery-refresh");
  if (discoveryRefresh) discoveryRefresh.addEventListener("click", () => loadDiscoveries());
  const discoveryFilter = $("discovery-filter");
  if (discoveryFilter) discoveryFilter.addEventListener("change", () => loadDiscoveries());
  const skillStart = $("skill-start");
  if (skillStart) skillStart.addEventListener("click", startSkill);
  const skillRefine = $("skill-refine");
  if (skillRefine) skillRefine.addEventListener("click", refineSkill);
  const skillGenerate = $("skill-generate");
  if (skillGenerate) skillGenerate.addEventListener("click", () => generateSkill(false));
  const skillForce = $("skill-force");
  if (skillForce) skillForce.addEventListener("click", () => generateSkill(true));
  const skillDownload = $("skill-download");
  if (skillDownload) skillDownload.addEventListener("click", downloadSkillMd);
  const skillBundle = $("skill-bundle");
  if (skillBundle) skillBundle.addEventListener("click", downloadSkillBundle);
  const refScout = $("ref-scout");
  if (refScout) refScout.addEventListener("click", scoutReference);
  syncSkillButtons();
  syncRefButtons();

  $("team-invite").addEventListener("submit", (ev) => {
    ev.preventDefault();
    const name = ($("team-name").value || "").trim();
    if (!name) return;
    runAction(ev.submitter, "team-status", async () => {
      const res = await postJson("/api/v1/team/members", { name, role: $("team-role").value });
      $("team-name").value = "";
      $("team-token-value").textContent = res.token || "";
      $("team-trust").textContent = res.trust || "";
      $("team-token").hidden = !res.token;            // shown once: loadTeam hides it on any later visit
      showStatus("team-status", dx.t("studio.teamInvited", { name: res.member ? res.member.name : name }), false);
      refreshTeamLists();
    });
  });
  $("team-token-copy").addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText($("team-token-value").textContent || "");
      showStatus("team-status", dx.t("studio.teamCopied"), false);
    } catch (err) {
      showStatus("team-status", dx.t("studio.teamCopyFailed"), true);
    }
  });
  $("team-key-form").addEventListener("submit", (ev) => {
    ev.preventDefault();
    const value = ($("team-key-value").value || "").trim();
    if (value) saveMyKey(ev.submitter, value);
  });
  $("team-key-remove").addEventListener("click", (ev) => saveMyKey(ev.currentTarget, ""));
  $("team-signout").addEventListener("click", () => {
    if (window.DaedalusAuth && window.DaedalusAuth.signOut) window.DaedalusAuth.signOut();
    location.reload();
  });

  const opening = readQuery();
  loadMe();
  showPanel(opening.panel, opening.tab);
  dx.watchSlug(onSlug);
  loadCatalog();
  loadGameIndex();
})();
