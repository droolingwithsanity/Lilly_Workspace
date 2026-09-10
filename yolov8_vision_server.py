#!/usr/bin/env python3
"""
Lilly Vision — YOLOv8 Multi-Agent Commentary with Edge TTS
Multiple AI personalities comment on what the camera sees, with unique voices.
"""

import base64
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
CONF_THRESHOLD = float(os.environ.get("CONF_THRESHOLD", "0.35"))
# Prefer Open Images V7 (601 classes) over COCO (80 classes) for broader detection
MODEL_NAME = os.environ.get("YOLO_MODEL", "yolov8n-oiv7.pt")
MODEL_PATH = os.environ.get("YOLO_MODEL_PATH", "")
IOU_THRESHOLD = float(os.environ.get("YOLO_IOU_THRESHOLD", "0.45"))
MAX_DETECTIONS = int(os.environ.get("YOLO_MAX_DETECTIONS", "300"))

# ── Per-frame cost controls ────────────────────────────────────────────
# The heavy identification stages (InsightFace, MediaPipe blink, the vehicle
# classifier) only need to run a few times a second — their results are cached
# and re-applied to boxes in between, so the overlay stays smooth on CPU.
MAX_INFER_SIDE = int(os.environ.get("MAX_INFER_SIDE", "960"))      # cap YOLO input
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
UNKNOWN_FACE_SAMPLE_INTERVAL = float(os.environ.get("UNKNOWN_FACE_SAMPLE_INTERVAL", "8.0"))
UNKNOWN_FACE_MAX = int(os.environ.get("UNKNOWN_FACE_MAX", "12"))
OSINT_PUSH_URL = os.environ.get("OSINT_PUSH_URL", "")  # lilly endpoint (empty = disabled)
OSINT_PUSH_INTERVAL = float(os.environ.get("OSINT_PUSH_INTERVAL", "15.0"))

_unknown_faces: list[dict] = []   # unsolved people awaiting OSINT (id-stable)
_osint_results: dict[str, dict] = {}  # id -> {name, social_accounts, sources, confidence, person_box, ts}


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
            if item.get("label") and item["label"].lower() == str(det.get("class", "")).lower():
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
    return {"x": d.get("x", 0), "y": d.get("y", 0), "w": d.get("w", 0), "h": d.get("h", 0)}


def _box_center(box: dict) -> tuple:
    return (box.get("x", 0) + box.get("w", 0) / 2, box.get("y", 0) + box.get("h", 0) / 2)


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
        item = {"box": _person_box(d), "kps": d.get("kps"), "face_confidence": d.get("face_confidence")}
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
            (e for e in _unknown_faces
             if abs(_box_center(e["person_box"])[0] - cx) < 0.06
             and abs(_box_center(e["person_box"])[1] - cy) < 0.06),
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
    kept = [e for e in _unknown_faces if e.get("resolved") is not None or now - e.get("osint_ts", 0) < 600]
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
                            e["osint_ts"] = now  # OSINT disabled upstream — cooldown instead of spam
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
    "bicycle": 0.6,
    "car": 1.8,
    "motorcycle": 0.8,
    "bus": 2.5,
    "truck": 2.5,
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
SCENE_TTL_SECS = float(os.environ.get("SCENE_TTL_SECS", "10"))
SENSOR_URL = os.environ.get("SENSOR_SERVER_URL", "http://100.115.234.87:8099")

_phone_sensor_cache: dict = {"ts": 0.0, "data": {}}  # live phone sensor context (always-on, best effort)


async def _fetch_phone_sensors() -> dict:
    """Pull live spatial context from the phone's sensor server (port 8099).
    Always-on but strictly best-effort: never raises, degrades to {} when the
    phone is offline, and is cached so it adds no latency at steady state."""
    global _phone_sensor_cache
    now = time.time()
    if _phone_sensor_cache["data"] and now - _phone_sensor_cache["ts"] < 4.0:
        return _phone_sensor_cache["data"]
    try:
        import httpx

        async with httpx.AsyncClient(timeout=2.5) as client:
            r = await client.get(f"{SENSOR_URL}/sensors/all")
            if r.status_code == 200:
                data = r.json()
                _phone_sensor_cache = {"ts": now, "data": data}
                return data
    except Exception:
        pass
    return {}


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
    pad = " I recognize a few of you." if any(
        d.get("label") != "person" and d.get("original_label") == "person"
        for d in detections
    ) else ""
    return "Right now I can see " + ", ".join(rows[:6]) + "." + pad


async def generate_reply(detections: list, sensors: dict | None = None, avatar: str = "") -> str:
    """Produce ONE cohesive scene description in the active avatar's voice,
    de-duplicated so the same scene isn't re-narrated on every frame."""
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
        text = describe_scene_fallback([])
    else:
        text = await _llm_describe(scene, sensors or {}, avatar)
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

    if MODEL is None:
        return JSONResponse(status_code=503, content={"error": "YOLO model not loaded"})

    try:
        work, sx, sy = _resize_for_inference(frame)
        results = cast(list, MODEL(work, conf=CONF_THRESHOLD, verbose=False))
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

    # ── Face recognition: rename "person" → "John" etc. ──────────────
    global FACE_ENGINE, _face_enrich_cache, _vehicle_enrich_cache, _blink_ts
    if FACE_ENGINE is None:
        try:
            FACE_ENGINE = get_face_engine()
        except Exception:
            FACE_ENGINE = False  # prevent retry

    now = time.time()
    if FACE_ENGINE and any(d.get("label", "").lower() in PERSON_KEYWORDS for d in detections):
        if now - _face_enrich_cache["ts"] >= FACE_ENRICH_INTERVAL:
            try:
                detections = FACE_ENGINE.enrich_person_detections(frame, detections)
                detections = _apply_osint_results(detections, now)
                _sample_unknown_faces(frame, detections, now)
                _face_enrich_cache = {"ts": now, "items": _face_cache_items(detections)}
            except Exception as e:
                log.warning(f"Face recognition error: {e}")
        else:
            detections = _apply_cached_ids(detections, _face_enrich_cache, "original_label")

    # ── Vehicle make/model: turn "Car" into "2012 BMW X5" ──────────────
    try:
        import car_classifier

        if car_classifier.is_ready() and any(
            d.get("class", "").lower() in car_classifier._VEHICLE_LABELS for d in detections
        ):
            if now - _vehicle_enrich_cache["ts"] >= CAR_CLASSIFY_INTERVAL:
                detections = car_classifier.enrich_vehicle_detections(frame, detections)
                _vehicle_enrich_cache = {
                    "ts": now,
                    "items": [
                        {"label": d.get("label"),
                         "make": d.get("make"), "model": d.get("model"),
                         "year": d.get("year"), "car_conf": d.get("car_conf"),
                         "box": {"x": d.get("x", 0), "y": d.get("y", 0), "w": d.get("w", 0), "h": d.get("h", 0)}}
                        for d in detections
                        if d.get("make")
                    ],
                }
            else:
                detections = _apply_cached_ids(detections, _vehicle_enrich_cache, "make")
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
            blink_result = BLINK_DETECTOR.detect(frame, int(time.time() * 1000))
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

    elapsed = round(time.time() - t0, 3)
    reply = await generate_reply(detections, sensors, avatar)
    agents = generate_agent_replies(reply, avatar)

    if generate_audio and agents:
        agent_cfg = AGENTS.get(avatar or "puppy", AGENTS["puppy"])
        voice = agent_cfg.get("voice", "en-US-JennyNeural")
        rate = agent_cfg.get("rate", "+0%")
        audio_id = await generate_tts(reply, voice, rate)
        agents[0]["audio_id"] = audio_id

    log.info(
        f"Detected {len(detections)} targets in {elapsed}s — narrated in a single voice ({avatar or 'puppy'})"
    )

    response = {
        "reply": reply,
        "agents": agents,
        "detections": detections,
        "audio_id": 0,
    }

    # Add blink info to response if available
    if blink_info:
        response["blink"] = blink_info

    return response


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

    reply = await generate_reply(detections, sensors, avatar)
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
            {"id": e["id"], "person_box": e["person_box"], "resolved": e.get("resolved")}
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
        "person_box": body.get("person_box") or (entry or {}).get("person_box", {}),
        "ts": time.time(),
    }
    if entry:
        entry["osint_ts"] = time.time()
        if body.get("name"):
            entry["resolved"] = body["name"]
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


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("PORT", "8198"))
    host = os.environ.get("HOST", "0.0.0.0")
    log.info(f"Starting Lilly Vision server on {host}:{port}")
    uvicorn.run(app, host=host, port=port)
