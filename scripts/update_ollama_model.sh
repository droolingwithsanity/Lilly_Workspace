#!/bin/bash
set -euo pipefail

OLLAMA_HOST="${OLLAMA_HOST:-100.93.131.114}"
OLLAMA_PORT="${OLLAMA_PORT:-11434}"
OLLAMA_URL="http://${OLLAMA_HOST}:${OLLAMA_PORT}"
DEFAULT_MODEL="${OLLAMA_MODEL:-qwen3:8b}"

echo "Checking Ollama at ${OLLAMA_URL}"

# Check if Ollama is reachable
if ! curl -sf "${OLLAMA_URL}/api/tags" > /dev/null 2>&1; then
    echo "ERROR: Cannot reach Ollama at ${OLLAMA_URL}"
    exit 1
fi

# Get current model info
echo "Current default model: ${DEFAULT_MODEL}"

# Pull latest if not present
if ! curl -sf "${OLLAMA_URL}/api/tags" | python3 -c "import sys,json; data=json.load(sys.stdin); models=[m['name'] for m in data.get('models',[])]; sys.exit(0 if '${DEFAULT_MODEL}' in models else 1)" 2>/dev/null; then
    echo "Pulling ${DEFAULT_MODEL}..."
    curl -sf "${OLLAMA_URL}/api/pull" -d "{\"name\":\"${DEFAULT_MODEL}\"}" || true
else
    echo "Model ${DEFAULT_MODEL} already available"
fi

# Show available Qwen models
echo ""
echo "Available Qwen models:"
curl -sf "${OLLAMA_URL}/api/tags" | python3 -c "
import sys, json
data = json.load(sys.stdin)
for m in data.get('models', []):
    name = m.get('name', '')
    if 'qwen' in name.lower():
        size = m.get('size', 0)
        size_gb = size / (1024**3)
        print(f'  {name} ({size_gb:.1f} GB)')
" 2>/dev/null || echo "  (unable to parse model list)"
