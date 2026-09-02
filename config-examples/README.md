# Lilly AI Automation Config Examples

Pre-made, editable automation configs you can drop into Lilly and run immediately.

## Quick Start

1. **Copy** any JSON file from this folder into your Lilly `config-examples/` directory
2. **Edit** the values to match your needs (times, counts, coordinates)
3. **Run** via chat: `"Run <name>"`
4. **Schedule** via chat: `"Schedule <name> at 6am daily"`

## Basic Configs (`config-examples/`)

| File | Description |
|------|-------------|
| `instagram_likes_only.json` | Scroll feed, like 10 posts |
| `instagram_follow_only.json` | Search hashtag, follow 5 accounts |
| `instagram_likes_and_comments.json` | Like 8 posts + comment on 2 |
| `youtube_music_play.json` | Open YT Music, play liked songs |
| `morning_news_check.json` | Open news, scroll, dismiss alerts |
| `bedtime_routine.json` | Dark mode, lower volume, open sleep sounds |
| `quick_camera_photo.json` | Open camera, take photo, return home |
| `read_and_dismiss_notifications.json` | List and clear notifications |

## Advanced Configs (`config-examples-extra/`)

| File | Description |
|------|-------------|
| `instagram_story_watch.json` | Watch stories, like/follow from stories |
| `morning_routine_chain.json` | Weather → news → calendar chain |
| `tiktok_engage.json` | TikTok likes + follows with safety limits |
| `instagram_with_filters.json` | Skip business accounts, follower filters |
| `simple_web_scrape.json` | Scrape HN titles via Scrapling |
| `flow_interact_unfollow_loop.json` | Multi-step flow with looping |

## JSON Schema

```json
{
  "name": "unique_automation_name",
  "platform": "instagram|tiktok|youtube_music|android_system|multi|web",
  "package": "com.instagram.android",
  "actions": [
    {"type": "open_app", "app": "settings", "comment": "Open Settings"},
    {"type": "wait", "seconds": 3},
    {"type": "tap", "x": 500, "y": 800, "comment": "Tap button"},
    {"type": "swipe", "x1": 500, "y1": 1200, "x2": 500, "y2": 400},
    {"type": "input_text", "text": "search query"},
    {"type": "press_key", "key": "HOME|BACK|VOLUME_UP|VOLUME_DOWN|POWER"},
    {"type": "screenshot"},
    {"type": "shell", "command": "termux-battery-status"},
    {"type": "like", "count": 10},
    {"type": "follow", "count": 5},
    {"type": "comment", "text": "Nice!", "count": 3},
    {"type": "search", "query": "#photography"},
    {"type": "delay", "seconds": 5},
    {"type": "browser_action", "url": "https://...", "selector": ".css-selector"}
  ],
  "schedule": {
    "repeat": "once|hourly|daily|weekly",
    "time": "06:00",
    "times": ["09:00", "18:00"],
    "comment": "Human-readable schedule note"
  },
  "limits": {
    "max_likes_per_run": 20,
    "max_follows_per_run": 5,
    "max_comments_per_run": 3,
    "max_runs_per_day": 1,
    "delay_between_actions": [2, 5],
    "comment": "Random delay range in seconds"
  },
  "conditions": {
    "working_hours_only": true,
    "working_hours_start": "09:00",
    "working_hours_end": "17:00",
    "skip_if_seen_recently": true,
    "reinteract_after_hours": 24,
    "max_follows_per_day": 20,
    "filters": {
      "skip_business": true,
      "min_followers": 100,
      "max_followers": 50000,
      "min_posts": 5,
      "skip_already_following": true
    }
  }
}
```

## Supported Action Types

| Type | Description |
|------|-------------|
| `open_app` | Launch app by name or package |
| `close_app` | Close current app |
| `wait` / `delay` | Pause N seconds |
| `tap` | Tap at x,y coordinates |
| `swipe` | Swipe from (x1,y1) to (x2,y2) |
| `input_text` | Type text into focused field |
| `press_key` | Hardware key press |
| `screenshot` | Take screenshot |
| `shell` | Run raw shell command |
| `like` | Like N posts (app-specific) |
| `follow` | Follow N accounts (app-specific) |
| `comment` | Comment on N posts with text |
| `search` | Search for query |
| `browser_action` | Scrape URL with CSS selector |

## Safety Tips

- Start with **low limits** (5 likes, 2 follows) and increase slowly
- Use **delays** (`delay_between_actions: [3, 6]`) to appear human
- Test with **"Run <name>"** before scheduling
- Check app terms of service — automation may violate some platform rules

## Troubleshooting

| Problem | Fix |
|---------|-----|
| Taps miss target | Adjust x,y coordinates in JSON |
| App doesn't open | Verify package name in Settings → Apps |
| Commands fail | Grant Termux `RUN_COMMAND` permission |
| Too fast/slow | Adjust `delay_between_actions` range |
