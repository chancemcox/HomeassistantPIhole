#!/bin/bash
# Home Assistant wrapper around the official Pi-hole container entrypoint.
set -uo pipefail

OPTIONS_FILE="/data/options.json"
PASSWORD_FILE="/data/pihole-password"
PIHOLE_DATA="/data/etc-pihole"
LOG_LEVEL="info"

# Numeric priorities: trace < debug < info < warning < error
log_level_num() {
  case "${1,,}" in
    trace) echo 10 ;;
    debug) echo 20 ;;
    info) echo 30 ;;
    warning|warn) echo 40 ;;
    error) echo 50 ;;
    *) echo 30 ;;
  esac
}

CURRENT_LEVEL_NUM=30

log_at() {
  local level="$1"
  shift
  local level_num
  level_num="$(log_level_num "${level}")"
  if (( level_num >= CURRENT_LEVEL_NUM )); then
    printf '[pihole-ha] [%s] %s\n' "${level^^}" "$*"
  fi
}

log_trace() { log_at trace "$@"; }
log_debug() { log_at debug "$@"; }
log_info() { log_at info "$@"; }
log_warn() { log_at warning "$@"; }
log_error() { log_at error "$@"; }

die() {
  log_error "$@"
  exit 1
}

generate_password() {
  # Avoid SIGPIPE+pipefail abort from `tr | head`.
  local pw=""
  while ((${#pw} < 24)); do
    pw+=$(dd if=/dev/urandom bs=32 count=1 status=none 2>/dev/null | tr -dc 'A-Za-z0-9' || true)
  done
  printf '%s' "${pw:0:24}"
}

log_info "Entrypoint starting (pid=$$, argv=$*)"
log_info "Container time: $(date -Is 2>/dev/null || date)"
log_debug "PATH=${PATH:-}"
log_debug "Working directory: $(pwd)"
log_trace "Environment snapshot (selected): TZ=${TZ:-} FTLCONF_webserver_port=${FTLCONF_webserver_port:-} PIHOLE_HA_UI_PORT=${PIHOLE_HA_UI_PORT:-}"

if [[ ! -d /data ]]; then
  log_warn "/data is missing; creating it for local/dev runs"
  mkdir -p /data || die "Unable to create /data"
fi

log_debug "Ensuring persistent Pi-hole data directory at ${PIHOLE_DATA}"
mkdir -p "${PIHOLE_DATA}" || die "Unable to create ${PIHOLE_DATA}"

if [[ -d /etc/pihole && ! -L /etc/pihole ]]; then
  log_info "Found image /etc/pihole directory; preparing persistent symlink"
  if [[ -z "$(ls -A "${PIHOLE_DATA}" 2>/dev/null || true)" ]]; then
    log_info "Persistent store is empty; seeding from image defaults"
    if cp -a /etc/pihole/. "${PIHOLE_DATA}/"; then
      log_debug "Seed copy completed"
    else
      log_warn "Seed copy returned non-zero; continuing with empty store"
    fi
  else
    log_debug "Persistent store already has content; skipping seed"
  fi
  log_debug "Removing image /etc/pihole so it can be replaced by symlink"
  rm -rf /etc/pihole || die "Unable to remove /etc/pihole"
fi

ln -sfn "${PIHOLE_DATA}" /etc/pihole || die "Unable to link /etc/pihole -> ${PIHOLE_DATA}"
log_info "Pi-hole config path: /etc/pihole -> $(readlink -f /etc/pihole 2>/dev/null || echo "${PIHOLE_DATA}")"

PASSWORD=""
UPSTREAM_DNS="1.1.1.1;1.0.0.1"
DNS_LISTENING_MODE="ALL"
INTERFACE_THEME="default-auto"
QUERY_LOGGING="true"
TIMEZONE="America/Chicago"
BLOCK_HULU_ADS="false"

if [[ -f "${OPTIONS_FILE}" ]]; then
  log_info "Loading add-on options from ${OPTIONS_FILE}"
  if ! LOG_LEVEL="$(jq -r '.log_level // "info"' "${OPTIONS_FILE}")"; then
    die "Failed to parse log_level from ${OPTIONS_FILE}"
  fi
  CURRENT_LEVEL_NUM="$(log_level_num "${LOG_LEVEL}")"
  log_info "Log level set to '${LOG_LEVEL}' (priority ${CURRENT_LEVEL_NUM})"
  if (( CURRENT_LEVEL_NUM <= 20 )); then
    # Redact password value when dumping options.
    log_debug "options.json (redacted): $(jq 'if .password then .password="***" else . end' "${OPTIONS_FILE}" 2>/dev/null || echo '<unreadable>')"
  fi
  if ! PASSWORD="$(jq -r '.password // empty' "${OPTIONS_FILE}")"; then
    die "Failed to parse password from ${OPTIONS_FILE}"
  fi
  if ! UPSTREAM_DNS="$(jq -r '(.upstream_dns // ["1.1.1.1","1.0.0.1"]) | join(";")' "${OPTIONS_FILE}")"; then
    die "Failed to parse upstream_dns from ${OPTIONS_FILE}"
  fi
  if ! DNS_LISTENING_MODE="$(jq -r '.dns_listening_mode // "ALL"' "${OPTIONS_FILE}")"; then
    die "Failed to parse dns_listening_mode from ${OPTIONS_FILE}"
  fi
  if ! INTERFACE_THEME="$(jq -r '.interface_theme // "default-auto"' "${OPTIONS_FILE}")"; then
    die "Failed to parse interface_theme from ${OPTIONS_FILE}"
  fi
  # Migrate legacy option values to Pi-hole v6 theme names.
  case "${INTERFACE_THEME}" in
    default|auto) INTERFACE_THEME="default-auto" ;;
  esac
  if ! QUERY_LOGGING="$(jq -r '.query_logging // true' "${OPTIONS_FILE}")"; then
    die "Failed to parse query_logging from ${OPTIONS_FILE}"
  fi
  if ! TIMEZONE="$(jq -r '.timezone // "America/Chicago"' "${OPTIONS_FILE}")"; then
    die "Failed to parse timezone from ${OPTIONS_FILE}"
  fi
  if ! BLOCK_HULU_ADS="$(jq -r '.block_hulu_ads // false' "${OPTIONS_FILE}")"; then
    die "Failed to parse block_hulu_ads from ${OPTIONS_FILE}"
  fi
else
  log_warn "${OPTIONS_FILE} not found; using built-in defaults (local/dev mode)"
  CURRENT_LEVEL_NUM="$(log_level_num "${LOG_LEVEL}")"
  log_info "Log level set to '${LOG_LEVEL}' (priority ${CURRENT_LEVEL_NUM})"
fi

if [[ -z "${PASSWORD}" ]]; then
  if [[ -f "${PASSWORD_FILE}" ]]; then
    PASSWORD="$(cat "${PASSWORD_FILE}")"
    log_info "Reusing previously generated admin password from ${PASSWORD_FILE}"
    log_debug "Password length: ${#PASSWORD}"
  else
    log_info "No password configured; generating a random admin password"
    PASSWORD="$(generate_password)"
    printf '%s' "${PASSWORD}" > "${PASSWORD_FILE}"
    chmod 600 "${PASSWORD_FILE}"
    log_info "Generated admin password (saved under add-on data): ${PASSWORD}"
  fi
else
  printf '%s' "${PASSWORD}" > "${PASSWORD_FILE}"
  chmod 600 "${PASSWORD_FILE}"
  log_info "Using admin password from add-on options (length=${#PASSWORD})"
fi

# Ingress listens on a fixed unique port (config.yaml ingress_port: 8189).
# Do not use 8099 — that is Home Assistant's default and collides with other
# host_network apps, which produced sidebar 502 / wrong-app symptoms.
export TZ="${TIMEZONE}"
export FTLCONF_webserver_api_password="${PASSWORD}"
export FTLCONF_dns_upstreams="${UPSTREAM_DNS}"
export FTLCONF_dns_listeningMode="${DNS_LISTENING_MODE}"
export FTLCONF_webserver_interface_theme="${INTERFACE_THEME}"
export FTLCONF_dns_queryLogging="${QUERY_LOGGING}"
export FTLCONF_webserver_port="${FTLCONF_webserver_port:-8088}"
# HA hosts usually already run NTP; Pi-hole's NTP server collides on :123.
export FTLCONF_ntp_ipv4_active="${FTLCONF_ntp_ipv4_active:-false}"
export FTLCONF_ntp_ipv6_active="${FTLCONF_ntp_ipv6_active:-false}"
export PIHOLE_HA_PASSWORD="${PASSWORD}"
export PIHOLE_HA_UI_PORT="${PIHOLE_HA_UI_PORT:-8189}"
export PIHOLE_API_BASE="${PIHOLE_API_BASE:-http://127.0.0.1:8088}"
export PIHOLE_HA_LOG_LEVEL="${LOG_LEVEL}"
export PIHOLE_HA_BLOCK_HULU_ADS="${BLOCK_HULU_ADS}"

if [[ "${PIHOLE_HA_UI_PORT}" == "8099" ]]; then
  log_warn "PIHOLE_HA_UI_PORT=8099 collides with other host_network Ingress apps; prefer 8189"
fi
log_info "Management UI will bind to Ingress port ${PIHOLE_HA_UI_PORT}"

# Map add-on log level onto Pi-hole FTL debug flags for deeper diagnostics.
case "${LOG_LEVEL,,}" in
  trace)
    export FTLCONF_debug_api="true"
    export FTLCONF_debug_database="true"
    export FTLCONF_debug_netlink="true"
    export FTLCONF_debug_queries="true"
    export FTLCONF_debug_flags="true"
    log_debug "Enabled FTL debug flags for trace logging"
    ;;
  debug)
    export FTLCONF_debug_api="true"
    export FTLCONF_debug_database="true"
    log_debug "Enabled FTL API/database debug flags"
    ;;
esac

log_info "Configuration summary:"
log_info "  upstream_dns=${UPSTREAM_DNS}"
log_info "  dns_listening_mode=${DNS_LISTENING_MODE}"
log_info "  interface_theme=${INTERFACE_THEME}"
log_info "  query_logging=${QUERY_LOGGING}"
log_info "  timezone=${TZ}"
log_info "  webserver_port=${FTLCONF_webserver_port}"
log_info "  ui_port=${PIHOLE_HA_UI_PORT}"
log_info "  api_base=${PIHOLE_API_BASE}"
log_info "  block_hulu_ads=${BLOCK_HULU_ADS}"
log_debug "  password_file=${PASSWORD_FILE}"
log_debug "  pihole_data=${PIHOLE_DATA}"

if command -v ip >/dev/null 2>&1; then
  log_debug "Network interfaces:"
  ip -br addr 2>/dev/null | while read -r line; do log_debug "  ${line}"; done || true
fi

if command -v ss >/dev/null 2>&1; then
  log_debug "Listening sockets before start:"
  ss -lntu 2>/dev/null | while read -r line; do log_debug "  ${line}"; done || true
elif command -v netstat >/dev/null 2>&1; then
  log_debug "Listening sockets before start (netstat):"
  netstat -lntu 2>/dev/null | while read -r line; do log_debug "  ${line}"; done || true
fi

# Port 53 conflict is a common HA install failure mode (AdGuard on localhost,
# systemd-resolved, another DNS add-on). When ONLY loopback holds :53, bind
# Pi-hole to the LAN interface instead of ALL so DNS can still start.
PORT53_PLAN="$(python3 /opt/pihole-ha/port53_plan.py 2>/dev/null || echo FREE)"
log_info "Port 53 plan: ${PORT53_PLAN}"
if [[ "${PORT53_PLAN}" == "FREE" ]]; then
  log_info "Port 53 appears free for Pi-hole DNS"
elif [[ "${PORT53_PLAN}" == LOOPBACK_ONLY* ]]; then
  LOOP_IFACE="$(printf '%s' "${PORT53_PLAN}" | sed -n 's/.*iface=\([^ ]*\).*/\1/p')"
  LOOP_IP="$(printf '%s' "${PORT53_PLAN}" | sed -n 's/.*ip=\([^ ]*\).*/\1/p')"
  if [[ -n "${LOOP_IFACE}" && "${DNS_LISTENING_MODE}" == "ALL" ]]; then
    log_warn "Port 53 is only busy on localhost (often AdGuard). Binding Pi-hole DNS to ${LOOP_IFACE} (${LOOP_IP}) instead of ALL"
    DNS_LISTENING_MODE="BIND"
    export FTLCONF_dns_listeningMode="BIND"
    export FTLCONF_dns_interface="${LOOP_IFACE}"
    # dnsmasq implicitly adds loopback even with interface + bind-interfaces.
    # Exclude it explicitly or FTL still fails on the occupied localhost :53.
    export FTLCONF_misc_dnsmasq_lines="${FTLCONF_misc_dnsmasq_lines:+${FTLCONF_misc_dnsmasq_lines};}except-interface=lo"
    # Expose to the management UI so Setup can explain the localhost workaround.
    export PIHOLE_HA_DNS_BIND_NOTE="localhost :53 busy; bound to ${LOOP_IFACE} (${LOOP_IP})"
    log_info "  dns_listening_mode=${DNS_LISTENING_MODE} interface=${LOOP_IFACE}"
  else
    log_warn "Port 53 busy on localhost; listening mode is ${DNS_LISTENING_MODE} (not auto-switching)"
  fi
elif [[ "${PORT53_PLAN}" == CONFLICT* ]]; then
  log_error "Port 53 is already in use on this host — Pi-hole DNS may fail to start"
  log_error "  ${PORT53_PLAN}"
  log_error "Stop the other DNS service (AdGuard, dnsmasq, etc.), then restart this add-on"
  if command -v ss >/dev/null 2>&1; then
    ss -lntup 2>/dev/null | grep -E ':53\s' | while read -r line; do log_error "  ss: ${line}"; done || true
  fi
fi

log_info "Starting management UI: python3 /opt/pihole-ha/server.py"
python3 /opt/pihole-ha/server.py &
UI_PID=$!
log_info "Management UI pid=${UI_PID}"

# Give the UI a moment to bind and report immediate crashes.
sleep 1
if ! kill -0 "${UI_PID}" 2>/dev/null; then
  die "Management UI exited immediately after start; check earlier Python traceback"
fi
log_debug "Management UI is alive after startup probe"

cleanup() {
  local signal="${1:-EXIT}"
  log_warn "Received ${signal}; stopping management UI (pid=${UI_PID})"
  if kill -0 "${UI_PID}" 2>/dev/null; then
    kill "${UI_PID}" 2>/dev/null || true
    wait "${UI_PID}" 2>/dev/null || true
  fi
}

# IMPORTANT: do not trap EXIT. `exec start.sh` would fire EXIT and kill the UI.
trap 'cleanup INT' INT
trap 'cleanup TERM' TERM

if ! command -v start.sh >/dev/null 2>&1; then
  die "Official Pi-hole entrypoint start.sh not found on PATH"
fi

log_info "Handing off to official Pi-hole entrypoint (exec start.sh)"
log_debug "UI will keep running under tini as pid ${UI_PID}"
exec start.sh
