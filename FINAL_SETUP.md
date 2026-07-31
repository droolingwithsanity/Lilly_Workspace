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
