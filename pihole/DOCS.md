# Pi-hole Home Assistant App

This app installs [Pi-hole](https://pi-hole.net/) v6 inside Home Assistant and configures it as a network-wide ad-blocking DNS server. A custom management panel is available through Home Assistant Ingress.

## Requirements

- Home Assistant OS or Supervised
- A **static IP** on the Home Assistant host
- Port **53/udp** and **53/tcp** available on the host (nothing else may bind DNS)

## Installation

1. In Home Assistant go to **Settings → Add-ons → Add-on store**.
2. Open the menu (⋮) → **Repositories**.
3. Add:

   `https://github.com/chancemcox/HomeassistantPIhole`

4. Install **Pi-hole**.
5. Open the app configuration and set:
   - **Admin password** (recommended)
   - **Upstream DNS servers** (defaults to Cloudflare `1.1.1.1` / `1.0.0.1`)
   - **Timezone**
6. Start the app.
7. Enable **Show in sidebar** to open the management panel.

## Make it the network DNS server

1. Confirm the Home Assistant host IP (also shown in the Ingress panel under **DNS setup**).
2. On your router, set the LAN DHCP DNS server to that IP.
3. Renew DHCP leases on clients (or reboot them).
4. Visit a site with ads — blocked domains appear under **Recent blocks**.

You can also set DNS manually on a single device to test before changing the router.

## Management panel

From the Home Assistant sidebar you can:

- Enable, disable, or pause blocking
- Watch query / block / client metrics
- Add or remove allow and deny domains
- Update gravity blocklists
- Flush the query log
- Copy the DNS address for router setup

The native Pi-hole admin UI can optionally be exposed by assigning host port **8080** under the app network settings. Ingress is enough for day-to-day control.

## Options

| Option | Description |
| --- | --- |
| `password` | Pi-hole API / admin password. Blank generates a random password (printed in the app log). |
| `upstream_dns` | Upstream resolvers for allowed queries. |
| `dns_listening_mode` | Usually `ALL` with Home Assistant host networking. |
| `interface_theme` | Theme for the native Pi-hole UI. |
| `query_logging` | Enable DNS query history. |
| `timezone` | IANA timezone string. |

Values supplied through these options are applied as Pi-hole `FTLCONF_*` environment settings and are the source of truth after each restart.

## Home Assistant integration

After DNS is working, you can also add the built-in **Pi-hole** integration pointed at the host IP and port `8080` (if exposed) using the same password.

## Notes

- The official Community Pi-hole add-on is deprecated; this app packages current Pi-hole v6 with a Home Assistant Ingress UI.
- Do not run a second DNS server on port 53 on the same host.
- Keep a backup DNS on your router for recovery if the Home Assistant host is offline.
