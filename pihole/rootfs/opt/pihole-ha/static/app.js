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
  adminUrls: document.getElementById("admin-urls"),
  dnsExamples: document.getElementById("dns-examples"),
  versionLine: document.getElementById("version-line"),
  actionLog: document.getElementById("action-log"),
  domainForm: document.getElementById("domain-form"),
  domainInput: document.getElementById("domain-input"),
  domainType: document.getElementById("domain-type"),
  trafficTotal: document.getElementById("traffic-total"),
  trafficBlocked: document.getElementById("traffic-blocked"),
  trafficCached: document.getElementById("traffic-cached"),
  trafficForwarded: document.getElementById("traffic-forwarded"),
  trafficChart: document.getElementById("traffic-chart"),
  trafficEmpty: document.getElementById("traffic-empty"),
  queryLog: document.getElementById("query-log"),
  topClients: document.getElementById("top-clients"),
};

function fmt(n) {
  if (n === null || n === undefined || Number.isNaN(n)) return "—";
  return new Intl.NumberFormat().format(n);
}

// Home Assistant Ingress serves this UI under /api/hassio_ingress/<token>/.
// Absolute "/api/..." and "/static/..." URLs escape that prefix and break CSS/JS/API.
const UI_BASE = (() => {
  const path = window.location.pathname || "/";
  if (path.endsWith("/")) return path;
  const idx = path.lastIndexOf("/");
  return idx >= 0 ? path.slice(0, idx + 1) : "/";
})();

function uiUrl(path) {
  return `${UI_BASE}${String(path || "").replace(/^\//, "")}`;
}

async function api(path, options = {}) {
  const url = uiUrl(path);
  console.debug("[pihole-ui] request", url, options.method || "GET");
  const response = await fetch(url, {
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
    console.error("[pihole-ui] request failed", url, response.status, message);
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
  const dnsPort = data.dns_port || 53;
  const adminPort = data.admin_port || 8088;

  els.dnsIps.innerHTML = "";
  const ips = data.dns_ips?.length ? data.dns_ips : ["Set a static IP on this host"];
  for (const ip of ips) {
    const chip = document.createElement("div");
    chip.className = "dns-chip";
    chip.textContent = `${ip}:${dnsPort}`;
    chip.title = "DNS — use this address on port 53";
    els.dnsIps.appendChild(chip);
  }

  if (els.adminUrls) {
    els.adminUrls.innerHTML = "";
    const adminUrls =
      data.admin_urls?.length > 0
        ? data.admin_urls
        : ips
            .filter((ip) => !String(ip).includes(" "))
            .map((ip) => `http://${ip}:${adminPort}/admin`);
    if (!adminUrls.length) {
      const chip = document.createElement("div");
      chip.className = "dns-chip";
      chip.textContent = `host:${adminPort}/admin`;
      els.adminUrls.appendChild(chip);
    } else {
      for (const url of adminUrls) {
        const link = document.createElement("a");
        link.className = "dns-chip admin-chip";
        link.href = url;
        link.target = "_blank";
        link.rel = "noopener noreferrer";
        link.textContent = url.replace(/^https?:\/\//, "");
        link.title = `Native Pi-hole web UI on port ${adminPort}`;
        els.adminUrls.appendChild(link);
      }
    }
  }

  renderDnsExamples(data.examples || []);
}

function buildPolyline(points, width, height, pad) {
  if (!points.length) return "";
  const max = Math.max(...points, 1);
  return points
    .map((value, index) => {
      const x = pad + (index / Math.max(points.length - 1, 1)) * (width - pad * 2);
      const y = height - pad - (value / max) * (height - pad * 2);
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");
}

function renderTrafficChart(history) {
  const svg = els.trafficChart;
  const empty = els.trafficEmpty;
  svg.innerHTML = "";
  const rows = (history || []).filter((row) => row && typeof row.total === "number");
  if (!rows.length) {
    empty.hidden = false;
    return;
  }
  empty.hidden = true;

  const width = 1000;
  const height = 280;
  const pad = 24;
  const totals = rows.map((row) => Number(row.total) || 0);
  const blocked = rows.map((row) => Number(row.blocked) || 0);
  const forwarded = rows.map((row) => Number(row.forwarded) || 0);
  const max = Math.max(...totals, 1);

  const grid = document.createElementNS("http://www.w3.org/2000/svg", "g");
  for (let i = 0; i <= 4; i += 1) {
    const y = pad + ((height - pad * 2) * i) / 4;
    const line = document.createElementNS("http://www.w3.org/2000/svg", "line");
    line.setAttribute("x1", String(pad));
    line.setAttribute("x2", String(width - pad));
    line.setAttribute("y1", String(y));
    line.setAttribute("y2", String(y));
    line.setAttribute("stroke", "rgba(33, 33, 33, 0.12)");
    if (window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches) {
      line.setAttribute("stroke", "rgba(225, 225, 225, 0.12)");
    }
    grid.appendChild(line);
    const label = document.createElementNS("http://www.w3.org/2000/svg", "text");
    label.textContent = fmt(Math.round(max - (max * i) / 4));
    label.setAttribute("x", "8");
    label.setAttribute("y", String(y + 4));
    label.setAttribute("fill", "#727272");
    if (window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches) {
      label.setAttribute("fill", "#9b9b9b");
    }
    label.setAttribute("font-size", "12");
    label.setAttribute("font-family", "Roboto Mono, monospace");
    grid.appendChild(label);
  }
  svg.appendChild(grid);

  // Home Assistant primary / error / accent
  const series = [
    { values: totals, color: "#009ac7", width: 2.5 },
    { values: blocked, color: "#db4437", width: 2 },
    { values: forwarded, color: "#ff9800", width: 2 },
  ];
  for (const item of series) {
    const polyline = document.createElementNS("http://www.w3.org/2000/svg", "polyline");
    polyline.setAttribute("fill", "none");
    polyline.setAttribute("stroke", item.color);
    polyline.setAttribute("stroke-width", String(item.width));
    polyline.setAttribute("stroke-linejoin", "round");
    polyline.setAttribute("stroke-linecap", "round");
    polyline.setAttribute("points", buildPolyline(item.values, width, height, pad));
    svg.appendChild(polyline);
  }
}

function queryStatusLabel(entry) {
  const status = String(entry.status || entry.status_id || "").toLowerCase();
  if (
    status.includes("block") ||
    status.includes("gravity") ||
    status.includes("denylist") ||
    status.includes("regex")
  ) {
    return { label: "Blocked", blocked: true };
  }
  if (entry.blocked === true) return { label: "Blocked", blocked: true };
  return { label: entry.status || entry.reply || "OK", blocked: false };
}

function renderQueryLog(queries) {
  els.queryLog.innerHTML = "";
  if (!queries?.length) {
    const row = document.createElement("tr");
    const cell = document.createElement("td");
    cell.colSpan = 4;
    cell.className = "empty";
    cell.textContent = "No recent queries yet.";
    row.appendChild(cell);
    els.queryLog.appendChild(row);
    return;
  }

  for (const entry of queries.slice(0, 40)) {
    const row = document.createElement("tr");
    const time = document.createElement("td");
    const ts = entry.time || entry.timestamp;
    time.textContent = ts
      ? new Date((Number(ts) > 1e12 ? Number(ts) : Number(ts) * 1000)).toLocaleTimeString()
      : "—";

    const domain = document.createElement("td");
    domain.className = "domain";
    domain.textContent = entry.domain || "—";

    const client = document.createElement("td");
    client.textContent = entry.client?.name || entry.client?.ip || entry.client || "—";

    const result = document.createElement("td");
    const info = queryStatusLabel(entry);
    const badge = document.createElement("span");
    badge.className = `badge-result ${info.blocked ? "is-blocked" : "is-ok"}`;
    badge.textContent = info.label;
    result.appendChild(badge);

    row.append(time, domain, client, result);
    els.queryLog.appendChild(row);
  }
}

async function refreshTraffic() {
  const data = await api("/api/traffic?length=40");
  const totals = data.totals_24h || {};
  els.trafficTotal.textContent = fmt(totals.total);
  els.trafficBlocked.textContent = fmt(totals.blocked);
  els.trafficCached.textContent = fmt(totals.cached);
  els.trafficForwarded.textContent = fmt(totals.forwarded);
  renderTrafficChart(data.history || []);
  renderQueryLog(data.queries || []);

  const clients = data.top_clients?.clients || [];
  renderList(
    els.topClients,
    clients.map((client) => ({
      domain: client.name || client.ip || "unknown",
      count: client.count,
    })),
    "No client traffic yet.",
  );
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
    await Promise.all([
      refreshOverview(),
      refreshTraffic(),
      refreshDomains(),
      refreshSetup(),
      refreshPresets(),
    ]);
  } catch (error) {
    els.badge.classList.remove("is-on", "is-off");
    els.badge.classList.add("is-loading");
    els.badge.textContent = `Waiting for Pi-hole… (${error.message})`;
    showLog(`Refresh failed: ${error.message}`);
  }
}

refreshAll();
setInterval(refreshAll, 10000);
