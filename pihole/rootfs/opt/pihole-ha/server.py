#!/usr/bin/env python3
"""Home Assistant Ingress management bridge for Pi-hole v6."""

from __future__ import annotations

import json
import os
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

STATIC_DIR = Path(__file__).resolve().parent / "static"
API_BASE = os.environ.get("PIHOLE_API_BASE", "http://127.0.0.1:8080").rstrip("/")
PASSWORD = os.environ.get("PIHOLE_HA_PASSWORD", "")
UI_PORT = int(os.environ.get("PIHOLE_HA_UI_PORT", "8099"))
_session_lock = threading.Lock()
_session: dict[str, Any] = {"sid": None, "expires": 0.0}


def log(message: str) -> None:
    print(f"[pihole-ha-ui] {message}", flush=True)


def wait_for_pihole(timeout: int = 120) -> bool:
    deadline = time.time() + timeout
    host = "127.0.0.1"
    port = 8080
    try:
        parsed = urllib.parse.urlparse(API_BASE)
        host = parsed.hostname or host
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except Exception:  # noqa: BLE001
        pass

    while time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=2):
                log(f"Pi-hole API is reachable at {host}:{port}")
                return True
        except OSError:
            time.sleep(1)
    log("Timed out waiting for Pi-hole API")
    return False


def api_request(
    method: str,
    path: str,
    body: dict[str, Any] | None = None,
    sid: str | None = None,
    timeout: float = 30.0,
) -> tuple[int, Any, dict[str, str]]:
    url = f"{API_BASE}{path}"
    data = None
    headers = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if sid:
        headers["X-FTL-SID"] = sid

    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
            payload: Any
            if not raw:
                payload = None
            else:
                content_type = response.headers.get("Content-Type", "")
                if "application/json" in content_type or raw[:1] in (b"{", b"["):
                    payload = json.loads(raw.decode("utf-8"))
                else:
                    payload = raw.decode("utf-8", errors="replace")
            return response.status, payload, dict(response.headers)
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            payload = json.loads(raw.decode("utf-8")) if raw else {"error": str(exc)}
        except json.JSONDecodeError:
            payload = {"error": raw.decode("utf-8", errors="replace")}
        return exc.code, payload, dict(exc.headers)
    except urllib.error.URLError as exc:
        return 502, {"error": f"Pi-hole unreachable: {exc.reason}"}, {}


def authenticate(force: bool = False) -> str | None:
    with _session_lock:
        now = time.time()
        if not force and _session["sid"] and now < _session["expires"] - 30:
            return _session["sid"]

        status, payload, _ = api_request(
            "POST",
            "/api/auth",
            {"password": PASSWORD} if PASSWORD else {},
        )
        if status >= 400 or not isinstance(payload, dict):
            log(f"Auth failed ({status}): {payload}")
            _session["sid"] = None
            return None

        session = payload.get("session") or {}
        if not session.get("valid") and PASSWORD:
            # Empty password installs may still return a usable session.
            if not session.get("sid"):
                log(f"Auth rejected: {payload}")
                _session["sid"] = None
                return None

        sid = session.get("sid")
        validity = float(session.get("validity") or 1800)
        _session["sid"] = sid
        _session["expires"] = now + validity
        return sid


def authed_request(
    method: str,
    path: str,
    body: dict[str, Any] | None = None,
    timeout: float = 30.0,
) -> tuple[int, Any]:
    sid = authenticate()
    status, payload, _ = api_request(method, path, body, sid=sid, timeout=timeout)
    if status == 401:
        sid = authenticate(force=True)
        status, payload, _ = api_request(method, path, body, sid=sid, timeout=timeout)
    return status, payload


def detect_host_ips() -> list[str]:
    ips: list[str] = []
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("1.1.1.1", 80))
            ip = sock.getsockname()[0]
            if ip and not ip.startswith("127."):
                ips.append(ip)
    except OSError:
        pass

    try:
        hostname = socket.gethostname()
        for info in socket.getaddrinfo(hostname, None, socket.AF_INET):
            candidate = info[4][0]
            if candidate not in ips and not candidate.startswith("127."):
                ips.append(candidate)
    except OSError:
        pass
    return ips


class Handler(BaseHTTPRequestHandler):
    server_version = "PiHoleHAUI/1.0"

    def log_message(self, fmt: str, *args: Any) -> None:
        log(f"{self.address_string()} {fmt % args}")

    def _client_allowed(self) -> bool:
        # Prefer Ingress identity header when present.
        if self.headers.get("X-Hass-Source") == "core.ingress":
            return True
        host = self.client_address[0]
        # Only Supervisor ingress + local loopback. Do not expose the panel on the LAN.
        if host in {"127.0.0.1", "::1", "localhost", "172.30.32.2"}:
            return True
        if host.startswith("172.30.32."):
            return True
        return False

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, status: int, payload: Any) -> None:
        body = json.dumps(payload).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8")

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        if not raw:
            return {}
        data = json.loads(raw.decode("utf-8"))
        if not isinstance(data, dict):
            raise ValueError("JSON body must be an object")
        return data

    def _serve_static(self, rel: str) -> None:
        target = (STATIC_DIR / rel).resolve()
        if not str(target).startswith(str(STATIC_DIR.resolve())) or not target.is_file():
            self._send_json(404, {"error": "Not found"})
            return
        suffix = target.suffix.lower()
        content_types = {
            ".html": "text/html; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".js": "application/javascript; charset=utf-8",
            ".svg": "image/svg+xml",
            ".png": "image/png",
            ".ico": "image/x-icon",
        }
        self._send(200, target.read_bytes(), content_types.get(suffix, "application/octet-stream"))

    def do_GET(self) -> None:  # noqa: N802
        if not self._client_allowed():
            self._send_json(403, {"error": "Forbidden"})
            return

        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if path in {"/", "/index.html"}:
            self._serve_static("index.html")
            return
        if path.startswith("/static/"):
            self._serve_static(path[len("/static/") :])
            return
        if path == "/api/health":
            self._send_json(200, {"ok": True, "service": "pihole-ha-ui"})
            return
        if path == "/api/overview":
            self._handle_overview()
            return
        if path == "/api/domains":
            kind = urllib.parse.parse_qs(parsed.query).get("filter", ["deny/exact"])[0]
            status, payload = authed_request("GET", f"/api/domains/{kind}")
            self._send_json(status, payload)
            return
        if path == "/api/setup":
            self._send_json(
                200,
                {
                    "dns_ips": detect_host_ips(),
                    "dns_port": 53,
                    "admin_port": 8080,
                    "upstream_hint": "Point your router DHCP DNS to one of these addresses.",
                },
            )
            return

        self._send_json(404, {"error": "Not found"})

    def do_POST(self) -> None:  # noqa: N802
        if not self._client_allowed():
            self._send_json(403, {"error": "Forbidden"})
            return

        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        try:
            body = self._read_json()
        except (json.JSONDecodeError, ValueError) as exc:
            self._send_json(400, {"error": str(exc)})
            return

        if path == "/api/blocking":
            payload = {"blocking": bool(body.get("blocking", True))}
            if body.get("timer") is not None:
                payload["timer"] = int(body["timer"])
            status, result = authed_request("POST", "/api/dns/blocking", payload)
            self._send_json(status, result)
            return

        if path == "/api/domains":
            domain = str(body.get("domain", "")).strip()
            list_type = str(body.get("type", "deny")).strip() or "deny"
            kind = str(body.get("kind", "exact")).strip() or "exact"
            comment = body.get("comment")
            if not domain:
                self._send_json(400, {"error": "domain is required"})
                return
            post_body: dict[str, Any] = {"domain": domain, "enabled": True}
            if comment:
                post_body["comment"] = comment
            status, result = authed_request("POST", f"/api/domains/{list_type}/{kind}", post_body)
            self._send_json(status, result)
            return

        if path == "/api/gravity":
            status, result = authed_request("POST", "/api/action/gravity", timeout=600.0)
            # Gravity streams text; normalize for the UI.
            if isinstance(result, str):
                self._send_json(status, {"ok": status < 400, "output": result[-4000:]})
            else:
                self._send_json(status, result)
            return

        if path == "/api/flush-logs":
            status, result = authed_request("POST", "/api/action/flush/logs")
            self._send_json(status, result)
            return

        self._send_json(404, {"error": "Not found"})

    def do_DELETE(self) -> None:  # noqa: N802
        if not self._client_allowed():
            self._send_json(403, {"error": "Forbidden"})
            return

        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        if path.startswith("/api/domains/"):
            remainder = path[len("/api/domains/") :]
            parts = remainder.split("/")
            if len(parts) >= 3:
                list_type, kind, domain = parts[0], parts[1], "/".join(parts[2:])
                domain = urllib.parse.unquote(domain)
                status, result = authed_request(
                    "DELETE",
                    f"/api/domains/{list_type}/{kind}/{urllib.parse.quote(domain, safe='')}",
                )
                if status == 204:
                    self._send_json(200, {"ok": True})
                else:
                    self._send_json(status, result or {"ok": False})
                return

        self._send_json(404, {"error": "Not found"})

    def _handle_overview(self) -> None:
        status_summary, summary = authed_request("GET", "/api/stats/summary")
        status_blocking, blocking = authed_request("GET", "/api/dns/blocking")
        status_version, version = authed_request("GET", "/api/info/version")
        status_top, top_blocked = authed_request("GET", "/api/stats/top_domains?blocked=true&count=8")
        status_recent, recent = authed_request("GET", "/api/stats/recent_blocked?count=8")

        healthy = status_summary < 400 and status_blocking < 400
        self._send_json(
            200 if healthy else 503,
            {
                "healthy": healthy,
                "summary": summary if status_summary < 400 else None,
                "blocking": blocking if status_blocking < 400 else None,
                "version": version if status_version < 400 else None,
                "top_blocked": top_blocked if status_top < 400 else None,
                "recent_blocked": recent if status_recent < 400 else None,
                "errors": {
                    "summary": summary if status_summary >= 400 else None,
                    "blocking": blocking if status_blocking >= 400 else None,
                },
            },
        )


def main() -> None:
    wait_for_pihole()
    authenticate(force=True)
    server = ThreadingHTTPServer(("0.0.0.0", UI_PORT), Handler)
    log(f"Management UI listening on 0.0.0.0:{UI_PORT}")
    server.serve_forever()


if __name__ == "__main__":
    main()
