#!/usr/bin/env bash
# ─── Download Ternary-Bonsai-27B-Q2_g64 + build llama-server ───────────────
# Run once to get the model and server binary ready.
# The model file is ~14 GB — make sure you have enough disk space.
set -euo pipefail

LLAMA_DIR="$HOME/llama.cpp"
MODEL_DIR="$LLAMA_DIR/models"
MODEL_FILE="Ternary-Bonsai-27B-Q2_g64.gguf"
MODEL_URL="https://huggingface.co/prism-ml/Ternary-Bonsai-27B-gguf/resolve/main/Ternary-Bonsai-27B-Q2_g64.gguf?download=true"

echo "==> Checking disk space..."
AVAIL_GB=$(df -BG "$HOME" | awk 'NR==2 {gsub("G",""); print $4}')
echo "    Available: ${AVAIL_GB}GB (need ~15GB)"
if [ "$AVAIL_GB" -lt 15 ]; then
    echo "WARNING: Less than 15GB free. Proceeding anyway — watch for ENOSPC errors."
fi

# ── 1. Build llama-server if not already built ────────────────────────────────
if [ ! -f "$LLAMA_DIR/build/bin/llama-server" ]; then
    echo "==> llama.cpp not built yet — building now..."

    # Install build deps
    if command -v pkg &>/dev/null; then
        pkg update -y
        pkg install -y cmake make ninja build-essential git wget
    elif command -v apt &>/dev/null; then
        sudo apt update -y
        sudo apt install -y cmake make build-essential git wget
    fi

    # Clone if needed
    if [ ! -d "$LLAMA_DIR" ]; then
        echo "==> Cloning llama.cpp..."
        git clone --depth 1 https://github.com/ggml-org/llama.cpp "$LLAMA_DIR"
    else
        echo "==> llama.cpp repo already exists, pulling latest..."
        git -C "$LLAMA_DIR" pull --ff-only || true
    fi

    echo "==> Building llama-server (this takes a few minutes)..."
    cmake -S "$LLAMA_DIR" -B "$LLAMA_DIR/build" \
        -DLLAMA_CUDA=OFF \
        -DLLAMA_METAL=OFF \
        -DCMAKE_BUILD_TYPE=Release
    cmake --build "$LLAMA_DIR/build" --config Release -j"$(nproc)" \
        --target llama-server llama-cli
    echo "==> llama-server built: $LLAMA_DIR/build/bin/llama-server"
else
    echo "==> llama-server already built — skipping."
fi

# ── 2. Download model ─────────────────────────────────────────────────────────
mkdir -p "$MODEL_DIR"

if [ -f "$MODEL_DIR/$MODEL_FILE" ]; then
    echo "==> Model already downloaded: $MODEL_DIR/$MODEL_FILE"
else
    echo "==> Downloading $MODEL_FILE (~14GB) — this will take a while..."
    echo "    URL: $MODEL_URL"
    # Use wget with resume support and progress bar
    wget --continue --show-progress \
        -O "$MODEL_DIR/$MODEL_FILE" \
        "$MODEL_URL"
    echo "==> Download complete: $MODEL_DIR/$MODEL_FILE"
fi

echo ""
echo "==> All done!"
echo "    Binary:  $LLAMA_DIR/build/bin/llama-server"
echo "    Model:   $MODEL_DIR/$MODEL_FILE"
echo ""
echo "To start Lilly with Ternary-Bonsai-27B:"
echo "    PREFER_BACKEND=llama python3 $HOME/Lilly_Workspace/lilly_ai.py"
echo ""
echo "Or just restart — PREFER_BACKEND=llama is already set in .env"
