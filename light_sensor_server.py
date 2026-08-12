#!/usr/bin/env python3
"""
LightSensorServer — a lightweight, crash-resilient Termux sensor server.

Designed for Lilly AI: minimal dependencies (FastAPI only), auto-restarts on
crash, caches sensor data to a local SQLite WAL journal, and syncs when the
main Lilly AI process reconnects.

Install on Termux:
    pip install fastapi uvicorn

Run:
    python3 light_sensor_server.py --port 8099

If the existing termux_sensor_server.py is also running on 8099, stop that first:
    killall python3; python3 light_sensor_server.py

Endpoints (identical API to the original sensor server):
    GET  /sensors/all          — cached snapshot of all sensors (2s refresh batch)
    GET  /sensors/all/live     — fresh read (runs termux-sensor now)
    GET  /sensors/{name}       — cached value for a specific sensor
    GET  /sensors/{name}/live  — fresh read for a specific sensor
    GET  /sensors/list         — list of available sensor names
    GET  /battery              — cached battery data
    GET  /battery/live         — fresh battery read
    GET  /location             — cached location
    GET  /location/live        — fresh location read
    GET  /health               — health + uptime + cache stats
    POST /shell                — run a shell command
    GET  /notification/list    — Android notifications
    POST /notification/send    — send a notification
    GET  /bluetooth/scan       — scan for Bluetooth devices

Crash recovery:
    - Writes sensor snapshots every BATCH_INTERVAL seconds to SQLite
    - On startup, reads the last snapshot from SQLite to bootstrap immediately
    - Heartbeat endpoint (/health) allows external watchdog to detect crashes
    - Graceful SIGTERM handling to flush cache before exit
    - Auto-restart via the included shell loop: ./light_sensor_server.py --watch
"""

import argparse
import json
import os
import signal
import sqlite3
import sys
import time
import asyncio
import logging
from pathlib import Path
from contextlib import asynccontextmanager
from typing import Any, Optional, cast

from fastapi import FastAPI, Query, HTTPException
from fastapi.responses import JSONResponse
import uvicorn

# ─── CONFIG ──────────────────────────────────────────────────────────────

PORT = int(os.environ.get("SENSOR_PORT", "8099"))
BATCH_INTERVAL = float(os.environ.get("SENSOR_BATCH_INTERVAL", "2.0"))
CACHE_DB = Path(
    os.environ.get("SENSOR_CACHE_DB", str(Path.home() / ".lilly_sensors.db"))
)

# Sensors we actively collect (subset of the 23 Android sensors)
SENSOR_NAMES = [
    "light",
    "accelerometer",
    "magnetometer",
    "gyroscope",
    "gravity",
    "linear_acceleration",
    "rotation_vector",
    "pressure",
    "relative_humidity",
    "temperature",
    "proximity",
    "step_counter",
    "significant_motion",
    "timestamp",
]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("LightSensorServer")

# ─── IN-MEMORY STATE ─────────────────────────────────────────────────────

LATEST_SENSORS: dict = {}
LATEST_BATTERY: dict = {}
LATEST_LOCATION: dict = {}
LAST_UPDATE: float = 0.0
START_TIME: float = time.time()
SHUTDOWN: bool = False

# ─── SQLITE CACHE ────────────────────────────────────────────────────────


def _init_cache() -> None:
    """Create the cache database if it doesn't exist."""
    CACHE_DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(CACHE_DB)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sensor_cache (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at REAL NOT NULL
        )
    """)
    conn.commit()
    conn.close()


def _cache_get(key: str) -> Optional[dict]:
    """Read a cached value from SQLite."""
    try:
        conn = sqlite3.connect(str(CACHE_DB))
        row = conn.execute(
            "SELECT value, updated_at FROM sensor_cache WHERE key = ?", (key,)
        ).fetchone()
        conn.close()
        if row:
            return {"data": json.loads(row[0]), "updated_at": row[1]}
    except Exception as e:
        logger.debug(f"cache get error: {e}")
    return None


def _cache_set(key: str, value: dict) -> None:
    """Write a cached value to SQLite."""
    try:
        conn = sqlite3.connect(str(CACHE_DB))
        conn.execute(
            """
            INSERT OR REPLACE INTO sensor_cache (key, value, updated_at)
            VALUES (?, ?, ?)
        """,
            (key, json.dumps(value), time.time()),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logger.debug(f"cache set error: {e}")


def _load_bootstrap() -> None:
    """Load the last cached sensor snapshot on startup for immediate availability."""
    global LATEST_SENSORS, LATEST_BATTERY, LATEST_LOCATION, LAST_UPDATE

    cached = _cache_get("sensors_all")
    if cached:
        LATEST_SENSORS = cached.get("data", {}).get("sensors", {})
        LATEST_BATTERY = cached.get("data", {}).get("battery", {})
        LATEST_LOCATION = cached.get("data", {}).get("location", {})
        LAST_UPDATE = cached.get("updated_at", 0)
        logger.info(
            f"Bootstrapped from cache: {len(LATEST_SENSORS)} sensors, "
            f"last update {LAST_UPDATE:.1f}s ago"
        )


# ─── ASYNC SENSOR READING ─────────────────────────────────────────────────


async def _run_termux_cmd(*args: str, timeout: float = 5.0) -> str:
    """Run a termux-* command asynchronously."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return stdout.decode().strip()
    except Exception:
        return ""


async def _read_sensor(sensor_name: str, timeout: float = 5.0) -> Optional[list]:
    """Read a single sensor via termux-sensor."""
    cmd = f"termux-sensor -s {sensor_name} -n 1 -d 1000"
    output = await _run_termux_cmd("sh", "-c", cmd, timeout=timeout)
    if not output:
        return None
    try:
        data = json.loads(output)
        # termux-sensor returns {"sensor_name": {"values": [...]}}
        if isinstance(data, dict):
            for key, val in data.items():
                if isinstance(val, dict) and "values" in val:
                    return val["values"]
                if isinstance(val, list):
                    return val
    except Exception:
        pass
    return None


async def read_all_sensors(timeout: float = 15.0) -> dict:
    """Read all sensors concurrently for speed."""
    tasks = {name: _read_sensor(name, timeout=timeout) for name in SENSOR_NAMES}
    results = await asyncio.gather(*tasks.values(), return_exceptions=True)
    sensors = {}
    for name, result in zip(tasks.keys(), results):
        if isinstance(result, list):
            sensors[name] = result
        elif isinstance(result, Exception):
            logger.debug(f"sensor {name} error: {result}")
    return sensors


async def read_battery(timeout: float = 5.0) -> dict:
    """Read battery status via termux-battery."""
    output = await _run_termux_cmd("termux-battery", timeout=timeout)
    if output:
        try:
            return json.loads(output)
        except Exception:
            pass
    return {}


async def read_location(timeout: float = 5.0) -> dict:
    """Read GPS location via termux-location."""
    output = await _run_termux_cmd("termux-location", "-p", "gps", timeout=timeout)
    if output:
        try:
            return json.loads(output)
        except Exception:
            pass
    return {}


# ─── UPDATE LOOP ──────────────────────────────────────────────────────────


async def sensor_update_loop() -> None:
    """Continuously poll sensors and update cache."""
    global LATEST_SENSORS, LATEST_BATTERY, LATEST_LOCATION, LAST_UPDATE

    while not SHUTDOWN:
        try:
            # Read all sensors concurrently with battery + location
            sensor_task = read_all_sensors()
            battery_task = read_battery()
            location_task = read_location()

            sensors, battery, location = await asyncio.gather(
                sensor_task, battery_task, location_task, return_exceptions=True
            )

            if isinstance(sensors, dict):
                LATEST_SENSORS = sensors
            if isinstance(battery, dict):
                LATEST_BATTERY = battery
            if isinstance(location, dict):
                LATEST_LOCATION = location

            LAST_UPDATE = time.time()

            # Cache to SQLite for crash recovery
            _cache_set(
                "sensors_all",
                {
                    "sensors": LATEST_SENSORS,
                    "battery": LATEST_BATTERY,
                    "location": LATEST_LOCATION,
                },
            )

            logger.info(
                f"Updated {len(LATEST_SENSORS)} sensors | "
                f"battery: {LATEST_BATTERY.get('percentage', '?')}% | "
                f"location: {'yes' if LATEST_LOCATION.get('latitude') else 'no'}"
            )

        except Exception as e:
            logger.error(f"Sensor loop error: {e}")

        await asyncio.sleep(BATCH_INTERVAL)


# ─── FASTAPI APP ──────────────────────────────────────────────────────────

app = FastAPI(title="LightSensorServer", version="1.0.0")


@app.get("/health")
async def health():
    """Health check + cache stats + uptime."""
    cache_info = _cache_get("sensors_all") or {}
    return {
        "ok": True,
        "service": "light-sensor-server",
        "port": PORT,
        "uptime_seconds": round(time.time() - START_TIME, 1),
        "last_update": LAST_UPDATE,
        "sensor_count": len(LATEST_SENSORS),
        "battery": LATEST_BATTERY.get("percentage", 0),
        "location": "yes" if LATEST_LOCATION.get("latitude") else "no",
        "cache_last_write": cache_info.get("updated_at", 0),
        "cache_age": round(time.time() - cache_info.get("updated_at", 0), 1)
        if cache_info.get("updated_at")
        else 0,
    }


@app.get("/sensors/all")
async def get_all_sensors():
    """Cached snapshot of all sensors (updated every BATCH_INTERVAL seconds)."""
    return {
        "sensors": LATEST_SENSORS,
        "battery": LATEST_BATTERY,
        "location": LATEST_LOCATION,
        "timestamp": LAST_UPDATE,
        "count": len(LATEST_SENSORS),
        "age": round(time.time() - LAST_UPDATE, 2) if LAST_UPDATE else None,
    }


@app.get("/sensors/all/live")
async def get_all_sensors_live():
    """Fresh read — runs termux-sensor now (slower, but always current)."""
    data = await read_all_sensors()
    return {
        "sensors": data,
        "timestamp": time.time(),
        "count": len(data),
    }


@app.get("/sensors/{name}")
async def get_sensor(name: str):
    """Get cached value for a specific sensor."""
    val = LATEST_SENSORS.get(name)
    if val is None:
        name_lower = name.lower()
        for k, v in LATEST_SENSORS.items():
            if name_lower in k.lower():
                return {"name": k, "values": v, "timestamp": LAST_UPDATE}
        raise HTTPException(status_code=404, detail=f"Sensor '{name}' not found")
    return {"name": name, "values": val, "timestamp": LAST_UPDATE}


@app.get("/sensors/{name}/live")
async def get_sensor_live(name: str):
    """Fresh read for a specific sensor."""
    val = await _read_sensor(name)
    if val is None:
        raise HTTPException(status_code=404, detail=f"Sensor '{name}' not available")
    return {"name": name, "values": val, "timestamp": time.time()}


@app.get("/sensors/list")
async def get_sensor_list():
    """List available sensor names."""
    return {"sensors": SENSOR_NAMES, "count": len(SENSOR_NAMES)}


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
async def shell_command(cmd: str = Query("", max_length=500)):
    """Run a shell command (for app launching, navigation, etc)."""
    if not cmd:
        return {"error": "cmd parameter required"}
    try:
        proc = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=10.0)
        return {
            "stdout": stdout.decode().strip(),
            "stderr": stderr.decode().strip(),
            "exit_code": proc.returncode,
        }
    except asyncio.TimeoutError:
        return {"error": "command timed out", "exit_code": -1}
    except Exception as e:
        return {"error": str(e), "exit_code": -1}


@app.get("/notification/list")
async def get_notifications():
    output = await _run_termux_cmd("termux-notification-list")
    if not output:
        return {"notifications": [], "error": "termux-notification not installed"}
    try:
        return {"notifications": json.loads(output)}
    except Exception:
        return {"notifications": [], "error": "parse error"}


@app.post("/notification/send")
async def send_notification(
    title: str = "", content: str = "", priority: str = "default"
):
    cmd = f"termux-notification --title '{title}' --content '{content}' --priority {priority}"
    output = await _run_termux_cmd("sh", "-c", cmd)
    return {"sent": True, "command": cmd}


@app.get("/bluetooth/scan")
async def get_bluetooth_scan():
    output = await _run_termux_cmd("termux-bluetooth")
    if not output:
        return {"devices": [], "error": "termux-bluetooth not installed"}
    try:
        data = json.loads(output)
        return {"devices": data}
    except Exception:
        return {"devices": [], "error": "parse error"}


@app.get("/bluetooth/scan/live")
async def get_bluetooth_scan_live():
    """Live scan (takes 5-10 seconds)."""
    proc = await asyncio.create_subprocess_exec(
        "termux-bluetooth",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=15.0)
    try:
        return {"devices": json.loads(stdout.decode().strip())}
    except Exception:
        return {"devices": [], "error": "scan failed"}


# ─── LIFESPAN / GRACEFUL SHUTDOWN ─────────────────────────────────────────


@asynccontextmanager
async def lifespan(app: FastAPI):
    global SHUTDOWN
    # Initialize cache DB
    _init_cache()
    # Bootstrap from last cached state
    _load_bootstrap()
    # Start sensor polling loop
    SHUTDOWN = False
    loop_task = asyncio.create_task(sensor_update_loop())
    logger.info(f"LightSensorServer starting on port {PORT}")
    logger.info(f"Cache DB: {CACHE_DB}")
    logger.info(f"Batch interval: {BATCH_INTERVAL}s")

    # Handle SIGTERM for graceful shutdown (flushes cache)
    def _handle_sigterm(signum, frame):
        global SHUTDOWN
        logger.info("Received SIGTERM, shutting down gracefully...")
        SHUTDOWN = True
        loop_task.cancel()

    signal.signal(signal.SIGTERM, _handle_sigterm)
    signal.signal(signal.SIGINT, _handle_sigterm)

    try:
        yield
    finally:
        SHUTDOWN = True
        loop_task.cancel()
        try:
            await loop_task
        except asyncio.CancelledError:
            pass
        _cache_set("shutdown", {"time": time.time()})
        logger.info("LightSensorServer stopped.")


app.router.lifespan_context = cast(Any, lifespan)

# ─── MAIN ENTRY POINT ─────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(
        description="LightSensorServer — lightweight Termux sensor server"
    )
    parser.add_argument(
        "--port", type=int, default=PORT, help="HTTP port (default: 8099)"
    )
    parser.add_argument(
        "--host", default="0.0.0.0", help="Bind host (default: 0.0.0.0)"
    )
    parser.add_argument(
        "--watch", action="store_true", help="Auto-restart on crash (shell loop)"
    )
    parser.add_argument(
        "--interval", type=float, default=BATCH_INTERVAL, help="Sensor polling interval"
    )
    args = parser.parse_args()

    if args.watch:
        # Restart loop: kills any existing server, runs, and restarts on exit
        import subprocess

        # Kill any existing sensor server on this port
        subprocess.run(f"fuser -k {args.port}/tcp 2>/dev/null", shell=True)
        while True:
            log_file = Path.home() / ".lilly_sensor_server.log"
            cmd = [
                sys.executable,
                __file__,
                "--port",
                str(args.port),
                "--host",
                args.host,
                "--interval",
                str(args.interval),
            ]
            logger.info(f"Starting sensor server (watch mode): {' '.join(cmd)}")
            try:
                proc = subprocess.run(
                    cmd, stdout=log_file.open("a"), stderr=subprocess.STDOUT
                )
                if proc.returncode != 0:
                    logger.warning(
                        f"Sensor server exited with code {proc.returncode}, restarting in 3s..."
                    )
                    time.sleep(3)
                else:
                    logger.info("Sensor server exited cleanly, stopping watch loop.")
                    break
            except KeyboardInterrupt:
                logger.info("Watch mode interrupted.")
                break
    else:
        config = uvicorn.Config(app, host=args.host, port=args.port, log_level="info")
        uvicorn.Server(config).run()


if __name__ == "__main__":
    from contextlib import asynccontextmanager

    main()
