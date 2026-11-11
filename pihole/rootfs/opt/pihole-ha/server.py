#!/usr/bin/env python3
"""Home Assistant Ingress management bridge for Pi-hole v6."""

from __future__ import annotations

import json
import logging
import os
import re
import socket
import subprocess
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
UI_PORT = int(os.environ.get("PIHOLE_HA_UI_PORT", "8189"))
DNS_PORT = int(os.environ.get("PIHOLE_DNS_PORT", "53"))
DNS_BIND_INTERFACE = (os.environ.get("FTLCONF_dns_interface") or "").strip()
DNS_LISTENING_MODE = (os.environ.get("FTLCONF_dns_listeningMode") or "ALL").strip()
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


def _admin_port_from_api_base(api_base: str) -> int:
    """Native Pi-hole web/API listen port used for direct admin access and API forwarding."""
    try:
        parsed = urllib.parse.urlparse(api_base)
        if parsed.port:
            return int(parsed.port)
        return 443 if parsed.scheme == "https" else 80
    except Exception:  # noqa: BLE001
        return 8088


ADMIN_PORT = int(os.environ.get("PIHOLE_ADMIN_PORT", str(_admin_port_from_api_base(API_BASE))))

_session_lock = threading.Lock()
_session: dict[str, Any] = {"sid": None, "expires": 0.0}

# Common Home Assistant DNS add-ons that collide on host port 53.
_KNOWN_DNS_SLUGS = {
    "adguard",
    "adguard-home",
    "core_adguard",
    "a0d7b954_adguard",
    "dnsmasq",
    "core_dnsmasq",
    "unbound",
    "core_unbound",
    "technitium",
    "coredns",
    "blocky",
    "nextdns",
}

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL_NAME, logging.INFO),
    format="[pihole-ha-ui] %(levelname)s %(message)s",
)
log = logging.getLogger("pihole-ha-ui")


def wait_for_pihole(timeout: int = 120) -> bool:
    deadline = time.time() + timeout
    host = "127.0.0.1"
    port = ADMIN_PORT
    try:
        parsed = urllib.parse.urlparse(API_BASE)
        host = parsed.hostname or host
        port = parsed.port or port
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


def _read_cmdline(pid: int) -> str:
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return ""
    text = raw.replace(b"\x00", b" ").decode("utf-8", errors="replace").strip()
    return text[:200]


def _read_comm(pid: int) -> str:
    try:
        return Path(f"/proc/{pid}/comm").read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ""


def _inode_to_pids() -> dict[int, list[int]]:
    mapping: dict[int, list[int]] = {}
    proc = Path("/proc")
    try:
        entries = list(proc.iterdir())
    except OSError:
        return mapping
    for entry in entries:
        if not entry.name.isdigit():
            continue
        fd_dir = entry / "fd"
        try:
            for fd in fd_dir.iterdir():
                try:
                    target = os.readlink(fd)
                except OSError:
                    continue
                if not target.startswith("socket:["):
                    continue
                try:
                    inode = int(target[8:-1])
                except ValueError:
                    continue
                mapping.setdefault(inode, []).append(int(entry.name))
        except OSError:
            continue
    return mapping


def _parse_proc_net_listeners(port: int) -> list[dict[str, Any]]:
    """Find listeners on <port> via /proc/net/{tcp,udp,tcp6,udp6} + socket inodes."""
    hex_port = f"{port:04X}"
    owners: list[dict[str, Any]] = []
    seen: set[tuple[str, int | None, str]] = set()
    inode_map = _inode_to_pids()

    for table in ("udp", "tcp", "udp6", "tcp6"):
        path = Path(f"/proc/net/{table}")
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()[1:]
        except OSError:
            continue
        proto = table.replace("6", "").upper()
        for line in lines:
            parts = line.split()
            if len(parts) < 10:
                continue
            local = parts[1]
            state = parts[3]
            # UDP has no listen state equivalent; TCP listen is 0A.
            if table.startswith("tcp") and state != "0A":
                continue
            if ":" not in local:
                continue
            addr_hex, local_port = local.rsplit(":", 1)
            if local_port.upper() != hex_port:
                continue
            address = _hex_ip_to_str(addr_hex, ipv6=table.endswith("6"))
            try:
                inode = int(parts[9])
            except ValueError:
                inode = 0
            pids = inode_map.get(inode) or []
            if not pids:
                key = (proto, None, f"inode={inode}")
                if key not in seen:
                    seen.add(key)
                    owners.append(
                        {
                            "proto": proto,
                            "pid": None,
                            "comm": "",
                            "cmdline": "",
                            "address": address,
                            "detail": (
                                f"{proto} listener on {address}:{port}"
                                if address
                                else f"{proto} listener inode={inode} (process not visible in this namespace)"
                            ),
                        }
                    )
                continue
            for pid in pids:
                comm = _read_comm(pid)
                cmdline = _read_cmdline(pid)
                key = (proto, pid, comm)
                if key in seen:
                    continue
                seen.add(key)
                owners.append(
                    {
                        "proto": proto,
                        "pid": pid,
                        "comm": comm,
                        "cmdline": cmdline,
                        "address": address,
                        "detail": (
                            f"{proto} on {address or '?'}:{port} → pid={pid} {comm or 'unknown'}"
                            + (f" — {cmdline}" if cmdline else "")
                        ).strip(),
                    }
                )
    return owners


def _hex_ip_to_str(addr_hex: str, ipv6: bool = False) -> str | None:
    try:
        if ipv6:
            raw = bytes.fromhex(addr_hex)
            if len(raw) != 16:
                return None
            # /proc/net/tcp6 stores little-endian 32-bit words.
            words = [raw[i : i + 4][::-1].hex() for i in range(0, 16, 4)]
            packed = bytes.fromhex("".join(words))
            return socket.inet_ntop(socket.AF_INET6, packed)
        # IPv4 in /proc/net is little-endian hex.
        value = int(addr_hex, 16)
        return socket.inet_ntoa(value.to_bytes(4, "little"))
    except (ValueError, OSError):
        return None


def _ss_local_address(line: str, port: int) -> str | None:
    """Extract the local IP from an ss listen line for the given port."""
    # Prefer bracketed IPv6 then plain IPv4/host tokens ending in :port.
    match = re.search(rf"\[([^\]]+)\]:{port}\b", line)
    if match:
        return match.group(1)
    match = re.search(rf"(\d+\.\d+\.\d+\.\d+):{port}\b", line)
    if match:
        return match.group(1)
    match = re.search(rf"\*:{port}\b|0\.0\.0\.0:{port}\b", line)
    if match:
        return "0.0.0.0"
    return None


def _is_loopback_addr(addr: str | None) -> bool:
    if not addr:
        return False
    lowered = addr.lower().strip()
    return (
        lowered in {"127.0.0.1", "127.0.0.53", "127.0.0.54", "::1", "localhost"}
        or lowered.startswith("127.")
    )


def _parse_ss_listeners(port: int) -> list[dict[str, Any]]:
    if not __import__("shutil").which("ss"):
        return []
    owners: list[dict[str, Any]] = []
    seen: set[tuple[str, str | None, int | None, str]] = set()
    # Prefer -p variants; fall back without process names when CAP/PID namespace blocks them.
    for args in (["ss", "-lntup"], ["ss", "-lnup"], ["ss", "-lntp"], ["ss", "-lntu"]):
        try:
            completed = subprocess.run(
                args,
                check=False,
                capture_output=True,
                text=True,
                timeout=3,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            log.debug("ss failed (%s): %s", args, exc)
            continue
        batch: list[dict[str, Any]] = []
        for line in completed.stdout.splitlines():
            if not re.search(rf":{port}\b", line):
                continue
            cleaned = " ".join(line.split())
            first = cleaned.split(" ", 1)[0].lower()
            proto = "TCP" if first.startswith("tcp") else "UDP"
            local = _ss_local_address(cleaned, port)
            pid = None
            comm = ""
            match = re.search(r'users:\(\("([^"]+)",pid=(\d+)', cleaned)
            if match:
                comm = match.group(1)
                pid = int(match.group(2))
            else:
                match = re.search(r'pid=(\d+).*?users:\(\("([^"]+)"', cleaned)
                if match:
                    pid = int(match.group(1))
                    comm = match.group(2)
            key = (proto, local, pid, comm)
            if key in seen:
                continue
            seen.add(key)
            if pid and comm:
                cmdline = _read_cmdline(pid)
                detail = (
                    f"{proto} on {local or '?'}:{port} → pid={pid} {comm}"
                    + (f" — {cmdline}" if cmdline else "")
                )
            elif local:
                detail = f"{proto} listener on {local}:{port}"
            else:
                detail = f"{proto} listener on port {port}"
            batch.append(
                {
                    "proto": proto,
                    "pid": pid,
                    "comm": comm,
                    "cmdline": _read_cmdline(pid) if pid else "",
                    "address": local,
                    "raw": cleaned,
                    "detail": detail,
                }
            )
        if batch:
            owners.extend(batch)
            # Stop once we got a useful sample; prefer the first successful ss invocation.
            break
    return owners


def _supervisor_api_bases() -> list[str]:
    configured = (os.environ.get("SUPERVISOR_API_BASE") or "").strip()
    if configured:
        return [configured.rstrip("/")]
    # host_network add-ons cannot rely on the "supervisor" DNS name.
    return ["http://172.30.32.2", "http://supervisor", "http://hassio"]


def _supervisor_get_json(path: str) -> Any | None:
    token = os.environ.get("SUPERVISOR_TOKEN") or os.environ.get("HASSIO_TOKEN")
    if not token:
        log.warning("SUPERVISOR_TOKEN missing; cannot identify peer DNS add-ons")
        return None
    headers = {
        "Authorization": f"Bearer {token}",
        "X-Supervisor-Token": token,
        "Accept": "application/json",
    }
    errors: list[str] = []
    for base in _supervisor_api_bases():
        url = f"{base.rstrip('/')}/{path.lstrip('/')}"
        request = urllib.request.Request(url, headers=headers, method="GET")
        try:
            with urllib.request.urlopen(request, timeout=3) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = ""
            try:
                body = exc.read().decode("utf-8", errors="replace")[:200]
            except Exception:  # noqa: BLE001
                body = ""
            errors.append(f"{url}: HTTP {exc.code} {body}")
            # 401/403 mean role/token issue — no point trying other bases the same way.
            if exc.code in {401, 403}:
                break
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{url}: {exc}")
            continue
    if errors:
        log.warning("Supervisor GET %s failed: %s", path, "; ".join(errors))
    return None


def _probe_localhost_dns_ui() -> list[dict[str, Any]]:
    """Best-effort fingerprint of common DNS admin UIs on localhost."""
    probes = (
        ("AdGuard Home", ("http://127.0.0.1:3000/", "http://127.0.0.1:80/")),
        ("Technitium DNS", ("http://127.0.0.1:5380/",)),
    )
    found: list[dict[str, Any]] = []
    for label, urls in probes:
        for url in urls:
            request = urllib.request.Request(url, method="GET", headers={"Accept": "text/html"})
            try:
                with urllib.request.urlopen(request, timeout=1.5) as response:
                    body = response.read(4096).decode("utf-8", errors="replace").lower()
                    server = (response.headers.get("Server") or "").lower()
            except Exception:  # noqa: BLE001
                continue
            hit = False
            if "adguard" in label.lower() and (
                "adguard" in body or "adguard" in server or "control.html" in body
            ):
                hit = True
            if "technitium" in label.lower() and "technitium" in body:
                hit = True
            if hit:
                found.append(
                    {
                        "proto": "probe",
                        "pid": None,
                        "comm": label,
                        "cmdline": url,
                        "detail": f"{label} admin UI responds at {url}",
                    }
                )
                break
    return found


def _is_known_dns_addon(slug: str, name: str) -> bool:
    blob = f"{slug} {name}".lower()
    if slug.lower() in _KNOWN_DNS_SLUGS:
        return True
    return any(
        key in blob
        for key in ("adguard", "dnsmasq", "unbound", "blocky", "technitium", "nextdns", "coredns")
    )


def _supervisor_dns_conflicts(port: int = DNS_PORT) -> list[dict[str, Any]]:
    """Ask Home Assistant Supervisor which other add-ons may own DNS port 53."""
    payload = _supervisor_get_json("/addons")
    if not isinstance(payload, dict):
        return _probe_localhost_dns_ui()

    data = payload.get("data") if isinstance(payload, dict) else None
    addons = []
    if isinstance(data, dict):
        addons = data.get("addons") or data.get("installed") or []
    elif isinstance(data, list):
        addons = data
    if not isinstance(addons, list):
        return _probe_localhost_dns_ui()

    conflicts: list[dict[str, Any]] = []
    for addon in addons:
        if not isinstance(addon, dict):
            continue
        slug = str(addon.get("slug") or "")
        name = str(addon.get("name") or slug or "unknown add-on")
        state = str(addon.get("state") or "")
        slug_l = slug.lower()
        # Skip ourselves (slug is "pihole" or a namespaced "*_pihole").
        if slug_l == "pihole" or slug_l.endswith("_pihole"):
            continue

        known = _is_known_dns_addon(slug, name)
        if state and state not in {"started", "running"}:
            # Still flag known DNS add-ons even when stopped.
            if not known:
                continue

        network = addon.get("network") or addon.get("ports") or {}
        # List endpoint omits ports — fetch detail for DNS-like add-ons.
        if not network and known:
            detail_payload = _supervisor_get_json(f"/addons/{slug}/info")
            if isinstance(detail_payload, dict):
                detail_data = detail_payload.get("data") or detail_payload
                if isinstance(detail_data, dict):
                    network = detail_data.get("network") or detail_data.get("ports") or network
                    state = str(detail_data.get("state") or state)
                    name = str(detail_data.get("name") or name)

        claims_53 = False
        if isinstance(network, dict):
            for key, value in network.items():
                key_l = str(key).lower()
                if key_l.startswith("53/") or key_l == "53":
                    claims_53 = True
                if value in {53, "53", port, str(port)}:
                    claims_53 = True
        if not claims_53 and not known:
            continue
        # Prefer running DNS add-ons; mention stopped known ones as secondary.
        if state and state not in {"started", "running"} and not claims_53:
            continue
        action = "Stop it under Settings → Add-ons, then restart Pi-hole"
        detail = (
            f"Home Assistant add-on '{name}' (slug={slug}, state={state or 'unknown'})"
        )
        if claims_53:
            detail += f" publishes host port {port}. {action}"
        elif known:
            detail += f" is a DNS add-on that typically binds port {port}. {action}"
        conflicts.append(
            {
                "proto": "addon",
                "pid": None,
                "comm": slug,
                "cmdline": name,
                "detail": detail,
            }
        )

    if not conflicts:
        conflicts.extend(_probe_localhost_dns_ui())
    return conflicts


def _humanize_port_owner_summary(
    port: int,
    socket_owners: list[dict[str, Any]],
    addon_conflicts: list[dict[str, Any]],
) -> str:
    """Build a short user-facing explanation instead of dumping raw ss lines."""
    parts: list[str] = []

    named = [
        o
        for o in socket_owners
        if o.get("comm")
        and "pihole" not in str(o.get("comm", "")).lower()
        and "ftl" not in str(o.get("comm", "")).lower()
    ]
    for owner in named:
        parts.append(str(owner.get("detail")))

    for conflict in addon_conflicts:
        if conflict.get("detail"):
            parts.append(str(conflict["detail"]))

    addresses = sorted(
        {
            str(o.get("address"))
            for o in socket_owners
            if o.get("address")
        }
    )
    loopback_only = bool(addresses) and all(_is_loopback_addr(addr) for addr in addresses)
    has_resolved_stub = any(addr in {"127.0.0.53", "127.0.0.54"} for addr in addresses)

    if not parts:
        if has_resolved_stub:
            parts.append(
                "systemd-resolved stub resolver on 127.0.0.53 "
                "(disable DNSStubListener or stop systemd-resolved on the host)"
            )
        elif loopback_only:
            # AdGuard Home on HA commonly binds 127.0.0.1:53 and [::1]:53.
            addr_list = ", ".join(f"{a}:{port}" for a in addresses)
            parts.append(
                f"localhost DNS on {addr_list} — usually AdGuard Home. "
                "Open Settings → Add-ons, stop AdGuard (or another DNS add-on), "
                "then restart Pi-hole"
            )
        elif addresses:
            parts.append(
                "listeners on "
                + ", ".join(f"{a}:{port}" for a in addresses)
                + " (process name not visible from this add-on namespace — "
                "check AdGuard, dnsmasq, or another DNS add-on)"
            )
        else:
            parts.append(
                "another DNS service on this host (process not visible from this add-on — "
                "check AdGuard, dnsmasq, systemd-resolved)"
            )
    elif loopback_only and not any("AdGuard" in p or "adguard" in p.lower() for p in parts):
        # Named add-on missing but bind pattern matches AdGuard/localhost stub.
        if has_resolved_stub:
            parts.insert(
                0,
                "systemd-resolved-style stub on loopback",
            )
        else:
            parts.append(
                "bound only on localhost "
                + ", ".join(addresses)
                + " (typical of AdGuard Home)"
            )

    # Deduplicate while preserving order.
    unique: list[str] = []
    for part in parts:
        if part and part not in unique:
            unique.append(part)
    return "; ".join(unique)


def describe_port_owners(port: int = DNS_PORT) -> dict[str, Any]:
    """Identify what is using a host port (process list + Supervisor add-ons)."""
    socket_owners = _parse_ss_listeners(port)
    if not any(o.get("comm") for o in socket_owners):
        # Merge /proc inode mapping when ss could not name the process.
        for owner in _parse_proc_net_listeners(port):
            if owner.get("comm") or not socket_owners:
                socket_owners.append(owner)
    addon_conflicts = _supervisor_dns_conflicts(port)
    summary = _humanize_port_owner_summary(port, socket_owners, addon_conflicts)
    owners = list(socket_owners) + list(addon_conflicts)
    return {"port": port, "owners": owners, "summary": summary}


def _dns_answers_on(host: str, port: int = DNS_PORT) -> bool:
    # Minimal DNS query (transaction 0xaaaa, A for ".")
    query = b"\xaa\xaa\x01\x00\x00\x01\x00\x00\x00\x00\x00\x00\x00\x00\x01\x00\x01"
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.settimeout(1.0)
            sock.sendto(query, (host, port))
            sock.recvfrom(512)
        return True
    except OSError:
        return False


def dns_port_status(port: int = DNS_PORT) -> dict[str, Any]:
    """Probe DNS on LAN (preferred) and localhost; report owners when Pi-hole cannot serve."""
    ownership = describe_port_owners(port)
    lan_ips = detect_host_ips()
    lan = lan_ips[0] if lan_ips else None
    lan_ok = bool(lan) and _dns_answers_on(lan, port)
    loop_ok = _dns_answers_on("127.0.0.1", port)

    pihole_listeners = [
        o
        for o in ownership["owners"]
        if "pihole" in str(o.get("comm", "")).lower()
        or "ftl" in str(o.get("comm", "")).lower()
        or "pihole" in str(o.get("cmdline", "")).lower()
    ]

    # Success: Pi-hole is serving on the LAN address (even if AdGuard keeps localhost).
    if lan_ok and (pihole_listeners or DNS_LISTENING_MODE.upper() == "BIND" or not loop_ok):
        detail = f"DNS responding on {lan}:{port}"
        if DNS_BIND_INTERFACE:
            detail += f" (listening mode {DNS_LISTENING_MODE or 'BIND'} on {DNS_BIND_INTERFACE})"
        return {
            "port": port,
            "busy": False,
            "detail": detail,
            "owners": ownership["owners"],
            "owner_summary": ownership["summary"],
        }
    if lan_ok or (loop_ok and pihole_listeners):
        return {
            "port": port,
            "busy": False,
            "detail": f"DNS responding on {lan or '127.0.0.1'}:{port}",
            "owners": ownership["owners"],
            "owner_summary": ownership["summary"],
        }
    if loop_ok and not lan_ok:
        detail = (
            f"DNS answers only on 127.0.0.1:{port}, not on LAN"
            + (f" {lan}:{port}" if lan else "")
            + f". Port {port} owner: {ownership['summary']}"
        )
        log.warning("%s", detail)
        return {
            "port": port,
            "busy": True,
            "detail": detail,
            "owners": ownership["owners"],
            "owner_summary": ownership["summary"],
        }

    detail = (
        f"no DNS response on LAN"
        + (f" {lan}:{port}" if lan else "")
        + f" or 127.0.0.1:{port}. Port {port} owner: {ownership['summary']}"
    )
    log.warning("%s", detail)
    return {
        "port": port,
        "busy": True,
        "detail": detail,
        "owners": ownership["owners"],
        "owner_summary": ownership["summary"],
    }


def enrich_dns_failure(result: Any, port: int = DNS_PORT) -> Any:
    """Attach port-owner context when Pi-hole reports DNS is down."""
    ownership = describe_port_owners(port)
    owner_summary = ownership["summary"]
    hint = f"Port {port} is in use by: {owner_summary}"

    if not isinstance(result, dict):
        return {
            "error": {
                "key": "dns_failure",
                "message": str(result) if result is not None else "DNS resolver is not running",
                "hint": hint,
            },
            "port": port,
            "owners": ownership["owners"],
            "owner_summary": owner_summary,
        }

    enriched = dict(result)
    err = enriched.get("error")
    if isinstance(err, dict):
        err = dict(err)
        message = str(err.get("message") or "DNS resolver is not running")
        existing_hint = err.get("hint")
        err["message"] = message
        err["hint"] = f"{existing_hint}; {hint}" if existing_hint else hint
        enriched["error"] = err
    elif err:
        enriched["error"] = {"message": str(err), "hint": hint}
    else:
        enriched["error"] = {
            "key": "dns_failure",
            "message": "DNS resolver is not running",
            "hint": hint,
        }
    enriched["port"] = port
    enriched["owners"] = ownership["owners"]
    enriched["owner_summary"] = owner_summary
    return enriched


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
            port_status = dns_port_status(DNS_PORT)
            self._send_json(
                200,
                {
                    "dns_ips": ips,
                    "dns_port": DNS_PORT,
                    "dns_port_busy": port_status["busy"],
                    "dns_port_detail": port_status["detail"],
                    "dns_port_owners": port_status.get("owners") or [],
                    "dns_port_owner_summary": port_status.get("owner_summary") or "",
                    "dns_listening_mode": DNS_LISTENING_MODE,
                    "dns_bind_interface": DNS_BIND_INTERFACE,
                    "admin_port": ADMIN_PORT,
                    "admin_urls": [f"http://{ip}:{ADMIN_PORT}/admin" for ip in ips],
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
            if status >= 400:
                err_key = ""
                err_msg = ""
                if isinstance(result, dict):
                    err = result.get("error") or {}
                    if isinstance(err, dict):
                        err_key = str(err.get("key") or "")
                        err_msg = str(err.get("message") or "")
                    else:
                        err_msg = str(err)
                dns_down = (
                    err_key == "dns_failure"
                    or "dns resolver is not running" in err_msg.lower()
                    or "dns" in err_msg.lower() and "not running" in err_msg.lower()
                )
                if dns_down or dns_port_status(DNS_PORT)["busy"]:
                    result = enrich_dns_failure(result, DNS_PORT)
                    log.error(
                        "Blocking change failed because DNS is down; port %s owner: %s",
                        DNS_PORT,
                        result.get("owner_summary") if isinstance(result, dict) else result,
                    )
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

    port_status = dns_port_status(DNS_PORT)
    if port_status.get("busy"):
        log.error("Pi-hole DNS is not healthy on port %s: %s", DNS_PORT, port_status.get("detail"))
        for owner in port_status.get("owners") or []:
            log.error("  port %s owner: %s", DNS_PORT, owner.get("detail") or owner)
    else:
        log.info("Pi-hole DNS probe OK on port %s", DNS_PORT)

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
