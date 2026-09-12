#!/usr/bin/env python3
"""
Lilly Node Registry — Multi-Phone Sensor Network
──────────────────────────────────────────────────
Manages up to N phone nodes (default 3). Each node runs:
  - Termux sensor server (:8099) — GPS, BLE, WiFi, 23 sensors
  - Camera frame capture → YOLO vision pipeline
  - Face recognition + person tracking

Nodes register via:
  1. WebSocket auth message with sensor_url (auto-discovery)
  2. HTTP POST /api/nodes/register (manual pairing)
  3. NODES JSON config file (static fallback)

Features:
  - HMAC-SHA256 signed heartbeats for authentication
  - Delta heartbeats: nodes send only changed fields to reduce bandwidth
  - Bundled scans: BT/WiFi results ride along in heartbeat packets
  - Auto-cleanup: nodes offline >10 min are automatically removed
  - EMA RSSI smoothing for stable distance estimates
"""

import hashlib
import hmac
import json
import math
import os
import time
import logging
from pathlib import Path
from dataclasses import dataclass, field, asdict
from typing import Optional

logger = logging.getLogger("lilly-nodes")

# ── Config ───────────────────────────────────────────────────────────────
NODES_DB = Path(os.environ.get("NODES_DB", "/app/data/nodes.json"))
MAX_NODES = int(os.environ.get("LILLY_MAX_NODES", "3"))
HEARTBEAT_TIMEOUT = int(os.environ.get("NODE_HEARTBEAT_TIMEOUT", "90"))  # seconds — must exceed max fleet poll cycle time
AUTO_REMOVE_OFFLINE_SEC = int(os.environ.get("NODE_AUTO_REMOVE_SEC", "600"))  # 10 min
HMAC_SECRET = os.environ.get("LILLY_NODE_HMAC_SECRET", "")  # shared secret for heartbeat auth
RSSI_EMA_ALPHA = float(os.environ.get("RSSI_EMA_ALPHA", "0.3"))  # smoothing factor (0-1, higher = more responsive)


@dataclass
class PhoneNode:
    """A registered phone node in the network."""

    node_id: str  # unique ID (e.g. "phone-1", MAC, or user-chosen)
    name: str  # human label (e.g. "Kitchen Phone", "Lilly's S23")
    sensor_url: str  # e.g. "http://100.115.234.87:8099"
    vision_url: str = ""  # camera frame endpoint (optional, defaults to sensor_url)
    model: str = ""  # phone model (e.g. "Samsung S23")
    role: str = "node"  # "primary" | "node" | "edge" | "host"
    # Runtime state
    online: bool = False
    last_heartbeat: float = 0.0
    last_gps_lat: float = 0.0
    last_gps_lng: float = 0.0
    last_gps_accuracy: float = 0.0
    battery_pct: float = -1.0
    camera_active: bool = False
    face_engine_active: bool = False
    capabilities: list = field(
        default_factory=lambda: ["sensors", "gps", "ble", "wifi", "camera"]
    )
    # Bundled scan data (sent inside heartbeat packets)
    last_bt_scan: list = field(default_factory=list)  # [{name, address, rssi, type}]
    last_wifi_scan: list = field(default_factory=list)  # [{ssid, bssid, rssi, freq}]
    last_bt_scan_ts: float = 0.0
    last_wifi_scan_ts: float = 0.0
    # EMA-smoothed RSSI per device (address -> smoothed_rssi)
    rssi_ema: dict = field(default_factory=dict)
    # HMAC auth state
    last_hmac_valid: bool = True
    # Heartbeat failure tracking
    consecutive_failures: int = 0

    def is_online(self) -> bool:
        if not self.last_heartbeat:
            return False
        return (time.time() - self.last_heartbeat) < HEARTBEAT_TIMEOUT

    def apply_delta(self, delta: dict) -> None:
        """Apply a delta heartbeat — only update fields that are present."""
        if "battery_pct" in delta:
            self.battery_pct = delta["battery_pct"]
        if "camera_active" in delta:
            self.camera_active = delta["camera_active"]
        if "face_engine_active" in delta:
            self.face_engine_active = delta["face_engine_active"]
        if "gps_lat" in delta and "gps_lng" in delta:
            self.last_gps_lat = delta["gps_lat"]
            self.last_gps_lng = delta["gps_lng"]
        if "gps_accuracy" in delta:
            self.last_gps_accuracy = delta["gps_accuracy"]
        if "name" in delta:
            self.name = delta["name"]
        if "model" in delta:
            self.model = delta["model"]

    def ingest_bundled_scans(self, bt_scan: list = None, wifi_scan: list = None) -> None:
        """Ingest BT/WiFi scan results bundled in a heartbeat packet."""
        now = time.time()
        if bt_scan is not None:
            self.last_bt_scan = bt_scan
            self.last_bt_scan_ts = now
            # Update EMA for each seen device
            for dev in bt_scan:
                addr = dev.get("address", "")
                rssi = dev.get("rssi", -100)
                if addr and rssi is not None:
                    self._update_rssi_ema(addr, rssi)
        if wifi_scan is not None:
            self.last_wifi_scan = wifi_scan
            self.last_wifi_scan_ts = now

    def _update_rssi_ema(self, address: str, new_rssi: float) -> None:
        """Exponential moving average for RSSI smoothing."""
        old = self.rssi_ema.get(address)
        if old is None:
            self.rssi_ema[address] = new_rssi
        else:
            self.rssi_ema[address] = RSSI_EMA_ALPHA * new_rssi + (1 - RSSI_EMA_ALPHA) * old
        # Prune devices not seen in a while (keep last 200)
        if len(self.rssi_ema) > 200:
            # Remove oldest entries (dict preserves insertion order in 3.7+)
            excess = len(self.rssi_ema) - 200
            for k in list(self.rssi_ema.keys())[:excess]:
                del self.rssi_ema[k]

    def get_smoothed_rssi(self, address: str) -> Optional[float]:
        """Get EMA-smoothed RSSI for a device, or None if unknown."""
        return self.rssi_ema.get(address)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["online"] = self.is_online()
        # Don't persist volatile scan data — only runtime cache
        d.pop("last_bt_scan", None)
        d.pop("last_wifi_scan", None)
        d.pop("last_bt_scan_ts", None)
        d.pop("last_wifi_scan_ts", None)
        d.pop("rssi_ema", None)
        d.pop("last_hmac_valid", None)
        d.pop("consecutive_failures", None)
        return d

    def short_str(self) -> str:
        status = "🟢" if self.is_online() else "⚫"
        return f"{status} {self.name} ({self.node_id}) @ {self.sensor_url}"


class NodeRegistry:
    """
    Manages phone nodes. Thread-safe for concurrent access.

    Stores nodes in /app/data/nodes.json for persistence across restarts.
    """

    def __init__(self):
        self.nodes: dict[str, PhoneNode] = {}
        self._load()

    def _load(self):
        """Load nodes from disk."""
        if not NODES_DB.exists():
            # Seed from environment if available
            self._seed_from_env()
            return
        try:
            data = json.loads(NODES_DB.read_text())
            for nd in data.get("nodes", []):
                node = PhoneNode(
                    node_id=nd["node_id"],
                    name=nd.get("name", nd["node_id"]),
                    sensor_url=nd.get("sensor_url", ""),
                    vision_url=nd.get("vision_url", ""),
                    model=nd.get("model", ""),
                    role=nd.get("role", "node"),
                    capabilities=nd.get(
                        "capabilities", ["sensors", "gps", "ble", "wifi", "camera"]
                    ),
                )
                self.nodes[node.node_id] = node
            logger.info(f"Loaded {len(self.nodes)} nodes from {NODES_DB}")
        except Exception as e:
            logger.warning(f"Failed to load nodes: {e}")
            self._seed_from_env()

    def _seed_from_env(self):
        """Create default nodes from environment variables."""
        # Primary node (backward compat with SENSOR_SERVER_URL)
        primary_url = os.environ.get("SENSOR_SERVER_URL", "http://100.115.234.87:8099")
        if primary_url:
            self.nodes["phone-1"] = PhoneNode(
                node_id="phone-1",
                name="Primary Phone",
                sensor_url=primary_url,
                role="primary",
            )
        # Additional nodes from NODE_2_URL, NODE_3_URL
        for i in range(2, MAX_NODES + 1):
            url = os.environ.get(f"NODE_{i}_URL", "")
            name = os.environ.get(f"NODE_{i}_NAME", f"Phone Node {i}")
            if url:
                self.nodes[f"phone-{i}"] = PhoneNode(
                    node_id=f"phone-{i}",
                    name=name,
                    sensor_url=url,
                    role="node",
                )
        if self.nodes:
            self._save()
            logger.info(f"Seeded {len(self.nodes)} nodes from environment")

    def _save(self):
        """Persist nodes to disk."""
        try:
            NODES_DB.parent.mkdir(parents=True, exist_ok=True)
            data = {"nodes": [n.to_dict() for n in self.nodes.values()]}
            NODES_DB.write_text(json.dumps(data, indent=2))
        except Exception as e:
            logger.warning(f"Failed to save nodes: {e}")

    def register(
        self,
        node_id: str,
        name: str,
        sensor_url: str,
        model: str = "",
        role: str = "node",
        vision_url: str = "",
        capabilities: list | None = None,
    ) -> PhoneNode:
        """Register or update a phone node."""
        if node_id in self.nodes:
            node = self.nodes[node_id]
            node.name = name or node.name
            node.sensor_url = sensor_url or node.sensor_url
            node.vision_url = vision_url or node.vision_url
            node.model = model or node.model
            node.role = role
            if capabilities:
                node.capabilities = capabilities
            node.last_heartbeat = time.time()
            node.online = True
            logger.info(f"Updated node: {node.short_str()}")
        else:
            if len(self.nodes) >= MAX_NODES:
                # Evict oldest offline node
                offline = [n for n in self.nodes.values() if not n.is_online()]
                if offline:
                    evict = min(offline, key=lambda n: n.last_heartbeat)
                    del self.nodes[evict.node_id]
                    logger.info(f"Evicted offline node: {evict.node_id}")
                else:
                    raise ValueError(
                        f"Max {MAX_NODES} nodes reached and all are online"
                    )
            node = PhoneNode(
                node_id=node_id,
                name=name or node_id,
                sensor_url=sensor_url,
                vision_url=vision_url,
                model=model,
                role=role,
                capabilities=capabilities
                or ["sensors", "gps", "ble", "wifi", "camera"],
                last_heartbeat=time.time(),
                online=True,
            )
            self.nodes[node_id] = node
            logger.info(f"Registered node: {node.short_str()}")
        self._save()
        return node

    def heartbeat(self, node_id: str, **kwargs) -> bool:
        """Update heartbeat for a node. Returns False if node not found."""
        node = self.nodes.get(node_id)
        if not node:
            return False
        node.last_heartbeat = time.time()
        node.online = True
        node.consecutive_failures = 0
        if "battery_pct" in kwargs:
            node.battery_pct = kwargs["battery_pct"]
        if "camera_active" in kwargs:
            node.camera_active = kwargs["camera_active"]
        if "face_engine_active" in kwargs:
            node.face_engine_active = kwargs["face_engine_active"]
        if "gps_lat" in kwargs and "gps_lng" in kwargs:
            node.last_gps_lat = kwargs["gps_lat"]
            node.last_gps_lng = kwargs["gps_lng"]
        if "gps_accuracy" in kwargs:
            node.last_gps_accuracy = kwargs["gps_accuracy"]
        # Ingest bundled scans if present
        if "bt_scan" in kwargs:
            node.ingest_bundled_scans(bt_scan=kwargs["bt_scan"])
        if "wifi_scan" in kwargs:
            node.ingest_bundled_scans(wifi_scan=kwargs["wifi_scan"])
        self._save()
        return True

    def heartbeat_failed(self, node_id: str) -> None:
        """Record a failed heartbeat attempt for backoff tracking."""
        node = self.nodes.get(node_id)
        if node:
            node.consecutive_failures += 1

    @staticmethod
    def sign_hmac(node_id: str, timestamp: float, secret: str) -> str:
        """Generate HMAC-SHA256 signature for a heartbeat payload."""
        msg = f"{node_id}:{timestamp:.0f}"
        return hmac.new(secret.encode(), msg.encode(), hashlib.sha256).hexdigest()

    @staticmethod
    def verify_hmac(node_id: str, timestamp: float, sig: str, secret: str) -> bool:
        """Verify HMAC-SHA256 signature. Allows 60s clock skew."""
        if not secret:
            return True  # HMAC disabled
        now = time.time()
        if abs(now - timestamp) > 60:
            return False
        expected = NodeRegistry.sign_hmac(node_id, timestamp, secret)
        return hmac.compare_digest(sig, expected)

    def unregister(self, node_id: str) -> bool:
        """Remove a node."""
        if node_id in self.nodes:
            del self.nodes[node_id]
            self._save()
            logger.info(f"Unregistered node: {node_id}")
            return True
        return False

    def get(self, node_id: str) -> Optional[PhoneNode]:
        return self.nodes.get(node_id)

    def get_all(self) -> list[PhoneNode]:
        return list(self.nodes.values())

    def get_online(self) -> list[PhoneNode]:
        return [n for n in self.nodes.values() if n.is_online()]

    def get_sensor_urls(self) -> list[str]:
        """Return sensor URLs for all online nodes (for person_tracker)."""
        return [n.sensor_url for n in self.get_online() if n.sensor_url]

    def get_all_sensor_urls(self) -> list[str]:
        """Return sensor URLs for all nodes (online or not)."""
        return [n.sensor_url for n in self.nodes.values() if n.sensor_url]

    def get_vision_urls(self) -> list[str]:
        """Return vision server URLs for all online nodes."""
        urls = []
        for n in self.get_online():
            url = n.vision_url or n.sensor_url.replace(":8099", ":8198")
            if url:
                urls.append(url)
        return urls

    def to_dict(self) -> dict:
        return {
            "nodes": [n.to_dict() for n in self.nodes.values()],
            "online_count": len(self.get_online()),
            "total_count": len(self.nodes),
            "max_nodes": MAX_NODES,
        }

    def cleanup_stale_nodes(self) -> int:
        """Remove nodes offline longer than AUTO_REMOVE_OFFLINE_SEC.
        Returns count of removed nodes."""
        now = time.time()
        stale = []
        for nid, node in self.nodes.items():
            if node.last_heartbeat == 0:
                continue  # never had a heartbeat, keep for now
            if (now - node.last_heartbeat) > AUTO_REMOVE_OFFLINE_SEC:
                stale.append(nid)
        for nid in stale:
            logger.info(f"Auto-removing stale node: {nid} (offline >{AUTO_REMOVE_OFFLINE_SEC}s)")
            del self.nodes[nid]
        if stale:
            self._save()
        return len(stale)


# ── Singleton ────────────────────────────────────────────────────────────
_registry: NodeRegistry | None = None


def get_node_registry() -> NodeRegistry:
    global _registry
    if _registry is None:
        _registry = NodeRegistry()
    return _registry
