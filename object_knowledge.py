"""Object Knowledge & Learning for Lilly AI

Tracks detected objects, correlates them with Bluetooth/WiFi context,
and allows the user to teach Lilly names and identities.

Example:
  YOLO detects a "person" near an iPhone named "XXX's iPhone"
  → User says "this is XXX"
  → System stores: object_id → {name: "XXX", type: "person", context: {bt: [...], wifi: [...]}}
  → Future detections of that person are recognized as "XXX"
"""

import json
import time
from pathlib import Path
from typing import Any

WORKSPACE = Path(__file__).parent
OBJECT_KNOWLEDGE_FILE = WORKSPACE / "object_knowledge.json"
OBJECT_DETECTION_LOG = WORKSPACE / "object_detection_log.jsonl"


def _load_knowledge() -> dict:
    if OBJECT_KNOWLEDGE_FILE.exists():
        try:
            return json.loads(OBJECT_KNOWLEDGE_FILE.read_text())
        except Exception:
            pass
    return {"objects": {}, "contexts": {}}


def _save_knowledge(knowledge: dict):
    try:
        OBJECT_KNOWLEDGE_FILE.write_text(json.dumps(knowledge, indent=2))
    except Exception:
        pass


def _log_detection(detection: dict):
    try:
        with open(OBJECT_DETECTION_LOG, "a") as f:
            f.write(json.dumps({"ts": time.time(), "detection": detection}) + "\n")
    except Exception:
        pass


def record_detection(detections: list[dict], context: dict | None = None):
    """Record YOLO detections with optional BT/WiFi context."""
    knowledge = _load_knowledge()
    ts = time.time()

    for det in detections:
        label = det.get("label", "unknown")
        obj_id = det.get("object_id") or f"{label}_{int(ts * 1000)}"

        entry = {
            "label": label,
            "confidence": det.get("confidence", 0),
            "first_seen": ts,
            "last_seen": ts,
            "seen_count": 1,
            "context": context or {},
            "learned_name": None,
            "attributes": det.get("attributes", {}),
        }

        if obj_id in knowledge["objects"]:
            existing = knowledge["objects"][obj_id]
            existing["last_seen"] = ts
            existing["seen_count"] += 1
            if context:
                existing["context"] = _merge_context(
                    existing.get("context", {}), context
                )
        else:
            knowledge["objects"][obj_id] = entry

        _log_detection(
            {
                "object_id": obj_id,
                "label": label,
                "ts": ts,
                "context": context,
            }
        )

    _save_knowledge(knowledge)
    return knowledge


def learn_object(object_id: str, name: str, attributes: dict | None = None) -> dict:
    """Teach Lilly a name for a detected object."""
    knowledge = _load_knowledge()
    obj = knowledge["objects"].get(object_id)

    if not obj:
        return {"ok": False, "error": "Object not found", "object_id": object_id}

    obj["learned_name"] = name
    if attributes:
        obj["attributes"].update(attributes)

    # Index by learned name for fast lookup
    knowledge["contexts"][name.lower()] = object_id

    _save_knowledge(knowledge)
    return {
        "ok": True,
        "object_id": object_id,
        "name": name,
        "label": obj["label"],
        "message": f"Learned: {obj['label']} is now known as {name}",
    }


def recognize_object(context: dict) -> dict | None:
    """Try to identify an object from BT/WiFi/device context."""
    knowledge = _load_knowledge()

    # Check BT device names
    bt_devices = context.get("bluetooth", [])
    for dev in bt_devices:
        name = dev.get("name", "").lower()
        if name in knowledge["contexts"]:
            obj_id = knowledge["contexts"][name]
            return knowledge["objects"].get(obj_id)

    # Check WiFi SSIDs
    wifi_networks = context.get("wifi", [])
    for net in wifi_networks:
        ssid = net.get("ssid", "").lower()
        if ssid in knowledge["contexts"]:
            obj_id = knowledge["contexts"][ssid]
            return knowledge["objects"].get(obj_id)

    return None


def get_known_objects() -> list[dict]:
    """Get all learned objects."""
    knowledge = _load_knowledge()
    result = []
    for obj_id, obj in knowledge["objects"].items():
        if obj.get("learned_name"):
            result.append(
                {
                    "object_id": obj_id,
                    "name": obj["learned_name"],
                    "label": obj["label"],
                    "first_seen": obj["first_seen"],
                    "last_seen": obj["last_seen"],
                    "seen_count": obj["seen_count"],
                }
            )
    return result


def get_recent_detections(limit: int = 20) -> list[dict]:
    """Get recent detection log entries."""
    if not OBJECT_DETECTION_LOG.exists():
        return []
    try:
        lines = OBJECT_DETECTION_LOG.read_text().splitlines()
        entries = [json.loads(line) for line in lines if line.strip()]
        return sorted(entries, key=lambda x: x.get("ts", 0), reverse=True)[:limit]
    except Exception:
        return []


def _merge_context(old: dict, new: dict) -> dict:
    """Merge detection contexts without duplicates."""
    merged = dict(old)
    for key, val in new.items():
        if key not in merged:
            merged[key] = val
        elif isinstance(val, list) and isinstance(merged[key], list):
            merged[key] = list(set(merged[key] + val))
        elif isinstance(val, dict) and isinstance(merged[key], dict):
            merged[key].update(val)
    return merged
