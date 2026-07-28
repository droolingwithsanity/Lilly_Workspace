#!/usr/bin/env python3
"""
Termux utility functions for Lilly AI and other services.

Handles Termux command execution and device control with proper async/await patterns.
"""
import asyncio
import json
import os
import subprocess
import time
import logging
from pathlib import Path
from typing import Optional, Tuple, List

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("TermuxUtils")

// ─── ASYNC TERMINUX EXECUTION ─────────────────────────────────────────────

async def termux_run(cmd_list, timeout=15):
    """Run Termux command with fallbacks for different execution modes."""
    try:
        env = os.environ.copy()
        env["TERMUX_APP_PACKAGE_NAME"] = "ai.agent1c.lillyoverlay"
        
        result = await asyncio.wait_for(
            asyncio.create_subprocess_exec(
                *cmd_list,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            ).communicate(),
            timeout=timeout,
        )
        
        stdout = result[0].decode() if result[0] else ""
        stderr = result[1].decode() if result[1] else ""
        
        return stdout.strip(), stderr.strip()
        
    except asyncio.TimeoutExpired:
        logger.debug(f"Termux command timeout: {' '.join(cmd_list)}")
        return "", f"Timeout: {' '.join(cmd_list)}"
        
    except Exception as e:
        logger.debug(f"Termux command failed: {' '.join(cmd_list)} - {e}")
        return "", str(e)

async def termux_exec(cmd_str, timeout=30):
    """Execute Termux shell command with advanced JSON parsing."""
    try:
        env = os.environ.copy()
        env["TERMUX_APP_PACKAGE_NAME"] = "ai.agent1c.lillyoverlay"
        
        result = await asyncio.wait_for(
            asyncio.create_subprocess_shell(
                cmd_str,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            ).communicate(),
            timeout=timeout,
        )
        
        stdout = result[0].decode() if result[0] else ""
        stderr = result[1].decode() if result[1] else ""
        
        return stdout.strip(), stderr.strip()
        
    except asyncio.TimeoutExpired:
        logger.debug(f"Termux shell timeout: {cmd_str[:200]}...")
        return "", f"Timeout: {cmd_str[:200]}..."
        
    except Exception as e:
        logger.debug(f"Termux shell failed: {e}")
        return "", str(e)

// ─── DEVICE CONTROL ──────────────────────────────────────────────────────

async def execute_command(cmd):
    """Execute Termux command with security checks."""
    if not cmd or len(cmd) > 100:
        return {"output": "Command rejected", "error": "Length exceeded"}
    
    dangerous_patterns = [
        ";", "&&", "||", "rm ", "mv ", "cp ", "chmod ", "tar ", 
        "adb ", "ssh ", "wget ", "curl ", "su ", "su -c"
    ]
    
    for pattern in dangerous_patterns:
        if pattern in cmd:
            return {"output": "Command rejected", "error": "Safety violation"}
    
    try:
        env = os.environ.copy()
        env["TERMUX_APP_PACKAGE_NAME"] = "ai.agent1c.lillyoverlay"
        
        result = await asyncio.wait_for(
            asyncio.create_subprocess_shell(
                cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            ).communicate(),
            timeout=10,
        )
        
        stdout = result[0].decode() if result[0] else ""
        stderr = result[1].decode() if result[1] else ""
        
        return {
            "output": stdout + ("\n" + stderr if stderr else ""),
            "exit_code": result.returncode
        }
        
    except asyncio.TimeoutExpired:
        return {"output": "", "error": "Timeout after 10s", "exit_code": -1}
        
    except Exception as e:
        return {"output": "", "error": str(e), "exit_code": -1}

// ─── SENSOR ACQUISITION ──────────────────────────────────────────────────

async def read_all_sensors():
    """Get all sensor data in a single termux-sensor call (non-blocking)."""
    cmd = ["termux-sensor", "-a", "-n", "1"]
    stdout, stderr = await termux_run(cmd, timeout=30)
    
    if not stdout:
        return {}
    
    if stdout.startswith(":"):
        stdout = stdout[1:]
    
    try:
        data = json.loads(stdout)
        result = {}
        for name, info in data.items():
            if isinstance(info, dict) and "values" in info:
                result[name] = info["values"]
            else:
                result[name] = info
        return result
    except json.JSONDecodeError:
        logger.debug(f"All sensors JSON decode failed: {stdout[:200]}")
        return {}
        
async def read_sensor(name):
    """Read a single sensor value using termux-sensor."""
    cmd = ["termux-sensor", "-s", name, "-n", "1"]
    stdout, stderr = await termux_run(cmd, timeout=15)
    
    if not stdout:
        return None
    
    if stdout.startswith(":"):
        stdout = stdout[1:]
        
    try:
        data = json.loads(stdout)
        return data.get(name, {}).get("values", [])
    except json.JSONDecodeError:
        logger.debug(f"Sensor {name} JSON decode failed: {stdout[:200]}")
        try:
            return [float(v) for v in stdout.split() if v]
        except:
            return None
        
async def list_sensors():
    """List all available sensors using termux-sensor -l."""
    cmd = ["termux-sensor", "-l"]
    stdout, stderr = await termux_run(cmd, timeout=10)
    
    if not stdout:
        return []
    
    try:
        data = json.loads(stdout)
        if isinstance(data, dict) and "sensors" in data:
            return data["sensors"]
    except json.JSONDecodeError:
        logger.debug(f"Sensor list JSON decode failed: {stdout[:200]}")
        
    return []

// ─── BLUETOOTH & NFC ─────────────────────────────────────────────────────

async def scan_bluetooth_devices():
    """Scan for nearby Bluetooth devices using Termux Bluetooth scan."""
    try:
        result = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: subprocess.run(
                ["termux-bluetooth-scan"],
                capture_output=True,
                text=True,
                timeout=30
            )
        )
        
        if result.returncode != 0:
            return []
        
        if not result.stdout.strip():
            return []
            
        devices = []
        try:
            scan_data = json.loads(result.stdout)
            if isinstance(scan_data, list):
                for dev in scan_data:
                    devices.append({
                        "name": dev.get("name", "Unknown"),
                        "address": dev.get("address", ""),
                        "rssi": dev.get("rssi", -100),
                        "paired": False,
                        "type": "scan"
                    })
        except json.JSONDecodeError:
            logger.debug("Bluetooth scan JSON decode failed")
            
        if not devices:
            paired = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: subprocess.run(
                    ["termux-bluetooth-paired"],
                    capture_output=True,
                    text=True,
                    timeout=10
                )
            )
            
            if paired.returncode == 0 and paired.stdout.strip():
                try:
                    paired_data = json.loads(paired.stdout)
                    if isinstance(paired_data, list):
                        paired_addresses = {d["address"] for d in devices}
                        for dev in paired_data:
                            addr = dev.get("address", "")
                            if addr not in paired_addresses:
                                devices.append({
                                    "name": dev.get("name", "Unknown"),
                                    "address": addr,
                                    "rssi": dev.get("rssi", -100),
                                    "paired": True,
                                    "type": "paired"
                                })
                except json.JSONDecodeError:
                    pass
                    
        return devices
        
    except Exception as e:
        logger.debug(f"Bluetooth scan error: {e}")
        return []

// ─── WRAPPER FUNCTIONS ──────────────────────────────────────────────────

async def get_battery():
    """Get battery status using termux-battery-status."""
    cmd = ["termux-battery-status"]
    stdout, stderr = await termux_run(cmd, timeout=10)
    
    if not stdout:
        return {}
    
    try:
        return json.loads(stdout)
    except json.JSONDecodeError:
        logger.debug(f"Battery JSON decode failed: {stdout[:200]}")
        return {}
        
async def get_location():
    """Get GPS location using termux-location."""
    cmd = ["termux-location"]
    stdout, stderr = await termux_run(cmd, timeout=20)
    
    if not stdout:
        return {}
    
    try:
        data = json.loads(stdout)
        return {
            "latitude": data.get("latitude", 0.0),
            "longitude": data.get("longitude", 0.0),
            "altitude": data.get("altitude", 0.0),
            "speed": data.get("speed", 0.0),
            "bearing": data.get("bearing", 0.0),
            "accuracy": data.get("accuracy", 0.0),
            "timestamp": time.time()
        }
    except json.JSONDecodeError:
        logger.debug(f"Location JSON decode failed: {stdout[:200]}")
        return {}
        
async def get_wifi_scan():
    """Scan for WiFi networks using termux-wifi-scaninfo."""
    cmd = ["termux-wifi-scaninfo"]
    stdout, stderr = await termux_run(cmd, timeout=30)
    
    if not stdout:
        return {"networks": [], "count": 0}
    
    try:
        data = json.loads(stdout)
        return {
            "networks": data,
            "count": len(data),
            "timestamp": time.time()
        }
    except json.JSONDecodeError:
        logger.debug(f"WiFi scan JSON decode failed: {stdout[:200]}")
        return {"networks": [], "count": 0}
        
async def get_notifications():
    """Get recent notifications using termux-notification-list."""
    cmd = ["termux-notification-list"]
    stdout, stderr = await termux_run(cmd, timeout=10)
    
    if not stdout:
        return {"notifications": []}
    
    try:
        return {"notifications": json.loads(stdout)}
    except json.JSONDecodeError:
        logger.debug(f"Notifications JSON decode failed: {stdout[:200]}")
        return {"notifications": []}
        
async def send_notification(title, content, priority="default"):
    """Send notification using termux-notification."""
    cmd = ["termux-notification", "-t", title, "-c", content, "--priority", priority]
    stdout, stderr = await termux_run(cmd, timeout=10)
    
    return {
        "sent": bool(stdout.strip()),
        "output": stdout + ("\n" + stderr if stderr else "")
    }

async def get_volume():
    """Get current volume levels using termux-volume."""
    cmd = ["termux-volume", "--dump"]
    stdout, stderr = await termux_run(cmd, timeout=10)
    
    if not stdout:
        return None
    
    result = {}
    for line in stdout.split("\n"):
        if "ROFI_VOLUME_MEDIA_" in line:
            result["volume"] = int(line.split("=")[1])
        elif "ROFI_VOLUME_RING_" in line:
            result["ringer_volume"] = int(line.split("=")[1])
        elif "ROFI_VOLUME_NOTIFICATION_" in line:
            result["notification_volume"] = int(line.split("=")[1])
            
    return result if result else None

async def set_volume(stream, level):
    """Set volume using termux-volume."""
    cmd_map = {"media": "media", "ring": "ring", "notification": "notification"}
    stream_key = cmd_map.get(stream, "media")
    cmd = ["termux-volume", "set", stream_key, str(level).strip()]
    
    stdout, stderr = await termux_run(cmd, timeout=10)
    
    return {
        "set": bool(stdout.strip()),
        "stream": stream,
        "level": level
    }

async def get_screen_brightness():
    """Get screen brightness using termux-brightness-get."""
    cmd = ["termux-brightness-get"]
    stdout, stderr = await termux_run(cmd, timeout=10)
    
    if not stdout:
        return None
    
    try:
        level = float(stdout.strip())
        return {
            "brightness": int(level * 100),
            "level": level,
            "success": True
        }
    except ValueError:
        logger.debug(f"Brightness decode failed: {stdout[:200]}")
        return None
        
async def set_screen_brightness(level):
    """Set screen brightness using termux-brightness-set."""
    brightness = max(0.0, min(1.0, float(level) / 100.0))
    cmd = ["termux-brightness-set", f"{brightness:.2f}"]
    
    stdout, stderr = await termux_run(cmd, timeout=10)
    
    return {
        "set": bool(stdout.strip()),
        "level": brightness,
        "success": True
    }

async def get_current_app():
    """Get current foreground app using dumpsys."""
    cmd = ["dumpsys", "activity", "recents", "--1"]
    
    result = await asyncio.wait_for(
        asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        ).communicate(),
        timeout=5,
    )
    
    stdout = result[0].decode() if result[0] else ""
    
    for line in stdout.split("\n"):
        if "ACTIVITY:" in line and "pid=" in line:
            try:
                activity_name = line.split("ACTIVITY: ")[1].split(" ")[0].split("/")[-1]
                if activity_name and len(activity_name) < 100:
                    return {"current_app": activity_name}
            except IndexError:
                continue
                
    return {"current_app": "unknown"}

async def close_all_apps():
    """Close all running apps using System UI settings."""
    cmd = ["am", "start", "-a", "android.settings.MANAGEMENT_ACTIONS", 
           "-n", "com.android.settings/.PerformanceActivity"]
    
    result = await asyncio.wait_for(
        asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        ).communicate(),
        timeout=5,
    )
    
    stdout = result[0].decode() if result[0] else ""
    
    return {
        "action": "force_stop_all",
        "action_triggered": bool(stdout.strip())
    }

async def launch_app(package_name):
    """Launch an Android app with package whitelist."""
    whitelist = [
        "com.whatsapp", "com.facebook", "com.instagram", "com.youtube", 
        "com.spotify", "com.discord", "com.slack", "com.google.android.gm",
        "com.android.calendar", "com.android.contacts", "com.android.browser",
        "com.android.phone", "com.android.settings", "com.android.camera"
    ]
    
    if package_name not in whitelist:
        return {"launched": False, "error": "Package not in whitelist"}
    
    cmd = ["am", "start", "-n", f"{package_name}/com.android.launcher.Main"]
    
    result = await asyncio.wait_for(
        asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        ).communicate(),
        timeout=5,
    )
    
    stdout = result[0].decode() if result[0] else ""
    
    return {
        "launched": bool(stdout.strip()),
        "package_name": package_name,
        "output": stdout
    }

// ─── PUBLIC INTERFACE ───────────────────────────────────────────────────\n
__all__ = [
    "termux_run", "termux_exec", "execute_command",
    "read_all_sensors", "read_sensor", "list_sensors",
    "scan_bluetooth_devices",
    "get_battery", "get_location", "get_wifi_scan",
    "get_notifications", "send_notification", "get_volume", "set_volume",
    "get_screen_brightness", "set_screen_brightness", "get_current_app", "close_all_apps",
    "launch_app"
]
