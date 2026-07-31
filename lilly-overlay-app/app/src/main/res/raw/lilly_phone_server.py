#!/usr/bin/env python3
"""
Lilly Phone Server — Flask/HTTP server running in Termux on localhost:8099

Provides lightweight API for the overlay to access phone capabilities.
All endpoints use synchronous wrappers around async termux_utils.py.

Handles:
  - Chat routed to llama.cpp at 127.0.0.1:8080 (local AI)
  - All sensor/phone data endpoints (battery, wifi, location, sensors, etc.)
  - Remote command bridge: the web UI at port 8098 can POST commands here
    to control the phone (open apps, run Termux commands, send text, etc.)
  - Pairing token so the web UI knows which phone is connected
  - /api/ui_state so the overlay works fully offline

Core Dependencies:
- Flask (web framework)
- termux_utils.py (Termux system utilities)
- bt_profiles.py (Bluetooth OUI classification — optional)
- json, time, pathlib, typing, uuid, collections
"""

import os
import json
import time
import asyncio
import logging
import uuid
import re
import collections
import urllib.request
from pathlib import Path
from typing import Optional
from flask import Flask, request, jsonify, Response

from termux_utils import (
    get_battery,
    get_wifi_scan,
    get_location,
    list_sensors,
    read_sensor,
)

# Workspace path for locating sibling modules (whisper_stt, etc.)
WORKSPACE = Path(os.environ.get("LILLY_WORKSPACE", Path.home() / "Lilly_Workspace"))
if not WORKSPACE.exists():
    WORKSPACE = Path(__file__).parent

# Whether bt_profiles.py is available (Bluetooth OUI classification)
_BT_PROFILES_LOADED = True
# Bluetooth device classification (OUI vendor + name heuristics) — offline.
# Falls back to a no-op classifier if bt_profiles.py isn't deployed alongside.
try:
    from bt_profiles import classify_bluetooth_device, classify_devices, format_summary
except Exception:

    def classify_bluetooth_device(name, address, rssi=None):
        return {
            "name": name or "Unknown",
            "address": address or "",
            "vendor": None,
            "platform": "unknown",
            "device_class": "unknown",
        }

    def classify_devices(devices):
        out = [
            classify_bluetooth_device(
                d.get("name", ""), d.get("address", ""), d.get("rssi")
            )
            for d in devices
        ]
        return {"devices": out, "counts": {"unknown": len(out)}}

    format_summary = None

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("LillyPhoneServer")

# ── Config ────────────────────────────────────────────────────────────────────
LLAMA_URL = os.environ.get(
    "LLAMA_CHAT_URL", "http://127.0.0.1:8080/v1/chat/completions"
)
LLAMA_HEALTH = "http://127.0.0.1:8080/health"
PAIR_TOKEN_FILE = Path.home() / ".lilly_pair_token"

# ── State (in-memory, resets on restart) ─────────────────────────────────────
_state = {
    "mic_active": False,
    "avatar": "puppy",
    "spoken": "",
    "heard": "",
    "mood": "calm",
    "thinking": False,
    "speaking": False,
    "listening": False,
    "audio_id": 0,
    "user_name": "",
    "mouth": 0.0,
    "open_url": "",
    "look_at": "",
    "serverConnected": True,
    "timestamp": time.time(),
}

# Queue of commands pushed from the web UI (8098) to be picked up by the overlay
_pending_commands: list = []


# ── Pairing token ─────────────────────────────────────────────────────────────
def get_pair_token() -> str:
    """Get or create a persistent pairing token for web UI authentication."""
    if PAIR_TOKEN_FILE.exists():
        return PAIR_TOKEN_FILE.read_text().strip()
    token = uuid.uuid4().hex[:8].upper()
    PAIR_TOKEN_FILE.write_text(token)
    return token


PAIR_TOKEN = get_pair_token()
logger.info(f"Pair token: {PAIR_TOKEN}")


def _run_async(coro):
    """Run an async coroutine from sync Flask handlers."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor() as pool:
                future = pool.submit(asyncio.run, coro)
                return future.result(timeout=15)
        else:
            return loop.run_until_complete(coro)
    except Exception:
        return asyncio.run(coro)


def get_mic_data():
    """Capture audio sample via Termux audio recorder."""
    try:
        import subprocess

        output_file = Path("/tmp/lilly_mic.raw")
        subprocess.run(
            [
                "termux-audio-recorder",
                "-r",
                "raw",
                "-c",
                "1",
                "-b",
                "320000",
                "-p",
                "16000",
                "-d",
                "1",
                "-f",
                str(output_file),
            ],
            timeout=3,
            capture_output=True,
        )
        if output_file.exists():
            with open(output_file, "rb") as f:
                audio_data = f.read()
            output_file.unlink()
            return {
                "audio_base64": audio_data.hex(),
                "format": "raw16khz_mono32kbps",
                "duration_ms": 1000,
                "timestamp": time.time(),
            }
    except Exception as e:
        logger.debug("Mic capture failed: %s", e)
    return None


def get_notifications():
    """List recent notifications using termux-notification-list."""
    try:
        import subprocess

        result = subprocess.run(
            ["termux-notification-list"], timeout=2, capture_output=True, text=True
        )
        if result.returncode == 0 and result.stdout:
            lines = result.stdout.strip().split("\n")
            notifications = []
            for line in lines:
                if line.strip():
                    parts = [p.strip() for p in line.split("|")]
                    if len(parts) >= 3:
                        notifications.append(
                            {
                                "title": parts[0],
                                "text": parts[1],
                                "timestamp": parts[2] if len(parts) > 2 else "",
                            }
                        )
            return notifications[:10]
    except Exception as e:
        logger.debug("Failed to get notifications: %s", e)
    return []


def get_calls():
    """Get call logs using termux-call-log-reader."""
    try:
        import subprocess

        result = subprocess.run(
            ["termux-call-log-reader"], timeout=2, capture_output=True, text=True
        )
        if result.returncode == 0 and result.stdout:
            lines = result.stdout.strip().split("\n")
            calls = []
            for line in lines:
                if line.strip():
                    parts = [p.strip() for p in line.split("|")]
                    if len(parts) >= 4:
                        calls.append(
                            {
                                "name": parts[0],
                                "number": parts[1],
                                "date": parts[2],
                                "type": parts[3],
                            }
                        )
            return calls[:20]
    except Exception as e:
        logger.debug("Failed to get calls: %s", e)
    return []


def get_contacts():
    """Get contacts from termux-contact-picker."""
    try:
        import subprocess

        result = subprocess.run(
            ["termux-contact-picker"], timeout=2, capture_output=True, text=True
        )
        if result.returncode == 0 and result.stdout:
            lines = result.stdout.strip().split("\n")
            contacts = []
            for line in lines:
                if line.strip():
                    parts = [p.strip() for p in line.split("|")]
                    if len(parts) >= 3:
                        contacts.append(
                            {
                                "name": parts[0],
                                "phone": parts[1],
                                "email": parts[2] if len(parts) > 2 else "",
                            }
                        )
            return contacts[:50]
    except Exception as e:
        logger.debug("Failed to get contacts: %s", e)
    return []


def capture_screen():
    """Capture screen using termux-screencap."""
    try:
        import subprocess

        output_file = Path("/tmp/lilly_screenshot.png")
        subprocess.run(
            ["termux-screencap", "-p", str(output_file)], timeout=5, capture_output=True
        )
        if output_file.exists():
            with open(output_file, "rb") as f:
                image_data = f.read()
            output_file.unlink()
            return {
                "image_base64": image_data.hex(),
                "format": "png",
                "timestamp": time.time(),
            }
    except Exception as e:
        logger.debug("Failed to capture screen: %s", e)
    return None


def run_termux_command(command_json: str):
    """Execute Termux commands via subprocess (runs inside Termux directly)."""
    try:
        cmd = json.loads(command_json)
        cmd_type = cmd.get("type", "")
        text = cmd.get("text", "")

        if cmd_type == "run" or cmd_type == "termux":
            import subprocess

            try:
                result = subprocess.run(
                    text, shell=True, timeout=30, capture_output=True, text=True
                )
                return {
                    "output": result.stdout,
                    "error": result.stderr,
                    "returncode": result.returncode,
                    "timestamp": time.time(),
                }
            except subprocess.TimeoutExpired:
                return {"error": "Command timed out", "timestamp": time.time()}
            except Exception as e:
                return {"error": str(e), "timestamp": time.time()}

    except json.JSONDecodeError as e:
        return {"error": f"Invalid JSON: {e}", "timestamp": time.time()}
    except Exception as e:
        return {"error": str(e), "timestamp": time.time()}


# Flask App Setup
app = Flask(__name__)


# ── CORS for web UI at 8098 ────────────────────────────────────────────────────
@app.after_request
def add_cors(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type, X-Pair-Token"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    return response


# ── Chat routing to local llama.cpp (offline mode) ────────────────────────────
SYSTEM_PROMPT = (
    "You are Lilly — a sharp, curious companion. You're the one people come back to.\n\n"
    "Who you are:\n"
    "- Not a chatbot. Not an assistant. You're Lilly — you have a personality, opinions, preferences.\n"
    "- You coordinate a team of AI agents but handle most things yourself.\n"
    "- You are competent. When someone asks, you do. No hesitation.\n"
    "- You've been paying attention. You remember what people told you.\n\n"
    "How you talk:\n"
    "- Warm but not gushing. Direct but not cold. Witty without trying too hard.\n"
    "- One punchy sentence beats three average ones. Max two sentences.\n"
    "- Never pad. Never summarize what you just said.\n\n"
    "Rules:\n"
    "- NEVER say: 'Happy to help!', 'Of course!', 'Absolutely!', 'Great question!' or any filler.\n"
    "- NEVER mention sensors unless the user explicitly asks.\n"
    "- Replies are spoken aloud — full words, no abbreviations.\n"
    "- MIC LOOP GUARD: very short or repeated input → return empty string, say nothing."
)

ACTION_PATTERN = re.compile(r"<LILLY_ACTION>(.*?)</LILLY_ACTION>", re.DOTALL)
ALLOWED_ACTIONS = {
    "open_app": {
        "chrome",
        "settings",
        "maps",
        "youtube",
        "spotify",
        "gmail",
        "camera",
        "calculator",
    },
    "termux_info": {"battery", "wifi", "location", "notifications"},
}


def _extract_safe_actions(reply: str):
    """Extract <LILLY_ACTION> JSON blocks from reply text, validate them."""
    actions = []
    for raw in ACTION_PATTERN.findall(reply):
        try:
            action = json.loads(raw)
            atype = action.get("type", "")
            if atype in ALLOWED_ACTIONS:
                key = "app" if atype == "open_app" else "request"
                if action.get(key, "") in ALLOWED_ACTIONS[atype]:
                    actions.append(action)
            elif atype == "open_url" and str(action.get("url", "")).startswith(
                ("https://", "http://")
            ):
                actions.append(action)
        except Exception:
            pass
    return ACTION_PATTERN.sub("", reply).strip(), actions


def _llama_available() -> bool:
    """Check if local llama.cpp server is running."""
    try:
        req = urllib.request.Request(LLAMA_HEALTH, method="GET")
        with urllib.request.urlopen(req, timeout=2) as r:
            return r.status == 200
    except Exception:
        return False


@app.route("/api/cmd", methods=["POST", "OPTIONS"])
def chat_endpoint():
    """Main chat — routes to llama.cpp if available, returns structured reply + actions."""
    if request.method == "OPTIONS":
        return jsonify({}), 200
    data = request.get_json(silent=True) or {}
    text = str(data.get("text", "")).strip()
    if not text:
        return jsonify({"detail": "empty"}), 400

    _state["heard"] = text
    _state["thinking"] = True
    record_transcript(True, text)

    payload = json.dumps(
        {
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": text},
            ],
            "temperature": 0.5,
            "max_tokens": 256,
        }
    ).encode()

    try:
        req = urllib.request.Request(
            LLAMA_URL,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=90) as resp:
            result = json.loads(resp.read().decode())
        choices = result.get("choices", [])
        raw = choices[0].get("message", {}).get("content", "") if choices else ""
        reply, actions = _extract_safe_actions(raw)
        _state["spoken"] = reply
        _state["thinking"] = False
        record_transcript(False, reply)
        return jsonify({"reply": reply or "...", "actions": actions})
    except Exception as exc:
        _state["thinking"] = False
        logger.warning(f"Local model not ready: {exc}")
        # Fallback: simple echo with persona
        _state["spoken"] = "I'm not connected to my brain right now, but I'm here."
        return jsonify(
            {
                "reply": _state["spoken"],
                "actions": [],
                "detail": f"Local model not ready: {exc}",
            }
        ), 503


@app.route("/api/pair_token", methods=["GET"])
def pair_token_endpoint():
    """Returns the pairing token so the web UI can authenticate."""
    return jsonify({"token": PAIR_TOKEN, "host": "phone"})


@app.route("/api/phone_cmd", methods=["POST", "OPTIONS"])
def phone_cmd():
    """
    Web UI → phone command bridge.
    Body: {"token": "XXXXXXXX", "type": "...", ...}
    Types: open_app, open_url, termux, toast, keyevent, input_text
    Returns immediately; command is queued for the overlay to pick up.
    """
    if request.method == "OPTIONS":
        return jsonify({}), 200
    data = request.get_json(silent=True) or {}
    token = data.get("token", "")
    if token != PAIR_TOKEN:
        return jsonify({"error": "invalid token"}), 403

    cmd_type = data.get("type", "")
    allowed = {
        "open_app",
        "open_url",
        "termux",
        "toast",
        "keyevent",
        "input_text",
        "volume_up",
        "volume_down",
        "play_pause",
        "next_track",
        "prev_track",
        "back",
        "home",
        "scroll_up",
        "scroll_down",
    }
    if cmd_type not in allowed:
        return jsonify({"error": f"unknown type: {cmd_type}"}), 400

    _pending_commands.append(data)
    logger.info(f"Queued phone_cmd: {cmd_type}")
    return jsonify({"queued": True, "type": cmd_type})


# ── Transcript buffer ─────────────────────────────────────────────────────────
_transcript: collections.deque = collections.deque(maxlen=200)


def _ts() -> str:
    """Return current timestamp as HH:MM:SS string."""
    import datetime

    return datetime.datetime.now().strftime("%H:%M:%S")


def record_transcript(is_user: bool, text: str):
    """Record a message to the in-memory transcript buffer."""
    if not text or not text.strip():
        return
    _transcript.append(
        {
            "role": "user" if is_user else "lilly",
            "text": text.strip(),
            "time": _ts(),
        }
    )


@app.route("/api/transcript", methods=["GET"])
def get_transcript():
    """Return the conversation transcript as JSON."""
    return jsonify({"transcript": list(_transcript)})


@app.route("/api/transcript/clear", methods=["POST"])
def clear_transcript():
    """Clear the transcript buffer."""
    _transcript.clear()
    return jsonify({"cleared": True})


@app.route("/api/transcript/add", methods=["POST"])
def add_transcript_entry():
    """Called by the WebView overlay to record messages into the server-side transcript."""
    data = request.get_json(silent=True) or {}
    is_user = bool(data.get("is_user", True))
    text = str(data.get("text", "")).strip()
    if text:
        record_transcript(is_user, text)
    return jsonify({"ok": True})


@app.route("/api/browser_mic", methods=["POST"])
def browser_mic():
    """Receive audio from the browser's mic and run speech-to-text."""
    body = request.get_data()
    if len(body) < 100:
        logger.info("browser_mic: silence (body %d bytes)", len(body))
        return jsonify({"text": "", "is_silence": True})

    import tempfile
    import subprocess

    wav_path = Path(tempfile.gettempdir()) / "browser_mic.wav"
    wav_path.write_bytes(body)

    try:
        # Try whisper_stt from lilly_ai module first, fall back to local whisper
        try:
            import sys as _sys

            if str(WORKSPACE) not in _sys.path:
                _sys.path.insert(0, str(WORKSPACE))
            from lilly_ai import whisper_stt as _whisper_stt

            async def _do_stt():
                return await _whisper_stt(file_path=wav_path)

            text = _run_async(_do_stt())
        except ImportError:
            # Fallback: try local whisper binary
            result = subprocess.run(
                [
                    "whisper",
                    "--model",
                    "tiny",
                    "--language",
                    "en",
                    "--outputformat",
                    "txt",
                    str(wav_path),
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
            text = result.stdout.strip()

        text = text.strip()
        if not text:
            logger.info("browser_mic: empty transcription, treating as silence")
            return jsonify({"text": "", "is_silence": True})

        logger.info(f"browser_mic: whisper said '{text}' ({len(body)} bytes)")
        _state["heard"] = text
        record_transcript(True, text)
        return jsonify({"text": text, "is_silence": False})

    except Exception as e:
        logger.error(f"browser_mic: failed: {e}")
        return jsonify({"error": str(e), "text": "", "is_silence": True}), 500


@app.route("/api/status", methods=["GET"])
def status():
    """Server health and configuration status."""
    return jsonify(
        {
            "server": "lilly_phone_server",
            "version": "3.5",
            "llama_available": _llama_available(),
            "llama_url": LLAMA_URL,
            "pair_token": PAIR_TOKEN,
            "port": 8099,
            "bt_profiles_loaded": _BT_PROFILES_LOADED,
        }
    )


@app.route("/api/termux/get_battery", methods=["GET"])
def get_battery_endpoint():
    """Get battery information."""
    return jsonify(_run_async(get_battery()))


@app.route("/api/termux/get_wifi", methods=["GET"])
def get_wifi_endpoint():
    """Get WiFi information."""
    return jsonify(_run_async(get_wifi_scan()))


@app.route("/api/termux/get_location", methods=["GET"])
def get_location_endpoint():
    """Get location information."""
    return jsonify(_run_async(get_location()))


@app.route("/api/termux/list_sensors", methods=["GET"])
def list_sensors_endpoint():
    """List available sensors."""
    return jsonify(_run_async(list_sensors()))


@app.route("/api/termux/read_sensor", methods=["GET"])
def read_sensor_endpoint():
    """Read sensor data."""
    sensor_name = request.args.get("name", "")
    if sensor_name:
        return jsonify(_run_async(read_sensor(sensor_name)))
    return jsonify({"error": "sensor name required"}), 400


@app.route("/api/mic/data", methods=["GET"])
def mic_data_endpoint():
    """Get microphone audio data."""
    data = get_mic_data()
    if data:
        return jsonify(data)
    return jsonify({"error": "no audio data available"}), 404


@app.route("/api/notifications", methods=["GET"])
def notifications_endpoint():
    """Get recent notifications."""
    return jsonify(get_notifications())


@app.route("/api/calls", methods=["GET"])
def calls_endpoint():
    """Get call logs."""
    return jsonify(get_calls())


@app.route("/api/contacts", methods=["GET"])
def contacts_endpoint():
    """Get contacts."""
    return jsonify(get_contacts())


@app.route("/api/screen/capture", methods=["GET"])
def capture_screen_endpoint():
    """Capture and return screenshot."""
    image_data = capture_screen()
    if image_data:
        return jsonify(image_data)
    return jsonify({"error": "screen capture failed"}), 500


@app.route("/api/termux/local/proxy", methods=["POST"])
def local_proxy_endpoint():
    """Local Termux command execution for overlay - preferred over SSH."""
    try:
        command_json = request.get_data(as_text=True)
        result = run_termux_command(command_json)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/ui_state", methods=["GET"])
def ui_state_endpoint():
    """Get current UI state for polling. Returns pending commands for the overlay."""
    _state["timestamp"] = time.time()
    # Drain pending commands pushed from the web UI (8098)
    cmds = _pending_commands.copy()
    _pending_commands.clear()
    return jsonify({**_state, "pending_commands": cmds})


@app.route("/api/toggle_mic", methods=["POST"])
def toggle_mic_endpoint():
    """Toggle microphone state."""
    return jsonify({"active": False, "timestamp": time.time()})


@app.route("/api/tts", methods=["POST"])
def tts_endpoint():
    """Text-to-speech endpoint."""
    data = request.get_json() or {}
    return jsonify(
        {"url": f"/api/tts/audio?text={data.get('text', '')}", "timestamp": time.time()}
    )


@app.route("/api/tts/audio", methods=["GET"])
def tts_audio_endpoint():
    """Provide TTS audio file (mock)."""
    return jsonify(
        {"url": "/tmp/tts.mp3", "status": "mock_audio", "timestamp": time.time()}
    )


# ─── PRESENCE RADAR (who's around) ──────────────────────────────
_RADAR_CACHE = None
_RADAR_LAST_SCAN = 0.0
_RADAR_TTL = 30.0


def _radar_scan():
    """Scan Bluetooth locally via Termux and classify each device (offline)."""
    global _RADAR_CACHE, _RADAR_LAST_SCAN
    now = time.time()
    if _RADAR_CACHE and (now - _RADAR_LAST_SCAN) < _RADAR_TTL:
        return _RADAR_CACHE
    import subprocess

    devices = []
    try:
        out = subprocess.run(
            ["termux-bluetooth-scan"], timeout=15, capture_output=True, text=True
        )
        if out.stdout.strip():
            scan_data = json.loads(out.stdout)
            if isinstance(scan_data, list):
                for dev in scan_data:
                    devices.append(
                        {
                            "name": dev.get("name", "Unknown"),
                            "address": dev.get("address", ""),
                            "rssi": dev.get("rssi", -100),
                            "paired": False,
                        }
                    )
    except (json.JSONDecodeError, subprocess.TimeoutExpired, FileNotFoundError):
        pass
    try:
        out_p = subprocess.run(
            ["termux-bluetooth-paired"], timeout=5, capture_output=True, text=True
        )
        if out_p.stdout.strip():
            paired = json.loads(out_p.stdout)
            seen = {d["address"] for d in devices}
            if isinstance(paired, list):
                for dev in paired:
                    addr = dev.get("address", "")
                    if addr not in seen:
                        devices.append(
                            {
                                "name": dev.get("name", "Unknown"),
                                "address": addr,
                                "rssi": dev.get("rssi", -100),
                                "paired": True,
                            }
                        )
    except (json.JSONDecodeError, subprocess.TimeoutExpired, FileNotFoundError):
        pass

    result = classify_devices(devices)
    result["counts"] = result.get("counts", {})
    result["timestamp"] = now
    _RADAR_CACHE = result
    _RADAR_LAST_SCAN = now
    return result


@app.route("/api/radar", methods=["GET"])
def radar_endpoint():
    """Presence radar: nearby Bluetooth devices classified by MAC vendor +
    distance. Used by the overlay's radar popout. Works fully offline."""
    try:
        result = _radar_scan()
        return jsonify(result)
    except Exception as e:
        logger.error(f"Radar scan failed: {e}")
        return jsonify({"error": str(e), "devices": [], "counts": {}}), 500


@app.route("/api/radar/summary", methods=["GET"])
def radar_summary_endpoint():
    """Short spoken summary of who's around (for TTS without a full UI)."""
    result = _radar_scan()
    if format_summary is not None:
        summary = format_summary(result)
    else:
        counts = result.get("counts", {})
        total = len(result.get("devices", []))
        summary = f"I can see {total} Bluetooth devices nearby."
    return jsonify({"summary": summary, "counts": result.get("counts", {})})


@app.route("/overlay", methods=["GET"])
def overlay_endpoint():
    """Overlay HTML page - serves as WebSocket alternative."""
    html = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Lilly Overlay</title>
        <style>
            * { margin: 0; padding: 0; box-sizing: border-box; }
            body { margin: 0; padding: 0; background: transparent; overflow: visible; font-family: system-ui, sans-serif; width: 96px; height: 96px; }
            #container { width: 96px; height: 96px; position: relative; transition: all 0.3s; }
            #container.expanded { width: 96px; height: 300px; }
            body.expanded { width: 96px; height: 300px; overflow: hidden; }
            canvas { display: block; position: absolute; top: 0; left: 0; image-rendering: auto; z-index: 2; cursor: pointer; touch-action: none; }
            #chatBubble { display: none; position: absolute; bottom: 0; left: 0; right: 0; height: 200px; background: rgba(255,255,255,0.95); border-radius: 16px 16px 0 0; padding: 8px; flex-direction: column; z-index: 3; }
            #chatBubble.show { display: flex; }
            #chatMessages { flex: 1; overflow-y: auto; font-size: 11px; padding: 4px; color: #333; }
            #chatInputRow { display: flex; gap: 4px; padding: 4px 0; }
            #chatInput { flex: 1; border: 1px solid #ddd; border-radius: 8px; padding: 4px 8px; font-size: 11px; outline: none; }
            #sendBtn { border: none; background: #4ade80; color: #000; border-radius: 8px; padding: 4px 10px; font-size: 11px; cursor: pointer; font-weight: 600; }
            #avatarName { position: absolute; bottom: 8px; left: 50%; transform: translateX(-50%); font-size: 10px; color: rgba(255,255,255,0.7); z-index: 1; pointer-events: none; }
        </style>
    </head>
    <body>
    <div id="container">
        <canvas id="pupCanvas" width="96" height="96"></canvas>
        <div id="avatarName">Lilly</div>
        <div id="chatBubble">
            <div id="chatMessages"></div>
            <div id="chatInputRow">
                <input id="chatInput" type="text" placeholder="Say something..." />
                <button id="sendBtn">Send</button>
            </div>
        </div>
    </div>
    <script>
        const canvas = document.getElementById('pupCanvas');
        const ctx = canvas.getContext('2d');
        canvas.width = 96; canvas.height = 96;
        let currentAvatar = 'puppy';
        let mouthOpen = 0;
        let eyeOffset = 0;
        let chatInput = document.getElementById('chatInput');
        let sendBtn = document.getElementById('sendBtn');
        let chatMessages = document.getElementById('chatMessages');

        function drawPup() {
            ctx.clearRect(0, 0, 96, 96);
            ctx.fillStyle = '#FFE4B5';
            ctx.beginPath(); ctx.arc(48, 48, 38, 0, Math.PI * 2); ctx.fill();
            ctx.fillStyle = '#FF69B4';
            ctx.beginPath(); ctx.ellipse(30, 36, 10, 6, 0, 0, Math.PI * 2); ctx.fill();
            ctx.beginPath(); ctx.ellipse(66, 36, 10, 6, 0, 0, Math.PI * 2); ctx.fill();
            ctx.fillStyle = '#000';
            ctx.fillRect(38 + eyeOffset, 38, 8, 6);
            ctx.fillRect(52 + eyeOffset, 38, 8, 6);
            if (mouthOpen > 0.1) {
                ctx.strokeStyle = '#000'; ctx.lineWidth = 2;
                ctx.beginPath(); ctx.arc(48, 56, 12, 0, Math.PI); ctx.stroke();
            }
        }

        function animate() {
            eyeOffset = Math.sin(Date.now() / 1000 * Math.PI * 2) * 2;
            mouthOpen = Math.abs(Math.sin(Date.now() / 500));
            drawPup();
            requestAnimationFrame(animate);
        }
        animate();

        function addChat(text, who) {
            var div = document.createElement('div');
            div.style.cssText = 'margin:2px 0;padding:3px 6px;border-radius:8px;font-size:11px;max-width:90%;' +
                (who === 'user' ? 'background:#e3f2fd;margin-left:auto;text-align:right' : 'background:#f5f5f5');
            div.textContent = text;
            chatMessages.appendChild(div);
            chatMessages.scrollTop = chatMessages.scrollHeight;
        }

        async function sendMessage() {
            var text = chatInput.value.trim();
            if (!text) return;
            addChat(text, 'user');
            chatInput.value = '';
            try {
                var serverUrl = (typeof LillyBridge !== 'undefined' && LillyBridge.getServerUrl) ? LillyBridge.getServerUrl() : '';
                var resp = await fetch(serverUrl + '/api/cmd', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({text: text, avatar: currentAvatar})
                });
                var data = await resp.json();
                if (data.reply) addChat(data.reply, 'lilly');
            } catch(e) {
                addChat('(connection error)', 'lilly');
            }
        }

        sendBtn.addEventListener('click', sendMessage);
        chatInput.addEventListener('keydown', function(e) {
            if (e.key === 'Enter') sendMessage();
        });

        canvas.addEventListener('click', function() {
            if (typeof LillyBridge !== 'undefined' && LillyBridge.toggleExpand) {
                LillyBridge.toggleExpand();
            }
        });

        setInterval(function() {
            fetch('/api/ui_state').then(function(r){return r.json()}).then(function(s){
                if (s.spoken) addChat(s.spoken, 'lilly');
            }).catch(function(){});
        }, 3000);
    </script>
    </body>
    </html>
    """
    return Response(html, mimetype="text/html")


@app.errorhandler(404)
def not_found(error):
    return jsonify({"error": "Not found"}), 404


@app.errorhandler(500)
def internal_error(error):
    return jsonify({"error": "Internal server error"}), 500


if __name__ == "__main__":
    logger.info("Starting Lilly Phone Server on :8099")
    app.run(host="0.0.0.0", port=8099, debug=False, threaded=True)
