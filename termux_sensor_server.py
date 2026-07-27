#!/usr/bin/env python3
"""
Lilly Sensor Server — runs on Termux, broadcasts all sensor data via HTTP.

Install on Termux:
  pip install fastapi uvicorn

Run:
  python3 termux_sensor_server.py --port 8099

Container fetches from http://<termux_ip>:8099/sensors/all
"""
import os, sys, json, time, asyncio, logging
from typing import Optional
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse
import uvicorn

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("SensorServer")

PORT = int(os.environ.get("SENSOR_PORT", "8099"))
BATCH_INTERVAL = float(os.environ.get("SENSOR_BATCH_INTERVAL", "2.0"))

LATEST_SENSORS: dict = {}
LATEST_BATTERY: dict = {}
LATEST_LOCATION: dict = {}
LAST_UPDATE: float = 0.0
LIST_AVAILABLE: list[str] = []

# ─── ASYNC SUBPROCESS HELPERS ───────────────────────────────────

async def _run_cmd(*args: str, timeout: float = 5.0) -> str:
    """Run a command async, return stdout."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return stdout.decode().strip()
    except Exception as e:
        logger.debug(f"cmd failed ({args[0]}): {e}")
        return ""

async def _run_sh(cmd_str: str, timeout: float = 5.0) -> str:
    """Run a shell command string async via sh -c."""
    try:
        proc = await asyncio.create_subprocess_shell(
            cmd_str,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return stdout.decode().strip()
    except asyncio.TimeoutError:
        logger.debug(f"cmd timed out: {cmd_str[:60]}")
        return ""
    except Exception as e:
        logger.debug(f"sh failed: {e}")
        return ""

# ─── SENSOR READING ─────────────────────────────────────────────

async def read_all_sensors() -> dict:
    """Read all sensors in a single termux-sensor call (non-blocking)."""
    out = await _run_sh("termux-sensor -a -n 1", timeout=15.0)
    if not out:
        return {}
    try:
        data = json.loads(out)
        result = {}
        for name, info in data.items():
            if isinstance(info, dict):
                result[name] = info.get("values", [])
            else:
                result[name] = info
        return result
    except json.JSONDecodeError:
        return {}

async def read_sensor(name: str) -> Optional[list]:
    """Read a single sensor (non-blocking)."""
    out = await _run_sh(f"termux-sensor -s '{name}' -n 1", timeout=10.0)
    if not out:
        return None
    try:
        data = json.loads(out)
        if name in data:
            return data[name].get("values", [])
    except json.JSONDecodeError:
        pass
    return None

async def list_sensors() -> list[str]:
    """List all available sensors."""
    out = await _run_cmd("termux-sensor", "-l", timeout=5.0)
    if not out:
        return []
    try:
        data = json.loads(out)
        if isinstance(data, dict) and "sensors" in data:
            return data["sensors"]
    except json.JSONDecodeError:
        pass
    return []

async def read_battery() -> dict:
    """Read battery status."""
    out = await _run_cmd("termux-battery-status", timeout=3.0)
    if not out:
        return {}
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return {}

async def read_location() -> dict:
    """Read GPS location."""
    out = await _run_cmd("termux-location", timeout=10.0)
    if not out:
        return {}
    try:
        data = json.loads(out)
        return {
            "latitude": data.get("latitude", 0.0),
            "longitude": data.get("longitude", 0.0),
            "altitude": data.get("altitude", 0.0),
            "speed": data.get("speed", 0.0),
            "bearing": data.get("bearing", 0.0),
            "accuracy": data.get("accuracy", 0.0),
        }
    except json.JSONDecodeError:
        return {}

# ─── BACKGROUND UPDATE LOOP ─────────────────────────────────────

_battery_tick = 0
_location_tick = 0
_wifi_tick = 0

async def sensor_update_loop():
    """Continuously read sensors and cache results (non-blocking)."""
    global LATEST_SENSORS, LATEST_BATTERY, LATEST_LOCATION, LAST_UPDATE, LIST_AVAILABLE
    global _battery_tick, _location_tick, _wifi_tick

    LIST_AVAILABLE = await list_sensors()
    logger.info(f"Found {len(LIST_AVAILABLE)} sensors")

    while True:
        try:
            new_data = await read_all_sensors()
            if new_data and len(new_data) > 3:
                LATEST_SENSORS = new_data
                LAST_UPDATE = time.time()
            elif new_data and len(LATEST_SENSORS) > len(new_data):
                logger.debug(f"Partial read ({len(new_data)} sensors), keeping previous ({len(LATEST_SENSORS)})")
            else:
                LATEST_SENSORS = new_data
                LAST_UPDATE = time.time()

            _battery_tick += 1
            if _battery_tick % 10 == 0:
                LATEST_BATTERY = await read_battery()

            _location_tick += 1
            if _location_tick % 30 == 0:
                LATEST_LOCATION = await read_location()

            _wifi_tick += 1
            if _wifi_tick % 75 == 0:  # every ~150 seconds (75 * 2s)
                await scan_wifi_networks()

        except Exception as e:
            logger.error(f"Sensor update error: {e}")

        await asyncio.sleep(BATCH_INTERVAL)

# ─── FASTAPI APP ────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    asyncio.create_task(sensor_update_loop())
    yield

app = FastAPI(lifespan=lifespan)

@app.get("/sensors/all")
async def get_all_sensors():
    return {
        "sensors": LATEST_SENSORS,
        "timestamp": LAST_UPDATE,
        "count": len(LATEST_SENSORS),
    }

@app.get("/sensors/all/live")
async def get_all_sensors_live():
    data = await read_all_sensors()
    return {
        "sensors": data,
        "timestamp": time.time(),
        "count": len(data),
    }

@app.get("/sensors/{name}")
async def get_sensor(name: str):
    val = LATEST_SENSORS.get(name)
    if val is None:
        name_lower = name.lower()
        for k, v in LATEST_SENSORS.items():
            if name_lower in k.lower():
                return {"name": k, "values": v, "timestamp": LAST_UPDATE}
        return JSONResponse(status_code=404, content={"error": f"Sensor '{name}' not found"})
    return {"name": name, "values": val, "timestamp": LAST_UPDATE}

@app.get("/sensors/{name}/live")
async def get_sensor_live(name: str):
    val = await read_sensor(name)
    if val is None:
        return JSONResponse(status_code=404, content={"error": f"Sensor '{name}' not found"})
    return {"name": name, "values": val, "timestamp": time.time()}

@app.get("/sensors/list")
async def get_sensor_list():
    return {"sensors": LIST_AVAILABLE, "count": len(LIST_AVAILABLE)}

@app.get("/battery")
async def get_battery():
    return {"battery": LATEST_BATTERY, "timestamp": LAST_UPDATE}

@app.get("/battery/live")
async def get_battery_live():
    data = await read_battery()
    return {"battery": data, "timestamp": time.time()}

@app.get("/location")
async def get_location():
    return {"location": LATEST_LOCATION, "timestamp": LAST_UPDATE}

@app.get("/location/live")
async def get_location_live():
    data = await read_location()
    return {"location": data, "timestamp": time.time()}

@app.get("/health")
async def health():
    return {
        "status": "ok",
        "sensor_count": len(LATEST_SENSORS),
        "last_update": LAST_UPDATE,
        "age_sec": round(time.time() - LAST_UPDATE, 1) if LAST_UPDATE else None,
        "battery": LATEST_BATTERY.get("percentage", None),
    }

@app.post("/shell")
async def shell_command(cmd: str = ""):
    out = await _run_sh(cmd, timeout=15.0)
    return {"output": out}

@app.get("/notification/list")
async def get_notifications():
    out = await _run_cmd("termux-notification-list", timeout=5.0)
    if not out:
        return {"notifications": []}
    try:
        return {"notifications": json.loads(out)}
    except json.JSONDecodeError:
        return {"notifications": []}

@app.post("/notification/send")
async def send_notification(title: str = "", content: str = "", priority: str = "default"):
    out = await _run_cmd("termux-notification", "-t", title, "-c", content, "--priority", priority, timeout=5.0)
    return {"sent": True, "output": out}

# ─── BLUETOOTH SCANNING ──────────────────────────────────────────
LATEST_BLUETOOTH: list = []
LAST_BT_SCAN: float = 0.0
BT_SCAN_INTERVAL: float = 10.0  # minimum seconds between scans

LATEST_WIFI: list = []
LAST_WIFI_SCAN: float = 0.0
WIFI_SCAN_INTERVAL: float = 15.0  # minimum seconds between WiFi scans

async def scan_bluetooth_devices() -> list:
    """Scan for nearby Bluetooth devices using termux-bluetooth-scan."""
    global LATEST_BLUETOOTH, LAST_BT_SCAN
    
    now = time.time()
    if LATEST_BLUETOOTH and (now - LAST_BT_SCAN) < BT_SCAN_INTERVAL:
        return LATEST_BLUETOOTH
    
    devices = []
    
    # Try termux-bluetooth-scan (requires BLUETOOTH_SCAN permission)
    out = await _run_cmd("termux-bluetooth-scan", timeout=15.0)
    if out:
        try:
            scan_data = json.loads(out)
            if isinstance(scan_data, list):
                for dev in scan_data:
                    devices.append({
                        "name": dev.get("name", "Unknown"),
                        "address": dev.get("address", ""),
                        "rssi": dev.get("rssi", -100),
                        "paired": False,
                        "type": "scan",
                    })
        except json.JSONDecodeError:
            pass
    
    # Also get paired devices
    out_paired = await _run_cmd("termux-bluetooth-paired", timeout=5.0)
    if out_paired:
        try:
            paired_data = json.loads(out_paired)
            if isinstance(paired_data, list):
                paired_addresses = {d["address"] for d in devices}
                for dev in paired_data:
                    addr = dev.get("address", "")
                    if addr not in paired_addresses:
                        devices.append({
                            "name": dev.get("name", "Unknown"),
                            "address": addr,
                            "rssi": dev.get("rssi", -100),
                            "paired": True,
                            "type": "paired",
                        })
                    else:
                        # Mark as paired if found in scan results too
                        for d in devices:
                            if d["address"] == addr:
                                d["paired"] = True
        except json.JSONDecodeError:
            pass
    
    # Get Bluetooth info (adapter state, enabled status)
    info_out = await _run_cmd("termux-bluetooth-info", timeout=5.0)
    bt_info = {}
    if info_out:
        try:
            bt_info = json.loads(info_out)
        except json.JSONDecodeError:
            pass
    
    LATEST_BLUETOOTH = devices
    LAST_BT_SCAN = now
    
    return devices

@app.get("/bluetooth/scan")
async def get_bluetooth_scan():
    """Scan for nearby Bluetooth devices."""
    devices = await scan_bluetooth_devices()
    return {
        "devices": devices,
        "count": len(devices),
        "timestamp": time.time(),
    }

@app.get("/bluetooth/scan/live")
async def get_bluetooth_scan_live():
    """Force a fresh Bluetooth scan."""
    global LAST_BT_SCAN
    LAST_BT_SCAN = 0.0  # Reset cache to force fresh scan
    devices = await scan_bluetooth_devices()
    return {
        "devices": devices,
        "count": len(devices),
        "timestamp": time.time(),
    }

# ─── WIFI SCANNING ──────────────────────────────────────────────

async def scan_wifi_networks() -> list:
    """Scan for nearby WiFi networks using termux-wifi-scaninfo."""
    global LATEST_WIFI, LAST_WIFI_SCAN
    
    now = time.time()
    if LATEST_WIFI and (now - LAST_WIFI_SCAN) < WIFI_SCAN_INTERVAL:
        return LATEST_WIFI
    
    networks = []
    
    out = await _run_cmd("termux-wifi-scaninfo", timeout=15.0)
    if out:
        try:
            scan_data = json.loads(out)
            if isinstance(scan_data, list):
                for net in scan_data:
                    ssid = net.get("ssid", "")
                    bssid = net.get("bssid", "")
                    frequency = net.get("frequency", 0)
                    rssi = net.get("rssi", -100)
                    
                    # Estimate distance from RSSI (log-distance path loss)
                    distance = None
                    if rssi and rssi != 0:
                        # Reference RSSI at 1m = -40 dBm, path loss exponent = 3.0 (indoor)
                        ref_rssi = -40.0
                        path_loss_exp = 3.0
                        distance = round(10 ** ((ref_rssi - rssi) / (10 * path_loss_exp)), 2)
                    
                    # Determine band from frequency
                    band = "unknown"
                    if frequency:
                        if frequency < 3000:
                            band = "2.4GHz"
                        else:
                            band = "5GHz"
                    
                    networks.append({
                        "ssid": ssid,
                        "bssid": bssid,
                        "frequency": frequency,
                        "band": band,
                        "rssi": rssi,
                        "distance": distance,
                        "security": net.get("security", ""),
                        "channel": net.get("channel", 0),
                    })
        except json.JSONDecodeError:
            pass
    
    LATEST_WIFI = networks
    LAST_WIFI_SCAN = now
    
    return networks

@app.get("/wifi/scan")
async def get_wifi_scan():
    """Scan for nearby WiFi networks."""
    networks = await scan_wifi_networks()
    return {
        "networks": networks,
        "count": len(networks),
        "timestamp": time.time(),
    }

@app.get("/wifi/scan/live")
async def get_wifi_scan_live():
    """Force a fresh WiFi scan."""
    global LAST_WIFI_SCAN
    LAST_WIFI_SCAN = 0.0  # Reset cache to force fresh scan
    networks = await scan_wifi_networks()
    return {
        "networks": networks,
        "count": len(networks),
        "timestamp": time.time(),
    }

# ─── MAIN ───────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Lilly Sensor Server for Termux")
    parser.add_argument("--port", type=int, default=PORT, help=f"Port (default {PORT})")
    parser.add_argument("--interval", type=float, default=BATCH_INTERVAL, help=f"Sensor poll interval in seconds (default {BATCH_INTERVAL})")
    args = parser.parse_args()
    PORT = args.port
    BATCH_INTERVAL = args.interval
    logger.info(f"Starting sensor server on port {PORT}, polling every {BATCH_INTERVAL}s")
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info")
