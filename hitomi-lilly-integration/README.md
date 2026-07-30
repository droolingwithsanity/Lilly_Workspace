# hitomi-lilly-integration

Replace Hitomi's hedgehog overlay with the Lilly AI companion avatar from lilly_ai.py, connected via a WebView overlay.

## Quick Start

Choose your AI backend and follow the setup steps below. Skip if you have everything ready.

## What this does

- **Replaces the hedgehog** with Lilly's canvas-rendered puppy avatar (plus fox, cat, bear, bunny, owl, deer, wolf, raccoon)
- **Connects chat** to lilly_ai's `/api/cmd` endpoint (Ollama/llama.cpp backend)
- **Voice toggle** in the overlay quick-actions menu (calls `/api/toggle_mic`)
- **Real-time state** polling from `/api/ui_state` (hears what Lilly says, shows mood/animation)
- **Speech output** via `/api/tts` audio playback through the WebView
- **Drag-to-move** via JavaScript bridge communicating with Android

### AI Backend Options

1. **Local GGUF** (Recommended for Termux)
   - Run `llama.cpp` or `ollama` on your phone
   - Uses GGUF format models (e.g., Llama2, CodeLLama, etc.)

2. **Hugging Face** 
   - Use Hugging Face inference APIs

3. **Cloud Model APIs**
   - OpenAI, Anthropic, Google, etc.

## Get Started with Local AI Server

### Option 1: Termux (Lightweight)

If you're running on Android phone:

1. **Install Termux** (if not already)
2. **Get llama.cpp**
   ```bash
   git clone https://github.com/ggml-org/llama.cpp.git
   cd llama.cpp
   make
   ```

3. **Download GGUF model**
   ```bash
   mkdir -p ~/models
   # Get any GGUF model from Hugging Face
   # Example: https://huggingface.co/TheBloke/Llama-2-7B-Chat-GGUF/resolve/main/llama-2-7b-chat.Q4_K_M.gguf
   curl -L "URL_FROM_HF" -o ~/models/model.gguf
   ```

4. **Run llama.cpp server**
   ```bash
   ./build/bin/llama-server \
       -m ~/models/model.gguf \
       -c 2048 \
       -n 512 \
       -threads 2 \
       -host 0.0.0.0 \
       -port 5005
   ```

5. **Start this app** - The server IP is what you'll enter in the app (e.g., `http://192.168.1.100:5005`)

### Option 2: Hugging Face Inference API (Cloud)

```bash
# Install via pip
curl -LsSL https://ollama.ai/install.sh | sh
# Or use Hugging Face API endpoints
```

**Server IP** will be the Hugging Face API endpoint URL (e.g., your Hugging Face Space URL)

### Option 3: Running lilly_ai.py directly

```bash
python3 lilly_ai.py
# Start the server locally
cd ~/Lilly_Workspace
python3 lilly_ai.py

# Server IP is usually http://192.168.1.100:8098
```

**For Running lilly_ai.py Locally:**

```bash
# Clone and setup
cd ~
git clone https://github.com/username/LillyAI.git
cd LillyAI

# Install dependencies
pip3 install -r requirements.txt

# Start the server
python3 lilly_ai.py
```

**Server IP options:**
- `http://192.168.1.100:8098` (local machine)
- `http://192.168.1.100:5005` (llama.cpp server)
- `http://192.168.1.100:8000` (Open WebUI with Ollama)

## Setup

### 1. Deploy the lilly_ai server

The `/api/cmd`, `/api/ui_state`, `/api/toggle_mic`, `/api/tts`, and `/overlay` endpoints are already included in `lilly_ai.py`. Start the server:

```bash
python3 lilly_ai.py
```

**For Termux (lightweight setup):**

```bash
cd ~/Lilly_Workspace
python3 lilly_ai.py
```

The overlay page is at `http://<server>:8098/overlay`.

### 2. Setup the App

Download this repository or copy the required files:

| File | Destination |
|------|-------------|
| `LillyOverlayService.java` | `app/src/main/java/ai/agent1c/hitomi/LillyOverlayService.java` |
| `LillyAIChatClient.java` | `app/src/main/java/ai/agent1c/hitomi/LillyAIChatClient.java` |
| `overlay_lilly.xml` | `app/src/main/res/layout/overlay_lilly.xml` |
| `overlay_lilly_actions.xml` | `app/src/main/res/layout/overlay_lilly_actions.xml` |
| `lilly_skills.json` | `app/src/main/res/raw/lilly_skills.json` |
| `MainActivity.java` | `app/src/main/java/ai/agent1c/hitomi/MainActivity.java` |

### 3. Register the service in `AndroidManifest.xml`

```xml
<uses-permission android:name="android.permission.SYSTEM_ALERT_WINDOW" />
<uses-permission android:name="android.permission.FOREGROUND_SERVICE" />
<uses-permission android:name="android.permission.FOREGROUND_SERVICE_SPECIAL_USE" />
<uses-permission android:name="android.permission.RECORD_AUDIO" />
<uses-permission android:name="android.permission.INTERNET" />
<uses-permission android:name="android.permission.POST_NOTIFICATIONS" />

<application
    android:allowBackup="true"
    android:icon="@drawable/ic_launcher_foreground"
    android:label="Lilly"
    android:supportsRtl="true"
    android:theme="@style/Theme.LillyOverlay"
    android:usesCleartextTraffic="true">

    <activity
        android:name=".MainActivity"
        android:exported="true"
        android:theme="@style/Theme.LillyOverlay">
        <intent-filter>
            <action android:name="android.intent.action.MAIN" />
            <category android:name="android.intent.category.LAUNCHER" />
        </intent-filter>
    </activity>

    <activity
        android:name=".WebViewActivity"
        android:exported="false"
        android:theme="@style/Theme.LillyOverlay"
        android:windowSoftInputMode="adjustResize" />

    <service
        android:name=".LillyOverlayService"
        android:exported="false"
        android:foregroundServiceType="specialUse" />

</application>
```

### 4. Build and Install the APK

1. Open the project in Android Studio or navigate to the Android directory
2. Build with `./gradlew assembleDebug` (as shown above)
3. Install the debug APK:
   ```bash
   adb install app/build/outputs/apk/debug/app-debug.apk
   ```

### 5. Configure Server URL

The default server URL is `http://100.93.131.114:8098`. To change it:

1. **Launch the app** - You'll see a MainActivity with an editable server URL field
2. **Enter custom server** - Fill in the server URL (e.g., `http://192.168.1.100:5005`)
3. **Save settings** - Click the "Save" button to persist the server URL
4. **Start overlay** - Click "Start Lilly Overlay" to begin the AI companion

**Supported Server Formats:**
- `http://192.168.1.100:8098` (local machine, lilly_ai.py)
- `http://192.168.1.100:5005` (llama.cpp server)
- `http://192.168.1.100:8000` (Open WebUI)
- `http://YOUR_IP_FROM_DROOLINGWITHSANITY.CA:8098` (public server)

### 6. Features with Sensor + Termux Integration

This APK includes:

- **Sensor Data**: Phone accelerometer, GPS, call info, battery, audio levels
- **Audio**: Playback and recording from phone
- **Termux Commands**: WiFi, SMS, contacts, media, app launching
- **Automation**: Camera and video capture via Termux
- **App Integration**: Direct app launching and UI automation

### 7. FAQ

**Q: What if lilly_ai.py isn't on the server?**

A: The "Deploy AI to Termux" button will download lilly_ai.py to your Termux directory. Then you can run it on your local machine or phone.

**Q: I need a smaller app build?**

A: Yes - this version removes unnecessary dependencies and focuses on core sensors + Termux features. Perfect for limited storage devices.

**Q: How do I choose my AI backend?**

A: The default is HHugging Face's Grok-4 (via hitomi-android), but you can:

1. Use **Termux** with llama.cpp for GGUF models
2. Use **Hugging Face** APIs directly
3. Use **lilly_ai.py** with your preferred LLM backend

Simply enter the server URL in the app and start using your chosen AI!

## files

| File | Purpose |
|------|---------|
| `LillyOverlayService.java` | Drop-in replacement for `HedgehogOverlayService.java` – renders a WebView loading the `/overlay` page with compact/expanded modes |
| `LillyAIChatClient.java` | HTTP client for lilly_ai's API (`/api/cmd`, `/api/ui_state`, `/api/toggle_mic`) with `open_url` support for app launching |
| `overlay_lilly.xml` | Layout — single WebView with transparent background (goes in `res/layout/`) |
| `overlay_lilly_actions.xml` | Quick actions bar — Settings / Script / Mic / Close buttons |
| `lilly_skills.json` | App name → package mappings with aliases, loaded at startup |

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
