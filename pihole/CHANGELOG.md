# Changelog

## 1.9.1

- Fix GitHub Actions add-on lint: remove `webui` while Ingress is enabled (rejected by `frenck/action-addon-linter`)
- Native admin remains available at host port `8088`; use the sidebar Ingress panel or open `http://<host>:8088/admin` directly

## 1.9.0

- Fix Ingress sidebar collision on `host_network`: use `ingress_port: 0` and bind the management UI to the Supervisor-assigned port
- Prevents the Pi-hole panel from opening another app that still listens on fixed `8099` (for example HomeScan)
- DNS remains on port `53`; native Pi-hole admin/API remains on port `8088`

## 1.8.0

- Point Home Assistant **Open Web UI** at the native Pi-hole admin on port `8088` (`webui`)
- Publish host port `8088` by default for the web UI; keep DNS on host port `53`
- Show separate DNS (`:53`) and web UI (`:8088/admin`) links in the Ingress setup panel
- Management UI API forwarding continues to use `PIHOLE_API_BASE` on port `8088`

## 1.7.0

- Add GitHub Actions CI with add-on lint, image build, UI smoke tests, and Home Assistant Core install for testing
- Remove default-value keys from `config.yaml` / `build.yaml` so `frenck/action-addon-linter` passes

## 1.6.0

- Move native Pi-hole admin / API webserver from port `8080` to `8088`

## 1.5.0

- Add DNS traffic panel with 24-hour chart, live query log, and top clients

## 1.2.0

- Add curated **Hulu ads** streaming preset (exact + regex deny list)
- Add sidebar **Block Hulu ads** button and preset notes
- Add `block_hulu_ads` option to auto-apply the preset on startup
- Document Hulu DNS blocking caveats and recovery steps

## 1.1.0

- Add configurable `log_level` option (`trace`, `debug`, `info`, `warning`, `error`)
- Add detailed startup and API logging in the entrypoint and management UI
- Fix management UI being killed on handoff to Pi-hole (`EXIT` trap + `exec`)
- Fix password generation abort under `pipefail`
- Harden Ingress access checks and domain API validation
- Expand DNS setup docs and in-app examples for routers and devices

## 1.0.0

- Initial Home Assistant app based on `pihole/pihole:2026.10.1`
- Automatic Pi-hole DNS setup from add-on options
- Ingress management UI for blocking, domain lists, gravity, and setup guidance
- Persistent Pi-hole configuration under add-on data
