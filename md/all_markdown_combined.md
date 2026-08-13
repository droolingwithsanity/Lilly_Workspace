---
FILE: ./.opencode/skills/lilly-theme/SKILL.md
---
---
name: lilly-theme
description: Use when customizing Lilly AI themes, creating popout interfaces, managing glass UI effects, or configuring visual appearance. Front-load keywords like theme, popout, glass, UI, appearance, visual, design, interface.
---

# Lilly Theme & Popout Skill

Use this skill when the user needs to customize the visual appearance of Lilly AI, create popout interfaces, or manage glass UI effects.

## Theme System

Lilly AI supports multiple theme layers:

### 1. Avatar Themes
Each avatar persona has its own visual theme:
- Bear, Bunny, Cat, Deer, Fox, Owl, Puppy, Raccoon, Wolf

### 2. Glass Effects
The overlay app supports glassmorphism effects:
- Blur backgrounds
- Transparency layers
- Shadow effects
- Border radius

### 3. Popout Interfaces
Create floating windows for:
- Chat bubbles
- Status indicators
- Quick actions
- Notification panels

## Configuration Files

### Theme Configuration
Location: `persona_configs.json`

```json
{
  "theme": {
    "primary_color": "#6366f1",
    "secondary_color": "#8b5cf6",
    "background": "rgba(0, 0, 0, 0.8)",
    "glass_blur": "20px",
    "border_radius": "16px"
  }
}
```

### Popout Settings
Location: `lilly-overlay-app/`

The Android overlay app handles:
- Floating windows
- System alerts
- Picture-in-picture mode
- Quick settings tiles

## Creating Custom Themes

### Step 1: Define Color Palette
```json
{
  "colors": {
    "primary": "#HEX",
    "secondary": "#HEX",
    "accent": "#HEX",
    "background": "rgba(R, G, B, A)",
    "text": "#HEX"
  }
}
```

### Step 2: Configure Glass Effects
```json
{
  "glass": {
    "blur": "20px",
    "transparency": 0.8,
    "border": "1px solid rgba(255, 255, 255, 0.2)",
    "shadow": "0 8px 32px rgba(0, 0, 0, 0.37)"
  }
}
```

### Step 3: Set Popout Behavior
```json
{
  "popout": {
    "position": "bottom-right",
    "size": "medium",
    "animation": "slide-in",
    "auto_hide": true
  }
}
```

## Integration Points

### With Termux
- SSH commands to update overlay app settings
- Push theme files to phone
- Restart overlay service

### With Docker
- Serve theme assets via HTTP
- Cache static resources
- Handle theme versioning

### With Lilly Backend
- API endpoints for theme management
- User preference storage
- A/B testing themes

## Quick Actions

### Apply Theme
```bash
# Via SSH to phone
ssh -i $TERMUX_SSH_KEY -p $TERMUX_SSH_PORT $TERMUX_SSH_USER@$TERMUX_SSH_HOST "am broadcast -a com.lilly.UPDATE_THEME --es theme 'dark'"
```

### Toggle Popout
```bash
# Via SSH to phone
ssh -i $TERMUX_SSH_KEY -p $TERMUX_SSH_PORT $TERMUX_SSH_USER@$TERMUX_SSH_HOST "am broadcast -a com.lilly.TOGGLE_POPOUT"
```

### Check Overlay Status
```bash
# Via SSH to phone
ssh -i $TERMUX_SSH_KEY -p $TERMUX_SSH_PORT $TERMUX_SSH_USER@$TERMUX_SSH_HOST "dumpsys activity services com.lilly.overlay"
```

## Design Guidelines

1. **Consistency**: Use the same color palette across all UI elements
2. **Accessibility**: Ensure sufficient contrast for text readability
3. **Performance**: Minimize blur effects on older devices
4. **Battery**: Reduce animations when battery is low
5. **Night Mode**: Support automatic theme switching based on time

## File Structure

```
lilly-overlay-app/
├── src/
│   └── main/
│       ├── java/
│       │   └── com/lilly/overlay/
│       │       ├── OverlayService.java
│       │       ├── ThemeManager.java
│       │       └── PopoutWindow.java
│       └── res/
│           ├── layout/
│           ├── values/
│           └── drawable/
├── build.gradle
└── src/main/AndroidManifest.xml
```

When helping users with themes, always check the current persona configuration and suggest changes that maintain visual consistency across the Lilly AI ecosystem.

---
FILE: ./.opencode/skills/repo-to-project/SKILL.md
---
---
name: repo-to-project
description: Use when turning a git repository into a running project — cloning or checking out a repo, converting it into a project, installing and building it into a Docker container, serving its UI on a free port or local domain with no port conflicts, or speeding up repo work by reusing the workspace's existing patterns (vibe code). Front-load keywords like repo, git clone, project, container, docker, deploy, serve, UI, domain, port, port conflict, dev server, hot reload, npm install, build.
---

# Repo → Project

Turn any git repo into a live, containerized project served at a stable URL —
reusing this workspace's proven patterns instead of reinventing them.

## Workflow

### 1. Take the repo and make it a project

- Accept a git URL, `owner/repo`, or a local path.
- Clone into `projects/<name>/` under the workspace (create `projects/` if missing):
  - `git clone <url> projects/<name>`
  - local dir: `cp -r <path> projects/<name>`
- **Repo becomes a project** — scaffold project identity:
  - `projects/<name>/AGENTS.md` — one page: stack, build command, run command, port, entrypoints.
  - Register it in `opencode.json` under `references` (mirrors how `PrivateCode` /
    `open-connector` are registered) so the model auto-discovers it and edits
    are allowed across the external-directory boundary.
- If the repo lives inside a git tree that should not track it, append
  `projects/` to that tree's `.gitignore`.

### 2. Inspect the stack

- **Node:** `package.json` → `build`/`start`/`dev` scripts and their port.
- **Python:** `requirements.txt` / `pyproject.toml` / `setup.py` → app entry, port.
- **Rust/Go:** `Cargo.toml` / `go.mod`.
- **Dockerfile present?** Prefer it. Otherwise generate one from the templates in
  this skill (`templates/`).
- Detect the internal port: `PORT` env, `server.listen(...)`, `app.run(...)`,
  uvicorn arg, vite `server.port`. Default: `3000` (node) / `8000` (python).

### 3. Install into a container

- Build: `docker build -t <name>:latest projects/<name>`
  (add `-f` if the Dockerfile has a custom name/path).
- Run with an allocator-reserved port (step 4) and bind-mount the repo for
  hot reload when it's a dev server:

  ```bash
  docker run -d --name <name> --restart unless-stopped \
    -p <HOSTPORT>:<APPPORT> \
    -v "$PWD/projects/<name>:/app" \
    <name>:latest
  ```

- For pure dev servers (`npm run dev`, `uvicorn --reload`), bind-mount + watch
  flag so repo edits reflect immediately without rebuild.

### 4. Serve with no port conflict

- Run `scripts/alloc-port.py <name> [preferred]` — it reserves a **stable port
  per project** in `.opencode/deployments.json` and probes the socket so it
  never collides with anything already listening (including Lilly `8098`,
  connector `3002`, code-server `8080`, static server `8199`, ollama `11434`).
- The registry gives each project a **stable URL across restarts**:
  `http://<host>:<port>`.
- If a **domain** is wanted:
  - Quick: add `127.0.0.1 <name>.test` to `/etc/hosts` and bind the container
    to port 80/443 only if free — otherwise keep the mapped port and set
    `APP_BASE_URL=http://<name>.test:<port>`.
  - Clean: point a local reverse proxy (Caddy / Traefik / nginx) at the
    container; `alloc-port.py` records `domain` in the registry for lookup.
- Log the result: container name, URL, and `docker logs -f <name>`.

### 5. Verify

- `curl -sf http://localhost:<port>/` → 200 (or the app's `/health`).
- If the app exposes a UI, confirm the HTML serves and report the URL.
- If the port gets taken later, rerun the allocator, re-map, and recreate the
  container (`docker rm -f <name>` then `docker run ...`).

## Reuse the workspace's vibe code

Speed up by copying proven artifacts instead of writing from scratch:

- `Dockerfile` — multi-stage build pattern (venv/pip, node_modules, slim runtime).
- `docker-compose.yml` — service layout, `env_file`, `restart: unless-stopped`, volumes.
- `deploy-lilly.sh` — banner, `step/ok/fail/warn/info` helpers, prereq checks
  (docker/compose/daemon), health-verification loop.
- `start.sh`, `ai-server-launcher.sh` — launcher conventions.
- `open-connector/` — Node API-proxy pattern (express + fetch + dotenv).
- `termux_utils.py`, `bt_profiles.py`, `skills_engine.py` — Python module
  conventions (typed funcs, `if __name__ == "__main__":` CLI).
- This skill's own templates: `templates/Dockerfile.node.tpl`,
  `templates/Dockerfile.python.tpl`, `templates/compose.service.tpl`.

## Gotchas

- `network_mode: host` (used by lilly) does **not** map ports — for new
  projects prefer bridge networking + `-p` so the allocator's probe works.
- Check `ss -tlnp` / `docker ps` before assuming a port is free; the allocator
  does this automatically.
- Bind-mounting `/app` overrides image files — fine for dev; use an image-only
  run for production-like serving.
- Prefer Node 18+ / Python 3.10+ images matching the repo's requirements.

## Registry format

`.opencode/deployments.json`:

```json
{
  "projects": {
    "<name>": {
      "port": 8123,
      "domain": null,
      "url": "http://localhost:8123",
      "container": "<name>",
      "updated": "2026-07-30T23:00:00"
    }
  }
}
```

---
FILE: ./AGENTS.md
---
# Lilly AI Project - Agent Instructions

## Project Overview

Lilly AI is a personal AI companion system that runs on Android phones (via Termux) with a Docker-based backend. The system includes:

- **FastAPI Backend**: Main Lilly AI server on port 8098
- **Open Connector**: Node.js email/calendar API on port 3002
- **Termux Services**: Phone sensors, camera, microphone access
- **Android Overlay**: Floating UI windows and notifications
- **ML Pipeline**: LoRA fine-tuned persona models

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    Lilly AI System                          │
├─────────────────────────────────────────────────────────────┤
│  Host Machine (Docker)          │  Android Phone (Termux)   │
│  ├── lilly_ai.py (FastAPI)      │  ├── AI Server (llama.cpp)│
│  ├── open-connector (Node.js)   │  ├── Sensor Server        │
│  ├── Docker Compose             │  ├── SSHD                 │
│  └── Tailscale Network          │  └── Termux API           │
└─────────────────────────────────────────────────────────────┘
```

## Development Guidelines

### Code Style
- **Python**: PEP 8, type hints, docstrings
- **JavaScript/TypeScript**: ESLint, Prettier
- **Shell**: ShellCheck, executable scripts
- **Java**: Google Java Style Guide

### Testing
- Run `pytest` for Python tests
- Run `npm test` for Node.js tests
- Run `docker-compose up` to verify full stack

### Git Workflow
- Main branch: `master`
- Feature branches: `feature/*`
- Bug fixes: `fix/*`
- Always run tests before committing

## Common Tasks

### Deploy Lilly AI
```bash
docker-compose build
docker-compose up -d
docker-compose logs -f
```

### Connect to Termux
```bash
ssh -i $TERMUX_SSH_KEY -p $TERMUX_SSH_PORT $TERMUX_SSH_USER@$TERMUX_SSH_HOST
```

### Check Phone Status
```bash
curl $SENSOR_SERVER_URL/status
```

### Update Persona Model
```bash
cd trainer
python train.py --persona <name>
cp trained_persona_model/<name> ../lillyos/models/
```

## Security Notes

- Never commit `.env` files or API keys
- Use Tailscale for secure networking
- Rotate SSH keys periodically
- Monitor access logs

## Troubleshooting

### Common Issues
1. **Connection refused**: Check Tailscale and SSHD status
2. **Docker build fails**: Verify Docker daemon is running
3. **Model not loading**: Check GGUF file path and permissions
4. **Sensor data missing**: Verify Termux API permissions

### Logs Location
- Host: `docker-compose logs`
- Phone: `~/ai-server/logs/server.log`
- Sensors: `http://$SENSOR_SERVER_URL/logs`

---
FILE: ./COMPLETION_PLAN.md
---
IMPLEMENTATION COMPLETION PLAN (Tasks 1-5) - CURRENT STATUS:

TASK 1: lilly_phone_server.py ✅ COMPLETED ✅
- Created: lilly_phone_server.py with all required endpoints
- Function: Flask/HTTP server on localhost:8099 for phone capabilities
- Features: commands, sensors, mic, notifications, volume, brightness, app launch

TASK 2: LillyOverlayService auto-start ✅ COMPLETED ✅
- Restored: lilly-overlay-app source code (LillyOverlayService.java, LocalPhoneClient.java, MainActivity.java, OverlaySettingsActivity.java)
- Added: startLillyPhoneServer() method in LillyOverlayService using TermuxCommandBridge
- Added: phoneClient field initialized in onCreate/onStartCommand
- Replaced: raw HttpURLConnection calls with LocalPhoneClient.get()
- Auto-start: phone server starts automatically in onStartCommand()

TASK 3: LocalPhoneClient.java ✅ COMPLETED ✅
- Created: LocalPhoneClient.java with HTTP client for overlay ↔ server communication
- Function: Wrapper for all phone server endpoints
- Wired: instantiated and used throughout LillyOverlayService

TASK 4: Wire LocalPhoneClient into LillyOverlayService ✅ COMPLETED ✅
- Status: LocalPhoneClient fully integrated into LillyOverlayService
- Added: executePendingCommand() method handling open_app/open_url/toast/termux/keyevent/input_text
- Added: handleServerActions() processes state.pendingCommands from UiState
- Updated: UiState class with pendingCommands and phoneConnected fields
- Updated: fetchUiState() parses pending_commands and phone_connected from server

TASK 5: lilly_ai.py updates ✅ COMPLETED ✅
- Created: /api/termui/local/proxy endpoint in lilly_phone_server.py
- Feature: Prefers local phone server over SSH when available
- Updated: get_ui_state() in lilly_ai.py to return pending_commands and phone_connected

ADDITIONAL COMPLETED WORK:
- Added bt_profiles.py for Bluetooth OUI vendor classification (radar feature)
- Sync raw resources: deployed lilly_phone_server.py and bt_profiles.py to res/raw/
- Reverted persona_configs.json from J.A.R.V.I.S. to simpler persona
- Fixed docker-compose.yml: host networking with SSH key mount
- Fixed setup_oauth.sh: corrected port from 3000 to 3002
- Fixed fix_vllm.sh: apt-get command fix
- Added lilly_sensor_engine.py for sensor data processing
- Added project documentation: AGENTS.md, FINAL_SETUP.md, OPENCODE_SETUP_SUMMARY.md, SETUP_COMPLETE.md
- Added deployment scripts: deploy-lilly.sh, phone-ssh-setup.sh, part1script.sh
- Added Kilo/OpenCode skills: termux-ssh, lilly-theme, repo-to-project
- Added opencode.json and deployments.json configuration

SUMMARY:
- Core phone server functionality: ✅ COMPLETE
- API endpoints for local proxy: ✅ COMPLETE
- Persona management: ✅ NEW FEATURE
- Local communication preference: ✅ COMPLETE
- Overlay app integration: ✅ COMPLETE
- Bluetooth device identification: ✅ COMPLETE

CURRENTLY DEPLOYABLE COMPONENTS:
- Phone Server (lilly_phone_server.py)
- Overlay App (LillyOverlayService.java with phone server auto-start)
- Persona System (lilly_persona_manager.py)
- Local Communication Preference (lilly_phone_server.py)

---
FILE: ./FINAL_SETUP.md
---
# Lilly AI OpenCode - Complete Setup

## Overview

This setup configures OpenCode for the Lilly AI project with:
- Termux SSH integration for Android phone control
- Git submodule management for multiple repositories
- Custom skills for SSH operations and theme customization
- Agents for Termux and deployment operations

## Files Created

### Core Configuration
| File | Purpose |
|------|---------|
| `opencode.json` | Main OpenCode configuration |
| `AGENTS.md` | Project documentation and agent instructions |
| `~/.ssh/config` | SSH configuration for quick connections |

### Skills
| File | Purpose |
|------|---------|
| `.opencode/skills/termux-ssh/SKILL.md` | SSH operations and phone management |
| `.opencode/skills/lilly-theme/SKILL.md` | Theme customization and popout UI |

### Agents
| File | Purpose |
|------|---------|
| `.opencode/agents/termux-agent.md` | Termux operations agent |

### Scripts
| File | Purpose |
|------|---------|
| `setup-termux-ssh.sh` | Host-side SSH setup |
| `phone-ssh-setup.sh` | Phone-side SSH setup (run on phone) |
| `connect-termux.sh` | Quick SSH connection |
| `test-termux-ssh.sh` | Test SSH connectivity |
| `deploy-lilly.sh` | Deploy Lilly AI system |

### Documentation
| File | Purpose |
|------|---------|
| `SETUP_COMPLETE.md` | Complete setup guide |
| `OPENCODE_SETUP_SUMMARY.md` | Configuration summary |

## Git Submodules

| Submodule | Repository | Purpose |
|-----------|------------|---------|
| `PrivateCode` | Legorobotdude/PrivateCode | Benchmarks and tests |
| `open-connector` | oomol-lab/open-connector | Email/calendar API |
| `hitomi-android` | Decentricity/hitomi-android | Android app |

## Quick Start

### 1. Set Up SSH Connection

```bash
# On host machine
./setup-termux-ssh.sh

# On phone (in Termux)
./phone-ssh-setup.sh
```

### 2. Test Connection

```bash
# Quick connect
./connect-termux.sh

# Or test connectivity
./test-termux-ssh.sh
```

### 3. Restart OpenCode

Quit and restart OpenCode to load the new configuration.

### 4. Use Commands

| Command | Description |
|---------|-------------|
| `termux-ssh` | Connect to Termux |
| `termux-status` | Check server status |
| `deploy-lilly` | Deploy Lilly AI |
| `setup-ssh` | Run SSH setup |
| `git-submodules` | Update submodules |

## SSH Configuration

### Environment Variables (from .env)
```bash
TERMUX_SSH_HOST=100.115.234.87
TERMUX_SSH_PORT=8022
TERMUX_SSH_USER=u0_a401
TERMUX_SSH_KEY=/home/labhrasd/Lilly_Workspace/Lilly_Workspace
```

### SSH Config (~/.ssh/config)
```
Host termux
    HostName 100.115.234.87
    Port 8022
    User u0_a401
    IdentityFile /home/labhrasd/Lilly_Workspace/Lilly_Workspace
```

## Troubleshooting

### SSH Issues
1. Check phone connectivity: `ping 100.115.234.87`
2. Verify SSH port: `nmap -p 8022 100.115.234.87`
3. Check key permissions: `ls -la ~/Lilly_Workspace/Lilly_Workspace`
4. Test with verbose: `ssh -v termux`

### OpenCode Issues
1. Verify JSON syntax: `python3 -c "import json; json.load(open('opencode.json'))"`
2. Check file structure: `find .opencode -type f`
3. Restart OpenCode after config changes

### Docker Issues
1. Check daemon: `docker ps`
2. View logs: `docker-compose logs -f`
3. Restart services: `docker-compose restart`

## Integration Points

- **Lilly Backend**: FastAPI on port 8098
- **Open Connector**: Node.js on port 3002
- **Termux Services**: Phone sensors
- **Android Overlay**: Floating UI
- **ML Pipeline**: Persona models

## Next Steps

1. Complete SSH setup with phone-side script
2. Start Termux AI server on phone
3. Deploy Lilly AI with `./deploy-lilly.sh`
4. Customize themes with lilly-theme skill
5. Manage git submodules as needed

---
FILE: ./OPENCODE_SETUP_SUMMARY.md
---
# OpenCode Configuration Summary

## Files Created

### 1. Main Configuration
- **`opencode.json`** - Main OpenCode configuration with:
  - Termux SSH settings from environment variables
  - Custom agents for Termux operations
  - MCP server for sensor data
  - Permission rules for SSH, Docker, and Git operations

### 2. Skills

#### Termux SSH Skill (`/.opencode/skills/termux-ssh/SKILL.md`)
- SSH connection management
- Remote command execution
- File transfer operations
- Phone sensor access
- Troubleshooting guides

#### Lilly Theme Skill (`/.opencode/skills/lilly-theme/SKILL.md`)
- Theme customization system
- Glass UI effects (blur, transparency)
- Popout interface management
- Avatar-specific themes
- Android overlay integration

### 3. Agents

#### Termux Agent (`/.opencode/agents/termux-agent.md`)
- Primary agent for Termux operations
- SSH connection handling
- Remote command execution
- Sensor data collection
- Server management

### 4. Documentation

#### AGENTS.md
- Project overview and architecture
- Development guidelines
- Common tasks and commands
- Security notes
- Troubleshooting guides

#### Test Script (`test-termux-ssh.sh`)
- SSH connection verification
- AI server status check
- Sensor server accessibility test

## Git Submodules Included

The project includes three git submodules:

1. **PrivateCode** (`https://github.com/Legorobotdude/PrivateCode.git`)
   - Contains benchmark and testing code

2. **open-connector** (`https://github.com/oomol-lab/open-connector.git`)
   - Node.js email/calendar API proxy
   - Runs on port 3002

3. **hitomi-android** (`https://github.com/Decentricity/hitomi-android.git`)
   - Android app integration layer

## Environment Variables (from .env)

```bash
# Termux SSH Configuration
TERMUX_SSH_HOST=100.115.234.87
TERMUX_SSH_PORT=8022
TERMUX_SSH_USER=u0_a401
TERMUX_SSH_KEY=/home/labhrasd/Lilly_Workspace/Lilly_Workspace

# Sensor Server
SENSOR_SERVER_URL=http://100.115.234.87:8099

# Open Connector
OPENCONNECTOR_ADMIN_TOKEN=...
OPENCONNECTOR_RUNTIME_TOKEN=...
OPENCONNECTOR_ENCRYPTION_KEY=...
```

## Usage

### Connect to Termux
```bash
ssh -i $TERMUX_SSH_KEY -p $TERMUX_SSH_PORT $TERMUX_SSH_USER@$TERMUX_SSH_HOST
```

### Test Connection
```bash
./test-termux-ssh.sh
```

### OpenCode Commands
- `termux-ssh` - Connect to Termux
- `termux-status` - Check server and sensor status
- `deploy-lilly` - Deploy Lilly AI system

## Next Steps

1. **Verify SSH Connection**: Ensure Termux is running and SSHD is started
2. **Start Sensor Server**: Run the sensor server on the phone
3. **Test Theme Customization**: Apply a theme via the overlay app
4. **Deploy Lilly AI**: Use Docker Compose to start all services

## Troubleshooting

### SSH Connection Issues
- Check Tailscale connection on both devices
- Verify SSHD is running in Termux: `sshd`
- Check SSH key permissions: `chmod 600 $TERMUX_SSH_KEY`
- Verify the key path in `.env` is correct

### Theme Issues
- Check overlay app permissions
- Verify theme files are in the correct location
- Restart the overlay service if needed

### Docker Issues
- Ensure Docker daemon is running
- Check Docker Compose logs: `docker-compose logs`
- Verify all services are healthy

## Integration Points

- **Lilly Backend**: FastAPI server on port 8098
- **Open Connector**: Node.js API on port 3002
- **Termux Services**: Phone sensors and camera
- **Android Overlay**: Floating UI windows
- **ML Pipeline**: LoRA fine-tuned persona models

---
FILE: ./PLAYBOOK.md
---
# Lilly Pup — Playbook

A curious digital companion who lives on your phone, senses the world through 23 Android sensors, reads your notifications, and helps you get things done — all through voice or text.

---

## Quick Start (New User — No SSH Required)

Lilly no longer needs SSH. Everything runs over HTTP from a single Termux server on your phone.

### Phone Setup (Termux)

```bash
# 1. Install Termux from F-Droid (not Play Store)
pkg install termux-api python -y
pip install fastapi uvicorn httpx

# 2. Copy termux_sensor_server.py to your phone

# 3. Start the sensor server
python termux_sensor_server.py --port 8099
```

### Avatar Server

```bash
# Clone + install
pip install -r requirements.txt

# Point to your phone (or localhost if on same device)
export SENSOR_SERVER_URL=http://<phone-ip>:8099

# Start
python lilly_ai.py
```

That's it. No SSH keys, no `sshd`, no `TERMUX_SSH_HOST`. The sensor server handles sensors, notifications, shell commands, and app launching over plain HTTP.

**If you also want voice (microphone):** Set up Termux:SSH for background mic streaming. Everything else works without it.

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                       DOCKER / PC                               │
│                                                                 │
│  lilly_ai.py (:8098)                                            │
│   ├── FastAPI server (all AI, routes, memory)                   │
│   ├── llama.cpp / Ollama backend                                │
│   ├── Piper TTS (12 voice profiles)                             │
│   ├── 9 avatar personas (hive mind team)                        │
│   └── Notification priority engine                              │
│                                                                 │
│  open-connector (:3002)                                         │
│   └── Gmail / Outlook / Calendar API (optional OAuth)           │
└──────────────┬──────────────────────────────────────────────────┘
               │ HTTP (no SSH needed)
┌──────────────▼──────────────────────────────────────────────────┐
│                    ANDROID PHONE                                │
│                                                                 │
│  termux_sensor_server.py (:8099)                                │
│   ├── /sensors/all        23 sensors (light, accel, gyro, etc) │
│   ├── /notification/list  Android notifications (OS priority)  │
│   ├── /shell              Run any shell command                  │
│   ├── /battery            Battery status                        │
│   ├── /location           GPS location                          │
│   └── /bluetooth/scan     Bluetooth device discovery            │
│                                                                 │
│  Termux:API                                                     │
│   ├── termux-sensor       Raw sensor access                     │
│   ├── termux-notification Read/listen to notifications          │
│   ├── termux-open         Open URLs / apps                      │
│   └── termux-bluetooth    Bluetooth scanning                    │
└─────────────────────────────────────────────────────────────────┘
```

---

## The 9 Avatars (Hive Mind)

| Avatar | Role | Strengths | TTS Voice |
|--------|------|-----------|-----------|
| 🐶 **Puppy** (Lilly) | Alpha Companion | Conversation, memory, emotional intelligence | Normal pace, warm |
| 🦊 **Fox** | Creative Strategist | Creative writing, storytelling, brainstorming | Fast, high-pitch, very animated |
| 🐱 **Cat** | Precision Analyst | Data analysis, code review, fact-checking | Normal, flat, crisp |
| 🐻 **Bear** | Steadfast Guardian | Scheduling, reminders, practical advice | Slow, deep, calm |
| 🐰 **Bunny** | Energetic Scout | Real-time monitoring, alerts, notifications | Very fast, high-pitch, animated |
| 🦉 **Owl** | Wisdom Keeper | Deep analysis, long-term planning | Very slow, low-pitch, flat |
| 🦌 **Deer** | Gentle Healer | Emotional support, wellness, meditation | Normal, warm-breathy |
| 🐺 **Wolf** | Fierce Protector | Security, threat assessment, decisive action | Fast, low-pitch, mid-animated |
| 🦝 **Raccoon** | Tech Tinkerer | Coding, hacking, gadgets, troubleshooting | Fast, medium-pitch, animated |

All 9 share the same body (phone) and sensors but have distinct personalities. They can defer tasks to each other.

---

## Voice Commands

### Apps (with PiP mode)

| Say | Action |
|-----|--------|
| "open youtube" / "watch..." | Opens YouTube in Picture-in-Picture |
| "open youtube in pip" | Same — YouTube always opens in PiP |
| "open camera" | Opens camera app |
| "open maps" / "navigate to..." | Google Maps |
| "open gmail" / "check email" | Gmail |
| "open music" / "play music" | YouTube Music |
| "open settings" / "calculator" / "calendar" | Respective apps |
| "google..." / "search for..." | Web search |
| "skip ad" | Taps skip button coordinates |

### Phone Navigation

| Say | Action |
|-----|--------|
| "go home" / "home" | Home screen |
| "go back" / "back" | Back button |
| "recent apps" / "switch apps" | App switcher |
| "notifications" / "pull down" | Notification panel |
| "quick settings" | Quick settings panel |
| "screenshot" | Screenshot |
| "volume up/down" / "louder/quieter" | Volume control |
| "dark mode" / "light mode" | Theme toggle |
| "power menu" | Power options |

### Notifications

| Say | Action |
|-----|--------|
| "what notifications" / "any alerts" | Lists active notifications by priority |
| "what did I miss" / "what's new" | Same — ordered OS-first, then app priority |
| "check my notifications" | Full notification inbox |
| "dismiss notifications" | Clear seen notifications |
| "read my emails" / "check my gmail" | Gmail inbox (needs OAuth) |

The avatar **proactively alerts** you about new notifications:
- **Tier 1 (OS):** Phone calls, SMS, system alerts
- **Tier 2 (Apps):** By priority (max > high > default > low > min)
- Each alert includes a **next-step suggestion** — "Want me to reply?", "Want me to call them back?"

### Sensors & Environment

| Say | Response |
|-----|----------|
| "what do you sense" | Full sensor narrative |
| "how bright is it" / "light" | Ambient light level |
| "what's the temperature" | Temperature readings |
| "what's the pressure" | Barometer |
| "am I moving" / "accelerometer" | Motion data |
| "compass" / "which way am I facing" | Magnetometer |
| "what's my step count" / "steps" | Pedometer |
| "how much battery" / "charge" | Battery percentage |
| "where are we" | GPS location |
| "what's the weather" | Current weather (pressure trend) |
| "is it raining" | Weather + pressure check |
| "who's near me" / "bluetooth" | Bluetooth device scan |

### Sensor-Triggered Skills

Background rules fire automatically when sensor conditions are met:

| Skill | Trigger | When |
|-------|---------|------|
| Dark Room Alert | Light < 10 lux | "It's gotten really dark..." |
| Phone Pickup | Pickup sensor > 0 | "I felt you pick up the phone!" |
| Vehicle Motion | Significant motion | "Feels like we're moving fast..." |
| Bright Light | Light > 1000 lux | "Wow, it's really bright!" |

Customize via `sensor_skills.json` or `GET/POST/DELETE /api/sensor_skills`.

### Stories & Imagination

| Say | Response |
|-----|----------|
| "tell me a story" | Real sensor-grounded story |
| "describe your world" | What Lilly senses right now |
| "paint a picture" | Poetic sensor description |
| "weave a tale" | Imaginative narrative using real data |

### Activity Tracking

| Say | Action |
|-----|--------|
| "start a walk" / "go for a walk" | Tracks walk |
| "start a run" | Tracks run |
| "start a bike ride" | Tracks cycling |
| "start a drive" | Tracks driving |
| "stop tracking" / "I'm done" | Saves activity |
| "activity status" | Current speed/distance |

### Teaching & Memory

| Say | What Lilly learns |
|-----|-------------------|
| "this is called [name]" | Saves sensor signature with label |
| "we're at [place]" | Labels current GPS location |
| "name device [MAC] as [name]" | Bluetooth device nickname |

---

## Picture-in-Picture (PiP)

YouTube automatically opens in Picture-in-Picture mode (Android 12+ via `--activity-picture-in-picture`, older versions via home-key simulation). You stay on the avatar UI while watching.

```json
{
  "youtube": {
    "package": "com.google.android.youtube",
    "pip": true
  }
}
```

To add PiP to other skills, set `"pip": true` in `lilly_skills.json`.

---

## Notification Priority System

Notifications are scored and alerted in order:

1. **OS Tier** — Phone calls, SMS, system dialogs
2. **App Priority** — Max > High > Default > Low > Min

Each alert includes a contextual next-step action based on the notification type.

**Proactive context:** When you talk to the avatar, it automatically sees the top 3 pending notifications and may mention them.

---

## Kid Mode (Socratic Learning)

**Toggle:** Say "kid mode on/off" or tap the avatar's nose 3 times.

In kid mode, the avatar never gives direct answers — only guiding questions and hints grounded in real sensor data.

| Kid asks | Avatar responds |
|----------|----------------|
| "What is gravity?" | "Have you ever dropped something and watched it fall? What do you think makes it go down?" |
| "How do birds fly?" | "Have you seen a bird flap its wings? What do you think those wings are doing?" |
| "Why is the sky blue?" | "Have you noticed the sky looks different at sunset? What colors do you see then?" |

---

## API Endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/chat` | POST | Send message, get response |
| `/api/notifications` | GET | Active notification list |
| `/api/sensor_skills` | GET/POST/DELETE | Manage sensor-triggered skills |
| `/api/lilly_skills` | GET | Available skills |
| `/api/sensors` | GET | Latest sensor readings |
| `/api/health` | GET | Service health |
| `/api/toggle_mic` | POST | Enable/disable mic |
| `/api/switch_avatar` | POST | Switch character |
| `/api/phone_status` | GET | Phone connectivity status |
| `/api/google/gmail` | GET | Gmail inbox (with OAuth) |
| `/api/google/calendar` | GET | Calendar events (with OAuth) |

---

## Environment Variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `SENSOR_SERVER_URL` | `http://100.115.234.87:8099` | Phone sensor server |
| `TERMUX_SSH_HOST` | *(empty)* | SSH host (optional — mic only) |
| `TERMUX_SSH_PORT` | `8022` | SSH port |
| `TERMUX_SSH_USER` | *(empty)* | SSH username |
| `OLLAMA_URL` | `http://100.93.131.114:11434` | Ollama backend |
| `PREFER_BACKEND` | `ollama` | `ollama` or `llama` |

---

## Files

| File | Purpose |
|------|---------|
| `lilly_ai.py` | Main application (12,500+ lines) |
| `termux_sensor_server.py` | Phone sensor server (HTTP) |
| `lilly_skills.json` | 35+ Android skill definitions |
| `sensor_skills.json` | 5 sensor-triggered reactive skills |
| `email_integration.py` | Gmail/Outlook/Calendar |
| `clerk_auth.py` | Clerk.com OAuth |
| `requirements.txt` | Python dependencies |
| `Dockerfile` | Multi-stage Docker build |
| `docker-compose.yml` | Docker Compose config |

---

## Troubleshooting

### "No sensor data"
Check the sensor server is running: `curl http://<phone-ip>:8099/health`

### "App won't launch"
If SSH is not set up, the sensor server's `/shell` endpoint is used automatically. Make sure `termux-open-url` is installed.

### "No notifications"
Ensure `termux-notification-list` is installed and Notification Listener permission is granted:
```
pkg install termux-api
# Then enable in Android Settings > Apps > Termux > Notification Listener
```

### "Mic not working"
Background mic requires SSH. Use the browser's built-in mic (click the mic button in the UI) as a no-SSH alternative.

### "TTS not speaking"
Check Piper voices exist at `lillyos/voices/*.onnx`. Run `pip install piper-tts` if missing.

---

## Moods

The avatar's current mood shows in the UI badge:
- **calm** — default
- **curious** — asking questions
- **cheerful** — happy/excited
- **excited** — very enthusiastic
- **gentle** — soft/careful
- **worried** — concerned
- **sad** — unfortunate events

---

## Tips

- Memory persists across sessions (last 20 exchanges)
- Camera vision: "what do you see" triggers photo + description
- Bluetooth devices are saved with friendly names you teach
- Lilly has opinions — ask about storms, magnets, constellations
- Sensor data updates in real-time (every 2 seconds)
- All 9 avatars can be active; switch anytime

---
FILE: ./PrivateCode/README.md
---
# Vibecoder.gg (Local LLM Coding Assistant)

[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](./LICENSE)

A terminal-based coding assistant that uses local Large Language Models (LLMs) via Ollama to provide coding help and answer programming questions. The assistant can also search the web for information while maintaining privacy, edit files, and execute commands with user confirmation.

## Features

- Interactive terminal interface
- Context-aware coding assistance
- File inclusion for code context
- Partial file reading with line range specification
- Web search capabilities with DuckDuckGo
- URL content extraction for reference
- Intelligent file editing with diff preview
- Safe command execution with LLM suggestions
- Create new files with a simple command
- Create and edit new files in one step
- Persistent conversation history
- Locally hosted LLM (no data sent to external services)
- AI thinking blocks with configurable display options
- Project planning with step-by-step execution (plan/vibecode feature)

## Prerequisites

- Python 3.6+
- [Ollama](https://ollama.ai/) installed and running locally
- A code LLM model pulled in Ollama (e.g., codellama, llama2, mixtral)
- (optional) Internet connection (for web search functionality)

## Installation

1. Clone this repository:
   ```
   git clone https://github.com/Legorobotdude/local-llm-coding-assistant.git
   cd local-llm-coding-assistant
   ```

2. Install dependencies:
   ```
   pip install -r requirements.txt
   ```

3. Make sure Ollama is running on your system:
   ```
   # On Windows, Ollama should be running in the background
   # You can check its status in the system tray
   ```

4. Pull a code-focused LLM if you haven't already:
   ```
   ollama pull codellama
   ```

## Usage

1. Run the coding assistant:
   ```
   python code_assistant.py
   ```

2. Enter your coding questions and include file paths in square brackets.
   
   Example:
   ```
   > What does this function do? [code_assistant.py]
   ```
   
   You can include multiple files:
   ```
   > How can I improve these two files? [file1.py] [file2.py]
   ```

   You can specify line ranges to read only parts of a file:
   ```
   > What does this function do? [code_assistant.py:100-150]
   > Check lines 20-30 of [example.py:20-30]
   > Show me from line 50 onwards [file.py:50-]
   > Show me up to line 25 [file.py:-25]
   > What's on line 42? [file.py:42]
   ```

3. Use web search by prefixing your query with `search:` or `search `:
   ```
   > search: Python requests library documentation
   > search Python requests library documentation
   ```

4. Combine web search with file context:
   ```
   > search: How to optimize this function [example.py]
   ```

5. Include URLs in square brackets to fetch their content:
   ```
   > How to use the API described in [https://api.example.com/docs]
   ```

6. Edit files by prefixing your query with `edit:` or `edit `:
   ```
   > edit: [example.py] to add error handling to the parse_json function
   > edit [example.py] to add error handling to the parse_json function
   ```
   You'll see a diff of proposed changes and be asked to confirm before saving.

   If the file doesn't exist, you'll be prompted to create it:
   ```
   > edit: [new_file.py] to create a hello world function
   ```
   This will create the file (and any necessary directories) and then proceed with the edit.

7. Create new empty files by prefixing your query with `create:` or `create `:
   ```
   > create: [new_file.py]
   > create: [src/utils/helper.py]
   ```
   You'll be prompted to confirm before creating the file. If the file already exists, 
   you'll be asked if you want to overwrite it with an empty file.

8. Run commands by prefixing your query with `run:` or `run `:
   ```
   > run: the tests for this project
   > run the tests for this project
   ```
   The LLM will suggest a command based on your description, or you can specify:
   ```
   > run: 'python example.py'
   > run 'python example.py'
   ```
   
   You can include file context to help the LLM suggest more appropriate commands:
   ```
   > run: [main.py] to test this script
   ```
   
   You can also specify line ranges to focus on specific parts of files:
   ```
   > run: [main.py:50-100] to test this function
   ```
   
   All commands require confirmation before execution for safety.

9. Change models by prefixing your query with `model:` or `model `:
   ```
   > model: llama3
   > model codellama
   ```

10. Toggle AI thinking display with special commands:
   ```
   > thinking:on
   > thinking:off
   > thinking:length 2000
   ```

11. Adjust the timeout for LLM operations using the timeout command:
   ```
   > timeout:30
   > timeout:60
   > timeout:500
   ```
   Users with slower hardware or those using larger models may need to increase the timeout value to prevent operations from being cut off prematurely.

12. Combine all features as needed:
   ```
   > search: How to implement better error handling [search_test.py] [https://docs.python.org/3/library/]
   > What's wrong with this function? [buggy.py:25-50]
   > edit: [utils.py:100-150] to optimize the data processing function
   > run: 'python test.py' after looking at [test.py:10-30]
   > create: [new_module.py]
   ```

13. Use project planning for complex tasks:
   ```
   > plan: Create a Flask API with endpoints for user authentication
   > plan: Add unit tests for [app.py] functions
   > vibecode: Refactor the database connection in [db.py] to use connection pooling
   ```

14. Type `exit` to quit the application.

## Thinking Blocks Feature

The Thinking Blocks feature allows the AI assistant to include its reasoning process in responses while keeping it hidden by default. This helps maintain clean responses while providing the option to see the AI's thought process when needed.

### Thinking Blocks Commands

- `thinking:on` or `thinking on` - Show thinking blocks in responses
- `thinking:off` or `thinking off` - Hide thinking blocks in responses (default)
- `thinking:length N` or `thinking length N` - Set the maximum length of thinking blocks to N characters

### Example Usage

```
> thinking:on
Thinking display is now ON

> What is the factorial function?
🤖 Assistant:
<thinking>
The factorial function is a mathematical function that multiplies a number by all the positive integers less than it.
For example, factorial of 5 (written as 5!) is 5 × 4 × 3 × 2 × 1 = 120.
It's commonly used in combinatorics, probability, and other areas of mathematics.
</thinking>

The factorial function (denoted as n!) multiplies a positive integer by all positive integers less than it.

For example:
- 5! = 5 × 4 × 3 × 2 × 1 = 120
- 3! = 3 × 2 × 1 = 6
- 1! = 1
- 0! is defined as 1

It's commonly used in combinatorics, probability theory, and many other areas of mathematics and computer science.

> thinking:off
Thinking display is now OFF
```

## Partial File Reading Feature

The Partial File Reading feature allows you to specify line ranges when including files in your queries. This helps focus the AI's attention on specific parts of a file, which is particularly useful for large files or when you only need help with a specific function or section.

### Line Range Syntax

You can specify line ranges using the following syntax in square brackets:

- `[filename:start-end]` - Read lines from `start` to `end` (inclusive)
- `[filename:start-]` - Read lines from `start` to the end of the file
- `[filename:-end]` - Read lines from the beginning of the file to `end`
- `[filename:line]` - Read just the specified line

Line numbers are 1-indexed (the first line is line 1).

### Example Usage

```
> What does this function do? [code_assistant.py:100-150]
```
This reads only lines 100-150 of code_assistant.py and asks the AI about the function in that range.

```
> Check for bugs in the calculate_average function [math_utils.py:75-100]
```
This focuses the AI on just the calculate_average function in lines 75-100.

```
> Show me from line 50 onwards [file.py:50-]
```
This reads the file from line 50 to the end.

```
> Show me up to line 25 [file.py:-25]
```
This reads the file from the beginning to line 25.

```
> What's on line 42? [file.py:42]
```
This reads just line 42 of the file.

### Benefits

- **Reduced Token Usage**: By including only relevant parts of files, you use fewer tokens in the context window.
- **Focused Responses**: The AI can focus on specific sections without being distracted by irrelevant code.
- **Better Performance**: Processing smaller chunks of code can lead to more accurate and faster responses.
- **Easier Debugging**: You can target specific functions or code blocks that need attention.

## Safety Features

The assistant includes several safety measures:
- File edits always require user confirmation
- A backup is created before modifying any file (as filename.bak)
- Colored diffs show exactly what changes will be made
- Commands are checked against a list of safe prefixes
- Potentially dangerous commands trigger extra safety warnings
- All commands require explicit user confirmation

## Configuration

You can modify the default settings in the `code_assistant.py` file:

- `DEFAULT_MODEL`: Change the default Ollama model
- `MAX_SEARCH_RESULTS`: Adjust the number of search results included (default: 5)
- `MAX_URL_CONTENT_LENGTH`: Limit the amount of content fetched from URLs (default: 10000 characters)
- `SHOW_THINKING`: Control whether thinking blocks are shown (default: False)
- `MAX_THINKING_LENGTH`: Set the maximum length of thinking blocks (default: 5000 characters)
- `DEFAULT_TIMEOUT`: Set the default timeout value for LLM operations (default: 500 seconds)
- `SAFE_COMMAND_PREFIXES`: List of command prefixes considered safe to execute
- `DANGEROUS_COMMANDS`: List of potentially dangerous command elements that trigger warnings

## Notes

- The application automatically connects to the Ollama server running at `localhost:11434`.
- All processing happens locally on your machine, ensuring privacy.
- Web searches are conducted through DuckDuckGo, which doesn't track users.
- The conversation history is maintained for context but is not saved between sessions.
- URL content is filtered to extract useful text and truncated if too long.
- Command suggestions are based on your description and the files in your directory.
- Thinking blocks are hidden by default but can be shown with the `thinking:on` command.
- Partial file reading allows you to focus the LLM on specific parts of a file, which can help reduce token usage and get more targeted responses.
- The default timeout for LLM operations is 500 seconds, which can be adjusted using the `timeout:N` command if you're experiencing timeouts with larger models or complex queries.

## Privacy Considerations

This tool is designed with privacy in mind:
- Uses a local LLM through Ollama instead of cloud-based APIs
- Uses DuckDuckGo for web searches, which doesn't track users
- All processing happens locally on your machine
- No data is stored beyond the current session

## Project Structure

- `code_assistant.py`: Main program file
- `benchmark.py`: Performance benchmarking tool
- `requirements.txt`: Required Python packages
- `examples/`: Example files for demonstrating the assistant's capabilities
- `tests/`: Test suite for the project
  - `test_thinking_blocks.py`: Tests for the thinking blocks feature 

## File Creation Feature

The File Creation feature allows you to create new empty files with a simple command. This is useful when you want to start a new file from scratch or create placeholder files for a project structure.

### Usage

To create a new file, use the `create:` prefix followed by the file path in square brackets:

```
> create: [new_file.py]
```

You'll be prompted to confirm before the file is created:

```
Create 'new_file.py'? (y/n): y
Created 'new_file.py'.
```

If the file already exists, you'll be asked if you want to overwrite it:

```
File 'existing_file.py' already exists.
Overwrite with an empty file? (y/n): n
File creation cancelled for 'existing_file.py'.
```

You can create files in directories that don't exist yet, and the necessary directories will be created automatically:

```
> create: [src/utils/helper.py]
Create 'src/utils/helper.py'? (y/n): y
Created 'src/utils/helper.py'.
```

You can also create multiple files at once:

```
> create: [file1.py] [file2.py]
```

### Enhanced Edit Functionality

The Edit functionality has been enhanced to handle new files as well. If you try to edit a file that doesn't exist, you'll be prompted to create it:

```
> edit: [new_file.py] to add a hello world function
File 'new_file.py' doesn't exist. Create it? (y/n): y
Created 'new_file.py'.
```

This allows you to create and edit files in a single step, making it easier to start new files with content.

## Enhanced Run Functionality

The Run functionality has been enhanced to include file context when suggesting commands. This helps the LLM understand what you're trying to do and suggest more appropriate commands.

### Usage

To run a command with file context, include file paths in square brackets:

```
> run: [main.py] to test this script
```

The LLM will read the content of the file and suggest a command based on it:

```
🤖 Assistant:
Based on the content of main.py, I suggest running the script with Python:

python main.py

This will execute the script and display its output.

Suggested command:
python main.py

Run this command? (y/n): y
```

You can include multiple files to provide more context:

```
> run: [test_file.py] [main.py] to run tests on the main file
```

You can also specify line ranges to focus on specific parts of files:

```
> run: [main.py:50-100] to test this function
```

This is particularly useful when you want to run a specific test or function within a larger file.

### Safety Features

The Run functionality includes several safety measures:
- Commands are checked against a list of safe prefixes
- Potentially dangerous commands trigger extra safety warnings with specific reasons
- All commands require explicit user confirmation before execution
- Command output is displayed and added to the conversation history

## Plan/Vibecode Feature

The Plan/Vibecode feature allows you to create and execute project plans with the LLM. This is useful for breaking down complex tasks into smaller, executable steps.

### Usage

To create and execute a project plan, use the `plan:` or `vibecode:` prefix followed by a description of the plan:

```
> plan: Create a simple Python script that prints 'Hello, World!' and run it to verify
> vibecode: Build a basic web server with Node.js and Express
```

The LLM will break down your request into executable steps in a standardized JSON format. These steps can include:
- Creating files
- Writing code to files
- Editing existing files
- Running commands
- Verifying command outputs

Each step is displayed and requires confirmation before execution, giving you full control over the process.

You can include file context to help the LLM understand the existing code:
```
> plan: Add more error handling to this API endpoint [api.py]
```

The plan will be executed interactively, allowing you to:
- Review each step before execution
- Skip steps you don't want to execute
- Save the plan to a JSON file for later use

## License

This project is licensed under the MIT License (c) 2025 Aditya Bawankule. See the [LICENSE](./LICENSE) file for details.

---
FILE: ./PrivateCode/examples/README.md
---
# Examples for the Local LLM Coding Assistant

This directory contains example files for demonstrating and testing different features of the Local LLM Coding Assistant.

## Files

- **simple_hello.py**: A minimal example for basic code execution.
- **web_search_demo.py**: Demonstrates the web search capabilities with JSON parsing and API requests.
- **file_editing_demo.py**: Contains code that can be improved, demonstrating the file editing capabilities.
- **code_review_demo.py**: Example file with functions for code review demonstrations.
- **sample_text.txt**: A sample text file for file reading/writing operations.

## Usage

These files are intended to be used as examples when interacting with the coding assistant. For example:

```
How can I improve this code? [examples/file_editing_demo.py]
```

```
Help me understand this code. [examples/code_review_demo.py]
```

```
How can I implement the make_api_request function? [examples/web_search_demo.py]
```

## Running Tests

These are not part of the test suite. The actual tests are located in the `tests/` directory and can be run with pytest:

```
python -m pytest tests/
``` 
---
FILE: ./PrivateCode/tests/NEXT_STEPS.md
---
# Test Improvement Plan: Next Steps

## Phase 2: Edge Cases and Error Handling

Based on the test improvement plan, the next phase focuses on testing edge cases and error handling with a priority on file operation edge cases. This document outlines the specific tests to implement next.

### File Operation Edge Cases

#### 1. Permission Errors and Access Control
- Create tests for reading files with insufficient permissions
- Test writing to read-only files
- Test accessing files with different user/group permissions
- Test handling of locked files

#### 2. Extremely Large Files
- Test reading very large files (100MB+)
- Test file size limits and truncation behavior
- Test memory usage during large file operations
- Test performance degradation with large files

#### 3. Binary Files and Unusual Encodings
- Test handling of binary file content
- Test various file encodings (UTF-8, UTF-16, Latin-1, etc.)
- Test files with mixed encodings
- Test files with invalid encoding declarations
- Test handling of BOM (Byte Order Mark) in different encodings

#### 4. Disk Full Scenarios and I/O Errors
- Test behavior when disk is full during write operations
- Test handling of network drive disconnection during file operations
- Test behavior during corrupted file access
- Test recovery mechanisms after I/O errors

### Implementation Strategy

1. Create a new test file `tests/test_file_operations_edge_cases.py`
2. Use temporary files and directories with specific permissions and content
3. Mock file system behavior for extreme cases (disk full, etc.)
4. Use parameterized tests to cover multiple encodings efficiently
5. Add tests for recovery mechanisms and proper error reporting

### Testing Tools and Approaches

- Use `pytest` fixtures for setting up test environments
- Use `io` and `StringIO`/`BytesIO` for simulating files without actual disk operations
- Use mocking to simulate disk full scenarios
- Create test files with various encodings and sizes

## Timeline

1. Implement permission and access control tests (2-3 days)
2. Implement large file handling tests (2-3 days)
3. Implement binary and encoding tests (1-2 days)
4. Implement disk full and I/O error tests (2-3 days)
5. Review and refine tests (1-2 days)

## Expected Outcomes

- Improved handling of edge cases in file operations
- Better error messages and recovery mechanisms
- More robust code for handling unusual file types and errors
- Increased test coverage for file operations from current level to 85%+ 
---
FILE: ./PrivateCode/tests/README.md
---
# Test Suite for Local LLM Coding Assistant

This directory contains a comprehensive test suite for the Local LLM Coding Assistant. The tests are designed to validate different components of the assistant and provide metrics for performance evaluation.

## Test Components

The test suite includes the following components:

1. **Unit Tests:** Tests for individual functions and components
   - File operations (read/write/detect encoding)
   - Content extraction (from LLM responses)
   - Command execution and safety checks
   - Web search functionality
   - Ollama API interaction
     - Connection verification
     - Response handling
     - Comprehensive error handling (timeouts, connection issues, HTTP errors, JSON parsing)
   - Planning functionality
     - File content inclusion
     - Step execution
     - Error handling

2. **Benchmarks:** Performance tests to evaluate the assistant's capabilities
   - Response time measurement
   - Accuracy evaluation
   - Success rate tracking

## Running the Tests

### Prerequisites

Ensure you have all the required dependencies installed:

```
pip install -r ../requirements.txt
```

### Running Unit Tests

To run all unit tests:

```
python -m pytest tests/
```

To run specific test modules:

```
python -m pytest tests/test_file_operations.py
python -m pytest tests/test_content_extraction.py
python -m pytest tests/test_command_execution.py
python -m pytest tests/test_web_search.py
python -m pytest tests/test_ollama_api.py
python -m pytest tests/test_plan_file_content.py
```

To run tests with verbose output:

```
python -m pytest -v tests/
```

To run tests with code coverage report:

```
python -m pytest --cov=code_assistant tests/
```

### Running Benchmarks

The benchmark tool can be run with:

```
python benchmark.py --model codellama
```

Additional options:
- `--output filename.json`: Specify output file for results (default: benchmark_results.json)
- `--charts directory_name`: Specify directory for benchmark charts (default: benchmark_charts)
- `--run-tests`: Run pytest tests before benchmarking

## Benchmark Results

Benchmark results will be saved as a JSON file and include:
- Success rates for different task types
- Average response times
- Detailed information for each test case

Visualization charts will be generated in the specified output directory, showing:
- Success rate by task type
- Average time by task type
- Success vs time correlation

## Error Handling Tests

We have extensive test coverage for error handling, particularly for the `get_ollama_response` function which includes tests for:

1. **Network Errors**
   - Timeouts (ensuring appropriate timeout messages)
   - Connection errors (verifying helpful error messages)

2. **HTTP Errors**
   - 404 Not Found (model not found)
   - 400 Bad Request
   - 500 Internal Server Error
   - Other status codes

3. **Data Processing Errors**
   - JSON decode errors
   - Empty response content
   - Malformed responses

These tests ensure users receive precise, actionable error messages that help troubleshoot problems with the Ollama API integration.

## Testing the Planning Functionality

The planning functionality tests in `test_plan_file_content.py` verify:

1. **File Content Inclusion**: Tests that file contents referenced in planning queries are correctly included in the prompt.
2. **Multiple File Handling**: Tests that multiple files can be referenced and included in a single plan.
3. **Edit File Steps**: Tests that edit operations are correctly parsed and can be executed.
4. **Nonexistent File Handling**: Tests that the planner gracefully handles references to files that don't exist.

### Important Notes for Testing Planning Functionality

When testing the planning functionality, be aware that:

1. **Direct API Calls**: The `handle_plan_query` function makes direct calls to the Ollama API using `requests.post()` rather than using the `get_ollama_response` function for some operations. Make sure to mock both:
   ```python
   @patch('code_assistant.get_ollama_response')
   @patch('requests.post')
   def test_planning_function(mock_requests_post, mock_get_response):
       # Setup mock responses
       mock_requests_post.return_value = MagicMock(status_code=200, ...)
   ```

2. **Multiple User Inputs**: Plan execution requires multiple user confirmations. Be sure to provide enough values for all prompts:
   ```python
   with patch('builtins.input', side_effect=['n', 'y', 'y', 'n']):
       # Execute test
   ```

## Adding New Tests

To add new test cases:
1. Create a new test file in the `tests/` directory
2. Follow the pytest pattern for test functions
3. Update the benchmark.py file if needed to include new benchmark categories

For benchmark tests, add new entries to the respective test case lists in the Benchmarker class methods. 
---
FILE: ./PrivateCode/tests/TEST_IMPROVEMENT_PLAN.md
---
# Test Coverage Improvement Plan

## Current Status
- Current code coverage: 78% (improved from 75%)
- Made significant improvements in file operations, command execution, planning functionality, web search functionality, handling of edge cases, and model switching
- Added 100+ new test cases across the codebase

## Completed Improvements
- **Command Execution**: Added tests for safety mechanisms, command parsing, and special handlers
- **File Operations**: Added tests for encoding detection, line range reading, and error handling
- **Planning Functionality**: Added tests for plan generation, JSON extraction, and step execution
- **Web Search Functionality**: Added extensive tests for content extraction, mocking external APIs, error handling, and result processing
- **File Operation Edge Cases**: Added comprehensive tests for permissions, binary files, encodings, large files, and I/O errors
- **Encoding Detection**: Enhanced UTF-32 encoding detection logic and added tests
- **Model Switching**: Expanded tests for model selection, unavailable models, error handling, and proposed fallback mechanisms

## Areas Needing Further Coverage

### 1. ✅ Web Search Functionality (High Priority) - COMPLETED
- ✅ Added tests for web search content extraction
- ✅ Implemented mocking of external API calls
- ✅ Added tests for error handling and timeout scenarios
- ✅ Created tests for search result processing and formatting
- Created comprehensive test suite in `test_web_search_extended.py` with 10 additional test cases

### 2. ✅ File Operation Edge Cases (Medium Priority) - COMPLETED
- ✅ Added tests for permission errors and access control
- ✅ Added tests for behavior with extremely large files
- ✅ Added tests for handling of binary files and unusual encodings
- ✅ Added tests for disk full scenarios and other I/O errors
- Created comprehensive test suite in `test_file_operations_edge_cases.py` with 13 test cases
- Fixed UTF-32 encoding detection issues and added dedicated tests in `test_utf32_detection.py`

### 3. ✅ Model Switching (Medium Priority) - COMPLETED
- ✅ Added tests for model selection logic
- ✅ Added tests for handling of unavailable models
- ✅ Added tests for error handling with different models
- ✅ Added proposals for model fallback functionality
- Expanded `test_model_switching.py` with 7 additional test cases
- Created new `test_model_fallback.py` with proposed enhancements for fallback behavior

### 4. Interactive CLI Features (Low Priority)
- Test the interactive features of the CLI
- Test user input handling and validation
- Test display formatting and color handling
- Test terminal size adaptations

## Testing Strategy

### Improved Mocking
- Create reusable mock fixtures for external dependencies
- Use filesystem virtualization where appropriate
- Mock HTTP responses with realistic test data

### Test Organization
- Continue organizing tests by functionality
- Use parameterized tests to cover multiple scenarios efficiently
- Employ property-based testing for complex inputs

### Edge Cases
- Test handling of very large inputs
- Test Unicode and special character handling
- Test resource exhaustion scenarios
- Test concurrency and timeout handling

## Implementation Plan

### Phase 1: Web Search Testing (COMPLETED ✅)
- Created `tests/test_web_search_extended.py`
- Implemented mocking of external search API responses
- Added tests for search query parsing and extraction
- Added tests for result processing and integration
- Achieved comprehensive coverage of web search functionality

### Phase 2: Edge Cases and Error Handling (COMPLETED ✅)
- Created `tests/test_file_operations_edge_cases.py` to test file operation edge cases
- Created `tests/test_utf32_detection.py` to test UTF-32 encoding detection
- Improved encoding detection algorithm for UTF-32 and other formats
- Implemented tests for resource limitations and error conditions
- Added tests for exceptional inputs and boundary conditions

### Phase 3: Model Switching and CLI Features (IN PROGRESS ⚙️)
- ✅ Enhanced model switching tests in `tests/test_model_switching.py`
- ✅ Created `tests/test_model_fallback.py` for proposed fallback functionality
- Focus on interactive CLI features (next)
- Test terminal rendering and display
- Work toward 80%+ coverage target

## Monitoring and Maintenance

- Run coverage reports after significant code changes
- Add regression tests for any bugs discovered
- Continuously refine tests as the codebase evolves
- Prioritize testing for frequently changing components 
---
FILE: ./SETUP_COMPLETE.md
---
# Lilly AI OpenCode Setup Guide

## Quick Start

### 1. Set Up SSH Connection to Phone

Run the setup script to configure SSH access to your Termux phone:

```bash
./setup-termux-ssh.sh
```

Then run the phone-side script ON YOUR PHONE (in Termux):

```bash
./phone-ssh-setup.sh
```

### 2. Test SSH Connection

```bash
# Using the quick connect script
./connect-termux.sh

# Or using SSH config
ssh termux

# Or manually
ssh -i ~/Lilly_Workspace/Lilly_Workspace -p 8022 u0_a401@100.115.234.87
```

### 3. Restart OpenCode

After setting up the configuration, restart OpenCode to load the new settings:

```bash
# Quit and restart OpenCode
```

## Files Created

### Configuration Files
- `opencode.json` - Main OpenCode configuration
- `AGENTS.md` - Project documentation and agent instructions
- `~/.ssh/config` - SSH configuration for quick connections

### Skills
- `.opencode/skills/termux-ssh/SKILL.md` - SSH operations skill
- `.opencode/skills/lilly-theme/SKILL.md` - Theme and popout UI skill

### Agents
- `.opencode/agents/termux-agent.md` - Termux operations agent

### Scripts
- `setup-termux-ssh.sh` - Host-side SSH setup
- `phone-ssh-setup.sh` - Phone-side SSH setup
- `connect-termux.sh` - Quick connect script
- `test-termux-ssh.sh` - Connection test script

## Git Submodules

The workspace includes three git submodules:

1. **PrivateCode** (`./PrivateCode`)
   - Repository: https://github.com/Legorobotdude/PrivateCode.git
   - Purpose: Benchmarks and testing code

2. **open-connector** (`./open-connector`)
   - Repository: https://github.com/oomol-lab/open-connector.git
   - Purpose: Node.js email/calendar API proxy

3. **hitomi-android** (`./hitomi-android`)
   - Repository: https://github.com/Decentricity/hitomi-android.git
   - Purpose: Hitomi Android application

### Managing Submodules

```bash
# Initialize submodules
git submodule init

# Update submodules
git submodule update --remote

# Check status
git submodule status

# Update from within a submodule
cd open-connector && git pull origin main
```

## OpenCode Commands

After setup, you can use these commands:

| Command | Description |
|---------|-------------|
| `termux-ssh` | Connect to Termux via SSH |
| `termux-status` | Check server and sensor status |
| `deploy-lilly` | Deploy Lilly AI system |
| `setup-ssh` | Run SSH setup script |
| `git-submodules` | Update git submodules |
| `list-repos` | List all repositories |

## Environment Variables

Key environment variables (from `.env`):

```bash
# Termux SSH
TERMUX_SSH_HOST=100.115.234.87
TERMUX_SSH_PORT=8022
TERMUX_SSH_USER=u0_a401
TERMUX_SSH_KEY=/home/labhrasd/Lilly_Workspace/Lilly_Workspace

# Sensor Server
SENSOR_SERVER_URL=http://100.115.234.87:8099

# Open Connector
OPENCONNECTOR_ADMIN_TOKEN=...
OPENCONNECTOR_RUNTIME_TOKEN=...
```

## Troubleshooting

### SSH Connection Issues

1. **Check phone connectivity**
   ```bash
   ping 100.115.234.87
   ```

2. **Check SSH port**
   ```bash
   nmap -p 8022 100.115.234.87
   ```

3. **Verify key permissions**
   ```bash
   ls -la ~/Lilly_Workspace/Lilly_Workspace
   # Should be 600 (-rw-------)
   ```

4. **Test SSH with verbose output**
   ```bash
   ssh -v -i ~/Lilly_Workspace/Lilly_Workspace -p 8022 u0_a401@100.115.234.87
   ```

### Theme/Popout Issues

1. **Check overlay app permissions**
2. **Verify theme files are in correct location**
3. **Restart overlay service on phone**

### Docker Issues

1. **Check Docker daemon**
   ```bash
   docker ps
   ```

2. **View logs**
   ```bash
   docker-compose logs -f
   ```

## Integration Points

- **Lilly Backend**: FastAPI server on port 8098
- **Open Connector**: Node.js API on port 3002
- **Termux Services**: Phone sensors and camera
- **Android Overlay**: Floating UI windows
- **ML Pipeline**: LoRA fine-tuned persona models

## Next Steps

1. Complete SSH setup by running the phone-side script
2. Test the connection with `./test-termux-ssh.sh`
3. Start the Termux AI server on the phone
4. Deploy Lilly AI with `./deploy-lilly.sh`
5. Customize themes with the lilly-theme skill

---
FILE: ./docs/OAUTH_SETUP.md
---
# OAuth Setup Guide for Gmail & Outlook Integration

## Gmail OAuth Setup

### Step 1: Create Google Cloud Project
1. Go to [Google Cloud Console](https://console.cloud.google.com)
2. Click "Select a project" → "New Project"
3. Name: `lilly-ai-email` → Create

### Step 2: Enable Gmail API
1. Go to "APIs & Services" → "Library"
2. Search for "Gmail API"
3. Click "Enable"

### Step 3: Create OAuth 2.0 Credentials
1. Go to "APIs & Services" → "Credentials"
2. Click "Create Credentials" → "OAuth client ID"
3. Application type: `Web application`
4. Name: `Lilly AI Gmail`
5. Authorized redirect URIs:
   - `http://localhost:3000/oauth/callback`
   - Add your production URL if deploying remotely
6. Click "Create"
7. **Save the Client ID and Client Secret**

### Step 4: Configure OAuth Consent Screen
1. Go to "APIs & Services" → "OAuth consent screen"
2. User type: `External` (or `Internal` for Workspace)
3. App name: `Lilly AI`
4. User support email: your email
5. Developer contact: your email
6. Save and continue through scopes and test users

### Step 5: Required Gmail Scopes
Add these scopes to your OAuth consent screen:
```
https://www.googleapis.com/auth/gmail.readonly
https://www.googleapis.com/auth/gmail.send
https://www.googleapis.com/auth/gmail.compose
https://www.googleapis.com/auth/gmail.modify
https://www.googleapis.com/auth/gmail.labels
https://www.googleapis.com/auth/gmail.settings.basic
https://www.googleapis.com/auth/gmail.settings.sharing
```

---

## Outlook OAuth Setup

### Step 1: Register Application in Azure
1. Go to [Azure Portal](https://portal.azure.com)
2. Navigate to "Azure Active Directory" → "App registrations"
3. Click "New registration"
4. Name: `Lilly AI Outlook`
5. Supported account types: Choose based on your needs
6. Redirect URI: `http://localhost:3000/oauth/callback`
7. Click "Register"
8. **Save the Application (client) ID**

### Step 2: Create Client Secret
1. Go to "Certificates & secrets"
2. Click "New client secret"
3. Description: `Lilly AI Secret`
4. Expires: Choose preference
5. Click "Add"
6. **Copy the secret value immediately (it won't be shown again)**

### Step 3: Configure API Permissions
1. Go to "API permissions" → "Add a permission"
2. Select "Microsoft Graph"
3. Choose "Delegated permissions"
4. Add these permissions:
   ```
   Mail.Read
   Mail.ReadWrite
   Mail.Send
   Calendars.Read
   Calendars.ReadWrite
   User.Read
   ```
5. Click "Grant admin consent" (if you're an admin)

### Step 4: Configure Authentication
1. Go to "Authentication"
2. Under "Advanced settings":
   - Allow public client flows: `No`
   - Supported account types: Based on your needs
3. Under "Web" → "Redirect URIs":
   - Ensure `http://localhost:3000/oauth/callback` is listed

---

## Environment Variables

Add to your `.env` file:

```bash
# Gmail OAuth
GMAIL_CLIENT_ID=123456789-abcdefg.apps.googleusercontent.com
GMAIL_CLIENT_SECRET=GOCSPX-your_client_secret_here

# Outlook OAuth
OUTLOOK_CLIENT_ID=12345678-abcd-efgh-ijkl-1234567890ab
OUTLOOK_CLIENT_SECRET=your_outlook_client_secret

# Open Connector Runtime Token (create in Open Connector UI)
OPENCONNECTOR_RUNTIME_TOKEN=oct_your_runtime_token_here
```

---

## Open Connector Setup

### Step 1: Start Open Connector
```bash
docker compose -f docker-compose.email.yml up -d connector
```

### Step 2: Access Open Connector UI
1. Open browser to `http://localhost:3000`
2. Go to Gmail provider page
3. Click "Configure OAuth Client"
4. Enter your Gmail Client ID and Client Secret
5. Click "Save OAuth Client"

### Step 3: Connect Gmail Account
1. Click "Connect Gmail"
2. Complete consent in browser
3. After redirect, Gmail is connected

### Step 4: Create Runtime Token
1. Go to "Access" page in Open Connector UI
2. Click "Create Token"
3. Name: `lilly-ai`
4. Copy the token (starts with `oct_`)
5. Add to `.env` as `OPENCONNECTOR_RUNTIME_TOKEN`

### Step 5: Repeat for Outlook
1. Go to Outlook provider page in Open Connector
2. Configure OAuth Client with Azure credentials
3. Connect Outlook account
4. The runtime token works for both providers

---

## Testing

### Test Gmail Connection
```bash
curl -s -X POST http://localhost:3000/v1/actions/gmail.get_profile \
  -H "Authorization: Bearer $OPENCONNECTOR_RUNTIME_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"input":{}}'
```

### Test Outlook Connection
```bash
curl -s -X POST http://localhost:3000/v1/actions/outlook.get_profile \
  -H "Authorization: Bearer $OPENCONNECTOR_RUNTIME_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"input":{}}'
```

---

## Security Notes

1. **Never commit secrets to git** - Use `.env` file and add to `.gitignore`
2. **Use HTTPS in production** - OAuth requires secure connections
3. **Rotate tokens periodically** - Open Connector UI allows token management
4. **Limit scopes** - Only request permissions you actually need
5. **Monitor API usage** - Google and Microsoft have quota limits

---
FILE: ./hitomi-android/AGENTS.md
---
# AGENTS.md - `hitomi-android`

This file is the handoff brief for future Codex/GPT instances working on the Hitomi Android app.

## 1) What this repo is

`hitomi-android` is the Android client app for Hitomi Companion:
- floating hedgehog overlay UI
- chat bubble + local interaction loop
- Supabase-authenticated cloud chat calls
- optional Termux shell bridge tooling

This app now uses a **separate branded login web frontend** (Hitomi Companion GitHub Pages), while still sharing backend infrastructure with Agent1c.

## 2) Cross-repo map (critical)

From this repo (`/home/decentricity/hitomi-android`):
- Web auth frontend repo is at: `../hitomicompanion.github.io`
- Agent1c.ai web OS repo is at: `../agent1c-ai.github.io`
- Agent1c.me web OS repo is at: `../agent1c-me.github.io`

From `../hitomicompanion.github.io` back to this app:
- Android app repo is: `../hitomi-android`

## 3) Current implemented auth architecture

### 3.1 Android app side (this repo)

Primary auth manager:
- `app/src/hosted/java/com/example/test/HitomiAuthManager.java` (hosted flavor)
- `app/src/open/java/com/example/test/HitomiAuthManager.java` (open flavor — local BYOK only)

Auth flow (hosted flavor):
- Direct Google OAuth via PKCE: `HitomiAuthManager.buildOAuthUrl("google")`
- Opens browser to Supabase `/auth/v1/authorize?provider=google`
- Deep-link callback: `hitomicompanion://auth/oauth` (legacy `agent1cai://auth/oauth` also accepted)
- PKCE code exchange: `completePkceCodeExchange(code)` → stores Supabase tokens in SharedPreferences

The previous web-based pairing/handoff workflow (`buildWebAuthLaunchUrl`, `android-auth-handoff` function) has been removed. Authentication is now direct OAuth from the app.

Deep-link support in app:
- primary scheme: `hitomicompanion://auth/callback` (+ `/oauth`)
- legacy compatibility scheme: `agent1cai://auth/callback` (+ `/oauth`)

Manifest intent filters:
- `app/src/hosted/AndroidManifest.xml` includes both schemes.

### 3.3 Supabase shared backend

Project:
- `gkfhxhrleuauhnuewfmw`

Functions used by Android:
- `xai-chat`

Note: `android-auth-handoff` function is no longer used. Auth is now direct OAuth.

## 4) What has already been built (important for continuity)

- Version bump to `0.1.2` in progress.
  - `app/build.gradle`: `versionCode 6`, `versionName "0.1.2"`
- Pairing/handing-off workflow removed — auth is now direct Google OAuth from the app.
- Backward-compatible callback handling retained (legacy `agent1cai://...` still accepted).
- Download link button added to settings UI (links to GitHub releases).
- "Sign in with Google" button added as primary auth entry point.
- iOS-oriented web-login click hardening exists on web side (`click + pointer/touch` handling).

## 5) Non-obvious watchouts from real failures

### 5.1 Supabase provider naming

Do not use `"twitter"` as OAuth provider when calling Supabase OAuth start.
- Use provider `"x"` in modern flows.
- Old naming caused `Unsupported provider: provider is not enabled`.

### 5.2 Redirect/allowlist drift

If login appears to work but returns to wrong site or fails callback:
- verify Supabase Auth redirect allowlist includes `https://hitomicompanion.github.io/`
- verify deep-link callback scheme in Android and web repo match.

### 5.3 xAI chat 401 trap (critical)

For `xai-chat` edge function in this Supabase project:
- **Verify JWT with legacy secret MUST stay OFF**
- Supabase UI can silently flip this ON during edits/deploys
- When ON, app frequently breaks with 401 in chat path

Always check it after touching function settings/deploys.

### 5.4 GitHub Pages publish trap

`hitomicompanion.github.io` can 404 if GitHub Pages source branch is not set.
- Must be configured to deploy from `master` (root) for this repo layout.

### 5.5 Android build environment trap

Observed local issue:
- system JDK lacked `jlink`, causing Gradle compile failure.

Working fallback used:
- `JAVA_HOME=/home/decentricity/jdk-21-temurin`
- then run Gradle with that JAVA_HOME.

## 6) Release/build procedure (known-good)

From this repo root:

```bash
cd /home/decentricity/hitomi-android
JAVA_HOME=/home/decentricity/jdk-21-temurin PATH=/home/decentricity/jdk-21-temurin/bin:$PATH ./gradlew --no-daemon assembleDebug
```

Then copy artifact:

```bash
mkdir -p releases/0.1.0
cp app/build/outputs/apk/debug/app-debug.apk releases/0.1.0/hitomi-v0.1.0-debug.apk
sha256sum releases/0.1.0/hitomi-v0.1.0-debug.apk > releases/0.1.0/SHA256SUMS_APK.txt
```

## 7) Operator rollback / recovery

The previous web-based auth fallback (pointing between `agent1c.ai` and `hitomicompanion.github.io`) has been removed. Auth is now direct OAuth from the app.

To switch OAuth endpoint (e.g., if Supabase config changes):
- edit `HitomiAuthManager.java` (hosted flavor)
- modify `BACKEND_URL` and `OAUTH_REDIRECT_URI`
- rebuild APK

## 8) Code areas to treat carefully

- `app/src/hosted/java/com/example/test/HitomiAuthManager.java`
  - OAuth URL builder, PKCE code exchange, callback parser, token refresh, user profile
- `app/src/open/java/com/example/test/HitomiAuthManager.java`
  - Local BYOK auth (Ollama endpoint or xAI API key)
- `MainActivity.java`
  - auth launch path, Google sign-in button, download link button, callback handling entrypoints
- `HedgehogOverlayService.java`
  - overlay lifecycle + browser snapshot + voice behavior + tool execution
- `HitomiCloudChatClient.java`
  - cloud call boundary to Supabase function or direct Ollama/xAI

Do not refactor these casually without end-to-end login + chat testing.

## 8.1) Existing code debt/watchouts in source

- Java source folder is still `app/src/main/java/com/example/test/...` while package name is `ai.agent1c.hitomi`.
  - This works, but can confuse tooling and future refactors.
- Some UI copy in `activity_main.xml` still references `Agent1c.ai` wording.
  - Functional but inconsistent with Hitomi Companion branding.
- Repo contains legacy release artifacts from earlier naming (`agent1c-hitomi-android-...`).
  - Keep for provenance unless explicitly cleaning release history.

## 9) Recommended next steps

1. Complete Android post-login continuity validation:
   - fresh install -> Google sign-in -> OAuth callback -> chat
   - resume from cold start with existing session
2. Add release signing path (AAB/APK) for distribution readiness.
3. Add explicit auth diagnostics screen in-app:
   - current provider
   - callback URI seen
   - token expiry
4. Test the download link button opens GitHub releases page correctly.
5. Add crash-safe analytics breadcrumbs around callback handling only (no sensitive payload logging).

## 10) Naming guardrail

Canonical domains:
- `agent1c.ai`
- `agent1c.me`
- `hitomicompanion.github.io` (for web auth frontend, if re-adopted)

Do not introduce `agentic.*` into this repo.

---
FILE: ./hitomi-android/BUILDING.md
---
# Building

This repo is built in two different environments:

1. `Ubuntu x86_64` on a normal server/workstation
2. `Termux -> Debian proot` on an Android phone

Do not assume one environment's build fixes apply to the other.

## Build Matrix

- `Ubuntu x86_64`
  - preferred environment for normal development and release-oriented builds
  - use standard Android SDK + Gradle flow
  - use `scripts/build-release-linux.sh` for signed AAB output
- `Termux -> Debian proot`
  - supported for local Android-on-Android debug and signed release builds
  - use `scripts/build-release-termux-proot.sh` for signed AAB output
  - requires using the Termux host `aapt2` binary
  - slower and more fragile than Ubuntu x86

## Before Any Build

1. Check `app/build.gradle` for the current `compileSdk`, `targetSdk`, `versionCode`, and `versionName`.
2. Install the matching Android SDK platform in the build environment.
3. For release builds, ensure `keystore.properties` exists at the repo root and points at a local keystore on that machine.
4. Use `./gradlew assembleDebug --no-daemon` unless you specifically need a release artifact.

## Ubuntu x86_64

Prereqs:

- JDK 17 or newer
- Android SDK command-line tools
- matching SDK platform installed for the current `compileSdk`
- matching build-tools installed

Typical flow:

```sh
export JAVA_HOME=/path/to/jdk
export ANDROID_HOME=/path/to/android-sdk
export ANDROID_SDK_ROOT=/path/to/android-sdk
./scripts/build-release-linux.sh
```

Notes:

- Keep `gradle.properties` free of Android/Termux-specific absolute paths.
- The Linux release script expects `keystore.properties` to be present locally and will fail fast if it is missing.

## Termux -> Debian Proot on Android

This environment uses the Debian clone of the repo but must rely on the Termux host `aapt2` binary:

- Termux host `aapt2` path:
  - `/data/data/com.termux/files/usr/bin/aapt2`

Typical flow inside Debian:

```sh
./scripts/build-release-termux-proot.sh
```

Important caveats:

- If AGP downloads an incompatible Linux tool, prefer the Termux-host override above rather than debugging Maven-downloaded binaries.
- If local Android builds start failing only after a `compileSdk` bump, verify the matching SDK platform is installed first.
- `compileSdk 35` may still be fragile in this environment when paired with the Termux-host `aapt2`. If Ubuntu x86 is available, prefer building there for higher-SDK changes.

## Output Paths

- Debug APK:
  - `app/build/outputs/apk/debug/app-debug.apk`
- Release AAB:
  - `app/build/outputs/bundle/release/app-release.aab`
- Versioned copied release AAB:
  - `releases/<version>/hitomi-v<version>-release-vc<versionCode>.aab`

## Agent Rule

If you are an agent working in this repo:

- inspect `app/build.gradle` before building
- inspect `gradle.properties` before building
- do not assume Termux/Android-specific paths exist on Ubuntu
- do not assume Ubuntu/x86 SDK behavior matches Debian-on-Android behavior
- when a build fix is environment-specific, document it here
- never commit `keystore.properties` or the keystore itself

---
FILE: ./hitomi-android/OPEN_HITOMI.md
---
# Open Hitomi Setup

Open Hitomi accepts either:

- A local or self-hosted Ollama-compatible endpoint
- An xAI API key

## Local Ollama

The simplest local endpoint is:

```text
http://127.0.0.1:11434
```

Open Hitomi will query Ollama for installed models and use the first available one. If no models are installed yet, pull one first. Example:

```sh
ollama pull qwen2.5:3b
```

Then paste `http://127.0.0.1:11434` into Open Hitomi and tap `>>`.

If your Ollama server is on another machine, paste that reachable `http://...` or `https://...` endpoint instead.

## xAI

If you prefer xAI, paste your API key into the same field and tap `>>`.

## Notes

- Open Hitomi stores the entered endpoint or API key locally on-device.
- Solana support is monitoring-only for public addresses.
- Open Hitomi is not a wallet, stores no local private keys, and does not sign or send transactions.

---
FILE: ./hitomi-android/README.md
---
# Open Hitomi

![Hitomi Android Screenshot](docs/hitomi-android-screenshot-latest.png)

Open Hitomi is an Android floating hedgehog assistant with a local overlay UI, browser tools, optional Termux bridge support, and bring-your-own-key chat.

License:
- `AGPL-3.0-or-later`
- Copyright `Decentricity`

The F-Droid-facing app variant in this repo is:
- `Open Hitomi`
  - package ID: `ai.agent1c.hitomi.open`
  - local API key entry on-device
  - intended long-term direction for BYOK and additional compatible local/remote model endpoints

## Building

Build instructions are environment-specific in this repo.

See `BUILDING.md` before running Gradle, especially if you are building either:
- on Ubuntu/x86_64
- inside Debian proot on Android/Termux

Convenience scripts:
- `scripts/build-release-linux.sh`
  - run on a normal Linux/x86_64 machine with Android SDK + JDK installed
  - builds a signed release AAB if `keystore.properties` is present
  - copies the result into `releases/<version>/`
- `scripts/build-release-termux-proot.sh`
  - run inside the Debian proot clone on the phone
  - applies the Termux-host `aapt2` override for this environment
  - builds a signed release AAB and copies it into `releases/<version>/`

For local debug APKs:
- `./gradlew assembleOpenDebug`
- `./gradlew assembleOpenRelease`

Typical output paths:
- open debug APK:
  - `app/build/outputs/apk/open/debug/app-open-debug.apk`
- open release APK:
  - `app/build/outputs/apk/open/release/`

## What The App Includes

- BeOS/HedgeyOS-inspired Android main screen styling
- Floating Hitomi hedgehog overlay as the main companion surface
- Signed-in welcome line that advertises browser, Termux, and Solana capabilities
- Clippy-style chat bubble with tail
- Local API key entry on-device for the open flavor
- Current open flavor targets bring-your-own-key Grok chat
- The architecture is intended to expand toward additional compatible endpoints, including local model routes in future work

- Long-press quick actions for settings, mic, and hide-to-edge
- Native Android STT always-listening mode
- Mic permission flow from the overlay into the main settings window
- Microphone/STT startup hardening and failure recovery
- Drag Hitomi to the bottom-center `X` target to close the floating overlay
- Hide-to-edge arc tab restore interaction
- Hidden-edge restore hit area and swipe restore polish
- Hidden edge tab now re-anchors correctly on rotation

- Hitomi Browser mini overlay in the same BeOS-style window family
- Hitomi-triggered browser open flow
- Browser-read page excerpt flow
- Visible stepped scrolling while Hitomi reads a page
- Particle stream effect linking Hitomi to the browser while browsing/reading
- Browser summon animation with first-spawn random placement near Hitomi
- Re-browse behavior reuses the existing Hitomi Browser window in place

- Green-on-black Terminal overlay window for visible Termux command/result transcripts
- Automatic terminal summon when Hitomi uses Termux
- Termux detection and command bridge for superuser use
- Termux package visibility fix so installed Termux, open, enable, and test controls appear reliably
- Copyable Termux setup command UX and friendlier setup guidance
- Termux-not-connected flow now opens the main settings window and tells the user how to reconnect
- Android Termux tool token support (`android_termux_exec`) with client-side blacklist

- Purple Solana wallet window with local wallet save flow
- Read-only Solana wallet overview and refresh tools

## App Workflow

### Overview

Open Hitomi is a floating Android companion (a hedgehog named Hitomi) that lives in a system-overlay window. It combines three integration layers:

1. **Local overlay UI** — floating windows managed by `HedgehogOverlayService` (a foreground service with `SYSTEM_ALERT_WINDOW` permission)
2. **Cloud/LLM chat** — `HitomiCloudChatClient` sends messages to either Supabase-hosted `xai-chat` (Grothendieck/Grok) or a local Ollama/xAI endpoint for the open variant
3. **Android tooling** — `TermuxCommandBridge`, `SolanaWalletClient`, and embedded browser provide on-device capabilities

### Startup & Auth Flow

```
[App Launch] → MainActivity.onCreate()
                 ├─ checks overlay permission (SYSTEM_ALERT_WINDOW)
                 ├─ if signed in → auto-launch overlay service + hide main window
                 └─ if not signed in → show main settings window

[Hosted flavor] Login button → opens browser to agent1c.ai or hitomicompanion.github.io
                                  → Google/magic-link sign-in on web
                                  → deep-link callback: hitomicompanion://auth/callback
                                  → HitomiAuthManager exchanges handoff code or PKCE code
                                  → stores Supabase access/refresh tokens in SharedPreferences

[Open flavor] Enter Ollama endpoint or xAI API key directly in-app (stored locally)
```

### Auth Methods

| Method | Flavor | How |
|--------|--------|-----|
| Google Sign-In | Hosted | Tap "Sign in with Google" → browser opens to Supabase OAuth → sign in with Google → deep-link callback with PKCE code exchange |
| X/Twitter Sign-In | Hosted | Via Supabase OAuth with `provider=x` |
| Magic Link | Hosted | Via Supabase email OTP |
| Local Ollama endpoint | Open | Paste `http://127.0.0.1:11434` or any Ollama-compatible URL |
| xAI API key | Open | Paste `xai-...` key directly |
| Download app | Hosted | "Download Hitomi" button in settings → opens GitHub releases page |

The previous web-based pairing workflow (browser to `agent1c.ai`, handoff code exchange) has been replaced with direct OAuth from the app.

### Overlay Architecture

```
HedgehogOverlayService (foreground service, FGS)
├─ WindowManager.addView() for each floating window:
│   ├─ hedgehogView (overlay_hedgehog.xml) — the main hedgehog icon + quick-action buttons
│   ├─ bubbleView (overlay_bubble.xml) — chat bubble with Clippy-style tail
│   ├─ browserView (overlay_browser.xml) — mini browser window
│   ├─ terminalView (overlay_terminal.xml) — Termux command/result transcript
│   ├─ solanaView (overlay_solana.xml) — purple Solana wallet window
│   └─ edgeTabView / exitTargetView — drag-to-edge and close target
├─ chatExecutor — single-thread executor for LLM calls
├─ chatClient — HitomiCloudChatClient (cloud or local LLM)
├─ termuxCommandBridge — TermuxCommandBridge (shell command execution)
├─ solanaWalletClient — SolanaWalletClient (read-only wallet RPC)
├─ speechRecognizer — Android STT always-listening
└─ Tool parsing: LLM emits {{tool:toolname|key=val}} tokens → parsed in parseAssistantReply()
```

### Chat / Skill Execution Pipeline

```
User types message in bubble → sendChatMessage()
  │
  ├─ appends user message to chatHistory (JSONArray, in-memory, session-scoped)
  ├─ chatClient.send() → POST to cloud/local LLM
  │     ├─ builds system prompt from SOUL.md + TOOLS.md
  │     ├─ injects Solana wallet context if stored
  │     └─ sends full message history
  │
  ├─ LLM responds with text containing tool tokens: {{tool:android_termux_exec|cmd=uname -a}}
  ├─ parseAssistantReply() extracts tools:
  │     ├─ android_browser_open    → opens URL in Hitomi Browser window
  │     ├─ android_browser_browse  → opens + reads page, sends excerpt back to LLM
  │     ├─ android_termux_exec     → runs shell command via Termux
  │     ├─ android_solana_wallet_overview → read-only wallet balance
  │     └─ android_solana_wallet_refresh  → refresh wallet
  │
  └─ tool results are appended to chatHistory as user messages → LLM continues
```

### Termux Integration

The app does **not** bundle a Linux environment. Instead it uses Android's `com.termux.RUN_COMMAND` broadcast:

```
[Overlay] → android_termux_exec|cmd=... → TermuxCommandBridge.runCommand()
  │
  ├─ checks: is Termux installed? Is RunCommandService available?
  ├─ builds PendingIntent for result callback
  ├─ sends Intent to com.termux.app.RunCommandService
  │     └─ path: /data/data/com.termux/files/usr/bin/sh
  │     └─ args: ["-lc", "<command>"]
  ├─ Termux executes → broadcasts result back
  └─ TermuxCommandBridge.onReceive() → Result (stdout/stderr/exitCode)
      → appended to terminal transcript overlay
      → sent as tool result to LLM for next turn
```

**Prerequisites for Termux commands:**
1. Grant `com.termux.permission.RUN_COMMAND` (guided by "Enable Termux Shell Tools" button)
2. In Termux: `allow-external-apps=true` in `~/.termux/termux.properties`, then restart Termux

### Termux Command Blacklist

The following are blocked client-side before execution:
- Privilege escalation: `sudo`, `su`
- Destructive deletes: `rm -rf`, `rm -fr`
- Device power: `reboot`, `shutdown`
- Process kills: `kill`, `pkill`, `killall`
- Package installs/upgrades: `apt install`, `pkg install`, `apt upgrade`, `pkg upgrade`
- Disk/device modification: `dd`, `mkfs`, writes to `/dev/block`

### Browser Integration

The `overlay_browser.xml` contains a `WebView`. When the LLM emits `android_browser_open`, the browser window appears with a visible URL bar. When `android_browser_browse` is emitted, Hitomi opens the page, injects a JS snippet via `evaluateJavascript()` to extract visible text, and sends the excerpt back as a tool result for the LLM to reason over.

### Solana Wallet

Stored locally in `SharedPreferences` (`hitomi_solana_wallet`). The purple Solana window lets users enter a wallet name and address. When the LLM emits `android_solana_wallet_overview` or `android_solana_wallet_refresh`, `SolanaWalletClient` queries Solana RPC (public fallback endpoints) for balance and recent transactions. This is **read-only**.

## Notes

- This is an early prototype release.
- Signed release AAB builds are supported when a local `keystore.properties` is present.
- Keep signing material local to the build machine; do not commit `keystore.properties` or the keystore itself.
- Public app-store metadata for Open Hitomi should live in upstream Fastlane files under `app/src/open/fastlane/`.

## Termux (T1/T1.5) setup notes

The Android app can detect Termux and test the Termux command bridge, but **two prerequisites** are required before commands work:

1. Grant the Android runtime permission:
   - `com.termux.permission.RUN_COMMAND`
   - (The app now guides this via the `Enable Termux Shell Tools` button.)
2. In Termux, enable external app commands by setting:
   - `allow-external-apps=true` in `~/.termux/termux.properties`
   - then fully close and reopen Termux

Helpful Termux command (the app also shows this in status/help text):

```sh
mkdir -p ~/.termux && grep -qx 'allow-external-apps=true' ~/.termux/termux.properties 2>/dev/null || echo 'allow-external-apps=true' >> ~/.termux/termux.properties
```

---
FILE: ./hitomi-android/SOLANA.md
---
# SOLANA.md - `hitomi-android`

This note captures the exact, minimal Solana integration plan for the Android Hitomi app.

The goal is to match the simple read-only Solana behavior already working in `agent1c.ai` without repeating the earlier token-bloat mistake, while using a manual local wallet entry flow that is actually testable on Android phones.

## Goal

Allow Hitomi Android to:
- see the stored wallet's SOL balance
- see a short list of recent transactions
- answer wallet questions honestly from fresh or cached read-only data

Do not add or imply:
- signing
- sending
- swapping
- custody
- private-key access
- background chain polling

## Shared architecture reality

- `agent1c.ai` is still the shared backend/runtime for both Agent1c and Hitomi.
- The Android app already shares Supabase auth and cloud chat backend with `agent1c.ai`.
- `hitomicompanion.github.io` is still only a partial auth/frontend split and should not be treated as the source of truth for Solana behavior.
- Mobile Solana wallet login through Supabase is not a good Android primary path here because phone-browser wallet flows are awkward and insecure to force as defaults.

## What already exists in this Android repo

Current useful hooks:
- Main cloud chat boundary:
  - `app/src/main/java/com/example/test/HitomiCloudChatClient.java`
- Assistant output tool parser/executor:
  - `app/src/main/java/com/example/test/HedgehogOverlayService.java`
- New local wallet read/storage owner:
  - `app/src/main/java/com/example/test/SolanaWalletClient.java`
- Android assistant prompt files:
  - `app/src/main/res/raw/soul_md.txt`
  - `app/src/main/res/raw/tools_md.txt`

Current approach:
- The Android app stores a Solana wallet name + wallet address locally.
- If the user asks wallet-related questions without a stored wallet, Hitomi opens a purple `Solana` window and asks the user to fill it in.
- Solana chain reads are then done on demand from that stored wallet address.

## The agent1c.ai pattern to copy exactly

From the working web implementation in `../agent1c-ai.github.io`:

1. Keep only the wallet address in runtime context/prompt.
2. Keep only the wallet address in runtime context/prompt.
3. Do not inject live balance or recent transaction data into the system prompt.
4. Teach the assistant via `TOOLS.md` that Solana balance/transactions can be checked on demand.
5. Fetch wallet data only when the assistant actually needs it.
6. Use simple read-only RPC calls with fallback RPC nodes.

This token guard matters:
- The earlier mistake on web was effectively sending too much wallet state into prompt context.
- Android must avoid that.
- Wallet data belongs in tool results or direct on-demand fetch results, not in every chat turn's base prompt.

## Exact Android hook plan

### 1) Add one small native read-only Solana helper

New file recommended:
- `app/src/main/java/com/example/test/SolanaWalletClient.java`

Mirror the simplicity of `agent1c-ai.github.io/js/solana-wallet.js`.

Responsibilities:
- normalize wallet address
- call `getBalance`
- call `getSignaturesForAddress`
- call `getTransaction`
- fall back across multiple RPC URLs if one fails
- return one normalized snapshot object

Recommended fallback RPC list:
- `https://api.mainnet-beta.solana.com`
- `https://solana-rpc.publicnode.com`
- `https://rpc.ankr.com/solana`

Suggested snapshot shape:
- `address`
- `chain`
- `lamports`
- `balanceSol`
- `fetchedAt`
- `rpcSource`
- `recentTransactions`
- `lastError`

Suggested transaction shape:
- `signature`
- `slot`
- `blockTime`
- `confirmationStatus`
- `ok`
- `err`
- `memo`
- `netLamports`
- `netSol`

### 2) Add a tiny Android wallet state holder

Do not build a large subsystem.

The Android app only needs enough state to answer wallet questions without refetching every turn:
- stored wallet name
- wallet address
- last fetched balance
- last fetched recent transactions
- rpc source
- fetched time
- last error

Implementation:
- keep this in `SolanaWalletClient` backed by `SharedPreferences`

Do not:
- poll in background
- refresh on every message
- append wallet snapshot to transcript by default

### 3) Add a visible purple `Solana` window in the overlay

Files:
- `app/src/main/java/com/example/test/HedgehogOverlayService.java`
- `app/src/main/res/layout/overlay_solana.xml`
- `app/src/main/res/drawable/beos_titlepill_bg_purple.xml`

Expected behavior:
- similar to the existing browser window pattern
- title bar should be purple, not yellow
- fields:
  - `Wallet Name`
  - `Wallet Address`
- button:
  - `OK`
- pressing `OK` stores the wallet locally for later read-only Solana checks
- if no wallet is stored and user asks anything wallet-related, Hitomi should open this window instead of pretending she can inspect a wallet already

### 4) Keep prompt injection minimal in `HitomiCloudChatClient`

File:
- `app/src/main/java/com/example/test/HitomiCloudChatClient.java`

Current hook:
- `buildSystemPrompt()` currently returns `soul_md + tools_md`

Required change:
- append only a short runtime note when a stored Solana wallet exists

Example shape:
- `Runtime note: the user is connected with Solana wallet "<address>". You may inspect balance and recent transactions when needed.`

Do not append:
- live balance
- lamports
- recent transaction lines
- summaries of wallet activity

This is the main token guardrail.

### 5) Update Android `soul_md.txt` and `tools_md.txt`

Files:
- `app/src/main/res/raw/soul_md.txt`
- `app/src/main/res/raw/tools_md.txt`

Add the same kind of read-only wallet-awareness language already working in `agent1c.ai`.

`soul_md.txt` should teach:
- stored Solana wallet is part of workspace context
- Hitomi may help inspect balances and recent transactions
- wallet access is read-only unless a tool proves otherwise
- never imply signing, sending, or custody
- never claim a refresh happened unless it actually did

`tools_md.txt` should teach:
- how to request wallet inspection
- that balance and transaction checks are on-demand only
- that these checks are read-only

Important:
- do not turn `tools_md.txt` into a dump of live wallet state
- keep it capability-oriented, not data-oriented

### 6) Android execution path

Use Android-local tool tokens for wallet reads:
- `android_solana_wallet_overview`
- `android_solana_wallet_refresh`

Extend `HedgehogOverlayService.parseAssistantReply()` and `maybeExtractAndroidTool()` to execute them locally and feed the tool result back into the next chat turn, similar to browser-read follow-up flow.

## Exact files likely to change

Almost certainly:
- `app/src/main/java/com/example/test/HitomiCloudChatClient.java`
- `app/src/main/java/com/example/test/HedgehogOverlayService.java`
- `app/src/main/res/raw/soul_md.txt`
- `app/src/main/res/raw/tools_md.txt`

Likely new:
- `app/src/main/java/com/example/test/SolanaWalletClient.java`
- `app/src/main/res/layout/overlay_solana.xml`
- `app/src/main/res/drawable/beos_titlepill_bg_purple.xml`

## Suggested minimal implementation order

1. Extend `SupabaseAuthManager` to capture wallet address and chain.
2. Add `SolanaWalletClient.java` with local wallet storage plus read-only RPC + fallback nodes.
3. Add the purple `Solana` window and local save flow in the overlay.
4. Add minimal wallet runtime note in `HitomiCloudChatClient.buildSystemPrompt()`.
5. Update `soul_md.txt` and `tools_md.txt` for read-only Solana capability.

## What not to do

- Do not inject full wallet snapshots into every system prompt.
- Do not put recent transactions into `SOUL.md` or `TOOLS.md`.
- Do not scrape wallet info from UI labels when structured auth identity fields exist.
- Do not depend on mobile browser wallet login as the main Android Solana path.
- Do not imply chain write capability.
- Do not add background refresh loops.

---
FILE: ./hitomi-android/releases/0.0.1/RELEASE_NOTES.md
---
# Agent1c Android 0.0.1

Initial public prototype of the native Android floating Hitomi app.

## Highlights

- Floating Hitomi hedgehog overlay with drag support
- Clippy-style dialog bubble with adaptive placement
- Bubble transcript scroll + auto-scroll
- Supabase login via Agent1c.ai web-first handoff
- Cloud chat using the existing Agent1c.ai backend (`xai-chat`)
- BeOS/HedgeyOS-inspired native main screen styling

## Known limitations

- Early prototype UX (not production polish yet)
- X login may require further tuning on some Android devices / OEM browser-link behaviors
- Tool use is not implemented in Android app yet (users should use `agent1c.ai`)
- No agentic heartbeat loop yet (chat-only for now)

## Build info

- App ID: `ai.agent1c.hitomi`
- Version: `0.0.1`
- APK type: debug (prototype testing build)

---
FILE: ./hitomi-android/releases/0.0.2/RELEASE_NOTES.md
---
# Agent1c Android Hitomi v0.0.2

## Highlights
- Hide-to-edge action replaces close action in the radial menu
- Edge arc tab restore interaction (tap arc to bring Hitomi back)
- Directional hide icon (left/right)
- Radial quick-action tap reliability improvements
- Native Android STT always-listening mode (quick mic toggle)
- Persistent mic indicator next to Hitomi while always-listening is enabled
- Transcript scroll + autoscroll in the dialog bubble
- Bubble tail + adaptive above/below placement improvements
- Keyboard avoidance and drag interaction fixes (reduced stuck behavior)
- BeOS/HedgeyOS-style native app chrome polish
- Web-first Agent1c.ai login handoff + cloud chat integration hardening

## Notes
- This is still a prototype debug APK.
- X login uses the web-first Agent1c.ai login flow.

---
FILE: ./hitomi-android/releases/0.0.3/RELEASE_NOTES.md
---
# Agent1c Android v0.0.3

## Highlights

- Hitomi Browser Phase 2 + Phase 3 (Android overlay mini browser)
  - Hidden by default; Hitomi can show it while browsing
  - Visible mini BeOS/HedgeyOS-style browser window next to Hitomi
  - URL loads inside the mini browser
  - Page title + visible text excerpt can be extracted for Hitomi follow-up replies
- Android browser tool tokens wired into chat flow
  - `android_browser_open` (show/load browser only)
  - `android_browser_browse` (show/load + read visible page excerpt)
- Android prompt tuning
  - `TOOLS.md` updated for Android browser behaviors
  - `SOUL.md` updated so Hitomi uses `fren` and keeps replies to max 2 sentences when possible
- Overlay polish retained from v0.0.2
  - STT always-listening
  - radial actions
  - hide-to-edge and edge arc restore
  - keyboard avoidance improvements

## Notes

- This is still a debug APK prototype build.
- Android mini browser page ingestion is visible-text excerpt based (not full tool/browser automation yet).

---
FILE: ./hitomi-android/releases/0.0.5/RELEASE_NOTES.md
---
# Agent1c Android Hitomi v0.0.5

## Highlights

- Web-first login flow polish
  - single `Login` entry point in the Android app
  - stronger web handoff callback handling (deduped handoff code consumption)
  - Google/X sign-in via Agent1c.ai login window flow retained
- Overlay-first startup flow
  - when already signed in, app starts directly into Hitomi overlay (main screen stays out of the way)
- BeOS/HedgeyOS UI polish on Android main screen
  - pill-style window title chrome
  - decorative window buttons
  - conditional login/logout and start/stop button visibility
  - pastel pink `Start Hitomi Overlay` button
- Hitomi bubble UX polish
  - rounded bubble + sharp tail
  - adaptive above/below bubble placement
  - tail tracks Hitomi horizontally when clamped
  - transcript scrolling + autoscroll
  - keyboard overlap avoidance improvements (including many edge cases)
- Long-press quick actions (radial menu) polished
  - settings / mic / hide-to-edge actions
  - improved hitboxes, colors, animations
  - hide-to-edge with edge arc restore tab
- Native Android STT always-listening
  - persistent `Listening...` indicator while mic mode is on
  - pinned mic toggle visible beside Hitomi while listening
- Hitomi Browser upgrades (Phase 2/3 + polish)
  - hidden by default, summoned by Hitomi tool use
  - `android_browser_open` and `android_browser_browse`
  - visible stepped scrolling while Hitomi reads a page
  - page snapshot ingestion for follow-up answers
  - browser reuse-in-place on subsequent browsing (no teleporting)
  - magical summon animation + particle burst on first show
  - particle stream effect between Hitomi and browser during browse scroll
- Termux (superuser optional) T1/T1.5/T2
  - Termux detection + bridge availability status
  - `Enable Termux Shell Tools` helper flow
  - copyable setup command panel for `allow-external-apps=true`
  - manual command bridge test
  - Hitomi generic Termux shell tool support via `android_termux_exec`
  - Android `TOOLS.md` + `SOUL.md` updated for Termux and Android-local capabilities

## Notes

- This is still a debug APK prototype build.
- Termux shell tools are optional and intended for superusers.
- Browser page reading is visible-text snapshot based and not full browser automation yet.

---
FILE: ./hitomi-android/releases/0.1.1/RELEASE_NOTES.md
---
# Hitomi Android v0.1.1

## Highlights

- Green-on-black Terminal overlay window for visible Termux command/result transcripts
- Automatic terminal summon when Hitomi uses Termux
- Termux package visibility fix so installed Termux controls appear reliably
- Termux reconnect flow now opens the main settings window when shell tools are unavailable
- Signed-in welcome line now advertises browsing, Termux, and Solana capabilities
- Drag Hitomi to the bottom-center `X` target to close the floating overlay
- Hidden-edge restore hit area and rotation behavior polished
- Mic permission flow and STT startup/recovery improved

## Notes

- This release keeps the current Hitomi app identity: app name `Hitomi`, icon `@drawable/hedgey1`, package `ai.agent1c.hitomi`.
- The GitHub release artifact in this repo is the debug APK for testing.

---
FILE: ./hitomi-lilly-integration/README.md
---
# hitomi-lilly-integration

Replace Hitomi's hedgehog overlay with the Lilly AI companion avatar from lilly_ai.py, connected via a WebView overlay.

## Quick Start

Choose your AI backend and follow the setup steps below. Skip if you have everything ready.

## What this does

- **Replaces the hedgehog** with Lilly's canvas-rendered puppy avatar (plus fox, cat, bear, bunny, owl, deer, wolf, raccoon)
- **Connects chat** to lilly_ai's `/api/cmd` endpoint (Ollama/llama.cpp backend)
- **Voice toggle** in the overlay quick-actions menu (calls `/api/toggle_mic`)
- **Real-time state** polling from `/api/ui_state` (hears what Lilly says, shows mood/animation)
- **Speech output** via `/api/tts` audio playback through the WebView
- **Drag-to-move** via JavaScript bridge communicating with Android

### AI Backend Options

1. **Local GGUF** (Recommended for Termux)
   - Run `llama.cpp` or `ollama` on your phone
   - Uses GGUF format models (e.g., Llama2, CodeLLama, etc.)

2. **Hugging Face** 
   - Use Hugging Face inference APIs

3. **Cloud Model APIs**
   - OpenAI, Anthropic, Google, etc.

## Get Started with Local AI Server

### Option 1: Termux (Lightweight)

If you're running on Android phone:

1. **Install Termux** (if not already)
2. **Get llama.cpp**
   ```bash
   git clone https://github.com/ggml-org/llama.cpp.git
   cd llama.cpp
   make
   ```

3. **Download GGUF model**
   ```bash
   mkdir -p ~/models
   # Get any GGUF model from Hugging Face
   # Example: https://huggingface.co/TheBloke/Llama-2-7B-Chat-GGUF/resolve/main/llama-2-7b-chat.Q4_K_M.gguf
   curl -L "URL_FROM_HF" -o ~/models/model.gguf
   ```

4. **Run llama.cpp server**
   ```bash
   ./build/bin/llama-server \
       -m ~/models/model.gguf \
       -c 2048 \
       -n 512 \
       -threads 2 \
       -host 0.0.0.0 \
       -port 5005
   ```

5. **Start this app** - The server IP is what you'll enter in the app (e.g., `http://192.168.1.100:5005`)

### Option 2: Hugging Face Inference API (Cloud)

```bash
# Install via pip
curl -LsSL https://ollama.ai/install.sh | sh
# Or use Hugging Face API endpoints
```

**Server IP** will be the Hugging Face API endpoint URL (e.g., your Hugging Face Space URL)

### Option 3: Running lilly_ai.py directly

```bash
python3 lilly_ai.py
# Start the server locally
cd ~/Lilly_Workspace
python3 lilly_ai.py

# Server IP is usually http://192.168.1.100:8098
```

**For Running lilly_ai.py Locally:**

```bash
# Clone and setup
cd ~
git clone https://github.com/username/LillyAI.git
cd LillyAI

# Install dependencies
pip3 install -r requirements.txt

# Start the server
python3 lilly_ai.py
```

**Server IP options:**
- `http://192.168.1.100:8098` (local machine)
- `http://192.168.1.100:5005` (llama.cpp server)
- `http://192.168.1.100:8000` (Open WebUI with Ollama)

## Setup

### 1. Deploy the lilly_ai server

The `/api/cmd`, `/api/ui_state`, `/api/toggle_mic`, `/api/tts`, and `/overlay` endpoints are already included in `lilly_ai.py`. Start the server:

```bash
python3 lilly_ai.py
```

**For Termux (lightweight setup):**

```bash
cd ~/Lilly_Workspace
python3 lilly_ai.py
```

The overlay page is at `http://<server>:8098/overlay`.

### 2. Setup the App

Download this repository or copy the required files:

| File | Destination |
|------|-------------|
| `LillyOverlayService.java` | `app/src/main/java/ai/agent1c/hitomi/LillyOverlayService.java` |
| `LillyAIChatClient.java` | `app/src/main/java/ai/agent1c/hitomi/LillyAIChatClient.java` |
| `overlay_lilly.xml` | `app/src/main/res/layout/overlay_lilly.xml` |
| `overlay_lilly_actions.xml` | `app/src/main/res/layout/overlay_lilly_actions.xml` |
| `lilly_skills.json` | `app/src/main/res/raw/lilly_skills.json` |
| `MainActivity.java` | `app/src/main/java/ai/agent1c/hitomi/MainActivity.java` |

### 3. Register the service in `AndroidManifest.xml`

```xml
<uses-permission android:name="android.permission.SYSTEM_ALERT_WINDOW" />
<uses-permission android:name="android.permission.FOREGROUND_SERVICE" />
<uses-permission android:name="android.permission.FOREGROUND_SERVICE_SPECIAL_USE" />
<uses-permission android:name="android.permission.RECORD_AUDIO" />
<uses-permission android:name="android.permission.INTERNET" />
<uses-permission android:name="android.permission.POST_NOTIFICATIONS" />

<application
    android:allowBackup="true"
    android:icon="@drawable/ic_launcher_foreground"
    android:label="Lilly"
    android:supportsRtl="true"
    android:theme="@style/Theme.LillyOverlay"
    android:usesCleartextTraffic="true">

    <activity
        android:name=".MainActivity"
        android:exported="true"
        android:theme="@style/Theme.LillyOverlay">
        <intent-filter>
            <action android:name="android.intent.action.MAIN" />
            <category android:name="android.intent.category.LAUNCHER" />
        </intent-filter>
    </activity>

    <activity
        android:name=".WebViewActivity"
        android:exported="false"
        android:theme="@style/Theme.LillyOverlay"
        android:windowSoftInputMode="adjustResize" />

    <service
        android:name=".LillyOverlayService"
        android:exported="false"
        android:foregroundServiceType="specialUse" />

</application>
```

### 4. Build and Install the APK

1. Open the project in Android Studio or navigate to the Android directory
2. Build with `./gradlew assembleDebug` (as shown above)
3. Install the debug APK:
   ```bash
   adb install app/build/outputs/apk/debug/app-debug.apk
   ```

### 5. Configure Server URL

The default server URL is `http://100.93.131.114:8098`. To change it:

1. **Launch the app** - You'll see a MainActivity with an editable server URL field
2. **Enter custom server** - Fill in the server URL (e.g., `http://192.168.1.100:5005`)
3. **Save settings** - Click the "Save" button to persist the server URL
4. **Start overlay** - Click "Start Lilly Overlay" to begin the AI companion

**Supported Server Formats:**
- `http://192.168.1.100:8098` (local machine, lilly_ai.py)
- `http://192.168.1.100:5005` (llama.cpp server)
- `http://192.168.1.100:8000` (Open WebUI)
- `http://YOUR_IP_FROM_DROOLINGWITHSANITY.CA:8098` (public server)

### 6. Features with Sensor + Termux Integration

This APK includes:

- **Sensor Data**: Phone accelerometer, GPS, call info, battery, audio levels
- **Audio**: Playback and recording from phone
- **Termux Commands**: WiFi, SMS, contacts, media, app launching
- **Automation**: Camera and video capture via Termux
- **App Integration**: Direct app launching and UI automation

### 7. FAQ

**Q: What if lilly_ai.py isn't on the server?**

A: The "Deploy AI to Termux" button will download lilly_ai.py to your Termux directory. Then you can run it on your local machine or phone.

**Q: I need a smaller app build?**

A: Yes - this version removes unnecessary dependencies and focuses on core sensors + Termux features. Perfect for limited storage devices.

**Q: How do I choose my AI backend?**

A: The default is HHugging Face's Grok-4 (via hitomi-android), but you can:

1. Use **Termux** with llama.cpp for GGUF models
2. Use **Hugging Face** APIs directly
3. Use **lilly_ai.py** with your preferred LLM backend

Simply enter the server URL in the app and start using your chosen AI!

## files

| File | Purpose |
|------|---------|
| `LillyOverlayService.java` | Drop-in replacement for `HedgehogOverlayService.java` – renders a WebView loading the `/overlay` page with compact/expanded modes |
| `LillyAIChatClient.java` | HTTP client for lilly_ai's API (`/api/cmd`, `/api/ui_state`, `/api/toggle_mic`) with `open_url` support for app launching |
| `overlay_lilly.xml` | Layout — single WebView with transparent background (goes in `res/layout/`) |
| `overlay_lilly_actions.xml` | Quick actions bar — Settings / Script / Mic / Close buttons |
| `lilly_skills.json` | App name → package mappings with aliases, loaded at startup |

## Architecture

```
┌─────────────────────────────────────────────┐
│           Android Overlay                   │
│  ┌───────────────────────────────────────┐  │
│  │  COLLAPSED (64×64dp)                 │  │
│  │  ┌──────────────┐                    │  │
│  │  │ Canvas Avatar│  Moving eyes/lips  │  │
│  │  └──────────────┘  Tap to expand     │  │
│  │                                       │  │
│  │  EXPANDED (320×500dp)                │  │
│  │  ┌──────────────┐                    │  │
│  │  │ Canvas Avatar│  + drag handle     │  │
│  │  ├──────────────┤                    │  │
│  │  │ Chat Bubble  │  text I/O          │  │
│  │  │ + Mic + Send │  voice control     │  │
│  │  └──────────────┘                    │  │
│  └───────────────────────────────────────┘  │
│                                              │
│  Quick actions (long-press when expanded)    │
│  ┌──────┬──────┬──────┐                    │
│  │ ⚙️  │ 🎤  │ ✕   │                    │
│  └──────┴──────┴──────┘                    │
│                                              │
│  Close target (drag icon to bottom X)       │
│  ┌──────────────────────────────┐           │
│  │              ✕               │           │
│  └──────────────────────────────┘           │
└──────────────┬──────────────────────────────┘
               │ JS Bridge / HTTP polling
               ▼
┌─────────────────────────────────────────────┐
│         lilly_ai.py (FastAPI)               │
│  /api/cmd        → chat + intent processing │
│  /api/ui_state   → real-time state + open_url│
│  /api/toggle_mic → voice activation toggle   │
│  /api/tts        → TTS audio playback        │
│  /overlay        → avatar+chat overlay page  │
│  Ollama / llama.cpp → LLM backend            │
│  Piper TTS       → per-character voices      │
│  SSH → Termux   → app launch + UI automation │
└─────────────────────────────────────────────┘
```

---
FILE: ./open-connector/.codex/skills/add-provider/SKILL.md
---
---
name: add-provider
description: Add or extend an open-source OOMOL Connect provider under src/providers/<service>, including provider definition, action schemas, local executors, credential validation, examples, and generated catalog updates.
---

# Add Provider

Use this skill when adding or updating a provider in this repository. The goal is to produce provider code that looks native to this project, is locally runnable when execution is supported, and keeps the public catalog accurate for agents and users.

This is the agent-oriented workflow for [CONTRIBUTING.md](../../../CONTRIBUTING.md#adding-providers). Follow [AGENTS.md](../../../AGENTS.md) for repository-wide style and verification rules.

## Public Boundary

Keep the repository focused on open-source local execution:

- Do not describe non-public products, non-public migration sources, unreleased SDK behavior, deployment operations, or organization-specific workflows in provider code, docs, examples, or skill text.
- Do not expose compatibility fields, storage placeholders, or implementation details that have no clear public source and consumer.
- Do not add provider-local generated schema modules such as `generated.ts` from OpenAPI/tool output. If the upstream surface is large, choose a small runnable subset and maintain schemas as source.
- Do not copy third-party logos, screenshots, API specs, documentation excerpts, generated schemas, or brand assets unless the project has the right to distribute them.
- Prefer official public URLs for `homepageUrl`, API documentation references in comments when truly needed, and credential help text.
- If you use a reference implementation, treat it only as behavioral evidence. Re-express the provider in this repository's architecture and remove non-public assumptions before finishing.

## Required Context

Before editing, read:

- `AGENTS.md`.
- `CONTRIBUTING.md`.
- `docs/catalog-format.md`.
- Existing providers with similar auth and runtime shape under `src/providers`.
- Shared provider helpers in `src/core/json-schema.ts`, `src/core/cast.ts`, `src/core/request.ts`, `src/core/types.ts`, and `src/providers/provider-runtime.ts`.

When the provider has official API documentation, use it as the source of truth for endpoints, auth, pagination, request bodies, response envelopes, limits, and error behavior. If you also have a reference implementation, compare it against the official docs instead of copying it blindly.

## Pattern Picker

Before writing code, pick the closest current provider and follow its current imports and file shape:

- `src/providers/hackernews` or `src/providers/nasa`: no-auth public APIs.
- `src/providers/avoma` or `src/providers/attention`: API key provider with `executors.ts` as credential wiring and provider logic in `runtime.ts`.
- `src/providers/github`: provider supporting both OAuth and API key through one bearer-token runtime context.
- `src/providers/gmail`: OAuth-only provider with credential validation and resource-specific runtime helpers.
- `src/providers/benchmark_email`, `src/providers/clickhouse`, or `src/providers/dataforseo`: custom credentials or user-configured API base URLs.
- `src/providers/baselinker`, `src/providers/iqair_airvisual`, or `src/providers/autotask`: provider proxy support and endpoint guards.
- `src/providers/http-json-runtime.ts`: shared helper for simple JSON HTTP providers when the provider does not need a richer local protocol.

Do not copy these examples mechanically. Use them to discover the current helper APIs and module boundaries, then keep only the pieces that match the provider being added.

## Target Shape

Provider code normally lives under:

```text
src/providers/<service>/
  definition.ts
  actions.ts       # Optional, but preferred once the provider has several actions.
  executors.ts
  scopes.ts        # Only for non-trivial provider-native scopes or permissions.
  runtime-*.ts     # Only for real API-family or resource boundaries.
```

Rules:

- Do not create `index.ts` or barrel files.
- Do not hand-edit generated registry or catalog files. Run generation.
- Keep definitions importable without network, credentials, or executor code.
- Keep executor modules lazy. `definition.ts` must not import `executors.ts`, and executor modules should not import `definition.ts` just to reuse catalog metadata.
- Keep provider-local helpers for provider-specific URLs, signing, pagination, envelopes, error extraction, and response normalization. Put generic casts, reads, query building, request helpers, and credential wiring in shared helpers when they are useful across providers.

## Provider Definition

Create or update `definition.ts` as catalog source code:

- Export `provider: ProviderDefinition`.
- Use a stable lowercase service id, usually the product slug.
- Declare `displayName`, `categories`, `authTypes`, `auth`, optional `homepageUrl` or `iconUrl`, and `actions`.
- Use `defineProviderAction(service, action)` from `src/core/provider-definition.ts` so action ids stay stable as `<service>.<name>`.
- Use provider-native `requiredScopes` and `providerPermissions`; do not invent private aliases.
- Keep action descriptions and schema descriptions useful for agents. They should describe the business operation and field meaning, not the implementation.

Build `inputSchema` and `outputSchema` with `s` from `src/core/json-schema.ts`:

- Prefer existing `s` helpers; read `src/core/json-schema.ts` for the current helper names instead of inventing provider-local schema wrappers.
- Drop to plain JSON Schema only for a provider-specific edge case that the shared helper cannot express clearly.
- Preserve meaningful required fields, enums, formats, min/max values, defaults, pagination cursors, follow-up actions, and async lifecycle metadata.
- Do not copy generated catalog JSON into source.
- Do not preserve generated safe-integer limits or converter artifacts unless they represent a real provider constraint.
- Do not hide stable top-level outputs behind `unknown` or overly loose objects when the provider returns a documented shape.

## Auth

Map provider auth to this repository's public credential types:

- `no_auth`: use `authTypes: ["no_auth"]` and `auth: [{ type: "no_auth" }]`.
- `api_key`: provide a user-facing label, placeholder, and useful description.
- `custom_credential`: define explicit fields with labels and descriptions.
- `oauth2`: configure public authorization URL, token URL, scopes, redirect path behavior if required by current types, token auth method, and static authorization params.

Open-source users bring their own credentials and OAuth applications. Provider code should validate and use those credentials through the shared runtime interfaces instead of assuming an external credential service.

Add `credentialValidators` in `executors.ts` when the provider can cheaply verify credentials and return a useful `CredentialProfile`. Use a stable account id and readable display name when the provider exposes them.

## Executors

Create or update `executors.ts` with `ProviderExecutors`:

- Use `defineProviderExecutors` from `src/providers/provider-runtime.ts`.
- Prefer `defineApiKeyProviderExecutors`, `defineOAuthProviderExecutors`, or `defineBearerProviderExecutors` when their context shape fits.
- Resolve credentials through `ExecutionContext` or the shared credential helpers.
- Keep action handlers keyed by provider action names.
- Preserve provider request semantics: endpoint paths, methods, auth headers, request bodies, query params, pagination, status handling, error mapping, and output normalization.
- Use `ProviderRequestError` for provider API failures that should become stable execution errors.
- Use shared request helpers such as `setSearchParams`, `readProviderJson`, and `readProviderText` when they fit existing patterns.
- Pass `context.signal` and transit file support through provider contexts when the provider needs cancellation or file output.

Provider-local runtime files are appropriate when a provider has multiple API areas or a meaningful shared protocol. Do not add local mini-frameworks, schema facades, or action adapter layers just to reduce edit size.

## Historical Failure Modes

Previous provider batches needed cleanup for these issues. Check them explicitly:

- Do not add catalog-only placeholders or empty `executors`. Add a provider when it has a runnable local executor.
- Do not commit generated action schema modules. Hand-maintained provider source should own action schemas.
- If a credential field contains a user-configured base URL, host, workspace URL, or region-derived URL, normalize and validate it with the current public URL helper from `src/core/request.ts` before any fetch or proxy call. Reject credentials in URLs and unsafe network targets according to that helper.
- If runtime downloads or uploads files, use existing transit-file and bounded-response helpers. Avoid unbounded `arrayBuffer()` or `text()` reads for file-sized responses.
- If the upstream API supports streaming, multipart uploads, or very large local files but this runtime only supports JSON-friendly calls, expose the JSON-friendly shape and reject unsupported flags deliberately.
- Do not add tests that only prove schema validation already rejects malformed fields. Add tests only when provider or shared runtime behavior would otherwise be unprotected.

## Examples And Tests

Examples should be concrete scripts users can run with `node examples/...`. If credentials are required, read them from environment variables and print a clear skip message when missing. Do not add package scripts for every provider example.

Provider tests are not required for simple request/response mapping already covered by centralized provider loader tests. Add targeted tests only when the change introduces non-trivial local logic, shared helper behavior, proxy endpoint guards, credential validation branches, pagination normalization, or a confirmed regression risk.

Do not add tests that only mirror static declarations such as provider labels, auth type arrays, action names, icon URLs, or generated catalog shape.

## Implementation Flow

1. Identify the provider id and the smallest useful runnable action set. Prefer a runnable provider over a catalog-only placeholder.
2. Read official docs and one or two nearby providers from the pattern picker.
3. Add or update `definition.ts`, `actions.ts`, `executors.ts`, and provider-local runtime files.
4. Move genuinely generic helper behavior to shared helper modules only after checking existing APIs and downstream call sites.
5. Run `npm run generate:catalog`.
6. Run `npm run fix-check`.
7. Run targeted tests when you changed shared helpers or added provider logic that is not covered by existing loader tests.
8. Run `npm run build` when you need CI-parity no-fix typechecking or when the task asks for it.
9. Review diffs for generated noise, non-public wording, copied assets, placeholder fields, and non-lazy imports.

## Quality Gate

Before finishing, inspect the result against these checks:

- The provider reads like native open-source code for this repository.
- Definitions are pure catalog data and executor code is lazy-loaded.
- Runtime fields have a clear public source and consumer.
- Auth is locally configurable by users.
- Schemas are useful public contracts for agents, not loose reflections of unknown JSON.
- Generic helper code has a single owner.
- Provider-local helper code has provider-specific meaning.
- No generated files were hand-edited.
- No third-party rights issue was introduced.
- No non-public product behavior is mentioned.

## Final Handoff

State:

- Which provider and actions were added or updated.
- Whether each action is locally executable or catalog-only.
- Main files changed.
- Any shared helpers changed and why the behavior belongs there.
- Tests intentionally omitted or added, with the reason.
- Exact validation commands run and their result.
- Whether full tests were not run.

---
FILE: ./open-connector/AGENTS.md
---
# Repository Guidelines

## Architecture

- Keep one clear owner for each fact. Do not repeat provider metadata such as `displayName` in executors when it already belongs to `definition.ts`; pass or inject it from the caller that has the definition/catalog.
- Provider definitions are catalog source code. Build schemas with `src/core/json-schema.ts` helpers, usually imported as `s`, instead of copying generated catalog JSON.
- Keep runtime lazy: catalog generation may import provider definitions, but executor modules should load only when an action or credential validator is actually used.
- Do not create barrel files such as `index.ts`. Import from the concrete module that owns the API.

## Code Style

- Prefer VS Code-style coherent modules: split files by responsibility or abstraction boundary, not by loose categories.
- Prefer `interface` for object-shaped contracts. Keep unions and mapped/utility compositions as `type`.
- Prefer named options/input interfaces over inline object types when a function signature spans multiple lines or crosses module boundaries.
- Avoid temporary ad hoc objects passed through many layers. Prefer explicit interfaces, classes, or top-level functions that match module boundaries.
- Put generic low-level casting/reading helpers in `src/core/cast.ts`; avoid provider-specific wrappers for generic reads.
- Avoid trivial pass-through helpers and conditional object spreads that only hide `undefined` JSON fields.
- Do not manually wrap code to 80 columns. Let `oxfmt` decide formatting.

## Runtime API

- Keep `/v1` response shaping in `src/server/runtime-api.ts`; route handlers should dispatch and validate, not assemble compatibility objects field by field.
- Public runtime fields should have a clear source and consumer. Do not expose local implementation concepts or placeholder fields just because they are easy to add.
- Match existing runtime wire shapes deliberately: catalog index endpoints, action metadata, connection aliases, envelopes, and error codes should stay stable for SDK/CLI clients.
- If an upstream-compatible field has no local source yet, prefer omitting it or returning a documented empty value from the serializer rather than scattering optional fields in routes.

## Providers

- Provider code normally lives in `src/providers/<service>/definition.ts`, `actions.ts`, `executors.ts`, and provider-local runtime helper files when needed.
- Prefer provider-local constants for official scopes, permissions, URLs, and API versions. Action `requiredScopes` should use provider-native scopes/capabilities, not private internal aliases.
- Avoid repeated action-name wiring. Define action handlers once and derive executor maps through shared provider runtime helpers.
- Do not import provider definitions from executor modules just to reuse metadata; inject catalog metadata from the server/loader side when needed.

## Provider Network Egress (SSRF)

- All provider egress must go through the shared SSRF-guarded fetch, never the global `fetch`. Use `context.fetcher` (injected by `defineProviderExecutors`/`defineApiKeyProviderExecutors`/etc.) or, in a hand-written proxy, the exported `providerFetch` / `createProviderFetch`. The guard validates the request URL and every redirect `Location` with `assertPublicHttpUrl`, follows redirects manually, and (by default) validates DNS-resolved addresses.
- DNS resolved-address validation is ON by default and runs once per request for hostname targets. Add `skipDnsValidation: true` (on `defineProviderExecutors`/`defineProviderProxy`/`createProviderFetch`) ONLY when the egress host is a hardcoded literal fully controlled by the code. NEVER add it when the host comes from credential/user input, when the base URL is a resolver, or when the provider fetches a user-supplied URL — there the DNS check is the SSRF defense, not redundant overhead.
- Self-hosted providers whose instance host is user/credential-configured and may live on a private network pass `allowPrivateNetwork: isPrivateNetworkAccessAllowed` into their executors/proxy AND thread the same flag into their base-URL `assertPublicHttpUrl` call (see Dokploy for the reference pattern). It is deployment-gated by `OOMOL_CONNECT_ALLOW_PRIVATE_NETWORK`; reserved, loopback, link-local, and cloud-metadata targets stay blocked even when it is enabled.
- User-supplied content/download URLs (e.g. `fileUrl`, `sourceUrl`, `imageUrl`) must ALWAYS be validated public-only — call `assertPublicHttpUrl` without `allowPrivateNetwork` and download them with the public-only `providerFetch`, never a private-aware `context.fetcher`. The private-network opt-in covers only the trusted instance host.
- Prefer the shared `assertPublicHttpUrl` / `isBlockedIpAddress` over a bespoke per-provider hostname guard; bespoke guards have missed the cloud-metadata blocklist and bracketed-IPv6 forms.
- Gotcha: a provider that branches on `fetcher === fetch` (e.g. to gate rate limiting to production) must compare against `providerFetch`, since that is the fetcher the runtime now injects — not the global `fetch`.

## TypeScript And Tooling

- Use native Node.js TypeScript execution. Do not add `tsx` or `--experimental-strip-types`.
- `src/`, `scripts/`, and `examples/` each have their own `tsconfig.json`; project checks focus on `src`.
- Exported top-level functions and public types should have explicit return types and useful JSDoc when it explains business meaning.
- Use `oxfmt` and `oxlint`; do not add Prettier.

## Examples And Web

- Examples should be concrete scripts users can run directly with `node examples/...`; do not add every example to `package.json`.
- If an example depends on external credentials, print a clear skip message when environment variables are missing.
- Do not put web UI code under `src/`. The future console should live as a separate Vite package under `web/`.
- Public docs should describe normal OSS usage and may include official SaaS, hosted, or team product paths when they are part of the public product strategy. Do not mention internal compatibility projects or unreleased SDK behavior.

## Verification

- Before finishing code changes, run `npm run fix-check`. It runs lint fixes, formatting fixes, and the `src` typecheck.
- Run `npm run build` only when you need a separate no-fix typecheck, for example after generated files changed or for CI parity.
- Run `npm run generate:catalog` when provider definitions or actions change.
- Run provider examples manually when the task changes user-facing example behavior.

---
FILE: ./open-connector/CODE_OF_CONDUCT.md
---
# Code of Conduct

## Our Standard

We want this project to be a respectful, practical, and productive place to collaborate.

Examples of welcome behavior:

- Be direct, factual, and kind.
- Assume good intent while still asking for clarity.
- Keep technical disagreement focused on evidence, tradeoffs, and maintainability.
- Respect different experience levels and backgrounds.

Examples of unacceptable behavior:

- Harassment, threats, or personal attacks.
- Discriminatory language or conduct.
- Publishing private information without permission.
- Repeatedly derailing issues or pull requests.
- Bad-faith participation, spam, or abuse.

## Enforcement

Project maintainers may remove comments, close issues, reject pull requests, or block users whose
behavior is harmful to the project or its participants.

If you need to report a concern, contact the maintainers through the project's public issue tracker
or the security/contact channel published by the project maintainers.

---
FILE: ./open-connector/CONTRIBUTING.md
---
# Contributing

Thanks for contributing to OOMOL Connect.

## Development Setup

```bash
npm install
npm test
```

## Before Opening a Pull Request

Run:

```bash
npm run fix-check
npm test
```

## Adding Providers

Provider definitions should be source-of-truth files under `src/providers/<service>/definition.ts`.
Generated files are updated through:

```bash
npm run generate:catalog
```

Generated `src/providers/registry.generated.ts` and `catalog/apps/*.json` files are local runtime
data and are ignored by git.

If you use an agent to add providers, the optional workflow in
[.codex/skills/add-provider/SKILL.md](.codex/skills/add-provider/SKILL.md) follows the same rules.

## Third-Party Rights

Do not contribute third-party logos, icons, screenshots, documentation excerpts, API schemas, or
brand assets unless you have the right to do so.

Provider names, app names, trademarks, logos, and brand assets belong to their respective owners.
This project uses such references only for identification and interoperability.

## Contribution License

By submitting a pull request, you agree that your contribution is provided under the Apache License,
Version 2.0, unless you clearly mark it otherwise in writing.

---
FILE: ./open-connector/NOTICE.md
---
# Notices

OOMOL Connect is licensed under the Apache License, Version 2.0, except where otherwise noted.

Third-party provider and app names, trademarks, logos, icons, service marks, trade names, APIs,
documentation, and brand assets remain the property of their respective owners.

References to third-party providers are included for identification and interoperability only. Such
references do not imply endorsement, sponsorship, partnership, certification, or verification by the
third-party owner.

---
FILE: ./open-connector/README.md
---
<div align="center">

<img src="assets/openconnector-readme-banner.png" alt="OpenConnector - Connect Once. Use Everywhere." width="100%" />

[English](README.md) | [简体中文](docs/README.zh-CN.md) | [日本語](docs/README.ja.md) | [Русский](docs/README.ru.md) | [Français](docs/README.fr.md)

[![License: Apache-2.0](https://img.shields.io/badge/License-Apache--2.0-blue.svg)](LICENSE.txt)
![Node.js 22+](https://img.shields.io/badge/Node.js-22%2B-339933)
![Cloudflare compatible](https://img.shields.io/badge/Cloudflare-compatible-F38020)
![MCP](https://img.shields.io/badge/MCP-ready-111827)
![OpenAPI](https://img.shields.io/badge/OpenAPI-3.1-6BA539)

[![Providers](https://img.shields.io/badge/dynamic/json?url=https%3A%2F%2Fconnector.oomol.com%2Fv1%2Fcatalog&query=data.providerCount&label=Providers&color=%237d7fe9)](https://oomol.com/apps)
[![Actions](https://img.shields.io/badge/dynamic/json?url=https%3A%2F%2Fconnector.oomol.com%2Fv1%2Fcatalog&query=data.actionCount&label=Actions&color=%237d7fe9)](https://oomol.com/apps)

</div>

OpenConnector is an open-source connector gateway for AI agents and an alternative to Composio.
Connect user app accounts once, then expose a shared catalog of 1,000+ providers and 10,000+
prebuilt Actions to agents and applications.

Use the [Connector SDK](https://github.com/oomol-lab/connector-sdk) from app code,
[oo CLI](https://github.com/oomol-lab/oo-cli) as the local-agent relay, MCP from agent hosts,
HTTP/OpenAPI from custom clients, and the Web Console for administration and debugging.

- Keep credentials, scopes, schemas, policies, and run logs inside an inspectable runtime.
- Run locally, on Fly.io, on Cloudflare-compatible infrastructure, or through OOMOL's hosted
  runtime.
- Use the same provider ids, Action ids, schemas, and contracts across open-source and commercial
  SaaS deployments.

## What It Provides

- A working connector catalog across products such as GitHub, Gmail, Notion, BigQuery, Google
  Analytics, Supabase, Airtable, Slack, and more.
- Credential handling for API keys, OAuth2, custom credentials, and no-auth providers.
- Inspectable Action contracts: request/response schemas, required scopes, and lazy-loaded executor
  source.
- Runtime controls for connection identity, scopes, runtime tokens, action allow/block policies,
  temporary file transit, and redacted run logs.
- Deployment options for local Docker or Node.js, Fly.io with persistent SQLite storage,
  Cloudflare Workers with D1/R2/Static Assets, and OOMOL's hosted runtime.

## Where It Fits

OpenConnector fits products where agents need durable access to the tools users already use, without
handing provider credentials to the agent process.

- Agent products that need reusable access across work apps, developer tools, data systems,
  communication platforms, and AI services.
- Products adding agent workflows that need stable, inspectable Action contracts for user app
  access.
- Teams that want hosted auth for speed while keeping a path to private or self-hosted runtime
  control.

## Developer Tools

| Tool                                                        | Purpose                                                                                                                                                                 |
| ----------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| [Connector SDK](https://github.com/oomol-lab/connector-sdk) | Thin TypeScript HTTP client. Use `OpenConnector` for self-hosted runtimes, or `Connector` / `ProjectConnector` for OOMOL-hosted personal and SaaS end-user connections. |
| [oo CLI](https://github.com/oomol-lab/oo-cli)               | Local agent relay for connector Actions. `oo connector` can search, inspect, and run Actions against OOMOL-hosted or self-hosted OpenConnector runtimes.                |
| MCP                                                         | Expose app Actions to MCP-capable agent hosts through `http://localhost:3000/mcp`.                                                                                      |
| HTTP / OpenAPI                                              | Call `/v1/actions/*` directly or inspect the generated `/openapi.json` document.                                                                                        |

Endpoint details, response envelopes, auth headers, MCP tools, and Action guide examples are in
[docs/runtime-api.md](docs/runtime-api.md).

## Dashboard Preview

OpenConnector ships with a local Dashboard for browsing connectors, configuring credentials,
creating runtime tokens, and inspecting runtime usage.

### Connector Catalog

Use the connector catalog to see available services, search for providers, and open their Actions
and credential setup from one place.

![OpenConnector connector catalog dashboard](assets/open-console-en.jpg)

### Usage Overview

Use the Overview page after deployment to monitor runtime readiness, available providers,
executable Actions, recent failures, tool call trends, and recent calls.

![OpenConnector runtime overview dashboard](assets/overview-page-en.jpg)

Provider names and trademarks belong to their respective owners and are used only for identification
and interoperability.

## How It Works

```mermaid
flowchart LR
  Agent["AI Agent / App"] -->|"SDK / CLI / MCP / HTTP"| Gateway["OpenConnector Gateway"]
  Gateway --> Auth["Credential & OAuth Boundary"]
  Gateway --> Catalog["Provider Catalog"]
  Gateway --> Actions["Open-source Action Executors"]
  Gateway --> Policy["Tokens, Scopes, Allow/Block Policy"]
  Gateway --> Logs["Run Logs"]
  Actions --> Providers["1,000+ Providers"]
  Console["Web Console"] --> Gateway
  Cloudflare["Cloudflare Workers, D1, R2"] -. deploy .-> Gateway
```

Apps and agents discover Actions, inspect schemas and scopes, select a connection alias, and execute
through the gateway. Provider secrets stay behind the runtime boundary; agents receive the metadata,
safe account labels, and execution results needed for the run.

## Usage Paths

| Path                         | Best for                                            | Includes                                                                                                                                                                  |
| ---------------------------- | --------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Open-source self-host        | Developers and teams that want full control         | Local Docker or Node runtime, SQLite storage, MCP, HTTP, OpenAPI, and Web Console                                                                                         |
| Fly.io self-host             | Teams that want a hosted Docker runtime             | Node Docker runtime, SQLite storage on a Fly volume, TLS, health checks, MCP, HTTP, OpenAPI, and Web Console                                                              |
| Cloudflare-compatible deploy | Teams that want a lightweight hosted runtime        | Workers runtime, D1 state, R2 transit files, and Static Assets for the console                                                                                            |
| [OOMOL](https://oomol.com/)  | Teams blocked by OAuth approval or launch deadlines | Hosted auth and runtime infrastructure with the same provider and Action contracts; compatible with the open-source interface for later private or self-hosted deployment |

## Cloudflare Quick Start Video

[![Deploy OpenConnector on Cloudflare Workers](assets/cloudflare-quickstart-video.png)](https://www.youtube.com/watch?v=R0V1ZdCuTgc)

The
[Cloudflare Workers deployment walkthrough](https://www.youtube.com/watch?v=R0V1ZdCuTgc) shows how
to launch OpenConnector on Cloudflare with Workers, D1, R2, and the Web Console. The video follows
the same flow as [docs/cloudflare.md](docs/cloudflare.md): create Cloudflare resources, copy
`wrangler.example.jsonc` to `wrangler.local.jsonc`, apply D1 migrations, set required secrets, and
run `npm run deploy:cloudflare`.

## Quick Start

Start the runtime from the published image with Docker Compose:

```bash
docker compose up
```

This pulls `ghcr.io/oomol-lab/open-connector:latest`. To build from source instead:

```bash
docker compose -f docker-compose.yml -f docker-compose.build.yml up --build
```

Open the local console and generated API reference:

```text
http://localhost:3000
http://localhost:3000/docs
```

Run a no-auth Action to verify the runtime:

```bash
curl -s -X POST http://localhost:3000/v1/actions/hackernews.get_top_stories \
  -H 'content-type: application/json' \
  -d '{"input":{}}'
```

See [docs/quickstart.md](docs/quickstart.md) for the full local setup, first provider connection,
OAuth flow, and runtime settings.

## Connect a Provider

GitHub is the simplest credentialed example because it can use a personal access token:

```bash
curl -s -X PUT http://localhost:3000/api/connections/github \
  -H 'content-type: application/json' \
  -d '{"authType":"api_key","values":{"apiKey":"github_pat_..."}}'

curl -s -X POST http://localhost:3000/v1/actions/github.get_current_user \
  -H 'content-type: application/json' \
  -d '{"input":{}}'
```

For OAuth2 apps, named connections, credential encryption, token refresh, and action policies, see
[docs/credentials.md](docs/credentials.md) and [docs/configuration.md](docs/configuration.md).

## Web Console

For npm-based local development, open `http://localhost:5173`; the Web Console dev server proxies
API requests to the runtime on `http://localhost:3000`. For Docker or a built Node runtime, the
console is served from `http://localhost:3000`.

The console supports provider browsing, API key and OAuth client configuration, runtime token
creation, Action schema inspection, Action debugging, recent run review, and access to the
generated OpenAPI and MCP metadata.

## Cloudflare Deployment

OpenConnector can run on Cloudflare with Workers for the runtime, D1 for state, R2 for transit
files, and Static Assets for the Web Console.

See [docs/cloudflare.md](docs/cloudflare.md) for resource creation, migrations, secrets, local Worker
preview, and remote deployment.

## Fly.io Deployment

OpenConnector can also run on Fly.io with the Node Docker runtime and persistent SQLite storage on a
Fly volume.

See [docs/fly-io.md](docs/fly-io.md) for app creation, volume setup, secrets, deployment, custom
domains, and scaling.

## Docker Image (GHCR)

Run OpenConnector from a prebuilt image on GitHub Packages (GHCR): `ghcr.io/oomol-lab/open-connector`. Use
`latest` for the newest release, a pinned version like `v1.0.0` for production, or `tip` for the
latest `main` build.

See [docs/docker-ghcr.md](docs/docker-ghcr.md) for tags, pulling, and running.

## Want to Use It Directly?

The paths above are for teams integrating connectors into their own products, runtimes, or
infrastructure. If you want to try the SaaS connection experience first, or use it directly in
day-to-day work, you do not need to deploy OpenConnector or integrate the SDK, CLI, MCP, or HTTP API
first.

[Wanta](https://wanta.ai/) is the desktop product entry point using the same shared 1,000+
SaaS/provider coverage. Connect accounts once, then use natural language to search, organize,
create, and sync across connected tools.

| If You Want to                           | Wanta Provides                                                                                                                  |
| ---------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------- |
| Try 1,000+ SaaS connections directly     | Use the same SaaS/provider coverage without deploying a runtime or integrating SDK/CLI first.                                   |
| Use Agents in daily work                 | Work across email, chat, docs, data, projects, support, developer tools, and marketing tools in natural language.               |
| Share connected capabilities with a team | Configure connections and access scopes once; teammates use them without setup while keys, tokens, and credentials stay hidden. |

## Documentation

- [Quickstart](docs/quickstart.md)
- [Developer tools](docs/sdk-cli.md)
- [Gmail OAuth and SDK tutorial](docs/gmail-oauth-sdk.md)
- [Runtime API and MCP](docs/runtime-api.md)
- [Fly.io deployment](docs/fly-io.md)
- [Cloudflare deployment](docs/cloudflare.md)
- [Docker image (GHCR)](docs/docker-ghcr.md)
- [Configuration](docs/configuration.md)
- [Credentials and OAuth](docs/credentials.md)
- [Catalog format](docs/catalog-format.md)
- [Verification language](docs/verification.md)
- [Contributing](CONTRIBUTING.md)
- [Code of Conduct](CODE_OF_CONDUCT.md)
- [Security](SECURITY.md)

## Development

Use Node.js 22 or newer:

```bash
npm install
npm run dev
```

The local API runtime listens on `http://localhost:3000`. The Web Console dev server listens on
`http://localhost:5173` and proxies API requests to the runtime.

Before opening a pull request:

```bash
npm run fix-check
npm test
```

Provider code lives under `src/providers/<service>`. See
[CONTRIBUTING.md](CONTRIBUTING.md#adding-providers) for provider contribution rules.

## License Scope

Unless otherwise noted, the source code, scripts, generated project scaffolding, tests, and
documentation authored for this repository are licensed under the Apache License, Version 2.0. See
[LICENSE.txt](LICENSE.txt).

The Apache-2.0 license for this repository does not grant rights to third-party products,
providers, apps, APIs, trademarks, service marks, trade names, logos, icons, brand assets,
documentation, screenshots, or other copyrighted materials owned by their respective holders.

Provider and app names, metadata, links, scopes, permissions, and optional logos/icons are included
only to identify services and enable interoperability. All third-party brand and product rights
remain with their respective owners. Inclusion in this catalog does not imply endorsement,
sponsorship, partnership, certification, or verification by those owners.

If you contribute provider metadata or assets, only submit material you have the right to submit.
Prefer linking to official public assets instead of copying brand files into this repository.

## Community

Please keep issues and pull requests focused, respectful, and actionable. Participation in this
project is governed by [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).

---
FILE: ./open-connector/SECURITY.md
---
# Security Policy

OpenConnector (`oomol-lab/open-connector`) is a connector gateway that stores and brokers sensitive user
credentials — API keys, OAuth client secrets, and OAuth access/refresh tokens — on behalf of many
third-party providers. We take security reports seriously and are grateful to the researchers and
users who help keep the project and its users safe.

This policy explains which versions receive security fixes, how to report a vulnerability privately,
what to expect after you report, and how operators and contributors share responsibility for keeping
credentials safe.

## Supported Versions

Security fixes are delivered on the **latest released version** and the **`main`** branch. We
recommend always running the latest release.

| Version                 | Security fixes |
| ----------------------- | -------------- |
| Latest release / `main` | ✅             |
| Older releases          | ❌             |

## Reporting a Vulnerability

**Please report security vulnerabilities privately.** Do not open a public issue, pull request, or
discussion, and do not post details on social media or any other public channel, until we have
released a fix and coordinated disclosure with you. Public reports expose every user of the project
to the vulnerability before a patch exists.

Use one of these private channels:

1. **GitHub private vulnerability reporting (preferred).** Open
   <https://github.com/oomol-lab/open-connector/security/advisories/new>, or go to the repository's
   **Security** tab → **Advisories** → **Report a vulnerability**. This creates a private advisory
   visible only to you and the maintainers, and is the fastest path to a coordinated fix and a CVE.
2. **Email.** If GitHub private reporting is unavailable to you, email **support@oomol.com** with
   the subject line prefixed `[security]`. Use this only as a fallback; it is not an encrypted
   channel, so keep secrets out of the message (see below).

If you do not receive an acknowledgement within **3 business days**, please re-send through the other
channel in case a message was missed.

### What to include

A good report lets us reproduce and assess the issue quickly. Where possible, include:

- A description of the vulnerability and its security impact (what an attacker can do).
- Step-by-step reproduction, proof-of-concept, or the relevant code path.
- The affected version, release, or commit, and the **deployment mode** (local Docker/Node or
  Cloudflare Workers).
- Any known mitigation or workaround.

### Protect secrets in your report

Because this project handles credentials, **do not include real user tokens, API keys, OAuth client
secrets, passwords, or customer data** in your report. Redact them and use placeholder values (for
example, `github_pat_REDACTED`). If a proof-of-concept requires a credential, describe how to
generate a disposable test one instead of sharing a live secret.

## What to Expect After Reporting

We follow a coordinated disclosure process:

- **Acknowledgement** within **3 business days** that we received your report.
- An **initial assessment and expected timeline** within **10 business days**. We triage by severity
  using [CVSS](https://www.first.org/cvss/calculator/4.0).
- **Regular status updates** as we work on a fix, and notification when it ships.
- **Credit** to you in the advisory and release notes when the fix is published, unless you ask to
  remain anonymous.

## Coordinated Disclosure

- Keep the report **private** until a fix is released. We aim to publish within **90 days** of the
  report; for actively exploited issues we move faster.
- We develop and review the fix in a **private** GitHub security advisory or fork — never in a public
  issue or pull request, which would reveal the vulnerability before a patch is available.
- When the fix is ready, we publish a **GitHub Security Advisory** and, for qualifying issues, request
  a **CVE** through GitHub (a CVE Numbering Authority) so downstream users are notified.
- We will coordinate the public disclosure date with you and credit your contribution.

## Scope

**In scope** — vulnerabilities in this repository's own code and defaults, for example:

- Credential storage and at-rest encryption (`src/server/secrets/*`), key handling, and token
  management.
- Runtime authentication and authorization: the admin bearer token, runtime tokens (`oct_…`),
  session cookies, and the OAuth authorization/callback flow.
- Leakage of secrets through API responses, run logs, or error messages (log redaction bypasses).
- Action allow/block policy bypass, SSRF, injection, path traversal, or transit-file abuse in the
  gateway and its executors.
- The Web Console, the HTTP/OpenAPI and MCP surfaces, and the Cloudflare Workers deployment code.

**Out of scope** — please do not report these as vulnerabilities in OpenConnector:

- Vulnerabilities in third-party providers or their APIs. Report those to the provider.
- Insecure **self-hosted configuration** that this project documents how to avoid — for example
  running without `OOMOL_CONNECT_ENCRYPTION_KEY`, running without `OOMOL_CONNECT_ADMIN_TOKEN`,
  binding to `0.0.0.0` on an untrusted network, or exposing the SQLite/D1/R2 data store. See
  [Hardening your deployment](#hardening-your-deployment).
- The hosted [OOMOL](https://oomol.com/) service and other OOMOL products. These are maintained
  separately from this repository and are not covered by this policy; report issues in them to
  support@oomol.com.
- Reports from automated scanners with no demonstrated impact, missing security headers without a
  concrete exploit, volumetric denial-of-service, social engineering, and physical attacks.

## Safe Harbor

We will not pursue or support legal action against researchers who, in good faith:

- follow this policy and stay within the scope above,
- avoid privacy violations, data destruction, and disruption of others' service, and
- give us a reasonable opportunity to remediate before any disclosure.

If in doubt about whether an action is authorized, ask us first at support@oomol.com. We do not
currently run a paid bug-bounty program, but we credit every reporter whose finding leads to a fix.

## Hardening Your Deployment

OpenConnector can be self-hosted and holds live provider credentials, so operators share
responsibility for securing their deployment. At minimum:

- **Enable at-rest encryption.** Set `OOMOL_CONNECT_ENCRYPTION_KEY` so stored credentials and OAuth
  client configs are encrypted (AES-256-GCM). Without it, secrets are stored in plaintext and the
  runtime logs a startup warning. Store the key outside the database; losing it makes encrypted
  records unrecoverable.
- **Require authentication.** Set `OOMOL_CONNECT_ADMIN_TOKEN` to protect `/api`, `/docs`, and the Web
  Console, and issue scoped runtime tokens for `/v1` and `/mcp`. Both are disabled by default for
  local development.
- **Control network exposure.** The Node server binds `127.0.0.1` by default; the Docker image binds
  `0.0.0.0`. Only expose the gateway on a trusted network or behind an authenticated proxy, and never
  expose it publicly without admin and runtime tokens set.
- **Protect the data store.** The SQLite database (local), D1 (Cloudflare), and R2 transit files
  contain sensitive material even when encrypted. Restrict file permissions and access, and store
  Cloudflare secrets with `wrangler secret put`.
- **Reduce attack surface.** Use `OOMOL_CONNECT_ALLOWED_ACTIONS` / `OOMOL_CONNECT_BLOCKED_ACTIONS` to
  limit which Actions can run.
- **Stay current.** Run a supported Node.js (22.18+ / 24) and update to the latest OpenConnector
  release for security fixes.

See [docs/credentials.md](docs/credentials.md), [docs/configuration.md](docs/configuration.md), and
[docs/cloudflare.md](docs/cloudflare.md) for full guidance.

## Handling Credentials in the Codebase

For contributors and anyone working with this repository:

- **Never commit** credentials, tokens, OAuth client secrets, API keys, `.env` files, or captured
  provider responses that contain user data.
- If a secret is committed by mistake, treat it as **compromised**: revoke and rotate it at the
  provider immediately, then report it privately through the channels above. Removing it from later
  commits or rewriting git history is not sufficient — assume it was captured.
- Keep provider secrets behind the runtime boundary. Do not add logging, error messages, or API
  responses that echo raw tokens, client secrets, or authorization headers; the run-log summarizer
  and logger redaction exist to prevent this and should not be weakened.

Thank you for helping keep OpenConnector and its users secure.

---
FILE: ./open-connector/docs/README.fr.md
---
<div align="center">

<img src="../assets/openconnector-readme-banner.png" alt="OpenConnector - Connect Once. Use Everywhere." width="100%" />

[English](../README.md) | [简体中文](README.zh-CN.md) | [日本語](README.ja.md) | [Русский](README.ru.md) | [Français](README.fr.md)

[![License: Apache-2.0](https://img.shields.io/badge/License-Apache--2.0-blue.svg)](../LICENSE.txt)
![Node.js 22+](https://img.shields.io/badge/Node.js-22%2B-339933)
![Cloudflare compatible](https://img.shields.io/badge/Cloudflare-compatible-F38020)
![MCP](https://img.shields.io/badge/MCP-ready-111827)
![OpenAPI](https://img.shields.io/badge/OpenAPI-3.1-6BA539)

[![Providers](https://img.shields.io/badge/dynamic/json?url=https%3A%2F%2Fconnector.oomol.com%2Fv1%2Fcatalog&query=data.providerCount&label=Providers&color=%237d7fe9)](https://oomol.com/apps)
[![Actions](https://img.shields.io/badge/dynamic/json?url=https%3A%2F%2Fconnector.oomol.com%2Fv1%2Fcatalog&query=data.actionCount&label=Actions&color=%237d7fe9)](https://oomol.com/apps)

</div>

OpenConnector est un connector gateway open source pour AI agents, et une alternative à Composio.
Connectez les comptes d'apps utilisateur une fois, puis exposez un catalog partagé de 1,000+
providers et 10 000+ Actions prêtes à l'emploi aux agents et applications.

Utilisez le [Connector SDK](https://github.com/oomol-lab/connector-sdk) dans le code applicatif,
[oo CLI](https://github.com/oomol-lab/oo-cli) comme relais pour les agents locaux, MCP pour les
hosts d'agents, HTTP/OpenAPI pour les clients personnalisés, et la Web Console locale pour
l'administration et le débogage.

- Gardez credentials, scopes, schemas, policies et run logs dans un runtime inspectable.
- Exécutez-le en local, sur Fly.io, sur une infrastructure compatible Cloudflare ou via le runtime
  hébergé d'OOMOL.
- Utilisez les mêmes provider ids, Action ids, schemas et contracts entre les déploiements open
  source et SaaS commercial.

## Ce Qu'il Fournit

- Un connector catalog prêt à l'emploi couvrant GitHub, Gmail, Notion, BigQuery, Google Analytics,
  Supabase, Airtable, Slack et d'autres produits.
- Une gestion centralisée des credentials dans un seul runtime : API keys, OAuth2, custom
  credentials et providers sans authentification.
- Des Action contracts inspectables et extensibles : request/response schemas, required scopes et
  executor source chargé à la demande.
- Des runtime controls pour la production : connection identity, scopes, runtime tokens, action
  allow/block policies, transit temporaire de fichiers et journaux d'exécution masqués.
- Des options de déploiement via Docker ou Node.js en local, Fly.io avec stockage SQLite persistant,
  Cloudflare Workers / D1 / R2 / Static Assets, ou le runtime hébergé d'OOMOL.

## Où L'utiliser

OpenConnector convient aux produits où les agents ont besoin d'un accès durable aux outils des
utilisateurs sans donner les provider credentials au processus agent.

- Produits d'agents qui nécessitent un accès réutilisable aux apps de travail, outils développeur,
  systèmes de données, plateformes de communication et services d'IA.
- Produits ajoutant des workflows d'agents et ayant besoin d'Action contracts stables et
  inspectables pour accéder aux applications des utilisateurs.
- Équipes qui veulent commencer vite avec hosted auth tout en gardant une voie vers un runtime privé
  ou self-hosted.

## Outils Développeur

| Outil                                                       | Rôle                                                                                                                                                                                           |
| ----------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| [Connector SDK](https://github.com/oomol-lab/connector-sdk) | Client HTTP TypeScript léger. Utilisez `OpenConnector` pour un runtime self-hosted, et `Connector` / `ProjectConnector` pour les connexions personnelles et SaaS end-user hébergées par OOMOL. |
| [oo CLI](https://github.com/oomol-lab/oo-cli)               | Relais de connector Actions pour agents locaux. `oo connector` peut chercher, inspecter et exécuter des Actions sur les runtimes OOMOL-hosted ou OpenConnector self-hosted.                    |
| MCP                                                         | Exposer les Actions d'app à des hosts d'agents compatibles MCP via `http://localhost:3000/mcp`.                                                                                                |
| HTTP / OpenAPI                                              | Appeler directement `/v1/actions/*` ou inspecter le document `/openapi.json` généré.                                                                                                           |

Consultez [runtime-api.md](runtime-api.md) pour les endpoints, response envelopes, auth headers,
outils MCP et exemples d'Action guide.

## Aperçu du Dashboard

OpenConnector inclut un Dashboard local pour parcourir les connectors, configurer les credentials,
créer des runtime tokens et inspecter l'usage du runtime.

### Connector Catalog

Le connector catalog permet de voir les services disponibles, de rechercher des providers et
d'ouvrir leurs Actions et leur credential setup depuis un seul endroit.

![OpenConnector connector catalog dashboard](../assets/open-console-en.jpg)

### Usage Overview

Après le déploiement, la page Overview affiche le runtime readiness, les providers disponibles, les
Actions exécutables, les failures récentes, les tool call trends et les recent calls.

![OpenConnector runtime overview dashboard](../assets/overview-page-en.jpg)

Les noms et marques des providers appartiennent à leurs propriétaires respectifs et sont utilisés
uniquement à des fins d'identification et d'interopérabilité.

## Fonctionnement

```mermaid
flowchart LR
  Agent["AI Agent / App"] -->|"SDK / CLI / MCP / HTTP"| Gateway["OpenConnector Gateway"]
  Gateway --> Auth["Credential & OAuth Boundary"]
  Gateway --> Catalog["Provider Catalog"]
  Gateway --> Actions["Open-source Action Executors"]
  Gateway --> Policy["Tokens, Scopes, Allow/Block Policy"]
  Gateway --> Logs["Run Logs"]
  Actions --> Providers["1,000+ Providers"]
  Console["Web Console"] --> Gateway
  Cloudflare["Cloudflare Workers, D1, R2"] -. deploy .-> Gateway
```

Les apps et agents découvrent les Actions, inspectent les schemas et scopes, sélectionnent un
connection alias et exécutent via le gateway. Les provider secrets restent derrière la frontière du
runtime ; les agents reçoivent les metadata, labels de compte sûrs et résultats d'exécution
nécessaires à la run.

## Parcours D'utilisation

| Parcours                          | Idéal pour                                                          | Inclus                                                                                                                                                                           |
| --------------------------------- | ------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Open source self-host             | Développeurs et équipes qui veulent un contrôle total               | Runtime Docker ou Node local, stockage SQLite, MCP, HTTP, OpenAPI et Web Console                                                                                                 |
| Fly.io self-host                  | Équipes qui veulent un runtime Docker hébergé                       | Runtime Docker Node, stockage SQLite sur un volume Fly, TLS, health checks, MCP, HTTP, OpenAPI et Web Console                                                                    |
| Déploiement compatible Cloudflare | Équipes qui veulent un runtime hébergé léger                        | Workers runtime, état D1, fichiers de transit R2 et Static Assets pour la console                                                                                                |
| [OOMOL](https://oomol.com/)       | Équipes bloquées par l'approbation OAuth ou les délais de lancement | Auth hébergée et infrastructure runtime avec les mêmes provider et Action contracts ; compatible avec l'interface open source pour un déploiement privé ou self-hosted ultérieur |

## Vidéo De Démarrage Rapide Cloudflare

[![Déployer OpenConnector sur Cloudflare Workers](../assets/cloudflare-quickstart-video.png)](https://www.youtube.com/watch?v=R0V1ZdCuTgc)

Le
[guide vidéo de déploiement Cloudflare Workers](https://www.youtube.com/watch?v=R0V1ZdCuTgc)
montre comment lancer OpenConnector sur Cloudflare avec Workers, D1, R2 et la Web Console. La vidéo
suit le même flux que [cloudflare.md](cloudflare.md) : créer les ressources Cloudflare, copier
`wrangler.example.jsonc` vers `wrangler.local.jsonc`, appliquer les migrations D1, définir les
secrets requis et exécuter `npm run deploy:cloudflare`.

## Démarrage Rapide

Démarrez le runtime depuis l'image publiée avec Docker Compose :

```bash
docker compose up
```

Cela récupère `ghcr.io/oomol-lab/open-connector:latest`. Pour builder depuis les sources :

```bash
docker compose -f docker-compose.yml -f docker-compose.build.yml up --build
```

Ouvrez la console locale et la référence API générée :

```text
http://localhost:3000
http://localhost:3000/docs
```

Exécutez une Action sans authentification pour vérifier le runtime :

```bash
curl -s -X POST http://localhost:3000/v1/actions/hackernews.get_top_stories \
  -H 'content-type: application/json' \
  -d '{"input":{}}'
```

Consultez [quickstart.md](quickstart.md) pour la configuration locale complète, la première
connexion provider, le flux OAuth et les paramètres runtime.

## Connecter Un Provider

GitHub est l'exemple authentifié le plus simple, car il peut utiliser un personal access token :

```bash
curl -s -X PUT http://localhost:3000/api/connections/github \
  -H 'content-type: application/json' \
  -d '{"authType":"api_key","values":{"apiKey":"github_pat_..."}}'

curl -s -X POST http://localhost:3000/v1/actions/github.get_current_user \
  -H 'content-type: application/json' \
  -d '{"input":{}}'
```

Pour les apps OAuth2, named connections, credential encryption, token refresh et action policies,
consultez [credentials.md](credentials.md) et [configuration.md](configuration.md).

## Web Console

Ouvrez `http://localhost:3000` après le démarrage du runtime. La console permet de parcourir les
providers, configurer les API keys et OAuth clients, créer des runtime tokens, inspecter les Action
schemas, déboguer les Actions, revoir les exécutions récentes et accéder aux metadata OpenAPI et MCP
générées.

## Déploiement Cloudflare

OpenConnector peut être déployé sur Cloudflare : Workers exécute le runtime, D1 stocke l'état, R2
gère les fichiers de transit et Static Assets sert la Web Console.

Consultez [cloudflare.md](cloudflare.md) pour la création des ressources, les migrations, les
secrets, la preview Worker locale et le déploiement distant.

## Déploiement Fly.io

OpenConnector peut aussi être déployé sur Fly.io avec le runtime Docker Node et un stockage SQLite
persistant sur un volume Fly.

Consultez [fly-io.md](fly-io.md) pour créer l'app Fly, configurer le volume et les secrets,
déployer, définir un domaine personnalisé et ajuster le scaling.

## Image Docker (GHCR)

Exécutez OpenConnector depuis une image préconstruite sur GitHub Packages (GHCR) :
`ghcr.io/oomol-lab/open-connector`. Utilisez `latest` pour la dernière release, une version épinglée comme
`v1.0.0` en production, ou `tip` pour le dernier build de `main`.

Consultez [docker-ghcr.md (anglais)](docker-ghcr.md) pour les tags d'image, le pull et l'exécution.

## Vous voulez l'utiliser directement ?

Les parcours ci-dessus s'adressent aux équipes qui intègrent le connector dans leurs produits,
runtimes ou infrastructures d'entreprise. Si vous voulez d'abord essayer l'expérience de connexion
SaaS, ou l'utiliser directement dans le travail quotidien, vous n'avez pas besoin de déployer
OpenConnector ni d'intégrer d'abord le SDK, la CLI, MCP ou l'API HTTP.

[Wanta](https://wanta.ai/) est le point d'entrée desktop product qui utilise la même couverture
1,000+ SaaS/providers. Après avoir connecté des comptes, vous pouvez chercher, organiser, créer et
synchroniser dans les outils connectés en natural language.

| Si Vous Voulez                                       | Wanta Fournit                                                                                                                       |
| ---------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------- |
| Essayer directement les connexions 1,000+ SaaS       | Utiliser la même couverture SaaS/provider sans déployer un runtime ni intégrer d'abord le SDK/la CLI.                               |
| Utiliser des Agents dans le travail quotidien        | Travailler en natural language à travers email, chat, docs, data, projets, support, developer tools et marketing tools.             |
| Partager des capabilities connectées avec une équipe | Configurer connections et access scopes une fois ; les teammates les utilisent sans setup, avec keys, tokens et credentials cachés. |

## Documentation

- [Démarrage rapide](quickstart.md)
- [Outils développeur](sdk-cli.md)
- [Tutoriel Gmail OAuth et SDK (anglais)](gmail-oauth-sdk.md)
- [Runtime API et MCP](runtime-api.md)
- [Déploiement Fly.io](fly-io.md)
- [Déploiement Cloudflare](cloudflare.md)
- [Image Docker (GHCR) (anglais)](docker-ghcr.md)
- [Configuration](configuration.md)
- [Credentials et OAuth](credentials.md)
- [Format du catalog](catalog-format.md)
- [Langage de verification](verification.md)
- [Contribution](../CONTRIBUTING.md)
- [Code de conduite](../CODE_OF_CONDUCT.md)
- [Sécurité](../SECURITY.md)

## Développement

Utilisez Node.js 22 ou plus récent :

```bash
npm install
npm run dev
```

Le runtime API local écoute sur `http://localhost:3000`. Le serveur de développement Web Console
écoute sur `http://localhost:5173` et proxy les requêtes API vers le runtime.

Avant d'ouvrir une pull request :

```bash
npm run fix-check
npm test
```

Le code provider se trouve dans `src/providers/<service>`. Consultez
[CONTRIBUTING.md](../CONTRIBUTING.md#adding-providers) pour les règles de contribution des
providers.

## Portée De La Licence

Sauf indication contraire, le code source, les scripts, les échafaudages de projet générés, les
tests et la documentation rédigés pour ce repository sont sous Apache License, Version 2.0. Consultez
[LICENSE.txt](../LICENSE.txt).

La licence Apache-2.0 de ce repository n'accorde aucun droit sur les produits, providers, apps,
APIs, trademarks, service marks, trade names, logos, icons, brand assets, documentation,
screenshots ou autres contenus protégés appartenant à leurs détenteurs respectifs.

Les noms de providers et d'apps, metadata, liens, scopes, permissions et logos/icons optionnels sont
inclus uniquement pour identifier les services et permettre l'interopérabilité. Tous les droits sur
les marques et produits tiers restent la propriété de leurs détenteurs respectifs. Leur présence
dans ce catalog n'implique aucune approbation, sponsorisation, partenariat, certification ou
vérification par ces détenteurs.

Si vous contribuez des provider metadata ou assets, soumettez uniquement des éléments pour lesquels
vous avez les droits nécessaires. Préférez les liens vers les assets publics officiels plutôt que de
copier des fichiers de marque dans ce repository.

## Communauté

Gardez les issues et pull requests ciblées, respectueuses et actionnables. La participation à ce
projet est régie par [CODE_OF_CONDUCT.md](../CODE_OF_CONDUCT.md).

---
FILE: ./open-connector/docs/README.ja.md
---
<div align="center">

<img src="../assets/openconnector-readme-banner.png" alt="OpenConnector - Connect Once. Use Everywhere." width="100%" />

[English](../README.md) | [简体中文](README.zh-CN.md) | [日本語](README.ja.md) | [Русский](README.ru.md) | [Français](README.fr.md)

[![License: Apache-2.0](https://img.shields.io/badge/License-Apache--2.0-blue.svg)](../LICENSE.txt)
![Node.js 22+](https://img.shields.io/badge/Node.js-22%2B-339933)
![Cloudflare compatible](https://img.shields.io/badge/Cloudflare-compatible-F38020)
![MCP](https://img.shields.io/badge/MCP-ready-111827)
![OpenAPI](https://img.shields.io/badge/OpenAPI-3.1-6BA539)

[![Providers](https://img.shields.io/badge/dynamic/json?url=https%3A%2F%2Fconnector.oomol.com%2Fv1%2Fcatalog&query=data.providerCount&label=Providers&color=%237d7fe9)](https://oomol.com/apps)
[![Actions](https://img.shields.io/badge/dynamic/json?url=https%3A%2F%2Fconnector.oomol.com%2Fv1%2Fcatalog&query=data.actionCount&label=Actions&color=%237d7fe9)](https://oomol.com/apps)

</div>

OpenConnector は AI Agent 向けのオープンソース connector gateway であり、Composio の代替です。
ユーザーのアプリアカウントを一度接続すれば、1,000+ の provider と 10,000+ の事前定義済み Action を含む共有
catalog を Agent とアプリケーションに公開できます。

アプリコードには [Connector SDK](https://github.com/oomol-lab/connector-sdk)、ローカル
Agent の relay には [oo CLI](https://github.com/oomol-lab/oo-cli)、Agent host には
MCP、custom client には HTTP/OpenAPI、管理とデバッグにはローカル Web Console を使います。

- credential、scope、schema、policy、実行ログを検査可能な runtime 内に保持します。
- ローカル、Fly.io、Cloudflare 互換 infrastructure、または OOMOL hosted runtime で実行できます。
- オープンソース版と commercial SaaS 版で同じ provider id、Action id、schema、contract を使います。

## 提供するもの

- すぐに使える connector catalog。GitHub、Gmail、Notion、BigQuery、Google Analytics、Supabase、Airtable、Slack
  などをカバーします。
- 1 つの runtime に集約された credential 管理：API key、OAuth2、custom credential、認証不要の provider。
- 検査、拡張できる Action contract：request/response schema、required scope、遅延読み込みされる executor source。
- 本番利用向けの runtime control：connection identity、scope、runtime token、action allow/block
  policy、一時ファイル転送、redacted run log。
- ローカル Docker / Node.js、Fly.io の永続 SQLite、Cloudflare Workers / D1 / R2 / Static Assets、OOMOL hosted
  runtime へのデプロイ。

## 適している場面

OpenConnector は、provider credential を Agent process に渡さずに、Agent がユーザーの既存ツールへ継続的にアクセスする必要がある製品に適しています。

- work app、developer tool、data system、communication platform、AI service を横断して再利用可能なアクセスを必要とする
  Agent 製品。
- ユーザーアプリへのアクセスに、安定して検査可能な Action contract を必要とする Agent workflow 搭載製品。
- hosted auth で素早く始めつつ、将来的な private または self-hosted runtime control を確保したいチーム。

## 開発者向けツール

| ツール                                                      | 用途                                                                                                                                                                       |
| ----------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| [Connector SDK](https://github.com/oomol-lab/connector-sdk) | 軽量 TypeScript HTTP client。self-hosted runtime には `OpenConnector`、OOMOL hosted personal / SaaS end-user connection には `Connector` / `ProjectConnector` を使います。 |
| [oo CLI](https://github.com/oomol-lab/oo-cli)               | ローカル Agent の connector Action relay です。`oo connector` は OOMOL hosted または self-hosted OpenConnector runtime の Action を検索、確認、実行できます。              |
| MCP                                                         | `http://localhost:3000/mcp` から MCP 対応 Agent host へ app Action を公開します。                                                                                          |
| HTTP / OpenAPI                                              | `/v1/actions/*` を直接呼び出すか、生成された `/openapi.json` document を確認します。                                                                                       |

Endpoint、response envelope、auth header、MCP tool、Action guide の例は
[runtime-api.md](runtime-api.md) を参照してください。

## Dashboard プレビュー

OpenConnector には、connectors の閲覧、credentials の設定、runtime token の作成、runtime usage
の確認に使えるローカル Dashboard が含まれます。

### Connector Catalog

Connector catalog では、利用可能な services の確認、provider の検索、Actions と credential setup
への移動を一か所から行えます。

![OpenConnector connector catalog dashboard](../assets/open-console-en.jpg)

### Usage Overview

デプロイ後は Overview page で runtime readiness、available providers、executable
Actions、recent failures、tool call trends、recent calls を確認できます。

![OpenConnector runtime overview dashboard](../assets/overview-page-en.jpg)

Provider 名と商標はそれぞれの権利者に帰属し、識別と相互運用性のためにのみ使用されています。

## 仕組み

```mermaid
flowchart LR
  Agent["AI Agent / App"] -->|"SDK / CLI / MCP / HTTP"| Gateway["OpenConnector Gateway"]
  Gateway --> Auth["Credential & OAuth Boundary"]
  Gateway --> Catalog["Provider Catalog"]
  Gateway --> Actions["Open-source Action Executors"]
  Gateway --> Policy["Tokens, Scopes, Allow/Block Policy"]
  Gateway --> Logs["Run Logs"]
  Actions --> Providers["1,000+ Providers"]
  Console["Web Console"] --> Gateway
  Cloudflare["Cloudflare Workers, D1, R2"] -. deploy .-> Gateway
```

App と Agent は Action を発見し、schema と scope を確認し、connection alias を選択して gateway
経由で実行します。provider secret は runtime 境界の内側に留まり、Agent には実行に必要な metadata、安全な account label、実行結果だけが渡されます。

## 利用パス

| パス                        | 適している対象                             | 含まれるもの                                                                                                                                                        |
| --------------------------- | ------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| オープンソース self-host    | 完全な制御を求める開発者とチーム           | ローカル Docker または Node runtime、SQLite storage、MCP、HTTP、OpenAPI、Web Console                                                                                |
| Fly.io self-host            | hosted Docker runtime を求めるチーム       | Node Docker runtime、Fly volume 上の SQLite storage、TLS、health check、MCP、HTTP、OpenAPI、Web Console                                                             |
| Cloudflare 互換デプロイ     | 軽量な hosted runtime を求めるチーム       | Workers runtime、D1 state、R2 transit file、console 用 Static Assets                                                                                                |
| [OOMOL](https://oomol.com/) | OAuth 承認やローンチ期限に制約があるチーム | Hosted auth と runtime infrastructure。同じ provider と Action contract を使い、後で private または self-hosted deployment へ移行できるオープンソース互換 interface |

## Cloudflare クイックスタート動画

[![OpenConnector を Cloudflare Workers にデプロイする](../assets/cloudflare-quickstart-video.png)](https://www.youtube.com/watch?v=R0V1ZdCuTgc)

[Cloudflare Workers deployment walkthrough](https://www.youtube.com/watch?v=R0V1ZdCuTgc) では、
OpenConnector を Cloudflare の Workers、D1、R2、Web Console で起動する手順を示します。動画は
[cloudflare.md](cloudflare.md) と同じ流れです。Cloudflare resource を作成し、
`wrangler.example.jsonc` を `wrangler.local.jsonc` へコピーし、D1 migration を適用し、必要な secret
を設定して `npm run deploy:cloudflare` を実行します。

## クイックスタート

公開イメージから Docker Compose で runtime を起動します。

```bash
docker compose up
```

これは `ghcr.io/oomol-lab/open-connector:latest` を pull します。ソースからビルドする場合：

```bash
docker compose -f docker-compose.yml -f docker-compose.build.yml up --build
```

ローカル console と生成された API reference を開きます。

```text
http://localhost:3000
http://localhost:3000/docs
```

認証不要の Action を実行して runtime を確認します。

```bash
curl -s -X POST http://localhost:3000/v1/actions/hackernews.get_top_stories \
  -H 'content-type: application/json' \
  -d '{"input":{}}'
```

完全なローカルセットアップ、最初の provider connection、OAuth flow、runtime settings は
[quickstart.md](quickstart.md) を参照してください。

## Provider を接続する

GitHub は personal access token を使用できるため、最も簡単な credential 付きの例です。

```bash
curl -s -X PUT http://localhost:3000/api/connections/github \
  -H 'content-type: application/json' \
  -d '{"authType":"api_key","values":{"apiKey":"github_pat_..."}}'

curl -s -X POST http://localhost:3000/v1/actions/github.get_current_user \
  -H 'content-type: application/json' \
  -d '{"input":{}}'
```

OAuth2 app、named connection、credential encryption、token refresh、action policy については、
[credentials.md](credentials.md) と [configuration.md](configuration.md) を参照してください。

## Web Console

runtime 起動後に `http://localhost:3000` を開きます。console では provider browsing、API key と OAuth
client configuration、runtime token 作成、Action schema inspection、Action debugging、recent run review、
生成された OpenAPI と MCP metadata へのアクセスができます。

## Cloudflare デプロイ

OpenConnector は Cloudflare にデプロイできます。Workers が runtime を実行し、D1 が state を保存し、R2 が transit file
を扱い、Static Assets が Web Console を配信します。

resource 作成、migration、secret、ローカル Worker preview、remote deployment については
[cloudflare.md](cloudflare.md) を参照してください。

## Fly.io デプロイ

OpenConnector は Fly.io にもデプロイできます。Node Docker runtime を使い、SQLite data を Fly
volume に永続化します。

Fly app 作成、volume、secret、deployment、custom domain、scaling については
[fly-io.md](fly-io.md) を参照してください。

## Docker イメージ（GHCR）

事前ビルドされた Docker イメージで OpenConnector を実行できます（GitHub Packages / GHCR）：
`ghcr.io/oomol-lab/open-connector`。最新 release は `latest`、production では `v1.0.0` のように version を固定、
最新の `main` build は `tip` を使います。

イメージの tag、pull、実行については [docker-ghcr.md（英語）](docker-ghcr.md) を参照してください。

## 先に接続せず直接使う場合

上記の path は、connector を自分たちの product、runtime、または enterprise infrastructure に統合する
team 向けです。SaaS connection の体験をまず試したい場合や、日々の業務でそのまま使いたい場合は、先に
OpenConnector を deploy したり、SDK、CLI、MCP、HTTP API を統合したりする必要はありません。

[Wanta](https://wanta.ai/) は、同じ 1,000+ SaaS/provider coverage を使う desktop product entry point です。
account を接続すれば、自然言語で connected tool を検索、整理、生成、同期できます。

| やりたいこと                           | Wanta が提供するもの                                                                                           |
| -------------------------------------- | -------------------------------------------------------------------------------------------------------------- |
| 1,000+ SaaS connection を直接試す      | runtime の deploy や SDK/CLI integration なしで、同じ SaaS/provider coverage を利用できます。                  |
| 日々の業務で Agent を使う              | email、chat、docs、data、project、support、developer tool、marketing tool を自然言語で横断できます。           |
| 接続済み capability を team で共有する | connection と access scope を一度設定すれば、teammate は setup なしで使え、key、token、credential は隠れます。 |

## ドキュメント

- [クイックスタート](quickstart.md)
- [開発者向けツール](sdk-cli.md)
- [Gmail OAuth と SDK チュートリアル（英語）](gmail-oauth-sdk.md)
- [Runtime API と MCP](runtime-api.md)
- [Fly.io デプロイ](fly-io.md)
- [Cloudflare デプロイ](cloudflare.md)
- [Docker イメージ（GHCR）（英語）](docker-ghcr.md)
- [設定](configuration.md)
- [Credential と OAuth](credentials.md)
- [Catalog format](catalog-format.md)
- [Verification language](verification.md)
- [コントリビューション](../CONTRIBUTING.md)
- [行動規範](../CODE_OF_CONDUCT.md)
- [セキュリティ](../SECURITY.md)

## 開発

Node.js 22 以上を使用してください。

```bash
npm install
npm run dev
```

Local API runtime は `http://localhost:3000` で待ち受けます。Web Console dev server は
`http://localhost:5173` で待ち受け、API requests を runtime に proxy します。

pull request を開く前に実行します。

```bash
npm run fix-check
npm test
```

Provider code は `src/providers/<service>` 配下にあります。Provider contribution rule は
[CONTRIBUTING.md](../CONTRIBUTING.md#adding-providers) を参照してください。

## ライセンス範囲

特に明記されていない限り、この repository の source code、script、生成された project scaffolding、test、
documentation は Apache License, Version 2.0 の下でライセンスされています。[LICENSE.txt](../LICENSE.txt) を参照してください。

この repository の Apache-2.0 license は、各権利者が所有する third-party product、provider、app、API、
trademark、service mark、trade name、logo、icon、brand asset、documentation、screenshot、その他の copyrighted
material に対する権利を付与するものではありません。

Provider と app の名称、metadata、link、scope、permission、任意の logo/icon は、service の識別と相互運用性のためだけに含まれます。
すべての third-party brand と product の権利は、それぞれの権利者に帰属します。この catalog に含まれることは、それらの権利者による承認、後援、提携、認証、検証を意味しません。

provider metadata や asset を提供する場合は、提出できる権利を持つ素材のみを含めてください。brand file
をこの repository にコピーするのではなく、公式に公開されている asset へリンクすることを優先してください。

## コミュニティ

issue と pull request は、焦点が合い、敬意があり、実行可能な内容にしてください。この project への参加には
[CODE_OF_CONDUCT.md](../CODE_OF_CONDUCT.md) が適用されます。

---
FILE: ./open-connector/docs/README.ru.md
---
<div align="center">

<img src="../assets/openconnector-readme-banner.png" alt="OpenConnector - Connect Once. Use Everywhere." width="100%" />

[English](../README.md) | [简体中文](README.zh-CN.md) | [日本語](README.ja.md) | [Русский](README.ru.md) | [Français](README.fr.md)

[![License: Apache-2.0](https://img.shields.io/badge/License-Apache--2.0-blue.svg)](../LICENSE.txt)
![Node.js 22+](https://img.shields.io/badge/Node.js-22%2B-339933)
![Cloudflare compatible](https://img.shields.io/badge/Cloudflare-compatible-F38020)
![MCP](https://img.shields.io/badge/MCP-ready-111827)
![OpenAPI](https://img.shields.io/badge/OpenAPI-3.1-6BA539)

[![Providers](https://img.shields.io/badge/dynamic/json?url=https%3A%2F%2Fconnector.oomol.com%2Fv1%2Fcatalog&query=data.providerCount&label=Providers&color=%237d7fe9)](https://oomol.com/apps)
[![Actions](https://img.shields.io/badge/dynamic/json?url=https%3A%2F%2Fconnector.oomol.com%2Fv1%2Fcatalog&query=data.actionCount&label=Actions&color=%237d7fe9)](https://oomol.com/apps)

</div>

OpenConnector — open-source connector gateway для AI agents и альтернатива Composio. Подключите
пользовательские аккаунты приложений один раз, а затем откройте общий catalog из 1,000+ providers и
10 000+ готовых Actions для агентов и приложений.

В application code используйте [Connector SDK](https://github.com/oomol-lab/connector-sdk), для
local-agent relay — [oo CLI](https://github.com/oomol-lab/oo-cli), для agent hosts — MCP, для
custom clients — HTTP/OpenAPI, а для администрирования и отладки — локальную Web Console.

- Держите credentials, scopes, schemas, policies и run logs внутри проверяемого runtime.
- Запускайте локально, на Fly.io, в Cloudflare-совместимой инфраструктуре или через hosted runtime
  OOMOL.
- Используйте одни и те же provider ids, Action ids, schemas и contracts в open-source и
  commercial SaaS deployments.

## Что Дает

- Рабочий connector catalog для GitHub, Gmail, Notion, BigQuery, Google Analytics, Supabase,
  Airtable, Slack и других продуктов.
- Управление credentials в одном runtime: API keys, OAuth2, custom credentials и providers без
  аутентификации.
- Проверяемые и расширяемые Action contracts: request/response schemas, required scopes и
  lazy-loaded executor source.
- Runtime controls для production: connection identity, scopes, runtime tokens, action allow/block
  policies, временный транзит файлов и редактированные журналы запусков.
- Варианты развертывания через локальный Docker или Node.js, Fly.io с persistent SQLite storage,
  Cloudflare Workers / D1 / R2 / Static Assets и hosted runtime OOMOL.

## Где Это Уместно

OpenConnector подходит продуктам, где агентам нужен длительный доступ к инструментам пользователей
без передачи provider credentials в процесс агента.

- Агентские продукты, которым нужен переиспользуемый доступ к рабочим приложениям, инструментам
  разработчика, системам данных, коммуникационным платформам и AI-сервисам.
- Продукты, добавляющие agent workflows и нуждающиеся в стабильных, проверяемых Action contracts
  для доступа к пользовательским приложениям.
- Команды, которые хотят быстро стартовать с hosted auth и сохранить путь к private или self-hosted
  runtime control.

## Инструменты Разработчика

| Инструмент                                                  | Назначение                                                                                                                                                                    |
| ----------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| [Connector SDK](https://github.com/oomol-lab/connector-sdk) | Легкий TypeScript HTTP client. Для self-hosted runtime используйте `OpenConnector`, для OOMOL-hosted personal и SaaS end-user connections — `Connector` / `ProjectConnector`. |
| [oo CLI](https://github.com/oomol-lab/oo-cli)               | Connector Action relay для локальных агентов. `oo connector` может искать, проверять и запускать Actions в OOMOL-hosted или self-hosted OpenConnector runtime.                |
| MCP                                                         | Экспортировать app Actions в MCP-совместимые hosts агентов через `http://localhost:3000/mcp`.                                                                                 |
| HTTP / OpenAPI                                              | Вызывать `/v1/actions/*` напрямую или просматривать сгенерированный документ `/openapi.json`.                                                                                 |

Подробности об endpoints, response envelopes, auth headers, MCP tools и примерах Action guide см. в
[runtime-api.md](runtime-api.md).

## Обзор Dashboard

OpenConnector включает локальный Dashboard для просмотра connectors, настройки credentials,
создания runtime tokens и проверки runtime usage.

### Connector Catalog

В connector catalog можно просматривать доступные services, искать providers и открывать их Actions
и credential setup из одного места.

![OpenConnector connector catalog dashboard](../assets/open-console-en.jpg)

### Usage Overview

После развертывания страница Overview показывает runtime readiness, доступные providers,
исполняемые Actions, недавние failures, tool call trends и recent calls.

![OpenConnector runtime overview dashboard](../assets/overview-page-en.jpg)

Названия и товарные знаки providers принадлежат их владельцам и используются только для
идентификации и совместимости.

## Как Это Работает

```mermaid
flowchart LR
  Agent["AI Agent / App"] -->|"SDK / CLI / MCP / HTTP"| Gateway["OpenConnector Gateway"]
  Gateway --> Auth["Credential & OAuth Boundary"]
  Gateway --> Catalog["Provider Catalog"]
  Gateway --> Actions["Open-source Action Executors"]
  Gateway --> Policy["Tokens, Scopes, Allow/Block Policy"]
  Gateway --> Logs["Run Logs"]
  Actions --> Providers["1,000+ Providers"]
  Console["Web Console"] --> Gateway
  Cloudflare["Cloudflare Workers, D1, R2"] -. deploy .-> Gateway
```

Приложения и агенты обнаруживают Actions, просматривают schemas и scopes, выбирают connection alias
и выполняют запросы через gateway. Provider secrets остаются за границей runtime; агенты получают
только metadata, безопасные account labels и результаты выполнения, необходимые для запуска.

## Пути Использования

| Путь                                 | Лучше всего подходит для                                | Включает                                                                                                                                                              |
| ------------------------------------ | ------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Open-source self-host                | Разработчиков и команд, которым нужен полный контроль   | Локальный Docker или Node runtime, SQLite storage, MCP, HTTP, OpenAPI и Web Console                                                                                   |
| Fly.io self-host                     | Команд, которым нужен hosted Docker runtime             | Node Docker runtime, SQLite storage на Fly volume, TLS, health checks, MCP, HTTP, OpenAPI и Web Console                                                               |
| Cloudflare-совместимое развертывание | Команд, которым нужен легкий hosted runtime             | Workers runtime, состояние D1, транзитные файлы R2 и Static Assets для console                                                                                        |
| [OOMOL](https://oomol.com/)          | Команд, ограниченных OAuth approval или сроками запуска | Hosted auth и runtime infrastructure с теми же provider и Action contracts; совместимость с open-source interface для последующего private или self-hosted deployment |

## Видео Быстрого Старта Cloudflare

[![Развернуть OpenConnector на Cloudflare Workers](../assets/cloudflare-quickstart-video.png)](https://www.youtube.com/watch?v=R0V1ZdCuTgc)

[Пошаговое видео по развертыванию на Cloudflare Workers](https://www.youtube.com/watch?v=R0V1ZdCuTgc)
показывает, как запустить OpenConnector на Cloudflare с Workers, D1, R2 и Web Console. Видео
следует тому же процессу, что и [cloudflare.md](cloudflare.md): создать ресурсы Cloudflare,
скопировать `wrangler.example.jsonc` в `wrangler.local.jsonc`, применить D1 migrations, задать
обязательные secrets и выполнить `npm run deploy:cloudflare`.

## Быстрый Старт

Запустите runtime из опубликованного образа через Docker Compose:

```bash
docker compose up
```

Это скачает `ghcr.io/oomol-lab/open-connector:latest`. Чтобы собрать из исходников:

```bash
docker compose -f docker-compose.yml -f docker-compose.build.yml up --build
```

Откройте локальную console и сгенерированную API reference:

```text
http://localhost:3000
http://localhost:3000/docs
```

Выполните Action без аутентификации, чтобы проверить runtime:

```bash
curl -s -X POST http://localhost:3000/v1/actions/hackernews.get_top_stories \
  -H 'content-type: application/json' \
  -d '{"input":{}}'
```

Полную локальную настройку, первое provider connection, OAuth flow и runtime settings см. в
[quickstart.md](quickstart.md).

## Подключить Provider

GitHub — самый простой пример с credentials, потому что он может использовать personal access
token:

```bash
curl -s -X PUT http://localhost:3000/api/connections/github \
  -H 'content-type: application/json' \
  -d '{"authType":"api_key","values":{"apiKey":"github_pat_..."}}'

curl -s -X POST http://localhost:3000/v1/actions/github.get_current_user \
  -H 'content-type: application/json' \
  -d '{"input":{}}'
```

OAuth2 apps, named connections, credential encryption, token refresh и action policies описаны в
[credentials.md](credentials.md) и [configuration.md](configuration.md).

## Web Console

Откройте `http://localhost:3000` после запуска runtime. Console поддерживает просмотр providers,
настройку API key и OAuth client, создание runtime tokens, просмотр Action schemas, отладку
Actions, проверку недавних запусков и доступ к сгенерированным OpenAPI и MCP metadata.

## Развертывание Cloudflare

OpenConnector можно развернуть на Cloudflare: Workers запускает runtime, D1 хранит state, R2
обрабатывает transit files, а Static Assets обслуживает Web Console.

Создание ресурсов, migrations, secrets, локальная Worker preview и remote deployment описаны в
[cloudflare.md](cloudflare.md).

## Развертывание Fly.io

OpenConnector также можно развернуть на Fly.io с Node Docker runtime и persistent SQLite storage на
Fly volume.

Создание Fly app, настройка volume, secrets, deployment, custom domain и scaling описаны в
[fly-io.md](fly-io.md).

## Docker-образ (GHCR)

Запускайте OpenConnector из готового образа в GitHub Packages (GHCR): `ghcr.io/oomol-lab/open-connector`.
Используйте `latest` для новейшего release, закреплённую версию вроде `v1.0.0` для production или `tip`
для последнего build из `main`.

О тегах образа, pull и запуске см. [docker-ghcr.md (на английском)](docker-ghcr.md).

## Хотите Использовать Напрямую?

Пути выше предназначены для команд, которые интегрируют connector в свои продукты, runtimes или
enterprise infrastructure. Если вы хотите сначала попробовать SaaS connection experience или сразу
использовать это в работе, вам не обязательно сначала deploy OpenConnector или интегрировать SDK,
CLI, MCP либо HTTP API.

[Wanta](https://wanta.ai/) — desktop product entry point с тем же 1,000+ SaaS/provider coverage.
После подключения accounts можно через natural language искать, организовывать, создавать и
синхронизировать данные между connected tools.

| Если Вы Хотите                          | Что Дает Wanta                                                                                                          |
| --------------------------------------- | ----------------------------------------------------------------------------------------------------------------------- |
| Попробовать 1,000+ SaaS connections     | Использовать то же SaaS/provider coverage без runtime deploy или предварительной SDK/CLI integration.                   |
| Использовать Agents в ежедневной работе | Работать через natural language с email, chat, docs, data, projects, support, developer tools и marketing tools.        |
| Делиться подключенными capabilities     | Один раз настроить connections и access scopes; teammates используют их без setup, а keys, tokens и credentials скрыты. |

## Документация

- [Быстрый старт](quickstart.md)
- [Инструменты разработчика](sdk-cli.md)
- [Руководство Gmail OAuth и SDK (на английском)](gmail-oauth-sdk.md)
- [Runtime API и MCP](runtime-api.md)
- [Развертывание Fly.io](fly-io.md)
- [Развертывание Cloudflare](cloudflare.md)
- [Docker-образ (GHCR) (на английском)](docker-ghcr.md)
- [Configuration](configuration.md)
- [Credentials и OAuth](credentials.md)
- [Формат catalog](catalog-format.md)
- [Язык verification](verification.md)
- [Contributing](../CONTRIBUTING.md)
- [Code of Conduct](../CODE_OF_CONDUCT.md)
- [Security](../SECURITY.md)

## Разработка

Используйте Node.js 22 или новее:

```bash
npm install
npm run dev
```

Локальный API runtime слушает `http://localhost:3000`. Dev server Web Console слушает
`http://localhost:5173` и проксирует API requests в runtime.

Перед открытием pull request:

```bash
npm run fix-check
npm test
```

Provider code находится в `src/providers/<service>`. Правила добавления providers см. в
[CONTRIBUTING.md](../CONTRIBUTING.md#adding-providers).

## Область Действия Лицензии

Если не указано иное, исходный код, scripts, сгенерированное project scaffolding, tests и
documentation, созданные для этого repository, лицензируются по Apache License, Version 2.0. См.
[LICENSE.txt](../LICENSE.txt).

Лицензия Apache-2.0 для этого repository не предоставляет прав на third-party products, providers,
apps, APIs, trademarks, service marks, trade names, logos, icons, brand assets, documentation,
screenshots или другие copyrighted materials, принадлежащие соответствующим правообладателям.

Названия providers и apps, metadata, links, scopes, permissions и optional logos/icons включены
только для идентификации сервисов и обеспечения совместимости. Все права на third-party brands и
products остаются у их владельцев. Включение в этот catalog не означает одобрения, спонсорства,
партнерства, сертификации или проверки со стороны этих владельцев.

Если вы добавляете provider metadata или assets, отправляйте только материалы, на передачу которых
у вас есть права. Предпочитайте ссылки на официальные публичные assets вместо копирования brand
files в этот repository.

## Сообщество

Пожалуйста, делайте issues и pull requests сфокусированными, уважительными и пригодными к
исполнению. Участие в проекте регулируется [CODE_OF_CONDUCT.md](../CODE_OF_CONDUCT.md).

---
FILE: ./open-connector/docs/README.zh-CN.md
---
<div align="center">

<img src="../assets/openconnector-readme-banner.png" alt="OpenConnector - Connect Once. Use Everywhere." width="100%" />

[English](../README.md) | [简体中文](README.zh-CN.md) | [日本語](README.ja.md) | [Русский](README.ru.md) | [Français](README.fr.md)

[![License: Apache-2.0](https://img.shields.io/badge/License-Apache--2.0-blue.svg)](../LICENSE.txt)
![Node.js 22+](https://img.shields.io/badge/Node.js-22%2B-339933)
![Cloudflare compatible](https://img.shields.io/badge/Cloudflare-compatible-F38020)
![MCP](https://img.shields.io/badge/MCP-ready-111827)
![OpenAPI](https://img.shields.io/badge/OpenAPI-3.1-6BA539)

[![Providers](https://img.shields.io/badge/dynamic/json?url=https%3A%2F%2Fconnector.oomol.com%2Fv1%2Fcatalog&query=data.providerCount&label=Providers&color=%237d7fe9)](https://oomol.com/apps)
[![Actions](https://img.shields.io/badge/dynamic/json?url=https%3A%2F%2Fconnector.oomol.com%2Fv1%2Fcatalog&query=data.actionCount&label=Actions&color=%237d7fe9)](https://oomol.com/apps)

</div>

OpenConnector 是面向 AI Agent 的开源 connector gateway，也是 Composio 的开源替代方案。
连接一次用户应用账号，就可以把包含 1,000+ 个 provider 和 10,000+ 个预置 Action 的共享 catalog 暴露给
Agent 和应用。

应用代码使用 [Connector SDK](https://github.com/oomol-lab/connector-sdk)，本地 Agent 使用
[oo CLI](https://github.com/oomol-lab/oo-cli) 中继，Agent host 使用 MCP，自定义客户端使用
HTTP/OpenAPI；管理和调试使用本地 Web 控制台。

- 把 credential、scope、schema、policy 和运行日志保留在可检查的 runtime 里。
- 支持本地运行、Fly.io 部署、Cloudflare 兼容基础设施部署，也可以使用 OOMOL 托管 runtime。
- 开源版和商业 SaaS 版共享同一套 provider id、Action id、schema 和契约。

## 提供什么

- 一套可直接使用的 connector catalog，覆盖 GitHub、Gmail、Notion、BigQuery、Google Analytics、Supabase、Airtable、Slack
  等常见产品。
- 支持 API key、OAuth2、自定义凭据，以及无需鉴权的 provider。
- 可以审查和扩展的 Action 契约：请求/响应 schema、required scope 和按需加载的 executor 源码。
- 面向生产的 runtime 控制：connection identity、scope、runtime token、action allow/block policy、临时文件中转和脱敏运行日志。
- 部署方式覆盖本地 Docker 或 Node.js、Fly.io 持久化 SQLite、Cloudflare Workers/D1/R2/Static
  Assets，以及 OOMOL 托管 runtime。

## 适合什么场景

OpenConnector 适合需要让 Agent 持续访问用户现有工具、但不想把 provider credential 交给 Agent 进程的产品。

- 需要在工作应用、开发者工具、数据系统、沟通平台和 AI 服务之间复用接入层的 Agent 产品。
- 正在加入 Agent workflow，并希望通过稳定、可审查的 Action 契约接入用户应用的产品。
- 希望先用托管鉴权快速上线，同时保留未来私有化或自托管 runtime 控制权的团队。

## 开发者工具

| 工具                                                        | 用途                                                                                                                                              |
| ----------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------- |
| [Connector SDK](https://github.com/oomol-lab/connector-sdk) | 轻量 TypeScript HTTP client。自托管 runtime 使用 `OpenConnector`，OOMOL 托管的个人连接和 SaaS 终端用户连接使用 `Connector` / `ProjectConnector`。 |
| [oo CLI](https://github.com/oomol-lab/oo-cli)               | 本地 Agent 的 connector Action 中继线。`oo connector` 可以搜索、查看和运行 OOMOL 托管或自托管 OpenConnector runtime 中的 Action。                 |
| MCP                                                         | 通过 `http://localhost:3000/mcp` 把应用 Action 暴露给支持 MCP 的 agent host。                                                                     |
| HTTP / OpenAPI                                              | 直接调用 `/v1/actions/*`，或查看生成的 `/openapi.json` 文档。                                                                                     |

Endpoint、response envelope、鉴权 header、MCP tools 和 Action guide 示例见
[runtime-api.md](runtime-api.md)。

## Dashboard 预览

OpenConnector 内置本地 Dashboard，可用于浏览 connector、配置 credential、创建 runtime token 和查看运行时使用数据。

### Connector 预览

通过 connector catalog 可以查看可用服务、搜索 provider，并进入对应的 Action 与 credential 配置。

![OpenConnector Connector 预览 Dashboard](../assets/open-console-zh.jpg)

### 数据统计概览

部署后，Overview 页面会集中展示运行时状态、可用 provider、可执行 Action、最近失败、工具调用趋势和最近调用记录。

![OpenConnector 运行时概览 Dashboard](../assets/overview-page-zh.jpg)

Provider 名称和商标归各自权利人所有，本项目仅用于识别服务和实现互操作。

## 工作方式

```mermaid
flowchart LR
  Agent["AI Agent / App"] -->|"SDK / CLI / MCP / HTTP"| Gateway["OpenConnector Gateway"]
  Gateway --> Auth["Credential & OAuth Boundary"]
  Gateway --> Catalog["Provider Catalog"]
  Gateway --> Actions["Open-source Action Executors"]
  Gateway --> Policy["Tokens, Scopes, Allow/Block Policy"]
  Gateway --> Logs["Run Logs"]
  Actions --> Providers["1,000+ Providers"]
  Console["Web Console"] --> Gateway
  Cloudflare["Cloudflare Workers, D1, R2"] -. deploy .-> Gateway
```

应用或 Agent 可以发现 Action、查看 schema 和 scope、选择 connection alias，并通过网关执行调用。Provider
secret 保留在运行时边界内；Agent 拿到本次运行所需的 metadata、安全账号标签和执行结果。

## 使用路径

| 路径                        | 适合谁                                | 提供什么                                                                                                       |
| --------------------------- | ------------------------------------- | -------------------------------------------------------------------------------------------------------------- |
| 开源自托管                  | 希望完全掌控基础设施的开发者和团队    | 本地 Docker 或 Node runtime、SQLite 存储、MCP、HTTP、OpenAPI 和 Web 控制台                                     |
| Fly.io 自托管               | 希望使用托管 Docker runtime 的团队    | Node Docker runtime、Fly volume 上的 SQLite 存储、TLS、健康检查、MCP、HTTP、OpenAPI 和 Web 控制台              |
| Cloudflare 兼容部署         | 希望快速获得轻量托管运行时的团队      | Workers runtime、D1 状态存储、R2 文件中转和控制台 Static Assets                                                |
| [OOMOL](https://oomol.com/) | 被 OAuth 申请周期或上线时间卡住的团队 | 托管鉴权和运行时基础设施，使用同一套 provider 和 Action 契约；接口与开源版兼容，后续可迁移到私有化或自托管部署 |

## Cloudflare 快速启动视频

[![一键部署 OpenConnector 到 Cloudflare Workers](../assets/cloudflare-quickstart-video.png)](https://www.youtube.com/watch?v=R0V1ZdCuTgc)

[Cloudflare Workers 部署演示](https://www.youtube.com/watch?v=R0V1ZdCuTgc) 展示如何把
OpenConnector 跑到 Cloudflare 的 Workers、D1、R2 和 Web 控制台上。视频流程与
[cloudflare.md](cloudflare.md) 保持一致：创建 Cloudflare 资源、把
`wrangler.example.jsonc` 复制成 `wrangler.local.jsonc`、执行 D1 migration、设置必需
secret，然后运行 `npm run deploy:cloudflare`。

## 快速开始

使用 Docker Compose 从发布的镜像启动运行时：

```bash
docker compose up
```

这会拉取 `ghcr.io/oomol-lab/open-connector:latest`。想改为从源码构建：

```bash
docker compose -f docker-compose.yml -f docker-compose.build.yml up --build
```

打开本地控制台和生成的 API 文档：

```text
http://localhost:3000
http://localhost:3000/docs
```

运行一个不需要鉴权的 Action，确认运行时已经正常工作：

```bash
curl -s -X POST http://localhost:3000/v1/actions/hackernews.get_top_stories \
  -H 'content-type: application/json' \
  -d '{"input":{}}'
```

完整本地启动、第一个 provider 连接、OAuth flow 和运行时设置见 [quickstart.md](quickstart.md)。

## 连接 Provider

GitHub 是最简单的带凭据示例，因为它可以使用 personal access token：

```bash
curl -s -X PUT http://localhost:3000/api/connections/github \
  -H 'content-type: application/json' \
  -d '{"authType":"api_key","values":{"apiKey":"github_pat_..."}}'

curl -s -X POST http://localhost:3000/v1/actions/github.get_current_user \
  -H 'content-type: application/json' \
  -d '{"input":{}}'
```

OAuth2 应用、命名连接、凭据加密、token 刷新和 action policy 见
[credentials.md](credentials.md) 与 [configuration.md](configuration.md)。

## Web 控制台

启动运行时后打开 `http://localhost:3000`。控制台支持浏览 provider、保存 API key 或 OAuth client
配置、创建 runtime token、查看 Action schema、调试 Action、查看最近运行记录，并打开生成的 OpenAPI 和
MCP metadata。

## Cloudflare 部署

OpenConnector 可以部署到 Cloudflare：Workers 运行 runtime，D1 保存状态，R2 处理中转文件，Static
Assets 承载 Web 控制台。

Cloudflare 资源创建、migration、secret、本地 Worker preview 和远程部署步骤见
[cloudflare.md](cloudflare.md)。

## Fly.io 部署

OpenConnector 也可以部署到 Fly.io：使用 Node Docker runtime，并把 SQLite 数据持久化到 Fly volume。

Fly app 创建、volume、secret、部署、自定义域名和扩缩容步骤见 [fly-io.md](fly-io.md)。

## Docker 镜像（GHCR）

可以直接用 GitHub Packages（GHCR）上的预构建镜像运行 OpenConnector：`ghcr.io/oomol-lab/open-connector`。最新
release 用 `latest`，生产环境固定版本号（如 `v1.0.0`），想用最新 `main` 构建则用 `tip`。

镜像标签、拉取和运行的说明见 [docker-ghcr.zh-CN.md](docker-ghcr.zh-CN.md)。

## 不想先接入？可以直接使用 Wanta

上面的路径更适合把 connector 接入自己的产品、runtime 或企业基础设施。如果你只是想先体验连接各种
SaaS 的效果，或者想直接在业务中使用这些能力，不一定要先部署 OpenConnector，也不一定要接入 SDK、CLI、MCP 或
HTTP API。

[Wanta](https://wanta.ai/) 是使用同一套 1,000+ SaaS/provider 覆盖的桌面端产品入口。用户连接账号后，就可以用自然语言让 AI
跨已连接工具查询、整理、生成和同步。

| 如果你想要             | Wanta 提供                                                                               |
| ---------------------- | ---------------------------------------------------------------------------------------- |
| 直接体验 SaaS 连接能力 | 使用同一套 1,000+ SaaS/provider 覆盖，不需要先部署 runtime 或接入 SDK/CLI。              |
| 直接在业务中使用       | 用自然语言跨邮件、沟通、文档、数据、项目、客服、开发和营销等工具查询、整理、生成和同步。 |
| 团队一起使用           | 一人配置连接和授权范围，成员免配置使用；Key、Token 和账号凭据不外露。                    |

## 文档

- [快速开始](quickstart.md)
- [开发者工具](sdk-cli.md)
- [Gmail OAuth 和 SDK 接入教程](gmail-oauth-sdk.zh-CN.md)
- [Runtime API 和 MCP](runtime-api.md)
- [Fly.io 部署](fly-io.md)
- [Cloudflare 部署](cloudflare.md)
- [Docker 镜像（GHCR）](docker-ghcr.zh-CN.md)
- [配置项](configuration.md)
- [凭据和 OAuth](credentials.md)
- [Catalog 格式](catalog-format.md)
- [Verification 语言](verification.md)
- [贡献指南](../CONTRIBUTING.md)
- [行为准则](../CODE_OF_CONDUCT.md)
- [安全政策](../SECURITY.md)

## 开发

请使用 Node.js 22 或更新版本：

```bash
npm install
npm run dev
```

本地 API runtime 监听 `http://localhost:3000`。Web Console 开发服务器监听
`http://localhost:5173`，并把 API 请求代理到 runtime。

打开 pull request 前运行：

```bash
npm run fix-check
npm test
```

Provider 代码位于 `src/providers/<service>`。Provider 贡献规则见
[CONTRIBUTING.md](../CONTRIBUTING.md#adding-providers)。

## 许可证范围

除非另有说明，本仓库中的源代码、脚本、生成的项目脚手架、测试和文档均基于 Apache License, Version
2.0 授权。见 [LICENSE.txt](../LICENSE.txt)。

本仓库的 Apache-2.0 许可证不授予任何第三方产品、provider、app、API、商标、服务标识、商号、logo、
icon、品牌资产、文档、截图或其它归属于相应权利人的版权材料的使用权。

Provider 和 app 名称、metadata、链接、scope、permission 以及可选 logo/icon 仅用于识别服务和实现互操作。
所有第三方品牌和产品权利仍归各自权利人所有。本 catalog 中出现某个服务不代表其权利人对本项目的认可、赞助、合作、认证或验证。

如果你贡献 provider metadata 或资产，请只提交你有权提交的材料。优先链接到官方公开资产，而不是把品牌文件复制到本仓库。

## 社区

请让 issue 和 pull request 保持聚焦、尊重且可执行。参与本项目需遵守 [CODE_OF_CONDUCT.md](../CODE_OF_CONDUCT.md)。

---
FILE: ./open-connector/docs/catalog-format.md
---
# Catalog Format

Provider definitions in `src/providers/<service>/definition.ts` are the source of truth.
Catalog JSON in `catalog/apps` is generated local runtime data and used by the server at startup.
Generated registry and catalog files are ignored by git. `npm install`, `npm run dev`, and
`npm start` create them when they are missing or stale.

Provider executors live in `src/providers/<service>/executors.ts` and are loaded only when an action is executed.

Do not hand-edit generated catalog files as source. Update provider definitions and run:

```bash
npm run generate:catalog
```

At runtime, catalog responses add execution status that is not stored in generated catalog JSON:

- `locallyExecutable`: the open-source runtime has a local executor for the action.
- `catalogOnly`: schemas and metadata are available, but no local executor is wired yet.
- `needsCredential`: the provider needs a configured local connection before execution.
- `noAuthRunnable`: the action belongs to a provider that can run without stored credentials.

Action definitions also declare provider-native `requiredScopes` and `providerPermissions`. The
runtime exposes those fields through HTTP and MCP discovery together with the current connection
profile, so agents can see both the capability they are about to use and the account it will run as.

For the full contribution workflow, see `.codex/skills/add-provider/SKILL.md`.

---
FILE: ./open-connector/docs/cloudflare.md
---
# Cloudflare Deployment

OpenConnector supports Cloudflare Workers as a metadata and runtime-state deployment target. The
Worker runtime uses:

- Workers for the HTTP runtime.
- D1 for connections, OAuth config/state, runtime tokens, and run logs.
- R2 or Workers KV for temporary transit files.
- Static Assets for the Web Console.

## Prerequisites

- A Cloudflare account with Workers, D1, and either R2 or Workers KV access.
- Wrangler available through `npx wrangler`.
- Node.js 22 or newer.

## Create Local Config

Install dependencies and copy the example Wrangler config:

```bash
npm install
cp wrangler.example.jsonc wrangler.local.jsonc
```

`wrangler.local.jsonc` is ignored by git. Fill it with your Cloudflare resource IDs before remote
deployment.

## Log In With Wrangler

Skip this step if you are already logged in:

```bash
npx wrangler login
```

## Create Cloudflare Resources

Create the D1 database:

```bash
npx wrangler d1 create open-connector
```

Then choose one transit-file backend. R2 is the default and supports files larger than 25 MiB:

```bash
npx wrangler r2 bucket create open-connector-transit-files
```

Alternatively, create a Workers KV namespace for lightweight deployments that do not need files
larger than 25 MiB:

```bash
npx wrangler kv namespace create open-connector-transit-files
```

Put the returned D1 `database_id` and R2 bucket name or KV namespace `id` into
`wrangler.local.jsonc`. For KV, comment out the `r2_buckets` block, uncomment the
`kv_namespaces` block, and set `TRANSIT_FILES_BACKEND` to `"kv"` as shown in the example config.
Only one backend may use the `TRANSIT_FILES` binding. All Wrangler commands that read the Worker
config should use `--config wrangler.local.jsonc`.

## Local Worker Preview

The local Worker preview stores its D1, R2, and KV data under the ignored `.wrangler` directory.
This data is separate from the remote Cloudflare resources.

Remote secrets set with `wrangler secret put` are not available to the local preview. To test local
admin authentication and credential encryption, add separate local values to the ignored `.env`
file before starting the Worker:

```dotenv
OOMOL_CONNECT_ADMIN_TOKEN=replace-with-a-local-admin-token
OOMOL_CONNECT_ENCRYPTION_KEY=replace-with-a-local-encryption-key
```

Apply the migrations to Wrangler's local D1 state, then start the Worker:

```bash
npx wrangler d1 migrations apply open-connector --local --config wrangler.local.jsonc
npm run dev:cloudflare
```

`npm run dev:cloudflare` generates the catalog, builds the Web Console, copies catalog assets, and
runs `wrangler dev --config wrangler.local.jsonc`. The local Worker preview uses the same generated
provider Action executor registry as the Node runtime.

Check the local Worker and open the Web Console at `http://localhost:8787`:

```bash
curl http://localhost:8787/health
```

The health endpoint should return `{"ok":true}`.

## Remote Deployment

Apply migrations to the remote D1 database:

```bash
npx wrangler d1 migrations apply open-connector --remote --config wrangler.local.jsonc
```

Generate two independent random values by running this command twice:

```bash
openssl rand -base64 32
```

Store the encryption key in a password manager or another external secrets vault. If it is lost or
changed, credentials and OAuth client configuration already encrypted in D1 cannot be recovered.
Keep the admin token available to operators who need the Web Console or admin API.

Paste the generated values when Wrangler prompts for each secret:

```bash
npx wrangler secret put OOMOL_CONNECT_ADMIN_TOKEN --config wrangler.local.jsonc
npx wrangler secret put OOMOL_CONNECT_ENCRYPTION_KEY --config wrangler.local.jsonc
```

Deploy:

```bash
npm run deploy:cloudflare
```

`npm run deploy:cloudflare` generates the catalog, builds the Web Console, copies catalog assets,
and runs `wrangler deploy --config wrangler.local.jsonc`. The copied `wrangler.local.jsonc` already
maps the built Web Console assets to the `ASSETS` binding used by the Worker.

Use the Worker URL printed by Wrangler to check the deployed runtime, then open the same URL in a
browser and enter the admin token to access the Web Console:

```bash
curl https://open-connector.example.workers.dev/health
```

The health endpoint should return `{"ok":true}`. Replace the example URL with the deployed Worker
URL or your custom domain.

## Runtime Behavior

The Cloudflare runtime serves catalog metadata, `/api` and `/v1` metadata endpoints, connections,
runtime tokens, OAuth config/state, transit files backed by the configured R2 bucket or KV
namespace, and the generated provider Action executor registry.

Configure an R2 lifecycle rule for the transit bucket if you want unread expired transit files
cleaned up automatically. Workers KV applies the configured TTL when each file is written and
deletes it automatically. KV clamps `OOMOL_CONNECT_TRANSIT_FILE_TTL_SECONDS` to a minimum of 60
seconds and `OOMOL_CONNECT_TRANSIT_FILE_MAX_BYTES` to a maximum of 25 MiB.

## Configuration

Cloudflare uses the same environment variable names for origin, auth tokens, action policy, transit
file limits, and credential encryption. `PORT`, `HOST`, and `OOMOL_CONNECT_DATA_DIR` are local
Node-only settings on Workers.

See [configuration.md](configuration.md) for all runtime environment variables.

---
FILE: ./open-connector/docs/configuration.md
---
# Configuration

OpenConnector is configured with environment variables.

| Variable                                 | Default                   | Purpose                                                                        |
| ---------------------------------------- | ------------------------- | ------------------------------------------------------------------------------ |
| `PORT`                                   | `3000`                    | Local HTTP server port.                                                        |
| `HOST`                                   | `127.0.0.1`               | Bind address. Docker image sets `0.0.0.0`.                                     |
| `OOMOL_CONNECT_ORIGIN`                   | `http://localhost:<PORT>` | Public origin used for OAuth redirect URLs.                                    |
| `OOMOL_CONNECT_DATA_DIR`                 | `./data`                  | Directory containing `connect.sqlite`. Docker image sets `/app/data`.          |
| `OOMOL_CONNECT_ENCRYPTION_KEY`           | unset                     | Enables AES-256-GCM encryption for stored credentials and OAuth client config. |
| `OOMOL_CONNECT_NEW_ENCRYPTION_KEY`       | unset                     | New key used by `runtime:data rotate-key`.                                     |
| `OOMOL_CONNECT_ADMIN_TOKEN`              | unset                     | Requires bearer-token auth for local admin API, docs, and web console.         |
| `OOMOL_CONNECT_RUNTIME_TOKEN`            | unset                     | Optional bootstrap runtime bearer token for `/v1` and MCP callers.             |
| `OOMOL_CONNECT_ALLOWED_ACTIONS`          | unset                     | Comma-separated executable action allowlist. Supports `service.*`.             |
| `OOMOL_CONNECT_BLOCKED_ACTIONS`          | unset                     | Comma-separated executable action denylist. Supports `service.*`.              |
| `OOMOL_CONNECT_ALLOWED_PROXIES`          | unset                     | Comma-separated provider proxy allowlist. Supports service names and `*`.      |
| `OOMOL_CONNECT_BLOCKED_PROXIES`          | unset                     | Comma-separated provider proxy denylist. Supports service names and `*`.       |
| `OOMOL_CONNECT_ALLOW_PRIVATE_NETWORK`    | `false`                   | Allow self-hosted provider connections to target private networks. See below.  |
| `OOMOL_CONNECT_TRANSIT_FILE_TTL_SECONDS` | `86400`                   | Transit file lifetime before cleanup.                                          |
| `OOMOL_CONNECT_TRANSIT_FILE_MAX_BYTES`   | `104857600`               | Maximum transit file upload size.                                              |

Example:

```bash
OOMOL_CONNECT_DATA_DIR="$PWD/data" \
OOMOL_CONNECT_ENCRYPTION_KEY="replace-with-a-long-random-secret" \
OOMOL_CONNECT_ADMIN_TOKEN="replace-with-an-admin-token" \
OOMOL_CONNECT_ALLOWED_ACTIONS="hackernews.*,github.get_current_user" \
OOMOL_CONNECT_ALLOWED_PROXIES="github" \
npm run dev
```

Create persistent runtime tokens from the web console Access tab or `POST /api/runtime-tokens`.
Only token hashes are stored in SQLite. `OOMOL_CONNECT_RUNTIME_TOKEN` remains available for
bootstrap scripts and backward compatibility.

## Private network access

By default OpenConnector applies a public-only SSRF guard to every user-supplied
URL, including self-hosted provider instance URLs (for example the Dokploy
**Instance URL**). Connections may therefore only target public addresses, and
private targets are rejected during connection setup.

Some self-hosted services are only reachable over a LAN or an overlay network
such as Tailscale or NetBird. To allow those connections, set
`OOMOL_CONNECT_ALLOW_PRIVATE_NETWORK=true`. When enabled, provider connections
that opt in (currently **Dokploy**) may target:

- RFC 1918 ranges: `10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`
- Carrier-grade NAT / shared address space `100.64.0.0/10` (Tailscale, NetBird)
- Private hostname suffixes: `.local`, `.internal`, `.home`, `.lan`

The following targets stay blocked even when the flag is enabled:

- Loopback and localhost (`127.0.0.0/8`, `localhost`, `.localhost`)
- Link-local and cloud metadata (`169.254.0.0/16`, `100.100.100.200/32`, and
  metadata hostnames such as `metadata.google.internal`)
- Reserved, multicast, and broadcast ranges, and all IPv6 targets

> **Enable this only on a single-tenant, self-hosted runtime that you operate.**
> On a shared or multi-tenant deployment, turning it on lets any connection
> owner reach the operator's internal network from the runtime's egress
> position, so leave it at the `false` default there.

## Cloudflare Workers

Cloudflare uses the same environment variable names for origin, auth tokens, execution policy,
transit file limits, and credential encryption. `PORT`, `HOST`, and `OOMOL_CONNECT_DATA_DIR` are local
Node-only settings on Workers.

The Worker runtime also requires these bindings in `wrangler.local.jsonc`. Copy
`wrangler.example.jsonc` to `wrangler.local.jsonc` and fill in your own Cloudflare resource IDs
before running Wrangler commands.

- `DB`: D1 database for connections, OAuth config/state, runtime tokens, and run logs.
- `TRANSIT_FILES`: R2 bucket or Workers KV namespace for temporary transit files.
- `ASSETS`: Workers Static Assets binding for the web console.

R2 is the default transit-file backend. To use Workers KV, bind the KV namespace as
`TRANSIT_FILES` and set the Wrangler variable `TRANSIT_FILES_BACKEND` to `"kv"`. Configure exactly
one R2 bucket or KV namespace with that binding name. KV limits each file to 25 MiB, clamps the
transit-file TTL to a minimum of 60 seconds, and deletes expired files automatically.

Set secrets with Wrangler instead of committing them to config:

```bash
npx wrangler secret put OOMOL_CONNECT_ADMIN_TOKEN --config wrangler.local.jsonc
npx wrangler secret put OOMOL_CONNECT_ENCRYPTION_KEY --config wrangler.local.jsonc
```

---
FILE: ./open-connector/docs/credentials.md
---
# Credentials And Local Storage

The local Node runtime stores connections, OAuth client configuration, pending OAuth states, and
recent run logs in SQLite. The Cloudflare Workers runtime stores the same runtime records in D1 and
temporary transit files in R2.

By default the database lives at:

```text
./data/connect.sqlite
```

Set `OOMOL_CONNECT_DATA_DIR` to use another directory. The Docker image defaults this to
`/app/data`, which is intended to be mounted as a volume.

- `no_auth` providers are available as virtual connections and do not store secrets.
- `api_key` and `custom_credential` providers store their local secrets in SQLite.
- `oauth2` providers use user-provided OAuth client configuration and a localhost callback URL.

## Encryption

Set `OOMOL_CONNECT_ENCRYPTION_KEY` to encrypt stored credentials and OAuth client secrets:

```bash
OOMOL_CONNECT_ENCRYPTION_KEY="replace-with-a-long-random-secret" npm run dev
```

The runtime uses AES-256-GCM for records that contain provider credentials or OAuth client
configuration. The key is not stored by OpenConnector; if it is lost, encrypted records cannot be
recovered.

Without `OOMOL_CONNECT_ENCRYPTION_KEY`, the runtime stays usable for local development and prints a
startup warning. In that mode, treat `connect.sqlite` as a sensitive local file.

## Credential Fields

Credential fields are declared by each provider's catalog `auth` metadata. The runtime treats that
metadata as the contract for local API requests:

- `api_key` connections always require `values.apiKey`.
- `api_key` connections may declare additional `extraFields`.
- `custom_credential` connections require exactly the provider-declared `fields`.
- `oauth2` client config may declare additional `clientConfigFields`.

All submitted string values are trimmed. Empty strings are treated as missing. Unknown submitted
fields are rejected instead of being silently stored, because credential forms, scripts, and provider
definitions should fail fast when they drift.

Inspect a provider before writing setup scripts:

```bash
curl -s http://localhost:3000/api/providers/github
```

## Connection Identity

When a provider can cheaply validate credentials against a current-user or current-account endpoint,
its validator stores a stable connection profile:

- `accountId`: provider-side user, workspace, bot, account, or token identifier.
- `displayName`: human-readable account label.
- `grantedScopes`: provider-native scopes granted to the credential, when known.

The runtime exposes this profile in `/api/connections`, MCP action discovery, action agent guides,
and recent run logs. Agents should use it to understand which account an action will run as; raw
provider tokens are never exposed.

Check current connections:

```bash
curl -s http://localhost:3000/api/connections
```

## API Key Connections

Create or replace the default API key connection:

```bash
curl -s -X PUT http://localhost:3000/api/connections/github \
  -H 'content-type: application/json' \
  -d '{"authType":"api_key","values":{"apiKey":"github_pat_..."}}'
```

Create or replace a named API key connection:

```bash
curl -s -X PUT http://localhost:3000/api/connections/github \
  -H 'content-type: application/json' \
  -d '{"authType":"api_key","connectionName":"work","values":{"apiKey":"github_pat_..."}}'
```

The accepted keys are `apiKey` plus the provider's `auth[].extraFields`.

Execute an action with the default connection:

```bash
curl -s -X POST http://localhost:3000/v1/actions/github.get_current_user \
  -H 'content-type: application/json' \
  -d '{"input":{}}'
```

## Custom Credential Connections

Create or replace the default custom credential connection:

```bash
curl -s -X PUT http://localhost:3000/api/connections/example \
  -H 'content-type: application/json' \
  -d '{"authType":"custom_credential","values":{"host":"localhost","password":"..."}}'
```

The accepted keys come from the provider's `auth[].fields`.

## OAuth2 Connections

OAuth2 providers require your own provider OAuth app. List OAuth-capable providers and copy the
`expectedRedirectUri` for the service:

```bash
curl -s http://localhost:3000/api/oauth/configs
```

Paste that exact callback URL into the provider OAuth app. With the default port, GitHub uses:

```text
http://localhost:3000/oauth/callback
```

If the browser reaches the runtime through another origin, set `OOMOL_CONNECT_ORIGIN` before
starting the runtime:

```bash
OOMOL_CONNECT_ORIGIN="https://your-tunnel.example" npm run dev
```

Then use the new `expectedRedirectUri` returned by `/api/oauth/configs`.

Store the local client configuration:

```bash
curl -s -X PUT http://localhost:3000/api/oauth/configs/github \
  -H 'content-type: application/json' \
  -d '{"clientId":"...","clientSecret":"..."}'
```

Some providers declare additional OAuth client fields in `auth[].clientConfigFields`; send those as
`extra`.

Start authorization:

```bash
curl -s -X POST http://localhost:3000/api/oauth/authorizations \
  -H 'content-type: application/json' \
  -d '{"service":"github"}'
```

Open the returned `authorizationUrl` in a browser. After the provider redirects to the local
callback URL, the runtime stores the OAuth credential as the default connection.

To store the OAuth credential as a named connection, include `connectionName` when starting
authorization:

```bash
curl -s -X POST http://localhost:3000/api/oauth/authorizations \
  -H 'content-type: application/json' \
  -d '{"service":"github","connectionName":"work"}'
```

Protect the local SQLite database like any other file containing API keys or OAuth tokens.

## Selecting A Connection For Execution

The default connection is used when no alias is provided:

```bash
curl -s -X POST http://localhost:3000/v1/actions/github.get_current_user \
  -H 'content-type: application/json' \
  -d '{"input":{}}'
```

If a named connection already exists, select it with `x-oo-connector-alias`:

```bash
curl -s -X POST http://localhost:3000/v1/actions/github.get_current_user \
  -H 'x-oo-connector-alias: work' \
  -H 'content-type: application/json' \
  -d '{"input":{}}'
```

The `alias` query parameter is also accepted:

```bash
curl -s -X POST "http://localhost:3000/v1/actions/github.get_current_user?alias=work" \
  -H 'content-type: application/json' \
  -d '{"input":{}}'
```

## Reset And Key Rotation

Reset local runtime data:

```bash
npm run runtime:data -- reset --yes
```

Rotate the local SQLite credential encryption key:

```bash
OOMOL_CONNECT_ENCRYPTION_KEY="old-secret" \
OOMOL_CONNECT_NEW_ENCRYPTION_KEY="new-secret" \
npm run runtime:data -- rotate-key
```

Remove local SQLite credential encryption only when you intentionally want plaintext local storage:

```bash
OOMOL_CONNECT_ENCRYPTION_KEY="old-secret" \
npm run runtime:data -- rotate-key --plain
```

`runtime:data` is for the local SQLite runtime only. For Cloudflare, back up and restore D1/R2
directly with Cloudflare tooling.

## OAuth Token Refresh

OAuth access tokens are refreshed automatically when they are expired and the provider issued a
refresh token. Refreshed credentials are written back to the local SQLite store, using encryption
when `OOMOL_CONNECT_ENCRYPTION_KEY` is configured.

If a token is expired and no refresh token is available, reconnect the provider from the local
runtime. Providers such as Google may require authorization parameters that request offline access;
provider definitions should include those parameters when refresh tokens are expected.

## Local API Access

The server binds to `127.0.0.1` by default. Set `HOST=0.0.0.0` only when the runtime must be
reachable from outside the local machine or container.

Set an admin bearer token when the admin API or web console is reachable outside your own shell:

```bash
OOMOL_CONNECT_ADMIN_TOKEN="replace-with-an-admin-token" npm run dev
```

Admin clients calling `/api`, `/docs`, or the web console should send:

```text
Authorization: Bearer replace-with-an-admin-token
```

Create runtime tokens for `/v1` and `/mcp` callers from the web console Access tab or
`POST /api/runtime-tokens`. The token is shown once when created; only a hash is stored in SQLite.
Runtime clients should send `Authorization: Bearer oct_...`.

`OOMOL_CONNECT_RUNTIME_TOKEN` is still accepted for bootstrap scripts and backward compatibility.

The bundled web console receives a same-site local cookie from the runtime so it can keep working
when API-token authentication is enabled.

## Action Policy

Use `OOMOL_CONNECT_ALLOWED_ACTIONS` to expose only selected actions to HTTP and MCP execution:

```bash
OOMOL_CONNECT_ALLOWED_ACTIONS="hackernews.*,github.get_current_user" npm run dev
```

Use `OOMOL_CONNECT_BLOCKED_ACTIONS` to deny specific actions even when a broader allowlist includes
them:

```bash
OOMOL_CONNECT_ALLOWED_ACTIONS="github.*" \
OOMOL_CONNECT_BLOCKED_ACTIONS="github.delete_repository" \
npm run dev
```

Provider proxy requests use separate service-level policy variables because `/v1/proxy/:service`
can reach provider API endpoints beyond the curated action catalog. If any action policy is
configured, proxy requests are denied by default unless the service is explicitly allowed:

```bash
OOMOL_CONNECT_ALLOWED_ACTIONS="github.get_current_user" \
OOMOL_CONNECT_ALLOWED_PROXIES="github" \
npm run dev
```

Use `OOMOL_CONNECT_BLOCKED_PROXIES` to deny provider proxies even when `OOMOL_CONNECT_ALLOWED_PROXIES`
contains `*`:

```bash
OOMOL_CONNECT_ALLOWED_PROXIES="*" \
OOMOL_CONNECT_BLOCKED_PROXIES="github" \
npm run dev
```

Action policy entries are comma-separated action ids. A provider-wide wildcard such as `gmail.*`
matches all actions for that provider. Proxy policy entries are comma-separated provider service
names, or `*` for all provider proxies.

---
FILE: ./open-connector/docs/docker-ghcr.md
---
[English](docker-ghcr.md) | [简体中文](docker-ghcr.zh-CN.md)

# Docker Image (GHCR)

OpenConnector ships a prebuilt Docker image on the GitHub Packages container registry (GHCR), so you
can run it without cloning the repository or building anything. The image is:

```text
ghcr.io/oomol-lab/open-connector
```

## Choose A Tag

| Tag           | Points at                            | Use it when                                                    |
| ------------- | ------------------------------------ | -------------------------------------------------------------- |
| `latest`      | the newest published release         | you want the current stable runtime                            |
| `v1.0.0`      | a specific release (immutable)       | you deploy to production and want a pinned, reproducible build |
| `tip`         | the latest commit on `main`          | you want to try changes that are not released yet              |
| `<short-sha>` | a specific `main` commit (immutable) | you want to pin an exact pre-release build                     |

For production, pin a released version such as `v1.0.0`.

## Pull

The image is public, so no sign-in is required:

```bash
docker pull ghcr.io/oomol-lab/open-connector:latest
```

If you get an `unauthorized` or `denied` error, sign in with a GitHub token that has the
`read:packages` scope:

```bash
echo "$GITHUB_TOKEN" | docker login ghcr.io -u <github-username> --password-stdin
```

The image is multi-arch (`linux/amd64` + `linux/arm64`), so Docker automatically pulls the variant
that matches your machine — running natively on Intel/AMD hosts and on arm64 hosts such as Apple
Silicon and AWS Graviton. No `--platform` flag is needed.

## Run

The image listens on port `3000`, binds to `0.0.0.0`, and stores runtime data in `/app/data`.

First generate the runtime secrets and save them somewhere safe. `OOMOL_CONNECT_ENCRYPTION_KEY`
encrypts stored credentials and OAuth client secrets; if it is lost, the encrypted data in
`/app/data` cannot be recovered. `OOMOL_CONNECT_ADMIN_TOKEN` authenticates the admin API and console.

```bash
# Save both values in a password manager or secrets vault before running.
export OOMOL_CONNECT_ENCRYPTION_KEY=$(openssl rand -base64 32)
export OOMOL_CONNECT_ADMIN_TOKEN=$(openssl rand -base64 32)
```

Then run the image, mounting a volume so data survives restarts:

```bash
docker run -d \
  --name open-connector \
  -p 3000:3000 \
  -v open_connector_data:/app/data \
  -e OOMOL_CONNECT_ORIGIN="https://api.example.com" \
  -e OOMOL_CONNECT_ENCRYPTION_KEY="$OOMOL_CONNECT_ENCRYPTION_KEY" \
  -e OOMOL_CONNECT_ADMIN_TOKEN="$OOMOL_CONNECT_ADMIN_TOKEN" \
  ghcr.io/oomol-lab/open-connector:latest
```

See [configuration.md](configuration.md) for the full environment variable reference and
[credentials.md](credentials.md) for connecting providers.

### Docker Compose

The repository ships a [`docker-compose.yml`](../docker-compose.yml) that runs this published image.
From a checkout, export the secrets shown above and start it:

```bash
docker compose up
```

To build from source instead of pulling, add the build overlay:

```bash
docker compose -f docker-compose.yml -f docker-compose.build.yml up --build
```

## Verify

Check the health endpoint:

```bash
curl http://localhost:3000/health
```

The expected response is:

```json
{ "ok": true }
```

## How Images Are Published

Images are built and pushed automatically, so the tags above stay current: every push to `main`
updates `tip` and adds the `<short-sha>` tag, and every published release adds `latest` and the
release version. Each tag is a multi-arch manifest built natively for `linux/amd64` and
`linux/arm64`. The build is defined in
[`.github/workflows/publish-docker.yml`](../.github/workflows/publish-docker.yml).

---
FILE: ./open-connector/docs/docker-ghcr.zh-CN.md
---
[English](docker-ghcr.md) | [简体中文](docker-ghcr.zh-CN.md)

# Docker 镜像（GHCR）

OpenConnector 在 GitHub Packages 容器镜像仓库（GHCR）提供了预构建的 Docker 镜像，你无需克隆仓库或自己构建即可
运行 OpenConnector。镜像地址为：

```text
ghcr.io/oomol-lab/open-connector
```

## 选择标签（Tag）

| 标签          | 指向                             | 适用场景                               |
| ------------- | -------------------------------- | -------------------------------------- |
| `latest`      | 最新发布的 release               | 想要当前的稳定 runtime                 |
| `v1.0.0`      | 某个具体 release（不可变）       | 部署到生产环境，需要固定、可复现的构建 |
| `tip`         | `main` 上的最新 commit           | 想体验尚未发布的改动                   |
| `<short-sha>` | 某个具体 `main` commit（不可变） | 想固定到某个确切的预发布构建           |

生产环境请固定到某个 release 版本，例如 `v1.0.0`。

## 拉取

镜像是 public 的，无需登录即可拉取：

```bash
docker pull ghcr.io/oomol-lab/open-connector:latest
```

如果遇到 `unauthorized` 或 `denied` 错误，用带 `read:packages` scope 的 GitHub token 登录：

```bash
echo "$GITHUB_TOKEN" | docker login ghcr.io -u <github-username> --password-stdin
```

镜像是多架构的（`linux/amd64` + `linux/arm64`），Docker 会自动拉取与你机器匹配的那个变体——在 Intel/AMD
主机和 arm64 主机（如 Apple Silicon、AWS Graviton）上都是原生运行，无需 `--platform` 参数。

## 运行

镜像监听 `3000` 端口，绑定到 `0.0.0.0`，并把运行时数据存放在 `/app/data`。

先生成运行时 secret 并妥善保存。`OOMOL_CONNECT_ENCRYPTION_KEY` 用于加密存储的凭据和 OAuth client secret；一旦
丢失，`/app/data` 里加密的数据将无法恢复。`OOMOL_CONNECT_ADMIN_TOKEN` 用于 admin API 和控制台的鉴权。

```bash
# 运行前请把两个值保存到密码管理器或 secrets vault。
export OOMOL_CONNECT_ENCRYPTION_KEY=$(openssl rand -base64 32)
export OOMOL_CONNECT_ADMIN_TOKEN=$(openssl rand -base64 32)
```

然后运行镜像，并挂载 volume 让数据在重启后保留：

```bash
docker run -d \
  --name open-connector \
  -p 3000:3000 \
  -v open_connector_data:/app/data \
  -e OOMOL_CONNECT_ORIGIN="https://api.example.com" \
  -e OOMOL_CONNECT_ENCRYPTION_KEY="$OOMOL_CONNECT_ENCRYPTION_KEY" \
  -e OOMOL_CONNECT_ADMIN_TOKEN="$OOMOL_CONNECT_ADMIN_TOKEN" \
  ghcr.io/oomol-lab/open-connector:latest
```

完整环境变量参考见 [configuration.md](configuration.md)，连接 provider 见 [credentials.md](credentials.md)。

### Docker Compose

仓库自带一个 [`docker-compose.yml`](../docker-compose.yml)，直接运行这个发布镜像。在仓库目录下，先 export
上面的 secret，再启动：

```bash
docker compose up
```

想改为从源码构建而不是拉取：

```bash
docker compose -f docker-compose.yml -f docker-compose.build.yml up --build
```

## 验证

检查健康检查端点：

```bash
curl http://localhost:3000/health
```

预期响应为：

```json
{ "ok": true }
```

## 镜像如何发布

镜像会自动构建并推送，因此上面的标签始终保持最新：每次 push 到 `main` 会更新 `tip` 并新增 `<short-sha>`
标签，每次发布 release 会新增 `latest` 和 release 版本号。每个标签都是为 `linux/amd64` 和 `linux/arm64`
原生构建的多架构 manifest。构建定义见
[`.github/workflows/publish-docker.yml`](../.github/workflows/publish-docker.yml)。

---
FILE: ./open-connector/docs/fly-io.md
---
# Fly.io Deployment

OpenConnector can run on Fly.io as the Node Docker runtime with persistent SQLite storage. Fly
provides TLS termination, remote Docker builds, health checks, rolling deploys, and optional custom
domains.

This deployment uses the repository's `docker/Dockerfile`, the Fly app config in `fly.toml`, and a
Fly volume mounted at `/app/data`.

## Prerequisites

- A Fly.io account.
- `flyctl` installed and authenticated with `fly auth login`.
- Docker available locally, or Fly remote builders enabled.
- A public origin for OAuth callback URLs, such as `https://api.example.com` or the default
  `https://<app>.fly.dev` hostname.

## Create The App

Create a Fly app without deploying yet:

```bash
fly apps create my-open-connector
```

Fly app names are globally unique. If you choose a different name, update the `app` field in
`fly.toml` before deploying:

```toml
app = "my-open-connector"
```

## Create Persistent Storage

The Docker image stores runtime data in `/app/data`. Create a Fly volume with the same source name
as `fly.toml`:

```bash
fly volumes create open_connector_data \
  --region iad \
  --size 1 \
  --app my-open-connector
```

Increase `--size` if you expect large run logs, many stored credentials, or heavy temporary file
transit usage.

## Set Secrets

Store production secrets with Fly instead of committing them to `fly.toml`:

```bash
OOMOL_CONNECT_ENCRYPTION_KEY=$(openssl rand -base64 32)
OOMOL_CONNECT_ADMIN_TOKEN=$(openssl rand -base64 32)
OOMOL_CONNECT_RUNTIME_TOKEN=$(openssl rand -base64 32)

fly secrets set \
  OOMOL_CONNECT_ORIGIN="https://my-open-connector.fly.dev" \
  OOMOL_CONNECT_ENCRYPTION_KEY="$OOMOL_CONNECT_ENCRYPTION_KEY" \
  OOMOL_CONNECT_ADMIN_TOKEN="$OOMOL_CONNECT_ADMIN_TOKEN" \
  OOMOL_CONNECT_RUNTIME_TOKEN="$OOMOL_CONNECT_RUNTIME_TOKEN" \
  --app my-open-connector
```

Keep `OOMOL_CONNECT_ENCRYPTION_KEY` in a password manager or another external secrets vault. If the
key is lost, encrypted credentials and OAuth client secrets in the SQLite database cannot be
recovered.

Optional runtime policy can also be set as secrets:

```bash
fly secrets set \
  OOMOL_CONNECT_ALLOWED_ACTIONS="github.*,hackernews.*" \
  OOMOL_CONNECT_ALLOWED_PROXIES="github" \
  --app my-open-connector
```

See [configuration.md](configuration.md) for the full environment variable reference.

## Deploy

Deploy from the repository root:

```bash
fly deploy --config fly.toml --remote-only
```

The Fly config uses:

- `docker/Dockerfile` for the image build.
- `internal_port = 3000` for the Node runtime.
- `/health` for HTTP health checks.
- `/app/data` as the mounted persistent data directory.

## Verify The Runtime

Check the health endpoint:

```bash
curl https://my-open-connector.fly.dev/health
```

The expected response is:

```json
{ "ok": true }
```

View logs when diagnosing deployment or startup issues:

```bash
fly logs --app my-open-connector
```

## Configure OAuth Redirects

For OAuth2 providers, set `OOMOL_CONNECT_ORIGIN` to the public origin users will access. The runtime
builds provider callback URLs from that origin and `/oauth/callback`.

For example, with:

```bash
OOMOL_CONNECT_ORIGIN="https://api.example.com"
```

the OAuth callback URL is:

```text
https://api.example.com/oauth/callback
```

Add that exact callback URL to each provider OAuth app.

## Custom Domain

Register the domain with Fly:

```bash
fly certs add api.example.com --app my-open-connector
```

Fly prints the DNS records to create. After DNS is ready, update the public origin:

```bash
fly secrets set \
  OOMOL_CONNECT_ORIGIN="https://api.example.com" \
  --app my-open-connector
```

Check certificate status:

```bash
fly certs check api.example.com --app my-open-connector
```

## Updating

Deploy new versions from the repository root:

```bash
git pull
fly deploy --config fly.toml --remote-only
```

The mounted volume keeps `connect.sqlite` and transit files across deployments.

## Scaling

`fly.toml` defaults to suspending the single machine when idle:

```toml
[http_service]
min_machines_running = 0
```

For production traffic that should avoid cold starts, keep one machine running:

```toml
[http_service]
min_machines_running = 1
```

Keep the machine count at one for the default SQLite deployment. Fly volumes are attached to
individual machines, so horizontal scaling requires a separate shared storage design. For this
repository's default Fly setup, prefer increasing the VM size before adding more machines.

---
FILE: ./open-connector/docs/gmail-oauth-sdk.md
---
# Gmail OAuth And SDK Tutorial

This guide starts after you already have a Gmail OAuth client. It does not cover creating or
configuring the OAuth app in Google Cloud. OpenConnector only needs the resulting client id, client
secret, and a redirect URI that the OAuth app allows.

## Prerequisites

- The local OpenConnector runtime can run with Node.js 22 or newer.
- You have a Gmail OAuth `clientId` and `clientSecret`.
- The Gmail OAuth app allows this runtime's redirect URI. The URI is the current runtime origin plus
  `/oauth/callback`.

If the runtime is reachable through a tunnel or another public origin, set `OOMOL_CONNECT_ORIGIN`
before starting it. The redirect URI is derived from that origin by appending `/oauth/callback`.

```bash
OOMOL_CONNECT_ORIGIN="https://your-runtime.example" npm run dev
```

For plain local development, start the runtime normally:

```bash
npm install
npm run dev
```

The examples below use `http://localhost:3000`. If you configured
`OOMOL_CONNECT_ADMIN_TOKEN` or a runtime token, add the matching `Authorization: Bearer ...` header
to admin and `/v1` requests.

OAuth redirect URI is shared by all services in the same runtime. Configure the Gmail OAuth app to
allow the current runtime origin plus `/oauth/callback`. With the default local origin, the redirect
URI is:

```txt
http://localhost:3000/oauth/callback
```

## 1. Store The Gmail OAuth Client

Open the local console at `http://localhost:3000`, open the Gmail provider page, and choose
**Configure OAuth Client**. Paste the Gmail OAuth `clientId` into **Client ID**, paste the
`clientSecret` into **Client Secret**, then choose **Save OAuth Client**.

![Gmail OAuth client form](../assets/gmail-oauth-client.png)

After saving, the Gmail provider page should allow you to start the connection flow.

## 2. Authorize A Gmail Account

After the OAuth client is configured, the Gmail provider page shows **Connect Gmail**. Choose that
button to start the OAuth authorization flow.

![Gmail connection action](../assets/gmail-connect.png)

Finish consent in the browser. After Gmail redirects back to the runtime, OpenConnector stores the
OAuth credential as the default Gmail connection.

After the callback completes, the Gmail provider page shows the connected OAuth state.

![Gmail connected state](../assets/gmail-connected.png)

## 3. Create A Runtime Token

Before calling the runtime from your own code, create a runtime token from the local console. Open
the Access page, choose **Create Token**, name the client, and copy the token when it is shown.

![Create runtime token dialog](../assets/create-runtime-token.png)

Set the copied token in the shell that runs your app:

```bash
export OOMOL_CONNECT_RUNTIME_TOKEN="oct_..."
```

## 4. Verify Gmail Through HTTP

Run a Gmail Action through the runtime API with the runtime token created above:

```bash
curl -s -X POST http://localhost:3000/v1/actions/gmail.search_threads \
  -H "authorization: Bearer $OOMOL_CONNECT_RUNTIME_TOKEN" \
  -H 'content-type: application/json' \
  -d '{"input":{"query":"newer_than:7d","maxResults":5}}'
```

## 5. Call Gmail From The SDK

Install the SDK in your TypeScript project:

```bash
npm install @oomol-lab/connector
```

Use `OpenConnector` for a self-hosted OpenConnector runtime. `baseUrl` is the server origin, not a
`/v1` URL. Pass the runtime token you created above.

```ts
import { OpenConnector } from "@oomol-lab/connector";

const open = new OpenConnector({
  baseUrl: process.env.OPENCONNECTOR_BASE_URL ?? "http://localhost:3000",
  runtimeToken: process.env.OOMOL_CONNECT_RUNTIME_TOKEN,
});

const { threads } = await open.execute("gmail.search_threads", {
  query: "newer_than:7d",
  maxResults: 5,
});

console.log(threads);
```

The namespace form calls the same Action:

```ts
const { threads } = await open.gmail.search_threads({
  query: "from:someone@example.com",
  maxResults: 5,
});
```

For precise Gmail Action types, install the optional types package and import the Gmail registry once
in the process:

```bash
npm install -D @oomol-lab/connector-types
```

```ts
import "@oomol-lab/connector-types/gmail";
```

## Common Issues

- `redirect_uri_mismatch`: make sure the OAuth app allows the current runtime origin plus
  `/oauth/callback`.
- `oauth_client_config_not_found`: save the Gmail OAuth client in the local console before starting
  authorization.
- `connection_not_found`: finish the browser authorization step before calling Gmail Actions.
- `unauthorized`: create a runtime token from the Access page and pass it as
  `OOMOL_CONNECT_RUNTIME_TOKEN`.
- `insufficient_permissions`: reconnect Gmail after the OAuth app has the scopes needed by the
  Action you are calling.

---
FILE: ./open-connector/docs/gmail-oauth-sdk.zh-CN.md
---
# Gmail OAuth 和 SDK 接入教程

这篇教程从你已经拥有 Gmail OAuth client 开始，不包含在 Google Cloud 创建或配置 OAuth app 的步骤。OpenConnector 只需要这个 OAuth app 的 `clientId`、`clientSecret`，以及它允许当前 runtime 使用的 redirect URI。

## 前置条件

- 本地 OpenConnector runtime 可以在 Node.js 22 或更新版本上运行。
- 你已经有 Gmail OAuth `clientId` 和 `clientSecret`。
- Gmail OAuth app 已经允许当前 runtime 的 redirect URI。这个 URI 是当前 runtime origin 加上 `/oauth/callback`。

如果 runtime 通过 tunnel 或其他公网 origin 访问，启动前先设置 `OOMOL_CONNECT_ORIGIN`。redirect URI 会基于这个 origin 拼接 `/oauth/callback`。

```bash
OOMOL_CONNECT_ORIGIN="https://your-runtime.example" npm run dev
```

普通本地开发可以直接启动 runtime：

```bash
npm install
npm run dev
```

下面的示例默认使用 `http://localhost:3000`。如果你配置了 `OOMOL_CONNECT_ADMIN_TOKEN` 或 runtime token，对应的 admin 请求和 `/v1` 请求需要加上 `Authorization: Bearer ...` header。

同一个 runtime 里的所有服务共用同一个 OAuth redirect URI。给 Gmail OAuth app 配置当前 runtime origin 加 `/oauth/callback`。默认本地 origin 下是：

```txt
http://localhost:3000/oauth/callback
```

## 1. 保存 Gmail OAuth Client

打开本地控制台 `http://localhost:3000`，进入 Gmail provider 页面，点击 **Configure OAuth
Client**。把 Gmail OAuth `clientId` 填入 **Client ID**，把 `clientSecret` 填入 **Client
Secret**，然后点击 **Save OAuth Client**。

![Gmail OAuth client 表单](../assets/gmail-oauth-client.png)

保存后，Gmail provider 页面应允许你继续发起连接流程。

## 2. 授权 Gmail 账号

OAuth client 配置完成后，Gmail provider 页面会显示 **Connect Gmail**。点击这个按钮启动 OAuth
授权流程。

![Gmail 连接按钮](../assets/gmail-connect.png)

在浏览器里完成 Gmail consent。Gmail 跳回 runtime 后，OpenConnector 会把 OAuth credential 保存为默认 Gmail connection。

回调完成后，Gmail provider 页面会显示已通过 OAuth 连接。

![Gmail 连接成功状态](../assets/gmail-connected.png)

## 3. 创建 Runtime Token

从你自己的代码调用 runtime 前，先在本地控制台创建 runtime token。打开 Access 页面，点击 **Create Token**，给 client 命名，然后复制只显示一次的 token。

![创建 runtime token 弹窗](../assets/create-runtime-token.png)

在运行你的应用的 shell 里设置这个 token：

```bash
export OOMOL_CONNECT_RUNTIME_TOKEN="oct_..."
```

## 4. 用 HTTP 验证 Gmail Action

使用上一步创建的 runtime token，通过 runtime API 调用一个 Gmail Action：

```bash
curl -s -X POST http://localhost:3000/v1/actions/gmail.search_threads \
  -H "authorization: Bearer $OOMOL_CONNECT_RUNTIME_TOKEN" \
  -H 'content-type: application/json' \
  -d '{"input":{"query":"newer_than:7d","maxResults":5}}'
```

## 5. 用 SDK 调用 Gmail

在你的 TypeScript 项目里安装 SDK：

```bash
npm install @oomol-lab/connector
```

自托管 OpenConnector runtime 使用 `OpenConnector`。`baseUrl` 是 server origin，不是 `/v1` URL。传入前面创建的 runtime token。

```ts
import { OpenConnector } from "@oomol-lab/connector";

const open = new OpenConnector({
  baseUrl: process.env.OPENCONNECTOR_BASE_URL ?? "http://localhost:3000",
  runtimeToken: process.env.OOMOL_CONNECT_RUNTIME_TOKEN,
});

const { threads } = await open.execute("gmail.search_threads", {
  query: "newer_than:7d",
  maxResults: 5,
});

console.log(threads);
```

namespace 写法调用的是同一个 Action：

```ts
const { threads } = await open.gmail.search_threads({
  query: "from:someone@example.com",
  maxResults: 5,
});
```

如果希望获得精确的 Gmail Action 类型，可以安装可选的 types 包，并在进程里导入一次 Gmail registry：

```bash
npm install -D @oomol-lab/connector-types
```

```ts
import "@oomol-lab/connector-types/gmail";
```

## 常见问题

- `redirect_uri_mismatch`：确认 Gmail OAuth app 允许当前 runtime origin 加 `/oauth/callback`。
- `oauth_client_config_not_found`：启动授权前，先在本地控制台保存 Gmail OAuth client。
- `connection_not_found`：先在浏览器里完成授权，再调用 Gmail Action。
- `unauthorized`：在 Access 页面创建 runtime token，并通过 `OOMOL_CONNECT_RUNTIME_TOKEN` 传给 SDK。
- `insufficient_permissions`：OAuth app 具备 Action 所需 scope 后，重新授权 Gmail。

---
FILE: ./open-connector/docs/quickstart.md
---
# Quickstart

Install dependencies and start the local development servers:

```bash
npm install
npm run dev
```

`npm install` and `npm run dev` create local generated files when they are missing or stale.

Open the API reference at `http://localhost:3000/docs`.

## Run A No-Auth Action

Hacker News does not need credentials, so it is the fastest way to verify the runtime:

```bash
curl -s -X POST http://localhost:3000/v1/actions/hackernews.get_top_stories \
  -H 'content-type: application/json' \
  -d '{"input":{}}'
```

## Discover Actions

List services that expose actions:

```bash
curl -s http://localhost:3000/v1/actions
```

List action contracts for one service:

```bash
curl -s "http://localhost:3000/v1/actions?service=hackernews"
```

Get a local markdown guide for one action:

```bash
curl -s http://localhost:3000/api/actions/hackernews.get_top_stories/agent.md
```

Inspect local connections and the account identity exposed to users and agents:

```bash
curl -s http://localhost:3000/api/connections
```

## Configure An API Key Connection

Inspect the provider to see supported auth types and credential fields:

```bash
curl -s http://localhost:3000/api/providers/github
```

Store the default API key connection:

```bash
curl -s -X PUT http://localhost:3000/api/connections/github \
  -H 'content-type: application/json' \
  -d '{"authType":"api_key","values":{"apiKey":"github_pat_..."}}'
```

Store a named API key connection:

```bash
curl -s -X PUT http://localhost:3000/api/connections/github \
  -H 'content-type: application/json' \
  -d '{"authType":"api_key","connectionName":"work","values":{"apiKey":"github_pat_..."}}'
```

Execute an action with that default connection:

```bash
curl -s -X POST http://localhost:3000/v1/actions/github.get_current_user \
  -H 'content-type: application/json' \
  -d '{"input":{}}'
```

## Configure An OAuth2 Connection

List OAuth configs and copy the `expectedRedirectUri` for your provider:

```bash
curl -s http://localhost:3000/api/oauth/configs
```

Paste that exact callback URL into your provider OAuth app. With the default port, GitHub uses:

```text
http://localhost:3000/oauth/callback
```

If you expose the runtime through another origin, set `OOMOL_CONNECT_ORIGIN` before starting it.

Store the provider OAuth client:

```bash
curl -s -X PUT http://localhost:3000/api/oauth/configs/github \
  -H 'content-type: application/json' \
  -d '{"clientId":"...","clientSecret":"..."}'
```

Start authorization and open the returned `authorizationUrl`:

```bash
curl -s -X POST http://localhost:3000/api/oauth/authorizations \
  -H 'content-type: application/json' \
  -d '{"service":"github"}'
```

After the browser callback completes, the OAuth credential is stored as the default connection. Add
`"connectionName":"work"` to the authorization request to store the result as a named connection.

## Web Console

For local development, open the Web Console at `http://localhost:5173`. The Vite dev server proxies
API requests to the runtime on `http://localhost:3000`.

For a built console served by the Node runtime, build the `web` workspace and start only the API
server:

```bash
npm run build:web
npm run start
```

## Cloudflare Workers Preview

Create the Cloudflare resources, apply the D1 schema, and start a local Worker preview:

```bash
cp wrangler.example.jsonc wrangler.local.jsonc
npx wrangler d1 create open-connector
npx wrangler r2 bucket create open-connector-transit-files
npx wrangler d1 migrations apply open-connector --local --config wrangler.local.jsonc
npm run dev:cloudflare
```

For remote deploys, put the returned D1 `database_id` in ignored `wrangler.local.jsonc`, run the
migration with `--remote --config wrangler.local.jsonc`, set secrets with
`wrangler secret put --config wrangler.local.jsonc`, then run:

```bash
npm run deploy:cloudflare
```

The Worker runtime exposes catalog metadata, connection/token/OAuth state APIs, R2-backed transit
files, and the same generated provider action executor registry used by the Node runtime.

## Runtime Settings

Local runtime state is stored in `./data/connect.sqlite` by default. Override the directory with:

```bash
OOMOL_CONNECT_DATA_DIR=/path/to/data npm run dev
```

With Docker Compose, the bundled `connector-data` volume is mounted at `/app/data`.

Set `OOMOL_CONNECT_ENCRYPTION_KEY` to encrypt stored credentials:

```bash
OOMOL_CONNECT_ENCRYPTION_KEY="replace-with-a-long-random-secret" npm run dev
```

Set an admin bearer token when the admin API or web console is reachable outside your own shell:

```bash
OOMOL_CONNECT_ADMIN_TOKEN="replace-with-an-admin-token" npm run dev
curl -s http://localhost:3000/api/actions \
  -H "authorization: Bearer replace-with-an-admin-token"
```

Use the admin token for `/api`, `/docs`, and the web console. Create persistent runtime tokens for
`/v1` and `/mcp` from the web console Access tab or `POST /api/runtime-tokens`; only token hashes are
stored in SQLite. `OOMOL_CONNECT_RUNTIME_TOKEN` remains available for bootstrap scripts.

The server binds to `127.0.0.1` by default. Set `HOST=0.0.0.0` only when the runtime must be
reachable from outside the local machine or container.

Constrain executable actions with comma-separated action ids or provider wildcards:

```bash
OOMOL_CONNECT_ALLOWED_ACTIONS="hackernews.*,github.get_current_user" npm run dev
```

When action policy is configured, provider proxy requests are denied unless the provider is
explicitly included in `OOMOL_CONNECT_ALLOWED_PROXIES`.

---
FILE: ./open-connector/docs/runtime-api.md
---
# Runtime API And MCP

OpenConnector exposes provider Actions through MCP, HTTP, OpenAPI, local Action guides, and the Web
Console. This document is the detailed reference that keeps endpoint lists and protocol examples out
of the README.

## Access Surfaces

| Surface          | Endpoint                              | Use it for                                                                               |
| ---------------- | ------------------------------------- | ---------------------------------------------------------------------------------------- |
| MCP              | `POST /mcp`                           | Agent hosts that can call MCP tools.                                                     |
| MCP metadata     | `GET /mcp/tools`                      | Preview the discovery-oriented MCP tool set.                                             |
| HTTP runtime API | `/v1/*`                               | SDK-style clients, scripts, and direct Action execution.                                 |
| OpenAPI          | `GET /openapi.json`                   | API importers, reference generation, and strongly scoped one-Action specs.               |
| Action guide     | `GET /api/actions/:actionId/agent.md` | Agent-readable markdown guide for one Action.                                            |
| Web Console      | `GET /`                               | Browser workflow for browsing providers, configuring credentials, and debugging Actions. |

When `OOMOL_CONNECT_RUNTIME_TOKEN` or persistent runtime tokens are configured, `/v1/*` and `/mcp`
callers should send:

```text
Authorization: Bearer oct_...
```

Admin endpoints under `/api/*`, `/docs`, and the Web Console use `OOMOL_CONNECT_ADMIN_TOKEN` when it
is configured.

## MCP

Point MCP-capable clients at:

```text
http://localhost:3000/mcp
```

The local MCP endpoint supports stateless `POST` JSON-RPC requests with JSON responses. It does not
keep `GET` SSE streams open.

The MCP server exposes a small discovery-oriented tool set:

- `list_apps`
- `search_actions`
- `get_action_guide`
- `execute_action`

Preview MCP tool metadata:

```bash
curl -s http://localhost:3000/mcp/tools
```

## HTTP Runtime API

Runtime clients should use `/v1`. Responses use a uniform JSON envelope:

```json
{
  "success": true,
  "message": "OK",
  "data": {},
  "meta": {}
}
```

Discover Actions:

```bash
curl -s http://localhost:3000/v1/actions
curl -s "http://localhost:3000/v1/actions?service=github"
curl -s http://localhost:3000/v1/actions/github.get_current_user
```

Execute an Action:

```bash
curl -s -X POST http://localhost:3000/v1/actions/github.get_current_user \
  -H 'content-type: application/json' \
  -d '{"input":{}}'
```

Select a named connection with `x-oo-connector-alias`:

```bash
curl -s -X POST http://localhost:3000/v1/actions/github.get_current_user \
  -H 'x-oo-connector-alias: work' \
  -H 'content-type: application/json' \
  -d '{"input":{}}'
```

The `alias` query parameter is also accepted:

```bash
curl -s -X POST "http://localhost:3000/v1/actions/github.get_current_user?alias=work" \
  -H 'content-type: application/json' \
  -d '{"input":{}}'
```

## Action Guides

Each Action has a local markdown guide that includes the input schema, scopes, provider
permissions, current connection identity, and request examples:

```bash
curl -s http://localhost:3000/api/actions/github.get_current_user/agent.md
```

The Web Console also lets you copy cURL, TypeScript, and agent prompt examples for each Action.

## Transit Files

Upload a temporary local transit file for Actions that accept a file URL:

```bash
curl -s -X POST http://localhost:3000/api/files \
  -F "file=@./report.pdf"
```

The response includes a `downloadUrl` under `/api/files/:fileId`. Local transit files are stored
under `OOMOL_CONNECT_DATA_DIR/files` and are cleaned up by age.

## Public Runtime Endpoints

- `GET /v1/health`
- `GET /v1/providers`
- `GET /v1/actions`
- `GET /v1/actions/search`
- `GET /v1/actions?service=<service>`
- `GET /v1/actions/:actionId`
- `POST /v1/actions/:actionId`
- `GET /v1/apps`
- `GET /v1/apps/services/:service`
- `GET /v1/apps/authenticated`
- `POST /v1/proxy/:service`

`POST /v1/proxy/:service` proxies one provider API request when that provider has a registered or
provider-specific local proxy executor. Providers without a proxy executor return `proxy_not_supported`.

Request body:

```json
{
  "endpoint": "/provider/path",
  "method": "GET",
  "query": { "limit": "10" },
  "headers": { "accept": "application/json" },
  "body": { "name": "example" }
}
```

`endpoint` must be a relative path beginning with `/`; absolute URLs are rejected. The runtime keeps
stored credentials local and lets the provider proxy executor apply provider-specific authentication.
Successful responses use the standard `/v1` success envelope with `data.status`, `data.headers`, and
`data.data`.

Proxy requests are controlled by `OOMOL_CONNECT_ALLOWED_PROXIES` and
`OOMOL_CONNECT_BLOCKED_PROXIES`. When action policy is configured, provider proxies are denied by
default unless explicitly allowlisted.

## Local Admin Endpoints

These endpoints power the Web Console, examples, and setup scripts:

- `GET /api/providers`
- `GET /api/providers/:service`
- `GET /api/actions`
- `GET /api/actions/search`
- `GET /api/actions/:actionId`
- `GET /api/actions/:actionId/agent.md`
- `POST /api/files`
- `GET /api/files/:fileId`
- `DELETE /api/files/:fileId`
- `GET /api/connections`
- `PUT /api/connections/:service`
- `DELETE /api/connections/:service`
- `GET /api/oauth/configs`
- `PUT /api/oauth/configs/:service`
- `DELETE /api/oauth/configs/:service`
- `POST /api/oauth/authorizations`
- `GET /oauth/callback`
- `GET /api/runtime-tokens`
- `POST /api/runtime-tokens`
- `DELETE /api/runtime-tokens/:id`
- `GET /api/runs`
- `POST /mcp`
- `GET /mcp/tools`
- `GET /openapi.json`

---
FILE: ./open-connector/docs/sdk-cli.md
---
# SDK And CLI

OpenConnector can be used directly through MCP and HTTP, and it also has dedicated developer tools
for application and agent workflows. The Connector SDK and `oo CLI` both work with the open-source
runtime and with OOMOL-hosted connector services, using the same provider ids, Action ids, and
schemas.

## Connector SDK

[Connector SDK](https://github.com/oomol-lab/connector-sdk) is the TypeScript client for calling
connector Actions from apps and agent runtimes. Use it when you want to execute Actions, proxy
upstream provider APIs, or inspect the runtime catalog from code.

Install:

```bash
npm install @oomol-lab/connector
```

For the self-hosted OpenConnector runtime, use `OpenConnector`. `baseUrl` is the server origin, not
a `/v1` URL:

```ts
import { OpenConnector } from "@oomol-lab/connector";

const open = new OpenConnector({
  baseUrl: "http://localhost:3000",
  runtimeToken: process.env.OOMOL_CONNECT_RUNTIME_TOKEN,
});

const stories = await open.hackernews.get_top_stories({});
console.log(stories);
```

For OOMOL-hosted connector services, use `Connector` for personal connections or `ProjectConnector`
for end-user connections in a SaaS product. All clients use the same Action model; the gateway keeps
provider credentials behind the runtime boundary, authorizes the request, and executes the provider
Action. The SDK is intentionally just a client: it does not run provider integrations locally or
manage OAuth setup.

For an end-to-end Gmail OAuth and SDK example, see
[gmail-oauth-sdk.md](gmail-oauth-sdk.md).

## oo CLI

[oo CLI](https://github.com/oomol-lab/oo-cli) is the command-line toolkit for local AI agent
workflows. Use it when you want an agent on your machine to discover, inspect, and call connector
Actions through a consistent command-line entry.

Point connector-family commands at a self-hosted OpenConnector runtime:

```bash
oo connector login http://localhost:3000
oo connector search "send an email"
oo connector schema gmail.send_email
oo connector run gmail --action send_email --data '@payload.json'
```

For authenticated runtimes, create a runtime token in the Web Console Access page and pass it during
login:

```bash
oo connector login https://connector.example.com --token <runtime-token>
```

For containers and CI, use environment variables instead of writing CLI config:

```bash
export OO_CONNECTOR_URL="https://connector.example.com"
export OO_CONNECTOR_TOKEN="<runtime-token>"
oo connector run github --action get_current_user --data '{}'
```

When no self-hosted runtime is configured, connector commands can still route to OOMOL-hosted
connector services through `OO_API_KEY` or the active `oo` account. A self-hosted connector is a
capability override for connector commands, not an `oo` account; non-connector commands such as
hosted LLM, file transfer, and skill publishing still use OOMOL account authentication.

## Protocol APIs

For custom clients, scripts, or agent hosts that do not use the SDK or CLI, use:

- MCP: `http://localhost:3000/mcp`
- HTTP runtime API: `/v1/actions`
- OpenAPI: `/openapi.json`

See [runtime-api.md](runtime-api.md) for protocol-level details.

---
FILE: ./open-connector/docs/verification.md
---
# Verification

Catalog coverage, local execution, and external API verification are separate states.

When documenting a provider, distinguish:

- Catalog-only actions: schemas and metadata are available for discovery.
- Locally executable actions: the open source runtime has an executor for the action.
- Verified coverage: maintainers have current evidence that the action or provider works against the real upstream API.

Do not imply that every catalog action is end-to-end verified unless that evidence is available in
public project artifacts. Prefer verification notes that users can reproduce from this repository,
such as example scripts, smoke tests, or public status pages.

---
FILE: ./open-connector/examples/local-http/README.md
---
# Local HTTP Example

Start the local runtime:

```bash
npm run dev
```

If the server requires bearer tokens, set `OOMOL_CONNECT_ADMIN_TOKEN` for examples that configure
connections or OAuth clients. Set `OOMOL_CONNECT_RUNTIME_TOKEN` for examples that run `/v1`
actions.

Run a no-auth Hacker News action:

```bash
node examples/local-http/hackernews.ts
```

Run GitHub with a personal access token:

```bash
GITHUB_TOKEN=github_pat_... node examples/local-http/github.ts
```

Run Notion with an internal integration token. Share target pages or databases with the integration
first.

```bash
NOTION_TOKEN=secret_... node examples/local-http/notion.ts
```

Prepare Gmail OAuth with your own Google OAuth app:

```bash
GMAIL_CLIENT_ID=... GMAIL_CLIENT_SECRET=... node examples/local-http/gmail.ts
```

Configure the Google OAuth app redirect URI to:

```text
http://localhost:3000/oauth/callback
```

Open the printed authorization URL in a browser, finish consent, then execute Gmail actions through
the local API.

---
FILE: ./open-connector/examples/mcp-client/README.md
---
# MCP Client Example

Start the local runtime:

```bash
npm run dev
```

If the runtime requires a bearer token, set `OOMOL_CONNECT_RUNTIME_TOKEN` when running this example.

List the MCP-style tools derived from the local action catalog:

```bash
node examples/mcp-client/list-tools.ts
```

---
FILE: ./open-connector/examples/openai-tools/README.md
---
# OpenAI Tools Example

These examples convert the local action catalog into OpenAI function tools.

Start the local runtime:

```bash
npm run dev
```

If the server requires bearer tokens, set `OOMOL_CONNECT_ADMIN_TOKEN` when listing `/api` actions.
Set `OOMOL_CONNECT_RUNTIME_TOKEN` when running action calls through `/v1`.

Print the tool definitions sent to the OpenAI Responses API:

```bash
node examples/openai-tools/list-tools.ts
```

Run a Hacker News tool call loop through the OpenAI Responses API:

```bash
OPENAI_API_KEY=sk-... OPENAI_MODEL=gpt-... node examples/openai-tools/run-hackernews.ts
```

---
FILE: ./open-connector/web/PRODUCT.md
---
# Product

## Register

product

## Users

Developers and operators running OOMOL Connect locally. They use the console to inspect provider capabilities, configure credentials, run actions, and understand execution results without exposing raw provider secrets to agents.

## Product Purpose

OOMOL Connect is a local connector runtime for AI agents and SDK-style clients. The web console should make runtime state, provider actions, credential readiness, request schemas, examples, and run history easy to inspect and trust.

## Brand Personality

Clear, restrained, professional. The interface should feel like a dependable local developer tool: dense enough for repeated work, calm enough for debugging, and explicit about credentials and execution state.

## Anti-references

Avoid marketing-style hero layouts, decorative gradients, oversized cards, playful affordances, and hidden debugging state. Avoid dense controls that visually disconnect a selected detail from the action browser.

## Design Principles

- Put the task first: search, inspect, debug, and copy examples should be immediate.
- Keep execution state visible: local availability, auth requirement, scopes, and provider context should not be buried.
- Favor familiar controls: use standard product UI patterns for filters, tabs, forms, dialogs, and code examples.
- Preserve scanability: lists, panels, and metadata should stay aligned and predictable under large catalogs.
- Keep the local boundary explicit: the console should reinforce that actions run through the localhost runtime.

## Accessibility & Inclusion

Target WCAG AA contrast for text and controls. Preserve keyboard focus states, readable system fonts, reduced-motion behavior for animations, and responsive layouts that remain usable on narrow screens.

