#!/usr/bin/env bash
# =============================================================================
# training_manager.sh — Lilly AI Training Manager
# =============================================================================
# Unified control for both training pipelines:
#
#   1. PERSONA  — LLM LoRA fine-tuning (trainer/train_all_avatars.py,
#                 trainer/train_all_background.py, trainer/run.py)
#   2. YOLO     — vision self-training (yolo_trainer_api:8199 + yolo_self_trainer
#                 + photo_training_bridge for phone-photo ingestion)
#
# Usage:
#   ./training_manager.sh <command> [type] [options]
#
# Commands:
#   start     Start a training run (background by default)
#   stop      Stop a running training run
#   status    Show live status of both pipelines
#   refresh   Stop current run, rebuild data, restart fresh
#   create    Register + launch a new training job with custom parameters
#   logs      Tail training logs (last N lines)
#   list      Show trained models / checkpoints / past jobs
#   review    Run an orchestrator review (thought + verdict) on latest artifacts
#   deploy    Deploy a trained model (yolo -> active model, persona -> models/)
#   json      Machine-readable output for the web dashboard:
#               json status | json list | json logs <persona|yolo|all> <lines>
#               json reviews <persona|yolo|all> <limit>
#   help      This help text
#
# Types: persona | yolo | all   (default: all)
#
# Examples:
#   ./training_manager.sh start persona --quick
#   ./training_manager.sh start yolo --epochs 100
#   ./training_manager.sh start yolo --import-photos 30     # pull phone photos first
#   ./training_manager.sh start persona --avatar fox --output-dir trained_avatars/fox_v2
#   ./training_manager.sh create persona --quick --name run_quick2
#   ./training_manager.sh refresh yolo --import-photos 20 --epochs 80
#   ./training_manager.sh stop persona
#   ./training_manager.sh status
#   ./training_manager.sh logs yolo --follow
#   ./training_manager.sh review persona --avatar puppy      # orchestrator review
#   ./training_manager.sh review yolo
#   ./training_manager.sh deploy yolo
# =============================================================================

set -u

WORKSPACE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$WORKSPACE" || exit 1

# ── Paths & constants ────────────────────────────────────────────────────────
LOG_DIR="$WORKSPACE/trainer/logs"
STATE_DIR="$WORKSPACE/.training"
PID_DIR="$STATE_DIR/pids"
JOBS_FILE="$STATE_DIR/jobs.json"
REVIEWS_DIR="$STATE_DIR/reviews"
PERSONA_OUT="${PERSONA_OUT:-trained_avatars}"
YOLO_MODELS_DIR="$WORKSPACE/trained_models"
YOLO_DATA_DIR="$WORKSPACE/training_data"
YOLO_API="http://localhost:8199"

mkdir -p "$LOG_DIR" "$PID_DIR"

# ── Colors ───────────────────────────────────────────────────────────────────
if [[ -t 1 ]]; then
  C_GREEN=$'\033[0;32m'; C_YELLOW=$'\033[1;33m'; C_RED=$'\033[0;31m'
  C_CYAN=$'\033[0;36m'; C_BOLD=$'\033[1m'; C_RESET=$'\033[0m'
else
  C_GREEN=""; C_YELLOW=""; C_RED=""; C_CYAN=""; C_BOLD=""; C_RESET=""
fi
ok()   { printf "%s✓ %s%s\n"   "$C_GREEN" "$*" "$C_RESET"; }
warn() { printf "%s! %s%s\n"   "$C_YELLOW" "$*" "$C_RESET"; }
err()  { printf "%s✗ %s%s\n"   "$C_RED" "$*" "$C_RESET" >&2; }
info() { printf "%s• %s%s\n"   "$C_CYAN" "$*" "$C_RESET"; }

# ── Help ─────────────────────────────────────────────────────────────────────
usage() {
  sed -n '2,40p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
  echo
  echo "Options:"
  echo "  --avatar NAME        persona: train a single avatar (default: all)"
  echo "  --quick              persona: fewer training steps"
  echo "  --gpu                persona: use GPU + Unsloth + GGUF export"
  echo "  --epochs N           yolo / persona: max steps to train            [yolo: 50]"
  echo "  --batch-size N       training batch size                           [8]"
  echo "  --lr FLOAT           learning rate                                 [0.001 yolo / 0.0002 persona]"
  echo "  --output-dir DIR     where trained models land"
  echo "  --name NAME          job name (recorded in $JOBS_FILE)"
  echo "  --import-photos N    yolo: import N phone photos before training   [default 0]"
  echo "  --background         run in background (default for start/refresh)"
  echo "  --foreground         run in foreground (logs to terminal)"
  echo "  --force              refresh: wipe previous model outputs first"
  echo "  --reset-progress     refresh yolo: forget which photos were imported"
  echo "  --follow             logs: follow (tail -f)"
  echo "  --lines N            logs: number of lines to show                 [50]"
  echo "  --model-path PATH    deploy: explicit model path"
  echo "  --dry-run            print commands without executing"
}

# ── State helpers ────────────────────────────────────────────────────────────
_save_pid() { echo "$2" > "$PID_DIR/$1.pid"; }
_get_pid()  { [[ -f "$PID_DIR/$1.pid" ]] && cat "$PID_DIR/$1.pid" || echo ""; }

_alive() { # pid
  local pid="$1"
  [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null
}

record_job() { # type name status cmd
  local type="$1" name="$2" status="$3" cmd="$4" ts
  ts="$(date -Iseconds)"
  if [[ ! -s "$JOBS_FILE" ]]; then echo '{"jobs": []}' > "$JOBS_FILE"; fi
  python3 - "$type" "$name" "$status" "$cmd" "$ts" "$JOBS_FILE" <<'PY'
import json, sys
typ, name, status, cmd, ts, path = sys.argv[1:7]
db = json.load(open(path))
db.setdefault("jobs", []).append({
    "type": typ, "name": name, "status": status,
    "command": cmd, "started_at": ts,
})
json.dump(db, open(path, "w"), indent=2)
PY
}

# ── Persona helpers ──────────────────────────────────────────────────────────
persona_is_running() { _alive "$(_get_pid persona)"; }

persona_log() { echo "$LOG_DIR/persona_$(date +%Y%m%d_%H%M%S).log"; }

# ── YOLO helpers ─────────────────────────────────────────────────────────────
yolo_api_up()   { curl -sf --max-time 3 "$YOLO_API/api/trainer/status" >/dev/null 2>&1; }
yolo_status() {
  curl -sf --max-time 3 "$YOLO_API/api/trainer/status" 2>/dev/null \
    || echo '{"status":"unknown","error":"API down"}'
}

# Find the most recent trained YOLO model
yolo_best_model() {
  find "$YOLO_MODELS_DIR" -name best.pt -printf '%T@ %p\n' 2>/dev/null \
    | sort -rn | head -1 | cut -d' ' -f2-
}

# ── COMMAND: status ──────────────────────────────────────────────────────────
cmd_status() {
  echo
  echo "${C_BOLD}══ Lilly Training Status ══${C_RESET}"
  echo

  # Persona
  if persona_is_running; then
    ok "PERSONA trainer: RUNNING (pid $(_get_pid persona))"
  else
    info "PERSONA trainer: idle"
  fi
  [[ -n "$(_get_pid persona)" ]] && \
    warn "  last persona pid: $(_get_pid persona)"

  # YOLO API
  if yolo_api_up; then
    local ys
    ys="$(yolo_status)"
    local st
    st="$(echo "$ys" | python3 -c 'import sys,json; print(json.load(sys.stdin).get("status","?"))' 2>/dev/null)"
    if [[ "$st" == "training" ]]; then
      ok "YOLO trainer API (:8199): RUNNING  [status=$st]"
    else
      info "YOLO trainer API (:8199): up  [status=$st]"
    fi
  else
    err "YOLO trainer API (:8199): DOWN"
  fi

  echo
  # Sample counts / data
  local samples=0
  if [[ -d "$YOLO_DATA_DIR/samples" ]]; then
    samples="$(find "$YOLO_DATA_DIR/samples" -name '*.jpg' 2>/dev/null | wc -l)"
  fi
  info "YOLO training samples: $samples"
  local pmodels=0
  if [[ -d "$PERSONA_OUT" ]]; then
    pmodels="$(find "$PERSONA_OUT" -maxdepth 2 -name final -type d 2>/dev/null | wc -l)"
  fi
  info "Persona models trained: $pmodels (in $PERSONA_OUT/)"

  local bm
  bm="$(yolo_best_model)"
  if [[ -n "$bm" ]]; then
    info "Latest YOLO model: $bm"
  else
    info "No YOLO model trained yet"
  fi

  echo
  # Recent jobs
  if [[ -s "$JOBS_FILE" ]]; then
    echo "${C_BOLD}Recent jobs:${C_RESET}"
    python3 - "$JOBS_FILE" <<'PY'
import json, sys
db = json.load(open(sys.argv[1]))
for j in db.get("jobs", [])[-5:]:
    print(f"  [{j['type']:>6}] {j['name']:<20} {j['status']:<10} {j['started_at']}")
PY
  fi
  echo
}

# ── COMMAND: start ───────────────────────────────────────────────────────────
cmd_start() {
  local type="$1" bg=1
  shift
  parse_start_opts "$@"
  case "$type" in
    persona) start_persona "$bg";;
    yolo)    start_yolo "$bg" 0;;
    all)     start_persona "$bg"; start_yolo "$bg" 0;;
  esac
}

parse_start_opts() {
  PERSONA_ARGS=(); YOLO_ARGS=()
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --avatar)      PERSONA_ARGS+=(--avatar); PERSONA_ARGS+=("${2:?--avatar needs a value}"); shift 2;;
      --quick)       PERSONA_ARGS+=(--quick); shift;;
      --gpu)         PERSONA_ARGS+=(--gpu); shift;;
      --gguf-quant)  PERSONA_ARGS+=(--gguf-quant); PERSONA_ARGS+=("${2}"); shift 2;;
      --name)        JOB_NAME="${2:?--name needs a value}"; shift 2;;
      --epochs)      YOLO_ARGS+=(--epochs); YOLO_ARGS+=("${2:?--epochs needs a value}"); PERSONA_ARGS+=(--max-steps); PERSONA_ARGS+=("${2}"); shift 2;;
      --batch-size)  YOLO_ARGS+=(--batch-size); YOLO_ARGS+=("${2}"); shift 2;;
      --lr)          YOLO_ARGS+=(--lr); YOLO_ARGS+=("${2}"); PERSONA_ARGS+=(--learning-rate); PERSONA_ARGS+=("${2}"); shift 2;;
      --output-dir)  PERSONA_ARGS+=(--output-dir); PERSONA_ARGS+=("${2:?--output-dir needs a value}"); shift 2;;
      --import-photos) IMPORT_PHOTOS="${2:?--import-photos needs a value}"; shift 2;;
      --background)  bg=1; shift;;
      --foreground)  bg=0; shift;;
      --dry-run)     DRY_RUN=1; shift;;
      -h|--help)     usage; exit 0;;
      *)             warn "ignoring unknown option: $1"; shift;;
    esac
  done
  JOB_NAME="${JOB_NAME:-${type}_$(date +%Y%m%d_%H%M%S)}"
  IMPORT_PHOTOS="${IMPORT_PHOTOS:-0}"
}

# ── Persona start ────────────────────────────────────────────────────────────
start_persona() { # bg
  local bg="$1"
  if persona_is_running; then
    warn "Persona training already running (pid $(_get_pid persona)). Use: $0 stop persona"
    return 1
  fi

  local logfile cmd
  logfile="$(persona_log)"
  cmd=(python3 trainer/train_all_avatars.py "${PERSONA_ARGS[@]}")

  if [[ "$DRY_RUN" == 1 ]]; then
    echo "  [dry-run] ${cmd[*]}"
    echo "  [dry-run] log -> $logfile"
    record_job persona "$JOB_NAME" dry-run "${cmd[*]}"
    return 0
  fi

  if [[ "$bg" == 1 ]]; then
    nohup "${cmd[@]}" >"$logfile" 2>&1 &
    _save_pid persona "$!"
    record_job persona "$JOB_NAME" started "${cmd[*]}"
    ok "Persona training started: pid $(_get_pid persona)"
    info "Log: $logfile"
    info "Tail with: $0 logs persona --follow"
  else
    record_job persona "$JOB_NAME" started "${cmd[*]}"
    info "Persona training in foreground (Ctrl-C to stop)..."
    "${cmd[@]}" 2>&1 | tee "$logfile"
    pid="$$"; _save_pid persona "$pid"
  fi
}

# ── YOLO start ───────────────────────────────────────────────────────────────
start_yolo() { # bg import_photos
  local bg="$1" import_photos="$2"

  if ! yolo_api_up; then
    err "YOLO trainer API (:8199) is down. Start it first:"
    echo "    python3 -m uvicorn yolo_trainer_api:app --host 0.0.0.0 --port 8199 &"
    return 1
  fi

  local st
  st="$(yolo_status | python3 -c 'import sys,json; print(json.load(sys.stdin).get("status","?"))' 2>/dev/null)"
  if [[ "$st" == "training" ]]; then
    warn "YOLO training already in progress. Use: $0 stop yolo"
    return 1
  fi

  # Optional: import fresh photo samples from the phone first
  if [[ "$import_photos" -gt 0 ]]; then
    info "Importing up to $import_photos photos from phone gallery..."
    curl -sf -X POST "$YOLO_API/api/trainer/import-photos" \
      -H 'Content-Type: application/json' \
      -d "{\"max_photos\": $import_photos}" >/dev/null \
      && ok "Photo import done" || warn "Photo import failed (phone offline?)"
  fi

  # Build request from parsed opts
  local epochs="${YOLO_EPOCHS:-50}" batch="${YOLO_BATCH:-8}" lr="${YOLO_LR:-0.001}"
  for a in "${YOLO_ARGS[@]}"; do :; done  # consume leftovers if any
  [[ "${YOLO_EPOCHS_SET:-0}" == 1 ]] && epochs="$YOLO_EPOCHS"

  local body
  body="{\"epochs\": $epochs, \"batch_size\": $batch, \"learning_rate\": $lr}"

  if [[ "$DRY_RUN" == 1 ]]; then
    echo "  [dry-run] POST $YOLO_API/api/trainer/train -> $body"
    record_job yolo "$JOB_NAME" dry-run "curl $YOLO_API/api/trainer/train $body"
    return 0
  fi

  local resp
  resp="$(curl -sf -X POST "$YOLO_API/api/trainer/train" \
    -H 'Content-Type: application/json' -d "$body")"
  if echo "$resp" | grep -q '"ok": *true'; then
    ok "YOLO training started on :8199 ($epochs epochs)"
    record_job yolo "$JOB_NAME" started "curl $YOLO_API/api/trainer/train $body"
    info "Watch: $0 status  |  logs: $0 logs yolo"
  else
    err "Failed to start YOLO training: $resp"
  fi
}

# ── COMMAND: stop ────────────────────────────────────────────────────────────
cmd_stop() {
  local type="$1" pid bm
  case "$type" in
    persona)
      pid="$(_get_pid persona)"
      if persona_is_running; then
        kill "$pid" 2>/dev/null
        sleep 1
        if _alive "$pid"; then kill -9 "$pid" 2>/dev/null; fi
        rm -f "$PID_DIR/persona.pid"
        record_job persona "$(basename "$(ps -o args= -p "$pid" 2>/dev/null || echo train)")-stopped" stopped "kill $pid"
        ok "Persona training stopped (pid $pid)"
      else
        warn "No persona training running."
      fi
      ;;
    yolo)
      if yolo_api_up; then
        local st
        st="$(yolo_status | python3 -c 'import sys,json; print(json.load(sys.stdin).get("status","?"))' 2>/dev/null)"
        if [[ "$st" == "training" ]]; then
          # No cancel endpoint on the API: stop the API process itself.
          local apipid
          apipid="$(pgrep -f 'yolo_trainer_api:app' | head -1)"
          if [[ -n "$apipid" ]]; then
            kill "$apipid" 2>/dev/null; sleep 1
            _alive "$apipid" && kill -9 "$apipid" 2>/dev/null
            record_job yolo "yolo-training-stopped" stopped "kill $apipid"
            ok "YOLO training stopped (killed trainer API pid $apipid). Restart with lilly_start.sh or:"
            echo "    python3 -m uvicorn yolo_trainer_api:app --host 0.0.0.0 --port 8199 &"
          else
            warn "YOLO trainer API process not found."
          fi
        else
          info "YOLO trainer API is idle (status=$st). Nothing to stop."
        fi
      else
        err "YOLO trainer API is down."
      fi
      ;;
    all)
      cmd_stop persona
      cmd_stop yolo
      ;;
  esac
}

# ── COMMAND: refresh ─────────────────────────────────────────────────────────
cmd_refresh() {
  local type="$1"; shift
  local bg=1 fetch_photos=0 reset_prog=0 force=0
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --import-photos)  fetch_photos="${2:?--import-photos needs a value}"; shift 2;;
      --reset-progress) reset_prog=1; shift;;
      --force)          force=1; shift;;
      --background)     bg=1; shift;;
      --foreground)     bg=0; shift;;
      --epochs)         YOLO_EPOCHS="$2"; YOLO_EPOCHS_SET=1; shift 2;;
      --dry-run)        DRY_RUN=1; shift;;
      -h|--help)        usage; exit 0;;
      *)                warn "ignoring: $1"; shift;;
    esac
  done

  case "$type" in
    persona)
      info "Refreshing persona training..."
      cmd_stop persona 2>&1 | sed 's/^/    /'
      if [[ "$force" == 1 ]]; then
        info "Wiping $PERSONA_OUT (--force)..."
        [[ "$DRY_RUN" == 1 ]] || rm -rf "$PERSONA_OUT" && mkdir -p "$PERSONA_OUT"
      fi
      start_persona "$bg"
      ;;
    yolo)
      info "Refreshing YOLO training..."
      cmd_stop yolo 2>&1 | sed 's/^/    /'
      if [[ "$force" == 1 ]]; then
        info "Wiping $YOLO_MODELS_DIR (--force)..."
        [[ "$DRY_RUN" == 1 ]] || rm -rf "$YOLO_MODELS_DIR" && mkdir -p "$YOLO_MODELS_DIR"
      fi
      if [[ "$reset_prog" == 1 ]]; then
        info "Resetting photo bridge progress (re-imports everything)..."
        [[ "$DRY_RUN" == 1 ]] || rm -f "$YOLO_DATA_DIR/photo_bridge_progress.json"
      fi
      # Make sure API is back up before starting
      yolo_api_up || {
        info "Starting YOLO trainer API back up..."
        [[ "$DRY_RUN" == 1 ]] || nohup python3 -m uvicorn yolo_trainer_api:app --host 0.0.0.0 --port 8199 \
          >"$LOG_DIR/yolo_api.log" 2>&1 &
        sleep 2
      }
      start_yolo "$bg" "$fetch_photos"
      ;;
    all)
      cmd_refresh persona "$@"
      cmd_refresh yolo "$@"
      ;;
  esac
}

# ── COMMAND: create ──────────────────────────────────────────────────────────
cmd_create() {
  local type="$1"; shift
  local bg=1
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --avatar)     NEW_AVATAR="$2"; shift 2;;
      --quick)      QUICK=1; shift;;
      --gpu)        GPU=1; shift;;
      --gguf-quant) NEW_GGUF="$2"; shift 2;;
      --epochs)     NEW_EPOCHS="$2"; shift 2;;
      --batch-size) NEW_BATCH="$2"; shift 2;;
      --lr)         NEW_LR="$2"; shift 2;;
      --output-dir) NEW_OUT="$2"; shift 2;;
      --name)       NEW_NAME="$2"; shift 2;;
      --import-photos) NEW_PHOTOS="$2"; shift 2;;
      --background) bg=1; shift;;
      --foreground) bg=0; shift;;
      --dry-run)    DRY_RUN=1; shift;;
      -h|--help)    usage; exit 0;;
      *)            warn "ignoring: $1"; shift;;
    esac
  done

  local name="${NEW_NAME:-new_${type}_$(date +%Y%m%d_%H%M%S)}"

  case "$type" in
    persona)
      local cmd=(python3 trainer/train_all_avatars.py)
      [[ -n "${NEW_AVATAR:-}" ]] && cmd+=(--avatar "$NEW_AVATAR")
      [[ "${QUICK:-0}" == 1 ]]   && cmd+=(--quick)
      [[ "${GPU:-0}" == 1 ]]     && cmd+=(--gpu)
      [[ -n "${NEW_GGUF:-}" ]]   && cmd+=(--gguf-quant "$NEW_GGUF")
      [[ -n "${NEW_EPOCHS:-}" ]] && cmd+=(--max-steps "$NEW_EPOCHS")
      [[ -n "${NEW_OUT:-}" ]]    && cmd+=(--output-dir "$NEW_OUT")

      info "Creating new persona training job: $name"
      echo "  command: ${cmd[*]}"
      if [[ "$DRY_RUN" == 1 ]]; then
        record_job persona "$name" created "${cmd[*]}"
        return 0
      fi
      if [[ "$bg" == 1 ]]; then
        local logfile="$(persona_log)"
        nohup "${cmd[@]}" >"$logfile" 2>&1 &
        _save_pid persona "$!"
        record_job persona "$name" created "${cmd[*]}"
        ok "Job '$name' started (pid $!). Log: $logfile"
      else
        record_job persona "$name" created "${cmd[*]}"
        info "Running in foreground (Ctrl-C to stop)..."
        "${cmd[@]}"
      fi
      ;;
    yolo)
      if ! yolo_api_up; then
        err "YOLO trainer API (:8199) down — start it first."
        return 1
      fi
      local ph="${NEW_PHOTOS:-0}"
      if [[ "$ph" -gt 0 ]]; then
        info "Importing $ph phone photos..."
        curl -sf -X POST "$YOLO_API/api/trainer/import-photos" \
          -H 'Content-Type: application/json' -d "{\"max_photos\": $ph}" >/dev/null \
          && ok "Photos imported" || warn "Photo import failed"
      fi
      local body
      body="{\"epochs\": ${NEW_EPOCHS:-50}, \"batch_size\": ${NEW_BATCH:-8}, \"learning_rate\": ${NEW_LR:-0.001}}"
      info "Creating new YOLO training job: $name  ->  POST /api/trainer/train $body"
      if [[ "$DRY_RUN" == 1 ]]; then
        record_job yolo "$name" created "curl -X POST $YOLO_API/api/trainer/train -d '$body'"
        return 0
      fi
      local resp
      resp="$(curl -sf -X POST "$YOLO_API/api/trainer/train" \
        -H 'Content-Type: application/json' -d "$body")"
      if echo "$resp" | grep -q '"ok": *true'; then
        record_job yolo "$name" created "curl -X POST $YOLO_API/api/trainer/train -d '$body'"
        ok "YOLO job '$name' started."
      else
        err "Failed: $resp"
      fi
      ;;
    all)
      cmd_create persona "${@}"
      cmd_create yolo "${@}"
      ;;
  esac
}

# ── COMMAND: logs ────────────────────────────────────────────────────────────
cmd_logs() {
  local type="$1"; shift
  local follow=0 lines=50
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --follow) follow=1; shift;;
      --lines)  lines="$2"; shift 2;;
      *)        warn "ignoring: $1"; shift;;
    esac
  done

  local file
  case "$type" in
    persona)
      file="$(ls -t "$LOG_DIR"/persona_*.log 2>/dev/null | head -1)"
      [[ -z "$file" ]] && file="$(ls -t "$LOG_DIR"/{background_training*.log,systemd_training.log,training_v*.log} 2>/dev/null | head -1)"
      ;;
    yolo)
      file="$(ls -t "$LOG_DIR"/yolo_api.log 2>/dev/null | head -1)"
      [[ -z "$file" ]] && file="$LOG_DIR/systemd_training.log"
      ;;
    all)
      cmd_logs persona "$@"
      echo
      cmd_logs yolo "$@"
      return
      ;;
    *)
      err "Unknown type: $type"; return 1;;
  esac

  if [[ -z "$file" || ! -f "$file" ]]; then
    err "No log file found for '$type'."
    return 1
  fi
  info "Log: $file"
  if [[ "$follow" == 1 ]]; then
    tail -f "$file"
  else
    tail -n "$lines" "$file"
  fi
}

# ── COMMAND: json — machine-readable output for the web dashboard ────────────
cmd_json() {
  local section="${1:-status}" type="${2:-all}" lines="${3:-100}"
  case "$section" in
    status) json_status ;;
    list)   json_list ;;
    logs)   json_logs "$type" "$lines" ;;
    reviews) json_reviews "$type" "$lines" ;;
    *)      echo '{"error":"unknown json section"}' ;;
  esac
}

json_status() {
python3 - "$JOBS_FILE" "$YOLO_MODELS_DIR" "$YOLO_DATA_DIR" "$PERSONA_OUT" "$PID_DIR" "$YOLO_API" <<'PY'
import json, os, sys, subprocess, urllib.request, glob
jobs_file, models_dir, data_dir, persona_out, pid_dir, api = sys.argv[1:7]

out = {
    "persona": {"state": "idle", "pid": None},
    "yolo": {"api_up": False, "state": "unknown", "error": None},
    "data": {"samples": 0, "sample_labels": {}, "persona_models": 0},
    "model": {"best": None, "size": None, "age": None},
    "jobs": [],
}

# ── persona state ──────────────────────────────────────────────────────────
pid_file = os.path.join(pid_dir, "persona.pid")
if os.path.exists(pid_file):
    try:
        pid = int(open(pid_file).read().strip())
        try:  os.kill(pid, 0)
        except OSError:  pid = None
        if pid:
            out["persona"] = {"state": "running", "pid": pid}
    except Exception:
        pass
if out["persona"]["state"] != "running":
    try:
        procs = subprocess.check_output(
            ["pgrep", "-f", "train_all_avatars.py|train_all_background.py|trainer/run.py"],
            text=True).split()
        if procs:
            out["persona"] = {"state": "running", "pid": int(procs[0])}
    except Exception:
        pass

# ── yolo api state ─────────────────────────────────────────────────────────
try:
    with urllib.request.urlopen(f"{api}/api/trainer/status", timeout=3) as r:
        data = json.loads(r.read())
    out["yolo"]["api_up"] = True
    out["yolo"]["state"] = data.get("status", "unknown")
except Exception as e:
    out["yolo"]["error"] = str(e)[:120]

# ── data counts ────────────────────────────────────────────────────────────
samples_dir = os.path.join(data_dir, "samples")
if os.path.isdir(samples_dir):
    labels = {}
    for lab in os.listdir(samples_dir):
        d = os.path.join(samples_dir, lab)
        if os.path.isdir(d):
            n = len(glob.glob(os.path.join(d, "*.jpg")))
            labels[lab] = n
            out["data"]["samples"] += n
    out["data"]["sample_labels"] = labels

if os.path.isdir(persona_out):
    out["data"]["persona_models"] = len([
        d for d in glob.glob(os.path.join(persona_out, "*/final"))
        if os.path.isdir(d)
    ])

# ── best yolo model ────────────────────────────────────────────────────────
cands = []
for p in glob.glob(os.path.join(models_dir, "**", "*.pt"), recursive=True):
    try:  cands.append((os.path.getmtime(p), os.path.getsize(p), p))
    except OSError:  pass
if cands:
    cands.sort(reverse=True)
    mtime, size, path = cands[0]
    import datetime
    out["model"] = {
        "best": path,
        "size": size,
        "age": (datetime.datetime.now() - datetime.datetime.fromtimestamp(mtime)).total_seconds(),
    }

# ── jobs history ───────────────────────────────────────────────────────────
if os.path.exists(jobs_file):
    try:
        db = json.load(open(jobs_file))
        out["jobs"] = db.get("jobs", [])[-15:][::-1]
    except Exception:
        out["jobs"] = []

print(json.dumps(out, indent=2))
PY
}

json_list() {
python3 - "$YOLO_MODELS_DIR" "$YOLO_DATA_DIR" "$PERSONA_OUT" <<'PY'
import json, os, sys, glob
models_dir, data_dir, persona_out = sys.argv[1:4]
out = {"persona": [], "yolo": []}

def fsize(p):
    try:
        return sum(os.path.getsize(os.path.join(dp, f)) for dp, _, fs in os.walk(p) for f in fs)
    except OSError:
        return 0

for d in sorted(glob.glob(os.path.join(persona_out, "*/"))):
    name = os.path.basename(d.rstrip("/"))
    entry = {"name": name, "kind": "none"}
    if os.path.isdir(os.path.join(d, "final")):
        entry["kind"] = "adapter"
        entry["size"] = fsize(os.path.join(d, "final"))
    gguf = glob.glob(os.path.join(d, "gguf", "*.gguf"))
    if gguf:
        entry["kind"] = ("gguf+" + entry["kind"]) if entry["kind"] != "none" else "gguf"
        entry["gguf"] = [os.path.basename(g) for g in gguf]
    out["persona"].append(entry)

for p in sorted(glob.glob(os.path.join(models_dir, "**", "*.pt"), recursive=True), key=os.path.getmtime, reverse=True):
    try:  sz = os.path.getsize(p)
    except OSError:  sz = 0
    out["yolo"].append({"path": p, "size": sz})

labels = {}
sd = os.path.join(data_dir, "samples")
if os.path.isdir(sd):
    for lab in os.listdir(sd):
        d = os.path.join(sd, lab)
        if os.path.isdir(d):
            labels[lab] = len(glob.glob(os.path.join(d, "*.jpg")))
out["samples"] = labels

print(json.dumps(out, indent=2))
PY
}

json_logs() {
  local type="$1" lines="$2"
python3 - "$LOG_DIR" "$type" "$lines" <<'PY'
import glob, json, os, sys, datetime
log_dir, typ, lines = sys.argv[1], sys.argv[2], int(sys.argv[3])
entries = {"persona": None, "yolo": None, "systemd": None}

def tail(path, n):
    try:
        with open(path, "r", errors="replace") as f:
            return f.readlines()[-n:]
    except Exception:
        return None

def find_newest(patterns):
    cands = []
    for pat in patterns:
        cands += glob.glob(os.path.join(log_dir, pat))
    cands.sort(key=os.path.getmtime, reverse=True)
    return cands[0] if cands else None

# latest persona log
p = find_newest(["persona_*.log", "background_training*.log", "training_v*.log", "training.log"])
if p:
    entries["persona"] = {"file": p, "lines": tail(p, lines)}

# latest recent yolo/service log (ignore stale systemd logs > 7 days old)
p = find_newest(["yolo_api.log", "systemd_training.log"])
if p and (datetime.datetime.now().timestamp() - os.path.getmtime(p)) < 7 * 86400:
    entries["yolo"] = {"file": p, "lines": tail(p, lines)}

out = {"ts": datetime.datetime.now().isoformat(), "logs": entries}
if typ != "all":
    out["logs"] = {typ: entries.get(typ)}
print(json.dumps(out))
PY
}

json_reviews() {
  local type="${1:-all}" limit="${2:-10}"
python3 - "$REVIEWS_DIR" "$type" "$limit" <<'PY'
import json, os, sys, glob
review_dir, typ, limit = sys.argv[1], sys.argv[2], int(sys.argv[3])
recs = []
if os.path.isdir(review_dir):
    for p in glob.glob(os.path.join(review_dir, "*.json")):
        try:
            data = json.load(open(p))
        except Exception:
            continue
        if isinstance(data, list):
            for r in data:
                if typ == "all" or r.get("type") == typ:
                    recs.append(r)
recs.sort(key=lambda r: r.get("ts", ""), reverse=True)
print(json.dumps({"reviews": recs[:limit]}, indent=2, default=str))
PY
}

# ── COMMAND: review — orchestrator thought + verdict on latest artifacts ───────
cmd_review() {
  local type="${1:-all}"
  shift || true
  info "Running orchestrator review (type=$type)..."
  python3 trainer/orchestrator_review.py review "$type" "$@"
}

# ── COMMAND: list ────────────────────────────────────────────────────────────
cmd_list() {
  local type="$1"
  case "$type" in
    persona)
      echo "${C_BOLD}Persona models in $PERSONA_OUT/:${C_RESET}"
      if [[ -d "$PERSONA_OUT" ]]; then
        for d in "$PERSONA_OUT"/*/; do
          [[ -d "$d" ]] || continue
          local name; name="$(basename "$d")"
          if [[ -d "$d/final" ]]; then
            ok "  ${name}: final adapter ($(du -sh "$d/final" 2>/dev/null | cut -f1))"
          elif [[ -d "$d/gguf" ]] && ls "$d/gguf"/*.gguf >/dev/null 2>&1; then
            ok "  ${name}: GGUF $(ls "$d/gguf"/*.gguf 2>/dev/null | xargs -n1 basename | tr '\n' ' ')"
          else
            info "  ${name}: (no final model)"
          fi
        done
      else
        warn "  No persona output dir yet."
      fi
      ;;
    yolo)
      echo "${C_BOLD}YOLO models in $YOLO_MODELS_DIR/:${C_RESET}"
      if [[ -d "$YOLO_MODELS_DIR" ]]; then
        find "$YOLO_MODELS_DIR" -name '*.pt' -printf '%T@ %p\n' 2>/dev/null \
          | sort -rn | while read -r _ p; do
            printf "  %s (%s)\n" "$p" "$(du -h "$p" | cut -f1)"
          done
        local bm; bm="$(yolo_best_model)"
        [[ -n "$bm" ]] && ok "  ACTIVE: $bm"
      else
        warn "  No YOLO models dir yet."
      fi
      echo
      local samples=0
      [[ -d "$YOLO_DATA_DIR/samples" ]] && samples="$(find "$YOLO_DATA_DIR/samples" -name '*.jpg' | wc -l)"
      info "Training samples: $samples"
      echo "${C_BOLD}Sample breakdown:${C_RESET}"
      find "$YOLO_DATA_DIR/samples" -mindepth 1 -maxdepth 1 -type d 2>/dev/null \
        | while read -r d; do
          local c; c="$(find "$d" -name '*.jpg' | wc -l)"
          printf "  %-20s %s samples\n" "$(basename "$d")" "$c"
        done
      ;;
    all)
      cmd_list persona
      echo
      cmd_list yolo
      ;;
  esac
}

# ── COMMAND: deploy ──────────────────────────────────────────────────────────
cmd_deploy() {
  local type="$1"; shift
  local model_path=""
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --model-path) model_path="$2"; shift 2;;
      *) warn "ignoring: $1"; shift;;
    esac
  done

  case "$type" in
    yolo)
      if ! yolo_api_up; then err "YOLO trainer API down."; return 1; fi
      [[ -z "$model_path" ]] && model_path="$(yolo_best_model)"
      if [[ -z "$model_path" ]]; then
        err "No YOLO model found. Train one first."
        return 1
      fi
      info "Deploying YOLO model: $model_path"
      local resp
      resp="$(curl -sf -X POST "$YOLO_API/api/trainer/deploy" \
        -H 'Content-Type: application/json' \
        -d "{\"model_path\": \"$model_path\", \"version\": \"v$(date +%s)\"}")"
      echo "$resp" | grep -q '"ok": *true' \
        && ok "YOLO model deployed (version v$(date +%s))" \
        || err "Deploy failed: $resp"
      ;;
    persona)
      [[ -z "$model_path" ]] && model_path="$PERSONA_OUT/$(ls "$PERSONA_OUT" 2>/dev/null | head -1)/final"
      if [[ -z "$model_path" || ! -d "$model_path" ]]; then
        err "No persona adapter to deploy. Train first."
        return 1
      fi
      info "Persona model deploy: $model_path"
      info "  llama.cpp/Ollama: see trainer/train_all_avatars.py export (--gpu for GGUF)."
      ok "Adapter ready at $model_path — point your backend at it (lillyos/models/ or Ollama)."
      ;;
    all)
      cmd_deploy persona "$@"
      cmd_deploy yolo "$@"
      ;;
  esac
}

# ── Main dispatch ────────────────────────────────────────────────────────────
main() {
  local cmd="${1:-help}"; shift 2>/dev/null || true
  local type="all"
  # optional second positional = type
  if [[ $# -gt 0 ]] && [[ "$1" == "persona" || "$1" == "yolo" || "$1" == "all" ]]; then
    type="$1"; shift
  fi

  DRY_RUN=0; JOB_NAME=""; IMPORT_PHOTOS=0; YOLO_EPOCHS=""; YOLO_EPOCHS_SET=0; YOLO_ARGS=(); PERSONA_ARGS=()

  case "$cmd" in
    start)   cmd_start "$type" "$@";;
    stop)    cmd_stop "$type";;
    status)  cmd_status;;
    refresh) cmd_refresh "$type" "$@";;
    create)  cmd_create "$type" "$@";;
    logs)    cmd_logs "$type" "$@";;
    list)    cmd_list "$type";;
    json)    cmd_json "$@";;
    review)  cmd_review "$type" "$@";;
    deploy)  cmd_deploy "$type" "$@";;
    help|-h|--help) usage;;
    *)
      err "Unknown command: '$cmd'"
      echo
      usage
      exit 1
      ;;
  esac
}

main "$@"