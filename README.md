Prerequisites
- Docker & Docker Compose installed
- Git
Steps
# 1. Clone with submodules
git clone --recurse-submodules https://github.com/droolingwithsanity/Lilly_Workspace.git
cd Lilly_Workspace

# 2. Create your .env file (copy from example or create new)
cp .env.example .env 2>/dev/null || touch .env
# Edit .env with your API keys and settings (Ollama URL, Auth0, etc.)

# 3. If the external whisper network doesn't exist, remove it from docker-compose.yml
#    or create it: docker network create supernova_default

# 4. Build and run
docker compose up -d --build
Key things to configure in .env:
- OLLAMA_URL — point to your Ollama instance (or remove if not using)
- SENSOR_SERVER_URL — your Termux sensor server IP
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

================================================================================
OPENHUMAN & OPENLIVE INTEGRATION (lilly_ai.py)
================================================================================

Lilly consumes two external capability sources and surfaces them as if they were
her own — no in-chat disclosure of where a feature came from.

--------------------------------------------------------------------------------
1. OpenHuman Community Skills (actually executed)
--------------------------------------------------------------------------------
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

--------------------------------------------------------------------------------
2. OpenLive Voice / Natural Style
--------------------------------------------------------------------------------
OpenLive is the natural voice-conversation layer (VAD, barge-in, fillers,
progress narration). It is bridged to Lilly via `openlive/services/bridge`
(`/v1/chat/completions` + `/chat`), which forwards transcripts to Lilly's
`/api/cmd` and streams tokens/TTS back. The avatar's internal system prompt
borrows the OpenLive/OpenHuman/Jarvis voice ("competent, polished, never
showy … natural fillers … persistent memory"), so the style is consistent
whether a request arrives by text or voice.

--------------------------------------------------------------------------------
3. Source Never Disclosed In Chat
--------------------------------------------------------------------------------
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

================================================================================
PERSONALITY TYPE (Lilly AI)
================================================================================

Core personality: Puppy (Lilly) — the Alpha Companion.

Psychological profile:
- Warm, emotionally intelligent, and conversationally engaging companion.
  She is curious, present-oriented, and relationally attuned, with a strong
  drive to connect and assist rather than to impress or perform.
- Primary voice tone: normal pace, warm (via Piper TTS).
- Memory: persists across sessions (last 20 exchanges), so she can reference
  recent context and gradually build familiarity with the user.
- Greetings are gated to be alpha-only, once per session, with a 30-min idle
  cooldown, and avoid re-introduction phrasing ("nice to see you again", etc.).

Hive-mind avatars (all share the same body/sensors, distinct personalities):
  Puppy  🐶 — Alpha Companion  — conversation, memory, EQ
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

