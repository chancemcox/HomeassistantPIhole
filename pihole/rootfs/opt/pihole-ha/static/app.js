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
      li.innerHTML = `<span>${item.domain}</span><span>${fmt(item.count)}</span>`;
    } else if (item.domain) {
      const label = document.createElement("span");
      label.textContent = item.domain;
      const remove = document.createElement("button");
      remove.type = "button";
      remove.textContent = "Remove";
      remove.addEventListener("click", () => removeDomain(item));
      li.append(label, remove);
    }
    target.appendChild(li);
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
}

async function setBlocking(blocking, timer) {
  const body = { blocking };
  if (timer !== undefined) body.timer = timer;
  await api("/api/blocking", { method: "POST", body: JSON.stringify(body) });
  await refreshOverview();
}

function showLog(text) {
  els.actionLog.hidden = false;
  els.actionLog.textContent = text;
}

document.getElementById("btn-enable").addEventListener("click", () => setBlocking(true));
document.getElementById("btn-disable").addEventListener("click", () => setBlocking(false));
document.getElementById("btn-pause").addEventListener("click", () => setBlocking(false, 300));
document.getElementById("btn-refresh").addEventListener("click", () => refreshAll());

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
});

document.querySelectorAll(".tab").forEach((tab) => {
  tab.addEventListener("click", async () => {
    state.filter = tab.dataset.filter;
    document.querySelectorAll(".tab").forEach((node) => node.classList.remove("is-active"));
    tab.classList.add("is-active");
    await refreshDomains();
  });
});

async function refreshAll() {
  try {
    await Promise.all([refreshOverview(), refreshDomains(), refreshSetup()]);
  } catch (error) {
    els.badge.classList.remove("is-on", "is-off");
    els.badge.classList.add("is-loading");
    els.badge.textContent = `Waiting for Pi-hole… (${error.message})`;
  }
}

refreshAll();
setInterval(refreshAll, 15000);
