#!/usr/bin/env bash
# ==============================================================================
# Lilly Sensor Platform - Part 1: Diagnostic & Environment Scanner
# ==============================================================================

PROJECT_DIR=$(pwd)
TIMESTAMP=$(date +"%Y-%m-%d %H:%M:%S")

echo "=========================================="
echo "  LILLY SENSOR SYSTEM: PART 1 SCANNER    "
echo "  Time: $TIMESTAMP"
echo "=========================================="

# 1. System & Architecture Check
echo -e "\n[+] 1. Checking System Architecture & OS..."
echo " - Kernel: $(uname -r)"
echo " - Architecture: $(uname -m)"
if [ -f /etc/os-release ]; then
    echo " - OS: $(grep -E '^PRETTY_NAME=' /etc/os-release | cut -d= -f2 | tr -d '"')"
fi

# 2. Check Termux-API Availability
echo -e "\n[+] 2. Checking Termux-API Integration..."
if command -v termux-location &> /dev/null; then
    echo " - termux-api CLI: INSTALLED"
else
    echo " - termux-api CLI: NOT INSTALLED (Run: pkg install termux-api)"
fi

# 3. Check Python & SQLite3
echo -e "\n[+] 3. Checking Local Runtime Dependencies..."
for tool in python sqlite3 ssh sshd; do
    if command -v $tool &> /dev/null; then
        echo " - $tool: INSTALLED ($(command -v $tool))"
    else
        echo " - $tool: MISSING"
    fi
done

# 4. Storage & Folder Structure Scan
echo -e "\n[+] 4. Verifying Project Directory Structure..."
echo " - Project Root: $PROJECT_DIR"
for dir in "logs" "db" "skills"; do
    if [ ! -d "$PROJECT_DIR/$dir" ]; then
        mkdir -p "$PROJECT_DIR/$dir"
        echo " - Created directory: $PROJECT_DIR/$dir"
    else
        echo " - Directory exists: $PROJECT_DIR/$dir"
    fi
done

# 5. Live Sensor Capabilities Test
echo -e "\n[+] 5. Testing Live Hardware Sensors (Termux API)..."

# Test Wi-Fi Sensor
if command -v termux-wifi-connectioninfo &> /dev/null; then
    WIFI_INFO=$(termux-wifi-connectioninfo 2>/dev/null)
    if [ -n "$WIFI_INFO" ]; then
        SSID=$(echo "$WIFI_INFO" | grep -o '"ssid": "[^"]*"' | cut -d'"' -f4)
        echo " - Wi-Fi Sensor: ACTIVE (Current SSID: ${SSID:-Disconnected})"
    else
        echo " - Wi-Fi Sensor: FAILED (Check Android Location/Wi-Fi permissions)"
    fi
else
    echo " - Wi-Fi Sensor: SKIPPED (termux-api binary missing)"
fi

# Test GPS Location
if command -v termux-location &> /dev/null; then
    echo " - Testing GPS sensor lock (5s timeout)..."
    GPS_INFO=$(timeout 5s termux-location -p gps -r last 2>/dev/null)
    if [ -n "$GPS_INFO" ]; then
        echo " - GPS Sensor: ACTIVE"
    else
        echo " - GPS Sensor: NO LOCK / PERMISSION DENIED"
    fi
fi

# 6. Generate Prerequisites Manifest for Part 2
MANIFEST_FILE="$PROJECT_DIR/logs/part1_manifest.json"
cat <<EOF > "$MANIFEST_FILE"
{
  "timestamp": "$TIMESTAMP",
  "project_dir": "$PROJECT_DIR",
  "termux_api_installed": $(command -v termux-location &> /dev/null && echo "true" || echo "false"),
  "python_installed": $(command -v python &> /dev/null && echo "true" || echo "false"),
  "sqlite_installed": $(command -v sqlite3 &> /dev/null && echo "true" || echo "false"),
  "sshd_running": $(pgrep sshd &> /dev/null && echo "true" || echo "false")
}
EOF

echo -e "\n=========================================="
echo "  PART 1 COMPLETE"
echo "  Manifest written to: $MANIFEST_FILE"
echo "=========================================="

