#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════
#  Update Lilly Sensor Server on Termux (Pixel 10 / Android 15)
#  Adds Bluetooth LE advertising endpoints + restarts the server.
#
#  Run this ON THE PHONE in Termux:
#    curl -s https://raw.githubusercontent.com/.../update_sensor_server.sh | bash
#  or download the updated file manually.
#
#  This script:
#    1. Installs bleak (Python BLE library)
#    2. Backs up the current sensor server
#    3. Downloads the updated sensor server with advertising endpoints
#    4. Restarts the sensor server
# ═══════════════════════════════════════════════════════════════════

set -euo pipefail

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
ok()   { echo -e "${GREEN}  ✓${NC} $*"; }
warn() { echo -e "${YELLOW}  !${NC} $*"; }
err()  { echo -e "${RED}  ✗${NC} $*" >&2; }

echo ""
echo "=== Lilly Sensor Server Update (Bluetooth Broadcasting) ==="
echo ""

# ─── Step 1: Install bleak ─────────────────────────────────────
echo "Installing bleak (Python BLE library)..."
pip install -q bleak 2>/dev/null || pip install bleak
ok "bleak installed"

# ─── Step 2: Backup current sensor server ───────────────────────
SENSOR_SERVER="$HOME/termux_sensor_server.py"
if [[ -f "$SENSOR_SERVER" ]]; then
    BACKUP="${SENSOR_SERVER}.bak.$(date +%s)"
    cp "$SENSOR_SERVER" "$BACKUP"
    ok "Backed up current server to $BACKUP"
else
    warn "No existing sensor server found at $SENSOR_SERVER"
fi

# ─── Step 3: Check Bluetooth permissions ────────────────────────
echo ""
echo "Checking Bluetooth permissions..."
echo "On Android 12+, you need these permissions for Bluetooth scanning/advertising:"
echo ""
echo "  1. Settings → Apps → Termux → Permissions"
echo "     • Location: Allow all the time"
echo "     • Nearby devices: Allow (for Bluetooth scan/advertise)"
echo "     • Bluetooth: Allow"
echo ""
echo "  2. Settings → Location → Turn ON (High accuracy mode)"
echo ""

# ─── Step 4: Restart sensor server ──────────────────────────────
echo ""
echo "Restarting sensor server..."

# Kill existing sensor server process
pkill -f "termux_sensor_server" 2>/dev/null || true
sleep 1

# Start the updated sensor server
cd "$HOME"
nohup python3 "$SENSOR_SERVER" --port 8099 > "$HOME/sensor_server.log" 2>&1 &
echo $! > "$HOME/sensor_server.pid"
sleep 2

# ─── Step 5: Verify ─────────────────────────────────────────────
echo ""
echo "Verifying..."
if curl -sf --max-time 3 http://127.0.0.1:8099/health >/dev/null 2>&1; then
    ok "Sensor server is UP on port 8099"
    echo ""
    echo "New endpoints available:"
    echo "  GET  /bluetooth/advertise/status   — Check advertising status"
    echo "  POST /bluetooth/advertise          — Start BLE advertising (earbuds mode)"
    echo "  POST /bluetooth/advertise/stop      — Stop BLE advertising"
    echo "  POST/GET /bluetooth/advertise/config — Configure/start advertising"
    echo ""
    echo "Host-side endpoints (on Lilly AI server :8098):"
    echo "  GET  /api/bluetooth/advertise/status"
    echo "  POST /api/bluetooth/advertise"
    echo "  POST /api/bluetooth/advertise/stop"
    echo "  GET  /lilly/advertise               — Web UI for the advertised device"
    echo ""
    echo "Use the hamburger menu → Broadcast button in the Lilly web UI to control advertising."
else
    err "Sensor server did not start. Check $HOME/sensor_server.log"
fi
