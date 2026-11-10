# Pi-hole for Home Assistant

Install [Pi-hole](https://pi-hole.net/) as a Home Assistant app and use it as your network’s ad-blocking DNS server. The app includes an Ingress sidebar panel for everyday Pi-hole management.

## Features

- Packages current **Pi-hole v6** for Home Assistant OS / Supervised
- Configures upstream DNS, password, listening mode, and timezone from the app options
- Serves a **management UI** in the Home Assistant sidebar (Ingress)
- Shows **DNS traffic**: 24-hour chart, live query log, and top clients
- Enable / pause / disable blocking, manage allow & deny lists, update gravity
- Optional **Hulu ads** preset for common Hulu/Disney advertising domains
- Shows the host DNS address so you can point your router at Pi-hole

## Add the repository

In Home Assistant:

1. **Settings → Add-ons → Add-on store**
2. Menu (⋮) → **Repositories**
3. Add:

```text
https://github.com/chancemcox/HomeassistantPIhole
```

4. Install **Pi-hole**, set a password, start it, and enable **Show in sidebar**.

[![Open your Home Assistant instance and show the add add-on repository dialog with a specific repository URL pre-filled.](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2Fchancemcox%2FHomeassistantPIhole)

## Wire up DNS

1. Give Home Assistant a static LAN IP.
2. Set that IP as the DNS server in your router’s DHCP settings (best for the whole network).
3. Or test one device first — Windows, macOS, iOS, Android, Linux, UniFi, and pfSense/OPNsense examples are in the docs and the sidebar **DNS setup** panel.
4. Renew client leases and confirm queries appear in the Pi-hole panel.

Full walkthrough with multiple examples: [`pihole/DOCS.md`](./pihole/DOCS.md).

## Repository layout

```text
.
├── repository.yaml          # Home Assistant add-on repository manifest
├── pihole/                  # Pi-hole app
│   ├── config.yaml
│   ├── Dockerfile
│   ├── DOCS.md
│   └── rootfs/              # Entrypoint + management UI
└── README.md
```

## License

MIT. Pi-hole itself is covered by its own upstream licenses.
