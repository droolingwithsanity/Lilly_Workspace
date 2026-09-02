# Automation Quickstart — Any App

Use this guide to automate actions on **any Android app** with Lilly: open the app, tap buttons, swipe, type text, and schedule repeats.

## Prerequisites

- Lilly AI server running on port 8098
- Android phone with the Lilly overlay app installed
- Termux `RUN_COMMAND` permission granted (Settings → Apps → Termux → Permissions → “Run command” → Allow)

## Two Ways to Automate

### 1. Macro Recorder (Fastest)

1. Open the overlay app and tap **⏺ Record**.
2. Perform the actions you want to automate (tap buttons, swipe, open apps).
3. Tap **⏹ Stop**.
4. Say **“Save macro as [name]”** to save it.
5. Say **“Run [name]”** to replay it anytime.

### 2. Chat-Based Setup (More Control)

Say: **“Automate [app name]”**

Lilly will ask:
1. **What should it do?** — e.g., “open Spotify, play my liked songs, skip ads”
2. **How many times / what limits?** — e.g., “skip up to 3 ads”
3. **When to run?** — e.g., “daily at 7am”, “every 2 hours”, “once tomorrow at 10pm”

Say **“confirm”** to save, or **“cancel”** to discard.

## JSON Config Format

Saved automations are stored as JSON files in `config-examples/`. You can edit them directly or use them as templates for new apps.

```json
{
  "name": "my_automation",
  "platform": "spotify",
  "package": "com.spotify.music",
  "actions": [
    { "type": "open_app" },
    { "type": "wait", "seconds": 3 },
    { "type": "tap", "x": 500, "y": 900, "comment": "Tap play button" },
    { "type": "swipe", "x1": 500, "y1": 1200, "x2": 500, "y2": 400, "comment": "Scroll down" },
    { "type": "input_text", "text": "search query", "comment": "Type in search bar" }
  ],
  "schedule": {
    "repeat": "daily",
    "time": "07:00"
  },
  "limits": {
    "max_runs_per_day": 5,
    "delay_between_actions": [2, 5]
  }
}
```

### Supported Action Types

| Type | Description |
|------|-------------|
| `open_app` | Launch the app by package name |
| `close_app` | Close the app |
| `wait` | Pause for N seconds |
| `tap` | Tap at x,y screen coordinates |
| `swipe` | Swipe from (x1,y1) to (x2,y2) |
| `input_text` | Type text into the focused field |
| `press_key` | Press a hardware key (`HOME`, `BACK`, `POWER`, `VOLUME_UP`, `VOLUME_DOWN`) |
| `screenshot` | Take a screenshot |
| `shell` | Run a raw shell command |

## Running Automations

| Command | Action |
|---------|--------|
| `Run my_automation` | Execute immediately |
| `Schedule my_automation at 6am daily` | Set a schedule |
| `List macros` | Show all saved automations |
| `Delete my_automation` | Remove a saved automation |

## API (Insomniac / HTTP)

```bash
# Run immediately
curl -X POST http://<your-ip>:8098/api/macro/play \
  -H "Content-Type: application/json" \
  -d '{"name": "my_automation"}'

# Schedule
curl -X POST http://<your-ip>:8098/api/macro/schedule \
  -H "Content-Type: application/json" \
  -d '{"name": "my_automation", "when": "at 6am", "repeat": "daily"}'
```

## Safety Tips

- Add **delays** (`wait` actions or `delay_between_actions`) so the app can keep up.
- Set **limits** (`max_runs_per_day`) to avoid being flagged or throttled.
- Test with **“Run [name]”** before scheduling — watch the first execution to verify coordinates and timing.
- Keep actions simple: open → tap → swipe → close. Complex flows may need manual adjustment.

## Troubleshooting

| Problem | Fix |
|---------|-----|
| App doesn’t open | Verify the package name is correct (check Settings → Apps → App info) |
| Taps miss the target | Re-record the macro or adjust x,y coordinates in the JSON |
| Commands don’t work | Grant Termux `RUN_COMMAND` permission and restart Termux |
| Automation runs too fast | Add `wait` actions or increase `delay_between_actions` |
