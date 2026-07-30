#!/usr/bin/env bash

# llama.cpp Server Manager with auto-restart and health checks
# Usage: ./llama-server-manager.sh [start|stop|status|logs|test]

set -euo pipefail

# ─── Configuration ───────────────────────────────────────────────
INSTALL_DIR="${LLAMA_INSTALL_DIR:-$HOME/ai-server}"
LLAMA_DIR="$INSTALL_DIR/llama.cpp"
SERVER_BIN="$LLAMA_DIR/build/bin/llama-server"
MODEL_DIR="$INSTALL_DIR/models"
LOG_DIR="$INSTALL_DIR/logs"
PID_FILE="$INSTALL_DIR/.server.pid"

# Default settings (override with env vars)
HOST="${LLAMA_HOST:-127.0.0.1}"
PORT="${LLAMA_PORT:-8080}"
MODEL="${LLAMA_MODEL:-$MODEL_DIR/Ternary-Bonsai-27B-Q2_K.gguf}"
THREADS="${LLAMA_THREADS:-$(nproc)}"
CTX_SIZE="${LLAMA_CTX:-4096}"
N_GPU_LAYERS="${LLAMA_GPU_LAYERS:-0}"
PARALLEL="${LLAMA_PARALLEL:-4}"
MAX_RETRIES="${LLAMA_MAX_RETRIES:-5}"
RETRY_DELAY="${LLAMA_RETRY_DELAY:-5}"
HEALTH_TIMEOUT="${LLAMA_HEALTH_TIMEOUT:-30}"

# ─── Colors ──────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'

# ─── Functions ───────────────────────────────────────────────────

log_info()  { echo -e "${GREEN}[INFO]${NC}  $(date '+%Y-%m-%d %H:%M:%S') $*"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC}  $(date '+%Y-%m-%d %H:%M:%S') $*"; }
log_error() { echo -e "${RED}[ERROR]${NC} $(date '+%Y-%m-%d %H:%M:%S') $*" >&2; }
log_debug() { echo -e "${BLUE}[DEBUG]${NC} $(date '+%Y-%m-%d %H:%M:%S') $*"; }

check_prereqs() {
    if [[ ! -x "$SERVER_BIN" ]]; then
        log_error "llama-server binary not found at $SERVER_BIN"
        log_error "Run: cd $LLAMA_DIR && cmake --build build --config Release"
        exit 1
    fi

    if [[ ! -f "$MODEL" ]]; then
        log_error "Model not found at $MODEL"
        log_error "Available models:"
        ls -lh "$MODEL_DIR"/*.gguf 2>/dev/null || echo "  (none in $MODEL_DIR)"
        ls -lh "$LLAMA_DIR"/*.gguf 2>/dev/null || echo "  (none in $LLAMA_DIR)"
        exit 1
    fi

    mkdir -p "$LOG_DIR"
}

is_running() {
    if [[ -f "$PID_FILE" ]]; then
        local pid
        pid=$(cat "$PID_FILE")
        if kill -0 "$pid" 2>/dev/null; then
            return 0
        fi
        rm -f "$PID_FILE"
    fi
    return 1
}

get_pid() {
    if [[ -f "$PID_FILE" ]]; then
        cat "$PID_FILE"
    fi
}

health_check() {
    local url="http://${HOST}:${PORT}/health"
    local timeout=$HEALTH_TIMEOUT
    local elapsed=0

    log_info "Waiting for server to become healthy..."
    while [[ $elapsed -lt $timeout ]]; do
        if curl -sf --max-time 2 "$url" >/dev/null 2>&1; then
            log_info "Server is healthy (responded in ${elapsed}s)"
            return 0
        fi
        sleep 1
        ((elapsed++))
        echo -n "."
    done
    echo ""
    log_error "Server failed health check after ${timeout}s"
    return 1
}

build_server_args() {
    local args=(
        --host "$HOST"
        --port "$PORT"
        --model "$MODEL"
        --threads "$THREADS"
        --ctx-size "$CTX_SIZE"
        --parallel "$PARALLEL"
        --log-disable
    )

    if [[ "$N_GPU_LAYERS" -gt 0 ]]; then
        args+=(--n-gpu-layers "$N_GPU_LAYERS")
    fi

    echo "${args[@]}"
}

start_server() {
    if is_running; then
        log_warn "Server already running (PID: $(get_pid))"
        return 0
    fi

    check_prereqs

    local args
    args=$(build_server_args)

    log_info "Starting llama-server..."
    log_info "  Model:   $(basename "$MODEL")"
    log_info "  Threads: $THREADS"
    log_info "  Context: $CTX_SIZE"
    log_info "  Listen:  $HOST:$PORT"
    echo ""

    nohup "$SERVER_BIN" $args \
        >> "$LOG_DIR/server.log" 2>&1 &
    local pid=$!
    echo "$pid" > "$PID_FILE"

    log_info "Server started (PID: $pid)"

    if health_check; then
        echo ""
        log_info "Server is ready at http://${HOST}:${PORT}"
        log_info "API endpoint: http://${HOST}:${PORT}/v1/chat/completions"
        log_info "Logs: tail -f $LOG_DIR/server.log"
        return 0
    else
        log_error "Server failed to start. Checking logs..."
        tail -20 "$LOG_DIR/server.log" 2>/dev/null
        stop_server
        return 1
    fi
}

start_with_restart() {
    local attempt=1

    while [[ $attempt -le $MAX_RETRIES ]]; do
        log_info "=== Attempt $attempt/$MAX_RETRIES ==="

        if start_server; then
            # Monitor loop - restart on crash
            log_info "Monitoring server... (Ctrl+C to stop)"
            while is_running; do
                sleep 10
                if ! curl -sf --max-time 3 "http://${HOST}:${PORT}/health" >/dev/null 2>&1; then
                    log_warn "Server health check failed, restarting..."
                    stop_server 2>/dev/null
                    sleep "$RETRY_DELAY"
                    break
                fi
            done

            if ! is_running; then
                log_warn "Server crashed, restarting in ${RETRY_DELAY}s..."
                sleep "$RETRY_DELAY"
                ((attempt++))
                continue
            fi
        else
            log_warn "Start failed, retrying in ${RETRY_DELAY}s..."
            sleep "$RETRY_DELAY"
            ((attempt++))
        fi
    done

    log_error "Server failed after $MAX_RETRIES attempts"
    return 1
}

stop_server() {
    if is_running; then
        local pid
        pid=$(get_pid)
        log_info "Stopping server (PID: $pid)..."
        kill "$pid" 2>/dev/null

        # Wait for graceful shutdown
        local waited=0
        while kill -0 "$pid" 2>/dev/null && [[ $waited -lt 10 ]]; do
            sleep 1
            ((waited++))
        done

        # Force kill if still running
        if kill -0 "$pid" 2>/dev/null; then
            log_warn "Force killing server..."
            kill -9 "$pid" 2>/dev/null
        fi

        rm -f "$PID_FILE"
        log_info "Server stopped"
    else
        log_info "Server not running"
    fi
}

show_status() {
    echo -e "${CYAN}═══════════════════════════════════════════════════${NC}"
    echo -e "${CYAN}  llama.cpp Server Status${NC}"
    echo -e "${CYAN}═══════════════════════════════════════════════════${NC}"
    echo ""

    if is_running; then
        local pid
        pid=$(get_pid)
        echo -e "  Status:    ${GREEN}Running${NC} (PID: $pid)"

        # Memory usage
        if [[ -f "/proc/$pid/status" ]]; then
            local rss
            rss=$(awk '/VmRSS/{print $2}' "/proc/$pid/status" 2>/dev/null)
            echo -e "  Memory:    $((rss / 1024)) MB"
        fi

        # Health check
        if curl -sf --max-time 3 "http://${HOST}:${PORT}/health" >/dev/null 2>&1; then
            echo -e "  Health:    ${GREEN}OK${NC}"
        else
            echo -e "  Health:    ${RED}FAIL${NC}"
        fi
    else
        echo -e "  Status:    ${RED}Stopped${NC}"
    fi

    echo ""
    echo "  Model:     $(basename "$MODEL")"
    echo "  Endpoint:  http://${HOST}:${PORT}"
    echo "  Logs:      $LOG_DIR/server.log"
    echo ""
}

show_logs() {
    if [[ -f "$LOG_DIR/server.log" ]]; then
        tail -${1:-50} "$LOG_DIR/server.log"
    else
        log_info "No logs found"
    fi
}

test_api() {
    local url="http://${HOST}:${PORT}/v1/chat/completions"

    log_info "Testing API at $url"
    echo ""

    local response
    response=$(curl -sf --max-time 30 "$url" \
        -H "Content-Type: application/json" \
        -d '{
            "model": "local",
            "messages": [
                {"role": "user", "content": "Say hello in exactly 5 words."}
            ],
            "temperature": 0.7,
            "max_tokens": 50
        }' 2>&1)

    if [[ $? -eq 0 ]]; then
        log_info "API Response:"
        echo "$response" | python3 -m json.tool 2>/dev/null || echo "$response"
        return 0
    else
        log_error "API test failed"
        echo "$response"
        return 1
    fi
}

select_model() {
    echo -e "${CYAN}Available models:${NC}"
    echo ""

    local models=()
    local i=1

    # Find all .gguf files
    while IFS= read -r f; do
        models+=("$f")
        local size
        size=$(du -h "$f" | cut -f1)
        printf "  ${GREEN}%2d${NC}) %-45s ${YELLOW}%s${NC}\n" "$i" "$(basename "$f")" "$size"
        ((i++))
    done < <(find "$MODEL_DIR" "$LLAMA_DIR" -maxdepth 3 -name "*.gguf" -not -path "*/ggml-vocab*" 2>/dev/null | sort)

    echo ""
    read -rp "Select model (1-${#models[@]}): " choice

    if [[ "$choice" -ge 1 && "$choice" -le "${#models[@]}" ]]; then
        export LLAMA_MODEL="${models[$((choice-1))]}"
        log_info "Selected: $(basename "$LLAMA_MODEL")"
    else
        log_error "Invalid selection"
        exit 1
    fi
}

show_help() {
    cat <<EOF
${CYAN}llama.cpp Server Manager${NC}

${GREEN}Usage:${NC}
    $0 <command> [options]

${GREEN}Commands:${NC}
    start           Start server (single run)
    start-bg        Start server with auto-restart on crash
    stop            Stop running server
    restart         Restart server
    status          Show server status
    logs [N]        Show last N lines of logs (default: 50)
    test            Test API with a simple request
    model           Interactively select a model
    setup           First-time setup wizard

${GREEN}Environment Variables:${NC}
    LLAMA_HOST          Bind address      (default: 127.0.0.1)
    LLAMA_PORT          Port              (default: 8080)
    LLAMA_MODEL         Path to .gguf     (default: Ternary-Bonsai-27B)
    LLAMA_THREADS       CPU threads       (default: $(nproc))
    LLAMA_CTX           Context size      (default: 4096)
    LLAMA_GPU_LAYERS    GPU layers (0=CPU) (default: 0)
    LLAMA_PARALLEL      Parallel requests  (default: 4)
    LLAMA_MAX_RETRIES   Max restart tries  (default: 5)

${GREEN}Examples:${NC}
    $0 start                        # Start with defaults
    $0 start-bg                     # Start with auto-restart
    LLAMA_PORT=9090 $0 start        # Custom port
    LLAMA_CTX=8192 $0 start         # Larger context

EOF
}

setup_wizard() {
    echo -e "${CYAN}═══════════════════════════════════════════════════${NC}"
    echo -e "${CYAN}  llama.cpp Server Setup${NC}"
    echo -e "${CYAN}═══════════════════════════════════════════════════${NC}"
    echo ""

    # Check binary
    if [[ ! -x "$SERVER_BIN" ]]; then
        log_warn "llama-server not built. Building..."
        cd "$LLAMA_DIR"
        cmake -B build -DLLAMA_CPU=ON -DCMAKE_BUILD_TYPE=Release
        cmake --build build --config Release -j$(nproc)
        cd -
    fi

    # Select model
    select_model

    # Create config file
    local config="$INSTALL_DIR/.server.env"
    cat > "$config" <<EOF
# llama.cpp Server Configuration
# Edit these values as needed

LLAMA_HOST=127.0.0.1
LLAMA_PORT=8080
LLAMA_MODEL=$LLAMA_MODEL
LLAMA_THREADS=$(nproc)
LLAMA_CTX=4096
LLAMA_GPU_LAYERS=0
LLAMA_PARALLEL=4
EOF

    log_info "Config saved to $config"
    log_info "Run: source $config && $0 start"

    echo ""
    log_info "To expose to LAN, set LLAMA_HOST=0.0.0.0"
}

# ─── Main ────────────────────────────────────────────────────────

# Source config if exists
[[ -f "$INSTALL_DIR/.server.env" ]] && source "$INSTALL_DIR/.server.env"

case "${1:-help}" in
    start)
        start_server
        ;;
    start-bg)
        start_with_restart
        ;;
    stop)
        stop_server
        ;;
    restart)
        stop_server
        sleep 2
        start_server
        ;;
    status)
        show_status
        ;;
    logs)
        show_logs "${2:-50}"
        ;;
    test)
        test_api
        ;;
    model)
        select_model
        ;;
    setup)
        setup_wizard
        ;;
    help|--help|-h)
        show_help
        ;;
    *)
        log_error "Unknown command: $1"
        show_help
        exit 1
        ;;
esac
