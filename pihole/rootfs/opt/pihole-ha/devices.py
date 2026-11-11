#!/usr/bin/env python3
"""Discover LAN hosts and publish stable *.local names via Pi-hole local DNS.

Each device is keyed by unique hardware identity (MAC). Default names are derived
from that MAC. User overrides are stored against the same identity so the
published .local name stays put when the IP changes.
"""

from __future__ import annotations

import ipaddress
import json
import logging
import os
import re
import socket
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote as urllib_quote

log = logging.getLogger("pihole-ha-ui")

SCAN_INTERVAL_SEC = int(os.environ.get("PIHOLE_HA_DEVICE_SCAN_INTERVAL", "600"))
MAX_HOSTS_PER_SCAN = int(os.environ.get("PIHOLE_HA_DEVICE_SCAN_MAX", "1024"))
STATE_PATH = Path(os.environ.get("PIHOLE_HA_DEVICE_STATE", "/data/discovered_devices.json"))
HOSTNAME_PREFIX = "lan-"

_state_lock = threading.Lock()
_state: dict[str, Any] = {
    "devices": [],
    "overrides": {},  # device_id -> custom hostname (with .local)
    "last_scan": None,
    "last_error": None,
    "scanning": False,
    "subnets": [],
}
_stop = threading.Event()
_AuthedRequest = Callable[..., tuple[int, Any]]
_HOSTNAME_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.local$")


def _private_only(network: ipaddress.IPv4Network) -> bool:
    return network.version == 4 and network.is_private and not network.is_loopback


def normalize_mac(mac: str | None) -> str | None:
    if not mac:
        return None
    compact = re.sub(r"[^0-9a-fA-F]", "", mac)
    if len(compact) != 12:
        return None
    pairs = [compact[i : i + 2].lower() for i in range(0, 12, 2)]
    if pairs == ["00"] * 6:
        return None
    return ":".join(pairs)


def device_id_for(mac: str | None, ip: str) -> str:
    """Stable unique key: prefer MAC; fall back to IP only when MAC is unknown."""
    normalized = normalize_mac(mac)
    if normalized:
        return f"mac:{normalized}"
    return f"ip:{ip}"


def default_hostname_for(mac: str | None, ip: str) -> str:
    """Static default name built from unique device info (MAC preferred)."""
    normalized = normalize_mac(mac)
    if normalized:
        compact = normalized.replace(":", "")
        return f"{HOSTNAME_PREFIX}{compact[-8:]}.local"
    return f"{HOSTNAME_PREFIX}{ip.replace('.', '-')}.local"


def sanitize_override_hostname(value: str) -> str:
    """Normalize a user override into a valid *.local hostname."""
    raw = (value or "").strip().lower()
    if not raw:
        raise ValueError("hostname is required")
    if not raw.endswith(".local"):
        raw = f"{raw}.local"
    label = raw[: -len(".local")]
    label = re.sub(r"[^a-z0-9-]+", "-", label).strip("-")
    label = re.sub(r"-{2,}", "-", label)
    if not label:
        raise ValueError("hostname must include a DNS label")
    hostname = f"{label[:63]}.local"
    if not _HOSTNAME_RE.match(hostname):
        raise ValueError("hostname must look like name.local (letters, numbers, hyphens)")
    return hostname


def hostname_for(ip: str, mac: str | None, override: str | None = None) -> str:
    """Resolved published name: override (keyed by identity) else MAC-derived default."""
    if override:
        try:
            return sanitize_override_hostname(override)
        except ValueError:
            pass
    return default_hostname_for(mac, ip)


def discover_subnets() -> list[str]:
    """Return connected private IPv4 subnets (CIDR strings), preferring /24 slices."""
    found: set[ipaddress.IPv4Network] = set()
    host_ips: list[ipaddress.IPv4Address] = []

    if __import__("shutil").which("ip"):
        try:
            completed = subprocess.run(
                ["ip", "-4", "-o", "addr", "show", "up"],
                check=False,
                capture_output=True,
                text=True,
                timeout=5,
            )
            for line in completed.stdout.splitlines():
                match = re.search(r"inet\s+(\d+\.\d+\.\d+\.\d+)/(\d+)", line)
                if not match:
                    continue
                addr = ipaddress.IPv4Address(match.group(1))
                prefix = int(match.group(2))
                if addr.is_loopback:
                    continue
                host_ips.append(addr)
                net = ipaddress.IPv4Network(f"{addr}/{prefix}", strict=False)
                if not _private_only(net):
                    continue
                # Cap huge prefixes to the host's /24 to keep scans bounded.
                if prefix < 24:
                    found.add(ipaddress.IPv4Network(f"{addr}/24", strict=False))
                else:
                    found.add(net)
        except (OSError, subprocess.TimeoutExpired, ValueError) as exc:
            log.debug("ip addr subnet discovery failed: %s", exc)

        try:
            completed = subprocess.run(
                ["ip", "-4", "route", "show"],
                check=False,
                capture_output=True,
                text=True,
                timeout=5,
            )
            for line in completed.stdout.splitlines():
                if " link " not in f" {line} " and "proto kernel" not in line and "scope link" not in line:
                    if not line.strip():
                        continue
                match = re.match(r"(\d+\.\d+\.\d+\.\d+/\d+)\s+dev\s+", line)
                if not match:
                    continue
                try:
                    net = ipaddress.IPv4Network(match.group(1), strict=False)
                except ValueError:
                    continue
                if not _private_only(net):
                    continue
                if net.prefixlen < 24:
                    for host in host_ips:
                        if host in net:
                            found.add(ipaddress.IPv4Network(f"{host}/24", strict=False))
                else:
                    found.add(net)
        except (OSError, subprocess.TimeoutExpired, ValueError) as exc:
            log.debug("ip route subnet discovery failed: %s", exc)

    if not found:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.connect(("1.1.1.1", 80))
                addr = ipaddress.IPv4Address(sock.getsockname()[0])
            if addr.is_private and not addr.is_loopback:
                found.add(ipaddress.IPv4Network(f"{addr}/24", strict=False))
        except OSError:
            pass

    return sorted(str(net) for net in found)


def _read_arp_table() -> dict[str, str]:
    """Map IP -> MAC from /proc/net/arp."""
    mapping: dict[str, str] = {}
    path = Path("/proc/net/arp")
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()[1:]
    except OSError:
        return mapping
    for line in lines:
        parts = line.split()
        if len(parts) < 4:
            continue
        ip, _hw, flag, mac = parts[0], parts[1], parts[2], parts[3]
        normalized = normalize_mac(mac)
        if not normalized:
            continue
        try:
            if int(flag, 16) == 0:
                continue
        except ValueError:
            pass
        mapping[ip] = normalized
    return mapping


def _probe_host(ip: str) -> bool:
    """Cheap reachability check: ICMP ping if available, else TCP connect probes."""
    if __import__("shutil").which("ping"):
        try:
            completed = subprocess.run(
                ["ping", "-c", "1", "-W", "1", ip],
                check=False,
                capture_output=True,
                timeout=2,
            )
            if completed.returncode == 0:
                return True
        except (OSError, subprocess.TimeoutExpired):
            pass
    for port in (80, 443, 22, 8080, 5353):
        try:
            with socket.create_connection((ip, port), timeout=0.35):
                return True
        except OSError:
            continue
    return False


def scan_network() -> dict[str, Any]:
    """Scan discovered private subnets and return device inventory (pre-merge)."""
    subnets = discover_subnets()
    targets: list[str] = []
    for cidr in subnets:
        net = ipaddress.IPv4Network(cidr, strict=False)
        hosts = list(net.hosts())
        if len(targets) + len(hosts) > MAX_HOSTS_PER_SCAN:
            remaining = MAX_HOSTS_PER_SCAN - len(targets)
            targets.extend(str(h) for h in hosts[:remaining])
            break
        targets.extend(str(h) for h in hosts)

    log.info("Device scan targeting %s host(s) across %s subnet(s)", len(targets), len(subnets))
    alive: set[str] = set()
    if targets:
        workers = min(64, max(8, len(targets) // 4 or 8))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(_probe_host, ip): ip for ip in targets}
            for future in as_completed(futures):
                ip = futures[future]
                try:
                    if future.result():
                        alive.add(ip)
                except Exception:  # noqa: BLE001
                    continue

    arp = _read_arp_table()
    for ip, mac in arp.items():
        try:
            addr = ipaddress.IPv4Address(ip)
        except ValueError:
            continue
        if not addr.is_private or addr.is_loopback:
            continue
        if any(addr in ipaddress.IPv4Network(cidr, strict=False) for cidr in subnets):
            alive.add(ip)

    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    discovered: list[dict[str, Any]] = []
    for ip in sorted(alive, key=lambda value: tuple(int(p) for p in value.split("."))):
        mac = arp.get(ip)
        discovered.append(
            {
                "ip": ip,
                "mac": mac,
                "id": device_id_for(mac, ip),
                "last_seen": now,
                "source": "arp" if mac else "probe",
            }
        )
    return {"discovered": discovered, "subnets": subnets, "scanned_hosts": len(targets), "last_scan": now}


def _snapshot_locked() -> dict[str, Any]:
    return {
        "devices": list(_state["devices"]),
        "overrides": dict(_state["overrides"]),
        "last_scan": _state["last_scan"],
        "last_error": _state["last_error"],
        "scanning": _state["scanning"],
        "subnets": list(_state["subnets"]),
        "interval_sec": SCAN_INTERVAL_SEC,
    }


def _apply_names(devices: list[dict[str, Any]], overrides: dict[str, str]) -> list[dict[str, Any]]:
    named: list[dict[str, Any]] = []
    for device in devices:
        device_id = str(device.get("id") or device_id_for(device.get("mac"), str(device.get("ip") or "")))
        mac = normalize_mac(device.get("mac"))
        ip = str(device.get("ip") or "")
        default_name = default_hostname_for(mac, ip)
        override = overrides.get(device_id)
        hostname = hostname_for(ip, mac, override)
        named.append(
            {
                **device,
                "id": device_id,
                "mac": mac,
                "default_hostname": default_name,
                "hostname": hostname,
                "override": override,
                "stable": device_id.startswith("mac:"),
            }
        )
    named.sort(key=lambda d: tuple(int(p) for p in str(d.get("ip") or "0.0.0.0").split(".")))
    return named


def _persist_locked() -> None:
    payload = {
        "devices": _state["devices"],
        "overrides": _state["overrides"],
        "subnets": _state["subnets"],
        "last_scan": _state["last_scan"],
    }
    try:
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        STATE_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    except OSError as exc:
        log.warning("Could not persist device state: %s", exc)


def load_state() -> dict[str, Any]:
    with _state_lock:
        if STATE_PATH.is_file():
            try:
                loaded = json.loads(STATE_PATH.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    devices = loaded.get("devices")
                    overrides = loaded.get("overrides")
                    if isinstance(devices, list):
                        _state["devices"] = devices
                    if isinstance(overrides, dict):
                        cleaned: dict[str, str] = {}
                        for key, value in overrides.items():
                            try:
                                cleaned[str(key)] = sanitize_override_hostname(str(value))
                            except ValueError:
                                continue
                        _state["overrides"] = cleaned
                    _state["last_scan"] = loaded.get("last_scan")
                    _state["subnets"] = loaded.get("subnets") or []
                    # Re-apply names in case overrides changed offline.
                    _state["devices"] = _apply_names(_state["devices"], _state["overrides"])
            except (OSError, json.JSONDecodeError) as exc:
                log.warning("Could not load device state: %s", exc)
        return _snapshot_locked()


def merge_discovered(
    discovered: list[dict[str, Any]],
    previous: list[dict[str, Any]],
    overrides: dict[str, str],
    last_scan: str,
) -> list[dict[str, Any]]:
    """Merge scan results by unique id so IP changes keep the same name."""
    previous_by_id = {str(d.get("id")): d for d in previous if d.get("id")}
    # Also index previous IP-only rows so a later MAC can adopt them.
    previous_by_ip = {str(d.get("ip")): d for d in previous if d.get("ip")}

    merged_ids: set[str] = set()
    merged: list[dict[str, Any]] = []

    for row in discovered:
        mac = normalize_mac(row.get("mac"))
        ip = str(row.get("ip") or "")
        device_id = device_id_for(mac, ip)
        prior = previous_by_id.get(device_id)
        if not prior and mac and ip in previous_by_ip:
            # Promote an earlier IP-only identity once MAC is known.
            prior = previous_by_ip[ip]
            old_id = str(prior.get("id") or "")
            if old_id.startswith("ip:") and old_id in overrides and device_id not in overrides:
                overrides[device_id] = overrides.pop(old_id)
        entry = {
            "id": device_id,
            "ip": ip,
            "mac": mac,
            "last_seen": last_scan,
            "source": row.get("source") or ("arp" if mac else "probe"),
            "online": True,
        }
        merged.append(entry)
        merged_ids.add(device_id)

    # Keep previously known devices (static DNS) even if offline this pass.
    for device_id, prior in previous_by_id.items():
        if device_id in merged_ids:
            continue
        merged.append(
            {
                "id": device_id,
                "ip": prior.get("ip"),
                "mac": normalize_mac(prior.get("mac")),
                "last_seen": prior.get("last_seen"),
                "source": prior.get("source") or "cache",
                "online": False,
            }
        )

    return _apply_names(merged, overrides)


def _parse_hosts_payload(payload: Any) -> list[tuple[str, str]]:
    """Normalize Pi-hole dns.hosts config into (ip, hostname) pairs."""
    rows: list[tuple[str, str]] = []
    config = payload.get("config") if isinstance(payload, dict) else None
    hosts = None
    if isinstance(config, dict):
        dns = config.get("dns") if isinstance(config.get("dns"), dict) else {}
        hosts = dns.get("hosts")
        if hosts is None and isinstance(config.get("dns.hosts"), list):
            hosts = config.get("dns.hosts")
    if hosts is None and isinstance(payload, dict):
        hosts = payload.get("hosts") or payload.get("dns.hosts")
    if not isinstance(hosts, list):
        return rows
    for item in hosts:
        if isinstance(item, str):
            parts = item.split()
            if len(parts) >= 2:
                rows.append((parts[0], parts[1]))
        elif isinstance(item, dict):
            ip = str(item.get("ip") or item.get("address") or "")
            name = str(item.get("name") or item.get("domain") or item.get("hostname") or "")
            if ip and name:
                rows.append((ip, name))
    return rows


def _is_managed_hostname(hostname: str, managed_names: set[str], defaults: set[str]) -> bool:
    host = hostname.lower()
    if host in managed_names or host in defaults:
        return True
    return host.startswith(HOSTNAME_PREFIX) and host.endswith(".local")


def sync_local_dns(authed_request: _AuthedRequest, devices: list[dict[str, Any]]) -> dict[str, Any]:
    """Create/update Pi-hole local DNS hosts for discovered devices."""
    status, payload = authed_request("GET", "/api/config/dns/hosts")
    existing = _parse_hosts_payload(payload) if status < 400 else []
    existing_by_host = {host.lower(): ip for ip, host in existing}

    desired = {
        str(d["hostname"]).lower(): str(d["ip"])
        for d in devices
        if d.get("hostname") and d.get("ip")
    }
    defaults = {str(d.get("default_hostname") or "").lower() for d in devices if d.get("default_hostname")}
    managed_names = set(desired) | defaults | {
        str(d.get("override") or "").lower() for d in devices if d.get("override")
    }

    added: list[str] = []
    updated: list[str] = []
    removed: list[str] = []
    failed: list[dict[str, str]] = []

    # Remove stale managed names that are no longer desired.
    for host, ip in list(existing_by_host.items()):
        if host in desired:
            continue
        if not _is_managed_hostname(host, managed_names, defaults):
            continue
        encoded = urllib_quote(f"{ip} {host}")
        del_status, del_payload = authed_request("DELETE", f"/api/config/dns/hosts/{encoded}")
        if del_status < 400:
            removed.append(host)
            existing_by_host.pop(host, None)
        else:
            failed.append({"hostname": host, "error": str(del_payload)})

    for host, ip in desired.items():
        previous = existing_by_host.get(host)
        if previous == ip:
            continue
        if previous and previous != ip:
            encoded_old = urllib_quote(f"{previous} {host}")
            authed_request("DELETE", f"/api/config/dns/hosts/{encoded_old}")
        encoded = urllib_quote(f"{ip} {host}")
        put_status, put_payload = authed_request("PUT", f"/api/config/dns/hosts/{encoded}")
        if put_status < 400:
            if previous:
                updated.append(host)
            else:
                added.append(host)
        else:
            failed.append({"hostname": host, "error": str(put_payload)})

    return {
        "added": added,
        "updated": updated,
        "removed": removed,
        "failed": failed,
        "managed": len(desired),
    }


def run_scan_and_sync(authed_request: _AuthedRequest) -> dict[str, Any]:
    with _state_lock:
        if _state["scanning"]:
            snapshot = _snapshot_locked()
            return {"ok": False, "error": "scan already running", **snapshot}
        _state["scanning"] = True
        _state["last_error"] = None

    try:
        result = scan_network()
        with _state_lock:
            previous = list(_state["devices"])
            overrides = dict(_state["overrides"])
            devices = merge_discovered(result["discovered"], previous, overrides, result["last_scan"])
            _state["overrides"] = overrides
            _state["devices"] = devices
            _state["subnets"] = result["subnets"]
            _state["last_scan"] = result["last_scan"]
            _persist_locked()
            snapshot_devices = list(devices)

        sync = sync_local_dns(authed_request, snapshot_devices)
        log.info(
            "Device scan done devices=%s added=%s updated=%s removed=%s failed=%s",
            len(snapshot_devices),
            len(sync["added"]),
            len(sync["updated"]),
            len(sync["removed"]),
            len(sync["failed"]),
        )
        with _state_lock:
            return {"ok": True, "scan": {"subnets": result["subnets"], "scanned_hosts": result["scanned_hosts"], "last_scan": result["last_scan"]}, "sync": sync, **_snapshot_locked()}
    except Exception as exc:  # noqa: BLE001
        log.exception("Device scan failed: %s", exc)
        with _state_lock:
            _state["last_error"] = str(exc)
            return {"ok": False, "error": str(exc), **_snapshot_locked()}
    finally:
        with _state_lock:
            _state["scanning"] = False


def set_device_hostname(device_id: str, hostname: str | None, authed_request: _AuthedRequest) -> dict[str, Any]:
    """Override (or clear) the .local name for a device identity."""
    device_id = str(device_id or "").strip()
    if not device_id:
        return {"ok": False, "error": "device_id is required"}

    with _state_lock:
        devices = list(_state["devices"])
        match = next((d for d in devices if str(d.get("id")) == device_id), None)
        if not match:
            return {"ok": False, "error": "unknown device"}

        if hostname is None or str(hostname).strip() == "":
            _state["overrides"].pop(device_id, None)
            applied = None
        else:
            try:
                applied = sanitize_override_hostname(str(hostname))
            except ValueError as exc:
                return {"ok": False, "error": str(exc)}
            # Prevent two devices sharing one override name.
            for other_id, other_name in list(_state["overrides"].items()):
                if other_id != device_id and other_name == applied:
                    return {"ok": False, "error": f"{applied} is already assigned to another device"}
            for other in devices:
                if str(other.get("id")) == device_id:
                    continue
                if str(other.get("hostname") or "").lower() == applied:
                    return {"ok": False, "error": f"{applied} is already in use"}
            _state["overrides"][device_id] = applied

        _state["devices"] = _apply_names(devices, _state["overrides"])
        _persist_locked()
        snapshot_devices = list(_state["devices"])

    sync = sync_local_dns(authed_request, snapshot_devices)
    with _state_lock:
        return {
            "ok": True,
            "hostname": next((d.get("hostname") for d in _state["devices"] if d.get("id") == device_id), applied),
            "override": _state["overrides"].get(device_id),
            "sync": sync,
            **_snapshot_locked(),
        }


def start_background_scanner(authed_request: _AuthedRequest) -> None:
    """Start the every-10-minute discovery loop."""
    load_state()

    def _loop() -> None:
        # Small delay so Pi-hole API auth is ready after startup.
        if _stop.wait(20):
            return
        while not _stop.is_set():
            run_scan_and_sync(authed_request)
            if _stop.wait(SCAN_INTERVAL_SEC):
                break

    thread = threading.Thread(target=_loop, name="device-scanner", daemon=True)
    thread.start()
    log.info("Local device scanner started (interval=%ss)", SCAN_INTERVAL_SEC)


def stop_background_scanner() -> None:
    _stop.set()
