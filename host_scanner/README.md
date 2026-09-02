# Lilly Host Scan Provider

Gives Lilly AI **real** Bluetooth + WiFi scans from the host machine's physical
radios, instead of relying on the phone (Termux) which often reports nothing.

## How it works

- **WiFi** → `nmcli dev wifi list` on the host WiFi interface (`wlp9s0`).
- **Bluetooth** → [bluehood](https://github.com/dannymcc/bluehood)'s
  `BluetoothScanner` — BLE via `bleak` (BlueZ D-Bus) + classic via `hcitool`,
  with a `bluetoothctl` fallback.

`lilly_ai.py` `/api/tracker/scan` calls this provider **first**
(`HOST_SCAN_PROVIDER_URL`, default `http://127.0.0.1:8096`), and falls back to
the phone sensor server if the provider is unreachable.

## Fleet node (radar_hub / tracker pages)

On startup, `lilly_ai.py` auto-registers the provider as a **fleet node**
(`host-127.0.0.1-8096`) pointing at this provider. The server's fleet poller
then calls this provider's fleet-compatible endpoints, so the host's **real**
BT + WiFi appear on `radar_hub.html` (`/radar`) and the tracker pages:

| Endpoint | Returns |
|----------|---------|
| `/wifi/scan` | `{networks:[...]}` (real SSIDs via nmcli) |
| `/bluetooth/scan` | `{devices:[...]}` (real BT via bluehood) |
| `/location` | `{location:{latitude,longitude}}` GPS for the host marker |
| `/battery` / `/sensors/all` / `/notification/list` | desktop placeholders for the poller |

The fleet BT/WiFi endpoints are **cache-first** with a long TTL so they stay
well under the lilly fleet poller's 6s HTTP timeout (a raw BT scan is ~16s,
too slow to serve synchronously; data refreshes in the background).

**Host GPS** for the radar map is resolved in this order:
1. `HOST_GPS_LAT` / `HOST_GPS_LNG` (explicit)
2. `SENSOR_SERVER_URL` — forwards the bound phone's `/location`
3. `HOST_GPS_DEFAULT` (default: the saved home, `43.862564,-79.382863`)

## Setup (already done on the host)

```bash
# Install host deps (bleak, fastapi, uvicorn, aiohttp, mac-vendor-lookup)
pip install -r host_scanner/requirements.txt

# Install + start as a systemd service
sudo cp host_scanner/lilly-host-scan.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now lilly-host-scan

# Manual foreground run instead:
PORT=8096 bash host_scanner/start.sh
```

## Verify

```bash
curl http://127.0.0.1:8096/health        # {"status":"ok",...,"bt_adapter_powered":true}
curl -X POST http://127.0.0.1:8096/api/scan   # {ok, bt:[...], wifi:[...], ...}
curl -X POST http://localhost:8098/api/tracker/scan  # Lilly's endpoint (source:"host")
```

## Adding a Bluetooth dongle

The provider auto-discovers adapters via BlueZ. When you plug in a dongle,
it now **auto-detects** an onboard radio vs. a USB dongle (by USB vendor ID
from the sysfs modalias) and runs **BLE + classic concurrently** on separate
adapters — no config needed:

- BLE runs on the onboard/Intel radio (best BLE range).
- Classic inquiry runs on the USB dongle.
- `/health` reports `dual_adapter`, `ble_adapter`, `classic_adapter`.

You can still pin adapters explicitly if auto-detect picks wrong:

1. Confirm what's visible: `bluetoothctl list` / `hciconfig -a`.
2. Make sure it's unblocked + powered:
   ```bash
   rfkill unblock bluetooth
   bluetoothctl power on            # powers the default adapter
   bluetoothctl select <mac-or-dev> # or select the dongle explicitly
   ```
3. Optionally pin a specific adapter:
   ```bash
   BLUEHOOD_ADAPTER=hci0 BLUEHOOD_CLASSIC_ADAPTER=hci1 python3 provider.py
   ```
   - A single dongle runs BLE + classic sequentially (no contention).
   - Two adapters (internal + dongle) scan BLE and classic **concurrently**.
4. Restart the service: `sudo systemctl restart lilly-host-scan`.

BT scans are serialized with an internal asyncio lock, so the fleet poller,
radar force-rescan, and `/api/scan` can all run at once without colliding on
the shared BLE adapter (`org.bluez.Error.InProgress`).

## Config (env vars)

| Variable | Default | Purpose |
|----------|---------|---------|
| `HOST_SCAN_PROVIDER_PORT` | `8096` | HTTP port |
| `HOST_BT_SCAN_SECONDS` | `5` | BLE scan window per pass |
| `HOST_WIFI_SCAN_SECONDS` | `15` | nmcli scan timeout |
| `HOST_SCAN_CACHE_SECONDS` | `5` | Results cached for this long |
| `HOST_FLEET_BT_SECONDS` | `45` | Fleet `/bluetooth/scan` cache TTL (fast responses) |
| `HOST_FLEET_WIFI_SECONDS` | `20` | Fleet `/wifi/scan` cache TTL |
| `HOST_GPS_LAT` / `HOST_GPS_LNG` | — | Explicit host GPS for the radar map |
| `HOST_GPS_DEFAULT` | `43.862564,-79.382863` | Fallback host GPS (saved home) |
| `BLUEHOOD_ADAPTER` / `BLUEHOOD_ADAPTER_BLE` / `HOST_BT_BLE_ADAPTER` | auto | BT adapter for BLE (e.g. `hci0`) |
| `BLUEHOOD_CLASSIC_ADAPTER` / `BLUEHOOD_ADAPTER_CLASSIC` / `HOST_BT_CLASSIC_ADAPTER` | auto | BT adapter for classic (e.g. `hci1`) |