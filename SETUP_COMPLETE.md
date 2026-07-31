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
