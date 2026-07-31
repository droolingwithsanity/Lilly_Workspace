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
