#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT}"

python3 -m py_compile \
  pihole/rootfs/opt/pihole-ha/server.py \
  pihole/rootfs/opt/pihole-ha/presets.py

python3 <<'PY'
import importlib.util
import json
import os
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

store = {"domains": []}


class Fake(BaseHTTPRequestHandler):
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
            return self._json(200, {"blocking": True, "timer": None})
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


fake = HTTPServer(("127.0.0.1", 18080), Fake)
threading.Thread(target=fake.serve_forever, daemon=True).start()

os.environ.update(
    PIHOLE_API_BASE="http://127.0.0.1:18080",
    PIHOLE_HA_PASSWORD="secret",
    PIHOLE_HA_UI_PORT="18099",
    PIHOLE_HA_LOG_LEVEL="warning",
    PIHOLE_HA_BLOCK_HULU_ADS="false",
)

spec = importlib.util.spec_from_file_location(
    "server",
    "pihole/rootfs/opt/pihole-ha/server.py",
)
mod = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(mod)
mod.wait_for_pihole = lambda timeout=120: True
threading.Thread(target=mod.main, daemon=True).start()
time.sleep(0.5)

with urllib.request.urlopen("http://127.0.0.1:18099/api/health", timeout=5) as response:
    health = json.load(response)
    assert health.get("ok") is True, health

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

print("UI smoke test passed")
PY
