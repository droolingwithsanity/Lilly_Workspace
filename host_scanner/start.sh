#!/usr/bin/env bash
# Start the Lilly Host Scan Provider (real BT + WiFi scans from host radios).
#
# Usage:
#   bash host_scanner/start.sh            # foreground (Ctrl+C to stop)
#   PORT=8096 bash host_scanner/start.sh  # custom port
#
# For a persistent background service, install the systemd unit instead:
#   sudo cp host_scanner/lilly-host-scan.service /etc/systemd/system/
#   sudo systemctl daemon-reload
#   sudo systemctl enable --now lilly-host-scan

set -euo pipefail

PORT="${PORT:-8096}"
HOST="${HOST:-127.0.0.1}"
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Make sure the host python has the needed deps
if ! /usr/bin/python3 -c "import bleak, fastapi, uvicorn, aiohttp" 2>/dev/null; then
    echo "[host-scan] installing deps..."
    /usr/bin/python3 -m pip install -r "$DIR/requirements.txt"
fi

echo "[host-scan] starting on ${HOST}:${PORT}..."
exec /usr/bin/python3 "$DIR/provider.py" --host "$HOST" --port "$PORT"