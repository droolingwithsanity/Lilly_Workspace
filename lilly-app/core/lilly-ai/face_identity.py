#!/usr/bin/env python3
"""
Lilly Face Identity Events — live "who is on camera" pipeline.

Tier 1 (FAISS enrolled match) and Tier 2 (Yandex OSINT resolve) both funnel
through here via `emit_identity_event()`:

  * per-name alert cooldown (no frame-spam) + separate daily cap
  * ring buffer of recent identity events for UI + chat
  * face-crop store so a later "remember X" can enroll into FAISS
  * repeat counter → after N sightings of an unremembered name, the alert
    suggests confirming it into the permanent database

The alert callback is registered by lilly_ai at startup
(`face_identity.init(alert_fn)`); the engine and osint modules only depend
on this file, never on lilly_ai (no import cycles, works in the vision
process too — vision POSTs to /api/faces/events instead).
"""

import logging
import os
import re
import time
from collections import deque
from pathlib import Path

logger = logging.getLogger("lilly-face-id")

FACE_ALERT_COOLDOWN = float(os.environ.get("FACE_ALERT_COOLDOWN", "900"))
FACE_ALERT_DAILY_CAP = int(os.environ.get("FACE_ALERT_DAILY_CAP", "10"))
FACE_ALERT_REPEAT_SUGGEST = int(os.environ.get("FACE_ALERT_REPEAT_SUGGEST", "3"))
FACE_IDENTITY_MAX = int(os.environ.get("FACE_IDENTITY_MAX", "50"))
FACE_CROP_DIR = Path(os.environ.get("FACE_CROP_DIR", "/app/data/face_crops"))
FACE_CROP_TTL = float(os.environ.get("FACE_CROP_TTL", "86400"))
# Familiar-face auto-learn: after this many Yandex sightings of the same
# unremembered name, enroll the stored crop into FAISS automatically.
FACE_AUTO_LEARN = os.environ.get("FACE_AUTO_LEARN", "1") == "1"
FACE_AUTO_LEARN_SIGHTINGS = int(os.environ.get("FACE_AUTO_LEARN_SIGHTINGS", "5"))

# Runtime override (Admin Dashboard toggle) — None means "follow env var".
_RUNTIME_AUTO_LEARN: bool | None = None


def set_runtime_auto_learn(enabled: bool | None) -> None:
    """Admin-dashboard toggle: override the env-gated auto-learn default."""
    global _RUNTIME_AUTO_LEARN
    _RUNTIME_AUTO_LEARN = enabled


def auto_learn_enabled() -> bool:
    if _RUNTIME_AUTO_LEARN is not None:
        return _RUNTIME_AUTO_LEARN
    return FACE_AUTO_LEARN


_events: deque = deque(maxlen=FACE_IDENTITY_MAX)
_last_alert: dict[str, float] = {}
_sightings: dict[str, int] = {}
_alert_day: str = ""
_alerts_today: int = 0
_alert_fn = None


def init(alert_fn):
    """Register the alert sender (lilly_ai: phone notify + web chat)."""
    global _alert_fn
    _alert_fn = alert_fn


def _norm(name: str) -> str:
    return re.sub(r"\s+", " ", (name or "").strip().lower())


def save_crop(face_id: str, crop_b64: str) -> bool:
    """Persist a face crop for later confirm-enroll. Returns True if saved."""
    if not crop_b64 or not face_id:
        return False
    try:
        import base64

        FACE_CROP_DIR.mkdir(parents=True, exist_ok=True)
        safe = re.sub(r"[^A-Za-z0-9_-]", "_", face_id)[:40]
        (FACE_CROP_DIR / f"{safe}.jpg").write_bytes(base64.b64decode(crop_b64))
        try:
            now = time.time()
            for f in FACE_CROP_DIR.glob("*.jpg"):
                try:
                    if now - f.stat().st_mtime > FACE_CROP_TTL:
                        f.unlink()
                except Exception:
                    pass
        except Exception:
            pass
        return True
    except Exception as e:
        logger.debug(f"crop save failed: {e}")
        return False


def get_crop(face_id: str) -> bytes | None:
    try:
        safe = re.sub(r"[^A-Za-z0-9_-]", "_", (face_id or ""))[:40]
        p = FACE_CROP_DIR / f"{safe}.jpg"
        if p.exists() and p.stat().st_size > 500:
            return p.read_bytes()
    except Exception:
        pass
    return None


def _is_known_face(name: str) -> bool:
    """True if FAISS already has this name (no need to auto-learn)."""
    try:
        from face_recognition_engine import get_face_engine

        known = get_face_engine().known_faces or {}
        want = _norm(name)
        return any(_norm(n) == want for n in known.keys())
    except Exception:
        return False


def _auto_enroll(event: dict) -> bool:
    """Enroll the event's stored crop into FAISS as a familiar face."""
    try:
        crop = get_crop(event.get("face_id", ""))
        if not crop:
            return False
        import cv2 as _cv2

        import numpy as _np

        from face_recognition_engine import get_face_engine

        frame = _cv2.imdecode(_np.frombuffer(crop, dtype=_np.uint8), _cv2.IMREAD_COLOR)
        if frame is None:
            return False
        engine = get_face_engine()
        faces = engine.detect_faces(frame)
        box = (
            max(faces, key=lambda f: f["w"] * f["h"])
            if faces
            else {"x": 0, "y": 0, "w": int(frame.shape[1]), "h": int(frame.shape[0])}
        )
        ok = engine.add_known_face(event["name"], frame, box, source="auto-learned")
        if ok:
            logger.info(f"Auto-learned familiar face: {event['name']}")
        return bool(ok)
    except Exception as e:
        logger.debug(f"auto-enroll failed: {e}")
        return False


def _cap_allows() -> bool:
    global _alert_day, _alerts_today
    today = time.strftime("%Y-%m-%d")
    if _alert_day != today:
        _alert_day, _alerts_today = today, 0
    if FACE_ALERT_DAILY_CAP > 0 and _alerts_today >= FACE_ALERT_DAILY_CAP:
        return False
    _alerts_today += 1
    return True


def emit_identity_event(
    name: str,
    source: str = "faiss",
    confidence: float = 0.0,
    face_id: str = "",
    social_accounts: list | None = None,
    sources: list | None = None,
    images: list | None = None,
    crop_b64: str = "",
    geo: dict | None = None,
) -> dict:
    """Record an identity sighting; alert if cooldown/cap allow.

    Returns the event dict (with alerted=True/False).
    """
    now = time.time()
    key = _norm(name)
    if not key:
        return {"alerted": False, "error": "empty name"}
    if crop_b64 and face_id:
        save_crop(face_id, crop_b64)

    _sightings[key] = _sightings.get(key, 0) + 1
    event = {
        "id": f"{key}-{int(now)}",
        "name": name.strip(),
        "source": source,
        "confidence": round(float(confidence or 0), 3),
        "face_id": face_id or "",
        "social_accounts": list(social_accounts or [])[:8],
        "sources": list(sources or [])[:10],
        "images": list(images or [])[:8],
        "geo": geo or {},
        "sightings": _sightings[key],
        "remembered": False,
        "auto_learned": False,
        "ts": now,
        "alerted": False,
    }
    _events.appendleft(event)

    # Familiar-face auto-learn: repeated Yandex ID + stored crop → FAISS.
    force_alert = False
    if (
        auto_learn_enabled()
        and source.startswith("osint")
        and _sightings[key] >= FACE_AUTO_LEARN_SIGHTINGS
        and not _is_known_face(name.strip())
    ):
        if _auto_enroll(event):
            event["auto_learned"] = True
            event["remembered"] = True
            force_alert = True

    last = _last_alert.get(key, 0)
    if not force_alert and now - last < FACE_ALERT_COOLDOWN:
        return event
    if not _cap_allows():
        logger.debug(f"face alert capped for {name}")
        return event
    _last_alert[key] = now
    event["alerted"] = True

    if _alert_fn is not None:
        try:
            import asyncio

            res = _alert_fn(event)
            if asyncio.iscoroutine(res):
                try:
                    loop = asyncio.get_running_loop()
                    loop.create_task(res)
                except RuntimeError:
                    asyncio.run(res)
        except Exception as e:
            logger.debug(f"face alert fn failed: {e}")
    return event


def mark_remembered(name: str):
    key = _norm(name)
    for e in _events:
        if _norm(e.get("name")) == key:
            e["remembered"] = True


def recent_events(limit: int = 20) -> list[dict]:
    return list(_events)[: max(1, min(limit, FACE_IDENTITY_MAX))]


def find_event(name_or_id: str) -> dict | None:
    q = _norm(name_or_id)
    for e in _events:
        if (
            _norm(e.get("name")) == q
            or _norm(e.get("face_id")) == q
            or e.get("id") == name_or_id
        ):
            return e
    return None
