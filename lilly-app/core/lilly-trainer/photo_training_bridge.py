#!/usr/bin/env python3
"""
Photo Training Bridge — pulls photos from the phone gallery and feeds them
into the YOLO self-training pipeline.

Usage:
  python3 photo_training_bridge.py [--phone-url http://100.115.234.87:8099]
                                    [--max-photos 50]
                                    [--min-confidence 0.3]
                                    [--max-confidence 0.7]
                                    [--label-filter person,car,dog]

What it does:
  1. Connects to the phone sensor server (/photos/list)
  2. Downloads photos in batches
  3. Runs YOLO detection on each photo
  4. Saves detections with bounding boxes as training samples
     (images + YOLO-format label files)
  5. Tracks which photos have been imported (no duplicates)

Output goes to: training_data/samples/{label}/{timestamp}.jpg + .txt
"""

import os, sys, json, time, hashlib, argparse, logging
import urllib.request, urllib.parse
from pathlib import Path
from typing import Optional

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger("PhotoBridge")

WORKSPACE = Path(__file__).parent
PROGRESS_FILE = WORKSPACE / "training_data" / "photo_bridge_progress.json"
SAMPLES_DIR = WORKSPACE / "training_data" / "samples"

# Phone sensor server URL
DEFAULT_PHONE_URL = os.environ.get("SENSOR_SERVER_URL", "http://100.115.234.87:8190")


def _load_progress() -> dict:
    """Load import progress (which photos have been processed)."""
    if PROGRESS_FILE.exists():
        try:
            return json.loads(PROGRESS_FILE.read_text())
        except Exception:
            pass
    return {"imported": {}, "stats": {"total": 0, "samples": 0, "errors": 0}}


def _save_progress(progress: dict):
    PROGRESS_FILE.parent.mkdir(parents=True, exist_ok=True)
    PROGRESS_FILE.write_text(json.dumps(progress, indent=2))


def _photo_key(photo: dict) -> str:
    """Unique key for a photo (path + size = fingerprint)."""
    return hashlib.md5(f"{photo['path']}:{photo['size']}".encode()).hexdigest()


def _detect_objects_in_image(img_path: str) -> list[dict]:
    """Run YOLO detection on a local image file."""
    try:
        import cv2
        import numpy as np
    except ImportError:
        logger.warning("OpenCV not available — skipping detection")
        return []

    try:
        from ultralytics import YOLO
    except ImportError:
        logger.warning("Ultralytics not available — skipping detection")
        return []

    # Import the model loader from lilly_ai
    sys.path.insert(0, str(WORKSPACE))
    try:
        from lilly_ai import (
            _try_import_ultralytics,
            _YOLO_MODEL,
            _YOLO_MODEL_COCO,
            COCO_CLASSES,
        )
    except ImportError:
        pass

    frame = cv2.imread(img_path)
    if frame is None:
        return []

    detections = []

    # Try to load OIV7 model
    model_paths = [
        WORKSPACE / "yolov8n-oiv7.pt",
        Path.home() / ".lilly" / "yolov8n-oiv7.pt",
        WORKSPACE / "yolov8n.pt",
        Path.home() / ".lilly" / "yolov8n.pt",
    ]

    YOLO = None
    try:
        from ultralytics import YOLO as _YOLOCls

        YOLO = _YOLOCls
    except ImportError:
        return []

    model = None
    for p in model_paths:
        if p.exists():
            try:
                model = YOLO(str(p))
                break
            except Exception:
                continue

    if model is None:
        return []

    try:
        results = model(frame, verbose=False)
        for r in results:
            for box in r.boxes:
                x1, y1, x2, y2 = box.xyxy[0].tolist()
                cls = int(box.cls[0])
                conf = float(box.conf[0])
                label = model.names.get(cls, f"obj_{cls}")
                if conf > 0.3:
                    detections.append(
                        {
                            "label": label,
                            "confidence": conf,
                            "x1": x1,
                            "y1": y1,
                            "x2": x2,
                            "y2": y2,
                        }
                    )
    except Exception as e:
        logger.debug(f"Detection error on {img_path}: {e}")

    return detections


def _save_training_sample(
    img_path: str, detection: dict, img_width: int, img_height: int
):
    """Save a detection as a YOLO-format training sample."""
    label = detection["label"]
    label_dir = SAMPLES_DIR / label
    label_dir.mkdir(parents=True, exist_ok=True)

    timestamp = int(time.time() * 1000)
    stem = Path(img_path).stem
    sample_name = f"{stem}_{timestamp}"

    # Copy image
    import shutil

    dst_img = label_dir / f"{sample_name}.jpg"
    if not dst_img.exists():
        shutil.copy2(img_path, dst_img)

    # YOLO format label: class_id x_center y_center width height (normalized)
    x1, y1, x2, y2 = detection["x1"], detection["y1"], detection["x2"], detection["y2"]
    x_center = ((x1 + x2) / 2) / img_width
    y_center = ((y1 + y2) / 2) / img_height
    w = (x2 - x1) / img_width
    h = (y2 - y1) / img_height

    dst_lbl = label_dir / f"{sample_name}.txt"
    dst_lbl.write_text(f"0 {x_center:.6f} {y_center:.6f} {w:.6f} {h:.6f}\n")

    return str(dst_img)


def import_photos(
    phone_url: str = DEFAULT_PHONE_URL,
    max_photos: int = 50,
    min_confidence: float = 0.3,
    max_confidence: float = 0.7,
    label_filter: Optional[list[str]] = None,
):
    """Main import loop: pull photos from phone, detect, save training samples."""
    import urllib.request

    progress = _load_progress()
    imported = progress["imported"]
    stats = progress["stats"]

    logger.info(f"Fetching photo list from {phone_url}/photos/list ...")
    try:
        req = urllib.request.urlopen(
            f"{phone_url}/photos/list?limit={max_photos}", timeout=10
        )
        data = json.loads(req.read())
    except Exception as e:
        logger.error(f"Failed to fetch photo list: {e}")
        return stats

    photos = data.get("photos", [])
    logger.info(f"Found {len(photos)} photos on phone ({data.get('total', '?')} total)")

    new_count = 0
    for photo in photos:
        key = _photo_key(photo)
        if key in imported:
            continue

        name = photo["name"]
        path = photo["path"]
        logger.info(f"Processing: {name} ...")

        # Download photo
        try:
            encoded_path = urllib.parse.quote(path, safe="")
            url = f"{phone_url}/photos/file?path={encoded_path}"
            req = urllib.request.urlopen(url, timeout=15)
            img_bytes = req.read()
        except Exception as e:
            logger.warning(f"Failed to download {name}: {e}")
            stats["errors"] += 1
            continue

        # Save temp file
        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
            tmp.write(img_bytes)
            tmp_path = tmp.name

        try:
            # Detect objects
            detections = _detect_objects_in_image(tmp_path)
            if not detections:
                logger.info(f"  No detections in {name}")
                imported[key] = {"name": name, "samples": 0, "time": time.time()}
                continue

            # Filter by confidence
            detections = [
                d
                for d in detections
                if min_confidence <= d["confidence"] <= max_confidence
            ]

            # Filter by label if specified
            if label_filter:
                detections = [d for d in detections if d["label"] in label_filter]

            # Get image dimensions
            import cv2

            img = cv2.imread(tmp_path)
            if img is None:
                continue
            img_h, img_w = img.shape[:2]

            # Save training samples
            saved = 0
            for det in detections:
                _save_training_sample(tmp_path, det, img_w, img_h)
                saved += 1
                stats["samples"] += 1

            imported[key] = {"name": name, "samples": saved, "time": time.time()}
            stats["total"] += 1
            new_count += 1
            logger.info(f"  Saved {saved} training samples from {name}")

        except Exception as e:
            logger.warning(f"Error processing {name}: {e}")
            stats["errors"] += 1
        finally:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass

        # Save progress periodically
        if new_count % 5 == 0:
            progress["imported"] = imported
            progress["stats"] = stats
            _save_progress(progress)

    # Final save
    progress["imported"] = imported
    progress["stats"] = stats
    _save_progress(progress)

    logger.info(
        f"Import complete: {new_count} new photos, "
        f"{stats['samples']} total samples, {stats['errors']} errors"
    )
    return stats


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Import phone photos for YOLO training"
    )
    parser.add_argument(
        "--phone-url", default=DEFAULT_PHONE_URL, help="Phone sensor server URL"
    )
    parser.add_argument(
        "--max-photos", type=int, default=50, help="Max photos to import"
    )
    parser.add_argument(
        "--min-confidence", type=float, default=0.3, help="Min detection confidence"
    )
    parser.add_argument(
        "--max-confidence",
        type=float,
        default=0.7,
        help="Max detection confidence (target uncertain range)",
    )
    parser.add_argument(
        "--label-filter", help="Comma-separated labels to keep (e.g. person,car,dog)"
    )
    args = parser.parse_args()

    label_filter = args.label_filter.split(",") if args.label_filter else None

    import_photos(
        phone_url=args.phone_url,
        max_photos=args.max_photos,
        min_confidence=args.min_confidence,
        max_confidence=args.max_confidence,
        label_filter=label_filter,
    )
