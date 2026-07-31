#!/usr/bin/env python3
"""
Lilly Sensor Platform - All-In-One Engine
Combines: DB Setup, Background Sensor Collector, and Fusion Engine.
"""

import sys
import os
import time
import json
import math
import sqlite3
import argparse
import subprocess
import threading
from pathlib import Path

# ==============================================================================
# CONFIGURATION & PATHS
# ==============================================================================
BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "db" / "lilly_sensors.db"
LOG_DIR = BASE_DIR / "logs"
POLL_INTERVAL = 30  # Polling rate in seconds

KNOWN_COMMERCIAL_PATTERNS = ["guest", "wifi", "coffee", "public", "store", "starbucks", "timhortons", "hotel"]

# Ensure directories exist
DB_PATH.parent.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)


# ==============================================================================
# PART 1: DATABASE MANAGER
# ==============================================================================
def init_db():
    """Creates SQLite tables if they do not exist."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS sensor_telemetry (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
        latitude REAL,
        longitude REAL,
        accuracy REAL,
        connected_ssid TEXT,
        nearby_ssids TEXT
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS semantic_visits (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        start_time DATETIME DEFAULT CURRENT_TIMESTAMP,
        end_time DATETIME,
        latitude REAL,
        longitude REAL,
        venue_name TEXT,
        primary_ssids TEXT,
        dwell_minutes INTEGER DEFAULT 0
    )
    """)

    conn.commit()
    conn.close()


# ==============================================================================
# PART 2: HARDWARE / TERMUX HELPER FUNCTIONS
# ==============================================================================
def run_termux_cmd(cmd):
    """Executes termux API CLI tool safely with timeout."""
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if res.stdout and res.stdout.strip():
            return json.loads(res.stdout)
    except Exception:
        pass
    return None

def calculate_haversine(lat1, lon1, lat2, lon2):
    """Calculates ground distance in meters between two lat/long points."""
    if None in (lat1, lon1, lat2, lon2):
        return 0.0
    R = 6371000  # Earth radius in meters
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(dlambda/2)**2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

def fetch_telemetry():
    """Polls GPS and Wi-Fi data from Termux API."""
    # GPS
    loc_raw = run_termux_cmd(["termux-location", "-p", "network", "-r", "last"])
    lat = loc_raw.get("latitude") if loc_raw else None
    lon = loc_raw.get("longitude") if loc_raw else None
    acc = loc_raw.get("accuracy") if loc_raw else None

    # Connected Wi-Fi
    wifi_conn = run_termux_cmd(["termux-wifi-connectioninfo"])
    connected_ssid = wifi_conn.get("ssid") if wifi_conn else None

    # Surrounding SSIDs
    wifi_scan = run_termux_cmd(["termux-wifi-scaninfo"])
    nearby_ssids = []
    if isinstance(wifi_scan, list):
        nearby_ssids = list(set([ap.get("ssid") for ap in wifi_scan if ap.get("ssid")]))

    return lat, lon, acc, connected_ssid, nearby_ssids


# ==============================================================================
# PART 3: BACKGROUND DAEMON WORKER
# ==============================================================================
def daemon_loop():
    """Background thread/process function that logs sensor deltas."""
    init_db()
    print(f"[*] Sensor Daemon running. Logging to {DB_PATH}")

    last_lat, last_lon, last_ssids = None, None, []

    while True:
        try:
            lat, lon, acc, connected_ssid, nearby_ssids = fetch_telemetry()
            
            dist_delta = calculate_haversine(last_lat, last_lon, lat, lon)
            ssid_changed = set(nearby_ssids) != set(last_ssids)

            # Record state if location shifted > 50m, Wi-Fi signature changed, or on first run
            if dist_delta > 50 or ssid_changed or last_lat is None:
                conn = sqlite3.connect(DB_PATH)
                cursor = conn.cursor()
                cursor.execute("""
                INSERT INTO sensor_telemetry (latitude, longitude, accuracy, connected_ssid, nearby_ssids)
                VALUES (?, ?, ?, ?, ?)
                """, (lat, lon, acc, connected_ssid, json.dumps(nearby_ssids)))
                conn.commit()
                conn.close()

                last_lat, last_lon, last_ssids = lat, lon, nearby_ssids
                print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Snapshot Logged | Delta: {dist_delta:.1f}m | SSIDs: {len(nearby_ssids)}")

        except Exception as e:
            print(f"[!] Daemon Error: {e}")

        time.sleep(POLL_INTERVAL)


# ==============================================================================
# PART 4: CONTEXT & FUSION ENGINE
# ==============================================================================
def analyze_ssids_for_venue(ssid_list):
    """Upskill Skill: Detect business indicators from surrounding SSIDs."""
    matched = []
    for ssid in ssid_list:
        if any(pattern in ssid.lower() for pattern in KNOWN_COMMERCIAL_PATTERNS):
            matched.append(ssid)
    return matched

def generate_context_payload():
    """Queries DB and builds context block for Lilly or remote SSH fetch."""
    if not DB_PATH.exists():
        return json.dumps({"error": "Database missing. Run --init or start daemon first."})

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute("""
    SELECT timestamp, latitude, longitude, connected_ssid, nearby_ssids 
    FROM sensor_telemetry 
    ORDER BY id DESC LIMIT 1
    """)
    row = cursor.fetchone()
    conn.close()

    if not row:
        return json.dumps({"status": "No telemetry recorded yet."})

    ts, lat, lon, conn_ssid, nearby_json = row
    nearby_ssids = json.loads(nearby_json) if nearby_json else []

    business_ssids = analyze_ssids_for_venue(nearby_ssids)

    context = {
        "status": "Active",
        "last_updated": ts,
        "connected_network": conn_ssid or "Mobile Data / None",
        "surrounding_ssids_count": len(nearby_ssids),
        "nearby_commercial_indicators": business_ssids if business_ssids else "None detected",
        "coordinates_coarse": f"{round(lat, 3)}, {round(lon, 3)}" if lat and lon else "Unknown"
    }

    return json.dumps(context, indent=2)


# ==============================================================================
# CLI ENTRY POINT
# ==============================================================================
def main():
    parser = argparse.ArgumentParser(description="Lilly Sensor Platform Automation Engine")
    parser.add_argument("--init", action="store_true", help="Initialize the database schema")
    parser.add_argument("--daemon", action="store_true", help="Run the background sensor collector loop")
    parser.add_argument("--context", action="store_true", help="Output current semantic context JSON for Lilly")
    parser.add_argument("--once", action="store_true", help="Take a single immediate telemetry reading and output context")

    args = parser.parse_args()

    if args.init:
        init_db()
        print(f"[+] Database initialized at: {DB_PATH}")
    elif args.daemon:
        daemon_loop()
    elif args.context:
        print(generate_context_payload())
    elif args.once:
        init_db()
        print("[*] Performing single telemetry sweep...")
        lat, lon, acc, conn_ssid, nearby_ssids = fetch_telemetry()
        
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("""
        INSERT INTO sensor_telemetry (latitude, longitude, accuracy, connected_ssid, nearby_ssids)
        VALUES (?, ?, ?, ?, ?)
        """, (lat, lon, acc, conn_ssid, json.dumps(nearby_ssids)))
        conn.commit()
        conn.close()
        
        print(generate_context_payload())
    else:
        parser.print_help()

if __name__ == "__main__":
    main()
