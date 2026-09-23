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
    """Record a detection for training."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"error": "Invalid JSON"})

    trainer = get_trainer()

    detection_id = await trainer.record_detection(
        source=body.get("source", "unknown"),
        label=body.get("label", ""),
        confidence=body.get("confidence", 0.0),
        bbox=body.get("bbox", [0, 0, 0, 0]),
    )

    return {"ok": True, "detection_id": detection_id}


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


# ═══════════════════ INSTAGRAM SCRAPER ENDPOINTS ═══════════════════

import importlib


def get_scraper():
    """Get Instagram scraper instance."""
    try:
        from instagram_scraper import get_scraper as _get_scraper

        return _get_scraper()
    except ImportError:
        return None


@app.get("/api/scraper/status")
async def scraper_status():
    """Get Instagram scraper status."""
    scraper = get_scraper()
    if not scraper:
        return JSONResponse(
            status_code=503, content={"error": "Scraper module not available"}
        )
    return scraper.get_status()


@app.get("/api/scraper/targets")
async def scraper_targets():
    """List all scraping targets."""
    scraper = get_scraper()
    if not scraper:
        return JSONResponse(
            status_code=503, content={"error": "Scraper module not available"}
        )
    return {
        "targets": [
            t.__dict__ if hasattr(t, "__dict__") else {} for t in scraper.targets
        ],
        "total": len(scraper.targets),
        "pending": len(scraper.get_pending_targets()),
    }


@app.post("/api/scraper/targets")
async def add_target(request: Request):
    """Add a new scraping target."""
    scraper = get_scraper()
    if not scraper:
        return JSONResponse(
            status_code=503, content={"error": "Scraper module not available"}
        )
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"error": "Invalid JSON"})

    target_id = scraper.add_target(
        target_type=body.get("type", "hashtag"),
        value=body.get("value", "").strip(),
        mode=body.get("mode", "posts"),
        notes=body.get("notes", ""),
    )
    return {"ok": True, "target_id": target_id}


@app.post("/api/scraper/csv-import")
async def csv_import(request: Request):
    """Import targets from CSV text."""
    scraper = get_scraper()
    if not scraper:
        return JSONResponse(
            status_code=503, content={"error": "Scraper module not available"}
        )
    try:
        body = await request.json()
        csv_text = body.get("csv_text", "")
        count = scraper.add_targets_from_csv_text(csv_text)
        scraper.save_targets_to_csv()
        return {"ok": True, "added": count}
    except Exception as e:
        return JSONResponse(status_code=400, content={"error": str(e)})


@app.get("/api/scraper/csv-sample")
async def csv_sample():
    """Get sample targets CSV template."""
    scraper = get_scraper()
    if not scraper:
        return JSONResponse(
            status_code=503, content={"error": "Scraper module not available"}
        )
    path = scraper.create_sample_targets_csv()
    with open(path) as f:
        return {"csv": f.read()}


@app.get("/api/scraper/results")
async def scraper_results(target_id: Optional[str] = None, selected_only: bool = False):
    """List scraped results."""
    scraper = get_scraper()
    if not scraper:
        return JSONResponse(
            status_code=503, content={"error": "Scraper module not available"}
        )
    if target_id:
        results = scraper.get_results_for_target(target_id)
    elif selected_only:
        results = scraper.get_selected_results()
    else:
        results = scraper.results
    return {
        "results": [r.__dict__ if hasattr(r, "__dict__") else {} for r in results],
        "total": len(results),
    }


@app.post("/api/scraper/results/select")
async def select_results(request: Request):
    """Select results for training."""
    scraper = get_scraper()
    if not scraper:
        return JSONResponse(
            status_code=503, content={"error": "Scraper module not available"}
        )
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"error": "Invalid JSON"})

    result_ids = body.get("result_ids", [])
    label = body.get("label", "")
    count = scraper.select_results(result_ids, label)
    return {"ok": True, "selected": count}


@app.post("/api/scraper/results/deselect")
async def deselect_results(request: Request):
    """Deselect results from training."""
    scraper = get_scraper()
    if not scraper:
        return JSONResponse(
            status_code=503, content={"error": "Scraper module not available"}
        )
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"error": "Invalid JSON"})

    result_ids = body.get("result_ids", [])
    count = scraper.deselect_results(result_ids)
    return {"ok": True, "deselected": count}


@app.post("/api/scraper/scrape")
async def start_scrape(request: Request):
    """Start or resume scraping."""
    scraper = get_scraper()
    if not scraper:
        return JSONResponse(
            status_code=503, content={"error": "Scraper module not available"}
        )
    try:
        body = await request.json() if request.body else {}
        mode = body.get("mode", "both")
        max_targets = body.get("max_targets", 0)
    except:
        mode = "both"
        max_targets = 0

    scraper.reset_stop()
    scraper.load_targets_from_csv()

    # Run in background
    import asyncio

    loop = asyncio.get_event_loop()
    loop.run_in_executor(None, scraper.run_scraper, mode, max_targets)

    return {"ok": True, "status": "running", "mode": mode}


@app.post("/api/scraper/stop")
async def stop_scrape():
    """Stop the running scraper."""
    scraper = get_scraper()
    if not scraper:
        return JSONResponse(
            status_code=503, content={"error": "Scraper module not available"}
        )
    scraper.stop_scraper()
    return {"ok": True, "status": "stopping"}


@app.get("/api/scraper/stats")
async def scraper_stats():
    """Get dashboard stats."""
    scraper = get_scraper()
    if not scraper:
        return JSONResponse(
            status_code=503, content={"error": "Scraper module not available"}
        )
    return scraper.get_dashboard_stats()


@app.post("/api/scraper/export-training")
async def export_training():
    """Export selected results as training data."""
    scraper = get_scraper()
    if not scraper:
        return JSONResponse(
            status_code=503, content={"error": "Scraper module not available"}
        )
    data = scraper.export_selected_for_training()
    return {"ok": True, "training_data": data, "count": len(data)}
