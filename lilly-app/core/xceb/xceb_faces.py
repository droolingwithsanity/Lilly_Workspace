#!/usr/bin/env python3
"""
XCEB faces — the "file with faces" + FAISS matching tools.

Persists under XCEB_DATA/faces/:
    faces.json      ledger of every enrolled face (metadata: geo, category…)
    embeddings.npy  (N,512) float32 ArcFace vectors, aligned to faces.json
    faces.index     FAISS IndexFlatIP (cosine on L2-normed vectors)
    thumbs/<fid>.jpg

Search supports narrowing/broadening by:
    geo          country / city / region substring match (free text)
    category     social | dating | fan | news | public_record | other | all
    platform     specific site (instagram, tinder, …)

Faces can be enrolled from camera crops, uploaded images or reverse-search
reference images.  When a case resolves to a name it is auto-enrolled here so
the NEXT sighting of the same person is recognized instantly (and the name can
be surfaced to the camera overlay).
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import threading
import time
from pathlib import Path

import cv2
import numpy as np
import faiss

from xceb_core import (
    FACES_DIR,
    XCEB_BLOCKED_SOURCES,
    XCEB_SHARED,
    now_iso,
    slugify,
)

logger = logging.getLogger("xceb.faces")

EMBED_DIM = 512
CATEGORIES = ("social", "dating", "fan", "news", "public_record", "other")

_ENGINE = None
_ENGINE_LOCK = threading.Lock()

FACES_LEDGER = FACES_DIR / "faces.json"
FACES_MATRIX = FACES_DIR / "embeddings.npy"
FACES_INDEX = FACES_DIR / "faces.index"
THUMBS_DIR = FACES_DIR / "thumbs"

_match = threading.Lock()


# ── face engine (vendored SCRFD + ArcFace) ───────────────────────────────
def get_engine():
    """Lazily build the shareable ArcFace engine (512-dim)."""
    global _ENGINE
    with _ENGINE_LOCK:
        if _ENGINE is not None:
            return _ENGINE
        os.environ.setdefault("FACES_DIR", str(FACES_DIR))
        os.environ.setdefault("FACE_DB", str(FACES_DIR / "known_faces.json"))
        # point the vendored engine at the shared models
        os.environ.setdefault("FACE_MODEL_DIR", str(XCEB_SHARED / "models"))
        import face_recognition_engine as fre

        _ENGINE = fre.get_face_engine()
        if not _ENGINE.is_ready:
            logger.warning("SCRFD/ArcFace not ready — embeddings will be weak/absent")
        return _ENGINE


def embed_crop_bytes(crop_bytes: bytes):
    """Embed the largest face in a JPEG crop. Returns (embedding list|None, bbox).

    Robust for small/obscure 256px vision crops: detects at the engine's
    threshold first, then progressively relaxes the SCRFD floor down to 0.05.
    Uses the detector's own keypoints for ArcFace (avoids a flaky re-detect).
    """
    try:
        frame = cv2.imdecode(np.frombuffer(crop_bytes, np.uint8), cv2.IMREAD_COLOR)
        if frame is None:
            return None, None
        engine = get_engine()
        if engine is None or not engine.is_ready:
            return None, None

        faces = engine.detect_faces(frame)

        # relaxed fallback: tiny / distant faces in 256px crops drop below
        # normal floors — try a low-threshold SCRFD pass directly.
        if not faces:
            try:
                import face_recognition_engine as fre

                low = fre.SCRFD(str(fre.SCRFD_WEIGHT), conf_thres=0.05)
                dets, kpss = low.detect(frame, max_num=1)
                if len(dets) and kpss is not None and len(kpss):
                    d, kp = dets[0], kpss[0]
                    faces = [
                        {
                            "x": max(0, int(d[0])),
                            "y": max(0, int(d[1])),
                            "w": int(d[2] - d[0]),
                            "h": int(d[3] - d[1]),
                            "confidence": float(d[4]),
                            "kps": np.asarray(kp, dtype=np.float32).tolist(),
                        }
                    ]
            except Exception as e:
                logger.debug(f"relaxed detect fallback failed: {e}")

        if not faces:
            return None, None
        f = max(faces, key=lambda x: (x.get("w", 0) or 0) * (x.get("h", 0) or 0))
        emb = None
        kps = np.asarray(f.get("kps") or [], dtype=np.float32)
        if kps.shape == (5, 2) and engine.recognizer is not None:
            try:
                emb = engine.recognizer.get_embedding(frame, kps, normalized=True)
            except Exception as e:
                logger.debug(f"kps embed failed ({e}) — falling back to box embed")
        if emb is None:
            emb = engine.encode_face(frame, f)
        if emb is None:
            return None, f
        if hasattr(emb, "tolist"):
            emb = emb.tolist()
        return list(emb), f
    except Exception as e:
        logger.warning(f"embed failed: {e}")
        return None, None


# ── index management ─────────────────────────────────────────────────────
def _load_ledger() -> dict:
    try:
        d = json.loads(FACES_LEDGER.read_text())
        if isinstance(d.get("faces"), list):
            return d
    except Exception:
        pass
    return {"faces": [], "updated": ""}


def _save_ledger(ledger: dict):
    FACES_DIR.mkdir(parents=True, exist_ok=True)
    FACES_LEDGER.write_text(json.dumps(ledger, indent=2))


def _matrix() -> np.ndarray:
    if FACES_MATRIX.exists():
        try:
            return np.load(FACES_MATRIX)
        except Exception:
            pass
    return np.zeros((0, EMBED_DIM), dtype=np.float32)


def _save_matrix(m: np.ndarray):
    np.save(FACES_MATRIX, m.astype(np.float32))


def _index() -> faiss.Index:
    if FACES_INDEX.exists():
        try:
            return faiss.read_index(str(FACES_INDEX))
        except Exception:
            pass
    idx = faiss.IndexFlatIP(EMBED_DIM)
    return idx


def _save_index(idx: faiss.Index):
    faiss.write_index(idx, str(FACES_INDEX))


def _rebuild_file():
    """Persist ledger + matrix + faiss file atomically.

    Vectors live in the matrix file (row-aligned with ledger order); the
    ledger's "embedding" field is always None, so rebuild from the matrix.
    Callers must hold `_match` (already held by add_face/search_face).
    """
    ledger = _load_ledger()
    faces = ledger.get("faces", [])
    M = _matrix()
    if M.shape[0] != len(faces):
        # Matrix out of sync with ledger — truncate to the ledger length.
        n = len(faces)
        if n:
            M = M[:n]
        else:
            M = np.zeros((0, EMBED_DIM), dtype=np.float32)
    idx = faiss.IndexFlatIP(EMBED_DIM)
    if M.shape[0]:
        idx.add(M)
    _save_matrix(M)
    _save_index(idx)
    _save_ledger(ledger)


def add_face(
    *,
    embedding: list,
    name: str = "",
    source: str = "case",
    thumb_b64: str = "",
    geo: dict | None = None,
    category: str = "other",
    platform: str = "",
    url: str = "",
    case_id: str = "",
    confidence: float = 0.0,
) -> dict | None:
    """Enroll a face vector into the XCEB index. Returns the record or None."""
    # GUARD: phone/vision/test/seed sources are never enrollable — they are
    # not human-confirmed identities. Defense-in-depth alongside the pipeline
    # guard: even if an eager caller asks, we refuse.
    _src = (source or "").lower()
    if any(b in _src for b in XCEB_BLOCKED_SOURCES):
        logger.debug(f"enroll blocked: source '{source}' not human-confirmed")
        return None

    emb = np.asarray(embedding, dtype=np.float32).flatten()
    if emb.ndim != 1 or emb.shape[0] != EMBED_DIM:
        logger.debug(f"bad embedding dim {emb.shape}")
        return None
    norm = float(np.linalg.norm(emb))
    if norm < 1e-4:
        return None

    with _match:
        ledger = _load_ledger()
        faces = ledger["faces"]
        # de-dup: same-face re-encounters merge (cosine > 0.8)
        # Vectors live in the matrix file (row-aligned with ledger order);
        # the ledger's "embedding" field is always None, so read M.
        existing = None
        if faces:
            M = _matrix()
            if M.shape[0] != len(faces):
                _rebuild_file()
                faces = _load_ledger().get("faces", [])
                M = _matrix()
            if M.shape[0]:
                sims = M @ emb
                best_i = int(np.argmax(sims))
                if sims[best_i] > 0.80:
                    existing = faces[best_i]

        if existing is not None:
            existing["sightings"] += 1
            existing["last_seen"] = now_iso()
            if name and not existing.get("name"):
                existing["name"] = name
                existing["resolve_conf"] = confidence
            if case_id and case_id not in existing.get("case_ids", []):
                existing.setdefault("case_ids", []).append(case_id)
            if thumb_b64 and not (THUMBS_DIR / f"{existing['fid']}.jpg").exists():
                _save_thumb(existing["fid"], thumb_b64)
            _save_ledger(ledger)
            _rebuild_file()
            return existing

        fid = f"f_{int(time.time() * 100)}_{len(faces)}"
        rec = {
            "fid": fid,
            "name": name,
            "encoding": None,  # kept in matrix only
            "embedding": None,
            "source": source,
            "geo": geo or {},
            "category": (category if category in CATEGORIES else "other"),
            "platform": platform,
            "url": url,
            "case_ids": [case_id] if case_id else [],
            "confidence": round(confidence, 3),
            "first_seen": now_iso(),
            "last_seen": now_iso(),
            "sightings": 1,
        }
        faces.append(rec)
        _save_ledger(ledger)
        # matrix + index must include the new vector exactly in order
        M = _matrix()
        idx = _index()
        M2 = np.vstack([M, emb.reshape(1, -1)]) if M.shape[0] else emb.reshape(1, -1)
        idx.add(emb.reshape(1, -1))
        _save_matrix(M2)
        _save_index(idx)
        if thumb_b64:
            _save_thumb(fid, thumb_b64)
        return rec


def _save_thumb(fid: str, b64: str):
    import base64

    try:
        THUMBS_DIR.mkdir(parents=True, exist_ok=True)
        p = THUMBS_DIR / f"{fid}.jpg"
        data = base64.b64decode(b64)
        if len(data) > 300:
            p.write_bytes(data)
    except Exception:
        pass


# ── search ───────────────────────────────────────────────────────────────
def search_face(
    embedding: list,
    top_k: int = 8,
    geo: str = "",
    category: str = "all",
    platform: str = "",
    min_sim: float = 0.0,
) -> list[dict]:
    """FAISS nearest-neighbour search with geography / category narrowing.

    Returns rows with sim (0..1, cosine) + ledger metadata.
    """
    emb = np.asarray(embedding, dtype=np.float32).flatten()
    if emb.ndim != 1 or emb.shape[0] != EMBED_DIM:
        return []
    with _match:
        faces = _load_ledger().get("faces", [])
        if not faces:
            return []
        M = _matrix()
        if M.shape[0] != len(faces):
            _rebuild_file()
            faces = _load_ledger().get("faces", [])
            M = _matrix()
        idxs = list(range(len(faces)))
        if geo or (category and category != "all") or platform:
            idxs = [
                i
                for i, f in enumerate(faces)
                if _meta_match(f, geo=geo, category=category, platform=platform)
            ]
        if not idxs:
            return []
        sub = np.vstack([M[i] for i in idxs])
        sims = sub @ emb
        order = np.argsort(-sims)
        out = []
        for rank in order[: max(1, top_k)]:
            i = idxs[int(rank)]
            sim = float(sims[int(rank)])
            if sim < min_sim:
                continue
            f = faces[i]
            out.append(
                {
                    "fid": f["fid"],
                    "name": f.get("name") or "",
                    "sim": round(sim, 4),
                    "category": f.get("category"),
                    "platform": f.get("platform"),
                    "geo": f.get("geo", {}),
                    "source": f.get("source"),
                    "url": f.get("url", ""),
                    "case_ids": f.get("case_ids", []),
                    "sightings": f.get("sightings", 1),
                    "last_seen": f.get("last_seen"),
                }
            )
        return out


def _meta_match(f: dict, geo: str, category: str, platform: str) -> bool:
    if category and category != "all" and f.get("category") != category:
        return False
    if platform:
        if platform.lower() not in (f.get("platform") or "").lower():
            return False
    if geo:
        g = f.get("geo") or {}
        hay = " ".join(
            str(x)
            for x in [g.get("country"), g.get("city"), g.get("region"), g.get("raw")]
        ).lower()
        if not any(part in hay for part in geo.lower().split()):
            return False
    return True


def stats() -> dict:
    faces = _load_ledger().get("faces", [])
    by_cat: dict[str, int] = {}
    for f in faces:
        by_cat[f.get("category", "other")] = (
            by_cat.get(f.get("category", "other"), 0) + 1
        )
    return {
        "total": len(faces),
        "named": sum(1 for f in faces if f.get("name")),
        "by_category": by_cat,
        "dim": EMBED_DIM,
        "ledger": str(FACES_LEDGER),
        "matrix": str(FACES_MATRIX),
        "index": str(FACES_INDEX),
    }


def list_faces(
    geo: str = "",
    category: str = "all",
    platform: str = "",
    q: str = "",
    limit: int = 100,
) -> list[dict]:
    out = []
    ql = q.lower()
    for f in _load_ledger().get("faces", []):
        if geo or (category and category != "all") or platform:
            if not _meta_match(f, geo, category, platform):
                continue
        if ql:
            hay = " ".join(
                [
                    f.get("name") or "",
                    str(f.get("platform") or ""),
                    str(f.get("url") or ""),
                ]
            ).lower()
            if ql not in hay:
                continue
        out.append(
            {
                k: f[k]
                for k in (
                    "fid",
                    "name",
                    "category",
                    "platform",
                    "url",
                    "source",
                    "geo",
                    "first_seen",
                    "last_seen",
                    "sightings",
                    "case_ids",
                    "confidence",
                )
                if k in f
            }
        )
        if len(out) >= limit:
            break
    return out


# ── ingest from the shared vision known_faces.json (seed) ────────────────
def seed_from_vision(force: bool = False) -> dict:
    """Import named faces from the lilly-vision FAISS DB (read-only side)."""
    src = XCEB_SHARED / "faces" / "known_faces.json"
    if not src.exists():
        return {"imported": 0}
    try:
        data = json.loads(src.read_text())
    except Exception as e:
        logger.debug(f"seed read failed: {e}")
        return {"imported": 0}
    imported = 0
    already = {f["fid"]: f for f in _load_ledger().get("faces", [])}
    for name, info in (data.get("faces") or {}).items():
        if not info:
            continue
        samples = info.get("samples") or (
            [info.get("encoding")] if info.get("encoding") else []
        )
        if not samples:
            continue
        emb = samples[0]
        if len(emb) != EMBED_DIM:
            continue
        if any(
            a.get("name", "").lower() == str(name).lower() for a in already.values()
        ):
            continue
        add_face(
            embedding=emb,
            name=str(name),
            source="vision_seed",
            category="other",
            confidence=0.9,
        )
        imported += 1
    logger.info(f"seeded {imported} faces from vision DB")
    return {"imported": imported, "total": stats()["total"]}


def ensure_init():
    FACES_DIR.mkdir(parents=True, exist_ok=True)
    THUMBS_DIR.mkdir(parents=True, exist_ok=True)
    if not FACES_INDEX.exists() or not FACES_MATRIX.exists():
        with _match:
            _rebuild_file()
    if os.environ.get("XCEB_SEED_VISION", "1") == "1":
        try:
            seed_from_vision()
        except Exception as e:
            logger.debug(f"seed skip: {e}")
