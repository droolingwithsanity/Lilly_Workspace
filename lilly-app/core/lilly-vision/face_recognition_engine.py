#!/usr/bin/env python3
"""
Lilly Face Recognition Engine (SCRFD + ArcFace + FAISS)
─────────────────────────────────────────────────────────
Identifies known faces from YOLO person detections.

This engine is a CPU-friendly re-implementation of the yakhyo
face-reidentification stack (https://github.com/yakhyo/face-reidentification):

  • SCRFD   — lightweight ONNX face detector, also returns the 5 facial
              keypoints (left eye, right eye, nose, left mouth, right mouth)
  • ArcFace — w600k_mbf ONNX encoder, 112x112 5-point-aligned, 512-dim
              L2-normalised embeddings
  • FAISS   — single batched IndexFlatIP cosine-similarity search over all
              detected faces in a frame

Design notes (v3):
- Models load from ONNX at init (fast) instead of the heavy InsightFace
  FaceAnalysis bundle; weights live in FACE_MODELS_DIR and are downloaded
  lazily from the yakhyo release when missing.
- Detection runs once per frame (not per person crop); each person box is
  matched to the face(s) whose centre falls inside it, then ALL faces are
  embedded and searched in ONE FAISS call.
- The facial keypoints are attached to every person detection (normalized
  0-1) so the Android overlay can draw the points the matcher actually uses.
- The per-source temporal tracker confirms identities across consecutive
  frames and holds a confirmed name briefly, killing flicker and noise IDs.
- known_faces.json keeps the historical schema (encoding + samples) so the
  enrollment/list APIs in lilly_ai keep working unchanged; the FAISS index
  is rebuilt from it on load/save (cheap at these sizes).
"""

import json
import logging
import os
import sys
import tempfile
import threading
import time
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

try:
    import onnxruntime

    ORT_AVAILABLE = True
except ImportError:  # pragma: no cover
    ORT_AVAILABLE = False
    onnxruntime = None  # type: ignore

try:
    import faiss

    FAISS_AVAILABLE = True
except ImportError:  # pragma: no cover
    FAISS_AVAILABLE = False
    faiss = None  # type: ignore

# ── DeepFace enhancer (optional) ───────────────────────────────────────
# Lives in the shared /app/data volume so both containers use it.
DEEPFACE_LIB = Path(os.environ.get("DEEPFACE_LIB", "/app/data/deepface-lib"))
DEEPFACE_HOME = Path(os.environ.get("DEEPFACE_HOME", "/app/data/deepface"))
if str(DEEPFACE_LIB) not in sys.path:
    sys.path.insert(0, str(DEEPFACE_LIB))
os.environ.setdefault("DEEPFACE_HOME", str(DEEPFACE_HOME))
os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "3")
os.environ.setdefault("GLOG_minloglevel", "3")

DEEPFACE_ENABLED = os.environ.get("DEEPFACE_ENABLED", "1") == "1"
DEEPFACE_MODEL = os.environ.get("DEEPFACE_MODEL", "SFace")
DEEPFACE_DETECTOR = os.environ.get("DEEPFACE_DETECTOR", "mtcnn")
DEEPFACE_MIN_VERIFY = float(os.environ.get("DEEPFACE_MIN_VERIFY", "0.98"))
DEEPFACE_VERIFY_COOLDOWN = float(os.environ.get("DEEPFACE_VERIFY_COOLDOWN", "20"))
DEEPFACE_DEMOG_COOLDOWN = float(os.environ.get("DEEPFACE_DEMOG_COOLDOWN", "30"))

_df_cache = {"module": None, "failed": False}


def _face_crop(frame, bbox):
    h, w = frame.shape[:2]
    if isinstance(bbox, dict):
        x1 = int(max(0, round(float(bbox.get("x", 0)))))
        y1 = int(max(0, round(float(bbox.get("y", 0)))))
        x2 = int(min(w, x1 + round(float(bbox.get("w", 0)))))
        y2 = int(min(h, y1 + round(float(bbox.get("h", 0)))))
    else:
        x1 = int(max(0, round(float(bbox[0]))))
        y1 = int(max(0, round(float(bbox[1]))))
        x2 = int(min(w, round(float(bbox[2]))))
        y2 = int(min(h, round(float(bbox[3]))))
    return frame[y1:y2, x1:x2]


_SFACE_WEIGHT = Path("/app/data/deepface/.deepface/weights/face_recognition_sface_2021dec.onnx")
_sface_net = None


def _get_sface():
    """Load SFace ONNX model via cv2.dnn (no TF required)."""
    global _sface_net
    if _sface_net is not None:
        return _sface_net
    if not _SFACE_WEIGHT.exists():
        logger.warning("SFace weights not found at %s", _SFACE_WEIGHT)
        return None
    try:
        _sface_net = cv2.dnn.readNetFromONNX(str(_SFACE_WEIGHT))
        logger.info("SFace ONNX model loaded")
        return _sface_net
    except Exception as e:
        logger.warning("SFace load failed: %s", e)
        return None


def _sface_embed(crop):
    """Compute SFace 128-dim embedding from a face crop (numpy BGR).
    Preprocessing matches the OpenCV SFace reference: resize to 112x112,
    convert BGR->RGB, normalize to [-1,1]."""
    net = _get_sface()
    if net is None:
        return None
    img = cv2.resize(crop, (112, 112))
    blob = cv2.dnn.blobFromImage(
        img, 1.0 / 127.5, (112, 112), (127.5, 127.5, 127.5),
        swapRB=True, crop=False,
    )
    net.setInput(blob)
    emb = net.forward()  # shape (1, 128)
    emb = emb.flatten().astype(np.float64)
    norm = np.linalg.norm(emb)
    if norm > 0:
        emb = emb / norm
    return emb


logger = logging.getLogger("lilly-faces")

# ── Identity-confirm callbacks ─────────────────────────────────────────
# Fired when a FAISS name newly appears in a source (absent → present).
# lilly_ai registers face_identity.emit; the vision process registers an
# HTTP poster to /api/faces/events. Downstream handles cooldowns/alerts.
_identity_callbacks: list = []
_present_by_source: dict[str, set] = {}


def on_identity_confirmed(fn):
    """Register fn(name, confidence, source, extra_dict)."""
    if callable(fn) and fn not in _identity_callbacks:
        _identity_callbacks.append(fn)


def _fire_identity_confirmed(
    name: str, conf: float, source: str, extra: dict | None = None
):
    if not name or not _identity_callbacks:
        return
    for fn in list(_identity_callbacks):
        try:
            fn(name, conf, source, extra or {})
        except Exception:
            pass


# ── Paths ────────────────────────────────────────────────────────────────
FACES_DIR = Path(os.environ.get("FACES_DIR", "/app/data/faces"))
FACES_DB = FACES_DIR / "known_faces.json"
CROP_DIR = FACES_DIR / "crops"
# Weights are stored in the shared /app/data volume so both the main server
# and the vision server see the same model files.
SCRFD_WEIGHT = FACES_DIR.parent / "models" / "det_2.5g.onnx"
ARCFACE_WEIGHT = FACES_DIR.parent / "models" / "w600k_mbf.onnx"
YAKHYO_BASE = os.environ.get(
    "YAKHYO_BASE",
    "https://github.com/yakhyo/face-reidentification/releases/download/v0.0.1",
)

# ── Tuning ───────────────────────────────────────────────────────────────
# Person-like class labels from YOLO models (COCO: "person"; Open Images V7:
# "Man", "Woman", "Human face", ...). Heuristics only — the overlay re-checks.
PERSON_KEYWORDS = {
    "person",
    "people",
    "human",
    "human face",
    "face",
    "man",
    "woman",
    "man face",
    "women",
    "girl",
    "boy",
    "child",
    "kid",
}

DEFAULT_THRESHOLD = float(os.environ.get("FACE_THRESHOLD", "0.42"))
DET_SCORE_MIN = float(os.environ.get("SCRFD_CONF", "0.5"))  # detector floor
IDENTIFY_MIN_SIZE = 24  # px — smaller faces are too noisy to embed
CONFIRM_STREAK = 2
HOLD_FRAMES = 6
SWITCH_MARGIN = 0.12
HOLD_DIST = 90.0
SAMPLES_MAX = 10
SCRFD_INPUT = (640, 640)
SCRFD_IOU = 0.4
EMBED_DIM = 512

# Threshold used when the deep models are unavailable (legacy 128-dim
# heuristics are weak, so they must clear a much higher bar).
FALLBACK_THRESHOLD = 0.72

# ArcFace 5-point alignment reference (112x112, mirrored = False).
REFERENCE_LMK = np.array(
    [
        [38.2946, 51.6963],
        [73.5318, 51.5014],
        [56.0252, 71.7366],
        [41.5493, 92.3655],
        [70.7299, 92.2041],
    ],
    dtype=np.float32,
)


@dataclass
class KnownFace:
    name: str
    encoding: list  # primary (averaged) 512-dim embedding
    samples: list = field(default_factory=list)  # raw enrollment snapshots
    photo_count: int = 0
    first_seen: str = ""
    last_seen: str = ""
    source: str = ""  # "manual", "instagram", "telegram", etc.
    notes: str = ""
    aliases: list = field(default_factory=list)


# ── SCRFD detection model ────────────────────────────────────────────────
def _distance2bbox(points: np.ndarray, distance: np.ndarray) -> np.ndarray:
    x1 = points[:, 0] - distance[:, 0]
    y1 = points[:, 1] - distance[:, 1]
    x2 = points[:, 0] + distance[:, 2]
    y2 = points[:, 1] + distance[:, 3]
    return np.stack([x1, y1, x2, y2], axis=-1)


def _distance2kps(points: np.ndarray, distance: np.ndarray) -> np.ndarray:
    preds = []
    for i in range(0, distance.shape[1], 2):
        px = points[:, i % 2] + distance[:, i]
        py = points[:, i % 2 + 1] + distance[:, i + 1]
        preds.append(px)
        preds.append(py)
    return np.stack(preds, axis=-1)


class SCRFD:
    """SCRFD face detector (works with the yakhyo ONNX releases)."""

    def __init__(
        self,
        model_path: str,
        input_size=SCRFD_INPUT,
        conf_thres=DET_SCORE_MIN,
        iou_thres=SCRFD_IOU,
    ):
        self.input_size = input_size
        self.conf_thres = conf_thres
        self.iou_thres = iou_thres
        self.fmc = 3
        self._feat_stride_fpn = [8, 16, 32]
        self._num_anchors = 2
        self.use_kps = True
        self.mean = 127.5
        self.std = 128.0
        self.center_cache = {}
        self.session = onnxruntime.InferenceSession(
            model_path, providers=["CPUExecutionProvider"]
        )
        self.output_names = [x.name for x in self.session.get_outputs()]
        self.input_names = [x.name for x in self.session.get_inputs()]

    def forward(self, image: np.ndarray, threshold: float):
        scores_list, bboxes_list, kpss_list = [], [], []
        input_size = tuple(image.shape[0:2][::-1])
        blob = cv2.dnn.blobFromImage(
            image, 1.0 / self.std, input_size, (self.mean,) * 3, swapRB=True
        )
        outputs = self.session.run(self.output_names, {self.input_names[0]: blob})
        input_height, input_width = blob.shape[2], blob.shape[3]
        fmc = self.fmc
        for idx, stride in enumerate(self._feat_stride_fpn):
            scores = outputs[idx]
            bbox_preds = outputs[idx + fmc] * stride
            kps_preds = outputs[idx + fmc * 2] * stride if self.use_kps else None
            height, width = input_height // stride, input_width // stride
            key = (height, width, stride)
            anchor_centers = self.center_cache.get(key)
            if anchor_centers is None:
                anchor_centers = np.stack(
                    np.mgrid[:height, :width][::-1], axis=-1
                ).astype(np.float32)
                anchor_centers = (anchor_centers * stride).reshape((-1, 2))
                if self._num_anchors > 1:
                    anchor_centers = np.stack(
                        [anchor_centers] * self._num_anchors, axis=1
                    ).reshape((-1, 2))
                if len(self.center_cache) < 100:
                    self.center_cache[key] = anchor_centers
            pos_inds = np.where(scores >= threshold)[0]
            bboxes = _distance2bbox(anchor_centers, bbox_preds)
            scores_list.append(scores[pos_inds])
            bboxes_list.append(bboxes[pos_inds])
            if self.use_kps:
                kpss = _distance2kps(anchor_centers, kps_preds)
                kpss = kpss.reshape((kpss.shape[0], -1, 2))
                kpss_list.append(kpss[pos_inds])
        return scores_list, bboxes_list, kpss_list

    @staticmethod
    def _nms(dets: np.ndarray, iou_thres: float) -> list:
        x1, y1, x2, y2, scores = (
            dets[:, 0],
            dets[:, 1],
            dets[:, 2],
            dets[:, 3],
            dets[:, 4],
        )
        areas = (x2 - x1 + 1) * (y2 - y1 + 1)
        order = scores.argsort()[::-1]
        keep = []
        while order.size > 0:
            i = order[0]
            keep.append(int(i))
            xx1 = np.maximum(x1[i], x1[order[1:]])
            yy1 = np.maximum(y1[i], y1[order[1:]])
            xx2 = np.minimum(x2[i], x2[order[1:]])
            yy2 = np.minimum(y2[i], y2[order[1:]])
            w = np.maximum(0.0, xx2 - xx1 + 1)
            h = np.maximum(0.0, yy2 - yy1 + 1)
            inter = w * h
            ovr = inter / (areas[i] + areas[order[1:]] - inter)
            order = order[np.where(ovr <= iou_thres)[0] + 1]
        return keep

    def detect(self, image: np.ndarray, max_num: int = 0):
        """Returns (detections[N,5] = x1,y1,x2,y2,score, keypoints[N,5,2])."""
        width, height = self.input_size
        im_ratio = float(image.shape[0]) / image.shape[1]
        model_ratio = height / width
        if im_ratio > model_ratio:
            new_height, new_width = height, int(height / im_ratio)
        else:
            new_width, new_height = width, int(width * im_ratio)
        det_scale = float(new_height) / image.shape[0]
        resized = cv2.resize(image, (new_width, new_height))
        det_image = np.zeros((height, width, 3), dtype=np.uint8)
        det_image[:new_height, :new_width, :] = resized

        scores_list, bboxes_list, kpss_list = self.forward(det_image, self.conf_thres)
        scores = np.vstack(scores_list)
        order = scores.ravel().argsort()[::-1]
        bboxes = np.vstack(bboxes_list) / det_scale
        kpss = np.vstack(kpss_list) / det_scale if self.use_kps else None

        pre_det = np.hstack((bboxes, scores)).astype(np.float32)
        pre_det = pre_det[order, :]
        keep = self._nms(pre_det, self.iou_thres)
        det = pre_det[keep, :]
        if self.use_kps:
            kpss = kpss[order, :, :][keep, :, :]

        if 0 < max_num < det.shape[0]:
            area = (det[:, 2] - det[:, 0]) * (det[:, 3] - det[:, 1])
            image_center = np.array([image.shape[1] / 2, image.shape[0] / 2])
            centers = np.stack(
                [(det[:, 0] + det[:, 2]) / 2, (det[:, 1] + det[:, 3]) / 2], axis=-1
            )
            offsets = np.sum(np.power(centers - image_center, 2.0), axis=-1)
            values = area - offsets
            bindex = np.argsort(values)[::-1][:max_num]
            det = det[bindex, :]
            if kpss is not None:
                kpss = kpss[bindex, :]
        return det, kpss


# ── ArcFace recognition model ────────────────────────────────────────────
def _align_face(
    image: np.ndarray, landmark: np.ndarray, image_size: int = 112
) -> np.ndarray:
    ratio = float(image_size) / 112.0
    target = REFERENCE_LMK * ratio
    pts = np.asarray(landmark, dtype=np.float32)
    M, inliers = cv2.estimateAffinePartial2D(pts, target, method=cv2.RANSAC)
    if M is None or inliers is None or int(np.sum(inliers)) < 4:
        M, _ = cv2.estimateAffinePartial2D(pts, target)
    if M is None or not np.all(np.isfinite(M)):
        raise ValueError("face alignment failed (degenerate landmarks)")
    return cv2.warpAffine(image, M, (image_size, image_size), borderValue=0.0)


class ArcFace:
    """ArcFace embedding model (w600k_mbf)."""

    def __init__(self, model_path: str):
        self.input_size = (112, 112)
        self.session = onnxruntime.InferenceSession(
            model_path, providers=["CPUExecutionProvider"]
        )
        self.input_name = self.session.get_inputs()[0].name
        self.output_names = [o.name for o in self.session.get_outputs()]
        self.embedding_size = int(self.session.get_outputs()[0].shape[1])

    def get_embedding(
        self, image: np.ndarray, landmarks: np.ndarray, normalized: bool = True
    ) -> np.ndarray:
        aligned = _align_face(
            image, np.asarray(landmarks, dtype=np.float32).reshape(5, 2)
        )
        blob = cv2.dnn.blobFromImage(
            aligned, 1.0 / 127.5, self.input_size, (127.5,) * 3, swapRB=True
        )
        embedding = self.session.run(self.output_names, {self.input_name: blob})[
            0
        ].flatten()
        if normalized:
            norm = np.linalg.norm(embedding)
            if norm > 1e-10:
                embedding = embedding / norm
        return embedding


# ── FAISS face database ──────────────────────────────────────────────────
class FaceDatabase:
    """Batch cosine-similarity lookup over all enrolled samples via FAISS."""

    def __init__(self, dim: int = EMBED_DIM):
        self.dim = dim
        self.index = faiss.IndexFlatIP(dim) if FAISS_AVAILABLE else None
        self.names: list[str] = []

    def rebuild(self, faces: dict[str, KnownFace]) -> None:
        if self.index is None:
            return
        names, rows = [], []
        for name, face in faces.items():
            samples = face.samples or ([face.encoding] if face.encoding else [])
            for s in samples:
                if not s or len(s) != self.dim:
                    continue
                v = np.asarray(s, dtype=np.float32)
                v /= max(np.linalg.norm(v), 1e-10)
                rows.append(v)
                names.append(name)
        index = faiss.IndexFlatIP(self.dim)
        if rows:
            index.add(np.stack(rows))
        self.index = index
        self.names = names

    def search(
        self, embeddings: list[np.ndarray], threshold: float
    ) -> list[tuple[Optional[str], float]]:
        if not embeddings or self.index is None or self.index.ntotal == 0:
            return [(None, 0.0)] * len(embeddings)
        mat = np.stack(embeddings).astype(np.float32)
        norms = np.linalg.norm(mat, axis=1, keepdims=True)
        mat /= np.maximum(norms, 1e-10)
        similarities, indices = self.index.search(mat, 1)
        results = []
        for sim, idx in zip(similarities, indices):
            s = float(sim[0])
            i = int(idx[0])
            if s > threshold and i < len(self.names):
                results.append((self.names[i], s))
            else:
                results.append((None, s))
        return results


class FaceRecognitionEngine:
    """Robust face recognition using SCRFD + ArcFace + FAISS."""

    def __init__(self):
        self._lock = threading.RLock()
        self.known_faces: dict[str, KnownFace] = {}
        self.detector: Optional[SCRFD] = None
        self.recognizer: Optional[ArcFace] = None
        self.db = FaceDatabase(EMBED_DIM) if FAISS_AVAILABLE else None
        self.threshold = DEFAULT_THRESHOLD
        self._tracks: dict[str, list[dict]] = {}
        self._init_models()
        self._load_database()
        if self.db is not None:
            with self._lock:
                self.db.rebuild(self.known_faces)

    @property
    def is_ready(self) -> bool:
        return self.detector is not None and self.recognizer is not None

    @property
    def _embedding_dim(self) -> int:
        return EMBED_DIM if self.is_ready else 128

    # ── Model setup ──────────────────────────────────────────────────────

    def _init_models(self):
        if not ORT_AVAILABLE:
            logger.warning("onnxruntime not installed — using weak heuristic encoding.")
            return
        try:
            det_path, rec_path = _ensure_weights(SCRFD_WEIGHT, ARCFACE_WEIGHT)
            self.detector = SCRFD(str(det_path))
            self.recognizer = ArcFace(str(rec_path))
            logger.info(
                f"SCRFD + ArcFace(w600k_mbf, {self.recognizer.embedding_size}-dim) ready"
            )
        except Exception as e:
            logger.warning(
                f"Face models failed to load ({e}) — using heuristic fallback."
            )

    # ── Database ─────────────────────────────────────────────────────────

    def _load_database(self):
        FACES_DIR.mkdir(parents=True, exist_ok=True)
        dim = EXISTING_DIM = self._embedding_dim
        if FACES_DB.exists():
            try:
                with open(FACES_DB) as f:
                    data = json.load(f)
                loaded = 0
                for name, info in data.get("faces", {}).items():
                    encoding = info.get("encoding", [])
                    samples = info.get("samples") or ([encoding] if encoding else [])
                    # Keep legacy 512-dim or current-dim samples; silently drop others.
                    samples = [s for s in samples if s and len(s) == dim]
                    if not samples and encoding:
                        logger.warning(
                            f"Face '{name}' has {len(encoding)}-dim encodings but {dim}-dim "
                            f"expected — re-enrollment required."
                        )
                        continue
                    if not samples:
                        continue
                    self.known_faces[name] = KnownFace(
                        name=name,
                        encoding=samples[0],
                        samples=samples,
                        photo_count=info.get("photo_count", 0),
                        first_seen=info.get("first_seen", ""),
                        last_seen=info.get("last_seen", ""),
                        source=info.get("source", ""),
                        notes=info.get("notes", ""),
                        aliases=info.get("aliases", []),
                    )
                    loaded += 1
                logger.info(f"Loaded {loaded} known faces from database")
            except Exception as e:
                logger.warning(f"Failed to load face database: {e}")

    def _save_database(self):
        data = {"faces": {}}
        for name, face in self.known_faces.items():
            data["faces"][name] = {
                "encoding": face.encoding,
                "samples": face.samples,
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
        if self.db is not None:
            with self._lock:
                self.db.rebuild(self.known_faces)
        logger.info(f"Saved {len(self.known_faces)} known faces to database")

    # ── Face detection ───────────────────────────────────────────────────

    def detect_faces(self, frame: np.ndarray) -> list[dict]:
        """Detect faces in a frame. Returns {"x","y","w","h","confidence","kps"}."""
        if self.detector is None:
            return []
        dets, kpss = self.detector.detect(frame)
        h, w = frame.shape[:2]
        results = []
        for bbox, kps in zip(dets, kpss):
            score = float(bbox[4])
            if score < DET_SCORE_MIN:
                continue
            x1, y1, x2, y2 = bbox[:4].astype(int)
            fw, fh = x2 - x1, y2 - y1
            if fw < IDENTIFY_MIN_SIZE or fh < IDENTIFY_MIN_SIZE:
                continue
            results.append(
                {
                    "x": max(0, int(x1)),
                    "y": max(0, int(y1)),
                    "w": int(fw),
                    "h": int(fh),
                    "confidence": score,
                    "kps": np.asarray(kps, dtype=np.float32).tolist(),
                }
            )
        return results

    # ── Encoding ─────────────────────────────────────────────────────────

    def _embed_from_box(self, frame: np.ndarray, x: int, y: int, w: int, h: int):
        """Detect one face inside an expanded box, return (embedding, kps, face_box)."""
        if self.detector is None or self.recognizer is None:
            return None, None, None
        fh, fw = frame.shape[:2]
        pad_x = int(w * 0.35)
        pad_y = int(h * 0.3)
        x1 = max(0, x - pad_x)
        y1 = max(0, y - pad_y)
        x2 = min(fw, x + w + pad_x)
        y2 = min(fh, y + h + pad_y)
        crop = frame[y1:y2, x1:x2]
        if crop.size == 0:
            return None, None, None
        dets, kpss = self.detector.detect(crop, max_num=1)
        if len(dets) == 0:
            return None, None, None
        kps = np.asarray(kpss[0], dtype=np.float32) + np.array([x1, y1])
        try:
            emb = self.recognizer.get_embedding(frame, kps, normalized=True)
        except Exception as e:
            logger.debug(f"ArcFace embed failed: {e}")
            return None, None, None
        bx1, by1, bx2, by2 = dets[0][:4].astype(int)
        face_box = {
            "x": x1 + int(bx1),
            "y": y1 + int(by1),
            "w": int(bx2 - bx1),
            "h": int(by2 - by1),
        }
        return emb, kps, face_box

    def encode_face(self, frame: np.ndarray, face_box: dict) -> Optional[list]:
        """Public API: embed a face region. Returns 512-dim normed list or None."""
        if self.is_ready:
            emb, _, _ = self._embed_from_box(
                frame,
                int(face_box["x"]),
                int(face_box["y"]),
                int(face_box["w"]),
                int(face_box["h"]),
            )
            return emb.tolist() if emb is not None else None
        return self._encode_face_fallback(frame, face_box)

    def _encode_face_fallback(
        self, frame: np.ndarray, face_box: dict
    ) -> Optional[list]:
        """Legacy 128-dim heuristic — only when the deep models are unavailable."""
        x, y, w, h = face_box["x"], face_box["y"], face_box["w"], face_box["h"]
        if w < 10 or h < 10:
            return None
        margin_x, margin_y_top, margin_y_bot = int(w * 0.2), int(h * 0.3), int(h * 0.1)
        x1 = max(0, x - margin_x)
        y1 = max(0, y - margin_y_top)
        x2 = min(frame.shape[1], x + w + margin_x)
        y2 = min(frame.shape[0], y + h + margin_y_bot)
        face_crop = frame[y1:y2, x1:x2]
        if face_crop.size == 0:
            return None
        encoding = []
        hsv = cv2.cvtColor(face_crop, cv2.COLOR_BGR2HSV)
        for ch in (0, 1, 2):
            hist = cv2.calcHist([hsv], [ch], None, [16], [0, 256])
            cv2.normalize(hist, hist)
            encoding.extend(hist.flatten().tolist())
        face_inner = frame[y : y + h, x : x + w]
        if face_inner.size > 0:
            gray_inner = cv2.cvtColor(face_inner, cv2.COLOR_BGR2GRAY)
            grid = 4
            cell_h, cell_w = max(1, h // grid), max(1, w // grid)
            for gi in range(grid):
                for gj in range(grid):
                    cell = gray_inner[
                        gi * cell_h : (gi + 1) * cell_h, gj * cell_w : (gj + 1) * cell_w
                    ]
                    if cell.size > 0:
                        encoding.append(float(np.mean(cell)) / 255.0)
                    else:
                        encoding.append(0.0)
        gray = (
            cv2.cvtColor(face_crop, cv2.COLOR_BGR2GRAY)
            if len(face_crop.shape) == 3
            else face_crop
        )
        grid = 4
        cell_h, cell_w = max(1, gray.shape[0] // grid), max(1, gray.shape[1] // grid)
        for gi in range(grid):
            for gj in range(grid):
                cell = gray[
                    gi * cell_h : (gi + 1) * cell_h, gj * cell_w : (gj + 1) * cell_w
                ]
                if cell.size > 0:
                    encoding.append(float(np.var(cell)) / 1000.0)
                else:
                    encoding.append(0.0)
        encoding.append(w / max(h, 1))
        encoding.append(x / max(frame.shape[1], 1))
        encoding.append(y / max(frame.shape[0], 1))
        encoding.append((w * h) / max(frame.shape[0] * frame.shape[1], 1))
        while len(encoding) < 128:
            encoding.append(0.0)
        return encoding[:128]

    @staticmethod
    def _compute_iou(box1: dict, box2: dict) -> float:
        x1 = max(box1["x"], box2["x"])
        y1 = max(box1["y"], box2["y"])
        x2 = min(box1["x"] + box1["w"], box2["x"] + box2["w"])
        y2 = min(box1["y"] + box1["h"], box2["y"] + box2["h"])
        inter = max(0.0, x2 - x1) * max(0.0, y2 - y1)
        area1 = box1["w"] * box1["h"]
        area2 = box2["w"] * box2["h"]
        return inter / (area1 + area2 - inter + 1e-6)

    @staticmethod
    def _cosine_similarity(a: list, b: list) -> float:
        a = np.array(a, dtype=np.float32)
        b = np.array(b, dtype=np.float32)
        dot = np.dot(a, b)
        na, nb = np.linalg.norm(a), np.linalg.norm(b)
        if na == 0 or nb == 0:
            return 0.0
        return float(dot / (na * nb))

    # ── Identification ───────────────────────────────────────────────────

    def identify_face(self, frame: np.ndarray, face_box: dict) -> Optional[dict]:
        """Identify a face region. Returns {"name","confidence","score"} or None."""
        if self.db is None or self.db.index is None or self.db.index.ntotal == 0:
            return None
        if not self.is_ready:
            return self._identify_fallback(frame, face_box)
        emb, _, _ = self._embed_from_box(
            frame,
            int(face_box["x"]),
            int(face_box["y"]),
            int(face_box["w"]),
            int(face_box["h"]),
        )
        if emb is None:
            return None
        ((name, score),) = self.db.search([emb], self.threshold)
        if name:
            with self._lock:
                self.known_faces[name].last_seen = time.strftime("%Y-%m-%dT%H:%M:%S")
            return {
                "name": name,
                "confidence": round(score, 3),
                "score": round(score, 3),
            }
        return None

    def _identify_fallback(self, frame: np.ndarray, face_box: dict) -> Optional[dict]:
        encoding = self._encode_face_fallback(frame, face_box)
        if not encoding or not self.known_faces:
            return None
        best_name, best_score = None, 0.0
        for name, known in self.known_faces.items():
            samples = known.samples or ([known.encoding] if known.encoding else [])
            for sample in samples:
                if not sample or len(sample) != len(encoding):
                    continue
                score = self._cosine_similarity(encoding, sample)
                if score > best_score:
                    best_score, best_name = score, name
        if best_name and best_score >= FALLBACK_THRESHOLD:
            return {
                "name": best_name,
                "confidence": round(best_score, 3),
                "score": round(best_score, 3),
            }
        return None

    # ── Enrollment ───────────────────────────────────────────────────────

    def add_known_face(
        self,
        name: str,
        frame: np.ndarray,
        face_box: dict,
        source: str = "manual",
        notes: str = "",
        aliases: list = None,
    ) -> bool:
        encoding = self.encode_face(frame, face_box)
        if encoding is None:
            return False
        now = time.strftime("%Y-%m-%dT%H:%M:%S")
        encoding_len = len(encoding)
        with self._lock:
            if name in self.known_faces:
                face = self.known_faces[name]
                if face.samples and len(face.samples[0]) != encoding_len:
                    logger.warning(
                        f"Dimension mismatch for '{name}' (old {len(face.samples[0])}, "
                        f"new {encoding_len}) — replacing face entirely."
                    )
                    face.samples = []
                face.samples.append(encoding)
                face.samples = face.samples[-SAMPLES_MAX:]
                face.encoding = self._mean_encoding(face.samples)
                face.photo_count += 1
                face.last_seen = now
                if not face.first_seen:
                    face.first_seen = now
            else:
                face = KnownFace(
                    name=name,
                    encoding=encoding,
                    samples=[encoding],
                    photo_count=1,
                    first_seen=now,
                    last_seen=now,
                    source=source,
                    notes=notes,
                    aliases=aliases or [],
                )
                self.known_faces[name] = face
        try:
            self.deepface_seed_crop(frame, face_box, name)
        except Exception:
            pass
        self._save_database()
        logger.info(
            f"Added/updated known face: {name} (source={source}, {face.photo_count} samples)"
        )
        return True

    @staticmethod
    def _mean_encoding(samples: list) -> list:
        if not samples:
            return []
        mean = np.mean(np.array(samples, dtype=np.float32), axis=0)
        norm = np.linalg.norm(mean)
        if norm > 0:
            mean = mean / norm
        return mean.tolist()

    def auto_enroll_from_osint(self, name: str, live_crop_b64: str,
                              profile_image_url: str) -> bool:
        """Fetch OSINT profile image, SFace-verify against live crop,
        enroll into FAISS if they match. Called from a background thread."""
        import base64, urllib.request
        if not name or not live_crop_b64 or not profile_image_url:
            return False
        try:
            # Decode live crop
            live_bytes = base64.b64decode(live_crop_b64)
            live_arr = np.frombuffer(live_bytes, np.uint8)
            live_img = cv2.imdecode(live_arr, cv2.IMREAD_COLOR)
            if live_img is None:
                return False

            # Fetch profile image
            req = urllib.request.Request(
                profile_image_url,
                headers={"User-Agent": "Mozilla/5.0"},
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                prof_bytes = resp.read()
            prof_arr = np.frombuffer(prof_bytes, np.uint8)
            prof_img = cv2.imdecode(prof_arr, cv2.IMREAD_COLOR)
            if prof_img is None:
                return False

            h1, w1 = live_img.shape[:2]

            def _detect_face(img):
                gray = img[:, :, 0] if len(img.shape) == 3 else img
                dets, kpss = self.detector.detect(gray, max_num=1)
                if dets is None or len(dets) == 0:
                    return None
                bx1, by1, bx2, by2 = dets[0][:4].astype(int)
                bx1 = max(0, int(bx1)); by1 = max(0, int(by1))
                bx2 = min(img.shape[1], int(bx2)); by2 = min(img.shape[0], int(by2))
                crop = img[by1:by2, bx1:bx2]
                if crop.size == 0:
                    return None
                return crop

            live_face = _detect_face(live_img)
            prof_face = _detect_face(prof_img)
            if live_face is None or prof_face is None:
                logger.info(f"OSINT auto-enroll {name}: no face in one/both images")
                return False

            live_emb = _sface_embed(live_face)
            prof_emb = _sface_embed(prof_face)
            if live_emb is None or prof_emb is None:
                logger.info(f"OSINT auto-enroll {name}: SFace embed failed")
                return False

            sim = float(np.dot(live_emb, prof_emb))
            if sim < 0.40:
                logger.info(f"OSINT auto-enroll REJECTED {name}: sim={sim:.3f}")
                return False

            # Enroll: use the live crop with a full-frame box
            live_box = {"x": 0, "y": 0, "w": float(w1), "h": float(h1)}
            ok = self.add_known_face(
                name, live_img, live_box,
                source="osint_auto",
                notes=f"auto-enrolled via OSINT profile (sim={sim:.3f})",
            )
            logger.info(
                f"OSINT auto-enroll {name}: sim={sim:.3f} enrolled={ok}"
            )
            return ok
        except Exception as e:
            logger.debug(f"OSINT auto-enroll failed for {name}: {e}")
            return False

    def remove_known_face(self, name: str) -> bool:
        if name in self.known_faces:
            del self.known_faces[name]
            self._save_database()
            logger.info(f"Removed known face: {name}")
            return True
        return False

    def list_known_faces(self) -> list[dict]:
        faces = []
        for f in self.known_faces.values():
            d = asdict(f)
            d["sample_count"] = len(f.samples)
            faces.append(d)
        return faces

    # ── Person detection enrichment + temporal smoothing ────────────────

    def enrich_person_detections(
        self, frame: np.ndarray, detections: list[dict], source: str = "camera"
    ) -> list[dict]:
        """
        Find faces inside YOLO person boxes, embed them, batch-search FAISS,
        rename matched persons AND attach the 5 facial keypoints (normalized
        0-1) so the overlay can draw them on every person, known or not.
        """
        if self.detector is None:
            return detections

        person_idx = [
            i
            for i, d in enumerate(detections)
            if d.get("label", "").lower() in PERSON_KEYWORDS
        ]
        if not person_idx:
            return detections

        h, w = frame.shape[:2]
        dets, kpss = self.detector.detect(frame)
        if len(dets) == 0:
            self._prune_tracks(source)
            return detections

        # Map each person box → its largest face (by area) whose centre lies inside.
        person_faces: dict[int, tuple[np.ndarray, np.ndarray, int, int]] = {}
        for i in person_idx:
            d = detections[i]
            px1 = int(d.get("x1", d.get("x", 0)) * w)
            py1 = int(d.get("y1", d.get("y", 0)) * h)
            px2 = int(d.get("x2", (d.get("x", 0) + d.get("w", 0))) * w)
            py2 = int(d.get("y2", (d.get("y", 0) + d.get("h", 0))) * h)
            best = None
            best_area = 0
            for bbox, kps in zip(dets, kpss):
                cx = (bbox[0] + bbox[2]) / 2.0
                cy = (bbox[1] + bbox[3]) / 2.0
                if not (px1 <= cx <= px2 and py1 <= cy <= py2):
                    continue
                if (
                    bbox[2] - bbox[0] < IDENTIFY_MIN_SIZE
                    or bbox[3] - bbox[1] < IDENTIFY_MIN_SIZE
                ):
                    continue
                area = (bbox[2] - bbox[0]) * (bbox[3] - bbox[1])
                if area > best_area:
                    best_area, best = area, (bbox, kps)
            if best is not None:
                person_faces[i] = best

        # Embed every assigned face up-front (batched FAISS search).
        embeds: dict[int, np.ndarray] = {}
        for i, (bbox, kps) in person_faces.items():
            try:
                emb = (
                    self.recognizer.get_embedding(frame, kps, normalized=True)
                    if self.recognizer
                    else None
                )
                if emb is not None:
                    embeds[i] = emb
            except Exception as e:
                logger.debug(f"ArcFace embed failed: {e}")

        matches: dict[int, str] = {}
        confs: dict[int, float] = {}
        if self.db is not None and embeds:
            order = list(embeds)
            results = self.db.search([embeds[i] for i in order], self.threshold)
            for i, (name, score) in zip(order, results):
                if name:
                    matches[i] = name
                    confs[i] = round(score, 3)

        # Fire identity-confirmed callbacks for newly appeared names only
        # (absent → present transitions; downstream applies alert cooldowns).
        try:
            frame_names = {matches[i]: confs[i] for i in matches}
            prev = _present_by_source.get(source, set())
            for _nm, _cf in frame_names.items():
                if _nm not in prev:
                    _fire_identity_confirmed(_nm, _cf, source, {"tier": "faiss"})
            _present_by_source[source] = set(frame_names)
        except Exception:
            pass

        enriched = []
        for idx, d in enumerate(detections):
            if idx not in person_faces:
                enriched.append(d)
                continue
            bbox, kps = person_faces[idx]
            norm_kps = np.asarray(kps, dtype=np.float32)
            norm_kps[:, 0] = norm_kps[:, 0] / w
            norm_kps[:, 1] = norm_kps[:, 1] / h
            new_det = dict(d)
            new_det["kps"] = [
                [round(float(p[0]), 4), round(float(p[1]), 4)] for p in norm_kps
            ]
            if idx in matches:
                name = matches[idx]
                new_det["label"] = name
                boosted, dverified, ddist = self.deepface_second_opinion(
                    frame, bbox, name, confs[idx]
                )
                new_det["face_confidence"] = boosted
                if dverified is not None:
                    new_det["deepface_verified"] = bool(dverified)
                    new_det["deepface_verified_dist"] = ddist
                    new_det["face_source"] = "deepface" if dverified else "faiss"
                dem = self.deepface_demographics(frame, bbox, name=name)
                if dem:
                    new_det["demographics"] = dem
                    for k in ("age", "gender", "emotion", "race"):
                        if dem.get(k) is not None:
                            new_det[k] = dem[k]
                new_det["original_label"] = "person"
                try:
                    from person_tracker import get_person_tracker

                    get_person_tracker().record_sighting(
                        name=matches[idx], confidence=confs[idx]
                    )
                except Exception:
                    pass  # non-fatal
            enriched.append(new_det)

        self._prune_tracks(source)
        return enriched

    def _smooth_identity(
        self, source: str, centroid: tuple, candidate: Optional[str], score: float
    ) -> tuple[Optional[str], float]:
        """Confirm identities across frames, hold through brief misses."""
        threshold = self.threshold if self.is_ready else FALLBACK_THRESHOLD
        cx, cy = centroid
        tracks = self._tracks.setdefault(source, [])
        best_idx, best_dist = -1, HOLD_DIST
        for i, t in enumerate(tracks):
            d = float(np.hypot(t["cx"] - cx, t["cy"] - cy))
            if d < best_dist:
                best_dist, best_idx = d, i
        name, conf = None, 0.0
        if candidate and score >= threshold:
            if best_idx < 0:
                tracks.append(
                    {
                        "name": candidate,
                        "cx": cx,
                        "cy": cy,
                        "streak": 1,
                        "misses": 0,
                        "confirmed": False,
                        "conf": score,
                    }
                )
            else:
                t = tracks[best_idx]
                t["cx"], t["cy"], t["misses"] = cx, cy, 0
                if t["name"] == candidate:
                    t["streak"] += 1
                    t["conf"] = max(score, t["conf"] * 0.8)
                elif t["confirmed"]:
                    if score >= t["conf"] + SWITCH_MARGIN:
                        t["streak"] += 1
                    else:
                        t["streak"] = 1
                    if t["streak"] >= CONFIRM_STREAK:
                        t["name"], t["conf"] = candidate, score
                        t["streak"] = CONFIRM_STREAK
                else:
                    t["streak"] += 1
                if t["streak"] >= CONFIRM_STREAK:
                    if not t["confirmed"] or t["name"] == candidate:
                        t["name"] = candidate
                        t["conf"] = max(score, t.get("conf", 0))
                    t["confirmed"] = True
                    name, conf = (
                        t["name"],
                        score if t["name"] == candidate else t["conf"],
                    )
        else:
            if best_idx >= 0:
                t = tracks[best_idx]
                t["misses"] += 1
                if t["confirmed"] and t["misses"] <= HOLD_FRAMES:
                    name, conf = (
                        t["name"],
                        max(t.get("conf", threshold), threshold + 0.02),
                    )
                elif t["misses"] > HOLD_FRAMES:
                    tracks.pop(best_idx)
        return name, conf

    def _prune_tracks(self, source: str):
        tracks = self._tracks.get(source)
        if tracks is not None:
            tracks[:] = [t for t in tracks if t["misses"] <= HOLD_FRAMES * 2]



    # ── DeepFace demography + second-opinion (optional) ────────────────
    _df_cooldowns = {}
    _df_last_boost = {}  # name -> (boosted_conf, verified, dist, ts)

    def _df_throttled(self, key, cooldown):
        now = time.monotonic()
        last = self._df_cooldowns.get(key, 0.0)
        if now - last < cooldown:
            return True
        self._df_cooldowns[key] = now
        return False

    @staticmethod
    def _slug(name):
        safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in str(name))
        return safe or "face"

    def deepface_seed_crop(self, frame, bbox, name):
        """Save/refresh an enrollment crop (used as DeepFace reference)."""
        try:
            crop_dir = CROP_DIR
            crop_dir.mkdir(parents=True, exist_ok=True)
            ref = crop_dir / f"{self._slug(name)}.jpg"
            if not name or name not in self.known_faces:
                return
            crop = _face_crop(frame, bbox)
            if crop.size == 0 or crop.shape[0] < 24 or crop.shape[1] < 24:
                return
            cv2.imwrite(str(ref), crop)
            return str(ref)
        except Exception:
            return None

    def deepface_demographics(self, frame, bbox, name=None):
        """Demographics placeholder — requires TF, incompatible with
        the torch+opencv runtime."""
        return None

    def deepface_second_opinion(self, frame, bbox, name, arcface_conf):
        """Cross-check FAISS match using SFace (ONNX) against enrolled crop."""
        if not DEEPFACE_ENABLED or not name or not arcface_conf:
            return arcface_conf, None, None
        if arcface_conf >= DEEPFACE_MIN_VERIFY:
            return arcface_conf, None, None
        slug = self._slug(name)
        recent = self._df_last_boost.get(slug)
        if recent and recent[1] and (time.time() - recent[3]) < DEEPFACE_VERIFY_COOLDOWN * 3:
            sim = max(0.0, 1.0 - recent[2])
            fresh = min(0.999, DEEPFACE_MIN_VERIFY + (sim - 0.5) * 0.02)
            return round(fresh, 3), True, recent[2]
        if self._df_throttled(f"verify:{slug}", DEEPFACE_VERIFY_COOLDOWN):
            return arcface_conf, None, None
        crop_dir = CROP_DIR
        ref = crop_dir / f"{self._slug(name)}.jpg"
        if not ref.exists():
            if name in self.known_faces:
                self.deepface_seed_crop(frame, bbox, name)
            return arcface_conf, None, None
        crop = _face_crop(frame, bbox)
        if crop.size == 0 or crop.shape[0] < 24 or crop.shape[1] < 24:
            return arcface_conf, None, None
        ref_img = cv2.imread(str(ref))
        if ref_img is None:
            return arcface_conf, None, None
        try:
            vec_l = _sface_embed(crop)
            vec_r = _sface_embed(ref_img)
            if vec_l is None or vec_r is None:
                return arcface_conf, None, None
            sim = float(np.dot(vec_l, vec_r))
            # SFace cosine threshold ~0.362 (paper) — 0.5 is conservative
            verified = sim >= 0.5
            logger.info(
                "SFace second-opinion %s -> sim=%.4f verified=%s",
                name, sim, verified,
            )
            if verified:
                boosted = min(0.999, DEEPFACE_MIN_VERIFY + (sim - 0.5) * 0.02)
                self._df_last_boost[slug] = (boosted, True, round(1.0 - sim, 4), time.time())
                return round(boosted, 3), True, round(1.0 - sim, 4)
            self._df_last_boost[slug] = (arcface_conf, False, round(1.0 - sim, 4), time.time())
            return arcface_conf, False, round(1.0 - sim, 4)
        except Exception as e:
            logger.debug("SFace verify failed: %s", e)
            return arcface_conf, None, None


# ── Weight download helper ────────────────────────────────────────────────
_download_lock = threading.Lock()


def _ensure_weights(*paths: Path) -> list[Path]:
    """Download yakhyo weights lazily if missing (shared /app/data volume)."""
    os.makedirs(paths[0].parent, exist_ok=True)
    with _download_lock:
        for path in paths:
            if path.exists() and path.stat().st_size > 1000:
                continue
            url = f"{YAKHYO_BASE}/{path.name}"
            logger.info(f"Downloading {path.name} → {path} ...")
            tmp_fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".partial")
            try:
                with (
                    urllib.request.urlopen(url, timeout=300) as resp,
                    os.fdopen(tmp_fd, "wb") as f,
                ):
                    f.write(resp.read())
                os.replace(tmp, path)
            except Exception as e:
                os.unlink(tmp)
                raise RuntimeError(f"Failed to download {url}: {e}")
    return list(paths)


# ── Singleton ────────────────────────────────────────────────────────────
_engine: Optional[FaceRecognitionEngine] = None
_engine_lock = threading.Lock()


def get_face_engine() -> FaceRecognitionEngine:
    global _engine
    if _engine is None:
        with _engine_lock:
            if _engine is None:
                _engine = FaceRecognitionEngine()
    return _engine
