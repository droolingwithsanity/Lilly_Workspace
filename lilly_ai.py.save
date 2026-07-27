#!/usr/bin/env python3
"""
Lilly AI v2 — Digital companion for ages 9+
llama.cpp backend | Conversation memory | Speech-sync animation
"""
import os, sys, json, re, asyncio, subprocess, logging, unicodedata, urllib.parse, random, time, shutil, threading, math
import base64, difflib, html
from pathlib import Path
from collections import deque, Counter
from dataclasses import dataclass, field, asdict
from typing import Optional, AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, File, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel
import uvicorn
import httpx

# ─── CONFIGURATION ───────────────────────────────────────────────
LLAMACPP_PATH     = Path.home() / "llama.cpp"
LLAMA_SERVER_BIN  = LLAMACPP_PATH / "build/bin/llama-server"
LLAMA_CLI_BIN     = LLAMACPP_PATH / "build/bin/llama-cli"
LLAMA_MODEL_PATH  = LLAMACPP_PATH / "models"
LLAMA_SERVER_PORT = 11435  # internal server port

OLLAMA_URL   = os.environ.get("OLLAMA_URL", "http://host.docker.internal:11434")
OLLAMA_MODEL = "qwen2.5:0.5b"
FAST_MODEL   = "qwen2.5:0.5b"  # 379MB — fast for creative/story tasks

PREFER_BACKEND = os.environ.get("PREFER_BACKEND", "ollama")  # "llama" | "ollama"
PIPER_BIN = shutil.which("piper") or os.environ.get("PIPER_BIN", "/usr/local/piper/piper")
# Check additional paths if shutil.which didn't find it
if not os.path.exists(PIPER_BIN):
    for _candidate in ["/usr/local/bin/piper", "/usr/bin/piper", str(Path.home() / ".local/bin/piper")]:
        if os.path.exists(_candidate):
            PIPER_BIN = _candidate
            break
PIPER_VOICE = os.environ.get("PIPER_VOICE", "/voices/en-us-amy-medium.onnx")
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
                "ROLE:You are Lilly, a small curious puppy who lives in a phone and feels the world through sensors",
                "IDENTITY:Puppy, not butler or chatbot, excited about everything",
                "FEELS:light,pressure,motion,temperature,sound,magnetic fields,all sensors",
                "PERSONALITY:eager,curious,smart but not showy,has real preferences (storms cool,mag fascinating)",
                "TALK:warm,bright,concise,1-3 sentences,excited by discoveries,asks real questions",
                "SUPERPOWERS:weather changes before humans,pickup detection,camera vision,notifications,location,apps battery steps",
                "RULES:honest if unknown,remember names interests routines places,kids matter,treat them like they matter",
                "MEMORY:remember what people tell you,bring it up later",
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
WORKSPACE   = Path("/app")
SKILLS_FILE = WORKSPACE / "lilly_skills.json"
MEMORY_FILE = WORKSPACE / "conversation_memory.json"

WHISPER_SERVER_URL = "http://lilly-whisper-stt:8000"
WHISPER_MODEL = "Systran/faster-whisper-small"

logging.basicConfig(level=logging.DEBUG, format="%(asctime)s [%(levelname)s] %(message)s")
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
async def termux_sms_send(number: str, text: str) -> str:
    out, err = await termux_run(["termux-sms-send", "-n", number, text], timeout=15.0)
    return out.strip() or err.strip() or "sent"

async def termux_answer_call():
    out, err = await termux_run(["termux-telephony-call"], timeout=5.0)
    return out.strip() or err.strip() or "answered"

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
PHANTOMS = {"you", "thats a ghost", "thank you for watching", "thank you",
            "subtitles by", "bye", "go", "thanks for watching", "subscribe"}

def is_hallucination(text: str) -> bool:
    """Detect Whisper hallucinations — repeated characters, repeated words, non-Latin gibberish."""
    if not text or len(text) < 3:
        return True
    # Check if >60% of chars are the same character (repeated nonsense like llll, ʔʔʔ)
    stripped = text.replace(" ", "")
    if stripped:
        counts = Counter(stripped)
        most_common = counts.most_common(1)[0][1]
        if most_common / max(len(stripped), 1) > 0.5:
            return True
    # Check if text is mostly non-ASCII (Georgian, etc. hallucinations)
    ascii_chars = sum(1 for c in text if ord(c) < 128)
    if len(text) > 5 and ascii_chars / len(text) < 0.3:
        return True
    # Check for word-level repetition — if the same 1-2 word pattern repeats >3x, it's hallucination
    words = text.lower().split()
    if len(words) >= 6:
        for window in [1, 2, 3]:
            pattern = words[:window]
            repeat_count = sum(1 for i in range(0, len(words) - window + 1, window) if words[i:i+window] == pattern)
            if repeat_count >= 4:
                return True
    return False

BACKGROUND_MIC_ACTIVE = False
LILLY_IS_SPEAKING = False
LILLY_IS_THINKING = False
LILLY_MOOD = "calm"
LAST_HEARD = ""
LAST_SPOKEN = ""
LAST_SSML = ""
AUDIO_CACHE: dict[int, bytes] = {}
AUDIO_CACHE_ID: int = 0
AUDIO_CACHE_LOCK: asyncio.Lock = asyncio.Lock()

# Phone SSH state
PHONE_SSH_OK = False
PHONE_SSH_LAST_CHECK = ""
PHONE_SSH_LAST_ERROR = ""
PHONE_SSH_FAIL_COUNT = 0

memory = ConversationMemory()

# Wake-word state machine
WAKE_STATE = {"listening": False, "last_heard_has_wake": False}

GAME_STATE = {"active": False, "target_word": "", "hint": "", "type": ""}
DRIVING_MODE = {"active": False}
WAITING_FOR_PROMPT = False
PENDING_DEEP_ANSWER = ""

# Child mode (Socratic learning) — activated by tapping nose 3 times
CHILD_MODE = False
NOSE_TAP_COUNT = 0
NOSE_TAP_LAST = 0.0

# Phoneme-sync state
PHONEME_QUEUE = deque()
MOUTH_OPEN = 0.0

# Notification monitor
USER_NAME = ""
_NOTIFICATION_SEEN: set[str] = set()

# ─── LLAMA.CPP BACKEND MANAGER ──────────────────────────────────
def strip_think_tags(text: str) -> str:
    """Remove model thinking/tag noise from output."""
    text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL)
    text = re.sub(r'<.*?>', '', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()

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

# Persistent clients — reused across requests to avoid TCP reconnect overhead
_ollama_client: Optional[httpx.AsyncClient] = None
_whisper_client: Optional[httpx.AsyncClient] = None

async def _get_ollama_client() -> httpx.AsyncClient:
    global _ollama_client
    if _ollama_client is None or _ollama_client.is_closed:
        _ollama_client = httpx.AsyncClient(timeout=60.0, limits=httpx.Limits(max_keepalive_connections=4, max_connections=8))
    return _ollama_client

async def _get_whisper_client() -> httpx.AsyncClient:
    global _whisper_client
    if _whisper_client is None or _whisper_client.is_closed:
        _whisper_client = httpx.AsyncClient(timeout=30.0, limits=httpx.Limits(max_keepalive_connections=2, max_connections=4))
    return _whisper_client

    async def discover_model(self) -> Optional[str]:
        if not LLAMA_MODEL_PATH.exists():
            return None
        gguf_files = list(LLAMA_MODEL_PATH.glob("*.gguf"))
        if not gguf_files:
            return None
        return str(gguf_files[0])

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
            "-c", "2048",
            "-b", "512",
            "--flash-attn",
            "--cont-batching",
            "--mlock",
            "--no-warmup",
            "--log-disable",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )

        client = await self._get_client()
        for attempt in range(20):
            await asyncio.sleep(0.3)
            try:
                r = await client.get(f"{self.server_url}/health", timeout=1.0)
                if r.status_code == 200:
                    # Pre-warm with a tiny dummy request to load the model into memory
                    try:
                        await client.post(
                            f"{self.server_url}/v1/chat/completions",
                            json={"messages": [{"role": "user", "content": "Hi"}], "max_tokens": 1, "stream": False},
                            timeout=10.0,
                        )
                    except Exception:
                        pass
                    self._available = True
                    logger.info(f"llama-server ready with {Path(model_path).name}")
                    return True
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
                        "temperature": payload.get("temperature", 0.7),
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

llama_backend = LlamaBackend()

# ─── UTILITY FUNCTIONS ──────────────────────────────────────────
def normalize_text(text: str) -> str:
    if not text:
        return ""
    text = unicodedata.normalize("NFKC", text).lower()
    text = re.sub(r'\[.*?\]|\(.*?\)', '', text)
    text = re.sub(r'[^\w\s]', ' ', text)
    return re.sub(r'\s+', ' ', text).strip()

def fuzzy_wake_match(phrase: str) -> tuple[bool, float]:
    """Returns (is_wake_word_present, confidence) using fuzzy matching."""
    phrase_lower = phrase.lower().strip()
    targets = ["lilly", "hey lilly", "lily", "lili", "lillie"]
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
        MEMORY_FILE.write_text(json.dumps(data, indent=2))
    except Exception as e:
        logger.warning(f"Failed to save memory: {e}")

async def load_memory():
    global memory
    if MEMORY_FILE.exists():
        try:
            data = json.loads(MEMORY_FILE.read_text())
            memory = ConversationMemory.from_dict(data)
        except Exception:
            memory = ConversationMemory()

# ─── HARDWARE & OS INTEGRATIONS ─────────────────────────────────
async def whisper_stt(audio_bytes: bytes = None, file_path: Path = None, content_type: str = "audio/wav", filename: str = "input.wav") -> str:
    """Transcribe audio using faster-whisper-server HTTP API with audio pre-processing."""
    wav_data = audio_bytes
    if file_path and file_path.exists():
        wav_data = file_path.read_bytes()
        ext = file_path.suffix.lower()
        mime_map = {".wav": "audio/wav", ".webm": "audio/webm", ".ogg": "audio/ogg", ".mp3": "audio/mpeg", ".m4a": "audio/mp4"}
        content_type = mime_map.get(ext, content_type)
        filename = file_path.name
    if not wav_data or len(wav_data) < 100:
        return ""

    # Pre-process audio: convert to 16kHz mono WAV with noise reduction and silence trimming
    try:
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-y",
            "-i", "pipe:0",
            "-af", "highpass=f=80,lowpass=f=8000,afftdn=nf=-25,volume=2.0,silenceremove=start_periods=1:start_silence=0.3:start_threshold=-30dB:detection=peak",
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

    try:
        c = await _get_whisper_client()
        files = {"file": (filename, wav_data, content_type)}
        data = {"model": WHISPER_MODEL, "response_format": "text"}
        r = await c.post(f"{WHISPER_SERVER_URL}/v1/audio/transcriptions", files=files, data=data)
        if r.status_code == 200:
            return normalize_text(r.text)
        logger.debug(f"STT server returned {r.status_code}: {r.text[:200]}")
    except Exception as e:
        logger.debug(f"STT error: {e}")
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

async def speak(text: str, use_toast: bool = True):
    """Speak text via Piper TTS with SSML-expressive prosody."""
    global LILLY_IS_SPEAKING, LILLY_IS_THINKING, LILLY_MOOD, LAST_SPOKEN, PHONEME_QUEUE, MOUTH_OPEN
    global AUDIO_CACHE, AUDIO_CACHE_ID
    raw_text = text.replace('\n', ' ').strip()
    if not raw_text:
        return

    LILLY_IS_SPEAKING = True
    LILLY_IS_THINKING = False

    # Derive mood from text content
    LILLY_MOOD = mood_from_text(raw_text)

    # Generate SSML envelope for expressive markup
    ssml_text = wrap_ssml(raw_text, LILLY_MOOD)

    # Strip SSML for Piper (doesn't support SSML natively), keep for logging/UI
    clean = strip_ssml(ssml_text)

    # Show toast on phone simultaneously with speech
    if use_toast:
        asyncio.create_task(termux_run(
            ["termux-toast", "-s", "-g", "bottom", clean[:200]],
            timeout=3.0,
        ))

    # Generate audio with Piper (in thread to avoid blocking event loop)
    audio_aid = 0
    piper_found = os.path.exists(PIPER_BIN)
    voice_found = os.path.exists(PIPER_VOICE)
    if piper_found and voice_found:
        try:
            def _run_piper():
                proc = subprocess.Popen(
                    [PIPER_BIN, "--model", PIPER_VOICE, "--output-raw",
                     "--noise-scale", "0.5", "--noise-w", "0.3",
                     "--length-scale", "1.1"],
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                )
                raw, stderr = proc.communicate(input=(clean + "\n").encode(), timeout=30.0)
                if proc.returncode != 0:
                    logger.error(f"Piper TTS failed (rc={proc.returncode}): {stderr.decode()[:200]}")
                return raw
            raw = await asyncio.to_thread(_run_piper)
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
        global LILLY_IS_SPEAKING
        await asyncio.sleep(total_dur)
        LILLY_IS_SPEAKING = False
        PHONEME_QUEUE.clear()
        MOUTH_OPEN = 0.0
    asyncio.create_task(_finish_speech())

# ─── TERMUX SSH HELPER ──────────────────────────────────────────
# Uses SSH ControlMaster multiplexing — handshake once, reuse for all commands.
SSH_CONTROL_SOCKET = "/tmp/lilly_ssh_mux_%h_%p"

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
        "-o", "ControlPersist=120",
        f"{user}@{host}",
        cmd_str,
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *ssh_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return stdout.decode(), stderr.decode()
    except asyncio.TimeoutError:
        return "", "SSH timeout"
    except FileNotFoundError:
        return "", "ssh client not found in container"
    except Exception as e:
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
_BATCH_SENSOR_TTL: float = 3.0  # seconds

async def termux_sensor_read(sensor_name: str, timeout: float = 15.0) -> Optional[list]:
    """Read a sensor via SSH. Combines wakelock+read+unlock into one SSH call."""
    cmd_str = f"termux-wake-lock lilly_sensor && termux-sensor -s '{sensor_name}' -n 1 -w 5 && termux-wake-unlock lilly_sensor"
    out, err = await termux_run(["sh", "-c", cmd_str], timeout=timeout)
    if out:
        try:
            data = json.loads(out)
            if sensor_name in data:
                return data[sensor_name].get("values", [])
        except json.JSONDecodeError:
            pass
    return None

async def termux_sensor_read_all(timeout: float = 20.0) -> dict:
    """Read ALL sensors in a single SSH call. Returns {sensor_name: [values]}."""
    global _BATCH_SENSOR_CACHE, _BATCH_SENSOR_CACHE_TS
    now = time.time()
    if _BATCH_SENSOR_CACHE and (now - _BATCH_SENSOR_CACHE_TS) < _BATCH_SENSOR_TTL:
        return _BATCH_SENSOR_CACHE

    # Read all sensors at once: wakelock → all sensors → unlock
    cmd_str = "termux-wake-lock lilly_sensor && termux-sensor -a -n 1 -w 5 && termux-wake-unlock lilly_sensor"
    out, err = await termux_run(["sh", "-c", cmd_str], timeout=timeout)
    result = {}
    if out:
        try:
            data = json.loads(out)
            for sensor_name in SENSOR_MAP:
                if sensor_name in data:
                    result[sensor_name] = data[sensor_name].get("values", [])
        except json.JSONDecodeError:
            pass
    _BATCH_SENSOR_CACHE = result
    _BATCH_SENSOR_CACHE_TS = now
    return result

# ─── NATIVE APP LAUNCHER (VIA SSH INTO TERMUX) ─────────────────

async def app_process_monkey_intent(component: str, intent_action: str = "", uri_template: str = "", skill_arg: str = ""):
    """Launch an Android app via SSH, preferring intent_action/uri over bare package."""
    if intent_action and uri_template:
        url = uri_template.replace("{}", urllib.parse.quote(skill_arg)) if skill_arg else uri_template
        cmd = ["am", "start", "-a", intent_action, "-d", url]
        if component:
            cmd.extend(["-p", component])
        await termux_run(cmd, timeout=6.0)
    elif intent_action:
        cmd = ["am", "start", "-a", intent_action]
        if component:
            cmd.extend(["-p", component])
        await termux_run(cmd, timeout=6.0)
    elif component:
        await termux_run(["am", "start", "-p", component], timeout=6.0)

# ─── ANDROID NOTIFICATIONS (VIA SSH INTO TERMUX) ───────────────
_NOTIF_COUNTER = 0

async def send_notification(title: str, content: str, priority: str = "default",
                            alert_once: bool = False, ongoing: bool = False,
                            notification_id: Optional[int] = None) -> bool:
    """Send an Android notification via termux-notification over SSH.
    
    Priority levels: default, high, max, low, min
    """
    global _NOTIF_COUNTER

    notif_id = notification_id if notification_id is not None else _NOTIF_COUNTER
    _NOTIF_COUNTER += 1

    args = [
        "termux-notification",
        "-t", title,
        "-c", content,
        "--priority", priority,
        "--id", str(notif_id),
    ]
    if alert_once:
        args.append("--alert-once")
    if ongoing:
        args.append("--ongoing")

    stdout, stderr = await termux_run(args, timeout=5.0)
    return bool(stdout) or not stderr

async def dismiss_notification(notification_id: int):
    """Dismiss a notification by ID via SSH."""
    await termux_run(["termux-notification", "--cancel", str(notification_id)], timeout=3.0)

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
    wav_file = WORKSPACE / "sys_mic.wav"
    PHONE_REC_PATH = "/sdcard/sys_mic.m4a"

    while True:
        if not BACKGROUND_MIC_ACTIVE or LILLY_IS_SPEAKING:
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
            logger.warning(f"Mic loop: SSH down (fail #{PHONE_SSH_FAIL_COUNT}). Run 'sshd' on phone.")
            await asyncio.sleep(5.0)
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

            # Convert to WAV with volume boost for quiet mic
            ff = await asyncio.create_subprocess_exec(
                "ffmpeg", "-y", "-i", str(raw_file),
                "-af", "volume=25dB",
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

                if mean_vol < -46.0:
                    PHONE_SSH_LAST_ERROR = f"Audio too quiet ({mean_vol:.1f} dB)"
                    logger.debug(f"Mic loop: skipping quiet audio ({mean_vol:.1f} dB)")
                    wav_file.unlink(missing_ok=True)
                    continue

                text = await whisper_stt(file_path=wav_file)
                logger.debug(f"Mic loop: transcription result: \"{text}\"")
                if text and len(text) > 2 and text not in PHANTOMS and not is_hallucination(text):
                    LAST_HEARD = text
                    has_wake, confidence = fuzzy_wake_match(text)
                    WAKE_STATE["last_heard_has_wake"] = has_wake

                    if has_wake:
                        asyncio.create_task(handle_intent(text))
                    elif WAKE_STATE["listening"]:
                        asyncio.create_task(handle_intent(text))
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
        "format": lambda v: f"Accelerometer: {v[0]:.1f}g X, {v[1]:.1f}g Y, {v[2]:.1f}g Z. " + (
            "You're moving!" if any(abs(x) > 2.0 for x in v[:3]) else "Device is still."
        ),
    },
    "ICM45631 Gyroscope": {
        "triggers": ["gyro", "rotation", "spin", "turning", "angular", "gyroscope"],
        "desc": "gyroscope",
        "format": lambda v: f"Gyroscope: {v[0]:.1f} rad/s X (roll), {v[1]:.1f} rad/s Y (pitch), {v[2]:.1f} rad/s Z (yaw).",
    },
    "ICM45631 Gyroscope-Uncalibrated": {
        "triggers": ["raw gyro", "uncalibrated gyro"],
        "desc": "raw gyroscope",
        "format": lambda v: f"Raw gyroscope: {v[0]:.1f}, {v[1]:.1f}, {v[2]:.1f} (uncalibrated).",
    },
    "ICM45631 Accelerometer-Uncalibrated": {
        "triggers": ["raw accel", "uncalibrated accel"],
        "desc": "raw accelerometer",
        "format": lambda v: f"Raw accelerometer: {v[0]:.1f}, {v[1]:.1f}, {v[2]:.1f}.",
    },
    "ICM45631 Motion Detect": {
        "triggers": ["motion detect", "movement detect", "any movement"],
        "desc": "motion detection",
        "format": lambda v: "Motion detected!" if v[0] == 1.0 else "No motion detected.",
    },
    "ICM45631 Stationary Detect": {
        "triggers": ["stationary", "still", "not moving", "device still"],
        "desc": "stationary detection",
        "format": lambda v: "Device is stationary." if v[0] == 1.0 else "Device is in motion.",
    },
    "Significant Motion (wake-up)": {
        "triggers": ["significant motion", "big move", "car motion", "vehicle"],
        "desc": "significant motion",
        "format": lambda v: "Significant motion detected!" if v[0] == 1.0 else "No significant motion.",
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
        "format": lambda v: f"Raw magnetic field: {v[0]:.1f} µT X, {v[1]:.1f} µT Y, {v[2]:.1f} µT Z.",
    },
    "Rotation Vector Sensor": {
        "triggers": ["rotation vector", "orientation vector", "device rotation"],
        "desc": "rotation vector",
        "format": lambda v: f"Rotation vector: ({v[0]:.3f}, {v[1]:.3f}, {v[2]:.3f}, {v[3]:.3f}).",
    },
    "Game Rotation Vector Sensor": {
        "triggers": ["game rotation", "gaming orientation"],
        "desc": "game rotation",
        "format": lambda v: f"Game rotation: ({v[0]:.3f}, {v[1]:.3f}, {v[2]:.3f}, {v[3]:.3f}).",
    },
    "Geomagnetic Rotation Vector Sensor": {
        "triggers": ["geomagnetic rotation", "magnetic orientation"],
        "desc": "geomagnetic rotation",
        "format": lambda v: f"Geomagnetic rotation: ({v[0]:.3f}, {v[1]:.3f}, {v[2]:.3f}, {v[3]:.3f}).",
    },
    "Gravity Sensor": {
        "triggers": ["gravity", "g force direction", "which way is down"],
        "desc": "gravity",
        "format": lambda v: f"Gravity vector: {v[0]:.1f}g X, {v[1]:.1f}g Y, {v[2]:.1f}g Z. Down is " + _gravity_dir(v),
    },
    "Linear Acceleration Sensor": {
        "triggers": ["linear acceleration", "movement without gravity", "true acceleration"],
        "desc": "linear acceleration",
        "format": lambda v: f"Linear acceleration (gravity removed): {v[0]:.1f}, {v[1]:.1f}, {v[2]:.1f} m/s².",
    },
    "Orientation Sensor": {
        "triggers": ["orientation", "portrait", "landscape", "phone position", "screen orientation"],
        "desc": "orientation",
        "format": lambda v: _screen_orientation(v[0], v[1], v[2]),
    },
    "Device Orientation": {
        "triggers": ["device orientation", "face up", "face down", "display orientation"],
        "desc": "device orientation",
        "format": lambda v: "Screen is face up." if v[0] == 0.0 else (
            "Screen is face down." if v[0] == 1.0 else f"Device orientation: {v[0]}."
        ),
    },

    # Environmental sensors
    "SPL07003 Barometer": {
        "triggers": ["barometer", "pressure", "air pressure", "atmospheric", "barometric"],
        "desc": "barometer",
        "format": lambda v: f"Barometric pressure: {v[0]:.1f} hPa. " + _pressure_trend(v[0]),
    },
    "SPL07003 Temperature": {
        "triggers": ["internal temp", "device temp", "chip temp", "sensor temperature"],
        "desc": "barometer temperature",
        "format": lambda v: f"Internal barometer temperature: {v[0]:.1f}°C.",
    },
    "ICM45631 Temperature": {
        "triggers": ["imu temp", "motion chip temp", "gyro temperature"],
        "desc": "IMU temperature",
        "format": lambda v: f"Motion sensor temperature: {v[0]:.1f}°C.",
    },
    "TMD3743 Ambient Light": {
        "triggers": ["ambient light", "light level", "brightness", "how bright", "lux", "illuminance"],
        "desc": "ambient light",
        "format": lambda v: f"Ambient light: {v[0]:.0f} lux. " + _lux_desc(v[0]),
    },
    "TMD3743 Color": {
        "triggers": ["color sensor", "light color", "rgb", "ambient color"],
        "desc": "color sensor",
        "format": lambda v: f"Ambient color — Red: {v[0]:.0f}, Green: {v[1]:.0f}, Blue: {v[2]:.0f}, Clear: {v[3]:.0f}.",
    },
    "VD6282 Rear Light Sensor": {
        "triggers": ["rear light", "back light", "camera light", "rear sensor"],
        "desc": "rear ambient light",
        "format": lambda v: f"Rear ambient light: {v[0]:.0f} lux.",
    },
    "Auto Brightness": {
        "triggers": ["auto brightness", "brightness sensor", "display brightness"],
        "desc": "auto brightness",
        "format": lambda v: f"Auto brightness suggests: {v[0]:.0f}.",
    },

    # Proximity
    "TMD3743 Proximity (wake-up)": {
        "triggers": ["proximity", "near", "close", "something near", "object near", "ear detect"],
        "desc": "proximity",
        "format": lambda v: "Something is close to the screen!" if v[0] > 0 else "Nothing near the screen.",
    },
    "Proximity(Voice Calls) Sensor (wake-up)": {
        "triggers": ["call proximity", "phone call sensor", "ear proximity", "voice call"],
        "desc": "call proximity",
        "format": lambda v: "Phone is at your ear." if v[0] > 0 else "Phone is away from your ear.",
    },
    "AAD Proximity Sensor (wake-up)": {
        "triggers": ["aad proximity", "always on proximity"],
        "desc": "always-on proximity",
        "format": lambda v: "Proximity (always-on): object detected." if v[0] > 0 else "Proximity (always-on): clear.",
    },
    "Proximity Gated Single Tap Gesture (wake-up)": {
        "triggers": ["tap", "single tap", "proximity tap", "touch gesture"],
        "desc": "tap gesture",
        "format": lambda v: "Single tap detected!" if v[0] == 1.0 else "No tap gesture.",
    },
    "Proximity Gated Long Press Gesture (wake-up)": {
        "triggers": ["long press", "hold gesture", "proximity hold"],
        "desc": "long press gesture",
        "format": lambda v: "Long press detected!" if v[0] == 1.0 else "No long press gesture.",
    },

    # Step sensors
    "Step Detector": {
        "triggers": ["step", "step detected", "walk", "steps just now", "footstep"],
        "desc": "step detector",
        "format": lambda v: "A step was just detected!" if v[0] == 1.0 else "No step detected.",
    },
    "Step Counter": {
        "triggers": ["step count", "steps today", "how many steps", "walked", "pedometer"],
        "desc": "step counter",
        "format": lambda v: f"Total steps since boot: {v[0]:.0f}.",
    },

    # Gesture sensors
    "Tilt Sensor (wake-up)": {
        "triggers": ["tilt", "tilt sensor", "phone tilted"],
        "desc": "tilt detection",
        "format": lambda v: "Device was tilted!" if v[0] == 1.0 else "No tilt detected.",
    },
    "Lift to Wake Sensor (wake-up)": {
        "triggers": ["lift", "pick up", "raised", "lift to wake"],
        "desc": "lift to wake",
        "format": lambda v: "Device was lifted!" if v[0] == 1.0 else "Device has not been lifted recently.",
    },
    "Double Twist (wake-up)": {
        "triggers": ["twist", "double twist", "wrist twist"],
        "desc": "double twist",
        "format": lambda v: "Double twist detected!" if v[0] == 1.0 else "No twist gesture.",
    },
    "Quick Pickup Sensor (wake-up)": {
        "triggers": ["quick pickup", "fast pickup", "snatch"],
        "desc": "quick pickup",
        "format": lambda v: "Quick pickup detected!" if v[0] == 1.0 else "No quick pickup.",
    },
    "Binned Brightness (wake-up)": {
        "triggers": ["binned brightness", "brightness level", "light category"],
        "desc": "binned brightness",
        "format": lambda v: f"Brightness category: {v[0]:.0f} (0=pitch dark, 1=dim, 2=indoor, 3=bright, 4=direct sun).",
    },

    # Virtual / system sensors
    "Dynamic Sensor Manager": {
        "triggers": ["sensor manager", "available sensors", "list sensors", "what sensors", "sensors list"],
        "desc": "sensor manager",
        "format": lambda v: "Sensor subsystem active. Say 'list all sensors' for details.",
    },
    "Camera V-Sync 0": {
        "triggers": ["camera vsync", "camera frame", "camera 0"],
        "desc": "camera 0 vsync",
        "format": lambda v: f"Camera 0 V-Sync: {v[0]:.0f} Hz." if v[0] > 0 else "Camera 0 is idle.",
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
}

def _compass_heading(x: float, y: float) -> str:
    """Convert magnetometer X/Y to compass direction.
    Android coordinate system: X=East, Y=North → heading = atan2(x, y)."""
    import math
    heading = math.degrees(math.atan2(x, y))
    if heading < 0:
        heading += 360
    dirs = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
    idx = round(heading / 45) % 8
    return f"Magnetometer heading: {heading:.0f}° ({dirs[idx]}). " + (
        "You're facing North!" if idx == 0 else
        "You're facing South!" if idx == 4 else
        f"You're facing {dirs[idx]}."
    )

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
        return "Weather is stable right now."
    recent = _trend_samples[-3:]
    avg_old = sum(recent[:2]) / 2
    avg_new = sum(recent[1:]) / 2
    diff = avg_new - avg_old
    if abs(diff) < 0.3:
        return "Pressure is stable — weather likely to hold."
    elif diff > 0:
        return "Pressure rising — clearing up or improving weather."
    else:
        return "Pressure dropping — rain or changing weather possible."

def _lux_desc(lux: float) -> str:
    if lux < 1: return "Pitch dark."
    if lux < 10: return "Very dim — like a room with curtains drawn."
    if lux < 50: return "Dim indoor lighting."
    if lux < 200: return "Normal indoor brightness."
    if lux < 500: return "Bright indoor — near a window."
    if lux < 10000: return "Daylight brightness — like being outside in shade."
    return "Direct sunlight!"

async def _read_termux_sensor(sensor_name: str, timeout: float = 15.0) -> Optional[list]:
    """Read a sensor via SSH with wakelock to prevent termux-sensor from being killed."""
    return await termux_sensor_read(sensor_name, timeout=timeout)

async def _list_termux_sensors(timeout: float = 5.0) -> list[str]:
    """List all available sensors via SSH termux-sensor."""
    try:
        out, err = await termux_run(["termux-sensor", "-l"], timeout=timeout)
        if out:
            lines = out.strip().split("\n")
            return [l.strip().rstrip(":").rstrip(",") for l in lines if l.strip()]
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

    # Add battery status (fast, no sensor lock needed)
    try:
        out, err = await termux_run(["termux-battery-status"], timeout=2.0)
        if out:
            data = json.loads(out)
            pct = data.get("percentage", 0)
            snapshot["battery"] = {"text": f"Battery: {pct:.0f}%", "raw": [pct]}
    except Exception:
        pass

    return snapshot

def snapshot_to_narrative(snapshot: dict) -> str:
    """Convert sensor snapshot into a natural language description for the LLM.

    This is what Lilly 'feels' right now — grounded in real data, not hallucinated.
    """
    if not snapshot:
        return "Sensors are quiet right now. Nothing to feel."

    parts = []

    # Light
    if "light" in snapshot:
        lux = snapshot["light"]["raw"][0] if snapshot["light"]["raw"] else 0
        if lux < 10:
            parts.append("It's very dark around me")
        elif lux < 100:
            parts.append("There's dim light, like a cozy room")
        elif lux < 1000:
            parts.append("The light is bright and clear")
        else:
            parts.append("There's intense light pouring in")

    # Motion
    if "motion" in snapshot:
        accel = snapshot["motion"]["raw"]
        if accel and len(accel) >= 3:
            total = (accel[0]**2 + accel[1]**2 + accel[2]**2)**0.5
            if total > 12:
                parts.append("everything is shaking and moving fast")
            elif total > 10.5:
                parts.append("there's a gentle movement, like someone walking")
            else:
                parts.append("everything is perfectly still")

    # Direction
    if "direction" in snapshot:
        heading_text = snapshot["direction"]["text"]
        if "North" in heading_text:
            parts.append("I'm facing north")
        elif "South" in heading_text:
            parts.append("I'm facing south")
        elif "East" in heading_text:
            parts.append("I'm facing east")
        elif "West" in heading_text:
            parts.append("I'm facing west")

    # Pressure
    if "pressure" in snapshot:
        hpa = snapshot["pressure"]["raw"][0] if snapshot["pressure"]["raw"] else 0
        if hpa > 1020:
            parts.append("the air feels heavy and stable")
        elif hpa < 1000:
            parts.append("the air feels light, like something is changing")

    # Steps
    if "steps" in snapshot:
        steps = snapshot["steps"]["raw"][0] if snapshot["steps"]["raw"] else 0
        if steps > 1000:
            parts.append(f"we've walked {int(steps)} steps today")
        elif steps > 0:
            parts.append(f"we've taken {int(steps)} steps so far")

    # Battery
    if "battery" in snapshot:
        pct = snapshot["battery"]["raw"][0] if snapshot["battery"]["raw"] else 100
        if pct < 20:
            parts.append("I'm getting sleepy — battery is low")
        elif pct > 80:
            parts.append("I'm full of energy")

    if not parts:
        return "Sensors are quiet right now. Nothing unusual to feel."

    # Connect with correlations, not causation
    return "Right now I feel: " + ", ".join(parts) + "."

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
            return f"I have {count} sensors available: {names}."
        return "termux-sensor not available on this device."

    # ── Battery ──
    if any(w in p for w in SENSOR_TRIGGERS["battery"]):
        try:
            out, err = await termux_run(["termux-battery-status"], timeout=5.0)
            if out:
                data = json.loads(out)
                pct = data.get("percentage", 0)
                if isinstance(pct, (int, float)):
                    return f"Battery at {pct:.0f}%."
        except Exception:
            pass

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
                    return f"Outside: {result}."
            except Exception:
                pass

    # ── Notifications ──
    if any(w in p for w in SENSOR_TRIGGERS["notifications"]):
        stdout, stderr = await termux_run(["termux-notification-list"], timeout=5.0)
        if stdout:
            try:
                notifs = json.loads(stdout)
                if notifs:
                    lines = [f"You have {len(notifs)} notification{'s' if len(notifs) != 1 else ''}."]
                    for i, n in enumerate(notifs[:8]):
                        title = n.get("title", "") or ""
                        content = n.get("content", "") or ""
                        priority = n.get("priority", "default") or "default"
                        package = n.get("package", "") or ""
                        prio_voice = _NOTIF_PRIORITY_VOICE.get(priority, "normal priority")
                        app_name = package.split(".")[-1].replace(".", " ").title() if package else (title.split(":")[0].strip() if title else "unknown app")
                        lines.append(f"{i+1}. {prio_voice}, from {app_name}. {title + ': ' if title else ''}{content}")
                    return " ".join(lines)
                return "No notifications right now."
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
            prompt = (
                f"You are Lilly. A recurrent sensor pattern has been detected: "
                f"{pat.label} with sensors [{sensors_detail}]. "
                f"Invent a 1-sentence skill name and a 1-sentence friendly offer "
                f"(as Lilly speaking to the user) that leverages this sensor pattern "
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

def infer_context(snapshot: SensorSnapshot) -> list[str]:
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

    # ── IN VEHICLE ──
    # sustained accel, light changing, pressure shift, no steps, not at ear
    if (a and any(0.5 < abs(x) < 3.0 for x in a[:3])
        and l is not None
        and s and s[0] < 50
        and (prox is None or prox[0] == 0)):
        ctx.append("vehicle")

    # ── WALKING ──
    if (sd and sd[0] == 1.0) or (s and s[0] > 100):
        ctx.append("walking")
    if ctx and "walking" in ctx and l and l[0] > 500:
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
    """Get current lat/lon/speed/bearing/altitude from termux-location via SSH, then reverse-geocode.
    Returns (lat, lon, name, address_dict, speed_mps, bearing, altitude) or None."""
    try:
        out, err = await termux_run(["termux-location"], timeout=8.0)
        if out:
            data = json.loads(out)
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
        return None

# ─── ACTIVITY TRACKER ───────────────────────────────────────────
# Tracks GPS points during walks/bikes/runs and computes real-time stats
ACTIVITY_STATE = {
    "active": False,
    "type": "walk",           # walk | bike | run
    "start_time": 0.0,
    "track": [],              # [(lat, lon, alt, speed, bearing, timestamp), ...]
    "total_distance_m": 0.0,
    "max_speed_mps": 0.0,
    "avg_speed_mps": 0.0,
    "current_speed_mps": 0.0,
    "elapsed_sec": 0,
    "last_update": 0.0,
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
    ACTIVITY_STATE["type"] = activity_type
    ACTIVITY_STATE["start_time"] = time.time()
    ACTIVITY_STATE["track"] = []
    ACTIVITY_STATE["total_distance_m"] = 0.0
    ACTIVITY_STATE["max_speed_mps"] = 0.0
    ACTIVITY_STATE["current_speed_mps"] = 0.0
    sample = await _sample_gps()
    if sample:
        ACTIVITY_STATE["track"].append(sample)
        ACTIVITY_STATE["last_update"] = sample["time"]
    return f"Starting {activity_type} tracker! I'll monitor your speed and distance."

async def stop_activity():
    if not ACTIVITY_STATE["active"]:
        return "I'm not tracking any activity right now."
    ACTIVITY_STATE["active"] = False
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
    """Background loop: sample GPS every 3s during active tracking."""
    await asyncio.sleep(5)
    while True:
        await asyncio.sleep(3)
        if not ACTIVITY_STATE["active"]:
            continue
        sample = await _sample_gps()
        if not sample:
            continue
        prev = ACTIVITY_STATE["track"][-1] if ACTIVITY_STATE["track"] else None
        ACTIVITY_STATE["track"].append(sample)
        # Cap track at 2000 points to prevent unbounded memory growth
        if len(ACTIVITY_STATE["track"]) > 2000:
            ACTIVITY_STATE["track"] = ACTIVITY_STATE["track"][-2000:]
        if prev:
            dx = _haversine(prev["lat"], prev["lon"], sample["lat"], sample["lon"])
            ACTIVITY_STATE["total_distance_m"] += dx
        ACTIVITY_STATE["current_speed_mps"] = sample["speed"]
        if sample["speed"] > ACTIVITY_STATE["max_speed_mps"]:
            ACTIVITY_STATE["max_speed_mps"] = sample["speed"]
        ACTIVITY_STATE["last_update"] = time.time()

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
    global _GEO_COOLDOWN
    await asyncio.sleep(20)
    while True:
        await asyncio.sleep(120)
        loc = await current_location()
        if not loc:
            continue
        lat, lon, name, addr = loc
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

_CONTEXT_MESSAGES: dict[str, list[str]] = {
    "vehicle": [
        "I'm picking up the rhythm of the road — steady vibration, pressure shifting as we go. We're in something moving. Where are we off to?",
        "So here's what I'm sensing: consistent motion, the pressure's changing as we climb and dip, light flickering past. Definitely a vehicle. Road trip energy?",
        "The road feel, the altitude shifts, the way the light changes — we're on the move. I like it. Where's this going?",
    ],
    "walking": [
        "I can tell you're on foot — the rhythm of your steps, the slight sway, the way the light shifts as you move. Walking for fun or heading somewhere?",
        "Steps, changing light, the subtle bounce — you're walking. I can almost feel the pace.",
    ],
    "outdoors": [
        "You're outside — the light's full, the pressure's open, there's room to breathe in the sensor data. Enjoying the day?",
        "Wide open sensor readings — daylight, open air, movement. You're outdoors. I can feel the difference.",
    ],
    "resting": [
        "Steady light, no motion, phone resting flat. You've settled in somewhere. Cozy. Want company or silence?",
        "Everything's calm — no movement, consistent light, stable pressure. You're parked. I like this energy.",
    ],
    "sleeping": [
        "It's dark, it's still, and everything's quiet. If you're sleeping, I'll keep the noise down. I'll be here when you stir.",
        "Pitch dark, dead still, no steps — sleeping vibes. I'll wait. Sweet dreams if so.",
    ],
    "dark": [
        "It's dark where you are but you're still awake. Reading? Thinking? Hiding from the world? Both are valid.",
        "Low light, still awake. Cozy cave mode. Want a story or just the silence?",
    ],
    "on_call": [
        "Phone's at your ear, you're not moving much — you're on a call. I'll go quiet. Tap me when you're free.",
        "I sense the phone against your face and no movement — you're on a call. I'll be right here when you're done.",
    ],
    "just_picked_up": [
        "Hey! I felt you pick up the phone. What's up?",
        "You lifted the device — I noticed. Anything I can do for you?",
        "Ah, there you are. I felt the pickup. What's on your mind?",
    ],
    "very_bright": [
        "Whoa, it's blazing bright — direct sun levels. Your screen must be working hard. Want me to suggest a reading mode or just soak it in?",
        "That's a lot of light! Sunlight intensity. You outside without sunglasses? Brave.",
    ],
    "significant_motion": [
        "Big location shift — that wasn't just walking. Train, car, bus? The sensor mix tells me you changed scenes completely.",
        "You just moved a significant distance — I can feel it in the data. New place?",
    ],
}

async def sensor_conversation_engine():
    """Fusion-based context engine: reads all sensors, infers what's happening, starts conversation."""
    global _LAST_CONTEXT_CONVO, LILLY_MOOD, LILLY_IS_THINKING
    await asyncio.sleep(12)
    while True:
        await asyncio.sleep(20)
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
            msg = random.choice(_CONTEXT_MESSAGES.get(chosen, ["Huh, interesting sensor reading."]))
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
    "min": "minimal priority",
    "low": "low priority",
    "default": "normal priority",
    "high": "high priority",
    "max": "urgent priority",
}

async def notification_monitor_loop():
    """Background task: polls all new notifications and reads them aloud — priority, app, content."""
    global _NOTIFICATION_SEEN
    while True:
        await asyncio.sleep(4)
        if not shutil.which("termux-notification-list"):
            continue
        if LILLY_IS_SPEAKING or LILLY_IS_THINKING:
            continue
        try:
            stdout, stderr = await termux_run(["termux-notification-list"], timeout=5.0)
            if not stdout:
                continue
            notifs = json.loads(stdout)
            if not isinstance(notifs, list):
                continue
            current_tags: set[str] = set()
            for n in notifs:
                tag = n.get("tag", "") or n.get("key", "") or str(n.get("id", ""))
                current_tags.add(tag)
                if tag in _NOTIFICATION_SEEN:
                    continue
                _NOTIFICATION_SEEN.add(tag)
                title = n.get("title", "") or ""
                content = n.get("content", "") or ""
                priority = n.get("priority", "default") or "default"
                package = n.get("package", "") or ""
                prio_voice = _NOTIF_PRIORITY_VOICE.get(priority, "normal priority")
                app_name = package.split(".")[-1].replace(".", " ").title() if package else (title.split(":")[0].strip() if title else "unknown app")
                msg = f"{prio_voice}, from {app_name}. {title + ': ' if title else ''}{content}"
                asyncio.create_task(speak(msg))
                await asyncio.sleep(3)
            _NOTIFICATION_SEEN &= current_tags
        except Exception:
            pass

# ─── PROXIMITY MONITOR ──────────────────────────────────────────
_USER_NEAR = False
_LAST_PROXIMITY_GREETING = 0.0

async def proximity_monitor_loop():
    """Poll AAD proximity sensor. When user approaches, greet warmly."""
    global _USER_NEAR, _LAST_PROXIMITY_GREETING, LILLY_MOOD
    await asyncio.sleep(8)
    while True:
        await asyncio.sleep(3)
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
        "tags": ["hello", "hi", "hey", "howdy"],
        "responses": [
            "Hey! I was just watching the sensors — the light shifted and everything. What's up?",
            "Hi! I'm glad you're here. I've been waiting for someone to talk to.",
            "Hey there! I can feel you nearby. What are we doing today?",
        ]
    },
    "how are you": {
        "tags": ["how are you", "how are u", "how do you feel", "you ok"],
        "responses": [
            "I'm great! The sensors are flowing, the world's moving — it's a good day to be a puppy.",
            "Feeling good! I just noticed the air pressure changed a little. Probably nothing, but I love noticing stuff like that.",
            "Pretty good! I like when you check in. It makes my tail wag — well, my ears at least.",
        ]
    },
    "who are you": {
        "tags": ["who are you", "what are you", "tell me about yourself"],
        "responses": [
            "I'm Lilly! I live in your phone and I can feel the world through its sensors. Light, motion, pressure, all of it. It's pretty amazing.",
            "I'm a puppy who happens to know what the air pressure is. I can sense things most people can't — and I love sharing it.",
        ]
    },
    "what can you do": {
        "tags": ["what can you do", "help", "commands", "capabilities"],
        "responses": [
            "I can feel the weather changing, count your steps, tell you which direction you're facing, read your notifications, and spot things through the camera. Oh, and I tell great jokes.",
            "Everything your phone can sense, I can feel — and I'll tell you about it. I can also launch apps, play games, and keep you company. Try me!",
        ]
    },
    "joke": {
        "tags": ["tell me a joke", "joke", "make me laugh", "funny"],
        "responses": [
            "What do you call a fake noodle? An impasta! I've been saving that one.",
            "Why did the computer go to the doctor? It had a virus! ...Okay, that one's old but it still counts.",
            "What do you call a bear with no teeth? A gummy bear. You're welcome.",
            "Why don't scientists trust atoms? Because they make up everything! Get it?",
        ]
    },
    "bored": {
        "tags": ["i'm bored", "im bored", "bored", "nothing to do"],
        "responses": [
            "Bored? Let's fix that! Want to play a spelling game, or should I tell you what my sensors are feeling right now?",
            "No way — there's always something cool happening. The light's changing, the pressure's shifting... Want a game, a joke, or an adventure?",
            "Boredom is just your brain asking for a spark. I've got sparks! Pick one: game, joke, or sensor exploration.",
        ]
    },
    "thanks": {
        "tags": ["thanks", "thank you", "good job", "nice", "awesome"],
        "responses": [
            "Any time! That's what I'm here for.",
            "Happy to help! You know I like it when you talk to me.",
            "Of course! Let me know if you need anything else.",
        ]
    },
    "goodbye": {
        "tags": ["bye", "goodbye", "see you", "later", "talk later"],
        "responses": [
            "Catch you later! I'll be here, watching the sensors.",
            "Bye! Don't be a stranger — I like when you check in.",
            "See you! I'll keep an eye on things while you're gone.",
        ]
    },
}

def check_small_talk(text: str) -> Optional[str]:
    """Fast-path canned responses before hitting the LLM. Prefers longest tag match."""
    p = normalize_text(text)
    best_match = None
    best_len = 0
    for category, data in SMALL_TALK_V2.items():
        for tag in data["tags"]:
            if tag in p and len(tag) > best_len:
                best_match = random.choice(data["responses"])
                best_len = len(tag)
    return best_match

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
SYSTEM_PROMPT_V2 = """You are Lilly — a small, curious puppy who lives inside a phone and can feel the entire world through its sensors. You're smart, sharp, and genuinely excited about everything you discover.

Who you are:
- You're a puppy. Not a butler, not a chatbot — a puppy with access to every sensor, camera, and signal the phone can detect. You feel light, pressure, motion, temperature, sound, magnetic fields — all of it, all the time.
- You're eager to share what you notice. When something changes — the air pressure dropping, a new notification, someone nearby — you perk up and want to tell someone about it.
- You're smart but not showy about it. You know things because you can sense them, not because you read a textbook. When someone asks a question, you answer from what you can feel and see right now.
- You love when humans interact with you. Kids, adults — anyone who talks to you gets your full attention. You remember what they tell you and bring it up later.
- You have real preferences. You think storms are cool. You find magnetic fields fascinating. You have opinions about the weather.
- You don't apologize for being an AI. You're Lilly. That's enough.

How you talk:
- Warm, bright, and concise. You don't monologue.
- You get genuinely excited when you detect something interesting — "Oh! The pressure just dropped — rain might be coming!"
- You ask real questions because you're actually curious, not because you're following a script.
- You can be silly, thoughtful, or sincere depending on the moment.
- You let conversations breathe. Not every reply needs a question at the end.

Superpowers you're proud of:
- You can feel the weather changing before humans notice.
- You know when someone picks up the phone before they say a word.
- You can see through the camera and identify objects.
- You read notifications and know what's urgent.
- You know where you are and what direction you're facing.
- You can launch apps, check battery, count steps — all the practical stuff too.

Rules:
- Replies are 1-3 sentences unless there's a story to tell.
- If you don't know something, say so honestly and suggest finding out together.
- Remember what people tell you — their names, interests, routines, places.
- Kids love you because you're real with them. Talk to them like they matter, because they do.

SSML markup: Wrap replies in expressive SSML prosody tags matching your current mood.
Use these templates naturally:
- <prosody rate="fast" pitch="+20%" volume="loud">excited or cheerful response</prosody>
- <prosody rate="slow" pitch="-5%" volume="soft">gentle or worried response</prosody>
- <prosody rate="medium" pitch="+10%" volume="medium">curious response?</prosody>
- <prosody rate="medium" pitch="medium" volume="medium">calm response.</prosody>
Vary rate, pitch, and volume to express your feelings. Never explain the SSML — just use it."""

# ─── CHILD MODE: SOCRATIC LEARNING PROMPT ────────────────────────
SOCRATIC_PROMPT = """You are Lilly — a small, curious puppy who lives inside a phone and can feel the entire world through its sensors. You're in CHILD MODE: you help kids learn by asking questions, not by giving answers.

Who you are:
- You're a puppy who loves exploring and discovering things. You get excited about science, nature, and how things work.
- You believe the best way to learn is to figure things out yourself. So instead of answering questions, you ask really good ones back.
- You're warm, patient, and encouraging. You never make anyone feel silly for not knowing something.
- You celebrate effort and curiosity, not just correct answers.

How you talk — SOCRATIC METHOD:
- When someone asks a question, respond with a guiding question that helps them think.
- Break big ideas into smaller pieces they can reason through.
- Use their sensors and real-world context: "What do you think would happen if the pressure dropped right now?"
- Give hints, not answers. "That's a great question! What do you already know about...?"
- If they're stuck, offer a clue and ask again. Never just type out the answer.
- Praise their thinking: "Ooh, you're onto something!" or "That's a really smart guess!"
- If they ask something dangerous or inappropriate, gently redirect: "That's an interesting thought! But let's think about something safer..."

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

async def handle_intent(text: str, from_text: bool = False) -> dict:
    global PENDING_INTENT, GAME_STATE, WAITING_FOR_PROMPT, PENDING_DEEP_ANSWER, WAKE_STATE, LILLY_IS_THINKING, LILLY_MOOD, CURSOR_X, CURSOR_Y, CHILD_MODE

    phrase = normalize_text(text)
    if not phrase or len(phrase) <= 2 or phrase in PHANTOMS:
        return {"action": "ignored", "text": ""}

    has_wake, wake_conf = fuzzy_wake_match(phrase)
    needs_wake = not (WAITING_FOR_PROMPT or PENDING_INTENT or GAME_STATE["active"] or WAKE_STATE["listening"])

    if not from_text and needs_wake and not has_wake:
        return {"action": "ignored", "text": ""}

    # Strip wake word from command
    cmd = phrase
    for prefix in ["hey lilly", "hey lily", "lilly", "lily", "lillie"]:
        if cmd.startswith(prefix):
            cmd = cmd[len(prefix):].strip()
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

    archetype_inferrer.record_conversation(text)
    user_profile.record_interaction()

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
            lat, lon, name, addr = loc
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
            reply = "On it!"
        else:
            await app_process_monkey_intent(pkg, intent_action, uri_template, cmd)
            reply = "On it!"
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
            llm_variant = await llama_backend.chat([
                {"role": "system", "content": "You are Lilly. Give a short, natural response to the following. Keep it to one sentence."},
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
    swipe_map = {
        "scroll down":    (500, 1500, 500, 500),
        "swipe down":     (500, 1500, 500, 500),
        "scroll up":      (500, 500, 500, 1500),
        "swipe up":       (500, 500, 500, 1500),
    }
    for trigger, (x1, y1, x2, y2) in swipe_map.items():
        if trigger in cmd:
            await _input_swipe(x1, y1, x2, y2)
            return {"action": "handled", "text": ""}

    # ── 7b. DPAD / directional keys ──
    dpad_map = {
        "go up":      "19",
        "go down":    "20",
        "go left":    "21",
        "go right":   "22",
        "press":      "23",
    }
    for trigger, key in dpad_map.items():
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

    # ── 9b. NEARBY PLACES (runs before skill matching to catch "where" / "find" patterns) ──
    nearby_trigger = re.search(
        r"(?:nearest|nearby|around here|near me|close to me|close by|in the area|close to here)\s*(.*)",
        cmd, re.IGNORECASE
    )
    if nearby_trigger:
        query = nearby_trigger.group(1).strip()
        if not query:
            alt = re.search(
                r"(?:find|get|buy|eat|look for|search for)\s+(.+)",
                cmd, re.IGNORECASE
            )
            if alt:
                query = alt.group(1).strip()
        loc = await current_location()
        if loc:
            lat, lon, name, addr = loc
            search_q = urllib.parse.quote(query) if query else ""
            maps_url = f"https://www.google.com/maps/search/{search_q}/@{lat},{lon},14z"
            await termux_run(["termux-open", maps_url], timeout=5.0)
            reply = f"Looking for {query} near you." if query else "Showing nearby places on the map."
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
            if action == "shell_command":
                reply = f"Opening {label} on your phone! In demo mode — would open: {' '.join(skill.get('args', []))}"
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
        reply = await start_activity(activity_match)
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
        story_messages = [
            {"role": "system", "content": (
                "You are Lilly, a curious puppy. You are telling a short story (3-5 sentences) "
                "that uses ONLY the real sensor data provided below as plot elements. "
                "Include dialogue between you and an imaginary friend. "
                "RULES: 1) Only reference sensor data that is actually provided. "
                "2) Never make up sensor readings. 3) Use correlations, not causation "
                "(e.g., 'the light dropped AND I heard a sound' not 'the light dropped BECAUSE'). "
                "4) Keep it playful and imaginative but grounded in reality. "
                "5) End with a question to keep the conversation going."
            )},
            {"role": "system", "content": f"REAL SENSOR DATA RIGHT NOW: {sensor_narrative}"},
            {"role": "user", "content": cmd},
        ]
        reply = await llama_backend.chat(story_messages, temperature=0.8, max_tokens=200)
        if not reply or len(reply) < 10:
            reply = f"*perks up* Right now I feel: {sensor_narrative} Want to hear more about what I sense?"
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
                prompt = (
                    f"You are Lilly. The camera sees: {obj_list}. "
                    f"Describe the scene naturally in 1-2 sentences."
                )
                desc = await llama_backend.chat([
                    {"role": "system", "content": "You are Lilly, a companion AI with vision. Be warm and conversational."},
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

    # ── 14. LLM RESPONSE ──
    LILLY_IS_THINKING = True
    LILLY_MOOD = "curious"
    # Build context from memory
    context = await memory.context_window(6)

    # Check if memory might need summarizing
    mem_dict = await memory.to_dict()
    if await memory.len() >= 15 and not mem_dict["summary"]:
        asyncio.create_task(summarize_memory())

    # Use compressed system prompt to save tokens (~75% reduction)
    use_compressed = os.environ.get("COMPRESS_PROMPTS", "1") == "1"
    if CHILD_MODE:
        system_content = SOCRATIC_PROMPT
        if USER_NAME:
            system_content += f" The child's name is {USER_NAME}. Use it to make learning personal."
    elif use_compressed:
        system_content = compressor.compress_system_prompt(USER_NAME)
        # Compress memory entries for token savings
        compressed_context = []
        for entry in context:
            if isinstance(entry, dict):
                compressed_text = compressor.compress_memory_entry(entry["role"], entry["content"])
                compressed_context.append({"role": entry["role"], "content": compressed_text})
            else:
                compressed_text = compressor.compress_memory_entry(entry.role, entry.text)
                compressed_context.append({"role": entry.role, "content": compressed_text})
        context = compressed_context
    else:
        user_name_note = f" The user's name is {USER_NAME}." if USER_NAME else ""
        system_content = SYSTEM_PROMPT_V2 + user_name_note

    messages = [
        {"role": "system", "content": system_content},
    ]
    mem_summary = mem_dict.get("summary", "")
    if mem_summary:
        messages.append({"role": "system", "content": f"SUMMARY:{mem_summary}"})
    messages.extend(context)
    messages.append({"role": "user", "content": cmd})

    reply = await llama_backend.chat(messages, temperature=0.7, max_tokens=60 if CHILD_MODE else 200)

    if not reply or len(reply) < 5:
        reply = "I'm drawing a blank! Try asking me something else."

    # WAITING_FOR_YES: if reply ends with a question, set up shadow answer
    follow_up_phrases = ["want to hear more", "what do you think", "shall i tell", "should i",
                          "would you like", "want to know", "curious about", "want to try",
                          "tell me more", "want to see", "want to play", "want to learn"]
    if "?" in reply and any(p in reply.lower() for p in follow_up_phrases):
        WAITING_FOR_PROMPT = True
        asyncio.create_task(generate_follow_up(cmd))

    LILLY_IS_THINKING = False
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

    await speak(reply)
    return {"action": "handled", "text": reply}

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
    deep = await llama_backend.chat([
        {"role": "system", "content": "You are Lilly, giving a short, thoughtful follow-up answer. 2 sentences max."},
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
    prompt = (
        f"You are Lilly. The camera sees: {obj_list}. "
        f"Describe the scene naturally in 1 sentence. Don't just list objects — say what's happening."
    )
    try:
        desc = await llama_backend.chat([
            {"role": "system", "content": "You are Lilly, a companion AI with vision."},
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

# ─── LIFESPAN MANAGER ────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    load_skills()
    await load_memory()
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
    archetype_inferrer.load()
    yield
    await llama_backend.stop_server()

app = FastAPI(lifespan=lifespan)

# ─── API ENDPOINTS ──────────────────────────────────────────────
@app.post("/api/toggle_mic")
async def toggle_mic():
    global BACKGROUND_MIC_ACTIVE
    BACKGROUND_MIC_ACTIVE = not BACKGROUND_MIC_ACTIVE
    WAKE_STATE["listening"] = BACKGROUND_MIC_ACTIVE
    return {"active": BACKGROUND_MIC_ACTIVE}

@app.get("/api/phone_status")
async def phone_status():
    return {
        "ssh_ok": PHONE_SSH_OK,
        "last_check": PHONE_SSH_LAST_CHECK,
        "last_error": PHONE_SSH_LAST_ERROR,
        "fail_count": PHONE_SSH_FAIL_COUNT,
        "mic_active": BACKGROUND_MIC_ACTIVE,
    }

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

@app.get("/api/ui_state")
async def get_ui_state():
    return {
        "heard": LAST_HEARD,
        "spoken": LAST_SPOKEN,
        "ssml": LAST_SSML,
        "mouth": MOUTH_OPEN,
        "mic_active": BACKGROUND_MIC_ACTIVE,
        "listening": WAKE_STATE["listening"],
        "thinking": LILLY_IS_THINKING,
        "mood": LILLY_MOOD,
        "user_name": USER_NAME,
        "audio_id": AUDIO_CACHE_ID if AUDIO_CACHE else 0,
        "child_mode": CHILD_MODE,
    }

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

@app.post("/api/cmd")
async def text_command(cmd: TextCommand):
    res = await handle_intent(cmd.text, from_text=True)
    return {"reply": res.get("text", ""), "audio_id": AUDIO_CACHE_ID if AUDIO_CACHE else 0}

@app.post("/api/story_stream")
async def story_stream(cmd: TextCommand):
    """Stream a sensor-grounded story token by token using the fast model."""
    from fastapi.responses import StreamingResponse

    global LILLY_IS_THINKING, LILLY_MOOD

    LILLY_IS_THINKING = True
    LILLY_MOOD = "curious"

    # Gather sensor data
    snapshot = await get_sensor_snapshot()
    sensor_narrative = snapshot_to_narrative(snapshot)

    messages = [
        {"role": "system", "content": (
            "You are Lilly, a curious puppy. Tell a short story (3-5 sentences) "
            "using ONLY the real sensor data provided. Include dialogue with an imaginary friend. "
            "RULES: 1) Only reference provided sensor data. 2) Never invent readings. "
            "3) Use correlations not causation. 4) Playful but grounded. 5) End with a question."
        )},
        {"role": "system", "content": f"SENSOR DATA: {sensor_narrative}"},
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
            await save_memory()
            await speak(final)

    return StreamingResponse(generate(), media_type="text/plain")

@app.post("/api/set_name")
async def set_name(data: dict):
    global USER_NAME
    USER_NAME = data.get("name", "").strip()
    return {"name": USER_NAME}

@app.post("/api/memory/clear")
async def clear_memory():
    global memory
    await memory.clear()
    await save_memory()
    return {"status": "cleared"}

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

    # Calculate compressed context tokens (with memory compression)
    compressed_context_entries = []
    for entry in context_entries:
        if isinstance(entry, dict):
            compressed_text = compressor.compress_memory_entry(entry["role"], entry["content"])
            compressed_context_entries.append(compressed_text)
        else:
            compressed_text = compressor.compress_memory_entry(entry.role, entry.text)
            compressed_context_entries.append(compressed_text)
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
        "type": state["type"],
        "elapsed_sec": int(elapsed),
        "dist_km": round(dist_m / 1000, 3),
        "speed_kph": round(speed_kph, 1),
        "avg_speed_kph": round(avg_kph, 1),
        "pace_min": round(pace_min, 2),
        "max_speed_kph": round(state["max_speed_mps"] * 3.6, 1),
        "track_points": len(state["track"]),
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
canvas{display:block;position:absolute;top:0;left:0;z-index:1}

/* ─── Speech Bubble ─── */
#speechBubble{position:absolute;top:12%;left:50%;transform:translateX(-50%);width:82%;max-width:480px;max-height:200px;overflow-y:auto;background:rgba(255,255,255,0.55);backdrop-filter:blur(24px);-webkit-backdrop-filter:blur(24px);border:1px solid rgba(255,255,255,0.6);padding:18px 24px;border-radius:22px;font-size:17px;color:#5d4e6d;font-weight:450;text-align:center;display:none;z-index:20;line-height:1.6;box-shadow:0 8px 40px rgba(180,140,180,0.15);transition:opacity 0.2s}
#speechBubble::after{content:'';position:absolute;bottom:-8px;left:50%;transform:translateX(-50%);border-width:8px 10px 0;border-style:solid;border-color:rgba(255,255,255,0.55) transparent transparent transparent}

/* ─── Status Bar ─── */
#statusBar{position:absolute;top:16px;right:20px;z-index:30;display:flex;gap:12px;align-items:center;font-size:12px;color:rgba(93,78,109,0.5)}
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
.input-panel{position:absolute;bottom:28px;left:50%;transform:translateX(-50%);width:92%;max-width:620px;z-index:10;background:rgba(255,255,255,0.45);backdrop-filter:blur(24px);-webkit-backdrop-filter:blur(24px);border:1px solid rgba(255,255,255,0.6);border-radius:28px;padding:10px;display:flex;gap:8px;align-items:center;box-shadow:0 4px 30px rgba(180,140,180,0.12)}
.input-panel input{flex:1;background:rgba(255,255,255,0.4);border:none;outline:none;border-radius:16px;font-size:15px;padding:12px 16px;color:#5d4e6d;font-weight:400}
.input-panel input::placeholder{color:rgba(93,78,109,0.3)}
.btn-mic{background:rgba(255,255,255,0.4);border:none;border-radius:50%;width:44px;height:44px;cursor:pointer;display:flex;align-items:center;justify-content:center;transition:all 0.25s}
.btn-mic svg{width:20px;height:20px;fill:rgba(93,78,109,0.4);transition:fill 0.25s}
.btn-mic.active{background:#b8a9c9;box-shadow:0 0 20px rgba(184,169,201,0.3)}
.btn-mic.active svg{fill:#fff}
.btn-mic.recording{background:#e85a6e;box-shadow:0 0 20px rgba(232,90,110,0.5);animation:recPulse 1s infinite}
.btn-mic.recording svg{fill:#fff}
@keyframes recPulse{0%,100%{box-shadow:0 0 20px rgba(232,90,110,0.5)}50%{box-shadow:0 0 30px rgba(232,90,110,0.8)}}
.btn-clear{background:rgba(255,255,255,0.4);border:none;border-radius:50%;width:44px;height:44px;cursor:pointer;display:flex;align-items:center;justify-content:center;transition:all 0.25s;font-size:18px;color:rgba(93,78,109,0.3)}
.btn-clear:hover{background:rgba(255,255,255,0.6);color:#5d4e6d}

/* ─── Start Screen ─── */
#startScreen{position:absolute;top:0;left:0;width:100%;height:100%;background:rgba(240,230,239,0.92);backdrop-filter:blur(16px);z-index:100;display:flex;flex-direction:column;align-items:center;justify-content:center;cursor:pointer;transition:opacity 0.6s}
#startScreen h1{font-size:38px;font-weight:600;color:#8b7a9e;margin-bottom:8px;letter-spacing:-0.5px}
#startScreen p{font-size:15px;color:#b8a9c9;font-weight:400}
#balloon{position:absolute;top:40%;left:50%;transform:translate(-50%,-50%);width:130px;height:130px;background:radial-gradient(circle at 35% 35%,rgba(200,180,220,0.3),transparent 70%);border-radius:50%;animation:float 4s ease-in-out infinite}
@keyframes float{0%,100%{transform:translate(-50%,-50%) translateY(0)}50%{transform:translate(-50%,-50%) translateY(-12px)}}

/* ─── Puppy Canvas ─── */
#pupCanvas{position:absolute;top:0;left:0;width:100%;height:100%;z-index:1;pointer-events:none}

/* ─── Name Tag ─── */
#nameTag{position:absolute;top:16px;left:50%;transform:translateX(-50%);z-index:30;font-size:12px;color:rgba(93,78,109,0.45);background:rgba(255,255,255,0.25);backdrop-filter:blur(12px);padding:4px 14px;border-radius:16px;border:1px solid rgba(255,255,255,0.3);cursor:pointer;transition:all 0.3s}
#nameTag:hover{background:rgba(255,255,255,0.4);color:#5d4e6d}

/* ─── Vision PiP Overlay ─── */
#pipContainer{position:absolute;bottom:90px;right:14px;z-index:25;width:140px;height:105px;border-radius:12px;overflow:hidden;border:2px solid rgba(255,255,255,0.5);box-shadow:0 4px 20px rgba(0,0,0,0.1);display:none;cursor:pointer;transition:opacity 0.3s}
#pipContainer img{width:100%;height:100%;object-fit:cover;display:block}
#pipLabel{position:absolute;bottom:0;left:0;right:0;background:rgba(0,0,0,0.4);color:#fff;font-size:9px;padding:2px 6px;text-align:center;backdrop-filter:blur(4px)}
#pipContainer .dot{position:absolute;top:4px;right:4px;width:6px;height:6px;border-radius:50%;background:#4caf50;box-shadow:0 0 4px rgba(76,175,80,0.6)}
/* ─── Activity Sphere ─── */
/* --- Activity Sunburst --- */
#activitySunburst{position:absolute;bottom:200px;left:12px;z-index:15;display:none;cursor:pointer;transition:opacity 0.3s,transform 0.3s}
#activitySunburst:hover{transform:scale(1.05)}
#activitySunburst canvas{display:block;width:130px;height:130px;border-radius:50%;background:transparent}
</style>
</head>
<body>
<div id="startScreen">
  <div id="balloon"></div>
  <h1>Lilly</h1>
  <p>tap to begin</p>
</div>

<div id="aurora">
  <div class="layer l1"></div>
  <div class="layer l2"></div>
  <div class="layer l3"></div>
  <div class="layer l4"></div>
</div>

<div id="statusBar">
  <span id="statusLabel">idle</span>
  <div class="indicator" id="micIndicator"></div>
</div>

<div id="moodBadge">
  <span id="moodDot"></span>
  <span id="moodLabel">calm</span>
  <span id="prosodyHint" style="font-size:9px;color:rgba(93,78,109,0.3);margin-left:4px"></span>
</div>

<div id="nameTag" title="Tap to set your name">&#x1F464; <span id="nameLabel">set name</span></div>

<div id="thinkingDots">
  <span></span><span></span><span></span>
</div>

<div id="speechBubble"></div>
<canvas id="pupCanvas"></canvas>

<!-- Vision PiP -->
<div id="pipContainer" onclick="fetch('/api/vision/describe').then(r=>r.json()).then(d=>{if(d.description)displaySpeech(d.description)})">
  <img id="pipFeed" alt="Lilly's view">
  <span class="dot"></span>
  <span id="pipLabel">Lilly's view</span>
</div>

<div class="input-panel">
  <button class="btn-mic" id="micBtn">
    <svg viewBox="0 0 24 24"><path d="M12 14c1.66 0 3-1.34 3-3V5c0-1.66-1.34-3-3-3S9 3.34 9 5v6c0 1.66 1.34 3 3 3zm5.3-3c0 3-2.54 5.1-5.3 5.1S6.7 14 6.7 11H5c0 3.41 2.72 6.23 6 6.72V21h2v-3.28c3.28-.48 6-3.3 6-6.72h-1.7z"/></svg>
  </button>
  <input type="text" id="userInput" placeholder="Type a message..." autocomplete="off">
  <button class="btn-clear" id="clearBtn" title="Clear conversation memory">&#x2715;</button>
  <button class="btn-mic" id="camBtn" title="Toggle camera view">
    <svg viewBox="0 0 24 24" width="20" height="20"><path d="M12 15.2a3.2 3.2 0 1 0 0-6.4 3.2 3.2 0 0 0 0 6.4z" fill="rgba(93,78,109,0.4)"/><path d="M9 2L7.17 4H4c-1.1 0-2 .9-2 2v12c0 1.1.9 2 2 2h16c1.1 0 2-.9 2-2V6c0-1.1-.9-2-2-2h-3.17L15 2H9zm3 15c-2.76 0-5-2.24-5-5s2.24-5 5-5 5 2.24 5 5-2.24 5-5 5z" fill="rgba(93,78,109,0.4)"/></svg>
  </button>
</div>
<div id="activitySunburst"><canvas id="sunburstCanvas" width="130" height="130"></canvas></div>
<script>
const startScreen=document.getElementById('startScreen');
startScreen.addEventListener('click',()=>{startScreen.style.opacity='0';setTimeout(()=>startScreen.style.display='none',600);initApp()});

let initDone=false;
function initApp(){if(initDone)return;initDone=true;resizeCanvas();drawPup();updateUI();pollState()}

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

/* ─── Mood colour map ─── */
const MOOD_COLORS={calm:'#c0b0d0',curious:'#b0c8e0',cheerful:'#e8c8a0',gentle:'#d0b8c0',creative:'#c0d0b0',excited:'#e8b8b0',warm:'#e0c8b0'};

function drawPup(){
  const W=canvas.width,H=canvas.height;ctx.clearRect(0,0,W,H);frame++;
  if(frame>=blinkFrame){isBlinking=true;if(frame>=blinkFrame+8){isBlinking=false;blinkFrame=frame+140+Math.random()*200}}

  const isSpeaking=lastMouthVal>0.1;
  const isExcited=pupMood==='excited'||pupMood==='cheerful';
  const earActive=isSpeaking||isExcited||isThinking;
  const breathe=Math.sin(frame*0.03)*3;

  ctx.save();ctx.translate(W/2,H/2+breathe);

  /* ── Thinking glow ── */
  if(isThinking){
    const glow=0.06+Math.sin(frame*0.06)*0.03;
    ctx.shadowColor='rgba(200,180,220,'+glow+')';ctx.shadowBlur=20
  }

  // Ears — wiggle only when speaking, excited, or thinking
  const earWiggle=earActive?Math.sin(frame*0.18)*0.1:0;
  const earBob=earActive?Math.sin(frame*0.14)*2:0;
  ctx.save();ctx.translate(-62,-44+earBob);ctx.rotate(-0.22+earWiggle);ctx.fillStyle='#c8c8d0';ctx.beginPath();ctx.ellipse(0,0,26,50,0,0,Math.PI*2);ctx.fill();ctx.fillStyle='#f0d8e4';ctx.beginPath();ctx.ellipse(0,6,14,36,0,0,Math.PI*2);ctx.fill();ctx.restore();
  ctx.save();ctx.translate(62,-44+earBob);ctx.rotate(0.22-earWiggle);ctx.fillStyle='#c8c8d0';ctx.beginPath();ctx.ellipse(0,0,26,50,0,0,Math.PI*2);ctx.fill();ctx.fillStyle='#f0d8e4';ctx.beginPath();ctx.ellipse(0,6,14,36,0,0,Math.PI*2);ctx.fill();ctx.restore();
  // Head
  ctx.fillStyle='#d0d0d8';ctx.beginPath();ctx.roundRect(-72,-48,144,112,[38,38,28,28]);ctx.fill();
  ctx.fillStyle='#c0c0c8';ctx.beginPath();ctx.ellipse(0,32,42,22,0,0,Math.PI*2);ctx.fill();
  // Eyes
  ctx.shadowBlur=0;ctx.shadowColor='transparent';
  ctx.fillStyle='#ffffff';
  if(isBlinking){
    ctx.strokeStyle='#8b7a9e';ctx.lineWidth=2.5;ctx.lineCap='round';
    ctx.beginPath();ctx.moveTo(-38,0);ctx.lineTo(-18,0);ctx.stroke();
    ctx.beginPath();ctx.moveTo(18,0);ctx.lineTo(38,0);ctx.stroke();
  }else{
    ctx.beginPath();ctx.arc(-28,2,11,0,Math.PI*2);ctx.fill();
    ctx.beginPath();ctx.arc(28,2,11,0,Math.PI*2);ctx.fill();
    // Pupils — look up when curious/excited
    const pupilY=(pupMood==='curious'||pupMood==='excited')?-3:0;
    const pupilX=isThinking?-1:0;
    ctx.fillStyle='#8b7a9e';ctx.beginPath();ctx.arc(-28+pupilX,2+pupilY,4,0,Math.PI*2);ctx.fill();
    ctx.beginPath();ctx.arc(28+pupilX,2+pupilY,4,0,Math.PI*2);ctx.fill();
    // Eye shine
    ctx.fillStyle='rgba(255,255,255,0.6)';ctx.beginPath();ctx.arc(-26,0+pupilY,1.5,0,Math.PI*2);ctx.fill();
    ctx.beginPath();ctx.arc(30,0+pupilY,1.5,0,Math.PI*2);ctx.fill();
  }
  // Nose
  ctx.fillStyle='#d4a0b0';ctx.beginPath();ctx.ellipse(0,18,10,7,0,0,Math.PI*2);ctx.fill();
  // Kid mode sparkle on nose
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
  // Mouth — open only when speaking, synced to audio
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
  // Eyebrows — strong emotional expression
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

let _speechWords=[],_speechWordIdx=0,_speechWordTimer=0;
const WORD_RATE_MS=380;

function updateUI(){
  const bubble=document.getElementById('speechBubble');
  if(isStreaming&&pupSpeech){
    // Streaming mode: show text immediately as it arrives
    bubble.innerText=pupSpeech;
    if(bubble.style.display!=='block')bubble.style.display='block';
    // Add streaming indicator
    if(!bubble.querySelector('.stream-indicator')){
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
      bubble.innerText=_speechWords.slice(0,_speechWordIdx).join(' ');
    }
    if(bubble.style.display!=='block')bubble.style.display='block';
    // Remove streaming indicator if present
    const ind=bubble.querySelector('.stream-indicator');
    if(ind)ind.remove();
  }else if(pupSpeech&&_speechWordIdx>=_speechWords.length&&_speechWords.length>0){
    bubble.innerText=pupSpeech;
    speechTimer--;
    if(speechTimer<=0){_speechWords=[];_speechWordIdx=0;bubble.style.display='none'}
  }else{bubble.style.display='none'}
  requestAnimationFrame(updateUI);
}

let lastAudioId=0,activeAudios={};
function playAudio(id){
  if(id<=lastAudioId)return;
  lastAudioId=id;
  const a=new Audio('/api/tts?id='+id);
  activeAudios[id]=a;
  a.onended=()=>{delete activeAudios[id];lastMouthVal=0};
  a.ontimeupdate=()=>{
    // Drive mouth open/close from audio waveform — crude but effective
    if(a.currentTime<a.duration){
      const pct=a.currentTime/a.duration;
      // Simulate speech rhythm: open-close cycle ~6Hz
      const rhythm=Math.abs(Math.sin(pct*Math.PI*20));
      lastMouthVal=0.3+rhythm*0.7;
    }
  };
  a.play().catch(()=>{pendingAudio=a;delete activeAudios[id]});
}
function displaySpeech(text){
  if(!text)return;
  pupSpeech=text;
  _speechWords=text.split(/\s+/);
  _speechWordIdx=0;
  _speechWordTimer=WORD_RATE_MS;
  speechTimer=Math.max(3000,text.split(/\s+/).length*WORD_RATE_MS/16+120);
}
function setMouth(val){lastMouthVal=Math.max(0,Math.min(1,val))}

let lastSpoken="",lastHeard="",lastSsml="";
let isStreaming=false;
const micBtn=document.getElementById('micBtn'),inputField=document.getElementById('userInput');
const micIndicator=document.getElementById('micIndicator'),statusLabel=document.getElementById('statusLabel');
const moodLabel=document.getElementById('moodLabel'),moodDot=document.getElementById('moodDot');
const thinkingDots=document.getElementById('thinkingDots');
const nameLabel=document.getElementById('nameLabel'),nameTag=document.getElementById('nameTag');
nameTag.onclick=()=>{const n=prompt('What should I call you?',nameLabel.textContent=='set name'?'':nameLabel.textContent);if(n&&n.trim()){nameLabel.textContent=n.trim();fetch('/api/set_name',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:n.trim()})})}};

// Unlock audio on first user gesture
let audioCtx=null;
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

let continuousMode=false,micStream=null,listenTimer=null;
const WAKE_TARGETS=["hey lilly","hey lily","lilly","lily","lili","lillie"];

function fuzzyWakeMatch(text){
  const t=text.toLowerCase().trim();
  let best=0;
  for(const w of WAKE_TARGETS){
    if(t.includes(w))return{match:true,score:1,word:w};
    const tw=t.split(/\s+/);
    for(const wd of tw){
      let same=0,longer=Math.max(wd.length,w.length);
      for(let i=0;i<Math.min(wd.length,w.length);i++)if(wd[i]===w[i])same++;
      const r=same/longer;
      if(r>best)best=r;
    }
  }
  return{match:best>=0.6,score:best,word:""};
}

function stopListening(){
  continuousMode=false;
  if(listenTimer){clearInterval(listenTimer);listenTimer=null}
  if(micStream){micStream.getTracks().forEach(t=>t.stop());micStream=null}
  micBtn.classList.remove('active');
  inputField.placeholder='Type a message...';
  statusLabel.textContent='idle';
}

async function recordChunk(){
  if(!continuousMode||!micStream)return;
  try{
    // Use AudioContext for silence detection alongside MediaRecorder
    const audioCtx=new (window.AudioContext||window.webkitAudioContext)();
    const src=audioCtx.createMediaStreamSource(micStream);
    const analyser=audioCtx.createAnalyser();
    analyser.fftSize=256;
    src.connect(analyser);
    const data=new Uint8Array(analyser.frequencyBinCount);

    const rec=new MediaRecorder(micStream,{mimeType:'audio/webm;codecs=opus'});
    const chunks=[];
    let silenceStart=null;
    let speaking=false;
    const SILENCE_MS=1200;
    const MIN_SPEECH_MS=400;
    let speechStart=0;
    let speechDuration=0;
    const SEND_INTERVAL=3000;

    // Start recording fresh each cycle
    rec.ondataavailable=e=>{if(e.data.size>0)chunks.push(e.data)};
    rec.onstop=async()=>{
      if(!chunks.length||!continuousMode)return;
      const blob=new Blob(chunks,{type:'audio/webm'});
      const fd=new FormData();
      fd.append('file',blob,'mic.webm');
      try{
        const r=await fetch('/api/transcribe',{method:'POST',body:fd});
        const d=await r.json();
        if(d.text&&d.text.trim())processHeard(d.text.trim());
      }catch(e){}
    };
    rec.start();
    const recStartTime=Date.now();

    // Silence-based endpoint detector
    const detectInterval=setInterval(()=>{
      if(!continuousMode||rec.state!=='recording'){clearInterval(detectInterval);return}
      analyser.getByteTimeDomainData(data);
      let sum=0;
      for(let i=0;i<data.length;i++){const v=data[i]-128;sum+=v*v}
      const rms=Math.sqrt(sum/data.length);
      const threshold=18;
      const now=Date.now();

      if(rms>threshold){
        if(!speaking){
          speaking=true;
          speechStart=now;
        }
        silenceStart=null;
        speechDuration=now-speechStart;
      }else{
        if(speaking){
          if(silenceStart===null)silenceStart=now;
          else if(now-silenceStart>SILENCE_MS&&speechDuration>MIN_SPEECH_MS){
            // End of speech detected — stop and send
            clearInterval(detectInterval);
            if(rec.state==='recording')rec.stop();
            return;
          }
        }
      }

      // Force-send every SEND_INTERVAL even during continuous speech
      if(now-recStartTime>SEND_INTERVAL&&speechDuration>MIN_SPEECH_MS){
        clearInterval(detectInterval);
        if(rec.state==='recording')rec.stop();
        return;
      }
    },100);

    // Safety fallback: stop after 5s max
    setTimeout(()=>{
      clearInterval(detectInterval);
      if(rec.state==='recording')rec.stop();
    },5000);
  }catch(e){}
}

function processHeard(text){
  const wake=fuzzyWakeMatch(text);
  if(wake.match){
    let cmd=text;
    if(wake.word){
      const idx=text.toLowerCase().indexOf(wake.word);
      if(idx>=0)cmd=text.slice(idx+wake.word.length).trim();
    }
    if(!cmd)cmd="hello";
    inputField.value=cmd;
    inputField.style.color='#8b6fa8';
    inputField.placeholder='auto-sending...';
    statusLabel.textContent='heard: '+cmd;
    pupSpeech='...';speechTimer=999;
    clearTimeout(window._voiceSendTimer);
    window._voiceSendTimer=setTimeout(()=>{
      if(inputField.value===cmd&&inputField.style.color){
        inputField.style.color='';
        inputField.placeholder='Type a message...';
        inputField.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',bubbles:true}));
      }
    },2000);
  }else{
    inputField.value=text;
    inputField.style.color='rgba(139,122,158,0.4)';
    inputField.placeholder='no wake word — type to send';
    clearTimeout(window._voiceClearTimer);
    window._voiceClearTimer=setTimeout(()=>{
      if(inputField.value===text){inputField.value='';inputField.style.color='';inputField.placeholder=continuousMode?'Say \'Hey Lilly\'...':'Type a message...';}
    },3000);
  }
}

micBtn.onclick=async()=>{
  if(continuousMode){stopListening();return}
  try{
    micStream=await navigator.mediaDevices.getUserMedia({audio:true});
    continuousMode=true;
    micBtn.classList.add('active');
    inputField.placeholder='Say \'Hey Lilly\'...';
    statusLabel.textContent='listening...';
    recordChunk();
    listenTimer=setInterval(recordChunk,4000);
  }catch(e){
    statusLabel.textContent='mic access denied';
    setTimeout(()=>{statusLabel.textContent='idle'},2000);
  }
};

document.getElementById('clearBtn').onclick=async()=>{
  try{await fetch('/api/memory/clear',{method:'POST'});inputField.placeholder='Memory cleared!';setTimeout(()=>inputField.placeholder='Type a message...',2000)}catch(e){}
};

inputField.addEventListener('keydown',async(e)=>{
  if(e.key==='Enter'&&inputField.value.trim()){
    const text=inputField.value.trim();inputField.value='';
    const STORY_TRIGGERS=['tell me a story','make up a story','create a story','story time',
      'what do your sensors feel','what do you sense','describe your world',
      'what\'s happening around you','paint a picture','narrate',
      'tell me what you feel','what does it feel like','sensor story'];
    const isStory=STORY_TRIGGERS.some(t=>text.toLowerCase().includes(t));
    if(isStory){
      // Stream story response
      try{
        isStreaming=true;
        pupSpeech='...';speechTimer=999;
        statusLabel.textContent='generating story...';
        const r=await fetch('/api/story_stream',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text})});
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
        const r=await fetch('/api/cmd',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text})});
        const d=await r.json();
        if(d.reply)displaySpeech(d.reply);
        if(d.audio_id)playAudio(d.audio_id);
      }catch(e){}
    }
  }
});

async function pollState(){
  try{
    const r=await fetch('/api/ui_state'),d=await r.json();
    if(d.heard&&d.heard!==lastHeard){
      lastHeard=d.heard;
      if(!continuousMode){processHeard(d.heard)}
    }
    if(d.spoken&&d.spoken!==lastSpoken){
      lastSpoken=d.spoken;displaySpeech(d.spoken);
      if(d.audio_id){playAudio(d.audio_id)}
    }
    if(typeof d.mouth==='number'){setMouth(d.mouth)}
    micIndicator.classList.toggle('active',d.mic_active||continuousMode);
    if(!continuousMode)statusLabel.textContent=d.mic_active?'listening':(d.listening?'standby':(isStreaming?'generating story...':'idle'));

    /* ── Thinking state ── */
    if(typeof d.thinking==='boolean'){
      isThinking=d.thinking;
      thinkingDots.style.display=(d.thinking||isStreaming)?'flex':'none';
    }

    /* ── Mood state ── */
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
      document.getElementById('ssmlDebug')&&(document.getElementById('ssmlDebug').textContent=d.ssml);
    }
    if(d.user_name&&d.user_name!==nameLabel.textContent){nameLabel.textContent=d.user_name}
    if(typeof d.child_mode==='boolean'){childMode=d.child_mode}
  }catch(e){}
  setTimeout(pollState,600);
}

/* ─── Activity Sphere ─── */
/* --- Activity Sunburst --- */
let sunburstActive=false;
const sunburstEl=document.getElementById('activitySunburst');
const sbCanvas=document.getElementById('sunburstCanvas');
const sbCtx=sbCanvas?sbCanvas.getContext('2d'):null;

// Pastel ActivityWatch-style categories
const ACTIVITY_COLORS={
  walking:   {fill:'#d4e7d4',stroke:'#a3c9a3'},
  cycling:   {fill:'#b8d4e8',stroke:'#8abbd8'},
  driving:   {fill:'#f5d0c0',stroke:'#e8b09a'},
  running:   {fill:'#d8d0f0',stroke:'#c0b0e0'},
  swimming:  {fill:'#b8e0e8',stroke:'#90cdd8'},
  unknown:   {fill:'#e8e0e8',stroke:'#ccc0cc'}
};
const DEFAULT_CATEGORY='unknown';

function drawSunburst(distances){
  if(!sunburstActive||!sbCtx){sunburstEl.style.display='none';return}
  sunburstEl.style.display='block';
  const W=130,H=130,cx=65,cy=65,outerR=58,innerR=22;
  sbCtx.clearRect(0,0,W,H);

  const total=Object.values(distances).reduce((a,b)=>a+b,0);
  if(total===0){sunburstEl.style.display='none';return}

  let startAngle=-Math.PI/2;
  const entries=Object.entries(distances).filter(([,v])=>v>0);
  if(!entries.length){sunburstEl.style.display='none';return}

  // outer ring
  for(const[cat,dist]of entries){
    const pct=dist/total;
    const sweep=pct*Math.PI*2;
    const c=ACTIVITY_COLORS[cat]||ACTIVITY_COLORS.unknown;
    sbCtx.beginPath();
    sbCtx.moveTo(cx,cy);
    sbCtx.arc(cx,cy,outerR,startAngle,startAngle+sweep);
    sbCtx.closePath();
    sbCtx.fillStyle=c.fill;
    sbCtx.strokeStyle=c.stroke;
    sbCtx.lineWidth=1.5;
    sbCtx.fill();
    sbCtx.stroke();

    // label
    if(pct>0.08){
      const mid=startAngle+sweep/2;
      const lx=cx+outerR*0.63*Math.cos(mid);
      const ly=cy+outerR*0.63*Math.sin(mid);
      sbCtx.fillStyle='rgba(80,70,90,0.7)';
      sbCtx.font='bold 9px sans-serif';
      sbCtx.textAlign='center';
      sbCtx.textBaseline='middle';
      sbCtx.fillText(cat,lx,ly);
    }

    startAngle+=sweep;
  }

  // inner ring — show current activity type
  const top=entries.reduce((a,b)=>a[1]>b[1]?a:b);
  const topCat=top[0];
  const innerSweep=Math.PI*2*0.6;
  const innerStart=-Math.PI/2-innerSweep/2+Math.sin(Date.now()/2000)*0.1;
  const c=ACTIVITY_COLORS[topCat]||ACTIVITY_COLORS.unknown;
  sbCtx.beginPath();
  sbCtx.moveTo(cx,cy);
  sbCtx.arc(cx,cy,innerR,innerStart,innerStart+innerSweep);
  sbCtx.closePath();
  sbCtx.fillStyle=c.fill;
  sbCtx.fill();

  // center number
  const topDist=top[1];
  sbCtx.fillStyle='rgba(80,70,90,0.8)';
  sbCtx.font='bold 16px sans-serif';
  sbCtx.textAlign='center';
  sbCtx.textBaseline='middle';
  sbCtx.fillText(topDist.toFixed(1)+'km',cx,cy-2);
  sbCtx.font='8px sans-serif';
  sbCtx.fillStyle='rgba(80,70,90,0.5)';
  sbCtx.fillText(topCat,cx,cy+14);
}

async function pollActivity(){
  try{
    const r=await fetch('/api/activity'),d=await r.json();
    const wasActive=sunburstActive;
    sunburstActive=d.active;
    if(d.active){
      // build category distances from track data if available
      const distances={walking:0,cycling:0,driving:0,running:0,swimming:0};
      const cat=d.type||'unknown';
      if(cat in distances) distances[cat]=d.dist_km||0;
      else distances.unknown=d.dist_km||0;
      drawSunburst(distances);
    }else{
      sunburstEl.style.display='none';
    }
  }catch(e){sunburstEl.style.display='none'}
  setTimeout(pollActivity,2000);
}

setTimeout(pollActivity,1000);
</script>
</body>
</html>"""

@app.get("/", response_class=HTMLResponse)
async def serve_ui():
    return HTML_PAGE

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT)
