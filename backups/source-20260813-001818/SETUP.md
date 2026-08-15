# Lilly AI — Setup Guide

## Prerequisites

- Docker & Docker Compose installed
- Git

## Steps

### 1. Clone with submodules

```bash
git clone --recurse-submodules https://github.com/droolingwithsanity/Lilly_Workspace.git
cd Lilly_Workspace
```

### 2. Create your `.env` file

```bash
cp .env.example .env 2>/dev/null || touch .env
```

Edit `.env` with your API keys and settings:
- `OLLAMA_URL` — point to your Ollama instance (or remove if not using)
- `SENSOR_SERVER_URL` — your Android app's built-in webserver address
- `AUTH0_*` credentials if you want auth
- `PREFER_BACKEND` — set to `ollama` or `openai` depending on your LLM setup

### 3. Docker network (if needed)

If the external whisper network doesn't exist, remove it from `docker-compose.yml` or create it:

```bash
docker network create supernova_default
```

### 4. Build and run

```bash
docker compose up -d --build
```

## Ports

| Port | Service |
|------|---------|
| 8098 | Lilly AI (main) |
| 3007 | Open Connector (mapped from 3002) |

## Direct run (no Docker)

```bash
pip install -r requirements.txt
python lilly_ai.py
```

The main entry point is `lilly_ai.py` on port **8098**.

## Restoring to an older version

```bash
# 1. Revert files to target commit
git checkout <commit-hash> -- lilly_ai.py docker-compose.yml start.sh Dockerfile requirements.txt

# 2. Rebuild and restart
docker compose up -d --build

# 3. Verify
curl http://localhost:8098/health
```

## Configuration

### Environment variables (`.env`)

Key environment variables:

```bash
# Android APK built-in webserver
SENSOR_SERVER_URL=http://<phone-ip>:8099

# LLM backend
OLLAMA_URL=http://<ollama-ip>:11434
PREFER_BACKEND=ollama

# Auth0 (optional)
AUTH0_DOMAIN=...
AUTH0_CLIENT_ID=...
AUTH0_CLIENT_SECRET=...
AUTH0_SECRET=...

# Open Connector (optional)
OPENCONNECTOR_ADMIN_TOKEN=...
OPENCONNECTOR_RUNTIME_TOKEN=...
```

### Web UI settings menu

You can also configure settings from the web UI:
- Open the app at `http://localhost:8098`
- Click the **⚙️ Settings** button in the top-right
- Available settings:
  - **Pushbullet API key** — for notification fallback
  - **Sensor server URL** — your Android app's built-in webserver address
  - **Proactive notifications** — enable/disable proactive alerts
  - **Notification sound** — enable/disable notification sounds
  - **Pushbullet fallback** — use Pushbullet if notifications fail
  - **Notifications paused** — temporarily pause all notifications
  - **Daily notification cap** — max proactive notifications per day

## Troubleshooting

### "No sensor data"
Check the Android app's webserver is running and reachable:
```bash
curl http://<phone-ip>:8099/health
```

### "No notifications"
Ensure the Android app has Notification Listener permission granted:
- Enable in Android Settings > Apps > [Lilly App] > Notification Listener

### "Mic not working"
Use the browser's built-in mic (click the mic button in the UI).

### "TTS not speaking"
Check Piper voices exist at `lillyos/voices/*.onnx`. Run `pip install piper-tts` if missing.
