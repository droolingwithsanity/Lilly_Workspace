# Lilly Pup — Playbook

A curious digital companion who lives on your phone, senses the world through 23 Android sensors, reads your notifications, and helps you get things done — all through voice or text.

---

## Quick Start (New User — No SSH Required)

Lilly no longer needs SSH. Everything runs over HTTP from a single Termux server on your phone.

### Phone Setup (Termux)

```bash
# 1. Install Termux from F-Droid (not Play Store)
pkg install termux-api python -y
pip install fastapi uvicorn httpx

# 2. Copy termux_sensor_server.py to your phone

# 3. Start the sensor server
python termux_sensor_server.py --port 8099
```

### Avatar Server

```bash
# Clone + install
pip install -r requirements.txt

# Point to your phone (or localhost if on same device)
export SENSOR_SERVER_URL=http://<phone-ip>:8099

# Start
python lilly_ai.py
```

That's it. No SSH keys, no `sshd`, no `TERMUX_SSH_HOST`. The sensor server handles sensors, notifications, shell commands, and app launching over plain HTTP.

**If you also want voice (microphone):** Set up Termux:SSH for background mic streaming. Everything else works without it.

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                       DOCKER / PC                               │
│                                                                 │
│  lilly_ai.py (:8098)                                            │
│   ├── FastAPI server (all AI, routes, memory)                   │
│   ├── llama.cpp / Ollama backend                                │
│   ├── Piper TTS (12 voice profiles)                             │
│   ├── 9 avatar personas (hive mind team)                        │
│   └── Notification priority engine                              │
│                                                                 │
│  open-connector (:3002)                                         │
│   └── Gmail / Outlook / Calendar API (optional OAuth)           │
└──────────────┬──────────────────────────────────────────────────┘
               │ HTTP (no SSH needed)
┌──────────────▼──────────────────────────────────────────────────┐
│                    ANDROID PHONE                                │
│                                                                 │
│  termux_sensor_server.py (:8099)                                │
│   ├── /sensors/all        23 sensors (light, accel, gyro, etc) │
│   ├── /notification/list  Android notifications (OS priority)  │
│   ├── /shell              Run any shell command                  │
│   ├── /battery            Battery status                        │
│   ├── /location           GPS location                          │
│   └── /bluetooth/scan     Bluetooth device discovery            │
│                                                                 │
│  Termux:API                                                     │
│   ├── termux-sensor       Raw sensor access                     │
│   ├── termux-notification Read/listen to notifications          │
│   ├── termux-open         Open URLs / apps                      │
│   └── termux-bluetooth    Bluetooth scanning                    │
└─────────────────────────────────────────────────────────────────┘
```

---

## The 9 Avatars (Hive Mind)

| Avatar | Role | Strengths | TTS Voice |
|--------|------|-----------|-----------|
| 🐶 **Puppy** (Lilly) | Alpha Companion | Conversation, memory, emotional intelligence | Normal pace, warm |
| 🦊 **Fox** | Creative Strategist | Creative writing, storytelling, brainstorming | Fast, high-pitch, very animated |
| 🐱 **Cat** | Precision Analyst | Data analysis, code review, fact-checking | Normal, flat, crisp |
| 🐻 **Bear** | Steadfast Guardian | Scheduling, reminders, practical advice | Slow, deep, calm |
| 🐰 **Bunny** | Energetic Scout | Real-time monitoring, alerts, notifications | Very fast, high-pitch, animated |
| 🦉 **Owl** | Wisdom Keeper | Deep analysis, long-term planning | Very slow, low-pitch, flat |
| 🦌 **Deer** | Gentle Healer | Emotional support, wellness, meditation | Normal, warm-breathy |
| 🐺 **Wolf** | Fierce Protector | Security, threat assessment, decisive action | Fast, low-pitch, mid-animated |
| 🦝 **Raccoon** | Tech Tinkerer | Coding, hacking, gadgets, troubleshooting | Fast, medium-pitch, animated |

All 9 share the same body (phone) and sensors but have distinct personalities. They can defer tasks to each other.

---

## Voice Commands

### Apps (with PiP mode)

| Say | Action |
|-----|--------|
| "open youtube" / "watch..." | Opens YouTube in Picture-in-Picture |
| "open youtube in pip" | Same — YouTube always opens in PiP |
| "open camera" | Opens camera app |
| "open maps" / "navigate to..." | Google Maps |
| "open gmail" / "check email" | Gmail |
| "open music" / "play music" | YouTube Music |
| "open settings" / "calculator" / "calendar" | Respective apps |
| "google..." / "search for..." | Web search |
| "skip ad" | Taps skip button coordinates |

### Phone Navigation

| Say | Action |
|-----|--------|
| "go home" / "home" | Home screen |
| "go back" / "back" | Back button |
| "recent apps" / "switch apps" | App switcher |
| "notifications" / "pull down" | Notification panel |
| "quick settings" | Quick settings panel |
| "screenshot" | Screenshot |
| "volume up/down" / "louder/quieter" | Volume control |
| "dark mode" / "light mode" | Theme toggle |
| "power menu" | Power options |

### Notifications

| Say | Action |
|-----|--------|
| "what notifications" / "any alerts" | Lists active notifications by priority |
| "what did I miss" / "what's new" | Same — ordered OS-first, then app priority |
| "check my notifications" | Full notification inbox |
| "dismiss notifications" | Clear seen notifications |
| "read my emails" / "check my gmail" | Gmail inbox (needs OAuth) |

The avatar **proactively alerts** you about new notifications:
- **Tier 1 (OS):** Phone calls, SMS, system alerts
- **Tier 2 (Apps):** By priority (max > high > default > low > min)
- Each alert includes a **next-step suggestion** — "Want me to reply?", "Want me to call them back?"

### Sensors & Environment

| Say | Response |
|-----|----------|
| "what do you sense" | Full sensor narrative |
| "how bright is it" / "light" | Ambient light level |
| "what's the temperature" | Temperature readings |
| "what's the pressure" | Barometer |
| "am I moving" / "accelerometer" | Motion data |
| "compass" / "which way am I facing" | Magnetometer |
| "what's my step count" / "steps" | Pedometer |
| "how much battery" / "charge" | Battery percentage |
| "where are we" | GPS location |
| "what's the weather" | Current weather (pressure trend) |
| "is it raining" | Weather + pressure check |
| "who's near me" / "bluetooth" | Bluetooth device scan |

### Sensor-Triggered Skills

Background rules fire automatically when sensor conditions are met:

| Skill | Trigger | When |
|-------|---------|------|
| Dark Room Alert | Light < 10 lux | "It's gotten really dark..." |
| Phone Pickup | Pickup sensor > 0 | "I felt you pick up the phone!" |
| Vehicle Motion | Significant motion | "Feels like we're moving fast..." |
| Bright Light | Light > 1000 lux | "Wow, it's really bright!" |

Customize via `sensor_skills.json` or `GET/POST/DELETE /api/sensor_skills`.

### Camera Vision & Stop Sign Detection

The overlay camera sends JPEG frames to the backend for real-time analysis:

| Say | Response |
|-----|----------|
| "what do you see" | Describes detected objects (YOLO 80 classes) |
| "what's in front of me" | Camera description with context |
| "is there a stop sign" | Checks latest stop sign detection |

**Pipeline:**
```
Android Camera → JPEG → /api/vision/browser → YOLOv8n + Haar Cascade → detections
```

**Stop Sign Alerts:**
- Haar Cascade classifier (`stop_data.xml`) runs alongside YOLO on every frame
- Android notification fires when stop sign detected (15s cooldown)
- Detections merged into avatar sensor context for natural language responses

**Vision Server:** Port 8095 (Ultralytics YOLOv8n, 80 COCO classes)
**Backend:** Port 8098 (`/api/vision/browser` POST/GET)

### Stories & Imagination

| Say | Response |
|-----|----------|
| "tell me a story" | Real sensor-grounded story |
| "describe your world" | What Lilly senses right now |
| "paint a picture" | Poetic sensor description |
| "weave a tale" | Imaginative narrative using real data |

### Activity Tracking

| Say | Action |
|-----|--------|
| "start a walk" / "go for a walk" | Tracks walk |
| "start a run" | Tracks run |
| "start a bike ride" | Tracks cycling |
| "start a drive" | Tracks driving |
| "stop tracking" / "I'm done" | Saves activity |
| "activity status" | Current speed/distance |

### Teaching & Memory

| Say | What Lilly learns |
|-----|-------------------|
| "this is called [name]" | Saves sensor signature with label |
| "we're at [place]" | Labels current GPS location |
| "name device [MAC] as [name]" | Bluetooth device nickname |

---

## Picture-in-Picture (PiP)

YouTube automatically opens in Picture-in-Picture mode (Android 12+ via `--activity-picture-in-picture`, older versions via home-key simulation). You stay on the avatar UI while watching.

```json
{
  "youtube": {
    "package": "com.google.android.youtube",
    "pip": true
  }
}
```

To add PiP to other skills, set `"pip": true` in `lilly_skills.json`.

---

## Notification Priority System

Notifications are scored and alerted in order:

1. **OS Tier** — Phone calls, SMS, system dialogs
2. **App Priority** — Max > High > Default > Low > Min

Each alert includes a contextual next-step action based on the notification type.

**Proactive context:** When you talk to the avatar, it automatically sees the top 3 pending notifications and may mention them.

---

## Kid Mode (Socratic Learning)

**Toggle:** Say "kid mode on/off" or tap the avatar's nose 3 times.

In kid mode, the avatar never gives direct answers — only guiding questions and hints grounded in real sensor data.

| Kid asks | Avatar responds |
|----------|----------------|
| "What is gravity?" | "Have you ever dropped something and watched it fall? What do you think makes it go down?" |
| "How do birds fly?" | "Have you seen a bird flap its wings? What do you think those wings are doing?" |
| "Why is the sky blue?" | "Have you noticed the sky looks different at sunset? What colors do you see then?" |

---

## API Endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/chat` | POST | Send message, get response |
| `/api/notifications` | GET | Active notification list |
| `/api/sensor_skills` | GET/POST/DELETE | Manage sensor-triggered skills |
| `/api/lilly_skills` | GET | Available skills |
| `/api/sensors` | GET | Latest sensor readings |
| `/api/health` | GET | Service health |
| `/api/toggle_mic` | POST | Enable/disable mic |
| `/api/switch_avatar` | POST | Switch character |
| `/api/phone_status` | GET | Phone connectivity status |
| `/api/google/gmail` | GET | Gmail inbox (with OAuth) |
| `/api/google/calendar` | GET | Calendar events (with OAuth) |

---

## Environment Variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `SENSOR_SERVER_URL` | `http://100.115.234.87:8099` | Phone sensor server |
| `TERMUX_SSH_HOST` | *(empty)* | SSH host (optional — mic only) |
| `TERMUX_SSH_PORT` | `8022` | SSH port |
| `TERMUX_SSH_USER` | *(empty)* | SSH username |
| `OLLAMA_URL` | `http://100.93.131.114:11434` | Ollama backend |
| `PREFER_BACKEND` | `ollama` | `ollama` or `llama` |

---

## Files

| File | Purpose |
|------|---------|
| `lilly_ai.py` | Main application (12,500+ lines) |
| `termux_sensor_server.py` | Phone sensor server (HTTP) |
| `lilly_skills.json` | 35+ Android skill definitions |
| `sensor_skills.json` | 5 sensor-triggered reactive skills |
| `email_integration.py` | Gmail/Outlook/Calendar |
| `auth0_auth.py` | Auth0 OIDC |
| `requirements.txt` | Python dependencies |
| `Dockerfile` | Multi-stage Docker build |
| `docker-compose.yml` | Docker Compose config |

---

## Troubleshooting

### "No sensor data"
Check the sensor server is running: `curl http://<phone-ip>:8099/health`

### "App won't launch" / "Commands don't work"
This is almost always a **Termux permission** issue. The overlay app needs the **`com.termux.permission.RUN_COMMAND`** permission to tell Termux to open apps and run shell commands.

**Fix:**
1. Open Android **Settings → Apps → Termux → Permissions**
2. Find **"Run command"** / **"Termux Run Command"** and set it to **Allow**
3. In Termux, enable external apps:
   ```bash
   mkdir -p ~/.termux
   grep -qx 'allow-external-apps=true' ~/.termux/termux.properties || echo 'allow-external-apps=true' >> ~/.termux/termux.properties
   ```
4. Fully close Termux (swipe it away) and reopen it
5. In the overlay app, tap the **"Enable Termux Shell Tools"** button to verify

After this, commands like "Open YouTube", "Go home", and "Take a screenshot" will work.

### "No notifications"
Ensure `termux-notification-list` is installed and Notification Listener permission is granted:
```
pkg install termux-api
# Then enable in Android Settings > Apps > Termux > Notification Listener
```

### "Mic not working"
Background mic requires SSH. Use the browser's built-in mic (click the mic button in the UI) as a no-SSH alternative.

### "TTS not speaking"
Check Piper voices exist at `lillyos/voices/*.onnx`. Run `pip install piper-tts` if missing.

---

## Moods

The avatar's current mood shows in the UI badge:
- **calm** — default
- **curious** — asking questions
- **cheerful** — happy/excited
- **excited** — very enthusiastic
- **gentle** — soft/careful
- **worried** — concerned
- **sad** — unfortunate events

---

## Frontend Skills & Overlay Controls

The overlay app (`lilly-overlay`) provides direct access to skills and automation:

### Overlay UI Buttons

| Button | Action |
|--------|--------|
| 🎤 Mic | Toggle voice input (browser mic or Android SpeechRecognizer) |
| ⏺ Macro | Start/stop recording a macro ("Follow me" mode) |
| 📍 Map | Show current location |
| 📡 Radar | Scan for nearby Bluetooth devices |
| ➤ Send | Send text message |

### Macro Recorder ("Follow Me")

1. Press **⏺** to start recording
2. Perform actions: tap apps, speak commands, navigate
3. Press **⏹** to stop
4. Say **"Save macro as [name]"** to save
5. Say **"Run [name]"** to replay

### Natural Language Automation

Say **"Automate Instagram"** (or any platform) to enter a setup conversation:

```
You: Automate Instagram
Lilly: What actions? Likes, follows, comments, or all three?
You: Likes and follows
Lilly: How many of each per run?
You: 30 likes, 10 follows
Lilly: When should I run this?
You: At 6am daily
Lilly: Done! Created 'instagram_automation' with 41 steps.
```

### Available Skills

View all skills via chat: **"What can you do?"** or **"List skills"**

Common categories:
- **Apps:** Open YouTube, Netflix, Gmail, Maps, etc.
- **Navigation:** Go home, back, recent apps, notifications
- **Sensors:** Light, temperature, compass, steps, battery
- **Automation:** Macros, scheduled tasks, batch actions
- **Communication:** SMS, calls, notifications
- **Media:** Camera, photos, audio recording

### Skill Triggers

Skills can be triggered by:
- **Voice:** "Open YouTube"
- **Text:** Type in chat input
- **Macro:** Recorded button/tap sequences
- **Schedule:** Time-based automation (daily at 6am, etc.)
- **Sensor:** Light level, motion, proximity triggers

---

## Advanced: Custom Skills

### Adding New Skills

Edit `lilly_skills.json`:

```json
{
  "my_custom_action": {
    "action_type": "shell_command",
    "command": "sh",
    "args": ["-c", "your-command-here"],
    "label": "My Custom Action",
    "aliases": ["custom action", "run custom", "my action"]
  }
}
```

### Macro Format

Macros are stored in `macros.json`:

```json
{
  "steps": [
    {"step": 1, "type": "launch", "data": "com.instagram.android"},
    {"step": 2, "type": "termux", "data": "input tap 500 800"}
  ]
}
```

### Automation Config

See `config-examples/` for templates:

| File | Purpose |
|------|---------|
| `instagram_automation.json` | Basic likes/follows template |
| `instagram_growth_advanced.json` | Advanced with scheduling, comments, delays |

---

## API Reference

### Macro Endpoints

| Method | Endpoint | Purpose |
|--------|----------|---------|
| POST | `/api/automation/learn` | Create/update automation |
| POST | `/api/automation/run` | Execute automation |
| GET | `/api/automation/list` | List all automations |
| DELETE | `/api/automation/delete` | Delete automation |
| POST | `/api/automation/export` | Export automation as JSON |
| POST | `/api/automation/import` | Import automation from JSON |
| POST | `/api/automation/follow` | Start follow-me recording |
| POST | `/api/automation/record` | Record automation steps |

### Example: Trigger from Insomniac

```bash
# Run immediately
curl -X POST http://<your-ip>:8098/api/automation/run \
  -H "Content-Type: application/json" \
  -d '{"name": "bedtime_routine"}'

# Import a config first
curl -X POST http://<your-ip>:8098/api/automation/import \
  -H "Content-Type: application/json" \
  -d @config-examples/bedtime_routine.json
```

---

## Troubleshooting

### "No sensor data"
Check the sensor server is running: `curl http://<phone-ip>:8099/health`

### "App won't launch" / "Commands don't work"
This is almost always a **Termux permission** issue. The overlay app needs the **`com.termux.permission.RUN_COMMAND`** permission to tell Termux to open apps and run shell commands.

**Fix:**
1. Open Android **Settings → Apps → Termux → Permissions**
2. Find **"Run command"** / **"Termux Run Command"** and set it to **Allow**
3. In Termux, enable external apps:
   ```bash
   mkdir -p ~/.termux
   grep -qx 'allow-external-apps=true' ~/.termux/termux.properties || echo 'allow-external-apps=true' >> ~/.termux/termux.properties
   ```
4. Fully close Termux (swipe it away) and reopen it
5. In the overlay app, tap the **"Enable Termux Shell Tools"** button to verify

After this, commands like "Open YouTube", "Go home", and "Take a screenshot" will work.

### "No notifications"
Ensure `termux-notification-list` is installed and Notification Listener permission is granted:
```
pkg install termux-api
# Then enable in Android Settings > Apps > Termux > Notification Listener
```

### "Mic not working"
Background mic requires SSH. Use the browser's built-in mic (click the mic button in the UI) as a no-SSH alternative.

### "TTS not speaking"
Check Piper voices exist at `lillyos/voices/*.onnx`. Run `pip install piper-tts` if missing.

### "Macro not executing"
- Verify Termux permission is granted
- Check macro steps are valid Termux commands
- Test individual commands via chat: "Run ls"
- Check server logs for errors

---

## Tips

- Memory persists across sessions (last 20 exchanges)
- Camera vision: "what do you see" triggers photo + description
- Bluetooth devices are saved with friendly names you teach
- Lilly has opinions — ask about storms, magnets, constellations
- Sensor data updates in real-time (every 2 seconds)
- All 9 avatars can be active; switch anytime
- Macros can be scheduled via chat or Insomniac webhooks
- Automation configs are saved to `config-examples/` for reuse
- Sensor data updates in real-time (every 2 seconds)
- All 9 avatars can be active; switch anytime
