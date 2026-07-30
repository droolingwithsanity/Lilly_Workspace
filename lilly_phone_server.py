#!/usr/bin/env python3
"""
Lilly Phone Server — Flask/HTTP server running in Termux on localhost:8099

Provides lightweight API for the overlay to access phone capabilities.
All endpoints use synchronous wrappers around async termux_utils.py.

Core Dependencies:
- Flask (web framework)
- termux_utils.py (Termux system utilities)
- json, time, pathlib, typing
"""
import os
import json
import time
import asyncio
import logging
from pathlib import Path
from typing import Optional
from flask import Flask, request, jsonify, Response

from termux_utils import get_battery, get_wifi_scan, get_location, list_sensors, read_sensor

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("LillyPhoneServer")

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
        subprocess.run([
            "termux-audio-recorder", "-r", "raw",
            "-c", "1", "-b", "320000", "-p", "16000", "-d", "1",
            "-f", str(output_file)
        ], timeout=3, capture_output=True)
        if output_file.exists():
            with open(output_file, "rb") as f:
                audio_data = f.read()
            output_file.unlink()
            return {
                "audio_base64": audio_data.hex(),
                "format": "raw16khz_mono32kbps",
                "duration_ms": 1000,
                "timestamp": time.time()
            }
    except Exception as e:
        logger.debug("Mic capture failed: %s", e)
    return None

def get_notifications():
    """List recent notifications using termux-notification-list."""
    try:
        import subprocess
        result = subprocess.run(
            ["termux-notification-list"],
            timeout=2,
            capture_output=True,
            text=True
        )
        if result.returncode == 0 and result.stdout:
            lines = result.stdout.strip().split('\n')
            notifications = []
            for line in lines:
                if line.strip():
                    parts = [p.strip() for p in line.split('|')]
                    if len(parts) >= 3:
                        notifications.append({
                            "title": parts[0],
                            "text": parts[1],
                            "timestamp": parts[2] if len(parts) > 2 else ""
                        })
            return notifications[:10]
    except Exception as e:
        logger.debug("Failed to get notifications: %s", e)
    return []

def get_calls():
    """Get call logs using termux-call-log-reader."""
    try:
        import subprocess
        result = subprocess.run(
            ["termux-call-log-reader"],
            timeout=2,
            capture_output=True,
            text=True
        )
        if result.returncode == 0 and result.stdout:
            lines = result.stdout.strip().split('\n')
            calls = []
            for line in lines:
                if line.strip():
                    parts = [p.strip() for p in line.split('|')]
                    if len(parts) >= 4:
                        calls.append({
                            "name": parts[0],
                            "number": parts[1],
                            "date": parts[2],
                            "type": parts[3]
                        })
            return calls[:20]
    except Exception as e:
        logger.debug("Failed to get calls: %s", e)
    return []

def get_contacts():
    """Get contacts from termux-contact-picker."""
    try:
        import subprocess
        result = subprocess.run(
            ["termux-contact-picker"],
            timeout=2,
            capture_output=True,
            text=True
        )
        if result.returncode == 0 and result.stdout:
            lines = result.stdout.strip().split('\n')
            contacts = []
            for line in lines:
                if line.strip():
                    parts = [p.strip() for p in line.split('|')]
                    if len(parts) >= 3:
                        contacts.append({
                            "name": parts[0],
                            "phone": parts[1],
                            "email": parts[2] if len(parts) > 2 else ""
                        })
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
            ["termux-screencap", "-p", str(output_file)],
            timeout=5,
            capture_output=True
        )
        if output_file.exists():
            with open(output_file, "rb") as f:
                image_data = f.read()
            output_file.unlink()
            return {
                "image_base64": image_data.hex(),
                "format": "png",
                "timestamp": time.time()
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
                    text,
                    shell=True,
                    timeout=30,
                    capture_output=True,
                    text=True
                )
                return {
                    "output": result.stdout,
                    "error": result.stderr,
                    "returncode": result.returncode,
                    "timestamp": time.time()
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

@app.route('/api/termux/get_battery', methods=['GET'])
def get_battery_endpoint():
    """Get battery information."""
    return jsonify(_run_async(get_battery()))

@app.route('/api/termux/get_wifi', methods=['GET'])
def get_wifi_endpoint():
    """Get WiFi information."""
    return jsonify(_run_async(get_wifi_scan()))

@app.route('/api/termux/get_location', methods=['GET'])
def get_location_endpoint():
    """Get location information."""
    return jsonify(_run_async(get_location()))

@app.route('/api/termux/list_sensors', methods=['GET'])
def list_sensors_endpoint():
    """List available sensors."""
    return jsonify(_run_async(list_sensors()))

@app.route('/api/termux/read_sensor', methods=['GET'])
def read_sensor_endpoint():
    """Read sensor data."""
    sensor_name = request.args.get('name', '')
    if sensor_name:
        return jsonify(_run_async(read_sensor(sensor_name)))
    return jsonify({"error": "sensor name required"}), 400

@app.route('/api/mic/data', methods=['GET'])
def mic_data_endpoint():
    """Get microphone audio data."""
    data = get_mic_data()
    if data:
        return jsonify(data)
    return jsonify({"error": "no audio data available"}), 404

@app.route('/api/notifications', methods=['GET'])
def notifications_endpoint():
    """Get recent notifications."""
    return jsonify(get_notifications())

@app.route('/api/calls', methods=['GET'])
def calls_endpoint():
    """Get call logs."""
    return jsonify(get_calls())

@app.route('/api/contacts', methods=['GET'])
def contacts_endpoint():
    """Get contacts."""
    return jsonify(get_contacts())

@app.route('/api/screen/capture', methods=['GET'])
def capture_screen_endpoint():
    """Capture and return screenshot."""
    image_data = capture_screen()
    if image_data:
        return jsonify(image_data)
    return jsonify({"error": "screen capture failed"}), 500

@app.route('/api/termux/local/proxy', methods=['POST'])
def local_proxy_endpoint():
    """Local Termux command execution for overlay - preferred over SSH."""
    try:
        command_json = request.get_data(as_text=True)
        result = run_termux_command(command_json)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/ui_state', methods=['GET'])
def ui_state_endpoint():
    """Get current UI state for polling."""
    return jsonify({
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
        "timestamp": time.time()
    })

@app.route('/api/toggle_mic', methods=['POST'])
def toggle_mic_endpoint():
    """Toggle microphone state."""
    return jsonify({"active": False, "timestamp": time.time()})

@app.route('/api/tts', methods=['POST'])
def tts_endpoint():
    """Text-to-speech endpoint."""
    data = request.get_json() or {}
    return jsonify({
        "url": f"/api/tts/audio?text={data.get('text', '')}",
        "timestamp": time.time()
    })

@app.route('/api/tts/audio', methods=['GET'])
def tts_audio_endpoint():
    """Provide TTS audio file (mock)."""
    return jsonify({
        "url": "/tmp/tts.mp3",
        "status": "mock_audio",
        "timestamp": time.time()
    })

@app.route('/overlay', methods=['GET'])
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
    return Response(html, mimetype='text/html')

@app.errorhandler(404)
def not_found(error):
    return jsonify({"error": "Not found"}), 404

@app.errorhandler(500)
def internal_error(error):
    return jsonify({"error": "Internal server error"}), 500

if __name__ == '__main__':
    logger.info("Starting Lilly Phone Server on :8099")
    app.run(host='0.0.0.0', port=8099, debug=False, threaded=True)
