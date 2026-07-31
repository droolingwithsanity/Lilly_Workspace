#!/bin/bash
# ═══════════════════════════════════════════════════════════════════
#  Phone-side SSH Setup Script
#  Run this ON YOUR PHONE in Termux to install the public key
# ═══════════════════════════════════════════════════════════════════

set -euo pipefail

# Colors
GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'

echo -e "${CYAN}"
cat << 'BANNER'
  ╔═══════════════════════════════════════════════╗
  ║     Phone SSH Setup for Lilly AI              ║
  ║     Install SSH public key                    ║
  ╚═══════════════════════════════════════════════╝
BANNER
echo -e "${NC}"

# The public key from the host machine
PUB_KEY="ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIK/zINhxHvwuTb8j3EXEGsF9NfJXK4kPhHkqRO98TmWk laurencekidey@gmail.com"

echo -e "${GREEN}Installing SSH public key...${NC}"
echo ""

# Create .ssh directory
mkdir -p ~/.ssh
chmod 700 ~/.ssh
echo -e "${GREEN}✓${NC} Created ~/.ssh directory"

# Add key to authorized_keys (avoid duplicates)
if grep -q "$PUB_KEY" ~/.ssh/authorized_keys 2>/dev/null; then
    echo -e "${YELLOW}!${NC} Key already installed"
else
    echo "$PUB_KEY" >> ~/.ssh/authorized_keys
    echo -e "${GREEN}✓${NC} Added public key to authorized_keys"
fi

chmod 600 ~/.ssh/authorized_keys
echo -e "${GREEN}✓${NC} Set correct permissions (600)"

# Ensure sshd is running
if pgrep -x sshd >/dev/null; then
    echo -e "${GREEN}✓${NC} sshd is already running"
else
    echo -e "${YELLOW}!${NC} Starting sshd..."
    sshd
    echo -e "${GREEN}✓${NC} sshd started"
fi

# Show SSH status
echo ""
echo -e "${CYAN}═══════════════════════════════════════════════════${NC}"
echo -e "${CYAN}  SSH Setup Complete!${NC}"
echo -e "${CYAN}═══════════════════════════════════════════════════${NC}"
echo ""
echo -e "  ${GREEN}SSH is ready for connections${NC}"
echo -e "  Port: $(grep -E '^Port ' /data/data/com.termux/files/usr/etc/ssh/sshd_config 2>/dev/null || echo '8022')"
echo ""
echo -e "  ${YELLOW}To verify from host machine, run:${NC}"
echo -e "    ssh -i ~/Lilly_Workspace/Lilly_Workspace -p 8022 u0_a401@100.115.234.87 'echo connected'"
echo ""
