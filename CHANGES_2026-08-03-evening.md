# Changes Summary: 2026-08-03 Session

## Previous Session (2026-08-03)

See CHANGES_2026-08-03.md for the full prior session log.

---

## Latest Session (2026-08-03 Late Evening)

### Issues Reported
1. Dashboard not showing in VibeCode UI
2. Pushbullet typo causing NameError
3. WHISPER_SERVER_URL hardcoded to non-existent Docker hostname
4. Dashboard CSS accidentally inside mobile media query
5. No pushbullet notification code despite API key in `.env`

### Fixes Applied

#### 1. Dashboard z-index raised (vibecode.html)
- Changed `#dashboard-window` z-index from `260` → `600`
- Reason: mobile `#chat-panel` gets `z-index:500 !important` and covers the entire screen. Dashboard was hidden behind it.

#### 2. Dashboard CSS moved outside mobile media query (vibecode.html)
- Dashboard styles were inside `@media(max-width:480px)` block — only applied on tiny screens
- Moved all `#dashboard-window` and `.dw-*` rules to global scope
- Added mobile-specific sizing inside the media query

#### 3. Pushbullet typo fixed (lilly_ai.py)
- Variable was `PUSBULLET_API_KEY` (missing 'S') in 5 locations
- Fixed to `PUSHBULLET_API_KEY` everywhere

#### 4. WHISPER_SERVER_URL uses env var (lilly_ai.py)
- Was hardcoded: `"http://lilly-whisper-stt:8000"` (non-existent container)
- Now: `os.environ.get("WHISPER_SERVER_URL", "http://localhost:8000")`
- Also made `WHISPER_MODEL` configurable via env var

#### 5. Pushbullet notification support added (lilly_ai.py)
- Added `send_pushbullet_note()` function
- Added fallback in `send_notification()` to Pushbullet when sensor server is unreachable
- Added `POST /api/pushbullet/send` REST endpoint (after `app` is defined)

#### 6. Pushbullet UI in VibeCode (vibecode.html)
- Added 🔔 `notify-btn` in topbar
- Added `#notify-panel` with title + message inputs
- Added `showNotifyPanel()`, `closeNotifyPanel()`, `sendPushNotification()` JS functions
- Wired button visibility into `updateUI()`

#### 7. `statusEl.className` bug fixed (vibecode.html)
- Was: `statusEl.className = 'dw-stat .dw-value ' + statusClass;` (invalid class with dots/spaces)
- Now: `statusEl.className = 'dw-value';` (preserves original class)

### Verification Results
| Test | Result |
|---|---|
| Dashboard chat ("show dashboard") | ✅ `action: dashboard_open` + full data |
| Dashboard endpoint | ✅ Real project data, no CNAME dependency |
| Pushbullet endpoint | ✅ `{"status":"sent"}` |
| Transcribe endpoint | ✅ Returns empty when Whisper unreachable (expected) |
| Notifications endpoint | ✅ Returns cached list (empty when phone not pushing) |
| Normal VibeCode chat | ✅ Returns LLM response |
| VibeCode page | ✅ HTTP 200, all elements present |
| Dashboard z-index | ✅ 600 > chat-panel 500 on mobile |

### Architecture Note
VibeCode runs on the SAME server (`localhost:8098`, container `lilly-ai`). No separate server, no CNAME, no external domain dependency. All endpoints are relative paths.

### New Backup
- `lilly_ai.py.bak.2026-08-03-late-evening`
- `vibecode.html.bak.2026-08-03-late-evening`
- `auth0_auth.py.bak.2026-08-03-late-evening`
