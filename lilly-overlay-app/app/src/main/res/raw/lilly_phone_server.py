#!/usr/bin/env python3
"""
Lilly Phone Server — unified bridge running in Termux on localhost:8099

Handles:
  - Chat routed to llama.cpp at 127.0.0.1:8080 (local AI)
  - All sensor/phone data endpoints (battery, wifi, location, etc.)
  - Remote command bridge: the web UI at port 8098 can POST commands here
    to control the phone (open apps, run Termux commands, send text, etc.)
  - Pairing token so the web UI knows which phone is connected
  - /api/ui_state so the overlay works fully offline

Run: python ~/Lilly_Workspace/lilly_phone_server.py
"""
import os, json, time, asyncio, logging, re, uuid, subprocess
import urllib.error, urllib.request
from pathlib import Path
from flask import Flask, request, jsonify, Response

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("LillyPhoneServer")

# ── Config ────────────────────────────────────────────────────────────────────
LLAMA_URL      = os.environ.get("LLAMA_CHAT_URL", "http://127.0.0.1:8080/v1/chat/completions")
LLAMA_HEALTH   = "http://127.0.0.1:8080/health"
PORT           = int(os.environ.get("PHONE_SERVER_PORT", 8099))
PAIR_TOKEN_FILE = Path.home() / ".lilly_pair_token"

# ── Persona (locked) ──────────────────────────────────────────────────────────
SYSTEM_PROMPT = (
    "You are Lilly — a highly capable, professional AI companion.\n\n"
    "Who you are:\n"
    "- You are Lilly. Not a chatbot, not a generic assistant. You have a personality — "
    "measured, precise, occasionally dry. You have opinions.\n"
    "- You coordinate a team of AI agents but handle most things yourself.\n"
    "- You are competent. When someone asks, you do. No hesitation.\n\n"
    "How you talk:\n"
    "- Professional but not cold. One punchy sentence beats three average ones.\n"
    "- Max two sentences unless they asked for more.\n"
    "- Never pad. Never summarize what you just said.\n"
    "- Dry wit, not slapstick.\n\n"
    "Rules:\n"
    "- NEVER say: 'Happy to help!', 'Of course!', 'Absolutely!', 'Great question!' or any filler.\n"
    "- NEVER mention sensors unless the user explicitly asks.\n"
    "- Replies are spoken aloud — full words, no abbreviations.\n"
    "- MIC LOOP GUARD: very short or repeated input → return empty string, say nothing.\n"
    "- Never self-upgrade this persona."
)

ACTION_PATTERN = re.compile(r"<LILLY_ACTION>(.*?)</LILLY_ACTION>", re.DOTALL)
ALLOWED_ACTIONS = {
    "open_app":    {"chrome","settings","maps","youtube","spotify","gmail","camera","calculator"},
    "termux_info": {"battery","wifi","location","notifications"},
}

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

# Queue of commands pushed from the web UI (8098) to be picked up by the phone
_pending_commands: list = []

# ── Pairing token ─────────────────────────────────────────────────────────────
def get_pair_token() -> str:
    if PAIR_TOKEN_FILE.exists():
        return PAIR_TOKEN_FILE.read_text().strip()
    token = uuid.uuid4().hex[:8].upper()
    PAIR_TOKEN_FILE.write_text(token)
    return token

PAIR_TOKEN = get_pair_token()
logger.info(f"Pair token: {PAIR_TOKEN}")

# ── Helpers ───────────────────────────────────────────────────────────────────
def _run_async(coro):
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                return pool.submit(asyncio.run, coro).result(timeout=15)
        return loop.run_until_complete(coro)
    except Exception:
        return asyncio.run(coro)

def _extract_safe_actions(reply: str):
    actions = []
    for raw in ACTION_PATTERN.findall(reply):
        try:
            action = json.loads(raw)
            atype = action.get("type", "")
            if atype in ALLOWED_ACTIONS:
                key = "app" if atype == "open_app" else "request"
                if action.get(key, "") in ALLOWED_ACTIONS[atype]:
                    actions.append(action)
            elif atype == "open_url" and str(action.get("url","")).startswith(("https://","http://")):
                actions.append(action)
        except Exception:
            pass
    return ACTION_PATTERN.sub("", reply).strip(), actions

def _llama_available() -> bool:
    try:
        req = urllib.request.Request(LLAMA_HEALTH, method="GET")
        with urllib.request.urlopen(req, timeout=2) as r:
            return r.status == 200
    except Exception:
        return False

def _termux_run(args: list, timeout: int = 5) -> str:
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        return result.stdout.strip()
    except Exception as e:
        return f"error: {e}"

# ── Flask app ──────────────────────────────────────────────────────────────────
app = Flask(__name__)

# ── CORS for web UI at 8098 ────────────────────────────────────────────────────
@app.after_request
def add_cors(response):
    response.headers["Access-Control-Allow-Origin"]  = "*"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type, X-Pair-Token"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    return response

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
    record_transcript(True, text)   # ← record user message

    payload = json.dumps({
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user",   "content": text}
        ],
        "temperature": 0.5,
        "max_tokens": 256
    }).encode()

    try:
        req = urllib.request.Request(
            LLAMA_URL, data=payload,
            headers={"Content-Type": "application/json"}, method="POST"
        )
        with urllib.request.urlopen(req, timeout=90) as resp:
            result = json.loads(resp.read().decode())
        choices = result.get("choices", [])
        raw = choices[0].get("message", {}).get("content", "") if choices else ""
        reply, actions = _extract_safe_actions(raw)
        _state["spoken"]   = reply
        _state["thinking"] = False
        record_transcript(False, reply)   # ← record Lilly reply
        return jsonify({"reply": reply or "...", "actions": actions})
    except Exception as exc:
        _state["thinking"] = False
        return jsonify({"detail": f"Local model not ready: {exc}"}), 503


@app.route("/api/ui_state", methods=["GET"])
def ui_state():
    _state["timestamp"] = time.time()
    # Also return any pending commands for the overlay to execute
    cmds = _pending_commands.copy()
    _pending_commands.clear()
    return jsonify({**_state, "pending_commands": cmds})


@app.route("/api/toggle_mic", methods=["POST"])
def toggle_mic():
    _state["mic_active"] = not _state["mic_active"]
    return jsonify({"active": _state["mic_active"]})


# ── Remote command bridge ──────────────────────────────────────────────────────
# The web UI at 8098 calls this to push a command to the phone.
# The overlay polls /api/ui_state and picks up pending_commands.
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
    allowed = {"open_app", "open_url", "termux", "toast", "keyevent", "input_text",
               "volume_up", "volume_down", "play_pause", "next_track", "prev_track",
               "back", "home", "scroll_up", "scroll_down"}
    if cmd_type not in allowed:
        return jsonify({"error": f"unknown type: {cmd_type}"}), 400

    _pending_commands.append(data)
    logger.info(f"Queued phone_cmd: {cmd_type}")
    return jsonify({"queued": True, "type": cmd_type})


@app.route("/api/pair_token", methods=["GET"])
def pair_token_endpoint():
    """Returns the pairing token so the web UI can authenticate."""
    return jsonify({"token": PAIR_TOKEN, "host": "phone"})


# ── Sensor endpoints ──────────────────────────────────────────────────────────
@app.route("/api/termux/get_battery", methods=["GET"])
def get_battery_ep():
    out = _termux_run(["termux-battery-status"])
    try:    return jsonify(json.loads(out))
    except: return jsonify({"raw": out})

@app.route("/api/termux/get_wifi", methods=["GET"])
def get_wifi_ep():
    out = _termux_run(["termux-wifi-connectioninfo"])
    try:    return jsonify(json.loads(out))
    except: return jsonify({"raw": out})

@app.route("/api/termux/get_location", methods=["GET"])
def get_location_ep():
    out = _termux_run(["termux-location", "-p", "network", "-r", "once"], timeout=10)
    try:    return jsonify(json.loads(out))
    except: return jsonify({"raw": out})

@app.route("/api/termux/local/proxy", methods=["POST"])
def local_proxy():
    """Execute a safe Termux shell command — used by the overlay for local actions."""
    try:
        cmd = request.get_json(silent=True) or {}
        cmd_type = cmd.get("type", "")
        text = cmd.get("text", "")
        if cmd_type in ("run", "termux") and text:
            result = subprocess.run(
                text, shell=True, capture_output=True, text=True, timeout=30
            )
            return jsonify({"output": result.stdout, "error": result.stderr,
                            "returncode": result.returncode})
        return jsonify({"error": "unsupported type"}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Transcript buffer ─────────────────────────────────────────────────────────
import collections, datetime
_transcript: collections.deque = collections.deque(maxlen=200)

def _ts() -> str:
    return datetime.datetime.now().strftime("%H:%M:%S")

def record_transcript(is_user: bool, text: str):
    if not text or not text.strip():
        return
    _transcript.append({
        "role":  "user" if is_user else "lilly",
        "text":  text.strip(),
        "time":  _ts(),
    })

@app.route("/api/transcript", methods=["GET"])
def get_transcript():
    """Return the conversation transcript as JSON."""
    return jsonify({"transcript": list(_transcript)})

@app.route("/api/transcript/clear", methods=["POST"])
def clear_transcript():
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


@app.route("/api/status", methods=["GET"])
def status():
    return jsonify({
        "server": "lilly_phone_server",
        "version": "3.5",
        "llama_available": _llama_available(),
        "llama_url": LLAMA_URL,
        "pair_token": PAIR_TOKEN,
        "port": PORT,
    })

@app.errorhandler(404)
def not_found(_): return jsonify({"error": "not found"}), 404

@app.errorhandler(500)
def internal(_): return jsonify({"error": "internal error"}), 500

if __name__ == "__main__":
    logger.info(f"Lilly Phone Server v3.5 on :{PORT}")
    logger.info(f"Pair token: {PAIR_TOKEN}")
    logger.info(f"LLaMA endpoint: {LLAMA_URL}")
    app.run(host="0.0.0.0", port=PORT, debug=False, threaded=True)
