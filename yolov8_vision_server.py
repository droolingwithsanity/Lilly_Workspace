#!/usr/bin/env python3
"""
Lilly Vision — YOLOv8 Multi-Agent Commentary with Edge TTS
Multiple AI personalities comment on what the camera sees, with unique voices.
"""

import base64
import os
import time
import random
import logging
import asyncio
import tempfile
from pathlib import Path
from contextlib import asynccontextmanager

import cv2
import numpy as np
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, FileResponse
from ultralytics import YOLO

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("lilly-vision")

MODEL = None
CONF_THRESHOLD = float(os.environ.get("CONF_THRESHOLD", "0.35"))
MODEL_NAME = os.environ.get("YOLO_MODEL", "yolov8n.pt")

PERSON_KEYWORDS = {"person", "human", "face", "man", "woman"}
VEHICLE_KEYWORDS = {"car", "truck", "bus", "motorcycle", "bicycle", "motorbike", "train", "airplane", "boat"}
DEVICE_KEYWORDS = {"laptop", "cell phone", "tv", "mouse", "keyboard", "remote", "monitor", "screen"}
ANIMAL_KEYWORDS = {"cat", "dog", "bird", "horse", "sheep", "cow", "elephant", "bear", "zebra", "giraffe"}
FOOD_ITEMS = {"banana", "apple", "sandwich", "orange", "broccoli", "carrot", "hot dog", "pizza", "donut", "cake",
              "bottle", "wine glass", "cup", "fork", "knife", "spoon", "bowl"}
FASHION_ITEMS = {"tie", "backpack", "handbag", "suitcase", "umbrella"}

# Average real-world widths (meters) for common YOLO classes
YOLO_CLASS_WIDTHS = {
    "person": 0.5, "bicycle": 0.6, "car": 1.8, "motorcycle": 0.8,
    "bus": 2.5, "truck": 2.5, "cat": 0.4, "dog": 0.5,
    "chair": 0.5, "couch": 2.0, "dining table": 1.2, "bed": 1.5,
    "laptop": 0.35, "tv": 1.0, "cell phone": 0.08, "book": 0.2,
    "bottle": 0.08, "cup": 0.08, "bowl": 0.15, "keyboard": 0.4,
    "mouse": 0.06, "remote": 0.15, "backpack": 0.3, "umbrella": 1.0,
    "suitcase": 0.5, "clock": 0.2, "vase": 0.15, "potted plant": 0.3,
    "sink": 0.6, "toilet": 0.4, "refrigerator": 0.8, "microwave": 0.5,
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
    if m < 0.5: return "very close"
    if m < 1.5: return "arm's length"
    if m < 3: return "a few steps"
    if m < 10: return "nearby"
    if m < 30: return "across the room"
    if m < 100: return "in the distance"
    return f"{int(m)}m away"


def classify_label(label: str) -> str:
    low = label.lower()
    if low in PERSON_KEYWORDS: return "person"
    if low in VEHICLE_KEYWORDS: return "vehicle"
    if low in DEVICE_KEYWORDS: return "device"
    if low in ANIMAL_KEYWORDS: return "animal"
    if low in FOOD_ITEMS: return "food"
    if low in FASHION_ITEMS: return "fashion"
    return low


def build_sensor_context(sensors: dict) -> str:
    if not sensors:
        return ""
    parts = []
    light = sensors.get("light", sensors.get("ambient_light"))
    if light is not None:
        if light < 50: parts.append("dark room")
        elif light > 1000: parts.append("bright daylight")
    temp = sensors.get("temperature")
    if temp is not None and (temp > 30 or temp < 10):
        parts.append(f"{temp}°C")
    steps = sensors.get("step_counter", sensors.get("steps"))
    if steps is not None and steps > 10000:
        parts.append(f"{steps} steps today")
    motion = sensors.get("significant_motion", sensors.get("motion"))
    if motion and motion > 0:
        parts.append("in motion")
    return ", ".join(parts) if parts else ""


# ── Agent voices (Edge TTS voice IDs) ───────────────────────────────────
AGENTS = {
    "puppy": {
        "name": "Puppy",
        "emoji": "🐕",
        "voice": "en-US-AvaNeural",        # Female, expressive, caring
        "rate": "+10%",                      # Slightly faster
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
        "voice": "en-US-AndrewNeural",       # Male, warm, confident
        "rate": "-5%",                        # Slightly slower, more deliberate
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
        "voice": "en-US-EmmaNeural",         # Female, cheerful, clear
        "rate": "-10%",                       # Slower, more deliberate
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
        "voice": "en-US-BrianNeural",        # Male, approachable, casual
        "rate": "-15%",                       # Slower, more thoughtful
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
        "voice": "en-US-JennyNeural",        # Female, friendly, considerate
        "rate": "+5%",                        # Slightly faster, energetic
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
        "rate": "-20%",                       # Slowest, most measured
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


def generate_reply(detections: list) -> str:
    if not detections:
        return random.choice([
            "Nothing detected. Either it's dark or you live in a minimalist nightmare.",
            "Zero targets. Try pointing me at something that isn't a wall.",
            "I see absolutely nothing. Are you testing me or is this just your life?",
        ])
    total = len(detections)
    labels = [d["label"] for d in detections]
    return f"Detected {total} targets: {', '.join(labels[:5])}"


def generate_agent_replies(detections: list, sensors: dict, avatar: str) -> list:
    if not detections:
        return []

    detected_set = set(d["label"] for d in detections)
    categories_hit = set(classify_label(l) for l in detected_set)

    all_agents = list(AGENTS.keys())
    if avatar in all_agents:
        all_agents.remove(avatar)
    random.shuffle(all_agents)
    speaking = [avatar] + all_agents[:2]

    replies = []
    for agent_id in speaking:
        agent = AGENTS[agent_id]
        lines = agent["lines"]

        if agent_id == "puppy" and "animal" in categories_hit:
            animal_labels = detected_set & ANIMAL_KEYWORDS
            animal = list(animal_labels)[0] if animal_labels else "dog"
            key = "dog" if animal == "dog" else "cat" if animal == "cat" else "animal"
            line = random.choice(lines.get(key, lines["default"]))
        elif agent_id == "cat" and "animal" in categories_hit:
            if detected_set & {"cat"}:
                line = random.choice(lines["cat"])
            else:
                line = random.choice(lines.get("default", ["*stares*"]))
        elif agent_id == "fox" and "food" in categories_hit:
            line = random.choice(lines["food"])
        else:
            for cat in ["person", "food", "fashion", "device", "animal"]:
                if cat in categories_hit:
                    line = random.choice(lines.get(cat, lines["default"]))
                    break
            else:
                line = random.choice(lines["default"])

        sensor_prefix = ""
        if agent_id == speaking[0]:
            ctx = build_sensor_context(sensors)
            if ctx:
                sensor_prefix = f"[{ctx}] "

        replies.append({
            "agent": agent["name"],
            "emoji": agent["emoji"],
            "voice": agent["voice"],
            "text": f"{sensor_prefix}{line}",
        })

    return replies


@asynccontextmanager
async def lifespan(app: FastAPI):
    global MODEL
    log.info(f"Loading YOLO model: {MODEL_NAME}")
    MODEL = YOLO(MODEL_NAME)
    log.info(f"YOLO model loaded. Confidence threshold: {CONF_THRESHOLD}")
    yield
    log.info("Shutting down.")


app = FastAPI(title="Lilly Vision Server", lifespan=lifespan)


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
    sensors = body.get("sensors", {})
    generate_audio = body.get("generate_audio", False)

    t0 = time.time()

    try:
        raw = base64.b64decode(image_b64)
        buf = np.frombuffer(raw, dtype=np.uint8)
        frame = cv2.imdecode(buf, cv2.IMREAD_COLOR)
        if frame is None:
            return JSONResponse(status_code=400, content={"error": "Could not decode image"})
    except Exception as e:
        return JSONResponse(status_code=400, content={"error": f"Image decode error: {e}"})

    if MODEL is None:
        return JSONResponse(status_code=503, content={"error": "YOLO model not loaded"})

    try:
        results = MODEL(frame, conf=CONF_THRESHOLD, verbose=False)
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
            conf = float(box.conf[0])
            cls_id = int(box.cls[0])
            label = MODEL.names[cls_id]

            nx = float(x1) / w
            ny = float(y1) / h
            nw = float(x2 - x1) / w
            nh = float(y2 - y1) / h

            detections.append({
                "label": label,
                "x": round(nx, 4),
                "y": round(ny, 4),
                "w": round(nw, 4),
                "h": round(nh, 4),
                "conf": round(conf, 4),
                "distance_m": estimate_distance(label, int(x2 - x1), w),
                "distance_desc": distance_desc(estimate_distance(label, int(x2 - x1), w)) if estimate_distance(label, int(x2 - x1), w) else None,
            })

    elapsed = round(time.time() - t0, 3)
    reply = generate_reply(detections)
    agents = generate_agent_replies(detections, sensors, avatar)

    # Generate TTS for each agent if requested
    if generate_audio and agents:
        for agent_reply in agents:
            agent_cfg = AGENTS.get(agent_reply["agent"].lower(), {})
            voice = agent_cfg.get("voice", "en-US-JennyNeural")
            rate = agent_cfg.get("rate", "+0%")
            audio_id = await generate_tts(reply, voice, rate)
            agent_reply["audio_id"] = audio_id

    log.info(f"Detected {len(detections)} targets in {elapsed}s — {len(agents)} agents responded")

    return {
        "reply": reply,
        "agents": agents,
        "detections": detections,
        "audio_id": 0,
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
    sensors = body.get("sensors", {})

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
        results = MODEL(frame, conf=CONF_THRESHOLD, verbose=False)
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
        (len(new_labels) > 0) or
        (len(_last_proactive_labels) > 0 and len(current_labels) == 0) or
        (len(current_labels) > 0 and len(_last_proactive_labels) == 0)
    )

    if not scene_changed:
        _last_proactive_labels = current_labels
        return {"should_speak": False, "reason": "no_change", "labels": list(current_labels)}

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
            detections.append({
                "label": label,
                "x": round(float(x1) / w, 4),
                "y": round(float(y1) / h, 4),
                "w": round(float(x2 - x1) / w, 4),
                "h": round(float(y2 - y1) / h, 4),
                "conf": round(conf, 4),
            })

    reply = generate_reply(detections)
    agents = generate_agent_replies(detections, sensors, avatar)

    # Generate TTS for primary agent
    audio_id = 0
    if agents:
        primary = agents[0]
        agent_cfg = AGENTS.get(primary["agent"].lower(), {})
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


@app.get("/api/ui_state")
async def ui_state():
    return {
        "mood": "curious",
        "speaking": False,
        "vision_active": False,
        "continuous_scan": False,
    }


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", "8198"))
    host = os.environ.get("HOST", "0.0.0.0")
    log.info(f"Starting Lilly Vision server on {host}:{port}")
    uvicorn.run(app, host=host, port=port)
