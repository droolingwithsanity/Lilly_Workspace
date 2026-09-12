# Lilly Pi Recon Station — Node for the Lilly Fleet

A **headless, read-only, plug-and-play Raspberry Pi** that runs as an
*additional* fleet node alongside your phones. It monitors the radio
environment far deeper than a phone can, and feeds the same radar/fleet API
so it appears on your existing map with zero host-code changes.

```
   Host (lilly_ai :8098)   ◄── Tailscale ──►   Pi Recon Station (:8099)
        ▲  radar UI  ▲                            │ Ethernet only
        │            │                            │ WiFi/Bluetooth = monitor
   phones (:8099)    └── kiosk (chromium /radar)  └─ iw + tcpdump + bluetooth
```

---

## What it does (deeper than a phone)

| Capability | Phone node | Pi Recon node |
|-----------|-----------|---------------|
| WiFi AP list (BSSID/SSID/channel/band/security/RSSI) | ✅ | ✅ `iw scan` |
| WiFi **client presence** (probe requests, unassociated devices) | ❌ | ✅ passive monitor-mode `tcpdump` |
| Bluetooth classic scan | ✅ | ✅ `hcitool scan` |
| Bluetooth LE scan | ✅ | ✅ `hcitool lescan` |
| Dedicated radio sensing (both bands at once) | ❌ (1 radio, must be a client) | ✅ (radio is 100% free) |
| Fleet endpoints (`/health`, `/sensors/all`, …) | ✅ | ✅ identical |
| Camera vision | ✅ | ❌ (headless, not needed) |

Because it takes no role as a WiFi *client*, its radio is fully devoted to
**monitoring** — it sees APs *and* the clients merely scanning for them, which
a phone (busy being a client) cannot.

---

## Two ways to run it

### A. Quick-start on an existing Pi (no image)
SSH/console into a Pi running Raspberry Pi OS (bookworm 64-bit Lite, rw):
```bash
sudo bash install_recon.sh \
  --name 'Garage Recon' --node-id recon-garage \
  --fleet http://100.93.131.114:8098 \
  --tailscale-authkey tskey-auth-XXXX
```

### B. Plug-and-play SD card image (recommended)
Use `build_pi_recon_image.sh` once on the host to bake a **ready-to-flash**,
headless, **read-only** image, then `dd`/BalenaEtcher it onto an SD card.
See **Building the image** below. This is the file you can publish to the
`file_share/` for download.

---

## Registration QR code
For frictionless onboarding, the node prints a **scannable QR**:

- **Tailscale auth key QR** — encode your real key and scan with your phone /
  the Tailscale app to approve the node into the tailnet (headless join).
- **Fleet QR** — opens the fleet view so you can watch the node appear.

Generate PNGs to print/stick on the case:
```bash
python3 gen_register_qr.py "tskey-auth-YOUR_REAL_KEY" qr-tailscale.png
python3 gen_register_qr.py "http://100.93.131.114:8098/api/nodes/fleet" qr-fleet.png
```
> ⚠️ The `qr-tailscale-authkey-TEMPLATE.png` in the share is a **placeholder**.
> It is NOT a valid key — regenerate with your real Tailscale key from
> https://login.tailscale.com/admin/settings/keys .

---

## Network: Ethernet + Tailscale
- **Ethernet = the only uplink** (DHCP). No WiFi client config; the radio stays
  free for monitoring. Plug in a cable — done.
- **Tailscale** = the fleet's private mesh. The node joins your tailnet with
  `--tailscale-authkey` so the host and phones can reach it by Tailscale IP,
  and it can call the host. **Point `--fleet` at the host's Tailscale URL.**
- The node self-registers with the host on startup
  (`POST /api/nodes/register`, role `edge`, capabilities `wifi/ble/recon/monitor`).

### Tailscale auth key (important)
Generate a key in the Tailscale admin console (Settings → Keys → Generate
auth key — enable **Reusable** so the SD card can be re-flashed without a new
key). Pass it as `--tailscale-authkey tskey-auth-…`. The node joins headlessly;
no typing on the Pi.

---

## Read-only rootfs (SD-card protection)
The node is designed for **24/7 headless duty** and runs **read-only** by
default, so it never wears out the SD and can't corrupt on power loss:

1. **First boot** = provisioning pass (RW): installs radio tooling, enables
   services, joins Tailscale, writes `/opt/lilly-recon/.provisioned`.
2. **Subsequent boots** = **read-only**. A systemd unit (`lilly-ro-lock`)
   remounts `/` read-only only once `.provisioned` exists. Runtime state lives
   in RAM (`/run`, `/var/tmp` tmpfs) — nothing needs to write the SD.

To return to RW (e.g. to update):
```bash
sudo systemctl disable lilly-ro-lock.service && sudo reboot
```

---

## Building the flashable image
Run **on the host** (x86_64 Linux is fine — it just edits the image; needs
`losetup`, `kpartx`/`mount`, `pigz`; run as root).

```bash
sudo bash build_pi_recon_image.sh \
  --base ./raspios-lite-arm64.img \        # or a URL; downloads if omitted
  --user alice --password 'hunter2' \      # headless SSH login
  --name 'Garage Recon' --node-id recon-garage \
  --fleet http://100.93.131.114:8098 \
  --tailscale-authkey tskey-auth-XXXX \
  --kiosk \                                # optional full-screen radar UI
  --pub /home/labhrasd/Lilly_Workspace/file_share/pi-recon-station
```

Output: `lilly-recon.img.gz` (flash with `dd`/BalenaEtcher/Raspberry Pi Imager).
Fine to re-run for each node (different `--node-id`/`--name`).

> Note: a genuinely read-only **squashfs-style** root is best baked at image
> build time for an image you hand out. The bundled script uses the proven
> first-boot-RW → lock-RO pattern, which is safe and simple. If you want a
> hardened squashfs root, tell me and I'll extend `build_pi_recon_image.sh`.

---

## Host-side one-time change (this repo)
The fleet caps at `MAX_NODES = 3` by default. To add the Pi as a 4th node,
`LILLY_MAX_NODES` was added to `docker-compose.yml` (default 6). Apply:
```bash
docker compose up -d lilly   # recreates container with new env
```

Enable the radar to render the node:
- No code change needed — the node serves the standard endpoints and
  registers itself. It shows up in `http://localhost:8098/api/nodes/fleet`
  and on the `/radar` map automatically.

---

## Files in this bundle
| File | Purpose |
|------|---------|
| `pi_recon_node.py` | The recon node server (fleet endpoints + deep wifi/bt monitoring) |
| `install_recon.sh` | First-boot provisioning (packages, systemd, tailscale, read-only lock, QR) |
| `pi-recon.service` | systemd unit for the node |
| `gen_register_qr.py` | Print/PNG QR codes for onboarding |
| `build_pi_recon_image.sh` | Bake the flashable image |
| `dist/qr-*.png` | Ready-to-print QRs (fleet URL real; tailscale one is a template) |
| `README.md` | This file |

## Verify the node is alive
```bash
# on the Pi / via Tailscale
curl http://<pi-tailscale-ip>:8099/health
# expect: status ok, role recon, wifi_ap_count, wifi_stations_seen, ...

# on the host
curl http://localhost:8098/api/nodes/fleet   # node appears under "nodes"
```
