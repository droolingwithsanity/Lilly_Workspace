#!/usr/bin/env python3
"""
Blink Detector for Lilly AI
Uses MediaPipe FaceLandmarker for facial landmarks and EAR (Eye Aspect Ratio) for blink detection.
Integrates with YOLO for person detection → face detection → blink detection.
"""

import time
import numpy as np
import cv2
from dataclasses import dataclass
from typing import Optional, List, Tuple, Any
import logging

logger = logging.getLogger(__name__)

# MediaPipe Eye Aspect Ratio (EAR) landmark indices
# Based on MediaPipe Face Mesh topology
LEFT_EYE_INDICES = [33, 160, 158, 133, 153, 144]
RIGHT_EYE_INDICES = [362, 385, 387, 263, 373, 380]

# EAR threshold for blink detection
EAR_THRESHOLD = 0.21

# Minimum consecutive frames for blink confirmation
MIN_BLINK_FRAMES = 2

# Blink cooldown to avoid double-counting
BLINK_COOLDOWN_FRAMES = 5


@dataclass
class BlinkResult:
    """Result of blink detection for a single face."""

    is_blinking: bool
    ear_score: float  # Eye Aspect Ratio (lower = more closed)
    blink_count: int  # Total blinks detected
    eyes_closed_ratio: float  # 0.0 (open) to 1.0 (fully closed)
    confidence: float  # Detection confidence


@dataclass
class EyeLandmarks:
    """Eye landmark coordinates."""

    left_eye: List[Tuple[float, float]]
    right_eye: List[Tuple[float, float]]


class BlinkDetector:
    """
    Blink detector using MediaPipe FaceLandmarker.

    Uses Eye Aspect Ratio (EAR) to detect blinks:
    - EAR = (||p2-p6|| + ||p3-p5||) / (2 * ||p1-p4||)
    - When eyes are open, EAR is high (~0.3)
    - When eyes are closed, EAR drops (~0.1)
    """

    def __init__(
        self,
        ear_threshold: float = EAR_THRESHOLD,
        min_blink_frames: int = MIN_BLINK_FRAMES,
        blink_cooldown: int = BLINK_COOLDOWN_FRAMES,
    ):
        """
        Initialize blink detector.

        Args:
            ear_threshold: EAR threshold below which eyes are considered closed
            min_blink_frames: Minimum consecutive frames with closed eyes for blink
            blink_cooldown: Cooldown frames between blinks to avoid double-counting
        """
        self.ear_threshold = ear_threshold
        self.min_blink_frames = min_blink_frames
        self.blink_cooldown = blink_cooldown

        # State tracking
        self.blink_counter = 0
        self.total_blinks = 0
        self.cooldown_counter = 0
        self.frame_count = 0

        # MediaPipe FaceLandmarker
        self.face_landmarker = None
        self._init_mediapipe()

        logger.info(
            f"BlinkDetector initialized: EAR threshold={ear_threshold}, "
            f"min frames={min_blink_frames}, cooldown={blink_cooldown}"
        )

    def _init_mediapipe(self):
        """Initialize MediaPipe FaceLandmarker."""
        try:
            import mediapipe as mp
            from mediapipe.tasks import python
            from mediapipe.tasks.python import vision

            # Create FaceLandmarker
            base_options = python.BaseOptions(model_asset_path="")
            options = vision.FaceLandmarkerOptions(
                base_options=base_options,
                running_mode=vision.RunningMode.VIDEO,
                num_faces=1,
                min_face_detection_confidence=0.5,
                min_tracking_confidence=0.5,
                output_face_blendshapes=True,
            )
            self.face_landmarker = vision.FaceLandmarker.create_from_options(options)
            logger.info("MediaPipe FaceLandmarker initialized successfully")

        except ImportError as e:
            logger.warning(f"MediaPipe not installed: {e}")
            logger.info("Falling back to OpenCV DNN for face detection")
            self._init_opencv_fallback()
        except Exception as e:
            logger.error(f"Failed to initialize MediaPipe: {e}")
            self._init_opencv_fallback()

    def _init_opencv_fallback(self):
        """Fallback to OpenCV DNN for face detection."""
        try:
            # Load YuNet face detector
            self.face_detector = cv2.FaceDetectorYN.create(
                model="yunet.onnx",
                config="",
                input_size=(320, 320),
                score_threshold=0.6,
                nms_threshold=0.3,
                top_k=500,
            )
            self.use_opencv = True
            logger.info("OpenCV YuNet face detector initialized")
        except Exception as e:
            logger.error(f"Failed to initialize OpenCV face detector: {e}")
            self.use_opencv = False

    def _calculate_ear(self, eye_landmarks: List[Tuple[float, float]]) -> float:
        """
        Calculate Eye Aspect Ratio (EAR).

        EAR = (||p2-p6|| + ||p3-p5||) / (2 * ||p1-p4||)

        Args:
            eye_landmarks: List of 6 (x, y) coordinates for eye landmarks
                          [p1, p2, p3, p4, p5, p6]

        Returns:
            EAR score (lower = more closed)
        """
        if len(eye_landmarks) < 6:
            return 1.0  # Default to open

        # Convert to numpy arrays
        points = np.array(eye_landmarks, dtype=np.float32)

        # Vertical distances
        p2_p6 = float(np.linalg.norm(points[1] - points[5]))
        p3_p5 = float(np.linalg.norm(points[2] - points[4]))

        # Horizontal distance
        p1_p4 = float(np.linalg.norm(points[0] - points[3]))

        # Avoid division by zero
        if p1_p4 < 1e-6:
            return 1.0

        # Calculate EAR
        ear = (p2_p6 + p3_p5) / (2.0 * p1_p4)

        return ear

    def _extract_eye_landmarks_mediapipe(
        self, face_landmarks
    ) -> Optional[EyeLandmarks]:
        """Extract eye landmarks from MediaPipe face landmarks."""
        try:
            left_eye = []
            right_eye = []

            # Extract left eye landmarks
            for idx in LEFT_EYE_INDICES:
                landmark = face_landmarks[idx]
                left_eye.append((landmark.x, landmark.y))

            # Extract right eye landmarks
            for idx in RIGHT_EYE_INDICES:
                landmark = face_landmarks[idx]
                right_eye.append((landmark.x, landmark.y))

            return EyeLandmarks(left_eye=left_eye, right_eye=right_eye)

        except Exception as e:
            logger.error(f"Failed to extract eye landmarks: {e}")
            return None

    def detect_blink_mediapipe(
        self, frame: np.ndarray, timestamp_ms: int
    ) -> Optional[BlinkResult]:
        """
        Detect blink using MediaPipe FaceLandmarker.

        Args:
            frame: BGR image (numpy array)
            timestamp_ms: Timestamp in milliseconds

        Returns:
            BlinkResult or None if no face detected
        """
        if self.face_landmarker is None:
            return None

        try:
            # Convert BGR to RGB
            rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

            # Create MediaPipe image
            import mediapipe as mp

            mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb_frame)

            # Detect face landmarks
            result = self.face_landmarker.detect_for_video(mp_image, timestamp_ms)

            if not result.face_landmarks:
                return None

            # Get first face
            face_landmarks = result.face_landmarks[0]

            # Extract eye landmarks
            eyes = self._extract_eye_landmarks_mediapipe(face_landmarks)
            if eyes is None:
                return None

            # Calculate EAR for both eyes
            left_ear = self._calculate_ear(eyes.left_eye)
            right_ear = self._calculate_ear(eyes.right_eye)

            # Average EAR
            avg_ear = (left_ear + right_ear) / 2.0

            # Determine if eyes are closed
            eyes_closed = avg_ear < self.ear_threshold

            # Update blink counter
            self.frame_count += 1

            if eyes_closed:
                self.blink_counter += 1
            else:
                if self.blink_counter >= self.min_blink_frames:
                    # Cooldown check
                    if self.cooldown_counter == 0:
                        self.total_blinks += 1
                        self.cooldown_counter = self.blink_cooldown
                        logger.debug(f"Blink detected! Total: {self.total_blinks}")

                self.blink_counter = 0

            # Update cooldown
            if self.cooldown_counter > 0:
                self.cooldown_counter -= 1

            # Calculate eyes closed ratio (0.0 = open, 1.0 = closed)
            eyes_closed_ratio = max(
                0.0, min(1.0, (self.ear_threshold - avg_ear) / self.ear_threshold)
            )

            # Get blendshapes for additional info
            confidence = 1.0
            if result.face_blendshapes:
                # Use eye blink blendshape as confidence
                for blendshape in result.face_blendshapes:
                    if "eyeBlink" in blendshape.category_name:
                        confidence = blendshape.score
                        break

            return BlinkResult(
                is_blinking=eyes_closed,
                ear_score=avg_ear,
                blink_count=self.total_blinks,
                eyes_closed_ratio=eyes_closed_ratio,
                confidence=confidence,
            )

        except Exception as e:
            logger.error(f"MediaPipe blink detection failed: {e}")
            return None

    def detect_blink_opencv(self, frame: np.ndarray) -> Optional[BlinkResult]:
        """
        Detect blink using OpenCV YuNet (fallback).
        Note: This is a simplified version without actual eye landmark detection.

        Args:
            frame: BGR image (numpy array)

        Returns:
            BlinkResult or None if no face detected
        """
        if not hasattr(self, "face_detector") or not self.use_opencv:
            return None

        try:
            # Resize frame to match detector input size
            h, w = frame.shape[:2]
            resized = cv2.resize(frame, (320, 320))

            # Detect faces
            faces = self.face_detector.detect(resized)

            if faces[1] is None or len(faces[1]) == 0:
                return None

            # Get first face and scale back to original coordinates
            face = faces[1][0]
            x, y, fw, fh = face[:4].astype(int)
            x = int(x * w / 320)
            y = int(y * h / 320)
            fw = int(fw * w / 320)
            fh = int(fh * h / 320)

            # Extract face region
            face_roi = frame[y : y + fh, x : x + fw]

            if face_roi.size == 0:
                return None

            # Simple eye detection using Haar cascades
            import os

            cascade_path = os.path.join(cv2.data.haarcascades, "haarcascade_eye.xml")
            eye_cascade = cv2.CascadeClassifier(cascade_path)
            eyes = eye_cascade.detectMultiScale(face_roi)

            # If no eyes detected, assume closed
            eyes_closed = len(eyes) == 0

            # Update blink counter
            self.frame_count += 1

            if eyes_closed:
                self.blink_counter += 1
            else:
                if self.blink_counter >= self.min_blink_frames:
                    if self.cooldown_counter == 0:
                        self.total_blinks += 1
                        self.cooldown_counter = self.blink_cooldown

                self.blink_counter = 0

            if self.cooldown_counter > 0:
                self.cooldown_counter -= 1

            # Estimate EAR from eye detection
            avg_ear = 0.1 if eyes_closed else 0.3
            eyes_closed_ratio = 0.9 if eyes_closed else 0.1

            return BlinkResult(
                is_blinking=eyes_closed,
                ear_score=avg_ear,
                blink_count=self.total_blinks,
                eyes_closed_ratio=eyes_closed_ratio,
                confidence=0.7,  # Lower confidence for OpenCV fallback
            )

        except Exception as e:
            logger.error(f"OpenCV blink detection failed: {e}")
            return None

    def detect(
        self, frame: np.ndarray, timestamp_ms: int = None
    ) -> Optional[BlinkResult]:
        """
        Detect blink in frame.

        Args:
            frame: BGR image (numpy array)
            timestamp_ms: Timestamp in milliseconds (for MediaPipe)

        Returns:
            BlinkResult or None if no face detected
        """
        if timestamp_ms is None:
            timestamp_ms = int(time.time() * 1000)

        # Try MediaPipe first
        if self.face_landmarker is not None:
            return self.detect_blink_mediapipe(frame, timestamp_ms)

        # Fallback to OpenCV
        if hasattr(self, "use_opencv") and self.use_opencv:
            return self.detect_blink_opencv(frame)

        return None

    def reset(self):
        """Reset blink counter."""
        self.blink_counter = 0
        self.total_blinks = 0
        self.cooldown_counter = 0
        self.frame_count = 0


# Global instance
_blink_detector: Optional[BlinkDetector] = None


def get_blink_detector() -> BlinkDetector:
    """Get or create global blink detector instance."""
    global _blink_detector
    if _blink_detector is None:
        _blink_detector = BlinkDetector()
    return _blink_detector


def detect_blink_in_frame(
    frame: np.ndarray, timestamp_ms: int = None
) -> Optional[BlinkResult]:
    """
    Convenience function to detect blink in a frame.

    Args:
        frame: BGR image (numpy array)
        timestamp_ms: Timestamp in milliseconds

    Returns:
        BlinkResult or None
    """
    detector = get_blink_detector()
    return detector.detect(frame, timestamp_ms)


# Demo/test function
if __name__ == "__main__":
    import sys

    # Set up logging
    logging.basicConfig(level=logging.INFO)

    # Create blink detector
    detector = BlinkDetector()

    # Test with webcam
    cap = cv2.VideoCapture(0)

    if not cap.isOpened():
        print("Cannot open webcam")
        sys.exit(1)

    print("Blink Detection Test")
    print("Press 'q' to quit")
    print("-" * 40)

    frame_count = 0
    start_time = time.time()

    while True:
        ret, frame = cap.read()
        if not ret:
            break

        frame_count += 1
        timestamp_ms = int((time.time() - start_time) * 1000)

        # Detect blink
        result = detector.detect(frame, timestamp_ms)

        if result:
            # Draw status
            status = "BLINKING" if result.is_blinking else "Eyes Open"
            color = (0, 0, 255) if result.is_blinking else (0, 255, 0)

            cv2.putText(
                frame,
                f"Status: {status}",
                (10, 30),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.7,
                color,
                2,
            )
            cv2.putText(
                frame,
                f"EAR: {result.ear_score:.3f}",
                (10, 60),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.7,
                (255, 255, 255),
                2,
            )
            cv2.putText(
                frame,
                f"Blinks: {result.blink_count}",
                (10, 90),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.7,
                (255, 255, 255),
                2,
            )
            cv2.putText(
                frame,
                f"Closed: {result.eyes_closed_ratio:.1%}",
                (10, 120),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.7,
                (255, 255, 255),
                2,
            )

        # Calculate FPS
        fps = frame_count / (time.time() - start_time)
        cv2.putText(
            frame,
            f"FPS: {fps:.1f}",
            (10, 150),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.7,
            (255, 255, 0),
            2,
        )

        cv2.imshow("Blink Detection Test", frame)

        if cv2.waitKey(1) & 0xFF == ord("q"):
            break

    cap.release()
    cv2.destroyAllWindows()

    print(
        f"\nTest complete: {detector.total_blinks} blinks detected in {frame_count} frames"
    )
