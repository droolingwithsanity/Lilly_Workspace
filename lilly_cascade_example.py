#!/usr/bin/env python3
"""
Example: Using Multi-Model Cascade with Lilly AI
Shows how to integrate the cascade into existing Lilly code
"""

import httpx
import asyncio
from typing import Optional


class LillyCascade:
    """Wrapper for using the cascade in Lilly AI"""

    def __init__(self, cascade_url: str = "http://localhost:8099"):
        self.cascade_url = cascade_url
        self.client = httpx.AsyncClient(timeout=60.0)

    async def close(self):
        await self.client.aclose()

    async def query(
        self, prompt: str, context: Optional[str] = None, force_heavy: bool = False
    ) -> str:
        """
        Query the cascade and return response

        Args:
            prompt: User message
            context: Optional conversation context
            force_heavy: Force use of heavy model

        Returns:
            Response string
        """
        try:
            response = await self.client.post(
                f"{self.cascade_url}/v1/chat/completions",
                json={"query": prompt, "context": context, "force_heavy": force_heavy},
            )
            response.raise_for_status()
            result = response.json()
            return result["response"]
        except Exception as e:
            return f"Error querying cascade: {e}"

    async def get_stats(self) -> dict:
        """Get cascade performance stats"""
        response = await self.client.get(f"{self.cascade_url}/stats")
        return response.json()


# Example usage in Lilly AI
async def example_lilly_integration():
    """Example of how to use cascade in Lilly"""

    cascade = LillyCascade()

    # Example 1: Simple query (uses light cascade)
    print("Example 1: Simple query")
    response = await cascade.query("What's 2 + 2?")
    print(f"Response: {response}\n")

    # Example 2: Complex query (uses heavy model)
    print("Example 2: Complex query")
    response = await cascade.query(
        "Explain the difference between TCP and UDP protocols, "
        "including when to use each one and real-world examples."
    )
    print(f"Response: {response}\n")

    # Example 3: Query with context
    print("Example 3: Query with context")
    response = await cascade.query(
        "Continue that thought and elaborate more",
        context="We were discussing quantum computing applications",
    )
    print(f"Response: {response}\n")

    # Example 4: Force heavy reasoning
    print("Example 4: Force heavy reasoning")
    response = await cascade.query(
        "Analyze the pros and cons of microservices vs monolithic architecture",
        force_heavy=True,
    )
    print(f"Response: {response}\n")

    # Check stats
    print("Cascade Stats:")
    stats = await cascade.get_stats()
    print(f"  Total queries: {stats['total_queries']}")
    print(f"  Tokens saved: {stats['tokens_saved']}")

    await cascade.close()


# Integration with existing Lilly chat endpoint
async def lilly_chat_with_cascade(message: str, history: list) -> str:
    """
    Drop-in replacement for Lilly's chat function

    Replace your existing chat logic with this
    """
    cascade = LillyCascade()

    # Format history for context
    context = "\n".join([f"{role}: {content}" for role, content in history[-5:]])

    # Query cascade
    response = await cascade.query(message, context)

    await cascade.close()
    return response


# Performance comparison
async def compare_performance():
    """Compare cascade vs direct Ollama call"""
    import time

    cascade = LillyCascade()

    queries = [
        "What is Python?",
        "Explain object-oriented programming",
        "How does memory management work in C++?",
        "Compare React vs Vue vs Angular",
    ]

    print("Performance Comparison")
    print("=" * 60)

    for query in queries:
        # Cascade query
        start = time.time()
        cascade_response = await cascade.query(query)
        cascade_time = (time.time() - start) * 1000

        print(f"\nQuery: {query[:50]}...")
        print(f"  Cascade: {cascade_time:.0f}ms")
        print(f"  Response length: {len(cascade_response)} chars")

    # Show stats
    stats = await cascade.get_stats()
    print(f"\nTotal tokens saved: {stats['tokens_saved']}")

    await cascade.close()


if __name__ == "__main__":
    print("Lilly AI + Multi-Model Cascade Integration Examples")
    print("=" * 60)

    # Run examples
    asyncio.run(example_lilly_integration())

    # Uncomment to run performance comparison
    # asyncio.run(compare_performance())
