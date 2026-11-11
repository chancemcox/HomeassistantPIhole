#!/usr/bin/env bash
# CI smoke: compile helpers, resolve ingress port, and prove the management page HTML shows.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT}"

python3 -m py_compile \
  pihole/rootfs/opt/pihole-ha/server.py \
  pihole/rootfs/opt/pihole-ha/presets.py \
  pihole/rootfs/opt/pihole-ha/resolve_ingress_port.py \
  pihole/rootfs/opt/pihole-ha/port53_plan.py

python3 <<'PY'
import importlib.util
import json
import os
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(".").resolve()
store = {"domains": []}


class FakePiHole(BaseHTTPRequestHandler):
    def log_message(self, *args):
        return

    def _json(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length)
        body = json.loads(raw.decode() or "{}")
        if self.path == "/api/auth":
            return self._json(200, {"session": {"valid": True, "sid": "test-sid", "validity": 1800}})
        if self.path.startswith("/api/domains/deny/"):
            kind = self.path.rsplit("/", 1)[-1]
            domain = body.get("domain")
            store["domains"].append({"domain": domain, "kind": kind, "type": "deny"})
            return self._json(201, {"domains": [store["domains"][-1]]})
        if self.path == "/api/dns/blocking":
            # Simulate FTL when host port 53 is taken (real user failure mode).
            return self._json(
                500,
                {
                    "error": {
                        "key": "dns_failure",
                        "message": "DNS resolver is not running",
                        "hint": None,
                    }
                },
            )
        return self._json(200, {"ok": True})

    def do_GET(self):
        if self.path.startswith("/api/stats/summary"):
            return self._json(
                200,
                {
                    "queries": {"total": 10, "blocked": 4, "percent_blocked": 40.0},
                    "clients": {"active": 2, "total": 3},
                },
            )
        if self.path.startswith("/api/dns/blocking"):
            return self._json(200, {"blocking": True, "timer": None})
        if self.path.startswith("/api/info/version"):
            return self._json(200, {"version": {}})
        if self.path.startswith("/api/stats/"):
            return self._json(200, {"domains": [], "blocked": []})
        if self.path.startswith("/api/domains"):
            return self._json(200, {"domains": store["domains"]})
        return self._json(404, {"error": "missing"})


class FakeSupervisor(BaseHTTPRequestHandler):
    def log_message(self, *args):
        return

    def do_GET(self):
        if self.path == "/addons/self/info":
            auth = self.headers.get("Authorization", "")
            if auth != "Bearer test-token":
                body = b'{"result":"error"}'
                self.send_response(401)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            body = json.dumps({"data": {"ingress_port": 62451, "ingress": True}}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path == "/addons":
            body = json.dumps(
                {
                    "data": {
                        "addons": [
                            {
                                "slug": "a0d7b954_adguard",
                                "name": "AdGuard Home",
                                "state": "started",
                                "network": {"53/udp": 53, "53/tcp": 53},
                            },
                            {
                                "slug": "pihole",
                                "name": "Pi-hole",
                                "state": "started",
                                "network": {"53/udp": 53},
                            },
                        ]
                    }
                }
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(404)
        self.end_headers()


fake = HTTPServer(("127.0.0.1", 18080), FakePiHole)
threading.Thread(target=fake.serve_forever, daemon=True).start()

supervisor = HTTPServer(("127.0.0.1", 18081), FakeSupervisor)
threading.Thread(target=supervisor.serve_forever, daemon=True).start()

# --- Ingress port resolver (must prefer Supervisor-assigned port, never silent 8099) ---
spec_resolve = importlib.util.spec_from_file_location(
    "resolve_ingress_port",
    ROOT / "pihole/rootfs/opt/pihole-ha/resolve_ingress_port.py",
)
resolve_mod = importlib.util.module_from_spec(spec_resolve)
assert spec_resolve.loader is not None
spec_resolve.loader.exec_module(resolve_mod)

resolved = resolve_mod.resolve_ingress_port(
    token="test-token",
    bases=["http://127.0.0.1:18081"],
)
assert resolved == 62451, resolved
print(f"Ingress port resolver OK -> {resolved}")

# --- Management UI page must render ---
os.environ.update(
    PIHOLE_API_BASE="http://127.0.0.1:18080",
    PIHOLE_HA_PASSWORD="secret",
    PIHOLE_HA_UI_PORT="18099",
    PIHOLE_HA_LOG_LEVEL="warning",
    PIHOLE_HA_BLOCK_HULU_ADS="false",
    SUPERVISOR_TOKEN="test-token",
    SUPERVISOR_API_BASE="http://127.0.0.1:18081",
)

spec = importlib.util.spec_from_file_location(
    "server",
    ROOT / "pihole/rootfs/opt/pihole-ha/server.py",
)
mod = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(mod)
mod.wait_for_pihole = lambda timeout=120: True
threading.Thread(target=mod.main, daemon=True).start()

deadline = time.time() + 10
last_error = None
while time.time() < deadline:
    try:
        with urllib.request.urlopen("http://127.0.0.1:18099/api/health", timeout=2) as response:
            health = json.load(response)
            if health.get("ok") is True:
                break
            last_error = health
    except Exception as exc:  # noqa: BLE001
        last_error = exc
        time.sleep(0.2)
else:
    raise AssertionError(f"UI health never became ready: {last_error}")

with urllib.request.urlopen("http://127.0.0.1:18099/api/setup", timeout=5) as response:
    setup = json.load(response)
    assert setup.get("dns_port") == 53, setup
    assert setup.get("admin_port") == 18080, setup
    assert isinstance(setup.get("admin_urls"), list), setup

# Prove the actual page shows (this is what Ingress users see; empty/502 must fail CI).
with urllib.request.urlopen("http://127.0.0.1:18099/", timeout=5) as response:
    assert response.status == 200, response.status
    content_type = response.headers.get("Content-Type", "")
    html = response.read().decode("utf-8", errors="replace")

assert "text/html" in content_type, content_type
assert "502" not in html[:200], html[:200]
assert "Bad Gateway" not in html, html[:300]
assert "<title>Pi-hole</title>" in html, html[:500]
assert 'class="brand">Pi-hole</h1>' in html or ">Pi-hole<" in html, html[:800]
assert 'id="traffic-title"' in html, "traffic panel missing from page"
assert 'id="setup-title"' in html, "DNS setup panel missing from page"
assert 'id="dns-ips"' in html and 'id="admin-urls"' in html, "port chips missing from page"
assert 'id="blocking-error"' in html, "blocking error banner missing from page"
assert 'src="static/app.js' in html, html[:500]
assert 'href="static/styles.css' in html, html[:500]
assert 'href="/static/' not in html and 'src="/static/' not in html, "absolute /static paths break HA Ingress"
print("Management page HTML OK")

with urllib.request.urlopen("http://127.0.0.1:18099/static/app.js", timeout=5) as response:
    js = response.read().decode("utf-8", errors="replace")
    assert response.status == 200
    assert "refreshSetup" in js
    assert "admin_port" in js or "adminUrls" in js
    assert "UI_BASE" in js and "uiUrl" in js, "Ingress-relative URL helper missing"
print("static/app.js OK")

with urllib.request.urlopen("http://127.0.0.1:18099/static/styles.css", timeout=5) as response:
    css = response.read().decode("utf-8", errors="replace")
    assert response.status == 200
    assert ".brand" in css
print("static/styles.css OK")

req = urllib.request.Request(
    "http://127.0.0.1:18099/api/presets/hulu-ads/apply",
    data=b"{}",
    method="POST",
    headers={"Content-Type": "application/json"},
)
with urllib.request.urlopen(req, timeout=10) as response:
    data = json.load(response)
    assert data.get("ok") is True, data
    assert len(data.get("added", [])) >= 1, data

# Enable blocking must report who owns port 53 when FTL returns dns_failure.
block_req = urllib.request.Request(
    "http://127.0.0.1:18099/api/blocking",
    data=b'{"blocking":true}',
    method="POST",
    headers={"Content-Type": "application/json"},
)
try:
    urllib.request.urlopen(block_req, timeout=10)
    raise AssertionError("expected blocking POST to fail with dns_failure")
except urllib.error.HTTPError as exc:
    assert exc.code == 500, exc.code
    body = json.loads(exc.read().decode())
    err = body.get("error") or {}
    assert err.get("key") == "dns_failure", body
    assert "Port 53" in str(err.get("hint") or ""), body
    assert "AdGuard Home" in str(err.get("hint") or "") or "AdGuard Home" in str(
        body.get("owner_summary") or ""
    ), body
    assert body.get("owner_summary"), body
    print("blocking dns_failure reports port owner OK")

# Config contract: Ingress must use the fixed unique port (not 8099).
import yaml

cfg = yaml.safe_load((ROOT / "pihole/config.yaml").read_text())
assert cfg.get("ingress_port") == 8189, cfg.get("ingress_port")
assert cfg.get("version") == "1.10.7", cfg.get("version")
assert cfg.get("hassio_role") == "manager", cfg.get("hassio_role")
assert cfg.get("host_pid") is True, cfg.get("host_pid")
assert cfg.get("ports", {}).get("53/tcp") == 53
assert cfg.get("ports", {}).get("8088/tcp") == 8088
print("config ingress_port=8189 OK")

# Humanized owner summary for the common AdGuard localhost bind pattern.
summary = mod._humanize_port_owner_summary(
    53,
    [
        {"proto": "UDP", "address": "127.0.0.1", "comm": "", "detail": "UDP listener on 127.0.0.1:53"},
        {"proto": "UDP", "address": "::1", "comm": "", "detail": "UDP listener on ::1:53"},
        {"proto": "TCP", "address": "127.0.0.1", "comm": "", "detail": "TCP listener on 127.0.0.1:53"},
        {"proto": "TCP", "address": "::1", "comm": "", "detail": "TCP listener on ::1:53"},
    ],
    [],
)
assert "localhost DNS" in summary, summary
assert "AdGuard" in summary, summary
assert "UNCONN" not in summary and "LISTEN 0 4096" not in summary, summary
print("humanized localhost port-53 summary OK")

# port53_plan: loopback-only should recommend LAN BIND; wildcard is CONFLICT.
spec_plan = importlib.util.spec_from_file_location(
    "port53_plan",
    ROOT / "pihole/rootfs/opt/pihole-ha/port53_plan.py",
)
plan_mod = importlib.util.module_from_spec(spec_plan)
assert spec_plan.loader is not None
spec_plan.loader.exec_module(plan_mod)
plan_mod._ss_lines = lambda: [
    "udp UNCONN 0 0 127.0.0.1:53 0.0.0.0:*",
    "udp UNCONN 0 0 [::1]:53 [::]:*",
    "tcp LISTEN 0 4096 127.0.0.1:53 0.0.0.0:*",
    "tcp LISTEN 0 4096 [::1]:53 [::]:*",
]
plan_mod._primary_lan = lambda: ("eth0", "192.168.1.50")
assert plan_mod.plan() == "LOOPBACK_ONLY iface=eth0 ip=192.168.1.50", plan_mod.plan()
plan_mod._ss_lines = lambda: ["udp UNCONN 0 0 0.0.0.0:53 0.0.0.0:*"]
assert plan_mod.plan().startswith("CONFLICT"), plan_mod.plan()
plan_mod._ss_lines = lambda: []
assert plan_mod.plan() == "FREE", plan_mod.plan()
print("port53_plan OK")

print("UI smoke test passed (page shows)")
PY
