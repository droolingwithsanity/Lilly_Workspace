#!/usr/bin/env python3
"""
Lilly Person Tracker — Face + Device Correlation (Multi-Node)
─────────────────────────────────────────────────────────────
When YOLO detects a known face, this engine:
1. Records the sighting (time, location, face name, node_id)
2. Scans for nearby BLE/WiFi signals from ALL phone nodes
3. Correlates the face with a device (phone)
4. Tracks device movement over time on a map
5. Provides sighting history + map trail API

Architecture:
  Camera (any node) → YOLO "person" → Face ID "John" → BLE scan → "John's iPhone" → Map trail
  Supports 3 phone nodes, each with independent sensors + camera.
"""

import json
import os
import time
import math
import logging
import threading
import urllib.request
import urllib.error
from pathlib import Path
from dataclasses import dataclass, field, asdict
from typing import Optional

logger = logging.getLogger("lilly-tracker")

# ── Paths ────────────────────────────────────────────────────────────────
TRACKER_DIR = Path(os.environ.get("TRACKER_DIR", "/app/data/tracker"))
SIGHTINGS_DB = TRACKER_DIR / "sightings.json"
DEVICES_DB = TRACKER_DIR / "known_devices.json"
TRAILS_DB = TRACKER_DIR / "trails.json"
MAP_HTML = TRACKER_DIR / "radar.html"

# Sensor server URL (phone's Termux sensor server) — legacy single-node fallback
SENSOR_SERVER_URL = os.environ.get("SENSOR_SERVER_URL", "http://100.115.234.87:8099")


@dataclass
class Sighting:
    """A single sighting of a person (face detected)."""

    name: str
    timestamp: float
    lat: float
    lng: float
    accuracy: float = 0.0
    device_address: str = ""  # correlated BLE/WiFi MAC
    device_type: str = ""  # "bluetooth", "wifi", ""
    device_rssi: float = 0.0
    confidence: float = 0.0
    frame_description: str = ""
    node_id: str = ""  # which phone node detected this


@dataclass
class TrackedDevice:
    """A device correlated to a person."""

    address: str
    name: str
    person_name: str
    device_type: str  # "bluetooth" or "wifi"
    first_seen: float = 0.0
    last_seen: float = 0.0
    rssi_history: list = field(default_factory=list)
    trail: list = field(default_factory=list)  # [[lat, lng, timestamp], ...]
    # Bluehood-enriched fields (preserved when available)
    vendor: str = ""          # MAC OUI vendor string e.g. "Apple, Inc."
    bt_subtype: str = ""      # "ble" or "classic"
    classified_type: str = "" # bluehood device class: "phone","watch","tracker","headset"...
    service_uuids: list = field(default_factory=list)  # BLE service UUIDs


class PersonTracker:
    """
    Tracks known people across camera sightings + device signals.

    Multi-node support:
    - Scans GPS/BLE/WiFi from all registered phone nodes
    - Each node can detect faces independently
    - Aggregated sighting + trail data from all nodes

    How correlation works:
    1. Camera detects face → "John" at time T (on node N)
    2. Simultaneous BLE/WiFi scan finds devices nearby (from node N or all nodes)
    3. The device closest to the camera at time T is correlated to John
    4. Future BLE sightings of that device are plotted on the map as John's trail
    """

    def __init__(self):
        self.sightings: list[Sighting] = []
        self.known_devices: dict[str, TrackedDevice] = {}
        self.trails: dict[str, list] = {}  # name → [[lat, lng, ts], ...]
        self._lock = threading.Lock()
        # Per-node location caches: node_id → {lat, lng, accuracy, ts}
        self._node_locations: dict[str, dict] = {}
        # Per-node device observations: node_url → {addr: {"type","rssi","name","ts"}}
        # Lets the radar show "which node sees which device".
        self._node_devices: dict[str, dict] = {}
        # Legacy single-node caches (backward compat)
        self._location_cache = {"lat": 0.0, "lng": 0.0, "accuracy": 0, "ts": 0}
        self._wifi_cache: list = []
        self._bt_cache: list = []
        self._last_scan_ts = 0
        # WiFi geolocation cache: bssid → {lat, lng, accuracy, source, cached_at}
        # Resolved via Mozilla Location Services so WiFi APs have real GPS coords.
        self._wifi_geo_cache: dict[str, dict] = {}
        # Most recently resolved WiFi networks (enriched with lat/lng from MLS)
        self._resolved_wifi: list = []
        self._load_databases()
        TRACKER_DIR.mkdir(parents=True, exist_ok=True)

    def _load_databases(self):
        """Load sightings and device correlations from disk."""
        TRACKER_DIR.mkdir(parents=True, exist_ok=True)

        if SIGHTINGS_DB.exists():
            try:
                with open(SIGHTINGS_DB) as f:
                    data = json.load(f)
                self.sightings = [Sighting(**s) for s in data.get("sightings", [])]
                logger.info(f"Loaded {len(self.sightings)} sightings")
            except Exception as e:
                logger.warning(f"Failed to load sightings: {e}")

        if DEVICES_DB.exists():
            try:
                with open(DEVICES_DB) as f:
                    data = json.load(f)
                for addr, info in data.get("devices", {}).items():
                    self.known_devices[addr] = TrackedDevice(address=addr, **info)
                logger.info(f"Loaded {len(self.known_devices)} known devices")
            except Exception as e:
                logger.warning(f"Failed to load devices: {e}")

        if TRAILS_DB.exists():
            try:
                with open(TRAILS_DB) as f:
                    self.trails = json.load(f).get("trails", {})
                logger.info(f"Loaded trails for {len(self.trails)} people")
            except Exception as e:
                logger.warning(f"Failed to load trails: {e}")

    def _save_databases(self):
        """Persist everything to disk."""
        TRACKER_DIR.mkdir(parents=True, exist_ok=True)

        with open(SIGHTINGS_DB, "w") as f:
            json.dump(
                {"sightings": [asdict(s) for s in self.sightings[-500:]]}, f, indent=2
            )

        with open(DEVICES_DB, "w") as f:
            json.dump(
                {
                    "devices": {
                        addr: {
                            k: v
                            for k, v in asdict(d).items()
                            if k != "rssi_history" and k != "trail"
                        }
                        for addr, d in self.known_devices.items()
                    }
                },
                f,
                indent=2,
            )

        with open(TRAILS_DB, "w") as f:
            json.dump({"trails": self.trails}, f, indent=2)

    def _http_get_json(self, url: str, timeout: int = 8):
        """Helper: HTTP GET and return parsed JSON."""
        resp = urllib.request.urlopen(url, timeout=timeout)
        return json.loads(resp.read())

    def _scan_single_node(self, sensor_url: str) -> dict:
        """
        Scan GPS + BLE + WiFi from a single phone node.
        Returns {"wifi": [...], "bluetooth": [...], "location": {...}, "node_url": ...}

        Handles both the Android app's NanoHTTPD server (port 8099) and the
        legacy Termux node_radar_server.py response shapes:

        Android app shapes:
          /location     → {"location": {"latitude": ..., "longitude": ..., "accuracy": ...}, "timestamp": ...}
          /bluetooth/scan → {"available": true, "devices": [...]}
          /wifi/scan    → {"available": true, "networks": [...], "connection": {...}}
                          WiFi entries use "level" for signal (not "rssi")

        node_radar_server.py shapes (legacy fallback):
          /location     → {"location": {"latitude": ..., "longitude": ...}, "timestamp": ...}
          /bluetooth/scan → {"devices": [...], "count": ..., "timestamp": ...}
          /wifi/scan    → {"networks": [...], "count": ..., "timestamp": ...}
        """
        result = {"wifi": [], "bluetooth": [], "location": {}, "node_url": sensor_url}

        # ── Location (also acts as a reachability gate) ───────────────────
        # If /location doesn't answer quickly the node is unreachable/offline —
        # skip the (slower) BT + WiFi calls so one dead node can't stall the
        # whole tracker scan and defeat a fast refresh cadence.
        location_ok = False
        try:
            loc_resp = self._http_get_json(f"{sensor_url}/location", timeout=4)
            location_ok = True
            # Both servers wrap coordinates under a "location" sub-object.
            inner = loc_resp.get("location", loc_resp)
            if isinstance(inner, dict):
                lat = inner.get("latitude", inner.get("lat", 0)) or 0
                lng = (
                    inner.get("longitude", inner.get("lng", 0) or inner.get("lon", 0))
                    or 0
                )
                acc = inner.get("accuracy", 0) or 0
                result["location"] = {
                    "lat": float(lat),
                    "lng": float(lng),
                    "accuracy": float(acc),
                }
        except Exception as e:
            logger.debug(f"Location scan failed ({sensor_url}): {e}")

        if not location_ok:
            # Unreachable node — don't bother with the slower BT/WiFi calls.
            return result

        # ── Bluetooth ───────────────────────────────────────────────────
        try:
            bt_resp = self._http_get_json(f"{sensor_url}/bluetooth/scan", timeout=8)
            if isinstance(bt_resp, list):
                # Very old shape: bare list — filter ghost entries
                raw_devs = bt_resp
            elif isinstance(bt_resp, dict):
                # Android app / node_radar_server: {"devices": [...]}
                # "devices" now contains only live detections; "ghost_devices" are
                # paired-but-not-in-range — we deliberately ignore ghost_devices here.
                raw_devs = bt_resp.get("devices", [])
                if not isinstance(raw_devs, list):
                    raw_devs = []
            else:
                raw_devs = []

            # Drop any entry that is clearly not a live detection:
            #   rssi == -100  → signal floor / never actually scanned
            #   live == False → explicitly flagged as ghost by the sensor server
            #   type == "paired" with rssi == -100 → bonded-list ghost
            live_devs = [
                d
                for d in raw_devs
                if isinstance(d, dict)
                and d.get("live", True)  # default True for old servers
                and d.get("rssi", -100) > -100  # must have a real signal reading
                and not (d.get("type") == "paired" and d.get("rssi", -100) <= -95)
            ]
            result["bluetooth"] = live_devs
        except Exception as e:
            logger.debug(f"Bluetooth scan failed ({sensor_url}): {e}")

        # ── WiFi ────────────────────────────────────────────────────────
        try:
            wifi_resp = self._http_get_json(f"{sensor_url}/wifi/scan", timeout=6)
            if isinstance(wifi_resp, list):
                # Very old shape: bare list — normalize field names
                nets = wifi_resp
            elif isinstance(wifi_resp, dict):
                # Android app uses "networks", node_radar_server also uses "networks"
                nets = wifi_resp.get("networks", wifi_resp.get("wifi", []))
            else:
                nets = []
            # Normalize: Android uses "level" for RSSI; downstream expects "rssi"
            normalized = []
            for net in nets or []:
                if not isinstance(net, dict):
                    continue
                entry = dict(net)
                if "rssi" not in entry:
                    entry["rssi"] = entry.get("level", -100)
                normalized.append(entry)
            result["wifi"] = normalized
        except Exception as e:
            logger.debug(f"WiFi scan failed ({sensor_url}): {e}")

        return result

    def scan_nearby_devices(self, sensor_url: str = None, node_id: str = "") -> dict:
        """
        Scan for nearby BLE + WiFi devices.

        Multi-node mode (sensor_url=None):
          Scans ALL registered nodes and merges results.
        Single-node mode (sensor_url provided):
          Scans just that one node.

        Returns {"wifi": [...], "bluetooth": [...], "location": {...}, "nodes_scanned": N}
        """
        merged = {"wifi": [], "bluetooth": [], "location": {}, "nodes_scanned": 0}

        # Get all sensor URLs to scan
        if sensor_url:
            urls = [sensor_url]
        else:
            # Try node registry (ALL nodes, online or not, so the host provider
            # and a phone without a fresh heartbeat still contribute), fall back
            # to the legacy single URL.
            try:
                from node_registry import get_node_registry

                registry = get_node_registry()
                urls = registry.get_all_sensor_urls()
                if not urls:
                    urls = [SENSOR_SERVER_URL]
                # Always ensure the phone default is covered (dedupe).
                if SENSOR_SERVER_URL and SENSOR_SERVER_URL not in urls:
                    urls.append(SENSOR_SERVER_URL)
            except ImportError:
                urls = [SENSOR_SERVER_URL]

        # Scan every node concurrently so a slow/offline phone (its HTTP timeouts
        # can pile up to tens of seconds) doesn't stall the whole scan and defeat
        # a fast cadence. Each node is independent and updates its own cache keys.
        from concurrent.futures import ThreadPoolExecutor

        def _process_node(url: str) -> dict:
            try:
                node_result = self._scan_single_node(url)
            except Exception as e:
                logger.warning(f"Node scan failed ({url}): {e}")
                return {}
            if not node_result:
                return {}

            # Cache per-node location
            if node_result.get("location", {}).get("lat"):
                self._node_locations[url] = node_result["location"]
                self._node_locations[url]["ts"] = time.time()

            # Per-node device correlation: record which node saw which device.
            node_devs: dict = {}
            for b in node_result.get("bluetooth", []):
                if not isinstance(b, dict):
                    continue
                addr = b.get("address") or b.get("mac") or ""
                if not addr:
                    continue
                node_devs[addr] = {
                    "type": "bluetooth",
                    "rssi": b.get("rssi", -100),
                    "name": b.get("name") or addr,
                    "ts": time.time(),
                }
            for w in node_result.get("wifi", []):
                if not isinstance(w, dict):
                    continue
                addr = w.get("bssid") or w.get("address") or w.get("mac") or ""
                if not addr:
                    continue
                node_devs[addr] = {
                    "type": "wifi",
                    "rssi": w.get("rssi", -100),
                    "name": w.get("ssid") or w.get("name") or addr,
                    "ts": time.time(),
                }
            self._node_devices[url] = node_devs
            return node_result

        results = []
        with ThreadPoolExecutor(max_workers=min(len(urls) or 1, 8)) as ex:
            results = list(ex.map(_process_node, urls))

        for node_result in results:
            if not node_result:
                continue
            merged["wifi"].extend(node_result.get("wifi", []))
            merged["bluetooth"].extend(node_result.get("bluetooth", []))
            if node_result.get("location", {}).get("lat") or node_result.get(
                "location", {}
            ).get("lng"):
                # Use the first node with a valid GPS fix
                if not merged["location"].get("lat") and not merged["location"].get(
                    "lng"
                ):
                    merged["location"] = node_result["location"]
            merged["nodes_scanned"] += 1

        # Update caches
        if merged["bluetooth"]:
            self._bt_cache = merged["bluetooth"]
        if merged["wifi"]:
            self._wifi_cache = merged["wifi"]
        if merged["location"]:
            self._location_cache = merged["location"]
            self._location_cache["ts"] = time.time()

        self._last_scan_ts = time.time()
        return merged

    @staticmethod
    def _rssi_to_distance(rssi: float, freq_mhz: float = 2412) -> float:
        """Estimate distance from RSSI using log-distance path loss model."""
        if rssi == 0:
            return 100.0
        # Simplified: reference RSSI at 1m, path loss exponent
        rssi_ref = -40  # typical at 1m
        n = 2.5  # path loss exponent (indoor)
        ratio = (rssi_ref - rssi) / (10 * n)
        return max(0.5, 10**ratio)

    def _hash_to_angle(self, s: str) -> float:
        """Deterministic angle from a string (for map offset)."""
        h = 0
        for c in s:
            h = ord(c) + ((h << 5) - h)
        return (abs(h) % 360) * (math.pi / 180)

    def _offset_position(
        self, lat: float, lng: float, distance_m: float, angle_rad: float
    ) -> tuple[float, float]:
        """Calculate offset position from a point."""
        R = 6378137
        d_lat = (distance_m * math.cos(angle_rad)) / R
        d_lng = (distance_m * math.sin(angle_rad)) / (R * math.cos(math.pi * lat / 180))
        return lat + (d_lat * 180 / math.pi), lng + (d_lng * 180 / math.pi)

    # ── WiFi Geolocation ─────────────────────────────────────────────────
    # Primary:  Apple WLOC API — per-BSSID GPS resolution, ~5–15m accuracy.
    #           Uses the same endpoint as iOS/macOS location services.
    #           No API key needed. Pure Python protobuf (struct only).
    # Fallback: Mozilla Location Services — aggregate fix from BSSID batch.
    #           Free, no key, ~10–50m accuracy.
    # Cache:    Both results cached per-BSSID for WIFI_GEO_CACHE_TTL seconds.

    WLOC_URL     = "https://gs-loc.apple.com/clls/wloc"
    WLOC_TIMEOUT = 6   # seconds
    MLS_URL      = "https://location.services.mozilla.com/v1/geolocate?key=geoclue"
    MLS_TIMEOUT  = 5   # seconds
    MLS_MIN_APS  = 2   # minimum APs for a reliable MLS fix
    WIFI_GEO_CACHE_TTL = 300  # 5 minutes — APs don't move

    # Fixed header Apple requires before the protobuf body.
    # Reverse-engineered by the apple-corelocation-experiments project.
    # See: https://github.com/acheong08/apple-corelocation-experiments
    _WLOC_PREFIX = bytes.fromhex(
        "0001000a656e2d3030315f3030310013636f6d2e6170706c652e6c6f636174696f6e"
        "64000c31372e352e312e323146393000000001000000"
    )
    _WLOC_HEADERS = {
        "Content-Type":  "application/x-www-form-urlencoded",
        "Accept":        "*/*",
        "Accept-Charset": "utf-8",
        "Accept-Language": "en-us",
        "User-Agent":    "locationd/2890.16.16 CFNetwork/1496.0.7 Darwin/23.5.0",
    }

    # ── Pure-Python protobuf helpers ──────────────────────────────────────
    # Apple WLOC uses protobuf3. We only need field tags 2 (wifiDevices),
    # 3 (numCellResults), 4 (numWifiResults), and 33 (deviceType).
    # The response has wifiDevices[].location.{latitude, longitude,
    # horizontalAccuracy} as int64 with 8-decimal-place encoding.

    @staticmethod
    def _pb_varint(value: int) -> bytes:
        """Encode a non-negative integer as a protobuf varint."""
        out = []
        while True:
            b = value & 0x7F
            value >>= 7
            if value:
                out.append(b | 0x80)
            else:
                out.append(b)
                break
        return bytes(out)

    @staticmethod
    def _pb_zigzag(value: int) -> int:
        """ZigZag-encode a signed integer for sint32/sint64."""
        return (value << 1) ^ (value >> 63)

    @staticmethod
    def _pb_tag(field: int, wire: int) -> bytes:
        """Encode a protobuf field tag (field_number << 3 | wire_type)."""
        return PersonTracker._pb_varint((field << 3) | wire)

    @staticmethod
    def _pb_len_field(field: int, data: bytes) -> bytes:
        """Length-delimited field (wire type 2)."""
        return (
            PersonTracker._pb_tag(field, 2)
            + PersonTracker._pb_varint(len(data))
            + data
        )

    @staticmethod
    def _pb_string(field: int, s: str) -> bytes:
        """Protobuf string field."""
        enc = s.encode()
        return PersonTracker._pb_len_field(field, enc)

    @staticmethod
    def _pb_sint32(field: int, value: int) -> bytes:
        """Protobuf sint32 (ZigZag varint, wire type 0)."""
        return (
            PersonTracker._pb_tag(field, 0)
            + PersonTracker._pb_varint(PersonTracker._pb_zigzag(value))
        )

    def _wloc_encode_request(self, bssids: list[str]) -> bytes:
        """
        Encode a WLOC request for a list of BSSIDs.

        protobuf schema (AppleWLoc):
          field 2 (repeated, len-delim): WifiDevice { field 1: bssid string }
          field 3 (sint32): numCellResults = 0
          field 4 (sint32): numWifiResults = 0  (0 = return all neighbours)
          field 33 (len-delim): DeviceType {
              field 1: operating_system string
              field 2: model string }
        """
        body = b""
        for bssid in bssids:
            # WifiDevice { bssid: field 1 string }
            wifi_device = self._pb_string(1, bssid)
            body += self._pb_len_field(2, wifi_device)

        # numCellResults = 0, numWifiResults = 0
        body += self._pb_sint32(3, 0)
        body += self._pb_sint32(4, 0)

        # DeviceType { operating_system: field 1, model: field 2 }
        device_type = (
            self._pb_string(1, "iPhone OS17.5/21F79")
            + self._pb_string(2, "iPhone12,1")
        )
        body += self._pb_len_field(33, device_type)

        # Full request = fixed prefix + length byte + protobuf body
        return self._WLOC_PREFIX + bytes([len(body)]) + body

    @staticmethod
    def _decode_varint(data: bytes, pos: int) -> tuple[int, int]:
        """Decode a protobuf varint. Returns (value, new_pos)."""
        result = 0
        shift = 0
        while pos < len(data):
            b = data[pos]
            pos += 1
            result |= (b & 0x7F) << shift
            shift += 7
            if not (b & 0x80):
                break
        return result, pos

    @staticmethod
    def _decode_sint64(raw: int) -> int:
        """Decode ZigZag-encoded sint64 back to signed int."""
        # WLOC lat/lng are stored as plain int64 (not zigzag) with value = coord * 1e8
        # but horizontalAccuracy is also int64. We treat all as signed via two's complement.
        if raw >= (1 << 63):
            raw -= (1 << 64)
        return raw

    def _wloc_parse_response(self, data: bytes) -> list[dict]:
        """
        Parse Apple WLOC binary response.

        Response layout: 10-byte header, then protobuf AppleWLoc.
        Returns list of {"bssid": str, "lat": float, "lng": float, "accuracy": float}.

        Wire format (protobuf3, all field numbers as in WaveDigger schema.ts):
          AppleWLoc.wifiDevices[] → field 2, len-delim
            WifiDevice.bssid      → field 1, string
            WifiDevice.location   → field 2, len-delim
              Location.latitude   → field 1, int64 (varint, value * 1e8)
              Location.longitude  → field 2, int64
              Location.horizontalAccuracy → field 3, int64 (metres * 1e8)
        """
        if len(data) < 10:
            return []
        data = data[10:]  # skip response header

        results = []

        def parse_fields(buf: bytes) -> dict:
            """Parse all fields in a protobuf message into {field_num: [values]}."""
            fields: dict = {}
            pos = 0
            while pos < len(buf):
                if pos >= len(buf):
                    break
                tag, pos = PersonTracker._decode_varint(buf, pos)
                field_num = tag >> 3
                wire_type = tag & 0x07
                if wire_type == 0:  # varint
                    val, pos = PersonTracker._decode_varint(buf, pos)
                    fields.setdefault(field_num, []).append(val)
                elif wire_type == 2:  # length-delimited
                    length, pos = PersonTracker._decode_varint(buf, pos)
                    val = buf[pos:pos + length]
                    pos += length
                    fields.setdefault(field_num, []).append(val)
                elif wire_type == 1:  # 64-bit fixed
                    pos += 8
                elif wire_type == 5:  # 32-bit fixed
                    pos += 4
                else:
                    break  # unknown wire type — stop
            return fields

        top = parse_fields(data)
        # field 2 = repeated WifiDevice
        for device_bytes in top.get(2, []):
            if not isinstance(device_bytes, bytes):
                continue
            dev_fields = parse_fields(device_bytes)
            # field 1 = bssid string
            bssid_bytes = dev_fields.get(1, [b""])[0]
            bssid = bssid_bytes.decode(errors="ignore") if isinstance(bssid_bytes, bytes) else ""
            if not bssid:
                continue
            # field 2 = Location message
            loc_list = dev_fields.get(2, [])
            if not loc_list:
                continue
            loc_bytes = loc_list[0]
            if not isinstance(loc_bytes, bytes):
                continue
            loc_fields = parse_fields(loc_bytes)
            raw_lat  = loc_fields.get(1, [None])[0]
            raw_lng  = loc_fields.get(2, [None])[0]
            raw_acc  = loc_fields.get(3, [None])[0]
            if raw_lat is None or raw_lng is None:
                continue
            lat = self._decode_sint64(raw_lat) * 1e-8
            lng = self._decode_sint64(raw_lng) * 1e-8
            acc = (self._decode_sint64(raw_acc) * 1e-8) if raw_acc is not None else 50.0
            # Sanity check — Apple returns (-180, -180) for unknowns
            if abs(lat) < 0.001 and abs(lng) < 0.001:
                continue
            if lat == -180.0 and lng == -180.0:
                continue
            results.append({
                "bssid": bssid.lower(),
                "lat":   round(lat, 8),
                "lng":   round(lng, 8),
                "accuracy": round(abs(acc), 1),
            })
        return results

    def _geolocate_wifi_apple(self, bssids: list[str]) -> list[dict]:
        """
        Query Apple WLOC for a list of BSSIDs.
        Returns a list of {"bssid", "lat", "lng", "accuracy", "source": "wloc"}
        for every BSSID Apple has in its database.

        Apple's API returns not just the queried BSSIDs but also their
        neighbours — we filter to only the ones we asked for.
        """
        if not bssids:
            return []
        # Normalise to lowercase colon-separated format
        norm = []
        for b in bssids:
            b = b.strip().lower().replace("-", ":").replace(".", ":")
            # Ensure each octet is two hex digits
            parts = b.split(":")
            if len(parts) == 6:
                b = ":".join(p.zfill(2) for p in parts)
            if len(b) == 17:
                norm.append(b)

        if not norm:
            return []

        try:
            payload = self._wloc_encode_request(norm)
            req = urllib.request.Request(
                self.WLOC_URL,
                data=payload,
                headers=self._WLOC_HEADERS,
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=self.WLOC_TIMEOUT) as resp:
                raw = resp.read()

            # Response may be gzip-compressed
            if len(raw) > 2 and raw[0] == 0x1F and raw[1] == 0x8B:
                import gzip
                raw = gzip.decompress(raw)

            hits = self._wloc_parse_response(raw)
            # Tag source and filter to requested BSSIDs
            requested = set(norm)
            out = []
            for h in hits:
                h["source"] = "wloc"
                if h["bssid"] in requested:
                    out.append(h)
            logger.info(
                f"Apple WLOC: {len(out)}/{len(norm)} BSSIDs resolved"
            )
            return out

        except urllib.error.HTTPError as e:
            logger.warning(f"Apple WLOC HTTP {e.code}: {e.reason}")
        except Exception as e:
            logger.debug(f"Apple WLOC request failed: {e}")
        return []

    def _geolocate_wifi_mls(self, wifi_networks: list) -> Optional[dict]:
        """
        Fallback: resolve an aggregate GPS fix via Mozilla Location Services.
        Returns {"lat", "lng", "accuracy", "source": "mls"} or None.
        """
        aps = []
        for net in wifi_networks:
            bssid = net.get("bssid") or net.get("mac") or net.get("address") or ""
            if not bssid or len(bssid) < 11:
                continue
            rssi = net.get("rssi") or net.get("level") or -100
            try:
                rssi = int(rssi)
            except (TypeError, ValueError):
                rssi = -100
            aps.append({"macAddress": bssid.lower(), "signalStrength": rssi})

        if len(aps) < self.MLS_MIN_APS:
            return None

        payload = json.dumps({"wifiAccessPoints": aps}).encode()
        req = urllib.request.Request(
            self.MLS_URL,
            data=payload,
            headers={"Content-Type": "application/json", "User-Agent": "LillyTracker/1.0"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.MLS_TIMEOUT) as resp:
                body = json.loads(resp.read())
            loc = body.get("location", {})
            lat = loc.get("lat")
            lng = loc.get("lng")
            if lat is None or lng is None:
                return None
            logger.info(f"MLS fallback fix: ({lat:.5f}, {lng:.5f}) ±{body.get('accuracy', '?')}m")
            return {
                "lat": float(lat),
                "lng": float(lng),
                "accuracy": float(body.get("accuracy", 50.0)),
                "source": "mls",
                "ap_count": len(aps),
            }
        except urllib.error.HTTPError as e:
            if e.code != 404:
                logger.debug(f"MLS HTTP {e.code}: {e.reason}")
        except Exception as e:
            logger.debug(f"MLS lookup failed: {e}")
        return None

    def _resolve_wifi_locations(self, wifi_networks: list) -> list:
        """
        Resolve real GPS coordinates for each visible WiFi AP.

        Strategy:
          1. Check per-BSSID cache (WIFI_GEO_CACHE_TTL seconds).
          2. For stale/uncached BSSIDs: query Apple WLOC in one batch call.
             Apple returns a real lat/lng per BSSID it knows (~5–15m accuracy).
          3. For BSSIDs Apple doesn't know: fall back to Mozilla MLS aggregate
             fix, then spread APs around that centroid by RSSI delta (~20–50m).
          4. Mark any remaining unknowns with a short negative-cache entry so
             we don't hammer the APIs on every scan.

        Returns the input list enriched with lat/lng/accuracy/geo_source on
        every AP that could be resolved.
        """
        now = time.time()
        resolved = []
        stale = []

        # ── Step 1: cache check ───────────────────────────────────────────
        for net in wifi_networks:
            bssid = (net.get("bssid") or net.get("mac") or net.get("address") or "").lower()
            if not bssid:
                continue
            cached = self._wifi_geo_cache.get(bssid)
            if cached and (now - cached.get("cached_at", 0)) < self.WIFI_GEO_CACHE_TTL:
                enriched = dict(net)
                if cached.get("lat") and cached.get("lng"):
                    enriched.update({
                        "lat": cached["lat"],
                        "lng": cached["lng"],
                        "accuracy": cached["accuracy"],
                        "geo_source": cached.get("source", "wloc"),
                    })
                resolved.append(enriched)
            else:
                stale.append(net)

        if not stale:
            return resolved

        stale_bssids = []
        for net in stale:
            b = (net.get("bssid") or net.get("mac") or net.get("address") or "").lower()
            if b:
                stale_bssids.append(b)

        # ── Step 2: Apple WLOC per-BSSID resolution ───────────────────────
        wloc_hits: dict[str, dict] = {}  # bssid → {lat, lng, accuracy, source}
        if stale_bssids:
            for hit in self._geolocate_wifi_apple(stale_bssids):
                wloc_hits[hit["bssid"]] = hit

        # ── Step 3: MLS fallback for BSSIDs Apple didn't resolve ──────────
        mls_unknowns = [
            net for net in stale
            if (net.get("bssid") or net.get("mac") or net.get("address") or "").lower()
            not in wloc_hits
        ]
        mls_fix: Optional[dict] = None
        if mls_unknowns:
            mls_fix = self._geolocate_wifi_mls(mls_unknowns)

        # ── Step 4: Enrich stale APs and populate cache ───────────────────
        # MLS fix spread: sort unknowns by RSSI to place stronger APs closer
        # to the centroid.
        if mls_fix and mls_unknowns:
            mls_unknowns_sorted = sorted(
                mls_unknowns,
                key=lambda x: x.get("rssi") or x.get("level") or -100,
                reverse=True,
            )
            strongest_rssi = (
                mls_unknowns_sorted[0].get("rssi")
                or mls_unknowns_sorted[0].get("level")
                or -55
            )
            for net in mls_unknowns_sorted:
                bssid = (net.get("bssid") or net.get("mac") or net.get("address") or "").lower()
                if not bssid:
                    continue
                rssi = net.get("rssi") or net.get("level") or -100
                rssi_delta = max(0, strongest_rssi - rssi)
                spread_m = min(30.0, rssi_delta * 0.5)
                angle = self._hash_to_angle(bssid)
                ap_lat, ap_lng = self._offset_position(
                    mls_fix["lat"], mls_fix["lng"], spread_m, angle
                )
                wloc_hits[bssid] = {
                    "bssid": bssid,
                    "lat": ap_lat,
                    "lng": ap_lng,
                    "accuracy": mls_fix["accuracy"],
                    "source": "mls",
                }

        for net in stale:
            bssid = (net.get("bssid") or net.get("mac") or net.get("address") or "").lower()
            enriched = dict(net)
            hit = wloc_hits.get(bssid)
            if hit and hit.get("lat") and hit.get("lng"):
                enriched.update({
                    "lat": hit["lat"],
                    "lng": hit["lng"],
                    "accuracy": hit.get("accuracy", 50),
                    "geo_source": hit.get("source", "wloc"),
                })
                self._wifi_geo_cache[bssid] = {
                    "lat": hit["lat"],
                    "lng": hit["lng"],
                    "accuracy": hit.get("accuracy", 50),
                    "source": hit.get("source", "wloc"),
                    "cached_at": now,
                }
            else:
                # Negative-cache: don't retry until TTL expires
                self._wifi_geo_cache[bssid] = {
                    "lat": 0, "lng": 0, "accuracy": 0,
                    "source": "none", "cached_at": now,
                }
            resolved.append(enriched)

        return resolved

    def _best_wifi_anchor(self, wifi_networks: list) -> Optional[dict]:
        """
        Return the strongest WiFi AP that has a resolved GPS fix.
        Used to anchor BT devices to real coordinates.
        """
        best = None
        best_rssi = -200
        for net in wifi_networks:
            if not (net.get("lat") and net.get("lng")):
                continue
            rssi = net.get("rssi") or net.get("level") or -100
            if rssi > best_rssi:
                best_rssi = rssi
                best = {
                    "lat":      float(net["lat"]),
                    "lng":      float(net["lng"]),
                    "accuracy": net.get("accuracy", 50),
                    "ssid":     net.get("ssid") or net.get("name") or "",
                    "bssid":    net.get("bssid") or "",
                    "rssi":     rssi,
                    "source":   net.get("geo_source", "wloc"),
                }
        return best

    def _trilaterate_bt(
        self, bt_addr: str, bt_rssi: float, resolved_wifi: list
    ) -> Optional[tuple[float, float, float]]:
        """
        Trilaterate a BT device position from 3+ resolved WiFi APs.

        Each AP whose GPS we know acts as a reference beacon.  We estimate
        the BT device's distance from that AP using the difference between the
        AP's RSSI (observed by the phone) and the BT device's RSSI, then run a
        weighted-centroid trilateration.

        Returns (lat, lng, accuracy_m) or None if fewer than 3 APs resolved.

        Note: This is a simplified weighted centroid, not full nonlinear least
        squares — good enough for 20–60m positioning without scipy.
        """
        anchors = [
            net for net in resolved_wifi
            if net.get("lat") and net.get("lng")
        ]
        if len(anchors) < 3:
            return None

        # Estimate BT distance from each AP using path-loss model:
        # distance(BT) ≈ distance(AP) * 10^((rssi_ap - rssi_bt) / (10*n))
        # where n≈2.5 indoors.  Cap at 80m to avoid outliers.
        n_exp = 2.5
        weighted_lat = 0.0
        weighted_lng = 0.0
        total_weight = 0.0

        for ap in anchors:
            ap_rssi = ap.get("rssi") or ap.get("level") or -70
            ap_dist = self._rssi_to_distance(ap_rssi)   # AP–phone distance
            # BT device is somewhere between the phone and (or beyond) the AP.
            # Use BT RSSI directly to estimate BT–phone distance.
            bt_dist  = min(self._rssi_to_distance(bt_rssi), 80.0)
            # Weight = inverse square of estimated BT–AP distance
            # (closer APs are more reliable anchors).
            combined_dist = max(1.0, (ap_dist + bt_dist) / 2)
            weight = 1.0 / (combined_dist ** 2)
            weighted_lat += float(ap["lat"]) * weight
            weighted_lng += float(ap["lng"]) * weight
            total_weight += weight

        if total_weight == 0:
            return None

        est_lat = weighted_lat / total_weight
        est_lng = weighted_lng / total_weight
        # Accuracy estimate: average BT distance + worst AP accuracy
        avg_acc = max(ap.get("accuracy", 50) for ap in anchors)
        est_acc = self._rssi_to_distance(bt_rssi) + avg_acc
        return est_lat, est_lng, min(est_acc, 150.0)

    def record_sighting(
        self,
        name: str,
        confidence: float,
        lat: float = 0,
        lng: float = 0,
        frame_description: str = "",
        node_id: str = "",
    ) -> dict:
        """
        Record a face sighting and correlate with nearby devices.
        Called by the vision pipeline when a known face is detected.
        Returns the correlation result.
        """
        now = time.time()
        if lat == 0:
            lat = self._location_cache.get("lat", 0)
        if lng == 0:
            lng = self._location_cache.get("lng", 0)

        # Scan for nearby devices at the moment of sighting
        nearby = self.scan_nearby_devices()
        bt_devices = nearby.get("bluetooth", [])
        wifi_devices = nearby.get("wifi", [])

        # Find the closest device (most likely belongs to the detected person)
        best_device = None
        best_distance = float("inf")

        for dev in bt_devices:
            addr = dev.get("address", "")
            rssi = dev.get("rssi", -100)
            # Skip ghost entries: no real signal means the device isn't actually here
            if rssi <= -95 or not addr:
                continue
            dist = self._rssi_to_distance(rssi)
            if dist < best_distance:
                best_distance = dist
                best_device = {
                    "address": addr,
                    "name": dev.get("name", "Unknown BLE"),
                    "type": "bluetooth",
                    "rssi": rssi,
                    "distance": dist,
                }

        for dev in wifi_devices:
            addr = dev.get("bssid", dev.get("address", ""))
            rssi = dev.get("rssi", -80)
            freq = dev.get("frequency", 2412)
            dist = self._rssi_to_distance(rssi, freq)
            if dist < best_distance:
                best_distance = dist
                best_device = {
                    "address": addr,
                    "name": dev.get("ssid", dev.get("name", "Unknown WiFi")),
                    "type": "wifi",
                    "rssi": rssi,
                    "distance": dist,
                }

        # Create sighting
        sighting = Sighting(
            name=name,
            timestamp=now,
            lat=lat,
            lng=lng,
            device_address=best_device["address"] if best_device else "",
            device_type=best_device["type"] if best_device else "",
            device_rssi=best_device["rssi"] if best_device else 0,
            confidence=confidence,
            frame_description=frame_description,
            node_id=node_id,
        )

        with self._lock:
            self.sightings.append(sighting)

            # Correlate device to person
            if best_device:
                addr = best_device["address"]
                if addr in self.known_devices:
                    dev = self.known_devices[addr]
                    dev.last_seen = now
                    dev.rssi_history.append({"rssi": best_device["rssi"], "ts": now})
                    dev.rssi_history = dev.rssi_history[-100:]  # keep last 100
                else:
                    self.known_devices[addr] = TrackedDevice(
                        address=addr,
                        name=best_device["name"],
                        person_name=name,
                        device_type=best_device["type"],
                        first_seen=now,
                        last_seen=now,
                        rssi_history=[{"rssi": best_device["rssi"], "ts": now}],
                        trail=[],
                    )

                # Add trail point
                angle = self._hash_to_angle(addr)
                trail_lat, trail_lng = self._offset_position(
                    lat, lng, best_device["distance"], angle
                )
                trail_point = [round(trail_lat, 6), round(trail_lng, 6), now]

                if name not in self.trails:
                    self.trails[name] = []
                # Avoid duplicate points
                last = self.trails[name][-1] if self.trails[name] else None
                if (
                    not last
                    or abs(last[0] - trail_lat) > 0.00001
                    or abs(last[1] - trail_lng) > 0.00001
                ):
                    self.trails[name].append(trail_point)
                    # Keep last 1000 points per person
                    self.trails[name] = self.trails[name][-1000:]

            # Save periodically (every 10 sightings)
            if len(self.sightings) % 10 == 0:
                self._save_databases()

        result = {
            "name": name,
            "confidence": confidence,
            "location": {"lat": lat, "lng": lng},
            "correlated_device": best_device,
            "nearby_devices": len(bt_devices) + len(wifi_devices),
            "total_sightings": len([s for s in self.sightings if s.name == name]),
        }

        logger.info(
            f"Sighting: {name} at ({lat:.4f}, {lng:.4f}) "
            f"— correlated to {best_device['name']}"
            if best_device
            else f"Sighting: {name} — no device correlated"
        )

        return result

    def get_person_trail(self, name: str) -> list:
        """Get movement trail for a person."""
        return self.trails.get(name, [])

    def get_all_trails(self) -> dict:
        """Get all person trails."""
        return self.trails

    def get_sightings(self, name: str = None, limit: int = 100) -> list:
        """Get recent sightings, optionally filtered by name."""
        with self._lock:
            s = self.sightings
            if name:
                s = [x for x in s if x.name == name]
            return [asdict(x) for x in s[-limit:]]

    def get_tracked_devices(self) -> list:
        """Get all devices correlated to people."""
        return [asdict(d) for d in self.known_devices.values()]

    def ingest_nearby(
        self,
        bt: list | None = None,
        wifi: list | None = None,
        lat: float = 0,
        lng: float = 0,
        node_id: str = "",
    ) -> dict:
        """
        Persist a raw BT + WiFi scan into the tracker so the radar map and
        /api/tracker/devices reflect real nearby devices — no face detection
        required. This is what /api/tracker/scan and /api/tracker/nearby call.

        WiFi geolocation:
          Each WiFi network's BSSID is resolved to a real GPS coordinate via
          Mozilla Location Services (MLS). Resolved positions are cached per
          BSSID for MLS_CACHE_TTL seconds so we don't hammer the API.

        BT anchoring:
          BT devices are placed at the location of the strongest WiFi AP that
          has a resolved GPS fix — ± a small RSSI-proportional offset.
          This gives BT devices a real neighbourhood-level position instead of
          orbiting your phone in a RSSI circle.

        Devices are upserted into known_devices so they surface on the tracker
        map, and the location cache is updated for later face sightings.
        """
        bt = bt or []
        wifi = wifi or []
        now = time.time()
        if lat:
            self._location_cache = {
                "lat": float(lat),
                "lng": float(lng),
                "ts": now,
            }

        # ── Step 1: Resolve WiFi APs to real GPS via MLS ─────────────────
        # Run in a background thread so it doesn't block the HTTP response,
        # but use the result immediately if it completes in time.
        resolved_wifi = self._resolve_wifi_locations(wifi)
        self._resolved_wifi = resolved_wifi  # cache for get_map_data()

        # Pick the best WiFi anchor (strongest AP with a GPS fix) for BT
        wifi_anchor = self._best_wifi_anchor(resolved_wifi)
        # If MLS gave us a fix, also update the location cache so face sightings
        # and node positions use the MLS-derived coordinate.
        if wifi_anchor and wifi_anchor.get("lat") and wifi_anchor.get("lng"):
            if not (lat and lng):
                # Only override if no GPS was provided by the phone
                self._location_cache = {
                    "lat": wifi_anchor["lat"],
                    "lng": wifi_anchor["lng"],
                    "accuracy": wifi_anchor.get("accuracy", 50),
                    "ts": now,
                    "source": "mls",
                }

        # ── Step 2: Upsert WiFi networks with resolved GPS ────────────────
        for w in resolved_wifi:
            if not isinstance(w, dict):
                continue
            addr = w.get("bssid") or w.get("address") or w.get("mac") or ""
            if not addr:
                continue
            rssi = w.get("rssi", -100) or -100
            name = w.get("ssid") or w.get("name") or addr
            w_lat = w.get("lat") or 0
            w_lng = w.get("lng") or 0
            w_acc = w.get("accuracy") or 50
            dev = self.known_devices.get(addr)
            if dev:
                dev.last_seen = now
                dev.rssi_history.append({"rssi": rssi, "ts": now})
                dev.rssi_history = dev.rssi_history[-100:]
                if w_lat and w_lng:
                    dev.trail.append({"lat": float(w_lat), "lng": float(w_lng), "rssi": rssi, "ts": now, "accuracy": w_acc, "source": w.get("geo_source", "rssi")})
                    dev.trail = dev.trail[-50:]
                elif lat and lng:
                    dev.trail.append({"lat": float(lat), "lng": float(lng), "rssi": rssi, "ts": now})
                    dev.trail = dev.trail[-50:]
            else:
                if w_lat and w_lng:
                    trail_seed = [{"lat": float(w_lat), "lng": float(w_lng), "rssi": rssi, "ts": now, "accuracy": w_acc, "source": w.get("geo_source", "rssi")}]
                elif lat and lng:
                    trail_seed = [{"lat": float(lat), "lng": float(lng), "rssi": rssi, "ts": now}]
                else:
                    trail_seed = []
                self.known_devices[addr] = TrackedDevice(
                    address=addr,
                    name=name,
                    person_name="nearby",
                    device_type="wifi",
                    first_seen=now,
                    last_seen=now,
                    rssi_history=[{"rssi": rssi, "ts": now}],
                    trail=trail_seed,
                )

        # ── Step 3: Upsert BT devices, anchored to WiFi GPS ──────────────
        for d in bt:
            if not isinstance(d, dict):
                continue
            addr = d.get("address") or d.get("mac") or d.get("id") or ""
            if not addr:
                continue
            rssi = d.get("rssi", -100) or -100
            name = d.get("name") or addr
            dtype = (d.get("type") or d.get("bt_type") or "bluetooth").lower()
            if dtype in ("ble", "classic", "bt", "le", "bar"):
                dtype = "bluetooth"

            # Bluehood-enriched metadata
            vendor = d.get("vendor") or ""
            bt_subtype = d.get("bt_type") or d.get("type") or ""
            if bt_subtype.lower() in ("ble", "le"):
                bt_subtype = "ble"
            elif bt_subtype.lower() in ("classic", "br/edr", "bt"):
                bt_subtype = "classic"
            classified_type = d.get("device_type") or ""  # e.g. "phone","watch","tracker"
            service_uuids = d.get("service_uuids") or []

            # Anchor BT device to WiFi position.
            # If 3+ APs have resolved GPS coordinates, trilaterate for better
            # accuracy.  Otherwise fall back to single-anchor + RSSI offset.
            bt_lat, bt_lng, bt_acc = 0.0, 0.0, 0.0
            anchor_source = "rssi"

            trilat = self._trilaterate_bt(addr, rssi, resolved_wifi)
            if trilat:
                bt_lat, bt_lng, bt_acc = trilat
                anchor_source = "wifi_trilat"
            elif wifi_anchor and wifi_anchor.get("lat") and wifi_anchor.get("lng"):
                bt_dist = min(self._rssi_to_distance(rssi), 80.0)
                angle   = self._hash_to_angle(addr)
                bt_lat, bt_lng = self._offset_position(
                    wifi_anchor["lat"], wifi_anchor["lng"], bt_dist, angle
                )
                bt_acc   = wifi_anchor.get("accuracy", 50) + bt_dist
                anchor_source = "wifi_" + wifi_anchor.get("source", "wloc")
            elif lat and lng:
                dist   = self._rssi_to_distance(rssi)
                angle  = self._hash_to_angle(addr)
                bt_lat, bt_lng = self._offset_position(float(lat), float(lng), dist, angle)
                bt_acc = dist
                anchor_source = "gps_rssi"

            dev = self.known_devices.get(addr)
            if dev:
                dev.last_seen = now
                dev.rssi_history.append({"rssi": rssi, "ts": now})
                dev.rssi_history = dev.rssi_history[-100:]
                # Update bluehood fields if we now have better data
                if vendor and not dev.vendor:
                    dev.vendor = vendor
                if classified_type and not dev.classified_type:
                    dev.classified_type = classified_type
                if bt_subtype and not dev.bt_subtype:
                    dev.bt_subtype = bt_subtype
                if service_uuids and not dev.service_uuids:
                    dev.service_uuids = service_uuids
                if bt_lat and bt_lng:
                    dev.trail.append({
                        "lat": bt_lat, "lng": bt_lng,
                        "rssi": rssi, "ts": now,
                        "accuracy": bt_acc, "source": anchor_source,
                        "wifi_anchor": wifi_anchor["bssid"] if wifi_anchor else "",
                    })
                    dev.trail = dev.trail[-50:]
            else:
                trail_seed = [{
                    "lat": bt_lat, "lng": bt_lng,
                    "rssi": rssi, "ts": now,
                    "accuracy": bt_acc, "source": anchor_source,
                    "wifi_anchor": wifi_anchor["bssid"] if wifi_anchor else "",
                }] if (bt_lat and bt_lng) else []
                self.known_devices[addr] = TrackedDevice(
                    address=addr,
                    name=name,
                    person_name=d.get("person") or "nearby",
                    device_type=dtype,
                    first_seen=now,
                    last_seen=now,
                    rssi_history=[{"rssi": rssi, "ts": now}],
                    trail=trail_seed,
                    vendor=vendor,
                    bt_subtype=bt_subtype,
                    classified_type=classified_type,
                    service_uuids=service_uuids,
                )

        self._save_databases()

        result = {
            "ok": True,
            "ingested_bt": len(bt),
            "ingested_wifi": len(wifi),
            "resolved_wifi": sum(1 for w in resolved_wifi if w.get("lat")),
            "wifi_anchor": wifi_anchor,
            "tracked_devices": len(self.known_devices),
            "location": {"lat": float(lat), "lng": float(lng)} if lat else None,
        }
        logger.info(
            f"ingest_nearby: {len(bt)} BT + {len(wifi)} WiFi "
            f"({result['resolved_wifi']} WiFi resolved via MLS) "
            f"→ {len(self.known_devices)} tracked"
        )
        return result

    def get_map_data(self) -> dict:
        """Get all data needed for the radar map — includes all nodes."""
        # Aggregate node positions
        node_positions = {}
        for url, loc in self._node_locations.items():
            node_positions[url] = {
                "lat": loc.get("lat", 0),
                "lng": loc.get("lng", 0),
                "accuracy": loc.get("accuracy", 0),
                "last_seen": loc.get("ts", 0),
            }

        # Add node info from registry if available
        nodes_info = []
        try:
            from node_registry import get_node_registry

            registry = get_node_registry()
            for node in registry.get_all():
                nodes_info.append(
                    {
                        "node_id": node.node_id,
                        "name": node.name,
                        "online": node.is_online(),
                        "sensor_url": node.sensor_url,
                        "lat": node.last_gps_lat
                        or self._node_locations.get(node.sensor_url, {}).get("lat", 0),
                        "lng": node.last_gps_lng
                        or self._node_locations.get(node.sensor_url, {}).get("lng", 0),
                        "battery": node.battery_pct,
                    }
                )
        except ImportError:
            pass

        # Reverse map: device address → which node(s) see it (for correlation).
        device_nodes: dict[str, list] = {}
        for url, devs in self._node_devices.items():
            for addr in devs:
                device_nodes.setdefault(addr, []).append(url)

        # Build a lookup of node URL → GPS from the tracker's per-node cache
        # (falling back to registry GPS so host-detected devices get real pins).
        node_gps: dict[str, dict] = {}
        for url, loc in self._node_locations.items():
            node_gps[url] = {"lat": loc.get("lat", 0), "lng": loc.get("lng", 0)}
        try:
            from node_registry import get_node_registry

            for node in get_node_registry().get_all():
                if node.sensor_url and (node.last_gps_lat or node.last_gps_lng):
                    node_gps.setdefault(
                        node.sensor_url,
                        {"lat": node.last_gps_lat or 0, "lng": node.last_gps_lng or 0},
                    )
        except ImportError:
            pass

        # Per-device RSSI cache from the live scan (falls back to -100).
        last_rssi: dict[str, float] = {}
        for url, devs in self._node_devices.items():
            for addr, info in devs.items():
                last_rssi[addr] = info.get("rssi", -100)

        # Compute a stable map pin for each known device.
        # Priority order:
        #   1. Most recent trail entry that has a real GPS source (mls / wifi_mls / gps_rssi)
        #   2. Most recent trail entry with any lat/lng + small RSSI scatter offset
        #   3. Fallback: RSSI offset from the detecting node's current GPS
        devices_out = []
        for d in self.known_devices.values():
            lat, lng = 0, 0
            pin_accuracy = 0
            pin_source = "rssi"
            node_url = ""
            node_list = device_nodes.get(d.address, [])
            if node_list:
                node_url = node_list[0]
            rssi = last_rssi.get(d.address, -100) or -100

            if d.trail:
                # Prefer trail entries with a real MLS-derived fix
                mls_entries = [
                    pt for pt in d.trail
                    if pt.get("source") in ("mls", "wifi_mls") and pt.get("lat") and pt.get("lng")
                ]
                if mls_entries:
                    best_pt = mls_entries[-1]
                    lat = float(best_pt["lat"])
                    lng = float(best_pt["lng"])
                    pin_accuracy = float(best_pt.get("accuracy", 50))
                    pin_source = best_pt.get("source", "mls")
                else:
                    # Use last trail entry with any valid coords
                    for pt in reversed(d.trail):
                        if pt.get("lat") and pt.get("lng"):
                            anchor_lat = float(pt["lat"])
                            anchor_lng = float(pt["lng"])
                            # Small scatter (≤15 m) so co-located devices don't stack
                            scatter_dist = min(15.0, self._rssi_to_distance(rssi) * 0.15)
                            angle = self._hash_to_angle(d.address)
                            lat, lng = self._offset_position(anchor_lat, anchor_lng, scatter_dist, angle)
                            pin_accuracy = float(pt.get("accuracy", scatter_dist))
                            pin_source = pt.get("source", "gps_rssi")
                            break

            if not (lat and lng):
                # Fallback: place relative to current node/phone GPS with full RSSI offset.
                gps = node_gps.get(node_url) or {
                    "lat": self._location_cache.get("lat", 0),
                    "lng": self._location_cache.get("lng", 0),
                }
                if gps and gps.get("lat") and gps.get("lng"):
                    dist = self._rssi_to_distance(rssi)
                    angle = self._hash_to_angle(d.address)
                    lat, lng = self._offset_position(
                        float(gps["lat"]), float(gps["lng"]), dist, angle
                    )
                    pin_accuracy = dist
                    pin_source = "rssi"

            devices_out.append(
                {
                    "address": d.address,
                    "name": d.name,
                    "person": d.person_name,
                    "type": d.device_type,
                    "rssi": rssi,
                    "lat": lat,
                    "lng": lng,
                    "accuracy": round(pin_accuracy, 1),
                    "geo_source": pin_source,
                    "last_seen": d.last_seen,
                    "nodes": node_list,
                    "node": node_url,
                    "trail_count": len(self.trails.get(d.person_name, [])),
                    # Bluehood enrichment
                    "vendor": d.vendor,
                    "bt_subtype": d.bt_subtype,
                    "classified_type": d.classified_type,
                }
            )
        devices_out.sort(key=lambda x: x["last_seen"], reverse=True)

        return {
            "location": {
                "lat": self._location_cache.get("lat", 0),
                "lng": self._location_cache.get("lng", 0),
                "accuracy": self._location_cache.get("accuracy", 0),
            },
            "nodes": nodes_info,
            "node_positions": node_positions,
            "node_devices": self._node_devices,
            "device_nodes": device_nodes,
            "trails": self.trails,
            "devices": devices_out,
            "sightings_count": len(self.sightings),
            "people_count": len(set(s.name for s in self.sightings)),
        }

    def get_recent_activity(self, minutes: int = 30) -> dict:
        """Get activity summary for the last N minutes."""
        cutoff = time.time() - (minutes * 60)
        with self._lock:
            recent = [s for s in self.sightings if s.timestamp > cutoff]

        by_person = {}
        for s in recent:
            if s.name not in by_person:
                by_person[s.name] = {"count": 0, "devices": set(), "locations": []}
            by_person[s.name]["count"] += 1
            if s.device_address:
                by_person[s.name]["devices"].add(s.device_address)
            by_person[s.name]["locations"].append({"lat": s.lat, "lng": s.lng})

        return {
            "minutes": minutes,
            "total_sightings": len(recent),
            "people": {
                name: {
                    "count": info["count"],
                    "devices": list(info["devices"]),
                    "locations": info["locations"],
                }
                for name, info in by_person.items()
            },
        }

    def save(self):
        """Force save all databases."""
        self._save_databases()


# ── Singleton ────────────────────────────────────────────────────────────
_tracker: Optional[PersonTracker] = None


def get_person_tracker() -> PersonTracker:
    global _tracker
    if _tracker is None:
        _tracker = PersonTracker()
    return _tracker
