#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

echo "=== Building Lilly v21 APK + Server ==="

# Check if Docker is available
if ! command -v docker &> /dev/null; then
    echo "ERROR: Docker is required. Install it from https://docs.docker.com/get-docker/"
    exit 1
fi

# Build the Docker image
echo "Building Docker image (this will compile the APK)..."
docker build -t lilly-overlay:latest .

echo ""
echo "=== Starting server on port 8098 ==="
echo "Open http://localhost:8098 in your browser to download the APK"
echo ""

# Run the container
docker run --rm -p 8098:8098  lilly-overlay:latest
