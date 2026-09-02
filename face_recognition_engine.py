#!/usr/bin/env python3
"""
Lilly Face Recognition Engine
─────────────────────────────
Identifies known faces from YOLO person detections.
Uses OpenCV DNN (YuNet) for face detection + InsightFace ArcFace
(512-dim deep embeddings) for robust matching.
"""

import json
import os
import hashlib
import time
import logging
from pathlib import Path
from typing import Optional
from dataclasses import dataclass, field, asdict

import cv2
import numpy as np

logger = logging.getLogger("lilly-faces")

# ── InsightFace (deep face embeddings) ─────────────────────────────────
try:
    from insightface.app import FaceAnalysis

    INSIGHTFACE_AVAILABLE = True
except ImportError:
    INSIGHTFACE_AVAILABLE = False
    FaceAnalysis = None  # type: ignore

# ── Paths ────────────────────────────────────────────────────────────────
FACES_DIR = Path(os.environ.get("FACES_DIR", "/app/data/faces"))
FACES_DB = FACES_DIR / "known_faces.json"
FACES_ENCODING_DIR = FACES_DIR / "encodings"


@dataclass
class KnownFace:
    name: str
    encoding: list  # 512-dim L2-normalized embedding (InsightFace ArcFace)
    photo_count: int = 0
    first_seen: str = ""
    last_seen: str = ""
    source: str = ""  # "manual", "instagram", "telegram", etc.
    notes: str = ""
    aliases: list = field(default_factory=list)


class FaceRecognitionEngine:
    """
    Robust face recognition using deep embeddings.

    Pipeline:
    1. OpenCV DNN face detector (YuNet or SSD) finds faces
    2. InsightFace ArcFace produces a 512-dim L2-normalized embedding
    3. Embedding is compared against known faces using cosine similarity (dot product)
    4. Best match above threshold returns the name

    If InsightFace is unavailable, falls back to hand-crafted 128-dim features.
    """

    def __init__(self):
        self.known_faces: dict[str, KnownFace] = {}
        self.face_detector = None
        self.face_analyzer = None  # InsightFace FaceAnalysis
        self.threshold = 0.5  # cosine similarity for 512-dim normalized embeddings
        self._init_detector()
        self._init_embedding_model()
        self._load_database()

    def _init_detector(self):
        """Initialize OpenCV DNN face detector."""
        try:
            # Try YuNet first (OpenCV 4.5.4+)
            yunet_path = str(Path(__file__).parent / "yunet.onnx")
            if not os.path.exists(yunet_path):
                # Try alternative locations
                for alt in ["/app/yunet.onnx", "yunet.onnx"]:
                    if os.path.exists(alt):
                        yunet_path = alt
                        break

            self.face_detector = cv2.FaceDetectorYN.create(
                model=yunet_path,
                config="",
                input_size=(320, 320),
                score_threshold=0.6,
                nms_threshold=0.3,
                top_k=500,
            )
            logger.info(f"Face detector: YuNet (DNN) from {yunet_path}")
        except Exception as e:
            logger.warning(f"YuNet init failed ({e}), trying Haar cascade")
            try:
                # Fallback: OpenCV Haar cascade (always available)
                cascade_path = (
                    cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
                )
                if os.path.exists(cascade_path):
                    self.face_detector = cv2.CascadeClassifier(cascade_path)
                    logger.info("Face detector: Haar cascade")
                else:
                    logger.warning("No face detector available")
                    self.face_detector = None
            except Exception as e2:
                logger.warning(f"Face detector init failed: {e2}")
                self.face_detector = None

    def _init_embedding_model(self):
        """Initialize InsightFace for deep 512-dim face embeddings."""
        if not INSIGHTFACE_AVAILABLE:
            logger.warning(
                "insightface not installed — using fallback hand-crafted encoding"
            )
            self.face_analyzer = None
            return

        try:
            self.face_analyzer = FaceAnalysis(
                name="buffalo_l",
                providers=["CPUExecutionProvider"],
            )
            self.face_analyzer.prepare(ctx_id=0, det_size=(320, 320))
            logger.info(
                "InsightFace embedding model ready (buffalo_l, 512-dim ArcFace)"
            )
        except Exception as e:
            logger.warning(f"InsightFace init failed ({e}), using fallback encoding")
            self.face_analyzer = None

    def _load_database(self):
        """Load known faces from JSON database."""
        FACES_DIR.mkdir(parents=True, exist_ok=True)
        FACES_ENCODING_DIR.mkdir(parents=True, exist_ok=True)

        expected_dim = 512 if self.face_analyzer is not None else 128

        if FACES_DB.exists():
            try:
                with open(FACES_DB) as f:
                    data = json.load(f)
                for name, info in data.get("faces", {}).items():
                    encoding = info.get("encoding", [])
                    if encoding and len(encoding) != expected_dim:
                        logger.warning(
                            f"Skipping face '{name}': expected {expected_dim}-dim encoding, "
                            f"got {len(encoding)}-dim. Re-enrollment required."
                        )
                        continue
                    self.known_faces[name] = KnownFace(
                        name=name,
                        encoding=encoding,
                        photo_count=info.get("photo_count", 0),
                        first_seen=info.get("first_seen", ""),
                        last_seen=info.get("last_seen", ""),
                        source=info.get("source", ""),
                        notes=info.get("notes", ""),
                        aliases=info.get("aliases", []),
                    )
                logger.info(f"Loaded {len(self.known_faces)} known faces from database")
            except Exception as e:
                logger.warning(f"Failed to load face database: {e}")

    def _save_database(self):
        """Save known faces to JSON database."""
        data = {"faces": {}}
        for name, face in self.known_faces.items():
            data["faces"][name] = {
                "encoding": face.encoding,
                "photo_count": face.photo_count,
                "first_seen": face.first_seen,
                "last_seen": face.last_seen,
                "source": face.source,
                "notes": face.notes,
                "aliases": face.aliases,
            }
        FACES_DB.parent.mkdir(parents=True, exist_ok=True)
        with open(FACES_DB, "w") as f:
            json.dump(data, f, indent=2)
        logger.info(f"Saved {len(self.known_faces)} known faces to database")

    def detect_faces(self, frame: np.ndarray) -> list[dict]:
        """
        Detect faces in a frame.
        Returns list of {"x": int, "y": int, "w": int, "h": int, "confidence": float}
        """
        if self.face_detector is None:
            return []

        h, w = frame.shape[:2]

        try:
            if isinstance(self.face_detector, cv2.FaceDetectorYN):
                # YuNet detector
                self.face_detector.setInputSize((w, h))
                faces = self.face_detector.detect(frame)
                results = []
                if faces[1] is not None:
                    for face in faces[1]:
                        x, y, fw, fh = face[:4].astype(int)
                        score = float(face[4])
                        results.append(
                            {
                                "x": max(0, x),
                                "y": max(0, y),
                                "w": min(fw, w - x),
                                "h": min(fh, h - y),
                                "confidence": score,
                            }
                        )
                return results
            else:
                # Haar cascade detector
                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                faces = self.face_detector.detectMultiScale(
                    gray, scaleFactor=1.1, minNeighbors=5, minSize=(30, 30)
                )
                results = []
                for x, y, fw, fh in faces:
                    results.append(
                        {
                            "x": int(x),
                            "y": int(y),
                            "w": int(fw),
                            "h": int(fh),
                            "confidence": 0.9,  # Haar doesn't give confidence
                        }
                    )
                return results
        except Exception as e:
            logger.debug(f"Face detection error: {e}")
            return []

    def encode_face(self, frame: np.ndarray, face_box: dict) -> Optional[list]:
        """
        Create a face embedding from a detected face region.
        Primary: InsightFace ArcFace (512-dim L2-normalized deep embedding).
        Fallback: hand-crafted 128-dim features if InsightFace is unavailable.

        Returns embedding vector (512 or 128 dims), or None on failure.
        """
        x, y, w, h = face_box["x"], face_box["y"], face_box["w"], face_box["h"]

        if w < 20 or h < 20:
            return None

        # ── Primary: InsightFace deep embedding ──────────────────────────
        if self.face_analyzer is not None:
            try:
                insight_faces = self.face_analyzer.get(frame)
                if insight_faces:
                    target_box = face_box
                    best_face = None
                    best_iou = 0.0
                    for f in insight_faces:
                        fx1, fy1, fx2, fy2 = f.bbox
                        candidate_box = {
                            "x": float(fx1),
                            "y": float(fy1),
                            "w": float(fx2 - fx1),
                            "h": float(fy2 - fy1),
                        }
                        iou_val = self._compute_iou(target_box, candidate_box)
                        if iou_val > best_iou:
                            best_iou = iou_val
                            best_face = f

                    if best_face is not None and best_iou > 0.3:
                        return best_face.normed_embedding.tolist()
            except Exception as e:
                logger.debug(f"InsightFace encoding failed: {e}")

        # ── Fallback: hand-crafted 128-dim features ──────────────────────
        return self._encode_face_fallback(frame, face_box)

    def _encode_face_fallback(
        self, frame: np.ndarray, face_box: dict
    ) -> Optional[list]:
        """
        Legacy hand-crafted face encoding (HSV histogram + spatial + texture).
        Used only when InsightFace is unavailable.
        """
        x, y, w, h = face_box["x"], face_box["y"], face_box["w"], face_box["h"]

        if w < 10 or h < 10:
            return None

        # Crop face region with some margin for context (hair, shoulders)
        margin_x = int(w * 0.2)
        margin_y_top = int(h * 0.3)  # more margin on top for hair
        margin_y_bot = int(h * 0.1)
        x1 = max(0, x - margin_x)
        y1 = max(0, y - margin_y_top)
        x2 = min(frame.shape[1], x + w + margin_x)
        y2 = min(frame.shape[0], y + h + margin_y_bot)
        face_crop = frame[y1:y2, x1:x2]

        if face_crop.size == 0:
            return None

        encoding = []

        # 1. HSV color histogram (64 dims)
        hsv = cv2.cvtColor(face_crop, cv2.COLOR_BGR2HSV)
        h_hist = cv2.calcHist([hsv], [0], None, [16], [0, 180])
        s_hist = cv2.calcHist([hsv], [1], None, [16], [0, 256])
        v_hist = cv2.calcHist([hsv], [2], None, [16], [0, 256])
        cv2.normalize(h_hist, h_hist)
        cv2.normalize(s_hist, s_hist)
        cv2.normalize(v_hist, v_hist)
        encoding.extend(h_hist.flatten().tolist())
        encoding.extend(s_hist.flatten().tolist())
        encoding.extend(v_hist.flatten().tolist())

        # 2. Spatial features (16 dims)
        face_inner = frame[y : y + h, x : x + w]
        if face_inner.size > 0:
            grid_size = 4
            cell_h, cell_w = h // grid_size, w // grid_size
            for gi in range(grid_size):
                for gj in range(grid_size):
                    cell = face_inner[
                        gi * cell_h : (gi + 1) * cell_h,
                        gj * cell_w : (gj + 1) * cell_w,
                    ]
                    if cell.size > 0:
                        encoding.append(float(np.mean(cell)) / 255.0)
                    else:
                        encoding.append(0.0)
        else:
            encoding.extend([0.0] * 16)

        # 3. Texture features via Laplacian variance (16 dims)
        gray = (
            cv2.cvtColor(face_crop, cv2.COLOR_BGR2GRAY)
            if len(face_crop.shape) == 3
            else face_crop
        )
        grid_size = 4
        cell_h, cell_w = gray.shape[0] // grid_size, gray.shape[1] // grid_size
        for gi in range(grid_size):
            for gj in range(grid_size):
                cell = gray[
                    gi * cell_h : (gi + 1) * cell_h,
                    gj * cell_w : (gj + 1) * cell_w,
                ]
                if cell.size > 0:
                    lap = cv2.Laplacian(cell, cv2.CV_64F)
                    encoding.append(float(np.var(lap)) / 1000.0)
                else:
                    encoding.append(0.0)

        # 4. Edge histogram (16 dims)
        edges = cv2.Canny(
            gray
            if len(face_crop.shape) == 2
            else cv2.cvtColor(face_crop, cv2.COLOR_BGR2GRAY),
            50,
            150,
        )
        grid_size = 4
        cell_h, cell_w = edges.shape[0] // grid_size, edges.shape[1] // grid_size
        for gi in range(grid_size):
            for gj in range(grid_size):
                cell = edges[
                    gi * cell_h : (gi + 1) * cell_h,
                    gj * cell_w : (gj + 1) * cell_w,
                ]
                if cell.size > 0:
                    encoding.append(float(np.mean(cell)) / 255.0)
                else:
                    encoding.append(0.0)

        # 5. Face aspect ratio + position (4 dims)
        encoding.append(w / max(h, 1))
        encoding.append(x / max(frame.shape[1], 1))
        encoding.append(y / max(frame.shape[0], 1))
        encoding.append((w * h) / max(frame.shape[0] * frame.shape[1], 1))

        # Pad or truncate to exactly 128 dims
        while len(encoding) < 128:
            encoding.append(0.0)
        encoding = encoding[:128]

        return encoding

    @staticmethod
    def _compute_iou(box1: dict, box2: dict) -> float:
        """Compute Intersection-over-Union between two bounding boxes."""
        x1 = max(box1["x"], box2["x"])
        y1 = max(box1["y"], box2["y"])
        x2 = min(box1["x"] + box1["w"], box2["x"] + box2["w"])
        y2 = min(box1["y"] + box1["h"], box2["y"] + box2["h"])
        inter = max(0.0, x2 - x1) * max(0.0, y2 - y1)
        area1 = box1["w"] * box1["h"]
        area2 = box2["w"] * box2["h"]
        return inter / (area1 + area2 - inter + 1e-6)

    def _cosine_similarity(self, a: list, b: list) -> float:
        """Compute cosine similarity between two vectors (dot product for normalized vectors)."""
        a = np.array(a, dtype=np.float32)
        b = np.array(b, dtype=np.float32)
        dot = np.dot(a, b)
        norm_a = np.linalg.norm(a)
        norm_b = np.linalg.norm(b)
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return float(dot / (norm_a * norm_b))

    def identify_face(self, frame: np.ndarray, face_box: dict) -> Optional[dict]:
        """
        Identify a face in the frame.
        Returns {"name": str, "confidence": float} or None.
        """
        encoding = self.encode_face(frame, face_box)
        if encoding is None:
            return None

        if not self.known_faces:
            return None

        best_name = None
        best_score = 0.0
        encoding_len = len(encoding)

        for name, known in self.known_faces.items():
            if not known.encoding or len(known.encoding) != encoding_len:
                continue
            score = self._cosine_similarity(encoding, known.encoding)
            if score > best_score and score >= self.threshold:
                best_score = score
                best_name = name

        if best_name:
            # Update last_seen timestamp
            self.known_faces[best_name].last_seen = time.strftime("%Y-%m-%dT%H:%M:%S")
            return {"name": best_name, "confidence": round(best_score, 3)}

        return None

    def add_known_face(
        self,
        name: str,
        frame: np.ndarray,
        face_box: dict,
        source: str = "manual",
        notes: str = "",
        aliases: list = None,
    ) -> bool:
        """
        Add a new known face to the database.
        Uses an averaged encoding if the person already exists (re-enrollment).
        """
        encoding = self.encode_face(frame, face_box)
        if encoding is None:
            return False

        now = time.strftime("%Y-%m-%dT%H:%M:%S")
        encoding_len = len(encoding)

        if name in self.known_faces:
            # Only average if dimensions match
            existing = self.known_faces[name]
            if existing.encoding and len(existing.encoding) == encoding_len:
                old = np.array(existing.encoding, dtype=np.float32)
                new = np.array(encoding, dtype=np.float32)
                count = existing.photo_count
                if count > 0:
                    alpha = 1.0 / (count + 1)
                    averaged = (1 - alpha) * old + alpha * new
                    encoding = averaged.tolist()
                existing.encoding = encoding
                existing.photo_count += 1
                existing.last_seen = now
            else:
                # Dimension mismatch — replace instead of average
                logger.warning(
                    f"Dimension mismatch for '{name}': existing {len(existing.encoding) if existing.encoding else 0}-dim, new {encoding_len}-dim. Replacing."
                )
                existing.encoding = encoding
                existing.photo_count += 1
                existing.last_seen = now
        else:
            self.known_faces[name] = KnownFace(
                name=name,
                encoding=encoding,
                photo_count=1,
                first_seen=now,
                last_seen=now,
                source=source,
                notes=notes,
                aliases=aliases or [],
            )

        self._save_database()
        logger.info(f"Added/updated known face: {name} (source={source})")
        return True

    def remove_known_face(self, name: str) -> bool:
        """Remove a known face from the database."""
        if name in self.known_faces:
            del self.known_faces[name]
            self._save_database()
            logger.info(f"Removed known face: {name}")
            return True
        return False

    def list_known_faces(self) -> list[dict]:
        """List all known faces."""
        return [asdict(f) for f in self.known_faces.values()]

    def enrich_person_detections(
        self, frame: np.ndarray, detections: list[dict]
    ) -> list[dict]:
        """
        Take YOLO detections, find "person" entries, detect faces in their region,
        and rename them if the face is recognized.

        Returns enriched detections with "name" field where possible.
        """
        if self.face_detector is None:
            return detections

        enriched = []
        for det in detections:
            if det.get("label", "").lower() != "person":
                enriched.append(det)
                continue

            # Get person bounding box in pixel coords
            h, w = frame.shape[:2]
            px1 = int(det.get("x1", det.get("x", 0)) * w)
            py1 = int(det.get("y1", det.get("y", 0)) * h)
            px2 = int(det.get("x2", (det.get("x", 0) + det.get("w", 0))) * w)
            py2 = int(det.get("y2", (det.get("y", 0) + det.get("h", 0))) * h)

            # Crop person region (upper body + head is where the face is)
            head_h = int((py2 - py1) * 0.5)  # upper half
            person_crop = frame[
                max(0, py1) : min(h, py1 + head_h), max(0, px1) : min(w, px2)
            ]

            if person_crop.size == 0:
                enriched.append(det)
                continue

            # Detect faces in the person region
            faces = self.detect_faces(person_crop)

            if not faces:
                enriched.append(det)
                continue

            # Use the largest face (most likely the person's face)
            largest = max(faces, key=lambda f: f["w"] * f["h"])

            # Adjust face coordinates back to full frame
            face_box_full = {
                "x": largest["x"] + px1,
                "y": largest["y"] + py1,
                "w": largest["w"],
                "h": largest["h"],
            }

            # Try to identify
            match = self.identify_face(frame, face_box_full)
            if match:
                new_det = det.copy()
                new_det["label"] = match["name"]
                new_det["face_confidence"] = match["confidence"]
                new_det["original_label"] = "person"
                enriched.append(new_det)

                # ── Trigger person tracker: record sighting + scan devices ──
                try:
                    from person_tracker import get_person_tracker

                    tracker = get_person_tracker()
                    tracker.record_sighting(
                        name=match["name"],
                        confidence=match["confidence"],
                    )
                except Exception:
                    pass  # non-fatal
            else:
                enriched.append(det)

        return enriched


# ── Singleton ────────────────────────────────────────────────────────────
_engine: Optional[FaceRecognitionEngine] = None


def get_face_engine() -> FaceRecognitionEngine:
    global _engine
    if _engine is None:
        _engine = FaceRecognitionEngine()
    return _engine
