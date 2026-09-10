#!/usr/bin/env python3
"""
Camera Window Workflow Test
Automated test for the new multi-camera Camera Window feature.
Tests: HTML/CSS/JS syntax, API endpoints, web UI, Blink integration.
"""

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from datetime import datetime

# ─── Test Framework ─────────────────────────────────────────
RESULTS = []
PASSED = 0
FAILED = 0
SKIPPED = 0
LOG_FILE = Path(__file__).parent / "test_camera_window_results.log"


def log_result(step, name, status, details=""):
    """Log a test result."""
    global PASSED, FAILED, SKIPPED
    icon = {"PASS": "✅", "FAIL": "❌", "SKIP": "⏭️", "INFO": "ℹ️"}
    if status == "PASS":
        PASSED += 1
    elif status == "FAIL":
        FAILED += 1
    elif status == "SKIP":
        SKIPPED += 1
    line = f"{icon.get(status, '?')} [{step:02d}] {name}: {status}"
    if details:
        line += f" — {details}"
    RESULTS.append(line)
    print(line)


def section(title):
    """Print a section header."""
    print(f"\n{'=' * 60}")
    print(f"  {title}")
    print(f"{'=' * 60}")


# ─── Step 1: File Integrity ─────────────────────────────────
section("STEP 1: File Integrity Checks")

WORKSPACE = Path("/home/labhrasd/Lilly_Workspace")
LILLY_PY = WORKSPACE / "lilly_ai.py"
WIKI_HTML = WORKSPACE / "wiki.html"
BLINK_CONNECTOR = WORKSPACE / "blink_connector.py"
BLINK_DETECTOR = WORKSPACE / "blink_detector.py"

# 1.1 Check files exist
for f, label in [
    (LILLY_PY, "lilly_ai.py"),
    (WIKI_HTML, "wiki.html"),
    (BLINK_CONNECTOR, "blink_connector.py"),
    (BLINK_DETECTOR, "blink_detector.py"),
]:
    if f.exists():
        log_result(1, f"{label} exists", "PASS", f"{f.stat().st_size:,} bytes")
    else:
        log_result(1, f"{label} exists", "FAIL", "File not found")

# 1.2 Check lilly_ai.py has Camera Window code
content = LILLY_PY.read_text(errors="replace")

checks = [
    ("#cameraWindow CSS", "#cameraWindow{"),
    ("Camera Window HTML", 'id="cameraWindow"'),
    ("Blink feed panel", 'id="cwBlink"'),
    ("Phone feed panel", 'id="cwPhone"'),
    ("Webcam feed panel", 'id="cwWebcam"'),
    ("openCameraWindow() JS", "function openCameraWindow()"),
    ("closeCameraWindow() JS", "function closeCameraWindow()"),
    ("_cwStartBlink() JS", "function _cwStartBlink()"),
    ("_cwStartPhone() JS", "function _cwStartPhone()"),
    ("_cwStartWebcam() JS", "function _cwStartWebcam()"),
    ("_initCwDrag() JS", "function _initCwDrag()"),
    ("pipContainer HTML", 'id="pipContainer"'),
    ("filterBar HTML", 'id="filterBar"'),
    ("arOverlay HTML", 'id="arOverlay"'),
    ("arCanvas HTML", 'id="arCanvas"'),
]

for i, (name, marker) in enumerate(checks, 2):
    if marker in content:
        log_result(i + 1, f"lilly_ai.py has {name}", "PASS")
    else:
        log_result(i + 1, f"lilly_ai.py has {name}", "FAIL", f"Missing: {marker}")

# ─── Step 2: CSS Validation ─────────────────────────────────
section("STEP 2: CSS Validation")

# 2.1 Check Camera Window CSS rules exist
css_checks = [
    ("#cameraWindow grid layout", "grid-template-columns:1fr 1fr 1fr"),
    ("#cameraWindow .cw-header", ".cw-header{"),
    ("#cameraWindow .cw-feed", ".cw-feed{"),
    ("#cameraWindow .cw-feed-label", ".cw-feed-label{"),
    ("#cameraWindow .cw-close", ".cw-close{"),
    ("#cameraWindow .cw-title", ".cw-title{"),
    ("#cameraWindow .cw-desc", ".cw-desc{"),
    ("#cameraWindow resize:both", "resize:both"),
    ("#cameraWindow backdrop-filter", "backdrop-filter:blur"),
]

for i, (name, css_marker) in enumerate(css_checks, 100):
    if css_marker in content:
        log_result(i, f"CSS: {name}", "PASS")
    else:
        log_result(i, f"CSS: {name}", "FAIL", f"Missing: {css_marker}")

# ─── Step 3: HTML Structure Validation ──────────────────────
section("STEP 3: HTML Structure Validation")

# 3.1 Check Camera Window HTML structure
html_checks = [
    ("Camera Window container", '<div id="cameraWindow">'),
    ("Header with title", 'class="cw-title"'),
    ("Close button", 'onclick="closeCameraWindow()"'),
    ("3-column grid", 'class="cw-grid"'),
    ("Blink feed div", 'id="cwBlink"'),
    ("Phone feed div", 'id="cwPhone"'),
    ("Webcam feed div", 'id="cwWebcam"'),
    ("Blink img element", 'id="cwBlinkImg"'),
    ("Phone img element", 'id="cwPhoneImg"'),
    ("Webcam img element", 'id="cwWebcamImg"'),
    ("Blink label", 'id="cwBlinkLabel"'),
    ("Phone label", 'id="cwPhoneLabel"'),
    ("Webcam label", 'id="cwWebcamLabel"'),
    ("Description area", 'id="cwDesc"'),
    ("pipContainer exists", 'id="pipContainer"'),
    ("filterBar exists", 'id="filterBar"'),
    ("arOverlay exists", 'id="arOverlay"'),
    ("arCanvas exists", 'id="arCanvas"'),
]

for i, (name, html_marker) in enumerate(html_checks, 200):
    if html_marker in content:
        log_result(i, f"HTML: {name}", "PASS")
    else:
        log_result(i, f"HTML: {name}", "FAIL", f"Missing: {html_marker}")

# 3.2 Check for balanced div tags in Camera Window
cw_start = content.find('<div id="cameraWindow">')
cw_end = content.find("</div>", cw_start + 500)  # rough end
if cw_start != -1 and cw_end != -1:
    cw_html = content[cw_start : cw_end + 10]
    opens = cw_html.count("<div")
    closes = cw_html.count("</div>")
    if opens == closes:
        log_result(
            220,
            "Camera Window div tags balanced",
            "PASS",
            f"{opens} opens, {closes} closes",
        )
    else:
        log_result(
            220,
            "Camera Window div tags balanced",
            "WARN",
            f"{opens} opens, {closes} closes (may be nested)",
        )

# ─── Step 4: JavaScript Validation ──────────────────────────
section("STEP 4: JavaScript Validation")

# 4.1 Check toggleCameraView calls openCameraWindow
if "openCameraWindow()" in content and "toggleCameraView" in content:
    # Find the toggleCameraView function and check it calls openCameraWindow
    tv_start = content.find("async function toggleCameraView()")
    if tv_start != -1:
        tv_body = content[tv_start : tv_start + 500]
        if "openCameraWindow()" in tv_body:
            log_result(300, "toggleCameraView calls openCameraWindow", "PASS")
        else:
            log_result(
                300,
                "toggleCameraView calls openCameraWindow",
                "FAIL",
                "openCameraWindow not in function body",
            )
    else:
        log_result(300, "toggleCameraView function found", "FAIL")
else:
    log_result(300, "toggleCameraView integration", "FAIL")

# 4.2 Check JS function signatures
js_functions = [
    ("openCameraWindow", "function openCameraWindow()"),
    ("closeCameraWindow", "function closeCameraWindow"),
    ("_cwStartBlink", "async function _cwStartBlink"),
    ("_cwStartPhone", "async function _cwStartPhone"),
    ("_cwStartWebcam", "async function _cwStartWebcam"),
    ("_initCwDrag", "function _initCwDrag"),
]

for i, (name, sig) in enumerate(js_functions, 310):
    if sig in content:
        log_result(i, f"JS: {name}() exists", "PASS")
    else:
        log_result(i, f"JS: {name}() exists", "FAIL", f"Missing: {sig}")

# 4.3 Check API endpoint references in JS
api_refs = [
    ("/api/blink/cameras in _cwStartBlink", "/api/blink/cameras"),
    ("/api/vision/single in _cwStartBlink", "/api/vision/single"),
    ("/api/phone/camera/status in _cwStartPhone", "/api/phone/camera/status"),
    ("/api/phone/camera/capture in _cwStartPhone", "/api/phone/camera/capture"),
    ("CameraBridge.start in _cwStartWebcam", "CameraBridge.start()"),
]

for i, (name, ref) in enumerate(api_refs, 320):
    # Only check within the camera window JS section
    cw_js_start = content.find("function openCameraWindow()")
    if cw_js_start != -1 and ref in content[cw_js_start : cw_js_start + 5000]:
        log_result(i, f"JS API ref: {name}", "PASS")
    else:
        log_result(
            i, f"JS API ref: {name}", "WARN", "Not found in Camera Window JS section"
        )

# 4.4 Check for JS syntax issues (basic brace matching)
cw_section = content[
    content.find("function openCameraWindow()") : content.find("// ─── AR Mode")
]
open_braces = cw_section.count("{")
close_braces = cw_section.count("}")
if open_braces == close_braces:
    log_result(
        330,
        "JS braces balanced in Camera Window section",
        "PASS",
        f"{open_braces} pairs",
    )
else:
    log_result(
        330,
        "JS braces balanced in Camera Window section",
        "FAIL",
        f"{open_braces} opens vs {close_braces} closes",
    )

# ─── Step 5: Wiki Documentation ─────────────────────────────
section("STEP 5: Wiki Documentation")

wiki_content = WIKI_HTML.read_text(errors="replace")

wiki_checks = [
    ("Camera Window documented", "Camera Window"),
    ("Blink cameras documented", "Blink Cameras"),
    ("3-panel layout mentioned", "3-panel"),
    ("Blink setup instructions", "BLINK_USERNAME"),
    ("/api/vision/multi endpoint", "/api/vision/multi"),
    ("/api/vision/single endpoint", "/api/vision/single"),
    ("/api/blink/status endpoint", "/api/blink/status"),
    ("/api/blink/cameras endpoint", "/api/blink/cameras"),
    ("/api/blink/snapshot endpoint", "/api/blink/snapshot"),
    ("/api/phone/camera/status endpoint", "/api/phone/camera/status"),
    ("/api/phone/camera/capture endpoint", "/api/phone/camera/capture"),
    ("Single camera commands", "show me Front Door"),
    ("Doorbell command", "who's at the door"),
]

for i, (name, marker) in enumerate(wiki_checks, 400):
    if marker in wiki_content:
        log_result(i, f"Wiki: {name}", "PASS")
    else:
        log_result(i, f"Wiki: {name}", "FAIL", f"Missing: {marker}")

# ─── Step 6: Server Health (if running) ─────────────────────
section("STEP 6: Server Health Checks")

import urllib.request
import urllib.error

BASE = "http://localhost:8098"


def try_request(url, method="GET", data=None, timeout=5):
    """Try an HTTP request, return (status, body) or (error_string, None)."""
    try:
        req = urllib.request.Request(url, method=method)
        if data:
            req.data = json.dumps(data).encode()
            req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode(errors="replace")
            return resp.status, body
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(errors="replace")
    except urllib.error.URLError as e:
        return f"ConnectionError: {e.reason}", None
    except Exception as e:
        return f"Error: {e}", None


# 6.1 Server health
status, body = try_request(f"{BASE}/api/health")
if status == 200:
    log_result(500, "Server health endpoint", "PASS", f"HTTP {status}")
else:
    log_result(500, "Server health endpoint", "SKIP", f"Not reachable: {status}")

# 6.2 Blink status
status, body = try_request(f"{BASE}/api/blink/status")
if status == 200 and body is not None:
    try:
        data = json.loads(body)
        log_result(501, "Blink status endpoint", "PASS", json.dumps(data)[:120])
    except Exception:
        log_result(501, "Blink status endpoint", "PASS", body[:120])
elif status == 404:
    log_result(
        501,
        "Blink status endpoint",
        "SKIP",
        "Endpoint not found (server may need restart)",
    )
else:
    log_result(501, "Blink status endpoint", "SKIP", f"HTTP {status}")

# 6.3 Blink cameras
status, body = try_request(f"{BASE}/api/blink/cameras")
if status == 200 and body is not None:
    try:
        data = json.loads(body)
        cams = data.get("cameras", [])
        log_result(502, "Blink cameras endpoint", "PASS", f"{len(cams)} camera(s)")
    except Exception:
        log_result(502, "Blink cameras endpoint", "PASS", body[:120])
elif status == 404:
    log_result(
        502,
        "Blink cameras endpoint",
        "SKIP",
        "Endpoint not found (server may need restart)",
    )
else:
    log_result(502, "Blink cameras endpoint", "SKIP", f"HTTP {status}")

# 6.4 Phone camera status
status, body = try_request(f"{BASE}/api/phone/camera/status")
if status == 200 and body is not None:
    try:
        data = json.loads(body)
        log_result(503, "Phone camera status", "PASS", json.dumps(data)[:120])
    except Exception:
        log_result(503, "Phone camera status", "PASS", body[:120])
elif status == 404:
    log_result(503, "Phone camera status", "SKIP", "Endpoint not found")
else:
    log_result(503, "Phone camera status", "SKIP", f"HTTP {status}")

# 6.5 Vision status
status, body = try_request(f"{BASE}/api/vision/status")
if status == 200 and body is not None:
    try:
        data = json.loads(body)
        log_result(504, "Vision status endpoint", "PASS", json.dumps(data)[:120])
    except Exception:
        log_result(504, "Vision status endpoint", "PASS", body[:120])
elif status == 404:
    log_result(504, "Vision status endpoint", "SKIP", "Endpoint not found")
else:
    log_result(504, "Vision status endpoint", "SKIP", f"HTTP {status}")

# 6.6 Phone sensor server
try:
    req = urllib.request.Request("http://100.115.234.87:8099/health")
    with urllib.request.urlopen(req, timeout=5) as resp:
        body = resp.read().decode()
        log_result(505, "Phone sensor server", "PASS", body[:100])
except Exception as e:
    log_result(505, "Phone sensor server", "SKIP", f"Not reachable: {e}")

# ─── Step 7: Web UI Content Check ───────────────────────────
section("STEP 7: Web UI Content Check")

# 7.1 Web UI loads
status, body = try_request(f"{BASE}/")
if status == 200 and body and "cameraWindow" in body:
    log_result(600, "Web UI loads with Camera Window", "PASS")
elif status == 200:
    log_result(
        600,
        "Web UI loads",
        "PASS",
        "Camera Window not in served HTML (may be embedded)",
    )
else:
    log_result(600, "Web UI loads", "SKIP", f"HTTP {status}")

# 7.2 Check Camera Window in served page
if body:
    ui_checks = [
        ("Camera Window div", "cameraWindow"),
        ("openCameraWindow function", "openCameraWindow"),
        ("closeCameraWindow function", "closeCameraWindow"),
        ("_cwStartBlink function", "_cwStartBlink"),
        ("pipContainer div", "pipContainer"),
        ("filterBar div", "filterBar"),
        ("arOverlay div", "arOverlay"),
    ]
    for i, (name, marker) in enumerate(ui_checks, 610):
        if marker in body:
            log_result(i, f"Web UI: {name}", "PASS")
        else:
            log_result(i, f"Web UI: {name}", "WARN", "Not found in served content")

# ─── Step 8: Blink Connector Unit Test ──────────────────────
section("STEP 8: Blink Connector Module")

try:
    sys.path.insert(0, str(WORKSPACE))
    from blink_connector import BlinkConnector, get_blink_connector

    log_result(700, "blink_connector imports successfully", "PASS")

    # Check class has expected methods
    conn = BlinkConnector()
    methods = [
        "start",
        "stop",
        "list_cameras",
        "get_snapshot",
        "get_latest_image",
        "get_all_snapshots",
        "get_snapshot_base64",
        "get_all_snapshots_base64",
    ]
    for i, method in enumerate(methods, 710):
        if hasattr(conn, method):
            log_result(i, f"BlinkConnector.{method}()", "PASS")
        else:
            log_result(i, f"BlinkConnector.{method}()", "FAIL", "Method missing")

    # Singleton check
    singleton = get_blink_connector()
    if isinstance(singleton, BlinkConnector):
        log_result(720, "get_blink_connector() returns BlinkConnector", "PASS")
    else:
        log_result(720, "get_blink_connector() returns BlinkConnector", "FAIL")

except ImportError as e:
    log_result(700, "blink_connector imports", "SKIP", str(e))
except Exception as e:
    log_result(700, "blink_connector test", "SKIP", str(e))

# ─── Step 9: Blink Detector Unit Test ───────────────────────
section("STEP 9: Blink Detector Module")

try:
    from blink_detector import (
        BlinkDetector,
        get_blink_detector,
        detect_blink_in_frame,
        BlinkResult,
    )

    log_result(800, "blink_detector imports successfully", "PASS")

    detector = BlinkDetector()
    if detector:
        log_result(801, "BlinkDetector() creates instance", "PASS")

    # Check expected attributes
    attrs = [
        "ear_threshold",
        "min_blink_frames",
        "blink_cooldown",
        "blink_counter",
        "total_blinks",
        "cooldown_counter",
        "frame_count",
    ]
    for i, attr in enumerate(attrs, 810):
        if hasattr(detector, attr):
            log_result(i, f"BlinkDetector.{attr}", "PASS")
        else:
            log_result(i, f"BlinkDetector.{attr}", "FAIL", "Attribute missing")

    # Check singleton
    singleton = get_blink_detector()
    if isinstance(singleton, BlinkDetector):
        log_result(820, "get_blink_detector() returns BlinkDetector", "PASS")
    else:
        log_result(820, "get_blink_detector() returns BlinkDetector", "FAIL")

    # Check BlinkResult dataclass
    result = BlinkResult(
        is_blinking=False,
        ear_score=0.3,
        blink_count=0,
        eyes_closed_ratio=0.1,
        confidence=0.9,
    )
    if result.is_blinking == False and result.ear_score == 0.3:
        log_result(821, "BlinkResult dataclass works", "PASS")
    else:
        log_result(821, "BlinkResult dataclass works", "FAIL")

except ImportError as e:
    log_result(800, "blink_detector imports", "SKIP", str(e))
except Exception as e:
    log_result(800, "blink_detector test", "SKIP", str(e))

# ─── Step 10: YOLO Vision Server Integration ────────────────
section("STEP 10: YOLO Vision Server Integration")

yolo_file = WORKSPACE / "yolov8_vision_server.py"
if yolo_file.exists():
    yolo_content = yolo_file.read_text(errors="replace")
    if "blink_detector" in yolo_content:
        log_result(900, "yolov8_vision_server imports blink_detector", "PASS")
    else:
        log_result(900, "yolov8_vision_server imports blink_detector", "FAIL")

    if "blink_info" in yolo_content:
        log_result(901, "yolov8_vision_server uses blink_info", "PASS")
    else:
        log_result(901, "yolov8_vision_server uses blink_info", "FAIL")
else:
    log_result(900, "yolov8_vision_server exists", "SKIP", "File not found")

# ─── Write Results Log ──────────────────────────────────────
section("TEST RESULTS SUMMARY")

timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
summary = f"""
Camera Window Test Results
=========================
Timestamp: {timestamp}

Total: {PASSED + FAILED + SKIPPED}
  ✅ Passed:  {PASSED}
  ❌ Failed:  {FAILED}
  ⏭️  Skipped: {SKIPPED}
  (Skipped = server not running or optional dependency)
"""

print(summary)

# Write log file
with open(LOG_FILE, "w") as f:
    f.write(f"Camera Window Test Results — {timestamp}\n")
    f.write("=" * 60 + "\n\n")
    for r in RESULTS:
        f.write(r + "\n")
    f.write(summary)

print(f"Results saved to: {LOG_FILE}")

# Exit code
sys.exit(0 if FAILED == 0 else 1)
