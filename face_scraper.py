#!/usr/bin/env python3
"""
Lilly Face Scraper — Social Media Face Enrollment
──────────────────────────────────────────────────
Scrapes photos from social media profiles, detects faces,
and enrolls them into the known-faces database.

Usage:
  python3 face_scraper.py --instagram <username> --name "John"
  python3 face_scraper.py --url <photo_url> --name "Sarah"
  python3 face_scraper.py --dir /path/to/photos --name "Mom"
  python3 face_scraper.py --webcam --name "New Person"   # live enrollment
"""

import argparse
import json
import os
import sys
import time
import hashlib
import logging
import urllib.request
import urllib.error
from pathlib import Path

import cv2
import numpy as np

from face_recognition_engine import get_face_engine, FACES_DIR

logger = logging.getLogger("face-scraper")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")


def download_image(url: str, timeout: int = 10) -> np.ndarray | None:
    """Download an image from URL and return as OpenCV frame."""
    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"
            },
        )
        resp = urllib.request.urlopen(req, timeout=timeout)
        data = resp.read()
        buf = np.frombuffer(data, dtype=np.uint8)
        frame = cv2.imdecode(buf, cv2.IMREAD_COLOR)
        return frame
    except Exception as e:
        logger.warning(f"Failed to download {url}: {e}")
        return None


def scrape_instagram(username: str, max_photos: int = 20) -> list[np.ndarray]:
    """
    Scrape photos from a public Instagram profile.
    Returns list of frames with detectable faces.

    Note: Instagram aggressively blocks scrapers. This uses a basic approach.
    For production use, consider using the Instagram Graph API or a service.
    """
    frames = []
    logger.info(f"Scraping Instagram profile: @{username}")

    # Method 1: Try the basic profile page
    profile_url = f"https://www.instagram.com/{username}/?__a=1&__d=dis"
    try:
        req = urllib.request.Request(
            profile_url,
            headers={
                "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X)",
                "Accept": "application/json",
            },
        )
        resp = urllib.request.urlopen(req, timeout=10)
        data = json.loads(resp.read())

        # Extract image URLs from the response
        edges = (
            data.get("graphql", {})
            .get("user", {})
            .get("edge_owner_to_timeline_media", {})
            .get("edges", [])
        )

        for edge in edges[:max_photos]:
            node = edge.get("node", {})
            img_url = node.get("display_url") or node.get("thumbnail_src")
            if img_url:
                frame = download_image(img_url)
                if frame is not None:
                    frames.append(frame)
                    logger.info(f"  Downloaded photo {len(frames)}/{max_photos}")

        if frames:
            return frames
    except Exception as e:
        logger.info(f"  Instagram API method failed: {e}")

    # Method 2: Scrape from the HTML page
    try:
        page_url = f"https://www.instagram.com/{username}/"
        req = urllib.request.Request(
            page_url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            },
        )
        resp = urllib.request.urlopen(req, timeout=10)
        html = resp.read().decode("utf-8", errors="ignore")

        # Extract image URLs from meta tags and JSON-LD
        import re

        img_pattern = re.compile(r'"display_url"\s*:\s*"([^"]+)"')
        for match in img_pattern.finditer(html):
            if len(frames) >= max_photos:
                break
            url = match.group(1).replace("\\u0026", "&")
            frame = download_image(url)
            if frame is not None:
                frames.append(frame)
                logger.info(f"  Downloaded photo {len(frames)}/{max_photos}")
    except Exception as e:
        logger.warning(f"  Instagram HTML scrape failed: {e}")

    logger.info(f"Scraped {len(frames)} photos from @{username}")
    return frames


def scrape_url(url: str, max_photos: int = 10) -> list[np.ndarray]:
    """Scrape images from any URL (website, direct image link, etc.)."""
    frames = []

    # Check if it's a direct image link
    lower = url.lower().endswith((".jpg", ".jpeg", ".png", ".webp", ".gif"))
    if lower:
        frame = download_image(url)
        if frame is not None:
            frames.append(frame)
        return frames

    # Try to scrape the page for images
    try:
        import re

        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            },
        )
        resp = urllib.request.urlopen(req, timeout=10)
        html = resp.read().decode("utf-8", errors="ignore")

        # Find all image URLs
        img_pattern = re.compile(r'<img[^>]+src="([^"]+)"', re.IGNORECASE)
        base_url = url.rsplit("/", 1)[0]

        for match in img_pattern.finditer(html):
            if len(frames) >= max_photos:
                break
            img_src = match.group(1)
            if img_src.startswith("//"):
                img_src = "https:" + img_src
            elif img_src.startswith("/"):
                img_src = base_url + img_src

            if not img_src.startswith("http"):
                continue

            frame = download_image(img_src)
            if frame is not None:
                frames.append(frame)
                logger.info(f"  Downloaded image {len(frames)}/{max_photos}")
    except Exception as e:
        logger.warning(f"URL scrape failed: {e}")

    return frames


def scrape_directory(dir_path: str, max_photos: int = 50) -> list[np.ndarray]:
    """Load images from a local directory."""
    frames = []
    dirp = Path(dir_path)
    if not dirp.exists():
        logger.error(f"Directory not found: {dir_path}")
        return frames

    extensions = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
    files = sorted(
        [f for f in dirp.iterdir() if f.suffix.lower() in extensions],
        key=lambda f: f.stat().st_mtime,
        reverse=True,
    )

    for f in files[:max_photos]:
        frame = cv2.imread(str(f))
        if frame is not None:
            frames.append(frame)
            logger.info(f"  Loaded {f.name}")

    logger.info(f"Loaded {len(frames)} images from {dir_path}")
    return frames


def webcam_enrollment(name: str, count: int = 5):
    """Live webcam enrollment — capture {count} photos and enroll."""
    engine = get_face_engine()
    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        logger.error("Cannot open webcam")
        return

    logger.info(f"Webcam enrollment for '{name}' — capture {count} photos")
    logger.info("Press SPACE to capture, ESC to finish early")

    captured = 0
    while captured < count:
        ret, frame = cap.read()
        if not ret:
            break

        # Show detected faces
        faces = engine.detect_faces(frame)
        display = frame.copy()
        for face in faces:
            x, y, w, h = face["x"], face["y"], face["w"], face["h"]
            cv2.rectangle(display, (x, y), (x + w, y + h), (0, 255, 0), 2)
            cv2.putText(
                display,
                f"Face {face['confidence']:.2f}",
                (x, y - 10),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.5,
                (0, 255, 0),
                1,
            )

        cv2.putText(
            display,
            f"Captured: {captured}/{count} — Press SPACE",
            (10, 30),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.7,
            (255, 255, 255),
            2,
        )
        cv2.imshow("Enrollment", display)

        key = cv2.waitKey(1) & 0xFF
        if key == 27:  # ESC
            break
        elif key == 32:  # SPACE
            if faces:
                largest = max(faces, key=lambda f: f["w"] * f["h"])
                success = engine.add_known_face(name, frame, largest, source="webcam")
                if success:
                    captured += 1
                    logger.info(f"  Captured face {captured}/{count}")
                else:
                    logger.warning("  No valid face in frame")
            else:
                logger.warning("  No face detected — try again")

    cap.release()
    cv2.destroyAllWindows()
    logger.info(f"Enrollment complete: {name} ({captured} photos)")


def enroll_from_frames(name: str, frames: list[np.ndarray], source: str = "manual"):
    """Enroll a person from multiple frames."""
    engine = get_face_engine()
    enrolled = 0

    for i, frame in enumerate(frames):
        faces = engine.detect_faces(frame)
        if not faces:
            logger.debug(f"  Frame {i + 1}: no face detected, skipping")
            continue

        # Use the largest face
        largest = max(faces, key=lambda f: f["w"] * f["h"])
        success = engine.add_known_face(name, frame, largest, source=source)
        if success:
            enrolled += 1
            logger.info(f"  Frame {i + 1}: enrolled face ({enrolled} total)")
        else:
            logger.debug(f"  Frame {i + 1}: encoding failed")

    logger.info(f"Enrolled {enrolled} photos for '{name}' (from {len(frames)} scraped)")
    return enrolled


def main():
    parser = argparse.ArgumentParser(description="Lilly Face Scraper")
    parser.add_argument("--name", required=True, help="Name of the person to enroll")
    parser.add_argument("--instagram", help="Instagram username to scrape")
    parser.add_argument("--url", help="URL to scrape photos from")
    parser.add_argument("--dir", help="Local directory of photos")
    parser.add_argument("--webcam", action="store_true", help="Live webcam enrollment")
    parser.add_argument(
        "--max-photos", type=int, default=20, help="Max photos to scrape"
    )
    parser.add_argument("--list", action="store_true", help="List all known faces")
    parser.add_argument("--remove", help="Remove a known face by name")
    args = parser.parse_args()

    engine = get_face_engine()

    if args.list:
        faces = engine.list_known_faces()
        if not faces:
            print("No known faces in database.")
        else:
            print(f"\n{'Name':<20} {'Photos':<8} {'Source':<12} {'Last Seen'}")
            print("-" * 65)
            for f in faces:
                print(
                    f"{f['name']:<20} {f['photo_count']:<8} {f['source']:<12} {f['last_seen']}"
                )
        return

    if args.remove:
        if engine.remove_known_face(args.remove):
            print(f"Removed: {args.remove}")
        else:
            print(f"Face '{args.remove}' not found")
        return

    # Enrollment
    frames = []

    if args.webcam:
        webcam_enrollment(args.name)
        return

    if args.instagram:
        frames = scrape_instagram(args.instagram, args.max_photos)
    elif args.url:
        frames = scrape_url(args.url, args.max_photos)
    elif args.dir:
        frames = scrape_directory(args.dir, args.max_photos)
    else:
        parser.error("Provide --instagram, --url, --dir, or --webcam")

    if frames:
        source = "instagram" if args.instagram else "url" if args.url else "dir"
        enroll_from_frames(args.name, frames, source=source)
    else:
        print("No photos found to enroll from.")


if __name__ == "__main__":
    main()
