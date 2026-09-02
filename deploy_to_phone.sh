#!/usr/bin/env bash
# Deploy Lilly Phone Server + Sensor Server to Termux on the phone.
#
# This script is meant to be run from the host machine (your PC) to push
# the latest phone server files to the phone over SSH, then restart the
# running servers there.
#
# Requirements on host:
#   - sshpass (for password-based SSH) or SSH key auth
#   - rsync or scp
#
# Requirements on phone (Termux):
#   - pkg install python termux-api -y
#   - pip install fastapi uvicorn httpx flask
#
# Usage:
#   bash deploy_to_phone.sh
#
# Environment variables (optional):
#   TERMUX_SSH_HOST     IP/hostname of the phone (default: 100.115.234.87)
#   TERMUX_SSH_PORT     SSH port (default: 8022)
#   TERMUX_SSH_USER     SSH username (default: u0_a123)
#   TERMUX_SSH_PASS     SSH password (if using password auth)
#   SENSOR_SERVER_PORT  Port for sensor server (default: 8099)
#   PHONE_SERVER_PORT   Port for phone server (default: 8097)

set -euo pipefail

# ─── Configuration ───────────────────────────────────────────────────────────

TERMUX_SSH_HOST="${TERMUX_SSH_HOST:-100.115.234.87}"
TERMUX_SSH_PORT="${TERMUX_SSH_PORT:-8022}"
TERMUX_SSH_USER="${TERMUX_SSH_USER:-u0_a123}"
TERMUX_SSH_PASS="${TERMUX_SSH_PASS:-}"
SENSOR_SERVER_PORT="${SENSOR_SERVER_PORT:-8099}"
PHONE_SERVER_PORT="${PHONE_SERVER_PORT:-8097}"
WORKSPACE="${WORKSPACE:-/home/labhrasd/Lilly_Workspace}"

# Colors
GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
log()  { echo -e "${GREEN}[deploy]${NC} $*"; }
warn() { echo -e "${YELLOW}[warn]${NC} $*"; }
err()  { echo -e "${RED}[error]${NC} $*" >&2; }

# ─── SSH Helper ─────────────────────────────────────────────────────────────

SSH_CMD="ssh -o StrictHostKeyChecking=no -p ${TERMUX_SSH_PORT}"

if [[ -n "$TERMUX_SSH_PASS" ]]; then
    if command -v sshpass &>/dev/null; then
        SSH_CMD="sshpass -p ${TERMUX_SSH_PASS} ssh -o StrictHostKeyChecking=no -p ${TERMUX_SSH_PORT}"
    else
        warn "sshpass not found — falling back to key-based auth"
    fi
fi

# ─── Pre-flight checks ──────────────────────────────────────────────────────

log "Checking connectivity to ${TERMUX_SSH_USER}@${TERMUX_SSH_HOST}:${TERMUX_SSH_PORT}..."
if ! $SSH_CMD ${TERMUX_SSH_USER}@${TERMUX_SSH_HOST} "echo connected" &>/dev/null; then
    err "Cannot reach phone via SSH. Check TERMUX_SSH_HOST, TERMUX_SSH_PORT, and SSH keys."
    exit 1
fi
log "Phone is reachable."

# ─── Stop running servers ────────────────────────────────────────────────────

log "Stopping any running servers on phone..."

# Stop sensor server
$SSH_CMD ${TERMUX_SSH_USER}@${TERMUX_SSH_HOST} bash -c "'
    pkill -f \"termux_sensor_server/main.py\" 2>/dev/null || true
    pkill -f \"termux_sensor_server.py\" 2>/dev/null || true
    echo \"Sensor server stopped.\"
'"

# Stop phone server (Flask on 8097)
$SSH_CMD ${TERMUX_SSH_USER}@${TERMUX_SSH_HOST} bash -c "'
    pkill -f \"lilly_phone_server.py\" 2>/dev/null || true
    echo \"Phone server stopped.\"
'"

# Stop any stray Python servers on our ports
$SSH_CMD ${TERMUX_SSH_USER}@${TERMUX_SSH_HOST} bash -c "'
    for port in ${SENSOR_SERVER_PORT} ${PHONE_SERVER_PORT}; do
        pid=$(lsof -ti :\$port 2>/dev/null || true)
        if [[ -n \"\$pid\" ]]; then
            kill \$pid 2>/dev/null || true
            echo \"Killed PID \$pid on port \$port\"
        fi
    done
'"

sleep 2
log "Servers stopped."

# ─── Deploy files ────────────────────────────────────────────────────────────

log "Deploying phone server files..."

# Create remote directories
$SSH_CMD ${TERMUX_SSH_USER}@${TERMUX_SSH_HOST} bash -c "'
    mkdir -p ~/lilly_phone_server/sensors
    mkdir -p ~/lilly_phone_server/data
    echo \"Directories created.\"
'"

# Deploy lilly_phone_server.py
log "Pushing lilly_phone_server.py..."
rsync -az --delete -e "ssh -p ${TERMUX_SSH_PORT} -o StrictHostKeyChecking=no" \
    "${WORKSPACE}/unwrapped_latest_apk/res/raw/lilly_phone_server.py" \
    ${TERMUX_SSH_USER}@${TERMUX_SSH_HOST}:~/lilly_phone_server/lilly_phone_server.py

# Deploy termux_utils.py
log "Pushing termux_utils.py..."
rsync -az --delete -e "ssh -p ${TERMUX_SSH_PORT} -o StrictHostKeyChecking=no" \
    "${WORKSPACE}/unwrapped_latest_apk/res/raw/termux_utils.py" \
    ${TERMUX_SSH_USER}@${TERMUX_SSH_HOST}:~/lilly_phone_server/termux_utils.py

# Deploy bt_profiles.py
log "Pushing bt_profiles.py..."
rsync -az --delete -e "ssh -p ${TERMUX_SSH_PORT} -o StrictHostKeyChecking=no" \
    "${WORKSPACE}/unwrapped_latest_apk/res/raw/bt_profiles.py" \
    ${TERMUX_SSH_USER}@${TERMUX_SSH_HOST}:~/lilly_phone_server/bt_profiles.py

# Deploy class-based sensor server
log "Pushing termux_sensor_server..."
rsync -az --delete -e "ssh -p ${TERMUX_SSH_PORT} -o StrictHostKeyChecking=no" \
    "${WORKSPACE}/termux_sensor_server/" \
    ${TERMUX_SSH_USER}@${TERMUX_SSH_HOST}:~/termux_sensor_server/

# ─── Install Python dependencies ─────────────────────────────────────────────

log "Installing Python dependencies on phone..."
$SSH_CMD ${TERMUX_SSH_USER}@${TERMUX_SSH_HOST} bash -c "'
    pip install -q fastapi uvicorn httpx flask python-multipart openai 2>/dev/null || \
    pip install fastapi uvicorn httpx flask python-multipart openai
    echo \"Dependencies installed.\"
'"

# ─── Start servers ───────────────────────────────────────────────────────────

log "Starting sensor server on port ${SENSOR_SERVER_PORT}..."
$SSH_CMD ${TERMUX_SSH_USER}@${TERMUX_SSH_HOST} bash -c "'
    cd ~/termux_sensor_server
    nohup python main.py --port ${SENSOR_SERVER_PORT} > /dev/null 2>&1 &
    echo \$! > /tmp/sensor_server.pid
    echo \"Sensor server starting (PID: \$!)\"
'"

log "Starting phone server on port ${PHONE_SERVER_PORT}..."
$SSH_CMD ${TERMUX_SSH_USER}@${TERMUX_SSH_HOST} bash -c "'
    cd ~/lilly_phone_server
    nohup python lilly_phone_server.py > /dev/null 2>&1 &
    echo \$! > /tmp/phone_server.pid
    echo \"Phone server starting (PID: \$!)\"
'"

# ─── Verify ──────────────────────────────────────────────────────────────────

sleep 3

log "Verifying servers..."
$SSH_CMD ${TERMUX_SSH_USER}@${TERMUX_SSH_HOST} bash -c "'
    # Check sensor server
    if curl -sf --max-time 3 http://127.0.0.1:${SENSOR_SERVER_PORT}/health >/dev/null 2>&1; then
        echo \"✓ Sensor server is UP on port ${SENSOR_SERVER_PORT}\"
    else
        echo \"✗ Sensor server is NOT responding\"
    fi

    # Check phone server
    if curl -sf --max-time 3 http://127.0.0.1:${PHONE_SERVER_PORT}/api/status >/dev/null 2>&1; then
        echo \"✓ Phone server is UP on port ${PHONE_SERVER_PORT}\"
    else
        echo \"✗ Phone server is NOT responding\"
    fi
'"

log ""
log "Deployment complete!"
log "  Sensor server: http://${TERMUX_SSH_HOST}:${SENSOR_SERVER_PORT}"
log "  Phone server:  http://${TERMUX_SSH_HOST}:${PHONE_SERVER_PORT}"
log "  Overlay UI:    http://${TERMUX_SSH_HOST}:${PHONE_SERVER_PORT}/overlay"
