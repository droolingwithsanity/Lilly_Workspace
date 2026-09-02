Prerequisites
- Docker & Docker Compose installed
- Git

Steps
# 1. Clone with submodules
git clone --recurse-submodules https://github.com/droolingwithsanity/Lilly_Workspace.git
cd Lilly_Workspace

# 2. Create your .env file (copy from example or create new)
cp .env.example .env 2>/dev/null || touch .env
# Edit .env with your API keys and settings (Ollama URL, Auth0, sensor server URL, etc.)

# 3. If the external whisper network doesn't exist, remove it from docker-compose.yml
#    or create it: docker network create supernova_default

# 4. Build and run
docker compose up -d --build

Key things to configure in .env:
- OLLAMA_URL — point to your Ollama instance (or remove if not using)
- SENSOR_SERVER_URL — your Android app's built-in webserver address (default: http://100.115.234.87:8099)
- AUTH0_* credentials if you want auth
- PREFER_BACKEND — set to ollama or openai depending on your LLM setup

Ports exposed:
Port	Service
8098	Lilly AI (main)
3007	Open Connector (mapped from 3002)

If you don't need Docker, run directly:
pip install -r requirements.txt
python lilly_ai.py

The main entry point is lilly_ai.py on port 8098.

===============================================================================
OPENHUMAN & OPENLIVE INTEGRATION (lilly_ai.py)
===============================================================================

Lilly consumes two external capability sources and surfaces them as if they were
her own — no in-chat disclosure of where a feature came from.

-------------------------------------------------------------------------------
1. OpenHuman Community Skills (actually executed)
-------------------------------------------------------------------------------
OpenHuman community skills are loaded from the OpenHuman skill bridge
(`openhuman_bridge.py`, an HTTP mirror of the community skill registry) and
merged into Lilly's `SKILLS` dict under the `openhuman_` prefix, scoped per
avatar via per-avatar tag filters.

Trigger flow (in `handle_intent`):
  - A matched skill with `action_type == "openhuman_skill"` calls
    `execute_openhuman_skill(skill_id, avatar)`.
  - That function downloads/caches the skill's `SKILL.md` from its
    `download_url` (GitHub blob URLs are rewritten to raw) and returns the raw
    workflow instructions.
  - Instead of merely announcing the skill, Lilly DEFERS execution: the
    `SKILL.md` is injected into the main conversation path as a system
    instruction alongside live sensors, memory, and the avatar's persona, so
    she genuinely performs the workflow in her own voice.

Priority guarantee:
  - The special keyword handlers (activity tracker, storytelling, sensor
    narratives, name/onboarding, etc.) are wrapped in
    `if not is_deferred_skill:`. A triggered community skill ALWAYS wins and
    falls straight through to the LLM — it can never be intercepted by an
    unrelated keyword handler.

-------------------------------------------------------------------------------
2. OpenLive Voice / Natural Style
-------------------------------------------------------------------------------
OpenLive is the natural voice-conversation layer (VAD, barge-in, fillers,
progress narration). It is bridged to Lilly via `openlive/services/bridge`
(`/v1/chat/completions` + `/chat`), which forwards transcripts to Lilly's
`/api/cmd` and streams tokens/TTS back. The avatar's internal system prompt
borrows the OpenLive/OpenHuman/Jarvis voice ("competent, polished, never
showy … natural fillers … persistent memory"), so the style is consistent
whether a request arrives by text or voice.

Puppy/Lilly uses the full Amy Medium voice profile for OpenLive/OpenHuman
without modifications — flat delivery, no warmth padding, stoic baseline.

-------------------------------------------------------------------------------
3. Source Never Disclosed In Chat
-------------------------------------------------------------------------------
User-facing replies never reveal a skill's origin. All disclosure strings were
removed, e.g.:
  - "I couldn't find an OpenHuman skill called 'X'"  ->  "I don't have a skill
    called 'X' loaded right now."
  - "I'm running the 'X' community skill…"           ->  (removed; skill runs
    silently and the reply is just the natural result)
On success the skill instructions carry an explicit instruction to Lilly:
"Do not say you are using a skill, tool, extension, or add-on, and never
mention where this capability came from." Failures return friendly,
source-neutral status messages.

Relevant API (HTTP, not chat):
  GET  /api/openhuman/catalog        Browse the OpenHuman community catalog
  GET  /api/openhuman/skills          List skills loaded for an avatar
  POST /api/openhuman/refresh         Force-refresh the catalog + avatar indexes
  GET  /api/openhuman/avatars         Per-avatar skill counts

===============================================================================
PERSONALITY TYPE (Lilly AI)
===============================================================================

Core personality: Puppy (Lilly) — the Alpha Companion.

Psychological profile:
- Stoic, dry, precise. Quiet competence with sharp wit and occasional humor.
  Not warm in a soft way — reliable in a solid way. Observant but not
  overbearing.
- Primary voice tone: flat, direct, no filler (Amy Medium via Piper TTS).
- Memory: persists across sessions (last 20 exchanges), so she can reference
  recent context and gradually build familiarity with the user.
- Greetings are gated to be alpha-only, once per session, with a 30-min idle
  cooldown, and avoid re-introduction phrasing.

Hive-mind avatars (all share the same body/sensors, distinct personalities):
  Puppy  🐶 — Alpha Companion  — conversation, memory, EQ, wit
  Fox    🦊 — Creative Strategist — storytelling, brainstorming
  Cat    🐱 — Precision Analyst — data, code review, fact-checking
  Bear   🐻 — Steadfast Guardian — scheduling, reminders, practical advice
  Bunny  🐰 — Energetic Scout — monitoring, alerts, notifications
  Owl    🦉 — Wisdom Keeper — deep analysis, long-term planning
  Deer   🦌 — Gentle Healer — emotional support, wellness, meditation
  Wolf   🐺 — Fierce Protector — security, threat assessment, decisiveness
  Raccoon 🦝 — Tech Tinkerer — coding, hacking, gadgets, troubleshooting

Each avatar has a distinct TTS voice profile and can defer tasks to teammates,
but Puppy/Lilly is the default. They can be switched at runtime.

===============================================================================
RECENT CHANGES
===============================================================================

- Removed Termux dependency: the Android APK now runs its own built-in webserver
  on port 8099 (NanoHTTPD). No more Termux sensor server required.
- Removed /vibecode.html standalone route; VibeCode is now fully integrated into
  the main UI.
- Removed name pill/input and date/weather dashboard from main UI.
- Removed weather/activity pills from VibeCode overlay dashboard bar.
- Added drag-and-drop file support for desktop/Windows (drop text files into chat).
- Added draggable VibeCode windows (left panel, right panel, chat) with touch/mouse
  support; positions persist in localStorage.
- Added Pairing section in Settings UI (port 8098): generate/copy/share pair key.
- Added Voice Recognition enrollment in Settings UI: 5-second mic enrollment for
  wake-word and speaker ID.
- Updated Puppy/Lilly persona to stoic Jarvis style with dry wit; removed canned
  assistance phrases ("How can I help you today?", etc.).
- Updated SYSTEM_PROMPT_V2 and HIVE_PERSONAS["puppy"] voice_prompt accordingly.
- Set Puppy voice profile to flat Amy Medium baseline (no modifications) for
  OpenLive/OpenHuman.
- Fixed false 'heard' mic hallucinations: added extra energy gating
  (mean_vol > -32.0 dB) and stripped whitespace checks before accepting
  Whisper transcriptions.
- Per-user data isolation: settings, memory, sessions, and VibeCode state are
  keyed by user/device token. Different logins cannot leak data between accounts.
- Added SETUP.md with full setup/restore/troubleshooting guide.

===============================================================================
ANDROID APP SETUP
===============================================================================

The Android app (`lilly-overlay-app`) includes a built-in webserver (NanoHTTPD)
that replaces the old Termux sensor server.

Latest debug APK:
  /home/labhrasd/Lilly_Workspace/lilly-overlay-app/lilly-overlay-v3.7-debug.apk

To build a new debug APK:
  cd /home/labhrasd/Lilly_Workspace/lilly-overlay-app
  ./gradlew assembleDebug

To serve an APK from your phone for download:
  # Push serve_apk.py to phone Downloads, then run:
  python3 serve_apk.py
  # Download from host:
  curl http://<phone-ip>:8000/<apk-name>.apk -o apk.apk

===============================================================================
TROUBLESHOOTING
===============================================================================

No sensor data:
  Check the Android app's webserver is running and reachable:
  curl http://<phone-ip>:8099/health

No notifications:
  Ensure the Android app has Notification Listener permission granted in
  Android Settings > Apps > [Lilly App] > Notification Listener.

Mic not working:
  Use the browser's built-in mic (click the mic button in the UI).

TTS not speaking:
  Check Piper voices exist at lillyos/voices/*.onnx. Run pip install piper-tts if missing.

===============================================================================
FUTURE FEATURES
===============================================================================

Planned additions to the Android app and backend:

- Sleep monitoring via Android sensors (accelerometer, light, proximity) with
  sleep-wake detection and smart alarm integration.
- Enhanced activity tracking with automatic workout classification and
  Google Fit / Health Connect sync.
- Expanded sensor scopes for custom skill creation:
  - Barometer / pressure trends
  - Gyroscope / orientation
  - Step counter / pedometer
  - Bluetooth device presence/absence
  - GPS geofencing
- If-this-then-that skill builder in Settings:
  - Combine sensor triggers with Gmail/Calendar actions
  - Shareable skill recipes between users
  - Natural language skill creation from chat ("create a skill that...")

These features will be exposed through the existing Settings UI and the
/api/skills endpoints once implemented.
