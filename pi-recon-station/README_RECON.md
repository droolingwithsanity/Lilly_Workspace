# Lilly Pi Recon Station — Bluehood + WiFi Probe + Dashboard

A self-provisioning Raspberry Pi image that turns any Pi 3B+/4/5 into a
passive neighbourhood recon station. Plug in power + ethernet, and it
starts logging every Bluetooth device and WiFi probe request in range.

---

## What's included

| Service | Port | Purpose |
|---------|------|---------|
| **Recon Dashboard** | 8080 | Unified UI — status of all services, live probe feed |
| **Bluehood** | 8081 | Passive BLE + Classic BT scanner, pattern analysis, push alerts |
| **WiFi Probe** | 8082 | Passive WiFi probe request capture, Wigle GPS lookup, device profiling |
| **Netdata** | 19999 | Lightweight system metrics — CPU, RAM, temp, network (< 2% CPU) |

---

## Hardware requirements

| Item | Notes |
|------|-------|
| Raspberry Pi 3B+, 4, or 5 | Pi Zero 2W works but slower builds |
| MicroSD card ≥ 16 GB | Class 10 or better |
| Ethernet cable | WiFi interface stays FREE for monitor mode |
| **USB WiFi dongle (monitor mode capable)** | Required for passive WiFi capture. Recommended: Alfa AWUS036ACH, TP-Link TL-WN722N v1, or any adapter with `rtl8812au` / `rtl8188` chipset |
| Power supply | 5V 3A (Pi 4) or 5V 2.5A (Pi 3) |

> The onboard WiFi (wlan0) can optionally be used for monitor mode if you have
> no ethernet. Set `WIFI_IFACE=wlan0` but you will lose network connectivity
> while it is in monitor mode.

---

## Flash instructions

### Step 1 — Flash Raspberry Pi OS Lite (64-bit)

1. Download [Raspberry Pi Imager](https://www.raspberrypi.com/software/)
2. Choose **Raspberry Pi OS Lite (64-bit)** — Bookworm
3. Click the ⚙️ gear icon → Advanced options:
   - Enable SSH, set username/password
   - Set hostname: `recon`
   - **Do NOT configure WiFi** (ethernet only — keep wlan free)
4. Flash to your SD card

### Step 2 — Drop firstboot.sh onto the boot partition

After flashing, mount the SD card's **boot** partition (FAT32, visible on
Windows/Mac) and copy `firstboot.sh` to the root of that partition.

Then create a file called `firstboot_config` with your settings:

```bash
# /boot/firstboot_config
WIFI_IFACE=wlan1          # wlan1 = USB dongle, wlan0 = onboard
TZ=America/Toronto
WIGLE_USER=your_wigle_username
WIGLE_TOKEN=your_wigle_api_token
NTFY_TOPIC=                # optional: your ntfy.sh topic name
LILLY_FLEET=               # optional: http://your-lilly-ai-server:8098
```

Then create `/boot/firstrun.sh` (Raspberry Pi OS runs this automatically on
first boot):

```bash
#!/bin/bash
source /boot/firstboot_config 2>/dev/null || true
bash /boot/firstboot.sh >> /boot/firstboot.log 2>&1
```

Make it executable:
```bash
chmod +x /boot/firstrun.sh
```

### Step 3 — Boot and wait

1. Insert SD card, connect ethernet cable, plug in USB WiFi dongle, power on
2. Wait 5–10 minutes for first boot provisioning (downloads Docker images)
3. Monitor progress: `tail -f /boot/firstboot.log` via SSH

```bash
ssh pi@recon.local
tail -f /boot/firstboot.log
```

### Step 4 — Access the dashboard

Once provisioning completes, open a browser to:

```
http://recon.local:8080
```

Or find the Pi's IP:
```bash
# From another machine on the same network
arp -a | grep recon
```

---

## Service URLs

| Service | URL |
|---------|-----|
| Unified Dashboard | `http://recon.local:8080` |
| Bluehood (BT) | `http://recon.local:8081` |
| WiFi Probe | `http://recon.local:8082` |
| Netdata | `http://recon.local:19999` |

---

## Wigle GPS lookup (optional but recommended)

The WiFiProbe service cross-references captured BSSIDs against Wigle.net's
crowd-sourced database to get approximate GPS coordinates for each access
point — letting you map the neighbourhood's WiFi coverage without a GPS unit.

1. Create a free account at https://wigle.net
2. Go to Account → API Token
3. Add `WIGLE_USER` and `WIGLE_TOKEN` to your `firstboot_config`

Free tier: ~100 lookups/day. The service rate-limits automatically.

---

## WiFi dongle — monitor mode

Not all WiFi dongles support monitor mode. Tested and working:

| Dongle | Chipset | Notes |
|--------|---------|-------|
| Alfa AWUS036ACH | rtl8812au | Best range, dual-band |
| TP-Link TL-WN722N **v1 only** | AR9271 | v2/v3 won't work |
| Alfa AWUS036NHA | AR9271 | Reliable, cheaper |
| Panda PAU09 | RT5572 | Dual-band |

Check if your dongle works:
```bash
sudo airmon-ng check kill
sudo airmon-ng start wlan1
# Should show "monitor mode enabled"
```

---

## Data storage

All data lives in `/data/recon/` on the Pi:

```
/data/recon/
├── bluehood/          ← Bluehood SQLite DB (BT devices, sightings)
├── wifiprobe/
│   └── wifiprobe.db   ← WiFi probes, beacons, devices, GPS coords
└── dashboard/         ← Static dashboard HTML
```

---

## Push notifications (optional)

Bluehood supports push notifications via [ntfy.sh](https://ntfy.sh) — free,
no account required for self-hosted topics.

1. Pick a topic name: e.g. `my-recon-alerts`
2. Install the ntfy app on your phone, subscribe to the topic
3. Set `NTFY_TOPIC=my-recon-alerts` in `firstboot_config`
4. Configure alert rules in Bluehood UI → Settings → Alerts

You'll get a ping when a watched device arrives or leaves range.

---

## Updating services

```bash
ssh pi@recon.local
cd /opt/lilly-recon
sudo docker compose pull
sudo docker compose up -d
```

---

## Useful commands

```bash
# Check service status
sudo docker compose -f /opt/lilly-recon/docker-compose.yml ps

# View logs
sudo docker compose -f /opt/lilly-recon/docker-compose.yml logs -f bluehood
sudo docker compose -f /opt/lilly-recon/docker-compose.yml logs -f wifiprobe

# Check monitor mode is active
iwconfig wlan1

# Query WiFiProbe API
curl http://localhost:8082/api/stats
curl http://localhost:8082/api/devices

# Query Bluehood API
curl http://localhost:8081/api/v1/devices

# Restart everything
sudo systemctl restart recon-stack
```

---

## Connecting to your Lilly fleet

Set `LILLY_FLEET=http://your-server:8098` in `firstboot_config`.
The Pi will register itself as a fleet node and send heartbeats so it
appears on your radar map at `/api/nodes/fleet`.

---

## CPU impact (Pi 4, measured)

| Service | Idle CPU | Peak CPU |
|---------|----------|----------|
| Bluehood | ~0.5% | ~2% |
| WiFiProbe | ~1% | ~3% (during capture) |
| Netdata | ~1% | ~2% |
| Nginx dashboard | ~0% | ~0.1% |
| **Total** | **~2.5%** | **~7%** |

All four services together stay well under 10% CPU on a Pi 4.
Pi 3B+ is fine too — Netdata may use slightly more on older hardware.
