#!/usr/bin/env python3
"""
Test script for the new awareness features in Lilly AI.
Tests the surroundings awareness and find my device features.
"""

import asyncio
import json
import httpx

# Test configuration
LILLY_AI_URL = "http://localhost:8098"
SENSOR_SERVER_URL = "http://100.115.234.87:8099"


async def test_surroundings_awareness():
    """Test the surroundings awareness feature."""
    print("Testing surroundings awareness...")

    # Test the /api/cmd endpoint with surroundings trigger
    test_phrases = [
        "what's around me",
        "whats around me",
        "who is near me",
        "scan surroundings",
        "describe my environment",
    ]

    async with httpx.AsyncClient(timeout=30.0) as client:
        for phrase in test_phrases:
            print(f"\nTesting phrase: '{phrase}'")
            try:
                response = await client.post(
                    f"{LILLY_AI_URL}/api/cmd", json={"text": phrase, "avatar": "puppy"}
                )
                if response.status_code == 200:
                    data = response.json()
                    reply = data.get("reply", "No reply")
                    print(f"✓ Success: {reply[:100]}...")
                else:
                    print(f"✗ HTTP {response.status_code}: {response.text[:100]}")
            except Exception as e:
                print(f"✗ Error: {e}")


async def test_find_my_device():
    """Test the find my device feature."""
    print("\n\nTesting find my device...")

    # Test the /api/cmd endpoint with find my device trigger
    test_phrases = [
        "find my phone",
        "where's my phone",
        "locate my device",
        "find my headphones",
        "where is my watch",
    ]

    async with httpx.AsyncClient(timeout=30.0) as client:
        for phrase in test_phrases:
            print(f"\nTesting phrase: '{phrase}'")
            try:
                response = await client.post(
                    f"{LILLY_AI_URL}/api/cmd", json={"text": phrase, "avatar": "puppy"}
                )
                if response.status_code == 200:
                    data = response.json()
                    reply = data.get("reply", "No reply")
                    print(f"✓ Success: {reply[:100]}...")
                else:
                    print(f"✗ HTTP {response.status_code}: {response.text[:100]}")
            except Exception as e:
                print(f"✗ Error: {e}")


async def test_bluetooth_devices():
    """Test the bluetooth devices feature."""
    print("\n\nTesting bluetooth devices...")

    # Test the /api/cmd endpoint with bluetooth trigger
    test_phrases = [
        "bluetooth",
        "devices near",
        "nearby devices",
        "who is near",
        "bluetooth devices",
    ]

    async with httpx.AsyncClient(timeout=30.0) as client:
        for phrase in test_phrases:
            print(f"\nTesting phrase: '{phrase}'")
            try:
                response = await client.post(
                    f"{LILLY_AI_URL}/api/cmd", json={"text": phrase, "avatar": "puppy"}
                )
                if response.status_code == 200:
                    data = response.json()
                    reply = data.get("reply", "No reply")
                    print(f"✓ Success: {reply[:100]}...")
                else:
                    print(f"✗ HTTP {response.status_code}: {response.text[:100]}")
            except Exception as e:
                print(f"✗ Error: {e}")


async def test_sensor_server_direct():
    """Test the sensor server endpoints directly."""
    print("\n\nTesting sensor server endpoints directly...")

    async with httpx.AsyncClient(timeout=10.0) as client:
        # Test health endpoint
        try:
            response = await client.get(f"{SENSOR_SERVER_URL}/health")
            if response.status_code == 200:
                data = response.json()
                print(f"✓ Sensor server health: {data}")
            else:
                print(f"✗ Sensor server health failed: HTTP {response.status_code}")
        except Exception as e:
            print(f"✗ Sensor server health error: {e}")

        # Test bluetooth scan
        try:
            response = await client.get(f"{SENSOR_SERVER_URL}/bluetooth/scan/live")
            if response.status_code == 200:
                data = response.json()
                print(f"✓ Bluetooth scan: {data.get('count', 0)} devices found")
            else:
                print(f"✗ Bluetooth scan failed: HTTP {response.status_code}")
        except Exception as e:
            print(f"✗ Bluetooth scan error: {e}")

        # Test wifi scan
        try:
            response = await client.get(f"{SENSOR_SERVER_URL}/wifi/scan/live")
            if response.status_code == 200:
                data = response.json()
                print(f"✓ WiFi scan: {data.get('count', 0)} networks found")
            else:
                print(f"✗ WiFi scan failed: HTTP {response.status_code}")
        except Exception as e:
            print(f"✗ WiFi scan error: {e}")


async def main():
    """Run all tests."""
    print("=== Testing Lilly AI Awareness Features ===\n")

    # Test sensor server first
    await test_sensor_server_direct()

    # Test Lilly AI endpoints
    await test_surroundings_awareness()
    await test_find_my_device()
    await test_bluetooth_devices()

    print("\n=== Tests completed ===")


if __name__ == "__main__":
    asyncio.run(main())
