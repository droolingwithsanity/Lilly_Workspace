#!/bin/bash
# Start the Multi-Model Cascade service

echo "Starting Multi-Model Cascade..."
echo "Ollama URL: http://100.73.249.14:11434"
echo "Cascade Port: 8099"
echo ""

# Check if Python dependencies are installed
if ! python3 -c "import httpx, fastapi, uvicorn" 2>/dev/null; then
    echo "Installing dependencies..."
    pip install -r requirements_cascade.txt
fi

# Start the service
echo "Starting cascade service on port 8099..."
python3 model_cascade.py
