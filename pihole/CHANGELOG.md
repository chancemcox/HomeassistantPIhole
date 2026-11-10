# Changelog

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
