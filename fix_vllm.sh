#!/bin/bash

echo "=========================================="
echo " vLLM & NVIDIA Driver Resolution Script "
echo "=========================================="

# 1. Update package lists
echo "[1/4] Updating package lists..."
sudo apt update -y

# 2. Install modern NVIDIA drivers (Version 535 is highly stable for modern PyTorch)
echo "[2/4] Installing modern NVIDIA drivers (nvidia-driver-535)..."
sudo apt install -y nvidia-driver-535

# 3. Create the correct launch script for your model
echo "[3/4] Creating the correct vLLM launch script..."
cat << 'EOF' > start_model.sh
#!/bin/bash
# The correct syntax uses the 'vllm' CLI command directly
vllm serve Qwen/Qwen2.5-VL-7B-Instruct --host 0.0.0.0 --port 8000
EOF

chmod +x start_model.sh
echo "Created 'start_model.sh' in your current directory."

# 4. Prompt for reboot
echo "=========================================="
echo "[4/4] ACTION REQUIRED: Reboot your machine"
echo "=========================================="
echo "The new NVIDIA drivers will not take effect until you restart."
echo "After rebooting, simply run:"
echo "  ./start_model.sh"
echo "=========================================="
