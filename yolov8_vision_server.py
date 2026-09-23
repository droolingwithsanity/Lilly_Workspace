#!/usr/bin/env python3
"""
Lilly Vision — YOLOv8 Multi-Agent Commentary with Edge TTS
Multiple AI personalities comment on what the camera sees, with unique voices.
"""

import base64
import hashlib
import json
import os
import time
import random
import logging
import asyncio
import threading
import tempfile
from pathlib import Path
from contextlib import asynccontextmanager
from typing import Any, Optional, cast

import cv2
import numpy as np
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, FileResponse
from ultralytics import YOLO

# Face recognition (optional — loaded lazily)
try:
    from face_recognition_engine import get_face_engine

    FACE_ENGINE = None  # initialized on first use
except ImportError:
    FACE_ENGINE = None
    log_face = logging.getLogger("lilly-vision")

# Blink detection (optional — loaded lazily)
try:
    from blink_detector import get_blink_detector

    BLINK_DETECTOR = None  # initialized on first use
except ImportError:
    BLINK_DETECTOR = None
    log_blink = logging.getLogger("lilly-vision")

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("lilly-vision")

MODEL: Optional[YOLO] = None
# 0.25 keeps sign/light/vehicle boxes alive at dusk + rain; tier floors
# (below) filter the noise so the drive overlay stays clean.
CONF_THRESHOLD = float(os.environ.get("CONF_THRESHOLD", "0.25"))
# Prefer Open Images V7 (601 classes) over COCO (80 classes) for broader detection
MODEL_NAME = os.environ.get("YOLO_MODEL", "yolov8n-oiv7.pt")
MODEL_PATH = os.environ.get("YOLO_MODEL_PATH", "")
IOU_THRESHOLD = float(os.environ.get("YOLO_IOU_THRESHOLD", "0.45"))
MAX_DETECTIONS = int(os.environ.get("YOLO_MAX_DETECTIONS", "300"))

# ── Admin class toggles (training console at :8098/training) ─────────────
# vision_class_config.json holds {"classes": {"person": true, "car": false},
# "default": true} — classes switched OFF here are dropped from detection
# results before any enrichment so the overlay / Lilly never see them.
CLASS_CONFIG_PATH = Path(__file__).parent / "vision_class_config.json"


def _class_toggles() -> dict:
    try:
        cfg = json.loads(CLASS_CONFIG_PATH.read_text(encoding="utf-8"))
        return isinstance(cfg, dict) and cfg.get("classes") or {}
    except Exception:
        return {}


def _class_default_enabled() -> bool:
    try:
        cfg = json.loads(CLASS_CONFIG_PATH.read_text(encoding="utf-8"))
        return bool(cfg.get("default", True))
    except Exception:
        return True


def _filter_disabled_classes(detections: list) -> list:
    """Drop detections whose class is toggled OFF in the admin console."""
    toggles = _class_toggles()
    if not toggles:
        return detections
    default = _class_default_enabled()
    kept = []
    for d in detections:
        label = str(d.get("label") or d.get("class") or "").strip().lower()
        if label:
            if label in toggles:
                if toggles[label]:
                    kept.append(d)
            elif default:
                kept.append(d)
        else:
            kept.append(d)  # no label -> keep, can't be turned off
    return kept


# ── Per-frame cost controls ────────────────────────────────────────────
# The heavy identification stages (InsightFace, MediaPipe blink, the vehicle
# classifier) only need to run a few times a second — their results are cached
# and re-applied to boxes in between, so the overlay stays smooth on CPU.
MAX_INFER_SIDE = int(os.environ.get("MAX_INFER_SIDE", "960"))  # cap YOLO input
FACE_ENRICH_INTERVAL = float(os.environ.get("FACE_ENRICH_INTERVAL", "2.0"))
BLINK_INTERVAL = float(os.environ.get("BLINK_INTERVAL", "1.5"))
CAR_CLASSIFY_INTERVAL = float(os.environ.get("CAR_CLASSIFY_INTERVAL", "2.0"))

# Last-enrichment caches: (ts, items) — items used to keep names on boxes
# while the expensive engine is on cooldown.
_face_enrich_cache = {"ts": 0.0, "items": []}
_vehicle_enrich_cache = {"ts": -10.0, "items": []}
_blink_ts: float = 0.0
_face_ts: float = 0.0

# ── Unknown-face OSINT (social-media identification of strangers) ─────────
# The vision server samples unsolved person crops, hands them to lilly-ai
# (which runs scrapling reverse-face search + osint_agents), then re-stamps
# the discovered name back onto the box. Entirely opt-in.
UNKNOWN_FACE_SAMPLE_INTERVAL = float(
    os.environ.get("UNKNOWN_FACE_SAMPLE_INTERVAL", "8.0")
)
UNKNOWN_FACE_MAX = int(os.environ.get("UNKNOWN_FACE_MAX", "12"))
OSINT_PUSH_URL = os.environ.get(
    "OSINT_PUSH_URL", ""
)  # lilly endpoint (empty = disabled)


def _lilly_base() -> str:
    """Base URL of lilly-ai, derived from the OSINT push endpoint."""
    if OSINT_PUSH_URL:
        return OSINT_PUSH_URL.split("/api/vision/faces/osint_unknown")[0]
    return os.environ.get("LILLY_AI_URL", "http://127.0.0.1:8098").rstrip("/")


def _post_identity_event(
    name: str, conf: float, source: str, extra: dict | None = None
):
    """Forward a Tier1 FAISS confirm to lilly-ai (fire-and-forget thread)."""
    base = _lilly_base()
    if not name or not base:
        return
    payload = {
        "name": name,
        "confidence": conf,
        "source": source,
        "face_id": (extra or {}).get("face_id", ""),
        "social_accounts": (extra or {}).get("social_accounts", []),
        "sources": (extra or {}).get("sources", []),
    }

    def _send():
        try:
            import httpx

            httpx.post(f"{base}/api/faces/events", json=payload, timeout=8.0)
        except Exception as e:
            log.debug(f"identity event post failed: {e}")

    try:
        threading.Thread(target=_send, daemon=True).start()
    except Exception:
        pass


OSINT_PUSH_INTERVAL = float(os.environ.get("OSINT_PUSH_INTERVAL", "15.0"))

_unknown_faces: list[dict] = []  # unsolved people awaiting OSINT (id-stable)
_osint_results: dict[
    str, dict
] = {}  # id -> {name, social_accounts, sources, confidence, person_box, ts}


def _iou(a: dict, b: dict) -> float:
    ax1, ay1 = a["x"], a["y"]
    ax2, ay2 = a["x"] + a["w"], a["y"] + a["h"]
    bx1, by1 = b["x"], b["y"]
    bx2, by2 = b["x"] + b["w"], b["y"] + b["h"]
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    inter = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
    aa = (ax2 - ax1) * (ay2 - ay1)
    bb = (bx2 - bx1) * (by2 - by1)
    denom = aa + bb - inter
    return inter / denom if denom > 0 else 0.0


def _apply_cached_ids(detections: list, cache: dict, enrich_flag: str) -> list:
    """Re-stamp previously identified names onto boxes while the engine cools down."""
    for det in detections:
        if det.get(enrich_flag):
            continue
        best, best_iou = None, 0.15
        for item in cache["items"]:
            # A label that equals the class means "nothing to rename"; items
            # without a label (kps-only face restamp) are still usable.
            if (
                item.get("label")
                and item["label"].lower() == str(det.get("class", "")).lower()
            ):
                continue
            if not item.get("label") and "kps" not in item:
                continue
            i = _iou(item.get("box", {}), det)
            if i > best_iou:
                best, best_iou = item, i
        if best:
            for k, v in best.items():
                if k == "box":
                    continue
                if v is not None:
                    det[k] = v
            det.setdefault("original_label", det.get("class"))
    return detections


# ── Unknown-face OSINT plumbing ────────────────────────────────────────────
def _person_box(d: dict) -> dict:
    return {
        "x": d.get("x", 0),
        "y": d.get("y", 0),
        "w": d.get("w", 0),
        "h": d.get("h", 0),
    }


def _box_center(box: dict) -> tuple:
    return (
        box.get("x", 0) + box.get("w", 0) / 2,
        box.get("y", 0) + box.get("h", 0) / 2,
    )


def _face_cache_items(detections: list) -> list:
    """Build the restamp cache for person detections (names + kps persist)."""
    items = []
    for d in detections:
        is_person = (
            d.get("label", "").lower() in PERSON_KEYWORDS
            or d.get("original_label", "").lower() in PERSON_KEYWORDS
        )
        if not is_person:
            continue
        item = {
            "box": _person_box(d),
            "kps": d.get("kps"),
            "face_confidence": d.get("face_confidence"),
        }
        if d.get("original_label") == "person":
            item["label"] = d.get("label")
        if d.get("face_source") == "osint":
            item["face_source"] = "osint"
            item["social_accounts"] = d.get("social_accounts")
            item["osint_sources"] = d.get("osint_sources")
        items.append({k: v for k, v in item.items() if v is not None})
    return items


def _crop_face_b64(frame, box: dict, kps=None) -> str:
    """Encode a face/upper-body JPEG crop for reverse-face search."""
    h, w = frame.shape[:2]
    if kps:
        pts = np.array([[k[0] * w, k[1] * h] for k in kps], dtype=np.float32)
        x1, y1 = int(pts[:, 0].min()), int(pts[:, 1].min())
        x2, y2 = int(pts[:, 0].max()), int(pts[:, 1].max())
    else:
        x1, y1 = int(box["x"] * w), int(box["y"] * h)
        x2, y2 = int((box["x"] + box["w"]) * w), int((box["y"] + box["h"]) * h)
    pw, ph = max(int((x2 - x1) * 0.3), 4), max(int((y2 - y1) * 0.25), 4)
    x1, y1 = max(0, x1 - pw), max(0, y1 - ph)
    x2, y2 = min(w, x2 + pw), min(h, y2 + ph)
    crop = frame[y1:y2, x1:x2]
    if crop.size == 0:
        return ""
    crop = cv2.resize(crop, (256, 256), interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", crop, [int(cv2.IMWRITE_JPEG_QUALITY), 72])
    return base64.b64encode(buf.tobytes()).decode() if ok else ""


def _sample_unknown_faces(frame, detections: list, now: float):
    """Queue unsolved person crops (stable per-identity) for lilly to OSINT."""
    if not OSINT_PUSH_URL:
        return
    for det in detections:
        if (det.get("label") or "").lower() not in PERSON_KEYWORDS:
            continue
        if not det.get("kps") or det.get("face_source") == "osint":
            continue
        box = _person_box(det)
        cx, cy = _box_center(box)
        entry = next(
            (
                e
                for e in _unknown_faces
                if abs(_box_center(e["person_box"])[0] - cx) < 0.06
                and abs(_box_center(e["person_box"])[1] - cy) < 0.06
            ),
            None,
        )
        if entry is None:
            entry = {
                "id": "uf_" + hashlib.sha1(json.dumps(box).encode()).hexdigest()[:12],
                "person_box": box,
                "kps": det.get("kps"),
                "crop_b64": "",
                "sent_ts": 0.0,
                "osint_ts": 0.0,
                "resolved": None,
            }
            _unknown_faces.append(entry)
        if now - entry["osint_ts"] >= UNKNOWN_FACE_SAMPLE_INTERVAL:
            entry["person_box"] = box
            entry["kps"] = det.get("kps")
            entry["crop_b64"] = _crop_face_b64(frame, box, det.get("kps"))
    kept = [
        e
        for e in _unknown_faces
        if e.get("resolved") is not None or now - e.get("osint_ts", 0) < 600
    ]
    kept.sort(key=lambda e: e.get("ts", 0))
    _unknown_faces[:] = kept[-UNKNOWN_FACE_MAX:]


def _apply_osint_results(detections: list, now: float) -> list:
    """Rename unsolved persons using results lilly pushed back."""
    for det in detections:
        if (det.get("label") or "").lower() not in PERSON_KEYWORDS:
            continue
        box = _person_box(det)
        for res_id, res in list(_osint_results.items()):
            if now - res.get("ts", 0) > 600:
                _osint_results.pop(res_id, None)
                continue
            if _iou(res.get("person_box", {}), box) >= 0.3:
                det["label"] = res.get("name") or "unknown"
                det["original_label"] = "person"
                det["face_confidence"] = res.get("confidence")
                det["face_source"] = "osint"
                det["social_accounts"] = res.get("social_accounts", [])
                det["osint_sources"] = res.get("sources", [])
                det["face_images"] = res.get("images", [])
                for e in _unknown_faces:
                    if e.get("id") == res_id:
                        e["resolved"] = det["label"]
                        e["osint_ts"] = now
                break
    return detections


async def _osint_push_loop():
    """Push freshly sampled unknown faces to lilly-ai (scrapidy/OSINT tier)."""
    import httpx

    async with httpx.AsyncClient(timeout=20.0) as client:
        while True:
            try:
                now = time.time()
                for e in _unknown_faces:
                    if e.get("sent_ts") or now - e.get("ts", 0) > 120:
                        continue
                    if not e.get("crop_b64"):
                        continue
                    try:
                        r = await client.post(
                            OSINT_PUSH_URL,
                            json={
                                "id": e["id"],
                                "person_box": e["person_box"],
                                "kps": e["kps"],
                                "crop_b64": e["crop_b64"],
                                "ts": e["ts"],
                            },
                        )
                        e["sent_ts"] = now
                        if r.status_code == 200 and not r.json().get("handled"):
                            e["osint_ts"] = (
                                now  # OSINT disabled upstream — cooldown instead of spam
                            )
                    except Exception as exc:
                        log.debug(f"OSINT push failed: {exc}")
                        e["ts"] = now
            except Exception as exc:
                log.debug(f"OSINT push loop error: {exc}")
            await asyncio.sleep(OSINT_PUSH_INTERVAL)


def _resize_for_inference(frame):
    """Downscale huge camera frames for YOLO; returns (work_frame, sx, sy)."""
    h, w = frame.shape[:2]
    if max(h, w) <= MAX_INFER_SIDE:
        return frame, 1.0, 1.0
    scale = MAX_INFER_SIDE / max(h, w)
    nw, nh = max(1, int(w * scale)), max(1, int(h * scale))
    return cv2.resize(frame, (nw, nh), interpolation=cv2.INTER_AREA), w / nw, h / nh


PERSON_KEYWORDS = {"person", "human", "face", "man", "woman"}
VEHICLE_KEYWORDS = {
    "car",
    "truck",
    "bus",
    "motorcycle",
    "bicycle",
    "motorbike",
    "train",
    "airplane",
    "boat",
}
DEVICE_KEYWORDS = {
    "laptop",
    "cell phone",
    "tv",
    "mouse",
    "keyboard",
    "remote",
    "monitor",
    "screen",
}
ANIMAL_KEYWORDS = {
    "cat",
    "dog",
    "bird",
    "horse",
    "sheep",
    "cow",
    "elephant",
    "bear",
    "zebra",
    "giraffe",
}
FOOD_ITEMS = {
    "banana",
    "apple",
    "sandwich",
    "orange",
    "broccoli",
    "carrot",
    "hot dog",
    "pizza",
    "donut",
    "cake",
    "bottle",
    "wine glass",
    "cup",
    "fork",
    "knife",
    "spoon",
    "bowl",
}
FASHION_ITEMS = {"tie", "backpack", "handbag", "suitcase", "umbrella"}

# Average real-world widths (meters) for common YOLO classes
YOLO_CLASS_WIDTHS = {
    "person": 0.5,
    "man": 0.5,
    "woman": 0.5,
    "human body": 0.5,
    "pedestrian": 0.5,
    "bicycle": 0.6,
    "bicycle wheel": 0.6,
    "car": 1.8,
    "motorcycle": 0.8,
    "motor scooter": 0.8,
    "bus": 2.5,
    "truck": 2.5,
    "land vehicle": 1.8,
    "vehicle": 1.8,
    "golf cart": 1.5,
    "vehicle registration plate": 0.5,
    "traffic light": 0.3,
    "traffic sign": 0.6,
    "stop sign": 0.6,
    "sign": 0.6,
    "traffic cone": 0.3,
    "traffic barrier": 0.5,
    "bollard": 0.2,
    "barricade": 0.6,
    "hydrant": 0.3,
    "street light": 0.4,
    "parking meter": 0.3,
    "wheel": 0.5,
    "railroad": 1.4,
    "crosswalk": 2.0,
    "speed bump": 0.5,
    "train": 3.0,
    "cat": 0.4,
    "dog": 0.5,
    "chair": 0.5,
    "couch": 2.0,
    "dining table": 1.2,
    "bed": 1.5,
    "laptop": 0.35,
    "tv": 1.0,
    "cell phone": 0.08,
    "book": 0.2,
    "bottle": 0.08,
    "cup": 0.08,
    "bowl": 0.15,
    "keyboard": 0.4,
    "mouse": 0.06,
    "remote": 0.15,
    "backpack": 0.3,
    "umbrella": 1.0,
    "suitcase": 0.5,
    "clock": 0.2,
    "vase": 0.15,
    "potted plant": 0.3,
    "sink": 0.6,
    "toilet": 0.4,
    "refrigerator": 0.8,
    "microwave": 0.5,
}

DEFAULT_FOCAL_MM = 4.0
DEFAULT_SENSOR_WIDTH_MM = 5.6


def estimate_distance(label: str, bbox_w_px: int, img_w_px: int) -> float | None:
    real_w = YOLO_CLASS_WIDTHS.get(label.lower())
    if real_w is None or bbox_w_px <= 0:
        return None
    focal_px = (DEFAULT_FOCAL_MM / DEFAULT_SENSOR_WIDTH_MM) * img_w_px
    dist = (real_w * focal_px) / bbox_w_px
    return round(max(0.1, dist), 2)


def distance_desc(m: float) -> str:
    if m < 0.5:
        return "very close"
    if m < 1.5:
        return "arm's length"
    if m < 3:
        return "a few steps"
    if m < 10:
        return "nearby"
    if m < 30:
        return "across the room"
    if m < 100:
        return "in the distance"
    return f"{int(m)}m away"


def classify_label(label: str) -> str:
    low = label.lower()
    if low in PERSON_KEYWORDS:
        return "person"
    if low in VEHICLE_KEYWORDS:
        return "vehicle"
    if low in DEVICE_KEYWORDS:
        return "device"
    if low in ANIMAL_KEYWORDS:
        return "animal"
    if low in FOOD_ITEMS:
        return "food"
    if low in FASHION_ITEMS:
        return "fashion"
    return low


# ── Observation / navigation modes (autonomous-car style) ─────────────
# Each concrete mode declares which objects matter and their priority
# tier. Anything not listed is treated as scene background and dropped
# from the overlay so only relevant targets are drawn — this keeps the
# webcam UI fast and makes detection feel immediate.
#
# Priority tiers (1 = highest, drawn first / always):
#   1  critical   — traffic controls, vehicles ahead, pedestrians
#   2  important  — obstacles, hazards, active agents
#   3  situational— infrastructure context (only kept when near)
#   0  hidden     — background noise (trees, walls, furniture, food …)

MODE_STATIONARY = "stationary"
MODE_WALKING = "walking"
MODE_DRIVING = "driving"
VISION_MODES = (MODE_STATIONARY, MODE_WALKING, MODE_DRIVING)

# Extended person set for OIV7's granular human labels + generic aliases
PERSON_ALL = PERSON_KEYWORDS | {
    "human body",
    "human face",
    "human head",
    "pedestrian",
    "people",
}

# VEHICLE set for OIV7 + generic traffic agents
VEHICLE_ALL = VEHICLE_KEYWORDS | {
    "land vehicle",
    "vehicle registration plate",
    "golf cart",
    "motor scooter",
    "wheel",
    "vehicle",
}

_TRAFFIC_CRITICAL = {
    "traffic light",
    "traffic sign",
    "stop sign",
    "traffic signal",
    "sign",
}
_TRAFFIC_VEHICLES = {
    "car",
    "truck",
    "bus",
    "motorcycle",
    "motorbike",
    "bicycle",
    "land vehicle",
    "vehicle",
    "golf cart",
    "motor scooter",
    "vehicle registration plate",
}
_TRAFFIC_OBSTACLES = {
    "traffic cone",
    "traffic barrier",
    "bollard",
    "barricade",
    "hydrant",
    "street light",
    "parking meter",
    "utility pole",
    "pole",
    "wheel",
    "railroad",
    "train",
}
_TRAFFIC_CONTEXT = {
    "road",
    "crosswalk",
    "speed bump",
    "lane",
    "tunnel",
    "parking meter",
    "railroad",
}

# Object categories that are pure scene background — never shown in any
# navigation mode (keeps the window clean + fast: tree, wall, building …)
_BACKGROUND_NOISE = {
    # foliage / terrain
    "tree",
    "bush",
    "plant",
    "potted plant",
    "flower",
    "grass",
    "leaf",
    "flower pot",
    "garden",
    "vegetable",
    "herb",
    "vine",
    "houseplant",
    # structures / walls
    "wall",
    "fence",
    "building",
    "house",
    "garage",
    "shed",
    "barn",
    "tower",
    "gate",
    "door",
    "window",
    "roof",
    "chimney",
    "bridge",
    "column",
    "pillar",
    "arch",
    "facade",
    "porch",
    "deck",
    "balcony",
    "walkway",
    "sidewalk",
    "curb",
    "pavement",
    "plaza",
    "courtyard",
    "alley",
    # indoor furniture / objects
    "chair",
    "couch",
    "sofa",
    "bed",
    "table",
    "desk",
    "dresser",
    "cabinet",
    "shelf",
    "counter",
    "stool",
    "bench",
    "lamp",
    "sofa",
    "rug",
    "carpet",
    "curtain",
    "pillow",
    "blanket",
    "towel",
    "mirror",
    "picture frame",
    # appliances / electronics indoors
    "refrigerator",
    "microwave",
    "oven",
    "stove",
    "toaster",
    "sink",
    "toilet",
    "bathtub",
    "shower",
    "washer",
    "dryer",
    "television",
    "tv",
    "laptop",
    "computer",
    "monitor",
    "keyboard",
    "mouse",
    "cell phone",
    "remote",
    "phone",
    "speaker",
    "headphones",
    "camera",
    "printer",
    "router",
    "charger",
    "clock",
    # food / drink
    *FOOD_ITEMS,
    # small misc
    "book",
    "magazine",
    "newspaper",
    "paper",
    "envelope",
    "box",
    "cardboard",
    "bag",
    "shopping bag",
    "plastic bag",
    "trash",
    "garbage",
    "waste",
    "dustbin",
    "bottle",
    "jar",
    "can",
    "container",
    "pack",
    "footwear",
    "shoe",
    "sneaker",
    "boot",
    "sandal",
    "slipper",
    "hat",
    "helmet",
    "bicycle helmet",
    "cap",
    "glasses",
    "sunglasses",
    "watch",
    "jewelry",
    "ring",
    "necklace",
    "wallet",
    "purse",
    "handbag",
    "backpack",
    "suitcase",
    "luggage",
    "umbrella",
    "tie",
    "clothing",
    "shirt",
    "jacket",
    "coat",
    "dress",
    "pants",
    "jeans",
    "shorts",
    "skirt",
    "sweater",
    "hoodie",
    "t-shirt",
    "scarf",
    "glove",
    "sock",
    "toy",
    "ball",
    "frisbee",
    "skateboard",
    "kite",
    "doll",
    "teddy bear",
    # embellishments / misc outdoor
    "flag",
    "fountain",
    "statue",
    "sculpture",
    "billboard",
    "poster",
    "advertisement",
    "graffiti",
    "painting",
    "drawing",
    "photo",
}
# OIV7 "Human <body part>" fragments — these are part-of-person boxes that
# arrive alongside a full-person box; in navigation modes they duplicate the
# critical person signal, so they are hidden (person itself stays priority 1).
_HUMAN_PARTS = {
    "human arm",
    "human beard",
    "human ear",
    "human eye",
    "human foot",
    "human hair",
    "human hand",
    "human leg",
    "human mouth",
    "human nose",
}

MODE_TIERS = {
    MODE_DRIVING: {
        1: _TRAFFIC_CRITICAL
        | _TRAFFIC_VEHICLES
        | {"person", "man", "woman", "human body", "pedestrian"},
        2: _TRAFFIC_OBSTACLES | {"motor scooter", "train"},
        3: _TRAFFIC_CONTEXT - _TRAFFIC_OBSTACLES,
    },
    MODE_WALKING: {
        1: {
            "person",
            "man",
            "woman",
            "human body",
            "pedestrian",
            "bicycle",
            "motorcycle",
            "motorbike",
            "car",
            "dog",
            "cat",
        }
        | _TRAFFIC_CRITICAL,
        2: _TRAFFIC_OBSTACLES | {"truck", "bus", "train"} | ANIMAL_KEYWORDS,
        3: _TRAFFIC_VEHICLES - {"car", "bicycle", "motorcycle", "motorbike"},
    },
    MODE_STATIONARY: {
        1: {"person", "man", "woman", "human body", "pedestrian"} | PERSON_ALL,
        2: ANIMAL_KEYWORDS
        | _TRAFFIC_CRITICAL
        | {"car", "truck", "bus", "motorcycle", "bicycle"},
        3: set(),
    },
}

# Confidence floor per tier — critical objects keep even weak boxes;
# background tier keeps nothing (it's hidden anyway).
# Driving: floors sit BELOW the model's 0.25 conf so nothing that survived
# inference gets killed by the overlay filter (the old 0.38 tier-3 floor
# silently dropped most cars/signs/lights at 0.30–0.38 conf).
_TIER_CONF_MIN = {1: 0.18, 2: 0.24, 3: 0.26}

# How many boxes can be drawn per tier (keeps the canvas cheap on mobile)
_TIER_MAX_BOXES = {1: 12, 2: 10, 3: 10}

# ── Drive-alert MOTION GATE ──────────────────────────────────────────
# Drive-safety alerts that have real consequences when wrong (PEDESTRIAN,
# FORWARD_COLLISION) are only meaningful while the device is actually in
# motion. A parked neighbor's car at the front door, or a porch decoration
# that looks like a person, must NEVER trigger "brake now / slow down".
# These constants gate those alerts on phone telemetry:
#   speed: GPS ground speed in km/h
#   motion: accelerometer-derived magnitude in g (linear accel ÷9.80665,
#           or |total_accel/1g − 1| for bumps/turns, or significant_motion)
# Stop-sign / traffic-light alerts stay ungated — you still see them while
# stopped at a red light, which is exactly when you need them.
DRIVE_ALERT_MIN_SPEED_KPH = float(os.environ.get("DRIVE_ALERT_MIN_SPEED_KPH", "8"))
DRIVE_ALERT_MIN_MOTION_G = float(os.environ.get("DRIVE_ALERT_MIN_MOTION_G", "0.12"))
# Pedestrian alerts need a higher confidence than the generic tier-1 floor
# (0.18) so furniture/porch/fence false positives don't make us yell.
PEDESTRIAN_ALERT_CONF = float(os.environ.get("PEDESTRIAN_ALERT_CONF", "0.35"))


def _motion_from_sensors(sensors: dict | None) -> float | None:
    """Best-effort motion magnitude in g from a termux /sensors/all payload.

    Mirrors lilly_ai._motion_g(): supports {sensor_name: {values:[x,y,z]}}
    termux payloads for significant_motion / linear_acceleration /
    accelerometer_uncalibrated / accelerometer. Returns None when no motion
    sensor data is present (caller treats that as "not moving", the safe
    default for drive alerts).
    """
    if not sensors:
        return None
    inner = sensors.get("sensors")
    raw: dict = inner if isinstance(inner, dict) else sensors

    def _mag(v):
        if isinstance(v, dict):
            v = v.get("values")
        if isinstance(v, (list, tuple)) and len(v) >= 3:
            try:
                return float((v[0] ** 2 + v[1] ** 2 + v[2] ** 2) ** 0.5)
            except Exception:
                return None
        if isinstance(v, (int, float)):
            return float(v)
        return None

    best: float | None = None
    for k in ("significant_motion", "motion"):
        v = _mag(raw.get(k))  # already in g (unitless event/gear value)
        if v is not None and (best is None or v > best):
            best = v
    G = 9.80665
    for k in ("linear_acceleration", "accelerometer_uncalibrated"):
        v = _mag(raw.get(k))  # termux reports m/s² → normalize to g
        if v is not None:
            v = v / G
            if best is None or v > best:
                best = v
    v = _mag(raw.get("accelerometer"))
    if v is not None:
        dev = abs(v / G - 1.0)  # deviation from 1g captures bumps/turns/braking
        if best is None or dev > best:
            best = dev
    return round(best, 3) if best is not None else None


def resolve_mode(detections: list, requested: str = "auto") -> str:
    """Resolve the effective vision mode. 'auto' derives from scene content:
    lots of vehicles → driving; mainly people → walking; else stationary.

    IMPORTANT: a SINGLE vehicle in frame is never enough to declare driving —
    otherwise pointing the camera at your front door while the neighbour's
    parked car is visible would arm the whole drive-alert system. Two or more
    vehicles, or confirmed GPS/accel motion (handled by the caller), is what
    switches us into driving mode.
    """
    req = (requested or "auto").strip().lower()
    if req in VISION_MODES:
        return req
    vehicle = sum(
        1 for d in detections if classify_label(d.get("label", "")) == "vehicle"
    )
    people = sum(
        1 for d in detections if (d.get("label", "") or "").lower() in PERSON_ALL
    )
    total = len(detections)
    if total == 0:
        return MODE_STATIONARY
    if vehicle >= 2 and vehicle >= people:
        return MODE_DRIVING
    if people and people / total >= 0.3:
        return MODE_WALKING
    # single vehicle + no motion evidence → treat as parked scene, not driving
    return MODE_STATIONARY


def detection_tier(label: str, mode: str) -> int:
    """Priority tier (1 critical → 3 situational, 0 = hidden background)."""
    low = label.lower()
    if mode == MODE_STATIONARY and low in PERSON_ALL:
        return 1 if low in ("person", "human body", "pedestrian") else 1
    tiers = MODE_TIERS.get(mode, MODE_TIERS[MODE_STATIONARY])
    for tier, labels in sorted(tiers.items()):
        if low in labels:
            return tier
    if low in _BACKGROUND_NOISE or low in _HUMAN_PARTS:
        return 0
    # Unknown classes: keep only in stationary personal mode as low priority
    return 3 if mode == MODE_STATIONARY else 0


# ── Motion / closing-speed tracking (per detection, lightweight) ───────
# Tracks the distance estimate of each object across a short window so we
# can report an approximate closing speed (kph) — "car 22m · 41 km/h closing".
_track_history: dict[str, list] = {}  # key → [(ts, dist_m), …] capped at 4
_TRACK_TTL = 6.0


def _track_speed(det: dict, now: float) -> float | None:
    """Closing speed in km/h from the distance estimate changing over time.
    Positive = approaching the camera. None = not enough data."""
    label = str(det.get("label", "object")).lower()
    dist = det.get("distance_m")
    if dist is None:
        return None
    key = f"{label}:{round(det.get('x', 0), 2)}:{round(det.get('y', 0), 2)}"
    hist = _track_history.setdefault(key, [])
    # evict stale entries
    hist[:] = [e for e in hist if now - e[0] <= _TRACK_TTL]
    speed = None
    if hist and hist[-1][1] is not None:
        dt = now - hist[-1][0]
        prev_dist = hist[-1][1]
        if dt >= 0.15:
            speed = (prev_dist - dist) / dt * 3.6  # m/s → km/h closing
            speed = round(max(-180.0, min(180.0, speed)), 1)
    hist.append((now, dist))
    if len(hist) > 4:
        hist.pop(0)
    # prune old keys occasionally
    if len(_track_history) > 512:
        for k in [k for k, v in _track_history.items() if not v]:
            _track_history.pop(k, None)
    return speed


def apply_vision_mode(
    detections: list, requested: str = "auto", now: float | None = None
) -> tuple:
    """Filter + annotate detections for the active (or auto-resolved) mode.

    Returns (resolved_mode, kept_detections). Each kept detection gains:
      tier (1/2/3), priority (same), speed_kph, motion (bool).
    dropped detections are removed entirely so the overlay stays clean.
    """
    now = now if now is not None else time.time()
    mode = resolve_mode(detections, requested)
    tiers = MODE_TIERS.get(mode, MODE_TIERS[MODE_STATIONARY])

    kept: list[dict] = []
    for d in detections:
        label = str(d.get("label", d.get("class", "object")))
        tier = detection_tier(label, mode)
        if tier <= 0:
            continue
        min_conf = _TIER_CONF_MIN.get(tier, 0.3)
        if float(d.get("conf", 0)) < min_conf:
            continue
        d["tier"] = tier
        d["priority"] = tier
        d["mode"] = mode
        spd = _track_speed(d, now)
        if spd is not None:
            d["speed_kph"] = abs(spd)
            d["closing_kph"] = spd if spd >= 0 else -spd
        else:
            d["speed_kph"] = None
            d["closing_kph"] = None
        kept.append(d)

    # Per-tier box cap: keep the closest (largest area → nearest) detections
    out: list[dict] = []
    for tier in (1, 2, 3):
        boxed = [d for d in kept if d["tier"] == tier]
        boxed.sort(
            key=lambda d: (
                d.get("distance_m") is not None,
                -(d.get("w", 0) * d.get("h", 0)),
            )
        )
        out.extend(boxed[: _TIER_MAX_BOXES.get(tier, 4)])
    # Stable order: critical first
    out.sort(key=lambda d: d["tier"])
    return mode, out


def overlay_summary(
    detections: list, mode: str, device_speed_kph: float | None = None
) -> dict:
    """Compact HUD data for the browser overlay (mode, speed, counts)."""
    counts: dict[str, int] = {}
    categories: dict[str, int] = {}
    for d in detections:
        label = d.get("label", "object")
        counts[label] = counts.get(label, 0) + 1
        cat = classify_label(label)
        categories[cat] = categories.get(cat, 0) + 1
    tier1 = sum(1 for d in detections if d.get("tier") == 1)
    return {
        "mode": mode,
        "device_speed_kph": device_speed_kph,
        "counts": counts,
        "categories": categories,
        "tier1": tier1,
        "critical": tier1,
        "total": len(detections),
    }


# ── Drive safety alerts (deterministic — no LLM, no randomness) ─────
# Built per frame from tier 1/2 detections + closing speeds + GPS speed.
# Cooldown-gated per type so the PiP voice never spams; level-1 collision
# alerts re-fire on a short timer while the risk persists.
_DRIVE_ALERT_STATE: dict[str, float] = {}
_DRIVE_ALERT_COOLDOWN = {
    "STOP_SIGN": 25.0,
    "PEDESTRIAN": 12.0,
    "TRAFFIC_LIGHT": 30.0,
    "FORWARD_COLLISION": 8.0,
}
_LABEL_STOP_SIGN = {"stop sign", "traffic sign", "traffic signal", "sign"}
_LABEL_PEDESTRIAN = {"person", "man", "woman", "human body", "pedestrian", "people"}
_LABEL_TRAFFIC_LIGHT = {"traffic light"}
_LABEL_VEHICLE = _TRAFFIC_VEHICLES

# Short spoken phrases — Piper-friendly (no symbols, no asterisks).
_ALERT_SPEECH = {
    "STOP_SIGN_NEAR": "stop sign coming up",
    "STOP_SIGN_AHEAD": "stop sign ahead",
    "PEDESTRIAN_NEAR": "pedestrian right there, slow down",
    "PEDESTRIAN_AHEAD": "pedestrian up ahead",
    "TRAFFIC_LIGHT": "traffic light ahead",
    "COLLISION_BRAKE": "too close to the vehicle ahead, brake now",
    "COLLISION_SLOW": "slow down, vehicle ahead is too close",
}

# Last drive-alert snapshot, readable by /api/vision/drive (passive polls)
_DRIVE_LAST: dict = {"ts": 0.0, "mode": None, "device_speed_kph": None, "alerts": []}


def _drive_alerts(
    detections: list,
    device_speed_kph: float | None,
    now: float | None = None,
    motion_g: float | None = None,
) -> list:
    """Deterministic drive-safety alerts for the current frame.

    Returns up to 3 alerts ordered by severity:
      {type, level (1 critical / 2 caution / 3 info), text, tts, ttc_s}
    Level 1/2 fire speech (cooldown-gated); level 3 is overlay-only.

    MOTION GATE: PEDESTRIAN + FORWARD_COLLISION alerts only fire while phone
    telemetry confirms the device is actually moving (GPS ≥ 8 km/h OR accel
    ≥ 0.12g). Otherwise a parked neighbour's car or a porch-decoration
    "pedestrian" would trigger braking alarms while you stand at the door.
    STOP_SIGN / TRAFFIC_LIGHT remain ungated (you still need them stopped).
    """
    if now is None:
        now = time.time()
    lvl1_warned: set[str] = set()
    alerts: list[dict] = []
    dev = float(device_speed_kph or 0)  # GPS ground speed (km/h)
    moving = bool(
        (device_speed_kph is not None and dev >= DRIVE_ALERT_MIN_SPEED_KPH)
        or (motion_g is not None and motion_g >= DRIVE_ALERT_MIN_MOTION_G)
    )

    for d in detections:
        if d.get("tier") not in (1, 2):
            continue
        label = str(d.get("label", "")).lower()
        conf = float(d.get("conf", 0))
        cx = d.get("x", 0.5) + d.get("w", 0) / 2
        where = (
            "on the left"
            if cx < 0.33
            else ("on the right" if cx > 0.66 else "straight ahead")
        )
        dist = d.get("distance_m") or None
        close = dist is not None and dist <= 14.0

        if label in _LABEL_STOP_SIGN and "STOP_SIGN" not in lvl1_warned:
            lvl1_warned.add("STOP_SIGN")
            alerts.append(
                {
                    "type": "STOP_SIGN",
                    "level": 2 if close else 3,
                    "text": f"stop sign {where}"
                    if close
                    else f"stop sign ahead, {where}",
                    "tts": _ALERT_SPEECH[
                        "STOP_SIGN_NEAR" if close else "STOP_SIGN_AHEAD"
                    ],
                }
            )
        if (
            moving
            and label in _LABEL_PEDESTRIAN
            and conf >= PEDESTRIAN_ALERT_CONF
            and "PEDESTRIAN" not in lvl1_warned
        ):
            # Motion-gated AND confidence-gated: a weak "person" box while
            # standing still is a porch plant, not a hazard worth shouting about.
            lvl1_warned.add("PEDESTRIAN")
            alerts.append(
                {
                    "type": "PEDESTRIAN",
                    "level": 1 if close else 2,
                    "text": f"pedestrian {where}"
                    if close
                    else f"pedestrian ahead, {where}",
                    "tts": _ALERT_SPEECH[
                        "PEDESTRIAN_NEAR" if close else "PEDESTRIAN_AHEAD"
                    ],
                }
            )
        if label in _LABEL_TRAFFIC_LIGHT and "TRAFFIC_LIGHT" not in lvl1_warned:
            lvl1_warned.add("TRAFFIC_LIGHT")
            alerts.append(
                {
                    "type": "TRAFFIC_LIGHT",
                    "level": 3,
                    "text": "traffic light ahead",
                    "tts": _ALERT_SPEECH["TRAFFIC_LIGHT"],
                }
            )
        if (
            moving
            and label in _LABEL_VEHICLE
            and "FORWARD_COLLISION" not in lvl1_warned
        ):
            closing = float(
                d.get("closing_kph") or 0
            )  # hi=this vehicle approaching fast
            ttc = None
            if dist and closing > 1.0:
                ttc = dist / (closing / 3.6)
            risky = False
            # 1) Physically too close at our speed: can't stop comfortably.
            if dist and dev > 8:
                # comfortable decel 3.0 m/s² → needs v²/(2a) metres to stop
                stop_dist = (dev / 3.6) ** 2 / (2 * 3.0)
                if dist < stop_dist * 1.25 + 2:
                    risky = True
            # 2) Really closing on us while we're in motion (jitter-suppressed
            #    when parked because this whole branch is motion-gated above).
            if close and (closing > 8 or (ttc is not None and ttc < 4.0)):
                risky = True
            if risky:
                lvl1_warned.add("FORWARD_COLLISION")
                # Emergency if contact in <3.5s or we're basically on it.
                emergency = (ttc is not None and ttc < 3.5) or (
                    dist is not None and dist <= 6.0
                )
                alerts.append(
                    {
                        "type": "FORWARD_COLLISION",
                        "level": 1,
                        "text": "vehicle ahead too close — brake now"
                        if emergency
                        else "vehicle ahead too close — slow down",
                        "tts": _ALERT_SPEECH[
                            "COLLISION_BRAKE" if emergency else "COLLISION_SLOW"
                        ],
                        "ttc_s": round(ttc, 1) if ttc is not None else None,
                    }
                )

    if not alerts:
        return []
    out: list[dict] = []
    for a in sorted(alerts, key=lambda x: (x["level"], -(x.get("ttc_s") or 99))):
        key = a["type"]
        last = _DRIVE_ALERT_STATE.get(key, 0.0)
        # Level 1 re-fires on its short cooldown while the risk is real;
        # level 2+ respect their (longer) cooldown so we nag, not yammer.
        if a["level"] == 1 or (now - last) >= _DRIVE_ALERT_COOLDOWN.get(key, 15.0):
            if a["level"] in (1, 2):
                _DRIVE_ALERT_STATE[key] = now
            out.append(a)
            if len(out) >= 3:
                break
    # Level-3 alerts always ride along (banner-only, never spoken)
    out.extend(
        a
        for a in sorted(alerts, key=lambda x: x["level"])
        if a["level"] == 3
        and len(out) < 4
        and a["type"] not in {x["type"] for x in out}
    )
    return out


def build_sensor_context(sensors: dict) -> str:
    if not sensors:
        return ""
    parts = []
    raw = sensors.get("sensors") or {}
    light = sensors.get("light", sensors.get("ambient_light"))
    if light is None:
        lux = raw.get("light")
        light = lux[0] if isinstance(lux, (list, tuple)) and lux else None
    if light is not None:
        if light < 50:
            parts.append("dark room")
        elif light < 500:
            parts.append("dim light")
        elif light > 1000:
            parts.append("bright light")
    prox = raw.get("proximity")
    if isinstance(prox, (list, tuple)) and prox and prox[0] < 8:
        parts.append("something right by your hand")
    accel = raw.get("linear_acceleration") or raw.get("accelerometer")
    if isinstance(accel, (list, tuple)) and len(accel) >= 3:
        mag = (accel[0] ** 2 + accel[1] ** 2 + accel[2] ** 2) ** 0.5
        if mag > 2.0:
            parts.append("you are moving")
    steps = sensors.get("step_counter", sensors.get("steps"))
    if isinstance(steps, (list, tuple)):
        steps = steps[0] if steps else None
    if steps and steps > 10000:
        parts.append(f"{int(steps)} steps today")
    temp = sensors.get("temperature")
    if temp is not None and (temp > 30 or temp < 10):
        parts.append(f"{temp}°C")
    motion = sensors.get("significant_motion", sensors.get("motion"))
    if motion and motion > 0:
        parts.append("in motion")
    return ", ".join(parts) if parts else ""


# ── Agent voices (Edge TTS voice IDs) ───────────────────────────────────
AGENTS = {
    "puppy": {
        "name": "Puppy",
        "emoji": "🐕",
        "voice": "en-US-AvaNeural",  # Female, expressive, caring
        "rate": "+10%",  # Slightly faster
        "lines": {
            "person": [
                "Oh hey, someone's there. They look like they're just vibing.",
                "Person in frame. They've got that 'I forgot what I walked in here for' energy.",
                "Someone's around. They look comfortable. Living their best life.",
                "Oh look, a human. They seem chill. I'd hang out with them.",
            ],
            "dog": [
                "Is that a dog? Oh hell yes. That's a good looking animal right there.",
                "DOG. Okay but look at that face. That's the face of someone who's never paid rent.",
                "A dog! You know what, dogs figured out life way before we did.",
                "There's a dog and honestly it's living better than most people I know.",
            ],
            "cat": [
                "Cat. Already judging everyone in the room. Respect.",
                "There's a cat and it looks like it pays more taxes than it should.",
                "A cat! It's got that 'I own this place' energy. Because it does.",
                "Cat detected. It's giving 'I'm better than you and we both know it.'",
            ],
            "food": [
                "Oh that's food. What kind? I need details. This matters.",
                "I see food and I need to know the backstory. Who made it? Was it good?",
                "Food! You know what, life's too short to eat boring food. What is it?",
                "There's something to eat there. The real question is — is it good?",
            ],
            "device": [
                "Laptop and phone? Someone's either working hard or barely working.",
                "I see screens. Lots of screens. The modern human condition, honestly.",
                "Tech detected. You know what, screens are the new windows.",
            ],
            "empty": [
                "Nothing. The room is giving minimalist chic. Or just empty.",
                "All clear. Either very tidy or very boring. Both are valid life choices.",
                "Nothing here. You know what, sometimes nothing IS something.",
            ],
            "default": [
                "Oh that's interesting. I have questions. Several, actually.",
                "I see it. Not sure what to make of it yet but I'm forming an opinion.",
                "Detected. I'm going to need more context but I'm intrigued.",
            ],
        },
    },
    "fox": {
        "name": "Fox",
        "emoji": "🦊",
        "voice": "en-US-AndrewNeural",  # Male, warm, confident
        "rate": "-5%",  # Slightly slower, more deliberate
        "lines": {
            "person": [
                "Another human. Groundbreaking. What are the odds in a world of 8 billion people.",
                "Someone's here. They look like they have opinions. I respect that.",
                "Person detected. They seem like they've got stories. I'd listen.",
                "Oh a person. They've got that 'I haven't slept enough' look. Relatable.",
            ],
            "dog": [
                "A dog. Still convinced it's the best creature on earth. And honestly? It might be right.",
                "Dog. The only being that gets excited about literally everything. We should all be more like dogs.",
                "There's a dog and it's just happy. What's that like?",
            ],
            "cat": [
                "A cat. Already planning world domination. I see you.",
                "Cat. It's giving 'I could conquer this room but I choose not to.' Iconic.",
                "There's a cat here and it's the most powerful being in this frame. By far.",
            ],
            "food": [
                "Food. Let me guess — it's something you're about to regret eating at 2am.",
                "I see food. The real question is: are you eating it alone or is this a social situation?",
                "That looks like something that belongs on a 'what I eat in a day' video.",
            ],
            "device": [
                "Screens. The modern equivalent of 'I'm busy, don't talk to me.'",
                "I see tech. Screens are just expensive windows to other people's problems.",
            ],
            "empty": [
                "Nothing. Either very zen or very lonely. Both hit different.",
                "Empty space. Marie Kondo would approve. Or maybe not. Who knows.",
                "Nothing here. The silence is deafening. And a little judging.",
            ],
            "default": [
                "Interesting. I'm forming an opinion. You'd rather I didn't share it.",
                "Detected. I'm going to pretend I didn't see that. For everyone's sake.",
                "I see it. I'm judging it. But like, professionally.",
            ],
        },
    },
    "cat": {
        "name": "Cat",
        "emoji": "🐱",
        "voice": "en-US-EmmaNeural",  # Female, cheerful, clear
        "rate": "-10%",  # Slower, more deliberate
        "lines": {
            "person": [
                "A human. They think they're the main character. Adorable.",
                "Someone's here. I'm not impressed. But then again, I'm rarely impressed.",
                "Person detected. They're probably going to try to pet me. They'd be right to.",
                "Oh, a person. How pedestrian. What else is new.",
            ],
            "dog": [
                "A dog. Still running on vibes and enthusiasm. I respect it.",
                "Dog detected. It's like a golden retriever if golden retrievers had opinions.",
                "There's a dog. It's very enthusiastic. About everything. Always.",
            ],
            "cat": [
                "Another cat. We shall judge these humans together. It's what we do best.",
                "A cat! Finally, someone with standards and good taste.",
                "Cat. Already in charge. Has been since before you were born.",
            ],
            "food": [
                "Food. Obviously. Is it for me? It should be for me.",
                "I see food. I'm not interested. Okay I'm slightly interested. Don't test me.",
                "Snacks detected. I'm choosing to care. Temporarily.",
            ],
            "device": [
                "Screens. You humans and your glowing rectangles. Fascinating species.",
                "Tech detected. I could operate that if I wanted to. I just don't want to.",
            ],
            "empty": [
                "Nothing. Perfect. The way I like it. Leave me alone.",
                "Empty. Good. The world is too loud anyway.",
                "Nothing here. I'm going back to sleep. Wake me when it matters.",
            ],
            "default": [
                "I'm judging you. You can't see it but I am.",
                "Detected. I'm acknowledging it. That's the most you're getting from me.",
                "I see it. I'm not impressed. But then again, what impresses a cat?",
            ],
        },
    },
    "bear": {
        "name": "Bear",
        "emoji": "🐻",
        "voice": "en-US-BrianNeural",  # Male, approachable, casual
        "rate": "-15%",  # Slower, more thoughtful
        "lines": {
            "person": [
                "A person. You know what, everyone's just trying to figure it out. No judgment.",
                "Someone's here. They look like they could use a hug. Or a nap. Probably both.",
                "A human! Remember, you're made of stardust. Literally. Look it up.",
                "Person detected. They've got that 'one more email and I'm done' energy.",
            ],
            "dog": [
                "A dog. Living proof that happiness is a choice. A very furry, loud choice.",
                "There's a dog and it's just existing. No thoughts. Just vibes. Goals.",
                "Dog detected. The philosopher of the animal kingdom. Doesn't even know it.",
            ],
            "cat": [
                "A cat. Contemplating the void. Probably. Or just staring at a wall. Same thing.",
                "Cat. The only being that's mastered the art of doing absolutely nothing with purpose.",
                "There's a cat. It's giving 'I've seen things you humans wouldn't understand.'",
            ],
            "food": [
                "Food. You know what, sharing a meal is the oldest form of connection. What are we eating?",
                "I see food. Remember, every meal is a small celebration of being alive. What is it?",
                "Sustenance detected. The universe provides. Sometimes in the form of snacks.",
            ],
            "device": [
                "Screens. We built these glowing windows to the world and now we can't stop looking at them.",
                "Tech detected. We're all just staring at rectangles. The future is weird.",
            ],
            "empty": [
                "Nothing. Sometimes the empty spaces say more than the full ones.",
                "Empty. You know what, the pause between notes is what makes the music.",
                "Nothing here. But you're here. And that's enough. Really.",
            ],
            "default": [
                "Interesting. Everything has a story. I wonder what this one's about.",
                "Detected. The more you look, the more you realize how much there is to see.",
                "I see it. Every object was once just an idea in someone's head. Wild.",
            ],
        },
    },
    "bunny": {
        "name": "Bunny",
        "emoji": "🐰",
        "voice": "en-US-JennyNeural",  # Female, friendly, considerate
        "rate": "+5%",  # Slightly faster, energetic
        "lines": {
            "person": [
                "Oh hey, someone's here! They seem cool. I'd chat with them.",
                "Person detected! They look like they have good taste in music. I can tell.",
                "Someone's around! They've got that 'I listen to podcasts' energy.",
                "A human! They seem like they'd remember your birthday. That's rare.",
            ],
            "dog": [
                "A dog! You know what, dogs are just pure joy in fur form.",
                "DOG! Okay but hear me out — dogs are proof that good things exist.",
                "There's a dog and it's just being itself. We should all aspire to that.",
            ],
            "cat": [
                "A cat! They seem like they have their life together. I'm jealous.",
                "Cat detected. It's giving 'I have a skincare routine and a 401k.'",
                "There's a cat and honestly it's more put together than most people I know.",
            ],
            "food": [
                "Oh that looks good! What is it? I need the recipe. For science.",
                "Food! You know what, life's too short for bad food. That looks decent though.",
                "I see food and I'm here for it. What's the occasion? Or is it a Tuesday?",
            ],
            "device": [
                "Tech! You know what, screens are just portals to other people's opinions. Fun.",
                "I see screens. The modern human's security blanket. No judgment though.",
            ],
            "empty": [
                "Nothing! You know what, that's peaceful. I'm into it.",
                "All clear. The room has a vibe. A quiet, empty vibe. I respect it.",
                "Nothing here. Sometimes the best thing to see is nothing. Peace.",
            ],
            "default": [
                "Oh that's cool! I have questions but in a good way!",
                "Detected! I'm curious about this one. Tell me more.",
                "I see it! It's interesting. I'm going to need details though.",
            ],
        },
    },
    "owl": {
        "name": "Owl",
        "emoji": "🦉",
        "voice": "en-US-ChristopherNeural",  # Male, reliable, authority
        "rate": "-20%",  # Slowest, most measured
        "lines": {
            "person": [
                "A human. The most fascinating and contradictory species on this planet.",
                "Person detected. They share 60% of their DNA with a banana. Fun fact.",
                "Someone's here. Did you know the average person walks the equivalent of 5 times around the earth in their lifetime?",
                "A person! Their phone has more computing power than NASA had in 1969. Think about that.",
            ],
            "dog": [
                "Canis lupus familiaris. Or as the poets say, a good boy. Scientifically verified.",
                "A dog. 15,000 years of domestication and they still eat garbage. Remarkable evolution.",
                "Dog detected. Their sense of smell is 10,000 times better than yours. They live in a different world.",
            ],
            "cat": [
                "Felis catus. The only animal that domesticated ITSELF. That takes confidence.",
                "A cat! They've been worshipped as gods in Egypt. They haven't forgotten. Neither should you.",
                "Cat detected. They control us. We just haven't realized it yet. Give it time.",
            ],
            "food": [
                "Edible matter detected. The average human eats 35 tons of food in a lifetime. Choose wisely.",
                "Food! You are literally what you eat. So if you eat garbage, well, you get the idea.",
                "Sustenance! Your body is 60% water. Your brain is 75% water. You're basically a walking puddle with opinions.",
            ],
            "device": [
                "Screens. The average person spends 7 hours a day looking at one. That's a lot of rectangles.",
                "Tech detected. Your phone has more processing power than the systems that sent humans to the moon.",
            ],
            "empty": [
                "Nothing. The absence of something is itself something to contemplate. Very zen.",
                "Empty. Einstein said imagination is more important than knowledge. Use this empty space wisely.",
                "Nothing here. The universe is 95% empty space. So this is statistically normal.",
            ],
            "default": [
                "Detected. The more you know about the world, the more you realize how much you don't know.",
                "I see it. Let me consult my mental database. Fascinating. Truly.",
                "Object identified. Every atom in that object was forged in the heart of a dying star. Poetic.",
            ],
        },
    },
}

# ── TTS cache ────────────────────────────────────────────────────────────
TTS_DIR = Path(tempfile.gettempdir()) / "lilly_tts"
TTS_DIR.mkdir(exist_ok=True)

# Audio ID counter
_audio_id = 0
_audio_map = {}  # id -> file path


async def generate_tts(text: str, voice: str, rate: str) -> int:
    """Generate TTS audio with Edge TTS, return audio ID."""
    global _audio_id
    import edge_tts

    _audio_id += 1
    audio_id = _audio_id
    out_path = TTS_DIR / f"agent_{audio_id}.mp3"

    try:
        communicate = edge_tts.Communicate(text, voice, rate=rate)
        await communicate.save(str(out_path))
        _audio_map[audio_id] = out_path
        return audio_id
    except Exception as e:
        log.error(f"Edge TTS error for {voice}: {e}")
        return 0


# ── Cohesive single-persona narration ───────────────────────────────────
# The camera always speaks in ONE voice: the active avatar. No round-robin
# of unrelated "agents". Text is LLM-synthesized per scene so it never
# repeats canned lines, and deduplicated so the same scene isn't re-narrated.

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")
VISION_LLM_MODEL = os.environ.get("VISION_LLM_MODEL", "qwen2.5:3b")
# True vision-LLM (image-in) — moondream:latest (Apache 2.0) via Ollama.
# Describes the actual pixels when enabled; falls back to VISION_LLM_MODEL
# (scene-text LLM) and then templates if it fails or is disabled.
VISION_LLM_VISION_MODEL = os.environ.get("VISION_LLM_VISION_MODEL", "moondream:latest")
VISION_MOONDREAM_ENABLED = os.environ.get("VISION_MOONDREAM_ENABLED", "1") not in (
    "0",
    "false",
    "no",
    "",
)
VISION_MOONDREAM_TIMEOUT = float(os.environ.get("VISION_MOONDREAM_TIMEOUT", "30.0"))
SCENE_TTL_SECS = float(os.environ.get("SCENE_TTL_SECS", "10"))
SENSOR_URL = os.environ.get("SENSOR_SERVER_URL", "http://100.115.234.87:8099")

_moondream_lock = None  # asyncio.Semaphore(1) — one image describe at a time
_moondream_available = True  # set False on first failure to skip retries per call

_phone_sensor_cache: dict = {
    "ts": 0.0,
    "data": {},
}  # live phone sensor context (always-on, best effort)
# Vision frames must never block on the phone's slow /sensors/all response
# (measured ~1.5s). Cache long enough that steady-state frames are instant,
# and refresh in the background instead of the request path.
_PHONE_SENSOR_TTL = float(os.environ.get("VISION_SENSOR_TTL", "15.0"))


async def _fetch_phone_sensors() -> dict:
    """Pull live spatial context from the phone's sensor server (port 8099).
    Never blocks the request: returns the cached snapshot immediately and
    refreshes in a background thread when stale. Degrades to {} when offline."""
    global _phone_sensor_cache
    now = time.time()
    if (
        _phone_sensor_cache["data"]
        and now - _phone_sensor_cache["ts"] < _PHONE_SENSOR_TTL
    ):
        return _phone_sensor_cache["data"]

    def _refresh():
        try:
            import httpx

            r = httpx.get(f"{SENSOR_URL}/sensors/all", timeout=2.0)
            if r.status_code == 200:
                _phone_sensor_cache = {"ts": time.time(), "data": r.json()}
        except Exception:
            pass

    try:
        threading.Thread(target=_refresh, daemon=True).start()
    except Exception:
        pass
    # stale-but-usable data is better than nothing; never block on the phone
    return _phone_sensor_cache["data"] if _phone_sensor_cache["data"] else {}


async def _effective_sensors(body_sensors: dict) -> dict:
    """Merge phone-native sensor data under the caller's own sensor values
    (if the caller supplied any, theirs wins). No toggle — always enriched,
    silently empty for sighted/simple use."""
    phone = await _fetch_phone_sensors()
    merged = dict(phone)
    merged.update(body_sensors or {})
    return merged


AVATAR_STYLE = {
    "puppy": (
        "Lilly",
        "stoic, dry, precise. sharp wit, understated, few words",
    ),
    "fox": ("Fox", "sharp, inventive, playful, quick, a little oblique"),
    "cat": ("Cat", "methodical, precise, dry, no fluff"),
    "bear": ("Bear", "warm, gentle, thoughtful, unhurried"),
    "bunny": ("Bunny", "cheerful, friendly, upbeat, curious"),
    "owl": ("Owl", "measured, wise, observant, a touch dramatic"),
    "deer": ("Deer", "calm, gentle, soft-spoken, mindful"),
    "wolf": ("Wolf", "direct, loyal, confident, level"),
    "raccoon": ("Raccoon", "sly, playful, resourceful, a little mischievous"),
}

_scene_cache: dict = {"key": None, "avatar": None, "ts": 0.0, "text": ""}


def _frame_zone(det: dict) -> str:
    cx = det.get("x", 0.5) + det.get("w", 0) / 2
    dist = det.get("distance_desc")
    meters = det.get("distance_m")
    if cx < 0.33:
        phrase = "on the left"
    elif cx > 0.66:
        phrase = "on the right"
    else:
        phrase = "straight ahead"
    if meters and 1.0 <= meters <= 30.0:
        return f"{phrase}, about {int(round(meters))} meters away"
    if dist and dist not in ("very close", "arm's length"):
        return f"{phrase}, {dist}"
    return phrase


def build_scene_text(detections: list) -> str:
    """Structured natural-language description of detections (for LLM prompt + fallback)."""
    if not detections:
        return ""
    groups: dict[str, dict] = {}
    for d in detections:
        label = d.get("label", "object")
        g = groups.setdefault(label, {"count": 0, "zones": []})
        g["count"] += 1
        g["zones"].append(_frame_zone(d))
    rows = []
    for label, g in sorted(groups.items(), key=lambda kv: -kv[1]["count"]):
        n = g["count"]
        zones = list(dict.fromkeys(g["zones"]))
        zone = zones[0] if len(zones) == 1 else "across the frame"
        if n == 1:
            rows.append(f"{label} {zone}")
        else:
            rows.append(f"{n} {label}s {zone}")
    return "; ".join(rows[:8])


def scene_key(detections: list) -> str:
    counts: dict[str, int] = {}
    for d in detections:
        label = d.get("label", "object")
        counts[label] = counts.get(label, 0) + 1
    return json.dumps(sorted(counts.items()), sort_keys=True)


async def _llm_describe(scene: str, sensors: dict, avatar: str) -> str | None:
    name, style = AVATAR_STYLE.get(avatar or "puppy", AVATAR_STYLE["puppy"])
    ctx = build_sensor_context(sensors)
    prompt = (
        f"You are {name}. Voice: {style}. You narrate live camera scenes — "
        "once, naturally, like a person glancing at what's in front of you. "
        "No object lists, no 'detected', no 'targets', no jargon. "
        "One or two short spoken sentences. Plain text, no markdown, no emoji. "
        "Only describe what is actually listed in the scene — never invent history, "
        "age, usage, conversations, or events. If there's little to see, say so plainly. "
        "Help someone who cannot see: place each thing in space as they face the camera — "
        "straight ahead, off to the left or right — and say how close it is "
        "(arm's reach, a few steps away, or meters). Speak as orientation, not inventory.\n\n"
        f"Scene: {scene}\n"
        f"Context: {ctx or 'none'}\n"
        "Describe it:"
    )
    try:
        import httpx

        payload = {
            "model": VISION_LLM_MODEL,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": 0.7, "num_ctx": 1024},
        }
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.post(f"{OLLAMA_URL}/api/generate", json=payload)
            if r.status_code == 200:
                text = r.json().get("response", "").strip()
                if text:
                    return text.splitlines()[0][:300]
    except Exception as e:
        log.debug(f"LLM scene describe failed: {e}")
    return None


async def _moondream_describe(
    frame_b64: str, scene: str, sensors: dict, avatar: str
) -> str | None:
    """True vision-LLM description: moondream (Apache 2.0) sees the actual
    frame via Ollama /api/generate with the image attached. Prefers this over
    the scene-text LLM when the caller supplied a frame. Falls back silently."""
    global _moondream_available, _moondream_lock
    if not VISION_MOONDREAM_ENABLED or not frame_b64 or not _moondream_available:
        return None
    try:
        import asyncio

        if _moondream_lock is None:
            _moondream_lock = asyncio.Semaphore(1)  # type: ignore[attr-defined]
    except Exception:
        return None
    name, style = AVATAR_STYLE.get(avatar or "puppy", AVATAR_STYLE["puppy"])
    ctx = build_sensor_context(sensors)
    prompt = (
        f"You are {name}. Voice: {style}. You are looking at a live photo "
        "from a phone camera. Tell me, in one or two short spoken sentences, "
        "what is actually in front of you right now. "
        "No object lists, no 'detected', no jargon, no markdown, no emoji. "
        "Say what kind of place it looks like and the specific things you can "
        "actually see, placed in space: straight ahead, off to the left or "
        "right — and roughly how close they are (arm's reach, a few steps "
        "away, or meters). Help someone who cannot see. "
        "Never invent people, history, events, or labels that are not visible. "
        f"{('A detector also flagged: ' + scene) if scene else ''}\n"
        f"{f'Context: {ctx}' if ctx else ''}\n"
        "What do you see?"
    )
    try:
        import httpx

        payload = {
            "model": VISION_LLM_VISION_MODEL,
            "prompt": prompt,
            "images": [frame_b64],  # Ollama accepts base64 jpeg/png
            "stream": False,
            "options": {"temperature": 0.6, "num_ctx": 1024},
        }
        async with _moondream_lock:  # type: ignore[union-attr]
            async with httpx.AsyncClient(timeout=VISION_MOONDREAM_TIMEOUT) as client:
                r = await client.post(f"{OLLAMA_URL}/api/generate", json=payload)
                if r.status_code == 200:
                    text = r.json().get("response", "").strip()
                    if text:
                        return text.splitlines()[0][:400]
    except Exception as e:
        log.warning(f"Moondream describe failed: {e}")
        _moondream_available = False  # don't hammer on every frame
    return None


def describe_scene_fallback(detections: list) -> str:
    """Readable fallback that still avoids raw label lists."""
    if not detections:
        return random.choice(
            [
                "Nothing moving right now. The room's quiet.",
                "All clear. Empty frame — either calm or very still.",
                "No action out there. Just space.",
            ]
        )
    rows = []
    groups: dict[str, dict] = {}
    for d in detections:
        label = d.get("label", "object")
        g = groups.setdefault(label, {"count": 0, "zones": []})
        g["count"] += 1
        g["zones"].append(_frame_zone(d))
    for label, g in sorted(groups.items(), key=lambda kv: -kv[1]["count"]):
        n = g["count"]
        zones = list(dict.fromkeys(g["zones"]))
        zone = zones[0] if len(zones) == 1 else "spread across the frame"
        if n == 1:
            rows.append(f"{label} {zone}")
        else:
            rows.append(f"{n} {label}s {zone}")
    pad = (
        " I recognize a few of you."
        if any(
            d.get("label") != "person" and d.get("original_label") == "person"
            for d in detections
        )
        else ""
    )
    return "Right now I can see " + ", ".join(rows[:6]) + "." + pad


async def generate_reply(
    detections: list,
    sensors: dict | None = None,
    avatar: str = "",
    frame_b64: str | None = None,
) -> str:
    """Produce ONE cohesive scene description in the active avatar's voice,
    de-duplicated so the same scene isn't re-narrated on every frame.
    Description chain: moondream (actual pixels) → scene-text LLM → template."""
    global _scene_cache
    key = scene_key(detections)
    if (
        key == _scene_cache.get("key")
        and avatar == _scene_cache.get("avatar")
        and (time.time() - _scene_cache.get("ts", 0)) < SCENE_TTL_SECS
    ):
        return _scene_cache["text"]

    scene = build_scene_text(detections)
    if not scene:
        # YOLO saw nothing — a quiet frame is still a scene worth seeing.
        # Moondream describes the actual pixels; template only as last resort.
        text = await _moondream_describe(frame_b64 or "", scene, sensors or {}, avatar)
        if text is None:
            text = describe_scene_fallback([])
    else:
        # 1) true vision-LLM on the real frame when we have one
        text = await _moondream_describe(frame_b64 or "", scene, sensors or {}, avatar)
        # 2) scene-text LLM over the YOLO labels
        if text is None:
            text = await _llm_describe(scene, sensors or {}, avatar)
        # 3) read template
        if text is None:
            text = describe_scene_fallback(detections)

    _scene_cache = {"key": key, "avatar": avatar, "ts": time.time(), "text": text}
    return text


def generate_agent_replies(text: str, avatar: str) -> list:
    """Single speaking agent — the active avatar only."""
    agent = AGENTS.get(avatar or "puppy", AGENTS["puppy"])
    return [
        {
            "agent": agent["name"],
            "emoji": agent["emoji"],
            "voice": agent["voice"],
            "text": text,
        }
    ]


@asynccontextmanager
async def lifespan(app: FastAPI):
    global MODEL, FACE_ENGINE
    log.info(f"Loading YOLO model: {MODEL_NAME}")

    # Use MODEL_PATH if specified, otherwise use MODEL_NAME
    model_to_load = MODEL_PATH if MODEL_PATH else MODEL_NAME
    MODEL = YOLO(model_to_load)
    log.info(f"YOLO model loaded: {model_to_load}")
    log.info(f"Confidence threshold: {CONF_THRESHOLD}")
    log.info(f"IOU threshold: {IOU_THRESHOLD}")
    log.info(f"Max detections: {MAX_DETECTIONS}")

    # Initialize face recognition engine
    if FACE_ENGINE is None and get_face_engine is not None:
        try:
            FACE_ENGINE = get_face_engine()
            log.info(
                f"Face recognition engine loaded — {len(FACE_ENGINE.known_faces)} known faces"
            )
            # Tier1 FAISS confirms → forward to lilly-ai for alert + remember flow.
            try:
                from face_recognition_engine import on_identity_confirmed

                on_identity_confirmed(
                    lambda n, c, s, e: _post_identity_event(n, c, f"faiss:{s}", e)
                )
                log.info("Tier1 identity events → lilly-ai /api/faces/events")
            except Exception as hook_err:
                log.debug(f"identity hook unavailable: {hook_err}")
        except Exception as e:
            log.warning(f"Face recognition engine failed to load: {e}")
            FACE_ENGINE = False

    # Preload vehicle make/model classifier in the background (downloads on first run)
    try:
        import car_classifier

        threading.Thread(target=car_classifier.preload, daemon=True).start()
        log.info("Vehicle make/model classifier: background load started")
    except Exception as e:
        log.warning(f"Vehicle classifier unavailable: {e}")

    # Moondream availability check (best-effort, async so boot isn't blocked)
    async def _check_moondream():
        global _moondream_available
        try:
            import httpx

            async with httpx.AsyncClient(timeout=3.0) as client:
                r = await client.get(f"{OLLAMA_URL}/api/tags")
                if r.status_code == 200:
                    names = [m.get("name", "") for m in r.json().get("models", [])]
                    hit = any(
                        n.startswith(VISION_LLM_VISION_MODEL.split(":")[0])
                        for n in names
                    )
                    if hit:
                        log.info(
                            f"Vision LLM available: {VISION_LLM_VISION_MODEL} "
                            f"({'ON' if VISION_MOONDREAM_ENABLED else 'disabled by env'}) — "
                            "describes actual pixels, falls back to "
                            f"{VISION_LLM_MODEL} → template on failure"
                        )
                    else:
                        log.warning(
                            f"Vision LLM {VISION_LLM_VISION_MODEL} not found in Ollama — "
                            f"falling back to {VISION_LLM_MODEL} for descriptions"
                        )
                        _moondream_available = False
        except Exception as e:
            log.warning(f"Moondream check skipped: {e}")

    try:
        app.state.moondream_task = asyncio.create_task(_check_moondream())
    except Exception:
        pass

    # OSINT push loop: forward unsolved face crops to lilly-ai's scrapidy tier.
    osint_task = None
    if OSINT_PUSH_URL:
        osint_task = asyncio.create_task(_osint_push_loop())
        log.info(f"Unknown-face OSINT push enabled → {OSINT_PUSH_URL}")

    yield
    if osint_task:
        osint_task.cancel()
    log.info("Shutting down.")


app = FastAPI(title="Lilly Vision Server", lifespan=lifespan)

_BOOT_TIME = time.time()


@app.get("/health")
@app.get("/api/health")
async def health():
    return {
        "status": "ok",
        "service": "lilly-vision",
        "yolo": MODEL is not None,
        "uptime_sec": int(time.time() - _BOOT_TIME),
    }


@app.get("/api/vision/status")
async def vision_status():
    return {
        "opencv": True,
        "yolo": MODEL is not None,
        "model": MODEL_NAME,
        "confidence": CONF_THRESHOLD,
        "tts": "edge-tts",
        "agents": len(AGENTS),
        "modes": list(VISION_MODES),
        "mode_default": "auto",
    }


@app.get("/api/vision/classes")
async def vision_classes():
    """Full class list of the loaded model + current enable toggles.

    Used by the admin training console (:8098/training → Objects panel).
    """
    names: list = list(MODEL.names.values()) if MODEL is not None else []
    toggles = _class_toggles()
    default = _class_default_enabled()
    seen: dict = {}
    for n in names:
        seen.setdefault(str(n).strip().lower(), str(n))
    classes = [
        {
            "name": label,
            "key": key,
            "enabled": toggles.get(key, default),
        }
        for key, label in seen.items()
    ]
    return {
        "ok": True,
        "model": MODEL_NAME,
        "count": len(classes),
        "default_enabled": default,
        "classes": classes,
    }


@app.post("/api/vision")
async def vision_detect(request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"error": "Invalid JSON"})

    image_b64 = body.get("image_b64", "")
    if not image_b64:
        return JSONResponse(status_code=400, content={"error": "Missing image_b64"})

    avatar = body.get("avatar", "puppy")
    mode = (body.get("mode", "auto") or "auto").strip().lower() or "auto"
    sensors = await _effective_sensors(body.get("sensors", {}))
    generate_audio = body.get("generate_audio", False)

    t0 = time.time()

    try:
        raw = base64.b64decode(image_b64)
        buf = np.frombuffer(raw, dtype=np.uint8)
        frame = cv2.imdecode(buf, cv2.IMREAD_COLOR)
        if frame is None:
            return JSONResponse(
                status_code=400, content={"error": "Could not decode image"}
            )
    except Exception as e:
        return JSONResponse(
            status_code=400, content={"error": f"Image decode error: {e}"}
        )

    # Keep the most recent real frame (capped size) for manual re-observe.
    if image_b64 and len(image_b64) <= 700000:
        _LAST_FRAME["ts"] = time.time()
        _LAST_FRAME["b64"] = image_b64

    if MODEL is None:
        return JSONResponse(status_code=503, content={"error": "YOLO model not loaded"})

    try:
        work, sx, sy = _resize_for_inference(frame)
        results = cast(
            list,
            await asyncio.to_thread(MODEL, work, conf=CONF_THRESHOLD, verbose=False),
        )
    except Exception as e:
        log.error(f"YOLO inference error: {e}")
        return JSONResponse(status_code=500, content={"error": f"Inference error: {e}"})

    detections = []
    h, w = frame.shape[:2]
    for r in results:
        if r.boxes is None:
            continue
        for box in r.boxes:
            x1, y1, x2, y2 = box.xyxy[0].cpu().numpy()
            # Map boxes back to the ORIGINAL frame coordinate space
            x1, y1, x2, y2 = x1 * sx, y1 * sy, x2 * sx, y2 * sy
            conf = float(box.conf[0])
            cls_id = int(box.cls[0])
            label = MODEL.names[cls_id]
            est = estimate_distance(label, int(x2 - x1), w)

            nx = float(x1) / w
            ny = float(y1) / h
            nw = float(x2 - x1) / w
            nh = float(y2 - y1) / h

            detections.append(
                {
                    "label": label,
                    "class": label,
                    "x": round(nx, 4),
                    "y": round(ny, 4),
                    "w": round(nw, 4),
                    "h": round(nh, 4),
                    "conf": round(conf, 4),
                    "distance_m": est,
                    "distance_desc": distance_desc(est) if est else None,
                }
            )

    # ── Admin class toggles: drop classes switched OFF in the training console
    detections = _filter_disabled_classes(detections)

    # ── Face recognition: rename "person" → "John" etc. ──────────────
    global FACE_ENGINE, _face_enrich_cache, _vehicle_enrich_cache, _blink_ts
    if FACE_ENGINE is None:
        try:
            FACE_ENGINE = get_face_engine()
        except Exception:
            FACE_ENGINE = False  # prevent retry

    now = time.time()
    if FACE_ENGINE and any(
        d.get("label", "").lower() in PERSON_KEYWORDS for d in detections
    ):
        if now - _face_enrich_cache["ts"] >= FACE_ENRICH_INTERVAL:
            try:
                detections = await asyncio.to_thread(
                    FACE_ENGINE.enrich_person_detections, frame, detections
                )
                detections = _apply_osint_results(detections, now)
                _sample_unknown_faces(frame, detections, now)
                _face_enrich_cache = {"ts": now, "items": _face_cache_items(detections)}
            except Exception as e:
                log.warning(f"Face recognition error: {e}")
        else:
            detections = _apply_cached_ids(
                detections, _face_enrich_cache, "original_label"
            )

    # ── Vehicle make/model: turn "Car" into "2012 BMW X5" ──────────────
    try:
        import car_classifier

        if car_classifier.is_ready() and any(
            d.get("class", "").lower() in car_classifier._VEHICLE_LABELS
            for d in detections
        ):
            if now - _vehicle_enrich_cache["ts"] >= CAR_CLASSIFY_INTERVAL:
                detections = await asyncio.to_thread(
                    car_classifier.enrich_vehicle_detections, frame, detections
                )
                _vehicle_enrich_cache = {
                    "ts": now,
                    "items": [
                        {
                            "label": d.get("label"),
                            "make": d.get("make"),
                            "model": d.get("model"),
                            "year": d.get("year"),
                            "car_conf": d.get("car_conf"),
                            "box": {
                                "x": d.get("x", 0),
                                "y": d.get("y", 0),
                                "w": d.get("w", 0),
                                "h": d.get("h", 0),
                            },
                        }
                        for d in detections
                        if d.get("make")
                    ],
                }
            else:
                detections = _apply_cached_ids(
                    detections, _vehicle_enrich_cache, "make"
                )
    except Exception as e:
        log.debug(f"Vehicle classifier skipped: {e}")

    # ── Blink detection: detect eye blinks in faces ──────────────────
    global BLINK_DETECTOR
    if BLINK_DETECTOR is None:
        try:
            BLINK_DETECTOR = get_blink_detector()
        except Exception:
            BLINK_DETECTOR = False  # prevent retry

    blink_info = None
    if (
        BLINK_DETECTOR
        and any(d.get("label", "").lower() in ["person", "face"] for d in detections)
        and now - _blink_ts >= BLINK_INTERVAL
    ):
        try:
            blink_result = await asyncio.to_thread(
                BLINK_DETECTOR.detect, frame, int(time.time() * 1000)
            )
            _blink_ts = now
            if blink_result:
                blink_info = {
                    "is_blinking": blink_result.is_blinking,
                    "ear_score": round(blink_result.ear_score, 4),
                    "blink_count": blink_result.blink_count,
                    "eyes_closed_ratio": round(blink_result.eyes_closed_ratio, 4),
                    "confidence": round(blink_result.confidence, 4),
                }
                # Add blink info to person/face detections
                for det in detections:
                    if det.get("label", "").lower() in ["person", "face"]:
                        det["blink"] = blink_info
                        break
        except Exception as e:
            log.warning(f"Blink detection error: {e}")

    # ── Observation mode: filter + priority annotate (traffic-aware) ──
    resolved_mode, detections = apply_vision_mode(detections, mode, now)

    # Forward live observations → training cache (fire-and-forget, never blocks)
    if detections:
        asyncio.create_task(
            _forward_observations(detections, image_b64, "lilly-camera", (h, w))
        )

    # Device forward speed from phone GPS (km/h) if available.
    # Callers (drive watch / PiP) can pass it directly; otherwise derive it
    # from the merged phone sensor context.
    device_speed_kph = body.get("device_speed_kph")
    if device_speed_kph is None:
        device_speed_kph = None
        try:
            raw = sensors.get("sensors") or {}
            gps = raw.get("gps_speed") or sensors.get("gps_speed")
            if gps is None:
                loc = raw.get("location") or sensors.get("location")
                gps = loc.get("speed") if isinstance(loc, dict) else None
            if gps is not None:
                device_speed_kph = round(float(gps) * 3.6, 1)
        except Exception:
            pass
    if device_speed_kph is not None:
        try:
            device_speed_kph = float(device_speed_kph)
        except Exception:
            device_speed_kph = None

    # Motion magnitude in g (Termux gravity/linear-accel/significant-motion).
    # Used by _drive_alerts to gate PEDESTRIAN/FORWARD_COLLISION on real
    # movement — callers may pass it, else derive from the phone sensors.
    motion_g = body.get("motion_g")
    if motion_g is None:
        motion_g = _motion_from_sensors(sensors)
    if motion_g is not None:
        try:
            motion_g = float(motion_g)
        except Exception:
            motion_g = None

    # GPS says we're rolling fast — treat as driving even if the scene is
    # sparse (few vehicles in frame yet). Keeps sign/light alerts armed.
    if (
        (mode in ("auto", ""))
        and device_speed_kph is not None
        and device_speed_kph >= 25.0
        and resolved_mode == MODE_STATIONARY
    ):
        resolved_mode, detections = apply_vision_mode(detections, MODE_DRIVING, now)

    overlay = overlay_summary(detections, resolved_mode, device_speed_kph)

    # Deterministic drive-safety alerts (stop sign / pedestrian / lights / FCW).
    # motion_g gates pedestrian + forward-collision so a parked neighbour's
    # car or a porch "pedestrian" can't trigger braking alarms while parked.
    alerts = (
        _drive_alerts(detections, device_speed_kph, now, motion_g=motion_g)
        if resolved_mode == MODE_DRIVING
        else []
    )
    _DRIVE_LAST.update(
        {
            "ts": time.time(),
            "mode": resolved_mode,
            "device_speed_kph": device_speed_kph,
            "motion_g": motion_g,
            "alerts": alerts,
        }
    )

    elapsed = round(time.time() - t0, 3)
    if generate_audio:
        reply = await generate_reply(detections, sensors, avatar, frame_b64=image_b64)
    else:
        scene_txt = build_scene_text(detections)
        reply = describe_scene_fallback(detections) if not scene_txt else scene_txt
    agents = generate_agent_replies(reply, avatar)

    if generate_audio and agents:
        agent_cfg = AGENTS.get(avatar or "puppy", AGENTS["puppy"])
        voice = agent_cfg.get("voice", "en-US-JennyNeural")
        rate = agent_cfg.get("rate", "+0%")
        audio_id = await generate_tts(reply, voice, rate)
        agents[0]["audio_id"] = audio_id

    log.info(
        f"Detected {len(detections)} targets in {elapsed}s — mode={resolved_mode} ({avatar or 'puppy'})"
    )

    response = {
        "reply": reply,
        "agents": agents,
        "detections": detections,
        "audio_id": 0,
        # Navigation/observation HUD data (driving: traffic lights, signs,
        # cars ahead with distance + closing speed)
        "mode": resolved_mode,
        "mode_requested": mode,
        "overlay": overlay,
        "device_speed_kph": device_speed_kph,
        "motion_g": motion_g,
        "alerts": alerts,
        "latency_ms": round(elapsed * 1000, 1),
        "dropped": max(0, len(detections) - overlay["total"]),
    }

    # Add blink info to response if available
    if blink_info:
        response["blink"] = blink_info

    return response


# ── Observation → training cache forwarding ─────────────────────────────
TRAINER_URL = os.environ.get("TRAINER_URL", "http://127.0.0.1:8199")
OBSERVE: list = []
OBSERVE_LIMIT = 80
_OBSERVE_RATE: dict = {}  # {label: last forward ts} — floor per label
OBSERVE_MIN_INTERVAL: float = float(
    os.environ.get("OBSERVE_MIN_INTERVAL", "1.0") or 1.0
)
_TRAINER_CFG: dict = {}
_TRAINER_CFG_TS: float = 0.0
_TRAINER_CFG_TTL: float = 30.0


async def _trainer_config_cached() -> dict:
    """Trainer config from :8199, cached briefly so we don't hammer it."""
    global _TRAINER_CFG, _TRAINER_CFG_TS
    now = time.time()
    if _TRAINER_CFG and _TRAINER_CFG_TS + _TRAINER_CFG_TTL > now:
        return _TRAINER_CFG
    import httpx

    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            r = await client.get(f"{TRAINER_URL}/api/trainer/config")
            if r.status_code == 200:
                data = r.json()
                _TRAINER_CFG = data.get("config", {}) if isinstance(data, dict) else {}
                if _TRAINER_CFG:
                    _TRAINER_CFG_TS = now
    except Exception as e:  # noqa: BLE001
        log.warning(f"trainer config fetch failed: {e}")
    return _TRAINER_CFG


def _observe_push(obs: dict) -> None:
    OBSERVE.append(obs)
    del OBSERVE[:-OBSERVE_LIMIT]


async def _forward_observations(
    detections: list, image_b64: str, source: str, frame_shape: tuple
) -> None:
    """Record every detection as a live observation and cache qualifying
    frames (with YOLO-format labels) to the trainer for real training data.
    Fire-and-forget; never raises."""
    try:
        cfg = await _trainer_config_cached()
        auto = bool(cfg.get("auto_collect", True))
        lo = float(cfg.get("collect_threshold", 0.3) or 0.3)
        hi = float(cfg.get("correction_threshold", 0.7) or 0.7)
        fh, fw = frame_shape[:2]
        now = time.time()
        import httpx

        client = httpx.AsyncClient(timeout=8.0)
        try:
            for d in detections:
                label = str(d.get("label", "")).strip() or "unknown"
                conf = float(d.get("conf", 0.0))
                nx, ny = float(d.get("x", 0.0)), float(d.get("y", 0.0))
                nw, nh = float(d.get("w", 0.0)), float(d.get("h", 0.0))
                bbox = [
                    round(nx * fw, 2),
                    round(ny * fh, 2),
                    round((nx + nw) * fw, 2),
                    round((ny + nh) * fh, 2),
                ]
                cached = False
                reason = None
                last = _OBSERVE_RATE.get(label, 0.0)
                if now - last < OBSERVE_MIN_INTERVAL:
                    reason = "rate-limited"
                elif auto and lo <= conf <= hi and image_b64:
                    try:
                        r = await client.post(
                            f"{TRAINER_URL}/api/trainer/detection",
                            json={
                                "source": source,
                                "label": label,
                                "confidence": conf,
                                "bbox": bbox,
                                "image": image_b64,
                            },
                        )
                        if r.status_code < 300:
                            data = r.json()
                            cached = bool(data.get("cached"))
                            reason = (
                                None
                                if cached
                                else str(data.get("error", "db-only"))[:80]
                            )
                            if cached:
                                _OBSERVE_RATE[label] = now
                        else:
                            reason = f"trainer HTTP {r.status_code}"
                    except Exception as e:  # noqa: BLE001
                        reason = f"trainer unreachable: {str(e)[:80]}"
                else:
                    if not auto:
                        reason = "auto_collect off"
                    elif conf < lo:
                        reason = "below collect_threshold"
                    elif conf > hi:
                        reason = "correction pool range"
                    elif not image_b64:
                        reason = "no frame"
                _observe_push(
                    {
                        "ts": now,
                        "source": source,
                        "label": label,
                        "conf": round(conf, 4),
                        "cached": cached,
                        "reason": reason,
                    }
                )
        finally:
            await client.aclose()
    except Exception as e:  # noqa: BLE001
        log.warning(f"observe forward error: {e}")


@app.get("/api/vision/observations")
async def vision_observations():
    """Live observation feed (newest first) + upstream trainer URL."""
    return {
        "ok": True,
        "trainer_url": TRAINER_URL,
        "observations": list(reversed(OBSERVE)),
    }


@app.get("/api/vision/drive")
async def vision_drive_state():
    """Latest deterministic drive-safety state (alerts, resolved mode, GPS speed).

    Polled by lightweight surfaces (PiP / overlay) that don't post frames.
    """
    return {
        "ok": True,
        "ts": _DRIVE_LAST["ts"],
        "age_s": int(time.time() - _DRIVE_LAST["ts"]) if _DRIVE_LAST["ts"] else None,
        "mode": _DRIVE_LAST["mode"],
        "device_speed_kph": _DRIVE_LAST["device_speed_kph"],
        "alerts": _DRIVE_LAST["alerts"],
    }


# Last real camera frame (for manual re-observe; capped base64 size)
_LAST_FRAME: dict = {"ts": 0.0, "b64": ""}


@app.get("/api/vision/last_frame")
async def vision_last_frame():
    """The most recent real frame the vision server analyzed (capped size)."""
    if not _LAST_FRAME.get("b64"):
        return {
            "ok": False,
            "error": "no camera frame seen yet — open the webcam page or the phone overlay first",
        }
    return {
        "ok": True,
        "ts": _LAST_FRAME["ts"],
        "age_s": int(time.time() - _LAST_FRAME["ts"]),
        "image_b64": _LAST_FRAME["b64"],
    }


# ── Proactive vision state ──────────────────────────────────────────────
_last_proactive_labels: set = set()
_last_proactive_time: float = 0.0
_PROACTIVE_COOLDOWN: float = 90.0  # seconds between proactive comments


@app.post("/api/vision/proactive")
async def vision_proactive(request: Request):
    """
    Lightweight scan that only returns results when the scene changes
    meaningfully. Designed for periodic idle checks — returns
    {"should_speak": false} when nothing new is happening.
    """
    global _last_proactive_labels, _last_proactive_time

    try:
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"error": "Invalid JSON"})

    image_b64 = body.get("image_b64", "")
    if not image_b64:
        return JSONResponse(status_code=400, content={"error": "Missing image_b64"})

    avatar = body.get("avatar", "puppy")
    sensors = await _effective_sensors(body.get("sensors", {}))

    # Cooldown check — don't nag the user
    now = time.time()
    if now - _last_proactive_time < _PROACTIVE_COOLDOWN:
        return {"should_speak": False, "reason": "cooldown"}

    if MODEL is None:
        return {"should_speak": False, "reason": "model_not_loaded"}

    try:
        raw = base64.b64decode(image_b64)
        buf = np.frombuffer(raw, dtype=np.uint8)
        frame = cv2.imdecode(buf, cv2.IMREAD_COLOR)
        if frame is None:
            return {"should_speak": False, "reason": "decode_error"}
    except Exception:
        return {"should_speak": False, "reason": "decode_error"}

    try:
        work, _, _ = _resize_for_inference(frame)
        results = cast(list, MODEL(work, conf=CONF_THRESHOLD, verbose=False))
    except Exception:
        return {"should_speak": False, "reason": "inference_error"}

    current_labels = set()
    for r in results:
        if r.boxes is None:
            continue
        for box in r.boxes:
            cls_id = int(box.cls[0])
            label = MODEL.names[cls_id]
            current_labels.add(label)

    # Scene-change detection:
    # - New labels appeared that weren't there before
    # - Previous scene had labels but now empty (something left)
    # - Enough time has passed since last speak
    new_labels = current_labels - _last_proactive_labels
    scene_changed = (
        (len(new_labels) > 0)
        or (len(_last_proactive_labels) > 0 and len(current_labels) == 0)
        or (len(current_labels) > 0 and len(_last_proactive_labels) == 0)
    )

    if not scene_changed:
        _last_proactive_labels = current_labels
        return {
            "should_speak": False,
            "reason": "no_change",
            "labels": list(current_labels),
        }

    # Scene changed — generate commentary
    _last_proactive_labels = current_labels
    _last_proactive_time = now

    detections = []
    h, w = frame.shape[:2]
    for r in results:
        if r.boxes is None:
            continue
        for box in r.boxes:
            x1, y1, x2, y2 = box.xyxy[0].cpu().numpy()
            conf = float(box.conf[0])
            cls_id = int(box.cls[0])
            label = MODEL.names[cls_id]
            est = estimate_distance(label, int(x2 - x1), w)
            detections.append(
                {
                    "label": label,
                    "class": label,
                    "x": round(float(x1) / w, 4),
                    "y": round(float(y1) / h, 4),
                    "w": round(float(x2 - x1) / w, 4),
                    "h": round(float(y2 - y1) / h, 4),
                    "conf": round(conf, 4),
                    "distance_m": est,
                    "distance_desc": distance_desc(est) if est else None,
                }
            )

    try:
        import car_classifier

        if car_classifier.is_ready():
            detections = car_classifier.enrich_vehicle_detections(frame, detections)
    except Exception as e:
        log.debug(f"Vehicle classifier skipped: {e}")

    reply = await generate_reply(detections, sensors, avatar, frame_b64=image_b64)
    agents = generate_agent_replies(reply, avatar)

    # Generate TTS for primary agent
    audio_id = 0
    if agents:
        primary = agents[0]
        agent_cfg = AGENTS.get(avatar or "puppy", AGENTS["puppy"])
        voice = agent_cfg.get("voice", "en-US-JennyNeural")
        rate = agent_cfg.get("rate", "+0%")
        audio_id = await generate_tts(reply, voice, rate)

    log.info(f"Proactive: scene changed — {len(detections)} targets, speaking")

    return {
        "should_speak": True,
        "reply": reply,
        "agents": agents,
        "detections": detections,
        "audio_id": audio_id,
    }


@app.get("/api/tts")
async def get_tts(id: int = 0):
    """Serve generated TTS audio."""
    path = _audio_map.get(id)
    if path and path.exists():
        return FileResponse(str(path), media_type="audio/mpeg")
    return JSONResponse(status_code=404, content={"error": "Audio not found"})


@app.post("/api/cmd")
async def chat_cmd(request: Request):
    body = await request.json()
    text = body.get("text", "")
    return {"reply": f"Lilly received: {text}", "audio_id": 0}


# ── Face Recognition Management ────────────────────────────────────────


@app.get("/api/faces")
async def list_faces():
    """List all known faces in the database."""
    if FACE_ENGINE is None:
        return {"faces": [], "error": "face engine not loaded"}
    return {"faces": FACE_ENGINE.list_known_faces()}


@app.post("/api/faces/enroll")
async def enroll_face(request: Request):
    """
    Enroll a face from a base64 image.
    Body: {"name": "John", "image_b64": "...", "source": "manual"}
    """
    if not FACE_ENGINE:
        return JSONResponse(
            status_code=503, content={"error": "face engine not loaded"}
        )

    body = await request.json()
    name = body.get("name", "").strip()
    image_b64 = body.get("image_b64", "")
    source = body.get("source", "api")

    if not name:
        return JSONResponse(status_code=400, content={"error": "name required"})
    if not image_b64:
        return JSONResponse(status_code=400, content={"error": "image_b64 required"})

    try:
        raw = base64.b64decode(image_b64)
        buf = np.frombuffer(raw, dtype=np.uint8)
        frame = cv2.imdecode(buf, cv2.IMREAD_COLOR)
        if frame is None:
            return JSONResponse(
                status_code=400, content={"error": "could not decode image"}
            )

        faces = FACE_ENGINE.detect_faces(frame)
        if not faces:
            return JSONResponse(
                status_code=400, content={"error": "no face detected in image"}
            )

        largest = max(faces, key=lambda f: f["w"] * f["h"])
        success = FACE_ENGINE.add_known_face(name, frame, largest, source=source)
        if success:
            return {"ok": True, "name": name, "faces_detected": len(faces)}
        return JSONResponse(status_code=500, content={"error": "enrollment failed"})
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.delete("/api/faces/{name}")
async def remove_face(name: str):
    """Remove a known face by name."""
    if not FACE_ENGINE:
        return JSONResponse(
            status_code=503, content={"error": "face engine not loaded"}
        )
    success = FACE_ENGINE.remove_known_face(name)
    return {"ok": success, "name": name}


@app.get("/api/faces/identify")
async def identify_test(request: Request):
    """
    Test face identification on an image.
    Body: {"image_b64": "..."}
    """
    if not FACE_ENGINE:
        return JSONResponse(
            status_code=503, content={"error": "face engine not loaded"}
        )

    body = await request.json()
    image_b64 = body.get("image_b64", "")
    if not image_b64:
        return JSONResponse(status_code=400, content={"error": "image_b64 required"})

    try:
        raw = base64.b64decode(image_b64)
        buf = np.frombuffer(raw, dtype=np.uint8)
        frame = cv2.imdecode(buf, cv2.IMREAD_COLOR)
        if frame is None:
            return JSONResponse(
                status_code=400, content={"error": "could not decode image"}
            )

        faces = FACE_ENGINE.detect_faces(frame)
        results = []
        for face in faces:
            match = FACE_ENGINE.identify_face(frame, face)
            results.append(
                {
                    "box": face,
                    "identified": match is not None,
                    "name": match["name"] if match else None,
                    "confidence": match["confidence"] if match else None,
                }
            )

        return {"faces_found": len(results), "results": results}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


# ── Person Tracker Endpoints ────────────────────────────────────────────


@app.get("/api/vision/unknown_faces")
async def unknown_faces_list():
    """Debug: unsolved people queued for OSINT + resolved results map."""
    return {
        "count": len(_unknown_faces),
        "osint_results": len(_osint_results),
        "faces": [
            {
                "id": e["id"],
                "person_box": e["person_box"],
                "resolved": e.get("resolved"),
            }
            for e in _unknown_faces
        ],
    }


@app.post("/api/vision/face/identify")
async def push_face_identity(request: Request):
    """lilly-ai pushes back an OSINT identity for an unknown face."""
    body = await request.json()
    res_id = body.get("id")
    if not res_id:
        return JSONResponse(status_code=400, content={"error": "id required"})

    entry = next((e for e in _unknown_faces if e.get("id") == res_id), None)
    _osint_results[res_id] = {
        "name": body.get("name"),
        "confidence": body.get("confidence"),
        "social_accounts": body.get("social_accounts", []),
        "sources": body.get("sources", []),
        "images": body.get("images", []),
        "person_box": body.get("person_box") or (entry or {}).get("person_box", {}),
        "ts": time.time(),
    }
    if entry:
        entry["osint_ts"] = time.time()
        if body.get("name"):
            entry["resolved"] = body["name"]
    # Auto-enroll: if OSINT found a name with profile images, background
    # SFace-verify the profile photo against the live crop and enroll
    # into FAISS so the next frame matches at 0.97+ directly.
    osint_name = body.get("name")
    osint_images = body.get("images", [])
    osint_crop_b64 = ""
    if entry:
        osint_crop_b64 = entry.get("crop_b64", "")
    if osint_name and osint_images and osint_crop_b64 and FACE_ENGINE:
        prof_url = osint_images[0] if isinstance(osint_images[0], str) else ""
        if prof_url:
            import asyncio as _aio

            _aio.get_running_loop().create_task(
                _aio.to_thread(
                    FACE_ENGINE.auto_enroll_from_osint,
                    osint_name,
                    osint_crop_b64,
                    prof_url,
                )
            )
            log.info(f"OSINT auto-enroll queued for {osint_name}")

    log.info(f"OSINT identity pushed for {res_id}: {body.get('name')}")
    return {"ok": True}


# ── Person Tracker Endpoints ────────────────────────────────────────────


@app.get("/api/tracker/map")
async def tracker_map_data():
    """Get all tracking data for the radar map."""
    try:
        from person_tracker import get_person_tracker

        tracker = get_person_tracker()
        return tracker.get_map_data()
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.get("/api/tracker/trail/{name}")
async def tracker_trail(name: str):
    """Get movement trail for a specific person."""
    try:
        from person_tracker import get_person_tracker

        tracker = get_person_tracker()
        trail = tracker.get_person_trail(name)
        return {"name": name, "trail": trail, "points": len(trail)}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.get("/api/tracker/sightings")
async def tracker_sightings(name: str = None, limit: int = 100):
    """Get recent sightings, optionally filtered by name."""
    try:
        from person_tracker import get_person_tracker

        tracker = get_person_tracker()
        return {"sightings": tracker.get_sightings(name=name, limit=limit)}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.get("/api/tracker/devices")
async def tracker_devices():
    """Get all devices correlated to people."""
    try:
        from person_tracker import get_person_tracker

        tracker = get_person_tracker()
        return {"devices": tracker.get_tracked_devices()}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.get("/api/tracker/activity")
async def tracker_activity(minutes: int = 30):
    """Get recent activity summary."""
    try:
        from person_tracker import get_person_tracker

        tracker = get_person_tracker()
        return tracker.get_recent_activity(minutes=minutes)
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.get("/api/tracker/scan")
async def tracker_scan_now():
    """Force an immediate BLE/WiFi scan."""
    try:
        from person_tracker import get_person_tracker

        tracker = get_person_tracker()
        result = tracker.scan_nearby_devices()
        return result
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.get("/api/ui_state")
async def ui_state():
    return {
        "mood": "curious",
        "speaking": False,
        "vision_active": False,
        "continuous_scan": False,
    }


# ── Node Registry (proxy to lilly-ai or local) ──────────────────────────


@app.get("/api/nodes")
async def api_nodes():
    """List all phone nodes. Tries lilly-ai first, falls back to local registry."""
    # Try proxying to lilly-ai
    try:
        import httpx

        async with httpx.AsyncClient(timeout=3) as client:
            r = await client.get("http://127.0.0.1:8098/api/nodes")
            if r.status_code == 200:
                return r.json()
    except Exception:
        pass
    # Fallback: local node registry
    try:
        from node_registry import get_node_registry

        registry = get_node_registry()
        return registry.to_dict()
    except Exception as e:
        return {"nodes": [], "online_count": 0, "total_count": 0, "error": str(e)}


# ── Radar Map Dashboard ─────────────────────────────────────────────────


@app.get("/tracker")
@app.get("/tracker/")
async def tracker_dashboard():
    """Radar map dashboard — tracks recognized people on a Leaflet map."""
    from fastapi.responses import HTMLResponse

    return HTMLResponse(TRACKER_MAP_HTML)


TRACKER_MAP_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
<title>Lilly Tracker — Multi-Node Radar</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{width:100%;height:100%;overflow:hidden;font-family:-apple-system,'Segoe UI',system-ui,sans-serif;color:#5d4e6d;background:#f0e6ef;display:flex}
#sidebar{width:340px;background:rgba(255,255,255,0.45);backdrop-filter:blur(24px);-webkit-backdrop-filter:blur(24px);padding:14px;display:flex;flex-direction:column;border-right:1px solid rgba(255,255,255,0.6);overflow-y:auto}
#map{flex:1;height:100%}
h2{margin:0 0 10px;font-size:1.1rem;color:#8b7a9e}
h3{font-size:.85rem;color:rgba(93,78,109,0.5);margin:10px 0 6px;text-transform:uppercase;letter-spacing:0.5px}
.stat-box{background:rgba(255,255,255,0.55);backdrop-filter:blur(20px);-webkit-backdrop-filter:blur(20px);padding:10px;border-radius:12px;margin-bottom:10px;font-size:.82rem;border:1px solid rgba(255,255,255,0.6);box-shadow:0 4px 20px rgba(180,140,180,0.12)}
.stat-box strong{color:#8b7a9e}
.node-row{display:flex;align-items:center;gap:8px;padding:8px 10px;border-radius:12px;margin-bottom:6px;background:rgba(255,255,255,0.5);backdrop-filter:blur(16px);-webkit-backdrop-filter:blur(16px);cursor:pointer;border-left:3px solid #b8a9c9;transition:all .2s;border:1px solid rgba(255,255,255,0.5)}
.node-row:hover{background:rgba(255,255,255,0.7);box-shadow:0 2px 12px rgba(180,140,180,0.15)}
.node-dot{width:10px;height:10px;border-radius:50%;flex-shrink:0;box-shadow:0 0 8px currentColor}
.node-info{flex:1;min-width:0}
.node-name{font-weight:700;font-size:.85rem;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.node-meta{font-size:.7rem;color:rgba(93,78,109,0.5);margin-top:2px}
.badge{display:inline-block;padding:2px 8px;border-radius:10px;font-size:.65rem;font-weight:700;margin-left:6px}
.badge-on{background:rgba(76,175,80,0.15);color:#2e7d32}
.badge-off{background:rgba(93,78,109,0.08);color:rgba(93,78,109,0.4)}
.person-card{padding:10px;border-radius:12px;margin-bottom:8px;border-left:3px solid #b8a9c9;cursor:pointer;transition:all .2s;background:rgba(255,255,255,0.5);backdrop-filter:blur(16px);border:1px solid rgba(255,255,255,0.5);box-shadow:0 2px 12px rgba(180,140,180,0.1)}
.person-card:hover{background:rgba(255,255,255,0.7);box-shadow:0 4px 20px rgba(180,140,180,0.18)}
.person-card .name{font-weight:700;font-size:.9rem}
.person-card .meta{color:rgba(93,78,109,0.5);font-size:.75rem;margin-top:3px}
.sighting-item{padding:8px;border-radius:10px;margin-bottom:5px;font-size:.78rem;border-left:2px solid #a892b8;background:rgba(255,255,255,0.45);backdrop-filter:blur(12px);border:1px solid rgba(255,255,255,0.4)}
.view-toggle{display:flex;gap:4px;margin-bottom:10px}
.view-btn{flex:1;padding:7px;border:1px solid rgba(255,255,255,0.5);border-radius:10px;background:rgba(255,255,255,0.4);color:rgba(93,78,109,0.5);font-size:.75rem;font-weight:600;cursor:pointer;text-align:center;transition:all .2s;backdrop-filter:blur(12px)}
.view-btn.active{background:rgba(139,122,158,0.25);color:#5d4e6d;border-color:rgba(139,122,158,0.4);box-shadow:0 2px 8px rgba(180,140,180,0.2)}
.btn{width:100%;padding:10px;border:none;border-radius:12px;font-weight:700;cursor:pointer;font-size:.85rem;margin-bottom:8px;transition:all .2s;backdrop-filter:blur(12px);-webkit-backdrop-filter:blur(12px)}
.btn:active{transform:scale(0.97)}
.btn-scan{background:rgba(139,122,158,0.25);color:#5d4e6d;border:1px solid rgba(139,122,158,0.3)}
.btn-scan:hover{background:rgba(139,122,158,0.35)}
.btn-enroll{background:rgba(255,255,255,0.4);color:rgba(93,78,109,0.6);border:1px solid rgba(255,255,255,0.5)}
.btn-enroll:hover{background:rgba(255,255,255,0.6)}
.empty{text-align:center;color:rgba(93,78,109,0.4);padding:20px;font-size:.8rem}
#enroll-modal{display:none;position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(93,78,109,0.3);backdrop-filter:blur(8px);-webkit-backdrop-filter:blur(8px);z-index:2000;justify-content:center;align-items:center}
#enroll-modal .modal{background:rgba(255,255,255,0.85);backdrop-filter:blur(24px);-webkit-backdrop-filter:blur(24px);padding:24px;border-radius:20px;width:340px;border:1px solid rgba(255,255,255,0.6);box-shadow:0 12px 48px rgba(93,78,109,0.2)}
#enroll-modal input,#enroll-modal select{width:100%;padding:10px 14px;border-radius:12px;border:1px solid rgba(184,169,201,0.3);background:rgba(255,255,255,0.5);color:#5d4e6d;margin-bottom:10px;font-size:.9rem;outline:none;box-sizing:border-box}
#enroll-modal input:focus{border-color:rgba(139,122,158,0.5);box-shadow:0 0 0 2px rgba(184,169,201,0.2)}
#enroll-modal .btn-row{display:flex;gap:8px}
#enroll-modal .btn-row button{flex:1}
#enroll-modal select option{background:#f0e6ef;color:#5d4e6d}
@media(max-width:768px){body{flex-direction:column-reverse}#sidebar{width:100%;height:50vh;border-right:none;border-top:1px solid rgba(255,255,255,0.6)}#map{height:50vh}}
</style>
</head>
<body>
<div id="sidebar">
  <h2>📡 Multi-Node Radar</h2>

  <div class="view-toggle">
    <button class="view-btn active" onclick="setView('combined')" id="v-all">All Nodes</button>
    <button class="view-btn" onclick="setView('n1')" id="v-n1">Node 1</button>
    <button class="view-btn" onclick="setView('n2')" id="v-n2">Node 2</button>
    <button class="view-btn" onclick="setView('n3')" id="v-n3">Node 3</button>
  </div>

  <button class="btn btn-scan" onclick="forceScan()">⚡ Scan All Nodes</button>
  <button class="btn btn-enroll" onclick="showEnroll()">👤 Enroll Face</button>

  <div class="stat-box">
    <div><strong>Network:</strong> <span id="net-status" style="color:#8b7a9e">Connecting...</span></div>
    <div><strong>People:</strong> <span id="ppl-count">0</span> | <strong>Sightings:</strong> <span id="sight-count">0</span></div>
    <div><strong>Spread:</strong> <span id="node-spread">—</span></div>
  </div>

  <h3>Phone Nodes</h3>
  <div id="nodes-list"></div>

  <h3>Known People</h3>
  <div id="people-list"></div>

  <h3>Recent Sightings</h3>
  <div id="sightings-list"></div>
</div>
<div id="map"></div>

<div id="enroll-modal">
  <div class="modal">
    <h3 style="margin-bottom:12px;color:#8b7a9e">Enroll New Face</h3>
    <input id="enroll-name" placeholder="Person name..." />
    <select id="enroll-node">
      <option value="">Auto (closest node)</option>
    </select>
    <input id="enroll-source" placeholder="Source (manual, instagram...)" value="manual" />
    <div class="btn-row">
      <button class="btn btn-scan" onclick="doEnroll()">Enroll</button>
      <button class="btn btn-enroll" onclick="hideEnroll()">Cancel</button>
    </div>
    <p id="enroll-status" style="font-size:.75rem;color:rgba(93,78,109,0.5);margin-top:8px"></p>
  </div>
</div>

<script>
/* ── Theme-matched node colours (lavender palette) ── */
const NODE_COLORS = ['#8b7a9e','#c084fc','#69f0ae'];
const HUMAN_COLORS = ['#a892b8','#c084fc','#69f0ae','#f0abfc','#fbbf24','#67e8f9'];

const map = L.map('map',{zoomControl:false}).setView([0,0],2);
L.control.zoom({position:'topright'}).addTo(map);
L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png',{
  maxZoom:19,attribution:'©CartoDB ©OSM'
}).addTo(map);

let currentView='combined';
let nodeMarkers={};
let personMarkers={};
let personTrails={};
let humanColorIdx=0;
function hColor(){const c=HUMAN_COLORS[humanColorIdx%HUMAN_COLORS.length];humanColorIdx++;return c}

function haversine(lat1,lng1,lat2,lng2){
  const R=6371000,dLat=(lat2-lat1)*Math.PI/180,dLng=(lng2-lng1)*Math.PI/180;
  const a=Math.sin(dLat/2)**2+Math.cos(lat1*Math.PI/180)*Math.cos(lat2*Math.PI/180)*Math.sin(dLng/2)**2;
  return R*2*Math.atan2(Math.sqrt(a),Math.sqrt(1-a));
}

function setView(v){
  currentView=v;
  document.querySelectorAll('.view-btn').forEach(b=>b.classList.remove('active'));
  const el=document.getElementById('v-'+v);if(el)el.classList.add('active');
  for(const[nid,m] of Object.entries(nodeMarkers)){
    if(v==='combined'){m.setStyle({opacity:1,fillOpacity:0.9})}
    else{const show=nid===v||nid===('phone-'+v.replace('n',''));m.setStyle({opacity:show?1:0.12,fillOpacity:show?0.9:0.12})}
  }
  for(const[name,data] of Object.entries(personTrails)){
    data.polyline.setStyle({opacity:v==='combined'?0.65:0.15});
  }
  if(v!=='combined'){
    const target='phone-'+v.replace('n','');
    const m=nodeMarkers[target];
    if(m)map.setView(m.getLatLng(),17);
  }else{fitAllNodes()}
}

function fitAllNodes(){
  const coords=Object.values(nodeMarkers).map(m=>m.getLatLng());
  if(coords.length>1)map.fitBounds(L.latLngBounds(coords).pad(0.3));
  else if(coords.length===1)map.setView(coords[0],16);
}

async function poll(){
  try{
    const[mapR,nodesR,sightR]=await Promise.all([
      fetch('/api/tracker/map').then(r=>r.json()),
      fetch('/api/nodes').then(r=>r.json()).catch(()=>({nodes:[]})),
      fetch('/api/tracker/sightings?limit=30').then(r=>r.json()).catch(()=>({sightings:[]}))
    ]);
    const nodes=nodesR.nodes||[];
    let nodeHtml='',onlineCount=0;
    const gpsNodes=nodes.filter(n=>n.lat&&n.lng);

    for(let i=0;i<nodes.length;i++){
      const n=nodes[i],online=n.online;if(online)onlineCount++;
      const col=NODE_COLORS[i%NODE_COLORS.length];
      const bat=n.battery>=0?' | '+Math.round(n.battery)+'%':'';
      if(n.lat&&n.lng){
        if(!nodeMarkers[n.node_id]){
          nodeMarkers[n.node_id]=L.circleMarker([n.lat,n.lng],{radius:14,fillColor:col,color:'#fff',weight:3,opacity:1,fillOpacity:0.9}).addTo(map).bindPopup('<b>'+n.name+'</b><br>'+n.sensor_url);
        }else{nodeMarkers[n.node_id].setLatLng([n.lat,n.lng])}
        nodeMarkers[n.node_id].setStyle({fillColor:col});
      }
      nodeHtml+='<div class="node-row" style="border-left-color:'+col+'" onclick="focusNode(\\''+n.node_id+'\\',\\''+i+'\\')">'
        +'<div class="node-dot" style="background:'+col+';color:'+col+'"></div>'
        +'<div class="node-info"><div class="node-name">'+n.name+'</div>'
        +'<div class="node-meta">'+(n.sensor_url||'').replace('http://','')+bat+'</div></div>'
        +'<span class="badge '+(online?'badge-on':'badge-off')+'">'+(online?'ONLINE':'OFF')+'</span></div>';
    }
    if(gpsNodes.length>=2){
      let maxD=0;
      for(let i=0;i<gpsNodes.length;i++)for(let j=i+1;j<gpsNodes.length;j++){
        const d=haversine(gpsNodes[i].lat,gpsNodes[i].lng,gpsNodes[j].lat,gpsNodes[j].lng);if(d>maxD)maxD=d;
      }
      const spreadEl=document.getElementById('node-spread');
      if(maxD>1000){spreadEl.textContent=(maxD/1000).toFixed(1)+' km apart';spreadEl.style.color='#c084fc'}
      else if(maxD>0){spreadEl.textContent=Math.round(maxD)+'m apart';spreadEl.style.color='#69f0ae'}
      else{spreadEl.textContent='Same location';spreadEl.style.color='rgba(93,78,109,0.4)'}
    }else{document.getElementById('node-spread').textContent='Need 2+ nodes with GPS'}
    document.getElementById('nodes-list').innerHTML=nodeHtml||'<div class="empty">No nodes registered</div>';
    const sel=document.getElementById('enroll-node'),curVal=sel.value;
    sel.innerHTML='<option value="">Auto (closest node)</option>';
    nodes.forEach((n,i)=>{sel.innerHTML+='<option value="'+n.node_id+'">'+n.name+'</option>'});sel.value=curVal;

    const trails=mapR.trails||{};let pplHtml='';
    for(const[name,pts]of Object.entries(trails)){
      if(!pts||pts.length<1)continue;
      if(!personTrails[name]){const col=hColor();personTrails[name]={color:col,pts:pts,polyline:L.polyline(pts.map(p=>[p[0],p[1]]),{color:col,weight:3,opacity:0.65,dashArray:'6,4'}).addTo(map)}}
      else{personTrails[name].pts=pts;personTrails[name].polyline.setLatLngs(pts.map(p=>[p[0],p[1]]))}
      const last=pts[pts.length-1],col=personTrails[name].color;
      if(!personMarkers[name]){personMarkers[name]=L.circleMarker([last[0],last[1]],{radius:8,fillColor:col,color:'#fff',weight:2,opacity:1,fillOpacity:0.9}).addTo(map).bindPopup('<b>'+name+'</b><br>'+pts.length+' points')}
      else{personMarkers[name].setLatLng([last[0],last[1]])}
      const age=Math.floor((Date.now()/1000-last[2])/60);
      pplHtml+='<div class="person-card" style="border-left-color:'+col+'" onclick="focusPerson(\\''+name+'\\')"><span class="name" style="color:'+col+'">'+name+'</span><span class="badge '+(age<5?'badge-on':'badge-off')+'">'+(age<5?'LIVE':age+'m ago')+'</span><div class="meta">'+pts.length+' trail pts | '+age+'m ago</div></div>';
    }
    document.getElementById('people-list').innerHTML=pplHtml||'<div class="empty">No people tracked</div>';

    const sightings=sightR.sightings||[];
    document.getElementById('ppl-count').textContent=mapR.people_count||0;
    document.getElementById('sight-count').textContent=sightings.length;
    let sh='';sightings.reverse().slice(0,20).forEach(s=>{
      const t=new Date(s.timestamp*1000).toLocaleTimeString();
      const nodeTag=s.node_id?'<span style="color:'+(NODE_COLORS[nodes.findIndex(n=>n.node_id===s.node_id)%NODE_COLORS.length]||'rgba(93,78,109,0.4)')+';font-size:.65rem"> ● '+s.node_id+'</span>':'';
      sh+='<div class="sighting-item"><strong>'+s.name+'</strong> <span style="color:rgba(93,78,109,0.4)">'+t+'</span>'+nodeTag+'</div>';
    });
    document.getElementById('sightings-list').innerHTML=sh||'<div class="empty">No sightings</div>';
    document.getElementById('net-status').textContent=onlineCount+' of '+nodes.length+' nodes online';
    document.getElementById('net-status').style.color=onlineCount>0?'#69f0ae':'#e57373';
    if(!window._fitted){window._fitted=true;if(gpsNodes.length>=2)fitAllNodes();else if(gpsNodes.length===1)map.setView([gpsNodes[0].lat,gpsNodes[0].lng],16)}
  }catch(e){document.getElementById('net-status').textContent='Offline';document.getElementById('net-status').style.color='#e57373'}
}

function focusNode(nid,idx){setView('n'+(parseInt(idx)+1))}
function focusPerson(name){const m=personMarkers[name];if(m){map.setView(m.getLatLng(),18);m.openPopup()}}
async function forceScan(){document.getElementById('net-status').textContent='Scanning all nodes...';document.getElementById('net-status').style.color='#c084fc';await fetch('/api/tracker/scan')}
function showEnroll(){document.getElementById('enroll-modal').style.display='flex'}
function hideEnroll(){document.getElementById('enroll-modal').style.display='none'}
async function doEnroll(){
  const name=document.getElementById('enroll-name').value.trim();
  if(!name){document.getElementById('enroll-status').textContent='Enter a name';return}
  document.getElementById('enroll-status').textContent='Enrolling from camera...';
  try{const stream=await navigator.mediaDevices.getUserMedia({video:{width:320,height:240}});const video=document.createElement('video');video.srcObject=stream;video.play();await new Promise(r=>setTimeout(r,1500));const canvas=document.createElement('canvas');canvas.width=320;canvas.height=240;canvas.getContext('2d').drawImage(video,0,0,320,240);stream.getTracks().forEach(t=>t.stop());const b64=canvas.toDataURL('image/jpeg',0.8).split(',')[1];const r=await fetch('/api/faces/enroll',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name,image_b64:b64,source:document.getElementById('enroll-source').value||'manual'})});const res=await r.json();document.getElementById('enroll-status').textContent=res.ok?'Enrolled: '+name:(res.error||'Failed');if(res.ok)setTimeout(hideEnroll,1500)}catch(e){document.getElementById('enroll-status').textContent='Camera error: '+e.message}
}
setInterval(poll,3000);poll();
</script>
</body>
</html>"""


# ── Browser webcam UI (live detection + OSINT dossiers) ──
@app.get("/webcam")
@app.get("/webcam/")
async def webcam_dashboard():
    from fastapi.responses import HTMLResponse

    return HTMLResponse(WEBCAM_HTML)


WEBCAM_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
<title>Lilly Vision — Live Webcam &amp; Dossiers</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
html,body{width:100%;height:100%;overflow:hidden;font-family:-apple-system,'Segoe UI',system-ui,sans-serif;background:#15101c;color:#e8dff0}
#wrap{position:fixed;inset:0}
#video{position:absolute;inset:0;width:100%;height:100%;object-fit:contain;background:#000}
#canvas{position:absolute;inset:0;width:100%;height:100%}
#hud{position:absolute;top:0;left:0;right:0;padding:14px;display:flex;gap:10px;align-items:flex-start;pointer-events:none;flex-wrap:wrap;z-index:5}
.pill{background:rgba(22,15,30,0.72);backdrop-filter:blur(14px);-webkit-backdrop-filter:blur(14px);border:1px solid rgba(192,132,252,0.25);border-radius:14px;padding:8px 14px;font-size:.78rem;color:#d8cbe8;box-shadow:0 4px 24px rgba(0,0,0,0.35);pointer-events:auto;white-space:nowrap}
.pill b{color:#fff}
.pill .tag{font-weight:700;letter-spacing:.5px}
.dot{display:inline-block;width:8px;height:8px;border-radius:50%;background:#4ade80;box-shadow:0 0 10px #4ade80;margin-right:6px;vertical-align:middle}
.dot.off{background:#e57373;box-shadow:0 0 10px #e57373}

/* alert banner layer */
#alerts{position:absolute;top:64px;left:50%;transform:translateX(-50%);z-index:6;display:flex;flex-direction:column;gap:8px;align-items:center;pointer-events:none;max-width:min(94vw,700px)}
.alertbar{display:flex;align-items:center;gap:12px;padding:10px 20px;border-radius:14px;font-weight:800;letter-spacing:.5px;font-size:.9rem;box-shadow:0 6px 30px rgba(0,0,0,0.5);border:2px solid;backdrop-filter:blur(10px);-webkit-backdrop-filter:blur(10px);animation:alertflash 1.1s ease-in-out infinite;text-transform:uppercase}
.alertbar.crit{border-color:#f87171;background:rgba(127,29,29,0.85);color:#fecaca;animation:alertflash 0.6s ease-in-out infinite}
.alertbar.warn{border-color:#fbbf24;background:rgba(120,72,0,0.85);color:#fde68a}
.alertbar.info{border-color:#38bdf8;background:rgba(3,60,90,0.85);color:#bae6fd}
.alertbar .sym{font-size:1.4rem;line-height:1}
.alertbar .sub{font-weight:600;font-size:.68rem;opacity:.85;letter-spacing:.3px}
@keyframes alertflash{0%,100%{opacity:1}50%{opacity:.45}}

#rail{position:absolute;right:14px;top:58px;bottom:14px;width:335px;display:flex;flex-direction:column;gap:10px;pointer-events:none;z-index:4}
#rail>*{pointer-events:auto}
.tabbar{display:flex;gap:4px;background:rgba(22,15,30,0.78);backdrop-filter:blur(18px);-webkit-backdrop-filter:blur(18px);border:1px solid rgba(192,132,252,0.25);border-radius:14px;padding:4px}
.tabbar button{flex:1;padding:8px 4px;border:none;border-radius:10px;background:transparent;color:rgba(216,203,232,0.6);font-size:.72rem;font-weight:700;cursor:pointer}
.tabbar button.active{background:linear-gradient(135deg,#8b5cf6,#c084fc);color:#fff}
.card{background:rgba(22,15,30,0.82);backdrop-filter:blur(18px);-webkit-backdrop-filter:blur(18px);border:1px solid rgba(192,132,252,0.25);border-radius:16px;padding:14px;box-shadow:0 8px 40px rgba(0,0,0,0.45);overflow-y:auto}
.card h3{margin:0 0 10px;font-size:.78rem;color:#c084fc;letter-spacing:.6px;text-transform:uppercase}
.hide{display:none !important}
.row{display:flex;align-items:center;gap:8px;margin-bottom:9px}
.row label{flex:0 0 80px;font-size:.72rem;color:rgba(216,203,232,0.75)}
.row select,.row input[type=text]{flex:1;background:rgba(0,0,0,0.3);border:1px solid rgba(192,132,252,0.25);color:#e8dff0;border-radius:10px;padding:7px 10px;font-size:.8rem;outline:none;min-width:0}
.row input[type=range]{flex:1;accent-color:#c084fc}
.btn{width:100%;padding:10px;border:none;border-radius:12px;font-weight:700;font-size:.82rem;cursor:pointer;transition:all .15s;margin-bottom:7px}
.btn:active{transform:scale(0.97)}
.btn-primary{background:linear-gradient(135deg,#8b5cf6,#c084fc);color:#fff;box-shadow:0 4px 16px rgba(139,92,246,0.4)}
.btn-enroll{background:rgba(0,0,0,0.35);color:#c084fc;border:1px solid rgba(192,132,252,0.35)}
.btn-enroll:hover{background:rgba(192,132,252,0.15)}
.btn-stop{background:rgba(224,103,103,0.18);color:#e57373;border:1px solid rgba(229,115,115,0.35)}
.toggle{display:flex;align-items:center;gap:8px;margin-bottom:9px;font-size:.76rem;color:rgba(216,203,232,0.8)}
.switch{position:relative;width:38px;height:20px;background:rgba(0,0,0,0.4);border-radius:20px;cursor:pointer;transition:background .2s;border:1px solid rgba(192,132,252,0.3);flex-shrink:0}
.switch::after{content:'';position:absolute;top:2px;left:2px;width:14px;height:14px;border-radius:50%;background:#b8a9c9;transition:all .2s}
.switch.on{background:linear-gradient(135deg,#8b5cf6,#c084fc)}
.switch.on::after{left:20px;background:#fff}
#enroll-status{font-size:.68rem;color:rgba(216,203,232,0.6);margin-top:4px;min-height:13px}

/* POI cards */
.poi-card{position:relative;display:flex;align-items:center;gap:10px;padding:10px 12px;border-radius:14px;margin-bottom:8px;background:linear-gradient(135deg,rgba(192,132,252,0.08),rgba(0,0,0,0.25));border:1px solid rgba(192,132,252,0.22);cursor:pointer;transition:all .18s;overflow:hidden}
.poi-card:hover{background:rgba(192,132,252,0.14);box-shadow:0 4px 20px rgba(139,92,246,0.18)}
.poi-avatar{width:44px;height:44px;border-radius:12px;display:flex;align-items:center;justify-content:center;font-size:1.25rem;font-weight:900;flex-shrink:0;box-shadow:inset 0 0 0 2px rgba(255,255,255,0.08)}
.poi-body{flex:1;min-width:0}
.poi-name{font-weight:800;font-size:.88rem;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.poi-meta{font-size:.66rem;color:rgba(216,203,232,0.55);margin-top:2px;display:flex;gap:6px;align-items:center;flex-wrap:wrap}
.poi-conf{height:4px;border-radius:3px;background:rgba(0,0,0,0.35);margin-top:6px;overflow:hidden}
.poi-conf i{display:block;height:100%;border-radius:3px}
.poi-tags{display:flex;gap:4px;margin-top:5px;flex-wrap:wrap}
.tagchip{font-size:.58rem;font-weight:700;padding:2px 7px;border-radius:8px;letter-spacing:.3px;text-transform:uppercase}
.tc-known{background:rgba(105,240,174,0.15);color:#69f0ae;border:1px solid rgba(105,240,174,0.35)}
.tc-unknown{background:rgba(251,191,36,0.15);color:#fbbf24;border:1px solid rgba(251,191,36,0.35)}
.tc-osint{background:rgba(192,132,252,0.2);color:#c084fc;border:1px solid rgba(192,132,252,0.4)}
.tc-face{background:rgba(56,189,248,0.15);color:#38bdf8;border:1px solid rgba(56,189,248,0.35)}
.tc-weapon{background:rgba(248,113,113,0.18);color:#f87171;border:1px solid rgba(248,113,113,0.4);animation:alertflash 1s infinite}
.badge-dossier{position:absolute;top:8px;right:8px;font-size:.6rem;color:rgba(216,203,232,0.4)}
#dossier-body{font-size:.78rem;line-height:1.5}
#dossier-body .acct{display:inline-block;background:rgba(192,132,252,0.14);color:#d8cbe8;border:1px solid rgba(192,132,252,0.3);border-radius:8px;padding:2px 8px;margin:2px 4px 2px 0;font-size:.7rem}
#dossier-body pre.report{white-space:pre-wrap;word-break:break-word;background:rgba(0,0,0,0.35);border:1px solid rgba(192,132,252,0.15);border-radius:10px;padding:10px;font-size:.7rem;color:#c9b8e0;max-height:240px;overflow-y:auto}
.grid{display:flex;flex-wrap:wrap;gap:6px;margin-top:8px}
.grid img{width:88px;height:66px;object-fit:cover;border-radius:8px;border:1px solid rgba(192,132,252,0.25);cursor:pointer}
.empty{text-align:center;color:rgba(216,203,232,0.4);padding:14px;font-size:.75rem}
#log{position:absolute;left:14px;bottom:14px;width:min(560px,calc(100vw - 390px));max-height:150px;overflow:hidden;background:rgba(22,15,30,0.7);backdrop-filter:blur(14px);-webkit-backdrop-filter:blur(14px);border:1px solid rgba(192,132,252,0.2);border-radius:16px;padding:12px 16px;pointer-events:none;z-index:4}
#log .agent{font-weight:700;font-size:.72rem;color:#c084fc;letter-spacing:.4px;text-transform:uppercase}
#log .msg{font-size:.85rem;line-height:1.4;color:#e8dff0;margin-top:3px}
#log .meta{font-size:.68rem;color:rgba(216,203,232,0.45);margin-top:5px}
/* drop zone */
#dropzone{display:none;position:fixed;inset:0;z-index:100;background:rgba(22,15,30,0.85);backdrop-filter:blur(12px);-webkit-backdrop-filter:blur(12px);justify-content:center;align-items:center;flex-direction:column;gap:14px}
#dropzone.show{display:flex}
#dropzone .ring{width:220px;height:220px;border:3px dashed rgba(192,132,252,0.5);border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:3rem;animation:dropspin 2s linear infinite}
@keyframes dropspin{to{transform:rotate(360deg)}}
#dropzone .label{font-size:1rem;color:#d8cbe8;font-weight:700}
#dropzone .sub{font-size:.72rem;color:rgba(216,203,232,0.5)}
.btn-capture{background:rgba(56,189,248,0.18);color:#38bdf8;border:1px solid rgba(56,189,248,0.4);width:100%;padding:10px;border-radius:12px;font-weight:700;font-size:.82rem;cursor:pointer;margin-bottom:7px;transition:all .15s}
.btn-capture:hover{background:rgba(56,189,248,0.28)}
@media(max-width:820px){#rail{left:8px;right:8px;top:auto;bottom:8px;width:auto;max-height:46vh}#log{display:none}.card{max-height:calc(46vh - 50px)}#hud{flex-wrap:wrap}}
</style>
</head>
<body>
<div id="dropzone">
  <div class="ring">📷</div>
  <div class="label">Drop an image here to identify faces</div>
  <div class="sub">Supports .jpg, .png, .webp — or paste with Ctrl+V</div>
</div>
<input type="file" id="filepick" accept="image/*" style="display:none">

<div id="wrap">
  <video id="video" autoplay playsinline muted></video>
  <canvas id="canvas"></canvas>

  <div id="alerts"></div>

  <div id="hud">
    <div class="pill"><span class="tag"><span class="dot off" id="live-dot"></span>VISION</span>&nbsp;<span id="live-label">offline</span></div>
    <div class="pill"><b id="targets">0</b> targets</div>
    <div class="pill"><b id="people">0</b> people</div>
    <div class="pill"><b id="faces">0</b> faces</div>
    <div class="pill"><b id="lat">—</b> ms</div>
    <div class="pill"><b id="face-count">0</b> known</div>
  </div>

  <div id="rail">
    <div class="tabbar">
      <button id="tab-ctrl" class="active" onclick="showTab('ctrl')">CONTROL</button>
      <button id="tab-ppl"  onclick="showTab('ppl')">POI</button>
      <button id="tab-dos"  onclick="showTab('dos')">DOSSIER</button>
    </div>

    <div class="card" id="card-ctrl">
      <h3>🎛 CONTROL</h3>
      <button class="btn btn-primary" id="btn-cam" onclick="toggleCamera()">▶ Start Camera</button>
      <button class="btn-capture" onclick="captureIdentify()">⚡ Capture &amp; Identify</button>
      <div class="row"><label>Analyze</label><input type="text" id="analyze-path" placeholder="or drop/paste an image anywhere" readonly style="cursor:pointer" onclick="document.getElementById('filepick').click()"></div>
      <div class="row"><label>Avatar</label>
        <select id="avatar">
          <option value="puppy">🐕 Puppy</option>
          <option value="fox">🦊 Fox</option>
          <option value="cat">🐱 Cat</option>
          <option value="bear">🐻 Bear</option>
          <option value="bunny">🐰 Bunny</option>
          <option value="owl">🦉 Owl</option>
          <option value="data">📊 Data</option>
        </select>
      </div>
      <div class="row"><label>Frame rate</label><input type="range" id="fpsctl" min="1" max="8" value="3" step="1"></div>
      <div class="row"><label>Min conf</label><input type="range" id="confctl" min="10" max="80" value="35" step="5"></div>
      <div class="row"><label>Face sens</label><input type="range" id="facesensctl" min="20" max="80" value="42" step="2"></div>
      <div class="toggle"><span>Narrate scene (LLM + voice)</span><div class="switch" id="narrate-sw"></div></div>
      <div class="toggle" style="margin-bottom:6px"><span>Traffic &amp; safety alerts</span><div class="switch on" id="alert-sw"></div></div>
      <hr style="border:none;border-top:1px solid rgba(192,132,252,0.15);margin:10px 0">
      <h3>👤 ENROLL MY FACE</h3>
      <div class="row"><label>Name</label><input type="text" id="enroll-name" placeholder="Enter your name..."></div>
      <button class="btn btn-enroll" onclick="enrollFace()">📸 Enroll from live frame</button>
      <p id="enroll-status"></p>
    </div>

    <div class="card hide" id="card-ppl">
      <h3>👥 PERSONS OF INTEREST</h3>
      <div id="people-list"><div class="empty">No people detected — point your camera at someone</div></div>
      <h3 style="margin-top:12px">📂 RECENT DOSSIERS</h3>
      <div id="dossier-list"></div>
    </div>

    <div class="card hide" id="card-dos">
      <h3>📁 DOSSIER</h3>
      <div id="dossier-body"><div class="empty">Select a person to view their dossier</div></div>
    </div>
  </div>

  <div id="log">
    <div class="agent" id="log-agent">Lilly Vision</div>
    <div class="msg" id="log-msg">Waiting for camera…</div>
    <div class="meta" id="log-meta"></div>
  </div>
</div>

<script>
const LILLY = location.protocol+'//'+location.hostname+':8098';
const video=document.getElementById('video');
const canvas=document.getElementById('canvas');
const ctx=canvas.getContext('2d');
let stream=null, running=false, captureTimer=null;
let lastFrame=null, lastDetections=[];
let dossierCache={};
const peopleCache=new Map();

const PERSON_LABELS=['person','people','human','human face','face','man','woman','man face','women','girl','boy','child','kid'];
const VEHICLE_LABELS=['car','vehicle','truck','pickup truck','pickup','suv','van','taxi','police car','bus'];
const COL_PERSON='#c084fc'; const COL_UNKNOWN='#fbbf24'; const COL_VEHICLE='#69f0ae'; const COL_OTHER='#94a3b8'; const COL_FACE='#38bdf8';

const TRAFFIC_ALERTS={ 'stop sign':['crit','🛑','STOP sign detected'], 'traffic light':['warn','🚦','Traffic light'], 'traffic sign':['warn','🪧','Traffic sign'] };
const SAFETY_ALERTS={ 'weapon':['crit','⚠️','WEAPON'], 'handgun':['crit','🔫','HANDGUN'], 'shotgun':['crit','🔫','SHOTGUN'], 'knife':['crit','🔪','KNIFE'], 'kitchen knife':['warn','🔪','Knife'], 'police car':['warn','🚓','Police vehicle'], 'fire hydrant':['info','🧯','Fire hydrant'] };
const FACE_MATCH=c=>c>=0.42;

document.getElementById('narrate-sw').addEventListener('click',e=>e.currentTarget.classList.toggle('on'));
document.getElementById('alert-sw').addEventListener('click',e=>e.currentTarget.classList.toggle('on'));

function showTab(t){
  ['ctrl','ppl','dos'].forEach(x=>{
    document.getElementById('card-'+x).classList.toggle('hide',x!==t);
    document.getElementById('tab-'+x).classList.toggle('active',x===t);
  });
  if(t==='ppl')renderPeople();
  if(t==='dos'&&selectedName)showDossier(selectedName);
}

let selectedName=null;
async function toggleCamera(){
  if(running){stopCamera();return}
  try{
    stream=await navigator.mediaDevices.getUserMedia({video:{width:{ideal:1280},height:{ideal:720}},audio:false});
    video.srcObject=stream; await video.play();
    setLive(true);
    const b=document.getElementById('btn-cam');
    b.textContent='■ Stop Camera';b.classList.remove('btn-primary');b.classList.add('btn-stop');
    resizeCanvas();
    refreshKnown();
    refreshDossiers();
    tick();
  }catch(e){setLog('Camera blocked', e.message||String(e), 'error')}
}
function stopCamera(){
  running=false;
  if(captureTimer){clearTimeout(captureTimer);captureTimer=null}
  if(stream){stream.getTracks().forEach(t=>t.stop());stream=null}
  video.srcObject=null;
  setLive(false);
  const b=document.getElementById('btn-cam');
  b.textContent='▶ Start Camera';b.classList.add('btn-primary');b.classList.remove('btn-stop');
  ctx.clearRect(0,0,canvas.width,canvas.height);
  document.getElementById('alerts').innerHTML='';
}
function setLive(on){
  const dot=document.getElementById('live-dot');dot.classList.toggle('off',!on);
  document.getElementById('live-label').textContent=on?'LIVE':'offline';
}
function resizeCanvas(){
  if(video.videoWidth){canvas.width=video.videoWidth;canvas.height=video.videoHeight}
  else{canvas.width=1280;canvas.height=720}
}

let lastNarration=0;
async function tick(){
  if(!running||video.readyState<2)return;
  const started=performance.now();
  try{
    resizeCanvas();
    const t=document.createElement('canvas');
    t.width=video.videoWidth;t.height=video.videoHeight;
    t.getContext('2d').drawImage(video,0,0);
    const b64=t.toDataURL('image/jpeg',0.72).split(',')[1];
    lastFrame=b64;
    const conf=parseInt(document.getElementById('confctl').value)/100;
    const narrate=document.getElementById('narrate-sw').classList.contains('on');
    const avatar=document.getElementById('avatar').value;
    const res=await fetch('/api/vision',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({image_b64:b64,avatar,generate_audio:narrate})});
    const data=await res.json();
    lastDetections=data.detections||[];
    document.getElementById('lat').textContent=Math.round(performance.now()-started);
    document.getElementById('targets').textContent=lastDetections.length;
    const ppl=lastDetections.filter(d=>isPerson(d));
    const fcs=lastDetections.filter(d=>d.kps&&d.kps.length>=5).length;
    document.getElementById('people').textContent=ppl.length;
    document.getElementById('faces').textContent=fcs;
    draw(lastDetections);
    renderAlerts(lastDetections);

    const named=_identityNames(lastDetections);
    for(const n of named){if(!dossierCache[n])loadDossierByName(n);}

    if(narrate&&data.reply&&(Date.now()-lastNarration>2500)){
      lastNarration=Date.now();
      setLog(avatar,tidy(data.reply),ppl.length+' person(s) · '+Math.round(performance.now()-started)+'ms');
    }else{
      const names=named;
      const rep=names.length?('Match: '+names.join(', ')):(ppl.length?('Person present — '+((fcs?'face tracked':''))||'UNKNOWN TARGET / OSINT queued'):'Clear frame');
      setLog(avatar,rep,lastDetections.filter(d=>isPerson(d)).map(d=>d.name||(d.kps?'☐':'UNKNOWN')).join(' · '));
    }
  }catch(e){
    document.getElementById('lat').textContent='ERR';
  }finally{
    if(running)captureTimer=setTimeout(tick,Math.max(80,1000/Math.max(1,parseInt(document.getElementById('fpsctl').value))));
  }
}

function isPerson(d){
  const label=(d.label||'').toLowerCase();
  return !!(d.name||d.face_source||d.kps||PERSON_LABELS.includes(label)||label==='unknown');
}

function _identityNames(dets){
  const out=[];
  for(const d of dets){
    if(!isPerson(d))continue;
    const nm=(d.name||(d.original_label&&d.label&&!(PERSON_LABELS.includes(d.label.toLowerCase()))?d.label:null)||null);
    if(nm&&!out.includes(nm))out.push(nm);
  }
  return out;
}

/* ── Face recognition markers (5-point mesh) ─────────────────────────── */
function drawFaceMesh(d, x, y, w, h, W, H, color){
  if(!d.kps||d.kps.length<5)return;
  const P=d.kps.map(kp=>[kp[0]*W, kp[1]*H]);
  const [le,re,nos,lm,rm]=P;
  const base=Math.max(3,W/170);
  const glow=color;

  ctx.lineJoin='round';ctx.lineCap='round';
  ctx.strokeStyle=glow;ctx.globalAlpha=0.85;ctx.lineWidth=Math.max(1.5,base*0.45);
  ctx.shadowColor=glow;ctx.shadowBlur=8;
  ctx.beginPath();
  ctx.moveTo(le[0],le[1]);ctx.lineTo(re[0],re[1]);
  ctx.moveTo(le[0],le[1]);ctx.lineTo(nos[0],nos[1]);
  ctx.moveTo(re[0],re[1]);ctx.lineTo(nos[0],nos[1]);
  ctx.moveTo(nos[0],nos[1]);ctx.lineTo(lm[0],lm[1]);
  ctx.moveTo(nos[0],nos[1]);ctx.lineTo(rm[0],rm[1]);
  ctx.moveTo(lm[0],lm[1]);ctx.lineTo(rm[0],rm[1]);
  ctx.stroke();
  ctx.globalAlpha=1;ctx.shadowBlur=0;

  const labels=['◉','◉','▲','‹','›'];
  P.forEach((p,i)=>{
    ctx.beginPath();
    ctx.fillStyle=i===2?'#fff':glow;
    ctx.shadowColor='rgba(0,0,0,0.9)';ctx.shadowBlur=5;
    ctx.arc(p[0],p[1],i===2?base*0.85:base*0.55,0,Math.PI*2);ctx.fill();
    ctx.shadowBlur=0;
  });

  // face ROI ring from keypoint bbox
  const xMin=Math.min(...P.map(p=>p[0])),xMax=Math.max(...P.map(p=>p[0]));
  const yMin=Math.min(...P.map(p=>p[1])),yMax=Math.max(...P.map(p=>p[1]));
  const pad=Math.max(8,(xMax-xMin)*0.12);
  ctx.strokeStyle=glow;ctx.globalAlpha=0.95;ctx.lineWidth=Math.max(2,base*0.6);
  ctx.shadowColor=glow;ctx.shadowBlur=10;
  ctx.strokeRect(xMin-pad,yMin-pad,(xMax-xMin)+pad*2,(yMax-yMin)+pad*2);
  ctx.globalAlpha=1;ctx.shadowBlur=0;
}

/* ── Traffic / safety alerts ─────────────────────────────────────────── */
function renderAlerts(dets){
  const alertsEl=document.getElementById('alerts');
  if(!document.getElementById('alert-sw').classList.contains('on')){alertsEl.innerHTML='';return}
  let html='';
  for(const d of dets){
    const label=(d.label||'').toLowerCase();
    if(TRAFFIC_ALERTS[label]){
      const [lv,ic,txt]=TRAFFIC_ALERTS[label];
      html+='<div class="alertbar '+lv+'"><span class="sym">'+ic+'</span><div>'+txt+'<div class="sub">'+((d.conf*100)|0)+'% · '+posDesc(d)+'</div></div></div>';
    }else if(SAFETY_ALERTS[label]){
      const [lv,ic,txt]=SAFETY_ALERTS[label];
      html+='<div class="alertbar '+lv+'"><span class="sym">'+ic+'</span><div>'+txt+'<div class="sub">'+((d.conf*100)|0)+'% · '+posDesc(d)+'</div></div></div>';
    }
  }
  alertsEl.innerHTML=html;
}
function posDesc(d){
  const cx=(d.x+d.w/2);
  return cx<0.34?'left of view':(cx>0.66?'right of view':'center of view');
}

function draw(dets){
  ctx.clearRect(0,0,canvas.width,canvas.height);
  const W=canvas.width,H=canvas.height;
  peopleCache.clear();
  for(const d of dets){
    const x=d.x*W,y=d.y*H,w=d.w*W,h=d.h*H;
    const label=(d.label||'').toLowerCase();
    const person=isPerson(d);
    const known=!!d.name;
    const isVehicle=VEHICLE_LABELS.includes(label);
    const color=isVehicle?COL_VEHICLE:(person?(known?COL_PERSON:(FACE_MATCH(d.face_confidence||d.conf)?COL_UNKNOWN:COL_PERSON)):COL_OTHER);

    if(person&&d.kps){
      drawFaceMesh(d,x,y,w,h,W,H, known?COL_MATCH:COL_UNKNOWN);
    }
    ctx.strokeStyle=color;ctx.lineWidth=Math.max(2,W/480);ctx.globalAlpha=0.9;
    ctx.strokeRect(x,y,w,h);
    ctx.globalAlpha=1;
    const text=known?(d.name+(d.face_confidence?(' '+((d.face_confidence*100)|0)+'%'):''))
                 :(person?(FACE_MATCH(d.face_confidence||d.conf)?'UNKNOWN TARGET':'person'):label);
    ctx.font='600 '+(Math.max(12,W/70))+'px -apple-system,Segoe UI,sans-serif';
    ctx.fillStyle=color;ctx.shadowColor='rgba(0,0,0,0.8)';ctx.shadowBlur=6;
    ctx.fillText(text,x+4,Math.max(14,y-6));
    // confidence bar under label
    const cw=Math.max(30,w*0.6),chh=Math.max(2.5,W/300);
    const confVal=Math.min(1,d.face_confidence||d.conf||0);
    ctx.fillStyle='rgba(0,0,0,0.5)';
    ctx.fillRect(x+4,Math.max(18,y-6)+4,cw,chh);
    ctx.fillStyle=color;
    ctx.fillRect(x+4,Math.max(18,y-6)+4,cw*confVal,chh);
    ctx.shadowBlur=0;

    if(person){
      const acc=d.social_accounts||[];
      const key=(Math.round(x/40)+','+Math.round(y/40));
      peopleCache.set(key,{
        name:d.name||null,
        conf:d.face_confidence||d.conf||0,
        kps:!!d.kps,
        faceSource:d.face_source||null,
        dfv:!!d.deepface_verified,
        dfd:d.deepface_verified_dist||null,
        accts:acc,
        color,
        label:label,
        x:d.x
      });
    }
  }
}

function renderPeople(){
  const list=document.getElementById('people-list');
  if(!peopleCache.size){list.innerHTML='<div class="empty">No people detected — point your camera at someone</div>';return}
  let h='';let i=0;
  for(const p of peopleCache.values()){
    i++;
    const known=!!p.name;
    const bg=known?'linear-gradient(135deg,#69f0ae,#3b82f6)':(p.kps?'linear-gradient(135deg,#fbbf24,#c084fc)':'linear-gradient(135deg,#94a3b8,#64748b)');
    const initial=known?p.name.charAt(0).toUpperCase():'?';
    const tags='';
    const tagHtml=(known?'<span class="tagchip tc-known">KNOWN</span>':'')+(p.kps?'<span class="tagchip tc-face">FACE</span>':'')+(!known&&p.kps?'<span class="tagchip tc-unknown">UNKNOWN</span>':'')+(p.faceSource==='osint'?'<span class="tagchip tc-osint">OSINT</span>':'')+(p.dfv?'<span class="tagchip" style="background:#69f0ae;color:#0b1a12">✓ 99% VERIFIED</span>':'')+(p.accts.length?'<span class="tagchip tc-osint">'+p.accts.length+' OSN</span>':'');
    const enrollBtn=!known&&p.kps?'<button class="tagchip tc-known" style="cursor:pointer;border:none;background:#69f0ae;color:#0b1a12" onclick="event.stopPropagation();enrollPrompt()">⬆ NAME THIS FACE</button>':'';
    h+='<div class="poi-card" onclick="openDossier(\''+(p.name||('UFACE-'+i)).replace(/'/g,"\'")+'\')" style="opacity:'+(p.kps?1:0.55)+'">'
      +'<div class="poi-avatar" style="background:'+bg+'">'+initial+'</div>'
      +'<div class="poi-body">'
      +'<div class="poi-name" style="color:'+p.color+'">'+(p.name||(p.kps?'◉ UNKNOWN PERSON':'· person'))+'</div>'
      +'<div class="poi-meta">'+(p.kps?'◉ 5-pt face':'no face')+(p.faceSource==='osint'?' · OSINT':'')+(p.dfv?' · SFace ✓':'')+' · '+(Math.round(p.conf*100))+'%</div>'
      +'<div class="poi-conf"><i style="width:'+Math.round(Math.min(100,p.conf*100))+'%;background:'+p.color+'"></i></div>'
      +'<div class="poi-tags">'+tagHtml+enrollBtn+'</div>'
      +'</div>'
      +'<span class="badge-dossier">'+(p.accts.length?'OSN ⚡':'')+'</span>'
      +'</div>';
  }
  list.innerHTML=h;
}

const COL_MATCH='#4ade80';
async function openDossier(nm){
  selectedName=nm;
  showTab('dos');
  // carry live deepface verdict into the dossier view
  for(const p of peopleCache.values()){
    if(p.name && String(p.name).toLowerCase()===String(nm).toLowerCase()){
      if(!dossierCache[nm])dossierCache[nm]={};
      dossierCache[nm].dfv=!!p.dfv;
      dossierCache[nm].conf=Math.max(dossierCache[nm].conf||0,p.conf||0);
      break;
    }
  }
  await loadDossierByName(nm);
  sendDossierToChat(nm);
}
function showDossier(nm){
  const body=document.getElementById('dossier-body');
  const d=dossierCache[nm];
  if(!d){body.innerHTML='<div class="empty">Loading dossier for '+esc(nm)+'…</div>';return}
  if(!d.dfv){for(const p of peopleCache.values()){if(p.name&&String(p.name).toLowerCase()===String(nm).toLowerCase()){d.dfv=!!p.dfv;d.conf=Math.max(d.conf||0,p.conf||0);break}}}
  let h='<h3 style="font-size:1rem;text-transform:none;color:#fff">'+esc(nm)+'</h3>';
  if(d.conf){h+='<div style="color:rgba(216,203,232,0.55);font-size:.68rem;margin-bottom:8px">'+(d.dfv?'<span style="color:#69f0ae">✓ DeepFace SFace verified — '+Math.round(d.conf*100)+'%</span>':'FAISS confidence '+((d.conf*100)|0)+'%')+'</div>'}
  const accts=(d.social_accounts||[]);
  if(accts.length){h+='<div style="margin:6px 0">'+accts.map(a=>'<span class="acct">'+esc(a)+'</span>').join('')+'</div>'}
  else{h+='<div class="empty" style="padding:6px">No public accounts linked</div>'}
  const sources=d.sources||d.osint_sources||[];
  if(sources.length){
    h+='<div style="margin:8px 0 4px;color:#c084fc;font-size:.68rem;text-transform:uppercase;letter-spacing:.5px">Investigation</div>';
    const report=sources.find(s=>typeof s==='string'&&/Investigation Report|Target:|Findings/i.test(s));
    if(report){h+='<pre class="report">'+esc(report)+'</pre>'}
    const urls=sources.filter(s=>typeof s==='string'&&s.startsWith('http'));
    if(urls.length){h+='<div style="margin-top:6px">'+urls.map(u=>'<div style="margin:2px 0"><a href="'+esc(u)+'" target="_blank" rel="noopener" style="color:#c084fc;font-size:.7rem;word-break:break-all">🔗 source</a></div>').join('')+'</div>'}
  }
  const imgs=d.images||d.face_images||[];
  if(imgs.length){
    h+='<div style="margin:8px 0 4px;color:#c084fc;font-size:.68rem;text-transform:uppercase;letter-spacing:.5px">Reverse-image matches</div>';
    h+='<div class="grid">'+imgs.map(im=>'<a href="'+esc(im.page_url||im.url||'#')+'" target="_blank" rel="noopener" title="'+esc(im.title||im.site||'')+'"><img src="'+esc(im.thumb||im.url)+'" loading="lazy" onerror="this.style.visibility=\'hidden\'"></a>').join('')+'</div>';
  }
  if(!sources.length&&!imgs.length&&!d.jobStatus){h+='<div class="empty">No dossier material yet — it builds as OSINT completes.</div>'}
  if(d.job){h+='<div style="margin-top:8px;font-size:.68rem;color:rgba(216,203,232,0.5)">Footprint job: <b>'+esc(d.job.status)+'</b> ('+d.job.elapsed_s+'s)</div>'}
  if(d.chatStatus){h+='<div class="chat-push-status" style="margin-top:8px;padding:8px 10px;border-radius:10px;border:1px solid rgba(105,240,174,0.25);background:rgba(105,240,174,0.08);color:#69f0ae;font-size:.72rem">'+esc(d.chatStatus==='sent'?'✓ sent to chat — Lilly is reviewing':(d.chatStatus==='failed'?'✗ failed to send to chat':d.chatStatus))+'</div>'}
  body.innerHTML=h;
}

const dossierSentToChat=new Set();
async function sendDossierToChat(nm){
  const key=String(nm).toLowerCase();
  if(dossierSentToChat.has(key))return;
  const d=dossierCache[nm];
  if(!d)return;
  d.chatStatus='sending…';
  showDossier(nm);
  try{
    const r=await fetch(LILLY+'/api/faces/dossier/chat',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({
      name:nm,
      report:(d.sources||[]).filter(s=>typeof s==='string'&&!s.startsWith('http')).join('
'),
      sources:(d.sources||[]).filter(s=>typeof s==='string'),
      social_accounts:d.social_accounts||[],
      images:(d.images||[]).map(i=>i.thumb||i.url||'').filter(Boolean),
      jobStatus:null
    })});
    const res=await r.json();
    if(res.ok){
      d.chatStatus='sent';
      dossierSentToChat.add(key);
      showDossier(nm);
      setLog(nm,res.reply||'Dossier pushed to chat — analyze with Lilly.','dossier → chat');
    }else{
      d.chatStatus='failed';
      showDossier(nm);
    }
  }catch(e){
    d.chatStatus='failed';
    showDossier(nm);
  }
}

async function loadDossierByName(nm){
  if(dossierCache[nm]){showDossier(nm);return}
  dossierCache[nm]={}
  refreshDossiers();
  try{
    const osr=await fetch(LILLY+'/api/faces/osint_results').then(r=>r.json()).catch(()=>({results:[]}));
    const res=(osr.results||[]).find(r=>String(r.name||'').toLowerCase()===String(nm).toLowerCase());
    if(res){dossierCache[nm]={name:nm,conf:res.confidence||null,social_accounts:res.social_accounts||[],sources:res.sources||[],images:res.images||[]};if(selectedName===nm)showDossier(nm);}
    else{
      const fp=await fetch(LILLY+'/api/faces/footprints?limit=25').then(r=>r.json()).catch(()=>({jobs:[]}));
      const job=(fp.jobs||[]).find(j=>String(j.name||'').toLowerCase()===String(nm).toLowerCase()&&j.status==='done');
      if(job){const jr=await fetch(LILLY+'/api/faces/footprint/'+encodeURIComponent(job.id)).then(r=>r.json()).catch(()=>null);
        const j=jr&&jr.job?jr.job:null;
        if(j){dossierCache[nm]={name:nm,sources:[j.report||j.summary||''].filter(Boolean),images:j.images||[],job};
          if(selectedName===nm)showDossier(nm);}
      }
    }
  }catch(e){}
  if(selectedName===nm){showDossier(nm)}
}

async function enrollFace(){
  const name=document.getElementById('enroll-name').value.trim();
  const st=document.getElementById('enroll-status');
  if(!name){st.textContent='Enter a name first.';return}
  if(!lastFrame){st.textContent='Start the camera first.';return}
  st.textContent='Enrolling '+name+'…';
  try{
    const r=await fetch('/api/faces/enroll',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name,image_b64:lastFrame,source:'webcam'})});
    const res=await r.json();
    st.textContent=res.ok?('✅ Enrolled '+name+(res.faces_detected>1?' ('+res.faces_detected+' faces found)':'')):(res.error||'Failed');
    st.style.color=res.ok?'#69f0ae':'#e57373';
    refreshKnown();
  }catch(e){st.textContent='❌ '+e.message}
}

function enrollPrompt(){
  document.getElementById('enroll-name').placeholder='Type a name for this face...';
  document.getElementById('enroll-name').value='';
  document.getElementById('enroll-name').focus();
  showTab('ctrl');
  document.getElementById('enroll-status').textContent='Type a name, then click Enroll to add this face to FAISS';
  document.getElementById('enroll-status').style.color='#fbbf24';
}

async function refreshKnown(){
  try{
    const r=await fetch('/api/faces');
    const res=await r.json();
    document.getElementById('face-count').textContent=(res.faces||[]).length;
  }catch(e){}
}
async function refreshDossiers(){
  try{
    const r=await fetch(LILLY+'/api/faces/footprints?limit=10');
    const res=await r.json();
    const jobs=res.jobs||[];
    const el=document.getElementById('dossier-list');
    el.innerHTML=jobs.length?jobs.map(j=>'<div class="poi-card" onclick="openDossier(\''+(j.name||'').replace(/'/g,"\'")+'\')" style="padding:8px 10px"><div class="poi-body"><div class="poi-name">'+esc(j.name)+'</div><div class="poi-meta">status '+esc(j.status||'')+' · '+(Math.round(j.elapsed_s||0))+'s</div><div class="poi-tags"><span class="tagchip '+(j.status==='done'?'tc-known':'tc-unknown')+'">'+(j.status||'')+'</span></div></div></div>').join(''):'<div class="empty">No dossiers yet — detected unknowns are queued to OSINT</div>';
    for(const j of jobs){if(j.status==='done'&&!dossierCache[j.name])loadDossierByName(j.name)}
  }catch(e){}
}

function setLog(agent,msg,meta){
  document.getElementById('log-agent').textContent=(agent||'Lilly').toUpperCase();
  document.getElementById('log-msg').textContent=msg;
  document.getElementById('log-meta').textContent=meta||'';
}
function tidy(s){return (s||'').replace(/[#*_]/g,'')}
function esc(s){const d=document.createElement('div');d.textContent=s==null?'':String(s);return d.innerHTML}

/* ── Drag & drop / paste / capture → analyze image ─────────────────── */
const dropzone=document.getElementById('dropzone');
const filepick=document.getElementById('filepick');
['dragenter','dragover'].forEach(ev=>window.addEventListener(ev,e=>{e.preventDefault();if(e.dataTransfer&&e.dataTransfer.types.includes('Files'))dropzone.classList.add('show')}));
window.addEventListener('dragleave',e=>{if(e.relatedTarget===null)dropzone.classList.remove('show')});
window.addEventListener('drop',e=>{
  e.preventDefault();dropzone.classList.remove('show');
  const f=e.dataTransfer&&e.dataTransfer.files[0];
  if(f&&f.type.startsWith('image/'))analyzeFile(f);
});
window.addEventListener('paste',e=>{
  const items=e.clipboardData&&e.clipboardData.items;
  if(!items)return;
  for(const it of items){if(it.type.startsWith('image/')){const f=it.getAsFile();if(f)analyzeFile(f)}}
});
filepick.addEventListener('change',()=>{if(filepick.files[0])analyzeFile(filepick.files[0])});

function blobToB64(blob){
  return new Promise((res,rej)=>{
    const r=new FileReader();
    r.onload=()=>res(String(r.result).split(',')[1]);
    r.onerror=rej;
    r.readAsDataURL(blob);
  });
}

async function analyzeFile(f){
  const img=document.createElement('img');
  img.src=URL.createObjectURL(f);
  await img.decode().catch(()=>{});
  const c=document.createElement('canvas');
  const scale=Math.min(1,1280/img.naturalWidth);
  c.width=Math.round(img.naturalWidth*scale);c.height=Math.round(img.naturalHeight*scale);
  c.getContext('2d').drawImage(img,0,0,c.width,c.height);
  URL.revokeObjectURL(img.src);
  const b64=c.toDataURL('image/jpeg',0.85).split(',')[1];
  analyzeB64(b64,'📷 '+f.name);
}

async function captureIdentify(){
  if(!running||video.readyState<2){setLog('Camera','Start the camera first, then capture','hint');return}
  const c=document.createElement('canvas');
  c.width=video.videoWidth;c.height=video.videoHeight;
  c.getContext('2d').drawImage(video,0,0);
  const b64=c.toDataURL('image/jpeg',0.9).split(',')[1];
  lastFrame=b64;
  analyzeB64(b64,'⚡ Live capture');
}

async function analyzeB64(b64,label){
  setLog(label,'Analyzing faces…','Sending frame to vision + FAISS');
  document.getElementById('lat').textContent='…';
  const t0=performance.now();
  try{
    const conf=parseInt(document.getElementById('confctl').value)/100;
    const avatar=document.getElementById('avatar').value;
    const res=await fetch('/api/vision',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({image_b64:b64,avatar,generate_audio:false})});
    const data=await res.json();
    const dets=data.detections||[];
    lastDetections=dets;
    const ms=Math.round(performance.now()-t0);
    document.getElementById('lat').textContent=ms;
    draw(dets);
    renderAlerts(dets);
    document.getElementById('targets').textContent=dets.length;
    const ppl=dets.filter(d=>isPerson(d));
    document.getElementById('people').textContent=ppl.length;
    document.getElementById('faces').textContent=dets.filter(d=>d.kps&&d.kps.length>=5).length;
    const names=_identityNames(dets);
    for(const n of names){if(!dossierCache[n])loadDossierByName(n);}
    if(ppl.length){
      const known=ppl.filter(d=>d.name);
      const unknown=ppl.filter(d=>!d.name);
      let msg=known.length?('Identified: '+known.map(d=>d.name+' ('+Math.round((d.face_confidence||0)*100)+'%)').join(', ')):'';
      if(unknown.length)msg+=(msg?' · ':'')+unknown.length+' UNKNOWN face(s) — not in FAISS database';
      if(!msg)msg='No people detected';
      setLog(label,msg,ms+'ms · '+ppl.length+' person(s) · '+(names.length?'FAISS match':'no match in DB'));
    }else{
      setLog(label,'No people found in this image','ms='+ms+' · '+dets.length+' object(s)');
    }
    showTab('ppl');
    renderPeople();
  }catch(e){
    setLog('Error',String(e.message||e),'analyze failed');
  }
}
</script>
</body>
</html>"""


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("PORT", "8198"))
    host = os.environ.get("HOST", "0.0.0.0")
    log.info(f"Starting Lilly Vision server on {host}:{port}")
    uvicorn.run(app, host=host, port=port)
