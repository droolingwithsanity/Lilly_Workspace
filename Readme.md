# Lilly AI — Phone + Web UI Guide

> **Drooling with Sanity** — Your personal AI companion that lives on your phone, senses the world through 23 Android sensors, reads your notifications, and helps you get things done — all through voice or text.

---

## What This Is

Lilly AI has two parts:

1. **Web UI** — The chat interface you're looking at right now. Runs in any browser, talks to your phone over the internet.
2. **Overlay App** — An Android app that runs on your phone, gives Lilly access to sensors, notifications, camera, microphone, and the ability to control your phone.

When paired, the web UI becomes a remote control for your phone. When not paired, it still works as a standalone chat interface.

---

## Prerequisites

### On Your Phone

- **Android 8+** (Oreo or newer)
- **Termux** — Install from [F-Droid](https://f-droid.org/en/packages/com.termux/) (not the Play Store version)
- **Termux:API** — Install from [F-Droid](https://f-droid.org/en/packages/com.termux.api/) to enable sensors, notifications, and hardware access
- **Lilly Overlay App** — Download from the **Hamburger Menu → Download** in this web UI

### Permissions You'll Need

When you first open the overlay app, grant these permissions:

| Permission | Why |
|------------|-----|
| **Draw over other apps** | So Lilly can show floating UI on top of other apps |
| **Notification Listener** | So Lilly can read your notifications and alert you to important ones |
| **Camera** | For camera vision ("what do you see") |
| **Microphone** | For voice chat |
| **Location** | For maps, navigation, and location-based context |
| **Bluetooth** | For radar/device scanning |
| **Phone** | For making calls and reading call logs |

---

## Getting Started

### Step 1: Install Termux

1. Go to [F-Droid.org](https://f-droid.org/en/packages/com.termux/)
2. Download and install **Termux**
3. Open Termux and run:
   ```bash
   pkg update && pkg upgrade -y
   pkg install termux-api python -y
   pip install fastapi uvicorn httpx
   ```

### Step 2: Install the Overlay App

1. Open this web UI on your computer or phone browser
2. Tap the **☰ hamburger menu** (top right)
3. Tap **Download**
4. Install the APK on your Android phone
5. Open the app and grant all requested permissions

### Step 3: Pair Your Phone

1. Open the overlay app on your phone
2. You'll see an **8-character pairing code** (e.g. `A1B2C3D4`)
3. Open this web UI → **☰ Menu → Pair**
4. Paste the code into the **"Pair with Overlay App"** section
5. Tap **Pair Overlay App**

You should see: **"Overlay app paired! (Lilly Overlay)"**

### Step 4: Start Chatting

Once paired, you can:

- **Type** messages in the chat box
- **Use voice** — tap the mic button and speak
- **Ask about your surroundings**: "What's around me?" → bluetooth + wifi scan
- **Open maps**: "Where am I?" → opens Google Maps on your phone
- **Check notifications**: "What notifications?" → reads your phone's notifications
- **Control your phone**: "Open YouTube", "Go home", "Take a screenshot"

---

## Using the Web UI

### Hamburger Menu (☰)

Tap the **☰** button top-right to access:

| Icon | Name | What It Does |
|------|------|--------------|
| 💬 | Chat | Opens the chat bubble |
| 🎤 | Mic | Toggles the browser microphone |
| 📡 | Radar | Scans surroundings (bluetooth/wifi) |
| 🗺️ | Map | Opens Google Maps |
| 🚗 | Car | Starts drive tracking |
| 📦 | Fetch | Checks notifications + sensors |
| ⚙️ | Settings | Opens settings panel |
| 🔗 | Pair | Opens the pairing panel |
| ⬇️ | Download | Downloads the overlay APK |
| ✕ | Close | Closes the menu |

**When paired**, most menu items control your **phone** directly instead of just the web UI.

### Settings Panel

Open **☰ → Settings** to configure:

- **Pushbullet** — Connect Pushbullet for fallback notifications
- **Notifications** — Control proactive alerts, sound, and daily caps
- **Sensor Server** — Connect to a phone sensor server (optional)
- **Pair Token** — Shared secret for device pairing
- **Pairing** — Generate or enter pairing codes

---

## Voice Commands

When the mic is active, try these:

### Phone Control
| Say | Action |
|-----|--------|
| "Open YouTube" / "Watch..." | Opens YouTube in Picture-in-Picture |
| "Open camera" / "Open maps" / "Open music" | Opens respective apps |
| "Go home" / "Go back" / "Recent apps" | Navigation controls |
| "Volume up" / "Volume down" | Volume control |
| "Take a screenshot" | Screenshot |
| "Dark mode" / "Light mode" | Theme toggle |

### Sensors & Environment
| Say | Response |
|-----|----------|
| "What do you sense" | Full sensor narrative |
| "How bright is it" | Ambient light level |
| "What's the temperature" | Temperature readings |
| "What's my step count" | Pedometer |
| "How much battery" | Battery percentage |
| "Where are we" | GPS location |
| "Who's near me" | Bluetooth device scan |

### Notifications
| Say | Action |
|-----|--------|
| "What notifications" | Lists active notifications by priority |
| "What did I miss" | Same — ordered by importance |
| "Read my emails" | Gmail inbox (needs OAuth) |

---

## Troubleshooting

### "No sensor data"
- Check the overlay app is running on your phone
- Verify Termux:API is installed
- Make sure all permissions are granted

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
5. In the overlay app, tap **☰ → Settings → "Enable Termux Shell Tools"** to verify

After this, commands like "Open YouTube", "Go home", and "Take a screenshot" will work.

### "No notifications"
- Enable Notification Listener permission:
  - Android Settings → Apps → Termux → Notification Listener → Enable
  - Android Settings → Apps → Lilly Overlay → Notification Listener → Enable

### "Mic not working"
- Grant microphone permission to the overlay app
- For background mic, use the browser's built-in mic (tap the mic button in the UI)

### "Pairing failed"
- Make sure both devices are on the same network
- The overlay app must be running (not force-closed)
- Try refreshing the pairing code in the app and re-entering it

### "Maps won't open on phone"
- Ensure Google Maps is installed
- Check that the overlay app has Location permission

---

## Privacy & Security

- All phone data stays on your phone — the web UI only sees what you share
- Pairing uses a one-time 8-character code, no passwords
- You can unpair anytime by clearing the overlay app data
- Notification data is processed locally on your phone

---

## Links

- **Project Homepage**: [droolingwithsanity.ca](https://droolingwithsanity.ca)
- **GitHub**: [github.com/labhrasd/Lilly_Workspace](https://github.com/labhrasd/Lilly_Workspace)
- **Termux**: [F-Droid](https://f-droid.org/en/packages/com.termux/)
- **Termux:API**: [F-Droid](https://f-droid.org/en/packages/com.termux.api/)

---

*Lilly AI · 9 Avatars · Termux + Docker · Built with ❤️*

---

## Camera Vision & Object Detection

Lilly can see through your phone's camera and describe what she sees.

### "What do you see"

Say or type any of these:
- "What do you see"
- "Look around"
- "What's in front of you"
- "Use your eyes"

Lilly will:
1. Take a photo from the phone camera
2. Run **YOLO object detection** to identify objects
3. Describe the scene in natural language

### AR Mode

Tap the **AR button** (camera icon) in the bottom toolbar to enable AR mode:
- Shows a live camera feed in a Picture-in-Picture window
- Draws detection boxes around recognized objects
- Labels objects in real-time as the camera moves

### Object Detection Model

Lilly uses **YOLO (You Only Look Once)** via the `ultralytics` library:
- Detects 80+ object classes (people, animals, vehicles, furniture, electronics, etc.)
- Runs locally on the device — no cloud uploads
- Falls back to "preview mode" if ultralytics is not installed

### Camera Permissions

Make sure the overlay app has **Camera** permission enabled:
- Android Settings → Apps → Lilly Overlay → Permissions → Camera → Allow

---

## Troubleshooting

### "Camera not available"
- Grant Camera permission to the overlay app
- On the web UI, allow camera access when prompted by the browser
- For AR mode, use the web UI (browser camera) — the overlay app camera vision is coming soon

### "Object detection not working"
- Install ultralytics in Termux: `pip install ultralytics`
- The first run will download the YOLO model (~6MB)
- If ultralytics is missing, Lilly falls back to preview mode and may not detect objects

### "AR mode is slow"
- AR mode runs detection every 500ms — this is normal for CPU-only inference
- For faster performance, use a device with GPU support or reduce the detection interval

---

## Privacy & Security

- All camera processing happens **locally on your device**
- No photos or video are uploaded to any server
- Object detection labels are generated locally and only shown to you
- You can disable camera vision anytime by revoking Camera permission

---

## Advanced: Vision Server

If you have a dedicated GPU or external vision server, you can offload object detection:

```bash
# In docker-compose.yml, set:
VISION_SERVER_URL=http://your-vision-server:8080
```

The vision server must accept JPEG frames and return JSON detections:
```json
{
  "detections": [
    {"label": "person", "conf": 0.92, "x": 0.1, "y": 0.2, "w": 0.3, "h": 0.5}
  ]
}
```

---

*Lilly AI · 9 Avatars · Termux + Docker · Built with ❤️*

---

## Memory System

Lilly uses a **4-layer memory pyramid** to remember conversations, facts, and context.

### Free Local Memory (Default)

Lilly includes a **free local memory server** that stores everything on your machine:
- No external service required
- No payment method needed
- Data stored in `/tmp/lilly_memory/` (or `LILLY_MEMORY_DIR`)
- Provides the same 4-layer memory as cloud options:
  - **L0 Conversation** — raw dialogue history
  - **L1 Atom** — atomic facts (preferences, names, patterns)
  - **L2 Scenario** — sensor context scenes ("driving", "at home")
  - **L3 Core** — shared hive-mind personality profile

### Starting the Local Memory Server

```bash
# In the workspace directory:
python local_memory_server.py --port 8420
```

Or with Docker Compose:
```bash
docker compose up -d local-memory
```

### Optional: External TencentDB Memory

If you want cloud-backed memory with additional features, you can use the
optional TencentDB Agent Memory service. This is **not required** — the local
memory server is fully functional for all features.

To use TencentDB instead:
```bash
export TENCENTDB_GATEWAY_URL=https://your-tencentdb-gateway.example.com
export TENCENTDB_API_KEY=your-api-key
```

---

*Lilly AI · 9 Avatars · Termux + Docker · Built with ❤️*
