#!/usr/bin/env python3
"""
Lilly Sensor Server — runs on Termux, broadcasts all sensor data via HTTP.

Install on Termux:
  pip install fastapi uvicorn

Run:
  python3 termux_sensor_server.py --port 8099

Container fetches from http://<termux_ip>:8099/sensors/all
"""

import os, sys, json, re, time, asyncio, logging
from typing import Optional
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse
import uvicorn

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger("SensorServer")

PORT = int(os.environ.get("SENSOR_PORT", "8099"))
BATCH_INTERVAL = float(os.environ.get("SENSOR_BATCH_INTERVAL", "2.0"))

LATEST_SENSORS: dict = {}
LATEST_BATTERY: dict = {}
LATEST_LOCATION: dict = {}
LAST_UPDATE: float = 0.0
LIST_AVAILABLE: list[str] = []

# ─── GAME STATE ──────────────────────────────────────────────────
_CAR_GAME_ACTIVE = False
_CAR_GAME_START = 0.0
_CAR_GAME_DISTANCE_KM = 0.0
_CAR_GAME_LAST_SPEED_MPS = 0.0
_CAR_GAME_DIRECTION = ""
_CAR_GAME_PLAYER = "Driver"
_CAR_GAME_POLL_TASK = None

_FETCH_GAME_ACTIVE = False
_FETCH_GAME_START = 0.0
_FETCH_GAME_THROWS = 0
_FETCH_GAME_CATCHES = 0
_FETCH_GAME_SCORE = 0
_FETCH_GAME_LAST_FORCE = 0.0
_FETCH_GAME_DIRECTION = ""
_FETCH_GAME_POLL_TASK = None

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
                logger.debug(
                    f"Partial read ({len(new_data)} sensors), keeping previous ({len(LATEST_SENSORS)})"
                )
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
        return JSONResponse(
            status_code=404, content={"error": f"Sensor '{name}' not found"}
        )
    return {"name": name, "values": val, "timestamp": LAST_UPDATE}


@app.get("/sensors/{name}/live")
async def get_sensor_live(name: str):
    val = await read_sensor(name)
    if val is None:
        return JSONResponse(
            status_code=404, content={"error": f"Sensor '{name}' not found"}
        )
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
async def send_notification(
    title: str = "", content: str = "", priority: str = "default"
):
    out = await _run_cmd(
        "termux-notification",
        "-t",
        title,
        "-c",
        content,
        "--priority",
        priority,
        timeout=5.0,
    )
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
                    devices.append(
                        {
                            "name": dev.get("name", "Unknown"),
                            "address": dev.get("address", ""),
                            "rssi": dev.get("rssi", -100),
                            "paired": False,
                            "type": "scan",
                        }
                    )
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
                        devices.append(
                            {
                                "name": dev.get("name", "Unknown"),
                                "address": addr,
                                "rssi": dev.get("rssi", -100),
                                "paired": True,
                                "type": "paired",
                            }
                        )
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
                        distance = round(
                            10 ** ((ref_rssi - rssi) / (10 * path_loss_exp)), 2
                        )

                    # Determine band from frequency
                    band = "unknown"
                    if frequency:
                        if frequency < 3000:
                            band = "2.4GHz"
                        else:
                            band = "5GHz"

                    networks.append(
                        {
                            "ssid": ssid,
                            "bssid": bssid,
                            "frequency": frequency,
                            "band": band,
                            "rssi": rssi,
                            "distance": distance,
                            "security": net.get("security", ""),
                            "channel": net.get("channel", 0),
                        }
                    )
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


# ─── SCREEN + FOREGROUND APP (watch-together mode) ──────────────
# Lets Lilly see what's on the phone screen so she can comment on
# reels / videos the user is watching, instead of guessing from audio.

_SCREEN_CAPTURE_CACHE: str = ""
_SCREEN_CAPTURE_TS: float = 0.0
_SCREEN_CAPTURE_TTL: float = 3.0  # seconds — reuse recent capture


async def capture_screen_base64() -> Optional[str]:
    """Capture the phone screen via termux-screencap, return base64 PNG."""
    import base64 as _b64

    png = "/tmp/lilly_screen.png"
    await _run_cmd("termux-screencap", "-p", png, timeout=8.0)
    try:
        with open(png, "rb") as f:
            data = f.read()
        os.remove(png)
        if data:
            return _b64.b64encode(data).decode()
    except Exception as e:
        logger.debug(f"screen capture read failed: {e}")
    return None


async def get_screen_capture(force: bool = False) -> Optional[str]:
    """Return base64 PNG of the screen, cached briefly to avoid spamming screencap."""
    global _SCREEN_CAPTURE_CACHE, _SCREEN_CAPTURE_TS
    now = time.time()
    if (
        not force
        and _SCREEN_CAPTURE_CACHE
        and (now - _SCREEN_CAPTURE_TS) < _SCREEN_CAPTURE_TTL
    ):
        return _SCREEN_CAPTURE_CACHE
    data = await capture_screen_base64()
    if data:
        _SCREEN_CAPTURE_CACHE = data
        _SCREEN_CAPTURE_TS = now
    return data


async def get_foreground_app() -> Optional[str]:
    """Return the package name of the foreground app via dumpsys."""
    out = await _run_sh(
        "dumpsys activity activities 2>/dev/null | grep -m1 -oE 'topResumedActivity=[^ ]+ [^ ]+ com\\.[^/]+'",
        timeout=6.0,
    )
    m = re.search(r"com\.[^/]+", out)
    if m:
        return m.group(0)
    # Fallback: window focus
    out = await _run_sh(
        "dumpsys window windows 2>/dev/null | grep -m1 -oE 'mCurrentFocus=[^ ]+ com\\.[^/]+'",
        timeout=6.0,
    )
    m = re.search(r"com\.[^/]+", out)
    return m.group(0) if m else None


@app.get("/screen/capture")
async def screen_capture(force: bool = False):
    """Return the current phone screen as base64 PNG."""
    data = await get_screen_capture(force=force)
    if not data:
        return {"error": "screen capture failed", "image_base64": None}
    return {"image_base64": data, "format": "png", "timestamp": time.time()}


@app.get("/app/foreground")
async def foreground_app():
    """Return the currently foreground app package name."""
    pkg = await get_foreground_app()
    return {"package": pkg, "timestamp": time.time()}


# ─── GAME HELPERS ───────────────────────────────────────────────


def _get_accel_magnitude() -> float:
    vals = LATEST_SENSORS.get("Accelerometer Sensor", [])
    if not vals:
        vals = LATEST_SENSORS.get("Linear Acceleration Sensor", [])
    if not vals:
        return 9.8
    x, y, z = vals[:3]
    return (x * x + y * y + z * z) ** 0.5


def _get_light_lux() -> float:
    vals = LATEST_SENSORS.get("Light Sensor", [])
    return float(vals[0]) if vals else 0.0


def _get_heading_deg() -> float:
    vals = LATEST_SENSORS.get("Rotation Vector Sensor", [])
    if not vals:
        vals = LATEST_SENSORS.get("Game Rotation Vector Sensor", [])
    if not vals:
        return 0.0
    # alpha is the compass heading in radians
    import math

    alpha = vals[0] if len(vals) > 0 else 0.0
    return math.degrees(alpha) % 360.0


def _get_speed_mps() -> float:
    loc = LATEST_LOCATION
    if not loc:
        return 0.0
    return float(loc.get("speed", 0.0))


def _direction_name(heading: float) -> str:
    dirs = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
    idx = int(((heading + 22.5) % 360.0) / 45.0)
    return dirs[idx]


# ─── CAR RIDE GAME ──────────────────────────────────────────────


@app.post("/api/game_guide/car_ride/start")
async def car_ride_start(request: dict | None = None):
    global \
        _CAR_GAME_ACTIVE, \
        _CAR_GAME_START, \
        _CAR_GAME_DISTANCE_KM, \
        _CAR_GAME_LAST_SPEED_MPS, \
        _CAR_GAME_PLAYER
    _CAR_GAME_ACTIVE = True
    _CAR_GAME_START = time.time()
    _CAR_GAME_DISTANCE_KM = 0.0
    _CAR_GAME_LAST_SPEED_MPS = 0.0
    _CAR_GAME_PLAYER = (
        (request or {}).get("player_name", "Driver")
        if isinstance(request, dict)
        else "Driver"
    )
    return {"status": "started", "player": _CAR_GAME_PLAYER}


@app.get("/api/car_game")
async def car_game_state():
    global \
        _CAR_GAME_DISTANCE_KM, \
        _CAR_GAME_LAST_SPEED_MPS, \
        _CAR_GAME_DIRECTION, \
        _CAR_GAME_ACTIVE
    if not _CAR_GAME_ACTIVE:
        return {"active": False}

    speed = _get_speed_mps()
    now = time.time()
    dt = 2.0  # polling interval
    if now - _CAR_GAME_START > 0:
        dt = min(now - _CAR_GAME_START, 2.0)
    _CAR_GAME_DISTANCE_KM += speed * dt / 1000.0
    _CAR_GAME_LAST_SPEED_MPS = speed
    heading = _get_heading_deg()
    _CAR_GAME_DIRECTION = _direction_name(heading)

    return {
        "active": True,
        "speed": round(speed, 1),
        "direction": _CAR_GAME_DIRECTION,
        "heading": round(heading, 1),
        "light": round(_get_light_lux(), 1),
        "position_km": round(_CAR_GAME_DISTANCE_KM, 3),
        "score": round(_CAR_GAME_DISTANCE_KM * 100, 0),
        "player": _CAR_GAME_PLAYER,
    }


@app.post("/api/car_game/narrate")
async def car_game_narrate():
    if not _CAR_GAME_ACTIVE:
        return {"narrative": "Start a drive first!"}
    state = await car_game_state()
    speed = state.get("speed", 0.0)
    direction = state.get("direction", "")
    light = state.get("light", 0.0)
    pos = state.get("position_km", 0.0)

    if speed < 1.0:
        narrative = f"Stopped at {pos:.2f} km. {direction} ahead."
    elif speed < 10.0:
        narrative = f"Cruising {direction} at {speed:.0f} m/s. {pos:.2f} km traveled."
    else:
        narrative = f"Zooming {direction} — {speed:.0f} m/s! {pos:.2f} km on the clock."

    if light > 10000:
        narrative += " Sunny out there."
    elif light < 10:
        narrative += " Dark now."

    return {"narrative": narrative, **state}


@app.post("/api/car_game/stop")
async def car_game_stop():
    global _CAR_GAME_ACTIVE
    if not _CAR_GAME_ACTIVE:
        return {"trip_km": 0.0}
    state = await car_game_state()
    _CAR_GAME_ACTIVE = False
    return {"trip_km": round(state.get("position_km", 0.0), 2)}


# ─── FETCH GAME ─────────────────────────────────────────────────


@app.post("/api/game_guide/fetch/start")
async def fetch_start(request: dict | None = None):
    global \
        _FETCH_GAME_ACTIVE, \
        _FETCH_GAME_START, \
        _FETCH_GAME_THROWS, \
        _FETCH_GAME_CATCHES, \
        _FETCH_GAME_SCORE
    _FETCH_GAME_ACTIVE = True
    _FETCH_GAME_START = time.time()
    _FETCH_GAME_THROWS = 0
    _FETCH_GAME_CATCHES = 0
    _FETCH_GAME_SCORE = 0
    return {"status": "started"}


@app.get("/api/fetch_game")
async def fetch_state():
    global \
        _FETCH_GAME_ACTIVE, \
        _FETCH_GAME_THROWS, \
        _FETCH_GAME_CATCHES, \
        _FETCH_GAME_SCORE, \
        _FETCH_GAME_LAST_FORCE, \
        _FETCH_GAME_DIRECTION
    if not _FETCH_GAME_ACTIVE:
        return {"active": False}

    accel = _get_accel_magnitude()
    heading = _get_heading_deg()
    direction = _direction_name(heading)
    light = _get_light_lux()

    # Auto-detect throw: sudden acceleration spike
    if accel > 15.0 and _FETCH_GAME_LAST_FORCE < 12.0:
        _FETCH_GAME_THROWS += 1
        _FETCH_GAME_LAST_FORCE = accel
    elif accel < 12.0:
        _FETCH_GAME_LAST_FORCE = accel

    return {
        "active": True,
        "force": round(accel, 1),
        "throws": _FETCH_GAME_THROWS,
        "catches": _FETCH_GAME_CATCHES,
        "score": _FETCH_GAME_SCORE,
        "heading": round(heading, 1),
        "direction": direction,
        "light": round(light, 1),
        "wind_speed_ms": round(abs(accel - 9.8) * 0.5, 1),
        "wind_direction": heading,
        "weather_desc": "clear"
        if light > 100
        else "overcast"
        if light > 10
        else "dark",
    }


@app.post("/api/fetch_game/catch")
async def fetch_catch():
    global _FETCH_GAME_CATCHES, _FETCH_GAME_SCORE
    if not _FETCH_GAME_ACTIVE:
        return {"narrative": "Start the fetch game first!"}
    state = await fetch_state()
    force = state.get("force", 0.0)
    _FETCH_GAME_CATCHES += 1
    _FETCH_GAME_SCORE += max(1, int(force * 10))
    narrative = f"Caught it! Force {force:.1f} m/s². Score {_FETCH_GAME_SCORE}."
    return {"narrative": narrative, **state}


@app.post("/api/fetch_game/stop")
async def fetch_stop():
    global _FETCH_GAME_ACTIVE
    if not _FETCH_GAME_ACTIVE:
        return {"throws": 0, "catches": 0, "score": 0}
    state = await fetch_state()
    _FETCH_GAME_ACTIVE = False
    return {
        "throws": _FETCH_GAME_THROWS,
        "catches": _FETCH_GAME_CATCHES,
        "score": _FETCH_GAME_SCORE,
    }


# ─── MAIN ───────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Lilly Sensor Server for Termux")
    parser.add_argument("--port", type=int, default=PORT, help=f"Port (default {PORT})")
    parser.add_argument(
        "--interval",
        type=float,
        default=BATCH_INTERVAL,
        help=f"Sensor poll interval in seconds (default {BATCH_INTERVAL})",
    )
    args = parser.parse_args()
    PORT = args.port
    BATCH_INTERVAL = args.interval
    logger.info(
        f"Starting sensor server on port {PORT}, polling every {BATCH_INTERVAL}s"
    )
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info")
