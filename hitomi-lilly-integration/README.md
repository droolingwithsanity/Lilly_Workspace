# hitomi-lilly-integration

Replace Hitomi's hedgehog overlay with the Lilly AI companion avatar from lilly_ai.py, connected via a WebView overlay.

## What this does

- **Replaces the hedgehog** with Lilly's canvas-rendered puppy avatar (plus fox, cat, bear, bunny, owl, deer, wolf, raccoon)
- **Connects chat** to lilly_ai's `/api/cmd` endpoint (Ollama/llama.cpp backend)
- **Voice toggle** in the overlay quick-actions menu (calls `/api/toggle_mic`)
- **Real-time state** polling from `/api/ui_state` (hears what Lilly says, shows mood/animation)
- **Speech output** via `/api/tts` audio playback through the WebView
- **Drag-to-move** via JavaScript bridge communicating with Android

## Files

| File | Purpose |
|------|---------|
| `LillyOverlayService.java` | Drop-in replacement for `HedgehogOverlayService.java` – renders a WebView loading the `/overlay` page with compact/expanded modes |
| `LillyAIChatClient.java` | HTTP client for lilly_ai's API (`/api/cmd`, `/api/ui_state`, `/api/toggle_mic`) with `open_url` support for app launching |
| `overlay_lilly.xml` | Layout — single WebView with transparent background (goes in `res/layout/`) |
| `overlay_lilly_actions.xml` | Quick actions bar — Settings / Script / Mic / Close buttons |
| `lilly_skills.json` | App name → package mappings with aliases, loaded at startup |

## Setup

### 1. Deploy the lilly_ai server

The `/overlay` endpoint is already added to `lilly_ai.py`. Start the server:

```bash
python3 lilly_ai.py
```

The overlay page is at `http://<server>:8098/overlay`.

### 2. Add files to hitomi-android

| File | Destination |
|------|-------------|
| `LillyOverlayService.java` | `app/src/main/java/ai/agent1c/hitomi/LillyOverlayService.java` |
| `LillyAIChatClient.java` | `app/src/main/java/ai/agent1c/hitomi/LillyAIChatClient.java` |
| `overlay_lilly.xml` | `app/src/main/res/layout/overlay_lilly.xml` |
| `overlay_lilly_actions.xml` | `app/src/main/res/layout/overlay_lilly_actions.xml` |
| `lilly_skills.json` | `app/src/main/res/raw/lilly_skills.json` |

### 3. Register the service in `AndroidManifest.xml`

```xml
<service
    android:name="ai.agent1c.hitomi.LillyOverlayService"
    android:exported="false"
    android:foregroundServiceType="specialUse" />
```

### 4. Add start/stop buttons in `activity_main.xml` (or wire to existing buttons)

```java
// Start Lilly overlay
Intent intent = new Intent(this, LillyOverlayService.class);
intent.setAction(LillyOverlayService.ACTION_START);
ContextCompat.startForegroundService(this, intent);

// Stop Lilly overlay
Intent intent = new Intent(this, LillyOverlayService.class);
intent.setAction(LillyOverlayService.ACTION_STOP);
startService(intent);
```

### 5. Server URL config

The default server URL is `http://100.93.131.114:8098`. To change it, set it in SharedPreferences:

```java
LillyAIChatClient client = new LillyAIChatClient(this);
client.setServerUrl("http://your-server:8098");
```

## Features

- **Icon-sized avatar** – Starts as a 64×64dp floating icon (transparent background, just the canvas-rendered Lilly pup with moving eyes/lips/ears). Tap to expand to full 320×500 overlay with chat UI.
- **Messenger-style close** – Drag the icon down to a red X target at the bottom of the screen to close the overlay.
- **Drag anywhere** – Drag the overlay by the canvas area to reposition.
- **Voice control** – Built-in STT via Android SpeechRecognizer, forwards text to Lilly's `/api/cmd`.
- **App launching** – When the server responds with an `open_url` (Android intent or http), the overlay automatically launches the app or opens the URL. Ask Lilly "open Settings" or "launch Chrome" and it works via Termux/SSH.
- **Canvas avatar** – Not an image, but a procedurally drawn puppy (or fox/cat/bear/bunny/owl/deer/wolf/raccoon) with real-time emotion, blinking, ear wiggle, and mouth animation synced to speech.

## Architecture

```
┌─────────────────────────────────────────────┐
│           Android Overlay                   │
│  ┌───────────────────────────────────────┐  │
│  │  COLLAPSED (64×64dp)                 │  │
│  │  ┌──────────────┐                    │  │
│  │  │ Canvas Avatar│  Moving eyes/lips  │  │
│  │  └──────────────┘  Tap to expand     │  │
│  │                                       │  │
│  │  EXPANDED (320×500dp)                │  │
│  │  ┌──────────────┐                    │  │
│  │  │ Canvas Avatar│  + drag handle     │  │
│  │  ├──────────────┤                    │  │
│  │  │ Chat Bubble  │  text I/O          │  │
│  │  │ + Mic + Send │  voice control     │  │
│  │  └──────────────┘                    │  │
│  └───────────────────────────────────────┘  │
│                                              │
│  Quick actions (long-press when expanded)    │
│  ┌──────┬──────┬──────┐                    │
│  │ ⚙️  │ 🎤  │ ✕   │                    │
│  └──────┴──────┴──────┘                    │
│                                              │
│  Close target (drag icon to bottom X)       │
│  ┌──────────────────────────────┐           │
│  │              ✕               │           │
│  └──────────────────────────────┘           │
└──────────────┬──────────────────────────────┘
               │ JS Bridge / HTTP polling
               ▼
┌─────────────────────────────────────────────┐
│         lilly_ai.py (FastAPI)               │
│  /api/cmd        → chat + intent processing │
│  /api/ui_state   → real-time state + open_url│
│  /api/toggle_mic → voice activation toggle   │
│  /api/tts        → TTS audio playback        │
│  /overlay        → avatar+chat overlay page  │
│  Ollama / llama.cpp → LLM backend            │
│  Piper TTS       → per-character voices      │
│  SSH → Termux   → app launch + UI automation │
└─────────────────────────────────────────────┘
```
