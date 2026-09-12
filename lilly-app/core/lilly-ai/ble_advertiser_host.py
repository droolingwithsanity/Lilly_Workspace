#!/usr/bin/env python3
"""
Lilly BLE + Classic Advertiser — HOST machine (no bleak required).

Two advertising modes run simultaneously:

  1. BLE (LE non-connectable undirected advertising) — shows in BLE scanner apps
     and Lilly's own radar. Uses raw HCI commands; no bleak/D-Bus required.

  2. Classic BR/EDR "headphone pairing mode" — makes the adapter appear as an
     Audio/Headset device in ISCAN+PSCAN mode so nearby phones show a proactive
     "Lilly Pup wants to pair" notification without the user opening Bluetooth
     settings. The adapter is deliberately kept non-connectable at the L2CAP
     level (no SDP, no profile, PIN always rejected) so it announces itself but
     can never actually complete pairing — read-only presence advertising.

Runs on port 8110 so it coexists with the phone advertiser (8100).

Start:
    python3 ble_advertiser_host.py
  or
    python3 ble_advertiser_host.py --port 8110 --hci hci1

Endpoints match ble_advertiser_phone.py so _bt_proxy can treat them identically:
    GET  /health
    GET  /status
    POST /advertise   { name, image, service_uuid, web_ui_url, mode }
    POST /stop
    POST /configure   { name, image, service_uuid, web_ui_url, mode, hci }
    GET  /scan        ?duration=5  → {"devices":[...]}

mode field (in /advertise or /configure body):
    "ble"       — BLE only  (silent radar advertising)
    "classic"   — Classic only  (headphone popup, no BLE)
    "fast_pair" — Google Fast Pair BLE (triggers Android popup) only
    "both" /
    "balanced"  — Classic + Fast Pair BLE simultaneously (default)
    "all"       — same as "both"
"""

import asyncio
import logging
import os
import re
import struct
import subprocess
import time
from contextlib import asynccontextmanager
from typing import Optional

import uvicorn
from fastapi import FastAPI, Request

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger("BleAdvertiserHost")

PORT = int(os.environ.get("BLE_HOST_ADVERTISER_PORT", "8110"))
HCI = os.environ.get("BLE_HOST_HCI", "hci0")

# Public URL that nearby devices see in the BLE advertisement.
# Override via BLE_WEB_UI_URL env var (e.g. https://droolingwithsanity.ca).
_DEFAULT_WEB_UI_URL = os.environ.get("BLE_WEB_UI_URL", "https://droolingwithsanity.ca")

# ─── State ──────────────────────────────────────────────────────────────────

_advertising = False
_adv_task: Optional[asyncio.Task] = None
_config = {
    "name": "Lilly Pup",
    "image": "/static/lilly/puppy-avatar.svg",
    "service_uuid": "0000feed-0000-1000-8000-00805f9b34fb",
    "web_ui_url": _DEFAULT_WEB_UI_URL,
    "mode": "balanced",
    "hci": HCI,
}

# ─── HCI helpers ────────────────────────────────────────────────────────────


def _run(cmd: list, sudo: bool = True) -> tuple:
    """Run a command, optionally under sudo -n (no-password).

    When running as root (e.g., inside Docker), sudo is unnecessary —
    the container's /dev/hci0 is already accessible.  We auto-detect
    and skip sudo in that case so the tools don't need sudo installed.
    """
    if sudo and os.geteuid() == 0:
        sudo = False
    full = (["sudo", "-n"] if sudo else []) + cmd
    try:
        r = subprocess.run(full, capture_output=True, text=True, timeout=5)
        return r.returncode, r.stdout, r.stderr
    except subprocess.TimeoutExpired:
        return 1, "", "timeout"
    except FileNotFoundError:
        return 1, "", f"Command not found: {full[0]}"
    except Exception as e:
        return 1, "", str(e)


def _hci_cmd(hci: str, ogf_ocf_data: str) -> tuple:
    """Send a raw HCI command via hcitool cmd.

    ogf_ocf_data: space-separated hex bytes — first two are OGF OCF, rest is payload.
    e.g. "0x08 0x0006 A0 00 A0 00 00 00 00 00 00 00 00 00 00 07 00"
    Returns (ok: bool, output: str).

    Parses the HCI Command Complete event to check the actual status byte:
      - 0x0e event type = Command Complete
      - 0x0f event type = Command Status (used for LE Enable, etc.)
      - Last byte = status: 0x00 = success, anything else = error
    """
    parts = ogf_ocf_data.split()
    rc, out, err = _run(["hcitool", "-i", hci, "cmd"] + parts)
    combined = out + err
    if rc != 0 or not combined.strip():
        return False, combined

    # Parse HCI event status from output
    # hcitool output format:
    #   < HCI Command: ogf 0x08, ocf 0xXXXX, plen N
    #   > HCI Event: 0x0e plen N
    #     BB BB BB BB ...    (response bytes)
    for line in combined.splitlines():
        stripped = line.strip()
        # Look for response hex bytes (not the command/event header lines)
        if stripped.startswith("<") or stripped.startswith(">") or not stripped:
            continue
        hex_parts = stripped.split()
        if not hex_parts:
            continue
        # Check if this looks like hex bytes
        try:
            vals = [int(h, 16) for h in hex_parts]
        except ValueError:
            continue
        if len(vals) < 2:
            continue

        # HCI Command Complete (event 0x0e):
        #   byte 0 = 0x01 (Num HCI Command Packets)
        #   byte 1-2 = Command opcode (OGF|OCF little-endian)
        #   byte 3 = Parameter Total Length
        #   byte 4 = Status (0x00 = success)
        if vals[0] == 0x01 and len(vals) >= 5:
            status = vals[4]
            if status != 0:
                logger.warning(
                    f"[host-adv] HCI command failed: status=0x{status:02X} "
                    f"({HCI_STATUS.get(status, 'unknown error')})"
                )
                return False, combined
            return True, combined

        # HCI Command Status (event 0x0f):
        #   byte 0 = Status (0x00 = success)
        #   byte 1 = Num HCI Command Packets
        #   byte 2-3 = Command opcode
        if len(vals) >= 4:
            status = vals[0]
            if status != 0:
                logger.warning(
                    f"[host-adv] HCI command status: 0x{status:02X} "
                    f"({HCI_STATUS.get(status, 'unknown error')})"
                )
                return False, combined
            return True, combined

    # Fallback: if we got output but couldn't parse it, assume success
    # (this preserves backward compatibility)
    return True, combined


# HCI status code meanings for logging
HCI_STATUS = {
    0x00: "Success",
    0x01: "Unknown HCI Command",
    0x02: "Unknown Connection Identifier",
    0x04: "Page Timeout",
    0x05: "Authentication Failure",
    0x06: "PIN or Key Missing",
    0x07: "Memory Capacity Exceeded",
    0x08: "Connection Timeout",
    0x09: "Connection Limit Exceeded",
    0x0A: "Synchronous Connection Limit Exceeded",
    0x0B: "Connection Already Exists",
    0x0C: "Command Disallowed",
    0x0D: "Connection Rejected (Limited Resources)",
    0x0E: "Invalid HCI Command Parameters",
    0x0F: "Remote User Terminated Connection",
    0x10: "Remote Device Terminated (Low Resources)",
    0x11: "Remote Device Terminated (Power Off)",
    0x12: "Connection Terminated by Local Host",
    0x1A: "Unsupported Remote Feature",
    0x1F: "Unspecified Error",
    0x22: "LMP Response Timeout / LL Response Timeout",
}


def _encode_flags(classic_also: bool = False) -> bytes:
    """AD flags byte.

    classic_also=False → 0x06  (LE General Discoverable | BR/EDR Not Supported)
                           Standard BLE-only flag used for plain BLE advertising.
    classic_also=True  → 0x02  (LE General Discoverable, BR/EDR IS supported)
                           Required by Fast Pair spec when the adapter also does
                           Classic BT — tells Android the device can be fully paired.
    """
    flag = 0x02 if classic_also else 0x06
    return bytes([0x02, 0x01, flag])


def _encode_service_uuid(uuid_str: str) -> bytes:
    """Encode a 16-bit UUID AD structure (0x03 = Incomplete 16-bit UUIDs)."""
    try:
        short = uuid_str.replace("-", "").lower()
        if len(short) == 32:
            uid16 = int(short[4:8], 16)
            return bytes([0x03, 0x03]) + struct.pack("<H", uid16)
    except Exception:
        pass
    return b""


def _encode_name(name: str, max_bytes: int = 20) -> bytes:
    """AD type 0x09 = Complete Local Name."""
    enc = name.encode("utf-8")[:max_bytes]
    return bytes([len(enc) + 1, 0x09]) + enc


# ─── Google Fast Pair payload builders ────────────────────────────────────────
#
# Fast Pair spec:
#   https://developers.google.com/nearby/fast-pair/specifications/service/provider
#
# The BLE advertisement must contain:
#   1. Flags AD (0x01):  0x02  (LE General Discoverable, BR/EDR supported)
#   2. Service UUID (0x03): 0xFE2C  (Google Fast Pair service UUID, little-endian)
#   3. Service Data (0x16): 2-byte UUID 0xFE2C + 3-byte model ID
#
# That's it — 13 bytes total, fits easily in the 31-byte payload.
#
# Model IDs:
#   FAST_PAIR_MODEL_UNREGISTERED = 0x000000  → generic "unknown headphones" popup,
#                                               no image, works immediately
#   Once Google approves your registration you get a real 3-byte model ID that
#   maps to your device image + name on Google's servers.  Swap it in below.
#
# Fast Pair service UUID: 0xFE2C  (assigned to Google by Bluetooth SIG)
_FP_SERVICE_UUID = 0xFE2C

# Placeholder model ID — triggers the generic Fast Pair popup on Android.
# Replace with your approved model ID (e.g. 0x2A96E2) after Google registration.
FAST_PAIR_MODEL_ID: int = int(os.environ.get("FAST_PAIR_MODEL_ID", "0x000000"), 16)


def _encode_fast_pair_service_uuid() -> bytes:
    """16-bit Service UUID list AD — type 0x03, UUID 0xFE2C little-endian."""
    return bytes([0x03, 0x03]) + struct.pack("<H", _FP_SERVICE_UUID)


def _encode_fast_pair_service_data(model_id: int) -> bytes:
    """Service Data AD — type 0x16, 2-byte UUID + 3-byte model ID.

    Layout: [length] [0x16] [UUID_LO] [UUID_HI] [MODEL_B2] [MODEL_B1] [MODEL_B0]
    Model ID is big-endian in the service data per the Fast Pair spec.
    """
    uuid_bytes = struct.pack("<H", _FP_SERVICE_UUID)  # little-endian UUID
    model_bytes = struct.pack(">I", model_id & 0xFFFFFF)[1:]  # 3 bytes big-endian
    payload = bytes([0x16]) + uuid_bytes + model_bytes  # type + 2 + 3 = 6 bytes
    return bytes([len(payload)]) + payload  # prepend length = 7 bytes total


def _build_fast_pair_payload(model_id: int, classic_also: bool = True) -> bytes:
    """Build the 31-byte BLE advertising payload for Google Fast Pair.

    Triggers Android's background Fast Pair scanner to show a bottom-sheet popup
    without the user opening Bluetooth settings.

    classic_also should be True when the adapter is also doing Classic BR/EDR
    (ISCAN+PSCAN), which is our default "both" mode.
    """
    flags = _encode_flags(classic_also=classic_also)  # 3 bytes
    svc_uuid = _encode_fast_pair_service_uuid()  # 4 bytes
    svc_data = _encode_fast_pair_service_data(model_id)  # 7 bytes
    # Total significant: 14 bytes.  Pad to 31.
    raw = flags + svc_uuid + svc_data
    return (raw + bytes(31))[:31]


def _build_adv_payload(
    name: str,
    service_uuid: str,
    fast_pair: bool = False,
    model_id: int = 0,
    classic_also: bool = False,
) -> bytes:
    """Build a padded 31-byte LE advertising payload.

    fast_pair=True  → use Google Fast Pair format (triggers Android popup)
    fast_pair=False → standard BLE with flags + service UUID + local name
    """
    if fast_pair:
        return _build_fast_pair_payload(model_id, classic_also=classic_also)

    flags = _encode_flags(classic_also=classic_also)
    uuid = _encode_service_uuid(service_uuid)
    remaining = 31 - len(flags) - len(uuid) - 2
    name_ad = _encode_name(name, max_bytes=max(0, remaining))
    raw = flags + uuid + name_ad
    return (raw + bytes(31))[:31]


def _build_scan_resp(web_ui_url: str, name: str = "") -> bytes:
    """Build a scan response.

    For Fast Pair: carries the complete device name (Android shows this in the
    pairing sheet once it resolves the scan response).
    For standard BLE: carries web UI URL as manufacturer data.
    """
    if name:
        # Fast Pair: name in scan response so Android can display it
        name_ad = _encode_name(name, max_bytes=29)
        return (name_ad + bytes(31))[:31]
    # Standard: URL as manufacturer-specific data
    url_bytes = web_ui_url.encode("utf-8")[:25]
    ad = bytes([len(url_bytes) + 3, 0xFF, 0xFF, 0xFF]) + url_bytes
    return (ad + bytes(31))[:31]


def _payload_hex(payload: bytes) -> str:
    """Convert 31-byte payload to space-separated uppercase hex string."""
    return " ".join(format(b, "02X") for b in payload)


# ─── Classic BR/EDR "headphone pairing mode" ────────────────────────────────
#
# Device class bytes for Audio/Headset (what headphones use):
#   Major Service: Audio (bit 21) + Rendering (bit 18)  → 0x240000
#   Major Device:  Audio/Video                          → 0x000400
#   Minor Device:  Headset                              → 0x000004
#   Combined:      0x240404
_HEADSET_CLASS = "0x240404"

# Device class for Generic Audio (speaker/headphones — slightly broader):
_AUDIO_CLASS = "0x240400"

# Mapping from friendly mode names to CoD hex
_CLASS_MAP = {
    "headset": _HEADSET_CLASS,
    "headphone": _HEADSET_CLASS,
    "speaker": _AUDIO_CLASS,
    "audio": _AUDIO_CLASS,
}


def _classic_start(hci: str, name: str, device_class: str = _HEADSET_CLASS) -> bool:
    """Put adapter into Classic BR/EDR headphone pairing mode.

    Sets:
      - Device name  (what shows up in the popup)
      - Device class (Audio/Headset so phones show the headphone icon)
      - ISCAN + PSCAN  (Inquiry Scan + Page Scan = discoverable + connectable)

    The adapter will appear as a connectable headset in pairing mode on every
    nearby phone/laptop that does a BT scan.  Actual pairing is blocked at the
    BlueZ level because we never register an SDP service or accept a PIN —
    the connection attempt will fail gracefully (no crash, no data exchange).
    """
    _run(["hciconfig", hci, "up"])
    rc1, _, _ = _run(["hciconfig", hci, "name", name])
    rc2, _, _ = _run(["hciconfig", hci, "class", device_class])
    rc3, _, _ = _run(["hciconfig", hci, "piscan"])  # Page Scan + Inquiry Scan
    ok = all(r == 0 for r in (rc1, rc2, rc3))
    if ok:
        logger.info(
            f"[host-adv] Classic BT pairing mode active on {hci}: "
            f"name='{name}' class={device_class} (ISCAN+PSCAN)"
        )
    else:
        logger.warning(f"[host-adv] Classic setup partial — rc: {rc1},{rc2},{rc3}")
    return ok


def _classic_stop(hci: str) -> None:
    """Disable Classic BT discoverability — drop back to PSCAN only (connectable
    but no longer showing up in inquiry scans / pairing popups)."""
    # pscan = page scan only (connected devices can reach us, but we don't
    # broadcast for new discoveries)
    _run(["hciconfig", hci, "pscan"])
    # Restore a neutral name and generic Computer class
    _run(["hciconfig", hci, "name", "labhrasd"])
    _run(["hciconfig", hci, "class", "0x000104"])  # Computer, Desktop
    logger.info(f"[host-adv] Classic BT discoverability stopped on {hci}")


# ─── Advertising loop ────────────────────────────────────────────────────────


async def _adv_loop(name: str, service_uuid: str, web_ui_url: str) -> None:
    """Start advertising on hci — BLE/Fast Pair and/or Classic depending on config mode.

    mode values (in _config["mode"]):
      "ble"             — standard BLE non-connectable advertising only
      "classic"         — Classic BR/EDR ISCAN+PSCAN headphone pairing mode only
      "fast_pair"       — Google Fast Pair BLE (triggers Android popup) only
      "both" /
      "balanced" /
      "high_visibility" — Classic + BLE simultaneously (best coverage)
      "all"             — same as "both"
    """
    global _advertising
    hci = _config["hci"]
    mode = _config.get("mode", "both")

    # Resolve mode flags — normalize aliases
    if mode in ("balanced", "both", "all", "high_visibility"):
        do_classic = True
        do_ble = True
        do_fast_pair = True
    elif mode == "classic":
        do_classic = True
        do_ble = False
        do_fast_pair = False
    elif mode == "fast_pair":
        do_classic = False
        do_ble = True
        do_fast_pair = True
    else:
        # "ble", "power_save", "low_power", or unknown
        do_classic = False
        do_ble = True
        do_fast_pair = False

    model_id = _config.get("fast_pair_model_id", FAST_PAIR_MODEL_ID)

    logger.info(
        f"[host-adv] Starting '{name}' on {hci} "
        f"(mode={mode}, classic={do_classic}, fast_pair={do_fast_pair}, ble={do_ble})"
    )

    ble_via_dbus = False  # Track if we had to fall back to D-Bus for BLE

    try:
        _run(["hciconfig", hci, "up"])

        # ── Classic BR/EDR: headphone pairing popup ──────────────────────────
        if do_classic:
            _classic_start(hci, name, _HEADSET_CLASS)

        # ── BLE advertising (Fast Pair or standard) ───────────────────────────
        if do_ble:
            # Advertising parameters:
            #   OGF=0x08 OCF=0x0006
            #   interval ~100ms (0x00A0), type=0x00 (connectable undirected) for
            #   Fast Pair (Android requires connectable to complete the pairing
            #   handshake flow), or 0x03 (non-connectable) for standard BLE.
            adv_type = "00" if do_fast_pair else "03"
            ok, out = _hci_cmd(
                hci, f"0x08 0x0006 A0 00 A0 00 {adv_type} 00 00 00 00 00 00 00 00 07 00"
            )
            if not ok:
                logger.warning(
                    f"[host-adv] LE set adv params failed (raw HCI): {out.strip()[:120]}"
                )

            # Build the right payload
            adv_pl = _build_adv_payload(
                name,
                service_uuid,
                fast_pair=do_fast_pair,
                model_id=model_id,
                classic_also=do_classic,
            )

            # Significant byte count
            if do_fast_pair:
                # flags(3) + svc_uuid(4) + svc_data(7) = 14 significant bytes
                sig = 14
            else:
                flags = _encode_flags(classic_also=do_classic)
                uuid_ad = _encode_service_uuid(service_uuid)
                name_ad = _encode_name(
                    name, max_bytes=max(0, 31 - len(flags) - len(uuid_ad) - 2)
                )
                sig = min(len(flags + uuid_ad + name_ad), 31)

            ok, out = _hci_cmd(hci, f"0x08 0x0008 {sig:02X} {_payload_hex(adv_pl)}")
            if not ok:
                logger.warning(
                    f"[host-adv] LE set adv data failed (raw HCI): {out.strip()[:120]}"
                )

            # Scan response: name for Fast Pair (Android displays it in the sheet),
            # URL for standard BLE
            if do_fast_pair:
                scan_pl = _build_scan_resp(web_ui_url, name=name)
                name_bytes = name.encode("utf-8")[:29]
                scan_sig = min(len(name_bytes) + 2, 31)
            else:
                scan_pl = _build_scan_resp(web_ui_url)
                scan_sig = min(len(web_ui_url.encode()) + 4, 31)

            ok, out = _hci_cmd(
                hci, f"0x08 0x0009 {scan_sig:02X} {_payload_hex(scan_pl)}"
            )
            if not ok:
                logger.warning(
                    f"[host-adv] LE set scan resp failed (raw HCI): {out.strip()[:120]}"
                )

            # Enable LE advertising
            ok, out = _hci_cmd(hci, "0x08 0x000A 01")
            if not ok:
                logger.warning(
                    f"[host-adv] Raw HCI LE advertising failed — "
                    f"trying D-Bus LEAdvertisingManager1 fallback"
                )
                # ── D-Bus fallback for adapters that reject raw HCI ──
                ble_via_dbus = _dbus_adv_start_sync(name, service_uuid, hci)
                if not ble_via_dbus:
                    logger.error(
                        "[host-adv] D-Bus fallback also failed — BLE advertising unavailable"
                    )
                    if not do_classic:
                        _advertising = False
                        return
            else:
                fp_note = " [Fast Pair — Android popup active]" if do_fast_pair else ""
                logger.info(f"[host-adv] BLE advertising active on {hci}{fp_note}")

        # Keep alive loop
        while _advertising:
            await asyncio.sleep(30)
            if not _advertising:
                break
            if do_ble and not ble_via_dbus:
                _hci_cmd(hci, "0x08 0x000A 01")
            if do_classic:
                rc, out, err = _run(["hciconfig", hci])
                if "ISCAN" not in out:
                    logger.debug("[host-adv] ISCAN dropped, re-asserting")
                    _classic_start(hci, name, _HEADSET_CLASS)

    except asyncio.CancelledError:
        pass
    except Exception as e:
        logger.error(f"[host-adv] Loop error: {e}")
        _advertising = False
    finally:
        try:
            if do_ble:
                if ble_via_dbus:
                    _dbus_adv_stop_sync()
                else:
                    _hci_cmd(hci, "0x08 0x000A 00")
                    _run(["hciconfig", hci, "noleadv"])
        except Exception:
            pass
        try:
            if do_classic:
                _classic_stop(hci)
        except Exception:
            pass
        logger.info("[host-adv] Advertising stopped")


# ─── D-Bus LE Advertising Fallback ────────────────────────────────────────
# For Intel/Realtek adapters where raw HCI LE advertising commands fail.
# Spawns a small Python helper that registers on D-Bus and stays alive to
# keep the advertisement active.  Uses subprocess (not asyncio) so it works
# from both sync and async contexts.

_dbus_adv_proc: Optional[subprocess.Popen] = None


def _dbus_adv_start_sync(name: str, service_uuid: str, hci: str) -> bool:
    """Register a BLE advertisement via D-Bus helper subprocess."""
    global _dbus_adv_proc

    # Kill any existing D-Bus ad
    _dbus_adv_stop_sync()

    # Build the helper script — note: use %s substitution, NOT f-strings,
    # because {hci} etc. must be literal in the subprocess script.
    script = (
        '''#!/usr/bin/env python3
import dbus, dbus.service, dbus.mainloop.glib, signal, sys, time
from gi.repository import GLib

dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
loop = GLib.MainLoop()

class Ad(dbus.service.Object):
    def __init__(self):
        super().__init__(dbus.SystemBus(), "/org/bluez/lilly_ad")
    @dbus.service.method("org.freedesktop.DBus.Properties", in_signature="ss", out_signature="v")
    def Get(self, iface, prop):
        return self.GetAll(iface).get(prop)
    @dbus.service.method("org.freedesktop.DBus.Properties", in_signature="s", out_signature="a{sv}")
    def GetAll(self, iface):
        if iface == "org.bluez.LEAdvertisement1":
            return {
                "Type": dbus.String("peripheral"),
                "ServiceUUIDs": dbus.Array(["'''
        + service_uuid
        + '''"], signature="s"),
                "LocalName": dbus.String("'''
        + name
        + """"),
                "IncludeTxPowerLevel": dbus.Boolean(True),
                "IncludeName": dbus.Boolean(True),
            }
        return {}
    @dbus.service.method("org.bluez.LEAdvertisement1")
    def Release(self):
        pass

signal.signal(signal.SIGTERM, lambda s, f: loop.quit())
signal.signal(signal.SIGINT, lambda s, f: loop.quit())
Ad()
time.sleep(0.5)
bus = dbus.SystemBus()
proxy = bus.get_object("org.bluez", "/org/bluez/"""
        + hci
        + """")
mgr = dbus.Interface(proxy, "org.bluez.LEAdvertisingManager1")
try:
    mgr.RegisterAdvertisement(
        dbus.ObjectPath("/org/bluez/lilly_ad"),
        dbus.Dictionary({}, signature="sv")
    )
    sys.stderr.write("LILLY_ADV_OK\\n")
    sys.stderr.flush()
except Exception as e:
    sys.stderr.write("LILLY_ADV_FAIL:" + str(e) + "\\n")
    sys.stderr.flush()
    sys.exit(1)
loop.run()
"""
    )
    try:
        _dbus_adv_proc = subprocess.Popen(
            ["python3", "-c", script],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        # Wait for OK/FAIL on stderr with timeout
        import select

        deadline = time.time() + 10
        while time.time() < deadline:
            if select.select([_dbus_adv_proc.stderr], [], [], 0.5)[0]:
                line = _dbus_adv_proc.stderr.readline().decode().strip()
                if "LILLY_ADV_OK" in line:
                    logger.info(
                        f"[host-adv] D-Bus BLE ad registered on {hci} (pid={_dbus_adv_proc.pid})"
                    )
                    return True
                elif "LILLY_ADV_FAIL" in line:
                    logger.warning(f"[host-adv] D-Bus BLE ad failed: {line}")
                    _dbus_adv_proc.kill()
                    _dbus_adv_proc = None
                    return False
            if _dbus_adv_proc.poll() is not None:
                logger.warning("[host-adv] D-Bus helper exited unexpectedly")
                _dbus_adv_proc = None
                return False
        logger.warning("[host-adv] D-Bus registration timed out after 10s")
        _dbus_adv_proc.kill()
        _dbus_adv_proc = None
        return False
    except Exception as e:
        logger.error(f"[host-adv] D-Bus helper error: {e}")
        _dbus_adv_proc = None
        return False


def _dbus_adv_stop_sync() -> None:
    """Stop D-Bus advertising helper."""
    global _dbus_adv_proc
    if _dbus_adv_proc and _dbus_adv_proc.poll() is None:
        _dbus_adv_proc.terminate()
        try:
            _dbus_adv_proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            _dbus_adv_proc.kill()
        logger.info("[host-adv] D-Bus ad process stopped")
    _dbus_adv_proc = None


# ─── FastAPI app ─────────────────────────────────────────────────────────────


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    # Clean up advertising on shutdown
    global _adv_task, _advertising
    _advertising = False
    if _adv_task and not _adv_task.done():
        _adv_task.cancel()
        try:
            await _adv_task
        except asyncio.CancelledError:
            pass


app = FastAPI(lifespan=lifespan)


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "service": "ble-advertiser-host",
        "port": PORT,
        "hci": _config["hci"],
    }


@app.get("/status")
async def status():
    hci = _config["hci"]
    # Check live Classic BT state from hciconfig
    classic_active = False
    try:
        rc, out, _ = _run(["hciconfig", hci], sudo=False)
        classic_active = "ISCAN" in out
    except Exception:
        pass
    mode = _config.get("mode", "both")
    return {
        "ok": True,
        "advertising": _advertising,
        "ble_advertising": _advertising and mode not in ("classic",),
        "classic_advertising": classic_active,
        "mode": mode,
        "config": dict(_config),
        "source": "host",
        "hci": hci,
        "timestamp": time.time(),
    }


@app.post("/advertise")
async def start_advertise(req: Request):
    global _advertising, _adv_task, _config

    try:
        body = await req.json()
    except Exception:
        body = {}

    if _advertising:
        return {
            "ok": True,
            "advertising": True,
            "already_advertising": True,
            "name": _config["name"],
            "mode": _config.get("mode", "both"),
            "source": "host",
            "message": "Already advertising",
        }

    for key in (
        "name",
        "image",
        "service_uuid",
        "web_ui_url",
        "mode",
        "fast_pair_model_id",
    ):
        if body.get(key) is not None:
            _config[key] = body[key]

    _advertising = True
    _adv_task = asyncio.create_task(
        _adv_loop(
            name=_config["name"],
            service_uuid=_config["service_uuid"],
            web_ui_url=_config["web_ui_url"],
        )
    )

    await asyncio.sleep(0.8)  # let the loop set up both BLE + Classic

    mode = _config.get("mode", "both")
    do_classic = mode not in ("ble",)
    do_ble = mode not in ("classic",)
    parts = []
    if do_classic:
        parts.append("Classic BR/EDR headphone pairing popup")
    if do_ble:
        parts.append("BLE advertising")
    mode_desc = " + ".join(parts) if parts else mode

    return {
        "ok": _advertising,
        "advertising": _advertising,
        "name": _config["name"],
        "image": _config["image"],
        "service_uuid": _config["service_uuid"],
        "web_ui_url": _config["web_ui_url"],
        "mode": mode,
        "classic_advertising": do_classic and _advertising,
        "ble_advertising": do_ble and _advertising,
        "source": "host",
        "hci": _config["hci"],
        "message": f"Started '{_config['name']}' on {_config['hci']} — {mode_desc}",
        "timestamp": time.time(),
    }


@app.post("/stop")
async def stop_advertise():
    global _advertising, _adv_task

    was = _advertising
    _advertising = False

    if _adv_task and not _adv_task.done():
        _adv_task.cancel()
        try:
            await _adv_task
        except asyncio.CancelledError:
            pass
        _adv_task = None

    hci = _config["hci"]
    # Belt-and-suspenders: directly clean up both modes
    _hci_cmd(hci, "0x08 0x000A 00")  # disable BLE advertising
    _run(["hciconfig", hci, "noleadv"])
    _classic_stop(hci)  # drop ISCAN, restore name/class

    return {
        "ok": True,
        "was_advertising": was,
        "advertising": False,
        "classic_advertising": False,
        "ble_advertising": False,
        "source": "host",
    }


@app.post("/configure")
async def configure(req: Request):
    try:
        body = await req.json()
    except Exception:
        body = {}
    for key in (
        "name",
        "image",
        "service_uuid",
        "web_ui_url",
        "mode",
        "hci",
        "fast_pair_model_id",
    ):
        if body.get(key) is not None:
            _config[key] = body[key]
    return {"ok": True, "config": dict(_config), "source": "host"}


# ─── BLE Scan ───────────────────────────────────────────────────────────


def _classify_bt(name: str, service_uuids: list | None) -> str:
    """Minimal BLE classification via service UUIDs + name hints.

    Mirrors the classifier in lilly_ai.py so the radar/UI gets labels
    even when only hcitool lescan data is available (no full AD metadata).
    """
    if service_uuids:
        norm = [str(u).lower().replace("-", "") for u in service_uuids]
        for prefix, t in (
            ("180d", "phone"),
            ("eafe", "phone"),
            ("180f", "watch"),
            ("fee0", "watch"),
            ("180a", "watch"),
            ("febd", "headphones"),
            ("fe8f", "headphones"),
            ("1809", "headphones"),
            ("fe2c", "tracker"),
            ("feb0", "tracker"),
            ("feaa", "tracker"),
            ("feab", "tracker"),
            ("fee4", "tracker"),
            ("fe95", "smart_home"),
        ):
            if any(p in u for u in norm):
                return t
    if name:
        nl = name.lower()
        for hints, t in (
            (
                [
                    "iphone",
                    "android",
                    "pixel",
                    "galaxy s",
                    "galaxy z",
                    "oneplus",
                    "xiaomi",
                    "samsung a",
                    "oppo",
                    "vivo",
                    "huawei",
                ],
                "phone",
            ),
            (["ipad", "tab", "tablet"], "tablet"),
            (["macbook", "thinkpad", "xps", "laptop", "surface"], "laptop"),
            (["watch", "band", "mi band", "amazfit", "fitbit", "garmin"], "watch"),
            (["airpod", "buds", "earbud", "headphone", "headset"], "headphones"),
            (["homepod", "echo", "speaker", "boombox"], "speaker"),
            (["tv", "roku", "firestick", "chromecast"], "tv"),
            (
                [
                    "airtag",
                    "tile",
                    "chipolo",
                    "smarttag",
                    "smart tag",
                    "pebble",
                    "trackr",
                    "findmy",
                ],
                "tracker",
            ),
            (["tesla", "model 3", "model y", "model s"], "vehicle"),
        ):
            if any(h in nl for h in hints):
                return t
    return "unknown"


@app.get("/scan")
async def scan_ble(duration: float = 5.0):
    """Scan for nearby BLE devices using hcitool.

    Returns classified devices in the same ``{"devices": [...]}`` format
    that lilly_ai.py's ``_bt_scan_sources`` aggregator expects.

    Duration is clamped to 1–15 seconds.
    """
    duration = min(max(float(duration), 1.0), 15.0)
    hci = _config.get("hci", "hci0")

    # hcitool lescan runs indefinitely; wrap with ``timeout``.
    # --active requests scan-response data (full name) from scannable devices.
    rc, out, err = _run(
        [
            "timeout",
            f"{int(duration) + 1}",
            "hcitool",
            "-i",
            hci,
            "lescan",
            "--active",
            "--duplicates",
        ],
        sudo=True,
    )

    devices: list[dict] = []
    seen: set[str] = set()

    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        # Format: "AA:BB:CC:DD:EE:FF (public) Device Name" or
        #         "AA:BB:CC:DD:EE:FF (random) Device Name"
        # Some versions include RSSI inline: "AA:BB:... Device Name [RSSI -50]"
        parts = line.split(None, 3)
        if len(parts) < 2:
            continue
        mac = parts[0].lower().replace(":", "")
        if not re.match(r"^[0-9a-f]{12}$", mac):
            continue
        if mac in seen:
            continue
        seen.add(mac)

        # Extract name (strip type annotations like "(public)", "(random)", "(complete)")
        raw_name = ""
        for chunk in parts[1:]:
            if chunk.startswith("(") or chunk.startswith("LE"):
                continue
            raw_name += " " + chunk
        raw_name = raw_name.strip()
        if not raw_name:
            raw_name = "(unknown)"

        # RSSI is not available from hcitool lescan output on most versions;
        # use a placeholder that sorts devices naturally.
        devices.append(
            {
                "address": mac,
                "mac": mac,
                "name": raw_name,
                "rssi": -100,
                "classified_type": _classify_bt(raw_name, None),
                "source": "host_advertiser",
                "bt_type": "ble",
            }
        )

    return {
        "ok": True,
        "devices": devices,
        "count": len(devices),
        "source": "host",
        "hci": hci,
        "duration": duration,
        "timestamp": time.time(),
    }


@app.post("/advertise/switch-mode")
async def switch_mode(mode: str = "balanced"):
    """Switch advertising mode on-the-fly.

    Modes: "ble" | "classic" | "fast_pair" | "both" | "balanced"
    Maps phone-side interval-mode names ("high_visibility", "power_save", etc.)
    to the host advertiser's type-mode names.

    Restarts the advertiser with the new mode so the change takes effect
    immediately.
    """
    global _advertising, _adv_task, _config

    # Map phone-side interval profiles to host-side advertising types
    _mode_map = {
        "high_visibility": "both",
        "balanced": "both",
        "power_save": "ble",
        "low_power": "ble",
    }
    resolved = _mode_map.get(mode, mode)

    if resolved not in ("ble", "classic", "fast_pair", "both", "balanced", "all"):
        return {
            "ok": False,
            "error": f"Unknown mode: {mode}. Use: ble, classic, fast_pair, both, balanced",
        }

    _config["mode"] = resolved

    if not _advertising:
        return {
            "ok": False,
            "error": "Not currently advertising. Start advertising first.",
        }

    # Restart the advertiser with the new mode
    if _adv_task and not _adv_task.done():
        _adv_task.cancel()
        try:
            await _adv_task
        except asyncio.CancelledError:
            pass
        _adv_task = None

    # Clean up old advertising
    hci = _config["hci"]
    _hci_cmd(hci, "0x08 0x000A 00")
    _run(["hciconfig", hci, "noleadv"])
    _classic_stop(hci)

    _advertising = True
    _adv_task = asyncio.create_task(
        _adv_loop(
            name=_config["name"],
            service_uuid=_config["service_uuid"],
            web_ui_url=_config["web_ui_url"],
        )
    )
    await asyncio.sleep(0.8)

    return {
        "ok": _advertising,
        "mode": resolved,
        "advertising": _advertising,
        "source": "host",
        "message": f"Switched to '{resolved}' mode",
        "timestamp": time.time(),
    }


# ─── Entry point ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser(description="Lilly BLE Host Advertiser")
    p.add_argument("--port", type=int, default=PORT)
    p.add_argument("--host", type=str, default="0.0.0.0")
    p.add_argument(
        "--hci", type=str, default=HCI, help="HCI device to use (default: hci0)"
    )
    args = p.parse_args()
    PORT = args.port
    _config["hci"] = args.hci
    logger.info(
        f"Starting BLE Host Advertiser on {args.host}:{args.port} using {args.hci}"
    )
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
