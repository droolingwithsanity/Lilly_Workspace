#!/usr/bin/env python3
"""
YOLO Self-Training API Endpoints
FastAPI endpoints for monitoring and controlling the YOLO self-training agent.
"""

import os
import json
import time
import logging
import asyncio
from typing import Optional, Dict, Any
from fastapi import FastAPI, Request, BackgroundTasks
from fastapi.responses import JSONResponse

logger = logging.getLogger("yolo-trainer-api")

app = FastAPI(title="YOLO Self-Training API", version="1.0.0")

# Global trainer instance
_trainer = None


def get_trainer():
    """Get or create trainer instance."""
    global _trainer
    if _trainer is None:
        from yolo_self_trainer import YOLOSelfTrainer

        _trainer = YOLOSelfTrainer()
    return _trainer


@app.get("/api/trainer/status")
async def trainer_status():
    """Get training agent status."""
    trainer = get_trainer()
    return {
        "status": trainer.status.value,
        "total_detections": trainer.stats.total_detections,
        "avg_confidence": round(trainer.stats.avg_confidence, 4),
        "sample_count": trainer._count_samples(),
        "config": trainer.config,
        "last_training": trainer.stats.last_updated,
        "progress": trainer.training_progress,
    }


@app.get("/api/trainer/stats")
async def trainer_stats():
    """Get performance statistics."""
    trainer = get_trainer()
    stats = trainer.get_performance_stats()
    return {
        "total_detections": stats.total_detections,
        "avg_confidence": round(stats.avg_confidence, 4),
        "accuracy_estimate": round(stats.accuracy_estimate, 4),
        "false_positive_rate": round(stats.false_positive_rate, 4),
        "false_negative_rate": round(stats.false_negative_rate, 4),
        "class_distribution": stats.class_distribution,
        "confidence_distribution": stats.confidence_distribution,
        "last_updated": stats.last_updated,
    }


@app.get("/api/trainer/history")
async def trainer_history(hours: int = 24, source: Optional[str] = None):
    """Get detection history."""
    trainer = get_trainer()
    history = trainer.get_detection_history(hours, source)
    return {
        "count": len(history),
        "detections": [
            {
                "id": row[0],
                "timestamp": row[1],
                "source": row[2],
                "label": row[3],
                "confidence": row[4],
                "bbox": [row[5], row[6], row[7], row[8]],
                "ground_truth": row[9],
                "is_correct": row[10],
            }
            for row in history
        ],
    }


@app.post("/api/trainer/detection")
async def record_detection(request: Request):
    """Record a detection for training.

    Body: {source, label, confidence, bbox, image?}
      image: optional base64-encoded JPEG/PNG frame. When provided (and
      auto_collect is on with conf in [collect_threshold, correction_threshold]),
      the frame is cached on disk as a real YOLO-format training sample.
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"error": "Invalid JSON"})

    trainer = get_trainer()

    # Decode optional base64 image so the trainer can cache a real sample.
    image = None
    img_b64 = body.get("image")
    if img_b64:
        try:
            import base64 as _b64
            import numpy as np
            import cv2

            raw = _b64.b64decode(img_b64)
            arr = np.frombuffer(raw, dtype=np.uint8)
            image = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        except Exception as e:
            return JSONResponse(
                status_code=400, content={"error": f"image decode: {e}"}
            )
        if image is None:
            return JSONResponse(
                status_code=400, content={"error": "image decode failed"}
            )

    detection_id = await trainer.record_detection(
        source=body.get("source", "unknown"),
        label=body.get("label", ""),
        confidence=body.get("confidence", 0.0),
        bbox=body.get("bbox", [0, 0, 0, 0]),
        image=image,
    )

    cfg = trainer.config
    collected = bool(
        image is not None
        and cfg.get("auto_collect", True)
        and cfg.get("collect_threshold", 0.3)
        <= float(body.get("confidence", 0.0))
        <= cfg.get("correction_threshold", 0.7)
    )
    return {
        "ok": True,
        "detection_id": detection_id,
        "cached": collected,
        "sample_count": trainer._count_samples(),
    }


@app.get("/api/trainer/samples")
async def trainer_samples():
    """Real sample-cache state: per-label counts on disk + readiness."""
    trainer = get_trainer()
    samples_dir = trainer.data_dir / "samples"
    labels = []
    total = 0
    newest = 0.0
    if samples_dir.exists():
        for label_dir in sorted(samples_dir.iterdir()):
            if not label_dir.is_dir():
                continue
            jpgs = sorted(label_dir.glob("*.jpg"))
            n = len(jpgs)
            if not n:
                continue
            updated = jpgs[-1].stat().st_mtime
            newest = max(newest, updated)
            total += n
            labels.append(
                {
                    "label": label_dir.name,
                    "count": n,
                    "last_sample_ts": updated,
                    "last_sample_age_s": max(0, int(time.time() - updated)),
                }
            )

    cfg = trainer.config
    min_samples = int(cfg.get("min_samples_for_training", 100) or 100)
    readiness = round(min(100, total / min_samples * 100), 1) if min_samples else 100.0
    return {
        "ok": True,
        "data_dir": str(trainer.data_dir / "samples"),
        "total": total,
        "min_samples_for_training": min_samples,
        "readiness_pct": readiness,
        "labels": labels,
        "auto_collect": bool(cfg.get("auto_collect", True)),
        "collect_threshold": cfg.get("collect_threshold", 0.3),
        "correction_threshold": cfg.get("correction_threshold", 0.7),
        "newest_sample_ts": newest or None,
    }


@app.post("/api/trainer/correct")
async def correct_detection(request: Request):
    """Correct a misclassified detection or add new training data from click-to-identify."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"error": "Invalid JSON"})

    trainer = get_trainer()

    # Handle click-to-identify format (from UI)
    if "detections" in body and "user_label" in body:
        detections = body.get("detections", [])
        user_label = body.get("user_label", "")
        click_x = body.get("click_x", 0.5)
        click_y = body.get("click_y", 0.5)
        timestamp = body.get("timestamp", time.time() * 1000)
        frame_b64 = body.get("frame_b64")

        # Decode frame image if provided (for training sample collection)
        image = None
        if frame_b64:
            try:
                import base64 as b64
                import cv2
                import numpy as np

                # Strip data URL prefix if present
                raw = frame_b64
                if raw.startswith("data:image"):
                    raw = raw.split(",", 1)[1]
                img_bytes = b64.b64decode(raw)
                nparr = np.frombuffer(img_bytes, np.uint8)
                image = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
            except Exception as e:
                logger.warning(f"Failed to decode frame_b64: {e}")

        # Record each detection with the user's label
        for det in detections:
            x1 = det.get("x1", 0)
            y1 = det.get("y1", 0)
            x2 = det.get("x2", 1)
            y2 = det.get("y2", 1)
            confidence = det.get("confidence", 1.0)

            detection_id = await trainer.record_detection(
                source="click_to_identify",
                label=user_label,
                confidence=confidence,
                bbox=[x1, y1, x2, y2],
            )

            # If image is provided, save as a training sample (user confirmed this label)
            if image is not None:
                from yolo_self_trainer import DetectionMetrics

                h, w = image.shape[:2]
                # Convert normalized bbox to pixel coordinates for sample saving
                pixel_bbox = [x1 * w, y1 * h, x2 * w, y2 * h]
                sample_det = DetectionMetrics(
                    timestamp=time.time(),
                    source="click_to_identify",
                    label=user_label,
                    confidence=confidence,
                    bbox=pixel_bbox,
                )
                await trainer._collect_training_sample(sample_det, image)

        return {
            "ok": True,
            "message": f"Saved {len(detections)} detection(s) with label '{user_label}'",
            "samples_saved": len(detections) if image is not None else 0,
        }

    # Handle original correction format
    await trainer.correct_detection(
        detection_id=body.get("detection_id"), correct_label=body.get("correct_label")
    )

    return {"ok": True}


@app.post("/api/trainer/train")
async def start_training(request: Request, background_tasks: BackgroundTasks):
    """Start a training job."""
    try:
        body = await request.json()
    except Exception:
        body = {}

    trainer = get_trainer()

    if trainer.status.value == "training":
        return {"ok": False, "error": "Training already in progress"}

    # Start training in background
    async def run_training():
        job = await trainer.train_model(
            epochs=body.get("epochs", 50),
            batch_size=body.get("batch_size", 8),
            learning_rate=body.get("learning_rate", 0.001),
        )
        # Orchestrator review of the run (best-effort, never affects training)
        try:
            from trainer.orchestrator_review import review_yolo_result

            failed = job.status.name == "ERROR" or job.status.value == "error"
            review_yolo_result(
                job="yolo_api",
                metrics={
                    **(job.metrics or {}),
                    "epochs": job.epochs,
                    "batch_size": job.batch_size,
                    "learning_rate": job.learning_rate,
                },
                model_path=job.model_path,
                llm=os.environ.get("TRAINING_LLM_REVIEW", "0") == "1",
                succeeded=not failed,
                error=job.error,
            )
        except Exception:
            pass
        return job

    background_tasks.add_task(run_training)

    return {"ok": True, "message": "Training started"}


@app.post("/api/trainer/prepare-dataset")
async def prepare_dataset():
    """Prepare training dataset from collected samples."""
    trainer = get_trainer()

    dataset_path = await trainer.prepare_dataset()

    if dataset_path:
        return {"ok": True, "dataset_path": dataset_path}
    else:
        return {"ok": False, "error": "No training samples available"}


@app.get("/api/trainer/evaluate/{model_path:path}")
async def evaluate_model(model_path: str):
    """Evaluate a trained model."""
    trainer = get_trainer()

    metrics = await trainer.evaluate_model(model_path)

    return {"ok": True, "metrics": metrics}


@app.post("/api/trainer/deploy")
async def deploy_model(request: Request):
    """Deploy a trained model."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"error": "Invalid JSON"})

    trainer = get_trainer()

    success = await trainer.deploy_model(
        model_path=body.get("model_path"), version=body.get("version")
    )

    return {"ok": success}


@app.get("/api/trainer/models")
async def list_models():
    """List model versions."""
    trainer = get_trainer()
    models = trainer.get_model_versions()

    return {
        "count": len(models),
        "models": [
            {
                "id": row[0],
                "version": row[1],
                "model_path": row[2],
                "created_at": row[3],
                "is_active": row[5],
            }
            for row in models
        ],
    }


@app.post("/api/trainer/auto-check")
async def auto_train_check():
    """Check if auto-training should be triggered."""
    trainer = get_trainer()

    job = await trainer.auto_train_check()

    if job:
        return {
            "ok": True,
            "message": "Training started",
            "job_id": job.job_id,
            "status": job.status.value,
        }
    else:
        return {
            "ok": False,
            "message": "No training needed",
            "sample_count": trainer._count_samples(),
            "status": trainer.status.value,
        }


@app.get("/api/trainer/config")
async def get_config():
    """Get training configuration."""
    trainer = get_trainer()
    return {"config": trainer.config}


@app.post("/api/trainer/import-photos")
async def import_photos(request: Request):
    """Import photos from the phone gallery for training.
    Body params:
      phone_url: str - Phone sensor server URL (default: from SENSOR_SERVER_URL env)
      max_photos: int - Max photos to import (default: 50)
      min_confidence: float - Min detection confidence (default: 0.3)
      max_confidence: float - Max detection confidence (default: 0.7)
      label_filter: list[str] - Optional label whitelist
    """
    try:
        body = await request.json()
    except Exception:
        body = {}

    phone_url = body.get(
        "phone_url", os.environ.get("SENSOR_SERVER_URL", "http://100.115.234.87:8099")
    )
    max_photos = body.get("max_photos", 50)
    min_conf = body.get("min_confidence", 0.3)
    max_conf = body.get("max_confidence", 0.7)
    label_filter = body.get("label_filter")

    import sys as _sys

    _sys.path.insert(0, os.path.dirname(__file__))
    from photo_training_bridge import import_photos as _do_import

    stats = _do_import(
        phone_url=phone_url,
        max_photos=max_photos,
        min_confidence=min_conf,
        max_confidence=max_conf,
        label_filter=label_filter,
    )

    return {
        "ok": True,
        "stats": stats,
        "message": f"Imported {stats.get('total', 0)} photos, {stats.get('samples', 0)} training samples",
    }


@app.post("/api/trainer/config")
async def update_config(request: Request):
    """Update training configuration."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"error": "Invalid JSON"})

    trainer = get_trainer()
    trainer.config.update(body)
    trainer._save_config()

    return {"ok": True, "config": trainer.config}
