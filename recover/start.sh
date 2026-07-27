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

echo "Starting Lilly AI on :8098..."
cd /app
exec python3 lilly_ai.py
