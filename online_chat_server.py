#!/usr/bin/env python3
"""
Online AI Chat Server — mic + sensors + online LLM.

No local LLM required. Uses an OpenAI-compatible API endpoint.

Run:
  pip install fastapi uvicorn httpx python-multipart
  python online_chat_server.py --port 8098

Environment:
  OPENAI_API_KEY      API key for the online LLM
  OPENAI_BASE_URL     Base URL (default: https://api.openai.com/v1)
  OPENAI_MODEL        Model name (default: gpt-4o-mini)
  SENSOR_SERVER_URL   Termux sensor server (default: http://100.115.234.87:8099)
"""

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, HTMLResponse
import httpx

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("OnlineChat")

# ─── Configuration ───────────────────────────────────────────────

PORT = int(os.environ.get("PORT", "8098"))
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
OPENAI_BASE_URL = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip(
    "/"
)
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
SENSOR_SERVER_URL = os.environ.get("SENSOR_SERVER_URL", "http://100.115.234.87:8099")

# ─── Simple in-memory chat history ───────────────────────────────

chat_history: Dict[str, List[Dict[str, Any]]] = {}
MAX_HISTORY = 20

# ─── Sensor client ───────────────────────────────────────────────

_sensor_client: Optional[httpx.AsyncClient] = None


async def get_sensor_client() -> httpx.AsyncClient:
    global _sensor_client
    if _sensor_client is None or _sensor_client.is_closed:
        _sensor_client = httpx.AsyncClient(
            timeout=10.0,
            limits=httpx.Limits(max_keepalive_connections=4, max_connections=8),
        )
    return _sensor_client


# ─── Online LLM client ───────────────────────────────────────────


async def online_chat(messages: List[Dict[str, str]], model: str = OPENAI_MODEL) -> str:
    """Send chat messages to an OpenAI-compatible API and return the reply text."""
    if not OPENAI_API_KEY:
        return "Error: OPENAI_API_KEY is not set. Please configure your API key."

    client = await get_sensor_client()
    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "messages": messages,
        "max_tokens": 500,
        "temperature": 0.7,
    }
    try:
        resp = await client.post(
            f"{OPENAI_BASE_URL}/chat/completions",
            headers=headers,
            json=payload,
            timeout=30.0,
        )
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"]
    except Exception as exc:
        logger.error(f"Online chat failed: {exc}")
        return f"Error: {exc}"


# ─── FastAPI App ─────────────────────────────────────────────────

app = FastAPI(title="Online AI Chat", version="1.0.0")


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "backend": "online",
        "model": OPENAI_MODEL,
        "sensor_server": SENSOR_SERVER_URL,
    }


@app.get("/api/sensor_proxy")
async def sensor_proxy(path: str = ""):
    """Proxy requests to the Termux sensor server."""
    if not path:
        path = "/health"
    client = await get_sensor_client()
    try:
        url = f"{SENSOR_SERVER_URL}{path}"
        resp = await client.get(url, timeout=5.0)
        return JSONResponse(content=resp.json(), status_code=resp.status_code)
    except Exception as exc:
        return JSONResponse(status_code=502, content={"error": str(exc)})


@app.post("/api/chat")
async def chat_endpoint(request: Request):
    """Send a text message and get an online AI response."""
    body = await request.json()
    text = body.get("text", "").strip()
    user_id = body.get("user_id", "default")
    if not text:
        return JSONResponse(status_code=400, content={"error": "Missing 'text'"})

    history = chat_history.setdefault(user_id, [])
    history.append({"role": "user", "content": text})
    if len(history) > MAX_HISTORY:
        del history[: len(history) - MAX_HISTORY]

    reply = await online_chat(history)
    history.append({"role": "assistant", "content": reply})
    return {"reply": reply, "history": history}


@app.post("/api/browser_mic")
async def browser_mic_upload(request: Request):
    """Receive audio from browser mic, transcribe with Whisper, then chat."""
    body = await request.body()
    if len(body) < 500:
        return {"status": "silence"}

    # Save audio
    wav_path = Path("/tmp/browser_mic.wav")
    wav_path.write_bytes(body)

    text = ""
    try:
        # Try OpenAI Whisper API if key is available
        if OPENAI_API_KEY:
            client = await get_sensor_client()
            with open(wav_path, "rb") as f:
                resp = await client.post(
                    f"{OPENAI_BASE_URL}/audio/transcriptions",
                    headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
                    files={"file": ("audio.wav", f, "audio/wav")},
                    data={"model": "whisper-1"},
                    timeout=30.0,
                )
                if resp.status_code == 200:
                    text = str(resp.json().get("text", ""))
        else:
            # Fallback: try local whisper if available
            try:
                import whisper as _whisper

                model = _whisper.load_model("base")
                result = model.transcribe(str(wav_path))
                text = str(result.get("text", ""))
            except Exception:
                text = ""
    except Exception as exc:
        logger.error(f"Whisper failed: {exc}")
    finally:
        wav_path.unlink(missing_ok=True)

    if not text or len(text.strip()) <= 2:
        return {"status": "silence"}

    # Chat with the transcribed text
    history = chat_history.setdefault("mic", [])
    history.append({"role": "user", "content": text})
    if len(history) > MAX_HISTORY:
        history[:] = history[-MAX_HISTORY:]

    reply = await online_chat(history)
    history.append({"role": "assistant", "content": reply})
    return {
        "status": "ok",
        "heard": text,
        "reply": reply,
    }


@app.get("/api/chat/history")
async def get_history(user_id: str = "default"):
    return {"history": chat_history.get(user_id, [])}


@app.delete("/api/chat/history")
async def clear_history(user_id: str = "default"):
    chat_history.pop(user_id, None)
    return {"status": "cleared"}


# ─── Simple HTML UI ──────────────────────────────────────────────

HTML_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Online AI Chat</title>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      background: #0f172a;
      color: #e2e8f0;
      height: 100vh;
      display: flex;
      flex-direction: column;
    }
    .header {
      padding: 16px;
      background: #1e293b;
      border-bottom: 1px solid #334155;
      display: flex;
      justify-content: space-between;
      align-items: center;
    }
    .header h1 { font-size: 18px; font-weight: 600; }
    .status {
      font-size: 12px;
      padding: 4px 8px;
      border-radius: 4px;
      background: #059669;
    }
    .status.offline { background: #dc2626; }
    .chat-container {
      flex: 1;
      overflow-y: auto;
      padding: 16px;
      display: flex;
      flex-direction: column;
      gap: 12px;
    }
    .message {
      max-width: 80%;
      padding: 10px 14px;
      border-radius: 12px;
      line-height: 1.5;
      word-wrap: break-word;
    }
    .message.user {
      align-self: flex-end;
      background: #2563eb;
      color: white;
    }
    .message.assistant {
      align-self: flex-start;
      background: #1e293b;
      color: #e2e8f0;
    }
    .input-panel {
      padding: 12px;
      background: #1e293b;
      border-top: 1px solid #334155;
      display: flex;
      gap: 8px;
    }
    .input-panel input {
      flex: 1;
      padding: 12px;
      border-radius: 8px;
      border: 1px solid #475569;
      background: #0f172a;
      color: #e2e8f0;
      font-size: 16px;
    }
    .input-panel button {
      padding: 12px 20px;
      border-radius: 8px;
      border: none;
      background: #2563eb;
      color: white;
      font-size: 14px;
      cursor: pointer;
    }
    .mic-btn {
      background: #dc2626 !important;
    }
    .mic-btn.listening {
      background: #059669 !important;
      animation: pulse 1s infinite;
    }
    @keyframes pulse {
      0%, 100% { opacity: 1; }
      50% { opacity: 0.6; }
    }
    .sensors-bar {
      padding: 8px 16px;
      background: #0f172a;
      border-bottom: 1px solid #1e293b;
      display: flex;
      gap: 12px;
      overflow-x: auto;
      font-size: 12px;
    }
    .sensor-item {
      white-space: nowrap;
      padding: 4px 8px;
      background: #1e293b;
      border-radius: 4px;
    }
  </style>
</head>
<body>
  <div class="header">
    <h1>Online AI Chat</h1>
    <span id="status" class="status">Online</span>
  </div>
  <div class="sensors-bar" id="sensorsBar">
    <span class="sensor-item">Loading sensors...</span>
  </div>
  <div class="chat-container" id="chat"></div>
  <div class="input-panel">
    <input type="text" id="textInput" placeholder="Type a message..." autocomplete="off">
    <button onclick="sendText()">Send</button>
    <button id="micBtn" class="mic-btn" onclick="toggleMic()">Mic</button>
  </div>

  <script>
    const chat = document.getElementById('chat');
    const textInput = document.getElementById('textInput');
    const micBtn = document.getElementById('micBtn');
    let mediaRecorder = null;
    let micActive = false;

    function addMessage(role, text) {
      const div = document.createElement('div');
      div.className = 'message ' + role;
      div.textContent = text;
      chat.appendChild(div);
      chat.scrollTop = chat.scrollHeight;
    }

    async function sendText() {
      const text = textInput.value.trim();
      if (!text) return;
      textInput.value = '';
      addMessage('user', text);
      const res = await fetch('/api/chat', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({text}),
      });
      const data = await res.json();
      if (data.reply) addMessage('assistant', data.reply);
    }

    textInput.addEventListener('keypress', (e) => {
      if (e.key === 'Enter') sendText();
    });

    async function toggleMic() {
      if (micActive) {
        mediaRecorder?.stop();
        micActive = false;
        micBtn.textContent = 'Mic';
        micBtn.classList.remove('listening');
        return;
      }
      try {
        const stream = await navigator.mediaDevices.getUserMedia({audio: true});
        mediaRecorder = new MediaRecorder(stream);
        const chunks = [];
        mediaRecorder.ondataavailable = (e) => chunks.push(e.data);
        mediaRecorder.onstop = async () => {
          const blob = new Blob(chunks, {type: 'audio/webm'});
          addMessage('user', '[voice...]');
          const res = await fetch('/api/browser_mic', {
            method: 'POST',
            body: blob,
          });
          const data = await res.json();
          if (data.heard) addMessage('user', data.heard);
          if (data.reply) addMessage('assistant', data.reply);
          stream.getTracks().forEach(t => t.stop());
        };
        mediaRecorder.start();
        micActive = true;
        micBtn.textContent = 'Stop';
        micBtn.classList.add('listening');
      } catch (e) {
        alert('Mic access denied: ' + e.message);
      }
    }

    async function loadSensors() {
      try {
        const res = await fetch('/api/sensor_proxy?path=/sensors/all');
        const data = await res.json();
        const bar = document.getElementById('sensorsBar');
        if (data.sensors && Object.keys(data.sensors).length > 0) {
          bar.innerHTML = Object.entries(data.sensors)
            .slice(0, 8)
            .map(([k, v]) => `<span class="sensor-item">${k}: ${JSON.stringify(v).slice(0, 40)}</span>`)
            .join('');
        } else {
          bar.innerHTML = '<span class="sensor-item">No sensor data</span>';
        }
      } catch (e) {
        document.getElementById('sensorsBar').innerHTML = '<span class="sensor-item">Sensor server offline</span>';
      }
    }

    loadSensors();
    setInterval(loadSensors, 5000);
  </script>
</body>
</html>
"""


@app.get("/", response_class=HTMLResponse)
async def index():
    return HTMLResponse(content=HTML_PAGE)


# ─── Entry Point ─────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn

    parser = argparse.ArgumentParser(description="Online AI Chat Server")
    parser.add_argument("--port", type=int, default=PORT)
    parser.add_argument("--host", type=str, default="0.0.0.0")
    args = parser.parse_args()

    if not OPENAI_API_KEY:
        logger.warning("OPENAI_API_KEY not set. Online chat will return errors.")

    logger.info(f"Starting Online AI Chat on {args.host}:{args.port}")
    logger.info(f"LLM: {OPENAI_BASE_URL} ({OPENAI_MODEL})")
    logger.info(f"Sensors: {SENSOR_SERVER_URL}")

    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
