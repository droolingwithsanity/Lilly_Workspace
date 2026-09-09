"""Face Recognition module for Lilly AI camera view.

Uses OpenCV Haar Cascades for face detection and LBPH face recognizer
for identification. Provides face mesh overlay, corner brackets, and
name labels similar to the Face-Recognition repo style.

Usage:
    from face_recognition_module import FaceRecognizer
    fr = FaceRecognizer()
    faces = fr.detect_and_recognize(gray_frame)
    fr.draw_overlay(frame, faces)
"""

import cv2
import os
import time
import logging
import base64
import numpy as np
from pathlib import Path
from typing import List, Dict, Tuple, Optional

logger = logging.getLogger(__name__)

# Paths
MODELS_DIR = Path(__file__).parent / "models" / "face"
CASCADE_PATH = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
LBPH_MODEL_PATH = MODELS_DIR / "lbph_model.yml"
LABELS_PATH = MODELS_DIR / "labels.txt"

# Ensure models directory exists
MODELS_DIR.mkdir(parents=True, exist_ok=True)

# Colors (BGR for OpenCV)
CYAN = (255, 255, 0)  # Known faces
YELLOW = (0, 255, 255)  # Corner brackets
RED = (0, 0, 255)  # Unknown faces
WHITE = (255, 255, 255)
BLACK = (0, 0, 0)

# Face mesh points (relative to face bounding box)
MESH_POINTS = [
    (0.10, 0.45),
    (0.13, 0.60),
    (0.20, 0.75),
    (0.32, 0.88),
    (0.50, 0.95),
    (0.68, 0.88),
    (0.80, 0.75),
    (0.87, 0.60),
    (0.90, 0.45),  # 0-8 jawline
    (0.12, 0.22),
    (0.88, 0.22),  # 9-10 temples
    (0.28, 0.30),
    (0.40, 0.24),
    (0.60, 0.24),
    (0.72, 0.30),  # 11-14 brows
    (0.30, 0.42),
    (0.42, 0.40),
    (0.58, 0.40),
    (0.70, 0.42),  # 15-18 eyes
    (0.50, 0.46),
    (0.44, 0.60),
    (0.56, 0.60),
    (0.50, 0.63),  # 19-22 nose
    (0.34, 0.73),
    (0.50, 0.70),
    (0.66, 0.73),  # 23-25 mouth top
    (0.40, 0.82),
    (0.50, 0.84),
    (0.60, 0.82),  # 26-28 mouth bottom
]

MESH_LINES = [
    (0, 1),
    (1, 2),
    (2, 3),
    (3, 4),
    (4, 5),
    (5, 6),
    (6, 7),
    (7, 8),
    (9, 0),
    (10, 8),
    (9, 11),
    (10, 14),
    (11, 12),
    (12, 13),
    (13, 14),
    (11, 15),
    (12, 16),
    (13, 17),
    (14, 18),
    (15, 16),
    (16, 19),
    (17, 19),
    (17, 18),
    (19, 20),
    (19, 21),
    (20, 22),
    (21, 22),
    (16, 23),
    (17, 25),
    (22, 24),
    (23, 24),
    (24, 25),
    (23, 26),
    (25, 28),
    (26, 27),
    (27, 28),
    (2, 23),
    (6, 25),
    (3, 26),
    (5, 28),
]

CONFIDENCE_THRESHOLD = 70  # LBPH distance threshold (lower = stricter)


class FaceRecognizer:
    """Face detection and recognition using OpenCV Haar Cascades + LBPH."""

    def __init__(self):
        self.face_cascade = cv2.CascadeClassifier(CASCADE_PATH)
        if self.face_cascade.empty():
            logger.error(f"Failed to load face cascade from {CASCADE_PATH}")

        self.recognizer = cv2.face.LBPHFaceRecognizer_create()
        self.labels: Dict[int, str] = {}
        self._load_recognizer()

        # Tracking state
        self._face_history: List[Dict] = []
        self._last_faces: List[Dict] = []

    def _load_recognizer(self):
        """Load the LBPH model and labels if they exist."""
        if LBPH_MODEL_PATH.exists() and LABELS_PATH.exists():
            try:
                self.recognizer.read(str(LBPH_MODEL_PATH))
                with open(LABELS_PATH, "r") as f:
                    for line in f:
                        idx, name = line.strip().split(",", 1)
                        self.labels[int(idx)] = name
                logger.info(
                    f"Loaded face recognizer with {len(self.labels)} identities"
                )
            except Exception as e:
                logger.warning(f"Failed to load face recognizer: {e}")
        else:
            logger.info(
                "No pre-trained face recognizer found — will use detection only"
            )

    def save_recognizer(self):
        """Save the LBPH model and labels."""
        try:
            self.recognizer.write(str(LBPH_MODEL_PATH))
            with open(LABELS_PATH, "w") as f:
                for idx, name in self.labels.items():
                    f.write(f"{idx},{name}\n")
            logger.info("Face recognizer saved")
        except Exception as e:
            logger.error(f"Failed to save face recognizer: {e}")

    def detect_faces(self, gray_frame: np.ndarray) -> List[Tuple[int, int, int, int]]:
        """Detect faces in a grayscale frame."""
        faces = self.face_cascade.detectMultiScale(
            gray_frame, scaleFactor=1.1, minNeighbors=5, minSize=(60, 60)
        )
        return faces if len(faces) > 0 else []

    def detect_and_recognize(self, frame: np.ndarray) -> List[Dict]:
        """Detect faces and attempt recognition.

        Returns list of dicts:
            {
                "x": int, "y": int, "w": int, "h": int,
                "name": str, "confidence": float,
                "is_known": bool
            }
        """
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        faces = self.detect_faces(gray)

        results = []
        for x, y, w, h in faces:
            x, y, w, h = int(x), int(y), int(w), int(h)

            # Crop and resize face for recognition
            face_crop = cv2.resize(gray[y : y + h, x : x + w], (200, 200))

            name = "Unknown"
            confidence = 0.0
            is_known = False

            # Try recognition if model is loaded
            if self.labels:
                try:
                    label_id, distance = self.recognizer.predict(face_crop)
                    if distance < CONFIDENCE_THRESHOLD:
                        name = self.labels.get(label_id, "Unknown")
                        confidence = max(0, 100 - distance)
                        is_known = True
                except Exception as e:
                    logger.debug(f"Recognition failed: {e}")

            # Apply padding for better visualization
            pad_x = int(w * 0.15)
            pad_top = int(h * 0.35)
            pad_bottom = int(h * 0.15)
            px = max(0, x - pad_x)
            py = max(0, y - pad_top)
            pw = min(frame.shape[1] - px, w + pad_x * 2)
            ph = min(frame.shape[0] - py, h + pad_top + pad_bottom)

            results.append(
                {
                    "x": px,
                    "y": py,
                    "w": pw,
                    "h": ph,
                    "raw_x": x,
                    "raw_y": y,
                    "raw_w": w,
                    "raw_h": h,
                    "name": name,
                    "confidence": confidence,
                    "is_known": is_known,
                }
            )

        self._last_faces = results
        return results

    def draw_overlay(self, frame: np.ndarray, faces: List[Dict]) -> np.ndarray:
        """Draw face recognition overlay on frame.

        Draws:
        - Face mesh (jawline, brows, eyes, nose, mouth)
        - Corner brackets
        - Name labels with confidence
        - HUD with face count
        """
        overlay = frame.copy()

        for face in faces:
            x, y, w, h = face["x"], face["y"], face["w"], face["h"]
            name = face["name"]
            confidence = face["confidence"]
            is_known = face["is_known"]

            # Draw face mesh
            self._draw_mesh(overlay, x, y, w, h)

            # Draw corner brackets
            self._draw_corners(overlay, x, y, w, h)

            # Draw name label
            self._draw_label(overlay, x, y, w, h, name, confidence, is_known)

        # Blend mesh overlay
        cv2.addWeighted(overlay, 0.7, frame, 0.3, 0, frame)

        # Draw HUD
        self._draw_hud(frame, len(faces))

        return frame

    def _draw_mesh(self, frame: np.ndarray, x: int, y: int, w: int, h: int):
        """Draw face mesh with points and connecting lines."""
        pts = [(int(x + rx * w), int(y + ry * h)) for rx, ry in MESH_POINTS]

        for a, b in MESH_LINES:
            cv2.line(frame, pts[a], pts[b], CYAN, 1, cv2.LINE_AA)

        for p in pts:
            cv2.circle(frame, p, 2, WHITE, -1, cv2.LINE_AA)

    def _draw_corners(
        self, frame: np.ndarray, x: int, y: int, w: int, h: int, size: int = 28
    ):
        """Draw corner brackets around face."""
        t = 3
        for px, py, dx, dy in [
            (x, y, 1, 1),
            (x + w, y, -1, 1),
            (x, y + h, 1, -1),
            (x + w, y + h, -1, -1),
        ]:
            cv2.line(frame, (px, py), (px + dx * size, py), YELLOW, t)
            cv2.line(frame, (px, py), (px, py + dy * size), YELLOW, t)

    def _draw_label(
        self,
        frame: np.ndarray,
        x: int,
        y: int,
        w: int,
        h: int,
        name: str,
        confidence: float,
        is_known: bool,
    ):
        """Draw name label with background."""
        color = CYAN if is_known else RED
        label = name.upper()

        (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_DUPLEX, 1.2, 2)
        label_y = y - 35 if y - 35 > th else y + h + th + 45

        # Background rectangle
        cv2.rectangle(
            frame,
            (x - 4, label_y - th - 8),
            (x + tw + 8, label_y + 8),
            BLACK,
            cv2.FILLED,
        )
        cv2.putText(frame, label, (x, label_y), cv2.FONT_HERSHEY_DUPLEX, 1.2, color, 2)

        # Confidence percentage for known faces
        if is_known and confidence > 0:
            conf_text = f"{confidence:.0f}% MATCH"
            cv2.putText(
                frame,
                conf_text,
                (x, label_y + 25),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.7,
                color,
                2,
            )

    def _draw_hud(self, frame: np.ndarray, face_count: int):
        """Draw HUD with face count."""
        h, w = frame.shape[:2]

        # Background bar
        cv2.rectangle(frame, (0, h - 50), (200, h), BLACK, cv2.FILLED)

        # Face count
        cv2.putText(
            frame,
            f"FACES: {face_count}",
            (10, h - 28),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.6,
            CYAN,
            1,
        )

        # LIVE indicator
        cv2.rectangle(frame, (w - 90, 0), (w, 30), (0, 0, 255), cv2.FILLED)
        cv2.putText(frame, "LIVE", (w - 78, 22), cv2.FONT_HERSHEY_DUPLEX, 0.6, WHITE, 2)

    def process_frame(self, frame: np.ndarray) -> Tuple[np.ndarray, List[Dict]]:
        """Full pipeline: detect + recognize + draw overlay.

        Returns (annotated_frame, face_data_list)
        """
        faces = self.detect_and_recognize(frame)
        annotated = self.draw_overlay(frame.copy(), faces)
        return annotated, faces

    def encode_frame(self, frame: np.ndarray, quality: int = 85) -> str:
        """Encode frame as base64 JPEG."""
        _, buffer = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, quality])
        return base64.b64encode(buffer).decode("utf-8")

    def get_face_data_json(self, faces: List[Dict]) -> List[Dict]:
        """Convert face data to JSON-serializable format."""
        return [
            {
                "x": f["x"],
                "y": f["y"],
                "w": f["w"],
                "h": f["h"],
                "name": f["name"],
                "confidence": round(f["confidence"], 1),
                "is_known": f["is_known"],
            }
            for f in faces
        ]


# Singleton instance
_face_recognizer: Optional[FaceRecognizer] = None


def get_face_recognizer() -> FaceRecognizer:
    """Get or create the singleton FaceRecognizer instance."""
    global _face_recognizer
    if _face_recognizer is None:
        _face_recognizer = FaceRecognizer()
    return _face_recognizer
