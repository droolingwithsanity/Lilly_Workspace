#!/bin/bash
# Graceful shutdown with proper signal propagation
# No set -e — handle errors manually so background services survive

SHUTDOWN=false
MAX_RESTARTS=5
RESTART_COUNT=0
RESTART_DELAY=5

cleanup() {
    SHUTDOWN=true
    echo ""
    echo "Shutting down all services..."
    kill -TERM "$AI_PID" "$BRIDGE_PID" "$BLE_PID" "$OC_PID" 2>/dev/null
    sleep 2
    # Force kill any remaining processes
    kill -KILL "$AI_PID" "$BRIDGE_PID" "$BLE_PID" "$OC_PID" 2>/dev/null
    echo "All services stopped."
    exit 0
}

# Trap all termination signals
trap cleanup TERM INT QUIT EXIT

# Reap zombie background processes
cleanup_zombies() {
    wait -n 2>/dev/null || true
}
trap cleanup_zombies CHLD

echo "Starting Open Connector on :3002..."
cd /opt/open-connector
OOMOL_CONNECT_DATA_DIR=/app/data/connect \
OOMOL_CONNECT_ADMIN_TOKEN="${OOMOL_CONNECT_ADMIN_TOKEN:-}" \
OOMOL_CONNECT_RUNTIME_TOKEN="${OOMOL_CONNECT_RUNTIME_TOKEN:-}" \
OOMOL_CONNECT_ENCRYPTION_KEY="${OOMOL_CONNECT_ENCRYPTION_KEY:-}" \
OOMOL_CONNECT_ALLOWED_ACTIONS="${OOMOL_CONNECT_ALLOWED_ACTIONS:-gmail.*}" \
OOMOL_CONNECT_ORIGIN="http://localhost:3002" \
NODE_ENV=production \
PORT=3002 \
HOST=0.0.0.0 \
node src/server/index.ts &

OC_PID=$!
echo "Open Connector PID: $OC_PID"

# Wait for OC to be ready
echo "Waiting for Open Connector..."
for i in $(seq 1 30); do
    if curl -sf http://localhost:3002/health > /dev/null 2>&1; then
        echo "Open Connector is ready."
        break
    fi
    sleep 1
done

echo "Skipping OpenLive Agent :8787 (placeholder, no source in image)."
echo "Skipping OpenLive Web :3000 (placeholder, no build output in image)."

echo "Starting Lilly Bridge on :8788..."
cd /opt/openlive-bridge
BRIDGE_PORT=8788 \
BRIDGE_HOST=0.0.0.0 \
LILLY_AI_URL="http://127.0.0.1:8098" \
LILLY_SENSOR_URL="${SENSOR_SERVER_URL:-http://100.115.234.87:8099}" \
node src/index.js &

BRIDGE_PID=$!
echo "Lilly Bridge PID: $BRIDGE_PID"

# Wait for Lilly Bridge to be ready
echo "Waiting for Lilly Bridge..."
for i in $(seq 1 30); do
    if curl -sf http://localhost:8788/health > /dev/null 2>&1; then
        echo "Lilly Bridge is ready."
        break
    fi
    sleep 1
done

echo ""
echo "==========================================="
echo "  All services started:"
echo "  • Open Connector  : :3002"
echo "  • OpenLive Agent  : :8787 (skipped — placeholder)"
echo "  • OpenLive Web UI : :3000 (skipped — placeholder)"
echo "  • Lilly Bridge    : :8788"
echo "  • BLE Host Advert : :8110"
echo "  • Lilly AI        : :8098"
echo "==========================================="
echo ""

echo "Starting BLE Host Advertiser on :8110..."
cd /app

HCI_DEV="${BLE_HOST_HCI:-hci0}"
if command -v hciconfig >/dev/null 2>&1; then
    hciconfig "$HCI_DEV" up 2>/dev/null || true
    echo "  HCI adapter: $HCI_DEV (hciconfig up)"
else
    echo "  WARNING: hciconfig not found — BLE advertising may not work"
fi

python3 ble_advertiser_host.py --port 8110 --hci "$HCI_DEV" &
BLE_PID=$!
echo "BLE Host Advertiser PID: $BLE_PID"

# ── Lilly AI with crash recovery loop ──────────────────────────
# If lilly_ai.py crashes, restart it up to MAX_RESTARTS times
# with a delay between restarts. This prevents the container
# from entering a crash loop or silently dying.

start_lilly_ai() {
    echo "Starting Lilly AI on :8098..."
    cd /app
    python3 lilly_ai.py &
    AI_PID=$!
    echo "Lilly AI PID: $AI_PID"
}

monitor_lilly_ai() {
    while [ "$SHUTDOWN" = "false" ]; do
        if ! kill -0 "$AI_PID" 2>/dev/null; then
            RESTART_COUNT=$((RESTART_COUNT + 1))
            if [ "$RESTART_COUNT" -ge "$MAX_RESTARTS" ]; then
                echo "ERROR: Lilly AI crashed $MAX_RESTARTS times. Giving up."
                cleanup
            fi
            echo "WARNING: Lilly AI died (restart $RESTART_COUNT/$MAX_RESTARTS). Restarting in ${RESTART_DELAY}s..."
            sleep $RESTART_DELAY
            start_lilly_ai
        fi
        sleep 5
    done
}

start_lilly_ai
monitor_lilly_ai &
MONITOR_PID=$!

# Keep the container alive — wait on the monitor process.
# Signal propagation: Docker sends SIGTERM → cleanup trap → kill all children.
wait $MONITOR_PID
