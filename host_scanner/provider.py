#!/usr/bin/env python3
"""
Host Scan Provider — real BT + WiFi scanning for Lilly AI.

Runs ON THE HOST (outside Docker) and exposes a small HTTP API that
lilly_ai.py (inside the container, using host networking) can call to get
*real* Bluetooth and WiFi scan results from the host's physical radios.

  WiFi: `nmcli dev wifi list`        (host has a working wifi interface)
  BT  : bluehood's BluetoothScanner  (BLE via bleak + classic via hcitool)
        with a bluetoothctl fallback when bluehood is unavailable.

Usage:
  python3 host_scan_provider.py --port 8096

Endpoints:
  GET  /health         → {"status":"ok","wifi":true,"bt":true,...}
  GET  /api/scan       → fresh scan: {ok, bt, wifi, bt_count, wifi_count, timestamp}
  POST /api/scan       → same as GET /api/scan
  GET  /api/wifi       → wifi only
  GET  /api/bt         → bt only

  FLET-NODE COMPAT ENDPOINTS (the lilly_ai.py node_fleet_poller polls these
  on each registered node's sensor_url, so the host can appear as a fleet
  node on radar_hub.html / tracker pages):
  GET  /wifi/scan          → {"networks":[...]}           (fleet poller /wifi/scan)
  GET  /bluetooth/scan     → {"devices":[...]}            (fleet poller /bluetooth/scan)
  GET  /location           → {"location":{lat,lng,...}}   (fleet poller /location)
  GET  /battery            → {"battery":{"percentage":n}} (fleet poller /battery)
  GET  /sensors/all        → {"sensors":{...}}            (fleet poller /sensors/all)
  GET  /notification/list  → {"notifications":[]}         (fleet poller /notification/list)
"""

import argparse
import asyncio
import json
import logging
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# Self-contained bluehood scanner lives next to this file.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from bluehood.scanner import BluetoothScanner  # noqa: E402

logger = logging.getLogger("host_scan_provider")
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)

app = FastAPI(title="Lilly Host Scan Provider", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

SCAN_DURATION = float(os.environ.get("HOST_BT_SCAN_SECONDS", "5"))
WIFI_SCAN_TIMEOUT = float(os.environ.get("HOST_WIFI_SCAN_SECONDS", "15"))

# WiFi SSIDs to keep off the radar/scan — the user's own home networks.
# Comma-separated, env-overridable. Matching is case-insensitive on SSID.
_WIFI_IGNORE_RAW = os.environ.get(
    "HOST_WIFI_IGNORE_SSIDS", "Mr Bigs House,You May Pass"
)
WIFI_IGNORE_SSIDS = {
    s.strip().lower() for s in _WIFI_IGNORE_RAW.split(",") if s.strip()
}

# GPS for the host node. Two ways to supply it:
#   1. HOST_GPS_LAT / HOST_GPS_LNG  (explicit)
#   2. SENSOR_SERVER_URL  (forward a bound phone's /location)
# Otherwise we fall back to HOST_GPS_DEFAULT (a set table / saved home) so the
# radar hub can still place the host on the map. Set HOST_GPS_LAT/LNG to
# override, or SENSOR_SERVER_URL to follow the phone.
HOST_GPS_LAT = os.environ.get("HOST_GPS_LAT")
HOST_GPS_LNG = os.environ.get("HOST_GPS_LNG")
_HOME_DEFAULT = os.environ.get(
    "HOST_GPS_DEFAULT", "44.29528,-79.54514"
)  # saved home: Barrie/Innisfil area (matches phone GPS + radar center)
_HOME_DEFAULT_LAT, _HOME_DEFAULT_LNG = _HOME_DEFAULT.split(",")
FORWARD_LOCATION_URL = os.environ.get("SENSOR_SERVER_URL", "").rstrip("/")
if FORWARD_LOCATION_URL and "://" not in FORWARD_LOCATION_URL:
    FORWARD_LOCATION_URL = "http://" + FORWARD_LOCATION_URL

_bt_scanner: BluetoothScanner | None = None
_bt_last = {"ts": 0.0, "data": []}
_wifi_last = {"ts": 0.0, "data": []}
# Serializes BT scans: bleak can only run one BLE discovery at a time on an
# adapter, so overlapping callers (fleet poller, forceRescan, /api/scan) would
# otherwise collide with org.bluez.Error.InProgress and wedge the adapter.
_bt_scan_lock = asyncio.Lock()
_CACHE_TTL = float(os.environ.get("HOST_SCAN_CACHE_SECONDS", "5"))
# Fleet endpoints refresh more slowly: long enough to always serve instant
# cached data between the lilly fleet poller's slow-cadence polls (~60s),
# while short enough to keep the radar fresh.
_FLEET_BT_TTL = float(os.environ.get("HOST_FLEET_BT_SECONDS", "45"))
_FLEET_WIFI_TTL = float(os.environ.get("HOST_FLEET_WIFI_SECONDS", "20"))


def _list_hci_adapters() -> list[dict]:
    """Return a descriptor for every host BT adapter.

    Each item: {"name": "hci0", "vendor": "8087", "device": "0029",
                "mac": "64:BC:...", "bus": "USB"}.

    vendor/device are read from the USB modalias (the reliable way to tell a
    USB dongle apart from the onboard radio even when both enumerate over USB,
    which `hciconfig -a`'s "Bus: USB" line cannot distinguish).
    """
    adapters: list[dict] = []
    try:
        for p in sorted(Path("/sys/class/bluetooth").glob("hci*")):
            info = {"name": p.name, "vendor": "", "device": "", "mac": "", "bus": ""}
            try:
                info["mac"] = p.joinpath("address").read_text().strip()
            except Exception:
                pass
            modalias = ""
            try:
                modalias = p.joinpath("device", "modalias").read_text().strip()
            except Exception:
                pass
            vm = re.search(r"v([0-9a-fA-F]{4})", modalias)
            pm = re.search(r"p([0-9a-fA-F]{4})", modalias)
            if vm:
                info["vendor"] = vm.group(1).lower()
            if pm:
                info["device"] = pm.group(1).lower()
            dev_path = ""
            try:
                dev_path = str((p / "device").resolve())
            except Exception:
                pass
            info["bus"] = (
                "PCIe"
                if (
                    ("pci" in dev_path or "PCIe" in dev_path) and "/usb" not in dev_path
                )
                else ("USB" if "usb" in dev_path else "")
            )
            adapters.append(info)
    except Exception as e:  # pragma: no cover
        logger.debug("sysfs adapter detection failed: %s", e)
    # Fallback to hciconfig if sysfs gave nothing.
    if not adapters:
        try:
            out = subprocess.run(
                ["hciconfig", "-a"], capture_output=True, text=True, timeout=8
            ).stdout
            cur = None
            for line in out.splitlines():
                m = re.match(r"^\s*(hci\d+):", line)
                if m:
                    cur = m.group(1)
                    adapters.append(
                        {
                            "name": cur,
                            "vendor": "",
                            "device": "",
                            "mac": "",
                            "bus": "USB",
                        }
                    )
                    continue
                if cur:
                    am = re.search(r"BD Address:\s*([0-9A-Fa-f:]+)", line)
                    if am:
                        adapters[-1]["mac"] = am.group(1).upper()
        except Exception as e:  # pragma: no cover
            logger.debug("hciconfig adapter detection failed: %s", e)
    return adapters


# USB vendor IDs whose controllers are almost always cheap classic (BR/EDR)
# USB dongles with weak/no BLE — used to route classic inquiry to the dongle
# so BLE and classic run concurrently on separate hardware.
_DONGLE_VENDORS = {"1131", "0a12", "0bda", "0a5c", "0eef", "0d6a", "cit", "0a5c"}
_ONBOARD_VENDORS = {"8087"}  # Intel (AX200 / AX210) onboard radios


def _pick_adapters() -> tuple[str | None, str | None]:
    """Choose which host BT adapter to use for BLE vs classic inquiry.

    Returns (ble_adapter, classic_adapter).

    Explicit env vars always win:
      BLUEHOOD_ADAPTER / BLUEHOOD_ADAPTER_BLE      → BLE adapter
      BLUEHOOD_CLASSIC_ADAPTER / BLUEHOOD_ADAPTER_CLASSIC → classic adapter
    Or the generic single-adapter name (e.g. "hci0").

    Otherwise auto-detect: on a machine with an onboard radio + a USB dongle,
    run BLE on the onboard radio and classic inquiry *concurrently* on the
    dongle, so both hardware radios contribute scan data.
    """
    ble = (
        os.environ.get("BLUEHOOD_ADAPTER")
        or os.environ.get("BLUEHOOD_ADAPTER_BLE")
        or os.environ.get("HOST_BT_BLE_ADAPTER")
    )
    classic = (
        os.environ.get("BLUEHOOD_CLASSIC_ADAPTER")
        or os.environ.get("BLUEHOOD_ADAPTER_CLASSIC")
        or os.environ.get("HOST_BT_CLASSIC_ADAPTER")
    )
    if ble and classic:
        return ble, classic
    if ble or classic:
        preferred = ble or classic
        return (ble or preferred), (classic or preferred)

    adapters = _list_hci_adapters()
    if len(adapters) < 2:
        single = adapters[0]["name"] if adapters else None
        return single, single

    # Prefer BLE on an Intel onboard radio, classic on a USB dongle.
    onboard = [a for a in adapters if a["vendor"] in _ONBOARD_VENDORS]
    dongles = [a for a in adapters if a["vendor"] in _DONGLE_VENDORS]

    ble_ad = (onboard + dongles)[0] if (onboard + dongles) else adapters[0]
    # classic goes on a distinct second adapter whenever one exists.
    classic_candidates = [a for a in dongles if a["name"] != ble_ad["name"]]
    if not classic_candidates and onboard and onboard[0]["name"] != ble_ad["name"]:
        classic_candidates = [onboard[0]]
    if not classic_candidates:
        classic_candidates = [a for a in adapters if a["name"] != ble_ad["name"]]
    classic_ad = classic_candidates[0] if classic_candidates else ble_ad
    return ble_ad["name"], classic_ad["name"]


def _get_scanner() -> BluetoothScanner | None:
    """Return a shared BluetoothScanner instance (lazy).

    Uses dual-adapter mode when two distinct host BT radios are available so
    BLE and classic inquiry run concurrently and both contribute devices.
    """
    global _bt_scanner
    if _bt_scanner is None:
        ble, classic = _pick_adapters()
        try:
            _bt_scanner = BluetoothScanner(adapter=ble, classic_adapter=classic)
            if ble and classic and ble != classic:
                logger.info(
                    "bluehood dual-adapter mode: BLE on %s, classic on %s",
                    ble,
                    classic,
                )
        except Exception as e:  # pragma: no cover
            logger.warning("Could not create BluetoothScanner: %s", e)
            _bt_scanner = None
    return _bt_scanner


def _ensure_bt_adapter() -> bool:
    """Bring the BT adapter up if possible (rfkill unblock + bluetoothctl power on)."""
    try:
        subprocess.run(
            ["rfkill", "unblock", "bluetooth"], capture_output=True, timeout=5
        )
        subprocess.run(
            ["bluetoothctl", "power", "on"], capture_output=True, text=True, timeout=8
        )
        out = subprocess.run(
            ["bluetoothctl", "show"], capture_output=True, text=True, timeout=8
        )
        for line in out.stdout.splitlines():
            if line.strip().lower().startswith("powered:"):
                return "yes" in line.strip().lower()
    except Exception as e:
        logger.debug("bt power check failed: %s", e)
    return False


# ─── BT scanning ──────────────────────────────────────────────────────


def _scan_bt_fallback(duration: int = 8) -> list[dict]:
    """Fallback BT scan using hcitool inquiry + bluetoothctl when bluehood can't run."""
    from bluehood.classifier import classify_device

    devices: list[dict] = []
    try:
        # Classic inquiry via hcitool (1.28s units)
        out = subprocess.run(
            ["hcitool", "inq", "--length", str(duration)],
            capture_output=True,
            text=True,
            timeout=duration * 1.5 + 5,
        )
        macs = re.findall(r"([0-9A-Fa-f:]{17})", out.stdout)
        for mac in macs:
            name = None
            try:
                n = subprocess.run(
                    ["hcitool", "name", mac.upper()],
                    capture_output=True,
                    text=True,
                    timeout=3,
                )
                name = n.stdout.strip() or None
            except Exception:
                pass
            device_type = classify_device("", name, [], None)
            devices.append(
                {
                    "name": name or "",
                    "address": mac.upper(),
                    "rssi": -60,
                    "type": "classic",
                    "bt_type": "classic",
                    "device_type": device_type,
                    "live": True,
                }
            )
    except Exception as e:
        logger.debug("hcitool fallback scan failed: %s", e)
    return devices


async def _scan_bt() -> list[dict]:
    """Scan the host BT radios. Returns a list of device dicts.

    Serialized via _bt_scan_lock so concurrent callers queue instead of
    colliding on the shared BLE adapter (bleak throws InProgress).
    """
    async with _bt_scan_lock:
        return await _scan_bt_unlocked()


async def _scan_bt_unlocked() -> list[dict]:
    """Actual BT scan — must be called while holding _bt_scan_lock."""
    scanner = _get_scanner()
    devices = []
    if scanner is not None:
        try:
            found = await scanner.scan(duration=SCAN_DURATION)
            from bluehood.classifier import classify_device

            for d in found:
                # Classify the device
                device_type = d.device_type or classify_device(
                    d.vendor, d.name, d.service_uuids, d.device_class
                )
                devices.append(
                    {
                        "name": d.name or "",
                        "address": (d.mac or "").upper(),
                        "rssi": d.rssi if isinstance(d.rssi, int) else int(d.rssi or 0),
                        "type": d.bt_type or "ble",
                        "bt_type": d.bt_type or "ble",
                        "vendor": d.vendor or "",
                        "service_uuids": d.service_uuids or [],
                        "device_type": device_type,
                        "live": True,
                    }
                )
        except Exception as e:
            logger.warning("bluehood BT scan failed, falling back: %s", e)
            devices = []
    if not devices:
        # Fallbacks: hcitool classic + bluetoothctl BLE
        loop = asyncio.get_event_loop()
        try:
            classic = await loop.run_in_executor(None, _scan_bt_fallback, 8)
            devices.extend(classic)
        except Exception as e:
            logger.debug("hcitool fallback error: %s", e)
        try:
            ble_ctl = await loop.run_in_executor(None, _scan_bt_ctl, 6)
            devices.extend(ble_ctl)
        except Exception as e:
            logger.debug("bluetoothctl fallback error: %s", e)
    # Dedupe by address
    seen = set()
    out = []
    for d in devices:
        addr = d.get("address") or d.get("mac") or ""
        if addr in seen:
            continue
        seen.add(addr)
        d.setdefault("type", "BT")
        d.setdefault("rssi", -100)
        out.append(d)
    return out


def _scan_bt_ctl(duration: int = 6) -> list[dict]:
    """BLE scan via bluetoothctl --timeout."""
    from bluehood.classifier import classify_device

    try:
        out = subprocess.run(
            ["bluetoothctl", "--timeout", str(duration), "scan", "on"],
            capture_output=True,
            text=True,
            timeout=duration + 4,
        )
        devices = {}
        for line in out.stdout.splitlines():
            parts = line.split()
            if len(parts) >= 2 and parts[0] == "Device":
                addr = parts[1]
                # second token is the name; may be empty
                name = parts[2] if len(parts) > 2 else ""
                devices.setdefault(addr, {"name": name, "address": addr})
            elif "NEW. Device" in line:
                pass
        return [
            {
                "name": v.get("name") or "",
                "address": k.upper(),
                "rssi": -70,
                "type": "ble",
                "bt_type": "ble",
                "device_type": classify_device("", v.get("name") or "", [], None),
                "live": True,
            }
            for k, v in devices.items()
        ]
    except Exception as e:
        logger.debug("bluetoothctl scan failed: %s", e)
        return []


# ─── WiFi scanning ────────────────────────────────────────────────────


def _parse_nmcli_list(rows) -> list[dict]:
    """Parse `nmcli -t` device list output into clean dicts."""
    networks = []
    for row in rows:
        if not row.strip():
            continue
        # nmcli -t escapes colons in fields as \: — split on unescaped colons
        fields = re.split(r"(?<!\\):", row)
        # Undo escaping
        fields = [f.replace("\\:", ":") for f in fields]
        if len(fields) < 2:
            continue
        ssid = fields[0]
        bssid = fields[1] if len(fields) > 1 else ""
        rssi = -100
        try:
            rssi = int(fields[2]) if len(fields) > 2 else -100
        except ValueError:
            pass
        security = fields[3] if len(fields) > 3 else ""
        freq = fields[4] if len(fields) > 4 else ""
        chan = fields[5] if len(fields) > 5 else ""
        freq_mhz = None
        try:
            m = re.search(r"(\d+)", freq)
            if m:
                freq_mhz = int(m.group(1))
        except Exception:
            pass
        # Estimate distance (log-distance path loss, indoor-ish reference)
        distance = None
        if rssi and rssi > -100 and rssi != 0:
            ref = -40.0
            n = 3.0
            distance = round(10 ** ((ref - rssi) / (10 * n)), 2)
        networks.append(
            {
                "ssid": ssid,
                "bssid": bssid,
                "rssi": rssi,
                "security": security,
                "frequency": freq,
                "channel": chan,
                "distance": distance,
                "band": "2.4GHz"
                if (freq_mhz and freq_mhz < 3000)
                else ("5GHz" if freq_mhz else "unknown"),
            }
        )
    return networks


def _scan_wifi() -> list[dict]:
    """Scan WiFi networks using nmcli (host interface)."""
    try:
        out = subprocess.run(
            [
                "nmcli",
                "-t",
                "-f",
                "SSID,BSSID,SIGNAL,SECURITY,FREQ,CHAN",
                "dev",
                "wifi",
                "list",
            ],
            capture_output=True,
            text=True,
            timeout=WIFI_SCAN_TIMEOUT,
        )
        if out.returncode != 0:
            logger.warning("nmcli wifi scan failed: %s", out.stderr.strip()[:200])
            return []
        nets = _parse_nmcli_list(out.stdout.splitlines())
        # Drop the user's own home networks from the scan so they don't clutter
        # the radar. Match case-insensitively on SSID.
        if WIFI_IGNORE_SSIDS:
            nets = [
                n
                for n in nets
                if (n.get("ssid") or "").strip().lower() not in WIFI_IGNORE_SSIDS
            ]
        return nets
    except Exception as e:
        logger.warning("nmcli wifi scan error: %s", e)
        return []


# ─── HTTP API ─────────────────────────────────────────────────────────


async def _scan_once(force: bool = False) -> dict:
    """Run a concurrent BT + WiFi scan (with a short cache)."""
    now = time.time()
    bt_data, wifi_data = _bt_last, _wifi_last
    use_cache = not force and (
        now - bt_data["ts"] < _CACHE_TTL and now - wifi_data["ts"] < _CACHE_TTL
    )

    if use_cache:
        return {
            "ok": True,
            "bt": bt_data["data"],
            "wifi": wifi_data["data"],
            "bt_count": len(bt_data["data"]),
            "wifi_count": len(wifi_data["data"]),
            "cached": True,
            "timestamp": now,
        }

    # Keep adapter powered (for a dongle that came online since last scan)
    _ensure_bt_adapter()

    bt_list, wifi_list = await asyncio.gather(_scan_bt(), asyncio.to_thread(_scan_wifi))

    _bt_last.update({"ts": time.time(), "data": bt_list})
    _wifi_last.update({"ts": time.time(), "data": wifi_list})

    return {
        "ok": True,
        "bt": bt_list,
        "wifi": wifi_list,
        "bt_count": len(bt_list),
        "wifi_count": len(wifi_list),
        "cached": False,
        "timestamp": time.time(),
    }


@app.get("/health")
async def health():
    bt_power = _ensure_bt_adapter()
    scanner = _get_scanner()
    ble_ad, classic_ad = _pick_adapters()
    return {
        "status": "ok",
        "service": "host-scan-provider",
        "bt_adapter_powered": bt_power,
        "wifi": bool(_scan_wifi() or True),
        "scanner": "bluehood" if scanner is not None else "fallback",
        "dual_adapter": bool(ble_ad and classic_ad and ble_ad != classic_ad),
        "ble_adapter": ble_ad,
        "classic_adapter": classic_ad,
        "adapters": _list_hci_adapters(),
    }


@app.get("/api/wifi")
async def api_wifi():
    nets = await asyncio.to_thread(_scan_wifi)
    return {"ok": True, "networks": nets, "count": len(nets), "timestamp": time.time()}


@app.get("/api/bt")
async def api_bt():
    devs = await _scan_bt()
    return {"ok": True, "devices": devs, "count": len(devs), "timestamp": time.time()}


@app.get("/api/scan")
@app.post("/api/scan")
async def api_scan(force: bool = False):
    try:
        return await _scan_once(force=force)
    except Exception as e:
        logger.exception("scan failed")
        return {
            "ok": False,
            "error": str(e),
            "bt": [],
            "wifi": [],
            "bt_count": 0,
            "wifi_count": 0,
            "timestamp": time.time(),
        }


# ─── Fleet-node compat endpoints ─────────────────────────────────────
# These mirror the phone sensor-server endpoints that lilly_ai.py's
# node_fleet_poller() calls on every registered node, so the host radios
# can appear as a normal fleet node on radar_hub.html / tracker pages.


async def _fleet_bt_cached() -> tuple[list[dict], float]:
    """Return BT devices from cache when fresh (long fleet TTL), else kick off
    a background refresh and return whatever is cached (possibly empty on the
    very first call). This keeps the endpoint instant for the fleet poller."""
    now = time.time()
    if now - _bt_last["ts"] < _FLEET_BT_TTL:
        return _bt_last["data"], _bt_last["ts"]
    # Return current (possibly empty) cache immediately, refresh in background.
    _ensure_bt_adapter()
    asyncio.create_task(_refresh_bt_in_background())
    return _bt_last["data"], _bt_last["ts"]


async def _refresh_bt_in_background():
    try:
        devs = await _scan_bt()
        _bt_last.update({"ts": time.time(), "data": devs})
    except Exception as e:  # pragma: no cover
        logger.warning("background BT refresh failed: %s", e)


async def _fleet_wifi_cached() -> tuple[list[dict], float]:
    """Return WiFi from cache when fresh, else refresh in background and return
    cached. Never blocks on the slow BT scan, so the fleet poller's 6s timeout
    is never hit for wifi."""
    now = time.time()
    if now - _wifi_last["ts"] < _FLEET_WIFI_TTL:
        return _wifi_last["data"], _wifi_last["ts"]
    asyncio.create_task(_refresh_wifi_in_background())
    return _wifi_last["data"], _wifi_last["ts"]


async def _refresh_wifi_in_background():
    try:
        nets = await asyncio.to_thread(_scan_wifi)
        _wifi_last.update({"ts": time.time(), "data": nets})
    except Exception as e:  # pragma: no cover
        logger.warning("background WiFi refresh failed: %s", e)


@app.get("/wifi/scan")
async def fleet_wifi_scan():
    nets, ts = await _fleet_wifi_cached()
    return {"networks": nets, "count": len(nets), "timestamp": ts}


@app.get("/bluetooth/scan")
async def fleet_bt_scan():
    # Cache-first with a long fleet TTL so we never block on the slow ~16s
    # raw scan when the fleet poller (6s HTTP timeout) asks. Data refreshes
    # in the background and becomes available on the next poll.
    devs, ts = await _fleet_bt_cached()
    return {"devices": devs, "count": len(devs), "timestamp": ts}


@app.get("/location")
async def fleet_location():
    """Return a GPS location for the host node.

    Order of preference:
      1. explicit HOST_GPS_LAT / HOST_GPS_LNG
      2. a bound phone's /location (SENSOR_SERVER_URL)
    """
    if HOST_GPS_LAT and HOST_GPS_LNG:
        loc = {
            "latitude": float(HOST_GPS_LAT),
            "longitude": float(HOST_GPS_LNG),
            "accuracy": 0.0,
            "provider": "static",
        }
        return {"location": loc, "static": True, "timestamp": time.time()}
    if FORWARD_LOCATION_URL:
        try:
            import httpx

            with httpx.Client(timeout=8) as client:
                r = client.get(FORWARD_LOCATION_URL + "/location")
                if r.status_code == 200:
                    data = r.json()
                    loc = data.get("location")
                    if isinstance(loc, dict) and loc.get("latitude"):
                        return data
        except Exception as e:
            logger.debug("forwarded /location failed: %s", e)
    # Fall back to a saved-home default so the radar can place the host.
    return {
        "location": {
            "latitude": float(_HOME_DEFAULT_LAT),
            "longitude": float(_HOME_DEFAULT_LNG),
            "accuracy": 0.0,
            "provider": "home-default",
        },
        "default": True,
        "timestamp": time.time(),
    }


@app.get("/battery")
async def fleet_battery():
    # Desktop host is effectively always "plugged in" at full charge.
    return {
        "battery": {"percentage": 100, "level": 100, "scale": 100, "status": "full"}
    }


@app.get("/sensors/all")
async def fleet_sensors():
    # Host has no Android motion sensors; return an empty (but valid) payload.
    return {"sensors": {}, "timestamp": time.time()}


@app.get("/notification/list")
async def fleet_notifications():
    # Host does not surface notifications to the radar hub.
    return {"notifications": [], "timestamp": time.time()}


def main():
    parser = argparse.ArgumentParser(description="Lilly Host Scan Provider")
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("HOST_SCAN_PROVIDER_PORT", "8096")),
    )
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()
    logger.info("Host scan provider starting on %s:%s", args.host, args.port)
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
