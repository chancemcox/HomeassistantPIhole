#!/usr/bin/env python3
"""Decide how Pi-hole should bind DNS when host port 53 is partly occupied.

Prints one of:
  FREE
  LOOPBACK_ONLY iface=<name> ip=<addr>
  CONFLICT summary=<text>
"""

from __future__ import annotations

import re
import shutil
import socket
import subprocess
import sys


PORT = 53


def _ss_lines() -> list[str]:
    """Return normalized `ss -lntu` rows that mention port 53."""
    if not shutil.which("ss"):
        return []
    try:
        completed = subprocess.run(
            ["ss", "-lntu"],
            check=False,
            capture_output=True,
            text=True,
            timeout=3,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    lines = []
    for line in completed.stdout.splitlines():
        if re.search(rf":{PORT}\b", line):
            lines.append(" ".join(line.split()))
    return lines


def _local_addrs(lines: list[str]) -> set[str]:
    """Extract listener addresses from ss rows, including `%zone` suffixes."""
    addrs: set[str] = set()
    for line in lines:
        match = re.search(rf"\[([^\]]+)\](?:%[^\]\s]+)?:{PORT}\b", line)
        if match:
            addrs.add(match.group(1).lower())
            continue
        match = re.search(rf"(\d+\.\d+\.\d+\.\d+)(?:%\S+)?:{PORT}\b", line)
        if match:
            addrs.add(match.group(1))
            continue
        if re.search(rf"\*:{PORT}\b|0\.0\.0\.0:{PORT}\b", line):
            addrs.add("0.0.0.0")
            continue
        # Unparsed :53 listener — treat as non-loopback so we do not claim FREE.
        addrs.add("unparsed")
    return addrs


def _is_loopback(addr: str) -> bool:
    """Return True when addr is a loopback / resolved-stub address."""
    a = addr.lower()
    return a in {"127.0.0.1", "127.0.0.53", "127.0.0.54", "::1", "localhost"} or a.startswith(
        "127."
    )


def _primary_lan() -> tuple[str | None, str | None]:
    """Return (iface, ipv4) for the default egress route."""
    if shutil.which("ip"):
        try:
            completed = subprocess.run(
                ["ip", "-o", "route", "get", "1.1.1.1"],
                check=False,
                capture_output=True,
                text=True,
                timeout=3,
            )
            text = completed.stdout.strip()
            iface = None
            ip_addr = None
            match = re.search(r"\bdev\s+(\S+)", text)
            if match:
                iface = match.group(1)
            match = re.search(r"\bsrc\s+(\S+)", text)
            if match:
                ip_addr = match.group(1)
            if iface and ip_addr and not ip_addr.startswith("127."):
                return iface, ip_addr
        except (OSError, subprocess.TimeoutExpired):
            pass
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("1.1.1.1", 80))
            ip_addr = sock.getsockname()[0]
            if ip_addr and not ip_addr.startswith("127."):
                return None, ip_addr
    except OSError:
        pass
    return None, None


def plan() -> str:
    """Classify port-53 occupancy for the HA entrypoint."""
    lines = _ss_lines()
    if not lines:
        return "FREE"
    addrs = _local_addrs(lines)
    if not addrs:
        return "FREE"

    if any(not _is_loopback(a) for a in addrs):
        # Wildcard, LAN, or unparsed listener — Pi-hole cannot safely share port 53.
        summary = ", ".join(sorted(f"{a}:{PORT}" for a in addrs))
        return f"CONFLICT summary={summary}"

    iface, ip_addr = _primary_lan()
    if not ip_addr:
        return "CONFLICT summary=loopback-only but no LAN IP detected"
    # FTL BIND mode needs a real interface name; leave iface empty when unknown.
    return f"LOOPBACK_ONLY iface={iface or ''} ip={ip_addr}"


def main() -> int:
    """CLI entrypoint used by ha-entrypoint.sh."""
    print(plan())
    return 0


if __name__ == "__main__":
    sys.exit(main())
