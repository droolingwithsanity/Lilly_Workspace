# Lilly AI Advanced Automation Configs

Pre-made, editable advanced automations with conditions, filters, multi-step flows, and scraping.

## Files

| File | Description |
|------|-------------|
| `instagram_story_watch.json` | Watch stories, like/follow from story viewers |
| `morning_routine_chain.json` | Multi-app chain: weather → news → calendar |
| `tiktok_engage.json` | TikTok likes + follows with conservative limits |
| `instagram_with_filters.json` | Business-account filter, working hours, daily caps |
| `simple_web_scrape.json` | Scrape web pages via Scrapling + BeautifulSoup |
| `flow_interact_unfollow_loop.json` | Multi-step flow with looping and delays |

## Usage

```bash
# Import directly
curl -X POST http://<server>:8098/api/automation/import \
  -H "Content-Type: application/json" \
  -d @config-examples-extra/instagram_story_watch.json

# Or run from chat
"Run instagram_story_watch"
```

## Advanced Features

### Conditions
```json
"conditions": {
  "working_hours_only": true,
  "working_hours_start": "09:00",
  "working_hours_end": "17:00",
  "skip_if_seen_recently": true,
  "reinteract_after_hours": 24,
  "max_follows_per_day": 12
}
```

### Filters (for interact/scrape)
```json
"filters": {
  "skip_business": true,
  "min_followers": 100,
  "max_followers": 50000,
  "min_posts": 5,
  "skip_already_following": true
}
```

### Multi-step Flows
```json
"steps": [
  {"step": 1, "config_file": "config-examples/instagram_likes_only.json"},
  {"step": 2, "config_file": "config-examples-extra/instagram_follow_only.json"}
]
```

## Tips

- Combine basic configs into flows for complex routines
- Use `working_hours_only` to avoid running at night
- Set `max_follows_per_day` to stay under platform limits
- Test each step individually before chaining
