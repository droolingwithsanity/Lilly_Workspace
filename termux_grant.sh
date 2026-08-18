#!/data/data/com.termux/files/usr/bin/bash
#
# Lilly AI v6.0 — One-Command Overlay Grant + Setup
#
# This script automates everything possible and opens the remaining
# permission screens for one-tap user approval.
#
# Usage:
#   chmod +x termux_grant.sh
#   ./termux_grant.sh
#

set -euo pipefail

echo "╔══════════════════════════════════════════════════════════════╗"
echo "║          Lilly AI v6.0 — Overlay Grant + Setup             ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""

# ─── Color helpers ────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

info()  { echo -e "${CYAN}[INFO]${NC} $1"; }
ok()    { echo -e "${GREEN}[OK]${NC}    $1"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $1"; }
fail()  { echo -e "${RED}[FAIL]${NC}  $1"; }

# ─── Step 1: Check Termux environment ────────────────────────────────
info "Checking Termux environment..."

if ! command -v pkg &>/dev/null; then
    fail "pkg not found — are you running inside Termux?"
    exit 1
fi
ok "Termux environment verified"

# ─── Step 2: Install Termux:API ──────────────────────────────────────
info "Installing Termux:API (sensor + notification access)..."
pkg install termux-api -y
ok "Termux:API installed"

# ─── Step 3: Grant overlay permission ────────────────────────────────
info "Opening Overlay Permission screen..."
# Launch Android Settings to grant SYSTEM_ALERT_WINDOW
am start -a android.settings.action.ManageOverlayPermission \
    -p com.android.settings 2>/dev/null || \
am start -a android.settings.action.ManageOverlayPermission 2>/dev/null || true
ok "Overlay permission screen opened — tap 'Lilly AI' and enable"

# ─── Step 4: Grant Notification Listener ─────────────────────────────
info "Opening Notification Listener screen..."
am start -a android.settings.action.notification_listener_settings 2>/dev/null || true
ok "Notification listener screen opened — enable 'Lilly AI'"

# ─── Step 5: Grant Microphone permission ─────────────────────────────
info "Opening Microphone permission screen..."
pm grant ai.agent1c.hitomi android.permission.RECORD_AUDIO 2>/dev/null || \
    am start -a android.settings.action.ApplicationDetailsSettings \
        -d package:ai.agent1c.hitomi 2>/dev/null || true
ok "Microphone permission screen opened"

# ─── Step 6: Grant Camera permission ────────────────────────────────
info "Opening Camera permission screen..."
pm grant ai.agent1c.hitomi android.permission.CAMERA 2>/dev/null || \
    am start -a android.settings.action.ApplicationDetailsSettings \
        -d package:ai.agent1c.hitomi 2>/dev/null || true
ok "Camera permission screen opened"

# ─── Step 7: Install Python dependencies ─────────────────────────────
info "Installing Python dependencies..."
pip install fastapi uvicorn httpx -q
ok "Python dependencies installed"

# ─── Step 8: Create sensor server directory ─────────────────────────
info "Creating ~/ai-server directory..."
mkdir -p ~/ai-server
ok "Directory ready"

# ─── Step 9: Copy sensor server (if bundled) ─────────────────────────
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
if [ -f "$SCRIPT_DIR/termux_sensor_server.py" ]; then
    info "Copying sensor server to ~/ai-server/..."
    cp "$SCRIPT_DIR/termux_sensor_server.py" ~/ai-server/
    ok "Sensor server copied"
else
    warn "termux_sensor_server.py not found in script directory — skip copy"
fi

# ─── Step 10: Set up pairing token ──────────────────────────────────
info "Setting up device pairing token..."
if [ ! -f ~/.lilly_pair_token ]; then
    cat /dev/urandom | tr -dc 'A-F0-9' | head -c8 > ~/.lilly_pair_token
    ok "New pairing token generated: $(cat ~/.lilly_pair_token)"
else
    ok "Existing pairing token preserved: $(cat ~/.lilly_pair_token)"
fi

# ─── Step 11: Start sensor server ────────────────────────────────────
info "Starting Lilly Sensor Server v6.0 on port 8099..."
cd ~/ai-server || cd ~

if [ -f termux_sensor_server.py ]; then
    nohup python3 termux_sensor_server.py --port 8099 > ~/ai-server/logs/sensor.log 2>&1 &
    echo $! > ~/ai-server/sensor.pid
    sleep 2
    if curl -s -m 3 http://127.0.0.1:8099/health >/dev/null 2>&1; then
        ok "Sensor server running on port 8099"
    else
        warn "Sensor server may still be starting..."
    fi
else
    warn "Sensor server script not found — start it manually with:"
    warn "  python3 termux_sensor_server.py --port 8099"
fi

# ─── Step 12: Display pairing instructions ──────────────────────────
echo ""
echo "╔══════════════════════════════════════════════════════════════╗"
echo "║                  Setup Complete! v6.0                       ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""
echo -e "${GREEN}Next steps:${NC}"
echo ""
echo "  1. Open the Lilly overlay app on your phone"
echo "  2. Tap the 🔗 Pair button on the overlay"
echo "  3. Open your web dashboard (https://droolingwithsanity.ca)"
echo "  4. Enter the 6-digit code shown in the overlay"
echo ""
echo -e "${CYAN}Sensor server:${NC}"
echo "  Health:  http://127.0.0.1:8099/health"
echo "  Sensors: http://127.0.0.1:8099/sensors/all"
echo ""
echo -e "${CYAN}Logs:${NC}"
echo "  tail -f ~/ai-server/logs/sensor.log"
echo ""
echo -e "${YELLOW}Note:${NC} Grant all permissions in the opened settings screens,"
echo "      then restart the overlay app to apply them."
echo ""
