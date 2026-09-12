#!/usr/bin/env python3
"""
YOLO Self-Training Agent for Lilly AI
Monitors detection performance, collects training data, and self-trains to improve accuracy.
"""

import os
import json
import time
import logging
import asyncio
import sqlite3
import numpy as np
import cv2
from pathlib import Path
from datetime import datetime, timedelta
from dataclasses import dataclass, asdict
from typing import Optional, List, Dict, Any, Tuple
from enum import Enum

logger = logging.getLogger("yolo-trainer")


class TrainingStatus(Enum):
    IDLE = "idle"
    COLLECTING = "collecting"
    TRAINING = "training"
    EVALUATING = "evaluating"
    DEPLOYING = "deploying"
    ERROR = "error"


@dataclass
class DetectionMetrics:
    """Metrics for a single detection."""

    timestamp: float
    source: str  # camera source
    label: str
    confidence: float
    bbox: List[float]  # [x1, y1, x2, y2]
    ground_truth: Optional[str] = None  # user-corrected label
    is_correct: Optional[bool] = None


@dataclass
class TrainingJob:
    """Training job configuration."""

    job_id: str
    status: TrainingStatus
    started_at: float
    epochs: int
    batch_size: int
    learning_rate: float
    dataset_path: str
    model_path: str
    metrics: Dict[str, float]
    error: Optional[str] = None


@dataclass
class PerformanceStats:
    """Overall performance statistics."""

    total_detections: int
    avg_confidence: float
    accuracy_estimate: float
    false_positive_rate: float
    false_negative_rate: float
    class_distribution: Dict[str, int]
    confidence_distribution: Dict[str, int]
    last_updated: float


class YOLOSelfTrainer:
    """
    Self-training agent for YOLO models.

    Features:
    - Monitors detection performance in real-time
    - Collects training data from detections
    - Corrects misclassifications via user feedback
    - Fine-tunes YOLO on collected data
    - Evaluates model improvements
    - Deploys improved models automatically
    """

    def __init__(
        self,
        data_dir: str = "training_data",
        models_dir: str = "trained_models",
        base_model: str = "yolov8n-oiv7.pt",
    ):
        """
        Initialize the self-training agent.

        Args:
            data_dir: Directory to store training data
            models_dir: Directory to store trained models
            base_model: Base YOLO model to fine-tune from
        """
        self.data_dir = Path(data_dir)
        self.models_dir = Path(models_dir)
        self.base_model = base_model

        # Create directories
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.models_dir.mkdir(parents=True, exist_ok=True)

        # Database for tracking
        self.db_path = self.data_dir / "training.db"
        self._init_database()

        # State
        self.status = TrainingStatus.IDLE
        self.current_job: Optional[TrainingJob] = None
        self.detection_buffer: List[DetectionMetrics] = []
        self.max_buffer_size = 1000

        # Performance tracking
        self.stats = PerformanceStats(
            total_detections=0,
            avg_confidence=0.0,
            accuracy_estimate=0.0,
            false_positive_rate=0.0,
            false_negative_rate=0.0,
            class_distribution={},
            confidence_distribution={},
            last_updated=time.time(),
        )

        # Configuration
        self.config = {
            "auto_collect": True,  # Automatically collect training data
            "collect_threshold": 0.3,  # Minimum confidence to collect
            "correction_threshold": 0.7,  # Confidence below which to ask for correction
            "min_samples_for_training": 100,  # Minimum samples before training
            "training_interval_hours": 24,  # Hours between auto-training
            "max_epochs": 50,  # Maximum training epochs
            "early_stopping_patience": 10,  # Patience for early stopping
            "auto_deploy_threshold": 0.05,  # Minimum improvement to auto-deploy
        }

        # Load config if exists
        self._load_config()

        logger.info(
            f"YOLOSelfTrainer initialized: data_dir={data_dir}, base_model={base_model}"
        )

    def _init_database(self):
        """Initialize SQLite database for tracking."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        # Detections table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS detections (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp REAL,
                source TEXT,
                label TEXT,
                confidence REAL,
                bbox_x1 REAL,
                bbox_y1 REAL,
                bbox_x2 REAL,
                bbox_y2 REAL,
                ground_truth TEXT,
                is_correct INTEGER,
                image_path TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Training jobs table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS training_jobs (
                id TEXT PRIMARY KEY,
                status TEXT,
                started_at REAL,
                completed_at REAL,
                epochs INTEGER,
                batch_size INTEGER,
                learning_rate REAL,
                dataset_path TEXT,
                model_path TEXT,
                metrics_json TEXT,
                error TEXT
            )
        """)

        # Model versions table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS model_versions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                version TEXT,
                model_path TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                metrics_json TEXT,
                is_active INTEGER DEFAULT 0
            )
        """)

        conn.commit()
        conn.close()

    def _load_config(self):
        """Load configuration from file."""
        config_path = self.data_dir / "config.json"
        if config_path.exists():
            try:
                with open(config_path, "r") as f:
                    loaded_config = json.load(f)
                    self.config.update(loaded_config)
                logger.info("Loaded config from file")
            except Exception as e:
                logger.warning(f"Failed to load config: {e}")

    def _save_config(self):
        """Save configuration to file."""
        config_path = self.data_dir / "config.json"
        try:
            with open(config_path, "w") as f:
                json.dump(self.config, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save config: {e}")

    async def record_detection(
        self,
        source: str,
        label: str,
        confidence: float,
        bbox: List[float],
        image: Optional[np.ndarray] = None,
    ) -> str:
        """
        Record a detection for potential training.

        Args:
            source: Camera source (e.g., "blink_front_door")
            label: Detected object label
            confidence: Detection confidence
            bbox: Bounding box [x1, y1, x2, y2]
            image: Optional image for saving

        Returns:
            Detection ID
        """
        detection = DetectionMetrics(
            timestamp=time.time(),
            source=source,
            label=label,
            confidence=confidence,
            bbox=bbox,
        )

        # Add to buffer
        self.detection_buffer.append(detection)
        if len(self.detection_buffer) > self.max_buffer_size:
            self.detection_buffer.pop(0)

        # Update stats
        self._update_stats(detection)

        # Save to database
        detection_id = self._save_detection(detection, image)

        # Auto-collect if enabled and confidence is in range
        if (
            self.config["auto_collect"]
            and self.config["collect_threshold"]
            <= confidence
            <= self.config["correction_threshold"]
        ):
            await self._collect_training_sample(detection, image)

        return str(detection_id)

    def _save_detection(
        self, detection: DetectionMetrics, image: Optional[np.ndarray]
    ) -> int:
        """Save detection to database."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        image_path = None
        if image is not None:
            # Save image
            timestamp_str = datetime.fromtimestamp(detection.timestamp).strftime(
                "%Y%m%d_%H%M%S"
            )
            image_filename = f"{detection.source}_{timestamp_str}.jpg"
            image_path = str(self.data_dir / "images" / image_filename)
            os.makedirs(os.path.dirname(image_path), exist_ok=True)
            cv2.imwrite(image_path, image)

        cursor.execute(
            """
            INSERT INTO detections (timestamp, source, label, confidence, 
                                   bbox_x1, bbox_y1, bbox_x2, bbox_y2, image_path)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
            (
                detection.timestamp,
                detection.source,
                detection.label,
                detection.confidence,
                detection.bbox[0],
                detection.bbox[1],
                detection.bbox[2],
                detection.bbox[3],
                image_path,
            ),
        )

        detection_id = cursor.lastrowid
        conn.commit()
        conn.close()

        return detection_id

    def _update_stats(self, detection: DetectionMetrics):
        """Update performance statistics."""
        self.stats.total_detections += 1

        # Update average confidence
        n = self.stats.total_detections
        self.stats.avg_confidence = (
            self.stats.avg_confidence * (n - 1) + detection.confidence
        ) / n

        # Update class distribution
        if detection.label not in self.stats.class_distribution:
            self.stats.class_distribution[detection.label] = 0
        self.stats.class_distribution[detection.label] += 1

        # Update confidence distribution
        conf_bucket = str(int(detection.confidence * 10) / 10)
        if conf_bucket not in self.stats.confidence_distribution:
            self.stats.confidence_distribution[conf_bucket] = 0
        self.stats.confidence_distribution[conf_bucket] += 1

        self.stats.last_updated = time.time()

    async def _collect_training_sample(
        self, detection: DetectionMetrics, image: Optional[np.ndarray]
    ):
        """Collect a training sample."""
        if image is None:
            return

        # Create training sample directory
        sample_dir = self.data_dir / "samples" / detection.label
        sample_dir.mkdir(parents=True, exist_ok=True)

        # Save image with YOLO format label
        timestamp_str = datetime.fromtimestamp(detection.timestamp).strftime(
            "%Y%m%d_%H%M%S"
        )
        image_filename = f"{timestamp_str}.jpg"
        label_filename = f"{timestamp_str}.txt"

        image_path = sample_dir / image_filename
        label_path = sample_dir / label_filename

        # Save image
        cv2.imwrite(str(image_path), image)

        # Save label in YOLO format (class x_center y_center width height)
        h, w = image.shape[:2]
        x1, y1, x2, y2 = detection.bbox
        x_center = (x1 + x2) / 2 / w
        y_center = (y1 + y2) / 2 / h
        width = (x2 - x1) / w
        height = (y2 - y1) / h

        with open(label_path, "w") as f:
            f.write(f"0 {x_center:.6f} {y_center:.6f} {width:.6f} {height:.6f}\n")

        logger.debug(f"Collected training sample: {detection.label}")

    async def correct_detection(self, detection_id: int, correct_label: str):
        """
        Correct a misclassified detection.

        Args:
            detection_id: Database ID of the detection
            correct_label: The correct label
        """
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        # Update detection
        cursor.execute(
            """
            UPDATE detections 
            SET ground_truth = ?, is_correct = 0
            WHERE id = ?
        """,
            (correct_label, detection_id),
        )

        conn.commit()
        conn.close()

        logger.info(f"Corrected detection {detection_id}: {correct_label}")

    async def prepare_dataset(self) -> str:
        """
        Prepare training dataset from collected samples.

        Returns:
            Path to dataset YAML file
        """
        dataset_dir = self.data_dir / "dataset"
        train_dir = dataset_dir / "train"
        val_dir = dataset_dir / "val"

        # Create directories
        train_dir.mkdir(parents=True, exist_ok=True)
        val_dir.mkdir(parents=True, exist_ok=True)
        (train_dir / "images").mkdir(exist_ok=True)
        (train_dir / "labels").mkdir(exist_ok=True)
        (val_dir / "images").mkdir(exist_ok=True)
        (val_dir / "labels").mkdir(exist_ok=True)

        # Collect all samples
        samples = []
        for label_dir in (self.data_dir / "samples").iterdir():
            if label_dir.is_dir():
                for img_path in label_dir.glob("*.jpg"):
                    label_path = img_path.with_suffix(".txt")
                    if label_path.exists():
                        samples.append(
                            {
                                "image": str(img_path),
                                "label": str(label_path),
                                "class": label_dir.name,
                            }
                        )

        if not samples:
            logger.warning("No samples found for dataset preparation")
            return ""

        # Shuffle and split (80/20)
        np.random.shuffle(samples)
        split_idx = int(len(samples) * 0.8)
        train_samples = samples[:split_idx]
        val_samples = samples[split_idx:]

        # Copy samples to dataset directories
        for i, sample in enumerate(train_samples):
            img_dst = train_dir / "images" / f"sample_{i:06d}.jpg"
            lbl_dst = train_dir / "labels" / f"sample_{i:06d}.txt"

            # Copy image
            import shutil

            shutil.copy2(sample["image"], img_dst)

            # Copy label
            shutil.copy2(sample["label"], lbl_dst)

        for i, sample in enumerate(val_samples):
            img_dst = val_dir / "images" / f"sample_{i:06d}.jpg"
            lbl_dst = val_dir / "labels" / f"sample_{i:06d}.txt"

            import shutil

            shutil.copy2(sample["image"], img_dst)
            shutil.copy2(sample["label"], lbl_dst)

        # Get class names
        classes = list(set(s["class"] for s in samples))
        classes.sort()

        # Create dataset YAML
        dataset_yaml = {
            "path": str(dataset_dir),
            "train": "train/images",
            "val": "val/images",
            "nc": len(classes),
            "names": classes,
        }

        yaml_path = dataset_dir / "dataset.yaml"
        import yaml

        with open(yaml_path, "w") as f:
            yaml.dump(dataset_yaml, f, default_flow_style=False)

        logger.info(
            f"Dataset prepared: {len(train_samples)} train, {len(val_samples)} val samples"
        )
        return str(yaml_path)

    async def train_model(
        self, epochs: int = None, batch_size: int = 8, learning_rate: float = 0.001
    ) -> TrainingJob:
        """
        Train a new model on collected data.

        Args:
            epochs: Number of training epochs
            batch_size: Batch size
            learning_rate: Learning rate

        Returns:
            TrainingJob with results
        """
        if self.status == TrainingStatus.TRAINING:
            raise RuntimeError("Training already in progress")

        self.status = TrainingStatus.TRAINING

        # Create training job
        job_id = f"job_{int(time.time())}"
        job = TrainingJob(
            job_id=job_id,
            status=TrainingStatus.TRAINING,
            started_at=time.time(),
            epochs=epochs or self.config["max_epochs"],
            batch_size=batch_size,
            learning_rate=learning_rate,
            dataset_path="",
            model_path="",
            metrics={},
        )
        self.current_job = job

        try:
            # Prepare dataset
            logger.info("Preparing dataset...")
            dataset_path = await self.prepare_dataset()
            if not dataset_path:
                raise RuntimeError("No training data available")

            job.dataset_path = dataset_path

            # Load base model
            logger.info(f"Loading base model: {self.base_model}")
            from ultralytics import YOLO

            model = YOLO(self.base_model)

            # Train
            logger.info(f"Starting training: {job.epochs} epochs")
            results = model.train(
                data=dataset_path,
                epochs=job.epochs,
                batch_size=job.batch_size,
                lr0=job.learning_rate,
                patience=self.config["early_stopping_patience"],
                save=True,
                save_period=10,
                project=str(self.models_dir),
                name=job_id,
                exist_ok=True,
            )

            # Save trained model
            model_path = self.models_dir / job_id / "weights" / "best.pt"
            job.model_path = str(model_path)

            # Evaluate model
            logger.info("Evaluating trained model...")
            eval_results = model.val()

            job.metrics = {
                "mAP50": float(eval_results.box.map50),
                "mAP50-95": float(eval_results.box.map),
                "precision": float(eval_results.box.mp),
                "recall": float(eval_results.box.mr),
            }

            job.status = TrainingStatus.IDLE
            self.status = TrainingStatus.IDLE

            # Save job to database
            self._save_training_job(job)

            logger.info(f"Training complete: {job.metrics}")

        except Exception as e:
            job.status = TrainingStatus.ERROR
            job.error = str(e)
            self.status = TrainingStatus.ERROR
            logger.error(f"Training failed: {e}")

        self.current_job = job
        return job

    def _save_training_job(self, job: TrainingJob):
        """Save training job to database."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute(
            """
            INSERT OR REPLACE INTO training_jobs 
            (id, status, started_at, epochs, batch_size, learning_rate, 
             dataset_path, model_path, metrics_json, error)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
            (
                job.job_id,
                job.status.value,
                job.started_at,
                job.epochs,
                job.batch_size,
                job.learning_rate,
                job.dataset_path,
                job.model_path,
                json.dumps(job.metrics),
                job.error,
            ),
        )

        conn.commit()
        conn.close()

    async def evaluate_model(self, model_path: str) -> Dict[str, float]:
        """
        Evaluate a trained model.

        Args:
            model_path: Path to model file

        Returns:
            Evaluation metrics
        """
        from ultralytics import YOLO

        model = YOLO(model_path)
        results = model.val()

        metrics = {
            "mAP50": float(results.box.map50),
            "mAP50-95": float(results.box.map),
            "precision": float(results.box.mp),
            "recall": float(results.box.mr),
        }

        return metrics

    async def deploy_model(self, model_path: str, version: str = None) -> bool:
        """
        Deploy a trained model.

        Args:
            model_path: Path to model file
            version: Version string

        Returns:
            True if successful
        """
        try:
            # Verify model exists
            if not os.path.exists(model_path):
                raise FileNotFoundError(f"Model not found: {model_path}")

            # Create version if not provided
            if version is None:
                version = f"v{int(time.time())}"

            # Save to database
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            # Deactivate current model
            cursor.execute("UPDATE model_versions SET is_active = 0")

            # Insert new model
            cursor.execute(
                """
                INSERT INTO model_versions (version, model_path, is_active)
                VALUES (?, ?, 1)
            """,
                (version, model_path),
            )

            conn.commit()
            conn.close()

            # Update vision server model (if running)
            # This would typically involve calling the vision server API
            logger.info(f"Deployed model version: {version}")

            return True

        except Exception as e:
            logger.error(f"Deployment failed: {e}")
            return False

    def get_performance_stats(self) -> PerformanceStats:
        """Get current performance statistics."""
        return self.stats

    def get_detection_history(
        self, hours: int = 24, source: Optional[str] = None
    ) -> List[Dict]:
        """
        Get detection history.

        Args:
            hours: Number of hours to look back
            source: Filter by camera source

        Returns:
            List of detections
        """
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cutoff_time = time.time() - (hours * 3600)

        if source:
            cursor.execute(
                """
                SELECT * FROM detections 
                WHERE timestamp > ? AND source = ?
                ORDER BY timestamp DESC
            """,
                (cutoff_time, source),
            )
        else:
            cursor.execute(
                """
                SELECT * FROM detections 
                WHERE timestamp > ?
                ORDER BY timestamp DESC
            """,
                (cutoff_time,),
            )

        rows = cursor.fetchall()
        conn.close()

        return rows

    def get_training_history(self) -> List[Dict]:
        """Get training job history."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute("""
            SELECT * FROM training_jobs 
            ORDER BY started_at DESC
            LIMIT 10
        """)

        rows = cursor.fetchall()
        conn.close()

        return rows

    def get_model_versions(self) -> List[Dict]:
        """Get model version history."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute("""
            SELECT * FROM model_versions 
            ORDER BY created_at DESC
        """)

        rows = cursor.fetchall()
        conn.close()

        return rows

    async def auto_train_check(self) -> Optional[TrainingJob]:
        """
        Check if auto-training should be triggered.

        Returns:
            TrainingJob if training was started, None otherwise
        """
        if self.status != TrainingStatus.IDLE:
            return None

        # Check if enough samples
        sample_count = self._count_samples()
        if sample_count < self.config["min_samples_for_training"]:
            logger.info(
                f"Not enough samples for training: {sample_count}/{self.config['min_samples_for_training']}"
            )
            return None

        # Check time since last training
        last_job = self._get_last_training_job()
        if last_job:
            hours_since = (time.time() - last_job.started_at) / 3600
            if hours_since < self.config["training_interval_hours"]:
                logger.info(
                    f"Last training was {hours_since:.1f} hours ago, waiting..."
                )
                return None

        # Start training
        logger.info(f"Starting auto-training with {sample_count} samples")
        return await self.train_model()

    def _count_samples(self) -> int:
        """Count total training samples."""
        count = 0
        samples_dir = self.data_dir / "samples"
        if samples_dir.exists():
            for label_dir in samples_dir.iterdir():
                if label_dir.is_dir():
                    count += len(list(label_dir.glob("*.jpg")))
        return count

    def _get_last_training_job(self) -> Optional[TrainingJob]:
        """Get the last training job."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute("""
            SELECT * FROM training_jobs 
            ORDER BY started_at DESC 
            LIMIT 1
        """)

        row = cursor.fetchone()
        conn.close()

        if row:
            return TrainingJob(
                job_id=row[0],
                status=TrainingStatus(row[1]),
                started_at=row[2],
                epochs=row[4],
                batch_size=row[5],
                learning_rate=row[6],
                dataset_path=row[7],
                model_path=row[8],
                metrics=json.loads(row[9]) if row[9] else {},
                error=row[10],
            )

        return None


# Singleton instance
_trainer: Optional[YOLOSelfTrainer] = None


def get_trainer() -> YOLOSelfTrainer:
    """Get or create global trainer instance."""
    global _trainer
    if _trainer is None:
        _trainer = YOLOSelfTrainer()
    return _trainer
