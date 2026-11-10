#!/usr/bin/env python3
"""Resolve the Supervisor-assigned Ingress listen port for host_network apps."""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request


DEFAULT_BASES = (
    "http://172.30.32.2",
    "http://supervisor",
    "http://hassio",
)


def resolve_ingress_port(
    token: str | None = None,
    bases: tuple[str, ...] | list[str] | None = None,
    timeout: float = 3.0,
) -> int:
    token = token or os.environ.get("SUPERVISOR_TOKEN") or os.environ.get("HASSIO_TOKEN") or ""
    if not token:
        raise RuntimeError("SUPERVISOR_TOKEN is not set")

    bases = tuple(bases or DEFAULT_BASES)
    headers = {"Authorization": f"Bearer {token}"}
    errors: list[str] = []

    for base in bases:
        url = f"{base.rstrip('/')}/addons/self/info"
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as response:
                payload = json.load(response)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{url}: {exc}")
            continue

        data = payload.get("data") or payload
        port = data.get("ingress_port")
        if isinstance(port, int) and port > 0:
            return port
        errors.append(f"{url}: ingress_port={port!r}")

    for key in ("INGRESS_PORT", "ADDON_INGRESS_PORT"):
        raw = (os.environ.get(key) or "").strip()
        if raw.isdigit() and int(raw) > 0:
            return int(raw)

    raise RuntimeError("; ".join(errors) or "ingress_port unavailable")


def main() -> int:
    try:
        port = resolve_ingress_port()
    except Exception as exc:  # noqa: BLE001
        print(str(exc), file=sys.stderr)
        return 1
    print(port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
