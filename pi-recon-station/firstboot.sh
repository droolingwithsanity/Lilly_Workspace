#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# firstboot.sh — Lilly Pi Recon Station (Bluehood + WiFi Probe + Dashboard)
# ─────────────────────────────────────────────────────────────────────────────
# Flash Raspberry Pi OS Lite (64-bit, Bookworm) to an SD card, drop this file
# at /boot/firstboot.sh (or inject via Raspberry Pi Imager → Advanced →
# "Run custom script"), and it will self-configure on first boot.
#
# What gets installed:
#   • Bluehood          — passive BLE + Classic BT neighbourhood scanner
#   • wifiprobe         — passive WiFi probe-request capture (monitor mode)
#                         + Wigle BSSID→GPS lookup, SQLite storage, REST API
#   • recon-dashboard   — unified single-pane-of-glass web UI (port 8080)
#   • Netdata           — lightweight system metrics (CPU/RAM/temp) on :19999
#   • Docker + Compose  — all services containerised
#   • BlueZ             — Bluetooth stack (required by Bluehood)
#   • aircrack-ng suite — for wlan monitor mode (airodump-ng / airmon-ng)
#
# Ports exposed on the Pi:
#   8080  → Recon Dashboard (unified UI, links to all services)
#   8081  → Bluehood web UI (BT neighbourhood)
#   8082  → WiFiProbe web UI (WiFi probe map)
#   19999 → Netdata (system metrics)
#
# Configuration — edit these or export before running:
#   WIFI_IFACE   — WiFi interface for monitor mode (default: wlan1 — USB dongle)
#   WIGLE_USER / WIGLE_TOKEN — optional, enables GPS lookup for SSIDs
#   TZ           — timezone (e.g. America/Toronto)
#   NTFY_TOPIC   — optional ntfy.sh topic for push notifications
#   LILLY_FLEET  — optional URL of your lilly_ai server for heartbeat
#
# Usage on a running Pi:
#   sudo bash firstboot.sh
#
# To build a flashable image with this baked in, use build_pi_recon_image.sh
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

WIFI_IFACE="${WIFI_IFACE:-wlan1}"   # USB WiFi dongle in monitor mode
TZ="${TZ:-America/Toronto}"
WIGLE_USER="${WIGLE_USER:-}"
WIGLE_TOKEN="${WIGLE_TOKEN:-}"
NTFY_TOPIC="${NTFY_TOPIC:-}"
LILLY_FLEET="${LILLY_FLEET:-}"
RECON_DIR="/opt/lilly-recon"
DATA_DIR="/data/recon"
MARK_FILE="${RECON_DIR}/.provisioned"

# ── Guard: only run once ─────────────────────────────────────────────────────
if [[ -f "$MARK_FILE" ]]; then
    echo "[firstboot] Already provisioned — skipping."
    exit 0
fi

log() { echo "[firstboot] $*"; }
die() { echo "[firstboot] FATAL: $*" >&2; exit 1; }

log "═══════════════════════════════════════════════════════"
log " Lilly Pi Recon Station — First Boot Provisioning"
log " $(date)"
log "═══════════════════════════════════════════════════════"

# ── 1. System basics ─────────────────────────────────────────────────────────
log "Setting timezone: $TZ"
timedatectl set-timezone "$TZ" 2>/dev/null || ln -sf "/usr/share/zoneinfo/$TZ" /etc/localtime

log "Updating package lists..."
apt-get update -qq

log "Installing core dependencies..."
apt-get install -y -qq \
    curl wget git python3 python3-pip python3-venv \
    bluez bluez-tools \
    aircrack-ng wireless-tools iw net-tools \
    sqlite3 libsqlite3-dev \
    tcpdump tshark \
    libpcap-dev \
    ca-certificates gnupg lsb-release \
    jq unzip pigz \
    2>/dev/null

# ── 2. Docker ────────────────────────────────────────────────────────────────
if ! command -v docker &>/dev/null; then
    log "Installing Docker..."
    curl -fsSL https://get.docker.com | bash
    usermod -aG docker pi 2>/dev/null || true
    systemctl enable --now docker
else
    log "Docker already installed."
fi

if ! command -v docker-compose &>/dev/null && ! docker compose version &>/dev/null 2>&1; then
    log "Installing Docker Compose plugin..."
    apt-get install -y -qq docker-compose-plugin 2>/dev/null || \
        pip3 install --quiet docker-compose
fi

# ── 3. Bluetooth stack ───────────────────────────────────────────────────────
log "Enabling Bluetooth service..."
systemctl enable --now bluetooth || true

# ── 4. WiFi monitor mode setup ───────────────────────────────────────────────
log "Checking WiFi interfaces..."
# Prevent NetworkManager from grabbing wlan1 (monitor interface)
NM_CONF="/etc/NetworkManager/conf.d/99-recon-unmanaged.conf"
mkdir -p "$(dirname "$NM_CONF")"
cat > "$NM_CONF" <<EOF
[keyfile]
unmanaged-devices=interface-name:${WIFI_IFACE}
EOF

# Disable wpa_supplicant on monitor interface
cat > "/etc/systemd/system/wifimonitor.service" <<EOF
[Unit]
Description=Put ${WIFI_IFACE} into monitor mode
After=network.target
Before=docker.service

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStartPre=/bin/bash -c 'ip link show ${WIFI_IFACE} || exit 0'
ExecStart=/bin/bash -c '\
    ip link set ${WIFI_IFACE} down 2>/dev/null || true; \
    iw dev ${WIFI_IFACE} set type monitor 2>/dev/null || \
    iwconfig ${WIFI_IFACE} mode monitor 2>/dev/null || true; \
    ip link set ${WIFI_IFACE} up 2>/dev/null || true; \
    echo "Monitor mode set on ${WIFI_IFACE}"'
ExecStop=/bin/bash -c '\
    ip link set ${WIFI_IFACE} down 2>/dev/null || true; \
    iw dev ${WIFI_IFACE} set type managed 2>/dev/null || true; \
    ip link set ${WIFI_IFACE} up 2>/dev/null || true'
Restart=on-failure

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable wifimonitor.service

# ── 5. Create directory structure ────────────────────────────────────────────
log "Creating data directories..."
mkdir -p "${RECON_DIR}"
mkdir -p "${DATA_DIR}/bluehood"
mkdir -p "${DATA_DIR}/wifiprobe"
mkdir -p "${DATA_DIR}/dashboard"

# ── 6. Write wifiprobe service ───────────────────────────────────────────────
log "Writing WiFiProbe service..."
mkdir -p "${RECON_DIR}/wifiprobe"

cat > "${RECON_DIR}/wifiprobe/requirements.txt" <<'PYREQ'
scapy==2.5.0
flask==3.0.3
flask-cors==4.0.1
requests==2.32.3
wigle==1.0.4
PYREQ

cat > "${RECON_DIR}/wifiprobe/wifiprobe.py" <<'PYEOF'
#!/usr/bin/env python3
"""
wifiprobe.py — Passive WiFi probe request capture + Wigle BSSID→GPS lookup.

Captures 802.11 probe request frames from nearby devices (phones, laptops etc)
using a WiFi adapter in monitor mode. Stores results in SQLite, exposes a
REST API and web UI on port 8082.

Each probe request reveals:
  - The probing device's MAC address
  - The SSID (network name) it's looking for
  - Signal strength (RSSI)
  - Timestamp

If Wigle credentials are configured, BSSIDs from beacons are cross-referenced
to estimate approximate GPS coordinates.
"""
import os
import json
import time
import threading
import sqlite3
import logging
import requests
from datetime import datetime, timezone
from collections import defaultdict
from flask import Flask, jsonify, render_template_string, request
from flask_cors import CORS

try:
    from scapy.all import sniff, Dot11, Dot11ProbeReq, Dot11Beacon, Dot11Elt, RadioTap
    SCAPY_OK = True
except ImportError:
    SCAPY_OK = False
    logging.warning("scapy not available — probe capture disabled")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [wifiprobe] %(message)s")
log = logging.getLogger(__name__)

IFACE        = os.environ.get("WIFI_IFACE", "wlan1")
DB_PATH      = os.environ.get("DB_PATH", "/data/wifiprobe.db")
WIGLE_USER   = os.environ.get("WIGLE_USER", "")
WIGLE_TOKEN  = os.environ.get("WIGLE_TOKEN", "")
PORT         = int(os.environ.get("PORT", "8082"))

app = Flask(__name__)
CORS(app)

# ── Database ──────────────────────────────────────────────────────────────────

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_db() as db:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS probes (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                ts        REAL NOT NULL,
                mac       TEXT NOT NULL,
                ssid      TEXT,
                rssi      INTEGER,
                iface     TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_probes_mac ON probes(mac);
            CREATE INDEX IF NOT EXISTS idx_probes_ts  ON probes(ts);

            CREATE TABLE IF NOT EXISTS beacons (
                bssid     TEXT PRIMARY KEY,
                ssid      TEXT,
                channel   INTEGER,
                rssi      INTEGER,
                first_seen REAL,
                last_seen  REAL,
                lat        REAL,
                lng        REAL,
                wigle_queried INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS devices (
                mac         TEXT PRIMARY KEY,
                first_seen  REAL,
                last_seen   REAL,
                probe_count INTEGER DEFAULT 0,
                ssids_seen  TEXT DEFAULT '[]',
                vendor      TEXT,
                label       TEXT
            );
        """)
    log.info("Database initialised at %s", DB_PATH)

# ── Scapy packet handler ──────────────────────────────────────────────────────

def packet_handler(pkt):
    try:
        ts = time.time()
        rssi = None
        if pkt.haslayer(RadioTap):
            try:
                rssi = -(256 - pkt[RadioTap].dBm_AntSignal) if hasattr(pkt[RadioTap], 'dBm_AntSignal') else None
            except Exception:
                pass

        # Probe requests (device looking for a known network)
        if pkt.haslayer(Dot11ProbeReq):
            mac  = pkt[Dot11].addr2
            ssid = ""
            if pkt.haslayer(Dot11Elt):
                try:
                    ssid = pkt[Dot11Elt].info.decode("utf-8", errors="replace").strip()
                except Exception:
                    pass
            if mac and mac != "ff:ff:ff:ff:ff:ff":
                store_probe(mac, ssid, rssi, ts)

        # Beacons (access points advertising themselves)
        elif pkt.haslayer(Dot11Beacon):
            bssid = pkt[Dot11].addr3
            ssid  = ""
            channel = 0
            if pkt.haslayer(Dot11Elt):
                try:
                    ssid = pkt[Dot11Elt].info.decode("utf-8", errors="replace").strip()
                except Exception:
                    pass
            if bssid and bssid != "ff:ff:ff:ff:ff:ff":
                store_beacon(bssid, ssid, channel, rssi, ts)

    except Exception as e:
        log.debug("Packet handler error: %s", e)


def store_probe(mac, ssid, rssi, ts):
    try:
        with get_db() as db:
            db.execute(
                "INSERT INTO probes(ts,mac,ssid,rssi,iface) VALUES(?,?,?,?,?)",
                (ts, mac, ssid, rssi, IFACE)
            )
            # Upsert device
            row = db.execute("SELECT ssids_seen, probe_count FROM devices WHERE mac=?", (mac,)).fetchone()
            if row:
                ssids = json.loads(row["ssids_seen"] or "[]")
                if ssid and ssid not in ssids:
                    ssids.append(ssid)
                db.execute(
                    "UPDATE devices SET last_seen=?, probe_count=probe_count+1, ssids_seen=? WHERE mac=?",
                    (ts, json.dumps(ssids), mac)
                )
            else:
                ssids = [ssid] if ssid else []
                db.execute(
                    "INSERT INTO devices(mac,first_seen,last_seen,probe_count,ssids_seen) VALUES(?,?,?,1,?)",
                    (mac, ts, ts, json.dumps(ssids))
                )
    except Exception as e:
        log.debug("store_probe error: %s", e)


def store_beacon(bssid, ssid, channel, rssi, ts):
    try:
        with get_db() as db:
            row = db.execute("SELECT bssid FROM beacons WHERE bssid=?", (bssid,)).fetchone()
            if row:
                db.execute("UPDATE beacons SET last_seen=?,rssi=? WHERE bssid=?", (ts, rssi, bssid))
            else:
                db.execute(
                    "INSERT INTO beacons(bssid,ssid,channel,rssi,first_seen,last_seen) VALUES(?,?,?,?,?,?)",
                    (bssid, ssid, channel, rssi, ts, ts)
                )
    except Exception as e:
        log.debug("store_beacon error: %s", e)

# ── Wigle lookup thread ───────────────────────────────────────────────────────

def wigle_lookup_loop():
    """Periodically look up unresolved BSSIDs against Wigle for GPS coords."""
    if not WIGLE_USER or not WIGLE_TOKEN:
        log.info("Wigle not configured — GPS lookup disabled")
        return
    log.info("Wigle lookup thread started")
    while True:
        try:
            with get_db() as db:
                rows = db.execute(
                    "SELECT bssid FROM beacons WHERE wigle_queried=0 LIMIT 5"
                ).fetchall()
            for row in rows:
                bssid = row["bssid"]
                try:
                    resp = requests.get(
                        "https://api.wigle.net/api/v2/network/search",
                        params={"netid": bssid, "resultsPerPage": 1},
                        auth=(WIGLE_USER, WIGLE_TOKEN),
                        timeout=10
                    )
                    data = resp.json()
                    lat, lng = 0.0, 0.0
                    if data.get("success") and data.get("results"):
                        r = data["results"][0]
                        lat = r.get("trilat", 0.0)
                        lng = r.get("trilong", 0.0)
                    with get_db() as db:
                        db.execute(
                            "UPDATE beacons SET lat=?,lng=?,wigle_queried=1 WHERE bssid=?",
                            (lat, lng, bssid)
                        )
                    log.info("Wigle: %s → %.5f, %.5f", bssid, lat, lng)
                    time.sleep(1.5)  # rate limit: ~100 lookups/day free tier
                except Exception as e:
                    log.debug("Wigle lookup failed for %s: %s", bssid, e)
                    with get_db() as db:
                        db.execute("UPDATE beacons SET wigle_queried=1 WHERE bssid=?", (bssid,))
        except Exception as e:
            log.debug("wigle_lookup_loop error: %s", e)
        time.sleep(60)

# ── Capture thread ────────────────────────────────────────────────────────────

def capture_loop():
    if not SCAPY_OK:
        log.warning("Scapy unavailable — not capturing")
        return
    log.info("Starting capture on %s", IFACE)
    while True:
        try:
            sniff(
                iface=IFACE,
                prn=packet_handler,
                store=False,
                filter="type mgt subtype probe-req or type mgt subtype beacon",
                timeout=30
            )
        except Exception as e:
            log.warning("Capture error: %s — retrying in 5s", e)
            time.sleep(5)

# ── REST API ──────────────────────────────────────────────────────────────────

@app.route("/api/stats")
def api_stats():
    with get_db() as db:
        total_devices = db.execute("SELECT COUNT(*) FROM devices").fetchone()[0]
        total_probes  = db.execute("SELECT COUNT(*) FROM probes").fetchone()[0]
        total_aps     = db.execute("SELECT COUNT(*) FROM beacons").fetchone()[0]
        active_5m     = db.execute(
            "SELECT COUNT(DISTINCT mac) FROM probes WHERE ts > ?", (time.time()-300,)
        ).fetchone()[0]
    return jsonify({
        "total_devices": total_devices,
        "total_probes": total_probes,
        "total_aps": total_aps,
        "active_last_5m": active_5m,
        "iface": IFACE,
        "wigle_enabled": bool(WIGLE_USER and WIGLE_TOKEN)
    })

@app.route("/api/devices")
def api_devices():
    limit  = int(request.args.get("limit", 100))
    offset = int(request.args.get("offset", 0))
    with get_db() as db:
        rows = db.execute(
            "SELECT * FROM devices ORDER BY last_seen DESC LIMIT ? OFFSET ?",
            (limit, offset)
        ).fetchall()
    return jsonify([dict(r) for r in rows])

@app.route("/api/probes")
def api_probes():
    limit = int(request.args.get("limit", 200))
    mac   = request.args.get("mac")
    with get_db() as db:
        if mac:
            rows = db.execute(
                "SELECT * FROM probes WHERE mac=? ORDER BY ts DESC LIMIT ?", (mac, limit)
            ).fetchall()
        else:
            rows = db.execute(
                "SELECT * FROM probes ORDER BY ts DESC LIMIT ?", (limit,)
            ).fetchall()
    return jsonify([dict(r) for r in rows])

@app.route("/api/aps")
def api_aps():
    with get_db() as db:
        rows = db.execute(
            "SELECT * FROM beacons ORDER BY last_seen DESC"
        ).fetchall()
    return jsonify([dict(r) for r in rows])

@app.route("/api/map")
def api_map():
    """APs with GPS coords — for the map overlay."""
    with get_db() as db:
        rows = db.execute(
            "SELECT bssid,ssid,lat,lng,rssi,last_seen FROM beacons WHERE lat!=0 AND lng!=0"
        ).fetchall()
    return jsonify([dict(r) for r in rows])

@app.route("/api/device/<mac>/label", methods=["POST"])
def label_device(mac):
    label = request.json.get("label", "")
    with get_db() as db:
        db.execute("UPDATE devices SET label=? WHERE mac=?", (label, mac))
    return jsonify({"ok": True})

@app.route("/health")
def health():
    return jsonify({"status": "ok", "service": "wifiprobe", "iface": IFACE})

# ── Minimal web UI (served at /) ──────────────────────────────────────────────
UI_HTML = """<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>WiFi Probe Monitor</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:system-ui,sans-serif;background:#0d1117;color:#e6edf3;min-height:100vh}
header{background:#161b22;border-bottom:1px solid #30363d;padding:12px 20px;display:flex;align-items:center;gap:12px}
h1{font-size:1.1rem;color:#58a6ff}
.badge{background:#21262d;border:1px solid #30363d;border-radius:20px;padding:3px 10px;font-size:.72rem;color:#8b949e}
.grid{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;padding:16px}
.card{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:14px}
.card h3{font-size:.7rem;color:#8b949e;text-transform:uppercase;margin-bottom:6px}
.card .val{font-size:1.8rem;font-weight:700;color:#e6edf3}
.card .sub{font-size:.68rem;color:#8b949e;margin-top:3px}
table{width:100%;border-collapse:collapse;font-size:.78rem}
th{text-align:left;padding:8px 10px;background:#161b22;color:#8b949e;border-bottom:1px solid #30363d;position:sticky;top:0}
td{padding:7px 10px;border-bottom:1px solid #21262d;font-family:monospace}
.tbl-wrap{background:#161b22;border:1px solid #30363d;border-radius:8px;overflow:hidden;margin:0 16px 16px}
.rssi-good{color:#3fb950}.rssi-ok{color:#d29922}.rssi-weak{color:#f85149}
.tabs{display:flex;gap:4px;padding:0 16px 12px}
.tab{padding:6px 14px;border-radius:6px;cursor:pointer;font-size:.8rem;background:#21262d;border:1px solid #30363d;color:#8b949e}
.tab.active{background:#1f6feb;border-color:#1f6feb;color:#fff}
</style>
</head>
<body>
<header>
  <h1>📡 WiFi Probe Monitor</h1>
  <span class="badge" id="statusBadge">Loading…</span>
</header>
<div class="grid">
  <div class="card"><h3>Unique Devices</h3><div class="val" id="totalDevices">—</div><div class="sub">all time</div></div>
  <div class="card"><h3>Active (5m)</h3><div class="val" id="active5m">—</div><div class="sub">probing now</div></div>
  <div class="card"><h3>Access Points</h3><div class="val" id="totalAPs">—</div><div class="sub">beacons seen</div></div>
  <div class="card"><h3>Total Probes</h3><div class="val" id="totalProbes">—</div><div class="sub">probe requests logged</div></div>
</div>
<div class="tabs">
  <div class="tab active" onclick="showTab('devices')">Devices</div>
  <div class="tab" onclick="showTab('probes')">Probe Log</div>
  <div class="tab" onclick="showTab('aps')">Access Points</div>
</div>
<div class="tbl-wrap">
<div id="tab-devices">
<table><thead><tr><th>MAC</th><th>Label</th><th>SSIDs Probed</th><th>Probes</th><th>Last Seen</th></tr></thead>
<tbody id="deviceBody"><tr><td colspan="5" style="color:#8b949e;text-align:center;padding:20px">Loading…</td></tr></tbody></table>
</div>
<div id="tab-probes" style="display:none">
<table><thead><tr><th>Time</th><th>MAC</th><th>SSID</th><th>RSSI</th></tr></thead>
<tbody id="probeBody"></tbody></table>
</div>
<div id="tab-aps" style="display:none">
<table><thead><tr><th>BSSID</th><th>SSID</th><th>RSSI</th><th>GPS</th><th>Last Seen</th></tr></thead>
<tbody id="apBody"></tbody></table>
</div>
</div>
<script>
let currentTab='devices';
function showTab(t){
  currentTab=t;
  document.querySelectorAll('.tab').forEach((el,i)=>{
    const tabs=['devices','probes','aps'];
    el.classList.toggle('active',tabs[i]===t);
  });
  ['devices','probes','aps'].forEach(n=>{
    document.getElementById('tab-'+n).style.display=n===t?'block':'none';
  });
}
function rssiClass(r){return r>-60?'rssi-good':r>-75?'rssi-ok':'rssi-weak';}
function ts(t){return new Date(t*1000).toLocaleTimeString();}
async function loadStats(){
  const s=await fetch('/api/stats').then(r=>r.json()).catch(()=>({}));
  document.getElementById('totalDevices').textContent=s.total_devices??'—';
  document.getElementById('active5m').textContent=s.active_last_5m??'—';
  document.getElementById('totalAPs').textContent=s.total_aps??'—';
  document.getElementById('totalProbes').textContent=s.total_probes??'—';
  document.getElementById('statusBadge').textContent='🟢 '+s.iface+(s.wigle_enabled?' + Wigle':'');
}
async function loadDevices(){
  const d=await fetch('/api/devices?limit=100').then(r=>r.json()).catch(()=>[]);
  document.getElementById('deviceBody').innerHTML=d.map(r=>`
    <tr>
      <td>${r.mac}</td>
      <td>${r.label||'<span style="color:#8b949e">—</span>'}</td>
      <td style="max-width:200px;overflow:hidden;text-overflow:ellipsis">${(JSON.parse(r.ssids_seen||'[]')).filter(Boolean).join(', ')||'<span style="color:#8b949e">(hidden)</span>'}</td>
      <td>${r.probe_count}</td>
      <td>${ts(r.last_seen)}</td>
    </tr>`).join('');
}
async function loadProbes(){
  const d=await fetch('/api/probes?limit=100').then(r=>r.json()).catch(()=>[]);
  document.getElementById('probeBody').innerHTML=d.map(r=>`
    <tr>
      <td>${ts(r.ts)}</td>
      <td>${r.mac}</td>
      <td>${r.ssid||'<span style="color:#8b949e">(broadcast)</span>'}</td>
      <td class="${rssiClass(r.rssi||0)}">${r.rssi||'—'} dBm</td>
    </tr>`).join('');
}
async function loadAPs(){
  const d=await fetch('/api/aps').then(r=>r.json()).catch(()=>[]);
  document.getElementById('apBody').innerHTML=d.map(r=>`
    <tr>
      <td>${r.bssid}</td>
      <td>${r.ssid||'<span style="color:#8b949e">(hidden)</span>'}</td>
      <td class="${rssiClass(r.rssi||0)}">${r.rssi||'—'} dBm</td>
      <td>${r.lat&&r.lat!=0?`${parseFloat(r.lat).toFixed(4)}, ${parseFloat(r.lng).toFixed(4)}`:'<span style="color:#8b949e">—</span>'}</td>
      <td>${ts(r.last_seen)}</td>
    </tr>`).join('');
}
async function tick(){
  await loadStats();
  if(currentTab==='devices') await loadDevices();
  else if(currentTab==='probes') await loadProbes();
  else if(currentTab==='aps') await loadAPs();
}
tick(); setInterval(tick,5000);
</script>
</body></html>"""

@app.route("/")
def ui():
    return UI_HTML

# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    init_db()
    # Start capture thread
    t = threading.Thread(target=capture_loop, daemon=True)
    t.start()
    # Start Wigle lookup thread
    w = threading.Thread(target=wigle_lookup_loop, daemon=True)
    w.start()
    log.info("WiFiProbe starting on port %s (iface: %s)", PORT, IFACE)
    app.run(host="0.0.0.0", port=PORT, threaded=True)
PYEOF

# ── 7. Write docker-compose.yml ───────────────────────────────────────────────
log "Writing docker-compose.yml..."
cat > "${RECON_DIR}/docker-compose.yml" <<COMPOSE
version: "3.9"

services:

  # ── Bluehood: passive BLE + Classic BT scanner ───────────────────────────
  bluehood:
    image: ghcr.io/dannymcc/bluehood:latest
    container_name: bluehood
    restart: unless-stopped
    network_mode: host
    privileged: true
    volumes:
      - ${DATA_DIR}/bluehood:/data
    environment:
      - TZ=${TZ}
      - BLUEHOOD_DATA_DIR=/data
      - BLUEHOOD_METRICS_PORT=9199
      - BLUEHOOD_HEARTBEAT_URL=${LILLY_FLEET:+${LILLY_FLEET}/api/nodes/bluehood/heartbeat}
      - BLUEHOOD_HEARTBEAT_INTERVAL=60
      - BLUEHOOD_PRUNE_DAYS=30
      - NTFY_TOPIC=${NTFY_TOPIC}
    ports:
      - "8081:8080"
      - "9199:9199"

  # ── WiFiProbe: passive probe request capture + Wigle GPS ─────────────────
  wifiprobe:
    build:
      context: ${RECON_DIR}/wifiprobe
      dockerfile: Dockerfile
    container_name: wifiprobe
    restart: unless-stopped
    network_mode: host
    cap_add:
      - NET_ADMIN
      - NET_RAW
    volumes:
      - ${DATA_DIR}/wifiprobe:/data
    environment:
      - WIFI_IFACE=${WIFI_IFACE}
      - DB_PATH=/data/wifiprobe.db
      - WIGLE_USER=${WIGLE_USER}
      - WIGLE_TOKEN=${WIGLE_TOKEN}
      - PORT=8082
      - TZ=${TZ}
    ports:
      - "8082:8082"
    depends_on:
      - bluehood

  # ── Recon Dashboard: unified single-pane UI ──────────────────────────────
  dashboard:
    image: nginx:alpine
    container_name: recon-dashboard
    restart: unless-stopped
    ports:
      - "8080:80"
    volumes:
      - ${DATA_DIR}/dashboard:/usr/share/nginx/html:ro
      - ${RECON_DIR}/nginx-dashboard.conf:/etc/nginx/conf.d/default.conf:ro

  # ── Netdata: lightweight system metrics (<2% CPU on Pi 4) ────────────────
  netdata:
    image: netdata/netdata:stable
    container_name: netdata
    restart: unless-stopped
    pid: host
    network_mode: host
    cap_add:
      - SYS_PTRACE
      - SYS_ADMIN
    security_opt:
      - apparmor:unconfined
    volumes:
      - /proc:/host/proc:ro
      - /sys:/host/sys:ro
      - /etc/passwd:/host/etc/passwd:ro
      - /etc/group:/host/etc/group:ro
      - /etc/os-release:/host/etc/os-release:ro
      - /var/run/docker.sock:/var/run/docker.sock:ro
    environment:
      - NETDATA_CLAIM_TOKEN=
      - NETDATA_CLAIM_URL=
      - DOCKER_USR=root
    ports:
      - "19999:19999"
COMPOSE

# ── 8. Write WiFiProbe Dockerfile ─────────────────────────────────────────────
cat > "${RECON_DIR}/wifiprobe/Dockerfile" <<'DOCKERFILE'
FROM python:3.11-slim-bookworm

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpcap-dev gcc iw wireless-tools \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY wifiprobe.py .

CMD ["python", "wifiprobe.py"]
DOCKERFILE

# ── 9. Write nginx config for dashboard ──────────────────────────────────────
cat > "${RECON_DIR}/nginx-dashboard.conf" <<'NGINX'
server {
    listen 80;
    root /usr/share/nginx/html;
    index index.html;

    location / {
        try_files $uri $uri/ /index.html;
    }

    # Proxy Bluehood API
    location /bluehood/ {
        proxy_pass http://localhost:8081/;
        proxy_set_header Host $host;
    }

    # Proxy WiFiProbe API
    location /wifiprobe/ {
        proxy_pass http://localhost:8082/;
        proxy_set_header Host $host;
    }

    # Proxy Netdata
    location /netdata/ {
        proxy_pass http://localhost:19999/;
        proxy_set_header Host $host;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
    }
}
NGINX

# ── 10. Write the unified dashboard HTML ─────────────────────────────────────
log "Writing unified dashboard HTML..."
mkdir -p "${DATA_DIR}/dashboard"
PI_IP=$(hostname -I | awk '{print $1}' 2>/dev/null || echo "localhost")

cat > "${DATA_DIR}/dashboard/index.html" <<DASHHTML
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Lilly Recon Station</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:system-ui,-apple-system,sans-serif;background:#0d1117;color:#e6edf3;min-height:100vh}
header{background:#161b22;border-bottom:1px solid #30363d;padding:14px 20px;display:flex;align-items:center;justify-content:space-between}
.logo{display:flex;align-items:center;gap:10px}
.logo h1{font-size:1.1rem;color:#58a6ff;font-weight:700}
.logo span{font-size:.72rem;color:#8b949e;background:#21262d;padding:2px 8px;border-radius:10px}
.timestamp{font-size:.7rem;color:#8b949e}
.service-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:16px;padding:20px}
.service-card{background:#161b22;border:1px solid #30363d;border-radius:10px;overflow:hidden;transition:border-color .2s}
.service-card:hover{border-color:#58a6ff}
.service-header{padding:14px 16px;display:flex;align-items:center;justify-content:space-between;border-bottom:1px solid #21262d}
.service-title{display:flex;align-items:center;gap:8px;font-weight:600;font-size:.9rem}
.service-icon{font-size:1.2rem}
.status-dot{width:8px;height:8px;border-radius:50%;background:#3fb950}
.status-dot.offline{background:#f85149}
.status-dot.checking{background:#d29922}
.stats{padding:12px 16px;display:grid;grid-template-columns:1fr 1fr;gap:8px}
.stat{text-align:center}
.stat-val{font-size:1.4rem;font-weight:700}
.stat-lbl{font-size:.62rem;color:#8b949e;text-transform:uppercase;margin-top:2px}
.actions{padding:10px 16px;display:flex;gap:8px;border-top:1px solid #21262d}
.btn{flex:1;padding:7px;border:1px solid #30363d;border-radius:6px;background:#21262d;color:#e6edf3;font-size:.75rem;cursor:pointer;text-align:center;text-decoration:none;transition:background .15s}
.btn:hover{background:#30363d}
.btn.primary{background:#1f6feb;border-color:#1f6feb;color:#fff}
.btn.primary:hover{background:#388bfd}
.sysbar{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;padding:0 20px 20px}
.syscard{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:12px;text-align:center}
.syscard .val{font-size:1.6rem;font-weight:700;color:#58a6ff}
.syscard .lbl{font-size:.65rem;color:#8b949e;margin-top:4px;text-transform:uppercase}
.feed-section{margin:0 20px 20px}
.feed-section h2{font-size:.8rem;color:#8b949e;text-transform:uppercase;margin-bottom:10px;letter-spacing:.5px}
.feed{background:#161b22;border:1px solid #30363d;border-radius:8px;max-height:220px;overflow-y:auto}
.feed-item{padding:8px 12px;border-bottom:1px solid #21262d;font-size:.75rem;display:flex;align-items:center;gap:8px;font-family:monospace}
.feed-item:last-child{border-bottom:none}
.feed-item .time{color:#8b949e;min-width:60px}
.feed-item .mac{color:#58a6ff}
.feed-item .ssid{color:#3fb950}
</style>
</head>
<body>
<header>
  <div class="logo">
    <h1>🛰️ Lilly Recon Station</h1>
    <span id="hostname">${PI_IP}</span>
  </div>
  <div class="timestamp" id="clock">—</div>
</header>

<div class="sysbar">
  <div class="syscard"><div class="val" id="cpuVal">—</div><div class="lbl">CPU %</div></div>
  <div class="syscard"><div class="val" id="ramVal">—</div><div class="lbl">RAM %</div></div>
  <div class="syscard"><div class="val" id="tempVal">—</div><div class="lbl">°C</div></div>
  <div class="syscard"><div class="val" id="uptimeVal">—</div><div class="lbl">Uptime</div></div>
</div>

<div class="service-grid">

  <!-- Bluehood -->
  <div class="service-card">
    <div class="service-header">
      <div class="service-title">
        <span class="service-icon">🔵</span> Bluehood
      </div>
      <div class="status-dot checking" id="bh-dot"></div>
    </div>
    <div class="stats">
      <div class="stat"><div class="stat-val" id="bh-devices">—</div><div class="stat-lbl">BT Devices</div></div>
      <div class="stat"><div class="stat-val" id="bh-active">—</div><div class="stat-lbl">Active Now</div></div>
    </div>
    <div class="actions">
      <a class="btn primary" href="http://${PI_IP}:8081" target="_blank">Open UI</a>
      <a class="btn" href="http://${PI_IP}:8081/api/v1/devices" target="_blank">API</a>
    </div>
  </div>

  <!-- WiFiProbe -->
  <div class="service-card">
    <div class="service-header">
      <div class="service-title">
        <span class="service-icon">📡</span> WiFi Probe
      </div>
      <div class="status-dot checking" id="wp-dot"></div>
    </div>
    <div class="stats">
      <div class="stat"><div class="stat-val" id="wp-devices">—</div><div class="stat-lbl">Devices Seen</div></div>
      <div class="stat"><div class="stat-val" id="wp-active">—</div><div class="stat-lbl">Active (5m)</div></div>
    </div>
    <div class="actions">
      <a class="btn primary" href="http://${PI_IP}:8082" target="_blank">Open UI</a>
      <a class="btn" href="http://${PI_IP}:8082/api/map" target="_blank">GPS Map</a>
    </div>
  </div>

  <!-- Netdata -->
  <div class="service-card">
    <div class="service-header">
      <div class="service-title">
        <span class="service-icon">📊</span> System Monitor
      </div>
      <div class="status-dot checking" id="nd-dot"></div>
    </div>
    <div class="stats">
      <div class="stat"><div class="stat-val" id="nd-cpu">—</div><div class="stat-lbl">CPU %</div></div>
      <div class="stat"><div class="stat-val" id="nd-temp">—</div><div class="stat-lbl">Temp °C</div></div>
    </div>
    <div class="actions">
      <a class="btn primary" href="http://${PI_IP}:19999" target="_blank">Netdata</a>
    </div>
  </div>

</div>

<!-- Live probe feed -->
<div class="feed-section">
  <h2>📶 Live WiFi Probe Feed</h2>
  <div class="feed" id="probeFeed">
    <div class="feed-item"><span style="color:#8b949e">Waiting for probes…</span></div>
  </div>
</div>

<script>
const PI = location.hostname;
function ts(t){return new Date(t*1000).toLocaleTimeString();}

// Clock
setInterval(()=>{ document.getElementById('clock').textContent=new Date().toLocaleString(); },1000);
document.getElementById('clock').textContent=new Date().toLocaleString();

// Bluehood stats
async function pollBluehood(){
  try{
    const r=await fetch('http://'+PI+':8081/api/v1/devices?page_size=1',{signal:AbortSignal.timeout(3000)});
    if(r.ok){
      const d=await r.json();
      document.getElementById('bh-devices').textContent=d.total??'—';
      document.getElementById('bh-active').textContent=d.active??'—';
      document.getElementById('bh-dot').className='status-dot';
    } else throw new Error();
  } catch {
    document.getElementById('bh-dot').className='status-dot offline';
  }
}

// WiFiProbe stats
async function pollWifiProbe(){
  try{
    const r=await fetch('http://'+PI+':8082/api/stats',{signal:AbortSignal.timeout(3000)});
    if(r.ok){
      const d=await r.json();
      document.getElementById('wp-devices').textContent=d.total_devices??'—';
      document.getElementById('wp-active').textContent=d.active_last_5m??'—';
      document.getElementById('wp-dot').className='status-dot';
    } else throw new Error();
  } catch {
    document.getElementById('wp-dot').className='status-dot offline';
  }
}

// Netdata CPU
async function pollNetdata(){
  try{
    const r=await fetch('http://'+PI+':19999/api/v1/data?chart=system.cpu&after=-5&format=json',{signal:AbortSignal.timeout(3000)});
    if(r.ok){
      const d=await r.json();
      const cpu=d.data?.[0]?.[1];
      if(cpu!=null){
        document.getElementById('nd-cpu').textContent=Math.round(cpu)+'%';
        document.getElementById('cpuVal').textContent=Math.round(cpu)+'%';
      }
      document.getElementById('nd-dot').className='status-dot';
    } else throw new Error();
  } catch {
    document.getElementById('nd-dot').className='status-dot offline';
  }
}

// Probe feed
async function pollProbeFeed(){
  try{
    const r=await fetch('http://'+PI+':8082/api/probes?limit=20',{signal:AbortSignal.timeout(3000)});
    if(r.ok){
      const items=await r.json();
      if(items.length){
        document.getElementById('probeFeed').innerHTML=items.map(p=>\`
          <div class="feed-item">
            <span class="time">\${ts(p.ts)}</span>
            <span class="mac">\${p.mac}</span>
            <span class="ssid">\${p.ssid||'(broadcast)'}</span>
            <span style="color:#8b949e">\${p.rssi||''}dBm</span>
          </div>\`).join('');
      }
    }
  } catch {}
}

async function tick(){
  await Promise.allSettled([pollBluehood(), pollWifiProbe(), pollNetdata(), pollProbeFeed()]);
}
tick(); setInterval(tick,6000);
</script>
</body>
</html>
DASHHTML

# ── 11. Build and start services ──────────────────────────────────────────────
log "Building and starting Docker services..."
cd "${RECON_DIR}"
docker compose pull --quiet 2>/dev/null || true
docker compose build --quiet 2>/dev/null || true
docker compose up -d

# ── 12. Enable Docker to start on boot ───────────────────────────────────────
log "Enabling services on boot..."
systemctl enable docker
cat > /etc/systemd/system/recon-stack.service <<SYSTEMD
[Unit]
Description=Lilly Recon Station Docker Stack
Requires=docker.service
After=docker.service wifimonitor.service network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=${RECON_DIR}
ExecStart=/usr/bin/docker compose up -d
ExecStop=/usr/bin/docker compose down
Restart=on-failure
RestartSec=10s

[Install]
WantedBy=multi-user.target
SYSTEMD
systemctl daemon-reload
systemctl enable recon-stack.service

# ── 13. Optional: mark read-only (after all writes done) ─────────────────────
# Uncomment to lock rootfs RO after provisioning
# echo "overlay" >> /etc/modules
# Already handled by build_pi_recon_image.sh if used

# ── 14. Mark provisioned ──────────────────────────────────────────────────────
mkdir -p "${RECON_DIR}"
touch "${MARK_FILE}"

log "═══════════════════════════════════════════════════════"
log " ✅ Provisioning complete!"
log ""
log "   Dashboard  → http://${PI_IP}:8080"
log "   Bluehood   → http://${PI_IP}:8081"
log "   WiFi Probe → http://${PI_IP}:8082"
log "   Netdata    → http://${PI_IP}:19999"
log ""
log " USB WiFi dongle for monitor mode: plug into USB port"
log " Set WIFI_IFACE=wlan0 if only one interface"
log "═══════════════════════════════════════════════════════"
