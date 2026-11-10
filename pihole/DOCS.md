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
   - **Log level** (`info` by default; use `debug` or `trace` while troubleshooting)
6. Start the app.
7. Enable **Show in sidebar** to open the management panel.

## How to use Pi-hole as DNS

Replace `192.168.1.50` below with the Home Assistant host IP shown in the sidebar under **DNS setup**.

### 1) Recommended: set DNS on your router (whole network)

This is the best approach. Every device that gets DHCP from the router will use Pi-hole automatically.

1. Give Home Assistant a static LAN IP (example: `192.168.1.50`).
2. Open your router admin page.
3. Find **LAN**, **DHCP**, or **DNS** settings.
4. Set **Primary / DNS server 1** to `192.168.1.50`.
5. Leave secondary empty, or only add a public DNS if you accept ads when Pi-hole is offline.
6. Save, then renew leases (reboot devices or toggle Wi‑Fi).

**Examples by router brand:**

| Router / system | Where to set DNS |
| --- | --- |
| Generic consumer router | LAN → DHCP → DNS Server |
| UniFi Network | Settings → Networks → LAN → DHCP Service Management → DHCP DNS Server |
| pfSense | Services → DHCP Server → LAN → DNS Servers |
| OPNsense | Services → DHCPv4 → LAN → DNS servers |
| OpenWrt | Network → Interfaces → LAN → DHCP Server → Advanced → DHCP-Options (`6,192.168.1.50`) |
| Asus Merlin / stock Asus | LAN → DHCP Server → DNS Server 1 |
| Google Nest Wifi / Wifi Pro | App usually forces Google DNS; set DNS per-device instead or use custom firmware/router mode |
| Eero | Network settings are limited; prefer per-device DNS or put eero behind another router |

### 2) Quick test on a single device first

Before changing the whole house, point one laptop/phone at Pi-hole:

1. Set that device’s DNS to `192.168.1.50`.
2. Open the Pi-hole sidebar in Home Assistant.
3. Visit a site with ads.
4. Confirm queries appear and blocked domains show under **Recent blocks**.

### 3) Windows 11 example

1. **Settings → Network & internet**.
2. Open **Wi‑Fi** or **Ethernet** → your connection → **DNS server assignment → Edit**.
3. Choose **Manual**, enable **IPv4**.
4. Preferred DNS: `192.168.1.50`
5. Save.

Verify in PowerShell:

```powershell
nslookup doubleclick.net 192.168.1.50
Resolve-DnsName pi.hole -Server 192.168.1.50
```

### 4) macOS example

1. **System Settings → Network →** select Wi‑Fi/Ethernet → **Details… → DNS**.
2. Click **+** and add `192.168.1.50`.
3. Apply.

Flush cache:

```bash
sudo dscacheutil -flushcache; sudo killall -HUP mDNSResponder
dig @192.168.1.50 pi.hole +short
```

### 5) iPhone / iPad example

1. **Settings → Wi‑Fi**.
2. Tap the **ⓘ** next to your network.
3. **Configure DNS → Manual**.
4. Remove existing servers.
5. Add `192.168.1.50`.
6. Save.

### 6) Android example

1. **Settings → Network & internet → Internet**.
2. Tap the gear on your Wi‑Fi network.
3. **Advanced / Pencil → IP settings**.
4. If needed switch to static IP (keep your current IP/gateway), set **DNS 1** to `192.168.1.50`.
5. Save and reconnect.

Note: Android **Private DNS** (DNS-over-TLS) bypasses Pi-hole. Leave Private DNS **Off** for ad blocking on that device.

### 7) Linux (NetworkManager) example

```bash
# List connections
nmcli connection show

# Point one connection at Pi-hole
nmcli connection modify 'Wi-Fi' ipv4.dns "192.168.1.50" ipv4.ignore-auto-dns yes
nmcli connection up 'Wi-Fi'

# Verify
resolvectl status
dig @192.168.1.50 doubleclick.net +short
```

### 8) UniFi Network example

1. UniFi Network application → **Settings → Networks**.
2. Edit your LAN.
3. Under **DHCP Service Management**, set **DHCP DNS Server** to `192.168.1.50`.
4. Apply Changes.
5. Reconnect clients or wait for lease renewal.

### 9) pfSense example

1. **Services → DHCP Server → LAN**.
2. Set **DNS Server** to `192.168.1.50`.
3. Under **System → General Setup**, either leave DNS empty for clients or keep resolver behavior intentional.
4. Apply Changes.

### 10) OPNsense example

1. **Services → DHCPv4 → [LAN]**.
2. DNS servers: `192.168.1.50`.
3. Save & apply.
4. Renew client leases.

### 11) Verify DNS is actually using Pi-hole

```bash
# Should talk to your HA host
dig @192.168.1.50 pi.hole +short

# Often returns 0.0.0.0 / NXDOMAIN / blocked reply when blocked
dig @192.168.1.50 doubleclick.net +short

# From a client that should be using Pi-hole via DHCP
nslookup doubleclick.net
```

In the Home Assistant Pi-hole panel you should see:

- rising **Queries**
- non-zero **Blocked**
- your client under activity / recent blocks

### 12) Upstream DNS examples (add-on options)

These are the resolvers Pi-hole uses **after** it allows a query:

```yaml
# Cloudflare
upstream_dns:
  - 1.1.1.1
  - 1.0.0.1

# Google
upstream_dns:
  - 8.8.8.8
  - 8.8.4.4

# Quad9
upstream_dns:
  - 9.9.9.9
  - 149.112.112.112

# Mixed
upstream_dns:
  - 1.1.1.1
  - 9.9.9.9
```

## Block Hulu ads

Hulu ads are harder than normal web ads because many creatives are served from content CDNs. This app includes a curated **Hulu ads** preset that adds common advertising / tracking hostnames and regex filters to Pi-hole’s deny list.

### One-click from the sidebar

1. Make sure the Hulu device uses Pi-hole for DNS.
2. Open the Pi-hole panel in Home Assistant.
3. Under **Streaming presets**, click **Block Hulu ads**.
4. Play Hulu and watch **Recent blocks** for `hulustream`, `hulu.com` ad hosts, or `disneyadvertising.com`.

### Auto-apply on startup

In the add-on options, enable:

```yaml
block_hulu_ads: true
```

The management UI applies the same preset after Pi-hole becomes ready.

### What the preset blocks

Exact hosts (examples):

- `ads-e-darwin.hulustream.com`
- `ads-v-darwin.hulustream.com`
- `ads-api.hulu.com`
- `track.hulu.com`
- `collector.hulu.com`
- `disneyadvertising.com`

Regex examples:

- `(^|\.)disneyadvertising\.com$`
- `(^|\.)ads-.*\.hulustream\.com$`

### Important caveats

- Results vary by app / TV / browser. Some ads cannot be blocked with DNS alone.
- Blocking `disneyadvertising.com` often removes ads but may skip a few seconds of show around ad breaks.
- Do **not** block `vortex.hulu.com` or `doppler.hulu.com` — that breaks playback telemetry and can error the app.
- If Hulu breaks, remove the Hulu preset entries from the deny list (or disable `block_hulu_ads` and delete those domains).
- An ad-free Hulu plan is still the only fully reliable approach.

## Management panel

From the Home Assistant sidebar you can:

- Enable, disable, or pause blocking
- Watch **DNS traffic** over 24 hours, live queries, and top clients
- Apply the **Hulu ads** streaming preset
- Watch query / block / client metrics
- Add or remove allow and deny domains
- Update gravity blocklists
- Flush the query log
- View DNS setup examples for routers and devices

DNS always listens on host port **53**. The native Pi-hole admin / API listens on host port **8088**.

- **Ingress sidebar** → custom management panel (blocking, traffic, lists). Because this app uses `host_network`, Ingress uses a **Supervisor-assigned dynamic port** (`ingress_port: 0`) so it does not collide with other host-network apps on `8099`.
- **Native admin / API** → `http://<host>:8088/admin` (host port `8088`). With Ingress enabled, Home Assistant does not offer a separate Open Web UI button.
- You can disable the published `8088` host mapping under Network if you only want Ingress.

If the Pi-hole sidebar ever shows a different app (for example HomeScan), update to **1.9.0+** and fully restart the Pi-hole add-on so it rebinds to its own Ingress port.

## Options

| Option | Description |
| --- | --- |
| `password` | Pi-hole API / admin password. Blank generates a random password (printed in the app log). |
| `upstream_dns` | Upstream resolvers for allowed queries. |
| `dns_listening_mode` | Usually `ALL` with Home Assistant host networking. |
| `interface_theme` | Theme for the native Pi-hole UI. |
| `query_logging` | Enable DNS query history. |
| `timezone` | IANA timezone string. |
| `log_level` | Add-on + UI log verbosity: `trace`, `debug`, `info`, `warning`, `error`. |
| `block_hulu_ads` | When true, apply the curated Hulu/Disney advertising deny-list on startup. |

Values supplied through these options are applied as Pi-hole `FTLCONF_*` environment settings and are the source of truth after each restart.

### Troubleshooting with logs

1. Set **Log level** to `debug` or `trace`.
2. Restart the app.
3. Open **Log** on the add-on page.
4. Look for `[pihole-ha]` and `[pihole-ha-ui]` lines covering options loading, UI start, Pi-hole API wait/auth, and request failures.

## Home Assistant integration

After DNS is working, you can also add the built-in **Pi-hole** integration pointed at the host IP and port `8088` (if exposed) using the same password.

## Continuous integration

GitHub Actions installs Home Assistant Core and builds this add-on on every PR:

- Lint add-on metadata
- Build the Docker image
- Smoke-test the Ingress management UI
- Boot `ghcr.io/home-assistant/home-assistant:stable` with `tests/ha-config`

See the root README for local `docker compose -f docker-compose.ci.yml` usage.

## Notes

- The official Community Pi-hole add-on is deprecated; this app packages current Pi-hole v6 with a Home Assistant Ingress UI.
- Do not run a second DNS server on port 53 on the same host.
- Keep a recovery plan if Home Assistant is offline (temporary public DNS on the router).
- Devices using DNS-over-HTTPS / Private DNS bypass Pi-hole unless you block those paths on the firewall.
