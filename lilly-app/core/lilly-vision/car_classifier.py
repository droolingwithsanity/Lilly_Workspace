"""Vehicle make/model classifier for the vision pipeline.

Boosts raw YOLO classes ("Car", "Vehicle") into real identities:
  "2012 BMW X5" → make="BMW", model="X5", year=2012

Uses the ZEDEDA ResNet50 ONNX fine-tuned on Stanford Cars 196.
Always best-effort: if the model or onnxruntime is unavailable the
pipeline degrades silently back to generic labels.
"""
import os
import threading
import time
import urllib.request

import cv2
import numpy as np

MODEL_URL = os.environ.get(
    "CAR_MODEL_URL",
    "https://huggingface.co/zededa/resnet50-cars/resolve/main/resnet50_cars_enhanced.onnx",
)
MODEL_PATH = os.environ.get("CAR_MODEL_PATH", "/app/data/models/resnet50_cars_enhanced.onnx")
CONF_MIN = float(os.environ.get("CAR_CONF_MIN", "0.25"))

_LABELS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "car_labels.txt")

_IMAGENET_MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32).reshape(1, 3, 1, 1)
_IMAGENET_STD = np.array([0.229, 0.224, 0.225], dtype=np.float32).reshape(1, 3, 1, 1)

LOADED = None
_LOAD_LOCK = threading.Lock()
_LOADED_AT = 0.0

# Vehicle buckets from YOLO Open Images V7 that merit make/model classification.
_VEHICLE_LABELS = {
    "car", "vehicle", "truck", "pickup truck", "pickup",
    "suv", "van", "taxi", "police car", "bus",
}

# Tiny in-process cache: bbox hash → guess, avoids reclassifying a static car each frame.
_CACHE = {}
_CACHE_MAX = 32
_CACHE_TTL = 30.0


def _load() -> None:
    global LOADED, _LOADED_AT
    with _LOAD_LOCK:
        if LOADED is not None or time.time() - _LOADED_AT < 3600:
            return
        _LOADED_AT = time.time()
        try:
            import onnxruntime as ort
        except Exception:
            LOADED = False
            return
        try:
            os.makedirs(os.path.dirname(MODEL_PATH), exist_ok=True)
            if not os.path.exists(MODEL_PATH):
                req = urllib.request.Request(MODEL_URL, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=120) as r, open(MODEL_PATH, "wb") as f:
                    total = int(r.headers.get("Content-Length", 0))
                    done = 0
                    while True:
                        chunk = r.read(1 << 16)
                        if not chunk:
                            break
                        f.write(chunk)
                        done += len(chunk)
                        if total and done % (1 << 20) < (1 << 16):
                            print(f"[car] model {done // (1 << 20)}/{total // (1 << 20)}MB", flush=True)
            sess = ort.InferenceSession(MODEL_PATH, providers=["CPUExecutionProvider"])
            labels = []
            with open(_LABELS_PATH, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    _, _, name = line.partition("\t")
                    labels.append(name)
            input_name = sess.get_inputs()[0].name
            LOADED = {
                "sess": sess,
                "labels": labels,
                "input_name": input_name,
            }
        except Exception:
            LOADED = False


def _preprocess(bgr: np.ndarray) -> np.ndarray:
    img = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    h, w = img.shape[:2]
    scale = max(256.0 / h, 256.0 / w)
    nh, nw = int(round(h * scale)), int(round(w * scale))
    img = cv2.resize(img, (nw, nh), interpolation=cv2.INTER_LINEAR)
    top = max(0, (nh - 224) // 2)
    left = max(0, (nw - 224) // 2)
    img = img[top:top + 224, left:left + 224]
    x = img.astype(np.float32) / 255.0
    x = x.transpose(2, 0, 1)[None]
    x = (x - _IMAGENET_MEAN) / _IMAGENET_STD
    return np.ascontiguousarray(x)


def is_ready() -> bool:
    """True when onnxruntime + weights are loaded. Never triggers a download."""
    return LOADED is not None and LOADED is not False


def preload() -> None:
    """Background load (downloads weights on first run). Never raises."""
    try:
        _load()
    except Exception:
        pass


def classify_crop(bgr: np.ndarray) -> dict:
    """Top candidate make/model for a cropped vehicle. Returns {} if unavailable."""
    _load()
    if not LOADED or bgr is None or bgr.size == 0:
        return {}
    sess = LOADED["sess"]
    try:
        x = _preprocess(bgr)
        out = sess.run(None, {LOADED["input_name"]: x})[0]
        logits = np.squeeze(out).astype(np.float64)
        max_logit = logits.max()
        exp = np.exp(logits - max_logit)
        probs = exp / exp.sum()
        top3 = np.argsort(probs)[::-1][:3]
        candidates = []
        for idx in top3:
            p = float(probs[idx])
            if p < 0.01:
                break
            candidates.append({"label": LOADED["labels"][idx], "conf": round(p, 4)})
        if not candidates:
            return {}
        top = candidates[0]
        return {
            "car_guess": top["label"],
            "car_conf": top["conf"],
            "candidates": candidates,
        }
    except Exception:
        return {}


def _guess_from_name(name: str) -> tuple:
    """'2012 BMW X5' → ('BMW', 'X5', 2012). Falls back to making no claims."""
    parts = name.split()
    if not parts:
        return None, None, None
    year = None
    for i, p in enumerate(parts):
        if p.isdigit() and len(p) == 4 and 1980 <= int(p) <= 2030:
            year = int(p)
            break
    if year is None:
        return parts[0], " ".join(parts[1:]), None
    rest = [p for p in parts if not (p.isdigit() and len(p) == 4)]
    if not rest:
        return None, str(year), year
    return rest[0], " ".join(rest[1:]), year


def enrich_vehicle_detections(frame: np.ndarray, detections: list) -> list:
    """Attach make/model to car-like detections in place and return them."""
    found = False
    for det in detections:
        if det.get("label", "").lower() not in _VEHICLE_LABELS:
            continue
        found = True
    if not found:
        return detections
    h, w = frame.shape[:2]
    for det in detections:
        if det.get("label", "").lower() not in _VEHICLE_LABELS:
            continue
        if det.get("car_guess"):
            continue  # already classified
        nx, ny, nw, nh = det.get("x", 0.5), det.get("y", 0.2), det.get("w", 0.5), det.get("h", 0.5)
        x1, y1 = int(nx * w), int(ny * h)
        x2, y2 = int((nx + nw) * w), int((ny + nh) * h)
        # Expand a little so the full car/silhouette is captured for classification.
        pad_x, pad_y = int((x2 - x1) * 0.15), int((y2 - y1) * 0.15)
        x1, y1 = max(0, x1 - pad_x), max(0, y1 - pad_y)
        x2, y2 = min(w, x2 + pad_x), min(h, y2 + pad_y)
        if x2 - x1 < 40 or y2 - y1 < 40:
            continue
        crop = frame[y1:y2, x1:x2]
        cache_key = (x1 // 20, y1 // 20, (x2 - x1) // 20, (y2 - y1) // 20)
        cached = _CACHE.get(cache_key)
        if cached and time.time() - cached[1] < _CACHE_TTL:
            det.update(cached[0])
            continue
        guess = classify_crop(crop)
        if not guess:
            continue
        make, model, year = _guess_from_name(guess["car_guess"])
        extra = {
            "car_guess": guess["car_guess"],
            "car_conf": guess["car_conf"],
            "candidates": guess["candidates"],
        }
        if make:
            extra["make"] = make
        if model:
            extra["model"] = model
        if year:
            extra["year"] = year
        det.update(extra)
        if guess["car_conf"] >= CONF_MIN and make and model:
            det["label"] = f"{make} {model}"
        _CACHE[cache_key] = (extra, time.time())
        if len(_CACHE) > _CACHE_MAX:
            oldest = min(_CACHE, key=lambda k: _CACHE[k][1])
            _CACHE.pop(oldest, None)
    return detections