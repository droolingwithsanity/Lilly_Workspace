# Lilly AI Automation System

## Overview

Lilly can now create, schedule, and run social media automations through natural conversation. The system uses:
- **Macro recorder** (⏺ button) to capture actions
- **Chat-based intent clarification** to configure automations
- **Task scheduler** for time-based execution
- **Config examples** for reusable automation templates
- **Insomniac integration** via HTTP webhooks

## Quick Start

### 1. Install the APK
- Download `lilly-overlay-v6.12.0-macro-debug.apk` from the web UI
- Install on your Android phone
- Grant Termux `RUN_COMMAND` permission

### 2. Create an Automation via Chat

Say something like:
- "Automate Instagram"
- "Create a bot for Instagram"
- "Schedule Instagram automation"
- "Auto like and follow on Instagram"

Lilly will ask clarifying questions:
1. **What actions?** — likes, follows, comments, or all three
2. **How many?** — e.g., "20 likes, 5 follows, 3 comments"
3. **When?** — e.g., "at 6am daily", "every 3 hours", "tomorrow at 10pm"
4. **Comments text?** — if you chose comments, what text to use

### 3. Confirm or Cancel

After answering all questions, Lilly summarizes and asks:
- Say **"confirm"** to save the automation
- Say **"cancel"** to discard

### 4. Run or Schedule

- **Run now:** "Run instagram_automation"
- **Schedule:** Already scheduled if you specified a time
- **Edit config:** Config saved to `config-examples/instagram_automation.json`

## Example Conversation

```
You: Automate Instagram
Lilly: Sure, I can help you set up an Instagram automation. 
       What actions should it perform? Likes, follows, comments, or all three?

You: Likes and follows
Lilly: Got it — I'll include likes and follows. 
       How many of each per run? For example: 20 likes, 5 follows, 3 comments.

You: 30 likes and 10 follows
Lilly: Perfect. When should I run this? 
       For example: 'at 6am daily', 'every 3 hours', or 'once tomorrow at 10pm'.

You: At 6am daily
Lilly: Done! Created 'instagram_automation' with 41 steps. 
       Config saved to config-examples/instagram_automation.json. 
       You can say 'run instagram_automation' to execute it.
```

## Config Examples

### Basic: `config-examples/instagram_automation.json`
```json
{
  "name": "instagram_likes",
  "platform": "instagram",
  "package": "com.instagram.android",
  "actions": [
    {"type": "open_app"},
    {"type": "wait", "seconds": 3},
    {"type": "like", "count": 10}
  ],
  "schedule": {
    "repeat": "daily",
    "time": "06:00"
  },
  "limits": {
    "max_likes_per_run": 20,
    "max_follows_per_run": 5,
    "max_comments_per_run": 3
  }
}
```

### Advanced: `config-examples-extra/instagram_growth_advanced.json`
```json
{
  "name": "instagram_growth",
  "platform": "instagram",
  "package": "com.instagram.android",
  "actions": [
    {"type": "open_app"},
    {"type": "wait", "seconds": 3},
    {"type": "search", "query": "#photography"},
    {"type": "follow", "count": 5},
    {"type": "like", "count": 15},
    {"type": "comment", "texts": ["Great shot! 📸", "Nice work!"], "count": 3}
  ],
  "schedule": {
    "repeat": "daily",
    "times": ["09:00", "18:00"]
  },
  "limits": {
    "max_likes_per_run": 30,
    "max_follows_per_run": 10,
    "max_comments_per_run": 5,
    "delay_between_actions": [2, 5]
  }
}
```

## Insomniac Integration

Insomniac can trigger Lilly automations via HTTP:

```bash
# Trigger immediately
POST http://<your-ip>:8098/api/macro/play
{"name": "instagram_automation"}

# Schedule a macro
POST http://<your-ip>:8098/api/macro/schedule
{"name": "instagram_automation", "when": "at 6am", "repeat": "daily"}
```

### Insomniac Setup

1. In Insomniac, create a new **HTTP Request** action
2. Set method to **POST**
3. URL: `http://<your-server-ip>:8098/api/macro/play`
4. Headers: `Content-Type: application/json`
5. Body: `{"name": "instagram_automation"}`
6. Set your schedule trigger (time, charger, etc.)

## Supported Platforms

| Platform | Package Name | Notes |
|----------|-------------|-------|
| Instagram | `com.instagram.android` | Official app |
| Instagram Lite | `com.instagram.lite` | Light version |
| TikTok | `com.zhiliaoapp.musically` | |
| Twitter/X | `com.twitter.android` | |
| Facebook | `com.facebook.katana` | |
| YouTube | `com.google.android.youtube` | |

## Safety Limits

Default limits are conservative to avoid shadowbans:
- **Likes:** 20 per run
- **Follows:** 5 per run  
- **Comments:** 3 per run
- **Delay between actions:** 2-5 seconds (random)

Adjust in the config JSON if needed.

## Files Reference

| File | Purpose |
|------|---------|
| `config-examples/instagram_automation.json` | Basic template |
| `config-examples-extra/instagram_growth_advanced.json` | Advanced template with scheduling |
| `macros.json` | Saved macros (auto-generated) |
| `scheduled_tasks.json` | Scheduled reminders/tasks |
| `lilly_skills.json` | Skill registry (macros appear here) |

## Commands

| Say | Action |
|-----|--------|
| "Automate Instagram" | Start automation setup |
| "Run instagram_automation" | Execute saved macro |
| "Schedule X at 6am daily" | Schedule macro (uses task scheduler) |
| "List macros" | Show all saved macros |
| "Cancel" | Discard current automation setup |
| "Confirm" | Save automation setup |
