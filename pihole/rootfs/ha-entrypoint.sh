#!/bin/bash
set -euo pipefail

OPTIONS_FILE="/data/options.json"
PASSWORD_FILE="/data/pihole-password"
PIHOLE_DATA="/data/etc-pihole"

log() {
  echo "[pihole-ha] $*"
}

# Persist Pi-hole state across add-on rebuilds.
mkdir -p "${PIHOLE_DATA}"
if [[ -d /etc/pihole && ! -L /etc/pihole ]]; then
  # Seed empty persistent store from image defaults once.
  if [[ -z "$(ls -A "${PIHOLE_DATA}" 2>/dev/null || true)" ]]; then
    cp -a /etc/pihole/. "${PIHOLE_DATA}/" 2>/dev/null || true
  fi
  rm -rf /etc/pihole
fi
ln -sfn "${PIHOLE_DATA}" /etc/pihole

# Defaults when options.json is missing (local docker runs).
PASSWORD=""
UPSTREAM_DNS="1.1.1.1;1.0.0.1"
DNS_LISTENING_MODE="ALL"
INTERFACE_THEME="default"
QUERY_LOGGING="true"
TIMEZONE="America/Chicago"

if [[ -f "${OPTIONS_FILE}" ]]; then
  PASSWORD="$(jq -r '.password // empty' "${OPTIONS_FILE}")"
  UPSTREAM_DNS="$(jq -r '(.upstream_dns // ["1.1.1.1","1.0.0.1"]) | join(";")' "${OPTIONS_FILE}")"
  DNS_LISTENING_MODE="$(jq -r '.dns_listening_mode // "ALL"' "${OPTIONS_FILE}")"
  INTERFACE_THEME="$(jq -r '.interface_theme // "default"' "${OPTIONS_FILE}")"
  QUERY_LOGGING="$(jq -r '.query_logging // true' "${OPTIONS_FILE}")"
  TIMEZONE="$(jq -r '.timezone // "America/Chicago"' "${OPTIONS_FILE}")"
fi

if [[ -z "${PASSWORD}" ]]; then
  if [[ -f "${PASSWORD_FILE}" ]]; then
    PASSWORD="$(cat "${PASSWORD_FILE}")"
    log "Reusing previously generated admin password from ${PASSWORD_FILE}"
  else
    PASSWORD="$(tr -dc 'A-Za-z0-9' </dev/urandom | head -c 24)"
    printf '%s' "${PASSWORD}" > "${PASSWORD_FILE}"
    chmod 600 "${PASSWORD_FILE}"
    log "Generated admin password (also saved to add-on data): ${PASSWORD}"
  fi
else
  printf '%s' "${PASSWORD}" > "${PASSWORD_FILE}"
  chmod 600 "${PASSWORD_FILE}"
fi

export TZ="${TIMEZONE}"
export FTLCONF_webserver_api_password="${PASSWORD}"
export FTLCONF_dns_upstreams="${UPSTREAM_DNS}"
export FTLCONF_dns_listeningMode="${DNS_LISTENING_MODE}"
export FTLCONF_webserver_interface_theme="${INTERFACE_THEME}"
export FTLCONF_dns_queryLogging="${QUERY_LOGGING}"
export FTLCONF_webserver_port="${FTLCONF_webserver_port:-8080}"
export PIHOLE_HA_PASSWORD="${PASSWORD}"
export PIHOLE_HA_UI_PORT="${PIHOLE_HA_UI_PORT:-8099}"
export PIHOLE_API_BASE="${PIHOLE_API_BASE:-http://127.0.0.1:8080}"

log "Upstream DNS: ${UPSTREAM_DNS}"
log "Listening mode: ${DNS_LISTENING_MODE}"
log "Timezone: ${TZ}"
log "Starting management UI on port ${PIHOLE_HA_UI_PORT}"
log "Native Pi-hole admin available on port ${FTLCONF_webserver_port}"

# Start Home Assistant management UI (Ingress).
python3 /opt/pihole-ha/server.py &
UI_PID=$!

cleanup() {
  log "Shutting down management UI (pid ${UI_PID})"
  kill "${UI_PID}" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

# Hand off to the official Pi-hole container entrypoint.
exec start.sh
