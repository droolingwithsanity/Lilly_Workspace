#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════
#  Termux AI Server - Main Menu / Launcher
#  Interactive app-like interface
# ═══════════════════════════════════════════════════════════════════

set -euo pipefail

INSTALL_DIR="${LLAMA_INSTALL_DIR:-$HOME/ai-server}"
LLAMA_DIR="$INSTALL_DIR/llama.cpp"
SERVER_BIN="$LLAMA_DIR/build/bin/llama-server"
MANAGER="$INSTALL_DIR/scripts/llama-server-manager.sh"
LOG_DIR="$INSTALL_DIR/logs"
PID_FILE="$INSTALL_DIR/.server.pid"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; CYAN='\033[0;36m'; BOLD='\033[1m'; DIM='\033[2m'; NC='\033[0m'

clear 2>/dev/null || true

is_running() {
    [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null
}

get_status() {
    if is_running; then
        echo -e "${GREEN}● Running${NC} (PID: $(cat "$PID_FILE"))"
    else
        echo -e "${RED}● Stopped${NC}"
    fi
}

show_banner() {
    echo -e "${CYAN}"
    cat << 'EOF'
  ╔═══════════════════════════════════════════╗
  ║       Termux AI Server                    ║
  ║       llama.cpp on Android                ║
  ╚═══════════════════════════════════════════╝
EOF
    echo -e "${NC}"
    echo -e "  Status: $(get_status)"
    echo ""
}

show_menu() {
    echo -e "${BOLD}  [1]${NC} Start Server"
    echo -e "${BOLD}  [2]${NC} Stop Server"
    echo -e "${BOLD}  [3]${NC} Restart Server"
    echo -e "${BOLD}  [4]${NC} Test API"
    echo -e "${BOLD}  [5]${NC} View Logs"
    echo -e "${BOLD}  [6]${NC} Download Model"
    echo -e "${BOLD}  [7]${NC} Server Settings"
    echo -e "${BOLD}  [8]${NC} Health Check"
    echo -e "${BOLD}  [9]${NC} Open Web UI"
    echo ""
    echo -e "${DIM}  [q] Quit${NC}"
    echo ""
}

do_start() {
    echo ""
    if is_running; then
        echo -e "${YELLOW}  Server already running${NC}"
        return
    fi
    echo -e "  Starting server..."
    export LLAMA_HOST="${LLAMA_HOST:-127.0.0.1}"
    export LLAMA_PORT="${LLAMA_PORT:-8080}"
    bash "$MANAGER" start
}

do_stop() {
    echo ""
    bash "$MANAGER" stop
}

do_restart() {
    echo ""
    bash "$MANAGER" restart
}

do_test() {
    echo ""
    local host="${LLAMA_HOST:-127.0.0.1}"
    local port="${LLAMA_PORT:-8080}"
    echo -e "  Testing API at http://$host:$port ..."
    echo ""

    if ! curl -sf --max-time 3 "http://$host:$port/health" >/dev/null 2>&1; then
        echo -e "${RED}  Server not responding. Start it first.${NC}"
        return
    fi

    echo -e "  Sending test request..."
    local response
    response=$(curl -sf --max-time 30 "http://$host:$port/v1/chat/completions" \
        -H "Content-Type: application/json" \
        -d '{
            "model": "local",
            "messages": [
                {"role": "system", "content": "You are a helpful assistant."},
                {"role": "user", "content": "Say hello in exactly 3 words."}
            ],
            "temperature": 0.7,
            "max_tokens": 30
        }' 2>&1)

    if [[ $? -eq 0 ]]; then
        echo -e "${GREEN}  ✓ API Response:${NC}"
        echo "$response" | python3 -m json.tool 2>/dev/null || echo "$response"
    else
        echo -e "${RED}  ✗ API test failed${NC}"
        echo "$response"
    fi
}

do_logs() {
    echo ""
    if [[ -f "$LOG_DIR/server.log" ]]; then
        echo -e "${DIM}  Last 30 lines of server.log:${NC}"
        echo ""
        tail -30 "$LOG_DIR/server.log"
    else
        echo -e "${YELLOW}  No logs found${NC}"
    fi
    echo ""
}

do_download_model() {
    echo ""
    echo -e "${BOLD}  Available models:${NC}"
    echo ""
    echo -e "  ${GREEN}[1]${NC} Ternary-Bonsai-27B-Q2_K (~10GB)"
    echo -e "  ${GREEN}[2]${NC} Qwen2.5-0.5B-Instruct (~300MB)"
    echo -e "  ${GREEN}[3]${NC} Custom URL"
    echo ""
    read -rp "  Choice [1]: " choice
    choice=${choice:-1}

    mkdir -p "$INSTALL_DIR/models"

    case $choice in
        1)
            echo ""
            echo -e "  Downloading Ternary-Bonsai-27B-Q2_K..."
            wget -c --progress=bar:force:noscroll \
                -O "$INSTALL_DIR/models/Ternary-Bonsai-27B-Q2_K.gguf" \
                "https://huggingface.co/unsloth/Ternary-Bonsai-27B-GGUF/resolve/main/Ternary-Bonsai-27B-Q2_K.gguf" \
                2>&1 || echo -e "${RED}  Download failed${NC}"
            ;;
        2)
            echo ""
            echo -e "  Downloading Qwen2.5-0.5B..."
            wget -c --progress=bar:force:noscroll \
                -O "$INSTALL_DIR/models/Qwen2.5-0.5B-Instruct-Q4_K_M.gguf" \
                "https://huggingface.co/Qwen/Qwen2.5-0.5B-Instruct-GGUF/resolve/main/qwen2.5-0.5b-instruct-q4_k_m.gguf" \
                2>&1 || echo -e "${RED}  Download failed${NC}"
            ;;
        3)
            read -rp "  Enter URL: " url
            read -rp "  Filename: " fname
            wget -c --progress=bar:force:noscroll \
                -O "$INSTALL_DIR/models/$fname" "$url" 2>&1 || echo -e "${RED}  Download failed${NC}"
            ;;
    esac
}

do_settings() {
    echo ""
    echo -e "${BOLD}  Current Settings:${NC}"
    echo ""
    echo -e "  Host:         ${GREEN}${LLAMA_HOST:-127.0.0.1}${NC}"
    echo -e "  Port:         ${GREEN}${LLAMA_PORT:-8080}${NC}"
    echo -e "  Threads:      ${GREEN}${LLAMA_THREADS:-$(nproc)}${NC}"
    echo -e "  Context:      ${GREEN}${LLAMA_CTX:-4096}${NC}"
    echo -e "  Parallel:     ${GREEN}${LLAMA_PARALLEL:-2}${NC}"
    echo ""
    echo -e "  ${DIM}To change, set env vars before starting:${NC}"
    echo -e "  ${DIM}  LLAMA_PORT=9090 ai-server start${NC}"
    echo ""
}

do_health() {
    echo ""
    local host="${LLAMA_HOST:-127.0.0.1}"
    local port="${LLAMA_PORT:-8080}"

    if is_running; then
        echo -e "${GREEN}  ✓ Process running${NC}"
    else
        echo -e "${RED}  ✗ Process not running${NC}"
        return
    fi

    if curl -sf --max-time 3 "http://$host:$port/health" >/dev/null 2>&1; then
        echo -e "${GREEN}  ✓ HTTP health OK${NC}"
    else
        echo -e "${RED}  ✗ HTTP health failed${NC}"
        return
    fi

    # Memory
    local pid
    pid=$(cat "$PID_FILE")
    if [[ -f "/proc/$pid/status" ]]; then
        local rss
        rss=$(awk '/VmRSS/{print $2}' "/proc/$pid/status" 2>/dev/null)
        echo -e "  Memory:       ${rss:-?} KB"
    fi

    # Model info
    local model_info
    model_info=$(curl -sf "http://$host:$port/v1/models" 2>/dev/null || echo "{}")
    echo -e "  Models:       $(echo "$model_info" | python3 -c "import sys,json; d=json.load(sys.stdin); print(len(d.get('data',[])))" 2>/dev/null || echo "?")"
    echo ""
}

do_open_web() {
    local host="${LLAMA_HOST:-127.0.0.1}"
    local port="${LLAMA_PORT:-8080}"
    echo ""
    if command -v termux-open-url &>/dev/null; then
        termux-open-url "http://$host:$port"
        echo -e "${GREEN}  Opened browser${NC}"
    elif command -v xdg-open &>/dev/null; then
        xdg-open "http://$host:$port"
    else
        echo -e "  Open in browser: ${GREEN}http://$host:$port${NC}"
    fi
    echo ""
}

# ─── Main Loop ───────────────────────────────────────────────────

while true; do
    show_banner
    show_menu
    read -rp "  Select: " choice
    case "$choice" in
        1) do_start ;;
        2) do_stop ;;
        3) do_restart ;;
        4) do_test ;;
        5) do_logs ;;
        6) do_download_model ;;
        7) do_settings ;;
        8) do_health ;;
        9) do_open_web ;;
        q|Q|quit|exit)
            echo -e "\n  ${DIM}Goodbye!${NC}\n"
            exit 0
            ;;
        *)
            echo -e "${YELLOW}  Invalid choice${NC}"
            ;;
    esac

    echo ""
    read -rp "  Press Enter to continue..." _
done
