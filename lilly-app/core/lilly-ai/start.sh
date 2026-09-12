#!/bin/bash
set -e

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

# ── OpenLive Agent (voice/vision LLM pipeline) ──────────────────────────
# SKIPPED 2026-09-11: placeholder — image ships empty /ol-agent/services/agent/src
# (Dockerfile ol-agent-build only mkdirs dirs, no source). Re-enable when the
# real openlive repo is vendored: restore the tsx src/server.ts block below.
# Connects to local Ollama at 127.0.0.1:11434 (host network → host Ollama).
echo "Skipping OpenLive Agent :8787 (placeholder, no source in image)."

# ── OpenLive Web (Next.js UI for voice/vision) ───────────────────────────
# SKIPPED 2026-09-11: placeholder — image ships only public/scripts + an empty
# .next/build-manifest.json (Dockerfile ol-web-build), no server.mjs/standalone
# output. Re-enable when the real openlive repo is vendored and built.
echo "Skipping OpenLive Web :3000 (placeholder, no build output in image)."

# ── Lilly Bridge (connects OpenLive to Lilly AI) ──────────────────────────
# Placeholder plain-JS service (openlive/services/bridge/src/index.js responds
# 200 on any path, incl. /health). NOTE: image start command was wrong
# (tsx src/server.ts — neither exists); run the actual file with node.
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

# ── BLE Host Advertiser ─────────────────────────────────────────────
# Runs ble_advertiser_host.py which uses raw HCI commands (hcitool/hciconfig)
# to broadcast BLE + Classic BR/EDR advertisements — the container-side
# equivalent of Bluetooth LE Spam, using YOUR own payloads (name, service UUID,
# image URL, Fast Pair model ID). No phone needed for broadcast.
#
# Requires the Docker container to have access to the host Bluetooth adapter:
#   - In docker-compose.yml: devices: ["/dev/hci0:/dev/hci0"]
#   - Or run the container with --privileged (less secure)
# If no BT adapter is available the server still starts (scan/advertise will
# simply return errors); lilly_ai.py's _bt_proxy silently falls through.
echo "Starting BLE Host Advertiser on :8110..."
cd /app

# Ensure Bluetooth adapter is up and ready before handing off to the advertiser.
# In Docker this requires: devices: ["/dev/hci0:/dev/hci0"] (already in compose).
HCI_DEV="${BLE_HOST_HCI:-hci0}"
if command -v hciconfig >/dev/null 2>&1; then
    hciconfig "$HCI_DEV" up 2>/dev/null || true
    echo "  HCI adapter: $HCI_DEV (hciconfig up)"
else
    echo "  WARNING: hciconfig not found — BLE advertising may not work"
    echo "  (Install: apt-get install -y bluez)"
fi

python3 ble_advertiser_host.py --port 8110 --hci "$HCI_DEV" &
BLE_PID=$!
echo "BLE Host Advertiser PID: $BLE_PID"

# Start Lilly AI (must NOT use exec so it runs in foreground
# while the background services (Bridge, OC) continue running).
echo "Starting Lilly AI on :8098..."
cd /app
python3 lilly_ai.py &
AI_PID=$!

# Keep the container alive — wait on the Lilly AI process (main job).
# Signal propagation: Docker sends SIGTERM → our trap → SIGTERM each child.
trap 'kill $AI_PID $BRIDGE_PID $BLE_PID 2>/dev/null; exit 0' TERM INT
wait $AI_PID
