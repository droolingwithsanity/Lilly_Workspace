# Lilly AI v7.3.1 — "Overlay Overhaul"

## What Changed

v7.3.1 is a major overlay rebuild with new UI, vision pipeline integration, and system feature parity.

### 1. Lavender Papirus Theme

**Before:** Yellow/pink bubble (`#FFF8E6`, `#F08AC1`)
**After:** Lavender theme (`#F5F0FA`, `#9B7DB8`, `#EDE5F5`) — consistent with Papirus Light design language.

- Bubble background, tails, and shadows all use lavender palette
- Chat input and send button themed to match
- Radial menu items use glass morphism with lavender accents

### 2. Chat Messenger Redesign

- Emoji avatar (`🐶`) replaces broken 32dp WebView canvas in bubble header
- Bubble width fixed to 340dp (was `wrap_content` causing icon clipping)
- ScrollView uses `layout_weight="1"` for proper keyboard-aware layout
- Bottom tail default hidden (`gone` in XML)
- Chat history capped at 40 entries to prevent unbounded memory growth

### 3. 4-Item Radial Menu

**Before:** 3 items (Chat, Settings, Close)
**After:** 4 items spiraling to center:
- **Chat (0°)** — opens chat messenger
- **Wiki (90°)** — opens knowledge base wiki panel
- **Kid Mode (180°)** — toggles Socratic learning mode
- **Settings (270°)** — opens settings

### 4. Camera Vision Pipeline

- **CRITICAL FIX:** Camera frame executor deadlock — `sendFrameToServer()` was re-queued on same single-thread executor as frame loop. Fixed with separate `frameSendExecutor`.
- **CRITICAL FIX:** Camera ID ArrayIndexOutOfBounds — `getCameraIdList()[0]` now guarded with length check
- **HIGH FIX:** All camera-related NPEs guarded (`cameraStatusLabel`, `bubbleSendButton`, `closeButton`, `bubbleInputView`)
- `toggleCameraFromOverlay()` now actually calls `stopCamera()` / `showCameraWindow()` (was just toggling visibility)

### 5. Stop Sign Detection (Haar Cascade)

Integrated OpenCV Haar Cascade stop sign detector alongside YOLO:
- `stop_data.xml` loaded lazily from workspace root
- Runs on grayscale frame after YOLO detection in `detect_objects()`
- Fires Android notification ("Stop Sign Detected") with 15s cooldown
- Detections merged into avatar sensor context

### 6. Missing Backend Endpoints

- `POST /api/macro/save` — saves macro to `macros.json` and registers as skill
- `POST /api/macro/play` — executes macro steps via Termux
- `GET /api/macro/list` — lists all saved macros

### 7. Dead Code & Resource Cleanup

- Removed `avatarSyncTicker` Runnable (no-op since `syncAvatarState()` was gutted)
- Removed dead `if/else` branch in avatar name resolution
- Fixed SSH bridge leak — proper null-after-use pattern
- Transcript capped at 200 lines to prevent OOM

### 8. Termux Setup Simplified

- New `lilly_termux_setup.sh` — single command setup (packages, permissions, sensor server, SSH)
- No SSH required for most features — sensors, chat, skills work over plain HTTP

### 9. Voice Commands Added

- `"open wiki"` / `"wiki"` — opens wiki panel
- `"kid mode on"` / `"socratic mode"` — toggles kid mode
- `"kid mode off"` / `"normal mode"` — exits kid mode

---

# Lilly AI v6.0 — "Unified Pairing"

## What Changed

v6.0 is a clean break from the v4.x rebuild lineage. It focuses on three things:
simplified pairing, a single overlay codebase, and a one-command phone setup.

### 1. Unified Pairing Protocol (One Code)

**Before (v4.x):**
- 8-char phone token → user copies → pastes into web UI
- Separate 64-char device token returned
- Overlay had two pairing UIs (device code + web dashboard code)
- `/api/pair/verify` was called but didn't exist in the current server

**After (v6.0):**
- Web UI generates a **6-digit code** via `POST /api/pair/initiate`
- Code expires in **5 minutes**
- Overlay shows a single input: "Enter 6-digit code from web UI"
- User types code → overlay calls `POST /api/pair/confirm`
- Server returns **64-char device token**
- Overlay stores token in `localStorage` as `lilly_device_token`
- All subsequent calls use `X-Device-Token` header

**Endpoints:**
| Method | Endpoint | Purpose |
|--------|----------|---------|
| POST | `/api/pair/initiate` | Generate 6-digit code |
| POST | `/api/pair/confirm` | Exchange code for device token |
| GET | `/api/pair/status` | Check code validity |
| POST | `/api/phone_pair` | Legacy v4.x compat (still works) |

### 2. One Termux Grant

New `termux_grant.sh` script automates everything possible:

```bash
chmod +x termux_grant.sh
./termux_grant.sh
```

What it does:
1. Verifies Termux environment
2. Installs `termux-api`
3. Opens overlay permission screen (`SYSTEM_ALERT_WINDOW`)
4. Opens notification listener settings
5. Grants microphone + camera permissions
6. Installs Python dependencies (`fastapi uvicorn httpx`)
7. Creates `~/ai-server/` directory
8. Copies `termux_sensor_server.py` to phone
9. Generates persistent pairing token at `~/.lilly_pair_token`
10. Starts sensor server on port 8099
11. Displays pairing instructions

### 3. Complete Overlay Experience

**Overlay App v6.0:**
- Version: `6.0` (versionCode 60)
- Simplified `MainActivity` — removed old 8-char token UI
- Pairing now happens entirely in the overlay WebView
- Auto-focus on code input, auto-submit when 6 digits entered
- Clear success/error feedback with colors

**Overlay WebView (`overlay_local.html`):**
- Removed "Your device code" section
- Removed legacy 8-char code input
- Single 6-digit code input with numeric keyboard
- "Pair Now" button with mint accent color
- Auto-submit when 6 digits typed
- Status feedback with color-coded messages

### 4. Sensor Server v6.0

- Version string added to all responses
- `/api/version` endpoint returns service info
- `/health` endpoint includes `version` and `pair_token_required`
- `/pair/public-key` includes `version`

### 5. Backward Compatibility

- `/api/phone_pair` still accepts 8-char tokens
- Legacy overlay code calling `/api/pair/verify` will get 404 — use `/api/pair/confirm` instead
- Auth0 OIDC unchanged
- All existing API endpoints preserved

## Migration from v4.x

1. **Update APK:** Install `lilly-overlay-app` v6.0
2. **Run setup:** `./termux_grant.sh` on your phone
3. **Pair:** Open overlay → tap Pair → enter 6-digit code from web UI
4. **Done:** No more copy-pasting tokens, no more 8-char codes

## Files Changed

| File | Change |
|------|--------|
| `lilly_ai.py` | Added `/api/pair/initiate`, `/api/pair/confirm`, `/api/pair/status`; updated docstring to v6.0 |
| `termux_sensor_server.py` | Added `/api/version`, updated `/health` and `/pair/public-key` with version |
| `lilly-overlay-app/app/build.gradle` | versionCode 60, versionName "6.0" |
| `hitomi-android/app/build.gradle` | versionCode 60, versionName "6.0" |
| `lilly-overlay-app/app/src/main/res/raw/overlay_local.html` | Simplified pairing UI to 6-digit code flow |
| `lilly-overlay-app/app/src/main/res/layout/activity_main.xml` | Removed PAIRING section, updated header to v6.0 |
| `lilly-overlay-app/app/src/main/java/ai/agent1c/hitomi/MainActivity.java` | Removed legacy pairing code |
| `termux_grant.sh` | New one-command setup script |
