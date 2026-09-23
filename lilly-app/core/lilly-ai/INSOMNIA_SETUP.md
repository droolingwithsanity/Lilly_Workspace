# Insomnia — Instagram Engagement Automation

"Insomnia" is a background engagement session for Instagram that runs entirely
from the phone's Termux sensor server via its `/shell` bridge. **No ADB, no
scrcpy, no computer connection.** The engine lives in `insomnia_runner.py` and
is controlled from the main Lilly UI (natural language, skill `9d`) and from
the Training dashboard's **🤖 Insomnia** tab.

Config keys map 1:1 to [Insomniac](https://github.com/alexal1/Insomniac) flags
(`likes_count`, `likes_percentage`, `stories_count`, `follow_percentage`,
`comment_percentage`, `comments_list`, `max_following`, `speed`,
`working_hours`, `repeat`), so existing Insomniac configs port over directly.

---

## How it works

Each session probes the phone once and picks a **driver mode**:

| Mode | Used when | What it does |
|------|-----------|--------------|
| `intent` | `input` not available | Opens deep links only (`termux-open-url` / `am start VIEW`). Safe fallback that needs no special permissions. |
| `tap` | `input` / `uiautomator` expose tap access | Full Insomniac-style behaviour: taps the follow button, opens the first grid post, likes, comments, swipes back. Requires shell-uid tap access (see below). |

Mode is decided per session:
`driver: "auto"` (default) → `tap` if the phone answers `input --help`,
otherwise `intent`. You can force either with `driver: "tap"` or `"intent"`.

`intent` mode posts targets open, but follow/comment/like interactions need
tap-mode. Tap mode needs **Shizuku or another shell grant that exposes
`input`/`uiautomator`** to the Termux uid — it is *probed at runtime*, not
assumed.

---

## Phone requirements

| Requirement | Needed for | Install |
|-------------|------------|---------|
| Termux + `termux-open-url` | Opening profiles/hashtags/posts (`termux-api` pkg) | `pkg install termux-api` |
| `am` (`pm` optional) | Intent deep-link fallback | ships with Android, probed via `adb shell`? No — probed via the sensor server `/shell` |
| `input` shell tool | **Tap mode** (follow, like, comment, back-nav) | needs Shizuku (`pkg install shizuku`?) — no: **Granting** is done in Android settings, not Termux |
| Storage permission | `uiautomator dump` XML (tap coordinates) | grant Termux storage in Android Settings |

### Enabling tap mode (recommended)

1. Install **Shizuku** from the Play Store (or `pkg install shizuku`).
2. Start it (adb wireless *once*, or root), then run `adb shell sh /sdcard/Android/data/moe.shizuku.privileged.api/start.sh`.
3. On the phone grant the **Termux** app shell access:
   `Settings → Apps → Termux → Permissions`, and in Shizuku add Termux as an
   allowed app. This exposes `input`, `input text`, and `uiautomator` to the
   Termux uid so the sensor server's `/shell` can drive taps.
4. Verify: in the session log you'll see `driver mode: tap` instead of `intent`.

> Colour-keys and tap coordinates assume a **1080 × 2400** screen and the
> Instagram app in its default layout. Override with `screen_w` / `screen_h`
> and the `coordinates` block if your resolution differs.

---

## Targets

Targets are comma / newline separated:

```
@someuser          -> user profile    https://www.instagram.com/someuser/
#photography       -> hashtag         https://www.instagram.com/explore/tags/photography/
https://..com/p/xx -> single post
```

Both `interact` and `targets` are accepted as key names. Insomniac-style
scraping targets like `@user-followers` / `#tag-likers` need profile
enumeration that a Tap-less driver can't do — the runner opens the parent
target only.

---

## Config keys (all optional unless noted)

| Key | Example | Meaning |
|-----|---------|---------|
| `interact` / `targets` | `["@jane","#travel","…/p/AbCd/"]` | Targets to visit per pass (list or string) |
| `likes_count` | `"2"` or `"2-4"` | Likes per opened post (range = random) |
| `likes_percentage` | `100` | Chance to like each opened post |
| `stories_count` | `1` | Stories watched per profile (capped at 3) |
| `follow_percentage` | `20` | Chance to follow the profile |
| `comment_percentage` | `10` | Chance to comment |
| `comments_list` | `"Nice shot,Love this"` | Comments pool (comma/newline split) |
| `max_following` | `5` | Hard cap on follows per session |
| `speed` | `1–4` | 1 slow/human → 4 fast (default 3) |
| `working_hours` | `"9-23"` | Only act inside this hour range; loop until allowed |
| `repeat` | `"180"` | Re-run the pass every N minutes (default single pass) |
| `driver` | `"auto"` \| `"tap"` \| `"intent"` | Force a driver |
| `screen_w` / `screen_h` | `1080` / `2400` | Device resolution for relative coordinates |
| `coordinates` | `{...}` | `like_xy`, `follow_xy`, `comment_xy`, `send_xy`, `first_post_xy` as relative `"0.86,0.47"` strings — only for tap mode |

Session state mirrors to `lilly-app/core/lilly-ai/trainer/logs/insomnia_sessions.json`
and each session appends a human-readable log to
`lilly-app/core/lilly-ai/trainer/logs/insomnia_<timestamp>.log`
(`WORKSPACE/trainer/logs`, visible in the Training tab's log box).

---

## Running & stopping

### Main UI (natural language)
- **Start**: `9d, run insomnia — interact @jane and #photography, 3 likes each, speed 4`
- **Presets**: `9d, run preset nightly` (presets saved via Training tab persist in
  `lilly-app/core/lilly-ai/data/automation_presets.json`)
- **Status**: `9d, insomnia status`
- **Stop**: `9d, stop insomnia`

### Training dashboard (:8098/training.html → 🤖 Insomnia)
- **Presets** list + `▶ Run`, **quick-run** form (targets, qty, speed, toggles for
  follow/comment/story), **Stop** button, live status stats, and a log box
  showing the newest session's output.
- Endpoints behind the dashboard (require owner login):
  - `GET  /api/training/automation/status`
  - `POST /api/training/automation/run`
  - `POST /api/training/automation/stop`
  - `GET  /api/training/logs?type=insomnia`
- Public (chat-side) mirrors: `GET /api/automation/status`.

---

## Safety notes

- Sessions are **asynchronous and stoppable** — a stop sets the cancel event;
  the loop checks it before every action and exits at the next pause.
- `driver: intent` is the zero-risk mode; enable tap mode only if you
  understand the interaction pattern (auto-follow/like can trip
  Instagram's automation heuristics).
- Rate ceilings are built in (`max_following`, `speed`, human-like jitter);
  lower `speed` and raise `working_hours` for a conservative setup.
- Comments run through an allowlist regex — only
  `A-Za-z0-9@.:#$%&*()_+-` and spaces survive.