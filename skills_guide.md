# Lilly AI — Frontend Skills Guide

## What are Skills?

Skills are pre-built actions Lilly can perform on your phone. They range from opening apps to checking sensors, reading notifications, and controlling media. Skills are triggered by voice command, chat text, or the radial menu.

## How Skills Work

1. **Voice** — Say a trigger phrase like "open youtube" or "what notifications"
2. **Chat** — Type the same phrase in the overlay or web UI chat
3. **Radial Menu** — Long-press the Lilly sphere and tap an icon

Lilly matches your input against a skill registry, then executes the action locally on your phone or via the web UI.

## Skill Categories

### Apps & Media
- **YouTube** — Opens in Picture-in-Picture mode. Say "open youtube" or "watch [query]".
- **Gmail** — Opens your inbox. Requires OAuth setup.
- **Spotify / Netflix / Music** — Launches the media app.
- **Camera** — Opens the camera for photos/video.
- **Maps** — Opens Google Maps with your location or a searched destination.

### Phone Controls
- **Volume** — "volume up", "louder", "quieter"
- **Screenshot** — Captures the screen
- **Home / Back** — Navigation shortcuts
- **Settings / Calculator / Calendar** — Quick app launchers

### Sensors & Environment
- **Battery** — "how much battery", "battery status"
- **Light** — "how bright is it"
- **Location** — "where are we"
- **Bluetooth** — "who's around", "radar"
- **Weather** — Derived from barometer + temperature sensors

### Notifications
- **Read Alerts** — "what notifications", "any alerts"
- **Priority Order** — OS calls/SMS first, then app priority (max > high > default > low > min)
- **Actions** — Say "reply", "call back", or "dismiss"

### Games & Activities
- **Car Ride Game** — "start car game" (kid mode)
- **Fetch Game** — "start fetch game" (kid mode)
- **Activity Tracking** — "start a walk", "start a run", "stop tracking"

### Advanced
- **SSH** — Remote shell into a server *(host-only, removed for shared users)*
- **Package Management** — Install/update Termux packages *(host-only)*
- **Termux Commands** — "run [command]" executes via Termux bridge

## Expected Behavior

| Action | Expected Result |
|--------|-----------------|
| "open youtube" | YouTube opens in PiP mode |
| "what notifications" | Lilly reads top 3 pending alerts aloud |
| "skip ad" | Taps the skip button coordinates |
| "where are we" | Returns city/neighborhood + GPS link |
| "start a walk" | Begins tracking speed/distance |
| "screenshot" | Saves to Photos app |

## Known Problems & Coming Editions

### v6.2 (Current)
- [x] YouTube PiP Error 153 fixed by using embed URL instead of watch URL
- [x] Overlay sphere too small — increased to 160px
- [x] Chat messages cut off — increased max-height and padding
- [x] Pairing sync between overlay and web UI — unified 6-digit code flow
- [x] Duplicate overlay/video buttons in hamburger menu — removed video button

### v6.3 (Planned)
- [ ] Swipe-to-dismiss for chat/speech bubble
- [ ] Hamburger menu open speed optimization from top-right corner
- [ ] Video menu button repurposed or removed entirely
- [ ] Notification TTS engine improvements for longer alerts
- [ ] Fix "Watch video on YouTube" Error 153 for all video link formats

### v6.4 (Planned)
- [ ] Full skill marketplace with install/uninstall
- [ ] Skill icons themed per avatar (puppy/fox/cat/bear/etc.)
- [ ] Skill tutorials with "Teach Me" button for every skill
- [ ] Offline skill execution when web UI is unreachable

### v7.0 (Roadmap)
- [ ] Plug-in skill system for custom user skills
- [ ] Skill permissions sandbox (no shell access for guest users)
- [ ] Cross-device skill sync via pairing token
- [ ] Voice-activated skill creation ("learn to do X")

## Troubleshooting

| Problem | Solution |
|---------|----------|
| Skill does nothing | Check `/api/lilly_skills` in web UI to verify skill is loaded |
| YouTube shows Error 153 | Use "watch [query]" instead of pasting raw YouTube links |
| Notifications not read | Enable Notification Listener in Android Settings > Apps > Termux |
| Overlay lags | Close other floating apps; restart overlay service |
| Pair code mismatch | Refresh code in web UI and re-enter on phone |
| Skills missing in overlay | Rebuild APK with latest `lilly_skills.json` |

## Developer Notes

- Skills are defined in `lilly_skills.json` (132+ skills)
- Tutorials are in `skill_tutorials.json`
- Overlay skill triggers: `overlay_local.html` → `handleLocalCommand()`
- Web UI skill triggers: `lilly_ai.py` → `/api/cmd` endpoint
- Dangerous skills (ssh, package_install, etc.) are stripped for non-host users
