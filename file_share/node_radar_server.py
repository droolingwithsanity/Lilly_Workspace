#!/usr/bin/env python3
"""
Responsive Radar — Multi-Node Fleet Server
───────────────────────────────────────────
Runs on EACH Android phone (Termux) in the Lilly 3-node fleet. Provides:
  - A self-contained Leaflet radar dashboard at  /          (open in a browser)
  - /api/stream            → {location, wifi, bluetooth, ...} (single scan payload)
  - /api/force-scan        → trigger an immediate rescan
  - /api/native-settings   → open Android settings via Activity Manager
  - Standard "sensor server" endpoints so the HOST fleet poller (lilly_ai.py
    node_fleet_poller + node_registry) can aggregate all 3 nodes on the radar map:
      /health  /location  /wifi/scan  /bluetooth/scan  /battery  /sensors/all

Each phone runs:  python3 node_radar_server.py --port 8080
Then register it on the host with the node's Tailscale IP + :8080.
"""

import http.server
import socketserver
import subprocess
import json
import urllib.parse
import os
import math
import threading
import time
import argparse

PORT = 8080

# Human name for this node (shown on the host radar). Change per phone.
NODE_NAME = "Radio Node"
NODE_ID = os.environ.get("LILLY_NODE_ID", "phone-x")
VERSION = "1.0.0"


# Shared state store (updated by continuous background scanner)
scan_cache = {
    "location": {"latitude": 0.0, "longitude": 0.0, "accuracy": 0},
    "wifi": [],
    "bluetooth": [],
    "last_update": 0,
    "is_scanning": False,
}


def _sh(cmd, timeout=12):
    """Run a subprocess command, return stripped stdout ('' on failure)."""
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if res.returncode == 0 and res.stdout.strip():
            return res.stdout.strip()
    except Exception:
        pass
    return ""


def execute_single_scan():
    """Runs a complete scan cycle across location, Wi-Fi, and Bluetooth."""
    scan_cache["is_scanning"] = True

    # 1. Location
    out = _sh(["termux-location", "-p", "network"], timeout=10)
    if out:
        try:
            scan_cache["location"] = json.loads(out)
        except Exception:
            pass

    # 2. Wi-Fi
    out = _sh(["termux-wifi-scaninfo"], timeout=10)
    if out:
        try:
            data = json.loads(out)
            if isinstance(data, list):
                scan_cache["wifi"] = data
        except Exception:
            pass

    # 3. Bluetooth
    out = _sh(["termux-bluetooth-scan"], timeout=15)
    if out:
        try:
            data = json.loads(out)
            if isinstance(data, list):
                scan_cache["bluetooth"] = data
        except Exception:
            pass

    scan_cache["last_update"] = time.time()
    scan_cache["is_scanning"] = False


def bg_scanner():
    """Continuous background scanner thread."""
    while True:
        if not scan_cache["is_scanning"]:
            execute_single_scan()
        time.sleep(4)


# ── Normalize raw termux scan entries into host-friendly shapes ──────────
def wifi_entries():
    """[[{ssid,bssid,frequency,rssi,...}]] → list of network dicts (host shape)."""
    networks = []
    raw = scan_cache.get("wifi") or []
    for net in raw if isinstance(raw, list) else []:
        if not isinstance(net, dict):
            continue
        networks.append(
            {
                "ssid": net.get("ssid", ""),
                "bssid": net.get("bssid", ""),
                "frequency": int(net.get("frequency", 0) or 0),
                "band": (
                    "2.4GHz"
                    if 0 < (net.get("frequency") or 0) < 3000
                    else ("5GHz" if (net.get("frequency") or 0) >= 3000 else "unknown")
                ),
                "rssi": net.get("rssi", -100),
                "distance": net.get("distance"),
                "security": net.get("security", ""),
                "channel": net.get("channel", 0),
            }
        )
    return networks


def bt_entries():
    """[[{address,name,rssi,...}]] → list of device dicts."""
    devices = []
    raw = scan_cache.get("bluetooth") or []
    for b in raw if isinstance(raw, list) else []:
        if not isinstance(b, dict):
            continue
        devices.append(
            {
                "address": b.get("address", ""),
                "name": b.get("name", ""),
                "rssi": b.get("rssi", -75),
            }
        )
    return devices


class ResponsiveRadarHandler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"

        # ── Host fleet / sensor-server endpoints ────────────────────────
        if path == "/health":
            self.send_json_response(
                json.dumps(
                    {
                        "status": "ok",
                        "version": VERSION,
                        "node_id": NODE_ID,
                        "name": NODE_NAME,
                        "port": PORT,
                        "last_update": scan_cache["last_update"],
                        "is_scanning": scan_cache["is_scanning"],
                    }
                )
            )

        elif path == "/location":
            self.send_json_response(
                json.dumps(
                    {
                        "location": scan_cache["location"],
                        "timestamp": scan_cache["last_update"],
                    }
                )
            )

        elif path == "/wifi/scan":
            self.send_json_response(
                json.dumps(
                    {
                        "networks": wifi_entries(),
                        "count": len(wifi_entries()),
                        "timestamp": scan_cache["last_update"],
                    }
                )
            )

        elif path == "/bluetooth/scan":
            self.send_json_response(
                json.dumps(
                    {
                        "devices": bt_entries(),
                        "count": len(bt_entries()),
                        "timestamp": scan_cache["last_update"],
                    }
                )
            )

        elif path == "/battery":
            self.send_json_response(
                json.dumps(
                    {
                        "battery": {"level": -1, "charging": False, "status": ""},
                        "timestamp": scan_cache["last_update"],
                    }
                )
            )

        elif path == "/sensors/all":
            self.send_json_response(
                json.dumps(
                    {
                        "sensors": {
                            "location": scan_cache["location"] or {},
                            "wifi": wifi_entries(),
                            "bluetooth": bt_entries(),
                        },
                        "timestamp": scan_cache["last_update"],
                        "count": 3,
                    }
                )
            )

        # ── Original reactive radar endpoints ───────────────────────────
        elif path == "/api/stream":
            self.send_json_response(json.dumps(scan_cache))

        elif path == "/api/force-scan":
            if not scan_cache["is_scanning"]:
                threading.Thread(target=execute_single_scan, daemon=True).start()
            self.send_json_response(json.dumps({"status": "Rescan Initiated"}))

        elif path == "/api/native-settings":
            subprocess.run(
                ["am", "start", "-a", "android.settings.SETTINGS"], capture_output=True
            )
            self.send_json_response(json.dumps({"status": "Settings Opened via AM"}))

        elif path == "/":
            self.send_response(200)
            self.send_header("Content-type", "text/html")
            self.end_headers()
            self.wfile.write(self.get_html().encode("utf-8"))
        else:
            self.send_error(404, "Not Found")

    def send_json_response(self, data):
        self.send_response(200)
        self.send_header("Content-type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(data.encode("utf-8"))

    def get_html(self):
        return """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>Radar Dashboard</title>
    <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
    <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
    <style>
        :root {
            --bg-dark: #121212;
            --panel-bg: #1e1e1e;
            --card-bg: #2a2a2a;
            --primary: #007bff;
            --success: #28a745;
            --danger: #dc3545;
            --warning: #ff9800;
        }


        body {
            font-family: system-ui, -apple-system, sans-serif;
            margin: 0;
            padding: 0;
            background: var(--bg-dark);
            color: #eee;
            height: 100vh;
            display: flex;
            flex-direction: row;
            overflow: hidden;
        }


        /* Desktop Layout (Default) */
        #sidebar {
            width: 360px;
            background: var(--panel-bg);
            padding: 15px;
            box-sizing: border-box;
            display: flex;
            flex-direction: column;
            border-right: 1px solid #333;
            z-index: 1000;
            overflow-y: auto;
        }

        #map {
            flex: 1;
            height: 100%;
            width: 100%;
            background: #222;
        }


        h2 { margin: 0 0 10px 0; font-size: 1.2rem; color: #4CAF50; }


        .btn-group {
            display: flex;
            gap: 12px;
            margin-bottom: 12px;
        }


        button {
            flex: 1;
            padding: 10px;
            border: none;
            border-radius: 6px;
            font-weight: bold;
            color: white;
            cursor: pointer;
            font-size: 0.85rem;
            transition: opacity 0.2s;
        }
        button:active { opacity: 0.7; }
        .btn-rescan { background: var(--primary); }
        .btn-am { background: #6c757d; }


        .stat-box {
            background: var(--card-bg);
            padding: 10px;
            border-radius: 6px;
            margin-bottom: 10px;
            font-size: 0.85rem;
            border: 1px solid #333;
        }


        .feed-list { flex: 1; overflow-y: auto; }
        .device-item {
            background: var(--card-bg);
            padding: 8px 10px;
            border-radius: 4px;
            margin-bottom: 6px;
            font-size: 0.8rem;
            border-left: 3px solid var(--primary);
        }
        .device-item.tracker { border-left-color: var(--warning); background: #332718; }
        .badge {
            display: inline-block;
            padding: 2px 5px;
            border-radius: 3px;
            font-size: 0.7rem;
            font-weight: bold;
            background: #444;
            color: #fff;
        }
        .badge-tracker { background: var(--warning); color: #000; }


        /* Mobile Layout Adjustments */
        @media (max-width: 768px) {
            body {
                flex-direction: column-reverse; /* Map on top, control panel on bottom */
            }


            #sidebar {
                width: 100%;
                height: 48vh;
                border-right: none;
                border-top: 1px solid #333;
                padding: 12px;
            }


            #map {
                height: 52vh;
                width: 100%;
            }


            .btn-group button {
                padding: 12px 8px; /* Larger touch targets for mobile */
            }
        }
    </style>
</head>
<body>
    <div id="sidebar">
        <h2>🌐 Proximity Radar</h2>


        <div class="btn-group">
            <button class="btn-rescan" onclick="forceRescan()">⚡ Rescan Now</button>
            <button class="btn-am" onclick="openAMSettings()">📱 Open Settings (AM)</button>
        </div>


        <div class="stat-box">
            <div><strong>Status:</strong> <span id="status" style="color:#00bcd4">Connecting...</span></div>
            <div><strong>GPS Center:</strong> <span id="gpsPos">Acquiring...</span></div>
            <div><strong>Devices Tracked:</strong> <span id="devCount">0</span></div>
        </div>


        <h3 style="font-size: 0.95rem; margin: 5px 0 8px 0;">Active Devices Feed</h3>
        <div class="feed-list" id="feed"></div>
    </div>


    <div id="map"></div>


    <script>
        let map = L.map('map', { zoomControl: false }).setView([0, 0], 2);
        L.control.zoom({ position: 'topright' }).addTo(map);


        L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
            maxZoom: 19, attribution: '© OpenStreetMap'
        }).addTo(map);


        let userMarker = null;
        let deviceMarkers = {};
        let deviceHistory = {};
        let devicePolylines = {};


        let KNOWN_TRACKERS = ['tile', 'chipolo', 'pebblebee', 'smarttag', 'moto tag', 'airtag'];


        function hashToAngle(str) {
            let hash = 0;
            for (let i = 0; i < str.length; i++) hash = str.charCodeAt(i) + ((hash << 5) - hash);
            return (Math.abs(hash) % 360) * (Math.PI / 180);
        }


        function calculateOffset(lat, lng, distanceMeters, angleRad) {
            const earthRadius = 6378137;
            const dLat = (distanceMeters * Math.cos(angleRad)) / earthRadius;
            const dLng = (distanceMeters * Math.sin(angleRad)) / (earthRadius * Math.cos(Math.PI * lat / 180));
            return { lat: lat + (dLat * 180 / Math.PI), lng: lng + (dLng * 180 / Math.PI) };
        }


        function calculateDistance(rssi, freq) {
            let f = freq || 2412;
            let exp = (27.55 - (20 * Math.log10(f)) + Math.abs(rssi)) / 20.0;
            return Math.pow(10, exp);
        }


        async function pollData() {
            try {
                let res = await fetch('/api/stream');
                let data = await res.json();


                let statusElem = document.getElementById('status');
                if (data.is_scanning) {
                    statusElem.innerText = "Scanning in progress...";
                    statusElem.style.color = "#ff9800";
                } else {
                    statusElem.innerText = "Live Tracking Active";
                    statusElem.style.color = "#4CAF50";
                }


                let userLat = data.location.latitude || 0;
                let userLng = data.location.longitude || 0;


                if (userLat !== 0 && userLng !== 0) {
                    document.getElementById('gpsPos').innerText = `${userLat.toFixed(4)}, ${userLng.toFixed(4)}`;


                    if (!userMarker) {
                        map.setView([userLat, userLng], 18);
                        userMarker = L.circleMarker([userLat, userLng], {
                            radius: 8, fillColor: "#007bff", color: "#fff", weight: 2, opacity: 1, fillOpacity: 1
                        }).addTo(map).bindPopup("<b>Your Location</b>");
                    } else {
                        userMarker.setLatLng([userLat, userLng]);
                    }


                    let currentScanKeys = new Set();
                    let feedHtml = '';
                    let count = 0;


                    function processDevice(mac, name, isTracker, rssi, freq, color) {
                        let dist = calculateDistance(rssi, freq);
                        let angle = hashToAngle(mac);
                        let pos = calculateOffset(userLat, userLng, dist, angle);


                        currentScanKeys.add(mac);
                        count++;


                        if (!deviceHistory[mac]) deviceHistory[mac] = [];
                        let lastPos = deviceHistory[mac][deviceHistory[mac].length - 1];
                        if (!lastPos || Math.abs(lastPos[0] - pos.lat) > 0.00001 || Math.abs(lastPos[1] - pos.lng) > 0.00001) {
                            deviceHistory[mac].push([pos.lat, pos.lng]);
                        }


                        if (!devicePolylines[mac]) {
                            devicePolylines[mac] = L.polyline(deviceHistory[mac], {
                                color: color, weight: 3, opacity: 0.6, dashArray: '5, 5'
                            }).addTo(map);
                        } else {
                            devicePolylines[mac].setLatLngs(deviceHistory[mac]);
                        }


                        let pings = deviceHistory[mac].length;
                        updateOrCreateMarker(mac, pos.lat, pos.lng, color, `
                            <b>${isTracker ? '🏷️ Tracker' : '📱 Device'}</b><br>
                            Name: ${name}<br>MAC: ${mac}<br>Pings: ${pings}<br>Proximity: ~${dist.toFixed(1)}m
                        `);


                        feedHtml += `
                            <div class="device-item ${isTracker ? 'tracker' : ''}">
                                <strong>${name}</strong>
                                <span class="badge ${isTracker ? 'badge-tracker' : ''}">${isTracker ? 'Tracker' : 'Device'}</span><br>
                                Dist: ~${dist.toFixed(1)}m | Pings: ${pings}
                            </div>`;
                    }


                    if (Array.isArray(data.wifi)) {
                        data.wifi.forEach(w => processDevice(w.bssid, w.ssid || "Hidden Wi-Fi", false, w.rssi, w.frequency, "#00bcd4"));
                    }


                    if (Array.isArray(data.bluetooth)) {
                        data.bluetooth.forEach(b => {
                            let name = b.name || "Unknown BLE Tag";
                            let isTracker = KNOWN_TRACKERS.some(t => name.toLowerCase().includes(t)) || name === "Unknown BLE Tag";
                            processDevice(b.address, name, isTracker, b.rssi || -75, 2400, isTracker ? "#ff9800" : "#9c27b0");
                        });
                    }


                    document.getElementById('devCount').innerText = count;
                    document.getElementById('feed').innerHTML = feedHtml || "<p style='color:#aaa;'>Scanning for nearby devices...</p>";
                }
            } catch (e) {}
        }


        function updateOrCreateMarker(key, lat, lng, color, popupContent) {
            if (deviceMarkers[key]) {
                deviceMarkers[key].setLatLng([lat, lng]);
                deviceMarkers[key].getPopup().setContent(popupContent);
            } else {
                deviceMarkers[key] = L.circleMarker([lat, lng], {
                    radius: 7, fillColor: color, color: "#ffffff", weight: 2, opacity: 0.9, fillOpacity: 0.7
                }).addTo(map).bindPopup(popupContent);
            }
        }


        async function forceRescan() {
            document.getElementById('status').innerText = "Triggering manual rescan...";
            document.getElementById('status').style.color = "#ff9800";
            await fetch('/api/force-scan');
        }


        async function openAMSettings() {
            await fetch('/api/native-settings');
        }


        setInterval(pollData, 2500);
        pollData();
    </script>
</body>
</html>"""


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=PORT)
    parser.add_argument("--name", default=NODE_NAME)
    parser.add_argument("--node-id", default=NODE_ID)
    args = parser.parse_args()

    PORT = args.port
    NODE_NAME = args.name
    NODE_ID = args.node_id

    scanner_thread = threading.Thread(target=bg_scanner, daemon=True)
    scanner_thread.start()

    handler = ResponsiveRadarHandler
    with socketserver.TCPServer(("", PORT), handler) as httpd:
        print(f"Responsive Server running at http://localhost:{PORT}")
        print(f"Node: {NODE_NAME} (id={NODE_ID})")
        print(
            "Fleet endpoints: /health /location /wifi/scan /bluetooth/scan /battery /sensors/all"
        )
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            httpd.server_close()
