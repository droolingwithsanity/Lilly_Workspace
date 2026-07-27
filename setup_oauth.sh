#!/bin/bash
# OAuth Setup Script for Lilly AI Email Integration
# This script helps configure the OAuth credentials and start Open Connector

set -e

echo "=== Lilly AI OAuth Setup ==="
echo ""

# Check if .env exists
if [ ! -f .env ]; then
    echo "Creating .env from .env.email.example..."
    cp .env.email.example .env
    echo "Please edit .env with your OAuth credentials before continuing."
    echo "Press Enter when ready..."
    read
fi

# Source .env
source .env

# Validate required variables
echo "Validating configuration..."
required_vars=(
    "GMAIL_CLIENT_ID"
    "GMAIL_CLIENT_SECRET"
    "OUTLOOK_CLIENT_ID"
    "OUTLOOK_CLIENT_SECRET"
    "OPENCONNECTOR_RUNTIME_TOKEN"
)

missing=()
for var in "${required_vars[@]}"; do
    if [ -z "${!var}" ] || [[ "${!var}" == *"your_"* ]]; then
        missing+=("$var")
    fi
done

if [ ${#missing[@]} -gt 0 ]; then
    echo "Missing or placeholder values in .env:"
    for var in "${missing[@]}"; do
        echo "  - $var"
    done
    echo ""
    echo "Please update .env with your actual credentials."
    echo "See docs/OAUTH_SETUP.md for detailed instructions."
    exit 1
fi

echo "Configuration looks good!"
echo ""

# Start Open Connector
echo "Starting Open Connector..."
docker compose -f docker-compose.email.yml up -d connector

echo "Waiting for Open Connector to be healthy..."
for i in {1..30}; do
    if curl -sf http://localhost:3000/health > /dev/null 2>&1; then
        echo "Open Connector is ready!"
        break
    fi
    echo "Waiting... ($i/30)"
    sleep 2
done

# Test connection
echo ""
echo "Testing Gmail connection..."
response=$(curl -s -X POST http://localhost:3000/v1/actions/gmail.get_profile \
    -H "Authorization: Bearer $OPENCONNECTOR_RUNTIME_TOKEN" \
    -H "Content-Type: application/json" \
    -d '{"input":{}}' 2>&1)

if echo "$response" | grep -q "emailAddress"; then
    echo "✓ Gmail connection successful!"
else
    echo "✗ Gmail connection failed"
    echo "Response: $response"
    echo ""
    echo "Please ensure you've:"
    echo "1. Configured Gmail OAuth in Open Connector UI (http://localhost:3000)"
    echo "2. Connected your Gmail account"
    echo "3. Created a runtime token"
fi

echo ""
echo "Testing Outlook connection..."
response=$(curl -s -X POST http://localhost:3000/v1/actions/outlook.get_profile \
    -H "Authorization: Bearer $OPENCONNECTOR_RUNTIME_TOKEN" \
    -H "Content-Type: application/json" \
    -d '{"input":{}}' 2>&1)

if echo "$response" | grep -q "emailAddress"; then
    echo "✓ Outlook connection successful!"
else
    echo "✗ Outlook connection failed"
    echo "Response: $response"
    echo ""
    echo "Please ensure you've:"
    echo "1. Configured Outlook OAuth in Open Connector UI (http://localhost:3000)"
    echo "2. Connected your Outlook account"
fi

echo ""
echo "=== Setup Complete ==="
echo ""
echo "Next steps:"
echo "1. Start Lilly AI with email integration:"
echo "   docker compose -f docker-compose.email.yml up -d"
echo ""
echo "2. Access Open Connector UI: http://localhost:3000"
echo "3. Test email features via Lilly API endpoints"
