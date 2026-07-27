#!/usr/bin/env bash
set -euo pipefail
# ─── llama.cpp setup for Lilly AI ───────────────────────────────
# Run this on Termux (Android) or Linux to build llama.cpp

LLAMA_DIR="$HOME/llama.cpp"
MODEL_DIR="$LLAMA_DIR/models"

echo "==> Installing dependencies (Termux)..."
if command -v pkg &>/dev/null; then
    pkg update -y
    pkg install -y cmake make ninja build-essential git wget python3 ffmpeg termux-tts-speak termux-toast termux-microphone-record termux-battery-status termux-sensor termux-notification-list
elif command -v apt &>/dev/null; then
    sudo apt update
    sudo apt install -y cmake make build-essential git wget python3-pip ffmpeg
fi

echo "==> Cloning llama.cpp..."
if [ ! -d "$LLAMA_DIR" ]; then
    git clone --depth 1 https://github.com/ggml-org/llama.cpp "$LLAMA_DIR"
fi

echo "==> Building llama.cpp..."
cmake -S "$LLAMA_DIR" -B "$LLAMA_DIR/build" -DLLAMA_CUDA=OFF -DLLAMA_METAL=OFF
cmake --build "$LLAMA_DIR/build" --config Release -j4 --target llama-server llama-cli

echo "==> Downloading a small GGUF model (Phi-3-mini 4K)..."
mkdir -p "$MODEL_DIR"
if [ ! -f "$MODEL_DIR/phi-3-mini-4k-instruct.Q4_K_M.gguf" ]; then
    wget -O "$MODEL_DIR/phi-3-mini-4k-instruct.Q4_K_M.gguf" \
        "https://huggingface.co/microsoft/Phi-3-mini-4k-instruct-gguf/resolve/main/Phi-3-mini-4k-instruct.Q4_K_M.gguf"
fi

echo ""
echo "==> Setup complete!"
echo "    Server binary: $LLAMA_DIR/build/bin/llama-server"
echo "    CLI binary:    $LLAMA_DIR/build/bin/llama-cli"
echo ""
echo "To start Lilly: python3 $HOME/Lilly_Workspace/lilly_ai.py"
