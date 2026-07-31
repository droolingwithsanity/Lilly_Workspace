#!/bin/bash
# ═══════════════════════════════════════════════════════════════════
#  Deploy Lilly AI System
#  Builds and starts all Docker services
# ═══════════════════════════════════════════════════════════════════

set -euo pipefail

# Colors
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'

step() { echo -e "\n${CYAN}━━━ $1 ━━━${NC}\n"; }
ok()   { echo -e "${GREEN}  ✓${NC} $*"; }
fail() { echo -e "${RED}  ✗${NC} $*" >&2; }
warn() { echo -e "${YELLOW}  !${NC} $*"; }
info() { echo -e "${BLUE}  →${NC} $*"; }

# Load environment
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ -f "$SCRIPT_DIR/.env" ]]; then
    set -a
    source "$SCRIPT_DIR/.env"
    set +a
fi

echo -e "${CYAN}"
cat << 'BANNER'
  ╔═══════════════════════════════════════════════╗
  ║     Deploy Lilly AI System                   ║
  ║     Docker Compose Deployment                ║
  ╚═══════════════════════════════════════════════╝
BANNER
echo -e "${NC}"

# ─── Step 1: Verify prerequisites ────────────────────────────────
step "Step 1: Verifying prerequisites"

# Check Docker
if command -v docker &>/dev/null; then
    ok "Docker installed: $(docker --version)"
else
    fail "Docker not installed"
    exit 1
fi

# Check Docker Compose
if command -v docker-compose &>/dev/null; then
    ok "Docker Compose installed: $(docker-compose --version)"
elif docker compose version &>/dev/null; then
    ok "Docker Compose (plugin) installed"
else
    fail "Docker Compose not installed"
    exit 1
fi

# Check Docker daemon
if docker info &>/dev/null; then
    ok "Docker daemon running"
else
    fail "Docker daemon not running"
    echo "  Start Docker with: sudo systemctl start docker"
    exit 1
fi

# Check .env file
if [[ -f "$SCRIPT_DIR/.env" ]]; then
    ok "Environment file found"
else
    fail "Environment file not found"
    exit 1
fi

# ─── Step 2: Build Docker images ─────────────────────────────────
step "Step 2: Building Docker images"

info "Building main Lilly AI image..."
if docker-compose build; then
    ok "Docker images built successfully"
else
    fail "Docker build failed"
    exit 1
fi

# ─── Step 3: Stop existing services ──────────────────────────────
step "Step 3: Stopping existing services"

info "Stopping any running containers..."
docker-compose down 2>/dev/null || true
ok "Existing services stopped"

# ─── Step 4: Start services ──────────────────────────────────────
step "Step 4: Starting Lilly AI services"

info "Starting services in detached mode..."
if docker-compose up -d; then
    ok "Services started successfully"
else
    fail "Failed to start services"
    exit 1
fi

# ─── Step 5: Verify services ─────────────────────────────────────
step "Step 5: Verifying services"

info "Waiting for services to initialize..."
sleep 10

# Check if containers are running
info "Checking container status..."
docker-compose ps

# Check Lilly AI server
info "Checking Lilly AI server..."
if curl -sf http://localhost:8098/health >/dev/null 2>&1; then
    ok "Lilly AI server is healthy"
else
    warn "Lilly AI server not responding yet (may still be starting)"
fi

# Check Open Connector
info "Checking Open Connector..."
if curl -sf http://localhost:3002/health >/dev/null 2>&1; then
    ok "Open Connector is healthy"
else
    warn "Open Connector not responding yet (may still be starting)"
fi

# ─── Step 6: Check Termux connection ─────────────────────────────
step "Step 6: Checking Termux connection"

info "Testing SSH connection to phone..."
if ssh -i "$TERMUX_SSH_KEY" -p "$TERMUX_SSH_PORT" -o ConnectTimeout=5 "$TERMUX_SSH_USER@$TERMUX_SSH_HOST" "echo 'SSH OK'" 2>/dev/null; then
    ok "SSH connection to phone successful"
    
    info "Checking phone AI server..."
    if ssh -i "$TERMUX_SSH_KEY" -p "$TERMUX_SSH_PORT" "$TERMUX_SSH_USER@$TERMUX_SSH_HOST" "~/ai-server/ai-server status" 2>/dev/null; then
        ok "Phone AI server is running"
    else
        warn "Phone AI server not running"
        echo "  Start it with: ssh termux '~/ai-server/ai-server start'"
    fi
else
    warn "SSH connection to phone failed"
    echo "  Run: ./setup-termux-ssh.sh"
fi

# ─── Summary ──────────────────────────────────────────────────────
echo ""
echo -e "${CYAN}═══════════════════════════════════════════════════${NC}"
echo -e "${CYAN}  Deployment Complete!${NC}"
echo -e "${CYAN}═══════════════════════════════════════════════════${NC}"
echo ""
echo -e "  ${BOLD}Services:${NC}"
echo -e "    Lilly AI:      ${GREEN}http://localhost:8098${NC}"
echo -e "    Open Connector: ${GREEN}http://localhost:3002${NC}"
echo ""
echo -e "  ${BOLD}Useful commands:${NC}"
echo -e "    View logs:      ${GREEN}docker-compose logs -f${NC}"
echo -e "    Stop services:  ${GREEN}docker-compose down${NC}"
echo -e "    Restart:        ${GREEN}docker-compose restart${NC}"
echo -e "    Status:         ${GREEN}docker-compose ps${NC}"
echo ""
echo -e "  ${BOLD}Phone connection:${NC}"
echo -e "    SSH:            ${GREEN}ssh termux${NC}"
echo -e "    Sensor server:  ${GREEN}$SENSOR_SERVER_URL${NC}"
echo ""
