# Lilly AI Project - Agent Instructions

## Project Overview

Lilly AI is a personal AI companion system that runs on Android phones (via Termux) with a Docker-based backend. The system includes:

- **FastAPI Backend**: Main Lilly AI server on port 8098
- **YOLOv8 Vision**: Object detection (80+ classes) running inside the Docker container
- **Open Connector**: Node.js email/calendar API on port 3002
- **WebSocket Broker**: Phone pairing, automation engine, real-time relay
- **Android Overlay**: Floating UI windows with camera overlay + detection boxes
- **ML Pipeline**: LoRA fine-tuned persona models
- **9 Avatar Agents**: Each with distinct personality, voice, and strengths

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                    Lilly AI System                               │
├──────────────────────────┬──────────────────────────────────────┤
│  Host Machine (Docker)   │  Android Phone (Termux)              │
│                          │                                      │
│  lilly_ai.py (:8098)     │  LillySensorServer (:8099)           │
│   ├── FastAPI + YOLOv8   │   ├── 23 sensor endpoints            │
│   ├── Camera vision API  │   ├── Notification listener           │
│   ├── WebSocket broker   │   ├── Shell command execution         │
│   ├── 9 avatar personas  │   └── Camera frame capture            │
│   ├── Piper TTS          │                                      │
│   └── Automation engine  │  LillyPupOverlayService              │
│                          │   ├── Floating avatar UI              │
│  open-connector (:3002)  │   ├── Camera overlay + detection boxes│
│   └── Gmail/Calendar API │   ├── Radial menu (8 buttons)         │
│                          │   └── Browser WebSocket client         │
│  Phone Broker (WS)       │                                      │
│   └── Pairing + relay    │  Tailscale Network (:8022 SSH)       │
└──────────────────────────┴──────────────────────────────────────┘
```

## Camera & Vision Pipeline

```
Phone Camera / Browser Webcam
        │
        ▼
  POST /api/vision/browser  (JPEG frame)
        │
        ▼
  detect_objects() in lilly_ai.py
        │
        ├── If VISION_SERVER_URL set → proxy to external YOLO server
        │
        └── Else → local YOLOv8 (ultralytics) + OpenCV
                │
                ▼
        Response: { "ok": true, "detections": [...], "description": "..." }
                │
                ├── Android: parseDetectionsFromResponse() → draw boxes on overlay
                └── Browser: CameraBridge stores description → UI displays
```

## Key Files

| File | Purpose |
|------|---------|
| `lilly_ai.py` | Main server (~22,700 lines) — FastAPI, vision, avatars, memory |
| `phone_broker.py` | WebSocket relay + AutomationEngine (conditions, actions, persistence) |
| `wiki.html` | Documentation page served at `/wiki.html` |
| `lilly_skills.json` | 35+ Android skill definitions |
| `Dockerfile` | Multi-stage build (Node.js + Python, includes ultralytics + opencv) |
| `requirements.txt` | Python deps including ultralytics, opencv-python-headless |
| `hitomi-android/` | Android overlay app (Java, Gradle) |
| `hitomi-android/.../LillyPupOverlayService.java` | Overlay service, camera, detection boxes |
| `hitomi-android/.../DetectionBoxOverlay.java` | Custom View for YOLO bounding boxes |
| `hitomi-android/.../LillySensorServer.java` | Embedded sensor HTTP server |

## Development Guidelines

### Code Style
- **Python**: PEP 8, type hints, docstrings
- **JavaScript/TypeScript**: ESLint, Prettier
- **Shell**: ShellCheck, executable scripts
- **Java**: Google Java Style Guide

### Testing
- Run `pytest` for Python tests
- Run `npm test` for Node.js tests
- Run `docker compose up` to verify full stack

### Git Workflow
- Main branch: `master`
- Feature branches: `feature/*`
- Bug fixes: `fix/*`
- Always run tests before committing

## Common Tasks

### Deploy Lilly AI
```bash
docker compose build
docker compose up -d
docker compose logs -f
```

### Verify Vision is Working
```bash
# Check YOLO status
curl http://localhost:8098/api/vision/status
# Expected: {"enabled":false,"opencv":true,"yolo":true,...}

# Test with sample image
curl -X POST http://localhost:8098/api/vision/browser \
  -F "file=@test_image.jpg"
```

### Connect to Termux (optional — sensor server works without SSH)
```bash
ssh -i $TERMUX_SSH_KEY -p $TERMUX_SSH_PORT $TERMUX_SSH_USER@$TERMUX_SSH_HOST
```

### Check Phone Status
```bash
curl http://100.115.234.87:8099/health
```

### Update Persona Model
```bash
cd trainer
python train.py --persona <name>
cp trained_persona_model/<name> ../lillyos/models/
```

## Network Configuration

- **Host Tailscale IP**: `100.93.131.114`
- **Phone Tailscale IP**: `100.115.234.87`
- **Phone Sensor Server**: `http://100.115.234.87:8099` (or `127.0.0.1:8099` on phone)
- **Lilly AI Server**: `http://100.93.131.114:8098` (or `localhost:8098` on host)
- **SSH**: port `8022`, user `u0_a401` (optional — only needed for mic streaming)

## Security Notes

- Never commit `.env` files or API keys
- Use Tailscale for secure networking
- CORS is set to `allow_origins=["*"]` for WebView + browser clients
- Rotate SSH keys periodically
- Monitor access logs

## Troubleshooting

### Common Issues
1. **Connection refused**: Check Tailscale and Docker daemon
2. **Docker build fails**: Verify Docker daemon is running
3. **YOLO not available**: Check `ultralytics` in container — `docker exec lilly-ai python -c "from ultralytics import YOLO; print('OK')"`
4. **Camera frames not reaching server**: Android app must use host IP (`100.93.131.114:8098`), not `127.0.0.1`
5. **No sensor data**: Verify sensor server is running — `curl http://100.115.234.87:8099/health`
6. **Model not loading**: Check GGUF file path and permissions

### Logs Location
- Host: `docker compose logs lilly`
- Phone sensor server: `http://100.115.234.87:8099/health`
- Vision status: `curl http://localhost:8098/api/vision/status`
