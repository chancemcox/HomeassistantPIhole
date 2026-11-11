#!/bin/bash
# Run inside the built image, with --entrypoint bash, in an isolated network.
set -euo pipefail
python3 -u - <<'PY' &
import socket
import time
tcp = socket.socket()
tcp.bind(('127.0.0.1', 53))
tcp.listen()
udp = socket.socket(type=socket.SOCK_DGRAM)
udp.bind(('127.0.0.1', 53))
open('/tmp/loopback-ready', 'w').close()
time.sleep(120)
PY
peer=$!
trap 'kill "$peer" "${app:-}" 2>/dev/null || true' EXIT
for i in {1..20}; do
  [ -f /tmp/loopback-ready ] && break
  sleep 0.2
done
[ -f /tmp/loopback-ready ]
/ha-entrypoint.sh >/tmp/pihole-test.log 2>&1 &
app=$!
for i in {1..60}; do
  if grep -q 'FTL started' /tmp/pihole-test.log && /opt/pihole-ha/healthcheck.sh; then
    kill -0 "$peer"
    ! grep -q 'failed to create listening socket' /tmp/pihole-test.log
    echo 'LAN DNS works while localhost TCP/UDP port 53 stays occupied'
    exit 0
  fi
  sleep 1
done
cat /tmp/pihole-test.log
exit 1
