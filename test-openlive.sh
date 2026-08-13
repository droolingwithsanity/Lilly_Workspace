#!/bin/bash
echo "=========================================="
echo "  OpenLive + Lilly AI Integration Test"
echo "=========================================="
echo

echo "1. Testing Ollama..."
if curl -s http://127.0.0.1:11434/api/tags > /dev/null 2>&1; then
  echo "   ✅ Ollama is running"
else
  echo "   ❌ Ollama is not running"
fi

echo
echo "2. Testing Lilly AI..."
if curl -s http://localhost:8098/api/health > /dev/null 2>&1; then
  echo "   ✅ Lilly AI is running"
else
  echo "   ❌ Lilly AI is not running"
fi

echo
echo "3. Testing OpenLive Agent..."
if curl -s http://localhost:8787/health > /dev/null 2>&1; then
  echo "   ✅ OpenLive Agent is running"
else
  echo "   ❌ OpenLive Agent is not running"
fi

echo
echo "4. Testing OpenLive Web..."
if curl -s http://localhost:3000 > /dev/null 2>&1; then
  echo "   ✅ OpenLive Web UI is running"
else
  echo "   ❌ OpenLive Web UI is not running"
fi

echo
echo "5. Testing Lilly Bridge..."
if curl -s http://localhost:8788/health > /dev/null 2>&1; then
  echo "   ✅ Lilly Bridge is running"
else
  echo "   ❌ Lilly Bridge is not running"
fi

echo
echo "=========================================="
echo "  URLs"
echo "=========================================="
echo
echo "  OpenLive Web UI:    http://localhost:3000"
echo "  OpenLive Agent:     http://localhost:8787"
echo "  Lilly Bridge:       http://localhost:8788"
echo "  Lilly AI:           http://localhost:8098"
echo "  Autensa:            http://localhost:4000"
echo
echo "=========================================="
