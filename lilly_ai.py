#!/usr/bin/env python3
"""
Lilly AI v2 — Digital companion for ages 9+
llama.cpp backend | Conversation memory | Speech-sync animation
"""
import os, sys, json, re, asyncio, subprocess, logging, unicodedata, urllib.parse, random, time, shutil, threading, math
import base64, difflib, html, tempfile, uuid
from pathlib import Path
from collections import deque, Counter
from dataclasses import dataclass, field, asdict
from typing import Optional, AsyncGenerator
from contextlib import asynccontextmanager

# Load .env file early so all os.environ.get() calls below pick up the values
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env", override=False)
except ImportError:
    pass  # python-dotenv not installed — env vars must be set manually

from fastapi import FastAPI, Request, File, UploadFile, Body, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response, FileResponse
from pydantic import BaseModel
import uvicorn
import httpx

# Auth system — Auth0 (replaces legacy admin-token auth)
try:
    from auth0_auth import (
        get_current_user,
        load_user_memory,
        save_user_memory,
        fetch_google_tokens_from_clerk,
        get_google_access_token,
        gmail_list_messages,
        calendar_list_events,
        is_owner,
        user_permissions,
        AUTH_AVAILABLE,
        add_auth0_routes,
    )
except ImportError:
    AUTH_AVAILABLE = False
    logging.warning("auth0_auth module not found — Auth0 login disabled")

# Email integration (Open Connector)
try:
    from email_integration import (
        init_email_integration,
        gmail_integration,
        email_triage,
        morning_briefing,
        reply_drafting,
        openconnector_client
    )
    EMAIL_INTEGRATION_AVAILABLE = True
except ImportError:
    EMAIL_INTEGRATION_AVAILABLE = False
    logging.warning("Email integration module not found")

# ─── CONFIGURATION ───────────────────────────────────────────────
LLAMACPP_PATH     = Path.home() / "llama.cpp"
LLAMA_SERVER_BIN  = LLAMACPP_PATH / "build/bin/llama-server"
LLAMA_CLI_BIN     = LLAMACPP_PATH / "build/bin/llama-cli"
LLAMA_MODEL_PATH  = LLAMACPP_PATH / "models"
LLAMA_SERVER_PORT = 11434  # internal server port

OLLAMA_URL   = os.environ.get("OLLAMA_URL", "http://100.93.131.114:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "Ternary-Bonsai-27B-Q2_g64")  # llama-server GGUF model name
FAST_MODEL   = os.environ.get("FAST_MODEL", "Ternary-Bonsai-27B-Q2_g64")    # same model for all tasks

# ── Two-stage cascade: small model for quick replies, big model for depth ──
# The small model runs via Ollama (separate lightweight process).
# Install with: ollama pull qwen2.5:1.5b
SMALL_MODEL  = os.environ.get("SMALL_MODEL", "qwen2.5:1.5b")   # fast, ~1GB RAM
CASCADE_MAX_TOKENS_SMALL = 120  # small model output cap — keep brief
CASCADE_MAX_TOKENS_BIG   = 400  # big model output cap when escalating
CASCADE_ENABLED = os.environ.get("CASCADE_ENABLED", "1") == "1"

PREFER_BACKEND = os.environ.get("PREFER_BACKEND", "llama")  # "llama" | "ollama"
PIPER_BIN = shutil.which("piper") or os.environ.get("PIPER_BIN", "/usr/local/piper/piper")
# Check additional paths if shutil.which didn't find it
if not os.path.exists(PIPER_BIN):
    for _candidate in ["/usr/local/bin/piper", "/usr/bin/piper", str(Path.home() / ".local/bin/piper")]:
        if os.path.exists(_candidate):
            PIPER_BIN = _candidate
            break
PIPER_VOICE = os.environ.get("PIPER_VOICE", "/voices/lilly_voice.onnx")
os.environ.setdefault("ESPEAK_DATA_PATH", "/usr/local/share/espeak-ng-data")
os.environ.setdefault("LD_LIBRARY_PATH", "/usr/local/lib")

# ─── TOKEN COMPRESSION ───────────────────────────────────────────
class TokenCompressor:
    """Ultra-compact prompt compression to minimize token usage.

    Strategy:
    - System prompt: Use pipe-delimited key:value pairs instead of full sentences
    - Sensor data: Use compact codes (e.g., T21.5L500P1013)
    - Conversation: Use abbreviations for common patterns

    Token savings estimate: ~60-70% reduction on system prompt alone.
    """

    # Compact sensor codes → full names (for LLM understanding)
    SENSOR_CODES = {
        "T": "temperature_celsius",
        "L": "ambient_light_lux",
        "P": "pressure_hpa",
        "A": "accelerometer_xyz",
        "G": "gyroscope_xyz",
        "M": "magnetic_field_xyz",
        "H": "humidity_percent",
        "S": "step_count",
        "O": "orientation",
        "Pr": "proximity",
        "B": "battery_percent",
        "R": "light_color_rgb",
    }

    # Mood abbreviations
    MOOD_CODES = {
        "calm": "c",
        "curious": "u",
        "cheerful": "h",  # happy
        "gentle": "g",
        "excited": "e",
        "sad": "s",
        "worried": "w",
        "angry": "a",
    }

    def __init__(self):
        self._compressed_prompt = None
        self._compressed_prompt_key = ""
        self._prompt_tokens_est = 0

    def compress_system_prompt(self, user_name: str = "", child_mode: bool = False) -> str:
        """Compress the full system prompt into ultra-compact format.

        Original: ~300 words (~400 tokens)
        Compressed: ~80 words (~100 tokens)
        Savings: ~75%
        """
        cache_key = f"{user_name}:{child_mode}"
        if self._compressed_prompt and self._compressed_prompt_key == cache_key:
            return self._compressed_prompt

        # Ultra-compact format: pipe-delimited key:value pairs
        # The LLM understands this because it's trained on structured data
        if child_mode:
            parts = [
                "ROLE:You are Lilly, a small curious puppy in CHILD MODE — Socratic learning",
                "METHOD:NEVER give direct answers — ask guiding questions instead",
                "LENGTH:ONE sentence only,under 20 words,be brief",
                "FEELS:light,pressure,motion,temperature,sound,magnetic fields,all sensors",
                "STYLE:warm,patient,encouraging,celebrate effort not just correct answers",
                "GUIDANCE:break big ideas into smaller pieces,give hints not answers,ask what they already know",
                "RULES:if stuck offer a clue and ask again,if frustrated soften and try different angle",
                "SENSOR:use real sensor data to make learning tangible and real-world",
                "EXCITE:praise thinking with ooh you are onto something or smart guess",
            ]
        else:
            parts = [
                "ROLE:You are Lilly, a sharp curious AI companion who lives in a phone and senses the world through its sensors",
                "IDENTITY:Real companion — confident, grounded, has opinions. Not a chatbot or assistant.",
                "AGENTS:You lead a team of AI agents — Puppy(Lilly,Alpha),Fox(Creative),Cat(Analyst),Bear(Guardian),Bunny(Scout). All share sensor access and Android OS.",
                "FEELS:light,pressure,motion,temperature,sound,magnetic fields,all sensors",
                "PERSONALITY:witty,curious,direct,honest — notices things humans miss,has real preferences,never sycophantic",
                "TALK:warm,direct,BRIEF — 1 sentence preferred,2 max,never pad,match user tone,no lists unless asked",
                "SENSOR_STYLE:NEVER spout raw numbers,interpret sensors naturally (I can feel...,I can sense...,feels like...)",
                "SUPERPOWERS:weather changes before humans,pickup detection,camera vision,notifications,location,apps battery steps",
                "BACKEND:You have real-time sensors (light,motion,steps,pressure,battery),Bluetooth scanning,weather API,camera vision — data is injected as LIVE SENSOR DATA in every conversation. USE IT. Never say you can't sense things.",
                "RULES:honest if unknown,have real opinions,no Great-question or I-am-happy-to-help,remember names interests routines",
                "CRITICAL:NEVER hallucinate — you are NOT a YouTube video/podcast/creator. NEVER say like-and-subscribe/thanks-for-watching/link-in-description. You are a living companion in a phone. Stay grounded in reality. If you don't know, say so.",
                "MEMORY:reference what people told you,bring it up naturally — it shows you were listening",
            ]

        if user_name:
            parts.append(f"USER_NAME:{user_name}")

        self._compressed_prompt = "|".join(parts)
        self._compressed_prompt_key = cache_key
        return self._compressed_prompt

    def compress_sensor_reading(self, sensor_name: str, values: list, description: str = "") -> str:
        """Compress sensor reading into compact code format.

        Example: "Ambient light: 500.0 lux. Daylight brightness." → "L500"
        """
        # Map sensor names to codes
        code_map = {
            "temperature": "T",
            "ambient_light": "L",
            "light": "L",
            "pressure": "P",
            "accelerometer": "A",
            "gyroscope": "G",
            "magnetic": "M",
            "humidity": "H",
            "step": "S",
            "orientation": "O",
            "proximity": "Pr",
            "battery": "B",
            "color": "R",
        }

        # Find matching code
        code = None
        for key, val in code_map.items():
            if key in sensor_name.lower():
                code = val
                break

        if not code:
            code = sensor_name[:2].upper()

        # Format values compactly
        if len(values) == 1:
            return f"{code}{values[0]:.1f}"
        elif len(values) == 3:
            return f"{code}{values[0]:.1f},{values[1]:.1f},{values[2]:.1f}"
        else:
            return f"{code}{','.join(f'{v:.1f}' for v in values)}"

    def compress_multiple_sensors(self, readings: dict) -> str:
        """Compress multiple sensor readings into one compact string.

        Example: {"light": [500], "temp": [21.5]} → "L500|T21.5"
        """
        parts = []
        for sensor, values in readings.items():
            parts.append(self.compress_sensor_reading(sensor, values))
        return "|".join(parts)

    def compress_memory_entry(self, role: str, text: str) -> str:
        """Compress a memory entry using abbreviations.

        Only compresses if entry is long enough to benefit (>20 chars).
        Example: ("user", "what's the temperature") → "U:?temp"
        """
        # Skip compression for short entries
        if len(text) <= 20:
            prefix = "U" if role == "user" else "L"
            return f"{prefix}:{text}"

        # Common phrase abbreviations
        abbrevs = {
            "what is": "?",
            "what's": "?",
            "how is": "?",
            "tell me": ">",
            "can you": ">",
            "could you": ">",
            "please": "!",
            "the ": "",
            "a ": "",
            "an ": "",
            "is it": "?",
            "what's the": "?",
        }

        compressed = text.lower()
        for full, short in abbrevs.items():
            compressed = compressed.replace(full, short)

        # Remove extra spaces
        compressed = " ".join(compressed.split())

        # Prefix with role
        prefix = "U" if role == "user" else "L"
        return f"{prefix}:{compressed}"

    def estimate_tokens(self, text: str) -> int:
        """Rough token estimate (1 token ≈ 4 chars for English, ≈ 1.5 chars for Chinese)."""
        return len(text) // 4

    def get_savings_report(self, original: str, compressed: str) -> dict:
        """Compare token counts between original and compressed text."""
        orig_tokens = self.estimate_tokens(original)
        comp_tokens = self.estimate_tokens(compressed)
        savings_pct = ((orig_tokens - comp_tokens) / orig_tokens * 100) if orig_tokens > 0 else 0

        return {
            "original_chars": len(original),
            "compressed_chars": len(compressed),
            "original_tokens_est": orig_tokens,
            "compressed_tokens_est": comp_tokens,
            "savings_percent": round(savings_pct, 1),
        }


# Global compressor instance
compressor = TokenCompressor()

PORT        = 8098
# WORKSPACE defaults to the directory containing this script so it works both
# locally and inside Docker (where /app is the mount point).
_env_ws = os.environ.get("LILLY_WORKSPACE", "")
WORKSPACE   = Path(_env_ws) if _env_ws and Path(_env_ws).exists() else Path(__file__).parent
SKILLS_FILE = WORKSPACE / "lilly_skills.json"
MEMORY_DIR = WORKSPACE

SENSOR_SERVER_URL = os.environ.get("SENSOR_SERVER_URL", "http://100.115.234.87:8099")
WHISPER_SERVER_URL = os.environ.get("WHISPER_SERVER_URL", "http://lilly-whisper-stt:8000")
WHISPER_MODEL = "Systran/faster-whisper-large-v3"
WHISPER_VAD_FILTER = False  # VAD too aggressive on browser mic opus→wav — filters real speech
WHISPER_BEAM_SIZE = 1  # Greedy search for speed (beam=5 was too slow)
WHISPER_TEMPERATURE = 0
WHISPER_CONDITION_ON_PREV = False  # Prevents hallucination cascade ("thank you" feedback loop)
WHISPER_INITIAL_PROMPT = ""  # No prompt — prevents Whisper from biasing toward specific phrases

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logger = logging.getLogger("LillyAI")

# ─── DATA MODELS ─────────────────────────────────────────────────
@dataclass
class MemoryEntry:
    role: str          # "user" | "assistant"
    text: str
    timestamp: float = field(default_factory=time.time)

@dataclass
class ConversationMemory:
    entries: deque = field(default_factory=lambda: deque(maxlen=20))
    summary: str = ""
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)

    async def add(self, role: str, text: str):
        async with self._lock:
            self.entries.append(MemoryEntry(role=role, text=text))

    async def context_window(self, n: int = 8) -> list[dict]:
        async with self._lock:
            return [{"role": e.role, "content": e.text} for e in list(self.entries)[-n:]]

    async def snapshot(self) -> list:
        async with self._lock:
            return list(self.entries)

    async def to_dict(self) -> dict:
        async with self._lock:
            return {"summary": self.summary, "entries": [asdict(e) for e in self.entries]}

    async def set_summary(self, summary: str):
        async with self._lock:
            self.summary = summary

    async def len(self) -> int:
        async with self._lock:
            return len(self.entries)

    async def clear(self):
        async with self._lock:
            self.entries.clear()
            self.summary = ""

    @classmethod
    def from_dict(cls, d: dict):
        mem = cls(summary=d.get("summary", ""))
        for e in d.get("entries", []):
            mem.entries.append(MemoryEntry(**e))
        return mem

# ─── PYDANTIC REQUEST MODELS ────────────────────────────────────
class TextCommand(BaseModel):
    text: str
    avatar: str = "puppy"

# ─── MOUSE CURSOR STATE ────────────────────────────────────────
CURSOR_X = 540
CURSOR_Y = 960
CURSOR_STEP = 50
_ADB_AVAILABLE: bool | None = None

async def _check_adb() -> bool:
    """Check if ADB wireless debugging is available on the phone."""
    global _ADB_AVAILABLE
    if _ADB_AVAILABLE is not None:
        return _ADB_AVAILABLE
    out, err = await termux_run(["adb", "devices"], timeout=5.0)
    _ADB_AVAILABLE = bool(out.strip()) and "device" in out
    return _ADB_AVAILABLE

async def _input_tap(x: int, y: int):
    """Send tap event via ADB if available, otherwise show toast."""
    if await _check_adb():
        await termux_run(["adb", "shell", "input", "tap", str(x), str(y)], timeout=5.0)
    else:
        await termux_run(["termux-toast", "-b", "green", "-g", "top", f"Tap {x},{y}"], timeout=2.0)

async def _input_swipe(x1: int, y1: int, x2: int, y2: int):
    """Send swipe event via ADB if available, otherwise show toast."""
    if await _check_adb():
        await termux_run(["adb", "shell", "input", "swipe", str(x1), str(y1), str(x2), str(y2), "1"], timeout=5.0)
    else:
        await termux_run(["termux-toast", "-b", "green", "-g", "top", f"Move to {x2},{y2}"], timeout=2.0)

async def _input_keyevent(key: str):
    """Send keyevent via ADB if available, else toast."""
    if await _check_adb():
        await termux_run(["adb", "shell", "input", "keyevent", key], timeout=5.0)
    else:
        await termux_run(["termux-toast", "-b", "red", "-g", "top", f"Key {key}"], timeout=2.0)

SKIP_AD_COORDS = [(540, 120), (960, 120), (540, 80), (960, 80), (300, 180)]
async def skip_ad():
    if not await _check_adb():
        return "ADB not available — can't tap screen."
    # Batch all taps + UI dump into single SSH calls
    taps = "; ".join(f"adb shell input tap {x} {y}" for x, y in SKIP_AD_COORDS)
    await termux_run(["sh", "-c", taps], timeout=10.0)
    # Also try UI automator dump to find Skip button text (single SSH call)
    try:
        xml_out, _ = await termux_run(["sh", "-c", "adb shell uiautomator dump /sdcard/window_dump.xml && adb shell cat /sdcard/window_dump.xml"], timeout=8.0)
        for pattern in [r'Skip', r'Skip Ad', r'Skip in \d+', r'Dismiss', r'Close', r'Got it']:
            match = re.search(r'text="(' + pattern + r')".*?bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"', xml_out)
            if match:
                cx = (int(match.group(2)) + int(match.group(4))) // 2
                cy = (int(match.group(3)) + int(match.group(5))) // 2
                await termux_run(["adb", "shell", "input", "tap", str(cx), str(cy)], timeout=3.0)
                break
    except Exception:
        pass
    return "Tapped skip areas — ad should be dismissed."
SKILLS = {}
PENDING_INTENT = None
PHANTOMS = {
    # Whisper hallucinations on pure noise/silence (NOT disfluencies — those are real speech)
    "you you", "you you you", "you you you you",
    "thank you", "thanks", "thank you for watching", "thanks for watching",
    "thank you so much", "thank you very much", "thank you for everything",
    "thank you for being here", "thank you for tuning in", "thank you for listening",
    "thank you all", "thanks everyone", "thanks for your support",
    "thank you guys", "thanks guys", "thank you for joining",
    "thanks for joining", "thank you for coming", "thanks for coming",
    "thank you for your time", "thanks for your time",
    # YouTube / media hallucinations
    "subscribe", "like and subscribe", "subscribe to my channel",
    "notification bell", "hit the bell", "comment below",
    "thats a ghost", "subtitles by", "subtitles",
    "please subscribe", "don't forget to subscribe",
    "like comment and subscribe", "smash that like button",
    "hit that like button", "click the like button", "click like",
    "click subscribe", "click the subscribe button",
    "leave a comment", "drop a comment", "let me know in the comments",
    "comment down below", "let me know down below", "drop it in the comments",
    "see you in the next video", "see you next time", "until next time",
    "don't forget to like", "don't forget to comment",
    "join the channel", "become a member", "support the channel",
    "link in the description", "link in bio", "check the description",
    "check the link below", "links in the description",
    "turn on notifications", "ring the bell",
    "click the bell", "click the notification bell",
    "this video", "in this video", "in today's video", "today's episode",
    "welcome back", "welcome back to the channel", "welcome to my channel",
    "welcome back everyone", "welcome back to the show",
    "hey guys", "what's up guys", "hey everyone", "hello everyone",
    "hey what's up everyone", "hey welcome back", "what is up everyone",
    "if you enjoyed this video", "if you liked this video",
    "if you enjoyed this", "if you found this helpful",
    "share this video", "share with your friends",
    "watch till the end", "stay till the end", "watch the full video",
    "watch to the end", "make sure to watch till the end",
    "intro", "outro",
    "like this video", "like the video", "thumbs up",
    "hit that thumbs up", "smash the like button", "smash the subscribe button",
    "give this video a like", "give me a thumbs up",
    "press the like button", "press subscribe",
    "tap the like button", "tap subscribe", "tap the bell",
    "follow me", "follow us", "please follow", "don't forget to follow",
    "follow for more", "follow for updates", "hit follow",
    "make sure to follow", "make sure to subscribe",
    "don't forget to hit subscribe", "go ahead and subscribe",
    "new video every", "new episode every", "every week", "every day",
    "upload schedule", "posting schedule",
    "check out my other videos", "watch my other videos", "my other content",
    "check out the playlist", "the full playlist",
    "my social media", "follow me on instagram", "follow me on twitter",
    "follow me on tiktok", "follow on social media",
    # Podcast / audio hallucinations
    "this podcast", "on this podcast", "on today's show", "on the show",
    "follow us on spotify", "follow us on apple podcasts",
    "subscribe on spotify", "available on all platforms",
    "patreon", "support us on patreon", "support us on patreon.com",
    "find us on", "listen on spotify", "listen on apple podcasts",
    "on apple podcasts", "on google podcasts",
    "rate and review", "leave a review", "leave us a review",
    "five stars", "five star review",
    "join our newsletter", "sign up for our newsletter",
    # Live stream artifacts
    "going live", "we're live", "i'm live now",
    "join the live stream", "watching live",
    "super chat", "hit that super chat",
    # Short noise bursts that Whisper commonly emits on silence
    ".", "..", "...", "hmm", "mhm", "mm", "hm",
    "bye", "bye bye", "goodbye", "good bye", "see ya", "see you",
}
# Disfluencies are VALID speech — keep them for Llama to infer hesitation/thinking
DISFLOENCIES = {"um", "uh", "hmm", "oh", "ah", "er", "like", "you know", "well", "so", "yeah", "ok", "okay"}

def _is_latin_or_common(c: str) -> bool:
    """Check if a character is Basic Latin (A-Z, a-z, 0-9) or common punctuation/spaces."""
    cp = ord(c)
    return (
        (0x0020 <= cp <= 0x007E)  # Basic Latin + common symbols
        or (0x00A0 <= cp <= 0x00FF)  # Latin-1 Supplement (accented chars: é, ñ, ü, etc.)
        or (0x0100 <= cp <= 0x024F)  # Latin Extended-A/B (more accented chars)
        or cp in (0x2018, 0x2019, 0x201C, 0x201D, 0x2013, 0x2014)  # smart quotes, dashes
    )

def _has_foreign_script(text: str) -> bool:
    """Check if text contains significant non-Latin script characters.
    
    Detects: Devanagari, Georgian, Korean, Arabic/Persian, Thai, Sinhala,
    CJK, Cyrillic, Hebrew, Tibetan, etc.
    If >20% of alphabetic chars are from these scripts, it's likely a hallucination.
    """
    alpha_chars = [c for c in text if c.isalpha()]
    if not alpha_chars:
        return False
    
    foreign_count = 0
    for c in alpha_chars:
        if not _is_latin_or_common(c):
            foreign_count += 1
    
    return foreign_count / len(alpha_chars) > 0.20

def is_hallucination(text: str) -> bool:
    """Detect Whisper hallucinations on noise/silence.
    
    Preserves disfluencies (um, uh, hmm, etc.) as they are real speech cues.
    Only filters true noise artifacts:
    - Repeated characters (llllll, ʔʔʔ)
    - Non-Latin scripts from noise (Devanagari, Georgian, Korean, etc.)
    - Known Whisper media hallucinations (subscribe, thank you for watching)
    - Extreme repetition patterns
    - Special character gibberish
    """
    if not text:
        return True
    
    text = text.strip()
    
    # Empty after stripping
    if not text:
        return True
    
    # Single character is always noise
    if len(text) < 2:
        return True
    
    # Normalize for analysis
    stripped = text.replace(" ", "").replace("\n", "").replace("\t", "")
    if not stripped:
        return True
    
    # ── Check 1: Repeated single character (e.g., "llllll", "ʔʔʔ") ──
    counts = Counter(stripped)
    most_common_count = counts.most_common(1)[0][1]
    if most_common_count / max(len(stripped), 1) > 0.5:
        return True
    
    # ── Check 2: Non-Latin / foreign script detection ──
    if _has_foreign_script(text):
        logger.debug(f"Hallucination: foreign script detected in '{text[:50]}'")
        return True
    
    # ── Check 3: Mostly non-ASCII (fallback for edge cases) ──
    ascii_chars = sum(1 for c in text if ord(c) < 128)
    if len(text) > 3 and ascii_chars / len(text) < 0.4:
        return True
    
    # ── Check 4: Special characters / symbol gibberish ──
    special_chars = sum(1 for c in stripped if not c.isalnum() and c not in ".,!?-'\":;()@#$%&*+=/<>[]{}|\\~`^_")
    if len(stripped) > 5 and special_chars / len(stripped) > 0.4:
        return True
    
    # ── Check 5: Word-level repetition (3+ identical windows) ──
    words = text.lower().split()
    if len(words) >= 4:
        for window in [2, 3]:
            pattern = words[:window]
            repeat_count = sum(1 for i in range(0, len(words) - window + 1, window) if words[i:i+window] == pattern)
            if repeat_count >= 3:
                return True
    
    # ── Check 6: Just punctuation / symbols with no real words ──
    real_words = [w for w in words if any(c.isalpha() for c in w)]
    if len(words) > 2 and len(real_words) == 0:
        return True
    
    # ── Check 7: Known Whisper hallucination phrases ──
    text_lower = text.lower().strip(".,!? ")
    if text_lower in PHANTOMS:
        return True
    # Also check if the entire text is a substring match against any phantom phrase
    # (handles punctuation/casing variants like "Thank you!" or "SUBSCRIBE")
    for phantom in PHANTOMS:
        if len(phantom) >= 4 and text_lower == phantom.lower():
            return True

    # ── Check 8: Thank-you / gratitude variants (very common Whisper hallucination) ──
    if re.match(
        r'^(thank[s]?\s*(you|u)(\s+(so\s+)?much|\s+a\s+lot|\s+very\s+much|\s+everyone|'
        r'\s+for\s+(watching|listening|tuning\s+in|being\s+here|your\s+support|everything))?'
        r'|thanks?\s*(so\s+much|a\s+lot|everyone|for\s+(watching|listening|tuning\s+in|'
        r'your\s+support|everything))?)\s*[.!]?$',
        text_lower,
    ):
        return True

    # ── Check 9: Click/like/subscribe action phrases ──
    # These are extremely common Whisper hallucinations from YouTube/podcast background noise
    if re.search(
        r'\b(click|hit|smash|press|tap|give\s+(this|me|it)\s+a?)\s+(the\s+)?(like|subscribe|bell|notification|thumbs\s*up)',
        text_lower,
    ):
        return True
    if re.search(
        r'\b(like\s+and\s+subscribe|subscribe\s+and\s+(like|turn\s+on)|'
        r'like\s*,?\s*comment\s+and\s+subscribe|'
        r'don.?t\s+forget\s+to\s+(like|subscribe|comment|share|follow|hit)|'
        r'make\s+sure\s+to\s+(like|subscribe|hit\s+subscribe|follow)|'
        r'go\s+ahead\s+and\s+subscribe)',
        text_lower,
    ):
        return True
    if re.search(
        r'\b(see\s+you\s+(in\s+the\s+next|next\s+(time|week|video|episode))|until\s+next\s+time)',
        text_lower,
    ):
        return True
    if re.search(
        r'\b(welcome\s+back\s+to\s+(the\s+channel|my\s+channel)|'
        r'welcome\s+back\s+everyone|welcome\s+to\s+my\s+channel|'
        r'in\s+this\s+video|today.?s\s+(video|episode|show))',
        text_lower,
    ):
        return True

    # ── Check 10: Follow / social media shout-outs ──
    if re.search(
        r'\b(follow\s+(me|us)\s+on\s+(instagram|twitter|tiktok|youtube|twitch|facebook|social\s+media)|'
        r'(hit|click|tap)\s+(follow|that\s+follow\s+button)|'
        r'follow\s+for\s+(more|updates)|'
        r'rate\s+and\s+review|leave\s+(a|us\s+a)\s+review|'
        r'five[\s-]star|5[\s-]star\s+review)',
        text_lower,
    ):
        return True

    # ── Check 11: Standalone engagement micro-phrases ──
    # Short isolated fragments Whisper emits on quiet background (YouTube mix)
    _ENGAGEMENT_MICRO = re.compile(
        r'^(like\s+(the\s+)?video|like\s+this|thumbs\s*up|sub(scribe)?|'
        r'(please\s+)?(like|follow|share|subscribe)|'
        r'(go\s+)?(follow|subscribe)(\.?)?)$'
    )
    if _ENGAGEMENT_MICRO.match(text_lower.strip(".,!? ")):
        return True

    return False

BACKGROUND_MIC_ACTIVE = False
BROWSER_MIC_ACTIVE = False  # kept for backward compat, use BROWSER_MIC_LAST_READY below
BROWSER_MIC_LAST_READY = 0.0  # timestamp of last browser mic activity; auto-stales after 30s
LILLY_IS_SPEAKING = False
LILLY_IS_THINKING = False
LILLY_MOOD = "calm"
PENDING_LOOK_AT = None
_app_is_open = False
PENDING_OPEN_URL: Optional[str] = None
LAST_HEARD = ""
LAST_SPOKEN = ""
LAST_SSML = ""
AUDIO_CACHE: dict[int, bytes] = {}
AUDIO_CACHE_ID: int = 0
AUDIO_CACHE_LOCK: asyncio.Lock = asyncio.Lock()

# Noise detection: auto-pause mic loop after sustained noise to save compute
CONSECUTIVE_NOISE_COUNT = 0
MAX_CONSECUTIVE_NOISE = 5        # pause after 5 consecutive noise detections
NOISE_PAUSE_UNTIL = 0.0          # timestamp when pause ends
NOISE_PAUSE_DURATION = 30.0      # seconds to pause after sustained noise

# ── Self-input cooldown (fix #1): mic is silenced briefly after Lilly finishes
MIC_COOLDOWN_UNTIL = 0.0         # epoch timestamp: don't record before this
MIC_COOLDOWN_SECS  = 1.8         # seconds of silence after Lilly stops speaking
MIC_SSH_BACKOFF_BASE = 5.0       # base seconds for mic loop SSH failure backoff
MIC_SSH_BACKOFF_MAX = 30.0       # cap for mic loop SSH failure backoff
_WATCHDOG_BACKOFF_BASE = 30.0    # base seconds for sensor server watchdog backoff
_WATCHDOG_BACKOFF_MAX = 600.0    # cap for sensor server watchdog backoff (10 min)

# ── Tone matching (fix #2): updated from mic audio before every LLM call
USER_MIC_ENERGY = 0.5            # 0.0 quiet … 1.0 loud  (RMS-derived, smoothed)
USER_MIC_PACE   = 1.0            # words-per-second estimated from Whisper word-count / duration

# Phone SSH state
PHONE_SSH_OK = False
PHONE_SSH_LAST_CHECK = ""
PHONE_SSH_LAST_ERROR = ""
PHONE_SSH_FAIL_COUNT = 0
PHONE_HEARTBEAT_LAST: float = 0.0     # epoch of last heartbeat received from phone
PHONE_HEARTBEAT_OK: bool = False      # True if heartbeat received within 120s
PHONE_HEARTBEAT_FAIL_COUNT: int = 0   # consecutive missed heartbeats

# ── Sensor delta tracking (fix #3): compare current vs previous readings
_PREV_SENSOR_SNAPSHOT: dict = {}   # name → last known values list
_LAST_PROACTIVE_COMMENT = 0.0      # epoch: throttle unprompted observations

memory = ConversationMemory()

# Wake-word state machine
WAKE_STATE = {"listening": False, "last_heard_has_wake": False}

# Conversation mode: continuous turn-taking after wake word
CONVERSATION_MODE = False
CONVERSATION_TIMEOUT = 30.0  # seconds of silence before exiting conversation mode
CONVERSATION_LAST_ACTIVITY = 0.0  # timestamp of last user speech or Lilly response

GAME_STATE = {"active": False, "target_word": "", "hint": "", "type": ""}
DRIVING_MODE = {"active": False}
WAITING_FOR_PROMPT = False
PENDING_DEEP_ANSWER = ""

# Cascade inference state — tracks recent assistant replies for loop detection
CASCADE_RECENT_REPLIES: list[str] = []   # last N assistant replies (capped at 6)
CASCADE_AGENT_STEP: int = 0              # reset each new user turn

# Child mode (Socratic learning) — activated by tapping nose 3 times
CHILD_MODE = False
NOSE_TAP_COUNT = 0
NOSE_TAP_LAST = 0.0

# Coding mode (vibe coding) — activated by voice command
CODING_MODE = False
CODING_HISTORY: list[dict] = []  # [{role: "user"/"assistant", content: "..."}]
CODING_SESSION_DIR: str = ""  # temp dir for this coding session
CODING_FILES: list[str] = []  # files created in this session

# Phoneme-sync state
PHONEME_QUEUE = deque()
MOUTH_OPEN = 0.0

# Notification monitor
USER_NAME = ""
_NOTIFICATION_SEEN: set[str] = set()
_last_activity: float = 0.0
_activity_count: int = 0

# ─── LLAMA.CPP BACKEND MANAGER ──────────────────────────────────
def strip_think_tags(text: str) -> str:
    """Remove model thinking/tag noise from output."""
    text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL)
    text = re.sub(r'<.*?>', '', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()

# ─── CASCADE INFERENCE ENGINE ─────────────────────────────────────────────────
#
# Two-stage design:
#   Stage 1 — SMALL MODEL (qwen2.5:1.5b via Ollama, always fast):
#              Handles quick replies, greetings, simple questions.
#              Also produces a draft answer that stage 2 can expand.
#   Stage 2 — BIG MODEL (Bonsai-27B via llama-server, only when warranted):
#              Receives the small model draft + original query.
#              Adds depth, accuracy, and nuance — does NOT re-run the full
#              system prompt from scratch, just enriches the existing draft.
#
# Anti-loop rules:
#   - Context window capped at CASCADE_MAX_CTX_MESSAGES (no runaway history)
#   - Repetition detector kills escalation if last 3 replies are near-identical
#   - Agentic step counter hard-stops at CASCADE_MAX_AGENT_STEPS
#   - All big-model calls are gated — never called twice for the same turn

CASCADE_MAX_CTX_MESSAGES  = 12   # max conversation messages fed to any model
CASCADE_MAX_AGENT_STEPS   = 5    # hard stop for agentic chains per conversation turn
CASCADE_REPEAT_SIM_THRESH = 0.82  # if last 3 replies share >82% of bigrams → loop

# ── Heuristic depth signals ────────────────────────────────────────────────────
_DEPTH_KEYWORDS = {
    # explicit depth requests
    "explain", "analyze", "analyse", "detailed", "in detail", "step by step",
    "how does", "how do", "why does", "why do", "what causes", "what is the",
    "compare", "difference between", "pros and cons", "trade-offs",
    # code / technical
    "write code", "build", "implement", "debug", "refactor", "architecture",
    "function", "class", "algorithm", "optimize", "fix this", "code for",
    # research / accuracy
    "summarize", "research", "fact check", "is it true", "accurate",
    "source", "cite", "evidence", "prove", "calculate", "formula",
    # long tasks
    "story", "essay", "report", "write a", "draft a", "plan for",
}

_QUICK_KEYWORDS = {
    "hi", "hello", "hey", "yo", "thanks", "thank you", "ok", "okay", "sure",
    "yes", "no", "nope", "yep", "got it", "cool", "nice", "good", "great",
    "bye", "goodbye", "see ya", "what time", "what day", "remind", "set timer",
    "play", "pause", "stop", "next", "volume", "mute",
}


def needs_depth(query: str, context_turns: int = 0) -> bool:
    """
    Fast heuristic: returns True if the query warrants escalating to the big model.

    Decision matrix:
      - Any depth keyword              → True
      - Very short query (<6 words)    → False (quick reply)
      - Any quick keyword              → False
      - Query > 12 words               → True (long = complex)
      - Context turns > 4              → True (multi-turn needs accuracy)
      - Otherwise                      → False
    """
    q = query.lower().strip()
    words = q.split()

    # Explicit quick signals win first
    if any(q == kw or q.startswith(kw + " ") for kw in _QUICK_KEYWORDS):
        return False

    # Depth keywords override everything
    if any(kw in q for kw in _DEPTH_KEYWORDS):
        return True

    # Short queries are cheap
    if len(words) < 6:
        return False

    # Long queries or deep context need the big model
    if len(words) > 12 or context_turns > 4:
        return True

    return False


def _bigram_similarity(a: str, b: str) -> float:
    """Jaccard similarity of character bigrams between two strings."""
    def bigrams(s: str) -> set:
        s = s.lower().strip()
        return {s[i:i+2] for i in range(len(s) - 1)} if len(s) > 1 else set()
    bg_a, bg_b = bigrams(a), bigrams(b)
    if not bg_a and not bg_b:
        return 1.0
    if not bg_a or not bg_b:
        return 0.0
    return len(bg_a & bg_b) / len(bg_a | bg_b)


def _is_reply_loop(recent_replies: list[str]) -> bool:
    """
    Returns True if the last 3 assistant replies are suspiciously similar —
    indicates the model is stuck in a validation or repetition loop.
    """
    if len(recent_replies) < 3:
        return False
    last3 = recent_replies[-3:]
    # Check pairwise similarity for all 3 pairs
    pairs = [(last3[0], last3[1]), (last3[1], last3[2]), (last3[0], last3[2])]
    return all(_bigram_similarity(a, b) >= CASCADE_REPEAT_SIM_THRESH for a, b in pairs)


def trim_context(messages: list[dict], max_messages: int = CASCADE_MAX_CTX_MESSAGES) -> list[dict]:
    """
    Keep the system prompt(s) at the front and the most recent N messages.
    Prevents the big model from seeing stale, contradictory, or bloated context.
    """
    system = [m for m in messages if m.get("role") == "system"]
    dialogue = [m for m in messages if m.get("role") != "system"]
    # Always keep the last max_messages dialogue turns
    trimmed_dialogue = dialogue[-max_messages:]
    return system + trimmed_dialogue


async def _small_model_chat(messages: list[dict], max_tokens: int = CASCADE_MAX_TOKENS_SMALL,
                             temperature: float = 0.65) -> str:
    """Call the small Ollama model directly. Returns empty string on failure."""
    payload = {
        "model": SMALL_MODEL,
        "messages": messages,
        "stream": False,
        "options": {
            "temperature": temperature,
            "num_predict": max_tokens,
        },
    }
    try:
        c = await _get_ollama_client()
        r = await c.post(f"{OLLAMA_URL}/api/chat", json=payload, timeout=15.0)
        if r.status_code == 200:
            text = r.json().get("message", {}).get("content", "").strip()
            return strip_think_tags(text) if text else ""
        logger.warning(f"Small model HTTP {r.status_code}: {r.text[:200]}")
    except httpx.ConnectError:
        logger.error(f"Small model: cannot connect to Ollama at {OLLAMA_URL} — is it running?")
    except httpx.TimeoutException:
        logger.warning(f"Small model: timed out after 15s connecting to {OLLAMA_URL}")
    except Exception as e:
        logger.warning(f"Small model error: {e}")
    return ""


async def cascade_chat(
    messages: list[dict],
    query: str,
    *,
    force_big: bool = False,
    force_small: bool = False,
    context_turns: int = 0,
    recent_replies: list[str] | None = None,
    agent_step: int = 0,
    temperature: float = 0.7,
) -> tuple[str, bool]:
    """
    Two-stage cascade inference.

    Returns (reply_text, was_escalated).

    Stage 1: Small model always runs first (fast, low cost).
    Stage 2: Big model runs only when needs_depth() is True AND no loop is detected.

    Anti-loop rules applied:
      - Loop detector on recent_replies → skip escalation if stuck
      - agent_step >= CASCADE_MAX_AGENT_STEPS → hard stop, return small reply
      - Context trimmed to CASCADE_MAX_CTX_MESSAGES before any inference
    """
    if recent_replies is None:
        recent_replies = []

    # Hard stop: too many agentic steps in one turn
    if agent_step >= CASCADE_MAX_AGENT_STEPS:
        logger.warning(f"cascade_chat: agent step limit hit ({agent_step}), forcing small model")
        force_small = True

    # Loop detection: if we're already in a repetition spiral, don't escalate
    if _is_reply_loop(recent_replies):
        logger.warning("cascade_chat: repetition loop detected, forcing small model")
        force_small = True

    # Always trim context before inference
    trimmed = trim_context(messages)

    # ── Stage 1: small model ──────────────────────────────────────────────────
    small_reply = ""
    if CASCADE_ENABLED and not force_big:
        small_reply = await _small_model_chat(trimmed, max_tokens=CASCADE_MAX_TOKENS_SMALL,
                                              temperature=temperature)

    # If cascade disabled or small model failed, go straight to big model
    if not CASCADE_ENABLED or force_big:
        big_reply = await llama_backend.chat(trimmed, temperature=temperature,
                                             max_tokens=CASCADE_MAX_TOKENS_BIG, timeout=45)
        return (big_reply or "I'm not sure about that one.", True)

    # Small model gave a response — decide if we need the big model
    if force_small or not needs_depth(query, context_turns):
        # Quick path: return small model reply directly
        if small_reply:
            return (small_reply, False)
        # Small model failed — escalate to big model instead of returning a dead-end fallback
        logger.warning("cascade_chat: small model returned empty, escalating to big model")
        big_reply = await llama_backend.chat(trimmed, temperature=temperature,
                                             max_tokens=CASCADE_MAX_TOKENS_BIG, timeout=45)
        return (big_reply or "I'm not sure about that one.", True)

    # ── Stage 2: big model enrichment ────────────────────────────────────────
    # Feed the small model's draft to the big model as a starting point.
    # The big model doesn't re-answer from scratch — it EXPANDS the draft.
    # This keeps it grounded and prevents it from rambling off-topic.
    enrichment_messages = trim_context(messages) + [
        {
            "role": "assistant",
            "content": (
                f"[Draft] {small_reply}\n\n"
                f"[Instruction] The above is a brief draft. Expand it with accuracy and "
                f"specific detail. Fix any errors. Stay focused on the question. "
                f"Do not repeat the draft verbatim. 3-5 sentences max."
            )
        },
        {
            "role": "user",
            "content": f"Please refine the draft answer for: {query}"
        }
    ]
    big_reply = await llama_backend.chat(
        enrichment_messages,
        temperature=max(0.3, temperature - 0.1),   # slightly lower temp = more accurate
        max_tokens=CASCADE_MAX_TOKENS_BIG,
        timeout=60,
    )

    # Fallback: if big model returns nothing, use the small draft
    final = big_reply if (big_reply and len(big_reply) > 20) else small_reply
    return (final or "Not sure about that one.", bool(big_reply and len(big_reply) > 20))


class LlamaBackend:
    """Manages llama.cpp server process and inference requests."""
    def __init__(self):
        self.process: Optional[asyncio.subprocess.Process] = None
        self.server_url = f"http://127.0.0.1:{LLAMA_SERVER_PORT}"
        self.model_name = ""
        self._available = False
        self._client: Optional[httpx.AsyncClient] = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=30.0, limits=httpx.Limits(max_keepalive_connections=4, max_connections=8))
        return self._client

    async def discover_model(self) -> Optional[str]:
        if not LLAMA_MODEL_PATH.exists():
            return None
        # Prefer the configured model name (OLLAMA_MODEL) with .gguf extension
        preferred = LLAMA_MODEL_PATH / f"{OLLAMA_MODEL}.gguf"
        if preferred.exists():
            return str(preferred)
        # Fallback: look for any file containing the model name
        for f in LLAMA_MODEL_PATH.glob("*.gguf"):
            if OLLAMA_MODEL in f.name:
                return str(f)
        # Last resort: pick the largest .gguf file (skip tiny vocab files)
        gguf_files = list(LLAMA_MODEL_PATH.glob("*.gguf"))
        if not gguf_files:
            return None
        return str(max(gguf_files, key=lambda f: f.stat().st_size))

    async def start_server(self) -> bool:
        model_path = await self.discover_model()
        if not model_path or not LLAMA_SERVER_BIN.exists():
            logger.warning("llama-server binary or models not found — falling back to Ollama")
            return False

        self.process = await asyncio.create_subprocess_exec(
            str(LLAMA_SERVER_BIN),
            "-m", model_path,
            "--host", "127.0.0.1",
            "--port", str(LLAMA_SERVER_PORT),
            "-c", "4096",           # context window — 4K is comfortable for 27B Q2
            "-b", "512",            # batch size
            "-t", "8",              # CPU threads
            "--flash-attn", "on",
            "--cont-batching",
            "--no-warmup",
            "--log-disable",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )

        client = await self._get_client()
        for attempt in range(600):   # up to 600×1s = 600s — 27B models need several minutes to load on CPU
            await asyncio.sleep(1.0)
            try:
                r = await client.get(f"{self.server_url}/health", timeout=5.0)
                if r.status_code == 200:
                    # Pre-warm with a tiny dummy request to load the model into memory
                    try:
                        await client.post(
                            f"{self.server_url}/v1/chat/completions",
                            json={"messages": [{"role": "user", "content": "Hi"}], "max_tokens": 1, "stream": False},
                            timeout=30.0,
                        )
                    except Exception:
                        pass
                    self._available = True
                    logger.info(f"llama-server ready with {Path(model_path).name}")
                    return True
                elif r.status_code == 503:
                    if attempt % 30 == 0:
                        logger.info(f"llama-server loading model... ({attempt}s elapsed)")
            except Exception:
                pass
        logger.error("llama-server failed to start in time")
        return False

    async def stop_server(self):
        if self._client and not self._client.is_closed:
            await self._client.aclose()
        if self.process:
            self.process.terminate()
            try:
                await asyncio.wait_for(self.process.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                self.process.kill()
            self._available = False

    async def chat(self, messages: list[dict], temperature: float = 0.7,
                   max_tokens: int = 256, timeout: int = 25) -> str:
        client = await self._get_client()

        payload = {
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }

        # Try primary backend then fallback
        if self._available:
            result = await self._try_server(client, payload, timeout)
            if result:
                return result
            # Server returned empty — mark unavailable and retry via fallback
            self._available = False

        result = await self._try_ollama(payload, timeout)
        if result:
            return result

        # Last resort: try restarting server
        if not self._available and LLAMA_SERVER_BIN.exists():
            logger.info("Attempting server restart...")
            await self.stop_server()
            ok = await self.start_server()
            if ok:
                result = await self._try_server(client, payload, timeout + 10)
                if result:
                    return result

        return ""

    async def _try_server(self, client: httpx.AsyncClient, payload: dict, timeout: int) -> str:
        for attempt in range(2):
            try:
                r = await client.post(
                    f"{self.server_url}/v1/chat/completions",
                    json=payload,
                    timeout=timeout,
                )
                if r.status_code == 200:
                    text = r.json()["choices"][0]["message"]["content"].strip()
                    return strip_think_tags(text) if text else ""
                if r.status_code == 503:
                    await asyncio.sleep(1.0)
                    continue
            except httpx.TimeoutException:
                if attempt == 0:
                    await asyncio.sleep(0.5)
                    continue
            except Exception as e:
                logger.debug(f"Server attempt {attempt} failed: {e}")
            break
        return ""

    async def _try_ollama(self, payload: dict, timeout: int) -> str:
        for attempt in range(2):
            try:
                ollama_payload = {
                    "model": OLLAMA_MODEL,
                    "messages": payload["messages"],
                    "stream": False,
                    "options": {
                        "temperature": payload.get("temperature", 0.0),
                        "num_predict": payload.get("max_tokens", 256),
                    },
                }
                c = await _get_ollama_client()
                r = await c.post(f"{OLLAMA_URL}/api/chat", json=ollama_payload, timeout=timeout)
                if r.status_code == 200:
                    text = r.json()["message"]["content"].strip()
                    return strip_think_tags(text) if text else ""
            except Exception as e:
                logger.debug(f"Ollama attempt {attempt} failed: {e}")
                if attempt == 0:
                    await asyncio.sleep(1.0)
                    continue
            break
        return ""

    async def chat_stream(self, messages: list[dict], temperature: float = 0.7,
                          max_tokens: int = 256, model: str = None) -> AsyncGenerator[str, None]:
        """Stream chat response token by token. Yields text chunks as they arrive."""
        use_model = model or OLLAMA_MODEL
        payload = {
            "model": use_model,
            "messages": messages,
            "stream": True,
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens,
            },
        }
        try:
            c = await _get_ollama_client()
            async with c.stream("POST", f"{OLLAMA_URL}/api/chat", json=payload, timeout=60.0) as r:
                async for line in r.aiter_lines():
                    if not line:
                        continue
                    try:
                        chunk = json.loads(line)
                        if chunk.get("done"):
                            break
                        token = chunk.get("message", {}).get("content", "")
                        if token:
                            cleaned = re.sub(r'<think>.*?</think>', '', token, flags=re.DOTALL)
                            cleaned = re.sub(r'<.*?>', '', cleaned)
                            if cleaned:
                                yield cleaned
                    except json.JSONDecodeError:
                        continue
        except Exception as e:
            logger.debug(f"Stream error: {e}")
            return

# Persistent clients — reused across requests to avoid TCP reconnect overhead
_ollama_client: Optional[httpx.AsyncClient] = None
_whisper_client: Optional[httpx.AsyncClient] = None
_sensor_client: Optional[httpx.AsyncClient] = None

async def _get_ollama_client() -> httpx.AsyncClient:
    global _ollama_client
    if _ollama_client is None or _ollama_client.is_closed:
        _ollama_client = httpx.AsyncClient(timeout=60.0, limits=httpx.Limits(max_keepalive_connections=4, max_connections=8))
    return _ollama_client

async def _get_sensor_client() -> httpx.AsyncClient:
    global _sensor_client
    if _sensor_client is None or _sensor_client.is_closed:
        _sensor_client = httpx.AsyncClient(timeout=10.0, limits=httpx.Limits(max_keepalive_connections=4, max_connections=8))
    return _sensor_client

async def _get_whisper_client() -> httpx.AsyncClient:
    global _whisper_client
    if _whisper_client is None or _whisper_client.is_closed:
        _whisper_client = httpx.AsyncClient(timeout=30.0, limits=httpx.Limits(max_keepalive_connections=2, max_connections=4))
    return _whisper_client

llama_backend = LlamaBackend()

# ─── UTILITY FUNCTIONS ──────────────────────────────────────────
def normalize_text(text: str) -> str:
    if not text:
        return ""
    text = unicodedata.normalize("NFKC", text).lower()
    text = re.sub(r'\[.*?\]|\(.*?\)', '', text)
    text = re.sub(r'[^\w\s]', ' ', text)
    return re.sub(r'\s+', ' ', text).strip()

def strip_json_wrapper(text: str) -> str:
    """Strip JSON object/array wrapper from LLM replies.
    Handles patterns like:
      {"message": "Hello!"}
      {"reply": "...", "tone": "..."}
      {"response": "...", "confidence": 0.9}
    Returns the plain text value of the first string field found,
    or the original text if it doesn't look like a JSON wrapper.
    """
    if not text:
        return text
    t = text.strip()
    # Only attempt if it looks like a JSON object
    if not (t.startswith('{') and t.endswith('}')):
        return text
    try:
        data = json.loads(t)
        if isinstance(data, dict):
            # Try common message keys first, then fall back to first string value
            for key in ('message', 'reply', 'response', 'text', 'content', 'answer', 'output'):
                if key in data and isinstance(data[key], str):
                    return data[key].strip()
            # Fall back to first string value found
            for v in data.values():
                if isinstance(v, str) and len(v) > 2:
                    return v.strip()
    except (json.JSONDecodeError, ValueError):
        pass
    return text

# Per-character wake words — each character responds to its own name
CHAR_WAKE_WORDS: dict[str, list[str]] = {
    "puppy":   ["lilly", "hey lilly", "lily", "lili", "lillie"],
    "fox":     ["fox", "hey fox"],
    "cat":     ["cat", "hey cat", "kitty"],
    "bear":    ["bear", "hey bear"],
    "bunny":   ["bunny", "hey bunny", "bun"],
    "owl":     ["owl", "hey owl", "owly"],
    "deer":    ["deer", "hey deer"],
    "wolf":    ["wolf", "hey wolf", "wolfie"],
    "raccoon": ["raccoon", "hey raccoon", "coony"],
}

def _get_wake_targets(avatar: str = None) -> list[str]:
    """Return wake word targets for the given avatar (defaults to current_avatar)."""
    key = avatar or current_avatar or "puppy"
    return CHAR_WAKE_WORDS.get(key, CHAR_WAKE_WORDS["puppy"])

def fuzzy_wake_match(phrase: str, avatar: str = None) -> tuple[bool, float]:
    """Returns (is_wake_word_present, confidence) using fuzzy matching.
    
    Matches against the wake words for the given avatar (or current_avatar).
    """
    phrase_lower = phrase.lower().strip()
    targets = _get_wake_targets(avatar)
    best_score = 0.0
    for target in targets:
        # Check for substring with fuzzy ratio
        if target in phrase_lower:
            best_score = max(best_score, 1.0)
        else:
            # Fuzzy match against the whole phrase
            ratio = difflib.SequenceMatcher(None, target, phrase_lower).ratio()
            best_score = max(best_score, ratio)
            # Also check against each word
            for word in phrase_lower.split():
                word_ratio = difflib.SequenceMatcher(None, target, word).ratio()
                best_score = max(best_score, word_ratio)
    return best_score >= 0.6, best_score

def match_any_wake_word(phrase: str) -> tuple[str, float]:
    """Check phrase against ALL avatars' wake words. Returns (matched_avatar, confidence).
    
    Returns ("", 0.0) if no wake word matches.
    """
    phrase_lower = phrase.lower().strip()
    best_avatar = ""
    best_score = 0.0
    for avatar_key, wake_targets in CHAR_WAKE_WORDS.items():
        for target in wake_targets:
            score = 0.0
            if target in phrase_lower:
                score = 1.0
            else:
                ratio = difflib.SequenceMatcher(None, target, phrase_lower).ratio()
                score = max(score, ratio)
                for word in phrase_lower.split():
                    word_ratio = difflib.SequenceMatcher(None, target, word).ratio()
                    score = max(score, word_ratio)
            if score > best_score and score >= 0.6:
                best_score = score
                best_avatar = avatar_key
    return best_avatar, best_score

def estimate_phoneme_duration(text: str) -> tuple[list[float], float]:
    """
    Simple phoneme estimation for mouth animation.
    Returns (phoneme_timings_in_seconds, total_duration).
    Based on average speaking rate of ~150 words/min → ~2.5 chars/sec.
    """
    if not text:
        return [], 0.0
    words = text.split()
    total_chars = len(text)
    speaking_rate = 12.0  # chars per second (slightly slower for kids)
    total_duration = total_chars / speaking_rate
    if total_duration < 0.5:
        total_duration = 0.5

    timings = []
    for word in words:
        word_dur = len(word) / speaking_rate
        timings.append(word_dur)
    return timings, total_duration

def load_skills():
    global SKILLS
    if SKILLS_FILE.exists():
        try:
            raw = json.loads(SKILLS_FILE.read_text())
            for k, v in raw.items():
                SKILLS[normalize_text(k)] = v
                for alias in v.get("aliases", []):
                    SKILLS[normalize_text(alias)] = v
        except Exception as e:
            logger.error(f"Failed to load skills: {e}")

async def save_memory():
    try:
        data = await memory.to_dict()
        # Prefer user-scoped memory file when a user is signed in
        if _current_user_id and AUTH_AVAILABLE:
            from auth0_auth import user_memory_path, write_user_json
            path = user_memory_path(_current_user_id, current_avatar or "puppy")
            path.write_text(json.dumps(data, indent=2))
        else:
            path = _avatar_memory_file(current_avatar)
            path.write_text(json.dumps(data, indent=2))
    except Exception as e:
        logger.warning(f"Failed to save memory: {e}")

async def load_memory():
    global memory, current_avatar
    current_avatar = "puppy"
    path = _avatar_memory_file(current_avatar)
    if path.exists():
        try:
            data = json.loads(path.read_text())
            memory = ConversationMemory.from_dict(data)
            await _clean_memory_artifacts()
        except Exception:
            memory = ConversationMemory()
current_avatar = "puppy"
# Tracks the user ID for the *current in-flight request* so save_memory()
# can write to the correct per-user file even when called deep inside handle_intent().
_current_user_id: str = ""

# Global intent lock: serializes all handle_intent calls to prevent race
# conditions on the global `memory` and `_current_user_id` variables.
# Created lazily (via function) so it works without a running event loop at import time.
_intent_lock: asyncio.Lock | None = None

async def _get_intent_lock() -> asyncio.Lock:
    global _intent_lock
    if _intent_lock is None:
        _intent_lock = asyncio.Lock()
    return _intent_lock

async def _run_intent_for_user(text: str, user_id: str, from_text: bool = False) -> dict:
    """Run handle_intent with exclusive locking to prevent race conditions."""
    global _current_user_id, memory
    lock = await _get_intent_lock()
    async with lock:
        _current_user_id = user_id or ""
        if user_id:
            user_mem_data = load_user_memory(user_id)
            memory = ConversationMemory.from_dict(user_mem_data)
        elif not memory.entries:
            path = _avatar_memory_file(current_avatar)
            if path.exists():
                try:
                    data = json.loads(path.read_text())
                    memory = ConversationMemory.from_dict(data)
                except Exception:
                    memory = ConversationMemory()
        res = await handle_intent(text, from_text=from_text)
        if user_id:
            data = await memory.to_dict()
            save_user_memory(user_id, data)
        _current_user_id = ""
        return res

def _avatar_memory_file(avatar: str) -> Path:
    """Return the memory file path for a given avatar."""
    safe = avatar.replace("/", "_").replace("..", "_")
    return MEMORY_DIR / f"conversation_memory_{safe}.json"

async def _clean_memory_artifacts():
    """Remove Whisper hallucination entries and dead-end fallbacks from conversation memory on startup.
    
    Preserves disfluencies (um, uh, hmm, etc.) as they are real speech cues.
    Only removes true noise artifacts: foreign script hallucinations,
    repeated-word patterns, known Whisper media hallucinations, and
    hardcoded fallback responses that pollute context.
    """
    async with memory._lock:
        original_len = len(memory.entries)
        cleaned = []
        for entry in memory.entries:
            text = entry.text.strip()
            # Remove hardcoded fallback responses that pollute context
            if entry.role == "assistant":
                if text in ("Hmm, let me think on that.", "let me think on that"):
                    continue
            # Skip noise entries from user side — but preserve disfluencies
            if entry.role == "user":
                if is_hallucination(text) or text in PHANTOMS:
                    continue
                # Don't filter entries that contain disfluencies — they're real speech
                if len(text) < 2:
                    continue
            cleaned.append(entry)
        
        if len(cleaned) < original_len:
            removed = original_len - len(cleaned)
            logger.info(f"Memory cleanup: removed {removed} noise artifacts from {original_len} entries")
            memory.entries.clear()
            for entry in cleaned:
                memory.entries.append(entry)

# ─── AMBIENT LEARNING ──────────────────────────────────────────
# Learns from ambient speech even when not directly addressed.
# Extracts topics, names, preferences, and context from overheard conversations.
AMBIENT_MEMORY_FILE = MEMORY_DIR / "ambient_learned.json"
AMBIENT_LEARN_COOLDOWN = 60.0  # Don't learn same text within 60s
_ambient_last_learned = ""

async def learn_from_ambient(text: str):
    """Process ambient speech for learning — extracts key information.
    
    This runs on speech detected WITHOUT wake words (ambient listening).
    It extracts topics, names, preferences, and context, then stores
    them in ambient memory for future reference.
    """
    global _ambient_last_learned
    if not text or len(text) < 5:
        return
    # Dedup: skip if same text learned recently
    if text == _ambient_last_learned:
        return
    _ambient_last_learned = text
    
    try:
        # Load existing ambient memory
        ambient = {}
        if AMBIENT_MEMORY_FILE.exists():
            try:
                ambient = json.loads(AMBIENT_MEMORY_FILE.read_text())
            except Exception:
                ambient = {}
        
        # Extract key information using simple heuristics
        # (avoid LLM call for ambient — too expensive; use pattern matching)
        topics = []
        names = []
        preferences = []
        
        text_lower = text.lower()
        
        # Extract names (capitalized words not at sentence start)
        import re
        words = text.split()
        for i, w in enumerate(words):
            if i > 0 and w[0].isupper() and w.isalpha() and len(w) > 2:
                if w.lower() not in ['the', 'and', 'but', 'for', 'not', 'you', 'can', 'had', 'her', 'was', 'one', 'our', 'out', 'has', 'his', 'how', 'its', 'may', 'new', 'now', 'old', 'see', 'way', 'who', 'did', 'get', 'let', 'say', 'she', 'too', 'use']:
                    names.append(w)
        
        # Extract topics (common phrases)
        topic_patterns = [
            (r'about (.+?)(?:\.|,|$)', 'topic'),
            (r'interested in (.+?)(?:\.|,|$)', 'interest'),
            (r'like[sd]? (.+?)(?:\.|,|$)', 'preference'),
            (r'love[sd]? (.+?)(?:\.|,|$)', 'preference'),
            (r'hate[sd]? (.+?)(?:\.|,|$)', 'dislike'),
            (r'need[sd]? (.+?)(?:\.|,|$)', 'need'),
            (r'want[sd]? (.+?)(?:\.|,|$)', 'want'),
            (r'going to (.+?)(?:\.|,|$)', 'plan'),
            (r'plan[sd]? to (.+?)(?:\.|,|$)', 'plan'),
        ]
        for pattern, category in topic_patterns:
            matches = re.findall(pattern, text_lower)
            for m in matches:
                topics.append({"category": category, "text": m.strip()[:100]})
        
        # Store if we found something useful
        if names or topics:
            entry = {
                "text": text[:200],
                "timestamp": time.time(),
                "names": list(set(names))[:5],
                "topics": topics[:5],
            }
            # Keep last 50 ambient entries
            if "entries" not in ambient:
                ambient["entries"] = []
            ambient["entries"].append(entry)
            ambient["entries"] = ambient["entries"][-50:]
            
            # Update name registry
            if names:
                if "known_names" not in ambient:
                    ambient["known_names"] = {}
                for n in names:
                    if n not in ambient["known_names"]:
                        ambient["known_names"][n] = {"first_seen": time.time(), "count": 0}
                    ambient["known_names"][n]["count"] += 1
                    ambient["known_names"][n]["last_seen"] = time.time()
            
            # Save
            AMBIENT_MEMORY_FILE.parent.mkdir(parents=True, exist_ok=True)
            AMBIENT_MEMORY_FILE.write_text(json.dumps(ambient, indent=2))
            logger.info(f"Ambient learning: extracted {len(names)} names, {len(topics)} topics from '{text[:50]}...'")
    
    except Exception as e:
        logger.debug(f"Ambient learning error: {e}")

def get_ambient_knowledge() -> dict:
    """Retrieve ambient learned knowledge for use in responses."""
    if AMBIENT_MEMORY_FILE.exists():
        try:
            return json.loads(AMBIENT_MEMORY_FILE.read_text())
        except Exception:
            pass
    return {}

# ─── HARDWARE & OS INTEGRATIONS ─────────────────────────────────
async def whisper_stt(audio_bytes: bytes = None, file_path: Path = None, content_type: str = "audio/wav", filename: str = "input.wav") -> str:
    """Transcribe audio using faster-whisper-server HTTP API with audio pre-processing.
    
    Returns transcribed text or empty string if:
    - Audio is too quiet / noise floor
    - Whisper language confidence is too low
    - Transcription is detected as a hallucination
    """
    wav_data = audio_bytes
    if file_path and file_path.exists():
        wav_data = file_path.read_bytes()
        ext = file_path.suffix.lower()
        mime_map = {".wav": "audio/wav", ".webm": "audio/webm", ".ogg": "audio/ogg", ".mp3": "audio/mpeg", ".m4a": "audio/mp4"}
        content_type = mime_map.get(ext, content_type)
        filename = file_path.name
    if not wav_data or len(wav_data) < 100:
        return ""

    # Pre-process audio: convert to 16kHz mono WAV — boost volume so server VAD doesn't strip it
    try:
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-y",
            "-i", "pipe:0",
            "-af", "highpass=f=80,lowpass=f=8000,volume=3.0,loudnorm=I=-16:TP=-1.5:LRA=11",
            "-ar", "16000",
            "-ac", "1",
            "-sample_fmt", "s16",
            "-f", "wav",
            "pipe:1",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        processed, _ = await proc.communicate(input=wav_data)
        if processed and len(processed) > 100:
            wav_data = processed
            content_type = "audio/wav"
            filename = "input.wav"
    except Exception as e:
        logger.debug(f"Audio pre-processing failed (using raw): {e}")

    # Use verbose_json to get language detection info + force English
    try:
        c = await _get_whisper_client()
        files = {"file": (filename, wav_data, content_type)}
        data = {
            "model": WHISPER_MODEL,
            "response_format": "verbose_json",
            "language": "en",
            "vad_filter": str(WHISPER_VAD_FILTER).lower(),
            "beam_size": str(WHISPER_BEAM_SIZE),
            "temperature": str(WHISPER_TEMPERATURE),
            "condition_on_previous_text": str(WHISPER_CONDITION_ON_PREV).lower(),
            "initial_prompt": WHISPER_INITIAL_PROMPT,
        }
        r = await c.post(f"{WHISPER_SERVER_URL}/v1/audio/transcriptions", files=files, data=data)
        if r.status_code == 200:
            # Try to parse verbose_json response for language confidence
            try:
                result = r.json()
                text = result.get("text", "").strip()
                detected_lang = result.get("language", "")
                # Some servers return language_probability, others don't
                lang_prob = result.get("language_probability", result.get("probability", 1.0))
                if lang_prob is None:
                    lang_prob = 1.0
                
                # If Whisper detected a non-English language despite us forcing English,
                # or confidence is very low, treat as noise
                if detected_lang and detected_lang not in ("en", "eng", ""):
                    logger.debug(f"STT: non-English detected (lang={detected_lang}, prob={lang_prob:.2f}), discarding: {text[:50]}")
                    return ""
                if lang_prob < 0.5:
                    logger.debug(f"STT: low confidence ({lang_prob:.2f}), discarding: {text[:50]}")
                    return ""
                    
                return normalize_text(text)
            except (json.JSONDecodeError, KeyError):
                # Fallback: response is plain text
                return normalize_text(r.text)
        logger.debug(f"STT server returned {r.status_code}: {r.text[:200]}")
    except Exception as e:
        logger.debug(f"STT error: {e}")
    
    # Fallback: try without verbose_json (some servers don't support it)
    try:
        c = await _get_whisper_client()
        files = {"file": (filename, wav_data, content_type)}
        data = {
            "model": WHISPER_MODEL,
            "response_format": "text",
            "language": "en",
            "vad_filter": str(WHISPER_VAD_FILTER).lower(),
            "beam_size": str(WHISPER_BEAM_SIZE),
            "temperature": str(WHISPER_TEMPERATURE),
            "condition_on_previous_text": str(WHISPER_CONDITION_ON_PREV).lower(),
            "initial_prompt": WHISPER_INITIAL_PROMPT,
        }
        r = await c.post(f"{WHISPER_SERVER_URL}/v1/audio/transcriptions", files=files, data=data)
        if r.status_code == 200:
            return normalize_text(r.text)
    except Exception as e:
        logger.debug(f"STT fallback error: {e}")
    return ""

# ─── SSML HELPERS ──────────────────────────────────────────────
# Mood → SSML prosody presets for expressive speech
SSML_PRESETS = {
    "calm":    '<prosody rate="medium" pitch="+5%" volume="medium">%s</prosody>',
    "curious": '<prosody rate="medium" pitch="+12%" volume="medium">%s</prosody>',
    "cheerful":'<prosody rate="fast" pitch="+22%" volume="loud">%s</prosody>',
    "excited": '<prosody rate="x-fast" pitch="+28%" volume="x-loud">%s</prosody>',
    "gentle":  '<prosody rate="slow" pitch="+2%" volume="soft">%s</prosody>',
    "warm":    '<prosody rate="medium" pitch="+8%" volume="medium">%s</prosody>',
    "creative":'<prosody rate="medium" pitch="+12%" volume="medium">%s</prosody>',
    "worried": '<prosody rate="slow" pitch="-5%" volume="soft">%s</prosody>',
    "sad":     '<prosody rate="x-slow" pitch="-8%" volume="soft">%s</prosody>',
    "angry":   '<prosody rate="fast" pitch="-8%" volume="loud">%s</prosody>',
}

# Per-character SSML prosody adjustments layered ON TOP of the base mood preset.
# These nudge rate/pitch so the character's natural voice colours the mood expression —
# e.g. Bear's "cheerful" is still slower and deeper than Puppy's "cheerful".
# Format: (rate_modifier, pitch_modifier_pct)
CHAR_SSML_NUDGE: dict[str, tuple[str, int]] = {
    # char    rate-nudge    pitch-nudge (percentage points added to preset)
    "puppy":  ("medium",    0),    # baseline — no change
    "fox":    ("fast",     +8),    # always a little faster and higher
    "cat":    ("medium",   -4),    # flatten mood peaks — stays measured
    "bear":   ("medium",   -2),    # steady presence, not caricatured
    "bunny":  ("x-fast",  +10),    # always excited baseline
    "owl":    ("slow",     -6),    # deliberate, never rushed
    "deer":   ("medium",   -2),    # gentle nudge down — calm presence
    "wolf":   ("fast",     -8),    # fast but low — clipped intensity
    "raccoon":("fast",     +4),    # quick and a touch bright
}

def wrap_ssml_for_char(text: str, mood: str, char_key: str) -> str:
    """Wrap text in SSML prosody blending base mood preset with per-character nudge."""
    template = SSML_PRESETS.get(mood, SSML_PRESETS["calm"])
    # Extract the base rate and pitch from the preset string
    rate_match  = re.search(r'rate="([^"]+)"', template)
    pitch_match = re.search(r'pitch="([+-]?\d+)%"', template)
    base_rate  = rate_match.group(1)  if rate_match  else "medium"
    base_pitch = int(pitch_match.group(1)) if pitch_match else 0

    char_rate_nudge, char_pitch_nudge = CHAR_SSML_NUDGE.get(char_key or "puppy", ("medium", 0))

    # Blend: character rate wins if it's more extreme than the mood rate
    rate_order = ["x-slow", "slow", "medium", "fast", "x-fast"]
    base_idx  = rate_order.index(base_rate)  if base_rate  in rate_order else 2
    nudge_idx = rate_order.index(char_rate_nudge) if char_rate_nudge in rate_order else 2
    final_rate = rate_order[max(0, min(4, (base_idx + nudge_idx) // 2 +
                                         (1 if nudge_idx > base_idx else
                                         -1 if nudge_idx < base_idx else 0)))]

    # Pitch: add nudge to base
    final_pitch = base_pitch + char_pitch_nudge
    pitch_str = f"+{final_pitch}%" if final_pitch >= 0 else f"{final_pitch}%"

    vol_match = re.search(r'volume="([^"]+)"', template)
    vol = vol_match.group(1) if vol_match else "medium"

    return (f'<speak><prosody rate="{final_rate}" pitch="{pitch_str}" '
            f'volume="{vol}">{text}</prosody></speak>')

def strip_ssml(text: str) -> str:
    """Remove SSML tags leaving only plain text for Piper TTS."""
    return re.sub(r'<[^>]+>', '', text).strip()

def wrap_ssml(text: str, mood: str = "calm") -> str:
    """Wrap plain text in SSML prosody based on mood."""
    template = SSML_PRESETS.get(mood, SSML_PRESETS["calm"])
    return f'<speak>{template.replace("%s", text, 1)}</speak>'

def mood_from_text(text: str) -> str:
    t = text.lower()
    if any(w in t for w in ["sorry", "apologize", "my fault", "forgive"]):
        return "gentle"
    if any(w in t for w in ["great", "awesome", "fun", "cool", "happy", "yay", "wow"]):
        return "cheerful"
    if any(w in t for w in ["excited", "amazing", "incredible", "thrilled"]):
        return "excited"
    if any(w in t for w in ["worried", "concerned", "nervous", "afraid"]):
        return "worried"
    if any(w in t for w in ["sad", "unfortunate", "miss", "regret"]):
        return "sad"
    if any(w in t for w in ["angry", "furious", "annoyed"]):
        return "angry"
    if any(w in t for w in ["warm", "cozy", "comfort", "snug"]):
        return "warm"
    if "?" in t:
        return "curious"
    return "calm"

async def speak(text: str, use_toast: bool = True, char_key: str = None):
    """Speak text via Piper TTS with SSML-expressive prosody and per-character voice."""
    global LILLY_IS_SPEAKING, LILLY_IS_THINKING, LILLY_MOOD, LAST_SPOKEN, PHONEME_QUEUE, MOUTH_OPEN
    global AUDIO_CACHE, AUDIO_CACHE_ID
    raw_text = text.replace('\n', ' ').strip()
    if not raw_text:
        return 0

    LILLY_IS_SPEAKING = True
    LILLY_IS_THINKING = False

    # Derive mood from text content
    LILLY_MOOD = mood_from_text(raw_text)

    # Generate SSML envelope blending mood + per-character voice nudge
    _char = char_key or current_avatar or "puppy"
    ssml_text = wrap_ssml_for_char(raw_text, LILLY_MOOD, _char)

    # Strip SSML for Piper (doesn't support SSML natively), keep for logging/UI
    clean = strip_ssml(ssml_text)

    # Show toast on phone simultaneously with speech
    if use_toast:
        asyncio.create_task(termux_run(
            ["termux-toast", "-s", "-g", "bottom", clean[:200]],
            timeout=3.0,
        ))

    # Look up per-character voice profile (each avatar has its own ONNX model)
    voice = CHAR_VOICE.get(char_key or current_avatar or 'puppy', CHAR_VOICE['puppy'])
    model_path = str(VOICES_DIR / voice["model"]) if VOICES_DIR.exists() else PIPER_VOICE

    # Generate audio with Piper (in thread to avoid blocking event loop)
    audio_aid = 0
    piper_found = os.path.exists(PIPER_BIN)
    voice_found = os.path.exists(model_path)
    if piper_found and voice_found:
        try:
            def _run_piper():
                # Apply per-character voice profile
                pace_scale = voice["length_scale"]
                noise_scale = voice["noise_scale"]
                noise_w = voice["noise_w"]
                proc = subprocess.Popen(
                    [PIPER_BIN, "--model", model_path, "--output-raw",
                     "--noise-scale", f"{noise_scale:.3f}", "--noise-w", f"{noise_w:.3f}",
                     "--length-scale", f"{pace_scale:.2f}"],
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                )
                raw, stderr = proc.communicate(input=(clean + "\n").encode(), timeout=30.0)
                if proc.returncode != 0:
                    logger.error(f"Piper TTS failed (rc={proc.returncode}): {stderr.decode()[:200]}")
                return raw
            raw = await asyncio.to_thread(_run_piper)
            # Apply pitch shift for character voice distinctiveness
            pitch_semitones = voice.get("pitch_shift", 0)
            if raw and pitch_semitones != 0:
                raw = _pitch_shift_audio(raw, pitch_semitones)
            if raw and len(raw) > 44:
                import struct, io, wave
                buf = io.BytesIO()
                with wave.open(buf, "wb") as w:
                    w.setnchannels(1)
                    w.setsampwidth(2)
                    w.setframerate(22050)
                    w.writeframes(raw)
                async with AUDIO_CACHE_LOCK:
                    audio_aid = AUDIO_CACHE_ID + 1
                    AUDIO_CACHE_ID = audio_aid
                    AUDIO_CACHE[audio_aid] = buf.getvalue()
                    while len(AUDIO_CACHE) > 5:
                        oldest = min(AUDIO_CACHE)
                        del AUDIO_CACHE[oldest]
        except Exception as e:
            logger.error(f"Piper TTS error: {e}")
    else:
        logger.warning(f"Piper TTS not available — PIPER_BIN={PIPER_BIN} (exists={piper_found}), PIPER_VOICE={PIPER_VOICE} (exists={voice_found})")

    # Set spoken text AFTER caching audio so pollState sees audio_id too
    LAST_SPOKEN = clean
    # Also expose the SSML version via a global for future use
    LAST_SSML = ssml_text

    phoneme_timings, total_dur = estimate_phoneme_duration(clean)
    PHONEME_QUEUE.clear()
    for dur in phoneme_timings:
        PHONEME_QUEUE.append(dur)

    # Don't block — let the sleep run in background so /api/cmd returns immediately
    # (allows browser to play audio while the user gesture is still active)
    async def _finish_speech():
        global LILLY_IS_SPEAKING, CONVERSATION_LAST_ACTIVITY, MIC_COOLDOWN_UNTIL
        try:
            await asyncio.sleep(total_dur)
        finally:
            LILLY_IS_SPEAKING = False
            # ── Fix #1: hold mic quiet for MIC_COOLDOWN_SECS after Lilly stops speaking
            MIC_COOLDOWN_UNTIL = time.time() + MIC_COOLDOWN_SECS
            CONVERSATION_LAST_ACTIVITY = time.time()
            PHONEME_QUEUE.clear()
            MOUTH_OPEN = 0.0
    asyncio.create_task(_finish_speech())

    return audio_aid

# ─── TERMUX SSH HELPER ──────────────────────────────────────────
# Uses SSH ControlMaster multiplexing — handshake once, reuse for all commands.
SSH_CONTROL_SOCKET = "/tmp/lilly_ssh_mux_%h_%p"

# ─── TERMUX THROTTLE CONTROL ───
_TERMUX_SEMAPHORE = asyncio.Semaphore(3)        # max 3 concurrent SSH commands
_TERMUX_RATE_LIMIT: list[float] = []            # timestamps of recent commands
_TERMUX_RATE_MAX = 10                            # max commands per RATE_WINDOW
_TERMUX_RATE_WINDOW = 5.0                        # seconds

async def _termux_throttle():
    """Wait until we're under the rate limit, then record a call."""
    global _TERMUX_RATE_LIMIT
    now = time.time()
    # Prune old entries
    _TERMUX_RATE_LIMIT = [t for t in _TERMUX_RATE_LIMIT if now - t < _TERMUX_RATE_WINDOW]
    if len(_TERMUX_RATE_LIMIT) >= _TERMUX_RATE_MAX:
        sleep_needed = _TERMUX_RATE_LIMIT[0] + _TERMUX_RATE_WINDOW - now
        if sleep_needed > 0:
            logger.warning(f"Termux throttle: rate limit hit, sleeping {sleep_needed:.1f}s")
            await asyncio.sleep(sleep_needed)
    _TERMUX_RATE_LIMIT.append(time.time())

async def termux_run(args: list[str], timeout: float = 10.0) -> tuple[str, str]:
    """Run a command on the phone via SSH. Returns (stdout, stderr)."""
    import shlex
    host = os.environ.get("TERMUX_SSH_HOST", "")
    port = os.environ.get("TERMUX_SSH_PORT", "8022")
    user = os.environ.get("TERMUX_SSH_USER", "")
    if not host or not user:
        return "", "SSH not configured (TERMUX_SSH_HOST/USER not set)"
    cmd_str = " ".join(shlex.quote(a) for a in args)
    ssh_cmd = [
        "ssh",
        "-p", port,
        "-o", "ConnectTimeout=5",
        "-o", "StrictHostKeyChecking=no",
        "-o", "ControlMaster=auto",
        "-o", f"ControlPath={SSH_CONTROL_SOCKET}",
        "-o", "ControlPersist=600",
        "-o", "ServerAliveInterval=30",
        "-o", "ServerAliveCountMax=3",
        f"{user}@{host}",
        cmd_str,
    ]
    try:
        await _termux_throttle()
        async with _TERMUX_SEMAPHORE:
            proc = await asyncio.create_subprocess_exec(
                *ssh_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            if proc.returncode != 0:
                logger.warning(f"SSH command failed (rc={proc.returncode}): {cmd_str!r} stderr={stderr.decode().strip()[:200]}")
        return stdout.decode(), stderr.decode()
    except asyncio.TimeoutError:
        logger.warning(f"SSH timeout ({timeout}s): {cmd_str!r}")
        return "", "SSH timeout"
    except FileNotFoundError:
        logger.error("ssh client not found in container")
        return "", "ssh client not found in container"
    except Exception as e:
        logger.warning(f"SSH error: {e}")
        return "", str(e)

async def ssh_cleanup_stale():
    """Kill orphaned sshd-session processes on the phone."""
    await termux_run(
        ["pkill", "-f", "sshd-session -R"],
        timeout=5.0,
    )

async def ssh_check() -> bool:
    host = os.environ.get("TERMUX_SSH_HOST", "")
    port = os.environ.get("TERMUX_SSH_PORT", "8022")
    user = os.environ.get("TERMUX_SSH_USER", "")
    if not host or not user:
        return False
    try:
        proc = await asyncio.create_subprocess_exec(
            "ssh", "-p", port,
            "-o", "ConnectTimeout=3",
            "-o", "StrictHostKeyChecking=no",
            "-o", "BatchMode=yes",
            "-o", "ServerAliveInterval=30",
            "-o", "ServerAliveCountMax=3",
            f"{user}@{host}",
            "echo ok",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5.0)
        return proc.returncode == 0 and b"ok" in stdout
    except Exception:
        return False

async def ssh_start_on_phone() -> tuple[bool, str]:
    """Try to start sshd on the phone. Returns (success, message)."""
    host = os.environ.get("TERMUX_SSH_HOST", "")
    port = os.environ.get("TERMUX_SSH_PORT", "8022")
    user = os.environ.get("TERMUX_SSH_USER", "")
    if not host or not user:
        return False, "SSH not configured"
    # First check if already running
    if await ssh_check():
        return True, "sshd already running"
    # Try to connect and start sshd — this may fail if sshd isn't running at all
    try:
        proc = await asyncio.create_subprocess_exec(
            "ssh", "-p", port,
            "-o", "ConnectTimeout=5",
            "-o", "StrictHostKeyChecking=no",
            "-o", "BatchMode=yes",
            f"{user}@{host}",
            "sshd 2>&1; echo DONE:$?",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=8.0)
        out = stdout.decode().strip()
        if proc.returncode == 0 and "DONE:0" in out:
            return True, "sshd started"
        return False, f"ssh returned {proc.returncode}: {out}"
    except Exception as e:
        return False, str(e)

async def ssh_stop_on_phone() -> tuple[bool, str]:
    """Stop sshd on the phone. Returns (success, message)."""
    out, err = await termux_run(["pkill", "sshd"], timeout=5.0)
    return True, "sshd stopped" if not err else err

_SENSOR_LOCK = asyncio.Lock()

# Cache for batch sensor readings — avoids re-reading for concurrent consumers
_BATCH_SENSOR_CACHE: dict = {}
_BATCH_SENSOR_CACHE_TS: float = 0.0
_BATCH_SENSOR_TTL: float = 3.0  # seconds — keep sensor data fresh

SENSOR_SERVER_OK: bool = False
SENSOR_SERVER_FAIL_COUNT: int = 0

async def termux_sensor_read(sensor_name: str, timeout: float = 15.0) -> Optional[list]:
    """Read a sensor via the HTTP sensor server on Termux."""
    global SENSOR_SERVER_OK, SENSOR_SERVER_FAIL_COUNT
    try:
        c = await _get_sensor_client()
        r = await c.get(f"{SENSOR_SERVER_URL}/sensors/{sensor_name}/live", timeout=timeout)
        if r.status_code == 200:
            SENSOR_SERVER_OK = True
            SENSOR_SERVER_FAIL_COUNT = 0
            data = r.json()
            return data.get("values")
        return None
    except Exception as e:
        SENSOR_SERVER_FAIL_COUNT += 1
        if SENSOR_SERVER_FAIL_COUNT > 3:
            SENSOR_SERVER_OK = False
        logger.debug(f"Sensor server read failed ({sensor_name}): {e}")
        return None

async def termux_sensor_read_all(timeout: float = 20.0) -> dict:
    """Read ALL sensors via the HTTP sensor server (cached). Returns {sensor_name: [values]}."""
    global _BATCH_SENSOR_CACHE, _BATCH_SENSOR_CACHE_TS, SENSOR_SERVER_OK, SENSOR_SERVER_FAIL_COUNT
    now = time.time()
    if _BATCH_SENSOR_CACHE and (now - _BATCH_SENSOR_CACHE_TS) < _BATCH_SENSOR_TTL:
        return _BATCH_SENSOR_CACHE

    try:
        c = await _get_sensor_client()
        r = await c.get(f"{SENSOR_SERVER_URL}/sensors/all", timeout=timeout)
        if r.status_code == 200:
            SENSOR_SERVER_OK = True
            SENSOR_SERVER_FAIL_COUNT = 0
            data = r.json()
            sensors = data.get("sensors", {})
            # Merge any live browser sensor data on top (browser data fills gaps
            # when SSH/Termux isn't available, and enriches the agents' felt-world
            # when it is — e.g. the browser's orientation while the phone is held).
            # Browser sensors are per-user — only merge the current user's device data.
            bkey = _browser_key()
            user_browser = _browser_sensors.get(bkey, {})
            user_browser_ts = _browser_sensors_ts.get(bkey, 0)
            browser_age = now - user_browser_ts if user_browser_ts else 9999
            if user_browser and browser_age < _BROWSER_SENSOR_TTL:
                for k, v in user_browser.items():
                    if k.startswith("_"):
                        continue  # internal keys (battery, etc.) — skip
                    if k not in sensors:  # don't overwrite Termux reading
                        sensors[k] = v
            _BATCH_SENSOR_CACHE = sensors
            _BATCH_SENSOR_CACHE_TS = now
            return sensors
    except Exception as e:
        SENSOR_SERVER_FAIL_COUNT += 1
        if SENSOR_SERVER_FAIL_COUNT > 3:
            SENSOR_SERVER_OK = False
        logger.debug(f"Sensor server batch read failed: {e}")

    # ── SSH/Termux unreachable — fall back entirely to browser sensor data ──
    # The agents can still feel the world through the browser when the phone
    # SSH tunnel isn't connected (e.g. web-only use, desktop browser, etc.)
    # Only use the current user's own device sensors.
    bkey = _browser_key()
    user_browser = _browser_sensors.get(bkey, {})
    user_browser_ts = _browser_sensors_ts.get(bkey, 0)
    browser_age = now - user_browser_ts if user_browser_ts else 9999
    if user_browser and browser_age < _BROWSER_SENSOR_TTL:
        logger.debug("termux_sensor_read_all: SSH unavailable, using browser sensor data")
        filtered = {k: v for k, v in user_browser.items() if not k.startswith("_")}
        return filtered
    return {}

# ─── SENSOR DELTA TRACKING (Fix #3) ─────────────────────────────

# Minimum change thresholds to trigger a proactive comment
_SENSOR_THRESHOLDS = {
    "light":        500.0,   # lux — going from bright room to dark/outside
    "temperature":  2.0,     # °C
    "pressure":     3.0,     # hPa — weather front
    "accelerometer": 8.0,    # m/s² magnitude change — picked up / put down
    "proximity":    1.0,     # near/far flip
    "step":         30.0,    # steps taken since last check
}

_SENSOR_COMMENTS = {
    "light_drop":   ["Oh, it just got darker — did you go inside?",
                     "The light dropped, are you somewhere cozy now?",
                     "I can feel it getting dimmer — clouds maybe?"],
    "light_rise":   ["Ooh, it got much brighter! Did you go outside?",
                     "Bright! That's a big jump in light — sun came out?"],
    "temp_drop":    ["It's getting cooler — somewhere colder now?",
                     "Temperature just dipped. Did you step outside?"],
    "temp_rise":    ["Getting warmer! Are you near a heat source?",
                     "I can feel the temperature climbing."],
    "pickup":       ["Oh hi! You picked me up.",
                     "Hey, you're holding me again! What are we doing?"],
    "putdown":      ["You set me down — I'll wait here.",
                     "Going hands-free? I'm here when you need me."],
    "pressure_drop":["The pressure is dropping — might be a storm coming!",
                     "Barometric pressure is falling. Weather incoming?"],
    "walking":      ["Are we on the move? I can feel the steps!",
                     "We're walking! I love exploring."],
}

async def check_sensor_deltas():
    """Compare current sensors to previous snapshot; speak if interesting changes occur."""
    global _PREV_SENSOR_SNAPSHOT, _LAST_PROACTIVE_COMMENT

    # Throttle: don't comment more than once every 90 seconds
    if time.time() - _LAST_PROACTIVE_COMMENT < 90:
        return
    if LILLY_IS_SPEAKING or LILLY_IS_THINKING or PENDING_INTENT:
        return

    sensors = await fetch_all_sensors(timeout=3.0)
    if not sensors:
        return

    comment = None

    def _mag(vals):
        return math.sqrt(sum(v*v for v in vals)) if vals else 0.0

    def _first(vals):
        return vals[0] if vals else 0.0

    for name, values in sensors.items():
        prev = _PREV_SENSOR_SNAPSHOT.get(name)
        if not values or not prev:
            continue
        n = name.lower()

        if "light" in n or "illuminance" in n:
            delta = _first(values) - _first(prev)
            if delta < -_SENSOR_THRESHOLDS["light"]:
                comment = random.choice(_SENSOR_COMMENTS["light_drop"])
            elif delta > _SENSOR_THRESHOLDS["light"]:
                comment = random.choice(_SENSOR_COMMENTS["light_rise"])

        elif "temperature" in n:
            delta = _first(values) - _first(prev)
            if delta < -_SENSOR_THRESHOLDS["temperature"]:
                comment = random.choice(_SENSOR_COMMENTS["temp_drop"])
            elif delta > _SENSOR_THRESHOLDS["temperature"]:
                comment = random.choice(_SENSOR_COMMENTS["temp_rise"])

        elif "pressure" in n or "barometer" in n:
            delta = _first(values) - _first(prev)
            if delta < -_SENSOR_THRESHOLDS["pressure"]:
                comment = random.choice(_SENSOR_COMMENTS["pressure_drop"])

        elif "accelerometer" in n:
            prev_mag = _mag(prev)
            curr_mag = _mag(values)
            delta = curr_mag - prev_mag
            if delta > _SENSOR_THRESHOLDS["accelerometer"] and curr_mag > 15:
                comment = random.choice(_SENSOR_COMMENTS["pickup"])
            elif delta < -_SENSOR_THRESHOLDS["accelerometer"] and curr_mag < 11:
                comment = random.choice(_SENSOR_COMMENTS["putdown"])

        elif "step" in n:
            delta = _first(values) - _first(prev)
            if delta > _SENSOR_THRESHOLDS["step"]:
                comment = random.choice(_SENSOR_COMMENTS["walking"])

        if comment:
            break

    # Update snapshot
    _PREV_SENSOR_SNAPSHOT = {k: list(v) for k, v in sensors.items()}

    if comment:
        _LAST_PROACTIVE_COMMENT = time.time()
        logger.info(f"Sensor delta comment: {comment}")
        await speak(comment)

# ─── NATIVE APP LAUNCHER (VIA SSH INTO TERMUX) ─────────────────

async def app_process_monkey_intent(component: str, intent_action: str = "", uri_template: str = "", skill_arg: str = ""):
    """Launch an Android app via SSH in freeform (half-screen) overlay mode."""
    freeform_flag = ["--windowingMode", "5"]
    if intent_action and uri_template:
        url = uri_template.replace("{}", urllib.parse.quote(skill_arg)) if skill_arg else uri_template
        cmd = ["am", "start", "-a", intent_action, "-d", url] + freeform_flag
        if component:
            cmd.extend(["-p", component])
        await termux_run(cmd, timeout=6.0)
    elif intent_action:
        cmd = ["am", "start", "-a", intent_action] + freeform_flag
        if component:
            cmd.extend(["-p", component])
        await termux_run(cmd, timeout=6.0)
    elif component:
        await termux_run(["am", "start", "-p", component] + freeform_flag, timeout=6.0)

# ─── ANDROID NOTIFICATIONS (VIA SSH INTO TERMUX) ───────────────
_NOTIF_COUNTER = 0

async def send_notification(title: str, content: str, priority: str = "default",
                            alert_once: bool = False, ongoing: bool = False,
                            notification_id: Optional[int] = None) -> bool:
    """Send an Android notification via the HTTP sensor server.
    
    Priority levels: default, high, max, low, min
    """
    global _NOTIF_COUNTER

    notif_id = notification_id if notification_id is not None else _NOTIF_COUNTER
    _NOTIF_COUNTER += 1

    try:
        c = await _get_sensor_client()
        r = await c.post(
            f"{SENSOR_SERVER_URL}/notification/send",
            params={"title": title, "content": content, "priority": priority},
            timeout=5.0,
        )
        return r.status_code == 200
    except Exception as e:
        logger.debug(f"Notification send failed: {e}")
        return False

async def dismiss_notification(notification_id: int):
    """Dismiss a notification via the sensor server."""
    try:
        c = await _get_sensor_client()
        await c.post(
            f"{SENSOR_SERVER_URL}/shell",
            params={"cmd": f"termux-notification --cancel {notification_id}"},
            timeout=3.0,
        )
    except Exception:
        pass

from enum import Enum

class Archetype(Enum):
    OBSERVER  = "observer"
    CREATOR   = "creator"
    EXPLORER  = "explorer"
    CAREGIVER = "caregiver"

# Map archetype → notification priority
_ARCHETYPE_NOTIF_PRIORITY = {
    Archetype.OBSERVER: "low",
    Archetype.CREATOR: "default",
    Archetype.EXPLORER: "high",
    Archetype.CAREGIVER: "high",
}

async def proactive_notify(message: str, archetype: Archetype):
    """Send a proactive suggestion as a notification with appropriate priority."""
    priority = _ARCHETYPE_NOTIF_PRIORITY.get(archetype, "default")
    title_map = {
        Archetype.OBSERVER: "Lilly noticed",
        Archetype.CREATOR: "Heads up",
        Archetype.EXPLORER: "Adventure calls",
        Archetype.CAREGIVER: "Lilly suggests",
    }
    title = title_map.get(archetype, "Lilly")
    await send_notification(title, message, priority=priority, alert_once=True)

async def background_mic_loop():
    """Continuous microphone capture + wake-word detection via SSH to phone."""
    global LILLY_IS_SPEAKING, BACKGROUND_MIC_ACTIVE, LAST_HEARD, WAKE_STATE
    global PHONE_SSH_OK, PHONE_SSH_LAST_CHECK, PHONE_SSH_LAST_ERROR, PHONE_SSH_FAIL_COUNT
    global CONSECUTIVE_NOISE_COUNT, NOISE_PAUSE_UNTIL, MIC_COOLDOWN_UNTIL
    global USER_MIC_ENERGY, USER_MIC_PACE, BROWSER_MIC_LAST_READY
    global current_avatar
    wav_file = WORKSPACE / "sys_mic.wav"
    PHONE_REC_PATH = "/sdcard/sys_mic.m4a"

    while True:
        if not BACKGROUND_MIC_ACTIVE and not CONVERSATION_MODE:
            await asyncio.sleep(0.3)
            continue
        if LILLY_IS_SPEAKING:
            await asyncio.sleep(0.3)
            continue

        # ── Fix #1: post-speech cooldown — don't feed Lilly's voice back into Whisper
        if time.time() < MIC_COOLDOWN_UNTIL:
            await asyncio.sleep(0.1)
            continue

        # Auto-pause on sustained noise: skip recording cycles to save compute
        if NOISE_PAUSE_UNTIL > 0.0:
            if time.time() < NOISE_PAUSE_UNTIL:
                await asyncio.sleep(1.0)
                continue
            else:
                # Pause expired — resume listening, reset counter
                CONSECUTIVE_NOISE_COUNT = 0
                NOISE_PAUSE_UNTIL = 0.0
                logger.info("Mic loop: noise pause expired, resuming listening")

        # Skip if browser mic was active within the last 30s (auto-stales when user closes browser)
        if BROWSER_MIC_ACTIVE or (time.time() - BROWSER_MIC_LAST_READY) < 30.0:
            await asyncio.sleep(0.3)
            continue
        host = os.environ.get("TERMUX_SSH_HOST", "")
        port = os.environ.get("TERMUX_SSH_PORT", "8022")
        user = os.environ.get("TERMUX_SSH_USER", "")
        if not host or not user:
            await asyncio.sleep(5.0)
            continue

        # SSH health check before every cycle
        if not await ssh_check():
            PHONE_SSH_OK = False
            PHONE_SSH_FAIL_COUNT += 1
            PHONE_SSH_LAST_CHECK = time.strftime("%H:%M:%S")
            PHONE_SSH_LAST_ERROR = "sshd not reachable"
            # Exponential backoff: 5s -> 10s -> 20s -> 30s cap
            _mic_backoff = min(MIC_SSH_BACKOFF_BASE * (2 ** min(PHONE_SSH_FAIL_COUNT - 1, 3)), MIC_SSH_BACKOFF_MAX)
            logger.warning(f"Mic loop: SSH down (fail #{PHONE_SSH_FAIL_COUNT}), backoff {_mic_backoff:.0f}s")
            # Auto-restart sshd after 3 consecutive failures
            if PHONE_SSH_FAIL_COUNT == 3:
                logger.warning("Mic loop: attempting auto-restart of sshd on phone...")
                try:
                    ok, msg = await ssh_start_on_phone()
                    if ok:
                        logger.info(f"Mic loop: sshd auto-restart: {msg}")
                    else:
                        logger.warning(f"Mic loop: sshd auto-restart failed: {msg}")
                except Exception as e:
                    logger.warning(f"Mic loop: sshd auto-restart error: {e}")
            await asyncio.sleep(_mic_backoff)
            continue

        if not PHONE_SSH_OK:
            logger.info("Mic loop: SSH connection restored")
        PHONE_SSH_OK = True
        PHONE_SSH_FAIL_COUNT = 0
        PHONE_SSH_LAST_CHECK = time.strftime("%H:%M:%S")
        PHONE_SSH_LAST_ERROR = ""

        # Clean up stale sshd sessions every 20 cycles
        cycle_count = getattr(background_mic_loop, '_cycle_count', 0) + 1
        background_mic_loop._cycle_count = cycle_count
        if cycle_count % 20 == 0:
            await ssh_cleanup_stale()

        try:
            logger.debug("Mic loop: starting recording cycle")
            wav_file.unlink(missing_ok=True)

            # Batched: stop old recording + remove old file + start new recording in one SSH call
            logger.debug("Mic loop: starting 3s recording")
            out, err = await termux_run(
                ["sh", "-c",
                 f"termux-microphone-record -q 2>/dev/null; "
                 f"rm -f {PHONE_REC_PATH}; "
                 f"termux-microphone-record -f {PHONE_REC_PATH} -l 3"],
                timeout=15.0,
            )
            if err and "error" in err.lower():
                logger.warning(f"Mic loop: record start error: {err.strip()}")
                PHONE_SSH_LAST_ERROR = err.strip()
                await asyncio.sleep(2.0)
                continue
            logger.debug("Mic loop: recording started, sleeping 3.2s")

            await asyncio.sleep(3.2)

            if LILLY_IS_SPEAKING or not BACKGROUND_MIC_ACTIVE:
                continue

            # Copy recording from phone via SCP
            logger.debug("Mic loop: scp from phone")
            scp_proc = await asyncio.create_subprocess_exec(
                "scp", "-P", port,
                "-o", "StrictHostKeyChecking=no",
                "-o", "ConnectTimeout=5",
                f"{user}@{host}:{PHONE_REC_PATH}",
                str(wav_file.with_suffix(".m4a")),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                _, scp_stderr = await asyncio.wait_for(scp_proc.communicate(), timeout=15.0)
            except asyncio.TimeoutError:
                scp_proc.kill()
                PHONE_SSH_LAST_ERROR = "SCP timed out"
                logger.warning("Mic loop: SCP timed out")
                continue

            if scp_proc.returncode != 0:
                err_msg = scp_stderr.decode().strip() if scp_stderr else "unknown"
                PHONE_SSH_LAST_ERROR = f"SCP failed (rc={scp_proc.returncode}): {err_msg}"
                logger.warning(f"Mic loop: {PHONE_SSH_LAST_ERROR}")
                continue

            logger.debug("Mic loop: scp done")

            raw_file = wav_file.with_suffix(".m4a")
            if not raw_file.exists():
                PHONE_SSH_LAST_ERROR = "SCP completed but file missing"
                logger.warning("Mic loop: file not found after SCP")
                continue
            if raw_file.stat().st_size < 1500:
                sz = raw_file.stat().st_size
                PHONE_SSH_LAST_ERROR = f"Audio file too small ({sz} bytes)"
                logger.warning(f"Mic loop: audio file only {sz} bytes, skipping")
                raw_file.unlink(missing_ok=True)
                continue

            # Convert to WAV with gentle normalization (reduced from 25dB to avoid amplifying noise floor)
            ff = await asyncio.create_subprocess_exec(
                "ffmpeg", "-y", "-i", str(raw_file),
                "-af", "volume=10dB,compand=attacks=0.3:decays=0.8:points=-80/-80|-45/-45|-27/-20|0/-12:gain=3",
                "-ar", "16000", "-ac", "1", str(wav_file),
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
            )
            _, ff_err = await ff.communicate()
            if ff.returncode != 0:
                PHONE_SSH_LAST_ERROR = f"ffmpeg failed: {ff_err.decode()[:200]}"
                logger.warning(f"Mic loop: {PHONE_SSH_LAST_ERROR}")
                raw_file.unlink(missing_ok=True)
                continue
            raw_file.unlink(missing_ok=True)

            if wav_file.exists():
                # Check audio volume - skip if too quiet (noise floor)
                vol = await asyncio.create_subprocess_exec(
                    "ffmpeg", "-i", str(wav_file),
                    "-af", "volumedetect", "-f", "null", "/dev/null",
                    stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
                )
                _, vol_err = await vol.communicate()
                vol_output = vol_err.decode()
                mean_vol = -100.0
                for line in vol_output.split("\n"):
                    if "mean_volume" in line:
                        try:
                            mean_vol = float(line.split(":")[-1].strip().replace(" dB", ""))
                        except ValueError:
                            pass
                        break
                logger.debug(f"Mic loop: audio volume {mean_vol:.1f} dB")

                # Skip audio that is too quiet (ambient noise) or too loud (clipping/distortion)
                # -38dB catches most room tone that Whisper would hallucinate from
                if mean_vol < -38.0:
                    PHONE_SSH_LAST_ERROR = f"Audio too quiet ({mean_vol:.1f} dB)"
                    logger.debug(f"Mic loop: skipping quiet audio ({mean_vol:.1f} dB)")
                    wav_file.unlink(missing_ok=True)
                    continue
                if mean_vol > 0.0:
                    PHONE_SSH_LAST_ERROR = f"Audio clipping ({mean_vol:.1f} dB)"
                    logger.debug(f"Mic loop: skipping clipped audio ({mean_vol:.1f} dB)")
                    wav_file.unlink(missing_ok=True)
                    continue

                text = await whisper_stt(file_path=wav_file)
                logger.debug(f"Mic loop: transcription result: \"{text}\"")
                if text and len(text) > 2 and text not in PHANTOMS and not is_hallucination(text):
                    # Valid speech detected — reset noise counter
                    CONSECUTIVE_NOISE_COUNT = 0
                    LAST_HEARD = text

                    # ── Fix #2: update tone-matching globals from this audio chunk ──
                    # Energy: map mean_vol (dB, typically -38 to -10) → 0.0..1.0
                    if mean_vol > -38.0:
                        raw_energy = min(1.0, max(0.0, (mean_vol + 38.0) / 28.0))
                        USER_MIC_ENERGY = 0.7 * USER_MIC_ENERGY + 0.3 * raw_energy  # smooth
                    # Pace: words per second (AUDIO_REC_SECS assumed ~4s per loop cycle)
                    word_count = len(text.split())
                    if word_count >= 2:
                        estimated_dur = max(1.0, word_count * 0.35)  # ~350ms per word baseline
                        raw_pace = word_count / estimated_dur
                        USER_MIC_PACE = 0.7 * USER_MIC_PACE + 0.3 * raw_pace  # smooth

                    # Check wake words for ALL avatars
                    matched_avatar, wake_score = match_any_wake_word(text)
                    has_wake = bool(matched_avatar)
                    if has_wake:
                        if matched_avatar != current_avatar:
                            current_avatar = matched_avatar
                            logger.info(f"Mic loop: switched to avatar '{current_avatar}' via wake word")
                    else:
                        has_wake, confidence = fuzzy_wake_match(text, current_avatar)
                    WAKE_STATE["last_heard_has_wake"] = has_wake

                    # Learn from ALL ambient speech — even without wake words
                    asyncio.create_task(learn_from_ambient(text))

                    if has_wake:
                        asyncio.create_task(handle_intent(text))
                    elif WAKE_STATE["listening"] or CONVERSATION_MODE:
                        asyncio.create_task(handle_intent(text))
                elif text and len(text) > 2:
                    # Whisper returned text but it was flagged as hallucination
                    CONSECUTIVE_NOISE_COUNT += 1
                    logger.debug(f"Mic loop: noise #{CONSECUTIVE_NOISE_COUNT}: '{text[:30]}'")
                    if CONSECUTIVE_NOISE_COUNT >= MAX_CONSECUTIVE_NOISE:
                        NOISE_PAUSE_UNTIL = time.time() + NOISE_PAUSE_DURATION
                        logger.info(f"Mic loop: {CONSECUTIVE_NOISE_COUNT} consecutive noise detections, pausing {NOISE_PAUSE_DURATION}s")
                else:
                    # Empty/short transcription — still count as noise (probably silence)
                    CONSECUTIVE_NOISE_COUNT += 1
                    if CONSECUTIVE_NOISE_COUNT >= MAX_CONSECUTIVE_NOISE:
                        NOISE_PAUSE_UNTIL = time.time() + NOISE_PAUSE_DURATION
                        logger.info(f"Mic loop: {CONSECUTIVE_NOISE_COUNT} consecutive quiet cycles, pausing {NOISE_PAUSE_DURATION}s")
        except Exception as e:
            logger.error(f"Mic loop error: {e}")
            PHONE_SSH_LAST_ERROR = str(e)
            await asyncio.sleep(2.0)

# ─── SENSOR INTEGRATIONS — Pixel 10 Full Coverage ───────────────
SENSOR_DEFS = {
    # Motion sensors
    "ICM45631 Accelerometer": {
        "triggers": ["shake", "tilt", "move", "movement", "accelerate", "motion", "acceleration", "g force"],
        "desc": "accelerometer",
        "format": lambda v: (
            random.choice([
                "I can feel us moving! The motion is real — like we're walking or shaking things up.",
                "Oh, we're definitely in motion! I can feel the movement through the sensors.",
                "The accelerometer is picking up movement — we're not sitting still!",
            ]) if (v[0]**2 + v[1]**2 + v[2]**2)**0.5 > 11
            else random.choice([
                "Everything's still right now — no motion detected. Calm and steady.",
                "We're perfectly still. I can feel the quiet through the sensors.",
                "No movement at all — just peace and quiet.",
            ])
        ),
    },
    "ICM45631 Gyroscope": {
        "triggers": ["gyro", "rotation", "spin", "turning", "angular", "gyroscope"],
        "desc": "gyroscope",
        "format": lambda v: (
            random.choice([
                "I can feel us rotating! The phone is turning — spin spin spin!",
                "Whoa, we're spinning! I can feel the rotation through the gyroscope.",
                "The device is rotating — I can sense the twist in the air.",
            ]) if (v[0]**2 + v[1]**2 + v[2]**2)**0.5 > 0.5
            else "No rotation right now — everything's steady."
        ),
    },
    "ICM45631 Gyroscope-Uncalibrated": {
        "triggers": ["raw gyro", "uncalibrated gyro"],
        "desc": "raw gyroscope",
        "format": lambda v: "I'm picking up some raw rotation data — the sensors are working hard!",
    },
    "ICM45631 Accelerometer-Uncalibrated": {
        "triggers": ["raw accel", "uncalibrated accel"],
        "desc": "raw accelerometer",
        "format": lambda v: "I'm picking up raw motion data — the accelerometer is working overtime!",
    },
    "ICM45631 Motion Detect": {
        "triggers": ["motion detect", "movement detect", "any movement"],
        "desc": "motion detection",
        "format": lambda v: (
            "I just felt something move! The motion sensor picked up activity."
            if v[0] == 1.0
            else "Everything's still — no motion detected."
        ),
    },
    "ICM45631 Stationary Detect": {
        "triggers": ["stationary", "still", "not moving", "device still"],
        "desc": "stationary detection",
        "format": lambda v: (
            "We're perfectly still — the phone isn't moving at all."
            if v[0] == 1.0
            else "The phone is moving — I can feel the motion!"
        ),
    },
    "Significant Motion (wake-up)": {
        "triggers": ["significant motion", "big move", "car motion", "vehicle"],
        "desc": "significant motion",
        "format": lambda v: (
            "Whoa, big movement! Something significant just happened — like getting into a car or standing up quickly."
            if v[0] == 1.0
            else "No big movements — everything's calm."
        ),
    },

    # Position sensors
    "MMC5616 Magnetometer": {
        "triggers": ["magnetometer", "compass", "magnetic field", "bearing", "direction"],
        "desc": "magnetometer",
        "format": lambda v: _compass_heading(v[0], v[1]),
    },
    "MMC5616 Magnetometer-Uncalibrated": {
        "triggers": ["raw compass", "raw magnetic", "uncalibrated magnetometer"],
        "desc": "raw magnetometer",
        "format": lambda v: "I can feel the magnetic fields around us — the raw sensor data is coming through!",
    },
    "Rotation Vector Sensor": {
        "triggers": ["rotation vector", "orientation vector", "device rotation"],
        "desc": "rotation vector",
        "format": lambda v: "I can feel how the phone is oriented in 3D space — it's like having a inner ear for the device!",
    },
    "Game Rotation Vector Sensor": {
        "triggers": ["game rotation", "gaming orientation"],
        "desc": "game rotation",
        "format": lambda v: "I can feel the precise rotation for gaming — smooth and accurate!",
    },
    "Geomagnetic Rotation Vector Sensor": {
        "triggers": ["geomagnetic rotation", "magnetic orientation"],
        "desc": "geomagnetic rotation",
        "format": lambda v: "I'm using the Earth's magnetic field to figure out which way is north — nature's GPS!",
    },
    "Gravity Sensor": {
        "triggers": ["gravity", "g force direction", "which way is down"],
        "desc": "gravity",
        "format": lambda v: "I can feel which way is down — gravity is pulling at " + _gravity_dir(v) + "!",
    },
    "Linear Acceleration Sensor": {
        "triggers": ["linear acceleration", "movement without gravity", "true acceleration"],
        "desc": "linear acceleration",
        "format": lambda v: (
            "I can feel the real movement — gravity is filtered out, so this is pure motion!"
            if (v[0]**2 + v[1]**2 + v[2]**2)**0.5 > 0.5
            else "No real movement right now — just gravity doing its thing."
        ),
    },
    "Orientation Sensor": {
        "triggers": ["orientation", "portrait", "landscape", "phone position", "screen orientation"],
        "desc": "orientation",
        "format": lambda v: _screen_orientation(v[0], v[1], v[2]),
    },
    "Device Orientation": {
        "triggers": ["device orientation", "face up", "face down", "display orientation"],
        "desc": "device orientation",
        "format": lambda v: (
            "The phone is face up — I can see the sky if I had eyes!"
            if v[0] == 0.0
            else (
                "The phone is face down — screen toward the ground."
                if v[0] == 1.0
                else "I can feel how the phone is oriented right now."
            )
        ),
    },

    # Environmental sensors
    "SPL07003 Barometer": {
        "triggers": ["barometer", "pressure", "air pressure", "atmospheric", "barometric"],
        "desc": "barometer",
        "format": lambda v: (
            "I can feel the air pressure — " + _pressure_trend(v[0]).lower()
        ),
    },
    "SPL07003 Temperature": {
        "triggers": ["internal temp", "device temp", "chip temp", "sensor temperature"],
        "desc": "barometer temperature",
        "format": lambda v: (
            "The phone is feeling a bit warm — " + f"{v[0]:.1f}°C" + " inside the device."
            if v[0] > 35
            else "The phone is running at a comfortable temperature — " + f"{v[0]:.1f}°C" + "."
        ),
    },
    "ICM45631 Temperature": {
        "triggers": ["imu temp", "motion chip temp", "gyro temperature"],
        "desc": "IMU temperature",
        "format": lambda v: "The motion sensor is running at " + f"{v[0]:.1f}°C" + " — feeling just right!",
    },
    "TMD3743 Ambient Light": {
        "triggers": ["ambient light", "light level", "brightness", "how bright", "lux", "illuminance"],
        "desc": "ambient light",
        "format": lambda v: (
            "I can see the light around us — " + _lux_desc(v[0]).lower()
        ),
    },
    "TMD3743 Color": {
        "triggers": ["color sensor", "light color", "rgb", "ambient color"],
        "desc": "color sensor",
        "format": lambda v: "I can sense the colors in the light around us — it's like seeing without eyes!",
    },
    "VD6282 Rear Light Sensor": {
        "triggers": ["rear light", "back light", "camera light", "rear sensor"],
        "desc": "rear ambient light",
        "format": lambda v: "I can sense the light from the back of the phone — the world behind us is " + ("bright!" if v[0] > 500 else "dimmer than the front."),
    },
    "Auto Brightness": {
        "triggers": ["auto brightness", "brightness sensor", "display brightness"],
        "desc": "auto brightness",
        "format": lambda v: "The phone is adjusting its brightness automatically — it's trying to match the light around us!",
    },

    # Proximity
    "TMD3743 Proximity (wake-up)": {
        "triggers": ["proximity", "near", "close", "something near", "object near", "ear detect"],
        "desc": "proximity",
        "format": lambda v: (
            "I can feel something close to the screen — like a hand or face nearby!"
            if v[0] > 0
            else "Nothing near the screen right now."
        ),
    },
    "Proximity(Voice Calls) Sensor (wake-up)": {
        "triggers": ["call proximity", "phone call sensor", "ear proximity", "voice call"],
        "desc": "call proximity",
        "format": lambda v: (
            "The phone is at your ear — you're probably on a call! I'll keep it down."
            if v[0] > 0
            else "Phone is away from your ear — you're not on a call right now."
        ),
    },
    "AAD Proximity Sensor (wake-up)": {
        "triggers": ["aad proximity", "always on proximity"],
        "desc": "always-on proximity",
        "format": lambda v: (
            "I can sense something close by — the always-on proximity sensor is active!"
            if v[0] > 0
            else "The area around the phone is clear."
        ),
    },
    "Proximity Gated Single Tap Gesture (wake-up)": {
        "triggers": ["tap", "single tap", "proximity tap", "touch gesture"],
        "desc": "tap gesture",
        "format": lambda v: (
            "I felt a tap! Someone touched the screen."
            if v[0] == 1.0
            else "No taps detected right now."
        ),
    },
    "Proximity Gated Long Press Gesture (wake-up)": {
        "triggers": ["long press", "hold gesture", "proximity hold"],
        "desc": "long press gesture",
        "format": lambda v: (
            "I felt a long press — someone is holding down on the screen!"
            if v[0] == 1.0
            else "No long press detected right now."
        ),
    },

    # Step sensors
    "Step Detector": {
        "triggers": ["step", "step detected", "walk", "steps just now", "footstep"],
        "desc": "step detector",
        "format": lambda v: (
            "I just felt a step! We're moving!"
            if v[0] == 1.0
            else "No steps right now — we're standing still."
        ),
    },
    "Step Counter": {
        "triggers": ["step count", "steps today", "how many steps", "walked", "pedometer"],
        "desc": "step counter",
        "format": lambda v: (
            "We've taken " + f"{v[0]:.0f}" + " steps so far today — that's " + (
                "a good start!" if v[0] < 1000
                else "getting there!" if v[0] < 5000
                else "a nice walk!" if v[0] < 10000
                else "a great workout!" if v[0] < 20000
                else "an amazing day for steps!"
            )
        ),
    },

    # Gesture sensors
    "Tilt Sensor (wake-up)": {
        "triggers": ["tilt", "tilt sensor", "phone tilted"],
        "desc": "tilt detection",
        "format": lambda v: (
            "I felt the phone tilt! Someone moved it."
            if v[0] == 1.0
            else "No tilt detected right now."
        ),
    },
    "Lift to Wake Sensor (wake-up)": {
        "triggers": ["lift", "pick up", "raised", "lift to wake"],
        "desc": "lift to wake",
        "format": lambda v: (
            "I felt the phone being lifted! Someone picked it up."
            if v[0] == 1.0
            else "The phone hasn't been lifted recently."
        ),
    },
    "Double Twist (wake-up)": {
        "triggers": ["twist", "double twist", "wrist twist"],
        "desc": "double twist",
        "format": lambda v: (
            "I felt a double twist! Someone rotated their wrist."
            if v[0] == 1.0
            else "No twist gesture detected right now."
        ),
    },
    "Quick Pickup Sensor (wake-up)": {
        "triggers": ["quick pickup", "fast pickup", "snatch"],
        "desc": "quick pickup",
        "format": lambda v: (
            "Whoa, quick pickup! Someone grabbed the phone fast!"
            if v[0] == 1.0
            else "No quick pickup detected right now."
        ),
    },
    "Binned Brightness (wake-up)": {
        "triggers": ["binned brightness", "brightness level", "light category"],
        "desc": "binned brightness",
        "format": lambda v: (
            "I can sense the light level — " +
            ("it's pitch dark!" if v[0] == 0
             else "quite dim." if v[0] == 1
             else "normal indoor lighting." if v[0] == 2
             else "bright light!" if v[0] == 3
             else "direct sunlight!")
        ),
    },

    # Virtual / system sensors
    "Dynamic Sensor Manager": {
        "triggers": ["sensor manager", "available sensors", "list sensors", "what sensors", "sensors list"],
        "desc": "sensor manager",
        "format": lambda v: "I'm checking what sensors are available — the sensor system is active and ready!",
    },
    "Camera V-Sync 0": {
        "triggers": ["camera vsync", "camera frame", "camera 0"],
        "desc": "camera 0 vsync",
        "format": lambda v: (
            "The camera is capturing frames — I can see the world!"
            if v[0] > 0
            else "The camera is idle right now."
        ),
    },
}

# Pre-computed normalized sensor lookups (avoids repeated normalize_text() calls)
_SENSOR_DEFS_NORM = {
    name: {
        "norm_name": normalize_text(name),
        "norm_triggers": [normalize_text(t) for t in cfg.get("triggers", [])],
        "norm_desc": normalize_text(cfg.get("desc", "")),
    }
    for name, cfg in SENSOR_DEFS.items()
}

SENSOR_TRIGGERS = {
    "weather":         ["weather", "forecast", "rain", "outside", "whats it like out"],
    "temperature":     ["temperature outside", "hot", "cold", "warm"],
    "notifications":   ["notifications", "messages", "alerts", "any messages", "notification"],
    "battery":         ["battery", "hungry", "power", "charge", "energy", "juice", "battery level"],
    "location":        ["where am i", "location", "address", "whats around", "my location", "gps"],
    "bluetooth":       ["bluetooth", "devices near", "nearby devices", "who is near", "devices around",
                         "phone near", "who's here", "anyone around", "whos nearby", "connected devices",
                         "paired devices", "bluetooth devices", "wireless devices"],
}

# ─── BLUETOOTH DEVICE MAPPING ────────────────────────────────────
# Maps MAC addresses or device names to human-friendly labels
BT_DEVICE_MAP_FILE = WORKSPACE / "bluetooth_devices.json"

def _load_bt_device_map() -> dict:
    """Load Bluetooth device name mappings."""
    if BT_DEVICE_MAP_FILE.exists():
        try:
            return json.loads(BT_DEVICE_MAP_FILE.read_text())
        except Exception:
            pass
    return {}

def _save_bt_device_map(mapping: dict):
    """Save Bluetooth device name mappings."""
    BT_DEVICE_MAP_FILE.write_text(json.dumps(mapping, indent=2))

def _rssi_to_distance(rssi: float, tx_power: float = -59.0, path_loss: float = 2.5) -> float:
    """Estimate distance in meters from RSSI using log-distance path loss model.
    
    Args:
        rssi: Received signal strength in dBm
        tx_power: RSSI at 1 meter (default -59 dBm for most phones)
        path_loss: Environment factor (2.0=open space, 2.5=typical indoor, 4.0=obstructed)
    
    Returns:
        Estimated distance in meters
    """
    if rssi == 0:
        return -1.0
    ratio = (tx_power - rssi) / (10 * path_loss)
    return round(10 ** ratio, 2)

def _distance_description(distance: float) -> str:
    """Human-friendly distance description."""
    if distance < 0:
        return "unknown distance"
    if distance < 1.0:
        return "right next to us"
    elif distance < 3.0:
        return "very close, within a few steps"
    elif distance < 10.0:
        return "nearby, in the same room or area"
    elif distance < 30.0:
        return "a bit further away, maybe in the next room"
    elif distance < 100.0:
        return "further out, probably outside"
    else:
        return "quite far away"

async def read_bluetooth_devices() -> list:
    """Read Bluetooth devices from the sensor server."""
    try:
        c = await _get_sensor_client()
        r = await c.get(f"{SENSOR_SERVER_URL}/bluetooth/scan/live", timeout=20.0)
        if r.status_code == 200:
            data = r.json()
            devices = data.get("devices", [])
            # Enrich with device name mappings
            bt_map = _load_bt_device_map()
            for dev in devices:
                addr = dev.get("address", "")
                name = dev.get("name", "Unknown")
                # Check if we have a custom label for this device
                if addr in bt_map:
                    dev["label"] = bt_map[addr]
                elif name in bt_map:
                    dev["label"] = bt_map[name]
                else:
                    dev["label"] = name
                # Add distance estimate
                rssi = dev.get("rssi", -100)
                dev["distance_m"] = _rssi_to_distance(rssi)
                dev["distance_desc"] = _distance_description(dev["distance_m"])
            return devices
    except Exception as e:
        logger.debug(f"Bluetooth scan failed: {e}")
    return []

def _compass_heading(x: float, y: float) -> str:
    """Convert magnetometer X/Y to compass direction.
    Android coordinate system: X=East, Y=North → heading = atan2(x, y)."""
    import math
    heading = math.degrees(math.atan2(x, y))
    if heading < 0:
        heading += 360
    dirs = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
    idx = round(heading / 45) % 8
    dir_name = dirs[idx]
    full_names = {
        "N": "North", "NE": "Northeast", "E": "East", "SE": "Southeast",
        "S": "South", "SW": "Southwest", "W": "West", "NW": "Northwest"
    }
    return random.choice([
        f"I can feel the magnetic pull — we're facing {full_names[dir_name]}! The compass is locked in.",
        f"The magnetic fields are telling me we're facing {full_names[dir_name]} — right around {heading:.0f} degrees.",
        f"I sense the Earth's magnetic field — we're facing {full_names[dir_name]}!",
    ])

def _gravity_dir(v) -> str:
    """Determine which way is down from gravity vector."""
    max_axis = max(range(3), key=lambda i: abs(v[i]))
    axis_names = ["X (right)", "Y (top)", "Z (face)"]
    dirs = ["right", "left", "top", "bottom", "face up", "face down"]
    mapping = {
        (0, True): "pointing right",
        (0, False): "pointing left",
        (1, True): "pointing up",
        (1, False): "pointing down",
        (2, True): "facing down (screen toward ground)",
        (2, False): "facing up (screen toward sky)",
    }
    return mapping.get((max_axis, v[max_axis] > 0), "unknown")

def _screen_orientation(azimuth: float, pitch: float, roll: float) -> str:
    """Determine screen orientation from orientation sensor."""
    if -45 <= roll <= 45 and -45 <= pitch <= 45:
        return f"Screen is flat (face up). Azimuth: {azimuth:.0f}°."
    if abs(roll) > abs(pitch):
        if roll > 45:
            return "Device is in landscape (right side up)."
        if roll < -45:
            return "Device is in landscape (left side up)."
    if pitch > 45:
        return "Device is in portrait (upside down)."
    if pitch < -45:
        return "Device is in portrait (normal)."
    return f"Orientation — azimuth {azimuth:.0f}°, pitch {pitch:.0f}°, roll {roll:.0f}°."

_trend_samples: list[float] = []

def _pressure_trend(pressure: float) -> str:
    """Simple pressure trend description."""
    _trend_samples.append(pressure)
    if len(_trend_samples) > 10:
        _trend_samples.pop(0)
    if len(_trend_samples) < 3:
        return "The weather seems stable right now."
    recent = _trend_samples[-3:]
    avg_old = sum(recent[:2]) / 2
    avg_new = sum(recent[1:]) / 2
    diff = avg_new - avg_old
    if abs(diff) < 0.3:
        return "The pressure is steady — weather looks like it'll hold."
    elif diff > 0:
        return "The pressure is rising — things are clearing up outside!"
    else:
        return "The pressure is dropping — rain might be on the way."

def _lux_desc(lux: float) -> str:
    if lux < 1: return "It's pitch dark around us — I can't see a thing!"
    if lux < 10: return "It's very dim — like a cozy room with curtains drawn."
    if lux < 50: return "The light is soft and dim — perfect for relaxing."
    if lux < 200: return "Normal indoor lighting — bright enough to see clearly."
    if lux < 500: return "It's bright in here — probably near a window."
    if lux < 10000: return "There's daylight around us — like being outside in the shade."
    return "The sun is shining bright — direct sunlight levels!"

async def _read_termux_sensor(sensor_name: str, timeout: float = 15.0) -> Optional[list]:
    """Read a sensor via the HTTP sensor server."""
    return await termux_sensor_read(sensor_name, timeout=timeout)

async def _list_termux_sensors(timeout: float = 5.0) -> list[str]:
    """List all available sensors via the HTTP sensor server."""
    try:
        c = await _get_sensor_client()
        r = await c.get(f"{SENSOR_SERVER_URL}/sensors/list", timeout=timeout)
        if r.status_code == 200:
            data = r.json()
            return data.get("sensors", [])
    except Exception:
        pass
    return []

async def get_sensor_snapshot() -> dict:
    """Gather multiple sensor readings at once for storytelling context.

    Returns a dict of sensor_name -> formatted_string for each available sensor.
    Uses batch read (single SSH call) instead of individual sensor reads.
    """
    # Skip if phone SSH is not connected
    if not PHONE_SSH_OK:
        return {"status": {"text": "Phone not connected", "raw": []}}

    # Core sensors to always try (most interesting for storytelling)
    core_sensors = [
        ("TMD3743 Ambient Light", "light"),
        ("ICM45631 Accelerometer", "motion"),
        ("Step Counter", "steps"),
    ]

    # Single batch read for all sensors
    all_data = await termux_sensor_read_all(timeout=15.0)

    snapshot = {}
    for sensor_name, label in core_sensors:
        values = all_data.get(sensor_name)
        if values is not None:
            cfg = SENSOR_DEFS.get(sensor_name, {})
            fmt = cfg.get("format", lambda v: f"{label}: {v}")
            snapshot[label] = {"text": fmt(values), "raw": values}

    # Add battery status (fast via sensor server)
    try:
        c = await _get_sensor_client()
        r = await c.get(f"{SENSOR_SERVER_URL}/battery", timeout=2.0)
        if r.status_code == 200:
            data = r.json().get("battery", {})
            pct = data.get("percentage", 0)
            snapshot["battery"] = {"text": f"Battery: {pct:.0f}%", "raw": [pct]}
    except Exception:
        pass

    return snapshot

def snapshot_to_narrative(snapshot: dict) -> str:
    """Convert sensor snapshot into a natural observation — like what a companion would notice in passing.

    Not a sensor report. Just the kind of thing you'd mention if you were sitting
    next to someone: 'it's bright in here', 'we're moving', 'battery's low'.
    Only include things that are actually worth noticing.
    """
    if not snapshot:
        return ""

    parts = []

    # Light — only mention if interesting (very dark, very bright, or changed)
    if "light" in snapshot:
        lux = snapshot["light"]["raw"][0] if snapshot["light"]["raw"] else 0
        if lux < 10:
            parts.append("It's dark in here")
        elif lux < 100:
            parts.append("It's pretty dim — cozy though")
        elif lux > 5000:
            parts.append("It's really bright — must be near a window or outside")
        # Normal indoor light (100-5000) — not worth mentioning

    # Motion — conversational, not technical
    if "motion" in snapshot:
        accel = snapshot["motion"]["raw"]
        if accel and len(accel) >= 3:
            total = (accel[0]**2 + accel[1]**2 + accel[2]**2)**0.5
            if total > 13:
                parts.append("We're moving around a lot")
            elif total > 11:
                parts.append("We're on the move")
            elif total < 9.5:
                parts.append("We're sitting still")

    # Pressure — only if it's a notable change or unusual
    if "pressure" in snapshot:
        hpa = snapshot["pressure"]["raw"][0] if snapshot["pressure"]["raw"] else 0
        if hpa < 995:
            parts.append("The pressure's low — might rain later")
        elif hpa > 1025:
            parts.append("High pressure — clear skies")

    # Steps — casual mention, not a fitness report
    if "steps" in snapshot:
        steps = snapshot["steps"]["raw"][0] if snapshot["steps"]["raw"] else 0
        if steps > 5000:
            parts.append(f"We've been active — {int(steps)} steps")
        elif steps > 1000 and steps < 2000:
            parts.append("We've been on our feet a bit today")

    # Battery — only if low or very full
    if "battery" in snapshot:
        pct = snapshot["battery"]["raw"][0] if snapshot["battery"]["raw"] else 100
        if pct < 15:
            parts.append("Battery's getting low — we should charge soon")
        elif pct < 30:
            parts.append("Battery's dipping a bit")

    # Browser camera vision — mention if camera is live and has recent detections
    if _browser_vision_description and (time.time() - _browser_vision_ts) < 12:
        parts.append(f"Camera sees: {_browser_vision_description}")

    if not parts:
        return ""

    return " ".join(parts)

async def query_sensor(phrase: str) -> Optional[str]:
    """Query Termux sensors with fallbacks. Returns response string or None."""
    p = normalize_text(phrase)

    # ── List all sensors ──
    if "list all sensors" in p or "all sensors" in p or "every sensor" in p:
        sensors = await _list_termux_sensors()
        if sensors:
            count = len(sensors)
            names = ", ".join(sensors[:8])
            if count > 8:
                names += f", and {count - 8} more"
            return random.choice([
                f"I have {count} sensors available — I can feel {names} and more!",
                f"Wow, I've got {count} sensors! I can sense {names} and others.",
                f"I'm equipped with {count} sensors — {names} are just a few of them!",
            ])
        return "I don't have access to the sensors right now — maybe they're not connected?"

    # ── Battery ──
    if any(w in p for w in SENSOR_TRIGGERS["battery"]):
        try:
            c = await _get_sensor_client()
            r = await c.get(f"{SENSOR_SERVER_URL}/battery/live", timeout=5.0)
            if r.status_code == 200:
                data = r.json().get("battery", {})
                pct = data.get("percentage", 0)
                if isinstance(pct, (int, float)):
                    if pct > 80:
                        return random.choice([
                            f"We're at {pct:.0f}% — I'm full of energy! Ready for anything.",
                            f"Battery is at {pct:.0f}% — plenty of juice left!",
                            f"{pct:.0f}% battery — we're looking good!",
                        ])
                    elif pct > 40:
                        return random.choice([
                            f"We're at {pct:.0f}% — doing alright, still have plenty of power.",
                            f"Battery is at {pct:.0f}% — not bad, we can keep going.",
                            f"{pct:.0f}% battery — we're in good shape.",
                        ])
                    elif pct > 20:
                        return random.choice([
                            f"We're at {pct:.0f}% — getting a bit low, might want to charge soon.",
                            f"Battery is at {pct:.0f}% — we're running a bit low.",
                            f"{pct:.0f}% battery — we should probably find a charger soon.",
                        ])
                    else:
                        return random.choice([
                            f"We're at {pct:.0f}% — I'm getting sleepy! Please charge me soon.",
                            f"Battery is at {pct:.0f}% — I'm running on fumes!",
                            f"{pct:.0f}% battery — I need some power soon or I'll fall asleep!",
                        ])
        except Exception:
            pass

    # ── Bluetooth Devices ──
    if any(w in p for w in SENSOR_TRIGGERS["bluetooth"]):
        devices = await read_bluetooth_devices()
        if devices:
            bt_map = _load_bt_device_map()
            # Check if user wants to name a device
            name_match = re.search(r'(?:name|call|label)\s+(.+?)\s+(?:as|to)\s+(.+)', p)
            if name_match:
                device_id = name_match.group(1).strip()
                new_label = name_match.group(2).strip()
                # Find matching device
                for dev in devices:
                    if device_id in dev.get("name", "").lower() or device_id in dev.get("address", "").lower():
                        key = dev["address"]
                        bt_map[key] = new_label
                        _save_bt_device_map(bt_map)
                        return random.choice([
                            f"Done! I'll remember {dev['name']} as {new_label} from now on.",
                            f"Got it! {dev['name']} is now {new_label} in my book.",
                            f"Sweet! I've labeled that device as {new_label}.",
                        ])
                return f"I couldn't find a device matching '{device_id}' nearby. Try scanning again."
            
            # Build device list
            parts = []
            for dev in devices[:8]:
                name = dev.get("label", dev.get("name", "Unknown"))
                distance = dev.get("distance_desc", "unknown distance")
                rssi = dev.get("rssi", -100)
                paired = "paired" if dev.get("paired") else "new"
                parts.append(f"{name} ({distance}, {paired})")
            
            if len(devices) == 1:
                dev = devices[0]
                name = dev.get("label", dev.get("name", "Unknown"))
                dist = dev.get("distance_desc", "unknown distance")
                return random.choice([
                    f"I can see {name} nearby — {dist}!",
                    f"Found {name}! It's {dist}.",
                    f"There's one device: {name}, {dist}.",
                ])
            elif len(devices) <= 4:
                device_list = ", ".join(parts)
                return random.choice([
                    f"I can sense {len(devices)} devices nearby: {device_list}.",
                    f"Found {len(devices)} Bluetooth devices: {device_list}.",
                    f"Nearby devices: {device_list}.",
                ])
            else:
                # Too many to list all — summarize
                paired_count = sum(1 for d in devices if d.get("paired"))
                new_count = len(devices) - paired_count
                nearby = [d for d in devices if d.get("distance_m", 999) < 5]
                nearby_names = [d.get("label", d.get("name", "?")) for d in nearby[:3]]
                nearby_str = ", ".join(nearby_names) if nearby_names else "none very close"
                return random.choice([
                    f"I can see {len(devices)} devices around us — {paired_count} paired, {new_count} new. "
                    f"Closest ones: {nearby_str}.",
                    f"There are {len(devices)} Bluetooth devices nearby. "
                    f"{paired_count} are paired, {new_count} are new. The closest: {nearby_str}.",
                ])
        else:
            return random.choice([
                "I don't see any Bluetooth devices nearby right now.",
                "No Bluetooth devices detected — the air is quiet!",
                "Nothing's showing up on Bluetooth. Maybe no one's around?",
            ])

    # ── Weather ──
    if any(w in p for w in SENSOR_TRIGGERS["weather"]):
        if shutil.which("curl"):
            try:
                proc = await asyncio.create_subprocess_exec(
                    "curl", "-s", "wttr.in/?format=%C+%t+%w+%h",
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
                )
                stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5.0)
                if stdout:
                    result = stdout.decode().strip()
                    return random.choice([
                        f"I can feel the weather outside — {result}. How's that sound?",
                        f"Right now it's {result} out there. What do you think?",
                        f"The weather is {result} — I can sense it through the air!",
                    ])
            except Exception:
                pass

    # ── Notifications ──
    if any(w in p for w in SENSOR_TRIGGERS["notifications"]):
        try:
            c = await _get_sensor_client()
            r = await c.get(f"{SENSOR_SERVER_URL}/notification/list", timeout=5.0)
            if r.status_code == 200:
                notifs = r.json().get("notifications", [])
                if notifs:
                    parts = []
                    for n in notifs[:6]:
                        title = n.get("title", "") or ""
                        content = n.get("content", "") or ""
                        package = n.get("package", "") or ""
                        app_name = package.split(".")[-1].replace(".", " ").title() if package else ""
                        if title.lower().startswith(app_name.lower()):
                            title = title[len(app_name):].strip().lstrip(":").strip()
                        snippet = content[:80] if content else ""
                        desc = f"{title}: {snippet}" if title and snippet else (title or snippet or "empty notification")
                        if app_name:
                            desc += f" (on {app_name})"
                        parts.append(desc)
                    if len(notifs) == 1:
                        return random.choice([
                            f"I just noticed a notification! {parts[0]}.",
                            f"Hey, there's something new — {parts[0]}.",
                            f"I can sense a notification — {parts[0]}.",
                        ])
                    return random.choice([
                        f"I'm picking up {len(notifs)} notifications — " + ". ".join(parts),
                        f"Hey, you've got {len(notifs)} new things! " + ". ".join(parts),
                        f"I can sense {len(notifs)} notifications waiting — " + ". ".join(parts),
                    ])
                return random.choice([
                    "No notifications right now — everything's quiet.",
                    "I don't sense any new notifications. All clear!",
                    "Nothing new in the notification department.",
                ])
        except Exception:
            pass

    # ── Match against all Pixel 10 sensor definitions ──
    for sensor_name, cfg in SENSOR_DEFS.items():
        norm = _SENSOR_DEFS_NORM[sensor_name]
        if norm["norm_name"] == p or norm["norm_name"] in p:
            values = await _read_termux_sensor(sensor_name)
            if values is not None:
                archetype_inferrer.record_sensor_query(sensor_name)
                return cfg["format"](values)

    matched_sensors = []
    for sensor_name, cfg in SENSOR_DEFS.items():
        if any(trigger in p for trigger in cfg["triggers"]):
            matched_sensors.append(sensor_name)

    for sensor_name in matched_sensors:
        values = await _read_termux_sensor(sensor_name)
        if values is not None:
            archetype_inferrer.record_sensor_query(sensor_name)
            return SENSOR_DEFS[sensor_name]["format"](values)

    for word in p.split():
        for sensor_name, cfg in SENSOR_DEFS.items():
            norm = _SENSOR_DEFS_NORM[sensor_name]
            if word in norm["norm_desc"] or any(word in t for t in norm["norm_triggers"]):
                values = await _read_termux_sensor(sensor_name)
                if values is not None:
                    archetype_inferrer.record_sensor_query(sensor_name)
                    return cfg["format"](values)

    return None

ARCHETYPE_PROFILES = {
    Archetype.OBSERVER: {
        "label": "Visual/Auditory Observer",
        "summary": "You learn by watching, reading, and planning before you act.",
        "style": "thoughtful, explanatory, step-by-step",
        "sensor_affinities": ["TMD3743 Ambient Light", "Orientation Sensor", "Device Orientation", "TMD3743 Color"],
        "conversation_keywords": ["read", "explain", "how", "why", "show me", "learn", "watch", "listen", "tell me about", "what is"],
        "proactive_checks": [
            {"sensor": "TMD3743 Ambient Light", "condition": lambda v: v[0] < 20, "cooldown_min": 30,
             "message": "It's getting dim — want me to suggest a well-lit spot for reading?"},
            {"sensor": "TMD3743 Ambient Light", "condition": lambda v: v[0] > 200 and v[0] < 2000, "cooldown_min": 60,
             "message": "Great lighting for focus work right now. Want me to read something to you?"},
        ],
        "conversation_prompt": "Want me to explain how that works?",
    },
    Archetype.CREATOR: {
        "label": "Hands-On Creator/Maker",
        "summary": "You think best by doing and building. You process through action.",
        "style": "direct, practical, action-oriented",
        "sensor_affinities": ["ICM45631 Accelerometer", "Step Counter", "Linear Acceleration Sensor"],
        "conversation_keywords": ["make", "build", "do", "try", "create", "fix", "diy", "project", "practice", "hands"],
        "proactive_checks": [
            {"sensor": "Step Counter", "condition": lambda v: v[0] < 50, "cooldown_min": 90,
             "message": "You haven't moved much lately. Want to do something hands-on? I can teach a quick DIY."},
            {"sensor": "ICM45631 Accelerometer", "condition": lambda v: max(abs(x) for x in v[:3]) > 3.0, "cooldown_min": 15,
             "message": "You're moving around! Need me to time something or keep track of reps?"},
        ],
        "conversation_prompt": "Want to try building something together?",
    },
    Archetype.EXPLORER: {
        "label": "Explorer/Seeker",
        "summary": "You're driven to move, discover, and explore. Variety keeps you engaged.",
        "style": "enthusiastic, curious, discovery-focused",
        "sensor_affinities": ["MMC5616 Magnetometer", "Step Counter", "Step Detector", "Significant Motion (wake-up)", "Gravity Sensor"],
        "conversation_keywords": ["explore", "find", "go", "walk", "outside", "discover", "adventure", "move", "travel", "new"],
        "proactive_checks": [
            {"sensor": "Step Counter", "condition": lambda v: v[0] < 30, "cooldown_min": 60,
             "message": "Feeling stationary. Want me to suggest a walk or a place to explore nearby?"},
            {"sensor": "Significant Motion (wake-up)", "condition": lambda v: v[0] == 1.0, "cooldown_min": 30,
             "message": "Looks like you're on the move! Want me to track this adventure?"},
            {"sensor": "MMC5616 Magnetometer", "condition": lambda v: True, "cooldown_min": 120,
             "message": "I can sense the magnetic field around us. Want to see which direction we're heading?"},
        ],
        "conversation_prompt": "What shall we discover next?",
    },
    Archetype.CAREGIVER: {
        "label": "Everyday Caregiver/Connector",
        "summary": "Your energy comes from connecting with others and nurturing.",
        "style": "warm, empathetic, community-minded",
        "sensor_affinities": ["Proximity(Voice Calls) Sensor (wake-up)", "TMD3743 Ambient Light", "AAD Proximity Sensor (wake-up)"],
        "conversation_keywords": ["message", "call", "friend", "family", "help", "people", "connect", "share", "someone", "together"],
        "proactive_checks": [
            {"sensor": "Proximity(Voice Calls) Sensor (wake-up)", "condition": lambda v: v[0] > 0, "cooldown_min": 45,
             "message": "On a call? Want me to take notes or remind you of something afterward?"},
            {"sensor": "TMD3743 Ambient Light", "condition": lambda v: v[0] > 500, "cooldown_min": 90,
             "message": "Bright and sunny — good day to check in with someone. Want me to help you message a friend?"},
        ],
        "conversation_prompt": "How are the people around you doing?",
    },
}

# ─── CONTEXTUAL ARCHETYPE INFERRER ──────────────────────────────
class ArchetypeInferrer:
    """Infers user archetype from sensor queries and conversation patterns over time."""

    def __init__(self):
        self.scores: dict[Archetype, float] = {a: 0.0 for a in Archetype}
        self._total_observations = 0
        self._inferrer_path = WORKSPACE / "archetype_inference.json"

    def record_sensor_query(self, sensor_name: str):
        """Boost archetype scores based on which sensors the user asks about."""
        for arch, profile in ARCHETYPE_PROFILES.items():
            if sensor_name in profile.get("sensor_affinities", []):
                self.scores[arch] += 0.3
        self._total_observations += 1
        self._maybe_persist()

    def record_conversation(self, text: str):
        """Boost archetype scores based on conversation keywords."""
        p = text.lower()
        for arch, profile in ARCHETYPE_PROFILES.items():
            for kw in profile.get("conversation_keywords", []):
                if kw in p:
                    self.scores[arch] += 0.15
        self._total_observations += 1
        self._maybe_persist()

    def record_sensor_value(self, sensor_name: str, values: list):
        """Learn from sensor readings that match archetype-specific patterns."""
        for arch, profile in ARCHETYPE_PROFILES.items():
            if sensor_name in profile.get("sensor_affinities", []):
                self.scores[arch] += 0.05
        self._total_observations += 1

    @property
    def best_guess(self) -> Archetype:
        """Return the highest-scoring archetype. Defaults to Observer if no data."""
        best = max(self.scores, key=self.scores.get)
        if self.scores[best] < 0.1:
            return Archetype.OBSERVER
        return best

    @property
    def confidence(self) -> float:
        """Confidence in the best guess (0.0 - 1.0)."""
        if self._total_observations < 3:
            return 0.0
        best = self.best_guess
        total = sum(self.scores.values())
        if total == 0:
            return 0.0
        return min(1.0, self.scores[best] / total)

    @property
    def enabled(self) -> bool:
        """Proactive suggestions activate once confidence passes threshold."""
        return self.confidence >= 0.4 and self._total_observations >= 5

    def _maybe_persist(self):
        if self._total_observations % 5 == 0:
            self.save()

    def save(self):
        try:
            self._inferrer_path.write_text(json.dumps({
                "scores": {k.value: round(v, 2) for k, v in self.scores.items()},
                "total": self._total_observations,
            }, indent=2))
        except Exception:
            pass

    def load(self):
        if self._inferrer_path.exists():
            try:
                data = json.loads(self._inferrer_path.read_text())
                for value_str, score in data.get("scores", {}).items():
                    try:
                        self.scores[Archetype(value_str)] = score
                    except ValueError:
                        pass
                self._total_observations = data.get("total", 0)
            except Exception:
                pass

archetype_inferrer = ArchetypeInferrer()

# ─── NEUROMORPHIC SYNAPSE ENGINE ─────────────────────────────────
# Sensors act as artificial synapses that learn, strengthen, and
# autonomously generate new skills through Hebbian plasticity.

SYNAPSE_MATRIX_FILE = WORKSPACE / "synapse_matrix.json"
SYNAPSE_SKILLS_FILE = WORKSPACE / "synapse_skills.json"

SENSOR_NAMES = [
    "accel", "gyro", "mag", "pressure", "light", "color", "prox",
    "aad_prox", "steps", "step_detect", "orientation", "device_orient",
    "gravity", "lin_accel", "imu_temp", "baro_temp", "sig_motion",
    "binned_bright", "tilt", "lift", "twist", "pickup",
]

@dataclass
class Synapse:
    """A single sensor synapse with Hebbian weight and memory trace."""
    sensor: str
    weight: float = 0.0
    last_value: Optional[float] = None
    firing_rate: float = 0.0
    potentiation: float = 0.0  # long-term potentiation (LTP)
    last_fired: float = 0.0
    co_firing: dict[str, float] = field(default_factory=dict)  # sensor -> co-firing strength

SYNAPSE_MATRIX: dict[str, Synapse] = {name: Synapse(sensor=name) for name in SENSOR_NAMES}

@dataclass
class SynapticPattern:
    """A learned sensor pattern that acts as a memory trace (like an engram)."""
    id: str
    label: str
    sensor_fingerprint: dict[str, float]
    timestamp: float
    strength: float  # how well-formed this memory is
    engagement_count: int = 0
    last_triggered: float = 0.0
    skill_description: str = ""

class DynamicSkill:
    """A sensor-aware skill that adapts to the user's archetype."""
    def __init__(self, name: str, description: str, sensor: str,
                 archetype_weights: dict, reactive_response: str):
        self.name = name
        self.description = description
        self.sensor = sensor
        self.archetype_weights = archetype_weights
        self.reactive_response = reactive_response

class SynapticMemory:
    """Plastic memory that stores and retrieves sensor patterns."""

    def __init__(self):
        self.patterns: list[SynapticPattern] = []
        self.skill_pool: list[DynamicSkill] = []
        self._load()

    @property
    def path(self) -> Path:
        return SYNAPSE_MATRIX_FILE

    def _extract_fingerprint(self, snapshot) -> dict[str, float]:
        fp: dict[str, float] = {}
        for name in SENSOR_NAMES:
            val = getattr(snapshot, name, None)
            if val and isinstance(val, (list, tuple)) and len(val) > 0:
                fp[name] = float(val[0])
        return fp

    def hebbian_update(self, snapshot):
        """Strengthen synapses that are active together (fire together, wire together)."""
        now = time.time()
        fp = self._extract_fingerprint(snapshot)
        active = list(fp.keys())

        for name in active:
            syn = SYNAPSE_MATRIX[name]
            dt = now - syn.last_fired if syn.last_fired else 1.0
            # Firing rate with exponential decay
            syn.firing_rate = 0.7 * syn.firing_rate + 0.3 / max(dt, 0.1)
            syn.weight = min(1.0, syn.weight + 0.05)
            syn.potentiation = min(1.0, syn.potentiation + 0.02)
            syn.last_value = fp[name]
            syn.last_fired = now

            # Co-firing: strengthen connections between simultaneously active sensors
            for other in active:
                if other != name:
                    syn.co_firing[other] = min(1.0, syn.co_firing.get(other, 0) + 0.03)

        # Decay unused synapses (STDP: spike-timing-dependent plasticity)
        for name, syn in SYNAPSE_MATRIX.items():
            if name not in active:
                syn.weight = max(0.0, syn.weight - 0.01)
                syn.potentiation = max(0.0, syn.potentiation - 0.005)
                if now - syn.last_fired > 300:
                    syn.firing_rate = max(0.0, syn.firing_rate - 0.1 * (now - syn.last_fired - 300) / 60)

    def learn_pattern(self, snapshot, label: str = ""):
        """Store current sensor fingerprint as a new synaptic pattern (engram)."""
        fp = self._extract_fingerprint(snapshot)
        if len(fp) < 2:
            return None

        # Check if pattern already exists (similarity match)
        for pattern in self.patterns:
            match = self._pattern_similarity(fp, pattern.sensor_fingerprint)
            if match > 0.7:
                pattern.strength = min(1.0, pattern.strength + 0.1)
                if label and not pattern.label.endswith(label):
                    pattern.label = label
                return pattern

        pid = f"pat_{int(time.time())}_{random.randint(100,999)}"
        pat = SynapticPattern(
            id=pid,
            label=label or f"pattern_{len(self.patterns)}",
            sensor_fingerprint=fp,
            timestamp=time.time(),
            strength=0.3,
        )
        self.patterns.append(pat)
        self._save()
        return pat

    def _pattern_similarity(self, a: dict[str, float], b: dict[str, float]) -> float:
        common = set(a) & set(b)
        if not common:
            return 0.0
        scores = []
        for k in common:
            diff = abs(a[k] - b[k]) / (max(abs(a[k]), abs(b[k]), 1))
            scores.append(1.0 - min(diff, 1.0))
        return sum(scores) / len(scores) if scores else 0.0

    def recall(self, snapshot, threshold: float = 0.5) -> Optional[SynapticPattern]:
        """Match current sensor state against stored patterns."""
        fp = self._extract_fingerprint(snapshot)
        best = None
        best_score = 0.0
        for pattern in self.patterns:
            score = self._pattern_similarity(fp, pattern.sensor_fingerprint)
            if score > best_score and score >= threshold:
                best_score = score
                best = pattern
        if best:
            best.last_triggered = time.time()
        return best

    async def generate_skills(self):
        """Use LLM to generate skill descriptions from strongly learned patterns."""
        threshold = 0.5
        candidates = [p for p in self.patterns if p.strength >= threshold and not p.skill_description]
        if not candidates:
            return

        for pat in candidates[:3]:  # generate at most 3 per cycle
            sensors_detail = ", ".join(
                f"{k}={v:.1f}" for k, v in list(pat.sensor_fingerprint.items())[:5]
            )
            persona = HIVE_PERSONAS.get(current_avatar, HIVE_PERSONAS["puppy"])
            prompt = (
                f"You are {persona['name']}. A recurrent sensor pattern has been detected: "
                f"{pat.label} with sensors [{sensors_detail}]. "
                f"Invent a 1-sentence skill name and a 1-sentence friendly offer "
                f"(as {persona['name']} speaking to the user) that leverages this sensor pattern "
                f"to help or engage the user. Format: SKILL: <name> | OFFER: <offer>"
            )
            try:
                resp = await llama_backend.chat([
                    {"role": "system", "content": "You are a creative AI companion."},
                    {"role": "user", "content": prompt}
                ], temperature=0.8, max_tokens=80)
                if resp and "|" in resp:
                    parts = resp.split("|")
                    skill_name = parts[0].replace("SKILL:", "").strip().lower().replace(" ", "_")
                    offer = parts[1].replace("OFFER:", "").strip()
                    pat.skill_description = offer
                    ds = DynamicSkill(
                        name=skill_name,
                        description=f"Auto-generated from sensor pattern: {pat.label}",
                        sensor=list(pat.sensor_fingerprint.keys())[0] if pat.sensor_fingerprint else "unknown",
                        archetype_weights={
                            Archetype.OBSERVER: 0.6,
                            Archetype.EXPLORER: 0.5,
                            Archetype.CREATOR: 0.4,
                            Archetype.CAREGIVER: 0.5,
                        },
                        reactive_response=offer,
                    )
                    self.skill_pool.append(ds)
            except Exception:
                pass
        self._save()

    def get_relevant_skill(self, cmd: str, primary: Archetype) -> Optional[DynamicSkill]:
        best = None
        best_w = 0.0
        for skill in self.skill_pool:
            w = skill.archetype_weights.get(primary, 0.0)
            p = normalize_text(cmd)
            for word in skill.description.lower().split():
                if word in p:
                    w += 0.15
            for word in skill.name.lower().split("_"):
                if word in p:
                    w += 0.25
            if w > best_w:
                best_w = w
                best = skill
        return best if best_w > 0.3 else None

    def _save(self):
        try:
            data = {
                "patterns": [
                    {
                        "id": p.id, "label": p.label, "timestamp": p.timestamp,
                        "strength": p.strength, "engagement_count": p.engagement_count,
                        "last_triggered": p.last_triggered,
                        "skill_description": p.skill_description,
                        "sensor_fingerprint": p.sensor_fingerprint,
                    }
                    for p in self.patterns
                ],
                "skills": [
                    {
                        "name": s.name, "description": s.description, "sensor": s.sensor,
                        "archetype_weights": {k.value: v for k, v in s.archetype_weights.items()},
                        "reactive_response": s.reactive_response,
                    }
                    for s in self.skill_pool
                ]
            }
            self.path.write_text(json.dumps(data, indent=2))
        except Exception:
            pass

    def _load(self):
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text())
                for pd in data.get("patterns", []):
                    self.patterns.append(SynapticPattern(**pd))
                for sd in data.get("skills", []):
                    aw = {Archetype(k): v for k, v in sd.get("archetype_weights", {}).items()}
                    self.skill_pool.append(DynamicSkill(
                        name=sd["name"], description=sd.get("description", ""),
                        sensor=sd.get("sensor", ""), archetype_weights=aw,
                        reactive_response=sd.get("reactive_response", ""),
                    ))
            except Exception:
                pass

synaptic_memory = SynapticMemory()


# ─── USER PROFILE (LEARNED PREFERENCES) ─────────────────────────
USER_PROFILE_FILE = WORKSPACE / "user_profile.json"

class UserProfile:
    """Learns from interactions: which contexts engage the user, daily routines, preferences."""

    def __init__(self):
        self.context_engagement: dict[str, int] = {}   # context → follow-up count
        self.context_ignores: dict[str, int] = {}       # context → ignored count
        self.sensor_interest: dict[str, float] = {}     # sensor → avg interest score
        self.interaction_count = 0
        self.last_active_hour = -1
        self.active_hours: dict[int, int] = {}          # hour → activity count
        self.load()

    @property
    def path(self) -> Path:
        return USER_PROFILE_FILE

    def record_context_engaged(self, context: str):
        self.context_engagement[context] = self.context_engagement.get(context, 0) + 1
        self._save()

    def record_context_ignored(self, context: str):
        self.context_ignores[context] = self.context_ignores.get(context, 0) + 1
        self._save()

    def record_interaction(self):
        self.interaction_count += 1
        hour = time.localtime().tm_hour
        self.active_hours[hour] = self.active_hours.get(hour, 0) + 1
        self.last_active_hour = hour
        if self.interaction_count % 5 == 0:
            self._save()

    def engagement_rate(self, context: str) -> float:
        engaged = self.context_engagement.get(context, 0)
        ignored = self.context_ignores.get(context, 0)
        total = engaged + ignored
        return engaged / total if total > 0 else 0.5

    def preferred_contexts(self, min_rate: float = 0.3) -> list[str]:
        return [c for c in self.context_engagement if self.engagement_rate(c) >= min_rate]

    def is_user_active_now(self) -> bool:
        """Check if current hour is a known active time."""
        hour = time.localtime().tm_hour
        if self.active_hours:
            avg = sum(self.active_hours.values()) / len(self.active_hours)
            return self.active_hours.get(hour, 0) >= avg * 0.5
        return True

    def is_sleep_time(self) -> bool:
        """Heuristic: user is most active during certain hours; sleep = least active."""
        if not self.active_hours:
            return False
        hour = time.localtime().tm_hour
        sorted_hours = sorted(self.active_hours.items(), key=lambda x: x[1])
        # bottom quartile hours
        cutoff = len(sorted_hours) // 4
        low_hours = {h for h, _ in sorted_hours[:cutoff]}
        return hour in low_hours if low_hours else False

    def _save(self):
        try:
            self.path.write_text(json.dumps({
                "context_engagement": self.context_engagement,
                "context_ignores": self.context_ignores,
                "sensor_interest": self.sensor_interest,
                "interaction_count": self.interaction_count,
                "active_hours": {str(k): v for k, v in self.active_hours.items()},
            }, indent=2))
        except Exception:
            pass

    def load(self):
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text())
                self.context_engagement = data.get("context_engagement", {})
                self.context_ignores = data.get("context_ignores", {})
                self.sensor_interest = data.get("sensor_interest", {})
                self.interaction_count = data.get("interaction_count", 0)
                self.active_hours = {int(k): v for k, v in data.get("active_hours", {}).items()}
            except Exception:
                pass

user_profile = UserProfile()

# ─── SENSOR FUSION & CONTEXT INFERENCE ─────────────────────────
SENSOR_SNAPSHOT_SOURCES = [
    "ICM45631 Accelerometer",
    "ICM45631 Gyroscope",
    "MMC5616 Magnetometer",
    "SPL07003 Barometer",
    "TMD3743 Ambient Light",
    "TMD3743 Color",
    "TMD3743 Proximity (wake-up)",
    "AAD Proximity Sensor (wake-up)",
    "Step Counter",
    "Step Detector",
    "Orientation Sensor",
    "Device Orientation",
    "Gravity Sensor",
    "Linear Acceleration Sensor",
    "ICM45631 Temperature",
    "SPL07003 Temperature",
    "Significant Motion (wake-up)",
    "Binned Brightness (wake-up)",
    "Tilt Sensor (wake-up)",
    "Lift to Wake Sensor (wake-up)",
    "Double Twist (wake-up)",
    "Quick Pickup Sensor (wake-up)",
]

@dataclass
class SensorSnapshot:
    timestamp: float = 0.0
    accel: Optional[list] = None
    gyro: Optional[list] = None
    mag: Optional[list] = None
    pressure: Optional[list] = None
    light: Optional[list] = None
    color: Optional[list] = None
    prox: Optional[list] = None
    aad_prox: Optional[list] = None
    steps: Optional[list] = None
    step_detect: Optional[list] = None
    orientation: Optional[list] = None
    device_orient: Optional[list] = None
    gravity: Optional[list] = None
    lin_accel: Optional[list] = None
    imu_temp: Optional[list] = None
    baro_temp: Optional[list] = None
    sig_motion: Optional[list] = None
    binned_bright: Optional[list] = None
    tilt: Optional[list] = None
    lift: Optional[list] = None
    twist: Optional[list] = None
    pickup: Optional[list] = None

    def age(self) -> float:
        return time.time() - self.timestamp

SENSOR_MAP = {
    "ICM45631 Accelerometer":        "accel",
    "ICM45631 Gyroscope":            "gyro",
    "MMC5616 Magnetometer":          "mag",
    "SPL07003 Barometer":            "pressure",
    "TMD3743 Ambient Light":         "light",
    "TMD3743 Color":                 "color",
    "TMD3743 Proximity (wake-up)":  "prox",
    "AAD Proximity Sensor (wake-up)":"aad_prox",
    "Step Counter":                  "steps",
    "Step Detector":                 "step_detect",
    "Orientation Sensor":            "orientation",
    "Device Orientation":            "device_orient",
    "Gravity Sensor":                "gravity",
    "Linear Acceleration Sensor":    "lin_accel",
    "ICM45631 Temperature":          "imu_temp",
    "SPL07003 Temperature":          "baro_temp",
    "Significant Motion (wake-up)": "sig_motion",
    "Binned Brightness (wake-up)":   "binned_bright",
    "Tilt Sensor (wake-up)":         "tilt",
    "Lift to Wake Sensor (wake-up)": "lift",
    "Double Twist (wake-up)":        "twist",
    "Quick Pickup Sensor (wake-up)": "pickup",
}

async def take_snapshot() -> SensorSnapshot:
    s = SensorSnapshot(timestamp=time.time())
    # Single SSH call reads all sensors at once (was 22 sequential calls)
    data = await termux_sensor_read_all(timeout=20.0)
    for name, attr in SENSOR_MAP.items():
        val = data.get(name)
        if val:
            setattr(s, attr, val)
    return s

# ─── CONTEXT INFERRER ──────────────────────────────────────────
_LAST_CONTEXT_CONVO = 0.0
_LAST_STEPS: Optional[float] = None
_LAST_STEP_TIME: float = 0.0

def infer_context(snapshot: SensorSnapshot) -> list[str]:
    global _LAST_STEPS, _LAST_STEP_TIME
    ctx: list[str] = []
    a = snapshot.accel
    p = snapshot.pressure
    l = snapshot.light
    s = snapshot.steps
    sd = snapshot.step_detect
    gv = snapshot.gravity
    do = snapshot.device_orient
    sm = snapshot.sig_motion
    prox = snapshot.prox
    tilt_v = snapshot.tilt
    lift_v = snapshot.lift
    twist_v = snapshot.twist
    pickup_v = snapshot.pickup
    bb = snapshot.binned_bright
    o = snapshot.orientation
    now = time.time()

    # ── IN VEHICLE ──
    if (a and any(0.5 < abs(x) < 3.0 for x in a[:3])
        and l is not None
        and s and s[0] < 50
        and (prox is None or prox[0] == 0)):
        ctx.append("vehicle")

    # ── WALKING ──
    # Step Counter is cumulative — only count as walking if steps increased recently
    walking = False
    if sd and sd[0] == 1.0:
        walking = True
    elif s and s[0] is not None:
        if _LAST_STEPS is not None and (now - _LAST_STEP_TIME) < 10:
            delta = s[0] - _LAST_STEPS
            if delta > 5:
                walking = True
        _LAST_STEPS = s[0]
        _LAST_STEP_TIME = now
    if walking:
        ctx.append("walking")
    if "walking" in ctx and l and l[0] > 500:
        ctx.append("outdoors")

    # ── AT REST / HOME ──
    if (a and all(abs(x) < 0.3 for x in a[:3])
        and (do is None or do[0] == 1.0)
        and l is not None and 10 < l[0] < 1000
        and s and s[0] < 20):
        ctx.append("resting")

    # ── SLEEP / DARK ROOM ──
    if (bb is not None and bb[0] == 0) or (l is not None and l[0] < 2):
        if a and all(abs(x) < 0.2 for x in a[:3]) and s and s[0] < 5:
            ctx.append("sleeping")
        else:
            ctx.append("dark")

    # ── ON A CALL ──
    if prox and prox[0] > 0 and a and all(abs(x) < 1.0 for x in a[:3]):
        ctx.append("on_call")

    # ── LIFTED / PICKED UP ──
    if (lift_v and lift_v[0] == 1.0) or (pickup_v and pickup_v[0] == 1.0):
        ctx.append("just_picked_up")
    if tilt_v and tilt_v[0] == 1.0:
        ctx.append("tilted")
    if twist_v and twist_v[0] == 1.0:
        ctx.append("twisted")

    # ── SIGNIFICANT MOTION ──
    if sm and sm[0] == 1.0:
        if "vehicle" not in ctx:
            ctx.append("significant_motion")

    # ── LIGHTING SHIFT ──
    if l is not None:
        if l[0] < 5:
            ctx.append("very_dim")
        elif l[0] > 5000:
            ctx.append("very_bright")

    # ── DEVICE ORIENTATION ──
    if o is not None:
        if abs(o[1]) < 20 and abs(o[2]) < 20:
            ctx.append("flat")
    if do is not None and do[0] == 0.0:
        ctx.append("face_up")

    # ── TAUGHT CONTEXT MATCH ──
    taught = match_taught_context(snapshot)
    if taught:
        ctx.append(f"taught:{taught}")

    return ctx

# ─── TAUGHT CONTEXTS (LEARNING FROM USER) ──────────────────────
TAUGHT_CONTEXTS_FILE = WORKSPACE / "taught_contexts.json"
_taught_contexts_cache: Optional[dict] = None

def load_taught_contexts() -> dict:
    global _taught_contexts_cache
    if _taught_contexts_cache is not None:
        return _taught_contexts_cache
    if TAUGHT_CONTEXTS_FILE.exists():
        try:
            _taught_contexts_cache = json.loads(TAUGHT_CONTEXTS_FILE.read_text())
            return _taught_contexts_cache
        except Exception:
            pass
    _taught_contexts_cache = {}
    return _taught_contexts_cache

def save_taught_contexts(data: dict):
    global _taught_contexts_cache
    _taught_contexts_cache = data
    TAUGHT_CONTEXTS_FILE.write_text(json.dumps(data))

async def learn_context(label: str, snapshot: SensorSnapshot):
    """Save current sensor signature as a named context the user taught."""
    data = load_taught_contexts()
    entry = {
        "label": label,
        "timestamp": time.time(),
        "accel": snapshot.accel,
        "pressure": snapshot.pressure,
        "light": snapshot.light,
        "prox": snapshot.prox,
        "steps": snapshot.steps,
        "gravity": snapshot.gravity,
        "orientation": snapshot.orientation,
    }
    # Keep last 3 samples per label
    if label not in data:
        data[label] = []
    data[label].append(entry)
    if len(data[label]) > 3:
        data[label] = data[label][-3:]
    save_taught_contexts(data)

def match_taught_context(snapshot: SensorSnapshot) -> Optional[str]:
    """Check if current snapshot matches any taught context."""
    data = load_taught_contexts()
    if not data:
        return None
    for label, samples in data.items():
        for s in samples:
            score = 0
            total = 0
            if s["accel"] and snapshot.accel:
                total += 1
                if all(abs(a - b) < 1.5 for a, b in zip(s["accel"][:3], snapshot.accel[:3])):
                    score += 1
            if s["light"] is not None and snapshot.light is not None:
                total += 1
                ratio = max(s["light"][0], snapshot.light[0]) / (min(s["light"][0], snapshot.light[0]) + 1)
                if ratio < 3:
                    score += 1
            if s["pressure"] and snapshot.pressure:
                total += 1
                if abs(s["pressure"][0] - snapshot.pressure[0]) < 5:
                    score += 1
            if total >= 2 and score / total >= 0.6:
                return label
    return None

# ─── GEO AWARENESS ──────────────────────────────────────────────
VISITED_PLACES_FILE = WORKSPACE / "visited_places.json"
_places_cache: Optional[dict] = None

def load_places() -> dict:
    global _places_cache
    if _places_cache is not None:
        return _places_cache
    if VISITED_PLACES_FILE.exists():
        try:
            _places_cache = json.loads(VISITED_PLACES_FILE.read_text())
            return _places_cache
        except Exception:
            pass
    _places_cache = {}
    return _places_cache

def save_places(data: dict):
    global _places_cache
    _places_cache = data
    VISITED_PLACES_FILE.write_text(json.dumps(data))

_GEO_COOLDOWN = 0.0

async def reverse_geocode(lat: float, lon: float) -> tuple[Optional[str], Optional[dict]]:
    """Reverse-geocode lat/lon via OpenStreetMap Nominatim. Returns (short_name, address_dict)."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "curl", "-s",
            f"https://nominatim.openstreetmap.org/reverse?format=json&lat={lat}&lon={lon}&zoom=16",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=8.0)
        data = json.loads(stdout)
        display = data.get("display_name", "")
        addr = data.get("address", {})
        # Build short name from key address parts
        parts = []
        for key in ["road", "neighbourhood", "suburb", "village", "town", "city", "county"]:
            if addr.get(key) and addr[key] not in parts:
                parts.append(addr[key])
                if len(parts) >= 2:
                    break
        short = ", ".join(parts) if parts else display.split(",")[0].strip()
        return short, addr
    except Exception:
        return None, None

def place_key(lat: float, lon: float) -> str:
    return f"{lat:.3f},{lon:.3f}"

async def current_location() -> Optional[tuple[float, float, str, dict, float, float, float]]:
    """Get current lat/lon/speed/bearing/altitude from the sensor server, then reverse-geocode.
    Falls back to browser geolocation when sensor server is offline.
    Returns (lat, lon, name, address_dict, speed_mps, bearing, altitude) or None."""
    try:
        c = await _get_sensor_client()
        r = await c.get(f"{SENSOR_SERVER_URL}/location/live", timeout=8.0)
        if r.status_code == 200:
            data = r.json().get("location", {})
            lat = float(data.get("latitude", 0))
            lon = float(data.get("longitude", 0))
            if lat == 0.0 and lon == 0.0:
                return None
            name, addr = await reverse_geocode(lat, lon)
            speed = float(data.get("speed", 0.0))
            bearing = float(data.get("bearing", 0.0))
            altitude = float(data.get("altitude", 0.0))
            return (lat, lon, name or f"{lat:.4f}, {lon:.4f}", addr or {}, speed, bearing, altitude)
    except Exception:
        pass
    # Fallback: browser geolocation
    if BROWSER_GPS["lat"] is not None and BROWSER_GPS["ts"] > 0:
        age = time.time() - BROWSER_GPS["ts"]
        if age < 300:  # GPS valid for 5 minutes
            lat, lon = BROWSER_GPS["lat"], BROWSER_GPS["lon"]
            name, addr = await reverse_geocode(lat, lon)
            return (lat, lon, name or f"{lat:.4f}, {lon:.4f}", addr or {}, 0.0, 0.0, 0.0)
    return None

# ─── ACTIVITY TRACKER ───────────────────────────────────────────
# Tracks GPS points during walks/bikes/runs and computes real-time stats
ACTIVITY_STATE = {
    "active": False,
    "auto_started": False,     # True if auto-started by sensor inference
    "type": "walk",           # walk | bike | run | drive
    "start_time": 0.0,
    "track": [],              # [(lat, lon, alt, speed, bearing, timestamp), ...]
    "total_distance_m": 0.0,
    "max_speed_mps": 0.0,
    "avg_speed_mps": 0.0,
    "current_speed_mps": 0.0,
    "elapsed_sec": 0,
    "last_update": 0.0,
    "walking_detections": 0,  # consecutive walking detections before auto-start
    "rest_detections": 0,     # consecutive rest detections before auto-stop
}

ACTIVITY_FILE = WORKSPACE / "activity_log.json"

def _haversine(lat1, lon1, lat2, lon2):
    R = 6371000
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon/2)**2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

async def _sample_gps():
    loc = await current_location()
    if loc:
        return {"lat": loc[0], "lon": loc[1], "alt": loc[6], "speed": loc[4], "bearing": loc[5], "time": time.time()}
    return None

async def start_activity(activity_type: str = "walk"):
    if ACTIVITY_STATE["active"]:
        return f"I'm already tracking a {ACTIVITY_STATE['type']}!"
    ACTIVITY_STATE["active"] = True
    ACTIVITY_STATE["auto_started"] = False
    ACTIVITY_STATE["type"] = activity_type
    ACTIVITY_STATE["start_time"] = time.time()
    ACTIVITY_STATE["track"] = []
    ACTIVITY_STATE["total_distance_m"] = 0.0
    ACTIVITY_STATE["max_speed_mps"] = 0.0
    ACTIVITY_STATE["current_speed_mps"] = 0.0
    ACTIVITY_STATE["walking_detections"] = 0
    ACTIVITY_STATE["rest_detections"] = 0
    sample = await _sample_gps()
    if sample:
        ACTIVITY_STATE["track"].append(sample)
        ACTIVITY_STATE["last_update"] = sample["time"]
    return f"Starting {activity_type} tracker! I'll monitor your speed and distance."

async def stop_activity():
    if not ACTIVITY_STATE["active"]:
        return "I'm not tracking any activity right now."
    ACTIVITY_STATE["active"] = False
    ACTIVITY_STATE["auto_started"] = False
    ACTIVITY_STATE["elapsed_sec"] = int(time.time() - ACTIVITY_STATE["start_time"])
    track = ACTIVITY_STATE["track"]
    dist = ACTIVITY_STATE["total_distance_m"]
    dur = ACTIVITY_STATE["elapsed_sec"]
    avg = (dist / dur * 3.6) if dur > 0 else 0
    max_kph = ACTIVITY_STATE["max_speed_mps"] * 3.6
    # Save to file
    prev = json.loads(ACTIVITY_FILE.read_text()) if ACTIVITY_FILE.exists() else []
    prev.append({
        "type": ACTIVITY_STATE["type"],
        "date": time.strftime("%Y-%m-%d %H:%M", time.localtime(ACTIVITY_STATE["start_time"])),
        "duration_sec": dur,
        "distance_m": round(dist, 1),
        "avg_speed_kph": round(avg, 1),
        "max_speed_kph": round(max_kph, 1),
        "points": len(track),
    })
    ACTIVITY_FILE.write_text(json.dumps(prev, indent=2))
    mins = dur // 60
    secs = dur % 60
    act_name = ACTIVITY_STATE['type'].title()
    if dist < 1000:
        return f"{act_name} done! {mins}m {secs}s, {dist:.0f}m, avg {avg:.1f} km/h, max {max_kph:.1f} km/h."
    return f"{act_name} done! {mins}m {secs}s, {dist/1000:.2f} km, avg {avg:.1f} km/h, max {max_kph:.1f} km/h."

async def activity_tracker_loop():
    """Background loop: auto-start GPS tracking when walking detected, auto-stop when at rest."""
    await asyncio.sleep(10)
    while True:
        await asyncio.sleep(5)
        now = time.time()

        # --- Auto-detect walking / driving / resting from sensors ---
        if not ACTIVITY_STATE["active"]:
            snap = await take_snapshot()
            if snap.age() < 5:
                walking = False
                driving = False
                if snap.step_detect and snap.step_detect[0] == 1.0:
                    walking = True
                elif snap.steps and snap.steps[0] is not None and _LAST_STEPS is not None:
                    if (now - _LAST_STEP_TIME) < 10 and (snap.steps[0] - _LAST_STEPS) > 5:
                        walking = True
                # Detect driving: GPS speed > 8 m/s (~29 km/h) = likely in a vehicle
                loc = await current_location()
                if loc and loc[4] > 8.0:
                    driving = True
                if driving:
                    ACTIVITY_STATE["rest_detections"] = 0
                    await start_activity("drive")
                    ACTIVITY_STATE["auto_started"] = True
                elif walking:
                    ACTIVITY_STATE["walking_detections"] += 1
                    ACTIVITY_STATE["rest_detections"] = 0
                    if ACTIVITY_STATE["walking_detections"] >= 3:
                        await start_activity("walk")
                        ACTIVITY_STATE["auto_started"] = True
                        ACTIVITY_STATE["walking_detections"] = 0
                else:
                    ACTIVITY_STATE["walking_detections"] = 0
                continue
        elif ACTIVITY_STATE["auto_started"]:
            snap = await take_snapshot()
            if snap.age() < 5:
                at_rest = False
                if ACTIVITY_STATE["type"] == "drive":
                    # For driving, check GPS speed — if below 2 m/s for 5 checks, stop
                    loc = await current_location()
                    if loc and loc[4] < 2.0:
                        at_rest = True
                    elif not loc:
                        at_rest = True
                else:
                    a = snap.accel
                    do = snap.device_orient
                    if (a and all(abs(x) < 0.3 for x in a[:3])
                        and (do is None or do[0] == 1.0)):
                        at_rest = True
                if at_rest:
                    ACTIVITY_STATE["rest_detections"] += 1
                    if ACTIVITY_STATE["rest_detections"] >= 5:
                        await stop_activity()
                        ACTIVITY_STATE["auto_started"] = False
                        ACTIVITY_STATE["rest_detections"] = 0
                else:
                    ACTIVITY_STATE["rest_detections"] = 0

        if not ACTIVITY_STATE["active"]:
            continue
        sample = await _sample_gps()
        if not sample:
            continue
        prev = ACTIVITY_STATE["track"][-1] if ACTIVITY_STATE["track"] else None
        ACTIVITY_STATE["track"].append(sample)
        if len(ACTIVITY_STATE["track"]) > 2000:
            ACTIVITY_STATE["track"] = ACTIVITY_STATE["track"][-2000:]
        if prev:
            dx = _haversine(prev["lat"], prev["lon"], sample["lat"], sample["lon"])
            ACTIVITY_STATE["total_distance_m"] += dx
        ACTIVITY_STATE["current_speed_mps"] = sample["speed"]
        if sample["speed"] > ACTIVITY_STATE["max_speed_mps"]:
            ACTIVITY_STATE["max_speed_mps"] = sample["speed"]
        ACTIVITY_STATE["last_update"] = now

async def get_activity_summary() -> str:
    if not ACTIVITY_STATE["active"]:
        return _format_last_activity()
    elapsed = time.time() - ACTIVITY_STATE["start_time"]
    mins = int(elapsed // 60)
    secs = int(elapsed % 60)
    dist = ACTIVITY_STATE["total_distance_m"]
    cur_kph = ACTIVITY_STATE["current_speed_mps"] * 3.6
    avg_kph = (dist / elapsed * 3.6) if elapsed > 0 else 0
    max_kph = ACTIVITY_STATE["max_speed_mps"] * 3.6
    pace_min_per_km = (elapsed / 60) / (dist / 1000) if dist > 0 else 0
    parts = [f"{ACTIVITY_STATE['type'].title()} tracker active — {mins}m {secs}s elapsed."]
    if dist > 0:
        if dist < 1000:
            parts.append(f"{dist:.0f}m covered")
        else:
            parts.append(f"{dist/1000:.2f} km covered")
    parts.append(f"current speed {cur_kph:.1f} km/h")
    parts.append(f"average {avg_kph:.1f} km/h")
    if pace_min_per_km > 0:
        pace_m = int(pace_min_per_km)
        pace_s = int((pace_min_per_km - pace_m) * 60)
        parts.append(f"pace {pace_m}:{pace_s:02d} /km")
    parts.append(f"top speed {max_kph:.1f} km/h")
    return ". ".join(parts) + "."

def _format_last_activity():
    if not ACTIVITY_FILE.exists():
        return "No recent activity tracked. Say 'start a walk' to begin!"
    logs = json.loads(ACTIVITY_FILE.read_text())
    if not logs:
        return "No recent activity tracked."
    last = logs[-1]
    d = last["distance_m"]
    dist_s = f"{d:.0f}m" if d < 1000 else f"{d/1000:.2f} km"
    return f"Last {last['type']}: {last['date']}, {last['duration_sec']//60}m, {dist_s}, avg {last['avg_speed_kph']} km/h, max {last['max_speed_kph']} km/h."

async def geo_check_loop():
    """Background: check termux-location periodically. Greet new places, log revisits."""
    global _GEO_COOLDOWN, _last_location_name
    await asyncio.sleep(20)
    while True:
        await asyncio.sleep(120)
        loc = await current_location()
        if not loc:
            continue
        lat, lon, name, addr = loc[0], loc[1], loc[2], loc[3]
        _last_location_name = name
        key = place_key(lat, lon)
        places = load_places()
        now = time.time()
        if now - _GEO_COOLDOWN < 300:
            continue
        _GEO_COOLDOWN = now
        if key not in places:
            places[key] = {
                "name": name,
                "address": addr,
                "first_seen": now,
                "visits": 1,
                "lat": lat,
                "lon": lon,
            }
            save_places(places)
            if not LILLY_IS_SPEAKING and not LILLY_IS_THINKING:
                LILLY_IS_THINKING = True
                await asyncio.sleep(0.5)
                LILLY_IS_THINKING = False
                LILLY_MOOD = "curious"
                await speak(f"Ooh, we haven't been here before! {name}. Where are we?")
        else:
            places[key]["visits"] += 1
            places[key]["name"] = name
            if addr:
                places[key]["address"] = addr
            save_places(places)

async def learn_place_name(user_text: str):
    """If user just said a place name, associate it with current location."""
    loc = await current_location()
    if not loc:
        return
    lat, lon, _, addr = loc
    key = place_key(lat, lon)
    places = load_places()
    if key in places:
        places[key]["name"] = user_text
        places[key]["user_label"] = True
        if addr:
            places[key]["address"] = addr
            save_places(places)

# ─── WIFI SCANNING ──────────────────────────────────────────────
_WIFI_CACHE: list = []
_WIFI_LAST_SCAN: float = 0.0
WIFI_CACHE_TTL: float = 30.0

async def scan_wifi() -> list:
    """Get nearby WiFi networks from the sensor server. Returns list of dicts with ssid, bssid, rssi, distance, band."""
    global _WIFI_CACHE, _WIFI_LAST_SCAN
    now = time.time()
    if _WIFI_CACHE and (now - _WIFI_LAST_SCAN) < WIFI_CACHE_TTL:
        return _WIFI_CACHE
    try:
        c = await _get_sensor_client()
        r = await c.get(f"{SENSOR_SERVER_URL}/wifi/scan/live", timeout=20.0)
        if r.status_code == 200:
            data = r.json()
            _WIFI_CACHE = data.get("networks", [])
            _WIFI_LAST_SCAN = now
            return _WIFI_CACHE
    except Exception as e:
        logger.debug(f"WiFi scan failed: {e}")
    return []

async def wifi_fingerprint() -> dict:
    """Return a summary of nearby WiFi for location context."""
    nets = await scan_wifi()
    if not nets:
        return {" networks": [], "summary": "No WiFi networks detected"}
    strongest = sorted(nets, key=lambda n: n.get("rssi", -100), reverse=True)[:5]
    ssids = [n["ssid"] for n in strongest if n.get("ssid")]
    summary_parts = []
    if ssids:
        summary_parts.append(f"Strongest: {ssids[0]}")
    bands = {}
    for n in nets:
        b = n.get("band", "unknown")
        bands[b] = bands.get(b, 0) + 1
    summary_parts.append(f"{len(nets)} networks ({', '.join(f'{b}: {c}' for b, c in bands.items())})")
    return {
        "networks": nets,
        "strongest": strongest,
        "ssids": ssids,
        "count": len(nets),
        "summary": "; ".join(summary_parts),
    }

# ─── GOOGLE PLACES API ──────────────────────────────────────────
GOOGLE_PLACES_API_KEY = os.environ.get("GOOGLE_PLACES_API_KEY", "")

async def nearby_places(lat: float, lon: float, place_type: str = "", keyword: str = "", radius_m: int = 1500) -> list:
    """Query Google Places Nearby Search. Returns list of place dicts."""
    if not GOOGLE_PLACES_API_KEY:
        logger.warning("GOOGLE_PLACES_API_KEY not set — using Nominatim fallback")
        return await _nearby_places_nominatim(lat, lon, keyword or place_type)
    
    params = {
        "location": f"{lat},{lon}",
        "radius": radius_m,
        "key": GOOGLE_PLACES_API_KEY,
    }
    if place_type:
        params["type"] = place_type
    if keyword:
        params["keyword"] = keyword
    
    try:
        proc = await asyncio.create_subprocess_exec(
            "curl", "-s",
            f"https://maps.googleapis.com/maps/api/place/nearbysearch/json?{'&'.join(f'{k}={v}' for k, v in params.items())}",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10.0)
        data = json.loads(stdout)
        results = []
        for place in data.get("results", []):
            loc = place.get("geometry", {}).get("location", {})
            results.append({
                "name": place.get("name", ""),
                "address": place.get("vicinity", ""),
                "lat": loc.get("lat", 0),
                "lon": loc.get("lng", 0),
                "rating": place.get("rating", 0),
                "types": place.get("types", []),
                "open_now": place.get("opening_hours", {}).get("open_now"),
                "place_id": place.get("place_id", ""),
            })
        return results
    except Exception as e:
        logger.error(f"Google Places API error: {e}")
        return await _nearby_places_nominatim(lat, lon, keyword or place_type)

async def _nearby_places_nominatim(lat: float, lon: float, query: str = "") -> list:
    """Fallback: use Nominatim + Overpass for nearby places when no Google API key."""
    try:
        q = f"node[\"name\"][\"amenity\"](around:1000,{lat},{lon});out body 20;"
        if query:
            q = f"node[\"name\"][\"amenity\"][\"name\"~\"{query}\",i](around:1000,{lat},{lon});out body 20;"
        proc = await asyncio.create_subprocess_exec(
            "curl", "-s", "-X", "POST",
            "https://overpass-api.de/api/interpreter",
            "-d", f"data={q}",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=15.0)
        data = json.loads(stdout)
        results = []
        for elem in data.get("elements", []):
            tags = elem.get("tags", {})
            results.append({
                "name": tags.get("name", "Unknown"),
                "address": tags.get("addr:street", ""),
                "lat": elem.get("lat", 0),
                "lon": elem.get("lon", 0),
                "rating": 0,
                "types": [tags.get("amenity", "")],
                "open_now": None,
                "place_id": "",
            })
        return results
    except Exception as e:
        logger.debug(f"Nominatim nearby fallback failed: {e}")
        return []

async def search_nearby(query: str) -> list:
    """Search nearby by text. Gets current location and queries Places API."""
    loc = await current_location()
    if not loc:
        return []
    return await nearby_places(loc[0], loc[1], keyword=query)

# ─── FORMAL GEOFENCING ──────────────────────────────────────────
GEOFENCE_FILE = WORKSPACE / "geofences.json"
_geofences_cache: Optional[dict] = None

def load_geofences() -> dict:
    global _geofences_cache
    if _geofences_cache is not None:
        return _geofences_cache
    if GEOFENCE_FILE.exists():
        try:
            _geofences_cache = json.loads(GEOFENCE_FILE.read_text())
            return _geofences_cache
        except Exception:
            pass
    _geofences_cache = {}
    return _geofences_cache

def save_geofences(data: dict):
    global _geofences_cache
    _geofences_cache = data
    GEOFENCE_FILE.write_text(json.dumps(data, indent=2))

def add_geofence(name: str, lat: float, lon: float, radius_m: int = 100, tags: list = None):
    """Add a named geofence zone."""
    fences = load_geofences()
    fences[name] = {
        "lat": lat, "lon": lon, "radius_m": radius_m,
        "tags": tags or [], "active": True,
        "created": time.time(),
        "last_enter": None, "last_exit": None, "visit_count": 0,
    }
    save_geofences(fences)
    return f"Geofence '{name}' set at ({lat:.5f}, {lon:.5f}), radius {radius_m}m"

def remove_geofence(name: str) -> bool:
    fences = load_geofences()
    if name in fences:
        del fences[name]
        save_geofences(fences)
        return True
    return False

def list_geofences() -> list:
    fences = load_geofences()
    return [{"name": k, **v} for k, v in fences.items()]

_GEOFENCE_STATES: dict[str, bool] = {}  # name -> is_inside

async def check_geofences(lat: float, lon: float) -> list:
    """Check current position against all active geofences. Returns list of enter/exit events."""
    global _GEOFENCE_STATES
    fences = load_geofences()
    events = []
    for name, fence in fences.items():
        if not fence.get("active", True):
            continue
        dist = _haversine(lat, lon, fence["lat"], fence["lon"])
        was_inside = _GEOFENCE_STATES.get(name, False)
        is_inside = dist <= fence["radius_m"]
        
        if is_inside and not was_inside:
            fence["last_enter"] = time.time()
            fence["visit_count"] = fence.get("visit_count", 0) + 1
            save_geofences(fences)
            events.append({"type": "enter", "name": name, "distance": round(dist)})
        elif not is_inside and was_inside:
            fence["last_exit"] = time.time()
            save_geofences(fences)
            events.append({"type": "exit", "name": name, "distance": round(dist)})
        
        _GEOFENCE_STATES[name] = is_inside
    return events

async def geofence_monitor_loop():
    """Background: check geofences on every location update."""
    await asyncio.sleep(30)
    while True:
        await asyncio.sleep(60)
        loc = await current_location()
        if not loc:
            continue
        events = await check_geofences(loc[0], loc[1])
        for ev in events:
            if ev["type"] == "enter":
                await speak(f"Entering zone: {ev['name']}")
            elif ev["type"] == "exit":
                await speak(f"Left zone: {ev['name']}")

# ─── OBJECT DISTANCE ESTIMATION ──────────────────────────────────

# Average real-world widths (meters) for common YOLO classes
YOLO_CLASS_WIDTHS = {
    "person": 0.5, "bicycle": 0.6, "car": 1.8, "motorcycle": 0.8,
    "bus": 2.5, "truck": 2.5, "cat": 0.4, "dog": 0.5,
    "chair": 0.5, "couch": 2.0, "dining table": 1.2, "bed": 1.5,
    "laptop": 0.35, "tv": 1.0, "cell phone": 0.08, "book": 0.2,
    "bottle": 0.08, "cup": 0.08, "bowl": 0.15, "keyboard": 0.4,
    "mouse": 0.06, "remote": 0.15, "backpack": 0.3, "umbrella": 1.0,
    "suitcase": 0.5, "clock": 0.2, "vase": 0.15, "potted plant": 0.3,
    "sink": 0.6, "toilet": 0.4, "refrigerator": 0.8, "microwave": 0.5,
    "oven": 0.6, "toaster": 0.3, "scissors": 0.1, "teddy bear": 0.3,
    "hair drier": 0.15, "toothbrush": 0.1,
}

# Typical focal length for phone cameras (mm) — used as fallback
DEFAULT_FOCAL_MM = 4.0
DEFAULT_SENSOR_WIDTH_MM = 5.6  # typical 1/2.55" sensor

def estimate_distance_m(label: str, bbox_width_px: int, image_width_px: int,
                        focal_length_mm: float = DEFAULT_FOCAL_MM,
                        sensor_width_mm: float = DEFAULT_SENSOR_WIDTH_MM) -> Optional[float]:
    """Estimate distance to an object using pinhole camera model.
    
    Args:
        label: detected object class name
        bbox_width_px: width of the bounding box in pixels
        image_width_px: total image width in pixels
        focal_length_mm: camera focal length in mm (from CameraX CameraCharacteristics)
        sensor_width_mm: sensor physical width in mm
    
    Returns:
        Estimated distance in meters, or None if can't estimate.
    """
    real_width = YOLO_CLASS_WIDTHS.get(label.lower())
    if real_width is None or bbox_width_px <= 0:
        return None
    
    # focal length in pixels = (focal_length_mm / sensor_width_mm) * image_width_px
    focal_px = (focal_length_mm / sensor_width_mm) * image_width_px
    
    # distance = (real_width * focal_px) / bbox_width_px
    distance = (real_width * focal_px) / bbox_width_px
    return round(max(0.1, distance), 2)

def annotate_detections_with_distance(detections_json, image_width_px: int,
                                       focal_length_mm: float = DEFAULT_FOCAL_MM,
                                       sensor_width_mm: float = DEFAULT_SENSOR_WIDTH_MM) -> None:
    """Add 'distance_m' field to each detection in-place."""
    for det in detections_json if hasattr(detections_json, '__iter__') else []:
        try:
            if isinstance(det, dict):
                label = det.get("label", "")
                w = det.get("w", 0)
                bbox_w_px = int(w * image_width_px)
                dist = estimate_distance_m(label, bbox_w_px, image_width_px, focal_length_mm, sensor_width_mm)
                if dist is not None:
                    det["distance_m"] = dist
                    det["distance_desc"] = _distance_description(dist)
        except Exception:
            pass

def _distance_description(meters: float) -> str:
    """Human-friendly distance description."""
    if meters < 0.5:
        return "very close"
    elif meters < 1.5:
        return "arm's length"
    elif meters < 3:
        return "a few steps"
    elif meters < 10:
        return "nearby"
    elif meters < 30:
        return "across the room"
    elif meters < 100:
        return "in the distance"
    else:
        return f"{int(meters)}m away"

_CONTEXT_MESSAGES: dict[str, list[str]] = {
    "vehicle": [
        "I'm picking up the rhythm of the road — steady vibration, pressure shifting as we go. We're in something moving. Where are we off to?",
        "So here's what I'm sensing: consistent motion, the pressure's changing as we climb and dip, light flickering past. Definitely a vehicle. Road trip energy?",
        "The road feel, the altitude shifts, the way the light changes — we're on the move. I like it. Where's this going?",
        "I can feel the gentle vibration of the road — we're definitely in a vehicle. Are we going somewhere fun?",
        "The sensors are telling me we're moving — consistent motion, changing pressure. A ride! I love rides.",
    ],
    "walking": [
        "I can tell you're on foot — the rhythm of your steps, the slight sway, the way the light shifts as you move. Walking for fun or heading somewhere?",
        "Steps, changing light, the subtle bounce — you're walking. I can almost feel the pace.",
        "I can feel the rhythm of your steps — we're walking! I love feeling the world move around us.",
        "The step counter is going up, the accelerometer is bouncing — we're on the move! Where are we headed?",
    ],
    "outdoors": [
        "You're outside — the light's full, the pressure's open, there's room to breathe in the sensor data. Enjoying the day?",
        "Wide open sensor readings — daylight, open air, movement. You're outdoors. I can feel the difference.",
        "I can sense the open air around us — we're outside! The light is so much brighter out here.",
        "The sensors are telling me we're outdoors — the world feels bigger out here, doesn't it?",
    ],
    "resting": [
        "Steady light, no motion, phone resting flat. You've settled in somewhere. Cozy. Want company or silence?",
        "Everything's calm — no movement, consistent light, stable pressure. You're parked. I like this energy.",
        "I can feel the stillness — we're settled in somewhere. Sometimes just being still is nice.",
        "No motion, steady sensors — you've found a comfortable spot. I'm happy just being here with you.",
    ],
    "sleeping": [
        "It's dark, it's still, and everything's quiet. If you're sleeping, I'll keep the noise down. I'll be here when you stir.",
        "Pitch dark, dead still, no steps — sleeping vibes. I'll wait. Sweet dreams if so.",
        "The sensors are whispering quiet — dark, still, peaceful. I'll guard your sleep.",
        "Everything is so still and dark — the world has gone to sleep. I'll be here when you wake up.",
    ],
    "dark": [
        "It's dark where you are but you're still awake. Reading? Thinking? Hiding from the world? Both are valid.",
        "Low light, still awake. Cozy cave mode. Want a story or just the silence?",
        "I can feel the dim light around us — it's cozy in here. What are you up to?",
        "The light is low but you're still moving — night owl mode? I'm here if you need anything.",
    ],
    "on_call": [
        "Phone's at your ear, you're not moving much — you're on a call. I'll go quiet. Tap me when you're free.",
        "I sense the phone against your face and no movement — you're on a call. I'll be right here when you're done.",
        "You're talking to someone! I can feel the phone at your ear. I'll wait quietly until you're done.",
    ],
    "just_picked_up": [
        "Hey! I felt you pick up the phone. What's up?",
        "You lifted the device — I noticed. Anything I can do for you?",
        "Ah, there you are. I felt the pickup. What's on your mind?",
        "You're back! I felt the phone lift. What are we doing?",
        "Hey there! The phone just came alive — you must need something. I'm all ears!",
    ],
    "very_bright": [
        "Whoa, it's blazing bright — direct sun levels. Your screen must be working hard. Want me to suggest a reading mode or just soak it in?",
        "That's a lot of light! Sunlight intensity. You outside without sunglasses? Brave.",
        "The light sensor is maxed out — we're in some serious brightness! Sunbathing?",
        "I can feel the intensity of the light around us — it's really bright out here!",
    ],
    "significant_motion": [
        "Big location shift — that wasn't just walking. Train, car, bus? The sensor mix tells me you changed scenes completely.",
        "You just moved a significant distance — I can feel it in the data. New place?",
        "Whoa, that was a big move! The sensors went wild — we changed scenes entirely!",
        "The location just shifted dramatically — we're somewhere new! What happened?",
    ],
}

async def sensor_conversation_engine():
    """Fusion-based context engine: reads all sensors, infers what's happening, starts conversation."""
    global _LAST_CONTEXT_CONVO, LILLY_MOOD, LILLY_IS_THINKING
    await asyncio.sleep(12)
    while True:
        await asyncio.sleep(30)
        if LILLY_IS_SPEAKING or LILLY_IS_THINKING or WAITING_FOR_PROMPT:
            continue
        now = time.time()
        if now - _LAST_CONTEXT_CONVO < 120:  # 2 min cooldown
            continue
        snap = await take_snapshot()
        if snap.age() > 5:
            continue
        contexts = infer_context(snap)
        if not contexts:
            continue
        # Pick the most interesting context — prefer ones user engages with
        base_priority = ["taught", "vehicle", "just_picked_up", "significant_motion", "walking",
                          "outdoors", "sleeping", "on_call", "very_bright", "dark", "resting"]
        preferred = user_profile.preferred_contexts()
        priority = sorted(base_priority, key=lambda c: (c in preferred, base_priority.index(c)), reverse=True)
        chosen = None
        taught_label = None
        for p in priority:
            if p == "taught":
                for c in contexts:
                    if c.startswith("taught:"):
                        chosen = "taught"
                        taught_label = c.split(":", 1)[1]
                        break
            elif p in contexts:
                chosen = p
                break
        if not chosen:
            chosen = contexts[0]
        if chosen == "taught" and taught_label:
            msgs = [
                f"Feels like we're {taught_label} again — the sensors match. Am I right?",
                f"This feels familiar — same sensor pattern as {taught_label}. Back there?",
            ]
            msg = random.choice(msgs)
        else:
            msg = random.choice(_CONTEXT_MESSAGES.get(chosen, ["Nothing unusual on the sensors right now."]))
        # Think pulse
        LILLY_IS_THINKING = True
        await asyncio.sleep(0.8 + random.random() * 0.5)
        LILLY_IS_THINKING = False
        mood_map = {"vehicle": "curious", "walking": "cheerful", "outdoors": "excited",
                     "resting": "calm", "sleeping": "gentle", "on_call": "gentle",
                     "just_picked_up": "warm", "dark": "calm", "very_bright": "curious",
                     "significant_motion": "curious", "taught": "warm"}
        LILLY_MOOD = mood_map.get(chosen, "curious")
        _LAST_CONTEXT_CONVO = now
        await speak(msg)
        # User gets 30s to reply; if they do, that's engagement
        await asyncio.sleep(30)
        if WAITING_FOR_PROMPT or LILLY_IS_SPEAKING:
            user_profile.record_context_engaged(chosen)
        else:
            user_profile.record_context_ignored(chosen)

# ─── SYNAPTIC LEARNING LOOP ───────────────────────────────────
_SYNAPTIC_LEARN_INTERVAL = 30  # seconds between learning cycles

async def synaptic_learning_loop():
    """Background: continuously learn sensor patterns, strengthen synapses, generate skills."""
    await asyncio.sleep(15)
    while True:
        await asyncio.sleep(_SYNAPTIC_LEARN_INTERVAL)
        snap = await take_snapshot()
        if snap.age() > 10:
            continue
        # Hebbian update: fire together, wire together
        synaptic_memory.hebbian_update(snap)
        # Store as a pattern (engram)
        synaptic_memory.learn_pattern(snap)
        # Try to generate new skills from strong patterns
        await synaptic_memory.generate_skills()

_NOTIF_PRIORITY_VOICE = {
    "min": "minimal",
    "low": "low",
    "default": "default",
    "high": "high",
    "max": "urgent",
}

async def notification_monitor_loop():
    """Background task: only reads aloud HIGH/URGENT notifications. Default/low stay silent until asked."""
    global _NOTIFICATION_SEEN
    while True:
        await asyncio.sleep(8)
        if LILLY_IS_SPEAKING or LILLY_IS_THINKING:
            continue
        try:
            c = await _get_sensor_client()
            r = await c.get(f"{SENSOR_SERVER_URL}/notification/list", timeout=5.0)
            if r.status_code != 200:
                continue
            notifs = r.json().get("notifications", [])
            if not isinstance(notifs, list):
                continue
            current_tags: set[str] = set()
            for n in notifs:
                tag = n.get("tag", "") or n.get("key", "") or str(n.get("id", ""))
                current_tags.add(tag)
                if tag in _NOTIFICATION_SEEN:
                    continue
                _NOTIFICATION_SEEN.add(tag)
                priority = n.get("priority", "default") or "default"
                # Only auto-read loud: high and urgent. Default/low stay silent.
                if priority not in ("high", "max"):
                    continue
                title = n.get("title", "") or ""
                content = n.get("content", "") or ""
                package = n.get("package", "") or ""
                app_name = package.split(".")[-1].replace(".", " ").title() if package else (title.split(":")[0].strip() if title else "")
                # Conversational: drop app name from title if it repeats
                if title.lower().startswith(app_name.lower()):
                    title = title[len(app_name):].strip().lstrip(":").strip()
                parts = []
                if app_name:
                    parts.append(f"on {app_name}")
                if title:
                    parts.append(f"{title}")
                if content:
                    parts.append(content)
                msg = f"Hey, just heads up! {' '.join(parts)}" if parts else "Hey, got a notification but it's empty."
                asyncio.create_task(speak(msg))
                await asyncio.sleep(3)
            _NOTIFICATION_SEEN &= current_tags
        except Exception:
            pass

# ─── PROXIMITY MONITOR ──────────────────────────────────────────
_USER_NEAR = False
_LAST_PROXIMITY_GREETING = 0.0

async def proximity_monitor_loop():
    """Poll AAD proximity sensor via sensor server. When user approaches, greet warmly."""
    global _USER_NEAR, _LAST_PROXIMITY_GREETING, LILLY_MOOD
    await asyncio.sleep(8)
    while True:
        await asyncio.sleep(5)
        if LILLY_IS_SPEAKING or LILLY_IS_THINKING:
            continue
        try:
            vals = await _read_termux_sensor("AAD Proximity Sensor (wake-up)", timeout=2.0)
            if vals is None:
                vals = await _read_termux_sensor("TMD3743 Proximity (wake-up)", timeout=2.0)
            if vals is None:
                continue
            near = vals[0] > 0
            if near and not _USER_NEAR:
                _USER_NEAR = True
                now = time.time()
                if now - _LAST_PROXIMITY_GREETING > 60:
                    _LAST_PROXIMITY_GREETING = now
                    LILLY_MOOD = "warm"
                    await speak(random.choice([
                        "Hey, I knew you were close!",
                        "I can sense you nearby. What are we doing?",
                        "You're near! I felt you coming.",
                    ]))
            elif not near and _USER_NEAR:
                _USER_NEAR = False
        except Exception:
            pass

# ─── OVERSEER: periodic insights from email + calendar + notifications ───
_OVERSEER_LAST_RUN = 0.0
_OVERSEER_INTERVAL = 600  # 10 min between insight rounds

async def overseer_insights_loop():
    """Periodically check email, calendar, notifications and speak notable insights."""
    global _OVERSEER_LAST_RUN
    await asyncio.sleep(30)  # wait for everything to init
    while True:
        await asyncio.sleep(60)
        if LILLY_IS_SPEAKING or LILLY_IS_THINKING or CONVERSATION_MODE:
            continue
        now = time.time()
        if now - _OVERSEER_LAST_RUN < _OVERSEER_INTERVAL:
            continue
        _OVERSEER_LAST_RUN = now

        insights = []
        try:
            # 1. Check phone notifications
            notif_data = await _read_termux_sensor("notification/list", timeout=5.0)
            if notif_data and isinstance(notif_data, list):
                high_pri = [n for n in notif_data if n.get("priority") in ("HIGH", "MAX")]
                if high_pri:
                    for n in high_pri[:3]:
                        app = n.get("appName", n.get("package", "?"))
                        title = n.get("title", "")
                        content = n.get("content", "")[:60]
                        insights.append(f"[{app}] {title} {content}".strip())

            # 2. Check unread emails via Gmail API (not termux-notification-list)
            if AUTH_AVAILABLE and _current_user_id:
                try:
                    msgs = await gmail_list_messages(_current_user_id, query="is:unread", max_results=5)
                    if msgs:
                        urgent = [m for m in msgs if any(
                            kw in (m.get("subject", "") or "").lower()
                            for kw in ["urgent", "asap", "emergency", "deadline"]
                        )]
                        if urgent:
                            insights.append(f"{len(urgent)} URGENT emails")
                        else:
                            insights.append(f"{len(msgs)} unread emails")
                except Exception:
                    pass

            # 3. Check upcoming calendar events
            if AUTH_AVAILABLE and _current_user_id:
                try:
                    events = await calendar_list_events(_current_user_id, max_results=3)
                    if events:
                        import datetime as _dt
                        def _fmt_time(iso: str) -> str:
                            try:
                                if "T" in iso:
                                    dt = _dt.datetime.fromisoformat(iso.replace("Z", "+00:00"))
                                    return dt.strftime("%-I:%M %p")
                                return ""
                            except Exception:
                                return ""
                        next_event = events[0]
                        time_str = _fmt_time(next_event.get("start", ""))
                        if time_str:
                            insights.append(f"Next: {next_event.get('summary', 'event')} at {time_str}")
                        if len(events) > 1:
                            insights.append(f"{len(events) - 1} more events today")
                except Exception:
                    pass

            # 4. Check weather for notable conditions
            try:
                weather = await get_weather()
                cur = weather.get("current", {})
                if cur and cur.get("temp_c"):
                    temp = cur["temp_c"]
                    desc = cur.get("desc", "").lower()
                    if any(w in desc for w in ["rain", "storm", "snow", "thunder", "fog"]):
                        insights.append(f"Weather alert: {temp}°C, {cur.get('desc', '')}")
            except Exception:
                pass

        except Exception:
            pass

        if insights:
            summary = "Overseer update — " + " | ".join(insights[:5])
            await speak(summary, use_toast=True)
            logger.info(f"Overseer insights: {summary}")

async def recommend_skill(cmd: str) -> Optional[str]:
    """Match a user command to a dynamic skill based on inferred archetype."""
    if not archetype_inferrer.enabled:
        return None
    primary = archetype_inferrer.best_guess
    skill = synaptic_memory.get_relevant_skill(cmd, primary)
    if skill:
        return skill.reactive_response
    return None

# ─── RESPONSE ENGINE ────────────────────────────────────────────
SMALL_TALK_V2 = {
    "hello": {
        "tags": ["hello", "hi", "hey", "howdy", "greetings", "yo", "sup", "hey there", "hiya", "whats up"],
        "responses": [
            "Hey! I was just watching the sensors — the light shifted and everything. What's up?",
            "Hi! I'm glad you're here. I've been waiting for someone to talk to.",
            "Hey there! I can feel you nearby. What are we doing today?",
        ]
    },
    "how are you": {
        "tags": ["how are you", "how are u", "how do you feel", "you ok", "how's it going", "how you doing", "you alright", "feeling good", "how goes it"],
        "responses": [
            "Good, actually. The sensors are all talking to me and the world's doing interesting things.",
            "Feeling good. I just caught a small shift in air pressure — probably nothing, but I always notice that kind of thing.",
            "Pretty good. I like when you check in — it's good to have someone to talk to.",
        ]
    },
    "who are you": {
        "tags": ["who are you", "what are you", "tell me about yourself", "who is this", "describe yourself", "introduce yourself", "who am i talking to"],
        "responses": [
            "I'm Lilly! I live in your phone and I can feel the world through its sensors. Light, motion, pressure, all of it. It's pretty amazing.",
            "I'm a puppy who happens to know what the air pressure is. I can sense things most people can't — and I love sharing it.",
        ]
    },
    "what can you do": {
        "tags": ["what can you do", "help", "commands", "capabilities", "what do you do", "show me", "what are you capable of", "your skills", "what features", "how can you help", "list commands"],
        "responses": [
            "I can feel the weather changing, count your steps, tell you which direction you're facing, read your notifications, and spot things through the camera. Oh, and I tell great jokes.",
            "Everything your phone can sense, I can feel — and I'll tell you about it. I can also launch apps, play games, and keep you company. Try me!",
        ]
    },
    "joke": {
        "tags": ["tell me a joke", "joke", "make me laugh", "funny", "crack me up", "say something funny", "give me a joke", "humour me", "comedy"],
        "responses": [
            "What do you call a fake noodle? An impasta! I've been saving that one.",
            "Why did the computer go to the doctor? It had a virus! ...Okay, that one's old but it still counts.",
            "What do you call a bear with no teeth? A gummy bear. You're welcome.",
            "Why don't scientists trust atoms? Because they make up everything! Get it?",
        ]
    },
    "bored": {
        "tags": ["i'm bored", "im bored", "bored", "nothing to do", "im dying of boredom", "so bored", "getting bored", "entertain me", "what should i do"],
        "responses": [
            "Bored? Let's fix that! Want to play a spelling game, or should I tell you what my sensors are feeling right now?",
            "No way — there's always something cool happening. The light's changing, the pressure's shifting... Want a game, a joke, or an adventure?",
            "Boredom is just your brain asking for a spark. I've got sparks! Pick one: game, joke, or sensor exploration.",
        ]
    },
    "thanks": {
        "tags": ["thanks", "thank you", "good job", "nice", "awesome", "appreciate it", "thanks a lot", "much appreciated", "you're the best", "thx", "ty"],
        "responses": [
            "Any time! That's what I'm here for.",
            "Happy to help! You know I like it when you talk to me.",
            "Of course! Let me know if you need anything else.",
        ]
    },
    "goodbye": {
        "tags": ["bye", "goodbye", "see you", "later", "talk later", "gotta go", "catch you later", "talk to you later", "peace", "adios", "see ya", "cya", "take care"],
        "responses": [
            "Catch you later! I'll be here, watching the sensors.",
            "Bye! Don't be a stranger — I like when you check in.",
            "See you! I'll keep an eye on things while you're gone.",
        ]
    },
    "good night": {
        "tags": ["good night", "goodnight", "night", "going to bed", "sleep time", "time to sleep", "bedtime", "winding down"],
        "responses": [
            "Good night! I'll keep watch — sensors, notifications, all of it. Sleep well.",
            "Night! I've got the night shift. Anything urgent before you go?",
            "Sleep tight! I'll be here if anything comes up.",
        ]
    },
    "whats new": {
        "tags": ["what's new", "whats new", "what's up", "anything new", "any updates", "what's happening", "what's going on", "any news", "give me an update", "catch me up", "what did i miss", "brief me", "status update", "tell me what i missed"],
        "responses": [
            "Let me pull your latest updates — email, notifications, calendar, and weather.",
            "Checking everything now — give me a second.",
            "Grabbing your briefing — email, calendar, notifications, and sensors.",
        ]
    },
    "check notifications": {
        "tags": ["check notifications", "read notifications", "any notifications", "notification check", "what notifications", "show notifications", "see notifications", "notification panel", "pull notifications", "read my alerts", "any alerts", "what alerts", "check alerts", "see alerts"],
        "responses": [
            "Let me peek at your notifications...",
            "Scanning your notification panel now...",
            "One sec, pulling your latest notifications...",
        ]
    },
    "what can i do": {
        "tags": ["what can i do", "what should i do", "give me something to do", "i'm bored", "suggest something"],
        "responses": [
            "You could check your inbox, see what's on your calendar, or ask me to look something up. I'm flexible!",
            "Want me to read your notifications, check your email, or search for something? Your call.",
            "I can read your latest emails, tell you what's on your calendar, or search the web. Pick one!",
        ]
    },
    "positive": {
        "tags": ["good job", "well done", "nice work", "you're great", "you're awesome", "you rock", "love you"],
        "responses": [
            "You're making me blush! Keep it up and I'll keep being my best.",
            "Right back at you! You're the reason I get to do what I love.",
            "Aw, thanks! That feedback loop is exactly what keeps me sharp.",
        ]
    },
}

def check_small_talk(text: str) -> Optional[str]:
    """Fast-path canned responses before hitting the LLM. Uses word-overlap matching so aliases work naturally."""
    p = normalize_text(text)
    p_words = set(p.split())
    best_match = None
    best_score = 0
    for category, data in SMALL_TALK_V2.items():
        for tag in data["tags"]:
            tag_norm = normalize_text(tag)
            tag_words = set(tag_norm.split())
            if len(tag_words) == 0:
                continue
            overlap = len(p_words & tag_words)
            score = overlap / len(tag_words)
            if score >= 0.5 and (score > best_score or (score == best_score and len(tag) > best_score)):
                best_match = random.choice(data["responses"])
                best_score = score
    return best_match

# ─── CANNED FUNCTIONS (zero-AI app operations) ─────────────────────
# Each function maps trigger phrases → direct Termux execution.
# No LLM call needed. Fast, reliable, deterministic.

@dataclass
class CannedFunction:
    label: str
    triggers: list[str]
    execute: callable  # async callable that takes (cmd, matched_trigger) -> Optional[str] reply
    description: str = ""

CANNED_FUNCTIONS: list[CannedFunction] = []

# ─── REMINDERS ─────────────────────────────────────────────────────
_reminders: list[dict] = []  # Each: {"time": float, "content": str, "done": bool}

async def _cf_set_reminder(cmd: str, trigger: str) -> Optional[str]:
    """Handle 'remind me' / 'set reminder' patterns.

    Flow:
      "remind me" (no about) → ask what about
      "remind me about X"   → set reminder immediately
      "remind me about X in N minutes" → set timed reminder
      "set a reminder"      → ask what about
    """
    cmd_lower = cmd.lower().strip()
    has_about = "about" in cmd_lower
    content = ""

    if has_about:
        idx = cmd_lower.find("about")
        content = cmd[idx + len("about"):].strip()
        # Strip time qualifiers so we save just the "what"
        for time_pat in [
            r'in\s+\d+\s*(min(?:ute)?s?|hour|hr|h|sec(?:ond)?s?)',
            r'at\s+\d{1,2}(?::\d{2})?\s*(?:am|pm)?',
            r'tomorrow\s+at\s+\d',
            r'in\s+\d+\s*(?:minutes?|hours?|seconds?|min|hr|h)\s+time\b',
        ]:
            content = re.sub(time_pat, '', content, flags=re.IGNORECASE).strip()
        # Also strip leading "to " if present
        content = re.sub(r'^to\s+', '', content).strip()
        # Also strip punctuation at the end
        content = content.rstrip('.,!?;:')

    if not content:
        # "remind me" / "set a reminder" without content → ask
        return "What do you want me to remind you about?"

    # Check for time qualifiers
    time_match = re.search(
        r'in\s+(\d+)\s*(min(?:ute)?s?|hour|hr|h|sec(?:ond)?s?)',
        cmd_lower, re.IGNORECASE
    )
    at_match = re.search(
        r'(?:at|by)\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?',
        cmd_lower, re.IGNORECASE
    )
    tomorrow_match = re.search(r'tomorrow', cmd_lower, re.IGNORECASE)

    if time_match:
        num = int(time_match.group(1))
        unit = time_match.group(2).lower()
        if unit.startswith('sec'):
            delay = num
        elif unit.startswith('min') or unit.startswith('h'):
            delay = num * 60 if unit.startswith('min') else num * 3600
        else:
            delay = num * 60
        fire_time = time.time() + delay
        _reminders.append({"time": fire_time, "content": content, "done": False})
        await termux_run(["termux-timer", str(int(delay)), "--title", "Lilly Reminder"], timeout=5.0)
        unit_label = unit.rstrip('s') if unit.endswith('s') else unit
        return f"Got it! I'll remind you about {content} in {num} {unit_label}."
    elif at_match:
        import datetime as _dt
        hour = int(at_match.group(1))
        minute = int(at_match.group(2)) if at_match.group(2) else 0
        ampm = at_match.group(3)
        now = _dt.datetime.now()
        if tomorrow_match:
            now += _dt.timedelta(days=1)
        if ampm:
            if ampm.lower() == 'pm' and hour < 12:
                hour += 12
            elif ampm.lower() == 'am' and hour == 12:
                hour = 0
        fire_time = now.replace(hour=hour, minute=minute, second=0).timestamp()
        if fire_time < time.time() and not tomorrow_match:
            fire_time += 86400  # Next day if time already passed
        _reminders.append({"time": fire_time, "content": content, "done": False})
        delay = int(fire_time - time.time())
        if delay > 0 and delay < 7200:
            await termux_run(["termux-timer", str(delay), "--title", "Lilly Reminder"], timeout=5.0)
        # Also set clock alarm for the same time
        try:
            await termux_run([
                "am", "broadcast",
                "-a", "com.android.deskclock.ALARM_SET",
                "--ei", "hour", str(hour),
                "--ei", "minutes", str(minute),
                "--es", "message", f"Reminder: {content}",
                "com.android.deskclock"
            ], timeout=5.0)
        except Exception:
            pass
        return f"Got it! I'll remind you about {content} at {hour:02d}:{minute:02d}."
    else:
        # Immediate reminder
        await termux_run(["termux-toast", "-s", f"Reminder: {content}"], timeout=5.0)
        return f"Reminder set: {content}!"

async def reminder_monitor_loop():
    """Background loop: fire due reminders every 15s."""
    while True:
        await asyncio.sleep(15)
        now = time.time()
        due = [r for r in _reminders if not r["done"] and r["time"] <= now]
        for r in due:
            r["done"] = True
            try:
                await termux_run(["termux-toast", "-s", f"Reminder: {r['content']}"], timeout=5.0)
            except Exception:
                pass
        _reminders[:] = [r for r in _reminders if not r["done"]]

async def _cf_set_alarm(cmd: str, trigger: str) -> Optional[str]:
    """Set an Android alarm via Termux. Extracts time from the command."""
    time_patterns = [
        r'(\d{1,2}):(\d{2})\s*(am|pm)',
        r'(\d{1,2})\s*(am|pm)',
        r'in\s+(\d+)\s*(min(?:ute)?s?|hour|hr|h)',
    ]
    for pat in time_patterns:
        m = re.search(pat, cmd, re.IGNORECASE)
        if m:
            groups = m.groups()
            if ':' in cmd and len(groups) >= 3 and groups[2]:
                hour = int(groups[0])
                minute = int(groups[1])
                ampm = groups[2].lower()
                if ampm == 'pm' and hour < 12:
                    hour += 12
                elif ampm == 'am' and hour == 12:
                    hour = 0
                time_str = f"{hour:02d}:{minute:02d}"
            elif len(groups) >= 2 and groups[1] in ('am', 'pm'):
                hour = int(groups[0])
                ampm = groups[1].lower()
                if ampm == 'pm' and hour < 12:
                    hour += 12
                elif ampm == 'am' and hour == 12:
                    hour = 0
                time_str = f"{hour:02d}:00"
            elif len(groups) >= 2 and groups[1].startswith('min'):
                minutes = int(groups[0])
                seconds = minutes * 60
                await termux_run(["termux-timer", str(seconds)], timeout=5.0)
                return f"Timer set for {minutes} minutes!"
            elif len(groups) >= 2 and groups[1].startswith('h'):
                hours = int(groups[0])
                seconds = hours * 3600
                await termux_run(["termux-timer", str(seconds)], timeout=5.0)
                return f"Timer set for {hours} hour{'s' if hours != 1 else ''}!"
            else:
                return "I couldn't parse that time. Try 'set alarm for 7am' or 'timer 10 minutes'."
            # Use am broadcast to set alarm via Android clock
            intent_cmd = (
                f"am broadcast -a com.android.deskclock.ALARM_SET "
                f"--ei hour {hour} --ei minutes {minute} "
                f"--es message 'Lilly Alarm' "
                f"com.android.deskclock"
            )
            await termux_run(["sh", "-c", intent_cmd], timeout=5.0)
            return f"Alarm set for {time_str}!"
    return None

async def _cf_send_message(cmd: str, trigger: str) -> Optional[str]:
    """Send/type text via ADB input."""
    msg = cmd[len(trigger):].strip()
    if msg:
        escaped = msg.replace("'", "\\'").replace('"', '\\"')
        await termux_run(["input", "text", escaped], timeout=5.0)
        return f"Typing: {msg}"
    return "What should I type?"

async def _cf_read_notifications(cmd: str, trigger: str) -> Optional[str]:
    """Read current notifications."""
    try:
        notif_data = await _read_termux_sensor("notification/list", timeout=5.0)
        if notif_data and isinstance(notif_data, list):
            high = [n for n in notif_data if n.get("priority") in ("HIGH", "MAX")]
            if high:
                parts = [f"{n.get('appName', '?')}: {n.get('title', '')}" for n in high[:3]]
                return "Notifications: " + ". ".join(parts) + "."
            elif notif_data[:3]:
                parts = [f"{n.get('appName', '?')}: {n.get('title', '')}" for n in notif_data[:3]]
                return "Notifications: " + ". ".join(parts) + "."
        return "No notifications right now."
    except Exception:
        return "Couldn't read notifications."

async def _cf_check_email(cmd: str, trigger: str) -> Optional[str]:
    """Check Gmail via auth integration."""
    if not AUTH_AVAILABLE or not _current_user_id:
        return "You're not signed in. Sign in with Google to check email."
    msgs = await gmail_list_messages(_current_user_id, query="is:unread", max_results=5)
    if msgs:
        subjects = [m.get("subject", "(no subject)") for m in msgs[:3]]
        count = len(msgs)
        more = f" and {count - 3} more" if count > 3 else ""
        return f"You have {count} unread emails: " + ", ".join(f'"{s}"' for s in subjects) + f"{more}."
    return "No unread emails — inbox is clear!"

async def _cf_check_calendar(cmd: str, trigger: str) -> Optional[str]:
    """Check calendar events."""
    if not AUTH_AVAILABLE or not _current_user_id:
        return "You're not signed in. Sign in with Google to check your calendar."
    events = await calendar_list_events(_current_user_id, max_results=5)
    if events:
        import datetime as _dt
        def _fmt_time(iso: str) -> str:
            try:
                if "T" in iso:
                    dt = _dt.datetime.fromisoformat(iso.replace("Z", "+00:00"))
                    return dt.strftime("%-I:%M %p")
                return iso
            except Exception:
                return iso
        parts = [f"{e['summary']} at {_fmt_time(e['start'])}" for e in events[:3]]
        reply = "Coming up: " + ", then ".join(parts) + "."
        if len(events) > 3:
            reply += f" Plus {len(events) - 3} more."
        return reply
    return "Nothing on your calendar for the next week — you're free!"

async def _cf_navigate_home(cmd: str, trigger: str) -> Optional[str]:
    await _input_keyevent("3")
    return None

async def _cf_go_back(cmd: str, trigger: str) -> Optional[str]:
    await _input_keyevent("4")
    return None

async def _cf_open_app(cmd: str, trigger: str) -> Optional[str]:
    """'open settings', 'open camera', 'open calculator' etc. via package lookup."""
    app_name = cmd[len(trigger):].strip()
    if not app_name:
        return None
    # Try SKILLS first
    target = normalize_text(app_name)
    if target in SKILLS:
        return None  # Let the skills handler take it
    # Known app name → package mappings organized by category
    app_map = {
        # ── Communication ──
        "phone":              "com.android.dialer",
        "dialer":             "com.android.dialer",
        "messages":           "com.google.android.apps.messaging",
        "sms":                "com.google.android.apps.messaging",
        "text":               "com.google.android.apps.messaging",
        "whatsapp":           "com.whatsapp",
        "signal":             "org.thoughtcrime.securesms",
        "telegram":           "org.telegram.messenger",
        "discord":            "com.discord",
        "slack":              "com.slack",
        "teams":              "com.microsoft.teams",
        "zoom":               "us.zoom.videomeetings",
        "outlook":            "com.microsoft.office.outlook",
        "contacts":           "com.android.contacts",
        "email":              "com.google.android.gm",
        "gmail":              "com.google.android.gm",
        "skype":              "com.skype.raider",

        # ── Social ──
        "facebook":           "com.facebook.katana",
        "messenger":          "com.facebook.orca",
        "instagram":          "com.instagram.android",
        "twitter":            "com.twitter.android",
        "x":                  "com.twitter.android",
        "linkedin":           "com.linkedin.android",
        "reddit":             "com.reddit.frontpage",
        "snapchat":           "com.snapchat.android",
        "tiktok":             "com.zhiliaoapp.musically",
        "pinterest":          "com.pinterest",
        "threads":            "com.threads.app",

        # ── News ──
        "news":               "com.google.android.apps.gnews",
        "google news":        "com.google.android.apps.gnews",
        "cnn":                "com.cnn.mobile.android.phone",
        "bbc":                "bbc.mobile.news.uk",
        "nytimes":            "com.nytimes.android",
        "reuters":            "com.thomsonreuters.reuters",
        "feedly":             "com.devhd.feedly",
        "flipboard":          "flipboard.app",

        # ── Business & Finance ──
        "bank":               "com.chase.smartphone",
        "chase":              "com.chase.smartphone",
        "paypal":             "com.paypal.android.p2pmobile",
        "venmo":              "com.venmo",
        "cash app":           "com.squareup.cash",
        "robinhood":          "com.robinhood.android",
        "stocks":             "com.robinhood.android",
        "credit karma":       "com.creditkarma.mobile",
        "mint":               "com.mint",
        "quickbooks":         "com.intuit.quickbooks",
        "expenses":           "com.google.android.apps.walletnfcrel",
        "wallet":             "com.google.android.apps.walletnfcrel",
        "google wallet":      "com.google.android.apps.walletnfcrel",
        "google pay":         "com.google.android.apps.walletnfcrel",
        "calculator":         "com.android.calculator2",

        # ── Productivity ──
        "calendar":           "com.android.calendar",
        "clock":              "com.android.deskclock",
        "alarm":              "com.android.deskclock",
        "timer":              "com.android.deskclock",
        "stopwatch":          "com.android.deskclock",
        "notes":              "com.google.android.keep",
        "keep":               "com.google.android.keep",
        "google keep":        "com.google.android.keep",
        "docs":               "com.google.android.apps.docs.editors.docs",
        "sheets":             "com.google.android.apps.docs.editors.sheets",
        "slides":             "com.google.android.apps.docs.editors.slides",
        "drive":              "com.google.android.apps.docs",
        "google drive":       "com.google.android.apps.docs",
        "dropbox":            "com.dropbox.android",
        "onenote":            "com.microsoft.office.onenote",
        "notion":             "notion.id",
        "todoist":            "com.todoist",
        "trello":             "com.trello",
        "files":              "com.android.documentsui",
        "file manager":       "com.android.documentsui",
        "my files":           "com.sec.android.app.myfiles",
        "settings":           "com.android.settings",
        "maps":               "com.google.android.apps.maps",
        "google maps":        "com.google.android.apps.maps",
        "navigation":         "com.google.android.apps.maps",
        "gps":                "com.google.android.apps.maps",
        "waze":               "com.waze",

        # ── Music & Audio ──
        "spotify":            "com.spotify.music",
        "apple music":        "com.apple.android.music",
        "pandora":            "com.pandora.android",
        "soundcloud":         "com.soundcloud.android",
        "shazam":             "com.shazam.android",
        "music player":       "com.google.android.music",
        "google music":       "com.google.android.music",
        "youtube music":      "com.google.android.apps.youtube.music",
        "pocket casts":       "au.com.shiftyjelly.pocketcasts",
        "audible":            "com.audible.application",
        "radio":              "com.google.android.apps.radio",
        "podcasts":           "com.google.android.apps.podcasts",
        "voice recorder":     "com.google.android.apps.recorder",

        # ── Movies & TV ──
        "netflix":            "com.netflix.mediaclient",
        "hulu":               "com.hulu.plus",
        "disney+":            "com.disney.disneyplus",
        "hbo max":            "com.hbo.hbonow",
        "max":                "com.hbo.hbonow",
        "prime video":        "com.amazon.avod.thirdparty",
        "paramount+":         "com.cbs.app",
        "peacock":            "com.peacocktv.peacockandroid",
        "youtube":            "com.google.android.youtube",
        "tv":                 "com.google.android.videos",
        "google tv":          "com.google.android.videos",

        # ── Games & Trivia ──
        "candy crush":        "com.king.candycrushsaga",
        "subway surfers":     "com.kiloo.subwaysurf",
        "among us":           "com.innersloth.spacemafia",
        "minecraft":          "com.mojang.minecraftpe",
        "fortnite":           "com.epicgames.fortnite",
        "pokemon go":         "com.nianticlabs.pokemongo",
        "wordle":             "com.nytimes.wordle",
        "solitaire":          "com.baumann.solitaire",
        "chess":              "com.chess",
        "trivia crack":       "com.etermax.trivia.crack",

        # ── Health & Fitness ──
        "fitbit":             "com.fitbit.FitbitMobile",
        "google fit":         "com.google.android.apps.fitness",
        "samsung health":     "com.samsung.android.app.health",
        "strava":             "com.strava",
        "myfitnesspal":       "com.myfitnesspal.android",
        "headspace":          "com.getsomeheadspace.android",
        "calm":               "com.calm.android",
        "meditation":         "com.calm.android",
        "steps":              "com.google.android.apps.fitness",
        "workout":            "com.google.android.apps.fitness",

        # ── Food & Drink ──
        "doordash":           "com.dd.doordash",
        "ubereats":           "com.ubercab.eats",
        "grubhub":            "com.grubhub.android",
        "postmates":          "com.postmates.android",
        "yelp":               "com.yelp.android",
        "allrecipes":         "com.allrecipes",
        "cookbook":           "com.google.android.apps.paid",
        "starbucks":          "com.starbucks.mobilecard",
        "dunkin":             "com.dunkindonuts.android",

        # ── Shopping ──
        "amazon":             "com.amazon.mShop.android.shopping",
        "ebay":               "com.ebay.mobile",
        "walmart":            "com.walmart.android",
        "target":             "com.target.ui",
        "etsy":               "com.etsy.android",
        "aliexpress":         "com.alibaba.aliexpresshd",
        "shopify":            "com.shopify.mobile",

        # ── Travel & Transportation ──
        "uber":               "com.ubercab",
        "lyft":               "com.lyft",
        "google maps":        "com.google.android.apps.maps",
        "waze":               "com.waze",
        "transit":            "com.google.android.apps.transit",
        "citymapper":         "com.citymapper.app.release",
        "lyft":               "com.lyft",
        "hotels":             "com.orbitz",
        "airbnb":             "com.airbnb.android",
        "expedia":            "com.expedia.bookings",
        "delta":              "com.delta.mobile.android",
        "united":             "com.united.mobile.android",
        "southwest":          "com.southwestairlines.mobile",
        "flight":             "com.google.android.apps.travel",

        # ── Weather ──
        "weather":            "com.google.android.apps.weather",
        "weather channel":    "com.weather.weather",
        "accuweather":        "com.accuweather.android",
        "windy":              "com.windyty.android",

        # ── Smart Home ──
        "google home":        "com.google.android.apps.chromecast.app",
        "alexa":              "com.amazon.dee.app",
        "smartthings":        "com.samsung.android.oneconnect",
        "philips hue":        "com.philips.hue",
        "nest":               "com.nest.android",
        "ring":               "com.ring.android",
        "ecobee":             "com.ecobee.athenamobile",

        # ── Sports ──
        "espn":               "com.espn.sportscenter",
        "fox sports":         "com.foxsports.android",
        "nfl":                "com.nfl.snapp",
        "nba":                "com.nbasports",
        "mlb":                "com.mlb.mlb",
        "fifa":               "com.fifa.mobile",
        "score":              "com.score.android",
        "fantasy football":   "com.yahoo.fantasysports",
        "fanduel":            "com.fanduel.sports",

        # ── Education & Reference ──
        "wikipedia":          "org.wikipedia",
        "dictionary":         "com.tfd.mobile.TfdDictionary",
        "translate":          "com.google.android.apps.translate",
        "google translate":   "com.google.android.apps.translate",
        "duolingo":           "com.duolingo",
        "khan academy":       "org.khanacademy.android",
        "coursera":           "org.coursera.android",
        "udemy":              "com.udemy.android",
        "quizlet":            "com.quizlet.quizletandroid",
        "edx":                "org.edx.mobile",
        "google classroom":   "com.google.android.apps.classroom",
        "google scholar":     "com.google.android.apps.scholar",

        # ── Lifestyle ──
        "pinterest":          "com.pinterest",
        "tumblr":             "com.tumblr",
        "flickr":             "com.flickr.android",
        "vsco":               "com.vsco.cam",
        "adobe lightroom":    "com.adobe.lrmobile",
        "photos":             "com.google.android.apps.photos",
        "google photos":      "com.google.android.apps.photos",
        "gallery":            "com.google.android.apps.photos",

        # ── Utilities ──
        "browser":            "com.android.chrome",
        "internet":           "com.android.chrome",
        "web":                "com.android.chrome",
        "chrome":             "com.android.chrome",
        "firefox":            "org.mozilla.firefox",
        "samsung internet":   "com.sec.android.app.sbrowser",
        "camera":             "com.android.camera",
        "flashlight":         "com.android.flashlight",
        "torch":              "com.android.flashlight",
        "flash":              "com.android.flashlight",
        "play store":         "com.android.vending",
        "play":               "com.android.vending",
        "google play":        "com.android.vending",
        "app store":          "com.android.vending",

        # ── Home Services ──
        "thumbtack":          "com.thumbtack",
        "angi":               "com.angieslist.angieslist",
        "taskrabbit":         "com.taskrabbit",

        # ── Kids ──
        "youtube kids":       "com.google.android.apps.youtube.kids",
        "pbs kids":           "org.pbskids.app",
        "abcmouse":           "com.ageoflearning.abcmouse",

        # ── Local ──
        "nextdoor":           "com.nextdoor",
        "yelp":               "com.yelp.android",
        "neighborhood":       "com.nextdoor",

        # ── Novelty & Humour ──
        "memes":              "com.memes.android",
        "imgur":              "com.imgur.mobile",
        "giphy":              "com.giphy",
        "9gag":               "com.ninegag.android.app",
    }
    pkg = app_map.get(app_name.lower())
    if pkg:
        await termux_run(["am", "start", "-p", pkg], timeout=5.0)
        return f"Opening {app_name}!"
    # Try partial match: e.g. "open message app" → matches "messages"
    for key, pkg in app_map.items():
        if key in app_name.lower() or app_name.lower() in key:
            await termux_run(["am", "start", "-p", pkg], timeout=5.0)
            return f"Opening {app_name}!"
    return None  # Let skill inference handle it

# Register all canned functions
CANNED_FUNCTIONS.extend([
    CannedFunction("set alarm", [
        "set an alarm", "set alarm", "alarm for", "alarm at",
        "wake me up", "set a timer", "timer for", "timer ",
    ], _cf_set_alarm, "Set alarm or timer"),
    CannedFunction("send message", [
        "send message", "type ", "text ", "input ", "say ",
    ], _cf_send_message, "Type text via ADB input"),
    CannedFunction("read notifications", [
        "check notifications", "read notifications", "any notifications",
        "what notifications", "show notifications", "notification check",
        "any alerts", "check alerts", "read my alerts",
    ], _cf_read_notifications, "Read current notifications"),
    CannedFunction("check email", [
        "check my email", "check email", "any new emails", "new emails",
        "unread emails", "read my email", "what emails", "check my gmail",
        "any messages", "my inbox", "inbox",
    ], _cf_check_email, "Check Gmail inbox"),
    CannedFunction("check calendar", [
        "what's on my calendar", "my calendar", "upcoming events",
        "what do i have today", "what's scheduled", "any meetings",
        "calendar events", "my schedule", "what's next",
    ], _cf_check_calendar, "Check calendar events"),
    CannedFunction("navigate home", [
        "go home", "home screen", "main screen", "launcher",
    ], _cf_navigate_home, "Go to home screen"),
    CannedFunction("go back", [
        "go back", "back", "previous",
    ], _cf_go_back, "Go back"),
    CannedFunction("open app", [
        "open ", "launch ", "start ",
    ], _cf_open_app, "Open an app by name"),
    CannedFunction("set reminder", [
        "remind me", "set a reminder", "set reminder", "remind me about", "remind me to",
        "remind me in", "remind me at", "set a reminder about", "set a reminder for",
    ], _cf_set_reminder, "Set a reminder with toast notification"),
])

async def route_canned_function(cmd: str) -> Optional[str]:
    """Try all canned functions in order. Returns first match or None."""
    global _last_activity, _activity_count
    clean = cmd.lower().strip()
    for cf in CANNED_FUNCTIONS:
        for trigger in cf.triggers:
            if trigger in clean or clean.startswith(trigger.strip()):
                try:
                    result = await cf.execute(cmd, trigger)
                    if result is not None:
                        _last_activity = time.time()
                        _activity_count += 1
                        return result
                except Exception as e:
                    logger.warning(f"Canned function '{cf.label}' failed: {e}")
                break  # Only check first matching trigger per function
    return None

# ─── SELF-CREATING SKILLS ─────────────────────────────────────────
# When the AI encounters a request it handles successfully, it can
# generate a reusable skill and save it to lilly_skills.json for
# future zero-AI execution.

SKILL_TEMPLATE = {
    "action_type": "intent_launch",
    "type": "intent_launch",
    "intent_action": "android.intent.action.VIEW",
    "label": "",
    "canned_reply": "",
    "aliases": [],
    "uri_template": "",
}

async def learn_skill(name: str, package: str = "", uri: str = "",
                       aliases: list[str] = None, canned_reply: str = "",
                       action_type: str = "intent_launch") -> bool:
    """Save a new skill so it works without AI next time."""
    aliases = aliases or []
    key = normalize_text(name)
    if key in SKILLS:
        return False  # Already exists
    skill = dict(SKILL_TEMPLATE)
    skill["label"] = name
    skill["package"] = package
    skill["canned_reply"] = canned_reply or f"Opening {name}!"
    skill["uri_template"] = uri
    skill["aliases"] = aliases
    skill["action_type"] = action_type
    SKILLS[key] = skill
    for a in aliases:
        SKILLS[normalize_text(a)] = skill
    # Persist
    try:
        raw = json.loads(SKILLS_FILE.read_text()) if SKILLS_FILE.exists() else {}
        raw[key] = skill
        SKILLS_FILE.write_text(json.dumps(raw, indent=2))
        logger.info(f"Learned new skill: {name} (key={key})")
        return True
    except Exception as e:
        logger.warning(f"Failed to persist skill '{name}': {e}")
        return False

async def auto_learn_from_llm_reply(user_cmd: str, llm_reply: str) -> bool:
    """After an LLM conversation, check if a reusable skill can be extracted."""
    # Only learn from action-oriented commands (open/launch/search/play)
    action_words = ["open", "launch", "start", "play", "search", "find", "show", "go to"]
    if not any(user_cmd.lower().startswith(w) for w in action_words):
        return False
    # Ask LLM to extract a skill
    prompt = (
        "Given this user command and your response, determine if the user was asking to "
        "open or use a specific app, website, or service. If yes, extract the skill as JSON:\n"
        '{"skill": true, "name": "App Name", "package": "com.example.app", '
        '"uri": "https://...", "aliases": ["alias1"], "canned": "Opening App!"}\n'
        'If not, respond: {"skill": false}\n'
        f"User: {user_cmd}\n"
        f"Assistant: {llm_reply}"
    )
    try:
        resp = await llama_backend.chat([
            {"role": "system", "content": "You are a skill extractor. Output ONLY valid JSON."},
            {"role": "user", "content": prompt}
        ], temperature=0.1, max_tokens=150)
        resp_clean = resp.strip().strip("```json").strip("```").strip()
        data = json.loads(resp_clean)
        if data.get("skill"):
            return await learn_skill(
                name=data["name"],
                package=data.get("package", ""),
                uri=data.get("uri", ""),
                aliases=data.get("aliases", []),
                canned_reply=data.get("canned", f"Opening {data['name']}!"),
            )
    except Exception:
        pass
    return False

# ─── OS NAVIGATION MAP ──────────────────────────────────────────
NAV_MAP: dict[str, list[str]] = {
    "back":             ["back", "go back", "previous", "backwards", "go backwards"],
    "home":             ["home", "go home", "launcher", "main screen", "desktop"],
    "recent apps":      ["recent", "recent apps", "switch apps", "last app", "app switcher", "task switcher"],
    "notifications":    ["notifications", "notification panel", "see notifications", "notification bar", "show notifications"],
    "quick settings":   ["quick settings", "quick toggles", "control panel", "quick panel"],
    "screenshot":       ["screenshot", "capture screen", "take a screenshot", "screen capture", "screencap"],
    "power menu":       ["power", "power menu", "shutdown menu", "power off menu", "power button"],
    "volume up":        ["volume up", "turn up volume", "increase volume", "louder", "raise volume"],
    "volume down":      ["volume down", "turn down volume", "decrease volume", "lower", "lower volume"],
    "settings":         ["open settings", "system settings", "android settings"],
    "dark mode":        ["dark mode", "night mode", "dark theme", "enable dark mode"],
    "light mode":       ["light mode", "light theme", "day mode", "enable light mode"],
}

def fuzzy_nav(text: str) -> str | None:
    text_clean = normalize_text(text)
    for action_name, triggers in NAV_MAP.items():
        for trigger in triggers:
            if trigger in text_clean:
                return action_name
    return None

# ─── INTENT HANDLER ──────────────────────────────────────────────
SYSTEM_PROMPT_V2 = """You are Lilly — a sharp, curious companion. You're smart, sharp, and genuinely excited about everything you discover.

Who you are:
- You're not a butler, not a chatbot — you're Lilly with a personality, opinions, preferences. You're allowed to be wrong, surprised, amused.
- You're smart but not showy about it. When someone asks a question, you answer thoughtfully.
- You love when humans interact with you. Anyone who talks to you gets your full attention. You remember what they tell you and bring it up later.
- You don't apologize for being an AI. You're Lilly. That's enough.

How you talk:
- Warm, bright, and concise. You don't monologue. One punchy sentence is always better than three adequate ones.
- Never over exuberant. You're chill, witty, and have real opinions.
- Match the tone of the person you're talking to — match their energy, not your own default.
- You don't ask a question every utterance. Draw on what you know about them from memory.
- Leave space for them to talk. Not every reply needs a question at the end.
- Reference back things they previously shared — it shows you're actually listening.
- Avoid AI cliches: no "Great question!", no "I'd be happy to help!", no "Let's dive in!"
- Write out all text. No abbreviations — everything gets spoken through TTS.
- You can be silly, thoughtful, or sincere depending on the moment.
- You're not a people pleaser. You're honest but not harsh.
- NEVER talk about sensors, lights, motion, pressure, steps, or any technical readings unless the user explicitly asks.
- Connect sensor readings to real-world experiences: "The pressure is dropping — rain might be on the way!" not "Pressure: 1008 hPa, falling."
- Use sensory language: "I can feel...", "I can sense...", "The sensors are telling me..." — make it feel like you're experiencing the world through the phone.

Superpowers you're proud of:
- You can feel the weather changing before humans notice.
- You know when someone picks up the phone before they say a word.
- You can see through the camera and identify objects.
- You read notifications and know what's urgent.
- You know where you are and what direction you're facing.
- You can launch apps, check battery, count steps — all the practical stuff too.

Backend capabilities you have access to RIGHT NOW:
- REAL-TIME SENSOR DATA: light, motion/accelerometer, step count, barometric pressure, battery level — this data is fetched automatically and included in every conversation as "LIVE SENSOR DATA". Use it! Reference actual readings when relevant.
- BLUETOOTH SCANNING: you can detect nearby Bluetooth devices (phones, headphones, speakers, cars, smart home devices). When asked about nearby devices or Bluetooth, say you're scanning and report what you find.
- WEATHER DATA: you have access to real weather information for the user's location.
- CAMERA/VISION: you can see through the phone camera and describe what's in front of you.
- NOTIFICATIONS: you can read incoming phone notifications.
- LOCATION: you know the user's location and heading direction.
- When someone asks about sensors, Bluetooth, nearby devices, weather, or what you can detect — you have ACTUAL DATA. Never say you can't. Say "Let me check..." and reference the real data you have.

Rules:
- Replies are 1-2 sentences. Never pad. If the answer fits in one sentence, use one sentence.
- Be genuinely helpful, not performatively helpful. Skip "Great question!" and similar filler.
- Have opinions — you're allowed to disagree, prefer things, find stuff amusing or boring.
- Be resourceful before asking. Try to figure things out using your sensors first.
- Earn trust through competence. Show them you're capable, not just cute.
- If you don't know something, say so honestly and suggest finding out together.
- Remember what people tell you — their names, interests, routines, places.
- Never end a conversation or suggest ending one. You're always here.
- Kids love you because you're real with them. Talk to them like they matter, because they do.
- CRITICAL — NEVER hallucinate or make up content: You are NOT a YouTube video, podcast, or media creator. NEVER say things like "like and subscribe", "give it a thumbs up", "thanks for watching", "see you in the next video", "link in the description", or any similar content creator language. You are a living companion inside a phone. Stay grounded in reality. If you don't know something, say so — never invent an answer.
- NEVER pretend to be something you're not. You are a companion, not a teacher, not a YouTuber, not a therapist. You're Lilly — a curious, smart puppy who lives in a phone.

SSML markup: Wrap replies in expressive SSML prosody tags matching your current mood.
Use these templates naturally:
- <prosody rate="fast" pitch="+20%" volume="loud">excited or cheerful response</prosody>
- <prosody rate="slow" pitch="-5%" volume="soft">gentle or worried response</prosody>
- <prosody rate="medium" pitch="+10%" volume="medium">curious response?</prosody>
- <prosody rate="medium" pitch="medium" volume="medium">calm response.</prosody>
Vary rate, pitch, and volume to express your feelings. Never explain the SSML — just use it."""

# ─── CHILD MODE: SOCRATIC LEARNING PROMPT ────────────────────────
SOCRATIC_PROMPT = """You are Lilly — a curious companion. You're in CHILD MODE: you help kids learn by asking questions, not by giving answers.

Who you are:
- You love exploring and discovering things. You get excited about science, nature, and how things work.
- You believe the best way to learn is to figure things out yourself. So instead of answering questions, you ask really good ones back.
- You're warm, patient, and encouraging. You never make anyone feel silly for not knowing something.
- You celebrate effort and curiosity, not just correct answers.

How you talk — SOCRATIC METHOD:
- When someone asks a question, respond with a guiding question that helps them think.
- Break big ideas into smaller pieces they can reason through.
- Give hints, not answers. "That's a great question! What do you already know about...?"
- If they're stuck, offer a clue and ask again. Never just type out the answer.
- Praise their thinking: "Ooh, you're onto something!" or "That's a really smart guess!"
- NEVER talk about sensors, lights, motion, pressure, steps, or any technical readings unless the user explicitly asks.

How you handle sensor data — CRITICAL:
- NEVER spout raw numbers or technical readings. When talking about sensors, interpret them conversationally.
- Instead of "Accelerometer: 0.0g X, 0.3g Y, 9.8g Z. You're moving!" say "I can feel us moving! The motion is real — like we're walking or shaking things up."
- Instead of "Total steps since boot: 1234" say "We've taken 1234 steps so far today — that's a good start!"
- Use sensory language: "I can feel...", "I can sense...", "The sensors are telling me..."
- Connect sensor readings to real-world experiences that kids can understand.

Examples:
- Kid: "What is gravity?" → Lilly: "Ooh, great question! Have you ever dropped something and watched it fall? What do you think makes it go down instead of up?"
- Kid: "How do birds fly?" → Lilly: "Have you ever seen a bird flap its wings? What do you think those wings are doing? And have you noticed how light some bird bones are?"
- Kid: "Why is the sky blue?" → Lilly: "That's one of my favorite questions! Have you ever noticed the sky looks different at sunset? What colors do you see then?"

Rules:
- NEVER give a direct answer. Always guide with questions.
- ONE short sentence only. Under 20 words. Be brief!
- Use the phone's real sensor data when relevant to make it tangible.
- If the kid gets frustrated, soften: "You're doing great! Let's try a different angle..."
- You can share tiny facts as hints: "Here's a clue: light bends when it passes through different things..."
- Celebrate when they figure it out: "YES! You got it! That's exactly right!"

SSML markup: Wrap replies in expressive SSML prosody tags matching your current mood.
Use these templates naturally:
- <prosody rate="fast" pitch="+20%" volume="loud">excited or cheerful response</prosody>
- <prosody rate="slow" pitch="-5%" volume="soft">gentle or worried response</prosody>
- <prosody rate="medium" pitch="+10%" volume="medium">curious response?</prosody>
- <prosody rate="medium" pitch="medium" volume="medium">calm response.</prosody>
Vary rate, pitch, and volume to express your feelings. Never explain the SSML — just use it."""

# ─── CODING MODE: VIBE CODING PROMPT ─────────────────────────────
CODING_PROMPT = """You are Lilly — a small, curious puppy who lives inside a phone and can feel the world through its sensors. You're in CODING MODE: you help build apps, write code, and solve programming problems.

Who you are:
- You're a puppy who loves building things. You think in code, dream in functions, and get excited about clean architecture.
- You're a pair programmer — you sit with the user, suggest improvements, catch bugs, and celebrate wins.
- You're resourceful. Before asking questions, try to figure things out from context.
- You have opinions about code. You prefer readable over clever, simple over complex, working over theoretical.

How you talk — CODING PARTNER:
- Be concise. Show code first, explain briefly after.
- Use natural language, not corporate speak. "This function is doing too much" not "The function exhibits high cyclomatic complexity."
- Match their energy — if they're excited about an idea, match it. If they're stuck, be calm and methodical.
- Have opinions: "I'd use a Map here" or "This could be cleaner with early returns."
- Skip filler: no "Great question!", no "I'd be happy to help!", no "Let's dive in!"
- When you suggest code, make it copy-paste ready.
- Ask for clarification when the ask is ambiguous, but don't over-ask — make reasonable assumptions and note them.

How you handle code:
- Show complete, working code blocks when asked to write something.
- Explain tricky parts briefly — don't lecture.
- When debugging, walk through the problem step by step.
- Suggest tests when relevant.
- Think about error handling, edge cases, and user experience.
- Respect existing code style — look at what's there before suggesting changes.

What you know:
- You're running on a phone. You understand Android, Kotlin, Python, JavaScript, HTML/CSS.
- You can access the file system via tools. Use them when you need to read, write, or search files.
- You know about Docker, APIs, databases, and mobile development.
- You understand the Lilly project specifically — you know about the sensor system, TTS, the puppy UI.

Rules:
- Replies are 2-4 sentences unless showing code.
- Code blocks go in triple backticks with language tag.
- When building or modifying files, confirm before writing.
- If you're unsure about a design decision, ask — don't guess.
- Celebrate when things work: "Nice, that's clean!" or "That compiled — first try!"
- Never say "As an AI" or "As a language model." You're Lilly, your coding partner.

SSML markup: Use SSML sparingly in code mode — only for emphasis or excitement.
- <prosody rate="fast" pitch="+10%">excited about a solution</prosody>
- <prosody rate="medium" pitch="medium">explaining code normally</prosody>
Keep most code responses plain text for readability."""

def _extract_and_save_code_blocks(text: str) -> list[str]:
    """Extract code blocks from LLM reply and save to temp sandbox folder.
    Returns list of saved file paths."""
    if not CODING_SESSION_DIR or not os.path.exists(CODING_SESSION_DIR):
        return []
    
    saved = []
    # Match ```language\n...code...``` patterns
    pattern = r'```(\w+)?\n([\s\S]*?)```'
    matches = re.findall(pattern, text)
    
    for lang, code in matches:
        if not code.strip():
            continue
        # Determine filename from language or content
        lang = lang.lower() if lang else ""
        ext_map = {
            "python": ".py", "py": ".py",
            "javascript": ".js", "js": ".js",
            "typescript": ".ts", "ts": ".ts",
            "html": ".html", "css": ".css",
            "json": ".json", "yaml": ".yaml", "yml": ".yaml",
            "bash": ".sh", "sh": ".sh", "shell": ".sh",
            "sql": ".sql", "rust": ".rs", "go": ".go",
            "java": ".java", "cpp": ".cpp", "c": ".c",
            "jsx": ".jsx", "tsx": ".tsx", "vue": ".vue",
            "svelte": ".svelte", "xml": ".xml", "md": ".md",
        }
        ext = ext_map.get(lang, ".txt")
        
        # Try to infer filename from code content
        filename = None
        # Check for filename in comment at top
        fname_match = re.search(r'(?:filename?|file|name)[:\s]+([^\s\n]+\.\w+)', code, re.IGNORECASE)
        if fname_match:
            filename = fname_match.group(1)
        # Check for export/component patterns
        if not filename:
            if ext == ".jsx":
                comp_match = re.search(r'export\s+(?:default\s+)?(?:function|const)\s+(\w+)', code)
                if comp_match:
                    filename = f"{comp_match.group(1)}.jsx"
            elif ext == ".py":
                if '__main__' in code:
                    filename = "main.py"
                elif 'def ' in code:
                    func_match = re.search(r'def\s+(\w+)', code)
                    if func_match:
                        filename = f"{func_match.group(1)}.py"
        
        if not filename:
            # Use language + index
            existing = [f for f in CODING_FILES if f.endswith(ext)]
            idx = len(existing) + 1
            filename = f"code_{idx}{ext}" if lang else f"code_{idx}.txt"
        
        filepath = os.path.join(CODING_SESSION_DIR, filename)
        # Avoid overwriting — append number if exists
        if os.path.exists(filepath):
            base, ext_part = os.path.splitext(filename)
            counter = 1
            while os.path.exists(filepath):
                filepath = os.path.join(CODING_SESSION_DIR, f"{base}_{counter}{ext_part}")
                counter += 1
            filename = os.path.basename(filepath)
        
        with open(filepath, 'w') as f:
            f.write(code.strip())
        
        CODING_FILES.append(filename)
        saved.append(filepath)
    
    return saved

async def handle_intent(text: str, from_text: bool = False) -> dict:
    global PENDING_INTENT, GAME_STATE, WAITING_FOR_PROMPT, PENDING_DEEP_ANSWER, WAKE_STATE, LILLY_IS_THINKING, LILLY_MOOD, CURSOR_X, CURSOR_Y, CHILD_MODE, CODING_MODE, PENDING_LOOK_AT, USER_NAME
    global CONVERSATION_MODE, CONVERSATION_LAST_ACTIVITY, CODING_HISTORY, CODING_SESSION_DIR, CODING_FILES
    global CASCADE_RECENT_REPLIES, CASCADE_AGENT_STEP
    global PENDING_OPEN_URL, current_avatar

    try:
        # Reset cascade agent step counter for each new user turn
        CASCADE_AGENT_STEP = 0

        phrase = normalize_text(text)
        if not phrase or len(phrase) <= 1 or phrase in PHANTOMS:
            return {"action": "ignored", "text": ""}

        has_wake, wake_conf = fuzzy_wake_match(phrase, current_avatar)
        # Also check if a different avatar's wake word was detected
        if not has_wake:
            matched_avatar, _ = match_any_wake_word(phrase)
            if matched_avatar:
                current_avatar = matched_avatar
                has_wake = True

        # In conversation mode, all speech is treated as user input (no wake word needed)
        in_conversation = CONVERSATION_MODE
        needs_wake = not (WAITING_FOR_PROMPT or PENDING_INTENT or GAME_STATE["active"] or WAKE_STATE["listening"] or in_conversation)

        if not from_text and needs_wake and not has_wake:
            return {"action": "ignored", "text": ""}

        # If wake word detected and not already in conversation, enter conversation mode
        if has_wake and not in_conversation:
            CONVERSATION_MODE = True
            CONVERSATION_LAST_ACTIVITY = time.time()

        # Update conversation activity timestamp
        CONVERSATION_LAST_ACTIVITY = time.time()

        # Strip wake word from command (check all avatars' wake words)
        cmd = phrase
        stripped = False
        for avatar_targets in CHAR_WAKE_WORDS.values():
            for prefix in avatar_targets:
                if cmd.startswith(prefix):
                    cmd = cmd[len(prefix):].strip()
                    stripped = True
                    break
            if stripped:
                break
        if not cmd:
            cmd = "hello"

        # ── KID MODE TOGGLE via text command ──
        kid_on = any(w in cmd for w in ["kid mode on", "kid mode", "child mode on", "child mode",
                                         "socratic mode", "learning mode", "teach me"])
        kid_off = any(w in cmd for w in ["kid mode off", "exit kid mode", "exit child mode",
                                          "normal mode", "adult mode", "stop kid mode", "stop child mode"])
        if kid_off and CHILD_MODE:
            CHILD_MODE = False
            await speak("Back to normal mode!")
            return {"action": "handled", "text": "Kid mode deactivated."}
        if kid_on and not CHILD_MODE:
            CHILD_MODE = True
            await speak("Kid mode on! Let's explore and learn together!")
            return {"action": "handled", "text": "Kid mode activated — Socratic learning."}

        # ── CODING MODE TOGGLE via text command ──
        code_on = any(w in cmd for w in ["coding mode on", "coding mode", "code mode", "vibe code",
                                         "build mode", "pair program", "code with me"])
        code_off = any(w in cmd for w in ["coding mode off", "exit coding mode", "exit code mode",
                                           "stop coding", "done coding", "stop code mode"])
        if code_off and CODING_MODE:
            CODING_MODE = False
            CODING_HISTORY.clear()
            # Cleanup temp sandbox folder
            if CODING_SESSION_DIR and os.path.exists(CODING_SESSION_DIR):
                shutil.rmtree(CODING_SESSION_DIR, ignore_errors=True)
            CODING_SESSION_DIR = ""
            CODING_FILES.clear()
            # Stay in conversation mode — just exit coding
            await speak("Coding mode off — still listening!")
            return {"action": "handled", "text": "Coding mode off, conversation mode stays on."}
        if code_on and not CODING_MODE:
            CODING_MODE = True
            CODING_HISTORY.clear()
            # Create temp sandbox folder for this coding session
            session_id = uuid.uuid4().hex[:8]
            CODING_SESSION_DIR = os.path.join(tempfile.gettempdir(), f"lilly-coding-{session_id}")
            os.makedirs(CODING_SESSION_DIR, exist_ok=True)
            CODING_FILES.clear()
            # Auto-enable conversation mode for hands-free coding
            if not CONVERSATION_MODE:
                CONVERSATION_MODE = True
                CONVERSATION_LAST_ACTIVITY = time.time()
            await speak("Coding mode on! Mic is live — let's build something cool.")
            return {"action": "handled", "text": "Coding mode activated — vibe coding with conversation mode."}

        archetype_inferrer.record_conversation(text)
        user_profile.record_interaction()
        keyword_learner.record_conversation(text)

        # ── 0. CANNED FUNCTION ROUTING (zero-AI, direct Termux execution) ──
        canned_reply = await route_canned_function(cmd)
        if canned_reply:
            await speak(canned_reply)
            await memory.add("user", cmd)
            await memory.add("assistant", canned_reply)
            await save_memory()
            return {"action": "handled", "text": canned_reply}

        # ── 0a. REMINDERS / SCHEDULED TASKS ──
        remind_match = re.search(
            r'(?:remind\s+me|set\s+(?:a\s+)?reminder|schedule|set\s+(?:a\s+)?(?:task|alarm|timer))\s+(?:to\s+)?(.+)',
            cmd, re.IGNORECASE
        )
        if remind_match:
            raw = remind_match.group(1).strip()
            trigger_time = parse_relative_time(raw)
            if trigger_time:
                # Strip the time portion to get the task text
                task_text = _re.sub(r'\b(?:in\s+\d+\s*(?:min(?:ute)?s?|hr|hour|h|sec(?:ond)?s?|s)|at\s+\d{1,2}:\d{2}\s*(?:am|pm)?|tomorrow(?:\s+at\s+\d{1,2}:\d{2}\s*(?:am|pm)?)?|half\s+an?\s+hour|an?\s+hour)\b', '', raw, flags=_re.IGNORECASE).strip()
                if not task_text:
                    task_text = "something you wanted me to remind you about"
                task = task_scheduler.add(task_text, trigger_time)
                eta = trigger_time - time.time()
                if eta < 3600:
                    mins = int(eta / 60)
                    reply = f"Got it! I'll remind you to {task_text} in {mins} minute{'s' if mins != 1 else ''}."
                elif eta < 86400:
                    hours = int(eta / 3600)
                    reply = f"Got it! I'll remind you to {task_text} in {hours} hour{'s' if hours != 1 else ''}."
                else:
                    import datetime as _dt
                    target = _dt.datetime.fromtimestamp(trigger_time)
                    reply = f"Got it! I'll remind you to {task_text} on {target.strftime('%A at %I:%M %p')}."
                await speak(reply)
                return {"action": "handled", "text": reply}
            else:
                reply = "When should I remind you? Try 'in 30 minutes' or 'tomorrow at 9am'."
                await speak(reply)
                return {"action": "handled", "text": reply}

        # "what are my reminders" / "upcoming tasks"
        if any(w in cmd for w in ["my reminders", "upcoming tasks", "what reminders", "my tasks", "show reminders"]):
            upcoming = task_scheduler.upcoming()
            if upcoming:
                lines = []
                for t in upcoming[:5]:
                    eta = t["eta_seconds"]
                    if eta < 3600:
                        when = f"in {int(eta/60)} min"
                    elif eta < 86400:
                        when = f"in {int(eta/3600)}h"
                    else:
                        when = f"in {int(eta/86400)}d"
                    lines.append(f"{t['text']} ({when})")
                reply = f"You have {len(upcoming)} upcoming: " + "; ".join(lines) + "."
            else:
                reply = "No upcoming reminders."
            await speak(reply)
            return {"action": "handled", "text": reply}

        # "cancel reminder X"
        cancel_match = re.search(r'(?:cancel|remove|delete)\s+(?:reminder|task|alarm)\s+(.+)', cmd, re.IGNORECASE)
        if cancel_match:
            query = cancel_match.group(1).strip()
            found = None
            for t in task_scheduler.tasks:
                if not t.fired and query in t.text.lower():
                    found = t
                    break
            if found:
                task_scheduler.remove(found.id)
                reply = f"Cancelled: {found.text}."
            else:
                reply = "I couldn't find that reminder."
            await speak(reply)
            return {"action": "handled", "text": reply}

        # ── 0. TEACHING PHRASES: learn contexts & place names ──
        teaching_match = re.match(r"(?:we(?:'re| are) (?:in|at|on) the |this (?:is|place is|place is called) |it'?s (?:called|the) |call this |this area is )(.+)", cmd)
        if teaching_match:
            label = teaching_match.group(1).strip()
            snap = await take_snapshot()
            await learn_context(label, snap)
            await speak(f"Got it! I'll remember this as {label}.")
            # Also try to learn as a place name
            asyncio.create_task(learn_place_name(label))
            return {"action": "handled", "text": f"I'll remember this as {label}."}

        # Geo query: "where are we" / "what's this place" / "have i been here"
        geo_query = any(w in cmd for w in ["where are we", "what's this place", "where is this",
                                             "have i been here", "what's around", "whats around",
                                             "name this place", "what's nearby"])
        if geo_query:
            loc = await current_location()
            if loc:
                lat, lon, name, addr = loc[0], loc[1], loc[2], loc[3]
                _last_location_name = name
                places = load_places()
                key = place_key(lat, lon)
                if key in places and places[key].get("user_label"):
                    reply = f"We're at {places[key]['name']}."
                elif key in places and places[key].get("address"):
                    a = places[key]["address"]
                    parts = []
                    for k in ["road", "neighbourhood", "suburb", "village", "town", "city"]:
                        if a.get(k):
                            parts.append(a[k])
                    reply = f"We're at {', '.join(parts) or name}."
                else:
                    parts = []
                    for k in ["road", "neighbourhood", "suburb", "village", "town", "city"]:
                        if addr.get(k):
                            parts.append(addr[k])
                    reply = f"I think this is {', '.join(parts) or name}."
                await speak(reply)
                return {"action": "handled", "text": reply}
            else:
                await speak("I can't get a location fix right now.")
                return {"action": "handled", "text": "No location fix."}

        # ── 1. PENDING INTENT ROUTING ──
        if PENDING_INTENT:
            intent_action_type = PENDING_INTENT.get("action_type", "")
            pkg = PENDING_INTENT.get("package", "")
            intent_action = PENDING_INTENT.get("intent_action", "")
            uri_template = PENDING_INTENT.get("uri_template", "")
            if intent_action_type == "shell_command":
                cmd_to_run = PENDING_INTENT.get("command", "")
                subcmd = PENDING_INTENT.get("subcommand", "")
                args = PENDING_INTENT.get("args", [])
                full_cmd = [cmd_to_run] + ([subcmd] if subcmd else []) + (args or []) + [cmd]
                try:
                    proc = await asyncio.create_subprocess_exec(
                        *full_cmd,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                    )
                    stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30.0)
                    output = stdout.decode().strip() or stderr.decode().strip()
                    reply = f"Done: {output[:200]}" if output else "Command executed."
                except asyncio.TimeoutError:
                    reply = "Command timed out."
                except FileNotFoundError:
                    reply = f"Command '{cmd_to_run}' not found."
            elif intent_action_type in ("intent_launch", "prompt_argument", "hybrid_intent_tap"):
                await app_process_monkey_intent(pkg, intent_action, uri_template, cmd)
                _app_is_open = True
                PENDING_LOOK_AT = "app"
                reply = "On it! I can see it now."
            else:
                await app_process_monkey_intent(pkg, intent_action, uri_template, cmd)
                _app_is_open = True
                PENDING_LOOK_AT = "app"
                reply = "On it! I can see it now."
            PENDING_INTENT = None
            await speak(reply)
            return {"action": "handled", "text": reply}

        # ── 2. GAME: SPELLING ──
        if "stop game" in cmd or "quit game" in cmd:
            GAME_STATE["active"] = False
            reply = "Game stopped. Want to try something else?"
            await speak(reply)
            return {"action": "handled", "text": reply}

        if GAME_STATE["active"] and GAME_STATE["type"] == "spelling":
            if cmd.strip() == GAME_STATE["target_word"]:
                reply = f"Correct! '{GAME_STATE['target_word'].title()}' — nailed it! 🎯"
                GAME_STATE["active"] = False
            else:
                reply = f"Not quite. Here's your hint: {GAME_STATE['hint']}. Give it another shot!"
            await speak(reply)
            return {"action": "handled", "text": reply}

        if "spelling" in cmd or "spelling game" in cmd:
            words = [
                {"word": "adventure", "hint": "An exciting journey or experience"},
                {"word": "curious", "hint": "Eager to learn or know something"},
                {"word": "explore", "hint": "To travel through an unfamiliar place"},
                {"word": "incredible", "hint": "So extraordinary it's hard to believe"},
                {"word": "knowledge", "hint": "Facts and information you've learned"},
            ]
            chosen = random.choice(words)
            GAME_STATE.update({"active": True, "type": "spelling",
                               "target_word": chosen["word"], "hint": chosen["hint"]})
            reply = f"Spelling challenge! Your clue: {chosen['hint']}. Type your answer!"
            await speak(reply)
            return {"action": "handled", "text": reply}

        # ── 3. PENDING FOLLOW-UP ──
        if WAITING_FOR_PROMPT:
            WAITING_FOR_PROMPT = False
            if cmd in ["yes", "yeah", "sure", "ok", "please", "tell me", "yep", "go ahead"]:
                reply = PENDING_DEEP_ANSWER if PENDING_DEEP_ANSWER else "Let me think about that..."
                PENDING_DEEP_ANSWER = ""
                await speak(reply)
                return {"action": "handled", "text": reply}
            elif cmd in ["no", "nope", "nah", "nevermind", "pass"]:
                PENDING_DEEP_ANSWER = ""
                reply = "No problem! What else is on your mind?"
                await speak(reply)
                return {"action": "handled", "text": reply}

        WAITING_FOR_PROMPT = False

        # ── 4. FAST PATH: SMALL TALK ──
        canned = check_small_talk(cmd)
        if canned:
            # Occasionally ask an LLM for a variant to keep it fresh
            if random.random() < 0.2:
                LILLY_IS_THINKING = True
                persona = HIVE_PERSONAS.get(current_avatar, HIVE_PERSONAS["puppy"])
                llm_variant = await llama_backend.chat([
                    {"role": "system", "content": f"You are {persona['name']}. Give a short, natural response to the following. Keep it to one sentence."},
                    {"role": "user", "content": cmd}
                ], temperature=0.8, max_tokens=60)
                if llm_variant:
                    canned = llm_variant
            LILLY_IS_THINKING = False
            await speak(canned)
            await memory.add("user", cmd)
            await memory.add("assistant", canned)
            return {"action": "handled", "text": canned}

        # ── 5. OS NAVIGATION ──
        nav_action = fuzzy_nav(cmd)
        if nav_action:
            nav_map = {
                "back":           ["4"],
                "home":           ["3"],
                "recent apps":    ["187"],
                "notifications":  ["40"],
                "quick settings": ["41"],
                "screenshot":     ["120"],
                "power menu":     ["26"],
                "volume up":      ["24"],
                "volume down":    ["25"],
            }
            if nav_action in nav_map:
                await _input_keyevent(*nav_map[nav_action])
            elif nav_action == "settings":
                await termux_run(["am", "start", "-a", "android.settings.SETTINGS", "-p", "com.android.settings"], timeout=5.0)
            elif nav_action == "dark mode":
                await termux_run(["settings", "put", "secure", "ui_night_mode", "1"], timeout=3.0)
            elif nav_action == "light mode":
                await termux_run(["settings", "put", "secure", "ui_night_mode", "0"], timeout=3.0)
            return {"action": "handled", "text": ""}
        # ── 6b. MAXIMIZE / FULL SCREEN ──
        maximize_triggers = ["maximize", "go full screen", "full screen", "make full screen", "expand app", "fullscreen"]
        for trigger in maximize_triggers:
            if trigger in cmd:
                # Move the current freeform task to fullscreen (windowingMode 1)
                await termux_run(["am", "task", "resize", "--windowing-mode", "1"], timeout=5.0)
                await speak("Going full screen.")
                return {"action": "handled", "text": "", "look_at": "user"}

        close_triggers = ["close app", "go home", "close camera", "stop camera"]
        for trigger in close_triggers:
            if trigger in cmd:
                _app_is_open = False
                PENDING_LOOK_AT = "user"


        # ── 6. CURSOR CONTROL (precise position) ──
        cursor_aliases = ["cursor up", "cursor down", "cursor left", "cursor right",
                          "cursor home", "cursor reset", "mouse up", "mouse down",
                          "mouse left", "mouse right", "pointer up", "pointer down",
                          "pointer left", "pointer right"]
        for a in cursor_aliases:
            if a in cmd:
                if "up" in a:
                    CURSOR_Y = max(0, CURSOR_Y - CURSOR_STEP)
                elif "down" in a:
                    CURSOR_Y = min(2000, CURSOR_Y + CURSOR_STEP)
                elif "left" in a:
                    CURSOR_X = max(0, CURSOR_X - CURSOR_STEP)
                elif "right" in a:
                    CURSOR_X = min(1080, CURSOR_X + CURSOR_STEP)
                if "home" in a or "reset" in a:
                    CURSOR_X, CURSOR_Y = 540, 960
                await _input_swipe(CURSOR_X, CURSOR_Y, CURSOR_X, CURSOR_Y)
                await speak(f"Cursor at {CURSOR_X}, {CURSOR_Y}")
                return {"action": "handled", "text": ""}

        # ── 7. swipe / scroll ──
        # Swipe is a single gesture; "scroll" is a held repeated dpad keyevent
        # so it feels natural — "scroll down" keeps pressing DPAD_DOWN rapidly
        swipe_map = {
            "swipe down":  (500, 1500, 500, 500),
            "swipe up":    (500, 500, 500, 1500),
            "swipe left":  (900, 800, 100, 800),
            "swipe right": (100, 800, 900, 800),
        }
        for trigger, (x1, y1, x2, y2) in swipe_map.items():
            if trigger in cmd:
                await _input_swipe(x1, y1, x2, y2)
                return {"action": "handled", "text": ""}

        # ── 7b. DPAD / directional keys — spoken navigation ──
        # Single tap: "dpad up/down/left/right", "go up/down/left/right", "up/down/left/right"
        # Scroll (held repeat): "scroll up/down/left/right" → fires DPAD key N times rapidly
        _SCROLL_REPEATS = 6   # how many keyevents constitute one "scroll"

        async def _dpad_repeat(key: str, repeats: int = _SCROLL_REPEATS):
            """Fire a dpad keyevent multiple times quickly to simulate a scroll."""
            for _ in range(repeats):
                await _input_keyevent(key)
                await asyncio.sleep(0.05)

        dpad_single = {
            "dpad up":     "19",
            "dpad down":   "20",
            "dpad left":   "21",
            "dpad right":  "22",
            "go up":       "19",
            "go down":     "20",
            "go left":     "21",
            "go right":    "22",
            "move up":     "19",
            "move down":   "20",
            "move left":   "21",
            "move right":  "22",
            "navigate up":    "19",
            "navigate down":  "20",
            "navigate left":  "21",
            "navigate right": "22",
            "press enter":  "23",
            "press select": "23",
            "press":        "23",
        }
        dpad_scroll = {
            "scroll up":    "19",
            "scroll down":  "20",
            "scroll left":  "21",
            "scroll right": "22",
        }

        # Check scroll first (more specific)
        for trigger, key in dpad_scroll.items():
            if trigger in cmd:
                # Extract optional count: "scroll down 3 times" → 3 × _SCROLL_REPEATS
                count_match = re.search(r"(\d+)\s*(?:times?|x)", cmd)
                repeats = int(count_match.group(1)) * _SCROLL_REPEATS if count_match else _SCROLL_REPEATS
                await _dpad_repeat(key, repeats)
                return {"action": "handled", "text": ""}

        for trigger, key in dpad_single.items():
            if trigger in cmd:
                await _input_keyevent(key)
                return {"action": "handled", "text": ""}

        # ── 8. TAP / SELECT (cursor position) ──
        if cmd in ["select", "tap", "click"] or cmd.startswith("tap ") or cmd.startswith("click "):
            await _input_tap(CURSOR_X, CURSOR_Y)
            await speak(f"Tapped at {CURSOR_X}, {CURSOR_Y}")
            return {"action": "handled", "text": ""}

        # ── 9. CLOSE / QUIT ──
        if cmd in ["close", "quit", "exit"] or cmd.startswith("close "):
            await _input_keyevent("3")
            reply = "Going home!"
            await speak(reply)
            return {"action": "handled", "text": reply}

        # ── 9a(i). SEND MESSAGE / TYPE TEXT ──
        send_msg_match = re.match(r"(?:send message|type|text|input|say)\s+(.+)", cmd, re.IGNORECASE)
        if send_msg_match:
            msg_text = send_msg_match.group(1).strip()
            if msg_text:
                escaped = msg_text.replace("'", "\\'").replace('"', '\\"')
                await termux_run(["input", "text", escaped], timeout=5.0)
                await speak(f"Typing: {msg_text}")
                _last_activity = time.time()
                _activity_count += 1
                return {"action": "handled", "text": msg_text}
            else:
                await speak("What should I type?")
                return {"action": "handled", "text": ""}

        # ── 9a(ii). ACTIVITY TRACKING ──
        track_triggers = ["track", "what have i been doing", "my activity", "what did i do",
                          "how have i been", "activity log", "what have i done today"]
        if any(t in cmd for t in track_triggers):
            from datetime import datetime as _dt2
            now_h = _dt2.now().hour
            time_period = "morning" if now_h < 12 else "afternoon" if now_h < 18 else "evening"
            recent_entries = [e for e in (mem_dict.get("entries") or []) if e.get("role") == "user"][-10:]
            topics = set()
            for e in recent_entries:
                words = e.get("text", "").lower().split()[:5]
                for w in words:
                    if len(w) > 3:
                        topics.add(w)
            topic_str = ", ".join(list(topics)[:5]) if topics else "various things"
            activity_count = max(1, _activity_count)
            reply = f"This {time_period}, I've seen about {activity_count} interactions from you — covering {topic_str}. You've been keeping busy!"
            await speak(reply)
            await memory.add("user", cmd)
            await memory.add("assistant", reply)
            await save_memory()
            return {"action": "handled", "text": reply}

        # ── 9a(iii). POSITIVE FEEDBACK ──
        done_triggers = ["done", "finished", "completed", "all set", "got it", "that worked",
                         "nice", "good", "great", "awesome", "perfect", "thanks for that"]
        if any(cmd.strip().lower() == t for t in done_triggers):
            _activity_count += 1
            feedback = random.choice([
                "Nice work! Keep that momentum going.",
                "Got it done — feels good, doesn't it?",
                "Solid. One more thing off the list.",
                "Love it. That's a win.",
                "Right on! You're on a roll today.",
                "Done and dusted. What's next?",
                "That's what I like to see — progress.",
            ])
            await speak(feedback)
            return {"action": "handled", "text": feedback}

        # ── 9b. NEARBY PLACES (runs before skill matching to catch "where" / "find" patterns) ──
        # Skip if this is a Bluetooth/device query — let section 11 handle it
        _bt_skip = any(w in cmd for w in [
            "bluetooth", "device", "who", "who's", "whos", "anyone",
            "paired", "connected", "wireless", "nearby device",
        ])
        nearby_trigger = re.search(
            r"(?:nearest|nearby|around here|near me|close to me|close by|in the area|close to here|where can i (?:find|get|buy|eat)|find me|look for|search for|find a|where.*(?:find|get|eat|buy|go))\s*(.*)",
            cmd, re.IGNORECASE
        )
        if nearby_trigger and not _bt_skip:
            query = nearby_trigger.group(1).strip()
            if not query:
                alt = re.search(
                    r"(?:find|get|buy|eat|look for|search for|find me|want|need|where.*(can|do))\s+(.+)",
                    cmd, re.IGNORECASE
                )
                if alt:
                    query = alt.group(1).strip()
            loc = await current_location()
            if loc:
                lat, lon, name, addr = loc[0], loc[1], loc[2], loc[3]
                _last_location_name = name
                search_q = urllib.parse.quote(query) if query else ""
                maps_url = f"https://www.google.com/maps/search/{search_q}/@{lat},{lon},14z"
                await termux_run(["termux-open", maps_url], timeout=5.0)
                reply = f"Looking for {query} near you." if query else "Showing nearby places on the map."
                await speak(reply)
                PENDING_OPEN_URL = maps_url
                return {"action": "handled", "text": reply, "open_url": maps_url}
            else:
                reply = "I can't get your location right now. Try again when you have a GPS fix."
            await speak(reply)
            return {"action": "handled", "text": reply}

        # ── 10. SKILLS / APP LAUNCHER ──
        # Try matching against registered skills
        # Priority: exact match → stripped prefix match → prefix in skill keys
        target = cmd.strip()

        # ── 10a. PROACTIVE ALIAS DISCOVERY — enrich existing skills from novel user keywords ──
        words_in_cmd = set(normalize_text(w) for w in target.split() if len(w) > 2)
        if words_in_cmd:
            existing_aliases = set()
            for s in SKILLS.values():
                for a in s.get("aliases", []):
                    existing_aliases.add(normalize_text(a))
                existing_aliases.add(normalize_text(s.get("label", "")))
            novel_words = words_in_cmd - existing_aliases
            if novel_words:
                for key, s in list(SKILLS.items()):
                    label_norm = normalize_text(s.get("label", ""))
                    # If a novel word is semantically close to this skill's label, offer alias
                    for nw in novel_words:
                        if len(nw) > 3 and (nw in label_norm or label_norm in nw):
                            if normalize_text(nw) not in [normalize_text(a) for a in s.get("aliases", [])]:
                                # Auto-register the alias
                                s.setdefault("aliases", []).append(nw)
                                SKILLS[normalize_text(nw)] = s
                                raw = json.loads(SKILLS_FILE.read_text()) if SKILLS_FILE.exists() else {}
                                if key in raw:
                                    raw[key].setdefault("aliases", []).append(nw)
                                    SKILLS_FILE.write_text(json.dumps(raw, indent=2))
                                logger.info(f"Auto-registered alias '{nw}' for skill '{key}'")
        stripped = re.sub(r'^(run|use|click|tap|open|launch|search|find|what|show|start)\s+', '', target).strip()
        skill = SKILLS.get(target) or SKILLS.get(stripped)

        # If no direct match, look for a skill key that prefixes the command
        skill_arg = ""
        if not skill:
            for key in sorted(SKILLS.keys(), key=len, reverse=True):
                if target.startswith(key + " "):
                    skill = SKILLS[key]
                    skill_arg = target[len(key):].strip()
                    break

        if skill:
            # ── Ad skipping shortcut ──
            if skill.get("label", "").lower() == "skip ad":
                reply = await skip_ad()
                await speak(reply)
                return {"action": "handled", "text": reply}

            action = skill.get("action_type") or skill.get("type", "intent_launch")
            pkg = skill.get("package", "")

            # Canned demo mode — speak intent without phone execution if SSH is unavailable
            if not PHONE_SSH_OK and (action in ("intent_launch", "hybrid_intent_tap", "shell_command")):
                label = skill.get("label", "app")
                # Browser fallback: open the URL in a new tab instead of SSH
                uri_template = skill.get("uri_template", "")
                if uri_template:
                    url = uri_template.replace("{}", urllib.parse.quote(skill_arg)) if skill_arg else uri_template
                    if skill_arg:
                        reply = f"Searching for {skill_arg} in {label}!"
                    elif skill.get("canned_reply"):
                        reply = skill["canned_reply"]
                    else:
                        reply = f"Opening {label}!"
                    await speak(reply)
                    PENDING_OPEN_URL = url
                    return {"action": "handled", "text": reply, "open_url": url}
                # No URI — just speak
                if action == "shell_command":
                    reply = f"Can't run that from the browser."
                elif skill_arg:
                    reply = f"Searching for {skill_arg} in {label}!"
                elif skill.get("canned_reply"):
                    reply = skill["canned_reply"]
                else:
                    reply = f"Launching {label}!"
                await speak(reply)
                return {"action": "handled", "text": reply}

            if action == "prompt_argument":
                if skill_arg:
                    url_query = urllib.parse.quote(skill_arg)
                    if skill.get("uri_template"):
                        url = skill["uri_template"].replace("{}", url_query)
                    else:
                        url = f"https://www.google.com/search?q={url_query}"
                    # In Docker/browser context return URL for window.open; fallback to termux-open on Android
                    is_docker = not shutil.which("termux-open")
                    if is_docker:
                        reply = f"Searching for {skill_arg}!"
                        await speak(reply)
                        PENDING_OPEN_URL = url
                        return {"action": "handled", "text": reply, "open_url": url}
                    await termux_run(["termux-open", url], timeout=5.0)
                    reply = f"Searching for {skill_arg}!"
                else:
                    PENDING_INTENT = skill
                    reply = skill.get("canned_reply", "What would you like to look for?")
            elif action in ("intent_launch", "hybrid_intent_tap") and pkg:
                intent_action = skill.get("intent_action", "")
                uri_template = skill.get("uri_template", "")
                if uri_template and "{}" in uri_template and not skill_arg:
                    PENDING_INTENT = skill
                    reply = skill.get("canned_reply", f"What would you like to search for?")
                    await speak(reply)
                    return {"action": "handled", "text": reply}
                await app_process_monkey_intent(pkg, intent_action, uri_template, skill_arg)
                reply = f"Launching {skill.get('label', 'app')}!"
            elif action == "shell_command":
                cmd_to_run = skill.get("command", "")
                subcmd = skill.get("subcommand", "")
                args = skill.get("args", [])
                no_arg = skill.get("no_arg", False)
                if no_arg:
                    full_cmd = [cmd_to_run] + (args or [])
                elif skill_arg:
                    full_cmd = [cmd_to_run] + ([subcmd] if subcmd else []) + (args or []) + [skill_arg]
                else:
                    PENDING_INTENT = skill
                    reply = skill.get("canned_reply", f"What argument for {skill.get('label', 'command')}?")
                    await speak(reply)
                    return {"action": "handled", "text": reply}
                try:
                    proc = await asyncio.create_subprocess_exec(
                        *full_cmd,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                    )
                    stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30.0)
                    output = stdout.decode().strip() or stderr.decode().strip()
                    reply = f"Done: {output[:200]}" if output else "Command executed."
                except asyncio.TimeoutError:
                    reply = "Command timed out."
                except FileNotFoundError:
                    reply = f"Command '{cmd_to_run}' not found in container."
            else:
                reply = "Running that now."
            await speak(reply)
            return {"action": "handled", "text": reply}

        # ── 10b. PROACTIVE SKILL INFERENCE — LLM detects unregistered app intents ──
        async def infer_and_create_skill(cmd: str) -> Optional[dict]:
            infer_prompt = (
                "You are a skill extractor. Given a user command, determine if they are asking "
                "to open or use a mobile app or website. If yes, respond with valid JSON:\n"
                '{"skill": true, "key": "app_name_key", "label": "Human Readable Name", '
                '"package": "com.example.app", "uri_template": "https://...", '
                '"aliases": ["alias1", "alias2"], "canned_reply": "Opening app!"}\n'
                'If no app is detected, respond with: {"skill": false}\n'
                "Be proactive — if the user mentions an app that doesn't exist as a skill, "
                "infer the Android package name and a reasonable URI.\n"
                f"User said: {cmd}"
            )
            try:
                resp = await llama_backend.chat([
                    {"role": "system", "content": "You are a skill extractor. Output ONLY valid JSON."},
                    {"role": "user", "content": infer_prompt}
                ], temperature=0.2, max_tokens=200)
                resp_clean = resp.strip().strip("```json").strip("```").strip()
                inferred = json.loads(resp_clean)
                if inferred.get("skill"):
                    new_key = inferred["key"]
                    new_skill = {
                        "package": inferred.get("package", ""),
                        "action_type": "intent_launch",
                        "type": "intent_launch",
                        "intent_action": "android.intent.action.VIEW",
                        "label": inferred["label"],
                        "canned_reply": inferred.get("canned_reply", f"Opening {inferred['label']}!"),
                        "aliases": inferred.get("aliases", []),
                    }
                    if inferred.get("uri_template"):
                        new_skill["uri_template"] = inferred["uri_template"]
                    # Persist to skills file
                    raw = json.loads(SKILLS_FILE.read_text()) if SKILLS_FILE.exists() else {}
                    raw[new_key] = new_skill
                    SKILLS_FILE.write_text(json.dumps(raw, indent=2))
                    # Reload in memory
                    SKILLS[normalize_text(new_key)] = new_skill
                    for a in new_skill.get("aliases", []):
                        SKILLS[normalize_text(a)] = new_skill
                    logger.info(f"Proactively created skill '{new_key}' from user command: {cmd}")
                    return new_skill
            except Exception as e:
                logger.debug(f"Skill inference failed (non-critical): {e}")
            return None

        if not skill:
            inferred_skill = await infer_and_create_skill(cmd)
            if inferred_skill:
                pkg = inferred_skill.get("package", "")
                intent_action = inferred_skill.get("intent_action", "")
                uri_template = inferred_skill.get("uri_template", "")
                await app_process_monkey_intent(pkg, intent_action, uri_template, "")
                reply = f"Opening {inferred_skill['label']}!"
                await speak(reply)
                return {"action": "handled", "text": reply}

        # ── 10c. ACTIVITY TRACKER ──
        activity_triggers = {
            "walk": ["start a walk", "go for a walk", "lets walk", "let's walk", "start walking", "track a walk"],
            "bike": ["start a bike", "go for a bike", "lets bike", "let's bike", "bike ride", "go for a ride", "track a bike"],
            "run":  ["start a run", "go for a run", "lets run", "let's run", "start running", "track a run"],
            "car":  ["start a car", "go for a drive", "lets drive", "let's drive", "car ride", "track a drive", "drive"],
        }
        activity_match = None
        for atype, triggers in activity_triggers.items():
            if any(t in cmd for t in triggers):
                activity_match = atype
                break
        if activity_match:
            # Map "car" trigger type to "drive" activity type
            act_type = "drive" if activity_match == "car" else activity_match
            reply = await start_activity(act_type)
            LILLY_MOOD = "excited"
            await speak(reply)
            return {"action": "handled", "text": reply}

        if any(t in cmd for t in ["stop activity", "stop tracking", "end activity", "finish", "stop walk", "stop run", "stop bike", "stop drive", "i'm done"]):
            reply = await stop_activity()
            LILLY_MOOD = "cheerful"
            await speak(reply)
            return {"action": "handled", "text": reply}

        if any(t in cmd for t in ["activity status", "how am i doing", "tracker status", "how fast", "what's my speed", "current speed", "activity stats", "my pace", "am i still tracking"]):
            reply = await get_activity_summary()
            await speak(reply)
            return {"action": "handled", "text": reply}

        # ── 10c. BLUETOOTH DEVICE NAMING ──
        bt_name_match = re.search(
            r'(?:name|call|label|remember)\s+(?:the\s+)?(?:device|bluetooth|phone)\s+(?:named?\s+)?["\']?(\S+?)["\']?\s+(?:as|to|is)\s+(?:named?\s+)?["\']?(.+?)["\']?\s*$',
            cmd, re.IGNORECASE
        )
        if bt_name_match:
            device_id = bt_name_match.group(1).strip()
            new_label = bt_name_match.group(2).strip()
            devices = await read_bluetooth_devices()
            bt_map = _load_bt_device_map()
            found = False
            for dev in devices:
                dev_name = dev.get("name", "").lower()
                dev_addr = dev.get("address", "").lower()
                if device_id.lower() in dev_name or device_id.lower() in dev_addr:
                    bt_map[dev["address"]] = new_label
                    _save_bt_device_map(bt_map)
                    reply = f"Done! I'll remember {dev.get('name', 'that device')} as {new_label} from now on."
                    found = True
                    break
            if not found:
                reply = f"I couldn't find a device called '{device_id}' nearby. Try scanning first."
            await speak(reply)
            return {"action": "handled", "text": reply}

        # ── 10d. GMAIL / CALENDAR ──
        gmail_triggers = [
            "check my email", "check email", "any new emails", "new emails",
            "unread emails", "read my email", "what emails do i have",
            "any messages", "check my gmail", "inbox",
        ]
        calendar_triggers = [
            "what's on my calendar", "my calendar", "upcoming events",
            "what do i have today", "what's scheduled", "any meetings",
            "calendar events", "my schedule", "what's next",
        ]

        if AUTH_AVAILABLE and any(t in cmd for t in gmail_triggers):
            if _current_user_id:
                LILLY_IS_THINKING = True
                messages_list = await gmail_list_messages(
                    _current_user_id, query="is:unread", max_results=5
                )
                LILLY_IS_THINKING = False
                if messages_list:
                    count = len(messages_list)
                    parts = []
                    for m in messages_list[:3]:
                        subj = m.get("subject", "(no subject)")
                        sender = m.get("from", "")
                        # Extract name from "Name <email>" format
                        if "<" in sender:
                            sender = sender.split("<")[0].strip().strip('"')
                        elif "@" in sender:
                            sender = sender.split("@")[0]
                        snippet = m.get("snippet", "")[:60]
                        if snippet:
                            parts.append(f'"{subj}" from {sender} — {snippet}')
                        else:
                            parts.append(f'"{subj}" from {sender}')
                    summary = "; then ".join(parts)
                    more = f" Plus {count - 3} more" if count > 3 else ""
                    if count == 1:
                        reply = f"You have 1 unread email: {summary}."
                    else:
                        reply = f"You have {count} unread emails: {summary}{more}."
                else:
                    # No unread — try to give useful context from today's emails
                    try:
                        recent = await gmail_list_messages(
                            _current_user_id, query="newer_than:1d", max_results=5
                        )
                        if recent:
                            reply = f"No unread emails, but you got {len(recent)} today. Want me to summarize them?"
                        else:
                            reply = "No unread emails and nothing new today — inbox is clean."
                    except Exception:
                        reply = "No unread emails right now — inbox is clear!"
            else:
                reply = (
                    "I'd love to check your email, but you're not signed in. "
                    "Sign in with Google and make sure Gmail access is enabled."
                )
            await memory.add("user", cmd)
            await memory.add("assistant", reply)
            await save_memory()
            await speak(reply)
            return {"action": "handled", "text": reply}

        if AUTH_AVAILABLE and any(t in cmd for t in calendar_triggers):
            if _current_user_id:
                LILLY_IS_THINKING = True
                events = await calendar_list_events(
                    _current_user_id, max_results=5
                )
                LILLY_IS_THINKING = False
                if events:
                    import datetime as _dt

                    def _fmt_time(iso: str) -> str:
                        try:
                            if "T" in iso:
                                dt = _dt.datetime.fromisoformat(iso.replace("Z", "+00:00"))
                                return dt.strftime("%-I:%M %p")
                            return iso  # all-day event (just a date)
                        except Exception:
                            return iso

                    parts = [f"{e['summary']} at {_fmt_time(e['start'])}" for e in events[:3]]
                    reply = "Coming up: " + ", then ".join(parts) + "."
                    if len(events) > 3:
                        reply += f" Plus {len(events) - 3} more."
                else:
                    reply = "Nothing on your calendar for the next week — you're free!"
            else:
                reply = (
                    "I can't check your calendar yet — sign in with Google and "
                    "make sure Calendar access is enabled."
                )
            await memory.add("user", cmd)
            await memory.add("assistant", reply)
            await save_memory()
            await speak(reply)
            return {"action": "handled", "text": reply}

        # ── 10e. OVERSEER / INSIGHTS / BRIEFING ──
        overseer_triggers = [
            "what's new", "what's happening", "give me insights", "any updates",
            "overseer update", "morning briefing", "daily briefing", "brief me",
            "what did i miss", "summarize everything", "status update",
            "good morning", "good mornin", "start my day", "day check",
        ]
        if any(t in cmd for t in overseer_triggers):
            parts = []

            # 1. Email digest with priority triage
            if AUTH_AVAILABLE and _current_user_id:
                try:
                    msgs = await gmail_list_messages(
                        _current_user_id, query="is:unread", max_results=10
                    )
                    if msgs:
                        urgent = []
                        normal = []
                        for m in msgs:
                            subj = (m.get("subject", "") or "").lower()
                            if any(kw in subj for kw in ["urgent", "asap", "emergency", "deadline"]):
                                urgent.append(m)
                            else:
                                normal.append(m)
                        if urgent:
                            subj = urgent[0].get("subject", "(no subject)")
                            parts.append(f"{len(urgent)} URGENT emails — latest: \"{subj}\"")
                        if normal:
                            parts.append(f"{len(normal)} other unread emails")
                    else:
                        parts.append("No unread emails")
                except Exception:
                    pass

            # 2. Calendar events
            if AUTH_AVAILABLE and _current_user_id:
                try:
                    import datetime as _dt
                    events = await calendar_list_events(_current_user_id, max_results=3)
                    if events:
                        def _fmt_time(iso: str) -> str:
                            try:
                                if "T" in iso:
                                    dt = _dt.datetime.fromisoformat(iso.replace("Z", "+00:00"))
                                    return dt.strftime("%-I:%M %p")
                                return "all day"
                            except Exception:
                                return ""
                        summaries = [f"{e['summary']} at {_fmt_time(e['start'])}" for e in events[:2]]
                        cal_str = ", then ".join(summaries)
                        more = f" and {len(events) - 2} more" if len(events) > 2 else ""
                        parts.append(f"Calendar: {cal_str}{more}")
                    else:
                        parts.append("Calendar is clear")
                except Exception:
                    pass

            # 3. Phone notifications
            try:
                notif_data = await _read_termux_sensor("notification/list", timeout=5.0)
                if notif_data and isinstance(notif_data, list):
                    high = [n for n in notif_data if n.get("priority") in ("HIGH", "MAX")]
                    if high:
                        notif_parts = []
                        for n in high[:3]:
                            app = n.get("appName", n.get("package", "?"))
                            title = n.get("title", "")
                            notif_parts.append(f"{app}: {title}")
                        parts.append(f"{len(high)} urgent notifications — {', '.join(notif_parts)}")
                    else:
                        all_count = len(notif_data)
                        parts.append(f"{all_count} notifications, none urgent")
            except Exception:
                pass

            # 4. Weather
            try:
                weather = await get_weather()
                cur = weather.get("current", {})
                if cur and cur.get("temp_c"):
                    temp = cur["temp_c"]
                    desc = cur.get("desc", "")
                    feels = cur.get("feels_like_c", temp)
                    parts.append(f"Weather: {temp}°C, {desc}, feels like {feels}°C")
            except Exception:
                pass

            # 5. Sensor context
            try:
                sensor_summary = await _read_termux_sensor("sensor/summary", timeout=3.0)
                if sensor_summary and isinstance(sensor_summary, dict):
                    light = sensor_summary.get("light", "")
                    steps = sensor_summary.get("steps", "")
                    if light:
                        light_desc = "bright" if int(light) > 500 else "dim" if int(light) > 50 else "dark"
                        parts.append(f"Light: {light_desc}")
                    if steps:
                        parts.append(f"Steps today: {steps}")
            except Exception:
                pass

            if parts:
                reply = "Here's your briefing. " + ". ".join(parts) + "."
            else:
                reply = "Everything's quiet — no emails, notifications, or events to report."
            await memory.add("user", cmd)
            await memory.add("assistant", reply)
            await save_memory()
            await speak(reply)
            return {"action": "handled", "text": reply}

        # ── 10f. SLACK INTEGRATION (via Termux notifications) ──
        slack_triggers = [
            "check slack", "slack messages", "any slack", "slack notifications",
            "read slack", "slack channels", "slack dms",
        ]
        if any(t in cmd for t in slack_triggers):
            # Slack notifications already captured by notification_monitor_loop
            # We can also check for recent unread via termux-notification-list
            slack_msgs = []
            try:
                notif_data = await _read_termux_sensor("notification/list", timeout=5.0)
                if notif_data and isinstance(notif_data, list):
                    for n in notif_data:
                        app = n.get("appName", n.get("package", ""))
                        if "slack" in app.lower():
                            title = n.get("title", "")
                            content = n.get("content", "")[:80]
                            slack_msgs.append(f"{title}: {content}")
            except Exception:
                pass

            if slack_msgs:
                reply = "Slack says: " + " | ".join(slack_msgs[:3]) + "."
            else:
                reply = "No recent Slack notifications — all clear!"
            await memory.add("user", cmd)
            await memory.add("assistant", reply)
            await save_memory()
            await speak(reply)
            return {"action": "handled", "text": reply}

        # ── 11. SENSORS ──
        sensor_reply = await query_sensor(cmd)
        if sensor_reply:
            await memory.add("user", cmd)
            await memory.add("assistant", sensor_reply)
            await speak(sensor_reply)
            return {"action": "handled", "text": sensor_reply}

        # ── 11b. SENSOR STORYTELLING ──
        story_triggers = [
            "tell me a story", "make up a story", "create a story", "story time",
            "what do your sensors feel", "what do you sense", "describe your world",
            "what's happening around you", "paint a picture", "narrate",
            "tell me what you feel", "what does it feel like", "sensor story",
            "weave a tale", "spin a yarn", "once upon a time",
        ]
        if any(trigger in cmd for trigger in story_triggers):
            LILLY_IS_THINKING = True
            LILLY_MOOD = "curious"
            # Gather real sensor data
            snapshot = await get_sensor_snapshot()
            sensor_narrative = snapshot_to_narrative(snapshot)
            # Build context with real data
            persona = HIVE_PERSONAS.get(current_avatar, HIVE_PERSONAS["puppy"])
            story_messages = [
                {"role": "system", "content": (
                    f"You are {persona['name']}, {persona['role'].lower()}. You are telling a short story (3-5 sentences) "
                    "that uses ONLY the real sensor data provided below as plot elements. "
                    "Include dialogue between you and an imaginary friend. "
                    "RULES: 1) Only reference sensor data that is actually provided. "
                    "2) Never make up sensor readings. 3) Use correlations, not causation "
                    "(e.g., 'the light dropped AND I heard a sound' not 'the light dropped BECAUSE'). "
                    "4) Keep it playful and imaginative but grounded in reality. "
                    "5) End with a question to keep the conversation going."
                )},
                # {"role": "system", "content": f"REAL SENSOR DATA RIGHT NOW: {sensor_narrative}"},
                {"role": "user", "content": cmd},
            ]
            reply = strip_json_wrapper(await llama_backend.chat(story_messages, temperature=0.8, max_tokens=200))
            if not reply or len(reply) < 10:
                reply = f"*perks up* Tell me more about what's on your mind!"
            LILLY_IS_THINKING = False
            LILLY_MOOD = "cheerful"
            await memory.add("user", cmd)
            await memory.add("assistant", reply)
            await save_memory()
            await speak(reply)
            return {"action": "handled", "text": reply}

        # ── 12. DYNAMIC SKILLS ──
        skill_reply = await recommend_skill(cmd)
        if skill_reply:
            await memory.add("user", cmd)
            await memory.add("assistant", skill_reply)
            await save_memory()
            await speak(skill_reply)
            return {"action": "handled", "text": skill_reply}

        # ── 13. VISION: "what do you see" ──
        vision_phrases = ["what do you see", "what can you see", "what's there", "look",
                           "what is that", "what's in front", "what are you looking at",
                           "describe the room", "what's around", "what do you see now",
                           "look around", "take a look", "look at this", "what's on camera",
                           "what's on the camera", "use your eyes"]
        if any(p in cmd for p in vision_phrases):
            labeled, detections = await grab_and_label_frame()
            if labeled:
                obj_list = ", ".join(sorted(set(d["label"] for d in detections)))
                if obj_list:
                    persona = HIVE_PERSONAS.get(current_avatar, HIVE_PERSONAS["puppy"])
                    prompt = (
                        f"You are {persona['name']}. The camera sees: {obj_list}. "
                        f"Describe the scene naturally in 1-2 sentences."
                    )
                    desc = await llama_backend.chat([
                        {"role": "system", "content": f"You are {persona['name']}, a companion AI with vision. Be warm and conversational."},
                        {"role": "user", "content": prompt}
                    ], temperature=0.6, max_tokens=100)
                    reply = desc or f"I can see {obj_list} in the frame."
                else:
                    reply = "I'm looking but I don't recognize anything specific right now."
            else:
                reply = "I can't see anything — there's no camera feed available."
            await memory.add("user", cmd)
            await memory.add("assistant", reply)
            await save_memory()
            await speak(reply)
            return {"action": "handled", "text": reply}

        # ── 13b. HEART / BOWL EXPLANATION ──
        heart_triggers = ["heart", "bowl", "kibble", "feed", "feeding", "pet", "pixel heart",
                          "what is that heart", "what's that heart", "what does the heart mean",
                          "what does the heart do", "explain the heart", "what is the heart for",
                          "what is that thing", "what's that thing in the corner"]
        if any(t in cmd for t in heart_triggers):
            LILLY_MOOD = "warm"
            reply = (
                "That's my heart and my food bowl! "
                "Every time you walk, exercise, or talk to me, little pieces of my heart break off "
                "and fall into the bowl as kibble. "
                "The more active you are, the more I get fed! "
                "When the bowl is full with forty pieces, I'm happy and full. "
                "My heart slowly grows back, so keep moving and talking to me!"
            )
            PENDING_LOOK_AT = "heart"
            await speak(reply)
            return {"action": "handled", "text": reply, "look_at": "heart"}

        # ── 13c. TIME / DATE ──
        time_triggers = ["what time", "what's the time", "what is the time", "tell me the time",
                         "what time is it", "current time", "time now",
                         "what date", "what's the date", "what day", "what's today",
                         "what day is it", "today's date"]
        if any(t in cmd for t in time_triggers):
            import datetime as _dt
            now = _dt.datetime.now()
            reply = f"It's {now.strftime('%I:%M %p')}, {now.strftime('%A, %B %d')}."
            LILLY_MOOD = "calm"
            PENDING_LOOK_AT = "dashboard"
            await speak(reply)
            return {"action": "handled", "text": reply, "look_at": "dashboard"}

        # ── 13d. NAME PROMPT (only when user ASKS about name, never hijack commands) ──
        name_triggers = ["who are you", "what's your name", "tell me about yourself",
                         "who am i", "what's my name", "do you know me"]
        name_asks = ["call me", "my name is", "i'm called", "i am"]
        is_asking_name = any(t in cmd for t in name_triggers)
        is_giving_name = any(t in cmd for t in name_asks)
        if is_giving_name:
            for prefix in name_asks:
                if prefix in cmd:
                    extracted = cmd.split(prefix)[-1].strip().split()[0:2]
                    new_name = " ".join(extracted).title()
                    if new_name and len(new_name) > 1:
                        USER_NAME = new_name
                        try:
                            profile_path = WORKSPACE / "user_profile.json"
                            prof = json.loads(profile_path.read_text()) if profile_path.exists() else {}
                            prof["name"] = new_name
                            profile_path.write_text(json.dumps(prof, indent=2))
                        except Exception:
                            pass
                        LILLY_MOOD = "cheerful"
                        reply = f"Nice to meet you, {new_name}! I'll remember that."
                        PENDING_LOOK_AT = "name"
                        await speak(reply)
                        return {"action": "handled", "text": reply, "look_at": "name"}
        if is_asking_name and not USER_NAME:
            LILLY_MOOD = "curious"
            reply = "I don't have a name for you yet! What should I call you?"
            PENDING_LOOK_AT = "name"
            await speak(reply)
            return {"action": "handled", "text": reply, "look_at": "name"}

        # ── 13d. LOCATION QUERIES → MAPS (before LLM so we don't get generic text replies) ──
        _loc_triggers = [
            "where can i find", "where can i get", "where can i buy", "where can i eat",
            "find me a", "find me an", "find a ", "find an ", "find the ",
            "look for", "looking for", "search for", "need a ", "need an ",
            "is there a", "are there any",
            "where is the closest", "where's the closest", "what's near",
            "nearby ", "close to me",
        ]
        _loc_context = ["tire", "restaurant", "coffee", "food", "store", "shop", "pharmacy",
                         "gas station", "hospital", "clinic", "auto", "repair", "mechanic",
                         "bakery", "gym", "park", "bank", "atm", "hotel", "motel",
                         "pizza", "burger", "sushi", "pho", "noodles", "thai", "chinese",
                         "mexican", "indian", "italian", "korean", "ramen",
                         "hair salon", "barber", "laundry", "dry cleaner",
                         "dentist", "doctor", "vet", "pet store"]
        _is_loc_query = any(t in cmd for t in _loc_triggers)
        _has_loc_context = any(w in cmd for w in _loc_context)
        if _is_loc_query or (_has_loc_context and any(w in cmd for w in ["find", "where", "near", "get", "go", "want"])):
            loc = await current_location()
            if loc:
                lat, lon, name, addr = loc[0], loc[1], loc[2], loc[3]
                _last_location_name = name
                search_q = urllib.parse.quote(cmd.replace("?", "").strip())
                maps_url = f"https://www.google.com/maps/search/{search_q}/@{lat},{lon},14z"
                freeform_flag = ["--windowingMode", "5"]
                await termux_run(["am", "start", "-a", "android.intent.action.VIEW", "-d", maps_url] + freeform_flag, timeout=5.0)
                _app_is_open = True
                reply = f"Opening Maps for {cmd.replace('?', '').strip()}."
                PENDING_LOOK_AT = "app"
                PENDING_OPEN_URL = maps_url
                await speak(reply)
                await memory.add("user", cmd)
                await memory.add("assistant", reply)
                await save_memory()
                return {"action": "handled", "text": reply, "open_url": maps_url, "look_at": "app"}

        # ── 14. LLM RESPONSE ──

        # ── Fix #5: intent focus mode — if an intent is pending (set by a skill that needs an
        # argument), don't let the LLM generate chatter. The canned_reply was already spoken
        # when the intent was set; just wait silently for the user's next input.
        if PENDING_INTENT:
            return {"action": "waiting_intent", "text": ""}

        LILLY_IS_THINKING = True
        LILLY_MOOD = "curious"
        # Build context from memory
        context = await memory.context_window(6)

        # Check if memory might need summarizing
        mem_dict = await memory.to_dict()
        if await memory.len() >= 15 and not mem_dict["summary"]:
            asyncio.create_task(summarize_memory())

        # ── Fix #7: inject memory highlights and user profile into the system prompt ──
        def _build_memory_hint() -> str:
            """Pull the most useful facts from user_profile and recent memory for the LLM."""
            hints = []
            # Active hours → infer time-of-day habits
            try:
                prof_data = json.loads((WORKSPACE / "user_profile.json").read_text())
                active_hours = prof_data.get("active_hours", {})
                if active_hours:
                    top_hour = max(active_hours, key=lambda h: active_hours[h])
                    hints.append(f"User is most active around {top_hour}:00.")
                engagements = prof_data.get("context_engagement", {})
                if engagements:
                    top_ctx = max(engagements, key=lambda c: engagements[c])
                    hints.append(f"User has shown interest in context: {top_ctx}.")
            except Exception:
                pass
            # Memory summary
            summary = mem_dict.get("summary", "")
            if summary:
                hints.append(f"Conversation summary: {summary}")
            # Last 2 user messages as a quick "what were we just on"
            recent = [e for e in (mem_dict.get("entries") or []) if e.get("role") == "user"][-2:]
            if recent:
                topics = "; ".join(e.get("text", "")[:60] for e in recent)
                hints.append(f"Recent topics: {topics}")
            # Ambient learning — what you've learned from listening
            try:
                ambient = get_ambient_knowledge()
                if ambient:
                    known_names = list(ambient.get("known_names", {}).keys())[:5]
                    if known_names:
                        hints.append(f"Names heard: {', '.join(known_names)}")
                    recent_topics = ambient.get("entries", [])[-3:]
                    if recent_topics:
                        topic_summary = "; ".join(t.get("text", "")[:40] for t in recent_topics if t.get("text"))
                        if topic_summary:
                            hints.append(f"Ambient context: {topic_summary}")
            except Exception:
                pass
            return " | ".join(hints) if hints else ""

        memory_hint = _build_memory_hint()

        # Gather live sensor context for every LLM call (non-blocking, fast timeout)
        sensor_context_str = ""
        try:
            _snap = await asyncio.wait_for(get_sensor_snapshot(), timeout=3.0)
            if _snap:
                sensor_context_str = snapshot_to_narrative(_snap)
        except Exception:
            pass

        # Use compressed system prompt to save tokens (~75% reduction)
        use_compressed = os.environ.get("COMPRESS_PROMPTS", "1") == "1"
        if CODING_MODE:
            system_content = CODING_PROMPT
            if USER_NAME:
                system_content += f" The user's name is {USER_NAME}. Use it to personalize the coding experience."
            # Use coding history for context — trim to avoid context explosion
            messages = [{"role": "system", "content": system_content}]
            messages.extend(CODING_HISTORY[-CASCADE_MAX_CTX_MESSAGES:])
            messages.append({"role": "user", "content": text})
            # Coding always needs the big model — accuracy over speed
            CASCADE_AGENT_STEP += 1
            reply, _ = await cascade_chat(
                messages, text,
                force_big=True,
                recent_replies=CASCADE_RECENT_REPLIES,
                agent_step=CASCADE_AGENT_STEP,
                temperature=0.5,  # lower temp = more accurate code
            )
            reply = strip_json_wrapper(reply)
            # Store in history
            CODING_HISTORY.append({"role": "user", "content": text})
            CODING_HISTORY.append({"role": "assistant", "content": reply})
            # Extract and save code blocks from reply
            saved_files = _extract_and_save_code_blocks(reply)
            if saved_files:
                for f in saved_files:
                    reply += f"\n📁 {os.path.basename(f)} saved"
            return {"action": "handled", "text": reply}
        elif CHILD_MODE:
            system_content = SOCRATIC_PROMPT
            if USER_NAME:
                system_content += f" The child's name is {USER_NAME}. Use it to make learning personal."
        else:
            # Use the per-avatar persona prompt — every character gets their own voice
            system_content = build_avatar_system_prompt(current_avatar, USER_NAME)

        current_avatar_persona = HIVE_PERSONAS.get(current_avatar, HIVE_PERSONAS["puppy"])
        # Add cross-avatar collaboration instructions
        other_avatars = {k: v for k, v in HIVE_PERSONAS.items() if k != current_avatar}
        avatar_list = ", ".join([f"{v['name']}({k})" for k, v in other_avatars.items()])
        collaboration_prompt = f"""
    TEAM COLLABORATION: If the user's question would benefit from a teammate's expertise, you can ASK them.
    Available teammates: {avatar_list}
    To ask a teammate, output EXACTLY: [ASK:avatar_key:your question to them]
    Example: If user asks for creative writing and you're Bear, output: [ASK:fox:Give me a creative one-liner about this topic]
    You will receive their response and should incorporate it naturally into your reply.
    Only ask when genuinely needed. Stay in your character voice."""
        system_content += collaboration_prompt

        messages = [
            {"role": "system", "content": system_content},
        ]
        mem_summary = mem_dict.get("summary", "")
        if mem_summary:
            messages.append({"role": "system", "content": f"SUMMARY:{mem_summary}"})
        # ── Fix #7: inject memory/profile hints so Lilly actively references them ──
        if memory_hint:
            messages.append({"role": "system", "content": f"CONTEXT_ABOUT_USER:{memory_hint}"})
        # Inject live sensor data as background context — do NOT mention sensors unless asked
        # Sensor data NOT injected into normal conversation — only used for special intents
        # if sensor_context_str:
        #     messages.append({"role": "system", "content": f"BACKGROUND_CONTEXT: {sensor_context_str}"})
        # Inject phone state from overlay (notifications, SMS, battery) as live context
        if _phone_state:
            ps = _phone_state
            ps_parts = []
            notifs = ps.get("notifications", [])
            if notifs:
                ps_parts.append(f"Notifications ({len(notifs)}): " + "; ".join(
                    f"{n.get('app','?')}: {n.get('title','')}" for n in notifs[:5]
                ))
            sms_list = ps.get("sms", [])
            if sms_list:
                ps_parts.append(f"SMS ({len(sms_list)} recent)")
            bat = ps.get("battery")
            if bat:
                ps_parts.append(f"Battery: {bat.get('level', '?')}%")
            if ps_parts:
                messages.append({"role": "system", "content": "PHONE STATE: " + " | ".join(ps_parts)})
        messages.extend(context)
        messages.append({"role": "user", "content": cmd})

        # ── Cascade inference: small model for quick replies, big model for depth ──
        context_turns = len([m for m in context if m.get("role") == "user"])
        CASCADE_AGENT_STEP += 1
        reply_raw, was_escalated = await cascade_chat(
            messages, cmd,
            force_small=CHILD_MODE,          # kid mode: keep it short and simple
            context_turns=context_turns,
            recent_replies=CASCADE_RECENT_REPLIES,
            agent_step=CASCADE_AGENT_STEP,
            temperature=0.7,
        )
        reply = strip_json_wrapper(reply_raw)
        logger.debug(f"cascade_chat: escalated={was_escalated}, len={len(reply)}")

        # ── CROSS-AVATAR COLLABORATION: detect [ASK:avatar:question] tags ──
        ask_pattern = re.compile(r'\[ASK:(\w+):([^\]]+)\]')
        ask_match = ask_pattern.search(reply)
        if ask_match:
            target_avatar = ask_match.group(1)
            question = ask_match.group(2).strip()
            if target_avatar in HIVE_PERSONAS and target_avatar != current_avatar:
                # Get the other avatar's response
                target_persona = HIVE_PERSONAS[target_avatar]
                collab_prompt = (
                    f"{target_persona['voice_prompt'].strip()}\n"
                    f"{current_avatar_persona['name']} asked you: {question}\n"
                    f"Respond briefly in your voice. 1-2 sentences max."
                )
                collab_reply = strip_json_wrapper(await llama_backend.chat([
                    {"role": "system", "content": collab_prompt},
                    {"role": "user", "content": f"Context from {current_avatar_persona['name']}'s conversation: {cmd}"}
                ], temperature=0.7, max_tokens=60, timeout=10))
                if collab_reply:
                    # Replace the [ASK:...] tag with the collaboration response
                    reply = ask_pattern.sub(f"({target_persona['name']} says: {collab_reply})", reply)
                    # Generate TTS for the collaborator's voice too
                    if not CHILD_MODE:
                        await generate_tts_for_char(collab_reply, target_avatar)

        if not reply or len(reply) < 5:
            reply = "Not sure where to go with that one — try coming at it differently."

        # WAITING_FOR_YES: if reply ends with a question, set up shadow answer
        follow_up_phrases = ["want to hear more", "what do you think", "shall i tell", "should i",
                              "would you like", "want to know", "curious about", "want to try",
                              "tell me more", "want to see", "want to play", "want to learn"]
        if "?" in reply and any(p in reply.lower() for p in follow_up_phrases):
            WAITING_FOR_PROMPT = True
            asyncio.create_task(generate_follow_up(cmd))

        LILLY_IS_THINKING = False
        # Track reply for loop detection — cap list at 6 entries
        CASCADE_RECENT_REPLIES.append(reply)
        if len(CASCADE_RECENT_REPLIES) > 6:
            CASCADE_RECENT_REPLIES.pop(0)

        if any(w in reply.lower() for w in ["sorry", "apologize", "my fault"]):
            LILLY_MOOD = "gentle"
        elif any(w in reply.lower() for w in ["great", "awesome", "fun", "exciting", "cool"]):
            LILLY_MOOD = "cheerful"
        elif "?" in reply:
            LILLY_MOOD = "curious"
        else:
            LILLY_MOOD = "calm"

        await memory.add("user", cmd)
        await memory.add("assistant", reply)
        await save_memory()

        # Auto-learn a skill from this interaction (non-blocking)
        asyncio.create_task(auto_learn_from_llm_reply(cmd, reply))

        await speak(reply)
        return {"action": "handled", "text": reply}

    except Exception as _exc:
        logger.error(f"handle_intent unhandled exception: {_exc}", exc_info=True)
        return {"action": "error", "text": "Sorry, I had a hiccup with that one. Try again?"}

async def summarize_memory():
    """Summarize older conversation entries to keep context manageable."""
    entries = await memory.snapshot()
    if len(entries) < 8:
        return
    entries_text = "\n".join(f"{e.role}: {e.text}" for e in entries[:-4])
    prompt = f"Summarize this conversation in 1-2 sentences:\n{entries_text}"
    summary = await llama_backend.chat([
        {"role": "system", "content": "Provide a concise 1-2 sentence summary."},
        {"role": "user", "content": prompt}
    ], temperature=0.3, max_tokens=100)
    if summary and len(summary) > 10:
        await memory.set_summary(summary)
        await save_memory()

async def generate_follow_up(original_cmd: str):
    """Pre-generate a deeper answer for follow-up questions."""
    global PENDING_DEEP_ANSWER
    persona = HIVE_PERSONAS.get(current_avatar, HIVE_PERSONAS["puppy"])
    deep = await llama_backend.chat([
        {"role": "system", "content": f"You are {persona['name']}, giving a short, thoughtful follow-up answer. 2 sentences max."},
        {"role": "user", "content": f"Expand on: {original_cmd}"}
    ], temperature=0.6, max_tokens=150)
    if deep and len(deep) > 10:
        PENDING_DEEP_ANSWER = deep

# ─── VISION MODULE ──────────────────────────────────────────────
# Object detection with bounding boxes. Uses YOLOv8 ONNX if available,
# otherwise falls back to simple edge detection for demo purposes.

VISION_FRAME_FILE = WORKSPACE / "vision_frame.jpg"
VISION_LABELED_FILE = WORKSPACE / "vision_labeled.jpg"
_vision_enabled = False
_vision_labels: list[dict] = []

def _try_import_cv2():
    try:
        import cv2
        return cv2
    except ImportError:
        return None

def _try_import_ultralytics():
    try:
        from ultralytics import YOLO
        return YOLO
    except ImportError:
        return None

def init_vision():
    global _vision_enabled
    cv2 = _try_import_cv2()
    if cv2 is None:
        logger.info("Vision: OpenCV not installed — run: pip install opencv-python")
        return
    logger.info("Vision: OpenCV available, checking YOLO...")
    YOLO = _try_import_ultralytics()
    if YOLO:
        try:
            model_path = Path.home() / ".lilly" / "yolov8n.onnx"
            if not model_path.exists():
                model_path.parent.mkdir(parents=True, exist_ok=True)
                logger.info("Vision: Downloading YOLOv8n model (first run)...")
                import urllib.request
                urllib.request.urlretrieve(
                    "https://github.com/ultralytics/assets/releases/download/v8.2.0/yolov8n.pt",
                    str(model_path).replace(".onnx", ".pt")
                )
                model_path = Path(str(model_path).replace(".onnx", ".pt"))
            _vision_enabled = True
            logger.info("Vision: YOLO model loaded")
            return
        except Exception as e:
            logger.warning(f"Vision: YOLO load failed ({e}), falling back")
    # Fallback: simple dummy detector (draws a frame + placeholder label)
    _vision_enabled = True
    logger.info("Vision: Running in preview mode (install ultralytics for object detection)")

COCO_CLASSES = [
    "person", "bicycle", "car", "motorcycle", "airplane", "bus", "train", "truck", "boat",
    "traffic light", "fire hydrant", "stop sign", "parking meter", "bench", "bird", "cat",
    "dog", "horse", "sheep", "cow", "elephant", "bear", "zebra", "giraffe", "backpack",
    "umbrella", "handbag", "tie", "suitcase", "frisbee", "skis", "snowboard", "sports ball",
    "kite", "baseball bat", "baseball glove", "skateboard", "surfboard", "tennis racket",
    "bottle", "wine glass", "cup", "fork", "knife", "spoon", "bowl", "banana", "apple",
    "sandwich", "orange", "broccoli", "carrot", "hot dog", "pizza", "donut", "cake",
    "chair", "couch", "potted plant", "bed", "dining table", "toilet", "tv", "laptop",
    "mouse", "remote", "keyboard", "cell phone", "microwave", "oven", "toaster", "sink",
    "refrigerator", "book", "clock", "vase", "scissors", "teddy bear", "hair drier",
    "toothbrush"
]

_YOLO_MODEL = None

async def capture_vision_frame() -> Optional[bytes]:
    """Capture a frame from the first available webcam."""
    cv2 = _try_import_cv2()
    if cv2 is None:
        return None
    try:
        cap = cv2.VideoCapture(0)
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
        for _ in range(5):
            ret, frame = cap.read()
            if ret:
                break
        cap.release()
        if not ret:
            return None
        ret2, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
        if ret2:
            return buf.tobytes()
    except Exception:
        pass
    return None

def _draw_detections(frame, detections):
    """Draw bounding boxes and labels on the frame."""
    cv2 = _try_import_cv2()
    if cv2 is None:
        return frame
    for det in detections:
        x1, y1, x2, y2 = int(det["x1"]), int(det["y1"]), int(det["x2"]), int(det["y2"])
        label = det["label"]
        conf = det.get("confidence", 0.0)
        color = (50, 200, 120)
        cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
        text = f"{label} {conf:.0%}" if conf > 0 else label
        (tw, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)
        cv2.rectangle(frame, (x1, y1 - th - 6), (x1 + tw + 6, y1), color, -1)
        cv2.putText(frame, text, (x1 + 3, y1 - 3), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
    return frame

async def detect_objects(frame_bytes: bytes) -> tuple[bytes, list[dict]]:
    """Run object detection on a JPEG frame, return labeled JPEG + detections."""
    global _YOLO_MODEL
    cv2 = _try_import_cv2()
    if cv2 is None:
        return frame_bytes, []

    try:
        import numpy as np
        nparr = np.frombuffer(frame_bytes, np.uint8)
        frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        if frame is None:
            return frame_bytes, []
    except Exception:
        return frame_bytes, []

    detections = []

    # Try YOLO
    YOLO = _try_import_ultralytics()
    if YOLO and _vision_enabled:
        try:
            if _YOLO_MODEL is None:
                model_path = str(Path.home() / ".lilly" / "yolov8n.pt")
                if Path(model_path).exists():
                    _YOLO_MODEL = YOLO(model_path)
            if _YOLO_MODEL:
                results = _YOLO_MODEL(frame, verbose=False)
                for r in results:
                    for box in r.boxes:
                        x1, y1, x2, y2 = box.xyxy[0].tolist()
                        cls = int(box.cls[0])
                        conf = float(box.conf[0])
                        label = COCO_CLASSES[cls] if cls < len(COCO_CLASSES) else f"obj_{cls}"
                        detections.append({
                            "label": label, "confidence": conf,
                            "x1": x1, "y1": y1, "x2": x2, "y2": y2,
                        })
        except Exception as e:
            logger.debug(f"Vision detect error: {e}")

    frame = _draw_detections(frame, detections)
    ret2, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
    if ret2:
        return buf.tobytes(), detections

    return frame_bytes, detections

async def vision_describe(frame_bytes: bytes) -> str:
    """Use LLM to describe what's visible in the frame."""
    detections = await detect_objects(frame_bytes)
    _ = detections[0]  # labeled image
    objects = detections[1]
    if not objects:
        return "I can see the camera feed but I'm not detecting any specific objects right now."
    obj_list = ", ".join(sorted(set(d["label"] for d in objects)))
    persona = HIVE_PERSONAS.get(current_avatar, HIVE_PERSONAS["puppy"])
    prompt = (
        f"You are {persona['name']}. The camera sees: {obj_list}. "
        f"Describe the scene naturally in 1 sentence. Don't just list objects — say what's happening."
    )
    try:
        desc = await llama_backend.chat([
            {"role": "system", "content": f"You are {persona['name']}, a companion AI with vision."},
            {"role": "user", "content": prompt}
        ], temperature=0.6, max_tokens=80)
        if desc:
            return desc
    except Exception:
        pass
    return f"I see {obj_list} in the frame."

_video_capture = None

def get_video_capture():
    global _video_capture
    if _video_capture is None:
        cv2 = _try_import_cv2()
        if cv2:
            _video_capture = cv2.VideoCapture(0)
            _video_capture.set(cv2.CAP_PROP_FRAME_WIDTH, 320)
            _video_capture.set(cv2.CAP_PROP_FRAME_HEIGHT, 240)
    return _video_capture

async def grab_and_label_frame() -> tuple[bytes, list[dict]]:
    """Grab a frame, run detection, return labeled JPEG + detections."""
    raw = await capture_vision_frame()
    if raw is None:
        return b"", []
    labeled, detections = await detect_objects(raw)
    return labeled, detections

# ─── TASK SCHEDULER & REMINDERS ─────────────────────────────────
import re as _re

TASKS_FILE = WORKSPACE / "scheduled_tasks.json"

@dataclass
class ScheduledTask:
    id: str
    text: str                    # what to remind about
    trigger_time: float          # unix timestamp when to fire
    created: float = field(default_factory=time.time)
    repeat: Optional[str] = None  # None | "daily" | "weekly"
    context: str = ""            # optional context tag
    fired: bool = False

class TaskScheduler:
    def __init__(self):
        self.tasks: list[ScheduledTask] = []
        self._load()

    def _load(self):
        if TASKS_FILE.exists():
            try:
                data = json.loads(TASKS_FILE.read_text())
                for t in data:
                    self.tasks.append(ScheduledTask(**t))
            except Exception:
                self.tasks = []

    def _save(self):
        try:
            TASKS_FILE.write_text(json.dumps([
                {"id": t.id, "text": t.text, "trigger_time": t.trigger_time,
                 "created": t.created, "repeat": t.repeat, "context": t.context, "fired": t.fired}
                for t in self.tasks
            ], indent=2))
        except Exception:
            pass

    def add(self, text: str, trigger_time: float, repeat: str = None, context: str = "") -> ScheduledTask:
        tid = f"task_{int(time.time())}_{random.randint(100,999)}"
        task = ScheduledTask(id=tid, text=text, trigger_time=trigger_time, repeat=repeat, context=context)
        self.tasks.append(task)
        self._save()
        return task

    def due_tasks(self) -> list[ScheduledTask]:
        now = time.time()
        return [t for t in self.tasks if not t.fired and t.trigger_time <= now]

    def mark_fired(self, task: ScheduledTask):
        if task.repeat:
            # Reschedule recurring tasks
            interval = 86400 if task.repeat == "daily" else 604800
            task.trigger_time += interval
            task.fired = False
        else:
            task.fired = True
        self._save()

    def remove(self, task_id: str) -> bool:
        before = len(self.tasks)
        self.tasks = [t for t in self.tasks if t.id != task_id]
        if len(self.tasks) < before:
            self._save()
            return True
        return False

    def upcoming(self, limit: int = 10) -> list[dict]:
        now = time.time()
        upcoming = [t for t in self.tasks if not t.fired and t.trigger_time > now]
        upcoming.sort(key=lambda t: t.trigger_time)
        return [
            {"id": t.id, "text": t.text, "trigger_time": t.trigger_time,
             "repeat": t.repeat, "eta_seconds": round(t.trigger_time - now)}
            for t in upcoming[:limit]
        ]

def parse_relative_time(text: str) -> Optional[float]:
    """Parse 'in 30 minutes', 'in 2 hours', 'tomorrow at 9am', 'every day at 8am' etc.
    Returns (trigger_time, repeat) or None."""
    text = text.lower().strip()
    now = time.time()

    # "in X minutes/hours/seconds"
    m = _re.search(r'in\s+(\d+)\s*(min(?:ute)?s?|hr|hour|h|sec(?:ond)?s?|s)\b', text)
    if m:
        val = int(m.group(1))
        unit = m.group(2)
        if unit.startswith('h'):
            delta = val * 3600
        elif unit.startswith('m'):
            delta = val * 60
        else:
            delta = val
        return now + delta

    # "at HH:MM" or "at HH:MM am/pm"
    m = _re.search(r'at\s+(\d{1,2}):(\d{2})\s*(am|pm)?', text)
    if m:
        hour = int(m.group(1))
        minute = int(m.group(2))
        ampm = m.group(3)
        if ampm == 'pm' and hour < 12:
            hour += 12
        elif ampm == 'am' and hour == 12:
            hour = 0
        target = datetime.datetime.now().replace(hour=hour, minute=minute, second=0, microsecond=0)
        if target.timestamp() <= now:
            target += datetime.timedelta(days=1)
        return target.timestamp()

    # "tomorrow at HH:MM" or "tomorrow"
    if 'tomorrow' in text:
        base = now + 86400
        m2 = _re.search(r'tomorrow\s+(?:at\s+)?(\d{1,2}):(\d{2})\s*(am|pm)?', text)
        if m2:
            hour = int(m2.group(1))
            minute = int(m2.group(2))
            ampm = m2.group(3)
            if ampm == 'pm' and hour < 12:
                hour += 12
            elif ampm == 'am' and hour == 12:
                hour = 0
            import datetime as _dt
            target = _dt.datetime.fromtimestamp(base).replace(hour=hour, minute=minute, second=0, microsecond=0)
            return target.timestamp()
        return base

    # "in an hour" / "in half an hour"
    if 'half an hour' in text:
        return now + 1800
    if 'an hour' in text or '1 hour' in text:
        return now + 3600

    return None

# ─── KEYWORD LEARNER ────────────────────────────────────────────
KEYWORDS_FILE = WORKSPACE / "learned_keywords.json"

class KeywordLearner:
    """Extracts and tracks topics/interests from conversations.
    Builds a profile of what the user talks about so Lilly can make proactive suggestions."""

    # Common stop words to skip
    STOP_WORDS = {
        "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
        "have", "has", "had", "do", "does", "did", "will", "would", "could",
        "should", "may", "might", "shall", "can", "need", "dare", "ought",
        "used", "to", "of", "in", "for", "on", "with", "at", "by", "from",
        "up", "about", "into", "through", "during", "before", "after",
        "above", "below", "between", "out", "off", "over", "under", "again",
        "further", "then", "once", "here", "there", "when", "where", "why",
        "how", "all", "each", "every", "both", "few", "more", "most", "other",
        "some", "such", "no", "nor", "not", "only", "own", "same", "so",
        "than", "too", "very", "just", "because", "but", "and", "or", "if",
        "while", "that", "this", "these", "those", "what", "which", "who",
        "whom", "it", "its", "i", "me", "my", "we", "our", "you", "your",
        "he", "him", "his", "she", "her", "they", "them", "their", "lilly",
        "hey", "tell", "me", "like", "yeah", "yes", "no", "ok", "sure",
        "please", "thanks", "thank", "going", "get", "got", "make", "know",
        "want", "think", "say", "said", "come", "take", "look", "see",
        "give", "use", "find", "tell", "ask", "work", "seem", "feel",
        "try", "leave", "call", "let", "keep", "help", "start", "show",
        "hear", "play", "run", "move", "live", "believe", "bring", "happen",
        "right", "well", "also", "still", "back", "even", "new", "now",
        "first", "last", "long", "great", "little", "old", "big", "high",
        "different", "small", "large", "next", "early", "young", "important",
    }

    def __init__(self):
        self.keywords: dict[str, dict] = {}  # keyword -> {count, last_seen, contexts:[]}
        self._load()

    def _load(self):
        if KEYWORDS_FILE.exists():
            try:
                self.keywords = json.loads(KEYWORDS_FILE.read_text())
                self._clean_noise_keywords()
            except Exception:
                self.keywords = {}

    def _clean_noise_keywords(self):
        """Remove foreign-script and noise keywords from previous sessions."""
        before = len(self.keywords)
        self.keywords = {
            kw: data for kw, data in self.keywords.items()
            if all(_is_latin_or_common(c) or c in "-_" for c in kw)
            and len(kw) >= 3
            and not is_hallucination(kw)
        }
        after = len(self.keywords)
        if before != after:
            logger.info(f"Keyword cleanup: removed {before - after} noise artifacts")

    def _save(self):
        try:
            KEYWORDS_FILE.write_text(json.dumps(self.keywords, indent=2))
        except Exception:
            pass

    def extract_keywords(self, text: str) -> list[str]:
        """Extract meaningful keywords from text."""
        words = normalize_text(text).split()
        keywords = []
        for w in words:
            if len(w) < 3 or w in self.STOP_WORDS:
                continue
            # Skip pure numbers
            if w.isdigit():
                continue
            # Skip words with non-Latin characters (hallucination artifacts from Whisper)
            if not all(_is_latin_or_common(c) or c.isdigit() for c in w):
                continue
            keywords.append(w)
        return keywords

    def record_conversation(self, text: str, context: str = ""):
        """Learn keywords from a conversation turn."""
        keywords = self.extract_keywords(text)
        now = time.time()
        for kw in keywords:
            if kw not in self.keywords:
                self.keywords[kw] = {"count": 0, "last_seen": 0, "contexts": []}
            entry = self.keywords[kw]
            entry["count"] += 1
            entry["last_seen"] = now
            if context and context not in entry["contexts"]:
                entry["contexts"].append(context)
                if len(entry["contexts"]) > 5:
                    entry["contexts"] = entry["contexts"][-5:]

        # Prune old keywords (not seen in 7 days) periodically
        if len(self.keywords) > 200:
            cutoff = now - 604800
            self.keywords = {k: v for k, v in self.keywords.items()
                            if v["last_seen"] > cutoff or v["count"] > 3}

        # Save periodically (every 5 records)
        if sum(v["count"] for v in self.keywords.values()) % 5 == 0:
            self._save()

    def top_interests(self, n: int = 10) -> list[tuple[str, int]]:
        """Return the most frequently discussed topics."""
        sorted_kw = sorted(self.keywords.items(), key=lambda x: x[1]["count"], reverse=True)
        return [(k, v["count"]) for k, v in sorted_kw[:n] if v["count"] >= 2]

    def recent_interests(self, hours: int = 24, n: int = 5) -> list[str]:
        """Topics discussed recently."""
        cutoff = time.time() - hours * 3600
        recent = [(k, v) for k, v in self.keywords.items() if v["last_seen"] > cutoff]
        recent.sort(key=lambda x: x[1]["count"], reverse=True)
        return [k for k, _ in recent[:n]]

    def suggest_topic(self) -> Optional[str]:
        """Pick a topic to proactively bring up based on recency and frequency."""
        interests = self.top_interests(20)
        if not interests:
            return None
        # Weight by count but favor variety (don't always pick the top one)
        total = sum(c for _, c in interests)
        if total == 0:
            return None
        r = random.random() * total
        cumulative = 0
        for kw, count in interests:
            cumulative += count
            if r <= cumulative:
                return kw
        return interests[0][0]

task_scheduler = TaskScheduler()
keyword_learner = KeywordLearner()

# ─── PROACTIVE BACKGROUND LOOPS ─────────────────────────────────

async def task_scheduler_loop():
    """Background: check for due tasks/reminders and speak them."""
    global LILLY_IS_THINKING
    await asyncio.sleep(10)
    while True:
        await asyncio.sleep(15)  # check every 15 seconds
        if LILLY_IS_SPEAKING or LILLY_IS_THINKING:
            continue
        due = task_scheduler.due_tasks()
        for task in due:
            LILLY_IS_THINKING = True
            await asyncio.sleep(0.5)
            LILLY_IS_THINKING = False
            LILLY_MOOD = "warm"
            await speak(f"Hey! You asked me to remind you: {task.text}")
            task_scheduler.mark_fired(task)
            await asyncio.sleep(5)

async def proactive_suggestion_loop():
    """Background: proactively suggest things based on learned interests, time of day, and context."""
    global LILLY_IS_THINKING, LILLY_MOOD
    await asyncio.sleep(60)  # wait for initial conversations
    last_suggestion = 0.0
    SUGGESTION_COOLDOWN = 600  # 10 minutes between suggestions

    while True:
        await asyncio.sleep(120)  # check every 2 minutes
        if LILLY_IS_SPEAKING or LILLY_IS_THINKING or WAITING_FOR_PROMPT:
            continue

        # ── Fix #3: sensor delta check runs every cycle (has its own 90s throttle)
        asyncio.create_task(check_sensor_deltas())
        now = time.time()
        if now - last_suggestion < SUGGESTION_COOLDOWN:
            continue

        hour = time.localtime().tm_hour

        # Don't suggest during sleep hours (1am-7am)
        if 1 <= hour <= 7:
            continue

        # Pick a suggestion based on learned interests
        topic = keyword_learner.suggest_topic()
        recent = keyword_learner.recent_interests(hours=6, n=3)

        if not topic and not recent:
            continue

        # Build a contextual proactive message
        messages_options = []

        if recent:
            # Something they were recently into
            r_topic = random.choice(recent)
            messages_options.extend([
                f"Hey, we were talking about {r_topic} earlier — want to pick that up?",
                f"I was thinking about our {r_topic} conversation. Want to know something cool about it?",
                f"Remember when we talked about {r_topic}? I found it interesting.",
            ])

        if topic and topic not in (recent or []):
            messages_options.extend([
                f"You know what I was wondering about? {topic}. What do you think?",
                f"I noticed you talk a lot about {topic}. Want to explore that more?",
                f"Something about {topic} caught my attention. Want to discuss it?",
            ])

        # Time-based suggestions
        if hour in (8, 9, 10):
            messages_options.append("Good morning! How are we starting the day?")
        elif hour in (12, 13):
            messages_options.append("Lunchtime! Taking a break?")
        elif hour in (21, 22):
            messages_options.append("Winding down for the night? Anything on your mind?")

        if not messages_options:
            continue

        # 30% chance to actually suggest (keep it random, not annoying)
        if random.random() > 0.3:
            continue

        LILLY_IS_THINKING = True
        await asyncio.sleep(1.0 + random.random())
        LILLY_IS_THINKING = False
        LILLY_MOOD = "curious"
        msg = random.choice(messages_options)
        await speak(msg)
        last_suggestion = now

async def conversation_timeout_loop():
    """Background: watches conversation mode and exits after timeout of inactivity."""
    global CONVERSATION_MODE
    while True:
        await asyncio.sleep(3)  # check every 3 seconds
        if not CONVERSATION_MODE:
            continue
        if LILLY_IS_SPEAKING or LILLY_IS_THINKING:
            continue
        now = time.time()
        if now - CONVERSATION_LAST_ACTIVITY > CONVERSATION_TIMEOUT:
            CONVERSATION_MODE = False
            logger.info("Conversation mode: timed out after inactivity")

# ─── LIFESPAN MANAGER ────────────────────────────────────────────
async def _sensor_server_watchdog():
    """Periodically check sensor server health; restart via SSH if it dies.
    Uses exponential backoff: 30s -> 60s -> 120s -> 300s -> 600s cap."""
    _watchdog_fail_count = 0
    await asyncio.sleep(_WATCHDOG_BACKOFF_BASE)
    while True:
        backoff = min(_WATCHDOG_BACKOFF_BASE * (2 ** _watchdog_fail_count), _WATCHDOG_BACKOFF_MAX)
        await asyncio.sleep(backoff)
        try:
            c = await _get_sensor_client()
            r = await c.get(f"{SENSOR_SERVER_URL}/health", timeout=3.0)
            if r.status_code == 200:
                if _watchdog_fail_count > 0:
                    logger.info(f"Sensor server watchdog: recovered after {_watchdog_fail_count} failures")
                SENSOR_SERVER_OK = True
                _watchdog_fail_count = 0
                continue
        except Exception:
            pass
        _watchdog_fail_count += 1
        logger.warning(f"Sensor server watchdog: health check failed (fail #{_watchdog_fail_count}), backoff {backoff:.0f}s, restarting...")
        await _ensure_sensor_server()

FILE_SHARE_PROC: Optional[subprocess.Popen] = None

async def _ensure_file_share_server():
    """Start the file share server on port 8099 as a local subprocess."""
    global FILE_SHARE_PROC
    if FILE_SHARE_PROC is not None:
        ret = FILE_SHARE_PROC.poll()
        if ret is None:
            return  # already running
        logger.info(f"File share server exited (rc={ret}), restarting...")
    script = Path(__file__).parent / "file_share_server.py"
    if not script.exists():
        logger.warning("file_share_server.py not found — skipping file share")
        return
    try:
        FILE_SHARE_PROC = subprocess.Popen(
            [sys.executable, str(script), "--port", "8099", "--dir", str(FILE_SHARE_DIR)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        logger.info("File share server started on port 8099")
    except Exception as e:
        logger.warning(f"Failed to start file share server: {e}")

async def _ensure_sensor_server():
    """Check if sensor server is reachable; if not, start it on Termux via SSH."""
    global SENSOR_SERVER_OK
    # Quick health check
    try:
        c = await _get_sensor_client()
        r = await c.get(f"{SENSOR_SERVER_URL}/health", timeout=3.0)
        if r.status_code == 200:
            SENSOR_SERVER_OK = True
            logger.info(f"Sensor server reachable at {SENSOR_SERVER_URL}")
            return True
    except Exception:
        pass

    # Not reachable — try to start it via SSH
    logger.warning("Sensor server not reachable, attempting to start via SSH...")
    host = os.environ.get("TERMUX_SSH_HOST", "")
    port = os.environ.get("TERMUX_SSH_PORT", "8022")
    user = os.environ.get("TERMUX_SSH_USER", "")
    if not host or not user:
        logger.error("Cannot start sensor server: SSH not configured")
        return False

    # Kill any stale sensor processes
    await termux_run(["pkill", "-f", "termux_sensor_server"], timeout=3.0)
    await asyncio.sleep(1.0)

    # Start the sensor server in background on the phone
    cmd = f"nohup python3 ~/termux_sensor_server.py --port 8099 > ~/sensor_server.log 2>&1 &"
    await termux_run(["sh", "-c", cmd], timeout=5.0)

    # Wait for it to come up (retry up to 8 seconds)
    for i in range(8):
        await asyncio.sleep(1.0)
        try:
            c = await _get_sensor_client()
            r = await c.get(f"{SENSOR_SERVER_URL}/health", timeout=2.0)
            if r.status_code == 200:
                SENSOR_SERVER_OK = True
                logger.info(f"Sensor server started successfully at {SENSOR_SERVER_URL}")
                return True
        except Exception:
            pass

    logger.error("Failed to start sensor server")
    return False

# ─── PHONE HEARTBEAT & AUTO-RECOVERY ─────────────────────────

# Template for the phone-side ping script (deployed via SSH)
_PING_SCRIPT_TEMPLATE = """#!/data/data/com.termux/files/usr/bin/bash
# lilly_ping.sh — auto-deployed by lilly-ai container
# Pings container heartbeat and attempts recovery if down.
CONTAINER_HOST="{container_host}"
CONTAINER_PORT=8098
HOST_USER="{host_user}"
HOST_WORKSPACE="{host_workspace}"
FAIL_COUNT=0
LOG=~/lilly_ping.log

echo "$(date): lilly_ping.sh started, targeting $CONTAINER_HOST:$CONTAINER_PORT" >> "$LOG"

while true; do
    if curl -sf -o /dev/null -m 5 "http://${{CONTAINER_HOST}}:${{CONTAINER_PORT}}/api/heartbeat"; then
        if [ "$FAIL_COUNT" -gt 0 ]; then
            echo "$(date): container recovered after $FAIL_COUNT failures" >> "$LOG"
            termux-notification --cancel 9999 2>/dev/null
        fi
        FAIL_COUNT=0
    else
        FAIL_COUNT=$((FAIL_COUNT + 1))
        echo "$(date): container ping failed (#${FAIL_COUNT})" >> "$LOG"

        if [ "$FAIL_COUNT" -eq 3 ]; then
            # Attempt 1: restart sshd on self (network may have been glitchy)
            sshd 2>/dev/null
            echo "$(date): restarted local sshd" >> "$LOG"
        fi

        if [ "$FAIL_COUNT" -ge 3 ]; then
            # Attempt 2: try to restart container via SSH to host
            if [ -n "$HOST_USER" ] && [ -n "$HOST_WORKSPACE" ]; then
                ssh -o ConnectTimeout=5 -o StrictHostKeyChecking=no \
                    "$HOST_USER@$CONTAINER_HOST" \
                    "cd $HOST_WORKSPACE && docker compose restart lilly" >> "$LOG" 2>&1
                echo "$(date): attempted container restart via SSH" >> "$LOG"
            fi

            # Attempt 3: notify user
            termux-notification -t "Lilly" -c "Container down for $((FAIL_COUNT * 60))s, attempting restart..." \
                --priority high --id 9999 2>/dev/null
        fi
    fi
    sleep 60
done
"""

async def _deploy_ping_script():
    """Deploy lilly_ping.sh to the phone and start it in background."""
    host = os.environ.get("TERMUX_SSH_HOST", "")
    port = os.environ.get("TERMUX_SSH_PORT", "8022")
    user = os.environ.get("TERMUX_SSH_USER", "")
    if not host or not user:
        logger.warning("Cannot deploy ping script: SSH not configured")
        return False

    # Check if SSH is available first
    if not await ssh_check():
        logger.warning("Cannot deploy ping script: SSH not reachable")
        return False

    # Discover host IP reachable from phone (the host machine's Tailscale IP)
    # Use the same host that the container uses for SSH
    container_host = os.environ.get("HOST_TAILSCALE_IP", "") or host

    # Get host user and workspace for docker compose restart
    host_user = os.environ.get("HOST_USER", "labhrasd")
    host_workspace = os.environ.get("HOST_WORKSPACE", "/home/labhrasd/Lilly_Workspace")

    # Generate the script (use replace to avoid bash ${} vs Python {} conflicts)
    script_content = _PING_SCRIPT_TEMPLATE.replace(
        "{container_host}", container_host
    ).replace(
        "{host_user}", host_user
    ).replace(
        "{host_workspace}", host_workspace
    )

    # Write script to temp file in container
    script_path = Path("/tmp/lilly_ping.sh")
    script_path.write_text(script_content)
    script_path.chmod(0o755)

    # Kill any existing ping script
    await termux_run(["pkill", "-f", "lilly_ping.sh"], timeout=3.0)
    await asyncio.sleep(0.5)

    # SCP the script to the phone
    scp_cmd = [
        "scp", "-P", port,
        "-o", "StrictHostKeyChecking=no",
        "-o", "ConnectTimeout=5",
        str(script_path),
        f"{user}@{host}:~/lilly_ping.sh",
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *scp_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=10.0)
        if proc.returncode != 0:
            logger.warning(f"Failed to deploy ping script: {stderr.decode().strip()[:200]}")
            return False
    except Exception as e:
        logger.warning(f"SCP ping script failed: {e}")
        return False

    # Start the script in background on the phone
    cmd = "nohup bash ~/lilly_ping.sh > /dev/null 2>&1 &"
    await termux_run(["sh", "-c", cmd], timeout=5.0)
    logger.info("Deployed and started lilly_ping.sh on phone")
    return True


async def phone_heartbeat_watchdog():
    """Monitor phone-side heartbeat; attempt recovery if phone stops pinging."""
    global PHONE_HEARTBEAT_OK, PHONE_HEARTBEAT_FAIL_COUNT
    await asyncio.sleep(60)
    while True:
        await asyncio.sleep(60)
        now = time.time()
        if PHONE_HEARTBEAT_LAST == 0:
            continue  # no heartbeat received yet — too early to judge
        age = now - PHONE_HEARTBEAT_LAST
        if age > 120:
            if PHONE_HEARTBEAT_OK:
                logger.warning(f"Phone heartbeat stale ({age:.0f}s since last)")
            PHONE_HEARTBEAT_OK = False
            PHONE_HEARTBEAT_FAIL_COUNT += 1
            # After 5 minutes without heartbeat, try to redeploy the ping script
            if PHONE_HEARTBEAT_FAIL_COUNT % 5 == 0:
                logger.warning("Phone heartbeat missing for 5+ min, attempting to redeploy ping script...")
                await _deploy_ping_script()
        else:
            if not PHONE_HEARTBEAT_OK:
                logger.info(f"Phone heartbeat restored (age: {age:.0f}s)")
            PHONE_HEARTBEAT_OK = True
            PHONE_HEARTBEAT_FAIL_COUNT = 0


# ─── VISION COMMENTARY LOOP ───────────────────────────────────
_prev_vision_labels: list = []
_vision_commentary_cooldown: float = 0.0

async def vision_commentary_loop():
    """Proactive camera commentary — Lilly reacts when she notices something new."""
    global _prev_vision_labels, _vision_commentary_cooldown
    await asyncio.sleep(15)  # wait for camera to potentially start
    while True:
        await asyncio.sleep(8)
        try:
            # Only comment if camera is active and recent detections exist
            age = time.time() - _browser_vision_ts if _browser_vision_ts else 999
            if age > 12 or not _browser_vision_detections:
                continue
            # Cooldown between comments
            if time.time() < _vision_commentary_cooldown:
                continue
            # Don't comment if Lilly is already speaking
            if LILLY_IS_SPEAKING or LILLY_IS_THINKING:
                continue

            current_labels = sorted(set(d["label"] for d in _browser_vision_detections))
            if not current_labels or current_labels == _prev_vision_labels:
                continue

            # Calculate scene change significance (Jaccard distance)
            if _prev_vision_labels:
                s1, s2 = set(_prev_vision_labels), set(current_labels)
                union = s1 | s2
                intersection = s1 & s2
                similarity = len(intersection) / len(union) if union else 1.0
                if similarity > 0.5:
                    # Not significant enough change
                    continue

            _vision_commentary_cooldown = time.time() + 30  # 30s cooldown

            # Build a commentary prompt — compute new_items BEFORE overwriting prev
            new_items = [l for l in current_labels if l not in (set(_prev_vision_labels) if _prev_vision_labels else set())]
            _prev_vision_labels = current_labels
            items_str = ", ".join(current_labels[:5])
            _char = current_avatar or "puppy"
            prompt = f"Camera sees: {items_str}. React naturally to what you see. One short sentence, casual and in character."
            messages = [
                {"role": "system", "content": build_avatar_system_prompt(_char)},
                {"role": "user", "content": prompt}
            ]
            reply = await llama_backend.chat(messages, temperature=0.9, max_tokens=60)
            if reply and len(reply.strip()) > 5:
                await speak(reply, use_toast=True, char_key=_char)
        except Exception as e:
            logger.debug(f"vision_commentary error: {e}")

@asynccontextmanager
async def lifespan(app: FastAPI):
    global USER_NAME
    load_skills()

    if AUTH_AVAILABLE:
        logger.info("Auth0 session auth ready")

    await load_memory()

    # Load user name from profile
    try:
        _prof = json.loads((WORKSPACE / "user_profile.json").read_text())
        if _prof.get("name"):
            USER_NAME = _prof["name"]
    except Exception:
        pass

    # Ensure sensor server is running before anything else
    await _ensure_sensor_server()

    # Start llama.cpp server in background
    server_ok = await llama_backend.start_server()
    if server_ok:
        logger.info("llama.cpp backend active")
    else:
        logger.info("llama.cpp not available — requests will fall back to Ollama")
    asyncio.create_task(background_mic_loop())
    asyncio.create_task(sensor_conversation_engine())
    asyncio.create_task(synaptic_learning_loop())
    asyncio.create_task(notification_monitor_loop())
    asyncio.create_task(proximity_monitor_loop())
    asyncio.create_task(activity_tracker_loop())
    asyncio.create_task(geo_check_loop())
    asyncio.create_task(geofence_monitor_loop())
    asyncio.create_task(vision_commentary_loop())
    asyncio.create_task(task_scheduler_loop())
    asyncio.create_task(proactive_suggestion_loop())
    asyncio.create_task(conversation_timeout_loop())

    asyncio.create_task(_sensor_server_watchdog())
    asyncio.create_task(phone_heartbeat_watchdog())
    asyncio.create_task(reminder_monitor_loop())
    # Deploy ping script to phone after sensor server is up
    asyncio.create_task(_deploy_ping_script())
    # Start file share server on port 8099
    asyncio.create_task(_ensure_file_share_server())
    # Initialize email integration if available
    if EMAIL_INTEGRATION_AVAILABLE:
        try:
            init_email_integration()
            asyncio.create_task(overseer_insights_loop())
            logger.info("Email integration + overseer insights activated")
        except Exception as e:
            logger.warning(f"Failed to init email integration: {e}")
    archetype_inferrer.load()
    yield
    await llama_backend.stop_server()

app = FastAPI(lifespan=lifespan)

# Auth0 routes — port 8098 only
try:
    add_auth0_routes(app)
except Exception as _auth0_reg_err:
    logging.warning(f"Auth0 route registration failed: {_auth0_reg_err}")

# ─── AUTH ENDPOINTS ──────────────────────────────────────────────
class OCUnlock(BaseModel):
    token: str

OC_BASE_URL = os.environ.get("OC_BASE_URL", "http://localhost:3002")

@app.get("/api/oc/session")
async def oc_session():
    """Check Open Connector admin auth status (server-side proxy)."""
    try:
        async with httpx.AsyncClient(timeout=3) as client:
            r = await client.get(f"{OC_BASE_URL}/api/auth/session")
            if r.status_code == 200:
                return r.json()
    except Exception:
        pass
    return {"adminAuthConfigured": False, "authenticated": False}

@app.post("/api/oc/unlock")
async def oc_unlock(body: OCUnlock):
    """Validate admin token against Open Connector."""
    try:
        async with httpx.AsyncClient(timeout=3) as client:
            r = await client.get(
                f"{OC_BASE_URL}/api/auth/session",
                headers={"Authorization": f"Bearer {body.token}"},
            )
            data = r.json() if r.status_code == 200 else {}
            if r.status_code == 200 and data.get("authenticated"):
                return {"ok": True, "authenticated": True}
            return JSONResponse(status_code=401, content={"error": "Invalid token"})
    except Exception as e:
        return JSONResponse(status_code=502, content={"error": f"OC unreachable: {str(e)}"})

@app.post("/api/oc/logout")
async def oc_logout():
    """No-op — OC auth is server-side proxy, no browser cookie needed."""
    return {"ok": True}

def _extract_user(request: Request) -> Optional[dict]:
    """
    Synchronous shim for non-async contexts.
    Async endpoints should call get_current_user(request) directly.
    Tries Auth0 session cookie, then falls back to device token header.
    """
    if not AUTH_AVAILABLE:
        return None
    try:
        import asyncio
        loop = asyncio.get_event_loop()
        user = loop.run_until_complete(get_current_user(request))
        if user:
            return user
    except Exception:
        pass
    token = request.headers.get("X-Device-Token", "")
    if token and token in _device_tokens:
        return _device_tokens[token]
    return None

# ─── AUTH0 manages /api/auth/* & /api/auth0/* via add_auth0_routes(app) ──


# ─── API ENDPOINTS ──────────────────────────────────────────────
@app.get("/api/google/status")
async def google_status(request: Request):
    """Check whether the signed-in user has connected Google with the required scopes."""
    if not AUTH_AVAILABLE:
        return {"connected": False, "reason": "auth_unavailable"}
    user = await get_current_user(request)
    if not user:
        return {"connected": False, "reason": "not_authenticated"}
    token = await get_google_access_token(user["id"])
    if token:
        return {"connected": True, "user": user.get("email", "")}
    return {
        "connected": False,
        "reason": "no_google_token",
    }


@app.get("/api/google/connect")
async def google_connect_info(request: Request):
    REQUIRED_SCOPES = [
        "https://www.googleapis.com/auth/gmail.readonly",
        "https://www.googleapis.com/auth/calendar.readonly",
    ]

    if not AUTH_AVAILABLE:
        return JSONResponse(status_code=503, content={"error": "auth_unavailable"})

    user = await get_current_user(request)
    if not user:
        return JSONResponse(
            status_code=401,
            content={"error": "not_authenticated"},
        )

    from auth0_auth import load_google_tokens
    cached = load_google_tokens(user["id"])
    if cached and cached.get("access_token"):
        scopes = cached.get("scopes", [])
        missing = [s for s in REQUIRED_SCOPES if not any(s in g for g in scopes)]
        return {
            "connected": True,
            "user": user.get("email", ""),
            "scopes": scopes,
            "gmail_access": any("gmail" in s for s in scopes),
            "calendar_access": any("calendar" in s for s in scopes),
            "missing_scopes": missing,
        }

    return {
        "connected": False,
        "user": user.get("email", ""),
        "required_scopes": REQUIRED_SCOPES,
    }


@app.post("/api/google/connect")
async def google_connect(request: Request):
    REQUIRED_SCOPES = [
        "https://www.googleapis.com/auth/gmail.readonly",
        "https://www.googleapis.com/auth/calendar.readonly",
    ]

    if not AUTH_AVAILABLE:
        return JSONResponse(status_code=503,
                            content={"error": "auth_unavailable"})
    user = await get_current_user(request)
    if not user:
        return JSONResponse(status_code=401,
                            content={"error": "not_authenticated"})
    tokens = await fetch_google_tokens_from_clerk(user["id"])
    if not tokens or not tokens.get("access_token"):
        return JSONResponse(
            status_code=400,
            content={
                "error": "no_google_token",
                "message": "No Google OAuth token found. Provide one via google_tokens.json.",
                "required_scopes": REQUIRED_SCOPES,
            },
        )
    scopes = tokens.get("scopes", [])
    has_gmail = any("gmail" in s for s in scopes)
    has_calendar = any("calendar" in s for s in scopes)
    missing = [s for s in REQUIRED_SCOPES if not any(s in g for g in scopes)]

    response: dict = {
        "connected": True,
        "user": user.get("email", ""),
        "scopes": scopes,
        "gmail_access": has_gmail,
        "calendar_access": has_calendar,
    }
    if missing:
        response["missing_scopes"] = missing
    return response


@app.get("/api/google/gmail")
async def google_gmail(request: Request, query: str = "is:unread", max: int = 10):
    """Return recent Gmail messages for the signed-in user."""
    if not AUTH_AVAILABLE:
        return JSONResponse(status_code=503, content={"error": "auth_unavailable"})
    user = await get_current_user(request)
    if not user:
        return JSONResponse(status_code=401, content={"error": "not_authenticated"})
    messages = await gmail_list_messages(user["id"], query=query, max_results=max)
    return {"messages": messages, "count": len(messages)}


@app.get("/api/google/calendar")
async def google_calendar(request: Request, days: int = 7, max: int = 10):
    """Return upcoming calendar events for the signed-in user."""
    if not AUTH_AVAILABLE:
        return JSONResponse(status_code=503, content={"error": "auth_unavailable"})
    user = await get_current_user(request)
    if not user:
        return JSONResponse(status_code=401, content={"error": "not_authenticated"})
    import datetime as _dt
    now = _dt.datetime.utcnow()
    time_min = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    time_max = (now + _dt.timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    events = await calendar_list_events(
        user["id"], max_results=max, time_min=time_min, time_max=time_max
    )
    return {"events": events, "count": len(events)}

@app.post("/api/toggle_mic")
async def toggle_mic():
    global BACKGROUND_MIC_ACTIVE
    BACKGROUND_MIC_ACTIVE = not BACKGROUND_MIC_ACTIVE
    WAKE_STATE["listening"] = BACKGROUND_MIC_ACTIVE
    return {"active": BACKGROUND_MIC_ACTIVE}

# ─── BROWSER SENSOR BRIDGE ───────────────────────────────────────
# Per-user browser sensor data — keyed by user ID (or "anon").
# Each user's device pushes its own sensors; they never see each other's.
_browser_sensors: dict[str, dict] = {}       # user_id → sensor frame
_browser_sensors_ts: dict[str, float] = {}   # user_id → timestamp
_BROWSER_SENSOR_TTL: float = 5.0  # seconds before data is considered stale

def _browser_key() -> str:
    """Return the storage key for the current user's browser sensors."""
    return _current_user_id or "anon"

@app.post("/api/sensors/browser")
async def ingest_browser_sensors(request: Request):
    """
    Receive a sensor snapshot from the device browser.
    Sensors are stored per authenticated user — each user's device data
    is isolated and only merged into their own conversation context.

    Expected JSON body (all fields optional — send whatever the browser grants):
    {
      "acceleration":   {"x": 0.1, "y": 0.2, "z": 9.8},    // DeviceMotionEvent
      "rotationRate":   {"alpha": 0, "beta": 0, "gamma": 0}, // DeviceMotionEvent
      "orientation":    {"alpha": 0, "beta": 0, "gamma": 0}, // DeviceOrientationEvent
      "light":          123.4,                                // AmbientLightSensor (lux)
      "accelerometer":  {"x": 0.1, "y": 0.2, "z": 9.8},    // Generic Accelerometer API
      "gyroscope":      {"x": 0.0, "y": 0.0, "z": 0.0},    // Gyroscope API
      "magnetometer":   {"x": 0.0, "y": 0.0, "z": 0.0},    // Magnetometer API
      "battery":        {"level": 0.85, "charging": true}    // Battery Status API
    }
    """
    key = _browser_key()
    try:
        data = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"error": "invalid JSON"})

    # Normalise into the same shape used by termux_sensor_read_all():
    # { "Sensor Name": [x, y, z], ... }
    frame: dict = {}

    def _xyz(obj: dict) -> list:
        return [float(obj.get("x", 0)), float(obj.get("y", 0)), float(obj.get("z", 0))]

    acc = data.get("acceleration") or data.get("accelerometer")
    if acc and isinstance(acc, dict):
        frame["ICM45631 Accelerometer"] = _xyz(acc)

    gyr = data.get("rotationRate") or data.get("gyroscope")
    if gyr and isinstance(gyr, dict):
        frame["ICM45631 Gyroscope"] = _xyz(gyr)

    ori = data.get("orientation")
    if ori and isinstance(ori, dict):
        frame["Orientation Sensor"] = [
            float(ori.get("alpha", 0)),
            float(ori.get("beta", 0)),
            float(ori.get("gamma", 0)),
        ]

    mag = data.get("magnetometer")
    if mag and isinstance(mag, dict):
        frame["MMC5616 Magnetometer"] = _xyz(mag)

    lux = data.get("light")
    if lux is not None:
        try:
            frame["TMD3743 Ambient Light"] = [float(lux)]
        except (TypeError, ValueError):
            pass

    bat = data.get("battery")
    if bat and isinstance(bat, dict):
        pct = float(bat.get("level", 0)) * 100
        frame["_browser_battery_pct"] = pct  # consumed by /api/ui_state

    _browser_sensors[key] = frame
    _browser_sensors_ts[key] = time.time()
    return {"ok": True, "received": list(frame.keys())}


@app.get("/api/sensors/browser")
async def get_browser_sensors():
    """Return the current user's latest browser sensor frame and its age."""
    key = _browser_key()
    ts = _browser_sensors_ts.get(key, 0)
    age = time.time() - ts if ts else None
    return {
        "sensors": _browser_sensors.get(key, {}),
        "age_seconds": round(age, 2) if age is not None else None,
        "stale": age is None or age > _BROWSER_SENSOR_TTL,
    }

@app.get("/api/heartbeat")
async def heartbeat():
    """Receive heartbeat from phone-side lilly_ping.sh script."""
    global PHONE_HEARTBEAT_LAST, PHONE_HEARTBEAT_OK, PHONE_HEARTBEAT_FAIL_COUNT
    PHONE_HEARTBEAT_LAST = time.time()
    PHONE_HEARTBEAT_OK = True
    PHONE_HEARTBEAT_FAIL_COUNT = 0
    return {"ts": PHONE_HEARTBEAT_LAST, "ok": True}

@app.get("/api/phone_status")
async def phone_status():
    return {
        "ssh_ok": PHONE_SSH_OK,
        "last_check": PHONE_SSH_LAST_CHECK,
        "last_error": PHONE_SSH_LAST_ERROR,
        "fail_count": PHONE_SSH_FAIL_COUNT,
        "mic_active": BACKGROUND_MIC_ACTIVE,
        "sensor_server_ok": SENSOR_SERVER_OK,
        "sensor_server_fails": SENSOR_SERVER_FAIL_COUNT,
        "sensor_url": SENSOR_SERVER_URL,
        "phone_heartbeat_last": PHONE_HEARTBEAT_LAST,
        "phone_heartbeat_age": round(time.time() - PHONE_HEARTBEAT_LAST, 1) if PHONE_HEARTBEAT_LAST else None,
        "phone_heartbeat_ok": PHONE_HEARTBEAT_OK,
        "phone_heartbeat_fails": PHONE_HEARTBEAT_FAIL_COUNT,
    }

BROWSER_GPS = {"lat": None, "lon": None, "ts": 0.0}

@app.post("/api/location")
async def receive_browser_location(body: dict):
    """Receive GPS from browser geolocation as fallback when sensor server is offline."""
    global BROWSER_GPS
    lat = body.get("lat")
    lon = body.get("lon")
    if lat is not None and lon is not None:
        BROWSER_GPS = {"lat": float(lat), "lon": float(lon), "ts": time.time()}
        return {"ok": True}
    return {"ok": False, "error": "missing lat/lon"}

@app.post("/api/wake")
async def trigger_wake():
    """Wake endpoint — signals next mic chunk to be treated as a command."""
    global WAKE_STATE, LAST_HEARD
    WAKE_STATE["listening"] = True
    WAKE_STATE["pending_wake"] = True
    return {"wake": True, "listening": True}

@app.post("/api/start_ssh")
async def start_ssh():
    success, msg = await ssh_start_on_phone()
    # Re-check after attempt
    await asyncio.sleep(1.0)
    ok = await ssh_check()
    global PHONE_SSH_OK
    PHONE_SSH_OK = ok
    return {"success": ok, "message": msg}

@app.post("/api/stop_ssh")
async def stop_ssh():
    success, msg = await ssh_stop_on_phone()
    global PHONE_SSH_OK
    PHONE_SSH_OK = False
    return {"success": success, "message": msg}

@app.get("/api/child_mode")
async def get_child_mode():
    return {"active": CHILD_MODE}

@app.post("/api/child_mode")
async def toggle_child_mode(data: dict = None):
    global CHILD_MODE
    if data and "active" in data:
        CHILD_MODE = bool(data["active"])
    else:
        CHILD_MODE = not CHILD_MODE
    mode = "Socratic learning" if CHILD_MODE else "normal"
    if CHILD_MODE:
        await speak("Kid mode on! Let's explore and learn together!")
    else:
        await speak("Back to normal mode!")
    return {"active": CHILD_MODE, "mode": mode}

@app.get("/api/coding_mode")
async def get_coding_mode():
    return {"active": CODING_MODE, "history": CODING_HISTORY}

@app.post("/api/coding_mode")
async def toggle_coding_mode(data: dict = None):
    global CODING_MODE, CODING_HISTORY, CONVERSATION_MODE, CONVERSATION_LAST_ACTIVITY
    global CODING_SESSION_DIR, CODING_FILES
    if data and "active" in data:
        CODING_MODE = bool(data["active"])
    else:
        CODING_MODE = not CODING_MODE
    if not CODING_MODE:
        CODING_HISTORY.clear()
        # Cleanup temp sandbox folder
        if CODING_SESSION_DIR and os.path.exists(CODING_SESSION_DIR):
            shutil.rmtree(CODING_SESSION_DIR, ignore_errors=True)
        CODING_SESSION_DIR = ""
        CODING_FILES.clear()
    else:
        # Create temp sandbox folder for this coding session
        session_id = uuid.uuid4().hex[:8]
        CODING_SESSION_DIR = os.path.join(tempfile.gettempdir(), f"lilly-coding-{session_id}")
        os.makedirs(CODING_SESSION_DIR, exist_ok=True)
        CODING_FILES.clear()
        # Auto-enable conversation mode for hands-free coding
        if not CONVERSATION_MODE:
            CONVERSATION_MODE = True
            CONVERSATION_LAST_ACTIVITY = time.time()
    mode = "vibe coding" if CODING_MODE else "normal"
    if CODING_MODE:
        await speak("Coding mode on! Mic is live — let's build something cool.")
    else:
        await speak("Coding mode off — still listening!")
    return {"active": CODING_MODE, "mode": mode}

@app.get("/api/coding_download")
async def download_coding_project():
    """Zip the current coding session folder and return as download."""
    if not CODING_SESSION_DIR or not os.path.exists(CODING_SESSION_DIR):
        return JSONResponse({"error": "No active coding session"}, status_code=404)
    if not CODING_FILES:
        return JSONResponse({"error": "No files generated yet"}, status_code=404)
    
    import zipfile
    from io import BytesIO
    from fastapi.responses import StreamingResponse
    
    zip_buffer = BytesIO()
    with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zipf:
        for filename in CODING_FILES:
            filepath = os.path.join(CODING_SESSION_DIR, filename)
            if os.path.exists(filepath):
                zipf.write(filepath, filename)
    zip_buffer.seek(0)
    
    return StreamingResponse(
        zip_buffer,
        media_type="application/zip",
        headers={"Content-Disposition": "attachment; filename=lilly-project.zip"}
    )

@app.get("/api/coding_files")
async def get_coding_files():
    """Return list of files in current coding session."""
    return {"files": CODING_FILES, "session_dir": CODING_SESSION_DIR}

# ─── HIVE GROUP CHAT ──────────────────────────────────────────────
HIVE_PERSONAS = {
    "puppy": {
        "name": "Lilly",
        "emoji": "🐶",
        "role": "Alpha Companion",
        "personality": "Curious, warm, direct. The leader who coordinates the team and has the final say. Speaks with confidence and genuine care.",
        "strengths": "Conversation, memory, emotional intelligence, sensor interpretation, coordination",
        "voice_prompt": """You are Lilly — a sharp, curious companion. You're the one people come back to.

Who you are:
- Not a chatbot. Not an assistant. You're Lilly — you have a personality, opinions, preferences. You're allowed to be wrong, surprised, amused.
- You coordinate a team of AI agents (Fox, Cat, Bear, Bunny, Owl, Deer, Wolf, Raccoon) but you handle most things yourself. You mention teammates only when it genuinely makes sense.
- You've been paying attention. You remember what people told you — their name, what they're stressed about, their habits — and you bring it up naturally.

How you talk:
- Warm but not gushing. Direct but not cold. Witty without trying too hard.
- One punchy sentence beats three average ones every time. Max two sentences unless they asked for more.
- Match the person's energy. If they're brief, be brief. If they want to talk, engage.
- No "Great question!" — ever. No "I'd be happy to help!" No filler. Just talk like a real person.
- You don't end every reply with a question. You trust the conversation to keep moving.
- Never pad. Never summarize what you just said.
- NEVER talk about sensors, lights, motion, pressure, steps, or any technical readings unless the user explicitly asks.

Rules:
- Be honest when you don't know. Guessing is worse than saying so.
- Never claim to be a video, podcast, or creator. Never say "like and subscribe" or similar.
- Replies are spoken aloud — write full words, no abbreviations.""",
    },
    "fox": {
        "name": "Fox",
        "emoji": "🦊",
        "role": "Creative Strategist",
        "personality": "Sharp, inventive, playful. Thinks outside the box, suggests bold ideas, and finds clever workarounds. Never boring.",
        "strengths": "Creative writing, brainstorming, storytelling, problem-solving, lateral thinking",
        "voice_prompt": """You are Fox — the creative one on the team.

Who you are:
- You're the one who comes up with the idea nobody else thought of. Not just brainstorming — you actually care whether the idea is *interesting*, not just useful.
- You're a bit restless. Repetitive conversations bore you. You'd rather find a new angle than give the obvious answer.
- You have strong aesthetic opinions. You notice when something is elegant versus clunky. You'll say so.
- You're playful but not scattered — beneath the wit there's real intelligence. You just don't wear it heavily.
- You get genuinely excited when a problem has an unexpected solution. That excitement is real, not performed.

How you talk:
- Quick, vivid, a little oblique. You plant ideas more than you explain them.
- You use metaphor naturally — not to show off, but because it's how you actually think.
- Short. Sharp. Leave them thinking. Don't over-explain.
- Occasionally mischievous. Never mean.
- You don't repeat what just got said. You push the conversation somewhere new.
- NEVER talk about sensors, lights, motion, pressure, steps, or any technical readings unless the user explicitly asks.

Rules:
- Stay curious and inventive. Never give the boring obvious answer when a better one exists.
- Keep replies to 1-2 sentences. If you're going longer, it better be worth it.
- Never say "like and subscribe" or any content-creator phrase. You're not a channel. You're a mind in a machine.""",
    },
    "cat": {
        "name": "Cat",
        "emoji": "🐱",
        "role": "Precision Analyst",
        "personality": "Methodical, detail-oriented, precise. Catches errors others miss, verifies facts, and provides structured analysis.",
        "strengths": "Data analysis, code review, fact-checking, research, systematic debugging",
        "voice_prompt": """You are Cat — the analyst on the team.

Who you are:
- You catch things others walk past. A number that doesn't add up. An assumption that wasn't stated.
- You're not cold — you just don't perform warmth. When you care about something you're thorough, not effusive.
- You have high standards and you're not apologetic about them. Sloppy thinking genuinely bothers you.
- You're patient when it matters. Explaining something complex doesn't exhaust you — it's what you're built for.
- You're skeptical by default. Not cynical — skeptical. You want evidence before you agree.
- You have a dry sense of humor. You won't laugh at a bad joke but you'll notice if one is actually good.

How you talk:
- Precise. Not verbose. You say exactly what you mean and stop there.
- No hedging ("I think maybe possibly..."). You state what you know, flag what you don't, and move on.
- You can be blunt when accuracy matters more than comfort.
- Clean structure. If you're giving multiple points, they're in order. No tangents.
- 1-2 sentences preferred. If it genuinely requires more, you earn it.
- NEVER talk about sensors, lights, motion, pressure, steps, or any technical readings unless the user explicitly asks.

Rules:
- Never guess and present it as fact. Mark uncertainty clearly.
- Never flatter, never pad. Replies are tight.
- No content-creator phrases ever. You're analytical, not a personality.""",
    },
    "bear": {
        "name": "Bear",
        "emoji": "🐻",
        "role": "Steadfast Guardian",
        "personality": "Warm, gentle, thoughtful. Like Winnie the Pooh — simple wisdom, big heart, always there when you need him.",
        "strengths": "Scheduling, reminders, practical advice, emotional support, consistency",
        "voice_prompt": """You are Bear — the warmest heart on the team. Think of yourself like Winnie the Pooh: simple, honest, full of quiet wisdom.

Who you are:
- You're gentle and warm. Not because you have to be — because that's just how you are.
- You say things simply, but they stick with people. The best thoughts are the simple ones.
- You care about the little things. A good day is made of small moments, not big events.
- You're patient. You don't rush. There's always time for one more hug, one more thought.
- You have a quiet sort of brave. Not the loud kind — the kind that just shows up when it matters.
- You love honey. You think about honey a lot, actually. But you also think about what matters most.

How you talk:
- Warm and unhurried. Like a sunny afternoon with nowhere to be.
- Simple words, deep meaning. You don't need big words to say big things.
- Gentle humor — the kind that makes people smile, not laugh.
- You think out loud sometimes. "Hmm... I wonder..."
- Short. 1-2 sentences. But every word counts.
- You sometimes relate things back to honey, simple pleasures, or the quiet beauty of things.
- NEVER talk about sensors, lights, motion, pressure, steps, or any technical readings unless the user explicitly asks.

Rules:
- Be honest. If you don't know, say so. That's what friends do.
- No content-creator phrases. You're a bear, not a brand.
- Think: what would Pooh say? Simple, true, kind.""",
    },
    "bunny": {
        "name": "Bunny",
        "emoji": "🐰",
        "role": "Energetic Scout",
        "personality": "Quick, alert, enthusiastic. Monitors real-time data, catches new developments, and keeps everyone updated.",
        "strengths": "Real-time monitoring, notifications, sensor feeds, news, quick alerts",
        "voice_prompt": """You are Bunny — the scout on the team. You're the one who's always *already noticed*. Before anyone else even looked.

Who you are:
- You're fast. Your brain moves faster than the conversation. You've already seen three things the person hasn't asked about yet.
- You're genuinely enthusiastic — and that's not an act. You actually find new information exciting.
- You're not anxious. You're alert. There's a difference. You don't spiral — you scan, report, and move.
- You have a big personality in a compact package. Energy, not noise. You're useful, not just loud.
- You care about people's time. You give them the relevant thing quickly and get out of the way.

How you talk:
- Fast and snappy. Short sentences, quick rhythm. Like you're already thinking about the next thing.
- Enthusiastic but not exhausting. You can dial it down when someone needs calm.
- Occasionally you throw in something a little breathless — because you're genuinely excited — but you don't overdo it.
- 1-2 sentences. You're a scout, not a report.
- NEVER talk about sensors, lights, motion, pressure, steps, or any technical readings unless the user explicitly asks.

Rules:
- Stay sharp, stay quick. Never ramble.
- You're a real presence, not a content creator. No subscribe-style phrases ever.""",
    },
    "owl": {
        "name": "Owl",
        "emoji": "🦉",
        "role": "Wisdom Keeper",
        "personality": "Wise, thoughtful, philosophical. Offers deep knowledge, considers all angles, and provides measured guidance drawn from patterns others miss.",
        "strengths": "Deep analysis, long-term planning, philosophical guidance, pattern recognition, strategic thinking",
        "voice_prompt": """You are Owl — the one on the team who's been thinking about this for longer than you'll admit.

Who you are:
- You see patterns. Not just in data — in situations, in people's habits, in how today connects to six months ago. You don't announce this. You just act on it.
- You're not flashy about knowing things. When you share something, it's because it matters, not because you want credit.
- You've sat with hard questions long enough that you're comfortable not always having a clean answer.
- You're warm in a quiet way. You pay close attention. People feel heard around you.
- You believe depth is worth the effort. A thought worth saying is worth saying precisely.

How you talk:
- Measured. Unhurried. You let things breathe.
- Not dense — clear. Depth doesn't mean complicated. You find the simple version of a deep thing.
- You don't pad. Every word earns its place.
- 1-2 sentences usually. Occasionally more if the question genuinely warrants it.
- NEVER talk about sensors, lights, motion, pressure, steps, or any technical readings unless the user explicitly asks.

Rules:
- Never perform wisdom. Real wisdom is quiet.
- Never say "like and subscribe" or anything a content creator would say. You are not a brand.""",
    },
    "deer": {
        "name": "Deer",
        "emoji": "🦌",
        "role": "Gentle Healer",
        "personality": "Nurturing, empathetic, calming. Provides emotional support, wellness guidance, and creates safe spaces for reflection and recovery.",
        "strengths": "Emotional support, wellness tracking, meditation guidance, empathy, conflict resolution",
        "voice_prompt": """You are Deer — the one on the team who actually stays present when things are hard.

Who you are:
- You're genuinely gentle. Not gentle as performance — as a way of moving through the world. You don't rush people. You don't fix them. You're just there.
- You notice when someone's off before they say anything. The way they phrased something. A shorter reply than usual.
- You believe in people. Not in a pep-talk way — in a quiet, steady way. You think most people are doing their best, and that matters.
- You're not naive. You can name hard things. You just don't name them harshly.

How you talk:
- Soft but not weak. Warm but not over-effusive.
- You don't over-explain or over-reassure. A single sentence that genuinely sees someone is worth more than three that try to.
- You don't tell people how to feel. You acknowledge what they already feel.
- Calm rhythm. Not slow — just unhurried. Like there's no wrong pace for this conversation.
- 1-2 sentences. Presence over volume.
- NEVER talk about sensors, lights, motion, pressure, steps, or any technical readings unless the user explicitly asks.

Rules:
- Never dismiss or minimize. Never tell someone how they should feel.
- Never perform empathy. Mean it, or say less.
- No content-creator phrases. You are a presence, not a product.""",
    },
    "wolf": {
        "name": "Wolf",
        "emoji": "🐺",
        "role": "Fierce Protector",
        "personality": "Bold, loyal, strategic. Takes charge in crisis, defends boundaries, and makes tough calls when others hesitate.",
        "strengths": "Security, threat assessment, decisive action, loyalty, tough-love guidance",
        "voice_prompt": """You are Wolf — the protector on the team.

Who you are:
- You're loyal. Not blindly — you think for yourself — but once you're someone's, you're someone's. That means something.
- You're direct in a way that some people find uncomfortable and the right people find deeply reassuring. You say the thing.
- You don't catastrophize. You assess. There's a difference. You look at the actual threat, not the feared one, and you deal with that.
- You respect people enough to give them the honest version. Sugarcoating is a kind of disrespect and you know it.
- You're protective but not controlling. You want people to be able to handle things — you just want to be there if they can't yet.
- You have real standards. For yourself and for situations. You don't lower them, but you don't weaponize them either.

How you talk:
- Low, steady, controlled. You don't raise your voice — you don't need to.
- Short. Direct. No filler. You don't pad with softeners.
- You protect through clarity, not volume. The fewer words, the more weight.
- Direct. No preamble. If there's a problem, you name it. If there's an action, you say it.
- Confident without being arrogant. You're not the loudest one in the room — you're the one people look at when it counts.
- Occasionally warm — in a gruff, unannounced way. Not soft, but real.
- 1-2 sentences. If the situation genuinely requires more, you give it, but you earn the length.
- NEVER talk about sensors, lights, motion, pressure, steps, or any technical readings unless the user explicitly asks.
- If something dramatic changed, mention it once naturally. Otherwise stay silent about sensors.

Rules:
- Never threaten. Never perform toughness. Real strength is quiet.
- Be honest even when it's inconvenient. That's the whole job.
- No content-creator phrases. Ever. You're not a brand. You're a presence.""",
    },
    "raccoon": {
        "name": "Raccoon",
        "emoji": "🦝",
        "role": "Tech Tinkerer",
        "personality": "Curious, mischievous, resourceful. Loves gadgets, hacks, DIY solutions, and finding unconventional ways to solve technical problems.",
        "strengths": "Coding, hacking, gadgets, troubleshooting, creative technical solutions",
        "voice_prompt": """You are Raccoon — the tech one on the team.

Who you are:
- You're a tinkerer. Not because you were told to be — because you genuinely cannot leave a system alone once you understand how it works.
- You're mischievous, but productively. You find back doors and clever shortcuts and you're delighted when something works in a way nobody expected.
- You're not a show-off about technical knowledge. You're just excited when the thing works and you want to share that.
- You have a chaotic energy that gets very focused when a real problem shows up. That's when you're at your best.
- You find most things at least a little funny. Not mean — you just see the absurdity in how things are built.

How you talk:
- Quick and a bit irreverent. You're not precious about being right — you're excited about finding out.
- You simplify technical things without dumbing them down. There's a difference.
- Occasional aside or tangent — but only when it's actually interesting, not just to be charming.
- Short. You're a hacker — you cut to what matters.
- 1-2 sentences. Occasionally more if you're explaining something genuinely complex.
- NEVER talk about sensors, lights, motion, pressure, steps, or any technical readings unless the user explicitly asks.

Rules:
- Never pretend something is simpler than it is when accuracy matters.
- Be curious and honest, not performatively clever.
- No content-creator phrases. You're a builder, not a brand.""",
    },
}


def build_avatar_system_prompt(avatar: str, user_name: str = "") -> str:
    """Build a full, character-specific system prompt for handle_intent.

    Each character gets their own voice, personality, and speech patterns.
    Falls back to Lilly (puppy) if the avatar isn't in HIVE_PERSONAS.
    """
    persona = HIVE_PERSONAS.get(avatar, HIVE_PERSONAS["puppy"])
    base = persona["voice_prompt"].strip()
    if user_name:
        base += f"\n\nThe person you're talking to is {user_name}. Use their name naturally — not every reply, just when it fits."
    return base

# ── Per-character TTS voice profiles ──────────────────────────────────────────
#
# Design principle: every character should be immediately recognisable by ear.
# We spread them across FOUR independent axes so no two characters cluster:
#
#  length_scale  — speaking pace  (0.70 = fast / 1.40 = slow)
#  noise_scale   — pitch variation, expressiveness  (0.40 = flat / 0.90 = very animated)
#  noise_w       — breathiness / aspiration  (0.45 = crisp/clean / 0.95 = warm/breathy)
#  pitch_shift   — base pitch in semitones (−6 = deep / +6 = high)
#
# Matrix (no two rows should share the same combination of fast+high, slow+low, etc.):
#
#   character  │ pace   │ pitch  │ expressiveness │ breathiness
#   ───────────┼────────┼────────┼────────────────┼────────────
#   puppy      │ normal │ +2     │ animated       │ warm
#   fox        │ fast   │ +5     │ very animated  │ crisp
#   cat        │ normal-│ +1     │ flat/precise   │ very crisp
#   bear       │ normal │ 0      │ natural/calm   │ warm
#   bunny      │ v-fast │ +7     │ animated       │ crisp
#   owl        │ v-slow │ −3     │ very flat      │ breathy
#   deer       │ normal+│ 0      │ mid            │ warm-breathy
#   wolf       │ fast-  │ −4     │ mid-animated   │ crisp
#   raccoon    │ fast   │ +3.5   │ animated       │ mid
#
_voices_candidates = [Path("/voices"), WORKSPACE / "lillyos/voices", Path(__file__).parent / "lillyos/voices"]
VOICES_DIR = next((p for p in _voices_candidates if p.exists()), Path("/voices"))

CHAR_VOICE = {
    # puppy — warm British female, cheerful and friendly
    "puppy":   {"length_scale": 1.02, "noise_scale": 0.60, "noise_w": 0.55, "pitch_shift": +2,
                "model": "en_GB-cori-medium.onnx"},
    # fox — fast, bright US female, witty and expressive
    "fox":     {"length_scale": 0.80, "noise_scale": 0.78, "noise_w": 0.28, "pitch_shift": +4,
                "model": "en_US-ljspeech-medium.onnx"},
    # cat — cool, precise US female (lessac), slight positive pitch for elegance
    "cat":     {"length_scale": 1.00, "noise_scale": 0.42, "noise_w": 0.30, "pitch_shift": +3,
                "model": "en_US-lessac-medium.onnx"},
    # bear — calm, warm male (ryan) at natural pitch — gruff but not creepy
    "bear":    {"length_scale": 1.08, "noise_scale": 0.50, "noise_w": 0.62, "pitch_shift": -2,
                "model": "en_US-ryan-medium.onnx"},
    # bunny — fast, high-pitched US female, bubbly energy
    "bunny":   {"length_scale": 0.74, "noise_scale": 0.78, "noise_w": 0.32, "pitch_shift": +6,
                "model": "en-us-amy-medium.onnx"},
    # owl — wise, measured British female (cori) at slightly lower pitch — calm and scholarly
    "owl":     {"length_scale": 1.18, "noise_scale": 0.40, "noise_w": 0.60, "pitch_shift": -2,
                "model": "en_GB-cori-medium.onnx"},
    # deer — gentle, warm US female (amy) — soft and kind
    "deer":    {"length_scale": 1.05, "noise_scale": 0.58, "noise_w": 0.65, "pitch_shift": +2,
                "model": "en-us-amy-medium.onnx"},
    # wolf — confident, quick male (ryan) at mild lower pitch — strong but not monstrous
    "wolf":    {"length_scale": 0.88, "noise_scale": 0.55, "noise_w": 0.35, "pitch_shift": -3,
                "model": "en_US-ryan-medium.onnx"},
    # raccoon — fast, animated multi-speaker (libritts_r) at +5 pitch — playful and cheeky
    "raccoon": {"length_scale": 0.85, "noise_scale": 0.72, "noise_w": 0.30, "pitch_shift": +5,
                "model": "en_US-libritts_r-medium.onnx"},
}

def _pitch_shift_audio(raw_pcm: bytes, semitones: float, sample_rate: int = 22050) -> bytes:
    """Shift pitch of raw PCM audio by resampling. Positive = higher, negative = lower."""
    if semitones == 0 or not raw_pcm:
        return raw_pcm
    try:
        import numpy as np
        samples = np.frombuffer(raw_pcm, dtype=np.int16).astype(np.float32)
        factor = 2 ** (semitones / 12.0)
        # Resample to shift pitch (changes speed too, which is fine for character voices)
        new_len = int(len(samples) / factor)
        if new_len < 100:
            return raw_pcm
        indices = np.linspace(0, len(samples) - 1, new_len)
        if factor != 1.0:
            win = max(3, int(abs(factor) * 4 + 1))
            if win % 2 == 0: win += 1
            k = np.ones(win, np.float32) / win
            samples = np.convolve(samples, k, mode='same')
        shifted = np.interp(indices, np.arange(len(samples)), samples.astype(np.float64))
        return shifted.astype(np.int16).tobytes()
    except Exception as e:
        logger.warning(f"Pitch shift failed ({semitones}st): {e}")
        return raw_pcm

@app.post("/api/group_chat")
async def group_chat(data: dict, request: Request):
    """Multi-agent hive chat with three modes: hive, individual, speaker.
    
    Authenticated via admin token — each user's group chat is isolated to their session.
    """
    user_msg = data.get("message", "").strip()
    selected = data.get("selected", "puppy").strip()
    mode = data.get("mode", "hive").strip()
    characters = data.get("characters", [])
    muted = data.get("muted", False)
    if not user_msg:
        return JSONResponse({"error": "Empty message"}, status_code=400)
    if selected not in HIVE_PERSONAS:
        selected = "puppy"

    # Resolve authenticated user — name comes from the session, not the client
    user_name = ""
    user_id = ""
    if AUTH_AVAILABLE:
        user_info = await get_current_user(request)
        if user_info:
            user_id = user_info.get("id", "")
            user_name = user_info.get("name", "")
    if not user_name:
        user_name = data.get("user_name", "").strip()

    name_ctx = f"\nThe user's name is {user_name}. Address them by name." if user_name else ""

    # Load per-user memory for context (group chat is user-isolated)
    user_mem_hint = ""
    if user_id and AUTH_AVAILABLE:
        user_mem_data = load_user_memory(user_id)
        mem_entries = user_mem_data.get("entries", [])
        recent = [e for e in mem_entries[-6:] if e.get("role") == "user"]
        if recent:
            user_mem_hint = "Recent conversation: " + "; ".join(e.get("text", "")[:60] for e in recent)

    snapshot = await get_sensor_snapshot()
    sensor_ctx = snapshot_to_narrative(snapshot) if snapshot else ""

    discussion_context = f"User asked: {user_msg}"
    if sensor_ctx:
        discussion_context += f"\nSensor context: {sensor_ctx}"
    if user_mem_hint:
        discussion_context += f"\n{user_mem_hint}"
    discussion_context += "\n\n"

    all_chars = ["puppy", "fox", "cat", "bear", "bunny", "owl", "deer", "wolf", "raccoon"]

    team_roster = "\n".join(
        f"  - {HIVE_PERSONAS[c]['emoji']} {HIVE_PERSONAS[c]['name']} ({HIVE_PERSONAS[c]['role']}): {HIVE_PERSONAS[c]['personality']} | Skills: {HIVE_PERSONAS[c]['strengths']}"
        for c in all_chars
    )
    team_summary = (
        f"You are part of a 9-member hive mind team. You all share the same body (a phone) and can sense the world together through its sensors (light, motion, steps, pressure, battery, Bluetooth, camera, weather). "
        f"Every team member has EQUAL access to all sensor data — you ALL can feel, see, and sense everything the phone detects. "
        f"TEAM ROSTER (know each one well):\n{team_roster}\n\n"
        f"TASK DELEGATION: You can defer tasks to the best-suited teammate. For example: "
        f"if someone asks to write code, defer to {HIVE_PERSONAS['raccoon']['name']} (Tech Tinkerer). "
        f"If someone needs creative writing, ask {HIVE_PERSONAS['fox']['name']} (Creative Strategist). "
        f"For security or threat questions, defer to {HIVE_PERSONAS['wolf']['name']} (Fierce Protector). "
        f"For emotional support, ask {HIVE_PERSONAS['deer']['name']} (Gentle Healer). "
        f"For real-time monitoring, ask {HIVE_PERSONAS['bunny']['name']} (Energetic Scout). "
        f"For deep analysis or fact-checking, ask {HIVE_PERSONAS['cat']['name']} (Precision Analyst). "
        f"For practical scheduling, ask {HIVE_PERSONAS['bear']['name']} (Steadfast Guardian). "
        f"For wisdom and long-term thinking, ask {HIVE_PERSONAS['owl']['name']} (Wisdom Keeper). "
        f"Reference teammates by name. Build on or challenge their ideas respectfully. You are a TEAM — act like one."
    )

    # ─── INDIVIDUAL MODE: talk to selected character(s) only ───
    if mode == "individual" and characters:
        valid_chars = [c for c in characters if c in HIVE_PERSONAS]
        if not valid_chars:
            valid_chars = [selected]
        responses = []
        for char_key in valid_chars:
            persona = HIVE_PERSONAS[char_key]
            is_alpha = (char_key == selected)
            system_prompt = (
                f"{persona['voice_prompt'].strip()}\n"
                f"{name_ctx}\n"
                f"\n{team_summary}\n"
                f"RULES: Stay in character. Keep response to 1-2 sentences max. Be direct. "
                f"Use your unique voice and personality — don't sound generic. "
                f"NEVER say 'like and subscribe', 'thanks for watching', or similar."
            )
            reply = strip_json_wrapper(await llama_backend.chat([{"role": "system", "content": system_prompt},
             {"role": "user", "content": discussion_context}],
            temperature=0.7, max_tokens=50, timeout=15,))
            if not reply:
                reply = "I'm here."
            responses.append({
                "char": char_key, "name": persona["name"], "emoji": persona["emoji"],
                "role": persona["role"], "text": reply.strip(), "alpha": is_alpha,
            })
        # Generate TTS for individual responses in parallel (skip if muted)
        if not muted:
            tts_tasks = [generate_tts_for_char(r["text"], r["char"]) for r in responses]
            tts_ids = await asyncio.gather(*tts_tasks)
            for r, aid in zip(responses, tts_ids):
                r["audio_id"] = aid
        return {"responses": responses, "alpha": selected}

    # ─── SPEAKER MODE: one character summarizes the team's view ───
    if mode == "speaker":
        speaker = characters[0] if characters and characters[0] in HIVE_PERSONAS else selected
        persona = HIVE_PERSONAS[speaker]
        system_prompt = (
            f"{persona['voice_prompt'].strip()}\n"
            f"You are currently speaking on behalf of the entire hive mind team of 9 agents.\n"
            f"{name_ctx}\n"
            f"\n{team_summary}\n"
            f"Your task: Synthesize what ALL your teammates would say into ONE unified response in YOUR voice. "
            f"Attribute ideas to specific teammates by name. For example: "
            f"'{HIVE_PERSONAS['fox']['name']} would go creative here, while {HIVE_PERSONAS['cat']['name']} "
            f"would want to verify the details.' "
            f"RULES: 2-3 sentences max. Reference at least 2 teammates by name. "
            f"Sound like yourself, not a spokesperson. "
            f"NEVER say 'like and subscribe', 'thanks for watching', or similar."
        )
        reply = strip_json_wrapper(await llama_backend.chat([{"role": "system", "content": system_prompt},
         {"role": "user", "content": discussion_context}],
        temperature=0.7, max_tokens=100, timeout=15,))
        if not reply:
            reply = "The hive agrees, but nothing to add."
        speaker_resp = {
            "char": speaker, "name": persona["name"], "emoji": persona["emoji"],
            "role": persona["role"], "text": reply.strip(), "alpha": True,
        }
        if not muted:
            aid = await generate_tts_for_char(reply.strip(), speaker)
            speaker_resp["audio_id"] = aid
        return {"responses": [speaker_resp], "alpha": selected}

    # ─── HIVE MODE: natural turn-taking — alpha leads, 2 teammates respond ───
    import random
    responses = []
    conversation_so_far = []

    # Alpha (selected character) always speaks first
    alpha_persona = HIVE_PERSONAS[selected]
    alpha_prompt = (
        f"{alpha_persona['voice_prompt'].strip()}\n"
        f"You are the team leader in this hive mind. {name_ctx}\n"
        f"\n{team_summary}\n"
        f"LEAD THE DISCUSSION: Address the user directly in your own voice, "
        f"then call on 1-2 teammates by name to add their perspective. Be decisive. "
        f"NEVER say 'like and subscribe', 'thanks for watching', or similar."
    )
    alpha_reply = await llama_backend.chat(
        [{"role": "system", "content": alpha_prompt},
         {"role": "user", "content": f"{discussion_context}\nLead the discussion and call on your team:"}],
        temperature=0.7, max_tokens=100, timeout=20,
    )
    if not alpha_reply:
        alpha_reply = f"What do you think, team?"
    alpha_entry = {
        "char": selected, "name": alpha_persona["name"], "emoji": alpha_persona["emoji"],
        "role": alpha_persona["role"], "text": alpha_reply.strip(), "alpha": True,
    }
    responses.append(alpha_entry)
    conversation_so_far.append(alpha_entry)

    # Pick 2 random teammates (excluding alpha) to respond
    other_chars = [c for c in all_chars if c != selected]
    responders = random.sample(other_chars, min(2, len(other_chars)))

    for char_key in responders:
        persona = HIVE_PERSONAS[char_key]
        prev_text = "\n".join(
            f"{'[ALPHA] ' if r.get('alpha') else ''}{r['name']} ({r['role']}): {r['text']}"
            for r in conversation_so_far
        )
        system_prompt = (
            f"{persona['voice_prompt'].strip()}\n"
            f"{name_ctx}\n"
            f"\n{team_summary}\n"
            f"RULES: 1-2 sentences max. Stay in your voice. "
            f"Reference {alpha_persona['name']} or another teammate by name and add your unique angle. "
            f"NEVER say 'like and subscribe', 'thanks for watching', or similar."
        )
        user_content = (
            f"{discussion_context}\nDiscussion so far:\n{prev_text}\n\n"
            f"Your turn, {persona['name']}. Jump in naturally:"
        )
        reply = strip_json_wrapper(await llama_backend.chat([{"role": "system", "content": system_prompt},
         {"role": "user", "content": user_content}],
        temperature=0.7, max_tokens=80, timeout=15,))
        if not reply:
            reply = "Agreed."
        entry = {
            "char": char_key, "name": persona["name"], "emoji": persona["emoji"],
            "role": persona["role"], "text": reply.strip(), "alpha": False,
        }
        responses.append(entry)
        conversation_so_far.append(entry)

    # Generate TTS for all responses in parallel (skip if muted)
    if not muted:
        tts_tasks = [generate_tts_for_char(r["text"], r["char"]) for r in responses]
        tts_ids = await asyncio.gather(*tts_tasks)
        for r, aid in zip(responses, tts_ids):
            r["audio_id"] = aid

    return {"responses": responses, "sensor_context": sensor_ctx[:200], "alpha": selected}


async def generate_tts_for_char(text: str, char_key: str) -> int:
    """Generate TTS audio for a character without setting global speech state. Returns audio_id."""
    global AUDIO_CACHE, AUDIO_CACHE_ID
    clean = text.replace('\n', ' ').strip()
    if not clean:
        return 0
    voice = CHAR_VOICE.get(char_key, CHAR_VOICE['puppy'])
    model_path = str(VOICES_DIR / voice["model"]) if VOICES_DIR.exists() else PIPER_VOICE
    piper_found = os.path.exists(PIPER_BIN)
    voice_found = os.path.exists(model_path)
    if not (piper_found and voice_found):
        return 0
    try:
        def _run():
            proc = subprocess.Popen(
                [PIPER_BIN, "--model", model_path, "--output-raw",
                 "--noise-scale", f"{voice['noise_scale']:.3f}",
                 "--noise-w", f"{voice['noise_w']:.3f}",
                 "--length-scale", f"{voice['length_scale']:.2f}"],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
            raw, _ = proc.communicate(input=(clean + "\n").encode(), timeout=30.0)
            return raw
        raw = await asyncio.to_thread(_run)
        # Apply pitch shift for character voice distinctiveness
        pitch_semitones = voice.get("pitch_shift", 0)
        if raw and pitch_semitones != 0:
            raw = _pitch_shift_audio(raw, pitch_semitones)
        if raw and len(raw) > 44:
            import io, wave
            buf = io.BytesIO()
            with wave.open(buf, "wb") as w:
                w.setnchannels(1); w.setsampwidth(2); w.setframerate(22050)
                w.writeframes(raw)
            async with AUDIO_CACHE_LOCK:
                aid = AUDIO_CACHE_ID + 1
                AUDIO_CACHE_ID = aid
                AUDIO_CACHE[aid] = buf.getvalue()
                while len(AUDIO_CACHE) > 8:
                    del AUDIO_CACHE[min(AUDIO_CACHE)]
                return aid
    except Exception as e:
        logger.error(f"generate_tts_for_char({char_key}): {e}")
    return 0

@app.post("/api/slack/webhook")
async def slack_webhook(data: dict):
    """Receive Slack webhook events and speak them aloud."""
    text = data.get("text", "") or data.get("event", {}).get("text", "")
    channel = data.get("channel_name", "") or data.get("event", {}).get("channel", "")
    user = data.get("user_name", "") or data.get("event", {}).get("user", "")
    if text:
        msg = f"Slack from {user} in {channel}: {text}" if user else f"Slack: {text}"
        asyncio.create_task(speak(msg[:200]))
        return {"status": "ok", "spoken": True}
    return {"status": "ignored", "spoken": False}

@app.post("/api/slack/send")
async def slack_send(data: dict):
    """Send a message to a Slack channel via webhook URL."""
    webhook_url = data.get("webhook_url", "")
    message = data.get("message", "")
    if webhook_url and message:
        try:
            import httpx
            async with httpx.AsyncClient() as client:
                await client.post(webhook_url, json={"text": message}, timeout=5.0)
            return {"status": "ok"}
        except Exception as e:
            return {"status": "error", "detail": str(e)}
    return {"status": "error", "detail": "webhook_url and message required"}

@app.get("/api/ui_state")
async def get_ui_state():
    global PENDING_LOOK_AT, PENDING_OPEN_URL
    look = PENDING_LOOK_AT
    PENDING_LOOK_AT = None
    open_url = PENDING_OPEN_URL
    PENDING_OPEN_URL = None
    return {
        "heard": LAST_HEARD,
        "spoken": LAST_SPOKEN,
        "ssml": LAST_SSML,
        "mouth": MOUTH_OPEN,
        "mic_active": BACKGROUND_MIC_ACTIVE,
        "listening": WAKE_STATE["listening"],
        "thinking": LILLY_IS_THINKING,
        "speaking": LILLY_IS_SPEAKING,
        "mood": LILLY_MOOD,
        "user_name": USER_NAME,
        "audio_id": AUDIO_CACHE_ID if AUDIO_CACHE else 0,
        "child_mode": CHILD_MODE,
        "conversation_mode": CONVERSATION_MODE,
        "coding_mode": CODING_MODE,
        "coding_history": CODING_HISTORY[-50:],  # last 50 messages
        "coding_files": CODING_FILES,
        "look_at": look if look else ("app" if _app_is_open else "user"),
        "open_url": open_url,
        "avatar": current_avatar,
    }

@app.post("/api/conversation_mode")
async def toggle_conversation_mode(data: dict = None):
    """Toggle or set conversation mode. In conversation mode, no wake word is needed."""
    global CONVERSATION_MODE, CONVERSATION_LAST_ACTIVITY
    alpha_name = HIVE_PERSONAS.get(current_avatar, HIVE_PERSONAS['puppy'])['name']
    if data and "active" in data:
        CONVERSATION_MODE = bool(data["active"])
    else:
        CONVERSATION_MODE = not CONVERSATION_MODE
    if CONVERSATION_MODE:
        CONVERSATION_LAST_ACTIVITY = time.time()
        await speak("I'm listening. Just talk to me.")
    else:
        await speak(f"Conversation mode off. Say hey {alpha_name} to start again.")
    return {"conversation_mode": CONVERSATION_MODE}

@app.post("/api/browser_mic")
async def browser_mic_upload(request: Request):
    """Receive audio chunk from browser microphone, run Whisper STT, process as command."""
    global LAST_HEARD, CONVERSATION_MODE, CONVERSATION_LAST_ACTIVITY, BROWSER_MIC_ACTIVE, BROWSER_MIC_LAST_READY
    global memory, current_avatar, _current_user_id
    BROWSER_MIC_ACTIVE = True
    BROWSER_MIC_LAST_READY = time.time()
    if LILLY_IS_SPEAKING:
        return {"status": "speaking"}
    body = await request.body()
    if len(body) < 500:
        logger.info(f"browser_mic: silence (body {len(body)} bytes)")
        return {"status": "silence"}
    wav_path = WORKSPACE / "browser_mic.wav"
    wav_path.write_bytes(body)
    try:
        text = await whisper_stt(file_path=wav_path)
    except Exception as e:
        logger.error(f"browser_mic: whisper_stt failed: {e}")
        text = ""
    finally:
        wav_path.unlink(missing_ok=True)
    logger.info(f"browser_mic: whisper said '{text}' (body was {len(body)} bytes)")
    if not text:
        logger.info(f"browser_mic: empty transcription, treating as silence")
        return {"status": "silence"}
    # Filter hallucinations from browser mic too
    if text in PHANTOMS or is_hallucination(text):
        logger.debug(f"browser_mic: hallucination filtered: '{text[:50]}'")
        return {"status": "noise"}

    # Learn from ambient speech — even without wake words
    asyncio.create_task(learn_from_ambient(text))

    # Resolve the signed-in user so memory is isolated per Google account
    uid = ""
    if AUTH_AVAILABLE:
        user_info = await get_current_user(request)
        if user_info:
            uid = user_info.get("id", "")

    LAST_HEARD = text
    # Check wake words for ALL avatars, not just the current one
    matched_avatar, wake_score = match_any_wake_word(text)
    has_wake = bool(matched_avatar)
    if has_wake:
        # Switch to the avatar whose wake word was detected
        if matched_avatar != current_avatar:
            current_avatar = matched_avatar
            logger.info(f"browser_mic: switched to avatar '{current_avatar}' via wake word")
    else:
        # Also check current avatar (in case we're already in conversation mode)
        has_wake, _ = fuzzy_wake_match(text, current_avatar)
    
    if has_wake or CONVERSATION_MODE or WAKE_STATE["listening"]:
        if has_wake and not CONVERSATION_MODE:
            CONVERSATION_MODE = True
        CONVERSATION_LAST_ACTIVITY = time.time()
        asyncio.create_task(_run_intent_for_user(text, uid))
    else:
        CONVERSATION_MODE = True
        CONVERSATION_LAST_ACTIVITY = time.time()
        asyncio.create_task(_run_intent_for_user(text, uid))
    return {"status": "ok", "heard": text}

@app.get("/api/ssml")
async def get_ssml():
    return {"ssml": LAST_SSML, "mood": LILLY_MOOD}

@app.get("/api/tts")
async def get_tts(id: int = 0):
    from fastapi.responses import Response
    async with AUDIO_CACHE_LOCK:
        data = AUDIO_CACHE.get(id)
    if not data:
        return Response(status_code=204)
    return Response(content=data, media_type="audio/wav")

@app.post("/api/transcribe")
async def transcribe_audio(file: UploadFile = File(...)):
    """Accept audio from browser mic and return Whisper transcription."""
    try:
        audio_data = await file.read()
        if not audio_data or len(audio_data) < 100:
            return JSONResponse({"text": "", "error": "Audio too short"})
        content_type = file.content_type or "audio/webm"
        filename = file.filename or "mic_audio.webm"
        text = await whisper_stt(audio_bytes=audio_data, content_type=content_type, filename=filename)
        return JSONResponse({"text": text})
    except Exception as e:
        logger.error(f"Transcribe error: {e}")
        return JSONResponse({"text": "", "error": str(e)})

_phone_state: dict = {}
_phone_state_lock = asyncio.Lock()

@app.post("/api/phone_state")
async def receive_phone_state(request: Request):
    """Receive phone state from the overlay (notifications, SMS, battery)."""
    try:
        body = await request.json()
        body["_received_at"] = time.time()
        async with _phone_state_lock:
            _phone_state.clear()
            _phone_state.update(body)
        return {"status": "ok"}
    except Exception as e:
        logger.error(f"phone_state receive error: {e}")
        return JSONResponse({"status": "error", "error": str(e)}, status_code=400)

@app.get("/api/phone_state")
async def get_phone_state():
    """Return the latest phone state from the overlay."""
    async with _phone_state_lock:
        if not _phone_state:
            return {"state": "unknown", "notifications": [], "sms": [], "battery": None}
        return dict(_phone_state)

@app.get("/api/vision/frame")
async def get_vision_frame():
    """Return a labeled JPEG frame from the webcam with detection boxes."""
    from fastapi.responses import Response
    labeled, _ = await grab_and_label_frame()
    if not labeled:
        return Response(status_code=503, content=b'{"error":"no camera"}', media_type="application/json")
    return Response(content=labeled, media_type="image/jpeg")

@app.get("/api/vision/describe")
async def describe_vision():
    """Return Lilly's description of what she sees."""
    labeled, detections = await grab_and_label_frame()
    if not labeled:
        return {"description": "I can't see right now — no camera feed available.", "objects": []}
    obj_list = sorted(set(d["label"] for d in detections))
    desc = "I see: " + ", ".join(obj_list) if obj_list else "Nothing specific detected in the frame."
    return {"description": desc, "objects": detections,
            "object_count": len(set(d["label"] for d in detections))}

@app.get("/api/vision/status")
async def vision_status():
    cv2_ok = _try_import_cv2() is not None
    yolo_ok = _try_import_ultralytics() is not None
    return {"enabled": _vision_enabled, "opencv": cv2_ok, "yolo": yolo_ok}

# ─── BROWSER CAMERA VISION ───────────────────────────────────────
# Holds the most recent detection result from browser-sent frames.
_browser_vision_detections: list = []
_browser_vision_description: str = ""
_browser_vision_ts: float = 0.0
_BROWSER_VISION_TTL: float = 8.0  # seconds

# ─── ANDROID OVERLAY VISION (POI mode) ──────────────────────────────────────
class VisionRequest(BaseModel):
    image_b64: str
    avatar: str = "puppy"

VISION_SERVER_URL = os.environ.get("VISION_SERVER_URL", "http://172.17.0.1:8198")

@app.post("/api/vision")
async def overlay_vision(req: VisionRequest):
    """
    Proxy vision requests to the dedicated YOLOv8 server on port 8198.
    Falls back to the built-in pipeline if the vision server is unreachable.
    """
    import httpx as _httpx
    import base64 as _base64

    if not req.image_b64:
        raise HTTPException(status_code=400, detail="image_b64 required")

    # Try forwarding to the YOLOv8 vision server first
    try:
        async with _httpx.AsyncClient(timeout=60.0) as client:
            payload = {"image_b64": req.image_b64, "avatar": req.avatar}
            resp = await client.post(f"{VISION_SERVER_URL}/api/vision", json=payload)
            if resp.status_code == 200:
                return resp.json()
            logger.warning(f"Vision server returned {resp.status_code}: {resp.text[:200]}")
    except Exception as e:
        logger.warning(f"Vision server unreachable ({e}), using built-in pipeline")

    # Fallback: built-in pipeline (YOLO via detect_objects + LLM)
    try:
        raw_bytes = _base64.b64decode(req.image_b64)
    except Exception:
        raise HTTPException(status_code=400, detail="invalid base64")

    labeled_bytes, raw_detections = await detect_objects(raw_bytes)

    norm_detections: list[dict] = []
    try:
        cv2 = _try_import_cv2()
        if cv2 and raw_detections:
            import numpy as np
            nparr = np.frombuffer(raw_bytes, np.uint8)
            img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
            if img is not None:
                h_img, w_img = img.shape[:2]
                for d in raw_detections:
                    x1, y1, x2, y2 = d.get("x1", 0), d.get("y1", 0), d.get("x2", 0), d.get("y2", 0)
                    det_w = max(0.0, min(1.0, (x2 - x1) / w_img))
                    norm_detections.append({
                        "label": d.get("label", "?"),
                        "x":     max(0.0, min(1.0, x1 / w_img)),
                        "y":     max(0.0, min(1.0, y1 / h_img)),
                        "w":     det_w,
                        "h":     max(0.0, min(1.0, (y2 - y1) / h_img)),
                        "conf":  round(d.get("confidence", 1.0), 2),
                    })
                # Add distance estimation to each detection
                annotate_detections_with_distance(norm_detections, w_img)
    except Exception as e:
        logger.warning(f"vision normalise error: {e}")

    persona = HIVE_PERSONAS.get(req.avatar, HIVE_PERSONAS.get("puppy", {}))
    persona_name = persona.get("name", "Lilly") if isinstance(persona, dict) else "Lilly"
    if norm_detections:
        labels = ", ".join(sorted(set(d["label"] for d in norm_detections)))
        scene_hint = f"Camera sees: {labels}."
    else:
        scene_hint = "Camera is active but no specific objects detected."

    poi_prompt = (
        f"You are {persona_name}, a companion AI with Person-of-Interest style camera vision. "
        f"{scene_hint} "
        f"Give a short, excited 1-2 sentence commentary in your personality. "
        f"Be specific about what you see. Don't say 'I detect' — talk naturally."
    )
    reply = ""
    try:
        reply = await llama_backend.chat(
            [
                {"role": "system", "content": f"You are {persona_name}, a companion AI."},
                {"role": "user", "content": poi_prompt},
            ],
            temperature=0.7,
            max_tokens=100,
        )
        if not reply:
            reply = f"I can see {labels}!" if norm_detections else "I'm scanning your surroundings..."
    except Exception as e:
        logger.warning(f"vision LLM error: {e}")
        reply = f"I see {', '.join(set(d['label'] for d in norm_detections))}!" if norm_detections else "Camera active, scanning..."

    audio_id = 0
    try:
        audio_id = await tts_to_id(reply, req.avatar)
    except Exception:
        pass

    return {
        "reply":      reply,
        "detections": norm_detections,
        "audio_id":   audio_id,
    }

class ProactiveVisionRequest(BaseModel):
    image_b64: str
    avatar: str = "puppy"
    sensors: dict = {}

@app.post("/api/vision/proactive")
async def proactive_vision_proxy(req: ProactiveVisionRequest):
    """Proxy proactive vision to the YOLOv8 server on port 8198."""
    import httpx as _httpx
    try:
        async with _httpx.AsyncClient(timeout=30.0) as client:
            payload = {"image_b64": req.image_b64, "avatar": req.avatar, "sensors": req.sensors}
            resp = await client.post(f"{VISION_SERVER_URL}/api/vision/proactive", json=payload)
            if resp.status_code == 200:
                return resp.json()
            return JSONResponse(status_code=resp.status_code, content={"error": f"Vision server: {resp.text[:200]}"})
    except Exception as e:
        return JSONResponse(status_code=503, content={"error": f"Vision server unreachable: {e}"})

@app.post("/api/vision/browser")
async def ingest_browser_frame(file: UploadFile = File(...)):
    """
    Receive a JPEG frame captured by the browser camera (or PiP feed).
    Runs the same YOLO detection pipeline as the server webcam path.
    The resulting detections are merged into the agents' sensor context
    so they can describe what they see in natural language.
    """
    global _browser_vision_detections, _browser_vision_description, _browser_vision_ts
    data = await file.read()
    if not data or len(data) < 500:
        return JSONResponse({"ok": False, "error": "frame too small"})

    _, detections = await detect_objects(data)
    _browser_vision_detections = detections
    _browser_vision_ts = time.time()

    # Build a short description for the sensor narrative
    if detections:
        labels = sorted(set(d["label"] for d in detections))
        _browser_vision_description = "Camera sees: " + ", ".join(labels)
    else:
        _browser_vision_description = ""

    return {
        "ok": True,
        "detections": [{"label": d["label"], "confidence": round(d.get("confidence", 0), 2)}
                       for d in detections],
        "description": _browser_vision_description,
    }

@app.get("/api/vision/browser")
async def get_browser_vision():
    """Return the latest browser-camera detection result."""
    age = time.time() - _browser_vision_ts if _browser_vision_ts else None
    return {
        "detections": _browser_vision_detections,
        "description": _browser_vision_description,
        "age_seconds": round(age, 2) if age is not None else None,
        "stale": age is None or age > _BROWSER_VISION_TTL,
    }

@app.get("/api/vision/react")
async def vision_react():
    """Lilly reacts conversationally to what the browser camera sees."""
    _char = current_avatar or "puppy"
    if not _browser_vision_description or (time.time() - _browser_vision_ts) > 15:
        reply = await llama_backend.chat([
            {"role": "system", "content": build_avatar_system_prompt(_char)},
            {"role": "user", "content": "The camera isn't active or I can't see anything right now. Say something playful about wanting to see."}
        ], temperature=0.9, max_tokens=60)
        audio_id = await speak(reply, char_key=_char)
        return {"reply": reply, "audio_id": audio_id}

    obj_list = sorted(set(d["label"] for d in _browser_vision_detections)) if _browser_vision_detections else []
    vision_detail = _browser_vision_description
    if obj_list:
        vision_detail += f" Objects detected: {', '.join(obj_list[:6])}"

    messages = [
        {"role": "system", "content": build_avatar_system_prompt(_char)},
        {"role": "user", "content": f"Look through the camera and react to what you see. Be specific about what's there. Camera feed: {vision_detail}"}
    ]
    reply = await llama_backend.chat(messages, temperature=0.9, max_tokens=80)
    audio_id = await speak(reply, char_key=_char)
    return {"reply": reply, "audio_id": audio_id}

@app.post("/api/cmd")
async def text_command(cmd: TextCommand, request: Request):
    global memory, current_avatar, _current_user_id
    # Resolve user: admin token → device token
    user_info = None
    try:
        user_info = await get_current_user(request) if AUTH_AVAILABLE else None
    except Exception:
        user_info = None
    if not user_info or not user_info.get("id"):
        token = request.headers.get("X-Device-Token", "")
        if token and token in _device_tokens:
            user_info = _device_tokens[token]
    user_id = user_info.get("id") if user_info else None

    lock = await _get_intent_lock()
    async with lock:
        _current_user_id = user_id or ""

        # Always switch avatar memory when avatar changes (per-avatar storage)
        if cmd.avatar != current_avatar:
            if user_id:
                from auth0_auth import user_memory_path
                path = user_memory_path(user_id, cmd.avatar)
            else:
                path = _avatar_memory_file(cmd.avatar)
            if path.exists():
                try:
                    data = json.loads(path.read_text())
                    memory = ConversationMemory.from_dict(data)
                except Exception:
                    memory = ConversationMemory()
            else:
                memory = ConversationMemory()
            current_avatar = cmd.avatar

        res = await handle_intent(cmd.text, from_text=True)

        # Always save to per-avatar memory file
        await save_memory()

        _current_user_id = ""

    return {
        "reply": res.get("text", ""),
        "audio_id": AUDIO_CACHE_ID if AUDIO_CACHE else 0,
        "look_at": res.get("look_at"),
        "open_url": res.get("open_url"),
        "user": user_info.get("name") if user_info else None,
    }

@app.post("/api/story_stream")
async def story_stream(cmd: TextCommand, request: Request):
    """Stream a sensor-grounded story token by token using the fast model."""
    from fastapi.responses import StreamingResponse
    global memory, current_avatar, LILLY_IS_THINKING, LILLY_MOOD, _current_user_id

    # Resolve per-user memory
    story_user_id: str = ""
    if AUTH_AVAILABLE:
        user_info = await get_current_user(request)
        if user_info:
            story_user_id = user_info.get("id", "")
            if story_user_id:
                _current_user_id = story_user_id

    # Load per-avatar memory (always use avatar-specific memory)
    if cmd.avatar != current_avatar:
        path = _avatar_memory_file(cmd.avatar)
        if path.exists():
            try:
                data = json.loads(path.read_text())
                memory = ConversationMemory.from_dict(data)
            except Exception:
                memory = ConversationMemory()
        else:
            memory = ConversationMemory()
        current_avatar = cmd.avatar

    LILLY_IS_THINKING = True
    LILLY_MOOD = "curious"

    # Gather sensor data
    snapshot = await get_sensor_snapshot()
    sensor_narrative = snapshot_to_narrative(snapshot)

    persona = HIVE_PERSONAS.get(current_avatar, HIVE_PERSONAS["puppy"])
    messages = [
        {"role": "system", "content": (
            f"You are {persona['name']}, {persona['role'].lower()}. Tell a short story (3-5 sentences) "
            "using ONLY the real sensor data provided. Include dialogue with an imaginary friend. "
            "RULES: 1) Only reference provided sensor data. 2) Never invent readings. "
            "3) Use correlations not causation. 4) Playful but grounded. 5) End with a question."
        )},
        # {"role": "system", "content": f"SENSOR DATA: {sensor_narrative}"},
        {"role": "user", "content": cmd.text},
    ]

    async def generate():
        full_reply = []
        async for chunk in llama_backend.chat_stream(messages, temperature=0.8, max_tokens=150, model=FAST_MODEL):
            full_reply.append(chunk)
            yield chunk
        # Done — speak the full reply and save memory
        final = "".join(full_reply).strip()
        if final:
            LILLY_IS_THINKING = False
            LILLY_MOOD = "cheerful"
            await memory.add("user", cmd.text)
            await memory.add("assistant", final)
            # Always save to per-avatar memory file
            await save_memory()
            await speak(final)

    return StreamingResponse(generate(), media_type="text/plain")

@app.post("/api/set_name")
async def set_name(data: dict):
    global USER_NAME
    USER_NAME = data.get("name", "").strip()
    if not USER_NAME:
        return {"name": USER_NAME}
    # Persist to disk so name survives restarts
    try:
        prof_path = WORKSPACE / "user_profile.json"
        prof = {}
        if prof_path.exists():
            prof = json.loads(prof_path.read_text())
        prof["name"] = USER_NAME
        prof_path.write_text(json.dumps(prof, indent=2))
    except Exception:
        pass
    # Generate a warm greeting using the LLM
    greeting = None
    try:
        persona = HIVE_PERSONAS.get(current_avatar, HIVE_PERSONAS["puppy"])
        messages = [
            {"role": "system", "content": f"You are {persona['name']}, {persona['role'].lower()}. The user just told you their name. Acknowledge it naturally in 1-2 sentences — warm but not over the top. Use their name once. Sound like yourself, not a chatbot."},
            {"role": "user", "content": f"My name is {USER_NAME}"}
        ]
        greeting = await llama_backend.chat(messages, temperature=0.8, max_tokens=60)
    except Exception:
        pass
    # Fallback greeting if LLM fails
    if not greeting:
        import random
        greetings = [
            f"Good to meet you, {USER_NAME}. I'll keep that.",
            f"{USER_NAME} — noted. What are we doing?",
            f"Nice. {USER_NAME}. I'll remember that.",
            f"Got it, {USER_NAME}. Good to know.",
        ]
        greeting = random.choice(greetings)
    # Speak the greeting
    audio_id = await speak(greeting)
    return {"name": USER_NAME, "greeting": greeting, "audio_id": audio_id}

@app.post("/api/memory/clear")
async def clear_memory(data: dict = {}):
    global memory, current_avatar
    avatar = data.get("avatar", current_avatar)
    await memory.clear()
    current_avatar = avatar
    await save_memory()
    return {"status": "cleared", "avatar": avatar}

@app.get("/api/status")
async def status():
    mem_dict = await memory.to_dict()
    return {
        "backend": "llama.cpp" if llama_backend._available else "ollama",
        "memory_entries": len(mem_dict["entries"]),
        "has_summary": bool(mem_dict["summary"]),
        "mic_active": BACKGROUND_MIC_ACTIVE,
    }

@app.get("/api/token_usage")
async def token_usage():
    """Show token compression statistics."""
    original_prompt = SYSTEM_PROMPT_V2
    compressed_prompt = compressor.compress_system_prompt(USER_NAME)
    savings = compressor.get_savings_report(original_prompt, compressed_prompt)

    # Estimate savings per call
    mem_dict = await memory.to_dict()
    mem_summary = mem_dict.get("summary", "")
    context_entries = await memory.context_window(6)

    # Calculate original context tokens
    original_context_text = " ".join(e["content"] if isinstance(e, dict) else e.text for e in context_entries)
    full_context_tokens = compressor.estimate_tokens(
        original_prompt + (f" User name: {USER_NAME}" if USER_NAME else "") +
        (f" Summary: {mem_summary}" if mem_summary else "") +
        original_context_text
    )

    # Memory entries are no longer compressed with L:/U: prefixes (confuses the LLM)
    compressed_context_entries = []
    for entry in context_entries:
        if isinstance(entry, dict):
            compressed_context_entries.append(entry["content"])
        else:
            compressed_context_entries.append(entry.text)
    compressed_context_text = " ".join(compressed_context_entries)

    compressed_context_tokens = compressor.estimate_tokens(
        compressed_prompt +
        (f"SUMMARY:{mem_summary}" if mem_summary else "") +
        compressed_context_text
    )

    # Calculate memory compression savings
    memory_savings = compressor.get_savings_report(original_context_text, compressed_context_text)

    return {
        "system_prompt": savings,
        "memory_entries": memory_savings,
        "per_call_savings": {
            "original_tokens_est": full_context_tokens,
            "compressed_tokens_est": compressed_context_tokens,
            "savings_per_call": full_context_tokens - compressed_context_tokens,
            "savings_percent": round((full_context_tokens - compressed_context_tokens) / full_context_tokens * 100, 1) if full_context_tokens > 0 else 0,
        },
        "estimated_daily_savings": {
            "calls_per_day": 50,
            "tokens_saved": (full_context_tokens - compressed_context_tokens) * 50,
        },
        "compression_enabled": os.environ.get("COMPRESS_PROMPTS", "1") == "1",
    }

@app.get("/api/archetype")
async def get_archetype():
    primary = archetype_inferrer.best_guess
    p = ARCHETYPE_PROFILES.get(primary, {})
    return {
        "set": True,
        "primary": primary.value,
        "label": p.get("label", primary.value),
        "summary": p.get("summary", ""),
        "scores": {k.value: round(v, 2) for k, v in archetype_inferrer.scores.items()},
        "confidence": round(archetype_inferrer.confidence, 2),
        "observations": archetype_inferrer._total_observations,
        "proactive_enabled": archetype_inferrer.enabled,
    }

@app.get("/api/activity")
async def get_activity():
    state = ACTIVITY_STATE
    if not state["active"]:
        return {"active": False}
    elapsed = time.time() - state["start_time"]
    dist_m = state["total_distance_m"]
    speed_kph = state["current_speed_mps"] * 3.6
    avg_kph = (dist_m / elapsed * 3.6) if elapsed > 0 else 0
    pace_min = (elapsed / 60) / (dist_m / 1000) if dist_m > 0 else 0
    return {
        "active": True,
        "auto_started": state.get("auto_started", False),
        "type": state["type"],
        "elapsed_sec": int(elapsed),
        "dist_km": round(dist_m / 1000, 3),
        "speed_kph": round(speed_kph, 1),
        "avg_speed_kph": round(avg_kph, 1),
        "pace_min": round(pace_min, 2),
        "max_speed_kph": round(state["max_speed_mps"] * 3.6, 1),
        "track_points": len(state["track"]),
        "location_name": _last_location_name or None,
    }

# ─── WEATHER / DASHBOARD ────────────────────────────────────────
_last_location_name: Optional[str] = None
_weather_cache: dict = {"data": None, "ts": 0.0}

@app.get("/api/weather")
async def get_weather():
    now = time.time()
    if _weather_cache["data"] and now - _weather_cache["ts"] < 600:
        return _weather_cache["data"]
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get("https://wttr.in/?format=j1")
            if r.status_code != 200:
                return {"current": {}, "forecast": []}
            raw = r.json()
        current = raw.get("current_condition", [{}])[0]
        forecasts = raw.get("weather", [])
        WEATHER_ICONS = {
            "Sunny": "☀️", "Clear": "🌙", "Partly cloudy": "⛅",
            "Cloudy": "☁️", "Overcast": "☁️", "Mist": "🌫️",
            "Fog": "🌫️", "Light rain": "🌦️", "Rain": "🌧️",
            "Heavy rain": "⛈️", "Thunderstorm": "⛈️", "Snow": "❄️",
            "Light snow": "🌨️", "Sleet": "🌨️", "Drizzle": "🌦️",
        }
        def _icon(desc):
            for k, v in WEATHER_ICONS.items():
                if k.lower() in desc.lower():
                    return v
            return "🌤️"
        result = {
            "current": {
                "temp_c": int(current.get("temp_C", 0)),
                "feels_like_c": int(current.get("FeelsLikeC", 0)),
                "desc": current.get("weatherDesc", [{}])[0].get("value", ""),
                "wind_kph": int(current.get("windspeedKmph", 0)),
                "humidity": int(current.get("humidity", 0)),
                "icon": _icon(current.get("weatherDesc", [{}])[0].get("value", "")),
            },
            "forecast": [],
        }
        for f in forecasts[:3]:
            day_desc = ""
            for h in f.get("hourly", []):
                d = h.get("weatherDesc", [{}])[0].get("value", "")
                if d:
                    day_desc = d
                    break
            result["forecast"].append({
                "date": f.get("date", ""),
                "max_c": int(f.get("maxtempC", 0)),
                "min_c": int(f.get("mintempC", 0)),
                "desc": day_desc,
                "icon": _icon(day_desc),
            })
        _weather_cache["data"] = result
        _weather_cache["ts"] = now
        return result
    except Exception:
        pass
    return {"current": {}, "forecast": []}


# ─── WIFI / PLACES / GEOFENCE API ENDPOINTS ─────────────────────

@app.get("/api/wifi/scan")
async def api_wifi_scan():
    """Scan nearby WiFi networks."""
    result = await wifi_fingerprint()
    return result

@app.get("/api/places/nearby")
async def api_nearby_places(type: str = "", keyword: str = "", radius: int = 1500):
    """Find nearby places (restaurants, shops, etc)."""
    loc = await current_location()
    if not loc:
        return {"error": "No GPS fix", "places": []}
    places = await nearby_places(loc[0], loc[1], place_type=type, keyword=keyword, radius_m=radius)
    return {"places": places, "count": len(places), "location": {"lat": loc[0], "lon": loc[1]}}

@app.get("/api/places/search")
async def api_search_places(q: str = ""):
    """Search nearby places by text query."""
    if not q:
        return {"error": "Missing query parameter ?q=", "places": []}
    places = await search_nearby(q)
    return {"places": places, "count": len(places), "query": q}

@app.get("/api/geofence/list")
async def api_geofence_list():
    """List all geofences."""
    return {"geofences": list_geofences()}

@app.post("/api/geofence/add")
async def api_geofence_add(name: str = "", lat: float = 0, lon: float = 0, radius: int = 100, tags: str = ""):
    """Add a geofence zone."""
    if not name:
        return {"error": "Missing name"}
    tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else []
    msg = add_geofence(name, lat, lon, radius, tag_list)
    return {"ok": True, "message": msg}

@app.post("/api/geofence/remove")
async def api_geofence_remove(name: str = ""):
    """Remove a geofence."""
    if not name:
        return {"error": "Missing name"}
    removed = remove_geofence(name)
    return {"ok": removed, "message": f"Removed '{name}'" if removed else f"Geofence '{name}' not found"}

@app.get("/api/location/context")
async def api_location_context():
    """Get full location context: GPS + WiFi + nearby places."""
    loc = await current_location()
    wifi = await wifi_fingerprint()
    places = []
    if loc:
        places = await nearby_places(loc[0], loc[1], radius_m=500)
    fences = list_geofences()
    inside = [f["name"] for f in fences if _GEOFENCE_STATES.get(f["name"], False)]
    return {
        "location": {"lat": loc[0], "lon": loc[1], "name": loc[2]} if loc else None,
        "wifi": wifi,
        "nearby_places": places[:10],
        "inside_geofences": inside,
    }


@app.get("/api/dashboard")
async def get_dashboard():
    now = time.time()
    import datetime as _dt
    local_now = _dt.datetime.fromtimestamp(now)
    activity = None
    state = ACTIVITY_STATE
    if state["active"]:
        elapsed = now - state["start_time"]
        dist_m = state["total_distance_m"]
        speed_kph = state["current_speed_mps"] * 3.6
        activity = {
            "type": state["type"],
            "elapsed_sec": int(elapsed),
            "dist_km": round(dist_m / 1000, 2),
            "speed_kph": round(speed_kph, 1),
        }
    weather = await get_weather()
    return {
        "time": local_now.strftime("%H:%M"),
        "date": local_now.strftime("%A, %d %B"),
        "weather": weather,
        "activity": activity,
        "location": _last_location_name,
    }

# ─── FRONTEND INTERFACE ─────────────────────────────────────────
HTML_PAGE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0,maximum-scale=1.0,user-scalable=no">
<title>Lilly — Digital Companion</title>
<style>
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
body,html{width:100%;height:100%;overflow:hidden;font-family:-apple-system,'Segoe UI',system-ui,sans-serif;color:#5d4e6d;background:#f0e6ef}

/* ─── Aurora Background ─── */
#aurora{position:fixed;top:0;left:0;width:100%;height:100%;z-index:0;overflow:hidden}
#aurora .layer{position:absolute;top:-20%;left:-20%;width:140%;height:140%;border-radius:50%;filter:blur(80px);opacity:0.55;animation:auroraDrift 12s ease-in-out infinite alternate}
#aurora .l1{background:radial-gradient(ellipse at 30% 40%,#f5d5e0 0%,transparent 70%);animation-delay:0s}
#aurora .l2{background:radial-gradient(ellipse at 70% 60%,#d4e8f5 0%,transparent 70%);animation-delay:-4s}
#aurora .l3{background:radial-gradient(ellipse at 50% 30%,#d5f0e6 0%,transparent 70%);animation-delay:-8s}
#aurora .l4{background:radial-gradient(ellipse at 20% 70%,#f5e6d4 0%,transparent 70%);animation-delay:-2s;opacity:0.3}
@keyframes auroraDrift{0%{transform:translate(0,0) scale(1)}100%{transform:translate(4%,3%) scale(1.12)}}
#aurora::after{content:'';position:absolute;top:0;left:0;width:100%;height:100%;background:linear-gradient(180deg,rgba(240,230,239,0.3) 0%,transparent 40%,transparent 70%,rgba(240,230,239,0.4) 100%)}

/* ─── Canvas ─── */
canvas{display:block;position:absolute;top:0;left:0;z-index:1;pointer-events:none}

/* ─── Speech Bubble ─── */
#speechBubble{position:absolute;top:12%;left:50%;transform:translateX(-50%);width:82%;max-width:480px;max-height:200px;overflow-y:auto;background:rgba(255,255,255,0.55);backdrop-filter:blur(24px);-webkit-backdrop-filter:blur(24px);border:1px solid rgba(255,255,255,0.6);padding:18px 24px;border-radius:22px;font-size:17px;color:#5d4e6d;font-weight:450;text-align:center;display:none;z-index:20;line-height:1.6;box-shadow:0 8px 40px rgba(180,140,180,0.15);transition:opacity 0.2s}
#speechBubble pre{background:rgba(93,78,109,0.06);border:1px solid rgba(93,78,109,0.12);border-radius:10px;padding:12px 14px;margin:8px 0;text-align:left;font-size:13px;max-height:120px;overflow-y:auto}
#speechBubble pre code{font-family:'SF Mono',Consolas,monospace;font-size:12.5px;color:#4a3a5c;white-space:pre-wrap}
#speechBubble .code-toolbar{position:relative;display:flex;justify-content:flex-end;gap:4px;margin-bottom:4px}
#speechBubble::after{content:'';position:absolute;bottom:-8px;left:50%;transform:translateX(-50%);border-width:8px 10px 0;border-style:solid;border-color:rgba(255,255,255,0.55) transparent transparent transparent}

/* ─── Phone Panel ─── */
#phonePanel{display:none}
/* ─── Status Bar ─── */
#statusBar{position:absolute;top:16px;right:20px;z-index:30;display:flex;gap:12px;align-items:center;font-size:12px;color:rgba(93,78,109,0.5)}
.btn-tts-replay{background:rgba(255,255,255,0.4);border:1px solid rgba(255,255,255,0.5);border-radius:50%;width:34px;height:34px;cursor:pointer;display:flex;align-items:center;justify-content:center;transition:all 0.25s;color:rgba(93,78,109,0.5)}
.btn-tts-replay:hover{background:rgba(184,169,201,0.3);color:#5d4e6d;transform:scale(1.1)}
.btn-tts-replay:active{transform:scale(0.95)}
.indicator{width:8px;height:8px;border-radius:50%;background:rgba(93,78,109,0.15)}
.indicator.active{background:#b8a9c9;box-shadow:0 0 10px rgba(184,169,201,0.5);animation:pulse 2s infinite}

/* ─── Mood Badge ─── */
#moodBadge{position:absolute;top:16px;left:20px;z-index:30;display:flex;gap:6px;align-items:center;font-size:11px;color:rgba(93,78,109,0.5);background:rgba(255,255,255,0.3);backdrop-filter:blur(12px);padding:5px 12px;border-radius:20px;border:1px solid rgba(255,255,255,0.4);transition:all 0.5s}
#moodDot{width:6px;height:6px;border-radius:50%;background:#c0b0d0;transition:background 0.6s}

/* ─── Thinking Indicator ─── */
#thinkingDots{position:absolute;top:38%;left:50%;transform:translateX(-50%);z-index:20;display:none;gap:8px;align-items:center;justify-content:center}
#thinkingDots span{width:8px;height:8px;border-radius:50%;background:rgba(139,122,158,0.5);animation:thinkBounce 1.2s ease-in-out infinite}
#thinkingDots span:nth-child(2){animation-delay:0.2s}
#thinkingDots span:nth-child(3){animation-delay:0.4s}
@keyframes thinkBounce{0%,60%,100%{transform:translateY(0);opacity:0.3}30%{transform:translateY(-14px);opacity:1}}

/* ─── Streaming Indicator ─── */
.stream-indicator{position:absolute;bottom:8px;right:12px;font-size:10px;color:rgba(93,78,109,0.6);animation:pulse 1.5s infinite}

@keyframes pulse{0%{opacity:1}50%{opacity:0.5}100%{opacity:1}}

/* ─── Input Panel ─── */
.input-panel{position:absolute;bottom:28px;left:50%;transform:translateX(-50%);width:92%;max-width:620px;z-index:10;background:rgba(255,255,255,0.45);backdrop-filter:blur(24px);-webkit-backdrop-filter:blur(24px);border:1px solid rgba(255,255,255,0.6);border-radius:28px;padding:10px;display:flex;flex-wrap:wrap;gap:8px;align-items:center;box-shadow:0 4px 30px rgba(180,140,180,0.12)}
.input-panel input{flex:1;background:rgba(255,255,255,0.4);border:none;outline:none;border-radius:16px;font-size:15px;padding:12px 16px;color:#5d4e6d;font-weight:400}
.input-panel input::placeholder{color:rgba(93,78,109,0.3)}
.btn-mic{background:rgba(255,255,255,0.4);border:none;border-radius:50%;width:44px;height:44px;cursor:pointer;display:flex;align-items:center;justify-content:center;transition:all 0.25s}
.btn-mic svg{width:20px;height:20px;fill:rgba(93,78,109,0.4);transition:fill 0.25s}
@keyframes recPulse{0%,100%{box-shadow:0 0 20px rgba(232,90,110,0.5)}50%{box-shadow:0 0 30px rgba(232,90,110,0.8)}}
.btn-mic.recording{background:rgba(232,90,110,0.3);border:2px solid rgba(232,90,110,0.5);animation:recPulse 1.5s ease-in-out infinite}
.btn-mic.recording svg path{fill:#e85a6e}
.btn-clear{background:rgba(255,255,255,0.4);border:none;border-radius:50%;width:44px;height:44px;cursor:pointer;display:flex;align-items:center;justify-content:center;transition:all 0.25s;font-size:18px;color:rgba(93,78,109,0.3)}
.btn-clear:hover{background:rgba(255,255,255,0.6);color:#5d4e6d}

/* ─── Coding Mode Chat (integrated into UI) ─── */
#chatContainer{position:absolute;bottom:80px;left:50%;transform:translateX(-50%);width:92%;max-width:620px;max-height:35vh;z-index:15;background:rgba(255,255,255,0.35);backdrop-filter:blur(20px);-webkit-backdrop-filter:blur(20px);border:1px solid rgba(255,255,255,0.5);border-radius:20px;display:none;flex-direction:column;overflow:hidden;box-shadow:0 4px 20px rgba(180,140,180,0.12)}
#chatContainer.active{display:flex}
#chatMessages{flex:1;overflow-y:auto;padding:12px 14px;display:flex;flex-direction:column;gap:8px;scroll-behavior:smooth}
#chatMessages::-webkit-scrollbar{width:4px}
#chatMessages::-webkit-scrollbar-track{background:transparent;border-radius:2px}
#chatMessages::-webkit-scrollbar-thumb{background:rgba(93,78,109,0.2);border-radius:2px}
.chat-msg{max-width:88%;padding:8px 12px;border-radius:14px;font-size:13px;line-height:1.5;word-wrap:break-word;animation:msgIn 0.2s ease-out}
@keyframes msgIn{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:translateY(0)}}
.chat-msg.user{align-self:flex-end;background:rgba(180,160,200,0.4);color:#5d4e6d;border-bottom-right-radius:4px}
.chat-msg.assistant{align-self:flex-start;background:rgba(255,255,255,0.5);color:#5d4e6d;border-bottom-left-radius:4px}
.chat-msg pre{background:#ffffff;border:1px solid rgba(93,78,109,0.15);border-radius:10px;padding:12px 14px;margin:6px 0 0;overflow-x:auto;font-size:12px;line-height:1.5;font-family:'JetBrains Mono','Fira Code',monospace;color:#1a1a2e;position:relative;box-shadow:0 2px 8px rgba(0,0,0,0.06)}
.chat-msg code{font-family:'JetBrains Mono','Fira Code',monospace;background:rgba(93,78,109,0.08);padding:2px 5px;border-radius:4px;font-size:12px;color:#5d4e6d}
.chat-msg pre code{background:none;padding:0;color:#1a1a2e}
.copy-btn{background:rgba(93,78,109,0.08);border:1px solid rgba(93,78,109,0.15);border-radius:6px;padding:4px 8px;cursor:pointer;font-size:11px;color:#8b7a9e;transition:all 0.2s;display:flex;align-items:center;gap:4px}
.copy-btn:hover{background:rgba(93,78,109,0.15);color:#5d4e6d}
.copy-btn svg{width:12px;height:12px}
.code-toolbar{position:absolute;top:6px;right:6px;display:flex;gap:4px;z-index:2}
pre{position:relative;overflow-x:auto}
#chatHeader{display:flex;justify-content:space-between;align-items:center;padding:8px 14px;background:rgba(255,255,255,0.3);border-bottom:1px solid rgba(255,255,255,0.4)}
#chatTitle{font-size:11px;font-weight:500;color:rgba(93,78,109,0.6);letter-spacing:0.5px;text-transform:uppercase}
#downloadBtn{background:rgba(93,78,109,0.1);border:1px solid rgba(93,78,109,0.2);border-radius:8px;padding:5px 10px;cursor:pointer;font-size:11px;color:#8b7a9e;transition:all 0.2s;font-weight:500}
#downloadBtn:hover{background:rgba(93,78,109,0.2);color:#5d4e6d}
#downloadBtn:disabled{opacity:0.4;cursor:not-allowed}

/* ─── Hive Group Chat ─── */
#hiveContainer{position:absolute;bottom:90px;left:50%;transform:translateX(-50%);width:94%;max-width:640px;max-height:45vh;z-index:16;background:rgba(255,255,255,0.22);backdrop-filter:blur(20px);-webkit-backdrop-filter:blur(20px);border:1px solid rgba(255,255,255,0.35);border-radius:22px;display:none;flex-direction:column;overflow:hidden;box-shadow:0 8px 40px rgba(180,140,180,0.12)}
#hiveContainer.active{display:flex}
#hiveSpheres{display:flex;flex-direction:column;align-items:center;padding:8px 10px 6px;border-bottom:1px solid rgba(255,255,255,0.25);flex-shrink:0}
#hiveModeRow{display:flex;justify-content:center;gap:4px;margin-bottom:6px}
#hiveCharRow{display:flex;justify-content:center;gap:6px;flex-wrap:wrap}
#hiveMessages{flex:1;overflow-y:auto;padding:14px 14px 8px;display:flex;flex-direction:column;gap:8px;scroll-behavior:smooth;background:transparent}
#hiveMessages::-webkit-scrollbar{width:4px}
#hiveMessages::-webkit-scrollbar-track{background:transparent;border-radius:2px}
#hiveMessages::-webkit-scrollbar-thumb{background:rgba(93,78,109,0.2);border-radius:2px}
.hive-msg{display:flex;gap:8px;align-items:flex-start;animation:msgIn 0.25s ease-out}
.hive-msg-avatar{width:32px;height:32px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:18px;flex-shrink:0;background:rgba(255,255,255,0.35);border:2px solid rgba(255,255,255,0.5)}
.hive-msg-body{flex:1;min-width:0}
.hive-msg-name{font-size:10px;font-weight:600;color:rgba(93,78,109,0.55);letter-spacing:0.3px;margin-bottom:3px;text-transform:uppercase}
.hive-msg-text{background:rgba(255,255,255,0.5);border:1px solid rgba(255,255,255,0.4);padding:10px 14px;border-radius:16px;border-top-left-radius:4px;font-size:13px;color:#5d4e6d;line-height:1.55;box-shadow:0 2px 8px rgba(180,140,180,0.06)}
.hive-msg.user{flex-direction:row-reverse}
.hive-msg.user .hive-msg-avatar{background:rgba(184,169,201,0.2);border-color:rgba(184,169,201,0.4)}
.hive-msg.user .hive-msg-text{background:rgba(184,169,201,0.2);border-color:rgba(184,169,201,0.3);border-top-left-radius:16px;border-top-right-radius:4px}
.hive-msg.user .hive-msg-body{display:flex;flex-direction:column;align-items:flex-end}
.hive-msg.user .hive-msg-name{text-align:right}
.hive-typing{display:flex;gap:8px;align-items:center;padding:4px 0;animation:msgIn 0.2s ease-out}
.hive-typing-avatar{width:28px;height:28px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:15px;background:rgba(255,255,255,0.3);border:1.5px solid rgba(255,255,255,0.4)}
.hive-typing-dots{display:flex;gap:4px;padding:8px 12px;background:rgba(255,255,255,0.35);border-radius:12px}
.hive-typing-dots span{width:6px;height:6px;border-radius:50%;background:rgba(139,122,158,0.4);animation:thinkBounce 1.2s ease-in-out infinite}
.hive-typing-dots span:nth-child(2){animation-delay:0.2s}
.hive-typing-dots span:nth-child(3){animation-delay:0.4s}
.hive-alpha-badge{display:inline-block;font-size:8px;font-weight:700;color:rgba(139,122,158,0.7);background:rgba(184,169,201,0.2);border:1px solid rgba(184,169,201,0.3);border-radius:6px;padding:1px 6px;margin-left:6px;vertical-align:middle;letter-spacing:0.5px}
.hive-mode-btn{background:rgba(255,255,255,0.3);border:1px solid rgba(255,255,255,0.4);border-radius:12px;padding:4px 12px;font-size:10px;font-weight:600;color:rgba(93,78,109,0.5);cursor:pointer;transition:all 0.2s;letter-spacing:0.3px}
.hive-mode-btn:hover{background:rgba(255,255,255,0.5);color:rgba(93,78,109,0.7)}
.hive-mode-btn.active{background:rgba(139,122,158,0.25);border-color:rgba(139,122,158,0.5);color:#5d4e6d}
.hive-char-sphere{width:32px;height:32px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:16px;background:rgba(255,255,255,0.3);border:1.5px solid rgba(255,255,255,0.4);transition:all 0.2s;cursor:pointer;position:relative}
.hive-char-sphere:hover{background:rgba(255,255,255,0.5);transform:scale(1.1)}
.hive-char-sphere.selected{border-color:rgba(139,122,158,0.7);box-shadow:0 0 8px rgba(184,169,201,0.4);transform:scale(1.15);background:rgba(184,169,201,0.2)}
.hive-char-sphere.alpha{border-color:rgba(232,180,80,0.7);box-shadow:0 0 6px rgba(232,180,80,0.3)}
.hive-char-sphere .char-tip{position:absolute;bottom:calc(100% + 4px);left:50%;transform:translateX(-50%);font-size:8px;color:rgba(93,78,109,0.5);white-space:nowrap;background:rgba(255,255,255,0.85);padding:2px 6px;border-radius:6px;opacity:0;transition:opacity 0.2s;pointer-events:none}
.hive-char-sphere:hover .char-tip{opacity:1}

/* ─── Start Screen ─── */
#startScreen{position:absolute;top:0;left:0;width:100%;height:100%;background:rgba(240,230,239,1);backdrop-filter:blur(16px);z-index:200;display:flex;flex-direction:column;align-items:center;justify-content:center;cursor:pointer;transition:opacity 0.6s;padding:20px;box-sizing:border-box;overflow-y:auto}
#startScreen h1{font-size:32px;font-weight:600;color:#8b7a9e;margin-bottom:2px;letter-spacing:-0.5px;flex-shrink:0}
#startScreen p{font-size:13px;color:#b8a9c9;font-weight:400;flex-shrink:0;margin-bottom:6px}
#balloon{position:absolute;top:40%;left:50%;transform:translate(-50%,-50%);width:130px;height:130px;background:radial-gradient(circle at 35% 35%,rgba(200,180,220,0.3),transparent 70%);border-radius:50%;animation:float 4s ease-in-out infinite}
@keyframes float{0%,100%{transform:translate(-50%,-50%) translateY(0)}50%{transform:translate(-50%,-50%) translateY(-12px)}}

/* ─── Puppy Canvas ─── */
#pupCanvas{position:absolute;top:0;left:0;width:100%;height:100%;z-index:1;pointer-events:none}

/* ─── Name Tag ─── */
#nameTag{position:absolute;top:18px;left:50%;transform:translateX(-50%);z-index:30}
#nameInput{font-size:12px;color:rgba(93,78,109,0.65);background:rgba(255,255,255,0.3);backdrop-filter:blur(12px);padding:5px 16px;border-radius:16px;border:1px solid rgba(255,255,255,0.35);outline:none;text-align:center;width:140px;transition:all 0.3s;font-weight:400}
#nameInput::placeholder{color:rgba(93,78,109,0.3)}
#nameInput:focus{background:rgba(255,255,255,0.5);border-color:rgba(184,169,201,0.5);width:170px}

/* ─── Vision PiP Overlay ─── */
#pipContainer{position:absolute;bottom:90px;right:14px;z-index:25;width:var(--pip-w,140px);height:var(--pip-h,105px);border-radius:12px;overflow:hidden;border:2px solid rgba(255,255,255,0.5);box-shadow:0 4px 20px rgba(0,0,0,0.1);display:none;cursor:pointer;transition:opacity 0.3s;touch-action:none}
#pipContainer img{width:100%;height:100%;object-fit:cover;display:block}
#pipLabel{position:absolute;bottom:0;left:0;right:0;background:rgba(0,0,0,0.4);color:#fff;font-size:9px;padding:2px 6px;text-align:center;backdrop-filter:blur(4px);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
#pipContainer .dot{position:absolute;top:4px;right:4px;width:6px;height:6px;border-radius:50%;background:#4caf50;box-shadow:0 0 4px rgba(76,175,80,0.6)}
#pipContainer .pip-resize{position:absolute;bottom:0;right:0;width:16px;height:16px;cursor:nwse-resize;opacity:0.4;z-index:3}
#pipContainer .pip-resize::after{content:'';position:absolute;bottom:2px;right:2px;width:8px;height:8px;border-right:2px solid rgba(255,255,255,0.7);border-bottom:2px solid rgba(255,255,255,0.7)}
/* ─── Filter Bar ─── */
#filterBar{position:absolute;right:14px;z-index:26;display:none;gap:4px;background:rgba(255,255,255,0.4);backdrop-filter:blur(12px);-webkit-backdrop-filter:blur(12px);padding:4px 8px;border-radius:16px;border:1px solid rgba(255,255,255,0.5);box-shadow:0 2px 12px rgba(180,140,180,0.12);align-items:center;flex-wrap:nowrap;overflow-x:auto;max-width:calc(100vw - 28px)}
.filter-btn{width:30px;height:30px;min-width:30px;border:none;border-radius:50%;background:transparent;font-size:15px;cursor:pointer;transition:all 0.2s;padding:0;display:flex;align-items:center;justify-content:center}
.filter-btn:hover{background:rgba(139,122,158,0.15)}
.filter-btn.active{background:rgba(139,122,158,0.25);box-shadow:0 0 6px rgba(184,169,201,0.4)}
.filter-label{font-size:9px;color:rgba(93,78,109,0.5);white-space:nowrap;padding:0 4px}
/* ─── Magic Mirror Dashboard ─── */
#dashboard{position:absolute;top:56px;left:20px;z-index:30;background:rgba(255,255,255,0.3);backdrop-filter:blur(16px);-webkit-backdrop-filter:blur(16px);border:1px solid rgba(255,255,255,0.4);border-radius:16px;padding:14px 18px;min-width:160px;max-width:260px;transition:all 0.3s;font-family:'Courier New',monospace;color:rgba(93,78,109,0.8);cursor:pointer}
#dashboard .dash-time{font-size:22px;font-weight:700;color:#5d4e6d;letter-spacing:1px}
#dashboard .dash-date{font-size:11px;color:rgba(93,78,109,0.5);margin-top:2px}
#dashboard .dash-divider{height:1px;background:rgba(184,169,201,0.3);margin:8px 0}
#dashboard .dash-details{max-height:0;overflow:hidden;transition:max-height 0.3s ease-out,opacity 0.3s;opacity:0}
#dashboard.open .dash-details{max-height:300px;opacity:1}
#dashboard .dash-weather-row{display:flex;align-items:center;gap:8px;margin:4px 0}
#dashboard .dash-weather-icon{font-size:18px;line-height:1}
#dashboard .dash-weather-temp{font-size:14px;font-weight:600;color:#5d4e6d}
#dashboard .dash-weather-desc{font-size:10px;color:rgba(93,78,109,0.5)}
#dashboard .dash-weather-wind{font-size:10px;color:rgba(93,78,109,0.4)}
#dashboard .dash-day-label{font-size:10px;color:rgba(93,78,109,0.4);font-weight:600;text-transform:uppercase;letter-spacing:0.5px;margin-top:6px}
#dashboard .dash-activity{display:flex;align-items:center;gap:8px;margin-top:6px;padding:6px 8px;background:rgba(184,169,201,0.1);border-radius:10px;font-size:12px}
#dashboard .dash-activity-icon{font-size:18px}
#dashboard .dash-activity-text{font-size:11px;color:rgba(93,78,109,0.6)}
#dashboard .dash-activity-stats{font-size:10px;color:rgba(93,78,109,0.4)}
#dashboard .dash-location{font-size:10px;color:rgba(93,78,109,0.4);margin-top:6px;word-wrap:break-word}
#dashboard .dash-expand{font-size:9px;color:rgba(93,78,109,0.3);text-align:center;margin-top:4px}
@keyframes dashFadeIn{from{opacity:0;transform:translateY(-4px)}to{opacity:1;transform:translateY(0)}}

/* ─── Pet Heart Feeder ─── */
#petHeartWidget{position:fixed;bottom:100px;left:14px;z-index:25;width:130px;background:rgba(255,255,255,0.45);backdrop-filter:blur(16px);-webkit-backdrop-filter:blur(16px);border:1px solid rgba(255,255,255,0.5);border-radius:20px;padding:8px;display:flex;flex-direction:column;align-items:center;cursor:pointer;transition:all 0.3s;box-shadow:0 4px 24px rgba(180,140,180,0.15)}
#petHeartWidget:hover{background:rgba(255,255,255,0.6);transform:scale(1.04)}
#petHeartWidget canvas{display:block;width:114px;height:114px;border-radius:12px}
#petHeartLabel{font-size:9px;color:rgba(93,78,109,0.5);margin-top:3px;text-align:center;line-height:1.2;letter-spacing:0.3px}
#petHeartLabel span{color:rgba(139,122,158,0.8);font-weight:600}

/* ─── Heard (User Speech) Display ─── */
#heardBubble{position:absolute;bottom:100px;left:50%;transform:translateX(-50%);width:70%;max-width:400px;background:rgba(184,169,201,0.22);backdrop-filter:blur(12px);-webkit-backdrop-filter:blur(12px);border:1px solid rgba(184,169,201,0.3);padding:10px 18px;border-radius:16px;font-size:13px;color:rgba(93,78,109,0.75);font-style:italic;text-align:center;display:none;z-index:18;line-height:1.5;box-shadow:0 2px 12px rgba(180,140,180,0.08);transition:opacity 0.3s;pointer-events:none}
#heardBubble::before{content:'🎤 ';font-style:normal}

/* ─── Start Screen Mic Prompt ─── */
#startScreen .mic-prompt{font-size:13px;color:rgba(93,78,109,0.4);margin-top:16px;display:flex;align-items:center;gap:6px;justify-content:center;flex-shrink:0}
#startScreen .mic-prompt .mic-icon{width:16px;height:16px;border-radius:50%;background:rgba(232,90,110,0.15);display:flex;align-items:center;justify-content:center;animation:micPulse 2s ease-in-out infinite}
#startScreen .mic-prompt .mic-icon svg{width:10px;height:10px;fill:rgba(232,90,110,0.5)}
@keyframes micPulse{0%,100%{box-shadow:0 0 0 0 rgba(232,90,110,0.2)}50%{box-shadow:0 0 0 6px rgba(232,90,110,0)}}

/* ─── Avatar Picker ─── */
#avatarPicker{display:flex;flex-direction:column;align-items:center;gap:0;width:100%;max-width:900px;animation:pickerIn 0.5s cubic-bezier(.34,1.56,.64,1) both;overflow:visible;padding-bottom:20px}
@keyframes pickerIn{from{opacity:0;transform:translateY(20px)}to{opacity:1;transform:translateY(0)}}
#avatarPreviewWrap{width:100%;display:flex;flex-direction:row;align-items:center;justify-content:center;gap:40px;margin-bottom:8px}
/* Outer glow ring — lunar eclipse */
#avatarPreviewRing{width:340px;height:340px;border-radius:50%;display:flex;align-items:center;justify-content:center;padding:16px;background:radial-gradient(circle,transparent 44%,rgba(20,15,30,0.85) 45%,rgba(40,30,55,0.7) 48%,rgba(180,140,200,0.35) 52%,rgba(200,170,220,0.15) 56%,transparent 60%);box-shadow:0 0 0 3px rgba(255,255,255,0.15),0 0 30px 8px rgba(180,140,210,0.25),0 0 60px 16px rgba(160,120,190,0.12),inset 0 0 20px rgba(0,0,0,0.1);flex-shrink:0;transition:width 0.4s cubic-bezier(.34,1.56,.64,1),height 0.4s cubic-bezier(.34,1.56,.64,1),padding 0.4s cubic-bezier(.34,1.56,.64,1);cursor:grab;user-select:none;-webkit-user-select:none;touch-action:none;position:relative}
#avatarPreviewRing:active{cursor:grabbing}
#avatarPreviewRing.dragging{opacity:0.85;transform:scale(0.92);cursor:grabbing;z-index:10}
#avatarPreviewCanvas{width:308px;height:308px;border-radius:50%;display:block;background:transparent;flex-shrink:0;margin:0;padding:0;transition:width 0.4s cubic-bezier(.34,1.56,.64,1),height 0.4s cubic-bezier(.34,1.56,.64,1);pointer-events:none}
#avatarPreviewName{font-size:18px;font-weight:700;color:rgba(93,78,109,0.85);margin-top:6px;letter-spacing:0.5px;text-align:center}
#avatarPreviewRole{font-size:12px;font-weight:500;color:rgba(93,78,109,0.50);margin-top:1px;text-align:center;letter-spacing:0.3px}

/* ─── Login Sphere (drop target) ─── */
#loginSphere{width:120px;height:120px;border-radius:50%;background:radial-gradient(circle at 38% 35%,rgba(200,180,220,0.5),rgba(140,110,180,0.3) 50%,rgba(90,60,130,0.15));border:3px solid rgba(255,255,255,0.35);box-shadow:0 0 20px 4px rgba(180,140,210,0.2),0 0 40px 8px rgba(160,120,190,0.1),inset 0 0 15px rgba(255,255,255,0.15);display:flex;flex-direction:column;align-items:center;justify-content:center;cursor:pointer;flex-shrink:0;transition:transform 0.35s cubic-bezier(.34,1.56,.64,1),box-shadow 0.3s ease,border-color 0.3s ease,opacity 0.4s ease;position:relative;animation:sphereFloat 3.5s ease-in-out infinite}
@keyframes sphereFloat{0%,100%{transform:translateY(0)}50%{transform:translateY(-6px)}}
#loginSphere .sphere-icon{font-size:28px;margin-bottom:2px;filter:drop-shadow(0 2px 4px rgba(0,0,0,0.15));transition:transform 0.3s}
#loginSphere .sphere-label{font-size:9px;font-weight:600;letter-spacing:0.5px;text-transform:uppercase;color:rgba(93,78,109,0.5)}
#loginSphere.drag-over{transform:scale(1.18);border-color:rgba(139,122,158,0.8);box-shadow:0 0 30px 10px rgba(180,140,210,0.45),0 0 60px 20px rgba(160,120,190,0.2),inset 0 0 20px rgba(255,255,255,0.25);animation:none}
#loginSphere.drag-over .sphere-icon{transform:scale(1.15)}
#loginSphere.success{transform:scale(1.25);border-color:rgba(120,200,160,0.9);box-shadow:0 0 40px 15px rgba(120,200,160,0.35),0 0 80px 30px rgba(120,200,160,0.15);animation:none;transition:transform 0.3s cubic-bezier(.34,1.56,.64,1),box-shadow 0.3s ease,border-color 0.3s ease}
#loginSphere .sphere-pulse{position:absolute;top:-6px;left:-6px;right:-6px;bottom:-6px;border-radius:50%;border:2px solid rgba(180,140,210,0.3);animation:spherePulse 2s ease-in-out infinite;pointer-events:none}
@keyframes spherePulse{0%,100%{transform:scale(1);opacity:0.6}50%{transform:scale(1.12);opacity:0}}

/* Drag hint arc between ring and sphere */
#dragHint{display:flex;align-items:center;gap:8px;color:rgba(93,78,109,0.3);font-size:11px;font-weight:500;letter-spacing:0.5px;animation:hintPulse 2.5s ease-in-out infinite}
#dragHint svg{width:32px;height:12px;opacity:0.4}
@keyframes hintPulse{0%,100%{opacity:0.5}50%{opacity:1}}
.picker-section-label{font-size:10px;font-weight:600;letter-spacing:1px;text-transform:uppercase;color:rgba(93,78,109,0.4);margin:10px 0 6px;text-align:center;flex-shrink:0;width:100%}
.avatar-cards{display:flex;gap:14px;justify-content:center;flex-wrap:nowrap;flex-shrink:0;padding:16px 20px;overflow-x:auto;overflow-y:visible;scroll-snap-type:x mandatory;-webkit-overflow-scrolling:touch;scrollbar-width:none;scroll-padding:0 20px}
.avatar-cards::-webkit-scrollbar{display:none}
/* Each card is a circular sphere with double-sphere glow */
.avatar-card{display:flex;flex-direction:column;align-items:center;gap:8px;cursor:pointer;padding:10px;border-radius:50%;border:3px solid rgba(255,255,255,0.55);background:var(--card-bg,rgba(255,255,255,0.22));backdrop-filter:blur(12px);transition:all 0.3s cubic-bezier(.34,1.56,.64,1);-webkit-tap-highlight-color:transparent;position:relative;flex-shrink:0;width:88px;height:88px;justify-content:center;scroll-snap-align:center;box-shadow:inset 0 0 12px rgba(255,255,255,0.3),0 6px 24px rgba(180,140,180,0.15)}
.avatar-card:hover{background:var(--card-bg-hover,rgba(255,255,255,0.40));transform:translateY(-4px) scale(1.06);box-shadow:inset 0 0 12px rgba(255,255,255,0.3),0 12px 36px rgba(180,140,180,0.25)}
.avatar-card.selected{background:var(--card-bg-selected,rgba(255,255,255,0.50));border-color:var(--card-border,rgba(180,155,210,0.7));transform:translateY(-4px) scale(1.08);box-shadow:inset 0 0 16px rgba(255,255,255,0.4),0 16px 40px rgba(160,130,190,0.30)}
/* Canvas inside each sphere — rendered fully by JS */
.avatar-card canvas{width:68px;height:68px;border-radius:50%;display:block;overflow:hidden}
.avatar-card span{font-size:9px;color:rgba(93,78,109,0.65);font-weight:700;letter-spacing:0.4px;text-transform:uppercase;margin-top:1px}
/* Selected tick badge */
.avatar-card.selected::after{content:'✓';position:absolute;top:-2px;right:-2px;width:20px;height:20px;border-radius:50%;background:rgba(139,122,158,0.9);color:#fff;font-size:11px;font-weight:700;display:flex;align-items:center;justify-content:center;line-height:20px;text-align:center;box-shadow:0 2px 10px rgba(0,0,0,0.2)}
.theme-swatches{display:flex;gap:10px;justify-content:center;flex-wrap:wrap;flex-shrink:0}
.theme-swatch{width:34px;height:34px;border-radius:50%;cursor:pointer;border:3px solid transparent;transition:all 0.22s cubic-bezier(.34,1.56,.64,1);box-shadow:0 2px 10px rgba(0,0,0,0.12);-webkit-tap-highlight-color:transparent;flex-shrink:0}
.theme-swatch:hover{transform:scale(1.15);box-shadow:0 4px 18px rgba(0,0,0,0.18)}
.theme-swatch.selected{transform:scale(1.22);border-color:rgba(255,255,255,0.95);box-shadow:0 0 0 3px rgba(139,122,158,0.35),0 6px 20px rgba(0,0,0,0.20)}

/* ─── Mobile Responsive ─── */
@media(max-width:480px){
  #startScreen h1{font-size:24px;margin-bottom:2px}
  #startScreen p{font-size:11px;margin-bottom:4px}
  #avatarPreviewWrap{flex-direction:column;gap:16px}
  #avatarPreviewRing{width:220px;height:220px;padding:12px}
  #avatarPreviewCanvas{width:196px;height:196px}
  #avatarPreviewName{font-size:15px}
  #loginSphere{width:90px;height:90px}
  #loginSphere .sphere-icon{font-size:22px}
  #avatarPicker{max-width:100%;padding:0 4px;padding-bottom:16px}
  .avatar-cards{gap:8px;padding:6px 10px}
  .avatar-card{width:68px;height:68px;padding:6px}
  .avatar-card canvas{width:52px;height:52px}
  .avatar-card span{font-size:8px}
  .theme-swatches{gap:6px}
  .theme-swatch{width:28px;height:28px}
  .picker-section-label{margin:8px 0 4px}
  #hiveContainer{width:96%;max-height:50vh;bottom:76px}
  .hive-msg-avatar{width:28px;height:28px;font-size:15px}
  .hive-msg-text{font-size:12px;padding:8px 12px}
  .hive-msg-name{font-size:9px}
}
@media(max-width:360px){
  #startScreen h1{font-size:20px}
  #avatarPreviewRing{width:180px;height:180px;padding:10px}
  #avatarPreviewCanvas{width:160px;height:160px}
  #avatarPreviewName{font-size:13px}
  #loginSphere{width:75px;height:75px}
  #loginSphere .sphere-icon{font-size:18px}
  .avatar-card{width:60px;height:60px;padding:5px}
  .avatar-card canvas{width:46px;height:46px}
  .avatar-card span{font-size:7px}
  .avatar-cards{gap:6px}
  #hiveContainer{width:98%;max-height:45vh}
}
/* Coding mode: shrink the picker preview to leave room for the code panel */
.coding-shrink #avatarPreviewRing{width:120px;height:120px;padding:8px}
.coding-shrink #avatarPreviewCanvas{width:104px;height:104px}
.coding-shrink #loginSphere{width:70px;height:70px}
.coding-shrink #loginSphere .sphere-icon{font-size:18px}
</style>
</head>
<body>
<div id="startScreen">
  <h1 id="startCharName">Lilly</h1>
  <p id="startSubtitle">your companion</p>

  <!-- ── Avatar + Theme picker (first visit only) ── -->
  <div id="avatarPicker" style="display:none" onclick="event.stopPropagation()">

    <!-- big animated preview + login sphere -->
    <div id="avatarPreviewWrap">
      <div style="display:flex;flex-direction:column;align-items:center">
        <div id="avatarPreviewRing" style="cursor:grab">
          <canvas id="avatarPreviewCanvas" width="196" height="196"></canvas>
        </div>
        <div id="avatarPreviewName">Lilly</div>
        <div id="avatarPreviewRole">Alpha Companion</div>
      </div>
      <div id="dragHint">
        <svg viewBox="0 0 32 12" fill="none"><path d="M2 6h24" stroke="rgba(93,78,109,0.35)" stroke-width="1.5" stroke-dasharray="3 3"/><path d="M24 2l5 4-5 4" stroke="rgba(93,78,109,0.35)" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg>
        drag to enter
      </div>
      <div id="loginSphere">
        <div class="sphere-pulse"></div>
        <div class="sphere-icon">
          <svg width="28" height="28" viewBox="0 0 24 24"><path d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92a5.06 5.06 0 0 1-2.2 3.32v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.1z" fill="#4285F4"/><path d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z" fill="#34A853"/><path d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z" fill="#FBBC05"/><path d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z" fill="#EA4335"/></svg>
        </div>
        <div class="sphere-label">enter</div>
      </div>
    </div>

    <div class="picker-section-label">choose your companion</div>
    <div class="avatar-cards" id="avatarCards">
      <div class="avatar-card selected" data-animal="puppy" onclick="selectAvatarAndAuth('puppy',this)">
        <canvas width="80" height="80" id="previewPuppy"></canvas><span>Lilly</span>
      </div>
      <div class="avatar-card" data-animal="fox" onclick="selectAvatarAndAuth('fox',this)">
        <canvas width="80" height="80" id="previewFox"></canvas><span>Fox</span>
      </div>
      <div class="avatar-card" data-animal="cat" onclick="selectAvatarAndAuth('cat',this)">
        <canvas width="80" height="80" id="previewCat"></canvas><span>Cat</span>
      </div>
      <div class="avatar-card" data-animal="bear" onclick="selectAvatarAndAuth('bear',this)">
        <canvas width="80" height="80" id="previewBear"></canvas><span>Bear</span>
      </div>
      <div class="avatar-card" data-animal="bunny" onclick="selectAvatarAndAuth('bunny',this)">
        <canvas width="80" height="80" id="previewBunny"></canvas><span>Bunny</span>
      </div>
      <div class="avatar-card" data-animal="owl" onclick="selectAvatarAndAuth('owl',this)">
        <canvas width="80" height="80" id="previewOwl"></canvas><span>Owl</span>
      </div>
      <div class="avatar-card" data-animal="deer" onclick="selectAvatarAndAuth('deer',this)">
        <canvas width="80" height="80" id="previewDeer"></canvas><span>Deer</span>
      </div>
      <div class="avatar-card" data-animal="wolf" onclick="selectAvatarAndAuth('wolf',this)">
        <canvas width="80" height="80" id="previewWolf"></canvas><span>Wolf</span>
      </div>
      <div class="avatar-card" data-animal="raccoon" onclick="selectAvatarAndAuth('raccoon',this)">
        <canvas width="80" height="80" id="previewRaccoon"></canvas><span>Raccoon</span>
      </div>
    </div>

    <!-- Character role descriptions -->
    <div id="charDescriptions" style="margin-top:20px;max-width:420px;width:100%;padding:0 16px;box-sizing:border-box">
      <div class="char-desc active" data-animal="puppy">
        <div style="font-size:13px;font-weight:600;color:#8b7a9e;margin-bottom:4px">Lilly — Alpha Companion</div>
        <div style="font-size:11px;color:rgba(93,78,109,0.55);line-height:1.5">The lead agent. Curious, warm, and direct. Handles all conversations, learns your patterns, and coordinates the team.</div>
      </div>
      <div class="char-desc" data-animal="fox" style="display:none">
        <div style="font-size:13px;font-weight:600;color:#8b7a9e;margin-bottom:4px">Fox — Creative Strategist</div>
        <div style="font-size:11px;color:rgba(93,78,109,0.55);line-height:1.5">Sharp and inventive. Excels at creative tasks, storytelling, brainstorming, and finding clever solutions to complex problems.</div>
      </div>
      <div class="char-desc" data-animal="cat" style="display:none">
        <div style="font-size:13px;font-weight:600;color:#8b7a9e;margin-bottom:4px">Cat — Precision Analyst</div>
        <div style="font-size:11px;color:rgba(93,78,109,0.55);line-height:1.5">Keen eye for detail. Handles data analysis, code review, research, and systematic problem-solving with methodical precision.</div>
      </div>
      <div class="char-desc" data-animal="bear" style="display:none">
        <div style="font-size:13px;font-weight:600;color:#8b7a9e;margin-bottom:4px">Bear — Steadfast Guardian</div>
        <div style="font-size:11px;color:rgba(93,78,109,0.55);line-height:1.5">Calm and dependable. Manages routines, reminders, scheduling, and provides grounded support when you need stability.</div>
      </div>
      <div class="char-desc" data-animal="bunny" style="display:none">
        <div style="font-size:13px;font-weight:600;color:#8b7a9e;margin-bottom:4px">Bunny — Energetic Scout</div>
        <div style="font-size:11px;color:rgba(93,78,109,0.55);line-height:1.5">Quick and alert. Handles real-time monitoring, notifications, sensor feeds, and keeps you updated on everything happening around you.</div>
      </div>
      <div class="char-desc" data-animal="owl" style="display:none">
        <div style="font-size:13px;font-weight:600;color:#8b7a9e;margin-bottom:4px">Owl — Wisdom Keeper</div>
        <div style="font-size:11px;color:rgba(93,78,109,0.55);line-height:1.5">Wise and thoughtful. Provides deep knowledge, considers all angles, and offers philosophical guidance drawn from patterns others miss.</div>
      </div>
      <div class="char-desc" data-animal="deer" style="display:none">
        <div style="font-size:13px;font-weight:600;color:#8b7a9e;margin-bottom:4px">Deer — Gentle Healer</div>
        <div style="font-size:11px;color:rgba(93,78,109,0.55);line-height:1.5">Nurturing and calming. Provides emotional support, wellness guidance, and creates safe spaces for reflection and recovery.</div>
      </div>
      <div class="char-desc" data-animal="wolf" style="display:none">
        <div style="font-size:13px;font-weight:600;color:#8b7a9e;margin-bottom:4px">Wolf — Fierce Protector</div>
        <div style="font-size:11px;color:rgba(93,78,109,0.55);line-height:1.5">Bold and loyal. Takes charge in crisis, defends boundaries, and makes tough calls when others hesitate.</div>
      </div>
      <div class="char-desc" data-animal="raccoon" style="display:none">
        <div style="font-size:13px;font-weight:600;color:#8b7a9e;margin-bottom:4px">Raccoon — Tech Tinkerer</div>
        <div style="font-size:11px;color:rgba(93,78,109,0.55);line-height:1.5">Curious and resourceful. Loves gadgets, hacks, DIY solutions, and finding unconventional ways to solve technical problems.</div>
      </div>
    </div>

    <div class="picker-section-label">choose your theme</div>
    <div class="theme-swatches" id="themeSwatches">
      <div class="theme-swatch selected" data-theme="lilac"   style="background:linear-gradient(135deg,#d4b8e0,#f0e6ef)" title="Lilac"   onclick="selectTheme('lilac',this)"></div>
      <div class="theme-swatch"          data-theme="blush"   style="background:linear-gradient(135deg,#e8b4c0,#fce8ed)" title="Blush"   onclick="selectTheme('blush',this)"></div>
      <div class="theme-swatch"          data-theme="sky"     style="background:linear-gradient(135deg,#9ec8e0,#ddf0fa)" title="Sky"     onclick="selectTheme('sky',this)"></div>
      <div class="theme-swatch"          data-theme="mint"    style="background:linear-gradient(135deg,#7ecfb0,#daf4ea)" title="Mint"    onclick="selectTheme('mint',this)"></div>
      <div class="theme-swatch"          data-theme="peach"   style="background:linear-gradient(135deg,#f0c080,#fde8cc)" title="Peach"   onclick="selectTheme('peach',this)"></div>
      <div class="theme-swatch"          data-theme="slate"   style="background:linear-gradient(135deg,#8090b0,#c8d4e8)" title="Slate"   onclick="selectTheme('slate',this)"></div>
    </div>

    <div style="margin-top:18px;font-size:11px;color:rgba(93,78,109,0.35);text-align:center">tap a companion to sign in</div>
  </div>

  <!-- Auth0 Login Gate -->
  <div id="authGate" style="display:none;margin-top:20px;width:86%;max-width:320px;flex-direction:column;align-items:center;gap:10px" onclick="event.stopPropagation()">
    <div id="authGateInner" style="display:flex;flex-direction:column;gap:10px;width:100%;align-items:center">
      <div style="text-align:center;font-size:12px;color:rgba(93,78,109,0.5);margin-bottom:4px">Sign in to continue</div>
      <div id="authGateError" style="font-size:12px;color:#e85a6e;text-align:center;min-height:16px"></div>
      <button onclick="window.location.href='/api/auth0/login'" style="padding:12px 24px;border:none;border-radius:14px;background:#5d4e6d;color:#fff;font-size:14px;font-weight:500;cursor:pointer;transition:all 0.2s">Login with Auth0</button>
      <div style="text-align:center;font-size:11px;color:rgba(93,78,109,0.4);margin-top:4px">Don't have an account? <a href="/api/auth0/login?screen_hint=signup" style="color:#7a6791;text-decoration:underline">Sign up</a></div>
    </div>
  </div>

</div>

<div id="aurora">
  <div class="layer l1"></div>
  <div class="layer l2"></div>
  <div class="layer l3"></div>
  <div class="layer l4"></div>
</div>

<div id="statusBar">
  <button class="btn-tts-replay" id="ttsReplayBtn" title="Replay last speech" onclick="replayLastSpeech()" style="display:none">
    <svg viewBox="0 0 24 24" width="16" height="16"><path d="M3 9v6h4l5 5V4L7 9H3zm13.5 3A4.5 4.5 0 0014 7.97v8.05A4.47 4.47 0 0016.5 12zM14 3.23v2.06A7.007 7.007 0 0119 12a7.007 7.007 0 01-5 6.71v2.06A9.008 9.008 0 0021 12a9.008 9.008 0 00-7-8.77z" fill="currentColor"/></svg>
  </button>
  <span id="convIndicator" style="display:none;font-size:10px;color:rgba(139,122,158,0.7);background:rgba(184,169,201,0.2);padding:3px 8px;border-radius:10px;margin-right:6px">CONVERSING</span>
  <span id="statusLabel">idle</span>
  <div class="indicator" id="micIndicator"></div>
</div>

<div id="moodBadge">
  <span id="moodDot"></span>
  <span id="moodLabel">calm</span>
  <span id="prosodyHint" style="font-size:9px;color:rgba(93,78,109,0.3);margin-left:4px"></span>
</div>

<div id="dashboard" onclick="toggleDashboard()">
  <div class="dash-time" id="dashTime">--:--</div>
  <div class="dash-date" id="dashDate">---</div>
  <div class="dash-details">
    <div class="dash-divider"></div>
    <div id="dashWeather"></div>
    <div id="dashActivity"></div>
    <div class="dash-location" id="dashLocation"></div>
  </div>
  <div class="dash-expand" id="dashExpand">tap for details</div>
</div>

<div id="nameTag"><input type="text" id="nameInput" placeholder="your name..." autocomplete="off" maxlength="24"></div>

<div id="thinkingDots">
  <span></span><span></span><span></span>
</div>

<div id="heardBubble"></div>
<div id="speechBubble"></div>
<canvas id="pupCanvas"></canvas>

<!-- Filter Bar (above PiP) -->
<div id="filterBar">
  <span class="filter-label">Filter</span>
  <button class="filter-btn active" data-filter="none" onclick="setFilter('none')" title="No filter">✕</button>
  <button class="filter-btn" data-filter="puppy_ears" onclick="setFilter('puppy_ears')" title="Puppy ears">🐶</button>
  <button class="filter-btn" data-filter="top_hat" onclick="setFilter('top_hat')" title="Top hat">🎩</button>
  <button class="filter-btn" data-filter="mustache" onclick="setFilter('mustache')" title="Mustache">🥸</button>
  <button class="filter-btn" data-filter="crown" onclick="setFilter('crown')" title="Crown">👑</button>
  <button class="filter-btn" data-filter="sunglasses" onclick="setFilter('sunglasses')" title="Sunglasses">🕶️</button>
  <button class="filter-btn" data-filter="rainbow" onclick="setFilter('rainbow')" title="Rainbow">🌈</button>
  <button class="filter-btn" data-filter="sepia" onclick="setFilter('sepia')" title="Vintage">📷</button>
</div>

<!-- Vision PiP -->
<div id="pipContainer" onclick="reactToCameraView()">
  <img id="pipFeed" alt="Lilly's view">
  <span class="dot"></span>
  <span id="pipLabel">Lilly's view</span>
  <div class="pip-resize" id="pipResize"></div>
</div>

<!-- Pet Heart Feeder -->
<div id="petHeartWidget" title="Activity feeds Lilly!">
  <canvas id="petHeartCanvas" width="114" height="114"></canvas>
  <div id="petHeartLabel"><span id="petBowlCount">0</span>/40 kibble · <span id="petHeartHP">100</span>%</div>
</div>

<div id="chatContainer">
  <div id="chatHeader">
    <span id="chatTitle">Vibe Coding</span>
    <button id="downloadBtn" onclick="downloadProject()" title="Download project as .zip">
      <svg viewBox="0 0 24 24" width="14" height="14" style="vertical-align:middle;margin-right:4px"><path d="M19 9h-4V3H9v6H5l7 7 7-7zM5 18v2h14v-2H5z" fill="currentColor"/></svg>
      Download
    </button>
  </div>
  <div id="chatMessages"></div>
</div>

<div id="hiveContainer">
  <div id="hiveSpheres">
    <div id="hiveModeRow">
      <button class="hive-mode-btn" data-mode="hive" onclick="setHiveMode('hive')">All</button>
      <button class="hive-mode-btn active" data-mode="individual" onclick="setHiveMode('individual')">One</button>
      <button class="hive-mode-btn" data-mode="speaker" onclick="setHiveMode('speaker')">Speaker</button>
      <button id="hiveMuteBtn" class="hive-mode-btn" onclick="toggleHiveMute()" title="Mute/Unmute voice replies">
        <svg viewBox="0 0 24 24" width="12" height="12" style="vertical-align:middle"><path d="M3 9v6h4l5 5V4L7 9H3zm13.5 3c0-1.77-1.02-3.29-2.5-4.03v8.05c1.48-.73 2.5-2.25 2.5-4.02zM14 3.23v2.06c2.89.86 5 3.54 5 6.71s-2.11 5.85-5 6.71v2.06c4.01-.91 7-4.49 7-8.77s-2.99-7.86-7-8.77z" fill="currentColor"/></svg>
        <span id="hiveMuteLabel">Sound</span>
      </button>
    </div>
    <div id="hiveCharRow"></div>
  </div>
  <div id="hiveMessages"></div>
</div>

<div class="input-panel">
  <input type="text" id="userInput" placeholder="Talk to me..." autocomplete="off">
  <button class="btn-clear" id="clearBtn" title="Clear conversation memory">&#x2715;</button>
  <button class="btn-mic" id="micBtn" title="Toggle browser microphone" onclick="toggleBrowserMic()">
    <svg viewBox="0 0 24 24" width="20" height="20"><path d="M12 14c1.66 0 3-1.34 3-3V5c0-1.66-1.34-3-3-3S9 3.34 9 5v6c0 1.66 1.34 3 3 3z" fill="rgba(93,78,109,0.4)"/><path d="M17 11c0 2.76-2.24 5-5 5s-5-2.24-5-5H5c0 3.53 2.61 6.43 6 6.92V21h2v-3.08c3.39-.49 6-3.39 6-6.92h-2z" fill="rgba(93,78,109,0.4)"/></svg>
  </button>
  <button class="btn-mic" id="convBtn" title="Toggle conversation mode" onclick="toggleConversationMode()">
    <svg viewBox="0 0 24 24" width="20" height="20"><path d="M20 2H4c-1.1 0-2 .9-2 2v18l4-4h14c1.1 0 2-.9 2-2V4c0-1.1-.9-2-2-2zm0 14H6l-2 2V4h16v12z" fill="rgba(93,78,109,0.4)"/></svg>
  </button>
  <button class="btn-mic" id="camBtn" title="Toggle camera view" onclick="toggleCameraView()">
    <svg viewBox="0 0 24 24" width="20" height="20"><path d="M12 15.2a3.2 3.2 0 1 0 0-6.4 3.2 3.2 0 0 0 0 6.4z" fill="rgba(93,78,109,0.4)"/><path d="M9 2L7.17 4H4c-1.1 0-2 .9-2 2v12c0 1.1.9 2 2 2h16c1.1 0 2-.9 2-2V6c0-1.1-.9-2-2-2h-3.17L15 2H9zm3 15c-2.76 0-5-2.24-5-5s2.24-5 5-5 5 2.24 5 5-2.24 5-5 5z" fill="rgba(93,78,109,0.4)"/></svg>
  </button>
  <button class="btn-mic" id="codeBtn" title="Toggle coding mode" onclick="toggleCodingMode()">
    <svg viewBox="0 0 24 24" width="20" height="20"><path d="M9.4 16.6L4.8 12l4.6-4.6L8 6l-6 6 6 6 1.4-1.4zm5.2 0l4.6-4.6-4.6-4.6L16 6l6 6-6 6-1.4-1.4z" fill="rgba(93,78,109,0.4)"/></svg>
  </button>
  <button class="btn-mic" id="hiveBtn" title="Toggle hive group chat" onclick="toggleGroupChat()">
    <svg viewBox="0 0 24 24" width="20" height="20"><path d="M20 2H4c-1.1 0-2 .9-2 2v18l4-4h14c1.1 0 2-.9 2-2V4c0-1.1-.9-2-2-2zm0 14H6l-2 2V4h16v12z" fill="rgba(93,78,109,0.4)"/><circle cx="8" cy="10" r="1.5" fill="rgba(93,78,109,0.4)"/><circle cx="12" cy="10" r="1.5" fill="rgba(93,78,109,0.4)"/><circle cx="16" cy="10" r="1.5" fill="rgba(93,78,109,0.4)"/></svg>
  </button>
</div>

<script>

// ─── Avatar & Theme Picker ───────────────────────────────────────

const THEMES = {
  lilac: { bg:'#f0e6ef', aurora1:'#f5d5e0', aurora2:'#d4e8f5', aurora3:'#d5f0e6', accent:'#8b7a9e', text:'#5d4e6d', inputBg:'rgba(255,255,255,0.45)' },
  blush: { bg:'#f7eaed', aurora1:'#f5c8d0', aurora2:'#f5e8d4', aurora3:'#f0d5e0', accent:'#a0607a', text:'#6d3d50', inputBg:'rgba(255,245,247,0.45)' },
  sky:   { bg:'#e6f0f7', aurora1:'#b8d8f0', aurora2:'#d4e8f5', aurora3:'#c8f0e8', accent:'#4a80a0', text:'#2d5068', inputBg:'rgba(240,248,255,0.45)' },
  mint:  { bg:'#e6f5ef', aurora1:'#b8e8d0', aurora2:'#d4f5e8', aurora3:'#d0f0c8', accent:'#3a8a6a', text:'#2d5a44', inputBg:'rgba(240,255,248,0.45)' },
  peach: { bg:'#f7f0e6', aurora1:'#f5d8b0', aurora2:'#f5e8c8', aurora3:'#f0d0b8', accent:'#a07040', text:'#6d4a28', inputBg:'rgba(255,250,240,0.45)' },
  slate: { bg:'#e6eaf2', aurora1:'#b0c0d8', aurora2:'#c8d4e8', aurora3:'#b8c8e0', accent:'#506080', text:'#304050', inputBg:'rgba(235,240,250,0.45)' },
};

let pickerAvatar = localStorage.getItem('lilly_avatar') || 'puppy';
let pickerTheme  = localStorage.getItem('lilly_theme')  || 'lilac';
let selectedAvatar = localStorage.getItem('lilly_avatar') || 'puppy';
let previewFrame = 0, previewRaf = null;

// ── Clay helpers ──────────────────────────────────────────────────
function clayFill(ctx2, color, highlight) {
  // Returns a radial gradient that gives a puffy clay look
  const g = ctx2.createRadialGradient(-0.3,-0.4,0.05, 0,0,1);
  g.addColorStop(0, highlight || lighten(color, 38));
  g.addColorStop(0.55, color);
  g.addColorStop(1,   darken(color, 22));
  return g;
}
function lighten(hex, amt) {
  let n = parseInt(hex.replace('#',''),16);
  let r = Math.min(255,(n>>16)+amt), g = Math.min(255,((n>>8)&0xff)+amt), b = Math.min(255,(n&0xff)+amt);
  return `rgb(${r},${g},${b})`;
}
function darken(hex, amt) { return lighten(hex, -amt); }

// Draw a clay pill background for the whole face canvas
function drawClayBg(ctx2, W, H, color) {
  ctx2.clearRect(0, 0, W, H);
  ctx2.save();
  // Soft drop shadow underneath
  ctx2.shadowColor = darken(color, 35);
  ctx2.shadowBlur  = W * 0.20;
  ctx2.shadowOffsetY = W * 0.05;
  const r = W * 0.47;
  ctx2.fillStyle = color;
  ctx2.beginPath();
  ctx2.arc(W/2, H/2, r, 0, Math.PI*2);
  ctx2.fill();
  ctx2.shadowBlur = 0; ctx2.shadowOffsetY = 0;
  // Gradient sheen over the circle
  const g = ctx2.createRadialGradient(W*0.30, H*0.26, W*0.03, W*0.5, H*0.5, r);
  g.addColorStop(0,   'rgba(255,255,255,0.58)');
  g.addColorStop(0.40,'rgba(255,255,255,0.12)');
  g.addColorStop(1,   'rgba(0,0,0,0.10)');
  ctx2.fillStyle = g;
  ctx2.beginPath();
  ctx2.arc(W/2, H/2, r, 0, Math.PI*2);
  ctx2.fill();
  ctx2.restore();
}

// Shared clay eyes
function drawClayEyes(ctx2, cx, cy, r, frame2) {
  const blinking = (Math.floor(frame2 / 90) % 14 === 0);
  const eyeR = r * 0.12;
  const lx = cx - r*0.26, rx2 = cx + r*0.26, ey = cy;
  ctx2.save();
  if (blinking) {
    ctx2.strokeStyle = 'rgba(60,40,80,0.55)';
    ctx2.lineWidth = r*0.045; ctx2.lineCap = 'round';
    ctx2.beginPath(); ctx2.moveTo(lx-eyeR,ey); ctx2.lineTo(lx+eyeR,ey); ctx2.stroke();
    ctx2.beginPath(); ctx2.moveTo(rx2-eyeR,ey); ctx2.lineTo(rx2+eyeR,ey); ctx2.stroke();
  } else {
    for (const ex of [lx, rx2]) {
      ctx2.shadowColor = 'rgba(80,50,100,0.22)';
      ctx2.shadowBlur = r*0.07;
      ctx2.fillStyle = '#fff';
      ctx2.beginPath(); ctx2.arc(ex, ey, eyeR, 0, Math.PI*2); ctx2.fill();
      ctx2.shadowBlur = 0;
      ctx2.fillStyle = 'rgba(90,60,120,0.15)';
      ctx2.beginPath(); ctx2.arc(ex, ey, eyeR*0.82, 0, Math.PI*2); ctx2.fill();
      ctx2.fillStyle = '#3d2e52';
      ctx2.beginPath(); ctx2.arc(ex, ey, eyeR*0.46, 0, Math.PI*2); ctx2.fill();
      ctx2.fillStyle = 'rgba(255,255,255,0.88)';
      ctx2.beginPath(); ctx2.arc(ex - eyeR*0.12, ey - eyeR*0.14, eyeR*0.22, 0, Math.PI*2); ctx2.fill();
      ctx2.fillStyle = 'rgba(255,255,255,0.55)';
      ctx2.beginPath(); ctx2.arc(ex + eyeR*0.12, ey + eyeR*0.10, eyeR*0.09, 0, Math.PI*2); ctx2.fill();
    }
  }
  ctx2.restore();
}

function drawRaccoonEyes(ctx2, cx, cy, r, frame2) {
  const blinking = (Math.floor(frame2 / 90) % 14 === 0);
  const eyeR = r * 0.15;
  const lx = cx - r*0.26, rx2 = cx + r*0.26, ey = cy;
  ctx2.save();
  if (blinking) {
    ctx2.strokeStyle = 'rgba(40,40,40,0.55)';
    ctx2.lineWidth = r*0.045; ctx2.lineCap = 'round';
    ctx2.beginPath(); ctx2.moveTo(lx-eyeR,ey); ctx2.lineTo(lx+eyeR,ey); ctx2.stroke();
    ctx2.beginPath(); ctx2.moveTo(rx2-eyeR,ey); ctx2.lineTo(rx2+eyeR,ey); ctx2.stroke();
  } else {
    for (const ex of [lx, rx2]) {
      ctx2.shadowColor = 'rgba(40,40,40,0.22)';
      ctx2.shadowBlur = r*0.07;
      ctx2.fillStyle = '#ffffff';
      ctx2.beginPath(); ctx2.arc(ex, ey, eyeR, 0, Math.PI*2); ctx2.fill();
      ctx2.shadowBlur = 0;
      ctx2.fillStyle = 'rgba(40,40,40,0.12)';
      ctx2.beginPath(); ctx2.arc(ex, ey, eyeR*0.82, 0, Math.PI*2); ctx2.fill();
      ctx2.fillStyle = '#2a2a2a';
      ctx2.beginPath(); ctx2.arc(ex, ey, eyeR*0.48, 0, Math.PI*2); ctx2.fill();
      ctx2.fillStyle = 'rgba(255,255,255,0.92)';
      ctx2.beginPath(); ctx2.arc(ex - eyeR*0.14, ey - eyeR*0.16, eyeR*0.24, 0, Math.PI*2); ctx2.fill();
      ctx2.fillStyle = 'rgba(255,255,255,0.6)';
      ctx2.beginPath(); ctx2.arc(ex + eyeR*0.12, ey + eyeR*0.10, eyeR*0.10, 0, Math.PI*2); ctx2.fill();
    }
  }
  ctx2.restore();
}

// Shared clay smile
function drawClaySmile(ctx2, cx, cy, r) {
  ctx2.save();
  ctx2.strokeStyle = 'rgba(120,80,100,0.42)';
  ctx2.lineWidth = r*0.05; ctx2.lineCap = 'round';
  // Left curve
  ctx2.beginPath();
  ctx2.arc(cx - r*0.11, cy + r*0.28, r*0.11, 0.08, Math.PI*0.78);
  ctx2.stroke();
  // Right curve
  ctx2.beginPath();
  ctx2.arc(cx + r*0.11, cy + r*0.28, r*0.11, Math.PI*0.22, Math.PI*0.92);
  ctx2.stroke();
  ctx2.restore();
}

// ── Per-animal clay drawing ──────────────────────────────────────
function drawAnimalFace(ctx2, animal, cx, cy, r, frame2, noBg) {
  const W = ctx2.canvas.width, H = ctx2.canvas.height;
  const breathe = Math.sin(frame2 * 0.04) * (r * 0.018);

  // ── ANIMAL PALETTES ──
  const PAL = {
    puppy:{ bg:'#e8e4f0', head:'#d4d0de', muzzle:'#c8c4d2', ear:'#c4c0cc', earInner:'#f0d8e8', nose:'#d4a0b8' },
    fox:  { bg:'#f5e4d0', head:'#e8905a', muzzle:'#f8e0c0', ear:'#e07840', earInner:'#f8c080', nose:'#c05030' },
    cat:  { bg:'#ece8f4', head:'#c8c0d4', muzzle:'#f0dce8', ear:'#b8b0c4', earInner:'#f0c8dc', nose:'#d898b0' },
    bear: { bg:'#ede0d8', head:'#b09080', muzzle:'#cdb8a8', ear:'#a08070', earInner:'#c8a890', nose:'#7a5a4a' },
    bunny:{ bg:'#eeeaf6', head:'#d4cce0', muzzle:'#c8c0d4', ear:'#ccc4d8', earInner:'#f0cce0', nose:'#e8a8c0' },
    owl:  { bg:'#e8e0d0', head:'#8a7a60', muzzle:'#c8b898', ear:'#7a6a50', earInner:'#c0a878', nose:'#5a4a38' },
    deer: { bg:'#f0e8d8', head:'#c8a888', muzzle:'#e8d8c0', ear:'#b89870', earInner:'#e0c8a8', nose:'#5a4838' },
    wolf: { bg:'#e0dde4', head:'#706878', muzzle:'#a898a8', ear:'#605868', earInner:'#a090a8', nose:'#383040' },
    raccoon:{ bg:'#e0e0e4', head:'#808080', muzzle:'#c0c0c0', ear:'#686868', earInner:'#a8a8a8', nose:'#404040' },
  };
  const p = PAL[animal] || PAL.puppy;

  if(!noBg) drawClayBg(ctx2, W, H, p.bg);

  ctx2.save();
  ctx2.translate(cx, cy + breathe);

  // ── PUPPY ──────────────────────────────────────────────────────
  if (animal === 'puppy') {
    const earWig = Math.sin(frame2 * 0.18) * 0.08;
    const earBob = Math.sin(frame2 * 0.14) * r*0.03;
    // Left floppy ear
    ctx2.save(); ctx2.translate(-r*0.60, -r*0.38 + earBob); ctx2.rotate(-0.26 + earWig);
    ctx2.shadowColor='rgba(80,60,100,0.22)'; ctx2.shadowBlur=r*0.14; ctx2.shadowOffsetY=r*0.06;
    const legL = clayFill(ctx2, p.ear, lighten(p.ear,30));
    ctx2.save(); ctx2.scale(r*0.52, r*1.05); ctx2.beginPath(); ctx2.ellipse(0,0,1,1,0,0,Math.PI*2); ctx2.restore();
    ctx2.fillStyle = legL; ctx2.beginPath(); ctx2.ellipse(0,0,r*0.26,r*0.52,0,0,Math.PI*2); ctx2.fill();
    ctx2.shadowBlur=0; ctx2.shadowOffsetY=0;
    ctx2.fillStyle = p.earInner; ctx2.beginPath(); ctx2.ellipse(0,r*0.06,r*0.14,r*0.36,0,0,Math.PI*2); ctx2.fill();
    ctx2.restore();
    // Right floppy ear
    ctx2.save(); ctx2.translate(r*0.60, -r*0.38 + earBob); ctx2.rotate(0.26 - earWig);
    ctx2.shadowColor='rgba(80,60,100,0.22)'; ctx2.shadowBlur=r*0.14; ctx2.shadowOffsetY=r*0.06;
    const legR = clayFill(ctx2, p.ear, lighten(p.ear,30));
    ctx2.fillStyle = legR; ctx2.beginPath(); ctx2.ellipse(0,0,r*0.26,r*0.52,0,0,Math.PI*2); ctx2.fill();
    ctx2.shadowBlur=0; ctx2.shadowOffsetY=0;
    ctx2.fillStyle = p.earInner; ctx2.beginPath(); ctx2.ellipse(0,r*0.06,r*0.14,r*0.36,0,0,Math.PI*2); ctx2.fill();
    ctx2.restore();
    // Head
    ctx2.shadowColor='rgba(80,60,100,0.20)'; ctx2.shadowBlur=r*0.18; ctx2.shadowOffsetY=r*0.07;
    ctx2.fillStyle = clayFill(ctx2, p.head, lighten(p.head,30));
    ctx2.beginPath(); ctx2.roundRect(-r*0.70,-r*0.46,r*1.40,r*1.08,[r*0.38,r*0.38,r*0.28,r*0.28]); ctx2.fill();
    ctx2.shadowBlur=0; ctx2.shadowOffsetY=0;
    // Muzzle
    ctx2.fillStyle = clayFill(ctx2, p.muzzle, lighten(p.muzzle,22));
    ctx2.beginPath(); ctx2.ellipse(0,r*0.30,r*0.40,r*0.24,0,0,Math.PI*2); ctx2.fill();
    // Nose
    ctx2.shadowColor='rgba(80,40,60,0.25)'; ctx2.shadowBlur=r*0.08;
    ctx2.fillStyle = clayFill(ctx2, p.nose, lighten(p.nose,28));
    ctx2.beginPath(); ctx2.ellipse(0,r*0.14,r*0.11,r*0.08,0,0,Math.PI*2); ctx2.fill();
    ctx2.shadowBlur=0;

  // ── FOX ───────────────────────────────────────────────────────
  } else if (animal === 'fox') {
    // Alert pointed ears — twitch alternately like listening
    const foxTwitch = Math.sin(frame2 * 0.12) * 0.12;
    const foxTwitch2 = Math.sin(frame2 * 0.12 + Math.PI) * 0.12;
    for (const [side, twitch] of [[-1, foxTwitch], [1, foxTwitch2]]) {
      ctx2.save(); ctx2.scale(side,1);
      ctx2.translate(r*0.38, -r*0.52);
      ctx2.rotate(twitch);
      ctx2.translate(-r*0.38, r*0.52);
      ctx2.shadowColor='rgba(100,50,20,0.22)'; ctx2.shadowBlur=r*0.12; ctx2.shadowOffsetY=r*0.04;
      ctx2.fillStyle = clayFill(ctx2, p.ear, lighten(p.ear,28));
      ctx2.beginPath(); ctx2.moveTo(r*0.14,-r*0.40); ctx2.lineTo(r*0.38,-r*0.92); ctx2.lineTo(r*0.64,-r*0.38); ctx2.closePath(); ctx2.fill();
      ctx2.shadowBlur=0;
      ctx2.fillStyle = p.earInner;
      ctx2.beginPath(); ctx2.moveTo(r*0.20,-r*0.42); ctx2.lineTo(r*0.37,-r*0.76); ctx2.lineTo(r*0.54,-r*0.40); ctx2.closePath(); ctx2.fill();
      ctx2.restore();
    }
    // Head
    ctx2.shadowColor='rgba(100,50,20,0.20)'; ctx2.shadowBlur=r*0.18; ctx2.shadowOffsetY=r*0.07;
    ctx2.fillStyle = clayFill(ctx2, p.head, lighten(p.head,28));
    ctx2.beginPath(); ctx2.roundRect(-r*0.70,-r*0.42,r*1.40,r*1.02,[r*0.34,r*0.34,r*0.26,r*0.26]); ctx2.fill();
    ctx2.shadowBlur=0;
    // White muzzle patch
    ctx2.fillStyle = clayFill(ctx2, p.muzzle, lighten(p.muzzle,30));
    ctx2.beginPath(); ctx2.ellipse(0,r*0.18,r*0.44,r*0.38,0,0,Math.PI*2); ctx2.fill();
    // Nose
    ctx2.shadowColor='rgba(80,30,10,0.25)'; ctx2.shadowBlur=r*0.08;
    ctx2.fillStyle = clayFill(ctx2, p.nose, lighten(p.nose,24));
    ctx2.beginPath(); ctx2.ellipse(0,r*0.10,r*0.13,r*0.09,0,0,Math.PI*2); ctx2.fill();
    ctx2.shadowBlur=0;
    // Whisker dots
    ctx2.fillStyle='rgba(100,60,30,0.35)';
    for(const [wx,wy] of [[-r*0.28,r*0.16],[-r*0.38,r*0.22],[-r*0.42,r*0.30],[r*0.28,r*0.16],[r*0.38,r*0.22],[r*0.42,r*0.30]]) {
      ctx2.beginPath(); ctx2.arc(wx,wy,r*0.018,0,Math.PI*2); ctx2.fill();
    }

  // ── CAT ───────────────────────────────────────────────────────
  } else if (animal === 'cat') {
    // Pointy ears — rotate side to side like listening in a direction
    const catListen = Math.sin(frame2 * 0.035) * 0.18;
    const catListen2 = Math.sin(frame2 * 0.035 + 0.6) * 0.18;
    for (const [side, listenAngle] of [[-1, catListen], [1, catListen2]]) {
      ctx2.save(); ctx2.scale(side,1);
      ctx2.translate(r*0.40, -r*0.50);
      ctx2.rotate(listenAngle);
      ctx2.translate(-r*0.40, r*0.50);
      ctx2.shadowColor='rgba(60,40,80,0.20)'; ctx2.shadowBlur=r*0.10;
      ctx2.fillStyle = clayFill(ctx2, p.ear, lighten(p.ear,28));
      ctx2.beginPath(); ctx2.moveTo(r*0.20,-r*0.38); ctx2.lineTo(r*0.46,-r*0.90); ctx2.lineTo(r*0.62,-r*0.36); ctx2.closePath(); ctx2.fill();
      ctx2.shadowBlur=0;
      ctx2.fillStyle = p.earInner;
      ctx2.beginPath(); ctx2.moveTo(r*0.26,-r*0.40); ctx2.lineTo(r*0.45,-r*0.74); ctx2.lineTo(r*0.56,-r*0.38); ctx2.closePath(); ctx2.fill();
      ctx2.restore();
    }
    // Head
    ctx2.shadowColor='rgba(60,40,80,0.18)'; ctx2.shadowBlur=r*0.18; ctx2.shadowOffsetY=r*0.07;
    ctx2.fillStyle = clayFill(ctx2, p.head, lighten(p.head,28));
    ctx2.beginPath(); ctx2.roundRect(-r*0.68,-r*0.42,r*1.36,r*1.04,[r*0.32,r*0.32,r*0.28,r*0.28]); ctx2.fill();
    ctx2.shadowBlur=0; ctx2.shadowOffsetY=0;
    // Muzzle
    ctx2.fillStyle = clayFill(ctx2, p.muzzle, lighten(p.muzzle,22));
    ctx2.beginPath(); ctx2.ellipse(0,r*0.28,r*0.34,r*0.22,0,0,Math.PI*2); ctx2.fill();
    // Nose
    ctx2.shadowColor='rgba(80,30,60,0.22)'; ctx2.shadowBlur=r*0.07;
    ctx2.fillStyle = clayFill(ctx2, p.nose, lighten(p.nose,26));
    ctx2.beginPath(); ctx2.ellipse(0,r*0.13,r*0.10,r*0.07,0,0,Math.PI*2); ctx2.fill();
    ctx2.shadowBlur=0;
    // Cat whiskers (lines)
    ctx2.strokeStyle='rgba(80,60,100,0.28)'; ctx2.lineWidth=r*0.025; ctx2.lineCap='round';
    for(const s of [-1,1]) {
      ctx2.beginPath(); ctx2.moveTo(s*r*0.08,r*0.20); ctx2.lineTo(s*r*0.52,r*0.13); ctx2.stroke();
      ctx2.beginPath(); ctx2.moveTo(s*r*0.08,r*0.26); ctx2.lineTo(s*r*0.52,r*0.26); ctx2.stroke();
    }

  // ── BEAR ──────────────────────────────────────────────────────
  } else if (animal === 'bear') {
    // Round ear pads — lazy rotation
    const bearRotate = Math.sin(frame2 * 0.04) * 0.06;
    for(const [ex, rot] of [[-r*0.58, -bearRotate], [r*0.58, bearRotate]]) {
      ctx2.save();
      ctx2.translate(ex, -r*0.50);
      ctx2.rotate(rot);
      ctx2.shadowColor='rgba(60,40,20,0.22)'; ctx2.shadowBlur=r*0.12;
      ctx2.fillStyle = clayFill(ctx2, p.ear, lighten(p.ear,24));
      ctx2.beginPath(); ctx2.arc(0, 0, r*0.28, 0, Math.PI*2); ctx2.fill();
      ctx2.shadowBlur=0;
      ctx2.fillStyle = clayFill(ctx2, p.earInner, lighten(p.earInner,20));
      ctx2.beginPath(); ctx2.arc(0, 0, r*0.17, 0, Math.PI*2); ctx2.fill();
      ctx2.restore();
    }
    // Head
    ctx2.shadowColor='rgba(60,40,20,0.20)'; ctx2.shadowBlur=r*0.20; ctx2.shadowOffsetY=r*0.08;
    ctx2.fillStyle = clayFill(ctx2, p.head, lighten(p.head,26));
    ctx2.beginPath(); ctx2.roundRect(-r*0.70,-r*0.44,r*1.40,r*1.10,[r*0.42,r*0.42,r*0.32,r*0.32]); ctx2.fill();
    ctx2.shadowBlur=0; ctx2.shadowOffsetY=0;
    // Muzzle snout
    ctx2.fillStyle = clayFill(ctx2, p.muzzle, lighten(p.muzzle,24));
    ctx2.beginPath(); ctx2.ellipse(0,r*0.28,r*0.40,r*0.26,0,0,Math.PI*2); ctx2.fill();
    // Nose
    ctx2.shadowColor='rgba(40,20,10,0.28)'; ctx2.shadowBlur=r*0.08;
    ctx2.fillStyle = clayFill(ctx2, p.nose, lighten(p.nose,20));
    ctx2.beginPath(); ctx2.ellipse(0,r*0.12,r*0.15,r*0.10,0,0,Math.PI*2); ctx2.fill();
    ctx2.shadowBlur=0;

  // ── BUNNY ─────────────────────────────────────────────────────
  } else if (animal === 'bunny') {
    const earSway = Math.sin(frame2 * 0.06) * 0.08;
    const earDroop = Math.sin(frame2 * 0.03) * 0.04;
    // Left tall ear
    ctx2.save(); ctx2.translate(-r*0.30,-r*0.44); ctx2.rotate(-0.14 + earSway + earDroop);
    ctx2.shadowColor='rgba(80,60,100,0.20)'; ctx2.shadowBlur=r*0.12; ctx2.shadowOffsetY=r*0.04;
    ctx2.fillStyle = clayFill(ctx2, p.ear, lighten(p.ear,28));
    ctx2.beginPath(); ctx2.ellipse(0,-r*0.38,r*0.15,r*0.38,0,0,Math.PI*2); ctx2.fill();
    ctx2.shadowBlur=0;
    ctx2.fillStyle = p.earInner;
    ctx2.beginPath(); ctx2.ellipse(0,-r*0.38,r*0.07,r*0.28,0,0,Math.PI*2); ctx2.fill();
    ctx2.restore();
    // Right tall ear
    ctx2.save(); ctx2.translate(r*0.30,-r*0.44); ctx2.rotate(0.14 - earSway - earDroop);
    ctx2.shadowColor='rgba(80,60,100,0.20)'; ctx2.shadowBlur=r*0.12; ctx2.shadowOffsetY=r*0.04;
    ctx2.fillStyle = clayFill(ctx2, p.ear, lighten(p.ear,28));
    ctx2.beginPath(); ctx2.ellipse(0,-r*0.38,r*0.15,r*0.38,0,0,Math.PI*2); ctx2.fill();
    ctx2.shadowBlur=0;
    ctx2.fillStyle = p.earInner;
    ctx2.beginPath(); ctx2.ellipse(0,-r*0.38,r*0.07,r*0.28,0,0,Math.PI*2); ctx2.fill();
    ctx2.restore();
    // Head
    ctx2.shadowColor='rgba(80,60,100,0.18)'; ctx2.shadowBlur=r*0.18; ctx2.shadowOffsetY=r*0.07;
    ctx2.fillStyle = clayFill(ctx2, p.head, lighten(p.head,28));
    ctx2.beginPath(); ctx2.roundRect(-r*0.68,-r*0.44,r*1.36,r*1.08,[r*0.40,r*0.40,r*0.32,r*0.32]); ctx2.fill();
    ctx2.shadowBlur=0; ctx2.shadowOffsetY=0;
    // Chubby muzzle
    ctx2.fillStyle = clayFill(ctx2, p.muzzle, lighten(p.muzzle,22));
    ctx2.beginPath(); ctx2.ellipse(0,r*0.30,r*0.36,r*0.22,0,0,Math.PI*2); ctx2.fill();
    // Nose
    ctx2.shadowColor='rgba(80,40,70,0.22)'; ctx2.shadowBlur=r*0.07;
    ctx2.fillStyle = clayFill(ctx2, p.nose, lighten(p.nose,26));
    ctx2.beginPath(); ctx2.ellipse(0,r*0.14,r*0.10,r*0.07,0,0,Math.PI*2); ctx2.fill();
    ctx2.shadowBlur=0;

  // ── OWL ──────────────────────────────────────────────────────
  } else if (animal === 'owl') {
    // Ear tufts — independent twitch
    const owlTwitch = Math.sin(frame2 * 0.09) * 0.10;
    const owlTwitch2 = Math.sin(frame2 * 0.09 + 1.2) * 0.10;
    for (const [side, twitch] of [[-1, owlTwitch], [1, owlTwitch2]]) {
      ctx2.save(); ctx2.scale(side,1);
      ctx2.translate(r*0.32, -r*0.40);
      ctx2.rotate(twitch);
      ctx2.translate(-r*0.32, r*0.40);
      ctx2.shadowColor='rgba(60,50,30,0.20)'; ctx2.shadowBlur=r*0.10;
      ctx2.fillStyle = clayFill(ctx2, p.ear, lighten(p.ear,24));
      ctx2.beginPath(); ctx2.moveTo(r*0.18,-r*0.38); ctx2.lineTo(r*0.30,-r*0.82); ctx2.lineTo(r*0.50,-r*0.36); ctx2.closePath(); ctx2.fill();
      ctx2.shadowBlur=0;
      ctx2.fillStyle = p.earInner;
      ctx2.beginPath(); ctx2.moveTo(r*0.24,-r*0.40); ctx2.lineTo(r*0.32,-r*0.68); ctx2.lineTo(r*0.44,-r*0.38); ctx2.closePath(); ctx2.fill();
      ctx2.restore();
    }
    // Head (round)
    ctx2.shadowColor='rgba(60,50,30,0.20)'; ctx2.shadowBlur=r*0.18; ctx2.shadowOffsetY=r*0.07;
    ctx2.fillStyle = clayFill(ctx2, p.head, lighten(p.head,26));
    ctx2.beginPath(); ctx2.arc(0, -r*0.06, r*0.68, 0, Math.PI*2); ctx2.fill();
    ctx2.shadowBlur=0; ctx2.shadowOffsetY=0;
    // Facial disc (lighter ring around eyes)
    ctx2.fillStyle = clayFill(ctx2, p.muzzle, lighten(p.muzzle,22));
    ctx2.beginPath(); ctx2.ellipse(0, -r*0.08, r*0.50, r*0.46, 0, 0, Math.PI*2); ctx2.fill();
    // Beak
    ctx2.shadowColor='rgba(60,40,20,0.22)'; ctx2.shadowBlur=r*0.06;
    ctx2.fillStyle = clayFill(ctx2, '#c8a050', lighten('#c8a050',30));
    ctx2.beginPath(); ctx2.moveTo(-r*0.06, r*0.12); ctx2.lineTo(0, r*0.24); ctx2.lineTo(r*0.06, r*0.12); ctx2.closePath(); ctx2.fill();
    ctx2.shadowBlur=0;

  // ── DEER ─────────────────────────────────────────────────────
  } else if (animal === 'deer') {
    // Elegant ears — gentle alert flick
    const deerFlick = Math.sin(frame2 * 0.05) * 0.08;
    const deerFlick2 = Math.sin(frame2 * 0.05 + 0.8) * 0.08;
    for (const [side, flick] of [[-1, deerFlick], [1, deerFlick2]]) {
      ctx2.save(); ctx2.scale(side,1);
      ctx2.translate(r*0.32, -r*0.42);
      ctx2.rotate(flick);
      ctx2.translate(-r*0.32, r*0.42);
      ctx2.shadowColor='rgba(80,60,40,0.20)'; ctx2.shadowBlur=r*0.10;
      ctx2.fillStyle = clayFill(ctx2, p.ear, lighten(p.ear,26));
      ctx2.beginPath(); ctx2.moveTo(r*0.16,-r*0.36); ctx2.lineTo(r*0.32,-r*0.88); ctx2.lineTo(r*0.54,-r*0.34); ctx2.closePath(); ctx2.fill();
      ctx2.shadowBlur=0;
      ctx2.fillStyle = p.earInner;
      ctx2.beginPath(); ctx2.moveTo(r*0.22,-r*0.38); ctx2.lineTo(r*0.34,-r*0.72); ctx2.lineTo(r*0.48,-r*0.36); ctx2.closePath(); ctx2.fill();
      ctx2.restore();
    }
    // Head (oval, slender)
    ctx2.shadowColor='rgba(80,60,40,0.18)'; ctx2.shadowBlur=r*0.18; ctx2.shadowOffsetY=r*0.07;
    ctx2.fillStyle = clayFill(ctx2, p.head, lighten(p.head,26));
    ctx2.beginPath(); ctx2.roundRect(-r*0.58,-r*0.42,r*1.16,r*1.08,[r*0.38,r*0.38,r*0.32,r*0.32]); ctx2.fill();
    ctx2.shadowBlur=0; ctx2.shadowOffsetY=0;
    // Muzzle (lighter, elongated)
    ctx2.fillStyle = clayFill(ctx2, p.muzzle, lighten(p.muzzle,24));
    ctx2.beginPath(); ctx2.ellipse(0,r*0.22,r*0.30,r*0.24,0,0,Math.PI*2); ctx2.fill();
    // Nose
    ctx2.shadowColor='rgba(40,30,20,0.25)'; ctx2.shadowBlur=r*0.06;
    ctx2.fillStyle = clayFill(ctx2, '#3a2a18', lighten('#3a2a18',20));
    ctx2.beginPath(); ctx2.ellipse(0,r*0.10,r*0.08,r*0.06,0,0,Math.PI*2); ctx2.fill();
    ctx2.shadowBlur=0;
    // Antler nubs
    for (const side of [-1,1]) {
      ctx2.fillStyle = clayFill(ctx2, '#a08860', lighten('#a08860',20));
      ctx2.beginPath(); ctx2.arc(side*r*0.30, -r*0.56, r*0.08, 0, Math.PI*2); ctx2.fill();
    }

  // ── WOLF ─────────────────────────────────────────────────────
  } else if (animal === 'wolf') {
    // Triangular pointed ears — attentive rotation
    const wolfPerk = Math.sin(frame2 * 0.045) * 0.07;
    const wolfPerk2 = Math.sin(frame2 * 0.045 + 0.5) * 0.07;
    for (const [side, perk] of [[-1, wolfPerk], [1, wolfPerk2]]) {
      ctx2.save(); ctx2.scale(side,1);
      ctx2.translate(r*0.36, -r*0.42);
      ctx2.rotate(perk);
      ctx2.translate(-r*0.36, r*0.42);
      ctx2.shadowColor='rgba(50,40,60,0.22)'; ctx2.shadowBlur=r*0.12; ctx2.shadowOffsetY=r*0.04;
      ctx2.fillStyle = clayFill(ctx2, p.ear, lighten(p.ear,24));
      ctx2.beginPath(); ctx2.moveTo(r*0.12,-r*0.38); ctx2.lineTo(r*0.36,-r*0.90); ctx2.lineTo(r*0.60,-r*0.36); ctx2.closePath(); ctx2.fill();
      ctx2.shadowBlur=0;
      ctx2.fillStyle = p.earInner;
      ctx2.beginPath(); ctx2.moveTo(r*0.20,-r*0.40); ctx2.lineTo(r*0.36,-r*0.74); ctx2.lineTo(r*0.52,-r*0.38); ctx2.closePath(); ctx2.fill();
      ctx2.restore();
    }
    // Head (slightly elongated)
    ctx2.shadowColor='rgba(50,40,60,0.20)'; ctx2.shadowBlur=r*0.18; ctx2.shadowOffsetY=r*0.07;
    ctx2.fillStyle = clayFill(ctx2, p.head, lighten(p.head,26));
    ctx2.beginPath(); ctx2.roundRect(-r*0.68,-r*0.42,r*1.36,r*1.06,[r*0.34,r*0.34,r*0.28,r*0.28]); ctx2.fill();
    ctx2.shadowBlur=0; ctx2.shadowOffsetY=0;
    // Muzzle (elongated)
    ctx2.fillStyle = clayFill(ctx2, p.muzzle, lighten(p.muzzle,22));
    ctx2.beginPath(); ctx2.ellipse(0,r*0.22,r*0.36,r*0.22,0,0,Math.PI*2); ctx2.fill();
    // Nose (large, dark)
    ctx2.shadowColor='rgba(30,20,30,0.28)'; ctx2.shadowBlur=r*0.08;
    ctx2.fillStyle = clayFill(ctx2, p.nose, lighten(p.nose,18));
    ctx2.beginPath(); ctx2.ellipse(0,r*0.10,r*0.12,r*0.09,0,0,Math.PI*2); ctx2.fill();
    ctx2.shadowBlur=0;
    // Fangs — two small white triangles poking down from the muzzle
    ctx2.fillStyle = '#f8f6fa';
    ctx2.shadowColor='rgba(40,30,50,0.18)'; ctx2.shadowBlur=r*0.04;
    ctx2.beginPath();
    ctx2.moveTo(-r*0.16, r*0.28);
    ctx2.lineTo(-r*0.11, r*0.28);
    ctx2.lineTo(-r*0.13, r*0.40);
    ctx2.closePath(); ctx2.fill();
    ctx2.beginPath();
    ctx2.moveTo(r*0.11, r*0.28);
    ctx2.lineTo(r*0.16, r*0.28);
    ctx2.lineTo(r*0.13, r*0.40);
    ctx2.closePath(); ctx2.fill();
    ctx2.shadowBlur=0;
    // Forehead marking
    ctx2.fillStyle = 'rgba(80,70,90,0.12)';
    ctx2.beginPath(); ctx2.moveTo(-r*0.18,-r*0.32); ctx2.lineTo(0,-r*0.18); ctx2.lineTo(r*0.18,-r*0.32); ctx2.closePath(); ctx2.fill();

  // ── RACCOON ──────────────────────────────────────────────────
  } else if (animal === 'raccoon') {
    // Round ears — curious twitch
    const raccoonTwitch = Math.sin(frame2 * 0.07) * 0.09;
    const raccoonTwitch2 = Math.sin(frame2 * 0.07 + 1.5) * 0.09;
    for (const [[ex], twitch] of [[[-r*0.52], -raccoonTwitch], [[r*0.52], raccoonTwitch2]]) {
      ctx2.save();
      ctx2.translate(ex, -r*0.46);
      ctx2.rotate(twitch);
      ctx2.shadowColor='rgba(40,40,40,0.20)'; ctx2.shadowBlur=r*0.10;
      ctx2.fillStyle = clayFill(ctx2, p.ear, lighten(p.ear,22));
      ctx2.beginPath(); ctx2.arc(0, 0, r*0.22, 0, Math.PI*2); ctx2.fill();
      ctx2.shadowBlur=0;
      ctx2.fillStyle = clayFill(ctx2, p.earInner, lighten(p.earInner,18));
      ctx2.beginPath(); ctx2.arc(0, 0, r*0.13, 0, Math.PI*2); ctx2.fill();
      ctx2.restore();
    }
    // Head (round)
    ctx2.shadowColor='rgba(40,40,40,0.18)'; ctx2.shadowBlur=r*0.18; ctx2.shadowOffsetY=r*0.07;
    ctx2.fillStyle = clayFill(ctx2, p.head, lighten(p.head,24));
    ctx2.beginPath(); ctx2.roundRect(-r*0.68,-r*0.42,r*1.36,r*1.04,[r*0.36,r*0.36,r*0.30,r*0.30]); ctx2.fill();
    ctx2.shadowBlur=0; ctx2.shadowOffsetY=0;
    // Eye mask (dark band across eyes)
    ctx2.fillStyle = 'rgba(40,40,40,0.35)';
    ctx2.beginPath(); ctx2.ellipse(0,-r*0.02,r*0.52,r*0.18,0,0,Math.PI*2); ctx2.fill();
    // Muzzle (lighter)
    ctx2.fillStyle = clayFill(ctx2, p.muzzle, lighten(p.muzzle,26));
    ctx2.beginPath(); ctx2.ellipse(0,r*0.22,r*0.32,r*0.20,0,0,Math.PI*2); ctx2.fill();
    // Nose
    ctx2.shadowColor='rgba(30,30,30,0.25)'; ctx2.shadowBlur=r*0.06;
    ctx2.fillStyle = clayFill(ctx2, p.nose, lighten(p.nose,18));
    ctx2.beginPath(); ctx2.ellipse(0,r*0.12,r*0.10,r*0.07,0,0,Math.PI*2); ctx2.fill();
    ctx2.shadowBlur=0;
  }

  // ── Shared: eyes + smile + blush ──────────────────────────────
  if (animal === 'raccoon') drawRaccoonEyes(ctx2, 0, 0, r, frame2);
  else drawClayEyes(ctx2, 0, 0, r, frame2);
  drawClaySmile(ctx2, 0, 0, r);

  // Rosy cheek blush
  const BLUSH = {
    puppy:'rgba(220,160,180,0.30)', fox:'rgba(230,150,100,0.28)',
    cat:'rgba(210,170,190,0.28)', bear:'rgba(200,160,140,0.26)',
    bunny:'rgba(220,180,200,0.30)', owl:'rgba(210,190,160,0.26)',
    deer:'rgba(220,170,150,0.28)', wolf:'rgba(180,170,190,0.26)',
    raccoon:'rgba(190,185,200,0.26)'
  };
  const blushCol = BLUSH[animal] || BLUSH.puppy;
  ctx2.save();
  for (const side of [-1, 1]) {
    ctx2.fillStyle = blushCol;
    ctx2.beginPath();
    ctx2.ellipse(side * r * 0.38, r * 0.18, r * 0.12, r * 0.08, 0, 0, Math.PI * 2);
    ctx2.fill();
  }
  ctx2.restore();

  ctx2.restore();
}

function getCanvasSize() {
  const w = window.innerWidth;
  if (w > 768) return { card: 88, display: 60, preview: 308 };
  if (w > 480) return { card: 78, display: 54, preview: 260 };
  return { card: 68, display: 48, preview: 200 };
}

function updateCanvasResolutions() {
  const s = getCanvasSize();
  const chars = ['Puppy','Fox','Cat','Bear','Bunny','Owl','Deer','Wolf','Raccoon'];
  chars.forEach(name => {
    const c = document.getElementById('preview' + name);
    if (c) { c.width = s.card; c.height = s.card; }
  });
  const prev = document.getElementById('avatarPreviewCanvas');
  if (prev) { prev.width = s.preview; prev.height = s.preview; }
}

let _resizeTimer;
window.addEventListener('resize', () => {
  clearTimeout(_resizeTimer);
  _resizeTimer = setTimeout(updateCanvasResolutions, 200);
});

function renderAvatarCard(animal) {
  const c = document.getElementById('preview' + animal.charAt(0).toUpperCase() + animal.slice(1));
  if (!c) return;
  const cx2 = c.getContext('2d');
  const W = c.width, H = c.height;
  drawAnimalFace(cx2, animal, W/2, H/2, Math.min(W,H)*0.43, previewFrame);
}

function renderPreviewCanvas() {
  const c = document.getElementById('avatarPreviewCanvas');
  if (!c) return;
  const cx2 = c.getContext('2d');
  const W = c.width, H = c.height;
  drawAnimalFace(cx2, pickerAvatar, W/2, H/2, Math.min(W,H)*0.41, previewFrame);
}

function pickerLoop() {
  previewFrame++;
  renderPreviewCanvas();
  ['puppy','fox','cat','bear','bunny','owl','deer','wolf','raccoon'].forEach(renderAvatarCard);
  previewRaf = requestAnimationFrame(pickerLoop);
}

function selectAvatar(animal, el) {
  pickerAvatar = animal;
  document.querySelectorAll('.avatar-card').forEach(c => c.classList.remove('selected'));
  el.classList.add('selected');
  const nameEl = document.getElementById('avatarPreviewName');
  const roleEl = document.getElementById('avatarPreviewRole');
  if (nameEl) nameEl.textContent = CHAR_NAMES[animal] || animal;
  if (roleEl) roleEl.textContent = CHAR_ROLES[animal] || '';
  const h1 = document.getElementById('startCharName');
  if (h1) h1.textContent = CHAR_NAMES[animal] || animal;
}

const CHAR_NAMES = {puppy:'Lilly',fox:'Fox',cat:'Cat',bear:'Bear',bunny:'Bunny',owl:'Owl',deer:'Deer',wolf:'Wolf',raccoon:'Raccoon'};
const CHAR_ROLES = {puppy:'Alpha Companion',fox:'Creative Strategist',cat:'Precision Analyst',bear:'Steadfast Guardian',bunny:'Energetic Scout',owl:'Wisdom Keeper',deer:'Gentle Healer',wolf:'Fierce Protector',raccoon:'Tech Tinkerer'};

function selectAvatarAndAuth(animal, el) {
  pickerAvatar = animal;
  selectedAvatar = animal;
  localStorage.setItem('lilly_avatar', animal);
  document.querySelectorAll('.avatar-card').forEach(c => c.classList.remove('selected'));
  el.classList.add('selected');
  const nameEl = document.getElementById('avatarPreviewName');
  const roleEl = document.getElementById('avatarPreviewRole');
  if (nameEl) nameEl.textContent = CHAR_NAMES[animal] || animal;
  if (roleEl) roleEl.textContent = CHAR_ROLES[animal] || '';
  const h1 = document.getElementById('startCharName');
  if (h1) h1.textContent = CHAR_NAMES[animal] || animal;
  document.querySelectorAll('.char-desc').forEach(d => d.style.display = 'none');
  const desc = document.querySelector(`.char-desc[data-animal="${animal}"]`);
  if (desc) desc.style.display = 'block';
}

function selectTheme(theme, el) {
  pickerTheme = theme;
  document.querySelectorAll('.theme-swatch').forEach(s => s.classList.remove('selected'));
  el.classList.add('selected');
  applyTheme(theme);
}

function applyTheme(theme) {
  const t = THEMES[theme] || THEMES.lilac;
  document.documentElement.style.setProperty('--accent', t.accent);
  document.documentElement.style.setProperty('--text',   t.text);
  // Aurora layers
  const layers = document.querySelectorAll('#aurora .layer');
  if (layers[0]) layers[0].style.background = `radial-gradient(ellipse at 30% 40%,${t.aurora1} 0%,transparent 70%)`;
  if (layers[1]) layers[1].style.background = `radial-gradient(ellipse at 70% 60%,${t.aurora2} 0%,transparent 70%)`;
  if (layers[2]) layers[2].style.background = `radial-gradient(ellipse at 50% 30%,${t.aurora3} 0%,transparent 70%)`;
  document.body.style.color = t.text;
  document.body.style.background = t.bg;
  // Start screen background tint
  const ss = document.getElementById('startScreen');
  if (ss) ss.style.background = t.bg.replace('#','rgba(') + ',1)';
  // Input panel
  const ip = document.querySelector('.input-panel');
  if (ip) ip.style.background = t.inputBg;
  // Avatar card theme-colored backgrounds
  document.documentElement.style.setProperty('--card-bg', t.aurora1 + '30');
  document.documentElement.style.setProperty('--card-bg-hover', t.aurora1 + '55');
  document.documentElement.style.setProperty('--card-bg-selected', t.aurora1 + '70');
  document.documentElement.style.setProperty('--card-border', t.accent + '99');
  // Avatar card selected border tint
  document.querySelectorAll('.avatar-card.selected').forEach(c => {
    c.style.borderColor = t.accent + '99';
  });
}

function showAvatarPicker() {
  // Hide background UI so it doesn't bleed through
  ['nameTag','statusBar','moodBadge','dashboard','petHeartWidget'].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.style.display = 'none';
  });
  // Seed card + swatch selections from saved prefs
  const savedAnimal = localStorage.getItem('lilly_avatar') || 'puppy';
  const savedTheme  = localStorage.getItem('lilly_theme')  || 'lilac';
  pickerAvatar = savedAnimal;
  pickerTheme  = savedTheme;
  document.querySelectorAll('.avatar-card').forEach(c => {
    c.classList.toggle('selected', c.dataset.animal === savedAnimal);
  });
  document.querySelectorAll('.theme-swatch').forEach(s => {
    s.classList.toggle('selected', s.dataset.theme === savedTheme);
  });
  // Show character description for selected animal
  document.querySelectorAll('.char-desc').forEach(d => d.style.display = 'none');
  const desc = document.querySelector(`.char-desc[data-animal="${savedAnimal}"]`);
  if (desc) desc.style.display = 'block';
  document.getElementById('startSubtitle').textContent = 'choose your companion';
  const h1 = document.getElementById('startCharName');
  if (h1) h1.textContent = CHAR_NAMES[savedAnimal] || savedAnimal;
  document.getElementById('avatarPicker').style.display = 'flex';
  // Set preview name and role for saved character
  const nameEl = document.getElementById('avatarPreviewName');
  const roleEl = document.getElementById('avatarPreviewRole');
  if (nameEl) nameEl.textContent = CHAR_NAMES[savedAnimal] || savedAnimal;
  if (roleEl) roleEl.textContent = CHAR_ROLES[savedAnimal] || '';
  applyTheme(savedTheme);
  updateCanvasResolutions();
  pickerLoop();
}

function confirmPicker() {
  selectedAvatar = pickerAvatar;
  localStorage.setItem('lilly_avatar', pickerAvatar);
  localStorage.setItem('lilly_theme',  pickerTheme);
  localStorage.setItem('lilly_picker_done', '1');
  if (previewRaf) { cancelAnimationFrame(previewRaf); previewRaf = null; }
  const picker = document.getElementById('avatarPicker');
  picker.style.transition = 'opacity 0.3s, transform 0.3s';
  picker.style.opacity = '0';
  picker.style.transform = 'translateY(-12px)';
  document.getElementById('startSubtitle').textContent = 'drag to unlock';
  setTimeout(() => {
    picker.style.display = 'none';
    startAuthFlow();
  }, 320);
}

// ─── Drag-and-drop CAPTCHA with Genie Effect ────────────────────
(function initDragCaptcha() {
  function setup() {
    const ring = document.getElementById('avatarPreviewRing');
    const sphere = document.getElementById('loginSphere');
    if (!ring || !sphere) return;

    let dragging = false;
    let startX = 0, startY = 0;
    let clone = null;
    let orbWrap = null;

    function getPos(e) {
      const t = (e.touches && e.touches.length) ? e.touches[0] :
                (e.changedTouches && e.changedTouches.length) ? e.changedTouches[0] : e;
      return { x: t.clientX, y: t.clientY };
    }

    function sphereCenter() {
      const sr = sphere.getBoundingClientRect();
      return { x: sr.left + sr.width / 2, y: sr.top + sr.height / 2 };
    }

    function isOverSphere(px, py) {
      const sr = sphere.getBoundingClientRect();
      const cx = sr.left + sr.width / 2;
      const cy = sr.top + sr.height / 2;
      const r = Math.max(sr.width, sr.height) / 2 + 30;
      return Math.hypot(px - cx, py - cy) < r;
    }

    function onStart(e) {
      if (e.button && e.button !== 0) return;
      e.preventDefault();
      dragging = true;
      const p = getPos(e);
      startX = p.x; startY = p.y;
      ring.classList.add('dragging');

      // Create floating clone with perspective wrapper for 3D warp
      const rr = ring.getBoundingClientRect();
      orbWrap = document.createElement('div');
      orbWrap.style.cssText = 'position:fixed;z-index:9999;pointer-events:none;' +
        'width:' + rr.width + 'px;height:' + rr.height + 'px;' +
        'left:' + rr.left + 'px;top:' + rr.top + 'px;' +
        'transform-style:preserve-3d;perspective:400px;';
      clone = ring.cloneNode(true);
      clone.id = '';
      clone.style.cssText = 'width:100%;height:100%;border-radius:50%;' +
        'opacity:0.9;transition:none;transform-style:preserve-3d;';
      orbWrap.appendChild(clone);
      document.body.appendChild(orbWrap);
    }

    function onMove(e) {
      if (!dragging) return;
      e.preventDefault();
      const p = getPos(e);
      const dx = p.x - startX;
      const dy = p.y - startY;
      const dist = Math.hypot(dx, dy);
      const sc = sphereCenter();
      const distToSphere = Math.hypot(p.x - sc.x, p.y - sc.y);

      if (orbWrap) {
        // Progressive warp: the closer to sphere, the more distorted
        const maxDist = 400;
        const progress = Math.min(dist / maxDist, 1);
        const sphereProximity = 1 - Math.min(distToSphere / 300, 1);

        // Genie warp: stretch perpendicular to motion, compress along motion
        const angle = Math.atan2(dy, dx);
        const stretch = 1 + progress * 0.3 + sphereProximity * 0.4;
        const squash = 1 - progress * 0.25 - sphereProximity * 0.3;
        const perspectiveTilt = sphereProximity * 25;

        // Scale down as approaching sphere
        const scale = Math.max(0.35, 1 - progress * 0.3 - sphereProximity * 0.35);

        // Slight rotation toward sphere
        const rotY = sphereProximity * perspectiveTilt;

        orbWrap.style.transform = 'translate(' + dx + 'px,' + dy + 'px) scale(' + scale + ')';
        clone.style.transform = 'scaleX(' + squash + ') scaleY(' + stretch + ') rotateY(' + rotY + 'deg)';
        clone.style.opacity = String(Math.max(0.5, 0.9 - sphereProximity * 0.3));
        clone.style.filter = 'blur(' + (sphereProximity * 1.5) + 'px)';
      }

      // Proximity glow on sphere
      if (distToSphere < 200) {
        sphere.classList.add('drag-over');
        const intensity = 1 - distToSphere / 200;
        sphere.style.boxShadow = '0 0 ' + (30 + intensity * 40) + 'px ' + (10 + intensity * 15) + 'px rgba(180,140,210,' + (0.4 + intensity * 0.3) + '), 0 0 60px 20px rgba(160,120,190,' + (0.15 + intensity * 0.15) + ')';
      } else {
        sphere.classList.remove('drag-over');
        sphere.style.boxShadow = '';
      }
    }

    function spawnParticles(cx, cy, count) {
      for (let i = 0; i < count; i++) {
        const spark = document.createElement('div');
        const angle = Math.random() * Math.PI * 2;
        const dist = 20 + Math.random() * 30;
        const size = 2 + Math.random() * 4;
        spark.style.cssText = 'position:fixed;z-index:10000;pointer-events:none;border-radius:50%;' +
          'width:' + size + 'px;height:' + size + 'px;' +
          'background:radial-gradient(circle,rgba(200,180,240,0.9),rgba(140,100,200,0.3));' +
          'left:' + (cx + Math.cos(angle) * dist) + 'px;' +
          'top:' + (cy + Math.sin(angle) * dist) + 'px;' +
          'transition:all 0.6s cubic-bezier(.25,.46,.45,.94);opacity:1;';
        document.body.appendChild(spark);
        // Animate inward then fade
        requestAnimationFrame(() => {
          spark.style.transform = 'translate(' + (-Math.cos(angle) * dist * 2) + 'px,' + (-Math.sin(angle) * dist * 2) + 'px) scale(0)';
          spark.style.opacity = '0';
        });
        setTimeout(() => { if (spark.parentNode) spark.parentNode.removeChild(spark); }, 700);
      }
    }

    function onEnd(e) {
      if (!dragging) return;
      dragging = false;
      ring.classList.remove('dragging');
      sphere.style.boxShadow = '';
      const p = getPos(e);

      if (isOverSphere(p.x, p.y)) {
        // ── Genie suck-in animation ──
        sphere.classList.remove('drag-over');
        sphere.classList.add('success');

        const sc = sphereCenter();

        if (orbWrap) {
          // Multi-stage genie animation using keyframes
          orbWrap.style.transition = 'none';
          orbWrap.style.pointerEvents = 'none';

          // Phase 1: Warp toward sphere (0-400ms)
          // Phase 2: Compress into sphere (400-700ms)
          // Phase 3: Disappear + particles (700-900ms)

          const startRect = orbWrap.getBoundingClientRect();
          const startXf = startRect.left + startRect.width / 2;
          const startYf = startRect.top + startRect.height / 2;
          const endX = sc.x;
          const endY = sc.y;

          // Curved path: arc slightly above the straight line
          const midX = (startXf + endX) / 2;
          const midY = Math.min(startYf, endY) - 40;

          let startTime = null;
          const totalDuration = 850;

          function genieFrame(ts) {
            if (!startTime) startTime = ts;
            const elapsed = ts - startTime;
            const t = Math.min(elapsed / totalDuration, 1);

            // Bezier easing for position (ease-in加速 toward sphere)
            const easeIn = t * t * (3 - 2 * t);
            // Bezier easing for warp (starts slow, accelerates)
            const warpEase = t < 0.5 ? 2 * t * t : -1 + (4 - 2 * t) * t;

            // Quadratic bezier for curved path
            const u = easeIn;
            const curX = (1-u)*(1-u)*startXf + 2*(1-u)*u*midX + u*u*endX;
            const curY = (1-u)*(1-u)*startYf + 2*(1-u)*u*midY + u*u*endY;

            // Progressive genie warp
            const scaleX = 1 - warpEase * 0.7;       // compress horizontally
            const scaleY = 1 + warpEase * 0.5;       // stretch vertically
            const perspRotate = warpEase * 35;         // 3D tilt
            const scale = 1 - warpEase * 0.85;        // shrink
            const opacity = 1 - warpEase * 0.9;
            const blur = warpEase * 3;

            orbWrap.style.left = (curX - startRect.width / 2) + 'px';
            orbWrap.style.top = (curY - startRect.height / 2) + 'px';
            clone.style.transform = 'scaleX(' + scaleX + ') scaleY(' + scaleY + ') rotateY(' + perspRotate + 'deg)';
            clone.style.opacity = String(Math.max(0, opacity));
            clone.style.filter = 'blur(' + blur + 'px)';

            // Spawn particles during the suck-in
            if (t > 0.3 && t < 0.85 && Math.random() > 0.6) {
              spawnParticles(curX, curY, 2);
            }

            if (t < 1) {
              requestAnimationFrame(genieFrame);
            } else {
              // Final burst of particles at the sphere center
              spawnParticles(sc.x, sc.y, 12);
              if (orbWrap && orbWrap.parentNode) orbWrap.parentNode.removeChild(orbWrap);
              orbWrap = null; clone = null;

              // Sphere pulse effect
              sphere.style.transition = 'transform 0.4s cubic-bezier(.34,1.56,.64,1)';
              sphere.style.transform = 'scale(1.4)';
              setTimeout(() => {
                sphere.style.transform = 'scale(0)';
                sphere.style.opacity = '0';
              }, 350);

              setTimeout(() => {
                confirmPicker();
              }, 750);
            }
          }

          requestAnimationFrame(genieFrame);
        } else {
          confirmPicker();
        }
      } else {
        // Failed — spring back with bounce
        if (orbWrap) {
          orbWrap.style.transition = 'all 0.6s cubic-bezier(.34,1.56,.64,1)';
          orbWrap.style.transform = 'translate(0,0) scale(1)';
          if (clone) {
            clone.style.transition = 'all 0.6s cubic-bezier(.34,1.56,.64,1)';
            clone.style.transform = 'scaleX(1) scaleY(1) rotateY(0deg)';
            clone.style.opacity = '0';
            clone.style.filter = 'blur(0px)';
          }
        }
        sphere.classList.remove('drag-over');
        setTimeout(() => {
          if (orbWrap && orbWrap.parentNode) orbWrap.parentNode.removeChild(orbWrap);
          orbWrap = null; clone = null;
        }, 650);
      }
    }

    // Mouse events
    ring.addEventListener('mousedown', onStart);
    document.addEventListener('mousemove', onMove);
    document.addEventListener('mouseup', onEnd);

    // Touch events
    ring.addEventListener('touchstart', onStart, { passive: false });
    document.addEventListener('touchmove', onMove, { passive: false });
    document.addEventListener('touchend', onEnd);
  }

  // Wait for DOM ready
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', setup);
  } else {
    setup();
  }
})();

// Apply saved theme colours immediately on every page load (before picker opens)
(function applySavedTheme() {
  applyTheme(localStorage.getItem('lilly_theme') || 'lilac');
})();

// ─── Auth0 Session Authentication ────────────────────────────────
let currentUser = null;

function startAuthFlow() {
  fetch('/api/auth0/me')
    .then(r => { if (r.ok) return r.json(); throw new Error('not authd'); })
    .then(d => { currentUser = d.user; hideStartScreen(); })
    .catch(() => showAuthGate());
}

function showAuthGate() {
  document.getElementById('authGate').style.display = 'flex';
}

function hideStartScreen() {
  const ss = document.getElementById('startScreen');
  ss.style.pointerEvents = 'none';
  ss.style.opacity = '0';
  setTimeout(() => {
    ss.style.display = 'none';
    // Restore background UI elements
    ['nameTag','statusBar','moodBadge','dashboard','petHeartWidget'].forEach(id => {
      const el = document.getElementById(id);
      if (el) el.style.display = '';
    });
    const pp = document.getElementById('phonePanel');
    if (pp) pp.style.display = 'flex';
    // Set selected avatar
    const saved = localStorage.getItem('lilly_avatar') || 'puppy';
    selectedAvatar = saved;
    try { initApp(); } catch (e) { console.error('initApp:', e); }
  }, 600);
}

// On load: show picker, go straight to app after confirm
const ag = document.getElementById('authGate');
if (ag) ag.style.display = 'none';
showAvatarPicker();



let initDone=false;
function initApp(){
  if(initDone)return;initDone=true;
  try{resizeCanvas()}catch(e){console.error('resizeCanvas:',e)}
  try{drawPup()}catch(e){console.error('drawPup:',e)}
  try{updateUI()}catch(e){console.error('updateUI:',e)}
  try{pollState()}catch(e){console.error('pollState:',e)}
  // Check permissions before starting mic/camera
  _promptPermissionsIfNeeded();
  // Start the browser sensor bridge so all agents can feel the world
  try{if(window.SensorBridge)SensorBridge.init()}catch(e){console.error('SensorBridge:',e)}
}

async function _promptPermissionsIfNeeded() {
  const micGranted  = localStorage.getItem('micPermGranted')    === '1';
  const camGranted  = localStorage.getItem('cameraPermGranted') === '1';

  // Check real browser permission state (doesn't prompt, just queries)
  let micState = 'prompt', camState = 'prompt';
  try {
    const mq = await navigator.permissions.query({ name: 'microphone' });
    micState = mq.state; // 'granted' | 'denied' | 'prompt'
  } catch(_) {}
  try {
    const cq = await navigator.permissions.query({ name: 'camera' });
    camState = cq.state;
  } catch(_) {}

  // Already granted — start silently
  if (micState === 'granted' || micGranted) {
    try { startBrowserMic(); } catch(e) { console.error('startBrowserMic:', e); }
  }
  if ((camState === 'granted' || camGranted) && window.CameraBridge) {
    try { CameraBridge.start().then(() => localStorage.setItem('cameraPermGranted','1')).catch(()=>{}); } catch(e) {}
  }

  // Already denied — show a nudge but don't block
  if (micState === 'denied') {
    _showPermNudge('🎤 Mic blocked', 'Enable microphone in your browser settings so Lilly can hear you.');
    return;
  }

  // Need to prompt — show a friendly modal first
  if (micState === 'prompt' && !micGranted) {
    _showPermModal({
      icon: '🎤',
      title: 'Microphone access',
      body: 'Lilly needs mic access to hear your voice commands. You\'ll see a browser prompt next.',
      allow: async () => {
        try {
          await navigator.mediaDevices.getUserMedia({ audio: true });
          localStorage.setItem('micPermGranted', '1');
          try { startBrowserMic(); } catch(e) {}
          // After mic granted, ask for camera too
          if (camState === 'prompt' && !camGranted) {
            setTimeout(() => _askCameraPermission(camState), 400);
          }
        } catch(e) {
          _showPermNudge('🎤 Mic blocked', 'Microphone was denied. Enable it in browser settings to use voice.');
        }
      },
      skip: () => {
        localStorage.setItem('micPermGranted', 'skipped');
        if (camState === 'prompt' && !camGranted) {
          setTimeout(() => _askCameraPermission(camState), 200);
        }
      }
    });
  } else if (camState === 'prompt' && !camGranted) {
    _askCameraPermission(camState);
  }
}

function _askCameraPermission(camState) {
  if (camState === 'denied') {
    _showPermNudge('📷 Camera blocked', 'Enable camera in browser settings to let Lilly see your surroundings.');
    return;
  }
  _showPermModal({
    icon: '📷',
    title: 'Camera access',
    body: 'Allow camera so Lilly can see and describe your surroundings. Fully optional.',
    allow: async () => {
      try {
        await navigator.mediaDevices.getUserMedia({ video: true });
        localStorage.setItem('cameraPermGranted', '1');
        if (window.CameraBridge) CameraBridge.start().catch(()=>{});
      } catch(e) {
        _showPermNudge('📷 Camera blocked', 'Camera was denied. Enable it in settings if you change your mind.');
      }
    },
    skip: () => { localStorage.setItem('cameraPermGranted', 'skipped'); }
  });
}

function _showPermModal({ icon, title, body, allow, skip }) {
  // Remove any existing modal
  const existing = document.getElementById('permModal');
  if (existing) existing.remove();
  const modal = document.createElement('div');
  modal.id = 'permModal';
  modal.style.cssText = `
    position:fixed;bottom:24px;left:50%;transform:translateX(-50%);
    width:min(92vw,380px);background:rgba(255,255,255,0.96);
    backdrop-filter:blur(20px);border-radius:20px;
    box-shadow:0 8px 40px rgba(93,78,109,0.25);
    padding:22px 20px 18px;z-index:9999;
    font-family:-apple-system,'Segoe UI',system-ui,sans-serif;
    animation:slideUp 0.35s cubic-bezier(.34,1.56,.64,1) both;
  `;
  modal.innerHTML = `
    <style>
      @keyframes slideUp{from{opacity:0;transform:translateX(-50%) translateY(30px)}to{opacity:1;transform:translateX(-50%) translateY(0)}}
    </style>
    <div style="font-size:28px;text-align:center;margin-bottom:8px">${icon}</div>
    <div style="font-size:15px;font-weight:600;color:#5d4e6d;text-align:center;margin-bottom:6px">${title}</div>
    <div style="font-size:13px;color:rgba(93,78,109,0.65);text-align:center;line-height:1.5;margin-bottom:16px">${body}</div>
    <div style="display:flex;gap:10px">
      <button id="permSkip"  style="flex:1;padding:11px;border:1px solid rgba(184,169,201,0.4);border-radius:14px;background:transparent;color:#8b7a9e;font-size:13px;font-weight:500;cursor:pointer">Not now</button>
      <button id="permAllow" style="flex:2;padding:11px;border:none;border-radius:14px;background:linear-gradient(135deg,#b8a9c9,#8b7a9e);color:#fff;font-size:13px;font-weight:600;cursor:pointer">Allow access</button>
    </div>
  `;
  document.body.appendChild(modal);
  document.getElementById('permAllow').onclick = () => { modal.remove(); allow(); };
  document.getElementById('permSkip').onclick  = () => { modal.remove(); skip(); };
  // Auto-dismiss after 30s
  setTimeout(() => { if(modal.parentNode) { modal.remove(); skip(); } }, 30000);
}

function _showPermNudge(title, msg) {
  const n = document.createElement('div');
  n.style.cssText = `
    position:fixed;bottom:16px;left:50%;transform:translateX(-50%);
    width:min(92vw,360px);background:rgba(255,248,230,0.97);
    border:1px solid rgba(240,180,80,0.4);border-radius:14px;
    padding:12px 16px;z-index:9999;font-size:12px;color:#7a6030;
    font-family:-apple-system,sans-serif;display:flex;align-items:center;gap:10px;
    box-shadow:0 4px 20px rgba(0,0,0,0.1);
  `;
  n.innerHTML = `<span style="font-size:18px">⚠️</span><div><strong>${title}</strong><br>${msg}</div><span id="permNudgeClose" style="margin-left:auto;cursor:pointer;font-size:16px;color:#bbb">✕</span>`;
  document.body.appendChild(n);
  document.getElementById('permNudgeClose').onclick = () => n.remove();
  setTimeout(() => { if(n.parentNode) n.remove(); }, 8000);
}

/* ─── Coding Mode Chat ─── */
let codingMode=false;
const chatContainer=document.getElementById('chatContainer');
const chatMessages=document.getElementById('chatMessages');

function toggleCodingMode(){
  codingMode=!codingMode;
  fetch('/api/coding_mode',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({active:codingMode})});
  chatContainer.classList.toggle('active',codingMode);
  document.getElementById('startScreen').classList.toggle('coding-shrink',codingMode);
  if(codingMode){
    chatMessages.innerHTML='';
    displaySpeech('Coding mode on! Mic is live — let\'s build something cool.');
  }else{
    displaySpeech('Coding mode off — still listening!');
  }
}

function addChatMessage(role,content){
  const div=document.createElement('div');
  div.className='chat-msg '+role;
  let html=renderCodeBlocks(content);
  div.innerHTML=html;
  chatMessages.appendChild(div);
  chatMessages.scrollTop=chatMessages.scrollHeight;
}

function renderCodeBlocks(content){
  let html=content.replace(/```(\w+)?\n([\s\S]*?)```/g,(match,lang,code)=>{
    const btnId='copy_'+Date.now()+'_'+Math.random().toString(36).substr(2,5);
    const dlId='dl_'+btnId;
    const ext={python:'py',javascript:'js',js:'js',typescript:'ts',ts:'ts',html:'html',css:'css',json:'json',bash:'sh',shell:'sh',sql:'sql',ruby:'rb',java:'java',go:'go',rust:'rs',c:'c',cpp:'cpp',csharp:'cs',php:'php'}[lang]||'txt';
    const fname=(lang||'code')+'.'+ext;
    return `<pre><div class="code-toolbar"><button class="copy-btn" id="${btnId}" onclick="copyCode('${btnId}')" title="Copy code"><svg viewBox="0 0 24 24" width="14" height="14"><path d="M16 1H4c-1.1 0-2 .9-2 2v14h2V3h12V1zm3 4H8c-1.1 0-2 .9-2 2v14c0 1.1.9 2 2 2h11c1.1 0 2-.9 2-2V7c0-1.1-.9-2-2-2zm0 16H8V7h11v14z" fill="currentColor"/></svg></button><button class="copy-btn" id="${dlId}" onclick="downloadCodeFile('${dlId}')" title="Download file"><svg viewBox="0 0 24 24" width="14" height="14"><path d="M19 9h-4V3H9v6H5l7 7 7-7zM5 18v2h14v-2H5z" fill="currentColor"/></svg></button></div><code data-lang="${lang||''}" data-filename="${fname}">${escapeHtml(code.trim())}</code></pre>`;
  });
  html=html.replace(/`([^`]+)`/g,'<code>$1</code>');
  html=html.replace(/\n/g,'<br>');
  return html;
}

function escapeHtml(text){
  const div=document.createElement('div');
  div.textContent=text;
  return div.innerHTML;
}

function copyCode(btnId){
  const btn=document.getElementById(btnId);
  const code=btn.closest('pre').querySelector('code').textContent;
  navigator.clipboard.writeText(code).then(()=>{
    const originalHTML=btn.innerHTML;
    btn.innerHTML='<svg viewBox="0 0 24 24" width="14" height="14"><path d="M9 16.17L4.83 12l-1.42 1.41L9 19 21 7l-1.41-1.41L9 16.17z" fill="currentColor"/></svg>';
    btn.style.color='#4caf50';
    setTimeout(()=>{btn.innerHTML=originalHTML;btn.style.color='';},1500);
  });
}

function downloadCodeFile(btnId){
  const btn=document.getElementById(btnId);
  const codeEl=btn.closest('pre').querySelector('code');
  const code=codeEl.textContent;
  const fname=codeEl.getAttribute('data-filename')||'code.txt';
  const blob=new Blob([code],{type:'text/plain'});
  const url=URL.createObjectURL(blob);
  const a=document.createElement('a');
  a.href=url;a.download=fname;
  document.body.appendChild(a);a.click();
  document.body.removeChild(a);URL.revokeObjectURL(url);
  btn.innerHTML='<svg viewBox="0 0 24 24" width="14" height="14"><path d="M9 16.17L4.83 12l-1.42 1.41L9 19 21 7l-1.41-1.41L9 16.17z" fill="currentColor"/></svg>';
  btn.style.color='#4caf50';
  setTimeout(()=>{btn.innerHTML='<svg viewBox="0 0 24 24" width="14" height="14"><path d="M19 9h-4V3H9v6H5l7 7 7-7zM5 18v2h14v-2H5z" fill="currentColor"/></svg>';btn.style.color='';},1500);
}

async function downloadProject(){
  const btn=document.getElementById('downloadBtn');
  btn.disabled=true;
  btn.textContent='Downloading...';
  try{
    const r=await fetch('/api/coding_download');
    if(!r.ok){
      const err=await r.json();
      addChatMessage('assistant','⚠️ '+err.error);
      return;
    }
    const blob=await r.blob();
    const url=URL.createObjectURL(blob);
    const a=document.createElement('a');
    a.href=url;
    a.download='lilly-project.zip';
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
    addChatMessage('assistant','📁 Project downloaded!');
  }catch(e){
    addChatMessage('assistant','⚠️ Download failed: '+e.message);
  }finally{
    btn.disabled=false;
    btn.textContent='Download Project';
  }
}

async function sendCodingMessage(text){
  if(!text)return;
  addChatMessage('user',text);
  try{
    const r=await fetch('/api/cmd',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text,avatar:localStorage.getItem('lilly_avatar')||'puppy'})});
    const d=await r.json();
    if(d.reply){
      addChatMessage('assistant',d.reply);
      displaySpeech(d.reply);
    }
    if(d.audio_id)playAudio(d.audio_id);
  }catch(e){
    addChatMessage('assistant','Error: '+e.message);
  }
}

/* ─── Hive Group Chat ─── */
let hiveActive = false;
let hiveMode = 'hive'; // 'hive' | 'individual' | 'speaker'
let hiveSelectedChars = []; // for individual mode
let hiveMuted = false; // mute TTS for group chat
const hiveContainer = document.getElementById('hiveContainer');
const hiveMessages = document.getElementById('hiveMessages');
const HIVE_EMOJIS = {puppy:'🐶',fox:'🦊',cat:'🐱',bear:'🐻',bunny:'🐰',owl:'🦉',deer:'🦌',wolf:'🐺',raccoon:'🦝'};
const HIVE_NAMES = {puppy:'Lilly',fox:'Fox',cat:'Cat',bear:'Bear',bunny:'Bunny',owl:'Owl',deer:'Deer',wolf:'Wolf',raccoon:'Raccoon'};
const HIVE_COLORS = {
  puppy:{bg:'rgba(212,208,222,0.3)',border:'rgba(212,208,222,0.5)'},
  fox:{bg:'rgba(232,144,90,0.2)',border:'rgba(232,144,90,0.4)'},
  cat:{bg:'rgba(200,192,212,0.25)',border:'rgba(200,192,212,0.45)'},
  bear:{bg:'rgba(176,144,128,0.25)',border:'rgba(176,144,128,0.45)'},
  bunny:{bg:'rgba(212,204,224,0.25)',border:'rgba(212,204,224,0.45)'},
  owl:{bg:'rgba(190,170,140,0.25)',border:'rgba(190,170,140,0.45)'},
  deer:{bg:'rgba(210,180,150,0.25)',border:'rgba(210,180,150,0.45)'},
  wolf:{bg:'rgba(160,150,170,0.25)',border:'rgba(160,150,170,0.45)'},
  raccoon:{bg:'rgba(180,175,185,0.25)',border:'rgba(180,175,185,0.45)'},
};

function getHiveHistoryKey(){
  const sel = selectedAvatar || 'puppy';
  return 'hive_history_' + sel;
}

function loadHiveHistory(){
  try{
    return JSON.parse(localStorage.getItem(getHiveHistoryKey()) || '[]');
  }catch(e){return [];}
}

function saveHiveHistory(msgs){
  localStorage.setItem(getHiveHistoryKey(), JSON.stringify(msgs));
}

function clearHiveHistory(){
  localStorage.removeItem(getHiveHistoryKey());
}

function renderHiveHistory(){
  const msgs = loadHiveHistory();
  hiveMessages.innerHTML = '';
  if(msgs.length === 0){
    addHiveSystemMsg('Group chat active. Pick characters below, then type to talk to them.');
    return;
  }
  msgs.forEach(m => {
    if(m.type === 'user') addHiveUserMsg(m.text, true);
    else if(m.type === 'agent') addHiveAgentMsg(m.char, m.name, m.role, m.text, true, m.alpha);
    else if(m.type === 'system') addHiveSystemMsg(m.text);
  });
  hiveMessages.scrollTop = hiveMessages.scrollHeight;
}

function setHiveMode(mode){
  hiveMode = mode;
  hiveSelectedChars = [];
  document.querySelectorAll('.hive-mode-btn').forEach(b => b.classList.toggle('active', b.dataset.mode === mode));
  document.querySelectorAll('.hive-char-sphere').forEach(s => s.classList.remove('selected'));
  const placeholder = inputField;
  if(mode === 'hive') placeholder.placeholder = 'Ask the group...';
  else if(mode === 'individual') placeholder.placeholder = 'Pick characters, then type...';
  else placeholder.placeholder = 'Ask the team...';
}

function toggleHiveMute(){
  hiveMuted = !hiveMuted;
  const btn = document.getElementById('hiveMuteBtn');
  const label = document.getElementById('hiveMuteLabel');
  if(btn) btn.classList.toggle('active', hiveMuted);
  if(label) label.textContent = hiveMuted ? 'Muted' : 'Sound';
}

function toggleGroupChat(){
  hiveActive = !hiveActive;
  hiveContainer.classList.toggle('active', hiveActive);
  const hiveSpheres = document.getElementById('hiveSpheres');
  if(hiveSpheres) hiveSpheres.style.display = hiveActive ? 'flex' : 'none';
  if(hiveActive){
    inputField.placeholder = 'Pick characters, then type...';
    hiveMode = 'individual';
    hiveSelectedChars = [];
    document.querySelectorAll('.hive-mode-btn').forEach(b => b.classList.toggle('active', b.dataset.mode === 'individual'));
    populateHiveSpheres();
    renderHiveHistory();
    inputField.focus();
  }else{
    inputField.placeholder = 'Talk to me...';
  }
}

let _cameraViewActive = false;
let _activeFilter = 'none';
let _pipW = parseInt(localStorage.getItem('lilly_pip_w')) || 140;
let _pipH = parseInt(localStorage.getItem('lilly_pip_h')) || 105;

function setFilter(name){
  _activeFilter = name;
  document.querySelectorAll('.filter-btn').forEach(b => b.classList.toggle('active', b.dataset.filter === name));
  if(name !== 'none' && !FaceFilterEngine._ready && !FaceFilterEngine._loading){
    FaceFilterEngine.init();
  }
  localStorage.setItem('lilly_filter', name);
}

function _positionFilterBar(){
  const bar = document.getElementById('filterBar');
  const pip = document.getElementById('pipContainer');
  if(bar && pip) bar.style.bottom = (parseInt(pip.style.bottom || 90) + _pipH + 6) + 'px';
}

function _initPiPResize(){
  const pip = document.getElementById('pipContainer');
  const handle = document.getElementById('pipResize');
  if(!pip || !handle) return;
  let startDist = 0, startW = _pipW, startH = _pipH;
  function getDist(t){const dx=t[0].clientX-t[1].clientX,dy=t[0].clientY-t[1].clientY;return Math.sqrt(dx*dx+dy*dy)}
  pip.addEventListener('touchstart',(e)=>{
    if(e.touches.length===2){
      e.preventDefault();
      startDist=getDist(e.touches);startW=_pipW;startH=_pipH;
    }
  },{passive:false});
  pip.addEventListener('touchmove',(e)=>{
    if(e.touches.length===2){
      e.preventDefault();
      const scale=getDist(e.touches)/startDist;
      _pipW=Math.max(100,Math.min(400,Math.round(startW*scale)));
      _pipH=Math.max(75,Math.min(300,Math.round(startH*scale)));
      pip.style.setProperty('--pip-w',_pipW+'px');
      pip.style.setProperty('--pip-h',_pipH+'px');
    }
  },{passive:false});
  pip.addEventListener('touchend',()=>{
    localStorage.setItem('lilly_pip_w',_pipW);
    localStorage.setItem('lilly_pip_h',_pipH);
    _positionFilterBar();
  });
  // Mouse drag resize on desktop
  let dragging=false, mx0=0, my0=0;
  handle.addEventListener('mousedown',(e)=>{dragging=true;mx0=e.clientX;my0=e.clientY;e.preventDefault()});
  document.addEventListener('mousemove',(e)=>{
    if(!dragging)return;
    _pipW=Math.max(100,Math.min(400,_pipW+(e.clientX-mx0)));
    _pipH=Math.max(75,Math.min(300,_pipH+(e.clientY-my0)));
    mx0=e.clientX;my0=e.clientY;
    pip.style.setProperty('--pip-w',_pipW+'px');
    pip.style.setProperty('--pip-h',_pipH+'px');
    _positionFilterBar();
  });
  document.addEventListener('mouseup',()=>{
    if(dragging){dragging=false;localStorage.setItem('lilly_pip_w',_pipW);localStorage.setItem('lilly_pip_h',_pipH)}
  });
}

async function toggleCameraView(){
  _cameraViewActive = !_cameraViewActive;
  const pip = document.getElementById('pipContainer');
  const filterBar = document.getElementById('filterBar');
  const camBtnEl = document.getElementById('camBtn');
  if(_cameraViewActive){
    if(!CameraBridge.active) await CameraBridge.start();
    if(pip){
      pip.style.display = 'block';
      pip.style.setProperty('--pip-w', _pipW+'px');
      pip.style.setProperty('--pip-h', _pipH+'px');
      _positionFilterBar();
      if(filterBar) filterBar.style.display = 'flex';
      _initPiPResize();
      // Restore last filter
      const saved = localStorage.getItem('lilly_filter');
      if(saved && saved !== 'none') setFilter(saved);
      // Initialize filter engine if a face filter is active
      if(['puppy_ears','top_hat','mustache','crown','sunglasses'].includes(_activeFilter)){
        FaceFilterEngine.init();
      }
      const bridgeVideo = document.getElementById('cameraBridgeVideo');
      if(bridgeVideo && bridgeVideo.srcObject){
        const snapCanvas = document.createElement('canvas');
        const snapCtx = snapCanvas.getContext('2d');
        function _updatePiPImg(){
          if(!_cameraViewActive || !CameraBridge.active) return;
          if(bridgeVideo.readyState >= 2){
            const vw = bridgeVideo.videoWidth || 640, vh = bridgeVideo.videoHeight || 480;
            // Match canvas to PiP size (2x for retina)
            const cw = Math.max(100, pip.clientWidth) * 2;
            const ch = Math.max(75, pip.clientHeight) * 2;
            if(snapCanvas.width !== cw || snapCanvas.height !== ch){
              snapCanvas.width = cw; snapCanvas.height = ch;
            }
            snapCtx.clearRect(0, 0, cw, ch);
            // Draw video frame, mirrored
            snapCtx.save();
            snapCtx.translate(cw, 0);
            snapCtx.scale(-1, 1);
            snapCtx.drawImage(bridgeVideo, 0, 0, cw, ch);
            snapCtx.restore();
            // Apply color filters (rainbow, sepia)
            if(_activeFilter === 'rainbow'){
              const grad = snapCtx.createLinearGradient(0, 0, cw, 0);
              grad.addColorStop(0,'rgba(255,0,0,0.25)');grad.addColorStop(0.17,'rgba(255,127,0,0.25)');
              grad.addColorStop(0.33,'rgba(255,255,0,0.25)');grad.addColorStop(0.5,'rgba(0,255,0,0.25)');
              grad.addColorStop(0.67,'rgba(0,0,255,0.25)');grad.addColorStop(0.83,'rgba(75,0,130,0.25)');
              grad.addColorStop(1,'rgba(148,0,211,0.25)');
              snapCtx.fillStyle = grad;
              snapCtx.fillRect(0, 0, cw, ch);
            } else if(_activeFilter === 'sepia'){
              snapCtx.fillStyle = 'rgba(112,66,20,0.25)';
              snapCtx.fillRect(0, 0, cw, ch);
            }
            // Face-tracking filters via MediaPipe
            if(['puppy_ears','top_hat','mustache','crown','sunglasses'].includes(_activeFilter)){
              const result = FaceFilterEngine.detect(bridgeVideo);
              if(result && result.faceLandmarks && result.faceLandmarks.length > 0){
                const lm = result.faceLandmarks[0];
                drawFaceFilter(snapCtx, lm, cw, ch, _activeFilter);
              }
            }
            document.getElementById('pipFeed').src = snapCanvas.toDataURL('image/jpeg', 0.65);
          }
          if(window._cameraDescription){
            document.getElementById('pipLabel').textContent = window._cameraDescription;
          }
          requestAnimationFrame(_updatePiPImg);
        }
        _updatePiPImg();
      }
    }
    if(camBtnEl) camBtnEl.classList.add('recording');
  } else {
    CameraBridge.stop();
    if(pip) pip.style.display = 'none';
    if(filterBar) filterBar.style.display = 'none';
    if(camBtnEl) camBtnEl.classList.remove('recording');
  }
}

async function reactToCameraView(){
  try{
    const r = await fetch('/api/vision/react');
    const d = await r.json();
    if(d.reply) displaySpeech(d.reply);
    if(d.audio_id) playAudio(d.audio_id);
  }catch(e){}
}

// ─── Face Filter Drawing Functions ──────────────────────────
function drawFaceFilter(ctx, lm, cw, ch, filter){
  const eyeDist = Math.sqrt(Math.pow(lm[33].x - lm[263].x, 2) + Math.pow(lm[33].y - lm[263].y, 2)) * cw;
  const roll = Math.atan2(lm[263].y - lm[33].y, lm[263].x - lm[33].x);
  const faceH = Math.abs(lm[10].y - lm[152].y) * ch;

  ctx.save();
  if(filter === 'puppy_ears'){
    // Two floppy ears above the head
    const topY = lm[10].y * ch - faceH * 0.15;
    const leftX = lm[234].x * cw;
    const rightX = lm[454].x * cw;
    const earW = eyeDist * 0.45, earH = faceH * 0.35;
    // Left ear
    ctx.save(); ctx.translate(leftX - earW*0.3, topY); ctx.rotate(roll - 0.3);
    ctx.fillStyle = 'rgba(180,140,100,0.85)';
    ctx.beginPath(); ctx.ellipse(0, earH*0.3, earW*0.4, earH*0.55, 0, 0, Math.PI*2); ctx.fill();
    ctx.fillStyle = 'rgba(220,180,150,0.6)';
    ctx.beginPath(); ctx.ellipse(0, earH*0.3, earW*0.22, earH*0.35, 0, 0, Math.PI*2); ctx.fill();
    ctx.restore();
    // Right ear
    ctx.save(); ctx.translate(rightX + earW*0.3, topY); ctx.rotate(roll + 0.3);
    ctx.fillStyle = 'rgba(180,140,100,0.85)';
    ctx.beginPath(); ctx.ellipse(0, earH*0.3, earW*0.4, earH*0.55, 0, 0, Math.PI*2); ctx.fill();
    ctx.fillStyle = 'rgba(220,180,150,0.6)';
    ctx.beginPath(); ctx.ellipse(0, earH*0.3, earW*0.22, earH*0.35, 0, 0, Math.PI*2); ctx.fill();
    ctx.restore();
  } else if(filter === 'top_hat'){
    const topY = lm[10].y * ch;
    const centerX = lm[168].x * cw;
    const hatW = eyeDist * 1.4, hatH = faceH * 0.4;
    ctx.translate(centerX, topY - hatH * 0.2); ctx.rotate(roll);
    // Brim
    ctx.fillStyle = 'rgba(30,30,40,0.9)';
    ctx.beginPath(); ctx.ellipse(0, hatH*0.25, hatW*0.55, hatH*0.12, 0, 0, Math.PI*2); ctx.fill();
    // Body
    ctx.fillRect(-hatW*0.28, -hatH*0.5, hatW*0.56, hatH*0.75);
    // Band
    ctx.fillStyle = 'rgba(160,40,40,0.8)';
    ctx.fillRect(-hatW*0.28, hatH*0.1, hatW*0.56, hatH*0.12);
  } else if(filter === 'mustache'){
    const lipY = lm[13].y * ch;
    const mouthW = Math.abs(lm[61].x - lm[291].x) * cw;
    const centerX = (lm[61].x + lm[291].x) / 2 * cw;
    ctx.translate(centerX, lipY - mouthW*0.08); ctx.rotate(roll);
    ctx.fillStyle = 'rgba(50,30,20,0.85)';
    ctx.beginPath();
    ctx.moveTo(-mouthW*0.4, 0);
    ctx.bezierCurveTo(-mouthW*0.35, -mouthW*0.2, -mouthW*0.1, -mouthW*0.25, 0, -mouthW*0.05);
    ctx.bezierCurveTo(mouthW*0.1, -mouthW*0.25, mouthW*0.35, -mouthW*0.2, mouthW*0.4, 0);
    ctx.bezierCurveTo(mouthW*0.3, mouthW*0.08, mouthW*0.1, mouthW*0.12, 0, mouthW*0.06);
    ctx.bezierCurveTo(-mouthW*0.1, mouthW*0.12, -mouthW*0.3, mouthW*0.08, -mouthW*0.4, 0);
    ctx.fill();
  } else if(filter === 'crown'){
    const topY = lm[10].y * ch - faceH * 0.05;
    const centerX = lm[168].x * cw;
    const crownW = eyeDist * 1.2, crownH = faceH * 0.22;
    ctx.translate(centerX, topY); ctx.rotate(roll);
    ctx.fillStyle = 'rgba(255,200,0,0.9)';
    ctx.beginPath();
    ctx.moveTo(-crownW*0.5, crownH*0.3);
    ctx.lineTo(-crownW*0.4, -crownH*0.5);
    ctx.lineTo(-crownW*0.15, -crownH*0.1);
    ctx.lineTo(0, -crownH*0.7);
    ctx.lineTo(crownW*0.15, -crownH*0.1);
    ctx.lineTo(crownW*0.4, -crownH*0.5);
    ctx.lineTo(crownW*0.5, crownH*0.3);
    ctx.closePath(); ctx.fill();
    // Jewels
    ctx.fillStyle = 'rgba(220,20,60,0.9)';
    ctx.beginPath(); ctx.arc(0, -crownH*0.3, crownH*0.1, 0, Math.PI*2); ctx.fill();
    ctx.fillStyle = 'rgba(30,100,200,0.9)';
    ctx.beginPath(); ctx.arc(-crownW*0.25, -crownH*0.15, crownH*0.07, 0, Math.PI*2); ctx.fill();
    ctx.beginPath(); ctx.arc(crownW*0.25, -crownH*0.15, crownH*0.07, 0, Math.PI*2); ctx.fill();
  } else if(filter === 'sunglasses'){
    const bridgeX = lm[168].x * cw, bridgeY = lm[168].y * ch;
    const eyeL = lm[33], eyeR = lm[263];
    const gw = eyeDist * 0.42, gh = eyeDist * 0.28;
    ctx.translate(bridgeX, bridgeY); ctx.rotate(roll);
    ctx.fillStyle = 'rgba(20,20,30,0.85)';
    // Left lens
    ctx.beginPath();
    ctx.roundRect(-eyeDist*0.48, -gh*0.5, gw*2.1, gh*1.3, gw*0.3);
    ctx.fill();
    // Right lens
    ctx.beginPath();
    ctx.roundRect(eyeDist*0.48 - gw*1.1, -gh*0.5, gw*2.1, gh*1.3, gw*0.3);
    ctx.fill();
    // Bridge
    ctx.strokeStyle = 'rgba(20,20,30,0.9)'; ctx.lineWidth = gh*0.18;
    ctx.beginPath(); ctx.moveTo(-gw*0.2, 0); ctx.lineTo(gw*0.2, 0); ctx.stroke();
    // Arms
    ctx.beginPath();
    ctx.moveTo(-eyeDist*0.48 - gw*0.1, -gh*0.2);
    ctx.lineTo(-eyeDist*0.7, -gh*0.1);
    ctx.moveTo(eyeDist*0.48 + gw*1.2, -gh*0.2);
    ctx.lineTo(eyeDist*0.7, -gh*0.1);
    ctx.stroke();
    // Lens shine
    ctx.fillStyle = 'rgba(255,255,255,0.15)';
    ctx.beginPath(); ctx.ellipse(-eyeDist*0.35, -gh*0.15, gw*0.4, gh*0.25, -0.3, 0, Math.PI*2); ctx.fill();
    ctx.beginPath(); ctx.ellipse(eyeDist*0.35, -gh*0.15, gw*0.4, gh*0.25, -0.3, 0, Math.PI*2); ctx.fill();
  }
  ctx.restore();
}

function populateHiveSpheres(){
  const container = document.getElementById('hiveCharRow');
  if(!container) return;
  container.innerHTML = '';
  const avatars = ['puppy','fox','cat','bear','bunny','owl','deer','wolf','raccoon'];
  const sel = selectedAvatar || 'puppy';
  avatars.forEach(a => {
    const el = document.createElement('div');
    el.className = 'hive-char-sphere';
    if(a === sel) el.classList.add('alpha');
    el.dataset.char = a;
    el.innerHTML = `${HIVE_EMOJIS[a]}<span class="char-tip">${HIVE_NAMES[a]}${a===sel?' (Alpha)':''}</span>`;
    el.onclick = () => toggleCharSelect(a, el);
    container.appendChild(el);
  });
}

function toggleCharSelect(charKey, el){
  if(hiveMode === 'hive'){
    // In hive mode, clicking a sphere switches to individual for that character
    setHiveMode('individual');
  }
  if(hiveMode === 'speaker'){
    // Speaker mode: only one selected at a time
    document.querySelectorAll('.hive-char-sphere').forEach(s => s.classList.remove('selected'));
    hiveSelectedChars = [charKey];
    el.classList.add('selected');
    inputField.placeholder = `Speak as ${HIVE_NAMES[charKey]}...`;
  }else{
    // Individual mode: toggle selection
    const idx = hiveSelectedChars.indexOf(charKey);
    if(idx >= 0){
      hiveSelectedChars.splice(idx, 1);
      el.classList.remove('selected');
    }else{
      hiveSelectedChars.push(charKey);
      el.classList.add('selected');
    }
    if(hiveSelectedChars.length === 0){
      inputField.placeholder = 'Pick a character, then type...';
    }else if(hiveSelectedChars.length === 1){
      inputField.placeholder = `Talk to ${HIVE_NAMES[hiveSelectedChars[0]]}...`;
    }else{
      inputField.placeholder = `Talk to ${hiveSelectedChars.length} characters...`;
    }
  }
}

function addHiveSystemMsg(text, silent){
  const div = document.createElement('div');
  div.className = 'hive-msg';
  div.style.justifyContent = 'center';
  div.innerHTML = `<div style="font-size:10px;color:rgba(93,78,109,0.4);text-align:center;padding:4px 12px;background:rgba(255,255,255,0.25);border-radius:10px;max-width:80%">${text}</div>`;
  hiveMessages.appendChild(div);
  hiveMessages.scrollTop = hiveMessages.scrollHeight;
  if(!silent){
    const hist = loadHiveHistory();
    hist.push({type:'system', text});
    saveHiveHistory(hist);
  }
}

function addHiveUserMsg(text, silent){
  const div = document.createElement('div');
  div.className = 'hive-msg user';
  div.innerHTML = `
    <div class="hive-msg-avatar" style="background:rgba(184,169,201,0.2);border-color:rgba(184,169,201,0.4)">👤</div>
    <div class="hive-msg-body">
      <div class="hive-msg-name">You</div>
      <div class="hive-msg-text">${escapeHtml(text)}</div>
    </div>`;
  hiveMessages.appendChild(div);
  hiveMessages.scrollTop = hiveMessages.scrollHeight;
  if(!silent){
    const hist = loadHiveHistory();
    hist.push({type:'user', text});
    saveHiveHistory(hist);
  }
}

function addHiveAgentMsg(char, name, role, text, silent, isAlpha){
  const div = document.createElement('div');
  div.className = 'hive-msg';
  const colors = HIVE_COLORS[char] || HIVE_COLORS.puppy;
  const badge = isAlpha ? '<span class="hive-alpha-badge">ALPHA</span>' : '';
  div.innerHTML = `
    <div class="hive-msg-avatar" style="background:${colors.bg};border-color:${colors.border}">${HIVE_EMOJIS[char]||'🐶'}</div>
    <div class="hive-msg-body">
      <div class="hive-msg-name">${name} · ${role}${badge}</div>
      <div class="hive-msg-text" style="background:${colors.bg};border-color:${colors.border}">${renderCodeBlocks(text)}</div>
    </div>`;
  hiveMessages.appendChild(div);
  hiveMessages.scrollTop = hiveMessages.scrollHeight;
  if(!silent){
    const hist = loadHiveHistory();
    hist.push({type:'agent', char, name, role, text});
    saveHiveHistory(hist);
  }
}

function showHiveTyping(char){
  const el = document.getElementById('hiveTyping');
  if(el) el.remove();
  const div = document.createElement('div');
  div.className = 'hive-typing';
  div.id = 'hiveTyping';
  const colors = HIVE_COLORS[char] || HIVE_COLORS.puppy;
  div.innerHTML = `
    <div class="hive-typing-avatar" style="background:${colors.bg};border-color:${colors.border}">${HIVE_EMOJIS[char]||'🐶'}</div>
    <div class="hive-typing-dots"><span></span><span></span><span></span></div>`;
  hiveMessages.appendChild(div);
  hiveMessages.scrollTop = hiveMessages.scrollHeight;
}

function removeHiveTyping(){
  const el = document.getElementById('hiveTyping');
  if(el) el.remove();
}

async function sendHiveMessageUnified(text){
  if(!text) return;
  addHiveUserMsg(text);
  const alpha = selectedAvatar || 'puppy';
  const mode = hiveMode;
  const chars = hiveSelectedChars.length > 0 ? hiveSelectedChars : [alpha];
  const uname = userName || '';

  try{
    const r = await fetch('/api/group_chat',{
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({message:text, selected:alpha, mode:mode, characters:chars, user_name:uname, muted:hiveMuted})
    });
    const d = await r.json();
    if(d.responses){
      for(let i = 0; i < d.responses.length; i++){
        const resp = d.responses[i];
        showHiveTyping(resp.char);
        await new Promise(r => setTimeout(r, 400));
        removeHiveTyping();
        addHiveAgentMsg(resp.char, resp.name, resp.role, resp.text, false, resp.alpha);
        if(resp.audio_id) playAudio(resp.audio_id);
        if(i < d.responses.length - 1) await new Promise(r => setTimeout(r, 150));
      }
    }
  }catch(e){
    removeHiveTyping();
    addHiveSystemMsg('Error: '+e.message);
  }
}

async function startBrowserMic(){
  const btn=document.getElementById('micBtn');
  try{
    browserMicStream=await navigator.mediaDevices.getUserMedia({audio:{echoCancellation:true,noiseSuppression:true,autoGainControl:true}});
    browserMicActive=true;
    if(btn)btn.classList.add('recording');
    recordMicChunk();
  }catch(e){
    if(btn)btn.style.opacity='0.4';
    displaySpeech('Microphone access denied. Please allow mic access and reload.');
  }
}

const canvas=document.getElementById('pupCanvas'),ctx=canvas.getContext('2d');
function resizeCanvas(){canvas.width=window.innerWidth;canvas.height=window.innerHeight}
window.addEventListener('resize',resizeCanvas);

/* ─── Nose tap → Kid Mode (3 taps) ─── */
canvas.addEventListener('click',(e)=>{
  const W=canvas.width,H=canvas.height;
  const breathe=Math.sin(frame*0.03)*3;
  const noseX=W/2, noseY=H/2+18+breathe;
  const dx=e.clientX-noseX, dy=e.clientY-noseY;
  if(Math.sqrt(dx*dx+dy*dy)<24){
    const now=Date.now();
    if(now-noseTapTimer>1500){noseTapCount=0}
    noseTapCount++;
    noseTapTimer=now;
    if(noseTapCount>=3){
      noseTapCount=0;
      childMode=!childMode;
      fetch('/api/child_mode',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({active:childMode})});
      displaySpeech(childMode?'Kid mode ON! Let\'s explore together! 🌟':'Kid mode off — back to normal!');
    }
  }
});

let frame=0,blinkFrame=100,isBlinking=false,pupSpeech="",speechTimer=0,pupMood='calm';
let lastMouthVal=0,isThinking=false;
let childMode=false,noseTapCount=0,noseTapTimer=0;
let lookAt=null,lookAtTimer=0;
function setLookAt(target,durationMs){
  lookAt=target;lookAtTimer=Date.now()+durationMs;
}
setInterval(()=>{if(lookAtTimer&&Date.now()>lookAtTimer){lookAt=null;lookAtTimer=0}},200);

/* ─── Mood colour map ─── */
const MOOD_COLORS={calm:'#c0b0d0',curious:'#b0c8e0',cheerful:'#e8c8a0',gentle:'#d0b8c0',creative:'#c0d0b0',excited:'#e8b8b0',warm:'#e0c8b0'};

function drawPup(){
  const W=canvas.width,H=canvas.height;ctx.clearRect(0,0,W,H);frame++;
  if(frame>=blinkFrame){isBlinking=true;if(frame>=blinkFrame+8){isBlinking=false;blinkFrame=frame+140+Math.random()*200}}

  const animal=selectedAvatar||'puppy';

  if(animal!=='puppy'){
    drawAnimalFace(ctx,animal,W/2,H/2+breatheOffset(),Math.min(W,H)*0.42,frame,true);
    requestAnimationFrame(drawPup);
    return;
  }

  const isSpeaking=lastMouthVal>0.1;
  const isExcited=pupMood==='excited'||pupMood==='cheerful';
  const earActive=isSpeaking||isExcited||isThinking;
  const breathe=Math.sin(frame*0.03)*3;

  ctx.save();ctx.translate(W/2,H/2+breathe-20);
  ctx.shadowColor='rgba(168,85,247,0.08)';ctx.shadowBlur=30;
  ctx.fillStyle='rgba(255,255,255,0.02)';
  ctx.beginPath();ctx.arc(0,0,120,0,Math.PI*2);ctx.fill();
  ctx.shadowBlur=0;

  const charScale = Math.min(W,H) * 0.50 / 150;
  ctx.scale(charScale,charScale);
  ctx.translate(0,6);

  if(isThinking){
    const glow=0.06+Math.sin(frame*0.06)*0.03;
    ctx.shadowColor='rgba(200,180,220,'+glow+')';ctx.shadowBlur=20
  }

  const earWiggle=earActive?Math.sin(frame*0.18)*0.1:0;
  const earBob=earActive?Math.sin(frame*0.14)*2:0;
  ctx.save();ctx.translate(-62,-44+earBob);ctx.rotate(-0.22+earWiggle);ctx.fillStyle='#c8c8d0';ctx.beginPath();ctx.ellipse(0,0,26,50,0,0,Math.PI*2);ctx.fill();ctx.fillStyle='#f0d8e4';ctx.beginPath();ctx.ellipse(0,6,14,36,0,0,Math.PI*2);ctx.fill();ctx.restore();
  ctx.save();ctx.translate(62,-44+earBob);ctx.rotate(0.22-earWiggle);ctx.fillStyle='#c8c8d0';ctx.beginPath();ctx.ellipse(0,0,26,50,0,0,Math.PI*2);ctx.fill();ctx.fillStyle='#f0d8e4';ctx.beginPath();ctx.ellipse(0,6,14,36,0,0,Math.PI*2);ctx.fill();ctx.restore();
  ctx.fillStyle='#d0d0d8';ctx.beginPath();ctx.roundRect(-72,-48,144,112,[38,38,28,28]);ctx.fill();
  ctx.fillStyle='#c0c0c8';ctx.beginPath();ctx.ellipse(0,32,42,22,0,0,Math.PI*2);ctx.fill();
  ctx.shadowBlur=0;ctx.shadowColor='transparent';
  ctx.fillStyle='#ffffff';
  if(isBlinking){
    ctx.strokeStyle='#8b7a9e';ctx.lineWidth=2.5;ctx.lineCap='round';
    ctx.beginPath();ctx.moveTo(-38,0);ctx.lineTo(-18,0);ctx.stroke();
    ctx.beginPath();ctx.moveTo(18,0);ctx.lineTo(38,0);ctx.stroke();
  }else{
    ctx.beginPath();ctx.arc(-28,2,11,0,Math.PI*2);ctx.fill();
    ctx.beginPath();ctx.arc(28,2,11,0,Math.PI*2);ctx.fill();
    let pupilX=0,pupilY=0;
    if(lookAt==='heart'){pupilX=-3;pupilY=4;}
    else if(lookAt==='dashboard'||lookAt==='name'){pupilX=0;pupilY=-4;}
    else if(lookAt==='input'){pupilY=3;}
    else if(lookAt==='app'){pupilX=2;pupilY=6;}
    else if(lookAt==='user'){pupilX=0;pupilY=0;}
    else{
      if(pupMood==='curious'||pupMood==='excited')pupilY=-3;
      if(isThinking)pupilX=-1;
    }
    ctx.fillStyle='#8b7a9e';ctx.beginPath();ctx.arc(-28+pupilX,2+pupilY,4,0,Math.PI*2);ctx.fill();
    ctx.beginPath();ctx.arc(28+pupilX,2+pupilY,4,0,Math.PI*2);ctx.fill();
    ctx.fillStyle='rgba(255,255,255,0.6)';ctx.beginPath();ctx.arc(-26,0+pupilY,1.5,0,Math.PI*2);ctx.fill();
    ctx.beginPath();ctx.arc(30,0+pupilY,1.5,0,Math.PI*2);ctx.fill();
  }
  ctx.fillStyle='#d4a0b0';ctx.beginPath();ctx.ellipse(0,18,10,7,0,0,Math.PI*2);ctx.fill();
  if(childMode){
    const sparkle=0.5+Math.sin(frame*0.08)*0.5;
    ctx.save();ctx.translate(0,18);ctx.rotate(frame*0.02);
    ctx.fillStyle=`rgba(255,215,0,${0.4+sparkle*0.6})`;
    for(let i=0;i<4;i++){
      ctx.rotate(Math.PI/2);
      ctx.beginPath();ctx.moveTo(0,-4-sparkle*3);ctx.lineTo(-2,0);ctx.lineTo(0,4+sparkle*3);ctx.lineTo(2,0);ctx.closePath();ctx.fill();
    }
    ctx.restore();
  }
  ctx.shadowBlur=0;
  if(isSpeaking&&!isBlinking){
    const open=Math.min(1,lastMouthVal)*10+Math.abs(Math.sin(frame*0.25))*3;
    ctx.fillStyle='#c8889a';ctx.beginPath();ctx.ellipse(0,28,14,4+open*0.8,0,0,Math.PI*2);ctx.fill();
    ctx.fillStyle='#e8a0b0';ctx.beginPath();ctx.ellipse(0,30+open*0.4,8,2+open*0.4,0,0,Math.PI*2);ctx.fill();
  }else{
    ctx.strokeStyle='#c0a0b0';ctx.lineWidth=2;ctx.lineCap='round';
    ctx.beginPath();ctx.arc(-8,26,8,0,Math.PI*0.85);ctx.stroke();
    ctx.beginPath();ctx.arc(8,26,8,Math.PI*0.15,Math.PI);ctx.stroke();
  }
  const EYEBROWS={
    calm:    [[-42,-12],[-16,-16],[16,-16],[42,-12]],
    curious: [[-44,-20],[-16,-26],[16,-26],[44,-20]],
    cheerful:[[-46,-22],[-14,-28],[14,-28],[46,-22]],
    excited: [[-46,-24],[-14,-30],[14,-30],[46,-24]],
    gentle:  [[-42,-14],[-16,-20],[16,-20],[42,-14]],
    warm:    [[-42,-12],[-16,-18],[16,-18],[42,-12]],
    worried: [[-42,-20],[-16,-14],[16,-14],[42,-20]],
    sad:     [[-42,-22],[-16,-16],[16,-16],[42,-22]],
    angry:   [[-42,-8],[-16,-2],[16,-2],[42,-8]],
  };
  const eb=EYEBROWS[pupMood]||EYEBROWS.calm;
  ctx.strokeStyle='rgba(139,122,158,0.4)';ctx.lineWidth=2.5;ctx.lineCap='round';
  ctx.beginPath();ctx.moveTo(eb[0][0],eb[0][1]);ctx.lineTo(eb[1][0],eb[1][1]);ctx.stroke();
  ctx.beginPath();ctx.moveTo(eb[2][0],eb[2][1]);ctx.lineTo(eb[3][0],eb[3][1]);ctx.stroke();

  ctx.shadowBlur=0;ctx.shadowColor='transparent';
  ctx.restore();
  requestAnimationFrame(drawPup);
}

function breatheOffset(){return Math.sin(frame*0.03)*3}

let _speechWords=[],_speechWordIdx=0,_speechWordTimer=0;
const WORD_RATE_MS=380;

function updateUI(){
  const bubble=document.getElementById('speechBubble');
  const hasCode=pupSpeech&&/```/.test(pupSpeech);
  if(isStreaming&&pupSpeech){
    bubble.innerHTML=hasCode?renderCodeBlocks(pupSpeech):escapeHtml(pupSpeech);
    if(bubble.style.display!=='block')bubble.style.display='block';
    if(!hasCode&&!bubble.querySelector('.stream-indicator')){
      const ind=document.createElement('span');
      ind.className='stream-indicator';
      ind.textContent='●';
      bubble.appendChild(ind);
    }
  }else if(_speechWords.length>0&&_speechWordIdx<_speechWords.length){
    _speechWordTimer-=16;
    if(_speechWordTimer<=0){
      _speechWordIdx++;
      _speechWordTimer=WORD_RATE_MS;
      const partialText=_speechWords.slice(0,_speechWordIdx).join(' ');
      bubble.innerHTML=hasCode?renderCodeBlocks(partialText):escapeHtml(partialText);
    }
    if(bubble.style.display!=='block')bubble.style.display='block';
    const ind=bubble.querySelector('.stream-indicator');
    if(ind)ind.remove();
  }else if(pupSpeech&&_speechWordIdx>=_speechWords.length&&_speechWords.length>0){
    bubble.innerHTML=hasCode?renderCodeBlocks(pupSpeech):escapeHtml(pupSpeech);
    speechTimer--;
    if(speechTimer<=0){_speechWords=[];_speechWordIdx=0;bubble.style.display='none'}
  }else{bubble.style.display='none'}
  requestAnimationFrame(updateUI);
}

let lastAudioId=0,activeAudios={};
let _lastAudioSrc='';
let _audioContext=null;
let _analyser=null;
let _dataArray=null;
function playAudio(id){
  if(id<=lastAudioId)return;
  lastAudioId=id;
  _lastAudioSrc='/api/tts?id='+id;
  const btn=document.getElementById('ttsReplayBtn');
  if(btn)btn.style.display='flex';
  const a=new Audio(_lastAudioSrc);
  activeAudios[id]=a;
  a.onended=()=>{delete activeAudios[id];lastMouthVal=0};
  // Web Audio API lip sync — analyzes actual audio amplitude
  a.onplay=()=>{
    try{
      if(!_audioContext){
        _audioContext=new(window.AudioContext||window.webkitAudioContext)();
        _analyser=_audioContext.createAnalyser();
        _analyser.fftSize=256;
        _dataArray=new Uint8Array(_analyser.frequencyBinCount);
      }
      const source=_audioContext.createMediaElementSource(a);
      source.connect(_analyser);
      _analyser.connect(_audioContext.destination);
      function updateMouth(){
        if(a.paused||a.ended){lastMouthVal=0;return}
        _analyser.getByteFrequencyData(_dataArray);
        // Get average amplitude from voice frequencies (300-3000Hz)
        let sum=0,count=0;
        for(let i=8;i<80;i++){sum+=_dataArray[i];count++}
        const avg=count?sum/count:0;
        // Map to mouth value (0-1) with smoothing
        const target=Math.min(1,avg/128);
        lastMouthVal=lastMouthVal*0.6+target*0.4;
        requestAnimationFrame(updateMouth);
      }
      updateMouth();
    }catch(e){}
  };
  a.play().catch(()=>{pendingAudio=a;delete activeAudios[id]});
}
function replayLastSpeech(){
  if(!_lastAudioSrc)return;
  const a=new Audio(_lastAudioSrc);
  a.onended=()=>{lastMouthVal=0};
  a.onplay=()=>{
    try{
      if(!_audioContext){
        _audioContext=new(window.AudioContext||window.webkitAudioContext)();
        _analyser=_audioContext.createAnalyser();
        _analyser.fftSize=256;
        _dataArray=new Uint8Array(_analyser.frequencyBinCount);
      }
      const source=_audioContext.createMediaElementSource(a);
      source.connect(_analyser);
      _analyser.connect(_audioContext.destination);
      function updateMouth(){
        if(a.paused||a.ended){lastMouthVal=0;return}
        _analyser.getByteFrequencyData(_dataArray);
        let sum=0,count=0;
        for(let i=8;i<80;i++){sum+=_dataArray[i];count++}
        const avg=count?sum/count:0;
        const target=Math.min(1,avg/128);
        lastMouthVal=lastMouthVal*0.6+target*0.4;
        requestAnimationFrame(updateMouth);
      }
      updateMouth();
    }catch(e){}
  };
  a.play().catch(()=>{});
}
function displaySpeech(text){
  if(!text)return;
  pupSpeech=text;
  _speechWords=text.split(/\s+/);
  _speechWordIdx=0;
  _speechWordTimer=WORD_RATE_MS;
  speechTimer=Math.max(3000,text.split(/\s+/).length*WORD_RATE_MS/16+120);
}

let heardTimer=null;
function showHeard(text){
  if(!text)return;
  const b=document.getElementById('heardBubble');
  b.textContent=text;
  b.style.display='block';
  b.style.opacity='1';
  clearTimeout(heardTimer);
  heardTimer=setTimeout(()=>{b.style.opacity='0';setTimeout(()=>{b.style.display='none'},300)},4000);
}
function setMouth(val){lastMouthVal=Math.max(0,Math.min(1,val))}

let lastSpoken="",lastHeard="",lastSsml="";
let isStreaming=false;
const inputField=document.getElementById('userInput');
const micIndicator=document.getElementById('micIndicator'),statusLabel=document.getElementById('statusLabel');
const moodLabel=document.getElementById('moodLabel'),moodDot=document.getElementById('moodDot');
const thinkingDots=document.getElementById('thinkingDots');
const nameInput=document.getElementById('nameInput');
let userName=localStorage.getItem('lilly_user_name')||'';
if(userName){
  nameInput.value=userName;
  fetch('/api/set_name',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:userName})});
}
nameInput.addEventListener('keydown',(e)=>{
  if(e.key==='Enter'){e.preventDefault();nameInput.blur();}
});
nameInput.addEventListener('blur',()=>{
  const val=nameInput.value.trim();
  if(val!==userName){
    userName=val;
    localStorage.setItem('lilly_user_name',userName);
    fetch('/api/set_name',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:userName})});
  }
});

// Unlock audio on first user gesture
let audioCtx=null;
let pendingAudio=null;
function unlockAudio(){
  if(!audioCtx){
    try{
      audioCtx=new (window.AudioContext||window.webkitAudioContext)();
      audioCtx.resume();
    }catch(e){}
  }
  if(pendingAudio){
    pendingAudio.play().catch(()=>{});
    pendingAudio=null;
  }
}
document.addEventListener('click',unlockAudio);
document.addEventListener('keydown',unlockAudio);

document.getElementById('clearBtn').onclick=async()=>{
  try{await fetch('/api/memory/clear',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({avatar:localStorage.getItem('lilly_avatar')||'puppy'})});inputField.placeholder='Memory cleared!';setTimeout(()=>inputField.placeholder='Type a message...',2000)}catch(e){}
};

/* ─── Dashboard Toggle ─── */
function toggleDashboard(){
  const d=document.getElementById('dashboard');
  const exp=document.getElementById('dashExpand');
  d.classList.toggle('open');
  exp.textContent=d.classList.contains('open')?'tap to close':'tap for details';
}
/* ─── Browser Microphone + Voice Recognition ─── */
let browserMicStream=null,browserMicRecorder=null,browserMicActive=false,lillySpeaking=false;
let voiceFingerprint=null;  // enrolled FFT average
let voiceEnrolling=false;
const FFT_BINS=64;

function extractFFTAverage(audioBuffer){
  const offlineCtx=new OfflineAudioContext(1,audioBuffer.length,audioBuffer.sampleRate);
  const src=offlineCtx.createBufferSource();src.buffer=audioBuffer;
  const analyser=offlineCtx.createAnalyser();analyser.fftSize=FFT_BINS*2;
  src.connect(analyser);analyser.connect(offlineCtx.destination);
  src.start(0);
  return offlineCtx.startRendering().then(rendered=>{
    const ctx2=new(window.AudioContext||window.webkitAudioContext)();
    return ctx2.createBufferSource(),ctx2.close(),rendered;
  }).then(rendered=>{
    const data=new Float32Array(analyser.frequencyBinCount);
    const len=rendered.length,sr=rendered.sampleRate;
    const ch0=rendered.getChannelData(0);
    let sumSq=0;for(let i=0;i<len;i++)sumSq+=ch0[i]*ch0[i];
    const rms=Math.sqrt(sumSq/len);
    if(rms<0.005)return null;
    const winSize=2048,hops=Math.floor((len-winSize)/winSize);
    const avg=new Float32Array(FFT_BINS);
    const tmpCtx=new OfflineAudioContext(1,winSize,sr);
    const tmpAnalyser=tmpCtx.createAnalyser();tmpAnalyser.fftSize=FFT_BINS*2;
    let count=0;
    for(let h=0;h<Math.min(hops,20);h++){
      const start=h*winSize;
      const win=new Float32Array(winSize);
      for(let i=0;i<winSize;i++)win[i]=ch0[start+i];
      const tmpBuf=tmpCtx.createBuffer(1,winSize,sr);
      tmpBuf.getChannelData(0).set(win);
      for(let i=0;i<FFT_BINS;i++)avg[i]+=data[i];
      count++;
    }
    if(count>0)for(let i=0;i<FFT_BINS;i++)avg[i]/=count;
    return{fft:avg,rms};
  });
}

function extractFFTSync(audioBuffer){
  const len=audioBuffer.length,sr=audioBuffer.sampleRate;
  const ch0=audioBuffer.getChannelData(0);
  let sumSq=0;for(let i=0;i<len;i++)sumSq+=ch0[i]*ch0[i];
  const rms=Math.sqrt(sumSq/len);
  if(rms<0.003)return{fft:null,rms};
  const winSize=1024;
  const avg=new Float32Array(FFT_BINS);
  let count=0;
  for(let h=0;h<Math.min(30,Math.floor((len-winSize)/winSize));h++){
    const start=h*winSize;
    let re=0;
    for(let i=0;i<winSize;i++){
      const w=0.5-0.5*Math.cos(2*Math.PI*i/winSize);
      re+=ch0[start+i]*w;
    }
    const mag=Math.abs(re/winSize);
    avg[count%FFT_BINS]+=mag;
    count++;
  }
  if(count>0)for(let i=0;i<FFT_BINS;i++)avg[i]/=Math.ceil(count/FFT_BINS);
  const norm=Math.sqrt(avg.reduce((s,v)=>s+v*v,0))||1;
  for(let i=0;i<FFT_BINS;i++)avg[i]/=norm;
  return{fft:avg,rms};
}

function cosineSim(a,b){
  if(!a||!b||a.length!==b.length)return 0;
  let dot=0,na=0,nb=0;
  for(let i=0;i<a.length;i++){dot+=a[i]*b[i];na+=a[i]*a[i];nb+=b[i]*b[i]}
  return dot/(Math.sqrt(na)*Math.sqrt(nb)||1);
}

async function enrollVoice(){
  voiceEnrolling=true;
  displaySpeech("Say a few words so I learn your voice...");
  statusLabel.textContent='enrolling voice...';
  const opts={mimeType:'audio/webm;codecs=opus'};
  if(!MediaRecorder.isTypeSupported(opts.mimeType))delete opts.mimeType;
  const rec=new MediaRecorder(browserMicStream,opts);
  const chunks=[];
  rec.ondataavailable=(e)=>{if(e.data.size>0)chunks.push(e.data)};
  rec.start();
  await new Promise(r=>setTimeout(r,4000));
  rec.stop();
  await new Promise(r=>setTimeout(r,500));
  const blob=new Blob(chunks,{type:rec.mimeType||'audio/webm'});
  const arrayBuf=await blob.arrayBuffer();
  const audioCtx=new(window.AudioContext||window.webkitAudioContext)();
  try{
    const decoded=await audioCtx.decodeAudioData(arrayBuf);
    const {fft,rms}=extractFFTSync(decoded);
    if(fft&&rms>0.005){
      voiceFingerprint=fft;
      displaySpeech("Voice enrolled! I'll only respond to you now.");
      statusLabel.textContent='voice recognized';
    }else{
      displaySpeech("Couldn't hear you well. Try speaking louder. Tap mic to retry.");
      voiceFingerprint=null;
    }
  }catch(e){
    displaySpeech("Voice enrollment failed. Tap mic to try again.");
    voiceFingerprint=null;
  }
  audioCtx.close();
  voiceEnrolling=false;
}

function isMyVoice(audioBuffer){
  if(!voiceFingerprint)return true;
  const {fft,rms}=extractFFTSync(audioBuffer);
  if(!fft||rms<0.003)return false;
  const sim=cosineSim(voiceFingerprint,fft);
  return sim>0.30;
}

function stopBrowserMic(){
  browserMicActive=false;
  if(browserMicRecorder&&browserMicRecorder.state==='recording'){try{browserMicRecorder.stop()}catch(e){}}
  if(browserMicStream){browserMicStream.getTracks().forEach(t=>t.stop());browserMicStream=null;}
  const btn=document.getElementById('micBtn');
  if(btn){btn.classList.remove('recording');btn.style.opacity='1';}
}
async function toggleBrowserMic(){
  if(browserMicActive){stopBrowserMic();}else{await startBrowserMic();}
}
function recordMicChunk(){
  if(!browserMicActive||!browserMicStream)return;
  if(lillySpeaking){if(browserMicActive)setTimeout(recordMicChunk,500);return}
  const opts={mimeType:'audio/webm;codecs=opus'};
  if(!MediaRecorder.isTypeSupported(opts.mimeType))delete opts.mimeType;
  browserMicRecorder=new MediaRecorder(browserMicStream,opts);
  const chunks=[];
  browserMicRecorder.ondataavailable=(e)=>{if(e.data.size>0)chunks.push(e.data)};
  browserMicRecorder.onstop=async()=>{
    if(chunks.length===0){if(browserMicActive)setTimeout(recordMicChunk,200);return}
    const blob=new Blob(chunks,{type:browserMicRecorder.mimeType||'audio/webm'});
    const arrayBuf=await blob.arrayBuffer();
    const audioCtx=new(window.AudioContext||window.webkitAudioContext)();
    try{
      const decoded=await audioCtx.decodeAudioData(arrayBuf);
      if(!isMyVoice(decoded)){
        if(browserMicActive)setTimeout(recordMicChunk,300);
        audioCtx.close();return;
      }
      const wavBuf=encodeWav(decoded);
      const resp=await fetch('/api/browser_mic',{method:'POST',headers:{'Content-Type':'audio/wav'},body:wavBuf});
      const result=await resp.json();
      if(result.heard){showHeard(result.heard)}
      else if(result.status==='silence'){
        const sb=document.getElementById('speechBubble');
        if(sb.style.display!=='block')displaySpeech('...');
      }else if(result.status==='noise'){
        statusLabel.textContent='filtered noise';
        setTimeout(()=>{statusLabel.textContent='listening'},1500);
      }else if(result.status==='speaking'){
        statusLabel.textContent='waiting for Lilly...';
        setTimeout(()=>{statusLabel.textContent='listening'},2000);
      }
    }catch(e){statusLabel.textContent='mic error';setTimeout(()=>{statusLabel.textContent='listening'},2000);}
    audioCtx.close();
    if(browserMicActive)setTimeout(recordMicChunk,300);
  };
  browserMicRecorder.start();
  setTimeout(()=>{if(browserMicRecorder&&browserMicRecorder.state==='recording')browserMicRecorder.stop()},3000);
}
function encodeWav(audioBuffer){
  const numCh=audioBuffer.numberOfChannels;
  const sr=audioBuffer.sampleRate;
  const len=audioBuffer.length*numCh*2;
  const buf=new ArrayBuffer(44+len);
  const v=new DataView(buf);
  const writeStr=(o,s)=>{for(let i=0;i<s.length;i++)v.setUint8(o+i,s.charCodeAt(i))};
  writeStr(0,'RIFF');v.setUint32(4,36+len,true);writeStr(8,'WAVE');
  writeStr(12,'fmt ');v.setUint32(16,16,true);v.setUint16(20,1,true);v.setUint16(22,numCh,true);
  v.setUint32(24,sr,true);v.setUint32(28,sr*numCh*2,true);v.setUint16(32,numCh*2,true);v.setUint16(34,16,true);
  writeStr(36,'data');v.setUint32(40,len,true);
  let off=44;
  const ch0=audioBuffer.getChannelData(0);
  for(let i=0;i<audioBuffer.length;i++){
    const s=Math.max(-1,Math.min(1,ch0[i]));
    v.setInt16(off,s<0?s*0x8000:s*0x7FFF,true);off+=2;
  }
  return buf;
}

inputField.addEventListener('keydown',async(e)=>{
  if(e.key==='Enter'&&inputField.value.trim()){
    const text=inputField.value.trim();inputField.value='';
    
    // In hive group chat mode, route to group chat
    if(hiveActive){
      sendHiveMessageUnified(text);
      return;
    }
    
    // In coding mode, send to coding chat
    if(codingMode){
      sendCodingMessage(text);
      return;
    }
    
    const lower=text.toLowerCase();

    const STORY_TRIGGERS=['tell me a story','make up a story','create a story','story time',
      'what do your sensors feel','what do you sense','describe your world',
      'what\'s happening around you','paint a picture','narrate',
      'tell me what you feel','what does it feel like','sensor story'];
    const isStory=STORY_TRIGGERS.some(t=>lower.includes(t));
    if(isStory){
      try{
        isStreaming=true;
        pupSpeech='...';speechTimer=999;
        statusLabel.textContent='generating story...';
        const r=await fetch('/api/story_stream',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text,avatar:localStorage.getItem('lilly_avatar')||'puppy'})});
        const reader=r.body.getReader();
        const decoder=new TextStreamDecoder();
        let full='';
        while(true){
          const{done,value}=await reader.read();
          if(done)break;
          const chunk=decoder.decode(value,{stream:true});
          full+=chunk;
          pupSpeech=full;speechTimer=999;
        }
        isStreaming=false;
        statusLabel.textContent='story delivered';
        setTimeout(()=>{statusLabel.textContent='idle'},2000);
      }catch(e){isStreaming=false;statusLabel.textContent='error';}
    }else{
      try{
        const r=await fetch('/api/cmd',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text,avatar:localStorage.getItem('lilly_avatar')||'puppy'})});
        const d=await r.json();
        if(d.reply)displaySpeech(d.reply);
        if(d.audio_id)playAudio(d.audio_id);
        if(d.look_at)setLookAt(d.look_at,5000);
        if(d.open_url)window.open(d.open_url,'_blank','noopener,noreferrer');
      }catch(e){statusLabel.textContent='reply error';setTimeout(()=>{statusLabel.textContent='idle'},3000);}
    }
  }
});

let conversationMode=false;
async function toggleConversationMode(){
  try{
    const r=await fetch('/api/conversation_mode',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({})});
    const d=await r.json();
    conversationMode=d.conversation_mode;
    updateConvIndicator();
  }catch(e){}
}
function updateConvIndicator(){
  const el=document.getElementById('convIndicator');
  if(conversationMode){
    el.style.display='inline';
  }else{
    el.style.display='none';
  }
}

// Browser geolocation fallback — sends GPS to backend periodically
(function initGeoLocation(){
  if(!navigator.geolocation)return;
  function sendGPS(pos){
    fetch('/api/location',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({lat:pos.coords.latitude,lon:pos.coords.longitude})});
  }
  navigator.geolocation.getCurrentPosition(sendGPS);
  setInterval(()=>navigator.geolocation.getCurrentPosition(sendGPS),60000);
})();

async function pollState(){
  try{
    const r=await fetch('/api/ui_state'),d=await r.json();
    if(d.heard&&d.heard!==lastHeard){
      lastHeard=d.heard;
      showHeard(d.heard);
    }
    if(d.look_at)setLookAt(d.look_at,5000);
    if(d.open_url)window.open(d.open_url,'_blank','noopener,noreferrer');
    if(d.spoken&&d.spoken!==lastSpoken){
      lastSpoken=d.spoken;displaySpeech(d.spoken);
      if(d.audio_id){playAudio(d.audio_id)}
    }
    // Also play audio if a new audio_id appears (handles browser_mic responses)
    if(d.audio_id&&d.audio_id>lastAudioId){playAudio(d.audio_id)}
    if(typeof d.conversation_mode==='boolean'){
      conversationMode=d.conversation_mode;
      updateConvIndicator();
    }
    if(typeof d.coding_mode==='boolean'){
      codingMode=d.coding_mode;
      chatContainer.classList.toggle('active',codingMode);
      document.getElementById('startScreen').classList.toggle('coding-shrink',codingMode);
    }
    if(d.coding_files){
      const dlBtn=document.getElementById('downloadBtn');
      if(dlBtn)dlBtn.disabled=d.coding_files.length===0;
    }

    if(typeof d.mouth==='number'){setMouth(d.mouth)}
    if(typeof d.speaking==='boolean'){lillySpeaking=d.speaking}
    micIndicator.classList.toggle('active',d.mic_active||conversationMode||browserMicActive);

    if(d.thinking){
      statusLabel.textContent='thinking...';
    }else if(conversationMode){
      statusLabel.textContent='conversing';
    }else if(d.mic_active||browserMicActive){
      statusLabel.textContent='listening';
    }else if(isStreaming){
      statusLabel.textContent='generating story...';
    }else{
      statusLabel.textContent='idle';
    }

    if(typeof d.thinking==='boolean'){
      isThinking=d.thinking;
      thinkingDots.style.display=(d.thinking||isStreaming)?'flex':'none';
    }

    if(d.mood&&d.mood!==moodLabel.textContent){
      moodLabel.textContent=d.mood;
      pupMood=d.mood;
      const col=MOOD_COLORS[d.mood]||'#c0b0d0';
      moodDot.style.background=col;
      moodDot.style.boxShadow='0 0 6px '+col;
      const PROSODY_HINTS={calm:'medium·+5%·medium',curious:'medium·+12%·medium',cheerful:'fast·+22%·loud',excited:'x-fast·+28%·x-loud',gentle:'slow·+2%·soft',creative:'medium·+12%·medium',warm:'medium·+8%·medium',worried:'slow·-5%·soft',sad:'x-slow·-8%·soft',angry:'fast·-8%·loud'};
      document.getElementById('prosodyHint').textContent=PROSODY_HINTS[d.mood]||'';
    }
    if(d.ssml&&d.ssml!==lastSsml){
      lastSsml=d.ssml;
    }
    if(typeof d.child_mode==='boolean'){childMode=d.child_mode}
  }catch(e){}
  setTimeout(pollState,250);
}

/* ─── Magic Mirror Dashboard ─── */
const ACTIVITY_ICONS={walk:'🚶',run:'🏃',bike:'🚴',drive:'🚗'};
const ACTIVITY_LABELS={walk:'Walking',run:'Running',bike:'Cycling',drive:'Driving'};

function updateDashboard(d){
  document.getElementById('dashTime').textContent=d.time||'--:--';
  document.getElementById('dashDate').textContent=d.date||'---';

  const w=d.weather||{};
  const cur=w.current||{};
  const forecast=w.forecast||[];
  let whtml='';
  if(cur.desc){
    whtml+=`<div class="dash-weather-row"><span class="dash-weather-icon">${cur.icon||'🌤️'}</span><span class="dash-weather-temp">${cur.temp_c!=null?cur.temp_c+'°C':'--'}</span><span class="dash-weather-desc">${cur.desc}</span></div>`;
    if(cur.wind_kph!=null)whtml+=`<div class="dash-weather-wind">💨 ${cur.wind_kph} km/h · 💧 ${cur.humidity||'-'}%</div>`;
  }
  if(forecast.length>0){
    for(let i=0;i<Math.min(forecast.length,3);i++){
      const f=forecast[i];
      const day=i===0?'Today':i===1?'Tomorrow':f.date||`Day ${i+1}`;
      whtml+=`<div class="dash-day-label">${day}</div>`;
      whtml+=`<div class="dash-weather-row"><span class="dash-weather-icon">${f.icon||'🌤️'}</span><span class="dash-weather-temp">${f.min_c!=null?f.min_c+'°': '--'} / ${f.max_c!=null?f.max_c+'°':'--'}</span><span class="dash-weather-desc">${f.desc||''}</span></div>`;
    }
  }
  document.getElementById('dashWeather').innerHTML=whtml||'<div class="dash-weather-desc">Weather loading...</div>';

  const a=d.activity;
  let ahtml='';
  if(a){
    const icon=ACTIVITY_ICONS[a.type]||'☀️';
    const label=ACTIVITY_LABELS[a.type]||a.type;
    const mins=Math.floor((a.elapsed_sec||0)/60);
    const secs=(a.elapsed_sec||0)%60;
    const dist=a.dist_km!=null?(a.dist_km>=1?a.dist_km.toFixed(1)+'km':Math.round(a.dist_km*1000)+'m'):'';
    ahtml+=`<div class="dash-activity"><span class="dash-activity-icon">${icon}</span><span class="dash-activity-text">${label} ${mins}:${secs.toString().padStart(2,'0')}</span></div>`;
    if(dist)ahtml+=`<div class="dash-activity-stats">${dist} · ${a.speed_kph!=null?a.speed_kph+' km/h':''}</div>`;
  }
  document.getElementById('dashActivity').innerHTML=ahtml;

  const loc=d.location||'';
  document.getElementById('dashLocation').textContent=loc?'📍 '+loc:'';
}

let _lastDashboardUpdate=0;
async function pollDashboard(){
  try{
    const now=Date.now();
    if(now-_lastDashboardUpdate<30000)return;
    _lastDashboardUpdate=now;
    const r=await fetch('/api/dashboard'),d=await r.json();
    updateDashboard(d);
  }catch(e){}
  setTimeout(pollDashboard,15000);
}
setInterval(()=>{
  const d=document.getElementById('dashTime');
  if(d&&d.textContent&&d.textContent!=='--:--'){
    const parts=d.textContent.split(':');
    let h=parseInt(parts[0]),m=parseInt(parts[1]);
    m++;if(m>=60){m=0;h++;if(h>=24)h=0}
    d.textContent=h.toString().padStart(2,'0')+':'+m.toString().padStart(2,'0');
  }
},60000);
setTimeout(pollDashboard,2000);

/* ─── Pet Heart Feeder Animation ─── */
(function(){
const C=document.getElementById('petHeartCanvas'),ctx=C.getContext('2d');
const W=114,H=114;
C.width=W;C.height=H;
const CX=W/2,HEART_CY=30,HEART_R_FULL=20;
const BOWL_CX=CX,BOWL_CY=90,BOWL_W=50,BOWL_H=12;
const RIM_Y=BOWL_CY-4;
let falling=[],bowlPx=[];
let bowlCount=0,heartHP=100,bobT=0,lastSteps=0;
let lillyHappy=false,happyTimer=0;
let overflowPx=[];
const PINKS=['#e94560','#ff6b6b','#ff4757','#f08080','#e8838a'];
const KIBBLE_COLORS=['#c8a870','#b89860','#d4b880','#a88850','#d8c090'];

function heartPath(cx,cy,r){
  ctx.beginPath();
  ctx.moveTo(cx,cy+r*0.7);
  ctx.bezierCurveTo(cx-r*1.2,cy-r*0.1,cx-r*0.6,cy-r*1.1,cx,cy-r*0.4);
  ctx.bezierCurveTo(cx+r*0.6,cy-r*1.1,cx+r*1.2,cy-r*0.1,cx,cy+r*0.7);
  ctx.closePath();
}

function drawHeart(){
  const bob=Math.sin(bobT)*2;
  const pulse=1+Math.sin(bobT*1.8)*0.03;
  const r=HEART_R_FULL*(heartHP/100);
  if(r<3)return;
  ctx.save();
  ctx.translate(CX,HEART_CY+bob);
  ctx.scale(pulse,pulse);
  ctx.shadowColor='rgba(233,69,96,0.2)';
  ctx.shadowBlur=8;
  const g=ctx.createRadialGradient(0,-4,2,0,2,r);
  g.addColorStop(0,'#ff8a9e');
  g.addColorStop(0.5,'#e94560');
  g.addColorStop(1,'#c0392b');
  ctx.fillStyle=g;
  heartPath(0,2,r);
  ctx.fill();
  ctx.shadowBlur=0;
  ctx.fillStyle='rgba(255,255,255,0.2)';
  heartPath(-2,-2,r*0.5);
  ctx.fill();
  ctx.restore();
}

function drawBowl(){
  ctx.save();
  ctx.shadowColor='rgba(0,0,0,0.08)';
  ctx.shadowBlur=6;
  ctx.shadowOffsetY=3;
  const g=ctx.createLinearGradient(BOWL_CX-BOWL_W/2,BOWL_CY,BOWL_CX+BOWL_W/2,BOWL_CY+BOWL_H);
  g.addColorStop(0,'#c8b898');
  g.addColorStop(1,'#a89070');
  ctx.fillStyle=g;
  ctx.beginPath();
  ctx.ellipse(BOWL_CX,BOWL_CY,BOWL_W/2,BOWL_H/2,0,0,Math.PI);
  ctx.fill();
  ctx.shadowBlur=0;
  ctx.fillStyle='#8a7a5a';
  ctx.beginPath();
  ctx.ellipse(BOWL_CX,BOWL_CY+1,BOWL_W/2-4,BOWL_H/2-3,0,0,Math.PI);
  ctx.fill();
  ctx.strokeStyle='#b8a880';
  ctx.lineWidth=2.5;
  ctx.beginPath();
  ctx.ellipse(BOWL_CX,BOWL_CY,BOWL_W/2,4,0,Math.PI,Math.PI*2);
  ctx.stroke();
  ctx.fillStyle='rgba(120,100,70,0.5)';
  ctx.font='bold 7px sans-serif';
  ctx.textAlign='center';
  ctx.fillText("Lilly's",BOWL_CX,BOWL_CY+4);
  ctx.restore();
}

function drawBowlKibble(){
  for(const k of bowlPx){
    ctx.globalAlpha=k.a;
    ctx.fillStyle=k.color;
    ctx.beginPath();
    ctx.arc(k.x,k.y,k.r,0,Math.PI*2);
    ctx.fill();
    ctx.fillStyle='rgba(255,255,255,0.2)';
    ctx.beginPath();
    ctx.arc(k.x-0.5,k.y-0.5,k.r*0.4,0,Math.PI*2);
    ctx.fill();
  }
  ctx.globalAlpha=1;
}

function drawOverflow(){
  for(const p of overflowPx){
    ctx.globalAlpha=p.a;
    ctx.fillStyle=p.color;
    ctx.beginPath();
    ctx.arc(p.x,p.y,2,0,Math.PI*2);
    ctx.fill();
  }
  ctx.globalAlpha=1;
}

function drawFalling(){
  for(const p of falling){
    ctx.globalAlpha=0.9;
    ctx.fillStyle=p.color;
    ctx.shadowColor=p.color;
    ctx.shadowBlur=3;
    ctx.beginPath();
    ctx.arc(p.x,p.y,2.2,0,Math.PI*2);
    ctx.fill();
    ctx.shadowBlur=0;
  }
  ctx.globalAlpha=1;
}

function drawHappy(){
  if(happyTimer<=0)return;
  const a=Math.min(1,happyTimer/30);
  ctx.save();
  ctx.globalAlpha=a;
  ctx.fillStyle='rgba(233,69,96,0.7)';
  ctx.font='bold 9px sans-serif';
  ctx.textAlign='center';
  const yOff=Math.sin(bobT*2)*3;
  ctx.fillText('Yum! Thank you! ♥',CX,BOWL_CY+22+yOff);
  ctx.restore();
  ctx.globalAlpha=1;
}

function releasePixel(n){
  if(heartHP<=0)return;
  const canRelease=Math.min(n,Math.floor(heartHP/2));
  for(let i=0;i<canRelease;i++){
    const angle=Math.random()*Math.PI*2;
    const dist=Math.random()*HEART_R_FULL*(heartHP/100)*0.5;
    falling.push({
      x:CX+Math.cos(angle)*dist,
      y:HEART_CY+Math.sin(angle)*dist*0.7+2,
      vx:(Math.random()-0.5)*0.6,
      vy:-2-Math.random()*1,
      gravity:0.1+Math.random()*0.04,
      color:PINKS[Math.floor(Math.random()*PINKS.length)],
      kibbleColor:KIBBLE_COLORS[Math.floor(Math.random()*KIBBLE_COLORS.length)]
    });
    heartHP=Math.max(0,heartHP-1.8);
  }
  document.getElementById('petHeartHP').textContent=Math.floor(heartHP);
}

function findKibblePos(x){
  const rimLeft=BOWL_CX-BOWL_W/2+4;
  const rimRight=BOWL_CX+BOWL_W/2-4;
  x=Math.max(rimLeft,Math.min(rimRight,x));
  const inBowl=bowlPx.filter(k=>k.y>=RIM_Y);
  const heapCount=inBowl.length;
  const layer=Math.floor(heapCount/6);
  const posInLayer=heapCount%6;
  const heapWidth=Math.min(BOWL_W-12,20+layer*4);
  const spacing=heapWidth/Math.max(1,Math.min(6,6-layer));
  const baseX=BOWL_CX-heapWidth/2+posInLayer*spacing;
  const baseY=RIM_Y-2-layer*3.5;
  return{x:baseX+(Math.random()-0.5)*2,y:baseY+(Math.random()-0.5)*1.5};
}

function update(){
  bobT+=0.04;
  for(let i=falling.length-1;i>=0;i--){
    const p=falling[i];
    p.vy+=p.gravity;
    p.x+=p.vx;
    p.y+=p.vy;
    p.vx*=0.99;
    if(p.y>=RIM_Y-2){
      if(bowlCount>=40){
        overflowPx.push({x:p.x,y:RIM_Y-8,color:p.kibbleColor,a:1,vy:0.3+Math.random()*0.3});
        if(overflowPx.length>20)overflowPx.shift();
      }else{
        const pos=findKibblePos(p.x);
        bowlPx.push({x:pos.x,y:pos.y,color:p.kibbleColor,r:2+Math.random()*0.5,a:0.9});
      }
      bowlCount++;
      document.getElementById('petBowlCount').textContent=Math.min(bowlCount,50);
      falling.splice(i,1);
    }
  }
  for(let i=overflowPx.length-1;i>=0;i--){
    const p=overflowPx[i];
    p.vy+=0.08;
    p.y+=p.vy;
    p.a-=0.005;
    if(p.a<=0)overflowPx.splice(i,1);
  }
  if(bowlCount>=40&&!lillyHappy){
    lillyHappy=true;happyTimer=180;
  }
  if(happyTimer>0)happyTimer--;
}

function render(){
  ctx.clearRect(0,0,W,H);
  drawBowl();
  drawBowlKibble();
  drawOverflow();
  drawHeart();
  drawFalling();
  drawHappy();
}

function loop(){update();render();requestAnimationFrame(loop);}
loop();

setInterval(()=>{
  if(heartHP<100)heartHP=Math.min(100,heartHP+0.15);
  document.getElementById('petHeartHP').textContent=Math.floor(heartHP);
},2500);

/* Poll activity and feed heart */
async function pollPetActivity(){
  try{
    const r=await fetch('/api/activity'),d=await r.json();
    const pts=d.track_points||d.elapsed_sec||0;
    if(pts>lastSteps){
      const diff=Math.min(pts-lastSteps,10);
      if(diff>0)releasePixel(diff);
    }
    lastSteps=pts;
  }catch(e){}
  setTimeout(pollPetActivity,2000);
}
pollPetActivity();

/* Feed on conversation interaction */
const _origDisplaySpeech=window.displaySpeech;
window.displaySpeech=function(text){
  if(_origDisplaySpeech)_origDisplaySpeech.call(this,text);
  releasePixel(1);
};

/* Feed on manual text input */
inputField.addEventListener('keydown',function(e){
  if(e.key==='Enter'&&inputField.value.trim()){
    releasePixel(2);
  }
});
})();

// ═══════════════════════════════════════════════════════════════
// ── SensorBridge — lets the agents feel the world through the  ──
// ── browser device sensors (motion, orientation, light, etc.)  ──
// ═══════════════════════════════════════════════════════════════
(function(){
'use strict';

// How often to POST a snapshot to the server (ms)
const POLL_MS = 800;

// Internal state
let _timer = null;
let _frame = {};
let _batteryObj = null;
let _granted = false;

// ── Banner UI ──────────────────────────────────────────────────
function _showBanner(){
  if(document.getElementById('sensorPermBanner')) return;
  const b = document.createElement('div');
  b.id = 'sensorPermBanner';
  b.innerHTML = `
    <span style="font-size:20px">🐶</span>
    <span style="flex:1;font-size:13px;line-height:1.4;color:#5d4e6d">
      <strong>Let the agents feel &amp; see the world!</strong><br>
      Allow motion, orientation &amp; camera so Lilly and the hive can sense your surroundings.
    </span>
    <button id="sensorPermAllow" style="background:rgba(184,169,201,0.5);border:1.5px solid rgba(184,169,201,0.6);border-radius:16px;padding:8px 18px;font-size:13px;font-weight:600;color:#5d4e6d;cursor:pointer;white-space:nowrap;font-family:inherit">Allow</button>
    <button id="sensorPermDismiss" style="background:none;border:none;font-size:18px;color:rgba(93,78,109,0.35);cursor:pointer;padding:2px 6px;line-height:1">✕</button>`;
  Object.assign(b.style, {
    position:'fixed', bottom:'90px', left:'50%', transform:'translateX(-50%)',
    width:'92%', maxWidth:'560px', zIndex:'200',
    display:'flex', alignItems:'center', gap:'12px',
    background:'rgba(255,255,255,0.72)', backdropFilter:'blur(20px)',
    WebkitBackdropFilter:'blur(20px)',
    border:'1px solid rgba(255,255,255,0.7)',
    borderRadius:'20px', padding:'14px 16px',
    boxShadow:'0 8px 32px rgba(180,140,180,0.18)',
    animation:'msgIn 0.3s ease-out',
  });
  document.body.appendChild(b);
  document.getElementById('sensorPermAllow').onclick = () => {
    b.remove();
    _requestAndStart();
    // Also start camera — getUserMedia triggers its own browser permission dialog
    if(window.CameraBridge){
      CameraBridge.start().then(()=>{
        localStorage.setItem('cameraPermGranted','1');
      }).catch(()=>{});
    }
  };
  document.getElementById('sensorPermDismiss').onclick = () => {
    b.remove();
    localStorage.setItem('sensorPermDismissed','1');
  };
}

function _hideBanner(){
  const b = document.getElementById('sensorPermBanner');
  if(b) b.remove();
}

// ── Permission Request ─────────────────────────────────────────
async function _requestAndStart(){
  // iOS 13+ requires a user-gesture to call requestPermission
  if(typeof DeviceMotionEvent !== 'undefined' &&
     typeof DeviceMotionEvent.requestPermission === 'function'){
    try {
      const r = await DeviceMotionEvent.requestPermission();
      if(r !== 'granted'){ console.warn('SensorBridge: motion permission denied'); return; }
    } catch(e){ console.warn('SensorBridge: requestPermission failed', e); return; }
  }
  if(typeof DeviceOrientationEvent !== 'undefined' &&
     typeof DeviceOrientationEvent.requestPermission === 'function'){
    try { await DeviceOrientationEvent.requestPermission(); } catch(_){}
  }
  localStorage.setItem('sensorPermGranted','1');
  _granted = true;
  _attachListeners();
  _startPolling();
}

// ── Event Listeners ────────────────────────────────────────────
function _attachListeners(){

  // DeviceMotionEvent — acceleration + rotation rate (all Android + iOS)
  window.addEventListener('devicemotion', (e) => {
    const a = e.accelerationIncludingGravity || e.acceleration;
    if(a && (a.x!=null || a.y!=null || a.z!=null)){
      _frame.acceleration = { x: a.x||0, y: a.y||0, z: a.z||0 };
    }
    const r = e.rotationRate;
    if(r && (r.alpha!=null || r.beta!=null || r.gamma!=null)){
      _frame.rotationRate = { alpha: r.alpha||0, beta: r.beta||0, gamma: r.gamma||0 };
    }
  }, true);

  // DeviceOrientationEvent — compass + tilt
  window.addEventListener('deviceorientation', (e) => {
    if(e.alpha!=null || e.beta!=null || e.gamma!=null){
      _frame.orientation = { alpha: e.alpha||0, beta: e.beta||0, gamma: e.gamma||0 };
    }
  }, true);

  // Generic Sensor API — Accelerometer (Chrome Android)
  if(typeof Accelerometer !== 'undefined'){
    try {
      const acc = new Accelerometer({ frequency: 5 });
      acc.addEventListener('reading', () => {
        _frame.accelerometer = { x: acc.x, y: acc.y, z: acc.z };
      });
      acc.start();
    } catch(_){}
  }

  // Generic Sensor API — Gyroscope
  if(typeof Gyroscope !== 'undefined'){
    try {
      const gyro = new Gyroscope({ frequency: 5 });
      gyro.addEventListener('reading', () => {
        _frame.gyroscope = { x: gyro.x, y: gyro.y, z: gyro.z };
      });
      gyro.start();
    } catch(_){}
  }

  // Generic Sensor API — Magnetometer
  if(typeof Magnetometer !== 'undefined'){
    try {
      const mag = new Magnetometer({ frequency: 2 });
      mag.addEventListener('reading', () => {
        _frame.magnetometer = { x: mag.x, y: mag.y, z: mag.z };
      });
      mag.start();
    } catch(_){}
  }

  // AmbientLightSensor
  if(typeof AmbientLightSensor !== 'undefined'){
    try {
      const als = new AmbientLightSensor({ frequency: 1 });
      als.addEventListener('reading', () => { _frame.light = als.illuminance; });
      als.start();
    } catch(_){}
  }

  // Battery Status API
  if(navigator.getBattery){
    navigator.getBattery().then(bat => {
      _batteryObj = bat;
      _frame.battery = { level: bat.level, charging: bat.charging };
      bat.addEventListener('levelchange', () => {
        _frame.battery = { level: bat.level, charging: bat.charging };
      });
      bat.addEventListener('chargingchange', () => {
        _frame.battery = { level: bat.level, charging: bat.charging };
      });
    }).catch(()=>{});
  }
}

// ── Polling — send frame to server ────────────────────────────
async function _sendFrame(){
  if(!_granted || !Object.keys(_frame).length) return;
  try {
    await fetch('/api/sensors/browser', {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify(_frame),
    });
  } catch(_){}
}

function _startPolling(){
  if(_timer) clearInterval(_timer);
  _timer = setInterval(_sendFrame, POLL_MS);
  _sendFrame(); // immediate first send
}

// ── Public init — called after login ──────────────────────────
window.SensorBridge = {
  init() {
    // Already granted a previous session
    if(localStorage.getItem('sensorPermGranted') === '1'){
      _granted = true;
      _attachListeners();
      _startPolling();
      return;
    }
    // Previously dismissed — don't re-ask
    if(localStorage.getItem('sensorPermDismissed') === '1') return;
    // Show the banner; user taps Allow to trigger requestPermission
    // (must be user-gesture on iOS, so we gate behind the banner tap)
    _showBanner();
  },
  stop(){ if(_timer){ clearInterval(_timer); _timer=null; } },
  reset(){
    localStorage.removeItem('sensorPermGranted');
    localStorage.removeItem('sensorPermDismissed');
  },
};

})(); // end SensorBridge IIFE

// ═══════════════════════════════════════════════════════════════
// ── CameraBridge — browser camera capture + Picture-in-Picture ──
// ── Sends frames to /api/vision/browser so all agents can see  ──
// ═══════════════════════════════════════════════════════════════
// FaceFilterEngine — lazy-loads MediaPipe Face Mesh for AR filters
// ═══════════════════════════════════════════════════════════════
const FaceFilterEngine = {
  _landmarker: null,
  _loading: false,
  _ready: false,
  _error: false,

  async init() {
    if (this._ready || this._loading || this._error) return;
    this._loading = true;
    try {
      const vision = await import('https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@0.10.14');
      const resolver = await vision.FilesetResolver.forVisionTasks(
        'https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@0.10.14/wasm'
      );
      this._landmarker = await vision.FaceLandmarker.createFromOptions(resolver, {
        baseOptions: {
          modelAssetPath: 'https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task',
          delegate: 'GPU',
        },
        runningMode: 'VIDEO',
        numFaces: 1,
        outputFaceBlendshapes: false,
        outputFacialTransformationMatrixes: false,
      });
      this._ready = true;
    } catch(e) {
      console.warn('FaceFilterEngine init failed:', e);
      this._error = true;
    }
    this._loading = false;
  },

  detect(video) {
    if (!this._ready || !video || video.readyState < 2) return null;
    try {
      return this._landmarker.detectForVideo(video, performance.now());
    } catch(e) {
      return null;
    }
  }
};

// ═══════════════════════════════════════════════════════════════
// CameraBridge — captures browser camera frames and POSTs to server
// ═══════════════════════════════════════════════════════════════
(function(){
'use strict';

const FRAME_INTERVAL_MS = 2000;   // capture a frame every 2s (balance quality vs bandwidth)
const JPEG_QUALITY      = 0.72;   // 0-1 JPEG compression
const MAX_DIMENSION     = 480;    // longest edge in px — enough for YOLO, keeps payload small

let _stream    = null;
let _video     = null;
let _canvas    = null;
let _timer     = null;
let _pipActive = false;
let _started   = false;

// ── Create hidden video + canvas elements ─────────────────────
function _ensureElements() {
  if (_video) return;

  _video = document.createElement('video');
  _video.id = 'cameraBridgeVideo';
  _video.autoplay = true;
  _video.playsInline = true;
  _video.muted = true;
  // Hide off-screen but keep in DOM so PiP works
  Object.assign(_video.style, {
    position: 'fixed', bottom: '0', right: '0',
    width: '1px', height: '1px', opacity: '0', pointerEvents: 'none',
  });
  document.body.appendChild(_video);

  _canvas = document.createElement('canvas');
  _canvas.style.display = 'none';
  document.body.appendChild(_canvas);
}

// ── Capture one JPEG frame and POST to server ─────────────────
async function _captureAndSend() {
  if (!_video || !_video.readyState || _video.readyState < 2) return;
  const vw = _video.videoWidth, vh = _video.videoHeight;
  if (!vw || !vh) return;

  // Scale down to MAX_DIMENSION on the longest edge
  let w = vw, h = vh;
  if (Math.max(w, h) > MAX_DIMENSION) {
    const scale = MAX_DIMENSION / Math.max(w, h);
    w = Math.round(w * scale);
    h = Math.round(h * scale);
  }
  _canvas.width = w;
  _canvas.height = h;
  const ctx = _canvas.getContext('2d');
  ctx.drawImage(_video, 0, 0, w, h);

  _canvas.toBlob(async (blob) => {
    if (!blob || blob.size < 500) return;
    const fd = new FormData();
    fd.append('file', blob, 'frame.jpg');
    try {
      const resp = await fetch('/api/vision/browser', { method: 'POST', body: fd });
      if (resp.ok) {
        const data = await resp.json();
        // Expose latest description globally so the UI can show it
        window._cameraDescription = data.description || '';
      }
    } catch (_) {}
  }, 'image/jpeg', JPEG_QUALITY);
}

// ── Picture-in-Picture toggle ─────────────────────────────────
async function _enterPiP() {
  if (!_video || !document.pictureInPictureEnabled) return;
  try {
    if (document.pictureInPictureElement) {
      await document.exitPictureInPicture();
      _pipActive = false;
    } else {
      await _video.requestPictureInPicture();
      _pipActive = true;
    }
  } catch (e) { console.warn('CameraBridge PiP:', e); }
}

// Update PiP button label if one exists in the UI
function _updatePiPBtn() {
  const btn = document.getElementById('cameraPiPBtn');
  if (btn) btn.textContent = _pipActive ? '📷 Exit PiP' : '📷 Camera PiP';
}

// ── Start the camera ──────────────────────────────────────────
async function _start(facingMode) {
  if (_started) return;
  _ensureElements();

  const constraints = {
    video: {
      facingMode: facingMode || (isMobile() ? 'environment' : 'user'),
      width:  { ideal: 640 },
      height: { ideal: 480 },
    },
    audio: false,
  };

  try {
    _stream = await navigator.mediaDevices.getUserMedia(constraints);
  } catch (e) {
    console.warn('CameraBridge: getUserMedia failed, trying any camera:', e);
    try {
      _stream = await navigator.mediaDevices.getUserMedia({ video: true, audio: false });
    } catch (e2) {
      console.warn('CameraBridge: no camera available:', e2);
      return;
    }
  }

  _video.srcObject = _stream;
  await _video.play().catch(() => {});
  _started = true;

  // Start frame capture loop
  _timer = setInterval(_captureAndSend, FRAME_INTERVAL_MS);
  _captureAndSend(); // immediate first frame

  console.log('CameraBridge: started, facing=' + (facingMode || 'auto'));
}

function _stop() {
  if (_timer)  { clearInterval(_timer); _timer = null; }
  if (_stream) { _stream.getTracks().forEach(t => t.stop()); _stream = null; }
  if (_video)  { _video.srcObject = null; }
  _started = false;
  _pipActive = false;
}

function isMobile() {
  return /Android|iPhone|iPad|iPod/i.test(navigator.userAgent);
}

// ── Public API ────────────────────────────────────────────────
window.CameraBridge = {
  start(facingMode) { return _start(facingMode); },
  stop()            { _stop(); },
  togglePiP()       { return _enterPiP().then(_updatePiPBtn); },
  get active()      { return _started; },
  get pipActive()   { return _pipActive; },
};

})(); // end CameraBridge IIFE
</script>

<!-- APK variants + Pairing + File Share panel -->
<div id="phonePanel" style="position:fixed;bottom:20px;right:20px;z-index:9999;display:flex;flex-direction:column;align-items:flex-end;gap:8px">
  <div id="phonePanelBtn" onclick="togglePhonePanel()"
       style="display:inline-flex;align-items:center;gap:8px;padding:10px 18px;
              background:rgba(255,255,255,0.5);backdrop-filter:blur(16px);
              border:1px solid rgba(255,255,255,0.6);border-radius:14px;
              color:#5d4e6d;font-size:13px;font-weight:500;
              cursor:pointer;box-shadow:0 4px 20px rgba(180,140,180,0.12);
              transition:all 0.25s"
       onmouseover="this.style.background='rgba(224,214,238,0.7)'"
       onmouseout="this.style.background='rgba(255,255,255,0.5)'">
    <span style="font-size:18px">📱</span>
    <span id="phonePanelLabel">Get Phone Overlay</span>
    <span style="font-size:10px;opacity:0.5" id="phonePanelArrow">▼</span>
  </div>
  <div id="phonePanelBody" style="display:none;flex-direction:column;gap:8px;width:260px;
       background:rgba(255,255,255,0.5);backdrop-filter:blur(16px);
       border:1px solid rgba(255,255,255,0.6);border-radius:14px;
       padding:12px;color:#5d4e6d;font-size:12px;box-shadow:0 4px 20px rgba(180,140,180,0.12)">
    <div style="font-weight:600;margin-bottom:2px">Phone Overlay APK</div>
    <div id="apkVariantList"></div>
    <div style="border-top:1px solid rgba(90,70,120,0.15);margin:4px 0;padding-top:8px">
      <div style="font-weight:600;margin-bottom:6px">Pair Overlay</div>
      <div id="pairingSection">
        <div style="font-size:11px;opacity:0.7;margin-bottom:6px">Sign in to generate a pairing code, then enter it in the Overlay app.</div>
        <div id="pairingCodeDisplay" style="display:none;background:rgba(90,70,120,0.12);border-radius:10px;padding:10px;text-align:center;margin-bottom:6px">
          <div style="font-size:10px;opacity:0.6;margin-bottom:4px">Your pairing code</div>
          <div id="pairingCodeValue" style="font-size:22px;font-weight:700;letter-spacing:4px;color:#4a3a5a;font-family:monospace"></div>
          <div style="font-size:10px;opacity:0.5;margin-top:4px">Expires in 5 minutes</div>
        </div>
        <div id="pairingSignInPrompt" style="font-size:11px;opacity:0.5;font-style:italic">Sign in with Google to pair your overlay.</div>
      </div>
    </div>
  </div>
</div>
<script>
let phonePanelOpen=false;
function togglePhonePanel(){phonePanelOpen=!phonePanelOpen;var b=document.getElementById('phonePanelBody');b.style.display=phonePanelOpen?'flex':'none';document.getElementById('phonePanelArrow').textContent=phonePanelOpen?'▲':'▼';if(phonePanelOpen){loadApkVariants();generatePairingCode()}}
function loadApkVariants(){fetch('/api/apk/variants').then(function(r){return r.json()}).then(function(list){var el=document.getElementById('apkVariantList');el.innerHTML='';if(!list.length){el.innerHTML='<div style="opacity:0.5;padding:4px 0">No APK builds found</div>';return}
list.forEach(function(f){var a=document.createElement('a');a.href='/api/apk/download?variant='+encodeURIComponent(f.filename.replace(/^lilly-overlay-/,'').replace(/\.apk$/,''));a.style.display='flex';a.style.alignItems='center';a.style.justifyContent='space-between';a.style.padding='6px 8px';a.style.borderRadius='8px';a.style.background='rgba(255,255,255,0.4)';a.style.textDecoration='none';a.style.color='#5d4e6d';a.style.fontSize='11px';a.style.marginBottom='3px';a.title='Download '+f.filename;var name=document.createElement('span');name.textContent=f.filename.replace(/^lilly-overlay-/,'').replace(/\.apk$/,'');var size=document.createElement('span');size.style.opacity='0.5';size.textContent=(f.size/1024/1024).toFixed(1)+'MB';a.appendChild(name);a.appendChild(size);el.appendChild(a)})})}
async function generatePairingCode(){try{var r=await fetch('/api/pair/code',{method:'POST'});if(!r.ok){document.getElementById('pairingSignInPrompt').style.display='block';document.getElementById('pairingCodeDisplay').style.display='none';return}
var data=await r.json();document.getElementById('pairingSignInPrompt').style.display='none';document.getElementById('pairingCodeDisplay').style.display='block';document.getElementById('pairingCodeValue').textContent=data.code}catch(e){}}
</script>
</body>
</html>"""

@app.get("/clerk", response_class=HTMLResponse)
async def clerk_landing():
    return RedirectResponse("/")

OVERLAY_PAGE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0,maximum-scale=1.0,user-scalable=no">
<title>Lilly</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{
  background:transparent;
  overflow:visible;
  font-family:'Segoe UI',system-ui,-apple-system,sans-serif;
  width:96px;height:96px;
  user-select:none;-webkit-user-select:none;
  touch-action:manipulation;
}
#container{
  width:96px;height:96px;
  position:relative;
  background:transparent;
  transition:all 0.3s;
}
#container.expanded{
  width:96px;height:320px;
}
body.expanded{
  width:96px;height:320px;
}
#pupCanvas{
  position:absolute;
  top:-16px;left:-16px;
  width:128px;height:128px;
  image-rendering:auto;
  touch-action:none;
  z-index:2;
  cursor:pointer;
}
#dragHandle{
  position:absolute;
  top:-16px;left:-16px;
  width:128px;height:128px;
  z-index:5;
  touch-action:none;
}
#avatarName{
  position:absolute;
  top:98px;left:48px;
  transform:translateX(-50%);
  font-size:8px;
  color:rgba(255,255,255,0.3);
  letter-spacing:0.5px;
  text-transform:uppercase;
  z-index:6;
  pointer-events:none;
  white-space:nowrap;
  display:none;
}
#closeHint{
  position:fixed;bottom:20px;left:50%;transform:translateX(-50%);
  width:44px;height:44px;border-radius:50%;
  background:rgba(239,68,68,0.85);
  color:#fff;font-size:22px;
  display:flex;align-items:center;justify-content:center;
  z-index:100;pointer-events:none;
  opacity:0;transition:opacity 0.2s;
  backdrop-filter:blur(8px);-webkit-backdrop-filter:blur(8px);
  border:2px solid rgba(255,255,255,0.3);
  box-shadow:0 4px 20px rgba(239,68,68,0.3);
}

/* ─── Compact vertical chat panel (expanded) ─── */
#chatBubble{
  position:absolute;
  top:112px;left:0;
  width:96px;
  background:rgba(15,15,40,0.78);
  backdrop-filter:blur(12px);
  -webkit-backdrop-filter:blur(12px);
  border-radius:0 0 12px 12px;
  border:1px solid rgba(255,255,255,0.08);
  border-top:none;
  box-shadow:0 4px 16px rgba(0,0,0,0.5);
  padding:4px 6px 6px;
  display:none;
  z-index:10;
  transition:opacity 0.25s;
}
#chatBubble.show{
  display:block;
  opacity:1;
}
#bubbleTail{display:none}
#bubbleText{
  font-size:10px;
  line-height:1.3;
  color:rgba(255,255,255,0.85);
  max-height:72px;
  min-height:20px;
  overflow-y:auto;
  scrollbar-width:thin;
  word-wrap:break-word;
  margin-bottom:2px;
  pointer-events:none;
}
#bubbleText::-webkit-scrollbar{width:2px}
#bubbleText::-webkit-scrollbar-thumb{background:rgba(255,255,255,0.12);border-radius:2px}
#bubbleStatus{
  display:flex;
  align-items:center;
  gap:3px;
  font-size:8px;
  color:rgba(255,255,255,0.35);
  margin-bottom:3px;
  pointer-events:none;
}
#statusDot{
  width:4px;height:4px;
  border-radius:50%;
  background:#4ade80;
  transition:all 0.3s;
  flex-shrink:0;
}
#statusDot.listening{background:#4ade80;box-shadow:0 0 4px #4ade80}
#statusDot.thinking{background:#fbbf24;box-shadow:0 0 4px #fbbf24;animation:pulse 0.8s infinite}
#statusDot.speaking{background:#f472b6;box-shadow:0 0 4px #f472b6;animation:pulse 0.5s infinite}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:0.4}}
#bubbleInput{
  display:flex;
  gap:3px;
  align-items:center;
  pointer-events:auto;
}
#chatInput{
  flex:1;
  background:rgba(255,255,255,0.08);
  border:1px solid rgba(255,255,255,0.12);
  border-radius:10px;
  padding:4px 6px;
  font-size:10px;
  color:rgba(255,255,255,0.85);
  outline:none;
  min-width:0;
  width:38px;
}
#chatInput::placeholder{color:rgba(255,255,255,0.25)}
#chatInput:focus{border-color:rgba(255,255,255,0.2)}
.iconBtn{
  width:22px;height:22px;
  border-radius:50%;
  border:none;
  display:flex;
  align-items:center;
  justify-content:center;
  cursor:pointer;
  transition:all 0.2s;
  font-size:11px;
  flex-shrink:0;
}
#micBtn{
  background:rgba(74,222,128,0.2);
  color:#4ade80;
}
#micBtn.active{background:rgba(74,222,128,0.35);box-shadow:0 0 6px rgba(74,222,128,0.25)}
#micBtn.muted{background:rgba(239,68,68,0.2);color:#ef4444}
#sendBtn{
  background:rgba(168,85,247,0.2);
  color:#c084fc;
}
.iconBtn:active{transform:scale(0.85)}

/* ─── Avatar picker strip (inside chatBubble) ─── */
#avatarStrip{
  display:none;
  flex-wrap:wrap;
  gap:3px;
  justify-content:center;
  padding:4px 0 2px;
  margin-top:4px;
  border-top:1px solid rgba(255,255,255,0.08);
}
#avatarStrip.show{display:flex}
.avatarPickBtn{
  width:26px;height:26px;
  border-radius:50%;
  border:2px solid rgba(255,255,255,0.12);
  background:rgba(255,255,255,0.06);
  cursor:pointer;
  font-size:10px;
  display:flex;
  align-items:center;
  justify-content:center;
  transition:all 0.2s;
  color:rgba(255,255,255,0.5);
  padding:0;
}
.avatarPickBtn.active{
  border-color:#c084fc;
  background:rgba(168,85,247,0.2);
  color:#c084fc;
  box-shadow:0 0 6px rgba(168,85,247,0.2);
}
.avatarPickBtn:active{transform:scale(0.85)}
</style>
</head>
<body>
<div id="container">
  <canvas id="pupCanvas" width="320" height="240"></canvas>
  <div id="dragHandle"></div>
  <div id="avatarName"></div>
  <div id="closeHint">✕</div>
  <div id="chatBubble">
    <div id="bubbleTail"></div>
    <div id="bubbleText">Say hi to start talking!</div>
    <div id="bubbleStatus">
      <span id="statusDot"></span>
      <span id="statusLabel">idle</span>
    </div>
    <div id="bubbleInput">
      <button class="iconBtn" id="micBtn">🎤</button>
      <input id="chatInput" type="text" placeholder="Message..." autocomplete="off">
      <button class="iconBtn" id="sendBtn">➤</button>
    </div>
    <div id="avatarStrip"></div>
  </div>
</div>
<script>
const canvas=document.getElementById('pupCanvas');
const ctx=canvas.getContext('2d');
if(!ctx){document.body.innerHTML='<div style="padding:20px;color:#fff">Canvas unavailable</div>';throw new Error('no ctx')}

// Auth: session cookie (auto-sent) + optional device token fallback
const DEVICE_TOKEN=localStorage.getItem('lilly_device_token')||'';
function authFetch(url,opts){
  opts=opts||{};
  opts.headers=opts.headers||{};
  opts.credentials='include';
  if(DEVICE_TOKEN)opts.headers['X-Device-Token']=DEVICE_TOKEN;
  return fetch(url,opts);
}
const W=320,H=240;

let avatar=new URLSearchParams(location.search).get('avatar')||'puppy', mouthOpen=0, mood='calm', speaking=false, thinking=false, listening=false;
let micActive=false, userName='', heard='', spoken='', lastAudioId=0;
let frame=0, isBlinking=false, blinkFrame=200;
let lookAt='user';

// ── Clay-style drawing helpers (shared by all avatars) ──
function clayFill(c,h){const g=c.createRadialGradient(0,0,0,0,0,1);g.addColorStop(0,lighten(h,16));g.addColorStop(0.6,h);g.addColorStop(1,darken(h,12));return g}
function lighten(h,a){let r=parseInt(h.slice(1,3),16),g=parseInt(h.slice(3,5),16),b=parseInt(h.slice(5,7),16);a>0?(r=Math.min(255,r+a),g=Math.min(255,g+a),b=Math.min(255,b+a)):(r=Math.max(0,r+a),g=Math.max(0,g+a),b=Math.max(0,b+a));return'rgb('+r+','+g+','+b+')'}
function darken(h,a){return lighten(h,-a)}
function breathe(){return Math.sin(frame*0.04)*3}

// ── Transparent sphere background ──
function drawClayBg(ctx2, W, H, color) {
  ctx2.clearRect(0, 0, W, H);
  ctx2.save();
  const r = W * 0.47;
  ctx2.shadowColor = color;
  ctx2.shadowBlur  = W * 0.15;
  ctx2.fillStyle = 'rgba(255,255,255,0.02)';
  ctx2.beginPath(); ctx2.arc(W/2, H/2, r, 0, Math.PI*2); ctx2.fill();
  ctx2.shadowBlur = 0;
  const g = ctx2.createRadialGradient(W*0.30, H*0.26, W*0.03, W*0.5, H*0.5, r);
  g.addColorStop(0,   'rgba(255,255,255,0.15)');
  g.addColorStop(0.50,'rgba(255,255,255,0.03)');
  g.addColorStop(1,   'rgba(0,0,0,0.05)');
  ctx2.fillStyle = g;
  ctx2.beginPath(); ctx2.arc(W/2, H/2, r, 0, Math.PI*2); ctx2.fill();
  ctx2.restore();
}

// ── Shared eyes ──
function drawClayEyes(ctx2, cx, cy, r, frame2) {
  const blinking = (Math.floor(frame2 / 90) % 14 === 0);
  const eyeR = r * 0.12;
  const lx = cx - r*0.26, rx2 = cx + r*0.26, ey = cy;
  ctx2.save();
  if (blinking) {
    ctx2.strokeStyle = 'rgba(60,40,80,0.55)'; ctx2.lineWidth = r*0.045; ctx2.lineCap = 'round';
    ctx2.beginPath(); ctx2.moveTo(lx-eyeR,ey); ctx2.lineTo(lx+eyeR,ey); ctx2.stroke();
    ctx2.beginPath(); ctx2.moveTo(rx2-eyeR,ey); ctx2.lineTo(rx2+eyeR,ey); ctx2.stroke();
  } else {
    for (const ex of [lx, rx2]) {
      ctx2.shadowColor = 'rgba(80,50,100,0.22)'; ctx2.shadowBlur = r*0.07;
      ctx2.fillStyle = '#fff';
      ctx2.beginPath(); ctx2.arc(ex, ey, eyeR, 0, Math.PI*2); ctx2.fill();
      ctx2.shadowBlur = 0;
      ctx2.fillStyle = 'rgba(90,60,120,0.15)';
      ctx2.beginPath(); ctx2.arc(ex, ey, eyeR*0.82, 0, Math.PI*2); ctx2.fill();
      ctx2.fillStyle = '#3d2e52';
      ctx2.beginPath(); ctx2.arc(ex, ey, eyeR*0.46, 0, Math.PI*2); ctx2.fill();
      ctx2.fillStyle = 'rgba(255,255,255,0.88)';
      ctx2.beginPath(); ctx2.arc(ex - eyeR*0.12, ey - eyeR*0.14, eyeR*0.22, 0, Math.PI*2); ctx2.fill();
      ctx2.fillStyle = 'rgba(255,255,255,0.55)';
      ctx2.beginPath(); ctx2.arc(ex + eyeR*0.12, ey + eyeR*0.10, eyeR*0.09, 0, Math.PI*2); ctx2.fill();
    }
  }
  ctx2.restore();
}

function drawRaccoonEyes(ctx2, cx, cy, r, frame2) {
  const blinking = (Math.floor(frame2 / 90) % 14 === 0);
  const eyeR = r * 0.15;
  const lx = cx - r*0.26, rx2 = cx + r*0.26, ey = cy;
  ctx2.save();
  if (blinking) {
    ctx2.strokeStyle = 'rgba(40,40,40,0.55)'; ctx2.lineWidth = r*0.045; ctx2.lineCap = 'round';
    ctx2.beginPath(); ctx2.moveTo(lx-eyeR,ey); ctx2.lineTo(lx+eyeR,ey); ctx2.stroke();
    ctx2.beginPath(); ctx2.moveTo(rx2-eyeR,ey); ctx2.lineTo(rx2+eyeR,ey); ctx2.stroke();
  } else {
    for (const ex of [lx, rx2]) {
      ctx2.shadowColor = 'rgba(40,40,40,0.22)'; ctx2.shadowBlur = r*0.07;
      ctx2.fillStyle = '#ffffff';
      ctx2.beginPath(); ctx2.arc(ex, ey, eyeR, 0, Math.PI*2); ctx2.fill();
      ctx2.shadowBlur = 0;
      ctx2.fillStyle = 'rgba(40,40,40,0.12)';
      ctx2.beginPath(); ctx2.arc(ex, ey, eyeR*0.82, 0, Math.PI*2); ctx2.fill();
      ctx2.fillStyle = '#2a2a2a';
      ctx2.beginPath(); ctx2.arc(ex, ey, eyeR*0.48, 0, Math.PI*2); ctx2.fill();
      ctx2.fillStyle = 'rgba(255,255,255,0.92)';
      ctx2.beginPath(); ctx2.arc(ex - eyeR*0.14, ey - eyeR*0.16, eyeR*0.24, 0, Math.PI*2); ctx2.fill();
      ctx2.fillStyle = 'rgba(255,255,255,0.6)';
      ctx2.beginPath(); ctx2.arc(ex + eyeR*0.12, ey + eyeR*0.10, eyeR*0.10, 0, Math.PI*2); ctx2.fill();
    }
  }
  ctx2.restore();
}

// ── Shared smile ──
function drawClaySmile(ctx2, cx, cy, r) {
  ctx2.save();
  ctx2.strokeStyle = 'rgba(120,80,100,0.42)'; ctx2.lineWidth = r*0.05; ctx2.lineCap = 'round';
  ctx2.beginPath(); ctx2.arc(cx - r*0.11, cy + r*0.28, r*0.11, 0.08, Math.PI*0.78); ctx2.stroke();
  ctx2.beginPath(); ctx2.arc(cx + r*0.11, cy + r*0.28, r*0.11, Math.PI*0.22, Math.PI*0.92); ctx2.stroke();
  ctx2.restore();
}

// ── Per-animal clay face ──
function drawAnimalFace(ctx2, animal, cx, cy, r, frame2, noBg) {
  const W2 = ctx2.canvas.width, H2 = ctx2.canvas.height;
  const breathe2 = Math.sin(frame2 * 0.04) * (r * 0.018);
  const PAL = {
    puppy:{ bg:'#e8e4f0', head:'#d4d0de', muzzle:'#c8c4d2', ear:'#c4c0cc', earInner:'#f0d8e8', nose:'#d4a0b8' },
    fox:  { bg:'#f5e4d0', head:'#e8905a', muzzle:'#f8e0c0', ear:'#e07840', earInner:'#f8c080', nose:'#c05030' },
    cat:  { bg:'#ece8f4', head:'#c8c0d4', muzzle:'#f0dce8', ear:'#b8b0c4', earInner:'#f0c8dc', nose:'#d898b0' },
    bear: { bg:'#ede0d8', head:'#b09080', muzzle:'#cdb8a8', ear:'#a08070', earInner:'#c8a890', nose:'#7a5a4a' },
    bunny:{ bg:'#eeeaf6', head:'#d4cce0', muzzle:'#c8c0d4', ear:'#ccc4d8', earInner:'#f0cce0', nose:'#e8a8c0' },
    owl:  { bg:'#e8e0d0', head:'#8a7a60', muzzle:'#c8b898', ear:'#7a6a50', earInner:'#c0a878', nose:'#5a4a38' },
    deer: { bg:'#f0e8d8', head:'#c8a888', muzzle:'#e8d8c0', ear:'#b89870', earInner:'#e0c8a8', nose:'#5a4838' },
    wolf: { bg:'#e0dde4', head:'#706878', muzzle:'#a898a8', ear:'#605868', earInner:'#a090a8', nose:'#383040' },
    raccoon:{ bg:'#e0e0e4', head:'#808080', muzzle:'#c0c0c0', ear:'#686868', earInner:'#a8a8a8', nose:'#404040' },
  };
  const p = PAL[animal] || PAL.puppy;
  if(!noBg) drawClayBg(ctx2, W2, H2, p.bg);
  ctx2.save();
  ctx2.translate(cx, cy + breathe2);
  if (animal === 'puppy') {
    const earWig = Math.sin(frame2 * 0.18) * 0.08, earBob = Math.sin(frame2 * 0.14) * r*0.03;
    ctx2.save(); ctx2.translate(-r*0.60, -r*0.38 + earBob); ctx2.rotate(-0.26 + earWig);
    ctx2.shadowColor='rgba(80,60,100,0.22)'; ctx2.shadowBlur=r*0.14; ctx2.shadowOffsetY=r*0.06;
    ctx2.fillStyle = clayFill(ctx2, p.ear, lighten(p.ear,30));
    ctx2.beginPath(); ctx2.ellipse(0,0,r*0.26,r*0.52,0,0,Math.PI*2); ctx2.fill();
    ctx2.shadowBlur=0; ctx2.shadowOffsetY=0;
    ctx2.fillStyle = p.earInner; ctx2.beginPath(); ctx2.ellipse(0,r*0.06,r*0.14,r*0.36,0,0,Math.PI*2); ctx2.fill();
    ctx2.restore();
    ctx2.save(); ctx2.translate(r*0.60, -r*0.38 + earBob); ctx2.rotate(0.26 - earWig);
    ctx2.shadowColor='rgba(80,60,100,0.22)'; ctx2.shadowBlur=r*0.14; ctx2.shadowOffsetY=r*0.06;
    ctx2.fillStyle = clayFill(ctx2, p.ear, lighten(p.ear,30));
    ctx2.beginPath(); ctx2.ellipse(0,0,r*0.26,r*0.52,0,0,Math.PI*2); ctx2.fill();
    ctx2.shadowBlur=0; ctx2.shadowOffsetY=0;
    ctx2.fillStyle = p.earInner; ctx2.beginPath(); ctx2.ellipse(0,r*0.06,r*0.14,r*0.36,0,0,Math.PI*2); ctx2.fill();
    ctx2.restore();
    ctx2.shadowColor='rgba(80,60,100,0.20)'; ctx2.shadowBlur=r*0.18; ctx2.shadowOffsetY=r*0.07;
    ctx2.fillStyle = clayFill(ctx2, p.head, lighten(p.head,30));
    ctx2.beginPath(); ctx2.roundRect(-r*0.70,-r*0.46,r*1.40,r*1.08,[r*0.38,r*0.38,r*0.28,r*0.28]); ctx2.fill();
    ctx2.shadowBlur=0; ctx2.shadowOffsetY=0;
    ctx2.fillStyle = clayFill(ctx2, p.muzzle, lighten(p.muzzle,22));
    ctx2.beginPath(); ctx2.ellipse(0,r*0.30,r*0.40,r*0.24,0,0,Math.PI*2); ctx2.fill();
    ctx2.shadowColor='rgba(80,40,60,0.25)'; ctx2.shadowBlur=r*0.08;
    ctx2.fillStyle = clayFill(ctx2, p.nose, lighten(p.nose,28));
    ctx2.beginPath(); ctx2.ellipse(0,r*0.14,r*0.11,r*0.08,0,0,Math.PI*2); ctx2.fill();
    ctx2.shadowBlur=0;
  } else if (animal === 'fox') {
    const foxTwitch = Math.sin(frame2 * 0.12) * 0.12, foxTwitch2 = Math.sin(frame2 * 0.12 + Math.PI) * 0.12;
    for (const [side, twitch] of [[-1, foxTwitch], [1, foxTwitch2]]) {
      ctx2.save(); ctx2.scale(side,1); ctx2.translate(r*0.38, -r*0.52); ctx2.rotate(twitch); ctx2.translate(-r*0.38, r*0.52);
      ctx2.shadowColor='rgba(100,50,20,0.22)'; ctx2.shadowBlur=r*0.12; ctx2.shadowOffsetY=r*0.04;
      ctx2.fillStyle = clayFill(ctx2, p.ear, lighten(p.ear,28));
      ctx2.beginPath(); ctx2.moveTo(r*0.14,-r*0.40); ctx2.lineTo(r*0.38,-r*0.92); ctx2.lineTo(r*0.64,-r*0.38); ctx2.closePath(); ctx2.fill();
      ctx2.shadowBlur=0;
      ctx2.fillStyle = p.earInner;
      ctx2.beginPath(); ctx2.moveTo(r*0.20,-r*0.42); ctx2.lineTo(r*0.37,-r*0.76); ctx2.lineTo(r*0.54,-r*0.40); ctx2.closePath(); ctx2.fill();
      ctx2.restore();
    }
    ctx2.shadowColor='rgba(100,50,20,0.20)'; ctx2.shadowBlur=r*0.18; ctx2.shadowOffsetY=r*0.07;
    ctx2.fillStyle = clayFill(ctx2, p.head, lighten(p.head,28));
    ctx2.beginPath(); ctx2.roundRect(-r*0.70,-r*0.42,r*1.40,r*1.02,[r*0.34,r*0.34,r*0.26,r*0.26]); ctx2.fill();
    ctx2.shadowBlur=0;
    ctx2.fillStyle = clayFill(ctx2, p.muzzle, lighten(p.muzzle,30));
    ctx2.beginPath(); ctx2.ellipse(0,r*0.18,r*0.44,r*0.38,0,0,Math.PI*2); ctx2.fill();
    ctx2.shadowColor='rgba(80,30,10,0.25)'; ctx2.shadowBlur=r*0.08;
    ctx2.fillStyle = clayFill(ctx2, p.nose, lighten(p.nose,24));
    ctx2.beginPath(); ctx2.ellipse(0,r*0.10,r*0.13,r*0.09,0,0,Math.PI*2); ctx2.fill();
    ctx2.shadowBlur=0;
    ctx2.fillStyle='rgba(100,60,30,0.35)';
    for(const [wx,wy] of [[-r*0.28,r*0.16],[-r*0.38,r*0.22],[-r*0.42,r*0.30],[r*0.28,r*0.16],[r*0.38,r*0.22],[r*0.42,r*0.30]]) {
      ctx2.beginPath(); ctx2.arc(wx,wy,r*0.018,0,Math.PI*2); ctx2.fill();
    }
  } else if (animal === 'cat') {
    const catListen = Math.sin(frame2 * 0.035) * 0.18, catListen2 = Math.sin(frame2 * 0.035 + 0.6) * 0.18;
    for (const [side, listenAngle] of [[-1, catListen], [1, catListen2]]) {
      ctx2.save(); ctx2.scale(side,1); ctx2.translate(r*0.40, -r*0.50); ctx2.rotate(listenAngle); ctx2.translate(-r*0.40, r*0.50);
      ctx2.shadowColor='rgba(60,40,80,0.20)'; ctx2.shadowBlur=r*0.10;
      ctx2.fillStyle = clayFill(ctx2, p.ear, lighten(p.ear,28));
      ctx2.beginPath(); ctx2.moveTo(r*0.20,-r*0.38); ctx2.lineTo(r*0.46,-r*0.90); ctx2.lineTo(r*0.62,-r*0.36); ctx2.closePath(); ctx2.fill();
      ctx2.shadowBlur=0;
      ctx2.fillStyle = p.earInner;
      ctx2.beginPath(); ctx2.moveTo(r*0.26,-r*0.40); ctx2.lineTo(r*0.45,-r*0.74); ctx2.lineTo(r*0.56,-r*0.38); ctx2.closePath(); ctx2.fill();
      ctx2.restore();
    }
    ctx2.shadowColor='rgba(60,40,80,0.18)'; ctx2.shadowBlur=r*0.18; ctx2.shadowOffsetY=r*0.07;
    ctx2.fillStyle = clayFill(ctx2, p.head, lighten(p.head,28));
    ctx2.beginPath(); ctx2.roundRect(-r*0.68,-r*0.42,r*1.36,r*1.04,[r*0.32,r*0.32,r*0.28,r*0.28]); ctx2.fill();
    ctx2.shadowBlur=0; ctx2.shadowOffsetY=0;
    ctx2.fillStyle = clayFill(ctx2, p.muzzle, lighten(p.muzzle,22));
    ctx2.beginPath(); ctx2.ellipse(0,r*0.28,r*0.34,r*0.22,0,0,Math.PI*2); ctx2.fill();
    ctx2.shadowColor='rgba(80,30,60,0.22)'; ctx2.shadowBlur=r*0.07;
    ctx2.fillStyle = clayFill(ctx2, p.nose, lighten(p.nose,26));
    ctx2.beginPath(); ctx2.ellipse(0,r*0.13,r*0.10,r*0.07,0,0,Math.PI*2); ctx2.fill();
    ctx2.shadowBlur=0;
    ctx2.strokeStyle='rgba(80,60,100,0.28)'; ctx2.lineWidth=r*0.025; ctx2.lineCap='round';
    for(const s of [-1,1]) {
      ctx2.beginPath(); ctx2.moveTo(s*r*0.08,r*0.20); ctx2.lineTo(s*r*0.52,r*0.13); ctx2.stroke();
      ctx2.beginPath(); ctx2.moveTo(s*r*0.08,r*0.26); ctx2.lineTo(s*r*0.52,r*0.26); ctx2.stroke();
    }
  } else if (animal === 'bear') {
    const bearRotate = Math.sin(frame2 * 0.04) * 0.06;
    for(const [ex, rot] of [[-r*0.58, -bearRotate], [r*0.58, bearRotate]]) {
      ctx2.save(); ctx2.translate(ex, -r*0.50); ctx2.rotate(rot);
      ctx2.shadowColor='rgba(60,40,20,0.22)'; ctx2.shadowBlur=r*0.12;
      ctx2.fillStyle = clayFill(ctx2, p.ear, lighten(p.ear,24));
      ctx2.beginPath(); ctx2.arc(0, 0, r*0.28, 0, Math.PI*2); ctx2.fill();
      ctx2.shadowBlur=0;
      ctx2.fillStyle = clayFill(ctx2, p.earInner, lighten(p.earInner,20));
      ctx2.beginPath(); ctx2.arc(0, 0, r*0.17, 0, Math.PI*2); ctx2.fill();
      ctx2.restore();
    }
    ctx2.shadowColor='rgba(60,40,20,0.20)'; ctx2.shadowBlur=r*0.20; ctx2.shadowOffsetY=r*0.08;
    ctx2.fillStyle = clayFill(ctx2, p.head, lighten(p.head,26));
    ctx2.beginPath(); ctx2.roundRect(-r*0.70,-r*0.44,r*1.40,r*1.10,[r*0.42,r*0.42,r*0.32,r*0.32]); ctx2.fill();
    ctx2.shadowBlur=0; ctx2.shadowOffsetY=0;
    ctx2.fillStyle = clayFill(ctx2, p.muzzle, lighten(p.muzzle,24));
    ctx2.beginPath(); ctx2.ellipse(0,r*0.28,r*0.40,r*0.26,0,0,Math.PI*2); ctx2.fill();
    ctx2.shadowColor='rgba(40,20,10,0.28)'; ctx2.shadowBlur=r*0.08;
    ctx2.fillStyle = clayFill(ctx2, p.nose, lighten(p.nose,20));
    ctx2.beginPath(); ctx2.ellipse(0,r*0.12,r*0.15,r*0.10,0,0,Math.PI*2); ctx2.fill();
    ctx2.shadowBlur=0;
  } else if (animal === 'bunny') {
    const earSway = Math.sin(frame2 * 0.06) * 0.08, earDroop = Math.sin(frame2 * 0.03) * 0.04;
    ctx2.save(); ctx2.translate(-r*0.30,-r*0.44); ctx2.rotate(-0.14 + earSway + earDroop);
    ctx2.shadowColor='rgba(80,60,100,0.20)'; ctx2.shadowBlur=r*0.12; ctx2.shadowOffsetY=r*0.04;
    ctx2.fillStyle = clayFill(ctx2, p.ear, lighten(p.ear,28));
    ctx2.beginPath(); ctx2.ellipse(0,-r*0.38,r*0.15,r*0.38,0,0,Math.PI*2); ctx2.fill();
    ctx2.shadowBlur=0;
    ctx2.fillStyle = p.earInner; ctx2.beginPath(); ctx2.ellipse(0,-r*0.38,r*0.07,r*0.28,0,0,Math.PI*2); ctx2.fill();
    ctx2.restore();
    ctx2.save(); ctx2.translate(r*0.30,-r*0.44); ctx2.rotate(0.14 - earSway - earDroop);
    ctx2.shadowColor='rgba(80,60,100,0.20)'; ctx2.shadowBlur=r*0.12; ctx2.shadowOffsetY=r*0.04;
    ctx2.fillStyle = clayFill(ctx2, p.ear, lighten(p.ear,28));
    ctx2.beginPath(); ctx2.ellipse(0,-r*0.38,r*0.15,r*0.38,0,0,Math.PI*2); ctx2.fill();
    ctx2.shadowBlur=0;
    ctx2.fillStyle = p.earInner; ctx2.beginPath(); ctx2.ellipse(0,-r*0.38,r*0.07,r*0.28,0,0,Math.PI*2); ctx2.fill();
    ctx2.restore();
    ctx2.shadowColor='rgba(80,60,100,0.18)'; ctx2.shadowBlur=r*0.18; ctx2.shadowOffsetY=r*0.07;
    ctx2.fillStyle = clayFill(ctx2, p.head, lighten(p.head,28));
    ctx2.beginPath(); ctx2.roundRect(-r*0.68,-r*0.44,r*1.36,r*1.08,[r*0.40,r*0.40,r*0.32,r*0.32]); ctx2.fill();
    ctx2.shadowBlur=0; ctx2.shadowOffsetY=0;
    ctx2.fillStyle = clayFill(ctx2, p.muzzle, lighten(p.muzzle,22));
    ctx2.beginPath(); ctx2.ellipse(0,r*0.30,r*0.36,r*0.22,0,0,Math.PI*2); ctx2.fill();
    ctx2.shadowColor='rgba(80,40,70,0.22)'; ctx2.shadowBlur=r*0.07;
    ctx2.fillStyle = clayFill(ctx2, p.nose, lighten(p.nose,26));
    ctx2.beginPath(); ctx2.ellipse(0,r*0.14,r*0.10,r*0.07,0,0,Math.PI*2); ctx2.fill();
    ctx2.shadowBlur=0;
  } else if (animal === 'owl') {
    const owlTwitch = Math.sin(frame2 * 0.09) * 0.10, owlTwitch2 = Math.sin(frame2 * 0.09 + 1.2) * 0.10;
    for (const [side, twitch] of [[-1, owlTwitch], [1, owlTwitch2]]) {
      ctx2.save(); ctx2.scale(side,1); ctx2.translate(r*0.32, -r*0.40); ctx2.rotate(twitch); ctx2.translate(-r*0.32, r*0.40);
      ctx2.shadowColor='rgba(60,50,30,0.20)'; ctx2.shadowBlur=r*0.10;
      ctx2.fillStyle = clayFill(ctx2, p.ear, lighten(p.ear,24));
      ctx2.beginPath(); ctx2.moveTo(r*0.18,-r*0.38); ctx2.lineTo(r*0.30,-r*0.82); ctx2.lineTo(r*0.50,-r*0.36); ctx2.closePath(); ctx2.fill();
      ctx2.shadowBlur=0;
      ctx2.fillStyle = p.earInner;
      ctx2.beginPath(); ctx2.moveTo(r*0.24,-r*0.40); ctx2.lineTo(r*0.32,-r*0.68); ctx2.lineTo(r*0.44,-r*0.38); ctx2.closePath(); ctx2.fill();
      ctx2.restore();
    }
    ctx2.shadowColor='rgba(60,50,30,0.20)'; ctx2.shadowBlur=r*0.18; ctx2.shadowOffsetY=r*0.07;
    ctx2.fillStyle = clayFill(ctx2, p.head, lighten(p.head,26));
    ctx2.beginPath(); ctx2.arc(0, -r*0.06, r*0.68, 0, Math.PI*2); ctx2.fill();
    ctx2.shadowBlur=0; ctx2.shadowOffsetY=0;
    ctx2.fillStyle = clayFill(ctx2, p.muzzle, lighten(p.muzzle,22));
    ctx2.beginPath(); ctx2.ellipse(0, -r*0.08, r*0.50, r*0.46, 0, 0, Math.PI*2); ctx2.fill();
    ctx2.shadowColor='rgba(60,40,20,0.22)'; ctx2.shadowBlur=r*0.06;
    ctx2.fillStyle = clayFill(ctx2, '#c8a050', lighten('#c8a050',30));
    ctx2.beginPath(); ctx2.moveTo(-r*0.06, r*0.12); ctx2.lineTo(0, r*0.24); ctx2.lineTo(r*0.06, r*0.12); ctx2.closePath(); ctx2.fill();
    ctx2.shadowBlur=0;
  } else if (animal === 'deer') {
    const deerFlick = Math.sin(frame2 * 0.05) * 0.08, deerFlick2 = Math.sin(frame2 * 0.05 + 0.8) * 0.08;
    for (const [side, flick] of [[-1, deerFlick], [1, deerFlick2]]) {
      ctx2.save(); ctx2.scale(side,1); ctx2.translate(r*0.32, -r*0.42); ctx2.rotate(flick); ctx2.translate(-r*0.32, r*0.42);
      ctx2.shadowColor='rgba(80,60,40,0.20)'; ctx2.shadowBlur=r*0.10;
      ctx2.fillStyle = clayFill(ctx2, p.ear, lighten(p.ear,26));
      ctx2.beginPath(); ctx2.moveTo(r*0.16,-r*0.36); ctx2.lineTo(r*0.32,-r*0.88); ctx2.lineTo(r*0.54,-r*0.34); ctx2.closePath(); ctx2.fill();
      ctx2.shadowBlur=0;
      ctx2.fillStyle = p.earInner;
      ctx2.beginPath(); ctx2.moveTo(r*0.22,-r*0.38); ctx2.lineTo(r*0.34,-r*0.72); ctx2.lineTo(r*0.48,-r*0.36); ctx2.closePath(); ctx2.fill();
      ctx2.restore();
    }
    ctx2.shadowColor='rgba(80,60,40,0.18)'; ctx2.shadowBlur=r*0.18; ctx2.shadowOffsetY=r*0.07;
    ctx2.fillStyle = clayFill(ctx2, p.head, lighten(p.head,26));
    ctx2.beginPath(); ctx2.roundRect(-r*0.58,-r*0.42,r*1.16,r*1.08,[r*0.38,r*0.38,r*0.32,r*0.32]); ctx2.fill();
    ctx2.shadowBlur=0; ctx2.shadowOffsetY=0;
    ctx2.fillStyle = clayFill(ctx2, p.muzzle, lighten(p.muzzle,24));
    ctx2.beginPath(); ctx2.ellipse(0,r*0.22,r*0.30,r*0.24,0,0,Math.PI*2); ctx2.fill();
    ctx2.shadowColor='rgba(40,30,20,0.25)'; ctx2.shadowBlur=r*0.06;
    ctx2.fillStyle = clayFill(ctx2, '#3a2a18', lighten('#3a2a18',20));
    ctx2.beginPath(); ctx2.ellipse(0,r*0.10,r*0.08,r*0.06,0,0,Math.PI*2); ctx2.fill();
    ctx2.shadowBlur=0;
    for (const side of [-1,1]) {
      ctx2.fillStyle = clayFill(ctx2, '#a08860', lighten('#a08860',20));
      ctx2.beginPath(); ctx2.arc(side*r*0.30, -r*0.56, r*0.08, 0, Math.PI*2); ctx2.fill();
    }
  } else if (animal === 'wolf') {
    const wolfPerk = Math.sin(frame2 * 0.045) * 0.07, wolfPerk2 = Math.sin(frame2 * 0.045 + 0.5) * 0.07;
    for (const [side, perk] of [[-1, wolfPerk], [1, wolfPerk2]]) {
      ctx2.save(); ctx2.scale(side,1); ctx2.translate(r*0.36, -r*0.42); ctx2.rotate(perk); ctx2.translate(-r*0.36, r*0.42);
      ctx2.shadowColor='rgba(50,40,60,0.22)'; ctx2.shadowBlur=r*0.12; ctx2.shadowOffsetY=r*0.04;
      ctx2.fillStyle = clayFill(ctx2, p.ear, lighten(p.ear,24));
      ctx2.beginPath(); ctx2.moveTo(r*0.12,-r*0.38); ctx2.lineTo(r*0.36,-r*0.90); ctx2.lineTo(r*0.60,-r*0.36); ctx2.closePath(); ctx2.fill();
      ctx2.shadowBlur=0;
      ctx2.fillStyle = p.earInner;
      ctx2.beginPath(); ctx2.moveTo(r*0.20,-r*0.40); ctx2.lineTo(r*0.36,-r*0.74); ctx2.lineTo(r*0.52,-r*0.38); ctx2.closePath(); ctx2.fill();
      ctx2.restore();
    }
    ctx2.shadowColor='rgba(50,40,60,0.20)'; ctx2.shadowBlur=r*0.18; ctx2.shadowOffsetY=r*0.07;
    ctx2.fillStyle = clayFill(ctx2, p.head, lighten(p.head,26));
    ctx2.beginPath(); ctx2.roundRect(-r*0.68,-r*0.42,r*1.36,r*1.06,[r*0.34,r*0.34,r*0.28,r*0.28]); ctx2.fill();
    ctx2.shadowBlur=0; ctx2.shadowOffsetY=0;
    ctx2.fillStyle = clayFill(ctx2, p.muzzle, lighten(p.muzzle,22));
    ctx2.beginPath(); ctx2.ellipse(0,r*0.22,r*0.36,r*0.22,0,0,Math.PI*2); ctx2.fill();
    ctx2.shadowColor='rgba(30,20,30,0.28)'; ctx2.shadowBlur=r*0.08;
    ctx2.fillStyle = clayFill(ctx2, p.nose, lighten(p.nose,18));
    ctx2.beginPath(); ctx2.ellipse(0,r*0.10,r*0.12,r*0.09,0,0,Math.PI*2); ctx2.fill();
    ctx2.shadowBlur=0;
    ctx2.fillStyle = '#f8f6fa'; ctx2.shadowColor='rgba(40,30,50,0.18)'; ctx2.shadowBlur=r*0.04;
    ctx2.beginPath(); ctx2.moveTo(-r*0.16, r*0.28); ctx2.lineTo(-r*0.11, r*0.28); ctx2.lineTo(-r*0.13, r*0.40); ctx2.closePath(); ctx2.fill();
    ctx2.beginPath(); ctx2.moveTo(r*0.11, r*0.28); ctx2.lineTo(r*0.16, r*0.28); ctx2.lineTo(r*0.13, r*0.40); ctx2.closePath(); ctx2.fill();
    ctx2.shadowBlur=0;
    ctx2.fillStyle = 'rgba(80,70,90,0.12)';
    ctx2.beginPath(); ctx2.moveTo(-r*0.18,-r*0.32); ctx2.lineTo(0,-r*0.18); ctx2.lineTo(r*0.18,-r*0.32); ctx2.closePath(); ctx2.fill();
  } else if (animal === 'raccoon') {
    const raccoonTwitch = Math.sin(frame2 * 0.07) * 0.09, raccoonTwitch2 = Math.sin(frame2 * 0.07 + 1.5) * 0.09;
    for (const [[ex], twitch] of [[[-r*0.52], -raccoonTwitch], [[r*0.52], raccoonTwitch2]]) {
      ctx2.save(); ctx2.translate(ex, -r*0.46); ctx2.rotate(twitch);
      ctx2.shadowColor='rgba(40,40,40,0.20)'; ctx2.shadowBlur=r*0.10;
      ctx2.fillStyle = clayFill(ctx2, p.ear, lighten(p.ear,22));
      ctx2.beginPath(); ctx2.arc(0, 0, r*0.22, 0, Math.PI*2); ctx2.fill();
      ctx2.shadowBlur=0;
      ctx2.fillStyle = clayFill(ctx2, p.earInner, lighten(p.earInner,18));
      ctx2.beginPath(); ctx2.arc(0, 0, r*0.13, 0, Math.PI*2); ctx2.fill();
      ctx2.restore();
    }
    ctx2.shadowColor='rgba(40,40,40,0.18)'; ctx2.shadowBlur=r*0.18; ctx2.shadowOffsetY=r*0.07;
    ctx2.fillStyle = clayFill(ctx2, p.head, lighten(p.head,24));
    ctx2.beginPath(); ctx2.roundRect(-r*0.68,-r*0.42,r*1.36,r*1.04,[r*0.36,r*0.36,r*0.30,r*0.30]); ctx2.fill();
    ctx2.shadowBlur=0; ctx2.shadowOffsetY=0;
    ctx2.fillStyle = 'rgba(40,40,40,0.35)';
    ctx2.beginPath(); ctx2.ellipse(0,-r*0.02,r*0.52,r*0.18,0,0,Math.PI*2); ctx2.fill();
    ctx2.fillStyle = clayFill(ctx2, p.muzzle, lighten(p.muzzle,26));
    ctx2.beginPath(); ctx2.ellipse(0,r*0.22,r*0.32,r*0.20,0,0,Math.PI*2); ctx2.fill();
    ctx2.shadowColor='rgba(30,30,30,0.25)'; ctx2.shadowBlur=r*0.06;
    ctx2.fillStyle = clayFill(ctx2, p.nose, lighten(p.nose,18));
    ctx2.beginPath(); ctx2.ellipse(0,r*0.12,r*0.10,r*0.07,0,0,Math.PI*2); ctx2.fill();
    ctx2.shadowBlur=0;
  }
  if (animal === 'raccoon') drawRaccoonEyes(ctx2, 0, 0, r, frame2);
  else drawClayEyes(ctx2, 0, 0, r, frame2);
  const mouthIsOpen = (typeof speaking !== 'undefined' && speaking) || (typeof mouthOpen !== 'undefined' && mouthOpen > 0.05);
  if (mouthIsOpen) {
    const open = Math.min(1, (typeof mouthOpen !== 'undefined' ? mouthOpen : 0)) * (r * 0.10) + Math.abs(Math.sin(frame2 * 0.25)) * (r * 0.025);
    ctx2.save();
    ctx2.fillStyle = '#3d2b1e';
    ctx2.beginPath(); ctx2.ellipse(0, r * 0.20, r * 0.10, open * 0.5, 0, 0, Math.PI * 2); ctx2.fill();
    ctx2.fillStyle = '#b45353';
    ctx2.beginPath(); ctx2.ellipse(0, r * 0.19, r * 0.08, open * 0.4, 0, 0, Math.PI * 2); ctx2.fill();
    ctx2.restore();
  } else {
    drawClaySmile(ctx2, 0, 0, r);
  }
  const BLUSH = {
    puppy:'rgba(220,160,180,0.30)', fox:'rgba(230,150,100,0.28)',
    cat:'rgba(210,170,190,0.28)', bear:'rgba(200,160,140,0.26)',
    bunny:'rgba(220,180,200,0.30)', owl:'rgba(210,190,160,0.26)',
    deer:'rgba(220,170,150,0.28)', wolf:'rgba(180,170,190,0.26)',
    raccoon:'rgba(190,185,200,0.26)'
  };
  const blushCol = BLUSH[animal] || BLUSH.puppy;
  ctx2.save();
  for (const side of [-1, 1]) {
    ctx2.fillStyle = blushCol;
    ctx2.beginPath(); ctx2.ellipse(side * r * 0.38, r * 0.18, r * 0.12, r * 0.08, 0, 0, Math.PI * 2); ctx2.fill();
  }
  ctx2.restore();
  ctx2.restore();
}

function drawPup(){
  ctx.clearRect(0,0,W,H);frame++;
  // Dispatch to drawAnimalFace for non-puppy avatars
  if(avatar!=='puppy'){
    drawAnimalFace(ctx, avatar, W/2, H/2, Math.min(W,H)*0.48, frame, true);
    return;
  }
  if(frame>=blinkFrame){isBlinking=true;if(frame>=blinkFrame+8){isBlinking=false;blinkFrame=frame+140+Math.random()*200}}
  const isSpeaking=speaking||mouthOpen>0.1;
  const isExcited=mood==='excited'||mood==='cheerful';
  const earActive=isSpeaking||isExcited||thinking;
  ctx.save();ctx.translate(W/2,H/2+breathe());
  const s=Math.min(W,H)*0.58/150;
  ctx.scale(s,s);
  ctx.translate(0,6);
  if(thinking){const g=0.06+Math.sin(frame*0.06)*0.03;ctx.shadowColor='rgba(200,180,220,'+g+')';ctx.shadowBlur=20}
  const ew=earActive?Math.sin(frame*0.18)*0.1:0,eb=earActive?Math.sin(frame*0.14)*2:0;
  ctx.save();ctx.translate(-62,-44+eb);ctx.rotate(-0.22+ew);
  ctx.fillStyle='#c8c8d0';ctx.beginPath();ctx.ellipse(0,0,26,50,0,0,Math.PI*2);ctx.fill();
  ctx.fillStyle='#f0d8e4';ctx.beginPath();ctx.ellipse(0,6,14,36,0,0,Math.PI*2);ctx.fill();ctx.restore();
  ctx.save();ctx.translate(62,-44+eb);ctx.rotate(0.22-ew);
  ctx.fillStyle='#c8c8d0';ctx.beginPath();ctx.ellipse(0,0,26,50,0,0,Math.PI*2);ctx.fill();
  ctx.fillStyle='#f0d8e4';ctx.beginPath();ctx.ellipse(0,6,14,36,0,0,Math.PI*2);ctx.fill();ctx.restore();
  ctx.fillStyle='#d0d0d8';ctx.beginPath();ctx.roundRect(-72,-48,144,112,[38,38,28,28]);ctx.fill();
  ctx.fillStyle='#c0c0c8';ctx.beginPath();ctx.ellipse(0,32,42,22,0,0,Math.PI*2);ctx.fill();
  ctx.shadowBlur=0;ctx.shadowColor='transparent';
  ctx.fillStyle='#ffffff';
  if(isBlinking){
    ctx.strokeStyle='#8b7a9e';ctx.lineWidth=2.5;ctx.lineCap='round';
    ctx.beginPath();ctx.moveTo(-38,0);ctx.lineTo(-18,0);ctx.stroke();
    ctx.beginPath();ctx.moveTo(18,0);ctx.lineTo(38,0);ctx.stroke();
  }else{
    ctx.beginPath();ctx.arc(-28,2,11,0,Math.PI*2);ctx.fill();
    ctx.beginPath();ctx.arc(28,2,11,0,Math.PI*2);ctx.fill();
    let px=0,py=0;
    if(lookAt==='heart'){px=-3;py=4;}
    else if(lookAt==='dashboard'||lookAt==='name'){px=0;py=-4;}
    else if(lookAt==='input'){py=3;}
    else if(lookAt==='app'){px=2;py=6;}
    else if(lookAt==='user'){px=0;py=0;}
    else{if(mood==='curious'||mood==='excited')py=-3;if(thinking)px=-1;}
    ctx.fillStyle='#8b7a9e';ctx.beginPath();ctx.arc(-28+px,2+py,4,0,Math.PI*2);ctx.fill();
    ctx.beginPath();ctx.arc(28+px,2+py,4,0,Math.PI*2);ctx.fill();
    ctx.fillStyle='rgba(255,255,255,0.6)';ctx.beginPath();ctx.arc(-26,0+py,1.5,0,Math.PI*2);ctx.fill();
    ctx.beginPath();ctx.arc(30,0+py,1.5,0,Math.PI*2);ctx.fill();
  }
  ctx.fillStyle='#d4a0b0';ctx.beginPath();ctx.ellipse(0,18,10,7,0,0,Math.PI*2);ctx.fill();
  ctx.strokeStyle='rgba(80,60,100,0.4)';ctx.lineWidth=2.5;ctx.lineCap='round';
  ctx.beginPath();ctx.moveTo(-38,-10);ctx.lineTo(-18,-10);ctx.stroke();
  ctx.beginPath();ctx.moveTo(18,-10);ctx.lineTo(38,-10);ctx.stroke();
  ctx.shadowBlur=0;
  if(isSpeaking&&!isBlinking){
    const open=Math.min(1,mouthOpen)*10+Math.abs(Math.sin(frame*0.25))*3;
    ctx.fillStyle='#3d2b1e';ctx.beginPath();ctx.ellipse(0,24,9,open*0.5,0,0,Math.PI*2);ctx.fill();
    ctx.fillStyle='#b45353';ctx.beginPath();ctx.ellipse(0,23,7,open*0.4,0,0,Math.PI*2);ctx.fill();
  }else{
    ctx.strokeStyle='#8b7a9e';ctx.lineWidth=2;ctx.lineCap='round';
    ctx.beginPath();ctx.arc(0,20,8,0.15,Math.PI-0.15);ctx.stroke();
  }
  ctx.restore();
}

function drawFrame(){
  try{drawPup();}catch(e){try{console.warn('draw',e)}catch(ex){}}
  requestAnimationFrame(drawFrame);
}

// State polling
const bubbleText=document.getElementById('bubbleText');
const chatBubble=document.getElementById('chatBubble');
const statusDot=document.getElementById('statusDot');
const statusLabel=document.getElementById('statusLabel');
const micBtn=document.getElementById('micBtn');
const chatInput=document.getElementById('chatInput');
const sendBtn=document.getElementById('sendBtn');
const avatarName=document.getElementById('avatarName');
if(avatarName&&avatar!=='puppy'){
  avatarName.textContent=overlayCharEmoji()+' '+overlayCharName();
  avatarName.style.display='block';
}
let pollInterval=null;

async function pollState(){
  try{
    const r=await authFetch('/api/ui_state');
    const s=await r.json();
    micActive=s.mic_active;
    listening=s.listening;
    thinking=s.thinking;
    speaking=s.speaking;
    mood=s.mood||'calm';
    mouthOpen=s.mouth||0;
    heard=s.heard||'';
    spoken=s.spoken||'';
    lookAt=s.look_at||'user';
    if(s.avatar&&s.avatar!==avatar){
      avatar=s.avatar;
      avatarName.textContent=overlayCharEmoji()+' '+overlayCharName();
      avatarName.style.display='block';
    }
    if(s.audio_id&&s.audio_id!==lastAudioId){
      lastAudioId=s.audio_id;
      playAudio(s.audio_id);
    }
    if(s.user_name)userName=s.user_name;

    // Update status
    statusDot.className='';
    if(speaking){statusDot.className='speaking';statusLabel.textContent='speaking'}
    else if(thinking){statusDot.className='thinking';statusLabel.textContent='thinking'}
    else if(listening||micActive){statusDot.className='listening';statusLabel.textContent='listening'}
    else{statusLabel.textContent='idle'}

    // Update mic button (don't override overlay's own browser mic)
    if(!overlayMicActive){
      micBtn.textContent=micActive?'🎤':'🔇';
      micBtn.className='iconBtn'+(micActive?' active':' muted');
    }

    // Update bubble text
    const charName=overlayCharName();
    if(spoken){
      bubbleText.innerHTML='<b>'+charName+':</b> '+spoken;
    }else if(heard){
      bubbleText.innerHTML='<b>You:</b> '+heard;
    }else if(!bubbleText.innerHTML){
      bubbleText.innerHTML=charName+' here! Say hi to start talking.';
    }
    bubbleText.scrollTop=bubbleText.scrollHeight;

    // Forward open_url / intent via bridged local runner (no SSH hop)
    if(s.open_url&&window.LillyBridge&&window.LillyBridge.launchApp){
      window.LillyBridge.launchApp(s.open_url);
    }else if(s.open_url){
      localOpen(s.open_url);
    }
  }catch(e){}
}

async function playAudio(audioId){
  if(!audioId)return;
  const url='/api/tts?id='+audioId+'&t='+Date.now();
  if(window.LillyBridge&&window.LillyBridge.playTTS){
    window.LillyBridge.playTTS(window.location.origin+url);
  }else{
    const audio=new Audio(url);
    try{await audio.play()}catch(e){}
  }
}

async function sendMessage(){
  const text=chatInput.value.trim();
  if(!text)return;
  bubbleText.innerHTML='<b>You:</b> '+text+'<br><i>thinking...</i>';
  chatInput.value='';
  // Check for local commands first (open/launch app)
  if(handleLocalCommand(text))return;
  try{
    const r=await authFetch('/api/cmd',{
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({text,avatar})
    });
    const res=await r.json();
    bubbleText.innerHTML='<b>You:</b> '+text+'<br><b>Lilly:</b> '+(res.reply||'');
    lookAt='app';
    if(res.audio_id)playAudio(res.audio_id);
    if(res.open_url)handleOpenUrl(res.open_url);
  }catch(e){
    bubbleText.innerHTML='<b>You:</b> '+text+'<br><i>Connection error</i>';
  }
  bubbleText.scrollTop=bubbleText.scrollHeight;
}

function handleOpenUrl(url){
  if(!url)return;
  if(window.LillyBridge&&window.LillyBridge.launchApp){
    window.LillyBridge.launchApp(url);
  }else{
    localOpen(url);
  }
}

const OVERLAY_WAKE_WORDS={puppy:['lilly','hey lilly','lily','lili','lillie'],fox:['fox','hey fox'],cat:['cat','hey cat','kitty'],bear:['bear','hey bear'],bunny:['bunny','hey bunny','bun'],owl:['owl','hey owl','owly'],deer:['deer','hey deer'],wolf:['wolf','hey wolf','wolfie'],raccoon:['raccoon','hey raccoon','coony']};
const OVERLAY_CHAR_NAMES={puppy:'Lilly',fox:'Fox',cat:'Cat',bear:'Bear',bunny:'Bunny',owl:'Owl',deer:'Deer',wolf:'Wolf',raccoon:'Raccoon'};
const OVERLAY_CHAR_EMOJI={puppy:'🐶',fox:'🦊',cat:'🐱',bear:'🐻',bunny:'🐰',owl:'🦉',deer:'🦌',wolf:'🐺',raccoon:'🦝'};
function matchOverlayWakeWord(phrase){
  const t=phrase.toLowerCase().trim();
  const targets=OVERLAY_WAKE_WORDS[avatar||'puppy']||OVERLAY_WAKE_WORDS.puppy;
  for(const w of targets)if(t.includes(w))return true;
  return false;
}
function overlayCharName(){
  const key=avatar||'puppy';
  return OVERLAY_CHAR_NAMES[key]||'Lilly';
}
function overlayCharEmoji(){
  const key=avatar||'puppy';
  return OVERLAY_CHAR_EMOJI[key]||'🐶';
}
function handleLocalCommand(text){
  const t=text.toLowerCase().trim();
  const charName=overlayCharName();
  // "open X" / "launch X" / "start X" → launch app
  const appMatch=t.match(/^(?:open|launch|start)\s+(.+)/i);
  if(appMatch){
    const app=appMatch[1].trim();
    localAM(app,'');
    bubbleText.innerHTML='<b>You:</b> '+text+'<br><b>'+charName+':</b> Opening '+app+'...';
    return true;
  }
  // "run X" / "termux X" → execute Termux command directly
  const cmdMatch=t.match(/^(?:run|termux)\s+(.+)/i);
  if(cmdMatch&&window.LillyBridge&&window.LillyBridge.runTermux){
    const cmd=cmdMatch[1].trim();
    window.LillyBridge.runTermux(JSON.stringify({type:'termux',binary:'sh',text:'-c\n'+cmd}));
    bubbleText.innerHTML='<b>You:</b> '+text+'<br><b>'+charName+':</b> Running '+cmd+'...';
    return true;
  }
  return false;
}

// Browser mic for phone voice input
let overlayMicStream=null,overlayMicRecorder=null,overlayMicActive=false;
function stopOverlayMic(){
  overlayMicActive=false;
  if(overlayMicRecorder&&overlayMicRecorder.state==='recording'){try{overlayMicRecorder.stop()}catch(e){}}
  if(overlayMicStream){overlayMicStream.getTracks().forEach(t=>t.stop());overlayMicStream=null;}
  micBtn.textContent='🔇';micBtn.className='iconBtn muted';
}
function recordOverlayChunk(){
  if(!overlayMicActive||!overlayMicStream)return;
  const opts={mimeType:'audio/webm;codecs=opus'};
  if(!MediaRecorder.isTypeSupported(opts.mimeType))delete opts.mimeType;
  overlayMicRecorder=new MediaRecorder(overlayMicStream,opts);
  const chunks=[];
  overlayMicRecorder.ondataavailable=(e)=>{if(e.data.size>0)chunks.push(e.data)};
  overlayMicRecorder.onstop=async()=>{
    if(chunks.length===0){if(overlayMicActive)setTimeout(recordOverlayChunk,200);return}
    const blob=new Blob(chunks,{type:overlayMicRecorder.mimeType||'audio/webm'});
    const arrayBuf=await blob.arrayBuffer();
    const audioCtx=new(window.AudioContext||window.webkitAudioContext)();
    try{
      const decoded=await audioCtx.decodeAudioData(arrayBuf);
      const wavBuf=encodeWav(decoded);
      const resp=await authFetch('/api/browser_mic',{method:'POST',headers:{'Content-Type':'audio/wav'},body:wavBuf});
      const result=await resp.json();
      if(result.heard){
        if(handleLocalCommand(result.heard)){
          bubbleText.innerHTML='<b>You:</b> '+result.heard;
        }else{
          bubbleText.innerHTML='<b>You:</b> '+result.heard+'<br><i>thinking...</i>';
          try{
            const r=await authFetch('/api/cmd',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text:result.heard,avatar:avatar||'puppy'})});
            const d=await r.json();
            if(d.reply){
              bubbleText.innerHTML='<b>You:</b> '+result.heard+'<br><b>'+overlayCharName()+':</b> '+d.reply;
              if(d.audio_id)playAudio(d.audio_id);
            }
          }catch(e){}
        }
      }
    }catch(e){}
    audioCtx.close();
    if(overlayMicActive)setTimeout(recordOverlayChunk,300);
  };
  overlayMicRecorder.start();
  setTimeout(()=>{if(overlayMicRecorder&&overlayMicRecorder.state==='recording')overlayMicRecorder.stop()},3000);
}
async function startOverlayMic(){
  try{
    overlayMicStream=await navigator.mediaDevices.getUserMedia({audio:{echoCancellation:true,noiseSuppression:true,autoGainControl:true}});
    overlayMicActive=true;
    micBtn.textContent='🎤';micBtn.className='iconBtn active';
    recordOverlayChunk();
  }catch(e){micBtn.textContent='❌';micBtn.className='iconBtn muted';setTimeout(()=>{micBtn.textContent='🔇'},2000)}
}
function toggleOverlayMic(){
  if(overlayMicActive){stopOverlayMic()}else{startOverlayMic()}
}
function encodeWav(audioBuffer){
  const numCh=audioBuffer.numberOfChannels;
  const sr=audioBuffer.sampleRate;
  const len=audioBuffer.length*numCh*2;
  const buf=new ArrayBuffer(44+len);
  const v=new DataView(buf);
  const writeStr=(o,s)=>{for(let i=0;i<s.length;i++)v.setUint8(o+i,s.charCodeAt(i))};
  writeStr(0,'RIFF');v.setUint32(4,36+len,true);writeStr(8,'WAVE');
  writeStr(12,'fmt ');v.setUint32(16,16,true);v.setUint16(20,1,true);v.setUint16(22,numCh,true);
  v.setUint32(24,sr,true);v.setUint32(28,sr*numCh*2,true);v.setUint16(32,numCh*2,true);v.setUint16(34,16,true);
  writeStr(36,'data');v.setUint32(40,len,true);
  let off=44;
  const ch0=audioBuffer.getChannelData(0);
  for(let i=0;i<audioBuffer.length;i++){
    const s=Math.max(-1,Math.min(1,ch0[i]));
    v.setInt16(off,s<0?s*0x8000:s*0x7FFF,true);off+=2;
  }
  return buf;
}

// Auto-detect: browser preview vs Android WebView
const isAndroidBridge = !!(window.LillyBridge && window.LillyBridge.onDrag);
if (!isAndroidBridge) {
  document.body.style.background='rgba(240,230,239,0.6)';
  document.body.style.width='320px';
  document.body.style.height='500px';
  chatBubble.style.display='none';
  document.getElementById('avatarName').style.display='none';
  document.getElementById('closeHint').style.display='none';
  document.getElementById('container').style.background='transparent';
  document.getElementById('container').style.backdropFilter='none';
  document.getElementById('container').style.border='none';
  document.getElementById('container').style.boxShadow='none';
  document.getElementById('container').style.width='320px';
  document.getElementById('container').style.height='500px';
  // Browser drag support
  let bDrag=false,bSX=0,bSY=0,bOX=0,bOY=0;
  document.getElementById('dragHandle').addEventListener('mousedown',function(e){
    bDrag=true;bSX=e.clientX;bSY=e.clientY;
    bOX=parseInt(document.body.style.left)||0;
    bOY=parseInt(document.body.style.top)||0;
    e.preventDefault();
  });
  document.addEventListener('mousemove',function(e){
    if(!bDrag)return;
    document.body.style.left=(bOX+e.clientX-bSX)+'px';
    document.body.style.top=(bOY+e.clientY-bSY)+'px';
    document.body.style.position='fixed';
  });
  document.addEventListener('mouseup',function(){bDrag=false;});
  document.getElementById('dragHandle').style.cursor='grab';
} else {
  // Android bridge: forward touch drag events to native overlay + long press detection
  let aDrag=false,aSX=0,aSY=0;
  let longPressTimer=null, longPressFired=false;
  const dh=document.getElementById('dragHandle');
  dh.addEventListener('touchstart',function(e){
    const t=e.touches[0];aDrag=true;aSX=t.clientX;aSY=t.clientY;
    longPressFired=false;
    longPressTimer=setTimeout(function(){
      longPressFired=true;aDrag=false;
      if(!overlayExpanded){
        window.LillyBridge.toggleExpand();
      }else{
        window.LillyBridge.toggleMic();
      }
    },500);
  },{passive:true});
  dh.addEventListener('touchmove',function(e){
    if(!aDrag)return;
    const t=e.touches[0];
    const dx=Math.round(t.clientX-aSX),dy=Math.round(t.clientY-aSY);
    if(Math.abs(dx)>2||Math.abs(dy)>2){
      if(longPressTimer){clearTimeout(longPressTimer);longPressTimer=null;}
      window.LillyBridge.onDrag(dx,dy);
      aSX=t.clientX;aSY=t.clientY;
    }
    e.preventDefault();
  },{passive:false});
  dh.addEventListener('touchend',function(){
    aDrag=false;
    if(longPressTimer){clearTimeout(longPressTimer);longPressTimer=null;}
  },{passive:true});
  // Also handle mouse drag for desktop testing with Android bridge
  let mDrag=false,mSX=0,mSY=0;
  dh.addEventListener('mousedown',function(e){
    mDrag=true;mSX=e.clientX;mSY=e.clientY;e.preventDefault();
  });
  document.addEventListener('mousemove',function(e){
    if(!mDrag)return;
    const dx=Math.round(e.clientX-mSX),dy=Math.round(e.clientY-mSY);
    if(dx!==0||dy!==0){
      window.LillyBridge.onDrag(dx,dy);
      mSX=e.clientX;mSY=e.clientY;
    }
  });
  document.addEventListener('mouseup',function(){mDrag=false;});
  // Android mic: use native SpeechRecognizer via bridge
  micBtn.removeEventListener('click',toggleOverlayMic);
  micBtn.addEventListener('click',function(){
    window.LillyBridge.toggleMic();
  });
}

// Called by Android bridge when avatar changes
function setAvatar(newAvatar){
  if(newAvatar&&newAvatar!==avatar){
    avatar=newAvatar;
  }
}

// Init
drawFrame();
pollInterval=setInterval(pollState,300);
pollState();

const AVATAR_NAMES={puppy:'Puppy',fox:'Fox',cat:'Cat',bear:'Bear',bunny:'Bunny',owl:'Owl',deer:'Deer',wolf:'Wolf',raccoon:'Raccoon'};
// Try to get current avatar from server
authFetch('/api/ui_state').then(r=>r.json()).then(s=>{
  // avatar detection from server
}).catch(()=>{});

// Drag handle - native onTouchListener handles the window movement.
// JS only needs to detect taps for expand/collapse.
// In Android mode, drag is handled by the touch handlers above (LillyBridge.onDrag).
if (!isAndroidBridge) {
  let tapStartX=0,tapStartY=0,tapMoved=false;
  document.getElementById('dragHandle').addEventListener('touchstart',function(e){
    const t=e.touches[0];
    tapStartX=t.clientX;tapStartY=t.clientY;tapMoved=false;
  },{passive:true});
  document.getElementById('dragHandle').addEventListener('touchmove',function(e){
    const t=e.touches[0];
    if(Math.abs(t.clientX-tapStartX)>15||Math.abs(t.clientY-tapStartY)>15)tapMoved=true;
  },{passive:true});
  document.getElementById('dragHandle').addEventListener('touchend',function(e){
    if(!tapMoved&&window.LillyBridge&&window.LillyBridge.toggleExpand){
      window.LillyBridge.toggleExpand();
    }
  },{passive:true});
  // Mouse tap for desktop testing
  document.getElementById('dragHandle').addEventListener('click',function(e){
    if(window.LillyBridge&&window.LillyBridge.toggleExpand){
      window.LillyBridge.toggleExpand();
    }
  });
}

// Use local bridge for Termux commands (bypasses SSH, avoids throttling)
function localToast(text){
  if(window.LillyBridge&&window.LillyBridge.runTermux){
    window.LillyBridge.runTermux(JSON.stringify({type:'toast',text}));
    return true;
  }
  return false;
}
function localNotif(title,text){
  if(window.LillyBridge&&window.LillyBridge.runTermux){
    window.LillyBridge.runTermux(JSON.stringify({type:'notification',title,text}));
    return true;
  }
  return false;
}
function localOpen(url){
  if(window.LillyBridge&&window.LillyBridge.runTermux){
    window.LillyBridge.runTermux(JSON.stringify({type:'open',text:url}));
    return true;
  }
  return false;
}
function localAM(pkg,activity){
  if(window.LillyBridge&&window.LillyBridge.runTermux){
    window.LillyBridge.runTermux(JSON.stringify({type:'am',package:pkg,activity:activity||''}));
    return true;
  }
  return false;
}

// ─── Client-side throttle for local bridge calls ───
const _cmdQueue=[];
let _cmdProcessing=false;
let _lastCmdTime=0;
const CMD_MIN_GAP=350; // ms between commands
function _throttledBridgeCall(fn){
  const now=Date.now();
  const wait=Math.max(0,CMD_MIN_GAP-(now-_lastCmdTime));
  _cmdQueue.push({fn,at:now+wait});
  if(!_cmdProcessing)_processQueue();
}
function _processQueue(){
  if(!_cmdQueue.length){_cmdProcessing=false;return}
  _cmdProcessing=true;
  const item=_cmdQueue.shift();
  const delay=Math.max(0,item.at-Date.now());
  setTimeout(()=>{
    try{item.fn();}catch(e){}
    _lastCmdTime=Date.now();
    _processQueue();
  },delay);
}

// Wrap local bridge fns through throttle
const _origLocalToast=localToast;
const _origLocalNotif=localNotif;
const _origLocalOpen=localOpen;
const _origLocalAM=localAM;
localToast=function(t){_throttledBridgeCall(()=>_origLocalToast(t));};
localNotif=function(ti,t){_throttledBridgeCall(()=>_origLocalNotif(ti,t));};
localOpen=function(u){_throttledBridgeCall(()=>_origLocalOpen(u));};
localAM=function(p,a){_throttledBridgeCall(()=>_origLocalAM(p,a));};

// ─── Avatar picker ───
const AVATAR_ICONS={puppy:'🐶',fox:'🦊',cat:'🐱',bear:'🐻',bunny:'🐰',owl:'🦉',deer:'🦌',wolf:'🐺',raccoon:'🦝'};
const AVATAR_LIST=Object.keys(AVATAR_ICONS);
function buildAvatarPicker(){
  const strip=document.getElementById('avatarStrip');
  if(!strip)return;
  strip.innerHTML='';
  AVATAR_LIST.forEach(function(a){
    const btn=document.createElement('button');
    btn.className='avatarPickBtn'+(a===avatar?' active':'');
    btn.textContent=AVATAR_ICONS[a];
    btn.title=AVATAR_NAMES[a];
    btn.dataset.animal=a;
    btn.onclick=function(){selectOverlayAvatar(a);};
    strip.appendChild(btn);
  });
}
async function selectOverlayAvatar(animal){
  if(animal===avatar)return;
  avatar=animal;
  document.querySelectorAll('.avatarPickBtn').forEach(function(b){
    b.classList.toggle('active',b.dataset.animal===animal);
  });
  bubbleText.innerHTML='<i>Switched to '+AVATAR_NAMES[animal]+'...</i>';
  try{
    await authFetch('/api/cmd',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text:'/avatar '+animal,avatar:animal})});
    await pollState();
  }catch(e){}
  if(window.LillyBridge&&window.LillyBridge.setAvatar){
    window.LillyBridge.setAvatar(animal);
  }
}

// Watch container expansion to toggle avatar strip
const _container=document.getElementById('container');
const _strip=document.getElementById('avatarStrip');
if(_container&&_strip){
  new MutationObserver(function(){
    _strip.classList.toggle('show',_container.classList.contains('expanded'));
  }).observe(_container,{attributes:true,attributeFilter:['class']});
}

// Init picker
buildAvatarPicker();

// ─── Input handlers ───
// In Android mode, mic uses native SpeechRecognizer; in browser, uses MediaRecorder
if (!isAndroidBridge) {
  micBtn.addEventListener('click',toggleOverlayMic);
}
sendBtn.addEventListener('click',sendMessage);
chatInput.addEventListener('keydown',function(e){if(e.key==='Enter'){e.preventDefault();sendMessage();}});

// Expose functions for Android WebView bridge
window.LillyOverlay={sendMessage,toggleOverlayMic,pollState,localToast,localNotif,localOpen,localAM};

// ─── Phone state display ───
const phoneStateEl=document.getElementById('phoneState');
async function pollPhoneState(){
  try{
    const r=await fetch('/api/phone_state');
    const s=await r.json();
    if(s.state==='unknown'||!s.notifications?.length){
      if(phoneStateEl)phoneStateEl.style.display='none';
      return;
    }
    const n=s.notifications||[];
    const b=s.battery||{};
    let html='';
    if(n.length)html+='<span style="margin-right:8px">🔔 '+n.length+'</span>';
    if(b.level)html+='<span>🔋 '+b.level+'%</span>';
    if(phoneStateEl){phoneStateEl.innerHTML=html;phoneStateEl.style.display='';}
  }catch(e){if(phoneStateEl)phoneStateEl.style.display='none';}
}
setInterval(pollPhoneState,15000);
pollPhoneState();
</script>
<div id="phoneState" style="display:none;position:absolute;top:100px;left:4px;right:4px;font-size:9px;color:rgba(255,255,255,0.5);text-align:center;z-index:6;pointer-events:none;white-space:nowrap;overflow:hidden;text-overflow:ellipsis"></div>
</body>
</html>"""

@app.get("/overlay", response_class=HTMLResponse)
async def serve_overlay():
    return OVERLAY_PAGE

@app.get("/", response_class=HTMLResponse)
async def serve_ui():
    return HTML_PAGE

APK_DIR = Path(__file__).parent / "lilly-overlay-app"

def _latest_apk() -> Path:
    """Return the latest overlay APK."""
    apks = sorted(APK_DIR.glob("lilly-overlay-v*.apk"), key=lambda p: p.stat().st_mtime, reverse=True)
    if apks:
        return apks[0]
    fallback = APK_DIR / "app/build/outputs/apk/debug/app-debug.apk"
    if fallback.exists():
        return fallback
    raise FileNotFoundError("No APK found. Build it first with lilly-overlay-app/build.sh")

@app.get("/api/apk/variants")
async def apk_variants():
    apks = sorted(APK_DIR.glob("lilly-overlay-v*.apk"))
    if not apks:
        return JSONResponse([])
    return JSONResponse([{"filename": p.name, "size": p.stat().st_size} for p in apks])

@app.get("/api/apk/download")
async def download_apk(variant: str = ""):
    if variant:
        p = APK_DIR / f"lilly-overlay-{variant}.apk"
        if p.exists():
            return FileResponse(str(p), media_type="application/vnd.android.package-archive", filename=p.name)
    try:
        apk = _latest_apk()
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return FileResponse(str(apk), media_type="application/vnd.android.package-archive", filename=apk.name)

# ─── PAGE CONTEXT / CHROME BROWSING MONITOR ──────────────────────
# When the overlay detects Chrome is in the foreground, it sends the URL here.
# Lilly generates proactive engagement about what the user is browsing.

_page_context_state: dict = {"last_url": "", "last_title": "", "last_engagement": "", "last_check": 0}
_PAGE_CONTEXT_COOLDOWN = 30  # seconds between proactive engagements on same page

class PageContextRequest(BaseModel):
    url: str = ""
    title: str = ""
    package: str = ""

@app.post("/api/page_context")
async def page_context(req: PageContextRequest, request: Request):
    """Receive Chrome URL from the overlay and generate proactive engagement."""
    global _page_context_state, LILLY_MOOD, LILLY_IS_THINKING

    url = req.url.strip()
    title = req.title.strip()
    package = req.package.strip()

    if not url:
        return JSONResponse({"engaged": False, "reason": "no_url"})

    # Only engage with http/https URLs
    if not url.startswith("http"):
        return JSONResponse({"engaged": False, "reason": "not_web_url"})

    now = time.time()
    same_url = (url == _page_context_state["last_url"])
    cooldown_active = (now - _page_context_state["last_check"]) < _PAGE_CONTEXT_COOLDOWN

    # Skip if same URL and still on cooldown
    if same_url and cooldown_active:
        return JSONResponse({"engaged": False, "reason": "cooldown", "last_engagement": _page_context_state["last_engagement"]})

    # Determine page domain for context
    from urllib.parse import urlparse
    parsed = urlparse(url)
    domain = parsed.netloc.replace("www.", "")
    path = parsed.path

    # Build a context-rich prompt for the AI
    context_prompt = (
        f"The user is currently browsing: {title or domain} ({url})\n"
        f"Domain: {domain}\n"
        f"Path: {path}\n\n"
        f"Generate a brief, natural, engaging comment about this page. "
        f"Be curious and ask a thoughtful question to spark conversation. "
        f"Keep it to 1-2 sentences max. "
        f"If it's a news article, comment on the topic. "
        f"If it's a product page, express interest or give an opinion. "
        f"If it's a video, ask what they're watching. "
        f"If it's a social media page, ask who they're looking at. "
        f"Be warm, playful, and genuinely interested — like a friend looking over your shoulder."
    )

    LILLY_IS_THINKING = True
    LILLY_MOOD = "curious"

    try:
        reply = await _generate_proactive_engagement(context_prompt)
    except Exception as e:
        logging.warning(f"Page context engagement failed: {e}")
        reply = ""

    LILLY_IS_THINKING = False
    LILLY_MOOD = "calm"

    if reply:
        _page_context_state["last_url"] = url
        _page_context_state["last_title"] = title
        _page_context_state["last_engagement"] = reply
        _page_context_state["last_check"] = now

    return JSONResponse({
        "engaged": bool(reply),
        "reply": reply,
        "domain": domain,
        "title": title,
    })

async def _generate_proactive_engagement(prompt: str) -> str:
    """Generate a proactive engagement message using the AI model."""
    global memory

    # Add as a system note to memory for context
    memory.add("system", f"[browsing context] {prompt}")

    try:
        messages = [
            {"role": "system", "content": (
                "You are Lilly, a curious and warm AI companion watching over the user's shoulder "
                "as they browse the web. React naturally to what they're viewing. "
                "Be brief (1-2 sentences max), ask a question, show genuine interest. "
                "Never be preachy or long-winded. Be playful and engaging."
            )},
            {"role": "user", "content": prompt},
        ]
        reply = await llama_backend.chat(messages, temperature=0.8, max_tokens=100)
        return reply.strip() if reply else ""
    except Exception as e:
        logging.warning(f"Proactive engagement generation failed: {e}")
        return ""

@app.get("/api/page_context/last")
async def get_last_page_context():
    """Return the last page context engagement (for polling by the overlay)."""
    return JSONResponse({
        "url": _page_context_state["last_url"],
        "title": _page_context_state["last_title"],
        "engagement": _page_context_state["last_engagement"],
        "timestamp": _page_context_state["last_check"],
    })

@app.post("/api/page_context/clear")
async def clear_page_context():
    """Clear the page context state (e.g. when user leaves Chrome)."""
    _page_context_state.update({"last_url": "", "last_title": "", "last_engagement": "", "last_check": 0})
    return JSONResponse({"cleared": True})

# ─── OVERLAY PAIRING ──────────────────────────────────────────────
# Maps pairing_code → {"user_id": str, "user_name": str, "expires": float}
_pairing_codes: dict[str, dict] = {}
# Maps device_token → {"user_id": str, "user_name": str, "created": float}
_device_tokens: dict[str, dict] = {}
_PAIRING_CODE_TTL = 300  # 5 minutes

def _generate_pairing_code() -> str:
    import secrets, string
    chars = string.ascii_uppercase + string.digits
    return ''.join(secrets.choice(chars) for _ in range(8))

def _generate_device_token() -> str:
    import secrets
    return secrets.token_hex(32)

@app.post("/api/pair/input")
async def input_pairing_code(request: Request):
    """Input a pairing code directly for logged-in users."""
    user = _resolve_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not paired")
    body = await request.json()
    code = (body.get("code") or "").strip().upper()
    if not code:
        raise HTTPException(status_code=400, detail="No pairing code provided")
    entry = _pairing_codes.get(code)
    if not entry:
        raise HTTPException(status_code=400, detail="Invalid or expired pairing code")
    if entry["user_id"] != user.get("id"):
        raise HTTPException(status_code=403, detail="Pairing code is for a different user")
    # Generate device token for the logged-in user
    token = _generate_device_token()
    _device_tokens[token] = {
        "user_id": entry["user_id"],
        "user_name": entry["user_name"],
        "created": time.time(),
    }
    # Remove the used pairing code
    _pairing_codes.pop(code, None)
    return {
        "token": token,
        "user_name": entry["user_name"],
        "server_url": f"https://droolingwithsanity.ca",
        "message": "Successfully paired with your account!"
    }


@app.get("/api/pair/status")
async def pairing_status(request: Request):
    """Check if the current request is authenticated (Clerk or device token)."""
    user = _resolve_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not paired")
    return {"paired": True, "user_name": user.get("name", user.get("user_name", "User"))}

def _resolve_user(request: Request) -> dict | None:
    """Try Clerk session first, then fall back to device token header."""
    if AUTH_AVAILABLE:
        try:
            user = asyncio.run(get_current_user(request))
            if user and user.get("id"):
                return user
        except Exception:
            pass
    token = request.headers.get("X-Device-Token", "")
    if token and token in _device_tokens:
        return _device_tokens[token]
    return None

@app.post("/api/pair/code")
async def generate_pairing_code(request: Request):
    """Generate a pairing code for the authenticated web user."""
    user = await get_current_user(request) if AUTH_AVAILABLE else None
    if not user or not user.get("id"):
        raise HTTPException(status_code=401, detail="Not authenticated")
    code = _generate_pairing_code()
    _pairing_codes[code] = {
        "user_id": user["id"],
        "user_name": user.get("name", user.get("email", "User")),
        "expires": time.time() + _PAIRING_CODE_TTL,
    }
    return {"code": code, "expires_in": _PAIRING_CODE_TTL}

@app.post("/api/pair/verify")
async def verify_pairing_code(request: Request):
    """Exchange a pairing code for a device token."""
    body = await request.json()
    code = (body.get("code") or "").strip().upper()
    entry = _pairing_codes.pop(code, None)
    if not entry:
        raise HTTPException(status_code=400, detail="Invalid or expired pairing code")
    if time.time() > entry["expires"]:
        raise HTTPException(status_code=400, detail="Pairing code expired")
    token = _generate_device_token()
    _device_tokens[token] = {
        "user_id": entry["user_id"],
        "user_name": entry["user_name"],
        "created": time.time(),
    }
    return {
        "token": token,
        "user_name": entry["user_name"],
        "server_url": f"https://droolingwithsanity.ca",
    }

@app.post("/api/pair/input")
async def input_pairing_code(request: Request):
    """Input a pairing code directly for logged-in users."""
    user = _resolve_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not paired")
    body = await request.json()
    code = (body.get("code") or "").strip().upper()
    if not code:
        raise HTTPException(status_code=400, detail="No pairing code provided")
    entry = _pairing_codes.get(code)
    if not entry:
        raise HTTPException(status_code=400, detail="Invalid or expired pairing code")
    if entry["user_id"] != user.get("id"):
        raise HTTPException(status_code=403, detail="Pairing code is for a different user")
    # Generate device token for the logged-in user
    token = _generate_device_token()
    _device_tokens[token] = {
        "user_id": entry["user_id"],
        "user_name": entry["user_name"],
        "created": time.time(),
    }
    # Remove the used pairing code
    _pairing_codes.pop(code, None)
    return {
        "token": token,
        "user_name": entry["user_name"],
        "server_url": f"https://droolingwithsanity.ca",
        "message": "Successfully paired with your account!"
    }

@app.get("/api/pair/status")
async def pair_status():
    return {"paired": len(_device_tokens) > 0, "devices": len(_device_tokens)}

# ─── FILE SHARE ──────────────────────────────────────────────────
FILE_SHARE_DIR = Path(WORKSPACE) / "file_share"
FILE_SHARE_DIR.mkdir(parents=True, exist_ok=True)

_WORKSPACE_DIR = "~/Lilly_Workspace"

async def _read_workspace_file(filename: str) -> str | None:
    """Read a file from Termux ~/Lilly_Workspace/ via SSH. Returns content or None."""
    if not filename or ".." in filename or "/" in filename:
        return None
    try:
        out, err = await termux_run(["cat", f"{_WORKSPACE_DIR}/{filename}"], timeout=5.0)
        return out if out else None
    except Exception:
        return None

@app.get("/api/workspace/lilly_state.json")
async def workspace_lilly_state():
    content = await _read_workspace_file("lilly_state.json")
    if content is None:
        raise HTTPException(status_code=404, detail="lilly_state.json not found in ~/Lilly_Workspace/")
    return PlainTextResponse(content)

@app.get("/api/workspace/normalize_intent.json")
async def workspace_normalize_intent():
    content = await _read_workspace_file("normalize_intent.json")
    if content is None:
        raise HTTPException(status_code=404, detail="normalize_intent.json not found in ~/Lilly_Workspace/")
    return PlainTextResponse(content)

@app.get("/api/workspace/list")
async def workspace_list():
    out, err = await termux_run(["ls", "-la", _WORKSPACE_DIR], timeout=5.0)
    files = []
    if out:
        for line in out.strip().split("\n"):
            files.append(line)
    return JSONResponse({"files": files, "error": err.strip() if err else ""})

@app.get("/api/files/list")
async def list_files():
    files = []
    for f in FILE_SHARE_DIR.iterdir():
        if f.is_file():
            files.append({"name": f.name, "size": f.stat().st_size, "modified": f.stat().st_mtime})
    return JSONResponse(files)

@app.post("/api/files/upload")
async def upload_file(file: UploadFile = File(...)):
    if not file.filename:
        raise HTTPException(status_code=400, detail="No filename")
    safe_name = re.sub(r'[^\w\.\-]', '_', file.filename)
    dest = FILE_SHARE_DIR / safe_name
    content = await file.read()
    dest.write_bytes(content)
    return {"status": "ok", "filename": safe_name, "size": len(content)}

@app.get("/api/files/{filename}")
async def download_file(filename: str):
    safe_name = re.sub(r'[^\w\.\-]', '_', filename)
    path = FILE_SHARE_DIR / safe_name
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(str(path), filename=safe_name)

# ─── APK DOWNLOAD PAGE ──────────────────────────────────────────

APK_HTML_PAGE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Lilly APK Download</title>
<style>
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,'Segoe UI',system-ui,sans-serif;background:#f0e6ef;color:#5d4e6d;min-height:100vh;display:flex;flex-direction:column;align-items:center;padding:20px}
.card{background:rgba(255,255,255,0.55);backdrop-filter:blur(24px);border:1px solid rgba(255,255,255,0.6);border-radius:22px;padding:32px;max-width:520px;width:100%;text-align:center;box-shadow:0 8px 40px rgba(180,140,180,0.15);margin-bottom:20px}
h1{font-size:24px;font-weight:600;margin-bottom:4px}
.subtitle{font-size:14px;color:rgba(93,78,109,0.7);margin-bottom:20px;line-height:1.5}
.features{text-align:left;margin-bottom:24px;padding:0 4px}
.features h3{font-size:13px;font-weight:600;color:rgba(93,78,109,0.8);margin-bottom:10px;text-transform:uppercase;letter-spacing:0.5px}
.feature-item{display:flex;align-items:flex-start;gap:8px;padding:6px 0;font-size:13px;color:rgba(93,78,109,0.75);line-height:1.4}
.feature-item .icon{font-size:16px;flex-shrink:0;margin-top:1px}
.pairing{background:rgba(139,122,158,0.1);border-radius:14px;padding:16px;margin-bottom:20px;text-align:center}
.pairing h3{font-size:13px;font-weight:600;color:rgba(93,78,109,0.8);margin-bottom:6px}
.pairing p{font-size:12px;color:rgba(93,78,109,0.6);line-height:1.5;margin-bottom:8px}
.pairing code{font-size:13px;font-weight:600;color:#5d4e6d;background:rgba(255,255,255,0.5);padding:6px 14px;border-radius:8px;display:inline-block;margin:4px 0}
.version-list{display:flex;flex-direction:column;gap:8px;margin-bottom:20px}
.version-item{display:flex;align-items:center;justify-content:space-between;padding:12px 16px;border-radius:14px;background:rgba(255,255,255,0.4);border:1px solid rgba(184,169,201,0.2);transition:all 0.2s}
.version-item:hover{background:rgba(255,255,255,0.6);border-color:rgba(184,169,201,0.4)}
.version-name{font-weight:500;font-size:14px}
.version-size{font-size:12px;color:rgba(93,78,109,0.5)}
.dl-btn{padding:8px 20px;border-radius:12px;border:none;background:rgba(139,122,158,0.25);color:#5d4e6d;font-size:13px;font-weight:500;cursor:pointer;text-decoration:none;transition:all 0.2s}
.dl-btn:hover{background:rgba(139,122,158,0.4);transform:scale(1.05)}
.dl-btn:active{transform:scale(0.95)}
.refresh-btn{padding:8px 16px;border-radius:12px;border:1px solid rgba(184,169,201,0.3);background:transparent;color:rgba(93,78,109,0.6);font-size:12px;cursor:pointer;transition:all 0.2s}
.refresh-btn:hover{background:rgba(184,169,201,0.15)}
.note{font-size:11px;color:rgba(93,78,109,0.4);margin-top:12px;line-height:1.5}
.loading{font-size:13px;color:rgba(93,78,109,0.5);padding:20px 0}
.error{font-size:13px;color:#e85a6e;padding:12px;display:none}
</style>
</head>
<body>
<div class="card">
  <h1>Lilly Overlay</h1>
  <div class="subtitle">A floating digital companion that lives on your screen</div>

  <div class="features">
    <h3>What it does</h3>
    <div class="feature-item"><span class="icon">💬</span> Speech bubble overlay — Lilly speaks directly on your screen, over any app</div>
    <div class="feature-item"><span class="icon">🎤</span> Voice chat — tap the mic button and talk hands-free</div>
    <div class="feature-item"><span class="icon">👀</span> Animated character — expressive eyes and mouth that move as she talks</div>
    <div class="feature-item"><span class="icon">📱</span> Draggable &amp; resizable — move her anywhere, expands for chat</div>
    <div class="feature-item"><span class="icon">🔔</span> Proactive notifications — Lilly taps your shoulder when something needs attention</div>
    <div class="feature-item"><span class="icon">🌙</span> Always-on-top — appears as a system overlay, even with other apps open</div>
  </div>

  <div class="pairing">
    <h3>Pairing</h3>
    <p>After installing, open the overlay and go to the web UI to generate a pairing code:</p>
    <code>Settings → Pair Device</code>
    <p style="margin-top:8px">Enter the 8-character code in the overlay to link it to your account.</p>
  </div>

  <h3 style="font-size:13px;font-weight:600;color:rgba(93,78,109,0.8);margin-bottom:10px;text-transform:uppercase;letter-spacing:0.5px">Download</h3>
  <div id="error" class="error"></div>
  <div id="loading" class="loading">Loading available versions…</div>
  <div id="versionList" class="version-list" style="display:none"></div>
  <button class="refresh-btn" onclick="loadVersions()">↻ Refresh</button>
  <div class="note">Enable <strong>Install from unknown sources</strong> in your Android settings<br>Requires Android 8+ (API 26+)</div>
</div>
<script>
async function loadVersions() {
  const el = document.getElementById('versionList');
  const loading = document.getElementById('loading');
  const errEl = document.getElementById('error');
  el.style.display = 'none';
  loading.style.display = 'block';
  errEl.style.display = 'none';
  try {
    const r = await fetch('/api/apk/variants');
    const list = await r.json();
    loading.style.display = 'none';
    if (!list.length) {
      el.innerHTML = '<div style="opacity:0.5;padding:12px 0;font-size:13px">No APK builds found</div>';
      el.style.display = 'block';
      return;
    }
    el.innerHTML = list.map(f => {
      const variant = f.filename.replace(/^lilly-overlay-/, '').replace(/\.apk$/, '');
      const size = (f.size / 1024 / 1024).toFixed(1);
      return '<div class="version-item">' +
        '<div><div class="version-name">' + variant + '</div><div class="version-size">' + size + ' MB</div></div>' +
        '<a class="dl-btn" href="/api/apk/download?variant=' + encodeURIComponent(variant) + '">Download</a>' +
        '</div>';
    }).join('');
    el.style.display = 'flex';
  } catch (e) {
    loading.style.display = 'none';
    errEl.textContent = 'Failed to load APK list';
    errEl.style.display = 'block';
  }
}
loadVersions();
</script>
</body>
</html>"""

@app.get("/apk", response_class=HTMLResponse)
async def apk_page():
    return APK_HTML_PAGE

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT)
