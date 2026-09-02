#!/bin/bash
# ============================================================
# NVIDIA NVS 510 (Kepler GK107) — Driver Fix Script
# Run with: sudo bash fix_driver.sh
# ============================================================
set -e

echo "============================================"
echo "  Fixing NVIDIA Driver for NVS 510 (Kepler)"
echo "============================================"
echo ""

# Step 1: Remove conflicting 535 driver
echo "[1/6] Removing driver 535 (incompatible with Kepler)..."
apt-get remove -y --purge nvidia-driver-535 nvidia-utils-535 \
  libnvidia-gl-535 libnvidia-compute-535 libnvidia-decode-535 \
  libnvidia-encode-535 libnvidia-cfg1-535 libnvidia-common-535 2>/dev/null || true
apt-get autoremove -y

# Step 2: Install driver 470 (last to support Kepler)
echo "[2/6] Installing nvidia-driver-470..."
apt-get update
apt-get install -y --allow-downgrades \
  nvidia-driver-470 \
  nvidia-utils-470 \
  libnvidia-gl-470 \
  libnvidia-compute-470 \
  libnvidia-decode-470 \
  libnvidia-encode-470

# Step 3: Install CUDA 12.4 toolkit
echo "[3/6] Installing CUDA 12.4 toolkit..."
apt-get install -y nvidia-cuda-toolkit 2>/dev/null || true

# Step 4: Update initramfs
echo "[4/6] Updating initramfs..."
update-initramfs -u 2>/dev/null || true

# Step 5: Load the module
echo "[5/6] Loading nvidia module..."
modprobe nvidia 2>/dev/null || echo "  (will load after reboot)"

# Step 6: Verify
echo "[6/6] Verifying..."
nvidia-smi 2>&1 || echo "  Driver installed but needs reboot to take effect"

echo ""
echo "============================================"
echo "  DONE. Reboot now:"
echo "    sudo reboot"
echo ""
echo "  After reboot run:"
echo "    nvidia-smi"
echo "    python3 fix_pytorch.sh"
echo "============================================"
