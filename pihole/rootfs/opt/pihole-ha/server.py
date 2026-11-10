#!/usr/bin/env python3
"""Home Assistant Ingress management bridge for Pi-hole v6."""

from __future__ import annotations

import json
import logging
import os
import socket
import threading
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

# Allow `python3 /opt/pihole-ha/server.py` imports from the same directory.
sys_path_dir = str(Path(__file__).resolve().parent)
if sys_path_dir not in __import__("sys").path:
    __import__("sys").path.insert(0, sys_path_dir)

from presets import HULU_ADS_PRESET, PRESETS, list_presets  # noqa: E402

STATIC_DIR = Path(__file__).resolve().parent / "static"
API_BASE = os.environ.get("PIHOLE_API_BASE", "http://127.0.0.1:8088").rstrip("/")
PASSWORD = os.environ.get("PIHOLE_HA_PASSWORD", "")
UI_PORT = int(os.environ.get("PIHOLE_HA_UI_PORT", "8099"))
LOG_LEVEL_NAME = os.environ.get("PIHOLE_HA_LOG_LEVEL", "info").upper()
AUTO_BLOCK_HULU_ADS = os.environ.get("PIHOLE_HA_BLOCK_HULU_ADS", "false").lower() in {
    "1",
    "true",
    "yes",
    "on",
}
ALLOWED_DOMAIN_FILTERS = {
    "allow/exact",
    "allow/regex",
    "deny/exact",
    "deny/regex",
}
ALLOWED_TYPES = {"allow", "deny"}
ALLOWED_KINDS = {"exact", "regex"}

_session_lock = threading.Lock()
_session: dict[str, Any] = {"sid": None, "expires": 0.0}

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL_NAME, logging.INFO),
    format="[pihole-ha-ui] %(levelname)s %(message)s",
)
log = logging.getLogger("pihole-ha-ui")


def wait_for_pihole(timeout: int = 120) -> bool:
    deadline = time.time() + timeout
    host = "127.0.0.1"
    port = 8088
    try:
        parsed = urllib.parse.urlparse(API_BASE)
        host = parsed.hostname or host
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except Exception as exc:  # noqa: BLE001
        log.warning("Failed to parse API_BASE=%s: %s", API_BASE, exc)

    log.info("Waiting for Pi-hole API at %s:%s (timeout=%ss)", host, port, timeout)
    attempt = 0
    while time.time() < deadline:
        attempt += 1
        try:
            with socket.create_connection((host, port), timeout=2):
                log.info("Pi-hole API reachable at %s:%s after %s attempt(s)", host, port, attempt)
                return True
        except OSError as exc:
            log.debug("Pi-hole API not ready yet (attempt %s): %s", attempt, exc)
            time.sleep(1)
    log.error("Timed out waiting for Pi-hole API at %s:%s", host, port)
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
        # Never log password values.
        safe_body = {k: ("***" if "password" in k.lower() else v) for k, v in body.items()}
        log.debug("API %s %s body=%s", method, path, safe_body)
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    else:
        log.debug("API %s %s", method, path)
    if sid:
        headers["X-FTL-SID"] = sid

    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    started = time.time()
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
            elapsed_ms = int((time.time() - started) * 1000)
            log.debug("API %s %s -> %s (%sms)", method, path, response.status, elapsed_ms)
            return response.status, payload, dict(response.headers)
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            payload = json.loads(raw.decode("utf-8")) if raw else {"error": str(exc)}
        except json.JSONDecodeError:
            payload = {"error": raw.decode("utf-8", errors="replace")}
        elapsed_ms = int((time.time() - started) * 1000)
        level = logging.DEBUG if exc.code in {400, 404} else logging.WARNING
        log.log(
            level,
            "API %s %s -> HTTP %s (%sms): %s",
            method,
            path,
            exc.code,
            elapsed_ms,
            payload,
        )
        return exc.code, payload, dict(exc.headers)
    except urllib.error.URLError as exc:
        elapsed_ms = int((time.time() - started) * 1000)
        log.error("API %s %s unreachable after %sms: %s", method, path, elapsed_ms, exc.reason)
        return 502, {"error": f"Pi-hole unreachable: {exc.reason}"}, {}
    except Exception as exc:  # noqa: BLE001
        log.exception("API %s %s unexpected failure: %s", method, path, exc)
        return 500, {"error": str(exc)}, {}


def authenticate(force: bool = False) -> str | None:
    with _session_lock:
        now = time.time()
        if not force and _session["sid"] and now < _session["expires"] - 30:
            log.debug("Reusing Pi-hole session (expires_in=%.0fs)", _session["expires"] - now)
            return _session["sid"]

        log.info("Authenticating with Pi-hole API (force=%s, password_set=%s)", force, bool(PASSWORD))
        status, payload, _ = api_request(
            "POST",
            "/api/auth",
            {"password": PASSWORD} if PASSWORD else {},
        )
        if status >= 400 or not isinstance(payload, dict):
            log.error("Auth failed (%s): %s", status, payload)
            _session["sid"] = None
            return None

        session = payload.get("session") or {}
        if not session.get("valid") and PASSWORD:
            if not session.get("sid"):
                log.error("Auth rejected: %s", payload)
                _session["sid"] = None
                return None

        sid = session.get("sid")
        validity = float(session.get("validity") or 1800)
        _session["sid"] = sid
        _session["expires"] = now + validity
        log.info("Authenticated with Pi-hole (validity=%ss)", int(validity))
        return sid


def authed_request(
    method: str,
    path: str,
    body: dict[str, Any] | None = None,
    timeout: float = 30.0,
) -> tuple[int, Any]:
    sid = authenticate()
    if not sid and PASSWORD:
        log.warning("No SID available before %s %s", method, path)
    status, payload, _ = api_request(method, path, body, sid=sid, timeout=timeout)
    if status == 401:
        log.info("Got 401 for %s %s; forcing re-auth", method, path)
        sid = authenticate(force=True)
        status, payload, _ = api_request(method, path, body, sid=sid, timeout=timeout)
    return status, payload


def apply_preset(preset_id: str) -> dict[str, Any]:
    preset = PRESETS.get(preset_id)
    if not preset:
        return {"ok": False, "error": f"Unknown preset: {preset_id}"}

    comment = preset.get("comment") or f"pihole-ha preset: {preset_id}"
    added: list[dict[str, str]] = []
    skipped: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []

    def _add(kind: str, value: str) -> None:
        status, payload = authed_request(
            "POST",
            f"/api/domains/deny/{kind}",
            {"domain": value, "enabled": True, "comment": comment},
        )
        entry = {"kind": kind, "domain": value}
        if status in {200, 201}:
            added.append(entry)
            log.info("Preset %s added deny/%s %s", preset_id, kind, value)
            return
        # Pi-hole returns database_error when the item already exists.
        message = ""
        if isinstance(payload, dict):
            err = payload.get("error") or {}
            if isinstance(err, dict):
                message = str(err.get("message") or err.get("hint") or "")
            else:
                message = str(payload.get("error") or payload)
        lowered = message.lower()
        if status == 400 and ("already" in lowered or "present" in lowered or "unique" in lowered):
            skipped.append({**entry, "reason": message or "already present"})
            log.debug("Preset %s already had deny/%s %s", preset_id, kind, value)
            return
        failed.append({**entry, "status": status, "error": payload})
        log.warning("Preset %s failed deny/%s %s -> %s %s", preset_id, kind, value, status, payload)

    for domain in preset.get("exact", []):
        _add("exact", domain)
    for pattern in preset.get("regex", []):
        _add("regex", pattern)

    return {
        "ok": not failed,
        "preset": preset_id,
        "title": preset.get("title"),
        "added": added,
        "skipped": skipped,
        "failed": failed,
        "notes": preset.get("notes", []),
        "never_block": preset.get("never_block", []),
    }


def detect_host_ips() -> list[str]:
    ips: list[str] = []
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("1.1.1.1", 80))
            ip = sock.getsockname()[0]
            if ip and not ip.startswith("127."):
                ips.append(ip)
                log.debug("Detected primary egress IP: %s", ip)
    except OSError as exc:
        log.debug("Could not detect egress IP: %s", exc)

    try:
        hostname = socket.gethostname()
        for info in socket.getaddrinfo(hostname, None, socket.AF_INET):
            candidate = info[4][0]
            if candidate not in ips and not candidate.startswith("127."):
                ips.append(candidate)
                log.debug("Detected hostname IP: %s", candidate)
    except OSError as exc:
        log.debug("Could not resolve hostname IPs: %s", exc)
    log.info("DNS setup IPs: %s", ips or ["<none detected>"])
    return ips


class Handler(BaseHTTPRequestHandler):
    server_version = "PiHoleHAUI/1.1"

    def log_message(self, fmt: str, *args: Any) -> None:
        log.info("%s %s", self.address_string(), fmt % args)

    def _client_allowed(self) -> bool:
        # Peer address only — do not trust spoofable ingress headers on host_network.
        host = self.client_address[0]
        allowed = host in {"127.0.0.1", "::1", "localhost", "172.30.32.2"} or host.startswith(
            "172.30.32."
        )
        if not allowed:
            log.warning("Rejected client %s for %s %s", host, self.command, self.path)
        else:
            log.debug("Allowed client %s for %s %s", host, self.command, self.path)
        return allowed

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, status: int, payload: Any) -> None:
        body = json.dumps(payload).encode("utf-8")
        log.debug("Response %s (%s bytes)", status, len(body))
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
            log.warning("Static file missing or path escape blocked: %s", rel)
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
        log.debug("Serving static file %s", target.name)
        self._send(200, target.read_bytes(), content_types.get(suffix, "application/octet-stream"))

    def do_GET(self) -> None:  # noqa: N802
        try:
            self._do_GET()
        except Exception as exc:  # noqa: BLE001
            log.error("Unhandled GET error for %s: %s\n%s", self.path, exc, traceback.format_exc())
            self._send_json(500, {"error": "Internal server error", "detail": str(exc)})

    def _do_GET(self) -> None:
        if not self._client_allowed():
            self._send_json(403, {"error": "Forbidden"})
            return

        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        log.debug("GET path=%s query=%s", path, parsed.query)

        if path in {"/", "/index.html"}:
            self._serve_static("index.html")
            return
        if path.startswith("/static/"):
            self._serve_static(path[len("/static/") :])
            return
        if path == "/api/health":
            self._send_json(
                200,
                {
                    "ok": True,
                    "service": "pihole-ha-ui",
                    "log_level": LOG_LEVEL_NAME.lower(),
                    "api_base": API_BASE,
                },
            )
            return
        if path == "/api/overview":
            self._handle_overview()
            return
        if path == "/api/traffic":
            self._handle_traffic(parsed.query)
            return
        if path == "/api/domains":
            kind = urllib.parse.parse_qs(parsed.query).get("filter", ["deny/exact"])[0]
            if kind not in ALLOWED_DOMAIN_FILTERS:
                log.warning("Rejected invalid domain filter: %s", kind)
                self._send_json(
                    400,
                    {
                        "error": "Invalid filter",
                        "allowed": sorted(ALLOWED_DOMAIN_FILTERS),
                    },
                )
                return
            status, payload = authed_request("GET", f"/api/domains/{kind}")
            self._send_json(status, payload)
            return
        if path == "/api/setup":
            ips = detect_host_ips()
            example_ip = ips[0] if ips else "192.168.1.50"
            self._send_json(
                200,
                {
                    "dns_ips": ips,
                    "dns_port": 53,
                    "admin_port": 8088,
                    "example_ip": example_ip,
                    "upstream_hint": "Point your router DHCP DNS to one of these addresses.",
                    "examples": build_dns_examples(example_ip),
                },
            )
            return
        if path == "/api/presets":
            self._send_json(200, {"presets": list_presets()})
            return
        if path.startswith("/api/presets/"):
            preset_id = path[len("/api/presets/") :].strip("/")
            preset = PRESETS.get(preset_id)
            if not preset:
                self._send_json(404, {"error": "Unknown preset"})
                return
            self._send_json(
                200,
                {
                    "id": preset["id"],
                    "title": preset["title"],
                    "description": preset["description"],
                    "exact": preset["exact"],
                    "regex": preset["regex"],
                    "notes": preset["notes"],
                    "never_block": preset["never_block"],
                },
            )
            return

        self._send_json(404, {"error": "Not found"})

    def do_POST(self) -> None:  # noqa: N802
        try:
            self._do_POST()
        except Exception as exc:  # noqa: BLE001
            log.error("Unhandled POST error for %s: %s\n%s", self.path, exc, traceback.format_exc())
            self._send_json(500, {"error": "Internal server error", "detail": str(exc)})

    def _do_POST(self) -> None:
        if not self._client_allowed():
            self._send_json(403, {"error": "Forbidden"})
            return

        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        log.debug("POST path=%s", path)

        try:
            body = self._read_json()
        except (json.JSONDecodeError, ValueError) as exc:
            log.warning("Invalid JSON body on %s: %s", path, exc)
            self._send_json(400, {"error": str(exc)})
            return

        if path == "/api/blocking":
            payload: dict[str, Any] = {"blocking": bool(body.get("blocking", True))}
            if body.get("timer") is not None:
                try:
                    payload["timer"] = int(body["timer"])
                except (TypeError, ValueError):
                    self._send_json(400, {"error": "timer must be an integer number of seconds"})
                    return
            log.info("Setting blocking=%s timer=%s", payload.get("blocking"), payload.get("timer"))
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
            if list_type not in ALLOWED_TYPES or kind not in ALLOWED_KINDS:
                self._send_json(
                    400,
                    {
                        "error": "Invalid type/kind",
                        "allowed_types": sorted(ALLOWED_TYPES),
                        "allowed_kinds": sorted(ALLOWED_KINDS),
                    },
                )
                return
            post_body: dict[str, Any] = {"domain": domain, "enabled": True}
            if comment:
                post_body["comment"] = comment
            log.info("Adding domain type=%s kind=%s domain=%s", list_type, kind, domain)
            status, result = authed_request("POST", f"/api/domains/{list_type}/{kind}", post_body)
            self._send_json(status, result)
            return

        if path == "/api/gravity":
            log.info("Starting gravity update")
            status, result = authed_request("POST", "/api/action/gravity", timeout=600.0)
            if isinstance(result, str):
                log.info("Gravity finished status=%s output_chars=%s", status, len(result))
                self._send_json(status, {"ok": status < 400, "output": result[-4000:]})
            else:
                log.info("Gravity finished status=%s payload=%s", status, result)
                self._send_json(status, result)
            return

        if path == "/api/flush-logs":
            log.info("Flushing query logs")
            status, result = authed_request("POST", "/api/action/flush/logs")
            self._send_json(status, result)
            return

        if path.startswith("/api/presets/") and path.endswith("/apply"):
            preset_id = path[len("/api/presets/") : -len("/apply")].strip("/")
            log.info("Applying preset %s", preset_id)
            result = apply_preset(preset_id)
            self._send_json(200 if result.get("ok") else 207, result)
            return

        self._send_json(404, {"error": "Not found"})

    def do_DELETE(self) -> None:  # noqa: N802
        try:
            self._do_DELETE()
        except Exception as exc:  # noqa: BLE001
            log.error("Unhandled DELETE error for %s: %s\n%s", self.path, exc, traceback.format_exc())
            self._send_json(500, {"error": "Internal server error", "detail": str(exc)})

    def _do_DELETE(self) -> None:
        if not self._client_allowed():
            self._send_json(403, {"error": "Forbidden"})
            return

        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        log.debug("DELETE path=%s", path)
        if path.startswith("/api/domains/"):
            remainder = path[len("/api/domains/") :]
            parts = remainder.split("/")
            if len(parts) >= 3:
                list_type, kind, domain = parts[0], parts[1], "/".join(parts[2:])
                domain = urllib.parse.unquote(domain)
                if list_type not in ALLOWED_TYPES or kind not in ALLOWED_KINDS:
                    self._send_json(400, {"error": "Invalid type/kind"})
                    return
                log.info("Deleting domain type=%s kind=%s domain=%s", list_type, kind, domain)
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
        log.debug("Building overview payload")
        status_summary, summary = authed_request("GET", "/api/stats/summary")
        status_blocking, blocking = authed_request("GET", "/api/dns/blocking")
        status_version, version = authed_request("GET", "/api/info/version")
        status_top, top_blocked = authed_request("GET", "/api/stats/top_domains?blocked=true&count=8")
        status_recent, recent = authed_request("GET", "/api/stats/recent_blocked?count=8")

        healthy = status_summary < 400 and status_blocking < 400
        log.info(
            "Overview healthy=%s summary=%s blocking=%s version=%s top=%s recent=%s",
            healthy,
            status_summary,
            status_blocking,
            status_version,
            status_top,
            status_recent,
        )
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

    def _handle_traffic(self, query: str) -> None:
        params = urllib.parse.parse_qs(query)
        try:
            length = max(1, min(int((params.get("length") or ["40"])[0]), 200))
        except (TypeError, ValueError):
            length = 40

        log.debug("Building traffic payload length=%s", length)
        status_history, history = authed_request("GET", "/api/history")
        status_clients, top_clients = authed_request("GET", "/api/stats/top_clients?count=8")
        status_queries, queries = authed_request("GET", f"/api/queries?length={length}")
        status_upstreams, upstreams = authed_request("GET", "/api/stats/upstreams")

        history_rows = []
        if status_history < 400 and isinstance(history, dict):
            history_rows = history.get("history") or []

        totals = {"total": 0, "blocked": 0, "cached": 0, "forwarded": 0}
        for row in history_rows:
            if not isinstance(row, dict):
                continue
            for key in totals:
                try:
                    totals[key] += int(row.get(key) or 0)
                except (TypeError, ValueError):
                    continue

        query_rows = []
        if status_queries < 400 and isinstance(queries, dict):
            query_rows = queries.get("queries") or []

        healthy = status_history < 400
        self._send_json(
            200 if healthy else 503,
            {
                "healthy": healthy,
                "history": history_rows if status_history < 400 else [],
                "totals_24h": totals,
                "top_clients": top_clients if status_clients < 400 else None,
                "queries": query_rows if status_queries < 400 else [],
                "upstreams": upstreams if status_upstreams < 400 else None,
                "errors": {
                    "history": history if status_history >= 400 else None,
                    "queries": queries if status_queries >= 400 else None,
                    "clients": top_clients if status_clients >= 400 else None,
                },
            },
        )


def build_dns_examples(ip: str) -> list[dict[str, str]]:
    return [
        {
            "title": "Router DHCP (recommended)",
            "steps": (
                f"Open your router admin page → LAN / DHCP settings. "
                f"Set Primary DNS to {ip}. Leave Secondary empty or set a backup only if needed. "
                f"Save, then renew leases on clients."
            ),
        },
        {
            "title": "Quick test on one device",
            "steps": (
                f"Before changing the whole network, set only one device DNS to {ip}. "
                f"Browse a site with ads and confirm blocks appear in the Pi-hole panel."
            ),
        },
        {
            "title": "Windows 11",
            "steps": (
                f"Settings → Network & internet → your adapter → DNS server assignment → Edit. "
                f"Choose Manual, enable IPv4, set Preferred DNS to {ip}, save."
            ),
        },
        {
            "title": "macOS",
            "steps": (
                f"System Settings → Network → your interface → Details → DNS. "
                f"Click + and add {ip}. Apply, then flush with: sudo dscacheutil -flushcache"
            ),
        },
        {
            "title": "iPhone / iPad",
            "steps": (
                f"Settings → Wi-Fi → tap the info icon → Configure DNS → Manual. "
                f"Remove existing servers and add {ip}."
            ),
        },
        {
            "title": "Android",
            "steps": (
                f"Settings → Network & internet → Internet → your Wi-Fi → pencil/edit → Advanced → DNS. "
                f"Choose Private DNS off / static IP mode if needed, then set DNS 1 to {ip}."
            ),
        },
        {
            "title": "Linux (NetworkManager)",
            "steps": (
                f"nmcli connection modify '<connection>' ipv4.dns \"{ip}\" "
                f"ipv4.ignore-auto-dns yes && nmcli connection up '<connection>'"
            ),
        },
        {
            "title": "UniFi Network",
            "steps": (
                f"UniFi Network → Settings → Networks → your LAN → DHCP Service Management. "
                f"Set DHCP DNS Server to {ip}. Apply changes and reconnect clients."
            ),
        },
        {
            "title": "pfSense / OPNsense",
            "steps": (
                f"Services → DHCP Server → your LAN. Set DNS Server to {ip}. "
                f"Under General Setup, avoid forcing different DNS unless intentional. Apply."
            ),
        },
        {
            "title": "Verify with dig / nslookup",
            "steps": (
                f"dig @{ip} doubleclick.net +short   # often returns 0.0.0.0 when blocked\n"
                f"nslookup pi.hole {ip}              # should resolve to your Pi-hole host"
            ),
        },
    ]


def main() -> None:
    log.info(
        "Starting management UI log_level=%s api_base=%s ui_port=%s password_set=%s block_hulu_ads=%s",
        LOG_LEVEL_NAME.lower(),
        API_BASE,
        UI_PORT,
        bool(PASSWORD),
        AUTO_BLOCK_HULU_ADS,
    )
    ready = wait_for_pihole()
    if not ready:
        log.error("Continuing without confirmed Pi-hole API readiness; requests may fail until it starts")
    sid = authenticate(force=True)
    if sid:
        log.info("Initial Pi-hole session established")
    else:
        log.warning("Initial Pi-hole authentication failed; will retry on demand")

    if AUTO_BLOCK_HULU_ADS:
        log.info("Auto-applying Hulu ads preset (%s)", HULU_ADS_PRESET["id"])
        result = apply_preset(HULU_ADS_PRESET["id"])
        log.info(
            "Hulu preset result added=%s skipped=%s failed=%s",
            len(result.get("added", [])),
            len(result.get("skipped", [])),
            len(result.get("failed", [])),
        )

    # Bind all interfaces so Home Assistant Ingress can reach us with host_network.
    # Access is still restricted by peer IP allow-list.
    server = ThreadingHTTPServer(("0.0.0.0", UI_PORT), Handler)
    log.info("Management UI listening on 0.0.0.0:%s", UI_PORT)
    server.serve_forever()


if __name__ == "__main__":
    main()
