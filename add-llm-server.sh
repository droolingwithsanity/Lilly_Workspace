#!/bin/bash
# Add LLM Server to OpenClaw Network
# Automates SSH key setup, OpenClaw install, node pairing, and approval

set -euo pipefail

# Colors
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'

step() { echo -e "\n${CYAN}===== $1 =====${NC}\n"; }
ok()   { echo -e "${GREEN}  [OK]${NC} $*"; }
fail() { echo -e "${RED}  [FAIL]${NC} $*" >&2; }
warn() { echo -e "${YELLOW}  [!]${NC} $*"; }
info() { echo -e "${BLUE}  ->${NC} $*"; }

# Config
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SSH_KEY="${SSH_KEY:-$HOME/.ssh/id_ed25519}"
SSH_PUB="${SSH_KEY}.pub"
SSH_USER="${SSH_USER:-lilly}"
GATEWAY_HOST="${GATEWAY_HOST:-127.0.0.1}"
GATEWAY_PORT="${GATEWAY_PORT:-18789}"
GATEWAY_TOKEN="${GATEWAY_TOKEN:-e387e90310c20aa154ab4442dba51f96d37c1489a325da7c}"
OPENCLAW_NODE_NAME="${OPENCLAW_NODE_NAME:-llm-server}"

if [[ $# -lt 1 ]]; then
  echo "Usage: $0 <new-llm-host> [node-name]"
  echo "  <new-llm-host>   SSH hostname/IP of the new LLM server"
  echo "  [node-name]      Optional node name (default: llm-server)"
  echo ""
  echo "Example:"
  echo "  $0 100.73.249.14 lilly-gpu-01"
  echo "  $0 llm-server.tail12345.ts.net"
  exit 1
fi

NEW_HOST="$1"
NEW_NODE_NAME="${2:-${OPENCLAW_NODE_NAME}}"

# Step 1: Verify prerequisites
step "Step 1: Verifying prerequisites"

if [[ ! -f "$SSH_KEY" ]]; then
  fail "SSH private key not found: $SSH_KEY"
  exit 1
fi
ok "SSH key found: $SSH_KEY"

if [[ ! -f "$SSH_PUB" ]]; then
  fail "SSH public key not found: $SSH_PUB"
  exit 1
fi
ok "SSH pub key found: $SSH_PUB"

PUB_KEY_CONTENT=$(cat "$SSH_PUB")

# Check gateway is reachable
info "Checking gateway connectivity..."
if curl -sf "http://${GATEWAY_HOST}:${GATEWAY_PORT}/" >/dev/null 2>&1; then
  ok "Gateway reachable at ${GATEWAY_HOST}:${GATEWAY_PORT}"
else
  fail "Gateway NOT reachable at ${GATEWAY_HOST}:${GATEWAY_PORT}"
  warn "Start gateway first: openclaw gateway start"
  exit 1
fi

# Check remote host reachable
info "Checking remote LLM server connectivity..."
if ping -c 1 -W 3 "$NEW_HOST" >/dev/null 2>&1; then
  ok "Remote host reachable: $NEW_HOST"
else
  fail "Remote host unreachable: $NEW_HOST"
  exit 1
fi

# Check SSH port open on remote
info "Checking SSH on remote host..."
if nc -z -w 5 "$NEW_HOST" 22 2>/dev/null; then
  ok "SSH port 22 open on $NEW_HOST"
else
  fail "SSH port 22 not reachable on $NEW_HOST"
  exit 1
fi

# Step 2: Copy SSH public key to remote LLM server
step "Step 2: Installing SSH public key on remote LLM server"

info "Copying public key to ${SSH_USER}@${NEW_HOST}..."

# Try key-based auth first
if ssh -i "$SSH_KEY" -o ConnectTimeout=5 -o StrictHostKeyChecking=no \
    "$SSH_USER@$NEW_HOST" "echo 'SSH OK'" 2>/dev/null; then
  ok "SSH key auth already works"
else
  warn "SSH key auth not working yet, will set up password auth or manual install"
  info "Run this command ON THE REMOTE SERVER to install the key:"
  echo -e "${GREEN}mkdir -p ~/.ssh && chmod 700 ~/.ssh && echo '${PUB_KEY_CONTENT}' >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys && echo 'Key installed!'${NC}"
  read -rp "Press Enter once key is installed..."
fi

# Verify key auth now works
info "Verifying SSH key authentication..."
if ! ssh -i "$SSH_KEY" -o ConnectTimeout=5 -o StrictHostKeyChecking=no \
    "$SSH_USER@$NEW_HOST" "echo 'SSH connection successful!'" 2>/dev/null; then
  fail "SSH key authentication failed"
  exit 1
fi
ok "SSH key authentication verified"

# Step 3: Install OpenClaw on remote LLM server
step "Step 3: Installing OpenClaw on remote LLM server"

info "Checking if OpenClaw is already installed..."
if ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no "$SSH_USER@$NEW_HOST" \
    "which openclaw" 2>/dev/null; then
  ok "OpenClaw already installed"
  ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no "$SSH_USER@$NEW_HOST" \
    "openclaw --version" 2>/dev/null || true
else
  warn "OpenClaw not found, installing..."
  ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no "$SSH_USER@$NEW_HOST" bash << 'REMOTE_EOF'
    set -e
    echo "Installing OpenClaw..."
    if command -v npm >/dev/null 2>&1; then
      npm install -g openclaw
    elif command -v pnpm >/dev/null 2>&1; then
      pnpm add -g openclaw
    elif command -v yarn >/dev/null 2>&1; then
      yarn global add openclaw
    else
      echo "Installing Node.js + npm..."
      curl -fsSL https://deb.nodesource.com/setup_20.x | bash -
      apt-get install -y nodejs
      npm install -g openclaw
    fi
    echo "OpenClaw installed:"
    openclaw --version
REMOTE_EOF
  ok "OpenClaw installed on remote server"
fi

# Step 4: Configure OpenClaw node on remote LLM server
step "Step 4: Configuring OpenClaw node host"

info "Creating OpenClaw config on remote server..."

ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no "$SSH_USER@$NEW_HOST" bash << REMOTE_EOF
  set -e
  mkdir -p ~/.openclaw
  
  cat > ~/.openclaw/openclaw.json <<EOFCONF
{
  "gateway": {
    "mode": "remote",
    "auth": {
      "mode": "token",
      "token": "${GATEWAY_TOKEN}"
    },
    "url": "ws://${GATEWAY_HOST}:${GATEWAY_PORT}",
    "tailscale": {
      "mode": "off",
      "resetOnExit": false
    }
  },
  "agents": {
    "defaults": {
      "workspace": "~/openclaw-workspace",
      "model": {
        "primary": "ollama/qwen2.5:7b"
      }
    }
  },
  "models": {
    "mode": "merge",
    "providers": {
      "ollama": {
        "baseUrl": "http://127.0.0.1:11434",
        "api": "ollama",
        "apiKey": "OLLAMA_API_KEY",
        "models": [
          {
            "id": "qwen2.5:7b",
            "name": "qwen2.5:7b",
            "reasoning": false,
            "input": ["text"],
            "cost": { "input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0 },
            "contextWindow": 32768,
            "maxTokens": 8192
          }
        ]
      }
    }
  },
  "skills": {
    "install": {
      "nodeManager": "npm"
    }
  }
}
EOFCONF

  chmod 600 ~/.openclaw/openclaw.json
  echo "Config written to ~/.openclaw/openclaw.json"
REMOTE_EOF

ok "OpenClaw node config created"

# Step 5: Install Ollama on remote LLM server (optional)
step "Step 5: Installing Ollama on remote LLM server (optional)"

info "Checking if Ollama is installed..."
if ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no "$SSH_USER@$NEW_HOST" \
    "which ollama" 2>/dev/null; then
  ok "Ollama already installed"
else
  warn "Ollama not found. Install manually with:"
  echo "  curl -fsSL https://ollama.com/install.sh | sh"
  read -rp "Press Enter to continue without Ollama, or Ctrl+C to install it first..."
fi

# Step 6: Start OpenClaw node host on remote LLM server
step "Step 6: Starting OpenClaw node host"

info "Starting node host on remote server..."

ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no "$SSH_USER@$NEW_HOST" bash << 'REMOTE_EOF'
  set -e
  
  # Create workspace
  mkdir -p ~/openclaw-workspace
  
  # Start node host in background
  nohup openclaw node run \
    --host 0.0.0.0 \
    --port 18789 \
    --gateway ws://127.0.0.1:18789 \
    --token e387e90310c20aa154ab4442dba51f96d37c1489a325da7c \
    > /tmp/openclaw-node.log 2>&1 &
  
  echo $! > /tmp/openclaw-node.pid
  echo "Node host started (PID: $!)"
  sleep 2
  
  # Check if running
  if kill -0 $! 2>/dev/null; then
    echo "Node host is running"
  else
    echo "Node host failed to start, check /tmp/openclaw-node.log"
    tail -20 /tmp/openclaw-node.log
  fi
REMOTE_EOF

ok "Node host startup command sent"

# Step 7: Pair the node with the gateway
step "Step 7: Pairing node with OpenClaw Gateway"

info "Waiting for node to connect and generate pairing request..."
sleep 5

info "Checking for pending node pairing requests..."
PAIRING_OUTPUT=$(openclaw nodes pending 2>/dev/null || true)

if echo "$PAIRING_OUTPUT" | grep -qi "pending\|no pending"; then
  echo "$PAIRING_OUTPUT"
  warn "No pending pairing requests found"
  info "The node may need to be manually paired from the gateway side"
  info "Check gateway logs: openclaw gateway logs"
else
  echo "$PAIRING_OUTPUT"
  ok "Pairing requests found"
fi

# Try to approve any pending nodes
info "Attempting to approve pending nodes..."
NODE_IDS=$(echo "$PAIRING_OUTPUT" | grep -oE '[a-f0-9\-]{36}' | head -5 || true)

if [[ -n "$NODE_IDS" ]]; then
  for NODE_ID in $NODE_IDS; do
    info "Approving node: $NODE_ID"
    if openclaw nodes approve "$NODE_ID" 2>/dev/null; then
      ok "Node $NODE_ID approved"
    else
      warn "Failed to approve node $NODE_ID (may need manual approval)"
    fi
  done
else
  warn "No node IDs found to approve automatically"
fi

# Step 8: Verify node status
step "Step 8: Verifying node status"

sleep 3
info "Checking node status..."
openclaw nodes status 2>/dev/null || true

# Step 9: Update Mission Control config
step "Step 9: Updating Mission Control configuration"

info "Adding new node to Mission Control..."

MISSION_CONTROL_DIR="$SCRIPT_DIR/mission-control"
if [[ -d "$MISSION_CONTROL_DIR" ]]; then
  # Update .env with new node info if not already present
  ENV_FILE="$MISSION_CONTROL_DIR/.env"
  if [[ -f "$ENV_FILE" ]]; then
    if ! grep -q "NEW_LLM_NODE_HOST=$NEW_HOST" "$ENV_FILE" 2>/dev/null; then
      echo "" >> "$ENV_FILE"
      echo "# Auto-added by add-llm-server.sh" >> "$ENV_FILE"
      echo "NEW_LLM_NODE_HOST=$NEW_HOST" >> "$ENV_FILE"
      echo "NEW_LLM_NODE_NAME=$NEW_NODE_NAME" >> "$ENV_FILE"
      echo "NEW_LLM_NODE_ADDED_AT=$(date -u +%Y-%m-%dT%H:%M:%SZ)" >> "$ENV_FILE"
      ok "Updated mission-control/.env with new node"
    else
      ok "Node already in mission-control/.env"
    fi
  fi
else
  warn "Mission Control directory not found at $MISSION_CONTROL_DIR"
fi

# Summary
echo ""
echo -e "${CYAN}==========================================${NC}"
echo -e "${CYAN}  LLM Server Added!${NC}"
echo -e "${CYAN}==========================================${NC}"
echo ""
echo -e "  Node:           $NEW_NODE_NAME"
echo -e "  Host:           $NEW_HOST"
echo -e "  SSH User:       $SSH_USER"
echo -e "  Gateway:        ${GATEWAY_HOST}:${GATEWAY_PORT}"
echo -e "  Node Port:      18789"
echo ""
echo -e "  Next steps:"
echo -e "    1. Verify node is paired: ${GREEN}openclaw nodes status${NC}"
echo -e "    2. Restart Mission Control if needed"
echo -e "    3. Check agent catalog sync in Mission Control UI"
echo ""
echo -e "  Useful commands:"
echo -e "    ${GREEN}openclaw nodes status${NC}            - List all nodes"
echo -e "    ${GREEN}openclaw nodes describe <id>${NC}      - Node details"
echo -e "    ${GREEN}openclaw agents list${NC}              - List agents from gateway"
echo -e "    ${GREEN}openclaw gateway status${NC}           - Gateway status"
echo ""
