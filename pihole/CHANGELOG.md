# Changelog

## 1.11.0

- Scan private LAN subnets every 10 minutes, key each host by MAC, assign a static `lan-….local` name from that identity, and publish it in Pi-hole local DNS
- New **Local devices** page in the Ingress UI with rename/override support — custom names stay bound to the MAC when the IP changes

## 1.10.10

- Explicitly exclude loopback when automatically binding DNS to the LAN interface. dnsmasq otherwise still opens localhost port 53 and leaves blocking unavailable.
- Probe the configured LAN interface in the container health check when DNS uses BIND mode.

## 1.10.9

- Stop assuming AdGuard when the owner is unknown — scan **all** started add-ons for port 53, use `fuser`/`host_pid` for process names, and tell users to check any DNS add-on
- Clearer LAN conflict copy when `192.168.x.x:53` is taken without naming a product

## 1.10.8

- When port 53 is taken on the **LAN IP** (not just localhost), spell out that another DNS server must be stopped — Pi-hole cannot share that address
- Scan host processes for DNS binaries and probe common admin UIs when Supervisor cannot list add-ons

## 1.10.7

- When port 53 is busy **only on localhost** (`127.0.0.1` / `::1`, typical AdGuard), auto-bind Pi-hole DNS to the LAN interface (`BIND`) so blocking can start without stopping the peer
- Enable `host_pid` so owner errors can name the real process (e.g. `AdGuardHome`)

## 1.10.6

- Set `hassio_role: manager` so the UI can **name the conflicting DNS add-on** via Supervisor (`GET /addons` needs manager, not default)
- Fallback fingerprint for AdGuard Home admin UI on localhost when Supervisor lookup is empty
- Clearer action text: stop the peer add-on under Settings → Add-ons, then restart Pi-hole

## 1.10.5

- Name the port-53 conflict clearly (no raw `ss` dump): localhost-only binds call out AdGuard / DNS stubs; `127.0.0.53` → systemd-resolved
- Fix Supervisor add-on lookup on `host_network` by trying `172.30.32.2` first (same as Ingress port resolution)

## 1.10.4

- Fix Enable blocking failures when DNS never started: surface Pi-hole `dns_failure` next to the button
- Error text now reports **what is using port 53** (process via `ss`/`/proc`, plus conflicting Supervisor DNS add-ons)
- Startup logs list the port-53 owner the same way

## 1.10.3

- Fix invalid Pi-hole v6 theme values (`default` → `default-auto`; update option schema)
- Disable Pi-hole NTP server by default (avoids `:123` conflicts on Home Assistant hosts)
- Louder startup errors + in-UI warning when **port 53 is already in use** (DNS cannot start)

## 1.10.2

- Fix unstyled Ingress UI (Times New Roman / stuck on Checking…): use **relative** `static/` and API URLs so assets load under `/api/hassio_ingress/.../`
- Absolute `/static/...` and `/api/...` paths were escaping the Ingress prefix

## 1.10.1

- Align UI primary color to current Home Assistant token `#009ac7` (`--ha-color-primary-40`)
- Chart total series uses the same primary for consistency

## 1.10.0

- Restyle the Ingress management UI to closer match Home Assistant (primary `#03a9f4`, card surfaces, Roboto, light/dark via `prefers-color-scheme`)
- Chart and status colors use HA primary / accent / error tokens

## 1.9.3

- Fix persistent Ingress **502 Bad Gateway** by using a fixed unique `ingress_port: 8189` (not `8099`, not dynamic `0`)
- Management UI always binds to `8189`, matching what Home Assistant Ingress proxies — no Supervisor port lookup required
- DNS remains on `53`; native Pi-hole admin/API remains on `8088`

## 1.9.2

- Fix Ingress **502 Bad Gateway**: resolve the Supervisor-assigned ingress port via `172.30.32.2` (host_network cannot rely on the `supervisor` DNS name)
- Retry lookup and abort startup instead of silently binding `8099`, which left Ingress proxying an empty dynamic port
- CI now asserts the management **page HTML shows** (title, traffic/setup panels, static assets) and that ingress-port resolution works

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
