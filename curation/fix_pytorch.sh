#!/bin/bash
set -e
echo "=== Reinstalling PyTorch with CUDA 12.1 (matches driver 470) ==="
pip3 uninstall -y torch torchvision torchaudio
pip3 install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu121
echo ""
python3 -c "import torch; print(f'PyTorch {torch.__version__}'); print(f'CUDA: {torch.cuda.is_available()}')"
echo "=== Done ==="
