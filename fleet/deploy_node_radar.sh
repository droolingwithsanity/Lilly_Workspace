#!/usr/bin/env bash
#
# deploy_node_radar.sh — push the enhanced node_radar_server.py to a phone
# and start it on :8080.
#
# Usage:
#   ./deploy_node_radar.sh <PHONE_IP> <NODE_NAME> <NODE_ID> [PORT]
#   ./deploy_node_radar.sh 100.115.234.88 "Kitchen Phone" phone-2 8080
#
# Requires ssh + scp access (Termux SSHD on the phone) OR the sensor server
# /shell endpoint. Defaults to scp + ssh over port 8022.
#
set -euo pipefail

IP="${1:?phone IP (e.g. 100.115.234.88)}"
NAME="${2:?node name (e.g. Kitchen Phone)}"
NODE_ID="${3:?node id (e.g. phone-2)}"
PORT="${4:-8080}"
SSH_PORT="${TERMUX_SSH_PORT:-8022}"
SSH_USER="${TERMUX_SSH_USER:-u0_a401}"
SSH_KEY="${TERMUX_SSH_KEY:-}"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SRC="$SCRIPT_DIR/node_radar_server.py"
REMOTE="termux/"

SSH_ARGS=( -p "$SSH_PORT" -o StrictHostKeyChecking=no )
[ -n "$SSH_KEY" ] && SSH_ARGS+=( -i "$SSH_KEY" )

echo "==> Copying node_radar_server.py to $IP:$REMOTE"
scp "${SSH_ARGS[@]}" "$SRC" "$SSH_USER@$IP:$REMOTE"

echo "==> Starting node radar on $IP:$PORT (name='$NAME', id=$NODE_ID)"
ssh "${SSH_ARGS[@]}" "$SSH_USER@$IP" "
  cd termux || exit 1
  pkill -f node_radar_server.py 2>/dev/null || true
  nohup python3 node_radar_server.py --port $PORT --name '$NAME' --node-id $NODE_ID > node_radar.log 2>&1 &
  sleep 2
  echo '--- health ---'
  curl -s http://127.0.0.1:$PORT/health || echo '(server not up yet — check node_radar.log)'
"

echo "==> Done. If SSH isn't set up, run this manually on the phone:"
echo "    python3 termux/node_radar_server.py --port $PORT --name '$NAME' --node-id $NODE_ID"
echo "==> Then register on host (if not auto):"
echo "    curl -X POST http://localhost:8098/api/nodes/register -H 'Content-Type: application/json' -d '{\"node_id\":\"$NODE_ID\",\"name\":\"$NAME\",\"sensor_url\":\"http://$IP:$PORT\"}'"
