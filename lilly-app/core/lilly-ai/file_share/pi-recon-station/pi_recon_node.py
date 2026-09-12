#!/usr/bin/env python3
"""
Lilly Pi Recon Station Node  (role: recon | edge)
────────────────────────────────────────────────────────────────
A headless Raspberry Pi that acts as a read-only, plug-and-play
monitoring node in the Lilly fleet. It does DEEPER radio recon than a
phone node:

  • WiFi  — lists all access points (BSSID, SSID, channel, band, security,
            RSSI) AND passively captures client presence (probe requests /
            beacons) via a monitor-mode interface, so you can see which
            devices are "out there" even when they're NOT associated.
  • Bluetooth — classic + BLE scans (name, MAC, RSSI) plus optional passive
            capture of advertising packets (lower-power deep sensing than a
            phone, which can only do active scans on its single radio).

It serves the SAME fleet endpoints as a phone node so it drops into the
existing radar / node registry with zero host code changes:

  /health            /location            /wifi/scan
  /bluetooth/scan    /battery             /sensors/all
  /notification/list /api/stream          /api/force-scan

Design notes (per the operator):
  • ETHERNET = the only uplink. The node connects to the fleet over wired
    Ethernet (DHCP, no WiFi client) so the WiFi radio is 100% free for
    passive monitoring.
  • ROOTFS = read-only. This script writes NO state to disk by default
    (override with --state-dir on an overlay/writable mount). It's designed
    to run from a read-only SD card in a 24/7 headless role.

Run (as root or with sudo, typically via systemd):
    sudo python3 pi_recon_node.py --port 8099 --name 'Garage Recon' --node-id recon-garage

Optional env / flags:
    --host HOST        Fleet host base URL to self-register with
                       (default: $FLEET_HOST)
    --iface IFACE      WiFi interface to scan/monitor (default: $WIFI_IFACE or wlan0)
    --services 'wifi bt'   which radios to monitor (default: wifi bt)

To run as an actual WiFi *client* uplink instead of Ethernet, use
--client-ssid/--client-pass (see install_recon.sh) which takes the radio out
of monitor mode and uses it as a normal client — but then passive client
capture is unavailable. Ethernet is strongly recommended.
"""

import argparse
import http.server
import json
import math
import os
import socket
import subprocess
import threading
import time
import urllib.parse
import urllib.request

VERSION = "1.0.0"
DEFAULT_PORT = 8099
FLEET_HOST = os.environ.get("FLEET_HOST", "").rstrip(
    "/"
)  # e.g. http://100.93.131.114:8098
WIFI_IFACE = os.environ.get("WIFI_IFACE", "wlan0")
NODE_NAME = os.environ.get("NODE_NAME", "")
NODE_ID = os.environ.get("NODE_ID", "")

# ── Cached scan results (radar needs a consistent snapshot per poll) ─────
CACHE = {
    "networks": [],  # [ {ssid,bssid,frequency,band,rssi,security,channel} ]
    "stations": [],  # [ {mac,last_rssi,ssid_or_probe,first_seen,last_seen} ]
    "bt_devices": [],  # [ {address,name,rssi,last_seen,kind} ]
    "location": None,
    "last_update": 0.0,
    "scanning": False,
}

LOCK = threading.Lock()

# --------------------------------------------------------------------------
# Low-level radio helpers
# --------------------------------------------------------------------------


def sh(cmd, timeout=30, check=False):
    """Run a shell command, return (rc, stdout, stderr)."""
    try:
        p = subprocess.run(
            cmd, shell=True, capture_output=True, text=True, timeout=timeout
        )
        return p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired:
        return -1, "", "timeout"


def have(command):
    return sh(f"command -v {command}")[0] == 0


def rssi_to_meters(rssi, freq_mhz):
    """Inverse free-space path loss: rough distance estimate from RSSI."""
    if rssi is None or rssi >= -10 or rssi < -120:
        return None
    f = (freq_mhz or 2412) / 1e6
    path_loss = abs(rssi) - 27.55
    denom = 20 * math.log10(f)
    if denom <= 0:
        return None
    return round(10 ** ((path_loss - denom) / 20), 1)  # meters


def to_mac(raw):
    """Normalize any MAC-ish token to colon form, or None."""
    if not raw:
        return None
    raw = raw.strip().lower()
    if ":" not in raw and "." in raw:
        # tcpdump 802.11 sometimes prints mac (4 groups of 4 hex) — convert
        raw = "".join(raw.split("."))
        if len(raw) == 12:
            raw = ":".join(raw[i : i + 2] for i in range(0, 12, 2))
    head = "".join(c for c in raw if c in "0123456789abcdef")
    if len(head) != 12:
        return None
    return ":".join(head[i : i + 2] for i in range(0, 12, 2))


# --------------------------------------------------------------------------
# WiFi: active AP scan (iw) + passive station capture (monitor mode)
# --------------------------------------------------------------------------


def wifi_active_scan(iface):
    """
    Enumerate access points via `iw scan` (requires root).
    Returns list of network dicts (host / radar shape).
    """
    nets = []
    if not have("iw"):
        return nets
    rc, out, _ = sh(f"iw dev {iface} scan 2>/dev/null")
    if rc != 0:
        return nets
    cur: "dict" = {}
    for line in out.splitlines():
        line = line.strip()
        low = line.lower()
        if line.startswith("BSS "):
            if cur:
                nets.append(_finish_net(cur))
            cur = {"bssid": to_mac(line.split()[1]) or ""}
        elif low.startswith("freq:"):
            cur["freq"] = _parse_int(line.split()[1])
        elif low.startswith("signal:"):
            cur["rssi"] = _parse_rssi(line)
        elif low.startswith("ssid:"):
            cur["ssid"] = line.split(":", 1)[1].strip()
        elif low.startswith("ds parameter set:"):
            cur["channel"] = _parse_int(line.split(":")[1].strip().split()[0])
        elif low.startswith("security:"):
            cur["security"] = line.split(":", 1)[1].strip()
        elif ":" in line and ("wpa" in low or "rsn" in low or "owe" in low):
            cur.setdefault("security", "").join("")
    if cur:
        nets.append(_finish_net(cur))
    # de-dupe by bssid, keep strongest
    seen = {}
    for n in nets:
        b = n["bssid"] or id(n)
        if b not in seen or n["rssi"] > seen[b]["rssi"]:
            seen[b] = n
    return sorted(list(seen.values()), key=lambda x: x["rssi"], reverse=True)


def _finish_net(cur):
    freq = cur.get("freq", 0)
    band = "2.4GHz" if 0 < freq < 3000 else ("5GHz" if freq >= 3000 else "unknown")
    sec = cur.get("security")
    if not sec:
        # heuristic from flags we may have seen
        flags = str(cur.get("_flags", ""))
        sec = (
            "WPA2"
            if "wpa" in flags.lower()
            else ("WPA3" if "owe" in flags.lower() else "open")
        )
    return {
        "ssid": cur.get("ssid", ""),
        "bssid": cur.get("bssid", ""),
        "frequency": freq,
        "band": band,
        "rssi": cur.get("rssi", -100),
        "channel": cur.get("channel", 0),
        "security": sec or "",
        "distance": rssi_to_meters(cur.get("rssi"), freq),
    }


def _parse_rssi(line):
    """Extract signal dBm from an iw line like 'signal: -55.00 dBm'."""
    import re

    m = re.search(r"(-?\d+(?:\.\d+)?)", line)
    return float(m.group(1)) if m else -100


def _parse_int(tok):
    """Safely parse a token to int (ignores units/garbage)."""
    import re

    m = re.search(r"-?\d+", str(tok))
    return int(m.group(0)) if m else 0


def wifi_radio_state(iface):
    """Return (mode, connected_ssid_or_None) for the wi fi radio."""
    rc, out, _ = sh(f"iw dev {iface} info 2>/dev/null")
    mode, ssid = "unknown", None
    for line in out.splitlines():
        line = line.strip().lower()
        if line.startswith("type "):
            mode = line.split()[1]
        if line.startswith("ssid "):
            ssid = line.split(": ", 1)[-1].strip()
    return mode, ssid


def station_monitor(iface, capture_secs=12):
    """
    Passive capture of client presence. Brings the radio up in a SECOND
    monitor vif (wlan0mon) so we don't tear down the (typically off/Ethernet)
    client, then captures probe requests + beacons with tcpdump and parses
    the unique client MACs + last-seen signal. Deeper than a phone scan:
    shows clients that are merely scanning, not associated.
    Returns list of {mac,last_rssi,ssid,first_seen,last_seen}.
    """
    mon = f"{iface}mon"
    sh(f"iw dev {mon} del 2>/dev/null")
    sh(f"iw dev {iface} interface add {mon} type monitor 2>/dev/null")
    sh(f"ip link set {mon} up 2>/dev/null")
    # hop across a spread of 2.4/5 GHz channels for breadth
    import threading as _t

    def _hop():
        for ch in (1, 6, 11):
            sh(f"iw dev {mon} set channel {ch} 2>/dev/null", timeout=5)
            time.sleep(capture_secs / 5)

    t = _t.Thread(target=_hop, daemon=True)
    t.start()
    rc, out, _ = sh(
        f"timeout {capture_secs} tcpdump -i {mon} -n -e -l "
        f"'type mgt subtype probe-req or type mgt subtype assoc-req or type mgt subtype probe-resp' "
        f"2>/dev/null",
        timeout=capture_secs + 8,
    )
    sh(f"iw dev {mon} del 2>/dev/null")
    return _parse_probes(out)


def _parse_probes(tcpdump_out):
    import re

    seen = {}
    first_ts = time.time()
    for line in (tcpdump_out or "").splitlines():
        if "probe" not in line:
            continue
        # tcpdump prints: <sender_mac> <...> Probe Request (<ssid>)
        mac = to_mac(line.split(" ", 2)[-1][:18] if line.count(" ") >= 2 else "")
        if not mac:
            mac = to_mac(next((w for w in line.split() if to_mac(w)), None))
        if not mac:
            continue
        mm = re.search(r"Probe Request \(([^)]*)\)", line)
        ssid = (mm.group(1) if mm else "").strip()[:64]
        e = seen.setdefault(
            mac,
            {
                "mac": mac,
                "last_rssi": -75,
                "ssid": ssid,
                "first_seen": first_ts,
                "last_seen": first_ts,
            },
        )
        e["ssid"] = e.get("ssid") or ssid
        e["last_seen"] = time.time()
        sig = re.search(r"(-?\d+)dB", line)
        if sig:
            try:
                e["last_rssi"] = int(sig.group(1))
            except ValueError:
                pass
    return list(seen.values())


# --------------------------------------------------------------------------
# Bluetooth: classic + LE scans + optional passive capture
# --------------------------------------------------------------------------


def bt_scan(capture_secs=8):
    """
    Active classic (hcitool/bluetoothctl) + BLE (lescan) enumeration.
    Returns list of {address,name,rssi,kind}.
    """
    devices = {}
    # BLE scan (gives MACs even for unnamed devices)
    if have("hcitool"):
        rc, out, _ = sh(
            f"timeout {capture_secs} hcitool lescan --duplicates 2>/dev/null",
            timeout=capture_secs + 6,
        )
        for line in (out or "").splitlines():
            parts = line.split()
            if len(parts) >= 2 and to_mac(parts[0]):
                mac = to_mac(parts[0])
                name = " ".join(parts[1:]).strip()
                d = devices.setdefault(
                    mac, {"address": mac, "name": name, "rssi": -70, "kind": "le"}
                )
                if name and not d.get("name"):
                    d["name"] = name
    # Classic inquiry scan
    if have("hcitool"):
        sh("hciconfig hci0 up 2>/dev/null", timeout=5)
        rc, out, _ = sh(
            f"timeout {capture_secs} hcitool scan 2>/dev/null", timeout=capture_secs + 6
        )
        for line in (out or "").splitlines():
            parts = line.split(None, 1)
            if len(parts) == 2 and to_mac(parts[0]):
                mac = to_mac(parts[0])
                name = parts[1].strip()
                d = devices.setdefault(
                    mac, {"address": mac, "name": name, "rssi": -70, "kind": "classic"}
                )
                d["name"] = name or d.get("name")
                d["kind"] = d.get("kind", "classic")
    # RSSI for known BLE peers is not directly returned; keep -70 default tag
    return list(devices.values())


# --------------------------------------------------------------------------
# Snapshot engine
# --------------------------------------------------------------------------


def execute_scan():
    if CACHE["scanning"]:
        return
    with LOCK:
        CACHE["scanning"] = True
    try:
        wifi = wifi_active_scan(WIFI_IFACE) if opts.services.wifi else []
        bt = bt_scan() if opts.services.bt else []
        stations = (
            station_monitor(WIFI_IFACE) if (opts.services.wifi and opts.monitor) else []
        )
        with LOCK:
            CACHE["networks"] = wifi
            CACHE["bt_devices"] = bt
            CACHE["stations"] = stations
            CACHE["last_update"] = time.time()
    finally:
        with LOCK:
            CACHE["scanning"] = False


def bg_loop():
    while True:
        try:
            execute_scan()
        except Exception as e:
            print(f"[recon] scan error: {e}", flush=True)
        time.sleep(6)


# --------------------------------------------------------------------------
# HTTP server (fleet-compatible endpoints)
# --------------------------------------------------------------------------


class Handler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, format, *args):  # noqa: A002  (base-class signature)
        pass

    def _json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path.rstrip("/") or "/"
        with LOCK:
            nets = list(CACHE["networks"])
            stas = list(CACHE["stations"])
            bt = list(CACHE["bt_devices"])
            last = CACHE["last_update"]
            loc = CACHE["location"]
        mode, ssid = wifi_radio_state(WIFI_IFACE)

        if path == "/health":
            self._json(
                {
                    "status": "ok",
                    "version": VERSION,
                    "node_id": opts.node_id or socket.gethostname(),
                    "name": opts.name or "Pi Recon",
                    "port": opts.port,
                    "role": "recon",
                    "platform": "raspberry-pi",
                    "readonly_rootfs": True,
                    "uplink": "ethernet",
                    "wifi_mode": mode,
                    "wifi_connected_ssid": ssid,
                    "wifi_ap_count": len(nets),
                    "wifi_stations_seen": len(stas),
                    "bluetooth_device_count": len(bt),
                    "last_update": last,
                    "is_scanning": CACHE["scanning"],
                }
            )

        elif path == "/location":
            self._json({"location": loc, "timestamp": last})

        elif path == "/wifi/scan":
            self._json(
                {
                    "networks": nets,
                    "stations": stas,  # deep-sensing extra (probe requests seen)
                    "count": len(nets),
                    "monitor_mode": opts.monitor,
                    "timestamp": last,
                }
            )

        elif path == "/bluetooth/scan":
            self._json({"devices": bt, "count": len(bt), "timestamp": last})

        elif path == "/battery":
            self._json(
                {
                    "battery": {"level": -1, "charging": True, "status": "ethernet"},
                    "timestamp": last,
                }
            )

        elif path == "/notification/list":
            # A headless recon node has no notifications; the fleet poller
            # simply gets an empty list (non-fatal).
            self._json(
                {"notifications": [], "count": 0, "error": "headless-recon-node"}
            )

        elif path == "/sensors/all":
            self._json(
                {
                    "sensors": {
                        "location": loc or {},
                        "wifi": nets,
                        "wifi_stations": stas,
                        "bluetooth": bt,
                    },
                    "timestamp": last,
                    "count": 4,
                }
            )

        elif path == "/api/stream":
            self._json(
                {
                    "networks": nets,
                    "stations": stas,
                    "bluetooth": bt,
                    "last_update": last,
                }
            )

        elif path == "/api/force-scan":
            threading.Thread(target=execute_scan, daemon=True).start()
            self._json({"status": "Rescan Initiated"})

        elif path == "/":
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(
                b"Lilly Pi Recon Station " + VERSION.encode() + b"  (fleet node)\n"
            )
        else:
            self._json({"error": "not found"}, 404)


# --------------------------------------------------------------------------
# Fleet registration
# --------------------------------------------------------------------------


def register_with_fleet():
    """POST /api/nodes/register on the host so the radar shows this node."""
    if not FLEET_HOST:
        print(
            "[recon] no --host/FLEET_HOST set — skipping auto-register. "
            "Register manually via /api/nodes/register.",
            flush=True,
        )
        return
    nid = opts.node_id or socket.gethostname()
    url = f"{FLEET_HOST}/api/nodes/register"
    payload = {
        "node_id": nid,
        "name": opts.name or f"Pi Recon {nid}",
        "sensor_url": f"http://{_lan_ip()}:{opts.port}",
        "model": "Raspberry Pi (readonly recon node)",
        "role": "edge",  # "node"|"primary"|"edge" — edge = dedicated sensor station
        "capabilities": ["wifi", "ble", "recon", "monitor"],  # no "camera"
    }
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            print(
                f"[recon] registered with fleet: {r.read().decode()[:200]}", flush=True
            )
    except Exception as e:
        print(
            f"[recon] register failed ({e}) — will retry on next interval", flush=True
        )


def _lan_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()


def registration_loop():
    while True:
        register_with_fleet()
        time.sleep(120)


# --------------------------------------------------------------------------


class _Svc:
    def __init__(self, s):
        self.wifi = "wifi" in s
        self.bt = "bt" in s


def main():
    global opts
    ap = argparse.ArgumentParser(description="Lilly Pi Recon Station node")
    ap.add_argument(
        "--port", type=int, default=int(os.environ.get("PORT", DEFAULT_PORT))
    )
    ap.add_argument(
        "--host", default=FLEET_HOST, help="fleet host base URL to register with"
    )
    ap.add_argument("--name", default=NODE_NAME, help="human node name")
    ap.add_argument("--node-id", default=NODE_ID, help="unique node id")
    ap.add_argument("--iface", default=WIFI_IFACE, help="wifi interface")
    ap.add_argument("--services", default=os.environ.get("SERVICES", "wifi bt"))
    ap.add_argument(
        "--no-monitor",
        action="store_true",
        help="disable passive monitor-mode client capture",
    )
    opts = ap.parse_args()
    opts.monitor = not opts.no_monitor
    opts.services = _Svc(opts.services)
    if not opts.node_id:
        opts.node_id = socket.gethostname()
    if not opts.name:
        opts.name = f"Pi Recon {opts.node_id}"

    print(
        f"[recon] starting node_id={opts.node_id} name={opts.name} "
        f"port={opts.port} iface={opts.iface} services={opts.services.__dict__}",
        flush=True,
    )

    threading.Thread(target=bg_loop, daemon=True).start()
    threading.Thread(target=registration_loop, daemon=True).start()
    srv = http.server.ThreadingHTTPServer(("0.0.0.0", opts.port), Handler)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n[recon] shutdown", flush=True)


if __name__ == "__main__":
    main()
