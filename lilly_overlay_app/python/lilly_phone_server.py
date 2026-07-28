#!/usr/bin/env python3
"""
Lilly Phone Server — Flask/HTTP server running in Termux on localhost:8099

Provides lightweight API for the overlay to access phone capabilities.
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

def get_mic_data():
    """Capture audio sample via Termux Open Recorder or ASR.
    
    For simplicity, returns a placeholder. In practice:
    - Use termux-audio-recorder -r raw -c 1 -b 320000 -p 12000 -d 1
    - Encode as base64 or return as file path
    """
    try:
        import subprocess
        # Capture 1 second, 16kHz, mono, 32kHz (optimal for Whisper)
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
                "audio_base64": audio_data.hex(),  # compact hex encoding
                "format": "raw16khz_mono32kbps",
                "duration_ms": 1000,
                "timestamp": time.time()
            }
    except Exception as e:
        logging.debug(f"Mic capture failed: {e}")
    return None

def get_notifications():
    """List recent notifications using termux-notification-list."""
    try:
        import subprocess
        result = subprocess.run(
            ["termux-notification-list"],
            capture_output=True, text=True, timeout=3
        )
        if result.returncode == 0 and result.stdout.strip():
            return {"notifications": json.loads(result.stdout)}
    except Exception as e:
        logging.debug(f"Notification list failed: {e}")
    return {"notifications": []}

def send_notification(title="", content="", priority="default"):
    """Send a notification via termux-notification command."""
    try:
        import subprocess
        cmd = ["termux-notification", "-t", title, "-c", content, "--priority", priority]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        return {
            "sent": result.returncode == 0,
            "output": result.stdout if result.returncode == 0 else result.stderr
        }
    except Exception as e:
        logging.debug(f"Send notification failed: {e}")
        return {"sent": False, "error": str(e)}

def execute_command(cmd):
    """Execute shell command via termux-exec or limited termux-sh.
    
    This is highly restricted for security. Only allow safe commands.
    """
    if not cmd or len(cmd) > 100 or any(x in cmd for x in ["rm-", "mv-", "mv", "dd", "nc", "ssh", "curl", "wget"]):
        return {"output": "Command rejected", "error": "Security restriction"}
    
    try:
        import subprocess
        env = os.environ.copy()
        env["TERMUX_APP_PACKAGE_NAME"] = "ai.agent1c.lillyoverlay"
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=10, env=env)
        output = result.stdout + result.stderr
        return {
            "output": output.strip(),
            "exit_code": result.returncode,
            "error": None if result.returncode == 0 else output
        }
    except subprocess.TimeoutExpired:
        return {"output": "", "error": "Command timed out"}
    except Exception as e:
        return {"output": "", "error": str(e)}

def get_screen_brightness():
    """Get current screen brightness using 'termux-brightness-get'."""
    try:
        import subprocess
        result = subprocess.run(["termux-brightness-get"], capture_output=True, text=True, timeout=3)
        if result.returncode == 0 and result.stdout.strip():
            level = int(float(result.stdout.strip()) * 100)
            return {"brightness": level, "level": level / 100.0}
    except Exception as e:
        logging.debug(f"Brightness get failed: {e}")
    return None

def set_screen_brightness(level):
    """Set screen brightness (0-100) using 'termux-brightness-set'."""
    try:
        import subprocess
        brightness = max(0.0, min(1.0, float(level) / 100.0))
        result = subprocess.run(
            ["termux-brightness-set", f"{brightness:.2f}"],
            capture_output=True, text=True, timeout=3
        )
        return {"set": result.returncode == 0, "level": brightness}
    except Exception as e:
        logging.debug(f"Brightness set failed: {e}")
        return {"set": False, "error": str(e)}

def get_volume():
    """Get current volume levels."""
    result = {}
    # Media volume
    try:
        import subprocess
        out = subprocess.run(["termux-volume", "--dump"], capture_output=True, text=True, timeout=3)
        if out.returncode == 0:
            lines = out.stdout.split('\n')
            for line in lines:
                if line.startswith("ROFI_VOLUME_MEDIA_"):
                    vol = int(line.split('=')[1])
                    result["volume"] = vol
                elif line.startswith("ROFI_VOLUME_RING_"):
                    vol = int(line.split('=')[1])
                    result["ringer_volume"] = vol
                elif line.startswith("ROFI_VOLUME_NOTIFICATION_"):
                    vol = int(line.split('=')[1])
                    result["notification_volume"] = vol
    except Exception as e:
        logging.debug(f"Volume get failed: {e}")
    return result if result else None

def set_volume(stream, level):
    """Set volume (0-100) for specific stream."""
    try:
        import subprocess
        cmd_map = {
            "media": "media",
            "ring": "ring",
            "notification": "notification"
        }
        stream_key = cmd_map.get(stream, "media")
        cmd = ["termux-volume", "set", stream_key, f"{level}"]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=3)
        return {"set": result.returncode == 0, "stream": stream, "level": level}
    except Exception as e:
        logging.debug(f"Volume set failed: {e}")
        return {"set": False, "error": str(e)}

def get_battery_info():
    """Get battery status using termux-battery-status."""
    return get_battery()

def get_device_info():
    """Get device identification info."""
    info = {}
    try:
        import platform
        info["device"] = platform.node()
        info["type"] = "termux"
        info["available"] = True
        
        import subprocess
        out = subprocess.run(["termux-version"], capture_output=True, text=True, timeout=3)
        if out.returncode == 0:
            info["version_output"] = out.stdout.strip()
            
        out = subprocess.run(["termux-os-release"], capture_output=True, text=True, timeout=3)
        if out.returncode == 0:
            info["os_release"] = out.stdout.strip()
            
    except Exception:
        pass
    return info

def scan_bluetooth():
    """Scan for nearby Bluetooth devices."""
    try:
        return get_bluetooth_scan()
    except Exception as e:
        logging.debug(f"Bluetooth scan failed: {e}")
    return {"devices": [], "count": 0}

def scan_wifi():
    """Scan for nearby WiFi networks."""
    return get_wifi_scan()

def launch_app(package_name):
    """Launch an Android app using am start.
    
    Only allow safe, whitelisted package names for security.
    """
    whitelist = ["com.whatsapp", "com.facebook", "com.instagram", "com.youtube", "com.spotify", "com.discord", "com.slack"]
    
    if package_name not in whitelist:
        return {"launched": False, "error": "Package not in whitelist"}
    
    try:
        import subprocess
        cmd = ["am", "start", "-n", f"{package_name}/com.android.launcher.Main"]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        return {
            "launched": result.returncode == 0,
            "package_name": package_name,
            "output": result.stdout if result.returncode == 0 else result.stderr
        }
    except Exception as e:
        logging.debug(f"App launch failed: {e}")
        return {"launched": False, "error": str(e)}

def get_current_app():
    """Get info about current foreground app."""
    try:
        import subprocess
        cmd = ["dumpsys", "activity", "recents", "--1"]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            output = result.stdout
            for line in output.split('\n'):
                if "ACTIVITY:" in line and "pid=" in line:
                    activity_name = line.split("ACTIVITY: ")[1].split(" ")[0].split("/")[-1]
                    if activity_name and len(activity_name) < 100:
                        return {"current_app": activity_name}
    except Exception as e:
        logging.debug(f"Current app failed: {e}")
    return {"current_app": "unknown"}

def close_all_apps():
    """Close all running apps (Android behavior)."""
    try:
        import subprocess
        cmd = ["am", "start", "-a", "android.settings.MANAGEMENT_ACTIONS", "-n", "com.android.settings/.PerformanceActivity"]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        return {"action": "force_stop_all", "action_triggered": result.returncode == 0}
    except Exception as e:
        logging.debug(f"Close all apps failed: {e}")
        return {"action": "force_stop_all", "error": str(e)}

# Flask app setup
app = Flask(__name__)

# ─── ENDPOINTS ─────────────────────────────────────────────────────

@app.route('/api/phone/commands/execute', methods=['POST'])
def execute():
    """Execute shell command."""
    cmd = request.form.get('cmd') or request.json.get('cmd') if request.is_json else None
    if not cmd:
        return jsonify({"error": "No command provided"}), 400
    
    logging.info(f"Phone server executing: {cmd[:50]}...")
    result = execute_command(cmd)
    return jsonify(result)

@app.route('/api/phone/sensors/all', methods=['GET'])
def get_sensors_all():
    """Get all sensor readings."""
    logging.info("Phone server sensors/all requested")
    result = {}
    sensors = list_sensors()
    for sensor in sensors[:20]:  # Limit to 20 most common
        val = read_sensor(sensor)
        if val is not None:
            result[sensor] = val
    
    return jsonify({
        "sensors": result,
        "timestamp": time.time(),
        "count": len(result),
        "source": "termux"
    })

@app.route('/api/phone/mic', methods=['POST'])
def capture_mic():
    """Capture audio sample."""
    logging.info("Phone server mic requested")
    result = get_mic_data()
    if result:
        return jsonify(result)
    return jsonify({"error": "Failed to capture audio"}), 500

@app.route('/api/phone/notifications/list', methods=['GET'])
def list_notifications():
    """Get notification list."""
    logging.info("Phone server notifications/list requested")
    return jsonify(get_notifications())

@app.route('/api/phone/notifications/send', methods=['POST'])
def send_notification():
    """Send notification."""
    logging.info("Phone server notifications/send requested")
    data = request.get_json() or {}
    result = send_notification(
        title=data.get('title', ''),
        content=data.get('content', ''),
        priority=data.get('priority', 'default')
    )
    return jsonify(result)

@app.route('/api/phone/volume', methods=['GET'])
def get_volume_info():
    """Get current volume levels."""
    logging.info("Phone server volume GET requested")
    result = get_volume()
    if result:
        return jsonify(result)
    return jsonify({"error": "Failed to get volume"}), 500

@app.route('/api/phone/volume', methods=['POST'])
def set_volume_level():
    """Set volume level."""
    logging.info("Phone server volume POST requested")
    data = request.get_json() or {}
    result = set_volume(
        stream=data.get('stream', 'media'),
        level=data.get('level', 50)
    )
    return jsonify(result)

@app.route('/api/phone/brightness', methods=['GET'])
def get_brightness():
    """Get current brightness level."""
    logging.info("Phone server brightness GET requested")
    result = get_screen_brightness()
    if result:
        return jsonify(result)
    return jsonify({"error": "Failed to get brightness"}), 500

@app.route('/api/phone/brightness', methods=['POST'])
def set_brightness_level():
    """Set brightness level."""
    logging.info("Phone server brightness POST requested")
    data = request.get_json() or {}
    result = set_screen_brightness(data.get('level', 50))
    return jsonify(result)

@app.route('/api/phone/battery', methods=['GET'])
def get_battery_status():
    """Get battery status."""
    logging.info("Phone server battery requested")
    result = get_battery_info()
    return jsonify(result)

@app.route('/api/phone/bluetooth', methods=['GET'])
def get_bluetooth_info():
    """Get Bluetooth scan info."""
    logging.info("Phone server bluetooth requested")
    return jsonify(scan_bluetooth())

@app.route('/api/phone/wi-fi', methods=['GET'])
def get_wifi_info():
    """Get WiFi scan info."""
    logging.info("Phone server wi-fi requested")
    return jsonify(scan_wifi())

@app.route('/api/phone/location', methods=['GET'])
def get_location_info():
    """Get GPS location."""
    logging.info("Phone server location requested")
    result = get_location()
    return jsonify(result)

@app.route('/api/phone/apps/launch', methods=['POST'])
def launch_app_endpoint():
    """Launch an app."""
    logging.info("Phone server apps/launch requested")
    data = request.get_json() or {}
    result = launch_app(data.get('package_name', ''))
    return jsonify(result)

@app.route('/api/phone/apps/current', methods=['GET'])
def get_current_app_endpoint():
    """Get current foreground app."""
    logging.info("Phone server apps/current requested")
    return jsonify(get_current_app())

@app.route('/api/phone/system/close_all', methods=['POST'])
def close_all_apps_endpoint():
    """Close all running apps."""
    logging.info("Phone server system/close_all requested")
    return jsonify(close_all_apps())

@app.route('/api/phone/health', methods=['GET'])
def health_check():
    """Server health check."""
    logging.info("Phone server health check requested")
    return jsonify({
        "status": "ok",
        "timestamp": time.time(),
        "service": "lilly-phone-server"
    })

@app.route('/api/termux/local/proxy', methods=['POST'])
def termux_local_proxy():
    """Proxy requests to Termux via local server."""
    data = request.get_json() or {}
    target_endpoint = data.get('endpoint')
    
    if not target_endpoint:
        return jsonify({"error": "No endpoint specified"}), 400
    
    # Only allow specific endpoints for security
    allowed_endpoints = [
        '/api/phone/sensors/all',
        '/api/phone/commands/execute',
        '/api/phone/mic',
        '/api/phone/notifications/list',
        '/api/phone/notifications/send',
        '/api/phone/volume',
        '/api/phone/brightness',
        '/api/phone/battery',
        '/api/phone/bluetooth',
        '/api/phone/wi-fi',
        '/api/phone/location',
        '/api/phone/apps/launch',
        '/api/phone/apps/current',
        '/api/phone/system/close_all',
        '/api/phone/health'
    ]
    
    if target_endpoint not in allowed_endpoints:
        return jsonify({"error": f"Endpoint not allowed: {target_endpoint}"}), 403
    
    logging.info(f"Proxy request to {target_endpoint}")
    
    # This is a simple proxy - in practice you'd need to handle the actual request
    # For now, just return the endpoint info
    return jsonify({
        "proxied_to": f"http://localhost:8099{target_endpoint}",
        "method": request.method,
        "timestamp": time.time()
    })

if __name__ == '__main__':
    print("Starting Lilly Phone Server on http://localhost:8099")
    print("Endpoints available:")
    print("  /api/phone/commands/execute   (POST)")
    print("  /api/phone/sensors/all       (GET)")
    print("  /api/phone/mic               (POST)")
    print("  /api/phone/notifications/list (GET)")
    print("  /api/phone/notifications/send (POST)")
    print("  /api/phone/volume             (GET, POST)")
    print("  /api/phone/brightness         (GET, POST)")
    print("  /api/phone/battery            (GET)")
    print("  /api/phone/bluetooth          (GET)")
    print("  /api/phone/wi-fi              (GET)")
    print("  /api/phone/location           (GET)")
    print("  /api/phone/apps/launch        (POST)")
    print("  /api/phone/apps/current       (GET)")
    print("  /api/phone/system/close_all   (POST)")
    print("  /api/phone/health             (GET)")
    print("  /api/termux/local/proxy       (POST)")
    
    app.run(host='0.0.0.0', port=8099, debug=False, threaded=True)