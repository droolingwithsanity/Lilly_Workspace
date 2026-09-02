#!/usr/bin/env python3
"""
Lilly BLE Advertiser — standalone BLE advertising server for Termux.

Runs alongside the sensor server on a different port (default 8100).
Allows the host (Lilly AI) to start/stop BLE advertising so your Pixel 10
appears in Bluetooth scans like earbuds in pairing mode.

Install on Termux:
  pip install bleak
  python3 ble_advertiser_phone.py --port 8100

Usage from host:
  curl http://<phone-ip>:8100/status
  curl -X POST http://<phone-ip>:8100/advertise -d "name=Lilly Pup&image=/static/lilly/puppy-avatar.svg"
  curl -X POST http://<phone-ip>:8100/stop
"""

import os
import sys
import json
import time
import asyncio
import logging
from typing import Optional
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
import uvicorn

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger("BleAdvertiser")

PORT = int(os.environ.get("BLE_ADVERTISER_PORT", "8100"))

# ─── Advertising State ──────────────────────────────────────────────

_BT_ADVERTISING = False
_BT_ADVERTISER = None
_BT_ADVERTISER_TASK: Optional[asyncio.Task] = None

_DEFAULT_CONFIG = {
    "name": "Lilly Pup",
    "image": "/static/lilly/puppy-avatar.svg",
    "service_uuid": "0000feed-0000-1000-8000-00805f9b34fb",
    "service_data_key": "6942",
    "web_ui_url": "http://100.93.131.114:8098/lilly/advertise",
    "tx_power": -6,
    "interval_min": 0x0020,
    "interval_max": 0x0040,
}

_BT_CONFIG = dict(_DEFAULT_CONFIG)


async def _run_advertiser_loop(
    name: str,
    service_uuid: str,
    service_data_key: str,
    web_ui_url: str,
    image: str,
) -> None:
    """Background loop that keeps the BLE advertiser alive."""
    global _BT_ADVERTISER, _BT_ADVERTISING

    while _BT_ADVERTISING:
        try:
            from bleak import BleakAdvertiser

            if _BT_ADVERTISER is None:
                _BT_ADVERTISER = BleakAdvertiser()

            url_suffix = web_ui_url.split("/")[-1] if "/" in web_ui_url else web_ui_url
            img_suffix = image.split("/")[-1] if "/" in image else image
            payload = f"{service_data_key}:{url_suffix}:{img_suffix}"

            await _BT_ADVERTISER.start(
                name=name,
                service_uuids=[service_uuid],
                service_data={service_uuid: payload.encode()},
                timeout=0,
            )
            logger.info(f"BLE advertising started as '{name}' with image {img_suffix}")

            while _BT_ADVERTISING:
                await asyncio.sleep(1.0)

        except ImportError:
            logger.error("bleak not installed. Run: pip install bleak")
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


# ─── FastAPI App ────────────────────────────────────────────────────


@asynccontextmanager
async def lifespan(app: FastAPI):
    asyncio.create_task(_advertiser_watchdog())
    yield


app = FastAPI(lifespan=lifespan)


async def _advertiser_watchdog():
    """Periodically check if the advertiser is running."""
    global _BT_ADVERTISING
    while True:
        if _BT_ADVERTISING and _BT_ADVERTISER is None and _BT_ADVERTISER_TASK is None:
            _BT_ADVERTISING = False
        await asyncio.sleep(5.0)


@app.get("/health")
async def health():
    return {"status": "ok", "service": "ble-advertiser", "port": PORT}


@app.get("/status")
async def status():
    """Check advertising status."""
    return {
        "advertising": _BT_ADVERTISING,
        "config": _BT_CONFIG,
        "timestamp": time.time(),
    }


@app.post("/advertise")
async def start_advertise(
    name: str = "",
    image: str = "",
    service_uuid: str = "",
    web_ui_url: str = "",
    tx_power: int = -6,
    interval_min: int = 0x0020,
    interval_max: int = 0x0040,
):
    """Start BLE advertising — like earbuds in pairing mode."""
    global _BT_ADVERTISING, _BT_ADVERTISER, _BT_ADVERTISER_TASK
    global _BT_CONFIG

    if _BT_ADVERTISING:
        return {
            "ok": True,
            "already_advertising": True,
            "name": _BT_CONFIG["name"],
            "image": _BT_CONFIG["image"],
            "message": "Already advertising",
        }

    if name:
        _BT_CONFIG["name"] = name
    if image:
        _BT_CONFIG["image"] = image
    if service_uuid:
        _BT_CONFIG["service_uuid"] = service_uuid
    if web_ui_url:
        _BT_CONFIG["web_ui_url"] = web_ui_url
    _BT_CONFIG["tx_power"] = tx_power
    _BT_CONFIG["interval_min"] = interval_min
    _BT_CONFIG["interval_max"] = interval_max

    cfg = _BT_CONFIG
    _BT_ADVERTISING = True
    _BT_ADVERTISER = None

    _BT_ADVERTISER_TASK = asyncio.create_task(
        _run_advertiser_loop(
            name=cfg["name"],
            service_uuid=cfg["service_uuid"],
            service_data_key=cfg["service_data_key"],
            web_ui_url=cfg["web_ui_url"],
            image=cfg["image"],
        )
    )

    return {
        "ok": True,
        "name": cfg["name"],
        "image": cfg["image"],
        "service_uuid": cfg["service_uuid"],
        "service_data_key": cfg["service_data_key"],
        "web_ui_url": cfg["web_ui_url"],
        "advertising": True,
        "pairing_mode": True,
        "message": f"Started advertising as '{cfg['name']}'",
        "timestamp": time.time(),
    }


@app.post("/stop")
async def stop_advertise():
    """Stop BLE advertising."""
    global _BT_ADVERTISING, _BT_ADVERTISER, _BT_ADVERTISER_TASK

    if not _BT_ADVERTISING and _BT_ADVERTISER_TASK is None:
        return {"ok": True, "was_advertising": False, "advertising": False}

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
        "timestamp": time.time(),
    }


@app.post("/configure")
async def configure_advertising(
    name: str = "",
    image: str = "",
    service_uuid: str = "",
    web_ui_url: str = "",
    tx_power: int = -6,
):
    """Update the advertising configuration without starting/stopping."""
    global _BT_CONFIG

    updates = {
        "name": name,
        "image": image,
        "service_uuid": service_uuid,
        "web_ui_url": web_ui_url,
        "tx_power": tx_power,
    }
    for k, v in updates.items():
        if v:
            _BT_CONFIG[k] = v

    return {"ok": True, "config": _BT_CONFIG, "message": "Configuration updated"}


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Lilly BLE Advertiser")
    parser.add_argument("--port", type=int, default=PORT)
    parser.add_argument("--host", type=str, default="0.0.0.0")
    args = parser.parse_args()
    PORT = args.port

    logger.info(f"Starting BLE Advertiser on {args.host}:{PORT}")
    uvicorn.run(app, host=args.host, port=PORT, log_level="info")
