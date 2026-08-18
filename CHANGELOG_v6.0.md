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
