#!/bin/bash
set -euo pipefail

echo "=========================================="
echo "  Lilly AI Startup"
echo "=========================================="

# ── Clean up stale processes on our ports ──────────────────────────────────
for port in 3000 3002 8787 8788 8098; do
    pid=$(lsof -ti :$port 2>/dev/null || true)
    if [ -n "$pid" ]; then
        echo "Killing stale process on :$port (PID: $pid)"
        kill -9 $pid 2>/dev/null || true
        sleep 1
    fi
done

# ── Kill any leftover background jobs from previous runs ────────────────────
jobs -p 2>/dev/null | xargs -r kill -9 2>/dev/null || true
sleep 1

echo ""
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

echo "Waiting for Open Connector..."
for i in $(seq 1 30); do
    if curl -sf http://localhost:3002/health > /dev/null 2>&1; then
        echo "Open Connector is ready."
        break
    fi
    sleep 1
done

# ── OpenLive Agent (voice/vision LLM pipeline) ─────────────────────────────
echo "Starting OpenLive Agent on :8787..."
cd /ol-agent/services/agent
OPENLIVE_AGENT_SECRET="${OPENLIVE_AGENT_SECRET:-lilly-openlive-secret-2026}" \
WEB_PUBLIC_URL="http://localhost:3000" \
OLLAMA_BASE_URL="${OLLAMA_BASE_URL:-http://127.0.0.1:11434}" \
OPENLIVE_DATA_DIR=/ol-agent/data \
AGENT_PORT=8787 \
AGENT_HOST=0.0.0.0 \
tsx src/server.ts &

AGENT_PID=$!
echo "OpenLive Agent PID: $AGENT_PID"

echo "Waiting for OpenLive Agent..."
for i in $(seq 1 30); do
    if curl -sf http://localhost:8787/health > /dev/null 2>&1; then
        echo "OpenLive Agent is ready."
        break
    fi
    sleep 1
done

# ── OpenLive Web (Next.js UI for voice/vision) ─────────────────────────────
echo "Starting OpenLive Web on :3000..."
cd /ol-web/apps/web
WEB_PORT=3000 \
HOSTNAME=0.0.0.0 \
AGENT_SERVICE_URL="http://127.0.0.1:8787" \
OPENLIVE_AGENT_SECRET="${OPENLIVE_AGENT_SECRET:-lilly-openlive-secret-2026}" \
OPENLIVE_DATA_DIR=/ol-web/data \
NODE_ENV=production \
node server.mjs &

WEB_PID=$!
echo "OpenLive Web PID: $WEB_PID"

echo "Waiting for OpenLive Web..."
for i in $(seq 1 30); do
    if curl -sf http://localhost:3000 > /dev/null 2>&1; then
        echo "OpenLive Web is ready."
        break
    fi
    sleep 1
done

# ── Lilly Bridge (connects OpenLive to Lilly AI) ───────────────────────────
echo "Starting Lilly Bridge on :8788..."
cd /opt/openlive-bridge
BRIDGE_PORT=8788 \
BRIDGE_HOST=0.0.0.0 \
LILLY_AI_URL="http://127.0.0.1:8098" \
LILLY_SENSOR_URL="${SENSOR_SERVER_URL:-http://100.115.234.87:8099}" \
node_modules/.bin/tsx src/server.ts &

BRIDGE_PID=$!
echo "Lilly Bridge PID: $BRIDGE_PID"

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
echo "  • OpenLive Agent  : :8787"
echo "  • OpenLive Web UI : :3000"
echo "  • Lilly Bridge    : :8788"
echo "  • Lilly AI        : :8098"
echo "==========================================="
echo ""

# ── Start Lilly AI ─────────────────────────────────────────────────────────
echo "Starting Lilly AI on :8098..."
cd /app
python3 lilly_ai.py &
AI_PID=$!

# Cleanup on exit
cleanup() {
    echo "Shutting down services..."
    kill $AI_PID $AGENT_PID $WEB_PID $BRIDGE_PID $OC_PID 2>/dev/null || true
    # Final cleanup of any stragglers
    for port in 3000 3002 8787 8788 8098; do
        pid=$(lsof -ti :$port 2>/dev/null || true)
        if [ -n "$pid" ]; then
            kill -9 $pid 2>/dev/null || true
        fi
    done
    exit 0
}

trap cleanup TERM INT EXIT

wait $AI_PID
