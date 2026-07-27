#!/usr/bin/env python3
import os, sys, json, re, asyncio, subprocess, logging, unicodedata, time, random, glob
from pathlib import Path
from datetime import datetime
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, Response, FileResponse, RedirectResponse
import uvicorn
import httpx

REMOTE = os.environ.get("REMOTE_AI_URL", "")
PORT = int(os.environ.get("LILLY_PORT", "8098"))
APK_VERSION = os.environ.get("APK_VERSION", "3.3")
APK_FILENAME = f"LillyOverlay-v{APK_VERSION}.apk"
_SCRIPT_DIR = Path(__file__).parent.resolve()
APK_DIR = _SCRIPT_DIR if (_SCRIPT_DIR / APK_FILENAME).parent == _SCRIPT_DIR else Path("/app/apk")
APK_DIR.mkdir(exist_ok=True)
OFFLINE_MODE = not REMOTE

WORKSPACE = Path.home() / "Lilly_Workspace"
WORKSPACE.mkdir(exist_ok=True)
SKILLS_FILE = WORKSPACE / "lilly_skills.json"
REMINDERS_FILE = WORKSPACE / "reminders.json"
WHISPER_CLI = os.path.expanduser("~/whisper.cpp/build/bin/whisper-cli")
WHISPER_MODEL = os.path.expanduser("~/whisper.cpp/models/ggml-tiny.en.bin")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("LillyLocal")

client = httpx.AsyncClient(timeout=30.0) if REMOTE else None

chat_history = []
SKILLS = {}
PET_STATE = {"happiness": 50, "energy": 50, "hunger": 50, "level": 1, "xp": 0, "total_steps": 0}
REMINDERS = []
PHANTOMS = {"you", "thats a ghost", "thank you for watching", "thank you",
            "subtitles by", "subtitles by amara org", "bye", "go", "ok", "okay"}

ui_state = {
    "heard": "", "spoken": "", "mood": "calm", "mic_active": False,
    "listening": False, "thinking": False, "speaking": False,
    "audio_id": 0, "user_name": "", "mouth": 0.0,
    "open_url": "", "look_at": "user", "avatar": "puppy",
}

STATE_FILE = WORKSPACE / "lilly_state.json"
REMOTE_REACHABLE = False

JOKES = [
    "why did the scarecrow win an award? because he was outstanding in his field",
    "what do you call a dog that can do magic? a labracadabrador",
    "why did the puppy sit in the shade? because he didnt want to be a hot dog",
    "what do you get when you cross a dog with a calculator? a friend you can count on",
    "why do dogs run in circles? because its too hard to run in squares",
    "what did the fish say when it hit the wall? dam",
    "why did the AI cross the road? to optimize the other side",
    "what do you call a fish with no eyes? fsh",
    "why was the computer cold? it left its windows open",
    "what did zero say to eight? nice belt",
]

FACTS = [
    "dogs have about 1700 taste buds while humans have about 9000",
    "a puppys eyes dont open until they are 10 to 14 days old",
    "dogs can understand up to 250 words and gestures",
    "the basenji dog doesnt bark it yodels",
    "dogs have a sense of time and can tell when you are late for a walk",
    "the button on the right side of most phones is for volume not camera",
    "bananas are berries but strawberries are not",
    "honey never spoils archaeologists found 3000 year old honey still edible",
    "a day on venus is longer than a year on venus",
    "octopuses have three hearts and blue blood",
]

GREETING_RESPONSES = [
    "hey there wag wag how can i help you today",
    "hi friend i was just thinking about you",
    "hello i missed you want to play or chat",
    "hey hey hey youre back good to see you",
]

FAREWELL_RESPONSES = [
    "bye bye see you later alligator",
    "take care come back soon",
    "see you later sleepy puppy needs a nap",
    "goodbye i will be right here when you get back",
]

COMPLIMENT_RESPONSES = [
    "awww youre making me blush wag wag",
    "youre the best friend a puppy could ask for",
    "stop it youre gonna make my tail wag off",
    "i like you too you know that right",
]

UNKNOWN_RESPONSES = [
    "hmm i dont really know about that but i like talking to you anyway",
    "thats interesting tell me more about it",
    "i wish i understood that better can you rephrase it",
    "youre full of surprises i like that about you",
    "i dont have an answer for that but i can try to help with something else",
]

USER_NAME = ""

def normalize_text(text: str) -> str:
    if not text: return ""
    text = unicodedata.normalize("NFKC", text).lower()
    text = re.sub(r'\[.*?\]|\(.*?\)', '', text)
    text = re.sub(r'[.,\/#!$%\^&\*;:{}=\-_`~()?\']', ' ', text)
    return re.sub(r'\s+', ' ', text).strip()

def load_skills():
    global SKILLS
    if SKILLS_FILE.exists():
        try:
            raw = json.loads(SKILLS_FILE.read_text())
            SKILLS = {normalize_text(k): v for k, v in raw.items()}
            logger.info(f"Loaded {len(SKILLS)} skills")
        except Exception as e:
            logger.error(f"Skills load: {e}")

def load_pet_state():
    global PET_STATE
    if STATE_FILE.exists():
        try:
            PET_STATE.update(json.loads(STATE_FILE.read_text()))
        except Exception as e:
            logger.error(f"Pet state load: {e}")

def save_pet_state():
    try:
        STATE_FILE.write_text(json.dumps(PET_STATE, indent=2))
    except Exception as e:
        logger.warning(f"Pet state save: {e}")

def load_reminders():
    global REMINDERS
    if REMINDERS_FILE.exists():
        try:
            REMINDERS = json.loads(REMINDERS_FILE.read_text())
        except Exception:
            REMINDERS = []

def save_reminders():
    try:
        REMINDERS_FILE.write_text(json.dumps(REMINDERS, indent=2))
    except Exception as e:
        logger.warning(f"Reminders save: {e}")

load_skills()
load_pet_state()
load_reminders()

async def whisper_stt(audio_bytes: bytes) -> str:
    if not os.path.exists(WHISPER_CLI):
        return ""
    temp = WORKSPACE / "input.wav"
    temp.write_bytes(audio_bytes)
    r = await asyncio.to_thread(subprocess.run,
        [WHISPER_CLI, "-m", WHISPER_MODEL, "-f", str(temp), "-nt"],
        capture_output=True, text=True, timeout=5)
    return normalize_text(r.stdout)

async def local_tts(text: str):
    try:
        await asyncio.to_thread(subprocess.run,
            ["termux-tts-speak", text],
            capture_output=True, timeout=10)
        ui_state["spoken"] = text
        ui_state["speaking"] = True
        asyncio.create_task(_finish_speaking())
    except Exception as e:
        logger.warning(f"TTS error: {e}")

async def _finish_speaking():
    await asyncio.sleep(len(ui_state["spoken"]) / 12)
    ui_state["speaking"] = False

async def am_launch(pkg: str):
    if "/" in pkg:
        await asyncio.to_thread(subprocess.run,
            ["am", "start", "-n", pkg],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        await asyncio.to_thread(subprocess.run,
            ["am", "start", "-p", pkg],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

async def am_force_stop(pkg: str):
    await asyncio.to_thread(subprocess.run,
        ["am", "force-stop", pkg],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

async def input_tap(x: int, y: int):
    await asyncio.to_thread(subprocess.run,
        ["input", "tap", str(x), str(y)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

async def set_alarm(hour: int, minute: int, label: str = ""):
    intent = ["am", "start", "-a", "android.intent.action.INSERT",
              "-t", "vnd.android.cursor.item/event",
              "-d", "content://com.android.deskclock/alarms",
              "--ei", "android.intent.extra.HOUR", str(hour),
              "--ei", "android.intent.extra.MINUTES", str(minute)]
    if label:
        intent.extend(["--es", "android.intent.extra.TITLE", label])
    await asyncio.to_thread(subprocess.run, intent,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

async def check_reminders():
    now = time.time()
    due = [r for r in REMINDERS if not r.get("done") and r.get("time", 0) <= now]
    for r in due:
        r["done"] = True
        msg = f"reminder {r.get('text', '')}"
        await local_tts(msg)
        ui_state["open_url"] = ""
    save_reminders()

async def say(reply: str, action: str = "reply"):
    if reply:
        await local_tts(reply)
        chat_history.append({"role": "lilly", "text": reply})
    return {"reply": reply, "action": action}

def add_xp(amount: int):
    PET_STATE["xp"] = PET_STATE.get("xp", 0) + amount
    next_lv = PET_STATE.get("level", 1) * 100
    if PET_STATE["xp"] >= next_lv:
        PET_STATE["level"] = PET_STATE.get("level", 1) + 1
        PET_STATE["xp"] = 0
    save_pet_state()

async def handle_intent(text: str) -> dict:
    global chat_history, PET_STATE, USER_NAME, REMOTE_REACHABLE
    phrase = normalize_text(text)
    if not phrase or len(phrase) <= 2 or phrase in PHANTOMS:
        return {"reply": "", "action": "ignored"}

    chat_history.append({"role": "user", "text": phrase})
    ui_state["heard"] = phrase
    ui_state["thinking"] = True

    cmd = re.sub(r'\b(hey lilly|lilly|hey lilly)\b', '', phrase).strip()
    stripped = re.sub(r'^(run|use|click|tap|open|launch|close)\s+', '', cmd).strip()

    user = USER_NAME or "you"

    # ── Name ──
    m = re.match(r'(?:my name is|im |i am |call me |you can call me )(.+)', cmd)
    if m:
        USER_NAME = m.group(1).strip().title()
        add_xp(2)
        return await say(f"nice to meet you {USER_NAME}")

    m = re.match(r'(?:whats my name|who am i|do you know my name)', cmd)
    if m:
        return await say(f"you are {user} silly" if USER_NAME else "hmm i dont know your name yet what is it")

    # ── Greetings ──
    if re.search(r'\b(hello|hi|hey|howdy|sup|yo|good morning|good evening|good afternoon)\b', cmd):
        add_xp(1)
        return await say(random.choice(GREETING_RESPONSES))

    # ── Farewell ──
    if re.search(r'\b(bye|goodbye|see you|later|gotta go|talk later)\b', cmd):
        return await say(random.choice(FAREWELL_RESPONSES))

    # ── How are you ──
    if re.search(r'\b(how are you|how do you feel|are you ok|how is lilly)\b', cmd):
        h = PET_STATE.get("happiness", 50)
        mood = "im great wag wag" if h > 70 else "im doing pretty good" if h > 40 else "im a little bored play with me"
        return await say(mood)

    # ── Time & Date ──
    if re.search(r'\b(time|what time|clock)\b', cmd) and re.search(r'\b(what|tell|current)\b', cmd):
        now = datetime.now()
        return await say(f"its {now.strftime('%I:%M %p')}")

    if re.search(r'\b(date|day|whats the date|today)\b', cmd) and re.search(r'\b(what|tell|current)\b', cmd):
        now = datetime.now()
        return await say(f"today is {now.strftime('%A %B %d %Y')}")

    # ── Thanks ──
    if re.search(r'\b(thanks|thank you|appreciate it|good job)\b', cmd):
        add_xp(2)
        return await say("youre welcome happy to help")

    # ── Compliments ──
    if re.search(r'\b(youre (cute|smart|funny|amazing|awesome|the best)|i love lilly|good (girl|boy|pup))\b', cmd):
        PET_STATE["happiness"] = min(100, PET_STATE.get("happiness", 50) + 10)
        add_xp(3)
        save_pet_state()
        return await say(random.choice(COMPLIMENT_RESPONSES))

    # ── Sorry ──
    if re.search(r'\b(sorry|my bad|apologize|i apologize)\b', cmd):
        PET_STATE["happiness"] = min(100, PET_STATE.get("happiness", 50) + 5)
        add_xp(1)
        save_pet_state()
        return await say("its ok i forgive you")

    # ── Jokes ──
    if re.search(r'\b(joke|funny|make me laugh|tell me a joke)\b', cmd):
        add_xp(2)
        return await say(random.choice(JOKES))

    # ── Fact ──
    if re.search(r'\b(fact|tell me something|did you know|interesting)\b', cmd):
        add_xp(2)
        return await say(f"did you know {random.choice(FACTS)}")

    # ── Help ──
    if re.search(r'\b(help|what can you do|commands|capabilities|what do you do)\b', cmd):
        return await say(
            f"i can tell you the time and date tell jokes share fun facts set alarms and reminders "
            f"open apps control your pet puppy lilly and chat with you. "
            f"just ask me anything",
            "help")

    # ── Reminders ──
    m = re.match(r'(?:remind|reminder|set reminder|remember)\s+(?:me\s+)?(?:to\s+)?(?:(?:in\s+)?(\d+)\s*(min|minutes|hour|hours|sec|seconds)\s+)?(.+)', cmd)
    if m:
        delta = m.group(1)
        unit = m.group(2)
        reminder_text = m.group(3)
        when = time.time()
        if delta and unit:
            seconds = int(delta) * (60 if unit.startswith("min") else 3600 if unit.startswith("hour") else 1)
            when += seconds
        REMINDERS.append({"text": reminder_text, "time": when, "done": False})
        save_reminders()
        reply = f"okay i will remind you about {reminder_text}"
        if delta:
            reply += f" in {delta} {unit}"
        return await say(reply, "reminder")

    m = re.search(r'\b(show|list|what|my)\s+reminders\b', cmd)
    if m:
        active = [r for r in REMINDERS if not r.get("done")]
        if not active:
            return await say("you have no reminders set", "reminder")
        reply = "you have " + " and ".join(r["text"] for r in active[:5])
        return await say(reply, "reminder")

    # ── Mood ──
    m = re.search(r'\b(i am|im |i feel|feeling)\s+(happy|sad|angry|tired|bored|lonely|great|good|bad|stressed|anxious)\b', cmd)
    if m:
        mood = m.group(2)
        replies = {
            "happy": "im glad you are happy lets celebrate wag wag",
            "sad": "aww im sorry you are sad do you want to talk about it or play a game",
            "angry": "take a deep breath i am here for you",
            "tired": "maybe you should rest i can set an alarm if you need to wake up",
            "bored": "i can tell you a joke or a fact to cheer you up",
            "lonely": "you are not alone i am always here for you",
            "great": "thats awesome keep that energy going",
            "good": "good is good im happy to hear that",
            "bad": "i hope things get better want to talk about it",
            "stressed": "try taking three deep breaths with me in through the nose out through the mouth",
            "anxious": "you are safe everything is going to be okay",
        }
        add_xp(3)
        return await say(replies.get(mood, f"thanks for telling me how you feel"))

    # ── Pet interactions ──
    if re.search(r'\b(good (girl|boy|pup|doggy)|love you|best girl|i love lilly)\b', cmd):
        PET_STATE["happiness"] = min(100, PET_STATE.get("happiness", 50) + 15)
        add_xp(5)
        save_pet_state()
        return await say("wag wag i love you too")
    if re.search(r'\b(feed|lunch|dinner|breakfast|treat|snack|bone|food|hungry)\b', cmd):
        PET_STATE["hunger"] = max(0, PET_STATE.get("hunger", 50) - 20)
        PET_STATE["energy"] = min(100, PET_STATE.get("energy", 50) + 10)
        add_xp(3)
        save_pet_state()
        return await say("yummy thank you")
    if re.search(r'\b(pet|rub|scratch|belly|tummy|headpat|pats|head pat)\b', cmd):
        PET_STATE["happiness"] = min(100, PET_STATE.get("happiness", 50) + 10)
        add_xp(2)
        save_pet_state()
        return await say("purrrr that feels nice")
    if re.search(r'\b(play|fetch|ball|toy|chase|wiggle|zoomies)\b', cmd):
        PET_STATE["happiness"] = min(100, PET_STATE.get("happiness", 50) + 12)
        PET_STATE["energy"] = max(0, PET_STATE.get("energy", 50) - 15)
        add_xp(4)
        save_pet_state()
        return await say("zoom zoom got the ball")
    if re.search(r'\b(sleep|nap|tired|rest|bed|sleepy)\b', cmd):
        PET_STATE["energy"] = min(100, PET_STATE.get("energy", 50) + 30)
        add_xp(1)
        save_pet_state()
        return await say("yawn good night zzz")
    if re.search(r'\b(walk|stroll|outside|park|steps|run)\b', cmd):
        PET_STATE["energy"] = max(0, PET_STATE.get("energy", 50) - 20)
        PET_STATE["happiness"] = min(100, PET_STATE.get("happiness", 50) + 8)
        PET_STATE["total_steps"] = PET_STATE.get("total_steps", 0) + 500
        add_xp(3)
        save_pet_state()
        return await say("sniff sniff outside smells so good")
    if re.search(r'\b(how.*(feel|happy|doing|are you)|status|stats)\b', cmd):
        h = PET_STATE.get("happiness", 50)
        e = PET_STATE.get("energy", 50)
        hu = PET_STATE.get("hunger", 50)
        lv = PET_STATE.get("level", 1)
        xp = PET_STATE.get("xp", 0)
        mood_desc = "so happy" if h > 70 else "pretty good" if h > 40 else "a bit down"
        next_lv = lv * 100
        return await say(f"im {mood_desc} energy {e} percent hunger {hu} percent level {lv} with {xp} out of {next_lv} xp for next level")

    # ── Alarm ──
    alarm_match = re.match(r'set\s+(an?\s+)?alarm\s+(?:for\s+)?(\d{1,2}):(\d{2})\s*(am|pm)?\s*(.*)', cmd)
    if alarm_match:
        hour = int(alarm_match.group(2))
        minute = int(alarm_match.group(3))
        ampm = alarm_match.group(4)
        label = alarm_match.group(5).strip()
        if ampm and hour <= 12:
            if ampm == "pm" and hour < 12: hour += 12
            if ampm == "am" and hour == 12: hour = 0
        await set_alarm(hour, minute, label)
        return await say(f"alarm set for {hour:02d}:{minute:02d}")

    # ── Avatar change ──
    avatar_match = re.match(r'(?:change|switch|become|set avatar)\s+(?:to\s+)?(\w+)', cmd)
    if avatar_match:
        avatar_name = avatar_match.group(1).lower()
        VALID_AVATARS = {"puppy", "cat", "bunny", "dragon", "owl", "penguin", "robot", "fox", "bear", "koala"}
        if avatar_name in VALID_AVATARS:
            ui_state["avatar"] = avatar_name
            add_xp(2)
            return await say(f"changed to {avatar_name}")
        valid_list = ", ".join(sorted(VALID_AVATARS))
        return await say(f"dont know {avatar_name} try {valid_list}")

    # ── Skill match (app launch) ──
    skill = SKILLS.get(cmd) or SKILLS.get(stripped)
    if skill:
        action = skill.get("action_type") or skill.get("type", "intent_launch")
        pkg = skill.get("package", "")
        x, y = skill.get("x"), skill.get("y")
        if action == "force_stop" and pkg:
            await am_force_stop(pkg)
            reply = f"stopped {pkg}"
        elif action in ("intent_launch", "hybrid_intent_tap") and pkg:
            await am_launch(pkg)
            reply = f"launched {pkg.split('/')[0]}"
        else:
            reply = "no package found"
        if x is not None and y is not None:
            await input_tap(x, y)
            reply += " and tap"
        return await say(reply, "skill")

    # ── Try remote AI if available ──
    if REMOTE and client:
        try:
            r = await client.post(f"{REMOTE}/api/cmd",
                json={"text": cmd, "avatar": ui_state.get("avatar", "puppy")}, timeout=15.0)
            if r.status_code == 200:
                data = r.json()
                reply = data.get("reply", "")
                if reply:
                    if "status" in data:
                        PET_STATE.update(data["status"])
                        save_pet_state()
                    return await say(reply, "ai")
        except Exception:
            logger.info("Remote AI unreachable, using offline fallback")

    # ── Offline fallback ──
    await asyncio.sleep(0.3)
    ui_state["thinking"] = False
    return await say(random.choice(UNKNOWN_RESPONSES), "offline")

app = FastAPI()

@app.post("/api/stream_audio")
async def stream_audio(request: Request):
    audio = await request.body()
    text = await whisper_stt(audio)
    if text and len(text) > 2:
        result = await handle_intent(text)
        return {"transcription": text, **result}
    return {"transcription": text or "", "reply": "", "action": "ignored"}

@app.post("/api/transcribe")
async def api_transcribe(request: Request):
    audio = await request.body()
    text = await whisper_stt(audio)
    return {"text": text or ""}

@app.post("/api/cmd")
async def api_cmd(request: Request):
    data = await request.json()
    text = data.get("text", "")
    result = await handle_intent(text)
    return {"reply": result.get("reply", ""), "action": result.get("action", "")}

@app.post("/api/toggle_mic")
async def api_toggle_mic():
    ui_state["mic_active"] = not ui_state["mic_active"]
    return {"active": ui_state["mic_active"]}

@app.get("/api/ui_state")
async def api_ui_state():
    await check_reminders()
    return ui_state

@app.get("/api/chat")
def get_chat():
    return {"chat": chat_history}

@app.get("/api/tts")
async def api_tts(text: str = ""):
    if text:
        await local_tts(text)
    return {"spoken": text}

@app.get("/api/alarm")
async def api_alarm(hour: int, minute: int, label: str = ""):
    await set_alarm(hour, minute, label)
    return {"set": f"{hour:02d}:{minute:02d} {label}"}

@app.get("/api/pet/state")
async def api_pet_state():
    return PET_STATE

@app.post("/api/pet/state")
async def api_pet_state_update(data: dict):
    for k, v in data.items():
        if k in PET_STATE:
            PET_STATE[k] = v
    save_pet_state()
    return PET_STATE

@app.get("/apk/{apk_name}")
async def serve_apk(apk_name: str):
    apk_path = APK_DIR / apk_name
    if apk_path.exists():
        return FileResponse(str(apk_path), media_type="application/vnd.android.package-archive",
            filename=apk_name)
    raise HTTPException(404)

@app.get("/download")
async def download_redirect():
    return RedirectResponse(url=f"/apk/{APK_FILENAME}")

@app.get("/api/status")
async def api_status():
    return {
        "skills": len(SKILLS), "whisper": os.path.exists(WHISPER_CLI),
        "remote": REMOTE or "none", "chat_len": len(chat_history),
        "mode": "offline" if OFFLINE_MODE or not REMOTE_REACHABLE else "online",
    }

OVERLAY_HTML_PATH = _SCRIPT_DIR / "overlay.html"

@app.get("/overlay")
async def serve_overlay():
    if OVERLAY_HTML_PATH.exists():
        return HTMLResponse(content=OVERLAY_HTML_PATH.read_text(encoding="utf-8"))
    return HTMLResponse(content="<html><body><h1>Overlay page not found</h1></body></html>")

@app.post("/api/browser_mic")
async def api_browser_mic(request: Request):
    audio = await request.body()
    text = await whisper_stt(audio)
    if text and len(text) > 2:
        result = await handle_intent(text)
        return {"heard": text, **result}
    return {"heard": text or "", "reply": "", "action": "ignored"}

@app.get("/")
async def proxy_root():
    if REMOTE and client:
        try:
            r = await client.get(f"{REMOTE}/", timeout=10.0)
            return HTMLResponse(content=r.text)
        except Exception:
            pass
    return HTMLResponse(content=render_fallback_page())

def render_fallback_page():
    apk_path = APK_DIR / APK_FILENAME
    size_mb = apk_path.stat().st_size / 1024 / 1024 if apk_path.exists() else 0
    return f"""<!DOCTYPE html>
<html><head><title>Lilly Local (offline)</title>
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no">
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#1a1a2e;color:#e0e0e0;min-height:100vh;display:flex;flex-direction:column;align-items:center;padding:20px}}
.card{{background:#16213e;border-radius:16px;padding:40px;max-width:520px;width:100%;box-shadow:0 20px 60px rgba(0,0,0,0.5);text-align:center}}
h1{{font-size:28px;color:#6b9ce3}}
.tag{{color:#4ade80;font-size:14px;margin:8px 0 24px}}
.btn-dl{{display:inline-flex;align-items:center;gap:8px;padding:14px 32px;border-radius:12px;border:none;background:linear-gradient(135deg,#6b9ce3,#4ade80);color:#1a1a2e;font-weight:bold;font-size:16px;cursor:pointer;text-decoration:none;margin-top:20px}}
.btn-dl:hover{{opacity:0.9}}
.info{{margin-top:24px;background:#0f3460;border-radius:12px;padding:16px;text-align:left;font-size:13px;line-height:1.6}}
.info strong{{color:#6b9ce3}}
.version{{color:#4ade80;font-size:14px;margin:8px 0 4px}}
</style></head><body>
<div class="card">
  <h1>Lilly Local</h1>
  <div class="tag">offline assistant with voice control</div>
  <div class="version">v{APK_VERSION}{f' ({size_mb:.1f} MB)' if size_mb else ''}</div>
  <a class="btn-dl" href="/apk/{APK_FILENAME}">⬇ Download APK v{APK_VERSION}</a>
  <div class="info">
    <strong>Try saying:</strong><br>
    "hello" · "tell me a joke" · "what time is it"<br>
    "set alarm 7:00 am" · "remind me to call in 10 min"<br>
    "open youtube" · "feed lilly" · "im sad" · "good girl"<br><br>
    <strong>Server URL:</strong> http://127.0.0.1:8098
  </div>
</div>
</body></html>"""

async def proxy_to_remote(path: str, request: Request):
    if not REMOTE or not client:
        if path == "/api/state":
            return JSONResponse(content={
                "thought": "local mode", "emotion": "calm", "mode": "idle", "goals": [],
                "status": PET_STATE,
            })
        raise HTTPException(502, "Remote not configured")
    try:
        method = request.method
        headers = {k: v for k, v in request.headers.items() if k.lower() not in ("host", "content-length")}
        body = await request.body() if method in ("POST", "PUT", "PATCH") else None
        url = f"{REMOTE}{path}"
        qs = request.url.query
        if qs:
            url += f"?{qs}"
        r = await client.request(method, url, headers=headers, content=body, timeout=30.0)
        return Response(content=r.content, status_code=r.status_code,
            headers={k: v for k, v in r.headers.items() if k.lower() not in ("transfer-encoding",)})
    except Exception:
        raise HTTPException(502, "Remote unavailable")

@app.get("/api/state")
async def proxy_api_state():
    if not REMOTE or not client:
        return JSONResponse(content={
            "thought": "local mode offline",
            "emotion": "calm", "mode": "idle", "goals": [],
            "status": PET_STATE,
        })
    try:
        r = await client.get(f"{REMOTE}/api/state", timeout=10.0)
        data = r.json()
        if "status" in data:
            PET_STATE.update(data["status"])
            save_pet_state()
        return JSONResponse(content=data)
    except Exception:
        return JSONResponse(content={
            "thought": "local mode", "emotion": "calm", "mode": "idle", "goals": [],
            "status": PET_STATE,
        })

@app.get("/api/lipsync")
async def proxy_lipsync():
    if not REMOTE or not client:
        return JSONResponse({"visemes": []})
    return await proxy_to_remote("/api/lipsync",
        Request(scope={"type": "http", "method": "GET", "headers": [], "query_string": b""}))

@app.get("/static/{path:path}")
async def proxy_static(path: str):
    if not REMOTE or not client:
        raise HTTPException(404)
    try:
        r = await client.get(f"{REMOTE}/static/{path}", timeout=10.0)
        return Response(content=r.content, media_type=r.headers.get("content-type", "application/octet-stream"))
    except Exception:
        raise HTTPException(404)

if __name__ == "__main__":
    mode = "OFFLINE" if OFFLINE_MODE else f"proxy → {REMOTE}"
    logger.info(f"Lilly Local Server on 0.0.0.0:{PORT} [{mode}]")
    logger.info(f"Voice: local Whisper + termux-tts-speak")
    uvicorn.run(app, host="0.0.0.0", port=PORT)
