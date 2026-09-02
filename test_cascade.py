#!/usr/bin/env python3
"""
Test script for the Multi-Model Cascade
"""

import asyncio
import httpx
import json
import time

BASE_URL = "http://localhost:8099"


async def test_health():
    """Test health endpoint"""
    async with httpx.AsyncClient() as client:
        response = await client.get(f"{BASE_URL}/health")
        print(f"Health: {response.json()}")


async def test_simple_query():
    """Test a simple query (should use light cascade)"""
    async with httpx.AsyncClient() as client:
        start = time.time()
        response = await client.post(
            f"{BASE_URL}/v1/chat/completions",
            json={"query": "What is 2 + 2?", "force_heavy": False},
        )
        result = response.json()
        latency = (time.time() - start) * 1000

        print(f"\nSimple Query Test:")
        print(f"  Query: What is 2 + 2?")
        print(f"  Model used: {result['model_used']}")
        print(f"  Latency: {latency:.0f}ms (server: {result['latency_ms']:.0f}ms)")
        print(f"  Response: {result['response'][:200]}...")


async def test_complex_query():
    """Test a complex query (should use heavy cascade)"""
    async with httpx.AsyncClient() as client:
        start = time.time()
        response = await client.post(
            f"{BASE_URL}/v1/chat/completions",
            json={
                "query": "Explain the difference between quantum entanglement and quantum superposition, and how they could be used together in quantum computing. Include real-world applications.",
                "force_heavy": False,
            },
        )
        result = response.json()
        latency = (time.time() - start) * 1000

        print(f"\nComplex Query Test:")
        print(f"  Query: Quantum entanglement vs superposition...")
        print(f"  Model used: {result['model_used']}")
        print(f"  Route info: {result['route_info']}")
        print(f"  Latency: {latency:.0f}ms (server: {result['latency_ms']:.0f}ms)")
        print(f"  Response: {result['response'][:300]}...")


async def test_stats():
    """Test stats endpoint"""
    async with httpx.AsyncClient() as client:
        response = await client.get(f"{BASE_URL}/stats")
        print(f"\nStats: {json.dumps(response.json(), indent=2)}")


async def main():
    print("Testing Multi-Model Cascade...")
    print("=" * 50)

    try:
        await test_health()
        await test_simple_query()
        await test_complex_query()
        await test_stats()
    except Exception as e:
        print(f"Error: {e}")
        print("\nMake sure the cascade server is running on port 8099")


if __name__ == "__main__":
    asyncio.run(main())
