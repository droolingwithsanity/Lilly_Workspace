#!/data/data/com.termux/files/usr/bin/bash
# Lilly Server for Termux — start/stop/status
# Install:  chmod +x lilly-server.sh && cp lilly-server.sh ~/.termux/boot/
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
APP_PY="$SCRIPT_DIR/app.py"
PID_FILE="$SCRIPT_DIR/lilly-server.pid"
LOG_FILE="$SCRIPT_DIR/lilly-server.log"
PORT="${LILLY_PORT:-8098}"

ensure_deps() {
  if ! command -v python3 &>/dev/null; then
    echo "Installing python3..." && pkg install -y python3
  fi
  if ! python3 -c "import fastapi" &>/dev/null; then
    echo "Installing Python packages..." && pip install fastapi uvicorn httpx
  fi
}

start() {
  ensure_deps
  if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
    echo "Already running PID $(cat "$PID_FILE")"; return
  fi
  echo "Starting on port $PORT..."
  nohup python3 "$APP_PY" > "$LOG_FILE" 2>&1 &
  echo $! > "$PID_FILE"
  sleep 2
  if kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
    echo "Started PID $(cat "$PID_FILE")"
    termux-wake-lock lilly-server
  else
    echo "Failed" && tail -3 "$LOG_FILE"
  fi
}

stop() {
  if [ ! -f "$PID_FILE" ]; then echo "Not running"; return; fi
  PID=$(cat "$PID_FILE")
  kill "$PID" 2>/dev/null
  sleep 1
  kill -9 "$PID" 2>/dev/null
  termux-wake-unlock lilly-server
  rm -f "$PID_FILE"
  echo "Stopped"
}

status() {
  if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
    echo "Running PID $(cat "$PID_FILE") on port $PORT"
  else
    echo "Not running"
  fi
}

case "${1:-start}" in
  start) start ;;
  stop) stop ;;
  restart) stop; sleep 1; start ;;
  status) status ;;
  *)
    echo "Usage: $0 {start|stop|restart|status}"
    echo "  start   Launch server (auto-installs deps)"
    echo "  stop    Kill server"
    echo "  restart Stop then start"
    echo "  status  Check if running"
    ;;
esac
