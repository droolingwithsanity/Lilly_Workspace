#!/usr/bin/env python3
"""
YOLO Real-World Testing Script
Tests YOLO vision server with various real-world images and scenarios
"""

import base64
import json
import requests
import time
from pathlib import Path
from typing import Dict, List, Any

# Test images configuration
TEST_IMAGES = {
    "street_scene": {
        "file": "test_street.jpg",
        "description": "Street scene with laptop and houseplant",
        "expected_objects": ["laptop", "houseplant"],
    },
    "office_scene": {
        "file": "test_office.jpg",
        "description": "Office building exterior",
        "expected_objects": ["building", "door"],
    },
    "park_scene": {
        "file": "test_park.jpg",
        "description": "Park/nature scene",
        "expected_objects": ["tree", "plant", "nature"],
    },
    "cafe_scene": {
        "file": "test_cafe.jpg",
        "description": "Cafe/coffee shop interior",
        "expected_objects": ["cup", "table", "chair", "person"],
    },
}


def test_image_detection(image_path: str, scene_name: str) -> Dict[str, Any]:
    """Test YOLO detection on a single image"""
    try:
        with open(image_path, "rb") as f:
            image_data = f.read()
            image_b64 = base64.b64encode(image_data).decode("utf-8")

        payload = {"image_b64": image_b64, "avatar": "puppy", "generate_audio": False}

        start_time = time.time()
        response = requests.post(
            "http://127.0.0.1:8198/api/vision", json=payload, timeout=30
        )
        detection_time = time.time() - start_time

        result = response.json()

        return {
            "scene": scene_name,
            "status": "success",
            "detection_time_ms": round(detection_time * 1000, 2),
            "total_detections": len(result.get("detections", [])),
            "detections": result.get("detections", []),
            "reply": result.get("reply", ""),
            "agents": result.get("agents", []),
            "file_size_kb": round(len(image_data) / 1024, 2),
        }

    except Exception as e:
        return {"scene": scene_name, "status": "error", "error": str(e)}


def analyze_detection_results(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Analyze overall detection performance"""
    successful_tests = [r for r in results if r["status"] == "success"]
    failed_tests = [r for r in results if r["status"] == "error"]

    total_detections = sum(r["total_detections"] for r in successful_tests)
    avg_detection_time = (
        sum(r["detection_time_ms"] for r in successful_tests) / len(successful_tests)
        if successful_tests
        else 0
    )

    # Collect all unique labels detected
    all_labels = set()
    for r in successful_tests:
        for det in r["detections"]:
            all_labels.add(det["label"].lower())

    return {
        "total_tests": len(results),
        "successful_tests": len(successful_tests),
        "failed_tests": len(failed_tests),
        "total_detections": total_detections,
        "average_detection_time_ms": round(avg_detection_time, 2),
        "unique_labels_detected": sorted(list(all_labels)),
        "success_rate": f"{len(successful_tests) / len(results) * 100:.1f}%",
    }


def run_comprehensive_test():
    """Run comprehensive YOLO real-world testing"""
    print("🚀 Starting YOLO Real-World Testing Suite")
    print("=" * 60)

    results = []

    for scene_name, config in TEST_IMAGES.items():
        image_path = config["file"]

        # Check if image exists
        if not Path(image_path).exists():
            print(f"⚠️  Skipping {scene_name}: {image_path} not found")
            continue

        print(f"\n📸 Testing {scene_name}: {config['description']}")
        print(f"   Image: {image_path}")

        result = test_image_detection(image_path, scene_name)
        results.append(result)

        if result["status"] == "success":
            print(
                f"   ✅ Detected {result['total_detections']} objects in {result['detection_time_ms']}ms"
            )
            for det in result["detections"][:3]:  # Show top 3
                conf = det.get("conf", 0)
                distance = det.get("distance_desc", "N/A")
                print(f"      • {det['label']}: {conf:.1%} confidence, {distance}")
        else:
            print(f"   ❌ Error: {result['error']}")

    # Overall analysis
    print("\n" + "=" * 60)
    print("📊 Overall Analysis")
    print("=" * 60)

    analysis = analyze_detection_results(results)

    print(f"Success Rate: {analysis['success_rate']}")
    print(f"Total Detections: {analysis['total_detections']}")
    print(f"Average Detection Time: {analysis['average_detection_time_ms']}ms")
    print(f"Unique Labels Detected: {len(analysis['unique_labels_detected'])}")

    if analysis["unique_labels_detected"]:
        print(f"Labels: {', '.join(analysis['unique_labels_detected'][:10])}")
        if len(analysis["unique_labels_detected"]) > 10:
            print(
                f"        ... and {len(analysis['unique_labels_detected']) - 10} more"
            )

    # Save detailed results
    output_file = Path("yolo_real_world_results.json")
    with open(output_file, "w") as f:
        json.dump(
            {"summary": analysis, "detailed_results": results}, f, indent=2, default=str
        )

    print(f"\n💾 Detailed results saved to: {output_file}")

    return analysis


if __name__ == "__main__":
    run_comprehensive_test()
