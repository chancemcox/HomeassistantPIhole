#!/bin/sh
set -eu
target=127.0.0.1
# Read persisted settings: the image's default FTLCONF environment differs
# from the running entrypoint's automatic BIND override.
iface="$(python3 -c 'import tomllib; c=tomllib.load(open("/etc/pihole/pihole.toml", "rb"))["dns"]; print(c["interface"] if c["listeningMode"] == "BIND" else "")')"
if [ -n "$iface" ]; then
  target="$(ip -4 -o addr show dev "$iface" | awk '{split($4,a,"/"); print a[1]; exit}')"
  [ -n "$target" ] || exit 1
fi
exec dig +short +norecurse +retry=0 +time=2 "@$target" pi.hole
