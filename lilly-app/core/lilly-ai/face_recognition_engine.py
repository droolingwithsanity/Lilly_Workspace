"""Face recognition engine — OpenCV-based face detection + storage.

Stores face images linked to user IDs (Google accounts) for recognition.
Uses Haar cascade for detection and pixel-space comparison for matching.
"""
import base64
import json
import time
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

_FACES_DIR = Path(__file__).parent / "known_faces"
_FACES_DIR.mkdir(exist_ok=True)
_FACE_INDEX: dict[str, list[dict]] = {}

_HAAR_CASCADE = None


def _get_haar():
    global _HAAR_CASCADE
    if _HAAR_CASCADE is None:
        cascade_path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
        _HAAR_CASCADE = cv2.CascadeClassifier(cascade_path)
    return _HAAR_CASCADE


def _load_index():
    global _FACE_INDEX
    idx_file = _FACES_DIR / "index.json"
    if idx_file.exists():
        try:
            _FACE_INDEX = json.loads(idx_file.read_text())
        except Exception:
            _FACE_INDEX = {}


def _save_index():
    _FACES_DIR.mkdir(exist_ok=True)
    (_FACES_DIR / "index.json").write_text(json.dumps(_FACE_INDEX, indent=2))


def _compute_encoding(face_img):
    resized = cv2.resize(face_img, (64, 64))
    gray = cv2.cvtColor(resized, cv2.COLOR_BGR2GRAY) if len(resized.shape) == 3 else resized
    norm = gray.astype(np.float32) / 255.0
    return norm.flatten().tolist()


def _compare_faces(enc_a, enc_b):
    a = np.array(enc_a, dtype=np.float32)
    b = np.array(enc_b, dtype=np.float32)
    dot = float(np.dot(a, b))
    na = float(np.linalg.norm(a))
    nb = float(np.linalg.norm(b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


class FaceEngine:
    def __init__(self):
        _load_index()

    def detect_faces(self, frame):
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        faces = _get_haar().detectMultiScale(gray, 1.1, 5, minSize=(40, 40))
        return [{"x": int(x), "y": int(y), "w": int(w), "h": int(h)} for (x, y, w, h) in faces]

    def add_known_face(self, user_id, frame, face_box, name="", source="api"):
        x, y, w, h = face_box["x"], face_box["y"], face_box["w"], face_box["h"]
        pad = int(w * 0.15)
        x1, y1 = max(0, x - pad), max(0, y - pad)
        x2, y2 = min(frame.shape[1], x + w + pad), min(frame.shape[0], y + h + pad)
        face_crop = frame[y1:y2, x1:x2]
        if face_crop.size == 0:
            return False
        encoding = _compute_encoding(face_crop)
        thumb_path = _FACES_DIR / f"{user_id}_{int(time.time())}.jpg"
        cv2.imwrite(str(thumb_path), face_crop)
        entry = {"name": name or user_id, "encoding": encoding, "thumb": str(thumb_path.name), "ts": time.time(), "source": source}
        if user_id not in _FACE_INDEX:
            _FACE_INDEX[user_id] = []
        _FACE_INDEX[user_id].append(entry)
        _save_index()
        return True

    def remove_known_face(self, user_id):
        if user_id in _FACE_INDEX:
            del _FACE_INDEX[user_id]
            _save_index()
            return True
        return False

    def identify_face(self, frame, face_box, threshold=0.65):
        x, y, w, h = face_box["x"], face_box["y"], face_box["w"], face_box["h"]
        pad = int(w * 0.15)
        x1, y1 = max(0, x - pad), max(0, y - pad)
        x2, y2 = min(frame.shape[1], x + w + pad), min(frame.shape[0], y + h + pad)
        face_crop = frame[y1:y2, x1:x2]
        if face_crop.size == 0:
            return None
        enc = _compute_encoding(face_crop)
        best_match = None
        best_score = 0.0
        for uid, entries in _FACE_INDEX.items():
            for entry in entries:
                score = _compare_faces(enc, entry["encoding"])
                if score > best_score:
                    best_score = score
                    best_match = {"user_id": uid, "name": entry.get("name", uid), "confidence": round(score, 3)}
        if best_match and best_score >= threshold:
            return best_match
        return None

    def get_user_faces(self, user_id):
        entries = _FACE_INDEX.get(user_id, [])
        return [{"name": e.get("name", ""), "ts": e.get("ts", 0), "thumb": e.get("thumb", "")} for e in entries]

    def get_all_registered(self):
        return {uid: len(entries) for uid, entries in _FACE_INDEX.items()}


_engine = None

def get_face_engine():
    global _engine
    if _engine is None:
        _engine = FaceEngine()
    return _engine

def on_identity_confirmed(user_id, face_id):
    pass
