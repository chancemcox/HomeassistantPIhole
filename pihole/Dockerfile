ARG BUILD_FROM=pihole/pihole:2026.10.1
# hadolint ignore=DL3006
FROM ${BUILD_FROM}

# Management UI dependencies (stdlib-only Python server; jq for options parsing)
RUN apk add --no-cache \
    python3 \
    jq \
    tini

COPY rootfs /

RUN chmod a+x /ha-entrypoint.sh \
    && chmod a+x /opt/pihole-ha/server.py

ENV FTLCONF_webserver_port="8088" \
    FTLCONF_dns_listeningMode="ALL" \
    PIHOLE_API_BASE="http://127.0.0.1:8088"

# Ingress UI port is assigned dynamically (ingress_port: 0) under Supervisor.
# DNS stays on 53; native Pi-hole admin/API stays on 8088.
EXPOSE 53/tcp 53/udp 8088/tcp

HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=5 \
  CMD dig +short +norecurse +retry=0 @127.0.0.1 pi.hole >/dev/null || exit 1

ENTRYPOINT ["/sbin/tini", "--", "/ha-entrypoint.sh"]
