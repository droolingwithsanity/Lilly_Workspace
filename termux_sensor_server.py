#!/usr/bin/env python3
"""
Lilly Sensor Server v6.0 — runs on Termux, broadcasts all sensor data via HTTP.

Install on Termux:
  pip install fastapi uvicorn

Run:
  python3 termux_sensor_server.py --port 8099

Container fetches from http://<termux_ip>:8099/sensors/all
"""

import os, sys, json, re, time, asyncio, logging, subprocess, base64, shutil
from typing import Optional
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
import uvicorn

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger("SensorServer")

PORT = int(os.environ.get("SENSOR_PORT", "8099"))
BATCH_INTERVAL = float(os.environ.get("SENSOR_BATCH_INTERVAL", "2.0"))
PAIR_TOKEN = os.environ.get("LILLY_PAIR_TOKEN", "").strip()

LATEST_SENSORS: dict = {}
LATEST_BATTERY: dict = {}
LATEST_LOCATION: dict = {}
LAST_UPDATE: float = 0.0
LIST_AVAILABLE: list[str] = []

# ─── SENSOR CATEGORIES ────────────────────────────────────────────
# Map Android sensor names to the 6 synaptic categories.
_SENSOR_CATEGORIES = {
    "vision": [
        "Camera",
        "Depth Sensor",
        "Camera2",
        "Laser Sensor",
        "ToF Sensor",
    ],
    "orientation": [
        "Gyroscope",
        "Accelerometer",
        "Linear Acceleration",
        "Gravity",
        "Rotation Vector",
        "Game Rotation Vector",
        "Step Detector",
        "Step Counter",
        "Significant Motion",
    ],
    "acoustics": [
        "Microphone",
        "Audio",
        "Sound",
        "Ultrasonic",
    ],
    "touch": [
        "Fingerprint",
        "Touchscreen",
        "Pressure",
        "Temperature",
    ],
    "proximity": [
        "Proximity",
        "Magnetometer",
        "Compass",
    ],
    "ambient": [
        "Light",
        "Hall Effect",
        "Barometer",
        "Ambient Temperature",
        "Relative Humidity",
    ],
}


def _categorize_sensor(name: str) -> str | None:
    """Return category key for a sensor name, or None if uncategorized."""
    n = name.lower()
    for category, keywords in _SENSOR_CATEGORIES.items():
        for kw in keywords:
            if kw.lower() in n:
                return category
    return None


def get_categorized_sensors() -> dict:
    """Group latest sensor readings into the 6 categories."""
    result: dict = {}
    for category in _SENSOR_CATEGORIES.keys():
        result[category] = {}
    uncategorized: dict = {}
    for name, values in LATEST_SENSORS.items():
        cat = _categorize_sensor(name)
        if cat:
            result[cat][name] = values
        else:
            uncategorized[name] = values
    result["other"] = uncategorized
    return result


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


async def _run_sh_both(cmd_str: str, timeout: float = 5.0) -> tuple[str, str]:
    """Run a shell command async, return (stdout, stderr)."""
    try:
        proc = await asyncio.create_subprocess_shell(
            cmd_str,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr_ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return stdout.decode().strip(), stderr_.decode().strip()
    except asyncio.TimeoutError:
        logger.debug(f"cmd timed out: {cmd_str[:60]}")
        return "", "timeout"
    except Exception as e:
        logger.debug(f"sh failed: {e}")
        return "", str(e)


# ─── SENSOR READING ─────────────────────────────────────────────


async def read_all_sensors() -> dict:
    """Read all sensors in a single termux-sensor call (non-blocking).

    First reads all sensors via -a flag, then explicitly reads known Pixel 10
    sensors that may not appear in the batch read.
    """
    # Batch read all available sensors
    out = await _run_sh("termux-sensor -a -n 1", timeout=15.0)
    result = {}
    if out:
        try:
            data = json.loads(out)
            for name, info in data.items():
                if isinstance(info, dict):
                    result[name] = info.get("values", [])
                else:
                    result[name] = info
        except json.JSONDecodeError:
            pass

    # Explicitly read Pixel 10 sensors that may not appear in batch read
    # These are common Pixel 10 / Android 17 sensors
    PIXEL_10_SENSORS = [
        "game_rotation_vector",
        "geomagnetic_rotation_vector",
        "geomagnetic_field",
        "significant_motion",
        "step_detector",
        "heart_rate",
        "pose_6dof",
        "stationary_detect",
        "motion_detect",
        "absolute_heading",
        "heading",
        "low_latency_offbody_detect",
        "hinge_angle",
        "head_tracker",
        "accelerometer_uncalibrated",
        "gyroscope_uncalibrated",
        "magnetic_field_uncalibrated",
        "rotation_vector_uncalibrated",
    ]

    # Read sensors not already captured
    missing = [s for s in PIXEL_10_SENSORS if s not in result]
    if missing:
        # Read in batches of 4 to avoid overwhelming termux-sensor
        for i in range(0, len(missing), 4):
            batch = missing[i : i + 4]
            for sensor_name in batch:
                try:
                    val = await read_sensor(sensor_name)
                    if val is not None:
                        result[sensor_name] = val
                except Exception:
                    pass
            # Small delay between batches
            if i + 4 < len(missing):
                await asyncio.sleep(0.5)

    return result


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
    """Read GPS location.

    Uses termux-location -s (single update) with the GPS provider only.
    NOTE: never call plain `termux-location` (continuous) or `-s -p network`:
    Termux:API's LocationAPI writes a second top-level JSON document when a
    second fix arrives (cached + fresh, or network + gps) and dies with
    "JSON must have only one top-level value". GPS single-shot has no cached
    fix in the normal case, so it survives.
    """
    for provider, tmo in (("gps", 12.0), ("network", 8.0), ("passive", 6.0)):
        try:
            out = await _run_cmd(
                f"termux-location -s -p {provider} -d {int(tmo)}",
                timeout=tmo + 4.0,
            )
            if not out or not out.strip():
                continue
            data = json.loads(out)
            if data.get("latitude") is not None:
                return {
                    "latitude": data.get("latitude", 0.0),
                    "longitude": data.get("longitude", 0.0),
                    "altitude": data.get("altitude", 0.0),
                    "speed": data.get("speed", 0.0),
                    "bearing": data.get("bearing", 0.0),
                    "accuracy": data.get("accuracy", 0.0),
                    "provider": provider,
                }
        except Exception:
            continue
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


# ─── PAIR TOKEN AUTH ──────────────────────────────────────────────
# If LILLY_PAIR_TOKEN is set, require X-Pair-Token header on all requests
# except /health and /pair/public-key. This lets the web UI authenticate
# without needing the overlay APK.

_PUBLIC_PATHS = {"/health", "/pair/public-key", "/docs", "/openapi.json"}


async def _check_pair_token(request: Request):
    if not PAIR_TOKEN:
        return
    path = request.url.path
    for public in _PUBLIC_PATHS:
        if path == public or path.startswith(public + "/"):
            return
    token = request.headers.get("X-Pair-Token", "")
    if token != PAIR_TOKEN:
        return JSONResponse(
            status_code=401,
            content={
                "error": "unauthorized",
                "detail": "Invalid or missing X-Pair-Token",
            },
        )


@app.middleware("http")
async def pair_token_middleware(request: Request, call_next):
    response = await _check_pair_token(request)
    if response is not None:
        return response
    return await call_next(request)


@app.get("/pair/public-key")
async def pair_public_key():
    """Return the expected token name so the web UI knows what to send."""
    return {
        "token_name": "X-Pair-Token",
        "has_token": bool(PAIR_TOKEN),
        "hint": "Set LILLY_PAIR_TOKEN in Termux and in web UI settings",
        "version": "6.0",
    }


@app.get("/api/version")
async def sensor_server_version():
    return {
        "service": "lilly-sensor-server",
        "version": "6.0",
        "port": PORT,
        "pair_token_required": bool(PAIR_TOKEN),
    }


@app.post("/deploy")
async def deploy_update(request: Request):
    """Receive an updated file and save it.

    Body: {"filename": "termux_sensor_server.py", "content": "...", "sha256": "..."}

    This allows the AI server to push updates to the phone without SSH.
    Only accepts files in the home directory for security.
    """
    if PAIR_TOKEN:
        token = request.headers.get("X-Pair-Token", "")
        if token != PAIR_TOKEN:
            return JSONResponse(status_code=401, content={"error": "unauthorized"})

    try:
        body = await request.json()
        filename = body.get("filename", "")
        content = body.get("content", "")
        expected_sha = body.get("sha256", "")

        if not filename or not content:
            return JSONResponse(
                status_code=400, content={"error": "filename and content required"}
            )

        # Security: only allow specific files to be updated
        ALLOWED_FILES = {
            "termux_sensor_server.py",
            "lilly_skills.json",
            "sensor_skills.json",
        }
        if filename not in ALLOWED_FILES:
            return JSONResponse(
                status_code=403,
                content={
                    "error": f"File '{filename}' not in allowed list: {ALLOWED_FILES}"
                },
            )

        # Verify SHA256 if provided
        import hashlib

        actual_sha = hashlib.sha256(content.encode()).hexdigest()
        if expected_sha and actual_sha != expected_sha:
            return JSONResponse(
                status_code=400,
                content={
                    "error": "SHA256 mismatch",
                    "expected": expected_sha,
                    "actual": actual_sha,
                },
            )

        # Save to home directory
        import os

        home = os.path.expanduser("~")
        filepath = os.path.join(home, filename)

        # Backup existing file
        if os.path.exists(filepath):
            backup = f"{filepath}.bak"
            os.rename(filepath, backup)

        with open(filepath, "w") as f:
            f.write(content)

        logger.info(
            f"Deployed update: {filename} ({len(content)} bytes, sha256={actual_sha[:16]}...)"
        )

        return {
            "ok": True,
            "filename": filename,
            "size": len(content),
            "sha256": actual_sha,
            "path": filepath,
        }
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.post("/restart")
async def restart_server(request: Request):
    """Restart the sensor server (self-update after deploy)."""
    if PAIR_TOKEN:
        token = request.headers.get("X-Pair-Token", "")
        if token != PAIR_TOKEN:
            return JSONResponse(status_code=401, content={"error": "unauthorized"})

    import subprocess
    import sys

    logger.info("Restarting sensor server...")

    # Schedule restart after response is sent
    async def _do_restart():
        await asyncio.sleep(1.0)
        subprocess.Popen(
            [sys.executable, os.path.abspath(__file__), "--port", str(PORT)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        os._exit(0)

    asyncio.create_task(_do_restart())
    return {"ok": True, "message": "Restarting..."}


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "version": "6.0",
        "port": PORT,
        "sensors_cached": len(LATEST_SENSORS),
        "last_update": LAST_UPDATE,
        "pair_token_required": bool(PAIR_TOKEN),
    }


@app.get("/sensors/all")
async def get_all_sensors():
    """Return all sensor data with dynamic availability detection."""
    # Build available dict from actual sensor list + current readings
    available = {}
    for sensor_name in LIST_AVAILABLE:
        # Check if sensor has data in latest readings
        sensor_key = sensor_name.lower().replace(" ", "_").replace("-", "_")
        has_data = sensor_key in LATEST_SENSORS or sensor_name in LATEST_SENSORS
        available[sensor_name] = has_data
    # Also check for sensors in LATEST_SENSORS not in LIST_AVAILABLE
    for sensor_name in LATEST_SENSORS:
        if sensor_name not in available and sensor_name != "source":
            available[sensor_name] = True
    return {
        "sensors": LATEST_SENSORS,
        "available": available,
        "timestamp": LAST_UPDATE,
        "count": len(LATEST_SENSORS),
        "device": "Pixel 10",
        "model": "Pixel 10",
        "manufacturer": "Google",
        "android_version": "17",
        "sdk_int": 37,
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


@app.get("/sensors/categorized")
async def get_sensors_categorized():
    """Return sensors grouped into the 6 synaptic categories."""
    return {
        "categories": get_categorized_sensors(),
        "timestamp": LAST_UPDATE,
    }


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


@app.post("/shell")
async def shell_command(request: Request, cmd: str = ""):
    # Accept the command from the query string OR a JSON body. The overlay app
    # and lilly_ai historically send {"command": ...}; new clients send {"cmd": ...}.
    if request.headers.get("content-type", "").startswith("application/json"):
        try:
            body = await request.json()
        except Exception:
            body = {}
        cmd = body.get("cmd") or body.get("command") or cmd
    out, err = await _run_sh_both(cmd, timeout=15.0)
    return {"output": out, "stderr": err}


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
    scan_succeeded = False
    if out:
        try:
            scan_data = json.loads(out)
            if isinstance(scan_data, list):
                scan_succeeded = True
                for dev in scan_data:
                    rssi = dev.get("rssi", -100)
                    # Only mark as live if the active scan returned a real signal
                    live = rssi > -100
                    devices.append(
                        {
                            "name": dev.get("name", "Unknown"),
                            "address": dev.get("address", ""),
                            "rssi": rssi,
                            "paired": False,
                            "type": "scan",
                            "live": live,
                        }
                    )
        except json.JSONDecodeError:
            pass

    # Also get paired devices — these are ghost entries (bonded but not in range).
    # Mark them live:False so the tracker knows not to use them for location correlation.
    out_paired = await _run_cmd("termux-bluetooth-paired", timeout=5.0)
    if out_paired:
        try:
            paired_data = json.loads(out_paired)
            if isinstance(paired_data, list):
                paired_addresses = {d["address"] for d in devices}
                for dev in paired_data:
                    addr = dev.get("address", "")
                    if addr not in paired_addresses:
                        # Paired-only device: not seen in active scan — always ghost
                        devices.append(
                            {
                                "name": dev.get("name", "Unknown"),
                                "address": addr,
                                "rssi": dev.get("rssi", -100),
                                "paired": True,
                                "type": "paired",
                                "live": False,
                            }
                        )
                    else:
                        # Found in both scan and paired list — mark as paired,
                        # but keep the live flag set by the scan result above
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
    """Scan for nearby Bluetooth devices.

    Response shape:
      devices      — live detections only (rssi > -100, active scan hit)
      ghost_devices — paired-only, not currently in range (live: false)
      count        — number of live devices
    """
    all_devices = await scan_bluetooth_devices()
    live = [d for d in all_devices if d.get("live", False)]
    ghosts = [d for d in all_devices if not d.get("live", False)]
    return {
        "devices": live,
        "count": len(live),
        "ghost_devices": ghosts,
        "ghost_count": len(ghosts),
        "timestamp": time.time(),
    }


@app.get("/bluetooth/scan/live")
async def get_bluetooth_scan_live():
    """Force a fresh Bluetooth scan."""
    global LAST_BT_SCAN
    LAST_BT_SCAN = 0.0  # Reset cache to force fresh scan
    all_devices = await scan_bluetooth_devices()
    live = [d for d in all_devices if d.get("live", False)]
    ghosts = [d for d in all_devices if not d.get("live", False)]
    return {
        "devices": live,
        "count": len(live),
        "ghost_devices": ghosts,
        "ghost_count": len(ghosts),
        "timestamp": time.time(),
    }


# ─── BLUETOOTH ADVERTISING ──────────────────────────────────────────
# Uses bleak to advertise as a BLE peripheral — like earbuds in pairing mode.
# The phone broadcasts a name, service data, and a URL to Lilly's web UI
# where Lilly presents as an image/avatar.

_BT_ADVERTISING = False
_BT_ADVERTISER = None
_BT_ADVERTISER_TASK: Optional[asyncio.Task] = None

# Default advertising configuration — can be customized via POST endpoint.
_BT_ADVERTISE_CONFIG = {
    "name": "Lilly Pup",
    "image": "/static/lilly/puppy-avatar.svg",  # avatar image shown on web UI
    "service_uuid": "0000feed-0000-1000-8000-00805f9b34fb",
    "service_data_key": "6942",  # short key for sensor data
    "web_ui_url": "http://100.93.131.114:8098/lilly/advertise",  # host: lilly_ai.py
    "appearance": 0x0000,  # generic
    "tx_power": -6,
    "include_name": True,
    "interval_min": 0x0020,  # 20ms
    "interval_max": 0x0040,  # 40ms
}


async def _run_advertiser_loop(
    name: str,
    service_uuid: str,
    service_data_key: str,
    web_ui_url: str,
    image: str,
    interval_min: int,
    interval_max: int,
) -> None:
    """Background loop that keeps the BLE advertiser alive.

    bleak's BleakAdvertiser on Android can be finicky — advertising may
    stop unexpectedly.  This loop restarts it if needed.
    """
    global _BT_ADVERTISER, _BT_ADVERTISING

    while _BT_ADVERTISING:
        try:
            from bleak import BleakAdvertiser

            if _BT_ADVERTISER is None:
                _BT_ADVERTISER = BleakAdvertiser()

            # Encode the URL and image path into the service data (truncated to
            # fit 31-byte advertising packet limit; the full UI is served via web).
            url_suffix = web_ui_url.split("/")[-1] if "/" in web_ui_url else web_ui_url
            img_suffix = image.split("/")[-1] if "/" in image else image
            payload = f"{service_data_key}:{url_suffix}:{img_suffix}"

            await _BT_ADVERTISER.start(
                name=name,
                service_uuids=[service_uuid],
                service_data={service_uuid: payload.encode()},
                timeout=0,  # indefinite
            )
            logger.info(f"BLE advertising started as '{name}' with image {img_suffix}")

            # Keep the advertiser alive until stopped
            while _BT_ADVERTISING:
                await asyncio.sleep(1.0)

        except ImportError:
            logger.error("bleak not installed on Termux. Run: pip install bleak")
            _BT_ADVERTISING = False
            break
        except Exception as e:
            logger.error(f"BLE advertiser error: {e}")
            _BT_ADVERTISING = False
            if _BT_ADVERTISER:
                try:
                    await _BT_ADVERTISER.stop()
                except Exception:
                    pass
                _BT_ADVERTISER = None
            await asyncio.sleep(2.0)


@app.get("/bluetooth/advertise/status")
async def get_advertise_status():
    """Check whether the phone is currently advertising as a BLE device."""
    return {
        "advertising": _BT_ADVERTISING,
        "config": _BT_ADVERTISE_CONFIG,
        "timestamp": time.time(),
    }


@app.post("/bluetooth/advertise")
async def start_advertising(
    name: str = "",
    image: str = "",
    service_uuid: str = "",
    web_ui_url: str = "",
    tx_power: int = -6,
    interval_min: int = 0x0020,
    interval_max: int = 0x0040,
):
    """Start advertising as a BLE peripheral — like earbuds in pairing mode.

    The phone broadcasts a name, service UUID, and a URL to Lilly's web UI
    where Lilly presents as an image/avatar.  Nearby phones will see the
    device appear in their Bluetooth scan with the configured name and
    can connect or read the service data to discover the web UI URL.

    When a device pairs, it will see the advertised name and image,
    appearing as a discoverable device in pairing mode — just like
    earbuds do when you open the case.

    Args:
        name: Device name to advertise (default: "Lilly Pup")
        image: Avatar image path/URL to present (default: puppy-avatar.png)
        service_uuid: BLE service UUID (default: 0000feed-...)
        web_ui_url: URL to Lilly's web UI (default: http://host:8098/lilly/advertise)
        tx_power: Transmit power level
        interval_min/max: Advertising interval in BLE units

    Returns:
        {"ok": true, "name": "...", "advertising": true, ...}
    """
    global _BT_ADVERTISING, _BT_ADVERTISER, _BT_ADVERTISER_TASK
    global _BT_ADVERTISE_CONFIG

    # Pre-flight: verify bleak is installed before committing to advertising.
    # Otherwise the background loop silently fails and the host thinks it
    # started when it didn't — leading to "phone advertiser unreachable".
    try:
        import bleak  # noqa: F401
    except ImportError:
        return {
            "ok": False,
            "error": "bleak not installed on Termux — run: pip install bleak",
            "advertising": False,
            "requires": "bleak",
        }

    if _BT_ADVERTISING:
        return {
            "ok": True,
            "already_advertising": True,
            "name": _BT_ADVERTISE_CONFIG["name"],
            "image": _BT_ADVERTISE_CONFIG["image"],
            "message": "Already advertising",
        }

    # Update config
    if name:
        _BT_ADVERTISE_CONFIG["name"] = name
    if image:
        _BT_ADVERTISE_CONFIG["image"] = image
    if service_uuid:
        _BT_ADVERTISE_CONFIG["service_uuid"] = service_uuid
    if web_ui_url:
        _BT_ADVERTISE_CONFIG["web_ui_url"] = web_ui_url
    _BT_ADVERTISE_CONFIG["tx_power"] = tx_power
    _BT_ADVERTISE_CONFIG["interval_min"] = interval_min
    _BT_ADVERTISE_CONFIG["interval_max"] = interval_max

    cfg = _BT_ADVERTISE_CONFIG
    _BT_ADVERTISING = True
    _BT_ADVERTISER = None

    # Start the advertiser loop in the background
    _BT_ADVERTISER_TASK = asyncio.create_task(
        _run_advertiser_loop(
            name=cfg["name"],
            service_uuid=cfg["service_uuid"],
            service_data_key=cfg["service_data_key"],
            web_ui_url=cfg["web_ui_url"],
            image=cfg["image"],
            interval_min=cfg["interval_min"],
            interval_max=cfg["interval_max"],
        )
    )

    return {
        "ok": True,
        "name": cfg["name"],
        "image": cfg["image"],
        "service_uuid": cfg["service_uuid"],
        "web_ui_url": cfg["web_ui_url"],
        "advertising": True,
        "message": f"Started advertising as '{cfg['name']}'",
        "timestamp": time.time(),
        "pairing_mode": True,  # appears as a discoverable device in pairing mode
    }


@app.post("/bluetooth/advertise/stop")
async def stop_advertising():
    """Stop BLE advertising and return to discoverable/scannable mode only."""
    global _BT_ADVERTISING, _BT_ADVERTISER, _BT_ADVERTISER_TASK

    if not _BT_ADVERTISING and _BT_ADVERTISER_TASK is None:
        return {
            "ok": True,
            "was_advertising": False,
            "advertising": False,
            "message": "Was not advertising",
        }

    _BT_ADVERTISING = False

    if _BT_ADVERTISER_TASK:
        _BT_ADVERTISER_TASK.cancel()
        try:
            await _BT_ADVERTISER_TASK
        except asyncio.CancelledError:
            pass
        _BT_ADVERTISER_TASK = None

    if _BT_ADVERTISER:
        try:
            await _BT_ADVERTISER.stop()
        except Exception:
            pass
        _BT_ADVERTISER = None

    return {
        "ok": True,
        "was_advertising": True,
        "advertising": False,
        "message": "Stopped BLE advertising",
        "timestamp": time.time(),
    }


@app.get("/bluetooth/advertise/config")
async def get_advertise_config():
    """Get the current advertising configuration."""
    return _BT_ADVERTISE_CONFIG


@app.post("/bluetooth/advertise/configure")
async def configure_advertising(
    name: str = "",
    image: str = "",
    service_uuid: str = "",
    service_data_key: str = "",
    web_ui_url: str = "",
    appearance: int = 0,
    tx_power: int = -6,
    interval_min: int = 0x0020,
    interval_max: int = 0x0040,
):
    """Update the advertising configuration without starting/stopping."""
    global _BT_ADVERTISE_CONFIG

    updates = {
        "name": name,
        "image": image,
        "service_uuid": service_uuid,
        "service_data_key": service_data_key,
        "web_ui_url": web_ui_url,
        "appearance": appearance,
        "tx_power": tx_power,
        "interval_min": interval_min,
        "interval_max": interval_max,
    }
    # Only update non-empty values
    for k, v in updates.items():
        if v != "" and v != 0:
            _BT_ADVERTISE_CONFIG[k] = v

    return {
        "ok": True,
        "config": _BT_ADVERTISE_CONFIG,
        "message": "Configuration updated",
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


@app.post("/scan/trigger")
async def trigger_scan():
    """
    Force an immediate BT + WiFi scan concurrently and return combined results.
    Called by lilly_ai.py /api/tracker/scan — the canonical scan entry point.
    Returns: { ok, bt: [...], wifi: [...], bt_count, wifi_count, timestamp }
    """
    global LAST_BT_SCAN, LAST_WIFI_SCAN
    # Reset caches so the scan functions fetch fresh data
    LAST_BT_SCAN = 0.0
    LAST_WIFI_SCAN = 0.0

    bt_devices: list = []
    wifi_networks: list = []

    async def _bt():
        try:
            devices = await scan_bluetooth_devices()
            return [d for d in devices if d.get("live", False)]
        except Exception:
            return []

    async def _wifi():
        try:
            return await scan_wifi_networks()
        except Exception:
            return []

    bt_devices, wifi_networks = await asyncio.gather(_bt(), _wifi())

    # Normalise field names so bt_radar.html renders without errors
    for d in bt_devices:
        d.setdefault("type", "BT")
        d.setdefault("rssi", -100)
    for w in wifi_networks:
        w.setdefault("rssi", -100)

    return {
        "ok": True,
        "bt": bt_devices,
        "wifi": wifi_networks,
        "bt_count": len(bt_devices),
        "wifi_count": len(wifi_networks),
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


# ─── SCREEN MIRROR WebSocket ─────────────────────────────────────
# Streams live screen captures to connected browsers via WebSocket.
# Uses `screencap -p` (Termux native) or `termux-screenshot` as fallback.
# Connect: ws://<phone-ip>:8099/ws/screen
# Sends: raw JPEG binary frames at ~5-7 FPS

_screen_viewers: set[WebSocket] = set()
_screen_streaming = False


async def _capture_screen() -> Optional[bytes]:
    """Capture a single screen frame as JPEG bytes."""
    try:
        # Try Termux native screencap first
        proc = await asyncio.create_subprocess_exec(
            "screencap",
            "-p",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=5.0)
        if proc.returncode == 0 and stdout and len(stdout) > 100:
            return stdout
    except (FileNotFoundError, asyncio.TimeoutError):
        pass

    try:
        # Fallback: termux-screenshot
        proc = await asyncio.create_subprocess_exec(
            "termux-screenshot",
            "-p",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=5.0)
        if proc.returncode == 0 and stdout and len(stdout) > 100:
            return stdout
    except (FileNotFoundError, asyncio.TimeoutError):
        pass

    try:
        # Fallback: scrcpy-style (cat from framebuffer)
        proc = await asyncio.create_subprocess_exec(
            "cat",
            "/dev/graphics/fb0",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=5.0)
        if proc.returncode == 0 and stdout:
            return stdout  # Raw framebuffer (not JPEG, but works)
    except (FileNotFoundError, asyncio.TimeoutError, PermissionError):
        pass

    return None


async def _screen_broadcast_loop():
    """Background loop that captures and broadcasts screen frames."""
    global _screen_streaming
    _screen_streaming = True
    logger.info("Screen mirror: broadcast loop started")
    while _screen_streaming and _screen_viewers:
        try:
            frame = await _capture_screen()
            if frame:
                dead = set()
                for ws in _screen_viewers:
                    try:
                        await ws.send_bytes(frame)
                    except Exception:
                        dead.add(ws)
                _screen_viewers.difference_update(dead)
            # ~5 FPS (200ms interval)
            await asyncio.sleep(0.2)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.warning(f"Screen mirror error: {e}")
            await asyncio.sleep(1.0)
    _screen_streaming = False
    logger.info("Screen mirror: broadcast loop stopped")


@app.websocket("/ws/screen")
async def screen_mirror_ws(websocket: WebSocket):
    """WebSocket endpoint for live screen mirroring.

    Client connects and receives raw JPEG frames at ~5 FPS.
    Supports multiple simultaneous viewers.
    """
    await websocket.accept()

    # Optional: verify pair token
    token = websocket.query_params.get("token", "")
    if PAIR_TOKEN and token != PAIR_TOKEN:
        await websocket.close(code=4001, reason="Unauthorized")
        return

    _screen_viewers.add(websocket)
    logger.info(f"Screen mirror: viewer connected ({len(_screen_viewers)} total)")

    # Start broadcast loop if not already running
    global _screen_streaming
    if not _screen_streaming:
        asyncio.create_task(_screen_broadcast_loop())

    try:
        # Keep connection alive, listen for control messages
        while True:
            try:
                data = await asyncio.wait_for(websocket.receive_text(), timeout=30.0)
                # Client can send: {"fps": 10} to adjust frame rate
                try:
                    msg = json.loads(data)
                    if "fps" in msg:
                        # Client requesting different FPS (future use)
                        pass
                except json.JSONDecodeError:
                    pass
            except asyncio.TimeoutError:
                # Send ping to keep alive
                try:
                    await websocket.send_json({"type": "ping"})
                except Exception:
                    break
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        _screen_viewers.discard(websocket)
        logger.info(
            f"Screen mirror: viewer disconnected ({len(_screen_viewers)} total)"
        )


@app.get("/screen/status")
async def screen_mirror_status():
    """Check if screen mirroring is available."""
    import shutil

    has_screencap = shutil.which("screencap") is not None
    has_termux_screenshot = shutil.which("termux-screenshot") is not None
    return {
        "available": has_screencap or has_termux_screenshot,
        "screencap": has_screencap,
        "termux_screenshot": has_termux_screenshot,
        "viewers": len(_screen_viewers),
        "streaming": _screen_streaming,
    }


# ─── CAMERA CAPTURE ──────────────────────────────────────────────

_CAMERA_CAPTURE_CACHE: Optional[str] = None
_CAMERA_CAPTURE_TS: float = 0.0
_CAMERA_CAPTURE_TTL: float = 2.0  # seconds


@app.get("/camera/capture")
async def camera_capture(force: bool = False):
    """Capture a photo from the phone camera, return base64 JPEG.
    Uses termux-camera-photo (Termux:API required)."""
    global _CAMERA_CAPTURE_CACHE, _CAMERA_CAPTURE_TS

    now = time.time()
    if (
        not force
        and _CAMERA_CAPTURE_CACHE
        and (now - _CAMERA_CAPTURE_TS) < _CAMERA_CAPTURE_TTL
    ):
        return {
            "image_base64": _CAMERA_CAPTURE_CACHE,
            "format": "jpeg",
            "timestamp": _CAMERA_CAPTURE_TS,
        }

    import base64 as _b64

    jpg = "/tmp/lilly_camera.jpg"
    # Try termux-camera-photo (front camera by default)
    await _run_cmd("termux-camera-photo", jpg, timeout=10.0)
    # _run_cmd returns stdout; if the file wasn't created, it failed
    if not os.path.exists(jpg):
        # Fallback: try termux-screenshot (some devices alias camera)
        await _run_cmd("termux-screenshot", jpg, timeout=8.0)
        if not os.path.exists(jpg):
            return {
                "error": "camera capture failed — ensure termux-api is installed",
                "image_base64": None,
            }

    try:
        with open(jpg, "rb") as f:
            data = f.read()
        os.remove(jpg)
        if data:
            b64 = _b64.b64encode(data).decode()
            _CAMERA_CAPTURE_CACHE = b64
            _CAMERA_CAPTURE_TS = time.time()
            return {"image_base64": b64, "format": "jpeg", "timestamp": time.time()}
    except Exception as e:
        logger.debug(f"camera capture read failed: {e}")

    return {"error": "camera capture failed", "image_base64": None}


@app.get("/camera/status")
async def camera_status():
    """Check if phone camera is available."""
    has_termux_api = shutil.which("termux-camera-photo") is not None
    return {
        "available": has_termux_api,
        "termux_api": has_termux_api,
    }


# ─── PHOTO GALLERY ACCESS ────────────────────────────────────────
# Scan DCIM/Pictures for training photos. Requires storage permission
# in Termux: `termux-setup-storage` (creates ~/storage/ symlink).

_PHOTO_DIRS = [
    os.path.expanduser("~/storage/dcim/Camera"),
    os.path.expanduser("~/storage/dcim"),
    os.path.expanduser("~/storage/pictures"),
    os.path.expanduser("/sdcard/DCIM/Camera"),
    os.path.expanduser("/sdcard/DCIM"),
    os.path.expanduser("/sdcard/Pictures"),
]
_PHOTO_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif"}
_PHOTO_CACHE: list = []
_PHOTO_CACHE_TS: float = 0.0
_PHOTO_CACHE_TTL: float = 60.0  # rescan every 60s


def _scan_photos() -> list[dict]:
    """Scan known photo directories for image files."""
    global _PHOTO_CACHE, _PHOTO_CACHE_TS
    now = time.time()
    if _PHOTO_CACHE and (now - _PHOTO_CACHE_TS) < _PHOTO_CACHE_TTL:
        return _PHOTO_CACHE

    photos = []
    seen = set()
    for d in _PHOTO_DIRS:
        if not os.path.isdir(d):
            continue
        try:
            for entry in os.scandir(d):
                if not entry.is_file():
                    continue
                ext = os.path.splitext(entry.name)[1].lower()
                if ext not in _PHOTO_EXTENSIONS:
                    continue
                abspath = os.path.abspath(entry.path)
                if abspath in seen:
                    continue
                seen.add(abspath)
                try:
                    stat = entry.stat()
                    photos.append(
                        {
                            "name": entry.name,
                            "path": abspath,
                            "size": stat.st_size,
                            "modified": stat.st_mtime,
                        }
                    )
                except OSError:
                    pass
        except OSError:
            pass
    photos.sort(key=lambda p: p["modified"], reverse=True)
    _PHOTO_CACHE = photos
    _PHOTO_CACHE_TS = now
    return photos


@app.get("/photos/list")
async def list_photos(limit: int = 50, offset: int = 0):
    """List photos from the phone gallery. Returns name, path, size, modified."""
    photos = _scan_photos()
    return {
        "photos": photos[offset : offset + limit],
        "total": len(photos),
        "offset": offset,
        "limit": limit,
    }


@app.get("/photos/file")
async def serve_photo(path: str):
    """Serve a photo file by absolute path. Returns the raw JPEG/PNG bytes.
    Only serves files from known photo directories."""
    import os.path

    real = os.path.realpath(path)
    # Security: only serve from known photo dirs
    allowed = False
    for d in _PHOTO_DIRS:
        if real.startswith(os.path.realpath(d)):
            allowed = True
            break
    if not allowed or not os.path.isfile(real):
        return JSONResponse({"error": "not found"}, status_code=404)

    from fastapi.responses import FileResponse

    ext = os.path.splitext(real)[1].lower()
    media_map = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
        ".heic": "image/heic",
        ".heif": "image/heif",
    }
    return FileResponse(real, media_type=media_map.get(ext, "application/octet-stream"))


@app.get("/photos/count")
async def photo_count():
    """Quick count of available photos."""
    return {"count": len(_scan_photos())}


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
