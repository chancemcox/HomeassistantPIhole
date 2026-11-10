const state = {
  filter: "deny/exact",
};

const els = {
  badge: document.getElementById("blocking-badge"),
  total: document.getElementById("metric-total"),
  blocked: document.getElementById("metric-blocked"),
  percent: document.getElementById("metric-percent"),
  clients: document.getElementById("metric-clients"),
  domainList: document.getElementById("domain-list"),
  recentList: document.getElementById("recent-list"),
  topList: document.getElementById("top-list"),
  dnsIps: document.getElementById("dns-ips"),
  dnsExamples: document.getElementById("dns-examples"),
  versionLine: document.getElementById("version-line"),
  actionLog: document.getElementById("action-log"),
  domainForm: document.getElementById("domain-form"),
  domainInput: document.getElementById("domain-input"),
  domainType: document.getElementById("domain-type"),
};

function fmt(n) {
  if (n === null || n === undefined || Number.isNaN(n)) return "—";
  return new Intl.NumberFormat().format(n);
}

async function api(path, options = {}) {
  console.debug("[pihole-ui] request", path, options.method || "GET");
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  const text = await response.text();
  let data = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = { raw: text };
  }
  if (!response.ok) {
    const message = data?.error?.message || data?.error || data?.raw || response.statusText;
    console.error("[pihole-ui] request failed", path, response.status, message);
    throw new Error(message);
  }
  return data;
}

function setBlockingBadge(blocking) {
  const enabled = blocking?.blocking === true || blocking?.blocking === "enabled";
  const timer = blocking?.timer;
  els.badge.classList.remove("is-loading", "is-on", "is-off");
  if (enabled) {
    els.badge.classList.add("is-on");
    els.badge.textContent = timer ? `Blocking · resumes in ${timer}s` : "Blocking enabled";
  } else {
    els.badge.classList.add("is-off");
    els.badge.textContent = timer ? `Paused · ${timer}s left` : "Blocking disabled";
  }
}

function renderList(target, items, emptyText) {
  target.innerHTML = "";
  if (!items?.length) {
    const li = document.createElement("li");
    li.className = "empty";
    li.textContent = emptyText;
    target.appendChild(li);
    return;
  }
  for (const item of items) {
    const li = document.createElement("li");
    if (typeof item === "string") {
      li.textContent = item;
    } else if (item.domain && item.count !== undefined) {
      const domain = document.createElement("span");
      domain.textContent = item.domain;
      const count = document.createElement("span");
      count.textContent = fmt(item.count);
      li.append(domain, count);
    } else if (item.domain) {
      const label = document.createElement("span");
      label.textContent = item.domain;
      const remove = document.createElement("button");
      remove.type = "button";
      remove.textContent = "Remove";
      remove.addEventListener("click", () => {
        removeDomain(item).catch((error) => showLog(`Remove failed: ${error.message}`));
      });
      li.append(label, remove);
    }
    target.appendChild(li);
  }
}

function renderDnsExamples(examples) {
  els.dnsExamples.innerHTML = "";
  if (!examples?.length) {
    const empty = document.createElement("p");
    empty.className = "empty";
    empty.textContent = "DNS examples unavailable.";
    els.dnsExamples.appendChild(empty);
    return;
  }

  for (const example of examples) {
    const details = document.createElement("details");
    details.className = "dns-example";
    const summary = document.createElement("summary");
    summary.textContent = example.title || "Example";
    const body = document.createElement("p");
    body.textContent = example.steps || "";
    details.append(summary, body);
    els.dnsExamples.appendChild(details);
  }
}

async function removeDomain(item) {
  const type = item.type || (state.filter.startsWith("allow") ? "allow" : "deny");
  const kind = item.kind || "exact";
  const encoded = encodeURIComponent(item.domain);
  await api(`/api/domains/${type}/${kind}/${encoded}`, { method: "DELETE" });
  await refreshDomains();
}

async function refreshOverview() {
  const data = await api("/api/overview");
  const queries = data.summary?.queries || {};
  const clients = data.summary?.clients || {};
  els.total.textContent = fmt(queries.total);
  els.blocked.textContent = fmt(queries.blocked);
  els.percent.textContent =
    queries.percent_blocked === undefined ? "—" : `${Number(queries.percent_blocked).toFixed(1)}%`;
  els.clients.textContent = fmt(clients.active ?? clients.total);
  setBlockingBadge(data.blocking);

  const recent = data.recent_blocked?.blocked || [];
  renderList(els.recentList, recent, "No recent blocks yet.");

  const top = data.top_blocked?.domains || [];
  renderList(els.topList, top, "No blocked query data yet.");

  const version = data.version?.version || data.version;
  if (version) {
    const core = version.core?.local?.version || version.core || "—";
    const ftl = version.ftl?.local?.version || version.ftl || "—";
    const web = version.web?.local?.version || version.web || "—";
    els.versionLine.textContent = `Core ${core} · FTL ${ftl} · Web ${web}`;
  }
}

async function refreshDomains() {
  const data = await api(`/api/domains?filter=${encodeURIComponent(state.filter)}`);
  renderList(els.domainList, data.domains || [], "No domains on this list.");
}

async function refreshSetup() {
  const data = await api("/api/setup");
  els.dnsIps.innerHTML = "";
  const ips = data.dns_ips?.length ? data.dns_ips : ["Set a static IP on this host"];
  for (const ip of ips) {
    const chip = document.createElement("div");
    chip.className = "dns-chip";
    chip.textContent = `${ip}:${data.dns_port || 53}`;
    els.dnsIps.appendChild(chip);
  }
  renderDnsExamples(data.examples || []);
}

async function refreshPresets() {
  const notes = document.getElementById("hulu-notes");
  const desc = document.getElementById("hulu-preset-desc");
  if (!notes) return;
  try {
    const data = await api("/api/presets/hulu-ads");
    if (desc && data.description) desc.textContent = data.description;
    notes.innerHTML = "";
    for (const note of data.notes || []) {
      const li = document.createElement("li");
      li.textContent = note;
      notes.appendChild(li);
    }
  } catch (error) {
    notes.innerHTML = "";
    const li = document.createElement("li");
    li.textContent = `Could not load Hulu preset details: ${error.message}`;
    notes.appendChild(li);
  }
}

async function setBlocking(blocking, timer) {
  try {
    const body = { blocking };
    if (timer !== undefined) body.timer = timer;
    await api("/api/blocking", { method: "POST", body: JSON.stringify(body) });
    await refreshOverview();
  } catch (error) {
    showLog(`Blocking change failed: ${error.message}`);
  }
}

function showLog(text) {
  els.actionLog.hidden = false;
  els.actionLog.textContent = text;
}

document.getElementById("btn-enable").addEventListener("click", () => setBlocking(true));
document.getElementById("btn-disable").addEventListener("click", () => setBlocking(false));
document.getElementById("btn-pause").addEventListener("click", () => setBlocking(false, 300));
document.getElementById("btn-refresh").addEventListener("click", () => refreshAll());

document.getElementById("btn-hulu-ads").addEventListener("click", async () => {
  const button = document.getElementById("btn-hulu-ads");
  button.disabled = true;
  showLog("Applying Hulu ads deny-list preset…");
  try {
    const result = await api("/api/presets/hulu-ads/apply", {
      method: "POST",
      body: "{}",
    });
    const added = result.added?.length || 0;
    const skipped = result.skipped?.length || 0;
    const failed = result.failed?.length || 0;
    showLog(
      `Hulu preset finished. Added ${added}, already present ${skipped}, failed ${failed}.\n` +
        (result.notes || []).join("\n"),
    );
    state.filter = "deny/exact";
    document.querySelectorAll(".tab").forEach((tab) => {
      tab.classList.toggle("is-active", tab.dataset.filter === state.filter);
    });
    await refreshDomains();
    await refreshOverview();
  } catch (error) {
    showLog(`Hulu preset failed: ${error.message}`);
  } finally {
    button.disabled = false;
  }
});

document.getElementById("btn-gravity").addEventListener("click", async () => {
  showLog("Updating gravity lists…");
  try {
    const result = await api("/api/gravity", { method: "POST", body: "{}" });
    showLog(result.output || JSON.stringify(result, null, 2));
    await refreshOverview();
  } catch (error) {
    showLog(`Gravity update failed: ${error.message}`);
  }
});

document.getElementById("btn-flush").addEventListener("click", async () => {
  try {
    await api("/api/flush-logs", { method: "POST", body: "{}" });
    showLog("Query log flushed.");
    await refreshOverview();
  } catch (error) {
    showLog(`Flush failed: ${error.message}`);
  }
});

els.domainForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const domain = els.domainInput.value.trim();
  const type = els.domainType.value;
  if (!domain) return;
  try {
    await api("/api/domains", {
      method: "POST",
      body: JSON.stringify({ domain, type, kind: "exact" }),
    });
    els.domainInput.value = "";
    state.filter = `${type}/exact`;
    document.querySelectorAll(".tab").forEach((tab) => {
      tab.classList.toggle("is-active", tab.dataset.filter === state.filter);
    });
    await refreshDomains();
  } catch (error) {
    showLog(`Add domain failed: ${error.message}`);
  }
});

document.querySelectorAll(".tab").forEach((tab) => {
  tab.addEventListener("click", async () => {
    state.filter = tab.dataset.filter;
    document.querySelectorAll(".tab").forEach((node) => node.classList.remove("is-active"));
    tab.classList.add("is-active");
    try {
      await refreshDomains();
    } catch (error) {
      showLog(`Load domains failed: ${error.message}`);
    }
  });
});

async function refreshAll() {
  try {
    await Promise.all([refreshOverview(), refreshDomains(), refreshSetup(), refreshPresets()]);
  } catch (error) {
    els.badge.classList.remove("is-on", "is-off");
    els.badge.classList.add("is-loading");
    els.badge.textContent = `Waiting for Pi-hole… (${error.message})`;
    showLog(`Refresh failed: ${error.message}`);
  }
}

refreshAll();
setInterval(refreshAll, 15000);
