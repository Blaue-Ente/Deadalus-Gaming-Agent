(() => {
  const byId = (id) => document.getElementById(id);
  const template = byId("llm-provider-template");

  function showMessage(message, ok = false) {
    const box = byId("llm-message");
    box.textContent = message;
    box.classList.remove("hidden");
    box.classList.toggle("is-ok", ok);
  }

  function option(value, label) {
    const node = document.createElement("option");
    node.value = value;
    node.textContent = label;
    return node;
  }

  async function selectResource(resource, card) {
    const select = card.querySelector(".provider-model");
    const customInput = card.querySelector(".provider-model-input");
    const model = select.value === "__custom__" ? customInput.value.trim() : select.value;
    const baseUrl = card.querySelector(".provider-url").value.trim();
    const apiKey = card.querySelector(".provider-key").value.trim();
    if (!model) {
      showMessage("Избери модел или въведи Model ID.");
      customInput.focus();
      return;
    }
    const button = card.querySelector(".provider-select");
    button.disabled = true;
    button.textContent = "Записвам…";
    try {
      const response = await fetch("/admin/llm-router/api/selection", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({
          provider: resource.id,
          model,
          base_url: baseUrl,
          api_key: apiKey,
        }),
      });
      const data = await response.json();
      if (!response.ok || !data.ok) {
        throw new Error(data.detail || data.error || "Provider selection failed");
      }
      showMessage(`Активен модел: ${resource.label} · ${model}`, true);
      await loadResources(false);
    } catch (error) {
      showMessage(error.message || String(error));
    } finally {
      button.disabled = false;
      button.textContent = "Използвай";
    }
  }

  function renderResource(resource) {
    const card = template.content.firstElementChild.cloneNode(true);
    card.dataset.provider = resource.id;
    card.querySelector(".provider-label").textContent = resource.label;
    card.querySelector(".provider-meta").textContent =
      `${resource.kind} · ${resource.base_url || "URL не е зададен"}`;

    const status = card.querySelector(".provider-status");
    status.textContent = resource.status.replaceAll("_", " ");
    status.classList.add(resource.configured ? "is-ready" : "is-missing");

    const modelSelect = card.querySelector(".provider-model");
    const models = resource.models || [];
    models.forEach((model) => modelSelect.appendChild(option(model.id, model.label || model.id)));
    modelSelect.appendChild(option("__custom__", "Друг Model ID…"));

    const customWrap = card.querySelector(".provider-model-custom");
    const customInput = card.querySelector(".provider-model-input");
    if (!models.length || resource.id === "custom") {
      modelSelect.value = "__custom__";
      customWrap.classList.remove("hidden");
    }
    modelSelect.addEventListener("change", () => {
      customWrap.classList.toggle("hidden", modelSelect.value !== "__custom__");
      if (modelSelect.value === "__custom__") customInput.focus();
    });

    const urlWrap = card.querySelector(".provider-url-wrap");
    const urlInput = card.querySelector(".provider-url");
    urlInput.value = resource.base_url || "";
    if (resource.kind === "custom") urlWrap.classList.remove("hidden");

    const keyWrap = card.querySelector(".provider-key-wrap");
    if (resource.auth === "none") keyWrap.classList.add("hidden");

    card.querySelector(".provider-select").addEventListener(
      "click",
      () => selectResource(resource, card),
    );
    return card;
  }

  async function loadResources(probeLocal = true) {
    const suffix = probeLocal ? "" : "?probe_local=false";
    try {
      const response = await fetch("/api/v1/llm/resources" + suffix);
      const data = await response.json();
      if (!response.ok || !data.ok) throw new Error(data.error || "Неуспешно зареждане");

      const effective = data.effective || {};
      byId("llm-active").textContent =
        `${effective.provider || "—"} · ${effective.model || "—"}`;
      byId("llm-source").textContent = `source: ${effective.source || "—"}`;

      const local = byId("llm-local");
      const ready = byId("llm-cloud-ready");
      const missing = byId("llm-cloud-missing");
      local.replaceChildren();
      ready.replaceChildren();
      missing.replaceChildren();
      (data.resources || []).forEach((resource) => {
        const card = renderResource(resource);
        if (resource.kind === "local") local.appendChild(card);
        else if (resource.configured) ready.appendChild(card);
        else missing.appendChild(card);
      });
      if (!ready.children.length) {
        const empty = document.createElement("p");
        empty.className = "text-sm text-argos-dim";
        empty.textContent = "Няма конфигуриран cloud provider.";
        ready.appendChild(empty);
      }
    } catch (error) {
      showMessage(error.message || String(error));
    }
  }

  byId("llm-refresh").addEventListener("click", () => loadResources(true));
  loadResources(true);
})();
